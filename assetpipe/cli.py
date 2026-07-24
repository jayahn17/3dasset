"""assetpipe command line.

    python -m assetpipe demo                 # run the box-test slice, zero deps
    python -m assetpipe room VIDEO            # ONE sweep -> every object -> an asset each
    python -m assetpipe scan VIDEO            # Scaniverse-style: -> point cloud .ply/.splat
    python -m assetpipe rgbd SESSION          # RGB-D+pose -> nvblox/TSDF mesh (LiDAR path)
    python -m assetpipe cratescan EXPORT      # CrateScanner OBJ/STL (+ dims) -> meters mesh
    python -m assetpipe clean scene.ply       # auto background removal -> the asset
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


def cmd_cratescan(args) -> int:
    """Ingest CrateScanner OBJ/STL (+ measurement.json) → meters mesh + dims.json."""
    from .scene.cratescan_ingest import CrateScanError, ingest_cratescan

    try:
        res = ingest_cratescan(args.input, args.out, units=args.units)
    except CrateScanError as e:
        print(f"!! {e}")
        print("   See docs/CRATESCANNER_BRIDGE.md")
        return 2
    print(f"✔ cratescan -> {res.get('out_dir')}")
    for k in ("mesh_meters_ply", "mesh_meters_obj", "dims", "vertices", "triangles", "extent_m"):
        if k in res:
            print(f"  {k:<16} {res[k]}")
    return 0


def cmd_rgbd(args) -> int:
    """RGB-D + pose session → nvblox / TSDF mesh (KIRI-class depth fusion).

    Requires a session with color, depth, pose, and intrinsics. RGB-only
    photo folders (Camera.app HEIC) are rejected with a capture checklist.
    """
    from .scene.nvblox_fuse import backend_available, fuse_session
    from .scene.rgbd_session import RgbdSessionError, inspect_path

    report = inspect_path(args.input)
    print(json.dumps(report, indent=2))
    if args.inspect_only:
        return 0 if report.get("ok_for_nvblox") else 2

    if not report.get("ok_for_nvblox"):
        print("\n!! Cannot run nvblox / TSDF — depth+pose session required.")
        print("   Re-capture on iPhone LiDAR (Record3D / 3D Scanner / Polycam)")
        print("   or Quest Depth API → session layout in docs/NVBLOX_WORKFLOW.md")
        avail = backend_available()
        print(f"   backends on this box: {avail}")
        return 2

    try:
        res = fuse_session(
            args.input,
            args.out,
            backend=args.backend,
            voxel_size=args.voxel_size,
            clean=not args.no_clean,
            mesh=not args.no_mesh,
            viewer=not args.no_viewer,
        )
    except RgbdSessionError as e:
        print(f"!! {e}")
        return 2

    print(f"✔ fused {res.get('points', '?')} points ({res.get('backend')}, "
          f"{res.get('n_frames')} frames)")
    for k in ("ply", "clean_ply", "mesh", "tsdf_mesh", "viewer", "meta"):
        if k in res:
            print(f"  {k:<12} {res[k]}")
    return 0


def cmd_scan(args) -> int:
    """Scaniverse-style: video/folder -> ONE point cloud -> .ply/.splat + viewer."""
    from .scene import scan_scene

    if os.path.isdir(args.input):
        import glob

        paths = sorted(
            p for ext in ("jpg", "jpeg", "png", "ppm")
            for p in glob.glob(os.path.join(args.input, f"*.{ext}"))
        )
    else:
        from .capture.video import VideoSource

        paths = VideoSource(args.input, work_dir=args.out, fps=args.fps,
                            max_frames=args.max_frames,
                            max_width=args.width).extract()
    if not paths:
        print(f"!! no frames found in {args.input}")
        return 1
    title = os.path.splitext(os.path.basename(args.input.rstrip(os.sep)))[0]
    res = scan_scene(paths, args.out, backend=args.backend, splat=args.splat,
                     viewer=not args.no_viewer, title=title, clean=args.clean,
                     mesh=args.mesh)
    print(f"✔ {res['points']} points ({res['backend']}, {len(paths)} frames)")
    if "clean_points" in res:
        print(f"  asset: {res['clean_points']} points after background removal "
              f"(-{res['points'] - res['clean_points']})")
    if "mesh_faces" in res:
        print(f"  mesh: {res['mesh_faces']} faces ({res['mesh_method']})")
    if "mesh_error" in res:
        print(f"  !! mesh failed: {res['mesh_error']}")
    for k in ("ply", "splat", "clean_ply", "clean_splat", "mesh",
              "gaussian_ply", "usdz", "viewer"):
        if k in res:
            print(f"  {k:<12} {res[k]}")
    return 0


def cmd_clean(args) -> int:
    """Background removal on an existing scene.ply -> scene_clean.ply."""
    from .scene import ScenePointCloud, clean_cloud, write_ply, write_splat, read_ply
    from .scene.viewer import build_scan_viewer

    xyz, rgb = read_ply(args.input)
    cleaned = clean_cloud(ScenePointCloud(xyz, rgb))
    out_dir = args.out or os.path.dirname(args.input) or "."
    ply = write_ply(os.path.join(out_dir, "scene_clean.ply"),
                    cleaned.xyz, cleaned.rgb)
    print(f"✔ {len(xyz)} -> {len(cleaned.xyz)} points "
          f"(outliers {cleaned.stats.get('outliers_removed', 0)}, "
          f"ground {cleaned.stats.get('ground_removed', 0)}, "
          f"clutter {cleaned.stats.get('clutter_removed', 0)})")
    print(f"  clean_ply    {ply}")
    if args.splat:
        print(f"  clean_splat  {write_splat(os.path.join(out_dir, 'scene_clean.splat'), cleaned.xyz, cleaned.rgb)}")
    if not args.no_viewer:
        v = build_scan_viewer(os.path.join(out_dir, "clean_view.html"),
                              cleaned.xyz, cleaned.rgb, title="cleaned asset",
                              stats=cleaned.stats,
                              links={"scene_clean.ply": "scene_clean.ply"})
        print(f"  viewer       {v}")
    return 0


def cmd_views(args) -> int:
    """Scanner output (Scaniverse/Polycam PLY) -> isolated object -> orbit
    views: the conditioning images an image-to-3D generator wants."""
    from .scene.splat import isolate_object, load_ply, render_orbit_views

    xyz, rgb = load_ply(args.input, min_opacity=args.min_opacity)
    print(f"  loaded   {len(xyz):,} points from {os.path.basename(args.input)}")
    if not args.no_isolate:
        xyz, rgb = isolate_object(xyz, rgb, focus=args.focus)
        print(f"  isolated {len(xyz):,} points (room/ground removed)")
        if len(xyz) < 10:
            print("!! nothing left after isolation — rerun with --no-isolate")
            return 1
    paths = render_orbit_views(xyz, rgb, args.out, n_views=args.n,
                               size=args.size, transparent=not args.white)
    print(f"✔ {len(paths)} views -> {args.out}/")
    print(f"  feed these to TRELLIS / Hunyuan3D / Meshy / Tripo "
          f"({'RGBA' if not args.white else 'white bg'}, {args.size}px)")
    return 0


def cmd_generate(args) -> int:
    """Photos / video / scan -> generative image-to-3D -> real asset GLB.

    REAL PHOTOS BEAT A SCAN. These models were trained on photographs; a
    render of a point cloud is a photo of a mediocre reconstruction, with
    its holes, speckle and baked-in lighting. Feed the actual pixels.
    """
    import glob

    from .scene.generate import asset_from_scan, generate_asset

    lower = args.input.lower()
    try:
        if lower.endswith(".ply"):
            res = asset_from_scan(args.input, args.out, backend=args.backend,
                                  endpoint=args.endpoint, views=args.views,
                                  focus=args.focus, seed=args.seed)
            print(f"  isolated {res['points']:,} points -> "
                  f"{res['views_rendered']} views")
        else:
            if os.path.isdir(args.input):
                paths = sorted(
                    p for ext in ("png", "jpg", "jpeg", "webp", "PNG", "JPG", "JPEG")
                    for p in glob.glob(os.path.join(args.input, f"*.{ext}"))
                )
                if not paths:
                    print(f"!! no images in {args.input}")
                    return 1
                print(f"  {len(paths)} photos")
            else:  # a video: pull frames, then pick the good ones
                from .capture.video import VideoSource

                paths = VideoSource(args.input, work_dir=args.out, fps=args.fps,
                                    max_frames=args.max_frames).extract()
                if not paths:
                    print(f"!! no frames extracted from {args.input}")
                    return 1
                print(f"  {len(paths)} frames @ {args.fps} fps")
            res = generate_asset(paths,
                                 os.path.join(args.out, f"asset_{args.backend}.glb"),
                                 backend=args.backend, endpoint=args.endpoint,
                                 views=args.views, seed=args.seed)
    except Exception as e:  # noqa: BLE001 — the service carries the real message
        print(f"!! {e}")
        print("   is the generator running?  conda activate gen3d && "
              "python services/gen3d_server.py")
        return 1
    print(f"✔ {res['backend']}: {res['views_used']} views (background removed) "
          f"-> {res['glb']} ({res['bytes'] / 1e6:.1f} MB)")
    return 0


def _frames_from(input_path: str, out: str, fps: float, max_frames: int) -> list:
    """Video or folder -> frames IN CAPTURE ORDER (the tracker depends on it)."""
    import glob

    if os.path.isdir(input_path):
        return sorted(
            p for ext in ("jpg", "jpeg", "png", "JPG", "JPEG", "PNG")
            for p in glob.glob(os.path.join(input_path, f"*.{ext}"))
        )
    from .capture.video import VideoSource

    return VideoSource(input_path, work_dir=out, fps=fps,
                       max_frames=max_frames).extract()


def cmd_room(args) -> int:
    """ONE room sweep -> every object, tracked -> an asset each -> the twin.

    This is the scale step: the per-object path (views -> generator -> GLB)
    already worked, but the view stacks had to be produced by hand, once per
    object. Here a tracker produces all of them from a single walk-around.
    """
    from .scene.room import sweep_room

    frames = _frames_from(args.input, args.out, args.fps, args.max_frames)
    if not frames:
        print(f"!! no frames in {args.input}")
        return 1
    classes = args.classes.split(",") if args.classes else None
    print(f"  {len(frames)} frames  ->  discovering objects…")

    def _progress(i, n, t):
        print(f"  [{i}/{n}] generating {t.label} (track {t.track_id}, "
              f"{len(t.views)} views)…", flush=True)

    m = sweep_room(
        frames, args.out, classes=classes, backend=args.backend,
        masker=args.masker, gen_backend=args.gen_backend,
        endpoint=args.endpoint, conf=args.conf, views=args.views,
        gen_views=args.gen_views, min_views=args.min_views,
        location=args.location, generate=not args.no_generate,
        limit=args.limit, on_progress=_progress)

    ok = [t for t in m["tracks"] if t["status"] == "ok"]
    print(f"\n✔ {len(m['tracks'])} objects found ({m['discover_backend']}), "
          f"{len(ok)} passed the quality gate")
    for t in m["tracks"]:
        flag = "  " if t["status"] == "ok" else "!!"
        print(f"  {flag} track {t['track_id']:>3}  {t['label']:<16} "
              f"{t['n_frames']:>3} frames  {t['area_frac'] * 100:>5.1f}% "
              f"of frame   {t['status']}")
    if m["assets"]:
        print(f"\n✔ {len(m['assets'])} assets generated -> {args.out}/")
    for f in m["failed"]:
        print(f"  !! track {f['track_id']} ({f['label']}): {f['error']}")
    print(f"  twin    {os.path.join(args.out, 'twin.db')}")
    print(f"  sweep   {os.path.join(args.out, 'sweep.json')}")
    print(f"  viewer  {m['viewer']}")
    return 0


def cmd_list(args) -> int:
    catalog = AssetCatalog(os.path.join(args.out, "twin.db"))
    rows = catalog.find(label=args.label, category=args.category, location=args.location)
    if not rows:
        print("(no assets)")
    for r in rows:
        # A generated GLB is normalised: it knows its shape, not its size.
        # Printing those numbers as centimetres would invent a measurement
        # nobody took, so relative-scale assets say so until a metric source
        # (Quest pose / a scan with a reference) grounds them.
        try:
            relative = json.loads(r["extra"] or "{}").get("scale") == "relative"
        except (ValueError, IndexError, KeyError):
            relative = False
        if relative:
            dims = " x ".join(f"{r[k]:.2f}" for k in ("dim_w", "dim_h", "dim_d"))
            unit = "rel"
        else:
            dims = " x ".join(f"{r[k] * 100:.0f}" for k in ("dim_w", "dim_h", "dim_d"))
            unit = "cm "
        print(f"{r['asset_id']}  {r['label']:<18} {r['category']:<12} "
              f"{dims:>16} {unit} @ {r['location'] or '?'}")
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

    s = sub.add_parser("scan", help="video/folder -> point cloud -> .ply/.splat (no detect/catalog)")
    s.add_argument("input", help="video file (Quest passthrough recording) or folder of frames")
    s.add_argument("--out", default="scan_out", help="output directory")
    s.add_argument("--backend", default="auto",
                   choices=["auto", "stub", "colmap", "vggt", "splatfacto", "3dgut"],
                   help="scene backend (auto = best installed: vggt > colmap > stub; "
                        "3dgut = nv-tlabs/3dgrut room shell + optional USDZ)")
    s.add_argument("--fps", type=float, default=2.0, help="frame extraction rate for video input")
    s.add_argument("--max-frames", type=int, default=60, help="cap extracted frames")
    s.add_argument("--width", type=int, default=1280,
                   help="downscale frames to this width (SfM sweet spot; 0 = keep full res)")
    s.add_argument("--splat", action="store_true", help="also write scene.splat")
    s.add_argument("--clean", action="store_true",
                   help="auto-remove background (ground/outliers/clutter) -> scene_clean.ply")
    s.add_argument("--mesh", action="store_true",
                   help="also surface the cloud as a vertex-colored GLB (scene_mesh.glb)")
    s.add_argument("--no-viewer", action="store_true", help="skip scan_view.html")
    s.set_defaults(fn=cmd_scan)

    rg = sub.add_parser(
        "rgbd",
        help="RGB-D+pose session -> nvblox/TSDF mesh (needs LiDAR depth; not RGB-only photos)",
    )
    rg.add_argument("input", help="session/ dir with manifest.json (color+depth+pose+K)")
    rg.add_argument("--out", default="rgbd_out", help="output directory")
    rg.add_argument("--backend", default="auto", choices=["auto", "nvblox", "open3d"],
                    help="fusion backend (auto prefers nvblox_torch, else Open3D TSDF)")
    rg.add_argument("--voxel-size", type=float, default=0.01, help="TSDF voxel size in meters")
    rg.add_argument("--inspect-only", action="store_true",
                    help="only print whether the input is fuse-ready (exit 2 if not)")
    rg.add_argument("--no-clean", action="store_true", help="skip background cleanup")
    rg.add_argument("--no-mesh", action="store_true", help="skip Poisson/GLB meshing of cloud")
    rg.add_argument("--no-viewer", action="store_true", help="skip scan_view.html")
    rg.set_defaults(fn=cmd_rgbd)

    cs = sub.add_parser(
        "cratescan",
        help="CrateScanner export (OBJ/STL + measurement.json) -> meters mesh + dims.json",
    )
    cs.add_argument("input", help="export folder or a single .obj/.stl mesh")
    cs.add_argument("--out", default="cratescan_out", help="output directory")
    cs.add_argument("--units", default="inches", choices=["inches", "meters"],
                    help="units baked into the mesh (CrateScanner default: inches)")
    cs.set_defaults(fn=cmd_cratescan)

    c = sub.add_parser("clean", help="background removal on an existing scene.ply")
    c.add_argument("input", help="scene.ply from a previous scan")
    c.add_argument("--out", help="output dir (default: beside the input)")
    c.add_argument("--splat", action="store_true", help="also write scene_clean.splat")
    c.add_argument("--no-viewer", action="store_true", help="skip clean_view.html")
    c.set_defaults(fn=cmd_clean)

    w = sub.add_parser("views", help="scanner PLY (Scaniverse/Polycam) -> isolated object -> orbit views for image-to-3D")
    w.add_argument("input", help="scan .ply (point cloud or gaussian splat)")
    w.add_argument("--out", default="views", help="output directory for the PNGs")
    w.add_argument("--n", type=int, default=8, help="views per elevation ring")
    w.add_argument("--size", type=int, default=768, help="render resolution")
    w.add_argument("--white", action="store_true",
                   help="white background instead of transparent")
    w.add_argument("--no-isolate", action="store_true",
                   help="skip room/ground removal (scan is already just the object)")
    w.add_argument("--focus", type=float, default=0.9,
                   help="crop radius as a multiple of the object scale (raise if clipped, lower if clutter creeps in)")
    w.add_argument("--min-opacity", type=float, default=0.25,
                   help="drop gaussians fainter than this (splat PLY only)")
    w.set_defaults(fn=cmd_views)

    g = sub.add_parser("generate",
                       help="photos/ or video or scan.ply -> TRELLIS/Hunyuan3D -> asset GLB")
    g.add_argument("input",
                   help="folder of PHOTOS (best), a video file, or a scanner .ply")
    g.add_argument("--out", default="asset_out", help="output directory")
    g.add_argument("--backend", default="trellis", choices=["trellis", "hunyuan3d"])
    g.add_argument("--endpoint", default="http://localhost:8080/generate",
                   help="gen3d service URL (services/gen3d_server.py)")
    g.add_argument("--views", type=int, default=4,
                   help="how many views to condition on (sharpest, spread around)")
    g.add_argument("--fps", type=float, default=1.0, help="[video] frame extraction rate")
    g.add_argument("--max-frames", type=int, default=40, help="[video] cap frames")
    g.add_argument("--focus", type=float, default=0.9, help="[.ply] isolation crop")
    g.add_argument("--seed", type=int, default=1)
    g.set_defaults(fn=cmd_generate)

    rm = sub.add_parser("room",
                        help="ONE room video -> every object tracked -> an asset each")
    rm.add_argument("input", help="room sweep video, or a folder of frames in capture order")
    rm.add_argument("--out", default="room_out", help="output directory")
    rm.add_argument("--classes", help="comma-separated objects to look for "
                                      "(default: a household vocabulary)")
    rm.add_argument("--backend", default="auto", choices=["auto", "sam3", "track"],
                    help="object discovery: sam3 (gated weights) or track "
                         "(YOLO-World+SAM2, works today); auto picks sam3 if sam3.pt exists")
    rm.add_argument("--masker", default="sam2", choices=["sam2", "rembg", "box"],
                    help="[track backend] how a box becomes a mask")
    rm.add_argument("--gen-backend", default="trellis", choices=["trellis", "hunyuan3d"])
    rm.add_argument("--endpoint", default="http://localhost:8080/generate",
                    help="gen3d service URL (services/gen3d_server.py)")
    rm.add_argument("--fps", type=float, default=2.0, help="[video] frame extraction rate")
    rm.add_argument("--max-frames", type=int, default=300, help="[video] cap frames")
    rm.add_argument("--conf", type=float, default=0.15, help="detection confidence floor")
    rm.add_argument("--views", type=int, default=8, help="views kept per object")
    rm.add_argument("--gen-views", type=int, default=4, help="views fed to the generator")
    rm.add_argument("--min-views", type=int, default=4,
                    help="quality gate: fewer frames than this -> not reconstructed")
    rm.add_argument("--limit", type=int, default=0, help="only generate the first N objects")
    rm.add_argument("--location", help="human location label, e.g. 'bedroom'")
    rm.add_argument("--no-generate", action="store_true",
                    help="discover + cut out only; skip the GPU pass (fast dry run)")
    rm.set_defaults(fn=cmd_room)

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
