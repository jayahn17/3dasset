"""Object-centric crop for fused RGB-D / scan clouds.

Strips floor + far-field clutter so viewers and dims focus on the scanned
object (bottle, machine, …) rather than the whole room.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional


def _tighten_small_object(xyz, rgb, max_extent_m: float = 0.55):
    """If crop is still room-sized, keep the densest elevated blob (bottle-scale)."""
    import numpy as np

    if len(xyz) < 80:
        return xyz, rgb
    extent = xyz.max(0) - xyz.min(0)
    if float(extent.max()) <= max_extent_m:
        return xyz, rgb

    # Height axis ≈ smallest PCA? Prefer world-up if gravity-ish (largest vertical span not always Y)
    # Use axis with strongest plane-normal from earlier: assume largest horizontal extent axes.
    # Score voxels by count * mean height above lowest 10%.
    lo = np.percentile(xyz, 8, axis=0)
    hi = np.percentile(xyz, 92, axis=0)
    # pick up-axis as the one with smaller horizontal footprint in percentiles... use Y if ARKit-ish
    # Heuristic: up = axis with least horizontal travel of camera-ish — use variance: smallest
    # variance among axes after clipping extremes is often height for a floor scan? Actually floor
    # has large XZ variance. So up = argmin of (hi-lo) among axes that still have structure.
    spans = hi - lo
    up = int(np.argmin(spans))  # bottle standing → height often medium; carpet flat → up is small span
    # For carpet+bottle, height span of full crop is still large. Better: up = axis where
    # the top 20% of points form a compact cluster.
    # Try each axis as up; pick densest compact peak.
    best = None
    for axis in range(3):
        h = xyz[:, axis]
        h0 = np.percentile(h, 15)
        elevated = h > h0 + 0.03  # 3 cm above lower sheet
        if elevated.sum() < 40:
            continue
        P = xyz[elevated]
        # voxel density peak
        voxel = 0.025
        keys = np.floor(P / voxel).astype(np.int64)
        # hash
        uniq, counts = np.unique(keys, axis=0, return_counts=True)
        peak = uniq[int(np.argmax(counts))]
        center = (peak + 0.5) * voxel
        d = np.linalg.norm(xyz - center, axis=1)
        # radius: grow until extent ~ bottle or 25cm
        for rad in (0.12, 0.18, 0.25, 0.35):
            sel = d <= rad
            if sel.sum() < 40:
                continue
            sub = xyz[sel]
            ext = float((sub.max(0) - sub.min(0)).max())
            score = int(sel.sum()) / max(ext, 0.05)  # prefer dense+compact
            if best is None or score > best[0]:
                best = (score, sel, axis, rad, center)
    if best is None:
        return xyz, rgb
    _, sel, axis, rad, center = best
    return xyz[sel], rgb[sel]


def crop_fused_dir(
    out_dir: str,
    *,
    focus: float = 0.85,
    source_ply: str | None = None,
    also_mesh: bool = True,
    tighten: bool = True,
) -> dict[str, Any]:
    """Write ``scene_object.ply`` (+ optional cropped TSDF mesh) into ``out_dir``.

    Prefers ``scene_clean.ply``, else ``scene.ply``. Uses
    :func:`assetpipe.scene.splat.isolate_object`, then optional tighten for
    small objects on large floors.
    """
    from .io import read_ply, write_ply, write_splat
    from .splat import isolate_object
    from .viewer import build_scan_viewer

    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    src = source_ply
    if not src:
        for name in ("scene_clean.ply", "scene.ply"):
            p = os.path.join(out_dir, name)
            if os.path.isfile(p):
                src = p
                break
    if not src or not os.path.isfile(src):
        return {"ok": False, "error": "no scene_clean.ply / scene.ply to crop"}

    xyz, rgb = read_ply(src)
    n0 = len(xyz)
    if n0 < 50:
        return {"ok": False, "error": f"too few points ({n0})", "source": src}

    import numpy as np

    xyz_a = np.asarray(xyz, dtype=np.float64)
    rgb_a = np.asarray(rgb, dtype=np.uint8)
    xyz_o, rgb_o = isolate_object(xyz_a, rgb_a, focus=focus, drop_support=True)
    n1 = len(xyz_o)
    if n1 < 30:
        xyz_o, rgb_o = isolate_object(xyz_a, rgb_a, focus=min(1.4, focus + 0.35),
                                      drop_support=True)
        n1 = len(xyz_o)
    if n1 < 30:
        return {
            "ok": False,
            "error": f"crop left only {n1} points (from {n0})",
            "source": src,
            "focus": focus,
        }

    tightened = False
    if tighten:
        before = len(xyz_o)
        xyz_o, rgb_o = _tighten_small_object(xyz_o, rgb_o)
        tightened = len(xyz_o) < before
        n1 = len(xyz_o)
        if n1 < 30:
            return {
                "ok": False,
                "error": f"tighten left only {n1} points",
                "source": src,
                "focus": focus,
            }

    xyz_list = [tuple(map(float, p)) for p in xyz_o]
    rgb_list = [tuple(map(int, c)) for c in rgb_o]

    obj_ply = write_ply(os.path.join(out_dir, "scene_object.ply"), xyz_list, rgb_list)
    obj_splat = write_splat(os.path.join(out_dir, "scene_object.splat"),
                            xyz_list, rgb_list)

    ctr = xyz_o.mean(axis=0)
    extent = xyz_o.max(axis=0) - xyz_o.min(axis=0)
    report: dict[str, Any] = {
        "ok": True,
        "source": src,
        "focus": focus,
        "tightened": tightened,
        "points_in": n0,
        "points_out": n1,
        "kept_frac": round(n1 / max(n0, 1), 4),
        "center_m": [float(c) for c in ctr],
        "extent_m": [float(e) for e in extent],
        "object_ply": obj_ply,
        "object_splat": obj_splat,
    }

    # Crop TSDF / Poisson mesh to object AABB (padded)
    if also_mesh:
        mesh_src = None
        for name in ("scene_tsdf_mesh.ply", "scene_mesh.glb"):
            p = os.path.join(out_dir, name)
            if os.path.isfile(p) and name.endswith(".ply"):
                mesh_src = p
                break
        if mesh_src:
            try:
                import open3d as o3d

                mesh = o3d.io.read_triangle_mesh(mesh_src)
                if not mesh.is_empty() and len(mesh.triangles) > 0:
                    pad = 0.08 * float(np.linalg.norm(extent)) + 0.02
                    mn = xyz_o.min(axis=0) - pad
                    mx = xyz_o.max(axis=0) + pad
                    aabb = o3d.geometry.AxisAlignedBoundingBox(mn, mx)
                    cropped = mesh.crop(aabb)
                    if len(cropped.triangles) > 10:
                        dst = os.path.join(out_dir, "scene_object_mesh.ply")
                        cropped.compute_vertex_normals()
                        o3d.io.write_triangle_mesh(dst, cropped)
                        report["object_mesh"] = dst
                        report["object_mesh_tris"] = int(len(cropped.triangles))
            except Exception as e:  # noqa: BLE001
                report["mesh_crop_error"] = str(e)[:200]

    # Object-only offline viewer
    try:
        report["object_viewer"] = build_scan_viewer(
            os.path.join(out_dir, "object_view.html"),
            xyz_list,
            rgb_list,
            title="object crop",
            stats={
                "points": n1,
                "kept_frac": report["kept_frac"],
                "focus": focus,
                "tightened": tightened,
            },
            links={
                "scene_object.ply": "scene_object.ply",
                "scene_object.splat": "scene_object.splat",
                **({"scene_object_mesh.ply": "scene_object_mesh.ply"}
                   if "object_mesh" in report else {}),
            },
        )
    except Exception as e:  # noqa: BLE001
        report["viewer_error"] = str(e)[:200]

    meta_path = os.path.join(out_dir, "object_crop.json")
    with open(meta_path, "w") as fh:
        json.dump(report, fh, indent=2)
    report["meta"] = meta_path
    return report


def measure_object_crop(
    out_dir: str,
    *,
    padding_inches: float = 2.0,
) -> Optional[dict]:
    """Re-run inch measurement preferring ``scene_object_mesh.ply`` / object ply."""
    from .measure import inject_dims_hud, measure_geometry, quantize_inches, format_lwh

    out_dir = os.path.abspath(out_dir)
    mesh = None
    for name in ("scene_object_mesh.ply", "scene_object.ply"):
        p = os.path.join(out_dir, name)
        if os.path.isfile(p):
            mesh = p
            break
    if not mesh:
        return None

    m = measure_geometry(
        mesh, units="meters", padding_inches=padding_inches, label="object_crop",
    )
    # merge into dims.json if present
    dims_path = os.path.join(out_dir, "dims.json")
    dims: dict = {}
    if os.path.isfile(dims_path):
        with open(dims_path) as fh:
            dims = json.load(fh)
    dims["object_crop"] = m
    dims["primary"] = "object_crop"
    q = m["aabb"]["inches_0_25"]
    dims["object_crop_summary"] = (
        f"{q['length']:.2f} × {q['width']:.2f} × {q['height']:.2f} in @0.25″"
    )
    with open(dims_path, "w") as fh:
        json.dump(dims, fh, indent=2)

    txt = os.path.join(out_dir, "measurement.txt")
    lines = []
    if os.path.isfile(txt):
        lines = open(txt).read().splitlines()
    block = [
        "",
        "OBJECT CROP (isolate_object)",
        f"  raw:  {m['aabb'].get('summary_raw', m['aabb']['summary'])}",
        f"  @0.25″: {format_lwh(q['length'], q['width'], q['height'])}",
        f"  crate: {m['aabb']['crate_summary']}",
    ]
    with open(txt, "w") as fh:
        fh.write("\n".join(lines + block) + "\n")

    viewer = os.path.join(out_dir, "object_view.html")
    if os.path.isfile(viewer):
        inject_dims_hud(viewer, {"object": m})
    scan = os.path.join(out_dir, "scan_view.html")
    if os.path.isfile(scan):
        inject_dims_hud(scan, {"object": m})
    return m
