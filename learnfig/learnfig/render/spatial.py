"""Spatial learning room (WebXR): the topic as a globe you turn with your hands.

Four patterns from agent-built XR "Earth" interfaces, mapped onto a learning topic:

  a globe you turn by hand        -> variables are cities on a concept globe; grab it to turn it
  tap a place, a 3D city rises    -> tap a variable: its tower rises and a card unfolds on the table
  data panels side by side        -> the 2D figures stand on an arc around the reader
  live feeds (ISS, quakes, ...)   -> optional public JSON feeds shown beside the model's value

On the table next to the globe, the first two states trace their path (the orbit, the
predator-prey loop); "unroll time" lifts that path into a helix with time as height.
Desktop: mouse.  Headset: Enter VR or Enter AR (passthrough); pinch or trigger to grab and tap.
Distances in the scene are metres, so the room is the same size on a desk and in a headset.
"""
from __future__ import annotations

import json
import math
from html import escape
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ..graph import Graph
from ..model import Topic
from ..simulate import Trajectory
from ..style import KIND_COLORS, KIND_LABELS
from ..units import parse_unit, pretty_unit

THREE_VERSION = "0.160.0"
MAX_PATH_POINTS = 600
MAX_SERIES_POINTS = 200


def resolve_live(topic: Topic, values: Dict[str, float], feeds) -> Tuple[List[dict], List[str]]:
    """Check live feeds against the model: https only, and a unit compatible with the compared variable."""
    out: List[dict] = []
    problems: List[str] = []
    for i, f in enumerate(feeds or []):
        if not isinstance(f, dict) or not f.get("url") or not f.get("field"):
            problems.append(f"live[{i}] needs a url and a field")
            continue
        url = str(f["url"])
        if not url.startswith("https://"):
            problems.append(f"live[{i}] {url!r}: a page in a headset can only fetch https feeds")
            continue
        unit = str(f.get("unit", "1"))
        try:
            fu = parse_unit(unit)
        except Exception as exc:  # noqa: BLE001 - report, do not crash the build
            problems.append(f"live[{i}] unit {unit!r}: {exc}")
            continue
        factor, model = 1.0, None
        if f.get("compare"):
            v = topic.var(str(f["compare"]))
            if v is None:
                problems.append(f"live[{i}] compares to unknown variable {f['compare']!r}")
                continue
            vu = parse_unit(v.unit)
            if not fu.same_dims(vu):
                problems.append(f"live[{i}] unit {unit} is not compatible with {v.id} ({v.unit})")
                continue
            factor = fu.factor / vu.factor
            mv = values.get(v.id)
            model = {"id": v.id, "label": v.label, "sym": v.sym,
                     "value": mv if mv is not None and math.isfinite(mv) else None, "unit": pretty_unit(v.unit)}
        out.append({"label": str(f.get("label") or f["field"]), "url": url, "field": str(f["field"]),
                    "unit": pretty_unit(unit), "factor": factor, "every": max(2.0, float(f.get("every", 5))),
                    "model": model, "source": str(f.get("source") or urlparse(url).netloc)})
    return out, problems


def _downsample(n: int, limit: int) -> List[int]:
    step = max(1, math.ceil(n / limit))
    idx = list(range(0, n, step))
    if idx and idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def _span(vals: List[float]) -> Tuple[float, float]:
    finite = [v for v in vals if math.isfinite(v)]
    if not finite:
        return 0.0, 1.0
    lo, hi = min(finite), max(finite)
    if hi - lo <= 1e-12 * max(abs(lo), abs(hi), 1e-300):
        pad = abs(lo) * 0.05 or 1.0
        lo, hi = lo - pad, hi + pad
    return lo, hi


def _path(topic: Topic, traj: Optional[Trajectory], opts: dict, problems: List[str]) -> Optional[dict]:
    if traj is None or not traj.times:
        return None
    states = [s for s in topic.state_ids if s in traj.series]
    a = b = None
    want = opts.get("path")
    if want:
        want = list(want) if isinstance(want, (list, tuple)) else [want]
        a, b = want[0], (want[1] if len(want) > 1 else None)
        bad = [w for w in (a, b) if w is not None and w not in traj.series]
        if bad:
            problems.append(f"path variable(s) {bad} are not simulated; using the first states instead")
            a = b = None
    if a is None:
        if len(states) >= 2:
            a, b = states[0], states[1]
        elif states:
            a, b = states[0], None
        else:
            return None
    idx = _downsample(len(traj.times), MAX_PATH_POINTS)
    t0, t1 = traj.times[0], traj.times[-1]
    ts = [(traj.times[i] - t0) / ((t1 - t0) or 1.0) for i in idx]
    ua = [traj.series[a][i] for i in idx]
    ub = [traj.series[b][i] for i in idx] if b else [2 * t - 1 for t in ts]
    va, vb = topic.var(a), topic.var(b) if b else None
    same = bool(b and va and vb and parse_unit(va.unit).same_dims(parse_unit(vb.unit)))
    origin_r = None
    if same:
        if opts.get("origin_radius") is not None:
            ca = cb = 0.0
        else:
            (la, ha), (lb, hb) = _span(ua), _span(ub)
            ca, cb = (la + ha) / 2, (lb + hb) / 2
        ext = max([abs(x - ca) for x in ua if math.isfinite(x)] + [abs(y - cb) for y in ub if math.isfinite(y)] + [1e-300])
        if opts.get("origin_radius") is not None:
            ext = max(ext, float(opts["origin_radius"]))
            origin_r = float(opts["origin_radius"]) / ext
        sa = sb = ext
    else:
        (la, ha), (lb, hb) = _span(ua), _span(ub)
        ca, cb, sa, sb = (la + ha) / 2, (lb + hb) / 2, (ha - la) / 2, (hb - lb) / 2
    pts = [[round((x - ca) / sa, 4) if math.isfinite(x) else 0.0, round((y - cb) / sb, 4) if math.isfinite(y) else 0.0]
           for x, y in zip(ua, ub)]
    return {"a": a, "b": b, "label_a": va.label if va else a, "label_b": (vb.label if vb else "time"),
            "sym_a": va.sym if va else a, "sym_b": (vb.sym if vb else "t"), "same_scale": same,
            "origin_r": origin_r, "pts": pts, "ts": [round(t, 4) for t in ts]}


