"""Offline multi-backend pipeline: dataset pack + refine smoke (no GPU)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image


def _fake_rgbd_session(tmp: Path, n: int = 8) -> Path:
    root = tmp / "session"
    (root / "color").mkdir(parents=True)
    (root / "depth").mkdir()
    frames = []
    rng = np.random.default_rng(1)
    for i in range(n):
        rgb = rng.integers(40, 220, size=(96, 128, 3), dtype=np.uint8)
        rgb[::2, ::2] = np.clip(rgb[::2, ::2].astype(int) + 60, 0, 255)
        Image.fromarray(rgb).save(root / "color" / f"{i:04d}.jpg")
        d = np.full((48, 64), 800, dtype=np.uint16)
        d[16:32, 20:44] = 650
        Image.fromarray(d).save(root / "depth" / f"{i:04d}.png")
        ang = i * 0.4
        c2w = np.eye(4)
        c2w[0, 3] = 0.2 * np.cos(ang)
        c2w[2, 3] = 0.2 * np.sin(ang)
        c2w[1, 3] = 0.4
        # store as arkit_gl-ish identity rotation is fine; convention opencv via source
        frames.append(
            {
                "id": f"f{i:05d}",
                "t": float(i),
                "color": f"color/{i:04d}.jpg",
                "depth": f"depth/{i:04d}.png",
                "pose": c2w.reshape(-1).tolist(),
                "intrinsics": [50.0, 50.0, 32.0, 24.0],
            }
        )
    man = {
        "depth_unit": "mm",
        "pose_convention": "opencv",
        "object_hint": "test",
        "frames": frames,
    }
    (root / "manifest.json").write_text(json.dumps(man))
    return root


def test_build_dataset_pack_session(tmp_path: Path):
    from assetpipe.preprocess import build_dataset_pack, load_pack, pack_frame_paths

    src = _fake_rgbd_session(tmp_path)
    out = tmp_path / "run"
    man = build_dataset_pack(str(src), str(out), target_frames=6)
    assert man["has_depth"] and man["has_poses"]
    assert man["n_frames"] <= 6
    pack = load_pack(str(out))
    paths = pack_frame_paths(pack)
    assert len(paths) == man["n_frames"]
    assert (out / "dataset" / "manifest.json").is_file()


def test_detect_holes_and_refine_merge(tmp_path: Path):
    from assetpipe.offline.refine import detect_holes, merge_clouds
    from assetpipe.scene.io import write_ply

    rng = np.random.default_rng(0)
    xyz = rng.normal(size=(500, 3)) * 0.05
    rgb = np.full((500, 3), 120, dtype=np.uint8)
    h = detect_holes(xyz, voxel=0.02)
    assert h["ok"]
    assert 0.0 <= h["coverage"] <= 1.0

    p = tmp_path / "c.ply"
    write_ply(
        str(p),
        [tuple(map(float, q)) for q in xyz],
        [tuple(map(int, c)) for c in rgb],
    )
    clouds = [
        ("open3d", xyz, rgb, {"metric": True}),
        ("colmap", xyz * 2.0 + 1.0, rgb, {"metric": False}),
    ]
    mxyz, mrgb, meta = merge_clouds(clouds, voxel=0.01)
    assert len(mxyz) > 50
    assert "open3d" in meta["sources"]
