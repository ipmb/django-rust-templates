# M16 — the first performance milestone, and what it measured

**Milestone row:** none. The spec's milestone table (§10.3) ends at M15
(`docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md:834`);
M16 is not in it. Its authority is §3:70 —

> Optimising for speed. This is a **compatibility-first** build; performance is a
> possible later phase, not an acceptance criterion.

and §5.2:499, which names the cost the phase inherits — "`{{ x|date:"Y" }}` crosses
the FFI on every render, permanently. §3 makes performance a non-criterion, so that
is a choice rather than a cost." M16 is that later phase arriving, so this record is
the *first* measurement of the choice §3 deferred rather than a milestone closing a
row the spec wrote.

**Question:** is the Rust-backed engine faster than stock Django 6.0.9?
**Answer, measured:** **no.** It is **1.35× to 1.85× slower** across the four
workloads, and this record names where every microsecond of that is.

**Instruments** (all committed, all re-runnable):
`scripts/bench.py` (the four workloads), `.review/m16-measurements/m16_bench_decompose.py`
(one workload per node/filter/tag unit), `.review/m16-measurements/m16_measure_library_tables.py`
(the routing arm, in pieces). Transcripts: `.review/m16-measurements/transcript-*.txt`.

**Production change:** one, in `python/django_rust_templates/_impl/base.py`, worth
**3.5%** (§5). The verdict was already fixed before it landed.

---

## 1. How to re-run it

```bash
source scripts/env.sh
# our engine (shim installed)
DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \
    .venv/bin/python scripts/bench.py
# stock Django (control)
DJANGO_RUST_TEMPLATES=0 PYTHONPATH="$PWD/.django" \
    .venv/bin/python -I scripts/bench.py
```

Two processes, so the comparison is a `diff` of the two transcripts, not a flag: each
side prints which `base.py` it loaded (`tree:`) and a sha256 per workload. **All four
sha256 agree in every run in this record** — `72a0693dbb80e085`, `72a0693dbb80e085`,
`7c4227d7dd292ee8`, `787ec76dcafd20c1` — so the work is byte-identical and the ratio
below is a like-for-like comparison, not two different templates.

The estimator is **best-of-7 per-call microseconds** around a tight inner loop
(`perf_counter`), with the worst sample printed beside every figure so a noisy run is
visible rather than hidden. It is the same estimator in all three columns below, which
is what makes the ratios comparable; it is not an unbiased estimator of the mean, and
the spread across runs is ~1.5% (§4).

**Instrument defect found and fixed while writing this**: `scripts/bench.py` originally
called `settings.configure(` itself, which `tests/test_settings_configuration.py`
refuses — exactly one file in `scripts/` may configure settings (`scripts/filter_probe.py`),
because `settings.configure` is a no-op once settings exist and *which module runs first*
would then decide the values. The harness now reuses `filter_probe.ensure_settings()`,
which also supplies the `.django` `sys.path` insert the stock side needs under `-I`
(`-I` implies `-E`, so `PYTHONPATH` is ignored and `import django` dies without it).

---

## 2. The brief's before-table, and the one I could reproduce

The brief handed down this before-table: **1.82 / 1.75 / 1.38 / 2.05**
(render / parse / large / tiny).

**Three of its four rows did not reproduce.** The table this record stands on, measured
on this machine in this session on the *unoptimised* tree (§5's change backed out, so
this is genuinely "before"):

| workload | shim µs | stock µs | ratio |
|---|---|---|---|
| realistic/render | 1694.1 | 1211.5 | **1.398** |
| realistic/parse | 1839.6 | 1306.3 | **1.408** |
| large/parse | 3255.1 | 2234.0 | **1.457** |
| tiny/parse | 30.1 | 15.7 | **1.917** |

`large/parse` agrees with the brief to 6% (1.457 vs 1.38). The other three differ by
6–23%, and in the *opposite* direction from a machine-speed difference would produce —
the brief's `realistic/render` is 23% worse than mine while its `large/parse` is 6%
better. A faster or slower machine moves a ratio not at all, so this is not a clock
difference; the likeliest cause is a different page composition in whatever probe
produced the brief's table, which I cannot see and did not try to guess at. **The
discrepancy is recorded rather than reconciled**: what the brief's numbers and mine
agree on is the *sign* and the *ordering* (tiny ≫ large > render ≈ parse), and that is
the part the milestone's verdict turns on.