def render_spatial(topic: Topic, traj: Optional[Trajectory], values: Dict[str, float], panels: List[dict],
                   options: Optional[dict] = None, live: Optional[List[dict]] = None,
                   problems: Optional[List[str]] = None) -> str:
    opts = dict(options or {})
    problems = problems if problems is not None else []   # path problems are appended for the caller
    ids = topic.var_ids
    index = {vid: i for i, vid in enumerate(ids)}
    layered, back = Graph(ids, [(r.source, r.target) for r in topic.relations]).layers()
    L = max(1, len(layered))
    nodes: List[dict] = [{} for _ in ids]
    for li, layer in enumerate(layered):
        lon = 300.0 * ((li + 0.5) / L - 0.5)
        for k, vid in enumerate(layer):
            lat = 0.0 if len(layer) == 1 else -55.0 + 110.0 * k / (len(layer) - 1)
            v = topic.var(vid)
            val = values.get(vid)
            nodes[index[vid]] = {
                "id": vid, "label": v.label, "sym": v.sym, "kind": v.kind,
                "kind_label": KIND_LABELS.get(v.kind, v.kind), "color": KIND_COLORS.get(v.kind, "#898781"),
                "unit": pretty_unit(v.unit), "value": val if val is None or math.isfinite(val) else None,
                "equation": v.equation or "", "description": v.description or "", "lat": round(lat, 2), "lon": round(lon, 2),
            }
    edges = [{"s": index[r.source], "t": index[r.target], "sign": r.sign if r.sign in ("+", "-") else "?",
              "mech": r.mechanism or "", "auto": r.auto, "kind": r.kind, "back": (r.source, r.target) in back}
             for r in topic.relations if r.source in index and r.target in index]
    data = {
        "title": topic.title, "summary": topic.summary, "nodes": nodes, "edges": edges,
        "panels": panels, "live": live or [], "problems": problems,
        "kinds": [{"kind": k, "label": KIND_LABELS[k], "color": KIND_COLORS[k]} for k in KIND_COLORS
                  if any(v.kind == k for v in topic.variables)],
        "series": {}, "fired": [], "path": None, "t_span": None, "time_unit": "",
    }
    if traj is not None and traj.times:
        t0, t1 = traj.times[0], traj.times[-1]
        idx = _downsample(len(traj.times), MAX_SERIES_POINTS)
        for vid, vals in traj.series.items():
            if vid not in index:
                continue
            lo, hi = _span(vals)
            data["series"][vid] = {"min": lo, "max": hi,
                                   "pts": [round((vals[i] - lo) / (hi - lo), 4) if math.isfinite(vals[i]) else None for i in idx]}
        data["t_span"] = [t0, t1]
        data["time_unit"] = traj.time_unit
        data["fired"] = [{"label": f.event.title, "t": (f.t - t0) / ((t1 - t0) or 1.0)} for f in traj.fired]
        data["path"] = _path(topic, traj, opts, problems)
    payload = json.dumps(data, allow_nan=False, default=str).replace("<", "\\u003c")
    return (_TEMPLATE.replace("%%TITLE%%", escape(topic.title))
            .replace("%%THREE%%", THREE_VERSION)
            .replace("%%DATA%%", payload))


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%TITLE%%: spatial room</title>
<style>
  html,body{margin:0;height:100%;background:#0d0d0d;color:#fff;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;overflow:hidden}
  #hud{position:fixed;left:16px;top:14px;right:16px;display:flex;flex-direction:column;gap:8px;pointer-events:none;z-index:2}
  #hud > *{pointer-events:auto;align-self:flex-start}
  h1{font-size:15px;margin:0;font-weight:600;background:rgba(26,26,25,.88);padding:6px 10px;border-radius:8px}
  .row{display:flex;gap:6px;flex-wrap:wrap;align-items:center;background:rgba(26,26,25,.88);border:1px solid #2c2c2a;border-radius:8px;padding:6px 8px;font-size:13px}
  button{background:#1a1a19;color:#fff;border:1px solid #383835;border-radius:6px;padding:5px 10px;cursor:pointer;font:inherit}
  button:hover:not(:disabled){border-color:#3987e5} button:disabled{color:#6b6a66;cursor:not-allowed}
  button.xr{border-color:#3987e5}
  #tval{color:#c3c2b7;min-width:9em;font-variant-numeric:tabular-nums}
  .hint{margin:0;font-size:12.5px;color:#c3c2b7;background:rgba(26,26,25,.75);padding:5px 10px;border-radius:8px;max-width:640px;line-height:1.45}
  #side{position:fixed;left:16px;bottom:16px;display:flex;flex-direction:column;gap:8px;max-width:min(420px,calc(100vw - 32px));z-index:2}
  .card{background:rgba(26,26,25,.9);border:1px solid #2c2c2a;border-radius:8px;padding:8px 10px;font-size:12.5px;color:#c3c2b7;line-height:1.5}
  .card b{color:#fff} .card .num{font-variant-numeric:tabular-nums;color:#fff}
  .sw{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 4px 0 10px;vertical-align:0}
  .sw:first-child{margin-left:0} .ln{display:inline-block;width:14px;height:2px;margin:0 4px 3px 10px;vertical-align:middle}
  .warn{color:#e66767}
  #tip{position:fixed;display:none;background:rgba(26,26,25,.95);border:1px solid #383835;border-radius:8px;padding:6px 9px;font-size:12.5px;pointer-events:none;z-index:3}
  @media (max-width:600px){.hint{display:none}}
</style>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@%%THREE%%/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@%%THREE%%/examples/jsm/"}}</script>
</head><body>
<div id="hud">
  <h1>%%TITLE%% — spatial room</h1>
  <div class="row"><button id="play">⏸ pause</button><button id="unroll">unroll time ↑</button><button id="reset">reset view</button>
    <button id="vr" class="xr" disabled>Enter VR</button><button id="ar" class="xr" disabled>Enter AR</button><span id="tval"></span></div>
  <p class="hint">Drag the globe to turn it. Click a variable (a city) to raise it and unfold its card on the table. Click a panel to bring it close. In a headset, pinch or pull the trigger to do the same; with hand tracking you can reach into the globe and turn it; grab it with both hands to resize it.</p>
</div>
<div id="side"><div id="live"></div><div class="card" id="legend"></div></div>
<div id="tip"></div>
<script id="data" type="application/json">%%DATA%%</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { XRHandModelFactory } from 'three/addons/webxr/XRHandModelFactory.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';

const D = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';
const COL = { pos: 0x3987e5, neg: 0xe66767, unk: 0x898781, accent: 0xeb6834 };
const signColor = s => s === '+' ? COL.pos : s === '-' ? COL.neg : COL.unk;
const css = c => '#' + c.toString(16).padStart(6, '0');

// ---------------------------------------------------------------- renderer, camera, room
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.xr.enabled = true;
renderer.xr.setReferenceSpaceType('local-floor');
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const BG = new THREE.Color(0x0d0d0d);
scene.background = BG;
const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.02, 50);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
const HOME = { pos: new THREE.Vector3(0, 1.5, 0.85), target: new THREE.Vector3(0, 1.1, -0.9) };   // the table and the panel arc
function resetView() { camera.position.copy(HOME.pos); controls.target.copy(HOME.target); controls.update(); }
resetView();
scene.add(new THREE.HemisphereLight(0xffffff, 0x202020, 1.0));
const sun = new THREE.DirectionalLight(0xffffff, 1.1); sun.position.set(1.5, 3, 1); scene.add(sun);
const room = new THREE.Group(); scene.add(room);            // hidden in passthrough AR
room.add(new THREE.GridHelper(8, 32, 0x2c2c2a, 0x1f1f1d));

const TABLE_Y = 0.74;
const table = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.03, 0.62),
  new THREE.MeshStandardMaterial({ color: 0x1a1a19, roughness: 0.9, transparent: true }));
table.position.set(0, TABLE_Y - 0.015, -0.62); scene.add(table);
table.add(new THREE.LineSegments(new THREE.EdgesGeometry(table.geometry), new THREE.LineBasicMaterial({ color: 0x383835 })));

// ---------------------------------------------------------------- helpers
function tex(c) { const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; return t; }
function rr(x, X, Y, W, H, r) { x.beginPath(); if (x.roundRect) x.roundRect(X, Y, W, H, r); else x.rect(X, Y, W, H); }
function label(text, h = 0.02, color = '#ffffff', o = {}) {
  const c = document.createElement('canvas'), x = c.getContext('2d');
  const font = `${o.weight || 600} 48px ${FONT}`;
  x.font = font; const w = Math.ceil(x.measureText(text).width) + 28; c.width = w; c.height = 68;
  if (o.bg) { x.fillStyle = o.bg; rr(x, 0, 0, w, 68, 16); x.fill(); }
  x.font = font; x.fillStyle = color; x.textBaseline = 'middle'; x.fillText(text, 14, 36);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex(c), transparent: true, depthTest: o.depthTest ?? true, depthWrite: false }));
  sp.scale.set(h * w / 68, h, 1); sp.userData.h = h; sp.userData.aspect = w / 68; return sp;
}
function fmt(v) {
  if (v == null || !isFinite(v)) return '—';
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(3).replace('e+', 'e');
  return (+v.toPrecision(4)).toLocaleString('en-US', { maximumFractionDigits: 6 });
}
function wrap(x, text, maxW, maxLines) {
  const words = String(text).split(/\s+/), lines = []; let cur = '';
  for (const w of words) { const t = cur ? cur + ' ' + w : w; if (x.measureText(t).width > maxW && cur) { lines.push(cur); cur = w; } else cur = t; }
  if (cur) lines.push(cur);
  if (lines.length > maxLines) { lines.length = maxLines; lines[maxLines - 1] = lines[maxLines - 1].replace(/\s*\S*$/, '') + ' …'; }
  return lines;
}
function fit(x, text, maxW) { let t = String(text); if (x.measureText(t).width <= maxW) return t; while (t.length > 1 && x.measureText(t + '…').width > maxW) t = t.slice(0, -1); return t + '…'; }
const dirOf = (lat, lon) => { const a = THREE.MathUtils.degToRad(lat), b = THREE.MathUtils.degToRad(lon);
  return new THREE.Vector3(Math.cos(a) * Math.sin(b), Math.sin(a), Math.cos(a) * Math.cos(b)); };
const valueAt = (s, T) => { if (!s) return null; const i = Math.round(T * (s.pts.length - 1)); const p = s.pts[i]; return p == null ? null : s.min + p * (s.max - s.min); };

// ---------------------------------------------------------------- the concept globe
const R = 0.15;
const globe = new THREE.Group(); globe.position.set(-0.2, TABLE_Y + 0.25, -0.66); scene.add(globe);
const body = new THREE.Mesh(new THREE.SphereGeometry(R, 64, 32),
  new THREE.MeshStandardMaterial({ color: 0x0f2238, emissive: 0x081626, roughness: 0.55, metalness: 0.1 }));
globe.add(body);
globe.add(new THREE.Mesh(new THREE.SphereGeometry(R * 1.07, 48, 24),
  new THREE.MeshBasicMaterial({ color: 0x3987e5, transparent: true, opacity: 0.07, side: THREE.BackSide, depthWrite: false })));
{ const g = [];
  for (let lat = -60; lat <= 60; lat += 30) for (let lon = 0; lon < 360; lon += 6) g.push(dirOf(lat, lon).multiplyScalar(R * 1.002), dirOf(lat, lon + 6).multiplyScalar(R * 1.002));
  for (let lon = 0; lon < 360; lon += 30) for (let lat = -90; lat < 90; lat += 6) g.push(dirOf(lat, lon).multiplyScalar(R * 1.002), dirOf(lat + 6, lon).multiplyScalar(R * 1.002));
  globe.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(g), new THREE.LineBasicMaterial({ color: 0x3987e5, transparent: true, opacity: 0.2 }))); }
{ const h = globe.position.y - R - TABLE_Y;
  const ped = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.05, h, 24), new THREE.MeshStandardMaterial({ color: 0x2c2c2a, roughness: 0.6 }));
  ped.position.set(globe.position.x, TABLE_Y + h / 2, globe.position.z); scene.add(ped); }

