"""Critically refine an RGB-D session *before* TSDF / 3DGUT / object fusion.

Naive pipelines keep every Nth frame. That keeps motion blur, near-duplicates,
and bad framing — then reconstruction fights the junk.

This module:

1. Scores **every** frame from the images/depth/poses first
2. Rejects blur, bad exposure, weak depth relief, tiny camera motion duplicates
3. Picks a viewpoint-diverse keyframe set (quality × coverage)
4. Writes a refined ``session/`` (manifest + symlinked color/depth) for downstream

Downstream (``assetpipe object``, Open3D TSDF, ``run_3dgut_from_session``) should
consume the curated session, not the raw 300+ frame dump.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np


@dataclass
class FrameMetrics:
    index: int
    frame_id: str
    sharpness: float
    brightness: float
    contrast: float
    valid_depth_frac: float
    center_depth_m: float
    depth_relief_m: float
    edge_agree: float
    cam_step_m: float
    quality: float
    reject: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.reject


def _laplacian_var(gray: np.ndarray) -> float:
    g = gray.astype(np.float32)
    lap = (
        -4 * g[1:-1, 1:-1]
        + g[:-2, 1:-1]
        + g[2:, 1:-1]
        + g[1:-1, :-2]
        + g[1:-1, 2:]
    )
    return float(lap.var())


def _grad_mag(img: np.ndarray) -> np.ndarray:
    x = img.astype(np.float32)
    gx = np.zeros_like(x)
    gy = np.zeros_like(x)
    gx[:, 1:-1] = x[:, 2:] - x[:, :-2]
    gy[1:-1, :] = x[2:, :] - x[:-2, :]
    return np.sqrt(gx * gx + gy * gy)


def _edge_agreement(gray_small: np.ndarray, depth_m: np.ndarray) -> float:
    """How well RGB edges line up with depth edges (0–1-ish)."""
    from PIL import Image

    d = depth_m.copy()
    valid = d > 0.05
    if valid.mean() < 0.2:
        return 0.0
    # normalize valid depths for gradient
    lo, hi = np.percentile(d[valid], [5, 95])
    dn = np.clip((d - lo) / max(hi - lo, 1e-3), 0, 1)
    dn[~valid] = 0
    d_small = np.asarray(
        Image.fromarray((dn * 255).astype(np.uint8)).resize(
            (gray_small.shape[1], gray_small.shape[0]), Image.BILINEAR
        ),
        dtype=np.float32,
    )
    eg = _grad_mag(gray_small)
    ed = _grad_mag(d_small)
    # focus on stronger RGB edges
    thr = float(np.percentile(eg, 75))
    mask = eg >= thr
    if mask.sum() < 30:
        return 0.0
    eg_n = eg[mask] / (eg[mask].max() + 1e-6)
    ed_n = ed[mask] / (ed[mask].max() + 1e-6)
    # cosine similarity of edge strengths
    return float(np.dot(eg_n, ed_n) / (np.linalg.norm(eg_n) * np.linalg.norm(ed_n) + 1e-6))


def analyze_frames(session) -> list[FrameMetrics]:
    """Measure every frame — no selection yet."""
    from PIL import Image

    out: list[FrameMetrics] = []
    prev_t: Optional[np.ndarray] = None

    for i, fr in enumerate(session.frames):
        color = Image.open(fr.color_path).convert("RGB")
        gray = np.asarray(color.convert("L").resize((160, 120), Image.BILINEAR))
        sharp = _laplacian_var(gray)
        g = gray.astype(np.float32)
        brightness = float(g.mean() / 255.0)
        contrast = float(g.std() / 255.0)

        depth_raw = np.array(Image.open(fr.depth_path))
        unit = (fr.depth_unit or session.depth_unit or "mm").lower()
        depth_m = depth_raw.astype(np.float32) * (0.001 if unit == "mm" else 1.0)
        valid = depth_m > 0.05
        vfrac = float(valid.mean())

        H, W = depth_m.shape[:2]
        cy, cx = H // 2, W // 2
        patch = depth_m[cy - H // 5 : cy + H // 5, cx - W // 5 : cx + W // 5]
        pv = patch[patch > 0.05]
        if len(pv) >= 40:
            cmed = float(np.median(pv))
            relief = float(np.std(pv))
        else:
            cmed, relief = 0.0, 0.0

        edge = _edge_agreement(gray, depth_m)

        c2w = np.asarray(fr.pose, dtype=np.float64).reshape(4, 4)
        t = c2w[:3, 3]
        step = float(np.linalg.norm(t - prev_t)) if prev_t is not None else 0.0
        prev_t = t

        # Composite quality (absolute; thresholds applied later vs session stats)
        dist_bonus = 1.0 - min(abs(cmed - 0.70) / 0.70, 1.0) if cmed > 0 else 0.0
        relief_n = min(relief / 0.06, 1.0)
        expo = 1.0 - min(abs(brightness - 0.45) / 0.45, 1.0)
        quality = (
            sharp
            * (0.35 + 0.65 * dist_bonus)
            * (0.55 + 0.45 * vfrac)
            * (0.65 + 0.35 * relief_n)
            * (0.7 + 0.3 * edge)
            * (0.75 + 0.25 * expo)
            * (0.85 + 0.15 * min(contrast / 0.18, 1.0))
        )

        out.append(
            FrameMetrics(
                index=i,
                frame_id=fr.frame_id,
                sharpness=sharp,
                brightness=brightness,
                contrast=contrast,
                valid_depth_frac=vfrac,
                center_depth_m=cmed,
                depth_relief_m=relief,
                edge_agree=edge,
                cam_step_m=step,
                quality=quality,
            )
        )
    return out


def _apply_rejects(
    metrics: list[FrameMetrics],
    *,
    sharp_pct: float = 25.0,
    min_valid_depth: float = 0.55,
    depth_lo: float = 0.20,
    depth_hi: float = 2.20,
    min_relief_m: float = 0.008,
    bright_lo: float = 0.12,
    bright_hi: float = 0.88,
    min_edge: float = 0.15,
    max_step_m: Optional[float] = None,
) -> list[FrameMetrics]:
    """Hard-reject using session-adaptive sharpness floor + absolute sanity checks."""
    sharps = np.array([m.sharpness for m in metrics], dtype=np.float64)
    sharp_floor = float(np.percentile(sharps, sharp_pct))
    # never go below a tiny absolute floor (near-black / frozen frames)
    sharp_floor = max(sharp_floor, 80.0)

    for i, m in enumerate(metrics):
        reasons: list[str] = []
        if m.sharpness < sharp_floor:
            reasons.append(f"blur(sharp={m.sharpness:.0f}<{sharp_floor:.0f})")
        if m.valid_depth_frac < min_valid_depth:
            reasons.append(f"sparse_depth({m.valid_depth_frac:.0%})")
        if not (depth_lo <= m.center_depth_m <= depth_hi):
            reasons.append(f"bad_range({m.center_depth_m:.2f}m)")
        if m.depth_relief_m < min_relief_m:
            reasons.append(f"flat_depth({m.depth_relief_m:.3f}m)")
        if m.brightness < bright_lo or m.brightness > bright_hi:
            reasons.append(f"exposure({m.brightness:.2f})")
        if m.edge_agree < min_edge:
            reasons.append(f"rgb_depth_mismatch({m.edge_agree:.2f})")
        if max_step_m is not None and m.cam_step_m > max_step_m:
            reasons.append(f"pose_jump({m.cam_step_m:.3f}m)")
        # near-duplicate of previous *kept* pose — mark for later pass
        m.reject = reasons
    return metrics


def select_keyframes(
    session,
    metrics: list[FrameMetrics],
    *,
    target: int = 48,
    min_angle_deg: float = 8.0,
    min_step_m: float = 0.012,
    prefer_quality: bool = True,
) -> list[int]:
    """Greedy quality × viewpoint diversity selection among survivors."""
    survivors = [m for m in metrics if m.ok]
    if not survivors:
        # fall back: take top quality regardless of reject (still better than raw)
        survivors = sorted(metrics, key=lambda m: m.quality, reverse=True)[: max(target, 8)]
        for m in survivors:
            m.reject = []

    # Sort candidates by quality (best first)
    cands = sorted(survivors, key=lambda m: m.quality, reverse=True)

    poses = []
    for m in cands:
        c2w = np.asarray(session.frames[m.index].pose, float).reshape(4, 4)
        poses.append((c2w[:3, 3], c2w[:3, 2]))  # translation, forward

    chosen: list[int] = []
    chosen_pose: list[tuple[np.ndarray, np.ndarray]] = []

    def far_enough(t, fwd) -> bool:
        for t0, f0 in chosen_pose:
            if float(np.linalg.norm(t - t0)) < min_step_m:
                return False
            ang = float(
                np.degrees(np.arccos(np.clip(np.dot(fwd, f0), -1.0, 1.0)))
            )
            if ang < min_angle_deg and float(np.linalg.norm(t - t0)) < min_step_m * 3:
                return False
        return True

    for m, (t, fwd) in zip(cands, poses):
        if len(chosen) >= target:
            break
        if not chosen or far_enough(t, fwd):
            chosen.append(m.index)
            chosen_pose.append((t, fwd))

    # If coverage still thin, relax and fill by temporal spacing among survivors
    if len(chosen) < min(target, len(survivors)):
        by_idx = sorted(survivors, key=lambda m: m.index)
        need = target - len(chosen)
        if need > 0 and by_idx:
            step = max(1, len(by_idx) // (need + 1))
            for m in by_idx[::step]:
                if len(chosen) >= target:
                    break
                if m.index not in chosen:
                    chosen.append(m.index)

    chosen.sort()
    return chosen


def write_curated_session(
    session,
    indices: list[int],
    out_dir: str,
    *,
    metrics: list[FrameMetrics],
    report_extra: Optional[dict] = None,
    copy_files: bool = False,
) -> dict[str, Any]:
    """Write refined session/ with symlinked (or copied) frames + curation report.

    Use ``copy_files=True`` when the source may be ephemeral (e.g. extracted
    zip under /tmp) so the curated session stays self-contained.
    """
    out = Path(out_dir).resolve()
    if out.exists():
        shutil.rmtree(out)
    color_dir = out / "color"
    depth_dir = out / "depth"
    color_dir.mkdir(parents=True)
    depth_dir.mkdir(parents=True)

    frames_out = []
    by_idx = {m.index: m for m in metrics}

    for new_i, old_i in enumerate(indices):
        fr = session.frames[old_i]
        cid = f"{new_i:04d}"
        c_name = f"{cid}.jpg"
        d_name = f"{cid}.png"
        c_dst = color_dir / c_name
        d_dst = depth_dir / d_name
        for src, dst in ((fr.color_path, c_dst), (fr.depth_path, d_dst)):
            if copy_files:
                shutil.copy2(src, dst)
            else:
                try:
                    os.symlink(os.path.abspath(src), dst)
                except OSError:
                    shutil.copy2(src, dst)
        entry = {
            "id": f"f{new_i:05d}",
            "t": float(fr.timestamp),
            "color": f"color/{c_name}",
            "depth": f"depth/{d_name}",
            "pose": [float(x) for x in fr.pose],
            "intrinsics": [float(x) for x in fr.intrinsics],
            "src_index": int(old_i),
            "src_id": fr.frame_id,
        }
        if fr.color_intrinsics is not None:
            entry["K_color"] = [float(x) for x in fr.color_intrinsics]
        if fr.color_size is not None:
            entry["color_size"] = [int(x) for x in fr.color_size]
        if old_i in by_idx:
            entry["quality"] = float(by_idx[old_i].quality)
            entry["sharpness"] = float(by_idx[old_i].sharpness)
        frames_out.append(entry)

    manifest = {
        "object_hint": session.object_hint,
        "location": session.location,
        "depth_unit": session.depth_unit,
        # Poses below came through load_session, which normalizes to OpenCV —
        # declare that, or a source-keyed fallback would mis-flip on reload.
        "pose_convention": "opencv",
        "curated_from": os.path.abspath(session.root),
        "n_source_frames": session.n_frames,
        "n_curated_frames": len(frames_out),
        "frames": frames_out,
    }
    with open(out / "manifest.json", "w") as fh:
        json.dump(manifest, fh, indent=2)

    rejected = [asdict(m) for m in metrics if m.reject]
    eligible = [m for m in metrics if not m.reject]
    kept_metrics = [asdict(by_idx[i]) for i in indices if i in by_idx]
    report = {
        "source": os.path.abspath(session.root),
        "curated_session": str(out),
        "n_source": session.n_frames,
        "n_kept": len(indices),
        "n_rejected": len(rejected),
        "n_eligible": len(eligible),
        "n_not_selected": max(0, len(eligible) - len(indices)),
        "kept_indices": indices,
        "kept": kept_metrics,
        "rejected_sample": rejected[:40],
        "reject_counts": _count_rejects(metrics),
        "reject_runs": _reject_runs(metrics),
        "quality": {
            "kept_mean": float(np.mean([m["quality"] for m in kept_metrics])) if kept_metrics else 0,
            "all_mean": float(np.mean([m.quality for m in metrics])),
            "kept_sharp_mean": float(np.mean([m["sharpness"] for m in kept_metrics])) if kept_metrics else 0,
            "all_sharp_mean": float(np.mean([m.sharpness for m in metrics])),
        },
    }
    if report_extra:
        report.update(report_extra)
    with open(out / "curation_report.json", "w") as fh:
        json.dump(report, fh, indent=2)

    _write_curation_html(out, report, session, indices, metrics)
    return report


def _count_rejects(metrics: list[FrameMetrics]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in metrics:
        for r in m.reject:
            key = r.split("(")[0]
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def _reject_runs(metrics: list[FrameMetrics]) -> dict[str, list[list[int]]]:
    """Contiguous source-frame intervals per rejection reason."""
    by_reason: dict[str, list[int]] = {}
    for m in metrics:
        for reason in m.reject:
            by_reason.setdefault(reason.split("(")[0], []).append(m.index)
    out: dict[str, list[list[int]]] = {}
    for reason, indices in by_reason.items():
        runs: list[list[int]] = []
        for index in indices:
            if not runs or index > runs[-1][1] + 1:
                runs.append([index, index])
            else:
                runs[-1][1] = index
        out[reason] = runs
    return out


def _write_curation_html(
    out: Path,
    report: dict,
    session,
    indices: list[int],
    metrics: list[FrameMetrics],
) -> Path:
    from PIL import Image

    thumb_dir = out / "thumbs"
    thumb_dir.mkdir(exist_ok=True)
    by_idx = {m.index: m for m in metrics}

    # contact: kept frames
    thumbs = []
    for i, idx in enumerate(indices[:64]):
        fr = session.frames[idx]
        im = Image.open(fr.color_path).convert("RGB")
        im.thumbnail((160, 120))
        name = f"keep_{i:03d}.jpg"
        im.save(thumb_dir / name, quality=80)
        m = by_idx.get(idx)
        thumbs.append(
            {
                "file": f"thumbs/{name}",
                "idx": idx,
                "q": round(m.quality, 1) if m else 0,
                "sharp": round(m.sharpness, 0) if m else 0,
            }
        )

    # a few rejected examples
    rej = [m for m in metrics if m.reject][:12]
    rej_thumbs = []
    for j, m in enumerate(rej):
        fr = session.frames[m.index]
        im = Image.open(fr.color_path).convert("RGB")
        im.thumbnail((160, 120))
        name = f"rej_{j:03d}.jpg"
        im.save(thumb_dir / name, quality=80)
        rej_thumbs.append(
            {
                "file": f"thumbs/{name}",
                "idx": m.index,
                "why": ", ".join(m.reject)[:80],
            }
        )

    q = report["quality"]
    html = f"""<!doctype html>
