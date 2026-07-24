"""The room sweep: one video in, a populated digital closet out.

    video ─▶ frames ─▶ discover (track every object) ─▶ N view stacks
                                                        │
                          for each ok track: ───────────┘
                          generate (TRELLIS) ─▶ asset.glb ─▶ catalog ─▶ viewer

Everything here is orchestration; the two hard parts already exist
(:mod:`assetpipe.scene.discover` finds the objects, :mod:`assetpipe.scene.generate`
turns views into a mesh). What this adds is the batch discipline a 50-object
sweep needs:

* **generation is serial.** The GPU box has 16 GB VRAM / 31 GB RAM and the
  generator loads a multi-GB model; two at once invites the host OOM-killer,
  which takes the *whole service* down with no traceback. One at a time.
* **one bad object must not sink the sweep.** 40 minutes of unattended work is
  too much to lose to a single 500, so a failed generation is recorded against
  its track and the batch carries on.
* **scale is honest.** A generated GLB is normalised — the mesh knows its
  shape, not its size. Dimensions are recorded as *relative* until a metric
  source (Quest pose, or a COLMAP scan with a reference) fills them in; they
  are never guessed.
"""

from __future__ import annotations

import json
import os
import time
import uuid

from .discover import discover_objects, resolve_backend
from .generate import DEFAULT_ENDPOINT, generate_asset


def _glb_extents(glb_path: str):
    """Bounding-box extents of the generated mesh, in the GLB's own units.

    NOT meters. TRELLIS returns a normalised asset, so this is shape, not
    size — it is stored so the closet can show relative proportions, and is
    flagged ``scale: relative`` so nothing downstream mistakes it for metric.
    """
    try:
        import trimesh

        m = trimesh.load(glb_path)
        ext = (m.extents if hasattr(m, "extents") else
               list(m.geometry.values())[0].extents)
        return tuple(float(x) for x in ext)
    except Exception:  # noqa: BLE001 — a mesh we can't measure still counts
        return (0.0, 0.0, 0.0)


def _discover_worker(kwargs: dict, q) -> None:
    """Child-process entry point for discovery. Must be importable (spawn)."""
    try:
        q.put(discover_objects(**kwargs))
    except Exception as e:  # noqa: BLE001 — surface it in the parent
        q.put(f"__error__{type(e).__name__}: {e}")


def _discover_isolated(kwargs: dict):
    """Run discovery in a child process, so its GPU memory is *really* gone.

    Freeing torch's cache in-process is not enough, and this cost a failed
    generation to learn: ``empty_cache()`` returns cached blocks to torch's
    allocator but **cannot destroy the CUDA context**, which pins hundreds of
    MB of VRAM for the life of the process. With the detector still holding
    that context, TRELLIS's first model load OOM'd (``cudaMalloc`` error 2)
    even though nothing was actively using the GPU.

    Only process exit returns a CUDA context to the OS. So discovery gets its
    own process and dies before the generator is ever asked for memory — which
    also means a segfault deep in the CUDA stack can't take the sweep with it.

    The child writes the cutouts to disk and returns only paths and scalars, so
    there is nothing heavy to pickle back.
    """
    import multiprocessing as mp

    ctx = mp.get_context("spawn")   # fork would inherit a CUDA context
    q = ctx.Queue()
    p = ctx.Process(target=_discover_worker, args=(kwargs, q))
    p.start()
    res = q.get()                   # drain before join, or a full pipe deadlocks
    p.join()
    if isinstance(res, str) and res.startswith("__error__"):
        raise RuntimeError(res[len("__error__"):])
    return res


def sweep_room(
    frames: list[str],
    out_dir: str,
    classes: list[str] | None = None,
    backend: str = "auto",
    masker: str = "sam2",
    gen_backend: str = "trellis",
    endpoint: str = DEFAULT_ENDPOINT,
    conf: float = 0.15,
    views: int = 8,
    gen_views: int = 4,
    min_views: int = 4,
    min_area_frac: float = 0.004,
    weights: str = "sam3.pt",
    location: str | None = None,
    generate: bool = True,
    limit: int = 0,
    seed: int = 1,
    on_progress=None,
) -> dict:
    """Discover every object in the sweep, then generate an asset for each."""
    from ..catalog import AssetCatalog, build_viewer
    from ..pipeline import _categorize
    from ..types import Asset

    os.makedirs(out_dir, exist_ok=True)
    cuts_dir = os.path.join(out_dir, "_objects")
    resolved = resolve_backend(backend, weights)

    kwargs = dict(
        frames=frames, out_dir=cuts_dir, classes=classes, backend=backend,
        masker=masker, conf=conf, views=views, min_views=min_views,
        min_area_frac=min_area_frac, weights=weights)
    # In-process when we aren't going to touch the generator anyway (a dry run
    # keeps the GPU to itself, and staying in-process keeps tracebacks direct).
    tracks = (_discover_isolated(kwargs) if generate
              else discover_objects(**kwargs))

    todo = [t for t in tracks if t.ok]
    if limit:
        todo = todo[:limit]

    catalog = AssetCatalog(os.path.join(out_dir, "twin.db"))
    made, failed = [], []

    if generate:
        for i, t in enumerate(todo, 1):
            if on_progress:
                on_progress(i, len(todo), t)
            asset_id = uuid.uuid4().hex[:12]
            asset_dir = os.path.join(out_dir, asset_id)
            os.makedirs(asset_dir, exist_ok=True)
            glb = os.path.join(asset_dir, "model.glb")
            try:
                res = generate_asset(t.views, glb, backend=gen_backend,
                                     endpoint=endpoint, views=gen_views,
                                     seed=seed)
            except Exception as e:  # noqa: BLE001 — never sink the batch
                t.status = "generate_failed"
                failed.append({"track_id": t.track_id, "label": t.label,
                               "error": str(e)[:200]})
                continue

            catalog.add(Asset(
                asset_id=asset_id,
                label=t.label,
                category=_categorize(t.label),
                mesh_path=glb,
                urdf_path=None,
                dimensions_m=_glb_extents(glb),
                created_at=time.time(),
                source="room-sweep",
                location=location,
                tags=[t.label],
                extra={"track_id": t.track_id, "n_frames": t.n_frames,
                       "views_used": res["views_used"],
                       "area_frac": round(t.area_frac, 5),
                       "scale": "relative",   # not metric until Phase 2
                       "gen_backend": res["backend"],
                       "view_dir": t.out_dir},
            ))
            made.append({"asset_id": asset_id, "track_id": t.track_id,
                         "label": t.label, "glb": glb,
                         "bytes": res["bytes"]})

    viewer = os.path.join(out_dir, "control_center.html")
    build_viewer(catalog, viewer)
    catalog.close()

    manifest = {
        "frames": len(frames),
        "discover_backend": resolved,
        "masker": masker if resolved == "track" else "(built into sam3)",
        "tracks": [
            {"track_id": t.track_id, "label": t.label, "status": t.status,
             "n_frames": t.n_frames, "area_frac": round(t.area_frac, 5),
             "sharpness": round(t.sharpness, 1), "views": len(t.views),
             "view_dir": t.out_dir}
            for t in tracks
        ],
        "assets": made,
        "failed": failed,
        "viewer": viewer,
    }
    with open(os.path.join(out_dir, "sweep.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest
