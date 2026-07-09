"""Reconstruction interface: turn a detected object into a 3D mesh.

Two families of backend implement this:

* Single-image generative (TRELLIS 2, Hunyuan3D 2.1): one good crop ->
  full textured mesh in seconds. Great for a quick capture of an isolated
  item (the "point at it and it's digitized" UX).

* Multi-view scan (Nerfstudio + gsplat + COLMAP): many frames of the same
  tracked object -> Gaussian splat -> surface mesh. Higher metric fidelity;
  needs the user to walk around the object.

``reconstruct`` may return ``None`` if it cannot produce a usable mesh for
this detection (e.g. too few views yet).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import Detection, Frame, Reconstruction


class Reconstructor(ABC):
    @abstractmethod
    def reconstruct(self, frame: Frame, det: Detection) -> Reconstruction | None:
        raise NotImplementedError
