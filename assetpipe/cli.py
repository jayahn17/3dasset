"""assetpipe command line.

    python -m assetpipe demo                 # run the box-test slice, zero deps
    python -m assetpipe run  --input DIR ...  # run on a folder / quest session
    python -m assetpipe list                  # print the catalog
    python -m assetpipe viewer                # (re)build the control-center HTML
"""

from __future__ import annotations

import argparse
import os
import sys

from .capture import FolderSource, Quest3SessionSource
from .catalog import AssetCatalog, build_viewer
from .detect import HeuristicDetector
from .pipeline import AssetPipeline, PipelineConfig
from .reconstruct import ProceduralBoxReconstructor


def _run_pipeline(source, classes, cfg: PipelineConfig, box_dims):
    catalog = AssetCatalog(os.path.join(cfg.out_dir, "twin.db"))
    detector = HeuristicDetector(classes=classes, default_label="box")
    recon_dir = os.path.join(cfg.out_dir, "_meshes")
    os.makedirs(recon_dir, exist_ok=True)
    reconstructor = ProceduralBoxReconstructor(recon_dir, default_dims=box_dims)
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
    else:
        source = FolderSource(args.input)
        name = "folder"
    cfg = PipelineConfig(out_dir=args.out, source_name=name, location=args.location)
    assets, viewer = _run_pipeline(source, classes, cfg, box_dims)
    print(f"✔ {len(assets)} assets -> {cfg.out_dir}/  |  twin: {viewer}")
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
    r.add_argument("--classes", help="comma-separated open-vocab labels")
    r.add_argument("--location", help="human location label for these captures")
    r.add_argument("--box-dims", default="0.3,0.3,0.3", help="fallback WxHxD meters")
    r.set_defaults(fn=cmd_run)

    l = sub.add_parser("list", help="print the catalog")
    _add_out(l)
    l.add_argument("--label"); l.add_argument("--category"); l.add_argument("--location")
    l.set_defaults(fn=cmd_list)

    v = sub.add_parser("viewer", help="rebuild the control-center HTML")
    _add_out(v); v.set_defaults(fn=cmd_viewer)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
