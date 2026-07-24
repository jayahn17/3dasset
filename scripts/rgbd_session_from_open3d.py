#!/usr/bin/env python3
"""Convert Open3D / Redwood-style RGB-D + trajectory.log → assetpipe session/.

Used for open-source demos and as a template for iPad packages (already session/).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


def _parse_trajectory_log(path: str) -> list[list[float]]:
    """Open3D trajectory.log: blocks of 'i i i+1' then 4 rows of 4 floats (camera-to-world)."""
    poses: list[list[float]] = []
    lines = Path(path).read_text().strip().splitlines()
    i = 0
    while i < len(lines):
        header = lines[i].split()
        if len(header) == 3 and all(p.replace("-", "").isdigit() or p.isdigit() for p in header):
            i += 1
            rows = []
            for _ in range(4):
                rows.append([float(x) for x in lines[i].split()])
                i += 1
            # row-major 16 floats
            flat = [v for row in rows for v in row]
            poses.append(flat)
        else:
            i += 1
    return poses


def convert_redwood_style(
    color_dir: str,
    depth_dir: str,
    trajectory_log: str,
    out_session: str,
    fx: float = 525.0,
    fy: float = 525.0,
    cx: float = 319.5,
    cy: float = 239.5,
    depth_unit: str = "mm",
    object_hint: str = "opensource_rgbd_demo",
    max_frames: int = 0,
) -> dict:
    colors = sorted(
        p for p in Path(color_dir).iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    depths = sorted(
        p for p in Path(depth_dir).iterdir()
        if p.suffix.lower() in {".png", ".jpg"}
    )
    poses = _parse_trajectory_log(trajectory_log)
    n = min(len(colors), len(depths), len(poses))
    if max_frames > 0:
        n = min(n, max_frames)
    if n < 3:
        raise SystemExit(f"need ≥3 frames, got color={len(colors)} depth={len(depths)} poses={len(poses)}")

    root = Path(out_session)
    cdir = root / "color"
    ddir = root / "depth"
    if root.exists():
        shutil.rmtree(root)
    cdir.mkdir(parents=True)
    ddir.mkdir(parents=True)

    frames = []
    for i in range(n):
        c_name = f"{i:04d}{colors[i].suffix.lower()}"
        d_name = f"{i:04d}.png"
        shutil.copy2(colors[i], cdir / c_name)
        # depth may already be png
        if depths[i].suffix.lower() == ".png":
            shutil.copy2(depths[i], ddir / d_name)
        else:
            shutil.copy2(depths[i], ddir / d_name)
        frames.append({
            "id": f"f{i:05d}",
            "t": float(i) * 0.1,
            "color": f"color/{c_name}",
            "depth": f"depth/{d_name}",
            "pose": poses[i],
            "intrinsics": [fx, fy, cx, cy],
        })

    man = {
        "object_hint": object_hint,
        "location": "opensource_demo",
        "depth_unit": depth_unit,
        "source": "open3d_sample",
        "frames": frames,
    }
    (root / "manifest.json").write_text(json.dumps(man, indent=2))
    return {"session": str(root), "n_frames": n, "manifest": str(root / "manifest.json")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="redwood",
                    choices=["redwood", "lounge", "fountain", "path"],
                    help="built-in Open3D sample or --path")
    ap.add_argument("--path", help="custom extract dir with color/ depth/ trajectory.log")
    ap.add_argument("--out", default="captures/opensource_rgbd_session")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--depth-unit", default="mm", choices=["mm", "m"])
    args = ap.parse_args()

    import open3d as o3d

    if args.dataset == "path":
        base = Path(args.path)
        color_dir = base / "color"
        depth_dir = base / "depth"
        traj = base / "trajectory.log"
        # Redwood sample intrinsics
        fx = fy = 525.0
        cx, cy = 319.5, 239.5
        hint = "custom_rgbd"
    elif args.dataset == "redwood":
        ds = o3d.data.SampleRedwoodRGBDImages()
        color_dir = Path(ds.color_paths[0]).parent
        depth_dir = Path(ds.depth_paths[0]).parent
        traj = Path(ds.trajectory_log_path)
        fx = fy = 525.0
        cx, cy = 319.5, 239.5
        hint = "SampleRedwoodRGBDImages"
    elif args.dataset == "lounge":
        ds = o3d.data.LoungeRGBDImages()
        color_dir = Path(ds.color_paths[0]).parent
        depth_dir = Path(ds.depth_paths[0]).parent
        traj = Path(ds.trajectory_log_path)
        fx = fy = 525.0
        cx, cy = 319.5, 239.5
        hint = "LoungeRGBDImages"
        # Subsample evenly if many frames
        colors = sorted(
            p for p in color_dir.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
        )
        depths = sorted(
            p for p in depth_dir.iterdir()
            if p.suffix.lower() in {".png", ".jpg"}
        )
        poses = _parse_trajectory_log(str(traj))
        n_all = min(len(colors), len(depths), len(poses))
        max_f = args.max_frames if args.max_frames > 0 else min(80, n_all)
        idxs = [int(i * (n_all - 1) / max(max_f - 1, 1)) for i in range(max_f)]
        # write temp dirs with subsampled frames + traj
        tmp = Path(args.out).parent / f"_tmp_{hint}"
        if tmp.exists():
            shutil.rmtree(tmp)
        (tmp / "color").mkdir(parents=True)
        (tmp / "depth").mkdir(parents=True)
        traj_lines = []
        for j, idx in enumerate(idxs):
            shutil.copy2(colors[idx], tmp / "color" / f"{j:05d}{colors[idx].suffix.lower()}")
            shutil.copy2(depths[idx], tmp / "depth" / f"{j:05d}.png")
            traj_lines.append(f"{j} {j} {j+1}")
            pose = poses[idx]
            for r in range(4):
                traj_lines.append(" ".join(str(pose[r * 4 + c]) for c in range(4)))
        (tmp / "trajectory.log").write_text("\n".join(traj_lines) + "\n")
        info = convert_redwood_style(
            str(tmp / "color"), str(tmp / "depth"), str(tmp / "trajectory.log"),
            args.out, fx=fx, fy=fy, cx=cx, cy=cy,
            depth_unit=args.depth_unit, object_hint=hint, max_frames=0,
        )
        shutil.rmtree(tmp, ignore_errors=True)
        print(json.dumps(info, indent=2))
        return 0
    else:  # fountain
        ds = o3d.data.SampleFountainRGBDImages()
        color_dir = Path(ds.color_paths[0]).parent
        depth_dir = Path(ds.depth_paths[0]).parent
        traj = Path(ds.trajectory_log_path)
        fx = fy = 525.0
        cx, cy = 319.5, 239.5
        hint = "SampleFountainRGBDImages"

    info = convert_redwood_style(
        str(color_dir), str(depth_dir), str(traj), args.out,
        fx=fx, fy=fy, cx=cx, cy=cy,
        depth_unit=args.depth_unit,
        object_hint=hint,
        max_frames=args.max_frames,
    )
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
