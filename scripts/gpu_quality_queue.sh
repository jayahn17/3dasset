#!/usr/bin/env bash
# One-shot GPU quality queue — run after the reboot that fixes the NVIDIA
# 580.159 (kernel) vs 580.173 (userspace) mismatch.
#
#   bash scripts/gpu_quality_queue.sh
#
# Runs, in order (each logged to logs/gpu_queue/):
#   1. CUDA sanity check (aborts early with a clear message if still broken)
#   2. 3DGUT 30k on CrateScan-1E4E971C (object orbit, FIXED poses)  ~40-60 min
#   3. 3DGUT 30k on CrateScan-4179C57D (apartment room sweep)       ~60-90 min
#      -> the Kiri-3DGS / Scaniverse-level room-shell comparison
#   4. TRELLIS regeneration of the mouse via the NEW EWA render path
#      (services/gen3d_server.py must be up in the gen3d env; skipped if not)
#   5. Benchmark renders of every new artifact
set -u
cd "$(dirname "$0")/.."
mkdir -p logs/gpu_queue
PY=~/miniconda3/envs/assetpipe/bin/python
RUN="env PYTHONNOUSERSITE=1 PYTHONPATH=$PWD $PY"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a logs/gpu_queue/queue.log; }

log "1/5 CUDA sanity"
if ! nvidia-smi >/dev/null 2>&1; then
  log "!! nvidia-smi still failing — driver mismatch not resolved. Reboot first."
  exit 1
fi
if ! ~/miniconda3/envs/3dgrut/bin/python -c "import torch; assert torch.cuda.is_available()" >/dev/null 2>&1; then
  log "!! torch.cuda unavailable in 3dgrut env even though nvidia-smi works."
  exit 1
fi
log "   CUDA OK: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader)"

log "2/5 3DGUT 30k — CrateScan-1E4E971C (object)"
$RUN tools/run_3dgut_from_session.py \
  captures/work/CrateScan-1E4E971C/CrateScan-1E4E971C/session \
  --out demo_out/CrateScan-1E4E971C_3dgut_fixed \
  --every 2 --max-frames 120 --iterations 30000 \
  >> logs/gpu_queue/3dgut_object.log 2>&1 \
  && log "   ✔ object splat -> demo_out/CrateScan-1E4E971C_3dgut_fixed" \
  || log "   ✗ FAILED — see logs/gpu_queue/3dgut_object.log"

log "3/5 3DGUT 30k — CrateScan-4179C57D (apartment room shell)"
$RUN tools/run_3dgut_from_session.py \
  captures/work/CrateScan-4179C57D/CrateScan-4179C57D/session \
  --out demo_out/CrateScan-4179C57D_3dgut \
  --every 3 --max-frames 160 --iterations 30000 \
  >> logs/gpu_queue/3dgut_room.log 2>&1 \
  && log "   ✔ room splat -> demo_out/CrateScan-4179C57D_3dgut" \
  || log "   ✗ FAILED — see logs/gpu_queue/3dgut_room.log"

log "4/5 TRELLIS regen via EWA renders (needs gen3d server on :8080)"
if curl -s -o /dev/null -m 2 http://localhost:8080/health 2>/dev/null; then
  $RUN -m assetpipe generate Mouse.ply --out demo_out/mouse_ewa_regen \
    >> logs/gpu_queue/trellis_ewa.log 2>&1 \
    && log "   ✔ EWA-fed asset -> demo_out/mouse_ewa_regen" \
    || log "   ✗ FAILED — see logs/gpu_queue/trellis_ewa.log"
else
  log "   -- gen3d server not up; start it, then:"
  log "      $PY -m assetpipe generate Mouse.ply --out demo_out/mouse_ewa_regen"
fi

log "5/5 benchmark renders"
for d in demo_out/CrateScan-1E4E971C_3dgut_fixed demo_out/CrateScan-4179C57D_3dgut; do
  [ -f "$d/scene_gaussians.ply" ] || continue
  $RUN tools/view_splat.py "$d/scene_gaussians.ply" --out "$d/previews" \
    >> logs/gpu_queue/renders.log 2>&1 || true
done
log "DONE — compare previews against demo_out/CrateScan-1E4E971C_3dgut (broken baseline)"
