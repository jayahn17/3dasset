"""read → check → (revise)* → write → render, with a log of every stage."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..checks import run_checks
from ..load import dump_topic, topic_to_dict
from ..model import Topic
from ..pipeline import build
from ..render import render_page
from .llm import LLM, ClaudeLLM
from .prompts import CHECKER_SYSTEM, READER_SYSTEM, WRITER_SYSTEM
from .roles import apply_captions, read_sources, review_spec, write_captions
from .sources import gather


def run_pipeline(sources: List[str], topic: str, out_dir: Path, focus: Optional[str] = None, rounds: int = 2,
                 dry_run: bool = False, model: Optional[str] = None, pages: Optional[str] = None,
                 max_chars: int = 120000, llm: Optional[LLM] = None, spec: Optional[Topic] = None) -> Dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log: List[str] = [f"# Agent log: {topic}", ""]
    source_text = gather(sources, pages=pages, max_chars=max_chars) if sources else ""
    if dry_run:
        preview = ["READER system prompt:", READER_SYSTEM, "", "READER user content (first 3000 chars):",
                   (f"TOPIC: {topic}\nFOCUS: {focus or ''}\n" + source_text)[:3000], "",
                   "CHECKER system prompt:", CHECKER_SYSTEM, "", "WRITER system prompt:", WRITER_SYSTEM]
        return {"dry_run": "\n".join(preview)}
    llm = llm or ClaudeLLM(model=model)

    # 1. read
    if spec is None:
        topic_obj, raw_yaml = read_sources(llm, topic, source_text, focus=focus, model=model)
        log += ["## Reader", "", "```yaml", raw_yaml.strip(), "```", ""]
    else:
        topic_obj = spec
        log += ["## Reader", "", "(spec supplied; reader skipped)", ""]
    (out_dir / "spec.v0.yaml").write_text(yaml.safe_dump(topic_to_dict(topic_obj), sort_keys=False, allow_unicode=True),
                                          encoding="utf-8")

    # 2. check → revise loop
    verdict: Dict[str, Any] = {"verdict": "unchecked"}
    report = run_checks(topic_obj)
    used_rounds = 0
    for rnd in range(1, rounds + 1):
        used_rounds = rnd
        verdict = review_spec(llm, topic_obj, report, model=model)
        issues = verdict.get("issues", [])
        log += [f"## Checker round {rnd}: {verdict.get('verdict')}", ""]
        log += [f"- **{i.get('severity', '?')}** `{i.get('where', '')}`: {i.get('problem', '')} → {i.get('fix', '')}" for i in issues]
        log.append("")
        if verdict.get("verdict") == "pass" and report.ok:
            break
        revised = verdict.get("revised_topic")
        if revised is None:
            log.append("(no revised spec returned; keeping the current one)")
            break
        topic_obj = revised
        (out_dir / f"spec.v{rnd}.yaml").write_text(verdict.get("revised_yaml", ""), encoding="utf-8")
        report = run_checks(topic_obj)
        log += ["Automatic report after revision:", "", report.to_markdown("Checks"), ""]

    if verdict.get("summary") and not topic_obj.summary:
        topic_obj.summary = verdict["summary"]

    # 3. render once to get the figure list, 4. write captions, 5. render the final page
    result = build(topic_obj, out_dir, generated_by="learnfig agents (reader → checker → writer)")
    try:
        captions = write_captions(llm, topic_obj, result.report, result.figures, model=model)
        apply_captions(topic_obj, result.figures, captions)
        log += ["## Writer", "", "```json", json.dumps({k: v for k, v in captions.items() if k != "raw"}, indent=1), "```", ""]
        page = render_page(topic_obj, result.figures, result.report, result.report.values,
                           {"tree_steps": "tree_steps.html" if "tree_steps" in result.files else "",
                            "scene3d": "scene3d.html" if "scene3d" in result.files else "",
                            "spatial": "spatial.html" if "spatial" in result.files else "",
                            "report_md": "report.md", "spec": "spec.resolved.yaml"},
                           generated_by="learnfig agents (reader → checker → writer)")
        (out_dir / "index.html").write_text(page, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - captions are a nicety; the figures already exist
        log += ["## Writer", "", f"writer failed: {exc}", ""]
    dump_topic(topic_obj, out_dir / "spec.final.yaml")
    usage = getattr(llm, "usage", None)
    if usage:
        log += ["## Usage", "", f"calls: {usage['calls']}, input tokens: {usage['input_tokens']}, output tokens: {usage['output_tokens']}", ""]
    (out_dir / "agents_log.md").write_text("\n".join(log), encoding="utf-8")
    return {"topic": topic_obj, "report": result.report, "index": result.index, "verdict": verdict.get("verdict"),
            "rounds": used_rounds, "log": out_dir / "agents_log.md", "figures": result.figures}
