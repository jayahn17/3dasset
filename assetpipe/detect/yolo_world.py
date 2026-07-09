"""YOLO-World adapter — real-time, text-prompted open-vocabulary detection.

Best for the *live* on-headset / streaming path: fast, runs at interactive
rates, takes a list of class-name prompts. Lower accuracy ceiling than
Grounding DINO but far cheaper — use it as the always-on detector and
escalate promising crops to Grounded SAM 2 for a clean mask.

INSTALL
    pip install ultralytics            # ships YOLO-World weights
    # or the original: pip install inference  (Roboflow)

This module imports lazily so the package works without the dependency.
"""

from __future__ import annotations

from .base import Detector
from ..types import Detection, Frame


class YoloWorldDetector(Detector):
    def __init__(
        self,
        classes: list[str] | None = None,
        weights: str = "yolov8x-worldv2.pt",
        conf: float = 0.25,
    ) -> None:
        super().__init__(classes)
        self.weights = weights
        self.conf = conf
        self._model = None

    def _lazy_model(self):
        if self._model is None:
            from ultralytics import YOLOWorld  # type: ignore

            self._model = YOLOWorld(self.weights)
            if self.classes:
                self._model.set_classes(self.classes)
        return self._model

    def detect(self, frame: Frame) -> list[Detection]:
        model = self._lazy_model()
        results = model.predict(frame.image_path, conf=self.conf, verbose=False)
        out: list[Detection] = []
        for r in results:
            names = r.names
            for b in r.boxes:
                cls_id = int(b.cls[0])
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
                out.append(
                    Detection(
                        label=names.get(cls_id, str(cls_id)),
                        score=float(b.conf[0]),
                        bbox=(x1, y1, x2, y2),
                        frame_id=frame.frame_id,
                        extra={"timestamp": frame.timestamp, "detector": "yolo_world"},
                    )
                )
        return out
