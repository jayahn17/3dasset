"""assetpipe capture worker — the 4080-side service for live Quest 3 capture.

The headset (or any client) streams keyframes here; finishing a session runs
the detect -> reconstruct -> digitalize pipeline and the resulting assets are
served over HTTP (so the /spark VR room or the twin can hot-load them via
`model_3d_ref`) and optionally auto-posted to son's /api/quest-asset-scan.

    POST /session/start                 {label?, location?}        -> {session_id}
    POST /session/{sid}/keyframe        multipart: image, pose?, intrinsics?
    POST /session/{sid}/finish          {classes?, box_dims?}      -> {assets:[...]}
    GET  /scan                          ONE-BUTTON page: record/pick a video and
                                        WATCH the cloud build while it solves
    POST /scan                          multipart: video -> {scan_id} (job starts)
    GET  /scan/{sid}/status?since=V     growing cloud + final artifact urls
                                        (ply/splat/clean/mesh/viewer)
    GET  /live                          LIVE page: 4 panels — camera, coverage
                                        ring, growing point cloud, generated
                                        asset (needs HTTPS: WORKER_SSL_*)
    GET  /cert  /cert.pem               install this server's cert so phone
                                        browsers will hand over the camera
    POST /live/start                    {backend?} -> {session_id}
    POST /live/{sid}/frame              multipart: image -> {frame, version}
    GET  /live/{sid}/cloud?since=V      latest cloud + coverage angles
    GET  /live/{sid}/asset?since=V      live generated 3D asset (needs the
                                        segment+generate extra; else says so)
    POST /live/{sid}/finish             -> scene.ply/.splat/mesh/viewer
    POST /rgbd/upload                   multipart: package (CrateScan-*.zip from
                                        the iPad) -> queued into captures/inbox
                                        for tools/watch_inbox.py to fuse
    GET  /rgbd/status/{name}            queued | done | failed + artifacts
    GET  /rgbd/queue                    what's waiting / recently processed
    GET  /assets                        catalog dump (id, label, urls)
    GET  /blobs/<asset_id>/<file>       meshes/URDFs, fetchable by Quest//spark
    GET  /health                        backends + counts

RUN (on the 4080)
    pip install fastapi uvicorn python-multipart requests
    WORKER_DETECTOR=yolo-world WORKER_RECONSTRUCT=procedural \
    PUBLIC_BASE_URL=http://<4080-ip>:8090 \
    SON_ENDPOINT=http://localhost:8766/api/quest-asset-scan \
        python services/capture_worker.py

ENV
    WORKER_PORT (8090)   WORKER_DATA (./worker_data)
    WORKER_SSL_CERT / WORKER_SSL_KEY   serve HTTPS (phone camera needs it for
        /live off-localhost):  openssl req -x509 -newkey rsa:2048 -nodes \
        -days 365 -subj /CN=worker -keyout key.pem -out cert.pem
    WORKER_HTTPS_HINT   URL of the HTTPS twin of this worker; the /live page
        links there when opened over plain HTTP (where no camera API exists)
    WORKER_DETECTOR      heuristic | yolo-world           (default heuristic)
    WORKER_RECONSTRUCT   procedural | trellis             (default procedural)
    TRELLIS_ENDPOINT     for WORKER_RECONSTRUCT=trellis
    PUBLIC_BASE_URL      how clients reach this worker    (default http://localhost:PORT)
    SON_ENDPOINT         if set, auto-post finished scans to son (preview by default)
    SON_INGEST=1         persist in son (action=ingest_scan) instead of preview
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # repo root

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from assetpipe.capture.folder import FolderSource  # noqa: E402
from assetpipe.catalog import AssetCatalog, build_viewer  # noqa: E402
from assetpipe.pipeline import AssetPipeline, PipelineConfig  # noqa: E402
from assetpipe.integrations import build_quest_scan_payload, post_scan  # noqa: E402
from assetpipe.integrations.blob import LocalCopyUploader  # noqa: E402

PORT = int(os.environ.get("WORKER_PORT", "8090"))
DATA = os.path.abspath(os.environ.get("WORKER_DATA", "worker_data"))
DETECTOR = os.environ.get("WORKER_DETECTOR", "heuristic")
RECONSTRUCT = os.environ.get("WORKER_RECONSTRUCT", "procedural")
TRELLIS_ENDPOINT = os.environ.get("TRELLIS_ENDPOINT", "http://localhost:8080/generate")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", f"http://localhost:{PORT}").rstrip("/")
SON_ENDPOINT = os.environ.get("SON_ENDPOINT", "")
SON_INGEST = os.environ.get("SON_INGEST", "") in ("1", "true", "yes")

SESS_DIR = os.path.join(DATA, "sessions")
OUT_DIR = os.path.join(DATA, "twin_out")
BLOB_DIR = os.path.join(DATA, "blobstore")
for d in (SESS_DIR, OUT_DIR, BLOB_DIR):
    os.makedirs(d, exist_ok=True)


def _make_detector(classes):
    if DETECTOR == "yolo-world":
        from assetpipe.detect import YoloWorldDetector

        return YoloWorldDetector(classes=classes)
    from assetpipe.detect import HeuristicDetector

    return HeuristicDetector(classes=classes, default_label="object")


def _make_reconstructor(recon_dir, box_dims):
    if RECONSTRUCT == "trellis":
        from assetpipe.reconstruct import TrellisReconstructor

        return TrellisReconstructor(recon_dir, endpoint=TRELLIS_ENDPOINT)
    from assetpipe.reconstruct import ProceduralBoxReconstructor

    return ProceduralBoxReconstructor(recon_dir, default_dims=box_dims)


app = FastAPI(title="assetpipe capture worker")
app.mount("/blobs", StaticFiles(directory=BLOB_DIR), name="blobs")

_sessions: dict[str, dict] = {}


@app.get("/health")
def health():
    return {
        "ok": True,
        "detector": DETECTOR,
        "reconstruct": RECONSTRUCT,
        "public_base_url": PUBLIC_BASE_URL,
        "son_endpoint": SON_ENDPOINT or None,
        "open_sessions": len(_sessions),
    }


@app.post("/session/start")
def session_start(body: dict | None = None):
    body = body or {}
    sid = uuid.uuid4().hex[:10]
    sdir = os.path.join(SESS_DIR, sid)
    os.makedirs(sdir, exist_ok=True)
    _sessions[sid] = {
        "dir": sdir,
        "label": str(body.get("label", "quest capture"))[:120],
        "location": str(body.get("location", ""))[:160] or None,
        "frames": 0,
        "started": time.time(),
        "meta": [],
    }
    return {"ok": True, "session_id": sid}


@app.post("/session/{sid}/keyframe")
async def keyframe(
    sid: str,
    image: UploadFile = File(...),
    pose: str | None = Form(None),        # JSON: 16 floats, 4x4 row-major
    intrinsics: str | None = Form(None),  # JSON: [fx, fy, cx, cy]
):
    sess = _sessions.get(sid)
    if not sess:
        raise HTTPException(404, "unknown session")
    idx = sess["frames"]
    ext = os.path.splitext(image.filename or "f.jpg")[1] or ".jpg"
    path = os.path.join(sess["dir"], f"k{idx:05d}{ext}")
    with open(path, "wb") as fh:
        fh.write(await image.read())
    sess["meta"].append({
        "image": os.path.basename(path),
        "pose": json.loads(pose) if pose else None,
        "intrinsics": json.loads(intrinsics) if intrinsics else None,
        "t": time.time() - sess["started"],
    })
    sess["frames"] = idx + 1
    return {"ok": True, "frame": idx}


@app.post("/session/{sid}/finish")
def finish(sid: str, body: dict | None = None):
    sess = _sessions.pop(sid, None)
    if not sess:
        raise HTTPException(404, "unknown session")
    if sess["frames"] == 0:
        raise HTTPException(422, "session has no keyframes")
    body = body or {}
    classes = body.get("classes") or None
    box_dims = tuple(body.get("box_dims", (0.3, 0.3, 0.3)))

    # persist per-frame metadata (pose/intrinsics) beside the frames
    with open(os.path.join(sess["dir"], "meta.json"), "w") as fh:
        json.dump(sess["meta"], fh)

    cfg = PipelineConfig(out_dir=OUT_DIR, source_name="quest3",
                         location=sess["location"],
                         dedupe_labels=bool(body.get("dedupe", True)))
    catalog = AssetCatalog(os.path.join(OUT_DIR, "twin.db"))
    recon_dir = os.path.join(OUT_DIR, "_meshes")
    pipe = AssetPipeline(
        FolderSource(sess["dir"]),
        _make_detector(classes),
        _make_reconstructor(recon_dir, box_dims),
        catalog, cfg,
    )
    assets = pipe.run()

    # publish meshes under /blobs so Quest//spark can hot-load them
    uploader = LocalCopyUploader(BLOB_DIR, f"{PUBLIC_BASE_URL}/blobs")
    rows = [r for r in catalog.all() if r["asset_id"] in {a.asset_id for a in assets}]
    payload = build_quest_scan_payload(
        rows, scan_id=f"worker-{sid}", label=sess["label"],
        action="ingest_scan" if SON_INGEST else "preview_scan", uploader=uploader,
    )
    # publish URDFs alongside the meshes so urdf_ref is fetchable too
    urdf_by_id = {r["asset_id"]: r["urdf_path"] for r in rows if r.get("urdf_path")}
    for det in payload["detected_assets"]:
        urdf = urdf_by_id.get(det["asset_id"])
        if urdf and os.path.exists(urdf):
            det["urdf_ref"] = uploader.upload(
                urdf, dest_name=f"{det['asset_id']}/{os.path.basename(urdf)}"
            )
    son_result = None
    if SON_ENDPOINT:
        try:
            resp = post_scan(payload, SON_ENDPOINT)
            son_result = {"ok": resp.get("ok"), "persisted": resp.get("persisted")}
        except Exception as e:  # noqa: BLE001
            son_result = {"ok": False, "error": str(e)[:200]}

    build_viewer(catalog, os.path.join(OUT_DIR, "control_center.html"))
    catalog.close()

    return JSONResponse({
        "ok": True,
        "session_id": sid,
        "frames": sess["frames"],
        "assets": [
            {
                "asset_id": d["asset_id"],
                "label": d["label"],
                "model_3d_ref": d["model_3d_ref"],
                "urdf_ref": d["urdf_ref"],
                "dimensions_m": d["dimensions_m"],
            }
            for d in payload["detected_assets"]
        ],
        "son": son_result,
    })


SCAN_BACKEND = os.environ.get("WORKER_SCAN_BACKEND", "auto")

_SCAN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>assetpipe scan</title>
<style>
  :root{--bg:#0e1117;--panel:#161b22;--line:#30363d;--txt:#e6edf3;--dim:#8b949e;--accent:#58a6ff}
  *{box-sizing:border-box;margin:0}
  html,body{height:100%;background:var(--bg);color:var(--txt);
    font:15px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;overflow:hidden}
  #cloud{display:block;width:100vw;height:100vh;touch-action:none}
  #bar{position:fixed;left:0;right:0;bottom:0;display:flex;gap:12px;align-items:center;
    flex-wrap:wrap;padding:12px 16px;background:rgba(22,27,34,.92);
    border-top:1px solid var(--line)}
  label{padding:12px 30px;border-radius:12px;font-size:17px;font-weight:700;
    background:var(--accent);color:#08131f;cursor:pointer}
  input{display:none}
  #st{color:var(--dim);font-size:13px;flex:1;min-width:180px}
  #out a{margin-right:10px;color:var(--accent);text-decoration:none}
  #tip{position:fixed;top:12px;left:12px;right:12px;text-align:center;
    color:var(--dim);font-size:13px;pointer-events:none}
</style></head><body>
<canvas id="cloud"></canvas>
<div id="tip">Record or pick a slow walkaround video — the point cloud builds
here while it reconstructs. On Quest: record passthrough (Meta button &rarr;
Camera), then pick it below.</div>
<div id="bar"><label for="v">&#9679; Scan</label>
<input id="v" type="file" accept="video/*" capture="environment">
<div id="st">waiting for a video…</div><span id="out"></span></div>
<script>__RENDER_JS__</script>
<script>
"use strict";
const inp=document.getElementById("v"),st=document.getElementById("st"),
      out=document.getElementById("out");
const viewer=makeCloudViewer(document.getElementById("cloud"));
let timer=null,version=0;
async function poll(sid){
  const r=await fetch(`scan/${sid}/status?since=${version}`);
  const j=await r.json();
  if(j.version>version&&j.pos){
    version=j.version;
    viewer.setCloud(new Float32Array(b64bytes(j.pos).buffer),b64bytes(j.col));
  }
  if(!j.done){
    st.textContent=`${j.frames||0} frames · ${(j.points||0).toLocaleString()} points`+
      (j.solving?" · solving…":" · extracting…");
    return;
  }
  clearInterval(timer);timer=null;
  if(j.error){st.textContent="failed: "+j.error;return;}
  const r2=j.result;
  st.textContent=`done: ${r2.points.toLocaleString()} points`+
    (r2.clean_points?` → ${r2.clean_points.toLocaleString()} after cleanup`:"")+
    (r2.mesh_faces?` · mesh ${r2.mesh_faces.toLocaleString()} faces`:"");
  for(const[k,u]of Object.entries(r2.urls)){
    const a=document.createElement("a");a.href=u;a.textContent=k;
    if(k!=="viewer")a.setAttribute("download","");out.appendChild(a);}
}
inp.addEventListener("change",async()=>{
  if(!inp.files.length)return;
  const fd=new FormData();fd.append("video",inp.files[0]);
  st.textContent="uploading…";out.innerHTML="";version=0;
  try{
    const r=await fetch("scan",{method:"POST",body:fd});
    const j=await r.json();
    if(!r.ok||!j.ok)throw new Error(j.detail||j.error||r.status);
    timer=setInterval(()=>poll(j.scan_id).catch(()=>{}),1500);
  }catch(e){st.textContent="failed: "+e.message;}
  inp.value="";
});
</script></body></html>"""


