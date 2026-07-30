#!/usr/bin/env python3
"""Submit a bench_export bundle to the World Labs Marble World API.

Marble is the scene-level benchmark: a generative world model that rebuilds
the captured space from RGB (video <=30s or <=8 photos) and hallucinates what
the camera never saw. Output is a metric-scaled Gaussian splat world
(metadata carries metric_scale_factor) plus an optional GLB mesh. It is NOT a
photogrammetry reconstructor — compare it on completeness/photorealism, and
use bench_compare.py to check how honest its metric scale actually is.

Setup (once):
    1. Key:     https://platform.worldlabs.ai/api-keys
    2. Billing: https://platform.worldlabs.ai/billing  ($5 min, no free tier)
    3. export WORLDLABS_API_KEY=...
    Cost: ~$1.28 per world (1,600 credits, video/multi-image, marble-1.1).
    Splat PLY export free; HQ mesh export 3,500 credits (~$2.80).

Usage:
    python tools/bench_marble.py bench_out/CrateScan-5C0D0369 --input video
    python tools/bench_marble.py bench_out/CrateScan-BE53A423 --input images
    python tools/bench_marble.py --world <world_id> --out bench_out/x/marble
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from bench_export import _ffmpeg_exe  # noqa: E402

BASE = "https://api.worldlabs.ai/marble/v1"
MAX_VIDEO_S = 29.0        # Marble limit is 30 s
MAX_VIDEO_BYTES = 95 << 20  # Marble limit is 100 MB


def _headers() -> dict:
    key = os.environ.get("WORLDLABS_API_KEY", "")
    if not key:
        raise SystemExit(
            "WORLDLABS_API_KEY is not set.\n"
            "  1. Create a key: https://platform.worldlabs.ai/api-keys\n"
            "  2. Add credits:  https://platform.worldlabs.ai/billing ($5 min)\n"
            "  3. export WORLDLABS_API_KEY=..."
        )
    return {"WLT-Api-Key": key, "Content-Type": "application/json"}


def _post(path: str, body: dict) -> dict:
    r = requests.post(f"{BASE}{path}", headers=_headers(), json=body, timeout=60)
    if not r.ok:
        raise SystemExit(f"Marble API {r.status_code} on {path}: {r.text[:500]}")
    return r.json()


def _get(path: str) -> dict:
    r = requests.get(f"{BASE}{path}", headers=_headers(), timeout=60)
    if not r.ok:
        raise SystemExit(f"Marble API {r.status_code} on {path}: {r.text[:500]}")
    return r.json()


def upload_media(path: str, kind: str) -> str:
    """prepare_upload -> PUT bytes -> media_asset_id."""
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    prep = _post("/media-assets:prepare_upload", {
        "file_name": os.path.basename(path)[:64],
        "kind": kind,
        "extension": ext,
    })
    info = prep["upload_info"]
    with open(path, "rb") as fh:
        r = requests.request(
            info.get("upload_method", "PUT"), info["upload_url"],
            headers=info.get("required_headers") or {}, data=fh, timeout=600,
        )
    r.raise_for_status()
    return prep["media_asset"]["media_asset_id"]


def marble_video(bundle_dir: str) -> str:
    """Return a Marble-compliant video path, re-encoding if over limits."""
    src = os.path.join(bundle_dir, "video.mp4")
    if not os.path.isfile(src):
        raise SystemExit(f"{src} missing — run bench_export first (without --no-video)")
    exe = _ffmpeg_exe()
    probe = subprocess.run(
        [exe, "-i", src], capture_output=True, text=True,
    ).stderr
    dur = 0.0
    for line in probe.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            dur = int(h) * 3600 + int(m) * 60 + float(s)
    if dur <= MAX_VIDEO_S and os.path.getsize(src) <= MAX_VIDEO_BYTES:
        return src

    # Evenly subsample frames so the whole sweep fits in 29 s.
    keep_every = max(2, math.ceil(dur / MAX_VIDEO_S))
    dst = os.path.join(bundle_dir, "video_marble.mp4")
    print(f"video is {dur:.0f}s — subsampling 1/{keep_every} frames to fit "
          f"Marble's 30s cap -> {dst}")
    subprocess.run([
        exe, "-y", "-loglevel", "error", "-i", src,
        "-vf", f"select='not(mod(n\\,{keep_every}))',setpts=N/FRAME_RATE/TB",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-an", dst,
    ], check=True)
    return dst


def generate(bundle_dir: str, input_kind: str, *, model: str,
             text_prompt: str | None) -> str:
    if input_kind == "video":
        vid = marble_video(bundle_dir)
        print(f"uploading {vid} ({os.path.getsize(vid) >> 20} MB)...")
        asset_id = upload_media(vid, "video")
        world_prompt: dict = {
            "type": "video",
            "video_prompt": {"source": "media_asset", "media_asset_id": asset_id},
        }
    else:
        photo_dir = os.path.join(bundle_dir, "photos")
        photos = sorted(
            os.path.join(photo_dir, f) for f in os.listdir(photo_dir)
            if f.lower().endswith((".jpg", ".jpeg", ".png"))
        )
        # Marble takes at most 8 images in reconstruct (Auto Layout) mode;
        # spread picks across the sweep for maximum viewpoint coverage.
        step = max(1, len(photos) // 8)
        picks = photos[::step][:8]
        print(f"uploading {len(picks)} photos (of {len(photos)})...")
        items = []
        for p in picks:
            items.append({"content": {
                "source": "media_asset", "media_asset_id": upload_media(p, "image"),
            }})
        world_prompt = {
            "type": "multi-image",
            "multi_image_prompt": items,
            "reconstruct_images": True,
        }
    if text_prompt:
        world_prompt["text_prompt"] = text_prompt

    op = _post("/worlds:generate", {
        "display_name": os.path.basename(bundle_dir.rstrip("/"))[:64],
        "model": model,
        "world_prompt": world_prompt,
    })
    return op["operation_id"]


def poll_operation(operation_id: str, *, timeout_s: int = 30 * 60) -> dict:
    t0 = time.time()
    while True:
        op = _get(f"/operations/{operation_id}")
        if op.get("done"):
            if op.get("error"):
                raise SystemExit(f"Marble operation failed: {op['error']}")
            return op["response"]
        if time.time() - t0 > timeout_s:
            raise SystemExit(f"Timed out on operation {operation_id}")
        print(f"  [{time.time() - t0:5.0f}s] generating...")
        time.sleep(15)


def export_asset(world_id: str, out_dir: str, *, asset_type: str = "splats",
                 fmt: str = "ply", resolution: str = "full_res") -> str:
    body: dict = {"asset_type": asset_type, "format": fmt}
    if asset_type == "splats":
        body["resolution"] = resolution
    else:
        body["mesh_variant"] = "textured"
    op = _post(f"/worlds/{world_id}:export", body)
    resp = op["response"] if op.get("done") else poll_operation(op["operation_id"])
    url = resp["url"]
    dst = os.path.join(out_dir, f"marble_{asset_type}.{fmt}")
    print(f"downloading {asset_type} -> {dst}")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dst, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bundle", nargs="?", help="bench_export output dir")
    ap.add_argument("--input", default="video", choices=["video", "images"],
                    help="video (<=30s room sweep) or images (<=8 views)")
    ap.add_argument("--model", default="marble-1.1",
                    choices=["marble-1.0-draft", "marble-1.0", "marble-1.1",
                             "marble-1.1-plus"])
    ap.add_argument("--prompt", help="optional text prompt alongside the capture")
    ap.add_argument("--mesh", action="store_true",
                    help="also export HQ textured GLB (3,500 credits ~ $2.80)")
    ap.add_argument("--world", help="skip generation: export an existing world id")
    ap.add_argument("--out", help="output dir (default <bundle>/marble)")
    args = ap.parse_args()

    _headers()  # fail fast with setup instructions before any work

    if args.world:
        out_dir = args.out or "."
        os.makedirs(out_dir, exist_ok=True)
        world = _get(f"/worlds/{args.world}")
        world_id = args.world
    else:
        if not args.bundle:
            ap.error("need a bench_export bundle dir (or --world)")
        out_dir = args.out or os.path.join(args.bundle, "marble")
        os.makedirs(out_dir, exist_ok=True)
        op_id = generate(args.bundle, args.input, model=args.model,
                         text_prompt=args.prompt)
        print(f"generation started: operation={op_id} (~5 min, ~$1.28)")
        world = poll_operation(op_id)
        world_id = world.get("world_id") or world.get("id")

    with open(os.path.join(out_dir, "world.json"), "w") as fh:
        json.dump(world, fh, indent=2)
    print(f"world {world_id} ready — metadata saved to {out_dir}/world.json")

    export_asset(world_id, out_dir, asset_type="splats", fmt="ply")
    if args.mesh:
        export_asset(world_id, out_dir, asset_type="mesh", fmt="glb")
    print(
        "\nView the splat with tools/view_splat.py, or score scale honesty:\n"
        f"  python tools/bench_compare.py {out_dir}/marble_splats.ply "
        f"--truth demo_out/<session>/dims.json --label marble"
    )


if __name__ == "__main__":
    main()
