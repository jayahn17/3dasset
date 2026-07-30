#!/usr/bin/env python3
"""Browsable gallery for every benchmark output — splats, meshes, comparisons.

Scans bench_out/, writes a gallery under bench_out/gallery/, and serves the
repo over localhost so the big .ply/.glb assets stream straight into the
browser viewers (no upload, no size limit).

    python tools/bench_gallery.py            # build + serve on :8770
    python tools/bench_gallery.py --no-serve # just rebuild the HTML

Viewers: GaussianSplats3D for 3DGS .ply splats, three.js GLTFLoader/PLYLoader
for meshes. Both pull from jsDelivr, so the browser needs internet once.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import webbrowser

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(REPO, "bench_out")
GALLERY = os.path.join(BENCH, "gallery")

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#0b0d10;color:#e6edf3;
  font:14px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}
a{color:#58a6ff;text-decoration:none}
a:hover{text-decoration:underline}
header{padding:32px 28px 20px;border-bottom:1px solid #21262d;
  background:linear-gradient(180deg,#12161c,#0b0d10)}
h1{margin:0 0 6px;font-size:24px;letter-spacing:-.02em}
h2{font-size:17px;margin:34px 0 12px;letter-spacing:-.01em}
.sub{color:#8b949e;font-size:13px;max-width:70ch}
main{padding:0 28px 60px;max-width:1400px;margin:0 auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}
.card{background:#12161c;border:1px solid #21262d;border-radius:10px;
  padding:14px 16px 16px;transition:border-color .15s}
.card:hover{border-color:#30363d}
.card h3{margin:0 0 4px;font-size:15px;font-family:ui-monospace,monospace;color:#e6edf3}
.meta{color:#8b949e;font-size:12px;margin-bottom:10px}
.btns{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.btn{display:inline-block;padding:5px 11px;border-radius:6px;font-size:12px;
  background:#1f6feb;color:#fff;border:1px solid #388bfd}
.btn:hover{background:#2a7aff;text-decoration:none}
.btn.ghost{background:#21262d;border-color:#30363d;color:#c9d1d9}
.btn.ghost:hover{background:#2d333b}
.btn.hero{background:#8957e5;border-color:#a371f7}
img.sheet{width:100%;border-radius:8px;border:1px solid #21262d;margin-top:10px;
  display:block;background:#000}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
th,td{text-align:left;padding:7px 12px;border-bottom:1px solid #21262d}
th{color:#8b949e;font-weight:600;font-size:12px;text-transform:uppercase;
  letter-spacing:.04em}
td.num{font-family:ui-monospace,monospace}
.hi{color:#7ee787;font-weight:600}
.warn{color:#f0883e}
.note{background:#12161c;border-left:3px solid #8957e5;padding:12px 16px;
  border-radius:0 8px 8px 0;margin:14px 0;color:#c9d1d9;font-size:13px}
.wide{grid-column:1/-1}
"""

SPLAT_VIEWER = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>splat viewer</title>
<style>
html,body{margin:0;height:100%;background:#0b0d10;color:#e6edf3;overflow:hidden;
  font:13px/1.5 -apple-system,Segoe UI,sans-serif}
#c{position:fixed;inset:0}
#hud{position:fixed;top:12px;left:12px;z-index:5;background:rgba(18,22,28,.9);
  border:1px solid #30363d;border-radius:8px;padding:9px 13px;max-width:60vw}
