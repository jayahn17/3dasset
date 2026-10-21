"""Variable-interaction graph: every variable is a node, every influence an
arrow whose color and badge give the sign of the effect.  Numbered callouts
link arrows to the mechanism key printed under the figure.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..graph import Graph, loop_polarity
from ..model import Topic
from ..style import KIND_COLORS, KIND_LABELS, NEG, POS, UNK, INK2
from ..svg import SVG, bezier_point, fmt, text_width
from ..units import pretty_unit

NODE_H = 50
X_GAP = 120
Y_GAP = 28
MARGIN = 28


def _node_width(topic: Topic, vid: str) -> float:
    v = topic.var(vid)
    label = v.label if v else vid
    sub = _subtitle(topic, vid)
    w = max(text_width(label, 12.5, bold=True), text_width(sub, 10.5)) + 28
    return max(118.0, min(240.0, w))


def _subtitle(topic: Topic, vid: str, values: Optional[Dict[str, float]] = None) -> str:
    v = topic.var(vid)
    if v is None:
        return ""
    unit = pretty_unit(v.unit)
    val = values.get(vid) if values else (v.default if v.default is not None else None)
    sym = v.symbol if v.symbol and v.symbol != v.name else ""
    if val is None:
        return "  ".join(x for x in (sym, unit) if x)
    num = f"{fmt(val)} {unit}".strip()
    if not sym:
        return num
    return f"{sym} = {num}" if (v.kind == "derived" or v.equation) else f"{sym} · {num}"


def render_variable_graph(topic: Topic, values: Optional[Dict[str, float]] = None,
                          loops: Optional[List[dict]] = None, options: Optional[dict] = None,
                          standalone: bool = True) -> Tuple[str, List[dict]]:
    """Return (svg, key) where key lists numbered mechanisms for the caption."""
    options = options or {}
    show_values = options.get("show_values", True)
    ids = topic.var_ids
    rels = [r for r in topic.relations if r.source in ids and r.target in ids]
    linked = {n for r in rels for n in (r.source, r.target)}
    ids = [n for n in ids if n in linked or topic.var(n).kind != "constant"]
    if not ids:
        return SVG(320, 80, "empty").tostring(standalone), []
    g = Graph(ids, [r.key for r in rels])
    layers, back = g.layers()

    # geometry
    widths = {n: _node_width(topic, n) for n in ids}
    layer_w = [max(widths[n] for n in L) if L else 120 for L in layers]
    xs: List[float] = []
    x = MARGIN
    for w in layer_w:
        xs.append(x)
        x += w + X_GAP
    total_w = x - X_GAP + MARGIN
    max_rows = max(len(L) for L in layers)
    body_h = max_rows * NODE_H + (max_rows - 1) * Y_GAP
    has_back = bool(back) or any(r.source == r.target for r in rels)
    top = MARGIN + (44 if has_back else 0)
    legend_h = 34
    total_h = top + body_h + MARGIN + legend_h
    pos: Dict[str, Tuple[float, float]] = {}
    for li, L in enumerate(layers):
        h = len(L) * NODE_H + (len(L) - 1) * Y_GAP
        y0 = top + (body_h - h) / 2
        for ri, n in enumerate(L):
            pos[n] = (xs[li] + (layer_w[li] - widths[n]) / 2, y0 + ri * (NODE_H + Y_GAP))

    svg = SVG(total_w, total_h, f"{topic.title}: how the variables affect each other")
    m_pos = svg.marker("arr-pos", POS)
    m_neg = svg.marker("arr-neg", NEG)
    m_unk = svg.marker("arr-unk", UNK)

    key: List[dict] = []
    # edges first (under nodes)
    for r in rels:
        sx, sy = pos[r.source]
        tx, ty = pos[r.target]
        sw, tw = widths[r.source], widths[r.target]
        cls_sign = {"+": "lf-edge-pos", "-": "lf-edge-neg"}.get(r.sign, "lf-edge-unk")
        marker = {"+": m_pos, "-": m_neg}.get(r.sign, m_unk)
        color = {"+": POS, "-": NEG}.get(r.sign, UNK)
        number = None
        if r.mechanism:
            key.append({"n": len(key) + 1, "source": r.source, "target": r.target, "sign": r.sign,
                        "mechanism": r.mechanism, "kind": r.kind})
            number = len(key)
        title = f"{topic.var(r.source).label} → {topic.var(r.target).label} ({r.sign})"
        if r.mechanism:
            title += f": {r.mechanism}"
        if r.source == r.target:
            cx, cy = sx + sw - 14, sy
            d = f"M {cx - 10:.1f} {cy:.1f} C {cx - 10:.1f} {cy - 34:.1f} {cx + 26:.1f} {cy - 34:.1f} {cx + 14:.1f} {cy - 2:.1f}"
            svg.path(d, cls=f"lf-edge {cls_sign}", extra=f'marker-end="url(#{marker})"', title=title)
            bx, by = cx + 8, cy - 26
        elif (r.source, r.target) in back:
            # feedback: arc over the top, dashed
            p0 = (sx + sw / 2, sy)
            p3 = (tx + tw / 2, ty)
            lift = 46 + 10 * (abs(layers_index(layers, r.source) - layers_index(layers, r.target)))
            p1 = (p0[0], min(p0[1], p3[1]) - lift)
            p2 = (p3[0], min(p0[1], p3[1]) - lift)
            d = f"M {p0[0]:.1f} {p0[1]:.1f} C {p1[0]:.1f} {p1[1]:.1f} {p2[0]:.1f} {p2[1]:.1f} {p3[0]:.1f} {p3[1] - 2:.1f}"
            svg.path(d, cls=f"lf-edge lf-edge-back {cls_sign}", extra=f'marker-end="url(#{marker})"', title=title)
            bx, by = bezier_point(p0, p1, p2, p3, 0.5)
        else:
            p0 = (sx + sw, sy + NODE_H / 2)
            p3 = (tx - 1, ty + NODE_H / 2)
            dx = max(40.0, (p3[0] - p0[0]) / 2)
            p1 = (p0[0] + dx, p0[1])
            p2 = (p3[0] - dx, p3[1])
            d = f"M {p0[0]:.1f} {p0[1]:.1f} C {p1[0]:.1f} {p1[1]:.1f} {p2[0]:.1f} {p2[1]:.1f} {p3[0]:.1f} {p3[1]:.1f}"
            svg.path(d, cls=f"lf-edge {cls_sign}", extra=f'marker-end="url(#{marker})"', title=title)
            bx, by = bezier_point(p0, p1, p2, p3, 0.5)
        # sign badge + callout number
        svg.circle(bx, by, 8, cls="lf-badge", style=f"fill:{color}", title=title)
        glyph = {"+": "+", "-": "−"}.get(r.sign, "?")
        svg.text(bx, by + 0.5, glyph, cls="lf-badge-text", anchor="middle")
        if number is not None:
            svg.text(bx + 11, by - 6, f"({number})", cls="lf-callout")

    # nodes
    for n in ids:
        v = topic.var(n)
        x, y = pos[n]
        w = widths[n]
        color = KIND_COLORS.get(v.kind, UNK)
        desc = v.description or ""
        svg.rect(x, y, w, NODE_H, rx=8, cls="lf-node", style=f"stroke:{color}",
                 title=f"{v.label} [{v.kind}]" + (f": {desc}" if desc else ""),
                 extra=f'data-var="{n}"')
        svg.rect(x, y, 5, NODE_H, rx=2, style=f"fill:{color}")
        svg.text(x + 14, y + 20, v.label, cls="lf-label")
        sub = _subtitle(topic, n, values if show_values else None)
        if sub:
            svg.text(x + 14, y + 37, sub, cls="lf-sub")

    # legend: kinds present + sign meaning
    kinds = list(dict.fromkeys(topic.var(n).kind for n in ids))
    lx, ly = MARGIN, total_h - 14
    for k in kinds:
        svg.rect(lx, ly - 9, 10, 10, rx=2, style=f"fill:{KIND_COLORS.get(k, UNK)}")
        label = KIND_LABELS.get(k, k)
        svg.text(lx + 14, ly, label, cls="lf-legend")
        lx += text_width(label, 10.5) + 30
    for color, label in ((POS, "increases"), (NEG, "decreases")):
        svg.circle(lx + 5, ly - 4, 5, style=f"fill:{color}")
        svg.text(lx + 14, ly, label, cls="lf-legend")
        lx += text_width(label, 10.5) + 30
    if any((r.source, r.target) in back for r in rels):
        svg.line(lx, ly - 4, lx + 18, ly - 4, cls="lf-edge lf-edge-back", style=f"stroke:{INK2}")
        svg.text(lx + 22, ly, "feedback", cls="lf-legend")

    # loop labels in the top band
    if loops:
        lx = MARGIN
        for lp in loops[:6]:
            tag = f"{lp['id']}: {' → '.join(lp['cycle'])} → {lp['cycle'][0]} ({lp['polarity']})"
            svg.text(lx, 16, tag, cls="lf-muted")
            lx += text_width(tag, 10.5) + 24
            if lx > total_w - 200:
                break
    return svg.tostring(standalone), key


def layers_index(layers: List[List[str]], n: str) -> int:
    for i, L in enumerate(layers):
        if n in L:
            return i
    return 0
