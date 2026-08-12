#!/usr/bin/env python3
"""RGB-only route: session without depth → TRELLIS multiview asset.

Inference rule (also wired into watch_inbox): a CrateScan session whose
manifest has color+poses but NO depth cannot be fused — it is automatically
sent here instead of failing. Picks up to 4 azimuth-spread keyframes, masks
the salient object with rembg (largest component), waits for the GPU to be
free enough, generates via the gen3d TRELLIS server, and exports
glb / obj / ply. Without depth there is NO metric scale — the export is
normalized and flagged as such (use tools/scale_from_marker.py or borrow
dims from a matching RGB-D session to metricize).

Usage: python tools/rgb_only_trellis.py <session_dir> --out <out_dir>
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
ENDPOINT = "http://127.0.0.1:8080/generate"


def pick_spread_frames(manifest: dict, k: int = 4) -> list[dict]:
    frames = manifest.get("frames") or manifest.get("keyframes") or []
    if len(frames) <= k:
        return frames
    # spread by camera yaw around the up axis when poses exist, else uniform
    try:
        yaws = []
        for f in frames:
            m = np.asarray(f["pose"], np.float64).reshape(4, 4)
            fwd = -m[:3, 2]
            yaws.append(math.atan2(fwd[0], fwd[2]))
        order = np.argsort(yaws)
        picks = [frames[order[int(i)]] for i in np.linspace(0, len(order) - 1, k).astype(int)]
        return picks
    except Exception:
        idx = np.linspace(0, len(frames) - 1, k).astype(int)
        return [frames[i] for i in idx]


def make_views(session: Path, frames: list[dict], out: Path) -> int:
    import cv2
    from PIL import Image
    from rembg import new_session, remove

    sess = new_session("u2net")
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in frames:
        img = Image.open(session / f["color"]).convert("RGB")
        small = img.resize((img.width // 4, img.height // 4))
        m = np.array(remove(small, session=sess, only_mask=True))
        mb = (m > 127).astype(np.uint8)
        cc, lab, stats, _ = cv2.connectedComponentsWithStats(mb)
        if cc <= 1:
            continue
        big = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
        ys, xs = np.where(lab == big)
        l, t, r, b = xs.min() * 4, ys.min() * 4, xs.max() * 4, ys.max() * 4
        s = int(max(r - l, b - t) * 1.15)
        cx, cy = (l + r) // 2, (t + b) // 2
        box = (max(0, cx - s // 2), max(0, cy - s // 2),
               min(img.width, cx + s // 2), min(img.height, cy + s // 2))
        crop = img.crop(box).resize((768, 768), Image.LANCZOS)
        cm = np.array(remove(crop, session=sess, only_mask=True))
        cmb = np.where(cm > 127, 255, 0).astype(np.uint8)
        cc2, lab2, st2, _ = cv2.connectedComponentsWithStats((cmb > 0).astype(np.uint8))
        if cc2 > 1:
            big2 = 1 + np.argmax(st2[1:, cv2.CC_STAT_AREA])
            cmb = np.where(lab2 == big2, 255, 0).astype(np.uint8)
        rgba = np.dstack([np.array(crop), cmb])
        Image.fromarray(rgba).save(out / f"view_{n:02d}.png")
        n += 1
    return n


def wait_gpu_free(min_free_mb: int = 8000, timeout_s: int = 7200) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        q = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True)
        used, total = (int(x) for x in q.stdout.strip().split(", "))
        if total - used >= min_free_mb:
            return True
        time.sleep(60)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--out", required=True)
    ap.add_argument("--views", type=int, default=4)
    args = ap.parse_args()

    session, out = Path(args.session), Path(args.out)
    manifest = json.loads((session / "manifest.json").read_text())
    frames = pick_spread_frames(manifest, args.views)
    views_dir = out / "views"
    n = make_views(session, frames, views_dir)
    print(f"views: {n} -> {views_dir}", flush=True)
    if n == 0:
        print("!! no usable views", flush=True)
        return 1

    print("waiting for GPU …", flush=True)
    if not wait_gpu_free():
        print("!! GPU never freed", flush=True)
        return 1

    cmd = [sys.executable, "-m", "assetpipe", "generate", str(views_dir),
           "--out", str(out), "--backend", "trellis",
           "--endpoint", ENDPOINT, "--views", str(n), "--seed", "1"]
    print("$", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    print((proc.stdout or "") + (proc.stderr or ""), flush=True)
    glb = out / "asset_trellis.glb"
    if proc.returncode != 0 or not glb.is_file():
        return 1

    import trimesh
    mesh = trimesh.load(glb, force="mesh")
    mesh.export(out / "asset_trellis.obj")
    mesh.export(out / "asset_trellis.ply")
    (out / "NO_METRIC_SCALE.txt").write_text(
        "RGB-only session (no depth): mesh is normalized, NOT metric.\n"
        "Metricize with tools/scale_from_marker.py or dims from an RGB-D scan.\n")
    print(f"DONE_RGB_ONLY {glb}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
