#!/usr/bin/env python3
"""Critically refine RGB-D frames before reconstruction.

  python tools/curate_rgbd_session.py SESSION --out demo_out/ID_curated
  python -m assetpipe curate SESSION --out demo_out/ID_curated --then object

Open CURATION.html to see kept vs rejected, then run object / rgbd / 3dgut
on the curated session folder.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", help="raw session/ with manifest.json")
    ap.add_argument("--out", required=True, help="curated session output dir")
    ap.add_argument("--target", type=int, default=48,
                    help="max keyframes to keep (default 48)")
    ap.add_argument("--min-angle", type=float, default=8.0,
                    help="min viewpoint angle between keyframes (deg)")
    ap.add_argument("--min-step", type=float, default=0.012,
                    help="min camera translation between keyframes (m)")
    ap.add_argument("--sharp-pct", type=float, default=25.0,
                    help="reject frames below this sharpness percentile")
    ap.add_argument("--min-edge", type=float, default=0.15,
                    help="min RGB↔depth edge agreement")
    ap.add_argument("--then", choices=["none", "object"], default="none",
                    help="run object asset on curated session afterwards")
    ap.add_argument("--object-out", default=None,
                    help="object asset out dir (default: <out>_object)")
    ap.add_argument("--object-frames", type=int, default=1,
                    help="max frames for --then object (default 1 — best curated view)")
    args = ap.parse_args()

    from assetpipe.scene.rgbd_curate import curate_session

    report = curate_session(
        args.session,
        args.out,
        target=args.target,
        min_angle_deg=args.min_angle,
        min_step_m=args.min_step,
        sharp_pct=args.sharp_pct,
        min_edge=args.min_edge,
    )
    print(
        f"✔ curated {report['n_source']} → {report['n_kept']} frames "
        f"(rejected {report['n_rejected']})"
    )
    print(f"  rejects   {report.get('reject_counts')}")
    q = report["quality"]
    print(
        f"  sharp     {q['all_sharp_mean']:.0f} → {q['kept_sharp_mean']:.0f}  "
        f"quality {q['all_mean']:.0f} → {q['kept_mean']:.0f}"
    )
    print(f"  session   {report['curated_session']}")
    print(f"  report    {Path(report['curated_session']) / 'CURATION.html'}")

    if args.then == "object":
        from assetpipe.scene.rgbd_object_asset import build_object_asset

        obj_out = args.object_out or (str(Path(args.out).resolve()) + "_object")
        meta = build_object_asset(
            report["curated_session"],
            obj_out,
            max_frames=args.object_frames,
            stride=1,
        )
        print(f"✔ object    frames {meta['frames_used']} → {meta['points']:,} pts")
        print(f"  open      {meta['artifacts']['open_me']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
