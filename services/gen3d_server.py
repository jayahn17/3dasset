"""Generative image-to-3D GPU service — TRELLIS and Hunyuan3D behind one API.

    POST /generate   multipart: image=<png> (repeatable, 1..N views)
                     form: backend=trellis|hunyuan3d, seed, texture_size, simplify
                     -> model/gltf-binary (.glb)
    GET  /health     -> which backends loaded, VRAM

This is the step that closes the gap a scan can't: the point cloud measures
what the camera saw; the generator supplies clean watertight topology and
completes the surfaces it never saw (undersides, occlusions).

MULTI-VIEW MATTERS. Given several views the model reproduces *your* object;
given one it invents a plausible lookalike. Feed it the orbit renders from
`assetpipe views` (background already removed — these models expect a matted
object, and ours arrive that way).

RUN (see env/setup_gen3d.sh — models live in their own `gen3d` env)
    conda activate gen3d
    GEN3D_BACKENDS=trellis,hunyuan3d python services/gen3d_server.py

MEMORY (16 GB VRAM / 31 GB RAM): models load LAZILY, one at a time, and the
other is evicted first — neither GPU nor host memory holds both. Safest of
all is one process per backend (GEN3D_BACKENDS=hunyuan3d on its own port):
a host-RAM OOM kills the process with no traceback, which looks like a
network error on the client.

ENV
    PORT (8080)            GEN3D_BACKENDS (trellis,hunyuan3d)
    TRELLIS_ROOT           path to the TRELLIS checkout
    TRELLIS_MODEL          microsoft/TRELLIS-image-large
    HUNYUAN_MODEL          tencent/Hunyuan3D-2   (…-2mini if VRAM is tight)
    GEN3D_TEXTURE          1 to bake textures with Hunyuan's paint pipeline
"""

# NB: deliberately NO `from __future__ import annotations` — it turns the route
# annotations into strings and pydantic then can't resolve `List[UploadFile]`
# (PydanticUserError: not fully defined).

import io
import os
import sys
import threading
from typing import List

PORT = int(os.environ.get("PORT", "8080"))
BACKENDS = [b.strip() for b in
            os.environ.get("GEN3D_BACKENDS", "trellis,hunyuan3d").split(",") if b.strip()]
TRELLIS_MODEL = os.environ.get("TRELLIS_MODEL", "microsoft/TRELLIS-image-large")
HUNYUAN_MODEL = os.environ.get("HUNYUAN_MODEL", "tencent/Hunyuan3D-2")
WANT_TEXTURE = os.environ.get("GEN3D_TEXTURE", "") in ("1", "true", "yes")

_lock = threading.Lock()
_loaded: dict[str, object] = {}   # backend -> pipeline (at most one at a time)


def _evict(keep: str) -> None:
    """Drop the other generator before loading this one.

    Neither 16 GB of VRAM nor 31 GB of host RAM holds both: loading Hunyuan3D
    while TRELLIS is still resident gets the *system* OOM-killer, which kills
    the process outright (no python traceback — the request just dies with a
    connection reset). So free host memory too, not only the CUDA cache.
    """
    import gc

    import torch

    for name in list(_loaded):
        if name != keep:
            pipe = _loaded.pop(name)
            for attr in ("models", "model", "shape", "paint", "pipeline"):
                if hasattr(pipe, attr):
                    setattr(pipe, attr, None)
                elif isinstance(pipe, dict):
                    pipe[attr] = None
            del pipe
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


_rembg_session = None


def _matte(img):
    """Cut the object out of a real photo.

    These models want a matted object on empty background — that IS the
    "remove the environment" step, and a purpose-built matting net does it
    far better than any geometric heuristic on a point cloud.

    Images that already carry a real alpha channel (our splat renders) pass
    through untouched; opaque photos/JPEGs get matted.
    """
    global _rembg_session

    import numpy as np

    if img.mode == "RGBA":
        alpha = np.asarray(img.getchannel("A"))
        if alpha.min() < 250:      # a genuine cutout already
            return img

    from rembg import new_session, remove

    if _rembg_session is None:
        _rembg_session = new_session("u2net")
    return remove(img.convert("RGB"), session=_rembg_session).convert("RGBA")


def _xformers_compat():
    """TRELLIS calls ``xformers.ops.fmha.BlockDiagonalMask``; xformers >=0.0.27
    moved it to ``fmha.attn_bias``. Alias it back rather than patching the
    upstream checkout (which would be lost on every `git pull`)."""
    import xformers.ops.fmha as fmha
    import xformers.ops.fmha.attn_bias as attn_bias

    for name in ("BlockDiagonalMask", "BlockDiagonalCausalMask",
                 "LowerTriangularMask"):
        if not hasattr(fmha, name) and hasattr(attn_bias, name):
            setattr(fmha, name, getattr(attn_bias, name))


def _load_trellis():
    root = os.environ.get("TRELLIS_ROOT", os.path.expanduser("~/gen3d/TRELLIS"))
    if root not in sys.path:
        sys.path.insert(0, root)
    # xformers: flash-attn isn't built here, and spconv 'native' avoids a
    # benchmark pass that can OOM on a 16 GB card
    os.environ.setdefault("ATTN_BACKEND", "xformers")
    os.environ.setdefault("SPCONV_ALGO", "native")
    _xformers_compat()

    from trellis.pipelines import TrellisImageTo3DPipeline

    pipe = TrellisImageTo3DPipeline.from_pretrained(TRELLIS_MODEL)
    pipe.cuda()
    return pipe


