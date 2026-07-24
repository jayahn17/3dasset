# Quest 3 → 4080 Pipeline Test Plan

Two phases. **Phase A works today with zero headset code** (Quest system
recorder → video → pipeline). **Phase B** is the live streaming loop via
`services/capture_worker.py`, ready for the Quest capture app.

Everything below runs on the 4080 Linux box after:

```bash
git clone https://github.com/jayahn17/3dasset && cd 3dasset      # or git pull
git checkout claude/3d-asset-recording-quest-e1jvtx

# conda env (pins Python 3.10 + torch/CUDA + YOLO + worker deps)
# install Miniconda first if needed: https://docs.conda.io/en/latest/miniconda.html
bash env/setup_ubuntu.sh                       # creates assetpipe + smoke-tests it
# or manually:
#   conda env create -f env/environment.yml
#   conda activate assetpipe
#   pip install -e .

conda activate assetpipe
```

All `python -m assetpipe …` / `python services/…` commands below assume
`assetpipe` is active (or prefix with `conda run -n assetpipe`).
Full GPU backend walkthrough: `docs/GPU_SETUP.md`.

---

## Phase A — test today with a Quest passthrough recording

### 1. Record on the headset
Meta button → **Camera → Record video**, in passthrough. Slowly walk
around the box (or whatever object) for 15–30 s, keeping it centered.

### 2. Pull the recording to the 4080
```bash
adb devices                       # enable Developer Mode + USB debugging once
adb pull /sdcard/Oculus/VideoShots/ ./quest_videos/
# (or share the video to yourself and download — any path works)
```

### 3. Run the pipeline on it
```bash
python -m assetpipe run \
  --video quest_videos/<recording>.mp4 --fps 2 --dedupe \
  --detector yolo-world --classes "cardboard box,shoe box,book,mug,chair" \
  --location "garage" --box-dims 0.4,0.3,0.3 \
  --out twin_out
python -m assetpipe list --out twin_out
# open twin_out/control_center.html  (offline 3D viewer)
```
- `--dedupe` keeps one asset per label (a walk-around video yields many
  frames of the same object).
- `--detector yolo-world` gives real labels + boxes; drop it to smoke-test
  with the zero-dep heuristic first.
- `--reconstruct trellis` upgrades the cuboid proxy to a textured GLB once
  the TRELLIS server runs (see `docs/GPU_SETUP.md` §3).

### 4. Push the assets into son (twin + /spark room)
```bash
# son running locally: npm start (port 8766) in the son repo
python -m assetpipe export-son --out twin_out \
  --upload local --blob-dir ~/son/docs/visuals/models/assetpipe \
  --blob-base-url http://localhost:8766/docs/visuals/models/assetpipe \
  --endpoint http://localhost:8766/api/quest-asset-scan
```
Every asset lands with `model_3d_ref` set to a fetchable URL — the same
field `/spark`'s live-swap loads meshes from.

**Milestone: a box you filmed on the Quest exists as a labeled, digitalized
asset in the LifeTwin.**

---

## Phase B — live streaming loop (capture worker)

### 1. Start the worker on the 4080
```bash
WORKER_DETECTOR=yolo-world WORKER_RECONSTRUCT=procedural \
PUBLIC_BASE_URL=http://<4080-lan-ip>:8090 \
SON_ENDPOINT=http://localhost:8766/api/quest-asset-scan \
  python services/capture_worker.py
curl http://localhost:8090/health
```

### 2. The client contract (what the Quest app will call)
```bash
# start a capture
SID=$(curl -s -X POST http://<4080>:8090/session/start \
  -H 'Content-Type: application/json' \
  -d '{"label":"box scan","location":"garage"}' | jq -r .session_id)

# stream keyframes (the Unity PCA app posts exactly this)
curl -X POST http://<4080>:8090/session/$SID/keyframe \
  -F image=@frame.jpg \
  -F 'pose=[1,0,0,0.5, 0,1,0,1.2, 0,0,1,2.0, 0,0,0,1]' \
  -F 'intrinsics=[500,500,320,240]'

# finish -> detect + reconstruct + publish + (auto-post to son)
curl -X POST http://<4080>:8090/session/$SID/finish \
  -H 'Content-Type: application/json' \
  -d '{"classes":["cardboard box"],"box_dims":[0.4,0.3,0.3]}'
```
The response lists assets with `model_3d_ref` / `urdf_ref` as HTTP URLs
served by the worker itself (`/blobs/...`) — the Quest browser or `/spark`
can hot-load them immediately, and son gets the scan automatically when
`SON_ENDPOINT` is set.

You can dry-run Phase B **without the headset**: post any JPEGs as
keyframes (that's exactly how it was verified).

### 3. Quest-side capture app (the remaining build)
Unity + Passthrough Camera API recorder per `docs/QUEST3_CAPTURE.md` —
grab PCA frames + pose + intrinsics, POST them to the worker. Until then,
Phase A covers real-headset testing.

---

## Troubleshooting
- **ffmpeg missing** → `pip install imageio-ffmpeg` (auto-detected).
- **YOLO-World first run** downloads weights (~200 MB) — needs network once.
- **Worker unreachable from Quest** → same Wi-Fi/tailnet; check
  `PUBLIC_BASE_URL` uses the LAN IP, not localhost; open port 8090.
- **son endpoint 422** → detections empty; check `--classes` matched objects.
- **VRAM** → see cheat-sheet in `docs/GPU_SETUP.md`.
