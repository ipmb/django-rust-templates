# The sealed-loop frame rewrite: the fix, and what it costs

**Status.** Found by the adversarial reviewer (`scripts/adversarial_reviewer.sh`,
codex, `VERDICT: FAIL` on the step-5 slice-3 tree); a first fix landed as
`35ff15c`; the re-dispatched reviewer returned `VERDICT: FAIL` again with two
**BLOCKING** findings against that fix, both naming a route its docstring had
argued away; a second fix (an ordered write pass) landed as `f79f7c7`; the third
dispatch returned `VERDICT: FAIL` **again**, on two BLOCKING findings against the
ordered pass, and the third fix is in `crates/template-py/src/lib.rs` in this tree.

**§1–§3 are history and §7 is the current tree.** Those two sections describe
repairs that no longer exist, kept because each is the measured reason the next
one was needed — §3 in particular is the design the third round falsified. Its
"Option A / Option B" (§3.2) is a *different* choice from §7's — where the loop's
layer is pushed, versus whether the frame route exists at all — and the names
collide. Nothing in §1–§6 should be read as a description of this tree's code.

Verified by **59** differential cases, three rows in `tests/test_interposition.py`,
a **19**-row probe, and the 1547 vendored-suite tests; priced by three alternating
A/Bs, `frame_rewrite_ab.txt` (the gate, against slice 3), `frame_rewrite_ab2.txt`
(the ordered pass, against the gate) and `deletion-price/` (§5.5, the deletion,
against the ordered pass) — whose two runs, `ab4` (the run of record) and `ab3`
(the same pair, measured with a busy-wait poll loop of mine on the machine, kept
as the comparison), are both in-tree with every JSON carrying its own build's
sha256.

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

> **Superseded by §7.** Everything below describes `f79f7c7`. The pass it defines
> was falsified by the third round and deleted; "this tree" in §3–§6 means the
> ordered pass, not the tree you are reading. §3.2's Option A/B is about *where
> the loop's layer is pushed*; §7's is about *whether the frame route exists*.

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

> **Superseded by §7.2.** The probe is **19** rows, not 17, and on the tree you
> are reading it reads **0 of 19 DIFF**. The reading below is `f79f7c7`'s.

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

