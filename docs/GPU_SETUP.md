# GPU Setup — Ubuntu 22.04 + RTX 4080

The 4080 is Ada (compute **sm_89**, **16 GB** VRAM). That comfortably runs
YOLO-World, Grounded SAM 2, and object-scale Nerfstudio, and fits
**TRELLIS-image-large** with care. This guide gets each real backend running
and gives you a smoke test at every step.

## 0. Prereqs
```bash
nvidia-smi                      # driver present, CUDA >= 12.1 recommended
# install Miniconda if needed: https://docs.conda.io/en/latest/miniconda.html
```

## 1. Pipeline env + CPU smoke test (5 min)
```bash
git clone https://github.com/jayahn17/3dasset && cd 3dasset
git checkout claude/3d-asset-recording-quest-e1jvtx
bash env/setup_ubuntu.sh                       # conda env + demo + torch check
```
Expected: `python -m assetpipe demo` prints 3 assets; torch reports
`cuda True` with `NVIDIA GeForce RTX 4080`.

## 2. Real detection — YOLO-World (10 min, low VRAM)
```bash
mkdir photos && cp ~/some_box_pics/*.jpg photos/
conda run -n assetpipe python -m assetpipe run \
    --input photos/ --classes "cardboard box,shoe box,book,mug" \
    --detector yolo-world --reconstruct procedural --location "garage"
conda run -n assetpipe python -m assetpipe list
```
Now the label + bounding box come from the image (not `--classes` guesses).
Open `twin_out/control_center.html`. **Milestone: the box test is real.**

Add masks + cross-frame tracking with Grounded SAM 2 (`--detector grounded-sam2`)
after installing it: https://github.com/IDEA-Research/Grounded-SAM-2 — then wire
the two marked `TODO`s in `assetpipe/detect/grounded_sam.py`.

## 3. Real reconstruction — TRELLIS (30–60 min first build)
```bash
bash env/setup_ubuntu.sh trellis               # clones + builds TRELLIS
conda run -n assetpipe env TRELLIS_ROOT="$PWD/TRELLIS" \
    python services/trellis_server.py          # serves :8080 ; GET /health
```
In another shell:
```bash
conda run -n assetpipe python -m assetpipe run \
    --input photos/ --detector yolo-world --reconstruct trellis \
    --location "garage"
```
Each object → a **textured GLB** in `twin_out/<id>/model.glb`, a `model.obj`
preview for the viewer, a `model.urdf`, and a catalog row.

**If you OOM on 16 GB:** set `ATTN_BACKEND=xformers`, lower `texture_size` in
`services/trellis_server.py`, or switch `TRELLIS_MODEL` to a smaller variant.
Hunyuan3D 2.1 is a drop-in alternative server behind the same `/generate` API.

## 4. Multi-view scan — Nerfstudio (optional, higher fidelity)
```bash
pip install nerfstudio                          # or the official conda route
# capture a walk-around video of the object, then:
ns-process-data video --data walk.mp4 --output-dir proc/
ns-train splatfacto --data proc/
ns-export gaussian-splat --load-config outputs/.../config.yml --output-dir splat/
```
Then wire the CLI calls into `assetpipe/reconstruct/nerfstudio.py::finalize`
(surface-extract the splat to a mesh). Repo:
https://github.com/nerfstudio-project/nerfstudio (+ gsplat).

## 5. Quest 3 capture → this box
Build the Unity recorder in `docs/QUEST3_CAPTURE.md`, upload a `session/`
dir, and run:
```bash
conda run -n assetpipe python -m assetpipe run \
    --quest-session /path/to/session --detector yolo-world --reconstruct trellis
```
Pose + depth from the session give **metric scale** and **world placement**.

## VRAM cheat-sheet (16 GB / 4080)
| Backend | Fits | Notes |
|---|---|---|
| YOLO-World | ✅ easily | ~2–4 GB |
| Grounded SAM 2 | ✅ | ~4–8 GB |
| TRELLIS-image-large | ✅ with care | offload / lower texture_size if OOM |
| Hunyuan3D 2.1 (full) | ⚠️ tight | use 2mini or offload |
| Nerfstudio splatfacto | ✅ object-scale | scene-scale may need downscaling |
