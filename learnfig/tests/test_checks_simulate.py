import math

import pytest

from learnfig import load
from learnfig.checks import run_checks
from learnfig.simulate import cascade, integrate, invariant_drift, influence_matrix, resolve
from tests.conftest import EXAMPLES

PROJECTILE = """
id: proj
title: Projectile
variables:
  - {id: v0, name: Launch speed, unit: m/s, kind: parameter, default: 20}
  - {id: theta, name: Angle, unit: rad, kind: parameter, default: 0.7853981634}
  - {id: g, name: Gravity, unit: m/s^2, kind: constant, default: 9.81}
  - {id: x, name: x, unit: m, kind: state, default: 0}
  - {id: y, name: y, unit: m, kind: state, default: 0}
  - {id: vx, name: vx, unit: m/s, kind: state, equation: v0*cos(theta)}
  - {id: vy, name: vy, unit: m/s, kind: state, equation: v0*sin(theta)}
  - {id: range, name: Range, unit: m, kind: derived, equation: v0**2*sin(2*theta)/g}
  - {id: t_flight, name: Flight time, unit: s, kind: derived, equation: 2*v0*sin(theta)/g}
relations:
  - {from: v0, to: range, sign: "+", mechanism: faster launch flies farther}
dynamics:
  time: {unit: s, start: 0, end: 4, step: 0.002}
  rates: {x: vx, y: vy, vy: -g}
events:
  - {id: apex, label: Apex, when: "vy <= 0", once: true}
  - {id: land, label: Landing, when: "y <= 0 and t > 0.1", once: true, stop: true}
invariants:
  - {label: energy, expr: "0.5*(vx**2+vy**2) + g*y", tolerance: 1e-6}
cascade: {shock: {v0: 0.1}}
"""


def test_projectile_passes_and_lands_at_range():
    topic = load.loads_topic(PROJECTILE)
    rep = run_checks(topic)
    assert rep.ok, rep.to_markdown()
    env, problems = resolve(topic)
    assert not problems
    assert env["range"] == pytest.approx(400 / 9.81, rel=1e-6)
    traj = integrate(topic)
    land = [f for f in traj.fired if f.event.id == "land"]
    assert land and land[0].t == pytest.approx(env["t_flight"], abs=0.01)
    assert land[0].values["x"] == pytest.approx(env["range"], rel=0.01)
    drift = invariant_drift(topic, traj)
    assert all(ok for _, _, ok in drift)
    # state initial condition from equation, not overwritten while integrating
    assert traj.series["vx"][0] == pytest.approx(20 * math.cos(math.pi / 4))
    assert traj.series["vx"][-1] == pytest.approx(traj.series["vx"][0])


def test_auto_relations_and_signs():
    topic = load.loads_topic(PROJECTILE)
    r = topic.relation("g", "range")
    assert r is not None and r.auto and r.sign == "-"
    assert topic.relation("theta", "t_flight").sign == "+"


def test_sign_contradiction_and_unit_mismatch_are_errors():
    bad = PROJECTILE.replace('sign: "+", mechanism: faster', 'sign: "-", mechanism: faster')
    bad = bad.replace("{id: t_flight, name: Flight time, unit: s,", "{id: t_flight, name: Flight time, unit: m,")
    rep = run_checks(load.loads_topic(bad))
    codes = {f.code for f in rep.errors}
    assert "sign-contradiction" in codes and "unit-mismatch" in codes


def test_cascade_propagates_with_delays():
    topic = load.loads_topic(PROJECTILE)
    events = cascade(topic)
    assert events[0].var == "v0" and events[0].delta == pytest.approx(0.1)
    rng = [e for e in events if e.var == "range"]
    assert rng and rng[0].delta == pytest.approx(0.2, rel=0.05)  # elasticity of v0² is 2
    assert rng[0].t == pytest.approx(1.0) and rng[0].cause == 0


def test_influence_matrix_elasticities():
    topic = load.loads_topic(PROJECTILE)
    rows, cols, vals, mode = influence_matrix(topic)
    assert mode == "elasticity"
    assert vals[("v0", "range")] == pytest.approx(2.0, rel=1e-3)
    assert vals[("g", "range")] == pytest.approx(-1.0, rel=1e-3)


def test_unknown_key_flow_mapping_warning():
    spec = PROJECTILE.replace("equation: 2*v0*sin(theta)/g}", "equation: max(2*v0, 1)*sin(theta)/g}")
    topic = load.loads_topic(spec)
    rep = run_checks(topic, simulate=False)
    assert any(f.code == "unknown-key" for f in rep.findings)


@pytest.mark.parametrize("path", sorted(EXAMPLES.rglob("*.yaml")), ids=lambda p: p.stem)
def test_examples_pass_their_own_checks(path):
    topic = load.load_topic(path)
    rep = run_checks(topic)
    assert rep.ok, rep.to_markdown()


def test_cascade_ignores_self_loops_and_damps_rate_edges():
    topic = load.loads_topic(PROJECTILE)
    events = cascade(topic, {"v0": 0.1})
    assert all(not (e.relation and e.relation.source == e.relation.target) for e in events)
    vx = [e for e in events if e.var == "vx"]
    assert vx and vx[0].delta == pytest.approx(0.1, rel=1e-3)  # initial condition v0*cos(theta): elasticity 1
    x = [e for e in events if e.var == "x" and e.cause is not None and events[e.cause].var == "vx"]
    assert x and x[0].delta == pytest.approx(0.1 * topic.cascade.damping, rel=1e-6)  # rate edge: sign × damping
