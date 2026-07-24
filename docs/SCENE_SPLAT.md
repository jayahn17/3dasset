# Scene splat — the photoreal room shell

Two different things are called "3D" in this project, and they are built by
opposite methods:

| deliverable | method | command |
|---|---|---|
| **the digital closet** — each object as a clean, watertight, KeyShot-ready asset | **generative** (TRELLIS/Hunyuan3D) — completes surfaces the camera never saw | `assetpipe room`, `assetpipe generate` |
| **the digital *scene*** — the whole room, photoreal, navigable | **reconstruction** (gaussian splatting) — captures exactly what the cameras saw | `assetpipe scan --backend splatfacto` |

Reconstruction loses for *objects* (holey undersides, baked lighting — see
[[gen3d-generative-pipeline]]), which is why the closet is generative. But it
*wins* for the room shell: a trained gaussian splat is the photoreal thing you
walk through, and generative models can't invent a whole room coherently. They
are complementary — **splat = the room you walk through; TRELLIS = the clean
objects inside it** — and the room-sweep tracker already knows where each
object sits in that shell (`docs/ROOM_SWEEP.md`).

## Why splatting, not NeRF

Gaussian splatting is the evolved NeRF: real-time to render, editable, higher
fidelity, and this repo already renders gaussians (`scene/splat.py`). Instant-
NGP is 2022 tech. The whole splat path here is nerfstudio's **splatfacto**.

## Run it

```bash
assetpipe scan room.mp4 --backend splatfacto --out room_shell
```

`room_shell/splat/*.ply` is the trained splat; `scene/splat.py` renders it and
`scene/viewer.py` builds a browsable page. Record a slow 30–60 s orbit of the
room — richly textured, lots of overlap (SfM needs both).

## How it's wired (two conda envs, on purpose)

nerfstudio + gsplat live in the **`roomrecon`** env (cu124), kept separate from
`assetpipe` (cu121) so their CUDA stacks never collide — the same doctrine that
keeps gen3d apart. There is **no `colmap` binary** on this box, so the backend
does not use `ns-process-data`. Instead:

```
frames ─▶ pycolmap SfM (assetpipe env)         scene/backends.py::_run_colmap_sparse
       ─▶ colmap_to_json  (roomrecon env)      scene/_splat_driver.py   [cross-env]
       ─▶ ns-train splatfacto (roomrecon env)
       ─▶ ns-export gaussian-splat
       ─▶ splat.ply ─▶ scene/splat.py viewer
```

`_splat_driver.py` runs under the roomrecon python and imports **only**
nerfstudio + stdlib (that env has no `assetpipe`). The backend finds that env
via `_find_nerfstudio_python()` — override with `$ASSETPIPE_NERFSTUDIO_PYTHON`.

## What's proven, and what isn't (yet)

Validated 2026-07-15 on this box:

- ✅ **splatfacto trains and exports** on real posed images: an orbit rendered
  from `Mouse.ply` (48 views, exact known poses) → 3000-iter splat → exported
  10k gaussians → recognizable mouse from every angle in our own viewer. The
  nerfstudio training/export chain works here.
- ✅ **cross-env bridge**: `_splat_driver.py` converts a real COLMAP model to a
  valid `transforms.json` (+ `sparse_pc.ply` init) from the roomrecon env; the
  backend imports in the assetpipe env and auto-finds roomrecon.
- ✅ **pycolmap SfM** on a real textured orbit video (24/24 registered — prior
  work, [[assetpipe-env-setup]]).
- ⚠️ **not yet run as one continuous command on a real room**, only because no
  textured room video is on disk. A black-background object orbit is adversarial
  for SfM (COLMAP got 2/48 on the mouse renders — no background features); a real
  room registers fine. This is a data gap, not a code gap: shoot a room video and
  the links connect.

## Next: 3DGUT (the device-correct trainer)

splatfacto assumes pinhole cameras, so wide-FOV / rolling-shutter footage must
be undistorted first (lossy). **3DGUT** (NVIDIA, CVPR'25) replaces EWA splatting
with the Unscented Transform to train *directly* on fisheye + rolling-shutter
frames — exactly what Quest 3 passthrough and phone/glasses video produce.

**Wired now** as `--backend 3dgut`: pycolmap SfM in `assetpipe`, then
`nv-tlabs/3dgrut` train + optional USDZ export in the separate `3dgrut` conda
env (override with `$ASSETPIPE_3DGRUT_PYTHON` / `$ASSETPIPE_3DGRUT_ROOT`).
Matches the NuRec recipe (COLMAP → 3DGUT → USD → Isaac Sim). Example:

```bash
conda activate assetpipe
python -m assetpipe scan room.mp4 --backend 3dgut --out room_shell
# needs: conda env `3dgrut` + ~/3dgrut (train.py)
```

A lighter in-process path via **gsplat ≥1.5**
(`rasterization(..., with_ut=True, with_eval3d=True)`) remains optional —
`roomrecon` still has gsplat **1.4.0**. Prefer the 3dgrut backend when you
need USDZ for Omniverse / Isaac; use splatfacto for a faster nerfstudio
preview shell.
