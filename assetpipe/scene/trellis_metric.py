"""Scale TRELLIS GLB to RGB-D metric size and export mm measurements + STL.

TRELLIS output is normalized / non-metric. Source of truth is object
``dims.json`` from the RGB-D fuse. We:

1. Read AABB inches from dims.json
2. Write ``dims_mm.json`` (inches × 25.4)
3. Uniformly scale the GLB so its longest AABB edge matches RGB-D (meters)
4. Export ``asset_trellis_mm.stl`` with vertices in millimeters (Onshape/Fusion)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional

MM_PER_INCH = 25.4
METERS_PER_INCH = 0.0254


def aabb_inches_from_dims(dims: dict[str, Any]) -> dict[str, float]:
    obj = dims.get("object") or dims
    aabb = obj.get("aabb") or {}
    inches = aabb.get("inches_0_25") or aabb.get("raw_inches")
    if isinstance(inches, dict) and all(
        k in inches for k in ("length", "width", "height")
    ):
        return {k: float(inches[k]) for k in ("length", "width", "height")}
    if all(k in obj for k in ("rawLengthInches", "rawWidthInches", "rawHeightInches")):
        return {
            "length": float(obj["rawLengthInches"]),
            "width": float(obj["rawWidthInches"]),
            "height": float(obj["rawHeightInches"]),
        }
    raise ValueError("dims.json missing AABB inches (aabb.inches_0_25 / raw_inches)")


def dims_to_mm(inches: dict[str, float], *, padding_inches: float = 2.0) -> dict[str, Any]:
    mm = {k: round(v * MM_PER_INCH, 2) for k, v in inches.items()}
    pad = padding_inches * MM_PER_INCH
    crate = {
        "length": round(mm["length"] + 2 * pad, 2),
        "width": round(mm["width"] + 2 * pad, 2),
        "height": round(mm["height"] + 2 * pad, 2),
        "padding_mm": round(pad, 2),
    }
    return {
        "units": "mm",
        "source": "rgbd_dims_inches",
        "inches": {k: round(v, 4) for k, v in inches.items()},
        "mm": mm,
        "summary_mm": f"{mm['length']:.1f} × {mm['width']:.1f} × {mm['height']:.1f} mm",
        "summary_inches": (
            f"{inches['length']:.2f} × {inches['width']:.2f} × {inches['height']:.2f} in"
        ),
        "crate_mm": crate,
        "crate_summary_mm": (
            f"{crate['length']:.1f} × {crate['width']:.1f} × {crate['height']:.1f} mm"
        ),
        "note": "Scale from RGB-D dims.json — not from raw TRELLIS vertices",
    }


def _load_mesh(glb_path: Path):
    import trimesh

    loaded = trimesh.load(str(glb_path), force="scene")
    if isinstance(loaded, trimesh.Scene):
        geoms = list(loaded.geometry.values())
        if not geoms:
            raise ValueError(f"no geometry in {glb_path}")
        mesh = trimesh.util.concatenate(geoms) if len(geoms) > 1 else geoms[0]
    else:
        mesh = loaded
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"expected Trimesh, got {type(mesh)}")
    return mesh


def mesh_aabb_extents(mesh) -> tuple[float, float, float]:
    bounds = mesh.bounds
    ext = bounds[1] - bounds[0]
    return float(ext[0]), float(ext[1]), float(ext[2])


def apply_rgbd_scale_to_glb(
    glb_path: str | Path,
    dims: dict[str, Any] | str | Path,
    *,
    out_dir: Optional[str | Path] = None,
    keep_raw: bool = True,
) -> dict[str, Any]:
    """Scale TRELLIS GLB to RGB-D size; write dims_mm.json + metric STL.

    Returns paths and scale metadata.
    """
    glb_path = Path(glb_path)
    if isinstance(dims, (str, Path)):
        dims = json.loads(Path(dims).read_text())
    assert isinstance(dims, dict)

    out_dir = Path(out_dir) if out_dir else glb_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    inches = aabb_inches_from_dims(dims)
    dims_mm = dims_to_mm(inches)
    (out_dir / "dims_mm.json").write_text(json.dumps(dims_mm, indent=2) + "\n")
    # Keep a copy of source dims next to the asset when not already there.
    dims_out = out_dir / "dims.json"
    if not dims_out.is_file():
        dims_out.write_text(json.dumps(dims, indent=2) + "\n")

    raw_glb = out_dir / "asset_trellis_raw.glb"
    scaled_glb = out_dir / "asset_trellis.glb"
    if keep_raw:
        if glb_path.resolve() != raw_glb.resolve():
            if not raw_glb.is_file() or glb_path.resolve() != scaled_glb.resolve():
                shutil.copy2(glb_path, raw_glb)
        src_for_scale = raw_glb if raw_glb.is_file() else glb_path
    else:
        src_for_scale = glb_path

    mesh = _load_mesh(src_for_scale)
    ex, ey, ez = mesh_aabb_extents(mesh)
    longest_mesh = max(ex, ey, ez)
    if longest_mesh <= 1e-9:
        raise ValueError("TRELLIS mesh has zero AABB extent")

    target_m = {
        "length": inches["length"] * METERS_PER_INCH,
        "width": inches["width"] * METERS_PER_INCH,
        "height": inches["height"] * METERS_PER_INCH,
    }
    target_longest_m = max(target_m.values())
    scale = target_longest_m / longest_mesh
    mesh.apply_scale(scale)

    # Center at origin for viewers / CAD import.
    mesh.vertices -= mesh.bounds.mean(axis=0)

    mesh.export(str(scaled_glb))

    # STL in millimeters for Onshape / Fusion / SolidWorks mesh import.
    stl_path = out_dir / "asset_trellis_mm.stl"
    mesh_mm = mesh.copy()
    mesh_mm.apply_scale(1000.0)  # m → mm
    mesh_mm.export(str(stl_path))

    sx, sy, sz = mesh_aabb_extents(mesh)
    return {
        "glb": str(scaled_glb),
        "raw_glb": str(raw_glb) if raw_glb.is_file() else None,
        "stl_mm": str(stl_path),
        "dims_mm": str(out_dir / "dims_mm.json"),
        "dims": str(dims_out),
        "scale_factor": scale,
        "mesh_extents_m_before": [ex, ey, ez],
        "mesh_extents_m_after": [sx, sy, sz],
        "target_extents_m": target_m,
        "dims_mm_summary": dims_mm["summary_mm"],
    }


def write_trellis_view_html(
    out_dir: str | Path,
    *,
    title: str,
    dims_mm: Optional[dict[str, Any]] = None,
) -> Path:
    """Bright Three.js viewer with optional mm HUD."""
    out_dir = Path(out_dir)
    if dims_mm is None:
        p = out_dir / "dims_mm.json"
        dims_mm = json.loads(p.read_text()) if p.is_file() else {}
    summary = dims_mm.get("summary_mm") or ""
    inches = dims_mm.get("summary_inches") or ""
    hud_extra = ""
    if summary:
        hud_extra = f"<br><b>{summary}</b>"
        if inches:
            hud_extra += f"<br><span style='color:#666'>{inches}</span>"

    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title}</title>
<style>
html,body{{margin:0;height:100%;background:#f4f5f7;color:#1a1a1a;font:13px/1.4 system-ui,sans-serif;overflow:hidden}}
#c{{position:fixed;inset:0}}
#hud{{position:fixed;top:10px;left:10px;z-index:2;background:rgba(255,255,255,.85);border:1px solid #ddd;padding:8px 12px;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
#hud b{{color:#2563eb}}
</style></head><body>
<div id="hud"><b>asset_trellis.glb</b> · {title}<br>drag orbit · scroll zoom{hud_extra}</div>
<div id="c"></div>
<script type="importmap">
{{"imports":{{
  "three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
  "three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"
}}}}
</script>
<script type="module">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
import {{ GLTFLoader }} from 'three/addons/loaders/GLTFLoader.js';
const root=document.getElementById('c');
const renderer=new THREE.WebGLRenderer({{antialias:true}});
renderer.setPixelRatio(devicePixelRatio); renderer.setSize(innerWidth,innerHeight);
renderer.outputColorSpace=THREE.SRGBColorSpace;
renderer.toneMapping=THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure=1.15;
root.appendChild(renderer.domElement);
const scene=new THREE.Scene(); scene.background=new THREE.Color(0xf4f5f7);
const camera=new THREE.PerspectiveCamera(45,innerWidth/innerHeight,0.01,100);
camera.position.set(1.8,1.2,2.2);
const controls=new OrbitControls(camera,renderer.domElement); controls.target.set(0,0.4,0);
scene.add(new THREE.HemisphereLight(0xffffff, 0xd8dce3, 0.95));
scene.add(new THREE.AmbientLight(0xffffff, 0.45));
const key=new THREE.DirectionalLight(0xffffff, 1.35); key.position.set(2.5, 4.5, 2.2); scene.add(key);
const fill=new THREE.DirectionalLight(0xe8eef8, 0.7); fill.position.set(-3, 2, -1); scene.add(fill);
const rim=new THREE.DirectionalLight(0xffffff, 0.55); rim.position.set(-1.5, 3, 4); scene.add(rim);
const loader=new GLTFLoader();
loader.load('asset_trellis.glb', (g)=>{{
  const m=g.scene; scene.add(m);
  const box=new THREE.Box3().setFromObject(m);
  const size=box.getSize(new THREE.Vector3());
  const center=box.getCenter(new THREE.Vector3());
  m.position.sub(center);
  const max=Math.max(size.x,size.y,size.z)||1;
  camera.position.set(max*1.6, max*1.1, max*1.8);
  controls.target.set(0,0,0); controls.update();
}}, undefined, (e)=>alert('load failed: '+e));
addEventListener('resize',()=>{{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight)}});
(function loop(){{requestAnimationFrame(loop);controls.update();renderer.render(scene,camera)}})();
</script></body></html>
"""
    path = out_dir / "view.html"
    path.write_text(html)
    return path
