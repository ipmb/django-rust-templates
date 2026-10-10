# Step 5, slice 3 — a nested `{% for %}` seals: outcome

**What shipped, in full.**  Two edits and nothing else.

1. `sealable`'s `Act::For(_)` arm goes `false` → `true`: a loop body holding a
   nested `{% for %}` no longer refuses to seal.
2. `holds_delegate` becomes `needs_context_state`, and its arms go
   `Act::Delegate => true` → `Act::Delegate | Act::For(_) => true`, descending
   into `Act::If` branches as before.

The second is not decoration and §2 is the mutant that proves it.  A nested
loop needs the *context state* for two reasons that have nothing to do with a
delegate: `run_for` resolves its sequence through `sequence.resolve(&self.context, true)`,
so `{% for line in s.lines %}` needs `s` reachable from the context and not only
from the frame; and a nested loop that reads `forloop` wants `parentloop`.  So
the predicate is renamed to what it actually answers — *does rendering this body
need Django's per-iteration context state?* — and the answer stopped being
"only if a delegate is in there".

**What did not ship, said before any number below.**  The per-render
`get_nodes_by_type(BlockNode)` walk over an inherited page's parent is still paid
on every render, and `ExtendsNode.render` is still a delegate.  §5 prices the
walk's deferral and names its shape.  **`micro/extends` was predicted not to move
and does not** — it is the control that says the walk is still there.

**The headline is a miss, and it is the miss the slice was built on.**  The
prediction (`step5-slice3-prediction.md`, written and committed before the code)
put `real-world/complex` render at **1.53× → 0.55×–0.65×**.  Measured on this
build: **1.49× → 1.49×**, i.e. `ours/stock` 0.613 → 0.599, **−3.38%**, against a
cross-build control floor whose max is 2.51% (§3).  The mechanism fired exactly
as designed and the census proves it landed (§1).  The **price** the prediction
attached to it did not survive, and §3 is that measurement: the loop this slice
un-delegated is a loop whose body is **72 `{% include %}` tags**, and the
includes cost about what the Python loop they replaced cost.  The prediction
priced them with `include_swap.py`'s slice-2 reading, which said the include path
was *our* advantage (+77.2 µs).  On this build the same probe reads **−181.2**
(ours pays ~181 µs *more* than Jinja2).  The +77.2 was a property of the old
route — includes driven from inside a delegated Python `ForNode` — not of the
include.  That is the project's own `a-measurement-names-a-code-path` lesson, and
the two probes' sign word was also inverted by the same confusion (§3.3).

## 0. Instruments, artifacts, and the band

| label | artifact | what it is |
|---|---|---|
| `before` | `sha256:1ec2563e926cb649` | `bench-slice2-after-{a,b}.json`, the committed slice-2 build, 2720496 bytes |
| `after` | `sha256:e6a72702ba5877cb` | `bench-slice3-after-{a,b}.json`, two runs of this build, 2720544 bytes |

Both shas are the first 16 hex of `python/django_rust_templates/_native.cpython-314-darwin.so`,
which is the artifact the project's own route loads (`django_rust_templates._native`)
and the artifact `scripts/bench.py` records in its payload as
`sha256:e6a72702ba5877cb -> release (target/release/lib_native.dylib)`.

**Which artifact each instrument reads, checked rather than assumed.**  `import _native`
by its bare name resolves to `.venv/…/site-packages/_native/_native…so`, which is a
**stale orphan** — 2720496 bytes, the *slice-2* size, imported by nothing: the live
install is a `.pth` editable pointing at `python/`, and no file in the repo imports
the bare name (the one hit, `tests/test_interposition.py:364`, is a comment).  So
every consumer here — the backend, `host.py`, `engine/parser.py`, `engine/base.py`,
`scripts/bench.py`, `scripts/filter_probe.py`, and the probes in this directory —
reads `django_rust_templates._native` and gets the slice-3 build.  The orphan is
recorded in §5 as a trap for the next ad-hoc probe, not a defect in these numbers.

**The band.**  `scripts/bench.py --compare` bounds each delta by the scatter of
*its own row* across two runs of **one** build.  On this pair: 54 shared rows,
median |rel| **0.66%**, max **2.64%**.  A row marked `inside` is *not resolvable
to better than the band* — the differ says so at the point it prints, and that is
not the same finding as `did not move`.

