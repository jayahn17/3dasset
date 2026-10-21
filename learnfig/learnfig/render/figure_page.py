"""Compose the figures, captions, keys, values and check report into one
textbook-style HTML page with light/dark themes.
"""
from __future__ import annotations

from html import escape
from typing import Dict, List, Optional

from ..model import Topic
from ..style import FONT, SVG_CSS
from ..svg import fmt
from ..units import pretty_unit

PAGE_CSS = f"""
:root {{ --bg:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781; --line:#e1e0d9; --accent:#2a78d6; --good:#006300; --bad:#d03b3b; --warn:#b8491e; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781; --line:#2c2c2a; --accent:#3987e5; --good:#0ca30c; --bad:#e66767; --warn:#eb6834; }} }}
:root[data-theme="dark"] {{ --bg:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781; --line:#2c2c2a; --accent:#3987e5; --good:#0ca30c; --bad:#e66767; --warn:#eb6834; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; padding: 28px 16px 72px; background: var(--bg); color: var(--ink); font-family: {FONT}; line-height: 1.5; }}
main {{ max-width: 1040px; margin: 0 auto; }}
header h1 {{ font-size: 1.75rem; margin: 0 0 6px; letter-spacing: -0.01em; }}
.meta {{ display:flex; gap:8px; flex-wrap:wrap; margin: 0 0 10px; }}
.chip {{ font-size: 12px; color: var(--ink2); border:1px solid var(--line); border-radius: 999px; padding: 2px 10px; background: var(--surface); }}
.summary {{ font-size: 1.05rem; color: var(--ink2); max-width: 70ch; margin: 0 0 18px; }}
.toolbar {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin: 8px 0 22px; }}
.toolbar a, .toolbar button {{ font: inherit; font-size: 13.5px; color: var(--ink); background: var(--surface); border:1px solid var(--line); border-radius: 8px; padding: 6px 12px; text-decoration:none; cursor:pointer; }}
.toolbar a:hover, .toolbar button:hover {{ border-color: var(--accent); }}
.status {{ font-weight:600; }} .status.pass {{ color: var(--good); }} .status.fail {{ color: var(--bad); }}
figure.fig {{ margin: 26px 0 34px; background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 18px 18px 14px; }}
figure.fig .plot {{ overflow-x: auto; }} figure.fig svg {{ display:block; max-width: 100%; height:auto; margin: 0 auto; }}
figcaption {{ margin-top: 12px; font-size: 14.5px; color: var(--ink2); max-width: 90ch; }}
figcaption .fignum {{ font-weight: 700; color: var(--ink); }}
.key {{ margin: 8px 0 0; padding-left: 0; list-style: none; columns: 2; column-gap: 28px; font-size: 13.5px; color: var(--ink2); }}
.key li {{ break-inside: avoid; margin: 0 0 6px; }} .key b {{ color: var(--ink); font-weight: 600; }}
.key .pos {{ color:#2a78d6; font-weight:700; }} .key .neg {{ color:#e34948; font-weight:700; }}
.notice {{ margin: 10px 0 0; padding-left: 18px; font-size: 14px; color: var(--ink2); }} .notice li {{ margin: 3px 0; }}
.embed {{ margin-top: 12px; border:1px solid var(--line); border-radius: 10px; overflow:hidden; background: #0d0d0d; }}
.embed {{ position: relative; }} .embed iframe {{ width:100%; height: 460px; border:0; display:block; }}
.embed .shield {{ position:absolute; inset:0; display:flex; align-items:flex-end; justify-content:flex-end; padding: 10px 12px; color: #c3c2b7; font-size: 12.5px; background: linear-gradient(transparent 70%, rgba(13,13,13,.55)); cursor: pointer; }}
.embed.light .shield {{ color: var(--ink2); background: linear-gradient(transparent 70%, rgba(0,0,0,.12)); }}
.embed .shield a {{ color: inherit; margin-left: 6px; }}
.embed.light {{ background: var(--surface); }}
h2 {{ font-size: 1.2rem; margin: 34px 0 10px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; background: var(--surface); border:1px solid var(--line); border-radius: 10px; overflow:hidden; }}
th, td {{ text-align:left; padding: 7px 10px; border-bottom: 1px solid var(--line); }} th {{ color: var(--ink2); font-weight: 600; font-size: 12.5px; }}
td.num {{ font-variant-numeric: tabular-nums; text-align:right; }} tr:last-child td {{ border-bottom: 0; }}
.kind {{ display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:6px; vertical-align: 0; }}
details {{ background: var(--surface); border:1px solid var(--line); border-radius: 10px; padding: 10px 14px; margin: 10px 0; }}
summary {{ cursor:pointer; font-weight: 600; }}
.find {{ margin: 6px 0; font-size: 13.5px; }} .find code {{ font-size: 12px; background: var(--bg); padding: 1px 5px; border-radius: 4px; }}
.find.error {{ color: var(--bad); }} .find.warning {{ color: var(--warn); }} .find.info {{ color: var(--ink2); }}
.notes {{ font-size: 15px; padding-left: 20px; }} .notes li {{ margin: 6px 0; }}
footer {{ margin-top: 40px; color: var(--muted); font-size: 12.5px; }}
svg.lf {{ {SVG_CSS.replace(chr(10), ' ')} }}
:root[data-theme="dark"] svg.lf .lf-title, :root[data-theme="dark"] svg.lf .lf-label, :root[data-theme="dark"] svg.lf .lf-tn text {{ fill: #ffffff; }}
:root[data-theme="dark"] svg.lf .lf-sub, :root[data-theme="dark"] svg.lf .lf-legend, :root[data-theme="dark"] svg.lf .lf-callout, :root[data-theme="dark"] svg.lf .lf-te-label, :root[data-theme="dark"] svg.lf .lf-mono {{ fill: #c3c2b7; }}
:root[data-theme="dark"] svg.lf .lf-node, :root[data-theme="dark"] svg.lf .lf-tn circle, :root[data-theme="dark"] svg.lf .lf-tn .lf-pill {{ fill: #1a1a19; }}
:root[data-theme="dark"] svg.lf .lf-grid {{ stroke: #2c2c2a; }} :root[data-theme="dark"] svg.lf .lf-axis, :root[data-theme="dark"] svg.lf .lf-te {{ stroke: #55544f; }}
:root[data-theme="dark"] svg.lf .lf-badge {{ stroke: #1a1a19; }} :root[data-theme="dark"] svg.lf .lf-order-bg {{ fill: #c3c2b7; }} :root[data-theme="dark"] svg.lf .lf-order {{ fill: #0b0b0b; }}
:root[data-theme="dark"] svg.lf .lf-tn.visited text, :root[data-theme="dark"] svg.lf .lf-tn.found text {{ fill: #ffffff; }}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) svg.lf .lf-title, :root:not([data-theme="light"]) svg.lf .lf-label, :root:not([data-theme="light"]) svg.lf .lf-tn text {{ fill: #ffffff; }}
  :root:not([data-theme="light"]) svg.lf .lf-sub, :root:not([data-theme="light"]) svg.lf .lf-legend, :root:not([data-theme="light"]) svg.lf .lf-callout, :root:not([data-theme="light"]) svg.lf .lf-te-label, :root:not([data-theme="light"]) svg.lf .lf-mono {{ fill: #c3c2b7; }}
  :root:not([data-theme="light"]) svg.lf .lf-node, :root:not([data-theme="light"]) svg.lf .lf-tn circle, :root:not([data-theme="light"]) svg.lf .lf-tn .lf-pill {{ fill: #1a1a19; }}
  :root:not([data-theme="light"]) svg.lf .lf-grid {{ stroke: #2c2c2a; }} :root:not([data-theme="light"]) svg.lf .lf-axis, :root:not([data-theme="light"]) svg.lf .lf-te {{ stroke: #55544f; }}
  :root:not([data-theme="light"]) svg.lf .lf-badge {{ stroke: #1a1a19; }} :root:not([data-theme="light"]) svg.lf .lf-order-bg {{ fill: #c3c2b7; }} :root:not([data-theme="light"]) svg.lf .lf-order {{ fill: #0b0b0b; }}
  :root:not([data-theme="light"]) svg.lf .lf-tn.visited text, :root:not([data-theme="light"]) svg.lf .lf-tn.found text {{ fill: #ffffff; }}
}}
@media (max-width: 640px) {{ .key {{ columns: 1; }} figure.fig {{ padding: 12px; }} }}
"""

