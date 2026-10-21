import json
import re

import pytest

from learnfig import load
from learnfig.pipeline import build
from learnfig.render import resolve_live
from tests.conftest import EXAMPLES


def _payload(html: str) -> dict:
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    assert m, "spatial page carries its data block"
    assert "</" not in m.group(1), "payload must not be able to close the script tag"
    return json.loads(m.group(1))


@pytest.fixture(scope="module")
def orbit(tmp_path_factory):
    topic = load.load_topic(EXAMPLES / "zohdi_drone_book" / "ch9_satellite_orbit.yaml")
    out = tmp_path_factory.mktemp("orbit")
    return topic, build(topic, out)


def test_orbit_room_is_built_and_linked(orbit):
    topic, res = orbit
    page = (res.out_dir / "index.html").read_text(encoding="utf-8")
    assert (res.out_dir / "spatial.html").exists()
    assert 'href="spatial.html"' in page and 'allow="xr-spatial-tracking; fullscreen"' in page
    fig = [f for f in res.figures if f["type"] == "spatial"]
    assert len(fig) == 1 and fig[0] is res.figures[-1], "the room is rendered last, after the figures it shows"


def test_orbit_room_payload(orbit):
    topic, res = orbit
    D = _payload((res.out_dir / "spatial.html").read_text(encoding="utf-8"))
    assert [n["id"] for n in D["nodes"]] == topic.var_ids
    assert all(-90 <= n["lat"] <= 90 and -180 <= n["lon"] <= 180 for n in D["nodes"])
    assert len(D["edges"]) == len(topic.relations)
    assert [p["number"] for p in D["panels"]] == ["9.1", "9.2", "9.3", "9.5"]
    assert all(p["svg"].startswith("<svg") for p in D["panels"])
    path = D["path"]
    assert (path["a"], path["b"], path["same_scale"]) == ("x", "y", True)
    assert 0.5 < path["origin_r"] < 1, "the planet is drawn to scale under the orbit"
    assert max(max(abs(u), abs(v)) for u, v in path["pts"]) == pytest.approx(1.0, abs=1e-3)
    assert len(path["pts"]) == len(path["ts"]) <= 601 and path["ts"][0] == 0 and path["ts"][-1] == 1
    assert {"Apoapsis", "One orbit complete"} <= {f["label"] for f in D["fired"]}
    feed = D["live"][0]
    assert feed["model"]["id"] == "v_circ" and feed["factor"] == pytest.approx(1 / 3.6)
    assert feed["model"]["value"] == pytest.approx(7672.6, rel=1e-3)


def test_live_feeds_are_checked_against_units():
    topic = load.load_topic(EXAMPLES / "zohdi_drone_book" / "ch9_satellite_orbit.yaml")
    feeds = [
        {"url": "https://example.org/a", "field": "altitude", "unit": "km", "compare": "v_circ"},
        {"url": "http://example.org/b", "field": "velocity", "unit": "km/h", "compare": "v_circ"},
        {"url": "https://example.org/c", "field": "velocity", "unit": "km/h", "compare": "nope"},
        {"url": "https://example.org/d", "field": "altitude", "unit": "km"},
        {"url": "https://example.org/e"},
    ]
    live, problems = resolve_live(topic, {}, feeds)
    assert [f["url"] for f in live] == ["https://example.org/d"]
    assert live[0]["factor"] == 1.0 and live[0]["model"] is None
    assert len(problems) == 4
    assert any("not compatible" in p for p in problems)
    assert any("https" in p for p in problems)
    assert any("unknown variable" in p for p in problems)


def test_room_without_dynamics(tmp_path):
    topic = load.loads_topic("""
id: supply
title: Price and demand
variables:
  - {id: p, name: Price index, unit: "1", kind: parameter, default: 2}
  - {id: q, name: Quantity demanded, unit: "1", kind: derived, equation: 100 - 10*p}
relations:
  - {from: p, to: q, sign: "-", mechanism: Dearer goods sell less.}
""")
    res = build(topic, tmp_path)
    assert any(f["type"] == "spatial" for f in res.figures), "spatial is a default figure"
    D = _payload((tmp_path / "spatial.html").read_text(encoding="utf-8"))
    assert D["path"] is None and D["t_span"] is None and D["series"] == {}
    assert D["edges"][0]["sign"] == "-" and D["edges"][0]["mech"] == "Dearer goods sell less."


def test_bad_path_option_falls_back(tmp_path):
    topic = load.load_topic(EXAMPLES / "predator_prey.yaml")
    for f in topic.figures:
        if f.type == "spatial":
            f.options["path"] = ["x", "missing"]
    res = build(topic, tmp_path)
    D = _payload((tmp_path / "spatial.html").read_text(encoding="utf-8"))
    assert (D["path"]["a"], D["path"]["b"]) == ("x", "y")
    assert any("missing" in p for p in D["problems"])
    assert any("missing" in n for n in res.notes)
