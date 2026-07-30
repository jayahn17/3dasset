"""Fuse an RGB-D+pose session into a metric mesh (nvblox or Open3D TSDF).

nvblox is preferred when ``nvblox_torch`` is installed; otherwise we fall back
to Open3D's ScalableTSDFVolume — same *method class* (depth fusion), slower /
CPU-heavier, still far better than RGB-only COLMAP for LiDAR captures.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import numpy as np

from .rgbd_session import RgbdFrame, RgbdSession, RgbdSessionError, load_session
from . import write_artifacts
from .backends import ScenePointCloud


def _read_depth_m(frame: RgbdFrame) -> np.ndarray:
    """Load depth image as float32 meters, shape (H, W)."""
    path = frame.depth_path
    ext = os.path.splitext(path)[1].lower()
    if ext in {".exr", ".tif", ".tiff"}:
        import cv2

        d = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if d is None:
            raise RgbdSessionError(f"failed to read depth {path}")
        d = d.astype(np.float32)
        if frame.depth_unit == "mm":
            d *= 0.001
        return d

    # PNG uint16 mm (Quest / Record3D common) or 8-bit (reject).
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover
        raise RgbdSessionError("Pillow required to read depth PNGs") from e

    img = Image.open(path)
    d = np.array(img)
    if d.ndim == 3:
        d = d[..., 0]
    if d.dtype == np.uint16 or frame.depth_unit == "mm":
        return d.astype(np.float32) * (0.001 if frame.depth_unit != "m" else 1.0)
    if d.dtype == np.float32 or d.dtype == np.float64:
        return d.astype(np.float32)
    raise RgbdSessionError(
        f"Unsupported depth dtype {d.dtype} in {path}; export uint16 mm PNG."
    )


def _read_color_rgb(path: str) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGB"))


def _pose_matrix(pose: list[float]) -> np.ndarray:
    m = np.asarray(pose, dtype=np.float64).reshape(4, 4)
    return m


def backend_available() -> dict:
    """Which fusion backends can run on this machine."""
    info = {
        "nvblox_torch": False,
        "open3d_tsdf": False,
        "preferred": None,
        "nvblox_error": None,
    }
    try:
        from nvblox_torch.mapper import Mapper  # noqa: F401
        from nvblox_torch.sensor import Sensor  # noqa: F401

        info["nvblox_torch"] = True
    except Exception as e:  # noqa: BLE001 — report why wheel failed
        info["nvblox_error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        import open3d as o3d  # noqa: F401

        info["open3d_tsdf"] = True
    except Exception:
        pass
    if info["nvblox_torch"]:
        info["preferred"] = "nvblox"
    elif info["open3d_tsdf"]:
        info["preferred"] = "open3d"
    return info


def fuse_open3d_tsdf(
    session: RgbdSession,
    voxel_size: float = 0.01,
    sdf_trunc: float = 0.04,
    depth_max: float = 4.0,
) -> tuple[np.ndarray, np.ndarray, object]:
    """Integrate RGB-D frames with Open3D; return xyz, rgb, triangle mesh."""
    import open3d as o3d

    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=voxel_size,
        sdf_trunc=sdf_trunc,
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
    )
    for fr in session.frames:
        depth_m = _read_depth_m(fr)
        color = _read_color_rgb(fr.color_path)
        if color.shape[:2] != depth_m.shape[:2]:
            # Resize color to depth resolution (common for LiDAR apps).
            from PIL import Image

            color = np.asarray(
                Image.fromarray(color).resize(
                    (depth_m.shape[1], depth_m.shape[0]), Image.BILINEAR
                )
            )
        fx, fy, cx, cy = fr.intrinsics
        intrinsic = o3d.camera.PinholeCameraIntrinsic(
            depth_m.shape[1], depth_m.shape[0], fx, fy, cx, cy
        )
        depth_o3d = o3d.geometry.Image((depth_m * 1000.0).astype(np.uint16))
        color_o3d = o3d.geometry.Image(np.ascontiguousarray(color))
        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
            color_o3d,
            depth_o3d,
            depth_scale=1000.0,
            depth_trunc=depth_max,
            convert_rgb_to_intensity=False,
        )
        # Open3D integrate expects extrinsic = world-to-camera.
        c2w = _pose_matrix(fr.pose)
        w2c = np.linalg.inv(c2w)
        volume.integrate(rgbd, intrinsic, w2c)

    mesh = volume.extract_triangle_mesh()
    mesh.compute_vertex_normals()
    pcd = volume.extract_point_cloud()
    xyz = np.asarray(pcd.points)
    rgb = (
        (np.asarray(pcd.colors) * 255).astype(np.uint8)
        if pcd.has_colors()
        else np.full((len(xyz), 3), 180, dtype=np.uint8)
    )
    return xyz, rgb, mesh


def fuse_nvblox(
    session: RgbdSession,
    voxel_size: float = 0.01,
    depth_max: float = 4.0,
) -> tuple[np.ndarray, np.ndarray, Optional[object]]:
    """Integrate with nvblox_torch Mapper (add_depth_frame / add_color_frame)."""
    try:
        import torch
        from nvblox_torch.mapper import Mapper
        from nvblox_torch.sensor import Sensor
    except Exception as e:  # noqa: BLE001
        raise RgbdSessionError(
            f"nvblox_torch not usable ({type(e).__name__}: {e}). "
            "Torch ABI / CUDA libs often mismatch the pip wheel — "
            "use --backend open3d, or see docs/NVBLOX_WORKFLOW.md § install."
        ) from e

    mapper = Mapper(voxel_sizes_m=float(voxel_size))
    # Poses are camera-to-world (t_w_c); tensors on CPU per nvblox docs.
    for fr in session.frames:
        depth_m = _read_depth_m(fr)
        color = _read_color_rgb(fr.color_path)
        h, w = depth_m.shape[:2]
        if color.shape[:2] != (h, w):
            from PIL import Image

            color = np.asarray(
                Image.fromarray(color).resize((w, h), Image.BILINEAR)
            )
        # Truncate far depth
        depth_m = depth_m.copy()
        depth_m[depth_m > depth_max] = 0.0

        fx, fy, cx, cy = fr.intrinsics
        sensor = Sensor.from_camera(fu=fx, fv=fy, cu=cx, cv=cy, width=w, height=h)
        depth_t = torch.from_numpy(depth_m.astype(np.float32))
        color_t = torch.from_numpy(np.ascontiguousarray(color, dtype=np.uint8))
        pose_t = torch.from_numpy(_pose_matrix(fr.pose).astype(np.float32))

        mapper.add_depth_frame(depth_t, pose_t, sensor)
        mapper.add_color_frame(color_t, pose_t, sensor)

    mapper.update_color_mesh()
    mesh = mapper.get_color_mesh()
    xyz = mesh.vertices.detach().cpu().numpy()
    faces = mesh.triangles.detach().cpu().numpy()
    cols = mesh.vertex_colors.detach().cpu().numpy()
    if cols.max() <= 1.0:
        rgb = (cols * 255.0).astype(np.uint8)
    else:
        rgb = cols.astype(np.uint8)

    # Wrap as Open3D mesh for writers downstream.
    try:
        import open3d as o3d

        tri = o3d.geometry.TriangleMesh(
            o3d.utility.Vector3dVector(xyz),
            o3d.utility.Vector3iVector(faces.astype(np.int32)),
        )
        cols_f = cols.astype(np.float64)
        if cols_f.max() > 1.0:
            cols_f = cols_f / 255.0
        tri.vertex_colors = o3d.utility.Vector3dVector(cols_f)
        tri.compute_vertex_normals()
    except Exception:
        tri = mesh

    return xyz, rgb, tri


def _auto_voxel(session: RgbdSession, samples: int = 8) -> float:
    """Pick TSDF voxel from working distance: ~4 mm close-up object scans,
    1 cm room sweeps. The 256x192 sensor resolves ~Z/fx laterally (≈2.6 mm at
    0.55 m), so 1 cm voxels bin away real geometry on tabletop captures."""
    from PIL import Image

    meds = []
    step = max(1, session.n_frames // samples)
    for fr in session.frames[::step][:samples]:
        d = np.array(Image.open(fr.depth_path))
        h, w = d.shape[:2]
        patch = d[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
        valid = patch[patch > 50]
        if len(valid):
            meds.append(float(np.median(valid)) / 1000.0)
    med = float(np.median(meds)) if meds else 2.0
    return 0.004 if med < 1.2 else 0.01


def fuse_session(
    session_dir: str,
    out_dir: str,
    backend: str = "auto",
    voxel_size: Optional[float] = None,
    clean: bool = True,
    mesh: bool = True,
    viewer: bool = True,
) -> dict:
    """End-to-end: validate session → fuse → write scan artifacts."""
    session = load_session(session_dir)
    if voxel_size is None:
        voxel_size = _auto_voxel(session)
        print(f"  voxel auto-selected: {voxel_size * 1000:.0f} mm")
    avail = backend_available()
    if backend == "auto":
        backend = avail["preferred"] or "open3d"
    if backend == "nvblox" and not avail["nvblox_torch"]:
        raise RgbdSessionError(
            "Requested nvblox but nvblox_torch is not importable. "
            "Install per docs/NVBLOX_WORKFLOW.md or pass --backend open3d."
        )
    if backend == "open3d" and not avail["open3d_tsdf"]:
        raise RgbdSessionError("Open3D not installed (pip/conda install open3d).")

    os.makedirs(out_dir, exist_ok=True)
    if backend == "nvblox":
        xyz, rgb, tri = fuse_nvblox(session, voxel_size=voxel_size)
        method = "nvblox"
    else:
        xyz, rgb, tri = fuse_open3d_tsdf(session, voxel_size=voxel_size)
        method = "open3d_tsdf"

    if len(xyz) < 100:
        raise RgbdSessionError(
            f"Fusion produced only {len(xyz)} points — check depth scale/poses."
        )

    xyz_l = np.asarray(xyz, dtype=np.float64)
    rgb_l = np.asarray(rgb, dtype=np.uint8)
    cloud = ScenePointCloud(
        xyz=[(float(p[0]), float(p[1]), float(p[2])) for p in xyz_l],
        rgb=[(int(p[0]), int(p[1]), int(p[2])) for p in rgb_l],
        stats={
            "method": method,
            "voxel_size": voxel_size,
            "n_frames": session.n_frames,
            "object_hint": session.object_hint,
        },
    )

    result = write_artifacts(
        cloud,
        out_dir,
        backend_name=method,
        splat=False,
        viewer=viewer,
        title=session.object_hint or "rgbd",
        clean=clean,
        mesh=mesh,
    )
    result["backend"] = method
    result["n_frames"] = session.n_frames

    # Also write the triangle mesh from the volume (higher fidelity than Poisson).
    mesh_path = os.path.join(out_dir, "scene_tsdf_mesh.ply")
    try:
        import open3d as o3d

        if hasattr(tri, "vertices") and not isinstance(tri, o3d.geometry.TriangleMesh):
            # nvblox mesh — try convert
            v = tri.vertices
            v = v.detach().cpu().numpy() if hasattr(v, "detach") else np.asarray(v)
            f = getattr(tri, "faces", None) or getattr(tri, "triangles", None)
            if f is not None:
                f = f.detach().cpu().numpy() if hasattr(f, "detach") else np.asarray(f)
                tm = o3d.geometry.TriangleMesh(
                    o3d.utility.Vector3dVector(v),
                    o3d.utility.Vector3iVector(f.astype(np.int32)),
                )
                tm.compute_vertex_normals()
                o3d.io.write_triangle_mesh(mesh_path, tm)
                result["tsdf_mesh"] = mesh_path
        else:
            o3d.io.write_triangle_mesh(mesh_path, tri)
            result["tsdf_mesh"] = mesh_path
    except Exception as e:  # noqa: BLE001
        result["tsdf_mesh_error"] = str(e)[:200]

    # Object crop: strip floor/clutter for viewing + measurement
    try:
        from .object_crop import crop_fused_dir, measure_object_crop

        crop = crop_fused_dir(out_dir, focus=0.85, also_mesh=True)
        result["object_crop"] = crop
        if crop.get("ok"):
            mo = measure_object_crop(out_dir)
            if mo:
                result["object_measure"] = mo.get("aabb", {}).get("summary")
    except Exception as e:  # noqa: BLE001 — crop is best-effort
        result["object_crop_error"] = str(e)[:200]

    # Light object asset: score frames → keep best 1 → plane/cluster (no TSDF)
    try:
        from .rgbd_object_asset import build_object_asset

        asset_dir = os.path.join(out_dir, "object_asset")
        asset = build_object_asset(
            session.root, asset_dir, max_frames=4, stride=2
        )
        result["object_asset"] = {
            "dir": asset_dir,
            "frames_used": asset.get("frames_used"),
            "points": asset.get("points"),
            "open_me": asset["artifacts"]["open_me"],
            "ply": asset["artifacts"]["ply"],
        }
    except Exception as e:  # noqa: BLE001 — asset is best-effort
        result["object_asset_error"] = str(e)[:200]

    meta_path = os.path.join(out_dir, "rgbd_meta.json")
    with open(meta_path, "w") as fh:
        json.dump(
            {
                "backend": method,
                "voxel_size": voxel_size,
                "n_frames": session.n_frames,
                "session": session.root,
                "object_hint": session.object_hint,
                "location": session.location,
                "artifacts": {
                    k: v for k, v in result.items()
                    if isinstance(v, (str, int, float, bool))
                },
                "object_crop": result.get("object_crop"),
            },
            fh,
            indent=2,
        )
    result["meta"] = meta_path
    return result
