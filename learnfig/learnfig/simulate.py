"""Numerical analysis of a topic: values, sensitivities, time integration and
chain-reaction (cascade) propagation.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import expr as E
from .model import Event, Relation, Topic


# --------------------------------------------------------------------------
# values & sensitivities
# --------------------------------------------------------------------------

def base_env(topic: Topic, overrides: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    env: Dict[str, Any] = {v.id: v.default for v in topic.variables if v.default is not None}
    if overrides:
        env.update(overrides)
    return env


def resolve(topic: Topic, overrides: Optional[Dict[str, float]] = None,
            env: Optional[Dict[str, Any]] = None, skip: Optional[set] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Evaluate every derived variable in dependency order.

    A ``state`` variable with an equation uses it as its initial condition;
    pass those ids in ``skip`` while integrating so they are not overwritten.
    Returns (env, problems).  Variables whose inputs are unavailable or whose
    equation fails are listed in problems and left out of env.
    """
    env = dict(env) if env is not None else base_env(topic, overrides)
    if overrides and env is not None:
        env.update(overrides)
    skip = skip or set()
    pending = [v for v in topic.variables if v.equation and v.id not in skip]
    problems: List[str] = []
    progress = True
    while pending and progress:
        progress = False
        for v in list(pending):
            try:
                names = E.free_names(v.equation)
            except E.ExprError as exc:
                problems.append(f"{v.id}: {exc}")
                pending.remove(v)
                continue
            if all(n in env and env[n] is not None for n in names if n != "t"):
                try:
                    env[v.id] = float(E.evaluate(v.equation, dict(env, t=env.get("t", 0.0))))
                    pending.remove(v)
                    progress = True
                except (E.ExprError, ArithmeticError, TypeError, ValueError) as exc:
                    problems.append(f"{v.id}: {exc}")
                    pending.remove(v)
    for v in pending:
        missing = [n for n in E.free_names(v.equation) if n not in env or env[n] is None]
        problems.append(f"{v.id}: missing inputs {missing}")
    return env, problems


def direct_partial(topic: Topic, source: str, target: str,
                   env: Optional[Dict[str, Any]] = None, kind: Optional[str] = None) -> Optional[float]:
    """d(target)/d(source) holding the other inputs of target's formula fixed.

    ``kind="derived"`` uses only the target's equation (for a state: its initial
    condition), ``kind="dynamic"`` only its rate law; ``None`` sums both.
    """
    v = topic.var(target)
    if env is None:
        env, _ = resolve(topic)
    exprs: List[str] = []
    try:
        if kind != "dynamic" and v is not None and v.equation and source in E.free_names(v.equation):
            exprs.append(v.equation)
        if kind != "derived" and topic.dynamics and target in topic.dynamics.rates:
            rate = topic.dynamics.rates[target]
            if source in E.free_names(rate):
                exprs.append(rate)
    except E.ExprError:
        return None
    if not exprs or source not in env or env[source] is None:
        return None
    local = dict(env, t=env.get("t", 0.0))
    try:
        vals = [E.partial(x, local, source) for x in exprs]
    except (E.ExprError, KeyError):
        return None
    vals = [x for x in vals if x is not None and not math.isnan(x)]
    return sum(vals) if vals else None


def _probe_env(topic: Topic) -> Dict[str, Any]:
    """Defaults with resting states nudged off zero, so d(drag)/d(rho) is not read at v = 0."""
    base = base_env(topic)
    for v in topic.variables:
        if v.kind == "state" and not v.equation:
            val = base.get(v.id)
            if val is None or val == 0:
                lo, hi = (v.range or (0.0, 1.0))
                base[v.id] = (lo + hi) / 2 if (lo + hi) != 0 else 1.0
    env, _ = resolve(topic, env=base)
    return env