@app.get("/scan")
def scan_page():
    from assetpipe.scene.viewer import RENDER_JS

    return HTMLResponse(_SCAN_PAGE.replace("__RENDER_JS__", RENDER_JS))


_scan_jobs: dict[str, dict] = {}


def _run_scan_job(sid: str, vpath: str, out_dir: str, backend: str,
                  fps: float, max_frames: int, clean: bool,
                  width: int) -> None:
    """Video -> frames fed chunk-wise through a LiveScanSession so pollers
    watch the cloud grow -> final artifacts (ply/splat/clean/mesh/viewer)."""
    from assetpipe.capture.video import VideoSource
    from assetpipe.scene import LiveScanSession

    job = _scan_jobs[sid]
    try:
        frames = VideoSource(vpath, work_dir=out_dir, fps=fps,
                             max_frames=max_frames,
                             max_width=width or None).extract()
        sess = LiveScanSession(os.path.join(out_dir, "_scan_work"),
                               backend=backend, solve_every=6, min_frames=8,
                               title=f"scan {sid}")
        job["sess"] = sess
        job["frames"] = len(frames)
        for i, f in enumerate(frames):
            sess.add_frame(f)
            if i % 6 == 5:      # let each incremental solve land -> visible growth
                sess.wait()
        res = sess.finalize(out_dir, splat=True, clean=clean, mesh=True)
        base = f"{PUBLIC_BASE_URL}/blobs/scans/{sid}"
        res["urls"] = {k: f"{base}/{os.path.basename(res[k])}"
                       for k in ("clean_ply", "clean_splat", "ply", "splat",
                                 "mesh", "gaussian_ply", "viewer") if k in res}
        job["result"] = res
    except Exception as e:  # noqa: BLE001 — surface to the polling page
        job["error"] = str(e)[:300]
    finally:
        job["done"] = True


