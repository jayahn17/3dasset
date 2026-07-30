#!/usr/bin/env python3
"""Score a benchmark asset (Kiri / Marble / any API result) against our metric truth.

The commercial APIs only see RGB, so their results are scale-ambiguous or
unit-normalized. This scorer separates the two failure modes that matter:

  scale error  — how far the asset's absolute size is from measured truth
                 (1.0 = metric; generative APIs will be wildly off — expected)
  shape error  — residual per-axis error AFTER the best uniform rescale
                 (aspect fidelity; this is the fair cross-API comparison metric)

Truth comes from our pipeline's dims.json (on-device mesh + TSDF cross-check,
OBB sorted L>=W>=H in inches) — the one thing RGB-only services cannot have.

Usage:
    python tools/bench_compare.py kiri_result.glb \
        --truth demo_out/CrateScan-1B38880A/dims.json
    python tools/bench_compare.py marble_scene.ply --truth-lwh 34.6 19.8 28.4
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from assetpipe.scene.measure import measure_geometry  # noqa: E402


def truth_from_dims(dims_path: str, key: str | None) -> tuple[list[float], str]:
    with open(dims_path) as fh:
        dims = json.load(fh)
    key = key or dims.get("primary") or "object"
    block = dims.get(key)
    if not isinstance(block, dict):
        avail = [k for k, v in dims.items() if isinstance(v, dict) and "obb" in v]
        raise SystemExit(f"No '{key}' block in {dims_path}; available: {avail}")
    lwh = block["obb"]["raw_inches_sorted_lwh"]
    return [float(x) for x in lwh], f"{dims_path}:{key}"


def compare(asset_lwh: list[float], truth_lwh: list[float]) -> dict:
    """Both sorted descending, inches. Returns scale + shape error metrics."""
    ratios = [t / a for t, a in zip(truth_lwh, asset_lwh)]
    # Best uniform scale = least squares in log space = geometric mean of ratios.
    s = math.exp(sum(math.log(r) for r in ratios) / len(ratios))
    rescaled = [a * s for a in asset_lwh]
    shape_err = [abs(r - t) / t for r, t in zip(rescaled, truth_lwh)]
    return {
        "asset_lwh_in": [round(x, 3) for x in asset_lwh],
        "truth_lwh_in": [round(x, 3) for x in truth_lwh],
        "per_axis_ratio_truth_over_asset": [round(r, 4) for r in ratios],
        "best_uniform_scale": round(s, 4),
        "scale_error_pct": round(abs(s - 1.0) * 100.0, 2),
        "is_metric_within_5pct": abs(s - 1.0) <= 0.05,
        "rescaled_lwh_in": [round(x, 3) for x in rescaled],
        "shape_error_pct_per_axis": [round(e * 100.0, 2) for e in shape_err],
        "shape_error_pct_mean": round(sum(shape_err) / len(shape_err) * 100.0, 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("asset", help="API result: .glb/.gltf/.obj/.ply mesh or point cloud")
    ap.add_argument("--truth", help="dims.json from our pipeline")
    ap.add_argument("--truth-key", help="Block in dims.json (default: its 'primary')")
    ap.add_argument("--truth-lwh", nargs=3, type=float, metavar=("L", "W", "H"),
                    help="Manual truth dims in inches (sorted or not)")
    ap.add_argument("--units", default="auto", choices=["auto", "meters", "inches"],
                    help="Units of the asset file (default auto-guess)")
    ap.add_argument("--label", default=None, help="Service name for the report")
    args = ap.parse_args()

    if not args.truth and not args.truth_lwh:
        ap.error("need --truth dims.json or --truth-lwh L W H")

    if args.truth_lwh:
        truth = sorted(args.truth_lwh, reverse=True)
        truth_src = "manual"
    else:
        truth, truth_src = truth_from_dims(args.truth, args.truth_key)

    label = args.label or os.path.splitext(os.path.basename(args.asset))[0]
    geo = measure_geometry(args.asset, units=args.units, label=label)
    asset_lwh = geo["obb"]["raw_inches_sorted_lwh"]

    report = {
        "label": label,
        "asset": os.path.abspath(args.asset),
        "asset_units_assumed": geo["units_in"],
        "asset_n_points": geo["n_points"],
        "truth_source": truth_src,
        **compare(asset_lwh, truth),
    }

    out_path = os.path.splitext(args.asset)[0] + "_bench.json"
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=2)

    print(json.dumps(report, indent=2))
    verdict = (
        "METRIC (within 5%)" if report["is_metric_within_5pct"]
        else f"NOT metric (off by {report['scale_error_pct']:.0f}%)"
    )
    print(
        f"\n[{label}] scale: {verdict} | shape error after rescale: "
        f"{report['shape_error_pct_mean']:.1f}% mean "
        f"({'/'.join(str(x) for x in report['shape_error_pct_per_axis'])} per axis)"
        f"\nreport -> {out_path}"
    )


if __name__ == "__main__":
    main()
