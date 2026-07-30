"""Tests for RGB-D session curation (preprocess before reconstruction)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from PIL import Image


def _fake_session(tmp: Path, n: int = 12) -> Path:
    root = tmp / "session"
    (root / "color").mkdir(parents=True)
    (root / "depth").mkdir()
    frames = []
    rng = np.random.default_rng(0)
    for i in range(n):
        # alternate sharp vs blur
        sharp = i % 3 != 0
        base = rng.integers(40, 200, size=(96, 128, 3), dtype=np.uint8)
        if sharp:
            # add high-frequency pattern
            base[::2, ::2] = np.clip(base[::2, ::2].astype(int) + 80, 0, 255)
        else:
            base = np.asarray(
                Image.fromarray(base).resize((32, 24)).resize((128, 96)),
                dtype=np.uint8,
            )
        Image.fromarray(base).save(root / "color" / f"{i:04d}.jpg")
        # depth: object slab in center at ~0.7m + floor gradient
        d = np.full((48, 64), 900, dtype=np.uint16)  # mm
        d[18:30, 22:42] = 700
        if i == 1:
            d[:] = 0  # bad depth frame
        Image.fromarray(d).save(root / "depth" / f"{i:04d}.png")
        # orbit-ish poses
        ang = i * 0.35
        c2w = np.eye(4)
        c2w[0, 3] = 0.3 * np.cos(ang)
        c2w[2, 3] = 0.3 * np.sin(ang)
        c2w[1, 3] = 0.5
        frames.append(
            {
                "id": f"f{i:05d}",
                "t": float(i),
                "color": f"color/{i:04d}.jpg",
                "depth": f"depth/{i:04d}.png",
                "pose": c2w.reshape(-1).tolist(),
                "intrinsics": [50.0, 50.0, 32.0, 24.0],
                "K_color": [100.0, 100.0, 64.0, 48.0],
                "color_size": [128, 96],
            }
        )
    man = {"depth_unit": "mm", "object_hint": "test", "frames": frames}
    (root / "manifest.json").write_text(json.dumps(man))
    return root


def test_curate_rejects_and_writes(tmp_path: Path):
    from assetpipe.scene.rgbd_curate import curate_session
    from assetpipe.scene.rgbd_session import load_session

    src = _fake_session(tmp_path)
    out = tmp_path / "curated"
    report = curate_session(str(src), str(out), target=6, sharp_pct=20, min_edge=0.0)
    assert report["n_source"] == 12
    assert 1 <= report["n_kept"] <= 6
    assert (out / "manifest.json").is_file()
    assert (out / "CURATION.html").is_file()
    s = load_session(str(out))
    assert s.n_frames == report["n_kept"]
    # bad-depth frame index 1 should not dominate kept set
    frames = json.loads((out / "manifest.json").read_text())["frames"]
    src_idxs = [f.get("src_index") for f in frames]
    assert 1 not in src_idxs or report["n_kept"] == 1
    assert all(f["K_color"] == [100.0, 100.0, 64.0, 48.0] for f in frames)
    assert all(f["color_size"] == [128, 96] for f in frames)
    assert s.frames[0].color_intrinsics == (100.0, 100.0, 64.0, 48.0)
    assert s.frames[0].color_size == (128, 96)


def test_loads_high_resolution_keyframe_only_session(tmp_path: Path):
    from assetpipe.scene.rgbd_session import inspect_path, load_session

    src = _fake_session(tmp_path, n=4)
    manifest_path = src / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    keyframes = manifest.pop("frames")
    for frame in keyframes:
        frame.pop("intrinsics")
        frame["depth_size"] = [64, 48]
    manifest["frames"] = []
    manifest["keyframes"] = keyframes
    manifest_path.write_text(json.dumps(manifest))

    report = inspect_path(str(src))
    assert report["ok_for_nvblox"]
    session = load_session(str(src))
    assert session.n_frames == 4
    assert session.frames[0].intrinsics == (50.0, 50.0, 32.0, 24.0)
    assert session.frames[0].color_intrinsics == (100.0, 100.0, 64.0, 48.0)