**The cross-build floor is a different instrument and is printed first.**  A
same-build pair cannot see build-to-build layout, and this comparison's two sides
*are* two builds — so `cross_build_floor.py` measures the noise on the cells the
change **cannot** reach (50 of 54) and gives every named delta the max it must
clear: **median 0.84%, p90 1.61%, max 2.51%** (`micro/escape compile`).

## 1. Structure — the census, before → after, and exactly who moved

`node_census.py` walks the *walk's own* visit set, so it reports structure, not
time.  Over all 18 workloads × the derived rows, **exactly two rows changed, and
they are the two the slice is about**:

| row | `nodes H/N` (deleg share) | `handled:` gained | `delegated:` lost | `applied:` gained | `body xings:` |
|---|---|---|---|---|---|
| `real-world/complex` | 516/524 (1.5%) → **625/704 (11.2%)** | `ForNode=13 IfNode=12` | `ForNode=1` | `title=12` | **0 → 12** |
| `nested for (2 deep)` | 0/1 (100%) → **15/15 (0%)** | `ForNode=3` | `ForNode=1` | — | 0 → 0 |

Every other row is identical in every column, including `django-only/for-tags` —
the refusal that must stay, and does: `delegated: ForNode=1` unchanged, because
its body reads `forloop.counter` and that is the *other* reason a body refuses.

**Reading the `complex` row.**  `nodes` rises 516 → 625 because the walk now
*visits* the inner loop's body instead of handing the whole outer loop back —
704 is the full tree of the inlined complex page.  `ForNode` leaves `delegated:`
(the outer loop is no longer a delegate) and thirteen `ForNode`s join `handled:`
with twelve `IfNode`s that were previously inside the un-visited body.  `applied:
title=12` is the outer head `{{ s.title|title }}` — previously applied by the
delegate, now applied by the walk.  And **`body xings: 0 → 12` is the meter that
matters**: `applied:` says the walk *routed* a filter, not that Rust *computed*
it; the third meter counts Python work inside a filter body, and 12 of them
appeared where there were none, i.e. Rust is now doing per-section work Python
used to do.  The inner body is still 73 `IncludeNode`s (`delegated: IncludeNode=73`),
and §3 is what that costs.

## 2. Correctness — the parity probe, and the mutant that proves edit 2

**The differential probe.**  `nested_for_parity.py` renders 17 nested-loop
templates — direct nesting, `parentloop`, `parentloop` two deep, an inner loop
inside an `{% if %}`, an outer var read in the inner body, an empty inner, a
delegate inside the inner, `reversed`, sibling loops, an inner-over-outer-var, a
refused filter in the inner, a pure-outer/stated-inner split, an inner that is
only an include, `cycle` in the inner, an empty outer branch, three-deep nesting
— under stock Django and this build, and asserts **byte-identical output**.  On
the shipped build: **17/17 ok**, every template identical.

**The mutant for edit 2.**  Edit 1 alone cannot be mutated usefully — reverting
`sealable(Act::For)` reproduces the *before* artifact exactly, so the whole
`before` column is already that control at full scale.  Edit 2 is the one that
needs its own: `needs_context_state` with `Act::For(_)` **removed** (keeping
sealability intact) is a build in which the outer loop seals but does not write
`context[loopvar]`, so the nested loop's sequence resolves against a context that
never got `s`.

The mutant was built by copy-and-`cp`-back (`fanout-agents-share-one-tree`:
never `git checkout` to restore after a mutation), and its result is:

- **`nested_for_parity.py`: 13 of 17 differ**, and the failure is *silent* —
  the inner loop renders **nothing** (`ours: ''` against `stock: 'a1a2a3b1b2'`),
  not an exception.  `loopvar_in_inner_seq`, `delegate_in_inner`,
  `inner_include_only`, `cycle_in_inner`, `nested_three_deep` and eight others go
  empty.  The four that stay green are the ones whose inner sequence does not
  come from the outer loopvar.
- **The vendored 1547 suite also catches it: 3 failures**, all nested-`for`
  `regroup` sites (`test_regroup02/03/04`), e.g.
  `'02:,07:,' != '02:1428,07:04,'`.

So two independent instruments catch this mutant, and neither is redundant: the
suite reaches **3** nested-`for` shapes, the parity probe reaches **17**, and the
probe's silent-truncation signature is the one worth having in the record — a
regression here produces empty output, not an error, which is exactly the kind of
failure an exception-based gate misses.

