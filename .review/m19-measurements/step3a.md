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

## 0. Three meters, because `applied` is a statement about routing and not about who computed

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

**And the pair is still one short, which is the reviewer's catch.**  `applied` is
about **routing**: the walk reached Rust's registry for that name.  It is not a
statement that Rust computed the answer, because a Rust body may call back into
Python — `escape`'s body **is** `conditional_escape`
(`crates/template-core/src/filters/escape.rs`, one host call and no branch of its
own), `lower`'s is Python's `str.lower` through `utility_text`, and
`truncate`/`urlize`/`wrap` call Django's.  So for `{{ x|escape }}` the walk
reports `applied: [("escape", 1)]` with `crossed == []`, and both are true while
the escaping ran in Python — M11's finding one level further in.  Hence the third
meter:

  * `Census::body_crossings` / the census's `body xings:` line — how much Python
    ran **inside** the bodies `applied:` counted?

**Three properties make it trustworthy, and each is a decision rather than a
detail.**  The count is of *crossings* and not of host-method calls: `upper` is
`to_str` — pure for a `Value::Str` (`crates/template-core/src/filters/upper.rs`)
— plus a Rust table, and reads **zero**, so the number separates "the body is
Rust's" from "the body is Rust's and then asks Python".  The window is the
**body** and not the application: `run_filter_inner` reads its host's counter on
either side of `(entry.run)(...)`, so the seam's own work — `classify`, the
`@stringfilter` coercion, `localtime`, `is_safe`, the loop's `mark_safe` — is
outside it by construction, which is what makes `upper`'s zero mean "the body"
rather than "the wrapper".  And it counts only the **walk's** applications: the
seam has a second caller (`_native.filter`, the fallback arm) and counting both
put a number beside an `applied: -` row that reads as if the walk's empty set of
bodies had crossed.  Measured before that flag was added, `django-only/filter-arg`
printed `applied: -` with `body xings: 200` — the 200 being the *Python* arm's
`{{ …|title }}` inside the delegated `ForNode` — and three rows had that shape.
With it, the two lines are about the same set of bodies by construction, so
"applied, and read zero" means what it says.

The rows are in `FilterRoutingTests`: one that must read non-zero — `lower`, and
`escape` as the reviewer's own case — and one that must read zero, `upper`.  The
43 `self.crossed()` calls behind the meter, one per host method that reaches
Python, are gated by
`FilterCrossingCoverageTests` — a *source* row, because a missing bump site is
invisible from the Python side: the meter is a lower bound and reads zero for a
body that crossed and was not counted.  Its rule is reachability — a
`self.crossed();` in the body, or a call to a method that transitively has one —
over the trait impl's entry points, and its positive control cuts a bump out of
the source in memory and requires the rule to name it.

## 1. Structure — the census

`node_census.py`, 20 renders per row, release artifact `sha256:34386acb…`,
re-run for this record on the third-meter build `sha256:dff8b62d…`.  Baseline is
the same instrument on the pre-slice tree.  **The re-run is additive and that is
its own measurement:** the diff against the committed `node_census.txt` is 22
inserted lines — the `body xings:` line per row and the `check_flat_normal` line
— with every pre-existing number unchanged, so the meter's arrival moved nothing
it measures.

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

**`flat/normal` is a hand-made page, and the prediction is what defends it.**  It
is not a corpus row: it is `real-world/normal` with the inheritance spliced away,
because the question this step's whole justification turns on — does a loop seal
once nothing above it is a `BlockNode`? — is a question about *reachability*, and
the shipped page cannot answer it while two `BlockNode` delegates sit above the
body.  A fixture written to pass is the classic way a gate narrows toward
passing, and the defence here is not the fixture's shape but the *order*: the
acceptance row was published from a baseline census **before** the slice was
written, predicted `flat/normal` at ≈0.7% (41/5728), and landed on 0.7%
(41/5728) — including the 41 characters being exactly the corpus's `FOOTER`.  A
fixture chosen after the fact could not have that property.  The reviewer showed
the hazard from the other side in one round: splicing its own "normal without its
blocks" by hand, it got a *different* page and a different number (99.6%
delegated output against this row's 98.2% baseline), which is what a hand-typed
lookalike does.

