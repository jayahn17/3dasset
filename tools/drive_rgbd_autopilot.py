#!/usr/bin/env python3
"""Engin170 Drive autopilot: poll shared Drive folder → inbox → fuse → upload results.

Downloads new ``CrateScan-*.zip`` from the shared Google Drive folder (public
link works via gdown). The existing ``watch_inbox.py`` loop fuses them.
After a successful fuse, packs a RESULT zip and uploads it back to Drive
(requires ``rclone`` remote ``gdrive`` with write access — one-time setup).

RUN (foreground)
    python tools/drive_rgbd_autopilot.py
    python tools/drive_rgbd_autopilot.py --once

ENV / flags
    --folder-id   Drive folder id (default: CrateScans)
    --interval    seconds between Drive polls (default 60)
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
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_FOLDER = "1DpLpm4m4TxUn_Vn9Rfhr0o39HKM-Xkbl"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _known_names(captures: Path) -> set[str]:
    names: set[str] = set()
    for sub in ("inbox", "done", "failed", "work"):
        d = captures / sub
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.name.endswith(".zip"):
                names.add(p.name)
            elif p.is_dir() and p.name.startswith("CrateScan-"):
                names.add(p.name + ".zip")
    # also remember results already uploaded
    seen = captures / "status" / ".drive_seen.json"
    if seen.is_file():
        try:
            names.update(json.loads(seen.read_text()))
        except Exception:
            pass
    return names


def _save_seen(captures: Path, names: set[str]) -> None:
    path = captures / "status" / ".drive_seen.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(names), indent=2))


def _list_folder_zips(folder_id: str) -> list[tuple[str, str]]:
    """Parse public Drive folder HTML → [(filename, file_id), …]."""
    import re
    import urllib.request

    url = f"https://drive.google.com/drive/folders/{folder_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    # data-id="FID" … data-tooltip="CrateScan-XXXX.zip …
    pairs: list[tuple[str, str]] = []
    for m in re.finditer(
        r'data-id="([^"]+)"[^>]*data-tooltip="(CrateScan-[A-Fa-f0-9]{8}\.zip)',
        html,
    ):
        pairs.append((m.group(2), m.group(1)))
    if not pairs:
        # fallback: embedded Drive JSON
        for m in re.finditer(
            r'\\x22(1[-A-Za-z0-9_]{20,})\\x22,\\x5b\\x22'
            + re.escape(folder_id)
            + r'\\x22\\x5d,\\x22(CrateScan-[A-Fa-f0-9]{8}\.zip)\\x22',
            html,
        ):
            pairs.append((m.group(2), m.group(1)))
    # dedupe by name
    seen: dict[str, str] = {}
    for name, fid in pairs:
        seen[name] = fid
    return sorted(seen.items())


def _download_drive_file(file_id: str, dest: Path) -> bool:
    """Download a public Drive file (handles virus-scan interstitial)."""
    import re
    import urllib.parse
    import urllib.request

    def _get(url: str) -> tuple[bytes, str]:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.read(), resp.headers.get("Content-Type", "")

    raw, ctype = _get(f"https://drive.google.com/uc?export=download&id={file_id}")
    if raw[:2] == b"PK":
        dest.write_bytes(raw)
        return True
    html = raw.decode("utf-8", errors="ignore")
    inputs = dict(re.findall(r'name="([^"]+)"\s+value="([^"]*)"', html))
    if not inputs.get("id"):
        inputs = {"id": file_id, "export": "download", "confirm": "t"}
    url2 = "https://drive.usercontent.google.com/download?" + urllib.parse.urlencode(
        inputs
    )
    blob, ctype = _get(url2)
    if blob[:2] != b"PK":
        _log(f"  !! not a zip for {file_id}: ctype={ctype} head={blob[:40]!r}")
        return False
    dest.write_bytes(blob)
    return True


def pull_drive(folder_id: str, staging: Path, captures: Path) -> list[Path]:
    """List public folder → download only *new* CrateScan zips into inbox.

    Per-file download (not gdown --folder) so one large/blocked file cannot
    abort the whole poll.
    """
    staging.mkdir(parents=True, exist_ok=True)
    pull_dir = staging / "pull"
    pull_dir.mkdir(parents=True, exist_ok=True)

    _log(f"Drive pull {folder_id} …")
    try:
        listed = _list_folder_zips(folder_id)
    except Exception as e:  # noqa: BLE001
        _log(f"  !! folder list failed: {e}")
        # fallback: gdown folder (legacy)
        url = f"https://drive.google.com/drive/folders/{folder_id}"
        cmd = [sys.executable, "-m", "gdown", "--folder", url, "-O", str(pull_dir)]
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
        if proc.returncode != 0:
            _log(f"  !! gdown failed: {(proc.stderr or proc.stdout)[-400:]}")
            return []
        listed = []

    known = _known_names(captures)
    inbox = captures / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    new_zips: list[Path] = []

    if listed:
        _log(f"  listed {len(listed)} CrateScan zips on Drive")
        for name, fid in listed:
            if name in known:
                continue
            tmp = pull_dir / name
            _log(f"  ↓ {name} ({fid})")
            try:
                ok = _download_drive_file(fid, tmp)
            except Exception as e:  # noqa: BLE001
                _log(f"  !! download failed {name}: {e}")
                continue
            if not ok or not tmp.is_file():
                continue
            dest = inbox / name
            shutil.copy2(tmp, dest)
            _log(f"  → inbox/{name} ({tmp.stat().st_size / 1e6:.1f} MB)")
            new_zips.append(dest)
            known.add(name)
    else:
        for z in pull_dir.rglob("CrateScan-*.zip"):
            if z.name in known:
                continue
            dest = inbox / z.name
            shutil.copy2(z, dest)
            _log(f"  → inbox/{z.name} ({z.stat().st_size / 1e6:.1f} MB)")
            new_zips.append(dest)
            known.add(z.name)

    _save_seen(captures, known)
    if not new_zips:
        _log("  no new zips")
    return new_zips


def pack_result(out_dir: Path, name: str) -> Path | None:
    """Zip viewable artifacts for upload back to Drive."""
    if not out_dir.is_dir():
        return None
    result = out_dir.parent / f"{name}_RESULT.zip"
    if result.exists():
        result.unlink()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in (
            "OPEN_ME.html",
            "asset_pointer.json",
            "scan_view.html",
            "object_view.html",
            "scene_mesh.glb",
            "scene_tsdf_mesh.ply",
            "scene_object.ply",
            "scene_object.splat",
            "scene_object_mesh.ply",
            "scene_clean.ply",
            "scene.ply",
            "scene_gaussians.ply",
            "scene.splat",
            "rgbd_meta.json",
            "dims.json",
            "measurement.txt",
            "object_crop.json",
            "splat_view.html",
        ):
            p = out_dir / fname
            if p.is_file():
                zf.write(p, arcname=f"{name}_RESULT/{fname}")
        # Preferred light asset: curated + scored best frames → object.ply
        asset = out_dir / "object_asset"
        if asset.is_dir():
            for p in asset.rglob("*"):
                if p.is_file():
                    zf.write(
                        p,
                        arcname=f"{name}_RESULT/object_asset/{p.relative_to(asset)}",
                    )
        report = out_dir / "curated_session" / "curate_report.html"
        if report.is_file():
            zf.write(report, arcname=f"{name}_RESULT/curate_report.html")
    return result if result.is_file() and result.stat().st_size > 0 else None


def rclone_available() -> str | None:
    for cand in (
        shutil.which("rclone"),
        str(Path.home() / "bin" / "rclone"),
        "/tmp/rclone-v1.74.4-linux-amd64/rclone",
    ):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def upload_result(rclone_bin: str, local_zip: Path, folder_id: str) -> bool:
    """Upload RESULT zip into the Drive folder (needs write-capable gdrive remote)."""
    # Prefer folder-id path so we don't depend on a named Drive path.
    remote = f"gdrive,root_folder_id={folder_id}:"
    cmd = [rclone_bin, "copy", str(local_zip), remote, "--drive-shared-with-me", "-v"]
    _log(f"  upload {local_zip.name} → Drive …")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # retry without shared-with-me
        cmd2 = [rclone_bin, "copy", str(local_zip), remote, "-v"]
        proc = subprocess.run(cmd2, capture_output=True, text=True)
    if proc.returncode != 0:
        _log(f"  !! upload failed (is rclone 'gdrive' configured with write access?): "
             f"{(proc.stderr or proc.stdout)[-500:]}")
        # keep a local copy for manual upload
        outbox = REPO / "captures" / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_zip, outbox / local_zip.name)
        _log(f"  saved local copy → captures/outbox/{local_zip.name}")
        return False
    _log(f"  ✓ uploaded {local_zip.name}")
    return True


def process_pending_uploads(captures: Path, out_root: Path, folder_id: str) -> None:
    """For each successful status without upload yet, pack + upload RESULT."""
    status_dir = captures / "status"
    if not status_dir.is_dir():
        return
    rclone = rclone_available()
    uploaded_marker = status_dir / ".uploaded.json"
    uploaded: set[str] = set()
    if uploaded_marker.is_file():
        try:
            uploaded = set(json.loads(uploaded_marker.read_text()))
        except Exception:
            pass

    for st in status_dir.glob("CrateScan-*.json"):
        try:
            data = json.loads(st.read_text())
        except Exception:
            continue
        name = data.get("name") or st.stem
        if (
            not data.get("ok")
            or data.get("usable_asset") is False
            or name in uploaded
        ):
            continue
        out_dir = Path(data.get("out") or (out_root / name))
        if not out_dir.is_dir():
            # watch_inbox writes demo_out/<name>
            out_dir = out_root / name
        result = pack_result(out_dir, name)
        if not result:
            continue
        if rclone:
            ok = upload_result(rclone, result, folder_id)
            if ok:
                uploaded.add(name)
                uploaded_marker.write_text(json.dumps(sorted(uploaded), indent=2))
        else:
            outbox = captures / "outbox"
            outbox.mkdir(parents=True, exist_ok=True)
            shutil.copy2(result, outbox / result.name)
            _log(f"  RESULT ready (no rclone): captures/outbox/{result.name} — "
                 f"upload manually or run: rclone config")
            uploaded.add(name)  # don't spam
            uploaded_marker.write_text(json.dumps(sorted(uploaded), indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder-id", default=DEFAULT_FOLDER)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--captures", default=str(REPO / "captures"))
    ap.add_argument("--out", default=str(REPO / "demo_out"))
    args = ap.parse_args()

    captures = Path(args.captures)
    staging = captures / "drive_pull"
    out_root = Path(args.out)
    for d in (captures / "inbox", captures / "status", staging, out_root):
        d.mkdir(parents=True, exist_ok=True)

    _log(f"autopilot Drive folder={args.folder_id}  interval={args.interval}s")
    _log("fuse is handled by watch_inbox.py (run it in parallel)")

    while True:
        pull_drive(args.folder_id, staging, captures)
        process_pending_uploads(captures, out_root, args.folder_id)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
