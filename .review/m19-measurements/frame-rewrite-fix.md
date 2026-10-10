# The sealed-loop frame rewrite: the fix, and what it costs

**Status.** Found by the adversarial reviewer (`scripts/adversarial_reviewer.sh`,
codex, `VERDICT: FAIL` on the step-5 slice-3 tree); a first fix landed as
`35ff15c`; the re-dispatched reviewer returned `VERDICT: FAIL` again with two
**BLOCKING** findings against that fix, both naming a route its docstring had
argued away; the second fix is in `crates/template-py/src/lib.rs` in this tree.
Verified by **59** differential cases, two census/read-count rows in
`tests/test_interposition.py`, a 17-row probe, and the 1547 vendored-suite tests;
priced by two alternating A/Bs, `frame_rewrite_ab.txt` (the gate, against slice 3)
and `frame_rewrite_ab2.txt` (the ordered pass, against the gate).

## 1. The defect

`seal` has a read pass and a write pass. `sealable` decides whether a body may be
sealed — since step 5 slice 2 that includes a body holding a node the walk hands
back to Python (`Act::Delegate`) — and `rewrite` then moves every read of the loop
variable in that body onto the *frame*, the value the current iteration resolved.

The rewrite is sound exactly while `context[loopvar]` would give the same thing as
the frame. `Walk::run_stated` writes `context[loopvar] = frame.item` at the top of
every iteration, so a write from an earlier iteration is gone before the next read.
What survives that is a write made **inside this iteration, by this body** — and a
delegate is arbitrary Python with the context in hand.

`{% cycle %}` is the built-in that does it. `CycleNode.render` calls
`context.set_upward` (`defaulttags.py:104-114`), which lands the name in the
innermost layer already holding it — the enclosing loop's own. So

```
{% for s in sections %}{% cycle "X" as s silent %}{{ s }}{% endfor %}
```

is `X` in Django and was `old` here: the `{{ s }}` had been rewritten to the frame,
which holds what the sequence yielded.

**It is older than the slice that made it visible, and that is measured rather
than argued.** Built from step 5 slice 2's own source (`5a9d4ef`) the harness reads
**2 of 52 differ** (`sha256:1ec2563e926cb649` in `differential-fix.txt`) — the two
simplest rows, the second of which shows the rebinding surviving `{% endfor %}`
because `set_upward` wrote it past the loop. Slice 3 (a nested `{% for %}` seals)
added the other two, whose outer body needs a nested loop to seal at all. So the
count over the three artifacts is **2 → 4 → 0**: present before the slice that
exposed it, doubled by it, gone after the fix. Nothing in the suite could see any of
it: a wrong render is a wrong *string*, and the 1547 tests compare bytes on
templates they carry themselves, not on this one.

## 2. The first fix, and the two findings that falsified it

`may_write_context(body)` — true for `Act::Delegate`, descending into an
`Act::For`'s `body` and `empty` and into an `Act::If`'s branches, false for
`Act::Text`/`Act::Emit` — gated the write pass:

```rust
if !may_write_context(body) {
    for slot in body.iter_mut() {
        rewrite(&mut slot.act, loopvar);
    }
}
```

A body that failed the predicate was **still sealed**; it kept reading its loop
variable out of the context, which the stated loop was writing anyway. Two of the
three exclusions in that predicate's docstring were *arguments* rather than code,
and both were false. They came back as the second adversarial round's two
BLOCKING findings:

* **"a filter cannot write the context."** `FilterExpression.resolve` hands a
  filter the arguments the **template** gave it, and the context object is a value
  a template can name — `{{ xs|rebind_up:ctx }}` reaches Python holding the
  context (`tests/differential/ctxwrite_library.py`). `takes_context` is
  `simple_tag`'s protocol and a filter never gets the context *implicitly*, which
  is what the sentence had confused for "never".
* **"a nested loop the walk runs cannot write its ancestor's name… and its
  sequence is read."** The layer half is true; the sequence half is not. An
  `Act::For` resolves its sequence in Python with the context in hand
  (`lib.rs:4799`, `.call_method1("resolve", (&self.context, true))`), so a nested
  loop is a **writer at the position of its sequence** — and the predicate
  descended into `body` and `empty` and never looked at `sequence`.

Both are reproducible as bytes, which is what made them BLOCKING rather than
theoretical: seven rows of the differential corpus diverge on the `35ff15c` build
with them (`differential-fix2.txt`, "7 of 59").

**And there was a third thing in the same review that no widening of the
predicate could have repaired.** Django's `ForNode.render` opens
`with context.push():` at `engine/defaulttags.py:198` and resolves the sequence on
the *next* line, `values = self.sequence.resolve(context, ignore_failures=True)`
(`:199`). The sequence is therefore resolved **inside the loop's own layer**: a
filter can read that depth (`len(context.dicts)`) and can write into it, and
Django's write is popped by `{% endfor %}`. The walk resolved first and pushed
after, so a sequence filter saw one layer too few and a write from `context[name]`
landed a layer too shallow — and the *first* fix had not touched either, because
`may_write_context` was a predicate over a body and the layer is not in the body.
The reviewer's words for it are the reason it is a separate change: "widening
`may_write_context` alone cannot repair it".