#hud b{color:#58a6ff}#hud div{color:#8b949e;font-size:12px}
#err{color:#ff7b72;white-space:pre-wrap;font-size:12px}
a{color:#58a6ff}
</style>
<div id="hud"><b id="t">loading…</b><div>drag orbit · scroll zoom · right-drag pan</div>
<div id="err"></div><div><a href="/bench_out/gallery/">← gallery</a></div></div>
<div id="c"></div>
<script type="importmap">{"imports":{
"three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
"three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/",
"@mkkellogg/gaussian-splats-3d":"https://cdn.jsdelivr.net/npm/@mkkellogg/gaussian-splats-3d@0.4.6/build/gaussian-splats-3d.module.js"
}}</script>
<script type="module">
import * as THREE from 'three';
import * as GS from '@mkkellogg/gaussian-splats-3d';
const p = new URLSearchParams(location.search);
const file = p.get('f'), title = p.get('t') || file;
document.getElementById('t').textContent = title;
document.title = title;
function frameScene(viewer) {
  const mesh = (typeof viewer.getSplatMesh === 'function')
    ? viewer.getSplatMesh() : viewer.splatMesh;
  let center = new THREE.Vector3(0,0,0), radius = 2.5;
  if (mesh) {
    if (mesh.calculatedSceneCenter) center = mesh.calculatedSceneCenter.clone();
    if (mesh.maxSplatDistanceFromSceneCenter > 1e-3)
      radius = mesh.maxSplatDistanceFromSceneCenter;
    else if (mesh.boundingBox && !mesh.boundingBox.isEmpty()) {
      mesh.boundingBox.getCenter(center);
      radius = mesh.boundingBox.getSize(new THREE.Vector3()).length() * 0.5;
    }
  }
  radius = Math.max(radius, 0.35);
  if (!viewer.camera) return;
  viewer.camera.position.copy(center).add(new THREE.Vector3(radius*1.6, radius*0.95, radius*1.8));
  viewer.camera.near = Math.max(radius/200, 0.01);
  viewer.camera.far = Math.max(radius*40, 50);
  viewer.camera.updateProjectionMatrix();
  if (viewer.controls) { viewer.controls.target.copy(center); viewer.controls.update(); }
}
try {
  const viewer = new GS.Viewer({rootElement:document.getElementById('c'),
    sharedMemoryForWorkers:false, gpuAcceleratedSort:true,
    cameraUp:[0,1,0], initialCameraPosition:[2.5,1.6,3], initialCameraLookAt:[0,0,0]});
  await viewer.addSplatScene(new URL(file, location.origin).href,
    {showLoadingUI:true, progressiveLoad:false});
  viewer.start();
  requestAnimationFrame(() => { frameScene(viewer); setTimeout(() => frameScene(viewer), 250); });
} catch(e) {
  document.getElementById('err').textContent = 'Failed: ' + e;
  console.error(e);
}
</script>
"""

MESH_VIEWER = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>mesh viewer</title>
<style>
html,body{margin:0;height:100%;background:#0b0d10;color:#e6edf3;overflow:hidden;
  font:13px/1.5 -apple-system,Segoe UI,sans-serif}
#hud{position:fixed;top:12px;left:12px;z-index:5;background:rgba(18,22,28,.9);
  border:1px solid #30363d;border-radius:8px;padding:9px 13px;max-width:60vw}
#hud b{color:#58a6ff}#hud div{color:#8b949e;font-size:12px}
#err{color:#ff7b72;font-size:12px}a{color:#58a6ff}
</style>
<div id="hud"><b id="t">loading…</b><div id="info">drag orbit · scroll zoom</div>
<div id="err"></div><div><a href="/bench_out/gallery/">← gallery</a></div></div>
<script type="importmap">{"imports":{
"three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
"three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"
}}</script>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {PLYLoader} from 'three/addons/loaders/PLYLoader.js';
const p = new URLSearchParams(location.search);
const file = p.get('f'), title = p.get('t') || file;
document.getElementById('t').textContent = title; document.title = title;

const scene = new THREE.Scene(); scene.background = new THREE.Color(0x0b0d10);
const camera = new THREE.PerspectiveCamera(50, innerWidth/innerHeight, .01, 5000);
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
document.body.appendChild(renderer.domElement);
scene.add(new THREE.HemisphereLight(0xffffff, 0x334455, 2.2));
const dir = new THREE.DirectionalLight(0xffffff, 1.5); dir.position.set(2,4,3);
scene.add(dir);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

function frame(obj){
  const box = new THREE.Box3().setFromObject(obj);
  const size = box.getSize(new THREE.Vector3());
  const c = box.getCenter(new THREE.Vector3());
  obj.position.sub(c);
  const r = Math.max(size.x,size.y,size.z) || 1;
  camera.position.set(r*1.4, r*.9, r*1.4);
  camera.near = r/500; camera.far = r*100; camera.updateProjectionMatrix();
  controls.target.set(0,0,0); controls.update();
  document.getElementById('info').textContent =
    `extent ${size.x.toFixed(2)} x ${size.y.toFixed(2)} x ${size.z.toFixed(2)} m · drag orbit · scroll zoom`;
  scene.add(obj);
}
const onErr = e => {document.getElementById('err').textContent='Failed: '+e; console.error(e)};
if (file.endsWith('.ply')) {
  new PLYLoader().load(file, g => {
    g.computeVertexNormals();
    const hasFaces = g.index && g.index.count > 0;
    const m = hasFaces
      ? new THREE.Mesh(g, new THREE.MeshStandardMaterial({
          vertexColors: !!g.attributes.color, color: g.attributes.color?0xffffff:0xc9d1d9,
          roughness:.85, metalness:.05, side:THREE.DoubleSide}))
      : new THREE.Points(g, new THREE.PointsMaterial({
          size:.004, vertexColors: !!g.attributes.color, color:0xc9d1d9}));
    frame(m);
  }, undefined, onErr);
} else {
  new GLTFLoader().load(file, g => frame(g.scene), undefined, onErr);
}
addEventListener('resize', ()=>{camera.aspect=innerWidth/innerHeight;
  camera.updateProjectionMatrix(); renderer.setSize(innerWidth,innerHeight)});
(function loop(){requestAnimationFrame(loop); controls.update();
  renderer.render(scene,camera)})();
</script>
"""


def mb(path: str) -> str:
    try:
        return f"{os.path.getsize(path) / 1e6:.0f} MB"
    except OSError:
        return "—"


def rel(path: str) -> str:
    """Absolute URL path from the server root (repo root)."""
    return "/" + os.path.relpath(path, REPO).replace(os.sep, "/")


def _cameras() -> dict:
    f = os.path.join(BENCH, "view", "cameras.json")
    if os.path.isfile(f):
        with open(f) as fh:
            return json.load(fh)
    return {}


CAMS = _cameras()


def splat_link(path: str, title: str, cls: str = "btn") -> str:
    """Link a splat, preferring the .splat export + precomputed framing."""
    name = os.path.basename(path)
    cam = CAMS.get(name)
    q = f"f={rel(path)}&t={title}"
    if cam:
        q += (f"&pos={','.join(str(x) for x in cam['pos'])}"
              f"&look={','.join(str(x) for x in cam['look'])}")
    return f'<a class="{cls}" href="/bench_out/gallery/splat.html?{q}">{title}</a>'


def mesh_link(path: str, title: str, cls: str = "btn ghost") -> str:
    return (f'<a class="{cls}" href="/bench_out/gallery/mesh.html'
            f'?f={rel(path)}&t={title}">{title}</a>')


def build() -> str:
    os.makedirs(GALLERY, exist_ok=True)
    # splat.html carries the camera fix (OpenCV +y-down up-axis + explicit
    # framing); don't clobber it with the bundled fallback template.
    splat_html = os.path.join(GALLERY, "splat.html")
    if not (os.path.isfile(splat_html)
            and "cameraUp" in open(splat_html).read()):
        with open(splat_html, "w") as fh:
            fh.write(SPLAT_VIEWER)
    with open(os.path.join(GALLERY, "mesh.html"), "w") as fh:
        fh.write(MESH_VIEWER)

    sessions = sorted(
        d for d in os.listdir(BENCH)
        if d.startswith("CrateScan-") and os.path.isdir(os.path.join(BENCH, d))
    )

    cards = []
    for s in sessions:
        base = os.path.join(BENCH, s)
        row = {}
        bj = os.path.join(base, "ours_bench.json")
        if os.path.isfile(bj):
            with open(bj) as fh:
                row = json.load(fh)
        lwh = row.get("object_crop_lwh_in")
        dims = (" × ".join(f"{x:.1f}" for x in lwh) + " in") if lwh else "—"
        pts = (row.get("stages", {}).get("fuse", {}) or {}).get("points", "—")
        hulls = (row.get("stages", {}).get("sim_export", {}) or {}).get("hulls")

        sid = s.replace("CrateScan-", "")
        btns = []
        v5 = os.path.join(BENCH, "view", f"{sid}_5k.splat")
        if os.path.isfile(v5):
            btns.append(splat_link(v5, f"5k splat ({mb(v5)})"))
        v30 = os.path.join(BENCH, "view", "full_30k.splat")
        if sid == "1B38880A" and os.path.isfile(v30):
            btns.append(splat_link(v30, f"30k splat · 1M ({mb(v30)})", "btn hero"))
        for name, p in (
            ("scene mesh", os.path.join(base, "ours_tsdf", "scene_mesh.glb")),
            ("object mesh", os.path.join(base, "ours_tsdf", "scene_object_mesh.ply")),
            ("TRELLIS GLB", os.path.join(base, "ours_trellis", "asset_trellis.glb")),
        ):
            if os.path.isfile(p):
                btns.append(mesh_link(p, name))
        for name, p in (
            ("dims.json", os.path.join(base, "ours_tsdf", "dims.json")),
            ("sim (URDF/MJCF/USD)", os.path.join(base, "ours_sim")),
            ("photos →API", os.path.join(base, "photos")),
        ):
            if os.path.exists(p):
                btns.append(f'<a class="btn ghost" href="{rel(p)}">{name}</a>')

        sheet = os.path.join(base, "ours_3dgut", "renders", "contact_sheet.png")
        sheet_html = (f'<img class="sheet" src="{rel(sheet)}" loading="lazy" '
                      f'alt="GT vs render">' if os.path.isfile(sheet) else "")
        meta = (f"{row.get('n_frames','—')} frames · {pts:,} fused pts · "
                f"object {dims}" if isinstance(pts, int)
                else f"{row.get('n_frames','—')} frames · object {dims}")
        if hulls:
            meta += f" · {hulls} hulls"
        cards.append(
            f'<div class="card"><h3>{s.replace("CrateScan-","")}</h3>'
            f'<div class="meta">{meta}</div>'
            f'<div class="btns">{"".join(btns)}</div>{sheet_html}</div>'
        )

    # Hero: the 5k vs 30k comparison
    cmp_sheet = os.path.join(BENCH, "CrateScan-1B38880A", "splat_compare_trainviews",
                             "compare_sheet.png")
    hero = ""
    if os.path.isfile(cmp_sheet):
        p5 = os.path.join(BENCH, "view", "fast_30k_300k.splat")
        p30 = os.path.join(BENCH, "view", "full_30k.splat")
        hero = (
            '<h2>Quality tiers — 5k vs 30k (bottle session)</h2>'
            '<div class="card wide">'
            '<div class="btns">'
            + splat_link(p30, "open 30k · 960k gaussians", "btn hero")
            + splat_link(p5, "open 30k lite · 300k (fast)")
            + '</div>'
            '<table><tr><th></th><th>5k fast</th><th>30k precise</th></tr>'
            '<tr><td>gaussians</td><td class="num">144,502</td>'
            '<td class="num hi">1,000,000</td></tr>'
            '<tr><td>train loss</td><td class="num">0.08</td>'
            '<td class="num hi">0.03</td></tr>'
            '<tr><td>compute</td><td class="num">51 s</td><td class="num">21.4 min</td></tr>'
            '<tr><td>energy · cost</td><td class="num">5.6 Wh · 0.23¢</td>'
            '<td class="num">129 Wh · 5.2¢</td></tr></table>'
            f'<img class="sheet" src="{rel(cmp_sheet)}" alt="GT | 5k | 30k">'
            '<div class="note">Columns: <b>GT</b> (real photo) · <b>5k</b> · '
            '<b>30k</b>, rendered at identical capture poses. Naive PSNR ranks 5k '
            'higher — an artifact of sub-pixel pose misalignment rewarding blur, '
            'not a quality signal. Train loss and your eyes both favour 30k.</div>'
            '</div>'
        )

    lounge = os.path.join(BENCH, "lounge", "ours_tsdf")
    lounge_html = ""
    if os.path.isdir(lounge):
        lm = os.path.join(lounge, "scene_mesh.glb")
        lp = os.path.join(lounge, "scene.ply")
        btns = []
        if os.path.isfile(lm):
            btns.append(mesh_link(lm, "room mesh (GLB)", "btn"))
        if os.path.isfile(lp):
            btns.append(mesh_link(lp, f"room cloud ({mb(lp)})"))
        lounge_html = (
            '<h2>Scene benchmark</h2><div class="card wide">'
            '<h3>lounge — room shell</h3>'
            '<div class="meta">40 frames · 7,623,999 fused pts · '
            'the Marble / Teleport counterpart</div>'
            f'<div class="btns">{"".join(btns)}</div></div>'
        )

    html = f"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>3D asset benchmark — outputs</title>
<style>{CSS}</style>
<header>
<h1>RGB-D benchmark outputs</h1>
<div class="sub">Every artifact from the open-source pipelines: metric TSDF
fuses, 3DGUT splats, TRELLIS completions, and sim-ready bundles — from 7 iPad
RGB-D sessions plus one room scene. Click any button to open the model in an
interactive WebGL viewer.</div>
</header>
<main>
{hero}
<h2>Object sessions</h2>
<div class="grid">{"".join(cards)}</div>
{lounge_html}
<h2>Reports</h2>
<div class="card wide"><div class="btns">
<a class="btn ghost" href="/bench_out/RESULTS.md">RESULTS.md — metrics, power &amp; cost</a>
<a class="btn ghost" href="/docs/INDUSTRY_BENCHMARK.md">INDUSTRY_BENCHMARK.md — API landscape</a>
</div>
<div class="note">Cost per artifact: complete object asset <b>1.0¢</b>
(25.6 Wh) vs Kiri <b>$1.00</b> and Marble <b>$1.28</b> — measured at 224 W GPU
draw, $0.40/kWh.</div></div>
</main>
"""
    index = os.path.join(GALLERY, "index.html")
    with open(index, "w") as fh:
        fh.write(html)
    return index


def serve(port: int, open_browser: bool) -> None:
    os.chdir(REPO)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    socketserver.TCPServer.allow_reuse_address = True
    url = f"http://localhost:{port}/bench_out/gallery/"
    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        print(f"\n  Gallery → {url}\n  (Ctrl-C to stop)\n")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001
                pass
        httpd.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-serve", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    index = build()
    print(f"built {index}")
    if not args.no_serve:
        serve(args.port, not args.no_browser)


if __name__ == "__main__":
    main()
