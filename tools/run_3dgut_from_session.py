#!/usr/bin/env python3
"""CrateScan RGB-D session → COLMAP (ARKit poses) → 3DGUT smoke test.

Uses measured poses (not COLMAP SfM) so we test *this dataset* with 3dgrut.
Depth is only used to seed a sparse init point cloud.

Example:
  conda activate assetpipe
  python tools/run_3dgut_from_session.py \\
    captures/work/CrateScan-1E4E971C/CrateScan-1E4E971C/session \\
    --out demo_out/CrateScan-1E4E971C_3dgut \\
    --every 4 --iterations 5000
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from assetpipe.scene.backends import _find_3dgrut_python, _find_3dgrut_root  # noqa: E402
from assetpipe.scene.rgbd_session import load_session  # noqa: E402


def _rot_to_quat_wxyz(R: np.ndarray) -> np.ndarray:
    """Rotation matrix → quaternion (w, x, y, z)."""
    m = np.asarray(R, dtype=np.float64)
    tr = float(np.trace(m))
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    q = np.array([w, x, y, z], dtype=np.float64)
    return q / np.linalg.norm(q)


def write_colmap_from_session(
    session_dir: str,
    data_root: Path,
    every: int = 0,
    max_frames: int = 120,
    seed_stride: int = 8,
) -> dict:
    """Write images/ + sparse/0 COLMAP text using ARKit c2w poses.

    ``every`` of 0 (the default) means AUTO: keep every frame and let
    ``max_frames`` do the thinning.

    A fixed stride was the wrong knob, because two very different things arrive
    here. A **video sweep** is hundreds of near-duplicate frames and genuinely
    wants thinning. A **keyframe capture** is a couple of dozen deliberately
    spaced shots where every one is load-bearing — at ``every=4`` a 27-keyframe
    iPad capture trained on 7 images, discarding 3 of every 4 views of a scene
    that was already too sparse.

    Thinning is left entirely to the ``max_frames`` step below because it
    spreads its picks evenly across the whole capture and spends the full
    budget, where an integer stride quantises and undershoots: 118 frames at
    stride 2 yields 59 images when 100 were allowed.
    """
    s = load_session(session_dir)
    stride = every if every and every > 0 else 1
    idxs = list(range(0, s.n_frames, stride))
    if len(idxs) > max_frames:
        sel = np.linspace(0, len(idxs) - 1, max_frames).astype(int)
        idxs = [idxs[i] for i in sel]

    img_dir = data_root / "images"
    sparse = data_root / "sparse" / "0"
    shutil.rmtree(data_root, ignore_errors=True)
    img_dir.mkdir(parents=True)
    sparse.mkdir(parents=True)

    # Prefer the calibration emitted for the exact high-resolution RGB image.
    # Scaling depth-space K is only a compatibility fallback for older captures.
    fr0 = s.frames[idxs[0]]
    color0 = Image.open(fr0.color_path)
    W, H = color0.size
    depth0 = np.array(Image.open(fr0.depth_path))
    dH, dW = depth0.shape[:2]
    if fr0.color_intrinsics is not None:
        fx, fy, cx, cy = fr0.color_intrinsics
        intrinsics_source = "K_color"
    else:
        fx_d, fy_d, cx_d, cy_d = fr0.intrinsics
        sx, sy = W / dW, H / dH
        fx, fy, cx, cy = fx_d * sx, fy_d * sy, cx_d * sx, cy_d * sy
        intrinsics_source = "scaled_depth_fallback"

    with open(sparse / "cameras.txt", "w") as fh:
        fh.write("# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n")
        fh.write(f"1 PINHOLE {W} {H} {fx} {fy} {cx} {cy}\n")

    points: list[tuple] = []  # (x,y,z,r,g,b)
    images_lines: list[str] = []

    for img_id, i in enumerate(idxs, start=1):
        fr = s.frames[i]
        name = f"{img_id:05d}.jpg"
        dst = img_dir / name
        try:
            os.symlink(os.path.abspath(fr.color_path), dst)
        except OSError:
            shutil.copy2(fr.color_path, dst)

        c2w = np.asarray(fr.pose, dtype=np.float64).reshape(4, 4)
        w2c = np.linalg.inv(c2w)
        R, t = w2c[:3, :3], w2c[:3, 3]
        q = _rot_to_quat_wxyz(R)
        images_lines.append(
            f"{img_id} {q[0]} {q[1]} {q[2]} {q[3]} "
            f"{t[0]} {t[1]} {t[2]} 1 {name}\n"
        )
        images_lines.append("\n")

        # seed points from depth (camera → world)
        if len(points) < 80_000 and (img_id % 3 == 1):
            d = np.array(Image.open(fr.depth_path)).astype(np.float32) * 0.001
            col = np.asarray(Image.open(fr.color_path).convert("RGB").resize((dW, dH)))
            us = np.arange(0, dW, seed_stride)
            vs = np.arange(0, dH, seed_stride)
            uu, vv = np.meshgrid(us, vs)
            z = d[vv, uu]
            mask = z > 0.05
            if mask.any():
                fx_d, fy_d, cx_d, cy_d = fr.intrinsics
                x = (uu[mask] - cx_d) * z[mask] / fx_d
                y = (vv[mask] - cy_d) * z[mask] / fy_d
                xyz_c = np.stack([x, y, z[mask]], axis=1)  # camera
                ones = np.ones((len(xyz_c), 1))
                xyz_h = np.concatenate([xyz_c, ones], 1)
                xyz_w = (c2w @ xyz_h.T).T[:, :3]
                rgb = col[vv[mask], uu[mask]]
                for p, c in zip(xyz_w, rgb):
                    points.append((float(p[0]), float(p[1]), float(p[2]),
                                   int(c[0]), int(c[1]), int(c[2])))

    with open(sparse / "images.txt", "w") as fh:
        fh.write("# IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n")
        fh.write("".join(images_lines))

    # subsample points if huge
    if len(points) > 60_000:
        rng = np.random.default_rng(0)
        pick = rng.choice(len(points), 60_000, replace=False)
        points = [points[i] for i in pick]

    with open(sparse / "points3D.txt", "w") as fh:
        fh.write("# POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[]\n")
        for pid, (x, y, z, r, g, b) in enumerate(points, start=1):
            fh.write(f"{pid} {x} {y} {z} {r} {g} {b} 0\n")

    meta = {
        "n_images": len(idxs),
        "n_session_frames": s.n_frames,
        # How many views the capture had vs how many were trained on. A run that
        # quietly dropped 3 of every 4 frames used to be invisible here.
        "stride": stride,
        "stride_source": "explicit" if (every and every > 0) else "auto",
        "n_points": len(points),
        "color_wh": [W, H],
        "intrinsics_color": [fx, fy, cx, cy],
        "intrinsics_source": intrinsics_source,
        "frame_indices": idxs,
    }
    (data_root / "export_meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def run_3dgut(
    data_root: Path,
    out_dir: Path,
    iterations: int,
    export_usdz: bool,
    *,
    max_gaussians: int = 1_000_000,
    scale_factor: float = 0.1,
    downsample_factor: int = 1,
    gui: bool = False,
) -> Path:
    py = _find_3dgrut_python()
    root = _find_3dgrut_root(py)
    if not py or not root:
        raise SystemExit("3dgrut env/repo not found (need conda env 3dgrut + ~/3dgrut)")

    runs = out_dir / "3dgut_runs"
    runs.mkdir(parents=True, exist_ok=True)
    data_abs = str(data_root.resolve())
    runs_abs = str(runs.resolve())

    def hp(p: str) -> str:
        return "'" + p.replace("'", "\\'") + "'"

    cmd = [
        py, "train.py",
        "--config-name=apps/colmap_3dgut_mcmc.yaml",
        f"path={hp(data_abs)}",
        f"out_dir={hp(runs_abs)}",
        "experiment_name=cratescan_3dgut",
        f"n_iterations={iterations}",
        # GUI + full-res (12MP) can OOM with many dataloader workers.
        f"num_workers={0 if gui else 4}",
        "val_frequency=999999",
        f"strategy.add.max_n_gaussians={max_gaussians}",
        f"model.default_scale_factor={scale_factor}",
        f"dataset.downsample_factor={downsample_factor}",
        "export_ply.enabled=true",
        "test_last=false",
        f"export_usd.enabled={'true' if export_usdz else 'false'}",
    ]
    if gui:
        # 3dgrut's polyscope window — live splat + training stats. Needs an X
        # display; the env copy below keeps DISPLAY/XAUTHORITY intact.
        cmd.append("with_gui=true")
    env = os.environ.copy()
    # assetpipe / ROS often inject torch+CUDA libs into LD_LIBRARY_PATH; that
    # breaks the separate 3dgrut env's torch (mismatched _C extension).
    for k in ("LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME"):
        env.pop(k, None)
    env["PATH"] = os.path.dirname(py) + os.pathsep + env.get("PATH", "")
    print(">>", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=root, check=True, env=env)

    import glob
    plys = sorted(glob.glob(str(runs / "**" / "export_last.ply"), recursive=True))
    if not plys:
        plys = sorted(glob.glob(str(runs / "**" / "*.ply"), recursive=True))
    if not plys:
        raise SystemExit(f"no PLY exported under {runs}")
    dst = out_dir / "scene_gaussians.ply"
    shutil.copy2(plys[-1], dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session")
    ap.add_argument("--out", required=True)
    ap.add_argument("--every", type=int, default=0,
                    help="keep every Nth frame; 0 = auto, which keeps every "
                         "frame unless the capture has more than --max-frames "
                         "(i.e. only thins video-density sweeps)")
    ap.add_argument("--max-frames", type=int, default=100)
    ap.add_argument("--iterations", type=int, default=5000,
                    help="3DGUT steps (full quality ~30000; smoke ~5000)")
    ap.add_argument("--max-gaussians", type=int, default=1_000_000,
                    help="maximum adaptive Gaussian count")
    ap.add_argument("--scale-factor", type=float, default=0.1,
                    help="initial Gaussian scale relative to point spacing")
    ap.add_argument("--downsample-factor", type=int, default=1,
                    help="image downsampling used during training")
    ap.add_argument("--export-usdz", action="store_true")
    ap.add_argument("--gui", action="store_true",
                    help="open 3dgrut's live polyscope viewer while training")
    ap.add_argument("--colmap-only", action="store_true",
                    help="only write COLMAP dataset, skip train")
    ap.add_argument("--curate", action="store_true",
                    help="critically refine frames first (blur/dupe reject + diversity)")
    ap.add_argument("--curate-target", type=int, default=64,
                    help="max keyframes when --curate (default 64)")
    args = ap.parse_args()

    session_path = args.session
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.curate:
        from assetpipe.scene.rgbd_curate import curate_session

        curated = out / "curated_session"
        print(f"→ curate RGB-D → {curated}", flush=True)
        report = curate_session(
            args.session, str(curated), target=args.curate_target,
        )
        print(
            f"   kept {report['n_kept']}/{report['n_source']} "
            f"rejects={report.get('reject_counts')}",
            flush=True,
        )
        session_path = report["curated_session"]
        # curated session is already thinned — take all (every=1)
        args.every = 1
        args.max_frames = max(args.max_frames, report["n_kept"])

    data = out / "3dgrut_data"
    print(f"→ COLMAP from ARKit poses → {data}", flush=True)
    meta = write_colmap_from_session(
        session_path, data, every=args.every, max_frames=args.max_frames,
    )
    print(json.dumps(meta, indent=2), flush=True)
    if args.colmap_only:
        return 0
    ply = run_3dgut(
        data,
        out,
        args.iterations,
        args.export_usdz,
        max_gaussians=args.max_gaussians,
        scale_factor=args.scale_factor,
        downsample_factor=args.downsample_factor,
        gui=args.gui,
    )
    print(f"✔ 3DGUT gaussians → {ply}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
