"""assetpipe capture worker — the 4080-side service for live Quest 3 capture.

The headset (or any client) streams keyframes here; finishing a session runs
the detect -> reconstruct -> digitalize pipeline and the resulting assets are
served over HTTP (so the /spark VR room or the twin can hot-load them via
`model_3d_ref`) and optionally auto-posted to son's /api/quest-asset-scan.

    POST /session/start                 {label?, location?}        -> {session_id}
    POST /session/{sid}/keyframe        multipart: image, pose?, intrinsics?
    POST /session/{sid}/finish          {classes?, box_dims?}      -> {assets:[...]}
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
from fastapi.responses import JSONResponse  # noqa: E402
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

    print(f"assetpipe capture worker on :{PORT}  data={DATA}")
    print(f"  detector={DETECTOR}  reconstruct={RECONSTRUCT}")
    print(f"  public_base_url={PUBLIC_BASE_URL}  son={SON_ENDPOINT or '(off)'}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
