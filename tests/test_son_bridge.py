"""Tests for the son (LifeTwin/JayAsset) bridge — pure stdlib.

Verifies the exported payload matches the shape `son/api/quest-asset-scan.js`
consumes (action, detected_assets, per-detection location/jay3d_state).
"""

import json

from assetpipe.integrations.son_twin import (
    asset_to_detection,
    build_quest_scan_payload,
)


def _row(**kw):
    base = dict(
        asset_id="abc123", label="cardboard box", category="container",
        mesh_path="twin_out/abc123/model.obj", urdf_path="twin_out/abc123/model.urdf",
        dim_w=0.4, dim_h=0.3, dim_d=0.3, source="quest3", location="garage shelf",
        world_pose=json.dumps([1, 0, 0, 1.5, 0, 1, 0, 0.7, 0, 0, 1, 2.3, 0, 0, 0, 1]),
        thumbnail_path=None, tags=json.dumps(["cardboard box", "procedural_box"]),
        extra=json.dumps({"detection_score": 0.88, "recon_method": "procedural_box"}),
    )
    base.update(kw)
    return base


def test_detection_maps_pose_and_state():
    det = asset_to_detection(_row())
    assert det["asset_id"] == "abc123"
    assert det["kind"] == "container"
    assert det["confidence"] == 0.88
    assert det["jay3d_state"] == "box proxy ready"
    # pose translation is the last column of the row-major 4x4
    assert det["location"]["x"] == 1.5 and det["location"]["z"] == 2.3
    assert det["evidence"] == ["rgb_keyframes", "spatial_anchors", "depth"]


def test_trellis_method_state():
    row = _row(extra=json.dumps({"detection_score": 0.9, "recon_method": "trellis"}))
    assert asset_to_detection(row)["jay3d_state"].startswith("single-image mesh")


def test_payload_envelope_is_valid():
    payload = build_quest_scan_payload([_row(), _row(asset_id="def456")],
                                       scan_id="t", action="ingest_scan")
    assert payload["action"] == "ingest_scan"
    assert payload["scan_id"] == "t"
    assert len(payload["detected_assets"]) == 2
    assert payload["source_device"]["platform"] == "meta_quest"
    assert payload["scene_version"]["version_id"] == "t-v1"
    # capture_types is a subset of son's allowed set
    allowed = {"scene_mesh", "point_cloud", "depth", "rgb_keyframes",
               "spatial_anchors", "manual_note"}
    assert set(payload["capture_types"]) <= allowed


def test_empty_catalog_is_safe():
    payload = build_quest_scan_payload([])
    assert payload["detected_assets"] == []
    assert payload["capture_types"] == ["rgb_keyframes"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all son-bridge tests passed")
