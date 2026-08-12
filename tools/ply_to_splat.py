#!/usr/bin/env python3
"""3DGS .ply → antimatter15 .splat, the format web viewers load reliably.

Our 62-property training PLY is ~236 bytes/gaussian; .splat is 32, so a 1M
gaussian scene goes 241 MB → 31 MB and streams instantly. `write_splat` in
assetpipe.scene.io only handles point clouds (isotropic, identity rotation) —
this preserves real anisotropic scales, rotations, SH colour and opacity.

Also bakes two viewer fixes so HTML splat viewers Just Work:
  1. Axis: OpenCV (+y down) → web (+y up) via 180° about X.
  2. Origin: ARKit/COLMAP scenes often sit meters from (0,0,0); web
     viewers look at the origin by default, so we recenter on the robust
     median. Relative metric scale is preserved; the translation offset is
     written beside the .splat as ``*.splat.meta.json``.

    python tools/ply_to_splat.py bench_out/.../full_30k.ply
    python tools/ply_to_splat.py <in.ply> --no-flip-y --max 400000
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np
from plyfile import PlyData

SH_C0 = 0.28209479177387814


def convert(path: str, out_path: str, *, flip_y: bool = True,
            center: bool = True, max_gaussians: int = 0,
            min_opacity: float = 0.02, crop_radius: float = 0.0,
            drop_floor: bool = False) -> dict:
    v = PlyData.read(path)["vertex"]
    n0 = len(v)

    xyz = np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float64)
    op = 1.0 / (1.0 + np.exp(-np.clip(np.asarray(v["opacity"], np.float64), -50, 50)))
    scales = np.exp(np.clip(np.stack([v[f"scale_{i}"] for i in range(3)], 1)
                            .astype(np.float64), -30, 10))
    quats = np.stack([v[f"rot_{i}"] for i in range(4)], 1).astype(np.float64)
    rgb = 0.5 + SH_C0 * np.stack([v[f"f_dc_{i}"] for i in range(3)], 1).astype(np.float64)

    keep = (np.isfinite(xyz).all(1) & np.isfinite(scales).all(1)
            & np.isfinite(quats).all(1) & np.isfinite(rgb).all(1)
            & np.isfinite(op) & (op >= min_opacity)
            & (np.abs(xyz) < 1e4).all(1))
    xyz, op, scales, quats, rgb = (a[keep] for a in (xyz, op, scales, quats, rgb))
    n1 = len(xyz)

    if max_gaussians and n1 > max_gaussians:
        # keep the visually dominant ones: opacity x volume
        importance = op * scales.prod(1)
        idx = np.argsort(-importance)[:max_gaussians]
        xyz, op, scales, quats, rgb = (a[idx] for a in (xyz, op, scales, quats, rgb))

    if flip_y:
        # 180° about X: OpenCV (+y down) -> viewer convention (+y up)
        xyz[:, 1] *= -1
        xyz[:, 2] *= -1
        w, x, y, z = quats.T
        quats = np.stack([-x, w, z, -y], 1)

    offset = [0.0, 0.0, 0.0]
    radius = 1.0
    n_outlier = 0
    if len(xyz):
        # Drop spatial floaters from the *web* splat. They inflate the
        # viewer's bounding radius and park the camera inside / past the
        # real scene (blank canvas). Full PLY stays untouched.
        lo = np.percentile(xyz, 1, axis=0)
        hi = np.percentile(xyz, 99, axis=0)
        inlier = np.all((xyz >= lo) & (xyz <= hi), axis=1)
        if inlier.any() and int(inlier.sum()) < len(xyz):
            n_outlier = int((~inlier).sum())
            xyz, op, scales, quats, rgb = (
                a[inlier] for a in (xyz, op, scales, quats, rgb)
            )
        if center:
            mid = np.median(xyz, axis=0)
            xyz = xyz - mid
            offset = [float(x) for x in mid]
        if drop_floor and len(xyz) > 2000:
            # The subject sits ON something, and that something is a DENSE flat
            # sheet of gaussians. Cropped to the object it becomes the largest
            # surface in the panel, and the baked camera looks along it edge-on
            # — the object disappears behind a wall of white fog. Verified on
            # the chair: floor plane held more gaussians than the chair itself.
            # Only fires when the plane is thin AND heavy, so a genuinely flat
            # subject (a book lying down) is never gutted.
            yv = xyz[:, 1]
            lo_y, hi_y = np.percentile(yv, [1, 99])
            if hi_y - lo_y > 1e-3:
                hh, ee = np.histogram(yv, bins=120, range=(lo_y, hi_y))
                k = int(hh.argmax())
                plane = 0.5 * (ee[k] + ee[k + 1])
                half = max((hi_y - lo_y) / 40.0, 0.01)
                slab = np.abs(yv - plane) <= half
                frac = slab.mean()
                # a floor is near an extreme of the vertical range, not mid-object
                at_edge = (plane - lo_y) < (hi_y - lo_y) * 0.3 or \
                          (hi_y - plane) < (hi_y - lo_y) * 0.3
                if 0.10 < frac < 0.60 and at_edge:
                    keep = ~slab
                    n_outlier += int(slab.sum())
                    xyz, op, scales, quats, rgb = (
                        a[keep] for a in (xyz, op, scales, quats, rgb)
                    )
        if crop_radius and crop_radius > 0:
            # Hard spatial crop about the (centered) subject. The 1–99% box
            # above removes statistical outliers, but a room-scale tail —
            # walls, carpet, floaters several metres out — survives it and
            # renders as fog around the object in the web viewer. For panel
            # embeds the subject IS the deliverable, so everything beyond this
            # radius goes.
            keep = np.linalg.norm(xyz, axis=1) <= crop_radius
            n_outlier += int((~keep).sum())
            xyz, op, scales, quats, rgb = (
                a[keep] for a in (xyz, op, scales, quats, rgb)
            )
        radius = float(np.percentile(np.linalg.norm(xyz, axis=1), 95))
        radius = max(radius, 0.25)

    norm = np.linalg.norm(quats, axis=1, keepdims=True)
    quats = quats / np.where(norm > 0, norm, 1.0)

    n = len(xyz)
    buf = np.zeros((n, 32), dtype=np.uint8)
    buf[:, 0:12] = xyz.astype(np.float32).view(np.uint8).reshape(n, 12)
    buf[:, 12:24] = scales.astype(np.float32).view(np.uint8).reshape(n, 12)
    buf[:, 24:27] = np.clip(rgb * 255, 0, 255).astype(np.uint8)
    buf[:, 27] = np.clip(op * 255, 0, 255).astype(np.uint8)
    buf[:, 28:32] = np.clip(quats * 128 + 128, 0, 255).astype(np.uint8)

    with open(out_path, "wb") as fh:
        fh.write(buf.tobytes())

    meta = {
        "in": path, "n_in": n0, "n_out": n,
        "dropped_invalid": n0 - n1,
        "dropped_outliers": n_outlier,
        "downsampled_to": max_gaussians or None,
        "flip_y": flip_y, "center": center,
        "offset_viewer_from_world": offset,
        "radius_m": radius,
        "out": out_path,
        "out_mb": round(os.path.getsize(out_path) / 1e6, 1),
    }
    meta_path = out_path + ".meta.json"
    with open(meta_path, "w") as fh:
        json.dump(meta, fh, indent=2)
    meta["meta"] = meta_path
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ply")
    ap.add_argument("--out", help="default: <name>.splat beside the input")
    ap.add_argument("--no-flip-y", action="store_true",
                    help="keep the OpenCV +y-down frame as-is")
    ap.add_argument("--no-center", action="store_true",
                    help="keep world translation (HTML viewers look at origin)")
    ap.add_argument("--max", type=int, default=0,
                    help="cap gaussian count, keeping the most visible")
    ap.add_argument("--min-opacity", type=float, default=0.02)
    ap.add_argument("--drop-floor", action="store_true",
                    help="remove the dense flat plane the subject rests on")
    ap.add_argument("--crop-radius", type=float, default=0.0,
                    help="drop splats farther than this many metres from the "
                         "centred subject (0 = keep everything)")
    args = ap.parse_args()

    out = args.out or os.path.splitext(args.ply)[0] + ".splat"
    print(json.dumps(convert(args.ply, out, flip_y=not args.no_flip_y,
                             center=not args.no_center,
                             max_gaussians=args.max,
                             min_opacity=args.min_opacity,
                             crop_radius=args.crop_radius,
                             drop_floor=args.drop_floor), indent=2))


if __name__ == "__main__":
    main()
