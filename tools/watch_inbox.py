#!/usr/bin/env python3
"""Watch an inbox folder for CrateScanner zips and fuse them automatically.

This is the single automation point on the Linux (4080) side. Anything that
drops a ``CrateScan-*.zip`` into the inbox gets unzipped, checked, and fused —
no matter how it arrived:

    Tailscale:  iPad -> POST /rgbd/upload  -> inbox/
    Drive:      rclone copy gdrive:CrateScans/Packages inbox/   (cron/timer)
    Manual:     scp / mv                                        -> inbox/

Per zip:

    inbox/CrateScan-ab12cd34.zip
      -> work/CrateScan-ab12cd34/          unzipped
      -> python -m assetpipe rgbd <session> --backend auto --out OUT/<name>
      -> done/CrateScan-ab12cd34.zip       (or failed/ on error)
      -> status/CrateScan-ab12cd34.json    result / error, for polling

Stdlib only — no watchdog, no inotify — so it runs in the same bare env as the
zero-dep core. Polling a directory every few seconds is entirely adequate for
a scan that takes minutes to fuse.

RUN

    python tools/watch_inbox.py                       # defaults below
    python tools/watch_inbox.py --once                # drain and exit
    python tools/watch_inbox.py --dry-run             # print commands only
    python tools/watch_inbox.py --backend nvblox      # force a backend

    # keep it alive across reboots
    #   systemd --user, or:  nohup python tools/watch_inbox.py &
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# --------------------------------------------------------------------- utils

def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def find_session(root: str) -> str | None:
    """Locate the RGB-D session directory inside an unzipped package.

    The package layout is ``CrateScan-<id>/session/manifest.json``, but a user
    may also zip the ``session/`` folder alone (the app's "Share RGB-D session
    only" button does exactly that). Both must work, so we search for the
    manifest rather than assuming a fixed depth.
    """
    for dirpath, _dirnames, filenames in os.walk(root):
        if "manifest.json" in filenames:
            return dirpath
    return None


def is_stable(path: str, settle: float = 2.0) -> bool:
    """True once the file has stopped growing.

    rclone and HTTP uploads both create the file before it is complete; fusing
    a half-written zip fails confusingly. Two matching size samples is enough.
    """
    try:
        first = os.path.getsize(path)
        time.sleep(settle)
        return first > 0 and first == os.path.getsize(path)
    except OSError:
        return False


# ------------------------------------------------------------------ pipeline

def process(zip_path: str, dirs: dict[str, str], backend: str,
            voxel: float, dry_run: bool) -> dict:
    """Unzip one package, fuse it, and file the result. Returns a status dict."""
    name = os.path.splitext(os.path.basename(zip_path))[0]
    work = os.path.join(dirs["work"], name)
    out = os.path.join(dirs["out"], name)
    status: dict = {"name": name, "started": time.time(), "ok": False}

    _log(f"→ {name}")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(work)
    except zipfile.BadZipFile as e:
        status["error"] = f"bad zip: {e}"
        _finish(zip_path, dirs, status, ok=False)
        return status

    session = find_session(work)
    if not session:
        status["error"] = ("no manifest.json in package — is this a "
                           "CrateScanner RGB-D zip?")
        _finish(zip_path, dirs, status, ok=False)
        return status
    status["session"] = os.path.relpath(session, REPO_ROOT)

    cmd = [sys.executable, "-m", "assetpipe", "rgbd", session,
           "--backend", backend, "--voxel-size", str(voxel), "--out", out]
    _log("  " + " ".join(cmd))
    if dry_run:
        status.update(ok=True, dry_run=True, out=out)
        _finish(zip_path, dirs, status, ok=True, move=False)
        return status

    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    status["returncode"] = proc.returncode
    tail = (proc.stdout or "").strip().splitlines()[-12:]
    status["stdout_tail"] = tail
    if proc.returncode != 0:
        status["error"] = (proc.stderr or "\n".join(tail) or "fuse failed")[-800:]
        _log(f"  ✗ {name}: {status['error'].splitlines()[-1][:120]}")
        _finish(zip_path, dirs, status, ok=False)
        return status

    status.update(ok=True, out=out)
    artifacts = sorted(os.listdir(out)) if os.path.isdir(out) else []
    status["artifacts"] = artifacts
    _log(f"  ✓ {name} → {out}  ({len(artifacts)} files)")
    _finish(zip_path, dirs, status, ok=True)
    return status


def _finish(zip_path: str, dirs: dict[str, str], status: dict,
            ok: bool, move: bool = True) -> None:
    status["finished"] = time.time()
    with open(os.path.join(dirs["status"], f"{status['name']}.json"), "w") as fh:
        json.dump(status, fh, indent=2)
    if not move:
        return
    dest_dir = dirs["done"] if ok else dirs["failed"]
    dest = os.path.join(dest_dir, os.path.basename(zip_path))
    try:
        if os.path.exists(dest):
            os.remove(dest)
        shutil.move(zip_path, dest)
    except OSError as e:  # noqa: BLE001 — never let filing kill the loop
        _log(f"  !! could not move {zip_path}: {e}")


# ---------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=os.path.join(REPO_ROOT, "captures"),
                   help="folder holding inbox/ work/ done/ failed/ status/")
    p.add_argument("--out", default=os.path.join(REPO_ROOT, "demo_out"),
                   help="where fused results are written")
    p.add_argument("--backend", default="auto",
                   choices=["auto", "nvblox", "open3d"])
    p.add_argument("--voxel-size", type=float, default=0.01)
    p.add_argument("--interval", type=float, default=5.0,
                   help="seconds between inbox polls")
    p.add_argument("--once", action="store_true",
                   help="process what's there, then exit")
    p.add_argument("--dry-run", action="store_true",
                   help="unzip and locate the session, but don't fuse")
    args = p.parse_args(argv)

    dirs = {k: os.path.join(args.root, k)
            for k in ("inbox", "work", "done", "failed", "status")}
    dirs["out"] = args.out
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)

    _log(f"watching {dirs['inbox']}  backend={args.backend}  out={args.out}")
    if args.dry_run:
        _log("dry-run: will unzip and inspect, but not fuse")

    seen: set[str] = set()
    while True:
        try:
            entries = sorted(os.listdir(dirs["inbox"]))
        except OSError:
            entries = []
        for entry in entries:
            if not entry.lower().endswith(".zip"):
                continue
            path = os.path.join(dirs["inbox"], entry)
            if path in seen or not os.path.isfile(path):
                continue
            if not is_stable(path):
                continue          # still uploading — pick it up next pass
            seen.add(path)
            try:
                process(path, dirs, args.backend, args.voxel_size, args.dry_run)
            except Exception as e:  # noqa: BLE001 — one bad zip must not stop the watcher
                _log(f"  !! unexpected error on {entry}: {e}")
            finally:
                seen.discard(path)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
