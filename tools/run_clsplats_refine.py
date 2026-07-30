#!/usr/bin/env python3
"""Run CL-Splats continual refine on an existing 3DGS PLY + dual-timestep COLMAP set.

Stock CL-Splats rebuilds Gaussians from COLMAP points. This wrapper:
  1) loads your trained ``export_last.ply`` / ``scene_gaussians.ply``
  2) skips day_0 optimisation
  3) runs the paper CL phase on day_1 (change-detect → local densify/optimize)

Example:
  python tools/prepare_clsplats_dataset.py \\
    --src demo_out/CrateScan-9482A1A0_3dgut_full30k_gui/3dgrut_data \\
    --out demo_out/CrateScan-9482A1A0_clsplats/data

  python tools/run_clsplats_refine.py \\
    --data demo_out/CrateScan-9482A1A0_clsplats/data \\
    --ply demo_out/CrateScan-9482A1A0_3dgut_full30k_gui/3dgut_runs/.../export_last.ply \\
    --out demo_out/CrateScan-9482A1A0_clsplats/run \\
    --iters 200 --max-gaussians 300000
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import torch
from loguru import logger
from plyfile import PlyData


def _load_gs_ply(path: Path, device: torch.device, max_gaussians: int | None):
    """Load a standard 3DGS PLY into CL-Splats GaussianParams tensors."""
    from clsplats.representation.cl_gaussians import GaussianParams

    ply = PlyData.read(str(path))
    v = ply["vertex"]
    n = len(v.data)
    idx = np.arange(n)
    if max_gaussians is not None and n > max_gaussians:
        rng = np.random.default_rng(0)
        idx = np.sort(rng.choice(n, size=max_gaussians, replace=False))
        logger.warning("Subsampling PLY {n} → {m} Gaussians for VRAM", n=n, m=max_gaussians)

    xyz = np.stack([v["x"], v["y"], v["z"]], axis=1)[idx].astype(np.float32)

    # Activated opacity / scale are stored as logit / log in 3DGS PLYs.
    opac = np.asarray(v["opacity"], dtype=np.float32)[idx]
    # PLY stores logit(opacity); CLGaussians expects activated opacity in (0,1)
    # then re-applies inverse_sigmoid. Convert logit → sigmoid.
    opac_act = 1.0 / (1.0 + np.exp(-np.clip(opac, -20, 20)))
    opac_act = opac_act.reshape(-1, 1)

    scales = np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], axis=1)[idx].astype(
        np.float32
    )
    scales_act = np.exp(np.clip(scales, -20, 20))

    quats = np.stack(
        [v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], axis=1
    )[idx].astype(np.float32)

    # SH: f_dc_* + f_rest_*  (stored as separate floats, 3DGS layout)
    f_dc = np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], axis=1)[idx].astype(
        np.float32
    )  # (N, 3)
    rest_names = [name for name in v.data.dtype.names if name.startswith("f_rest_")]
    rest_names.sort(key=lambda s: int(s.split("_")[-1]))
    if rest_names:
        f_rest = np.stack([np.asarray(v[name], dtype=np.float32)[idx] for name in rest_names], axis=1)
        # f_rest is (N, 45) for degree 3 = 15 coeffs × 3 channels, channel-major in 3DGS
        n_rest = f_rest.shape[1] // 3
        f_rest = f_rest.reshape(-1, 3, n_rest)  # (N, 3, 15)
        sh = np.concatenate([f_dc[:, :, None], f_rest], axis=2)  # (N, 3, 16)
        sh_degree = int(round(math.sqrt(sh.shape[2]) - 1))
    else:
        sh = f_dc[:, :, None]
        sh_degree = 0
        # If somehow DC missing semantics, fall back to RGB2SH zeros — keep f_dc as-is.

    params = GaussianParams(
        positions=torch.from_numpy(xyz).to(device),
        scales=torch.from_numpy(scales_act).to(device),
        quats=torch.from_numpy(quats).to(device),
        sh_features=torch.from_numpy(sh).to(device),
        opacity=torch.from_numpy(opac_act).to(device),
    )
    return params, sh_degree


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True, help="CL-Splats COLMAP dataset root")
    ap.add_argument("--ply", type=Path, required=True, help="Existing trained Gaussian PLY")
    ap.add_argument("--out", type=Path, required=True, help="Output directory")
    ap.add_argument("--iters", type=int, default=200, help="CL-phase iterations (day_1)")
    ap.add_argument(
        "--max-gaussians",
        type=int,
        default=300_000,
        help="Subsample PLY to this many Gaussians (VRAM); 0 = keep all",
    )
    ap.add_argument(
        "--change-threshold",
        type=float,
        default=0.75,
        help="DINOv2 cosine threshold (lower = more sensitive / larger active set)",
    )
    args = ap.parse_args()

    # Ensure local clone is importable even if editable install is stale
    cl_root = Path.home() / "cl-splats"
    if cl_root.is_dir():
        sys.path.insert(0, str(cl_root))

    from clsplats.config import CLSplatsConfig
    from clsplats.dataset.dataset_reader import readColmapSceneInfo
    from clsplats.representation.cl_gaussians import CLGaussians
    from clsplats.trainer import CLSplatsTrainer

    args.out.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        raise SystemExit("CUDA required for CL-Splats")

    max_g = None if args.max_gaussians <= 0 else args.max_gaussians
    ply_params, sh_degree = _load_gs_ply(args.ply.resolve(), device, max_g)

    cfg = CLSplatsConfig()
    cfg.data_path = str(args.data.resolve())
    cfg.images = "images"
    cfg.eval = False
    cfg.wandb_mode = "disabled"
    cfg.model.sh_degree = sh_degree
    cfg.train.num_times = 2
    cfg.train.start_time = 0
    cfg.train.iters_per_timestep = args.iters
    cfg.train.log_interval = max(1, args.iters // 10)
    # CL phase densification windows relative to short refine runs
    cfg.train.densify_from_iter = min(50, max(10, args.iters // 4))
    cfg.train.densify_until_iter = args.iters
    cfg.train.densification_interval = 50
    cfg.change.threshold = args.change_threshold
    cfg.history.log_history = True

    scene = readColmapSceneInfo(
        path=cfg.data_path,
        images=cfg.images,
        eval=False,
        train_test_exp=False,
    )
    n_t0 = sum(1 for c in scene.train_cameras if c.timestep == 0)
    n_t1 = sum(1 for c in scene.train_cameras if c.timestep == 1)
    logger.info("Cameras: day_0={a} day_1={b}  SH degree={d}", a=n_t0, b=n_t1, d=sh_degree)
    if n_t1 == 0:
        raise SystemExit("No day_1_* images — run prepare_clsplats_dataset.py first")

    trainer = CLSplatsTrainer(cfg, scene)

    # Replace COLMAP-init Gaussians with the trained PLY
    trainer.gaussians = CLGaussians(cfg, ply_params, spatial_lr_scale=trainer.scene_extent)
    trainer.gaussians.initialize_strategy_state(trainer.scene_extent)
    trainer.gaussians.active_sh_degree = sh_degree
    trainer.gaussians.max_sh_degree = sh_degree
    logger.info(
        "Loaded PLY → {n} Gaussians (extent={e:.3f})",
        n=trainer.gaussians.num_gaussians,
        e=trainer.scene_extent,
    )

    # day_0: establish "already reconstructed" state without re-optimising
    logger.info("prepare_timestep(0) — skip train (using imported PLY)")
    trainer.prepare_timestep(0)

    # day_1: paper CL phase — detect disagreement vs GT, locally optimise
    logger.info("prepare_timestep(1) — CL change-detect + local refine")
    trainer.prepare_timestep(1)
    if trainer.active_mask is not None:
        n_active = int(trainer.active_mask.sum().item())
        logger.info(
            "Active Gaussians for refine: {a}/{t} ({p:.1f}%)",
            a=n_active,
            t=int(trainer.active_mask.numel()),
            p=100.0 * n_active / max(1, int(trainer.active_mask.numel())),
        )
        if n_active == 0:
            logger.error(
                "Change detector activated 0 Gaussians. "
                "Try --change-threshold 0.6 (more sensitive) or check pose/image alignment."
            )
            return 2

    trainer.train()

    # Export
    out_ply = args.out / "gaussians_clsplats_refined.ply"
    trainer.gaussians.export_ply(str(out_ply))
    if cfg.history.log_history:
        hist_dir = args.out / "history"
        hist_dir.mkdir(exist_ok=True)
        trainer._history.save(str(hist_dir))
    logger.info("Wrote {p}", p=out_ply)
    print(f"✔ refined PLY: {out_ply}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
