# Architecture

```
topic spec (YAML) ──load──▶ Topic ──finalize──▶ + auto relations (from equations & rate laws, signs by ∂)
                                     │
                       ┌─────────────┴─────────────┐
                       ▼                           ▼
                run_checks (checks.py)        analysis (simulate.py)
                units · signs · graph         resolve values · elasticities
                events · tree · dynamics      integrate (RK4) · events · invariants
                invariants → Report           cascade (shock → delayed hops)
                       │                           │
                       └─────────────┬─────────────┘
                                     ▼
                         renderers (render/*.py) → SVG / HTML
      variable_graph · influence_matrix · timeline · tree (static + stepper) · scene3d (three.js/WebXR)
      spatial (WebXR room: concept globe + the figures above as panels + path plate + live feeds)
                                     ▼
                      figure_page.py → index.html (+ report.md/json, spec.resolved.yaml)
```

| module | responsibility |
|---|---|
| `model.py` | dataclasses: Topic, Variable, Relation, Event, Dynamics, Invariant, CascadeSettings, TreeSpec, FigureRequest |
| `load.py` | YAML/dict ↔ Topic, normalisation, unknown-key capture, `finalize_topic` (auto relations, sign inference) |
| `expr.py` | whitelisted AST evaluator (no attribute access, no calls outside the math whitelist), numeric partials |
| `units.py` | unit parser (SI, prefixes, custom count dimensions) and dimensional evaluation of expressions |
| `graph.py` | DAG layering with back-edge removal, barycenter ordering, simple-cycle enumeration, loop polarity |
| `simulate.py` | value resolution, sensitivities/elasticities, RK4/Euler integration with events, invariant drift, cascade |
| `checks.py` | the deterministic science checker → `Report` (errors/warnings/info, loops, values) |
| `trees.py` | tree construction/generators, layout, algorithm traces (`Recorder` → step dicts) |
| `style.py`, `svg.py` | tokens (validated palette, light/dark), SVG builder, text width estimates |
| `render/` | one file per figure type + page composer |
| `pipeline.py` | `build()` orchestrates checks → analysis → figures → page; `render_gallery()` |
| `agents/` | `LLM` protocol, `ClaudeLLM` (SDK, streaming, effort, server-side fallback), prompts, roles, orchestrator |
| `cli.py` | `learnfig check|render|simulate|new|examples|agents` |

## Design decisions

- **Spec first.** Everything a figure needs is declared once; renderers never guess. The same spec
  drives the checker, so a figure cannot be rendered from a model that fails dimensionally.
- **Deterministic engine, model for judgement.** Claude reads sources into specs, reviews science
  and writes captions; it never draws. That keeps figures reproducible and auditable (`agents_log.md`).
- **Auto relations.** Equations and rate laws already encode "who affects whom"; authors only add
  the causal links that formulas do not express, each with a mechanism sentence that lands in the key.
- **Custom dimensions.** `rabbits`, `strings`, `USD` are dimensions, so ecology/CS/economics specs
  get the same consistency checks as physics.
- **Chain reaction = delayed graph propagation.** Gains come from declared `gain`, else the direct
  elasticity of the target's formula, else sign × damping; the cascade stops on threshold, depth,
  horizon or event cap, so loops terminate.
- **No chart library.** SVG is written directly against a validated palette so the output is
  identical everywhere and embeddable in any page; three.js is loaded only by `scene3d.html` and
  `spatial.html`.
- **The room reuses the page.** `spatial` is rendered last and carries the other figures' SVGs as
  panels, so the 2D figures and the room can never disagree. Live feeds pass the same unit check
  as equations before the page may fetch them (see `docs/SPATIAL_VR.md`).

## Extending

- New figure type: add `render/<type>.py` returning SVG/HTML, register in `pipeline.build()` and
  `model.FIGURE_TYPES`, document in `.claude/skills/learning-figures/reference/figure-types.md`.
- New check: add a `_check_*` function in `checks.py`, call it from `run_checks`, add its code to
  `reference/checks.md` and a test.
- New tree algorithm: add `trace_<name>` to `trees.py` and register it in `ALGORITHMS`/`ALGORITHM_TITLES`.
- New unit: extend `_DERIVED` in `units.py`.
