"""Point-cloud / splat file writers — stdlib only.

Two artifact formats, both openable by common tools:

* ``.ply``   binary little-endian point cloud (x y z, red green blue).
  Opens in MeshLab, CloudCompare, Blender, three.js, son's viewers.
* ``.splat`` the antimatter15/gsplat web-viewer format: 32 bytes per
  splat — position (3×f32), scale (3×f32), RGBA (4×u8), rotation
  quaternion (4×u8, ``round(q*128)+128``). A plain point cloud is written
  as tiny isotropic gaussians, so any .splat viewer can show it.
"""

from __future__ import annotations

import os
import struct

Vec3 = tuple[float, float, float]
Color = tuple[int, int, int]


def write_ply(path: str, xyz: list[Vec3], rgb: list[Color]) -> str:
    """Write a binary little-endian PLY point cloud."""
    if len(xyz) != len(rgb):
        raise ValueError(f"xyz ({len(xyz)}) and rgb ({len(rgb)}) length mismatch")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(xyz)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    pack = struct.Struct("<fffBBB").pack
    with open(path, "wb") as fh:
        fh.write(header.encode("ascii"))
        for (x, y, z), (r, g, b) in zip(xyz, rgb):
            fh.write(pack(x, y, z, r, g, b))
    return path


def read_ply(path: str) -> tuple[list[Vec3], list[Color]]:
    """Read the PLY layout :func:`write_ply` produces (and COLMAP-style
    ``x y z r g b`` variants). Enough for tests and re-serving artifacts."""
    with open(path, "rb") as fh:
        if fh.readline().strip() != b"ply":
            raise ValueError(f"{path}: not a PLY file")
        n = 0
        props: list[tuple[str, str]] = []
        binary = False
        while True:
            line = fh.readline()
            if not line:
                raise ValueError(f"{path}: unterminated PLY header")
            tok = line.decode("ascii", "replace").split()
            if not tok:
                continue
            if tok[0] == "format":
                binary = tok[1] == "binary_little_endian"
            elif tok[0] == "element":
                if tok[1] == "vertex":
                    n = int(tok[2])
                elif n:  # properties after a later element aren't vertex props
                    break
            elif tok[0] == "property" and n:
                props.append((tok[1], tok[2]))
            elif tok[0] == "end_header":
                break
        fmt = {"float": "f", "float32": "f", "uchar": "B", "uint8": "B",
               "double": "d", "int": "i", "short": "h", "ushort": "H"}
        names = [p[1] for p in props]
        for want in ("x", "y", "z", "red", "green", "blue"):
            if want not in names and want not in ("red", "green", "blue"):
                raise ValueError(f"{path}: no vertex property {want!r}")
        if not binary:
            raise ValueError(f"{path}: only binary_little_endian PLY supported")
        rec = struct.Struct("<" + "".join(fmt[t] for t, _ in props))
        xyz: list[Vec3] = []
        rgb: list[Color] = []
        idx = {name: i for i, (_, name) in enumerate(props)}
        has_rgb = all(k in idx for k in ("red", "green", "blue"))
        for _ in range(n):
            vals = rec.unpack(fh.read(rec.size))
            xyz.append((vals[idx["x"]], vals[idx["y"]], vals[idx["z"]]))
            rgb.append(tuple(int(vals[idx[k]]) for k in ("red", "green", "blue"))
                       if has_rgb else (200, 200, 200))
        return xyz, rgb


def write_splat(
    path: str,
    xyz: list[Vec3],
    rgb: list[Color],
    scale: float = 0.01,
    opacity: int = 255,
) -> str:
    """Write an antimatter15-format ``.splat``: points become small
    isotropic gaussians (identity rotation, uniform ``scale`` meters)."""
    if len(xyz) != len(rgb):
        raise ValueError(f"xyz ({len(xyz)}) and rgb ({len(rgb)}) length mismatch")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # rotation quat (w,x,y,z)=(1,0,0,0) -> bytes clamp(round(q*128)+128)
    rot = bytes((255, 128, 128, 128))
    pack = struct.Struct("<ffffff").pack
    with open(path, "wb") as fh:
        for (x, y, z), (r, g, b) in zip(xyz, rgb):
            fh.write(pack(x, y, z, scale, scale, scale))
            fh.write(bytes((r, g, b, opacity)))
            fh.write(rot)
    return path
