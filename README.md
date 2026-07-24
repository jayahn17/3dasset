# assetpipe — iPad LiDAR RGB-D → nvblox → 3D asset

**Primary path (Mac + iPad + Linux):** scan with **CrateScanner** (Xcode / LiDAR),
send RGB-D to this box, fuse with **nvblox**.

→ **Full Engin170 loop (iPad capture → Linux backend → iPad view-only):**  
> **[docs/ENGIN170_WORKFLOW.md](docs/ENGIN170_WORKFLOW.md)**
>
> → **Short daily Mac checklist:** **[docs/MAC_IPAD_NVBLOX.md](docs/MAC_IPAD_NVBLOX.md)**

```text
iPad capture  →  Linux nvblox (all compute)  →  iPad view-only results
```

App sources: [`mobile/CrateScannerApp/`](mobile/CrateScannerApp/)

---

Also includes a Quest 3 / digital-twin pipeline and a zero-dependency box-test
slice. Older RGB-only photo dumps are **not** the product path.

```
CAPTURE ──▶ IDENTIFY ──▶ RECONSTRUCT ──▶ DIGITALIZE ──▶ ORGANIZE
Quest 3     open-vocab    2D → 3D          mesh + URDF     twin catalog
PCA/Depth   detect+seg    generative /     + metadata      + control center
            (SAM 2)       multi-view                        (SQLite + viewer)
```

Each arrow is an interface with (a) something that runs now and (b) an adapter
stub for the state-of-the-art model. Read
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the full design and the
library choices behind every stage.

## Quickstart (no dependencies)

The box-test slice runs on the **Python 3.10+ stdlib alone** — no install, no
env required. From the repo root:

```bash
# 1. Run the box test: synthesize boxes → meshes → URDF → twin → viewer
python -m assetpipe demo

# 2. Inspect the catalog (the digital twin)
python -m assetpipe list
python -m assetpipe list --location closet

# 3. Open the control center in a browser (fully offline, drag to orbit)
#    twin_out/control_center.html
```

> On systems where `python` is Python 2 or unavailable (many Linux boxes only
> ship `python3`), use `python3` instead — or make the commands above work
> verbatim with the optional conda env below.

### The conda env (one env for everything: `assetpipe`)

Not required for the box test, but everything beyond it (scan, worker, GPU
backends) runs in the single **`assetpipe`** env:

```bash
conda env create -f env/environment.yml        # torch cu121 + pycolmap + the rest
conda activate assetpipe
pip install -e .                                # `assetpipe` + `python -m assetpipe` anywhere

python -m assetpipe demo        # or just:  assetpipe demo
pytest                          # full suite (zero-dep core + scene/scan tests)
```

GPU backend setup (YOLO-World, TRELLIS, Nerfstudio) and VRAM notes:
**[docs/GPU_SETUP.md](docs/GPU_SETUP.md)**.

Output for each recorded object:

```
twin_out/
  twin.db                     # the digital-twin catalog (SQLite)
  control_center.html         # self-contained 3D asset viewer
  <asset_id>/
    model.obj                 # reconstructed mesh
    model.urdf                # simulatable twin (PyBullet/Isaac/Gazebo/MuJoCo)
```

> **Engin170 full loop (Linux = backend, iPad = capture + view-only):**
> **[docs/ENGIN170_WORKFLOW.md](docs/ENGIN170_WORKFLOW.md)**
>
> **Daily Mac checklist:**
> **[docs/MAC_IPAD_NVBLOX.md](docs/MAC_IPAD_NVBLOX.md)**
>
> More detail: [docs/WORKFLOW.md](docs/WORKFLOW.md) ·
> [docs/NVBLOX_WORKFLOW.md](docs/NVBLOX_WORKFLOW.md) ·
> [docs/CRATESCANNER_BRIDGE.md](docs/CRATESCANNER_BRIDGE.md) ·
> [docs/DUAL_MACHINE_PLAYBOOK.md](docs/DUAL_MACHINE_PLAYBOOK.md)

## Scan — Scaniverse-style (click → point cloud → .ply / .splat)

No detector, no classes, no catalog: record a walkaround video, get ONE
point cloud of the scene plus a self-contained viewer:

```bash
python -m assetpipe scan recording.mp4 --splat --clean
# scan_out/scene.ply         colored point cloud (MeshLab/Blender/son)
# scan_out/scene.splat       antimatter15 splat (any web splat viewer)
# scan_out/scene_clean.ply   background removed — the ASSET   (--clean)
# scan_out/scan_view.html    offline viewer — drag orbit / pinch zoom
```

Backends (`--backend`, default `auto` = best installed): `colmap`
(pycolmap sparse SfM, CPU, the reliable default), `vggt` (feed-forward
dense cloud, GPU, seconds), `splatfacto` (nerfstudio-trained gaussians,
GPU, ~10 min), `3dgut` (nv-tlabs/3dgrut 3DGUT + optional USDZ for Isaac),
`stub` (zero-dep plumbing test).

**Auto background removal** (`--clean`, or `assetpipe clean scene.ply` on an
old scan): outliers → RANSAC ground plane → clutter clusters are stripped
automatically, leaving just the object — no manual cropping. The worker does
this by default, so what lands in son is the asset, not the floor.

## Scanner app → generative → real asset (the quality path)

