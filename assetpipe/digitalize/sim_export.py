"""Scan asset → simulation-ready export: SimReady-style USD + URDF + MJCF.

Turns a reconstructed mesh (TRELLIS GLB, CrateScanner OBJ, fused PLY) into a
rigid-prop asset a simulator can actually use — the post-mesh layer that
separates a viewer asset from a Lightwheel-class sim asset:

    * Z-up, base-at-origin pivot, metric meters (SimReady conventions)
    * CoACD convex decomposition -> collision meshes
    * true mass / center-of-mass / inertia tensor from mesh volume x density
      (trimesh), not bounding-box approximations
    * URDF (single link), MJCF (MuJoCo), USD with UsdPhysics APIs targeting
      the SimReady Foundation Prop-Robotics-Neutral profile

Mass honesty: vision cannot weigh objects. ``density`` priors are +-2x at
best; pass ``mass_kg`` from a real scale whenever you have one.

IMPORT ORDER WARNING: usd-core (pxr) MUST be imported before coacd in the
same process — the reverse order segfaults (native lib clash, observed with
coacd 1.x + usd-core 26.x on linux). This module imports pxr at module load;
keep it that way.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

try:  # noqa: SIM105 — pxr first; see module docstring
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade  # noqa: F401
    _HAVE_USD = True
except ImportError:  # pragma: no cover
    _HAVE_USD = False

INCHES_PER_METER = 39.37007874015748

# Density priors (kg/m^3) for common scanned-prop material classes.
# These are *apparent* densities over the mesh's own volume, not material
# densities: upholstery is mostly foam and air, so a couch that would weigh
# 375 kg as solid wood really weighs ~70.
DENSITY_PRIORS = {
    "cardboard": 120.0,
    "wood": 500.0,
    "plastic": 400.0,
    "metal": 2700.0,
    "upholstery": 60.0,
    "generic": 300.0,
}


def _load_mesh(path: str):
    import trimesh

    mesh = trimesh.load(path, force="mesh")
    if mesh.is_empty or len(mesh.faces) == 0:
        raise ValueError(f"no triangles in {path}")
    return mesh


def _guess_unit_scale(mesh, units: str) -> tuple[float, str]:
    """Return (scale_to_meters, resolved_units)."""
    if units == "meters":
        return 1.0, "meters"
    if units == "inches":
        return 1.0 / INCHES_PER_METER, "inches"
    if units in ("mm", "millimeters"):
        return 0.001, "millimeters"
    span = float(mesh.extents.max())
    # CrateScanner mesh.obj bakes inches (span >> 5); CAD exports bake mm
    # (span >> 100 — 2177 is a 2.2 m couch, never a 55 m one); metric assets
    # are a few m.
    if span > 100.0:
        return 0.001, "millimeters(auto)"
    if span > 5.0:
        return 1.0 / INCHES_PER_METER, "inches(auto)"
    return 1.0, "meters(auto)"


def sim_export(
    mesh_path: str,
    out_dir: str,
    *,
    units: str = "auto",
    up: str = "y",
    density: Optional[float] = None,
    material: str = "generic",
    mass_kg: Optional[float] = None,
    target_size_m: Optional[float] = None,
    label: Optional[str] = None,
    qcode: Optional[str] = None,
    friction: tuple[float, float] = (0.6, 0.5),
    max_hulls: int = 24,
    coacd_threshold: float = 0.05,
) -> dict[str, Any]:
    """Export ``mesh_path`` as sim-ready USD + URDF + MJCF into ``out_dir``.

    ``target_size_m``: uniform-rescale so the largest axis equals this, for
    normalized (generative) meshes; use measure.py dims as the source.

    ``up``: axis the source mesh stands on. GLB/OBJ from a scan or generator
    are Y-up (the default). Anything already levelled to a floor — the CAD
    export from ``tools/splat_to_cad.py`` — is ``"z"``, and rotating it again
    would lay the prop on its back.
    """
    import numpy as np
    import trimesh

    os.makedirs(out_dir, exist_ok=True)
    name = label or os.path.splitext(os.path.basename(mesh_path))[0]
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in name).lower()

    mesh = _load_mesh(mesh_path)
    unit_scale, units_resolved = _guess_unit_scale(mesh, units)
    mesh.apply_scale(unit_scale)
    if target_size_m:
        mesh.apply_scale(float(target_size_m) / float(mesh.extents.max()))

    # GLB/OBJ are Y-up; sim (REP-103 / SimReady) wants Z-up, base pivot at 0.
    if up.lower() == "y":
        mesh.apply_transform(
            trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    elif up.lower() != "z":
        raise ValueError(f"up must be 'y' or 'z', got {up!r}")
    mn, mx = mesh.bounds
    mesh.apply_translation([-(mn[0] + mx[0]) / 2, -(mn[1] + mx[1]) / 2, -mn[2]])

    # ---- physics: mass / COM / inertia from real volume ----
    watertight = bool(mesh.is_watertight)
    solid = mesh if watertight else mesh.convex_hull
    volume = float(solid.volume)
    if mass_kg is not None:
        rho = float(mass_kg) / max(volume, 1e-9)
    else:
        rho = float(density if density is not None
                    else DENSITY_PRIORS.get(material, 300.0))
    solid.density = rho
    props_mass = float(solid.mass)
    com = [float(x) for x in solid.center_mass]
    inertia = np.asarray(solid.moment_inertia, dtype=float)  # about COM

    # ---- collision: CoACD convex decomposition ----
    import coacd  # AFTER pxr — see module docstring

    cm = coacd.Mesh(np.asarray(mesh.vertices, np.float64),
                    np.asarray(mesh.faces, np.int64))
    hulls_raw = coacd.run_coacd(cm, threshold=coacd_threshold,
                                max_convex_hull=max_hulls)
    hull_paths: list[str] = []
    hulls = []
    for i, (v, f) in enumerate(hulls_raw):
        h = trimesh.Trimesh(vertices=v, faces=f)
        p = os.path.join(out_dir, f"collision_{i:02d}.obj")
        h.export(p)
        hulls.append(h)
        hull_paths.append(p)

    visual_obj = os.path.join(out_dir, f"{safe}_visual.obj")
    mesh.export(visual_obj)
    visual_glb = os.path.join(out_dir, f"{safe}_visual.glb")
    mesh.export(visual_glb)

    ext = [float(x) for x in mesh.extents]
    report: dict[str, Any] = {
        "asset": safe,
        "source": os.path.abspath(mesh_path),
        "units_in": units_resolved,
        "up_in": up.lower(),
        "extent_m": [round(x, 4) for x in ext],
        "watertight": watertight,
        "mass_source": ("measured" if mass_kg is not None else
                        f"density prior {rho:g} kg/m3 ({material}) — ±2x, "
                        "prefer a real scale"),
        "mass_kg": round(props_mass, 4),
        "volume_m3": round(volume, 6),
        "center_of_mass_m": [round(x, 4) for x in com],
        "n_collision_hulls": len(hull_paths),
        "friction": {"static": friction[0], "dynamic": friction[1]},
    }

    report["urdf"] = _write_urdf(out_dir, safe, visual_obj, hull_paths,
                                 props_mass, com, inertia)
    report["mjcf"] = _write_mjcf(out_dir, safe, visual_obj, hull_paths,
                                 props_mass, com, inertia, friction,
                                 rgb=_mean_color(mesh))
    if _HAVE_USD:
        report["usd"] = _write_usd(out_dir, safe, mesh, hulls, props_mass,
                                   com, inertia, friction, rho,
                                   label=label or safe, qcode=qcode)
    else:  # pragma: no cover
        report["usd_error"] = "usd-core not installed (pip install usd-core)"

    with open(os.path.join(out_dir, "sim_export.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    return report


def _write_urdf(out_dir, name, visual_obj, hull_paths, mass, com, I) -> str:
    vis = os.path.basename(visual_obj)
    colls = "\n".join(
        f'    <collision><geometry><mesh filename="{os.path.basename(p)}"/>'
        f"</geometry></collision>" for p in hull_paths)
    urdf = f"""<?xml version="1.0"?>
