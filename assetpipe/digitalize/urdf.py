"""URDF generation (pure stdlib).

A URDF turns a mesh into a *simulatable* digital twin: it can be dropped
into PyBullet / Isaac Sim / Gazebo / MuJoCo, given mass and collision, and
manipulated. For a rigid captured item this is a single link wrapping the
visual + collision mesh; for articulated items (a drawer, a laptop) you add
joints — that is where URDF-Anything (image -> articulated URDF) plugs in
later.

We keep the visual and collision geometry pointed at the same mesh, which
is fine for convex-ish captured props; swap in a decimated convex-hull
collision mesh for physics performance.
"""

from __future__ import annotations

import os

# rough default density (kg/m^3) — cardboard-ish; override per category.
_DEFAULT_DENSITY = 120.0


def write_urdf(
    path: str,
    name: str,
    mesh_rel: str,
    dimensions_m: tuple[float, float, float],
    density: float = _DEFAULT_DENSITY,
) -> None:
    w, h, d = dimensions_m
    mass = max(density * w * h * d, 0.01)
    # Solid-cuboid inertia about the center of mass.
    ixx = (1.0 / 12.0) * mass * (h * h + d * d)
    iyy = (1.0 / 12.0) * mass * (w * w + d * d)
    izz = (1.0 / 12.0) * mass * (w * w + h * h)
    com_y = h / 2.0  # base-origin mesh -> COM is half height up

    xml = f"""<?xml version="1.0"?>
<robot name="{name}">
  <link name="base_link">
    <visual>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <geometry>
        <mesh filename="{mesh_rel}" scale="1 1 1"/>
      </geometry>
    </visual>
    <collision>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <geometry>
        <mesh filename="{mesh_rel}" scale="1 1 1"/>
      </geometry>
    </collision>
    <inertial>
      <origin xyz="0 {com_y:.6f} 0" rpy="0 0 0"/>
      <mass value="{mass:.6f}"/>
      <inertia ixx="{ixx:.6f}" ixy="0" ixz="0" iyy="{iyy:.6f}" iyz="0" izz="{izz:.6f}"/>
    </inertial>
  </link>
</robot>
"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        fh.write(xml)
