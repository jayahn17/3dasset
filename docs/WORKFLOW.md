# Terminal workflow — capture → point cloud → cleaned asset → mesh

Every command below is copy-pasteable on the 4080 box. One conda env
(`assetpipe`) runs everything.

## 0. One-time setup (already done on this box)

```bash
conda env create -f env/environment.yml          # torch cu121 + pycolmap + deps
conda activate assetpipe
pip install -e .                                 # `assetpipe` CLI anywhere
conda env config vars set -n assetpipe PYTHONNOUSERSITE=1   # keep ~/.local out
conda env config vars set -n assetpipe YOLO_AUTOINSTALL=false  # no env mutation
# optional extras: meshing quality + dense backends
pip install open3d                               # Poisson meshing (else: alpha shape)
```

Every session starts with:

```bash
conda activate assetpipe
cd ~/3dasset
```

> ROS leaks into `PYTHONPATH` from the shell profile on this box. If imports
> break, prefix commands with `PYTHONPATH=~/3dasset` (or empty).

## 1. Scan a video → point cloud (+ cleaned asset + mesh)

Record a slow 30–45 s orbit (phone or Quest passthrough: Meta button →
Camera → Record), get the file onto the box, then:

```bash
python -m assetpipe scan capture.mp4 \
    --splat --clean --mesh \
    --fps 2.5 --max-frames 75 --width 1280 \
    --out scan_out
```

| flag | what it does | default |
|---|---|---|
| `--backend` | `auto` → vggt > colmap > stub | `auto` |
| `--fps` | frames sampled per second of video | 2.0 |
| `--max-frames` | cap total frames | 60 |
| `--width` | downscale frames (0 = full res; 1080p+ can OOM SIFT) | 1280 |
| `--splat` | also write `scene.splat` | off |
| `--clean` | auto background removal → `scene_clean.ply` | off |
| `--mesh` | vertex-colored GLB via Poisson → `scene_mesh.glb` | off |

Memory knob: `ASSETPIPE_COLMAP_THREADS=4` before the command caps COLMAP's
SIFT/mapping threads (default 8; box has 32 cores but ~14 GB free RAM).

Outputs land in `scan_out/`:

```
scene.ply          raw colored point cloud
scene.splat        antimatter15 splat
scene_clean.ply    background removed — THE asset
scene_clean.splat  cleaned splat
scene_mesh.glb     vertex-colored mesh (Blender/three.js/KeyShot/jay3d)
scan_view.html     offline viewer — open in any browser
```

## 1b. Scanner app → real asset (the good path)

A phone LiDAR scanner (Scaniverse, Polycam) beats our SfM at *capture* —
it has depth hardware and tracked poses. So let it capture, and use this
box as the asset factory:

```bash
# Scaniverse: scan the object, Share -> Export -> PLY.  Then:
python -m assetpipe views Mouse.ply --out mouse_asset/views   # isolate + render
```

That strips the far-field shell, the desk it sits on and the room, and
renders orbit views of just the object — the conditioning images an
image-to-3D model wants. `--focus` is the knob (raise if the object gets
clipped, lower if clutter creeps in); cropping in the scanner app first is
even more reliable.

Then generate the actual asset (needs the generator service, below):

```bash
python -m assetpipe generate mouse_asset/views --backend trellis  --out mouse_asset
python -m assetpipe generate mouse_asset/views --backend hunyuan3d --out mouse_asset
python -m assetpipe generate Mouse.ply --backend trellis --out mouse_asset  # both steps at once
```

**Why generate at all?** A scan only measures what the camera saw — the
underside is a hole, and Poisson meshing can only interpolate. The
generative model supplies clean watertight topology and completes the
surfaces the capture never saw. Multi-view conditioning (default 4 of the
rendered views) keeps it faithful to *your* object rather than a lookalike.

### The generator service (own env — see env/setup_gen3d.sh)

```bash
bash env/setup_gen3d.sh              # once: gen3d env + TRELLIS + Hunyuan3D (~1 h)
conda activate gen3d
GEN3D_TEXTURE=1 python services/gen3d_server.py       # serves :8080
curl -s localhost:8080/health
```

16 GB VRAM holds one generator at a time — the service loads lazily and
evicts the other. If it OOMs: fewer `--views`, smaller `texture_size`, or
`HUNYUAN_MODEL=tencent/Hunyuan3D-2mini`.

## 2. Re-clean or re-mesh an existing scan

```bash
python -m assetpipe clean scan_out/scene.ply --splat     # background removal only
python - <<'EOF'                                          # mesh any cleaned ply
from assetpipe.scene import read_ply, cloud_to_mesh
xyz, rgb = read_ply("scan_out/scene_clean.ply")
print(cloud_to_mesh(xyz, rgb, "scan_out/scene_mesh.glb"))
EOF
```

## 3. Phone / Quest browser flow (capture worker)

```bash
# HTTP worker (record-or-pick upload; watch the cloud build on the page)
WORKER_PORT=8090 WORKER_DATA=~/3dasset/worker_data \
PUBLIC_BASE_URL=http://192.168.0.41:8090 \
WORKER_HTTPS_HINT=https://192.168.0.41:8443/live \
python services/capture_worker.py

# HTTPS twin (live camera streaming needs a secure context)
openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj /CN=192.168.0.41 \
    -keyout worker_data/certs/key.pem -out worker_data/certs/cert.pem   # once
WORKER_PORT=8443 WORKER_DATA=~/3dasset/worker_data \
PUBLIC_BASE_URL=https://192.168.0.41:8443 \
WORKER_SSL_CERT=worker_data/certs/cert.pem \
WORKER_SSL_KEY=worker_data/certs/key.pem \
python services/capture_worker.py
```

Then on the phone/Quest (same Wi-Fi):

* `http://192.168.0.41:8090/scan` — record/pick a video, watch the cloud
  build, download ply/splat/clean/mesh.
* `https://192.168.0.41:8443/live` — live streaming (phone camera; Quest
  browser only if the OS exposes cameras to web pages — see
  [QUEST3_LIVE_CAPTURE.md](QUEST3_LIVE_CAPTURE.md)).

Same thing from the terminal (no browser):

```bash
SID=$(curl -s -X POST http://192.168.0.41:8090/scan \
      -F video=@capture.mp4 -F fps=2.5 | python3 -c \
      "import sys,json;print(json.load(sys.stdin)['scan_id'])")
watch -n 3 "curl -s http://192.168.0.41:8090/scan/$SID/status | python3 -m json.tool | head -8"
# artifacts: worker_data/blobstore/scans/$SID/
```

## 4. Identify & catalog objects (the twin path, GPU)

```bash
python -m assetpipe run --input scan_out/frames \
    --detector yolo-world --classes "keyboard,mouse,box" \
    --location "desk" --dedupe
python -m assetpipe list                      # what's in the twin
# push to son:
python -m assetpipe export-son --endpoint http://localhost:3000/api/quest-asset-scan
```

## 5. Tests

```bash
PYTHONPATH=~/3dasset python -m pytest tests/ -q
```

## Capture tips (small/dark/glossy objects)

Textured surface under the object (newspaper, patterned blanket), slow
close orbit with lots of overlap, even light, no glare, don't move the
object. Sparse SfM fails on featureless/blurry frames; `--fps 3` and more
frames help weak captures.
