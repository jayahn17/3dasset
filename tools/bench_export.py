#!/usr/bin/env python3
"""Export an RGB-D session into industry-API benchmark inputs.

Commercial services (Kiri Engine, World Labs Marble, Polycam, Luma, ...) accept
RGB photos and/or video — none of them take our depth/pose stream. This tool
turns one CrateScanner session into the two upload shapes they all share:

    bench_out/<name>/photos/photo_0000.jpg   curated multi-view photo set
    bench_out/<name>/video.mp4               color stream as a walkthrough video
    bench_out/<name>/meta.json               what was exported + capture stats

Frame selection reuses the curation stack (sharpness x viewpoint diversity)
so the APIs get the same "best frames" our own pipeline trains on — that keeps
the benchmark fair. Run inside the `assetpipe` env (needs cv2/numpy).

Usage:
    python tools/bench_export.py captures/done/CrateScan-BE53A423.zip
    python tools/bench_export.py demo_out/CrateScan-BE53A423/curated_session \
        --photos 80 --fps 12
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from assetpipe.scene.rgbd_curate import analyze_frames, select_keyframes  # noqa: E402
from assetpipe.scene.rgbd_session import load_session  # noqa: E402


def find_session_dir(root: str) -> str:
    """Locate the directory holding manifest.json under ``root``."""
    if os.path.isfile(os.path.join(root, "manifest.json")):
        return root
    for dirpath, _dirnames, filenames in os.walk(root):
        if "manifest.json" in filenames:
            return dirpath
    raise SystemExit(f"No manifest.json found under {root}")


def export(
    src: str,
    out_dir: str,
    *,
    n_photos: int = 60,
    fps: int = 12,
    make_video: bool = True,
) -> dict:
    tmp = None
    if src.lower().endswith(".zip"):
        tmp = tempfile.mkdtemp(prefix="bench_export_")
        with zipfile.ZipFile(src) as zf:
            zf.extractall(tmp)
        session_dir = find_session_dir(tmp)
    else:
        session_dir = find_session_dir(src)

    try:
        session = load_session(session_dir)
        metrics = analyze_frames(session)
        chosen = select_keyframes(session, metrics, target=n_photos)

        os.makedirs(out_dir, exist_ok=True)
        photo_dir = os.path.join(out_dir, "photos")
        os.makedirs(photo_dir, exist_ok=True)
        for old in os.listdir(photo_dir):
            os.remove(os.path.join(photo_dir, old))
        photo_map = []
        for k, idx in enumerate(chosen):
            fr = session.frames[idx]
            ext = os.path.splitext(fr.color_path)[1].lower() or ".jpg"
            dst = os.path.join(photo_dir, f"photo_{k:04d}{ext}")
            shutil.copyfile(fr.color_path, dst)
            photo_map.append({"photo": os.path.basename(dst), "frame_id": fr.frame_id})

        video_path = None
        if make_video:
            video_path = os.path.join(out_dir, "video.mp4")
            if not _write_video(session, video_path, fps=fps):
                video_path = None

        import cv2

        first = cv2.imread(session.frames[0].color_path)
        h, w = first.shape[:2]
        meta = {
            "source": os.path.abspath(src),
            "session_dir": os.path.abspath(session_dir) if tmp is None else None,
            "n_frames_total": session.n_frames,
            "n_photos_exported": len(photo_map),
            "color_resolution": [w, h],
            "object_hint": session.object_hint,
            "location": session.location,
            "pose_convention_in": session.pose_convention_in,
            "video": os.path.basename(video_path) if video_path else None,
            "video_fps": fps if video_path else None,
            "photos": photo_map,
            "note": (
                "Depth (256x192 mm) and poses are NOT uploadable to any of the "
                "benchmark APIs; they see RGB only. Metric truth stays with our "
                "pipeline's dims.json."
            ),
        }
        with open(os.path.join(out_dir, "meta.json"), "w") as fh:
            json.dump(meta, fh, indent=2)
        return meta
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _ffmpeg_exe() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


def _write_video(session, video_path: str, *, fps: int) -> bool:
    """Encode the full color stream with ffmpeg (H.264, yuv420p for uploaders)."""
    exe = _ffmpeg_exe()
    if exe is None:
        print("ffmpeg not found — skipping video export", file=sys.stderr)
        return False
    listfile = video_path + ".frames.txt"
    with open(listfile, "w") as fh:
        for fr in session.frames:
            fh.write(f"file '{os.path.abspath(fr.color_path)}'\n")
            fh.write(f"duration {1.0 / fps:.6f}\n")
    cmd = [
        exe, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", listfile,
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        video_path,
    ]
    subprocess.run(cmd, check=True)
    os.remove(listfile)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src", help="Session zip, session dir, or dir containing one")
    ap.add_argument("--out", help="Output dir (default bench_out/<session-name>)")
    ap.add_argument("--photos", type=int, default=60, help="Photos to export (default 60)")
    ap.add_argument("--fps", type=int, default=12, help="Video fps (default 12)")
    ap.add_argument("--no-video", action="store_true", help="Skip video encode")
    args = ap.parse_args()

    name = os.path.splitext(os.path.basename(args.src.rstrip("/")))[0]
    out_dir = args.out or os.path.join("bench_out", name)
    meta = export(
        args.src,
        out_dir,
        n_photos=args.photos,
        fps=args.fps,
        make_video=not args.no_video,
    )
    print(json.dumps({k: v for k, v in meta.items() if k != "photos"}, indent=2))
    print(f"\nExported {meta['n_photos_exported']} photos"
          + (f" + video.mp4" if meta["video"] else "")
          + f" -> {out_dir}")


if __name__ == "__main__":
    main()