**It is older than the slice that exposed it, and that is measured.** The probe
(`.review/m19-measurements/ctx_write_channels.py`) reads D2's three rows as
divergent on **all three** earlier artifacts — the slice-2 build
(`sha256:1ec2563e926cb649`) included: `top_level_sequence_filter_writes_only_its_own_layer`
is `[X]` against Django's `[old]`, and `sequence_filter_sees_the_loops_layer` is
`2` against Django's `3` on a *top-level* loop and on an empty one. Those three
rows need no nesting and no seal, so they belong to the frame route the walk has
had since `888b5a9`. The nested-sequence rows (D1's) appear only once slice 3
made a nested `{% for %}` sealable, which is why the corpus count moves
**5 → 11 → 7 → 0** across the four artifacts (§4).

## 3. The second fix: an ordered write pass, and the loop's own layer

**Two changes, and they answer the two different kinds of finding.**

**§3.1 The write pass is ordered rather than a property of the body.**
`rewrite_body` now rewrites a loop-variable read to the frame **up to the point in
the body where the walk hands Python the context**, and leaves every read after
that on the context:

```rust
fn rewrite_body(body: &mut [Slot], loopvar: &str, mut written: Written) -> Written {
    for slot in body.iter_mut() {
        match &mut slot.act {
            Act::Text(_) => {}
            Act::Delegate | Act::For(_) => written.python_seen = true,
            Act::Emit(emit) => {
                if written.python_seen { written.on_context = true; }
                else { rewrite_head(&mut emit.head, loopvar); }
            }
            Act::If(plan) => { /* condition heads the same way, branches recursed */ }
        }
    }
    written
}
```

The boundary set is `{Act::Delegate, Act::For(_)}` — a node the walk does not
model, and a nested loop whose sequence is Python resolved *inside the layer this
loop's body runs in*. `Act::If` recurses rather than being a boundary, because a
branch is the same body one level deep and its answer is handed back.

Position is the whole argument, and it makes the pass **finer** than the predicate
rather than merely different: rendering is sequential and the stated loop writes
`context[loopvar] = frame.item` at the top of every iteration, so the context
equals the frame at the body's first node and can only have been moved by Python
that has already run. A read *before* the first such node is frame-correct
whatever follows it — which the predicate denied itself. On
`django-only/realistic` the `{% blocktranslate %}` delegate comes after two
reads, and those two were being kept on the context for nothing.

The pass returns `Written { on_context, python_seen }`, and `plan_for` computes

```rust
let context_state = written.on_context || needs_context_state(&body);
```

so the coupling that the first fix had to *argue* — "a read left on the context is
a read the loop owes Django's state write" — is now enforced by construction: if
the pass ever leaves a read on the context where `needs_context_state` does not
look, the loop is stated anyway.

**§3.2 The loop's layer is Django's, opened once per loop, and opened before the
sequence resolves.** `run_for` pushes, then calls `run_loop_body` inside the
layer, so the sequence resolve, the length, the reversal and the empty arm are all
where Django's are; `delegate_slot` no longer pushes a layer of its own but writes
the frame's state *into the loop's* layer before handing the delegate the context.
Two consequences that are not conveniences:

* `parentloop` has to be read **before** the push, and it is: `Walk::parentloop`
  is called by `run_for`, and the value is carried on the `Frame`. Reading it
  after the push would find the `forloop` dict this loop wrote on an earlier
  iteration and name the loop its own parent.
* **Option B, not Option A, and for a measurable reason.** The alternative was to
  leave `delegate_slot` pushing its own layer and to widen the state write to
  reach it. That is one layer too deep on the refused-node path: Django's delegate
  runs in the loop's layer (`{% for %}` pushed it), and a second push is visible
  to anything that counts layers — the same `len(context.dicts)` the probe reads.
  Pushing once per loop is what makes the count match Django's exactly.

**What the ordered pass cannot see is stated, not closed.** Two Python routes into
a sealed body have no static position: a **refused** node — a `{{ … }}` whose
lookup missed — is handed back to Python with the context in hand, and a *value*
that holds the context writes the loop variable when it renders (`{{ s }}{{ s }}`
where `__str__` calls `set_upward`). That route is older than step 5 (the frame
route landed `888b5a9`), it is measured in the probe's rows 15–17, and it is why
`on_context` is not the same flag as `python_seen`: a read left on the context is
a read the loop owes a state write, and a write the pass cannot see is a residual.
The sketch for closing it is Option C — a `Refused` that marks the body's
position, and a value write that no plan can predict would need the render to
notice the write and re-read — and neither is worth its cost against three rows
that need a value carrying the context.

**One route is ruled out by a type rather than an argument.** The pass treats an
`Act::Emit` with a Rust-answered filter as *not* a boundary, so the claim "no Rust
filter can write the context" is load-bearing. It is true by construction:
`FilterCall` (`crates/template-core/src/filters/host.rs:42-48`) carries the host,
`autoescape` and `use_tz` — and deliberately not the context. A filter body
physically cannot reach the context; the only Python the walk runs on this path is
a dunder on a value the template supplied, which is the residual above.

## 4. What the evidence is

### 4.1 The first fix's four rows, and the two corpora

The corpus grew with the second round's findings, and the two sizes are kept side
by side rather than merged, because each is a measurement of the corpus *as it
was*:

| corpus | artifact | reads |
|---|---|---|
| 52 cases | `slice2` `sha256:1ec2563e926cb649` | **2 of 52 differ** — `cycle_rebinds_the_loop_variable` `X`/`old`, and the same row surviving `{% endfor %}` `X[]`/`old[]` |
| 52 cases | `slice3` `sha256:e6a72702ba5877cb` | **4 of 52 differ** — the two above plus `nested_for_keeps_an_ancestor_rebinding` `XXXX`/`XXXold` and `cycle_then_nested_for_reads_the_rebound_name` `XX`/`Xold` |
| 52 cases | `35ff15c` `sha256:b0c073c33ecdfe6c` | **52 identical** |
| **59 cases** | `slice2` `sha256:1ec2563e926cb649` | **5 of 59** |
| **59 cases** | `slice3` `sha256:e6a72702ba5877cb` | **11 of 59** |
| **59 cases** | `35ff15c` `sha256:b0c073c33ecdfe6c` | **7 of 59** |
| **59 cases** | this tree `sha256:0013195e7af12bb4` | **59 identical** |

`differential-fix.txt` holds the 52-case transcript and `differential-fix2.txt`
the 59-case one, each naming the divergences per artifact. **The 52-case count is
left as it was measured** — `2 → 4 → 0` — rather than restated over 59, because
that is what the first fix was accepted on. The seven rows the count moves by are
§4.2's.

The four original rows, and the shape of the defect each one carries:

| case | why it is in the set |
|---|---|
| `cycle_rebinds_the_loop_variable` | the defect itself, smallest form |
| `cycle_rebinds_the_loop_variable_and_survives_the_loop` | the rebinding is still `X` after `{% endfor %}` — it was written *upward*, past the loop |
| `nested_for_keeps_an_ancestor_rebinding` | the inner loop's body rebinds the **outer** loop's variable |
| `cycle_then_nested_for_reads_the_rebound_name` | the rebound name is the inner loop's *sequence* |

### 4.2 The second round's seven rows

Added to `tests/differential/cases.py` with the fix, and they are the two
findings plus the layer count:

| case | what it is | pre-fix reading (59-case) |
|---|---|---|
| `nested_sequence_filter_rebinds_an_ancestor` | D1: a nested loop's sequence writes the ancestor's name | slice2 `old`, slice3 `old`, `35ff15c` `old` (Django `X`) |
| `nested_sequence_filter_rebinds_an_ancestor_around_a_body` | D1 with a non-empty inner body | slice3 `zold`, `35ff15c` `zold` (Django `zX`) |
| `nested_sequence_filter_writes_only_its_own_layer` | D1 with a *plain* assignment, whose write Django pops with `{% endfor %}` | slice2 `X`, slice3 `X`, `35ff15c` `X` (Django `old`) |
| `top_level_sequence_filter_writes_only_its_own_layer` | D2 with no nesting at all: the layer the sequence writes into | **all three** `X` (Django `old`) |
| `sequence_filter_sees_the_loops_layer` | D2 observed: `len(context.dicts)` where the sequence resolves | **all three** `2` (Django `3`) |
| `nested_sequence_filter_sees_both_layers` | D2 one layer in: the nested sequence's depth | slice3 `3`, `35ff15c` `3` (Django `4`) |
| `empty_loop_sequence_filter_sees_the_loops_layer` | D2 on the `{% empty %}` arm, which Django returns from *inside* the layer | **all three** `2` (Django `3`) |

Three of the seven are D2 and diverge on the **slice-2** artifact, so the push
order is not a step-5 defect at all: it is a property of a walk that resolved its
sequence one layer out. The other four need a nested `{% for %}` to seal, which
slice 3 is what made possible.

The cases need two things the corpus did not have, and both are JSON-able by
construction: `libraries` (a tag-library name → module path) and `context_ref` (a
name, set to the context object itself, so a filter can be *handed* the context).
The library is `tests/differential/ctxwrite_library.py`, four stock-`Library`
filters. **The runner builds its engine per library set and caches it**, and
`scripts/check.sh:176-177` runs this harness, so the seven rows are in the gate —
which is the point: the 1547 vendored tests can see none of them.

### 4.3 The rows the corpus cannot carry

Two ways make every row above pass: keep the reads on the context (the fix), or
hand the loop back to Python (a re-delegation). They render identical bytes and
cost differently — which is exactly what a differential case cannot see. So
`tests/test_interposition.py` has two rows whose assertion is the census or a
count:

* `test_a_loop_whose_body_can_write_the_context_still_seals` (the first fix) —
  `ForNode` in `handled`, absent from `delegated`, with
  `delegated == [("CycleNode", 1)]`.
* `test_a_loop_whose_sequence_can_write_the_context_still_seals` — the same census
  for D1's shape: **both** loops in `handled`, and `delegated == [("LoadNode", 1)]`,
  because `{% load %}` is the only node the walk does not model. `ctxwrite_library`
  lives under `tests/`, which `discover` has on its path, so the row loads it the
  way a template does.
* `test_a_read_before_the_delegate_is_served_from_the_frame` — §3.1's claim, which
  no output can carry, counted instead: a `Context` subclass records every key read
  out of it, and the template
  `{% for s in sections %}{{ sections }}{{ s }}{% cycle "X" as s silent %}{{ s }}{% endfor %}`
  must read `s` **once** — a frame read is answered in Rust
  (`FrameTarget::get_item`) and a context read is `PyObject_GetItem` on the context.
  A body-wide gate reads `s` twice, a re-delegated loop reads it twice, and `1`
  requires a sealed loop whose pass stopped at the delegate. `sections` is the
  control drawn from the same render: a head that is not the loop variable is never
  rewritten, so it must reach the context, and if the instrument were disconnected
  both counts would be `0`.

### 4.4 The probe

`.review/m19-measurements/ctx_write_channels.py`: **17** rows, each rendered by
both engines from the same `Context` object (the context stored in itself under
`ctx`), printed with `!r`, with the artifact's sha printed first. It covers D1's
sequence route, the same write from an `{% include %}`-free emit, the two layer
*observations*, three controls, D2's four shapes, and the residual class's three.
On this tree it reads **14 `ok` and 3 `DIFF`**, and the three are the residual:
rows 15–17, where a *value* holding the context writes on render
(`{{ s }}{{ s }}` with a `__str__` that calls `set_upward`). Those three are
recorded rather than closed — see §3's "what the ordered pass cannot see".

### 4.5 The mutant

A regression row that passes on both the fix and the mutant catches nothing, so
the shape of the *wrong* fix is built and run.

* **The first fix's mutant** (`sha256:a4a2a272609c9875`): `may_write_context`'s
  `Act::Delegate | Act::For(_) => true` narrowed back to `Act::Delegate => true`,
  so a nested loop stops counting as a writer. The vendored suite fails
  `test_regroup01`, `test_regroup03`, `test_regroup04` independently — a `regroup`
  inside a nested `for` — and `nested_for_parity.py` reports `VOID: 13 of 16
  differ`. This is the row set the *first* fix was accepted on, and it is a mutant
  of a predicate that no longer exists.
* **This fix's mutant**: `rewrite_body`'s `Act::Delegate | Act::For(_) =>
  written.python_seen = true` with the `Act::For(_)` removed, so a nested loop is
  no longer a boundary. See §4.6.

### 4.6 The mutants against this tree, and the two test rows

A regression row that passes on the fix **and** on the mutant catches nothing, so
the rows that pin this fix were run against the wrong fixes. Instrument:
`.review/m19-measurements/sides.py build` for the artifacts (from
`crates/template-py/src/lib.rs`, with the working file put back from a saved copy
and never from `git`), then `row_probe.py`, which copies one side's `.so` in,
asserts the **live artifact's** sha256 as the package resolves it, runs the two
rows by name, and restores the shipped artifact. Transcript:
`mutant_rows.txt`; `differential-mutants.txt` for the corpus half.

| tree (artifact sha256) | `…a_read_before_the_delegate…` | `…a_loop_whose_sequence_can_write…` |
|---|---|---|
| **this tree** `0013195e7af12bb4` | **PASS** | **PASS** |
| gate / first fix `b0c073c33ecdfe6c` | FAIL — `AssertionError: 2 != 1` | FAIL — `'old' != 'X'` |
| slice 3 `e6a72702ba5877cb` | FAIL — `'Toldold' != 'ToldX'` | FAIL — `'old' != 'X'` |
| slice 2 `1ec2563e926cb649` | FAIL — `'Toldold' != 'ToldX'` | FAIL — `('ForNode', 2) not found in []` |
| boundary mutant `81b87938647bd42b` | PASS | FAIL — `'old' != 'X'` |
| push mutant `46ebc888fcc33853` | PASS | PASS |

Three things in that table are the point:

* **Every non-fixed tree fails row 2, and not for one reason.** The `'old' != 'X'`
  failures are the defect (a write that must be seen is not); slice 2's is a
  *census* failure — `('ForNode', 2) not found in []`, i.e. the nested loop was not
  the walk's at all, which is an earlier milestone's property that happens to fail
  first on this row. A row that failed for one reason on one tree would be a row
  about that tree.
* **Row 1 fails on three trees and for two different reasons** — `2 != 1` (a read
  count: the gate reads `s` twice) on the first fix, `'Toldold' != 'ToldX'` (a
  render) on slice 2 and slice 3. It is the row that prices `may_write_context`
  against `sealable`, and the first fix is the tree it exists for.
* **The push order has no unit row, and that asymmetry is recorded rather than
  smoothed.** The push mutant passes both rows above and is caught only by the
  differential corpus — `nested_sequence_filter_writes_only_its_own_layer` and
  `top_level_sequence_filter_writes_only_its_own_layer`, 2 of 59 — so
  `test_interposition.py` pins D1 and D2 and the corpus pins D3. The corpus *is* a
  test and the mutant is caught, so this is a distribution of the pinning and not
  a hole; a reader looking for D3 in the unit suite will not find it.

**The two mutants are disjoint by construction**, which is what makes each a
control for a different half: the boundary mutant removes `Act::For(_)` from
`rewrite_body`'s boundary arm and differs on the three **rebinding** rows; the push
mutant resolves the sequence outside the loop's layer and differs on the two
**layer** rows. Neither row set contains a row from the other, and all five rows
pass on this tree.

**The 1547 vendored tests pass under both mutants** — run here, with the sha
asserted per swap (`suite_probe.py`, in `mutant_rows.txt`): `Ran 1547 tests`, `OK
(skipped=6)`, exit 0 on `81b87938647bd42b` and on `46ebc888fcc33853`. The other
four trees in the table are milestone trees whose acceptance *was* this suite, and
it was green for each. That is the finding that started all of this: **the
acceptance suite is blind to every row in the table above**, and to a nested
`{% for %}` that stops being a boundary, and to a sequence resolved outside the
loop's own layer.

## 5. What it costs

Two prices, and they are prices of **two different trees**: §5.1 is what the
*gate* (`35ff15c`) cost against the slice-3 tree, and §5.2 is what the *ordered
pass* (this tree) costs against the gate. The first fix's number is not
restated as the second's.

**The pass is strictly finer than the gate, and that makes one *half* of this fix
cheaper by construction** — it moves fewer reads to the context — so by how much is
a measurement rather than an argument. An earlier draft of this sentence stopped
there, and it was false as a claim about the fix: the third finding (the loop's push
order) is a cost **every** `{% for %}` pays, it was not in the gate tree, and §5.2
measures it separately for that reason. The two halves pull in opposite directions
and the corpus row that shows it best is `real-world/large`, **+9.84% render**.

§5.3 is a provenance note — why the two transcripts' artifact sha is still this
tree's after the edits that followed them — and §5.4 is the headline on this build.

### 5.1 The gate's price, against slice 3

Measured by `frame_rewrite_ab.py`, which builds both sides from source and
alternates them round by round — 12 rounds a side, ABBA, `pin_iters` calibrated
once and handed to both, and the live artifact's sha256 asserted after every swap
so a round that ran the wrong build is a refusal rather than a number. The
transcript is `frame_rewrite_ab.txt`. Everything below is **ours**; the delta is
`fixed − prefix`, so positive is what the fix costs.

**The read is the unit, and the read is exact here.** Two templates under both
builds, differing only in whether the gate fires, over 50 rows:

| template | render Δ | parse Δ | compile Δ |
|---|---|---|---|
| `A1` 1 read + include | **+12.090 µs** (+7.75%) | +10.663 (+5.67%) | +0.011 (+0.05%) |
| `A2` 2 reads + include | **+21.897 µs** (+12.80%) | +22.018 (+10.66%) | +0.112 (+0.39%) |
| `A4` 4 reads + include | **+43.116 µs** (+21.35%) | +40.811 (+16.60%) | −0.021 (−0.06%) |
| `B1`/`B2`/`B4` — reads, no include | +0.120 / +0.179 / +0.161 | +0.441 / −0.022 / +0.299 | ≤0.059 |
| `C` — the include alone | −0.454 | +1.703 | +0.039 |
| `D` — the empty loop | −0.004 | +0.277 | +0.052 |

`B`, `C` and `D` are controls drawn from the **same family, in the same runs**, so
the floor and the thing it bounds are the same population. Their widest |delta| is
**0.454 µs** render and **1.703 µs** parse — **27× and 6.3× below** the smallest
subject delta. The gate's cost is therefore readable straight off the `A` row,
with no subtraction of two baselines anywhere:

* **per read, render +0.2076 µs** (slope of `A{k}` over k, three points, residual
  ≤0.34 µs), or **+0.2418** if read as `A1/50` alone; **parse +0.1991 / +0.2133**.
  The two estimators differ by 16% and that disagreement is **recorded rather than
  resolved**: the slope has three points behind it and is the one used below, the
  single-point figure is quoted beside it, and the spread is carried into the
  corpus prediction instead of being averaged away. What the disagreement *is*,
  read off the fit rather than named: `A1/50`, `A2/100`, `A4/200` fall
  monotonically — 0.2418, 0.2190, 0.2156 — so the fitted line has a **positive
  intercept, +1.480 µs**, and that intercept is an **extrapolation to k = 0 which
  no row measures**. It is not the include (the include is in both builds and
  subtracts out) and it is not a per-body cost the fix is known to have; it is the
  line compensating for a single-point rate that sits high. Reported as such: the
  slope is the estimate, and the extrapolation is printed beside it so the
  sublinearity is visible rather than hidden inside one number.
* **compile: no resolved per-read effect in these measurements.** The `A{k}`
  deltas are +0.011 / +0.112 / −0.021 µs, the fitted slope is **−0.0004 µs/read**,
  and the widest control is 0.059 µs. That is the claim this instrument can make:
  a per-read compile cost is *not resolved* here — the numbers are inside the
  instrument's own spread, and a cost of ~0.01 µs/read would be invisible in them.
  The stronger sentence an earlier draft carried ("no per-read cost at all") is a
  claim about the code, and the honest form of it is the mechanism: the gate is a
  compile-time predicate over an already-built plan, so what it could cost is a
  per-parse *plan walk*, and the compile rows bound that at below the resolution of
  this instrument rather than at zero. A question no ratio of render rows could
  have answered at all.

**On the corpus**, the same alternating runs, with the control floor taken over
the four rows the gate cannot reach (max |relative median delta| over those four —
the widest control, not an average):

| row | render | parse | compile |
|---|---|---|---|
| `django-only/realistic` | **+25.579 µs (+2.57%)** | +22.942 (+2.00%) | −0.155 (−0.13%) |
| `real-world/complex` | **+8.404 µs (+1.63%)** | +8.475 (+1.28%) | +0.575 (+0.50%) |
| floor over 4 unreachable rows | **0.745%** | **0.559%** | 0.461% |

**The estimator here is the difference of two separate medians**, and both halves
of that sentence were the second adversarial round's SHOULD-FIX: the two medians
are taken over *different moments* in the session (the `prefix` runs and the
`fixed` runs), so their difference carries the drift between the halves as well as
the change; and `frame_rewrite_corpus.py` calibrates its iteration counts once, off
the control engine, and pins them for every row (`:69-79`, `:92`), rather than
recalibrating per side. Neither is repaired by re-running this instrument, so both
are stated where they can be seen: the drift columns the report prints
(`p drift` / `f drift`, the spread within a side) are 3–6% here, which is the
scale of what a paired subtraction would remove. What the pairing *cannot* change
is the floor's role: it is taken over rows the change cannot reach, in the same
runs and under the same pins, so whatever regime bias the pins introduce is in the
floor and in the subject alike. §5.2's report computes the paired per-round median
as well, and prints both.

`realistic` is **3.5×** the floor and `complex` **2.2×** on render (3.6× / 2.3× on
parse). The report's own `MOVE` flag uses a flat >2% and does not light for
`complex`; the floor is the test that counts, and it is drawn from this
instrument's runs rather than an earlier one's. **The row that produced the false
positive in the failed instrument is a control here**: `django-only/filter-arg`
reads +0.29% render / +0.41% parse / −0.46% compile, inside the floor, where the
whole-corpus cross-build comparison had it at −3.89%.

**Reconciled against the read counts, which come from the corpus source:**

* `realistic` — 50 rows × 2 walk-owned reads (`{{ row.name }}` and
  `{{ row.value|floatformat:2 }}`; the `{% blocktranslate %}` is a delegate) =
  **100 reads, counted exactly**. 100 × the family's per-read predicts 20.8–24.2 µs
  render and the measurement is **+25.58 µs**, i.e. the row lands at 1.06–1.23× and
  at the top of the family's own spread; parse predicts 19.9–21.3 and measures
  **+22.94** (1.08–1.15×).
* `complex` — the outer body reads `s` at four sites (`{{ s.title|title }}`,
  `{% for line in s.lines %}`, `{% if s.total %}`, `{{ s.total }}`), the last only
  while `total` is truthy: 8 sections × 4 + 4 × 3 = **44, or 32 if the `{% if %}`
  condition is not a walk-owned read**. Measured +8.40 µs ÷ 0.2076 = **40.5 reads**
  (34.7 at the single-point rate) — the corpus row lands *between* the two
  source-derived counts, which is the honest form of this reconciliation: the count
  is bracketed by the source and the timing does not pick one.

**What it costs in the headline.** Both subject rows are the corpus's
walk-heavy pages, and the fix is **+2.57% render on `realistic`** — the row whose
render the whole step-5 push moved 0.914×→0.835×. That is the price of the four
differential cases in §3, and it is paid only by a body that holds a delegate or a
loop; a body whose reads are all `{{ … }}` of the loop variable keeps the frame
route and pays nothing (`B`, `C`, `D` move ≤0.45 µs).

### 5.2 The ordered pass's price, against the gate

Same instrument, same discipline — 12 rounds a side, ABBA, `pin_iters`
calibrated once and handed to both, the live artifact's sha256 asserted after
every swap — and the **same two source builds** (`S.rs` = the gate, `F.rs` = this
tree). Transcript: `frame_rewrite_ab2.txt`; live artifact
`sha256:0013195e7af12bb4` (and see §5.3 for why that sha did not move when the
citations in this fix were edited). The delta is `fixed − prefix`, so **negative
is what this tree saves**.

**The estimator is the paired per-round difference**, which is the second
adversarial round's SHOULD-FIX answered rather than restated: `cmd_ab` runs the
round's two sides back to back (order reversed on even rounds), so `f[i] − p[i]`
is a difference taken *within one round* and whatever drifted between rounds is in
both terms and cancels. The two separate medians are printed beside it. On this
data the two agree closely — every row below moves in the same direction under
both, and the widest disagreement is `real-world/large`'s render (+19.079
separate / +20.479 paired) — so the correction changed the *warrant* more than the
numbers, which is exactly what a SHOULD-FIX about an estimator should do.

