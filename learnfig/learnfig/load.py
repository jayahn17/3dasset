"""Load / dump topic specs (YAML or dict) and derive implicit structure.

After loading, ``finalize`` adds the relations implied by equations and rate
laws (marked ``auto``), so a spec author only writes what the formulas do
not already say.
"""
from __future__ import annotations

import copy
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from . import expr as E
from .model import (CascadeSettings, Dynamics, Event, FigureRequest, Invariant, Relation, Topic,
                    TreeSpec, Variable)


class SpecError(ValueError):
    pass


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "topic"


def _num(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    if isinstance(x, bool):
        return float(x)
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(E.evaluate(x, {}))
        except Exception:
            return None
    return None


def _as_list(x: Any) -> list:
    if x is None:
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]


def topic_from_dict(d: Dict[str, Any], finalize: bool = True) -> Topic:
    if not isinstance(d, dict):
        raise SpecError("topic spec must be a mapping")
    title = d.get("title") or d.get("topic") or "Untitled topic"
    tid = d.get("id") or _slug(title)

    variables: List[Variable] = []
    unknown: List[str] = []
    _VAR_KEYS = {"id", "name", "symbol", "unit", "kind", "default", "value", "range", "equation", "description", "group"}
    _REL_KEYS = {"from", "to", "source", "target", "sign", "gain", "delay", "mechanism", "why", "kind"}
    for raw in _as_list(d.get("variables")):
        if isinstance(raw, str):
            raw = {"id": raw}
        if "id" not in raw:
            raise SpecError(f"variable without id: {raw}")
        unknown += [f"variables[{raw['id']}].{k!r}" for k in raw if k not in _VAR_KEYS]
        rng = raw.get("range")
        variables.append(Variable(
            id=str(raw["id"]), name=raw.get("name", ""), symbol=str(raw.get("symbol", "") or ""),
            unit=str(raw.get("unit", "1") if raw.get("unit") is not None else "1"),
            kind=raw.get("kind", "derived" if raw.get("equation") else "parameter"),
            default=_num(raw.get("default", raw.get("value"))),
            range=tuple(rng) if isinstance(rng, (list, tuple)) and len(rng) == 2 else None,
            equation=raw.get("equation"), description=raw.get("description", ""),
            group=raw.get("group", ""),
        ))

    relations: List[Relation] = []
    for raw in _as_list(d.get("relations")):
        if isinstance(raw, str):
            m = re.match(r"\s*(\w+)\s*(-+|\+)?-?>\s*(\w+)\s*(?:\((\+|-|\?|±)\))?", raw)
            if not m:
                raise SpecError(f"cannot parse relation {raw!r}")
            raw = {"from": m.group(1), "to": m.group(3), "sign": m.group(4) or "?"}
        src = raw.get("from", raw.get("source"))
        tgt = raw.get("to", raw.get("target"))
        if not src or not tgt:
            raise SpecError(f"relation needs from/to: {raw}")
        unknown += [f"relations[{src}->{tgt}].{k!r}" for k in raw if k not in _REL_KEYS]
        sign = str(raw.get("sign", "?"))
        sign = {"pos": "+", "neg": "-", "positive": "+", "negative": "-", "plus": "+", "minus": "-"}.get(sign, sign)
        relations.append(Relation(
            source=str(src), target=str(tgt), sign=sign, gain=_num(raw.get("gain")),
            delay=_num(raw.get("delay")), mechanism=raw.get("mechanism", raw.get("why", "")),
            kind=raw.get("kind", "causal"),
        ))

    events: List[Event] = []
    for raw in _as_list(d.get("events")):
        if "id" not in raw:
            raw = dict(raw, id=_slug(raw.get("label", "event")))
        events.append(Event(
            id=str(raw["id"]), label=raw.get("label", ""), t=_num(raw.get("t", raw.get("time"))),
            when=raw.get("when"), once=bool(raw.get("once", True)), sets=dict(raw.get("sets") or {}),
            shock=dict(raw.get("shock") or {}), stop=bool(raw.get("stop", False)),
            description=raw.get("description", ""),
        ))

    dyn = None
    if d.get("dynamics"):
        rd = d["dynamics"]
        time = rd.get("time", {}) or {}
        dyn = Dynamics(
            rates={str(k): str(v) for k, v in (rd.get("rates") or {}).items()},
            start=float(time.get("start", rd.get("start", 0.0))),
            end=float(time.get("end", rd.get("end", 10.0))),
            step=float(time.get("step", rd.get("step", 0.01))),
            time_unit=str(time.get("unit", rd.get("time_unit", "s"))),
            integrator=rd.get("integrator", "rk4"),
        )

    invariants = [Invariant(label=i.get("label", i.get("expr", "")), expr=i["expr"],
                            tolerance=float(i.get("tolerance", 1e-3)))
                  for i in _as_list(d.get("invariants"))]

    cas = d.get("cascade") or {}
    cascade = CascadeSettings(
        shock={str(k): float(v) for k, v in (cas.get("shock") or {}).items()},
        default_delay=float(cas.get("default_delay", 1.0)), damping=float(cas.get("damping", 0.6)),
        threshold=float(cas.get("threshold", 0.02)), max_depth=int(cas.get("max_depth", 6)),
        horizon=_num(cas.get("horizon")), max_events=int(cas.get("max_events", 200)),
        time_unit=str(cas.get("time_unit", "step")),
    )

    tree = None
    if d.get("tree"):
        td = d["tree"]
        tree = TreeSpec(kind=td.get("kind", "binary"), title=td.get("title", ""), root=td.get("root"),
                        generate=td.get("generate"), algorithms=_as_list(td.get("algorithms")))

    figures = []
    for raw in _as_list(d.get("figures")):
        if isinstance(raw, str):
            raw = {"type": raw}
        figures.append(FigureRequest(type=raw["type"], title=raw.get("title", ""),
                                     caption=raw.get("caption", ""),
                                     options={k: v for k, v in raw.items() if k not in ("type", "title", "caption")}))

    topic = Topic(
        id=str(tid), title=str(title), domain=d.get("domain", ""), level=d.get("level", ""),
        chapter=str(d.get("chapter", "") or ""), summary=d.get("summary", ""),
        notes=_as_list(d.get("notes")), variables=variables, relations=relations, events=events,
        dynamics=dyn, invariants=invariants, cascade=cascade, tree=tree, figures=figures,
        sources=_as_list(d.get("sources")), meta=dict(d.get("meta") or {}),
    )
    if unknown:
        topic.meta["unknown_keys"] = unknown
    if finalize:
        finalize_topic(topic)
    return topic