@app.post("/scan")
async def scan(
    video: UploadFile = File(...),
    backend: str = Form(""),
    fps: float = Form(2.0),
    max_frames: int = Form(60),
    clean: bool = Form(True),  # asset pipeline: background removal by default
    width: int = Form(1280),   # SfM sweet spot; 0 keeps full resolution
):
    """Accept the video and return immediately; poll /scan/{sid}/status to
    watch the reconstruction build (and to get the artifact URLs at the end)."""
    import threading

    sid = uuid.uuid4().hex[:10]
    out_dir = os.path.join(BLOB_DIR, "scans", sid)
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(video.filename or "v.mp4")[1] or ".mp4"
    vpath = os.path.join(out_dir, f"capture{ext}")
    with open(vpath, "wb") as fh:
        fh.write(await video.read())
    _scan_jobs[sid] = {"sess": None, "frames": 0, "done": False,
                       "result": None, "error": None}
    threading.Thread(target=_run_scan_job, daemon=True,
                     args=(sid, vpath, out_dir, backend or SCAN_BACKEND,
                           fps, max_frames, clean, width)).start()
    return {"ok": True, "scan_id": sid}


@app.get("/scan/{sid}/status")
def scan_status(sid: str, since: int = 0):
    import base64
    import struct

    job = _scan_jobs.get(sid)
    if job is None:
        raise HTTPException(404, "unknown scan")
    resp: dict = {"ok": True, "done": job["done"], "error": job["error"],
                  "frames": job["frames"], "points": 0, "version": 0,
                  "solving": False}
    sess = job["sess"]
    if sess is not None:
        s = sess.status()
        resp.update(points=s["points"], version=s["version"],
                    solving=s["solving"])
        version, cloud = sess.cloud()
        if version > since and cloud.xyz:
            n = len(cloud.xyz)
            resp["pos"] = base64.b64encode(struct.pack(
                f"<{3 * n}f", *(c for p in cloud.xyz for c in p))).decode("ascii")
            resp["col"] = base64.b64encode(
                bytes(c for p in cloud.rgb for c in p)).decode("ascii")
    if job["done"] and job["result"]:
        resp["result"] = job["result"]
    return resp


