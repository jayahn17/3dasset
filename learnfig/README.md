# learnfig — a template for textbook-quality learning figures

Describe a topic once, in a small YAML spec, and get a checked, numbered set of figures that
explain it: **every variable as a node and every influence as a signed arrow**, an **influence
matrix**, **tree algorithms step by step**, a **chain-reaction timeline**, and the same timeline
**in 3D / VR** — plus a page with captions, a key of mechanisms, and the science-check report.

The engine is deterministic Python (no chart library). A **reader → checker → writer**
multi-agent loop (Claude, via the API or as Claude Code subagents) turns sources into specs,
reviews the science, and writes the words. This repository is meant to be copied as a template
for any course, book or lab.

```
spec (YAML) ──▶ science checker ──▶ figures (SVG/HTML) ──▶ page with captions & report
   ▲   units · signs · loops · simulation · invariants          │
   └── reader (source → spec) ◀── checker (review) ◀── writer (captions) ◀──┘
```

## Quick start

```bash
make setup                     # uv/venv + pip install -e ".[dev]"
make examples                  # renders examples/ into build/ with a gallery
make serve                     # http://localhost:8731
```

or directly:

```bash
learnfig new heat_pump --title "Heat pump cycle"      # scaffold topics/heat_pump.yaml
learnfig check  topics/heat_pump.yaml                 # units, signs, loops, simulation, invariants
learnfig render topics/heat_pump.yaml -o build/heat_pump --open
learnfig simulate topics/heat_pump.yaml --shock T_out=+10%
```

With an API key (`ANTHROPIC_API_KEY` or `ant auth login`) the whole loop runs headless from a
source document:

```bash
pip install -e ".[agents]"
learnfig agents --topic "Water droplet settling (Stokes drag)" sources/book.pdf --pages 259-262 -o build/stokes
```

