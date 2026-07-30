"""Analyze RGB-D session → pick best frame(s) → preprocess → object asset.

One good frame beat heavy TSDF/3DGUT on phone LiDAR. This module:

1. Scores every Nth frame (sharpness, valid depth, object-sized center depth)
2. Keeps only the top ``max_frames`` (default **1**; raise to 2–3 if needed)
3. Back-projects RGB+depth, drops floor plane, keeps largest cluster
4. Writes a small asset folder: ``object.ply``, ``view.html``, ``OPEN_ME.html``, …

No COLMAP / TSDF / 3DGUT.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Optional

import numpy as np


@dataclass
class FrameScore:
    index: int
    score: float
    sharpness: float
    valid_depth_frac: float
    center_depth_m: float
    reason: str = ""


def _sharpness_gray(rgb: np.ndarray) -> float:
    """Laplacian variance on a downscaled luma — motion-blur proxy."""
    from PIL import Image

    g = np.asarray(
        Image.fromarray(rgb).convert("L").resize((160, 120), Image.BILINEAR),
        dtype=np.float32,
    )
    # simple 2nd difference
    lap = (
        -4 * g[1:-1, 1:-1]
        + g[:-2, 1:-1]
        + g[2:, 1:-1]
        + g[1:-1, :-2]
        + g[1:-1, 2:]
    )
    return float(lap.var())


def score_frames(session, stride: int = 3) -> list[FrameScore]:
    """Rank frames; higher score = better single-view object capture."""
    from PIL import Image

    scores: list[FrameScore] = []
    for i in range(0, session.n_frames, max(1, stride)):
        fr = session.frames[i]
        depth_mm = np.array(Image.open(fr.depth_path))
        color = np.asarray(Image.open(fr.color_path).convert("RGB"))
        H, W = depth_mm.shape[:2]
        valid = depth_mm > 50
        vfrac = float(valid.mean())
        if vfrac < 0.4:
            continue
        # center patch depth (object usually framed mid-frame)
        cy, cx = H // 2, W // 2
        patch = depth_mm[cy - H // 6 : cy + H // 6, cx - W // 6 : cx + W // 6]
        pv = patch[patch > 50]
        if len(pv) < 80:
            continue
        cmed = float(np.median(pv)) * 0.001
        # sweet spot ~0.4–1.2 m for phone LiDAR objects
        if not (0.25 < cmed < 1.8):
            continue
        sharp = _sharpness_gray(color)
        # depth should vary in center (object stands off floor) but not chaotic
        pstd = float(np.std(pv)) * 0.001
        # score: sharp + good distance + some relief + lots of valid depth
        dist_bonus = 1.0 - min(abs(cmed - 0.65) / 0.65, 1.0)
        relief = min(pstd / 0.08, 1.0)
        score = sharp * (0.4 + 0.6 * dist_bonus) * (0.5 + 0.5 * vfrac) * (0.7 + 0.3 * relief)
        scores.append(
            FrameScore(
                index=i,
                score=score,
                sharpness=sharp,
                valid_depth_frac=vfrac,
                center_depth_m=cmed,
                reason=f"sharp={sharp:.0f} d={cmed:.2f}m valid={vfrac:.0%}",
            )
        )
    scores.sort(key=lambda s: s.score, reverse=True)
    return scores


def _backproject_object(
    frame,
    *,
    depth_lo_extra: float = 0.28,
    depth_hi_extra: float = 0.35,
) -> tuple[np.ndarray, np.ndarray]:
    """Camera-frame XYZ + RGB for near object slab; empty if failed."""
    from PIL import Image

    depth = np.array(Image.open(frame.depth_path)).astype(np.float32) * 0.001
    color = np.asarray(
        Image.open(frame.color_path)
        .convert("RGB")
        .resize((depth.shape[1], depth.shape[0]), Image.BILINEAR)
    )
    fx, fy, cx, cy = frame.intrinsics
    H, W = depth.shape
    us, vs = np.meshgrid(np.arange(W), np.arange(H))
    z = depth
    cy0, cx0 = H // 2, W // 2
    center = z[cy0 - 40 : cy0 + 40, cx0 - 40 : cx0 + 40]
    cv = center[center > 0.05]
    if len(cv) < 20:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.uint8)
    cmed = float(np.median(cv))
    # Wide slab: upright bottles need headroom toward camera + floor margin
    relief = float(np.std(cv))
    lo_e = float(np.clip(max(depth_lo_extra, relief * 2.0), 0.18, 0.40))
    hi_e = float(np.clip(max(depth_hi_extra, relief * 2.5), 0.22, 0.50))
    mask = (z >= max(0.05, cmed - lo_e)) & (z <= cmed + hi_e) & (z > 0.05)
    # Keep most of the frame — object framing varies
    mask &= (us > 0.05 * W) & (us < 0.95 * W) & (vs > 0.02 * H) & (vs < 0.98 * H)
    if mask.sum() < 50:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.uint8)
    x = (us - cx) * z / fx
    y = (vs - cy) * z / fy
    xyz = np.stack([x[mask], y[mask], z[mask]], axis=1)
    rgb = color[mask]
    return xyz, rgb


def _plane_and_cluster(
    xyz: np.ndarray, rgb: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Drop dominant floor plane; keep object dense (avoid over-thinning)."""
    import open3d as o3d

    if len(xyz) < 50:
        return xyz, rgb
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(xyz)
    pcd.colors = o3d.utility.Vector3dVector(rgb.astype(np.float64) / 255.0)
    # Mild outlier only
    cleaned, _ = pcd.remove_statistical_outlier(nb_neighbors=30, std_ratio=2.5)
    if len(cleaned.points) >= max(40, int(0.3 * len(pcd.points))):
        pcd = cleaned
    n0 = len(pcd.points)
    plane_model, inliers = pcd.segment_plane(
        distance_threshold=0.015, ransac_n=3, num_iterations=600
    )
    _ = plane_model
    # Only strip plane if it looks like a large floor (not the object itself)
    if len(inliers) >= int(0.25 * n0) and (n0 - len(inliers)) >= 200:
        obj = pcd.select_by_index(inliers, invert=True)
    else:
        obj = pcd
    # Soft cluster: merge nearby scraps; if it nukes most points, keep all
    labels = np.array(
        obj.cluster_dbscan(eps=0.05, min_points=12, print_progress=False)
    )
    if labels.max() >= 0:
        valid = labels >= 0
        counts = np.bincount(labels[valid])
        keep_id = int(np.argmax(counts))
        kept = obj.select_by_index(np.where(labels == keep_id)[0])
        # Refuse to keep a tiny shard of a dense cloud
        if len(kept.points) >= max(200, int(0.15 * len(obj.points))):
            obj = kept
    xyz_o = np.asarray(obj.points)
    rgb_o = (np.asarray(obj.colors) * 255).astype(np.uint8)
    return xyz_o, rgb_o


