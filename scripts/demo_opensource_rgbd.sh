#!/usr/bin/env bash
# Open-source RGB-D demo → session/ → fuse (nvblox preferred / Open3D fallback).
# Proves the same path iPad packages will use.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate assetpipe

DATASET="${1:-lounge}"          # redwood | lounge | fountain
MAX_FRAMES="${MAX_FRAMES:-60}"
OUT_SESSION="captures/opensource_${DATASET}_session"
OUT_FUSE="demo_out/opensource_${DATASET}_rgbd"

mkdir -p captures demo_out

echo "=== 1) build session from Open3D $DATASET (max $MAX_FRAMES frames) ==="
python scripts/rgbd_session_from_open3d.py \
  --dataset "$DATASET" \
  --out "$OUT_SESSION" \
  --max-frames "$MAX_FRAMES"

echo "=== 2) fuse (same launcher as iPad) ==="
BACKEND="${BACKEND:-auto}" bash scripts/launch_rgbd_fuse.sh "$OUT_SESSION" "$OUT_FUSE"

echo
echo "RESULTS: $OUT_FUSE"
echo "  viewer: $OUT_FUSE/scan_view.html"
echo "  mesh:   $OUT_FUSE/scene_tsdf_mesh.ply  (or scene_mesh.glb)"
