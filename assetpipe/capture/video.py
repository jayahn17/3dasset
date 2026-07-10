"""Video-file capture source — the zero-headset-code Quest 3 test path.

The Quest 3 system recorder captures the passthrough view (Meta button ->
Camera -> Record). Pull the MP4 off the headset (adb pull / share) and feed
it here: frames are extracted with ffmpeg at a chosen rate and flow through
the normal detect -> reconstruct -> digitalize -> twin pipeline.

This makes "test the pipeline with my Quest 3" possible today, before the
Unity PCA capture app exists. No per-frame pose (system recordings don't
carry it), so metric scale comes from --box-dims or the reconstructor;
the PCA app upgrade adds pose + intrinsics later.

Requires ffmpeg on PATH (or pass ffmpeg_bin / set FFMPEG_BIN).
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from collections.abc import Iterator

from .base import CaptureSource
from ..types import Frame


def find_ffmpeg(explicit: str | None = None) -> str:
    """Locate ffmpeg: explicit arg > $FFMPEG_BIN > PATH > imageio-ffmpeg bundle."""
    for candidate in (explicit, os.environ.get("FFMPEG_BIN"), shutil.which("ffmpeg")):
        if candidate and (os.path.sep not in candidate or os.path.exists(candidate)):
            return candidate
    try:
        import imageio_ffmpeg  # optional: ships a full static ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise FileNotFoundError(
        "ffmpeg not found — install it (apt install ffmpeg), set FFMPEG_BIN, "
        "or `pip install imageio-ffmpeg`"
    )


class VideoSource(CaptureSource):
    def __init__(
        self,
        video_path: str,
        work_dir: str,
        fps: float = 2.0,
        max_frames: int = 300,
        ffmpeg_bin: str | None = None,
    ) -> None:
        if not os.path.exists(video_path):
            raise FileNotFoundError(video_path)
        self.video_path = video_path
        self.work_dir = work_dir
        self.fps = fps
        self.max_frames = max_frames
        self.ffmpeg_bin = find_ffmpeg(ffmpeg_bin)

    def extract(self) -> list[str]:
        """Run ffmpeg once; return the extracted frame paths."""
        frames_dir = os.path.join(self.work_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        pattern = os.path.join(frames_dir, "f%05d.jpg")
        cmd = [
            self.ffmpeg_bin, "-y", "-i", self.video_path,
            "-vf", f"fps={self.fps}",
            "-frames:v", str(self.max_frames),
            "-q:v", "2",  # high-quality JPEG
            pattern,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-800:]}")
        return sorted(glob.glob(os.path.join(frames_dir, "f*.jpg")))

    def frames(self) -> Iterator[Frame]:
        for i, path in enumerate(self.extract()):
            yield Frame(
                frame_id=f"v{i:05d}",
                image_path=path,
                timestamp=i / self.fps,
                extra={"source_video": self.video_path},
            )
