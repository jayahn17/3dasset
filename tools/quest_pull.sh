#!/usr/bin/env bash
# Bridge a USB-connected Meta Quest 3 to assetpipe TODAY — before the on-device
# PCA session recorder exists (docs/QUEST3_CAPTURE.md, Roadmap Phase 3).
#
# It grabs real headset imagery over adb and runs it through the FolderSource
# path (detect -> reconstruct -> twin). No per-frame pose/depth (that needs the
# recorder app), but real Quest frames through the real pipeline.
#
# HEADSET PREREQS
#   * Developer Mode ON (Meta Horizon mobile app -> your headset -> Developer Mode)
#   * USB data cable; accept the in-headset "Allow USB debugging" prompt once.
#
# USAGE
#   tools/quest_pull.sh check                         # is the headset visible?
#   tools/quest_pull.sh shots  N   [FRAMESDIR]        # live-grab N screencaps (~1s apart)
#   tools/quest_pull.sh pull       [FRAMESDIR]        # pull existing in-headset screenshots
#   tools/quest_pull.sh run FRAMESDIR "classes" "location"   # run the pipeline on a frames dir
#   tools/quest_pull.sh auto  N    "classes" "location"      # shots N -> run, in one go
set -euo pipefail
export PATH="$HOME/platform-tools:$PATH"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEFAULT_FRAMES="$REPO/quest_frames"
QUEST_SHOTS="/sdcard/Oculus/Screenshots"   # where in-headset screenshots land

die() { echo "!! $*" >&2; exit 1; }

need_device() {
  command -v adb >/dev/null || die "adb not on PATH (expected ~/platform-tools/adb)"
  local st
  st="$(adb get-state 2>/dev/null || true)"
  if [ "$st" != "device" ]; then
    echo "adb state: ${st:-none}"
    adb devices -l || true
    die "no authorized Quest. Plug a DATA cable, put the headset on, accept 'Allow USB debugging'."
  fi
  echo ">> device: $(adb devices -l | sed -n '2p')"
}

cmd_check() {
  command -v adb >/dev/null || die "adb not installed"
  adb devices -l
  adb get-state >/dev/null 2>&1 && echo "OK: an authorized device is connected." \
    || echo "No authorized device yet (see prereqs at top of this script)."
}

cmd_shots() {   # shots N [FRAMESDIR]
  local n="${1:?usage: shots N [FRAMESDIR]}" out="${2:-$DEFAULT_FRAMES}"
  need_device
  mkdir -p "$out"
  echo ">> grabbing $n screencaps into $out (move your head slowly around the object)"
  for i in $(seq 1 "$n"); do
    printf -v f "%s/shot_%04d.png" "$out" "$i"
    adb exec-out screencap -p > "$f"
    # screencap can emit an empty file for protected surfaces; flag it
    [ -s "$f" ] || echo "   (frame $i empty — protected content or black view)"
    echo "   $f"
    sleep 1
  done
  echo ">> done: $(ls "$out"/*.png 2>/dev/null | wc -l) frames"
}

cmd_pull() {    # pull [FRAMESDIR]
  local out="${1:-$DEFAULT_FRAMES}"
  need_device
  mkdir -p "$out"
  echo ">> pulling $QUEST_SHOTS -> $out"
  adb pull -a "$QUEST_SHOTS" "$out" || die "no screenshots found at $QUEST_SHOTS"
  echo ">> pulled: $(find "$out" -type f | wc -l) files"
}

cmd_run() {     # run FRAMESDIR "classes" "location"
  local frames="${1:?usage: run FRAMESDIR classes location}" classes="${2:-box}" loc="${3:-unknown}"
  [ -d "$frames" ] || die "frames dir not found: $frames"
  ls "$frames"/*.png "$frames"/*.jpg >/dev/null 2>&1 || die "no images in $frames"
  echo ">> running assetpipe on $frames  (classes='$classes' location='$loc')"
  ( cd "$REPO" && python -m assetpipe run \
      --input "$frames" --classes "$classes" --location "$loc" \
      --out "$REPO/twin_out" )
  ( cd "$REPO" && python -m assetpipe list --out "$REPO/twin_out" )
  echo ">> twin: $REPO/twin_out/control_center.html"
}

cmd_auto() {    # auto N "classes" "location"
  local n="${1:?usage: auto N classes location}" classes="${2:-box}" loc="${3:-unknown}"
  cmd_shots "$n" "$DEFAULT_FRAMES"
  cmd_run "$DEFAULT_FRAMES" "$classes" "$loc"
}

case "${1:-}" in
  check) shift; cmd_check "$@";;
  shots) shift; cmd_shots "$@";;
  pull)  shift; cmd_pull  "$@";;
  run)   shift; cmd_run   "$@";;
  auto)  shift; cmd_auto  "$@";;
  *) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 1;;
esac
