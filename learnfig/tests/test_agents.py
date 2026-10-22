"""The agent loop with a fake model: no network, deterministic."""
import yaml

from learnfig.agents.orchestrator import run_pipeline
from learnfig.agents.roles import extract_block

GOOD_SPEC = """
id: cooling
title: Newton's law of cooling
variables:
  - {id: T, name: Temperature, unit: K, kind: state, default: 350}
  - {id: T_env, name: Room temperature, unit: K, kind: parameter, default: 293}
  - {id: k, name: Cooling constant, unit: 1/s, kind: parameter, default: 0.01}
  - {id: tau, name: Time constant, unit: s, kind: derived, equation: 1/k}
dynamics:
  time: {unit: s, start: 0, end: 600, step: 0.5}
  rates: {T: -k*(T - T_env)}
events:
  - {id: half, label: Halfway to room temperature, when: "T <= T_env + 0.5*(350 - T_env)", once: true}
cascade: {shock: {k: 0.5}}
"""

BAD_SPEC = GOOD_SPEC.replace("unit: s, kind: derived, equation: 1/k", "unit: K, kind: derived, equation: 1/k")


class FakeLLM:
    """Answers by role (detected from the system prompt); the checker fixes the unit error."""

    def __init__(self):
        self.calls = []
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    def complete(self, system, user, *, effort="medium", max_tokens=16000, model=None):
        self.calls.append(system[:30])
        self.usage["calls"] += 1
        if system.startswith("You are the READER"):
            return "Kept the three quantities the source names.\n```yaml\n" + BAD_SPEC + "\n```"
        if system.startswith("You are the SCIENCE CHECKER"):
            if "unit-mismatch" in user:
                return ("```json\n{\"verdict\": \"revise\", \"issues\": [{\"severity\": \"error\", \"where\": \"tau\","
                        " \"problem\": \"1/k has units of s\", \"fix\": \"declare tau in s\"}], \"summary\": \"fixed\"}\n```\n"
                        "```yaml\n" + GOOD_SPEC + "\n```")
            return "```json\n{\"verdict\": \"pass\", \"issues\": [], \"summary\": \"A hot object cools toward room temperature.\"}\n```"
        if system.startswith("You are the WRITER"):
            return ("```json\n{\"summary\": \"Cooling slows as the gap closes.\", \"notes\": [\"The time constant is 1/k.\"],"
                    " \"figures\": {\"fig-variables\": {\"title\": \"Who cools whom\", \"caption\": \"Arrows show the influences.\","
                    " \"notice\": [\"k drives the rate.\"]}}}\n```")
        raise AssertionError("unknown role")


def test_extract_block_prefers_language():
    txt = "```json\n{\"a\": 1}\n```\n```yaml\nb: 2\n```"
    assert extract_block(txt, "yaml").strip() == "b: 2"
    assert extract_block(txt, "json").strip() == '{"a": 1}'


def test_pipeline_reads_checks_revises_and_writes(tmp_path):
    llm = FakeLLM()
    res = run_pipeline(sources=[], topic="Newton's law of cooling", out_dir=tmp_path, llm=llm, rounds=2)
    assert res["report"].ok
    assert res["verdict"] == "pass" and res["rounds"] == 2
    assert res["index"].exists()
    final = yaml.safe_load((tmp_path / "spec.final.yaml").read_text())
    assert next(v for v in final["variables"] if v["id"] == "tau")["unit"] == "s"
    page = res["index"].read_text()
    assert "Who cools whom" in page and "Cooling slows as the gap closes." in page
    log = (tmp_path / "agents_log.md").read_text()
    assert "Checker round 1: revise" in log and "Checker round 2: pass" in log
    assert llm.calls[0].startswith("You are the READER") and llm.calls[-1].startswith("You are the WRITER")


def test_dry_run_prints_prompts(tmp_path):
    res = run_pipeline(sources=[], topic="x", out_dir=tmp_path, dry_run=True)
    assert "READER system prompt" in res["dry_run"]
