#!/usr/bin/env python3
"""Gaussian splat → CAD-measurable geometry (Onshape / Fusion / SolidWorks).

A trained 3DGUT/3DGS scene is a metric point-cloud-of-ellipsoids, not a
surface. CAD can neither import nor measure it. This turns one into geometry
a CAD seat can actually dimension:

  1. **Filter** — drop diverged / faint / oversized gaussians and the floater
     haze that surrounds every real capture (radius-outlier on the dense core).
  2. **Level** — RANSAC the floor, rotate world-up to **+Z**, put the floor on
     **z = 0**. Height then reads straight off the CAD origin plane.
  3. **Isolate** — optional crop box, then the largest above-floor cluster, so
     the export is the couch and not the room it sits in.
  4. **Square up** — minimum-area footprint box about the up axis defines
     X = length, Y = depth. The part lands on the origin corner, so every
     datum plane is a usable measuring reference.
  5. **Surface** — screened Poisson over normals estimated from the gaussian
     centers, low-density blobs trimmed, Taubin-smoothed, quadric-decimated.
  6. **Export** — STL/OBJ in **millimeters** (the CAD import convention),
     plus DXF **section profiles** that import into an Onshape sketch, which
     is how you dimension a curved shape like a couch arm.

Scale is inherited from the capture: 3DGUT trained on ARKit poses is metric,
so measurements are real. Verify on import with `scale_check_100mm.stl`.

Run once without --crop and open `views_levelled.png`; it is a dimensioned
plan/elevation of the levelled scene, which is how you read off the crop box
for a cluttered room.

    python tools/splat_to_cad.py demo_out/CrateScan-DC22F084_3dgut_fullres/scene_gaussians.ply \
        --out cad_out/couch --name couch --crop -4.75,4.90,0.02,-3.45,7.05,1.00

Defaults are tuned for upholstered furniture: a splat's centers form a noisy
shell a few cm thick, so normals are estimated over a wide radius and Poisson
runs shallow. Sharp-edged rigid parts want a smaller --voxel and a deeper
--poisson-depth.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

MM = 1000.0  # meters → millimeters
IN = 0.0254  # inches → meters


# ---------------------------------------------------------------- splat I/O

def read_gaussian_ply(path: Path) -> dict:
    """Read a 3DGS/3DGUT binary PLY without the plyfile dependency.

    All properties in this format are float32, so one structured dtype and a
    single np.fromfile beats any per-property parser.
    """
    with open(path, "rb") as fh:
        hdr = b""
        while b"end_header" not in hdr:
            chunk = fh.read(4096)
            if not chunk:
                raise ValueError(f"{path}: no end_header (not a PLY?)")
            hdr += chunk
    if b"binary_little_endian" not in hdr:
        raise ValueError(f"{path}: only binary_little_endian PLY is supported")
    offset = hdr.index(b"end_header") + len(b"end_header\n")
    text = hdr.split(b"end_header")[0].decode("ascii", "replace")

    n = int(re.search(r"element vertex (\d+)", text).group(1))
    props = re.findall(r"property\s+(\S+)\s+(\S+)", text)
    if any(t != "float" for t, _ in props):
        raise ValueError(f"{path}: expected all-float32 gaussian properties")
    names = [nm for _, nm in props]
    arr = np.fromfile(path, dtype=np.dtype([(nm, "<f4") for nm in names]),
                      count=n, offset=offset)

    out = {"n": n, "xyz": np.stack([arr["x"], arr["y"], arr["z"]], 1).astype(np.float64)}
    if "opacity" in names:
        out["opacity"] = _sigmoid(arr["opacity"].astype(np.float64))
    if "scale_0" in names:
        out["scale"] = np.exp(np.stack(
            [arr["scale_0"], arr["scale_1"], arr["scale_2"]], 1).astype(np.float64))
    if "f_dc_0" in names:
        # SH band 0 → linear RGB
        sh = np.stack([arr["f_dc_0"], arr["f_dc_1"], arr["f_dc_2"]], 1).astype(np.float64)
        out["rgb"] = np.clip(0.5 + 0.2820947917738781 * sh, 0, 1)
    return out


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50, 50)))


# ------------------------------------------------------------- filter stage

def filter_gaussians(g: dict, *, min_opacity: float, max_size: float,
                     max_abs: float = 1e4) -> tuple[np.ndarray, np.ndarray, dict]:
    """Drop diverged, near-transparent and balloon gaussians.

    Balloon gaussians (a metre wide) are the background-haze primitives a
    splat grows to explain unobserved regions; they carry no surface.
    """
    xyz = g["xyz"]
    keep = np.isfinite(xyz).all(1) & (np.abs(xyz) < max_abs).all(1)
    stats = {"n_in": int(g["n"]), "dropped_diverged": int((~keep).sum())}

    if "opacity" in g:
        faint = keep & (g["opacity"] < min_opacity)
        keep &= g["opacity"] >= min_opacity
        stats["dropped_faint"] = int(faint.sum())
    if "scale" in g:
        big = keep & (g["scale"].max(1) > max_size)
        keep &= g["scale"].max(1) <= max_size
        stats["dropped_oversized"] = int(big.sum())

    stats["n_kept"] = int(keep.sum())
    rgb = g["rgb"][keep] if "rgb" in g else None
    return xyz[keep], rgb, stats


def dense_core(xyz: np.ndarray, rgb, *, voxel: float, nb_points: int,
               radius: float, stats: dict):
    """Voxel-downsample, then keep only points with real neighbourhood support.

    Floaters are individually plausible but locally isolated; a radius filter
    removes them where a statistical (kNN-distance) filter does not, because
    the haze is dense enough to fool the kNN test.
    """
    import open3d as o3d

    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(xyz))
    if rgb is not None:
        pcd.colors = o3d.utility.Vector3dVector(rgb)
    pcd = pcd.voxel_down_sample(voxel)
    stats["n_voxel"] = len(pcd.points)
    pcd, _ = pcd.remove_radius_outlier(nb_points=nb_points, radius=radius)
    stats["n_dense"] = len(pcd.points)
    return pcd


# -------------------------------------------------------------- level stage

def find_floor(points: np.ndarray, *, thickness: float = 0.02) -> tuple[np.ndarray, float, dict]:
    """Return (up_unit_vector, floor_offset, info) for the dominant ground plane.

    The floor is the biggest horizontal population near the bottom of a scan,
    which a plain RANSAC over the whole cloud will happily lose to a couch
    seat or a wall. So: pick the up axis from the flattest world axis, seed a
    band at the low mode of that axis, and fit inside that band only.

    The fit is trimmed total-least-squares rather than RANSAC, because this
    plane is the datum every exported height is measured from. Open3D's
    ``segment_plane`` parallelises its hypothesis loop, so it returns a plane
    that wanders centimetres between runs even when seeded — measurements that
    change run to run are worse than measurements that are slightly wrong.
    """
    # Up axis = the world axis with the smallest spread (a room is wide and short)
    spans = np.percentile(points, 99, axis=0) - np.percentile(points, 1, axis=0)
    up_axis = int(np.argmin(spans))

    h = points[:, up_axis]
    hist, edges = np.histogram(h, bins=200,
                               range=(np.percentile(h, 0.5), np.percentile(h, 99.5)))
    # Search the lower half for the tallest bin: floor, not table or ceiling.
    lower = hist[: len(hist) // 2]
    peak = int(np.argmax(lower))
    seed = 0.5 * (edges[peak] + edges[peak + 1])

    band = points[np.abs(h - seed) < thickness * 3]
    if len(band) < 50:
        raise ValueError("no floor band found — is the capture floor-less?")

    # Seed from the band's own geometry: a slab of floor points has its
    # smallest principal direction along the floor normal. No RNG involved.
    centroid = band.mean(0)
    _, _, vh = np.linalg.svd(band - centroid, full_matrices=False)
    normal = vh[2] / np.linalg.norm(vh[2])
    offset = float(centroid @ normal)

    # Trim toward the true plane: each pass discards points more than
    # `thickness` off the current estimate, so clutter resting on the floor
    # stops dragging the fit after a couple of iterations.
    for _ in range(6):
        keep = band[np.abs(band @ normal - offset) < thickness]
        if len(keep) < 50:
            break
        centroid = keep.mean(0)
        _, _, vh = np.linalg.svd(keep - centroid, full_matrices=False)
        candidate = vh[2] / np.linalg.norm(vh[2])
        normal = candidate * np.sign(np.dot(candidate, normal) or 1.0)
        offset = float(centroid @ normal)

    # Orient up: away from the floor, toward the bulk of the cloud.
    if np.dot(normal, points.mean(0) - centroid) < 0:
        normal, offset = -normal, -offset

    resid = band @ normal - offset
    inliers = np.abs(resid) < thickness
    tilt = float(np.degrees(np.arccos(np.clip(abs(normal[up_axis]), -1, 1))))
    return normal, offset, {
        "up_axis_guess": "xyz"[up_axis],
        "plane_normal": normal.round(5).tolist(),
        "floor_height_m": round(offset, 4),
        "band_points": int(len(band)),
        "fit_inliers": int(inliers.sum()),
        "fit_rms_mm": round(float(np.sqrt(np.mean(resid[inliers] ** 2)) * MM), 2),
        "tilt_from_axis_deg": round(tilt, 2),
    }


def level_transform(up: np.ndarray, offset: float) -> np.ndarray:
    """4×4 that sends `up` → +Z and the floor plane → z = 0."""
    z = up / np.linalg.norm(up)
    seed = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = np.cross(seed, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    T = np.eye(4)
    T[:3, :3] = np.stack([x, y, z])       # rows = new basis
    T[:3, 3] = [0.0, 0.0, -offset]        # floor to z=0 (offset is along z after rotation)
    return T


# ------------------------------------------------------------ isolate stage

def crop_box(pcd, box: str, stats: dict):
    """Crop in levelled metres: 'x0,y0,z0,x1,y1,z1'.

    Clustering alone cannot separate a couch from the bags leaning on it, so a
    real cluttered room needs one human decision. `views_levelled.png` is the
    dimensioned drawing you read the numbers off.
    """
    import open3d as o3d

    v = [float(t) for t in box.replace(" ", "").split(",")]
    if len(v) != 6:
        raise ValueError(f"--crop needs 6 comma-separated metres, got {box!r}")
    lo, hi = np.minimum(v[:3], v[3:]), np.maximum(v[:3], v[3:])
    out = pcd.crop(o3d.geometry.AxisAlignedBoundingBox(lo, hi))
    stats["crop_box_m"] = {"min": lo.tolist(), "max": hi.tolist()}
    stats["n_cropped"] = len(out.points)
    return out


def isolate_object(pcd, *, floor_clearance: float, eps: float, min_points: int,
                   stats: dict):
    """Keep the largest connected above-floor cluster: the object, not the room."""
    import open3d as o3d

    pts = np.asarray(pcd.points)
    above = pts[:, 2] > floor_clearance
    stats["n_above_floor"] = int(above.sum())
    obj = pcd.select_by_index(np.flatnonzero(above))

    labels = np.asarray(obj.cluster_dbscan(eps=eps, min_points=min_points))
    if (labels >= 0).sum() == 0:
        stats["clusters"] = 0
        return obj
    ids, counts = np.unique(labels[labels >= 0], return_counts=True)
    best = ids[int(np.argmax(counts))]
    stats["clusters"] = int(len(ids))
    stats["largest_cluster"] = int(counts.max())
    stats["cluster_share"] = round(float(counts.max() / max(1, len(labels))), 3)
    return obj.select_by_index(np.flatnonzero(labels == best))


def square_up(points: np.ndarray) -> np.ndarray:
    """Yaw that aligns the footprint's minimum-area box to X (length) / Y (depth).

    Rotating about the up axis only — never tilting — keeps the floor on z = 0,
    which is the whole point of levelling first.
    """
    from scipy.spatial import ConvexHull

    xy = points[:, :2]
    try:
        hull = xy[ConvexHull(xy).vertices]
    except Exception:
        hull = xy
    edges = np.diff(np.vstack([hull, hull[:1]]), axis=0)
    angles = np.unique(np.round(np.arctan2(edges[:, 1], edges[:, 0]) % (np.pi / 2), 6))

    best, best_area = 0.0, np.inf
    for a in angles:
        c, s = np.cos(-a), np.sin(-a)
        rot = hull @ np.array([[c, -s], [s, c]]).T
        ext = rot.max(0) - rot.min(0)
        if ext[0] * ext[1] < best_area:
            best_area, best = ext[0] * ext[1], a

    # Longest footprint edge → X
    c, s = np.cos(-best), np.sin(-best)
    ext = (hull @ np.array([[c, -s], [s, c]]).T).ptp(0)
    if ext[1] > ext[0]:
        best += np.pi / 2

    c, s = np.cos(-best), np.sin(-best)
    T = np.eye(4)
    T[:2, :2] = [[c, -s], [s, c]]
    return T


# ------------------------------------------------------------ surface stage

def poisson_surface(pcd, *, depth: int, density_trim: float, normal_radius: float,
                    normal_nn: int, smooth: int, stats: dict):
    """Screened Poisson with MST-consistent normals, low-density shell trimmed.

    Poisson always returns a closed surface, inventing a shell across unseen
    regions. The density quantile trim is what removes that invention, so it
    matters more than the reconstruction depth.

    Normals are the sensitive part: splat centers sit in a noisy shell several
    cm thick, so a tight PCA radius fits the noise and the locally-flipped
    normals that follow make Poisson bubble. Averaging over a wide radius is
    what turns cauliflower into upholstery.
    """
    import open3d as o3d

    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(
        radius=normal_radius, max_nn=normal_nn))
    pcd.orient_normals_consistent_tangent_plane(k=20)

    mesh, density = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=depth, linear_fit=False)
    stats["poisson_faces"] = len(mesh.triangles)

    density = np.asarray(density)
    cut = np.quantile(density, density_trim)
    mesh.remove_vertices_by_mask(density < cut)
    mesh.remove_unreferenced_vertices()
    mesh.remove_degenerate_triangles()
    stats["after_density_trim"] = len(mesh.triangles)

    # Poisson leaves satellite shells around floaters that survived filtering.
    labels, counts, _ = mesh.cluster_connected_triangles()
    labels, counts = np.asarray(labels), np.asarray(counts)
    if len(counts):
        mesh.remove_triangles_by_mask(counts[labels] < counts.max())
        mesh.remove_unreferenced_vertices()
    stats["after_shell_trim"] = len(mesh.triangles)

    if smooth:
        # Taubin, not Laplacian: it low-passes without shrinking the part,
        # and shrinking would corrupt the dimensions this whole tool exists for.
        mesh = mesh.filter_smooth_taubin(number_of_iterations=smooth)
        stats["taubin_iterations"] = smooth
    return mesh


def decimate(mesh, target: int):
    import open3d as o3d  # noqa: F401  (mesh is an open3d type)

    if target and len(mesh.triangles) > target:
        mesh = mesh.simplify_quadric_decimation(int(target))
    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_vertices()
    mesh.remove_non_manifold_edges()
    mesh.compute_vertex_normals()
    return mesh


def to_trimesh(mesh):
    import trimesh

    colors = None
    if mesh.has_vertex_colors():
        colors = (np.asarray(mesh.vertex_colors) * 255).astype(np.uint8)
        colors = np.hstack([colors, np.full((len(colors), 1), 255, np.uint8)])
    return trimesh.Trimesh(vertices=np.asarray(mesh.vertices),
                           faces=np.asarray(mesh.triangles),
                           vertex_colors=colors, process=False)


# ------------------------------------------------------------ measure stage

def measure(tm) -> dict:
    """Dimensions in the levelled/squared frame: X length, Y depth, Z height."""
    lo, hi = tm.bounds
    ext = hi - lo
    labels = ("length_x", "depth_y", "height_z")
    dims = {
        "bbox_mm": {k: round(float(v), 1) for k, v in zip(labels, ext)},
        "bbox_in": {k: round(float(v) / (IN * MM), 2) for k, v in zip(labels, ext)},
        "floor_to_top_mm": round(float(hi[2]), 1),
        "origin_corner_mm": [round(float(v), 1) for v in lo],
        "volume_l": round(float(tm.volume) / 1e6, 2) if tm.is_volume else None,
        "watertight": bool(tm.is_watertight),
        "faces": int(len(tm.faces)),
    }
    dims["summary_in"] = "{length_x} in × {depth_y} in × {height_z} in".format(
        **{k: f"{v:.2f}" for k, v in dims["bbox_in"].items()})
    return dims


def seat_height(tm, *, band: float = 25.0) -> dict | None:
    """Largest horizontal ledge below mid-height — the seat, for a couch.

    Found from the histogram of upward-facing face area by height: a seat is
    the tallest peak of horizontal area that is not the floor or the backrest.
    """
    up = tm.face_normals[:, 2] > 0.85
    if up.sum() < 20:
        return None
    z = tm.triangles_center[up, 2]
    area = tm.area_faces[up]
    top = tm.bounds[1][2]
    sel = (z > 0.15 * top) & (z < 0.70 * top)
    if sel.sum() < 20:
        return None
    bins = np.arange(z[sel].min(), z[sel].max() + band, band)
    hist, _ = np.histogram(z[sel], bins=bins, weights=area[sel])
    if not len(hist) or hist.max() <= 0:
        return None
    k = int(np.argmax(hist))
    return {
        "seat_height_mm": round(float(0.5 * (bins[k] + bins[k + 1])), 1),
        "seat_height_in": round(float(0.5 * (bins[k] + bins[k + 1])) / (IN * MM), 2),
        "horizontal_area_m2": round(float(hist[k]) / 1e6, 3),
    }


# ------------------------------------------------------------ section stage

# kind → (cut axis, 4×4 rotation into the drawing plane, 2D axis labels).
# The rotations are chosen so DXF coordinates stay *model* coordinates: import
# a profile into an Onshape sketch and its Y ordinate is height above the floor.
_SECTION_FRAMES = {
    "plan":      (2, [[1, 0, 0], [0, 1, 0], [0, 0, 1]], ("X length", "Y depth"), -1),
    "profile":   (0, [[0, 1, 0], [0, 0, 1], [1, 0, 0]], ("Y depth", "Z height"), -1),
    "elevation": (1, [[1, 0, 0], [0, 0, 1], [0, -1, 0]], ("X length", "Z height"), +1),
}


def export_sections(tm, out_dir: Path, name: str, *, n_each: int = 3) -> list[dict]:
    """Slice the mesh and write DXF profiles — the CAD-native way to measure.

    An imported mesh is awkward to dimension in Onshape; a DXF drops straight
    into a sketch where every curve is snappable, dimensionable, and can be
    traced with real splines to rebuild the shape as parametric CAD.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    lo, hi = tm.bounds
    written: list[dict] = []

    for kind, (axis, rot, labels, sign) in _SECTION_FRAMES.items():
        normal = np.zeros(3)
        normal[axis] = 1.0
        for value in np.linspace(lo[axis], hi[axis], n_each + 2)[1:-1]:
            to_2d = np.eye(4)
            to_2d[:3, :3] = rot
            to_2d[2, 3] = sign * value  # cut plane → drawing plane z = 0

            try:
                section = tm.section(plane_origin=normal * value, plane_normal=normal)
                if section is None:
                    continue
                flatten = getattr(section, "to_2D", None) or section.to_planar
                planar = flatten(to_2D=to_2d, check=False)[0]
                path = out_dir / f"{name}_{kind}_{value:07.1f}mm.dxf"
                planar.export(str(path))
            except Exception:
                continue
            written.append({
                "file": str(path),
                "kind": kind,
                "cut": f"{'XYZ'[axis]} = {value:.1f} mm",
                "dxf_axes": list(labels),
                "curves": int(len(planar.entities)),
                "extent_mm": [round(float(v), 1) for v in planar.extents],
            })
    return written