**So the splice is mechanical now, and the claim says what it rests on.**  The
fixture is derived in code from `bench_corpus.BASE`/`NORMAL`
(`node_census.py:flatten_inheritance`, which refuses a source whose blocks nest
rather than cutting it wrongly), and `check_flat_normal` renders the shipped page
under stock Django, the shipped page under this engine, and the derived page
under this engine, requiring **one** byte string from all three.  Re-run with the
derived fixture, every number in the committed `node_census.txt` is unchanged —
which is the check that the derivation is the same page the row was measured on.
And the honest sentence: **this row's page does not ship.**  What ships is
`real-world/normal`, whose `out` row is unmoved at 198.8%.  The loop-seals claim
is about what the walk can reach *once inheritance is out of the way*, and the
slice that has to bring the shipped page along is the next one.

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

**The third meter's own line reads legibly on exactly one row, and that is the
right number of rows.**  `flat/normal` prints `applied: length=2 upper=1
title=50` with **`body xings: 50`** — 50 applications of `title` entering
Python's `str.title` once each (`str_title` is `call_text`, one host call per
body), and `length`'s two and `upper`'s one contributing nothing, because a
`Value::List`'s length is Rust's and `upper` is a Rust table.  Every other row
reads `0`, which is true of them: the shipped `real-world/normal` applies only
`length` (`applied: length=1`, `body xings: 0`), and its 50 `title` applications
are the *Python* arm's, inside the delegated `BlockNode` — the rows whose
`out` share is ~199% have nothing the walk applied to report on.

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
move below.  **What it does not license:** both band runs were adjacent, so what
it samples is short-timescale noise, while the `HEAD` ↔ `A`/`B` comparison below
also crosses two reports and so carries thermal and load drift the band never
saw.  The band is the *floor* of what a cross-report delta owes and not a
substitute for it, which is why only the two constructs this step is about — at
5× and 4× the band's max — are treated as moves at row level, why the report's
own tiles are additionally checked between the two *post*-slice runs, and why
`real-world/large`'s −5.9% is kept with its caveat rather than promoted.

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
* **`real-world/large` render, −5.9%, clears the median and p90 and nothing
  more.**  The direction is consistent with the census (145 characters of output
  stopped coming back from Python, and `IfNode=20` off `delegated:`), so it is
  kept here with its number — but the band it is measured against is a
  *within-report* one (both its runs were adjacent, sampling short-timescale
  noise), while this A/B crosses two reports and so adds thermal and load drift
  the band never saw.  For a single row under 10%, that licence is thin: it is
  not a number to lean on, and the sentence is the row's status rather than a
  defence of it.  (The reviewer's correction to an earlier draft of this
  paragraph: testing one row's delta against the *max* of 26 rows is an
  extreme-value statistic on a tiny sample and is the wrong test in both
  directions — median and p90 are the usable figures.)  Its parse row is flat
  (−1.2%).
* **`micro/if` *parse* is flat-to-worse (+2.8%, inside scatter), and the reason
  is measured rather than asserted.**  This is the case §4 item 9 exists for.
  `parse` is `from_string` + render per iteration (`bench.py:194`), so the claim
  decomposes, and `.review/m19-measurements/compile_only.py` times the first term
  alone — `from_string(source)` with no render, the corpus's own sources, ours
  and stock.  On `micro/if`: **compile ours 22.20 µs, stock 10.83 µs (+11.37 µs);
  render ours 2.31 µs, stock 2.42 µs (−0.11 µs)**; the measured parse gap is
  25.83 − 14.53 = **+11.30 µs**, i.e. **~100% of it is compile**.  The reviewer
  ran the same decomposition independently and got +11.312 µs compile, −0.038 µs
  render, with the parts reconstructing the measured parse ratio to 2%.  The
  instrument's own cross-check is that `compile + render` reconstructs the
  measured `parse` row to within 0.23–1.33 µs on all five rows it was run on
  (both engines) — the residual is the two runs' drift, not a term.  So
  flat-in-parse here means the row is a compile measurement this slice did not
  touch and never claimed to, not that the render path was made worse.  (An
  earlier draft of this paragraph said "the per-render saving is ~0.1 µs" without
  saying *which* saving: 0.11 µs is our advantage over stock on that row at this
  commit, where the saving the slice itself produced is the ratio's own
  `1.230 → 0.954` — a different quantity against a different baseline.)

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

## 4. Follow-up — the third argument shape: a node the walk declined and reported as covered

This section is a follow-up to §1, and the finding is the reviewer's.
`django-only/filter-arg` was reporting a `handled` row it did not have: the walk
declined every `{{ r|floatformat:2 }}` in it, and the *census* said so while the
family's node count did not, because a delegated `ForNode` carries the whole loop
with it and appears as one delegate.