<robot name="{name}">
  <link name="{name}_link">
    <inertial>
      <origin xyz="{com[0]:.6f} {com[1]:.6f} {com[2]:.6f}" rpy="0 0 0"/>
      <mass value="{mass:.6f}"/>
      <inertia ixx="{I[0,0]:.8f}" ixy="{I[0,1]:.8f}" ixz="{I[0,2]:.8f}"
               iyy="{I[1,1]:.8f}" iyz="{I[1,2]:.8f}" izz="{I[2,2]:.8f}"/>
    </inertial>
    <visual><geometry><mesh filename="{vis}"/></geometry></visual>
{colls}
  </link>
</robot>
"""
    path = os.path.join(out_dir, f"{name}.urdf")
    with open(path, "w") as fh:
        fh.write(urdf)
    return path


def _mean_color(mesh) -> tuple[float, float, float]:
    """Average vertex colour as 0-1 RGB, mid-grey when the mesh has none."""
    import numpy as np

    visual = getattr(mesh, "visual", None)
    vcolors = getattr(visual, "vertex_colors", None)
    if vcolors is None or not len(vcolors):
        return (0.6, 0.6, 0.6)
    rgb = np.asarray(vcolors, dtype=float)[:, :3].mean(0) / 255.0
    return tuple(float(min(max(c, 0.0), 1.0)) for c in rgb)


def _write_mjcf(out_dir, name, visual_obj, hull_paths, mass, com, I,
                friction, rgb=(0.6, 0.6, 0.6)) -> str:
    import numpy as np

    # MuJoCo wants principal ("diagonal") inertia + orientation quaternion.
    evals, evecs = np.linalg.eigh(np.asarray(I))
    evals = np.maximum(evals, 1e-10)
    R = evecs
    if np.linalg.det(R) < 0:
        R = R * np.array([1, 1, -1])
    import trimesh.transformations as tt

    q = tt.quaternion_from_matrix(
        np.vstack([np.hstack([R, [[0], [0], [0]]]), [0, 0, 0, 1]]))  # wxyz
    # MJCF meshes carry no per-vertex colour, so the scan's average tone is
    # the closest honest stand-in — otherwise every scanned prop renders grey.
    assets = [f'    <mesh name="{name}_vis" file="{os.path.basename(visual_obj)}"/>']
    geoms = [f'      <geom type="mesh" mesh="{name}_vis" contype="0" '
             f'conaffinity="0" group="2" '
             f'rgba="{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} 1"/>']
    for i, p in enumerate(hull_paths):
        assets.append(
            f'    <mesh name="{name}_c{i:02d}" file="{os.path.basename(p)}"/>')
        geoms.append(
            f'      <geom type="mesh" mesh="{name}_c{i:02d}" '
            f'friction="{friction[0]} 0.005 0.0001" group="3"/>')
    nl = "\n"
    mjcf = f"""<mujoco model="{name}">
  <compiler meshdir="." angle="radian"/>
  <option timestep="0.002"/>
  <!-- MuJoCo's default offscreen buffer is 640x480; headless renders of this
       prop fail without a larger one. -->
  <visual><global offwidth="1920" offheight="1080"/></visual>
  <asset>
{nl.join(assets)}
  </asset>
  <worldbody>
    <light pos="1.5 -1.5 3" dir="-0.4 0.4 -1" diffuse="0.7 0.7 0.7"/>
    <light pos="-2 2 3" dir="0.5 -0.5 -1" diffuse="0.4 0.4 0.4"/>
    <geom type="plane" size="4 4 0.1" rgba="0.9 0.9 0.9 1"/>
    <body name="{name}" pos="0 0 {0.02:.3f}">
      <freejoint/>
      <inertial pos="{com[0]:.6f} {com[1]:.6f} {com[2]:.6f}"
                quat="{q[0]:.6f} {q[1]:.6f} {q[2]:.6f} {q[3]:.6f}"
                mass="{mass:.6f}"
                diaginertia="{evals[2]:.8f} {evals[1]:.8f} {evals[0]:.8f}"/>
{nl.join(geoms)}
    </body>
  </worldbody>
