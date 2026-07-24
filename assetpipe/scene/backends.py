"""Scene-level reconstruction backends: frames in, ONE point cloud out.

This is the "Scaniverse path". Unlike ``assetpipe.reconstruct`` (per-object
meshes for the twin catalog), a scene backend takes the whole capture and
returns a single colored point cloud / splat of everything in view — no
detector, no classes, no catalog.

    Backend        needs                     what you get
    -------------  ------------------------  --------------------------------
    stub           nothing (Pillow or .ppm)  photo-ring plumbing stand-in
    colmap         pip install pycolmap      REAL sparse SfM cloud (CPU, ~min)
    vggt           torch + vggt (GPU)        dense feed-forward cloud (~sec)
    splatfacto     nerfstudio env (GPU)      trained gaussian splat (~10 min)
    3dgut          3dgrut env (GPU)          3DGUT splat + optional USDZ

``make_scene_backend("auto")`` picks the best installed one (vggt > colmap
> stub) so `assetpipe scan video.mp4` just works everywhere. splatfacto /
3dgut are opt-in room-shell paths that run out-of-env (see
``SplatfactoSceneBackend`` / ``ThreeDGutSceneBackend``).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass, field


@dataclass
class ScenePointCloud:
    xyz: list[tuple[float, float, float]]
    rgb: list[tuple[int, int, int]]
    gaussian_ply: str | None = None  # set when the backend trained real gaussians
    stats: dict = field(default_factory=dict)
    # solved camera centers — drives the live "which angles have I covered?" ring
    cameras: list[tuple[float, float, float]] = field(default_factory=list)

    def coverage_azimuths(self) -> list[float]:
        """Camera angles (deg, 0–360) around the scene, in the plane the
        capture path spans. Empty until enough views are registered."""
        if len(self.cameras) < 3 or not self.xyz:
            return []
        import numpy as np

        target = np.asarray(self.xyz, np.float64).mean(axis=0)
        P = np.asarray(self.cameras, np.float64) - target
        _, _, Vt = np.linalg.svd(np.cov(P.T))  # dominant plane of the orbit
        ang = np.degrees(np.arctan2(P @ Vt[1], P @ Vt[0])) % 360.0
        return sorted(float(a) for a in ang)


class SceneBackend:
    name = "base"

    def reconstruct(self, image_paths: list[str], work_dir: str) -> ScenePointCloud:
        raise NotImplementedError


# --------------------------------------------------------------------------- stub
def _load_rgb(path: str) -> tuple[int, int, bytes]:
    """(width, height, RGB bytes). Pillow when available, stdlib P6 PPM else."""
    try:
        from PIL import Image  # lazy

        img = Image.open(path).convert("RGB")
        return img.size[0], img.size[1], img.tobytes()
    except ImportError:
        pass
    with open(path, "rb") as fh:
        if fh.read(2) != b"P6":
            raise ValueError(f"{path}: need Pillow for non-PPM images")
        vals: list[int] = []
        while len(vals) < 3:  # width height maxval, with comments allowed
            line = fh.readline().split(b"#")[0].split()
            vals += [int(v) for v in line]
        w, h, _maxval = vals[:3]
        return w, h, fh.read(w * h * 3)


class StubSceneBackend(SceneBackend):
    """Zero-dep plumbing stand-in: pixels of each frame are placed on a
    'photo ring' around the origin (frame i at angle 2πi/N, luminance as
    relief). Proves click → cloud → .ply/.splat end-to-end anywhere; swap
    for colmap/vggt to get real geometry."""

    name = "stub"

    def __init__(self, max_points: int = 80_000, ring_radius: float = 2.0) -> None:
        self.max_points = max_points
        self.ring_radius = ring_radius

    def reconstruct(self, image_paths: list[str], work_dir: str) -> ScenePointCloud:
        import math

        if not image_paths:
            raise ValueError("no frames to reconstruct")
        xyz: list[tuple[float, float, float]] = []
        rgb: list[tuple[int, int, int]] = []
        per_frame = max(1, self.max_points // len(image_paths))
        for i, path in enumerate(image_paths):
            w, h, px = _load_rgb(path)
            step = max(1, int((w * h / per_frame) ** 0.5))
            a = 2 * math.pi * i / len(image_paths)
            cx, cz = self.ring_radius * math.sin(a), self.ring_radius * math.cos(a)
            right = (math.cos(a), 0.0, -math.sin(a))  # tangent, faces the origin
            bw = 1.6  # billboard width in meters
            bh = bw * h / w
            for v in range(0, h, step):
                for u in range(0, w, step):
                    o = 3 * (v * w + u)
                    r, g, b = px[o], px[o + 1], px[o + 2]
                    relief = ((r + g + b) / 765.0 - 0.5) * 0.15
                    fx = (u / w - 0.5) * bw
                    fy = (0.5 - v / h) * bh
                    nx, nz = -math.sin(a), -math.cos(a)  # inward normal
                    xyz.append((cx + right[0] * fx + nx * relief,
                                1.0 + fy,
                                cz + right[2] * fx + nz * relief))
                    rgb.append((r, g, b))
        return ScenePointCloud(xyz, rgb, stats={"frames": len(image_paths)})


# ------------------------------------------------------------------------- colmap
def _run_colmap_sparse(image_paths: list[str], work_dir: str,
                       num_threads: int) -> tuple[str, str, dict]:
    """Frames -> COLMAP sparse SfM model on disk. -> (img_dir, sparse_dir, recs).

    Shared by the colmap point-cloud backend and the splatfacto splat backend:
    both need exactly this SfM solve, and splatfacto additionally needs the
    model *on disk* (not just the points) to seed nerfstudio's poses.
    """
    try:
        import pycolmap
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pip install pycolmap (in the assetpipe env)") from e

    os.makedirs(work_dir, exist_ok=True)
    img_dir = os.path.join(work_dir, "images")
    os.makedirs(img_dir, exist_ok=True)
    for p in image_paths:  # colmap wants a directory; link, don't copy
        dst = os.path.join(img_dir, os.path.basename(p))
        if not os.path.exists(dst):
            try:
                os.symlink(os.path.abspath(p), dst)
            except OSError:  # e.g. FAT mounts
                shutil.copy(p, dst)

    db = os.path.join(work_dir, "database.db")
    # db caches features/matches across re-solves (live sessions); the
    # sparse models are cheap derived output — start each solve clean
    sparse = os.path.join(work_dir, "sparse")
    shutil.rmtree(sparse, ignore_errors=True)
    os.makedirs(sparse, exist_ok=True)
    # pycolmap 4.x: threads live on FeatureExtractionOptions, not Sift*
    extract_kw: dict = {}
    try:
        fe = pycolmap.FeatureExtractionOptions()
        fe.num_threads = num_threads
        # keep SIFT RAM in check on phone-res captures
        if hasattr(fe, "max_image_size"):
            fe.max_image_size = min(getattr(fe, "max_image_size", 3200) or 3200,
                                    1600)
        extract_kw["extraction_options"] = fe
    except (AttributeError, TypeError):  # pragma: no cover — older pycolmap
        try:
            extract_kw["sift_options"] = pycolmap.SiftExtractionOptions(
                num_threads=num_threads)
        except (AttributeError, TypeError):
            pass
    try:  # all frames come from one physical camera
        pycolmap.extract_features(db, img_dir,
                                  camera_mode=pycolmap.CameraMode.SINGLE,
                                  **extract_kw)
    except TypeError:  # older pycolmap without these kwargs
        pycolmap.extract_features(db, img_dir)
    if len(image_paths) > 45 and hasattr(pycolmap, "match_sequential"):
        pycolmap.match_sequential(db)
    else:
        pycolmap.match_exhaustive(db)
    map_kw: dict = {}
    try:
        map_kw["options"] = pycolmap.IncrementalPipelineOptions(
            num_threads=num_threads)
    except (AttributeError, TypeError):  # pragma: no cover — old pycolmap
        pass
    try:
        recs = pycolmap.incremental_mapping(db, img_dir, sparse, **map_kw)
    except TypeError:  # pragma: no cover — options kwarg unsupported
        recs = pycolmap.incremental_mapping(db, img_dir, sparse)
    if not recs:
        raise RuntimeError(
            "COLMAP could not reconstruct the scene — capture slower, "
            "overlap frames more, avoid textureless/blurry views"
        )
    return img_dir, sparse, recs


class ColmapSceneBackend(SceneBackend):
    """Real structure-from-motion via pycolmap (SIFT → match → incremental
    mapping → sparse RGB cloud). CPU-only, no model weights: the reliable
    default for 'I recorded a video, give me a point cloud'."""

    name = "colmap"

    def __init__(self, num_threads: int | None = None) -> None:
        # one SIFT worker per core OOMs 1080p captures on shared boxes —
        # cap it (override via $ASSETPIPE_COLMAP_THREADS)
        self.num_threads = num_threads if num_threads is not None else int(
            os.environ.get("ASSETPIPE_COLMAP_THREADS", "8"))

    def reconstruct(self, image_paths: list[str], work_dir: str) -> ScenePointCloud:
        _img_dir, sparse_dir, recs = _run_colmap_sparse(
            image_paths, work_dir, self.num_threads)
        best_key = max(recs, key=lambda k: len(recs[k].points3D))
        rec = recs[best_key]
        self._winning_sparse = os.path.join(sparse_dir, str(best_key))
        xyz, rgb = [], []
        for p in rec.points3D.values():
            x, y, z = (float(v) for v in p.xyz)
            r, g, b = (int(v) for v in p.color)
            xyz.append((x, y, z))
            rgb.append((r, g, b))
        cams = []
        for im in rec.images.values():
            try:
                c = im.projection_center()
            except AttributeError:  # pragma: no cover — pycolmap API drift
                c = im.cam_from_world.inverse().translation
            cams.append(tuple(float(v) for v in c))
        return ScenePointCloud(
            xyz, rgb,
            stats={"frames": len(image_paths),
                   "registered": len(rec.images),
                   "models": len(recs)},
            cameras=cams,
        )


# --------------------------------------------------------------------------- vggt
class VggtSceneBackend(SceneBackend):  # pragma: no cover — needs GPU + weights
    """Feed-forward VGGT-1B (CVPR'25): unposed frames → dense point map in
    ONE forward pass. The closest thing to Scaniverse's instant feel.

    INSTALL (assetpipe env):  pip install vggt   # + ~5 GB HF weights
    """

    name = "vggt"

    def __init__(self, max_frames: int = 32, conf_percentile: float = 40.0,
                 max_points: int = 400_000) -> None:
        self.max_frames = max_frames
        self.conf_percentile = conf_percentile
        self.max_points = max_points

    def reconstruct(self, image_paths: list[str], work_dir: str) -> ScenePointCloud:
        try:
            import torch
            from vggt.models.vggt import VGGT
            from vggt.utils.load_fn import load_and_preprocess_images
        except ImportError as e:
            raise RuntimeError(
                "VGGT backend needs the GPU env: conda activate assetpipe "
                "&& pip install vggt"
            ) from e

        if len(image_paths) > self.max_frames:  # spread picks across the sweep
            stride = len(image_paths) / self.max_frames
            image_paths = [image_paths[int(i * stride)] for i in range(self.max_frames)]

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if (device == "cuda" and
                                   torch.cuda.get_device_capability()[0] >= 8) \
            else torch.float16
        model = VGGT.from_pretrained("facebook/VGGT-1B").to(device).eval()
        images = load_and_preprocess_images(image_paths).to(device)
        with torch.no_grad(), torch.autocast(device_type=device, dtype=dtype):
            pred = model(images)

        pts = pred["world_points"].squeeze(0).float().cpu()      # S,H,W,3
        conf = pred["world_points_conf"].squeeze(0).float().cpu()  # S,H,W
        imgs = images.squeeze(0).float().cpu()                   # S,3,H,W
        thresh = torch.quantile(conf.flatten(), self.conf_percentile / 100.0)
        keep = conf >= thresh
        p = pts[keep]                                            # M,3
        c = (imgs.permute(0, 2, 3, 1)[keep] * 255).clamp(0, 255).byte()
        if p.shape[0] > self.max_points:
            sel = torch.randperm(p.shape[0])[: self.max_points]
            p, c = p[sel], c[sel]
        return ScenePointCloud(
            [tuple(map(float, q)) for q in p.tolist()],
            [tuple(map(int, q)) for q in c.tolist()],
            stats={"frames": len(image_paths), "device": device},
        )


# --------------------------------------------------------------------- splatfacto
def _find_nerfstudio_python() -> str | None:
    """Locate a python whose env has nerfstudio + gsplat.

    nerfstudio lives in its OWN conda env (default `roomrecon`) so its cu124
    CUDA stack can't disturb the assetpipe install — same reasoning as gen3d.
    Override with $ASSETPIPE_NERFSTUDIO_PYTHON.
    """
    env = os.environ.get("ASSETPIPE_NERFSTUDIO_PYTHON")
    if env and os.path.exists(env):
        return env
    home = os.path.expanduser("~")
    for name in ("roomrecon", "nerfstudio", "splat"):
        cand = os.path.join(home, "miniconda3", "envs", name, "bin", "python")
        if os.path.exists(cand):
            return cand
    return None


class SplatfactoSceneBackend(SceneBackend):  # pragma: no cover — needs nerfstudio
    """Quality path: a trained gaussian **splat** of the whole scene — the
    photoreal, navigable "room shell" (complement to the per-object generative
    assets). pycolmap solves poses, nerfstudio's splatfacto trains the splat
    (~10 min on the 4080), we export the .ply. The artifact is REAL gaussians
    (``gaussian_ply``), which ``scene.splat`` renders directly.

    No `colmap` binary needed: we run SfM through pycolmap (assetpipe env) and
    hand the sparse model to nerfstudio's own ``colmap_to_json``. nerfstudio +
    gsplat run in a separate env, reached via ``_find_nerfstudio_python`` (see
    ``_splat_driver`` for the env boundary).
    """

    name = "splatfacto"

    def __init__(self, iterations: int = 15_000,
                 num_threads: int | None = None,
                 nerfstudio_python: str | None = None) -> None:
        self.iterations = iterations
        self.num_threads = num_threads if num_threads is not None else int(
            os.environ.get("ASSETPIPE_COLMAP_THREADS", "8"))
        self.nerfstudio_python = nerfstudio_python or _find_nerfstudio_python()

    def reconstruct(self, image_paths: list[str], work_dir: str) -> ScenePointCloud:
        import glob
        import subprocess

        py = self.nerfstudio_python
        if not py:
            raise RuntimeError(
                "no nerfstudio env found — set $ASSETPIPE_NERFSTUDIO_PYTHON to "
                "the python of an env with nerfstudio + gsplat (e.g. roomrecon)")
        ns_bin = os.path.dirname(py)  # ns-train / ns-export sit beside python

        # 1) SfM poses via pycolmap (assetpipe env) -> sparse model on disk
        _img_dir, sparse, recs = _run_colmap_sparse(
            image_paths, work_dir, self.num_threads)
        best_key = max(recs, key=lambda k: len(recs[k].points3D))
        sparse_model = os.path.join(sparse, str(best_key))

        # 2) sparse model -> transforms.json (+ sparse_pc.ply), in the ns env
        driver = os.path.join(os.path.dirname(__file__), "_splat_driver.py")
        subprocess.run([py, driver, "colmap2json", sparse_model, work_dir],
                       check=True)

        # 3) train the splat, then 4) export gaussians — plain CLI in the ns env
        train_dir = os.path.join(work_dir, "train")
        out = os.path.join(work_dir, "splat")
        subprocess.run(
            [os.path.join(ns_bin, "ns-train"), "splatfacto",
             "--data", work_dir, "--output-dir", train_dir,
             "--max-num-iterations", str(self.iterations),
             "--viewer.quit-on-train-completion", "True",
             "--logging.local-writer.enable", "False",
             "nerfstudio-data", "--auto-scale-poses", "True"],
            check=True)
        cfgs = sorted(glob.glob(os.path.join(train_dir, "**", "config.yml"),
                                recursive=True))
        if not cfgs:
            raise RuntimeError("ns-train produced no config.yml")
        subprocess.run(
            [os.path.join(ns_bin, "ns-export"), "gaussian-splat",
             "--load-config", cfgs[-1], "--output-dir", out],
            check=True)
        plys = sorted(glob.glob(os.path.join(out, "*.ply")))
        if not plys:
            raise RuntimeError("ns-export produced no .ply")
        xyz, rgb = _gaussian_ply_points(plys[-1])
        return ScenePointCloud(xyz, rgb, gaussian_ply=plys[-1],
                               stats={"frames": len(image_paths),
                                      "registered": len(recs[best_key].images),
                                      "iterations": self.iterations})


def _gaussian_ply_points(path):  # pragma: no cover — exercised via splatfacto/3dgut
    """Gaussian centers + color from a 3DGS-convention PLY (for preview).

    3DGS / 3dgrut export ``f_dc_0..2`` (spherical-harmonics DC) instead of
    uchar RGB. Decode with the usual ``0.5 + 0.28209479 * f_dc`` map; fall
    back to ``read_ply`` for plain colored clouds.
    """
    import math
    import struct

    with open(path, "rb") as fh:
        if fh.readline().strip() != b"ply":
            raise ValueError(f"{path}: not a PLY file")
        n = 0
        props: list[tuple[str, str]] = []
        while True:
            line = fh.readline()
            if not line:
                raise ValueError(f"{path}: unterminated PLY header")
            tok = line.decode("ascii", "replace").split()
            if not tok:
                continue
            if tok[0] == "element" and tok[1] == "vertex":
                n = int(tok[2])
            elif tok[0] == "property" and n:
                props.append((tok[1], tok[2]))
            elif tok[0] == "end_header":
                break
        names = [p[1] for p in props]
        if "f_dc_0" not in names:
            from .io import read_ply
            return read_ply(path)
        fmt = {"float": "f", "float32": "f", "uchar": "B", "uint8": "B",
               "double": "d", "int": "i"}
        rec = struct.Struct("<" + "".join(fmt[t] for t, _ in props))
        idx = {name: i for i, (_, name) in enumerate(props)}
        C0 = 0.28209479177387814
        xyz, rgb = [], []
        for _ in range(n):
            vals = rec.unpack(fh.read(rec.size))
            xyz.append((vals[idx["x"]], vals[idx["y"]], vals[idx["z"]]))
            r = max(0, min(255, int((0.5 + C0 * vals[idx["f_dc_0"]]) * 255)))
            g = max(0, min(255, int((0.5 + C0 * vals[idx["f_dc_1"]]) * 255)))
            b = max(0, min(255, int((0.5 + C0 * vals[idx["f_dc_2"]]) * 255)))
            rgb.append((r, g, b))
        return xyz, rgb


# ------------------------------------------------------------------------ 3dgut
def _find_3dgrut_python() -> str | None:
    """Locate a python whose env has nv-tlabs/3dgrut (train.py + CUDA exts).

    3dgrut keeps its own conda env (default ``3dgrut``, torch cu118) so its
    OptiX / 3DGUT stack cannot disturb assetpipe — same split as gen3d and
    roomrecon. Override with ``$ASSETPIPE_3DGRUT_PYTHON``.
    """
    env = os.environ.get("ASSETPIPE_3DGRUT_PYTHON")
    if env and os.path.exists(env):
        return env
    home = os.path.expanduser("~")
    for name in ("3dgrut", "3dgut", "3dgrut_cuda12"):
        cand = os.path.join(home, "miniconda3", "envs", name, "bin", "python")
        if os.path.exists(cand):
            return cand
    return None


def _find_3dgrut_root(python_path: str | None = None) -> str | None:
    """Repo root that contains ``train.py`` (clone of nv-tlabs/3dgrut)."""
    env = os.environ.get("ASSETPIPE_3DGRUT_ROOT")
    if env and os.path.exists(os.path.join(env, "train.py")):
        return env
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, "3dgrut"),
                 os.path.join(home, "src", "3dgrut"),
                 os.path.join(home, "code", "3dgrut")):
        if os.path.exists(os.path.join(cand, "train.py")):
            return cand
    # last resort: walk up from the env's site-packages if editable-installed
    if python_path:
        site = os.path.join(os.path.dirname(os.path.dirname(python_path)),
                            "lib")
        # not reliable enough — leave None
        _ = site
    return None


def _write_colmap_text_pinhole(rec, sparse_out: str) -> None:
    """Write a classic COLMAP text model with PINHOLE cameras for 3dgrut/NuRec.

    pycolmap 4.x emits rigs/frames binaries that older 3dgrut readers skip;
    NVIDIA's Isaac path also wants PINHOLE / SIMPLE_PINHOLE. We drop a mild
    SIMPLE_RADIAL ``k`` into PINHOLE ``fx=fy=f``.
    """
    os.makedirs(sparse_out, exist_ok=True)
    # cameras.txt
    with open(os.path.join(sparse_out, "cameras.txt"), "w") as fh:
        fh.write("# Camera list with one line of data per camera:\n")
        fh.write("#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n")
        fh.write(f"# Number of cameras: {len(rec.cameras)}\n")
        for cid, cam in rec.cameras.items():
            model = str(getattr(cam.model, "name", cam.model))
            w, h = int(cam.width), int(cam.height)
            params = [float(p) for p in cam.params]
            if model in ("SIMPLE_RADIAL", "SIMPLE_PINHOLE"):
                f, cx, cy = params[0], params[1], params[2]
                fh.write(f"{cid} PINHOLE {w} {h} {f} {f} {cx} {cy}\n")
            elif model == "PINHOLE":
                fx, fy, cx, cy = params[:4]
                fh.write(f"{cid} PINHOLE {w} {h} {fx} {fy} {cx} {cy}\n")
            elif model == "RADIAL" and len(params) >= 4:
                f, cx, cy = params[0], params[1], params[2]
                fh.write(f"{cid} PINHOLE {w} {h} {f} {f} {cx} {cy}\n")
            else:  # best-effort: keep declared model + params
                fh.write(f"{cid} {model} {w} {h} "
                         + " ".join(str(p) for p in params) + "\n")
    # images.txt — two lines per image (COLMAP text convention)
    with open(os.path.join(sparse_out, "images.txt"), "w") as fh:
        fh.write("# Image list with two lines of data per image:\n")
        fh.write("#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n")
        fh.write("#   POINTS2D[] as (X, Y, POINT3D_ID)\n")
        fh.write(f"# Number of images: {len(rec.images)}\n")
        for iid, im in sorted(rec.images.items()):
            # pycolmap 3/4: cam_from_world is a Rigid3d
            try:
                T = im.cam_from_world()
            except TypeError:
                T = im.cam_from_world
            try:
                q = T.rotation.quat  # (w, x, y, z) in newer pycolmap
                t = T.translation
            except AttributeError:  # pragma: no cover
                q = im.qvec
                t = im.tvec
            name = im.name
            fh.write(
                f"{iid} {q[0]} {q[1]} {q[2]} {q[3]} "
                f"{t[0]} {t[1]} {t[2]} {im.camera_id} {name}\n"
            )
            # empty POINTS2D line — 3dgrut only needs poses for training;
            # points come from points3D.txt
            fh.write("\n")
    # points3D.txt
    with open(os.path.join(sparse_out, "points3D.txt"), "w") as fh:
        fh.write("# 3D point list with one line of data per point:\n")
        fh.write("#   POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] "
                 "as (IMAGE_ID, POINT2D_IDX)\n")
        fh.write(f"# Number of points: {len(rec.points3D)}\n")
        for pid, p in rec.points3D.items():
            x, y, z = (float(v) for v in p.xyz)
            r, g, b = (int(v) for v in p.color)
            err = float(getattr(p, "error", 0.0) or 0.0)
            fh.write(f"{pid} {x} {y} {z} {r} {g} {b} {err}\n")


class ThreeDGutSceneBackend(SceneBackend):  # pragma: no cover — needs 3dgrut
    """Room-shell path via NVIDIA 3DGUT (nv-tlabs/3dgrut).

    pycolmap solves poses in the assetpipe env; training + USDZ export run in
    the separate ``3dgrut`` conda env (see ``$ASSETPIPE_3DGRUT_PYTHON`` /
    ``$ASSETPIPE_3DGRUT_ROOT``). Matches the NuRec recipe: COLMAP → 3DGUT →
    USD for Isaac Sim / Omniverse.
    """

    name = "3dgut"

    def __init__(self, iterations: int = 30_000,
                 num_threads: int | None = None,
                 config_name: str = "apps/colmap_3dgut_mcmc.yaml",
                 export_usdz: bool = True,
                 python_path: str | None = None,
                 repo_root: str | None = None) -> None:
        self.iterations = iterations
        self.num_threads = num_threads if num_threads is not None else int(
            os.environ.get("ASSETPIPE_COLMAP_THREADS", "8"))
        self.config_name = config_name
        self.export_usdz = export_usdz
        self.python_path = python_path or _find_3dgrut_python()
        self.repo_root = repo_root or _find_3dgrut_root(self.python_path)

    def reconstruct(self, image_paths: list[str], work_dir: str) -> ScenePointCloud:
        import glob
        import subprocess

        py = self.python_path
        root = self.repo_root
        if not py or not root:
            raise RuntimeError(
                "no 3dgrut env/repo found — set $ASSETPIPE_3DGRUT_PYTHON to "
                "that env's python and $ASSETPIPE_3DGRUT_ROOT to the clone "
                "containing train.py (default ~/3dgrut)")

        # 1) SfM in assetpipe env
        img_dir, sparse, recs = _run_colmap_sparse(
            image_paths, work_dir, self.num_threads)
        best_key = max(recs, key=lambda k: len(recs[k].points3D))
        rec = recs[best_key]

        # 2) classic text + PINHOLE model for 3dgrut / NuRec
        data_root = os.path.join(work_dir, "3dgrut_data")
        sparse_txt = os.path.join(data_root, "sparse", "0")
        shutil.rmtree(data_root, ignore_errors=True)
        os.makedirs(os.path.join(data_root, "images"), exist_ok=True)
        for name in os.listdir(img_dir):
            src = os.path.join(img_dir, name)
            dst = os.path.join(data_root, "images", name)
            if not os.path.exists(dst):
                try:
                    os.symlink(os.path.abspath(src), dst)
                except OSError:
                    shutil.copy(src, dst)
        _write_colmap_text_pinhole(rec, sparse_txt)

        # 3) train + export in the 3dgrut env
        # Absolute paths: train.py's cwd is the 3dgrut repo, and Hydra treats
        # characters like ``~`` as grammar — quote overrides.
        runs = os.path.abspath(os.path.join(work_dir, "3dgut_runs"))
        data_abs = os.path.abspath(data_root)

        def _hydra_path(p: str) -> str:
            return "'" + p.replace("'", "\\'") + "'"

        cmd = [
            py, "train.py",
            f"--config-name={self.config_name}",
            f"path={_hydra_path(data_abs)}",
            f"out_dir={_hydra_path(runs)}",
            "experiment_name=assetpipe_3dgut",
            f"n_iterations={self.iterations}",
            "num_workers=4",
            "val_frequency=999999",
            "export_ply.enabled=true",
            "export_ingp.enabled=false",
            "test_last=false",
            f"export_usdz.enabled={'true' if self.export_usdz else 'false'}",
            "export_usdz.apply_normalizing_transform=true",
        ]
        # Ensure the 3dgrut env's bin (ninja, nvcc wrappers) is on PATH —
        # calling ``…/envs/3dgrut/bin/python`` alone does not activate conda.
        env = os.environ.copy()
        py_bin = os.path.dirname(py)
        env["PATH"] = py_bin + os.pathsep + env.get("PATH", "")
        subprocess.run(cmd, cwd=root, check=True, env=env)

        exp_dir = os.path.join(runs, "assetpipe_3dgut")
        plys = sorted(glob.glob(os.path.join(exp_dir, "**", "export_last.ply"),
                                recursive=True)) or sorted(
            glob.glob(os.path.join(exp_dir, "**", "*.ply"), recursive=True))
        usdzs = sorted(glob.glob(os.path.join(exp_dir, "**", "*.usdz"),
                                 recursive=True))
        # hydra may nest under out_dir/experiment_name/<stamp>/ — also search runs
        if not plys:
            plys = sorted(glob.glob(os.path.join(runs, "**", "export_last.ply"),
                                    recursive=True))
        if not usdzs:
            usdzs = sorted(glob.glob(os.path.join(runs, "**", "*.usdz"),
                                     recursive=True))
        if not plys:
            # fall back to sparse COLMAP cloud for the preview
            xyz, rgb = [], []
            for p in rec.points3D.values():
                xyz.append(tuple(float(v) for v in p.xyz))
                rgb.append(tuple(int(v) for v in p.color))
            return ScenePointCloud(
                xyz, rgb,
                stats={"frames": len(image_paths),
                       "registered": len(rec.images),
                       "iterations": self.iterations,
                       "usdz": usdzs[-1] if usdzs else None,
                       "note": "no export_last.ply — preview is COLMAP sparse"},
            )
        xyz, rgb = _gaussian_ply_points(plys[-1])
        return ScenePointCloud(
            xyz, rgb, gaussian_ply=plys[-1],
            stats={"frames": len(image_paths),
                   "registered": len(rec.images),
                   "iterations": self.iterations,
                   "usdz": usdzs[-1] if usdzs else None},
        )


# ------------------------------------------------------------------------ factory
_BACKENDS = {
    "stub": StubSceneBackend,
    "colmap": ColmapSceneBackend,
    "vggt": VggtSceneBackend,
    "splatfacto": SplatfactoSceneBackend,
    "3dgut": ThreeDGutSceneBackend,
}


def make_scene_backend(name: str = "auto", **kwargs) -> SceneBackend:
    if name != "auto":
        try:
            return _BACKENDS[name](**kwargs)
        except KeyError:
            raise ValueError(f"unknown scene backend {name!r}; "
                             f"choose from {sorted(_BACKENDS)} or 'auto'") from None
    if importlib.util.find_spec("vggt") and importlib.util.find_spec("torch"):
        return VggtSceneBackend(**kwargs)
    if importlib.util.find_spec("pycolmap"):
        return ColmapSceneBackend(**kwargs)
    print("! no real scene backend installed (pip install pycolmap) — "
          "using the stub photo-ring")
    return StubSceneBackend(**kwargs)