KIND_COLORS = {"parameter": "#eb6834", "state": "#2a78d6", "derived": "#1baf7a", "constant": "#898781", "external": "#4a3aa7"}


def _key_html(key: List[dict], topic: Topic) -> str:
    if not key:
        return ""
    items = []
    for k in key:
        s = topic.var(k["source"])
        t = topic.var(k["target"])
        sign = {"+": '<span class="pos">+</span>', "-": '<span class="neg">−</span>'}.get(k["sign"], "?")
        items.append(f'<li><b>({k["n"]})</b> {escape(s.label if s else k["source"])} → '
                     f'{escape(t.label if t else k["target"])} ({sign}): {escape(k["mechanism"])}</li>')
    return f'<ol class="key">{"".join(items)}</ol>'


def _findings_html(report) -> str:
    if report is None:
        return ""
    cls = "pass" if report.ok else "fail"
    counts = report.to_dict()["counts"]
    head = (f'<span class="status {cls}">{"PASS" if report.ok else "FAIL"}</span> · '
            f'{counts["error"]} errors · {counts["warning"]} warnings · {counts["info"]} notes')
    rows = []
    for f in report.findings:
        where = f" <code>{escape(f.where)}</code>" if f.where else ""
        hint = f" <em>— {escape(f.hint)}</em>" if f.hint else ""
        rows.append(f'<div class="find {f.level}"><b>{escape(f.code)}</b>{where}: {escape(f.message)}{hint}</div>')
    loops = ""
    if report.loops:
        loops = "<p><b>Feedback loops</b></p>" + "".join(
            f'<div class="find info">{lp["id"]} ({lp["polarity"]}): {escape(" → ".join(lp["cycle"] + [lp["cycle"][0]]))}</div>'
            for lp in report.loops)
    return f"<details><summary>Science check: {head}</summary>{''.join(rows) or '<div class=find>No findings.</div>'}{loops}</details>"


