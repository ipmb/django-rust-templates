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
| `before` | `sha256:4c14186d…` | `bench-ab/pre-{a,b}`, two runs on the pre-slice tree |
| `after` | `sha256:6954e0c1…` | runs `A` and `B` this session, on the built slice |
| `predicted` | — | `step5_prediction.md`, whose own baseline was a *different* run of the pre-slice tree (`sha256:bc4c750a`) |

`scripts/bench.py`, `.venv/bin/python -I`, Django 6.0.9, `--repeats 7
--target 0.05`, same machine.  Ratio is ours/stock, **lower is better**, 1.00 is
parity; every bench row reported `===` (three engines, byte-identical output).

**The band, stated before the numbers it bounds.**  `.review/m19-measurements/bench_scatter.py`
takes two runs of one artifact, ratio against ratio, and refuses two different
`sha256:` values — a scatter measured across two builds is the delta it was
supposed to bound.

| pair | rows | median \|rel\| | max \|rel\| |
|---|---|---|---|
| `after` A↔B (both `6954e0c1`) | 36 | **1.08%** | 8.43% (`real-world/large parse`, 10–23 iters) |
| `before` a↔b (both `4c14186d`) | 36 | **0.82%** | 2.87% |

A cross-artifact delta of two ratios owes roughly the sum of the two bands, so
**nothing under ~2% is called a move below, and the two rows this section treats
as moves are 200× and 44× that.**  What the bands do *not* license: both pairs
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

| row | mode | before | predicted | measured A / B | verdict |
|---|---|---|---|---|---|
| `real-world/normal` | render | 0.894 | 0.33–0.45 (ceiling 0.298) | **0.311 / 0.317** | **hit, and below the interval** |
| `real-world/normal` | parse | 0.990 | 0.85–0.99 | **0.550 / 0.561** | **miss — see below** |
| `real-world/complex` | render | 0.620 | 0.45–0.60 | 0.617 / 0.628 | **miss: unmoved** |
| `real-world/complex` | parse | 0.688 | 0.62–0.72 | 0.693 / 0.705 | hit |
| `django-only/realistic` | render | 0.929 | 0.40–0.60 | 0.915 / 0.923 | **miss: unmoved** |
| `django-only/realistic` | parse | 0.962 | 0.85–0.99 | 0.971 / 0.981 | hit |
| `real-world/large` | render | 0.300 | 0.28–0.32 unmoved | 0.299 / 0.296 | hit |
| `micro/extends` | render | 1.043 | 0.70–0.95 | **0.982 / 0.998** | **miss: moved only −5%** |

**`real-world/normal` is 0.894 → 0.311.**  That is −65%, 60× the `after` band's
median and 8× its max; the before column's own pair spans 0.894 and its scatter
is 0.82% median.  In microseconds: 372.8 → 129.0 ours against a stock 416.8 →
414.7.  And it is **above the 0.298 ceiling** `step5_ceiling.py` set from
`flat/normal`, which is the check that keeps a very good number from being read
as a broken fixture.

**The parse row was mislabelled as a control, and the miss is the correction.**
§3.3 called `parse` "the control" on the reasoning that a per-render saving must
not show up in a row that never renders.  It does show up, because
`bench.py`'s `parse` mode times `engine.render(engine.from_string(source), context)`
— **compile *plus* render** — so it moves whenever render moves.  Decomposed on
`normal`: ours parse 570.6 − ours render 129.0 = **185 µs** of compile
(before: 568.3 − 372.8 = 195.5), against stock's 570.6 − 414.7 = 155.9.  Our
compile is unchanged to within 5% and still ~19% slower than stock's; every bit
of the parse ratio's move is the render's.  The row that *would* have been a
control is a compile-only one (`.review/m19-measurements/compile_only.py`), not
this one — and the correct reading of the pair is the one
`render-parse-pair-is-the-diagnostic` states: flat-in-render alone is ambiguous,
so both rows are read together as *render + constant compile*, not as
*treatment + control*.

**The two unmoved render rows are unmoved because the walk reaches nothing that
matters in them, and §1 says so independently.**  `complex`'s bulk is a
delegated loop of 72 includes; `realistic`'s is a delegated loop of 50
`{% blocktranslate %}` rows.  Both were predicted to move (0.45–0.60 and
0.40–0.60) by reading "the block body is now Rust" as "the page is now Rust";
the census's `out deleg` at 494.5% and 295.0% said otherwise before the timings
did.  **A slice that walks a block body only pays where the body is the page.**

**`micro/extends` moved −5% against a predicted 0.70–0.95.**  Its bodies are 3
and 4 nodes; the render is dominated by the two Python frames (`ExtendsNode` and
`BlockNode`) and the per-render `compiled_parent.nodelist.get_nodes_by_type(BlockNode)`
walk, exactly the residual §3.3 named — but §3.3 then priced that residual as
"wide because unmeasured one frame at a time", and the interval it produced was
too optimistic.  The measured residual is the honest one: at this size the
frames are the cost, and a body plan does not touch them.

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

## 4. What did not move, and must not

* **The acceptance suite: `Ran 1547 tests … OK (skipped=6)`**, plus the 494
  unit tests, on the artifact this file measures.  Block semantics are the most
  heavily tested thing in the suite (`test_extends.py`, `test_partials.py`,
  `recursive_templates/`).
* **Every bench row's `out` column: `===`,** three engines byte-identical,
  including `django-only/realistic`, `micro/extends` and `real-world/normal`.
* **`real-world/large`'s census, all columns.**  No `{% extends %}` and no
  `{% block %}` in the source, so any move there would be a bug in the descent;
  measured identical, as predicted.
* `flat/normal`, `nested for (2 deep)` and the remaining `django-only` rows:
  identical, for the same reason.

## 5. The instruments

| file | what it settles |
|---|---|
| `bench_scatter.py` | the same-tree scatter band, with a `sha256:` guard against comparing two builds |
| `inheritance_share.py`, `lazy_size.py`, `step5_ceiling.py` | the prediction's inputs; unchanged |

All `.review/` records rather than shipped instruments, and none on the gate's
path.
