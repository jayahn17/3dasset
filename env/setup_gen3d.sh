#!/usr/bin/env bash
# Generative image-to-3D backends (TRELLIS + Hunyuan3D) in their OWN conda env.
#
# WHY A SEPARATE ENV: these pin exact CUDA-extension builds (spconv, nvdiffrast,
# diff-gaussian-rasterization, custom rasterizers). Installing them into
# `assetpipe` would put the working torch/CUDA setup at risk. They run as a
# GPU *service*; assetpipe stays a thin HTTP client (services/gen3d_server.py).
#
#   bash env/setup_gen3d.sh          # ~30-60 min: builds CUDA extensions
#
# NOTE: we do NOT use TRELLIS's own setup.sh — it swallows build failures and
# exits 0 (nvdiffrast needs --no-build-isolation, which it doesn't pass). Each
# extension is installed explicitly here and verified at the end.
#
# The box has CUDA 11.5 system-wide but torch is cu121, so nvcc 12.1 is
# installed INSIDE the env — extensions must compile against the torch CUDA.
set -euo pipefail

ENV=gen3d
ROOT="${GEN3D_ROOT:-$HOME/gen3d}"
CONDA_BASE="$(conda info --base)"
source "$CONDA_BASE/etc/profile.d/conda.sh"

echo ">> [1/7] conda env $ENV (python 3.10)"
conda env list | grep -q "^$ENV " || conda create -n "$ENV" python=3.10 -y
conda env config vars set -n "$ENV" PYTHONNOUSERSITE=1 >/dev/null

echo ">> [2/7] CUDA 12.1 toolkit in-env (nvcc must match torch cu121)"
conda run -n "$ENV" python -c "import subprocess,sys; sys.exit(subprocess.run(['nvcc','--version'],capture_output=True).returncode)" 2>/dev/null \
  || conda install -n "$ENV" -y -c "nvidia/label/cuda-12.1.0" cuda-toolkit >/dev/null

conda activate "$ENV"
export CUDA_HOME="$CONDA_PREFIX"
export PATH="$CUDA_HOME/bin:$PATH"
export TORCH_CUDA_ARCH_LIST="8.9"          # RTX 4080 SUPER (Ada, sm_89)
export MAX_JOBS="${MAX_JOBS:-8}"           # keep nvcc from OOMing the box

echo ">> [3/7] torch 2.4.1 + cu121"
python -c "import torch" 2>/dev/null || pip install -q \
    torch==2.4.1 torchvision==0.19.1 --index-url https://download.pytorch.org/whl/cu121

echo ">> [4/7] clone backends into $ROOT"
mkdir -p "$ROOT"
[ -d "$ROOT/TRELLIS" ]     || git clone --recurse-submodules https://github.com/microsoft/TRELLIS.git "$ROOT/TRELLIS"
[ -d "$ROOT/Hunyuan3D-2" ] || git clone https://github.com/Tencent-Hunyuan/Hunyuan3D-2.git "$ROOT/Hunyuan3D-2"

echo ">> [5/7] TRELLIS python deps"
pip install -q pillow imageio imageio-ffmpeg tqdm easydict opencv-python-headless \
    scipy ninja onnxruntime trimesh open3d xatlas pyvista pymeshfix igraph \
    transformers safetensors einops huggingface_hub rembg utils3d==0.0.2 \
    fastapi uvicorn python-multipart requests

echo ">> [6/7] CUDA extensions (the slow part)"
step () { echo "   .. $1"; }

step "xformers (memory-efficient attention, pinned to torch 2.4.1)"
python -c "import xformers" 2>/dev/null || \
    pip install -q xformers==0.0.28.post1 --index-url https://download.pytorch.org/whl/cu121

step "spconv (sparse convs for the structure stage)"
python -c "import spconv" 2>/dev/null || pip install -q spconv-cu120

step "kaolin"
python -c "import kaolin" 2>/dev/null || \
    pip install -q kaolin -f "https://nvidia-kaolin.s3.us-east-2.amazonaws.com/torch-2.4.1_cu121.html" || \
    echo "   !! kaolin failed (mesh extraction may still work)"

step "nvdiffrast (texture baking) — needs --no-build-isolation"
python -c "import nvdiffrast" 2>/dev/null || \
    pip install -q --no-build-isolation git+https://github.com/NVlabs/nvdiffrast.git

step "diff-gaussian-rasterization (mip-splatting build)"
python -c "import diff_gaussian_rasterization" 2>/dev/null || \
    pip install -q --no-build-isolation \
      "git+https://github.com/autonomousvision/mip-splatting.git#subdirectory=submodules/diff-gaussian-rasterization"

echo ">> [7/7] Hunyuan3D-2"
cd "$ROOT/Hunyuan3D-2"
pip install -q -r requirements.txt || echo "   !! some hunyuan reqs failed"
pip install -q -e .
( cd hy3dgen/texgen/custom_rasterizer && pip install -q --no-build-isolation -e . ) || \
    echo "   !! custom_rasterizer failed — shape gen works, texture baking won't"
( cd hy3dgen/texgen/differentiable_renderer && pip install -q --no-build-isolation -e . ) || \
    echo "   !! differentiable_renderer failed — same caveat"

echo
echo "=== verify ==="
cd "$ROOT/TRELLIS"
python - <<'PY'
import importlib, sys, os
sys.path.insert(0, os.path.expanduser("~/gen3d/TRELLIS"))
os.environ.setdefault("ATTN_BACKEND", "xformers")
os.environ.setdefault("SPCONV_ALGO", "native")
import torch
print(f"torch {torch.__version__}  cuda={torch.cuda.is_available()}")
ok = True
for m in ("xformers", "spconv", "kaolin", "nvdiffrast",
          "diff_gaussian_rasterization", "trellis", "hy3dgen"):
    try:
        importlib.import_module(m)
        print(f"  {m:30s} OK")
    except Exception as e:
        ok = False
        print(f"  {m:30s} FAILED: {type(e).__name__}: {e}")
sys.exit(0 if ok else 1)
PY
