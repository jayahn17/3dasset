#!/usr/bin/env bash
# Start Engin170 hands-off loop:
#   1) poll Google Drive for new CrateScan-*.zip  → captures/inbox
#   2) watch_inbox: object/rgbd fuse             → demo_out/<name>
#   3) watch_3dgut_queue (GPU route):
#        ≤24″ → TRELLIS + metric mm STL/GLB      → *_TRELLIS_RESULT.zip
#        >24″ → 3DGUT train + splat              → *_3DGUT_RESULT.zip
#   4) RESULT zips → outbox / Drive if rclone ready
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p captures/{inbox,work,done,failed,status,outbox,drive_pull} demo_out logs
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate assetpipe

FOLDER_ID="${FOLDER_ID:-1_HjCyP9-Th3UTjK6Kk89vTuud6ltL4AI}"
INTERVAL="${INTERVAL:-60}"
MODE="${MODE:-object}"          # object (fast preprocess) | rgbd (full TSDF)
BACKEND="${BACKEND:-auto}"
MAX_FRAMES="${MAX_FRAMES:-4}"
# Back-compat: ENABLE_3DGUT still works; prefer ENABLE_GPU_ROUTE.
ENABLE_GPU_ROUTE="${ENABLE_GPU_ROUTE:-${ENABLE_3DGUT:-1}}"
ENABLE_TRELLIS="${ENABLE_TRELLIS:-1}"
DGUT_ITERS="${DGUT_ITERS:-30000}"
BACKFILL_3DGUT="${BACKFILL_3DGUT:-0}"  # 1 = also process historical statuses

# ensure local rclone binary if system one missing
if ! command -v rclone >/dev/null 2>&1; then
  if [[ -x /tmp/rclone-v1.74.4-linux-amd64/rclone ]]; then
    mkdir -p "$HOME/bin"
    ln -sfn /tmp/rclone-v1.74.4-linux-amd64/rclone "$HOME/bin/rclone"
    export PATH="$HOME/bin:$PATH"
  fi
fi

echo "=== Engin170 autopilot ==="
echo "Drive folder: $FOLDER_ID"
echo "Mode:         $MODE  (max_frames=$MAX_FRAMES)"
echo "GPU route:    ENABLE_GPU_ROUTE=$ENABLE_GPU_ROUTE  TRELLIS=$ENABLE_TRELLIS  iters=$DGUT_ITERS  backfill=$BACKFILL_3DGUT"
echo "Logs:         $ROOT/logs/"
echo

# kill prior copies of these loops (same repo)
pkill -f "tools/watch_inbox.py" 2>/dev/null || true
pkill -f "tools/drive_rgbd_autopilot.py" 2>/dev/null || true
pkill -f "tools/watch_3dgut_queue.py" 2>/dev/null || true
sleep 1
rm -f captures/status/.3dgut_queue.lock

nohup python tools/watch_inbox.py \
  --root "$ROOT/captures" \
  --out "$ROOT/demo_out" \
  --mode "$MODE" \
  --max-frames "$MAX_FRAMES" \
  --backend "$BACKEND" \
  --interval 5 \
  > logs/watch_inbox.log 2>&1 &
echo $! > logs/watch_inbox.pid
echo "watch_inbox pid $(cat logs/watch_inbox.pid)"

nohup python tools/drive_rgbd_autopilot.py \
  --folder-id "$FOLDER_ID" \
  --interval "$INTERVAL" \
  > logs/drive_autopilot.log 2>&1 &
echo $! > logs/drive_autopilot.pid
echo "drive_autopilot pid $(cat logs/drive_autopilot.pid)"

if [[ "$ENABLE_GPU_ROUTE" == "1" ]]; then
  DGUT_ARGS=(
    --folder-id "$FOLDER_ID"
    --interval 30
    --iterations "$DGUT_ITERS"
  )
  if [[ "$BACKFILL_3DGUT" == "1" ]]; then
    DGUT_ARGS+=(--backfill)
  fi
  if [[ "$ENABLE_TRELLIS" != "1" ]]; then
    DGUT_ARGS+=(--no-trellis)
  fi
  nohup python tools/watch_3dgut_queue.py "${DGUT_ARGS[@]}" \
    > logs/watch_3dgut.log 2>&1 &
  echo $! > logs/watch_3dgut.pid
  echo "watch_gpu_route pid $(cat logs/watch_3dgut.pid)  (log: logs/watch_3dgut.log)"
else
  echo "GPU route queue disabled (ENABLE_GPU_ROUTE=0)"
fi

echo
echo "Tail logs:"
echo "  tail -f logs/drive_autopilot.log logs/watch_inbox.log logs/watch_3dgut.log"
echo
echo "Upload back to Drive needs one-time:  rclone config   (remote name: gdrive)"
echo "Until then RESULT zips land in captures/outbox/ for manual upload."
echo
echo "Stop:"
echo "  kill \$(cat logs/drive_autopilot.pid logs/watch_inbox.pid logs/watch_3dgut.pid 2>/dev/null)"
