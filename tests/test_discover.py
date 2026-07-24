"""Tests for room-sweep object discovery.

These cover the two pieces of real logic that decide whether a 50-object sweep
produces 50 assets or 600 duplicates: the tracker that gives each physical
object one identity, and the gate that refuses to reconstruct a track too thin
to be worth 45 s of GPU. Both are pure functions of boxes and numbers, so they
test without a model, a GPU, or a single frame of video.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assetpipe.scene.discover import _IouTracker, _iou, _pick_views, grade


def test_iou_basics():
    assert _iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert _iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    # half-overlapping boxes: intersection 50, union 150
    assert abs(_iou((0, 0, 10, 10), (5, 0, 15, 10)) - (50 / 150)) < 1e-9


def test_tracker_keeps_one_object_across_frames():
    """An object drifting a little per frame stays ONE id — the whole point."""
    t = _IouTracker()
    ids = [t.update([(10 + 2 * i, 10, 60 + 2 * i, 60)], ["mouse"])[0]
           for i in range(6)]
    assert len(set(ids)) == 1


def test_tracker_separates_two_nearby_objects():
    """Two mice on a desk must not fuse — this is what cutout.py could not do."""
    t = _IouTracker()
    for _ in range(4):
        a, b = t.update([(0, 0, 50, 50), (200, 0, 250, 50)], ["mouse", "mouse"])
        assert a != b
    assert t.update([(0, 0, 50, 50)], ["mouse"])[0] == a  # and ids are stable


def test_tracker_label_flip_does_not_split_a_track():
    """The same mouse mislabelled 'phone' for one frame is still that mouse."""
    t = _IouTracker()
    box = (10, 10, 60, 60)
    first = t.update([box], ["mouse"])[0]
    flipped = t.update([box], ["phone"])[0]     # detector wobble
    assert flipped == first


def test_tracker_reuses_id_after_brief_occlusion():
    t = _IouTracker(buffer=10)
    first = t.update([(10, 10, 60, 60)], ["cup"])[0]
    for _ in range(3):
        t.update([], [])                        # object hidden behind something
    assert t.update([(12, 10, 62, 60)], ["cup"])[0] == first


def test_tracker_forgets_after_buffer_expires():
    """A different object appearing later where an old one sat is NOT the old one."""
    t = _IouTracker(buffer=2)
    first = t.update([(10, 10, 60, 60)], ["cup"])[0]
    for _ in range(5):
        t.update([], [])
    assert t.update([(10, 10, 60, 60)], ["cup"])[0] != first


def test_grade_gates_thin_tracks():
    assert grade(n_frames=8, area_frac=0.05, sharpness=100.0) == "ok"
    assert grade(n_frames=1, area_frac=0.05, sharpness=100.0) == "too_few_views"
    assert grade(n_frames=8, area_frac=0.0001, sharpness=100.0) == "too_small"
    assert grade(n_frames=8, area_frac=0.05, sharpness=0.5) == "blurry"


def test_pick_views_spreads_across_time_and_prefers_sharp():
    """Two sharp frames sit next to each other; the picker must still spread out,
    because near-duplicate views teach the generator nothing."""
    cands = [{"sharpness": s} for s in (10, 99, 98, 10, 10, 50)]
    picked = _pick_views(cands, 2)
    assert picked[0]["sharpness"] == 99      # sharpest of the first half
    assert picked[1]["sharpness"] == 50      # sharpest of the second half
    assert _pick_views(cands, 10) == cands   # fewer candidates than asked for


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all tests passed")
