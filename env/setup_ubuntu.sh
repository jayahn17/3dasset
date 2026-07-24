#!/usr/bin/env bash
# One-shot setup for the GPU box (Ubuntu 22.04 + RTX 4080).
# Sets up the conda env and does a CPU smoke test. Heavy 3D backends
# (TRELLIS / Nerfstudio / Grounded-SAM-2) are cloned on demand — see the
# functions at the bottom and docs/GPU_SETUP.md.
set -euo pipefail
cd "$(dirname "$0")/.."

echo ">> checking NVIDIA driver / CUDA"
nvidia-smi || { echo "!! nvidia-smi failed — install the NVIDIA driver first"; exit 1; }

if ! command -v conda >/dev/null 2>&1; then
  echo "!! conda not found. Install Miniconda: https://docs.conda.io/en/latest/miniconda.html"
  exit 1
fi

echo ">> creating conda env 'assetpipe'"
conda env create -f env/environment.yml || conda env update -f env/environment.yml

echo ">> CPU smoke test (no GPU needed)"
conda run -n assetpipe python -m assetpipe demo
conda run -n assetpipe python -m assetpipe list

echo ">> torch/CUDA visibility"
conda run -n assetpipe python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"

cat <<'EOF'

Next steps (see docs/GPU_SETUP.md):
  # 1) detection on real photos (YOLO-World, runs on the 4080 easily)
  conda run -n assetpipe python -m assetpipe run \
      --input photos/ --classes "cardboard box" \
      --detector yolo-world --reconstruct procedural --location "garage"

  # 2) install + serve TRELLIS, then reconstruct textured meshes
  bash env/setup_ubuntu.sh trellis      # clones + builds TRELLIS
  conda run -n assetpipe env TRELLIS_ROOT="$PWD/TRELLIS" \
      python services/trellis_server.py &
  conda run -n assetpipe python -m assetpipe run \
      --input photos/ --detector yolo-world --reconstruct trellis
EOF

# --- optional heavy backends -------------------------------------------------
install_trellis() {
  [ -d TRELLIS ] || git clone --recurse-submodules https://github.com/microsoft/TRELLIS
  cd TRELLIS
  . ./setup.sh --new-env --basic --xformers --flash-attn --diffoctreerast \
      --spconv --mipgaussian --kaolin --nvdiffrast
  cd ..
  echo ">> TRELLIS ready. Serve: TRELLIS_ROOT=$PWD/TRELLIS python services/trellis_server.py"
}

if [ "${1:-}" = "trellis" ]; then install_trellis; fi
