"""assetpipe — a Quest 3 -> digital-twin 3D asset recording pipeline.

Stages (each swappable, see the sub-packages):
    capture   -> detect -> reconstruct -> digitalize -> catalog
"""

from .types import Asset, Detection, Frame, Reconstruction
from .pipeline import AssetPipeline, PipelineConfig

__version__ = "0.1.0"
__all__ = [
    "Asset",
    "Detection",
    "Frame",
    "Reconstruction",
    "AssetPipeline",
    "PipelineConfig",
]