def infer_signs(topic: Topic) -> None:
    """Fill the sign of auto relations from the numeric partial derivative.

    Evaluated at the defaults first; if the derivative is exactly flat there
    (a state resting at 0), re-evaluated at a probe point with nonzero states.
    """
    env, _ = resolve(topic)
    probe: Optional[Dict[str, Any]] = None
    for r in topic.relations:
        if r.sign != "?" or not r.auto:
            continue
        k = r.kind if r.kind in ("derived", "dynamic") else None
        p = direct_partial(topic, r.source, r.target, env, kind=k)
        if p is not None and abs(p) <= 1e-12:
            if probe is None:
                probe = _probe_env(topic)
            p = direct_partial(topic, r.source, r.target, probe, kind=k)
        if p is None:
            continue
        if p > 1e-12:
            r.sign = "+"
        elif p < -1e-12:
            r.sign = "-"


@dataclass
class Sensitivity:
    source: str
    target: str
    dydx: float
    elasticity: Optional[float]  # (dy/y)/(dx/x), None when x or y is 0


def total_sensitivities(topic: Topic, rel: float = 1e-4) -> List[Sensitivity]:
    """Perturb every input and record how each derived variable responds."""
    env0, problems = resolve(topic)
    derived = [v.id for v in topic.variables if v.equation and v.id in env0]
    inputs = [v for v in topic.variables if not v.equation and v.id in env0 and env0[v.id] is not None]
    out: List[Sensitivity] = []
    for x in inputs:
        x0 = float(env0[x.id])
        h = abs(x0) * rel if x0 != 0 else rel
        up, _ = resolve(topic, env=dict(base_env(topic), **{x.id: x0 + h}))
        dn, _ = resolve(topic, env=dict(base_env(topic), **{x.id: x0 - h}))
        for y in derived:
            if y not in up or y not in dn:
                continue
            dydx = (float(up[y]) - float(dn[y])) / (2 * h)
            y0 = float(env0[y])
            el = dydx * x0 / y0 if (x0 != 0 and y0 != 0) else None
            if math.isfinite(dydx):
                out.append(Sensitivity(x.id, y, dydx, el if el is None or math.isfinite(el) else None))
    return out


def influence_matrix(topic: Topic) -> Tuple[List[str], List[str], Dict[Tuple[str, str], float], str]:
    """(rows, cols, values, mode) for the influence-matrix figure.

    mode "elasticity": total % change in column per 1 % change in row (equation specs);
    mode "signed":     declared sign × gain for relation-only specs.
    """
    sens = total_sensitivities(topic) if topic.has_equations else []
    if sens:
        rows = list(dict.fromkeys(s.source for s in sens))
        cols = list(dict.fromkeys(s.target for s in sens))
        vals = {}
        for s in sens:
            if s.elasticity is not None:
                vals[(s.source, s.target)] = s.elasticity
            else:
                vals[(s.source, s.target)] = math.copysign(1.0, s.dydx) if s.dydx else 0.0
        return rows, cols, vals, "elasticity"
    rows = list(dict.fromkeys(r.source for r in topic.relations))
    cols = list(dict.fromkeys(r.target for r in topic.relations))
    vals = {}
    for r in topic.relations:
        g = r.gain if r.gain is not None else 1.0
        s = {"+": 1.0, "-": -1.0}.get(r.sign, 0.0)
        vals[(r.source, r.target)] = s * g
    return rows, cols, vals, "signed"


# --------------------------------------------------------------------------
# time integration
# --------------------------------------------------------------------------

@dataclass
class Fired:
    event: Event
    t: float
    values: Dict[str, float] = field(default_factory=dict)


@dataclass
class Trajectory:
    times: List[float]
    series: Dict[str, List[float]]
    fired: List[Fired]
    time_unit: str = "s"
    problems: List[str] = field(default_factory=list)

    def at(self, name: str, i: int) -> float:
        return self.series[name][i]