Both numbers below are therefore from *my* harness, on *my* machine, both sides in the
same session, with the same estimator — which is the only comparison this record makes
a claim about.

---

## 3. What was tried, and what the number said

The mandate was measure → change one thing → re-measure with the same harness → keep
only what moves the number. Two candidate changes were measured to the end; one landed.

### 3.1 `Variable.__init__` / parse-side caching — not attempted, on the decomposition's evidence

Skipped because §6's decomposition attributes ~96% of the render gap to filter
application and variable resolution, not parsing. Recorded so it is not re-derived.

### 3.2 Caching the routing decision — measured, **not kept**

`FilterExpression.resolve` decides "is this a built-in Rust ports?" by calling
`django_own_filters()` on every filter application. It costs **1.061 µs**, the largest
single term in the arm (§6.2). The obvious fix — memoise on the function handle — is
**unsound**, and `django_own_filters`' own docstring already measures why: the cache is
keyed on the identity of all six registry *objects* because a re-executed
`defaultfilters` replaces `register.filters` with fresh handles, and a stale map answers
"not ours" for *every* name, silently, with every test green. A per-handle memo inherits
the same hazard one level down with a smaller blast radius, and the fast path measured
below is worth less than the invariant it would weaken.

The fast path that *is* sound — skip the merge machinery and probe the six registries in
place — has its **components** measured (§6.2: the arm is 1.404 µs, of which the six
inlined reads are 0.104) but was **not built**, so the ~0.96 µs/filter, ~96 µs,
**~5.8%** figures in §6.3 are a subtraction on measured terms rather than a measurement
of the change. Not kept: see §6.3 for why the number is real but the verdict does not
move.

### 3.3 Hoisting the two in-function `_native` imports — **kept**, 3.5%

See §5. This is the only production change in the milestone.

### 3.4 Hoisting the in-loop `from django_rust_templates.host import (…)` — **measured, not kept**

`FilterExpression.resolve` re-imports three names from `django_rust_templates.host`
inside the per-filter loop (`_impl/base.py:823-827`). The microbench says the statement costs
**0.140 µs**; ~100 filter applications per `realistic/render` is therefore ~14 µs, 0.8%.

**That microbench under-measures by 2.4×, and §5 is the proof.** The same technique gave
0.207 µs for the in-function `_native` import while the in-situ A/B measured ~0.5 µs per
execution. Corrected, this change is worth ~34 µs (**2.1%**). Not kept, on cost rather
than on size: the import sits inside a declared transcription region, so moving it
re-shapes `scripts/check_transcriptions.py`'s `DECLARED` spans, every one of
`tests/test_compared_path_instrument.py`'s 30 `COMPARED_PATH` entries and
`scripts/anchors.txt` — the cascade §5 already paid once — and 2.1% does not move the
verdict by a decimal place. **Named as the next lever, with its number, rather than
rejected as worthless.**

### 3.5 The brief's ~20 µs FFI-overhead hypothesis — **refuted**

The brief suggested the FFI crossing itself was ~20 µs of the gap. Measured:
`_native.filter("upper", "x", (), True, False)` — a complete crossing, argument
classification, body, and return — is **0.216 µs**. The FFI is not the cost. What the
crossing *implies* is: every filter application re-runs Python-level routing work that
stock Django skips because its `FilterExpression.filters` tuple already holds the
callable. That is §6.2's 1.404 µs, and it is 6.7× the crossing it is deciding.

---

## 4. The after-table

