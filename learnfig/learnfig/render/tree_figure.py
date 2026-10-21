"""Tree figures: a static textbook figure (visit order printed on the nodes)
and an interactive stepper page that replays an algorithm trace.
"""
from __future__ import annotations

import json
from html import escape
from typing import Dict, List, Optional, Tuple

from ..style import SVG_CSS, FONT
from ..svg import SVG, text_width
from ..trees import TNode, layout


def _shape(root: TNode) -> Tuple[Dict[str, float], bool]:
    widths = {}
    pill = False
    for n in root.walk():
        w = text_width(n.label, 12, bold=True) + 18
        widths[n.id] = max(36.0, w)
        if len(n.label) > 5:
            pill = True
    return widths, pill


def _tree_svg(root: TNode, title: str, step: Optional[dict] = None, show_order: bool = False,
              standalone: bool = True, section_id: str = "") -> str:
    widths, pill = _shape(root)
    max_w = max(widths.values())
    x_gap = max_w + 22
    y_gap = 84 if pill else 76
    pos = layout(root, x_gap=x_gap, y_gap=y_gap, widths=widths if root.kind != "binary" else None)
    ys = [p[1] for p in pos.values()]
    # pills are centred on their x, so the horizontal extent must include half the width of the
    # outermost nodes or a wide leaf at either edge is clipped by the viewBox
    half = {n: (0.0 if root.kind == "binary" else widths[n] / 2) for n in pos}
    x_lo = min(x - half[n] for n, (x, _) in pos.items())
    x_hi = max(x + half[n] for n, (x, _) in pos.items())
    pad = (max_w / 2 if root.kind == "binary" else 24) + 24
    W = (x_hi - x_lo) + 2 * pad
    H = (max(ys) - min(ys)) + 2 * pad + 20
    ox = pad - x_lo
    oy = pad - min(ys) + 10
    svg = SVG(max(W, 320), H, title)
    hidden = set(step.get("hidden", [])) if step else set()
    active = {tuple(e) for e in step.get("active_edges", [])} if step else set()
    visited = set(step.get("visited", [])) if step else set()
    current = set(step.get("current", [])) if step else set()
    frontier = set(step.get("frontier", [])) if step else set()
    found = set(step.get("found", [])) if step else set()
    labels = step.get("labels", {}) if step else {}
    order = step.get("order", {}) if step else {}

    # edges
    for n in root.walk():
        x1, y1 = pos[n.id]
        for c in n.kids():
            x2, y2 = pos[c.id]
            cls = "lf-te"
            if (n.id, c.id) in active:
                cls += " active"
            if n.id in hidden or c.id in hidden:
                cls += " hidden"
            svg.line(x1 + ox, y1 + oy, x2 + ox, y2 + oy, cls=cls, extra=f'data-edge="{n.id}>{c.id}"')
            if c.edge_label:
                mx, my = (x1 + x2) / 2 + ox, (y1 + y2) / 2 + oy
                lw = text_width(c.edge_label, 10) + 8
                svg.rect(mx - lw / 2, my - 8, lw, 15, rx=3, style="fill:#fcfcfb;stroke:#e1e0d9")
                svg.text(mx, my + 3.5, c.edge_label, cls="lf-te-label", anchor="middle")
    # nodes
    for n in root.walk():
        x, y = pos[n.id][0] + ox, pos[n.id][1] + oy
        cls = "lf-tn"
        if n.id in visited:
            cls += " visited"
        if n.id in current:
            cls += " current"
        if n.id in frontier:
            cls += " frontier"
        if n.id in found:
            cls += " found"
        if n.id in hidden:
            cls += " hidden"
        w = widths[n.id]
        if pill:
            shape = f'<rect x="{x - w / 2:.1f}" y="{y - 16:.1f}" width="{w:.1f}" height="32" rx="16"/>'
            # a rect inside .lf-tn needs the same styling as circle
            shape = shape.replace("<rect", '<rect class="lf-pill"')
        else:
            shape = f'<circle cx="{x:.1f}" cy="{y:.1f}" r="18"/>'
        ann = labels.get(n.id, "")
        ann_el = (f'<text class="lf-ann" data-ann="{n.id}" x="{x:.1f}" y="{y - (22 if not pill else 24):.1f}">'
                  f'{escape(ann)}</text>')
        o = order.get(n.id)
        bx, by = x + (w / 2 if pill else 18) - 4, y - (12 if pill else 14)
        badge = (f'<g class="lf-order-badge" data-order="{n.id}" style="display:{"inline" if (show_order and o) else "none"}">'
                 f'<circle class="lf-order-bg" cx="{bx:.1f}" cy="{by:.1f}" r="8"/>'
                 f'<text class="lf-order" x="{bx:.1f}" y="{by + 0.5:.1f}">{o or ""}</text></g>')
        svg.add(f'<g class="{cls}" data-id="{n.id}"><title>{escape(n.label)}</title>{shape}'
                f'<text x="{x:.1f}" y="{y + 0.5:.1f}">{escape(n.label)}</text>{ann_el}{badge}</g>')
    out = svg.tostring(standalone)
    extra_css = ("<style>.lf-tn .lf-pill{fill:#fcfcfb;stroke:#52514e;stroke-width:1.5}"
                 ".lf-tn.visited .lf-pill{fill:#2a78d6;stroke:#2a78d6}.lf-tn.current .lf-pill{stroke:#eb6834;stroke-width:3.5}"
                 ".lf-tn.frontier .lf-pill{stroke:#1baf7a;stroke-dasharray:4 2;stroke-width:2.5}"
                 ".lf-tn.found .lf-pill{fill:#008300;stroke:#008300}</style>")
    return out.replace("<defs>", extra_css + "<defs>", 1) if "<defs>" in out else out.replace("</title>", "</title>" + extra_css, 1)


