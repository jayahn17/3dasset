"""Capture → backend router: TRELLIS (single object) vs 3DGRUT (multi/scene).

Policy (locked from bottle vs couch/desk captures):

* **trellis** — one dominant product-scale object. Mesh from TRELLIS;
  **measure always from RGB-D** ``dims.json`` (TRELLIS scale is not metric).
* **3dgrut** — multiple co-equal objects, furniture, or room/scene extent.

Heuristics prefer object AABB inches when present; otherwise fall back to
curation depth stats (median center depth + depth relief).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# Product-scale vs furniture/scene (inches on the longest AABB edge).
# Bottle ~10"; couch ~67"; desk crop ~57".
PRODUCT_MAX_INCHES = 24.0
# Furniture / multi-object floor when dims are missing.
SCENE_DEPTH_M = 1.0
SCENE_RELIEF_M = 0.15


@dataclass
class RouteDecision:
    route: str  # "trellis" | "3dgrut"
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    next_steps: list[str] = field(default_factory=list)
    measure: str = "rgbd_dims"  # never trust TRELLIS for measure
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def _find_file(root: str, name: str, max_depth: int = 3) -> Optional[str]:
    root = os.path.abspath(root)
    if os.path.isfile(root) and os.path.basename(root) == name:
        return root
    if os.path.isfile(root):
        root = os.path.dirname(root)
    direct = os.path.join(root, name)
    if os.path.isfile(direct):
        return direct
    # sibling object / curated folders: CrateScan-XXX_curated + _object
    parent = os.path.dirname(root.rstrip(os.sep))
    base = os.path.basename(root.rstrip(os.sep))
    candidates = [
        os.path.join(root, name),
        os.path.join(parent, name),
    ]
    for suffix in ("", "_curated", "_object", "_curated_object",
                   "_curated_object1", "/object_asset"):
        if base.endswith("_curated") and suffix == "_curated":
            stem = base[: -len("_curated")]
        elif base.endswith("_object") and suffix == "_object":
            stem = base[: -len("_object")]
        else:
            stem = base
        candidates.append(os.path.join(parent, stem + suffix, name)
                          if suffix.startswith("_") or suffix.startswith("/")
                          else os.path.join(parent, stem, name))
        if suffix.startswith("/"):
            candidates.append(os.path.join(parent, stem + suffix, name))
            candidates.append(os.path.join(root, suffix.lstrip("/"), name))

    seen: set[str] = set()
    for c in candidates:
        c = os.path.normpath(c)
        if c in seen:
            continue
        seen.add(c)
        if os.path.isfile(c):
            return c

    # shallow walk
    if os.path.isdir(root):
        for dirpath, dirnames, filenames in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth > max_depth:
                dirnames.clear()
                continue
            if name in filenames:
                return os.path.join(dirpath, name)
    return None


def _load_json(path: Optional[str]) -> Optional[dict]:
    if not path or not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _aabb_inches(dims: dict) -> Optional[dict[str, float]]:
    obj = dims.get("object") or dims
    aabb = obj.get("aabb") or {}
    inches = aabb.get("inches_0_25") or aabb.get("raw_inches")
    if isinstance(inches, dict) and all(
        k in inches for k in ("length", "width", "height")
    ):
        return {k: float(inches[k]) for k in ("length", "width", "height")}
    if all(k in obj for k in ("rawLengthInches", "rawWidthInches", "rawHeightInches")):
        return {
            "length": float(obj["rawLengthInches"]),
            "width": float(obj["rawWidthInches"]),
            "height": float(obj["rawHeightInches"]),
        }
    if all(k in obj for k in ("length", "width", "height")):
        return {k: float(obj[k]) for k in ("length", "width", "height")}
    return None


def _curation_depth_stats(report: dict) -> tuple[Optional[float], Optional[float]]:
    kept = report.get("kept") or []
    depths = [float(k["center_depth_m"]) for k in kept
              if k.get("center_depth_m") is not None]
    reliefs = [float(k["depth_relief_m"]) for k in kept
               if k.get("depth_relief_m") is not None]
    return _median(depths), _median(reliefs)


def _next_steps(route: str) -> list[str]:
    if route == "trellis":
        return [
            "Isolate / crop so one object dominates the frame",
            "Run: assetpipe generate <views_or_photos> --backend trellis",
            "Measure from RGB-D object dims (assetpipe object / dims.json) — "
            "do not use TRELLIS mesh scale",
        ]
    return [
        "Curate the RGB-D session, then train 3DGRUT "
        "(tools/run_3dgut_from_session.py)",
        "Skip TRELLIS as the primary path for this capture",
        "Review via scene_gaussians.ply → .splat viewer; optional splat_compare",
    ]


def classify_capture(
    root: str,
    *,
    product_max_inches: float = PRODUCT_MAX_INCHES,
    scene_depth_m: float = SCENE_DEPTH_M,
    scene_relief_m: float = SCENE_RELIEF_M,
    force: Optional[str] = None,
) -> RouteDecision:
    """Classify a capture / curated / object folder into trellis vs 3dgrut."""
    root = os.path.abspath(root)
    dims_path = _find_file(root, "dims.json")
    curation_path = _find_file(root, "curation_report.json")
    analysis_path = _find_file(root, "analysis.json")
    manifest_path = _find_file(root, "manifest.json")

    dims = _load_json(dims_path)
    report = _load_json(curation_path)
    analysis = _load_json(analysis_path)

    signals: dict[str, Any] = {
        "product_max_inches": product_max_inches,
        "scene_depth_m": scene_depth_m,
        "scene_relief_m": scene_relief_m,
    }
    paths = {k: v for k, v in {
        "root": root,
        "dims": dims_path,
        "curation_report": curation_path,
        "analysis": analysis_path,
        "manifest": manifest_path,
    }.items() if v}

    if force in ("trellis", "3dgrut"):
        return RouteDecision(
            route=force,
            reasons=[f"forced via --force {force}"],
            signals=signals,
            next_steps=_next_steps(force),
            paths=paths,
        )

    reasons: list[str] = []
    route: Optional[str] = None

    aabb = _aabb_inches(dims) if dims else None
    if aabb:
        longest = max(aabb.values())
        signals["aabb_inches"] = aabb
        signals["longest_edge_inches"] = longest
        if longest <= product_max_inches:
            route = "trellis"
            reasons.append(
                f"object AABB longest edge {longest:.1f}\" ≤ "
                f"{product_max_inches:.0f}\" (product-scale → TRELLIS)"
            )
        else:
            route = "3dgrut"
            reasons.append(
                f"object AABB longest edge {longest:.1f}\" > "
                f"{product_max_inches:.0f}\" (furniture/scene → 3DGRUT)"
            )

    depth_med = relief_med = None
    if report:
        depth_med, relief_med = _curation_depth_stats(report)
        signals["n_kept"] = report.get("n_kept") or len(report.get("kept") or [])
        signals["median_center_depth_m"] = depth_med
        signals["median_depth_relief_m"] = relief_med

    if route is None and depth_med is not None:
        # No dims: depth + relief approximate scene vs tabletop object.
        if depth_med >= scene_depth_m and (relief_med or 0) >= scene_relief_m:
            route = "3dgrut"
            reasons.append(
                f"no dims; median depth {depth_med:.2f}m and relief "
                f"{relief_med:.2f}m suggest scene/furniture → 3DGRUT"
            )
        elif depth_med < scene_depth_m:
            route = "trellis"
            reasons.append(
                f"no dims; median depth {depth_med:.2f}m < {scene_depth_m:.1f}m "
                f"suggests close single-object → TRELLIS (confirm with crop)"
            )
        else:
            route = "3dgrut"
            reasons.append(
                f"no dims; median depth {depth_med:.2f}m without strong "
                f"product-scale cue → default 3DGRUT"
            )

    if route is None and analysis:
        # analysis.top center depths as weak cue
        tops = analysis.get("top") or []
        tdepths = [float(t["center_depth_m"]) for t in tops
                   if t.get("center_depth_m") is not None]
        td = _median(tdepths)
        signals["analysis_median_depth_m"] = td
        if td is not None and td < scene_depth_m:
            route = "trellis"
            reasons.append(
                f"analysis-only: median depth {td:.2f}m → TRELLIS candidate"
            )
        else:
            route = "3dgrut"
            reasons.append("analysis-only / ambiguous → default 3DGRUT")

    if route is None:
        route = "3dgrut"
        reasons.append(
            "no dims.json / curation_report.json / analysis.json — "
            "default 3DGRUT (safer for multi/scene)"
        )

    # Dims said trellis but depth screams furniture: bump to 3dgrut.
    if (
        route == "trellis"
        and depth_med is not None
        and relief_med is not None
        and depth_med >= scene_depth_m
        and relief_med >= scene_relief_m
        and (signals.get("longest_edge_inches") or 0) > 18
    ):
        route = "3dgrut"
        reasons.append(
            "overridden to 3DGRUT: product-scale dims conflict with "
            "scene-like depth/relief"
        )

    reasons.append("measure always from RGB-D dims.json, never TRELLIS scale")
    return RouteDecision(
        route=route,
        reasons=reasons,
        signals=signals,
        next_steps=_next_steps(route),
        paths=paths,
    )


def write_decision(decision: RouteDecision, out_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(decision.to_dict(), fh, indent=2)
        fh.write("\n")
    return out_path
