#!/usr/bin/env python3
"""Render a trained 3DGS/3DGUT splat at real capture poses, beside the photo.

The honest way to look at a splat: same camera, ground truth on the left,
render on the right. Run in the `roomrecon` env (gsplat 1.4 + torch cu124).

Usage:
    python tools/render_splat_views.py \
        bench_out/CrateScan-1B38880A/ours_3dgut/scene_gaussians.ply \
        --session captures/work/bench_ours/1B38880A --views 3
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import torch
from PIL import Image
from plyfile import PlyData

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from assetpipe.scene.rgbd_session import load_session  # noqa: E402


def load_3dgs_ply(path: str, device: str = "cuda"):
    v = PlyData.read(path)["vertex"]
    means = np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float32)
    n_rest = len([p.name for p in v.properties if p.name.startswith("f_rest_")])
    dc = np.stack([v[f"f_dc_{i}"] for i in range(3)], 1)[:, None, :]  # N,1,3
    if n_rest:
        rest = np.stack([v[f"f_rest_{i}"] for i in range(n_rest)], 1)
        rest = rest.reshape(len(means), 3, n_rest // 3).transpose(0, 2, 1)  # N,K-1,3
        sh = np.concatenate([dc, rest], 1).astype(np.float32)
    else:
        sh = dc.astype(np.float32)
    scales = np.exp(np.stack([v[f"scale_{i}"] for i in range(3)], 1)).astype(np.float32)
    quats = np.stack([v[f"rot_{i}"] for i in range(4)], 1).astype(np.float32)
    opac = 1.0 / (1.0 + np.exp(-np.asarray(v["opacity"], np.float32)))
    t = lambda x: torch.from_numpy(np.ascontiguousarray(x)).to(device)
    return t(means), t(quats), t(scales), t(opac), t(sh)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("splat", help="3DGS-format .ply")
    ap.add_argument("--session", required=True,
                    help="RGB-D session dir (or dir containing it) for poses")
    ap.add_argument("--views", type=int, default=3)
    ap.add_argument("--out", help="output dir (default: beside the splat)")
    args = ap.parse_args()

    session_dir = args.session
    if not os.path.isfile(os.path.join(session_dir, "manifest.json")):
        for dirpath, _d, files in os.walk(session_dir):
            if "manifest.json" in files:
                session_dir = dirpath
                break
    session = load_session(session_dir)
    out_dir = args.out or os.path.join(os.path.dirname(args.splat), "renders")
    os.makedirs(out_dir, exist_ok=True)

    from gsplat import rasterization

    means, quats, scales, opac, sh = load_3dgs_ply(args.splat)
    sh_degree = int(round(np.sqrt(sh.shape[1]) - 1))

    idxs = np.linspace(0, session.n_frames - 1, args.views, dtype=int)
    panels = []
    for k, i in enumerate(idxs):
        fr = session.frames[i]
        gt = Image.open(fr.color_path).convert("RGB")
        W, H = gt.size
        fx, fy, cx, cy = fr.intrinsics
        # intrinsics are stored in depth-image space; rescale to color size
        dw, dh = Image.open(fr.depth_path).size
        sx, sy = W / dw, H / dh
        K = torch.tensor([[fx * sx, 0, cx * sx], [0, fy * sy, cy * sy],
                          [0, 0, 1]], dtype=torch.float32, device="cuda")
        c2w = torch.tensor(np.asarray(fr.pose, np.float32).reshape(4, 4),
                           device="cuda")
        viewmat = torch.linalg.inv(c2w)[None]
        img, _alpha, _meta = rasterization(
            means, quats, scales, opac, sh,
            viewmat, K[None], W, H, sh_degree=sh_degree,
            near_plane=0.01, far_plane=100.0,
        )
        render = (img[0].clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)
        pair = Image.new("RGB", (W * 2 + 4, H), "white")
        pair.paste(gt, (0, 0))
        pair.paste(Image.fromarray(render), (W + 4, 0))
        p = os.path.join(out_dir, f"view_{k}_gt_vs_render.png")
        pair.save(p)
        panels.append(pair)
        print(f"  view {k} (frame {fr.frame_id}) -> {p}")

    sheet = Image.new("RGB", (panels[0].width,
                              sum(p.height for p in panels) + 4 * len(panels)),
                      "white")
    y = 0
    for p in panels:
        sheet.paste(p, (0, y))
        y += p.height + 4
    sheet_path = os.path.join(out_dir, "contact_sheet.png")
    sheet.save(sheet_path)
    print(f"contact sheet -> {sheet_path}")


if __name__ == "__main__":
    main()