**The ordered pass returns the gate's cost.** Family, render, paired medians:

| template | Δ vs the gate | as % |
|---|---|---|
| `A1` 1 read + include | **−11.037 µs** | −6.63% |
| `A2` 2 reads + include | **−20.130 µs** | −10.45% |
| `A4` 4 reads + include | **−39.775 µs** | −16.38% |

Slope over k, **−0.1923 µs/read** render and **−0.1873 µs** parse, against §5.1's
**+0.2076 / +0.1991** for the gate: the pass is cheaper than the gate on every `A`
row, and the family slope is 7.4% below the gate's in magnitude. The residual is
the honest part of that sentence: the ordered pass does not make this read *free*,
it makes it cost **0.1923 instead of 0.2076** — because both trees move the read
to the context (the ordered pass moves *fewer* of them, not none). The compile
rows are flat again (slope −0.0006 µs/read, widest `A` delta 0.126 µs), so nothing
here is a claim about compile.

**`C` is the only true control**, and it is the one row of the family this change
cannot touch: no loop at all, so neither half of the fix applies. Its paired
median is **−0.199 µs** render / +0.029 parse / +0.023 compile, with a paired range
of −1.824…+2.633 µs. That range is the honest bound on everything above it: a
1.8 µs move in either direction is what this instrument does to a row the change
cannot reach, and the smallest subject delta is 11.037 µs — **6.1×** it.

