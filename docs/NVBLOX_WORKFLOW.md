# nvblox RGB-D workflow — beat photo-only COLMAP (KIRI-class path)

Goal: **metric dense mesh from LiDAR/depth + RGB + pose**, not sparse SfM dots.
COLMAP stays a fallback for RGB-only dumps. This path is what can compete with
KIRI Engine on geometry (texture/neural polish still optional after).

```
iPhone LiDAR / Quest Depth API
        │  RGB + depth(mm) + pose(4×4) + K
        ▼
 session/manifest.json
        │
        ▼
 assetpipe rgbd  →  nvblox (preferred) or Open3D TSDF
        │
        ▼
 scene.ply / scene_tsdf_mesh.ply / scene_mesh.glb / scan_view.html
        │
        ▼  (optional)
 assetpipe generate … --backend trellis   # watertight underside
```

---

## Status of *your current* gym photos

| Set | Format | Depth | Pose | nvblox-ready? |
|-----|--------|-------|------|----------------|
| `images/Chest/` (50× HEIC) | Camera.app RGB | **No** (`depth_images=[]`) | **No** | **No** |
| `images/leg press/` | Camera.app RGB | **No** | **No** | **No** |

Checked with pillow_heif: **0/50** Chest frames carry LiDAR depth.
**You need a new iPhone LiDAR capture** (or Quest Depth session) before this
pipeline can fuse. Photo-only COLMAP / 3DGUT remains the interim path.

Inspect any folder:

```bash
conda activate assetpipe
cd ~/3dasset
python -m assetpipe rgbd images/Chest --inspect-only
# exit 2 + JSON listing missing: depth, pose, intrinsics
```

---

## 1. Capture — prefer CrateScanner (your app), not Record3D

You already have a LiDAR iOS app:
**[CrateScanner](https://github.com/NathanJim17/E170_Client_Project)** — ARKit
`sceneReconstruction = .mesh`, LiDAR-gated, exports STL/OBJ/USDZ + inch dims.

Full bridge: **[docs/CRATESCANNER_BRIDGE.md](CRATESCANNER_BRIDGE.md)**

| Path | Input | Command |
|------|--------|---------|
| **A (ready)** | OBJ/STL from app | `python -m assetpipe cratescan EXPORT --out …` |
| **B (add SessionExporter)** | RGB-D `session/` | `python -m assetpipe rgbd SESSION --out …` |

Swift reference for Path B: `mobile/CrateScanner/SessionExporter.swift`

### Alternate apps (if not using CrateScanner)

| App | Export |
|-----|--------|
| **Record3D** | `.r3d` (needs importer) |
| **Polycam / 3D Scanner** | mesh or frames |

### Capture tips (gym / machinery)

- Slow orbit, keep the machine filling ~50–70% of the frame
- Diffuse light; tape matte markers on chrome if needed
- Include a known reference for ±0.25″ validation
- Avoid walking so close that LiDAR holes dominate

### Session layout (what `assetpipe rgbd` reads)

```
session/
  manifest.json
  color/0000.jpg  0001.jpg ...
  depth/0000.png  0001.png ...   # uint16 millimeters
```

```jsonc
// manifest.json
{
  "object_hint": "chest press",
  "location": "gym",
  "depth_unit": "mm",
  "frames": [
    {
      "id": "f00000",
      "t": 0.0,
      "color": "color/0000.jpg",
      "depth": "depth/0000.png",
      "pose": [ /* 16 floats, camera-to-world, row-major */ ],
      "intrinsics": [fx, fy, cx, cy]   // depth-image pixel space
    }
  ]
}
```

Same schema as `docs/QUEST3_CAPTURE.md` (Quest PCA + Depth API).

Drop the folder on the 4080 box, e.g. `captures/chest_lidar/session/`.

---

## 2. Install fusion backends

### Open3D TSDF (works today in `assetpipe`)

Already available if Open3D is installed — used automatically when nvblox is not.

### nvblox (preferred on this RTX 4080)

```bash
conda activate assetpipe
python -m pip install \
  "https://github.com/nvidia-isaac/nvblox/releases/download/v0.0.9/nvblox_torch-0.0.9+cu12ubuntu22-py3-none-linux_x86_64.whl"
# Needs libnpp.so.12 on LD_LIBRARY_PATH (conda: nvidia::libnpp=12.x)
python -c "from nvblox_torch.mapper import Mapper; print('ok')"
```

**This box (2026-07-24):** wheel installs, but loading `libpy_nvblox.so` fails with a
**PyTorch ABI mismatch** (`undefined symbol: …ConstantString::create…`) against the
current `assetpipe` torch build. Until that is resolved (match torch build to the
wheel, or rebuild nvblox_torch from source), use:

```bash
python -m assetpipe rgbd SESSION --backend open3d --out rgbd_out
```

Open3D TSDF is the same *method class* (GPU/CPU depth fusion). Swap to `--backend nvblox`
once `Mapper` imports cleanly — no session re-capture needed.

Docs: [nvidia-isaac/nvblox](https://github.com/nvidia-isaac/nvblox),
[nvblox_torch](https://github.com/NVlabs/nvblox_torch).

---

## 3. Run fusion

```bash
conda activate assetpipe
cd ~/3dasset

# Gate only
python -m assetpipe rgbd /path/to/session --inspect-only

# Fuse (auto: nvblox if importable, else Open3D TSDF)
python -m assetpipe rgbd /path/to/session \
  --out demo_out/user_drive/chest_nvblox \
  --voxel-size 0.01
```

Artifacts:

| File | Meaning |
|------|---------|
| `scene.ply` | Dense colored cloud from TSDF |
| `scene_tsdf_mesh.ply` | Volume mesh (preferred surface) |
| `scene_clean.ply` | Background stripped (optional) |
| `scene_mesh.glb` | Quick GLB for viewers |
| `scan_view.html` | Offline orbit viewer |
| `rgbd_meta.json` | Backend + frame counts |

---

## 4. Optional quality stack (to push past KIRI look)

1. **Texture** — OpenMVS `TextureMesh` or Meshroom on `scene_tsdf_mesh.ply` + RGB
2. **Generative fill** — `assetpipe generate` / TRELLIS for unseen undersides
3. **Isaac** — export USD/USDZ; nvblox maps also feed robotics ESDF queries

KIRI wins today on polished mobile UX + texturing. This stack wins on
**open, metric, GPU depth fusion** once capture includes LiDAR.

---

## 5. What *not* to do with the current HEICs

```bash
# This will correctly refuse:
python -m assetpipe rgbd images/Chest --out /tmp/nope
# → missing depth maps, pose, intrinsics
```

For those RGB-only sets, keep using:

```bash
python -m assetpipe scan demo_out/user_drive/chest_jpg_1k --backend 3dgut ...
# or the COLMAP dense path already under demo_out/user_drive/chest_colmap_dense/
```

---

## CLI cheat sheet

```bash
python -m assetpipe rgbd SESSION --inspect-only
python -m assetpipe rgbd SESSION --backend auto|nvblox|open3d --voxel-size 0.01 --out OUT
```
