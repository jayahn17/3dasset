#!/usr/bin/env python3
"""Preview an RGB-D session: color|depth pairs, trajectory, HTML scrubber.

Usage:
  python tools/preview_rgbd_session.py captures/work/CrateScan-ID/.../session \\
      --out demo_out/CrateScan-ID/rgbd_preview
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from assetpipe.scene.rgbd_session import load_session  # noqa: E402


def depth_vis(path: str) -> Image.Image:
    d = np.array(Image.open(path)).astype(np.float32)
    valid = d > 0
    if not valid.any():
        return Image.new("RGB", (d.shape[1], d.shape[0]), (40, 0, 0))
    lo, hi = np.percentile(d[valid], [5, 95])
    t = np.clip((d - lo) / max(hi - lo, 1.0), 0, 1)
    r = np.clip(1.5 - np.abs(t - 0.75) * 2, 0, 1)
    g = np.clip(1.5 - np.abs(t - 0.5) * 2, 0, 1)
    b = np.clip(1.5 - np.abs(t - 0.25) * 2, 0, 1)
    rgb = np.stack([r, g, b], -1)
    rgb[~valid] = 0
    return Image.fromarray((rgb * 255).astype(np.uint8))


def side_by_side(color: Image.Image, depth: Image.Image, h: int = 360) -> Image.Image:
    c2 = color.resize((int(color.width * h / color.height), h))
    d2 = depth.resize((int(depth.width * h / depth.height), h))
    gap = Image.new("RGB", (10, h), (30, 30, 30))
    strip = Image.new("RGB", (c2.width + 10 + d2.width, h), (20, 20, 20))
    strip.paste(c2, (0, 0))
    strip.paste(gap, (c2.width, 0))
    strip.paste(d2, (c2.width + 10, 0))
    return strip


def write_html(out: Path, meta: dict) -> Path:
    samples = meta["samples"]
    # Prefer dense list of all frames if present
    frames = meta.get("all_frames") or samples
    html = f"""<!doctype html>
