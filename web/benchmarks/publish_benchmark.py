#!/usr/bin/env python3
"""Publish a side-by-side reconstruction benchmark to the dashboard's blob store.

One page, one subject (the sofa), every method that produced geometry from it —
so photo vs video and photogrammetry vs generative vs RGB-D can be compared by
looking rather than by reading logs.

Two deliberate choices:

* ALBEDO (unlit) is the DEFAULT shading. A lit material multiplies vertex
  colour by a lighting term, which is what made earlier meshes look dark and
  "missing colour". Unlit shows the captured RGB exactly as reconstructed;
  the Lit toggle is there when you want to read shape instead of colour.
* Each viewer loads ON CLICK. Six meshes on one page is tens of megabytes
  through a serverless proxy otherwise.
"""
import json
import sys
import urllib.parse
from pathlib import Path

REPO = Path("/home/jaeahn-jammy/3dasset")
sys.path.insert(0, str(REPO))
from assetpipe.integrations.blob import VercelBlobUploader
import os

_raw = json.loads(Path(sys.argv[1]).read_text())
if isinstance(_raw, dict):
    SPEC = _raw["cards"]
    DEST = _raw["dest"]
    TITLE = _raw["title"]
    LEDE = _raw["lede"]
else:                       # legacy list spec = the sofa page
    SPEC = _raw
    DEST = "benchmark/sofa_20260805"
    TITLE = "Sofa 2026-08-05 — reconstruction benchmark"
    LEDE = ("One subject, one day, every pipeline that produced geometry from it. The "
            "same tan leather sofa was captured twice — 31 still photos and a 356-frame video — "
            "and put through RGB-D fusion, generative reconstruction and photogrammetry.")
SITE = os.environ.get("DASHBOARD_SITE", "").rstrip("/")
TOK = os.environ["BLOB_READ_WRITE_TOKEN"]
up = VercelBlobUploader(token=TOK, prefix="dashboard", access="private")

import json as _json
sys.path.insert(0, "/home/jaeahn-jammy/3dasset")
from tools.splat_viewer_html import splat_viewer_html

cards = []
for item in SPEC:
    if item.get("splat"):
        # A .splat needs its own WebGL renderer, so the panel embeds the
        # standalone viewer in an iframe instead of the shared three.js canvas.
        sp = Path(item["splat"])
        if not sp.is_file():
            print(f"  SKIP {item['id']}: {sp} missing")
            continue
        surl = up.upload(str(sp), f"{DEST}/{sp.name}")
        sproxy = "/api/download?" + urllib.parse.urlencode({"url": surl, "name": sp.name})
        radius = None
        meta = sp.with_suffix(sp.suffix + ".meta.json")
        if meta.is_file():
            try: radius = float(_json.loads(meta.read_text())["radius_m"])
            except Exception: pass
        html_doc = splat_viewer_html(sproxy, item["title"], radius_m=radius)
        html_doc = html_doc.replace(f"<b>{sproxy}</b>", f"<b>{sp.name}</b>")
        vf = sp.with_name(f"{item['id']}_view.html")
        vf.write_text(html_doc)
        vurl = up.upload(str(vf), f"{DEST}/{vf.name}")
        vproxy = "/api/download?" + urllib.parse.urlencode(
            {"inline": "1", "name": vf.name, "url": vurl + "?cache=0"})
        item = {**item, "iframe": vproxy, "mb": round(sp.stat().st_size / 1e6, 1)}
        cards.append(item)
        print(f"  uploaded {item['id']:<22} {item['mb']:>6.1f} MB (splat+viewer)")
        continue
    glb = Path(item["glb"])
    if not glb.is_file():
        print(f"  SKIP {item['id']}: {glb} missing")
        continue
    url = up.upload(str(glb), f"{DEST}/{glb.name}")
    proxy = "/api/download?" + urllib.parse.urlencode({"url": url, "name": glb.name})
    item = {**item, "src": proxy, "mb": round(glb.stat().st_size / 1e6, 1)}
    cards.append(item)
    print(f"  uploaded {item['id']:<22} {item['mb']:>6.1f} MB")

