#!/usr/bin/env python3
"""Snapshot a *running* 3dgrut training: checkpoint → viewable .splat.

3dgrut's own playground.py needs the OptiX 3DGRT tracer, which fails to build
here (missing PARTICLE_FEATURE_DIM / RAY_FEATURE_DIM / FEATURE_TRANSFORM_TYPE
defines), and a live GUI can only be attached at launch with `with_gui=true`.
This reads any `ours_*/ckpt_*.pt` a training has already written and produces
a .splat you can open in the browser — read-only, so it never disturbs the
run that is still training.

    python tools/ckpt_to_splat.py <run_dir>            # newest checkpoint
    python tools/ckpt_to_splat.py <path/to/ckpt.pt> --out live.splat

Checkpoint layout (verified): positions, rotation (quat), scale (log),
density (logit), features_albedo (SH DC), features_specular (SH rest).
"""

from __future__ import annotations

import argparse
import glob
import os
import re

import numpy as np
import torch

SH_C0 = 0.28209479177387814


def newest_ckpt(path: str) -> str:
    if path.endswith(".pt"):
        return path
    cands = glob.glob(os.path.join(path, "**", "ckpt_*.pt"), recursive=True)
    if not cands:
        raise SystemExit(f"no ckpt_*.pt under {path}")

    def it(p: str) -> int:
        m = re.search(r"ckpt_(\d+)\.pt", os.path.basename(p))
        return int(m.group(1)) if m else -1

    return max(cands, key=it)


def convert(ckpt_path: str, out_path: str, *, flip_y: bool = True,
            trim_pct: float = 1.0, min_opacity: float = 0.02,
            max_gaussians: int = 0) -> dict:
    c = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    def arr(k):  # params carry grad; detach before numpy
        return c[k].detach().cpu().numpy().astype(np.float64)

    xyz = arr("positions")
    quats = arr("rotation")
    scales = np.exp(np.clip(arr("scale"), -30, 10))
    op = (1.0 / (1.0 + np.exp(-np.clip(arr("density"), -50, 50)))).reshape(-1)
    rgb = 0.5 + SH_C0 * arr("features_albedo")

    n0 = len(xyz)
    keep = (np.isfinite(xyz).all(1) & np.isfinite(scales).all(1)
            & np.isfinite(quats).all(1) & np.isfinite(rgb).all(1)
            & np.isfinite(op) & (op >= min_opacity))
    if trim_pct > 0:
        g = xyz[keep]
        lo = np.percentile(g, trim_pct, axis=0)
        hi = np.percentile(g, 100 - trim_pct, axis=0)
        c0, e = (lo + hi) / 2, (hi - lo) * 1.6
        keep &= ((xyz >= c0 - e / 2) & (xyz <= c0 + e / 2)).all(1)

    xyz, quats, scales, op, rgb = (a[keep] for a in (xyz, quats, scales, op, rgb))
    if max_gaussians and len(xyz) > max_gaussians:
        idx = np.argsort(-(op * scales.prod(1)))[:max_gaussians]
        xyz, quats, scales, op, rgb = (a[idx] for a in (xyz, quats, scales, op, rgb))

    if flip_y:  # OpenCV (+y down) -> viewer (+y up)
        xyz[:, 1] *= -1
        xyz[:, 2] *= -1
        w, x, y, z = quats.T
        quats = np.stack([-x, w, z, -y], 1)
    nrm = np.linalg.norm(quats, axis=1, keepdims=True)
    quats = quats / np.where(nrm > 0, nrm, 1.0)

    n = len(xyz)
    buf = np.zeros((n, 32), dtype=np.uint8)
    buf[:, 0:12] = xyz.astype(np.float32).view(np.uint8).reshape(n, 12)
    buf[:, 12:24] = scales.astype(np.float32).view(np.uint8).reshape(n, 12)
    buf[:, 24:27] = np.clip(rgb * 255, 0, 255).astype(np.uint8)
    buf[:, 27] = np.clip(op * 255, 0, 255).astype(np.uint8)
    buf[:, 28:32] = np.clip(quats * 128 + 128, 0, 255).astype(np.uint8)
    with open(out_path, "wb") as fh:
        fh.write(buf.tobytes())

    m = re.search(r"ckpt_(\d+)\.pt", os.path.basename(ckpt_path))
    cen = np.median(xyz, 0)
    lo, hi = np.percentile(xyz, [2, 98], axis=0)
    r = float(np.max(hi - lo))
    return {
        "ckpt": ckpt_path, "iteration": int(m.group(1)) if m else None,
        "n_in": n0, "n_out": n, "out": out_path,
        "out_mb": round(os.path.getsize(out_path) / 1e6, 1),
        "cam_pos": [round(float(cen[0] + r * .9), 3), round(float(cen[1] + r * .7), 3),
                    round(float(cen[2] + r * .9), 3)],
        "cam_look": [round(float(x), 3) for x in cen],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ckpt", help="a ckpt_*.pt, or a run dir to scan for the newest")
    ap.add_argument("--out", help="default: live_iter<N>.splat in bench_out/view/")
    ap.add_argument("--max", type=int, default=0, help="cap gaussian count")
    ap.add_argument("--no-flip-y", action="store_true")
    args = ap.parse_args()

    ck = newest_ckpt(args.ckpt)
    out = args.out
    if not out:
        m = re.search(r"ckpt_(\d+)\.pt", os.path.basename(ck))
        out = os.path.join("bench_out", "view",
                           f"live_iter{m.group(1) if m else 'x'}.splat")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    import json
    print(json.dumps(convert(ck, out, flip_y=not args.no_flip_y,
                             max_gaussians=args.max), indent=2))


if __name__ == "__main__":
    main()
