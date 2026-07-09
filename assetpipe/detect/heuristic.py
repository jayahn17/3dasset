"""Zero-dependency fallback detector.

This does NOT do real recognition — it exists so the pipeline runs
end-to-end (and tests pass) on a machine with nothing installed. It reads
the image resolution if Pillow is present, otherwise assumes 1280x960
(the Quest 3 PCA resolution), and emits one full-frame detection whose
label is the first requested class.

Swap in :class:`YoloWorldDetector` or :class:`GroundedSam2Detector` for
real perception.
"""

from __future__ import annotations

from .base import Detector
from ..types import Detection, Frame


class HeuristicDetector(Detector):
    def __init__(self, classes: list[str] | None = None, default_label: str = "object") -> None:
        super().__init__(classes)
        self.default_label = default_label

    def detect(self, frame: Frame) -> list[Detection]:
        w, h = _image_size(frame.image_path)
        label = self.classes[0] if self.classes else self.default_label
        return [
            Detection(
                label=label,
                score=0.5,  # honest: this is a placeholder, not a real score
                bbox=(0.0, 0.0, float(w), float(h)),
                frame_id=frame.frame_id,
                extra={"timestamp": frame.timestamp, "detector": "heuristic"},
            )
        ]


def _image_size(path: str) -> tuple[int, int]:
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as im:
            return im.size
    except Exception:
        return (1280, 960)
