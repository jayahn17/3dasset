"""Bridge: assetpipe assets -> son's LifeTwin / JayAsset twin.

son (github.com/jayahn17/son) already owns the twin *platform*: the
canonical schema, the spatial model, `api/quest-asset-scan.js` (the Quest
scan ingest + closet), and `src/son/cad_urdf_usd.py` (mesh -> URDF/USD).
What it does NOT have is the thing that actually *produces* detections —
the CV + reconstruction compute. That is assetpipe.

This module maps assetpipe's catalog rows into the exact payload
`api/quest-asset-scan.js` consumes (action `ingest_scan` / `preview_scan`),
so the GPU worker feeds the twin directly. See docs/SON_INTEGRATION.md.

Only the POST needs `requests`; building the payload is pure stdlib.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Iterable

# recon method -> son's per-detection `jay3d_state`
_JAY3D_STATE = {
    "procedural_box": "box proxy ready",
    "trellis": "single-image mesh — owner review",
    "nerfstudio": "multi-view scan mesh",
}

# capture source -> son capture-packet `evidence` (subset of its allowed set)
_EVIDENCE = {
    "quest3": ["rgb_keyframes", "spatial_anchors", "depth"],
    "folder": ["rgb_keyframes"],
    "demo": ["manual_note"],
}


def _pose_to_xyz_yaw(world_pose: list[float] | None) -> tuple[float, float, float, float]:
    """Row-major 4x4 camera/object-to-world -> (x, y, z, yaw_deg).

    Translation is the last column; yaw is rotation about world-up (approx).
    Returns zeros when no pose is available.
    """
    if not world_pose or len(world_pose) < 12:
        return (0.0, 0.0, 0.0, 0.0)
    m = world_pose
    x, y, z = m[3], m[7], m[11]
    yaw = math.degrees(math.atan2(m[4], m[0]))  # atan2(r10, r00)
    return (round(x, 4), round(y, 4), round(z, 4), round(yaw, 2))


def asset_to_detection(row: dict[str, Any], mesh_ref: str | None = None) -> dict[str, Any]:
    """One assetpipe catalog row -> one son `detected_assets[]` entry.

    ``mesh_ref`` overrides ``model_3d_ref`` with a blob URL when the mesh was
    uploaded to storage (see build_quest_scan_payload's ``uploader``); the raw
    PLY/GLB never travels through son's API body.
    """
    extra = _loads(row.get("extra"), {})
    tags = _loads(row.get("tags"), [])
    method = extra.get("recon_method", "")
    source = row.get("source", "folder")
    x, y, z, yaw = _pose_to_xyz_yaw(_loads(row.get("world_pose"), None))
    loc_label = row.get("location") or "unassigned zone"

    return {
        "asset_id": row["asset_id"],
        "label": row["label"],
        "kind": row.get("category", "physical_asset"),
        "category": (tags[0] if tags else "physical"),
        "confidence": float(extra.get("detection_score", 0.7)),
        "location": {
            "site": "Home Lab",
            "room": loc_label,
            "zone": loc_label,
            "shelf": "unassigned shelf",
            "x": x, "y": y, "z": z, "yaw": yaw,
        },
        "method": f"assetpipe: {method or 'procedural'} + open-vocab detect",
        "evidence": _EVIDENCE.get(source, ["rgb_keyframes"]),
        "closet_action": "upsert",
        "state_change": "assetpipe reconstruction ready for owner review",
        "jay3d_state": _JAY3D_STATE.get(method, "proxy pending"),
        # extra refs son's TwinItem schema can carry (model_3d_ref etc.)
        "model_3d_ref": mesh_ref if mesh_ref is not None else row.get("mesh_path", ""),
        "urdf_ref": row.get("urdf_path") or "",
        "preview_image_ref": row.get("thumbnail_path") or "",
        "dimensions_m": [row.get("dim_w"), row.get("dim_h"), row.get("dim_d")],
    }


def build_quest_scan_payload(
    rows: Iterable[dict[str, Any]],
    scan_id: str = "assetpipe-scan",
    label: str = "assetpipe reconstruction batch",
    account_id: str = "acct_lifetwin_shared",
    action: str = "preview_scan",
    uploader=None,
) -> dict[str, Any]:
    """Assemble the full POST body for `api/quest-asset-scan.js`.

    When ``uploader`` is given (see integrations.blob), each asset's mesh file
    is uploaded to blob storage and ``model_3d_ref`` is set to the returned
    URL — so a 30 MB+ PLY/GLB flows to storage while only its URL + metadata
    go through son's 4 MB API body.
    """
    detections = []
    for r in rows:
        mesh_ref = None
        if uploader is not None:
            mesh_path = r.get("mesh_path", "")
            if mesh_path and os.path.exists(mesh_path):
                # namespace by asset_id so identically-named meshes
                # (every asset has a "model.obj") don't collide in storage
                dest = f"{r['asset_id']}/{os.path.basename(mesh_path)}"
                mesh_ref = uploader.upload(mesh_path, dest_name=dest)
        detections.append(asset_to_detection(r, mesh_ref=mesh_ref))
    sources = {d["evidence"][0] for d in detections} if detections else set()
    return {
        "action": action,  # preview_scan (no persist) | ingest_scan
        "scan_id": scan_id,
        "account_id": account_id,
        "label": label,
        "source_device": {
            "platform": "meta_quest",
            "device_label": "Meta Quest 3 (Passthrough Camera API)",
            "app_label": "assetpipe capture + reconstruction worker",
        },
        "capture_types": sorted({e for d in detections for e in d["evidence"]})
        or ["rgb_keyframes"],
        "detected_assets": detections,
        "scene_version": {
            "version_id": f"{scan_id}-v1",
            "label": label,
            "changed": f"{len(detections)} assets reconstructed by assetpipe",
        },
    }


def post_scan(payload: dict[str, Any], endpoint: str, timeout_s: float = 30.0) -> dict[str, Any]:
    """POST the payload to a running son `/api/quest-asset-scan`. Needs requests."""
    import requests  # lazy

    resp = requests.post(endpoint, json=payload, timeout=timeout_s)
    resp.raise_for_status()
    return resp.json()


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default