**The 1547 vendored tests pass under both mutants** — run here through
`django_suite/run.py -v 2 template_tests`, with the sha asserted per swap
(`suite_probe.py`, in `mutant_rows.txt`): `Ran 1547 tests`, `OK
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

**Superseded by §5.5.** The page this section reads is `f79f7c7`'s — the ordered
pass, the second fix — and the third round deleted that pass, so these are not
this tree's ratios. Its payload is kept in-tree as `bench-fix2.json` (2720176
bytes, `sha256:0013195e7af12bb4`, stamped **2026-10-10 16:45 UTC**), because
§5.5's price is a difference *against* it, and a difference needs both terms
readable. `docs/bench.html` has since been regenerated on the tree that carries
the deletion; §5.5 prints its rows.

This section compares that page against the one before it — not §5.2's six rows,
which were chosen to *resolve* a delta (`bench-fix2.txt`, `bench-fix2.json`).
That previous page was the **slice 3** tree — `bench-report.slice3.json`, artifact
`e6a72702ba5877cb`, written 05:02 local = 11:02 UTC, i.e. *before either fix*:

| tile (real-world, geometric mean of 3 pages) | slice 3 page | the ordered pass's page |
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

### 5.5 The deletion's price, against the ordered pass

Measured with the same instrument as §5.1 and §5.4 — `frame_rewrite_ab.py`, 12
rounds a side, ABBA, `pin_iters` calibrated once and handed to both sides, the
live artifact's sha256 re-resolved after **every one of the 24 swaps** — with
`prefix` = **the ordered pass**, `f79f7c7`, artifact `sha256:0013195e7af12bb4`
(2720176 bytes), and `fixed` = **this tree**, `sha256:f921c593cf7338ab` (2701680
bytes). That is the build the A/B and the bench payloads below name in-band; the
tree *as committed* differs from it in 86 bytes and in no instruction, and §7.1
measures the difference byte by byte rather than asserting it away. The transcript
is `.review/m19-measurements/deletion-price/ab4/`:
`report.txt`, the per-round sha log, and the 48 JSONs — each of which carries its
own build's sha in-band, so the identity of both sides is checkable from the data
and not only from the prose.

`deletion-price/ab3/` beside it is the **same pair, run thirty minutes earlier
while a poll loop of mine was on the machine** (`until …; do :; done`, which forks
without pause). It is kept rather than deleted, and the comparison it affords is
worth the paragraph it costs: `D`, the statement, agrees to **0.2%** between the
two runs (+14.331 vs +14.360 µs); the `f`-side drift columns are 2–3× worse in
`ab3` (up to 10.2% against 4.9%); and the one row where the two runs *disagree in
sign* is `C`, the control both runs decline to classify. `ab4` is the run of
record, and `ab3` is the measurement that shows what the load could and could not
move.

**The family, in µs, paired medians over 12 rounds a side:**

| row | render | parse |
|---|---|---|
| `D` — one `{% for %}` over 50 rows, **empty body, no reads** | 3.851 → 18.235 = **+14.360** | 18.800 → 33.249 = **+14.460** |
| `A1` — 1 read + include | 156.357 → 168.957 = +12.428 | 188.479 → 199.363 = +11.509 |
| `A2` — 2 reads + include | 173.212 → 192.605 = +20.171 | +19.793 |
| `A4` — 4 reads + include | 204.229 → 243.444 = +39.197 | +38.509 |
| `B1` / `B2` / `B4` — k reads, no include | +24.036 / +32.711 / +50.499 | +24.538 / +32.892 / +50.744 |
| `C` — a loop whose body is `{% include 'inc.html' %}`, no reads | 139.884 → 141.124 = **+1.421** | +1.437 |

**Each row now answers a different question than it did in §5.1, and saying which
is most of what this section has to say.** The family was built for the ordered
pass, where the question was *when* a statement lands. Against the deletion it
decomposes the per-iteration cost, because the two builds differ in exactly two
ways and each row cancels one of them:

* `A{k}` and `C` hold an `{% include %}` — a delegate — so **both** builds write
  the state for them (§5.4's `run_stated`, this tree's
  `Walk::run_loop_body`). The statement therefore **cancels** in `A{k}`'s
  difference and what is left is `k` reads' worth per iteration. `A{k}` is the
  *read* meter, not a cost meter.
* `B{k}` holds no delegate, so the ordered pass sealed it and wrote nothing at
  all while this tree writes the statement and serves the reads from the context.
  `B4 − B2` and `B2 − B1` are then differences of two rows that differ by
  **exactly 50 reads** and in nothing else: the statement cancels there too, and
  neither term is a baseline that has to be subtracted from anything.
* `D` is a 50-row loop with an **empty body** — the one row that reads the loop
  variable nowhere — so the elision had nothing to preserve and wrote nothing per
  iteration. `D` is the statement, alone, and it is exactly the number §5.4's
  `no context[loopvar] = item, no forloop dict: the two writes per iteration are
  what sealing buys` was buying.
* `C` must not move, and is checked for it rather than assumed.

**The cancellation is checkable in the table rather than asserted.** `A1`'s template
does everything `B1`'s does — one loop over the same 50 rows, one read of the loop
variable per iteration — **and one `{% include %}` more**, so on any reading in
which both builds write state for both rows, `A1`'s delta would sit *above* `B1`'s
by the include's own term. It reads **+12.428 against `B1`'s +24.036**, 11.6 µs
*less*. A row cannot get cheaper by doing more work; it gets cheaper only if the
thing the other row pays, this one does not — and the thing `B1` pays and `A1` does
not is the statement. That is why the `A{k}` column is a read meter — and it is the
one claim in this family that does not rest on the fit, since it compares two rows
rather than two points of one line.

**The statement is +0.2872 µs per iteration.** `D` — one loop statement, 50
iterations, nothing in the body that could read anything — goes **3.851 → 18.235
µs = +14.360 µs** paired, with the tightest interval in the family (+14.170…
+14.612, ±1.5%), which is **+0.2872 µs per iteration** render and +0.2892 parse.
Read relatively the row is **4.7×**, and that factor is the honest headline for
the statement: a bare iteration cost 77 ns on the ordered pass and costs 365 ns
here, so the transcription of `ForNode.render`'s per-iteration state is 79% of
what iterating an empty loop now costs. Seven `set_item` calls per iteration
(`crates/template-py/src/lib.rs:4434-4440`: six counters into the `forloop` dict,
then `context[loopvars[0]] = item`) is what the 288 ns buys. Nothing about that is
an accident of this implementation: it is the state Django writes, written where
Django writes it, and the third round is the argument that it cannot be written
conditionally.

**A read that leaves Rust is +0.17 to +0.25 µs, and four of the five estimators say
+0.17/+0.18:**

| estimator | render | parse |
|---|---|---|
| `B4 − B2` — 100 reads, two rows that differ in nothing else | **+0.1779** | **+0.1785** |
| `B2 − B1` — 50 reads, same shape | **+0.1735** | **+0.1671** |
| slope of `A{k}` over k = 1, 2, 4 | **+0.1801** | **+0.1810** |
| `A1/50` alone — the row to distrust | +0.2486 | +0.2302 |

`A{k}`'s fitted line has a **positive intercept, +2.915 µs** (parse +2.151), which
is an extrapolation to k = 0 that no row measures — the include is in both builds
and subtracts out, so it is not the include, and §5.1 saw the same shape on the
older pair (there +1.480). And `A1` is the noisiest row of the family by a
distance: its `f` drift is **12.58%** against 2.31–4.88% for every other row, and
its paired interval spans +8.884…+30.318 µs. The single-point reading is quoted
because it is the largest of the four estimators and not because it is the best
one; the slope and the two `B` differences, which share no arithmetic, agree to
**4%**.

The mechanism is not a lookup getting slower: **the read stops being a scan of a
Rust-side frame and becomes a Python-level one.** In this tree a loop variable read
resolves through `PyTarget::get_item` (`crates/template-py/src/lib.rs:2132`), i.e.
`PyObject_GetItem` on the context, i.e. our transcribed `Context.__getitem__` —
which is `return self.dicts[-1][key]`
(`python/django_rust_templates/engine/context.py:209`, Django's own `Context`
override at `.django/django/template/context.py:207`: the **innermost layer only**,
not `BaseContext`'s walk upward). A `µs`-scale difference for one dict probe is not
a dict probe; it is the price of crossing into Python to ask.

**§5.1's number is the same quantity, and the two trees agree as closely as the
family's own estimators do.** The ordered pass measured this read leaving the
frame at **+0.2076** (slope) / **+0.2418** (`A1/50`) render and **+0.1991 /
+0.2133** parse. So the two trees' `A1/50` readings sit **2.8%** apart while their
slopes sit 14% apart — the disagreement is in the *estimator*, not between the
trees, which is what the five-way table above says from the inside. Both readings
are consistent with a per-read cost of about 0.18–0.25 µs, and no reading supports
a claim finer than that. It is worth saying plainly what that means for §5.2: the
gate's cost and the deletion's cost are not "the same" as a matter of opinion; they
are the same *measured quantity*, taken on two trees with two denominators, and
they overlap.

**The control is a bound of ±2 µs on a 140 µs row, not a zero.** `C` — a loop whose
body is `{% include 'inc.html' %}` (the included template is one character, `x`,
and it reads the loop variable nowhere) — reads **+1.421 µs** render and +1.437
parse, 1.0% of its own 139.9 µs. Its paired interval spans zero (−0.113…+3.866), so
the report does not classify it as a move, and neither does this section: what `C`
bounds is that any number below ~2 µs on this row would be invisible here, and no
claim below is made on one. `ab3` reads the same row at −0.297 µs — the same
magnitude, the other sign — which is the same sentence about the same row from a
different run.

**Compile is flat, and that is a claim about the instrument.** Every family row's
compile delta is inside ±0.47 µs, the fitted slope is **−0.0012 µs/read**, and `D`
is −0.131. The deletion removes a pass over every loop body that the *compiler* ran,
so the expected direction is cheaper; what is bounded here is an effect below
±0.5 µs on a 13–36 µs row, not a zero.

**The corpus.** Ours in µs (medians), the paired median of the difference, and the
ratio against stock *taken inside the same run* (so a row's ratio and its µs answer
the same question). The `rel` column is **the paired median over the prefix median**
— the same numerator as the µs column, so the two are one reading:

| row | render: ordered pass → this tree | rel | × stock | parse: paired | × stock |
|---|---|---|---|---|---|
| `real-world/normal` | 135.567 → 201.386 | **+48.4%** | 0.330 → **0.490** | +63.709 | 0.569 → 0.679 |
| `real-world/large` | 228.085 → 275.406 | **+20.2%** | 0.335 → **0.405** | +49.997 | 1.087 → 1.114 |
| `django-only/filter-arg` | 487.862 → 535.346 | **+10.1%** | 1.090 → **1.195** | +43.326 | 1.125 → 1.223 |
| `django-only/realistic` | 1002.663 → 1027.308 | +2.3% | 0.833 → 0.858 | +17.520 | 0.885 → 0.907 |
| `django-only/i18n` | 385.205 → 386.105 | +0.1% | 0.573 → 0.579 | +2.631 | 0.633 → 0.640 |
| `real-world/complex` | 524.255 → 524.163 | −0.7% | 0.614 → 0.617 | −1.662 | 0.686 → 0.685 |

Two other estimators sit beside that one and both are below the resolution of the
sentence, but a reader computing either should find their number here rather than
have to decide whether the table is wrong. **The change in the ratio itself**
(`corpus_ratio.py`, and the number the page prints) reads **+48.25 / +20.84 / +9.70
/ +3.01 / +1.01 / +0.48%** — never more than 0.6% from the column above, the
difference being stock's own drift between the two builds; **the separated medians'
difference over the prefix median** (the instrument's `sep delta`, uncorrected for
the pairing) reads +48.55 / +20.74 / +9.73 / +2.46 / +0.23 / −0.02%. The three agree
on which rows moved, which is the only claim the table is used for.

The paired medians are +65.649 / +46.078 / +49.183 / +22.770 / +0.205 / −3.788 µs
render, and the report classifies the first four and `large` parse as moves: a row
that moved where its own control did not is the only kind of row this instrument
claims. Compile is negative on all six rows and small (−0.19 to −7.14 µs paired; the
last is `large`, whose compile pair has a ±100 µs interval and is not a claim).

**The corpus rows have a counted denominator, and counting changes what the two rows
that did not move mean.** `corpus_loop_count.py` counts, under stock Django, each row's
loop statements, its iterations (a `BaseContext.__setitem__` of a *live* loop variable —
the one write Django makes per iteration) and its reads of one; then renders the same
row under the walk on this tree with a counter on **our** `Context` and with the walk's
own census. Four things come out of it, and the fourth is the one this section did not
have before:

| row | loops | iters | reads | reads, ours / any key | the walk | floor | measured | m/floor |
|---|---|---|---|---|---|---|---|---|
| `real-world/normal` | 1 | 50 | 250 | 250 / 257 | 1 run, 0 delegated | 59.4 | **+65.649** | 1.11 |
| `real-world/large` | 20 | 60 | 120 | 120 / 370 | 20 run, 0 delegated | 38.8 | **+46.078** | 1.19 |
| `django-only/filter-arg` | 1 | 50 | 100 | 100 / 101 | 1 run, 0 delegated | 32.4 | **+49.183** | 1.52 |
| `django-only/realistic` | 1 | 50 | 150 | 150 / 251 | 1 run, 0 delegated | 41.4 | +22.770 | 0.55 |
| `real-world/complex` | 13 | 84 | 260 | 260 / 276 | 13 run, 0 delegated | 71.0 | −3.788 | −0.05 |
| `django-only/i18n` | 1 | 50 | 50 | 50 / 151 | 1 run, 0 delegated | 23.4 | +0.205 | 0.01 |

* **Every loop of every row is run by the walk**, `complex`'s thirteen included. So
  "the rows this change cannot move" is not a sentence about delegation: the change
  reaches all six rows. (Ownership is read off *this* tree; the deletion removed
  elision, not dispatch, so the prefix build ran the same set of nodes.)
* **The context traffic matches stock exactly** — 257, 276, 370, 101, 151, 251 reads
  of any key, ours and stock's, equal on every row, and the loop-variable subsets
  equal too. Whatever the deletion costs, it is not extra reads.
* **So `complex` and `i18n` did not move with their loops in the walk's hands.** The
  only per-iteration difference between the two builds is *whether* the walk writes
  Django's state, so a row the walk runs whose delta is zero is a row the **ordered
  pass was already writing state for**. That is the deduction §5.4's control rows
  could only suggest, and it is now the reading of those two rows rather than an
  inference from the silence of their intervals.
* **The floor is a floor.** `iters × 0.2872 + reads × 0.1801` is built from two
  constants measured in micro-templates, and the three rows that moved come in at
  **1.11, 1.19 and 1.52×** it. Since the read counts are equal, the excess is not
  traffic: it is the two constants not transferring from a 50-row loop over dicts to
  a whole page, and the excess is 0.13 µs per iteration on `normal` and `large` (which
  agree to 5%) against 0.34 on `filter-arg`, the row whose read is consumed by a filter
  application. I have no measured account of that excess and am not going to invent
  one; the decomposition above holds to a factor of 1.5 on whole pages, and exactly
  where the family measured it. `realistic` at 0.55 sits between the two groups and is
  left unclassified rather than assigned to either.

**§5.4's control sentence must not be reused here.** That paragraph says *"the rows
these two fixes cannot reach at all — `filter-arg` and `i18n`"*, and that is true of
the **gate**, whose action was to *skip* a body holding a delegate: neither row has
a loop body the gate rewrote. It is false of this change, which reaches **every**
loop — `filter-arg` moves by **49 µs** in this pair, one of the three largest
deltas in the corpus. The rows this change cannot move are the ones whose loops the
ordered pass already wrote state for, and `complex` and `i18n` are the two that read
that way here (both unclassified, at −3.788 and +0.205 µs — `i18n`'s interval spans
−7.2…+17.6, so it is bounded rather than pinned).

**The page, on this tree.** `docs/bench.html` is regenerated from the payload of
record on this build (`bench-del-a.json`, `sha256:f921c593cf7338ab`, 2701680
bytes, 2026-10-10 17:30 UTC) with the benchmark's own `bench_report.render`, the
same function `bench.py --html` calls — so the page is the instrument's output
and not a hand-assembled reading of it. Its `env` block prints that sha, because
that is the sha the payload carries; a reader who resolves the `.so` in the tree
now gets `32ee8d6cb88bcd86`, and the 86 bytes between the two are §7.1's table —
the page is a snapshot of a *build*, which is what it claims to be, and it is not
claiming to be a snapshot of a *commit*. Its hero tiles, against the ordered
pass's in the same slot:

| ours vs stock Django, real-world geomean | ordered pass | this tree |
|---|---|---|
| render | 0.41× | **0.50×** |
| parse | 0.74× | **0.82×** |
| compile | 1.36× | 1.36× |

Four runs of one build (a–d, 17:30–17:38 UTC) put those tiles at
0.50/0.49/0.50/0.50, 0.82/0.80/0.82/0.82 and 1.36/1.36/1.36/1.37, so the headline
is stable to a unit in the second decimal and the deletion's render tile is a
property of the *build*, not of one run. **The trade is that sentence**: the
render advantage over stock falls from 2.4× to 2.0×, and parse gives back most of
its margin. The three real-world pages read 0.620× (`complex`) / 0.416× (`large`)
/ 0.482× (`normal`) at render; at parse `complex` 0.688×, `normal` 0.688× and
`large` **1.135×** — the large page's parse has crossed to the wrong side of
parity, which it had not on the ordered pass (1.046×, itself already above 1).
Across all 18 rows the ratio spans 0.416–1.318 render, 0.598–2.356 parse and
1.257–3.510 compile. The worst **render** row is now **`micro/loop-text` at
1.318×** (19.63 against 14.89 µs), where it was `micro/text` at 1.058×; the report's
worst row in any mode is still `micro/text` **compile** at 3.510× (6.737 against
1.919 µs), a one-off cost this change moves by 0.06 µs. **Jinja2 now wins every
whole-page render**: `normal` 173.07 against our
197.51 µs, `large` 201.14 against 282.23, `complex` 341.34 against 524.97 — where
on the ordered pass we won `normal`. That is the project's goal moving away from
us on this change, and it is said here rather than left for the page's reader to
notice.

**What the report's own differ says, and what its band can carry.**
`bench.py --compare bench-fix2.json bench-del-a.json --band bench-del-b.json
bench-del-d.json` reads **37 of 54 cells outside their row's band**, band median
|rel| **0.89%** and max **9.87%**. (The band pair moves those aggregates: the same
comparison banded on runs `a`/`b` reads 39 outside with a 0.82% median, and no
verdict this section uses changes between them — which is the point of quoting
the invocation beside the number.) The movers are what §5.5's family predicts and
in its order: `micro/loop-text` render **+318.93%**, `micro/loop-var` render
**+107.57%**, `micro/loop-text` parse +67.84%, `real-world/normal` render
+47.90%, `real-world/large` render +22.13%, `django-only/filter-arg` render
+15.62%. Two rows *improved*, and **nothing in the family predicts a negative
row** — its cheapest are `C` at +1.42 and `D` at +14.36 µs, both positive, so the
improvement is a fact with no account here. The only shape of explanation the
measurements allow is the one the census gives `complex` and `i18n`: for a row whose
loops the ordered pass was already writing state for, the deletion removes the frame
build and the compiler's body pass and adds nothing, which makes the deletion a pure
saving there. `django-only/for-tags` is a page of nothing but loops — the row family
where that term would be largest — and it improves **−17.56%** at render and
**−15.19%** at parse, the parse figure being the larger share of a one-off, which is
the render/parse signature §4 item 9 exists to catch. I have not measured that
decomposition and it is offered as a hypothesis, not as this section's finding.
**A single pair's band is one draw, not the scatter.** The same
differ run over two runs of *one* build — `a` against `d`, banded on `b`/`c` —
lands outside its row's band on **31 of 54** rows, essentially half, which is what
two independent draws of one scatter look like when the band is itself estimated
from a single pair. Its one verdict that differs from the pair above is
`real-world/large` parse (+8.54%: `outside` there, `inside` here), which is the
`large` row whose §5.4 interval is already the widest in the corpus. So "inside" is
a statement about resolution and not about the code, and only the movers above, far
outside any band this tree can produce, are read as moves.

**And the output is still Django's.** In this run's own `verdicts`, 54 cells and
**no cell in which any two engines that ran disagree**; 39 cells where all three
ran and agree byte-for-byte. The change is a price, not a defect.

**Three levers, all of them cost reductions on an *unconditional* statement, and
none of them taken here.** They are named because the price is now on the page and
a reader is owed the route back. Each would need its own measurement *and* its own
review; the third especially needs §5.1's `render`/`parse` pair discipline, since a
per-object saving is invisible in one mode and 7–20% in the other:

* **the seven key strings.** `forloop.set_item("counter0", …)` and its five siblings
  (`crates/template-py/src/lib.rs:4434-4439`) and
  `self.context.set_item(&plan.loopvar, …)` (`:4440`) each take a `&str`, so PyO3
  builds a `PyString` and hashes it on **every call** — 350 string builds per
  50-iteration loop, which is most of `D`'s +14.360 µs. All seven names are static:
  six are literals and the seventh is `plan.loopvar`, known at plan time. Seven
  `Py<PyString>`s built once per loop (or once per plan) remove the build; the dict
  still hashes on insert, which no pre-built key can avoid.
* **the target dict.** The write's last step is `Context.__setitem__`, whose whole
  body is `self.dicts[-1][key] = value`
  (`python/django_rust_templates/engine/context.py:69-71`). Routing the walk's write
  to that layer dict directly — `dicts` fetched once per *iteration* and indexed at
  `-1`, so that a body which `push`es or `pop`s a layer is still respected — replaces
  a Python-level call with three C-API operations. It is sound for the transcribed
  class and **not** for a subclass that overrides `__setitem__`, so it needs a
  once-per-loop check on `type(context)` — a check on a *class*, which is the safe
  direction, not on a value's behaviour, which is §7's argument.
* **the read.** Every loop-variable read now enters Python at `PyTarget::get_item`
  (`crates/template-py/src/lib.rs:2132`) to reach a method whose entire body is
  `return self.dicts[-1][key]`
  (`python/django_rust_templates/engine/context.py:209`). Implementing that one line
  in Rust — `dicts`, index `-1`, one `PyObject_GetItem` — with a fallback for a layer
  that is not a `PyDict` and the same `type(context)` guard, keeps the read Rust-side
  **without caching anything**, and so without a staleness window for the third
  round's own finding to reopen on the next template. It applies to *every* root
  lookup the walk makes and not only to loop variables, which makes it the largest
  of the three and the one I would take first.
* **what is not a lever** is any return to a conditional statement —
  `plan.context_state`, a predicate, a boundary, a node-kind test. §7 is the argument
  that no widening of one terminates, and the second round's §5.3 is the demonstration
  that a version of it looks right for three templates and is wrong on the fourth.
  The push lever §5.4 owed (skipping `ForNode.render`'s `context.push()` where the
  layer is unobservable) is separate, still owed, and unaffected by any of this.

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

## 7. The third round: the route is deleted

The re-dispatched reviewer returned `VERDICT: FAIL` against `f79f7c7` with two
BLOCKING findings, and both are a **position** the ordered pass does not name:

```
{% load ctxwrite %}{% for s in sections %}{{ holder.s }}{{ s }}
    {% firstof "Q" as ignored %}{% endfor %}                       -> Zold / old
{% load ctxwrite %}{% for s in sections %}{% if holder %}{{ s }}{% endif %}
    {% firstof "Q" as ignored %}{% endfor %}                       -> old / X
