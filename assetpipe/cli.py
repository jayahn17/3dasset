"""assetpipe command line.

    python -m assetpipe demo                 # run the box-test slice, zero deps
    python -m assetpipe route DIR            # trellis (single) vs 3dgrut (multi/scene)
    python -m assetpipe pipeline run INPUT   # offline multi-backend refine (v001…)
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

    # Object L×W×H from package mesh.obj (0.25″ quantum) + optional fused scene check
    try:
        from .scene.measure import inject_dims_hud, measure_cratescan_package

        dims = measure_cratescan_package(
            args.input,
            args.out,
            padding_inches=getattr(args, "padding", 2.0),
        )
        obj = dims.get("object") or {}
        aabb = obj.get("aabb") or {}
        if aabb:
            q = aabb["inches_0_25"]
            print(f"✔ measure @0.25″  "
                  f"{q['length']:.2f} × {q['width']:.2f} × {q['height']:.2f} in "
                  f"(L×W×H)  raw {aabb.get('summary_raw', aabb.get('summary'))}")
            print(f"  crate +{aabb['crate_inches_0_25']['padding']:.1f}\"  "
                  f"{aabb.get('crate_summary')}")
            print(f"  dims         {dims.get('dims_path')}")
        viewer = res.get("viewer")
        if viewer:
            inject_dims_hud(viewer, dims)
    except Exception as e:  # noqa: BLE001 — fuse already succeeded
        print(f"  !! measure skipped: {e}")
    return 0


def cmd_crop(args) -> int:
    """Isolate object from a fused scan dir (scene_clean/scene.ply → scene_object.*)."""
    from .scene.object_crop import crop_fused_dir, measure_object_crop

    out = args.input
    crop = crop_fused_dir(out, focus=args.focus, also_mesh=not args.no_mesh)
    if not crop.get("ok"):
        print(f"!! crop failed: {crop.get('error')}")
        return 2
    print(f"✔ object crop {crop['points_in']} → {crop['points_out']} pts "
          f"(kept {crop['kept_frac']:.0%})")
    for k in ("object_ply", "object_splat", "object_mesh", "object_viewer"):
        if k in crop:
            print(f"  {k:<14} {crop[k]}")
    mo = measure_object_crop(out, padding_inches=args.padding)
    if mo:
        q = mo["aabb"]["inches_0_25"]
        print(f"✔ measure @0.25″  {q['length']:.2f} × {q['width']:.2f} × {q['height']:.2f} in")
    return 0


def cmd_pipeline_preprocess(args) -> int:
    """Normalize iPad/photos/video → curated dataset pack (data only)."""
    from .preprocess import build_dataset_pack

    man = build_dataset_pack(
        args.input,
        args.out,
        target_frames=args.frames,
        video_fps=args.fps,
    )
    print(f"✔ dataset pack  {args.out}/dataset")
    print(f"  kind         {man.get('kind')}")
    print(f"  frames       {man.get('n_frames')}")
    print(f"  depth/poses  {man.get('has_depth')}/{man.get('has_poses')}")
    return 0


def cmd_pipeline_run(args) -> int:
    """Preprocess → Open3D/COLMAP/TRELLIS/3DGRUT → versioned refine."""
    from .offline import pipeline_run

    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    summary = pipeline_run(
        args.input,
        args.out,
        backends=backends,
        versions=args.versions,
        target_frames=args.frames,
        video_fps=args.fps,
        trellis_endpoint=args.trellis_endpoint,
        try_generative=not args.no_generative,
        skip_3dgrut=args.no_3dgrut,
        focus=args.focus,
    )
    print(f"✔ pipeline     backends_ok={summary.get('backends_ok')}")
    print(f"  versions     {summary.get('versions')}")
    if summary.get("open"):
        print(f"  open         {summary['open']}")
    print(f"  summary      {args.out}/pipeline_summary.json")
    return 0


def cmd_pipeline_refine(args) -> int:
    """Continue offline refine versions on an existing run dir."""
    from .offline import pipeline_refine

    summary = pipeline_refine(
        args.input,
        versions=args.versions,
        trellis_endpoint=args.trellis_endpoint,
        try_generative=not args.no_generative,
        focus=args.focus,
    )
    print(f"✔ refine       {summary.get('versions')}")
    if summary.get("open"):
        print(f"  open         {summary['open']}")
    return 0


def cmd_curate(args) -> int:
    """Score every RGB-D frame → reject junk → write diverse keyframe session."""
    from .scene.rgbd_curate import curate_session

    report = curate_session(
        args.input,
        args.out,
        target=args.target,
        min_angle_deg=args.min_angle,
        min_step_m=args.min_step,
        sharp_pct=args.sharp_pct,
        min_edge=args.min_edge,
        depth_lo=args.depth_lo,
        depth_hi=args.depth_hi,
        max_step_m=args.max_pose_step or None,
    )
    print(
        f"✔ curated {report['n_source']} → {report['n_kept']} frames "
        f"(rejected {report['n_rejected']})"
    )
    print(f"  rejects      {report.get('reject_counts')}")
    q = report["quality"]
    print(
        f"  sharp        {q['all_sharp_mean']:.0f} → {q['kept_sharp_mean']:.0f}  "
        f"quality {q['all_mean']:.0f} → {q['kept_mean']:.0f}"
    )
    print(f"  session      {report['curated_session']}")
    print(f"  html         {report.get('html')}")

    if args.then == "object":
        from .scene.rgbd_object_asset import build_object_asset

        obj_out = args.object_out or (args.out.rstrip("/") + "_object")
        meta = build_object_asset(
            report["curated_session"],
            obj_out,
            max_frames=args.object_frames,
            stride=1,
        )
        print(
            f"✔ object asset {meta['n_session_frames']} → used {meta['frames_used']} "
            f"({meta['points']:,} pts)"
        )
        print(f"  open         {meta['artifacts']['open_me']}")
        dims = meta.get("dims")
        if dims:
            qq = dims["aabb"]["inches_0_25"]
            print(
                f"✔ measure @0.25″  "
                f"{qq['length']:.2f} × {qq['width']:.2f} × {qq['height']:.2f} in"
            )
    return 0


def cmd_object(args) -> int:
    """Analyze RGB-D session → pick best 1–N frames → object asset (no TSDF)."""
    from .scene.rgbd_object_asset import build_object_asset

    session_in = args.input
    if args.curate:
        from .scene.rgbd_curate import curate_session

        curated_dir = args.curate_out or (args.out.rstrip("/") + "_curated_session")
        report = curate_session(
            args.input,
            curated_dir,
            target=args.curate_target,
            min_angle_deg=args.min_angle,
            min_step_m=args.min_step,
        )
        print(
            f"✔ curated {report['n_source']} → {report['n_kept']} frames "
            f"(see {report.get('html')})"
        )
        session_in = report["curated_session"]

    meta = build_object_asset(
        session_in,
        args.out,
        max_frames=args.max_frames,
        stride=args.stride,
        frame=args.frame,
    )
    print(f"✔ object asset  {meta['n_session_frames']} frames → used {meta['frames_used']}")
    print(f"  pick         {meta['pick_note']}")
    print(f"  points       {meta['points']:,}")
    art = meta["artifacts"]
    print(f"  open         {art['open_me']}")
    print(f"  viewer       {art['viewer']}")
    print(f"  ply          {art['ply']}")
    print(f"  analysis     {art['analysis']}")
    dims = meta.get("dims")
    if dims:
        q = dims["aabb"]["inches_0_25"]
        print(f"✔ measure @0.25″  "
              f"{q['length']:.2f} × {q['width']:.2f} × {q['height']:.2f} in")
    return 0


def cmd_measure(args) -> int:
    """L×W×H inches from mesh.obj / package / fused PLY, quantized to 0.25\"."""
    from .scene.measure import inject_dims_hud, measure_cratescan_package

    dims = measure_cratescan_package(
        args.input,
        args.out,
        padding_inches=args.padding,
        also_fuse_mesh=not args.no_fuse_check,
    )
    obj = dims.get("object") or dims.get("fused_tsdf") or dims.get("fused_clean")
    if not obj:
        print(f"!! no measurable mesh under {args.input}")
        print(json.dumps(dims, indent=2)[:500])
        return 2
    aabb = obj["aabb"]
    q = aabb["inches_0_25"]
    print(f"✔ {obj['label']} @0.25″  "
          f"{q['length']:.2f} × {q['width']:.2f} × {q['height']:.2f} in (L×W×H)")
    print(f"  raw          {aabb.get('summary_raw', aabb['summary'])}")
    print(f"  crate        {aabb['crate_summary']}")
    print(f"  OBB sorted   {obj['obb']['summary_sorted']}")
    print(f"  dims         {dims.get('dims_path')}")
    print(f"  text         {dims.get('measurement_txt')}")
    viewer = os.path.join(args.out, "scan_view.html")
    if os.path.isfile(viewer):
        inject_dims_hud(viewer, dims)
        print(f"  viewer       {viewer} (dims HUD updated)")
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


