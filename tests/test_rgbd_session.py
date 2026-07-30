"""RGB-D session gate + inspect (no GPU / nvblox required)."""

from __future__ import annotations

import json
import os
import tempfile

from assetpipe.scene.rgbd_session import (
    RgbdSessionError,
    inspect_path,
    load_session,
    write_example_manifest,
)


def test_rgb_folder_not_ready():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "a.jpg"), "wb").write(b"x")
        r = inspect_path(d)
        assert r["kind"] == "rgb_folder"
        assert r["ok_for_nvblox"] is False
        assert "depth" in " ".join(r["missing"]).lower()


def test_session_ready_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        color = os.path.join(d, "color")
        depth = os.path.join(d, "depth")
        os.makedirs(color)
        os.makedirs(depth)
        open(os.path.join(color, "0000.jpg"), "wb").write(b"jpg")
        open(os.path.join(depth, "0000.png"), "wb").write(b"png")
        man = {
            "depth_unit": "mm",
            "frames": [
                {
                    "id": "f0",
                    "t": 0.0,
                    "color": "color/0000.jpg",
                    "depth": "depth/0000.png",
                    "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                    "intrinsics": [500.0, 500.0, 320.0, 240.0],
                },
                {
                    "id": "f1",
                    "t": 0.1,
                    "color": "color/0000.jpg",
                    "depth": "depth/0000.png",
                    "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.1, 0, 0, 1],
                    "intrinsics": [500.0, 500.0, 320.0, 240.0],
                },
                {
                    "id": "f2",
                    "t": 0.2,
                    "color": "color/0000.jpg",
                    "depth": "depth/0000.png",
                    "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.2, 0, 0, 1],
                    "intrinsics": [500.0, 500.0, 320.0, 240.0],
                },
            ],
        }
        with open(os.path.join(d, "manifest.json"), "w") as fh:
            json.dump(man, fh)
        r = inspect_path(d)
        assert r["ok_for_nvblox"] is True
        sess = load_session(d)
        assert sess.n_frames == 3


def test_session_missing_depth_raises():
    with tempfile.TemporaryDirectory() as d:
        write_example_manifest(os.path.join(d, "manifest.json"))
        # example points at missing color/depth files
        try:
            load_session(d)
            assert False, "expected RgbdSessionError"
        except RgbdSessionError:
            pass


if __name__ == "__main__":
    test_rgb_folder_not_ready()
    test_session_ready_roundtrip()
    test_session_missing_depth_raises()
    print("ok")


def _mini_session(d, extra_manifest=None, pose=None):
    """3-frame session with tiny real images on disk."""
    import numpy as np
    from PIL import Image

    os.makedirs(os.path.join(d, "color"), exist_ok=True)
    os.makedirs(os.path.join(d, "depth"), exist_ok=True)
    p = pose or [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    frames = []
    for i in range(3):
        Image.new("RGB", (8, 6)).save(os.path.join(d, f"color/{i:04d}.jpg"))
        Image.fromarray(
            (np.full((6, 8), 500, dtype=np.uint16))
        ).save(os.path.join(d, f"depth/{i:04d}.png"))
        frames.append({
            "id": f"f{i:05d}", "t": float(i),
            "color": f"color/{i:04d}.jpg", "depth": f"depth/{i:04d}.png",
            "pose": p, "intrinsics": [500.0, 500.0, 4.0, 3.0],
        })
    man = {"depth_unit": "mm", "frames": frames}
    man.update(extra_manifest or {})
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        json.dump(man, fh)


def test_pose_convention_normalization():
    """cratescanner (ARKit GL) poses get column-1/2 sign flips; opencv untouched."""
    # A GL pose with a distinctive rotation + translation
    gl = [0, -1, 0, 0.5,
          1, 0, 0, 0.25,
          0, 0, 1, 1.0,
          0, 0, 0, 1]
    expect_cv = [0, 1, 0, 0.5,
                 1, 0, 0, 0.25,
                 0, 0, -1, 1.0,
                 0, 0, 0, 1]

    with tempfile.TemporaryDirectory() as d:
        _mini_session(d, {"source": "cratescanner"}, pose=gl)
        sess = load_session(d)
        assert sess.pose_convention_in == "arkit_gl"
        assert sess.frames[0].pose == [float(x) for x in expect_cv]

    # explicit field wins over source fallback
    with tempfile.TemporaryDirectory() as d:
        _mini_session(d, {"source": "cratescanner",
                          "pose_convention": "opencv"}, pose=gl)
        sess = load_session(d)
        assert sess.pose_convention_in == "opencv"
        assert sess.frames[0].pose == [float(x) for x in gl]

    # open3d_sample / unlabeled sessions stay untouched
    with tempfile.TemporaryDirectory() as d:
        _mini_session(d, {"source": "open3d_sample"}, pose=gl)
        sess = load_session(d)
        assert sess.pose_convention_in == "opencv"
        assert sess.frames[0].pose == [float(x) for x in gl]
