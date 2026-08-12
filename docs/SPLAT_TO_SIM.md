# Splat → simulation (USD / MJCF / URDF / PLY)

A trained splat is not a simulable body: no surface, no mass, no collider.
Two steps get there.

```bash
conda activate assetpipe

# 1. splat → levelled metric mesh (see ONSHAPE.md for picking --crop)
python tools/splat_to_cad.py demo_out/CrateScan-DC22F084_3dgut_fullres/scene_gaussians.ply \
    --out cad_out/couch --name couch --crop=-4.75,4.90,0.02,-3.45,7.05,1.00

# 2. mesh → sim asset (CoACD colliders + mass/inertia + USD/MJCF/URDF)
python -m assetpipe sim-export cad_out/couch/couch_m.ply \
    --out sim_out/DC22F084_couch_splat \
    --units meters --up z --material upholstery --label couch
```

**`--up z` is not optional here.** `sim-export` defaults to `--up y` because
GLB/OBJ from a scan or a generator are Y-up. `splat_to_cad.py` output is
already levelled with its base on `z = 0`; rotating it again lays the prop on
its back.

## What step 1 hands over

| File | Units | Frame |
|------|-------|-------|
| `<name>_m.ply` | **m** | Z-up, base at z=0 — feed this to `sim-export` |
| `<name>_m.obj` | m | same, vertex colours |
| `<name>.glb` | m | same, for viewers |
| `<name>_cloud_m.ply` | m | the isolated point cloud, for perception |
| `<name>_mm.stl` / `.obj` | mm | CAD — see [ONSHAPE.md](ONSHAPE.md) |

## What step 2 produces

`couch.usda` (Z-up, 1 m/unit, `UsdPhysics` RigidBody + MassAPI + 24 convexHull
colliders, per-vertex `displayColor`), `couch.xml` (MuJoCo, freejoint,
principal inertia, visual group 2 / collision group 3), `couch.urdf`,
`couch_visual.obj|glb`, `collision_NN.obj` ×24, `sim_export.json`,
`preview.png`.

Verified: MuJoCo compiles it and it settles at rest (‖v‖ = 0, 3 contacts, 6 s).

## Mass

Vision cannot weigh things. `--material upholstery` is a 60 kg/m³ *apparent*
density over the mesh volume — a couch is foam and air, so the solid-wood
prior that produced 375 kg was nonsense. It yields **86 kg**, in range for a
3-seat leather sofa. Pass `--mass 72` the moment you have a real scale.

Other priors: `cardboard` 120, `plastic` 400, `wood` 500, `metal` 2700,
`generic` 300 kg/m³. All ±2×.

## Fidelity, honestly

The visual mesh is Poisson over splat centers, whose gaussians sit in a noisy
shell a few cm thick. It reads as melted upholstery up close, and no amount of
export-format work changes that — the mesh is the limit.

For simulation this matters less than it looks: contact behaviour comes from
the 24 convex hulls and the inertia tensor, both of which are sound. If you
need photoreal appearance too, the standard split is **render from the splat,
collide with the mesh** — keep `scene_gaussians.ply` in the 3DGUT/gsplat
renderer and use this asset purely as the physics body. A mesh good enough to
look at on its own needs a different capture (denser, slower orbit, full
coverage of the ends), not a different exporter.