```

`holder` is a value that holds the context and writes `s` upward when its `s`
property is read, or when it is truth-tested. Both render `ZX` / `X` in Django and
`Zold` / `old` on `f79f7c7`. The ordered pass rewrites a read to the frame *up to
the first node that hands Python the context* — and in the first template the
writer is **inside** the node before that boundary (the `{{ holder.s }}` emit),
while in the second the writer is the `{% if %}` **condition** itself, which is the
same chain plus a truth test. Neither is a boundary the pass could have moved: the
write has already happened by the time the boundary is reached.

**The finding was not "widen the boundary", and the second finding says why.**
The predicate and the pass are both properties of the *plan* — a node kind, a
position. What writes the loop variable in these templates is a **runtime value
that holds the context**, and that is a property of the *value*, not of the node
that happens to hold it. `{{ holder.s }}`, `{% if holder %}`, `{{ holder }}` and
`{% if holder.s %}` are four nodes, and the value can be reached through an
attribute, an index, a filter argument, a sequence, or a `{% with %}`. A boundary
set enumerated over node kinds is therefore not merely incomplete today, it is
**the wrong kind of answer** — the reviewer's second finding is the general form
of the same argument, and no widening of it can terminate.

So the elision goes, and with it the question.

### 7.1 What was deleted

Two structs, `Frame<'a, 'py>` and `FrameTarget` (with its `impl LookupTarget`),
and `Written` with its three fields `stated` / `python_seen` / `on_context`;
twelve free functions and methods, `head_sealable`, `emit_sealable`, `seal`,
`sealable`, `needs_context_state`, `rewrite_body`, `rewrite_head`, `delegate_slot`,
`write_frame`, `run_stated`, and `LookupScope::resolve_local`; and three fields:
`Head::Lookups::local`, `ForPlan::context_state`, and `FilterArg::Variable::head`
— the last one dead the moment `emit_sealable` went, and that is checkable rather
than testimonial: on the ordered pass that field is **written at one place and read
at exactly one** (`FilterArg::Variable { head, .. } => head != loopvar && …`, the
predicate `emit_sealable` at `lib.rs:3578-3581` of `f79f7c7`), so deleting the
predicate leaves a written-and-never-read field, which is the `warning: field 'head'
is never read` the build printed while the deletion was in progress. Worth the
sentence because the build is an auditor on this deletion: a field that only a
deleted reader read is a line the diff would otherwise have left behind. In `lib.rs`: `161 insertions,
721 deletions`; the built artifact goes 2 720 176 → 2 701 680 bytes, which is an
observation and not a size of anything.

The rule that replaces all of it is one sentence: **every `{% for %}` the walk owns
writes Django's per-iteration state, unconditionally, and every read goes to the
context.** `Walk::run_loop_body` is `ForNode.render`'s order transcribed —
`loop_dict = context["forloop"]`, then the six counters into the dict
(`engine/defaulttags.py:220-227`), then `context[loopvars[0]] = item`
(`engine/defaulttags.py:248`) — and `Head::Lookups` resolves through
`Variable.resolve` like any other expression. There is no `local` case, because
there is no read the context cannot answer correctly.

**A pre-existing ordering bug was corrected while in there.** `run_stated` wrote
`context[loopvar] = item` *before* the six counters; Django writes the counters
first. It is observable when the loop is named `forloop`, and it is wrong on
every earlier tree in this record — the fix does not depend on it, and it is
recorded here rather than folded into the fix's justification.

**The delete left two things behind, and only the gate's second cargo step could
see either.** `Walk::run` lost the frame argument (`run(&mut self, slots, depth)`),
and the production call site was updated while the *test* call site at
`lib.rs:5011` still read `walk.run(slots, None, 0)`. `cargo test --workspace`
compiles that crate with `extension-module` on, which gates the walk's tests out,
so it stayed green; `scripts/check.sh`'s second step is the only thing that
compiles them, and on the first gate run of this tree it stopped with `E0061: this
method takes 2 arguments but 3 arguments were supplied`. That is the failure mode
that step's own comment describes, met for the first time: *an empty test target
reports as a pass*. With that fixed, the same step found the second thing, and it
is the more interesting one: `the_depth_guard_raises_cpythons_error_at_the_cap_and_not_below_it`
**failed** with `TypeError: 'builtins.DepthContext' object does not support item
assignment`. The fixture in `mod walk_depth` modelled a context the walk only ever
*read* — `__contains__` for the parent loop, `push` for the layer — because on the
ordered pass a body this plan would have sealed wrote nothing at all. The
deletion makes the write unconditional, so the fixture was asserting a contract the
engine no longer has. `DepthContext` gained a no-op `__setitem__` and its docstring
now says why it is the fourth call and not the third. Both fixes are in
`#[cfg(all(test, not(feature = "extension-module")))]`, and the artifact is
**byte-identical** after them — `sha256:f921c593cf7338ab`, 2701680 bytes, the same
bytes §5.5's `fixed` side measured (rebuilt, not argued: §5.3's rule).