**Django parses a filter argument in three shapes, not two.** `plan_steps` read
`(lookup, value)` and took two: `lookup=False` is a constant (`mark_safe(arg)`,
`base.py:768`/`807`) and `lookup=True` is a `Variable(var_arg)` resolved against
the context per render (`base.py:770`).  But `var_chars` is `\w\.\+-`
(`base.py:714`) — digits included — so a **bare number** in the argument position
matches `var_arg` too, and `Variable.__init__` turns it into a literal:
`self.literal = int(var)` with `lookups` left `None` (`base.py:887-895`), which
`resolve` returns without touching the context (`base.py:929-931`).
`base.py:685-687` says why it is spelled this way — *"Numbers are treated as
variables for implementation reasons (so that they retain their type when passed
to filters)"* — and that sentence is the whole reason the third shape must keep
the **`int`** and not its text: `mark_safe(2)` would not merely skip a wrapper, it
would stringify (`SafeString(2)` is `"2"`).  The two are observably different:
`{{ x|floatformat:2.5 }}` on `1.2345` is `1.23` with the literal (`int(2.5)` is
`2`) and `1.2345` with the string, and both readings were taken against stock
Django before the row that asserts them was written.

**`lookups is None` in this arm means the numeric branch ran, and that is an
argument about the regex rather than about `Variable`.**  `Variable.__init__`
reaches `lookups = None` with a literal set by two other routes — the quoted
string at `base.py:906` and, in the `lookup=False` arm, `resolve({})`'s own
`SafeString` — and a reader who inspects the class alone sees three shapes with
`lookups is None`, not one.  What closes it is that `var_chars` cannot spell a
leading quote and `unescape_string_literal` raises unless `s[0]` is one
(`utils/text.py:457`), so within `lookup=True` the `base.py:906` route is
unreachable.  Measured on the pinned 6.0.9: `Variable("2")` → `literal=2`,
`lookups=None`; `Variable("2.")` → `literal=2.0` **and** `lookups=('2','')`, so
`resolve` raises `VariableDoesNotExist` and the node takes the *variable* arm —
"the argument looks like a number" is not the test, "the numeric branch completed"
is.

`plan_steps`'s `lookup` arm read `value.lookups` and returned `Ok(None)` when it
was `None` — which is this shape *and only this shape* — so the node went back to
Python.  Output right, bytes right, coverage absent, and nothing failed.

### The route, before and after

Two artifacts, same machine, adjacent in time: pre-fix
`sha256:4c14186d0f95…` (the arm reverted to `lookups.is_none() → None`, source
hash `b5e47af4…` verified restored afterwards) and post-fix
`sha256:82ce0e2f0d95…`.  Full transcripts: `filter_arg_route.before.txt` and
`filter_arg_route.txt`.

| argument | before | after |
|---|---|---|
| `floatformat:2` (bare) | `delegated VariableNode=1`, `applied -` | `handled`, `applied floatformat=1` |
| `floatformat:2.5` (bare float) | `delegated VariableNode=1`, `applied -` | `handled`, `applied floatformat=1` |
| `truncatechars:3` (bare) | `delegated VariableNode=1`, `applied -` | `handled`, `applied truncatechars=1` |
| `add:2` (bare) | `delegated VariableNode=1`, `applied -` | `handled`, `applied add=1` |
| `add:-2` (signed) | `delegated VariableNode=1`, `applied -` | `handled`, `applied add=1` |
| `floatformat:2` in a loop | `delegated ForNode=1`, `applied -` | `handled`, `applied floatformat=2` |
| `floatformat:"2"`, `:n`, `cut:"a"`, `slice:":2"` | `handled` | `handled` (unmoved) |

Every row's bytes are stock's in both columns — the hole was never a correctness
bug, which is why a differential gate could not have found it and the census did.
The loop row is the one that matters beyond the single expression: the same arm is
what decides whether a `{% for %}` seals, so the fix buys a loop.

### The census, and the one row that moved

Nineteen rows in `node_census.txt`; the diff against the committed file touches
**one of them**, and the other eighteen are byte-identical.

