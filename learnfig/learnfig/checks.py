"""The science checker: deterministic consistency rules for a topic spec.

Every rule produces Findings (error / warning / info).  Errors mean a figure
would teach something wrong (a dimensionally impossible formula, a sign that
contradicts the equation, an invariant that drifts); warnings mean a figure
would be incomplete or ambiguous.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import expr as E
from . import units as U
from .graph import Graph, loop_polarity
from .model import KINDS, SIGNS, Topic
from .simulate import direct_partial, integrate, invariant_drift, resolve


@dataclass
class Finding:
    level: str  # error | warning | info
    code: str
    message: str
    where: str = ""
    hint: str = ""

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message,
                "where": self.where, "hint": self.hint}


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)
    loops: List[dict] = field(default_factory=list)
    values: Dict[str, float] = field(default_factory=dict)

    def add(self, level: str, code: str, message: str, where: str = "", hint: str = "") -> None:
        self.findings.append(Finding(level, code, message, where, hint))

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "counts": {"error": len(self.errors), "warning": len(self.warnings),
                                          "info": len(self.findings) - len(self.errors) - len(self.warnings)},
                "findings": [f.to_dict() for f in self.findings], "loops": self.loops,
                "values": self.values}

    def to_markdown(self, title: str = "Check report") -> str:
        lines = [f"# {title}", "",
                 f"**{'PASS' if self.ok else 'FAIL'}** — {len(self.errors)} errors, "
                 f"{len(self.warnings)} warnings, "
                 f"{len(self.findings) - len(self.errors) - len(self.warnings)} notes", ""]
        for level in ("error", "warning", "info"):
            items = [f for f in self.findings if f.level == level]
            if not items:
                continue
            lines.append(f"## {level.title()}s")
            for f in items:
                where = f" `{f.where}`" if f.where else ""
                hint = f"  \n  _hint:_ {f.hint}" if f.hint else ""
                lines.append(f"- **{f.code}**{where}: {f.message}{hint}")
            lines.append("")
        if self.loops:
            lines.append("## Feedback loops")
            for lp in self.loops:
                lines.append(f"- {lp['id']} ({lp['polarity']}): {' → '.join(lp['cycle'] + [lp['cycle'][0]])}")
            lines.append("")
        return "\n".join(lines)


def run_checks(topic: Topic, simulate: bool = True) -> Report:
    rep = Report()
    _check_structure(topic, rep)
    _check_units(topic, rep)
    env = _check_values(topic, rep)
    _check_signs(topic, rep, env)
    _check_graph(topic, rep)
    _check_events(topic, rep)
    _check_tree(topic, rep)
    if simulate and topic.dynamics:
        _check_dynamics(topic, rep)
    return rep


# --------------------------------------------------------------------------

def _check_structure(topic: Topic, rep: Report) -> None:
    ids = topic.var_ids
    for k in topic.meta.get("unknown_keys", []):
        rep.add("warning", "unknown-key", f"unrecognised key {k}; in YAML flow style {{...}} a comma inside an "
                "expression splits the value — quote it", k.split("].")[0] + "]")
    dupes = {i for i in ids if ids.count(i) > 1}
    for d in dupes:
        rep.add("error", "duplicate-id", f"variable id {d!r} is declared more than once", d)
    if not ids:
        rep.add("info" if topic.tree else "warning", "no-variables",
                "topic declares no variables; only tree figures are possible")
    for v in topic.variables:
        if v.kind not in KINDS:
            rep.add("warning", "unknown-kind", f"kind {v.kind!r} is not one of {KINDS}", v.id)
        if not v.name:
            rep.add("info", "no-name", "variable has no display name; the id will be shown", v.id)
        if v.equation:
            try:
                for n in E.free_names(v.equation):
                    if n not in ids and n != "t":
                        rep.add("error", "unknown-name", f"equation uses undefined name {n!r}", v.id,
                                "declare it as a variable or fix the typo")
            except E.ExprError as exc:
                rep.add("error", "bad-equation", str(exc), v.id)
        if v.range and v.default is not None and not (v.range[0] <= v.default <= v.range[1]):
            rep.add("warning", "default-out-of-range",
                    f"default {v.default:g} is outside range {list(v.range)}", v.id)
    for r in topic.relations:
        for end in (r.source, r.target):
            if end not in ids:
                rep.add("error", "dangling-relation", f"relation {r.source}→{r.target} references unknown variable {end!r}",
                        f"{r.source}->{r.target}")
        if r.sign not in SIGNS:
            rep.add("warning", "bad-sign", f"sign {r.sign!r} should be one of {SIGNS}", f"{r.source}->{r.target}")
        if not r.auto and r.sign == "?":
            rep.add("warning", "unknown-sign", "declared relation has no sign; the figure cannot show direction of effect",
                    f"{r.source}->{r.target}", "add sign: '+' or '-'")
    if topic.dynamics:
        for s, rate in topic.dynamics.rates.items():
            if s not in ids:
                rep.add("error", "unknown-state", f"rate law given for undeclared variable {s!r}", s)
            try:
                for n in E.free_names(rate):
                    if n not in ids and n != "t":
                        rep.add("error", "unknown-name", f"rate law of {s} uses undefined name {n!r}", s)
            except E.ExprError as exc:
                rep.add("error", "bad-rate", str(exc), s)
        if topic.dynamics.step <= 0 or topic.dynamics.end <= topic.dynamics.start:
            rep.add("error", "bad-time-grid", "dynamics.time needs step > 0 and end > start")


def _unit_env(topic: Topic, rep: Report) -> Dict[str, U.Unit]:
    env: Dict[str, U.Unit] = {}
    for v in topic.variables:
        try:
            env[v.id] = U.parse_unit(v.unit)
        except U.UnitError as exc:
            rep.add("error", "bad-unit", f"cannot parse unit {v.unit!r}: {exc}", v.id)
            env[v.id] = U.DIMLESS
        if v.unit in ("1", "", None) and v.kind != "derived":
            rep.add("info", "dimensionless", "variable is dimensionless; fine for ratios and counts, "
                    "suspicious for physical quantities", v.id)
    if topic.dynamics:
        try:
            env["t"] = U.parse_unit(topic.dynamics.time_unit)
        except U.UnitError:
            env["t"] = U.DIMLESS
    return env


def _check_units(topic: Topic, rep: Report) -> None:
    uenv = _unit_env(topic, rep)
    for v in topic.variables:
        if not v.equation:
            continue
        notes: List[str] = []
        try:
            got = U.unit_of_expression(v.equation, uenv, notes)
        except (U.UnitError, E.ExprError) as exc:
            rep.add("error", "dimension-inconsistent", str(exc), v.id,
                    "every term that is added or compared must have the same dimensions")
            continue
        want = uenv.get(v.id, U.DIMLESS)
        if got.wild and not want.dimensionless:
            rep.add("warning", "unit-unverifiable", "equation is a bare constant; declared unit cannot be checked", v.id)
        elif not got.same_dims(want):
            rep.add("error", "unit-mismatch",
                    f"equation has dimensions {got.dims_str()} but unit {v.unit!r} is {want.dims_str()}", v.id,
                    "fix the formula or the declared unit")
        elif abs(math.log10(max(got.factor, 1e-300) / max(want.factor, 1e-300))) > 1e-9 and not got.wild:
            rep.add("info", "unit-scale",
                    f"dimensions agree but the unit scale differs by ×{got.factor / want.factor:g}; fine if the "
                    f"formula converts explicitly (e.g. R/1000 for mm → m), a bug otherwise", v.id)
        for n in notes:
            rep.add("info", "implicit-unit", n, v.id)
    if topic.dynamics:
        tu = uenv.get("t", U.DIMLESS)
        for s, rate in topic.dynamics.rates.items():
            if s not in uenv:
                continue
            notes = []
            try:
                got = U.unit_of_expression(rate, uenv, notes)
            except (U.UnitError, E.ExprError) as exc:
                rep.add("error", "dimension-inconsistent", f"rate of {s}: {exc}", s)
                continue
            want = uenv[s] / tu
            if not got.wild and not got.same_dims(want):
                rep.add("error", "rate-unit-mismatch",
                        f"d{s}/dt has dimensions {got.dims_str()} but should be {want.dims_str()}", s,
                        f"the rate law must have units of ({topic.var(s).unit})/({topic.dynamics.time_unit})")
            for n in notes:
                rep.add("info", "implicit-unit", n, s)
    for inv in topic.invariants:
        try:
            U.unit_of_expression(inv.expr, uenv, [])
        except (U.UnitError, E.ExprError) as exc:
            rep.add("error", "dimension-inconsistent", f"invariant {inv.label!r}: {exc}", inv.label)


def _check_values(topic: Topic, rep: Report) -> Dict[str, float]:
    env, problems = resolve(topic)
    for p in problems:
        vid = p.split(":")[0]
        rep.add("warning", "unresolved", p, vid, "give every input a default so figures can show numbers")
    for k, v in env.items():
        if isinstance(v, float) and not math.isfinite(v):
            rep.add("error", "non-finite", f"evaluates to {v} at the defaults", k)
    rep.values = {k: float(v) for k, v in env.items() if isinstance(v, (int, float)) and math.isfinite(v)}
    for v in topic.variables:
        if v.default is None and v.kind in ("parameter", "constant", "state") and not v.equation:
            rep.add("warning", "no-default", "input variable has no default value", v.id)
    return env


def _check_signs(topic: Topic, rep: Report, env: Dict[str, float]) -> None:
    for r in topic.relations:
        if r.auto:
            continue
        p = direct_partial(topic, r.source, r.target, env)
        if p is None or math.isnan(p):
            continue
        numeric = "+" if p > 1e-12 else "-" if p < -1e-12 else "0"
        if numeric == "0":
            rep.add("info", "flat-sign", f"declared {r.sign} but the formula is locally flat at the defaults",
                    f"{r.source}->{r.target}")
        elif r.sign in ("+", "-") and numeric != r.sign:
            rep.add("error", "sign-contradiction",
                    f"declared sign {r.sign} but ∂{r.target}/∂{r.source} = {p:.3g} at the defaults",
                    f"{r.source}->{r.target}", "the formula wins; fix the declared sign or the equation")
        elif r.sign == "?":
            rep.add("info", "sign-inferred", f"sign inferred as {numeric} from the formula", f"{r.source}->{r.target}")
            r.sign = numeric


def _check_graph(topic: Topic, rep: Report) -> None:
    if not topic.variables:
        return
    g = Graph(topic.var_ids, [r.key for r in topic.relations])
    connected = {n for e in g.edges for n in e}
    referenced = set()
    for inv in topic.invariants:
        try:
            referenced |= set(E.free_names(inv.expr))
        except E.ExprError:
            pass
    for ev in topic.events:
        if ev.when:
            try:
                referenced |= set(E.free_names(ev.when))
            except E.ExprError:
                pass
        referenced |= set(ev.sets) | set(ev.shock)
    for v in topic.variables:
        if v.id in referenced and v.id not in connected:
            continue  # used by an invariant or event: legitimately outside the causal graph
        if v.id not in connected and len(topic.variables) > 1:
            rep.add("warning", "isolated", "variable is not connected to anything; it will float in the graph", v.id,
                    "add a relation, an equation that uses it, or remove it")
    sign_of = {r.key: r.sign for r in topic.relations}
    for i, cyc in enumerate(g.simple_cycles(), start=1):
        pol = loop_polarity(cyc, sign_of)
        label = {"reinforcing": "R", "balancing": "B", "unknown": "L"}[pol] + str(i)
        rep.loops.append({"id": label, "cycle": cyc, "polarity": pol})
        if pol == "unknown":
            rep.add("info", "loop-polarity-unknown", f"loop {label} {' → '.join(cyc)} has an unsigned link", label)
    if len(rep.loops) == 0 and len(topic.relations) > 3 and not topic.has_equations:
        rep.add("info", "no-feedback", "no feedback loops: the system is a pure chain/tree of causes")


def _check_events(topic: Topic, rep: Report) -> None:
    ids = set(topic.var_ids) | {"t"}
    for ev in topic.events:
        if ev.t is None and not ev.when and not ev.shock:
            rep.add("warning", "event-never-fires", "event has neither t, when nor shock", ev.id)
        if ev.when:
            try:
                for n in E.free_names(ev.when):
                    if n not in ids:
                        rep.add("error", "unknown-name", f"event condition uses undefined name {n!r}", ev.id)
            except E.ExprError as exc:
                rep.add("error", "bad-condition", str(exc), ev.id)
        for k in list(ev.sets) + list(ev.shock):
            if k not in ids:
                rep.add("error", "unknown-name", f"event touches undefined variable {k!r}", ev.id)
    for k in topic.cascade.shock:
        if k not in ids:
            rep.add("error", "unknown-name", f"cascade shock targets undefined variable {k!r}", "cascade")


def _check_tree(topic: Topic, rep: Report) -> None:
    if not topic.tree:
        return
    from .trees import build_tree, trace, ALGORITHMS
    try:
        root = build_tree(topic.tree)
    except Exception as exc:  # noqa: BLE001 - surface any builder failure as a finding
        rep.add("error", "bad-tree", f"cannot build tree: {exc}", "tree")
        return
    for alg in topic.tree.algorithms:
        name = alg.get("name") if isinstance(alg, dict) else alg
        if name not in ALGORITHMS:
            rep.add("error", "unknown-algorithm", f"unknown tree algorithm {name!r}; known: {sorted(ALGORITHMS)}",
                    "tree")
            continue
        try:
            steps = trace(root, alg if isinstance(alg, dict) else {"name": alg}, topic.tree.kind)
            if not steps:
                rep.add("warning", "empty-trace", f"algorithm {name} produced no steps", "tree")
        except Exception as exc:  # noqa: BLE001
            rep.add("error", "algorithm-failed", f"{name}: {exc}", "tree")
    if topic.tree.kind == "binary" and root is not None:
        from .trees import is_bst
        if any((a.get("name") if isinstance(a, dict) else a) in ("bst_search", "bst_insert")
               for a in topic.tree.algorithms) and not is_bst(root):
            rep.add("error", "not-a-bst", "bst_* algorithms requested but the tree violates the BST ordering", "tree")


def _check_dynamics(topic: Topic, rep: Report) -> None:
    try:
        traj = integrate(topic)
    except Exception as exc:  # noqa: BLE001
        rep.add("error", "integration-failed", str(exc), "dynamics")
        return
    for p in traj.problems:
        rep.add("warning", "dynamics", p, "dynamics")
    for name, vals in traj.series.items():
        if any(not math.isfinite(v) for v in vals):
            rep.add("error", "blow-up", "trajectory becomes non-finite; shrink the time step or fix the rate law", name)
        elif max(abs(v) for v in vals) > 1e12:
            rep.add("warning", "huge-values", "trajectory exceeds 1e12; probably unstable", name)
    for label, drift, ok in invariant_drift(topic, traj):
        if math.isnan(drift):
            rep.add("error", "invariant-unevaluable", f"invariant {label!r} could not be evaluated", label)
        elif ok:
            rep.add("info", "invariant-ok", f"{label} conserved to {drift:.2e} relative drift", label)
        else:
            rep.add("error", "invariant-violated", f"{label} drifts by {drift:.2%} over the simulation", label,
                    "a conserved quantity is not conserved: check the rate laws, or the step size")
    once_ids = {e.id for e in topic.events if e.once and (e.t is not None or e.when)}
    fired = {f.event.id for f in traj.fired}
    for eid in once_ids - fired:
        rep.add("warning", "event-not-fired", "event never fired within the simulated window", eid,
                "extend dynamics.time.end or revisit the condition")
