"""One room sweep -> every object in it, tracked and cut out.

This is the step that makes the pipeline scale. Everything downstream
already works on *one* object ("here are K views of a thing" -> generator ->
GLB); what cost the user an afternoon was producing that view stack by hand,
fifty times. So:

    room video ─▶ [detect + TRACK every instance] ─▶ per-object view stacks
                                                     (each -> scene.generate)

The tracker is the whole trick. A detector alone gives you "a mouse" in
frame 12 and "a mouse" in frame 13 with no idea they are the same mouse; a
tracker gives every physical object a persistent id, so its views accumulate
into one folder and one asset. That is the difference between 50 assets and
600 duplicates.

Two backends, same output:

* ``sam3``  — SAM 3's promptable *concept* segmentation: text prompts, all
  instances, segmented and tracked across the video in one pass. The right
  tool. Needs the gated ``sam3.pt`` (see ``docs/ROOM_SWEEP.md``).
* ``track`` — YOLO-World (open-vocab boxes) + a per-frame tracker, with SAM 2
  turning each box into a mask. Ungated, weights auto-download, works today.

Not every track deserves an asset. A pen glimpsed in two blurry frames will
generate a confident-looking blob, and a closet full of those is worse than
useless — so tracks are *gated* (:func:`grade`) and only ``ok`` ones are fed
to the generator. The rest are still reported, as "seen but not
reconstructed", which is honest and tells the user where to re-shoot.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field

# Household sweep vocabulary. YOLO-World is open-vocab, so this is only a
# default starting point — pass --classes to aim it at your own room.
DEFAULT_CLASSES = [
    "mouse", "keyboard", "monitor", "laptop", "phone", "headphones", "speaker",
    "camera", "remote", "charger", "cable", "book", "notebook", "pen", "cup",
    "mug", "bottle", "can", "box", "bag", "backpack", "shoe", "hat", "clothes",
    "chair", "lamp", "plant", "picture frame", "clock", "toy", "tool", "plate",
    "bowl", "scissors", "stapler", "wallet", "keys", "glasses", "watch",
]


@dataclass
class ObjectTrack:
    """One physical object, seen across N frames of the sweep."""

    track_id: int
    label: str
    views: list[str] = field(default_factory=list)   # RGBA cutouts on disk
    n_frames: int = 0            # how many frames the tracker held it for
    area_frac: float = 0.0       # median share of the frame it covered
    sharpness: float = 0.0       # best view's blur score
    status: str = "ok"           # ok | too_few_views | too_small | blurry
    out_dir: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _sharpness(img) -> float:
    """Variance of the Laplacian. Mirrors scene.generate._sharpness, but on an
    in-memory array — we grade crops before they are ever written."""
    import numpy as np

    a = np.asarray(img.convert("L").resize((128, 128)), np.float64)
    lap = (-4 * a
           + np.roll(a, 1, 0) + np.roll(a, -1, 0)
           + np.roll(a, 1, 1) + np.roll(a, -1, 1))
    return float(lap.var())


def _cutout(frame_img, mask, bbox, pad: float = 0.08):
    """Frame + mask -> tight RGBA crop of just this object.

    The mask is the alpha channel, so neighbouring clutter inside the same
    bounding box is knocked out — the generator sees the object, not the desk.
    """
    import numpy as np
    from PIL import Image

    w, h = frame_img.size
    x1, y1, x2, y2 = bbox
    px, py = pad * (x2 - x1), pad * (y2 - y1)
    box = (max(0, int(x1 - px)), max(0, int(y1 - py)),
           min(w, int(x2 + px)), min(h, int(y2 + py)))

    rgba = np.dstack([np.asarray(frame_img.convert("RGB")),
                      (np.asarray(mask, bool) * 255).astype(np.uint8)])
    return Image.fromarray(rgba, "RGBA").crop(box)


def _pick_views(candidates: list, k: int) -> list:
    """Pick k views: sharp, and spread across the sweep.

    Views adjacent in time are near-duplicates and teach the generator
    nothing, so the track is cut into k time windows and the sharpest frame in
    each wins. Same logic as generate.select_views, but over *time* rather
    than orbit angle — on a walk-around, time is the proxy for viewpoint.
    """
    if k >= len(candidates):
        return list(candidates)
    stride = len(candidates) / k
    out = []
    for i in range(k):
        lo = int(i * stride)
        hi = max(lo + 1, int((i + 1) * stride))
        out.append(max(candidates[lo:hi], key=lambda c: c["sharpness"]))
    return out


def grade(n_frames: int, area_frac: float, sharpness: float,
          min_views: int = 4, min_area_frac: float = 0.004,
          min_sharpness: float = 12.0) -> str:
    """Is this track worth a 45-second generation? -> status string.

    Deliberately conservative. A bad asset is more expensive than a missing
    one: it silently pollutes the closet, whereas a flagged track tells the
    user exactly what to walk back and re-shoot.
    """
    if n_frames < min_views:
        return "too_few_views"
    if area_frac < min_area_frac:
        return "too_small"
    if sharpness < min_sharpness:
        return "blurry"
    return "ok"


# ---------------------------------------------------------------------------
# backend: YOLO-World + tracker + SAM 2   (ungated, works today)
# ---------------------------------------------------------------------------

def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


class _IouTracker:
    """Greedy IoU tracker — the thing that gives each object a stable id.

    Ultralytics' own BoT-SORT/ByteTrack **never attaches to YOLOWorld** (on
    8.4.90 ``boxes.id`` comes back ``None`` on every frame, stills and video
    alike, even with detections well above ``new_track_thresh``), so we do the
    association ourselves rather than depend on a silent failure.

    That turns out to be the right call anyway: a room sweep is filmed at
    walking pace, so an object barely moves between frames and plain IoU is a
    sufficient — and completely predictable — association rule. No Kalman
    filter, no extra dependency, and thresholds we control. SAM 3 does its own
    tracking, so this only serves the fallback path.

    Labels are a *hint*, not a key: the same mouse legitimately flips between
    "mouse" and "phone" on a bad frame, so a matching label is rewarded but a
    differing one never blocks a match — the per-track vote sorts it out later.
    """

    def __init__(self, iou_thresh: float = 0.3, buffer: int = 10) -> None:
        self.iou_thresh = iou_thresh
        self.buffer = buffer
        self._tracks: dict[int, dict] = {}
        self._next = 0
        self._frame = 0

    def update(self, boxes: list, labels: list[str]) -> list[int]:
        """-> a track id for each box, in the order given."""
        alive = [t for t, s in self._tracks.items()
                 if self._frame - s["last_seen"] <= self.buffer]

        cand = []
        for di, (box, label) in enumerate(zip(boxes, labels)):
            for tid in alive:
                s = self._tracks[tid]
                score = _iou(box, s["box"]) + (0.1 if s["label"] == label else 0.0)
                if score >= self.iou_thresh:
                    cand.append((score, di, tid))
        cand.sort(reverse=True)

        assigned: dict[int, int] = {}
        used: set[int] = set()
        for _, di, tid in cand:
            if di in assigned or tid in used:
                continue
            assigned[di] = tid
            used.add(tid)

        out = []
        for di, (box, label) in enumerate(zip(boxes, labels)):
            tid = assigned.get(di)
            if tid is None:
                tid = self._next
                self._next += 1
            self._tracks[tid] = {"box": box, "label": label,
                                 "last_seen": self._frame}
            out.append(tid)
        self._frame += 1
        return out


def _track_yoloworld(frames: list[str], classes: list[str], conf: float,
                     masker: str, iou_thresh: float = 0.3):
    """-> {track_id: [ {frame, bbox, label, crop, sharpness, area_frac} ]}

    Frames MUST arrive in capture order — the tracker's whole premise is that
    consecutive frames overlap.
    """
    from PIL import Image
    from ultralytics import YOLOWorld

    model = YOLOWorld("yolov8s-worldv2.pt")
    model.set_classes(classes)

    segmenter = _make_masker(masker)
    tracker = _IouTracker(iou_thresh=iou_thresh)
    per_track: dict[int, list] = {}

    for path in frames:
        res = model.predict(path, conf=conf, verbose=False)[0]
        if res.boxes is None or len(res.boxes) == 0:
            tracker.update([], [])          # still advances the frame clock
            continue
        img = Image.open(path)
        W, H = img.size
        boxes = res.boxes.xyxy.tolist()
        labels = [res.names[int(c)] for c in res.boxes.cls.tolist()]
        ids = tracker.update(boxes, labels)

        masks = segmenter(path, boxes, (W, H))
        for tid, box, label, mask in zip(ids, boxes, labels, masks):
            crop = _cutout(img, mask, box)
            if crop.width < 32 or crop.height < 32:
                continue
            area = float(mask.sum()) / (W * H)
            per_track.setdefault(tid, []).append(
                {"frame": path, "bbox": box, "label": label, "crop": crop,
                 "sharpness": _sharpness(crop), "area_frac": area})
    return per_track


def _make_masker(kind: str):
    """-> f(frame_path, boxes, size) -> list of boolean masks.

    SAM 2 gives a real instance mask from a box prompt (ungated, ~150 MB, auto
    -downloads). ``rembg`` is the fallback: inside a tight crop the object *is*
    the salient thing, which is exactly the regime u2net was built for — it is
    only at room scale that salient-object matting falls apart.
    """
    if kind == "sam2":
        from ultralytics import SAM

        sam = SAM("sam2.1_b.pt")

        def _sam2(path, boxes, size):
            import numpy as np

            if not boxes:
                return []
            r = sam(path, bboxes=boxes, verbose=False)[0]
            if r.masks is None:
                return [np.ones(size[::-1], bool) for _ in boxes]
            return [np.asarray(m, bool) for m in r.masks.data.cpu().numpy()]

        return _sam2

    if kind == "rembg":
        from .cutout import matte

        def _rembg(path, boxes, size):
            import numpy as np
            from PIL import Image

            W, H = size
            img = Image.open(path)
            out = []
            for x1, y1, x2, y2 in boxes:
                box = (max(0, int(x1)), max(0, int(y1)),
                       min(W, int(x2)), min(H, int(y2)))
                a = np.asarray(matte(img.crop(box)).getchannel("A")) >= 128
                full = np.zeros((H, W), bool)
                full[box[1]:box[1] + a.shape[0], box[0]:box[0] + a.shape[1]] = a
                out.append(full)
            return out

        return _rembg

    if kind == "box":  # no matting at all — the bbox is the mask
        def _box(path, boxes, size):
            import numpy as np

            W, H = size
            out = []
            for x1, y1, x2, y2 in boxes:
                m = np.zeros((H, W), bool)
                m[max(0, int(y1)):int(y2), max(0, int(x1)):int(x2)] = True
                out.append(m)
            return out

        return _box

    raise ValueError(f"unknown masker {kind!r} (sam2 | rembg | box)")


# ---------------------------------------------------------------------------
# backend: SAM 3  (gated weights; the one we actually want)
# ---------------------------------------------------------------------------

def _track_sam3(frames: list[str], classes: list[str], conf: float,
                weights: str):
    """Same contract as _track_yoloworld, in a single SAM 3 video pass.

    SAM 3 does detection, segmentation and tracking together, so there is no
    box->mask hand-off and no separate tracker to lose an object behind an
    occlusion.
    """
    from PIL import Image
    from ultralytics.models.sam import SAM3VideoSemanticPredictor

    predictor = SAM3VideoSemanticPredictor(overrides=dict(
        conf=conf, task="segment", mode="predict", model=weights, save=False))

    per_track: dict[int, list] = {}
    results = predictor(source=frames, text=classes, stream=True)
    for path, res in zip(frames, results):
        if res.masks is None or res.boxes is None or res.boxes.id is None:
            continue
        img = Image.open(path)
        W, H = img.size
        for m, box, tid, cls in zip(res.masks.data.cpu().numpy(),
                                    res.boxes.xyxy.tolist(),
                                    res.boxes.id.tolist(),
                                    res.boxes.cls.tolist()):
            import numpy as np

            mask = np.asarray(m, bool)
            crop = _cutout(img, mask, box)
            if crop.width < 32 or crop.height < 32:
                continue
            per_track.setdefault(int(tid), []).append(
                {"frame": path, "bbox": box, "label": res.names[int(cls)],
                 "crop": crop, "sharpness": _sharpness(crop),
                 "area_frac": float(mask.sum()) / (W * H)})
    return per_track


# ---------------------------------------------------------------------------

def resolve_backend(backend: str, weights: str = "sam3.pt") -> str:
    if backend != "auto":
        return backend
    return "sam3" if os.path.exists(weights) else "track"


def discover_objects(
    frames: list[str],
    out_dir: str,
    classes: list[str] | None = None,
    backend: str = "auto",
    masker: str = "sam2",
    conf: float = 0.15,
    views: int = 8,
    min_views: int = 4,
    min_area_frac: float = 0.004,
    weights: str = "sam3.pt",
) -> list[ObjectTrack]:
    """Room frames -> one folder of RGBA views per physical object.

    Frames must be in capture order (the tracker depends on it).
    Returns every track found, graded; only ``t.ok`` ones are worth generating.
    """
    import numpy as np

    classes = classes or DEFAULT_CLASSES
    backend = resolve_backend(backend, weights)

    if backend == "sam3":
        per_track = _track_sam3(frames, classes, conf, weights)
    elif backend == "track":
        per_track = _track_yoloworld(frames, classes, conf, masker)
    else:
        raise ValueError(f"unknown backend {backend!r} (sam3 | track | auto)")

    tracks: list[ObjectTrack] = []
    for tid, obs in sorted(per_track.items()):
        # The per-frame label is noisy (the same mouse fires as "remote" in a
        # bad frame) — the track as a whole votes, weighted by nothing fancier
        # than how often it won. Consensus across N frames beats any one frame.
        label = Counter(o["label"] for o in obs).most_common(1)[0][0]
        area = float(np.median([o["area_frac"] for o in obs]))
        best = max(o["sharpness"] for o in obs)
        status = grade(len(obs), area, best,
                       min_views=min_views, min_area_frac=min_area_frac)

        t = ObjectTrack(track_id=tid, label=label, n_frames=len(obs),
                        area_frac=area, sharpness=best, status=status)
        if t.ok:
            slug = "".join(c if c.isalnum() else "_" for c in label)
            t.out_dir = os.path.join(out_dir, f"obj{tid:03d}_{slug}")
            os.makedirs(t.out_dir, exist_ok=True)
            for i, o in enumerate(_pick_views(obs, views)):
                p = os.path.join(t.out_dir, f"view_{i:02d}.png")
                o["crop"].save(p)
                t.views.append(p)
        tracks.append(t)

    tracks.sort(key=lambda t: (not t.ok, -t.area_frac))
    return tracks
