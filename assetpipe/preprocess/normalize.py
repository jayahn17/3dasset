"""Normalize any capture into a curated dataset pack for offline backends.

iPad / CrateScan is data-only here: backends never see ARKit — only the pack.
"""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any, Optional


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".mkv"}


def find_session_dir(root: str) -> Optional[str]:
    """Locate directory holding manifest.json under ``root``."""
    if os.path.isfile(os.path.join(root, "manifest.json")):
        return os.path.abspath(root)
    for dirpath, _dirs, files in os.walk(root):
        if "manifest.json" in files:
            return os.path.abspath(dirpath)
    return None


def _detect_kind(src: str) -> str:
    src = os.path.abspath(src)
    if src.lower().endswith(".zip") and os.path.isfile(src):
        return "zip"
    if os.path.isfile(src) and Path(src).suffix.lower() in VIDEO_EXTS:
        return "video"
    if os.path.isdir(src):
        if find_session_dir(src):
            return "session"
        return "images"
    raise FileNotFoundError(f"unsupported capture input: {src}")


def _link_or_copy(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    if os.path.lexists(dst):
        os.remove(dst)
    try:
        os.symlink(os.path.abspath(src), dst)
    except OSError:
        shutil.copy2(src, dst)


def _pack_from_session(
    session_dir: str,
    out_dir: str,
    *,
    target_frames: int,
    source_label: str,
    copy_files: bool = True,
) -> dict[str, Any]:
    from ..scene.rgbd_curate import (
        _apply_rejects,
        analyze_frames,
        select_keyframes,
        write_curated_session,
    )
    from ..scene.rgbd_session import load_session

    session = load_session(session_dir)
    metrics = analyze_frames(session)
    _apply_rejects(metrics)
    indices = select_keyframes(session, metrics, target=target_frames)

    curated = os.path.join(out_dir, "session")
    # Always copy into the pack — zip extracts and /tmp sources vanish otherwise.
    write_curated_session(
        session,
        indices,
        curated,
        metrics=metrics,
        copy_files=copy_files,
    )

    frames_dir = os.path.join(out_dir, "frames")
    depth_dir = os.path.join(out_dir, "depth")
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(depth_dir, exist_ok=True)

    poses: list[dict] = []
    curated_sess = load_session(curated)
    for i, fr in enumerate(curated_sess.frames):
        c_name = f"{i:04d}.jpg"
        d_name = f"{i:04d}.png"
        # Prefer hard copies in frames/ for COLMAP (no symlink chasing)
        shutil.copy2(fr.color_path, os.path.join(frames_dir, c_name))
        shutil.copy2(fr.depth_path, os.path.join(depth_dir, d_name))
        poses.append(
            {
                "id": fr.frame_id,
                "color": f"frames/{c_name}",
                "depth": f"depth/{d_name}",
                "pose_c2w_opencv": [float(x) for x in fr.pose],
                "intrinsics": [float(x) for x in fr.intrinsics],
                "t": float(fr.timestamp),
            }
        )

    with open(os.path.join(out_dir, "poses.json"), "w") as fh:
        json.dump({"pose_convention": "opencv", "frames": poses}, fh, indent=2)

    # Curation HTML already in session/; mirror under report/
    report = os.path.join(out_dir, "report")
    os.makedirs(report, exist_ok=True)
    src_html = os.path.join(curated, "CURATION.html")
    if os.path.isfile(src_html):
        shutil.copy2(src_html, os.path.join(report, "CURATION.html"))

    return {
        "kind": "rgbd_session",
        "source": source_label,
        "has_depth": True,
        "has_poses": True,
        "n_frames": len(poses),
        "pose_convention": "opencv",
        "session_dir": "session",
        "depth_unit": curated_sess.depth_unit,
        "object_hint": curated_sess.object_hint,
        "location": curated_sess.location,
    }


def _sharpness_path(path: str) -> float:
    import numpy as np
    from PIL import Image

    g = np.asarray(
        Image.open(path).convert("L").resize((160, 120)), dtype=np.float32
    )
    lap = (
        -4 * g[1:-1, 1:-1]
        + g[:-2, 1:-1]
        + g[2:, 1:-1]
        + g[1:-1, :-2]
        + g[1:-1, 2:]
    )
    return float(lap.var())


def _pack_from_images(
    image_dir: str,
    out_dir: str,
    *,
    target_frames: int,
    source_label: str,
) -> dict[str, Any]:
    paths = sorted(
        os.path.join(image_dir, f)
        for f in os.listdir(image_dir)
        if Path(f).suffix.lower() in IMAGE_EXTS
    )
    if not paths:
        raise FileNotFoundError(f"no images in {image_dir}")

    scored = [(p, _sharpness_path(p)) for p in paths]
    scored.sort(key=lambda x: -x[1])
    # keep top by sharpness, then temporal spread among survivors
    keep_n = min(target_frames, len(scored))
    # take best 2x then evenly sample for coverage
    pool = [p for p, _ in scored[: max(keep_n * 2, keep_n)]]
    pool.sort()  # temporal
    if len(pool) > keep_n:
        sel = [
            pool[int(i * (len(pool) - 1) / max(keep_n - 1, 1))]
            for i in range(keep_n)
        ]
        # unique preserve order
        seen = set()
        chosen = []
        for p in sel:
            if p not in seen:
                seen.add(p)
                chosen.append(p)
        while len(chosen) < keep_n:
            for p, _ in scored:
                if p not in seen:
                    chosen.append(p)
                    seen.add(p)
                if len(chosen) >= keep_n:
                    break
    else:
        chosen = pool

    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    poses: list[dict] = []
    for i, p in enumerate(chosen):
        ext = Path(p).suffix.lower() or ".jpg"
        name = f"{i:04d}{ext}"
        _link_or_copy(p, os.path.join(frames_dir, name))
        poses.append({"id": f"f{i:05d}", "color": f"frames/{name}"})

    with open(os.path.join(out_dir, "poses.json"), "w") as fh:
        json.dump({"pose_convention": None, "frames": poses}, fh, indent=2)

    report = os.path.join(out_dir, "report")
    os.makedirs(report, exist_ok=True)
    (Path(report) / "CURATION.txt").write_text(
        f"RGB-only pack: kept {len(chosen)}/{len(paths)} sharp frames\n"
    )

    return {
        "kind": "rgb_images",
        "source": source_label,
        "has_depth": False,
        "has_poses": False,
        "n_frames": len(chosen),
        "pose_convention": None,
        "session_dir": None,
    }


def _pack_from_video(
    video_path: str,
    out_dir: str,
    *,
    target_frames: int,
    fps: float,
    source_label: str,
) -> dict[str, Any]:
    from ..capture.video import VideoSource

    work = os.path.join(out_dir, "_video_extract")
    vs = VideoSource(
        video_path,
        work,
        fps=fps,
        max_frames=max(target_frames * 3, 90),
        max_width=1280,
    )
    extracted = vs.extract()
    tmp_img = os.path.join(work, "flat")
    os.makedirs(tmp_img, exist_ok=True)
    for i, p in enumerate(extracted):
        dst = os.path.join(tmp_img, f"{i:04d}.jpg")
        if not os.path.exists(dst):
            _link_or_copy(p, dst)
    meta = _pack_from_images(
        tmp_img, out_dir, target_frames=target_frames, source_label=source_label
    )
    meta["kind"] = "rgb_video"
    meta["video"] = os.path.abspath(video_path)
    meta["extract_fps"] = fps
    return meta


def build_dataset_pack(
    src: str,
    out_dir: str,
    *,
    target_frames: int = 48,
    video_fps: float = 2.5,
) -> dict[str, Any]:
    """Build ``out_dir/dataset/`` pack. Returns pack manifest dict."""
    src = os.path.abspath(src)
    out_dir = os.path.abspath(out_dir)
    dataset = os.path.join(out_dir, "dataset")
    if os.path.isdir(dataset):
        shutil.rmtree(dataset)
    os.makedirs(dataset, exist_ok=True)

    kind = _detect_kind(src)
    if kind == "zip":
        # Keep extract under the run dir (not /tmp) so any residual links resolve.
        ingest = os.path.join(out_dir, "_ingest")
        if os.path.isdir(ingest):
            shutil.rmtree(ingest)
        os.makedirs(ingest, exist_ok=True)
        with zipfile.ZipFile(src) as zf:
            zf.extractall(ingest)
        session = find_session_dir(ingest)
        if session:
            core = _pack_from_session(
                session,
                dataset,
                target_frames=target_frames,
                source_label=src,
                copy_files=True,
            )
        else:
            core = _pack_from_images(
                ingest,
                dataset,
                target_frames=target_frames,
                source_label=src,
            )
    elif kind == "session":
        session = find_session_dir(src)
        assert session
        core = _pack_from_session(
            session,
            dataset,
            target_frames=target_frames,
            source_label=src,
            copy_files=True,
        )
    elif kind == "video":
        core = _pack_from_video(
            src,
            dataset,
            target_frames=target_frames,
            fps=video_fps,
            source_label=src,
        )
    else:
        core = _pack_from_images(
            src,
            dataset,
            target_frames=target_frames,
            source_label=src,
        )

    manifest = {
        "format": "assetpipe.dataset_pack/v1",
        "dataset_dir": dataset,
        **core,
        "frames_dir": "frames",
        "depth_dir": "depth" if core.get("has_depth") else None,
        "poses_file": "poses.json",
        "params": {"target_frames": target_frames, "video_fps": video_fps},
    }
    with open(os.path.join(dataset, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    with open(os.path.join(out_dir, "run.json"), "w") as fh:
        json.dump(
            {
                "dataset": "dataset",
                "source": src,
                "manifest": "dataset/manifest.json",
            },
            fh,
            indent=2,
        )
    return manifest


def load_pack(dataset_or_run: str) -> dict[str, Any]:
    """Load pack manifest from dataset/ or run root."""
    root = os.path.abspath(dataset_or_run)
    for cand in (
        os.path.join(root, "manifest.json"),
        os.path.join(root, "dataset", "manifest.json"),
    ):
        if os.path.isfile(cand):
            with open(cand) as fh:
                man = json.load(fh)
            man["_root"] = os.path.dirname(cand)
            return man
    raise FileNotFoundError(f"no dataset pack manifest under {root}")


def pack_frame_paths(pack: dict) -> list[str]:
    root = pack["_root"]
    poses_path = os.path.join(root, pack.get("poses_file") or "poses.json")
    with open(poses_path) as fh:
        poses = json.load(fh)
    return [
        os.path.join(root, fr["color"])
        for fr in poses.get("frames") or []
        if fr.get("color")
    ]
