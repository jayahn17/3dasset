"""Reader, checker and writer: one function each, pure except for the model call."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..checks import Report
from ..load import SpecError, topic_from_dict, topic_to_dict
from ..model import Topic
from .llm import LLM, AgentError
from .prompts import CHECKER_SYSTEM, READER_SYSTEM, WRITER_SYSTEM

_FENCE = re.compile(r"```(\w+)?\s*\n(.*?)```", re.S)


def extract_block(text: str, lang: str) -> Optional[str]:
    blocks = [(l or "", body) for l, body in _FENCE.findall(text)]
    for l, body in blocks:
        if l.lower() == lang:
            return body
    # fall back to the last unlabeled block
    for l, body in reversed(blocks):
        if not l:
            return body
    return None


def parse_spec_text(text: str) -> Tuple[Topic, str]:
    body = extract_block(text, "yaml")
    if body is None:
        raise AgentError("no ```yaml block in the model output")
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise AgentError(f"model YAML does not parse: {exc}") from exc
    try:
        return topic_from_dict(data), body
    except SpecError as exc:
        raise AgentError(f"spec rejected: {exc}") from exc


# -------------------------------------------------------------------------- reader

def read_sources(llm: LLM, topic: str, source_text: str, focus: Optional[str] = None,
                 model: Optional[str] = None) -> Tuple[Topic, str]:
    user = (f"TOPIC: {topic}\n" + (f"FOCUS: {focus}\n" if focus else "") +
            "\nSOURCE TEXT (may be noisy OCR/pdftotext; equations may be garbled — reconstruct them from context):\n"
            "<<<\n" + source_text + "\n>>>\n\nProduce the topic spec.")
    out = llm.complete(READER_SYSTEM, user, effort="medium", max_tokens=16000, model=model)
    try:
        return parse_spec_text(out)
    except AgentError as exc:
        # one repair round: tell the model exactly what failed
        repair = (f"Your previous spec was rejected: {exc}\n\nPrevious output:\n{out}\n\n"
                  "Return a corrected spec as a single ```yaml block.")
        out2 = llm.complete(READER_SYSTEM, user + "\n\n" + repair, effort="medium", max_tokens=16000, model=model)
        return parse_spec_text(out2)


# -------------------------------------------------------------------------- checker

def review_spec(llm: LLM, topic: Topic, report: Report, model: Optional[str] = None) -> Dict[str, Any]:
    spec_yaml = yaml.safe_dump(topic_to_dict(topic, include_auto=True), sort_keys=False, allow_unicode=True)
    user = ("TOPIC SPEC:\n```yaml\n" + spec_yaml + "```\n\nAUTOMATIC CHECK REPORT:\n" +
            report.to_markdown("Automatic checks") + "\n\nVALUES AT DEFAULTS:\n" +
            json.dumps(report.values, indent=1, default=str) + "\n\nReview it.")
    out = llm.complete(CHECKER_SYSTEM, user, effort="high", max_tokens=20000, model=model)
    body = extract_block(out, "json")
    verdict: Dict[str, Any] = {"verdict": "revise", "issues": [], "summary": ""}
    if body:
        try:
            verdict.update(json.loads(body))
        except json.JSONDecodeError:
            verdict["issues"].append({"severity": "warning", "where": "checker", "problem": "unparseable JSON verdict",
                                      "fix": "see raw output"})
    verdict["raw"] = out
    if verdict.get("verdict") == "revise":
        yaml_body = extract_block(out, "yaml")
        if yaml_body:
            try:
                verdict["revised_topic"] = topic_from_dict(yaml.safe_load(yaml_body))
                verdict["revised_yaml"] = yaml_body
            except (SpecError, yaml.YAMLError) as exc:
                verdict["issues"].append({"severity": "warning", "where": "checker", "problem": f"revised spec rejected: {exc}",
                                          "fix": "kept the previous spec"})
    return verdict


# -------------------------------------------------------------------------- writer

def write_captions(llm: LLM, topic: Topic, report: Report, figures: List[dict],
                   model: Optional[str] = None) -> Dict[str, Any]:
    fig_desc = []
    for f in figures:
        d = {"id": f["id"], "type": f["type"], "title": f["title"], "auto_caption": f.get("caption", "")}
        if f.get("key"):
            d["key"] = [{"n": k["n"], "from": k["source"], "to": k["target"], "sign": k["sign"], "mechanism": k["mechanism"]}
                        for k in f["key"]]
        if f.get("meta"):
            d["facts"] = f["meta"]
        fig_desc.append(d)
    spec_yaml = yaml.safe_dump(topic_to_dict(topic, include_auto=True), sort_keys=False, allow_unicode=True)
    user = ("TOPIC SPEC:\n```yaml\n" + spec_yaml + "```\n\nCHECK REPORT:\n" + report.to_markdown("Checks") +
            "\n\nFIGURES:\n```json\n" + json.dumps(fig_desc, indent=1, default=str) + "\n```\n\nWrite the captions.")
    out = llm.complete(WRITER_SYSTEM, user, effort="medium", max_tokens=12000, model=model)
    body = extract_block(out, "json")
    if not body:
        raise AgentError("writer returned no JSON block")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AgentError(f"writer JSON invalid: {exc}") from exc
    data["raw"] = out
    return data


def apply_captions(topic: Topic, figures: List[dict], data: Dict[str, Any]) -> None:
    if data.get("summary"):
        topic.summary = str(data["summary"])
    if data.get("notes"):
        topic.notes = [str(n) for n in data["notes"]]
    for f in figures:
        upd = (data.get("figures") or {}).get(f["id"])
        if not upd:
            continue
        if upd.get("title"):
            f["title"] = str(upd["title"])
        if upd.get("caption"):
            f["caption"] = str(upd["caption"])
        if upd.get("notice"):
            f["notice"] = [str(n) for n in upd["notice"]][:3]
