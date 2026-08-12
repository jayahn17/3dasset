#!/usr/bin/env python3
"""Metric-scale photogrammetry from AprilTag markers, via AliceVision.

Unlike tools/scale_from_marker.py — which reads mm-per-pixel off a single photo
and assumes the object shares the marker's plane — this solves the whole scene
and takes scale from the reconstructed 3D marker positions. No same-plane
approximation, so an object's height is as trustworthy as its footprint.

Print markers:  python tools/alicevision_metric.py --make-markers 4 --marker-mm 60
Reconstruct:    python tools/alicevision_metric.py --images photos/ --out recon/ \\
                    --markers 0:0,0,0 1:0.2,0,0 2:0,0.24,0

Marker coordinates are metres in whatever frame you want the output in; place
the printed tags at those positions. Two markers fix scale, three fix the full
frame. Any extra detected markers are reported as a held-out accuracy check.

Requires the AliceVision binaries (set ALICEVISION_ROOT, default
~/alicevision-3.3.0). Note the official 3.3.0 build ships tag16h5 only — it has
no CCTag describer compiled in, despite bundling libCCTag.so.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

AV_ROOT = Path(os.environ.get("ALICEVISION_ROOT", Path.home() / "alicevision-3.3.0"))
DESC = "sift,tag16h5"
# AprilTag 16h5 holds only 30 codes and has a high false-positive rate; ids stay
# low and every marker is cross-checked against its expected position.
APRILTAG_DICT = "DICT_APRILTAG_16h5"


def _run(exe: str, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    binary = AV_ROOT / "bin" / exe
    if not binary.is_file():
        sys.exit(f"!! {binary} not found — set ALICEVISION_ROOT to your AliceVision install")
    env = dict(os.environ)
    # Both are required, or every binary aborts on the embedded OCIO config.
    env["ALICEVISION_ROOT"] = str(AV_ROOT)
    env["LD_LIBRARY_PATH"] = f"{AV_ROOT / 'lib'}:{env.get('LD_LIBRARY_PATH', '')}"
    proc = subprocess.run([str(binary), *args], env=env, capture_output=True, text=True)
    if check and proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])
        sys.exit(f"!! {exe} failed:\n{tail}")
    return proc


def _draw_marker(aruco, d, mid: int, px: int):
    """OpenCV renamed drawMarker -> generateImageMarker in 4.7; system python3
    here still ships 4.5.4, so support both rather than forcing a conda env."""
    if hasattr(aruco, "generateImageMarker"):
        return aruco.generateImageMarker(d, mid, px)
    return aruco.drawMarker(d, mid, px)


def make_markers(count: int, edge_mm: float, dpi: int, out: Path) -> None:
    import cv2
    import numpy as np

    out.mkdir(parents=True, exist_ok=True)
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, APRILTAG_DICT))
    px = int(round(edge_mm / 25.4 * dpi))
    quiet = px // 4  # white margin; detection needs it, do not crop
    for mid in range(count):
        tag = _draw_marker(cv2.aruco, d, mid, px)
        canvas = np.full((px + 2 * quiet, px + 2 * quiet), 255, np.uint8)
        canvas[quiet:quiet + px, quiet:quiet + px] = tag
        cv2.putText(canvas, f"id {mid}  {edge_mm:g}mm", (6, quiet - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, 0, 1, cv2.LINE_AA)
        cv2.imwrite(str(out / f"apriltag16h5_{mid}_{int(edge_mm)}mm.png"), canvas)
    print(f"wrote {count} markers -> {out}")
    print(f"print at 100% scale; the black square must measure {edge_mm:g} mm on paper")
    print("keep the white margin; lay the tags flat, unfolded, and free of glare")


def reconstruct(images: Path, out: Path, markers: dict[int, tuple[float, float, float]],
                fov: float | None, force_cpu: bool = False) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    for sub in ("features", "matches", "sfm"):
        (out / sub).mkdir(exist_ok=True)
    cam = out / "cameraInit.sfm"

    init = ["--imageFolder", str(images), "--output", str(cam), "--allowSingleView", "1"]
    if fov:
        init += ["--defaultFieldOfView", str(fov)]
    print("[1/5] cameraInit")
    _run("aliceVision_cameraInit", init)

    print("[2/5] featureExtraction (sift + tag16h5)")
    feat = ["--input", str(cam), "--output", str(out / "features"), "-d", DESC]
    if force_cpu:
        _run("aliceVision_featureExtraction", feat + ["--forceCpuExtraction", "1"])
    else:
        # popsift allocates its DoG pyramid up front and aborts if the GPU is
        # busy (a training job easily leaves too little free VRAM for 12 MP
        # frames). Fall back rather than losing the run.
        proc = _run("aliceVision_featureExtraction", feat, check=False)
        if proc.returncode != 0:
            if "out of memory" not in (proc.stdout + proc.stderr):
                tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])
                sys.exit(f"!! aliceVision_featureExtraction failed:\n{tail}")
            print("      GPU out of memory — retrying on CPU (slower)")
            _run("aliceVision_featureExtraction", feat + ["--forceCpuExtraction", "1"])

    print("[3/5] featureMatching (exhaustive)")
    _run("aliceVision_featureMatching",
         ["--input", str(cam), "--featuresFolders", str(out / "features"),
          "--output", str(out / "matches"), "-d", DESC])

    print("[4/5] incrementalSfM")
    sfm = out / "sfm" / "sfm.sfm"
    _run("aliceVision_incrementalSfM",
         ["--input", str(cam), "--featuresFolders", str(out / "features"),
          "--matchesFolders", str(out / "matches"), "-d", DESC,
          "--output", str(sfm), "--extraInfoFolder", str(out / "sfm")])

    print("[5/5] sfmTransform (from_markers)")
    aligned = out / "sfm" / "aligned.sfm"
    spec = [f"{mid}:{x},{y},{z}" for mid, (x, y, z) in sorted(markers.items())]
    _run("aliceVision_sfmTransform",
         ["--input", str(sfm), "--method", "from_markers", "-d", "tag16h5",
          "--markers", *spec, "--output", str(aligned)])
    return aligned


def report(aligned: Path, markers: dict[int, tuple[float, float, float]]) -> None:
    import numpy as np
    from scipy.cluster.hierarchy import fcluster, linkage

    scene = json.loads(aligned.read_text())
    pts = np.array([[float(v) for v in l["X"]] for l in scene.get("structure", [])
                    if l.get("descType") == "tag16h5"])
    if len(pts) < 5:
        print(f"!! only {len(pts)} tag16h5 landmarks reconstructed — markers too small,"
              " too blurred, or seen from too few views")
        return
    n_tags = max(1, round(len(pts) / 5))  # 4 corners + centre per tag
    lab = fcluster(linkage(pts, "average"), n_tags, criterion="maxclust")
    cent = np.array([pts[lab == k].mean(0) for k in sorted(set(lab))])
    print(f"\nreconstructed {len(pts)} tag16h5 points in {n_tags} marker cluster(s)")
    print(f"{'tag':>5}  {'expected (m)':>26}  {'recovered (m)':>26}  {'err':>9}  role")
    for mid, g in sorted(markers.items()):
        gv = np.array(g, float)
        k = int(np.argmin(np.linalg.norm(cent - gv, axis=1)))
        err = float(np.linalg.norm(cent[k] - gv)) * 1000
        print(f"{mid:>5}  ({gv[0]:>7.4f},{gv[1]:>7.4f},{gv[2]:>7.4f})  "
              f"({cent[k][0]:>7.4f},{cent[k][1]:>7.4f},{cent[k][2]:>7.4f})  {err:>6.2f} mm  fit")
    extra = len(cent) - len(markers)
    if extra > 0:
        print(f"  ({extra} further marker cluster(s) detected but not constrained — "
              "check their spacing against the printed layout as an independent test)")
    print(f"\nscaled scene: {aligned}")


def parse_marker(s: str) -> tuple[int, tuple[float, float, float]]:
    mid, _, coords = s.partition(":")
    xyz = tuple(float(v) for v in coords.split(","))
    if len(xyz) != 3:
        raise argparse.ArgumentTypeError(f"expected ID:X,Y,Z — got {s!r}")
    return int(mid), xyz  # type: ignore[return-value]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--make-markers", type=int, metavar="N",
                    help="generate N printable AprilTag 16h5 markers and exit")
    ap.add_argument("--marker-mm", type=float, default=60.0,
                    help="printed marker edge in mm (default 60; below ~40mm detection suffers)")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--images", type=Path, help="folder of capture images")
    ap.add_argument("--out", type=Path, default=Path("av_metric"), help="output folder")
    ap.add_argument("--markers", type=parse_marker, nargs="+", metavar="ID:X,Y,Z",
                    help="marker ids and their true positions in metres")
    ap.add_argument("--fov", type=float, help="default horizontal FoV in degrees, "
                                              "for images with no EXIF intrinsics")
    ap.add_argument("--cpu", action="store_true",
                    help="force CPU feature extraction (skip the GPU attempt)")
    args = ap.parse_args()

    if args.make_markers:
        make_markers(args.make_markers, args.marker_mm, args.dpi, args.out)
        return 0
    if not args.images or not args.markers:
        ap.error("--images and --markers are both required (or use --make-markers)")
    if len(args.markers) < 2:
        ap.error("at least 2 markers are needed to fix scale (3 to fix the full frame)")

    markers = dict(args.markers)
    aligned = reconstruct(args.images, args.out, markers, args.fov, args.cpu)
    report(aligned, markers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
