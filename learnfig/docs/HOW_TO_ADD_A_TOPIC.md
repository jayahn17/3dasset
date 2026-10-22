# How to add a topic

1. Scaffold: `learnfig new heat_exchanger --title "Counter-flow heat exchanger"` → `topics/heat_exchanger.yaml`.
2. Fill in variables first. For each: id, name, symbol, **unit**, kind, default. Write the
   equations of derived quantities; the loader draws their arrows for you.
3. Add only the relations that equations do not imply, each with a `mechanism` sentence.
4. If the topic evolves in time, add `dynamics.rates`; add `events` for the moments a teacher
   would point at ("boils", "apex", "stall"); add `invariants` for anything conserved.
5. Pick one `cascade.shock` worth asking about ("what if the inlet temperature rises 10 %?").
6. If the topic contains an algorithm or decision procedure, add a `tree`.
7. `learnfig check topics/heat_exchanger.yaml` — fix errors, read warnings.
8. `learnfig render topics/heat_exchanger.yaml -o build/heat_exchanger --open` — look at it.
9. Write `summary`, `notes`, and per-figure `title`/`caption` (or let the figure-writer draft them).

Tips
- Quote any flow-style value containing a comma or colon: `equation: "max(a, b)"`, `title: "A: B"`.
- Convert units explicitly in formulas (`R/1000` for mm → m); the checker notes the scale.
- A `state` may have an `equation`: it is the initial condition (e.g. `vy: boost*sqrt(G*M/r0)`).
- Delays and gains are in cascade units ("step" by default); keep them relative and simple.
- For a whole book, keep one spec per teachable idea (see `examples/zohdi_drone_book/`) and
  render the folder with `learnfig examples --dir topics -o build`.
