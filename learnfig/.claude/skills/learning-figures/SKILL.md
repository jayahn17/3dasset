---
name: learning-figures
description: Generate textbook-quality learning figures for any topic from a declarative topic spec — variable-interaction graphs (every node, how each variable affects the others), influence matrices, tree-algorithm step visualizations, chain-reaction timelines, and 3D/VR timelines — with a science checker (units, signs, feedback loops, conservation laws, simulation) and a reader→checker→writer multi-agent loop. Use when asked to explain, teach, visualize or make a figure for a concept, a system of variables, a mechanism, a chain reaction, an algorithm on a tree, or a textbook/paper section.
---

# Learning figures

You turn a topic (a textbook section, a paper, lecture notes, or a request like "explain how X
affects Y") into a checked, numbered set of figures with captions. The engine is deterministic;
your job is the modelling and the judgement. Never draw a figure by hand when the spec can
express it.

## The loop

1. **Read** the source (or the user's description) and write a topic spec
   (`spec/TEMPLATE.topic.yaml` is annotated; `reference/spec-format.md` is the contract).
   Keep 4–14 variables, every variable with a unit, every hand-written relation with a
   one-sentence mechanism, equations in Python syntax, dynamics as `d(var)/dt`, conserved
   quantities as `invariants`, decision procedures or recursive algorithms as `tree`.
2. **Check**: `learnfig check topics/<id>.yaml`. Fix every error before rendering; read
   every warning. Dimensional errors, sign contradictions and violated invariants mean the
   figure would teach something false. `reference/checks.md` explains each code.
3. **Render**: `learnfig render topics/<id>.yaml -o build/<id>` and open `build/<id>/index.html`.
   Look at the actual page (browser or screenshot) before calling it done: label collisions,
   an empty panel, a cascade that dies after one hop, an event that never fires.
4. **Review** with the science-checker subagent (`.claude/agents/science-checker.md`): give it
   the spec, the report and the figure list, not your conclusions. Apply real findings, re-run.
5. **Write**: titles, captions and "what to notice" bullets are part of the figure
   (`figures[].caption`, `notes`). The figure-writer subagent drafts them from the final spec and
   report; captions never contain numbers that are not in the report.

For a long source (a whole chapter or book), split into one spec per teachable idea and render
them together with `learnfig examples --dir topics -o build` for a gallery.

## Multi-agent protocol

Roles are separated so that nobody grades their own work:

| role | input | output | agent |
|---|---|---|---|
| concept-reader | source text, topic, focus | topic spec (YAML) | `.claude/agents/concept-reader.md` |
| science-checker | spec + automatic report + values | verdict, issues, corrected spec | `.claude/agents/science-checker.md` |
| figure-writer | final spec + report + figure list | titles, captions, notes | `.claude/agents/figure-writer.md` |

Run them as subagents in that order; the checker may loop with the reader at most twice.
The same loop runs headless with the API: `learnfig agents --topic "..." source.pdf --pages 57-80 -o build/x`
(`--pages` are PDF page indices, front matter included, not printed page numbers; check with `pdftotext -f N -l N`).
Both paths produce `agents_log.md` / your notes so a reader can audit what was changed and why.

## Quality bar (what "textbook figure" means here)

- One idea per figure; the caption says what to look at and what it means.
- Signs are visible without reading: blue + / red −, dashed arcs for feedback, numbered callouts
  tied to a key that states the mechanism in words.
- Units everywhere; a derived value is shown with its number at the defaults.
- A chain reaction is shown as a timeline with causes linked to effects, never as a list.
- An algorithm is shown as the state of the structure at each step (what is being examined, what
  was recorded, what is waiting), plus a static frame with the visit order for print.
- Nothing is asserted that the engine did not compute: no invented numbers, no caption claims
  that contradict the report.

## Commands

```bash
learnfig new <slug> --title "..."           # scaffold topics/<slug>.yaml from the template
learnfig check topics/<slug>.yaml           # science checker (exit 1 on errors)
learnfig render topics/<slug>.yaml -o build/<slug> --open
learnfig simulate topics/<slug>.yaml --shock var=+10%
learnfig examples --dir topics -o build     # everything + gallery
learnfig agents --topic "..." SOURCE... --pages A-B -o build/<slug> [--dry-run]
```

## For learners (the mindset, not just the tool)

`docs/LEARNER_GUIDE.md` states the method as seven questions anyone can ask of any topic — quantities
and units, who affects whom, how strongly, where the loops are, what evolves and what is conserved,
what happens after a shock, what the procedure is — and how to read each figure. When a user is a
student rather than an author, teach the questions alongside the figures: name which question each
figure answers, and let them predict the cascade before they see it.

## Benchmark suites

`examples/` carries three suites that exercise every figure type on real textbook material:
`zohdi_drone_book/` (ME 244 drone-swarm primer, one spec per chapter idea), `einstein_relativity/`
(Einstein's 1920 *Relativity*, public domain: Lorentz transformation, velocity addition and Fizeau,
mass–energy, the tests of general relativity, the simultaneity thought experiment, the finite
unbounded universe) and `open_physics/` (standard introductory topics). Use them as patterns: copy
the nearest one and change the physics.

Reference material: `reference/spec-format.md`, `reference/checks.md`, `reference/figure-types.md`,
`docs/LEARNER_GUIDE.md`.