It writes `spec.v0.yaml` (reader), `spec.v1.yaml` (after the checker's revision), `spec.final.yaml`,
the figures, and `agents_log.md` with every verdict. Add `--dry-run` to see the prompts without calling anything.
`--pages` counts PDF pages (front matter included), not the numbers printed on the page.

## What a spec looks like

```yaml
id: predator_prey
title: Predator–prey cycles (Lotka–Volterra)
variables:
  - {id: x, name: Rabbits, unit: rabbits, kind: state, default: 10}
  - {id: y, name: Foxes,   unit: foxes,   kind: state, default: 2}
  - {id: alpha, name: Rabbit birth rate, unit: 1/yr, kind: parameter, default: 1.1}
  - {id: beta,  name: Predation rate,    unit: 1/(foxes*yr), kind: parameter, default: 0.4}
  - {id: period, name: Cycle period, unit: yr, kind: derived, equation: 2*pi/sqrt(alpha*gamma)}
relations:
  - {from: x, to: y, sign: "+", delay: 1, mechanism: "More rabbits means more food, so foxes breed faster."}
dynamics:
  time: {unit: yr, start: 0, end: 30, step: 0.005}
  rates: {x: alpha*x - beta*x*y, y: delta*x*y - gamma*y}
invariants:
  - {label: Lotka–Volterra constant V, expr: "delta*x - gamma*log(x/x_ref) + beta*y - alpha*log(y/y_ref)"}
cascade: {shock: {alpha: 0.2}}
```

The loader adds an arrow for every variable an equation or rate law depends on (sign from the
numeric partial derivative), finds the feedback loops and their polarity, evaluates every derived
value, integrates the dynamics, fires the events, checks the invariants, and propagates the shock.

Full contract: [`spec/TEMPLATE.topic.yaml`](spec/TEMPLATE.topic.yaml) (annotated) and
[`.claude/skills/learning-figures/reference/spec-format.md`](.claude/skills/learning-figures/reference/spec-format.md).

## Figures

| figure | what it shows |
|---|---|
| **Variable graph** | nodes = variables (color = kind, value + unit shown), arrows cause → effect, blue + / red −, dashed arcs = feedback, numbered callouts → key with the mechanism in words, loop labels (R/B) |
| **Influence matrix** | % change in each column per 1 % change in each row at the defaults (elasticities), or declared sign × gain; blue = rises with the cause, red = falls, as in the graph |
| **Timeline** | A: each state variable vs time with event markers · B: chain reaction lanes after the shock, marker size = size of change, arrows cause → effect |
| **3D / VR timeline** | three.js scene: lanes × time × magnitude, time scrubber, hover details, WebXR button |
| **Spatial room (VR / AR)** | the whole topic as a room: variables as cities on a globe you turn by hand, tap one to unfold its card on the table, the figures on an arc around you, the state path on a plate (unroll time into a helix), optional live feeds checked against the model's units; see [`docs/SPATIAL_VR.md`](docs/SPATIAL_VR.md) |
| **Tree algorithms** | static frame with visit-order numbers (print) + stepper (prev/next/play, stack/queue state, notes) for preorder, inorder, postorder, bfs, dfs, bst_search, bst_insert, minimax, height, decision |

Every page also carries the **science check** (errors block a PASS), the values table, take-away
notes and sources, in light and dark themes.

## The science checker

`learnfig check` is what makes the figures trustworthy:

- **Dimensional analysis** of every equation, rate law and invariant (SI, prefixes, and custom
  count dimensions such as `rabbits` or `strings`); `sin(x)` of a length is an error.
- **Sign checks**: a declared `+`/`−` that contradicts ∂target/∂source at the defaults is an error.
- **Graph checks**: dangling or isolated variables, feedback loops with polarity.
- **Simulation checks**: blow-ups, events that never fire, **conserved quantities that drift**.
- **Spec hygiene**: unknown keys (usually a comma inside a `{...}` value), missing defaults, ranges.

Codes are listed in [`reference/checks.md`](.claude/skills/learning-figures/reference/checks.md).

## Multi-agent loop

| role | reads | writes |
|---|---|---|
| **concept-reader** | source text, topic, focus | topic spec |
| **science-checker** | spec + automatic report + values | verdict, issues, corrected spec |
| **figure-writer** | final spec + report + figure list | titles, captions, "what to notice", notes |

Two ways to run it: `learnfig agents` (Python, `anthropic` SDK, streaming, adaptive thinking,
server-side refusal fallback on by default) or inside Claude Code with the skill
[`.claude/skills/learning-figures/SKILL.md`](.claude/skills/learning-figures/SKILL.md) and the
subagents in [`.claude/agents/`](.claude/agents). Nobody grades their own work: the checker never
drew the figure, the writer never chose the model.

## The mindset for learners

[`docs/LEARNER_GUIDE.md`](docs/LEARNER_GUIDE.md) states the method as seven questions anyone can ask of
any topic (quantities and units → who affects whom → how strongly → where the loops are → what evolves
and what is conserved → what happens after a shock → what the procedure is) and maps each question to
the figure that answers it. The specs are those questions written down; the checker is the teacher
who marks your units and signs.

## Examples and benchmark suites

`examples/` has two generic topics (predator–prey with loops and a conserved quantity; binary
search trees with five algorithms) and three benchmark suites in which **every spec exercises every
figure type** (variable graph, influence matrix, timeline, 3D scene, tree):

**`einstein_relativity/`** — Einstein, *Relativity: The Special and General Theory* (1920, public domain):

| spec | chapters | what the figures teach |
|---|---|---|
| `e_special_relativity_reasoning` | Part I | the embankment-and-train simultaneity experiment as a timeline; the map of the theory as a tree |
| `e_lorentz_clocks_rods` | XI–XII, App. I | the Lorentz factor, moving rods shrink and moving clocks run slow; proper time accumulating |
| `e_velocity_addition_fizeau` | VI, VII, XIII | classical vs relativistic addition; Fizeau's flowing water |
| `e_mass_energy` | XV | kinetic energy's expansion, inertia of energy, why nothing reaches c |
| `e_general_relativity_tests` | XVIII–XXIII, App. III | equivalence principle, rotating clocks, Mercury's 43″, light bending, red shift |
| `e_universe_structure` | XXX–XXXII, App. IV | the finite yet unbounded universe: radius from density, light going around |

**`open_physics/`** — standard introductory topics (cf. OpenStax University Physics, CC BY 4.0):
`damped_driven_oscillator` (regimes, Q, resonance) and `carnot_heat_engine` (efficiency, entropy, a finite reservoir running down).

**`zohdi_drone_book/`** — T. I. Zohdi's drone-swarm primer (ME 244), one spec per teachable idea:

| spec | book section | what the figures teach |
|---|---|---|
| `ch2_drone_vertical_climb` | §2.2, §3.3 | thrust-to-weight, quadratic drag, terminal climb speed; payload shock → longer mission |
| `ch3_swarm_interaction_rules` | §3.2–3.3 (eqs. 3.5–3.28) | the three urges (target / obstacle / member), their weights and decays, the per-step decision tree, the crowding → collision → cost chain |
| `ch3_genetic_algorithm` | §1.4, §3.4.2 | population / parents / refill trade-off, wall time, the generation recipe as a tree |
| `ch6_lidar_ranging` | Ch. 6 | time of flight, range error from jitter, swath, point density, return signal |
| `ch8_water_drop_stokes` | §8.3 (eqs. 8.3–8.13) | response time 2ρR²/9μ, terminal speed, airborne time, wind drift; finer spray shock |
| `ch9_satellite_orbit` | §9.2, §9.7 | two-body orbit, energy and angular momentum conserved to 1e-13, period from the launch speed |
| `ch1_time_stepping` | §1.2, §3.9–3.10 | the φ-method, explicit-step error vs step size, adaptive step control as a decision tree |
| `ch4_voxel_drone_frame` | Ch. 4 | voxel discrete elements: stiffness, critical time step, preflight stress test |
| `ch5_material_mixtures` | Ch. 5 | bounds on effective conductivity, load sharing, Joule heating, the multi-property cost |
| `ch7_digital_forest` | Ch. 7 | LiDAR canopy geometry → biomass, the digital-forest pipeline |
| `ch12_fragment_trajectories` | Ch. 12 (§12.3–12.5) | equal fragments share the launch speed; drag length ∝ size sets the range; which model to use |
| `ch13_ballistic_shielding` | Ch. 13 | membrane stretch and rupture, perforation criterion |

Render them all with `make examples` and open `build/index.html`.

## Repository layout

```
learnfig/            engine (model, load, expr, units, graph, simulate, checks, trees, render/, pipeline, cli, agents/)
spec/                TEMPLATE.topic.yaml, schema.json
examples/            generic topics + zohdi_drone_book/ suite
topics/              your specs (created by `learnfig new`)
tests/               pytest suite (engine, checker, trees, rendering, agent loop with a fake model)
docs/                ARCHITECTURE.md, HOW_TO_ADD_A_TOPIC.md, LEARNER_GUIDE.md, SPATIAL_VR.md
.claude/             skill, subagents, launch.json (preview server)
```

## Using this repository as a template

1. Copy it (or "Use this template" on GitHub), `make setup`, `make test`.
2. Put your sources in `sources/` (git-ignored) and your specs in `topics/`.
3. `learnfig examples --dir topics -o build` renders your whole course with a gallery.
4. Adjust the palette in `learnfig/style.py` if you have a brand; keep the rules in
   [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Requirements: Python ≥ 3.10, PyYAML; `anthropic` for the agents; `pdftotext` (poppler) or
`pypdf` for PDF sources; a browser for `scene3d.html` and `spatial.html` (three.js from a CDN), and a
WebXR headset (https or localhost) to step inside the room.

MIT licensed.
