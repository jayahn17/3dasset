# CrateScanner ↔ assetpipe bridge

You already have the capture app: [NathanJim17/E170_Client_Project](https://github.com/NathanJim17/E170_Client_Project)
(**CrateScanner**). It uses **ARKit LiDAR** (`sceneReconstruction = .mesh`) and
already enables `frameSemantics.sceneDepth` — it just doesn’t save RGB-D frames
yet. Export today is on-device **STL / OBJ / USDZ** + inch dimensions.

This doc connects that app to the `3dasset` / nvblox server pipeline.

---

## Two paths (both valid)

```text
                    ┌─────────────────────────────────────┐
   iPhone/iPad Pro  │         CrateScanner (ARKit)          │
   (LiDAR required) │  live mesh + ghost box + L×W×H inches │
                    └──────────────┬──────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                                         ▼
     PATH A — fast (MVP today)               PATH B — server RGB-D
     Share STL/OBJ/USDZ                      Sample sceneDepth + RGB + pose
     + measurement.json                      → session/ zip upload
              │                                         │
              ▼                                         ▼
     assetpipe cratescan                     assetpipe rgbd
     (catalog / foam CNC /                   (nvblox or Open3D TSDF
      optional TRELLIS)                       → textured / denser mesh)
```

| | Path A | Path B |
|---|--------|--------|
| Input | OBJ/STL + dims JSON | `session/` (color+depth+pose+K) |
| Where mesh is built | **On device** (ARKit mesh) | **Server** (nvblox/TSDF) |
| Best for | Crate L×W×H, foam CNC | Textured assets, Isaac, polish |
| Status | App already exports mesh | Needs `SessionExporter.swift` in app |

**Recommendation:** ship Path A for the crate client now; add Path B so the same
scan also feeds nvblox without Record3D.

---

## What CrateScanner already does (LiDAR — yes)

From `ScanViewModel.swift`:

```swift
config.sceneReconstruction = .mesh      // live LiDAR mesh
config.frameSemantics.insert(.sceneDepth)  // depth available but unused for export
```

- Gates on LiDAR (`LiDARAvailability`)
- Fits AABB → decimal inches (+ crate padding)
- Exports to-scale STL/OBJ/USDZ (coords baked to **inches**)
- README accuracy note: ±0.25″ is aspirational; validate with a reference

---

## Packaged app (recommended)

Full sources live in this repo:

**[`mobile/CrateScannerApp/`](../mobile/CrateScannerApp/)** — LiDAR mesh + RGB-D recording + **Share full package**.

On Mac: pull `3dasset`, open/merge that folder into Xcode, ⌘R on iPad.  
Details: [`mobile/CrateScannerApp/README.md`](../mobile/CrateScannerApp/README.md)

### On device
1. Orbit (watch **RGB-D** count in the banner) → Place box → Fit → **Capture**
2. Tap **Share full package (mesh + RGB-D)** → AirDrop / Files
3. Zip contains: `mesh.obj`, `measurement.json`, `session/`, `README_LINUX.txt`

### On the 4080 box

```bash
unzip CrateScan-XXXX.zip -d ~/3dasset/captures/scan1
conda activate assetpipe
cd ~/3dasset

# Path A — mesh + dims
python -m assetpipe cratescan ~/3dasset/captures/scan1 --out demo_out/crates/scan1

# Path B — RGB-D fuse (nvblox preferred)
python -m assetpipe rgbd ~/3dasset/captures/scan1/session --inspect-only
python -m assetpipe rgbd ~/3dasset/captures/scan1/session --backend auto \
  --out demo_out/crates/scan1_rgbd
```

---

## Path B notes (already wired in the packaged app)

`SessionExporter` records while scanning; `PackageExporter` builds the zip.
Standalone reference also at [`mobile/CrateScanner/SessionExporter.swift`](../mobile/CrateScanner/SessionExporter.swift).

---

## Customer mandate (no Camera.app / no Record3D)

CrateScanner already **refuses non-LiDAR devices**. Keep scan-only UX:

- No photo library import for measurement  
- Only ARKit LiDAR session produces a valid scan  
- Server rejects RGB-only uploads (`rgbd --inspect-only` exit 2)

---

## Accuracy (same as app README)

Phone LiDAR ≈ **1–3 cm** typical; **±0.25″** is a goal to validate with a known
reference object, not a promise. Use the on-screen inches for crate quoting;
spot-check critical dims with a tape.