def finalize_topic(topic: Topic) -> Topic:
    """Add relations implied by equations and rate laws; infer missing signs."""
    ids = set(topic.var_ids)
    existing = {r.key: r for r in topic.relations}

    def add(src: str, tgt: str, kind: str) -> None:
        if src not in ids or tgt not in ids or src == tgt and kind == "derived":
            return
        if (src, tgt) in existing:
            return
        r = Relation(source=src, target=tgt, kind=kind, auto=True)
        topic.relations.append(r)
        existing[(src, tgt)] = r

    for v in topic.variables:
        if v.equation:
            try:
                for name in E.free_names(v.equation):
                    add(name, v.id, "derived")
            except E.ExprError:
                pass
    if topic.dynamics:
        for state, rate in topic.dynamics.rates.items():
            try:
                for name in E.free_names(rate):
                    if name != "t":
                        add(name, state, "dynamic")
            except E.ExprError:
                pass
    # lazy import to avoid a cycle: simulate uses the model, not load
    from .simulate import infer_signs
    infer_signs(topic)
    return topic


def load_topic(path: str | Path, finalize: bool = True) -> Topic:
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    topic = topic_from_dict(data, finalize=finalize)
    topic.meta.setdefault("source_path", str(p))
    return topic


def loads_topic(text: str, finalize: bool = True) -> Topic:
    return topic_from_dict(yaml.safe_load(text) or {}, finalize=finalize)


def topic_to_dict(topic: Topic, include_auto: bool = False) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "id": topic.id, "title": topic.title, "domain": topic.domain, "level": topic.level,
        "chapter": topic.chapter, "summary": topic.summary, "notes": list(topic.notes),
        "variables": [], "relations": [], "events": [],
    }
    for v in topic.variables:
        vd = {k: val for k, val in asdict(v).items() if val not in (None, "", ())}
        if v.range:
            vd["range"] = list(v.range)
        d["variables"].append(vd)
    for r in topic.relations:
        if r.auto and not include_auto:
            continue
        rd = {"from": r.source, "to": r.target, "sign": r.sign}
        for k in ("gain", "delay", "mechanism", "kind"):
            val = getattr(r, k)
            if val not in (None, "", "causal"):
                rd[k] = val
        d["relations"].append(rd)
    for e in topic.events:
        ed = {k: val for k, val in asdict(e).items() if val not in (None, "", {}, False)}
        ed.setdefault("once", e.once)
        d["events"].append(ed)
    if topic.dynamics:
        dy = topic.dynamics
        d["dynamics"] = {"time": {"unit": dy.time_unit, "start": dy.start, "end": dy.end, "step": dy.step},
                         "rates": dict(dy.rates), "integrator": dy.integrator}
    if topic.invariants:
        d["invariants"] = [asdict(i) for i in topic.invariants]
    if topic.cascade.shock or topic.cascade != CascadeSettings():
        d["cascade"] = {k: v for k, v in asdict(topic.cascade).items() if v not in (None, {})}
    if topic.tree:
        d["tree"] = {k: v for k, v in asdict(topic.tree).items() if v not in (None, "", [])}
    if topic.figures:
        d["figures"] = [dict({"type": f.type, "title": f.title, "caption": f.caption}, **f.options)
                        for f in topic.figures]
    if topic.sources:
        d["sources"] = list(topic.sources)
    if topic.meta:
        d["meta"] = {k: v for k, v in topic.meta.items() if k != "source_path"}
    return {k: v for k, v in d.items() if v not in ("", [], {}, None)}


def dump_topic(topic: Topic, path: str | Path, include_auto: bool = False) -> None:
    with Path(path).open("w", encoding="utf-8") as fh:
        yaml.safe_dump(topic_to_dict(topic, include_auto), fh, sort_keys=False, allow_unicode=True,
                       width=100)


def clone(topic: Topic) -> Topic:
    return copy.deepcopy(topic)