def _camera_forward(pose: list[float]) -> np.ndarray:
    return np.asarray(pose, dtype=np.float64).reshape(4, 4)[:3, 2]


def _angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.degrees(np.arccos(np.clip(np.dot(a, b), -1.0, 1.0))))


def pick_diverse_frames(
    session,
    ranked: list[FrameScore],
    *,
    max_frames: int = 10,
    min_deg: float = 12.0,
    score_floor_frac: float = 0.4,
    index_window: int = 90,
) -> tuple[list[int], str]:
    """Best frame + nearby views with different camera headings.

    Prefer frames close in time to the best (ARKit drift is smaller), then
    fall back to the global ranked list if we still need coverage.
    """
    if not ranked:
        mid = session.n_frames // 2
        return [mid], "fallback mid-session"
    best = ranked[0]
    chosen = [best.index]
    note = f"best={best.index} score={best.score:.0f}"
    floor = score_floor_frac * best.score

    def try_add(cands: list[FrameScore]) -> None:
        for cand in cands:
            if len(chosen) >= max_frames:
                return
            if cand.index in chosen or cand.score < floor:
                continue
            fwd_c = _camera_forward(session.frames[cand.index].pose)
            if any(
                _angle_deg(fwd_c, _camera_forward(session.frames[j].pose)) < min_deg
                for j in chosen
            ):
                continue
            chosen.append(cand.index)

    # Pass 1: around the best frame (low drift)
    near = [
        r
        for r in ranked
        if abs(r.index - best.index) <= index_window and r.index != best.index
    ]
    try_add(near)
    # Pass 2: global ranked if still thin
    if len(chosen) < max_frames:
        try_add(ranked[1:])

    if len(chosen) > 1:
        note = (
            f"{len(chosen)} views ≥{min_deg:.0f}° "
            f"(anchor={best.index}, window=±{index_window})"
        )
    return chosen, note