The restore was verified end-to-end, not assumed: after `cp`-back and rebuild the
live artifact is again `sha256:e6a72702ba5877cb` / 2720544 bytes and the parity
probe is 17/17 ok.

## 3. Timing — the differ, the floor, and why the win is small

### 3.1 The differ, and the floor it answers to

`scripts/bench.py --compare bench-slice2-after-a.json bench-slice3-after-a.json`,
banded by the slice-3 pair: **33 rows outside their own band, 21 inside.**  The
project's change is inside that population and reads:

| row | before | after | rel | own band | verdict |
|---|---|---|---|---|---|
| `real-world/complex` parse | 0.694 | 0.673 | **−3.06%** | 0.23% | outside |
| `real-world/complex` render | 0.613 | 0.599 | **−2.29%** | 0.69% | outside |
| `django-only/for-tags` render | 0.645 | 0.649 | +0.63% | 1.44% | inside |
| `django-only/for-tags` parse | 0.704 | 0.702 | −0.20% | 0.14% | outside |

The `inside` verdicts are not evidence the refusal held — the own-band test is
too tight to resolve them.  The instrument that *can* answer it is the
cross-build floor, whose population is the 50 cells the change cannot reach:

- **SUBJECT** — `real-world/complex` render **−3.38%**, parse **−3.98%**, both
  `MOVE`, both clearing the floor's max of 2.51%.
- **REFUSAL** — `django-only/for-tags` render **+0.14%**, parse **+0.27%**, both
  well inside the floor, i.e. the refusal that stays is measurably still firing.

### 3.2 Why −3.38% and not −45%: the construct ladder

`complex_ladder.py` removes one construct at a time from the complex page and
renders every rung under both engines.  On this build:

| rung | ours µs | jinja2 µs | ours/them | Δ ours−them |
|---|---|---|---|---|
| `full` | 509.3 | 341.6 | **1.49** | — |
| `no-include` (72 inner includes inlined) | 181.7 | 185.3 | **0.98** | **−171.2** |
| `no-inner` | 61.4 | 44.1 | 1.39 | +20.8 |
| `no-outer-head` | 50.4 | 28.7 | 1.76 | +4.5 |
| `bare-section` | 30.2 | 19.0 | 1.59 | −10.6 |

(Rust and Jinja2 rendered the same bytes on every rung — the probe hashes each
and exits non-zero on a mismatch; it did not.)

This is the whole finding.  **Removing the 72 inner includes moves ours from 1.49×
to 0.98× Jinja2** — inlining them is worth −327.6 µs to us and −156.3 µs to
Jinja2, so the include path costs us **171.2 µs more than Jinja2**.  Slice 3
un-delegated the outer loop and thereby turned 72 Python `IncludeNode.render`
calls *inside a Python loop* into 72 Rust-driven delegate crossings; §3.3 shows
the second is not cheaper than the first.  The remaining rungs (head, section)
are the page's floor, and the page's floor is what slice 3 was always going to be
limited to.

### 3.3 The prediction's price was read off the wrong route

`include_swap.py` on **this** build: `rust include 528.8 µs → inlined 189.5 µs`
(−339.4), `jinja2 include 344.5 → 186.4` (−158.1), so **ours pays +181.2 µs more
than Jinja2 for the include path**, and the ratio goes 1.54 → 1.02.  The
prediction justified its target with the *slice-2* reading of this same probe,
**+77.2** — the opposite sign.  Both numbers are right; they measure two
different routes.  On slice 2 the includes were called from inside a delegated
Python `ForNode`, and the include path was where we lost least; once the loop
moved to Rust, the same 72 calls are the page's dominant cost.

**The sign word was wrong in both probes and is now corrected.**  `d ours-them`
is `d_rust − d_jinja = (ours_inlined − ours_full) − (jinja2_inlined −
jinja2_full)` = `C_jinja2 − C_ours`, so a **negative** number is a construct we
pay more for.  Both `complex_ladder.py`'s caption and `include_swap.py`'s print
said the reverse until this slice.  The slice-2 *record* read its value (+77.4)
correctly as "Jinja2's disadvantage" while the caption under the table said the
opposite — the number was never wrong, its label was.  Both files now carry the
correction, and `complex_ladder.py` documents it inline.

### 3.4 Compile is a control, as predicted

`compile_only_delta.py`, three runs a side, each row judged against **its own**
scatter:

- `real-world/complex` compile **+0.00%** (own band 1.93%), `real-world/normal`
  +0.15% (1.51%), `micro/extends` +1.87% (2.44%) — the largest row.
