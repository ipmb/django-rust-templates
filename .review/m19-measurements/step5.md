# Step 5, slice 1 — the walk owns a `{% block %}` body: outcome

The prediction is `.review/m19-measurements/step5_prediction.md`, written and
committed before the code (`d5767bb`).  This file is the outcome, and every row
is **baseline → predicted → measured**.

What shipped: a descent pass in Rust (`plan_block_bodies`) that plans the body
`NodeList` of every `{% block %}` it can and attaches the plan to that object;
`NodeList._native_plan = None` as a class default; and `NodeList._render_with_plan`,
which `loader_tags.py::BlockNode.render` calls in place of `render` on its two
body lines.  `ExtendsNode` and `BlockNode` stay Python delegates — the slice
takes the body, not the inheritance machinery.

**One design change against the prediction, and it is the reason this file
exists in this shape.**  §2 of the prediction put the arm "in `NodeList.render`".
It cannot go there: `NodeList.render` is on `scripts/check_transcriptions.py`'s
`COMPARED_PATH`, the set of functions `tests/parity_target.py` reads to decide
whether a declared transcription region sits on code the parity harness
executes, and clause (4) refuses a declared region that overlaps it — an arm
inside `render` would make the harness compare Rust against Rust.  So the arm
went into a **new method** and the hook moved to the arm's *caller*
(`BlockNode.render`, which is not on that path), with `loader_tags.py`'s two
lines rewritten by the generator's `TRANSFORMS` table so that file stays
generated.  The prediction's §2 is left as written; this paragraph is the
correction.  See `tests/test_interposition.py`'s diff-shape row and the
`COMPARED_PATH` note in the guard for the +18/+60 band split the move caused.

## 0. Instruments, artifacts, and the band

| label | artifact | what it is |
|---|---|---|
| `before` | `sha256:bc4c750a…` | `bench-pre-{a,b}.json`, two runs on the pre-slice tree |
| `after` | `sha256:6954e0c1…` | `bench-post-{a,b}.json`, two runs on the built slice |
| `predicted` | — | `step5_prediction.md`, whose declared baseline is the **same artifact** as `before` |

**The `before` column was re-measured, because this file's first version had it on
the wrong tree.**  It used `bench-ab/pre-{a,b}` (`sha256:4c14186d…`), which is
`dbce272`'s A/B pair — a tree **one slice older than step 5's own parent**, so it
was missing the bare-number argument work too.  The row that gives it away is
`django-only/filter-arg`: `1.374` render in `bench-ab/pre-a.txt`, against `1.089` /
`1.092` on the real pre-slice tree — and `dbce272`'s own message predicts 1.09
from 1.374 at −20.5%.  Reverting the three files the slice touches to `6231b04`
and rebuilding reproduces `sha256:bc4c750a…`, **byte-for-byte the artifact
`step5_prediction.md` declared as its baseline**, so `before` and the prediction
cite one artifact rather than two.  The correction moves the `before` render
ratio on `real-world/normal` from 0.894 to 0.899, a 0.6% shift: **the
conclusions survive the correction, the citation did not.**  §1's own `before`
column never had this problem — it comes from the committed `node_census.txt`,
which `dbce272` last regenerated.

`scripts/bench.py`, `.venv/bin/python -I`, Django 6.0.9, `--repeats 7
--target 0.05`, same machine.  Ratio is ours/stock, **lower is better**, 1.00 is
parity; every bench row reported `===` (three engines, byte-identical output).

**The band, stated before the numbers it bounds.**  `.review/m19-measurements/bench_scatter.py`
takes two runs of one artifact, ratio against ratio, and refuses two different
`sha256:` values — a scatter measured across two builds is the delta it was
supposed to bound.

| pair | rows | median \|rel\| | max \|rel\| |
|---|---|---|---|
| `after` a↔b (both `6954e0c1`) | 36 | **1.23%** | 3.67% (`real-world/complex parse`) |
| `before` a↔b (both `bc4c750a`) | 36 | **0.82%** | 2.87% (`django-only/for-tags render`) |

A cross-artifact delta of two ratios owes roughly the sum of the two bands, so
**nothing under ~2% is called a move below, and the two rows this section treats
as moves are 53× and 35× that.**  What the bands do *not* license: both pairs
were run back to back, so what they sample is short-timescale noise; the
`before` ↔ `after` comparison also crosses artifacts and sessions, so it carries
thermal and load drift neither band saw.  The band is the *floor* of what a
cross-artifact delta owes, which is why only the largest rows are promoted.

## 1. Structure — the census, before → measured

