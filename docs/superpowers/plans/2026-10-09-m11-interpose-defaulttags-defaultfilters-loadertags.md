# M11 — interpose `defaulttags`, `defaultfilters`, `loader_tags`

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `django.template.defaulttags`, `django.template.defaultfilters`
and `django.template.loader_tags` be our files, so the 454 `filter_tests` rows
and `syntax_tests`' tag rows move off Django's code and onto ours — and, for the
filters, onto Rust's, which is a path they have never been on.

**Architecture:** The same interposition mechanism M9 and M10 used, applied to
three modules at once: a transcription under `_impl/`, a `SHADOWING` entry, a
`DECLARED`/`COMPARED_PATH` pair in `scripts/check_transcriptions.py`, and rows in
`tests/test_interposition.py`. What is different here is that the *routing* is
part of the milestone rather than inherited: `_native.filter` is already
`FilterExpression.resolve`'s entire loop plus the body, so the work is deciding
where a built-in filter is recognised as Rust's and doing it once.

**Tech Stack:** Python 3.14, Rust (`crates/template-core`, `crates/template-py`),
`unittest`; the pinned checkout at `.django/` (6.0.9).

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md`
(§10.3's M11 row, §10.3's vacuity rule, §6's vendoring policy).
**Recon:** `.review/m11-recon.md` — read it first; it is the plan's argument.

## Global Constraints

* Django is pinned at **6.0.9** in `.django/`, gitignored, **never modified**.
* Acceptance is `bash scripts/check.sh` → `all checks passed`, containing
  `Ran 1547 tests … OK (skipped=6)`. Every task ends with it green.
* `_shim.unshimmed()` is the meter. M11 takes it **14 → 11**; no other task may
  move it.
* A transcription is **generated, not edited**: `scripts/gen_impl_transcription.py`
  owns the pin-plus-marker form, `scripts/check_transcriptions.py` owns the
  declaration, and a hand edit to a generated file fails `--check`.
* Every transcribed file's diff from the pin must be inside its declared regions;
  every claim in the plan's prose that names a line number is a claim about the
  pin and gets checked against it before it lands.
* Commit messages end with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
* Never round-trip `tests/filter_cases.py` through `splitlines()`/`join()`.

## Review Focus

Six input classes or failure modes the spec implies and no task's tests
exercise — most likely first. Each gets a row in the task that owns the code:

1. **A `{% load %}`ed library that rebinds a built-in filter.** `{{ x|lower }}`
   after loading a library defining `lower` must run *the library's* callable,
   never Rust's. (Task 2, Step 6.)
2. **A `{% load %}`ed library that rebinds a built-in tag.** Same question one
   level over, and it is already pinned (`test_filter_rebinding.py`'s shape) —
   M11 must not regress it when `defaulttags` becomes ours. (Task 3, Step 6.)
3. **A filter reached through a template, not through `_native.filter`.** The
   whole point of the milestone; a verbatim transcription passes every other
   check. (Task 2, Step 6.)
4. **`{{ x|lower }}` where `x` is not a `str`** — `@stringfilter`'s coercion
   happens inside `_native.filter`, so the `stringfilter` flag must survive the
   routing, not be re-derived. (Task 2, Step 8.)
5. **A template using a tag whose node class is ours.** `{% for %}`/`{% if %}`
   after Task 3 build *our* `ForNode`/`IfNode`; an inheritance or annotation
   protocol that reached Django's class would now be a different class.
   (Task 3, Step 5.)
6. **A chain that mixes a Rust arm and a host arm** — `{{ x|lower|mycustom }}`
   after a `{% load %}` holds one of each in a single `FilterExpression`, so
   the delegation cannot be whole-loop, and the loop must not run twice around
   the Rust arm. Routing rows prove *which* body ran and say nothing about how
   many times a wrapper ran around it; `escape` discriminates, because
   `escape("&")` is `&amp;` and `escape(escape("&"))` is `&amp;amp;`.
   (Task 2, Step 8.)

---

## File structure

| file | what it becomes |
|---|---|
| `python/django_rust_templates/_impl/loader_tags.py` | new — generated transcription of the pin |
| `python/django_rust_templates/_impl/defaultfilters.py` | new — generated transcription of the pin |
| `python/django_rust_templates/_impl/defaulttags.py` | new — generated transcription of the pin |
| `python/django_rust_templates/_impl/base.py` | modified — `FilterExpression.resolve` delegates the loop to Rust for built-ins (declared region) |
| `python/django_rust_templates/_shim.py` | modified — three `SHADOWING` entries |
| `scripts/check_transcriptions.py` | modified — three `DECLARED` + three `COMPARED_PATH` entries |
| `scripts/gen_impl_transcription.py` | modified — three `MODULES` entries |
| `tests/milestone_ownership.py` | modified — three names move `OWNERS` → `INTERPOSED` |
| `tests/test_interposition.py` | modified — routing rows (§Review Focus) |
| `docs/superpowers/specs/...-design.md` | modified — §10.3's M11 row at close |

---

### Task 0: Recon (done)

**Files:** `.review/m11-recon.md`, `.review/m11-measurements/`.

Committed as `b5fdf35`. No steps; the plan argues from it and the reviewer
reviews it before Task 1 starts.

---

### Task 1: `loader_tags` is our module — the meter is 13

**Files:**
- Create: `python/django_rust_templates/_impl/loader_tags.py` (generated)
- Modify: `scripts/gen_impl_transcription.py`, `python/django_rust_templates/_shim.py`,
  `scripts/check_transcriptions.py`, `tests/milestone_ownership.py`
- Test: `tests/test_interposition.py`

**Interfaces:**
- Consumes: nothing new. The three tags are Rust-compiled since M7 and the host
  predicate (`host.py:43-88`) already reads `loader_tags.register.tags`.
- Produces: `django.template.loader_tags` served from `_impl`, so
  `loader_tags.Node is django.template.base.Node` is **ours** by construction.

Smallest file first (370 lines, 4 classes), and it is the one whose
interposition **expires M7's stated reason** for `test_include_cache` being
vacuous.

- [ ] **Step 1: Add the file to the generator and generate it.**

```python
# scripts/gen_impl_transcription.py, in MODULES
"loader_tags.py": "django/template/loader_tags.py",
```

Run: `python -I scripts/gen_impl_transcription.py loader_tags.py`
Expected: writes `_impl/loader_tags.py` — the pin's 370 lines plus the two-line
marker. If it refuses with the `HAND_WRITTEN` message, stop: this file was
expected to be pin-plus-marker and is not.

- [ ] **Step 2: Run the guard to see the diff the marker does not explain.**

Run: `python -I scripts/check_transcriptions.py`
Expected: FAIL, "loader_tags.py: 1 undeclared differing region" (or equivalent) —
the guard is the instrument that tells us whether the file really is
pin-plus-marker. If it reports **0**, the file is verbatim and Step 3 declares
nothing.

- [ ] **Step 3: Declare the difference in `scripts/check_transcriptions.py`.**

If Step 2 reported a region, declare it in `DECLARED["loader_tags.py"]` with a
comment saying *why* that region differs. If it reported nothing, add the key
with an empty list, because the guard asserts the key sets of `DECLARED` and
`COMPARED_PATH` are equal — an absent key is invisible to a re-measurement that
iterates the pin.

- [ ] **Step 4: Measure the compared path and pin it.**

Run: `python -I .review/m9-measurements/m9_measure_parse_path.py loader_tags.py`
Expected: the entry, ready to paste into `COMPARED_PATH["loader_tags.py"]`.
Note the expectation from the recon: the three `do_*` functions are Rust's, so
what the parity harness reaches here is likely `[]` or a small set. **A zero must
be accompanied by the row that says the instrument fires nearby** (see
`test_the_utils_zero_is_a_measurement_and_not_a_silence`'s docstring) — reuse
that discipline rather than repeating the argument.

- [ ] **Step 5: Add the `SHADOWING` entry and move the ownership row.**

`_shim.py`: `"loader_tags.py": "transcribed",`
`tests/milestone_ownership.py`: move `"loader_tags": "M11"` from `OWNERS` to
`INTERPOSED`.

- [ ] **Step 6: Re-read `test_include_cache` now that M7's reason has expired.**

M7 recorded the `.django` row as vacuous *because the shim does not interpose
`loader_tags`*. That sentence is false as of Step 5. Run the row and say in
`tests/test_interposition.py` whether it is now real signal — if it is, name
which of it and `tests/test_inheritance_gate.py` is the direct-entry instrument
and which is the end-to-end one, rather than leaving two instruments claiming
one question.

**Do not retire either instrument on the strength of "now covered".** Two
instruments for one question is a cost; deleting the one that would have caught
something is a defect, and which is which cannot be read off the fact that both
are green. The general form is worth recording beside the row: **a vacuity
justification is a claim about the tree on a date, and interposing a module is
precisely the event that expires one** — the rule `.review/m5-seams.md` already
states for seams ("the row expires when the seam, its caller, or the input that
exercises it changes"), which cannot fail a build and so has to be re-read on
the trigger. M7's vacuity reasons fire the same way at this milestone.

If the row does go real, decide by measurement rather than inference: break the
behaviour it exercises and see which rows fail. If the `.django` row fails and
`test_inheritance_gate.py` does not, the gate is the one to demote — and the
measurement says so.

- [ ] **Step 7: Run the suite and commit.**

Run: `bash scripts/check.sh`
Expected: `all checks passed`, `Ran 1547 tests … OK (skipped=6)`, meter **13**.

```bash
git add -A
git commit -m "M11: loader_tags is our module — the meter is 13"
```

---

### Task 2: built-in filters reach Rust — the meter is 12

The milestone's load-bearing task. 454 rows turn from Django's filters to ours.

**Files:**
- Create: `python/django_rust_templates/_impl/defaultfilters.py` (generated)
- Modify: `python/django_rust_templates/_impl/base.py` (the routing), `_shim.py`,
  `check_transcriptions.py`, `gen_impl_transcription.py`, `milestone_ownership.py`
- Test: `tests/test_interposition.py`

**Interfaces:**
- Consumes: `_native.filter(name, value, args, autoescape, use_tz)` — the whole
  `FilterExpression.resolve` loop plus the body; `_native.filter_names()` (57);
  `_native.stringfilter_filters()` (29); `_native.filter_flags(name)` →
  `(is_safe, needs_autoescape, expects_localtime)`.
- Produces: a `FilterExpression.resolve` that runs Rust's loop for a built-in
  filter and Python's for anything else, decided **once per filter**.

**The decision this task makes, and its reason.** The arm is *identity of the
handle*, decided **at render time in `resolve`**, not by name and not at parse
time:

* **Not by name** — `{% load %}`'s `self.filters.update(lib.filters)`
  (`base.py:670`) rebinds a name to a caller's callable, and
  `_impl/parser.py:542` carries the `find_filter` handle rather than a name for
  exactly that reason.
* **Not at parse time** — the descriptor would have to carry a per-filter
  "is built-in" bit, which is a second account of a fact `resolve` can read
  directly, and it would go stale the moment a library is loaded *after* the
  filter expression was compiled (`{% load %}` is a parse-time directive that
  can appear between them).
* The identity test is `func is defaultfilters.register.filters[name]` — the same
  shape as `host.py`'s tag predicate, and available without a new table because
  `Library.filter` stores the function itself.

**Which table, named rather than left to the call site.** The comparison table
is `defaultfilters.register.filters` — the registry of the module Rust ports,
read at the module the shim serves, never `parser.filters`. This tree has
already paid for the other choice one level over: `django_own_tags()`'s
docstring records that its first version snapshotted `parser.tags` at
`ShellHost` construction, *after* `Parser.__init__` had merged the engine's
builtins, so a caller whose `OPTIONS["builtins"]` registered `for` compared
equal to the snapshot, Rust's `do_for` ran and the caller's tag was silently
ignored — and the snapshot was the *stated reason* for the predicate. The filter
analogue is exact, and `parser.filters` is the table that reads natural at the
call site. Measured on the pinned tree: `register.filters` holds 57 entries and
`register.filters["lower"] is defaultfilters.lower` is True.

**A chain can mix arms, so the delegation is per-filter and never whole-loop.**
`{{ x|lower|mycustom }}` after a `{% load %}` has one Rust arm and one host arm
inside a single `FilterExpression`, which is what `_native.filter`'s docstring
already says ("run **one** built-in filter over one value") and what gives this
task its consequence for the Python side: **`_impl/base.py`'s
`FilterExpression.resolve` stops being upstream's text.** It is upstream's text
today (`:790-819`) and upstream's text applies `needs_autoescape`, the
`stringfilter` coercion and `is_safe`/`mark_safe` around *every* arm — so for
Rust's arm Python must not, and the branch goes in a declared region. M11 is the
first milestone to declare a region in `base.py` for a render-path function.

**And that region is outside clause (4)'s reach — measured, and the entry must
say so.** `COMPARED_PATH["base.py"]` holds 29 entries and its
`FilterExpression.*` ones are `__init__` (744-788) and `args_check` (827-844);
`FilterExpression.resolve` is **not in it**, because the compared path measures
the parse harness and `resolve` is render-time. So a declared region over
`resolve` is permitted by clause (4) and by `assert_target_is_not_rust_owned`,
correctly and silently: the guard that has caught two defects this month will
say nothing about M11's most load-bearing edit. That is a reason to write the
under-protection into the entry — the way `utils.py`'s zero names its harness
and `context.py`'s three functions name why the reach is `do_verbatim` — and not
a reason to add anything to the guard. An under-protected region that says it is
under-protected is a measurement.

- [ ] **Step 1: Write the failing row that says what the milestone is worth.**

In `tests/test_interposition.py`, a row that renders a template using built-in
filters and asserts `_native.filter` was reached — by counting calls, the way
`scripts/differential.py` already counts `_native` calls. Assert the premise
too: with the row's own engine, `Parser.find_filter("lower")` is
`_impl/defaultfilters.lower` (not Django's), because a row that passes while the
module is still Django's is the M9 finding-3 failure.

- [ ] **Step 2: Run it to make sure it fails.**

Run: `bash scripts/run_unit_tests.sh tests.test_interposition -k filter`
Expected: FAIL — 0 `_native.filter` calls, and `find_filter` naming
`.django/.../defaultfilters.py`.

- [ ] **Step 3: Generate `_impl/defaultfilters.py` and declare it.**

`MODULES["defaultfilters.py"] = "django/template/defaultfilters.py"`, then
`python -I scripts/gen_impl_transcription.py defaultfilters.py`, then `DECLARED`
and `COMPARED_PATH` entries measured the way Task 1's Steps 2–4 did.

**Do not delegate the 59 bodies.** They stay verbatim: the routing lives in
`resolve`, and a delegate per body would be 59 declared regions that all say the
same thing. What *moves* is the loop, once.

- [ ] **Step 4: Implement the routing in `_impl/base.py`.**

`FilterExpression.resolve`'s loop gains one branch, in a declared region:
if the handle is the built-in one for its name, call `_native.filter` with the
name, the value, the argument tuple, `context.autoescape` and `context.use_tz`,
and take the result as the new value; otherwise the existing loop body runs
unchanged. **The branch replaces the loop for that filter, it does not wrap it** —
otherwise `@stringfilter` coerces twice and `mark_safe` fires twice (§2,
constraint 1).

Write the region's `DECLARED` comment as the under-protection note: this is a
render-path function, `COMPARED_PATH["base.py"]` cannot reach it (the compared
path is the parse harness), so clause (4) permits this region without
examining it. Say what it is that the guard therefore cannot see.

- [ ] **Step 5: Run the failing row and the suite.**

Run: `bash scripts/check.sh`
Expected: `all checks passed`; `Ran 1547 tests … OK (skipped=6)`. **454
`filter_tests` rows are now real signal** — any red here is the milestone's
whole point, not a nuisance.

- [ ] **Step 6: Pin the rebound-filter escape (Review Focus 1).**

A row that loads a library defining `lower`, renders `{{ x|lower }}`, and
asserts the *library's* callable ran and `_native.filter` was **not** reached for
it.

- [ ] **Step 7: Pin the `stringfilter` coercion (Review Focus 4).**

A row where the filter is reached through a rendered template with a non-`str`
input, asserting the coerced value is what the body received — the flag must have
travelled, not been re-derived.

- [ ] **Step 8: Pin the arms that can only fail for the doubled-wrapper reason (Review Focus 6).**

Routing rows prove which body ran and cannot see the loop running twice, so this
step adds the inputs that discriminate:

* `{{ x|escape }}` with `x = "&"` — `escape("&")` is `&amp;` and
  `escape(escape("&"))` is `&amp;amp;`, so an arm applied on both sides is
  visible in the output rather than silent.
* the same filter inside `{% autoescape off %}`, where `needs_autoescape` being
  applied twice changes the answer the body gives.
* a `stringfilter` filter over a non-`str` whose `__str__` is observable, so a
  second coercion is countable.
* **and the table itself, by name**: `_native.stringfilter_filters()` is 29 and
  `grep -c '^@stringfilter' .django/django/template/defaultfilters.py` is 29 —
  both measured — but the two are separate tables that produce a coercion
  difference and no error when they disagree, so the row compares the **names**,
  the way `host.py`'s "the 25 Rust-owned names are exactly the union of these
  two tables" does. A count can agree while the membership differs.

- [ ] **Step 9: Move the ownership row, run the fast check, commit.**

Run: `bash scripts/check.sh`
Expected: meter **12**.

```bash
git add -A
git commit -m "M11: built-in filters reach Rust — filter_tests is signal at last; the meter is 12"
```

---

### Task 3: `defaulttags` is our module — the meter is 11

**Files:**
- Create: `python/django_rust_templates/_impl/defaulttags.py` (generated)
- Modify: the four tables as in Tasks 1–2
- Test: `tests/test_interposition.py`

**Interfaces:**
- Consumes: `_impl/defaultfilters.py` (Task 2) — `defaulttags.py:41` imports
  `date` from it; `library.py` (M13's) is imported by the pin and is *not*
  interposed by this milestone.
- Produces: `django.template.defaulttags` served from `_impl`, so the 25 node
  classes the tag corpus builds are ours, and `URLNode` — which
  `syntax_tests/test_url.py` imports by name — is ours.

- [ ] **Step 1: Generate, and expect the largest declared diff of the three.**

`python -I scripts/gen_impl_transcription.py defaulttags.py`, then run the guard
and read what it reports before declaring anything. The 26 `do_*` bodies are
Rust-compiled, so they are unreachable through the shim; **transcribe them
verbatim and say so in the `DECLARED` comment**, because "unreachable but
faithful" and "replaced by a stub" are different claims about the file and only
one of them is true here.

- [ ] **Step 2: Measure the compared path.**

Run: `python -I .review/m9-measurements/m9_measure_parse_path.py defaulttags.py`
Expected: a non-empty set — `load` and `querystring` are the two names the host
does *not* own, so their `do_*` functions are executed from Python, and the stub
tags `partial`/`partialdef` route host-side too. Paste, and check the set against
that expectation rather than accepting whatever comes out.

- [ ] **Step 3: Move the ownership row and run the suite.**

Run: `bash scripts/check.sh`
Expected: `all checks passed`; `syntax_tests`' tag rows now build our node
classes; meter **11**.

- [ ] **Step 4: Pin the tag-rebinding escape (Review Focus 2).**

`test_filter_rebinding.py`'s shape, for a tag: a `{% load %}`ed library
registering `if` must win over Rust's, because the host predicate reads identity
and not a list.

- [ ] **Step 5: Pin that the node classes are ours (Review Focus 5).**

A row asserting `django.template.defaulttags.ForNode is not` the pinned file's,
and that a rendered `{% for %}` produces an instance of ours — the annotation
and inheritance protocols compare classes, so this is the row that would catch a
half-interposed module.

- [ ] **Step 6: Commit.**

```bash
git add -A
git commit -m "M11: defaulttags is our module — the meter is 11"
```

---

### Task 4: Close-out

- [ ] **Step 1: Re-measure the milestone's numbers on the closed tree**, rather
  than reporting the ones from the tasks: `unshimmed()` (expect 11), the unit
  suite count, `Ran 1547 tests … OK (skipped=6)`, `all checks passed`.
- [ ] **Step 2: Update §10.3's M11 row** with what is measured: the 454 rows that
  changed meaning, the routing decision and its reason, the meter 14 → 11, and
  the vacuity re-read of `test_include_cache`.
- [ ] **Step 3: Update `tests/milestone_ownership.py`'s prose** if any name's
  disposition changed, and confirm `INTERPOSED` is the meter's complement.
- [ ] **Step 4: Sweep every new prose line number** against the pinned tree
  (`scripts/check_python_anchors.py --check --frozen`).
- [ ] **Step 5: Send the close-out to the reviewer** and fold in what they find.

---

## Task 1 amendment: the compared-path sweep could not see a registry-dispatched function

*Appended after Task 1 was executed, rather than edited into Step 4 — the plan is
the record of what was expected and the amendment is the record of what was
measured, and a prediction silently rewritten is a prediction that cannot be
checked. Step 4's expectation was: "the three `do_*` functions are Rust's, so
what the parity harness reaches here is likely `[]` or a small set." It is
neither — it is **7**, and the three `do_*` are in it.*

**The disagreement, and it is the instrument's.** Step 4's sweep printed **4**
functions for `loader_tags.py`. An independent instrument built on call frames
(`sys.setprofile`; `frame.f_code.co_firstlineno` resolved to a label through
`definitions`' spans) read **7**. The three the sweep could not see were exactly
`do_block`, `do_extends` and `do_include`.

**Why, measured rather than argued.** Django does not call a tag's compile
function through the module that defines it. `Parser.parse` dispatches through
`parser.tags[command]`, and `Parser.add_library` builds that dict by copying —
`self.tags.update(lib.tags)` (`base.py:669`). So the callable is a dict *value*,
and `bindings()` walks module *namespaces*. The wrapper went on the module
global; the object the parse actually called was the unwrapped original.

**The direction is the unsafe one, which is why this is not a count to shrug
at.** Clause (4) refuses a declared region on the compared path so the parity
comparison cannot become Rust against Rust — and the sweep is clause (4)'s
input. Under-reporting means a region declared inside `do_block` is accepted
while the harness runs `do_block`. For M11 specifically, a tag's compile function
is where a transcription would most plausibly hide such a region.

**The fix, two names, in `tests/compared_path.py`.** `registries()` yields every
`Library`-shaped dispatch table reachable from any `django.*` module namespace;
`rebind_registries(old, new)` rewrites every entry that `is old`. Both are called
where the existing `rebind` is: on the way in from `wrap()`, and on the way out
from the restore loop.

**A sub-finding worth its line, because it only appears under full discovery.**
The first `registries()` read `getattr(value, "tags", None)` off arbitrary module
attributes. On Django's staticfiles storage that fires `LazyObject._setup()` and
raised `ImproperlyConfigured: You're using the staticfiles app without having set
the required STATIC_URL setting` — never in a targeted run, only in the whole
suite. `vars(value)` inside `try/except TypeError` is the fix. The rule is in the
docstring because it generalises: **a probe that can execute the code it is
probing is not a probe.**

**Verified in both directions.** With the fix, sweep and frames agree at 7 and
both disagreement lists are empty; with `rebind_registries` disabled, the pre-fix
reading reproduces at **4** — so the delta is the defect and not the corpus. Both
instruments agreed on all eleven other pinned modules, before and after.

**The alternative, offered and not taken.** The frame instrument is the
independent one and is more faithful to "the code the comparison runs": it
observes execution rather than reachability-by-binding, and it is structurally
immune to this whole family. Replacing the sweep with it is a larger change — a
profiler in the guard's path, and a way to attribute frames to spans — so it is
raised with the reviewer rather than decided inside Task 1.