Best-of-three runs per side, same estimator, same session. (Best-of-N applied
uniformly; the three runs' spread is 1.5% on the shim side and 1.5% on stock.)

| workload | shim µs | stock µs | ratio | before | Δ |
|---|---|---|---|---|---|
| realistic/render | 1637.2 | 1211.5 | **1.351** | 1.398 | −3.4% |
| realistic/parse | 1779.3 | 1306.3 | **1.362** | 1.408 | −3.3% |
| large/parse | 3131.2 | 2234.0 | **1.402** | 1.457 | −3.8% |
| tiny/parse | 29.1 | 15.7 | **1.854** | 1.917 | −3.3% |

**Every ratio is above 1. The engine is slower than stock Django on all four
workloads.** `tiny/parse` is the worst and is the honest signal: a one-variable
template is *the* fixed-overhead workload, and 29.1 µs against 15.7 µs says our
per-parse and per-render fixed costs are roughly double stock's.

Output bytes are identical in every run (the four sha256 above), so nothing here is
bought with a behavioural change.

---

## 5. The one landed change, its mechanism, and the A/B that proves it

**Change.** `_impl/base.py`: one module-level `from django_rust_templates import _native`
(line 73, after `from .exceptions import TemplateSyntaxError`), and the deletion of the
*two* in-function `from django_rust_templates import _native` statements — one in
`FilterExpression.resolve` (per filter application), one in `Variable._resolve_lookup`
(per variable resolution). Net −2 lines, 1214 → 1212.

**Mechanism, not just the fact.** A `from X import Y` executed inside a function body is
not free and is not a compile-time constant: CPython emits `IMPORT_NAME` +
`IMPORT_FROM` at the statement, so *every execution of the statement* runs the import
machinery — a `sys.modules` probe keyed on the module's fully-qualified name, then an
attribute fetch. The render path executes one per variable resolution and one per
filter application, so the cost is paid once per **node**, not once per render. Moving
the statement to module scope makes it execute once per *process* instead. There is no
caching to add and no semantics to preserve: the name is bound to the same object either
way, and the module cannot be swapped mid-render because the engine cannot be swapped
mid-render (that is what `scripts/assert_engine_installed.py` establishes before the
suite runs).

**The A/B, measured in situ, because the microbench for this exact statement
disagrees with the real cost.** Two runs with the imports back in the function bodies,
three runs with them hoisted, all six on this machine in this session:

| workload | no-hoist, best of 2 | hoisted, best of 3 | Δ µs | Δ % |
|---|---|---|---|---|
| realistic/render | 1694.1 | 1637.2 | −56.9 | −3.4% |
| realistic/parse | 1839.6 | 1779.3 | −60.3 | −3.3% |
| large/parse | 3255.1 | 3131.2 | −123.9 | −3.8% |
| tiny/parse | 30.1 | 29.1 | −1.0 | −3.3% |

Three things make this an effect rather than noise, and the magnitudes are given so a
reader can disagree with the judgement rather than have to accept it.

* **It moves all four rows together**, by a tight band (−3.3 to −3.8%), and run-to-run
  variance does not do that. The two *no-hoist* runs differ from each other by 0.2 µs on
  `tiny/parse` and by 18.9 µs on `realistic/render`; the three hoisted runs span 27.4 µs
  on `realistic/render`. So the effect on `realistic/render` (56.9 µs) is ~3× the
  no-hoist spread and ~2× the hoisted spread — real, and not by a wide margin.
* **Its size tracks node count**, which is the mechanism's own prediction rather than a
  post-hoc fit: `large/parse` executes ~260 variable resolutions and saves 123.9 µs,
  `realistic/render` executes ~250 variable-plus-filter resolutions and saves 56.9 µs,
  `tiny/parse` executes one and saves 1.0 µs.
* **Its cost per executed import, ~0.24–0.48 µs, brackets the microbench's 0.207 µs
  from above** — which is the discrepancy §3.4 corrects for and the reason §3.4's
  estimate was revised up rather than down.

The effect is measured on one machine and one interpreter. §8 keeps that caveat.

**Why this is not the M11 "NIT" precedent.** M11 reverted a reviewer NIT that moved
imports within `resolve` because it altered the transcription's diff shape and bought
nothing measurable; the project's rule is that "two regions where one would do is a
permanent tax on every future bump". This change is the same *kind* of edit and is kept
on the one ground the precedent names: it is **measurable**, by the table above. The
region *count* is unchanged at six — the new region is a new `delete` opcode in an
already-declared run, and `tests/test_interposition.py` pins the opcode list and the
`equal` count as `["delete", "delete", "replace", "replace", "delete", "replace"]` and
**1120** (was 1121). That count fell by one for a reason worth recording, because the
obvious explanation is wrong: it is *not* `1214 − 2 = 1212, therefore 1121 − 1` by
arithmetic on the insertion. Ours moved 1214 → 1212, and the differing lines moved
93 → 92 while upstream's moved 86 → **87** — upstream gained a differing line because
*its* blank line 1022 now falls inside the merged replaced run. `1212 − 92 = 1120` and
`1207 − 87 = 1120`, and the two sides still meet. The first draft of that test's prose
gave the arithmetic answer, which is the wrong one.

---

## 6. Where the remaining gap is

### 6.1 Per-unit decomposition

`m16_bench_decompose.py` runs 50 of one thing and nothing else, under both engines. The
two sides are two processes, so the script prints both transcripts and **deliberately
does not subtract across them**; the subtraction is below and is the reader's to check.

| unit | shim µs/unit | stock µs/unit | Δ |
|---|---|---|---|
| `{{ r.name }}` | 2.046 | 1.451 | **+0.595** |
| `{{ r.a.b }}` | 5.073 | 4.139 | **+0.934** |
| `{{ r.name\|upper }}` | 3.903 | 1.803 | **+2.100** |
| `{{ r.value\|floatformat:2 }}` | 12.763 | 7.407 | **+5.356** |
| `{% translate "Report" %}` | 5.093 | 5.119 | −0.026 |
| `{% blocktranslate count c=r.count %}` | 13.664 | 12.828 | **+0.836** |
| an empty `{% for %}` body | 0.256 | 0.252 | **+0.004** |

Weighted by the units `realistic/render` actually uses — one `{{ row.name }}`, one
`{{ row.value|floatformat:2 }}` and one `{% blocktranslate %}` per row, over 50 rows,
with the base template's single `{% translate %}` (5.093 − 5.119 = −0.026) —

```
50 x (0.595 + 5.356 + 0.836) + (-0.026)  =  339.3 us
```

against the measured `realistic/render` gap of 1637.2 − 1211.5 = **425.7 µs**. **The
decomposition accounts for 80% of the gap, not all of it.** The ~86 µs it does not
explain is the `{% extends %}`/`BlockNode` machinery and the text nodes, which this
probe does not decompose; it is left as an unattributed remainder rather than assigned a
cause the probe did not measure. (`var.nested` and `filter.upper` are in the table
because they are the two filters the *other* workloads and the arm analysis need; the
page uses neither, so neither is in the sum.)

**Three rows are controls, and they are the most informative rows in the table.**

* `{% translate "Report" %}` is **at parity** (−0.026 µs, inside noise), and the
  *reason* is worth stating because the intuitive one is wrong. Walking the tree
  (`Engine.from_string("{% load i18n %}{% translate … %}").nodelist`) shows the node
  class is `django.templatetags.i18n.TranslateNode` — Django's own class, from a module
  the shim does **not** serve (the meter's `django.templatetags` scope is empty, so all
  five libraries are stock). Rust's parser reads the token and names that class in the
  descriptor, but the node is materialised *as* `TranslateNode` and its `render` is
  Django's own Python body running against our transcribed `base.py`. The only frame
  that differs from the stock control is `Node.render_annotated`. **Parity here says the
  transcribed node and nodelist machinery costs nothing measurable** — it is the
  cleanest control in the table for that claim.
* `{% blocktranslate %}` — the same stock node class — is **+0.836 µs**, and the
  difference between the two tags is one thing: `blocktranslate`'s `count` argument is
  a `FilterExpression`, resolved once per loop iteration. +0.836 against
  `{{ r.name }}`'s +0.595 is the same operation counted twice, which is the check that
  the decomposition attributes this row correctly.
* The empty loop body is **at parity** (+0.004 µs). **The node-tree walk is not the
  bottleneck.** `ForNode.render`'s loop, `NodeList.render`, the output buffer, the
  context push/pop — all transcribed Python, all essentially free relative to stock.
  This retires the intuitive-but-wrong remedy ("the walk is Python, so port the walk")
  as the explanation for the *measured* gap; §6.3 says what it would still buy.

**Put together, four fifths of the gap is two operations.** Of the 80% the sum above
attributes, everything is `FilterExpression.resolve`'s routing decision (1.404 µs,
§6.2) and `Variable._resolve_lookup`'s extra hop (+0.595 µs, `_native.resolve` plus the
`".".join`). The tree walk, the node classes, the i18n tags, the context stack and the
output buffer are all at parity — that is the *negative* result, and it is the one that
matters, because it retires the remedy the ratio most invites. That is a much narrower
finding than the ratio alone suggests, and it is the reason §6.3's arithmetic is as
short as it is.

