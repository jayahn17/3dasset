"""Inch measurements from CrateScanner meshes / fused RGB-D geometry.

Primary source of truth for *object* size is the on-device ``mesh.obj``
(coordinates baked in inches). Fused TSDF meshes are usually room-scale
and are reported separately for sanity checks.

All display values are quantized to **0.25 in** (Engin170 min readout).
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional


INCHES_PER_METER = 39.37007874015748
QUANTUM_IN = 0.25  # minimum reported resolution


def quantize_inches(value: float, quantum: float = QUANTUM_IN) -> float:
    """Round to nearest quantum (default 0.25\")."""
    if quantum <= 0:
        return float(value)
    return round(float(value) / quantum) * quantum


def format_inches(value: float, *, quantize: bool = True) -> str:
    v = quantize_inches(value) if quantize else float(value)
    return f"{v:.2f} in"


def format_lwh(
    length: float, width: float, height: float, *, quantize: bool = True
) -> str:
    return (
        f"{format_inches(length, quantize=quantize)} × "
        f"{format_inches(width, quantize=quantize)} × "
        f"{format_inches(height, quantize=quantize)}"
    )


def _load_points(path: str):
    import numpy as np
    import open3d as o3d

    mesh = o3d.io.read_triangle_mesh(path)
    if not mesh.is_empty() and len(mesh.triangles) > 0:
        pts = np.asarray(mesh.vertices, dtype=np.float64)
        geom = mesh
        kind = "mesh"
    else:
        pcd = o3d.io.read_point_cloud(path)
        pts = np.asarray(pcd.points, dtype=np.float64)
        geom = pcd
        kind = "pcd"
        if len(pts) == 0 and not mesh.is_empty():
            pts = np.asarray(mesh.vertices, dtype=np.float64)
            geom = mesh
            kind = "mesh_verts"
    if len(pts) == 0:
        raise ValueError(f"no points/vertices in {path}")
    return pts, geom, kind


def _guess_units(pts, units: str) -> str:
    if units != "auto":
        return units
    import numpy as np

    span = float((pts.max(axis=0) - pts.min(axis=0)).max())
    # Crate / machine meshes in inches are typically >> 5 along an axis.
    # Metric room/object clouds are usually a few meters.
    return "inches" if span > 5.0 else "meters"


def measure_geometry(
    path: str,
    *,
    units: str = "auto",
    padding_inches: float = 2.0,
    label: str = "geometry",
) -> dict[str, Any]:
    """AABB + OBB extents in inches, with 0.25\" quantized readout.

    Axis mapping matches CrateScanner ``MeasurementResult``:
      AABB x → width, y → height, z → length (ARKit / world-up).
    """
    import numpy as np

    pts, geom, kind = _load_points(path)
    units_used = _guess_units(pts, units)
    scale_to_in = 1.0 if units_used == "inches" else INCHES_PER_METER

    mn = pts.min(axis=0)
    mx = pts.max(axis=0)
    extent = (mx - mn) * scale_to_in  # inches
    # ARKit: x=width, y=height, z=length
    raw_w, raw_h, raw_l = float(extent[0]), float(extent[1]), float(extent[2])

    obb = geom.get_oriented_bounding_box()
    obb_ext = np.asarray(obb.extent, dtype=np.float64) * scale_to_in
    obb_sorted = sorted((float(obb_ext[0]), float(obb_ext[1]), float(obb_ext[2])),
                        reverse=True)

    q_l, q_w, q_h = (
        quantize_inches(raw_l),
        quantize_inches(raw_w),
        quantize_inches(raw_h),
    )
    pad = float(padding_inches)

    out: dict[str, Any] = {
        "label": label,
        "source_path": os.path.abspath(path),
        "geometry_kind": kind,
        "units_in": units_used,
        "n_points": int(len(pts)),
        "quantum_inches": QUANTUM_IN,
        "aabb": {
            "raw_inches": {
                "length": round(raw_l, 4),
                "width": round(raw_w, 4),
                "height": round(raw_h, 4),
            },
            "inches_0_25": {"length": q_l, "width": q_w, "height": q_h},
            "summary_raw": format_lwh(raw_l, raw_w, raw_h, quantize=False),
            "summary": format_lwh(raw_l, raw_w, raw_h, quantize=True),
            "crate_inches_0_25": {
                "length": quantize_inches(q_l + 2 * pad),
                "width": quantize_inches(q_w + 2 * pad),
                "height": quantize_inches(q_h + 2 * pad),
                "padding": pad,
            },
            "crate_summary": format_lwh(
                q_l + 2 * pad, q_w + 2 * pad, q_h + 2 * pad, quantize=True
            ),
        },
        "obb": {
            "raw_inches_sorted_lwh": [round(x, 4) for x in obb_sorted],
            "inches_0_25_sorted_lwh": [quantize_inches(x) for x in obb_sorted],
            "summary_sorted": format_lwh(
                obb_sorted[0], obb_sorted[1], obb_sorted[2], quantize=True
            ),
        },
        # MeasurementResult-compatible fields (quantized AABB)
        "rawLengthInches": q_l,
        "rawWidthInches": q_w,
        "rawHeightInches": q_h,
        "paddingInches": pad,
    }
    return out


def _find_package_mesh(root: str) -> Optional[str]:
    for dirpath, _dns, filenames in os.walk(root):
        for name in ("mesh.obj", "mesh.stl", "mesh.ply"):
            if name in filenames:
                return os.path.join(dirpath, name)
    return None


def _load_app_measurement(root: str) -> Optional[dict]:
    for dirpath, _dns, filenames in os.walk(root):
        for name in ("measurement.json", "MeasurementResult.json"):
            if name in filenames:
                with open(os.path.join(dirpath, name)) as fh:
                    return json.load(fh)
    return None


def package_root_from_session(session_dir: str) -> Optional[str]:
    """``.../CrateScan-ID/session`` → ``.../CrateScan-ID`` if mesh/README present."""
    parent = os.path.dirname(os.path.abspath(session_dir))
    if os.path.isfile(os.path.join(parent, "mesh.obj")):
        return parent
    if os.path.isfile(os.path.join(parent, "README_LINUX.txt")):
        return parent
    # zip may nest one extra folder level under work/
    grand = os.path.dirname(parent)
    if os.path.isfile(os.path.join(grand, "mesh.obj")):
        return grand
    return parent if os.path.isdir(parent) else None


def measure_cratescan_package(
    package_or_session: str,
    out_dir: str,
    *,
    padding_inches: float = 2.0,
    also_fuse_mesh: bool = True,
) -> dict[str, Any]:
    """Measure object dims from package mesh; optionally also fused scene mesh.

    Writes ``dims.json`` + ``measurement.txt`` into ``out_dir``.
    """
    path = os.path.abspath(package_or_session)
    os.makedirs(out_dir, exist_ok=True)

    if os.path.isfile(os.path.join(path, "manifest.json")):
        pkg = package_root_from_session(path) or path
    else:
        pkg = path

    report: dict[str, Any] = {
        "source": "assetpipe.measure",
        "package": pkg,
        "quantum_inches": QUANTUM_IN,
        "note": (
            "Object L×W×H from on-device mesh.obj (inches). "
            "Values under inches_0_25 are rounded to the nearest 0.25 in. "
            "Fused TSDF extents are scene-scale unless the capture cropped to the object."
        ),
    }

    app_m = _load_app_measurement(pkg) if os.path.isdir(pkg) else None
    if app_m:
        report["app_measurement"] = app_m
        try:
            rl = float(app_m.get("rawLengthInches") or 0)
            rw = float(app_m.get("rawWidthInches") or 0)
            rh = float(app_m.get("rawHeightInches") or 0)
            pad = float(app_m.get("paddingInches") or padding_inches)
            report["app_inches_0_25"] = {
                "length": quantize_inches(rl),
                "width": quantize_inches(rw),
                "height": quantize_inches(rh),
                "summary": format_lwh(rl, rw, rh),
                "crate_summary": format_lwh(rl + 2 * pad, rw + 2 * pad, rh + 2 * pad),
            }
        except (TypeError, ValueError):
            pass

    mesh = None
    if os.path.isfile(path) and path.lower().endswith((".obj", ".stl", ".ply", ".glb")):
        mesh = path
    elif os.path.isdir(pkg):
        mesh = _find_package_mesh(pkg)

    primary = None
    if mesh:
        primary = measure_geometry(
            mesh,
            units="auto",
            padding_inches=padding_inches,
            label="on_device_mesh",
        )
        report["object"] = primary
        report["primary"] = "object"
    else:
        report["object_error"] = "no mesh.obj/stl/ply found in package"

    if also_fuse_mesh:
        for cand, label in (
            (os.path.join(out_dir, "scene_tsdf_mesh.ply"), "fused_tsdf"),
            (os.path.join(out_dir, "scene_clean.ply"), "fused_clean"),
            (os.path.join(out_dir, "scene.ply"), "fused_cloud"),
        ):
            if os.path.isfile(cand):
                try:
                    report[label] = measure_geometry(
                        cand,
                        units="meters",
                        padding_inches=padding_inches,
                        label=label,
                    )
                except Exception as e:  # noqa: BLE001
                    report[f"{label}_error"] = str(e)[:200]
                break

    if primary is None and "fused_tsdf" in report:
        report["primary"] = "fused_tsdf"
        primary = report["fused_tsdf"]
    elif primary is None and "fused_clean" in report:
        report["primary"] = "fused_clean"
        primary = report["fused_clean"]

    dims_path = os.path.join(out_dir, "dims.json")
    with open(dims_path, "w") as fh:
        json.dump(report, fh, indent=2)

    lines = [
        "CrateScanner / assetpipe measurement",
        f"quantum: {QUANTUM_IN} in (min reported resolution)",
        f"package: {pkg}",
        "",
    ]
    if primary:
        aabb = primary["aabb"]
        lines += [
            f"OBJECT ({primary['label']})  [{primary.get('n_points')} pts]",
            f"  raw AABB:  {aabb.get('summary_raw', aabb['summary'])}",
            f"  @ 0.25 in: "
            f"{aabb['inches_0_25']['length']:.2f} × "
            f"{aabb['inches_0_25']['width']:.2f} × "
            f"{aabb['inches_0_25']['height']:.2f} in   (L × W × H)",
            f"  crate +{aabb['crate_inches_0_25']['padding']:.1f}\" /side: "
            f"{aabb['crate_summary']}",
            f"  OBB sorted: {primary['obb']['summary_sorted']}",
            "",
        ]
    if report.get("app_inches_0_25"):
        lines.append(f"APP measurement.json: {report['app_inches_0_25']['summary']}")
        lines.append("")
    for key in ("fused_tsdf", "fused_clean", "fused_cloud"):
        if key in report and isinstance(report[key], dict):
            f = report[key]
            lines.append(
                f"SCENE {key}: {f['aabb']['summary']}  "
                f"(usually room-scale — not object quote)"
            )
    txt_path = os.path.join(out_dir, "measurement.txt")
    with open(txt_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    report["dims_path"] = dims_path
    report["measurement_txt"] = txt_path
    return report


def inject_dims_hud(viewer_html: str, report: dict[str, Any]) -> None:
    """Append a dims line into an existing scan_view.html HUD if present."""
    if not os.path.isfile(viewer_html):
        return
    primary = report.get("object") or report.get("fused_tsdf") or report.get("fused_clean")
    if not primary:
        return
    summary = primary["aabb"].get("summary_raw") or primary["aabb"]["summary"]
    q = primary["aabb"]["inches_0_25"]
    line = (
        f"L×W×H @0.25″: {q['length']:.2f} × {q['width']:.2f} × {q['height']:.2f} in"
    )
    marker = "<!-- assetpipe-dims -->"
    block = (
        f'{marker}<div class="dim" style="margin-top:6px;color:#3fb950">'
        f"{line}<br><span style=\"color:#8b949e\">raw {summary}</span></div>"
    )
    html = open(viewer_html).read()
    if marker in html:
        # replace previous inject
        start = html.index(marker)
        end = html.find("</div>", start)
        if end != -1:
            html = html[:start] + block + html[end + len("</div>") :]
    else:
        html = html.replace(
            '<div id="links"></div>',
            f'<div id="links"></div>\n{block}',
            1,
        )
    with open(viewer_html, "w") as fh:
        fh.write(html)