def _values_table(topic: Topic, values: Dict[str, float]) -> str:
    if not topic.variables:
        return ""
    rows = []
    for v in topic.variables:
        val = values.get(v.id)
        rows.append(
            f'<tr><td><span class="kind" style="background:{KIND_COLORS.get(v.kind, "#898781")}"></span>{escape(v.label)}</td>'
            f'<td>{escape(v.sym)}</td><td class="num">{fmt(val) if val is not None else "—"}</td>'
            f'<td>{escape(pretty_unit(v.unit))}</td><td>{escape(v.kind)}</td>'
            f'<td>{escape(v.equation or "")}</td><td>{escape(v.description or "")}</td></tr>')
    return ('<table><thead><tr><th>Variable</th><th>Symbol</th><th>Value at defaults</th><th>Unit</th><th>Kind</th>'
            '<th>Equation</th><th>Meaning</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>")


def render_page(topic: Topic, figures: List[dict], report=None, values: Optional[Dict[str, float]] = None,
                extras: Optional[Dict[str, str]] = None, generated_by: str = "learnfig") -> str:
    extras = extras or {}
    values = values or {}
    chips = [c for c in (topic.domain, topic.level, f"chapter {topic.chapter}" if topic.chapter else "") if c]
    tools = []
    if extras.get("tree_steps"):
        tools.append(f'<a href="{extras["tree_steps"]}">▶ Step through the tree algorithms</a>')
    if extras.get("scene3d"):
        tools.append(f'<a href="{extras["scene3d"]}">⬡ Open the 3D / VR timeline</a>')
    if extras.get("spatial"):
        tools.append(f'<a href="{extras["spatial"]}">◎ Open the spatial room (VR / AR)</a>')
    if extras.get("report_md"):
        tools.append(f'<a href="{extras["report_md"]}">Check report (markdown)</a>')
    if extras.get("spec"):
        tools.append(f'<a href="{extras["spec"]}">Topic spec (YAML)</a>')
    tools.append('<button id="theme" type="button">Toggle dark mode</button>')
    figs_html = []
    for f in figures:
        notice = ""
        if f.get("notice"):
            notice = '<ul class="notice">' + "".join(f"<li>{escape(n)}</li>" for n in f["notice"]) + "</ul>"
        embed = ""
        if f.get("embed"):
            allow = f' allow="{escape(f["allow"])}"' if f.get("allow") else ""
            embed = (f'<div class="embed {"light" if f.get("embed_light") else ""}">'
                     f'<iframe src="{f["embed"]}" loading="lazy" title="{escape(f["title"])}"{allow}></iframe>'
                     f'<div class="shield" onclick="this.remove()">click to interact · <a href="{f["embed"]}" target="_blank" rel="noopener" onclick="event.stopPropagation()">open full size ↗</a></div></div>')
        figs_html.append(
            f'<figure class="fig" id="{f["id"]}"><div class="plot">{f.get("svg_inline", "")}</div>{embed}'
            f'<figcaption><span class="fignum">Figure {f["number"]}.</span> {escape(f["title"])}. {escape(f.get("caption", ""))}</figcaption>'
            f'{_key_html(f.get("key", []), topic)}{notice}</figure>')
    notes = ""
    if topic.notes:
        notes = "<h2>What to take away</h2><ul class=\"notes\">" + "".join(f"<li>{escape(str(n))}</li>" for n in topic.notes) + "</ul>"
    sources = ""
    if topic.sources:
        sources = "<h2>Sources</h2><ul class=\"notes\">" + "".join(f"<li>{escape(str(s))}</li>" for s in topic.sources) + "</ul>"
    js = """
const b=document.getElementById('theme');const r=document.documentElement;
try{const s=localStorage.getItem('lf-theme');if(s)r.dataset.theme=s;}catch(e){}
b.addEventListener('click',()=>{const dark=r.dataset.theme==='dark'||(!r.dataset.theme&&matchMedia('(prefers-color-scheme: dark)').matches);r.dataset.theme=dark?'light':'dark';try{localStorage.setItem('lf-theme',r.dataset.theme);}catch(e){}});
"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(topic.title)}</title><style>{PAGE_CSS}</style></head>
<body><main>
<header><h1>{escape(topic.title)}</h1>
<div class="meta">{''.join(f'<span class="chip">{escape(c)}</span>' for c in chips)}</div>
<p class="summary">{escape(topic.summary)}</p>
<div class="toolbar">{''.join(tools)}</div>
{_findings_html(report)}
</header>
{''.join(figs_html)}
{notes}
<h2>Variables at a glance</h2>
{_values_table(topic, values)}
{sources}
<footer>Generated by {escape(generated_by)}. Figures are deterministic renderings of the topic spec; the science check runs on every build.</footer>
</main><script>{js}</script></body></html>"""