- **0 rows outside their own band.**
- Same-build scatter: before median 1.95% / max 3.14%, after median 1.82% / max 3.03%.

The slice touches only the render walk, so compile must not move, and it does not.

## 4. What did not move, and must not

- **`django-only/for-tags`** — the refusal `sealable` keeps (a body reading
  `forloop`) is still the one that fires; census identical, deltas +0.14%/+0.27%
  inside the floor.
- **Every other corpus row** — the census reports 16 further rows identical in
  every column.  This is expected and was checked before the fact: over all 18
  workloads the maximum `{% for %}` nesting depth is 2, and **`real-world/complex`
  is the only row at depth 2**, so nothing else can act.
- **`micro/extends`** — the block walk is still per-render and `ExtendsNode.render`
  is still a delegate; the row is the control for that (§5).
- **The corpus aggregate barely moves**, which is the honest headline.  Report
  tiles on this build (stamp `2026-10-10 11:02 UTC`): render **0.39×**, parse
  **0.75×**, compile **1.36×** ours-vs-stock real-world; 39/39 three-engine rows
  byte-agree.  Geometric means: real-world render ours/stock 0.387, ours/jinja2
  **1.043**; parse ours/stock 0.747, ours/jinja2 0.254.  The six real-world
  render+parse rows run **0.30×–1.11×** stock.

## 5. What this leaves

**The block walk is deferred, with its shape.**  `get_nodes_by_type(BlockNode)`
runs per render on an inherited page.  It is priced at 2.18% of `normal`, 0.60%
of `complex`, 0.56% of `realistic`, and 23.19% of the 11.6 µs `micro/extends`
row — so it is a real cost only on the row that is almost nothing but the call.
It is not in this slice because it carries its own correctness argument and its
natural site, `NodeList.get_nodes_by_type`, is on `COMPARED_PATH`: a new NodeList
method plus a `TRANSFORMS` line for the generated `loader_tags.py`, and the
`a-derived-trees-root-is-nobodys-file` check that the generated tree actually
picks the new line up.  Task #29 tracks the coverage reporting (bodies visited /
planned / declined) that should land with it.

**A stale orphan artifact to remove.**  `.venv/…/site-packages/_native/` holds a
2720496-byte copy of the **slice-2** extension that nothing imports and that a
future bare `import _native` — in an ad-hoc probe, or a reader trusting the
package name — would silently load.  The live route is `django_rust_templates._native`
via the `.pth` editable.  It is not a defect in these measurements (§0 shows
every consumer reads the live artifact) but it is a trap; it belongs with #6
(cleanup).

**Still ahead in step 5:** the demoted `ExtendsNode.render`-off-the-delegate
bullet (41.82% of `micro/extends`), and then the wider list — #5 lazy
materialisation, #6 cleanup, #20 the filter seam's caller enum, and the
`django_own_tags()` compile tax (1.13 µs of `ShellHost`'s 1.23 µs floor).

## 6. The instruments

| instrument | what it answers | where it lives |
|---|---|---|
| `node_census.py` | which nodes the walk visits, handles, delegates, applies, and how much Python work runs inside a filter body | §1 |
| `nested_for_parity.py` | 17 nested-loop shapes, byte-identical under stock and this build | §2 |
| `scripts/bench.py --compare` | each row's delta against **its own** same-build scatter | §3.1 |
| `cross_build_floor.py` | the floor a **cross-build** delta owes, measured on the cells the change cannot reach | §3.1 |
| `complex_ladder.py` | which construct each µs of the gap lives in | §3.2 |
| `include_swap.py` | the include path's cost, as a delta of deltas | §3.3 |
| `compile_only_delta.py` | compile as a control, per row against its own band | §3.4 |

## 7. The gate, on this artifact

`scripts/check.sh` on `sha256:e6a72702ba5877cb`: **`CHECK EXIT: 0`**,
`Ran 1547 tests in 3.790s`, **`OK (skipped=6)`**, six skips by identity,
differential harness 48 cases identical, `all checks passed`.  The gate rebuilds
and sha-checks the extension before the suites (`a-gate-must-build-its-subject`),
so the suites read this artifact and not whatever was installed last.
`scripts/check_python_anchors.py --check --frozen`: exit 0, after re-keying
`scripts/anchors.txt:56` and the spec citation at
`2026-10-09-clean-and-fast-design.md:221` to `crates/template-py/src/lib.rs:4917`.
