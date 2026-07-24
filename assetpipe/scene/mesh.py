"""Point cloud → MESH — see the scan as a surface, not pixels.

The Meshy-style step: after (cleaned) reconstruction, produce a triangle
mesh with vertex colors as a GLB any viewer/DCC (three.js, Blender,
KeyShot, son's jay3d) can open.

    method="poisson"  open3d Poisson surface reconstruction (best; needs
                      `pip install open3d`), low-density blobs trimmed
    method="alpha"    scipy Delaunay alpha shape (rougher shell, no open3d)
    method="auto"     poisson if open3d is importable, else alpha

A sparse SfM cloud (~2k pts) gives a preview-grade mesh; dense clouds
(vggt) or TRELLIS (image→textured mesh, services/trellis_server.py) are
the production-quality path.
"""

from __future__ import annotations

import importlib.util
import math


def cloud_to_mesh(
    xyz: list[tuple[float, float, float]],
    rgb: list[tuple[int, int, int]],
    out_path: str,
    method: str = "auto",
    poisson_depth: int = 8,
) -> dict:
    """Write a vertex-colored mesh (format from ``out_path`` extension,
    .glb recommended). Returns {"mesh": path, "method": ..., "faces": n}."""
    if len(xyz) < 20:
        raise ValueError(f"too few points to mesh ({len(xyz)})")
    if method == "auto":
        method = "poisson" if importlib.util.find_spec("open3d") else "alpha"
    if method == "poisson":
        verts, faces, colors = _poisson(xyz, rgb, poisson_depth)
    elif method == "alpha":
        verts, faces, colors = _alpha_shape(xyz, rgb)
    else:
        raise ValueError(f"unknown mesh method {method!r}")

    import numpy as np
    import trimesh

    rgba = np.hstack([np.asarray(colors, np.uint8),
                      np.full((len(colors), 1), 255, np.uint8)])
    mesh = trimesh.Trimesh(vertices=verts, faces=faces,
                           vertex_colors=rgba, process=True)
    mesh.export(out_path)
    return {"mesh": out_path, "method": method, "faces": len(mesh.faces)}


def _diag(xyz) -> float:
    lo = [min(p[k] for p in xyz) for k in range(3)]
    hi = [max(p[k] for p in xyz) for k in range(3)]
    return math.dist(lo, hi) or 1.0


def _poisson(xyz, rgb, depth):
    import numpy as np
    import open3d as o3d

    pts = np.asarray(xyz, np.float64)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    pcd.colors = o3d.utility.Vector3dVector(np.asarray(rgb, np.float64) / 255.0)
    r = _diag(xyz) * 0.05
    pcd.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=r, max_nn=30))
    try:  # best-effort global orientation; noisy sparse clouds can fail
        pcd.orient_normals_consistent_tangent_plane(20)
    except Exception:  # noqa: BLE001
        pass
    mesh, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=depth)
    dens = np.asarray(dens)
    # Poisson closes everything watertight; carve away the invented surface
    # (low sample density) and anything outside the cloud's neighborhood
    mesh.remove_vertices_by_mask(dens < np.quantile(dens, 0.08))
    bbox = pcd.get_axis_aligned_bounding_box().scale(1.08, pcd.get_center())
    mesh = mesh.crop(bbox)
    return (np.asarray(mesh.vertices),
            np.asarray(mesh.triangles),
            (np.asarray(mesh.vertex_colors) * 255).clip(0, 255).astype(np.uint8))


def _alpha_shape(xyz, rgb):
    import numpy as np
    from scipy.spatial import Delaunay, cKDTree

    pts = np.asarray(xyz, np.float64)
    # alpha from the cloud's own sampling density
    nn = cKDTree(pts).query(pts, k=2)[0][:, 1]
    alpha = float(np.median(nn)) * 4.0
    tet = Delaunay(pts)
    edges = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
    simp = tet.simplices
    longest = np.zeros(len(simp))
    for a, b in edges:
        d = np.linalg.norm(pts[simp[:, a]] - pts[simp[:, b]], axis=1)
        longest = np.maximum(longest, d)
    keep = simp[longest < alpha]
    # boundary triangles = faces belonging to exactly one kept tetrahedron
    faces: dict[tuple, tuple] = {}
    for tetra in keep:
        for f in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)):
            tri = tuple(sorted(tetra[list(f)]))
            faces[tri] = None if tri in faces else tri
    boundary = np.array([t for t in faces.values() if t is not None])
    if len(boundary) == 0:
        raise RuntimeError("alpha shape found no surface — cloud too sparse")
    return pts, boundary, np.asarray(rgb, np.uint8)