const UP = new THREE.Vector3(0, 1, 0);
const nodes = D.nodes.map((n, i) => {
  const d = dirOf(n.lat, n.lon), col = new THREE.Color(n.color);
  const city = new THREE.Mesh(new THREE.SphereGeometry(0.0085, 16, 12), new THREE.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: 0.35 }));
  city.position.copy(d).multiplyScalar(R); globe.add(city);
  const tower = new THREE.Mesh(new THREE.CylinderGeometry(0.0035, 0.0035, 1, 10), new THREE.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: 0.6 }));
  tower.quaternion.setFromUnitVectors(UP, d); globe.add(tower);
  const lab = label(n.sym, 0.019); globe.add(lab);
  const hit = new THREE.Mesh(new THREE.SphereGeometry(0.02, 8, 6), new THREE.MeshBasicMaterial({ visible: false }));
  hit.position.copy(city.position); hit.userData = { type: 'city', i }; globe.add(hit);
  return { n, d, city, tower, lab, hit, h: 0.012, hTarget: 0.012 };
});
function placeTower(N) { N.tower.scale.set(1, N.h, 1); N.tower.position.copy(N.d).multiplyScalar(R + N.h / 2); N.lab.position.copy(N.d).multiplyScalar(R + N.h + 0.016); }
nodes.forEach(placeTower);

function slerp(a, b, th, t) { if (th < 1e-4) return a.clone().lerp(b, t).normalize(); const s = Math.sin(th);
  return a.clone().multiplyScalar(Math.sin((1 - t) * th) / s).add(b.clone().multiplyScalar(Math.sin(t * th) / s)); }
