"""Shared HTML splat viewer that auto-frames the loaded scene.

GaussianSplats3D defaults to lookAt(0,0,0). ARKit / COLMAP captures often
sit meters away, so without framing (or centering in ply_to_splat) the
canvas looks empty. This template:
  1. Prefer a centered .splat from ply_to_splat (origin at scene median).
  2. Bake camera pose from ``*.splat.meta.json`` radius when present.
  3. Use ArcballControls for free 360° tumble (no OrbitControls pole-lock).
"""

from __future__ import annotations

import json
from pathlib import Path


def _camera_from_radius(radius: float) -> tuple[list[float], list[float]]:
    r = max(float(radius), 0.35)
    pos = [r * 1.55, r * 0.9, r * 1.75]
    return pos, [0.0, 0.0, 0.0]


def splat_viewer_html(
    splat_file: str,
    title: str,
    *,
    radius_m: float | None = None,
) -> str:
    """Return a complete HTML document that loads ``splat_file`` (basename)."""
    cam_pos, cam_look = _camera_from_radius(radius_m or 2.0)
    r0 = max(float(radius_m or 2.0), 0.35)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>splat viewer — {title}</title>
<style>
  html,body{{margin:0;height:100%;background:#0b0d10;color:#e6edf3;
    font:13px/1.4 -apple-system,Segoe UI,sans-serif;overflow:hidden}}
  #c{{position:fixed;inset:0}}
  #hud{{position:fixed;top:10px;left:10px;z-index:2;background:rgba(20,24,30,.85);
    border:1px solid #30363d;border-radius:8px;padding:8px 12px;max-width:70vw}}
  #hud b{{color:#58a6ff}}
  #hud .dim{{color:#8b949e}}
  #err{{color:#ff7b72;white-space:pre-wrap}}
</style>
</head>
<body>
<div id="hud"><b>{splat_file}</b> · {title}
<div class="dim">drag tumble · scroll zoom · right-drag pan</div>
<div id="err"></div></div>
<div id="c"></div>
<script type="importmap">
{{
  "imports": {{
    "three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/",
    "@mkkellogg/gaussian-splats-3d": "https://cdn.jsdelivr.net/npm/@mkkellogg/gaussian-splats-3d@0.4.6/build/gaussian-splats-3d.module.js"
  }}
}}
</script>
<script type="module">
import * as THREE from 'three';
import {{ ArcballControls }} from 'three/addons/controls/ArcballControls.js';
import * as GaussianSplats3D from '@mkkellogg/gaussian-splats-3d';

const err = document.getElementById('err');
const url = new URL('{splat_file}', window.location.href).href;
// Baked from ply_to_splat meta (core radius). Do NOT trust the library's
// maxSplatDistanceFromSceneCenter — floaters inflate it and the camera
// ends up inside the cloud (blank view).
const BAKE_RADIUS = {r0:.6f};
const BAKE_CAM = {json.dumps(cam_pos)};
const BAKE_LOOK = {json.dumps(cam_look)};

try {{
  const viewer = new GaussianSplats3D.Viewer({{
    rootElement: document.getElementById('c'),
    sharedMemoryForWorkers: false,
    gpuAcceleratedSort: false,
    // We own ArcballControls so GS3D cannot yank the camera after load.
    // Arcball = free 360° tumble (no OrbitControls polar lock / stuck poles).
    useBuiltInControls: false,
    cameraUp: [0, 1, 0],
    initialCameraPosition: BAKE_CAM,
    initialCameraLookAt: BAKE_LOOK,
  }});
  window.__splatViewer = viewer;
  await viewer.addSplatScene(url, {{
    showLoadingUI: true,
    progressiveLoad: false,
    splatAlphaRemovalThreshold: 1,
  }});

  const cam = viewer.camera;
  cam.up.set(0, 1, 0);
  cam.position.fromArray(BAKE_CAM);
  cam.near = Math.max(BAKE_RADIUS / 200, 0.01);
  cam.far = Math.max(BAKE_RADIUS * 40, 50);
  cam.updateProjectionMatrix();

  // scene=null → gizmos not added to the stage; still hide for safety.
  const controls = new ArcballControls(cam, viewer.renderer.domElement, null);
  controls.target.fromArray(BAKE_LOOK);
  controls.enableAnimations = false;
  controls.enablePan = true;
  controls.enableZoom = true;
  controls.enableRotate = true;
  if (typeof controls.setGizmosVisible === 'function') {{
    controls.setGizmosVisible(false);
  }}
  controls.update();
  window.__splatControls = controls;

  const prevUpdate = viewer.update.bind(viewer);
  viewer.update = (...args) => {{
    controls.update();
    return prevUpdate(...args);
  }};

  viewer.start();
}} catch (e) {{
  err.textContent = 'Failed to load splat: ' + e +
    '\\nServe this folder over http (not file://).';
  console.error(e);
}}
</script>
</body>
</html>
"""


def write_splat_viewer(
    directory: Path,
    splat_file: str = "scene_gaussians.splat",
    title: str = "3DGRUT splat",
) -> Path:
    """Write ``splat_view.html`` + ``OPEN_ME.html`` beside the asset."""
    directory = Path(directory)
    radius = None
    meta_path = directory / f"{splat_file}.meta.json"
    if meta_path.is_file():
        try:
            radius = float(json.loads(meta_path.read_text()).get("radius_m") or 0) or None
        except Exception:
            radius = None
    html_path = directory / "splat_view.html"
    html_path.write_text(
        splat_viewer_html(splat_file, title, radius_m=radius)
    )
    (directory / "OPEN_ME.html").write_text(
        "<!doctype html><meta charset=\"utf-8\">\n"
        "<meta http-equiv=\"refresh\" content=\"0; url=splat_view.html\">\n"
        f"<title>{title}</title>\n"
        "<a href=\"splat_view.html\">Open splat viewer</a>\n"
    )
    return html_path
