#!/usr/bin/env python3
"""RGB-D session → scored best frame(s) → object asset.

Analyzes the session, keeps only the top frame by default (one image was
already a good result), drops the floor plane, and writes a small asset
folder. Prefer this over full TSDF / COLMAP / 3DGUT for phone LiDAR objects.

  python tools/simple_rgbd_object.py path/to/session --out demo_out/my_object
  # or: python -m assetpipe object path/to/session --out demo_out/my_object
  xdg-open demo_out/my_object/OPEN_ME.html
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", help="session/ with manifest.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--frame", type=int, default=None,
                    help="force frame index (default: auto-score best)")
    ap.add_argument("--max-frames", type=int, default=4,
                    help="max diverse views to merge (default 4)")
    ap.add_argument("--stride", type=int, default=2,
                    help="score every Nth frame")
    args = ap.parse_args()

    from assetpipe.scene.rgbd_object_asset import build_object_asset

    meta = build_object_asset(
        args.session,
        args.out,
        max_frames=args.max_frames,
        stride=args.stride,
        frame=args.frame,
    )
    print(f"used frames {meta['frames_used']} / {meta['n_session_frames']}")
    print(f"  {meta['pick_note']}")
    print(f"  points {meta['points']:,}")
    print(f"  open   {meta['artifacts']['open_me']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