**Then rustfmt moved the artifact, and the third item is that I can say exactly
how.** The gate's fmt step found a stray blank line at `lib.rs:3557` — the only
non-test source edit in this whole change that is neither the deletion nor a
whitespace-neutral reflow — and removing it (the tree ships fmt-clean) rebuilt the
same 2701680 bytes to a **different** sha, `sha256:32ee8d6cb88bcd86`. So the record
now names two artifacts and owes the difference. It is **86 bytes**, and they are
the identity stamp rather than the code:

| bytes | where | what |
|---|---|---|
| 16 | load commands, offset 1928 | `LC_UUID` |
| 6 × 1 | `__DATA_CONST.__const` (2031616–2211840) | the low byte of six little-endian `u32`s, each exactly **1 lower** (4540→4539, 4549→4548, 3605→3604, 4173→4172, 4302→4301, 4621→4620) — the line fields of six embedded panic `Location` records, all of them past 3559, which is what removing one line above them does |
| 64 | `__LINKEDIT` (2228224–2701680) | two 32-byte ad-hoc code-signature page digests, over the one changed load command and over the one changed page of const data |

**Zero of the 86 lie in `__TEXT`** — the 2031616 bytes holding every instruction,
`__TEXT.__text` among them — and zero lie in `__DATA`; `strings` over both binaries
is **4260 lines with 0 differences**, because the *file* each `Location` names is a
shared string and only the line number beside it is an inline `u32`. The machine code
is therefore the same bytes in both builds, and §5's numbers, measured on
`f921c593cf7338ab`, are numbers about the code the commit contains — which is the
claim §5 owes; it is not the claim that the two artifacts are identical, and that
claim is the one this paragraph retires.