### 6.2 The filter arm, in pieces

`m16_measure_library_tables.py`, 200k iterations, best of 5:

| term | µs |
|---|---|
| `django_own_filters()` | **1.061** |
| `django_own_filters().get(func)` | 0.039 |
| `effective_use_tz(context, func)` | 0.060 |
| `effective_autoescape(context, func)` | 0.060 |
| `_native.filter("upper", …)` — the whole crossing and body | 0.216 |
| **the whole routing arm** | **1.404** |

and the control that makes the first row readable: **the same six registry reads,
inlined without the merge machinery, cost 0.104 µs.** The 1.061 is therefore *not*
dictionary I/O on 66 handles — it is `_library_filter_tables()` rebuilding a five-tuple
of module objects on every application, plus `_library_module`'s `sys.modules.get` /
`importlib.import_module` fallback (`importlib.import_module` measured 0.274 µs against
the `sys.modules` hit at 0.046), plus the six-way identity check.

**This is the mechanism for `{{ r.name|upper }}`'s +2.100 µs, to within 5%**:
1.404 (arm) + 0.595 (`{{ r.name }}`'s own resolve delta) = 1.999, against 2.100 measured.
The upper filter's own Rust body is 0.216 µs and is not the problem — the Python
decision that *reaches* it is 6.5× the crossing.

### 6.3 The two levers, and why the verdict does not move

| lever | cost | share of `realistic/render` | basis |
|---|---|---|---|
| probe the six registries in place instead of merging | 1.404 → ~0.44 µs = ~96 µs | **5.8%** | subtracted on measured terms, not built |
| hoist the in-loop `host` import (§3.4) | ~34 µs | **2.1%** | microbench × 2.4×, the §5 correction |
| make Rust's `floatformat` not round-trip through Python | ~135 µs | **8.2%** | measured (§7.1) — **and it changes behaviour** |

Taking **all three** — 265 µs of a 425.7 µs gap — leaves 1637 − 265 = 1372 µs against
stock's 1211.5, i.e. **1.13×**, still slower. That arithmetic is the reason this
milestone stops rather than spending its budget on the first two: a partial win that
does not reach the criterion is worth reporting, but a partial win that cannot *reach*
it is worth reporting *instead of* being chased.

---

## 7. What was tried that did not work, and must not be re-tried

### 7.1 Delegating `floatformat` to Python instead of re-entering it — the trap

`_native.filter("floatformat", 3.0, (2,), True, False)` costs **8.654 µs**. Python's
`floatformat(3.0, 2)` costs **5.896 µs**, and Python's `floatformat` is what stock Django
calls. **Our Rust body is 2.7 µs slower than the Python function it replaces**, which is
the whole of `filter.floatformat`'s +5.356 µs once the 0.595 resolve delta and the 1.404
routing arm are subtracted.

The mechanism is the `PyFilterHost` re-entry rule. `number_format`
(`crates/template-py/src/filters.rs:1109`) does `py.import("django.utils.formats")` +
`getattr("number_format")` + a `PyDict` with three `set_item`s + a kwargs call, **per
application**; `decimal_parse` (`:1230`) does the same for `decimal.Decimal` and
`decimal.InvalidOperation` and then walks `as_tuple()` digit by digit and extracts each
as a `u8`. `number_format(...)` alone, called from Python, is 0.812 µs; the import,
`getattr` and kwargs-boxing on top are the rest.

**The fix is obvious and is not taken, on purpose.** The module already contains the
precedent — `MARK_SAFE` and `SAFE_DATA` are `PyOnceLock`s caching
`django.utils.safestring` names — so caching `decimal.Decimal` is in-style, not novel.
But it is a **behaviour change**: `decimal.Decimal` rebound or monkeypatched between
renders would be observed by stock Django and not by us. §3's whole budget is spent on
compatibility, and §5.2:499 already records the re-entry as a *choice* rather than a
cost ("`{{ x|date:"Y" }}` crosses the FFI on every render, permanently"). 135 µs does
not buy a compatibility question, and §6.3 shows it is not sufficient on its own. This
row exists so the next milestone does not re-derive it and then take it.

### 7.2 "Parse is the problem" — refuted by `tiny/parse`'s arithmetic

`tiny/parse` (29.1 vs 15.7) is *the* parse-dominated row, so it looked like the parse
path. It is not: `cProfile` over 3,000 `Engine().from_string("{{ a|upper }}")` puts
`inspect.getfullargspec` — reached from `FilterExpression.args_check` — at ~13 µs of the
~57 µs per parse *under the profiler*, and **stock Django calls the identical
`args_check` on the identical path** (`.django/django/template/base.py:772`). A cost both
sides pay cannot explain a gap between them, and the profiler's inflation makes the
absolute number unusable. The row is left as a measured gap with an *unnamed* cause,
which is the honest state: `tiny/parse` is the one workload this milestone decomposes no
further, and it is the worst ratio.

### 7.3 A per-handle memo of the routing decision

Refused unsound, with the reason already measured and written down one level up:
`django_own_filters`' docstring records that a cache keyed on emptiness (or on anything
coarser than all six registry *objects*) answers "not ours" for every name after a
re-executed `defaultfilters`, **and every test stays green** — the silent-and-total
failure the M11 measurement scripts walked into. A per-handle memo narrows the blast
radius and keeps the failure mode.

