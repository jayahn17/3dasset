"""Standalone bridge that runs in the *nerfstudio* env, not the assetpipe env.

The assetpipe CLI runs in the `assetpipe` conda env (torch cu121, pycolmap).
nerfstudio + gsplat live in a separate env (`roomrecon`) on purpose — its
cu124 torch/gsplat CUDA stack must not perturb the hard-won assetpipe install
(same doctrine as the gen3d split). So the splatfacto backend shells the
nerfstudio-only work out to this script via that env's python.

This file imports **only** nerfstudio + stdlib — never `assetpipe` — because
the target env does not have assetpipe installed. Keep it that way.

    python _splat_driver.py colmap2json <colmap_sparse_dir> <output_dir>

Converts a COLMAP sparse model (cameras/images/points3D) into nerfstudio's
`transforms.json` (+ `sparse_pc.ply` for splat initialisation). This is the
one step that needs nerfstudio's battle-tested pose-convention handling; the
actual `ns-train` / `ns-export` are plain CLI calls the backend makes itself.
"""

import sys
from pathlib import Path


def colmap2json(sparse_dir: str, output_dir: str) -> int:
    from nerfstudio.process_data.colmap_utils import colmap_to_json

    recon = Path(sparse_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    # colmap_to_json writes <out>/transforms.json and, from points3D, an init
    # point cloud <out>/sparse_pc.ply that splatfacto seeds gaussians from.
    n = colmap_to_json(recon_dir=recon, output_dir=out,
                       ply_filename="sparse_pc.ply", use_single_camera_mode=True)
    print(f"transforms.json written for {n} frames -> {out}")
    return 0


def main(argv) -> int:
    if len(argv) >= 4 and argv[1] == "colmap2json":
        return colmap2json(argv[2], argv[3])
    print("usage: _splat_driver.py colmap2json <sparse_dir> <output_dir>",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
