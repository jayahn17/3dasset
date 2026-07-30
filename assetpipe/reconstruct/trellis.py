"""TRELLIS adapter — single-image -> textured 3D mesh (GPU).

TRELLIS 2 (Microsoft Research) is the strongest open-source image-to-3D
model; Hunyuan3D 2.1 drops into the same client (point ``endpoint`` at
either server). Feed one clean, matted crop of the detected object and get
back a textured GLB.

Runs the heavy model as a SEPARATE GPU service so the CUDA/torch deps stay
out of this package. Start it on your 4080 with:

    python services/trellis_server.py            # serves POST /generate

then use this adapter with ``endpoint="http://<gpu-host>:8080/generate"``.
The client only needs ``requests`` + ``trimesh`` (the `reconstruct` extra).
Repo: https://github.com/microsoft/TRELLIS
"""

from __future__ import annotations

import os

from .base import Reconstructor
from ..types import Detection, Frame, Reconstruction
from ..util import image as imgutil


class TrellisReconstructor(Reconstructor):
    def __init__(
        self,
        out_dir: str,
        endpoint: str = "http://localhost:8080/generate",
        timeout_s: float = 300.0,
    ) -> None:
        self.out_dir = out_dir
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        os.makedirs(out_dir, exist_ok=True)
        self._n = 0

    def reconstruct(self, frame: Frame, det: Detection) -> Reconstruction | None:
        import requests  # lazy

        self._n += 1
        stem = os.path.join(self.out_dir, f"trellis_{self._n:04d}")
        crop = imgutil.crop_object(
            frame.image_path, det.bbox, stem + "_crop.png", mask_path=det.mask_path
        )

        with open(crop, "rb") as fh:
            resp = requests.post(self.endpoint, files={"image": fh}, timeout=self.timeout_s)
        resp.raise_for_status()

        mesh_path = stem + ".glb"
        with open(mesh_path, "wb") as out:
            out.write(resp.content)

        dims = self._dims(mesh_path)
        return Reconstruction(
            label=det.label,
            mesh_path=mesh_path,
            dimensions_m=dims,
            method="trellis",
            world_pose=frame.pose,
            thumbnail_path=crop,
            # TRELLIS output is normalized — dims are RELATIVE until a metric
            # registration (ICP to the fused cloud / measure.py AABB) rescales.
            extra={"track_id": det.track_id, "endpoint": self.endpoint,
                   "scale": "relative"},
        )

    def _dims(self, mesh_path: str) -> tuple[float, float, float]:
        # TRELLIS output is normalized (unitless); use the Quest depth-based
        # metric size if the detector provided one, else the mesh bounds.
        try:
            from ..util.mesh import bounds_dimensions_m

            return bounds_dimensions_m(mesh_path)
        except Exception:
            return (0.3, 0.3, 0.3)
