"""Ingest a CrateScanner export (OBJ/STL + optional measurement.json).

CrateScanner bakes coordinates to **inches** by default. We convert to meters
for the rest of assetpipe / Isaac, and keep the inch dims in dims.json.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Optional


INCHES_PER_METER = 39.37007874015748


class CrateScanError(ValueError):
    pass


def _find_mesh(root: str) -> str:
    for name in ("mesh.obj", "mesh.stl", "mesh.usdz", "mesh.ply"):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            return p
    for ext in (".obj", ".stl", ".ply", ".usdz"):
        for f in sorted(os.listdir(root)):
            if f.lower().endswith(ext) and not f.startswith("."):
                return os.path.join(root, f)
    raise CrateScanError(
        f"No mesh (.obj/.stl/.ply/.usdz) in {root!r}. "
        "Export from CrateScanner share sheet into this folder."
    )


def _load_measurement(root: str) -> Optional[dict]:
    for name in ("measurement.json", "MeasurementResult.json", "dims.json"):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            with open(p) as fh:
                return json.load(fh)
    return None


def _scale_obj_inches_to_meters(src: str, dst: str) -> None:
    """Scale OBJ verts inches→meters and fan-triangulate polygon faces for Open3D."""
    scale = 1.0 / INCHES_PER_METER
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(src, "r", errors="replace") as fin, open(dst, "w") as fout:
        fout.write("# scaled inches -> meters by assetpipe cratescan\n")
        for line in fin:
            if line.startswith("v "):
                parts = line.split()
                if len(parts) >= 4:
                    x, y, z = (float(parts[1]), float(parts[2]), float(parts[3]))
                    rest = " ".join(parts[4:])
                    if rest:
                        fout.write(f"v {x * scale} {y * scale} {z * scale} {rest}\n")
                    else:
                        fout.write(f"v {x * scale} {y * scale} {z * scale}\n")
                    continue
            if line.startswith("f "):
                parts = line.split()[1:]
                if len(parts) >= 4:
                    # fan triangulation: (0,i,i+1)
                    for i in range(1, len(parts) - 1):
                        fout.write(f"f {parts[0]} {parts[i]} {parts[i + 1]}\n")
                    continue
            fout.write(line)


def _open3d_write_ply(src: str, dst_ply: str, scale: float) -> dict:
    import numpy as np
    import open3d as o3d

    mesh = o3d.io.read_triangle_mesh(src)
    if mesh.is_empty():
        raise CrateScanError(f"Open3D could not read mesh (empty): {src}")
    verts = np.asarray(mesh.vertices) * scale
    mesh.vertices = o3d.utility.Vector3dVector(verts)
    mesh.compute_vertex_normals()
    o3d.io.write_triangle_mesh(dst_ply, mesh)
    extent = mesh.get_axis_aligned_bounding_box().get_extent()
    return {
        "vertices": int(len(mesh.vertices)),
        "triangles": int(len(mesh.triangles)),
        "extent_m": [float(extent[0]), float(extent[1]), float(extent[2])],
    }


def ingest_cratescan(
    input_path: str,
    out_dir: str,
    units: str = "inches",
) -> dict:
    """Normalize a CrateScanner export folder (or single mesh file) into out_dir."""
    input_path = os.path.abspath(input_path)
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    measurement: Optional[dict] = None
    if os.path.isfile(input_path):
        ext = os.path.splitext(input_path)[1].lower()
        mesh_src = os.path.join(out_dir, f"mesh_original{ext}")
        shutil.copy2(input_path, mesh_src)
    else:
        mesh_src = _find_mesh(input_path)
        measurement = _load_measurement(input_path)
        ext = os.path.splitext(mesh_src)[1].lower()
        shutil.copy2(mesh_src, os.path.join(out_dir, f"mesh_original{ext}"))
        if measurement is not None:
            with open(os.path.join(out_dir, "measurement.json"), "w") as fh:
                json.dump(measurement, fh, indent=2)

    from_inches = units.lower() in {"inches", "in", "inch"}
    scale = (1.0 / INCHES_PER_METER) if from_inches else 1.0
    mesh_meters_obj = os.path.join(out_dir, "mesh_meters.obj")
    mesh_meters_ply = os.path.join(out_dir, "mesh_meters.ply")

    stats: dict = {
        "source_mesh": mesh_src,
        "units_in": units,
        "from_inches": from_inches,
        "out_dir": out_dir,
    }

    if ext == ".obj":
        if from_inches:
            _scale_obj_inches_to_meters(mesh_src, mesh_meters_obj)
        else:
            shutil.copy2(mesh_src, mesh_meters_obj)
        stats["mesh_meters_obj"] = mesh_meters_obj
        try:
            stats.update(_open3d_write_ply(mesh_meters_obj, mesh_meters_ply, scale=1.0))
            stats["mesh_meters_ply"] = mesh_meters_ply
        except Exception as e:  # noqa: BLE001
            stats["ply_error"] = str(e)[:200]
    else:
        stats.update(_open3d_write_ply(mesh_src, mesh_meters_ply, scale=scale))
        stats["mesh_meters_ply"] = mesh_meters_ply
        import open3d as o3d

        m = o3d.io.read_triangle_mesh(mesh_meters_ply)
        o3d.io.write_triangle_mesh(mesh_meters_obj, m)
        stats["mesh_meters_obj"] = mesh_meters_obj

    dims: dict = {
        "source": "cratescanner",
        "measurement": measurement,
        "extent_m": stats.get("extent_m"),
        "note": "CrateScanner exports inches by default; mesh_meters.* are meters.",
    }
    if measurement:
        p = float(measurement.get("paddingInches") or 0)
        rl = float(measurement.get("rawLengthInches") or 0)
        rw = float(measurement.get("rawWidthInches") or 0)
        rh = float(measurement.get("rawHeightInches") or 0)
        dims["raw_inches"] = {"length": rl, "width": rw, "height": rh}
        dims["crate_inches"] = {
            "length": rl + 2 * p,
            "width": rw + 2 * p,
            "height": rh + 2 * p,
            "padding": p,
        }

    dims_path = os.path.join(out_dir, "dims.json")
    with open(dims_path, "w") as fh:
        json.dump(dims, fh, indent=2)
    stats["dims"] = dims_path

    with open(os.path.join(out_dir, "README.txt"), "w") as fh:
        fh.write(
            "CrateScanner ingest (assetpipe cratescan)\n"
            "See docs/CRATESCANNER_BRIDGE.md\n"
            f"mesh: {stats.get('mesh_meters_ply') or stats.get('mesh_meters_obj')}\n"
            f"dims: {dims_path}\n"
        )
    return stats
