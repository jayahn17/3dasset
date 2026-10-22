# Figure types

| type | shows | needs | files |
|---|---|---|---|
| variable_graph | every variable as a node; arrows cause→effect, blue +/red −, dashed feedback arcs, numbered callouts + key with mechanisms, kind legend, loop labels | ≥ 2 variables | figures/fig-variables.svg |
| influence_matrix | rows = cause, columns = effect; cell = % change in column per 1 % change in row (equation specs) or sign × gain (relation-only specs); diverging blue/red | relations or equations | figures/fig-influence.svg |
| timeline | A: each state/derived series vs time with event markers; B: chain reaction lanes after the shock, marker size = size of change, arrows cause→effect | dynamics and/or a shock | figures/fig-timeline.svg |
| scene3d | same timeline in three.js: lanes × time × magnitude, time scrubber, hover details, WebXR button | dynamics and/or a shock | scene3d.html (embedded in the page) |
| tree | static frame with visit-order numbers per algorithm + interactive stepper (prev/next/play, stack/queue state, notes) | tree section | figures/fig-tree-<alg>.svg, tree_steps.html |
| spatial | a WebXR room: variables as cities on a globe you turn by hand (pulses cause→effect), tap a city to unfold its card (value, equation, mechanisms, curve), the 2D figures on an arc, the state path on a plate (unroll = time as height), optional unit-checked `live:` feeds; Enter VR / Enter AR | ≥ 2 variables (path needs dynamics) | spatial.html (embedded in the page); see docs/SPATIAL_VR.md |

Every build also writes `index.html` (numbered figures, captions, keys, check report, values table),
`report.md`, `report.json`, `spec.resolved.yaml` (spec plus the auto relations).

Design rules baked into the renderers (do not fight them): one y-axis per panel, categorical hues
in fixed order, text in ink colors never series colors, thin marks, legends for ≥ 2 series, light
and dark themes from the same tokens, hover titles on every mark.
