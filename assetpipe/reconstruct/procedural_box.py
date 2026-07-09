"""Procedural box reconstructor — the zero-dependency 'box test' backend.

For a box (or any roughly cuboid item) we do not need a diffusion model:
estimate width/height/depth and emit a clean cuboid mesh. This makes the
whole pipeline runnable today and is a genuinely useful primitive —
containers, furniture, appliances and packages are well approximated by
oriented bounding boxes, and it is the right first target for the vision's
"test on 'box'" milestone.

Dimensions are taken, in priority order, from:
    1. det.extra["dimensions_m"] if a real backend estimated them,
    2. a metric estimate from the bbox + depth if depth is available,
    3. the ``default_dims`` fallback.
"""

from __future__ import annotations

from .base import Reconstructor
from ..digitalize.mesh_io import write_box_obj
from ..types import Detection, Frame, Reconstruction


class ProceduralBoxReconstructor(Reconstructor):
    def __init__(
        self,
        out_dir: str,
        default_dims: tuple[float, float, float] = (0.30, 0.30, 0.30),
    ) -> None:
        self.out_dir = out_dir
        self.default_dims = default_dims
        self._n = 0

    def reconstruct(self, frame: Frame, det: Detection) -> Reconstruction | None:
        dims = self._estimate_dims(frame, det)
        self._n += 1
        mesh_path = f"{self.out_dir}/box_{self._n:04d}.obj"
        write_box_obj(mesh_path, dims)
        return Reconstruction(
            label=det.label,
            mesh_path=mesh_path,
            dimensions_m=dims,
            method="procedural_box",
            world_pose=frame.pose,
            extra={"track_id": det.track_id},
        )

    def _estimate_dims(self, frame: Frame, det: Detection) -> tuple[float, float, float]:
        if "dimensions_m" in det.extra:
            return tuple(det.extra["dimensions_m"])  # type: ignore[return-value]

        # If we have depth + intrinsics we could back-project the bbox to
        # metric size. Kept as a clearly-marked hook rather than a fake.
        if frame.depth_path and frame.intrinsics:
            est = _metric_from_depth(frame, det)
            if est:
                return est

        return self.default_dims


def _metric_from_depth(frame: Frame, det: Detection):
    """Placeholder for metric sizing from a depth frame + intrinsics.

    Real implementation: sample median depth ``z`` inside the mask, then
    width = (x2 - x1) * z / fx, height = (y2 - y1) * z / fy, depth ~ span of
    z across the mask. Returns None until wired to a depth reader.
    """
    return None
