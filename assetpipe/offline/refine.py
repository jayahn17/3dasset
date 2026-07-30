"""Offline versioned refine: merge backends → hole detect → fill → vNNN."""

from __future__ import annotations

import json
import os
import shutil
from typing import Any, Optional

import numpy as np


def _load_cloud(path: str) -> tuple[np.ndarray, np.ndarray]:
    from ..scene.io import read_ply

    xyz, rgb = read_ply(path)
    return np.asarray(xyz, np.float64), np.asarray(rgb, np.uint8)


def _collect_backend_clouds(backends_root: str) -> list[tuple[str, np.ndarray, np.ndarray, dict]]:
    status_path = os.path.join(backends_root, "status.json")
    status = {}
    if os.path.isfile(status_path):
        with open(status_path) as fh:
            status = json.load(fh)
    out = []
    for name, meta in (status.get("backends") or {}).items():
        if not meta.get("ok"):
            continue
        cloud = meta.get("cloud")
        if not cloud or not os.path.isfile(cloud):
            # try default path
            cand = os.path.join(backends_root, name, "cloud.ply")
            if os.path.isfile(cand):
                cloud = cand
            else:
                continue
        try:
            xyz, rgb = _load_cloud(cloud)
        except Exception:  # noqa: BLE001
            continue
        if len(xyz) < 20:
            continue
        out.append((name, xyz, rgb, meta))
    return out


