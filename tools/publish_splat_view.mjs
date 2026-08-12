// Publish the per-asset splat viewer the dashboard expects — with measurement.
//
// web/app/a/[id]/SplatEmbed.tsx builds its viewer URL at runtime by taking the
// asset's .splat URL and swapping the last path segment for "splat_view.html".
// Nothing in the publisher knew that name, so the file was never uploaded and
// EVERY asset's in-page splat panel answered `{"error":"blob 404"}` — six for
// six. It hid for weeks because that URL is built in the browser: it appears in
// no manifest and on no benchmark page, so crawling either reports a healthy
// site.
//
// Run from the repo root (that is where @vercel/blob resolves):
//     BLOB_READ_WRITE_TOKEN=... node tools/publish_splat_view.mjs [assetId ...]
// With no arguments it repairs every asset in the manifest.
//
// Three things here are load-bearing:
//   access: 'private'   the store is private; 'public' fails the upload with
//                       400 "Cannot use public access on a private store".
//   absolute proxy URL  the viewer runs in an iframe served through
//                       /api/download, so a bare basename would resolve against
//                       the app's origin instead of the blob directory.
//   rough measure       the tape here is deliberately labelled approximate. A
//                       splat has no clean surface: on the coffee table one
//                       plane's extent moved 69.8 -> 78.1 in on the opacity
//                       cutoff alone. The METRIC MESH is the authoritative
//                       measuring surface; this one is for orientation.
//   panel over scene    scene_gaussians.splat is the whole ROOM (~30 MB, ~930k
//                       gaussians). scene_panel.splat is the subject, cropped
//                       and floor-stripped (~0.5 MB, ~16k). The asset page was
//                       serving the room and looked far worse than the
//                       benchmark page beside it — same object, different file.

import { put } from '@vercel/blob';

const TOKEN = process.env.BLOB_READ_WRITE_TOKEN || '';
if (!TOKEN) {
  console.error('BLOB_READ_WRITE_TOKEN is empty — nothing published.');
  process.exit(1);
}
const STORE = 'https://sf4pvvi6x7hevyhw.private.blob.vercel-storage.com';
const only = new Set(process.argv.slice(2));
const auth = { headers: { authorization: `Bearer ${TOKEN}` } };
const bust = (u) => (u.includes('?') ? `${u}&cache=0` : `${u}?cache=0`);
const grab = async (u) => {
  const r = await fetch(bust(u), auth);
  if (!r.ok) throw new Error(`${r.status} ${u}`);
  return r;
};
const alive = async (u) => {
  try { return (await fetch(bust(u), { ...auth, method: 'HEAD' })).ok; }
  catch { return false; }
};

/** Mirrors SplatEmbed.pickSplat. If these disagree the panel 404s again. */
function pickSplat(asset) {
  const splats = asset.files.filter((f) => /\.splat$/i.test(f.name));
  if (!splats.length) return null;
  return (
    splats.find((f) => f.name === 'scene_gaussians.splat') ??
    [...splats].sort((a, b) => (b.bytes || 0) - (a.bytes || 0))[0]
  );
}

