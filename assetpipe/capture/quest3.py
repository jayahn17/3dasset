"""Quest 3 Passthrough Camera API (PCA) ingestion — integration spec + stub.

WHY THIS IS A STUB
------------------
The camera frames originate on-device (Meta Horizon OS). Two supported
integration shapes:

1. On-device app (Unity / Unreal / Native / Spatial SDK) grabs frames via
   the Passthrough Camera API and runs detection on-device with the AI
   Building Blocks, then streams *results* (crops, masks, poses) to this
   Python pipeline over the network. Preferred for latency.

2. On-device app records a session (color frames + per-frame camera pose
   + intrinsics + optional depth from the Depth API) and uploads it. This
   Python side then does detection + reconstruction offline. Preferred for
   quality (heavy TRELLIS / Nerfstudio models run on a workstation/GPU).

CAPABILITIES (as of Horizon OS v76, public release)
    * RGB front cameras, up to 1280x960 @ 30 FPS, 40-60 ms latency.
    * Per-frame camera pose + intrinsics -> metric, world-anchored assets.
    * Depth API + Scene API give geometry priors for scale and placement.
    * Publishable to the Meta Horizon Store.

See ``docs/QUEST3_CAPTURE.md`` for the on-device Unity C# reference and the
session upload schema this class expects.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

from .base import CaptureSource
from ..types import Frame


class Quest3SessionSource(CaptureSource):
    """Reads an uploaded Quest 3 capture session directory.

    Expected layout (produced by the on-device recorder, schema documented
    in ``docs/QUEST3_CAPTURE.md``)::

        session/
          manifest.json     # list of frames w/ pose + intrinsics + depth
          color/0000.jpg ...
          depth/0000.png ...   (optional)
    """

    def __init__(self, session_dir: str) -> None:
        self.session_dir = session_dir

    def frames(self) -> Iterator[Frame]:
        manifest_path = os.path.join(self.session_dir, "manifest.json")
        if not os.path.exists(manifest_path):
            raise FileNotFoundError(
                f"No manifest.json in {self.session_dir!r}. See "
                "docs/QUEST3_CAPTURE.md for the expected session schema."
            )
        with open(manifest_path) as fh:
            manifest = json.load(fh)

        for i, entry in enumerate(manifest.get("frames", [])):
            color = os.path.join(self.session_dir, entry["color"])
            depth = entry.get("depth")
            yield Frame(
                frame_id=entry.get("id", f"f{i:05d}"),
                image_path=color,
                timestamp=float(entry.get("t", i)),
                depth_path=os.path.join(self.session_dir, depth) if depth else None,
                pose=entry.get("pose"),
                intrinsics=tuple(entry["intrinsics"]) if entry.get("intrinsics") else None,
                extra={"headset": "quest3"},
            )
