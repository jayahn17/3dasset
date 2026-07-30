"""Persistent per-scene world model: constant 1+1+1 scene updates.

One-shot fuses forget everything between scans. This module gives a scene a
memory: every new RGB-D session is REGISTERED into the scene's canonical
frame and merged into cumulative voxel statistics, so the model grows and
refines instead of restarting —

    capture N  ─▶ register (FPFH+ICP, gravity-aware fallback)
               ─▶ merge   (per-voxel observation counts, session history)
               ─▶ LACK    (single-session / never-seen regions, heatmap)
               ─▶ improve (cumulative TSDF re-fuse over ALL sessions)

Scene store layout (``<scene_dir>/``):
    world.json          sessions, transforms, metrics history
    coverage.npz        voxel keys -> observation count, last session index
    world_cloud.ply     merged downsampled cloud (colored)
    world_mesh.ply      cumulative TSDF mesh over all registered sessions
    lack_map.png        top-down heatmap: green=corroborated, red=single-pass
    lack.json           machine-readable gaps report

Everything open source: Open3D registration/TSDF, numpy, the existing
session loader (which already normalizes pose conventions).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import numpy as np

VOXEL = 0.05  # coverage-accounting voxel (m)


# --------------------------------------------------------------- session cloud
def session_cloud(session, stride_px: int = 6, frame_step: int = 4,
                  depth_max: float = 4.0):
    """Sparse metric cloud (xyz, rgb) in the SESSION's own world frame."""
    from PIL import Image

    pts, cols = [], []
    for fr in session.frames[::max(1, frame_step)]:
        d = np.array(Image.open(fr.depth_path)).astype(np.float32) * 0.001
        c = np.asarray(Image.open(fr.color_path).convert("RGB").resize(
            (d.shape[1], d.shape[0])))
        fx, fy, cx, cy = fr.intrinsics
        H, W = d.shape
        us, vs = np.meshgrid(np.arange(0, W, stride_px),
                             np.arange(0, H, stride_px))
        z = d[vs, us]
        m = (z > 0.25) & (z < depth_max)
        u, v, z = us[m], vs[m], z[m]
        pc = np.stack([(u - cx) * z / fx, (v - cy) * z / fy, z], 1)
        c2w = np.asarray(fr.pose, float).reshape(4, 4)
        h = np.concatenate([pc, np.ones((len(pc), 1))], 1)
        pts.append((c2w @ h.T).T[:, :3])
        cols.append(c[v, u])
    return np.concatenate(pts, 0), np.concatenate(cols, 0)


# --------------------------------------------------------------- registration
def register_clouds(src: np.ndarray, dst: np.ndarray,
                    voxel: float = 0.08) -> tuple[np.ndarray, dict]:
    """T mapping src -> dst. FPFH+RANSAC global init, point-to-plane ICP
    polish; gravity-aware yaw-scan fallback when features fail.

    Both clouds are ARKit-world gravity-aligned (+y up), so the fallback
    reduces to 4-DOF: floor-height Δy, then yaw+XZ by 2D occupancy overlap.
    """
    import open3d as o3d

    def pcd(a):
        p = o3d.geometry.PointCloud()
        p.points = o3d.utility.Vector3dVector(a)
        return p.voxel_down_sample(voxel)

    s, t = pcd(src), pcd(dst)
    for p in (s, t):
        p.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(
            radius=voxel * 3, max_nn=30))

    def fpfh(p):
        return o3d.pipelines.registration.compute_fpfh_feature(
            p, o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 5, max_nn=100))

    T0 = np.eye(4)
    method = "identity"
    try:
        res = o3d.pipelines.registration.registration_ransac_based_on_feature_matching(
            s, t, fpfh(s), fpfh(t), True, voxel * 1.8,
            o3d.pipelines.registration.TransformationEstimationPointToPoint(False),
            3,
            [o3d.pipelines.registration.CorrespondenceCheckerBasedOnEdgeLength(0.9),
             o3d.pipelines.registration.CorrespondenceCheckerBasedOnDistance(voxel * 1.8)],
            o3d.pipelines.registration.RANSACConvergenceCriteria(200_000, 0.999),
        )
        if res.fitness > 0.15:
            T0, method = np.asarray(res.transformation), "fpfh_ransac"
    except Exception:  # noqa: BLE001
        pass

    if method == "identity":
        T0, method = _gravity_yaw_align(np.asarray(s.points),
                                        np.asarray(t.points)), "gravity_yaw"

    icp = o3d.pipelines.registration.registration_icp(
        s, t, voxel * 1.2, T0,
        o3d.pipelines.registration.TransformationEstimationPointToPlane())
    T = np.asarray(icp.transformation)
    return T, {"method": method, "icp_fitness": round(float(icp.fitness), 4),
               "icp_rmse_m": round(float(icp.inlier_rmse), 4)}


