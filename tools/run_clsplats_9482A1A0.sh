#!/usr/bin/env bash
# CL-Splats local refine on CrateScan-9482A1A0 (uses paper change-detect path).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLS="${HOME}/miniconda3/envs/cl-splats"
export CUDA_HOME="${HOME}/miniconda3/envs/3dgrut"
export PATH="${CUDA_HOME}/bin:${PATH}"
export TORCH_CUDA_ARCH_LIST=8.9
export LD_LIBRARY_PATH="${CUDA_HOME}/lib:${CUDA_HOME}/lib64:${LD_LIBRARY_PATH:-}"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

DATA="${ROOT}/demo_out/CrateScan-9482A1A0_clsplats/data"
PLY="${ROOT}/demo_out/CrateScan-9482A1A0_3dgut_full30k_gui/3dgut_runs/cratescan_3dgut/3dgrut_data-2407_224428/export_last.ply"
OUT="${ROOT}/demo_out/CrateScan-9482A1A0_clsplats/run"

if [[ ! -d "${DATA}/images" ]]; then
  "${CLS}/bin/python" "${ROOT}/tools/prepare_clsplats_dataset.py" \
    --src "${ROOT}/demo_out/CrateScan-9482A1A0_3dgut_full30k_gui/3dgrut_data" \
    --out "${DATA}" --images images_2 --scale 0.5
fi

"${CLS}/bin/python" "${ROOT}/tools/run_clsplats_refine.py" \
  --data "${DATA}" \
  --ply "${PLY}" \
  --out "${OUT}" \
  --iters "${ITERS:-100}" \
  --max-gaussians "${MAX_GAUSSIANS:-100000}" \
  --change-threshold "${CHANGE_THRESH:-0.75}"
