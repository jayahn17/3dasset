"""Open-source backend adapters: Open3D, COLMAP, TRELLIS, 3DGRUT.

Each reads the same dataset pack and writes ``backends/<name>/``.
Failures are recorded in status.json and do not abort the refine loop.
"""

from __future__ import annotations

import json
import os
import shutil
import traceback
from typing import Any, Callable

from ..preprocess.normalize import load_pack, pack_frame_paths


BACKENDS = ("open3d", "colmap", "trellis", "3dgrut")


def _status_path(backends_root: str) -> str:
    return os.path.join(backends_root, "status.json")


def _write_status(backends_root: str, status: dict) -> None:
    os.makedirs(backends_root, exist_ok=True)
    with open(_status_path(backends_root), "w") as fh:
        json.dump(status, fh, indent=2)


def run_open3d(pack: dict, out_dir: str) -> dict[str, Any]:
    if not pack.get("has_depth") or not pack.get("session_dir"):
        return {"ok": False, "error": "pack has no RGB-D session (skip Open3D TSDF)"}

    from ..scene.io import write_ply
    from ..scene.nvblox_fuse import fuse_open3d_tsdf
    from ..scene.rgbd_session import load_session

    session_dir = os.path.join(pack["_root"], pack["session_dir"])
    session = load_session(session_dir)
    # auto voxel: tighter for close-up
    voxel = 0.004 if session.n_frames <= 80 else 0.01
    xyz, rgb, mesh = fuse_open3d_tsdf(session, voxel_size=voxel)
    os.makedirs(out_dir, exist_ok=True)
    xyz_l = [tuple(map(float, p)) for p in xyz]
    rgb_l = [tuple(map(int, c)) for c in rgb]
    cloud = write_ply(os.path.join(out_dir, "cloud.ply"), xyz_l, rgb_l)
    mesh_path = os.path.join(out_dir, "mesh.ply")
    import open3d as o3d

    o3d.io.write_triangle_mesh(mesh_path, mesh)
    meta = {
        "ok": True,
        "backend": "open3d",
        "cloud": cloud,
        "mesh": mesh_path,
        "n_points": len(xyz_l),
        "voxel_size": voxel,
        "metric": True,
    }
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    return meta


def run_colmap(pack: dict, out_dir: str) -> dict[str, Any]:
    from ..scene.backends import ColmapSceneBackend
    from ..scene.io import write_ply

    frames = pack_frame_paths(pack)
    if len(frames) < 3:
        return {"ok": False, "error": f"need ≥3 frames for COLMAP, got {len(frames)}"}

    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, "_work")
    cloud = ColmapSceneBackend().reconstruct(frames, work)
    ply = write_ply(
        os.path.join(out_dir, "cloud.ply"), cloud.xyz, cloud.rgb
    )
    meta = {
        "ok": True,
        "backend": "colmap",
        "cloud": ply,
        "n_points": len(cloud.xyz),
        "stats": cloud.stats,
        "metric": False,
    }
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    return meta


def run_3dgrut(pack: dict, out_dir: str, *, iterations: int = 5000) -> dict[str, Any]:
    """RGB Gaussian path via existing ThreeDGut backend (best-effort)."""
    from ..scene.backends import ThreeDGutSceneBackend, _find_3dgrut_python
    from ..scene.io import write_ply

    if not _find_3dgrut_python():
        return {"ok": False, "error": "3dgrut conda env / python not found"}

    frames = pack_frame_paths(pack)
    if len(frames) < 8:
        return {"ok": False, "error": f"need ≥8 frames for 3DGRUT, got {len(frames)}"}

    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, "_work")
    try:
        backend = ThreeDGutSceneBackend(iterations=iterations)
        cloud = backend.reconstruct(frames, work)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    ply = write_ply(os.path.join(out_dir, "cloud.ply"), cloud.xyz, cloud.rgb)
    gauss = cloud.gaussian_ply
    if gauss and os.path.isfile(gauss):
        dst = os.path.join(out_dir, "gaussians.ply")
        if os.path.abspath(gauss) != os.path.abspath(dst):
            shutil.copy2(gauss, dst)
        gauss = dst
    meta = {
        "ok": True,
        "backend": "3dgrut",
        "cloud": ply,
        "gaussians": gauss,
        "n_points": len(cloud.xyz),
        "stats": cloud.stats,
        "metric": False,
    }
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    return meta


