"""Mesh helpers for real (textured) reconstruction output.

TRELLIS / Nerfstudio return GLB/PLY meshes that are too heavy to embed in
the offline canvas viewer. ``to_preview_obj`` makes a light, low-poly OBJ
stand-in (decimated, or convex hull as a last resort) so the existing
control-center viewer keeps working. The full GLB stays the real asset.

Requires trimesh (the `reconstruct` extra); imported lazily.
"""

from __future__ import annotations

import os


def to_preview_obj(src_path: str, dst_obj: str, max_faces: int = 3000) -> str:
    """Load any mesh trimesh can read and write a small OBJ preview."""
    import trimesh  # lazy

    scene_or_mesh = trimesh.load(src_path, force="mesh")
    mesh = scene_or_mesh
    if hasattr(mesh, "faces") and len(mesh.faces) > max_faces:
        try:
            mesh = mesh.simplify_quadric_decimation(max_faces)
        except Exception:
            # optional decimation backend missing -> rough convex hull
            mesh = mesh.convex_hull
    os.makedirs(os.path.dirname(dst_obj) or ".", exist_ok=True)
    mesh.export(dst_obj)
    return dst_obj


def bounds_dimensions_m(src_path: str) -> tuple[float, float, float]:
    """Axis-aligned size (w, h, d) of a mesh, in its own units (usually m)."""
    import trimesh  # lazy

    mesh = trimesh.load(src_path, force="mesh")
    ext = mesh.bounding_box.extents
    return (float(ext[0]), float(ext[1]), float(ext[2]))
