"""Core data types that flow through the pipeline.

The pipeline is a chain of stages, each consuming and producing these
dataclasses:

    Frame  ->  Detection  ->  Reconstruction  ->  Asset

Everything here is plain stdlib so the box-test slice runs with zero
third-party dependencies. Heavier backends (YOLO-World, SAM 2, TRELLIS,
Nerfstudio) attach their richer data onto the ``extra`` dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Frame:
    """A single captured RGB(-D) frame from a capture source.

    ``image_path`` points at the color image on disk. ``depth_path`` and
    ``pose`` are optional but strongly recommended when they are available
    (the Quest 3 Passthrough Camera API exposes camera intrinsics + head
    pose per frame, which makes metric reconstruction and world-placement
    possible).
    """

    frame_id: str
    image_path: str
    timestamp: float = 0.0
    depth_path: Optional[str] = None
    # 4x4 camera-to-world matrix, row-major, flattened to 16 floats.
    pose: Optional[list[float]] = None
    # fx, fy, cx, cy in pixels.
    intrinsics: Optional[tuple[float, float, float, float]] = None
    extra: dict = field(default_factory=dict)


@dataclass
class Detection:
    """One detected + (optionally) segmented object inside a frame."""

    label: str
    score: float
    # Axis-aligned box in pixels: (x_min, y_min, x_max, y_max).
    bbox: tuple[float, float, float, float]
    frame_id: str
    # Path to a binary/instance mask PNG (from SAM 2), if segmented.
    mask_path: Optional[str] = None
    # Stable id used to link the same physical object across frames.
    track_id: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass
class Reconstruction:
    """A 3D reconstruction of a single detected object."""

    label: str
    mesh_path: str  # .obj / .glb / .ply produced by the reconstructor
    # Bounding size in meters (width, height, depth).
    dimensions_m: tuple[float, float, float]
    method: str  # "procedural_box" | "trellis" | "nerfstudio" | ...
    # Optional world placement (4x4 flattened) of the object origin.
    world_pose: Optional[list[float]] = None
    thumbnail_path: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass
class Asset:
    """A digitalized object as it lives in the twin / control center.

    This is the record that gets written to the catalog and rendered in
    the viewer. It bundles the mesh, the URDF, provenance, and the
    metadata a user needs to answer "what did I own and where was it?".
    """

    asset_id: str
    label: str
    category: str
    mesh_path: str
    urdf_path: Optional[str]
    dimensions_m: tuple[float, float, float]
    created_at: float
    source: str  # "quest3" | "folder" | "demo"
    location: Optional[str] = None  # human label: "bedroom closet", "garage"
    world_pose: Optional[list[float]] = None
    thumbnail_path: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
