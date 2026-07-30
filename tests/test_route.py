"""Tests for TRELLIS vs 3DGRUT capture routing."""

from __future__ import annotations

import json
from pathlib import Path

from assetpipe.scene.route import classify_capture, write_decision


def _write(p: Path, obj: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def test_bottle_scale_routes_trellis(tmp_path: Path) -> None:
    root = tmp_path / "bottle_object"
    _write(root / "dims.json", {
        "object": {
            "aabb": {
                "inches_0_25": {"length": 5.25, "width": 9.75, "height": 7.75},
            }
        }
    })
    d = classify_capture(str(root))
    assert d.route == "trellis"
    assert d.measure == "rgbd_dims"
    assert any("product-scale" in r for r in d.reasons)


def test_couch_scale_routes_3dgrut(tmp_path: Path) -> None:
    root = tmp_path / "couch_object"
    _write(root / "dims.json", {
        "object": {
            "aabb": {
                "inches_0_25": {"length": 52.5, "width": 66.75, "height": 66.75},
            }
        }
    })
    d = classify_capture(str(root))
    assert d.route == "3dgrut"
    assert any("furniture/scene" in r for r in d.reasons)


def test_depth_fallback_scene(tmp_path: Path) -> None:
    root = tmp_path / "scene_curated"
    kept = [
        {"center_depth_m": 1.14, "depth_relief_m": 0.17},
        {"center_depth_m": 1.10, "depth_relief_m": 0.16},
        {"center_depth_m": 1.20, "depth_relief_m": 0.18},
    ]
    _write(root / "curation_report.json", {"n_kept": 3, "kept": kept})
    d = classify_capture(str(root))
    assert d.route == "3dgrut"


def test_depth_fallback_close_object(tmp_path: Path) -> None:
    root = tmp_path / "close_curated"
    kept = [
        {"center_depth_m": 0.73, "depth_relief_m": 0.10},
        {"center_depth_m": 0.74, "depth_relief_m": 0.11},
    ]
    _write(root / "curation_report.json", {"n_kept": 2, "kept": kept})
    d = classify_capture(str(root))
    assert d.route == "trellis"


def test_force_and_write(tmp_path: Path) -> None:
    root = tmp_path / "any"
    root.mkdir()
    d = classify_capture(str(root), force="trellis")
    assert d.route == "trellis"
    out = tmp_path / "route_decision.json"
    write_decision(d, str(out))
    assert json.loads(out.read_text())["route"] == "trellis"


def test_default_without_signals_is_3dgrut(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    d = classify_capture(str(root))
    assert d.route == "3dgrut"
