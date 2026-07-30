#!/usr/bin/env python3
"""Run OUR open-source pipelines over the benchmark sessions.

The in-house half of docs/INDUSTRY_BENCHMARK.md: the same sessions that
bench_kiri/bench_marble submit to industry APIs go through our stack, and
every geometry gets scored against the on-device ARKit mesh dims (the one
truth independent of both our fuse and the APIs).

CPU stage (runs today, per object session):
  P1 fuse   full-session TSDF (pose-fixed) -> scene mesh + dims.json
  P2 crop   object isolation from the fused scene -> object mesh + measure
  P3 icp    curate 48 -> multi-frame ICP object asset (catalog artifact)
  P4 scene  Lounge room session -> room-shell mesh (scene benchmark)
  score     bench_compare vs dims.json 'object' block (on-device mesh)

GPU stage (gsplat / 3DGUT / TRELLIS) is emitted as a queue script —
scripts/bench_gpu_queue.sh — because those need CUDA; run it after reboot.

Usage (assetpipe env):
    python tools/bench_ours.py                 # CPU stage, all sessions
    python tools/bench_ours.py --sessions BE53A423 1B38880A
    python tools/bench_ours.py --skip-icp      # fuse+crop+score only

Outputs: bench_out/<name>/ours_tsdf/, ours_object/, ours_bench.json,
and a summary table in bench_out/RESULTS.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# bench_compare is used by the API-side scoring; here we only cross-check
# the two independent measured geometries recorded in dims.json.

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP_DIR = os.path.join(REPO, "captures", "done")
WORK = os.path.join(REPO, "captures", "work", "bench_ours")
BENCH = os.path.join(REPO, "bench_out")
LOUNGE = os.path.join(REPO, "captures", "opensource_lounge_session")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def extract(name: str) -> str:
    """Unzip captures/done/CrateScan-<name>.zip -> work dir, return session dir."""
    zpath = os.path.join(ZIP_DIR, f"CrateScan-{name}.zip")
    root = os.path.join(WORK, name)
    if not os.path.isdir(root):
        os.makedirs(root, exist_ok=True)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(root)
    for dirpath, _d, files in os.walk(root):
        if "manifest.json" in files:
            return dirpath
    raise RuntimeError(f"no manifest.json in {zpath}")


def ensure_sim_export(name: str, row: dict) -> None:
    """P5: colliders + URDF/MJCF/USD from the cropped object mesh (idempotent)."""
    out = os.path.join(BENCH, f"CrateScan-{name}")
    mesh = os.path.join(out, "ours_tsdf", "scene_object_mesh.ply")
    sim_dir = os.path.join(out, "ours_sim")
    if not os.path.isfile(mesh):
        return
    if os.path.isdir(sim_dir) and any(
        f.endswith(".urdf") for f in os.listdir(sim_dir)
    ):
        row.setdefault("stages", {})["sim_export"] = {"cached": True}
        return
    try:
        from assetpipe.digitalize.sim_export import sim_export

        rep = sim_export(mesh, sim_dir, units="meters", label="scanned_object")
        row.setdefault("stages", {})["sim_export"] = {
            "hulls": rep.get("n_collision_hulls"),
            "mass_kg": rep.get("mass_kg"),
            "urdf": rep.get("urdf"),
        }
        log(f"{name}: sim-export {rep.get('n_collision_hulls')} hulls, "
            f"{rep.get('mass_kg', 0):.2f} kg")
    except Exception as e:  # noqa: BLE001 — P5 failing must not kill the batch
        row.setdefault("stages", {})["sim_export"] = {"error": str(e)[:120]}


def run_object_session(name: str, *, skip_icp: bool) -> dict:
    from assetpipe.scene.nvblox_fuse import fuse_session
    from assetpipe.scene.object_crop import crop_fused_dir, measure_object_crop
    from assetpipe.scene.measure import measure_cratescan_package
    from assetpipe.scene.rgbd_curate import curate_session
    from assetpipe.scene.rgbd_object_asset import build_object_asset

    session_dir = extract(name)
    out = os.path.join(BENCH, f"CrateScan-{name}")
    tsdf_out = os.path.join(out, "ours_tsdf")
    row: dict = {"session": name, "stages": {}}
    t0 = time.time()

    # P1 — full-session TSDF fuse + package measure (dims.json)
    res = fuse_session(session_dir, tsdf_out, backend="auto", viewer=False)
    row["n_frames"] = res.get("n_frames")
    row["stages"]["fuse"] = {"points": res.get("points"),
                             "backend": res.get("backend")}
    dims = measure_cratescan_package(session_dir, tsdf_out)
    dims_path = dims.get("dims_path") or os.path.join(tsdf_out, "dims.json")
    row["dims_path"] = dims_path
    log(f"{name}: fused {res.get('points')} pts "
        f"({res.get('n_frames')} frames, {res.get('backend')})")

    # P2 — object isolation + measure
    crop = crop_fused_dir(tsdf_out)
    if crop.get("ok"):
        measure_object_crop(tsdf_out)
        row["stages"]["crop"] = {
            "kept_frac": crop.get("kept_frac"),
            "object_mesh": crop.get("object_mesh") or crop.get("object_ply"),
        }
        log(f"{name}: crop kept {crop.get('kept_frac', 0):.0%}")
    else:
        row["stages"]["crop"] = {"error": crop.get("error")}

    # P3 — curated multi-frame ICP object asset
    if not skip_icp:
        cur_dir = os.path.join(out, "ours_curated")
        rep = curate_session(session_dir, cur_dir, target=48)
        obj_out = os.path.join(out, "ours_object")
        meta = build_object_asset(rep["curated_session"], obj_out,
                                  max_frames=10, stride=1)
        row["stages"]["icp_asset"] = {
            "frames_used": meta.get("frames_used"),
            "points": meta.get("points"),
        }
        log(f"{name}: ICP asset from {meta.get('frames_used')} frames")

    # Two honest numbers, not one misleading one:
    # (a) metric cross-check: on-device ARKit mesh vs our fused TSDF, both
    #     SCENE-scale AABBs from independent measurements of the same space.
    #     Coverage differs (ARKit meshes what it saw, TSDF what depth hit),
    #     so this is a consistency signal, not an exact equality.
    # (b) the object_crop dims — the deliverable the APIs get scored against.
    if os.path.exists(dims_path):
        with open(dims_path) as fh:
            d = json.load(fh)
        ondev = (d.get("object") or {}).get("aabb", {}).get("raw_inches", {})
        fused = (d.get("fused_tsdf") or {}).get("aabb", {}).get("raw_inches", {})
        if ondev and fused:
            ratios = {
                k: round(fused[k] / ondev[k], 3)
                for k in ("length", "width", "height")
                if ondev.get(k) and fused.get(k)
            }
            row["scene_aabb_fused_over_ondevice"] = ratios
        crop_dims = (d.get("object_crop") or {}).get("obb", {}) \
            .get("raw_inches_sorted_lwh")
        if crop_dims:
            row["object_crop_lwh_in"] = crop_dims

    row["wall_s"] = round(time.time() - t0, 1)
    with open(os.path.join(out, "ours_bench.json"), "w") as fh:
        json.dump(row, fh, indent=2)
    return row


def run_scene_lounge() -> dict:
    from assetpipe.scene.nvblox_fuse import fuse_session

    out = os.path.join(BENCH, "lounge", "ours_tsdf")
    t0 = time.time()
    res = fuse_session(LOUNGE, out, backend="auto", viewer=False)
    row = {"session": "lounge(scene)", "n_frames": res.get("n_frames"),
           "stages": {"fuse": {"points": res.get("points"),
                               "backend": res.get("backend")}},
           "wall_s": round(time.time() - t0, 1)}
    with open(os.path.join(BENCH, "lounge", "ours_bench.json"), "w") as fh:
        json.dump(row, fh, indent=2)
    log(f"lounge: fused {res.get('points')} pts (room shell)")
    return row


def write_results(rows: list[dict]) -> str:
    lines = [
        "# Our open-source pipelines — benchmark run",
        "",
        f"CPU stage run {time.strftime('%Y-%m-%d %H:%M')} — pose-fixed fuses "
        "(rebuilds the artifacts invalidated by the ARKit↔OpenCV fix). "
        "Scene AABB ratio compares our TSDF fuse to the on-device ARKit mesh "
        "— two independent measurements of the same space (coverage-sensitive; "
        "≈1.0 on each axis means the fuse is metrically consistent). "
        "Object L×W×H is the deliverable the APIs get scored against. "
        "GPU stages queued in scripts/bench_gpu_queue.sh.",
        "",
        "| session | frames | fused pts | crop kept | object L×W×H (in) | "
        "scene AABB fused/on-device (L,W,H) | wall s | note |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        crop = (r.get("stages") or {}).get("crop") or {}
        kept = crop.get("kept_frac")
        lwh = r.get("object_crop_lwh_in")
        lwh_s = " × ".join(f"{x:.1f}" for x in lwh) if lwh else "—"
        rat = r.get("scene_aabb_fused_over_ondevice") or {}
        rat_s = (f"{rat.get('length', '—')}, {rat.get('width', '—')}, "
                 f"{rat.get('height', '—')}" if rat else "—")
        lines.append(
            f"| {r['session']} | {r.get('n_frames', '—')} "
            f"| {(r.get('stages') or {}).get('fuse', {}).get('points', '—')} "
            f"| {f'{kept:.0%}' if isinstance(kept, float) else '—'} "
            f"| {lwh_s} | {rat_s} "
            f"| {r.get('wall_s', '—')} "
            f"| {r.get('error', crop.get('error', ''))} |"
        )
    lines += [
        "",
        "Industry columns (Kiri / Marble / Polycam) fill in from "
        "`bench_kiri.py` / `bench_marble.py` / `bench_polycam.py` runs — "
        "see docs/INDUSTRY_BENCHMARK.md.",
        "",
    ]
    path = os.path.join(BENCH, "RESULTS.md")
    with open(path, "w") as fh:
        fh.write("\n".join(lines))
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sessions", nargs="*",
                    help="short ids (default: every CrateScan zip)")
    ap.add_argument("--skip-icp", action="store_true",
                    help="skip the P3 multi-frame ICP asset (faster)")
    ap.add_argument("--skip-scene", action="store_true",
                    help="skip the Lounge room fuse")
    ap.add_argument("--force", action="store_true",
                    help="recompute even if ours_bench.json exists")
    args = ap.parse_args()

    names = args.sessions or sorted(
        f[len("CrateScan-"):-len(".zip")]
        for f in os.listdir(ZIP_DIR)
        if f.startswith("CrateScan-") and f.endswith(".zip")
    )

    rows = []
    for name in names:
        done_json = os.path.join(BENCH, f"CrateScan-{name}", "ours_bench.json")
        if not args.force and os.path.isfile(done_json):
            with open(done_json) as fh:
                row = json.load(fh)
            if "error" not in row:
                log(f"=== {name} === (cached — resume)")
                ensure_sim_export(name, row)
                with open(done_json, "w") as fh:
                    json.dump(row, fh, indent=2)
                rows.append(row)
                continue
        log(f"=== {name} ===")
        try:
            row = run_object_session(name, skip_icp=args.skip_icp)
            ensure_sim_export(name, row)
            with open(done_json, "w") as fh:
                json.dump(row, fh, indent=2)
            rows.append(row)
        except Exception as e:  # noqa: BLE001 — keep the batch going
            traceback.print_exc()
            rows.append({"session": name, "error": str(e)[:120]})
    lounge_json = os.path.join(BENCH, "lounge", "ours_bench.json")
    if not args.skip_scene:
        if not args.force and os.path.isfile(lounge_json):
            with open(lounge_json) as fh:
                rows.append(json.load(fh))
            log("lounge cached — resume")
        else:
            try:
                rows.append(run_scene_lounge())
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                rows.append({"session": "lounge(scene)", "error": str(e)[:120]})

    path = write_results(rows)
    log(f"results table -> {path}")


if __name__ == "__main__":
    main()