# ---------------------------------------------------------------- live scan
# Scaniverse-style feedback loop: the browser streams camera frames, a
# background thread re-solves the scene every few frames, and the page polls
# /cloud to draw the growing point cloud while you keep scanning.

_live: dict[str, "object"] = {}

_LIVE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>assetpipe live scan</title>
<style>
  :root{--bg:#0e1117;--panel:#161b22;--line:#30363d;--txt:#e6edf3;--dim:#8b949e;
    --accent:#58a6ff;--rec:#f85149;--ok:#3fb950}
  *{box-sizing:border-box;margin:0}
  html,body{height:100%;background:var(--bg);color:var(--txt);
    font:15px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;overflow:hidden}
  #grid{position:fixed;inset:0 0 76px 0;display:grid;gap:2px;
    grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;background:var(--line)}
  @media (max-aspect-ratio:3/4){#grid{grid-template-columns:1fr;
    grid-template-rows:1.4fr 1fr 1fr}}
  .cell{position:relative;background:var(--bg);overflow:hidden}
  .cell h2{position:absolute;top:8px;left:10px;z-index:2;font:600 11px/1 inherit;
    letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
  .cell .note{position:absolute;bottom:8px;left:10px;right:10px;z-index:2;
    font-size:11.5px;color:var(--dim)}
  video,canvas{display:block;width:100%;height:100%;object-fit:cover;
    touch-action:none}
  canvas.view{object-fit:contain}
  #camcell{grid-row:span 1}
  #ring{position:absolute;inset:0;pointer-events:none}
  #bar{position:fixed;left:0;right:0;bottom:0;height:76px;display:flex;gap:12px;
    align-items:center;padding:12px 16px;background:rgba(22,27,34,.95);
    border-top:1px solid var(--line)}
  button{padding:12px 28px;border-radius:12px;border:0;font-size:17px;font-weight:700;
    background:var(--accent);color:#08131f;cursor:pointer}
  button.rec{background:var(--rec);color:#fff}
  button:disabled{opacity:.5}
  #st{color:var(--dim);font-size:13px;flex:1;min-width:140px}
  #out a{margin-right:10px;color:var(--accent);text-decoration:none;font-size:13px}
  .big{color:var(--txt);font-variant-numeric:tabular-nums}
</style></head><body>
<div id="grid">
  <div class="cell" id="camcell"><h2>camera</h2>
    <video id="cam" autoplay playsinline muted></video>
    <div class="note" id="camnote">press start — then orbit the object slowly</div>
  </div>
  <div class="cell"><h2>coverage</h2>
    <canvas id="ring" class="view"></canvas>
    <div class="note" id="covnote">angles you've captured appear as you move</div>
  </div>
  <div class="cell"><h2>point cloud</h2>
    <canvas id="cloud" class="view"></canvas>
    <div class="note" id="cloudnote">rebuilds every few seconds</div>
  </div>
  <div class="cell"><h2>3D asset</h2>
    <canvas id="mesh" class="view"></canvas>
    <div class="note" id="meshnote">generator not installed</div>
  </div>
</div>
<div id="bar"><button id="go">&#9679; Start</button>
<div id="st">camera · coverage · point cloud · generated asset — all live</div>
<span id="out"></span></div>
<script>__RENDER_JS__</script>
<script>
"use strict";
const btn=document.getElementById("go"),st=document.getElementById("st"),
      out=document.getElementById("out"),cam=document.getElementById("cam"),
      camnote=document.getElementById("camnote"),
      covnote=document.getElementById("covnote"),
      meshnote=document.getElementById("meshnote");
const cloudView=makeCloudViewer(document.getElementById("cloud"));
const meshView=makeCloudViewer(document.getElementById("mesh"));
const ring=document.getElementById("ring"),rctx=ring.getContext("2d");
const grab=document.createElement("canvas");
let sid=null,version=0,assetVersion=0,timers=[],stream=null,covered=[];

function status(t){st.innerHTML=t;}

function drawRing(){
  const w=ring.clientWidth,h=ring.clientHeight,dpr=window.devicePixelRatio||1;
  if(ring.width!==w*dpr||ring.height!==h*dpr){ring.width=w*dpr;ring.height=h*dpr;}
  const cx=ring.width/2,cy=ring.height/2,r=Math.min(cx,cy)*0.66;
  rctx.clearRect(0,0,ring.width,ring.height);
  rctx.lineWidth=Math.max(8,r*0.16);
  rctx.strokeStyle="#30363d";                       // uncovered track
  rctx.beginPath();rctx.arc(cx,cy,r,0,Math.PI*2);rctx.stroke();
  rctx.strokeStyle="#3fb950";                       // covered arcs
  rctx.lineCap="round";
  const half=6*Math.PI/180;                         // each view lights ~12°
  for(const a of covered){
    const t=(a-90)*Math.PI/180;
    rctx.beginPath();rctx.arc(cx,cy,r,t-half,t+half);rctx.stroke();
  }
  // gap readout: biggest uncovered wedge
  let gap=360;
  if(covered.length>1){
    gap=0;
    for(let i=0;i<covered.length;i++){
      const d=(covered[(i+1)%covered.length]-covered[i]+360)%360;
      if(d>gap)gap=d;
    }
  }else if(covered.length===1)gap=348;
  rctx.fillStyle="#8b949e";
  rctx.font=`${Math.max(11,r*0.22)}px -apple-system,sans-serif`;
  rctx.textAlign="center";rctx.textBaseline="middle";
  rctx.fillText(covered.length?Math.round(360-gap)+"°":"—",cx,cy);
  covnote.textContent=covered.length
    ? (gap>60?`keep going — ${Math.round(gap)}° gap left`:"good coverage ✓")
    : "angles you've captured appear as you move";
}
drawRing();
addEventListener("resize",drawRing);

async function poll(){
  if(!sid)return;
  const r=await fetch(`live/${sid}/cloud?since=${version}`);
  const j=await r.json();
  if(j.version>version&&j.pos){
    version=j.version;
    cloudView.setCloud(new Float32Array(b64bytes(j.pos).buffer),b64bytes(j.col));
  }
  covered=j.coverage||[];drawRing();
  status(`<span class="big">${j.frames}</span> frames · `+
    `<span class="big">${(j.points||0).toLocaleString()}</span> points`+
    (j.solving?" · solving…":"")+
    (j.error&&!j.points?" · need more parallax — keep moving":""));
}
async function pollAsset(){
  if(!sid)return;
  const r=await fetch(`live/${sid}/asset?since=${assetVersion}`);
  const j=await r.json();
  if(!j.available){meshnote.textContent=j.reason||"generator not installed";return;}
  meshnote.textContent=j.generating?"generating…":(j.note||"");
  if(j.version>assetVersion&&j.pos){
    assetVersion=j.version;
    meshView.setCloud(new Float32Array(b64bytes(j.pos).buffer),b64bytes(j.col));
  }
}
async function shoot(){
  if(!sid||cam.videoWidth===0)return;
  grab.width=960;grab.height=Math.round(960*cam.videoHeight/cam.videoWidth);
  grab.getContext("2d").drawImage(cam,0,0,grab.width,grab.height);
  const blob=await new Promise(res=>grab.toBlob(res,"image/jpeg",0.85));
  const fd=new FormData();fd.append("image",blob,"f.jpg");
  await fetch(`live/${sid}/frame`,{method:"POST",body:fd});
}
async function start(){
  if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){
    const hint="__HTTPS_HINT__";
    status(hint
      ? 'the camera needs the secure address — '+
        `<a href="${hint}" style="color:var(--accent)">tap here to reopen</a>`
      : "the camera needs an https:// address — see /cert to trust this server");
    return;
  }
  try{
    stream=await navigator.mediaDevices.getUserMedia(
      {video:{facingMode:"environment",width:{ideal:1280}},audio:false});
  }catch(e){
    status(`camera blocked (${e.name}) — install this server's certificate `+
      '(<a href="cert" style="color:var(--accent)">/cert</a>), or record a '+
      'video and use <a href="scan" style="color:var(--accent)">/scan</a>');
    return;
  }
  cam.srcObject=stream;camnote.textContent="recording — orbit slowly, keep it centered";
  const r=await fetch("live/start",{method:"POST",
    headers:{"Content-Type":"application/json"},body:"{}"});
  sid=(await r.json()).session_id;version=0;assetVersion=0;covered=[];
  out.innerHTML="";
  timers=[setInterval(shoot,700),setInterval(poll,2500),setInterval(pollAsset,4000)];
  btn.textContent="■ Finish";btn.classList.add("rec");
}
async function finish(){
  timers.forEach(clearInterval);timers=[];
  if(stream){stream.getTracks().forEach(t=>t.stop());stream=null;}
  camnote.textContent="stopped";
  btn.disabled=true;status("final solve + cleanup + mesh — hold on…");
  try{
    const r=await fetch(`live/${sid}/finish`,{method:"POST"});
    const j=await r.json();
    if(!r.ok||!j.ok)throw new Error(j.detail||j.error||r.status);
    status(`done · <span class="big">${j.points.toLocaleString()}</span> points`);
    for(const[k,u]of Object.entries(j.urls)){
      const a=document.createElement("a");a.href=u;a.textContent=k;
      if(k!=="viewer")a.setAttribute("download","");out.appendChild(a);}
  }catch(e){status("finish failed: "+e.message);}
  sid=null;btn.disabled=false;
  btn.textContent="● Start";btn.classList.remove("rec");
}
btn.addEventListener("click",()=>sid?finish():start());
</script></body></html>"""


HTTPS_HINT = os.environ.get("WORKER_HTTPS_HINT", "")  # e.g. https://<ip>:8443/live


@app.get("/live")
def live_page():
    from assetpipe.scene.viewer import RENDER_JS

    return HTMLResponse(_LIVE_PAGE
                        .replace("__RENDER_JS__", RENDER_JS)
                        .replace("__HTTPS_HINT__", HTTPS_HINT))


@app.post("/live/start")
def live_start(body: dict | None = None):
    from assetpipe.scene import LiveScanSession

    body = body or {}
    sid = uuid.uuid4().hex[:10]
    _live[sid] = LiveScanSession(
        os.path.join(SESS_DIR, f"live-{sid}"),
        backend=body.get("backend") or SCAN_BACKEND,
        title=f"live scan {sid}",
    )
    return {"ok": True, "session_id": sid}


@app.post("/live/{sid}/frame")
async def live_frame(sid: str, image: UploadFile = File(...)):
    sess = _live.get(sid)
    if sess is None:
        raise HTTPException(404, "unknown live session")
    path = os.path.join(sess.work_dir, f"k{time.time_ns():020d}.jpg")
    with open(path, "wb") as fh:
        fh.write(await image.read())
    idx = sess.add_frame(path)
    s = sess.status()
    return {"ok": True, "frame": idx, "version": s["version"],
            "solving": s["solving"]}


@app.get("/live/{sid}/cloud")
def live_cloud(sid: str, since: int = 0):
    import base64
    import struct

    sess = _live.get(sid)
    if sess is None:
        raise HTTPException(404, "unknown live session")
    s = sess.status()
    resp = {"ok": True, **s}
    version, cloud = sess.cloud()
    if version > since and cloud.xyz:  # only ship points the poller lacks
        n = len(cloud.xyz)
        resp["pos"] = base64.b64encode(
            struct.pack(f"<{3 * n}f", *(c for p in cloud.xyz for c in p))
        ).decode("ascii")
        resp["col"] = base64.b64encode(
            bytes(c for p in cloud.rgb for c in p)
        ).decode("ascii")
    return resp


@app.get("/live/{sid}/asset")
def live_asset(sid: str, since: int = 0):
    """Live generated 3D asset (segment → image-to-3D).

    The generator is an optional GPU extra; until it's installed this
    reports why, and the live page shows the reason in its asset panel.
    """
    if sid not in _live:
        raise HTTPException(404, "unknown live session")
    try:
        from assetpipe.scene.generate import live_asset_preview  # noqa: F401
    except ImportError:
        return {"ok": True, "available": False, "version": 0,
                "reason": "generator not installed — needs Grounded-SAM-2 "
                          "+ TRELLIS (see docs/QUEST3_LIVE_CAPTURE.md)"}
    return live_asset_preview(_live[sid], since)  # pragma: no cover


_CERT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>trust this scanner</title>
<style>
  :root{--bg:#0e1117;--line:#30363d;--txt:#e6edf3;--dim:#8b949e;--accent:#58a6ff}
  *{box-sizing:border-box;margin:0}
  body{min-height:100vh;background:var(--bg);color:var(--txt);padding:28px 22px;
    font:16px/1.6 -apple-system,"Segoe UI",Roboto,sans-serif}
  main{max-width:520px;margin:0 auto}
  h1{font-size:21px;margin-bottom:6px}
  p{color:var(--dim);font-size:14.5px;margin-bottom:20px}
  a.dl{display:block;text-align:center;padding:16px;border-radius:12px;
    background:var(--accent);color:#08131f;font-weight:700;text-decoration:none;
    margin-bottom:24px}
  ol{padding-left:20px;font-size:14.5px}
  li{margin-bottom:10px}
  b{color:var(--txt)}
  code{background:#161b22;border:1px solid var(--line);border-radius:5px;
    padding:1px 6px;font-size:13px}
</style></head><body><main>
<h1>Let this phone use its camera here</h1>
<p>Phone browsers only hand the live camera to sites they trust. This scanner
runs on your own network with its own certificate — install it once and the
live page works.</p>
<a class="dl" href="cert.pem" download="assetpipe-scanner.pem">Download certificate</a>
<ol id="steps">
  <li><b>iPhone / iPad:</b> tap Download above &rarr; open <b>Settings</b> &rarr;
    <b>Profile Downloaded</b> &rarr; <b>Install</b> (enter your passcode).</li>
  <li>Then <b>Settings &rarr; General &rarr; About &rarr; Certificate Trust
    Settings</b> &rarr; switch <b>ON</b> for this certificate.
    <em>This step is required — installing alone is not enough.</em></li>
  <li><b>Android:</b> Download &rarr; <b>Settings &rarr; Security &rarr;
    Encryption &amp; credentials &rarr; Install a certificate &rarr; CA
    certificate</b>.</li>
  <li>Reopen the live page and press Start — the camera prompt will appear.</li>
</ol>
<p style="margin-top:22px">Prefer not to install anything? Record a normal video
and use <a href="scan" style="color:var(--accent)">/scan</a> instead — you just
won't see the reconstruction until after recording.</p>
</main></body></html>"""


@app.get("/cert")
def cert_page():
    return HTMLResponse(_CERT_PAGE)


@app.get("/cert.pem")
def cert_file():
    from fastapi.responses import FileResponse

    # the plain-HTTP worker serves the HTTPS twin's cert too (a public cert is
    # safe over HTTP) — that's where phones land first, so set WORKER_CERT_FILE
    path = os.environ.get("WORKER_CERT_FILE") or os.environ.get("WORKER_SSL_CERT")
    if not path or not os.path.exists(path):
        raise HTTPException(404, "no certificate configured (WORKER_CERT_FILE)")
    return FileResponse(path, media_type="application/x-x509-ca-cert",
                        filename="assetpipe-scanner.pem")


@app.post("/live/{sid}/finish")
def live_finish(sid: str):
    sess = _live.pop(sid, None)
    if sess is None:
        raise HTTPException(404, "unknown live session")
    out_dir = os.path.join(BLOB_DIR, "scans", f"live-{sid}")
    try:
        res = sess.finalize(out_dir, clean=True, mesh=True)  # asset defaults
    except Exception as e:  # noqa: BLE001 — surface solve errors to the page
        raise HTTPException(422, f"live scan failed: {e}") from e
    base = f"{PUBLIC_BASE_URL}/blobs/scans/live-{sid}"
    urls = {k: f"{base}/{os.path.basename(res[k])}"
            for k in ("clean_ply", "clean_splat", "ply", "splat", "mesh",
                      "viewer") if k in res}
    return {"ok": True, "scan_id": f"live-{sid}", "backend": res["backend"],
            "points": res["points"], "urls": urls}


# ------------------------------------------------------- iPad RGB-D packages
# The CrateScanner iPad app posts its finished CrateScan-*.zip here. We do NOT
# fuse inline: the zip is dropped into the same inbox that `rclone` writes to,
# and tools/watch_inbox.py is the single thing that unzips + fuses. That way the
# Tailscale route and the Google Drive route share one code path, and a long
# nvblox run never blocks this request.

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CAPTURES = os.path.abspath(os.environ.get(
    "RGBD_CAPTURES", os.path.join(REPO_ROOT, "captures")))
RGBD_INBOX = os.path.join(CAPTURES, "inbox")
RGBD_STATUS = os.path.join(CAPTURES, "status")
for _d in (RGBD_INBOX, RGBD_STATUS):
    os.makedirs(_d, exist_ok=True)


def _safe_stem(name: str) -> str:
    """Filename-safe stem — never let an upload escape the inbox."""
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    stem = "".join(c for c in stem if c.isalnum() or c in "-_")[:60]
    return stem or f"scan-{uuid.uuid4().hex[:8]}"


@app.post("/rgbd/upload")
async def rgbd_upload(package: UploadFile = File(...)):
    """Accept a CrateScanner package zip and queue it for fusion.

    Written to a .part file and renamed, so the watcher (which polls for stable
    files) can never observe a half-uploaded zip.
    """
    stem = _safe_stem(package.filename)
    final = os.path.join(RGBD_INBOX, f"{stem}.zip")
    if os.path.exists(final):                     # same scan sent twice
        stem = f"{stem}-{uuid.uuid4().hex[:4]}"
        final = os.path.join(RGBD_INBOX, f"{stem}.zip")
    part = final + ".part"

    size = 0
    with open(part, "wb") as fh:
        while chunk := await package.read(1 << 20):   # 1 MB at a time
            fh.write(chunk)
            size += len(chunk)
    if size == 0:
        os.remove(part)
        raise HTTPException(422, "empty upload")
    os.replace(part, final)

    return {"ok": True, "name": stem, "bytes": size,
            "status_url": f"{PUBLIC_BASE_URL}/rgbd/status/{stem}",
            "note": "queued — tools/watch_inbox.py will fuse it"}


@app.get("/rgbd/status/{name}")
def rgbd_status(name: str):
    """Poll one scan. `queued` until the watcher has written its status file."""
    stem = _safe_stem(name)
    path = os.path.join(RGBD_STATUS, f"{stem}.json")
    if not os.path.exists(path):
        queued = os.path.exists(os.path.join(RGBD_INBOX, f"{stem}.zip"))
        return {"ok": True, "name": stem,
                "state": "queued" if queued else "unknown"}
    with open(path) as fh:
        status = json.load(fh)
    status["state"] = "done" if status.get("ok") else "failed"
    return {"ok": True, **status}


@app.get("/rgbd/queue")
def rgbd_queue():
    """What's waiting and what's finished — a quick health view for the iPad."""
    waiting = sorted(f for f in os.listdir(RGBD_INBOX) if f.endswith(".zip"))
    finished = sorted(f[:-5] for f in os.listdir(RGBD_STATUS) if f.endswith(".json"))
    return {"ok": True, "queued": waiting, "processed": finished[-20:],
            "inbox": RGBD_INBOX}


@app.get("/assets")
def assets():
    catalog = AssetCatalog(os.path.join(OUT_DIR, "twin.db"))
    rows = catalog.all()
    catalog.close()
    return {"ok": True, "count": len(rows), "assets": [
        {"asset_id": r["asset_id"], "label": r["label"], "category": r["category"],
         "location": r["location"], "mesh_path": r["mesh_path"]}
        for r in rows
    ]}


if __name__ == "__main__":
    import uvicorn

    ssl_cert = os.environ.get("WORKER_SSL_CERT")
    ssl_key = os.environ.get("WORKER_SSL_KEY")
    print(f"assetpipe capture worker on :{PORT}  data={DATA}"
          f"  {'https' if ssl_cert else 'http'}")
    print(f"  detector={DETECTOR}  reconstruct={RECONSTRUCT}  scan={SCAN_BACKEND}")
    print(f"  public_base_url={PUBLIC_BASE_URL}  son={SON_ENDPOINT or '(off)'}")
    uvicorn.run(app, host="0.0.0.0", port=PORT,
                ssl_certfile=ssl_cert, ssl_keyfile=ssl_key)