def run_trellis(
    pack: dict,
    out_dir: str,
    *,
    endpoint: str | None = None,
    views: int = 4,
) -> dict[str, Any]:
    from ..scene.generate import DEFAULT_ENDPOINT, generate_asset

    frames = pack_frame_paths(pack)
    if len(frames) < 1:
        return {"ok": False, "error": "no frames for TRELLIS"}

    os.makedirs(out_dir, exist_ok=True)
    # stage copies as png/jpg for multipart
    view_dir = os.path.join(out_dir, "views")
    os.makedirs(view_dir, exist_ok=True)
    staged = []
    for i, p in enumerate(frames[:24]):
        ext = os.path.splitext(p)[1].lower() or ".jpg"
        dst = os.path.join(view_dir, f"view_{i:02d}{ext}")
        if not os.path.exists(dst):
            try:
                os.symlink(os.path.abspath(p), dst)
            except OSError:
                shutil.copy2(p, dst)
        staged.append(dst)

    glb = os.path.join(out_dir, "asset.glb")
    try:
        res = generate_asset(
            staged,
            glb,
            backend="trellis",
            endpoint=endpoint or DEFAULT_ENDPOINT,
            views=min(views, len(staged)),
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # sample cloud from GLB for refine merge
    cloud_path = None
    try:
        import numpy as np
        import trimesh

        from ..scene.io import write_ply

        m = trimesh.load(glb, force="mesh")
        if isinstance(m, trimesh.Scene):
            m = trimesh.util.concatenate(tuple(m.geometry.values()))
        pts, idx = m.sample(min(40_000, max(2000, len(m.faces) * 3)), return_index=True)
        cols = np.full((len(pts), 3), 180, dtype=np.uint8)
        if hasattr(m.visual, "vertex_colors") and m.visual.vertex_colors is not None:
            vc = np.asarray(m.visual.vertex_colors)[:, :3]
            # nearest vertex color approx via face
            pass
        xyz_l = [tuple(map(float, p)) for p in pts]
        rgb_l = [tuple(map(int, c)) for c in cols]
        cloud_path = write_ply(os.path.join(out_dir, "cloud.ply"), xyz_l, rgb_l)
    except Exception as e:  # noqa: BLE001
        cloud_path = None
        sample_err = str(e)
    else:
        sample_err = None

    n_pts = 0
    if cloud_path and os.path.isfile(cloud_path):
        try:
            from ..scene.io import read_ply

            n_pts = len(read_ply(cloud_path)[0])
        except Exception:  # noqa: BLE001
            n_pts = 0
    meta = {
        "ok": True,
        "backend": "trellis",
        "glb": res["glb"],
        "cloud": cloud_path,
        "n_points": n_pts,
        "views_used": res.get("views_used"),
        "metric": False,
        "sample_error": sample_err,
    }
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    return meta


_RUNNERS: dict[str, Callable[..., dict]] = {
    "open3d": run_open3d,
    "colmap": run_colmap,
    "3dgrut": run_3dgrut,
    "trellis": run_trellis,
}


def run_backends(
    pack_or_run: str,
    backends_root: str | None = None,
    *,
    backends: list[str] | None = None,
    trellis_endpoint: str | None = None,
    skip_3dgrut: bool = False,
) -> dict[str, Any]:
    """Run selected backends; return status dict."""
    pack = load_pack(pack_or_run)
    if backends_root is None:
        # pack_or_run is run root or dataset
        root = pack["_root"]
        run_root = os.path.dirname(root) if os.path.basename(root) == "dataset" else root
        backends_root = os.path.join(run_root, "backends")

    names = list(backends or BACKENDS)
    if skip_3dgrut and "3dgrut" in names:
        names = [n for n in names if n != "3dgrut"]

    status: dict[str, Any] = {"backends": {}, "pack_root": pack["_root"]}
    for name in names:
        if name not in _RUNNERS:
            status["backends"][name] = {"ok": False, "error": f"unknown backend {name}"}
            continue
        out = os.path.join(backends_root, name)
        print(f"→ backend {name} → {out}", flush=True)
        try:
            kwargs = {}
            if name == "trellis" and trellis_endpoint:
                kwargs["endpoint"] = trellis_endpoint
            meta = _RUNNERS[name](pack, out, **kwargs)
        except Exception as e:  # noqa: BLE001
            meta = {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-800:],
            }
        status["backends"][name] = meta
        tag = "✔" if meta.get("ok") else "!!"
        print(f"  {tag} {name}: {meta.get('error') or meta.get('cloud') or meta.get('glb')}", flush=True)

    _write_status(backends_root, status)
    return status