def _gravity_yaw_align(src: np.ndarray, dst: np.ndarray,
                       grid: float = 0.15, yaw_step_deg: float = 3.0) -> np.ndarray:
    """4-DOF init for gravity-aligned clouds: Δy from floor percentiles, yaw
    + XZ shift from best 2D occupancy-grid overlap."""
    dy = np.percentile(dst[:, 1], 3) - np.percentile(src[:, 1], 3)

    def grid2d(a):
        k = np.unique(np.floor(a[:, [0, 2]] / grid).astype(np.int64), axis=0)
        return set(map(tuple, k))

    gd = grid2d(dst)
    best = (-1, 0.0, np.zeros(2))
    for yaw in np.arange(0, 360, yaw_step_deg):
        r = np.radians(yaw)
        R = np.array([[np.cos(r), np.sin(r)], [-np.sin(r), np.cos(r)]])
        xz = src[:, [0, 2]] @ R.T
        # coarse translation candidates from centroid shift
        t0 = np.median(dst[:, [0, 2]], 0) - np.median(xz, 0)
        for jx in (-0.5, 0.0, 0.5):
            for jz in (-0.5, 0.0, 0.5):
                t = t0 + np.array([jx, jz])
                k = np.floor((xz + t) / grid).astype(np.int64)
                score = len(set(map(tuple, np.unique(k, axis=0))) & gd)
                if score > best[0]:
                    best = (score, yaw, t)
    _, yaw, t = best
    r = np.radians(yaw)
    T = np.eye(4)
    T[0, 0] = np.cos(r); T[0, 2] = np.sin(r)
    T[2, 0] = -np.sin(r); T[2, 2] = np.cos(r)
    T[0, 3] = t[0]; T[1, 3] = dy; T[2, 3] = t[1]
    return T


