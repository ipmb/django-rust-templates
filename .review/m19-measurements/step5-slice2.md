# Step 5, slice 2 — a loop that seals with a delegate inside: outcome

**What shipped.**  `Walk::run_stated`: a `{% for %}` whose body holds a node this
walk hands back to Python **seals anyway**, and the loop writes Django's
per-iteration state — `context[loopvar]` and the `forloop` dict — around the
delegate instead of the whole loop going to Python.  The delegate itself is
unchanged, and so is everything the walk already did inside a body.

**What did not ship, said before any number below.**  `ExtendsNode.render` is
still a delegate and the per-render `get_nodes_by_type(BlockNode)` walk over the
parent is still paid on every render of an inherited page.  Step 5 §3 named both
as part of "slice 2"; both are re-scoped to their own slice (§5).  **`micro/extends`
was predicted to move on the strength of the first bullet and does not move —
recorded here as a miss, not as an unpredicted control**, because the mechanism
the prediction named was not built.  The census says the same thing in structure:
`delegated: … ExtendsNode=1`.  **`complex` is the second miss, and it is the one
the built mechanism was supposed to reach** (§1): the bullet it was named on
conflated the two different things that unseal a loop body, and this slice lifts
only one of them.

**Two repairs came out of the probes, and they are why the slice is worth its
probe set.**  Both are the same shape — Django's `ForNode.render` has a *layer*
and *two writes* this walk has to reproduce exactly, and both were unobservable
until a delegate could live in a sealed body.  One was ours and predates this
slice (`forloop`'s `length` key was never written); one predates it too and is
still in the shipped code on the *first* version of this build (§2).  Each is
shown to be repaired by *breaking the repair on purpose* and watching the probe
name the row.  The first's mutant is the one that had to be chased: mutant A —
`write_frame`'s copy — is green across both family-B files, and the search that
explains that green (`stated_forloop_route.py`) is what found the row that turns
**the other** copy red, mutant A″ on `plen`.  §2 is about what each green and
each red means.

## 0. Instruments, artifacts, and the band

| label | artifact | what it is |
|---|---|---|
| `before` | `sha256:6954e0c1…` | `bench-post-{a,b}.json`, the committed pre-slice-2 payloads — **render and parse only** |
| `before` (re-taken) | `sha256:6954e0c1…` | `step2-before-{a,b}.json`, two runs on HEAD's `lib.rs`, all three modes |
| `after` | `sha256:1ec2563e…` | `step2-after-{a,b}.json`, two runs of this build |
| `band` | — | pairs drawn from the two `after` runs, via `bench_scatter.py` |

`scripts/bench.py`, `.venv/bin/python -I`, Django 6.0.9, `--repeats 7 --target
0.05`, same machine, release build.  Ratio is ours/stock, **lower is better**,
1.00 is parity; every bench row reported `===` (three engines byte-identical).

**The `before` side was re-taken, and the reason is a correction to something I
had asserted.**  `bench-post-{a,b}.json` were run before `compile` became a mode,
so they carry two modes to this build's three — and `bench.py --compare` refuses
two payloads that do not measure the same rows, correctly: *"a delta between them
is a delta between two corpora."*  That is the small reason.  The large one is
that `step2-{a,b,c}.json` existed and were discarded, because they were taken at
10:07–10:13 on a tree that was edited at 10:14.  **The edit was a comment** — the
`run_for` empty-arm comment rewritten to cite one span after the anchor gate
failed — and I had been treating a comment-only edit as artifact-neutral.  It is
not.  Measured, on this build, one comment line prepended:

    before the edit:  sha 1ec2563e926cb649   2720496 bytes
    after  the edit:  sha 19f4f10b85b3cb0a   2720496 bytes

Same size, different bytes.  A release Rust binary embeds `lib.rs:NNNN` location
strings; a comment renumbers them.  `bench.py`'s `native:` line **is** a sha256, so
a payload is a claim about a build, and those three payloads were claims about a
build that no longer existed — not wrong numbers, uncitable ones.  Hence
`step2-before-{a,b}` and `step2-after-{a,b}`, both sides on all three modes, both
sides re-taken after the last edit to the tree.

**Two bands, because they are two quantities, and only the second bounds this
comparison.**

| band | pairs | cells | median \|rel\| | max \|rel\| |
|---|---|---|---|---|
| same-build, this artifact | `after-a` vs `after-b` | 54 | 1.06% | 3.91% |
| **control floor, cross-build** | `before-a`→`after-a`, `before-b`→`after-b` | **46** | **0.92%** | **2.42%** |

The same-build band is stated because it is what `--compare` uses by default, and
this comparison is the case that shows why it must not be used alone:
**`bench.py --compare step2-before-a.json step2-after-a.json --band
step2-after-a.json step2-after-b.json --different-artifacts` marks 28 of the 54
cells as moves**, among them `micro/escape render +3.14%` and `micro/filter
compile +3.36%` — rows with no `{% for %}` in their source, and one of them a
*compile* ratio. A band that calls more than half the page a move has measured
one build's short-term noise and called it the instrument; the same-build figure
is quoted here so that the 28 is on the record as the reason the floor exists,
not as a finding.

**46 of the 54 cells are the floor, and they are controls by construction rather
than by inspection.**  A cell the change can act on must be a row with a
`{% for %}` whose body holds a node the walk hands back — the plan-time input
`holds_delegate` reads.  The census (§1) says two rows have that shape after the
change, `django-only/realistic` and `django-only/i18n`, and no row has it before;
every other `(row, mode)` cell is a row whose plan is the same bytes on both
sides.  Their \|rel\| is the floor a cross-build delta owes: same corpus, same
instrument, same two builds, and drawn from the same population as the subject
cells rather than borrowed from a summary.  §4 names its right tail instead of
smoothing it away.

`bench_scatter.py` bounds a delta by the scatter of *its own row* across two runs
of *one* build — the right test for a same-build A/B, and it refuses two
`sha256:` values precisely because a scatter measured across two builds is the
delta it was meant to bound.  But `before` and `after` **are** two builds, so what
a delta owes includes build-to-build layout, which no same-build pair can see.

**The tell that a two-run band is not that floor** is in this comparison's own
first version, and it is worth stating because it is what the 28 above means.
Against one pair of runs, `--compare` called `micro/loop-var render +4.41%` a
move against a 0.86% band — on a row with **no delegate in its loop body at
all**, whose hot path this change does not touch byte for byte.  Run across all
54 cells it calls half of them moves, on the strength of a per-row band built
from two samples.  A band that tight has measured one build's short-term noise
and called it the instrument.

## 1. Structure — the census, before → after, and who moved

`.review/m19-measurements/node_census.py`, both artifacts, one instrument, one
run each: `node_census.slice2-before.txt` is HEAD's artifact (`sha256:6954e0c1…`)
and `node_census.step5-slice2.txt` is this slice's (`sha256:1ec2563e…`), both with
the same tree above the extension.  The committed `node_census.txt` is step 5
§1's `before` column and is **not** overwritten; this slice's `before` is a fresh
measurement rather than that file, because a census is a claim about an artifact
and only one of those two artifacts is this slice's parent.

| row | column | before | after | who |
|---|---|---|---|---|
| `django-only/i18n` | `nodes` | 3/6 (50.0%) | **104/156 (33.3%)** | slice 2 |
| | `handled:` | `TextNode=3` | **`TextNode=103 ForNode=1`** | slice 2 |
| | `delegated:` | `LoadNode=1 TranslateNode=1 ForNode=1` | **`LoadNode=1 TranslateNode=1 BlockTranslateNode=50`** | slice 2 |
| | `out deleg` | 94.1% | **45.3%** | slice 2 |
| `django-only/realistic` | `nodes` | 6/14 (57.1%) | **307/364 (15.7%)** | slice 2 |
| | `handled:` | `TextNode=6` | **`TextNode=206 VariableNode=100 ForNode=1`** | slice 2 |
| | `delegated:` | `TranslateNode=2 ForNode=1 LoadNode=1 BlockNode=3 ExtendsNode=1` | **`TranslateNode=2 BlockTranslateNode=50 LoadNode=1 BlockNode=3 ExtendsNode=1`** | slice 2 |
| | `applied:` | `-` | **`floatformat=50`** | slice 2 |
| | `body xings:` | 0 | **200** | slice 2 |
| | `out deleg` | 295.0% | **211.6%** | slice 2 |
| `real-world/complex` | every column | — | **identical** | — |
| `django-only/for-tags` | every column | — | **identical** | — |
| `real-world/normal`, `real-world/large`, `flat/normal`, `nested for (2 deep)`, every `micro/*` | every column | — | **identical** | — |

**Two rows moved, and both are the shape the slice is about.**  On `i18n` the
`ForNode` leaves `delegated:` and `BlockTranslateNode=50` appears there instead —
fifty delegates the walk now *visits*, one per iteration of a loop it never
reached before.  On `realistic` the same, with the third meter as the extra
evidence: `applied: floatformat=50` **and** `body xings: 200`.  The second is the
one that matters, for the reason step 3a's third meter exists — `applied:` says
the walk *routed* the fifty applications, `body xings: 200` says Python actually
*computed* the filter bodies inside them, four crossings per application on a
`floatformat:2` whose argument is a bare number
(`floatformat_crossings.txt`: four whatever the value, five for a quoted
argument).  An `applied:` without a `body xings:` is exactly the
`handled`-while-delegated reading this census has been corrected for twice.

**`out deleg` falls on both, and the fall is the same fact twice.**  `i18n`'s
94.1% was one delegated `ForNode` returning the whole table; now the walk owns the
loop and only the fifty `{% blocktranslate %}` nodes come back, 8.4 characters
each.  `realistic`'s went **197.8% → 295.0% → 211.6%** across the two slices: up
when slice 1 moved the block bodies into the walk while leaving the loop
delegated (a delegated loop counted *and* nested under the block delegates above
it), down now that the largest single contributor — the loop — is no longer a
delegate at all and only fifty small ones remain, each counted once per delegate
above it.  The census does not clamp that, deliberately, and neither reading is a
partition.

**The refusals held, and each is a named mechanism rather than a coincidence.**
`for-tags`' body reads `{{ forloop.counter }}` (`scripts/bench_corpus.py:266`)
and `head_sealable` refuses a head naming `forloop` (`lib.rs`, "A `forloop` read is
refused outright: the dict it names is one of the two things sealing elides") — so
its `ForNode` is still Python's, on a row whose body holds no delegate-shaped node
and which would otherwise have sealed.  `complex`'s outer body holds the nested
`{% for line in s.lines %}` (`bench_corpus.py:200-202`), `sealable(Act::For) =
false` is unchanged for the `parentloop` reason, and the outer loop still
refuses — which is why `complex` is identical in every column.  The `nested for
(2 deep)` fixture is the same statement at its smallest: one visited node,
delegated, because the inner loop is native and therefore unsealable-around.

**`complex` is this slice's second miss, and step 5 named it as a target.**
`step5.md:264-267` puts `complex` and `realistic` on one bullet — "a loop whose
body holds a delegate must seal anyway … or `complex` and `realistic` cannot move
at all: their loops are the page".  `realistic` moved; `complex` did not, and the
reason is that the bullet named one of the two things that unseal a body where
there are two.  An `Act::Delegate` in the body was `sealable`'s refusal, and this
slice lifts it.  A nested `Act::For` in the body is a *different* refusal,
unchanged, because the inner loop's `parentloop` is the outer `forloop` — the
exact object sealing elides.  So `complex`'s outer loop still goes to Python for
its own reason, which is recorded here rather than folded into the `for-tags`
control: a refusal that *happens* to hold is not the same evidence as a refusal
whose named mechanism is still in force.  The prediction is left as written
(`step5_prediction.md:117-119`, "therefore **cannot seal**") with this paragraph
as its correction.

## 2. Correctness — six probes, two routes, four mutants

Reachability is a claim about the walk's *structure* and §1 settles it.
Byte-identity and context balance are claims about *behaviour* and need their own
instruments.  All six are committed in `.review/m19-measurements/` rather than
left in a scratch directory, because a record's evidence has to be runnable by
its reader.

| probe | what it settles | rows | after (this slice) | before (HEAD) |
|---|---|---|---|---|
| `stated_delegate.py` | templates whose loop body holds a delegate, each rendered by stock Django and by ours and compared byte for byte | 16 | **0 differ** | 0 differ |
| `stated_refused.py` | the *other* route into a sealed loop's Python state — a body holding **no** delegate, where a node refuses at render time and `write_frame` runs | 4 | **0 differ** | 0 differ |
| `stated_reach.py` | that the seal is *reached*: one loop's `ForNode` in `handled` **and** its delegate in `delegated` | 4 | **`handled: VariableNode x3, ForNode x1` / `delegated: LoadNode x1, SimpleNode x3`** | `handled: none` / `delegated: LoadNode x1, ForNode x1` |
| `stated_balance.py` | the context's layer depth across a stated loop and after it, on the success **and** the failure path | 2 paths | **`dicts 2 -> 2`, both paths, both engines** | `dicts 2 -> 2`, both paths, both engines |
| `stated_empty_write.py` | whether a loop body's context write can outlive the loop | 10 | **0 differ** | **1 differ** — `empty_tag.html`, stock `'[]'` vs ours `'[W]'` |
| `stated_forloop_route.py` | the search for a template whose `forloop` read reaches a state dict the walk wrote — the falsifier for "the `length` write is unobservable" | 9 | **0 differ**, and its `plen` row is the falsifier that lands (§ mutant A″) | 0 differ |

**There are two ways a loop's Python state gets written, and the probes are two
disjoint families.**  `stated_reach`'s `subject` row is the crossing itself: one
loop, `ForNode` handled by Rust, `SimpleNode x3` delegated — three crossings for
one delegate, because it is reached once per iteration.  That row is family **A**:
the body holds an `Act::Delegate`, so `ForPlan::context_state` is true and the
loop takes `run_stated`, which builds one `forloop` dict per loop and mutates it
in place.  Family **B** is the one `stated_refused.py` was written for: the body
holds no delegate, the loop is **pure-sealed**, `context_state` is false, and a
node can reach Python only by **refusing** — at which point `delegate_slot` calls
`write_frame` around it.  A byte-identity probe cannot tell which family ran, and
none of the 16 `stated_delegate` rows is in family B.

**Mutant A′ measures that disjointness, and it is the mutant that works.**  Delete
`write_frame`'s loop-variable write, rebuild, and re-run both files:

| probe | rows | result under mutant A′ |
|---|---|---|
| `stated_refused.py` | 4 | **3 differ** — `refuse`, `loopvar`, `after`.  `held` stays `ok` |
| `stated_delegate.py` | 16 | **0 differ** |

The three failures are exactly the rows that need `context[loopvar]`, and their
output difference is the value going missing rather than an exception —
`refuse` renders stock `['0xd800']` against ours `[]`, `loopvar` renders stock
`['0xd800', '0x58']` against ours `['0x58']`.  `held` is the control drawn from
the same population: the same loop, fed a value Rust can hold, where nothing
refuses and `write_frame` therefore never runs — and it is the row that keeps 0
differ, which is what says the mutant broke the route and not the probe.  The 16
greens beside it are the second half of the measurement: those rows write their
state through `run_stated`, so `write_frame` is not on their path at all.

**Repair 1 — the `{% empty %}` arm rendered outside any layer, and that one was
still in the shipped code.**  Django's `with context.push():` opens *before* the
empty test (`engine/defaulttags.py:198`) and returns the empty arm from inside it
(`:205-206`), so nothing a `{% for %}` renders — the empty arm included — writes
into a layer that outlives the tag.  Our sealed `run_for` returned
`self.run(&plan.empty, None, depth)` before any push.  It is observable, and
`stated_empty_write.py` is the row that shows it:

    {% for r in none %}{{ r }}{% empty %}{% writes "foo" %}{% endfor %}[{{ foo }}]
        stock: '[]'
        ours : '[W]'          (before the repair)

A `takes_context=True` tag writing the context, in an empty loop's arm.  The
`{% include %}` spelling of the same thing does **not** diverge — `IncludeNode.
render` pushes a layer of its own — which is why the probe grew a bare-tag row
after the include rows passed, and why the row set is body *shapes* rather than
templates.  The positive control is `full_tag.html`: the same tag in a body that
iterates is discarded by both engines, so the divergence is localised to the empty
path and not to context writes or delegates in general.  The repair is the layer
in `run_for`'s `length < 1` arm, and it predates slice 2 in exactly the way repair
2 does not — it is *in* the shipped code, and this slice is where it became
visible enough to name.

**Mutant B reverts that repair and the probe reproduces the pre-repair column
byte for byte.**  Delete the push/`__exit__` pair, rebuild, run
`stated_empty_write.py`:

| mutant | reverts | probe | result |
|---|---|---|---|
| A | `write_frame`'s `length` write (family B) | `stated_delegate.py` (16) + `stated_refused.py` (4) | **0 differ on both** — see below |
| A′ | `write_frame`'s loop-variable write | `stated_refused.py` | **3 differ** (`refuse`, `loopvar`, `after`); `stated_delegate.py` 0 differ |
| A″ | `run_stated`'s `length` write (family A) | `stated_forloop_route.py` | **1 differ** — `plen`, stock `'333'` vs ours `''` |
| B | the layer around the `{% empty %}` arm | `stated_empty_write.py` | **1 differ** — `empty_tag.html`, stock `'[]'` vs ours `'[W]'` |

Mutant B's single row is the *same* row and the *same* two outputs as the
before-side capture — a revert that lands on the old artifact's failure exactly,
which is what makes the repair's scope (one row of ten, and the nine that do not
move) a measurement rather than a claim.

**Repair 2 — `length` was never written by `write_frame`, and which of its two
copies a probe can see is the whole question.  This is a correction to the
previous version of this file, twice over.**  An earlier version called the
`length` write "the bug fix" for a `KeyError` on `forloop.parentloop.length`; a
second version, after mutant A came back green, concluded the write was
**unobservable by this probe set**.  Both were wrong, in opposite directions, and
the search that settles it is the one that was written to be wrong.

  * `length` **is** written, in two places, because Django writes it **once,
    outside the loop** — `loop_dict = context["forloop"] = {"parentloop":
    parentloop, "length": len_values}`, `engine/defaulttags.py:214-217`.  This
    slice adds it to `write_frame` (family B) and writes it in the new
    `run_stated` (family A).
  * **Mutant A is green because family B cannot be on a path that reads it, and
    that is structural.**  A `forloop` read in a loop's *own body* stops the loop
    sealing at all — `stated_forloop_route.py` measures eight such rows, and every
    one is `NOT sealed`.  So `write_frame`, which runs only for a **refused** node
    inside a **pure-sealed** loop, never has a `forloop` read beside it: the dict
    it builds is written and never read.  Green here means *the probe cannot see
    this copy*, not *the copy does nothing*.
  * **The other copy is observable, and mutant A″ shows it.**  `plen` — `{% for r
    in rows %}{% for c in r.name %}{{ forloop.parentloop.length }}{% endfor
    %}{% endfor %}` — is family **A**: the outer loop seals with the inner
    `ForNode` as its delegate, and the inner loop renders in Python reading
    `forloop.parentloop.length` off the dict `run_stated` builds.  Drop that
    write and `plen` renders `''` where stock renders `'333'`.
  * So the honest status of the two `length` writes is the opposite of the earlier
    sentence: **`write_frame`'s copy unobservable by this probe set, `run_stated`'s
    observable and now observed.**  The earlier claim generalised from the family
    the mutant lived in to the write as a whole.

**The probe that found it had reported the read as absent.**  The first version of
`stated_forloop_route.py` printed `forloop read by: none in this row` for `plen`,
and the reason is a limitation of the instrument rather than a fact about the
template: the census counts nodes **the walk visits**, and `plen`'s reader is
inside the delegated inner `ForNode`'s own render, on the far side of the seam,
where the census has no visibility.  The column now says `census sees none — the
read is inside a delegated node's own render, or the body has no read`, which is
what it can actually support.  **A negative column is a claim too**, and this one
was false: the falsifier had to be the mutant, not the census.

The old claim is left here rather than deleted for the reason the census's
corrections are left in `.review/`: a reader who remembers reading it needs to
find it, with the measurement that overturns it beside it.

**What the walk still does not reproduce, stated as a bound rather than left
implied.**  `delegate_slot` builds a **fresh `forloop` dict per delegate call**,
where Django builds one per loop; the *stated* path builds one per loop, as
Django does.  A nested loop inside one delegate call therefore sees exactly
Django's dict, and only the *identity* of `forloop` across two delegate calls in
one sealed body differs — same keys, same numbers, different object.  Django's
own tags read `forloop` inside their own render, so nothing in the builtin
library can observe it; a `takes_context=True` tag that *kept* the dict and
compared it across iterations could.  It is a real residual, it is bounded, and
the reason it is not repaired is that whether a node refuses is a render-time fact
(a lookup that misses on iteration three), so writing the state for *possible*
refusals is writing it for every iteration of every sealed loop — which is what
sealing is for.

**No corpus row renders an empty loop.**  `{% empty %}` appears once in
`bench_corpus.py` (`:268`, `django-only/for-tags`) and that loop refuses to seal,
so the `{% empty %}` repair is on no bench row's path.  It is stated rather than
assumed: the grep is one line, and §3's rows are read with it in view.

## 3. Timing — the render/parse pair, and a compile side that is a control

`scripts/bench.py` over all three modes, `.venv/bin/python -I`, Django 6.0.9,
`--repeats 7 --target 0.05`, release build, two runs a side, **54 `(row, mode)`
cells**.  The payloads are committed beside this file as
`bench-slice2-{before,after}-{a,b}.json`, each recording its own artifact sha —
`before` on `6954e0c1e52f4859` (HEAD's `lib.rs`, 2703632 bytes), `after` on
`1ec2563e926cb649` (this slice, 2720496 bytes) — so every number below is a claim
about a named build rather than about "the tree".  The band is §0's: a same-build
pair of the *after* build as what `--compare` uses, and the **cross-build control
floor** as what actually bounds a two-build delta.

**The subject is one row, and the second row the census moved is a miss.**
`cross_build_floor.py` prints both sides' artifact shas, each side's same-build
scatter, and then the floor **before** the deltas it bounds — an order that is
the whole point of the instrument:

| cell | before | after | Δ | vs floor's max 2.42% |
|---|---|---|---|---|
| `django-only/realistic` render | 0.914 | **0.835** | **−8.49%** | MOVE — 3.5× the floor |
| `django-only/realistic` parse | 0.961 | **0.892** | **−7.20%** | MOVE — 3.0× the floor |
| `django-only/i18n` render | 0.571 | 0.571 | +0.66% | no move |
| `django-only/i18n` parse | 0.633 | 0.639 | −0.50% | no move |
| `real-world/complex` render | 0.626 | 0.613 | −1.43% | no move (refusal held) |
| `real-world/complex` parse | 0.700 | 0.694 | −0.62% | no move (refusal held) |
| `django-only/for-tags` render | 0.652 | 0.645 | −1.26% | no move (refusal held) |
| `django-only/for-tags` parse | 0.708 | 0.704 | −1.38% | no move (refusal held) |

Ratios are ours/stock, **lower is better**, 1.00 parity; every bench row reported
`===`.  `realistic` goes from 0.91× to **0.84×** of stock on render and 0.96× to
**0.89×** on parse.

**`i18n` is this slice's third miss, and the census predicted it as a subject.**
§1 records its `nodes` column going 3/6 → 104/156, its `ForNode` leaving
`delegated:` and fifty `BlockTranslateNode`s appearing there instead — structure
that moved, on a page whose render ratio is flat to **+0.66%**.  The mechanism is
in the same census and it is not subtle: `i18n`'s `body xings:` is **0 before and
0 after**, because its loop body is *one* `{% blocktranslate %}` per iteration and
nothing else.  Sealing a body that is entirely a delegate does not remove a
Python call — it changes which Python function drives it.  Before, `ForNode.render`
ran the loop and called the 50 delegate renders inside it; after, Rust runs the
loop and calls the same 50 delegates one at a time through `delegate_slot`.  What
the slice removes is the Python loop bookkeeping, and what it adds is our
per-delegate crossing, and on a body with no native nodes those are the same
size.  `realistic` is the shape that pays: its body also holds
`{{ r.x|floatformat:2 }}`, so sealing moves **100 `VariableNode`s, 206
`TextNode`s and 200 filter-body crossings** into Rust (`applied: floatformat=50`,
`body xings: 200`) and leaves the 50 delegates where they were.  **A census column
that moves is not a timing that moves**, and this pair of rows is the cleanest
instance of it in the project: same slice, same body shape by the one metric the
census prints first (`nodes`), opposite outcomes by the one that matters.

**The two refusals held, each inside the floor.**  `complex`'s outer body holds a
nested `{% for %}` and `for-tags`' body reads `forloop`; both are unchanged
mechanisms (§1) and both move by less than the control floor's max — −1.43% and
−1.26% on render, −0.62% and −1.38% on parse, with the same sign pattern as the
controls' own right tail.  A refusal that *happens* to hold is not the same
evidence as a refusal whose named mechanism is still in force, which is why §1
names the mechanism for each rather than reading these four rows as one control.

**The compile side is a control too, and `bench.py`'s compile mode can say so on
the two rows the slice changes.**  `django-only/realistic` compile goes
**1.434 → 1.420 (−0.98%)** and `django-only/i18n` **1.593 → 1.589 (−0.24%)** —
both inside the floor, on the two rows whose plans actually differ.  That is the
prediction the mechanism makes: slice 1 added a **descent pass** over the whole
tree and paid **+11.7%** compile on `real-world/normal`; slice 2 adds **no pass**
— it flips a boolean on the seal decision the walk was already making, and the
body scan that computes it runs only for a loop that has already sealed.  A
`parse` row could not have separated those two costs from their own render
savings (the render/parse pair's whole point), which is why the third mode
exists and why it is the mode this claim is made in.

**The compile side is a control, and that is a fact about the code rather than
about the numbers.**  `compile_only.py` measures `from_string` alone on nine
rows, and this slice's only plan-time addition is `holds_delegate`: one recursive
scan of an already-built body, asked inside `plan_for` **after** `seal` has
accepted the body (`lib.rs:3730`, "Asked of the **sealed** body"), so a loop that
did not seal returns before it and a row with no planned loop never reaches it.
The census says which rows those are, per row rather than in aggregate:

| compile-only row | `ForNode` in `handled:`? | reaches `holds_delegate`? |
|---|---|---|
| `micro/text`, `micro/variable`, `micro/if`, `micro/filter`, `micro/extends` | no | no — no loop is planned |
| `real-world/complex`, `django-only/for-tags` | no (`delegated: ForNode=1`) | no — the body never sealed |
| `micro/loop-text`, `real-world/normal` | yes | yes, and it answers **`false`** |

**The two rows this slice changes are `django-only/realistic` and
`django-only/i18n`, and `compile_only.py` measures neither** — both print
`NOT MEASURED … 'i18n' is not a registered tag library`.  So all nine rows here
are drawn from the population the change cannot act on, which is what makes the
table a floor rather than an effect.  Read with `compile_only_delta.py`, **three
runs a side**, each row against **its own** scatter rather than the page's largest
(the rows do not answer alike, so a pooled band would be another row's noise
wearing this one's name):

| row | before | after | delta | own band | |
|---|---|---|---|---|---|
| `micro/loop-text` | 1.536 | 1.572 | +2.34% | 3.14% | inside |
| `micro/variable` | 2.137 | 2.106 | −1.45% | 2.58% | inside |
| `micro/filter` | 1.626 | 1.649 | +1.41% | 2.67% | inside |
| `django-only/for-tags` | 1.258 | 1.273 | +1.19% | 1.61% | inside |
| `micro/extends` | 1.460 | 1.443 | −1.16% | 4.03% | inside |
| `real-world/complex` | 1.246 | 1.254 | +0.64% | 1.12% | inside |
| `micro/if` | 2.078 | 2.086 | +0.38% | 3.26% | inside |
| `real-world/normal` | 1.351 | 1.346 | −0.37% | 1.73% | inside |
| `micro/text` | 3.580 | 3.578 | −0.06% | 2.80% | inside |

**The two-run version of this table had one row outside its own band, and the
three-run version has none — which is the correction, not the finding.**  At two
runs a side `micro/if` read −1.03% against a 0.96% band: 0.07 points outside, on
`{% if flag %}<b>on</b>{% else %}<i>off</i>{% endif %}` — no `{% for %}`, so no
`ForPlan`, so `holds_delegate` is not compiled into reach, let alone called, and
the delta was a *fall* on a row whose plan is the same bytes.  The sentence
written beside it said a two-run band is itself an estimate and that a third run
per side would replace the reading rather than be argued alongside it; that is
what happened, and the honest form of the claim is now **the table's largest |Δ|
is 2.34%, all nine rows are inside their own bands, and every row is a control**.

**The third run had to be re-taken, and the reason is worth keeping.**  The first
attempt invoked `compile_only.py` with **no `--corpus`**, so it covered the five
default micro rows while the two existing tables cover eleven — six rows of one
instrument that are not comparable, in a file that said nothing about which
population it held.  `compile_only.py` now prints a `population:` line naming the
default rows, the corpus rows and the total, so a table cannot be read without it;
all six tables here were re-taken on one population in one sitting.  A metric is
only a number once its population is named, and this is the same lesson the
project's census corrections are made of.

**And the compile cost this slice *could* have paid is not the one `parse` mode
would show.**  Slice 1 added a descent pass over the whole tree, which is why its
compile cost was +11.7% on `real-world/normal`; slice 2 adds no pass — it flips a
boolean on the seal decision the walk was already making, and the body scan that
computes it is on the path of a loop that has already been planned.  A `parse`
row could not have shown the difference either way, because it folds compile into
its own render; the mode that separates them is the one the last slice added, and
on the rows it can measure it reads a floor — including on the two rows this
slice changes, where `bench.py`'s compile mode does measure them (§3 above:
`realistic` −0.98%, `i18n` −0.24%, both inside the floor).

## 4. What did not move, and must not

* **The acceptance suite: `Ran 1547 tests … OK (skipped=6)`**, plus the unit
  suite, both from `scripts/check.sh` on this artifact, `EXIT=0`, with every other
  gate step green (§ the gate log).  Block and loop semantics are the most heavily
  tested thing in the suite (`test_extends.py`, `test_partials.py`,
  `test_for.py`, `recursive_templates/`).
* **Every bench row's `out` column: `===`,** three engines byte-identical.
* **`real-world/large`'s census, every column** — no inheritance and no delegate
  in any loop body, so any move there would be a bug.
* **`django-only/for-tags` and `real-world/complex`**, the two refusal controls §1
  names, each with its mechanism.
* **The control floor's right tail**, named in §3 rather than smoothed:
  `micro/escape render +2.42%`, then `micro/text compile −2.40%`, `micro/escape
  compile +2.33%`, `micro/loop-var compile −2.17%`, `micro/filter compile
  +2.17%`, `django-only/filter-arg compile +2.07%`.  Six cells, five of them
  `compile` ratios and one a `render` on a row with no `{% for %}` at all — so the
  floor's tail is mostly the *compile* mode's own build-to-build spread, which is
  worth stating because §3 makes a compile claim and this is the width it is made
  against.

## 5. What this leaves

Slice 2 as step 5 §3 scoped it had three bullets.  One shipped; two are owed, and
naming them here is what keeps `micro/extends`'s miss from reading as an effect:

1. **`ExtendsNode.render` is still a delegate.**  `out deleg` on `normal` can
   only approach zero once the frame that returns the whole page is Rust's, and
   `micro/extends`'s render is two Python frames plus the parent walk, which a
   body plan does not touch (step 5 §2 says this at its own size).
2. **The per-render `get_nodes_by_type(BlockNode)` walk over the whole parent**
   is paid on every render of an inherited page and is invisible in every column
   of this record.  It is a compile-time cache candidate, and slice 1 already has
   the place for one.
3. **`plan_block_bodies` should report its own coverage** — bodies visited,
   planned, declined — so a missing attach is a number rather than a gap.  Today
   `NodeList._native_plan = None` is a class default and the failure it hides is
   a silently unplanned body that renders correctly and slowly.  It changes the
   census's printed shape, which every M19 record cites, so it wants its own slice
   rather than a line in this one.  Step 5 §3 already said so; it is repeated here
   because neither this slice nor the next one may quietly absorb it.

## 6. The instruments

| file | what it settles | new here? |
|---|---|---|
| `node_census.py` | the structure behind the timings; its committed `.txt` is step 5 §1's `before` column, and `node_census.step5-slice2.txt` is this one's | |
| `bench_scatter.py` | the same-tree scatter band, with its `sha256:` guard against comparing two builds | |
| `compile_only.py` | what a `parse` row cannot separate and a `render` row cannot see | |
| `compile_only_delta.py` | N runs a side of a compile-only table, each row against **its own** scatter — the per-row test, not a pooled band | **yes** |
| `stated_delegate.py` | byte-identity on 16 loop-body-holds-a-delegate templates (family A) | |
| `stated_refused.py` | byte-identity on 4 templates that reach `write_frame` (family B), with the census showing the route | **yes** |
| `stated_forloop_route.py` | the search for a template whose `forloop` read reaches a state dict the walk wrote — the falsifier for "the `length` write is unobservable".  It lands on `plen`, family A | **yes** |
| `stated_forloop_route.mutant-stated-length.txt` | the output of that search under mutant A″ (`run_stated`'s `length` write dropped): `plen` renders `''`, 1 differ | **yes** |
| `stated_reach.py` | that the seal is reached, by node name, in one loop | |
| `stated_balance.py` | context-layer balance across a stated loop, both paths | |
| `stated_empty_write.py` | whether a loop body's context write outlives the loop | |
| `mylib.py` | the tag library the probes load (`noop`, `shout`, `writes`, `mark`, `boom`, and `surro` as a documented dead end) | |

`stated_delegate.py`, `stated_refused.py`, `stated_forloop_route.py`,
`stated_forloop_route.mutant-stated-length.txt`, `stated_reach.py`,
`stated_balance.py`, `stated_empty_write.py`, `mylib.py` and
`compile_only_delta.py` are all new with this slice — `git ls-files
.review/m19-measurements/` names none of them.  The census, scatter and
compile-only instruments predate it.

The bench payloads and the compile-only runs are committed beside this file: a
band quoted without its input is a number a reader cannot check.  All `.review/`
records rather than shipped instruments, and none on the gate's path.

## 7. The gate, on this artifact

`scripts/check.sh`, one run, on the tree as committed — `GATE_EXIT=0`, the whole
chain green.  The numbers this record's correctness claims rest on:

    the extension under test is the one just built: _native…so
      sha256:1ec2563e926cb649  ==  target/release/lib_native.dylib
    Ran 1547 tests in 3.788s
    OK (skipped=6)
    OK (skipped=6) after 1547 tests, and the six declared skips by identity
    differential: 48 cases, identical
    Ran 509 tests          (the port's own python unit tests)
    787 passed             (cargo test)
    all checks passed

The gate is the one that asserts its own subject: it rebuilds the extension and
compares the installed `.so`'s sha against the freshly built dylib *before* the
suites run, so a green here is a green on this build and not on whatever was
installed last — the failure mode `a-gate-must-build-its-subject` records.  The
six skips are checked by identity rather than counted, so a swap that keeps the
total at six cannot pass.  And the anchor manifest is checked `--frozen` after
this slice's `lib.rs` line moves were written into `scripts/anchors.txt`, which
is the step an earlier, aborted run of this gate failed on.
