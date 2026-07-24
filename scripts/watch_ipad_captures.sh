#!/usr/bin/env bash
# Watch captures/ for new iPad zips / session folders and auto-launch fuse.
# Usage: bash scripts/watch_ipad_captures.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p captures demo_out/ipad_auto
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate assetpipe

echo "Watching $ROOT/captures for new .zip or session dirs (Ctrl+C to stop)"
echo "Drop CrateScanner packages here; fuse prefers nvblox (BACKEND=auto)."

seen="$ROOT/captures/.fuse_seen"
touch "$seen"

while true; do
  # new zips
  while IFS= read -r -d '' z; do
    base=$(basename "$z" .zip)
    if grep -qxF "$base" "$seen" 2>/dev/null; then
      continue
    fi
    echo "=== NEW ZIP $z ==="
    out="demo_out/ipad_auto/$base"
    BACKEND="${BACKEND:-auto}" bash scripts/launch_rgbd_fuse.sh "$z" "$out" || echo "!! fuse failed for $z"
    echo "$base" >> "$seen"
  done < <(find captures -maxdepth 1 -type f -name 'CrateScan*.zip' -print0 2>/dev/null)

  # new session dirs with manifest
  while IFS= read -r -d '' s; do
    base=$(basename "$(dirname "$s")")
    [[ "$base" == .* ]] && continue
    key="session:$base"
    if grep -qxF "$key" "$seen" 2>/dev/null; then
      continue
    fi
    echo "=== NEW SESSION $(dirname "$s") ==="
    out="demo_out/ipad_auto/$base"
    BACKEND="${BACKEND:-auto}" bash scripts/launch_rgbd_fuse.sh "$(dirname "$s")" "$out" || echo "!! fuse failed"
    echo "$key" >> "$seen"
  done < <(find captures -mindepth 2 -maxdepth 2 -type f -name 'manifest.json' -print0 2>/dev/null)

  sleep 5
done
