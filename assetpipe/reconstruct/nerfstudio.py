"""Nerfstudio adapter — multi-view scan -> Gaussian splat -> surface mesh.

This is the high-fidelity path. The user walks around the object with the
Quest; SAM 2 keeps the object on one track id; those frames go to
Nerfstudio, which runs COLMAP for camera poses, trains splatfacto, and
exports geometry.

Unlike the per-frame reconstructors, this one is *session level*: it
accumulates the frames belonging to a track and produces one mesh at the
end. Call :meth:`add_view` per frame, then :meth:`finalize`.

PIPELINE (shell, run on a GPU box)
    ns-process-data images --data frames/ --output-dir proc/     # COLMAP poses
    ns-train splatfacto --data proc/                              # train splat
    ns-export gaussian-splat --load-config .../config.yml ...     # -> .ply
    # then surface-extract to a mesh (e.g. SDF/Poisson) -> .obj/.glb
    Repo: https://github.com/nerfstudio-project/nerfstudio  (+ gsplat)
"""

from __future__ import annotations

import os

from .base import Reconstructor
from ..types import Detection, Frame, Reconstruction


class NerfstudioReconstructor(Reconstructor):
    def __init__(self, work_dir: str) -> None:
        self.work_dir = work_dir
        os.makedirs(work_dir, exist_ok=True)
        self._views: dict[str, list[Frame]] = {}

    def add_view(self, frame: Frame, det: Detection) -> None:
        """Accumulate a segmented view for a tracked object."""
        key = det.track_id or det.label
        self._views.setdefault(key, []).append(frame)

    def reconstruct(self, frame: Frame, det: Detection) -> Reconstruction | None:
        # Per-frame call just buffers; real mesh comes from finalize().
        self.add_view(frame, det)
        return None

    def finalize(self, track_key: str, label: str) -> Reconstruction | None:  # pragma: no cover
        frames = self._views.get(track_key, [])
        if len(frames) < 20:  # too few views for a stable splat
            return None
        # TODO: write frames to disk, shell out to ns-process-data / ns-train
        # / ns-export, then run surface extraction -> mesh_path.
        raise NotImplementedError(
            "Wire the Nerfstudio CLI here — see module docstring for commands."
        )
