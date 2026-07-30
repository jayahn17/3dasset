#!/usr/bin/env python3
"""Repair a trained splat: drop diverged gaussians, crop to the measured object.

Two failures make a raw 3DGUT export unviewable:

  1. **Divergence** — a fraction of gaussians land at 1e30/inf positions. Any
     web viewer builds its bbox and depth sort from those, so the camera is
     placed at infinity and the canvas renders blank. (gsplat's offline
     rasterizer silently culls them, which is why headless renders looked OK
     while the browser showed nothing.)
  2. **Scene, not object** — training covers everything the camera saw, which
     for a tabletop/floor orbit is mostly carpet. The object is a few percent
     of the extent, so even a correctly framed viewer shows a floor patch.

This crops to the object AABB already measured by `object_crop.json` (same
world frame — both come from the pose-normalized session), so the result is an
object splat you can actually inspect.

    python tools/clean_splat.py bench_out/CrateScan-1B38880A/ours_3dgut_30k/scene_gaussians.ply
    python tools/clean_splat.py <splat.ply> --no-crop      # only strip divergence
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np
from plyfile import PlyData, PlyElement


def find_object_crop(splat_path: str) -> str | None:
    """object_crop.json lives in the session's ours_tsdf/ sibling dir."""
    session_dir = os.path.dirname(os.path.dirname(os.path.abspath(splat_path)))
    cand = os.path.join(session_dir, "ours_tsdf", "object_crop.json")
    return cand if os.path.isfile(cand) else None


def clean(
    path: str,
    out_path: str,
    *,
    crop: bool = True,
    pad: float = 1.25,
    trim_pct: float = 0.0,
    min_opacity: float = 0.02,
    max_abs: float = 1e4,
) -> dict:
    ply = PlyData.read(path)
    v = ply["vertex"]
    n0 = len(v)
    xyz = np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float64)

    keep = np.isfinite(xyz).all(1) & (np.abs(xyz) < max_abs).all(1)
    stats = {"n_in": n0, "dropped_diverged": int((~keep).sum())}

    # opacity is stored as logit; sigmoid it without overflowing
    op_raw = np.asarray(v["opacity"], np.float64)
    op = np.where(np.isfinite(op_raw), 1.0 / (1.0 + np.exp(-np.clip(op_raw, -50, 50))), 0.0)
    faint = keep & (op < min_opacity)
    keep &= op >= min_opacity
    stats["dropped_faint"] = int(faint.sum())

    box = None
    if trim_pct and trim_pct > 0:
        # Keep the dense core; stray gaussians (metres away) are what break
        # viewer auto-framing. Percentile bbox on the surviving points only.
        g = xyz[keep]
        lo = np.percentile(g, trim_pct, axis=0)
        hi = np.percentile(g, 100 - trim_pct, axis=0)
        c, e = (lo + hi) / 2, (hi - lo)
        lo, hi = c - e * pad / 2, c + e * pad / 2
        inside = ((xyz >= lo) & (xyz <= hi)).all(1)
        stats["dropped_outliers"] = int((keep & ~inside).sum())
        keep &= inside
        box = {"mode": f"percentile {trim_pct}-{100 - trim_pct} x{pad}",
               "center_m": c.tolist(), "extent_m": (e * pad).tolist()}
    elif crop:
        oc_path = find_object_crop(path)
        if oc_path:
            with open(oc_path) as fh:
                oc = json.load(fh)
            c = np.asarray(oc["center_m"], np.float64)
            e = np.asarray(oc["extent_m"], np.float64) * pad
            lo, hi = c - e / 2, c + e / 2
            inside = ((xyz >= lo) & (xyz <= hi)).all(1)
            stats["dropped_outside_object"] = int((keep & ~inside).sum())
            keep &= inside
            box = {"center_m": c.tolist(), "extent_m": e.tolist(),
                   "source": oc_path}
        else:
            stats["crop"] = "no object_crop.json found — kept full scene"

    n1 = int(keep.sum())
    if n1 == 0:
        raise SystemExit(f"nothing survived cleaning of {path}")

    el = PlyElement.describe(v.data[keep], "vertex")
    PlyData([el], byte_order="<").write(out_path)

    stats.update(
        n_out=n1,
        kept_pct=round(100.0 * n1 / n0, 2),
        out=out_path,
        out_mb=round(os.path.getsize(out_path) / 1e6, 1),
        object_box=box,
    )
    if n1:
        g = xyz[keep]
        stats["extent_m"] = (g.max(0) - g.min(0)).round(3).tolist()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("splat", help="3DGS .ply from training")
    ap.add_argument("--out", help="default: <name>_clean.ply beside the input")
    ap.add_argument("--no-crop", action="store_true",
                    help="keep the full scene, only strip diverged gaussians")
    ap.add_argument("--pad", type=float, default=1.25,
                    help="box padding factor (default 1.25)")
    ap.add_argument("--trim-pct", type=float, default=0.0,
                    help="keep the percentile core instead of the object box, "
                         "e.g. 1.0 keeps the 1-99%% bbox (full scene, no strays)")
    ap.add_argument("--min-opacity", type=float, default=0.02)
    args = ap.parse_args()

    out = args.out or os.path.splitext(args.splat)[0] + "_clean.ply"
    stats = clean(args.splat, out, crop=not args.no_crop, pad=args.pad,
                  min_opacity=args.min_opacity, trim_pct=args.trim_pct)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
