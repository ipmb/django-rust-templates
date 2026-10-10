# Step 5, slice 3 — the prediction, written before the code

Slice 1's §3 named three things for step 5's next slice: `ExtendsNode.render` off
the delegate, a loop whose body holds a delegate sealing anyway, and the
per-render `get_nodes_by_type(BlockNode)` walk cached.  Slice 2 took the second.
**This file prices the other two and re-scopes the slice**, because both came
back small on the rows that matter and a third subject came back large.

Everything below is measured on the shipped slice-2 tree: **`_native` sha256
`1ec2563e926cb649`**, Django 6.0.9, `scripts/bench.py`'s own rows and data,
`.venv/bin/python -I`, same machine and session.

## 1. The two bullets the plan named, priced

Two new probes, both observers that assert the rendered bytes are unchanged.

`extends_frame_cost.py` measures `ExtendsNode.render`'s **own lines** — the frame
minus every `Template._render` nested inside it, by pausing the open frame rather
than by subtracting two tables.  (The first version did subtract, and on
`real-world/complex` it read **-237.58 µs**: the outer frame's nested render
*contains* the inner `ExtendsNode`, so the subtracted term contained the term it
was subtracted from.  A negative time is the arithmetic telling on itself, and
the file carries the correction.)

| row | render µs | own µs | share | `find_template` µs |
|---|---|---|---|---|
| `real-world/normal` | 140.9 | 5.58 | 4.00% | 1.78 |
| `real-world/complex` | 563.9 | 10.53 | 1.87% | 3.54 |
| `django-only/realistic` | 1017.1 | 9.27 | 0.91% | 2.73 |
| `micro/extends` | 11.6 | 4.83 | 41.82% | 1.46 |
| `real-world/large` (control) | 206.5 | 0.00 | 0.00% | 0.000 |

`block_walk_cost.py` prices the third bullet — the same measurement slice 1's §3
called "invisible in every column":

| row | render µs | walk µs | share | node visits/render |
|---|---|---|---|---|
| `real-world/normal` | 142.4 | 3.10 | 2.18% | 6.0 |
| `real-world/complex` | 536.0 | 3.22 | 0.60% | 6.0 |
| `django-only/realistic` | 1016.8 | 5.64 | 0.56% | 10.1 |
| `micro/extends` | 12.3 | 2.86 | 23.19% | 6.0 |
| `real-world/large` (control) | 210.9 | 0.00 | 0.00% | 0.0 |

**What this says.**  The per-render walk is real but small — 3–6 µs — because the
parent nodelist it walks is small (6 nodes on `normal`), and the frame that
contains it is smaller still once the walk re-entry is taken out.  `own us` is
the *ceiling* on bullet 1, and `find_template` is inside that ceiling and is a
Python loader lookup Rust would still call.  **Subtracting the one from the
other: bullet 1 is worth at most 4.00% on `normal`, 1.87% on `complex` and 0.91%
on `realistic`** — against a cross-build band whose max is 2.42%
(`cross_build_floor.slice2.txt`).  It is decisive on one row only, `micro/extends`
at 41.82%, and that row is 11.6 µs long.

So `out deleg` falling on `normal` — slice 1 §3's stated reason for bullet 1 — is
a **structural** consequence and not a time.  The bytes that come back from a
delegate are not the cost of the delegate, which is the same distinction
`applied:`/`body xings:` was built for one level down.

**Neither bullet is withdrawn; both are re-ranked below the subject in §3, and
bullet 3 rides along with it** — the block walk is a compile-time cache this
slice's own mechanism has a place for, and 2.18% is above nothing.

## 2. The hypothesis the plan did not have, and two dead ones before it

`real-world/complex` is the **largest render loss on the whole board against
jinja2** — 523.18 µs against 341.65, **1.53×**, +181.5 µs — and the largest
absolute one.  Two explanations were priced and both died.

**Dead 1: the 73 `IncludeNode`s.**  `include_swap.py` replaces
`{% include "partials/line.html" %}` with that template's own text, leaving the
output bytes identical (asserted, and rust == jinja2 on every variant).

| variant | ours µs | jinja2 µs | ratio |
|---|---|---|---|
| include | 525.5 | 342.1 | 1.54 |
| inlined | 445.3 | 184.5 | 2.41 |

Removing the includes costs **jinja2 157.6 µs and us only 80.3**.  The include
path is jinja2's disadvantage, not ours; it is part of why we are as close as we
are.  (The `render_context.dicts[0]` cache keyed by the node is ours and jinja2
has no counterpart.)  The hypothesis is dead in the direction opposite to the one
it was raised in.

**Dead 2: the inheritance frames.**  §1: 10.53 µs of 563.9.

**Alive: the nested `{% for %}`.**  `complex_ladder.py` walks one construct at a
time off the same page, both engines, output hashed per rung and rust == jinja2
checked on every one:

