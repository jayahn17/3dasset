"""Minimal SVG builder with textbook defaults (no third-party dependency)."""
from __future__ import annotations

from typing import List, Optional
from xml.sax.saxutils import escape

from .style import SVG_CSS


def text_width(s: str, size: float = 12.0, bold: bool = False) -> float:
    """Rough width estimate for a system sans string."""
    narrow = sum(1 for ch in s if ch in "iljtfI.,:;'|!1 ")
    wide = sum(1 for ch in s if ch.isupper() or ch in "mwMW@")
    base = (len(s) - narrow - wide) * 0.54 + narrow * 0.3 + wide * 0.72
    return base * size * (1.06 if bold else 1.0)


def fmt(x: float, digits: int = 3) -> str:
    if x is None:
        return ""
    if x == 0:
        return "0"
    ax = abs(x)
    if ax >= 1e6 or ax < 1e-3:
        s = f"{x:.{max(digits - 1, 1)}e}"
        mant, _, exp = s.partition("e")
        return f"{mant.rstrip('0').rstrip('.')}e{int(exp)}"
    if ax >= 1000 and float(x).is_integer():
        return str(int(x))
    return f"{x:.{digits if ax < 1000 else 4}g}"


class SVG:
    def __init__(self, width: float, height: float, title: str = ""):
        self.width = width
        self.height = height
        self.title = title
        self.parts: List[str] = []
        self.defs: List[str] = []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def marker(self, mid: str, color: str) -> str:
        self.defs.append(
            f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/></marker>')
        return mid

    def rect(self, x, y, w, h, rx=0, cls="", style="", title="", extra="") -> None:
        t = f"<title>{escape(title)}</title>" if title else ""
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
                 f'class="{cls}" style="{style}" {extra}>{t}</rect>')

    def circle(self, cx, cy, r, cls="", style="", title="", extra="") -> None:
        t = f"<title>{escape(title)}</title>" if title else ""
        self.add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" class="{cls}" style="{style}" {extra}>{t}</circle>')

    def line(self, x1, y1, x2, y2, cls="", style="", extra="") -> None:
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" class="{cls}" style="{style}" {extra}/>')

    def path(self, d: str, cls="", style="", title="", extra="") -> None:
        t = f"<title>{escape(title)}</title>" if title else ""
        self.add(f'<path d="{d}" class="{cls}" style="{style}" {extra}>{t}</path>')

    def text(self, x, y, s: str, cls="lf-label", anchor="start", style="", extra="", title="") -> None:
        t = f"<title>{escape(title)}</title>" if title else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}" style="{style}" {extra}>'
                 f'{escape(str(s))}{t}</text>')

    def group(self, inner: str, cls="", extra="") -> None:
        self.add(f'<g class="{cls}" {extra}>{inner}</g>')

    def open_group(self, cls="", extra="") -> None:
        self.add(f'<g class="{cls}" {extra}>')

    def close_group(self) -> None:
        self.add("</g>")

    def tostring(self, standalone: bool = True) -> str:
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" class="lf" width="{self.width:.0f}" '
                f'height="{self.height:.0f}" viewBox="0 0 {self.width:.0f} {self.height:.0f}" role="img" '
                f'font-family="system-ui, sans-serif">')
        title = f"<title>{escape(self.title)}</title>" if self.title else ""
        style = f"<style>{SVG_CSS}</style>" if standalone else ""
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        bg = f'<rect width="100%" height="100%" fill="#fcfcfb" class="lf-bg"/>' if standalone else ""
        return head + title + style + defs + bg + "".join(self.parts) + "</svg>"


def bezier_point(p0, p1, p2, p3, t: float):
    mt = 1 - t
    x = mt ** 3 * p0[0] + 3 * mt ** 2 * t * p1[0] + 3 * mt * t ** 2 * p2[0] + t ** 3 * p3[0]
    y = mt ** 3 * p0[1] + 3 * mt ** 2 * t * p1[1] + 3 * mt * t ** 2 * p2[1] + t ** 3 * p3[1]
    return x, y
