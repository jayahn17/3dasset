# Topic spec format

A topic spec is YAML. Only the keys below are read; unknown keys are reported as warnings
(usually a YAML flow-mapping accident: a comma inside `{...}` splits the value — quote it).

```yaml
id: snake_case                 title: "Human title"          domain: physics|biology|cs|...
level: "undergraduate"         chapter: "3.2"                summary: two plain sentences
notes: ["take-away 1", ...]    sources: ["Book §3.2", ...]

variables:                      # 4–14; ids are Python identifiers
  - id: v0
    name: Launch speed          # shown on the node
    symbol: v₀                  # shown small, with the unit and value
    unit: m/s                   # SI symbols, prefixes (km, ms, kHz, µm) or a count noun (drones, USD)
    kind: parameter             # parameter | state | derived | constant | external
    default: 20                 # numeric; states need one (or an equation = initial condition)
    range: [0, 60]              # optional, checked against default
    equation: v0**2*sin(2*theta)/g     # derived variables; state: initial condition
    description: one sentence

relations:                      # only what equations/rates do NOT already imply
  - {from: wind, to: drift, sign: "+", gain: 1.2, delay: 2, mechanism: "why, in one sentence"}

events:
  - {id: impact, label: Impact, when: "y <= 0 and t > 0.1", stop: true}     # conditional
  - {id: start,  label: Engine on, t: 2.0, sets: {F: 30}}                   # scheduled, changes a value
  - {id: gust,   label: Wind gust, shock: {wind: 0.3}}                      # +30 % relative, cascades

dynamics:                       # optional ODEs; rate units must be (var unit)/(time unit)
  time: {unit: s, start: 0, end: 5, step: 0.005}
  rates: {x: vx, y: vy, vy: -g}
  integrator: rk4               # or euler

invariants:                     # conserved quantities, checked along the trajectory
  - {label: energy, expr: "0.5*m*(vx**2+vy**2) + m*g*y", tolerance: 1e-3}

cascade:                        # chain-reaction settings
  shock: {v0: 0.1}              # which variable, relative change
  default_delay: 1              # hops arrive this much later (in time_unit)
  damping: 0.6                  # gain used when a relation has neither gain nor an equation
  threshold: 0.02               # changes smaller than this stop propagating
  max_depth: 6
  time_unit: step

tree:
  kind: binary | general | decision
  title: "..."
  generate: {name: bst, keys: [50, 30, 70]}          # or fibonacci {n}, complete {depth}, minimax {depth, leaves}
  root: {value: 50, left: {...}, right: {...}}       # binary
  root: {label: "...", children: [{label: "..."}]}   # general
  root: {label: "Q?", branches: [{label: "yes", child: {...}}, {label: "no", child: {...}}]}   # decision
  algorithms: [{name: inorder}, {name: bst_search, key: 40}, {name: bst_insert, keys: [35]},
               {name: bfs}, {name: dfs}, {name: preorder}, {name: postorder}, {name: height},
               {name: minimax}, {name: decision, path: ["yes", "no"]}]
  # each algorithm may carry intro: "one sentence shown above the stepper"

figures:                        # optional; omit for the default set
  - {type: variable_graph, title: "...", caption: "...", show_values: true}
  - {type: influence_matrix}
  - {type: timeline, series: [x, y], max_series: 6}
  - {type: scene3d}
  - {type: tree, algorithm: inorder}
  - {type: spatial, path: [x, y], origin_radius: 6.371e6,      # path/origin optional
     live: [{label: "...", url: "https://...", field: velocity, unit: km/h, compare: v_circ, every: 5}]}
```

## Expressions
Python syntax over variable ids and `t`: `+ - * / ** % ( )`, comparisons and `and/or/not`
in conditions, functions `sin cos tan asin acos atan atan2 sinh cosh tanh exp log ln log10 log2
sqrt abs min max floor ceil round sign clip step heaviside pow`, constants `pi e tau inf`.
No attribute access, no indexing, no calls to anything else.

## Units
Base: m kg s A K mol cd. Derived: N J W Pa bar atm Hz C V ohm F T Wb L ha min h day yr eV cal
rad sr deg % ppm °C. Prefixes y…Y. `1` or empty = dimensionless. Any other token becomes its own
custom dimension (`rabbits`, `strings`), so `rabbits/yr` and `foxes` cannot be added by mistake.
Equations are evaluated on the numeric defaults *in the declared units*; convert explicitly in
the formula (`R/1000` for mm → m) — the checker reports the scale as a note, not an error.

## What the loader adds for you
- An arrow for every variable an equation or rate law depends on (`auto` relations), with the sign
  taken from the numeric partial derivative at the defaults.
- Feedback loops and their polarity (R = reinforcing, B = balancing).
- Values of every derived variable at the defaults, elasticities for the influence matrix.