def cloud_views(pcd, path: Path, title: str, *, grid: float = 0.5) -> Path:
    """Dimensioned plan/front/side scatter of a levelled cloud — the crop drawing."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = np.asarray(pcd.points)
    col = np.asarray(pcd.colors) if pcd.has_colors() else None
    keep = pts[:, 2] > 0.05  # hide the floor sheet; it hides the object under it
    if keep.sum() < 100:
        keep = np.ones(len(pts), bool)
    pts, col = pts[keep], (col[keep] if col is not None else None)

    fig, axes = plt.subplots(1, 3, figsize=(21, 7))
    for ax, (i, j, name) in zip(axes, [(0, 1, "PLAN x-y"), (0, 2, "FRONT x-z"),
                                       (1, 2, "SIDE y-z")]):
        if col is not None:
            ax.scatter(pts[:, i], pts[:, j], s=0.4, c=col)
        else:
            ax.scatter(pts[:, i], pts[:, j], s=0.4, c=pts[:, 2], cmap="viridis")
        ax.set_aspect("equal")
        ax.grid(alpha=0.35, lw=0.5)
        ax.xaxis.set_major_locator(plt.MultipleLocator(grid))
        ax.yaxis.set_major_locator(plt.MultipleLocator(grid))
        ax.set_title(f"{name}   ({title})", fontsize=10)
        ax.set_xlabel(f"{'xyz'[i]} (m)")
        ax.set_ylabel(f"{'xyz'[j]} (m)")
    fig.tight_layout()
    fig.savefig(path, dpi=70)
    plt.close(fig)
    return path


def mesh_views(tm, path: Path) -> Path:
    """Shaded orthographic elevations of the finished part, in mm."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    tri, normals = tm.vertices[tm.faces], tm.face_normals
    fig, axes = plt.subplots(1, 3, figsize=(21, 7))
    views = [(0, 2, 1, "FRONT x-z  (length × height)"),
             (1, 2, 0, "SIDE y-z  (depth × height)"),
             (0, 1, 2, "PLAN x-y  (length × depth)")]
    for ax, (i, j, k, name) in zip(axes, views):
        order = np.argsort(tri[:, :, k].mean(1))  # painter's algorithm
        shade = np.clip(0.25 + 0.75 * np.abs(normals[:, k]), 0, 1)[order]
        ax.add_collection(PolyCollection(tri[order][:, :, [i, j]],
                                         facecolors=plt.cm.gray(shade),
                                         edgecolors="none"))
        ax.autoscale()
        ax.set_aspect("equal")
        ax.grid(alpha=0.25, lw=0.5)
        ax.xaxis.set_major_locator(plt.MultipleLocator(200))
        ax.yaxis.set_major_locator(plt.MultipleLocator(200))
        ax.set_title(name, fontsize=10)
        ax.set_xlabel("mm")
    fig.tight_layout()
    fig.savefig(path, dpi=70)
    plt.close(fig)
    return path