def cmd_route(args) -> int:
    """Classify a capture as trellis (single object) vs 3dgrut (multi/scene)."""
    from .scene.route import classify_capture, write_decision

    decision = classify_capture(args.input, force=args.force)
    out = args.out
    if out:
        if os.path.isdir(out) or out.endswith(os.sep):
            out = os.path.join(out, "route_decision.json")
        write_decision(decision, out)
    print(f"✔ route: {decision.route}")
    for r in decision.reasons:
        print(f"  · {r}")
    print("  next:")
    for s in decision.next_steps:
        print(f"    - {s}")
    if out:
        print(f"  wrote      {out}")
    if args.json:
        print(json.dumps(decision.to_dict(), indent=2))
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


def cmd_sim_export(args) -> int:
    """Mesh/GLB → sim-ready USD + URDF + MJCF (colliders, mass, inertia)."""
    from .digitalize.sim_export import sim_export

    rep = sim_export(
        args.mesh,
        args.out,
        units=args.units,
        up=args.up,
        material=args.material,
        density=args.density,
        mass_kg=args.mass,
        target_size_m=args.target_size,
        label=args.label,
        qcode=args.qcode,
        max_hulls=args.max_hulls,
    )
    print(f"✔ sim export  {rep['asset']}  "
          f"{rep['extent_m'][0]:.3f} × {rep['extent_m'][1]:.3f} × "
          f"{rep['extent_m'][2]:.3f} m   mass {rep['mass_kg']:.3f} kg "
          f"({rep['mass_source']})")
    print(f"  hulls {rep['n_collision_hulls']}  watertight={rep['watertight']}")
    for k in ("usd", "urdf", "mjcf"):
        if rep.get(k):
            print(f"  {k:5s} {rep[k]}")
    return 0