function arc(a, b) {
  if (a.equals(b)) {                                   // self-influence: a small loop above the city
    const t1 = new THREE.Vector3().crossVectors(a, Math.abs(a.y) < 0.9 ? UP : new THREE.Vector3(1, 0, 0)).normalize();
    const c = a.clone().multiplyScalar(R + 0.02), out = [];
    for (let i = 0; i <= 32; i++) { const th = i / 32 * Math.PI * 2; out.push(c.clone().addScaledVector(a, -0.014 * Math.cos(th)).addScaledVector(t1, 0.014 * Math.sin(th))); }
    return out;
  }
  let bb = b, th = a.angleTo(b);
  if (th > Math.PI - 1e-3) { bb = b.clone().add(new THREE.Vector3(0, 0.02, 0)).normalize(); th = a.angleTo(bb); }
  const lift = 0.06 + 0.22 * th / Math.PI, out = [];
  for (let i = 0; i <= 40; i++) { const t = i / 40; out.push(slerp(a, bb, th, t).multiplyScalar(R * (1 + lift * Math.sin(Math.PI * t)))); }
  return out;
}
const PULSE = new THREE.SphereGeometry(0.0035, 8, 6);
const edges = D.edges.map((e, k) => {
  const pts = arc(nodes[e.s].d, nodes[e.t].d), color = signColor(e.sign);
  const mat = e.sign === '?' ? new THREE.LineDashedMaterial({ color, transparent: true, opacity: 0.7, dashSize: 0.006, gapSize: 0.004 })
                             : new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.7 });
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat);
  if (mat.isLineDashedMaterial) line.computeLineDistances();
  globe.add(line);
  const pulse = new THREE.Mesh(PULSE, new THREE.MeshBasicMaterial({ color })); globe.add(pulse);
  return { e, pts, line, pulse, phase: (k * 0.377) % 1 };
});

// ---------------------------------------------------------------- the card that unfolds on the table
const TW = 1200, TH = 760;
const tileCanvas = document.createElement('canvas'); tileCanvas.width = TW; tileCanvas.height = TH;
const tileTex = tex(tileCanvas);
const tile = new THREE.Mesh(new THREE.PlaneGeometry(0.42, 0.42 * TH / TW), new THREE.MeshBasicMaterial({ map: tileTex, transparent: true, toneMapped: false, side: THREE.DoubleSide }));
tile.position.set(0.0, TABLE_Y + 0.085, -0.43); tile.rotation.x = -0.9; tile.visible = false; scene.add(tile);
let tileS = 0, selected = -1, hovered = -1, T = 0;
function drawTile() {
  const x = tileCanvas.getContext('2d'); x.clearRect(0, 0, TW, TH);
  if (selected < 0) { tileTex.needsUpdate = true; return; }
  const n = D.nodes[selected], s = D.series[n.id];
  x.fillStyle = 'rgba(26,26,25,0.96)'; rr(x, 4, 4, TW - 8, TH - 8, 28); x.fill(); x.strokeStyle = '#383835'; x.lineWidth = 3; x.stroke();
  x.textBaseline = 'alphabetic';
  x.fillStyle = n.color; x.beginPath(); x.arc(58, 62, 13, 0, 7); x.fill();
  x.font = `500 30px ${FONT}`; x.fillStyle = '#898781'; x.fillText(n.kind_label, 84, 72);
  x.font = `700 52px ${FONT}`; x.fillStyle = '#ffffff'; x.fillText(fit(x, `${n.label}  ${n.sym}`, TW - 90), 44, 140);
  x.font = `600 58px ${FONT}`; x.fillStyle = '#ffffff';
  const now = s && D.t_span ? valueAt(s, T) : null;
  const vtxt = fmt(now ?? n.value) + (n.unit && n.unit !== '1' ? ' ' + n.unit : '');
  x.fillText(fit(x, vtxt, 640), 44, 226);
  x.font = `400 26px ${FONT}`; x.fillStyle = '#898781';
  x.fillText(now != null ? `at t = ${fmt(D.t_span[0] + T * (D.t_span[1] - D.t_span[0]))} ${D.time_unit}` : 'at the default values', 44, 264);
  let y = 318;
  if (n.equation) { x.font = `500 28px ui-monospace, Menlo, monospace`; x.fillStyle = '#c3c2b7'; x.fillText(fit(x, `${n.sym} = ${n.equation}`, s ? 600 : TW - 90), 44, y); y += 52; }
  const colW = s ? 600 : TW - 90;
  const inc = D.edges.filter(e => e.t === selected && e.s !== selected), out = D.edges.filter(e => e.s === selected && e.t !== selected);
  x.font = `700 28px ${FONT}`; x.fillStyle = '#ffffff'; x.fillText(inc.length ? 'Pushed by' : (n.description ? 'Meaning' : 'An input: nothing in the model pushes it'), 44, y); y += 40;
  if (!inc.length && n.description) { x.font = `400 26px ${FONT}`; x.fillStyle = '#c3c2b7'; for (const l of wrap(x, n.description, colW, 5)) { x.fillText(l, 44, y); y += 34; } }
  for (const e of inc.slice(0, 5)) {
    if (y > TH - 60) break;
    const src = D.nodes[e.s];
    x.font = `700 30px ${FONT}`; x.fillStyle = css(signColor(e.sign)); x.fillText(e.sign === '-' ? '−' : e.sign, 48, y);
    x.font = `600 27px ${FONT}`; x.fillStyle = '#ffffff'; x.fillText(fit(x, `${src.label} (${src.sym})`, colW - 40), 82, y); y += 32;
    if (e.mech) { x.font = `400 23px ${FONT}`; x.fillStyle = '#c3c2b7'; for (const l of wrap(x, e.mech, colW - 40, 2)) { x.fillText(l, 82, y); y += 28; } }
    y += 8;
  }
  if (out.length && y < TH - 40) { x.font = `400 24px ${FONT}`; x.fillStyle = '#898781';
    x.fillText(fit(x, 'Pushes → ' + out.map(e => D.nodes[e.t].sym).join(', '), colW), 44, Math.min(y + 6, TH - 30)); }
  if (s) {                                               // the variable over time, with a "now" marker
    const X0 = 690, X1 = TW - 50, Y0 = 330, Y1 = TH - 90;
    x.strokeStyle = '#383835'; x.lineWidth = 2; x.beginPath(); x.moveTo(X0, Y0); x.lineTo(X0, Y1); x.lineTo(X1, Y1); x.stroke();
    x.strokeStyle = '#3987e5'; x.lineWidth = 4; x.beginPath(); let first = true;
    s.pts.forEach((p, i) => { if (p == null) { first = true; return; } const px = X0 + (X1 - X0) * i / (s.pts.length - 1), py = Y1 - (Y1 - Y0) * p;
      if (first) { x.moveTo(px, py); first = false; } else x.lineTo(px, py); });
    x.stroke();
    const i = Math.round(T * (s.pts.length - 1)), p = s.pts[i];
    if (p != null) { x.fillStyle = '#eb6834'; x.beginPath(); x.arc(X0 + (X1 - X0) * T, Y1 - (Y1 - Y0) * p, 11, 0, 7); x.fill(); }
    x.font = `400 22px ${FONT}`; x.fillStyle = '#898781';
    x.fillText(fmt(s.max), X0 + 8, Y0 - 10); x.fillText(fmt(s.min), X0 + 8, Y1 + 30);
    x.textAlign = 'right'; x.fillText(`time (${D.time_unit}) →`, X1, Y1 + 30); x.textAlign = 'left';
  }
  tileTex.needsUpdate = true;
}
function select(i) {
  selected = selected === i ? -1 : i;
  nodes.forEach((N, k) => N.hTarget = k === selected ? 0.07 : 0.012);
  edges.forEach(E => { const on = selected < 0 || E.e.s === selected || E.e.t === selected;
    E.line.material.opacity = on ? (selected < 0 ? 0.7 : 1) : 0.07; E.pulse.visible = on; });
  drawTile();
}