def integrate(topic: Topic, overrides: Optional[Dict[str, float]] = None,
              max_points: int = 1500) -> Trajectory:
    dyn = topic.dynamics
    if dyn is None:
        raise ValueError("topic has no dynamics")
    env, problems = resolve(topic, overrides)
    states = list(dyn.rates.keys())
    for s in states:
        if s not in env or env[s] is None:
            problems.append(f"state {s} has no initial value; using 0")
            env[s] = 0.0
    rates = {s: E.parse(r) for s, r in dyn.rates.items()}

    state_set = set(states)

    def derivs(t: float, x: Dict[str, float]) -> Dict[str, float]:
        local, _ = resolve(topic, env=dict(env, t=t, **x), skip=state_set)
        local["t"] = t
        out = {}
        for s, tree in rates.items():
            try:
                out[s] = float(E.evaluate(tree, local))
            except (E.ExprError, ArithmeticError, TypeError, ValueError):
                out[s] = 0.0
        return out

    t, h = dyn.start, dyn.step
    n_steps = max(1, int(round((dyn.end - dyn.start) / h)))
    stride = max(1, n_steps // max_points)
    x = {s: float(env[s]) for s in states}
    times: List[float] = []
    series: Dict[str, List[float]] = {}
    fired: List[Fired] = []
    done_events: set = set()
    prev_cond: Dict[str, bool] = {}
    tracked: List[str] = []
    stop = False

    def record(t: float, x: Dict[str, float]) -> Dict[str, Any]:
        full, _ = resolve(topic, env=dict(env, t=t, **x), skip=state_set)
        full["t"] = t
        nonlocal tracked
        if not tracked:
            tracked = [v.id for v in topic.variables if v.id in full and (v.id in states or v.equation)]
            for k in tracked:
                series[k] = []
        times.append(t)
        for k in tracked:
            series[k].append(float(full.get(k, math.nan)))
        return full

    def handle_events(t: float, x: Dict[str, float], full: Dict[str, Any]) -> None:
        nonlocal stop
        for ev in topic.events:
            if ev.id in done_events:
                continue
            trig = False
            if ev.t is not None:
                trig = t + 1e-12 >= ev.t
            elif ev.when:
                try:
                    cond = bool(E.evaluate(ev.when, full))
                except (E.ExprError, ArithmeticError, TypeError, ValueError):
                    cond = False
                trig = cond and not prev_cond.get(ev.id, False)
                prev_cond[ev.id] = cond
            if not trig:
                continue
            if ev.once:
                done_events.add(ev.id)
            for k, val in ev.sets.items():
                num = val if isinstance(val, (int, float)) else E.evaluate(str(val), full)
                if k in x:
                    x[k] = float(num)
                env[k] = float(num)
            fired.append(Fired(ev, t, {k: float(full[k]) for k in tracked if k in full}))
            if ev.stop:
                stop = True

    full = record(t, x)
    handle_events(t, x, full)
    for i in range(1, n_steps + 1):
        if stop:
            break
        if dyn.integrator == "euler":
            k1 = derivs(t, x)
            x = {s: x[s] + h * k1[s] for s in states}
        else:
            k1 = derivs(t, x)
            k2 = derivs(t + h / 2, {s: x[s] + h / 2 * k1[s] for s in states})
            k3 = derivs(t + h / 2, {s: x[s] + h / 2 * k2[s] for s in states})
            k4 = derivs(t + h, {s: x[s] + h * k3[s] for s in states})
            x = {s: x[s] + h / 6 * (k1[s] + 2 * k2[s] + 2 * k3[s] + k4[s]) for s in states}
        t = dyn.start + i * h
        full_now, _ = resolve(topic, env=dict(env, t=t, **x), skip=state_set)
        full_now["t"] = t
        before = len(fired)
        handle_events(t, x, full_now)
        if i % stride == 0 or i == n_steps or len(fired) > before or stop:
            record(t, x)
    return Trajectory(times, series, fired, dyn.time_unit, problems)


def invariant_drift(topic: Topic, traj: Trajectory) -> List[Tuple[str, float, bool]]:
    """Relative drift of each declared invariant along the trajectory."""
    out = []
    for inv in topic.invariants:
        vals = []
        for i, t in enumerate(traj.times):
            env = {k: traj.series[k][i] for k in traj.series}
            env["t"] = t
            env = dict(base_env(topic), **env)
            try:
                vals.append(float(E.evaluate(inv.expr, env)))
            except (E.ExprError, ArithmeticError, TypeError, ValueError):
                vals.append(math.nan)
        clean = [v for v in vals if math.isfinite(v)]
        if not clean:
            out.append((inv.label, math.nan, False))
            continue
        scale = max(abs(sum(clean) / len(clean)), 1e-12)
        drift = (max(clean) - min(clean)) / scale
        out.append((inv.label, drift, drift <= inv.tolerance))
    return out


# --------------------------------------------------------------------------
# chain reaction / cascade
# --------------------------------------------------------------------------

@dataclass
class CascadeEvent:
    index: int
    t: float
    var: str
    delta: float  # relative change, e.g. +0.1 = +10 %
    depth: int
    cause: Optional[int]  # index of the causing event
    relation: Optional[Relation]
    note: str = ""


def _edge_gain(topic: Topic, r: Relation, elasticity: Dict[Tuple[str, str], float]) -> Optional[float]:
    if r.gain is not None:
        s = {"+": 1.0, "-": -1.0}.get(r.sign, 1.0)
        return s * abs(r.gain)
    if r.key in elasticity:
        e = elasticity[r.key]
        return max(-3.0, min(3.0, e))
    if r.sign in ("+", "-"):
        return (1.0 if r.sign == "+" else -1.0) * topic.cascade.damping
    return None


def cascade(topic: Topic, shock: Optional[Dict[str, float]] = None) -> List[CascadeEvent]:
    """Propagate a relative shock through the relation graph with delays.

    Each hop multiplies the change by the edge gain (declared gain, direct
    elasticity, or sign × damping) and arrives ``delay`` later.  Propagation
    stops when a change falls below the threshold, the depth limit, the time
    horizon, or the event cap.
    """
    cfg = topic.cascade
    shock = shock or cfg.shock
    if not shock:
        return []
    env, _ = resolve(topic)
    elasticity: Dict[Tuple[str, str], float] = {}
    for r in topic.relations:
        if r.source == r.target or r.kind == "dynamic":
            continue  # rate-law edges carry no static elasticity: they use sign × damping
        v = topic.var(r.target)
        if v is not None and v.equation and r.source in env and r.target in env:
            p = direct_partial(topic, r.source, r.target, env, kind="derived")
            x0, y0 = env.get(r.source), env.get(r.target)
            if p is not None and x0 and y0:
                elasticity[r.key] = p * float(x0) / float(y0)
    events: List[CascadeEvent] = []
    heap: List[Tuple[float, int, int, str, float, Optional[int], Optional[Relation]]] = []
    counter = 0
    for var, delta in shock.items():
        heapq.heappush(heap, (0.0, counter, 0, var, float(delta), None, None))
        counter += 1
    while heap and len(events) < cfg.max_events:
        t, _, depth, var, delta, cause, rel = heapq.heappop(heap)
        idx = len(events)
        events.append(CascadeEvent(idx, t, var, delta, depth, cause, rel))
        if depth >= cfg.max_depth:
            continue
        for r in topic.outgoing(var):
            if r.target == r.source:
                continue  # a variable's own rate law is not a new consequence
            g = _edge_gain(topic, r, elasticity)
            if g is None:
                continue
            nd = delta * g
            if abs(nd) < cfg.threshold:
                continue
            dt = r.delay if r.delay is not None else cfg.default_delay
            nt = t + dt
            if cfg.horizon is not None and nt > cfg.horizon:
                continue
            heapq.heappush(heap, (nt, counter, depth + 1, r.target, nd, idx, r))
            counter += 1
    return events
