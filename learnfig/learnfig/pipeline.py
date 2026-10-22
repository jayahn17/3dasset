"""Build everything for one topic: checks → analysis → figures → page."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Dict, List, Optional

from .checks import Report, run_checks
from .load import dump_topic
from .model import FigureRequest, Topic
from .style import SVG_CSS
from .render import (render_influence_matrix, render_page, render_scene3d, render_spatial, render_timeline,
                     render_tree_static, render_tree_steps_html, render_variable_graph, resolve_live)
from .simulate import Trajectory, cascade, influence_matrix, integrate
from .trees import ALGORITHM_TITLES, build_tree, final_tree, trace


@dataclass
class BuildResult:
    out_dir: Path
    topic: Topic
    report: Report
    figures: List[dict] = field(default_factory=list)
    files: Dict[str, Path] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @property
    def index(self) -> Path:
        return self.out_dir / "index.html"


def default_figures(topic: Topic) -> List[FigureRequest]:
    figs: List[FigureRequest] = []
    if len(topic.variables) >= 2:
        figs.append(FigureRequest("variable_graph"))
    if len(topic.relations) >= 2:
        figs.append(FigureRequest("influence_matrix"))
    if topic.dynamics or topic.cascade.shock or any(e.shock for e in topic.events) or len(topic.relations) >= 3:
        figs.append(FigureRequest("timeline"))
        figs.append(FigureRequest("scene3d"))
    if topic.tree:
        figs.append(FigureRequest("tree"))
    if len(topic.variables) >= 2:
        figs.append(FigureRequest("spatial"))
    return figs


def _auto_shock(topic: Topic) -> Optional[Dict[str, float]]:
    if topic.cascade.shock:
        return dict(topic.cascade.shock)
    for ev in topic.events:
        if ev.shock:
            return dict(ev.shock)
    for v in topic.variables:
        if v.kind in ("parameter", "external") and topic.outgoing(v.id):
            return {v.id: 0.10}
    for v in topic.variables:
        if topic.outgoing(v.id):
            return {v.id: 0.10}
    return None


def _auto_caption(kind: str, topic: Topic, meta: dict, extra: str = "") -> str:
    if kind == "variable_graph":
        s = ("Arrows run from cause to effect. A blue + means the effect rises when the cause rises; "
             "a red − means it falls. Dashed arcs are feedback loops; numbered arrows are explained in the key.")
        if meta.get("loops"):
            s += f" {len(meta['loops'])} feedback loop(s) found."
        return s
    if kind == "influence_matrix":
        if meta.get("mode") == "elasticity":
            return ("Each cell is the percentage change in the column variable produced by a 1 % increase in the row "
                    "variable, evaluated at the default values (all other inputs held fixed). Blue = the effect rises with "
                    "the cause, red = it falls, pale = weak; the same colours as the arrows in the variable graph.")
        return ("Each cell is the declared direction × strength of the row's influence on the column. "
                "Blue = same direction, red = opposite direction, as in the variable graph.")
    if kind == "timeline":
        parts = []
        if meta.get("fired") is not None:
            parts.append("Panel A plots each state variable against time, with the moments events fire marked.")
        if meta.get("cascade_events"):
            parts.append(f"Panel B follows the chain reaction set off by the shock: {meta['cascade_events'] - 1} downstream "
                         f"changes over {meta.get('cascade_depth', 0)} hops; later markers are consequences of earlier ones. "
                         "Sizes are first-order estimates (shock × elasticity along each path), so read the direction and "
                         "the order of events, and check large changes with a full simulation.")
        return " ".join(parts)
    if kind == "tree":
        alg = (meta or {}).get("algorithm", "")
        if alg == "decision":
            return ("The highlighted path follows one case through the questions from the root to a leaf; the other "
                    "branches are the outcomes it did not take. Step through it to read each test in order.")
        if alg == "minimax":
            return ("Leaf values are the outcomes; each parent takes the maximum or minimum of its children, so the "
                    "number on the root is the result of perfect play by both sides. Numbers give the evaluation order.")
        if alg == "height":
            return "Each node is labelled with the height of its subtree, computed from the leaves upward."
        if alg in ("bst_search", "bst_insert"):
            return ("Numbers give the order of comparisons along the search path; one comparison per level is why a "
                    "balanced tree is fast. Step through it to see each comparison.")
        return (f"Numbers on the nodes give the order in which {extra} records them; filled nodes were recorded. "
                "Open the step-by-step view to watch the stack or queue change.")
    if kind == "scene3d":
        return ("Lanes along x are the variables, depth is time and height is magnitude. Drag to orbit, scrub the time "
                "slider to replay the cascade, and press Enter VR on a headset.")
    if kind == "spatial":
        s = ("Every variable is a city on a globe you can turn; pulses run along the arcs from cause to effect. "
             "Tap a city to raise it and unfold its card on the table. The figures above stand on an arc around you, "
             "and press Enter VR or Enter AR on a headset to grab the globe with your hands.")
        if meta.get("path"):
            s += f" The plate beside the globe traces {meta['path']} over time; unroll it to make time the height."
        if meta.get("live"):
            s += f" {meta['live']} live feed(s) are shown beside the model value they test."
        return s
    return ""


def build(topic: Topic, out_dir: str | Path, checks: bool = True, figure_requests: Optional[List[FigureRequest]] = None,
          generated_by: str = "learnfig") -> BuildResult:
    out = Path(out_dir)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    for stale in (out / "figures").glob("*.svg"):   # a figure dropped from the spec must not linger
        stale.unlink()
    for stale in ("scene3d.html", "tree_steps.html", "spatial.html"):
        if (out / stale).exists():
            (out / stale).unlink()
    report = run_checks(topic, simulate=checks) if checks else Report()
    values = report.values
    requests = figure_requests or topic.figures or default_figures(topic)
    result = BuildResult(out_dir=out, topic=topic, report=report)
    counter = 0
    prefix = f"{topic.chapter}." if topic.chapter else ""

    def number() -> str:
        nonlocal counter
        counter += 1
        return f"{prefix}{counter}"

    traj: Optional[Trajectory] = None
    events = []
    needs_time = any(r.type in ("timeline", "scene3d", "spatial") for r in requests)
    if needs_time:
        if topic.dynamics:
            try:
                traj = integrate(topic)
            except Exception as exc:  # noqa: BLE001
                result.notes.append(f"integration failed: {exc}")
        shock = _auto_shock(topic)
        if shock:
            events = cascade(topic, shock)
            if not topic.cascade.shock and not any(e.shock for e in topic.events):
                result.notes.append(f"no shock declared; cascade uses an automatic +10 % shock on {list(shock)[0]}")
    extras: Dict[str, str] = {}
    scene_file = None
    if any(r.type == "scene3d" for r in requests) and (traj or events):
        scene_file = out / "scene3d.html"
        scene_file.write_text(render_scene3d(topic, traj, events), encoding="utf-8")
        result.files["scene3d"] = scene_file
        extras["scene3d"] = "scene3d.html"

    spatial_req: Optional[FigureRequest] = None
    for req in requests:
        if req.type == "spatial":
            spatial_req = req   # rendered last: its panels are the other figures
            continue
        if req.type == "variable_graph":
            svg, key = render_variable_graph(topic, values, report.loops, req.options, standalone=True)
            svg_inline, _ = render_variable_graph(topic, values, report.loops, req.options, standalone=False)
            fid = "fig-variables"
            path = out / "figures" / f"{fid}.svg"
            path.write_text(svg, encoding="utf-8")
            result.figures.append({"id": fid, "type": req.type, "number": number(),
                                   "title": req.title or "Every variable and how it affects the others",
                                   "caption": req.caption or _auto_caption(req.type, topic, {"loops": report.loops}),
                                   "svg_inline": svg_inline, "file": path, "key": key, "notice": req.options.get("notice")})
        elif req.type == "influence_matrix":
            rows, cols, vals, mode = influence_matrix(topic)
            if not rows:
                result.notes.append("influence matrix skipped: no relations")
                continue
            svg = render_influence_matrix(topic, rows, cols, vals, mode, standalone=True)
            svg_inline = render_influence_matrix(topic, rows, cols, vals, mode, standalone=False)
            fid = "fig-influence"
            path = out / "figures" / f"{fid}.svg"
            path.write_text(svg, encoding="utf-8")
            result.figures.append({"id": fid, "type": req.type, "number": number(),
                                   "title": req.title or "Who moves whom: the influence matrix",
                                   "caption": req.caption or _auto_caption(req.type, topic, {"mode": mode}),
                                   "svg_inline": svg_inline, "file": path, "notice": req.options.get("notice")})
        elif req.type == "timeline":
            if traj is None and not events:
                result.notes.append("timeline skipped: no dynamics and no shock to propagate")
                continue
            svg, meta = render_timeline(topic, traj, events, req.options, standalone=True)
            svg_inline, _ = render_timeline(topic, traj, events, req.options, standalone=False)
            fid = "fig-timeline"
            path = out / "figures" / f"{fid}.svg"
            path.write_text(svg, encoding="utf-8")
            result.figures.append({"id": fid, "type": req.type, "number": number(),
                                   "title": req.title or "Timeline: evolution and chain reaction",
                                   "caption": req.caption or _auto_caption(req.type, topic, meta),
                                   "svg_inline": svg_inline, "file": path, "meta": meta, "notice": req.options.get("notice")})
        elif req.type == "scene3d":
            if scene_file is None:
                continue
            result.figures.append({"id": "fig-3d", "type": req.type, "number": number(),
                                   "title": req.title or "The same timeline in 3D / VR",
                                   "caption": req.caption or _auto_caption(req.type, topic, {}),
                                   "svg_inline": "", "embed": "scene3d.html", "file": scene_file,
                                   "notice": req.options.get("notice")})
        elif req.type == "tree":
            if not topic.tree:
                result.notes.append("tree figure skipped: no tree in spec")
                continue
            root = build_tree(topic.tree)
            algs = [a if isinstance(a, dict) else {"name": a} for a in topic.tree.algorithms] or [{"name": "bfs"}]
            if req.options.get("algorithm"):
                algs = [a for a in algs if a["name"] == req.options["algorithm"]] or algs[:1]
            sections = []
            seen_ids: Dict[str, int] = {}
            for alg in algs:
                base = alg["name"]
                seen_ids[base] = seen_ids.get(base, 0) + 1
                suffix = "" if seen_ids[base] == 1 else f"-{seen_ids[base]}"
                steps = trace(root, alg, topic.tree.kind)
                froot = final_tree(root, alg)
                title = alg.get("title") or ALGORITHM_TITLES.get(alg["name"], alg["name"])
                if alg.get("key") is not None:
                    title += f" for {alg['key']}"
                if alg.get("keys"):
                    title += f" of {alg['keys']}"
                svg = render_tree_static(froot, title, steps, standalone=True)
                svg_inline = render_tree_static(froot, title, steps, standalone=False)
                fid = f"fig-tree-{alg['name']}{suffix}"
                path = out / "figures" / f"{fid}.svg"
                path.write_text(svg, encoding="utf-8")
                result.figures.append({"id": fid, "type": req.type, "number": number(),
                                       "title": req.title or f"{topic.tree.title or 'Tree'}: {title}",
                                       "caption": req.caption or _auto_caption(req.type, topic, {"algorithm": alg["name"]}, title.lower()),
                                       "svg_inline": svg_inline, "file": path, "notice": req.options.get("notice")})
                sections.append({"id": f"alg-{alg['name']}{suffix}", "title": title, "intro": alg.get("intro", ""),
                                 "root": froot, "steps": steps})
            steps_file = out / "tree_steps.html"
            steps_file.write_text(render_tree_steps_html(topic.title, sections), encoding="utf-8")
            result.files["tree_steps"] = steps_file
            extras["tree_steps"] = "tree_steps.html"
            if result.figures and result.figures[-1]["type"] == "tree":
                result.figures[-1]["embed"] = "tree_steps.html"
                result.figures[-1]["embed_light"] = True
        else:
            result.notes.append(f"unknown figure type {req.type!r} skipped")

    if spatial_req is not None:
        _add_spatial(result, topic, traj, values, spatial_req, number(), extras)

    # reports & spec
    (out / "report.md").write_text(report.to_markdown(f"Check report: {topic.title}"), encoding="utf-8")
    (out / "report.json").write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    dump_topic(topic, out / "spec.resolved.yaml", include_auto=True)
    extras["report_md"] = "report.md"
    extras["spec"] = "spec.resolved.yaml"
    page = render_page(topic, result.figures, report, values, extras, generated_by=generated_by)
    (out / "index.html").write_text(page, encoding="utf-8")
    result.files["index"] = out / "index.html"
    result.files["report"] = out / "report.md"
    return result


def _add_spatial(result: BuildResult, topic: Topic, traj: Optional[Trajectory], values: Dict[str, float],
                 req: FigureRequest, num: str, extras: Dict[str, str]) -> None:
    """The spatial room: a concept globe, the 2D figures as panels, the path plate and live feeds."""
    panels, seen = [], set()
    for f in result.figures:
        if f["type"] in ("variable_graph", "influence_matrix", "timeline", "tree") and f["type"] not in seen and f.get("file"):
            seen.add(f["type"])
            panels.append({"title": f["title"], "number": f["number"], "svg": Path(f["file"]).read_text(encoding="utf-8")})
    live, problems = resolve_live(topic, values, req.options.get("live"))
    html = render_spatial(topic, traj, values, panels[:4], req.options, live, problems)
    result.notes += [f"spatial: {p}" for p in problems]
    path = result.out_dir / "spatial.html"
    path.write_text(html, encoding="utf-8")
    result.files["spatial"] = path
    extras["spatial"] = "spatial.html"
    meta = {"live": len(live)}
    if traj is not None:
        want = req.options.get("path") or [s for s in topic.state_ids if s in traj.series][:2]
        names = [topic.var(v).label if topic.var(v) else v for v in want if v in traj.series]
        meta["path"] = " against ".join(names) if len(names) == 2 else (f"{names[0]} against time" if names else "")
    result.figures.append({"id": "fig-spatial", "type": "spatial", "number": num,
                           "title": req.title or "The whole topic as a room you can walk around (VR / AR)",
                           "caption": req.caption or _auto_caption("spatial", topic, meta),
                           "svg_inline": "", "embed": "spatial.html", "allow": "xr-spatial-tracking; fullscreen",
                           "file": path, "notice": req.options.get("notice")})


def render_gallery(results: List[BuildResult], out_dir: Path, title: str = "Learning figures") -> Path:
    groups: Dict[str, List[str]] = {}
    titles = {"": "Generic examples", "zohdi_drone_book": "Zohdi — drone-swarm primer (ME 244)",
              "einstein_relativity": "Einstein — Relativity: The Special and General Theory (1920)",
              "open_physics": "Open introductory physics"}
    for r in results:
        rel = r.out_dir.relative_to(out_dir)
        suite = rel.parts[0] if len(rel.parts) > 1 else ""
        status = "PASS" if r.report.ok else "FAIL"
        color = "#006300" if r.report.ok else "#d03b3b"
        first = next((f for f in r.figures if f.get("svg_inline")), None)
        thumb = first["svg_inline"] if first else ""
        thumb = thumb.replace('role="img"', 'role="img" preserveAspectRatio="xMidYMid meet"', 1)
        groups.setdefault(suite, []).append(f"""<a class="card" href="{rel}/index.html"><div class="thumb">{thumb}</div>
