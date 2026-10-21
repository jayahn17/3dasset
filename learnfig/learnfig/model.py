"""Data model for a learning topic.

Everything a figure needs is declared once in a topic spec and reused by
every renderer and every check.  Keep this module dependency-free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

KINDS = ("parameter", "state", "derived", "constant", "external")
SIGNS = ("+", "-", "?", "±")
FIGURE_TYPES = (
    "variable_graph",
    "influence_matrix",
    "timeline",
    "tree",
    "scene3d",
    "spatial",
)


@dataclass
class Variable:
    id: str
    name: str = ""
    symbol: str = ""
    unit: str = "1"
    kind: str = "parameter"
    default: Optional[float] = None
    range: Optional[tuple] = None
    equation: Optional[str] = None
    description: str = ""
    group: str = ""

    @property
    def label(self) -> str:
        return self.name or self.id

    @property
    def sym(self) -> str:
        return self.symbol or self.id


@dataclass
class Relation:
    source: str
    target: str
    sign: str = "?"
    gain: Optional[float] = None
    delay: Optional[float] = None
    mechanism: str = ""
    kind: str = "causal"  # causal | derived | dynamic | constraint
    auto: bool = False

    @property
    def key(self) -> tuple:
        return (self.source, self.target)


@dataclass
class Event:
    id: str
    label: str = ""
    t: Optional[float] = None
    when: Optional[str] = None
    once: bool = True
    sets: dict = field(default_factory=dict)
    shock: dict = field(default_factory=dict)
    stop: bool = False
    description: str = ""

    @property
    def title(self) -> str:
        return self.label or self.id


@dataclass
class Dynamics:
    rates: dict = field(default_factory=dict)
    start: float = 0.0
    end: float = 10.0
    step: float = 0.01
    time_unit: str = "s"
    integrator: str = "rk4"


@dataclass
class Invariant:
    label: str
    expr: str
    tolerance: float = 1e-3


@dataclass
class CascadeSettings:
    shock: dict = field(default_factory=dict)
    default_delay: float = 1.0
    damping: float = 0.6
    threshold: float = 0.02
    max_depth: int = 6
    horizon: Optional[float] = None
    max_events: int = 200
    time_unit: str = "step"


@dataclass
class TreeSpec:
    kind: str = "binary"  # binary | general | decision
    title: str = ""
    root: Optional[dict] = None
    generate: Optional[dict] = None
    algorithms: list = field(default_factory=list)


@dataclass
class FigureRequest:
    type: str
    title: str = ""
    caption: str = ""
    options: dict = field(default_factory=dict)


@dataclass
class Topic:
    id: str
    title: str
    domain: str = ""
    level: str = ""
    chapter: str = ""
    summary: str = ""
    notes: list = field(default_factory=list)
    variables: list = field(default_factory=list)
    relations: list = field(default_factory=list)
    events: list = field(default_factory=list)
    dynamics: Optional[Dynamics] = None
    invariants: list = field(default_factory=list)
    cascade: CascadeSettings = field(default_factory=CascadeSettings)
    tree: Optional[TreeSpec] = None
    figures: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    # -- lookups -----------------------------------------------------------
    @property
    def var_ids(self) -> list:
        return [v.id for v in self.variables]

    def var(self, vid: str) -> Optional[Variable]:
        for v in self.variables:
            if v.id == vid:
                return v
        return None

    def outgoing(self, vid: str) -> list:
        return [r for r in self.relations if r.source == vid]

    def incoming(self, vid: str) -> list:
        return [r for r in self.relations if r.target == vid]

    def relation(self, source: str, target: str) -> Optional[Relation]:
        for r in self.relations:
            if r.source == source and r.target == target:
                return r
        return None

    @property
    def state_ids(self) -> list:
        if self.dynamics:
            return list(self.dynamics.rates.keys())
        return [v.id for v in self.variables if v.kind == "state"]

    @property
    def has_equations(self) -> bool:
        return any(v.equation for v in self.variables)
