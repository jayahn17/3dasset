"""End-to-end test of the zero-dependency box-test slice.

Runs with plain `python tests/test_pipeline.py` or under pytest. No
third-party dependencies required.
"""

import os
import tempfile
import xml.dom.minidom as minidom

from assetpipe.catalog import AssetCatalog, build_viewer
from assetpipe.digitalize import mesh_io, urdf
from assetpipe.pipeline import AssetPipeline, PipelineConfig
from assetpipe.reconstruct import ProceduralBoxReconstructor
from assetpipe.detect import HeuristicDetector
from assetpipe.capture import FolderSource
from assetpipe.types import Detection, Frame


def test_box_obj_is_valid():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "b.obj")
        mesh_io.write_box_obj(p, (0.4, 0.3, 0.2))
        text = open(p).read()
        assert text.count("\nv ") + text.startswith("v ") >= 8  # 8 verts
        assert text.count("\nf ") == 6  # 6 quad faces
        # base sits at y=0, top at y=h
        ys = [float(l.split()[2]) for l in text.splitlines() if l.startswith("v ")]
        assert min(ys) == 0.0 and abs(max(ys) - 0.3) < 1e-9


def test_urdf_is_well_formed_and_has_mass():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.urdf")
        urdf.write_urdf(p, "shoe_box", "model.obj", (0.33, 0.13, 0.20))
        dom = minidom.parse(p)  # raises if malformed XML
        assert dom.getElementsByTagName("robot")[0].getAttribute("name") == "shoe_box"
        mass = float(dom.getElementsByTagName("mass")[0].getAttribute("value"))
        assert mass > 0


def test_pipeline_digitalizes_and_catalogs():
    with tempfile.TemporaryDirectory() as d:
        cfg = PipelineConfig(out_dir=d, source_name="test", location="lab")
        catalog = AssetCatalog(os.path.join(d, "twin.db"))
        recon = ProceduralBoxReconstructor(os.path.join(d, "_m"))
        ap = AssetPipeline(FolderSource(d), HeuristicDetector(), recon, catalog, cfg)

        frame = Frame(frame_id="f0", image_path="(synthetic)")
        det = Detection(label="cardboard box", score=1.0, bbox=(0, 0, 100, 100),
                        frame_id="f0", extra={"dimensions_m": (0.4, 0.3, 0.3)})
        asset = ap._digitalize(det, recon.reconstruct(frame, det))
        catalog.add(asset)

        assert catalog.count() == 1
        assert asset.category == "container"
        assert os.path.exists(asset.mesh_path)
        assert os.path.exists(asset.urdf_path)

        viewer = build_viewer(catalog, os.path.join(d, "v.html"))
        html = open(viewer).read()
        assert "cardboard box" in html and "http" not in html.split("DATA =")[1][:200]
        catalog.close()


def test_dedupe_labels_yields_one_asset_per_label():
    with tempfile.TemporaryDirectory() as d:
        # three frames of the "same" object -> heuristic emits 3 detections
        for i in range(3):
            open(os.path.join(d, f"f{i}.jpg"), "wb").write(b"\xff\xd8\xff\xdb")
        cfg = PipelineConfig(out_dir=os.path.join(d, "out"), dedupe_labels=True)
        catalog = AssetCatalog(os.path.join(cfg.out_dir, "twin.db"))
        pipe = AssetPipeline(
            FolderSource(d), HeuristicDetector(classes=["box"]),
            ProceduralBoxReconstructor(os.path.join(d, "_m")), catalog, cfg,
        )
        assets = pipe.run()
        assert len(assets) == 1  # 3 frames, 1 label -> 1 asset
        assert catalog.count() == 1
        catalog.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all tests passed")
