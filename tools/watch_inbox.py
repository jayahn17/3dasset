#!/usr/bin/env python3
"""Watch an inbox folder for CrateScanner zips and process them automatically.

This is the single automation point on the Linux (4080) side. Anything that
drops a ``CrateScan-*.zip`` into the inbox gets unzipped and processed —

    Tailscale:  iPad -> POST /rgbd/upload  -> inbox/
    Drive:      drive_rgbd_autopilot.py    -> inbox/
    Manual:     scp / mv                   -> inbox/

Default mode is **object** (fast): score frames → keep ~4 diverse views →
object.ply asset. Optional ``--mode rgbd`` still runs full TSDF fuse.

Per zip:

    inbox/CrateScan-ab12cd34.zip
      -> work/CrateScan-ab12cd34/          unzipped
      -> python -m assetpipe object …     (or rgbd)
      -> done/CrateScan-ab12cd34.zip       (or failed/ on error)
      -> status/CrateScan-ab12cd34.json

RUN

    python tools/watch_inbox.py                       # object preprocess (default)
    python tools/watch_inbox.py --mode rgbd           # full TSDF
    python tools/watch_inbox.py --once
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


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def find_session(root: str) -> str | None:
    """Locate the RGB-D session directory inside an unzipped package."""
    for dirpath, _dirnames, filenames in os.walk(root):
        if "manifest.json" in filenames:
            return dirpath
    return None


def detect_mode(session: str) -> str:
    """Classify a session: room sweep -> "rgbd" (full TSDF), close-up -> "object".

    A walk-around room capture has a long camera path and far surfaces; a
    tabletop object orbit stays within arm's reach. Cheap: manifest poses
    (translation is convention-invariant) + a few central depth medians.
    """
    import json

    import numpy as np
    from PIL import Image

    try:
        with open(os.path.join(session, "manifest.json")) as fh:
            man = json.load(fh)
        frames = man.get("frames") or []
        ts = np.array([[f["pose"][3], f["pose"][7], f["pose"][11]]
                       for f in frames if len(f.get("pose", [])) == 16])
        span = float(np.linalg.norm(ts.max(0) - ts.min(0))) if len(ts) else 0.0

        meds = []
        step = max(1, len(frames) // 6)
        for f in frames[::step][:6]:
            d = np.array(Image.open(os.path.join(session, f["depth"])))
            h, w = d.shape[:2]
            patch = d[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
            valid = patch[patch > 50]
            if len(valid):
                meds.append(float(np.median(valid)) / 1000.0)
        med_depth = float(np.median(meds)) if meds else 0.0
    except Exception:  # noqa: BLE001 — fall back to the safe default
        return "object"

    return "rgbd" if (span > 2.5 or med_depth > 1.4) else "object"


def is_stable(path: str, settle: float = 2.0) -> bool:
    """True once the file has stopped growing."""
    try:
        first = os.path.getsize(path)
        time.sleep(settle)
        return first > 0 and first == os.path.getsize(path)
    except OSError:
        return False


def evaluate_object_asset(asset_out: str, max_frames: int) -> dict:
    """Reject scene-scale or poorly aligned outputs before RESULT packaging."""
    meta_path = os.path.join(asset_out, "asset.json")
    if not os.path.isfile(meta_path):
        return {"verdict": "reject", "reasons": ["missing asset.json"]}
    with open(meta_path) as fh:
        meta = json.load(fh)

    extent = [float(x) for x in meta.get("extent_m", [])]
    kept = len(meta.get("frames_used", []))
    requested = max(1, int(max_frames))
    reasons: list[str] = []
    warnings: list[str] = []

    if len(extent) != 3:
        reasons.append("missing geometry extent")
    elif max(extent) > 1.0:
        reasons.append(
            f"scene-scale geometry ({max(extent):.2f} m); object was not isolated"
        )
    if kept < 3:
        reasons.append(f"only {kept} aligned views")
    elif kept < max(4, requested // 2):
        warnings.append(f"only {kept}/{requested} selected views aligned")

    mesh = meta.get("mesh") or {}
    if mesh.get("error"):
        reasons.append(f"rigid mesh failed: {mesh['error']}")
    if int(mesh.get("faces") or 0) < 1_000:
        reasons.append("rigid mesh has too few faces")

    verdict = "reject" if reasons else ("partial" if warnings else "pass")
    return {
        "verdict": verdict,
        "usable_asset": verdict != "reject",
        "reasons": reasons,
        "warnings": warnings,
        "frames_aligned": kept,
        "frames_requested": requested,
        "extent_m": extent,
    }


def process(
    zip_path: str,
    dirs: dict[str, str],
    *,
    mode: str,
    backend: str,
    voxel: float,
    max_frames: int,
    dry_run: bool,
    world_scene: str | None = None,
) -> dict:
    """Unzip one package, run object/rgbd, and file the result."""
    name = os.path.splitext(os.path.basename(zip_path))[0]
    work = os.path.join(dirs["work"], name)
    out = os.path.join(dirs["out"], name)
    status: dict = {"name": name, "started": time.time(), "ok": False, "mode": mode}

    _log(f"→ {name}  mode={mode}")
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
        status["error"] = (
            "no manifest.json in package — is this a CrateScanner RGB-D zip?"
        )
        _finish(zip_path, dirs, status, ok=False)
        return status
    status["session"] = os.path.relpath(session, REPO_ROOT)

    if mode == "auto":
        mode = detect_mode(session)
        status["mode"] = mode
        _log(f"  auto mode → {mode}")

    if mode == "object":
        asset_out = os.path.join(out, "object_asset")
        # Fast path: score raw session → ~4 views (skip heavy curate; it over-thinned)
        cmd = [
            sys.executable,
            "-m",
            "assetpipe",
            "object",
            session,
            "--out",
            asset_out,
            "--max-frames",
            str(max_frames),
            "--stride",
            "2",
        ]
    else:
        cmd = [
            sys.executable,
            "-m",
            "assetpipe",
            "rgbd",
            session,
            "--backend",
            backend,
            "--voxel-size",
            str(voxel),
            "--out",
            out,
        ]

    _log("  " + " ".join(cmd))
    if dry_run:
        status.update(ok=True, dry_run=True, out=out)
        _finish(zip_path, dirs, status, ok=True, move=False)
        return status

    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    status["returncode"] = proc.returncode
    tail = (proc.stdout or "").strip().splitlines()[-16:]
    status["stdout_tail"] = tail
    if proc.returncode != 0:
        status["error"] = (proc.stderr or "\n".join(tail) or "process failed")[-800:]
        _log(f"  ✗ {name}: {status['error'].splitlines()[-1][:120]}")
        _finish(zip_path, dirs, status, ok=False)
        return status

    # Constant scene update: register this session into the persistent world
    # model (fast, --no-mesh; refresh the cumulative mesh on demand with
    # `assetpipe world update ... `). Failure here never fails the zip.
    if world_scene and mode == "rgbd":
        wcmd = [sys.executable, "-m", "assetpipe", "world", "update",
                "--scene", world_scene, "--session", session, "--no-mesh"]
        wproc = subprocess.run(wcmd, cwd=REPO_ROOT, capture_output=True, text=True)
        status["world_update"] = wproc.returncode == 0
        tailw = (wproc.stdout or wproc.stderr or "").strip().splitlines()[-2:]
        for ln in tailw:
            _log(f"  world: {ln[:110]}")

    # Convenience: copy OPEN_ME to out root for RESULT packaging
    if mode == "object":
        quality = evaluate_object_asset(asset_out, max_frames)
        status["quality"] = quality
        status["usable_asset"] = quality["usable_asset"]
        _log(
            f"  quality={quality['verdict']} "
            f"frames={quality['frames_aligned']}/{quality['frames_requested']} "
            f"extent={quality['extent_m']}"
        )
        for reason in quality["reasons"]:
            _log(f"    reject: {reason}")
        for warning in quality["warnings"]:
            _log(f"    warning: {warning}")
        open_src = os.path.join(out, "object_asset", "OPEN_ME.html")
        if os.path.isfile(open_src):
            shutil.copy2(open_src, os.path.join(out, "OPEN_ME.html"))
        # also write a tiny status pointer
        pointer = {
            "primary": "object_asset",
            "open": "object_asset/OPEN_ME.html",
            "viewer": "object_asset/view.html",
            "ply": "object_asset/object.ply",
        }
        with open(os.path.join(out, "asset_pointer.json"), "w") as fh:
            json.dump(pointer, fh, indent=2)

    status.update(ok=True, out=out)
    artifacts = sorted(os.listdir(out)) if os.path.isdir(out) else []
    status["artifacts"] = artifacts
    _log(f"  ✓ {name} → {out}  ({len(artifacts)} files)")
    for line in tail[-6:]:
        _log(f"    {line}")
    _finish(zip_path, dirs, status, ok=True)
    return status


def _finish(
    zip_path: str, dirs: dict[str, str], status: dict, ok: bool, move: bool = True
) -> None:
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
    except OSError as e:  # noqa: BLE001
        _log(f"  !! could not move {zip_path}: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--root",
        default=os.path.join(REPO_ROOT, "captures"),
        help="folder holding inbox/ work/ done/ failed/ status/",
    )
    p.add_argument(
        "--out",
        default=os.path.join(REPO_ROOT, "demo_out"),
        help="where results are written",
    )
    p.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "object", "rgbd"],
        help="auto = detect per zip (room sweep→rgbd, close-up→object); "
             "object = score→~4 views→asset; rgbd = full TSDF fuse",
    )
    p.add_argument("--max-frames", type=int, default=10, help="[object] max views to merge")
    p.add_argument("--backend", default="auto", choices=["auto", "nvblox", "open3d"])
    p.add_argument("--voxel-size", type=float, default=0.01)
    p.add_argument("--world-scene", default=None,
                   help="persistent world-model dir: every rgbd zip also "
                        "registers into this scene (constant 1+1+1 updates)")
    p.add_argument("--interval", type=float, default=5.0, help="seconds between inbox polls")
    p.add_argument("--once", action="store_true", help="process what's there, then exit")
    p.add_argument("--dry-run", action="store_true", help="unzip/locate only")
    args = p.parse_args(argv)

    dirs = {
        k: os.path.join(args.root, k)
        for k in ("inbox", "work", "done", "failed", "status")
    }
    dirs["out"] = args.out
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)

    _log(f"watching {dirs['inbox']}  mode={args.mode}  out={args.out}")
    if args.dry_run:
        _log("dry-run: will unzip and inspect, but not process")

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
                continue
            seen.add(path)
            try:
                process(
                    path,
                    dirs,
                    mode=args.mode,
                    backend=args.backend,
                    voxel=args.voxel_size,
                    max_frames=args.max_frames,
                    dry_run=args.dry_run,
                    world_scene=args.world_scene,
                )
            except Exception as e:  # noqa: BLE001
                _log(f"  !! unexpected error on {entry}: {e}")
            finally:
                seen.discard(path)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