| rung | ours µs | jinja2 µs | ratio | Δ ours | Δ jinja2 | Δ(ours−jinja2) |
|---|---|---|---|---|---|---|
| full | 524.8 | 338.9 | 1.55 | — | — | — |
| no-include | 445.8 | 182.8 | 2.44 | −78.9 | −156.2 | **+77.2** |
| no-inner | 131.9 | 44.1 | 2.99 | −314.0 | −138.7 | **−175.3** |
| no-outer-head | 105.6 | 28.5 | 3.70 | −26.2 | −15.5 | −10.7 |
| bare-section | 30.8 | 18.8 | 1.63 | −74.8 | −9.7 | −65.1 |

The gap is the inner loop's body and the inner loop itself.

**Why, in the code.**  `sealable` refuses `Act::For(_)` **unconditionally**
(`crates/template-py/src/lib.rs:3620-3622`): "A nested `{% for %}` reads
`parentloop` out of the very dict being elided, so it refuses unconditionally."
The refusal is in the *read* pass, which `seal` applies to the whole body before
rewriting it, so a body containing a nested loop refuses **and the refusal
propagates to the enclosing loop**.  In `complex` that means the outer
`{% for s in sections %}` cannot seal, so `plan_for` returns `None` and the
`ForNode` is delegated — and Python's `ForNode.render` then runs the inner loop,
which the walk never sees at all.  The census reads it: `ForNode=1` delegated,
`body xings: 0`, `applied: upper=73` routed and none computed.

**It is the wrong refusal.**  The `parentloop` read is a property of
`ForNode.render`, not of the template's text: a nested `{% for %}` that is
**native** never reads `context["forloop"]` at all, and one that is not native
reads it only because it is writing Django's state.  Slice 2 already built the
thing that makes this safe — `ForPlan::context_state`, the loop that writes
Django's per-iteration state *because* something inside it reads it.

**So the rule generalises exactly as slice 2's did.**  `holds_delegate` asks
"does this body hold a node that reads the context"; `Act::For` is such a node,
for the same reason `Act::Delegate` is.  A loop whose body contains one must
write the state; once it does, the nested loop is a node the walk can run.

**And it must write the state for a second reason, which is the one that makes
the rule *sufficient*.**  A native inner loop resolves its `sequence` — `s.lines`
— against the **context** (`run_for` calls `sequence.resolve(&self.context,
true)`), and `s` is the outer loop's variable.  A sealed outer loop keeps `s` in
its frame and not in the context, so the state write is what puts it where the
inner loop's sequence can find it.  That is also why no `Frame` parent chain is
needed, and why `rewrite` need not descend into `Act::For`: **every read of an
outer loop variable from inside a nested loop is answered by the context, and the
enclosing loop writes it because the nested loop is there.**

## 3. What is predicted

The mechanism, stated as three edits:

1. `sealable`: `Act::For(_)` → `true`.
2. `holds_delegate` → true for `Act::For(_)` as well as `Act::Delegate`
   (the predicate is then "holds a node that reads `context["forloop"]`", and it
   wants renaming).
3. Nothing else.  `run_for` already ignores the enclosing frame, and the nested
   `Act::For` is run by `run`'s existing arm.

| row | measured now | predicted | why |
|---|---|---|---|
| `real-world/complex` render | 1.53× jinja2 | **0.55×–0.65×** | both loops native; the 72 delegates the inner loop holds stay delegates (slice 2's `run_stated`), which `include_swap.py` prices at +77.2 in our favour |
| `real-world/complex` parse | 0.52× | unchanged within band | no new parse work; `plan_for` is asked the same questions |
| every other row | — | **unchanged within the cross-build band** | `complex` is the only corpus row with a nested loop (checked over all 18 workloads: max `for`-nesting depth 2, 1 row) |
| our compile | — | unchanged within band | the descent pass is untouched |

The prediction is **falsifiable on one row and one number**, which is the point:
if `complex`'s render ratio does not move, the mechanism is wrong and the ladder
in §2 says what to look at instead.

**Predicted mechanism checks, independent of the wall clock** — the census, which
can see what the timing cannot:

* `real-world/complex` `delegated:` loses `ForNode=1`.
* its `body xings:` goes from **0** to non-zero (the 73 `|upper` currently routed
  and never computed), which is slice 3a's meter doing the job it was built for.
* `handled:` gains the inner loop's `TextNode`/`VariableNode` population.

## 4. The gates, named before the run

* `scripts/check.sh`: `template_tests` at 1547 by identity, the unit suites, the
  Rust suites, the differential harness, the transcription guard, packaging.
* `scripts/bench.py --compare before after --band A B` on the same build pair,
  with the `nondeterministic_rows` precondition it scans for.
* Block and loop semantics are the most heavily tested thing in the suite
  (`test_for.py`, `test_extends.py`, `recursive_templates/`), and `forloop.parentloop`
  is a name the suite exercises — a wrong `parentloop` is the failure this
  mechanism's whole justification is about, so it is the thing to look for first
  if a row goes red.
* `micro/extends` is the row where bullet 1 would be worth 41.8% and is
  **not** predicted to move this slice; it is the next slice's, if it is taken.

## 5. What this file is not

Not a claim that bullet 1 should never be done.  It is a claim that at 4.00%
`real-world/normal` / 1.87% `complex` / 0.91% `realistic` — and 41.8% of an 11.6 µs
row — it is *smaller than the band on two of the three rows the plan named it
for*, and that a measured 314.0 µs on the board's worst render loss comes first.