def cmd_world(args) -> int:
    """Constant scene updates: register a session into a persistent world model."""
    from .scene.world_model import WorldModel

    wm = WorldModel(args.scene)
    if args.action == "status":
        rep = wm.lack_report()
        print(json.dumps(rep, indent=2))
        return 0
    rep = wm.update(args.session, refuse_mesh=not args.no_mesh)
    reg = rep["registration"]
    print(f"✔ world update  {os.path.basename(rep['session'])}  "
          f"reg={reg['method']} fitness={reg['icp_fitness']} "
          f"rmse={reg['icp_rmse_m']*100:.1f}cm")
    print(f"  voxels {rep['voxels_total'] - rep['voxels_added']:,} "
          f"→ {rep['voxels_total']:,} (+{rep['voxels_added']:,})   "
          f"corroborated {rep['lack']['corroborated_frac']*100:.1f}%")
    if rep.get("world_mesh"):
        print(f"  mesh   {rep['world_mesh']}")
    print(f"  lack   {os.path.join(args.scene, 'lack_map.png')}")
    return 0


def _add_out(parser):
    parser.add_argument("--out", default="twin_out", help="output directory")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="assetpipe", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="run the zero-dependency box-test slice")
    _add_out(d); d.set_defaults(fn=cmd_demo)

    se = sub.add_parser(
        "sim-export",
        help="mesh/GLB -> sim-ready USD + URDF + MJCF (CoACD colliders, "
             "true mass/inertia; SimReady-style prop)",
    )
    se.add_argument("mesh", help="visual mesh (.glb/.obj/.ply/.stl)")
    se.add_argument("--out", default="sim_out", help="output directory")
    se.add_argument("--units", default="auto",
                    choices=["auto", "meters", "inches", "mm"])
    se.add_argument("--up", default="y", choices=["y", "z"],
                    help="up axis of the source mesh: 'y' for GLB/OBJ from a "
                         "scan or generator, 'z' for anything already levelled "
                         "to a floor (tools/splat_to_cad.py output)")
    se.add_argument("--material", default="generic",
                    choices=["generic", "cardboard", "wood", "plastic",
                             "metal", "upholstery"])
    se.add_argument("--density", type=float, default=None, help="kg/m^3 override")
    se.add_argument("--mass", type=float, default=None,
                    help="measured mass in kg (beats any density prior)")
    se.add_argument("--target-size", type=float, default=None,
                    help="uniform-rescale largest axis to this many meters "
                         "(for normalized generative GLBs; use measured dims)")
    se.add_argument("--label", default=None, help="semantic label")
    se.add_argument("--qcode", default=None, help="Wikidata QCode for SimReady semantics")
    se.add_argument("--max-hulls", type=int, default=24)
    se.set_defaults(fn=cmd_sim_export)

    wd = sub.add_parser(
        "world",
        help="persistent per-scene world model: each session registers in, "
             "coverage accumulates, lack map shows what to re-scan",
    )
    wd.add_argument("action", choices=["update", "status"])
    wd.add_argument("--scene", required=True, help="scene store directory")
    wd.add_argument("--session", help="[update] RGB-D session dir")
    wd.add_argument("--no-mesh", action="store_true",
                    help="[update] skip cumulative TSDF re-fuse")
    wd.set_defaults(fn=cmd_world)

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
    rg.add_argument("--voxel-size", type=float, default=None,
                    help="TSDF voxel size in meters (default: auto — 4mm for "
                         "close-up object scans, 1cm for room sweeps)")
    rg.add_argument("--inspect-only", action="store_true",
                    help="only print whether the input is fuse-ready (exit 2 if not)")
    rg.add_argument("--no-clean", action="store_true", help="skip background cleanup")
    rg.add_argument("--no-mesh", action="store_true", help="skip Poisson/GLB meshing of cloud")
    rg.add_argument("--no-viewer", action="store_true", help="skip scan_view.html")
    rg.add_argument("--padding", type=float, default=2.0,
                    help="per-side crate padding inches for dims.json (default 2)")
    rg.set_defaults(fn=cmd_rgbd)

    ms = sub.add_parser(
        "measure",
        help="L×W×H inches from mesh.obj/package/PLY, quantized to 0.25 in",
    )
    ms.add_argument("input", help="package dir, session/, or mesh.obj/.ply")
    ms.add_argument("--out", default="measure_out",
                    help="write dims.json + measurement.txt here")
    ms.add_argument("--padding", type=float, default=2.0,
                    help="per-side crate padding inches (default 2)")
    ms.add_argument("--no-fuse-check", action="store_true",
                    help="skip measuring fused scene_*.ply beside --out")
    ms.set_defaults(fn=cmd_measure)

    cr = sub.add_parser(
        "crop",
        help="isolate object from fused scan dir → scene_object.ply/.splat + object_view.html",
    )
    cr.add_argument("input", help="fused output directory (has scene_clean.ply or scene.ply)")
    cr.add_argument("--focus", type=float, default=0.85,
                    help="crop radius vs object scale (raise if clipped)")
    cr.add_argument("--padding", type=float, default=2.0,
                    help="crate padding inches for dims")
    cr.add_argument("--no-mesh", action="store_true",
                    help="skip cropping scene_tsdf_mesh.ply")
    cr.set_defaults(fn=cmd_crop)

    cu = sub.add_parser(
        "curate",
        help="RGB-D preprocess: score all frames → reject blur/dupes → curated session",
    )
    cu.add_argument("input", help="raw session/ dir with manifest.json")
    cu.add_argument("--out", required=True, help="curated session output directory")
    cu.add_argument("--target", type=int, default=48,
                    help="max keyframes to keep (default 48)")
    cu.add_argument("--min-angle", type=float, default=8.0,
                    help="min viewpoint angle between keyframes (deg)")
    cu.add_argument("--min-step", type=float, default=0.012,
                    help="min camera translation between keyframes (m)")
    cu.add_argument("--sharp-pct", type=float, default=25.0,
                    help="reject below this sharpness percentile")
    cu.add_argument("--min-edge", type=float, default=0.15,
                    help="min RGB↔depth edge agreement")
    cu.add_argument("--depth-lo", type=float, default=0.20,
                    help="reject frames with center depth below this many meters")
    cu.add_argument("--depth-hi", type=float, default=2.20,
                    help="reject frames with center depth above this many meters")
    cu.add_argument("--max-pose-step", type=float, default=0.0,
                    help="reject frame-to-frame pose jumps above this many meters (0 disables)")
    cu.add_argument("--then", choices=["none", "object"], default="none",
                    help="run object asset on curated session afterwards")
    cu.add_argument("--object-out", default=None,
                    help="object out dir when --then object")
    cu.add_argument("--object-frames", type=int, default=1,
                    help="max frames for --then object (default 1 — best curated view)")
    cu.set_defaults(fn=cmd_curate)

    ob = sub.add_parser(
        "object",
        help="RGB-D session → score frames → best 1–N → object.ply asset (no TSDF/COLMAP)",
    )
    ob.add_argument("input", help="session/ dir with manifest.json")
    ob.add_argument("--out", default="object_out", help="asset output directory")
    ob.add_argument("--max-frames", type=int, default=10,
                    help="max views to merge (default 10; cover available orbit)")
    ob.add_argument("--stride", type=int, default=3,
                    help="score every Nth frame (faster analysis)")
    ob.add_argument("--frame", type=int, default=None,
                    help="force this frame index (skip auto pick)")
    ob.add_argument("--curate", action="store_true",
                    help="run critical preprocess first, then build asset from curated session")
    ob.add_argument("--curate-out", default=None,
                    help="where to write curated session when --curate")
    ob.add_argument("--curate-target", type=int, default=48,
                    help="max keyframes when --curate")
    ob.add_argument("--min-angle", type=float, default=8.0)
    ob.add_argument("--min-step", type=float, default=0.012)
    ob.set_defaults(fn=cmd_object)

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

    rt = sub.add_parser(
        "route",
        help="classify capture: trellis (single product) vs 3dgrut (multi/scene)",
    )
    rt.add_argument(
        "input",
        help="curated session, object asset folder, or any dir with dims.json / "
             "curation_report.json",
    )
    rt.add_argument(
        "--out",
        help="write route_decision.json (dir or file path)",
    )
    rt.add_argument(
        "--force",
        choices=["trellis", "3dgrut"],
        help="override heuristic",
    )
    rt.add_argument(
        "--json",
        action="store_true",
        help="also print full decision JSON",
    )
    rt.set_defaults(fn=cmd_route)

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

    pipe = sub.add_parser(
        "pipeline",
        help="offline multi-backend refine: preprocess → open3d/colmap/trellis/3dgrut → vNNN",
    )
    pipe_sub = pipe.add_subparsers(dest="pipeline_cmd", required=True)

    pp = pipe_sub.add_parser("preprocess", help="capture → curated dataset pack")
    pp.add_argument("input", help="CrateScan zip/session, image folder, or video")
    pp.add_argument("--out", required=True, help="run output directory")
    pp.add_argument("--frames", type=int, default=48, help="max curated frames")
    pp.add_argument("--fps", type=float, default=2.5, help="video extract fps")
    pp.set_defaults(fn=cmd_pipeline_preprocess)

    pr = pipe_sub.add_parser(
        "run",
        help="preprocess + backends + versioned refine (hole-fill each version)",
    )
    pr.add_argument("input", help="CrateScan zip/session, image folder, or video")
    pr.add_argument("--out", required=True, help="run output directory")
    pr.add_argument(
        "--backends",
        default="open3d,colmap,trellis,3dgrut",
        help="comma list: open3d,colmap,trellis,3dgrut",
    )
    pr.add_argument("--versions", type=int, default=2, help="refine versions (default 2)")
    pr.add_argument("--frames", type=int, default=48, help="max curated frames")
    pr.add_argument("--fps", type=float, default=2.5, help="video extract fps")
    pr.add_argument("--focus", type=float, default=0.85, help="object crop focus")
    pr.add_argument("--trellis-endpoint", default="http://localhost:8080/generate")
    pr.add_argument("--no-generative", action="store_true",
                    help="skip TRELLIS hole-fill (geometric only)")
    pr.add_argument("--no-3dgrut", action="store_true",
                    help="skip 3DGRUT backend (faster smoke)")
    pr.set_defaults(fn=cmd_pipeline_run)

    pf = pipe_sub.add_parser("refine", help="extra refine versions on an existing run")
    pf.add_argument("input", help="existing run directory (has dataset/ + backends/)")
    pf.add_argument("--versions", type=int, default=1)
    pf.add_argument("--focus", type=float, default=0.85)
    pf.add_argument("--trellis-endpoint", default="http://localhost:8080/generate")
    pf.add_argument("--no-generative", action="store_true")
    pf.set_defaults(fn=cmd_pipeline_refine)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