---

## 8. What was not measured — the residue, with its rank

1. **The brief's before-table is not reproduced** (§2), so the *absolute* before/after
   comparison across the brief is not available. Within this record's own harness the
   before/after delta is §5's A/B, which is sound on its own terms.
2. **`tiny/parse`'s gap is measured and its cause is not.** +13.4 µs on a one-variable
   template is 1.85×. Decomposed no further than §7.2 refutes.
3. **The decomposition is over one machine, one CPython (`3.14.4`), one build.** The
   interpreter matters and the project's own rule says so: three `python3` here are
   3.14.4/3.13.7/3.9.6 and CPython changed `in`-operator `TypeError` text at 3.14. A
   ratio measured under 3.13 would not be this ratio; nothing here was measured under
   another interpreter.
4. **Warm-cache, single-threaded, no I/O.** Every figure is a warm loop in a
   long-lived process. Cold-start behaviour — where importing the extension and
   building the six library tables is *not* amortised — is unmeasured, and it is the
   regime a one-shot CLI renderer lives in. The sign of the comparison there could
   differ; this record does not claim it does not.
5. **`realistic/render` reuses one `Context` across iterations.** Stock Django caches
   nothing per-context on this path and neither do we, but it is a property of the
   harness a reader should know rather than infer.
6. **No Rust was changed, so no rebuild was needed** — and this is also the milestone's
   largest omission: every lever in §6.3 that *is* sufficient is a Rust change, and none
   was attempted. The 5.8% + 2.1% pair was costed, not built (§6.3, §3.4).

