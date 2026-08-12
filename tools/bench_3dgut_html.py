#!/usr/bin/env python3
"""Benchmark report for trained splats — one self-contained HTML file.

Answers the only question that matters after a training run finishes: *is it
any good?* Training "completing" says nothing; the log says 🥳 either way.

For every `demo_out/*_3dgut*/` run this collects:

  * **PSNR vs ground truth** at the real capture poses, from the contact sheet
    `tools/render_splat_views.py` writes. This is the honest signal — the
    `preview/*.png` scatter plots in each run directory are mean-position
    plots and look identical for a good and a diverged splat.
  * **Divergence rate** — the fraction of gaussians that trained to inf/1e30
    and got dropped on export. A healthy run loses a few percent.
  * **Input frame count**, which is what usually explains the other two.

Images are downscaled and base64-embedded, so the report is a single file you
can open with file:// or hand to someone else. No server, no asset paths.

    python tools/bench_3dgut_html.py                     # → demo_out/BENCHMARK.html
    python tools/bench_3dgut_html.py --open              # and open a browser

Runs with no `render_check/` are listed as un-rendered rather than skipped —
a missing measurement is a finding, not an absence.
"""

from __future__ import annotations

import argparse
import base64
import glob
import io
import json
import os
import sys
import webbrowser
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# PSNR bands, calibrated on this repo's runs: the couch (80 frames) averages
# 23 dB and is usable; the 18-frame laptop averages 14.5 dB and is fog.
GOOD, FAIR = 22.0, 18.0
# A healthy export loses a few percent to divergence; 18% means the fit blew up.
DIVERGED_WARN = 10.0


def psnr_halves(path: str) -> float | None:
    """PSNR between the two halves of a `gt | render` side-by-side image."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return None
    try:
        im = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64) / 255.0
    except Exception:
        return None
    w = im.shape[1] // 2
    mse = ((im[:, :w] - im[:, w:2 * w]) ** 2).mean()
    return float(10 * np.log10(1.0 / max(mse, 1e-12)))


def embed(path: str, max_w: int = 1100, quality: int = 78) -> str | None:
    """Downscale to a data: URI. Contact sheets are 8000 px wide; nobody needs that."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return None
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def session_frames(session: str | None) -> int | None:
    if not session or not os.path.isdir(session):
        return None
    sys.path.insert(0, REPO)
    try:
        from assetpipe.scene.rgbd_session import load_session
        return int(load_session(session).n_frames)
    except Exception:
        return None


