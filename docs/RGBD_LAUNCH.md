# RGB-D fuse launch (iPad + open-source demo)

Linux backend for Engin170: when an iPad RGB-D package arrives, fuse it
(prefer **nvblox**; Open3D TSDF if `nvblox_torch` cannot load).

## Quick launch (iPad package)

```bash
conda activate assetpipe
cd ~/3dasset

# one-shot
bash scripts/launch_rgbd_fuse.sh captures/CrateScan-XXXX.zip demo_out/ipad1

# or auto-watch folder (leave running)
bash scripts/watch_ipad_captures.sh
# then scp zip into ~/3dasset/captures/
```

Uses the same `session/` schema as CrateScanner **Share full package**.

## Open-source demo (no iPad required)

Proves the pipeline with public RGB-D (Open3D Redwood / Lounge / Fountain):

```bash
# small smoke (5 frames)
bash scripts/demo_opensource_rgbd.sh redwood

# richer room (default 60 frames, override with MAX_FRAMES=40)
MAX_FRAMES=40 bash scripts/demo_opensource_rgbd.sh lounge
```

Results land in `demo_out/opensource_<name>_rgbd/`:

| File | Meaning |
|------|---------|
| `scan_view.html` | Orbit viewer — open in browser |
| `scene_tsdf_mesh.ply` | TSDF mesh |
| `scene_mesh.glb` | GLB for Quick Look / three.js |
| `scene.ply` | Dense cloud |
| `rgbd_meta.json` | Backend used + frame count |

Convert only (no fuse):

```bash
python scripts/rgbd_session_from_open3d.py --dataset lounge --max-frames 40 \
  --out captures/opensource_lounge_session
```

## nvblox vs Open3D on this box

| Backend | Status |
|---------|--------|
| **nvblox** | Target. `nvblox_torch` wheel is installed but currently **ABI-mismatched** with `assetpipe` torch (`ConstantString` / cxx11). |
| **Open3D TSDF** | Working fallback — **same method class** (depth+pose fusion). Used by demos until nvblox loads. |

```bash
BACKEND=nvblox bash scripts/launch_rgbd_fuse.sh SESSION OUT   # force (fails until ABI fixed)
BACKEND=auto  bash scripts/launch_rgbd_fuse.sh SESSION OUT   # prefer nvblox, else open3d
BACKEND=open3d bash scripts/launch_rgbd_fuse.sh SESSION OUT
```

Fixing nvblox: match torch build to the wheel, or rebuild `nvblox_torch` against current torch — then `--backend nvblox` with **no session format change**.

## Tie-in

- Full product loop: [ENGIN170_WORKFLOW.md](ENGIN170_WORKFLOW.md)
- Mac checklist: [MAC_IPAD_NVBLOX.md](MAC_IPAD_NVBLOX.md)