**The push order is the second, opposite half, and it is paid by every loop.**
The family's `B`/`D` rows hold a `{% for %}` and no delegate, so the ordered pass
cannot act on them and the *only* thing that can move them is the fix's third
finding — reading `parentloop` through `contains` and pushing once per loop
statement, with the sequence resolve and the length/reversal/empty arm inside that
layer. They move, and they move *up*:

| row | render | parse | compile |
|---|---|---|---|
| `B1` / `B2` / `B4` — reads, no include | +1.340 / +1.846 / +2.336 | +1.720 / +2.296 / +2.947 | ≤0.056 |
| `D` — one empty loop, nothing else | **+0.930** (−0.199 is `C`) | **+1.100** | +0.042 |

`D` is the cleanest of them — one loop statement, no reads, no include, nothing
else that can move — and its paired range **+0.807…+1.029 µs** is the tightest
band in either transcript, which is what makes it usable as a **per-loop-statement
floor**: about **0.9 µs render / 1.1 µs parse per `{% for %}` statement**. `B{k}`
moves by the same order (the read count is not what it tracks; the loop statement
is), which is the consistency check.

**On the corpus**, with the control population corrected at the point of use. The
rows the *ordered pass* cannot reach are `django-only/filter-arg` and
`django-only/i18n`, and each holds exactly **one** loop statement — the same shape
as `D` — so they are the floor for this comparison:

