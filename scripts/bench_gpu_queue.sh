#!/usr/bin/env bash
# GPU half of the open-source benchmark (docs/INDUSTRY_BENCHMARK.md).
#
# BLOCKED until reboot: NVIDIA kernel module 580.159 vs userspace 580.173 —
# nvidia-smi must work before this script will start.
#
#   P6  3DGUT splat per session (measured ARKit poses, depth-seeded init)
#   P7  TRELLIS generative completion GLB from the curated photos,
#       scored against our metric dims.json (shape error is the number
#       that matters — TRELLIS output is unit-normalized by design)
#
# Run from the repo root, inside the assetpipe env:
#   conda activate assetpipe && bash scripts/bench_gpu_queue.sh
# CPU stage (tools/bench_ours.py) must have run first: it extracts the
# sessions into captures/work/bench_ours/ and writes the dims.json truths.

set -uo pipefail
cd "$(dirname "$0")/.."

if ! nvidia-smi >/dev/null 2>&1; then
    echo "!! GPU unavailable (driver/library mismatch) — reboot first." >&2
    exit 1
fi

SESSIONS=(1B38880A 1E4E971C 2A92CC43 4179C57D 5C0D0369 A7C99725 BE53A423)

session_dir() {  # short id -> extracted session dir (has manifest.json)
    find "captures/work/bench_ours/$1" -name manifest.json -printf '%h\n' \
        2>/dev/null | head -1
}

# ---- P6: 3DGUT splats (drives the 3dgrut env internally) -------------------
for s in "${SESSIONS[@]}"; do
    sd=$(session_dir "$s")
    out="bench_out/CrateScan-$s/ours_3dgut"
    if [ -z "$sd" ]; then echo "-- $s: not extracted, skip"; continue; fi
    if [ -e "$out/DONE" ]; then echo "-- $s: 3dgut already done"; continue; fi
    echo "== P6 3DGUT $s"
    python tools/run_3dgut_from_session.py "$sd" --out "$out" \
        --curate --iterations 5000 \
        && touch "$out/DONE" \
        || echo "!! 3DGUT failed on $s (continuing)"
done

# ---- P7: TRELLIS completion (needs the gen3d service) ----------------------
if ! curl -s -o /dev/null http://localhost:8080/ 2>/dev/null; then
    echo "== starting gen3d server"
    nohup conda run -n gen3d python services/gen3d_server.py \
        > logs/gen3d_server.log 2>&1 &
    for _ in $(seq 1 60); do
        curl -s -o /dev/null http://localhost:8080/ 2>/dev/null && break
        sleep 5
    done
fi

for s in "${SESSIONS[@]}"; do
    out="bench_out/CrateScan-$s/ours_trellis"
    glb="$out/asset_trellis.glb"
    if [ -e "$glb" ]; then echo "-- $s: trellis already done"; continue; fi
    echo "== P7 TRELLIS $s"
    python -m assetpipe generate "bench_out/CrateScan-$s/photos" \
        --out "$out" --backend trellis \
        || { echo "!! TRELLIS failed on $s (continuing)"; continue; }
    truth="bench_out/CrateScan-$s/ours_tsdf/dims.json"
    if [ -f "$glb" ] && [ -f "$truth" ]; then
        python tools/bench_compare.py "$glb" --truth "$truth" \
            --label "ours_trellis_$s" || true
    fi
done

echo "== GPU queue complete. Re-check bench_out/*/ and update"
echo "   bench_out/RESULTS.md / docs/INDUSTRY_BENCHMARK.md with the new rows."
