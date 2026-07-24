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
