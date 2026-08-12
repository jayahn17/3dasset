#!/usr/bin/env python3
"""Open-source KIRI Engine — the same pipeline, run locally, RGB-only.

KIRI is the object-scan benchmark we score against, but it is a cloud black box:
photos in, a scale-free mesh/splat out, $1 a scan, results deleted after 3 days.
This reproduces its *documented* pipeline from open source so the benchmark can
be run offline, inspected at every stage, and — unlike KIRI — closed to metric.

Stage for stage, against what KIRI ships:

    KIRI                             here
    ------------------------------   ------------------------------------------
    3D Scan Prep (blur/weak reject)  Laplacian-variance + exposure curation
    AI object masking (`isMask`)     BiRefNet via rembg  <- KIRI's own model
    Photo Scan (photogrammetry)      AliceVision SfM + MVS + texturing (Meshroom)
    3DGS Scan                        AliceVision poses -> 3DGUT (gsplat kernels)
    3DGS to Mesh                     TSDF over the splat's rendered depth
    (nothing - KIRI is scale-free)   Umeyama fit to the capture's own AR track

The masking model is not a guess: KIRI's own AGPL "3D Scan Preparation Tool"
ships BiRefNet, MaskFormer and YOLO in `_AI_Models/`, and their API exposes
`isMask` separately from the solve — so masks steer the dense/meshing stage, not
SfM. We do the same: SfM sees full frames (background texture is what makes a
shiny leather couch solvable at all), masks enter at prepareDenseScene.

The last row is the point of the exercise. KIRI never sees depth or poses, so
its output is scale-ambiguous; we fit the RGB-only reconstruction back onto the
capture's own AR camera track and read off the scale factor it was missing.

Usage:
    python tools/kiri_oss.py captures/work/CrateScan-XXXX/*/session \\
        --out kiri_oss_out/crate --truth demo_out/CrateScan-XXXX/*/dims.json
    python tools/kiri_oss.py photos/ --out kiri_oss_out/x --fov 64.4
    python tools/kiri_oss.py <session> --out <dir> --stages gs --iterations 30000

Stages: ingest, curate, mask, sfm, mvs, gs, scale, isolate, validate, report. The
default list is everything except `gs`, which wants the GPU for ~40 min at 30k
iterations. Each stage skips when its output already exists; --force re-runs the
ones named.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

AV_ROOT = Path(os.environ.get("ALICEVISION_ROOT", Path.home() / "alicevision-3.3.0"))
ALL_STAGES = ["ingest", "curate", "mask", "sfm", "mvs", "gs", "scale", "isolate",
              "validate", "report"]
DEFAULT_STAGES = ["ingest", "curate", "mask", "sfm", "mvs", "scale", "isolate",
                  "validate", "report"]

# KIRI's published input contract, mirrored so a run that passes here would also
# have been accepted by their API.
MIN_PHOTOS, MAX_PHOTOS = 20, 300


def log(msg: str) -> None:
    print(f"[kiri-oss] {msg}", flush=True)


def _av(exe: str, args: list[str], check: bool = True, log_to: Path | None = None):
    """Run an AliceVision binary. Both env vars are required or it aborts on the
    embedded OCIO config."""
    binary = AV_ROOT / "bin" / exe
    if not binary.is_file():
        sys.exit(f"!! {binary} not found - set ALICEVISION_ROOT")
    env = dict(os.environ)
    env["ALICEVISION_ROOT"] = str(AV_ROOT)
    env["LD_LIBRARY_PATH"] = f"{AV_ROOT / 'lib'}:{env.get('LD_LIBRARY_PATH', '')}"
    t0 = time.time()
    proc = subprocess.run([str(binary), *args], env=env, capture_output=True, text=True)
    dt = time.time() - t0
    if log_to:
        log_to.write_text(proc.stdout + proc.stderr)
    if check and proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
        sys.exit(f"!! {exe} failed after {dt:.0f}s:\n{tail}")
    log(f"    {exe} ({dt:.0f}s)")
    return proc


# --------------------------------------------------------------------------
# stage 1: ingest


def _load_session(session: Path) -> tuple[list[dict], dict]:
    manifest = json.loads((session / "manifest.json").read_text())
    frames = manifest.get("keyframes") or manifest.get("frames") or []
    if not frames:
        sys.exit(f"!! no keyframes/frames in {session}/manifest.json")
    return frames, manifest


def _floor_from_depth(session: Path, frames: list[dict]) -> float | None:
    """Floor height in the AR world frame, straight from the LiDAR depth.

    Masking deletes the ground from the images, so the RGB-only mesh has no
    floor to measure an object's height against - it can only measure from the
    lowest surface it reconstructed, which on a sofa is the underside of the
    base. Depth gives the real datum. This is the one input KIRI never gets.
    """
    import cv2

    ark_to_cv = np.diag([1.0, -1.0, -1.0])
    ys = []
    for fr in frames[::2]:
        dpath = session / fr.get("depth", "")
        pose = fr.get("pose")
        K = fr.get("K_color")
        if not (dpath.is_file() and pose and K):
            continue
        d = cv2.imread(str(dpath), cv2.IMREAD_UNCHANGED)
        if d is None:
            continue
        d = d.astype(np.float32) / 1000.0
        dh, dw = d.shape
        cw, ch = fr["color_size"]
        fx, fy, cx, cy = K[0] * dw / cw, K[1] * dh / ch, K[2] * dw / cw, K[3] * dh / ch
        u, v = np.meshgrid(np.arange(dw), np.arange(dh))
        ok = (d > 0.3) & (d < 5.0)
        if not ok.any():
            continue
        z = d[ok]
        pts = np.stack([(u[ok] - cx) / fx * z, (v[ok] - cy) / fy * z, z], 1) @ ark_to_cv.T
        T = np.asarray(pose, float).reshape(4, 4)
        ys.append((pts @ T[:3, :3].T + T[:3, 3])[:, 1])
    if not ys:
        return None
    y = np.concatenate(ys)
    # The floor is the densest horizontal slab in the lowest part of the scene,
    # not simply the minimum - stray depth outliers sit below it.
    lo, hi = np.percentile(y, [0.5, 60])
    hist, edges = np.histogram(y[(y >= lo) & (y <= hi)], bins=120)
    return float((edges[hist.argmax()] + edges[hist.argmax() + 1]) / 2)


def stage_ingest(src: Path, out: Path, max_edge: int, fov: float | None) -> dict:
    """CrateScanner session (or a plain photo folder) -> photos/ + track.json.

    Photos are downscaled to `max_edge`: KIRI caps uploads at 1920x1080 for
    video, and 12 MP frames make the MVS stage cost hours for no extra detail on
    a 31-frame orbit.
    """
    photos = out / "photos"
    photos.mkdir(parents=True, exist_ok=True)
    import cv2

    track: dict[str, list[float]] = {}
    floor_y = None
    if (src / "manifest.json").is_file():
        frames, manifest = _load_session(src)
        log(f"session: {len(frames)} keyframes, source={manifest.get('source')}")
        floor_y = _floor_from_depth(src, frames)
        for fr in frames:
            rel = fr.get("color") or fr.get("image")
            img_path = src / rel
            if not img_path.is_file():
                continue
            name = f"{Path(rel).stem}.jpg"
            im = cv2.imread(str(img_path))
            h, w = im.shape[:2]
            s = min(1.0, max_edge / max(h, w))
            if s < 1.0:
                im = cv2.resize(im, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(photos / name), im, [cv2.IMWRITE_JPEG_QUALITY, 95])
            pose = fr.get("pose")
            if pose:
                m = np.asarray(pose, float).reshape(4, 4)
                # Camera centre only: translation is convention-free, so the
                # ARKit/OpenCV basis question never enters the scale bridge.
                track[name] = m[:3, 3].tolist()
        if fov is None:
            K = frames[0].get("K_color")
            cw = frames[0].get("color_size", [None])[0]
            if K and cw:
                fov = 2 * math.degrees(math.atan(cw / 2 / float(K[0])))
    else:
        exts = (".jpg", ".jpeg", ".png", ".heic")
        srcs = sorted(p for p in src.iterdir() if p.suffix.lower() in exts)
        if not srcs:
            sys.exit(f"!! no images in {src}")
        log(f"photo folder: {len(srcs)} images")
        for p in srcs:
            im = cv2.imread(str(p))
            h, w = im.shape[:2]
            s = min(1.0, max_edge / max(h, w))
            if s < 1.0:
                im = cv2.resize(im, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(photos / f"{p.stem}.jpg"), im, [cv2.IMWRITE_JPEG_QUALITY, 95])

    meta = {"n_photos": len(list(photos.glob("*.jpg"))), "max_edge": max_edge,
            "fov_deg": fov, "floor_y_m": floor_y, "track": track}
    (out / "ingest.json").write_text(json.dumps(meta, indent=2))
    log(f"ingest: {meta['n_photos']} photos @ <={max_edge}px, fov={fov}, "
        f"{len(track)} AR poses held back for the metric bridge")
    if floor_y is not None:
        log(f"ingest: LiDAR floor plane at y={floor_y:.3f} m (height datum)")
    return meta


# --------------------------------------------------------------------------
# stage 2: curate  (KIRI's "3D Scan Preparation Tool")


def stage_curate(out: Path, keep: float) -> dict:
    """Drop blurry/weak frames the way KIRI's prep tool does.

    Variance of the Laplacian for sharpness, mean luma for exposure. With only a
    few dozen frames the risk is cutting into orbit coverage, so this never
    drops below KIRI's own 20-photo floor.
    """
    import cv2

    photos = sorted((out / "photos").glob("*.jpg"))
    rows = []
    for p in photos:
        g = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2GRAY)
        rows.append({"name": p.name, "lapvar": float(cv2.Laplacian(g, cv2.CV_64F).var()),
                     "luma": float(g.mean())})
    lv = np.array([r["lapvar"] for r in rows])
    lum = np.array([r["luma"] for r in rows])
    n_keep = max(MIN_PHOTOS, int(round(len(rows) * keep)))
    thresh = float(np.sort(lv)[max(0, len(lv) - n_keep)]) if n_keep < len(lv) else -1.0
    reject_dir = _mkdir(out / "rejected")
    rejected = []
    for r in rows:
        bad_blur = r["lapvar"] < thresh
        bad_exp = r["luma"] < 15 or r["luma"] > 240
        r["kept"] = not (bad_blur or bad_exp)
        if not r["kept"]:
            rejected.append(r["name"])
            (out / "photos" / r["name"]).rename(reject_dir / r["name"])
    n = len(rows) - len(rejected)
    if n < MIN_PHOTOS:
        log(f"!! only {n} photos survive curation (KIRI's floor is {MIN_PHOTOS})")
    if n > MAX_PHOTOS:
        log(f"note: {n} photos exceeds KIRI's {MAX_PHOTOS} cap - trimming for parity")
        for p in sorted((out / "photos").glob("*.jpg"))[MAX_PHOTOS:]:
            p.rename(reject_dir / p.name)
    meta = {"n_in": len(rows), "n_kept": n, "blur_threshold": thresh,
            "lapvar_range": [float(lv.min()), float(lv.max())],
            "luma_range": [float(lum.min()), float(lum.max())],
            "rejected": rejected, "frames": rows}
    (out / "curate.json").write_text(json.dumps(meta, indent=2))
    log(f"curate: kept {n}/{len(rows)} (blur thresh {thresh:.1f}, "
        f"lapvar {lv.min():.0f}-{lv.max():.0f})")
    return meta


def _mkdir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------
# stage 3: mask  (KIRI's BiRefNet)


def stage_mask(out: Path, model: str) -> dict:
    """Salient-object masks with BiRefNet - the model KIRI's own prep tool ships.

    Written as 8-bit PNGs named after each photo, which is the filename contract
    AliceVision's --masksFolders expects.
    """
    import cv2
    from rembg import new_session, remove

    masks = _mkdir(out / "masks")
    cutouts = _mkdir(out / "cutouts")
    sess = new_session(model)
    photos = sorted((out / "photos").glob("*.jpg"))
    covers = []
    for p in photos:
        img = cv2.imread(str(p))
        rgba = remove(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), session=sess)
        alpha = np.asarray(rgba)[:, :, 3]
        m = (alpha > 127).astype(np.uint8) * 255
        cv2.imwrite(str(masks / f"{p.stem}.png"), m)
        cv2.imwrite(str(cutouts / f"{p.stem}.png"),
                    np.dstack([img, alpha]))
        covers.append(float((m > 0).mean()))
    meta = {"model": model, "n": len(photos),
            "coverage_mean": float(np.mean(covers)) if covers else 0.0,
            "coverage_min": float(np.min(covers)) if covers else 0.0,
            "coverage_max": float(np.max(covers)) if covers else 0.0}
    (out / "mask.json").write_text(json.dumps(meta, indent=2))
    log(f"mask: {model} on {len(photos)} frames, subject covers "
        f"{meta['coverage_mean']*100:.1f}% of frame "
        f"({meta['coverage_min']*100:.1f}-{meta['coverage_max']*100:.1f}%)")
    return meta


# --------------------------------------------------------------------------
# stage 4: sfm  (KIRI "Photo Scan", solve half)


def stage_sfm(out: Path, fov: float | None, preset: str) -> Path:
    """AliceVision incremental SfM on the *unmasked* frames.

    Masking the solve is the classic own-goal on a low-texture subject: the
    couch's leather carries almost no features, the rug and room carry them all.
    KIRI's API exposes isMask separately from the solve for the same reason.
    """
    sfm_dir = _mkdir(out / "sfm")
    logs = _mkdir(out / "logs")
    cam = sfm_dir / "cameraInit.sfm"

    init = ["--imageFolder", str(out / "photos"), "--output", str(cam),
            "--allowSingleView", "1"]
    if fov:
        init += ["--defaultFieldOfView", str(fov)]
    _av("aliceVision_cameraInit", init, log_to=logs / "cameraInit.log")

    feats = _mkdir(sfm_dir / "features")
    feat = ["--input", str(cam), "--output", str(feats), "-d", "sift",
            "--describerPreset", preset]
    proc = _av("aliceVision_featureExtraction", feat, check=False,
               log_to=logs / "featureExtraction.log")
    if proc.returncode != 0:
        # popsift preallocates its DoG pyramid and aborts if the GPU is busy.
        log("    GPU extraction failed - retrying on CPU")
        _av("aliceVision_featureExtraction", feat + ["--forceCpuExtraction", "1"],
            log_to=logs / "featureExtraction_cpu.log")

    matches = _mkdir(sfm_dir / "matches")
    _av("aliceVision_featureMatching",
        ["--input", str(cam), "--featuresFolders", str(feats),
         "--output", str(matches), "-d", "sift"],
        log_to=logs / "featureMatching.log")

    sfm = sfm_dir / "sfm.sfm"
    _av("aliceVision_incrementalSfM",
        ["--input", str(cam), "--featuresFolders", str(feats),
         "--matchesFolders", str(matches), "-d", "sift",
         "--output", str(sfm), "--extraInfoFolder", str(sfm_dir)],
        log_to=logs / "incrementalSfM.log")

    scene = json.loads(sfm.read_text())
    n_views, n_poses = len(scene.get("views", [])), len(scene.get("poses", []))
    n_pts = len(scene.get("structure", []))
    log(f"sfm: {n_poses}/{n_views} views registered, {n_pts} landmarks")
    if n_poses < 3:
        sys.exit("!! SfM registered too few views - the RGB-only path cannot continue")
    return sfm


# --------------------------------------------------------------------------
# stage 5: mvs  (KIRI "Photo Scan", dense half)


def stage_mvs(out: Path, downscale: int, use_masks: bool, texture_side: int) -> Path:
    """prepareDenseScene -> depth maps -> meshing -> texturing.

    Masks enter here (KIRI's `isMask`): the dense stage only carves geometry
    where the subject is, so the room does not end up welded to the couch.
    """
    sfm = out / "sfm" / "sfm.sfm"
    mvs = _mkdir(out / "mvs")
    logs = _mkdir(out / "logs")

    undist = _mkdir(mvs / "undistorted")
    prep = ["--input", str(sfm), "--output", str(undist)]
    if use_masks and (out / "masks").is_dir():
        prep += ["--masksFolders", str(out / "masks"), "--maskExtension", "png"]
    _av("aliceVision_prepareDenseScene", prep, log_to=logs / "prepareDenseScene.log")

    depths = _mkdir(mvs / "depthMap")
    _av("aliceVision_depthMapEstimation",
        ["--input", str(sfm), "--imagesFolder", str(undist), "--output", str(depths),
         "--downscale", str(downscale)],
        log_to=logs / "depthMapEstimation.log")

    filt = _mkdir(mvs / "depthMapFiltered")
    _av("aliceVision_depthMapFiltering",
        ["--input", str(sfm), "--depthMapsFolder", str(depths), "--output", str(filt)],
        log_to=logs / "depthMapFiltering.log")

    dense = mvs / "densePointCloud.abc"
    raw_mesh = mvs / "mesh.obj"
    _av("aliceVision_meshing",
        ["--input", str(sfm), "--depthMapsFolder", str(filt),
         "--output", str(dense), "--outputMesh", str(raw_mesh),
         "--estimateSpaceFromSfM", "1", "--colorizeOutput", "1"],
        log_to=logs / "meshing.log")

    filtered_mesh = mvs / "mesh_filtered.obj"
    _av("aliceVision_meshFiltering",
        ["--inputMesh", str(raw_mesh), "--outputMesh", str(filtered_mesh)],
        log_to=logs / "meshFiltering.log")

    tex = _mkdir(mvs / "texture")
    _av("aliceVision_texturing",
        ["--input", str(dense), "--inputMesh", str(filtered_mesh),
         "--imagesFolder", str(undist), "--output", str(tex),
         "--textureSide", str(texture_side), "--fillHoles", "1",
         # Defaults to "none", which silently yields UVs but no baked map.
         "--colorMappingFileType", "png"],
        log_to=logs / "texturing.log")

    textured = tex / "texturedMesh.obj"
    final = out / "kiri_oss_photo.obj"
    if textured.is_file():
        for f in tex.iterdir():
            shutil.copy2(f, out / f.name.replace("texturedMesh", "kiri_oss_photo"))
    else:
        shutil.copy2(filtered_mesh, final)
    log(f"mvs: textured mesh -> {final}")
    return final


# --------------------------------------------------------------------------
# stage 6: gs  (KIRI "3DGS Scan")


def stage_gs(out: Path, iterations: int, max_gaussians: int) -> Path:
    """AliceVision poses -> COLMAP layout -> 3DGUT.

    This is the RGB-only splat: same frames, but pose comes from SfM rather than
    the AR track, so it is directly comparable to KIRI's 3DGS Scan product.
    """
    from assetpipe.scene.backends import _find_3dgrut_python, _find_3dgrut_root

    sfm = out / "sfm" / "sfm.sfm"
    col = out / "colmap"
    # exportColmap refuses to write into an existing sparse/0, so a --force
    # re-run dies unless the previous export is cleared first.
    if col.exists():
        shutil.rmtree(col)
    col = _mkdir(col)
    _av("aliceVision_exportColmap",
        ["--input", str(sfm), "--output", str(col)],
        log_to=_mkdir(out / "logs") / "exportColmap.log")

    # 3dgrut's colmap dataloader wants <root>/images + <root>/sparse/0
    root = _mkdir(out / "gs_data")
    found = list(col.rglob("cameras.*"))
    if not found:
        sys.exit(f"!! exportColmap produced no cameras.txt/.bin under {col}")
    sparse_src = found[0].parent
    sparse_dst = _mkdir(root / "sparse" / "0")
    for f in sparse_src.iterdir():
        if f.is_file():
            shutil.copy2(f, sparse_dst / f.name)
    imgs = root / "images"
    if not imgs.exists():
        imgs.symlink_to((out / "photos").resolve(), target_is_directory=True)

    py, grut = _find_3dgrut_python(), None
    if py:
        grut = _find_3dgrut_root(py)
    if not py or not grut:
        sys.exit("!! 3dgrut env/repo not found (need conda env 3dgrut + ~/3dgrut)")
    runs = _mkdir(out / "gs_runs")
    cmd = [py, "train.py", "--config-name=apps/colmap_3dgut_mcmc.yaml",
           f"path='{root.resolve()}'", f"out_dir='{runs.resolve()}'",
           "experiment_name=kiri_oss_3dgs", f"n_iterations={iterations}",
           "num_workers=4", "val_frequency=999999",
           f"strategy.add.max_n_gaussians={max_gaussians}",
           "model.default_scale_factor=0.1", "dataset.downsample_factor=1",
           "export_ply.enabled=true", "test_last=false"]
    env = os.environ.copy()
    for k in ("LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME"):
        env.pop(k, None)
    env["PATH"] = os.path.dirname(py) + os.pathsep + env.get("PATH", "")
    log(f"gs: training 3DGUT for {iterations} iters")
    subprocess.run(cmd, cwd=grut, check=True, env=env)
    plys = sorted(runs.rglob("export_last.ply")) or sorted(runs.rglob("*.ply"))
    if not plys:
        sys.exit(f"!! no PLY exported under {runs}")
    dst = out / "kiri_oss_3dgs.ply"
    shutil.copy2(plys[-1], dst)
    log(f"gs: splat -> {dst}")
    return dst


# --------------------------------------------------------------------------
# stage 7: scale  (what KIRI structurally cannot do)


def _mesh_to_sfm_frame(verts: np.ndarray, sfm: Path) -> tuple[np.ndarray, dict]:
    """Find the axis convention AliceVision wrote the mesh in.

    meshing/texturing emit the OBJ rotated 180 deg about X relative to their own
    SfM world frame, so a mesh dropped straight into the pose frame lands
    mirrored under the floor - and still *looks* like a plausible couch, which
    is what makes it dangerous. Rather than hard-code the flip and have it
    silently invert on an AliceVision upgrade, score both conventions against
    the sparse landmarks (which are unambiguously in the pose frame) and take
    the one the geometry agrees with.
    """
    from scipy.spatial import cKDTree

    scene = json.loads(sfm.read_text())
    L = np.array([[float(x) for x in l["X"]] for l in scene.get("structure", [])])
    cands = {"identity": np.diag([1.0, 1.0, 1.0]),
             "Rx180": np.diag([1.0, -1.0, -1.0])}
    if len(L) < 100:
        return cands["identity"], {"convention": "identity", "reason": "too few landmarks"}
    lo, hi = np.percentile(L, [2, 98], axis=0)
    tree = cKDTree(L[((L > lo) & (L < hi)).all(1)])
    sub = verts if len(verts) <= 20000 else verts[
        np.random.default_rng(0).choice(len(verts), 20000, replace=False)]
    scores = {}
    for name, M in cands.items():
        d, _ = tree.query(sub @ M.T)
        scores[name] = float(np.median(d))
    best = min(scores, key=scores.get)
    log(f"scale: mesh frame = {best} (median vertex->landmark "
        + ", ".join(f"{k} {v*1000:.0f} mm" for k, v in scores.items()) + ")")
    return cands[best], {"convention": best,
                         "median_vertex_to_landmark_mm": {k: round(v * 1000, 1)
                                                          for k, v in scores.items()}}


def _sfm_centers(sfm: Path) -> dict[str, np.ndarray]:
    scene = json.loads(sfm.read_text())
    poses = {p["poseId"]: p["pose"]["transform"] for p in scene.get("poses", [])}
    centers = {}
    for v in scene.get("views", []):
        pid = v.get("poseId")
        if pid in poses:
            name = Path(v["path"]).name
            centers[name] = np.array([float(x) for x in poses[pid]["center"]])
    return centers


def _umeyama(src: np.ndarray, dst: np.ndarray):
    """Similarity (scale, R, t) mapping src->dst, plus the RMS fit residual.

    The full transform matters, not just the scale: it lands the reconstruction
    in the AR world frame, which is gravity-aligned (+y up). That turns floor
    removal and an upright L/W/H into geometry rather than guesswork.
    """
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s0, d0 = src - mu_s, dst - mu_d
    cov = d0.T @ s0 / len(src)
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1
    R = U @ S @ Vt
    var_s = (s0 ** 2).sum() / len(src)
    scale = float((D * np.diag(S)).sum() / var_s)
    t = mu_d - scale * R @ mu_s
    resid = np.linalg.norm(dst - (scale * (R @ src.T).T + t), axis=1)
    return scale, R, t, resid


def stage_scale(out: Path, splat_opacity: float = 0.3) -> dict:
    """Fit the SfM camera track onto the capture's AR track and read off scale.

    KIRI returns an arbitrary-scale asset because it only ever sees RGB. The
    same frames here carry an AR pose track that ingest deliberately withheld
    from the solve, so it is an honest external ruler, not a leak.
    """
    ingest = json.loads((out / "ingest.json").read_text())
    track = {k: np.array(v) for k, v in ingest.get("track", {}).items()}
    sfm = out / "sfm" / "sfm.sfm"
    if not track:
        log("scale: no AR track in this capture - output stays scale-free (as KIRI's is)")
        return {"metric": False, "reason": "no AR poses in source"}
    centers = _sfm_centers(sfm)
    common = sorted(set(centers) & set(track))
    if len(common) < 4:
        log(f"scale: only {len(common)} frames in both tracks - too few to fit")
        return {"metric": False, "reason": f"only {len(common)} paired cameras"}

    src = np.stack([centers[k] for k in common])
    dst = np.stack([track[k] for k in common])
    scale, R, t, resid = _umeyama(src, dst)
    rms = float(np.sqrt((resid ** 2).mean()))
    # Three different "sizes of the capture", and they differ by 3x. The
    # bounding-box diagonal is the flattering one, so name it for what it is and
    # publish the honest denominators beside it: a single RMS also hides that one
    # camera in this bridge is 70 mm out.
    order = [k for k in common]
    path = float(np.linalg.norm(np.diff(np.stack([track[k] for k in order]), axis=0),
                                axis=1).sum())
    from scipy.spatial.distance import pdist
    max_sep = float(pdist(dst).max()) if len(dst) > 1 else 0.0
    bbox_diag = float(np.linalg.norm(dst.max(0) - dst.min(0)))
    # A scale factor with no uncertainty attached is how "+/- 0.25 in" gets
    # claimed. Three diagnostics, because the headline RMS hides all of them:
    #   lag-1 autocorrelation  - is the residual noise, or monotonic drift? On
    #                            this capture it is +0.83, i.e. drift, and a
    #                            single global similarity is then the wrong model.
    #   jackknife SE           - 1-sigma on the scale factor itself.
    #   sliding-window spread  - how far the local scale moves along the walk.
    # Expressed in mm on a 2 m object, which is the number a spec is written in.
    REF_MM = 2000.0
    resid_c = resid - resid.mean()
    lag1 = (float(np.corrcoef(resid_c[:-1], resid_c[1:])[0, 1])
            if len(resid) > 3 else float("nan"))
    jk = np.array([_umeyama(np.delete(src, i, 0), np.delete(dst, i, 0))[0]
                   for i in range(len(src))])
    jk_se = float(np.sqrt((len(jk) - 1) / len(jk) * ((jk - jk.mean()) ** 2).sum()))
    win = min(10, max(4, len(src) // 3))
    local = [_umeyama(src[i:i + win], dst[i:i + win])[0]
             for i in range(max(1, len(src) - win))]
    drift_pct = (max(local) - min(local)) / scale * 100 if local else 0.0

    meta = {"metric": True, "n_paired_cameras": len(common),
            "sfm_to_metres": scale, "fit_rms_m": rms,
            "scale_uncertainty": {
                "jackknife_se_pct": round(jk_se / scale * 100, 3),
                "jackknife_se_mm_per_2m": round(jk_se / scale * REF_MM, 1),
                "residual_lag1_autocorr": round(lag1, 3),
                "residual_is_drift_not_noise": bool(lag1 > 0.4),
                "local_scale_min": round(min(local), 4) if local else None,
                "local_scale_max": round(max(local), 4) if local else None,
                "local_scale_window": win,
                "drift_spread_pct": round(drift_pct, 2),
                "drift_spread_mm_per_2m": round(drift_pct / 100 * REF_MM, 1)},
            "fit_residual_mm": {"min": round(float(resid.min()) * 1000, 1),
                                "median": round(float(np.median(resid)) * 1000, 1),
                                "max": round(float(resid.max()) * 1000, 1)},
            "ar_track_bbox_diag_m": bbox_diag,
            "ar_track_path_len_m": path,
            "max_camera_separation_m": max_sep,
            "fit_rms_pct_of_path": round(rms / path * 100, 2) if path else None,
            "fit_rms_pct_of_max_baseline": round(rms / max_sep * 100, 2) if max_sep else None}
    log(f"scale: {len(common)} paired cameras, SfM unit = {scale:.4f} m, "
        f"fit RMS {rms*1000:.0f} mm (per-camera {meta['fit_residual_mm']['min']}-"
        f"{meta['fit_residual_mm']['max']} mm, median "
        f"{meta['fit_residual_mm']['median']} mm)")
    log(f"scale: capture size — {path:.2f} m camera path, {max_sep:.2f} m widest "
        f"baseline, {bbox_diag:.2f} m bbox diagonal")
    u = meta["scale_uncertainty"]
    log(f"scale: uncertainty {u['jackknife_se_mm_per_2m']} mm per 2 m (1-sigma, "
        f"jackknife); local scale {u['local_scale_min']}-{u['local_scale_max']} "
        f"= {u['drift_spread_mm_per_2m']} mm per 2 m of drift")
    if u["residual_is_drift_not_noise"]:
        log(f"scale: !! residual lag-1 autocorr {u['residual_lag1_autocorr']} — the "
            "fit residual is DRIFT, not noise, so one global similarity is the "
            "wrong model and this scale cannot support a tight tolerance")

    mesh_in = out / "kiri_oss_photo.obj"
    if mesh_in.is_file():
        import trimesh
        m = trimesh.load(mesh_in, process=False, force="mesh")
        M, conv = _mesh_to_sfm_frame(np.asarray(m.vertices), sfm)
        meta["mesh_frame"] = conv
        T = np.eye(4)
        T[:3, :3] = scale * R @ M
        T[:3, 3] = t
        m.apply_transform(T)
        dst_mesh = out / "kiri_oss_photo_metric.ply"
        m.export(dst_mesh)
        meta["metric_mesh"] = str(dst_mesh)
        meta["transform_sfm_to_ar_world"] = T.tolist()
        log(f"scale: metric mesh (AR world frame, +y up) -> {dst_mesh}")
    splat_in = out / "kiri_oss_3dgs.ply"
    if splat_in.is_file():
        from assetpipe.scene.splat import drop_far_field, load_ply

        # Measured dims are very sensitive to this on an under-trained splat:
        # at 7k iterations only 3% of gaussians reach 0.3 and L swings 143->70 in
        # across thresholds. A converged (30k) splat is far flatter here.
        xyz, rgb = load_ply(str(splat_in), min_opacity=splat_opacity)
        xyz, rgb = drop_far_field(xyz, rgb)
        meta["splat_opacity_threshold"] = splat_opacity
        M, conv = _mesh_to_sfm_frame(xyz, sfm)
        meta["splat_frame"] = conv
        world = scale * ((xyz @ M.T) @ R.T) + t
        _write_ply_xyzrgb(out / "kiri_oss_3dgs_metric.ply", world, rgb)
        meta["metric_splat"] = str(out / "kiri_oss_3dgs_metric.ply")
        log(f"scale: metric splat ({len(world)} solid gaussians) -> "
            f"{out / 'kiri_oss_3dgs_metric.ply'}")

    (out / "scale.json").write_text(json.dumps(meta, indent=2))
    return meta


def _write_ply_xyzrgb(path: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    import open3d as o3d

    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(xyz, float)))
    pc.colors = o3d.utility.Vector3dVector(np.asarray(rgb, float) / 255.0)
    o3d.io.write_point_cloud(str(path), pc)


# --------------------------------------------------------------------------
# stage 8: isolate


def _ply_vertex_count(path: Path) -> int:
    """`element vertex N` out of a PLY header, without loading the body."""
    if not path.is_file():
        return 0
    with open(path, "rb") as fh:
        for _ in range(60):
            line = fh.readline()
            if not line or line.strip() == b"end_header":
                break
            if line.startswith(b"element vertex"):
                return int(line.split()[-1])
    return 0


def _gravity_dims(pts: np.ndarray, base_y: float) -> dict:
    """L/W/H with up already known.

    A free 3D OBB is the wrong box for furniture: it tilts to hug the point
    soup and inflates every axis (it read this sofa as 100 in long). Gravity is
    known in the AR frame, so the footprint is a min-area rectangle in the
    horizontal plane and height is a straight vertical measurement.
    """
    import cv2

    (_, _), (rw, rh), ang = cv2.minAreaRect(pts[:, [0, 2]].astype(np.float32))
    foot = sorted([float(rw), float(rh)], reverse=True)
    top = float(pts[:, 1].max())
    height = top - base_y
    return {"footprint_angle_deg": round(float(ang), 1),
            "gravity_aligned_lwh_in": [round(foot[0] / 0.0254, 2),
                                       round(foot[1] / 0.0254, 2),
                                       round(height / 0.0254, 2)],
            "gravity_aligned_lwh_m": [round(foot[0], 3), round(foot[1], 3),
                                      round(height, 3)],
            "_top_y": top}


def stage_isolate(out: Path, floor_clear: float) -> dict:
    """Cut the floor, keep the largest connected blob: the object on its own.

    KIRI's masking already removed the room from the *images*, but MVS still
    welds a skirt of floor under the subject where mask edges meet the ground
    plane. In the AR world frame gravity is known, so the floor is a y-slab, not
    a RANSAC gamble.
    """
    import open3d as o3d

    src = out / "kiri_oss_photo_metric.ply"
    if not src.is_file():
        log("isolate: no metric mesh yet - run the scale stage first")
        return {}

    # Open3D's clustering, not trimesh.split(): a 436k-face mesh with thousands
    # of shells makes split() materialise every component and OOM. The dedup is
    # required first - the OBJ carries UV-split vertices, and without merging
    # them every triangle looks like its own island.
    m = o3d.io.read_triangle_mesh(str(src))
    m.remove_duplicated_vertices()
    m.remove_degenerate_triangles()
    m.remove_duplicated_triangles()
    v = np.asarray(m.vertices)
    floor_y = float(np.percentile(v[:, 1], 2))
    tri = np.asarray(m.triangles)
    keep_v = v[:, 1] > floor_y + floor_clear
    m.remove_triangles_by_mask(~keep_v[tri].all(axis=1))
    m.remove_unreferenced_vertices()

    labels, _, areas = m.cluster_connected_triangles()
    labels, areas = np.asarray(labels), np.asarray(areas)
    n_parts = len(areas)
    if n_parts > 1:
        m.remove_triangles_by_mask(labels != int(areas.argmax()))
        m.remove_unreferenced_vertices()
    dst = out / "kiri_oss_object.ply"
    o3d.io.write_triangle_mesh(str(dst), m)

    vv = np.asarray(m.vertices)
    dims = _gravity_dims(vv, floor_y)
    lwh_in = dims["gravity_aligned_lwh_in"]
    top = dims.pop("_top_y")
    meta = {"mesh_base_y_m": floor_y, "floor_clearance_m": floor_clear,
            "n_parts": n_parts, "n_vertices": int(len(vv)),
            "largest_part_area_m2": float(areas.max()) if n_parts else 0.0,
            **dims}

    # Masking removed the ground, so the mesh's own base is the underside of the
    # object, not the floor. Where depth gave us a real floor plane, quote the
    # height off that too - that is the number a buyer measures with a tape.
    ing = json.loads((out / "ingest.json").read_text()) if (out / "ingest.json").is_file() else {}
    floor_ar = ing.get("floor_y_m")
    if floor_ar is not None:
        h_floor = top - float(floor_ar)
        meta["lidar_floor_y_m"] = float(floor_ar)
        meta["height_from_floor_in"] = round(h_floor / 0.0254, 2)
        meta["lwh_from_floor_in"] = [lwh_in[0], lwh_in[1],
                                     round(h_floor / 0.0254, 2)]
        meta["base_above_floor_in"] = round((floor_y - float(floor_ar)) / 0.0254, 2)

    # The splat is trained on unmasked frames, so it is a *scene* splat: the
    # couch is continuous with the rug and the clutter around it, and clustering
    # alone runs away (it read 119 in long at opacity 0.2). Crop it to the mesh
    # object's footprint instead. That yields a genuinely useful object splat,
    # but its extent is inherited from the mesh - it is NOT an independent
    # measurement, and is reported as a coverage check, not as dims.
    splat = out / "kiri_oss_3dgs_metric.ply"
    if splat.is_file():
        pc = o3d.io.read_point_cloud(str(splat))
        p = np.asarray(pc.points)
        pcol = np.asarray(pc.colors)
        import cv2

        base = float(floor_ar) if floor_ar is not None else floor_y
        pad = 0.05
        # Crop to the mesh's *rotated* footprint, not its AABB: the couch sits at
        # ~30 deg to the world axes, so an axis-aligned box drags in the floor
        # corners and reads back as a near-square footprint.
        (cx, cz), (rw, rh), ang = cv2.minAreaRect(vv[:, [0, 2]].astype(np.float32))
        th = np.deg2rad(-float(ang))
        dx, dz = p[:, 0] - cx, p[:, 2] - cz
        u = np.cos(th) * dx - np.sin(th) * dz
        w = np.sin(th) * dx + np.cos(th) * dz
        inside = ((np.abs(u) <= rw / 2 + pad) & (np.abs(w) <= rh / 2 + pad)
                  & (p[:, 1] > base + floor_clear) & (p[:, 1] < vv[:, 1].max() + pad))
        p = p[inside]
        if len(p) > 100:
            # Carry the colour across. Rebuilding the cloud from points alone
            # silently shipped a colourless file while the source had RGB.
            obj_pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(p))
            if len(pcol) == len(inside):
                obj_pc.colors = o3d.utility.Vector3dVector(pcol[inside])
            o3d.io.write_point_cloud(str(out / "kiri_oss_3dgs_object.ply"), obj_pc)
            n_scene = _ply_vertex_count(out / "kiri_oss_3dgs.ply")
            meta["splat"] = {
                "n_points": int(len(p)),
                # Denominator matters: this is a fraction of the OPACITY-FILTERED
                # cloud, which is ~3% of the scene splat. Quoting it against the
                # scene splat overstates coverage 30x.
                "n_opacity_filtered": int(len(inside)),
                "n_scene_gaussians": n_scene,
                "fraction_of_opacity_filtered": round(float(inside.mean()), 4),
                "fraction_of_scene_gaussians": (round(len(p) / n_scene, 4)
                                                if n_scene else None),
                "cropped_to": "mesh object rotated footprint",
                "is_gaussian_splat": False,
                "note": "point cloud of gaussian CENTRES - no opacity/scale/rotation/SH",
                "independent_measurement": False}
            log(f"isolate: splat cropped to the mesh footprint: {len(p)} points "
                f"({inside.mean()*100:.1f}% of the opacity-filtered cloud, "
                f"{len(p)/n_scene*100:.2f}% of the {n_scene} scene gaussians) -> "
                f"kiri_oss_3dgs_object.ply")

    (out / "isolate.json").write_text(json.dumps(meta, indent=2))
    log(f"isolate: mesh base y={floor_y:.3f} m, largest of {n_parts} parts "
        f"({len(vv)} verts)")
    log(f"isolate: L x W x H = {lwh_in[0]} x {lwh_in[1]} x {lwh_in[2]} in "
        f"(height off the mesh's own base)")
    if floor_ar is not None:
        log(f"isolate: L x W x H = {meta['lwh_from_floor_in'][0]} x "
            f"{meta['lwh_from_floor_in'][1]} x {meta['lwh_from_floor_in'][2]} in "
            f"(height off the LiDAR floor; base sits "
            f"{meta['base_above_floor_in']:.1f} in up)")
    return meta


# --------------------------------------------------------------------------
# stage 9: validate


def _fuse_lidar(session: Path, frames: list[dict], stride: int = 1) -> np.ndarray:
    """Every depth frame unprojected into the AR world frame."""
    import cv2

    ark_to_cv = np.diag([1.0, -1.0, -1.0])
    out = []
    for fr in frames[::stride]:
        dpath = session / fr.get("depth", "")
        pose, K = fr.get("pose"), fr.get("K_color")
        if not (dpath.is_file() and pose and K):
            continue
        d = cv2.imread(str(dpath), cv2.IMREAD_UNCHANGED)
        if d is None:
            continue
        d = d.astype(np.float32) / 1000.0
        dh, dw = d.shape
        cw, ch = fr["color_size"]
        fx, fy, cx, cy = K[0] * dw / cw, K[1] * dh / ch, K[2] * dw / cw, K[3] * dh / ch
        u, v = np.meshgrid(np.arange(dw), np.arange(dh))
        ok = (d > 0.3) & (d < 5.0)
        if not ok.any():
            continue
        z = d[ok]
        pts = np.stack([(u[ok] - cx) / fx * z, (v[ok] - cy) / fy * z, z], 1) @ ark_to_cv.T
        T = np.asarray(pose, float).reshape(4, 4)
        out.append(pts @ T[:3, :3].T + T[:3, 3])
    return np.vstack(out) if out else np.empty((0, 3))


def stage_validate(src: Path, out: Path, voxel: float) -> dict:
    """Agreement between the RGB-only mesh and the LiDAR depth it never saw.

    This used to be a shell one-liner whose numbers reached the docs but no
    artifact and no code - i.e. an unreproducible claim. It is a stage now.

    Reported BOTH ways on purpose. mesh->LiDAR alone is biased low: it asks only
    "is every reconstructed vertex near some depth sample", and says nothing
    about the surfaces the RGB mesh never built. LiDAR->mesh (restricted to the
    object's own footprint, or the whole room would count as missing) is the
    completeness direction and is always the worse number.
    """
    import open3d as o3d
    from scipy.spatial import cKDTree

    mesh_p = out / "kiri_oss_object.ply"
    if not mesh_p.is_file():
        log("validate: no isolated object yet - run isolate first")
        return {}
    if not (src / "manifest.json").is_file():
        log("validate: source has no depth (not a session) - skipping")
        return {}

    frames, _ = _load_session(src)
    lidar = _fuse_lidar(src, frames)
    if not len(lidar):
        log("validate: no usable depth frames")
        return {}
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(lidar))
    lidar = np.asarray(pc.voxel_down_sample(voxel).points)

    import trimesh

    mesh = trimesh.load(mesh_p, process=False, force="mesh")
    V = np.asarray(mesh.vertices)
    # Sample the SURFACE by area, not the vertex list. Vertices cluster on small,
    # well-reconstructed triangles and under-count the big flat ones spanning the
    # badly-reconstructed leather, which flatters the score by ~8 points (84% of
    # vertices vs 76% of surface within 20 mm). The LiDAR side is already
    # area-fair because it is voxel-downsampled, so sampling here is also what
    # makes the two directions comparable.
    M, _ = trimesh.sample.sample_surface(mesh, min(400_000, max(50_000, len(V) * 2)),
                                         seed=0)
    M = np.asarray(M)
    lo, hi = V.min(0) - 0.10, V.max(0) + 0.10
    near = lidar[((lidar > lo) & (lidar < hi)).all(1)]

    d_ml, _ = cKDTree(near).query(M)          # accuracy
    d_lm, _ = cKDTree(M).query(near)          # completeness

    def stats(d):
        return {"median_mm": round(float(np.median(d)) * 1000, 1),
                "mean_mm": round(float(d.mean()) * 1000, 1),
                "p95_mm": round(float(np.percentile(d, 95)) * 1000, 1),
                "within_20mm_pct": round(float((d < 0.02).mean()) * 100, 1)}

    meta = {"voxel_m": voxel, "n_lidar_points": int(len(lidar)),
            "n_lidar_in_object_box": int(len(near)),
            "n_surface_samples": int(len(M)), "n_mesh_vertices": int(len(V)),
            "sampling": "area-uniform surface samples (not vertices)",
            "mesh_to_lidar": stats(d_ml), "lidar_to_mesh": stats(d_lm),
            "method": ("one-sided nearest-neighbour each way; LiDAR side restricted "
                       "to the object bbox + 10 cm, else the whole room counts as "
                       "missing geometry")}
    (out / "validate.json").write_text(json.dumps(meta, indent=2))
    log(f"validate: mesh->LiDAR median {meta['mesh_to_lidar']['median_mm']} mm "
        f"({meta['mesh_to_lidar']['within_20mm_pct']}% within 20 mm)  |  "
        f"LiDAR->mesh median {meta['lidar_to_mesh']['median_mm']} mm "
        f"({meta['lidar_to_mesh']['within_20mm_pct']}% within 20 mm)")
    return meta


# --------------------------------------------------------------------------
# stage 10: report


def stage_report(out: Path, truth: Path | None) -> dict:
    """Side-by-side of every branch, in one ruler.

    Everything is quoted as gravity-aligned L x W x H in inches. The free 3D OBB
    that measure_geometry uses is deliberately not the headline: on this sofa it
    tilts to hug the point soup and reads 100 in long.
    """
    report: dict = {"out": str(out)}
    for name in ("ingest", "curate", "mask", "scale", "isolate", "validate"):
        p = out / f"{name}.json"
        if p.is_file():
            d = json.loads(p.read_text())
            d.pop("track", None)
            d.pop("frames", None)
            report[name] = d

    iso = report.get("isolate", {})
    rows = []
    if iso.get("lwh_from_floor_in"):
        rows.append(("photogrammetry (AliceVision MVS)", iso["lwh_from_floor_in"],
                     f"{iso.get('n_vertices', 0)} verts, height off LiDAR floor"))
    elif iso.get("gravity_aligned_lwh_in"):
        rows.append(("photogrammetry (AliceVision MVS)", iso["gravity_aligned_lwh_in"],
                     f"{iso.get('n_vertices', 0)} verts, height off mesh base"))

    if truth and truth.is_file():
        t = json.loads(truth.read_text())
        block = t.get(t.get("primary", "object"), {})
        if "obb" in block:
            rows.append(("ours RGB-D object_asset (free OBB)",
                         [round(x, 2) for x in block["obb"]["raw_inches_sorted_lwh"]],
                         "existing pipeline, for comparison"))
        report["truth"] = t

    report["side_by_side"] = [{"branch": n, "lwh_in": v, "note": c} for n, v, c in rows]
    (out / "kiri_oss_report.json").write_text(json.dumps(report, indent=2))

    log("report  (gravity-aligned L x W x H, inches):")
    for name, (l, w, h), note in rows:
        log(f"    {name:36s} {l:7.2f} x {w:6.2f} x {h:6.2f}   {note}")
    sp = iso.get("splat")
    if sp:
        log(f"    {'3DGS (3DGUT) object splat':36s} {'visual asset':>21s}   "
            f"{sp['n_points']} gaussians, {sp['fraction_of_scene_splat']*100:.1f}% "
            "of the scene splat (extent inherited from the mesh)")
    sc = report.get("scale", {})
    if sc.get("metric"):
        log(f"    scale bridge: SfM unit = {sc['sfm_to_metres']:.4f} m from "
            f"{sc['n_paired_cameras']} cameras, fit RMS "
            f"{sc['fit_rms_m']*1000:.0f} mm ({sc.get('fit_rms_pct_of_track')}%)")
    log(f"report -> {out / 'kiri_oss_report.json'}")
    return report


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="CrateScanner session/ or a photo folder")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--stages", default=",".join(DEFAULT_STAGES),
                    help=f"comma list of {ALL_STAGES} (default: {','.join(DEFAULT_STAGES)})")
    ap.add_argument("--force", action="store_true", help="re-run stages that already have output")
    ap.add_argument("--max-edge", type=int, default=2016,
                    help="downscale photos to this long edge (default 2016)")
    ap.add_argument("--fov", type=float, help="horizontal FoV in degrees (else from manifest K)")
    ap.add_argument("--keep", type=float, default=0.9,
                    help="fraction of frames to keep in curation (default 0.9)")
    ap.add_argument("--mask-model", default="birefnet-general",
                    help="rembg model (default birefnet-general - KIRI's own choice)")
    ap.add_argument("--no-masks", action="store_true", help="run MVS without masks")
    ap.add_argument("--preset", default="high",
                    choices=["low", "medium", "normal", "high", "ultra"],
                    help="SIFT describer preset (default high - the subject is low-texture)")
    ap.add_argument("--mvs-downscale", type=int, default=2,
                    help="depth map downscale (default 2; 1 is ~4x slower)")
    ap.add_argument("--texture-side", type=int, default=4096)
    ap.add_argument("--iterations", type=int, default=7000, help="3DGS iterations")
    ap.add_argument("--max-gaussians", type=int, default=1_000_000)
    ap.add_argument("--splat-opacity", type=float, default=0.3,
                    help="opacity floor when measuring the splat (default 0.3)")
    ap.add_argument("--floor-clear", type=float, default=0.03,
                    help="metres above the floor plane to cut in isolate (default 0.03)")
    ap.add_argument("--validate-voxel", type=float, default=0.01,
                    help="voxel size (m) when downsampling fused LiDAR (default 0.01)")
    ap.add_argument("--truth", type=Path, help="dims.json to fold into the report")
    args = ap.parse_args()

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    bad = [s for s in stages if s not in ALL_STAGES]
    if bad:
        ap.error(f"unknown stage(s) {bad}; choose from {ALL_STAGES}")
    out = _mkdir(args.out)
    t0 = time.time()

    def want(stage: str, marker: Path) -> bool:
        if stage not in stages:
            return False
        if marker.exists() and not args.force:
            log(f"{stage}: skip (exists: {marker.name}) - use --force to redo")
            return False
        return True

    fov = args.fov
    if want("ingest", out / "ingest.json"):
        fov = stage_ingest(args.src, out, args.max_edge, args.fov)["fov_deg"]
    elif (out / "ingest.json").is_file():
        fov = args.fov or json.loads((out / "ingest.json").read_text()).get("fov_deg")

    if want("curate", out / "curate.json"):
        stage_curate(out, args.keep)
    if want("mask", out / "mask.json"):
        stage_mask(out, args.mask_model)
    if want("sfm", out / "sfm" / "sfm.sfm"):
        stage_sfm(out, fov, args.preset)
    if want("mvs", out / "kiri_oss_photo.obj"):
        stage_mvs(out, args.mvs_downscale, not args.no_masks, args.texture_side)
    if want("gs", out / "kiri_oss_3dgs.ply"):
        stage_gs(out, args.iterations, args.max_gaussians)
    if want("scale", out / "scale.json"):
        stage_scale(out, args.splat_opacity)
    if want("isolate", out / "isolate.json"):
        stage_isolate(out, args.floor_clear)
    if want("validate", out / "validate.json"):
        stage_validate(args.src, out, args.validate_voxel)
    if "report" in stages:
        stage_report(out, args.truth)

    log(f"done in {time.time() - t0:.0f}s -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