**And the gate found a third thing, in the *previous* commit's evidence rather
than in this change — the tree and its transcript disagree.** `f79f7c7` committed
`gate-2026-10-10.txt`, whose suite-counts step reads `suite counts: 21 line(s),
every one with its provenance`. Run against that commit's own tree,
`scripts/check_suite_counts.py` counts **22** count lines there and refuses
**one** of them: the §4 sentence recording the two mutants' suite runs, whose
count named its instrument (`suite_probe.py`) and not the invocation. The run is
committed beside this record as `suite-counts.f79f7c7-rerun.txt`, with `--list`'s
22 rows and the refusal, so it is reproducible rather than asserted. So that
transcript describes a tree with one fewer count line than the tree it was
committed beside — the record gained the row after the run. The count was **21
licensed** at the run and the tree is **22 with one bare**, which is one edit's
worth of drift; the class is not the blank line's, because a gate transcript is a
claim about a commit ([[a-reused-ab-pair-is-an-older-tree]]) and this one was made
about the working tree a moment before the commit. Both rows are repaired here
rather than in a follow-up, since the gate refuses the tree either way.

### 7.2 What the evidence is

* **The probe, and the whole residual class at once.** `ctx_write_channels.py` is
  **19 rows** — the third round's two templates are added verbatim as 18 and 19,
  and `Holder` gains `__bool__`, because a truth test is the `{% if %}`
  condition's own last step. On this tree: **0 of 19 DIFF**. On `f79f7c7`: 3. On
  the first repair: 10. On slice 3: 12. The five rows 15–19 that used to diverge
  go to zero **together**, which is the reading that says the change removed a
  class rather than patched two templates.