Photogrammetry *measures* what the camera saw; it cannot invent the
underside it never scanned. A generative image-to-3D model can. So: let a
LiDAR scanner app capture (it has depth hardware we don't), isolate the
object here, and let TRELLIS/Hunyuan3D turn it into a watertight textured
asset.

```bash
# Scaniverse -> Export -> PLY
python -m assetpipe views Mouse.ply --out mouse_asset/views   # isolate + orbit renders
python -m assetpipe generate Mouse.ply --backend trellis --out mouse_asset
#   -> mouse_asset/asset_trellis.glb   (Blender / KeyShot / three.js / jay3d)
```

The generator runs as a GPU service in its own env
(`bash env/setup_gen3d.sh`, then `python services/gen3d_server.py`) so its
CUDA extension stack stays out of `assetpipe`. Full command walkthrough:
**[docs/WORKFLOW.md](docs/WORKFLOW.md)**.

Two ways to drive it from a browser (capture worker):

* **`/scan`** — one button: record/pick a walkaround video, links to
  `.ply`/`.splat`/viewer come back when reconstruction finishes.
* **`/live`** — Scaniverse-style: the page streams camera frames while COLMAP
  re-solves in the background, and you *watch the point cloud grow* as you
  move; **Finish** runs the final solve + cleanup. (Phone/laptop browsers
  need HTTPS for the camera — set `WORKER_SSL_CERT`/`WORKER_SSL_KEY`. The
  Quest browser doesn't expose the passthrough camera to web pages, so on the
  headset use `/scan` with a recording — the future on-device capture app can
  stream to the same `/live` endpoints.)

## Run it on real images

```bash
# a folder of photos (phone dry-run, or frames pulled off a Quest recording)
python -m assetpipe run --input ./photos --classes "cardboard box,shoe box" \
    --location "garage" --box-dims 0.4,0.3,0.3
```

Out of the box this uses the zero-dep procedural reconstructor. Select real
backends with flags (all lazy-imported, so the core stays dependency-free):

```bash
# on a GPU box — real detection + textured 3D (see docs/GPU_SETUP.md)
python -m assetpipe run --input photos/ --classes "cardboard box,mug" \
    --detector yolo-world --reconstruct trellis --location "garage"
```

| Stage | Runs now | `--flag` to real backend | Install |
|---|---|---|---|
| Identify | `HeuristicDetector` | `--detector yolo-world` / `grounded-sam2` | `pip install ultralytics` |
| Reconstruct | `ProceduralBoxReconstructor` | `--reconstruct trellis` / `nerfstudio` | GPU service (`services/trellis_server.py`) |
| Capture | `FolderSource` | `--quest-session DIR` | Unity PCA recorder (see docs) |

**GPU box (Ubuntu 22.04 + RTX 4080):** `bash env/setup_ubuntu.sh` sets up the
conda env and smoke-tests it. Full walkthrough — detection → TRELLIS →
Nerfstudio → Quest capture, with VRAM notes — in
**[docs/GPU_SETUP.md](docs/GPU_SETUP.md)**.

## Test with a real Quest 3 — today

Record a passthrough video on the headset (Meta button → Camera → Record),
pull it to the box, and run the whole pipeline on it:

```bash
python -m assetpipe run --video recording.mp4 --fps 2 --dedupe \
    --detector yolo-world --classes "cardboard box,mug" --location garage
```

For the **live loop**, `services/capture_worker.py` is the 4080-side service:
the Quest streams keyframes (`/session/*/keyframe`), finishing a session runs
detect→reconstruct and serves the results at `/blobs/...` — `model_3d_ref`
URLs that son's `/spark` VR room hot-loads, with optional auto-post to
`/api/quest-asset-scan`. Step-by-step:
**[docs/QUEST3_TEST_PLAN.md](docs/QUEST3_TEST_PLAN.md)**.

## Layout

```
assetpipe/
  capture/      folder + video + Quest 3 session sources (CaptureSource)
  detect/       heuristic + YOLO-World + Grounded SAM 2 (Detector)
  reconstruct/  procedural box + TRELLIS + Nerfstudio   (Reconstructor)
  scene/        Scaniverse path: scan/live/clean -> .ply/.splat (SceneBackend)
  digitalize/   OBJ writer + URDF generator
  catalog/      SQLite twin store + control-center viewer
  integrations/ son_twin.py — bridge to the LifeTwin/JayAsset platform
  pipeline.py   wires the stages together
  cli.py        demo | scan | rgbd | clean | run | list | viewer | export-son
docs/           ARCHITECTURE · QUEST3_CAPTURE · GPU_SETUP · ROADMAP · SON_INTEGRATION
tests/          box-test + gpu-glue + son-bridge (zero deps to run)
```

## Fits the existing LifeTwin platform (`son`)

This repo is the **CV + reconstruction compute worker** for the `son`
LifeTwin/JayAsset platform, which already owns the canonical schema, the
`quest-asset-scan` API, the mesh→URDF/USD converter, and the control-center
apps (`jay3d`, `jayasset`, `life-twin`) — but has no code that actually
detects + reconstructs objects. assetpipe produces those detections and feeds
them in:

```bash
python -m assetpipe export-son --endpoint http://localhost:3000/api/quest-asset-scan
```

Verified against son's real handler. Full reconciliation +
field mapping: **[docs/SON_INTEGRATION.md](docs/SON_INTEGRATION.md)**.

## Status

Phase 0 (skeleton + box test) is complete and tested. See
[docs/ROADMAP.md](docs/ROADMAP.md) for the path from "box" to recording
*anything*, rigid or articulated, from the headset.

## Tests

```bash
PYTHONPATH=. python tests/test_pipeline.py      # or: pytest
```
