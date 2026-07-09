"""Ingest a folder of images as a capture source.

Use this to run the pipeline on photos or extracted video frames you
already have on disk — for example frames pulled off a Quest recording,
or a phone-camera dry run before the headset integration is ready.
"""

from __future__ import annotations

import glob
import os
from collections.abc import Iterator

from .base import CaptureSource
from ..types import Frame

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


class FolderSource(CaptureSource):
    def __init__(self, path: str) -> None:
        self.path = path

    def frames(self) -> Iterator[Frame]:
        paths = sorted(
            p
            for p in glob.glob(os.path.join(self.path, "*"))
            if p.lower().endswith(_IMAGE_EXTS)
        )
        for i, p in enumerate(paths):
            yield Frame(
                frame_id=f"f{i:05d}",
                image_path=p,
                timestamp=float(i),
            )
