"""Object detection interface (open-vocabulary + optional segmentation).

The detector turns a 2D :class:`Frame` into zero or more
:class:`Detection` objects. This is the "convert the 3D world the camera
sees into 2D detections we can identify" step in the vision.

Open-vocabulary means the caller supplies the class names as text
(``["cardboard box", "sneaker", "coffee mug"]``) instead of being locked
to a fixed 80-class list — essential for "track *every* object, clothes to
anything".
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import Detection, Frame


class Detector(ABC):
    def __init__(self, classes: list[str] | None = None) -> None:
        # None => detect anything the backend proposes.
        self.classes = classes

    @abstractmethod
    def detect(self, frame: Frame) -> list[Detection]:
        raise NotImplementedError
