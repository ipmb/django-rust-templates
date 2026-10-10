# Step 3a — the walk owns a filtered expression and a literal `{% if %}`: outcome

The planner gains two node forms and one consequence:

* `plan_emit` — a `{{ … }}` whose expression is a plain lookup followed by a
  filter chain Rust can name.  The head comes from the descriptor; the chain
  comes from `FilterExpression.filters` routed by handle through
  `django_own_filter_names`, so the walk calls the filter body directly instead
  of handing the node back for Python to call it.
* `plan_if` — a `{% if %}` whose condition is a `TemplateLiteral`.  An operator
  condition still declines the whole node (`IfConditionDesc::Operator` →
  `Ok(None)` under `plan_if`'s `Act::Delegate`), which is why the census's
  control for this step is an operator condition and not a bare lookup.
* `seal` therefore accepts a loop body containing either.  **That is the reason
  the step is worth taking**, and it is not visible in the two constructs' own
  rows: a body is sealed only if *every* node in it is walk-rendered, so one
  filtered variable or one delegated `{% if %}` anywhere inside a loop hands the
  whole loop — and the whole table it builds — back to Python.

The prediction was written and committed first, as
`.review/m19-measurements/step3a_prediction.md`, from the baseline census taken
on `0f5ae85` (the commit under this one).  Every row below is
**baseline → predicted → measured**, and the two rows that are claims about the
*seal* rather than about a node count are called out where they appear.

## 0. Two meters, because one of them cannot tell the fast route from the broken one

The interposition tests read `crossed == []` to mean "the walk answered this
itself".  After this step that reading is unsound: routing can break — a handle
missing from `django_own_filters()` — and the node then *declines*, Python's
`FilterExpression.resolve` loop applies the filter from the transcription, and
**`crossed` is still `[]`**, with the right answer.  So the acceptance is two
meters and not one:

  * `_native.filter` / the `crossed` list — did the call cross from Python?
  * `Census::filters` / the census's `applied:` line — did the walk apply it?

The fast route reads `applied: [(name, n)]` with `crossed == []`; the broken
route reads `applied: []` with `crossed == []`.  That argument lives in
`tests/test_interposition.py::FilterRoutingTests`'s class docstring and in the
rows that carry both meters.

## 1. Structure — the census

`node_census.py`, 20 renders per row, release artifact `sha256:34386acb…`.
Baseline is the same instrument on the pre-slice tree.

| row | `out` delegated before | predicted | measured | |
|---|---|---|---|---|
| `micro/if` | 100.0% (9/9) | 0.0% | **0.0%** (0/9) | met |
| `micro/filter` | 100.0% (14/14) | 0.0% | **0.0%** (0/14) | met |
| `flat/normal (no extends)` | 98.2% (5623/5728) | ≈0.7% (41/5728) | **0.7%** (41/5728) | met to the character |
| `real-world/large` | 3.5% (145/4121) | 0.0% | **0.0%** (0/4121) | met |
| `real-world/normal` | 198.8% | unmoved | **198.8%** | unmoved, as predicted |
| `real-world/complex` | 308.6% | unmoved, node share down | **298.8%**, node share 14.9% → **0.8%** | both halves |
| `django-only/for-tags` | 99.6% (3408/3423) | unmoved | **99.6%** (3408/3423) | unmoved, as predicted |
| `nested for (2 deep)` | 100.0% (32/32) | 100.0% | **100.0%** (32/32) | met — the seal's negative control |

**The node-level deltas, which is where the slice is legible.** Each row is
`delegated:` before → after, with the `handled:` side that paid for it:

| row | `delegated` before | `delegated` after | `handled` grew by | `applied:` |
|---|---|---|---|---|
| `micro/if` | `IfNode=1` | — | `TextNode=1 IfNode=1` | — |
| `micro/filter` | `VariableNode=1` | — | `VariableNode=1` | `upper=1` |
| `micro/include` | `VariableNode=1 IncludeNode=1` | `IncludeNode=1` | `VariableNode=1` | `length=1` |
| `real-world/normal` | `VariableNode=1 BlockNode=2 ExtendsNode=1` | `BlockNode=2 ExtendsNode=1` | `VariableNode=1` | `length=1` |
| `real-world/complex` | **`VariableNode=73`** `BlockNode=2 ExtendsNode=2` | `BlockNode=2 ExtendsNode=2` | `VariableNode=145→218` (+73) | `upper=72 length=1` |
| `real-world/large` | **`IfNode=20`** | — | `IfNode=20`, `TextNode=401→421`, `VariableNode=320→330` | — |
| `flat/normal` | `VariableNode=2 IfNode=1 ForNode=1 IncludeNode=1` | `IncludeNode=1` | 11 → 618 visits, `ForNode` now handled | `length=2 upper=1 title=50` |

Two things reconcile exactly and are worth keeping as the instrument's own
check.  `real-world/complex`'s delegated variable set is **73**, and the walk's
own `applied:` line for that row is **72 + 1 = 73** — the node delta and the
meter are the same number arriving two ways.  `real-world/large`'s 20 delegated
`IfNode`s become 20 handled `IfNode`s whose branches add 20 `TextNode`s and 10
`VariableNode`s — the walk now descends into the branches it used to hand back.

**The seal half, asserted separately because a `delegated:` line cannot show
it.** `flat/normal`'s `handled:` goes from 11 visits to **618** with `ForNode=1`
under `handled:` and only `IncludeNode=1` under `delegated:` — the walk iterates
the loop itself, 50 iterations × ~7 body nodes.  Its 41 remaining characters of
delegated output are `bench_corpus.FOOTER` and nothing else.  `real-world/large`
shows the same shape at 461 loop iterations.  `nested for (2 deep)` is the
control: `ForNode` stays under `delegated:` with an empty `handled:`, because
`seal` refuses a body that contains a nested `{% for %}`.

`.review/m19-measurements/seal_probe.py` was written to discriminate *which*
delegated construct kept a loop unsealed, by baking them one at a time.  Every
variant of it now seals, so the probe has stopped discriminating and its
docstring says so; it reads today as a regression witness.

## 2. Timing — the render/parse pair, with the scatter bound stated first

`scripts/bench.py`, release artifact `sha256:34386acb…`, `.venv/bin/python -I`,
Django 6.0.9, same machine, `--repeats 7`.

**The scatter first, because it is what makes the rest a claim.**  Two runs of
the *same* artifact on the *same* tree, ratio-against-ratio over all 26 rows:
**median 0.81%, p90 2.55%, max 4.20%.**  Nothing smaller than that is called a
move below.

`HEAD` is the committed report at `0f5ae85` (same corpus, same procedure); `A`
and `B` are the two post-slice runs.  Ratio is ours/stock, **lower is better**,
1.00 is parity.

| row | mode | HEAD | A | B | A/HEAD | B/A |
|---|---|---|---|---|---|---|
| `micro/if` | render | 1.230 | 0.954 | 0.935 | **−22.4%** | −2.0% |
| `micro/filter` | render | 1.060 | 0.870 | 0.870 | **−17.9%** | 0.0% |
| `real-world/complex` | render | 0.690 | 0.626 | 0.612 | **−9.3%** | −2.2% |
| `real-world/complex` | parse | 0.760 | 0.689 | 0.680 | **−9.3%** | −1.3% |
| `real-world/large` | render | 0.320 | 0.301 | 0.306 | −5.9% | +1.8% |
| `micro/filter` | parse | 1.500 | 1.411 | 1.434 | −5.9% | +1.6% |
| `real-world/normal` | render | 0.900 | 0.901 | 0.904 | +0.1% | +0.4% |
| `real-world/normal` | parse | 0.990 | 0.993 | 0.999 | +0.3% | +0.6% |
| `micro/if` | parse | 1.720 | 1.769 | 1.754 | +2.8% | −0.9% |

The two constructs this step is about are the two largest moves, and they are
5× and 4× the max scatter.  The step's own acceptance line — the engine's
headline against stock, which §5 sets as *the real-world render geometric mean
must not regress above 1.00* — reads **0.58× → 0.55× at render and 0.94× → 0.90×
at parse** in the report's own tiles, and both tiles hold on run `B`.

Three honest qualifications, all of them visible in the table:

* **`real-world/complex` moves −9.3% in *both* modes.**  A per-*compile* cost
  would show only in parse mode, so the render row moving on its own is what
  says the saving is per-render, and that is what the census says it is: 73
  delegated variable *visits* that stopped crossing.  It is the only whole-page
  row with a move outside the scatter, and it is the row whose body is not
  behind a `BlockNode`.
* **`real-world/large` render, −5.9%, is at the scatter's own max (4.20%)** —
  direction consistent with the census (145 characters of output stopped coming
  back from Python, and `IfNode=20` off `delegated:`), and not a number to lean
  on.  Its parse row is flat (−1.2%).
* **`micro/if` *parse* is flat-to-worse (+2.8%, inside scatter).**  This is the
  case §4 item 9 exists for and it is not a puzzle: the per-render saving is
  ~0.1 µs against a compile path that costs ~11 µs more than stock's, so parse
  mode — which calls `from_string` every iteration — cannot see it.  The render
  row for the same construct moves −22.4%.  Flat-in-parse here means the render
  win is small in absolute terms, not that the parse path was made worse.

## 3. What did not move, and why it must not

`real-world/normal` (+0.1% render, +0.3% parse) and `django-only/for-tags`
(+2.3% / +2.6%) are the rows the prediction said would be unmoved, and the
census says why in the same breath: `normal`'s body sits behind two `BlockNode`
delegates, and `for-tags`' body holds `{% cycle %}`, `{% widthratio %}` and a
`{{ forloop.counter }}` read that `seal` refuses outright.  Baking the leaves
inside a block changes nothing about who renders the block, and a body with one
unplannable node is Python's in full.  The negative controls in §1 are the same
statement from the other side.

**So the census is the instrument that carries this slice, and the bench is the
one that prices it.**  The census's 0.7% row is where the step's whole
justification lives — a loop that seals — and it is a row `bench.py`'s corpus
does not contain; the bench's largest honest move is `complex`'s −9.3%, because
the pages it can reach are the ones this step could already reach.
