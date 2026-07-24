"""Automatic background removal — turn a scene scan into an ASSET.

A raw scan contains the object *and* everything around it: the floor, SfM
speckle, bits of wall. This stage strips that automatically (the Scaniverse
auto-crop equivalent), no manual box-cropping:

    1. outliers   isolated points (sparse-SfM speckle) via a voxel grid
    2. ground     dominant plane found by RANSAC, removed with everything
                  hanging below it
    3. clutter    remaining points are clustered (26-connected voxels);
                  only the main cluster(s) near the middle survive

Pure stdlib, thresholds scale with the cloud's own size — no tuning needed
for "phone-video of a shoebox" vs "room sweep". All knobs overridable.
"""

from __future__ import annotations

import math
import random
from collections import deque

from .backends import ScenePointCloud

Vec3 = tuple[float, float, float]


def _bbox_diag(xyz: list[Vec3]) -> float:
    """Bounding-box diagonal of the 2–98 percentile core — far speckle must
    not inflate the scale every threshold derives from."""
    n = len(xyz)
    lo, hi = [], []
    for k in range(3):
        vals = sorted(p[k] for p in xyz)
        lo.append(vals[int(0.02 * (n - 1))])
        hi.append(vals[int(0.98 * (n - 1))])
    return math.dist(lo, hi) or 1.0


def _voxel_map(xyz: list[Vec3], size: float) -> dict[tuple, list[int]]:
    vox: dict[tuple, list[int]] = {}
    for i, (x, y, z) in enumerate(xyz):
        vox.setdefault((int(x // size), int(y // size), int(z // size)), []).append(i)
    return vox


_NEIGHBORS = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
              for dz in (-1, 0, 1) if (dx, dy, dz) != (0, 0, 0)]


def _drop_isolated(xyz: list[Vec3], keep: list[bool], voxel: float) -> int:
    """Drop points alone in their voxel with no occupied neighbor voxel."""
    vox = _voxel_map([p for i, p in enumerate(xyz) if keep[i]], voxel)
    # map back to original indices
    idx_of = [i for i in range(len(xyz)) if keep[i]]
    dropped = 0
    for key, members in vox.items():
        if len(members) > 1:
            continue
        if any((key[0] + d[0], key[1] + d[1], key[2] + d[2]) in vox
               for d in _NEIGHBORS):
            continue
        keep[idx_of[members[0]]] = False
        dropped += 1
    return dropped


def _ransac_plane(xyz: list[Vec3], keep: list[bool], dist: float,
                  iters: int = 300, sample_cap: int = 3000,
                  min_inlier_frac: float = 0.2):
    """Best plane (unit normal n, offset d) with n·p+d≈0, or None."""
    idx = [i for i in range(len(xyz)) if keep[i]]
    if len(idx) < 30:
        return None
    rng = random.Random(0)
    sample = rng.sample(idx, min(sample_cap, len(idx)))
    best, best_n, best_d = 0, None, 0.0
    for _ in range(iters):
        i, j, k = (xyz[a] for a in rng.sample(sample, 3))
        u = (j[0] - i[0], j[1] - i[1], j[2] - i[2])
        v = (k[0] - i[0], k[1] - i[1], k[2] - i[2])
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0])
        nn = math.hypot(*n)
        if nn < 1e-12:
            continue
        n = (n[0] / nn, n[1] / nn, n[2] / nn)
        d = -(n[0] * i[0] + n[1] * i[1] + n[2] * i[2])
        hits = sum(
            1 for a in sample
            if abs(n[0] * xyz[a][0] + n[1] * xyz[a][1] + n[2] * xyz[a][2] + d) < dist
        )
        if hits > best:
            best, best_n, best_d = hits, n, d
    if best_n is None or best < min_inlier_frac * len(sample):
        return None
    return best_n, best_d


def _components(xyz: list[Vec3], keep: list[bool], voxel: float) -> list[list[int]]:
    """Connected components (26-connectivity on the voxel grid) of kept points."""
    pts = [i for i in range(len(xyz)) if keep[i]]
    vox = _voxel_map([xyz[i] for i in pts], voxel)
    comps, seen = [], set()
    for start in vox:
        if start in seen:
            continue
        comp_keys, queue = [], deque([start])
        seen.add(start)
        while queue:
            key = queue.popleft()
            comp_keys.append(key)
            for d in _NEIGHBORS:
                nk = (key[0] + d[0], key[1] + d[1], key[2] + d[2])
                if nk in vox and nk not in seen:
                    seen.add(nk)
                    queue.append(nk)
        comps.append([pts[m] for k in comp_keys for m in vox[k]])
    return sorted(comps, key=len, reverse=True)


def clean_cloud(
    cloud: ScenePointCloud,
    remove_ground: bool = True,
    plane_dist: float | None = None,   # default: bbox diagonal / 150
    cluster_voxel: float | None = None,  # default: bbox diagonal / 40
    min_cluster_frac: float = 0.25,    # keep clusters ≥ this × largest
    max_planes: int = 1,
) -> ScenePointCloud:
    """Return a new cloud with background removed; ``stats`` says what went."""
    xyz, rgb = cloud.xyz, cloud.rgb
    if len(xyz) < 50:  # too small to segment meaningfully
        return ScenePointCloud(list(xyz), list(rgb),
                               stats={**cloud.stats, "clean": "skipped (tiny)"})
    diag = _bbox_diag(xyz)
    plane_dist = plane_dist if plane_dist is not None else diag / 150
    cluster_voxel = cluster_voxel if cluster_voxel is not None else diag / 40
    keep = [True] * len(xyz)
    stats = dict(cloud.stats)
    stats["raw_points"] = len(xyz)

    stats["outliers_removed"] = _drop_isolated(xyz, keep, diag / 80)

    planes_removed = 0
    for _ in range(max_planes if remove_ground else 0):
        fit = _ransac_plane(xyz, keep, plane_dist)
        if fit is None:
            break
        n, d = fit
        side = [n[0] * p[0] + n[1] * p[1] + n[2] * p[2] + d for p in xyz]
        inlier = [abs(s) < plane_dist for s in side]
        outside = [i for i in range(len(xyz)) if keep[i] and not inlier[i]]
        if len(outside) < 0.15 * sum(keep):  # scene ≈ the plane; keep it
            break
        # everything "hanging under" the ground goes too
        above = sum(1 for i in outside if side[i] > 0)
        sign = 1.0 if above >= len(outside) / 2 else -1.0
        for i in range(len(xyz)):
            if keep[i] and (inlier[i] or side[i] * sign < -plane_dist):
                keep[i] = False
                planes_removed += 1
    stats["ground_removed"] = planes_removed

    comps = _components(xyz, keep, cluster_voxel)
    if comps:
        threshold = max(1, int(min_cluster_frac * len(comps[0])))
        chosen = {i for comp in comps if len(comp) >= threshold for i in comp}
        stats["clutter_removed"] = sum(keep) - len(chosen)
        stats["clusters_kept"] = sum(1 for c in comps if len(c) >= threshold)
        keep = [i in chosen for i in range(len(xyz))]

    out_xyz = [p for i, p in enumerate(xyz) if keep[i]]
    out_rgb = [c for i, c in enumerate(rgb) if keep[i]]
    stats["points"] = len(out_xyz)
    return ScenePointCloud(out_xyz, out_rgb, stats=stats)
