# The sealed-loop frame rewrite: the fix, and what it costs

**Status.** Found by the adversarial reviewer (`scripts/adversarial_reviewer.sh`,
codex, `VERDICT: FAIL` on the step-5 slice-3 tree); fixed in
`crates/template-py/src/lib.rs`; verified by four differential cases, a census row
in `tests/test_interposition.py`, and 1547 vendored-suite tests; priced by the
alternating A/B in `frame_rewrite_ab.py`.

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

## 2. The fix

`may_write_context(body)` — true for `Act::Delegate`, descending into an
`Act::For`'s `body` and `empty` and into an `Act::If`'s branches, false for
`Act::Text`/`Act::Emit` — gates the write pass:

```rust
if !may_write_context(body) {
    for slot in body.iter_mut() {
        rewrite(&mut slot.act, loopvar);
    }
}
```

A body that fails the predicate is **still sealed**; it keeps reading its loop
variable out of the context, which the stated loop is maintaining anyway. What
sealing still buys it is every other name in the body staying a frame read.

Two of the three exclusions in the predicate's docstring are arguments rather than
code, and both were checked against Django rather than reasoned about:

* **a filter cannot write the context.** `FilterExpression.resolve` hands a filter
  only `autoescape` and the arguments (`base.py:812-815`); `takes_context` is
  `simple_tag`'s protocol. That is what lets the predicate skip `Act::Emit`
  instead of descending into the expression it carries.
* **a nested loop the walk runs cannot write its ancestor's name.** Its own writes
  go into the layer it pushes and are popped with it, and its sequence is read.
  Only its *body* can reach upward, which is why the predicate descends.

## 3. What the evidence is

**Four differential cases**, `tests/differential/cases.py`. Run under both
artifacts (`differential-fix.txt`): **4 of 52 differ on the pre-fix build
`sha256:e6a72702ba5877cb`** — `'X'`/`'old'`, `'X[]'`/`'old[]'`, `'XXXX'`/`'XXXold'`,
`'XX'`/`'Xold'` — and **52 identical on the fixed build
`sha256:b0c073c33ecdfe6c`**. An earlier draft of this section said three of the four
failed before the fix; the run says all four, and the run is what stands.

| case | why it is in the set |
|---|---|
| `cycle_rebinds_the_loop_variable` | the defect itself, smallest form |
| `cycle_rebinds_the_loop_variable_and_survives_the_loop` | the rebinding is still `X` after `{% endfor %}` — it was written *upward*, past the loop |
| `nested_for_keeps_an_ancestor_rebinding` | the inner loop's body rebinds the **outer** loop's variable |
| `cycle_then_nested_for_reads_the_rebound_name` | the rebound name is the inner loop's *sequence* |

**And the differential corpus cannot carry the decision.** Two ways make these
four pass: keep the reads on the context (the fix), or hand the loop back to
Python (a re-delegation). They render identical bytes and cost differently, which
is exactly what a differential case cannot see. `tests/test_interposition.py`
gained `test_a_loop_whose_body_can_write_the_context_still_seals`, which asserts
the **census** instead: `ForNode` present in `handled` and absent from
`delegated`, with `delegated == [("CycleNode", 1)]`. That is the row separating
the fix from a re-delegation, and it is why the pin moved 509 → 510.

**The 1547 vendored tests** pass, and the *mutant* that would be the wrong fix —
`Act::Delegate | Act::For(_) => true` narrowed back to `Act::Delegate => true`, so
a nested loop stops counting as a writer — was built
(`sha256:a4a2a272609c9875`) and run: the vendored suite fails
`test_regroup01`, `test_regroup03`, `test_regroup04` independently, and
`nested_for_parity.py` reports `VOID: 13 of 16 differ`. A regression row that
passes on both the fix and the mutant catches nothing; these catch it.

## 4. What it costs

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
  corpus prediction instead of being averaged away. The fitted intercept is
  **+1.480 µs** — which is *not* the include (the include is in both builds and
  subtracts out), and is the sublinearity of `A1 : A2 : A4` = 1 : 1.81 : 3.57
  against k = 1 : 2 : 4.
* **compile: no per-read cost at all.** The `A{k}` deltas are +0.011 / +0.112 /
  −0.021 µs, the fitted slope is **−0.0004 µs/read**, and the widest control is
  0.059 µs. The gate is a compile-time predicate over an already-built plan and it
  does not survive into the artifact as a per-parse cost — a question no ratio of
  render rows could have answered.

**On the corpus**, the same alternating runs, with the control floor taken over
the four rows the gate cannot reach (max |relative median delta| over those four —
the widest control, not an average):

| row | render | parse | compile |
|---|---|---|---|
| `django-only/realistic` | **+25.579 µs (+2.57%)** | +22.942 (+2.00%) | −0.155 (−0.13%) |
| `real-world/complex` | **+8.404 µs (+1.63%)** | +8.475 (+1.28%) | +0.575 (+0.50%) |
| floor over 4 unreachable rows | **0.745%** | **0.559%** | 0.461% |

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

## 5. What went wrong with the first attempt at the price

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
