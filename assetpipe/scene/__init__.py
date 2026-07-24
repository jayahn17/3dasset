"""Scene scanning — the Scaniverse-style path.

One call: frames (or a video, via the CLI/worker) → ONE point cloud →
artifacts you can open anywhere:

    scan_out/
      scene.ply          colored point cloud (MeshLab/Blender/son)
      scene.splat        antimatter15 splat (any web splat viewer) [--splat]
      scene_clean.ply    background removed: the ASSET             [--clean]
      scene_clean.splat  cleaned splat                             [--clean --splat]
      scene_gaussians.ply  real trained gaussians                  [splatfacto]
      scan_view.html     self-contained offline viewer (drag orbit)

``--clean`` runs the automatic background removal (outliers → ground plane
→ clutter clusters, see :mod:`assetpipe.scene.clean`); the viewer then
shows the cleaned asset. For live capture with a growing preview, see
:class:`LiveScanSession` / the capture worker's ``/live`` page.

No detector, no classes, no catalog — for the identify-and-file-into-the-
twin flow use ``assetpipe run`` / ``AssetPipeline`` instead.
"""

from __future__ import annotations

import os
import shutil

from .backends import (
    ColmapSceneBackend,
    SceneBackend,
    ScenePointCloud,
    SplatfactoSceneBackend,
    StubSceneBackend,
    ThreeDGutSceneBackend,
    VggtSceneBackend,
    make_scene_backend,
)
from .clean import clean_cloud
from .io import read_ply, write_ply, write_splat
from .mesh import cloud_to_mesh
from .viewer import build_scan_viewer

__all__ = [
    "SceneBackend", "ScenePointCloud", "StubSceneBackend",
    "ColmapSceneBackend", "VggtSceneBackend", "SplatfactoSceneBackend",
    "ThreeDGutSceneBackend",
    "make_scene_backend", "scan_scene", "LiveScanSession", "clean_cloud",
    "cloud_to_mesh", "read_ply", "write_ply", "write_splat",
    "build_scan_viewer",
    # RGB-D / nvblox path (import submodule for fuse APIs)
]


def write_artifacts(
    cloud: ScenePointCloud,
    out_dir: str,
    backend_name: str,
    splat: bool = False,
    viewer: bool = True,
    title: str = "scan",
    clean: bool = False,
    mesh: bool = False,
) -> dict:
    """Write scene.ply (+ optional .splat / cleaned asset / mesh / viewer);
    return the artifact-path dict the CLI prints and the worker returns as
    JSON."""
    os.makedirs(out_dir, exist_ok=True)
    result: dict = {"backend": backend_name, "points": len(cloud.xyz),
                    **cloud.stats}
    result["ply"] = write_ply(os.path.join(out_dir, "scene.ply"),
                              cloud.xyz, cloud.rgb)
    if splat:
        result["splat"] = write_splat(os.path.join(out_dir, "scene.splat"),
                                      cloud.xyz, cloud.rgb)

    shown = cloud  # what the viewer displays
    if clean:
        cleaned = clean_cloud(cloud)
        if cleaned.xyz:
            result["clean_ply"] = write_ply(
                os.path.join(out_dir, "scene_clean.ply"), cleaned.xyz, cleaned.rgb)
            if splat:
                result["clean_splat"] = write_splat(
                    os.path.join(out_dir, "scene_clean.splat"),
                    cleaned.xyz, cleaned.rgb)
            result["clean_points"] = len(cleaned.xyz)
            for k in ("outliers_removed", "ground_removed", "clutter_removed",
                      "clusters_kept"):
                if k in cleaned.stats:
                    result[k] = cleaned.stats[k]
            shown = cleaned

    if cloud.gaussian_ply:  # keep the real gaussians beside the point preview
        dst = os.path.join(out_dir, "scene_gaussians.ply")
        if os.path.abspath(cloud.gaussian_ply) != os.path.abspath(dst):
            shutil.copy(cloud.gaussian_ply, dst)
        result["gaussian_ply"] = dst

    usdz = cloud.stats.get("usdz") if cloud.stats else None
    if usdz and os.path.exists(usdz):  # NuRec / Isaac Sim artifact from 3dgut
        dst = os.path.join(out_dir, "scene.usdz")
        if os.path.abspath(usdz) != os.path.abspath(dst):
            shutil.copy(usdz, dst)
        result["usdz"] = dst

    if mesh:  # surface the (cleaned) cloud as a vertex-colored GLB
        try:
            m = cloud_to_mesh(shown.xyz, shown.rgb,
                              os.path.join(out_dir, "scene_mesh.glb"))
            result["mesh"] = m["mesh"]
            result["mesh_method"] = m["method"]
            result["mesh_faces"] = m["faces"]
        except Exception as e:  # noqa: BLE001 — meshing is best-effort
            result["mesh_error"] = str(e)[:200]

    if viewer:
        links = {os.path.basename(result[k]): os.path.basename(result[k])
                 for k in ("clean_ply", "clean_splat", "ply", "splat",
                           "mesh", "gaussian_ply") if k in result}
        vstats = {"backend": backend_name, **shown.stats}
        result["viewer"] = build_scan_viewer(
            os.path.join(out_dir, "scan_view.html"),
            shown.xyz, shown.rgb, title=title, stats=vstats, links=links,
        )
    return result


def scan_scene(
    image_paths: list[str],
    out_dir: str,
    backend: str | SceneBackend = "auto",
    splat: bool = False,
    viewer: bool = True,
    title: str = "scan",
    clean: bool = False,
    mesh: bool = False,
) -> dict:
    """Frames in → ``scene.ply`` (+ optional ``.splat`` / cleaned asset /
    mesh / offline viewer) out. Returns the artifact dict (see
    :func:`write_artifacts`) — also what the capture worker returns as JSON."""
    if not image_paths:
        raise ValueError("no input frames")
    b = backend if isinstance(backend, SceneBackend) else make_scene_backend(backend)
    cloud = b.reconstruct(image_paths, os.path.join(out_dir, "_scan_work"))
    return write_artifacts(cloud, out_dir, b.name, splat=splat, viewer=viewer,
                           title=title, clean=clean, mesh=mesh)


from .live import LiveScanSession  # noqa: E402 — needs write_artifacts above
