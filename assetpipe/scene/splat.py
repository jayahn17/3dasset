"""Ingest a real scanner's output (Scaniverse/Polycam/LumaAI) and turn it
into the clean multi-view images that image-to-3D models want.

    scan.ply  ──▶ isolate the object ──▶ render N orbit views (RGBA)
    (gaussian                              │
     splat or                              ▼
     point cloud)                   TRELLIS / Hunyuan3D / Meshy / Tripo
                                           │
                                           ▼
                                 clean watertight textured GLB

WHY THIS BEATS FEEDING RAW PHONE VIDEO TO A GENERATOR
A LiDAR scan already solved capture: no motion blur, no glare, no missed
angles. Rendering it gives perfectly framed, background-free views from
*any* viewpoint — the strongest possible conditioning for a generative
model, which then supplies the clean topology and completes unseen
surfaces. The scan keeps it honest (real geometry, real scale); the
generator makes it an asset.

Reads BOTH plain point-cloud PLY (x y z r g b) and 3D Gaussian Splat PLY
(x y z, f_dc_* spherical-harmonic color, opacity, scale_*, rot_*), which
is what Scaniverse/Polycam export. numpy-backed, so millions of gaussians
are fine.

    SPZ note: Niantic's .spz is a *compressed* splat container. Export PLY
    from Scaniverse (Share -> Export -> PLY), or convert spz -> ply first.
"""

from __future__ import annotations

import os

# spherical-harmonic DC coefficient: 3DGS stores color as SH, not RGB
_SH_C0 = 0.28209479177387814

_NPTYPE = {
    "float": "<f4", "float32": "<f4", "double": "<f8", "float64": "<f8",
    "uchar": "u1", "uint8": "u1", "char": "i1", "int8": "i1",
    "ushort": "<u2", "uint16": "<u2", "short": "<i2", "int16": "<i2",
    "uint": "<u4", "uint32": "<u4", "int": "<i4", "int32": "<i4",
}


def _parse_header(fh) -> tuple[int, list[tuple[str, str]]]:
    if fh.readline().strip() != b"ply":
        raise ValueError("not a PLY file")
    n, props, binary = 0, [], False
    while True:
        line = fh.readline()
        if not line:
            raise ValueError("unterminated PLY header")
        tok = line.decode("ascii", "replace").split()
        if not tok:
            continue
        if tok[0] == "format":
            binary = tok[1] == "binary_little_endian"
        elif tok[0] == "element":
            if tok[1] == "vertex":
                n = int(tok[2])
            elif n:  # properties of a later element aren't vertex props
                break
        elif tok[0] == "property" and n and tok[1] != "list":
            props.append((tok[1], tok[2]))
        elif tok[0] == "end_header":
            break
    if not binary:
        raise ValueError("only binary_little_endian PLY is supported — "
                         "re-export, or convert with a PLY tool")
    return n, props


def load_ply(path: str, min_opacity: float = 0.25):
    """-> (xyz float64 [N,3], rgb uint8 [N,3]). Handles plain and 3DGS PLY.

    For gaussian splats, near-transparent gaussians (the fog a splat uses
    to fake soft edges) are dropped — they wreck geometry downstream.
    """
    import numpy as np

    with open(path, "rb") as fh:
        n, props = _parse_header(fh)
        dt = np.dtype([(name, _NPTYPE[t]) for t, name in props])
        arr = np.frombuffer(fh.read(n * dt.itemsize), dtype=dt, count=n)

    names = set(arr.dtype.names)
    for k in ("x", "y", "z"):
        if k not in names:
            raise ValueError(f"{path}: PLY has no '{k}' vertex property")
    xyz = np.stack([arr["x"], arr["y"], arr["z"]], axis=1).astype(np.float64)

    if {"red", "green", "blue"} <= names:                      # plain cloud
        rgb = np.stack([arr["red"], arr["green"], arr["blue"]], axis=1)
        rgb = rgb.astype(np.uint8)
    elif {"f_dc_0", "f_dc_1", "f_dc_2"} <= names:              # 3DGS: SH -> RGB
        dc = np.stack([arr["f_dc_0"], arr["f_dc_1"], arr["f_dc_2"]], axis=1)
        rgb = np.clip(0.5 + _SH_C0 * dc, 0, 1) * 255.0
        rgb = rgb.astype(np.uint8)
    else:
        rgb = np.full((len(xyz), 3), 200, np.uint8)

    if "opacity" in names and min_opacity > 0:                 # logit-encoded
        alpha = 1.0 / (1.0 + np.exp(-arr["opacity"].astype(np.float64)))
        keep = alpha >= min_opacity
        xyz, rgb = xyz[keep], rgb[keep]
    return xyz, rgb


