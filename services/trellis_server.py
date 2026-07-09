"""TRELLIS image-to-3D GPU service (run on the Ubuntu 22.04 + RTX 4080 box).

    POST /generate   (multipart: image=<png/jpg>)  ->  model/gltf-binary (.glb)
    GET  /health     -> {"ok": true, "device": "cuda", "vram_gb": ...}

This isolates the CUDA/torch/TRELLIS dependency stack from the pipeline
package. The pipeline's TrellisReconstructor just POSTs a crop here.

SETUP (see docs/GPU_SETUP.md for the full walkthrough)
    conda env create -f env/environment.yml && conda activate assetpipe-gpu
    git clone https://github.com/microsoft/TRELLIS && cd TRELLIS
    . ./setup.sh --new-env --basic --xformers --flash-attn --diffoctreerast \
        --spconv --mipgaussian --kaolin --nvdiffrast
    pip install fastapi uvicorn python-multipart
    TRELLIS_ROOT=/path/to/TRELLIS python services/trellis_server.py

VRAM: the 4080 has 16 GB. Use the image-large pipeline with
`low_vram=True`/CPU offload if you hit OOM; drop to a smaller variant if needed.
"""

from __future__ import annotations

import io
import os
import sys

# ---- lazy heavy imports so `python -c "import ast"`-style checks don't need CUDA
def _load_pipeline():
    trellis_root = os.environ.get("TRELLIS_ROOT")
    if trellis_root and trellis_root not in sys.path:
        sys.path.insert(0, trellis_root)
    os.environ.setdefault("ATTN_BACKEND", "flash-attn")
    os.environ.setdefault("SPCONV_ALGO", "native")

    import torch  # noqa: F401
    from trellis.pipelines import TrellisImageTo3DPipeline
    from trellis.utils import postprocessing_utils

    model = os.environ.get("TRELLIS_MODEL", "microsoft/TRELLIS-image-large")
    pipe = TrellisImageTo3DPipeline.from_pretrained(model)
    pipe.cuda()
    return pipe, postprocessing_utils


def build_app():
    from fastapi import FastAPI, UploadFile, File
    from fastapi.responses import Response, JSONResponse
    from PIL import Image
    import torch

    app = FastAPI(title="assetpipe TRELLIS service")
    state = {}

    @app.on_event("startup")
    def _startup():
        state["pipe"], state["pp"] = _load_pipeline()

    @app.get("/health")
    def health():
        vram = (
            torch.cuda.get_device_properties(0).total_memory / 1e9
            if torch.cuda.is_available()
            else 0
        )
        return JSONResponse(
            {"ok": True, "device": "cuda" if torch.cuda.is_available() else "cpu",
             "vram_gb": round(vram, 1)}
        )

    @app.post("/generate")
    async def generate(image: UploadFile = File(...)):
        raw = await image.read()
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        pipe, pp = state["pipe"], state["pp"]
        outputs = pipe.run(img, seed=1)  # dict with 'gaussian', 'mesh', ...
        glb = pp.to_glb(
            outputs["gaussian"][0], outputs["mesh"][0],
            simplify=0.95, texture_size=1024,
        )
        buf = io.BytesIO()
        glb.export(buf, file_type="glb")
        return Response(content=buf.getvalue(), media_type="model/gltf-binary")

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