</mujoco>
"""
    path = os.path.join(out_dir, f"{name}.xml")
    with open(path, "w") as fh:
        fh.write(mjcf)
    return path


def _write_usd(out_dir, name, mesh, hulls, mass, com, I, friction, density,
               label=None, qcode=None) -> str:
    """Author a SimReady-style USD prop: Z-up metric stage, RigidBody root,
    visual Mesh + convex collider Meshes, MassAPI, friction material."""
    import numpy as np

    path = os.path.join(out_dir, f"{name}.usda")
    stage = Usd.Stage.CreateNew(path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetMetadata("kilogramsPerUnit", 1.0)

    root = UsdGeom.Xform.Define(stage, f"/{name}")
    prim = root.GetPrim()
    stage.SetDefaultPrim(prim)
    Usd.ModelAPI(prim).SetKind("component")
    UsdPhysics.RigidBodyAPI.Apply(prim)
    mass_api = UsdPhysics.MassAPI.Apply(prim)
    mass_api.CreateMassAttr(float(mass))
    mass_api.CreateCenterOfMassAttr(Gf.Vec3f(*[float(x) for x in com]))
    evals, evecs = np.linalg.eigh(np.asarray(I))
    mass_api.CreateDiagonalInertiaAttr(
        Gf.Vec3f(*[float(max(v, 1e-10)) for v in evals]))
    if label or qcode:
        attr = prim.CreateAttribute("semantic:labels", Sdf.ValueTypeNames.StringArray)
        attr.Set([x for x in (label, qcode) if x])

    def author_mesh(parent_path, mname, tm, display_color=None):
        m = UsdGeom.Mesh.Define(stage, f"{parent_path}/{mname}")
        m.CreatePointsAttr([Gf.Vec3f(*[float(c) for c in v])
                            for v in tm.vertices])
        m.CreateFaceVertexCountsAttr([3] * len(tm.faces))
        m.CreateFaceVertexIndicesAttr(
            [int(i) for f in tm.faces for i in f])
        if display_color is not None:
            m.CreateDisplayColorAttr([Gf.Vec3f(*display_color)])
        return m

    geo = UsdGeom.Scope.Define(stage, f"/{name}/geometry")
    vis = author_mesh(f"/{name}/geometry", "visual", mesh)

    # Bake vertex colors when the scan has them. Two visual types arrive here:
    # ColorVisuals already carries per-vertex colour and has no .to_color(),
    # TextureVisuals needs the conversion. Calling .to_color() unconditionally
    # raises on the first — and a scan mesh is the case that has colour, so
    # swallowing that error silently ships every prop grey.
    visual = getattr(mesh, "visual", None)
    vcolors = getattr(visual, "vertex_colors", None)
    if vcolors is None or len(vcolors) != len(mesh.vertices):
        to_color = getattr(visual, "to_color", None)
        vcolors = to_color().vertex_colors if to_color else None
    if vcolors is not None and len(vcolors) == len(mesh.vertices):
        vis.CreateDisplayColorAttr(
            [Gf.Vec3f(*(np.asarray(c[:3], dtype=float) / 255.0)) for c in vcolors])
        UsdGeom.PrimvarsAPI(vis.GetPrim()).GetPrimvar(
            "displayColor").SetInterpolation("vertex")

    pmat = UsdShade.Material.Define(stage, f"/{name}/physics_material")
    mat_api = UsdPhysics.MaterialAPI.Apply(pmat.GetPrim())
    mat_api.CreateStaticFrictionAttr(float(friction[0]))
    mat_api.CreateDynamicFrictionAttr(float(friction[1]))
    mat_api.CreateDensityAttr(float(density))

    for i, h in enumerate(hulls):
        cm = author_mesh(f"/{name}/geometry", f"collision_{i:02d}", h,
                         display_color=(0.8, 0.2, 0.2))
        cprim = cm.GetPrim()
        UsdGeom.Imageable(cprim).CreatePurposeAttr(UsdGeom.Tokens.guide)
        UsdPhysics.CollisionAPI.Apply(cprim)
        mesh_col = UsdPhysics.MeshCollisionAPI.Apply(cprim)
        mesh_col.CreateApproximationAttr(UsdPhysics.Tokens.convexHull)
        UsdShade.MaterialBindingAPI.Apply(cprim).Bind(
            pmat, materialPurpose="physics")

    stage.GetRootLayer().Save()
    return path