```
django-only/filter-arg   walked  4 nodes   2/4   deleg  50.0%  out 2454/2469  deleg  99.4%
                         handled:   TextNode=2
                         delegated: LoadNode=1 ForNode=1
                         applied:   -                     body xings: 0
                                                                          ↓
django-only/filter-arg   walked  4 nodes 253/254 deleg   0.4%  out    0/2469  deleg   0.0%
                         handled:   TextNode=152 VariableNode=100 ForNode=1
                         delegated: LoadNode=1
                         applied:   floatformat=50        body xings: 200
```

`applied: floatformat=50` and `body xings: 200` are the two meters agreeing on the
new state: 50 applications, 4 interior host crossings each — `floatformat`'s body
is Python.  Under the pre-fix build this row printed `applied: -` with
`body xings: 200`, and that pairing is exactly what the third meter was added to
make visible (§0).

**`django-only/realistic` holds the same argument and does not move, and that is
not a miss.** Its `{{ …|floatformat:2 }}` sits inside a `{% block %}`, so the
`BlockNode` above it is delegated whole and no argument shape can reach the walk
there.  The argument census (`filter_arg_route.py`) counts two numeric arguments
in the corpus and the coverage census moves one of them; both are right, and the
difference is *reachability*, which is the next slice's subject.  Corpus-wide the
argument census reads **0 constant, 0 variable, 2 numeric literal** — and its
control, a plain `\|\w+:[^\s|}]*` regex over the whole source rather than only the
`{{ … }}` tokens, returns the same 2, so the scan is a census of the corpus and not
a floor from a narrow pattern.

### Timing — six runs, three artifacts, and a comment-only control

`scripts/bench.py`, `--repeats 7`, `.venv/bin/python -I`, same machine, ratios are
ours/stock, **lower is better**.  Six runs: **two on the pre-fix artifact
`sha256:4c14186d0f95…`** (`preA`, `preB`) and **four on the fixed code, split
across two artifacts** — `82ce0e2f0d95…` (`postA`, `postB`) and `bc4c750a6940…`
(`postC`, `postD`), which is the *same code* rebuilt after the doc comments in
§4's first paragraph were re-keyed to the line numbers measured here.

**The comment-only rebuild is a control, and it is the control this A/B needs.**
Nothing about it can change behaviour, so if the instrument were reading the
rebuild rather than the code, A-vs-C would show it.  It does not: median 0.73%,
p90 2.05%.  **That is what licenses reading pre-vs-post as a code-only
comparison** rather than a comparison of two builds that differ in two ways.

All six runs' own output is archived under `bench-ab/` (`pre-a`, `pre-b`,
`post-a` … `post-d`), and each file's header line prints the artifact's sha256 and
the profile it came from (`→ release (target/release/lib_native.dylib)`), so the
three-artifact claim above is checkable in the transcripts rather than taken from
this prose.

**The scatter, before the numbers it bounds.**  Same-tree, ratio-against-ratio
over all 36 (row, mode) pairs:

| pair | artifacts | median | p90 | max |
|---|---|---|---|---|
| pre A/B | same, `4c14186d` | 0.81% | 1.91% | 2.90% |
| post A/B | same, `82ce0e2f` | 0.72% | 2.06% | 4.33% |
| post C/D | same, `bc4c750a` | 0.85% | 2.03% | **8.58%** |
| post A vs C | comment-only rebuild | 0.73% | 2.05% | 7.26% |
| post A vs D | comment-only rebuild | 0.76% | 2.44% | 7.11% |

**The max is not a per-row bound.**  Every one of those maxima comes from the same
handful of rows — `real-world/large` parse, `micro/lookup` parse, `micro/loop-text`
parse — so a single row can wander 7–8.6% while the median row repeats to 0.8%.
The band describes the population; for one row the test has to be *that row's own
range across the runs*.  Stated that way, over all six runs:

| row | mode | pre range (2 runs) | post range (4 runs) | ranges |
|---|---|---|---|---|
| `django-only/filter-arg` | render | 1.374–1.398 | 1.086–1.108 | **disjoint, gap +24.0%** |
| `django-only/filter-arg` | parse | 1.401–1.404 | 1.123–1.136 | **disjoint, gap +23.4%** |
| `django-only/escape-table` | render | 0.571–0.585 | 0.592–0.598 | disjoint, gap −1.3% |
| `micro/include` | render | 0.732–0.736 | 0.703–0.717 | disjoint, gap +2.0% |
| `django-only/realistic` | render | 0.923–0.929 | 0.913–0.921 | disjoint, gap +0.3% |
| `micro/extends` | parse | 1.240–1.253 | 1.260–1.273 | disjoint, gap −0.6% |
| `micro/if` | render | 0.930–0.935 | 0.938–0.948 | disjoint, gap −0.3% |
| `micro/loop-var` | render | 0.244–0.251 | 0.253–0.255 | disjoint, gap −0.8% |
| `micro/variable` | render | 0.763–0.763 | 0.780–0.784 | disjoint, gap −2.2% |
| `real-world/large` | parse | 1.069–1.069 | 0.998–1.084 | overlap, span 8.1% |
| `micro/lookup` | parse | 1.555–1.574 | 1.500–1.615 | overlap, span 7.3% |
| the other 25 (row, mode) pairs | | | | overlap, span ≤ 5.0% |