`.review/m19-measurements/node_census.py`, same two trees.  `handled:` is nodes
the walk rendered, `delegated:` nodes it handed back, `out D/T` the characters
returned from a delegate against the render's own length, `applied:` filter
applications the plan routed, `body xings:` the ones Rust actually computed.

| row | column | before | predicted | measured | verdict |
|---|---|---|---|---|---|
| `micro/extends` | `nodes` | 3/6 (50.0%) | 7/10 (30.0%) | **7/10 (30.0%)** | hit, exact |
| | `handled:` | `TextNode=3` | `+TextNode=3 VariableNode=1` | **`TextNode=6 VariableNode=1`** | hit, exact |
| | `delegated:` | `BlockNode=2 ExtendsNode=1` | identical | **identical** | hit, exact |
| | `out deleg` | 128.1% | 128.1% | **128.1%** | hit, exact |
| `real-world/normal` | `delegated:` | `BlockNode=2 ExtendsNode=1` | `+IncludeNode=1` | **`IncludeNode=1 BlockNode=2 ExtendsNode=1`** | hit, exact |
| | `applied:` | `length=1` | `length=2 upper=1 title=50` | **`length=2 upper=1 title=50`** | hit, exact |
| | `body xings:` | 0 | 50 | **50** | hit, exact |
| | `out deleg` | 198.8% | ≈199.5% | **199.5%** | hit, exact |
| | `handled:` | `TextNode=6 VariableNode=2` | `+354T +201V +51If +1For` | **`TextNode=362 VariableNode=205 IfNode=51 ForNode=1`** | **miss by +2 on T and V** |
| `real-world/large` | every column | — | identical | **identical** | hit, exact |
| `real-world/complex` | `delegated:` | `BlockNode=2 ExtendsNode=2` | `+IncludeNode=73` | **`ForNode=1 BlockNode=4 IncludeNode=1 ExtendsNode=2`** | **miss** |
| | `out deleg` | 298.8% | ≈372% | **494.5%** | **miss** |
| `django-only/realistic` | `delegated:` | `LoadNode=1 BlockNode=3 ExtendsNode=1` | `+TranslateNode=1 ForNode=1` | **`TranslateNode=2 ForNode=1 …`** | **miss: 2, not 1** |
| | `out deleg` | 197.8% | ≈290% | **295.0%** | near hit |

### The hit that this slice was built on

`real-world/normal` `applied: length=2 upper=1 title=50` and `body xings: 50` are
**identical to `flat/normal`'s row**, which is the prediction's own sharpest
gate: the same page with the inheritance spliced out, same bytes out, and the
same three filtered expressions counted the same two ways.  `body xings: 50`
rather than 0 is the part that matters — `applied:` alone would have said the
walk *routed* `title=50` without computing it (the distinction
`6231b04` is about), and a routed-but-not-computed reading is exactly the
failure a share meter cannot see.

`handled:` is `flat/normal`'s plus **one** `TextNode` (362 vs 361; `VariableNode`
205 on both): the layout's own text node, which `normal` visits and the spliced
fixture does not, because on `normal` the walk renders the base template's
non-block text after the delegated `ExtendsNode` returns.

### The miss in `handled:` is the derivation, not the phenomenon

The prediction derived `6+354 = 360` `TextNode` and `2+201 = 203` `VariableNode`
from the block bodies' source; measured is 362 and 205.  Both are +2, and both
land on `flat/normal`'s own counts (+1 `TextNode`).  So the *page* is what the
prediction said it was; the arithmetic undercounted the outer text and variable
nodes by two.  Stated that way round deliberately: the row and the derivation
disagree, and the row is the one that was measured.

### `real-world/complex`: the prediction contradicted itself, and the census is right