<html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>RGB-D preview — {meta.get('name','session')}</title>
<style>
:root{{--bg:#0e1117;--panel:#161b22;--line:#30363d;--txt:#e6edf3;--dim:#8b949e;--accent:#58a6ff}}
*{{box-sizing:border-box;margin:0}}
body{{background:var(--bg);color:var(--txt);font:14px/1.45 -apple-system,Segoe UI,sans-serif;
  padding:16px;max-width:1100px;margin:0 auto}}
h1{{font-size:18px;margin-bottom:6px}}
.dim{{color:var(--dim);margin-bottom:14px}}
img#view{{width:100%;border:1px solid var(--line);border-radius:8px;background:#000}}
input[type=range]{{width:100%;margin:12px 0}}
.row{{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px;flex:1;min-width:220px}}
code{{color:var(--accent)}}
a{{color:var(--accent)}}
</style></head><body>
<h1>RGB-D input preview</h1>
<div class=dim>{meta.get('session','')} · {meta.get('n_frames')} frames ·
{meta.get('duration_s',0):.1f}s · path {meta.get('path_len_m',0):.2f}m</div>
<img id=view alt="rgb|depth">
<input id=slider type=range min=0 max="{len(frames)-1}" value=0>
<div class=dim>frame <span id=fi>0</span> / {len(frames)-1} · left=RGB · right=depth (near=blue, far=warm)</div>
<div class=row>
  <div class=card><b>Camera bbox</b><br>{meta.get('cam_bbox_m')}</div>
  <div class=card><b>Trajectory</b><br><a href="trajectory.png">trajectory.png</a></div>
  <div class=card><b>Contact sheet</b><br><a href="contact_sheet.jpg">contact_sheet.jpg</a></div>
</div>
<script>
const FRAMES = {json.dumps([f['preview'] for f in frames])};
const view = document.getElementById('view');
const slider = document.getElementById('slider');
const fi = document.getElementById('fi');
function show(i){{view.src=FRAMES[i];fi.textContent=i;}}
slider.oninput=()=>show(+slider.value);
show(0);
document.addEventListener('keydown',e=>{{
  if(e.key==='ArrowRight'){{slider.value=Math.min(+slider.max,+slider.value+1);show(+slider.value)}}
  if(e.key==='ArrowLeft'){{slider.value=Math.max(0,+slider.value-1);show(+slider.value)}}
}});
</script></body></html>"""
    path = out / "index.html"
    path.write_text(html)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", help="session/ dir with manifest.json")
    ap.add_argument("--out", required=True, help="output preview folder")
    ap.add_argument("--every", type=int, default=1,
                    help="save every Nth frame for scrubber (1=all; large sessions use 2-5)")
    ap.add_argument("--max-frames", type=int, default=400,
                    help="cap scrubber frames (evenly subsample if over)")
    args = ap.parse_args()

    session = os.path.abspath(args.session)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    s = load_session(session)
    n = s.n_frames
    name = Path(session).parent.name

    ts = np.array([np.asarray(fr.pose, float).reshape(4, 4)[:3, 3] for fr in s.frames])
    path_len = float(np.sum(np.linalg.norm(np.diff(ts, axis=0), axis=1))) if n > 1 else 0.0
    t0 = float(s.frames[0].timestamp)
    t1 = float(s.frames[-1].timestamp)
    dur = t1 - t0
    if dur > 10_000:
        dur /= 1000.0

    # which indices for scrubber
    step = max(1, args.every)
    idxs = list(range(0, n, step))
    if len(idxs) > args.max_frames:
        sel = np.linspace(0, len(idxs) - 1, args.max_frames).astype(int)
        idxs = [idxs[i] for i in sel]

    all_frames = []
    for i in idxs:
        fr = s.frames[i]
        c = Image.open(fr.color_path).convert("RGB")
        dv = depth_vis(fr.depth_path)
        strip = side_by_side(c, dv, h=360)
        fname = f"frame_{i:04d}_rgb_depth.jpg"
        strip.save(out / fname, quality=85)
        d = np.array(Image.open(fr.depth_path))
        T = np.asarray(fr.pose, float).reshape(4, 4)
        all_frames.append({
            "i": i,
            "id": fr.frame_id,
            "preview": fname,
            "depth_mm_med": float(np.median(d[d > 0])) if (d > 0).any() else None,
            "depth_mm_max": int(d.max()),
            "valid_pct": float((d > 0).mean() * 100),
            "t": T[:3, 3].tolist(),
        })

    # contact sheet from evenly spaced samples
    sample_idxs = sorted(set(
        [0, n // 4, n // 2, 3 * n // 4, n - 1]
        + list(range(0, n, max(1, n // 11)))[:12]
    ))
    samples = []
    ims = []
    for i in sample_idxs:
        # reuse if already written
        fname = f"frame_{i:04d}_rgb_depth.jpg"
        if not (out / fname).is_file():
            fr = s.frames[i]
            strip = side_by_side(
                Image.open(fr.color_path).convert("RGB"),
                depth_vis(fr.depth_path),
                h=240,
            )
            strip.save(out / fname, quality=85)
        else:
            # also make smaller for sheet
            strip = Image.open(out / fname)
            h = 240
            strip = strip.resize((int(strip.width * h / strip.height), h))
        ims.append(Image.open(out / fname) if (out / fname).is_file() else strip)
        samples.append({"i": i, "preview": fname})

    # rebuild small contact
    ims = []
    for i in sample_idxs[:12]:
        fr = s.frames[i]
        ims.append(side_by_side(
            Image.open(fr.color_path).convert("RGB"),
            depth_vis(fr.depth_path),
            h=200,
        ))
    if ims:
        w, h = ims[0].size
        cols = 2
        rows_n = (len(ims) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * w, rows_n * (h + 8)), (20, 20, 20))
        for k, im in enumerate(ims):
            r, c = divmod(k, cols)
            sheet.paste(im, (c * w, r * (h + 8)))
        sheet.save(out / "contact_sheet.jpg", quality=85)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(1, 2, figsize=(10, 4))
        ax[0].plot(ts[:, 0], ts[:, 2], "-", lw=0.8)
        ax[0].scatter(ts[0, 0], ts[0, 2], c="g", s=40, label="start")
        ax[0].scatter(ts[-1, 0], ts[-1, 2], c="r", s=40, label="end")
        ax[0].set_aspect("equal")
        ax[0].set_xlabel("x (m)")
        ax[0].set_ylabel("z (m)")
        ax[0].set_title("camera path (top-down XZ)")
        ax[0].legend()
        meds = []
        for fr in s.frames[:: max(1, n // 200)]:
            d = np.array(Image.open(fr.depth_path))
            meds.append(float(np.median(d[d > 0])) if (d > 0).any() else 0.0)
        ax[1].plot(meds)
        ax[1].set_title("depth median (mm)")
        ax[1].set_xlabel("sample")
        ax[1].set_ylabel("mm")
        fig.tight_layout()
        fig.savefig(out / "trajectory.png", dpi=120)
        plt.close()
    except Exception as e:  # noqa: BLE001
        print(f"!! trajectory plot skipped: {e}")

    meta = {
        "name": name,
        "session": session,
        "n_frames": n,
        "duration_s": dur,
        "path_len_m": path_len,
        "cam_bbox_m": (ts.max(0) - ts.min(0)).tolist(),
        "samples": samples,
        "all_frames": all_frames,
    }
    (out / "preview_meta.json").write_text(json.dumps(meta, indent=2))
    html = write_html(out, meta)
    print(f"✔ RGB-D preview → {out}")
    print(f"  open  {html}")
    print(f"  frames={n} duration={dur:.1f}s path={path_len:.2f}m scrubber={len(all_frames)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
