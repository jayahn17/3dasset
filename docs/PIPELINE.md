# Offline multi-backend refine pipeline

iPad / Drive zip is **data only**. All reconstruction happens on Linux through
open-source backends, then a versioned refine loop fills holes.

```
capture (zip | session | photos | video)
  → dataset pack (curated frames ± depth/poses)
  → backends: open3d | colmap | trellis | 3dgrut
  → versions/v001 → v002 → …  (merge → hole detect → fill → PNG previews)
```

## Commands

```bash
conda activate assetpipe
cd ~/3dasset

# full run
python -m assetpipe pipeline run captures/done/CrateScan-1B38880A.zip \
  --out demo_out/runs/bottle1 \
  --backends open3d,colmap,trellis,3dgrut \
  --versions 2

# faster smoke (skip heavy / optional services)
python -m assetpipe pipeline run captures/done/CrateScan-1B38880A.zip \
  --out demo_out/runs/bottle1_smoke \
  --backends open3d,colmap \
  --no-generative --versions 2

# preprocess only
python -m assetpipe pipeline preprocess INPUT --out demo_out/runs/x

# more refine cycles later
python -m assetpipe pipeline refine demo_out/runs/bottle1 --versions 1
```

## Dataset pack (`run/dataset/`)

| Path | Meaning |
|------|---------|
| `manifest.json` | kind, depth/pose flags, frame count |
| `frames/` | curated RGB |
| `depth/` | optional mm depth |
| `poses.json` | OpenCV c2w when available |
| `session/` | curated RGB-D session for Open3D |
| `report/CURATION.html` | frame keep/reject review |

## Backends (`run/backends/<name>/`)

| Backend | Needs | Output |
|---------|-------|--------|
| `open3d` | depth + pose | metric `cloud.ply` / `mesh.ply` |
| `colmap` | RGB | SfM `cloud.ply` (non-metric) |
| `3dgrut` | RGB + 3dgrut env | `gaussians.ply` + preview cloud |
| `trellis` | gen3d service | `asset.glb` + sampled cloud |

Failures go into `backends/status.json` and do **not** abort refine if at least
one backend succeeded.

## Versions (`run/versions/vNNN/`)

Each cycle:

1. Merge available backend clouds (metric Open3D anchors scale when present)
2. Isolate object crop
3. Detect holes (voxel border coverage)
4. Geometric fill (Poisson/GLB) then optional TRELLIS fill + ICP rescale
5. Write **PNG previews** — success means the images look like the object

Open `versions/v00N/OPEN_ME.html` and check `qc.json`.

## Honesty rule

Process exit code 0 is not enough. Always inspect `previews/*.png` before
claiming quality (lesson from the failed COLMAP→3DGUT blob run).