function viewerHtml(splatUrl, title, radius, metric) {
  const r = Math.max(Number(radius) || 2.0, 0.35);
  const d = r * 2.5;
  const cam = [d * 0.62, d * 0.36, d * 0.70];
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>splat viewer — ${title}</title>
<style>
 html,body{margin:0;height:100%;background:#0b0d10;color:#e6edf3;
   font:13px/1.4 -apple-system,Segoe UI,sans-serif;overflow:hidden}
 #c{position:fixed;inset:0}
 #hud{position:fixed;top:10px;left:10px;z-index:3;background:rgba(20,24,30,.88);
   border:1px solid #30363d;border-radius:8px;padding:8px 12px;max-width:min(78vw,420px)}
 #hud b{color:#58a6ff} .dim{color:#8b949e;font-size:12px} #err{color:#ff7b72;white-space:pre-wrap}
 #mbtn{margin-top:7px;background:#1b2029;border:1px solid #30363d;color:#e6edf3;
   border-radius:6px;padding:4px 11px;cursor:pointer;font:inherit}
 #mbtn[data-on="1"]{background:#5c2320;border-color:#8d3a34;color:#ffb4a8}
 #mbtn:hover{border-color:#58a6ff}
 #rows{margin-top:7px;display:flex;flex-direction:column;gap:4px}
 .row{display:flex;align-items:center;gap:7px;background:#2a1516;border:1px solid #5c2320;
   border-radius:6px;padding:2px 7px;font-size:12px;color:#ff9c8a}
 .row span{color:#ffd9d2;font-variant-numeric:tabular-nums}
 .row button{background:none;border:0;color:#ff9c8a;cursor:pointer;font:inherit;padding:0 2px}
 .row button:hover{color:#fff}
 .lab{position:fixed;z-index:2;transform:translate(-50%,-50%);pointer-events:none;
   background:rgba(12,15,20,.9);border:1px solid #5c2320;border-radius:5px;
   padding:1px 6px;font-size:12px;color:#ffd9d2;white-space:nowrap;
   font-variant-numeric:tabular-nums}
</style></head><body>
<div id="hud"><b>${title}</b> · Splat
<div class="dim">drag tumble · scroll zoom · right-drag pan</div>
<button id="mbtn" data-on="0">Rough measure: off</button>
<div class="dim" id="mhelp" style="display:none">click two points · drag an end to adjust<br>
<b style="color:#d8a04c">Approximate.</b> A splat is a cloud, not a surface — the
same edge can read inches differently depending on where you click. Use the
mesh for sizes you intend to act on.</div>
<div id="rows"></div>
<div id="err"></div></div>
<div id="c"></div>
<script type="importmap">
{"imports":{
 "three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
 "three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/",
 "@mkkellogg/gaussian-splats-3d":"https://cdn.jsdelivr.net/npm/@mkkellogg/gaussian-splats-3d@0.4.6/build/gaussian-splats-3d.module.js"
}}
</script>
<script type="module">
import * as THREE from 'three';
import { TrackballControls } from 'three/addons/controls/TrackballControls.js';
import * as GaussianSplats3D from '@mkkellogg/gaussian-splats-3d';

const err = document.getElementById('err');
const url = new URL(${JSON.stringify(splatUrl)}, window.location.href).href;
const R = ${r};
const CAM = ${JSON.stringify(cam)};
const METRIC = ${metric ? 'true' : 'false'};
const IN = 0.0254;

try {
  // Our own scene, so measurement geometry has somewhere to live that the
  // splat renderer will still draw.
  const overlay = new THREE.Scene();
  const viewer = new GaussianSplats3D.Viewer({
    rootElement: document.getElementById('c'),
    threeScene: overlay,
    sharedMemoryForWorkers: false, gpuAcceleratedSort: false,
    useBuiltInControls: false, cameraUp: [0,1,0],
    initialCameraPosition: CAM, initialCameraLookAt: [0,0,0],
  });
  window.__splatViewer = viewer;
  await viewer.addSplatScene(url, { showLoadingUI: true, progressiveLoad: false,
                                    splatAlphaRemovalThreshold: 1 });

  const cam = viewer.camera;
  cam.up.set(0,1,0); cam.position.fromArray(CAM);
  cam.near = Math.max(R/200, 0.01); cam.far = Math.max(R*40, 50);
  cam.updateProjectionMatrix();

  const ctr = new TrackballControls(cam, viewer.renderer.domElement);
  ctr.target.set(0,0,0); ctr.rotateSpeed = 2.2; ctr.panSpeed = 0.6; ctr.update();
  window.__splatControls = ctr;

  // ---------------------------------------------------------------- measure
  // A splat is not a mesh, so three's Raycaster cannot touch it. The splat
  // library carries its own: intersectSplatMesh returns hits whose .origin is
  // a world-space point on the ellipsoid actually under the cursor. Distances
  // are true metres — ply_to_splat only flips and translates, never scales.
  const canvas = viewer.renderer.domElement;
  const dims = new THREE.Vector2();
  const hits = [];
  const DOT = 0.006 * Math.max(R, 0.35);

  function hitAt(ev) {
    const rect = canvas.getBoundingClientRect();
    viewer.getRenderDimensions(dims);
    hits.length = 0;
    viewer.raycaster.setFromCameraAndScreenPosition(
      cam, new THREE.Vector2(ev.clientX - rect.left, ev.clientY - rect.top), dims);
    viewer.raycaster.intersectSplatMesh(viewer.splatMesh, hits);
    return hits.length ? hits[0].origin.clone() : null;
  }

  const RED = 0xff3b30;
  const dotGeo = new THREE.SphereGeometry(DOT, 12, 10);
  const dotMat = new THREE.MeshBasicMaterial({ color: RED, depthTest: false });
  const lineMat = new THREE.LineBasicMaterial({ color: RED, depthTest: false });
  const rows = document.getElementById('rows');
  const measures = [];
  let measuring = false, pending = null, pendingDot = null, dragging = null, seq = 0;

  function mkDot(p) {
    const m = new THREE.Mesh(dotGeo, dotMat);
    m.position.copy(p); m.renderOrder = 999; overlay.add(m); return m;
  }
  function refresh(mm) {
    mm.line.geometry.setFromPoints([mm.a.position, mm.b.position]);
    mm.line.geometry.attributes.position.needsUpdate = true;
    const d = mm.a.position.distanceTo(mm.b.position);
    mm.dist = d;
    mm.row.querySelector('span').textContent =
      METRIC ? d/IN >= 12 ? \`\${(d/IN).toFixed(1)} in  (\${d.toFixed(3)} m)\`
                          : \`\${(d/IN).toFixed(2)} in  (\${d.toFixed(3)} m)\`
             : \`\${d.toFixed(3)} (relative)\`;
    mm.lab.textContent = METRIC ? \`~\${(d/IN).toFixed(1)} in\` : \`~\${d.toFixed(2)}\`;
  }
  function addMeasure(pa, pb) {
    const n = ++seq;
    const a = mkDot(pa), b = mkDot(pb);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([pa, pb]), lineMat);
    line.renderOrder = 998; overlay.add(line);
    const lab = document.createElement('div'); lab.className = 'lab'; document.body.appendChild(lab);
    const row = document.createElement('div'); row.className = 'row';
    row.innerHTML = \`<b>\${n}</b><span></span>\`;
    const del = document.createElement('button'); del.textContent = '\\u00d7'; del.title = 'delete';
    row.appendChild(del); rows.appendChild(row);
    const mm = { n, a, b, line, lab, row };
    del.onclick = () => {
      overlay.remove(a); overlay.remove(b); overlay.remove(line);
      lab.remove(); row.remove();
      measures.splice(measures.indexOf(mm), 1);
    };
    measures.push(mm); refresh(mm);
  }
  function handleAt(ev) {
    // pick the nearest endpoint within a few screen pixels
    const rect = canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    let best = null, bestD = 14;
    for (const mm of measures) for (const h of [mm.a, mm.b]) {
      const v = h.position.clone().project(cam);
      const sx = (v.x*0.5+0.5)*rect.width, sy = (-v.y*0.5+0.5)*rect.height;
      const d = Math.hypot(sx-mx, sy-my);
      if (d < bestD) { bestD = d; best = { h, mm }; }
    }
    return best;
  }

  const btn = document.getElementById('mbtn');
  const help = document.getElementById('mhelp');
  btn.onclick = () => {
    measuring = !measuring;
    btn.dataset.on = measuring ? '1' : '0';
    btn.textContent = 'Measure: ' + (measuring ? 'on' : 'off');
    help.style.display = measuring ? '' : 'none';
    if (!measuring && pendingDot) { overlay.remove(pendingDot); pendingDot = null; pending = null; }
  };

  canvas.addEventListener('pointerdown', (ev) => {
    if (!measuring || ev.button !== 0) return;
    const grabbed = handleAt(ev);
    if (grabbed) { dragging = grabbed; ctr.enabled = false; ev.stopPropagation(); return; }
    const p = hitAt(ev);
    if (!p) return;
    ev.stopPropagation();
    if (!pending) { pending = p; pendingDot = mkDot(p); }
    else {
      overlay.remove(pendingDot); pendingDot = null;
      addMeasure(pending, p); pending = null;
    }
  }, true);
  canvas.addEventListener('pointermove', (ev) => {
    if (!dragging) return;
    const p = hitAt(ev);
    if (p) { dragging.h.position.copy(p); refresh(dragging.mm); }
  }, true);
  const release = () => { if (dragging) { dragging = null; ctr.enabled = true; } };
  canvas.addEventListener('pointerup', release, true);
  canvas.addEventListener('pointerleave', release, true);

  // labels follow their midpoints
  const prev = viewer.update.bind(viewer);
  const mid = new THREE.Vector3();
  viewer.update = (...a) => {
    ctr.update();
    const rect = canvas.getBoundingClientRect();
    for (const mm of measures) {
      mid.addVectors(mm.a.position, mm.b.position).multiplyScalar(0.5).project(cam);
      mm.lab.style.left = ((mid.x*0.5+0.5)*rect.width + rect.left) + 'px';
      mm.lab.style.top  = ((-mid.y*0.5+0.5)*rect.height + rect.top) + 'px';
      mm.lab.style.display = mid.z > 1 ? 'none' : '';
    }
    return prev(...a);
  };
  viewer.start();
} catch (e) {
  err.textContent = 'Failed to load splat: ' + e;
  console.error(e);
}
</script></body></html>`;
}

const manifest = await (await grab(`${STORE}/dashboard/manifest.json`)).json();
const assets = manifest.assets || manifest.cards || [];
let ok = 0, skipped = 0, failed = 0;

for (const asset of assets) {
  if (only.size && !only.has(asset.id)) continue;
  const splat = pickSplat(asset);
  if (!splat) { console.log(`  --   ${asset.id}: no splat`); skipped++; continue; }

  // What SplatEmbed derives — this is where the file MUST land.
  const pathname = new URL(splat.url).pathname.replace(/^\//, '')
                                              .replace(/[^/]+$/, 'splat_view.html');

  // What the viewer should SHOW: the cropped panel if one exists anywhere,
  // else the full scene. The panel is ~60x smaller and is the subject rather
  // than the room.
  let show = splat, showName = splat.name;
  const panelInAsset = asset.files.find((f) => f.name === 'scene_panel.splat');
  const benchPanel = `${STORE}/dashboard/benchmark/${asset.id}/scene_panel.splat`;
  if (panelInAsset) { show = panelInAsset; showName = panelInAsset.name; }
  else if (await alive(benchPanel)) { show = { url: benchPanel, name: 'scene_panel.splat' }; showName = 'scene_panel.splat'; }

  // Radius sets the camera distance. The sidecar meta is the cheap source, but
  // it is not always published beside the panel (the chair's was missing, so the
  // viewer fell back to a 2 m default and framed a 0.77 m object from 5 m away).
  // Falling back to measuring the splat itself makes the framing independent of
  // whether anyone remembered to publish a .meta.json.
  let radius = null;
  const metaUrl = show.url.replace(/[^/]+$/, `${showName}.meta.json`);
  try { radius = Number((await (await grab(metaUrl)).json()).radius_m) || null; }
  catch { /* fall through to measuring it */ }
  if (!radius) {
    try {
      const head = await fetch(bust(show.url), { ...auth, method: 'HEAD' });
      const bytes = Number(head.headers.get('content-length') || 0);
      if (bytes && bytes < 8_000_000) {          // panels are ~0.5 MB; never pull a 30 MB scene
        const buf = new Float32Array(await (await grab(show.url)).arrayBuffer());
        const n = Math.floor(buf.length / 8);    // .splat = 32 bytes: xyz, scale, rgba, quat
        const d = new Float64Array(n);
        for (let i = 0; i < n; i++) {
          const x = buf[i*8], y = buf[i*8+1], z = buf[i*8+2];
          d[i] = Math.sqrt(x*x + y*y + z*z);
        }
        d.sort();
        radius = Math.max(d[Math.floor(n * 0.95)] || 0, 0.25);
        console.log(`       (radius measured from the splat: ${radius.toFixed(2)} m)`);
      }
    } catch { /* the viewer has a sane default */ }
  }

  const metric = !!(asset.dims || asset.metric);
  const proxied = `/api/download?url=${encodeURIComponent(show.url)}&name=${showName}`;
  const title = asset.id.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

  try {
    await put(pathname, viewerHtml(proxied, title, radius, metric), {
      access: 'private', token: TOKEN, addRandomSuffix: false,
      allowOverwrite: true, contentType: 'text/html; charset=utf-8',
    });
    console.log(`  ok   ${asset.id.padEnd(22)} shows ${showName.padEnd(21)} r=${radius ? radius.toFixed(2) : '?'}`);
    ok++;
  } catch (e) {
    console.log(`  FAIL ${asset.id}  ${String(e.message).slice(0, 140)}`);
    failed++;
  }
}
console.log(`\n${ok} published, ${skipped} without a splat, ${failed} failed`);
process.exit(failed ? 1 : 0);
