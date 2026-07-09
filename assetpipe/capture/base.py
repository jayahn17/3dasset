"""Capture source interface.

A capture source is any producer of :class:`Frame` objects — a folder of
photos, a video file, or a live Quest 3 Passthrough Camera stream. The
rest of the pipeline never knows which one it is talking to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..types import Frame


class CaptureSource(ABC):
    @abstractmethod
    def frames(self) -> Iterator[Frame]:
        """Yield frames in capture order."""
        raise NotImplementedError