* **The differential corpus**: **59 cases, identical** (`differential-fix2.txt`).
* **The vendored suite**: `django_suite/run.py -v 2 template_tests` reads
  `Ran 1547 tests ... OK (skipped=6)`, which it also said
  on every wrong tree in §4.6 — it is reported because it is the acceptance
  criterion, not because it is evidence about this defect.
* **The python unit suite**: **514 OK**, the pin moved `512 → 514` for the two
  new rows.
* **The anchors**: `1577 anchor occurrence(s) checked, 925 cited of 925 manifest
  entries, 0 failure(s)`.

### 7.3 The two test rows, and the rename

`test_interposition.py`:

* `test_a_read_before_the_delegate_is_served_from_the_frame` becomes
  **`test_every_read_of_the_loop_variable_reaches_the_context`**. It counts
  `Context.__getitem__` for `s` and requires **2** (one per iteration) where it
  required 1 — the row used to assert the frame read was taken, and there is no
  frame read to take. The `title` count stays 1 as the in-row control, so the row
  is not "the count went up".
* Two new rows, one per reviewer template, with a real `Holder` carrying the `s`
  property and `__bool__`: `…a_value_side_writer_inside_an_emit_is_visible_to_a_
  later_read` asserts `ZX`, and `…inside_an_if_condition_is_visible_to_a_later_
  read` asserts `X`. **Those are literals, and the byte-comparison against
  Django is the probe's rows 18–19 rather than these rows**: the unit-test
  session's `django` is the venv's, not the pinned `.django/` checkout
  (`scripts/run_unit_tests.sh`: *"Not for the interpreter's sake — the venv
  supplies Django"*), so an in-row comparison would be against the wrong oracle
  or against ours. The literal is the review's own reported stock answer, and
  `ctx_write_channels.py` renders the same template through both engines in one
  process and reports row 18/19 as identical. What the rows own is the *pin*:
  `ZX` is what a frame read cannot produce, since the frame is written before
  the property runs.
* The two census rows keep their assertions (`ForNode` in `handled:`,
  `delegated == [("CycleNode", 1)]` / `[("LoadNode", 1)]`) and gain rewritten
  docstrings, because the *reason* those rows exist is now the reverse of what
  they said: they no longer distinguish a gate from a pass, they distinguish
  **the walk owning the loop** from the loop being handed back to Python.

**A census assertion cannot catch this class, and the rows say so.** Re-delegating
the loop renders the same bytes *and* moves `ForNode` out of `handled:`, which is
why the census rows fail on a re-delegation; but a *wrong frame read* renders the
same bytes as nothing at all — 1547 green — and moves no census counter. The probe
and the two new rows are the only things that see it.

### 7.4 The transcription-numbering trap, recorded because it cost a pass

Every citation added in this round was written with `.django/django/template/
defaulttags.py` line numbers, and the anchor gate rejected them: `scripts/
anchors.txt` rows spelled `engine/defaulttags.py:NNN` name **our transcription**
(`python/django_rust_templates/engine/defaulttags.py`), whose lines are Django's
**+ 2**. `:246` "does not contain `self.loopvars[0]` (it starts
`context.update(unpacked_vars)`)". Ten dead `.django/`-numbered rows were pruned
and the corrected ones seeded; `docs/superpowers/specs/2026-10-09-clean-and-fast
-design.md:221` was retargeted off a stale `lib.rs:5038`. Nothing about this is
subtle once seen, and it is exactly the shape of the earlier lesson that a
citation expires when the tree moves: the file name in an anchor is not
decoration, it selects which of two texts the number is about.
