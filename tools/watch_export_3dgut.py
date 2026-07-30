#!/usr/bin/env python3
"""Wait for a 3DGRUT run to produce export_last.ply, then build review artifacts.

    python tools/watch_export_3dgut.py demo_out/CrateScan-DC22F084_3dgut_fullres \\
        --train-pid 1518023
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
from splat_viewer_html import write_splat_viewer  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir", type=Path, help="3dgut output folder")
    ap.add_argument("--train-pid", type=int, default=0,
                    help="optional train.py pid; exit early if it dies without ply")
    ap.add_argument("--timeout-h", type=float, default=3.5)
    ap.add_argument("--title", default="3DGRUT splat")
    args = ap.parse_args()

    out: Path = args.out_dir.resolve()
    deadline = time.time() + args.timeout_h * 3600
    ply: Path | None = None
    print(f"watching for export_last.ply under {out}", flush=True)

    while time.time() < deadline:
        cands = sorted(out.glob("**/export_last.ply"), key=lambda p: p.stat().st_mtime)
        if cands:
            ply = cands[-1]
            print(f"found {ply}", flush=True)
            break
        ckpts = sorted(out.glob("**/ckpt_*.pt"), key=lambda p: p.stat().st_mtime)
        if ckpts:
            print(f"ckpt seen {ckpts[-1].name} ({ckpts[-1].stat().st_size} B)",
                  flush=True)
        if args.train_pid and not os.path.exists(f"/proc/{args.train_pid}"):
            # Dataloader workers share the train.py cmdline; only treat as
            # dead if *no* matching train.py remains.
            still = False
            try:
                import subprocess as _sp
                still = bool(_sp.check_output(
                    ["pgrep", "-f", "train.py.*" + out.name],
                    text=True,
                ).strip())
            except Exception:
                still = False
            if still:
                print(f"pid {args.train_pid} gone but train.py still running; "
                      "continue watching", flush=True)
            else:
                cands = sorted(out.glob("**/*export*.ply"),
                               key=lambda p: p.stat().st_mtime)
                print("train exited; export candidates",
                      [str(p) for p in cands[-5:]], flush=True)
                if cands:
                    ply = cands[-1]
                    break
                print("FAILED: train exited with no export ply", flush=True)
                return 1
        time.sleep(30)

    if ply is None:
        print("FAILED: timeout waiting for ply", flush=True)
        return 2

    dst = out / "scene_gaussians.ply"
    shutil.copy2(ply, dst)
    print(f"copied {ply} -> {dst}", flush=True)

    splat = out / "scene_gaussians.splat"
    root = Path(__file__).resolve().parents[1]
    py = sys.executable
    # System python often lacks plyfile; prefer the 3dgrut conda env.
    cand = Path.home() / "miniconda3/envs/3dgrut/bin/python"
    if cand.is_file():
        py = str(cand)
    subprocess.check_call(
        [py, str(root / "tools/ply_to_splat.py"),
         str(dst), "--out", str(splat)],
        cwd=str(root),
    )
    print(f"splat {splat} ({splat.stat().st_size} B)", flush=True)

    html = write_splat_viewer(out, "scene_gaussians.splat", args.title)
    print("DONE", html, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