def _get(backend: str):
    """Cached load — TRELLIS only. Hunyuan is loaded per-call (see below)."""
    with _lock:
        if backend not in _loaded:
            _evict(keep=backend)
            _loaded[backend] = _load_trellis()
        return _loaded[backend]


def _run_trellis(images, seed: int, simplify: float, texture_size: int):
    pipe = _get("trellis")
    if len(images) == 1:
        out = pipe.run(images[0], seed=seed)
    else:  # several views -> reconstructs YOUR object, not a lookalike
        out = pipe.run_multi_image(images, seed=seed)
    from trellis.utils import postprocessing_utils

    return postprocessing_utils.to_glb(
        out["gaussian"][0], out["mesh"][0],
        simplify=simplify, texture_size=texture_size,
    )


def _run_hunyuan(images, seed: int, simplify: float, texture_size: int):
    """Shape, then texture — loaded and freed one at a time.

    Hunyuan's shape and paint pipelines are ~10 GB each. Holding both (plus a
    desktop) exceeds 31 GB of host RAM and the OOM-killer takes the process
    down with no traceback. So: generate the mesh, drop the shape model,
    only then bring up the painter.
    """
    import gc

    import torch
    from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline

    _evict(keep="hunyuan3d")          # TRELLIS must not be resident
    shape = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(HUNYUAN_MODEL)
    gen = torch.Generator(device="cuda").manual_seed(seed)
    mesh = shape(image=images[0], generator=gen)[0]
    del shape
    gc.collect()
    torch.cuda.empty_cache()

    if WANT_TEXTURE:
        try:
            from hy3dgen.texgen import Hunyuan3DPaintPipeline

            paint = Hunyuan3DPaintPipeline.from_pretrained(HUNYUAN_MODEL)
            mesh = paint(mesh, image=images[0])
            del paint
            gc.collect()
            torch.cuda.empty_cache()
        except Exception as e:  # noqa: BLE001 — shape is still a usable asset
            print(f"!! hunyuan texturing failed ({type(e).__name__}: {e}); "
                  "returning untextured shape")
    return mesh


def build_app():
    import torch
    import trimesh  # noqa: F401  (backends return trimesh objects)
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import JSONResponse, Response
    from PIL import Image

    app = FastAPI(title="assetpipe gen3d service")

    @app.get("/health")
    def health():
        vram = (torch.cuda.get_device_properties(0).total_memory / 1e9
                if torch.cuda.is_available() else 0)
        free = (torch.cuda.mem_get_info()[0] / 1e9
                if torch.cuda.is_available() else 0)
        return JSONResponse({
            "ok": True,
            "device": "cuda" if torch.cuda.is_available() else "cpu",
            "vram_gb": round(vram, 1), "vram_free_gb": round(free, 1),
            "backends": BACKENDS, "loaded": list(_loaded),
            "texture": WANT_TEXTURE,
        })

    @app.post("/generate")
    async def generate(
        image: List[UploadFile] = File(...),
        backend: str = Form("trellis"),
        seed: int = Form(1),
        simplify: float = Form(0.95),
        texture_size: int = Form(1024),
        matte: bool = Form(True),   # cut the object out of real photos
    ):
        if backend not in BACKENDS:
            raise HTTPException(400, f"backend {backend!r} not enabled "
                                     f"(GEN3D_BACKENDS={BACKENDS})")
        images = []
        for up in image:
            raw = await up.read()
            img = Image.open(io.BytesIO(raw)).convert("RGBA")
            images.append(_matte(img) if matte else img)
        if not images:
            raise HTTPException(422, "no images")
        import traceback

        try:
            run = _run_trellis if backend == "trellis" else _run_hunyuan
            mesh = run(images, seed, simplify, texture_size)
            buf = io.BytesIO()
            mesh.export(buf, file_type="glb")
        except torch.cuda.OutOfMemoryError as e:
            traceback.print_exc()
            raise HTTPException(
                507, "GPU out of memory — try fewer views, a smaller "
                     "texture_size, or HUNYUAN_MODEL=tencent/Hunyuan3D-2mini"
            ) from e
        except Exception as e:  # noqa: BLE001 — surface the real cause
            traceback.print_exc()  # the detail alone hides where it broke
            raise HTTPException(500, f"{backend} failed: {type(e).__name__}: {e}"
                                ) from e
        finally:
            # free the run's working set — without this a second request OOMs
            # on a 16 GB card even though the model itself still fits
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        return Response(content=buf.getvalue(), media_type="model/gltf-binary",
                        headers={"X-Backend": backend,
                                 "X-Views": str(len(images))})

    return app


if __name__ == "__main__":
    import uvicorn

    print(f"gen3d service on :{PORT}  backends={BACKENDS}  texture={WANT_TEXTURE}")
    uvicorn.run(build_app(), host="0.0.0.0", port=PORT)
