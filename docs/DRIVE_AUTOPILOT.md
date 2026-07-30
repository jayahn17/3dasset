# Hands-off Google Drive loop (Engin170)

Poll the shared Drive folder → fuse on Linux → size-route GPU → upload RESULT zips back.

Folder: [Engin170_sync](https://drive.google.com/drive/folders/1_HjCyP9-Th3UTjK6Kk89vTuud6ltL4AI)

```text
iPad Share package
      │
      ▼
Google Drive (CrateScan-*.zip)
      │  every ~60s  tools/drive_rgbd_autopilot.py  (per-file pull)
      ▼
captures/inbox/
      │  every ~5s   tools/watch_inbox.py --mode object
      ▼
demo_out/<name>/object_asset/   (curate → views → object.ply + dims.json)
      │  pack *_RESULT.zip
      │
      │  every ~30s  tools/watch_3dgut_queue.py  (GPU route, serial)
      ├─ longest AABB ≤ 24″ → TRELLIS → metric GLB + STL(mm) → *_TRELLIS_RESULT.zip
      └─ longest AABB  > 24″ → 3DGUT train → splat           → *_3DGUT_RESULT.zip
      ▼
Google Drive (rclone) / captures/outbox/
```

Default: **object** fuse + **GPU route** (`ENABLE_GPU_ROUTE=1`, alias `ENABLE_3DGUT=1`).
- Product-scale (bottle, small appliances) → TRELLIS + `dims_mm.json` + `asset_trellis_mm.stl` (Onshape/Fusion import).
- Furniture / scene → 3DGUT.
Full TSDF: `MODE=rgbd bash scripts/start_engin170_autopilot.sh`.
Disable GPU: `ENABLE_GPU_ROUTE=0 bash scripts/start_engin170_autopilot.sh`.
3DGUT-only (skip TRELLIS): `ENABLE_TRELLIS=0 bash scripts/start_engin170_autopilot.sh`.

## Start (Linux)

```bash
cd ~/3dasset
bash scripts/start_engin170_autopilot.sh
tail -f logs/drive_autopilot.log logs/watch_inbox.log logs/watch_3dgut.log
```

Stop:

```bash
kill $(cat logs/drive_autopilot.pid logs/watch_inbox.pid logs/watch_3dgut.pid 2>/dev/null)
```

## TRELLIS metric mm + Onshape

TRELLIS meshes are normalized. After generate, the queue scales the GLB from RGB-D `dims.json` and writes:

- `dims_mm.json` — L×W×H in millimeters
- `asset_trellis.glb` — scaled (meters)
- `asset_trellis_mm.stl` — vertices in **mm** for Onshape / Fusion / SolidWorks mesh import
- `view.html` — HUD shows mm summary

Import STL in Onshape: **Create → Import → Insert as Mesh**. Measure should match `dims_mm.json` (± mesh noise). See also [ONSHAPE.md](ONSHAPE.md).

## One-time: upload back to Drive (rclone)

Download/pull works with the public link (gdown). **Upload** needs your Google account once:

```bash
# if needed
mkdir -p ~/bin && ln -sfn /tmp/rclone-v1.74.4-linux-amd64/rclone ~/bin/rclone
export PATH="$HOME/bin:$PATH"

rclone config
# n) New remote
# name = gdrive
# storage = drive
# scope = drive
# follow prompts (browser auth)
rclone lsf "gdrive,root_folder_id=1_HjCyP9-Th3UTjK6Kk89vTuud6ltL4AI:"
```

Share the folder as **Editor** for the same Google account (Viewer-only cannot upload).

Until rclone is configured, results are still fused locally and copied to
`captures/outbox/*_RESULT.zip` — drag those into Drive manually.

## Measurements (0.25″)

Every fuse also runs `assetpipe measure` on the package `mesh.obj`:

- `demo_out/<name>/dims.json` — AABB + OBB, raw + **rounded to 0.25 in**
- `demo_out/<name>/measurement.txt` — human-readable L×W×H + crate (+2″/side)
- Shown in `scan_view.html` HUD
- Included in `*_RESULT.zip`

Manual:

```bash
python -m assetpipe measure captures/work/CrateScan-ID/CrateScan-ID \
  --out demo_out/CrateScan-ID
```

Object quote comes from on-device `mesh.obj`. Fused TSDF extents are usually
room-scale — use them only as a scene check, not for crate quoting.

## Already processed

- `CrateScan-2A92CC43` → `demo_out/CrateScan-2A92CC43_rgbd/`
- New zips on Drive (e.g. `CrateScan-5C0D0369`) are picked up automatically once autopilot is running.
