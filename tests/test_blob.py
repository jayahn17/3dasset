"""Tests for blob upload + the model_3d_ref rewrite in the son exporter.

LocalCopyUploader + the payload rewrite are pure stdlib. The presigned-PUT
test uses a tiny local HTTP sink and needs `requests` (skips without it).
"""

import http.server
import json
import os
import tempfile
import threading

from assetpipe.integrations.blob import LocalCopyUploader, PresignedPutUploader
from assetpipe.integrations.son_twin import build_quest_scan_payload


def _row(mesh_path, **kw):
    base = dict(
        asset_id="abc123", label="room scan", category="container",
        mesh_path=mesh_path, urdf_path=None, dim_w=1.0, dim_h=1.0, dim_d=1.0,
        source="quest3", location="bedroom", world_pose=None, thumbnail_path=None,
        tags=json.dumps(["scan"]),
        extra=json.dumps({"detection_score": 0.9, "recon_method": "nerfstudio"}),
    )
    base.update(kw)
    return base


def test_local_copy_uploader():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "scan.ply")
        open(src, "wb").write(b"ply\n" + b"x" * 1000)
        up = LocalCopyUploader(os.path.join(d, "served"), "http://host:8000/blobs")
        url = up.upload(src)
        assert url == "http://host:8000/blobs/scan.ply"
        assert os.path.exists(os.path.join(d, "served", "scan.ply"))


def test_payload_rewrites_model_3d_ref_to_blob_url():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "model.glb")
        open(src, "wb").write(b"glTF" + b"\0" * 500)
        up = LocalCopyUploader(os.path.join(d, "served"), "https://cdn.example/blobs")
        payload = build_quest_scan_payload([_row(src)], uploader=up)
        det = payload["detected_assets"][0]
        # namespaced by asset_id so same-named meshes don't collide
        assert det["model_3d_ref"] == "https://cdn.example/blobs/abc123/model.glb"
        assert os.path.exists(os.path.join(d, "served", "abc123", "model.glb"))
        # raw file is NOT in the payload — only the URL
        assert "glTF" not in json.dumps(payload)


def test_missing_mesh_file_falls_back_to_local_path():
    # a synthetic asset with no real file on disk should not crash the upload
    up = LocalCopyUploader(tempfile.mkdtemp(), "http://h/b")
    payload = build_quest_scan_payload([_row("(synthetic)")], uploader=up)
    assert payload["detected_assets"][0]["model_3d_ref"] == "(synthetic)"


def test_presigned_put_uploader_roundtrip():
    try:
        import requests  # noqa: F401
    except Exception:
        print("skip (requests not installed)")
        return

    received = {}

    class Sink(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            n = int(self.headers.get("Content-Length", 0))
            received["body"] = self.rfile.read(n)
            received["ct"] = self.headers.get("Content-Type")
            self.send_response(200); self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Sink)
    port = srv.server_address[1]
    threading.Thread(target=srv.handle_request, daemon=True).start()

    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "cloud.ply")
        open(src, "wb").write(b"POINTS" * 100)
        up = PresignedPutUploader(
            lambda name: (f"http://127.0.0.1:{port}/{name}?sig=xyz",
                          f"https://cdn.example/{name}")
        )
        url = up.upload(src)
    srv.server_close()

    assert url == "https://cdn.example/cloud.ply"       # public URL, not the presigned one
    assert received["body"] == b"POINTS" * 100          # bytes actually arrived
    assert received["ct"] == "application/octet-stream"  # PLY content-type


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all blob tests passed")