<html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>RGB-D curation</title>
<style>
:root{{--bg:#0e1117;--panel:#161b22;--line:#30363d;--txt:#e6edf3;--dim:#8b949e;--ok:#3fb950;--bad:#f85149}}
body{{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 system-ui;padding:20px;max-width:1100px;margin:0 auto}}
h1{{font-size:20px;margin:0 0 6px}} .dim{{color:var(--dim)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin:14px 0}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}}
.card img{{width:100%;display:block;aspect-ratio:4/3;object-fit:cover}}
.card .cap{{padding:6px 8px;font-size:11px;color:var(--dim)}}
.stat{{display:inline-block;margin:4px 12px 4px 0;padding:6px 10px;background:var(--panel);border:1px solid var(--line);border-radius:6px}}
.ok{{color:var(--ok)}} .bad{{color:var(--bad)}}
code{{color:#79c0ff}}
</style></head><body>
<h1>Preprocess: curated RGB-D session</h1>
<p class=dim>{report['n_source']} source → <b class=ok>{report['n_kept']} kept</b>
· <span class=bad>{report['n_rejected']} rejected</span>
· sharp {q['all_sharp_mean']:.0f} → {q['kept_sharp_mean']:.0f}
· quality {q['all_mean']:.0f} → {q['kept_mean']:.0f}</p>
<p>
<span class=stat>reject: {json.dumps(report.get('reject_counts',{}))}</span>
</p>
<p class=dim>Use this folder as session input for <code>assetpipe object</code>,
<code>assetpipe rgbd</code>, or <code>tools/run_3dgut_from_session.py</code>.</p>
<h2>Kept keyframes</h2>
<div class=grid>
{''.join(f'<div class=card><img src="{t["file"]}"><div class=cap>#{t["idx"]} q={t["q"]} s={t["sharp"]:.0f}</div></div>' for t in thumbs)}
</div>
<h2>Rejected samples</h2>
<div class=grid>
{''.join(f'<div class=card><img src="{t["file"]}"><div class=cap>#{t["idx"]}<br>{t["why"]}</div></div>' for t in rej_thumbs)}
</div>
<p class=dim><a href="curation_report.json">curation_report.json</a> ·
<a href="manifest.json">manifest.json</a></p>
</body></html>"""
    path = out / "CURATION.html"
    path.write_text(html)
    return path


def curate_session(
    session_dir: str,
    out_dir: str,
    *,
    target: int = 48,
    min_angle_deg: float = 8.0,
    min_step_m: float = 0.012,
    sharp_pct: float = 25.0,
    min_edge: float = 0.15,
    depth_lo: float = 0.20,
    depth_hi: float = 2.20,
    max_step_m: Optional[float] = None,
) -> dict[str, Any]:
    """Analyze → reject → diverse keyframes → write curated session."""
    from .rgbd_session import load_session

    session = load_session(session_dir)
    metrics = analyze_frames(session)
    _apply_rejects(
        metrics,
        sharp_pct=sharp_pct,
        min_edge=min_edge,
        depth_lo=depth_lo,
        depth_hi=depth_hi,
        max_step_m=max_step_m,
    )
    indices = select_keyframes(
        session,
        metrics,
        target=target,
        min_angle_deg=min_angle_deg,
        min_step_m=min_step_m,
    )
    report = write_curated_session(
        session,
        indices,
        out_dir,
        metrics=metrics,
        report_extra={
            "params": {
                "target": target,
                "min_angle_deg": min_angle_deg,
                "min_step_m": min_step_m,
                "sharp_pct": sharp_pct,
                "min_edge": min_edge,
                "depth_lo": depth_lo,
                "depth_hi": depth_hi,
                "max_step_m": max_step_m,
            }
        },
    )
    report["html"] = str(Path(out_dir).resolve() / "CURATION.html")
    return report