// ---------------------------------------------------------------- the path plate: states traced over time
const P = D.path, PLATE = new THREE.Vector3(0.27, TABLE_Y + 0.006, -0.6), PR = 0.17, PH = 0.3;
let unroll = 0, unrollTarget = 0, pathGeo = null, ghostGeo = null, pathMat = null, marker = null, planet = null, worldline = null;
{ const disc = new THREE.Mesh(new THREE.CylinderGeometry(PR, PR, 0.008, 64), new THREE.MeshStandardMaterial({ color: 0x141413, roughness: 0.8 }));
  disc.position.copy(PLATE); scene.add(disc);
  const ring = []; for (let i = 0; i <= 96; i++) { const a = i / 96 * Math.PI * 2; ring.push(new THREE.Vector3(PLATE.x + PR * Math.cos(a), PLATE.y + 0.0045, PLATE.z + PR * Math.sin(a))); }
  scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(ring), new THREE.LineBasicMaterial({ color: 0x383835 }))); }
if (P) {
  ghostGeo = new THREE.BufferGeometry(); ghostGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(P.pts.length * 3), 3));
  scene.add(new THREE.Line(ghostGeo, new THREE.LineBasicMaterial({ color: 0x52514e })));     // the whole run, faint
  pathGeo = new LineGeometry();                                                                // the part already traced, thick
  pathMat = new LineMaterial({ color: 0x6da7ec, linewidth: 0.0028, worldUnits: true });
  pathMat.resolution.set(innerWidth, innerHeight);
  scene.add(new Line2(pathGeo, pathMat));
  marker = new THREE.Mesh(new THREE.SphereGeometry(0.008, 16, 12), new THREE.MeshStandardMaterial({ color: COL.accent, emissive: COL.accent, emissiveIntensity: 0.5 }));
  scene.add(marker);
  if (P.origin_r) {
    const r = P.origin_r * PR * 0.9;
    planet = new THREE.Mesh(new THREE.SphereGeometry(r, 32, 16), new THREE.MeshStandardMaterial({ color: 0x123a6b, emissive: 0x0d2a4d, emissiveIntensity: 0.3, roughness: 0.8 }));
    scene.add(planet);
    worldline = new THREE.Mesh(new THREE.CylinderGeometry(r, r, 1, 32, 1, true), new THREE.MeshBasicMaterial({ color: 0x3987e5, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false }));
    scene.add(worldline);
  }
  const la = label(`${P.sym_a} →`, 0.018, '#c3c2b7'); la.position.set(PLATE.x + PR * 0.7, PLATE.y + 0.02, PLATE.z + PR + 0.02); scene.add(la);
  const lb = label(`${P.sym_b} ↑`, 0.018, '#c3c2b7'); lb.position.set(PLATE.x - PR - 0.035, PLATE.y + 0.02, PLATE.z - PR * 0.6); scene.add(lb);
  const lt = label('time ↑', 0.018, '#c3c2b7'); lt.position.set(PLATE.x + PR + 0.03, PLATE.y + PH * 0.6, PLATE.z); lt.userData.unroll = true; scene.add(lt); lt.visible = false;
  P.timeLabel = lt;
}
function layoutPath() {
  if (!P) return;
  const a = ghostGeo.attributes.position.array, s = PR * 0.9;
  for (let i = 0; i < P.pts.length; i++) {
    a[3 * i] = PLATE.x + P.pts[i][0] * s; a[3 * i + 1] = PLATE.y + 0.012 + unroll * P.ts[i] * PH; a[3 * i + 2] = PLATE.z - P.pts[i][1] * s;
  }
  ghostGeo.attributes.position.needsUpdate = true; ghostGeo.computeBoundingSphere();
  const n = P.pts.length, k = Math.min(n - 1, Math.round(T * (n - 1)));
  if (pathGeo.userData.unroll !== unroll) { pathGeo.setPositions(a); pathGeo.userData.unroll = unroll; }
  pathGeo.instanceCount = Math.max(0, k);
  marker.position.set(a[3 * k], a[3 * k + 1], a[3 * k + 2]);
  if (planet) { const y = PLATE.y + 0.012 + unroll * P.ts[k] * PH; planet.position.set(PLATE.x, y, PLATE.z);
    worldline.scale.set(1, Math.max(1e-3, unroll * PH), 1); worldline.position.set(PLATE.x, PLATE.y + 0.012 + unroll * PH / 2, PLATE.z);
    worldline.material.opacity = 0.1 * unroll; }
  P.timeLabel.visible = unroll > 0.5;
}

