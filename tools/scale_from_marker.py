#!/usr/bin/env python3
"""Absolute scale from a printed ArUco marker placed next to the object.

Use for RGB-only captures (no depth): a DICT_4X4_50 marker of known edge
length in frame gives mm-per-pixel at the marker plane, and — with the object
resting on the same surface — an approximate metric bounding box that the
rest of the pipeline (routing, metric export) can consume as dims.json.

Print a marker:   python tools/scale_from_marker.py --make-marker 50
Measure an image: python tools/scale_from_marker.py photo.jpg --marker-mm 50 \
                      [--mask mask.png] [--out dims.json]

In RGB-D sessions the depth-derived dims stay authoritative; a detected
marker is reported as a cross-check only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

DICT = cv2.aruco.DICT_4X4_50


def make_marker(edge_mm: float, out: Path, marker_id: int = 0, dpi: int = 300) -> None:
    px = int(round(edge_mm / 25.4 * dpi))
    img = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(DICT), marker_id, px)
    border = px // 5
    canvas = np.full((px + 2 * border, px + 2 * border), 255, np.uint8)
    canvas[border:border + px, border:border + px] = img
    cv2.imwrite(str(out), canvas)
    print(f"marker id={marker_id} edge={edge_mm}mm at {dpi}dpi -> {out}")
    print(f"print at 100% scale; printed marker square must measure {edge_mm}mm")


def detect_scale(image_path: Path, marker_mm: float) -> dict | None:
    img = cv2.imread(str(image_path))
    if img is None:
        raise FileNotFoundError(image_path)
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICT),
                                  cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    if ids is None or len(ids) == 0:
        return None
    # use the largest detected marker (closest / least blurred)
    areas = [cv2.contourArea(c.reshape(-1, 1, 2).astype(np.float32)) for c in corners]
    k = int(np.argmax(areas))
    c = corners[k].reshape(4, 2)
    edges_px = [np.linalg.norm(c[i] - c[(i + 1) % 4]) for i in range(4)]
    edge_px = float(np.mean(edges_px))
    spread = float(np.std(edges_px) / edge_px)
    return {
        "marker_id": int(np.asarray(ids).flatten()[k]),
        "edge_px": edge_px,
        "mm_per_px": marker_mm / edge_px,
        "edge_spread_frac": spread,  # >0.05 = strong perspective; scale less reliable
        "image": str(image_path),
        "image_size": [img.shape[1], img.shape[0]],
    }


def object_bbox_px(image_path: Path, mask_path: Path | None):
    if mask_path and mask_path.is_file():
        m = cv2.imread(str(mask_path), 0)
        ys, xs = np.where(m > 127)
        if len(xs) == 0:
            return None
        return float(xs.max() - xs.min()), float(ys.max() - ys.min())
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("image", nargs="?", help="photo containing the marker")
    ap.add_argument("--marker-mm", type=float, default=50.0, help="printed marker edge (mm)")
    ap.add_argument("--mask", type=Path, help="optional object mask to measure a bbox")
    ap.add_argument("--out", type=Path, help="write dims.json-style output here")
    ap.add_argument("--make-marker", type=float, metavar="MM",
                    help="generate a printable marker of this edge length instead")
    args = ap.parse_args()

    if args.make_marker:
        make_marker(args.make_marker, Path(f"aruco_{int(args.make_marker)}mm.png"))
        return 0
    if not args.image:
        ap.error("image required (or --make-marker)")

    res = detect_scale(Path(args.image), args.marker_mm)
    if res is None:
        print("!! no ArUco marker found (DICT_4X4_50)")
        return 1
    print(f"marker {res['marker_id']}: {res['edge_px']:.1f}px  ->  {res['mm_per_px']:.4f} mm/px"
          f"  (perspective spread {res['edge_spread_frac']*100:.1f}%)")

    bbox = object_bbox_px(Path(args.image), args.mask)
    if bbox:
        w_mm, h_mm = bbox[0] * res["mm_per_px"], bbox[1] * res["mm_per_px"]
        res["object_bbox_mm"] = {"width": round(w_mm, 1), "height": round(h_mm, 1)}
        res["note"] = ("same-plane approximation: object and marker assumed at similar "
                       "distance; expect ~5-10% error vs RGB-D depth")
        print(f"object bbox ≈ {w_mm:.0f} × {h_mm:.0f} mm (same-plane approx)")
    if args.out:
        args.out.write_text(json.dumps(res, indent=2) + "\n")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
