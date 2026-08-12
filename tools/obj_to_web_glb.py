#!/usr/bin/env python3
"""Textured OBJ (Meshroom output) -> a GLB a browser can open.

Keeps the UV texture rather than baking to vertex colours: photogrammetry's
whole selling point is the photographic surface detail, and per-vertex colour
at any sane decimation throws most of it away.
"""
import json
import sys
from pathlib import Path

import numpy as np
import trimesh

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
target_faces = int(sys.argv[3]) if len(sys.argv) > 3 else 300_000

# Optional similarity transform (from metric_scale_sfm.py): scale + rotation
# into ARKit's gravity-aligned Y-up world. Without it a Meshroom mesh keeps its
# arbitrary SfM frame — the photo run's was almost exactly upside down.
xform = None
if len(sys.argv) > 4:
    x = json.loads(Path(sys.argv[4]).read_text())
    import numpy as _np
    xform = (float(x["scale"]), _np.array(x["rotation"]), _np.array(x["translation"]))
    print(f"  applying similarity: scale={xform[0]:.4f} -> {x['target_frame']}"
          f" (residual {x['residual_mean_m']*1000:.0f} mm, {x['verdict']})")

scene = trimesh.load(src, process=False)
geoms = [scene] if isinstance(scene, trimesh.Trimesh) else list(scene.geometry.values())
print(f"  loaded {len(geoms)} geometry group(s)")

out = []
for i, g in enumerate(geoms):
    print(f"   [{i}] verts={len(g.vertices):,} faces={len(g.faces):,} visual={type(g.visual).__name__}")
    if len(g.faces) > target_faces // max(len(geoms), 1):
        # Texture UVs do not survive quadric decimation in trimesh, so bake the
        # texture into vertex colours FIRST, then decimate — colours interpolate.
        try:
            g = g.copy()
            g.visual = g.visual.to_color()
            print(f"       baked texture -> vertex colours")
        except Exception as e:
            print(f"       bake failed ({e}); keeping as-is")
        import open3d as o3d
        m = o3d.geometry.TriangleMesh(
            o3d.utility.Vector3dVector(np.asarray(g.vertices)),
            o3d.utility.Vector3iVector(np.asarray(g.faces)))
        try:
            vc = np.asarray(g.visual.vertex_colors)[:, :3] / 255.0
            m.vertex_colors = o3d.utility.Vector3dVector(vc)
        except Exception:
            pass
        n = max(10_000, target_faces // max(len(geoms), 1))
        m = m.simplify_quadric_decimation(target_number_of_triangles=n)
        m.remove_degenerate_triangles(); m.remove_duplicated_vertices(); m.remove_unreferenced_vertices()
        cols = (np.asarray(m.vertex_colors) * 255).astype(np.uint8) if m.has_vertex_colors() else None
        g = trimesh.Trimesh(vertices=np.asarray(m.vertices), faces=np.asarray(m.triangles),
                            vertex_colors=cols, process=False)
        print(f"       decimated -> verts={len(g.vertices):,} faces={len(g.faces):,}")
    out.append(g)

mesh = trimesh.util.concatenate(out) if len(out) > 1 else out[0]
if xform is not None:
    s, R, t = xform
    T = np.eye(4)
    T[:3, :3] = s * R
    T[:3, 3] = t
    mesh.apply_transform(T)
    print(f"  transformed: bbox now {np.round(mesh.bounding_box.extents,3)} (metres, Y up)")
mesh.apply_translation(-mesh.bounding_box.centroid)
# Normals are REQUIRED: a lit material with no NORMAL attribute renders black.
mesh.vertex_normals  # force computation
mesh.export(str(dst), include_normals=True)
print(f"  out: {dst}  {dst.stat().st_size/1e6:.1f} MB")
print(f"  bbox: {np.round(mesh.bounding_box.extents,3)}")

import json, struct
with open(dst, "rb") as f:
    f.read(12); clen, _ = struct.unpack('<II', f.read(8)); gl = json.loads(f.read(clen))
attrs = sorted(gl["meshes"][0]["primitives"][0]["attributes"].keys())
print(f"  GLB attributes: {attrs}   NORMAL present: {'NORMAL' in attrs}")
