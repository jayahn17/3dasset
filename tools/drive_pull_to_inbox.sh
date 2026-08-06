#!/usr/bin/env bash
#
# Pull CrateScanner packages out of Google Drive into the assetpipe inbox.
#
# Two things this has to get right, both of which a bare `rclone copy` straight
# into the inbox gets wrong:
#
#   1. The app writes one subfolder per uploader — CrateScans/<email>/ — but
#      watch_inbox.py lists the inbox with os.listdir and never recurses. So the
#      Drive tree is mirrored into a staging directory and the zips are
#      flattened from there into inbox/.
#
#   2. The watcher MOVES a processed zip to done/. Copying from Drive straight
#      into inbox/ therefore re-delivers every package on the next timer tick,
#      forever. A zip whose name already exists anywhere under captures/ is
#      skipped, which is the same rule tools/drive_rgbd_autopilot.py uses.
#
# Run by rgbd-drive-sync.service; safe to run by hand.
#
#   DRIVE_REMOTE   rclone remote + path   (default gdrive:CrateScans)
#   CAPTURES_DIR   assetpipe captures dir (default ~/3dasset/captures)
#
# The rclone remote must be authorised as the account that OWNS the folder. If
# yours is a collaborator instead, add --drive-shared-with-me to the copy below.

set -euo pipefail

REMOTE="${DRIVE_REMOTE:-gdrive:CrateScans}"
CAPTURES="${CAPTURES_DIR:-$HOME/3dasset/captures}"
STAGE="$CAPTURES/drive_pull"
INBOX="$CAPTURES/inbox"

mkdir -p "$STAGE" "$INBOX"

# --min-age lets an in-progress Drive upload finish before we copy it. The
# staging copy is left in place so rclone stays incremental across runs.
rclone copy "$REMOTE" "$STAGE" \
    --include "*.zip" \
    --min-age 30s \
    --log-level INFO

new=0
while IFS= read -r -d '' zip; do
    name="$(basename "$zip")"

    known=""
    for sub in inbox work done failed; do
        if [ -e "$CAPTURES/$sub/$name" ]; then known=1; break; fi
    done
    if [ -n "$known" ]; then continue; fi

    cp -p "$zip" "$INBOX/$name"
    echo "→ inbox/$name"
    new=$((new + 1))
done < <(find "$STAGE" -type f -name '*.zip' -print0)

echo "$new new package(s)"