# --------------------------------------------------------------- world model
class WorldModel:
    def __init__(self, scene_dir: str):
        self.dir = os.path.abspath(scene_dir)
        os.makedirs(self.dir, exist_ok=True)
        self.meta_path = os.path.join(self.dir, "world.json")
        self.cov_path = os.path.join(self.dir, "coverage.npz")
        self.meta: dict[str, Any] = {"sessions": [], "updates": []}
        self.keys = np.zeros((0, 3), np.int64)
        self.counts = np.zeros((0,), np.int32)
        self.last = np.zeros((0,), np.int16)
        self.colors = np.zeros((0, 3), np.uint8)
        if os.path.isfile(self.meta_path):
            with open(self.meta_path) as fh:
                self.meta = json.load(fh)
            z = np.load(self.cov_path)
            self.keys, self.counts = z["keys"], z["counts"]
            self.last, self.colors = z["last"], z["colors"]

    # ---- lack accounting -------------------------------------------------
    def lack_report(self) -> dict:
        n = len(self.keys)
        single = int((self.counts == 1).sum())
        rep = {
            "voxels_total": n,
            "voxels_single_session": single,
            "corroborated_frac": round(float((self.counts > 1).mean()), 4) if n else 0.0,
            "sessions": len(self.meta["sessions"]),
            "advice": ("re-scan red regions in lack_map.png (seen only once — "
                       "unverified) — coverage grows where green"),
        }
        return rep

    def _render_lack_map(self):
        from PIL import Image

        if not len(self.keys):
            return
        xz = self.keys[:, [0, 2]]
        lo = xz.min(0)
        span = (xz.max(0) - lo + 1)
        scale = max(1, int(np.ceil(span.max() / 900)))
        W, H = int(span[0] // scale + 1), int(span[1] // scale + 1)
        img = np.full((H, W, 3), 24, np.uint8)
        px = (xz - lo) // scale
        single = self.counts == 1
        img[px[~single][:, 1], px[~single][:, 0]] = (60, 200, 90)   # corroborated
        img[px[single][:, 1], px[single][:, 0]] = (230, 70, 60)     # lack: 1 pass
        Image.fromarray(np.kron(img, np.ones((2, 2, 1), np.uint8))).save(
            os.path.join(self.dir, "lack_map.png"))

    # ---- the 1+1+1 update ------------------------------------------------
    def update(self, session_dir: str, refuse_mesh: bool = True) -> dict:
        from .rgbd_session import load_session

        session = load_session(session_dir)
        xyz, rgb = session_cloud(session)
        report: dict[str, Any] = {"session": session.root,
                                  "n_points_in": int(len(xyz))}

        if len(self.keys) == 0:
            T = np.eye(4)
            report["registration"] = {"method": "init", "icp_fitness": 1.0,
                                      "icp_rmse_m": 0.0}
        else:
            world_pts = (self.keys.astype(np.float64) + 0.5) * VOXEL
            T, reg = register_clouds(xyz, world_pts)
            report["registration"] = reg
            h = np.concatenate([xyz, np.ones((len(xyz), 1))], 1)
            xyz = (T @ h.T).T[:, :3]

        # merge voxel stats
        voxels_before = int(len(self.keys))
        sess_idx = len(self.meta["sessions"])
        k_new = np.floor(xyz / VOXEL).astype(np.int64)
        k_new, first_idx = np.unique(k_new, axis=0, return_index=True)
        c_new = rgb[first_idx]

        if voxels_before:
            all_keys = np.ascontiguousarray(
                np.concatenate([self.keys, k_new]))
            view = all_keys.view(
                np.dtype((np.void, all_keys.dtype.itemsize * 3))).ravel()
            _, first_occ, inv = np.unique(view, return_index=True,
                                          return_inverse=True)
            keys_u = all_keys[first_occ]
            n_u = len(keys_u)
            counts_u = np.zeros(n_u, np.int32)
            last_u = np.zeros(n_u, np.int16)
            colors_u = np.zeros((n_u, 3), np.uint8)
            inv_old, inv_new = inv[:voxels_before], inv[voxels_before:]
            # old keys are unique among themselves; plain scatter-assign is safe
            counts_u[inv_old] = self.counts
            last_u[inv_old] = self.last
            colors_u[inv_old] = self.colors
            counts_u[inv_new] += 1          # new keys unique among themselves too
            last_u[inv_new] = sess_idx
            fresh = np.ones(n_u, bool)
            fresh[inv_old] = False           # keep first-seen color for old voxels
            sel = fresh[inv_new]
            colors_u[inv_new[sel]] = c_new[sel]
            self.keys, self.counts = keys_u, counts_u
            self.last, self.colors = last_u, colors_u
        else:
            self.keys = k_new
            self.counts = np.ones(len(k_new), np.int32)
            self.last = np.full(len(k_new), sess_idx, np.int16)
            self.colors = c_new

        report["voxels_added"] = int(len(self.keys)) - voxels_before
        report["voxels_total"] = int(len(self.keys))

        self.meta["sessions"].append({
            "root": session.root, "t": time.time(),
            "T_session_to_world": [float(x) for x in np.asarray(T).ravel()],
        })
        self.meta["updates"].append({k: v for k, v in report.items()
                                     if k != "session"})
        self._render_lack_map()
        report["lack"] = self.lack_report()

        # improve: cumulative TSDF over ALL sessions with corrected transforms
        if refuse_mesh:
            try:
                report["world_mesh"] = self._refuse_all()
            except Exception as e:  # noqa: BLE001
                report["world_mesh_error"] = str(e)[:200]

        self._save()
        with open(os.path.join(self.dir, "lack.json"), "w") as fh:
            json.dump(report["lack"], fh, indent=2)
        return report

    def _refuse_all(self) -> str:
        from .nvblox_fuse import fuse_open3d_tsdf
        from .rgbd_session import RgbdFrame, RgbdSession, load_session
        import open3d as o3d

        frames: list[RgbdFrame] = []
        for i, s in enumerate(self.meta["sessions"]):
            sess = load_session(s["root"])
            T = np.asarray(s["T_session_to_world"], float).reshape(4, 4)
            for fr in sess.frames[::3]:
                c2w = T @ np.asarray(fr.pose, float).reshape(4, 4)
                frames.append(RgbdFrame(
                    frame_id=f"s{i}_{fr.frame_id}", color_path=fr.color_path,
                    depth_path=fr.depth_path, timestamp=fr.timestamp,
                    pose=[float(x) for x in c2w.ravel()],
                    intrinsics=fr.intrinsics, depth_unit=fr.depth_unit))
        merged = RgbdSession(root=self.dir, frames=frames)
        _xyz, _rgb, mesh = fuse_open3d_tsdf(merged, voxel_size=0.01)
        out = os.path.join(self.dir, "world_mesh.ply")
        o3d.io.write_triangle_mesh(out, mesh)
        return out

    def _save(self):
        np.savez_compressed(self.cov_path, keys=self.keys, counts=self.counts,
                            last=self.last, colors=self.colors)
        with open(self.meta_path, "w") as fh:
            json.dump(self.meta, fh, indent=2)
        # merged cloud for viewers
        from .io import write_ply

        pts = (self.keys.astype(np.float64) + 0.5) * VOXEL
        write_ply(os.path.join(self.dir, "world_cloud.ply"),
                  [tuple(map(float, p)) for p in pts],
                  [tuple(map(int, c)) for c in self.colors])
