"""Orchestrate preprocess → backends → versioned refine."""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from ..preprocess.normalize import build_dataset_pack
from .backends import BACKENDS, run_backends
from .refine import refine_versions


def pipeline_run(
    src: str,
    out_dir: str,
    *,
    backends: Optional[list[str]] = None,
    versions: int = 2,
    target_frames: int = 48,
    video_fps: float = 2.5,
    trellis_endpoint: str | None = None,
    try_generative: bool = True,
    skip_3dgrut: bool = False,
    focus: float = 0.85,
) -> dict[str, Any]:
    """Full offline pipeline into ``out_dir``."""
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    print(f"→ preprocess {src}", flush=True)
    pack = build_dataset_pack(
        src, out_dir, target_frames=target_frames, video_fps=video_fps
    )
    print(
        f"✔ dataset pack  kind={pack.get('kind')} frames={pack.get('n_frames')} "
        f"depth={pack.get('has_depth')}",
        flush=True,
    )

    names = list(backends or BACKENDS)
    status = run_backends(
        out_dir,
        os.path.join(out_dir, "backends"),
        backends=names,
        trellis_endpoint=trellis_endpoint,
        skip_3dgrut=skip_3dgrut,
    )
    ok = [n for n, m in status.get("backends", {}).items() if m.get("ok")]
    if not ok:
        raise RuntimeError(
            "all backends failed — see backends/status.json\n"
            + json.dumps(status, indent=2)[:1200]
        )

    print(f"→ refine ×{versions} (ok backends: {ok})", flush=True)
    # Only try TRELLIS fill if trellis backend ran or service likely up
    gen = try_generative
    results = refine_versions(
        out_dir,
        n_versions=versions,
        focus=focus,
        trellis_endpoint=trellis_endpoint,
        try_generative=gen,
    )

    summary = {
        "out_dir": out_dir,
        "pack": {k: pack[k] for k in pack if not k.startswith("_")},
        "backends_ok": ok,
        "versions": [r.get("version") for r in results],
        "latest": results[-1] if results else None,
        "open": (results[-1] or {}).get("open_me") if results else None,
    }
    with open(os.path.join(out_dir, "pipeline_summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    return summary


def pipeline_refine(
    run_dir: str,
    *,
    versions: int = 1,
    trellis_endpoint: str | None = None,
    try_generative: bool = True,
    focus: float = 0.85,
) -> dict[str, Any]:
    """Continue refine on an existing run directory."""
    run_dir = os.path.abspath(run_dir)
    if not os.path.isdir(os.path.join(run_dir, "backends")):
        raise FileNotFoundError(f"{run_dir}/backends missing — run pipeline first")
    results = refine_versions(
        run_dir,
        n_versions=versions,
        focus=focus,
        trellis_endpoint=trellis_endpoint,
        try_generative=try_generative,
    )
    return {
        "out_dir": run_dir,
        "versions": [r.get("version") for r in results],
        "latest": results[-1] if results else None,
        "open": (results[-1] or {}).get("open_me") if results else None,
    }