| row | render | parse | compile |
|---|---|---|---|
| `django-only/realistic` (1 loop stmt) | **−26.831 µs (−2.62%)** | −25.075 (−2.13%) | +0.080 |
| `real-world/complex` (1 outer + 12 inner stmts) | −1.666 | +0.931 | +0.124 |
| `real-world/normal` (1 loop stmt) | +2.240 (+1.69%) | +3.377 (+1.06%) | +0.276 |
| `real-world/large` (**20 loop stmts**) | **+20.479 µs (+9.84%)** | +39.113 (+1.71%) | +3.308 |
| floor: `filter-arg` / `i18n` | **+1.819 / +0.603** | −2.796 / +1.795 | +0.242 / −0.103 |

The floor is **0.374%** render (|+1.819| / 486.557) and **0.498%** parse. On it:

* **`real-world/large` is a headline-visible regression, and it is real.** +20.479
  µs render is **26× the floor**, and it reconciles: that template makes **20
  sibling `{% for %}` statements** per render (`scripts/bench_corpus.py:229-232`,
  one per section, three items each), so the family's per-statement floor predicts
  20 × 0.930 = **18.60 µs** and the row measures **20.479** — **1.10×** the
  prediction, i.e. **1.02 µs per loop statement** on a page a reader recognizes.
  This is the one row in the corpus that prices the push order on its own, and it
  is the reason the corpus set's control population had to be corrected: every row
  in it holds at least one loop, so *none* of them is loop-free, and the two
  "controls" §5.1 used were unreachable by the **gate**, not by a per-loop cost.
  (The comment that made `large` a control said "has no loop at all"; it has 20.
  Corrected in `frame_rewrite_corpus.py` and in the template's own docstring.)
* **`real-world/normal` does not reconcile to the floor and is recorded rather
  than reworded**: one loop statement predicts +0.930 and it measures **+2.240**,
  2.4× the floor, with a paired range of −0.377…+8.903. The floor's row (`D`) has
  an *empty* body; this one has seven nodes and an `{% if %}` per iteration, so
  "one price per loop statement" is falsified as a constant across body shapes by
  this pair. The direction is not in doubt (and the row is not on the headline);
  the constant is.
* **`django-only/realistic` is net negative because the ordered pass dominates
  there**: −26.831 µs against the gate's **+25.579 µs** for the same row in §5.1.
  Two different transcripts and two different estimators, so the closure is
  bounded rather than asserted — slice 3's render for this row reconstructs to
  ≈997.4 µs and this tree measures **996.920**, i.e. **the ordered pass puts the
  row back where slice 3 had it, within 0.5 µs**, and the +0.93 µs push order on
  its one loop statement is inside that. It is the row whose 0.914×→0.835× render
  the whole of step 5 is about, and the fix gives it back.
* `real-world/complex` is **−1.666** and the two halves are visible in it: the
  ordered pass keeps its pre-boundary reads on the frame (24 of the ~40 the gate
  moved) and it pays 13 loop statements (1 outer + 12 inner executions), so a
  saving of roughly 5 µs and a cost of roughly 12 µs are both inside a −1.67 µs
  net. Stated as a bracket, not as a reconciliation: the family's 0.1923 and 0.930
  µs units do not resolve this row's two effects to better than a few µs.

**What it costs in the headline.** One row moves by ~10% of its render
(`large`, +9.84%) and one row gives back ~2.6% (`realistic`, −2.62%); the geomean
is a question for `docs/bench.html`, regenerated on this tree (§5.4). The price is
now stated where it can be acted on: the natural next lever is the push itself —
`ForNode.render`'s `with context.push()` is unobservable whenever the loop body
holds no delegate, no `{% include %}`, and no tag that reads the context as an
object, so a per-body predicate could skip it — and it is recorded here as owed
rather than attempted in this fix.



### 5.3 Why the A/B's artifact sha survives the edits made after it ran

Everything in §5.1 and §5.2 is a claim about the artifact `0013195e7af12bb4`, and
three edits landed in `crates/template-py/src/lib.rs` *after* those runs: a
`Context`/layer fixture in `mod walk_depth`, and in-place rewrites of four
doc-comment citations (`engine/defaulttags.py:104-114` → `defaulttags.py:104-114`,
`:198` and `:198-199` named in full, `:194-197` given its file). This project's
rule from `a-comment-moves-the-artifact` is that a comment-only Rust edit moves
the built binary — so the question is whether the transcript's provenance expired,
and it was answered by rebuilding, not by argument: the gate's own build step
prints `sha256:0013195e7af12bb4  ==  target/release/lib_native.dylib`
(`gate-2026-10-10.txt`), from the source as it now stands. **The
artifact is byte-identical, so both transcripts still describe this tree.**

The mechanism is worth the sentence, because the rule and this reading are the
same rule: the `lib.rs:NNNN` strings that memory names are **panic locations**, so
they move when *line numbers* move. The fixture sits inside
`#[cfg(all(test, not(feature = "extension-module")))]` and is compiled *out* of the
extension; the four citation edits replace text **within** a line and change no
line count, so no location string shifts and 2720176 bytes come out identical. A
doc-comment edit that added or removed a line would have moved it.

### 5.4 The headline, on this tree

`docs/bench.html` was regenerated on this tree and is the instrument of record
here — not §5.2's six rows, which were chosen to *resolve* a delta
(`bench-fix2.txt`, `bench-fix2.json`; artifact `sha256:0013195e7af12bb4`,
2720176 bytes, stamped **2026-10-10 16:45 UTC**). Against the previous page,
which was the **slice 3** tree — `bench-report.slice3.json`, artifact
`e6a72702ba5877cb`, written 05:02 local = 11:02 UTC, i.e. *before either fix*:

| tile (real-world, geometric mean of 3 pages) | slice 3 page | this page |
|---|---|---|
| render | 0.3872 → prints **0.39×** | 0.4093 → prints **0.41×** |
| parse | 0.7472 → prints **0.75×** | 0.7366 → prints **0.74×** |
| compile | 1.3617 → prints **1.36×** | 1.3597 → prints **1.36×** |
| the six real-world rows, render+parse | 0.297–1.109 → **0.30×–1.11×** | 0.326–1.046 → **0.33×–1.05×** |
| worst row in the corpus | `micro/text` compile **3.522×**, 6.733 vs 1.912 µs | `micro/text` compile **3.451×**, 6.764 vs 1.960 µs |

The render tile is the one that moves, and §5.2 predicted it would: the push
order is paid by every `{% for %}`, and `real-world/large` is the loop-heavy
page. Row by row (slice 3 → this page), render / parse / compile:

| row | render | parse | compile |
|---|---|---|---|
| `normal` | 0.322 → 0.326 (**+1.2%**) | 0.559 → 0.562 (+0.5%) | 1.367 → 1.345 (−1.6%) |
| `complex` | 0.606 → 0.618 (**+2.0%**) | 0.673 → 0.680 (+1.0%) | 1.260 → 1.246 (−1.1%) |
| `large` | 0.297 → 0.341 (**+14.8%**) | 1.109 → 1.046 (**−5.7%**) | 1.467 → 1.500 (+2.2%) |

**Only `large` render is a claim.** The rest of that table is a cross-run
comparison on a two-run spread, and the spread was measured rather than assumed,
on both sides of the ratio:

* the same build, 12 alternating rounds of the two-armed runner
  (`fixab-*-r*.json`): the engine ratio per row moves with a **median of max
  |rel| 2.25%, overall max 5.20%** over the 18 rows, and on `large` render its
  own max |rel| is 2.99%;
* the **stock denominator** alone, over the same 12 rounds, moves with median max
  |rel| **1.97%, overall max 3.62%**;
* the rows these two fixes cannot reach at all — `filter-arg` and `i18n`, the
  control population §5.2 named — read **−3.28% / −1.01% render**, **−3.02% /
  −1.25% parse** and **+0.38% / −1.88% compile** between the two runs.

So the floor on this comparison is about **3%**, and `normal`/`complex` at +1–2%
are below it, as is `large` parse at −5.7% against a row whose own scatter is
2.1% — the floor is the control rows' 3%, which `large` parse does not clear with
its own row scatter beside it. `large` render is **4–5× the floor**.

Three routes to `large` render's shift, recorded side by side because they
disagree by 1.3× and none of them is the average of the others:

| route | reading |
|---|---|
| the page, ratio-vs-ratio | 0.297 → 0.341 = **+14.8%** |
| the page's µs against the A/B runs' µs (same row, stock 677.2 vs 687.1 — 1.4%, inside that row's 3.62% stock scatter, so the movement is in the numerator) | 207.6 → 230.6 = **+11.1%** |
| §5.2's same-pair A/B against the gate, which also carried a 0.745% control floor | **+9.84%** (20.479 µs on a ~209 µs baseline) |

§5.2's prediction from the push-order floor was 20 statements × 0.930 µs =
18.6 µs = **+9.0%** of 207.6. All four numbers are the same sign and inside a
1.3× band; the row is the corpus's slowest sample (73 calls per sample) and the
two runs are six hours apart, which is the likeliest of the differences. The
honest headline sentence is therefore narrower than the tile:

> **The fix costs the port about 4% of its real-world render advantage, all of
> it on the loop-heavy page, and leaves parse and compile where they were.** The
> printed six-row band becomes 0.33×–1.05×, but only one end of that is a claim:
> the 0.30× end moved *up* with `large` render and the 1.11× end moved *down*
> with `large` parse, and the latter is inside the floor. The render geomean's
> +5.7% relative shift is the one claimed row diluted two-thirds by two pages
> that did not move beyond the floor; the parse and compile geomeans (−1.4%,
> −0.1%) did not move at all.

This is the price of **correctness**, not of the walk: the four rows that
diverged (`differential-fix2.txt`) are rendered wrong by the previous tree, and
`large` render is the tax the loop's own layer charges to get them right. It is
also the lever §5.2 owed: skipping `ForNode.render`'s `context.push()` where the
layer is unobservable would take most of these 26 µs back.

## 6. What went wrong with the first attempt at the price

The first instrument tried to get the cost out of **one** build, by differencing
two templates: `((A - C) - (B - D)) / N_ROWS`, where `A` is a read plus an
include, `B` a read alone, `C` the include alone, `D` the empty loop. Two runs of
it on the same artifact returned **+0.042** and **+0.295** µs/read — a factor of
seven. The frame half (`B`) held to 0.2% between the runs; the stated-loop half
(`A`, one include per iteration, ~140 µs) moved 6%. A difference of two ~140 µs
numbers, one of which moves, is not an instrument. It is kept, marked
`SUPERSEDED`, in `frame_rewrite_cost.py`.

Two further instruments were tried on the corpus before the one that worked, and
both are worth recording because each failed for a different reason:

* **a cross-build comparison of the whole corpus** (2 before / 4 after runs):
  the control floor — the |rel| of cells the change cannot reach, over the same
  corpus with the same instrument — came back with max **3.18%**, wider than every
  subject delta, and with a *false positive*: `django-only/filter-arg render`, a
  row where `rewrite` still runs, moved **−3.89%** with non-overlapping ranges.
  The right conclusion was that this could not resolve the question, not that the
  fix is free. (`cross_build_floor.fix.txt`.)
* **the same corpus rows, absolute µs**: each side's own run-to-run spread over 12
  runs is **3–6%**, larger than any delta measured. Reported beside the result,
  never as evidence for it.