def collect_splat(run_dir: str) -> dict:
    name = os.path.basename(run_dir)
    rec: dict = {"name": name, "dir": run_dir, "kind": "3dgut"}

    meta_path = os.path.join(run_dir, "scene_gaussians.splat.meta.json")
    if os.path.isfile(meta_path):
        m = json.load(open(meta_path))
        rec["n_in"] = m.get("n_in")
        rec["n_out"] = m.get("n_out")
        rec["radius_m"] = m.get("radius_m")
        if m.get("n_in"):
            rec["diverged_pct"] = 100.0 * m.get("dropped_invalid", 0) / m["n_in"]

    q_path = os.path.join(run_dir, "3dgut_queue.json")
    if os.path.isfile(q_path):
        q = json.load(open(q_path))
        rec["iterations"] = q.get("iterations")
        rec["session"] = q.get("session")
        if q.get("finished"):
            rec["finished"] = datetime.fromtimestamp(
                q["finished"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rec["frames"] = session_frames(rec.get("session"))

    route = os.path.join(run_dir, "route_decision.json")
    if os.path.isfile(route):
        r = json.load(open(route))
        edge = r.get("signals", {}).get("longest_edge_inches")
        rec["longest_edge_in"] = edge

    views = sorted(glob.glob(os.path.join(run_dir, "render_check",
                                          "view_*_gt_vs_render.png")))
    scores = [p for p in (psnr_halves(v) for v in views) if p is not None]
    if scores:
        rec["psnr"] = scores
        rec["psnr_mean"] = sum(scores) / len(scores)
    sheet = os.path.join(run_dir, "render_check", "contact_sheet.png")
    rec["image"] = embed(sheet) if os.path.isfile(sheet) else None
    return rec


def collect_mesh(mesh_dir: str, renders: list[str]) -> dict:
    rec = {"name": os.path.relpath(mesh_dir, os.path.join(REPO, "demo_out")),
           "dir": mesh_dir, "kind": "trellis"}
    glb = os.path.join(mesh_dir, "asset_trellis.glb")
    if os.path.isfile(glb):
        rec["glb_mb"] = os.path.getsize(glb) / 1e6

    # `dims_mm.json` is the only positive evidence of metric scale — the metric
    # route writes it. The absence of a NO_METRIC_SCALE.txt marker proves
    # nothing (the RGB-only v2 export simply never wrote one), so a dir with
    # neither is *unknown*, not metric. A generator mesh is normalized by
    # default; claiming scale it doesn't have is the expensive mistake here.
    dims_path = os.path.join(mesh_dir, "dims_mm.json")
    if os.path.isfile(dims_path):
        rec["scale"] = "metric"
        try:
            mm = json.load(open(dims_path)).get("mm", {})
            if mm:
                rec["dims_mm"] = " × ".join(f"{v:.0f}" for v in mm.values())
        except Exception:
            pass
    elif os.path.isfile(os.path.join(mesh_dir, "NO_METRIC_SCALE.txt")):
        rec["scale"] = "none"
    else:
        rec["scale"] = "unknown"

    rec["images"] = [i for i in (embed(r, max_w=520) for r in renders) if i]
    return rec


SCALE_UI = {
    "metric": ("good", "METRIC", "metric — dims_mm.json present"),
    "none": ("bad", "NO SCALE", "normalized, NOT metric — "
                                "metricize with tools/scale_from_marker.py"),
    "unknown": ("warn", "UNKNOWN", "no dims_mm.json — assume normalized "
                                   "until proven otherwise"),
}


def verdict(rec: dict) -> tuple[str, str]:
    """(css class, sentence) — say what to *do*, not just what happened."""
    p, d, f = rec.get("psnr_mean"), rec.get("diverged_pct"), rec.get("frames")
    if p is None:
        return "warn", ("Not rendered — run tools/render_splat_views.py before "
                        "trusting this run.")
    if p >= GOOD:
        s = "good"
        msg = f"Usable. {p:.1f} dB against ground truth at real capture poses."
    elif p >= FAIR:
        s = "warn"
        msg = f"Marginal at {p:.1f} dB — fine for shape, soft for appearance."
    else:
        s = "bad"
        msg = f"Failed. {p:.1f} dB — the render is fog, not the object."
    if d is not None and d >= DIVERGED_WARN:
        msg += f" {d:.0f}% of gaussians diverged to inf, {DIVERGED_WARN:.0f}%+ means the fit blew up."
    if f is not None and f < 30 and p < GOOD:
        msg += f" Only {f} input frames — re-capture with a slower orbit before retraining."
    return s, msg


CSS = """
*{box-sizing:border-box}
body{margin:0;background:#0b0d10;color:#e6edf3;
  font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{padding:34px 30px 22px;border-bottom:1px solid #21262d;
  background:linear-gradient(180deg,#141920,#0b0d10)}
h1{margin:0 0 6px;font-size:25px;letter-spacing:-.02em}
h2{font-size:18px;margin:38px 0 14px;letter-spacing:-.01em}
.sub{color:#8b949e;max-width:78ch}
main{padding:0 30px 70px;max-width:1240px;margin:0 auto}
table{border-collapse:collapse;width:100%;margin:18px 0;font-size:13px}
th,td{padding:9px 11px;text-align:left;border-bottom:1px solid #21262d}
th{color:#8b949e;font-weight:600;text-transform:uppercase;font-size:11px;letter-spacing:.05em}
tbody tr:hover{background:#11161d}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.card{border:1px solid #21262d;border-radius:11px;margin:20px 0;overflow:hidden;background:#0e1218}
.card h3{margin:0;padding:15px 18px;font-size:15px;border-bottom:1px solid #21262d;
  display:flex;justify-content:space-between;align-items:center;gap:14px}
.card .body{padding:16px 18px}
.card img{width:100%;border-radius:7px;display:block;border:1px solid #21262d}
.pill{font-size:11px;padding:3px 10px;border-radius:999px;font-weight:700;
  letter-spacing:.04em;white-space:nowrap}
.good{background:#12331d;color:#4ade80;border:1px solid #1e5631}
.warn{background:#33290f;color:#fbbf24;border:1px solid #614a12}
.bad{background:#3a1417;color:#f87171;border:1px solid #6b1f24}
.msg{margin:0 0 14px;color:#c9d1d9}
.meta{color:#8b949e;font-size:12px;margin:0 0 14px;
  display:flex;flex-wrap:wrap;gap:6px 18px}
.meta b{color:#e6edf3;font-weight:600}
.caption{color:#8b949e;font-size:12px;margin:9px 0 0}
.shots{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px}
.note{border-left:3px solid #30363d;padding:10px 0 10px 15px;color:#8b949e;margin:16px 0}
code{background:#161b22;padding:1px 6px;border-radius:5px;font-size:12px}
"""


def render_html(splats: list[dict], meshes: list[dict], generated: str) -> str:
    rows = []
    for r in sorted(splats, key=lambda x: -(x.get("psnr_mean") or -1)):
        cls, _ = verdict(r)
        fmt = lambda v, s: (s.format(v) if v is not None else "—")  # noqa: E731
        rows.append(f"""<tr>
      <td>{r['name']}</td>
      <td class=num>{fmt(r.get('frames'), '{}')}</td>
      <td class=num>{fmt(r.get('iterations'), '{:,}')}</td>
      <td class=num>{fmt(r.get('psnr_mean'), '{:.1f}')}</td>
      <td class=num>{fmt(r.get('diverged_pct'), '{:.1f}%')}</td>
      <td class=num>{fmt(r.get('n_out'), '{:,}')}</td>
      <td><span class="pill {cls}">{cls.upper()}</span></td>
    </tr>""")

    cards = []
    for r in sorted(splats, key=lambda x: -(x.get("psnr_mean") or -1)):
        cls, msg = verdict(r)
        bits = []
        if r.get("frames") is not None:
            bits.append(f"<span><b>{r['frames']}</b> input frames</span>")
        if r.get("iterations"):
            bits.append(f"<span><b>{r['iterations']:,}</b> iterations</span>")
        if r.get("psnr"):
            bits.append("<span>PSNR <b>" +
                        " / ".join(f"{v:.1f}" for v in r["psnr"]) + "</b> dB</span>")
        if r.get("diverged_pct") is not None:
            bits.append(f"<span><b>{r['diverged_pct']:.1f}%</b> diverged</span>")
        if r.get("longest_edge_in"):
            bits.append(f"<span>longest edge <b>{r['longest_edge_in']}\"</b></span>")
        if r.get("finished"):
            bits.append(f"<span>finished <b>{r['finished']}</b></span>")
        img = (f'<img src="{r["image"]}" alt="ground truth vs render">'
               f'<p class=caption>Left: ground-truth photo. Right: the splat '
               f'rendered from that same camera.</p>'
               if r.get("image") else
               '<p class=caption>No <code>render_check/</code> — nothing to show.</p>')
        cards.append(f"""<div class=card>
      <h3>{r['name']}<span class="pill {cls}">{cls.upper()}</span></h3>
      <div class=body>
        <p class=msg>{msg}</p>
        <div class=meta>{''.join(bits)}</div>
        {img}
      </div>
    </div>""")

    mesh_cards = []
    for m in sorted(meshes, key=lambda x: (x.get("scale") != "metric", x["name"])):
        cls, pill, note = SCALE_UI[m.get("scale", "unknown")]
        bits = [f"<span>scale: <b>{note}</b></span>",
                f"<span>glb <b>{m.get('glb_mb', 0):.1f} MB</b></span>"]
        if m.get("dims_mm"):
            bits.append(f"<span><b>{m['dims_mm']}</b> mm</span>")
        shots = "".join(f'<img src="{i}">' for i in m.get("images", []))
        body = (f"<div class=shots>{shots}</div>"
                "<p class=caption>Rendered turntable of the exported mesh.</p>"
                if shots else
                "<p class=caption>No renders — nothing to judge this mesh by.</p>")
        mesh_cards.append(f"""<div class=card>
      <h3>{m['name']}<span class="pill {cls}">{pill}</span></h3>
      <div class=body>
        <div class=meta>{''.join(bits)}</div>
        {body}
      </div>
    </div>""")

    return f"""<!doctype html>
<meta charset=utf-8><title>Splat benchmark</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>{CSS}</style>
<header>
  <h1>Splat &amp; mesh benchmark</h1>
  <p class=sub>PSNR is measured against the ground-truth photo at the real
  capture pose, not a held-out split — it answers "does this look like the
  thing I scanned". The scatter previews in each run directory are mean-position
  plots and cannot tell a good splat from a diverged one; ignore them.</p>
  <p class=sub style="margin-top:8px">Generated {generated}</p>
</header>
<main>
  <h2>3DGUT runs</h2>
  <table>
    <thead><tr><th>run</th><th class=num>frames</th><th class=num>iters</th>
      <th class=num>PSNR dB</th><th class=num>diverged</th>
      <th class=num>gaussians</th><th>verdict</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <div class=note>Bands: <b>good ≥ {GOOD:.0f} dB</b>, marginal ≥ {FAIR:.0f} dB,
  failed below. Divergence over {DIVERGED_WARN:.0f}% means the optimisation blew up
  rather than converged.</div>
  {''.join(cards)}
  <h2>TRELLIS meshes</h2>
  {''.join(mesh_cards) if mesh_cards else '<p class=sub>None found.</p>'}
</main>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(REPO, "demo_out", "BENCHMARK.html"))
    ap.add_argument("--open", action="store_true", help="open in a browser")
    args = ap.parse_args()

    splats = [collect_splat(d) for d in
              sorted(glob.glob(os.path.join(REPO, "demo_out", "*_3dgut*")))
              if os.path.isdir(d)]
    print(f"3DGUT runs: {len(splats)}")

    meshes = []
    for glb in sorted(glob.glob(os.path.join(REPO, "demo_out", "**",
                                             "asset_trellis.glb"), recursive=True)):
        d = os.path.dirname(glb)
        shots = sorted(glob.glob(os.path.join(d, "render", "*.png")))
        meshes.append(collect_mesh(d, shots))
    print(f"TRELLIS meshes: {len(meshes)}")

    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    html = render_html(splats, meshes, stamp)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        fh.write(html)
    print(f"→ {args.out}  ({os.path.getsize(args.out)/1e6:.1f} MB)")

    for r in sorted(splats, key=lambda x: -(x.get("psnr_mean") or -1)):
        cls, msg = verdict(r)
        print(f"  [{cls.upper():4s}] {r['name']:38s} {msg}")

    if args.open:
        webbrowser.open("file://" + os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
