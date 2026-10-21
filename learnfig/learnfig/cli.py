"""Command-line interface.

  learnfig check    spec.yaml                 run the science checker
  learnfig render   spec.yaml -o out/topic    build figures + page
  learnfig simulate spec.yaml --shock x=+0.1  print trajectory events and the cascade
  learnfig new      my-topic --title "..."    scaffold a spec from the template
  learnfig examples -o build                  render every example + gallery
  learnfig agents   --topic "..." SOURCE...   read → check → write with Claude
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import webbrowser
from pathlib import Path

from .checks import run_checks
from .load import SpecError, load_topic
from .pipeline import build, render_gallery
from .simulate import cascade, integrate

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "spec" / "TEMPLATE.topic.yaml"


def _load(path: str):
    try:
        return load_topic(path)
    except (SpecError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)


def cmd_check(a: argparse.Namespace) -> int:
    topic = _load(a.spec)
    rep = run_checks(topic, simulate=not a.no_simulate)
    if a.json:
        print(json.dumps(rep.to_dict(), indent=2, default=str))
    else:
        print(rep.to_markdown(f"Check report: {topic.title}"))
    return 0 if rep.ok else 1


def cmd_render(a: argparse.Namespace) -> int:
    topic = _load(a.spec)
    out = Path(a.out or Path("build") / topic.id)
    res = build(topic, out, checks=not a.no_checks)
    print(f"{'PASS' if res.report.ok else 'FAIL'}  {topic.title}")
    for f in res.figures:
        print(f"  figure {f['number']}: {f['title']}")
    for n in res.notes:
        print(f"  note: {n}")
    print(f"  → {res.index}")
    if a.open:
        webbrowser.open(res.index.resolve().as_uri())
    return 0 if res.report.ok or a.no_checks else 1


def _parse_shock(items):
    shock = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--shock expects var=+0.1, got {it!r}")
        k, v = it.split("=", 1)
        v = v.strip().rstrip("%")
        val = float(v)
        if it.strip().endswith("%"):
            val /= 100
        shock[k.strip()] = val
    return shock


def cmd_simulate(a: argparse.Namespace) -> int:
    topic = _load(a.spec)
    out = {}
    if topic.dynamics:
        traj = integrate(topic)
        out["trajectory"] = {"t_end": traj.times[-1], "final": {k: v[-1] for k, v in traj.series.items()},
                             "fired": [{"event": f.event.title, "t": f.t} for f in traj.fired]}
        if not a.json:
            print(f"Integrated {topic.title}: {len(traj.times)} samples, t = {traj.times[0]:g} … {traj.times[-1]:g} {traj.time_unit}")
            for f in traj.fired:
                print(f"  event {f.event.title!r} at t = {f.t:.4g}")
            for k, v in traj.series.items():
                print(f"  {k}: {v[0]:.4g} → {v[-1]:.4g}")
    shock = _parse_shock(a.shock) or topic.cascade.shock or None
    events = cascade(topic, shock) if shock else []
    out["cascade"] = [{"t": e.t, "var": e.var, "delta": e.delta, "depth": e.depth, "cause": e.cause} for e in events]
    if a.json:
        print(json.dumps(out, indent=2, default=str))
    elif events:
        print(f"Cascade from shock {shock}:")
        for e in events:
            cause = f"  ← {events[e.cause].var}" if e.cause is not None else "  (shock)"
            print(f"  t={e.t:6.2f}  {e.var:<18} {e.delta:+7.1%}  depth {e.depth}{cause}")
    elif not topic.dynamics:
        print("nothing to simulate: no dynamics and no shock (use --shock var=+0.1)")
    return 0


def cmd_new(a: argparse.Namespace) -> int:
    slug = a.slug.strip().lower().replace(" ", "_")
    dest_dir = Path(a.dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{slug}.yaml"
    if dest.exists() and not a.force:
        print(f"error: {dest} exists (use --force)", file=sys.stderr)
        return 2
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("id: my_topic", f"id: {slug}").replace("title: My topic", f"title: {a.title or slug.replace('_', ' ').title()}")
    dest.write_text(text, encoding="utf-8")
    print(f"created {dest}\nnext: edit it, then  learnfig check {dest}  and  learnfig render {dest}")
    return 0


def cmd_examples(a: argparse.Namespace) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    paths = sorted(p for p in Path(a.dir).rglob("*.yaml") if not p.name.startswith("_"))
    results = []
    failed = 0
    for p in paths:
        try:
            topic = load_topic(p)
        except SpecError as exc:
            print(f"SKIP  {p}: {exc}")
            failed += 1
            continue
        sub = out / p.relative_to(a.dir).with_suffix("")
        res = build(topic, sub)
        results.append(res)
        failed += 0 if res.report.ok else 1
        print(f"{'PASS' if res.report.ok else 'FAIL'}  {p} → {res.index}  ({len(res.figures)} figures)")
    gallery = render_gallery(results, out, title=a.title)
    print(f"gallery → {gallery}")
    if a.open:
        webbrowser.open(gallery.resolve().as_uri())
    return 1 if failed else 0


def cmd_agents(a: argparse.Namespace) -> int:
    from .agents.orchestrator import run_pipeline
    res = run_pipeline(sources=a.source, topic=a.topic, out_dir=Path(a.out), focus=a.focus, rounds=a.rounds,
                       dry_run=a.dry_run, model=a.model, pages=a.pages, max_chars=a.max_chars)
    if a.dry_run:
        print(res["dry_run"])
        return 0
    print(f"{'PASS' if res['report'].ok else 'FAIL'}  {res['topic'].title}")
    print(f"  rounds: {res['rounds']}  verdict: {res['verdict']}")
    print(f"  → {res['index']}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="learnfig", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="run the science checker on a spec")
    c.add_argument("spec")
    c.add_argument("--json", action="store_true")
    c.add_argument("--no-simulate", action="store_true", help="skip time integration checks")
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("render", help="build figures and the page for a spec")
    r.add_argument("spec")
    r.add_argument("-o", "--out")
    r.add_argument("--no-checks", action="store_true")
    r.add_argument("--open", action="store_true", help="open the page in a browser")
    r.set_defaults(fn=cmd_render)

    s = sub.add_parser("simulate", help="integrate dynamics and/or propagate a shock")
    s.add_argument("spec")
    s.add_argument("--shock", action="append", help="var=+0.1 or var=10%% (repeatable)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_simulate)

    n = sub.add_parser("new", help="scaffold a new topic spec from the template")
    n.add_argument("slug")
    n.add_argument("--title")
    n.add_argument("--dir", default="topics")
    n.add_argument("--force", action="store_true")
    n.set_defaults(fn=cmd_new)

    e = sub.add_parser("examples", help="render every spec under a directory and build a gallery")
    e.add_argument("--dir", default=str(ROOT / "examples"))
    e.add_argument("-o", "--out", default="build")
    e.add_argument("--title", default="Learning figures: examples")
    e.add_argument("--open", action="store_true")
    e.set_defaults(fn=cmd_examples)

    g = sub.add_parser("agents", help="read sources → spec → check → figures, with Claude")
    g.add_argument("source", nargs="*", help="text, markdown, yaml or pdf files")
    g.add_argument("--topic", required=True)
    g.add_argument("--focus", help="what the figures should explain (one sentence)")
    g.add_argument("-o", "--out", default="build/agents")
    g.add_argument("--rounds", type=int, default=2, help="max check→revise rounds")
    g.add_argument("--model", default=None, help="override the model for all roles")
    g.add_argument("--pages", help="PDF page range, e.g. 57-80")
    g.add_argument("--max-chars", type=int, default=120000)
    g.add_argument("--dry-run", action="store_true", help="print the prompts, call nothing")
    g.set_defaults(fn=cmd_agents)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
