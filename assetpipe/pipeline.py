"""Pipeline orchestration: capture -> detect -> reconstruct -> digitalize -> catalog.

Each stage is an interface (see ``capture.base``, ``detect.base``,
``reconstruct.base``). Swap a stub adapter for a real backend without
touching this file — that is the whole point of the architecture.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from .capture.base import CaptureSource
from .catalog.store import AssetCatalog
from .detect.base import Detector
from .digitalize import mesh_io, urdf
from .reconstruct.base import Reconstructor
from .types import Asset


# Rough label -> category map. In production this comes from the
# open-vocabulary detector's taxonomy or a small classifier.
_CATEGORY_HINTS = {
    "box": "container",
    "carton": "container",
    "shirt": "clothing",
    "jacket": "clothing",
    "shoe": "clothing",
    "book": "media",
    "cup": "kitchenware",
    "chair": "furniture",
}


def _categorize(label: str) -> str:
    low = label.lower()
    for key, cat in _CATEGORY_HINTS.items():
        if key in low:
            return cat
    return "uncategorized"


@dataclass
class PipelineConfig:
    out_dir: str = "twin_out"
    source_name: str = "folder"
    location: str | None = None
    write_urdf: bool = True
    # Keep only the highest-score detection per label across the whole run.
    # Right default for a walk-around capture of a few distinct objects until
    # Grounded SAM 2 track ids provide true instance-level dedup.
    dedupe_labels: bool = False


class AssetPipeline:
    """Wires the four swappable stages together and writes to the catalog."""

    def __init__(
        self,
        capture: CaptureSource,
        detector: Detector,
        reconstructor: Reconstructor,
        catalog: AssetCatalog,
        config: PipelineConfig | None = None,
    ) -> None:
        self.capture = capture
        self.detector = detector
        self.reconstructor = reconstructor
        self.catalog = catalog
        self.config = config or PipelineConfig()
        os.makedirs(self.config.out_dir, exist_ok=True)

    def run(self) -> list[Asset]:
        """Execute the full flow and return the assets that were created."""
        pairs = (
            (frame, det)
            for frame in self.capture.frames()
            for det in self.detector.detect(frame)
        )
        if self.config.dedupe_labels:
            best: dict[str, tuple] = {}
            for frame, det in pairs:
                key = det.track_id or det.label.lower()
                if key not in best or det.score > best[key][1].score:
                    best[key] = (frame, det)
            pairs = best.values()

        assets: list[Asset] = []
        for frame, det in pairs:
            recon = self.reconstructor.reconstruct(frame, det)
            if recon is None:
                continue
            asset = self._digitalize(det, recon)
            self.catalog.add(asset)
            assets.append(asset)
        return assets

    def _digitalize(self, det, recon) -> Asset:
        asset_id = uuid.uuid4().hex[:12]
        asset_dir = os.path.join(self.config.out_dir, asset_id)
        os.makedirs(asset_dir, exist_ok=True)

        # Copy the reconstructed mesh in, preserving its format (obj/glb/ply).
        ext = os.path.splitext(recon.mesh_path)[1].lower() or ".obj"
        mesh_dst = os.path.join(asset_dir, f"model{ext}")
        mesh_io.copy_or_convert(recon.mesh_path, mesh_dst)

        # The offline viewer renders OBJ; for a GLB/PLY asset make a light
        # OBJ preview next to it (best-effort; needs the `reconstruct` extra).
        if ext != ".obj":
            try:
                from .util.mesh import to_preview_obj

                to_preview_obj(mesh_dst, os.path.join(asset_dir, "model.obj"))
            except Exception:
                pass

        urdf_path = None
        if self.config.write_urdf:
            urdf_path = os.path.join(asset_dir, "model.urdf")
            urdf.write_urdf(
                urdf_path,
                name=_safe_name(recon.label),
                mesh_rel=f"model{ext}",
                dimensions_m=recon.dimensions_m,
            )

        return Asset(
            asset_id=asset_id,
            label=recon.label,
            category=_categorize(recon.label),
            mesh_path=mesh_dst,
            urdf_path=urdf_path,
            dimensions_m=recon.dimensions_m,
            created_at=det.extra.get("timestamp", 0.0),
            source=self.config.source_name,
            location=self.config.location,
            world_pose=recon.world_pose,
            thumbnail_path=recon.thumbnail_path,
            tags=[det.label, recon.method],
            extra={"detection_score": det.score, "recon_method": recon.method},
        )


def _safe_name(label: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_") or "object"
