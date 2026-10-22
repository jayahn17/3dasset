# The learnfig mindset: how to understand anything as a system

learnfig is a tool, but the habit it encodes is older than any tool. Every physical law, every
model in a textbook, every algorithm can be understood by asking the same seven questions in the
same order. The spec format is just those questions written down; the figures are their answers
drawn. Use this guide when you study a chapter, a paper, or a new idea, with or without the software.

## The seven questions

1. **What are the quantities?** Name every variable the explanation needs, and nothing more
   (4 to 14 is the range a mind and a page can hold). For each: what is it, what **unit** does it
   carry, and what **kind** is it: a *parameter* you choose, a *state* that evolves, a *derived*
   quantity fixed by the others, a *constant* nature fixes, or an *external* input.
   If you cannot write the unit, you do not yet know what the quantity is.

2. **Who affects whom, and in which direction?** For every arrow, say the mechanism in one
   sentence and give it a sign: + if the effect rises when the cause rises, − if it falls.
   Equations give you most arrows for free; the ones you have to add by hand are where the
   understanding lives. *(Figure: the variable graph.)*

3. **How strongly?** A 1 % change in the cause moves the effect by how many percent? This is the
   elasticity, and it tells you which knobs matter. A formula with v² has elasticity 2 in v;
   something that appears under a square root has ½. *(Figure: the influence matrix.)*

4. **Where are the loops?** Follow arrows until you return to where you started. An even number of
   minus signs makes a **reinforcing** loop (growth, collapse, runaway); an odd number makes a
   **balancing** loop (regulation, oscillation, terminal speed). Most behaviour that surprises
   students is a loop they did not see. *(Figure: the dashed arcs and R/B labels.)*

5. **What evolves, and what stays the same?** Write the rates: d(state)/dt in units of
   (state unit)/(time unit). Then look for what is conserved: energy, momentum, angular momentum,
   a Lotka–Volterra constant, probability. A conserved quantity is a promise the simulation must
   keep; if it drifts, the model or the step size is wrong. *(Figure: the timeline, panel A.)*

6. **What happens if…?** Pick one shock a curious person would ask about (payload +20 %, launch
   speed +5 %, finer spray, warmer reservoir) and follow it hop by hop, with delays, until the
   change dies out or comes back around. This is the chain reaction, and it is the test of whether
   you understand the system or merely the formula. *(Figure: timeline panel B and the 3D scene.)*

7. **What is the procedure?** If the idea contains a decision rule, a derivation, or an algorithm,
   write it as a tree and walk it step by step: what is examined now, what has been settled, what
   is waiting. Recursion, search, and proofs all have this shape. *(Figure: the tree stepper.)*

## How to read the figures

| figure | read it as |
|---|---|
| Variable graph | "A pushes B up, B pulls C down." Follow colors, not positions. Numbered arrows have their reason in the key. |
| Influence matrix | A row is a cause, a column an effect. Blue cells move together, red cells move opposite, pale cells barely matter (the same colours as the arrows). |
| Timeline A | Each state against time. Vertical dashed lines are the moments worth a sentence ("apex", "impact", "peak"). |
| Timeline B | The shock is the diamond. Each later dot is a consequence of the dot an arrow points from; size is the size of the change. |
| 3D / VR scene | The same timeline you can walk around: lanes are variables, depth is time, height is magnitude. Scrub time to replay the cascade. |
| Tree stepper | Blue = recorded, orange ring = being examined, dashed green = waiting in the stack or queue, green = found or inserted. The note explains each step. |

## Using it as a study method

- **Before reading a chapter**, write the seven questions as empty headings. Fill them while you read.
- **After reading**, write the spec (or just the lists) and run `learnfig check`. The checker will
  tell you where your units disagree with your formulas and where your signs disagree with your
  equations; those are exactly the places you misunderstood.
- **Teach it back** using the figures: a good caption says what to look at and what it means, in
  the variables' names, with no number that was not computed.
- **Ask the "what if"** out loud before looking at the cascade figure, then compare.

## Why this works

A formula tells you a relationship; a system tells you a story: what you can change, what follows,
how fast, how far, and when it stops. The seven questions turn the first into the second. They are
the same questions a physicist asks of a new phenomenon, an engineer of a new design, an economist
of a new market, and a programmer of a new data structure. Learning them once, you can apply them to
everything in this repository's examples: a drone swarm, a satellite, a falling droplet, Einstein's
moving clocks, a binary search tree, and whatever you study next.
