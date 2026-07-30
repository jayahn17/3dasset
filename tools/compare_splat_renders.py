#!/usr/bin/env python3
"""Compare N trained splats against ground-truth photos at shared poses.

Renders every splat at the same capture viewpoints, scores PSNR (and SSIM if
scikit-image is present) against the real photos, and writes a labeled
side-by-side sheet. Works for our 5k-vs-30k tiers today and for scoring an
API-returned splat (e.g. Kiri 3DGS) against the same session tomorrow.

Run in the `roomrecon` env:
    python tools/compare_splat_renders.py \
        --session captures/work/bench_ours/1B38880A \
        --splat 5k=bench_out/CrateScan-1B38880A/ours_3dgut/scene_gaussians.ply \
        --splat 30k=bench_out/CrateScan-1B38880A/ours_3dgut_30k/scene_gaussians.ply \
        --eval-views 12 --sheet-views 3 \
        --out bench_out/CrateScan-1B38880A/splat_compare
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import torch
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from assetpipe.scene.rgbd_session import load_session  # noqa: E402
from render_splat_views import load_3dgs_ply  # noqa: E402

try:
    from skimage.metrics import structural_similarity as ssim_fn
except ImportError:
    ssim_fn = None


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return 99.0 if mse == 0 else 20 * np.log10(255.0 / np.sqrt(mse))


def render_at(frame, means, quats, scales, opac, sh, sh_degree):
    from gsplat import rasterization

    gt = Image.open(frame.color_path).convert("RGB")
    W, H = gt.size
    fx, fy, cx, cy = frame.intrinsics
    dw, dh = Image.open(frame.depth_path).size
    sx, sy = W / dw, H / dh
    K = torch.tensor([[fx * sx, 0, cx * sx], [0, fy * sy, cy * sy], [0, 0, 1]],
                     dtype=torch.float32, device="cuda")
    c2w = torch.tensor(np.asarray(frame.pose, np.float32).reshape(4, 4),
                       device="cuda")
    img, _a, _m = rasterization(
        means, quats, scales, opac, sh,
        torch.linalg.inv(c2w)[None], K[None], W, H, sh_degree=sh_degree,
        near_plane=0.01, far_plane=100.0,
    )
    return gt, (img[0].clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", required=True)
    ap.add_argument("--splat", action="append", required=True,
                    metavar="LABEL=PATH", help="repeatable")
    ap.add_argument("--eval-views", type=int, default=12)
    ap.add_argument("--sheet-views", type=int, default=3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    session_dir = args.session
    if not os.path.isfile(os.path.join(session_dir, "manifest.json")):
        for dirpath, _d, files in os.walk(session_dir):
            if "manifest.json" in files:
                session_dir = dirpath
                break
    session = load_session(session_dir)
    os.makedirs(args.out, exist_ok=True)

    splats = []
    for spec in args.splat:
        label, path = spec.split("=", 1)
        splats.append((label, path, load_3dgs_ply(path)))

    eval_idx = np.linspace(0, session.n_frames - 1, args.eval_views, dtype=int)
    sheet_idx = set(np.linspace(0, session.n_frames - 1, args.sheet_views,
                                dtype=int).tolist())

    stats = {label: {"psnr": [], "ssim": []} for label, _p, _g in splats}
    sheet_rows = []
    for i in eval_idx:
        fr = session.frames[i]
        row_imgs = None
        for label, _path, (means, quats, scales, opac, sh) in splats:
            sh_degree = int(round(np.sqrt(sh.shape[1]) - 1))
            gt, render = render_at(fr, means, quats, scales, opac, sh, sh_degree)
            gt_np = np.asarray(gt)
            stats[label]["psnr"].append(psnr(gt_np, render))
            if ssim_fn is not None:
                stats[label]["ssim"].append(
                    float(ssim_fn(gt_np, render, channel_axis=2, data_range=255)))
            if int(i) in sheet_idx:
                if row_imgs is None:
                    row_imgs = [("GT", gt)]
                row_imgs.append((label, Image.fromarray(render)))
        if row_imgs:
            sheet_rows.append(row_imgs)

    report = {"session": session_dir, "n_eval_views": len(eval_idx), "splats": {}}
    for label, path, (means, *_rest) in splats:
        report["splats"][label] = {
            "path": path,
            "n_gaussians": int(means.shape[0]),
            "psnr_mean": round(float(np.mean(stats[label]["psnr"])), 2),
            "psnr_min": round(float(np.min(stats[label]["psnr"])), 2),
            "ssim_mean": (round(float(np.mean(stats[label]["ssim"])), 4)
                          if stats[label]["ssim"] else None),
        }
    with open(os.path.join(args.out, "compare.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report["splats"], indent=2))

    # Labeled composite: one row per sheet view, one column per (GT + splats)
    pad, band = 4, 22
    W, H = sheet_rows[0][0][1].size
    cols = len(sheet_rows[0])
    sheet = Image.new("RGB", (cols * W + pad * (cols - 1),
                              len(sheet_rows) * (H + band + pad)), "white")
    draw = ImageDraw.Draw(sheet)
    y = 0
    for row in sheet_rows:
        for c, (label, img) in enumerate(row):
            x = c * (W + pad)
            draw.text((x + 6, y + 4), label, fill="black")
            sheet.paste(img, (x, y + band))
        y += H + band + pad
    sheet_path = os.path.join(args.out, "compare_sheet.png")
    sheet.save(sheet_path)
    print(f"sheet -> {sheet_path}")


if __name__ == "__main__":
    main()
