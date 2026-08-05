# pipeline/

Session → scene bundle, on the 4080. Each stage reads and writes files; no
stage imports another's internals.

```
normalize.py   session v2 → canonical frames + intrinsics (one place for the
               ARKit→COLMAP convention flip; never inline it elsewhere)
poses.py       ARKit poses as BA priors → GLOMAP/COLMAP → restore scale+gravity
seed.py        LiDAR depth + confidence → dense init cloud (not SfM sparse)
train.py       gsplat / splatfacto + depth loss + appearance model
clean.py       floaters, crop to bounds, floor snap, opacity/scale pruning
compress.py    → compressed splat + LOD chunks, target ≤80 MB
proxy.py       ARKit mesh or nvblox TSDF → collision.glb
bundle.py      scene.json + artifacts
```

Reuses from `assetpipe`: `scene/backends.py` (SfM, splatfacto, 3dgut),
`scene/splat.py` (PLY parse, denoise, far-field drop), `scene/nvblox_fuse.py`
(TSDF), `scene/rgbd_session.py` (session reader).

Contracts: [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md#stage-contracts)
