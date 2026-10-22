"""Influence matrix: rows are causes, columns are effects, cells show how
strongly (and in which direction) a 1 % change in the row moves the column.
Diverging blue (negative) / red (positive) scale with a neutral midpoint.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..model import Topic
from ..style import MID, NEG, POS, diverging, ink_on
from ..svg import SVG, fmt, text_width

CELL = 46


def render_influence_matrix(topic: Topic, rows: List[str], cols: List[str],
                            values: Dict[Tuple[str, str], float], mode: str,
                            standalone: bool = True) -> str:
    rows = [r for r in rows if any(abs(values.get((r, c), 0.0)) > 1e-9 for c in cols if c != r)]
    cols = [c for c in cols if any(abs(values.get((r, c), 0.0)) > 1e-9 for r in rows if r != c)]
    if not rows or not cols:
        return SVG(320, 60, "empty").tostring(standalone)
    labels = {v.id: v.sym if len(v.sym) <= 10 else v.label for v in topic.variables}
    row_lab_w = max(text_width(labels.get(r, r), 11.5) for r in rows) + 24
    col_lab_h = max(text_width(labels.get(c, c), 11.5) for c in cols) * 0.72 + 28
    left = row_lab_w + 12
    top = col_lab_h + 10
    legend_w = 190
    w = left + len(cols) * CELL + legend_w + 24
    bar_h_ = min(len(rows) * CELL, 160)
    legend_bottom = top + bar_h_ + 22 + 13 * 2
    h = max(top + len(rows) * CELL, legend_bottom) + 34
    svg = SVG(w, h, f"{topic.title}: influence matrix")
    vmax = max((abs(v) for v in values.values()), default=1.0) or 1.0

    for j, c in enumerate(cols):
        cx = left + j * CELL + CELL / 2
        svg.text(cx, top - 10, labels.get(c, c), cls="lf-sub", anchor="start",
                 extra=f'transform="rotate(-42 {cx:.1f} {top - 10:.1f})"')
    for i, r in enumerate(rows):
        cy = top + i * CELL + CELL / 2
        svg.text(left - 10, cy + 4, labels.get(r, r), cls="lf-sub", anchor="end")
        for j, c in enumerate(cols):
            x = left + j * CELL
            y = top + i * CELL
            v = values.get((r, c))
            if v is None or (r == c):
                svg.rect(x + 1, y + 1, CELL - 2, CELL - 2, rx=4, style=f"fill:{MID};opacity:0.5")
                continue
            color = diverging(v, vmax)
            what = ("elasticity: % change in column per 1 % change in row" if mode == "elasticity"
                    else "declared sign × gain")
            svg.rect(x + 1, y + 1, CELL - 2, CELL - 2, rx=4, style=f"fill:{color}",
                     title=f"{labels.get(r, r)} → {labels.get(c, c)}: {fmt(v)}  ({what})")
            svg.text(x + CELL / 2, y + CELL / 2, fmt(v, 2), cls="lf-cell-text", anchor="middle",
                     style=f"fill:{ink_on(color)}")

    # legend bar
    lx = left + len(cols) * CELL + 28
    ly = top
    bar_h = min(len(rows) * CELL, 160)
    steps = 24
    for k in range(steps):
        t = 1 - 2 * k / (steps - 1)
        svg.rect(lx, ly + k * bar_h / steps, 14, bar_h / steps + 0.5, style=f"fill:{diverging(t * vmax, vmax)}")
    svg.text(lx + 20, ly + 8, f"+{fmt(vmax, 2)}", cls="lf-muted")
    svg.text(lx + 20, ly + bar_h / 2 + 4, "0", cls="lf-muted")
    svg.text(lx + 20, ly + bar_h, f"−{fmt(vmax, 2)}", cls="lf-muted")
    lines = (["% change in effect", "per 1 % change in cause"] if mode == "elasticity" else ["declared sign × gain"])
    for k, line in enumerate(lines):
        svg.text(lx, ly + bar_h + 22 + 13 * k, line, cls="lf-muted")
    svg.text(left, h - 14, "rows: cause   →   columns: effect.  Blue = rises with the cause, red = falls (as in the graph).",
             cls="lf-muted")
    return svg.tostring(standalone)