def _poisson_rigid_mesh(
    xyz: np.ndarray, rgb: np.ndarray, out_dir: str
) -> dict[str, Any]:
    """Build a closed-ish triangle mesh (rigid body) from the fused cloud."""
    import open3d as o3d

    from .mesh import cloud_to_mesh

    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(xyz))
    pcd.colors = o3d.utility.Vector3dVector(rgb.astype(np.float64) / 255.0)
    pcd.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=0.02, max_nn=40)
    )
    try:
        pcd.orient_normals_consistent_tangent_plane(20)
    except Exception:  # noqa: BLE001
        pass
    mesh, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=8
    )
    dens = np.asarray(dens)
    # Only trim the wildest Poisson halo — keep the closed rigid body
    # (aggressive density cuts remove the unseen "other half").
    mesh.remove_vertices_by_mask(dens < np.quantile(dens, 0.01))
    bbox = pcd.get_axis_aligned_bounding_box().scale(1.25, pcd.get_center())
    mesh = mesh.crop(bbox)
    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_triangles()
    mesh.remove_duplicated_vertices()
    mesh.remove_non_manifold_edges()
    mesh.compute_vertex_normals()

    ply = os.path.join(out_dir, "object_mesh.ply")
    o3d.io.write_triangle_mesh(ply, mesh)

    glb = os.path.join(out_dir, "object_mesh.glb")
    try:
        xyz_l = [tuple(map(float, p)) for p in xyz]
        rgb_l = [tuple(map(int, c)) for c in rgb]
        cloud_to_mesh(xyz_l, rgb_l, glb, method="poisson", poisson_depth=8)
    except Exception:  # noqa: BLE001
        glb_path = None
    else:
        glb_path = glb if os.path.isfile(glb) else None

    n_samp = min(40_000, max(8_000, len(mesh.triangles) // 2))
    samp = mesh.sample_points_uniformly(number_of_points=n_samp)
    sx = np.asarray(samp.points)
    tree = o3d.geometry.KDTreeFlann(pcd)
    sc = np.zeros((len(sx), 3), dtype=np.uint8)
    for i, p in enumerate(sx):
        _, idx, _ = tree.search_knn_vector_3d(p, 1)
        sc[i] = rgb[idx[0]]

    return {
        "glb": glb_path,
        "ply": ply,
        "faces": int(len(mesh.triangles)),
        "verts": int(len(mesh.vertices)),
        "method": "poisson",
        "sample_xyz": sx,
        "sample_rgb": sc,
    }


def _align_to_anchor(
    xyz: np.ndarray,
    xyz_anchor: np.ndarray,
    *,
    T_pose: np.ndarray,
) -> Optional[np.ndarray]:
    """Return 4x4 taking ``xyz`` → anchor frame, or None if ICP fails."""
    import open3d as o3d

    if len(xyz) < 40 or len(xyz_anchor) < 40:
        return None
    src = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(xyz))
    dst = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(xyz_anchor))
    # Pose init often fails under ARKit drift — also try centroid align.
    T_center = np.eye(4)
    T_center[:3, 3] = xyz_anchor.mean(0) - xyz.mean(0)
    best = None
    for T0 in (T_center, T_pose):
        reg = o3d.pipelines.registration.registration_icp(
            src,
            dst,
            0.045,
            T0,
            o3d.pipelines.registration.TransformationEstimationPointToPoint(),
            o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=80),
        )
        if best is None or reg.fitness > best[0]:
            best = (reg.fitness, reg.inlier_rmse, reg.transformation)
    if best is None or best[0] < 0.2 or best[1] > 0.02:
        return None
    return best[2]