§3.1 predicted `+ IncludeNode=73` for `complex` and derived it from "the inner
body does not seal and the walk visits the include 72 times".  Those two clauses
cannot both hold: `seal` refuses a body containing a delegate, so **a loop whose
body holds an `{% include %}` is delegated whole and the walk never reaches the
includes at all** — which is precisely what §3.1's own `django-only/realistic`
paragraph says about a loop holding `{% blocktranslate %}`.  The census agrees
with the second reading: `delegated: ForNode=1 … IncludeNode=1`, one include (the
layout's own) and one delegated loop, so the 73 routed `upper` applications and
the 297 text nodes come from the block body *outside* that loop.

And that is why `complex`'s render does not move (§2): `out deleg` rises to
494.5%, i.e. nearly five times the render's own output comes back from Python,
because the delegated `ForNode` returns the whole table.  A share meter that
rose is the correct reading of a slice that moved work into a frame the meter
cannot see inside — the generalisable statement §3.2 makes, now with a measured
instance.

### `django-only/realistic`: the same shape, one node further

`+ TranslateNode=2 ForNode=1` where the prediction said 1 and 1.  The loop is
delegated (it holds the per-row `{% blocktranslate %}`), so `body xings` stays
**0** — the opposite of `normal`'s 50, and the pair is the point §3.1 made: the
same count of delegate-bearing rows, reached and not reached.  `out deleg` at
295.0% against a predicted 290% is the near hit that says the *mechanism* was
predicted right even though the count was not.

## 2. Timing — the render/parse pair

| row | mode | before A / B | predicted | measured A / B | verdict |
|---|---|---|---|---|---|
| `real-world/normal` | render | 0.903 / 0.895 | 0.33–0.45 (ceiling 0.298) | **0.315 / 0.312** | **hit, and below the interval** |
| `real-world/normal` | parse | 0.996 / 0.987 | 0.85–0.99 | **0.562 / 0.557** | **miss — the row is not a control** |
| `real-world/complex` | render | 0.619 / 0.614 | 0.45–0.60 | 0.633 / 0.610 | **miss: reached, effect below the band** |
| `real-world/complex` | parse | 0.687 / 0.692 | 0.62–0.72 | 0.706 / 0.680 | hit |
| `django-only/realistic` | render | 0.923 / 0.915 | 0.40–0.60 | 0.921 / 0.920 | **miss: reached, effect below the band** |
| `django-only/realistic` | parse | 0.974 / 0.964 | 0.85–0.99 | 0.971 / 0.975 | hit |
| `real-world/large` | render | 0.298 / 0.298 | 0.28–0.32 unmoved | 0.305 / 0.305 | **unresolved, inside the band** |
| `micro/extends` | render | 1.021 / 1.022 | 0.70–0.95 | 1.016 / 0.990 | **miss: unmoved** |
| `micro/extends` | parse | 1.238 / 1.222 | — unmoved | 1.266 / 1.288 | **+3.8%: the compile cost** |

**`real-world/normal` is 0.899 → 0.313.**  That is −65%, 53× the `after` band's
median and 18× its max.  In microseconds: 369.3 → 128.1 ours against a stock
410.8 → 409.2.  And it is **above the 0.298 ceiling** `step5_ceiling.py` set from
`flat/normal`, which is the check that keeps a very good number from being read
as a broken fixture.

Every `after` figure in this section is the **median of the two committed runs**,
`bench-post-{a,b}.json`.  `docs/bench.html` is regenerated from the shipped
artifact as a *single* run, and its ratios are the cross-check that the pair is
not drifting: `normal` 0.317 render / 0.554 parse, `complex` 0.613 / 0.696,
`realistic` 0.916 / 0.969, `large` 0.300 / 1.098, `micro/extends` 0.991 / 1.271 —
each within the band of the pair's numbers below.

**The parse row was mislabelled as a control, and the repair is harder than
swapping an instrument.**  §3.3 called `parse` "the control" on the reasoning
that a per-render saving must not show up in a row that never renders.  It does
show up, because `bench.py`'s `parse` mode times
`engine.render(engine.from_string(source), context)` — **compile *plus* render**
— so it moves whenever render moves.

**And the obvious repair, `parse − render`, is not compile either.**  Both terms
are in the file and both are the wrong quantity: `parse`'s render is a **cold**
render of a template compiled on that very iteration, while `render` mode's is a
**warm** handle whose caches are filled, so the subtraction carries
`compile + (cold render − warm render)` and the second term is not small.  On
`real-world/normal` it gives ours `560.3 − 369.3 = 191.0 µs` before and
`317.8 − 128.1 = 189.7` after — our compile moved **−1.3 µs** — while
`compile_only.py` on the same two artifacts measures **+17.5 µs**
(149.3 → 166.8).  The two instruments do not merely differ in size: **they
disagree in sign**, which is the clearest statement available that they are not
the same quantity.

`compile_only.py --corpus micro/extends,real-world/normal,real-world/complex`,
twice per tree, artifact pinned (µs, median of the two runs per side;
`compile_only.step5-{pre,post}{,-b}.txt`):

| row | ours before | ours after | rel | stock before → after |
|---|---|---|---|---|
| `micro/extends` | 23.26 | 24.33 | **+4.6%** | 16.67 → 16.71 (−0.2%) |
| `real-world/normal` | 149.32 | 166.81 | **+11.7%** | 123.52 → 124.55 (+0.8%) |
| `real-world/complex` | 101.74 | 112.93 | **+11.0%** | 90.27 → 90.58 (+0.3%) |

So the descent pass is a **per-compile cost of +4.6% to +11.7% on our own
compile**, while stock's is flat within 0.8%, widening the compile gap on
`real-world/normal` from 1.21× to 1.34×.  That is the term `parse` mode cannot
separate and `render` mode cannot see at all — a one-off paid on every compile,
and `parse` compiles every iteration.  The parse row's move is therefore
**render's saving minus compile's new cost**, and on `real-world/normal` the
saving is ~240 µs against a ~17 µs cost.  The row that *would* have been a
control is the compile-only one, and the correct reading of the pair is the one
`render-parse-pair-is-the-diagnostic` states: both rows read together as *render
+ per-compile constant*, not as *treatment + control*.

**`micro/extends` is where the compile term is visible in the pair with no
arithmetic at all.**  Its render did **not** move — 1.021 → 1.003, −1.8%, inside
the band (the `after` pair's median is 1.23%) — while its parse moved **+3.8%**
(1.230 → 1.277), just above the `after` band's max of 3.67%.  Compile-only on
that row measures **+4.6%** independently and at the same size.  So the one row
where the two terms are comparable shows both: render flat, compile up, parse
up.  (This file's first version read that row as "moved only −5%" against a
1.043 `before`; on the right tree it is unmoved, and the band is what says so.)

**The unmoved render rows are where the walk reaches nothing that is the page,
and §1 says so independently.**  `complex`'s bulk is a delegated loop of
includes; `realistic`'s is a delegated loop of 50 `{% blocktranslate %}` rows.
Both were predicted to move (0.45–0.60 and 0.40–0.60) by reading "the block body
is now Rust" as "the page is now Rust", and the census's `out deleg` at 494.5%
and 295.0% said otherwise before the timings did.  But **"unmoved" here means
reached-and-below-the-band, not untouched**: `complex`'s census moved —
`applied: upper=72 → upper=73`, `handled: TextNode=294→297 VariableNode=218→219`
— so the slice does reach it, by one routed-and-computed application in the
layout text outside the delegated loop, and the effect is under the instrument's
resolution.  `micro/extends` is unmoved for the opposite reason: its bodies are 3
and 4 nodes, so the render is dominated by the two Python frames (`ExtendsNode`
and `BlockNode`) and the per-render
`compiled_parent.nodelist.get_nodes_by_type(BlockNode)` walk — the residual §3.3
named and then priced too optimistically as "wide because unmeasured one frame at
a time".  The measured residual is the honest one: at this size the frames are
the cost, and a body plan does not touch them.  **A slice that walks a block body
pays where the body is the page, and nowhere else.**

**`real-world/large` is the row whose movement I could not attribute, and five
runs are what settle it as unresolved rather than as an effect.**  Its render
ratio reads 0.298 / 0.298 on the pre-slice pair, 0.305 / 0.305 on this slice's
pair, and **0.300 on the regenerated `docs/bench.html` run** — a span of 2.3%,
inside the `after` band's own max of 3.67%, with our absolute render at
199.7–207.0 µs across the five.  Its parse reads 1.073 / 1.074 → 1.091 / 1.103 →
1.098, the same span.  The row has no `{% extends %}` and no `{% block %}` in its
source and its census is identical in every column (§1), so nothing in this
slice's mechanism can reach it: the honest statement is that this row's ratio is
not resolvable to better than the band, **not** that it moved.  It is the one row
here I would rather have had a smaller instrument for.

## 3. What this leaves for step 5 slice 2

The two unmoved rows *are* the case for slice 2, and they say what it has to be
rather than how big it is:

* `ExtendsNode.render` must stop being a delegate — `out deleg` can only fall on
  `normal` once the frame that returns the whole page is Rust's.
* a loop whose body holds a delegate must seal anyway, with the delegate
  *inside* the seal, or `complex` and `realistic` cannot move at all: their
  loops are the page.  That is a different mechanism from slice 1's (a body plan
  attached to a `NodeList`) and the census is what separates them.
* the per-render `get_nodes_by_type(BlockNode)` walk over the whole parent is
  paid on every render of an inherited page and is invisible in every column
  here — it is a candidate for the compile-time cache slice 1 already has a
  place for.

And one follow-up the slice's own shape asks for, which is **not** in slice 2:
`plan_block_bodies` should report its own coverage — bodies visited, planned,
declined — so that a missing attach is a number rather than a gap.  Today
`NodeList._native_plan = None` is a class default and the failure mode it hides
is a silently unplanned body that renders correctly and slowly, which is exactly
the class of miss this record has already had to correct twice.  It changes the
census's printed shape, which every M19 record cites, so it wants its own slice
rather than a line in this one.

## 4. What did not move, and must not

* **The acceptance suite: `Ran 1547 tests … OK (skipped=6)`**, plus the 494 unit
  tests — both from `scripts/check.sh`, on the artifact this file measures.  The
  vendored suite is that script's `django_suite/run.py -v 2 template_tests` step
  against the pinned, unmodified checkout, the unit suite `-m unittest` through
  `run_unit_tests.sh`.  Block semantics are the most heavily tested thing in the
  suite (`test_extends.py`, `test_partials.py`, `recursive_templates/`).
* **Every bench row's `out` column: `===`,** three engines byte-identical,
  including `django-only/realistic`, `micro/extends` and `real-world/normal`.
* **`real-world/large`'s census, all columns.**  No `{% extends %}` and no
  `{% block %}` in the source, so any move there would be a bug in the descent;
  measured identical, as predicted.
* `flat/normal`, `nested for (2 deep)` and the remaining `django-only` rows:
  identical, for the same reason.

One thing did move that is not a row: **our compile, and only our compile.**  §2
measures it at +4.6% to +11.7% on the three rows probed, with stock flat inside
0.8%.  It is called out here rather than left in §2 because a cost that appears
in no `render` row is the one a reader of the tables will not find.

## 5. The instruments

| file | what it settles |
|---|---|
| `bench_scatter.py` | the same-tree scatter band, with a `sha256:` guard against comparing two builds |
| `compile_only.py` | what a `parse` row cannot separate and a `render` row cannot see: `from_string(source)` with no render, and so the slice's per-compile cost |
| `node_census.py` | the structure behind the timings; its committed `.txt` is §1's `before` column |
| `inheritance_share.py`, `lazy_size.py`, `step5_ceiling.py` | the prediction's inputs; unchanged |

The four bench payloads (`bench-{pre,post}-{a,b}.json`) and the four compile-only
runs (`compile_only.step5-{pre,post}{,-b}.txt`) are committed beside this file:
they are what `bench_scatter.py` and the §2 table were computed from, and a band
quoted without its input is a number a reader cannot check.

All `.review/` records rather than shipped instruments, and none on the gate's
path. **One of them is no longer only a record.** `scripts/bench.py` gained a
third mode, `compile` — `from_string(source)` alone, across the whole corpus, on
the report's own floor and byte-identity discipline — because the report had
`render` and `parse` and no way to show the thing this slice moved most: the two
committed modes could separate neither of the terms the per-compile cost is made
of, and slice 2 is another descent pass with the same shape of cost. The mode is
not `parse − render`; its docstring carries the sign disagreement that killed
that subtraction.

It was checked against `compile_only.py` rather than trusted, and the two land in
the same place on all eight shared sources at different pinned iteration counts
(the probe's 200, the mode's calibrated per row), which is what makes it a second
instrument rather than the same one twice. **The bound, since "agree" without one
is not a number:** bench.py's `rust` cell is within 2.13% of the probe's two-run
span and its `stock` cell within 2.50%, and the ratio within 3.48%. Six of the
eight ratios sit 1.2%–3.5% *below* the probe's span and the two real-world ones
0.55% and 1.02% *above* it; the µs columns carry no consistent sign. Two of the
eight `rust` cells and one `stock` cell fall inside the span exactly, and no
ratio does. The gap is unattributed and the two instruments differ in more than
one way (iteration count, and `measure`'s `max(3, iters // 4)` warmup against the
probe's), so it is stated rather than explained. The three rows worth naming:
`micro/text` ours **6.71** against 6.71–6.83, `micro/extends` **24.09** against
24.16–24.49, `real-world/normal` **166.36** against 166.12–167.50.

Its first reading, as levels rather than as a delta:
the compile floor is **ours 4.63 / stock 1.49 / jinja2 56.3 µs**, so on a small
template most of our compile is fixed cost — `micro/text` is 6.7 against stock's
1.9, and the floor is quoted beside that row rather than subtracted from it,
which is `measure_floor`'s own rule (a floor is an intercept at one point, and
`micro_slope.py` puts a loop's at ~4.0 µs against the empty template's ~1.7). On
the page the slice is about, `real-world/normal`
compiles in 166.4 µs against stock's 122.6, a ratio of **1.36×** where the
pre-slice tree's was 1.21×.

The report is still a snapshot and not a differ: it shows the compile *level* per
row, so the tax becomes visible by comparing this `docs/bench.html` with the
previous one, not within a single page. A `--compare <payload.json>` that carries
the band and prints the delta is the obvious next instrument and is not this one.

