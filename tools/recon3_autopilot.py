#!/usr/bin/env python3
"""Standing ingest daemon: zip appears on Drive → recon3 → dashboard + page.

The complete automation of the 2026-08-05 workflow. Poll the shared Engin170
folder for ANY .zip (the old autopilot's CrateScan-only regex silently ignored
every crate_<timestamp> upload the app has produced since d0a7867); download
with the confirm-token-aware fetcher; extract; run tools/recon3.py, whose
stages end with the asset card AND the interactive orbit+measure page on the
dashboard. GPU-heavy work serialises behind recon3's flock, so several uploads
queue cleanly.

    nohup python tools/recon3_autopilot.py >> logs/recon3_autopilot.log 2>&1 &

State: captures/status/.recon3_done.json  (zip name → outcome). A zip is
attempted once; failures are recorded, not retried, so a broken capture cannot
crash-loop the GPU — clear its entry to retry. The blob token is read from
web/.env.local so cron restarts need no environment.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# BOTH upload folders, newest-first in priority. Watching one was a standing
# trap: the iPad has uploaded into Engin170_sync as recently as 2026-08-06 while
# CrateScans' newest was a day older, so whichever single folder is configured,
# the other one's uploads are silently never processed — and nothing logs a
# miss, because an empty listing is indistinguishable from "nothing new".
# A zip present in both is processed once; the ledger is keyed by NAME.
FOLDERS = [
    ("CrateScans", "1DpLpm4m4TxUn_Vn9Rfhr0o39HKM-Xkbl"),
    ("Engin170_sync", "1_HjCyP9-Th3UTjK6Kk89vTuud6ltL4AI"),
]
STAGING = REPO / "captures/staging/auto"
LEDGER = REPO / "captures/status/.recon3_done.json"
POLL_S = 120
# processed same-session already, or intentionally out of scope
SKIP = {"CrateScan-76B73843.zip", "crate_20260805_13_56_30.zip",
        "crate_20260805_15_14_12.zip", "crate_20260805_16_37_15.zip",
        "crate_20260805_17_14_00.zip", "crate_20260805_17_20_32.zip"}

# Which account subfolders may be processed.
#
# The shared Drive folder is MULTI-TENANT (CrateScans/<email>/), but everything
# downstream is single-tenant: recon3's publish stage writes into ONE manifest
# and ONE blob store, so another person's capture would surface on the same
# dashboard as yours. Publishing someone else's scan is the hard-to-undo
# direction; holding it is not — hence an allowlist rather than a blocklist.
#
# The list lives OUTSIDE the repo because it is a list of real people's email
# addresses and this repository is public. Configure it either way:
#
#   captures/status/accounts_allow.json   ["me@example.com", "you@example.com"]
#   RECON3_ACCOUNTS="me@example.com,you@example.com"     (env overrides the file)
#   RECON3_ACCOUNTS="*"                                  process every account
#
# With neither set, root-level captures still process and per-account ones are
# held and logged by name — the safe default for a fresh checkout.
ACCOUNTS_FILE = REPO / "captures/status/accounts_allow.json"
_HELD_LOGGED: set[str] = set()


def accounts_allow() -> set[str] | None:
    """-> allowed account emails, or None meaning 'every account'."""
    env = os.environ.get("RECON3_ACCOUNTS", "").strip()
    if env == "*":
        return None
    if env:
        return {e.strip() for e in env.split(",") if e.strip()}
    try:
        return set(json.loads(ACCOUNTS_FILE.read_text()))
    except Exception:
        return set()


def log(m: str) -> None:
    print(f"[{time.strftime('%m-%d %H:%M:%S')}] {m}", flush=True)


def blob_token() -> str:
    for line in (REPO / "web/.env.local").read_text().splitlines():
        if line.startswith("BLOB_READ_WRITE_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


# key -> which folder it came from, for the log line only
ZIP_SOURCE: dict[str, str] = {}
# key -> the account subfolder it belongs to (multi-tenant attribution)
ZIP_ACCOUNT: dict[str, str] = {}


def _fetch(folder: str) -> str:
    return urllib.request.urlopen(urllib.request.Request(
        f"https://drive.google.com/drive/folders/{folder}",
        headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read().decode("utf-8", "ignore")


def list_folder(folder: str) -> tuple[dict[str, str], dict[str, str]]:
    """-> (name -> file id for ALL children, name -> id for child FOLDERS).

    Two zip patterns because Drive serves two shapes. Only the second matches
    today: the first expects the tooltip to END at ".zip", but the real tooltip
    is "<name>.zip Compressed archive". NOTE it is anchored on `folder`, so a
    stale id yields zero rows rather than an error.
    """
    html = _fetch(folder)
    names: dict[str, str] = {}
    for m in re.finditer(r'data-id="([^"]+)"[^>]*data-tooltip="([^"]+?\.zip)"', html):
        names[m.group(2)] = m.group(1)
    for m in re.finditer(r'\\x22(1[-A-Za-z0-9_]{20,})\\x22,\\x5b\\x22' + re.escape(folder)
                         + r'\\x22\\x5d,\\x22([^\\]+?)\\x22', html):
        names[m.group(2)] = m.group(1)
    # The tooltip is the only place the TYPE is exposed: "<name> Shared folder".
    folder_names = set()
    for m in re.finditer(r'data-tooltip="([^"]+)"', html):
        tip = m.group(1)
        if tip.endswith("Shared folder") or tip.endswith(" Folder"):
            folder_names.add(tip.rsplit(" ", 2)[0])
    subs = {n: i for n, i in names.items() if n in folder_names}
    return names, subs


def list_zips() -> dict[str, str]:
    """Every capture zip across every watched folder AND its account subfolders.

    The iPad app now uploads into `CrateScans/<account email>/`, not the folder
    root — the two newest captures on 2026-08-06 sat in per-account subfolders
    where a root-only listing could not see them at all.

    Only ONE level down, and only into subfolders that look like an account
    (a name containing "@"). `Chest/` and `leg press/` are loose-HEIC photo sets
    for the photogrammetry route, not RGB-D sessions — recursing into them would
    queue 50 stills as if they were captures.

    Returns key -> file id, where key is "<label>/<zip>" for a subfolder capture
    so two accounts uploading the same filename stay distinct. The bare zip name
    is kept for root-level captures, which is what the existing ledger is keyed
    on.
    """
    out: dict[str, str] = {}

    def take(key: str, fid: str, label: str, account: str | None) -> None:
        if key in out:
            return
        out[key] = fid
        ZIP_SOURCE[key] = label
        if account:
            ZIP_ACCOUNT[key] = account

    for label, fid in FOLDERS:
        try:
            names, subs = list_folder(fid)
        except Exception as exc:  # one unreachable folder must not stop the other
            log(f"  !! {label} ({fid}) unreadable: {type(exc).__name__}: {exc}")
            continue
        for n, i in names.items():
            if n.endswith(".zip") and n not in subs:
                take(n, i, label, None)
        for sub_name, sub_id in sorted(subs.items()):
            if "@" not in sub_name:
                continue  # photo folder, not an account
            try:
                sub_names, _ = list_folder(sub_id)
            except Exception as exc:
                log(f"  !! {label}/{sub_name} unreadable: {type(exc).__name__}: {exc}")
                continue
            for n, i in sub_names.items():
                if n.endswith(".zip"):
                    take(f"{sub_name}/{n}", i, f"{label}/{sub_name}", sub_name)
    return out



def load_ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text())
    except Exception:
        return {}


def seed_ledger() -> dict:
    """Adopt work the OTHER pipelines already finished, once, at startup.

    This daemon's ledger only knows what this daemon did. Everything processed
    before it existed — by hand, or by the watch_inbox/watch_3dgut chain — lives
    in captures/done/ and captures/status/, so a fresh ledger makes every one of
    those look like a brand-new upload. That is not theoretical: it re-queued
    CrateScan-7C3DD25E (finished 2026-07-30) for a full 30k-iteration retrain.

    Only ADDS entries, so a real failure recorded here is never overwritten.
    """
    ledger = load_ledger()
    added = 0
    for zip_path in sorted((REPO / "captures/done").glob("*.zip")):
        if zip_path.name not in ledger:
            ledger[zip_path.name] = {"ok": True, "name": zip_path.stem,
                                     "adopted": "captures/done"}
            added += 1
    for status in sorted((REPO / "captures/status").glob("*.json")):
        if status.name.startswith("."):
            continue
        z = f"{status.stem}.zip"
        if z not in ledger:
            ledger[z] = {"ok": True, "name": status.stem,
                         "adopted": "captures/status"}
            added += 1
    if added:
        save_ledger(ledger)
        log(f"seeded ledger with {added} capture(s) already finished elsewhere")
    return ledger


def save_ledger(d: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(d, indent=1))


def process(key: str, file_id: str, downloader) -> dict:
    # `key` may be "<account>/<zip>"; everything on disk uses the bare filename.
    zip_name = key.rsplit("/", 1)[-1]
    stem = re.sub(r"\.zip$", "", zip_name)
    name = re.sub(r"[^A-Za-z0-9_]+", "_", stem)
    dest = STAGING / zip_name
    STAGING.mkdir(parents=True, exist_ok=True)
    if not (dest.exists() and dest.stat().st_size > 1_000_000):
        if not downloader(file_id, dest):
            return {"ok": False, "error": "download failed"}
    ex = STAGING / stem
    if not ex.is_dir():
        with zipfile.ZipFile(dest) as z:
            z.extractall(ex)
    manifests = list(ex.rglob("manifest.json"))
    if not manifests:
        return {"ok": False, "error": "no manifest.json in zip"}
    session = manifests[0].parent
    log(f"  session: {session}")
    env = dict(os.environ)
    env["BLOB_READ_WRITE_TOKEN"] = blob_token()
    apy = Path.home() / "miniconda3/envs/assetpipe/bin/python"
    rc = subprocess.run([str(apy), str(REPO / "tools/recon3.py"), str(session),
                         "--name", name], cwd=REPO, env=env).returncode
    return {"ok": rc == 0, "name": name, "rc": rc, "finished": time.time(),
            "account": ZIP_ACCOUNT.get(key), "source": ZIP_SOURCE.get(key),
            "file_id": file_id}


def main() -> None:
    spec = importlib.util.spec_from_file_location("d", REPO / "tools/drive_rgbd_autopilot.py")
    d = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(d)
    log("recon3 autopilot watching " + ", ".join(f"{n} ({i})" for n, i in FOLDERS)
        + f" every {POLL_S}s")
    log(f"source mtime {time.strftime('%m-%d %H:%M:%S', time.localtime(Path(__file__).stat().st_mtime))}"
        " — if this predates your last edit, the daemon is running stale code")
    seed_ledger()
    while True:
        try:
            zips = list_zips()
        except Exception as e:
            log(f"listing failed: {e}")
            time.sleep(POLL_S)
            continue
        ledger = load_ledger()
        def done(k: str) -> bool:
            base = k.rsplit("/", 1)[-1]
            # legacy entries (and SKIP) are keyed on the bare filename
            return k in ledger or base in ledger or base in SKIP or k in SKIP

        new = []
        for n, f in sorted(zips.items()):
            if done(n):
                continue
            acct = ZIP_ACCOUNT.get(n)
            allow = accounts_allow()
            if acct and allow is not None and acct not in allow:
                if n not in _HELD_LOGGED:      # once per capture, not every poll
                    _HELD_LOGGED.add(n)
                    log(f"HELD BACK (account not allowlisted): {n} — add that "
                        f"account to {ACCOUNTS_FILE.relative_to(REPO)} or set "
                        "RECON3_ACCOUNTS to process it")
                continue
            new.append((n, f))
        for zip_name, fid in new:
            log(f"NEW upload: {zip_name}  [{ZIP_SOURCE.get(zip_name, '?')}]")
            try:
                result = process(zip_name, fid, d._download_drive_file)
            except Exception as e:  # noqa: BLE001 — one bad zip must not kill the daemon
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            ledger = load_ledger()
            ledger[zip_name] = result
            save_ledger(ledger)
            log(f"  -> {result}")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
