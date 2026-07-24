#!/usr/bin/env bash
# Launch RGB-D fuse for an iPad CrateScanner package or a session/ folder.
# Prefers nvblox; falls back to Open3D TSDF if nvblox_torch cannot load.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# CUDA NPP for nvblox wheels (best-effort)
for d in \
  "$HOME/miniconda3/pkgs/libnpp-12.3.3.100-0/lib" \
  "$HOME/miniconda3/pkgs/libnpp-12.2.5.30-0/lib"
do
  if [[ -f "$d/libnppc.so.12" ]]; then
    export LD_LIBRARY_PATH="$d:${LD_LIBRARY_PATH:-}"
    break
  fi
done

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate assetpipe

INPUT="${1:-}"
OUT="${2:-demo_out/rgbd_run}"
BACKEND="${BACKEND:-auto}"

if [[ -z "$INPUT" ]]; then
  echo "Usage: $0 <session_dir|CrateScan.zip|package_dir> [out_dir]"
  echo "  Example: $0 captures/scan1/session demo_out/ipad1"
  echo "  Example: $0 captures/CrateScan-XXXX.zip demo_out/ipad1"
  exit 2
fi

SESSION="$INPUT"
WORK="$OUT/_incoming"
mkdir -p "$WORK" "$OUT"

# Unzip package if needed
if [[ -f "$INPUT" && "$INPUT" == *.zip ]]; then
  rm -rf "$WORK"
  mkdir -p "$WORK"
  unzip -qo "$INPUT" -d "$WORK"
  if [[ -d "$WORK/session" ]]; then
    SESSION="$WORK/session"
  elif [[ -f "$WORK/manifest.json" ]]; then
    SESSION="$WORK"
  else
    # zip may contain a single top-level folder
    TOP=$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -1)
    if [[ -d "$TOP/session" ]]; then SESSION="$TOP/session"
    elif [[ -f "$TOP/manifest.json" ]]; then SESSION="$TOP"
    else
      echo "!! zip has no session/ or manifest.json"
      exit 2
    fi
  fi
elif [[ -d "$INPUT/session" ]]; then
  SESSION="$INPUT/session"
fi

echo "=== inspect $SESSION ==="
python -m assetpipe rgbd "$SESSION" --inspect-only || true

echo "=== fuse backend=$BACKEND → $OUT ==="
python -m assetpipe rgbd "$SESSION" --backend "$BACKEND" --out "$OUT"

echo "=== done ==="
ls -lh "$OUT" | head -30
echo "Open: $OUT/scan_view.html"