def scale_check(out_dir: Path) -> Path:
    """A 100 mm cube: import it beside the part to confirm units survived."""
    import trimesh

    cube = trimesh.creation.box(extents=(100.0, 100.0, 100.0))
    cube.apply_translation((50.0, 50.0, 50.0))
    path = out_dir / "scale_check_100mm.stl"
    cube.export(str(path))
    return path


# -------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("splat", type=Path, help="trained gaussian PLY (3DGS/3DGUT)")
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument("--name", default="part", help="basename for exported files")

    ap.add_argument("--crop", help="levelled-metre box 'x0,y0,z0,x1,y1,z1' "
                                   "(read it off views_levelled.png)")
    ap.add_argument("--min-opacity", type=float, default=0.25,
                    help="low-opacity gaussians are haze, not surface")
    ap.add_argument("--max-gaussian-m", type=float, default=0.10,
                    help="drop balloon gaussians larger than this (m)")
    ap.add_argument("--voxel", type=float, default=0.02, help="downsample voxel (m)")
    ap.add_argument("--outlier-neighbors", type=int, default=12)
    ap.add_argument("--outlier-radius", type=float, default=0.0,
                    help="floater-rejection radius (m); 0 = 4× voxel")

    ap.add_argument("--floor-clearance", type=float, default=0.03,
                    help="ignore points within this height of the floor (m)")
    ap.add_argument("--cluster-eps", type=float, default=0.0,
                    help="DBSCAN radius (m); 0 = 4× voxel")
    ap.add_argument("--cluster-min-points", type=int, default=20)
    ap.add_argument("--no-square-up", action="store_true",
                    help="keep world XY instead of aligning to the footprint box")

    ap.add_argument("--poisson-depth", type=int, default=8)
    ap.add_argument("--density-trim", type=float, default=0.06,
                    help="drop this quantile of lowest-confidence Poisson vertices")
    ap.add_argument("--normal-radius", type=float, default=0.09,
                    help="PCA radius for normals (m); wide beats tight on splats")
    ap.add_argument("--normal-nn", type=int, default=60)
    ap.add_argument("--smooth", type=int, default=10,
                    help="Taubin smoothing iterations (0 = off)")
    ap.add_argument("--target-faces", type=int, default=150_000)
    ap.add_argument("--lite-faces", type=int, default=25_000,
                    help="also write a light mesh for responsive CAD picking (0 = skip)")
    ap.add_argument("--sections", type=int, default=3,
                    help="DXF slices per axis (0 = skip)")
    args = ap.parse_args()

    import open3d as o3d

    t0 = time.time()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    stats: dict = {}

    radius = args.outlier_radius or args.voxel * 4
    eps = args.cluster_eps or args.voxel * 4

    print(f"[1/7] read {args.splat}")
    g = read_gaussian_ply(args.splat)
    print(f"      {g['n']:,} gaussians")

    print("[2/7] filter")
    xyz, rgb, fstats = filter_gaussians(
        g, min_opacity=args.min_opacity, max_size=args.max_gaussian_m)
    stats.update(fstats)
    pcd = dense_core(xyz, rgb, voxel=args.voxel, nb_points=args.outlier_neighbors,
                     radius=radius, stats=stats)
    print(f"      kept {stats['n_kept']:,} → voxel {stats['n_voxel']:,} "
          f"→ dense {stats['n_dense']:,}")

    print("[3/7] level to floor")
    pts = np.asarray(pcd.points)
    up, offset, floor = find_floor(pts)
    stats["floor"] = floor
    T = level_transform(up, offset)
    pcd.transform(T)
    print(f"      up={floor['plane_normal']} floor={floor['floor_height_m']} m "
          f"tilt={floor['tilt_from_axis_deg']}°")
    views_levelled = cloud_views(pcd, out / "views_levelled.png",
                                 "levelled scene — read the --crop box here")

    print("[4/7] isolate object")
    if args.crop:
        pcd = crop_box(pcd, args.crop, stats)
        print(f"      crop → {stats['n_cropped']:,} points")
    obj = isolate_object(pcd, floor_clearance=args.floor_clearance,
                         eps=eps, min_points=args.cluster_min_points, stats=stats)
    print(f"      above floor {stats['n_above_floor']:,} → "
          f"largest cluster {stats.get('largest_cluster', 0):,} "
          f"of {stats.get('clusters', 0)}")
    if len(obj.points) < 500:
        print(f"ERROR: object cluster has {len(obj.points)} points — widen --crop, "
              f"raise --cluster-eps, or lower --min-opacity", file=sys.stderr)
        print(f"       see {views_levelled}", file=sys.stderr)
        return 2

    if not args.no_square_up:
        R = square_up(np.asarray(obj.points))
        obj.transform(R)
        T = R @ T
    # Drop the part onto the origin corner so CAD datum planes are useful.
    p = np.asarray(obj.points)
    shift = np.array([-p[:, 0].min(), -p[:, 1].min(), 0.0])
    obj.translate(shift)
    T[:3, 3] += shift
    stats["world_to_cad"] = np.round(T, 6).tolist()

    print("[5/7] poisson surface")
    mesh = poisson_surface(obj, depth=args.poisson_depth,
                           density_trim=args.density_trim,
                           normal_radius=args.normal_radius,
                           normal_nn=args.normal_nn, smooth=args.smooth,
                           stats=stats)
    print(f"      {stats['poisson_faces']:,} → trim {stats['after_density_trim']:,} "
          f"→ shell {stats['after_shell_trim']:,}")

    print("[6/7] export")
    exports: dict = {}
    full = None
    for tag, target in (("", args.target_faces), ("_lite", args.lite_faces)):
        if target <= 0 and tag:
            continue  # --lite-faces 0 skips the light copy; the part always ships
        m = decimate(o3d.geometry.TriangleMesh(mesh), target)
        tm = to_trimesh(m)
        tm.apply_scale(MM)  # meters → millimeters for CAD
        base = out / f"{args.name}{tag}_mm"
        tm.export(str(base.with_suffix(".stl")))
        tm.export(str(base.with_suffix(".obj")))
        exports[f"stl{tag}"] = str(base.with_suffix(".stl"))
        exports[f"obj{tag}"] = str(base.with_suffix(".obj"))
        if not tag:
            full = tm
        print(f"      {base.name}: {len(tm.faces):,} faces")

    # Metric copies for simulation and perception. These stay in METERS and
    # Z-up: sim ingest (USD/MJCF/URDF via assetpipe.digitalize.sim_export)
    # wants metres, and the part is already levelled with its base on z = 0.
    sim = to_trimesh(decimate(o3d.geometry.TriangleMesh(mesh),
                              args.lite_faces or args.target_faces))
    sim.export(str(out / f"{args.name}.glb"))
    sim.export(str(out / f"{args.name}_m.ply"))
    sim.export(str(out / f"{args.name}_m.obj"))
    exports["glb_meters"] = str(out / f"{args.name}.glb")
    exports["ply_meters"] = str(out / f"{args.name}_m.ply")
    exports["obj_meters"] = str(out / f"{args.name}_m.obj")

    cloud = out / f"{args.name}_cloud_m.ply"
    o3d.io.write_point_cloud(str(cloud), obj)
    exports["cloud_ply_meters"] = str(cloud)
    stats["cloud_points"] = len(obj.points)

    exports["scale_check"] = str(scale_check(out))

    dims = measure(full)
    seat = seat_height(full)
    if seat:
        dims["seat"] = seat

    print("[7/7] sections")
    sections = export_sections(full, out / "sections", args.name,
                               n_each=args.sections) if args.sections else []
    print(f"      {len(sections)} DXF profiles")

    exports["views_levelled"] = str(views_levelled)
    exports["views_part"] = str(mesh_views(full, out / "views_part.png"))

    report = {
        "source_splat": str(args.splat),
        "units": {"mesh_files": "mm", "glb": "m", "dxf": "mm"},
        "scale_provenance": "inherited from the trained splat "
                            "(3DGUT on ARKit metric poses → meters)",
        "frame": "X=length, Y=depth, Z=height; floor at z=0; part on the origin corner",
        "dims": dims,
        "exports": exports,
        "sections": sections,
        "pipeline": stats,
        "seconds": round(time.time() - t0, 1),
    }
    (out / "cad_report.json").write_text(json.dumps(report, indent=2))

    print()
    print(f"  {dims['summary_in']}   ({dims['bbox_mm']})")
    if seat:
        print(f"  seat height  {seat['seat_height_mm']} mm "
              f"({seat['seat_height_in']} in)")
    print(f"  → {out}/cad_report.json   [{report['seconds']}s]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