**Nine of the 36 pairs are disjoint, and seven of them are the trap.**  A pre side
with two samples makes disjointness cheap — `micro/variable` render's fires because
both pre runs happened to land on the same value to three decimals, which is a
pre range of width zero and not a separation.  Those seven gaps run 0.3% to 2.2%
and go **in both directions** (five read as regressions, two as improvements), and
none of them can be caused by this change: the corpus holds exactly two filter
arguments (the census above), `realistic`'s sits inside a delegated `BlockNode`, and
the node census — a *different instrument* — reports 18 of its 19 rows
byte-identical.  So disjointness at n=2 vs n=4 is not evidence, and it is exactly
the shape a real finding would have; what separates the treatment is the size of
the gap, 23–24%, and the mechanism.  `micro/include`'s 2.0% and `realistic`'s 0.3%
are named here so nobody quotes them later as wins.

**The treatment, however, is 20% and holds across all four post runs:**

| row | mode | pre (mean of 2) | post (mean of 4) | move |
|---|---|---|---|---|
| `django-only/filter-arg` | render | 1.386 | 1.101 | **−20.5%** |
| `django-only/filter-arg` | parse | 1.403 | 1.128 | **−19.6%** |

The same move in both modes is the diagnostic (spec §4 item 9): a per-*compile*
saving would show in `parse` and not in `render`, and here both move by ~20%, so
the saving is per-render — which is what the census says it is, 253 nodes per
render walked instead of 100 variables delegated from inside a Python loop.  In
absolute µs our render on that row went `623.2 / 626.8 → 483.0 / 483.7 / 488.3 /
488.2` (−22.3% on the means) against the control's own −2.2%: stock Django on the
same row moved `453.7 / 448.4 → 436.5 / 445.5 / 441.8 / 440.6`, in an engine this
change never touches.  The ratio is the measure and the raw pair is the
corroboration, in that order because the control moved.

The family tiles hold, and §1's acceptance line is unaffected — real-world
**render** geometric mean over the five post-fix runs `0.54–0.558` (pre
`0.546–0.550`), **parse** `0.875–0.903` (pre `0.896–0.900`): both far below 1.00,
and the one 0.875 is `postC`'s own wander, which is the *tile* moving 3% between
runs of the same code rather than a regression.  `docs/bench.html` is regenerated
on the shipped artifact and its own tiles read **0.54× render / 0.90× parse**.
`django-only` moves as the one row predicts: render `0.770 / 0.775 → 0.743 /
0.740 / 0.742 / 0.743` (−3.9%), parse `0.939 / 0.936 → 0.896 / 0.899 / 0.901 /
0.900` (−4.2%), and one row of five moving −20% is a family move of
`0.8^(1/5) = −4.4%`, so the arithmetic closes on the mechanism rather than merely
agreeing with it.

### What this row is now, stated plainly

**The row is still a loss against stock: 1.10× at render, 1.13× at parse.**  The
fix moved it from 1.39× to 1.10×; it did not make it a win, and the commit message
says so.  Its remaining 44.7 µs at render (485.8 ours against 441.1 stock, on the
four-run means) is ~0.9 µs per loop iteration, where each iteration applies one
filtered expression, and the census reads 4 interior
host crossings per application because `floatformat`'s body is Python.  **That the
residual is those crossings is a plausible reading and not a measurement** — the
decomposition of it is not taken here, and the next decision that would rest on it
owes its own instrument.

The gate that keeps the fix fixed, in two places: the route table above (a
regression flips those rows back to `delegated`, which is how the hole was found)
and three unit rows in `tests/test_interposition.py` — the argument reaching the
walk, the number reaching the body *as a number* (`floatformat:2.5` vs
`floatformat:"2.5"`, which must not agree), and the loop sealing.
