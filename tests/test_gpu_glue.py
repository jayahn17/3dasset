"""Tests for the real-backend glue (crop, GLB preview, GLB asset path).

These need the `reconstruct` extra (pillow, numpy, trimesh). They SKIP
cleanly when it isn't installed, so the suite still passes on a bare box.
Run on the GPU machine after `conda env create -f env/environment.yml`.
"""

import os
import tempfile

try:
    import trimesh  # noqa: F401
    from PIL import Image  # noqa: F401
    HAVE_EXTRAS = True
except Exception:
    HAVE_EXTRAS = False


def _skip():
    print("skip (reconstruct extra not installed: pillow/numpy/trimesh)")


def test_glb_to_preview_and_bounds():
    if not HAVE_EXTRAS:
        return _skip()
    import trimesh
    from assetpipe.util.mesh import to_preview_obj, bounds_dimensions_m

    with tempfile.TemporaryDirectory() as d:
        glb = os.path.join(d, "b.glb")
        trimesh.creation.box(extents=[0.4, 0.2, 0.3]).export(glb)
        obj = to_preview_obj(glb, os.path.join(d, "p.obj"))
        assert os.path.exists(obj)
        w, h, dep = bounds_dimensions_m(glb)
        assert round(w, 3) == 0.4 and round(h, 3) == 0.2 and round(dep, 3) == 0.3


def test_crop_object_matte_is_rgba():
    if not HAVE_EXTRAS:
        return _skip()
    from PIL import Image
    from assetpipe.util.image import crop_object

    with tempfile.TemporaryDirectory() as d:
        ip = os.path.join(d, "f.png")
        Image.new("RGB", (640, 480), (10, 20, 30)).save(ip)
        mp = os.path.join(d, "m.png")
        m = Image.new("L", (640, 480), 0)
        for y in range(100, 300):
            for x in range(150, 400):
                m.putpixel((x, y), 255)
        m.save(mp)
        out = crop_object(ip, (150, 100, 400, 300), os.path.join(d, "c.png"), mask_path=mp)
        c = Image.open(out)
        assert c.mode == "RGBA"


def test_glb_asset_gets_obj_preview_and_urdf():
    if not HAVE_EXTRAS:
        return _skip()
    import trimesh
    from assetpipe.pipeline import AssetPipeline, PipelineConfig
    from assetpipe.catalog import AssetCatalog
    from assetpipe.detect import HeuristicDetector
    from assetpipe.capture import FolderSource
    from assetpipe.reconstruct.base import Reconstructor
    from assetpipe.types import Detection, Frame, Reconstruction

    with tempfile.TemporaryDirectory() as d:
        raw = os.path.join(d, "raw.glb")
        trimesh.creation.box(extents=[0.35, 0.25, 0.15]).export(raw)

        class FakeGlb(Reconstructor):
            def reconstruct(self, frame, det):
                return Reconstruction("mug", raw, (0.35, 0.25, 0.15), "trellis")

        cfg = PipelineConfig(out_dir=os.path.join(d, "out"))
        cat = AssetCatalog(os.path.join(cfg.out_dir, "twin.db"))
        ap = AssetPipeline(FolderSource(d), HeuristicDetector(), FakeGlb(), cat, cfg)
        det = Detection("coffee mug", 0.9, (0, 0, 100, 100), "f0")
        asset = ap._digitalize(det, FakeGlb().reconstruct(Frame("f0", "(x)"), det))

        adir = os.path.dirname(asset.mesh_path)
        assert asset.mesh_path.endswith("model.glb")
        assert os.path.exists(os.path.join(adir, "model.obj"))       # viewer preview
        assert "model.glb" in open(asset.urdf_path).read()           # URDF -> glb
        cat.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("done")
