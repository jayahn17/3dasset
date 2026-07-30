"""Client for the generative image-to-3D step — the last mile to a real asset.

Prefer for clean single-object (product-scale) captures. Run
``assetpipe route`` first; multi-object / furniture / room → 3DGRUT.
Measure size from RGB-D ``dims.json``, never from the generated mesh scale.

    scan/video ─▶ isolated object ─▶ orbit views ─▶ [gen3d service] ─▶ asset.glb
                                     (this module picks the views and posts them)

The point cloud measures what the camera saw; the generator supplies clean
watertight topology and completes surfaces the capture never saw. Runs
against ``services/gen3d_server.py`` (TRELLIS or Hunyuan3D on the GPU box),
so this module stays dependency-light — just ``requests``.
"""

from __future__ import annotations

import os

DEFAULT_ENDPOINT = os.environ.get("GEN3D_ENDPOINT", "http://localhost:8080/generate")


def _sharpness(path: str) -> float:
    """Variance of the Laplacian — the classic blur score. Blurry views drag
    a generator toward mush, so they get dropped first."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return 1.0
    im = Image.open(path).convert("L").resize((256, 256))
    a = np.asarray(im, np.float64)
    lap = (-4 * a
           + np.roll(a, 1, 0) + np.roll(a, -1, 0)
           + np.roll(a, 1, 1) + np.roll(a, -1, 1))
    return float(lap.var())


def select_views(paths: list[str], k: int = 4) -> list[str]:
    """Pick ``k`` views: sharp, and spread around the object.

    More views is not better — generators take a handful, and a blurry or
    near-duplicate view costs more than it adds. Assumes ``paths`` are in
    orbit order (which ``render_orbit_views`` guarantees), so even spacing
    approximates angular diversity.
    """
    if k >= len(paths):
        return list(paths)
    if k <= 0:
        raise ValueError("k must be >= 1")
    stride = len(paths) / k
    chosen = []
    for i in range(k):
        lo = int(i * stride)
        hi = max(lo + 1, int((i + 1) * stride))
        window = paths[lo:hi]
        chosen.append(max(window, key=_sharpness))  # sharpest in each arc
    return chosen


def generate_asset(
    image_paths: list[str],
    out_glb: str,
    backend: str = "trellis",
    endpoint: str = DEFAULT_ENDPOINT,
    views: int = 4,
    seed: int = 1,
    texture_size: int = 1024,
    simplify: float = 0.95,
    timeout_s: float = 900.0,
) -> dict:
    """Post the best views to the gen3d service; write the returned GLB."""
    import requests

    if not image_paths:
        raise ValueError("no views to generate from")
    picked = select_views(image_paths, views)
    files = [("image", (os.path.basename(p), open(p, "rb"), "image/png"))
             for p in picked]
    data = {"backend": backend, "seed": str(seed),
            "texture_size": str(texture_size), "simplify": str(simplify)}
    try:
        r = requests.post(endpoint, files=files, data=data, timeout=timeout_s)
    finally:
        for _, (_, fh, _) in files:
            fh.close()
    if r.status_code != 200:
        detail = r.text[:300]
        raise RuntimeError(f"gen3d service {r.status_code}: {detail}")

    os.makedirs(os.path.dirname(out_glb) or ".", exist_ok=True)
    with open(out_glb, "wb") as fh:
        fh.write(r.content)
    return {"glb": out_glb, "backend": r.headers.get("X-Backend", backend),
            "views_used": len(picked), "bytes": len(r.content)}


def _sample_mesh_points(glb_path: str, n: int = 40_000):
    """Sample a GLB's surface into colored points, so the live page can draw
    the generated asset with the same point renderer it already uses."""
    import numpy as np
    import trimesh

    m = trimesh.load(glb_path)
    geom = (list(m.geometry.values())[0] if hasattr(m, "geometry") else m)
    pts, face_idx = trimesh.sample.sample_surface(geom, n)
    try:
        col = geom.visual.to_color().vertex_colors[geom.faces[face_idx]][:, 0, :3]
    except Exception:  # noqa: BLE001 — untextured mesh
        col = np.full((len(pts), 3), 200, np.uint8)
    return np.asarray(pts, np.float64), np.asarray(col, np.uint8)


# live sessions -> {"version", "xyz", "rgb", "generating", "error"}
_live_assets: dict[int, dict] = {}


def live_asset_preview(session, since: int = 0) -> dict:
    """Live-page hook: generate an asset from the session's cloud as it grows.

    Generation is a one-shot ~30 s pass, so this can't stream — it refreshes:
    whenever the cloud has grown enough and nothing is running, kick a
    background generation off the *current* cloud and serve the last result
    meanwhile.
    """
    import base64
    import struct
    import threading

    key = id(session)
    st = _live_assets.setdefault(
        key, {"version": 0, "xyz": None, "rgb": None,
              "generating": False, "error": None, "at_points": 0})

    _, cloud = session.cloud()
    enough = len(cloud.xyz) >= 800
    grown = len(cloud.xyz) >= 1.5 * st["at_points"]
    if enough and grown and not st["generating"]:
        st["generating"] = True
        st["at_points"] = len(cloud.xyz)

        def _work(xyz_snapshot, rgb_snapshot):
            import tempfile

            from .splat import render_orbit_views

            try:
                import numpy as np

                with tempfile.TemporaryDirectory() as d:
                    paths = render_orbit_views(
                        np.asarray(xyz_snapshot, np.float64),
                        np.asarray(rgb_snapshot, np.uint8),
                        os.path.join(d, "v"), n_views=6, elevations=(25.0,))
                    glb = os.path.join(d, "preview.glb")
                    generate_asset(paths, glb, views=4, timeout_s=600)
                    px, pc = _sample_mesh_points(glb)
                st.update(xyz=px, rgb=pc, version=st["version"] + 1, error=None)
            except Exception as e:  # noqa: BLE001 — preview is best-effort
                st["error"] = str(e)[:200]
            finally:
                st["generating"] = False

        threading.Thread(target=_work, daemon=True,
                         args=(list(cloud.xyz), list(cloud.rgb))).start()

    resp = {"ok": True, "available": True, "version": st["version"],
            "generating": st["generating"], "error": st["error"],
            "note": ("generating…" if st["generating"]
                     else (st["error"] or "refreshes as the scan grows"))}
    if st["version"] > since and st["xyz"] is not None:
        xyz, rgb = st["xyz"], st["rgb"]
        n = len(xyz)
        resp["pos"] = base64.b64encode(
            struct.pack(f"<{3 * n}f", *(float(c) for p in xyz for c in p))
        ).decode("ascii")
        resp["col"] = base64.b64encode(
            bytes(int(c) for p in rgb for c in p)).decode("ascii")
    return resp


def asset_from_scan(
    scan_ply: str,
    out_dir: str,
    backend: str = "trellis",
    endpoint: str = DEFAULT_ENDPOINT,
    views: int = 4,
    focus: float = 0.9,
    render_views: int = 8,
    seed: int = 1,
) -> dict:
    """The whole bridge in one call: scanner PLY -> isolated object -> EWA
    gaussian views (photo-like) -> generated GLB.

    The generator conditions on DINOv2 *photo* features; point-dot sprites
    produce featureless domes, real EWA splats produce real assets — so we
    always render through render_gaussian_views. Splat PLYs carry their true
    scale/rot/opacity; plain point clouds get soft synthetic footprints from
    load_gaussians, which still beats dots.
    """
    import numpy as np

    from .splat import (isolate_object, load_gaussians, render_gaussian_views,
                        render_orbit_views)

    g = load_gaussians(scan_ply)
    xyz_o, _rgb_o = isolate_object(g["xyz"], g["rgb"], focus=focus)
    if len(xyz_o) < 50:
        raise RuntimeError("isolation left almost nothing — try a larger --focus")
    # isolate_object filters copies; recover the row mask to filter the dict.
    void = np.ascontiguousarray(g["xyz"]).view(
        np.dtype((np.void, g["xyz"].dtype.itemsize * 3)))
    void_o = np.ascontiguousarray(xyz_o).view(
        np.dtype((np.void, xyz_o.dtype.itemsize * 3)))
    keep = np.isin(void.ravel(), void_o.ravel())
    g = {k: v[keep] for k, v in g.items()}

    view_dir = os.path.join(out_dir, "views")
    try:
        paths = render_gaussian_views(g, view_dir, n_views=render_views,
                                      elevations=(35.0, 55.0))
    except Exception:  # noqa: BLE001 — dots are the last resort, not the default
        paths = render_orbit_views(g["xyz"], g["rgb"], view_dir,
                                   n_views=render_views)
    res = generate_asset(paths, os.path.join(out_dir, f"asset_{backend}.glb"),
                         backend=backend, endpoint=endpoint, views=views, seed=seed)
    res.update(points=len(g["xyz"]), views_rendered=len(paths), view_dir=view_dir)
    return res
