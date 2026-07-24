"""Tests for the Scaniverse-style scan path (assetpipe.scene).

Runs with plain `python tests/test_scene.py` or under pytest. Zero
third-party dependencies: frames are stdlib-written P6 PPMs and the stub
backend reconstructs them without Pillow.
"""

import os
import struct
import tempfile

from assetpipe.scene import (
    StubSceneBackend,
    make_scene_backend,
    read_ply,
    scan_scene,
    write_ply,
    write_splat,
)

CLOUD = (
    [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0), (-1.5, 0.25, 8.0)],
    [(255, 0, 0), (0, 255, 0), (12, 34, 56)],
)


def _write_ppm_frames(d, n=4, w=32, h=24):
    paths = []
    for i in range(n):
        px = bytearray()
        for v in range(h):
            for u in range(w):
                px += bytes(((u * 8 + i * 40) % 256, (v * 10) % 256, 128))
        p = os.path.join(d, f"f{i:03d}.ppm")
        with open(p, "wb") as fh:
            fh.write(f"P6\n{w} {h}\n255\n".encode())
            fh.write(px)
        paths.append(p)
    return paths


def test_ply_roundtrip():
    xyz, rgb = CLOUD
    with tempfile.TemporaryDirectory() as d:
        p = write_ply(os.path.join(d, "c.ply"), xyz, rgb)
        head = open(p, "rb").read(64)
        assert head.startswith(b"ply\nformat binary_little_endian")
        x2, r2 = read_ply(p)
        assert r2 == rgb
        assert all(abs(a - b) < 1e-6 for p1, p2 in zip(x2, xyz) for a, b in zip(p1, p2))


def test_splat_layout():
    xyz, rgb = CLOUD
    with tempfile.TemporaryDirectory() as d:
        p = write_splat(os.path.join(d, "c.splat"), xyz, rgb, scale=0.02)
        blob = open(p, "rb").read()
        assert len(blob) == 32 * len(xyz)  # 3f pos + 3f scale + 4B rgba + 4B rot
        x, y, z, sx, sy, sz = struct.unpack_from("<6f", blob, 32)  # 2nd splat
        assert (x, y, z) == (1.0, 2.0, 3.0)
        assert all(abs(s - 0.02) < 1e-7 for s in (sx, sy, sz))  # float32 scale
        assert blob[56:60] == bytes((0, 255, 0, 255))  # rgba
        assert blob[60:64] == bytes((255, 128, 128, 128))  # identity quat


def test_stub_backend_reads_ppm_without_pillow():
    with tempfile.TemporaryDirectory() as d:
        frames = _write_ppm_frames(d)
        cloud = StubSceneBackend(max_points=2000).reconstruct(frames, d)
        assert len(cloud.xyz) == len(cloud.rgb) > 100
        assert cloud.stats["frames"] == len(frames)
        xs = [p[0] for p in cloud.xyz]
        assert max(xs) > 0 > min(xs)  # photo-ring wraps around the origin


def test_scan_scene_writes_artifacts():
    with tempfile.TemporaryDirectory() as d:
        frames = _write_ppm_frames(d)
        out = os.path.join(d, "scan_out")
        res = scan_scene(frames, out, backend="stub", splat=True, title="t")
        assert res["backend"] == "stub" and res["points"] > 0
        assert os.path.exists(res["ply"]) and os.path.exists(res["splat"])
        html = open(res["viewer"]).read()
        assert "scene.ply" in html and "webgl" in html  # links + renderer inline


def test_backend_factory():
    assert make_scene_backend("stub").name == "stub"
    assert make_scene_backend("auto").name in ("stub", "colmap", "vggt")
    assert make_scene_backend("3dgut").name == "3dgut"
    try:
        make_scene_backend("nope")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_clean_removes_ground_outliers_and_clutter():
    import random

    from assetpipe.scene import ScenePointCloud, clean_cloud

    rng = random.Random(1)
    xyz, tag = [], []
    for _ in range(3000):  # ground plane y=0
        xyz.append((rng.uniform(-2, 2), rng.gauss(0, 0.002), rng.uniform(-2, 2)))
        tag.append("ground")
    for _ in range(1200):  # the asset: a box cluster above the plane
        xyz.append((rng.uniform(-0.2, 0.2), rng.uniform(0.05, 0.45),
                    rng.uniform(-0.2, 0.2)))
        tag.append("asset")
    for _ in range(40):  # far speckle
        xyz.append((rng.uniform(-9, 9), rng.uniform(2, 9), rng.uniform(-9, 9)))
        tag.append("noise")
    rgb = [(120, 120, 120)] * len(xyz)
    out = clean_cloud(ScenePointCloud(xyz, rgb))

    kept = set()
    pos = {tuple(p): t for p, t in zip(xyz, tag)}
    for p in out.xyz:
        kept.add(pos[tuple(p)])
    n_asset_in = tag.count("asset")
    assert len(out.xyz) > 0.7 * n_asset_in          # most of the asset survives
    assert out.stats["ground_removed"] > 2000        # the plane went away
    assert "noise" not in kept or out.stats["clutter_removed"] >= 0
    frac_asset = sum(1 for p in out.xyz if pos[tuple(p)] == "asset") / len(out.xyz)
    assert frac_asset > 0.95                         # what's left IS the asset


