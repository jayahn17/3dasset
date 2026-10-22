"""Timeline figure: (A) how each state variable evolves, with the moments
events fire, and (B) the chain reaction a shock sets off, lane by lane.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from ..model import Topic
from ..simulate import CascadeEvent, Trajectory
from ..style import AXIS, INK2, MUTED, NEG, POS, UNK
from ..svg import SVG, fmt, text_width
from ..units import pretty_unit

W = 960
RIGHT = 84   # room for the max/min value labels of each row
BAND_ROW = 13
ROW_H = 66
LANE_H = 40


def _ticks(lo: float, hi: float, n: int = 6) -> List[float]:
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10)), key=lambda s: abs(s - raw))
    t0 = math.ceil(lo / step) * step
    out = []
    t = t0
    while t <= hi + 1e-9:
        out.append(round(t, 10))
        t += step
    return out


def render_timeline(topic: Topic, traj: Optional[Trajectory], events: List[CascadeEvent],
                    options: Optional[dict] = None, standalone: bool = True) -> Tuple[str, dict]:
    options = options or {}
    max_series = int(options.get("max_series", 6))
    meta: dict = {}
    series_ids: List[str] = []
    if traj is not None:
        states = [s for s in topic.state_ids if s in traj.series]
        derived = [v.id for v in topic.variables if v.equation and v.id in traj.series and v.id not in states]
        wanted = options.get("series")
        if wanted:
            series_ids = [s for s in wanted if s in traj.series]
        else:
            series_ids = (states + derived)[:max_series]
    lanes: List[str] = list(dict.fromkeys(e.var for e in events))
    names = [topic.var(v).label if topic.var(v) else v for v in series_ids + lanes]
    LEFT = max(150, min(300, int(max((text_width(n, 12.5, bold=True) for n in names), default=0) + 28)))
    plot_w = W - LEFT - RIGHT
    placements: List[Tuple[object, float, float, int, str]] = []   # (fired, x, label_x, row, label)
    if series_ids and traj is not None and traj.fired:
        t0_, span_ = traj.times[0], ((traj.times[-1] - traj.times[0]) or 1.0)
        row_end: List[float] = []
        for f in sorted(traj.fired, key=lambda f: f.t):
            x = LEFT + (f.t - t0_) / span_ * plot_w
            label = f"{f.event.title}  t={fmt(f.t)}"
            wl = text_width(label, 10.5, bold=True)
            lx = min(x + 4, LEFT + plot_w - wl)          # never into the value-label gutter
            lx = max(lx, LEFT)
            row = next((i for i, end in enumerate(row_end) if lx > end + 8), None)
            if row is None:
                row = len(row_end)
                row_end.append(0.0)
            row_end[row] = lx + wl
            placements.append((f, x, lx, row, label))
    band_h = (max((p[3] for p in placements), default=-1) + 1) * BAND_ROW
    panel_a_h = (len(series_ids) * ROW_H + 70 + band_h) if series_ids else 0
    panel_b_h = (len(lanes) * LANE_H + 86) if lanes else 0
    H = 20 + panel_a_h + (24 if panel_a_h and panel_b_h else 0) + panel_b_h + 10
    svg = SVG(W, max(H, 120), f"{topic.title}: timeline")
    m_gray = svg.marker("arr-gray", AXIS)
    y = 20

    # ---------------- Panel A: trajectories ----------------
    if series_ids:
        svg.text(LEFT, y + 6, "A.  How the state evolves in time", cls="lf-title")
        y += 18
        band_top = y + 4
        y += band_h
        t0, t1 = traj.times[0], traj.times[-1]
        span = (t1 - t0) or 1.0

        def xt(t: float) -> float:
            return LEFT + (t - t0) / span * plot_w

        for sid in series_ids:
            v = topic.var(sid)
            vals = traj.series[sid]
            finite = [x for x in vals if math.isfinite(x)]
            lo, hi = (min(finite), max(finite)) if finite else (0.0, 1.0)
            scale = max(abs(lo), abs(hi), 1e-300)
            if hi - lo <= 1e-6 * scale:  # conserved to numerical precision: show it flat, on an honest scale
                mid = (lo + hi) / 2
                pad = abs(mid) * 0.05 if mid != 0 else 1.0
                lo, hi = mid - pad, mid + pad
            top, bottom = y + 10, y + ROW_H - 12

            def yv(val: float) -> float:
                return bottom - (val - lo) / (hi - lo) * (bottom - top)

            svg.line(LEFT, bottom, LEFT + plot_w, bottom, cls="lf-axis")
            svg.line(LEFT, top, LEFT + plot_w, top, cls="lf-grid")
            name = v.label if v else sid
            svg.text(LEFT - 12, y + ROW_H / 2 - 4, name, cls="lf-label", anchor="end")
            unit = pretty_unit(v.unit) if v else ""
            svg.text(LEFT - 12, y + ROW_H / 2 + 11, f"{v.sym}  {unit}".strip() if v else "", cls="lf-sub", anchor="end")
            svg.text(LEFT + plot_w + 6, top + 4, fmt(hi), cls="lf-muted")
            svg.text(LEFT + plot_w + 6, bottom + 3, fmt(lo), cls="lf-muted")
            pts = []
            for t, val in zip(traj.times, vals):
                if math.isfinite(val):
                    pts.append(f"{xt(t):.1f},{yv(val):.1f}")
            if pts:
                svg.path("M " + " L ".join(pts), cls="lf-series",
                         title=f"{name}: from {fmt(vals[0])} to {fmt(vals[-1])} {unit}")
            # zero line if the range straddles zero
            if lo < 0 < hi:
                svg.line(LEFT, yv(0), LEFT + plot_w, yv(0), cls="lf-grid", style="stroke-dasharray:2 3")
            y += ROW_H
        # event markers across panel A, labels in their own band above the rows
        for f, x, lx, row, label in placements:
            ly = band_top + row * BAND_ROW + 6
            svg.line(x, ly + 3, x, y - 12, cls="lf-event")
            svg.text(lx, ly, label, cls="lf-event-label",
                     title=f.event.description or f"{f.event.title} at t = {fmt(f.t)} {traj.time_unit}")
        # x axis
        for t in _ticks(t0, t1):
            x = xt(t)
            svg.line(x, y - 12, x, y - 8, cls="lf-axis")
            svg.text(x, y + 4, fmt(t), cls="lf-muted", anchor="middle")
        svg.text(LEFT + plot_w, y + 18, f"time ({traj.time_unit})", cls="lf-muted", anchor="end")
        y += 30
        meta["fired"] = [(f.event.title, f.t) for f in traj.fired]

    # ---------------- Panel B: cascade ----------------
    if lanes:
        if series_ids:
            y += 14
        shock_desc = ", ".join(f"{topic.var(e.var).label if topic.var(e.var) else e.var} {e.delta:+.0%}"
                               for e in events if e.depth == 0)
        svg.text(LEFT, y + 6, f"B.  Chain reaction after a shock:  {shock_desc}", cls="lf-title")
        y += 24
        tmax = max((e.t for e in events), default=1.0) or 1.0
        dmax = max((abs(e.delta) for e in events), default=1.0) or 1.0
        lane_y = {lane: y + i * LANE_H + LANE_H / 2 for i, lane in enumerate(lanes)}

        def xc(t: float) -> float:
            return LEFT + 24 + t / tmax * (plot_w - 48)

        groups: Dict[Tuple[str, float], List[int]] = {}
        for e in events:
            groups.setdefault((e.var, round(e.t, 9)), []).append(e.index)
        px: Dict[int, float] = {}
        above: Dict[int, bool] = {}
        for members in groups.values():
            n = len(members)
            for j, idx in enumerate(members):
                px[idx] = xc(events[idx].t) + (j - (n - 1) / 2) * 16
                above[idx] = (j % 2 == 0)
        for i, lane in enumerate(lanes):
            ly = lane_y[lane]
            v = topic.var(lane)
            svg.line(LEFT, ly, LEFT + plot_w, ly, cls="lf-grid")
            svg.text(LEFT - 12, ly + 4, v.label if v else lane, cls="lf-label", anchor="end")
        # arrows (cause → effect) under markers
        for e in events:
            if e.cause is None:
                continue
            c = events[e.cause]
            x1, y1 = px[c.index], lane_y[c.var]
            x2, y2 = px[e.index], lane_y[e.var]
            mech = e.relation.mechanism if e.relation and e.relation.mechanism else ""
            if abs(y2 - y1) < 1:
                d = f"M {x1:.1f} {y1 - 6:.1f} C {x1 + 20:.1f} {y1 - 30:.1f} {x2 - 20:.1f} {y2 - 30:.1f} {x2:.1f} {y2 - 8:.1f}"
            else:
                mx = (x1 + x2) / 2
                d = f"M {x1:.1f} {y1:.1f} C {mx:.1f} {y1:.1f} {mx:.1f} {y2:.1f} {x2 - 2:.1f} {y2:.1f}"
            svg.path(d, cls="lf-edge", style=f"stroke:{AXIS};stroke-width:1.3",
                     extra=f'marker-end="url(#{m_gray})"',
                     title=f"{c.var} {c.delta:+.0%} → {e.var} {e.delta:+.0%}" + (f": {mech}" if mech else ""))
        for e in events:
            x, ly = px[e.index], lane_y[e.var]
            r = 4 + 7 * min(1.0, abs(e.delta) / dmax)
            color = POS if e.delta > 0 else NEG if e.delta < 0 else UNK
            title = (f"t={fmt(e.t)}: {e.var} changes {e.delta:+.1%}" +
                     (f" because {events[e.cause].var} changed {events[e.cause].delta:+.1%}" if e.cause is not None else " (the shock)"))
            if e.depth == 0:
                svg.path(f"M {x:.1f} {ly - r - 2:.1f} L {x + r + 2:.1f} {ly:.1f} L {x:.1f} {ly + r + 2:.1f} L {x - r - 2:.1f} {ly:.1f} Z",
                         style=f"fill:{color};stroke:#fcfcfb;stroke-width:1.5", title=title)
            else:
                svg.circle(x, ly, r, style=f"fill:{color};stroke:#fcfcfb;stroke-width:1.5", title=title)
            ty = ly - r - 5 if above[e.index] else ly + r + 11
            svg.text(x, ty, f"{e.delta:+.0%}", cls="lf-muted", anchor="middle", style=f"fill:{INK2}")
        y += len(lanes) * LANE_H + 6
        svg.line(LEFT, y, LEFT + plot_w, y, cls="lf-axis")
        for t in _ticks(0, tmax, 6):
            svg.line(xc(t), y, xc(t), y + 4, cls="lf-axis")
            svg.text(xc(t), y + 16, fmt(t), cls="lf-muted", anchor="middle")
        unit = topic.cascade.time_unit
        svg.text(LEFT + plot_w, y + 30, f"time after the shock ({unit}); marker size = size of the change, "
                 f"◆ = the shock, ● blue up / red down", cls="lf-muted", anchor="end")
        y += 40
        meta["cascade_events"] = len(events)
        meta["cascade_depth"] = max((e.depth for e in events), default=0)
        meta["cascade_tmax"] = tmax
    return svg.tostring(standalone), meta
