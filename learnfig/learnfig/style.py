"""Textbook figure style tokens (light theme in SVG, dark overrides in HTML).

Colors follow a validated, colorblind-safe palette: blue/red is the diverging
pair for polarity (+ / −), categorical slots mark variable kinds.
"""
from __future__ import annotations

FONT = 'system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif'
MONO = 'ui-monospace, SFMono-Regular, Menlo, monospace'

INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SURFACE = "#fcfcfb"
PAGE = "#f9f9f7"

POS = "#2a78d6"      # positive influence  (blue)
NEG = "#e34948"      # negative influence  (red)
UNK = "#898781"      # unknown sign        (gray)
MID = "#f0efec"      # diverging midpoint

KIND_COLORS = {
    "parameter": "#eb6834",  # orange  - what you choose
    "state": "#2a78d6",      # blue    - what evolves
    "derived": "#1baf7a",    # aqua    - what follows
    "constant": "#898781",   # gray    - what is fixed
    "external": "#4a3aa7",   # violet  - what comes from outside
}
KIND_LABELS = {
    "parameter": "parameter (chosen)",
    "state": "state (evolves)",
    "derived": "derived (computed)",
    "constant": "constant",
    "external": "external input",
}

SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
       "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]

TREE = {"node": "#fcfcfb", "stroke": "#52514e", "visited": "#2a78d6", "current": "#eb6834",
        "frontier": "#1baf7a", "found": "#008300", "edge": "#c3c2b7"}


def _hex(c: str) -> tuple:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ra, rb = _hex(a), _hex(b)
    return "#%02x%02x%02x" % tuple(round(ra[i] + (rb[i] - ra[i]) * t) for i in range(3))


def diverging(v: float, vmax: float) -> str:
    """Red (negative: opposite direction) ↔ neutral ↔ blue (positive: same direction).

    Same convention as the arrows of the variable graph and the cascade markers."""
    if vmax <= 0:
        return MID
    t = max(-1.0, min(1.0, v / vmax))
    return mix(MID, NEG, -t) if t < 0 else mix(MID, POS, t)


def luminance(c: str) -> float:
    r, g, b = (x / 255 for x in _hex(c))

    def lin(u: float) -> float:
        return u / 12.92 if u <= 0.03928 else ((u + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def ink_on(c: str) -> str:
    return INK if luminance(c) > 0.35 else "#ffffff"


SVG_CSS = f"""
  .lf {{ font-family: {FONT}; }}
  .lf-title {{ font-size: 13px; font-weight: 600; fill: {INK}; }}
  .lf-label {{ font-size: 12.5px; font-weight: 600; fill: {INK}; }}
  .lf-sub {{ font-size: 10.5px; fill: {INK2}; }}
  .lf-muted {{ font-size: 10.5px; fill: {MUTED}; }}
  .lf-mono {{ font-family: {MONO}; font-size: 10.5px; fill: {INK2}; }}
  .lf-node {{ fill: {SURFACE}; stroke-width: 1.5; }}
  .lf-edge {{ fill: none; stroke-width: 1.8; }}
  .lf-edge-pos {{ stroke: {POS}; }}
  .lf-edge-neg {{ stroke: {NEG}; }}
  .lf-edge-unk {{ stroke: {UNK}; stroke-dasharray: 4 3; }}
  .lf-edge-back {{ stroke-dasharray: 6 4; }}
  .lf-badge {{ stroke: {SURFACE}; stroke-width: 1.5; }}
  .lf-badge-text {{ font-size: 11px; font-weight: 700; fill: #ffffff; text-anchor: middle; dominant-baseline: central; }}
  .lf-callout {{ font-size: 10px; fill: {INK2}; }}
  .lf-grid {{ stroke: {GRID}; stroke-width: 1; }}
  .lf-axis {{ stroke: {AXIS}; stroke-width: 1; }}
  .lf-series {{ fill: none; stroke: {POS}; stroke-width: 2; stroke-linejoin: round; }}
  .lf-event {{ stroke: #eb6834; stroke-width: 1.2; stroke-dasharray: 3 3; }}
  .lf-event-label {{ font-size: 10.5px; fill: #b8491e; font-weight: 600; }}
  .lf-tn circle {{ fill: {TREE['node']}; stroke: {TREE['stroke']}; stroke-width: 1.5; }}
  .lf-tn text {{ font-size: 12px; font-weight: 600; fill: {INK}; text-anchor: middle; dominant-baseline: central; }}
  .lf-tn.visited circle {{ fill: {TREE['visited']}; stroke: {TREE['visited']}; }}
  .lf-tn.visited text {{ fill: #ffffff; }}
  .lf-tn.current circle {{ stroke: {TREE['current']}; stroke-width: 3.5; }}
  .lf-tn.frontier circle {{ stroke: {TREE['frontier']}; stroke-dasharray: 4 2; stroke-width: 2.5; }}
  .lf-tn.found circle {{ fill: {TREE['found']}; stroke: {TREE['found']}; }}
  .lf-tn.found text {{ fill: #ffffff; }}
  .lf-tn.hidden, .lf-te.hidden {{ opacity: 0; }}
  .lf-tn.dim circle {{ opacity: 0.35; }}
  .lf-te {{ stroke: {TREE['edge']}; stroke-width: 1.6; fill: none; }}
  .lf-te.active {{ stroke: {TREE['current']}; stroke-width: 2.4; }}
  .lf-te-label {{ font-size: 10px; fill: {INK2}; text-anchor: middle; }}
  .lf-order {{ font-size: 9.5px; fill: #ffffff; font-weight: 700; text-anchor: middle; dominant-baseline: central; }}
  .lf-order-bg {{ fill: {INK2}; }}
  .lf-ann {{ font-size: 10px; fill: #b8491e; font-weight: 700; text-anchor: middle; }}
  .lf-cell-text {{ font-size: 10.5px; text-anchor: middle; dominant-baseline: central; }}
  .lf-legend {{ font-size: 10.5px; fill: {INK2}; }}
"""