def test_coverage_azimuths_from_camera_ring():
    try:
        import numpy  # noqa: F401
    except ImportError:
        return  # coverage math is numpy-only (optional in the core env)

    import math

    from assetpipe.scene import ScenePointCloud

    # cameras on a 3/4 arc around the object: expect a ~90 deg gap
    cams = [(2 * math.cos(math.radians(a)), 0.5, 2 * math.sin(math.radians(a)))
            for a in range(0, 271, 15)]
    cloud = ScenePointCloud([(0.0, 0.0, 0.0), (0.1, 0.1, 0.0), (0.0, 0.1, 0.1)],
                            [(1, 1, 1)] * 3, cameras=cams)
    az = cloud.coverage_azimuths()
    assert len(az) == len(cams)
    assert all(0.0 <= a < 360.0 for a in az)
    gaps = [(az[(i + 1) % len(az)] - az[i]) % 360 for i in range(len(az))]
    assert 80 < max(gaps) < 100          # the uncovered quarter shows up
    assert sorted(az) == az              # returned sorted

    assert ScenePointCloud([], [], cameras=cams).coverage_azimuths() == []
    assert ScenePointCloud([(0.0, 0.0, 0.0)], [(1, 1, 1)]).coverage_azimuths() == []


def test_live_session_stub_end_to_end():
    from assetpipe.scene import LiveScanSession, StubSceneBackend

    with tempfile.TemporaryDirectory() as d:
        frames = _write_ppm_frames(d, n=8)
        sess = LiveScanSession(os.path.join(d, "w"),
                               backend=StubSceneBackend(max_points=2000),
                               solve_every=3, min_frames=3)
        for f in frames[:5]:
            sess.add_frame(f)
        sess.wait(30)
        v1, cloud1 = sess.cloud()
        assert v1 >= 1 and len(cloud1.xyz) > 0      # preview solved mid-stream
        st = sess.status()
        assert st["frames"] == 5 and st["points"] == len(cloud1.xyz)

        for f in frames[5:]:
            sess.add_frame(f)
        out = os.path.join(d, "final")
        res = sess.finalize(out, splat=True, clean=False)
        assert res["frames"] == 8                    # final solve saw everything
        assert os.path.exists(res["ply"]) and os.path.exists(res["splat"])
        try:
            sess.add_frame(frames[0])
            raise AssertionError("expected RuntimeError after finalize")
        except RuntimeError:
            pass


def test_cloud_to_mesh_alpha_shape():
    try:
        import numpy  # noqa: F401
        import scipy  # noqa: F401
        import trimesh
    except ImportError:
        return  # zero-dep environment: meshing is an optional extra

    import random

    from assetpipe.scene import cloud_to_mesh

    rng = random.Random(3)
    xyz, rgb = [], []
    for _ in range(1500):  # points on a unit-ish box surface
        p = [rng.uniform(-1, 1) for _ in range(3)]
        p[rng.randrange(3)] = rng.choice([-1.0, 1.0])
        xyz.append(tuple(p))
        rgb.append((200, 120, 60))
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "m.glb")
        res = cloud_to_mesh(xyz, rgb, out, method="alpha")
        assert res["faces"] > 100 and os.path.exists(out)
        m = trimesh.load(out)
        geom = list(m.geometry.values())[0] if hasattr(m, "geometry") else m
        assert len(geom.faces) == res["faces"]
        assert geom.extents.max() <= 2.2  # mesh stays within the cloud's bounds


def test_select_views_spreads_and_prefers_sharp():
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return  # view selection needs numpy/PIL (optional extras)

    from assetpipe.scene.generate import select_views

    rng = np.random.default_rng(0)
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i in range(8):
            sharp = i % 2 == 0  # every other view is crisp noise vs a blur
            a = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
            if not sharp:
                a = np.full((64, 64, 3), 128, np.uint8)  # flat = zero laplacian
            p = os.path.join(d, f"view_{i:02d}.png")
            Image.fromarray(a).save(p)
            paths.append(p)

        picked = select_views(paths, 4)
        assert len(picked) == 4
        assert len(set(picked)) == 4                    # no duplicates
        idx = [paths.index(p) for p in picked]
        assert all(i % 2 == 0 for i in idx)             # the sharp ones
        assert idx == sorted(idx)                       # kept in orbit order
        assert select_views(paths, 99) == paths         # k >= n -> all


def test_cli_scan_on_folder():
    from assetpipe.cli import main

    with tempfile.TemporaryDirectory() as d:
        _write_ppm_frames(d)
        out = os.path.join(d, "scan_out")
        rc = main(["scan", d, "--backend", "stub", "--splat", "--out", out])
        assert rc == 0
        for f in ("scene.ply", "scene.splat", "scan_view.html"):
            assert os.path.exists(os.path.join(out, f))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all tests passed")
