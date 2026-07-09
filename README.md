# assetpipe — Quest 3 → Digital-Twin 3D Asset Recording

Point a **Meta Quest 3** at a real object, "record" it, and have it identified,
reconstructed in 3D, and filed into your personal **digital twin** (the *Life
Twin Control Center → Asset*) — so you can always answer *"what did I own, and
where was it?"*

This repo is a **modular, swappable pipeline** for that flow, plus a
**zero-dependency box-test slice that runs today**.

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

```bash
# 1. Run the box test: synthesize boxes → meshes → URDF → twin → viewer
python -m assetpipe demo

# 2. Inspect the catalog (the digital twin)
python -m assetpipe list
python -m assetpipe list --location closet

# 3. Open the control center in a browser (fully offline, drag to orbit)
#    twin_out/control_center.html
```

Output for each recorded object:

```
twin_out/
  twin.db                     # the digital-twin catalog (SQLite)
  control_center.html         # self-contained 3D asset viewer
  <asset_id>/
    model.obj                 # reconstructed mesh
    model.urdf                # simulatable twin (PyBullet/Isaac/Gazebo/MuJoCo)
```

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

## Layout

```
assetpipe/
  capture/      folder + Quest 3 session sources        (CaptureSource)
  detect/       heuristic + YOLO-World + Grounded SAM 2 (Detector)
  reconstruct/  procedural box + TRELLIS + Nerfstudio   (Reconstructor)
  digitalize/   OBJ writer + URDF generator
  catalog/      SQLite twin store + control-center viewer
  integrations/ son_twin.py — bridge to the LifeTwin/JayAsset platform
  pipeline.py   wires the stages together
  cli.py        demo | run | list | viewer | export-son
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