def load_gaussians(path: str, min_opacity: float = 0.25) -> dict:
    """Full gaussian-splat load: centers, colors, opacity, scales, rotations.

    :func:`load_ply` throws away everything but position and color — fine for
    geometry, useless for *rendering*. A generator conditions on images, so
    the splat's real footprint (scale + rotation + opacity) is what turns a
    scan back into something that looks photographed rather than dotted.
    """
    import numpy as np

    with open(path, "rb") as fh:
        n, props = _parse_header(fh)
        dt = np.dtype([(name, _NPTYPE[t]) for t, name in props])
        arr = np.frombuffer(fh.read(n * dt.itemsize), dtype=dt, count=n)
    names = set(arr.dtype.names)

    xyz = np.stack([arr["x"], arr["y"], arr["z"]], 1).astype(np.float64)
    if {"f_dc_0", "f_dc_1", "f_dc_2"} <= names:
        dc = np.stack([arr["f_dc_0"], arr["f_dc_1"], arr["f_dc_2"]], 1)
        rgb = (np.clip(0.5 + _SH_C0 * dc, 0, 1) * 255).astype(np.uint8)
    elif {"red", "green", "blue"} <= names:
        rgb = np.stack([arr["red"], arr["green"], arr["blue"]], 1).astype(np.uint8)
    else:
        rgb = np.full((n, 3), 200, np.uint8)

    if "opacity" in names:  # stored as a logit
        alpha = 1.0 / (1.0 + np.exp(-arr["opacity"].astype(np.float64)))
    else:
        alpha = np.ones(n)

    if {"scale_0", "scale_1", "scale_2"} <= names:  # stored as log
        scale = np.exp(np.stack([arr[f"scale_{i}"] for i in range(3)], 1)
                       .astype(np.float64))
    else:  # plain cloud: fake a small isotropic footprint
        span = float(np.linalg.norm(xyz.max(0) - xyz.min(0))) or 1.0
        scale = np.full((n, 3), span / 400.0)

    if {"rot_0", "rot_1", "rot_2", "rot_3"} <= names:
        rot = np.stack([arr[f"rot_{i}"] for i in range(4)], 1).astype(np.float64)
        rot /= np.linalg.norm(rot, axis=1, keepdims=True) + 1e-12
    else:
        rot = np.tile([1.0, 0.0, 0.0, 0.0], (n, 1))

    keep = alpha >= min_opacity
    return {"xyz": xyz[keep], "rgb": rgb[keep], "opacity": alpha[keep],
            "scale": scale[keep], "rot": rot[keep]}


def _quat_to_R(q):
    """(w,x,y,z) quaternions -> rotation matrices, batched."""
    import numpy as np

    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    R = np.empty((len(q), 3, 3))
    R[:, 0, 0] = 1 - 2 * (y * y + z * z)
    R[:, 0, 1] = 2 * (x * y - w * z)
    R[:, 0, 2] = 2 * (x * z + w * y)
    R[:, 1, 0] = 2 * (x * y + w * z)
    R[:, 1, 1] = 1 - 2 * (x * x + z * z)
    R[:, 1, 2] = 2 * (y * z - w * x)
    R[:, 2, 0] = 2 * (x * z - w * y)
    R[:, 2, 1] = 2 * (y * z + w * x)
    R[:, 2, 2] = 1 - 2 * (x * x + y * y)
    return R