def build_object_asset(
    session_dir: str,
    out_dir: str,
    *,
    max_frames: int = 10,
    stride: int = 2,
    frame: Optional[int] = None,
) -> dict[str, Any]:
    """Analyze → select ≤max_frames → preprocess → rigid mesh asset."""
    from PIL import Image

    from .io import write_ply, write_splat
    from .measure import measure_geometry
    from .rgbd_session import load_session
    from .viewer import build_scan_viewer

    session = load_session(session_dir)
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    ranked = score_frames(session, stride=stride)
    analysis = {
        "n_session_frames": session.n_frames,
        "n_scored": len(ranked),
        "stride": stride,
        "max_frames_requested": max_frames,
        "top": [asdict(s) for s in ranked[:12]],
    }
    with open(os.path.join(out_dir, "analysis.json"), "w") as fh:
        json.dump(analysis, fh, indent=2)

    if frame is not None:
        chosen = [frame]
        pick_note = f"user frame {frame}"
    else:
        chosen, pick_note = pick_diverse_frames(
            session, ranked, max_frames=max_frames
        )

    clouds: list[tuple[np.ndarray, np.ndarray]] = []
    photos: list[int] = []
    align_log: list[dict[str, Any]] = []
    xyz_anchor: Optional[np.ndarray] = None
    c2w_0: Optional[np.ndarray] = None

    for i, idx in enumerate(chosen):
        fr = session.frames[idx]
        xyz_c, rgb = _backproject_object(fr)
        if len(xyz_c) < 50:
            align_log.append({"frame": idx, "status": "skip_backproj"})
            continue
        xyz_c, rgb = _plane_and_cluster(xyz_c, rgb)
        if len(xyz_c) < 30:
            align_log.append({"frame": idx, "status": "skip_cluster"})
            continue
        if xyz_anchor is None:
            xyz_anchor = xyz_c
            c2w_0 = np.asarray(fr.pose, float).reshape(4, 4)
            clouds.append((xyz_c, rgb))
            photo = Image.open(fr.color_path).convert("RGB")
            photo.save(os.path.join(out_dir, "photo.jpg"), quality=92)
            photos.append(idx)
            align_log.append({"frame": idx, "status": "anchor", "points": len(xyz_c)})
            continue

        assert c2w_0 is not None
        c2w_i = np.asarray(fr.pose, float).reshape(4, 4)
        T_pose = np.linalg.inv(c2w_0) @ c2w_i
        T = _align_to_anchor(xyz_c, xyz_anchor, T_pose=T_pose)
        if T is None:
            align_log.append({"frame": idx, "status": "skip_icp"})
            continue
        ones = np.ones((len(xyz_c), 1))
        xyz0 = (T @ np.concatenate([xyz_c, ones], 1).T).T[:, :3]
        clouds.append((xyz0, rgb))
        photos.append(idx)
        align_log.append(
            {"frame": idx, "status": "merged_icp", "points": int(len(xyz0))}
        )

    analysis["chosen_requested"] = chosen
    analysis["align_log"] = align_log
    analysis["frames_kept"] = photos
    with open(os.path.join(out_dir, "analysis.json"), "w") as fh:
        json.dump(analysis, fh, indent=2)

    if not clouds:
        raise RuntimeError(
            "no usable frames after scoring/preprocess — check depth/lighting"
        )

    xyz = np.concatenate([c[0] for c in clouds], 0)
    rgb = np.concatenate([c[1] for c in clouds], 0)
    # Light clean after multi-view merge — keep density
    if len(clouds) > 1:
        import open3d as o3d

        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(xyz)
        pcd.colors = o3d.utility.Vector3dVector(rgb.astype(np.float64) / 255.0)
        if len(xyz) > 60_000:
            pcd = pcd.voxel_down_sample(0.0025)
        cleaned, _ = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=3.0)
        if len(cleaned.points) >= int(0.4 * len(pcd.points)):
            pcd = cleaned
        xyz = np.asarray(pcd.points)
        rgb = (np.asarray(pcd.colors) * 255).astype(np.uint8)

    # Rigid body: Poisson surface (closed mesh) — primary asset, not a cut shell
    mesh_info: dict[str, Any] = {}
    try:
        mesh_info = _poisson_rigid_mesh(xyz, rgb, out_dir)
        view_xyz = mesh_info.pop("sample_xyz")
        view_rgb = mesh_info.pop("sample_rgb")
        xyz_l = [tuple(map(float, p)) for p in view_xyz]
        rgb_l = [tuple(map(int, c)) for c in view_rgb]
        method = "multi-view+icp+poisson"
    except Exception as e:  # noqa: BLE001 — fall back to points
        mesh_info = {"error": str(e)[:200]}
        xyz_l = [tuple(map(float, p)) for p in xyz]
        rgb_l = [tuple(map(int, c)) for c in rgb]
        method = "rgbd_select+icp+plane_cluster"
        view_xyz, view_rgb = xyz, rgb

    ply = write_ply(os.path.join(out_dir, "object.ply"), xyz_l, rgb_l)
    splat = write_splat(os.path.join(out_dir, "object.splat"), xyz_l, rgb_l)
    # keep raw fused cloud too
    write_ply(
        os.path.join(out_dir, "object_cloud.ply"),
        [tuple(map(float, p)) for p in xyz],
        [tuple(map(int, c)) for c in rgb],
    )

    extent = np.asarray(view_xyz).max(0) - np.asarray(view_xyz).min(0)
    dims = None
    try:
        import open3d as o3d

        measure_src = mesh_info.get("ply") or os.path.join(out_dir, "object.ply")
        dims = measure_geometry(measure_src, units="meters", label="object_asset")
    except Exception:  # noqa: BLE001
        dims = None

    links = {
        "object.ply": "object.ply",
        "object.splat": "object.splat",
        "photo.jpg": "photo.jpg",
    }
    if mesh_info.get("ply"):
        links["object_mesh.ply"] = "object_mesh.ply"
    if mesh_info.get("glb"):
        links["object_mesh.glb"] = "object_mesh.glb"

    viewer = build_scan_viewer(
        os.path.join(out_dir, "view.html"),
        xyz_l,
        rgb_l,
        title="object asset",
        stats={
            "points": len(xyz_l),
            "frames_used": len(photos),
            "frame_ids": photos,
            "method": method,
            "mesh_faces": mesh_info.get("faces"),
        },
        links=links,
    )

    if dims:
        q = dims["aabb"]["inches_0_25"]
        summary = (
            f"{q['length']:.2f} × {q['width']:.2f} × {q['height']:.2f} in @0.25″"
        )
    else:
        summary = f"extent_m={[round(float(e), 3) for e in extent]}"

    # contact sheet of kept views
    try:
        from PIL import Image as PILImage

        thumbs = []
        for idx in photos:
            im = PILImage.open(session.frames[idx].color_path).convert("RGB")
            im.thumbnail((200, 150))
            thumbs.append(im)
        if thumbs:
            sheet = PILImage.new("RGB", (200 * len(thumbs), 150), (14, 17, 23))
            for i, t in enumerate(thumbs):
                sheet.paste(t, (i * 200, 0))
            sheet.save(os.path.join(out_dir, "views_picked.jpg"), quality=90)
    except Exception:  # noqa: BLE001
        pass

    views_html = ""
    if os.path.isfile(os.path.join(out_dir, "views_picked.jpg")):
        views_html = '<p><img src="views_picked.jpg" alt="picked views"></p>'

    mesh_note = ""
    if mesh_info.get("faces"):
        mesh_note = (
            f" · rigid mesh {mesh_info['faces']:,} tris "
            f"(<a href='object_mesh.ply' style='color:#79c0ff'>object_mesh.ply</a>)"
        )

    open_me = os.path.join(out_dir, "OPEN_ME.html")
    with open(open_me, "w") as fh:
        fh.write(
            f"""<!doctype html>
<html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Object asset</title>
<style>
body{{margin:0;font:16px/1.45 system-ui;background:#0e1117;color:#e6edf3}}
.wrap{{max-width:1100px;margin:0 auto;padding:20px}}
a.btn{{display:inline-block;margin:8px 8px 8px 0;padding:12px 18px;background:#238636;
color:#fff;text-decoration:none;border-radius:8px;font-weight:600}}
a.sec{{background:#21262d;border:1px solid #30363d}}
img{{max-width:100%;border-radius:8px;border:1px solid #30363d}}
.dim{{color:#8b949e}} code{{color:#79c0ff}}
</style></head><body><div class=wrap>
<h1>Object asset</h1>
<p class=dim>Analyzed {session.n_frames} frames → used <b>{len(photos)}</b>
({pick_note}). Multi-view ICP + Poisson rigid mesh (not a cut half-shell).</p>
<p>
<a class=btn href="view.html">Open 3D viewer</a>
<a class=btn sec href="object_mesh.ply">object_mesh.ply</a>
<a class=btn sec href="object.ply">object.ply</a>
<a class=btn sec href="analysis.json">analysis.json</a>
</p>
{views_html}
<p><img src="photo.jpg" alt="anchor photo"></p>
<p class=dim>{len(xyz_l):,} display pts · frames {photos} · {summary}{mesh_note}</p>
</div></body></html>
"""
        )

    meta = {
        "kind": "rgbd_object_asset",
        "session": os.path.abspath(session_dir),
        "n_session_frames": session.n_frames,
        "frames_used": photos,
        "pick_note": pick_note,
        "align_log": align_log,
        "points": len(xyz_l),
        "cloud_points": int(len(xyz)),
        "extent_m": [float(e) for e in extent],
        "dims": dims,
        "mesh": {k: v for k, v in mesh_info.items() if k not in ("sample_xyz", "sample_rgb")},
        "artifacts": {
            "ply": ply,
            "splat": splat,
            "mesh_ply": mesh_info.get("ply"),
            "mesh_glb": mesh_info.get("glb"),
            "viewer": viewer,
            "open_me": open_me,
            "photo": os.path.join(out_dir, "photo.jpg"),
            "analysis": os.path.join(out_dir, "analysis.json"),
        },
    }
    with open(os.path.join(out_dir, "asset.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    if dims:
        with open(os.path.join(out_dir, "dims.json"), "w") as fh:
            json.dump({"object": dims, "primary": "object"}, fh, indent=2)

    return meta
