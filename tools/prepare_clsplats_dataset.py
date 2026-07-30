#!/usr/bin/env python3
"""Build a CL-Splats dual-timestep COLMAP dataset from a 3DGRUT export.

CL-Splats reads timesteps from image names matching ``day_<t>_...``.
This script duplicates the posed views as day_0 (base) and day_1 (refine),
optionally using half-res ``images_2`` so DINOv2 / Depth-Anything fit in VRAM.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


def _parse_images_txt(path: Path) -> list[tuple[str, str]]:
    """Return list of (header_line_without_name, image_name) for each image."""
    lines = path.read_text().splitlines()
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        # IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME
        if len(parts) < 10:
            raise ValueError(f"Bad images.txt line: {line}")
        name = parts[9]
        # pose fields only (QW..CAMERA_ID); IMAGE_ID is reassigned per day
        header = " ".join(parts[1:9])
        entries.append((header, name))
        # Skip points2D line if present
        if i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("#"):
            # empty points line is still a line
            i += 1
    return entries


def _scale_cameras_txt(src: Path, dst: Path, scale: float) -> None:
    out: list[str] = []
    for line in src.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            out.append(line)
            continue
        parts = line.split()
        # CAMERA_ID MODEL WIDTH HEIGHT PARAMS...
        cam_id, model = parts[0], parts[1]
        width = max(1, int(round(float(parts[2]) * scale)))
        height = max(1, int(round(float(parts[3]) * scale)))
        params = [float(p) * scale for p in parts[4:]]
        param_str = " ".join(f"{p:.6f}" if not float(p).is_integer() else str(p) for p in params)
        # keep float formatting simple
        param_str = " ".join(str(p) for p in params)
        out.append(f"{cam_id} {model} {width} {height} {param_str}")
    dst.write_text("\n".join(out) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--src",
        type=Path,
        required=True,
        help="3DGRUT COLMAP root (contains images/ and sparse/0/)",
    )
    ap.add_argument("--out", type=Path, required=True, help="Output CL-Splats dataset root")
    ap.add_argument(
        "--images",
        default="images_2",
        help="Image folder under src (default: images_2 half-res)",
    )
    ap.add_argument(
        "--scale",
        type=float,
        default=0.5,
        help="Intrinsic scale relative to sparse/0/cameras.txt (0.5 for images_2)",
    )
    args = ap.parse_args()

    src = args.src.resolve()
    out = args.out.resolve()
    img_src = src / args.images
    sparse_src = src / "sparse" / "0"
    if not img_src.is_dir():
        raise SystemExit(f"Missing images: {img_src}")
    if not (sparse_src / "images.txt").is_file():
        raise SystemExit(f"Missing {sparse_src / 'images.txt'}")

    img_out = out / "images"
    sparse_out = out / "sparse" / "0"
    if out.exists():
        shutil.rmtree(out)
    img_out.mkdir(parents=True)
    sparse_out.mkdir(parents=True)

    entries = _parse_images_txt(sparse_src / "images.txt")
    new_images_txt: list[str] = [
        "# IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME",
        "# CL-Splats dual-timestep export (day_0 base, day_1 refine)",
    ]
    image_id = 1
    for day in (0, 1):
        for header, name in entries:
            src_img = img_src / name
            if not src_img.is_file():
                raise SystemExit(f"Missing image {src_img}")
            # Preserve extension; CL-Splats timesteps via day_<t>_ prefix
            stem = Path(name).stem
            ext = Path(name).suffix
            # Avoid double-prefix if already day_*
            stem = re.sub(r"^day_\d+_", "", stem)
            new_name = f"day_{day}_{stem}{ext}"
            target = img_out / new_name
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(src_img)
            new_images_txt.append(f"{image_id} {header} {new_name}")
            new_images_txt.append("")  # empty POINTS2D line
            image_id += 1

    (sparse_out / "images.txt").write_text("\n".join(new_images_txt) + "\n")
    _scale_cameras_txt(sparse_src / "cameras.txt", sparse_out / "cameras.txt", args.scale)
    shutil.copy2(sparse_src / "points3D.txt", sparse_out / "points3D.txt")

    print(f"✔ wrote {out}")
    print(f"  images: {len(entries)} × 2 timesteps → {image_id - 1} entries")
    print(f"  using {args.images} @ scale={args.scale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
