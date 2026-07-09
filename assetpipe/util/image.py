"""Image crop + matte helpers for the generative reconstruction path.

Single-image 3D models (TRELLIS, Hunyuan3D) want ONE clean, tightly
cropped, background-removed picture of the object. Given a frame, a bbox,
and (optionally) a SAM 2 mask, produce that crop as an RGBA PNG.

Uses Pillow + numpy (installed via the `reconstruct` extra). Imported
lazily by callers so the stdlib core stays dependency-free.
"""

from __future__ import annotations

import os


def crop_object(
    image_path: str,
    bbox: tuple[float, float, float, float],
    out_path: str,
    mask_path: str | None = None,
    pad: float = 0.08,
) -> str:
    """Write a padded RGBA crop of the object to ``out_path``.

    If ``mask_path`` is given (a SAM 2 instance mask), pixels outside the
    mask are made transparent so the reconstructor sees only the object.
    """
    from PIL import Image  # lazy
    import numpy as np

    img = Image.open(image_path).convert("RGBA")
    W, H = img.size
    x1, y1, x2, y2 = bbox
    # pad the box a little so we don't clip the silhouette
    dx, dy = (x2 - x1) * pad, (y2 - y1) * pad
    x1 = max(0, int(x1 - dx)); y1 = max(0, int(y1 - dy))
    x2 = min(W, int(x2 + dx)); y2 = min(H, int(y2 + dy))

    if mask_path and os.path.exists(mask_path):
        mask = Image.open(mask_path).convert("L").resize((W, H))
        arr = np.array(img)
        arr[..., 3] = np.array(mask)  # mask -> alpha
        img = Image.fromarray(arr, "RGBA")

    crop = img.crop((x1, y1, x2, y2))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    crop.save(out_path)
    return out_path
