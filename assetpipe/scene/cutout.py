"""Photo -> matted object cutouts. The right way to remove a background.

Every earlier attempt to isolate an object *geometrically* (RANSAC planes,
biggest cluster, voxel components on a point cloud) failed: it kept the
wall, or the desk, or nothing at all. Objects are trivially separable in
2D pixels, and a purpose-built matting network does it in one shot.

This also handles the case a scan cannot: **several objects in frame**.
The matte is split into connected components, so two mice on a desk become
two independent assets instead of one fused blob.

    cutouts = split_objects("photo.jpg")      # -> [Cutout, Cutout]
    cutouts[0].image.save("mouse_a.png")      # RGBA, cropped, ready to generate

Needs ``rembg`` (CPU onnx) + ``scipy``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_session = None


@dataclass
class Cutout:
    image: object            # PIL RGBA, cropped to the object
    area_frac: float         # share of the photo the object covers
    centroid: tuple          # (y, x) in the original photo, for grouping
    bbox: tuple              # (left, upper, right, lower)


def _get_session():
    global _session
    if _session is None:
        from rembg import new_session

        _session = new_session("u2net")
    return _session


def matte(path_or_image):
    """Photo -> RGBA with the background knocked out."""
    from PIL import Image
    from rembg import remove

    img = (Image.open(path_or_image) if isinstance(path_or_image, (str, os.PathLike))
           else path_or_image)
    return remove(img.convert("RGB"), session=_get_session()).convert("RGBA")


def split_objects(path_or_image, min_area_frac: float = 0.02,
                  pad: float = 0.08, alpha_thresh: int = 128,
                  erode: int = 15, color_guard: bool = False,
                  color_tol: float = 3.5) -> list[Cutout]:
    """Matte, then split into one Cutout per distinct object.

    Two traps, both seen on real photos:

    * a matting net grabs *any* salient thing — printed text on a mousepad
      comes out as foreground, and those letters bridge two objects into one
      blob. So the mask is ERODED before labelling: thin bridges snap, while
      the objects survive. Each component is then regrown inside the original
      matte, so the object keeps its true silhouette.
    * a glossy highlight can split one object in two — closing first heals it.

    ``color_guard`` additionally strips vivid print from the matte. It is OFF
    by default, and that is a hard-won default: where the lettering *overlaps*
    the object's edge, removing it bites notches out of the silhouette, and a
    generator turns those notches into mangled geometry — measurably worse
    than just leaving the text in. Cutting clutter out of a bad photo is not a
    substitute for a clean one: shoot one object on a plain surface.

    ``min_area_frac`` drops specks. Results are ordered largest-first; group
    across photos with :func:`group_by_position`.
    """
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    rgba = matte(path_or_image)
    a = np.asarray(rgba.getchannel("A"))
    h, w = a.shape
    solid = a >= alpha_thresh
    solid = ndimage.binary_closing(solid, structure=np.ones((7, 7)))

    if color_guard and solid.any():
        # Kill vivid print BEFORE anything else. A matting net treats bold
        # lettering on a desk mat as foreground; those glyphs are thick enough
        # to survive erosion, so they end up inside the seed and get modelled
        # as 3D geometry. Saturation separates them cleanly: products here are
        # near-greyscale, print is vivid. If the object itself is colourful its
        # own saturation raises the bar, so nothing real is lost.
        arr = np.asarray(rgba, np.float64)[..., :3]
        hi, lo = arr.max(axis=2), arr.min(axis=2)
        sat = np.where(hi > 1, (hi - lo) / np.maximum(hi, 1), 0.0)
        typical = float(np.percentile(sat[solid], 60))
        limit = max(typical * 2.0 + 0.10, 0.22)
        solid &= sat <= limit
        solid = ndimage.binary_opening(solid, structure=np.ones((5, 5)))

    seeds = ndimage.binary_erosion(solid, structure=np.ones((erode, erode)))
    labels, n = ndimage.label(seeds)
    out: list[Cutout] = []
    for i in range(1, n + 1):
        seed = labels == i
        if seed.sum() < 0.005 * h * w:
            continue
        # regrow the eroded seed back to the real silhouette, staying inside
        # the matte and never touching a neighbouring object
        grown = ndimage.binary_dilation(
            seed, structure=np.ones((3, 3)), iterations=erode, mask=solid)
        # ...but only into pixels that LOOK like the object. Printed text on a
        # desk mat touches the object and the matte keeps it, so it would grow
        # right back and end up modelled as 3D geometry. The eroded seed is
        # definitely object, so use its colour spread as the test.
        if color_guard:
            # Saturation, not brightness, is what separates a printed mat from
            # a product: the mice are near-greyscale, the lettering is vivid.
            # (An RGB-distance test fails here — a bright silver edge widens
            # the object's spread enough to admit yellow.) For a genuinely
            # colourful object the seed is saturated too, so the bar rises
            # with it and nothing real gets cut.
            arr = np.asarray(rgba, np.float64)[..., :3]
            hi, lo = arr.max(axis=2), arr.min(axis=2)
            sat = np.where(hi > 1, (hi - lo) / np.maximum(hi, 1), 0.0)
            limit = max(float(np.percentile(sat[seed], 97)) * 1.6, 0.18)
            grown &= (sat <= limit) | seed
            grown = ndimage.binary_closing(grown, structure=np.ones((5, 5)))
            grown = ndimage.binary_opening(grown, structure=np.ones((5, 5)))
            lab2, n2 = ndimage.label(grown)   # keep only the piece holding the seed
            if n2 > 1:
                keep_id = np.bincount(lab2[seed]).argmax()
                grown = lab2 == keep_id
        m = grown
        area = int(m.sum())
        frac = area / (h * w)
        if frac < min_area_frac:
            continue
        ys, xs = np.where(m)
        py, px = int(pad * (ys.max() - ys.min())), int(pad * (xs.max() - xs.min()))
        box = (max(0, int(xs.min()) - px), max(0, int(ys.min()) - py),
               min(w, int(xs.max()) + px + 1), min(h, int(ys.max()) + py + 1))
        # keep only THIS object's pixels — a neighbour must not bleed into the crop
        only = np.asarray(rgba).copy()
        only[..., 3] = np.where(m, only[..., 3], 0)
        cut = Image.fromarray(only, "RGBA").crop(box)
        out.append(Cutout(image=cut, area_frac=frac,
                          centroid=(float(ys.mean()), float(xs.mean())), bbox=box))
    out.sort(key=lambda c: -c.area_frac)
    return out


def group_by_position(per_photo: list[list[Cutout]]) -> list[list[Cutout]]:
    """Match the same physical object across photos.

    With a static scene and a moving camera, objects keep their rough
    arrangement — so sorting each photo's cutouts by vertical position and
    zipping them is enough to keep "the top mouse" with "the top mouse".
    Photos with a different object count are skipped rather than guessed at.
    """
    counts = [len(c) for c in per_photo if c]
    if not counts:
        return []
    k = max(set(counts), key=counts.count)      # the usual number of objects
    groups: list[list[Cutout]] = [[] for _ in range(k)]
    for cuts in per_photo:
        if len(cuts) != k:
            continue                            # ambiguous -> don't corrupt a group
        for slot, c in enumerate(sorted(cuts, key=lambda c: c.centroid[0])):
            groups[slot].append(c)
    return [g for g in groups if g]


def write_cutouts(photo_paths: list[str], out_dir: str,
                  min_area_frac: float = 0.02) -> list[list[str]]:
    """Split every photo, group the objects, write one folder per object.

    -> [[obj0_view0.png, obj0_view1.png, ...], [obj1_view0.png, ...]]
    """
    per_photo = [split_objects(p, min_area_frac=min_area_frac) for p in photo_paths]
    groups = group_by_position(per_photo)
    written: list[list[str]] = []
    for gi, group in enumerate(groups):
        d = os.path.join(out_dir, f"object_{gi}")
        os.makedirs(d, exist_ok=True)
        paths = []
        for vi, cut in enumerate(group):
            p = os.path.join(d, f"view_{vi:02d}.png")
            cut.image.save(p)
            paths.append(p)
        written.append(paths)
    return written
