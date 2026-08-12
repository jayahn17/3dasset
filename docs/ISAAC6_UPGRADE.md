# Isaac Sim 6.0 / Isaac Lab 3.0 upgrade (2026-07-30)

Upgrade of this box's Isaac stack from Isaac Sim 4.5 / Isaac Lab 2.1.1 to
**Isaac Sim 6.0.1 + Isaac Lab 3.0.0-beta2.patch1** — the pairing that ships
the Newton physics backend our sim-export layer targets (see
`docs/OBJECT_ASSET_PLAN.md` Phase 3.5; Newton ingests exactly the
USD/URDF/MJCF that `assetpipe/digitalize/sim_export.py` emits).

## State before (2026-07-30)

| Where | What | Version |
|---|---|---|
| `~/isaacsim` | binary workstation install | 4.5.0 (rc.36 release build) |
| `~/IsaacLab` | git checkout, **has local uncommitted work** | v2.1.1 |
| 9 conda envs (py3.10) | pip `isaacsim` | 4.5.0.0 |
| `env_isaaclab`, `leisaac` | **editable** isaaclab → `~/IsaacLab/source/isaaclab` | 0.41.3 (= Lab 2.1.1) |
| `isaac45`, `khameleon_sim` | editable isaaclab → khameleon's own vendored IsaacLab | 0.36.6 |

## Design decisions (why it looks the way it does)

- **Isaac Sim 6.x requires Python 3.12** (5.x was 3.11, 4.5 was 3.10). An
  in-place upgrade of any existing env is impossible → new env **`isaac60`**.
  Side effect: every legacy env keeps working untouched.
- **`~/IsaacLab` must not move.** `env_isaaclab` and `leisaac` import isaaclab
  *editable* from that tree; checking out v3.0 there would break both
  instantly. Instead: `git worktree add ~/IsaacLab3 v3.0.0-beta2.patch1` —
  same repo objects, separate checkout. Never `git checkout` a different
  version inside `~/IsaacLab`.
- **leisaac stays on 4.5/2.1.1** — upstream doesn't support Lab 3.0 yet.
- `~/isaac_ws`, `~/isaac_ur5_ws` are pure ROS 2 workspaces (no Python-level
  isaacsim/isaaclab imports); they talk over the ROS 2 bridge, which 6.0
  still ships. No changes needed.

## The new stack

```bash
# env: conda create -y -n isaac60 python=3.12
conda activate isaac60
pip install --no-cache-dir "isaacsim[all,extscache]==6.0.1.0" \
    --extra-index-url https://pypi.nvidia.com     # ~30 GB, GLIBC 2.35+ ok
~/IsaacLab3/isaaclab.sh -i                        # installs isaaclab editable into the active env
```

Smoke test (asset settle check, headless by default in Lab 3.0):

```bash
~/IsaacLab3/isaaclab.sh -p tools/load_sim_asset_isaac.py \
    --usd sim_out/DC22F084_couch/couch.usda --steps 120
# add --viz kit for the GUI + measure tools
```

## Lab 2.1 → 3.0 breaking changes that matter here

- Quaternions are now **XYZW** (was WXYZ) — audit any code passing quats.
- `.data.*` properties return `wp.array` (Warp), not `torch.Tensor`.
- `--headless` deprecated: headless is the default; `--viz kit|newton|rerun|viser` selects visualizers.
- `isaacsim.core.utils.prims` no longer needed for prim creation —
  `isaaclab.sim.create_prim` is backend-neutral.
- Physics is multi-backend (factory): default PhysX; Newton opt-in via
  `isaaclab_newton` (`NewtonCfg`/solver cfgs — still beta, unused by
  upstream demos as of 3.0.0-beta2.patch1).
- `RigidBodyPropertiesCfg`, `CollisionPropertiesCfg`, `MeshCuboidCfg`,
  `UsdFileCfg`, `SimulationCfg(dt=, device=)`, `SimulationContext`
  all survive re-exported from `isaaclab.sim`.

## Repo changes

- `tools/load_sim_asset_isaac.py`: dropped `isaacsim.core.utils.prims`
  import → `sim_utils.create_prim`; docstring now points at
  `~/IsaacLab3/isaaclab.sh`, headless-default semantics. No other repo code
  touches Isaac APIs (docs mention it generically only).

## Status — COMPLETE (2026-07-30)

- [x] `isaac60` env created (py3.12)
- [x] `~/IsaacLab3` worktree @ v3.0.0-beta2.patch1
- [x] repo code updated + other repos audited
- [x] `pip install isaacsim…` (interrupted once by a session restart —
      re-running the same pip command over the partial install worked)
- [x] `~/IsaacLab3/isaaclab.sh -i` — torch 2.11.0+cu130, CUDA OK
- [x] smoke test: couch.usda spawned at z=0.79 m, 120 physics steps clean

First-run gotcha: headless kit hits an interactive EULA prompt and dies with
"EOF when reading a line" — set `OMNI_KIT_ACCEPT_EULA=YES` for any
non-interactive/cron invocation of the isaac60 stack.

Disk note: the env is ~25 GB; box landed at ~28 GB free. Reclaim from other
envs' pip caches if needed.
