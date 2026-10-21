"""3D / VR timeline: lanes along x, time along z, magnitude along y.

Events of the chain reaction are spheres linked by cause→effect arrows;
simulated trajectories are drawn as lines in their lane.  A time scrubber
moves a "now" plane through the scene; the VR button enters WebXR.
"""
from __future__ import annotations

import json
import math
from html import escape
from typing import List, Optional

from ..model import Topic
from ..simulate import CascadeEvent, Trajectory

THREE_VERSION = "0.160.0"


def render_scene3d(topic: Topic, traj: Optional[Trajectory], events: List[CascadeEvent]) -> str:
    lanes: List[str] = []
    if traj is not None:
        lanes += [s for s in topic.state_ids if s in traj.series]
        def varies(vid: str) -> bool:
            vals = [x for x in traj.series[vid] if math.isfinite(x)]
            return bool(vals) and (max(vals) - min(vals)) > 1e-9 * max(abs(max(vals)), abs(min(vals)), 1e-300)
        lanes += [v.id for v in topic.variables if v.equation and v.id in traj.series and v.id not in lanes and varies(v.id)][:4]
    for e in events:
        if e.var not in lanes:
            lanes.append(e.var)
    labels = {vid: (topic.var(vid).label if topic.var(vid) else vid) for vid in lanes}
    data = {
        "title": topic.title,
        "lanes": [{"id": l, "label": labels[l]} for l in lanes],
        "trajectories": [],
        "events": [],
        "time_unit": traj.time_unit if traj else topic.cascade.time_unit,
        "cascade_unit": topic.cascade.time_unit,
    }
    if traj is not None:
        t0, t1 = traj.times[0], traj.times[-1]
        for l in lanes:
            if l not in traj.series:
                continue
            vals = traj.series[l]
            finite = [v for v in vals if math.isfinite(v)]
            lo, hi = (min(finite), max(finite)) if finite else (0, 1)
            if hi - lo <= 1e-6 * max(abs(lo), abs(hi), 1e-300):
                mid = (lo + hi) / 2
                pad = abs(mid) * 0.05 if mid != 0 else 1.0
                lo, hi = mid - pad, mid + pad
            rng = (hi - lo) or 1.0
            pts = [[(t - t0) / ((t1 - t0) or 1.0), (v - lo) / rng if math.isfinite(v) else 0.0]
                   for t, v in zip(traj.times, vals)]
            step = max(1, len(pts) // 400)
            data["trajectories"].append({"lane": l, "label": labels[l], "min": lo, "max": hi, "points": pts[::step]})
        data["fired"] = [{"label": f.event.title, "t": (f.t - t0) / ((t1 - t0) or 1.0), "t_abs": f.t} for f in traj.fired]
        data["t_span"] = [t0, t1]
    if events:
        tmax = max(e.t for e in events) or 1.0
        dmax = max(abs(e.delta) for e in events) or 1.0
        for e in events:
            mech = e.relation.mechanism if e.relation and e.relation.mechanism else ""
            data["events"].append({"i": e.index, "lane": e.var, "label": labels.get(e.var, e.var),
                                   "t": e.t / tmax, "t_abs": e.t, "delta": e.delta, "size": abs(e.delta) / dmax,
                                   "cause": e.cause, "depth": e.depth, "mechanism": mech})
    payload = json.dumps(data)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(topic.title)}: 3D timeline</title>