ROWS = "".join(
    f"""<tr>
  <td><b>{c['title']}</b><div class=sub>{c['method']}</div></td>
  <td>{c['input']}</td><td class=num>{c['views']}</td><td class=num>{c['time']}</td>
  <td>{c['scale']}</td><td class=num>{c['mb']} MB</td><td class=note>{c['note']}</td>
</tr>""" for c in cards)

PANELS = "".join(
    f"""<div class=panel>
  <h3>{c['title']} <span class=tag>{c['method']}</span></h3>
  <div class=meta>{c['views']} views · {c['scale']} · {c['mb']} MB
    <span class=mread id="read-{c['id']}"></span></div>
  <div class=stage id="stage-{c['id']}">
    <button class=load data-id="{c['id']}" data-src="{c.get('src','')}" data-iframe="{c.get('iframe','')}">▶ Load 3D ({c['mb']} MB)</button>
  </div>
</div>""" for c in cards)

HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>__TITLE__</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0b0b0d;--fg:#e9e9ec;--mut:#9aa0aa;--line:#24252b;--card:#141519}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.55 ui-sans-serif,system-ui,-apple-system,sans-serif}
.wrap{max-width:1500px;margin:0 auto;padding:24px 18px 60px}
h1{font-size:22px;margin:0 0 4px} .lede{color:var(--mut);margin:0 0 20px;max-width:80ch}
table{width:100%;border-collapse:collapse;margin:0 0 26px;font-size:13px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.note{color:var(--mut)} .sub{color:var(--mut);font-size:12px}
.grid{display:grid;grid-template-columns:1fr;gap:22px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.panel h3{margin:0 0 2px;font-size:15px}
.tag{font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:99px;padding:1px 8px;margin-left:6px}
.meta{color:var(--mut);font-size:12px;margin-bottom:9px}
.mread{margin-left:10px}
.mchip{display:inline-flex;align-items:center;gap:6px;background:#2a1516;border:1px solid #5c2320;color:#ff6b5e;border-radius:99px;padding:2px 9px;margin:0 4px 2px 0;font-weight:600}
.mchip button{background:none;border:0;color:#ff9c8a;cursor:pointer;font:inherit;padding:0 2px}
.mchip button:hover{color:#fff}
.stage{position:relative;height:78vh;min-height:540px;border-radius:9px;overflow:hidden;background:#08080a;
  display:flex;align-items:center;justify-content:center}
.stage canvas{display:block}
button.load{background:#1d1f26;color:var(--fg);border:1px solid #2e3038;border-radius:8px;
  padding:10px 16px;cursor:pointer;font:inherit}
button.load:hover{background:#262932}
.toolbar{display:flex;gap:10px;align-items:center;margin:0 0 16px;flex-wrap:wrap}
.toolbar label{color:var(--mut);font-size:13px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{background:#141519;color:var(--mut);border:0;padding:7px 14px;cursor:pointer;font:inherit}
.seg button.on{background:#2b6cb0;color:#fff}
.err{color:#ff9c8a;font-size:12px;padding:8px}
.foot{color:var(--mut);font-size:12px;margin-top:26px;border-top:1px solid var(--line);padding-top:14px;max-width:90ch}
</style></head><body><div class=wrap>
<h1>__TITLE__</h1>
<p class=lede>__LEDE__</p>

<div class=toolbar>
  <label>Shading</label>
  <div class=seg id=shade>
    <button data-mode="albedo" class=on>Color</button>
    <button data-mode="lit">Shape</button>
  </div>
  <label style="margin-left:14px">Measure</label>
  <div class=seg id=measure>
    <button data-m="off" class=on>Off</button>
    <button data-m="on">📏 On — click 2 points</button>
  </div>
  <label style="margin-left:8px">Drag to spin · scroll to zoom · right-drag to move · with Measure on, drag a red dot to adjust</label>
</div>

<table>
<thead><tr><th>View</th><th>Input</th><th class=num>Views</th><th class=num>Time</th>
<th>Scale</th><th class=num>Size</th><th>Notes</th></tr></thead>
<tbody>__ROWS__</tbody></table>

<div class=grid>__PANELS__</div>

<p class=foot>Sizes are real-world unless a view says otherwise. Web copies are lightened for speed — downloads on the asset page carry full detail.</p>
</div>

<script type="importmap">
{"imports":{
 "three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
 "three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"
}}
</script>
<script type="module">
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
/* TrackballControls, not OrbitControls. Orbit keeps a fixed world "up" and
   clamps the polar angle, so dragging past the top or bottom pole stops dead —
   that is the "gets stuck" behaviour. Trackball has no up-vector and no polar
   clamp: free roll, pitch and yaw, tumbling continuously in any direction.
   It also still exposes .target, which ArcballControls does not. */
import { TrackballControls } from 'three/addons/controls/TrackballControls.js';

let MODE = 'albedo';
let MEASURE = false;
const scenes = [];
const IN = 0.0254;   // metres per inch — every mesh on this page is metric

/* Colour arrives in one of TWO forms and both must survive the material swap:
   - COLOR_0 vertex colours (Meshroom, the RGB-D fuse)
   - a baseColorTexture + TEXCOORD_0 (TRELLIS)
   Building a fresh material from the geometry alone silently discarded the
   texture, which is why TRELLIS rendered flat grey. Carry the original map
   across, and only fall back to a neutral tint when there is genuinely
   no colour of either kind. */
function makeMaterial(mesh, mode){
  const geom = mesh.geometry;
  if(!mesh.userData.origMat) mesh.userData.origMat = mesh.material;
  const orig = mesh.userData.origMat;
  const map = (orig && orig.map) ? orig.map : null;
  const hasCol = !!geom.attributes.color;
  const base = {
    side: THREE.DoubleSide,
    vertexColors: hasCol,
    map: map,
    // White base so a texture/vertex colour is shown unmodified; grey only
    // when the mesh carries no colour at all.
    color: (hasCol || map) ? 0xffffff : 0xb9b9c2,
  };
  if(map && map.colorSpace !== THREE.SRGBColorSpace) map.colorSpace = THREE.SRGBColorSpace;
  if(mode === 'albedo'){
    // Unlit: colour straight through, no lighting term to darken it.
    return new THREE.MeshBasicMaterial(base);
  }
  return new THREE.MeshStandardMaterial({...base, roughness:.95, metalness:0});
}

function mount(stage, src){
  stage.innerHTML = '';
  const renderer = new THREE.WebGLRenderer({antialias:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));
  const w = stage.clientWidth, h = stage.clientHeight;
  renderer.setSize(w,h); stage.appendChild(renderer.domElement);
  const scene = new THREE.Scene(); scene.background = new THREE.Color(0x08080a);
  const cam = new THREE.PerspectiveCamera(50, w/h, .01, 5000);
  scene.add(new THREE.HemisphereLight(0xffffff,0x33384a,2.2));
  const dl = new THREE.DirectionalLight(0xffffff,1.5); dl.position.set(3,5,4); scene.add(dl);
  const ctr = new TrackballControls(cam, renderer.domElement);
  ctr.rotateSpeed = 3.0;      // trackball rotation is slower per pixel than orbit
  ctr.zoomSpeed = 1.2;
  ctr.panSpeed = 0.8;
  ctr.staticMoving = false;   // glide instead of stopping the instant you release
  ctr.dynamicDampingFactor = 0.12;
  const rec = {renderer, scene, cam, ctr, meshes:[], pts:[], marks:[]};
  scenes.push(rec);

  /* Tape measure — multiple measurements, each one DRAGGABLE and deletable.
     Click two points to make a measurement. Afterwards either end can be
     grabbed and dragged along the surface: the line and the inches update as
     you move, so you can nudge to an exact corner instead of re-clicking.
     Trackball is disabled while a handle is held, or the drag would spin the
     model instead of moving the point. Meshes are in METRES, so the numbers
     are real. */
  const ray = new THREE.Raycaster();
  const readout = stage.parentElement.querySelector('.mread');
  const handles = [];        // draggable endpoint spheres
  let pending = null, pendingDot = null, dragging = null, mSeq = 0;

  function modelSize(){
    return rec.meshes.length
      ? new THREE.Box3().setFromObject(rec.meshes[0].parent||rec.meshes[0])
          .getSize(new THREE.Vector3()).length() : 1;
  }
  function makeDot(pt){
    const r = Math.max(modelSize()/500, 0.002);
    const dot = new THREE.Mesh(
      new THREE.SphereGeometry(r, 14, 12),
      new THREE.MeshBasicMaterial({color:0xff3b30, depthTest:false}));
    dot.renderOrder = 11; dot.position.copy(pt);
    scene.add(dot); return dot;
  }
  function surfaceHit(e){
    const r = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((e.clientX-r.left)/r.width)*2-1,
                                        -((e.clientY-r.top)/r.height)*2+1), cam);
    return ray.intersectObjects(rec.meshes, true)[0] || null;
  }
  function handleHit(e){
    if(!handles.length) return null;
    const r = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((e.clientX-r.left)/r.width)*2-1,
                                        -((e.clientY-r.top)/r.height)*2+1), cam);
    return ray.intersectObjects(handles, false)[0] || null;
  }
  function refresh(m){
    m.line.geometry.setFromPoints([m.a, m.b]);
    m.line.geometry.attributes.position.needsUpdate = true;
    const d = m.a.distanceTo(m.b);
    m.label.textContent = `${m.n}: ${(d/IN).toFixed(1)} in `;
    m.small.textContent = `(${d.toFixed(3)} m)`;
  }

  let downXY = null;
  renderer.domElement.addEventListener('pointerdown', e=>{
    downXY = [e.clientX, e.clientY];
    if(!MEASURE) return;
    const h = handleHit(e);
    if(h){                                   // grab an endpoint
      dragging = h.object.userData;
      ctr.enabled = false;                   // trackball must not steal the drag
      renderer.domElement.setPointerCapture?.(e.pointerId);
      renderer.domElement.style.cursor = 'grabbing';
    }
  });
  renderer.domElement.addEventListener('pointermove', e=>{
    if(dragging){
      const hit = surfaceHit(e);
      if(!hit) return;
      dragging.meas[dragging.end].copy(hit.point);
      dragging.dot.position.copy(hit.point);
      refresh(dragging.meas);
      return;
    }
    if(MEASURE) renderer.domElement.style.cursor = handleHit(e) ? 'grab' : 'crosshair';
    else renderer.domElement.style.cursor = '';
  });
  renderer.domElement.addEventListener('pointerup', e=>{
    if(dragging){                            // finished moving a point
      dragging = null; ctr.enabled = true;
      renderer.domElement.style.cursor = 'grab';
      downXY = null;
      return;
    }
    if(!MEASURE || !downXY) return;
    const moved = Math.hypot(e.clientX-downXY[0], e.clientY-downXY[1]);
    downXY = null;
    if(moved > 6) return;                    // that was a spin, not a click
    const hit = surfaceHit(e);
    if(!hit) return;
    if(!pending){
      pending = hit.point.clone();
      pendingDot = makeDot(pending);
      return;
    }
    const a = pending, b = hit.point.clone();
    const dotA = pendingDot, dotB = makeDot(b);
    pending = null; pendingDot = null;
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([a,b]),
      new THREE.LineBasicMaterial({color:0xff3b30, depthTest:false}));
    line.renderOrder = 11; scene.add(line);

    const chip = document.createElement('span');
    chip.className = 'mchip';
    const label = document.createElement('span');
    const small = document.createElement('small');
    chip.appendChild(label); chip.appendChild(small);
    const m = {a, b, dotA, dotB, line, chip, label, small, n: ++mSeq};
    dotA.userData = {meas:m, end:'a', dot:dotA};
    dotB.userData = {meas:m, end:'b', dot:dotB};
    handles.push(dotA, dotB);

    const del = document.createElement('button');
    del.textContent = '✕'; del.title = 'delete';
    del.addEventListener('click', ()=>{
      for(const o of [dotA, dotB, line]) scene.remove(o);
      for(const h of [dotA, dotB]){ const i = handles.indexOf(h); if(i>=0) handles.splice(i,1); }
      chip.remove();
    });
    chip.appendChild(del);
    if(readout) readout.appendChild(chip);
    refresh(m);
  });
  const note = document.createElement('div');
  note.className='err'; note.style.position='absolute'; note.style.bottom='4px'; note.style.left='8px';
  note.textContent='loading…'; stage.appendChild(note);

  new GLTFLoader().load(src, (gltf)=>{
    try{
      const obj = gltf.scene;
      obj.traverse(o=>{
        if(!o.isMesh) return;
        // No NORMAL attribute = black under any lit material. Compute if absent.
        if(!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
        o.material = makeMaterial(o, MODE);
        rec.meshes.push(o);
      });
      scene.add(obj);
      const box = new THREE.Box3().setFromObject(obj);
      const size = box.getSize(new THREE.Vector3()).length() || 1;
      const c = box.getCenter(new THREE.Vector3());
      cam.near = size/1000; cam.far = size*50;
      cam.position.set(c.x+size*.6, c.y+size*.4, c.z+size*.6);
      cam.updateProjectionMatrix(); ctr.target.copy(c); cam.lookAt(c); ctr.update();
      note.remove();
    }catch(e){ note.textContent = 'framing failed: '+e; }
  }, (ev)=>{ if(ev.total) note.textContent = `loading… ${Math.round(100*ev.loaded/ev.total)}%`; },
     (e)=>{ note.textContent = 'load failed: '+e; });

  ctr.handleResize();
  renderer.setAnimationLoop(()=>{ ctr.update(); renderer.render(scene,cam); });
  addEventListener('resize', ()=>{
    const w=stage.clientWidth,h=stage.clientHeight;
    cam.aspect=w/h; cam.updateProjectionMatrix(); renderer.setSize(w,h);
    // Trackball maps pointer motion through cached screen bounds; without this
    // the drag axis is wrong after any resize.
    ctr.handleResize();
  });
}

document.querySelectorAll('button.load').forEach(b=>{
  b.addEventListener('click', ()=>{
    if(b.dataset.iframe){
      const st = b.parentElement;
      st.innerHTML = '';
      const f = document.createElement('iframe');
      f.src = b.dataset.iframe; f.allow = 'fullscreen';
      f.style.cssText = 'width:100%;height:100%;border:0;display:block;background:#08080a';
      st.appendChild(f);
      return;   // splat panels: tumble inside the embedded viewer; no tape measure
    }
    mount(b.parentElement, b.dataset.src);
  });
});
document.querySelectorAll('#measure button').forEach(b=>{
  b.addEventListener('click', ()=>{
    document.querySelectorAll('#measure button').forEach(x=>x.classList.remove('on'));
    b.classList.add('on'); MEASURE = b.dataset.m === 'on';
  });
});
document.querySelectorAll('#shade button').forEach(b=>{
  b.addEventListener('click', ()=>{
    document.querySelectorAll('#shade button').forEach(x=>x.classList.remove('on'));
    b.classList.add('on'); MODE = b.dataset.mode;
    for(const r of scenes) for(const m of r.meshes) m.material = makeMaterial(m, MODE);
  });
});
</script></body></html>
"""

html = (HTML.replace("__TITLE__", TITLE).replace("__LEDE__", LEDE)
            .replace("__ROWS__", ROWS).replace("__PANELS__", PANELS))
out = Path(f"/tmp/claude-1000/-home-jaeahn-jammy-3dasset/e4ef1659-0b93-42bd-b9a5-a09d91009ab4/scratchpad/benchmark_{DEST.split(chr(47))[-1]}.html")
out.write_text(html)
url = up.upload(str(out), f"{DEST}/benchmark.html")
link = "/api/download?" + urllib.parse.urlencode({"inline":"1","name":"benchmark.html","url":url+"?cache=0"})
print(f"\n  cards: {len(cards)}")
print(f"\nOPEN:\n  {SITE}{link}")
