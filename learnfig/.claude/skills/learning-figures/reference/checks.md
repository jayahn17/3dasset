# Check codes

Errors block a PASS; warnings and notes do not. Codes are stable so you can grep for them.

| code | level | meaning / fix |
|---|---|---|
| duplicate-id | error | two variables share an id |
| unknown-name | error | an equation, rate, condition or shock uses an undeclared name |
| bad-equation / bad-rate / bad-condition | error | expression does not parse or uses disallowed syntax |
| dangling-relation | error | relation endpoint is not a variable |
| bad-unit | error | unit string cannot be parsed |
| dimension-inconsistent | error | adding/comparing different dimensions, or `sin(x)` of a dimensioned x |
| unit-mismatch | error | the equation's dimensions differ from the declared unit |
| rate-unit-mismatch | error | `d(var)/dt` must have units (var unit)/(time unit) |
| sign-contradiction | error | a declared sign disagrees with ∂target/∂source at the defaults (the formula wins) |
| non-finite / blow-up | error | a value or trajectory is inf/nan; fix the formula or the step size |
| invariant-violated | error | a conserved quantity drifts more than its tolerance |
| not-a-bst / unknown-algorithm / algorithm-failed / bad-tree | error | tree section problems |
| unknown-key | warning | unrecognised key, usually a comma inside a `{...}` value — quote it |
| unknown-sign | warning | hand-written relation without a sign |
| isolated | warning | variable with no arrows (constants used only in invariants/events are exempt) |
| default-out-of-range / no-default / unresolved | warning | missing or inconsistent numbers |
| event-never-fires / event-not-fired | warning | an event has no trigger, or never triggered in the simulated window |
| huge-values | warning | trajectory exceeds 1e12 |
| unit-scale | info | dimensions agree, scale differs (explicit conversion in the formula, or a bug) |
| implicit-unit | info | a bare literal was added to/compared with a dimensioned quantity |
| sign-inferred / flat-sign / loop-polarity-unknown / no-feedback / invariant-ok | info | bookkeeping |