<h3>{escape(r.topic.title)}</h3><p>{escape(r.topic.domain)} · {len(r.figures)} figures · <b style="color:{color}">{status}</b></p>
<p class="s">{escape(r.topic.summary[:160])}</p></a>""")
    sections = []
    for suite in sorted(groups, key=lambda k: (k != "", list(titles).index(k) if k in titles else 99, k)):
        sections.append(f'<h2>{escape(titles.get(suite, suite.replace("_", " ")))}</h2><div class="grid">{"".join(groups[suite])}</div>')
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><style>
:root{{--bg:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e1e0d9}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--line:#2c2c2a}}}}
:root[data-theme="dark"]{{--bg:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--line:#2c2c2a}}
body{{margin:0;padding:28px 16px;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1100px;margin:0 auto}} h1{{margin:0 0 4px}} h2{{font-size:1.1rem;margin:28px 0 10px;color:var(--ink2)}} .sub{{color:var(--ink2);margin:0 0 20px}} a{{color:#2a78d6}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}}
.card{{display:block;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px;text-decoration:none;color:inherit}}
.card:hover{{border-color:#2a78d6}} .thumb{{height:170px;overflow:hidden;border-radius:8px;background:#fcfcfb;border:1px solid var(--line)}}
.thumb svg{{width:100%;height:100%}} h3{{margin:10px 0 2px;font-size:1.05rem}}
svg.lf{{ {SVG_CSS.replace(chr(10), ' ')} }} p{{margin:2px 0;color:var(--ink2);font-size:13.5px}} .s{{font-size:13px}}
</style></head><body><main><h1>{escape(title)}</h1><p class="sub">{len(results)} topics rendered from specs. Each page carries its own science check.</p>
{''.join(sections)}
<p class="sub" style="margin-top:28px">How to read these: <a href="../docs/LEARNER_GUIDE.md">the seven questions</a> (docs/LEARNER_GUIDE.md).</p></main></body></html>"""
    path = out_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path
