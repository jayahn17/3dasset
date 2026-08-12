"""RGB-D (+ pose) session I/O for nvblox / TSDF fusion.

KIRI-class geometry needs measured depth, not SfM guesses. This module
validates and loads the session layout that capture apps (iPhone LiDAR,
Quest Depth API) should write — see ``docs/NVBLOX_WORKFLOW.md``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional


class RgbdSessionError(ValueError):
    """Raised when a session cannot be fused (usually missing depth/pose)."""


# Camera-axis conventions for the 4x4 camera-to-world poses in manifest.json.
#   "opencv"   +x right, +y down, +z forward  — what every consumer here expects
#              (Open3D integrate, COLMAP text, gsplat viewmats, back-projection).
#   "arkit_gl" +x right, +y up,  -z forward   — raw ARKit frame.camera.transform,
#              which CrateScanner exports. OpenCV = arkit_gl @ diag(1,-1,-1,1).
# load_session() normalizes everything to "opencv"; sessions without an explicit
# "pose_convention" field fall back on "source" ("cratescanner" wrote raw ARKit).
_GL_TO_CV = (1.0, -1.0, -1.0)  # per-column sign on the rotation part


def resolve_pose_convention(manifest: dict) -> str:
    conv = manifest.get("pose_convention")
    if conv in ("opencv", "arkit_gl"):
        return conv
    return "arkit_gl" if manifest.get("source") == "cratescanner" else "opencv"


def pose_gl_to_cv(pose16: list[float]) -> list[float]:
    """Right-multiply a row-major c2w by diag(1,-1,-1,1): negate columns 1, 2."""
    p = list(pose16)
    for r in range(4):
        p[r * 4 + 1] = -p[r * 4 + 1]
        p[r * 4 + 2] = -p[r * 4 + 2]
    return p


@dataclass
class RgbdFrame:
    frame_id: str
    color_path: str
    depth_path: str
    timestamp: float = 0.0
    # 4x4 camera-to-world, row-major, 16 floats (required for fusion).
    pose: list[float] = field(default_factory=list)
    # fx, fy, cx, cy in pixels (depth image space preferred).
    intrinsics: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    # Optional calibration for the stored RGB image. High-resolution ARKit
    # captures are not necessarily a simple scale of the depth camera image.
    color_intrinsics: Optional[tuple[float, float, float, float]] = None
    color_size: Optional[tuple[int, int]] = None
    # depth PNG units: "mm" (uint16) or "m" (float32 .exr / .tif).
    depth_unit: str = "mm"


@dataclass
class RgbdSession:
    root: str
    frames: list[RgbdFrame]
    object_hint: Optional[str] = None
    location: Optional[str] = None
    depth_unit: str = "mm"
    # Convention the manifest poses were WRITTEN in; frames[].pose is always
    # normalized to OpenCV (+z forward) by load_session regardless.
    pose_convention_in: str = "opencv"

    @property
    def n_frames(self) -> int:
        return len(self.frames)


def inspect_path(path: str) -> dict:
    """Report whether ``path`` is RGB-only photos or a fuse-ready session.

    Does not raise — used by the CLI to tell the user what to capture next.
    """
    path = os.path.abspath(path)
    report: dict = {
        "path": path,
        "kind": "unknown",
        "ok_for_nvblox": False,
        "n_color": 0,
        "n_depth": 0,
        "n_with_pose": 0,
        "n_with_intrinsics": 0,
        "missing": [],
        "hint": "",
    }
    if not os.path.exists(path):
        report["missing"].append("path does not exist")
        report["hint"] = "Pass a session/ dir or a folder of captures."
        return report

    manifest = os.path.join(path, "manifest.json")
    if os.path.isfile(manifest):
        return _inspect_manifest_session(path, manifest, report)

    # Flat image folder (current Chest / leg-press dumps).
    exts = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
    colors = [
        os.path.join(path, f)
        for f in sorted(os.listdir(path))
        if os.path.splitext(f)[1].lower() in exts
        and not f.lower().startswith("depth")
    ]
    depth_dir = os.path.join(path, "depth")
    depths = []
    if os.path.isdir(depth_dir):
        depths = [
            os.path.join(depth_dir, f)
            for f in sorted(os.listdir(depth_dir))
            if os.path.splitext(f)[1].lower() in {".png", ".tif", ".tiff", ".exr"}
        ]
    report["kind"] = "rgb_folder"
    report["n_color"] = len(colors)
    report["n_depth"] = len(depths)
    if not colors:
        report["missing"].append("no color images")
    if not depths:
        report["missing"].append("depth maps (LiDAR / ARKit depth PNGs)")
    report["missing"].append("per-frame camera pose (4x4)")
    report["missing"].append("camera intrinsics (fx,fy,cx,cy)")
    report["hint"] = (
        "These look like RGB-only photos (e.g. Camera.app HEIC). "
        "Re-capture with an iPhone LiDAR app that exports RGB + depth + pose "
        "(Record3D / 3D Scanner App / Polycam → session layout). "
        "See docs/NVBLOX_WORKFLOW.md."
    )
    return report


def _inspect_manifest_session(root: str, manifest_path: str, report: dict) -> dict:
    report["kind"] = "session"
    with open(manifest_path) as fh:
        man = json.load(fh)
    frames = man.get("frames") or man.get("keyframes") or []
    report["n_color"] = 0
    report["n_depth"] = 0
    report["n_with_pose"] = 0
    report["n_with_intrinsics"] = 0
    for entry in frames:
        color = entry.get("color")
        depth = entry.get("depth")
        if color and os.path.isfile(os.path.join(root, color)):
            report["n_color"] += 1
        if depth and os.path.isfile(os.path.join(root, depth)):
            report["n_depth"] += 1
        pose = entry.get("pose")
        if isinstance(pose, (list, tuple)) and len(pose) == 16:
            report["n_with_pose"] += 1
        intr = entry.get("intrinsics")
        color_intr = entry.get("K_color")
        color_size = entry.get("color_size")
        depth_size = entry.get("depth_size")
        has_scaled_color_k = (
            isinstance(color_intr, (list, tuple))
            and len(color_intr) >= 4
            and isinstance(color_size, (list, tuple))
            and len(color_size) >= 2
            and isinstance(depth_size, (list, tuple))
            and len(depth_size) >= 2
        )
        if (isinstance(intr, (list, tuple)) and len(intr) >= 4) or has_scaled_color_k:
            report["n_with_intrinsics"] += 1

    if report["n_color"] == 0:
        report["missing"].append("color frames")
    if report["n_depth"] == 0:
        report["missing"].append("depth frames")
    elif report["n_depth"] < report["n_color"]:
        report["missing"].append(
            f"depth for every color frame ({report['n_depth']}/{report['n_color']})"
        )
    if report["n_with_pose"] < report["n_color"]:
        report["missing"].append(
            f"pose for every frame ({report['n_with_pose']}/{report['n_color']})"
        )
    if report["n_with_intrinsics"] < report["n_color"]:
        report["missing"].append(
            f"intrinsics for every frame ({report['n_with_intrinsics']}/{report['n_color']})"
        )

    # The frame-count floor has to enter `missing` too, or it fails the session
    # without ever naming itself: a 1-frame capture has every field present, so
    # `missing` stays empty and the caller prints "missing: unknown" beside
    # "color=1" — which reads as "colour is present" rather than "one frame".
    if report["n_color"] < 3:
        report["missing"].append(
            f"at least 3 color frames (capture has {report['n_color']}) — "
            "the scan was stopped before enough of the object was recorded"
        )

    report["ok_for_nvblox"] = not report["missing"] and report["n_color"] >= 3
    if report["ok_for_nvblox"]:
        report["hint"] = "Session is fuse-ready for nvblox / TSDF."
    else:
        report["hint"] = (
            "Fix missing fields, or re-export from Record3D / Polycam / Quest "
            "capture app. See docs/NVBLOX_WORKFLOW.md."
        )
    return report


def load_session(session_dir: str) -> RgbdSession:
    """Load a fuse-ready session or raise ``RgbdSessionError`` with a clear why."""
    report = inspect_path(session_dir)
    if report["kind"] != "session":
        raise RgbdSessionError(
            "Not an RGB-D session (need session/manifest.json).\n"
            f"Inspect: {json.dumps(report, indent=2)}"
        )
    if not report["ok_for_nvblox"]:
        missing = ", ".join(report["missing"]) or "unknown"
        raise RgbdSessionError(
            f"Session is not fuse-ready — missing: {missing}.\n"
            f"{report['hint']}\n"
            f"Detail: color={report['n_color']} depth={report['n_depth']} "
            f"pose={report['n_with_pose']} K={report['n_with_intrinsics']}"
        )

    root = os.path.abspath(session_dir)
    with open(os.path.join(root, "manifest.json")) as fh:
        man = json.load(fh)
    depth_unit = str(man.get("depth_unit", "mm")).lower()
    convention = resolve_pose_convention(man)
    entries = man.get("frames") or man.get("keyframes") or []
    frames: list[RgbdFrame] = []
    for i, entry in enumerate(entries):
        intr = entry.get("intrinsics")
        color_intr = entry.get("K_color")
        color_size = entry.get("color_size")
        depth_size = entry.get("depth_size")
        if intr is None:
            if not (
                isinstance(color_intr, (list, tuple))
                and len(color_intr) >= 4
                and isinstance(color_size, (list, tuple))
                and len(color_size) >= 2
                and isinstance(depth_size, (list, tuple))
                and len(depth_size) >= 2
            ):
                raise RgbdSessionError(
                    f"Frame {i} has neither depth intrinsics nor scalable K_color."
                )
            sx = float(depth_size[0]) / max(float(color_size[0]), 1.0)
            sy = float(depth_size[1]) / max(float(color_size[1]), 1.0)
            intr = (
                float(color_intr[0]) * sx,
                float(color_intr[1]) * sy,
                float(color_intr[2]) * sx,
                float(color_intr[3]) * sy,
            )
        pose = [float(x) for x in entry["pose"]]
        if convention == "arkit_gl":
            pose = pose_gl_to_cv(pose)
        frames.append(
            RgbdFrame(
                frame_id=entry.get("id", f"f{i:05d}"),
                color_path=os.path.join(root, entry["color"]),
                depth_path=os.path.join(root, entry["depth"]),
                timestamp=float(entry.get("t", i)),
                pose=pose,
                intrinsics=(float(intr[0]), float(intr[1]), float(intr[2]), float(intr[3])),
                color_intrinsics=(
                    tuple(float(x) for x in color_intr[:4])
                    if isinstance(color_intr, (list, tuple)) and len(color_intr) >= 4
                    else None
                ),
                color_size=(
                    (int(color_size[0]), int(color_size[1]))
                    if isinstance(color_size, (list, tuple)) and len(color_size) >= 2
                    else None
                ),
                depth_unit=str(entry.get("depth_unit", depth_unit)).lower(),
            )
        )
    return RgbdSession(
        root=root,
        frames=frames,
        object_hint=man.get("object_hint"),
        location=man.get("location"),
        depth_unit=depth_unit,
        pose_convention_in=convention,
    )


def write_example_manifest(path: str) -> str:
    """Write a documented empty-ish example manifest next to capture docs."""
    example = {
        "object_hint": "chest press machine",
        "location": "gym",
        "depth_unit": "mm",
        "frames": [
            {
                "id": "f00000",
                "t": 0.0,
                "color": "color/0000.jpg",
                "depth": "depth/0000.png",
                "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                "intrinsics": [900.0, 900.0, 640.0, 480.0],
            }
        ],
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(example, fh, indent=2)
    return path
