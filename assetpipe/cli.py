"""assetpipe command line.

    python -m assetpipe demo                 # run the box-test slice, zero deps
    python -m assetpipe run  --input DIR ...  # run on a folder / quest session
    python -m assetpipe list                  # print the catalog
    python -m assetpipe viewer                # (re)build the control-center HTML
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .capture import FolderSource, Quest3SessionSource
from .catalog import AssetCatalog, build_viewer
from .detect import HeuristicDetector
from .pipeline import AssetPipeline, PipelineConfig
from .reconstruct import ProceduralBoxReconstructor


def _make_detector(name: str, classes):
    if name == "yolo-world":
        from .detect import YoloWorldDetector

        return YoloWorldDetector(classes=classes)
    if name == "grounded-sam2":
        from .detect import GroundedSam2Detector

        return GroundedSam2Detector(classes=classes)
    return HeuristicDetector(classes=classes, default_label="box")


def _make_reconstructor(name: str, recon_dir: str, box_dims, trellis_endpoint: str):
    if name == "trellis":
        from .reconstruct import TrellisReconstructor

        return TrellisReconstructor(recon_dir, endpoint=trellis_endpoint)
    if name == "nerfstudio":
        from .reconstruct import NerfstudioReconstructor

        return NerfstudioReconstructor(recon_dir)
    return ProceduralBoxReconstructor(recon_dir, default_dims=box_dims)


def _run_pipeline(source, classes, cfg: PipelineConfig, box_dims, args):
    catalog = AssetCatalog(os.path.join(cfg.out_dir, "twin.db"))
    recon_dir = os.path.join(cfg.out_dir, "_meshes")
    os.makedirs(recon_dir, exist_ok=True)
    detector = _make_detector(args.detector, classes)
    reconstructor = _make_reconstructor(
        args.reconstruct, recon_dir, box_dims, args.trellis_endpoint
    )
    pipe = AssetPipeline(source, detector, reconstructor, catalog, cfg)
    assets = pipe.run()
    viewer_path = os.path.join(cfg.out_dir, "control_center.html")
    build_viewer(catalog, viewer_path)
    catalog.close()
    return assets, viewer_path


def cmd_demo(args) -> int:
    """Synthesize the 'box test': three boxes -> meshes -> URDF -> twin."""
    cfg = PipelineConfig(out_dir=args.out, source_name="demo", location="test bench")
    catalog = AssetCatalog(os.path.join(cfg.out_dir, "twin.db"))
    recon_dir = os.path.join(cfg.out_dir, "_meshes")
    os.makedirs(recon_dir, exist_ok=True)
    reconstructor = ProceduralBoxReconstructor(recon_dir)

    # Three synthetic detections standing in for a real capture session.
    from .types import Detection, Frame

    samples = [
        ("cardboard box", (0.40, 0.30, 0.30), "garage shelf"),
        ("shoe box", (0.33, 0.13, 0.20), "bedroom closet"),
        ("moving carton", (0.50, 0.50, 0.50), "living room"),
    ]
    from .pipeline import AssetPipeline as _AP  # reuse digitalize logic

    ap = _AP(FolderSource(args.out), HeuristicDetector(), reconstructor, catalog, cfg)
    for i, (label, dims, loc) in enumerate(samples):
        frame = Frame(frame_id=f"demo{i}", image_path="(synthetic)", timestamp=float(i))
        det = Detection(label=label, score=1.0, bbox=(0, 0, 1280, 960),
                        frame_id=frame.frame_id, extra={"timestamp": float(i),
                                                        "dimensions_m": dims})
        recon = reconstructor.reconstruct(frame, det)
        cfg.location = loc
        asset = ap._digitalize(det, recon)
        catalog.add(asset)

    viewer = os.path.join(cfg.out_dir, "control_center.html")
    build_viewer(catalog, viewer)
    n = catalog.count()
    catalog.close()
    print(f"✔ digitalized {n} assets -> {cfg.out_dir}/")
    print(f"  catalog : {cfg.out_dir}/twin.db")
    print(f"  meshes  : {cfg.out_dir}/<asset_id>/model.obj (+ model.urdf)")
    print(f"  twin    : open {viewer} in a browser")
    return 0


def cmd_run(args) -> int:
    classes = args.classes.split(",") if args.classes else None
    box_dims = tuple(float(x) for x in args.box_dims.split(","))
    if args.quest_session:
        source = Quest3SessionSource(args.quest_session)
        name = "quest3"
    elif args.video:
        from .capture.video import VideoSource

        source = VideoSource(args.video, work_dir=args.out, fps=args.fps)
        name = "quest3"  # a Quest passthrough recording is the expected input
    else:
        source = FolderSource(args.input)
        name = "folder"
    cfg = PipelineConfig(out_dir=args.out, source_name=name, location=args.location,
                         dedupe_labels=args.dedupe)
    assets, viewer = _run_pipeline(source, classes, cfg, box_dims, args)
    print(f"✔ {len(assets)} assets ({args.detector} + {args.reconstruct}) "
          f"-> {cfg.out_dir}/  |  twin: {viewer}")
    return 0


def cmd_list(args) -> int:
    catalog = AssetCatalog(os.path.join(args.out, "twin.db"))
    rows = catalog.find(label=args.label, category=args.category, location=args.location)
    if not rows:
        print("(no assets)")
    for r in rows:
        dims = " x ".join(f"{r[k]*100:.0f}" for k in ("dim_w", "dim_h", "dim_d"))
        print(f"{r['asset_id']}  {r['label']:<18} {r['category']:<12} "
              f"{dims:>14} cm  @ {r['location'] or '?'}")
    catalog.close()
    return 0


def cmd_viewer(args) -> int:
    catalog = AssetCatalog(os.path.join(args.out, "twin.db"))
    out = os.path.join(args.out, "control_center.html")
    build_viewer(catalog, out)
    catalog.close()
    print(f"✔ control center -> {out}")
    return 0


def _export_son(out_dir, scan_id, endpoint, ingest, uploader=None) -> int:
    """Build the son quest-asset-scan payload from the catalog; optionally POST.

    When ``uploader`` is set, large meshes/scans (PLY/GLB) are pushed to blob
    storage and ``model_3d_ref`` becomes the URL — the raw file never touches
    son's API body.
    """
    from .integrations import build_quest_scan_payload, post_scan

    catalog = AssetCatalog(os.path.join(out_dir, "twin.db"))
    rows = catalog.all()
    catalog.close()
    action = "ingest_scan" if ingest else "preview_scan"
    payload = build_quest_scan_payload(rows, scan_id=scan_id, action=action, uploader=uploader)

    out_json = os.path.join(out_dir, "son_quest_scan.json")
    with open(out_json, "w") as fh:
        json.dump(payload, fh, indent=2)
    up = " + blob-upload" if uploader else ""
    print(f"✔ {len(payload['detected_assets'])} detections ({action}{up}) -> {out_json}")

    if endpoint:
        try:
            resp = post_scan(payload, endpoint)
            print(f"✔ POST {endpoint} -> ok={resp.get('ok')} "
                  f"persisted={resp.get('persisted')} status={resp.get('status')}")
        except Exception as e:  # noqa: BLE001
            print(f"!! POST to {endpoint} failed: {e}")
            return 1
    return 0


def _uploader_from_args(args):
    from .integrations import make_uploader

    if args.upload in (None, "none"):
        return None
    if args.upload == "local":
        if not args.blob_dir or not args.blob_base_url:
            raise SystemExit("--upload local needs --blob-dir and --blob-base-url")
        return make_uploader("local", dest_dir=args.blob_dir, base_url=args.blob_base_url)
    if args.upload == "vercel":
        token = args.blob_token or os.environ.get("BLOB_READ_WRITE_TOKEN")
        if not token:
            raise SystemExit("--upload vercel needs --blob-token or $BLOB_READ_WRITE_TOKEN")
        return make_uploader("vercel", token=token, prefix=args.blob_prefix)
    raise SystemExit(f"unknown --upload {args.upload!r}")


def cmd_export_son(args) -> int:
    """Emit the son `quest-asset-scan` payload from the catalog (optionally POST)."""
    return _export_son(args.out, args.scan_id, args.endpoint, args.ingest,
                       uploader=_uploader_from_args(args))


def _add_out(parser):
    parser.add_argument("--out", default="twin_out", help="output directory")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="assetpipe", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="run the zero-dependency box-test slice")
    _add_out(d); d.set_defaults(fn=cmd_demo)

    r = sub.add_parser("run", help="run the pipeline on a folder or quest session")
    _add_out(r)
    r.add_argument("--input", default=".", help="folder of images")
    r.add_argument("--quest-session", help="Quest 3 session dir (overrides --input)")
    r.add_argument("--video", help="video file (e.g. Quest passthrough recording); frames extracted via ffmpeg")
    r.add_argument("--fps", type=float, default=2.0, help="frame extraction rate for --video")
    r.add_argument("--dedupe", action="store_true",
                   help="keep only the best detection per label (one asset per object)")
    r.add_argument("--classes", help="comma-separated open-vocab labels")
    r.add_argument("--location", help="human location label for these captures")
    r.add_argument("--box-dims", default="0.3,0.3,0.3", help="fallback WxHxD meters")
    r.add_argument("--detector", default="heuristic",
                   choices=["heuristic", "yolo-world", "grounded-sam2"],
                   help="perception backend (GPU backends need extras installed)")
    r.add_argument("--reconstruct", default="procedural",
                   choices=["procedural", "trellis", "nerfstudio"],
                   help="reconstruction backend")
    r.add_argument("--trellis-endpoint", default="http://localhost:8080/generate",
                   help="URL of the TRELLIS GPU service")
    r.set_defaults(fn=cmd_run)

    l = sub.add_parser("list", help="print the catalog")
    _add_out(l)
    l.add_argument("--label"); l.add_argument("--category"); l.add_argument("--location")
    l.set_defaults(fn=cmd_list)

    v = sub.add_parser("viewer", help="rebuild the control-center HTML")
    _add_out(v); v.set_defaults(fn=cmd_viewer)

    e = sub.add_parser("export-son", help="emit son quest-asset-scan payload (optionally POST)")
    _add_out(e)
    e.add_argument("--scan-id", default="assetpipe-scan", help="scan id for the batch")
    e.add_argument("--endpoint", help="son /api/quest-asset-scan URL to POST to")
    e.add_argument("--ingest", action="store_true",
                   help="use action=ingest_scan (persist) instead of preview_scan")
    # blob upload for large meshes/scans (PLY/GLB) -> model_3d_ref becomes a URL
    e.add_argument("--upload", default="none", choices=["none", "local", "vercel"],
                   help="upload meshes to blob storage and reference by URL")
    e.add_argument("--blob-dir", help="[local] directory served at --blob-base-url")
    e.add_argument("--blob-base-url", help="[local] public base URL for --blob-dir")
    e.add_argument("--blob-token", help="[vercel] token (or $BLOB_READ_WRITE_TOKEN)")
    e.add_argument("--blob-prefix", default="scans", help="[vercel] path prefix")
    e.set_defaults(fn=cmd_export_son)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