// ---------------------------------------------------------------- panels: the 2D figures on an arc
const panels = D.panels.map((p, k) => {
  const g = new THREE.Group(), ang = THREE.MathUtils.degToRad((k - (D.panels.length - 1) / 2) * 32), r = 1.45;
  g.position.set(Math.sin(ang) * r, 1.55, -Math.cos(ang) * r); g.lookAt(0, 1.5, 0); scene.add(g);
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ color: 0x2c2c2a, toneMapped: false, side: THREE.DoubleSide }));
  mesh.scale.set(0.62, 0.4, 1); mesh.userData = { type: 'panel', k }; g.add(mesh);
  const title = label(`Fig. ${p.number}  ${p.title}`, 0.026, '#c3c2b7', { weight: 500 }); g.add(title);
  const P_ = { g, mesh, title, home: { p: g.position.clone(), q: g.quaternion.clone(), s: 1 }, to: null };
  const img = new Image();
  img.onload = () => {
    const W = 1800, H = Math.round(W * img.naturalHeight / img.naturalWidth), c = document.createElement('canvas'); c.width = W; c.height = H;
    const x = c.getContext('2d'); x.fillStyle = '#fcfcfb'; x.fillRect(0, 0, W, H); x.drawImage(img, 0, 0, W, H);
    mesh.material.map = tex(c); mesh.material.color.set(0xffffff); mesh.material.needsUpdate = true;
    let w = 0.62, h = w * H / W; if (h > 0.62) { h = 0.62; w = h * W / H; }
    mesh.scale.set(w, h, 1); title.position.set(0, h / 2 + 0.03, 0);
  };
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(p.svg);
  title.position.set(0, 0.23, 0);
  return P_;
});
let focused = -1;
function focusPanel(k) {
  if (focused >= 0) panels[focused].to = null;
  focused = focused === k ? -1 : k;
  if (focused < 0) return;
  const cam = renderer.xr.isPresenting ? renderer.xr.getCamera() : camera;
  const head = new THREE.Vector3(), fwd = new THREE.Vector3();
  cam.getWorldPosition(head); cam.getWorldDirection(fwd); fwd.y = 0; fwd.normalize();
  const dummy = new THREE.Object3D(); dummy.position.copy(head).addScaledVector(fwd, 0.72); dummy.position.y = head.y - 0.04; dummy.lookAt(head);
  panels[focused].to = { p: dummy.position.clone(), q: dummy.quaternion.clone(), s: 1.15 };
}

// ---------------------------------------------------------------- buttons on the table edge (usable in a headset)
const buttons = [];
function button(text, x, onTap) {
  const c = document.createElement('canvas'); c.width = 400; c.height = 140; const t = tex(c);
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(0.1, 0.035), new THREE.MeshBasicMaterial({ map: t, transparent: true, toneMapped: false }));
  mesh.position.set(x, TABLE_Y + 0.02, -0.36); mesh.rotation.x = -1.05; mesh.userData = { type: 'button', onTap }; mesh.visible = false;   // shown in a headset
  scene.add(mesh); buttons.push(mesh);
  const set = s => { const g = c.getContext('2d'); g.clearRect(0, 0, 400, 140); g.fillStyle = '#1a1a19'; rr(g, 4, 4, 392, 132, 26); g.fill();
    g.strokeStyle = '#3987e5'; g.lineWidth = 4; g.stroke(); g.fillStyle = '#fff'; g.font = `600 48px ${FONT}`; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(s, 200, 72); t.needsUpdate = true; };
  set(text); return set;
}
let playing = true;
const playBtn3d = button('⏸ pause', -0.52, togglePlay);
const unrollBtn3d = button('unroll ↑', -0.41, toggleUnroll);
button('reset', -0.30, resetAll);
function togglePlay() { if (!D.t_span) return; playing = !playing; if (playing && T >= 1) T = 0; syncButtons(); }
function toggleUnroll() { unrollTarget = unrollTarget ? 0 : 1; syncButtons(); }
function resetAll() { globe.quaternion.identity(); globe.scale.setScalar(1); if (selected >= 0) select(selected); if (focused >= 0) focusPanel(focused); if (!renderer.xr.isPresenting) resetView(); }
function syncButtons() {
  const p = playing ? '⏸ pause' : '▶ play', u = unrollTarget ? 'flatten ↓' : 'unroll ↑';
  playBtn3d(p); unrollBtn3d(u); $('play').textContent = p; $('unroll').textContent = unrollTarget ? 'flatten time ↓' : 'unroll time ↑';
}
$('play').onclick = togglePlay; $('unroll').onclick = toggleUnroll; $('reset').onclick = resetAll;
if (!D.t_span) { $('play').disabled = true; playing = false; }
if (!P) $('unroll').disabled = true;
{ const t = label(D.title, 0.03, '#ffffff'); t.position.set(0.03, TABLE_Y + 0.5, -0.86); scene.add(t); }
let toast = null, toastT = 0;                       // event name flashes above the plate when it fires
function showToast(text) { if (toast) scene.remove(toast);
  toast = label(text, 0.024, '#eb6834', { bg: 'rgba(26,26,25,0.9)', depthTest: false });
  toast.position.set(PLATE.x, PLATE.y + PH + 0.06, PLATE.z); scene.add(toast); toastT = 2.5; }

// ---------------------------------------------------------------- live feeds
const live = D.live.map(f => Object.assign({ value: null, raw: null, at: 0, err: '' }, f));
let livePanel = null, liveCanvas = null;
if (live.length) {
  liveCanvas = document.createElement('canvas'); liveCanvas.width = 900; liveCanvas.height = 70 + 170 * live.length;
  livePanel = new THREE.Mesh(new THREE.PlaneGeometry(0.34, 0.34 * liveCanvas.height / 900), new THREE.MeshBasicMaterial({ map: tex(liveCanvas), transparent: true, toneMapped: false, side: THREE.DoubleSide }));
  livePanel.position.set(-0.6, TABLE_Y + 0.38, -0.74); livePanel.lookAt(0, 1.45, 0); scene.add(livePanel);
}
function liveLines(f) {
  const ago = f.at ? Math.round((Date.now() - f.at) / 1000) + ' s ago' : 'waiting…';
  const val = f.raw == null ? '—' : `${fmt(f.raw)} ${f.unit}`;
  let cmp = '', delta = '';
  if (f.model) { const m = f.model; cmp = `model ${m.sym} = ${fmt(m.value)} ${m.unit}`;
    if (f.value != null && m.value) delta = `live is ${fmt(f.value)} ${m.unit}: ${(100 * (f.value - m.value) / m.value).toFixed(2)} % vs the model`; }
  return { ago, val, cmp, delta };
}
function drawLive() {
  if (!live.length) return;
  $('live').innerHTML = live.map(f => { const L = liveLines(f);
    return `<div class="card"><b>● ${esc(f.label)}</b> <span class="num">${esc(L.val)}</span><br>${L.cmp ? esc(L.cmp) + '<br>' : ''}${L.delta ? '<span class="num">' + esc(L.delta) + '</span><br>' : ''}` +
      `<span style="color:#898781">${esc(f.source)} · updated ${esc(L.ago)}${f.err ? ' · <span class="warn">' + esc(f.err) + '</span>' : ''}</span></div>`; }).join('');
  const x = liveCanvas.getContext('2d'), W = 900, H = liveCanvas.height; x.clearRect(0, 0, W, H);
  x.fillStyle = 'rgba(26,26,25,0.95)'; rr(x, 3, 3, W - 6, H - 6, 24); x.fill(); x.strokeStyle = '#1baf7a'; x.lineWidth = 3; x.stroke();
  x.font = `700 30px ${FONT}`; x.fillStyle = '#1baf7a'; x.fillText('● LIVE', 30, 50);
  live.forEach((f, k) => { const L = liveLines(f), y = 70 + 170 * k;
    x.font = `600 30px ${FONT}`; x.fillStyle = '#fff'; x.fillText(fit(x, f.label, W - 60), 30, y + 36);
    x.font = `700 44px ${FONT}`; x.fillText(fit(x, L.val, W - 60), 30, y + 86);
    x.font = `400 24px ${FONT}`; x.fillStyle = '#c3c2b7'; x.fillText(fit(x, L.delta || L.cmp, W - 60), 30, y + 122);
    x.fillStyle = f.err ? '#e66767' : '#898781'; x.fillText(fit(x, f.err || `${f.source} · updated ${L.ago}`, W - 60), 30, y + 154); });
  livePanel.material.map.needsUpdate = true;
}
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]); }
async function poll(f) {
  try {
    const r = await fetch(f.url, { cache: 'no-store' }); if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json(), v = f.field.split('.').reduce((o, k) => (o == null ? o : o[k]), j);
    if (typeof v !== 'number' || !isFinite(v)) throw new Error('no number at ' + f.field);
    f.raw = v; f.value = v * f.factor; f.at = Date.now(); f.err = '';
  } catch (e) { f.err = 'offline: ' + (e.message || e); }
  drawLive();
}
live.forEach(f => { poll(f); setInterval(() => poll(f), f.every * 1000); });
if (live.length) setInterval(drawLive, 1000);