def _align_to_ref(
    xyz: np.ndarray, rgb: np.ndarray, ref: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Uniform scale + translate so xyz AABB center/diag matches ref (non-metric backends)."""
    if len(xyz) < 10 or len(ref) < 10:
        return xyz, rgb
    c0 = ref.mean(0)
    c1 = xyz.mean(0)
    d0 = float(np.linalg.norm(ref.max(0) - ref.min(0))) or 1.0
    d1 = float(np.linalg.norm(xyz.max(0) - xyz.min(0))) or 1.0
    s = d0 / d1
    aligned = (xyz - c1) * s + c0
    return aligned, rgb


def merge_clouds(
    clouds: list[tuple[str, np.ndarray, np.ndarray, dict]],
    *,
    voxel: float = 0.008,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Merge backend clouds; metric Open3D anchors scale when present."""
    import open3d as o3d

    if not clouds:
        raise RuntimeError("no backend clouds to merge")

    metric = [(n, x, r, m) for n, x, r, m in clouds if m.get("metric")]
    others = [(n, x, r, m) for n, x, r, m in clouds if not m.get("metric")]

    if metric:
        # densest metric as ref
        metric.sort(key=lambda t: -len(t[1]))
        ref_name, ref_xyz, ref_rgb, _ = metric[0]
        parts_xyz = [ref_xyz]
        parts_rgb = [ref_rgb]
        used = [ref_name]
        for n, x, r, _ in metric[1:]:
            parts_xyz.append(x)
            parts_rgb.append(r)
            used.append(n)
        for n, x, r, _ in others:
            ax, ar = _align_to_ref(x, r, ref_xyz)
            parts_xyz.append(ax)
            parts_rgb.append(ar)
            used.append(n)
    else:
        # COLMAP / splat only — take largest as ref
        clouds = sorted(clouds, key=lambda t: -len(t[1]))
        ref_name, ref_xyz, ref_rgb, _ = clouds[0]
        parts_xyz = [ref_xyz]
        parts_rgb = [ref_rgb]
        used = [ref_name]
        for n, x, r, _ in clouds[1:]:
            ax, ar = _align_to_ref(x, r, ref_xyz)
            parts_xyz.append(ax)
            parts_rgb.append(ar)
            used.append(n)

    xyz = np.concatenate(parts_xyz, 0)
    rgb = np.concatenate(parts_rgb, 0)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(xyz)
    pcd.colors = o3d.utility.Vector3dVector(rgb.astype(np.float64) / 255.0)
    pcd = pcd.voxel_down_sample(max(voxel, 1e-4))
    try:
        pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
    except Exception:  # noqa: BLE001
        pass
    xyz = np.asarray(pcd.points)
    rgb = (np.asarray(pcd.colors) * 255).astype(np.uint8)
    return xyz, rgb, {"sources": used, "n_points": len(xyz), "voxel": voxel}


def isolate_object_cloud(
    xyz: np.ndarray, rgb: np.ndarray, focus: float = 0.85
) -> tuple[np.ndarray, np.ndarray]:
    from ..scene.splat import isolate_object

    return isolate_object(xyz, rgb, focus=focus)


def detect_holes(
    xyz: np.ndarray,
    *,
    voxel: float = 0.015,
    empty_neighbor_thresh: int = 3,
) -> dict[str, Any]:
    """Low-density surface voxels = holes (observed bbox occupancy heuristic)."""
    if len(xyz) < 50:
        return {
            "coverage": 0.0,
            "n_empty": 0,
            "n_occupied": 0,
            "hole_centers": [],
            "ok": False,
        }

    lo = xyz.min(0)
    hi = xyz.max(0)
    span = np.maximum(hi - lo, voxel)
    dims = np.ceil(span / voxel).astype(int) + 1
    dims = np.clip(dims, 1, 80)  # cap grid
    # rebuild voxel to fit cap
    voxel_eff = float(np.max(span / np.maximum(dims - 1, 1)))
    keys = np.floor((xyz - lo) / max(voxel_eff, 1e-6)).astype(int)
    keys = np.clip(keys, 0, dims - 1)
    occ = np.zeros(tuple(dims), dtype=np.uint8)
    occ[keys[:, 0], keys[:, 1], keys[:, 2]] = 1

    # surface candidates: occupied with empty 6-neighbors inside bbox
    holes = []
    occupied = int(occ.sum())
    empty_border = 0
    for i, j, k in zip(*np.where(occ == 1)):
        empty_n = 0
        for di, dj, dk in (
            (1, 0, 0),
            (-1, 0, 0),
            (0, 1, 0),
            (0, -1, 0),
            (0, 0, 1),
            (0, 0, -1),
        ):
            ni, nj, nk = i + di, j + dj, k + dk
            if (
                0 <= ni < dims[0]
                and 0 <= nj < dims[1]
                and 0 <= nk < dims[2]
                and occ[ni, nj, nk] == 0
            ):
                empty_n += 1
        if empty_n >= empty_neighbor_thresh:
            empty_border += 1
            if len(holes) < 64:
                holes.append(
                    (
                        float(lo[0] + (i + 0.5) * voxel_eff),
                        float(lo[1] + (j + 0.5) * voxel_eff),
                        float(lo[2] + (k + 0.5) * voxel_eff),
                    )
                )

    # coverage proxy: fraction of occupied vs shell (occupied + border empties)
    denom = occupied + empty_border
    coverage = float(occupied / denom) if denom else 0.0
    return {
        "coverage": round(coverage, 4),
        "n_occupied": occupied,
        "n_empty_border": empty_border,
        "hole_centers": holes,
        "voxel": voxel_eff,
        "ok": True,
    }


def geometric_fill(
    xyz: np.ndarray, rgb: np.ndarray, out_mesh: str
) -> dict[str, Any]:
    """Poisson mesh on object cloud — closes small holes geometrically."""
    from ..scene.mesh import cloud_to_mesh

    xyz_l = [tuple(map(float, p)) for p in xyz]
    rgb_l = [tuple(map(int, c)) for c in rgb]
    try:
        res = cloud_to_mesh(xyz_l, rgb_l, out_mesh, method="auto")
        return {"ok": True, **res}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def generative_fill(
    xyz: np.ndarray,
    rgb: np.ndarray,
    frame_paths: list[str],
    out_glb: str,
    *,
    endpoint: str | None = None,
) -> dict[str, Any]:
    """TRELLIS from RGB frames + orbit renders; best-effort."""
    from ..scene.generate import DEFAULT_ENDPOINT, generate_asset
    from ..scene.splat import render_orbit_views

    view_dir = os.path.join(os.path.dirname(out_glb), "fill_views")
    os.makedirs(view_dir, exist_ok=True)
    paths = list(frame_paths[:8])
    try:
        rendered = render_orbit_views(
            xyz, rgb, os.path.join(view_dir, "orbit"), n_views=6, size=512
        )
        paths = rendered + paths
    except Exception:  # noqa: BLE001
        pass
    if not paths:
        return {"ok": False, "error": "no views for generative fill"}
    try:
        res = generate_asset(
            paths,
            out_glb,
            endpoint=endpoint or DEFAULT_ENDPOINT,
            views=min(4, len(paths)),
        )
        return {"ok": True, **res}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _register_glb_to_cloud(glb: str, xyz: np.ndarray) -> Optional[dict]:
    """Similarity ICP (uniform scale) TRELLIS → measured cloud; rewrite GLB."""
    try:
        import open3d as o3d
        import trimesh
    except ImportError:
        return None
    if not os.path.isfile(glb) or len(xyz) < 50:
        return None
    try:
        m = trimesh.load(glb, force="mesh")
        if isinstance(m, trimesh.Scene):
            m = trimesh.util.concatenate(tuple(m.geometry.values()))
        src_pts, _ = m.sample(15000)
        src = o3d.geometry.PointCloud()
        src.points = o3d.utility.Vector3dVector(src_pts)
        tgt = o3d.geometry.PointCloud()
        tgt.points = o3d.utility.Vector3dVector(xyz)
        src_d = src.voxel_down_sample(0.01)
        tgt_d = tgt.voxel_down_sample(0.01)
        # estimate scale from AABB diags
        s = float(
            np.linalg.norm(np.asarray(tgt_d.get_max_bound()) - np.asarray(tgt_d.get_min_bound()))
            / max(
                np.linalg.norm(
                    np.asarray(src_d.get_max_bound()) - np.asarray(src_d.get_min_bound())
                ),
                1e-6,
            )
        )
        src_d.scale(s, center=src_d.get_center())
        src_d.translate(tgt_d.get_center() - src_d.get_center())
        reg = o3d.pipelines.registration.registration_icp(
            src_d,
            tgt_d,
            0.05,
            np.eye(4),
            o3d.pipelines.registration.TransformationEstimationPointToPoint(),
            o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=80),
        )
        T = reg.transformation
        # apply s then T to mesh vertices
        verts = np.asarray(m.vertices)
        center = verts.mean(0)
        verts = (verts - center) * s + center
        ones = np.ones((len(verts), 1))
        vh = np.concatenate([verts, ones], 1)
        verts2 = (T @ vh.T).T[:, :3]
        m.vertices = verts2
        m.export(glb)
        return {
            "scale": s,
            "fitness": float(reg.fitness),
            "rmse": float(reg.inlier_rmse),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def write_previews(xyz: np.ndarray, rgb: np.ndarray, preview_dir: str) -> list[str]:
    """Honest visual gate — PNG orbit renders."""
    from PIL import Image

    os.makedirs(preview_dir, exist_ok=True)
    paths = []
    # simple orthographic projections along principal axes + diagonal
    if len(xyz) < 3:
        img = Image.new("RGB", (256, 256), (20, 20, 20))
        p = os.path.join(preview_dir, "empty.png")
        img.save(p)
        return [p]

    def _proj(axis_pair, name):
        a0, a1 = axis_pair
        pts = xyz[:, [a0, a1]]
        lo, hi = pts.min(0), pts.max(0)
        span = np.maximum(hi - lo, 1e-6)
        size = 512
        pad = 0.08
        scale = (1 - 2 * pad) * size / span.max()
        mid = (lo + hi) / 2
        uv = (pts - mid) * scale + size / 2
        u = np.clip(uv[:, 0].astype(int), 0, size - 1)
        v = np.clip((size - 1 - uv[:, 1]).astype(int), 0, size - 1)
        canvas = np.zeros((size, size, 3), np.float32)
        cnt = np.zeros((size, size), np.float32)
        cols = rgb.astype(np.float32)
        for i in range(len(u)):
            canvas[v[i], u[i]] += cols[i]
            cnt[v[i], u[i]] += 1
        m = cnt > 0
        canvas[m] /= cnt[m, None]
        # dilate once
        for _ in range(1):
            miss = cnt == 0
            acc = np.zeros_like(canvas)
            n = np.zeros_like(cnt)
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                acc += np.roll(np.roll(canvas, dy, 0), dx, 1)
                n += np.roll(np.roll(cnt, dy, 0), dx, 1)
            fill = miss & (n > 0)
            canvas[fill] = acc[fill] / n[fill, None]
            cnt[fill] = 1
        im = Image.fromarray(canvas.astype(np.uint8))
        p = os.path.join(preview_dir, name)
        im.save(p)
        paths.append(p)

    _proj((0, 1), "view_xy.png")
    _proj((0, 2), "view_xz.png")
    _proj((1, 2), "view_yz.png")
    return paths


def _write_version_html(version_dir: str, meta: dict) -> str:
    prev = meta.get("previews") or []
    imgs = "".join(
        f'<img src="previews/{os.path.basename(p)}" style="width:30%;margin:4px">'
        for p in prev
    )
    path = os.path.join(version_dir, "OPEN_ME.html")
    with open(path, "w") as fh:
        fh.write(
            f"""<!doctype html>
<html><head><meta charset=utf-8><title>{meta.get('version')}</title>
<style>body{{font:14px system-ui;background:#0e1117;color:#e6edf3;padding:20px}}
.dim{{color:#8b949e}} code{{color:#79c0ff}}</style></head><body>
<h1>Refine {meta.get('version')}</h1>
<p class=dim>coverage={meta.get('coverage')} · points={meta.get('n_object_points')}
· fill={meta.get('fill')}</p>
<p>{imgs}</p>
<p class=dim>See <code>qc.json</code> · <code>holes.json</code></p>
</body></html>"""
        )
    return path


def refine_once(
    run_dir: str,
    *,
    version: Optional[str] = None,
    focus: float = 0.85,
    trellis_endpoint: str | None = None,
    try_generative: bool = True,
) -> dict[str, Any]:
    """One refine cycle → versions/vNNN/."""
    from ..preprocess.normalize import load_pack, pack_frame_paths
    from ..scene.io import write_ply

    run_dir = os.path.abspath(run_dir)
    backends_root = os.path.join(run_dir, "backends")
    versions_root = os.path.join(run_dir, "versions")
    os.makedirs(versions_root, exist_ok=True)

    # next version id
    existing = sorted(
        d for d in os.listdir(versions_root) if d.startswith("v") and d[1:].isdigit()
    )
    if version is None:
        n = int(existing[-1][1:]) + 1 if existing else 1
        version = f"v{n:03d}"
    vdir = os.path.join(versions_root, version)
    if os.path.isdir(vdir):
        shutil.rmtree(vdir)
    os.makedirs(vdir)

    # seed from previous version object if present
    prev_xyz = prev_rgb = None
    if existing:
        prev = os.path.join(versions_root, existing[-1], "object.ply")
        if version != existing[-1] and os.path.isfile(prev):
            prev_xyz, prev_rgb = _load_cloud(prev)

    clouds = _collect_backend_clouds(backends_root)
    if not clouds and prev_xyz is None:
        raise RuntimeError(
            f"no usable backend clouds under {backends_root} — run backends first"
        )

    if clouds:
        xyz, rgb, merge_meta = merge_clouds(clouds)
    else:
        xyz, rgb, merge_meta = prev_xyz, prev_rgb, {"sources": ["previous"], "n_points": len(prev_xyz)}

    if prev_xyz is not None and clouds:
        # blend previous refine into merge
        xyz = np.concatenate([xyz, prev_xyz], 0)
        rgb = np.concatenate([rgb, prev_rgb], 0)
        import open3d as o3d

        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(xyz)
        pcd.colors = o3d.utility.Vector3dVector(rgb.astype(np.float64) / 255.0)
        pcd = pcd.voxel_down_sample(0.006)
        xyz = np.asarray(pcd.points)
        rgb = (np.asarray(pcd.colors) * 255).astype(np.uint8)

    scene_ply = write_ply(
        os.path.join(vdir, "scene.ply"),
        [tuple(map(float, p)) for p in xyz],
        [tuple(map(int, c)) for c in rgb],
    )

    obj_xyz, obj_rgb = isolate_object_cloud(xyz, rgb, focus=focus)
    if len(obj_xyz) < 30:
        obj_xyz, obj_rgb = xyz, rgb

    object_ply = write_ply(
        os.path.join(vdir, "object.ply"),
        [tuple(map(float, p)) for p in obj_xyz],
        [tuple(map(int, c)) for c in obj_rgb],
    )

    holes = detect_holes(obj_xyz)
    with open(os.path.join(vdir, "holes.json"), "w") as fh:
        json.dump(holes, fh, indent=2)

    fill_info: dict[str, Any] = {"geometric": None, "generative": None}
    mesh_path = os.path.join(vdir, "object_mesh.glb")
    fill_info["geometric"] = geometric_fill(obj_xyz, obj_rgb, mesh_path)

    # generative fill when holes remain / coverage low
    glb_path = os.path.join(vdir, "object.glb")
    if try_generative and holes.get("coverage", 1.0) < 0.92:
        try:
            pack = load_pack(run_dir)
            frames = pack_frame_paths(pack)
        except Exception:  # noqa: BLE001
            frames = []
        gen = generative_fill(
            obj_xyz, obj_rgb, frames, glb_path, endpoint=trellis_endpoint
        )
        fill_info["generative"] = gen
        if gen.get("ok"):
            reg = _register_glb_to_cloud(glb_path, obj_xyz)
            fill_info["registration"] = reg
    elif fill_info["geometric"].get("ok"):
        # copy geometric mesh as object.glb
        if os.path.isfile(mesh_path):
            shutil.copy2(mesh_path, glb_path)

    previews = write_previews(obj_xyz, obj_rgb, os.path.join(vdir, "previews"))

    qc = {
        "version": version,
        "coverage": holes.get("coverage"),
        "n_scene_points": len(xyz),
        "n_object_points": len(obj_xyz),
        "merge": merge_meta,
        "holes": {
            "n_empty_border": holes.get("n_empty_border"),
            "n_hole_centers": len(holes.get("hole_centers") or []),
        },
        "fill": {
            "geometric_ok": bool((fill_info.get("geometric") or {}).get("ok")),
            "generative_ok": bool((fill_info.get("generative") or {}).get("ok")),
            "registration": fill_info.get("registration"),
        },
        "previews": [os.path.basename(p) for p in previews],
        "artifacts": {
            "scene_ply": scene_ply,
            "object_ply": object_ply,
            "object_glb": glb_path if os.path.isfile(glb_path) else None,
            "object_mesh": mesh_path if os.path.isfile(mesh_path) else None,
        },
    }
    with open(os.path.join(vdir, "qc.json"), "w") as fh:
        json.dump(qc, fh, indent=2)
    with open(os.path.join(vdir, "fill.json"), "w") as fh:
        json.dump(fill_info, fh, indent=2)

    html = _write_version_html(
        vdir,
        {
            "version": version,
            "coverage": holes.get("coverage"),
            "n_object_points": len(obj_xyz),
            "fill": qc["fill"],
            "previews": previews,
        },
    )
    qc["open_me"] = html
    return qc


def refine_versions(
    run_dir: str,
    *,
    n_versions: int = 2,
    focus: float = 0.85,
    trellis_endpoint: str | None = None,
    try_generative: bool = True,
    coverage_stop: float = 0.95,
) -> list[dict]:
    """Run ``n_versions`` refine cycles (or until coverage_stop)."""
    results = []
    for _ in range(n_versions):
        qc = refine_once(
            run_dir,
            focus=focus,
            trellis_endpoint=trellis_endpoint,
            try_generative=try_generative,
        )
        results.append(qc)
        print(
            f"✔ {qc['version']} coverage={qc.get('coverage')} "
            f"object_pts={qc.get('n_object_points')} "
            f"gen={qc['fill'].get('generative_ok')}",
            flush=True,
        )
        if (qc.get("coverage") or 0) >= coverage_stop and not (
            qc.get("holes") or {}
        ).get("n_hole_centers"):
            break
        # skip generative on later versions if first failed hard (service down)
        if try_generative and not qc["fill"].get("generative_ok"):
            # still allow geometric-only subsequent passes
            pass
    with open(os.path.join(run_dir, "refine_summary.json"), "w") as fh:
        json.dump({"versions": results}, fh, indent=2)
    return results