<style>
  html,body{{margin:0;height:100%;background:#0d0d0d;color:#fff;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;overflow:hidden}}
  #hud{{position:fixed;left:16px;top:14px;right:16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;pointer-events:none;z-index:2}}
  #hud > *{{pointer-events:auto}}
  h1{{font-size:15px;margin:0;font-weight:600;background:rgba(26,26,25,.85);padding:6px 10px;border-radius:8px}}
  .panel{{background:rgba(26,26,25,.85);border:1px solid #2c2c2a;border-radius:8px;padding:6px 10px;font-size:13px;display:flex;gap:8px;align-items:center}}
  button{{background:#1a1a19;color:#fff;border:1px solid #383835;border-radius:6px;padding:4px 10px;cursor:pointer}}
  button:hover{{border-color:#3987e5}}
  input[type=range]{{width:220px;accent-color:#3987e5}}
  #tip{{position:fixed;display:none;background:rgba(26,26,25,.95);border:1px solid #383835;border-radius:8px;padding:8px 10px;font-size:12.5px;max-width:320px;pointer-events:none;z-index:3;line-height:1.4}}
  #legend{{position:fixed;left:16px;bottom:16px;max-width:40vw;background:rgba(26,26,25,.85);border-radius:8px;padding:8px 10px;font-size:12px;color:#c3c2b7;z-index:2;line-height:1.5}}
  .sw{{display:inline-block;width:10px;height:10px;border-radius:50%;margin:0 4px 0 8px;vertical-align:-1px}}
</style>
<script type="importmap">{{"imports":{{"three":"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/"}}}}</script>
</head><body>
<div id="hud"><h1>{escape(topic.title)} — timeline in 3D</h1>
  <div class="panel"><button id="play">▶ play</button><label>time <input id="time" type="range" min="0" max="1000" value="1000"></label><span id="tval"></span></div>
  <div class="panel"><button id="reset">reset view</button><span style="color:#898781">drag: orbit · wheel: zoom · hover: details</span></div>
</div>
<div id="legend">x: variable lanes · z (depth): time → · y: value / size of change<span class="sw" style="background:#3987e5"></span>increase<span class="sw" style="background:#e66767"></span>decrease<span class="sw" style="background:#eb6834;border-radius:2px"></span>event</div>
<div id="tip"></div>
<script id="data" type="application/json">{payload}</script>
<script type="module">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
import {{ VRButton }} from 'three/addons/webxr/VRButton.js';

const D = JSON.parse(document.getElementById('data').textContent);
const LANE = 1.4, DEPTH = 10, HEIGHT = 2.2;
const renderer = new THREE.WebGLRenderer({{antialias:true}});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); renderer.setSize(innerWidth, innerHeight);
renderer.xr.enabled = true; document.body.appendChild(renderer.domElement);
document.body.appendChild(VRButton.createButton(renderer));
const scene = new THREE.Scene(); scene.background = new THREE.Color(0x0d0d0d);
const camera = new THREE.PerspectiveCamera(50, innerWidth/innerHeight, 0.05, 200);
const world = new THREE.Group(); scene.add(world);
const n = Math.max(1, D.lanes.length), width = (n - 1) * LANE;
world.position.set(-width/2, 0, -DEPTH/2);
scene.add(new THREE.HemisphereLight(0xffffff, 0x222222, 1.1));
const dl = new THREE.DirectionalLight(0xffffff, 0.8); dl.position.set(3, 6, 2); scene.add(dl);
const controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true;
function resetView(){{ camera.position.set(width*0.9 + 4, 3.2, DEPTH*0.9 + 3); controls.target.set(0, 0.6, 0); controls.update(); }}
resetView();

// floor grid + lane rails
const grid = new THREE.GridHelper(Math.max(width, DEPTH) + 2, 20, 0x2c2c2a, 0x2c2c2a); grid.position.set(width/2, 0, DEPTH/2); world.add(grid);
function label(text, pos, size = 0.26, color = '#c3c2b7') {{
  const c = document.createElement('canvas'); const ctx = c.getContext('2d'); ctx.font = '600 44px system-ui'; const w = Math.ceil(ctx.measureText(text).width) + 24; c.width = w; c.height = 64;
  ctx.font = '600 44px system-ui'; ctx.fillStyle = color; ctx.textBaseline = 'middle'; ctx.fillText(text, 12, 32);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({{map: new THREE.CanvasTexture(c), transparent:true, depthTest:false}}));
  sp.scale.set(size * w / 64, size, 1); sp.position.copy(pos); return sp;
}}
D.lanes.forEach((l, i) => {{
  const x = i * LANE;
  const rail = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(x, 0.002, 0), new THREE.Vector3(x, 0.002, DEPTH)]), new THREE.LineBasicMaterial({{color: 0x383835}}));
  world.add(rail); world.add(label(l.label, new THREE.Vector3(x, 0.25, -0.5), 0.3, '#ffffff'));
}});
world.add(label('time →', new THREE.Vector3(width + 0.9, 0.2, DEPTH/2), 0.3, '#898781'));
const laneX = Object.fromEntries(D.lanes.map((l, i) => [l.id, i * LANE]));

// trajectories
const timed = [];  // objects with .userData.t in [0,1] to fade past "now"
D.trajectories.forEach((tr, k) => {{
  const pts = tr.points.map(p => new THREE.Vector3(laneX[tr.lane], 0.15 + p[1] * HEIGHT, p[0] * DEPTH));
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({{color: 0x3987e5, transparent:true}}));
  line.userData = {{kind:'traj', lane: tr.label, min: tr.min, max: tr.max, pts}}; world.add(line);
  world.add(label(tr.max.toPrecision(3), new THREE.Vector3(laneX[tr.lane] + 0.25, 0.15 + HEIGHT, 0), 0.18));
  world.add(label(tr.min.toPrecision(3), new THREE.Vector3(laneX[tr.lane] + 0.25, 0.15, 0), 0.18));
}});
(D.fired || []).forEach(f => {{
  const g = new THREE.Mesh(new THREE.PlaneGeometry(width + 1, HEIGHT + 0.3), new THREE.MeshBasicMaterial({{color: 0xeb6834, transparent:true, opacity:0.12, side: THREE.DoubleSide, depthWrite:false}}));
  g.position.set(width/2, 0.15 + HEIGHT/2, f.t * DEPTH); g.userData = {{kind:'fired', t:f.t, label: f.label, t_abs: f.t_abs}}; world.add(g); timed.push(g);
  world.add(label(f.label, new THREE.Vector3(width + 0.6, HEIGHT + 0.5, f.t * DEPTH), 0.22, '#eb6834'));
}});
// cascade events
const evObjs = [];
D.events.forEach(e => {{
  const r = 0.07 + 0.22 * e.size;
  const color = e.delta > 0 ? 0x3987e5 : e.delta < 0 ? 0xe66767 : 0x898781;
  const geo = e.depth === 0 ? new THREE.OctahedronGeometry(r * 1.4) : new THREE.SphereGeometry(r, 24, 16);
  const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({{color, transparent:true, roughness:0.4}}));
  m.position.set(laneX[e.lane], 0.3 + Math.abs(e.delta) / Math.max(...D.events.map(x => Math.abs(x.delta)), 1e-9) * HEIGHT * 0.8, e.t * DEPTH);
  m.userData = {{kind:'event', ...e}}; world.add(m); evObjs.push(m); timed.push(m);
}});
D.events.forEach(e => {{
  if (e.cause == null) return;
  const a = evObjs[e.cause].position, b = evObjs[e.i].position;
  const dir = new THREE.Vector3().subVectors(b, a); const len = dir.length();
  const arrow = new THREE.ArrowHelper(dir.clone().normalize(), a, len - 0.1, 0x898781, 0.18, 0.09);
  arrow.userData = {{kind:'arrow', t: e.t}}; arrow.traverse(o => {{ if (o.material) {{ o.material.transparent = true; }} }}); world.add(arrow); timed.push(arrow);
}});
// "now" plane
const now = new THREE.Mesh(new THREE.PlaneGeometry(width + 1.2, HEIGHT + 0.6), new THREE.MeshBasicMaterial({{color: 0xffffff, transparent:true, opacity:0.08, side: THREE.DoubleSide, depthWrite:false}}));
now.position.set(width/2, 0.15 + HEIGHT/2, DEPTH); world.add(now);
const slider = document.getElementById('time'), tval = document.getElementById('tval'), playBtn = document.getElementById('play');
let playing = false;
function setTime(f) {{
  now.position.z = f * DEPTH;
  timed.forEach(o => {{ const vis = (o.userData.t ?? 0) <= f + 1e-6; o.traverse(c => {{ if (c.material) c.material.opacity = vis ? (c.userData.kind === 'fired' ? 0.12 : 1) : 0.08; }}); }});
  if (D.t_span) tval.textContent = (D.t_span[0] + f * (D.t_span[1] - D.t_span[0])).toPrecision(3) + ' ' + D.time_unit;
  else if (D.events.length) tval.textContent = (f * Math.max(...D.events.map(e => e.t_abs))).toPrecision(3) + ' ' + D.cascade_unit;
}}
slider.addEventListener('input', () => setTime(slider.value / 1000));
playBtn.addEventListener('click', () => {{ playing = !playing; playBtn.textContent = playing ? '⏸ pause' : '▶ play'; if (playing && slider.value >= 1000) slider.value = 0; }});
document.getElementById('reset').addEventListener('click', resetView);
setTime(1);
// hover tooltip
const ray = new THREE.Raycaster(); const mouse = new THREE.Vector2(); const tip = document.getElementById('tip');
addEventListener('pointermove', ev => {{
  mouse.set(ev.clientX / innerWidth * 2 - 1, -(ev.clientY / innerHeight) * 2 + 1); ray.setFromCamera(mouse, camera);
  const hit = ray.intersectObjects(evObjs, false)[0];
  if (hit) {{ const e = hit.object.userData; tip.style.display = 'block'; tip.style.left = ev.clientX + 14 + 'px'; tip.style.top = ev.clientY + 14 + 'px';
    const cause = e.cause != null ? D.events[e.cause] : null;
    tip.innerHTML = `<b>${{e.label}}</b> ${{(e.delta*100).toFixed(1)}}% at t = ${{e.t_abs.toPrecision(3)}} ${{D.cascade_unit}}<br>` + (cause ? `because <b>${{cause.label}}</b> changed ${{(cause.delta*100).toFixed(1)}}%` + (e.mechanism ? `<br><i>${{e.mechanism}}</i>` : '') : 'the initial shock');
  }} else tip.style.display = 'none';
}});
addEventListener('resize', () => {{ camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight); }});
renderer.xr.addEventListener('sessionstart', () => {{ scene.scale.setScalar(0.35); scene.position.set(0, 0.8, -2.5); }});
renderer.xr.addEventListener('sessionend', () => {{ scene.scale.setScalar(1); scene.position.set(0, 0, 0); }});
renderer.setAnimationLoop(() => {{ if (playing) {{ slider.value = Math.min(1000, +slider.value + 2); setTime(slider.value / 1000); if (slider.value >= 1000) {{ playing = false; playBtn.textContent = '▶ play'; }} }} controls.update(); renderer.render(scene, camera); }});
</script></body></html>"""