// ---------------------------------------------------------------- legend & notes
{ const k = D.kinds.map(k => `<span class="sw" style="background:${k.color}"></span>${esc(k.label)}`).join('');
  const e = `<span class="ln" style="background:#3987e5"></span>pushes up<span class="ln" style="background:#e66767"></span>pushes down<span class="ln" style="background:#898781"></span>unknown sign`;
  const pr = D.problems.length ? '<br><span class="warn">' + D.problems.map(esc).join('<br>') + '</span>' : '';
  $('legend').innerHTML = `${k}<br>${e}<br><span style="color:#898781">Pulses travel from cause to effect. ${P ? `Plate: ${esc(P.label_a)} vs ${esc(P.label_b)}${P.origin_r ? ' around the central body' : ''}.` : ''}</span>${pr}`; }

// ---------------------------------------------------------------- picking and grabbing (mouse, controllers and hands)
const raycaster = new THREE.Raycaster();
function pick(o, d) {
  raycaster.set(o, d); raycaster.far = 10;
  const h = raycaster.intersectObjects([...buttons.filter(b => b.visible), ...panels.map(p => p.mesh), ...nodes.map(N => N.hit), body], false)[0];
  if (!h) return null;
  return h.object === body ? { type: 'globe', distance: h.distance } : Object.assign({ distance: h.distance }, h.object.userData);
}
function spherePoint(o, d) {                         // where the ray meets the globe (or comes closest to it)
  const c = globe.position, Rs = R * globe.scale.x, oc = o.clone().sub(c), b = oc.dot(d), disc = b * b - (oc.lengthSq() - Rs * Rs);
  const t = disc >= 0 ? -b - Math.sqrt(disc) : -b;
  return o.clone().addScaledVector(d, t).sub(c).normalize();
}
const pointers = [];
let twoHand = null;
function mkPointer(kind) { const p = { kind, active: false, grab: null, down: null, moved: 0, d0: null, pos: new THREE.Vector3() }; pointers.push(p); return p; }
function pressStart(p, o, d, direct) {
  p.active = true; p.down = pick(o, d); p.moved = 0; p.d0 = d.clone();
  if (direct || (p.down && (p.down.type === 'globe' || p.down.type === 'city'))) {
    p.grab = { v0: direct ? direct.clone().sub(globe.position).normalize() : spherePoint(o, d), q0: globe.quaternion.clone(), direct: !!direct };
    const g = pointers.filter(q => q.grab);
    if (g.length === 2) twoHand = { d0: Math.max(1e-3, g[0].pos.distanceTo(g[1].pos)), s0: globe.scale.x };
  }
}
function pressMove(p, o, d, direct) {
  if (!p.active) return;
  if (!p.grab) { p.moved = Math.max(p.moved, p.d0.angleTo(d)); return; }
  const g = pointers.filter(q => q.grab);
  if (twoHand && g.length === 2) { p.moved = 1; if (p === g[0]) globe.scale.setScalar(THREE.MathUtils.clamp(twoHand.s0 * g[0].pos.distanceTo(g[1].pos) / twoHand.d0, 0.5, 3)); return; }
  const v1 = p.grab.direct && direct ? direct.clone().sub(globe.position).normalize() : spherePoint(o, d);
  p.moved = Math.max(p.moved, p.grab.v0.angleTo(v1));
  globe.quaternion.copy(new THREE.Quaternion().setFromUnitVectors(p.grab.v0, v1).multiply(p.grab.q0));
}
function pressEnd(p) {
  if (!p.active) return;
  const h = p.down;
  if (p.moved < 0.05 && h) {
    if (h.type === 'city') select(h.i);
    else if (h.type === 'button') h.onTap();
    else if (h.type === 'panel') focusPanel(h.k);
    else if (h.type === 'globe' && selected >= 0) select(selected);
  }
  p.active = false; p.grab = null; p.down = null; twoHand = null;
}
function hover(h, ev) {
  const i = h && h.type === 'city' ? h.i : -1;
  if (i !== hovered) { if (hovered >= 0) nodes[hovered].lab.scale.set(nodes[hovered].lab.userData.h * nodes[hovered].lab.userData.aspect, nodes[hovered].lab.userData.h, 1);
    hovered = i; if (i >= 0) { const L = nodes[i].lab; L.scale.multiplyScalar(1.5); } }
  if (!ev) return;
  const tip = $('tip');
  if (i >= 0) { const n = D.nodes[i]; tip.style.display = 'block'; tip.style.left = ev.clientX + 14 + 'px'; tip.style.top = ev.clientY + 14 + 'px';
    tip.textContent = `${n.label} (${n.sym}) = ${fmt(n.value)} ${n.unit !== '1' ? n.unit : ''} · click to unfold`; }
  else tip.style.display = 'none';
  renderer.domElement.style.cursor = !h ? 'default' : (h.type === 'globe' ? 'grab' : 'pointer');
}
// mouse: capture-phase listeners run before OrbitControls, so grabbing the globe does not orbit the camera
const mouse = mkPointer('mouse'), ndc = new THREE.Vector2();
function mouseRay(ev) { ndc.set(ev.clientX / innerWidth * 2 - 1, -(ev.clientY / innerHeight) * 2 + 1); raycaster.setFromCamera(ndc, camera); return [raycaster.ray.origin.clone(), raycaster.ray.direction.clone()]; }
const cv = renderer.domElement;
cv.addEventListener('pointerdown', ev => { if (renderer.xr.isPresenting || ev.button !== 0) return; const [o, d] = mouseRay(ev); pressStart(mouse, o, d, null);
  if (mouse.grab) { controls.enabled = false; cv.setPointerCapture(ev.pointerId); cv.style.cursor = 'grabbing'; } }, { capture: true });