def render_tree_static(root: TNode, title: str, steps: List[dict], standalone: bool = True) -> str:
    """Final frame with visit-order numbers: the printable textbook figure."""
    last = steps[-1] if steps else None
    # use the last step that still carries an order (the 'done' step may have reset it)
    best = None
    for s in reversed(steps):
        if s.get("order"):
            best = s
            break
    frame = dict(best or last or {})
    frame["current"] = []
    frame["frontier"] = []
    frame["active_edges"] = []
    frame["hidden"] = []
    return _tree_svg(root, title, step=frame, show_order=True, standalone=standalone)


def render_tree_steps_html(topic_title: str, sections: List[dict]) -> str:
    """One page, one stepper per algorithm.  sections: {id, title, intro, root, steps}."""
    parts = []
    for sec in sections:
        svg = _tree_svg(sec["root"], sec["title"], step=sec["steps"][0], standalone=False, section_id=sec["id"])
        parts.append(f"""
<section class="alg" id="{sec['id']}">
  <h2>{escape(sec['title'])}</h2>
  <p class="intro">{escape(sec.get('intro', ''))}</p>
  <div class="stage">{svg}</div>
  <div class="controls">
    <button data-act="first" title="First">⏮</button>
    <button data-act="prev" title="Previous (←)">◀</button>
    <button data-act="play" title="Play / pause (space)">▶</button>
    <button data-act="next" title="Next (→)">▶|</button>
    <input type="range" min="0" max="{len(sec['steps']) - 1}" value="0" step="1">
    <span class="counter">0 / {len(sec['steps']) - 1}</span>
  </div>
  <div class="note"></div>
  <div class="state"></div>
  <script type="application/json" class="steps">{json.dumps(sec['steps'])}</script>
</section>""")
    css = f"""
:root {{ --bg:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781; --line:#e1e0d9; --accent:#2a78d6; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781; --line:#2c2c2a; --accent:#3987e5; }} }}
:root[data-theme="dark"] {{ --bg:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781; --line:#2c2c2a; --accent:#3987e5; }}
body {{ margin:0; padding:24px 16px 64px; background:var(--bg); color:var(--ink); font-family:{FONT}; line-height:1.45; }}
main {{ max-width: 980px; margin: 0 auto; }}
h1 {{ font-size: 1.5rem; margin: 0 0 4px; }} h2 {{ font-size: 1.15rem; margin: 28px 0 4px; }}
.sub {{ color: var(--ink2); margin: 0 0 12px; }}
.alg {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px 18px; margin: 18px 0; }}
.intro {{ color: var(--ink2); margin: 0 0 10px; }}
.stage {{ overflow-x: auto; }} .stage svg {{ display:block; margin: 0 auto; max-width: 100%; height: auto; }}
.controls {{ display:flex; gap:8px; align-items:center; margin-top: 10px; flex-wrap: wrap; }}
.controls button {{ background: var(--surface); color: var(--ink); border:1px solid var(--line); border-radius: 8px; padding: 6px 10px; cursor:pointer; font-size: 14px; }}
.controls button:hover {{ border-color: var(--accent); }}
.controls input[type=range] {{ flex: 1; min-width: 140px; accent-color: var(--accent); }}
.counter {{ color: var(--muted); font-variant-numeric: tabular-nums; min-width: 64px; text-align:right; }}
.note {{ margin-top: 10px; font-size: 15px; min-height: 1.5em; }}
.state {{ margin-top: 6px; color: var(--ink2); font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; white-space: pre-wrap; }}
.legend {{ display:flex; gap:16px; flex-wrap: wrap; color: var(--ink2); font-size: 13px; margin: 8px 0 0; }}
.legend span::before {{ content:''; display:inline-block; width:12px; height:12px; border-radius:50%; margin-right:6px; vertical-align:-1px; border:2px solid transparent; }}
.legend .v::before {{ background:#2a78d6; }} .legend .c::before {{ border-color:#eb6834; }} .legend .f::before {{ border-color:#1baf7a; border-style:dashed; }} .legend .d::before {{ background:#008300; }}
svg {{ {SVG_CSS.replace(chr(10), ' ')} }}
:root[data-theme="dark"] svg .lf-bg, :root:not([data-theme="light"]) svg .lf-bg {{ fill: transparent; }}
"""
    js = """
document.querySelectorAll('section.alg').forEach(sec => {
  const steps = JSON.parse(sec.querySelector('script.steps').textContent);
  const nodes = [...sec.querySelectorAll('.lf-tn')];
  const edges = [...sec.querySelectorAll('.lf-te')];
  const slider = sec.querySelector('input[type=range]');
  const counter = sec.querySelector('.counter');
  const note = sec.querySelector('.note');
  const state = sec.querySelector('.state');
  let i = 0, timer = null;
  function apply(k) {
    i = Math.max(0, Math.min(steps.length - 1, k));
    const s = steps[i];
    const hidden = new Set(s.hidden || []);
    nodes.forEach(n => {
      const id = n.dataset.id;
      n.classList.remove('visited', 'current', 'frontier', 'found', 'hidden');
      if ((s.visited || []).includes(id)) n.classList.add('visited');
      if ((s.frontier || []).includes(id)) n.classList.add('frontier');
      if ((s.current || []).includes(id)) n.classList.add('current');
      if ((s.found || []).includes(id)) n.classList.add('found');
      if (hidden.has(id)) n.classList.add('hidden');
      const ann = n.querySelector('.lf-ann'); if (ann) ann.textContent = (s.labels || {})[id] || '';
      const badge = n.querySelector('.lf-order-badge');
      if (badge) { const o = (s.order || {})[id]; badge.style.display = o ? 'inline' : 'none'; badge.querySelector('text').textContent = o || ''; }
    });
    const act = new Set((s.active_edges || []).map(e => e[0] + '>' + e[1]));
    edges.forEach(e => {
      e.classList.toggle('active', act.has(e.dataset.edge));
      const [a, b] = e.dataset.edge.split('>');
      e.classList.toggle('hidden', hidden.has(a) || hidden.has(b));
    });
    slider.value = i; counter.textContent = i + ' / ' + (steps.length - 1);
    note.textContent = s.note || '';
    state.textContent = Object.entries(s.state || {}).map(([k, v]) => k + ': ' + (Array.isArray(v) ? '[' + v.join(', ') + ']' : v)).join('   ');
    if (i === steps.length - 1 && timer) stop();
  }
  function stop() { clearInterval(timer); timer = null; sec.querySelector('[data-act=play]').textContent = '▶'; }
  function play() { if (timer) return stop(); if (i >= steps.length - 1) apply(0); sec.querySelector('[data-act=play]').textContent = '⏸'; timer = setInterval(() => apply(i + 1), 900); }
  sec.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
    const a = b.dataset.act; if (a === 'first') apply(0); else if (a === 'prev') apply(i - 1); else if (a === 'next') apply(i + 1); else if (a === 'play') play();
  }));
  slider.addEventListener('input', () => { stop(); apply(+slider.value); });
  sec.addEventListener('keydown', ev => { if (ev.key === 'ArrowRight') { apply(i + 1); ev.preventDefault(); } if (ev.key === 'ArrowLeft') { apply(i - 1); ev.preventDefault(); } if (ev.key === ' ') { play(); ev.preventDefault(); } });
  sec.tabIndex = 0;
  apply(0);
});
"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(topic_title)}: tree algorithms, step by step</title><style>{css}</style></head>
<body><main>
<h1>{escape(topic_title)}: tree algorithms, step by step</h1>
<p class="sub">Use ◀ ▶ or the arrow keys. Each frame shows which node is being examined, what has been recorded, and what is waiting.</p>
<div class="legend"><span class="v">visited / recorded</span><span class="c">current</span><span class="f">waiting (stack / queue)</span><span class="d">found / inserted</span></div>
{''.join(parts)}
</main><script>{js}</script></body></html>"""
