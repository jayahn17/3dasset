from .base import Detector
from .heuristic import HeuristicDetector

# Real backends import heavy deps lazily; only the names are exported here.
from .yolo_world import YoloWorldDetector
from .grounded_sam import GroundedSam2Detector

__all__ = [
    "Detector",
    "HeuristicDetector",
    "YoloWorldDetector",
    "GroundedSam2Detector",
]