def render_gaussians(g: dict, eye, target, up, size: int = 768,
                     fov_scale: float = 1.25, bg=(255, 255, 255)):
    """Rasterize gaussians into an RGBA image (EWA splatting).

    Each gaussian's 3D covariance is projected to a 2D covariance and
    alpha-composited front-to-back — the same math the real splat viewers
    use. Output looks photographed, which is exactly what an image-to-3D
    model was trained on.
    """
    import numpy as np

    xyz, rgb, op, scale, rot = (g["xyz"], g["rgb"], g["opacity"],
                                g["scale"], g["rot"])
    z = eye - target
    z /= np.linalg.norm(z)
    x = np.cross(up, z)
    xn = np.linalg.norm(x)
    x = np.array([1.0, 0.0, 0.0]) if xn < 1e-9 else x / xn
    y = np.cross(z, x)
    W = np.stack([x, y, z])                    # world -> camera rotation
    f = fov_scale * size

    cam = (xyz - eye) @ W.T
    depth = -cam[:, 2]                         # camera looks down -z
    vis = depth > 1e-6
    if not vis.any():
        return np.zeros((size, size, 4), np.uint8)
    cam, depth = cam[vis], depth[vis]
    col, alpha = rgb[vis].astype(np.float64), op[vis]

    R = _quat_to_R(rot[vis])
    S = scale[vis]
    M = R * S[:, None, :]                      # R @ diag(S)
    Sigma = M @ np.transpose(M, (0, 2, 1))     # 3D covariance
    Sigma_cam = W @ Sigma @ W.T

    u = f * cam[:, 0] / depth + size / 2
    v = -f * cam[:, 1] / depth + size / 2
    J = np.zeros((len(cam), 2, 3))             # projection jacobian
    J[:, 0, 0] = f / depth
    J[:, 0, 2] = f * cam[:, 0] / depth ** 2
    J[:, 1, 1] = -f / depth
    J[:, 1, 2] = -f * cam[:, 1] / depth ** 2
    cov2d = J @ Sigma_cam @ np.transpose(J, (0, 2, 1))
    cov2d[:, 0, 0] += 0.3                      # antialiasing blur
    cov2d[:, 1, 1] += 0.3

    det = cov2d[:, 0, 0] * cov2d[:, 1, 1] - cov2d[:, 0, 1] ** 2
    ok = det > 1e-9
    inv = np.zeros_like(cov2d)
    inv[ok, 0, 0] = cov2d[ok, 1, 1] / det[ok]
    inv[ok, 1, 1] = cov2d[ok, 0, 0] / det[ok]
    inv[ok, 0, 1] = inv[ok, 1, 0] = -cov2d[ok, 0, 1] / det[ok]
    radius = np.zeros(len(cam))
    tr = cov2d[:, 0, 0] + cov2d[:, 1, 1]
    disc = np.sqrt(np.maximum(tr ** 2 / 4 - det, 0))
    radius[ok] = 3.0 * np.sqrt(np.maximum(tr[ok] / 2 + disc[ok], 1e-9))
    radius = np.clip(radius, 0, size / 4)

    colour = np.zeros((size, size, 3))
    trans = np.ones((size, size))              # remaining transmittance
    for i in np.argsort(depth):                # front to back
        if not ok[i] or radius[i] < 0.5:
            continue
        r = int(radius[i]) + 1
        cu, cv = int(round(u[i])), int(round(v[i]))
        u0, u1 = max(0, cu - r), min(size, cu + r + 1)
        v0, v1 = max(0, cv - r), min(size, cv + r + 1)
        if u0 >= u1 or v0 >= v1:
            continue
        gu, gv = np.meshgrid(np.arange(u0, u1) - u[i],
                             np.arange(v0, v1) - v[i])
        power = -0.5 * (inv[i, 0, 0] * gu ** 2
                        + 2 * inv[i, 0, 1] * gu * gv
                        + inv[i, 1, 1] * gv ** 2)
        a = alpha[i] * np.exp(np.minimum(power, 0.0))
        a = np.clip(a, 0, 0.99)
        t = trans[v0:v1, u0:u1]
        w = a * t
        colour[v0:v1, u0:u1] += w[..., None] * col[i]
        trans[v0:v1, u0:u1] = t * (1 - a)

    cov = 1.0 - trans                           # accumulated alpha
    out = np.zeros((size, size, 4), np.uint8)
    solid = cov > 0.02
    rgb_out = np.zeros((size, size, 3))
    rgb_out[solid] = colour[solid] / cov[solid][:, None]
    bg_arr = np.array(bg, np.float64)
    rgb_out = rgb_out * cov[..., None] + bg_arr * (1 - cov[..., None])
    out[..., :3] = np.clip(rgb_out, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(cov * 255, 0, 255).astype(np.uint8)
    return out


def render_gaussian_views(g: dict, out_dir: str, n_views: int = 8,
                          size: int = 768, elevations=(15.0, 40.0),
                          bg=(255, 255, 255)) -> list[str]:
    """Photo-like orbit renders of a gaussian splat -> PNGs for the generator."""
    import numpy as np
    from PIL import Image

    os.makedirs(out_dir, exist_ok=True)
    xyz = g["xyz"]
    ctr = (xyz.max(0) + xyz.min(0)) / 2
    radius = float(np.abs(xyz - ctr).max()) or 1.0
    _, _, Vt = np.linalg.svd(np.cov((xyz - xyz.mean(0)).T))
    up = Vt[2] / np.linalg.norm(Vt[2])
    a0 = Vt[0] / np.linalg.norm(Vt[0])
    a1 = np.cross(up, a0)

    paths = []
    for ei, elev in enumerate(elevations):
        el = np.radians(elev)
        for i in range(n_views):
            az = 2 * np.pi * i / n_views
            d = (np.cos(el) * (np.cos(az) * a0 + np.sin(az) * a1)
                 + np.sin(el) * up)
            eye = ctr + 2.4 * radius * d
            img = render_gaussians(g, eye, ctr, up, size=size, bg=bg)
            p = os.path.join(out_dir, f"view_e{ei}_{i:02d}.png")
            Image.fromarray(img, "RGBA").save(p)
            paths.append(p)
    return paths


def drop_far_field(xyz, rgb, k: float = 5.0):
    """Remove the far-field shell a splat trainer scatters around a scan.

    Real scans (Scaniverse et al.) carry a few % of gaussians parked
    hundreds of units out — the "sky" that soaks up unexplained pixels.
    They're harmless to look at and catastrophic to measure against: they
    inflate the scene's bounding box ~200x, so every density/plane
    threshold derived from it becomes meaningless.

    Keeps points within ``k`` x the 90th-percentile radius of the median
    center. No-ops when that would discard a big chunk (i.e. the scan is
    genuinely spread out).
    """
    import numpy as np

    if len(xyz) < 100:
        return xyz, rgb
    med = np.median(xyz, axis=0)
    r = np.linalg.norm(xyz - med, axis=1)
    cutoff = k * float(np.percentile(r, 90))
    if cutoff <= 0:
        return xyz, rgb
    keep = r <= cutoff
    if np.count_nonzero(keep) < 0.6 * len(xyz):  # not a far-field shell
        return xyz, rgb
    return xyz[keep], rgb[keep]


def denoise(xyz, rgb, k: int = 12, std_ratio: float = 1.5):
    """Statistical outlier removal: drop the speckle a splat leaves floating
    around an object (points whose neighbours are unusually far away)."""
    import numpy as np
    from scipy.spatial import cKDTree

    if len(xyz) <= k:
        return xyz, rgb
    d, _ = cKDTree(xyz).query(xyz, k=k + 1)
    mean_d = d[:, 1:].mean(axis=1)          # skip self
    keep = mean_d < mean_d.mean() + std_ratio * mean_d.std()
    return xyz[keep], rgb[keep]


def _dominant_plane(pts, thickness: float, iters: int = 400, seed: int = 0):
    """RANSAC the surface the object rests on -> (unit normal, offset, inliers)."""
    import numpy as np

    rng = np.random.default_rng(seed)
    sample = pts[rng.choice(len(pts), min(4000, len(pts)), replace=False)]
    best = (None, 0.0, 0)
    for _ in range(iters):
        p = sample[rng.choice(len(sample), 3, replace=False)]
        nvec = np.cross(p[1] - p[0], p[2] - p[0])
        nn = np.linalg.norm(nvec)
        if nn < 1e-12:
            continue
        nvec /= nn
        d = -float(nvec @ p[0])
        hits = int(np.count_nonzero(np.abs(sample @ nvec + d) < thickness))
        if hits > best[2]:
            best = (nvec, d, hits)
    return best


def isolate_object(xyz, rgb, focus: float = 0.9, center=None,
                   radius: float | None = None, drop_support: bool = True):
    """Strip the room and return just the object the scan was aimed at.

    Learned the hard way: "keep the biggest cluster" does NOT find the
    object — it finds the wall, or the desk, or (once the desk is removed)
    a blob still fused to it through a residual fringe. What *does* work
    exploits how these scans are made:

    * an orbit scan is centred on its subject, so the median radius from
      the scan's median point is a good proxy for the object's own size;
    * the object sits ON something — one RANSAC plane, gone, takes the
      desk and everything under it with it.

    So: strip the far field, keep what's within ``focus`` x the object
    scale, drop the support plane and everything below it.

    ``focus`` is the one knob: raise it if the object is clipped, lower it
    if neighbouring clutter creeps in. ``center``/``radius`` override the
    heuristic entirely, and cropping in the scanner app beats both.
    """
    import numpy as np

    if len(xyz) < 50:
        return xyz, rgb

    if center is not None and radius:  # explicit crop wins over any heuristic
        d = np.linalg.norm(xyz - np.asarray(center, np.float64), axis=1)
        return xyz[d <= radius], rgb[d <= radius]

    xyz, rgb = drop_far_field(xyz, rgb)  # else every threshold below is junk

    med = np.median(xyz, axis=0)
    r = np.linalg.norm(xyz - med, axis=1)
    obj_scale = float(np.median(r)) or 1.0

    near = r < 2.5 * obj_scale            # object + the surface under it
    P, C = xyz[near], rgb[near]
    if len(P) < 100:
        return P, C

    keep = np.linalg.norm(P - med, axis=1) < focus * obj_scale
    if drop_support:
        thickness = obj_scale / 25.0
        nvec, d, hits = _dominant_plane(P, thickness)
        if nvec is not None and hits > 0.12 * min(4000, len(P)):
            side = P @ nvec + d
            # the object is on whichever side holds more off-plane mass
            up = 1.0 if (np.count_nonzero(side > thickness)
                         >= np.count_nonzero(side < -thickness)) else -1.0
            keep &= side * up > 1.5 * thickness   # plane + everything below it
    P, C = P[keep], C[keep]
    try:
        P, C = denoise(P, C)                      # kill the floating speckle
    except ImportError:                           # scipy is an optional extra
        pass
    return P, C


def render_orbit_views(
    xyz,
    rgb,
    out_dir: str,
    n_views: int = 8,
    size: int = 768,
    elevations=(15.0, 40.0),
    point_scale: float = 1.6,
    transparent: bool = True,
) -> list[str]:
    """Render the isolated object from ``n_views`` angles → RGBA PNGs.

    These are the conditioning images for an image-to-3D model: object
    centered, background transparent (or white), no blur, no glare.
    """
    import numpy as np
    from PIL import Image

    if len(xyz) < 10:
        raise ValueError(f"too few points to render ({len(xyz)})")
    os.makedirs(out_dir, exist_ok=True)

    ctr = (xyz.max(0) + xyz.min(0)) / 2
    radius = float(np.abs(xyz - ctr).max()) or 1.0
    # scan "up" = normal of the least-variance axis (objects rest on a plane)
    _, _, Vt = np.linalg.svd(np.cov((xyz - xyz.mean(0)).T))
    up = Vt[2] / np.linalg.norm(Vt[2])
    a0 = Vt[0] / np.linalg.norm(Vt[0])
    a1 = np.cross(up, a0)

    # a point's screen footprint ~ the cloud's own sampling density
    spacing = radius / (len(xyz) ** (1 / 3) + 1e-9)
    paths: list[str] = []
    for ei, elev in enumerate(elevations):
        for i in range(n_views):
            az = 2 * np.pi * i / n_views
            el = np.radians(elev)
            dirv = (np.cos(el) * (np.cos(az) * a0 + np.sin(az) * a1)
                    + np.sin(el) * up)
            eye = ctr + 2.5 * radius * dirv
            z = eye - ctr
            z /= np.linalg.norm(z)
            x = np.cross(up, z)
            xn = np.linalg.norm(x)
            x = (a0 if xn < 1e-9 else x / xn)
            y = np.cross(z, x)

            cam = (xyz - eye) @ np.stack([x, y, z]).T
            depth = -cam[:, 2]
            vis = depth > 1e-6
            f = 1.25 * size
            u = (f * cam[vis, 0] / depth[vis] + size / 2)
            v = (-f * cam[vis, 1] / depth[vis] + size / 2)
            d = depth[vis]
            col = rgb[vis]

            # painter's algorithm: far first, near overwrites
            order = np.argsort(-d)
            u, v, d, col = u[order], v[order], d[order], col[order]
            rad = np.maximum(1, (f * spacing * point_scale / d)).astype(np.int32)
            rad = np.clip(rad, 1, 6)

            img = np.zeros((size, size, 4), np.uint8)
            for r in range(1, int(rad.max()) + 1):   # draw per radius, vectorized
                m = rad == r
                if not m.any():
                    continue
                uu, vv, cc = u[m].astype(np.int32), v[m].astype(np.int32), col[m]
                for dv in range(-r, r + 1):
                    for du in range(-r, r + 1):
                        if du * du + dv * dv > r * r:
                            continue
                        pu, pv = uu + du, vv + dv
                        ok = (pu >= 0) & (pu < size) & (pv >= 0) & (pv < size)
                        img[pv[ok], pu[ok], :3] = cc[ok]
                        img[pv[ok], pu[ok], 3] = 255
            if not transparent:  # composite on white for generators wanting RGB
                bg = np.full((size, size, 3), 255, np.uint8)
                alpha = (img[..., 3:4] / 255.0)
                rgbim = (img[..., :3] * alpha + bg * (1 - alpha)).astype(np.uint8)
                img = np.dstack([rgbim, np.full((size, size), 255, np.uint8)])

            p = os.path.join(out_dir, f"view_e{ei}_{i:02d}.png")
            Image.fromarray(img, "RGBA").save(p)
            paths.append(p)
    return paths
