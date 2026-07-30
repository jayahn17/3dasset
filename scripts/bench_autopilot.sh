#!/usr/bin/env bash
# Detached benchmark driver — safe to run headless, from cron @reboot, or by
# hand. Waits for a working GPU (the driver fix / reboot may be happening
# around it), then finishes the whole open-source benchmark:
#   1. resume the CPU stage   (tools/bench_ours.py — idempotent, cached rows)
#   2. GPU stage              (scripts/bench_gpu_queue.sh — 3DGUT + TRELLIS)
#   3. final table refresh    (bench_out/RESULTS.md)
# Leaves bench_out/AUTOPILOT_DONE only when the GPU stage actually ran, so a
# later reboot retries GPU work if the module reload didn't take.

set -uo pipefail
cd /home/jaeahn-jammy/3dasset
mkdir -p logs
exec >> logs/bench_autopilot.log 2>&1

exec 9> /tmp/bench_autopilot.lock
flock -n 9 || { echo "$(date +%T) another autopilot instance holds the lock"; exit 0; }
[ -e bench_out/AUTOPILOT_DONE ] && { echo "$(date +%T) already complete"; exit 0; }

echo "=== autopilot start $(date)"
source /home/jaeahn-jammy/miniconda3/etc/profile.d/conda.sh
conda activate assetpipe

# 1) wait up to 2 h for the driver to come back
for _ in $(seq 1 240); do
    nvidia-smi >/dev/null 2>&1 && break
    sleep 30
done
if nvidia-smi >/dev/null 2>&1; then
    echo "GPU OK — driver $(nvidia-smi --query-gpu=driver_version --format=csv,noheader)"
    GPU_OK=1
else
    echo "GPU still down after 2 h — running CPU stage only"
    GPU_OK=0
fi

# 2) CPU stage (resumes wherever the killed batch stopped). If an older
# bench_ours is somehow still alive (driver fix didn't happen), wait for it
# instead of racing it on the same output dirs.
while pgrep -f "tools/bench_ours.py" >/dev/null 2>&1; do
    echo "$(date +%T) waiting for the existing bench_ours run to finish"
    sleep 60
done
python tools/bench_ours.py

# 3) GPU stage
if [ "$GPU_OK" = "1" ]; then
    bash scripts/bench_gpu_queue.sh
    python tools/bench_ours.py   # refresh RESULTS.md from cached rows
    touch bench_out/AUTOPILOT_DONE
fi
echo "=== autopilot end $(date) (gpu_ok=$GPU_OK)"
