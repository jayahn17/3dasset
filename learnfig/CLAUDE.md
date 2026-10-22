# learnfig — learning-figure template

This repository is a template for generating textbook-quality figures for any learning topic.
Everything flows from a **topic spec** (YAML) through a **science checker** into **figures and a page**.

- Skill: `.claude/skills/learning-figures/SKILL.md` (read it before making figures).
- Subagents: `.claude/agents/{concept-reader,science-checker,figure-writer}.md` — read → check → write.
- Engine: `learnfig/` (pure Python + PyYAML; `anthropic` only for `learnfig agents`).
- Specs: `spec/TEMPLATE.topic.yaml` (annotated), `examples/` (generic + Zohdi drone-book suite), `topics/` (yours).
- Outputs: `build/<id>/index.html`, `figures/*.svg`, `tree_steps.html`, `scene3d.html`, `report.md`.

Commands: `make setup`, `make test`, `make examples`, `make serve` (http://localhost:8731),
or `learnfig check|render|simulate|new|examples|agents`.

Conventions
- Every variable has a unit; equations are checked dimensionally — fix errors, do not silence them.
- Hand-written relations carry a one-sentence `mechanism`; it is printed in the figure key.
- Captions and notes state only what the engine computed (see `report.md`).
- Add a topic as `topics/<id>.yaml`; add an example only if it demonstrates a new feature.
- Keep renderers deterministic and dependency-free; new figure types go in `learnfig/render/` and
  are registered in `learnfig/pipeline.py`.
