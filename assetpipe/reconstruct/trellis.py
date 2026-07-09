"""TRELLIS adapter — single-image -> textured 3D mesh.

TRELLIS 2 (Microsoft Research) is currently the strongest open-source
image-to-3D model; Hunyuan3D 2.1 (Tencent) is the leading alternative and
drops into the same interface. Feed it one clean, background-removed crop
of the detected object (use the SAM 2 mask to matte it out) and it returns
a watertight, PBR-textured mesh — ideal for the "one glance = one asset"
capture UX.

INSTALL / RUN
    Easiest is to run TRELLIS as a local HTTP service (the official Gradio
    / API server) and point ``endpoint`` at it, so the heavy CUDA deps stay
    out of this package. Alternatively import microsoft/TRELLIS directly.
    Repo: https://github.com/microsoft/TRELLIS

This adapter crops the frame to the detection (+ mask), POSTs it, and saves
the returned GLB. The network call is left as a marked TODO so the package
imports cleanly without the service.
"""

from __future__ import annotations

import os

from .base import Reconstructor
from ..types import Detection, Frame, Reconstruction


class TrellisReconstructor(Reconstructor):
    def __init__(self, out_dir: str, endpoint: str = "http://localhost:8080/generate") -> None:
        self.out_dir = out_dir
        self.endpoint = endpoint
        os.makedirs(out_dir, exist_ok=True)
        self._n = 0

    def reconstruct(self, frame: Frame, det: Detection) -> Reconstruction | None:  # pragma: no cover
        crop = self._crop(frame, det)  # matte using det.mask_path if present
        self._n += 1
        mesh_path = os.path.join(self.out_dir, f"trellis_{self._n:04d}.glb")

        # TODO: POST `crop` to self.endpoint, stream the GLB to mesh_path.
        #   resp = requests.post(self.endpoint, files={"image": open(crop, "rb")})
        #   open(mesh_path, "wb").write(resp.content)
        raise NotImplementedError(
            "Start a TRELLIS/Hunyuan3D server and implement the POST here — "
            "see module docstring."
        )

    def _crop(self, frame: Frame, det: Detection) -> str:
        # Crop frame.image_path to det.bbox, apply det.mask_path as alpha.
        raise NotImplementedError
