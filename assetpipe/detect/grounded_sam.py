"""Grounded SAM 2 adapter — open-set detection + segmentation + tracking.

Grounding DINO locates objects from free-text prompts (SOTA open-vocab
accuracy); SAM 2 turns each box into a pixel-perfect mask and *tracks the
same instance across video frames*. The track id is what lets one physical
box stay a single asset while the user walks around it — exactly what
multi-view reconstruction needs.

Use this on the offline / high-quality path, or on keyframes escalated
from the cheaper YOLO-World detector.

INSTALL (see https://github.com/IDEA-Research/Grounded-SAM-2)
    pip install "git+https://github.com/IDEA-Research/Grounded-SAM-2.git"
    # provides grounding-dino + sam2 weights & configs

This is a thin adapter; wire the actual model calls in ``_lazy_model`` and
``detect`` following the upstream README. Masks are written next to the
frame and referenced by ``Detection.mask_path``.
"""

from __future__ import annotations

import os

from .base import Detector
from ..types import Detection, Frame


class GroundedSam2Detector(Detector):
    def __init__(
        self,
        classes: list[str] | None = None,
        box_threshold: float = 0.3,
        text_threshold: float = 0.25,
        mask_dir: str | None = None,
    ) -> None:
        super().__init__(classes)
        self.box_threshold = box_threshold
        self.text_threshold = text_threshold
        self.mask_dir = mask_dir
        self._gdino = None
        self._sam = None

    def _lazy_model(self):
        if self._gdino is None:
            # from grounding_dino.util.inference import load_model, predict
            # from sam2.build_sam import build_sam2
            # self._gdino = load_model(...)
            # self._sam  = build_sam2(...)
            raise NotImplementedError(
                "Install Grounded-SAM-2 and wire the model loads here — see "
                "the module docstring for the repo link."
            )
        return self._gdino, self._sam

    def detect(self, frame: Frame) -> list[Detection]:  # pragma: no cover - needs weights
        gdino, sam = self._lazy_model()
        prompt = " . ".join(self.classes) if self.classes else "object"
        # boxes, phrases, scores = predict(gdino, frame.image_path, prompt, ...)
        # masks = sam.segment(frame.image_path, boxes)
        # -> write each mask to self.mask_dir and build Detection(track_id=...)
        raise NotImplementedError(
            f"Run Grounding DINO (prompt={prompt!r}) + SAM 2 on {frame.image_path}."
        )

    def _mask_path(self, frame: Frame, idx: int) -> str:
        base = self.mask_dir or os.path.dirname(frame.image_path)
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, f"{frame.frame_id}_m{idx}.png")