cv.addEventListener('pointermove', ev => { const [o, d] = mouseRay(ev); if (mouse.active) pressMove(mouse, o, d, null); else hover(pick(o, d), ev); });
cv.addEventListener('pointerup', () => { pressEnd(mouse); controls.enabled = true; });
cv.addEventListener('pointercancel', () => { pressEnd(mouse); controls.enabled = true; });
// controllers and hands: a pinch is a WebXR "select", so one code path serves both
const handFactory = new XRHandModelFactory();
const xrp = [0, 1].map(i => {
  const p = mkPointer('xr'); p.c = renderer.xr.getController(i); p.hand = renderer.xr.getHand(i); scene.add(p.c);
  p.hand.add(handFactory.createHandModel(p.hand, 'spheres')); scene.add(p.hand);
  p.ray = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, -1)]), new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.45 }));
  p.c.add(p.ray);
  p.c.addEventListener('connected', e => { p.isHand = !!(e.data && e.data.hand); p.connected = true; });
  p.c.addEventListener('disconnected', () => { pressEnd(p); p.connected = false; });
  p.c.addEventListener('selectstart', () => { const [o, d] = ctrlRay(p); const tipv = fingertip(p); pressStart(p, o, d, tipv && tipv.distanceTo(globe.position) < R * globe.scale.x * 1.35 ? tipv : null); });
  p.c.addEventListener('selectend', () => pressEnd(p));
  return p;
});
function ctrlRay(p) { const o = new THREE.Vector3().setFromMatrixPosition(p.c.matrixWorld), q = new THREE.Quaternion(); p.c.getWorldQuaternion(q); return [o, new THREE.Vector3(0, 0, -1).applyQuaternion(q)]; }
function fingertip(p) { const j = p.isHand && p.hand.joints && p.hand.joints['index-finger-tip']; if (!j) return null; const v = new THREE.Vector3(); j.getWorldPosition(v); return v; }

// ---------------------------------------------------------------- entering VR / AR
let xrMode = null;
async function setupXR() {
  for (const [id, mode] of [['vr', 'immersive-vr'], ['ar', 'immersive-ar']]) {
    const b = $(id); let ok = false;
    try { ok = !!navigator.xr && await navigator.xr.isSessionSupported(mode); } catch (e) { ok = false; }
    b.disabled = !ok;
    b.title = ok ? '' : (window.isSecureContext ? `This browser or device has no ${mode} support` : 'WebXR needs https or localhost');
    b.onclick = async () => {
      const cur = renderer.xr.getSession(); if (cur) { await cur.end(); return; }
      try { const s = await navigator.xr.requestSession(mode, { optionalFeatures: ['local-floor', 'bounded-floor', 'hand-tracking'] });
        xrMode = mode; await renderer.xr.setSession(s); } catch (e) { b.title = String(e.message || e); }
    };
  }
}
setupXR();
renderer.xr.addEventListener('sessionstart', () => { const ar = xrMode === 'immersive-ar';
  scene.background = ar ? null : BG; room.visible = !ar; table.material.opacity = ar ? 0.35 : 1; $('hud').style.display = 'none';
  buttons.forEach(b => b.visible = true); });
renderer.xr.addEventListener('sessionend', () => { scene.background = BG; room.visible = true; table.material.opacity = 1; xrMode = null; $('hud').style.display = '';
  buttons.forEach(b => b.visible = false); });

// ---------------------------------------------------------------- frame loop
const clock = new THREE.Clock();
let lastT = 0, frame = 0;
layoutPath(); drawTile(); syncButtons();
renderer.setAnimationLoop(() => {
  const dt = Math.min(clock.getDelta(), 0.1), now = clock.elapsedTime; frame++;
  if (playing && D.t_span) { T = Math.min(1, T + dt / 12); if (T >= 1) { playing = false; syncButtons(); } }
  const changed = T !== lastT;
  if (D.t_span) {
    if (T > lastT) for (const f of D.fired) if (lastT < f.t && f.t <= T) showToast(f.label);
    $('tval').textContent = `t = ${fmt(D.t_span[0] + T * (D.t_span[1] - D.t_span[0]))} ${D.time_unit}`;
  }
  lastT = T;
  if (toastT > 0) { toastT -= dt; if (toastT <= 0 && toast) { scene.remove(toast); toast = null; } }
  const u0 = unroll; unroll += (unrollTarget - unroll) * Math.min(1, dt * 5); if (Math.abs(unrollTarget - unroll) < 1e-3) unroll = unrollTarget;
  if (changed || unroll !== u0) layoutPath();
  if (selected >= 0 && changed && frame % 4 === 0) drawTile();
  nodes.forEach(N => { if (Math.abs(N.h - N.hTarget) > 1e-4) { N.h += (N.hTarget - N.h) * Math.min(1, dt * 8); placeTower(N); } });
  edges.forEach(E => { if (!E.pulse.visible) return; const f = (now * 0.35 + E.phase) % 1, x = f * (E.pts.length - 1), i = Math.floor(x);
    E.pulse.position.copy(E.pts[i]).lerp(E.pts[Math.min(i + 1, E.pts.length - 1)], x - i); });
  const ts = selected >= 0 ? 1 : 0; tileS += (ts - tileS) * Math.min(1, dt * 8); tile.scale.setScalar(Math.max(1e-3, tileS)); tile.visible = tileS > 0.01;
  panels.forEach(Pn => { const to = Pn.to || Pn.home, k = Math.min(1, dt * 7);
    Pn.g.position.lerp(to.p, k); Pn.g.quaternion.slerp(to.q, k); Pn.g.scale.setScalar(Pn.g.scale.x + (to.s - Pn.g.scale.x) * k); });
  if (renderer.xr.isPresenting) {
    let hov = null;
    for (const p of xrp) {
      if (!p.connected) continue;
      const [o, d] = ctrlRay(p); const tipv = fingertip(p); p.pos.copy(tipv || o);
      if (p.active) pressMove(p, o, d, p.grab && p.grab.direct ? tipv : null);
      const h = pick(o, d); p.ray.scale.z = h ? h.distance : 1.5; if (h && !hov) hov = h;
    }
    hover(hov, null);
  } else controls.update();
  renderer.render(scene, camera);
});
addEventListener('resize', () => { camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight);
  if (pathMat) pathMat.resolution.set(innerWidth, innerHeight); });
window.__spatial = { D, select, focusPanel, togglePlay, toggleUnroll, globe, camera, controls, get T() { return T; }, set T(v) { T = v; } };
</script></body></html>
"""