---

## 9. Gate state at close

`bash scripts/check.sh` — the **full** run, not `--fast` — on the tree of commit
`6cf4db9`, verbatim tail:

```
=== differential harness ===
differential: 48 cases, identical (stock vs shim-installed)

=== generated files are current ===
pyregex_word_ranges.txt is current: 771 ranges, 142940 code points
crates/template-core/src/pyunicode/case.rs is current (28 upper entries)

=== django template_tests (shim installed) ===
checkout pinned and unmodified
verifying the Rust engine is installed...
engine installed; unshimmed() is [] over django.template, django.templatetags (10 more of django.template are declared not the engine)
OK (skipped=6) after 1547 tests, and the six declared skips by identity (scripts/run_django_suite.sh)

all checks passed
```

Exit status **0** (`$?` read from the script's own exit, not from a pipeline — the
project's rule that a status after a pipe is the pipe's). The acceptance run under
`scripts/run_django_suite.sh` — the one label this project's suite is, `template_tests`
— reports `Ran 1547 tests … OK (skipped=6)`, and this milestone's whole point is that
that number says nothing about speed. Unit suite **487**, `cargo test` **787** (unchanged:
no Rust in this milestone), anchors 1472 occurrences / 867 of 867 cited / 0 failures,
the differential harness 48 cases identical, `unshimmed()` `[]` in both scopes, and the
checkout pinned and unmodified. Byte-identity across the two engines holds for all four
benchmark workloads.
