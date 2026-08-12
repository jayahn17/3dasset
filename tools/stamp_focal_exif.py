#!/usr/bin/env python3
"""Stamp the capture's real focal length into the JPEGs as EXIF.

The CrateScanner app writes no camera EXIF, so AliceVision falls back to a 45°
default field of view — a ~52% error in focal length on this device, which no
amount of bundle adjustment starts from a good place. The true intrinsics are
sitting in the session manifest (K_color), so convert fx to a 35 mm-equivalent
focal and write it where photogrammetry actually looks for it.

    focal_px = f35 * width / 36     (36 mm = full-frame sensor width)
"""
import json
import shutil
import sys
from pathlib import Path

import piexif

session = Path(sys.argv[1])
src_dir = Path(sys.argv[2])
dst_dir = Path(sys.argv[3])

man = json.loads((session / "manifest.json").read_text())
frames = man.get("keyframes") or man.get("frames")
by_name = {}
for fr in frames:
    name = Path(fr["color"]).name
    K = fr.get("K_color")
    size = fr.get("color_size")
    if K and size:
        by_name[name] = (float(K[0]), int(size[0]))

dst_dir.mkdir(parents=True, exist_ok=True)
stamped = skipped = 0
f35s = []

# ONE focal for every frame. The per-frame values differ by ~0.1 mm (autofocus
# breathing), and AliceVision groups intrinsics by exact focal — writing them
# verbatim split 31 images into 5 camera groups, so each group refined its
# intrinsics from ~6 views instead of all 31. It is one physical camera, so it
# should be one intrinsic group; SfM refines the shared focal from there.
shared_f35 = sum(36.0 * fx / W for fx, W in by_name.values()) / max(len(by_name), 1)

for jpg in sorted(src_dir.glob("*.jpg")):
    out = dst_dir / jpg.name
    shutil.copy2(jpg, out)
    hit = by_name.get(jpg.name)
    if not hit:
        skipped += 1
        continue
    fx, W = hit
    f35s.append(36.0 * fx / W)
    f35 = shared_f35
    exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
    # FocalLengthIn35mmFilm is a SHORT, so it rounds; FocalLength is a rational
    # and carries the exact value for anything that prefers it.
    exif["Exif"][piexif.ExifIFD.FocalLengthIn35mmFilm] = int(round(f35))
    exif["Exif"][piexif.ExifIFD.FocalLength] = (int(round(f35 * 100)), 100)
    exif["0th"][piexif.ImageIFD.Make] = b"Apple"
    exif["0th"][piexif.ImageIFD.Model] = b"CrateScanner"
    piexif.insert(piexif.dump(exif), str(out))
    stamped += 1

print(f"  stamped {stamped} images, {skipped} without manifest intrinsics")
if f35s:
    print(f"  f35 range: {min(f35s):.2f} – {max(f35s):.2f} mm  (rounded to {int(round(sum(f35s)/len(f35s)))} mm in EXIF)")
    W = by_name[next(iter(by_name))][1]
    eff = int(round(sum(f35s)/len(f35s))) * W / 36.0
    true = sum(by_name[k][0] for k in by_name)/len(by_name)
    print(f"  focal px from EXIF: {eff:.0f}   true fx: {true:.0f}   error: {abs(eff-true)/true*100:.2f}%")
    print(f"  (45° default would give {W/(2*0.41421356):.0f} px — {abs(W/(2*0.41421356)-true)/true*100:.0f}% error)")
