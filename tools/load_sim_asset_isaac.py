#!/usr/bin/env python3
"""Spawn an assetpipe sim-export USD in Isaac Lab for a quick smoke test.

Requires Isaac Lab >= 3.0 (Isaac Sim 6.0, py3.12 `isaac60` env). Usage:

    ~/IsaacLab3/isaaclab.sh -p tools/load_sim_asset_isaac.py \\
        --usd /home/jaeahn-jammy/3dasset/sim_out/DC22F084_couch/couch.usda \\
        --steps 120

Opens a ground plane + the USD (raised slightly so it can settle). Headless
is the default in Isaac Lab 3.0; pass ``--viz kit`` for the Isaac GUI to
orbit and use the measure tools (``--headless`` is deprecated upstream).
Physics runs on the default PhysX backend; Newton is opt-in via
``isaaclab_newton`` (still beta, unused by upstream demos as of 3.0.0-beta2).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--usd",
    type=Path,
    default=Path(__file__).resolve().parents[1] / "sim_out/DC22F084_couch/couch.usda",
    help="path to sim-export .usda / .usd",
)
parser.add_argument("--steps", type=int, default=120,
                    help="physics steps before exit (0 = run until window closed)")
parser.add_argument("--lift", type=float, default=0.05,
                    help="extra Z lift above ground (m)")
AppLauncher.add_app_launcher_args(parser)
args_cli = parser.parse_args()

app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

import isaaclab.sim as sim_utils  # noqa: E402


def _extent_z(usd_path: Path) -> float:
    meta = usd_path.parent / "sim_export.json"
    if meta.is_file():
        try:
            ext = json.loads(meta.read_text()).get("extent_m") or [1, 1, 1]
            return float(ext[2])
        except Exception:
            pass
    return 1.0


def design_scene(usd_path: Path, lift: float) -> float:
    # Prefer a local cuboid ground so headless runs do not hang waiting on
    # Nucleus/S3 for Isaac's default_environment.usd.
    cfg_box = sim_utils.MeshCuboidCfg(
        size=(20.0, 20.0, 0.05),
        rigid_props=sim_utils.RigidBodyPropertiesCfg(kinematic_enabled=True),
        collision_props=sim_utils.CollisionPropertiesCfg(),
        visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.35, 0.35, 0.35)),
    )
    cfg_box.func("/World/defaultGroundPlane", cfg_box, translation=(0.0, 0.0, -0.025))

    cfg_light = sim_utils.DistantLightCfg(intensity=3000.0, color=(0.8, 0.8, 0.8))
    cfg_light.func("/World/lightDistant", cfg_light, translation=(1.0, 0.0, 10.0))

    sim_utils.create_prim("/World/Objects", "Xform")

    height = _extent_z(usd_path)
    z = 0.5 * height + lift
    cfg = sim_utils.UsdFileCfg(usd_path=str(usd_path.resolve()))
    cfg.func("/World/Objects/Asset", cfg, translation=(0.0, 0.0, z))
    print(f"[INFO] spawned {usd_path} at z={z:.3f} m (extent_z≈{height:.3f})", flush=True)
    return height


def main() -> int:
    usd_path = args_cli.usd.expanduser().resolve()
    if not usd_path.is_file():
        print(f"[ERROR] USD not found: {usd_path}", file=sys.stderr, flush=True)
        return 1

    print(f"[INFO] opening Isaac Lab with {usd_path}", flush=True)
    sim_cfg = sim_utils.SimulationCfg(dt=0.01, device=args_cli.device)
    sim = sim_utils.SimulationContext(sim_cfg)
    height = design_scene(usd_path, args_cli.lift)
    # Camera looking at the asset
    eye = [height * 2.2, -height * 2.0, height * 1.6]
    target = [0.0, 0.0, height * 0.4]
    sim.set_camera_view(eye, target)

    print("[INFO] sim.reset()…", flush=True)
    sim.reset()
    print("[INFO] Setup complete — stepping physics…", flush=True)

    n = 0
    while simulation_app.is_running():
        sim.step()
        n += 1
        if n == 1 or n % 20 == 0:
            print(f"[INFO] step {n}", flush=True)
        if args_cli.steps > 0 and n >= args_cli.steps:
            print(f"[INFO] finished {n} steps OK", flush=True)
            break
    return 0


if __name__ == "__main__":
    code = main()
    simulation_app.close()
    raise SystemExit(code)
