# M4 — All 57 built-in filters: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rust owns the 57 filters Django's `defaultfilters` registers — their bodies, their `FilterFlags`, and the call protocol `FilterExpression.resolve` applies to them — proven by corpus parity against the pinned tree, and structured so that the filter work fans out to parallel agents without any two of them touching one file.

**Architecture:** M3 established that the render path stays Python's (§5.5) and that Rust owns the *parse*; M4 adds a **value model and a filter seam** to `template-core` and lands the 57 bodies behind a registry written once, in full, with unimplemented filters as stubs in their own files — the same shape `parser/registry.rs` uses for tags. Filters are not interposed at M4: `_native.render` still materialises into Django's real nodes and calls Django's `Template.render`, so `{{ x|upper }}` keeps running Django's `upper` until M11 replaces `defaultfilters`. What M4 delivers is the thing M11 will swap in, plus the parity evidence that it is right.

**Tech Stack:** Rust (edition 2021, `regex`, no Python dependency in `template-core`), PyO3 0.29 in `template-py`, Python 3.14 shell, Django 6.0.9 pinned at `.django/`.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` — read §5.1 (the crate's contents), §5.2 (the seam), §5.5 (the shadow tree), §6 (ownership), §9 (the three test layers), §10.3, §10.4. This plan argues from the spec; where they disagree the spec wins and this plan is wrong.

## Global Constraints

Copied verbatim from the spec; every task's requirements implicitly include them.

- **Django 6.0.9**, pinned at `.django/`. Never modified. It is the
  specification, not a dependency.
- **`cargo test` does not run clippy.** `scripts/check.sh` gates on
  `cargo clippy --workspace --all-targets -- -D warnings`. Every task runs
  clippy explicitly before committing. Two lints have already bitten this
  project's plan-supplied code: `clippy::module_inception` and
  `clippy::type_complexity`. Name tuples with a `type` alias before writing
  them; never nest a module `x/x.rs`.
- **No indented non-Rust code in doc comments.** rustdoc compiles indented
  blocks as doctests and `cargo test` will run them; this already broke a build
  once. Quote code inline or fence it with ```` ```text ````.
- **`source scripts/env.sh` before any cargo or venv command.** `cargo` is not
  on `PATH` otherwise.
- **`maturin develop` after every Rust change** or the Python tests silently
  exercise a stale `.so`.
- **Python is never a dependency of `template-core`.** If the engine needs
  something from Python it goes behind a trait implemented in `template-py`
  (§5.2).
- **Django's Python source is not vendored.** Error strings, positions and reprs
  are ported verbatim; algorithms are written against Django as a spec (§6).
- **A change is not committed until its review round returns APPROVE or all
  blocker/major findings are resolved** (§11).
- **Every snippet in this plan is executed against the pinned tree before the
  plan is committed, and a plan is not reviewable until it is** (§11). Where a
  step's evidence is a mutation, the mutation is *run* — M2 lost three plan
  steps to predictions about what an edit produces that nobody had made.
- **A task states its declared environment.** M2's and M3's defect rates were
  dominated by correct knowledge of Django combined with wrong assumptions about
  the harness. Each task below names what is constructible at that point, what
  imports resolve, and what the shim does and does not shadow.
- **Every guard is observed failing for its intended reason before it is kept**
  (§11), by disabling the guard and re-running. A guard nobody has seen fail is
  a comment.

## Review Focus

The five inputs and conditions the spec implies but no single task's tests
exercise. Each is pinned by a test in the task that owns the code.

1. **Python's case mapping is not Rust's — and that is two different findings
   with two different answers.** Measured against the pinned tree, over all
   1,112,064 code points: **56 code points diverge, 28 in each direction**, and
   every difference is the same shape — Python maps the code
   point to *itself* where Rust maps it to another character. `U+A7D3` is `Ll`
   in Python 3.14.4's Unicode 16.0.0 and `.upper()` returns itself, while Rust
   1.98's `char::to_uppercase` returns `U+A7D2`; `U+16EBB` (Beria Erfe, new in
   Unicode 16.0) is a third family. So Rust's tables are **ahead** of Python's
   here — the opposite of M1's `\w` divergence, where Rust was behind.
   **That is the code-point-local half, and it is `upper` and `capfirst`'s
   problem: ported, in Rust, pinned by an exhaustive parity test (Task 1).**
   The other half was found later and is not a table at all: `str.lower()` and
   `str.title()` are *context*-sensitive (Final_Sigma — `"ΟΣ".lower()` is
   `"ος"`), so no per-code-point fixup expresses them and both filters delegate
   to the host instead. A first-principles `title` reproduced all 1,112,064
   single code points and still failed 444 of 400,000 strings. **Owned by
   Task 1 (the table), Task 2's `str_lower`/`str_title` (the context half), and
   Task 4.**
2. **"Rust owns the filter" is a tiered claim and the tiers must be named.**
   `date`/`time` read the active language, `floatformat`/`filesizeformat` read
   `settings` and the **thread-local `decimal` context**
   (`defaultfilters.py:200`, `prec = max(getcontext().prec, prec)`), `urlize`
   reads `settings.URLIZE_ASSUME_HTTPS`, `timesince`/`timeuntil` read the wall
   clock and `gettext`. The plan's answer is §D2, and §D2 was **corrected after
   review**: this is a *permanent* delegation, not a stage that M10 ends, and the
   set is **20 filters, derived** — see §D2 for the derivation, the second
   independent metric (`lower` and `title`, via CPython's context-sensitive case
   methods), and the three neighbouring counts that are each a right answer to a
   different question.
   **Pinned by Task 3's derivation test** (which computes the set rather than
   trusting it) and Task 10's corpus, which renders the settings-sensitive ones
   under two settings states and asserts both sides move together.
3. **`@stringfilter` is a second, independent safety path from
   `base.py:816`'s.** 29 of the 57 are wrapped; the wrapper coerces with `str()`
   and then marks the result safe when the **coerced** input was `SafeData`,
   while `FilterExpression.resolve` checks the **pre-filter** `obj`
   (`base.py:803-819`). The two disagree whenever a filter is given a non-string
   that is `SafeData` — which cannot happen today, because `mark_safe` of a
   non-`str` goes through `SafeString.__new__`'s `str()` … and *that* is the
   measurement the plan must carry rather than assume. **Owned by Task 2's D3
   decision and pinned by Task 3's flags/corpus tests.**
4. **`random` cannot be pinned by equality.** `defaultfilters.random` is
   `random_module.choice(value)` on the global RNG. A corpus row asserting an
   output is a test that fails 56 times in 57. The property is *membership*, and
   the corpus must say so by name. **Owned by Task 6.**
5. **`pprint` is mentioned nowhere in Django's own suite.** Measured: a
   `\bpprint\b` token search over every `.py` under
   `.django/tests/template_tests/` returns zero hits and there is no
   `test_pprint.py`. It is therefore the one filter with **no upstream oracle**,
   and the one most likely to be implemented from a guess. **Owned by Task 9**,
   which pins it against `pprint.pformat` directly rather than through a
   template.

---

## Scope of M4

§10.3's M4 row: *"All 57 built-in filters"*, done when *"Filter corpus parity,
incl. `FilterFlags` round-tripping"*.

**M4 adds no tags and interposes nothing.** M5 owns the remaining tags, M11 owns
swapping `defaultfilters` out from under Django. So M4's deliverable is not
observable in the Django suite at all — the suite stays at `Ran 1547 tests … OK
(skipped=6)` and the progress meter stays at 25, and a reader who expects those
numbers to move at M4 has the wrong model. What moves is:

- `cargo test -p template-core` — the 57 bodies' own unit tests, which fan-out
  agents run in a bare shell;
- `tests/test_filter_parity.py` — every filter's output against Django's real
  implementation over `tests/filter_cases.py`, comparing **type as well as
  text**, so a lost safety marker is a failure;
- `tests/test_filter_inventory.py` — the names, the three flags, the
  `@stringfilter` membership and the stub set, cross-checked against Django's
  live registry so the table cannot drift.

**The count is 57 by one metric and needs its metric attached**, per §10.4's
standing instruction. Measured: `len(defaultfilters.register.filters)` is 57.
The module has **59 top-level `def`s** and neither set contains the other — 7
registered callables have a `def` name that is not their registry name
(`escape`→`escape_filter`, `escapejs`→`escapejs_filter`,
`linebreaks`→`linebreaks_filter`, `phone2numeric`→`phone2numeric_filter`,
`slice`→`slice_filter`, `timesince`→`timesince_filter`,
`timeuntil`→`timeuntil_filter`), and 9 `def`s are not filter names (those 7 plus
`_property_resolver` and `stringfilter`). `defaultfilters.escape` is
`django.utils.html.escape`, **not** the filter. `tests/test_filter_inventory.py`
pins all of this by name.

**What M4 does not do, stated so it is not rediscovered:**

- It does not interpose `defaultfilters`, `defaulttags` or `base` (M11, M9).
- It does not implement the escape **protocol** beyond what the filters need.
  `Promise`, `__html__`, and `is_safe` on arbitrary objects are M8's; M4 adds
  `conditional_escape` because `escape`, `force_escape`, `escapeseq` and `join`
  cannot exist without it, and §D4 says so as the milestone's one deviation.
- It does not port `pprint.pformat`, `unicodedata.normalize`,
  `textwrap.TextWrapper`, `HTMLParser` or `json.dumps`. §D2 names which filters
  ask the host for these and why that is the seam working rather than a hole.
- It does not touch `MAX_NESTING_DEPTH` or anything else M3 corrected.

---

## Design decisions this plan makes, and why

**D1 — `Value` carries the common Python scalars natively and everything else by
handle.** The forcing constraint is not performance, it is §10.4's fan-out
contract: a fan-out agent must be able to prove its filter with
`cargo test -p template-core` in a bare shell — no `.so`, no `maturin develop`,
no venv, no `.django`. A `Value` that is only an opaque host handle makes every
filter untestable there, and `#[cfg(test)]` unit tests would become integration
tests against Python. So:

```rust
pub enum Value {
    None,
    Bool(bool),
    Int(i64),
    /// A Python `int` outside `i64`, as canonical decimal text — the same
    /// representation `AttrValue::BigInt` uses, for the same reason.
    BigInt(Box<str>),
    Float(f64),
    Str(Box<str>),
    /// A `SafeString`: the text and the marker in one variant, so no filter can
    /// produce the text while dropping the marker by forgetting a second field.
    Safe(Box<str>),
    List(Vec<Value>),
    /// Anything else the host owns — a `datetime`, a `Decimal`, a `dict`, a
    /// tuple, a model instance, a `Promise`.
    Opaque(ValueId),
}
```

`Opaque` is an index into a table the **host** owns for the duration of one
top-level filter call, not the parse-scoped `NodeRef` table the resolver uses:
filter values are created *by filters* (`dictsort` returns a fresh list,
`template_localtime` may return a new object) and outlive no single node, so the
two lifetimes are genuinely different and sharing one table would give the
resolver's identity guarantees to values that have no identity to guarantee.

**D2 — Rust owns the filter and its algorithm; the host owns the engines whose
state is not the filter's argument.** Stated as a rule so the next reader does
not have to re-derive which filters are "really" Rust:

> A filter's body is Rust. When that body asks a question whose answer is not a
> function of the filter's arguments — what the active language is, what
> `settings` says, what a `decimal.Context` is set to, what `timezone.now()`
> returns — it asks the host, and the question is a named method on `FilterHost`
> rather than a call into Django's `defaultfilters`.

That is why `date` is Rust (it parses the format string, applies the `arg`
dispatch, decides between `date_format`/`time_format` — see
`defaultfilters.py:783-810`) while `formats.date_format` is a host call, and why
`floatformat`'s rounding, exponent arithmetic and 200-digit refusal are Rust
while `formats.number_format` is a host call.

**This delegation is permanent, and an earlier revision of this plan said
otherwise.** It claimed `urlize` would be "ported twice" because
"§10.4's M10 row interposes `utils`". **That is false, and it is false in a way
worth writing down rather than quietly deleting.** `_shim.py:52` sets
`TARGET = "django.template"`, and M10's `utils` sits in that row's list beside
`context`, `engine`, `loaders` and `origin` — all `django/template/*.py`. It is
`django/template/utils.py`, the `EngineHandler` module, which exists. It is not
`django.utils`. **The finder takes over exactly one namespace, so
`django.utils.dateformat`, `django.utils.formats`, `django.utils.html`,
`django.utils.text`, `django.utils.timesince` and `django.utils.translation` are
unreachable by any milestone in §10.3**, so no future milestone brings the 18
bodies whose answer lives in them home — which is what makes §D2's delegation
permanent rather than a stage M10 ends. §3 decided this — "We depend on the real
Django for everything else" — so it is the spec working as written; §6 has been
corrected, because it said Rust owns "all built-in tags and filters" and that was
too strong.

**The set is derived rather than enumerated**, by two metrics, because this
project has now had seven count disputes that were each two right answers to two
unstated questions. The first metric, which Task 3's `test_filter_inventory.py`
computes:

> a filter delegates iff the utility its body references bottoms out, within that
> utility's own module, in runtime state Rust cannot hold (settings, locale, the
> active language, the clock) or in a Python-stdlib table-or-parser port
> (`HTMLParser`, `textwrap`, `json`, `unicodedata`, `pprint`, `urllib.parse`).

**A second, independent metric, because this one measures references to
`django.utils` and a filter can rest on a CPython string method instead:**

> a filter delegates iff its body calls a `str` method whose result is not a
> function of the code point alone.

The review asked for this as a pre-fan-out verification and it found two more, so
the set is **20**, not 18. It is the fifth Unicode finding in this project, and the
first that is **not a table**: after `\w`, whitespace, `combining` and this
milestone's own per-code-point case fixup, `str.lower()` turns out to depend on
what surrounds the code point.

Measured against `.django` @ 6.0.9: **18** by the first metric — `date`,
`filesizeformat`, `floatformat`, `iriencode`, `json_script`, `slugify`,
`striptags`, `time`, `timesince`, `timeuntil`, `truncatechars`,
`truncatechars_html`, `truncatewords`, `truncatewords_html`, `urlize`,
`urlizetrunc`, `wordwrap`, `yesno` — and **2** more by the second, `lower` and
`title`, for **20**. `slugify` was already in the 18 and its `.lower()` now goes
through `str_lower` as well.

The three neighbouring counts for the *first* metric, each correct for its own
question and each wrong for this one: **57** registered filters; **31** reference
*any* `django.utils` name, which sweeps in `mark_safe` (a primitive Rust
reimplements as `Value::Safe`); **26** reference a utility living in a module that
imports something stateful, which wrongly sweeps in `escape`, `join`, `escapeseq`,
`force_escape`, `linenumbers` and `linebreaks` because `strip_tags` shares a file
with them. Classifying at module granularity is the mistake; the delegation is a
property of the utility.

**`upper` and `capfirst` stay Rust's, and that is a measurement rather than an
assumption.** `str.upper()` has no context rule — the exhaustive diff finds the
same 28 code points as `char::to_uppercase`, all of them code-point-local — so
Task 1's per-code-point table is complete for them. `capfirst` is
`value[0].upper() + value[1:]`, which is one per-code-point `upper` and no
lowercasing at all, so it is unaffected by the Final_Sigma finding.

One classification in that list looks over-broad and was checked rather than
assumed: the four `Truncator` filters delegate even on the **non-html** path, so
their inclusion is not an artefact of `Truncator`'s `HTMLParser` branches sitting
in the same class. `Truncator.chars` reaches `unicodedata.normalize("NFC", …)`
and `_text_chars` reaches `unicodedata.combining` (`django/utils/text.py:205`,
`:220`), and the truncation marker itself is translatable — `add_truncation_text`
falls back to `gettext("%s…")`. So `truncatechars` needs the `unicodedata` table
and the active language, and `truncatewords` needs the language.

One consequence, stated as a choice because it otherwise reads as an oversight at
M15: **`{{ x|date:"Y" }}` crosses the FFI on every render, forever.** §3 makes
performance a non-criterion, so this is acceptable — but it is acceptable
*because it was decided*, not because nobody noticed.

**D3 — `@stringfilter` is baked into each filter's body; `FilterFlags` keeps
§5.2's three fields.** The wrapper (`defaultfilters.py:38-52`) coerces with
`str()` and then marks safe iff the *coerced* value was `SafeData` and the inner
function's `is_safe` is true. Reproducing that needs no fourth flag: `is_safe` is
already carried, the coercion is part of the filter's implementation, and "was
the coerced value safe" is decidable at the call because `Value::Safe` is a
variant. The alternative — a `stringfilter: bool` on the registry entry and a
generic wrapper in the seam — was rejected because it would put the wrapper's
behaviour in a *shared* file, where §10.4's contract says a fan-out agent cannot
reach it, and the agent implementing `cut` is exactly the one who needs
`cut`'s special case (`mark_safe` only when `arg != ";"`,
`defaultfilters.py:447-450`).

**Which value the body checks is settled by measurement, not by taste, and it is
not interchangeable.** On a plain `SafeString` the two sites agree —
`type(str(mark_safe("x")))` is `SafeString`, because `SafeString.__str__` returns
`self` — which is why nothing has ever caught the difference. They **diverge on a
lazy operand**, measured against the pinned tree:

```text
proxy = lazy(lambda: mark_safe("MiXeD"), str)()
type(proxy)                        : __proxy__
isinstance(proxy, SafeData)        : False     ← base.py:816 sees this
type(str(proxy))                   : SafeString
isinstance(str(proxy), SafeData)   : True      ← stringfilter sees this
```

So for `{{ lazy_safe|lower }}` the two sites decide **differently**, and the
body must check the **coerced** value, exactly as `defaultfilters.py:44-48` does.
A body that checked the incoming value would be right on every case anyone would
think to write and wrong on the only case where the distinction exists. `__proxy__`
is already on the wire from Task 6, so this is reachable, and Task 8's group
carries the case.

**D4 — M4 does not implement `conditional_escape` at all; it calls the host's.
This is a correction, and it removes the milestone-boundary problem rather than
managing it.** An earlier revision had M4 writing a five-line `conditional_escape`
in Rust — `value if is_safe(value) else escape(value)` — on the reasoning that
§5.1 puts `escape` in `template-core` while §10.3 gives M8 the safety protocol, so
M4 should take "the smallest possible piece". **The premise was wrong: there is no
small pure piece.** `django/utils/html.py:115-127` branches three ways —

```python
if isinstance(text, Promise):   # not an `is_safe` question
    text = str(text)
if hasattr(text, "__html__"):   # also not an `is_safe` question
    return text.__html__()
else:
    return escape(text)
```

— and the function's own docstring says the `__html__` convention is shared with
`markupsafe`, which implements it **without being `SafeData`**. So a Rust version
built from `is_safe` + `escape` is wrong for `markupsafe` objects, and wrong for
`Promise` — which is not hypothetical, since lazy `gettext` results are `Promise`s
and `__proxy__` is already on the wire from Task 6. Both branches are
Python-protocol questions, and asking them across the seam is what `FilterHost` is
for.

So M4 adds **no** `escape` module and reaches into M8 for nothing: it calls
`FilterHost::conditional_escape` and gets back a `Value`. §10.3's M8 row is
unchanged, and the `Promise`/`__html__`/`SafeText` protocol remains M8's to
implement on the Python side where it already lives. Eight filters depend on this
call — `escape`, `force_escape`, `escapeseq`, `join`, `unordered_list`,
`linenumbers`, `linebreaks`, `linebreaksbr` — which is why it is one host method
rather than a Rust helper duplicated eight times.

The one thing M4 *does* still own here is `escape` and `escapejs` themselves:
`html.escape` is a fixed five-replacement table and `escapejs` a fixed ~30-entry
`str.translate`, both pure, so Rust implements them and no host method is needed.
An earlier revision had them on the trait; the derivation in §D2 says otherwise.

**D5 — the corpus compares types, not only text.** A filter's result *type* is
part of its behaviour: `escape` returns `SafeString`, `safeseq` returns a list
*of* `SafeString`s, `divisibleby` returns a `bool`, `add` returns an `int`.
A corpus that compared `str(result)` would pass on all four while three of them
were wrong. So a corpus row compares `(type(result).__name__, result)` with
`SafeData` folded into the type name, and a second row-shape compares
`[type(x).__name__ for x in result]` for the list-returning filters. This is the
lesson M3's `describe` arms cost 205 subtests to learn, applied before the fact
rather than after.

**D6 — a filter's expectations are produced by a command, not remembered.**
`scripts/filter_probe.py` (Task 3) prints, for one filter name and one input,
Django's real result as a literal Rust expression. Every filter task's Step 1
runs it for its own filters and pastes the output into Step 2's test. This is
M3's "measured, not read" discipline given an executable form, and it is what
lets a plan covering 57 components stay honest without inlining 500 rows the
author would have to keep current by hand.

**D7 — the fan-out contract, restated for filters because the shared surface is
bigger than the tags' was.** §10.4's contract is that an agent owns one module,
writes no other file, runs `cargo test -p template-core`, never runs
`maturin develop`, and never commits. Filters add one failure mode that tags did
not have: **the seam is a trait**, and an agent whose filter needs an operation
`FilterHost` does not have is one line away from editing a shared file. So the
contract gains a sentence:

> If `FilterHost` is missing an operation your filter needs, **stop and report
> it**. Do not add it. The trait is written once, in full, like the registry, and
> adding a method touches the file every other agent is compiled against.

The same applies to `filters/mod.rs`, `filters/value.rs`, `filters/host.rs`,
`scripts/*` and `tests/*`. An agent's output is its `filters/<name>.rs` files and
a report naming, for each guard in its tests, the mutation it was observed
failing under.

**D7.1 — `&str` or `&Value` for a host parameter, which is a type-safety rule and
not a style choice.** `&str` is correct exactly when the value has already been
coerced: a parameter that arrives through `@stringfilter` is `str(first)` by the
time the body sees it, so the Python function cannot observe anything but a
string, and `&str` is the honest signature. `&Value` is required wherever the
Python function observes the argument's **type** rather than its text. The trait
above is written to that rule and was re-checked end to end for it; exactly one
parameter violated it, `sort_by_property`'s `prop`, and the measurement is:

```
dictsort(['abc', 'xyz'], 1)    ->  ['abc', 'xyz']
dictsort(['abc', 'xyz'], '1')  ->  ''
```

`_property_resolver` (`:540-575`) tries `float(arg)` first, so an **int** `1`
takes the `itemgetter` branch and a **string** `'1'` takes the
`split(".")`-then-subscript branch, where `['abc','xyz']['1']` raises `TypeError`
— caught by `dictsort`, returning `""`. Same digits, different type, different
answer; and `filter_tests/test_dictsort.py:93` uses the int literal, so a corpus
that only ever passes strings never sees the string branch at all. Every other
`&str` in the trait is `@stringfilter`-wrapped at the call site; `iri_to_uri(text:
&str)` behind `iriencode` (`:223-227`, whole body `return iri_to_uri(value)`) is
the case that looks wrong and is right.

---

## Task 1: Python's case mapping in `pyunicode`

**Files:**
- Create: `crates/template-core/src/pyunicode/case.rs`
- Create: `scripts/gen_python_case_fixups.py`
- Create: `tests/test_pyunicode_case_parity.py`
- Modify: `crates/template-core/src/pyunicode.rs` (add `pub mod case;` and re-export)

**Interfaces:**
- Consumes: nothing from earlier milestones.
- Produces: `template_core::pyunicode::case::upper(ch: char) -> impl Iterator<Item = char> + '_`, `upper_str(&str) -> String`, and `const UNCASED: [char; 28]`. **No `lower`.** The `lower` filter delegates (Task 4) and `slugify`'s `.lower()` goes through the host (Task 9), so a per-code-point `lower` would have no consumer — and it would be *silently wrong* for the strings that reach it, since Python's `str.lower()` applies Final_Sigma and a `char`-level table cannot. Shipping a function that exists, looks right, and is wrong at its obvious call site is worse than shipping none; the generator still measures the lower divergence and records it in the module doc as the reason there is no `lower`.

**Why this is a task and not a step.** It is the only part of M4 whose correctness
is a *table*, it has its own generator, and a reviewer could reasonably accept
the seam (Task 2) while rejecting this. It also has to land before `upper` can be
written at all, which is why it is Task 1 rather than part of the group it lives
in.

- [ ] **Step 1: Write the generator**

`scripts/gen_python_case_fixups.py`, modelled on the existing
`scripts/gen_python_word_ranges.py` (read it first; the file-format comment
there is the convention to follow). It must:

```python
#!/usr/bin/env python3
"""Regenerate the case-mapping fixup table from the pinned tree.

Rust's `char::to_uppercase` and Python's `str.upper` both apply the Unicode
default full case mappings, but they are built from different Unicode versions.
Measured against the pinned tree on 2026-10-08: Rust 1.98's tables are *ahead*
of CPython 3.14.4's (unicodedata 16.0.0) on 56 code points, 28 in each
direction, and in every case Python maps the code point to itself where Rust
maps it to another character.

Only the **upper** direction becomes a table. The lower direction is measured
anyway and written into the module doc as a comment: `str.lower()` also applies
Final_Sigma, so a per-code-point `lower` would be wrong for the strings that
reach it, and `pyunicode::case` deliberately does not provide one. See Task 4
and `FilterHost::str_lower`.

Run this after a `rustc` upgrade or a Django pin bump. It writes
`crates/template-core/src/pyunicode/case.rs`;
`tests/test_pyunicode_case_parity.py` is what fails loudly if you forget.
"""
```

It walks `0..=0x10FFFF`, skips surrogates, and for each code point where
`chr(c).upper() != rs_upper(chr(c))` emits the code point; it accumulates the
same comparison for lower into the doc comment's count.
**It must obtain Rust's mapping by running the built probe, not by reimplementing
it**: emit a temporary Rust file, compile it with `rustc`, run it to a temp file,
and diff. Reimplementing Rust's table in Python is the one thing that would make
the generator agree with a wrong answer.

The emitted module:

```rust
//! Python's **uppercase** mapping, which is not Rust's. **Generated** by
//! `scripts/gen_python_case_fixups.py` — do not edit by hand.
//!
//! …the measurement and the direction, as the generator's docstring has it…
//!
//! There is deliberately no `lower` here. The generator measures the lower
//! divergence too (28 code points, the same shape) and it is *not* a
//! counter-argument: `str.lower()` also applies Unicode's Final_Sigma, so
//! `"ΟΣ".lower()` is `"ος"` and a per-code-point `lower` would be wrong for
//! real strings. The `lower` filter and `slugify` go through
//! `FilterHost::str_lower` instead — see §D2's second metric and Task 4.

/// Code points whose **uppercase** mapping Python 3.14.4 (Unicode 16.0.0)
/// leaves as the identity where Rust's tables map them elsewhere.
///
/// Sorted, so a binary search is the lookup and a stray edit is visible.
pub const UNCASED: [char; 28] = [/* … */];
```

- [ ] **Step 2: Write the failing parity test**

`tests/test_pyunicode_case_parity.py`, in the shape of
`tests/test_pyregex_parity.py`. It imports `_native`, and compares
`_native.python_upper(ch)` against Python's own `chr(c).upper()` for **every**
code point, not a sample:

```python
class CaseMappingParityTests(unittest.TestCase):
    def test_every_code_point_matches_python(self):
        for cp in range(0x110000):
            if 0xD800 <= cp <= 0xDFFF:
                continue
            ch = chr(cp)
            self.assertEqual(
                _native.python_upper(ch), ch.upper(),
                f"upper differs at U+{cp:04X}",
            )

    def test_there_is_no_lower_accessor(self):
        # The closed half of the obligation. `lower` is delegated because
        # `str.lower()` is context-sensitive, and the guard against someone
        # helpfully adding a per-code-point `lower` back is that its only
        # consumers stay on the host side: Task 3's derived delegate set names
        # both of them.
        self.assertFalse(hasattr(_native, "python_lower"))
```

Run: `source scripts/env.sh && maturin develop && .venv/bin/python -m unittest tests.test_pyunicode_case_parity -v`
Expected: FAIL — `_native` has no `python_upper`.

- [ ] **Step 3: Write the Rust table and the accessors**

Run `python scripts/gen_python_case_fixups.py` and commit its output. Then the
accessors in `case.rs`:

```rust
pub fn upper(ch: char) -> impl Iterator<Item = char> + '_ {
    let mapped = if UNCASED.binary_search(&ch).is_ok() {
        // Python leaves this one alone; Rust would map it. Yield the code point
        // itself, and only it — a `char`'s own iterator is `once`-like but the
        // *Rust* mapping may be multi-character (`ß` → `SS`), so the two arms
        // have different lengths and that asymmetry is the point.
        CaseMap::Identity(ch)
    } else {
        CaseMap::Rust(ch.to_uppercase())
    };
    mapped
}
```

Name the enum (do not nest a module), and give `upper_str` a single `String`
allocation. Then add `pub mod case;` to `pyunicode.rs` and one `#[pyfunction]`
(`python_upper`) to `crates/template-py/src/lib.rs`, returning `String`. **Not
`python_lower`** — see the Interfaces block above.

- [ ] **Step 4: Run the parity test to verify it passes**

Run: `maturin develop && .venv/bin/python -m unittest tests.test_pyunicode_case_parity -v`
Expected: PASS, 2 tests, the first over 1,112,064 code points.

- [ ] **Step 5: Observe the guard failing**

Delete `U+A7D3` from `UNCASED`, `cargo test -p template-core` and
`maturin develop`, and re-run the parity test. Expected:
`upper differs at U+A7D3` — `U+A7D2` against `U+A7D3`. Restore.

Then observe a *different* failure: the test must be sensitive to the Rust table
moving. Change one `UNCASED` entry to a code point Rust already maps to itself
(e.g. `U+0041`), rebuild, and confirm the test still passes — **that is the wrong
result and it is the point of the step**: the table is a *suppression* list, so a
stray entry is harmless and the test cannot catch it. Record that in the test's
docstring, and add an assertion that `UNCASED` is exactly the generated set, by
count and by hash of its contents, so an edit that changes its meaning without
changing its length is still caught.

Then the second guard: add a `python_lower` `#[pyfunction]` returning `String`,
rebuild, and confirm `test_there_is_no_lower_accessor` fails. That is the
observation that the closed half of the obligation is live; delete it again.

- [ ] **Step 6: Run the gate and commit**

```bash
source scripts/env.sh && bash scripts/check.sh
git add crates/template-core/src/pyunicode crates/template-core/src/pyunicode.rs \
        crates/template-py/src/lib.rs scripts/gen_python_case_fixups.py \
        tests/test_pyunicode_case_parity.py
git commit -m "pyunicode: Python's uppercase mapping, which is not Rust's"
```

---

## Task 2: `Value`, `FilterFlags`, `FilterHost`, and the registry in full

**Files:**
- Create: `crates/template-core/src/filters/value.rs`
- Create: `crates/template-core/src/filters/host.rs`
- Create: `crates/template-core/src/filters/escape.rs` (the `conditional_escape` of §D4)
- Modify: `crates/template-core/src/filters/mod.rs` (rewrite: the registry, `filter_names`, `filter`)
- Create: 57 files under `crates/template-core/src/filters/`, one per filter, each a stub
- Modify: `crates/template-core/src/lib.rs` (`pub mod filters;`)

**Interfaces:**
- Consumes: `pynum::IntValue` (M3), `pyunicode::case` (Task 1).
- Produces — and **these names are the contract every later task codes against**:

```rust
pub struct FilterFlags {
    pub is_safe: bool,
    pub needs_autoescape: bool,
    pub expects_localtime: bool,
}

pub type ValueId = u32;

pub enum Value { /* §D1 */ }

pub struct FilterCall<'a> {
    pub host: &'a dyn FilterHost,
    pub autoescape: bool,
    /// The context's `use_tz`, for the two `expects_localtime` filters.
    pub use_tz: bool,
}

pub type FilterFn = fn(Value, Option<Value>, &FilterCall<'_>) -> Result<Value, Error>;

pub struct FilterEntry {
    pub name: &'static str,
    pub flags: FilterFlags,
    pub run: FilterFn,
}

pub fn filter(name: &str) -> Option<&'static FilterEntry>;
pub fn filter_names() -> Vec<&'static str>;
```

- `FilterHost` is declared in full in this task and is not edited afterwards. Its
  methods, grouped by what they are. **This block is the revision that followed
  M4's first review round**, which found five filters with no expressible method;
  the derivations are quoted in "What the first review round changed" below.

```rust
pub trait FilterHost {
    // --- the Python value protocol -----------------------------------------
    fn to_str(&self, value: &Value) -> Result<String, Error>;              // str(v)
    fn to_int(&self, value: &Value) -> Result<IntValue, Error>;            // int(v)
    fn to_float(&self, value: &Value) -> Result<f64, Error>;               // float(v)
    fn is_true(&self, value: &Value) -> Result<bool, Error>;               // bool(v)
    fn len(&self, value: &Value) -> Result<usize, Error>;                  // len(v)
    fn iter_values(&self, value: &Value) -> Result<Vec<Value>, Error>;     // list(v)
    fn get_index(&self, value: &Value, index: i64) -> Result<Value, Error>;
    /// `value[start:stop:step]` — `slice_filter` (`defaultfilters.py:651-666`).
    /// Not expressible as `get_index` plus Rust slicing: `"abcdef"|slice:":3"`
    /// is `"abc"`, a *string*, where the index route yields a list. Every
    /// component is `Option` because `"::2"` and `":3"` are both legal and
    /// Django passes `None` through to `slice(*bits)`.
    fn get_slice(
        &self,
        value: &Value,
        start: Option<i64>,
        stop: Option<i64>,
        step: Option<i64>,
    ) -> Result<Value, Error>;
    fn add_values(&self, a: &Value, b: &Value) -> Result<Value, Error>;    // a + b
    fn percent_format(&self, value: &Value, spec: &str) -> Result<Value, Error>;
    fn is_safe(&self, value: &Value) -> bool;
    fn mark_safe(&self, value: Value) -> Value;
    fn repr(&self, value: &Value) -> String;

    // --- the escaping protocol: one call, four branches ---------------------
    /// `conditional_escape` (`django/utils/html.py:115-127`) — see §D4. One
    /// method, because two of its three branches are not `is_safe` questions:
    /// `isinstance(text, Promise)` and `hasattr(text, "__html__")`.
    fn conditional_escape(&self, value: &Value) -> Result<Value, Error>;

    // --- the one ordering operation Rust cannot express ---------------------
    /// `dictsort`/`dictsortreversed` (`:578-600`) together with
    /// `_property_resolver` (`:540-575`). One method, not two: the
    /// float-vs-string branch, the private-name `AttributeError`, the
    /// five-exception `__getitem__`→`getattr` fallback and `dictsort`'s own
    /// `(AttributeError, TypeError)`→`""` catch are a single semantic unit, and
    /// splitting them across the FFI would mean Rust reimplementing Python's
    /// exception classification — the thing this seam exists to avoid. Sorting
    /// arbitrary `Opaque` objects by an extracted key is not expressible in Rust
    /// at all.
    ///
    /// `prop` is `&Value` and not `&str`, per **§D7.1**: `arg` is *not*
    /// `@stringfilter`-coerced, and `float(arg)` sends `1` and `'1'` down
    /// different branches (`dictsort(x, 1)` is the list, `dictsort(x, '1')` is
    /// `""`). Coercing here would silently pick one branch for both.
    fn sort_by_property(
        &self, value: &Value, prop: &Value, reverse: bool,
    ) -> Result<Value, Error>;

    // --- CPython string-method semantics ------------------------------------
    /// `str.lower()`. **Not a per-code-point table**, which is this project's
    /// fifth Unicode-table finding after `\w`, whitespace, `combining` and the
    /// case fixup. Measured: `"ΟΣ".lower()` is `"ος"`, not `"οσ"` — Unicode's
    /// **Final_Sigma** rule, which is *context*-dependent, so Task 1's
    /// exhaustive per-code-point diff provably cannot see it. Rust's
    /// `str::to_lowercase` implements the rule, but the fixup table needs
    /// `char::to_lowercase` per code point, and the two cannot be combined
    /// without either replicating Final_Sigma (guardable only by sampling) or
    /// leaving a silent divergence on strings containing both a suppressed code
    /// point and a context-sensitive Σ. Delegating is complete by construction.
    fn str_lower(&self, text: &str) -> String;
    /// `str.title()`. Needs three things Rust has no equivalent of: Unicode's
    /// `Cased` property (4,578 code points, and `unicodedata` does not expose
    /// it), a **135-entry** titlecase mapping table distinct from uppercase, and
    /// the same Final_Sigma context rule when lowercasing the remainder of each
    /// run. Measured: a first-principles rule reproduced all 1,112,064 single
    /// code points and still got 444 of 400,000 random strings wrong, every one
    /// of them final sigma.
    fn str_title(&self, text: &str) -> String;

    // --- engines whose state is not the filter's argument -------------------
    /// `django.utils.html.strip_tags` — `HTMLParser`-based (`html.py:238-272`).
    fn strip_tags(&self, text: &str) -> String;
    /// `django.utils.text.slugify` — reaches `unicodedata` NFKD. Rust owns the
    /// two regexes and their order; the normalisation is the host's.
    fn slugify(&self, text: &str) -> String;
    fn unicode_normalize_nfkd(&self, text: &str) -> String;
    /// `django.utils.html.urlize` — regex over `settings.URLIZE_ASSUME_HTTPS`.
    fn urlize(&self, text: &str, limit: Option<usize>, autoescape: bool) -> String;
    /// `Truncator.chars` / `.words` (`text.py:193-261`), **whole**. Delegated
    /// rather than split because even the non-html path needs
    /// `unicodedata.normalize("NFC")`, `unicodedata.combining` per character
    /// (`:205`, `:220`) and the translatable marker `add_truncation_text` falls
    /// back to — so a Rust `chars` would need a **third** Unicode table
    /// diverging from CPython's, which is the M1 `\w` problem reintroduced.
    /// `truncate_text` is Django's `truncate` argument; `None` is its default.
    fn truncate(
        &self,
        text: &str,
        length: usize,
        words: bool,
        html: bool,
        truncate_text: Option<&str>,
    ) -> String;
    /// `django.utils.text.wrap` — `textwrap.TextWrapper`.
    fn wrap(&self, text: &str, width: usize) -> String;
    /// `django.utils.encoding.iri_to_uri`. `iriencode`'s entire body is this
    /// one call (`defaultfilters.py:225-227`).
    fn iri_to_uri(&self, text: &str) -> String;
    /// `urllib.parse.quote` — `urlencode`'s body, with the `safe` set Rust
    /// selects (`:365-380`).
    fn quote(&self, text: &str, safe: &str) -> String;
    fn json_dumps(&self, value: &Value) -> Result<String, Error>;
    fn pformat(&self, value: &Value) -> Result<String, Error>;
    fn random_choice(&self, value: &Value) -> Result<Value, Error>;

    // --- locale, language and the clock -------------------------------------
    fn localtime(&self, value: Value, use_tz: bool) -> Result<Value, Error>;
    fn date_format(&self, value: &Value, format: &str) -> Result<String, Error>;
    fn time_format(&self, value: &Value, format: &str) -> Result<String, Error>;
    fn timesince(&self, then: &Value, now: &Value) -> String;
    fn number_format(&self, args: NumberFormatArgs<'_>) -> String;
    fn gettext(&self, text: &str) -> String;
    fn ngettext(&self, singular: &str, plural: &str, n: u64) -> String;
    fn now(&self) -> Result<Value, Error>;
}
```

**`escape` and `escapejs` are deliberately *not* on this trait.** Both are pure —
`django.utils.html.escape` is `SafeString(html.escape(str(text)))`, and `escapejs`
is `mark_safe(str(value).translate(_js_escapes))` over a fixed ~30-entry table — so
Rust owns them, which is what the derived 18 already said (§D2). An earlier
revision of this plan listed both as host methods, contradicting its own
derivation; the review caught the trait rather than the derivation, which is a
useful reminder that a hand-written list and a computed one drift apart.

**The trait carries a Python exception taxonomy, and that is not decoration.**
Filters catch *specific* classes and let the rest escape, so "did it raise, and
with what class" is part of each body's observable behaviour, and a corpus that
flattens it to one error type cannot pin the filters that depend on it:

```text
first(5)          → TypeError escapes the filter    (:602-608 catches only IndexError)
first([])         → IndexError → ""                 (the same three lines)
divisibleby(x, 0) → ZeroDivisionError escapes       (:854-858 has no try at all)
add("a", "b")     → bare `except Exception` → ""    (:745-755)
get_digit(...)    → ValueError → the original value (:757-776)
```

So the value-protocol methods return
`Error::Host { kind: PyErrorKind, message, operation }`, with `PyErrorKind` ∈
`{AttributeError, IndexError, KeyError, TypeError, ValueError, ZeroDivisionError}`,
and a body matches on `kind` rather than on a message. Task 6's `first(5)` row
asserts the *escape*, not a value, and that is only expressible if the class
survives the seam.

**What the first review round changed, and why each is a group that would have
stalled.** All five were reported from Django's source and all five were then
re-checked against `.django` @ 6.0.9 before being accepted, because a
plausible-sounding review is exactly where an unmeasured claim propagates:

| # | filter(s) | the gap, verified | the fix |
|---|---|---|---|
| 1 | `slice` | `:651-666` is `value[slice(*bits)]`; `"abcdef"\|slice:":3"` is `"abc"`, a **string**, so `get_index` + Rust slicing returns the wrong *type* | `get_slice` |
| 2 | `dictsort`, `dictsortreversed` | `:558-573` subscripts with a **string** key (`value = value[part]`) before falling back to `getattr`, across five exception classes; `get_index(i64)` cannot, and `sorted(value, key=…)` over `Opaque` is inexpressible | `sort_by_property` (one method, not two) |
| 3 | `iriencode` | `:225-227` — the body is literally `return iri_to_uri(value)`, and no such method existed despite the filter being in the derived 18 | `iri_to_uri` |
| 4 | the four `Truncator` filters | `Truncator.chars` calls `unicodedata.combining` per character (`text.py:220`) so combining marks do not count toward the length; answering that in Rust needs a third Unicode table | `truncate`, whole — better than the `unicode_combining` method the review offered, because it removes the table *and* the `gettext` marker question in one move |
| 5 | `escape`, `force_escape`, `escapeseq`, `join`, `unordered_list`, `linenumbers`, `linebreaks`, `linebreaksbr` | `conditional_escape` (`html.py:115-127`) branches on `isinstance(text, Promise)` and `hasattr(text, "__html__")`, neither of which is `is_safe` — its own docstring says the convention is shared with `markupsafe`, which implements `__html__` **without being `SafeData`** | `conditional_escape`, whole (see §D4) |

The fifth is the one that changes a milestone boundary, and it changes it in the
direction of *removing* a reach rather than taking one: there is no small pure
piece of `conditional_escape` to implement in Rust, because two of its three
branches are Python-protocol questions. §D4 now says so.

**Two design questions the review raised and asked to be decided rather than
inherited:**

*§D1's `PartialEq`.* Python's `SafeString("x") == "x"` is `True`, while a
derived Rust `PartialEq` makes `Value::Safe("x") != Value::Str("x")`. **The plan
keeps the derived, structural equality, deliberately, and disagrees with the
direction the review implied** — the asymmetry that matters runs the other way.
Every fan-out agent's unit tests compare a result against an expected `Value`, and
there a folded equality would make a *dropped safety marker invisible*:
`Value::Str("x")` would satisfy an assertion written as `Value::Safe("x")`.
Structural equality is what makes §D1's "the marker cannot be dropped separately
from the text" testable at all. So the guard
`Value::Safe("x") != Value::Str("x")` is added with that reasoning in its doc.

**The cost of that choice is real and lands on exactly one filter, so the rule
that contains it is stated rather than left to the reader.** Measured with an AST
walk: four of the 57 bodies use `==`/`!=` — `divisibleby` (`int(value) %
int(arg) == 0`), `floatformat` (`last_char == "g"`), `pluralize` (`float(value)
== 1`, `len(value) == 1`) and `cut` (`arg != ";"`). The first three compare
*converted primitives* and are unaffected. **`cut` compares a `Value` against a
literal**, and under structural equality `Value::Safe(";") != Value::Str(";")` is
true where Python's `SafeString(";") != ";"` is false — which flips `cut`'s
`mark_safe` branch (`:447-450`) for a safe `";"` argument. So the rule is:

> A filter body compares **converted primitives** — `String`, `IntValue`, `f64`,
> `char` — never two `Value`s. `Value`'s `PartialEq` is for tests, not for bodies.

`cut` therefore tests `to_str(arg)? != ";"`, which is `str.__ne__` and therefore
`SafeString`-faithful by construction. Task 4 carries that as a named case. This
is the one place where the review's question ("does any body test equality?") had
a yes hiding behind a no, and it took an AST walk rather than a reading to see it.

*The group split.* Left as 8/8/9/10/12/6/4. The review's own point is that
sizing is downstream of the surface: three of the five gaps landed in the
escaping group, but they landed on the *trait*, and with `conditional_escape`
whole that group is eight calls to one host method rather than a group with
missing plumbing. Re-derive after Task 2 lands, not now.

**Declared environment.** `template-core` at this point has `lexer`, `parser`
(with `desc`, `error`, `host`, `kwargs`, `registry`, `resolve`, `smartif`,
`tags/`, `variable`, `filter_expr`), `pynum`, `pyregex`, `pyunicode`, `text`,
`token`, and `filters/` holding only `filter_names()`. There is **no** `escape`
module and no `nodes` module. `NoHost` implements only `Host`; this task adds
`NoFilterHost` for the `cargo test` unit tests, whose every value-protocol method
returns `Err(Error::host(...))` naming the operation, so a filter that needs the
host cannot pass a bare-shell test by accident.

- [ ] **Step 1: Write `value.rs`**

`Value` as §D1 specifies, with a hand-written `Debug` (the `filters/mod.rs`
precedent is `Step::Found`, which prints the handle rather than the container) and
`PartialEq` deriving structurally. Add a `pub fn type_name(&self) -> &'static str`
returning Python's name (`"NoneType"`, `"bool"`, `"int"`, `"float"`, `"str"`,
`"list"`), which the corpus's §D5 type comparison uses.

- [ ] **Step 2: Write `host.rs`**

The trait above, plus `NoFilterHost`. Each method's doc comment carries the
Django call site it stands for and the reason it is on the host rather than in
Rust — §D2's rule is the reason, and the *specific* reason is `settings`, the
active language, or the clock. `clippy::type_complexity` has bitten this project;
name the `number_format` parameter list with a struct if it warns.

- [ ] **Step 3: Write the 57 stubs**

`crates/template-core/src/filters/mod.rs` declares all 57 modules and the
`REGISTRY` table of 57 entries, ascending by name, each with its flags — the
measured values, which are not guessed:

| flag | value | filters |
|---|---|---|
| `is_safe` | `true` (36) | addslashes, capfirst, center, escape, escapeseq, filesizeformat, floatformat, force_escape, iriencode, join, json_script, last, linebreaks, linebreaksbr, linenumbers, ljust, lower, phone2numeric, pprint, random, rjust, safe, safeseq, slice, slugify, stringformat, striptags, title, truncatechars, truncatechars_html, truncatewords, truncatewords_html, unordered_list, urlize, urlizetrunc, wordwrap |
| `is_safe` | `false` (19) | add, date, default, default_if_none, dictsort, dictsortreversed, divisibleby, first, get_digit, length, make_list, pluralize, time, timesince, timeuntil, upper, urlencode, wordcount, yesno |
| `is_safe` | **absent** (2) | `cut`, `escapejs` |
| `needs_autoescape` | `true` (7) | join, linebreaks, linebreaksbr, linenumbers, unordered_list, urlize, urlizetrunc |
| `expects_localtime` | `true` (2) | date, time |

`cut` and `escapejs` are measured `hasattr`-absent, not `False`; `base.py:816` and
`stringfilter`'s line 48 both read `getattr(..., "is_safe", False)`, so absent
*behaves* as false and `FilterFlags`'s `bool` is the right model — but the
inventory test asserts the absence separately, because a Rust table cannot
distinguish them and a future reader will otherwise assume Django's attribute is
always present. `do_not_call_in_templates` and `alters_data` are measured absent
on all 57.

Each stub file is the same shape:

```rust
//! `upper` — `defaultfilters.py:358-362`.
//!
//! Stub. §10.3's M4 row owns this filter and Task 4 implements it; an entry with
//! a stub body rather than a missing registry row is what makes the milestone's
//! progress visible and keeps the registry a file no fan-out agent edits.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    Err(Error::unimplemented_filter("upper"))
}
```

Add `Error::unimplemented_filter` to `parser/error.rs` alongside the existing
`Error::host`, with the same `#[cfg(test)]`-visible kind, and a test in
`error.rs` that the message names the filter *and* the milestone that owns it —
the `UnimplementedTag` precedent.

- [ ] **Step 4: Write the `m3_owns_no_filters` replacement**

`filters/mod.rs`'s current test asserts `filter_names().is_empty()`. Replace it
with `the_registry_is_the_measured_57`, asserting the exact sorted name list
against a literal, plus `every_registry_entry_is_a_stub_at_m4` asserting that
calling all 57 `run`s returns `Error::unimplemented_filter`. That second test is
what makes each group's Task 4–10 deliverable *visible*: it is edited by the
central commit as each group lands, and its failure message names the filters
still stubbed.

- [ ] **Step 5: Observe both guards failing**

(a) Delete `("upper", …)` from `REGISTRY`. Expected:
`the_registry_is_the_measured_57` fails naming `upper`. (b) Implement `upper`'s
body in place of the stub. Expected: `every_registry_entry_is_a_stub_at_m4`
fails naming `upper`. Restore both.

- [ ] **Step 6: Run the gate and commit**

This is the **design gate**: the reviewer rules on `value.rs`, `host.rs` and the
registry before any fan-out begins.

```bash
source scripts/env.sh && cargo fmt --all && cargo clippy --workspace --all-targets --quiet -- -D warnings \
  && bash scripts/check.sh
git commit -m "filters: the value model, the host seam and the registry in full"
```

---

## Task 3: the FFI surface, the corpus harness and the inventory test

**Files:**
- Modify: `crates/template-py/src/lib.rs` (`filter_names`, `filter_flags`, `filter`)
- Create: `crates/template-py/src/filters.rs` (the `FilterHost` implementation and the value interner)
- Create: `scripts/filter_probe.py`
- Create: `tests/filter_cases.py`
- Create: `tests/test_filter_parity.py`
- Create: `tests/test_filter_inventory.py`

**Interfaces:**
- Consumes: Task 2's `Value`, `FilterFlags`, `FilterHost`, `filter`.
- Produces: `_native.filter(name: str, value: Any, arg: Any = None, autoescape: bool = True, use_tz: bool = True) -> Any`; `_native.filter_names() -> list[str]`; `_native.filter_flags(name: str) -> tuple[bool, bool, bool]`.

**Declared environment.** The shell's `_impl/` has `exceptions.py` and
`parser.py`. `_shim.py`'s rule applies: **no `_impl` file may take the name of a
real `django.template` module** — `django/template/defaultfilters.py` exists, so
an `_impl/defaultfilters.py` is forbidden; this task adds no shell module at all.

- [ ] **Step 1: Write the failing inventory test first**

`tests/test_filter_inventory.py`, modelled directly on `tests/test_tag_inventory.py`
(read it — its docstring is the template for why this file exists). It pins, from
Django's live registry:

```python
REGISTERED = { ... the measured 57 ... }
REGISTRY_NAMES_THAT_ARE_NOT_DEF_NAMES = {
    "escape": "escape_filter", "escapejs": "escapejs_filter",
    "linebreaks": "linebreaks_filter", "phone2numeric": "phone2numeric_filter",
    "slice": "slice_filter", "timesince": "timesince_filter",
    "timeuntil": "timeuntil_filter",
}
DEF_NAMES_THAT_ARE_NOT_REGISTRY_NAMES = set(REGISTRY_NAMES_THAT_ARE_NOT_DEF_NAMES.values()) | {
    "_property_resolver", "stringfilter",
}
STRINGFILTER = { ... the measured 29 ... }
```

and asserts the three-way relationship against `ast` over
`defaultfilters.py` (59 top-level `def`s), against
`defaultfilters.register.filters`, against `_native.filter_names()`, against
`_native.filter_flags(name)` for all three flags, and against `hasattr` for
`cut`/`escapejs`'s absent `is_safe`. It also asserts `defaultfilters.escape is not
defaultfilters.register.filters["escape"]`, because that is the trap the
inventory found and it is free to pin.

- [ ] **Step 1b: Derive the delegation set, do not assert a list of 20**

The same file carries `DELEGATING_FILTERS` and the test that **computes** it, in
the shape `test_tag_inventory.py` uses for 27. The reviewer's brief for M4 was
explicit that the deliverable is not the number but the test that says the number,
because this project has had seven count disputes and every one was two right
answers to two unstated questions. **Two metrics, because the first one measures
references to `django.utils` and cannot see a filter built on a CPython string
method** — the second is what found `lower` and `title`. They are computed and
asserted separately, so the failure message names which filter moved:

```python
def delegating_filters_by_utils():
    """Filters whose body's answer comes from a django.utils ENGINE — 18.

    The metric, written down because three neighbouring ones are each correct
    for a different question: 57 filters are registered; 31 reference *any*
    django.utils name (that sweeps in `mark_safe`, a primitive we reimplement
    as Value::Safe); 26 reference a utility in a module that imports something
    stateful (that wrongly sweeps in escape/join/escapeseq/force_escape/
    linenumbers/linebreaks, because strip_tags shares django/utils/html.py with
    them). A filter delegates iff the utility its body references bottoms out,
    within that utility's own module, in state Rust cannot hold (settings,
    locale, the active language, the clock) or a stdlib table-or-parser port.

    Classification is per-UTILITY, not per-module. Two resolver traps are
    load-bearing and both were hit while measuring: `from django.utils import
    formats` binds a MODULE, so looking up "formats" inside formats.py finds
    nothing (the fix is `module_path(f"{m}.{n}")`); and `gettext` resolves
    through a package __init__ that rebinds it, so a name lookup bottoms out
    empty and falsely reads as pure.
    """

def delegating_filters_by_string_method():
    """Filters calling a `str` method whose result is not a function of the
    code point alone — 2, `lower` and `title`.

    Found by walking each filter body's AST for `.lower()`/`.title()`/`.upper()`
    across the pinned tree, then comparing what each one costs: `str.upper()`
    and `str.title()`'s *uppercase* half are per-code-point (Task 1's table);
    `str.lower()` is Final_Sigma, so `"ΟΣ".lower()` is `"ος"` and no
    per-code-point table can express it. `str.title()`'s lowercasing of each
    run's remainder is Final_Sigma too. So the two `.lower()`-family callers
    delegate, and Task 1's per-code-point table is *not* their answer — there is
    deliberately no `lower` in `pyunicode::case` for them to reach for.

    This is the metric that moved the set from 18 to 20 after the plan's first
    review, which is why the two are counted separately rather than merged into
    a hand-checked list of 20.
    """
```

`DELEGATING_FILTERS` is the union of the two, asserted to equal the literal
**20**; each half is asserted separately against its own literal (18 and 2), and
the failure message prints the computed set plus both halves, so a future drift
reports "19 by utils, 2 by string method" rather than just red. It also asserts
that each delegating filter's `FilterHost` method is actually reached by the
corpus, so a filter cannot be reclassified into or out of the set by editing one
line.

Run: `.venv/bin/python -m unittest tests.test_filter_inventory -v`
Expected: FAIL — `_native` has no `filter_names`. Both derivations are Python-side
and already run, and they print their counts (`utils=18`, `string_method=2`,
`union=20`) before the attribute error, which is what makes the numbers visible
while the Rust side is still a stub.

- [ ] **Step 2: Write the FFI**

`crates/template-py/src/filters.rs` holds the Python-side `FilterHost` and the
value interner: a `Vec<Py<PyAny>>` owned by the call, `value_from_py` classifying
`None`/`bool`/`int`/`float`/`str`/`SafeString`/`list` natively and everything else
into the table, and `value_to_py` going back — with `Value::Safe` producing a real
`mark_safe`d string through the same import `literal_to_py` uses (`lib.rs:1149`),
not the `SafeStr` wire class.

`_native.filter` wraps the call in the one thing `FilterExpression.resolve`'s loop
does that is not the filter's own job (`base.py:803-819`), in this order:

```text
arg values are resolved by the caller and passed in
if flags.expects_localtime: value = host.localtime(value, use_tz)
result = run(value, arg, call)
if flags.is_safe and is_safe(the pre-filter value): result = mark_safe(result)
```

**The `mark_safe` here is not the whole story and the comment must say so**: it is
`base.py:816`'s half. The `@stringfilter` wrapper's half is inside the filter
(§D3). Writing this loop in the FFI now, rather than at M9 in Rust, is deliberate
— it is the thing the corpus is testing, and a corpus that skipped it would prove
parity of the bodies while leaving the flags unexercised, which is exactly what
§10.3's "incl. `FilterFlags` round-tripping" is asking for.

- [ ] **Step 3: Write `scripts/filter_probe.py`**

An oracle, not a test. `python scripts/filter_probe.py <filter> <value-literal>
[<arg-literal>]` prints Django's real result and the Rust literal for it:

```python
# usage: filter_probe.py upper "'a'"      ->  ("str", "A")
#        filter_probe.py add 3 4          ->  ("int", 7)
```

It must go through `FilterExpression.resolve`'s loop, not by calling
`register.filters[name]` directly, or it reports the body's answer without the
flags — the same mistake the corpus exists to avoid. Build a real
`Engine`/`Context` and a one-filter `FilterExpression`, and print both the type
name and the value.

- [ ] **Step 4: Write the corpus and the parity test**

`tests/filter_cases.py` holds `CASES`, a list of
`{"name": str, "filter": str, "value": …, "arg": …}`. `tests/test_filter_parity.py`
renders each side — Django's `FilterExpression.resolve` over a real `Context`, and
`_native.filter` over the same input — and compares `(type_name, value)` per §D5,
with a second comparison of `[type(x).__name__ for x in value]` when the result is
a list.

Two tests guard the corpus's own completeness, in the shape M3's generator-floor
test used:

- `every_implemented_filter_has_cases` — the set of filter names in `CASES`
  equals the set of filters whose `run` is no longer a stub, so a filter cannot
  be implemented without cases or have cases without an implementation;
- `every_stub_is_named` — the stub set is asserted by name, so implementing one
  is a deliberate edit to two files rather than a silent shrink.

At Task 3 the corpus holds `upper` and `lower` only; §D6 says the rest arrive with
their groups.

- [ ] **Step 5: Run the gate and commit**

```bash
source scripts/env.sh && maturin develop && bash scripts/check.sh
git commit -m "filters: the FFI surface, the corpus harness and the inventory test"
```

---

## Tasks 4–10: the 57 filter bodies

**These seven tasks are one shape, so it is written once and then instantiated.**
Each is a fan-out dispatch under §D7 and each ends with a central rebuild, the
central corpus addition, and one commit.

**The split was re-derived after the case-method finding, and it holds.** The
sizing input is not the filter count per task but **how many bodies are Rust's**
— a delegating filter is a thin wrapper, its flags and its corpus rows, while a
pure one is the work. Computed against §D2's 20 (the union of both metrics, not a
hand-checked list):

| task | filters | delegated | **Rust-owned** |
|---|---|---|---|
| 4 case and simple text | 8 | `lower`, `title` | **6** |
| 5 padding and truncation | 8 | the four `truncate*`, `wordwrap` | **3** |
| 6 sequences | 9 | — | **9** |
| 7 numbers and logic | 10 | `yesno`, `floatformat`, `filesizeformat` | **7** |
| 8 escaping and safety | 12 | `striptags`, `iriencode` | **10** |
| 9 URLs and markup | 6 | `urlize`, `urlizetrunc`, `json_script`, `slugify` | **2** |
| 10 dates and elapsed time | 4 | all four | **0** |

Task 4 is the only group whose composition the finding changed — it went from
eight ports to six plus two host calls — and the change *strengthens* it: it is
still the group that proves the seam end-to-end, and now it does so on the same
filters that used to be pure, with `"ΟΣ"` and `"ΟΣΑ"` side by side on one row.
The remaining asymmetry between the groups is deliberate and is not a sizing
error: Tasks 9 and 10 are nearly all delegation, but their cost is the corpus
machinery, not the bodies — two settings states for `urlize`'s
`URLIZE_ASSUME_HTTPS`, a `RemovedInDjango2028Warning` to decide about, a
time-dependent row that must be a property rather than an equality, and
`floatformat`'s thread-local decimal context. And `floatformat` — 128 lines, the
longest body in M4 — is dispatched as its **own unit inside Task 7 and reviewed
alone**, which is what "worth its own review round within the group" below means
concretely.

### The shared task shape

- [ ] **Step 1: Measure.** For each filter in the group, run
  `python scripts/filter_probe.py <name> <value> [<arg>]` over the group's input
  list and record the printed `(type, value)` pairs. **Do not predict a row.**
  M2 and M3 between them lost eight plan rows to predictions nobody had run.
- [ ] **Step 2: Write the Rust unit tests** in each `filters/<name>.rs`'s
  `#[cfg(test)] mod tests`, using `NoFilterHost` for the pure cases and a
  `TestFilterHost` (added centrally to `filters/host.rs` **before** the group is
  dispatched) for the ones that need the seam. Each test asserts the measured
  rows literally.
- [ ] **Step 3: Run them and watch them fail** —
  `cargo test -p template-core`. Expected: FAIL, `Error::unimplemented_filter`.
- [ ] **Step 4: Implement the body**, against the Django source quoted in the
  task below, with the call site's line numbers in the module doc.
- [ ] **Step 5: Observe each guard failing for its intended reason** (§11), by
  mutation, and record the mutation and the failure message in the task's report.
  A filter with no interesting guard says so; it does not claim one.
- [ ] **Step 6: Run `cargo test -p template-core` and `cargo clippy -p template-core
  --all-targets -- -D warnings`.** Both green.
- [ ] **Step 7: Report back** — files written, guards observed failing, and any
  `FilterHost` operation found missing (**stop; do not add it**, §D7).
- [ ] **Step 8: Central**: `maturin develop`, paste the group's rows into
  `tests/filter_cases.py`, shrink the stub assertion in
  `tests/test_filter_inventory.py`, run `bash scripts/check.sh`, commit.

---

### Task 4: case and simple text — 8 filters

`upper`, `lower`, `capfirst`, `title`, `cut`, `addslashes`, `make_list`, `wordcount`

The group that Task 1 unblocks, and the one that proves the seam end-to-end.

- `upper` (`:358-362`) — `@stringfilter`, `is_safe=False`, body is
  `pyunicode::case::upper_str`. **The parity test is Task 1's, over all code
  points; this task's tests are the three interesting shapes** — `"ß"` (Rust and
  Python both give `SS`), `U+A7D3` (Python gives itself, Rust gives `U+A7D2`),
  and a non-`str` input (`upper(5)` is `"5"`, because `@stringfilter` coerces).
- `lower` (`:247-252`) — `@stringfilter`, `is_safe=False`, body is **the host's
  `str_lower`** — not `pyunicode::case`, because there is no per-code-point
  `lower` to call (Task 1). `str.lower()` is
  context-sensitive: `"ΟΣ"|lower` is `"ος"` (Final_Sigma), and no per-code-point
  table can express that. **Put the three context shapes in the corpus** —
  `"ΟΣ"` → `"ος"`, `"ΟΣΑ"` → `"οσα"`, `"Σ1"` → `"σ1"` — because they differ only
  by context and a per-character implementation gets two of the three right by
  accident.
- `capfirst` (`:71-76`) — `value and value[0].upper() + value[1:]`. The empty
  string returns itself, not `""`-built-from-nothing; `value[0]` is a **code
  point**, so `capfirst("ßx")` is `"SSx"` and the string grows by one character.
  Measured, not derived.
- `title` (`:297-303`) — `@stringfilter`, `is_safe=True`, body is **the host's
  `str_title`**, followed by two Rust regexes:
  `re.sub("([a-z])'([A-Z])", lambda m: m[0].lower(), …)` then
  `re.sub(r"\d([A-Z])", lambda m: m[0].lower(), …)`, each lowercasing the whole
  match. An earlier revision of this plan called `title` "the trap of this
  group" and planned to port Python's rule over `pyunicode::case`; measurement
  killed that. The rule needs Unicode's `Cased` property (4,578 code points), a
  **135-entry** titlecase table distinct from uppercase, and Final_Sigma when
  lowercasing each run's remainder. A from-first-principles version reproduced
  every one of the 1,112,064 single code points and still failed **444 of
  400,000** random strings, every failure a final sigma. So it is a host call,
  and Task 1's `is_cased` helper is **not** written.
- `cut` (`:441-450`) — `@stringfilter`, `is_safe` **absent**. Body carries three
  measured subtleties: `arg` is **not** coerced by `str()` (only `value` is, by
  the wrapper), `str.replace` of an empty `arg` raises `ValueError` — measured,
  and it escapes — and the result is `mark_safe`d only when the *pre-replacement*
  value was `SafeData` **and** `arg != ";"`.
- `addslashes` (`:60-69`) — three `replace`s in the order backslash, `"`, `'`.
- `make_list` (`:254-263`) — `list(str(value))`, so **code points**: a combining
  sequence is two elements. An integer becomes a list of its digit characters.
- `wordcount` (`:403-408`) — `len(value.split())`, using `pyunicode::is_space`,
  which M1 already measured (U+001C..U+001F are separators).

### Task 5: padding and truncation — 8 filters

`ljust`, `rjust`, `center`, `truncatechars`, `truncatewords`,
`truncatechars_html`, `truncatewords_html`, `wordwrap`

- `ljust`/`rjust` (`:417-430`) — `value.ljust(int(arg))`, and Python's `str.ljust`
  pads by **code points** with the space character. `int(arg)` is Python's `int`,
  so `int(" 5 ")` is 5 and `int("5_0")` is 50 — `pynum` owns it, not Rust's
  `str::parse`.
- `center` (`:431-438`) — **two different padding rules in one filter**, which is
  the trap. It early-returns `value` when `width <= 0`, and otherwise uses
  `f"{value:^{width}}"`, whose padding split is CPython's format-spec rule and is
  **not** `str.center`'s. Measure both: `"ab".center(5)` and `f"{'ab':^5}"` are the
  same today, but `str.center`'s formula carries a `marg & width & 1` term that
  the format spec does not, and a test written from the obvious half-and-half
  rule passes on `width=5` and fails on `width=4`. Pin at least one even and one
  odd width.
- `truncatechars` (`:305-314`) / `truncatewords` (`:330-337`) — `int(arg)` in a
  `try/except ValueError` that **returns the value unchanged** (the failure is
  silent, so the test must pin the silence), then `Truncator(value).chars(length)`
  / `.words(length)`. **Rust owns the `int(arg)` conversion and the silent
  failure; the host owns the truncation**, via `FilterHost::truncate(…, words:
  false, html: false, truncate_text: None)`. Delegated whole rather than split
  because `Truncator.chars` needs `unicodedata.normalize("NFC")` and
  `unicodedata.combining` per character (`text.py:205`, `:220`) — combining marks
  do not count toward the length — and `add_truncation_text` falls back to
  `gettext("%s…")`. A Rust `chars` would need a third Unicode table.
- `truncatechars_html` (`:316-321`) / `truncatewords_html` (`:344-348`) — the same
  host call with `html: true`, which is the `HTMLParser` branches. A `<` in the
  *text* still has to be handled, so the corpus needs cases with an unbalanced
  tag. **One method serves all four filters** (`truncate`), which is the review's
  point taken: splitting the html and non-html paths would have put two of the
  four behind a method that did not exist.
- `wordwrap` (`:410-415`) — `django.utils.text.wrap`, which is
  `textwrap.TextWrapper(width=int(arg), break_long_words=False).wrap(...)`, i.e.
  Python's `textwrap`. Host call; Rust owns the `int(arg)` conversion and the
  join. The trap: `break_long_words=False` means a word longer than the width is
  emitted whole, and `textwrap` also collapses whitespace runs — measured.

### Task 6: sequences — 9 filters

`first`, `last`, `length`, `slice`, `join`, `random`, `dictsort`,
`dictsortreversed`, `unordered_list`

- `first` (`:602-608`) / `last` (`:624-630`) — `value[0]` / `value[-1]`, catching
  **only `IndexError`**, returning `""`. So `first(5)` raises `TypeError` — the
  exception escapes the filter — and the corpus must pin the escape, not just the
  value.
- `length` (`:633-639`) — `len(value)` catching `(ValueError, TypeError)` → `0`.
- `slice` (`:651-665`) — **Rust owns the parsing, the host owns the slicing.**
  Rust does `str(arg).split(":")` into `None`/`int` bits inside a
  `try/except (ValueError, TypeError, KeyError)` that returns `value`; the
  slicing itself is `FilterHost::get_slice(value, start, stop, step)`. It cannot
  be `get_index` plus Rust slicing: `"abcdef"|slice:":3"` is `"abc"`, a **string**,
  where the index route yields a list — the wrong *type*, which §D5's comparison
  is designed to catch. Rows: negative indices, a missing component, `"::2"`, and
  a four-component `"a:b:c:d"` (which raises `TypeError` from `slice(*bits)` and
  therefore returns `value`).
- `join` (`:611-621`) — `needs_autoescape`, `is_safe=True`, and the group's
  trap: on `TypeError` it returns **`value`, the unjoined list**, not `""`. With
  `autoescape=True` it is `conditional_escape(arg).join([conditional_escape(v)
  for v in value])`; with `False` it is a bare `arg.join(value)`. Result is
  `mark_safe`d. The corpus needs both `autoescape` states.
- `random` (`:642-648`) — `random_module.choice(value)`, catching `IndexError` →
  `""`. **Review Focus 4**: the corpus row asserts membership, and the test says
  so in its name, because an equality row would be green 1 time in 57.
- `dictsort` (`:578-588`) / `dictsortreversed` (`:590-600`) — **one host call,
  `FilterHost::sort_by_property(value, prop, reverse)`**, not a resolver plus a
  sort. `_property_resolver` (`:540-575`) splits `arg` on
  `VARIABLE_ATTRIBUTE_SEPARATOR`, routes a float-convertible `arg` to
  `itemgetter` and everything else to a `__getitem__`-then-`getattr` chain whose
  except clause names **five** classes, and raises
  `AttributeError("Access to private variables is forbidden.")` for a
  leading-underscore bit. Splitting that across the FFI would mean Rust
  reimplementing Python's exception classification, and `sorted` over `Opaque`
  values is not expressible in Rust at all. **What Rust owns is the filter's own
  behaviour**: the `(AttributeError, TypeError) → ""` catch, which is a match on
  `Error::Host { kind, .. }` — the taxonomy above is what makes that a one-line
  match rather than a string comparison.
- `unordered_list` (`:669-743`) — `needs_autoescape`, recursive, and the largest
  body in the group. Its `escaper` is `conditional_escape` or identity depending
  on `autoescape`, and its output indents with tabs. The corpus needs a nested
  list three deep, because a one-level case passes with the recursion missing.

### Task 7: numbers and logic — 10 filters

`add`, `divisibleby`, `get_digit`, `stringformat`, `pluralize`, `yesno`,
`default`, `default_if_none`, `floatformat`, `filesizeformat`

- `add` (`:745-755`) — `int(value) + int(arg)` on `(ValueError, TypeError)`, then
  `value + arg` on **bare `except Exception`** → `""`. Three arms, all reachable.
- `divisibleby` (`:854-858`) — `int(value) % int(arg) == 0` with **no** try, so a
  non-numeric raises `ValueError` and `arg == 0` raises `ZeroDivisionError`. Both
  escape. Returns a `bool`, which §D5's type comparison exists for.
- `get_digit` (`:757-776`) — `int()` of both in a `try/except ValueError` that
  returns the **original** value, `arg < 1` returns the value, and
  `int(str(value)[-arg])` catching `IndexError` → `0`. Note `str(value)[-arg]`
  indexes **code points**, so it is `str`-indexing and not digit arithmetic.
- `stringformat` (`:277-295`) — `("%" + str(arg)) % value` in a
  `try/except (ValueError, TypeError)` → `""`. The `%` operator on a Python value
  is `FilterHost::percent_format`; `str(arg)` is Rust's — via `to_str`, because
  `arg` here is already a resolved value.
- `pluralize` (`:942-982`) — `arg="s"`; a `","` is prepended when absent; more
  than two bits returns `""`; `float(value) == 1` decides, falling back on
  `len(value)` for a `TypeError`. `float(value)` is `to_float`, and a `ValueError`
  means "not a number, try `len`". Three arms.
- `yesno` (`:860-897`) — `arg=None` → `gettext("yes,no,maybe")`, so **the default
  case is a translation lookup** and the corpus needs `override_settings(LANGUAGE_CODE=...)`
  or an uninstalled i18n state to be meaningful. `len(bits) < 2` returns `value`
  (not `""`); the three-way unpack has a `ValueError` fallback; `None` maps to
  `maybe`, truthiness to `yes`.
- `default` (`:840-844`) — `value or arg`, i.e. **truthiness**, not
  `is None`. `default(0, "x")` is `"x"`.
- `default_if_none` (`:846-851`) — `is None` exactly. The pair's difference is the
  whole reason both exist and is a corpus row.
- `floatformat` (`:94-221`) — the longest body in M4 and the one with the most
  measured traps: `arg` defaults to `-1`; a 200-digit/exponent refusal returns the
  input **as-is**; `Decimal` arithmetic with `prec = max(getcontext().prec, prec)`
  reading **thread-local decimal state**; `mark_safe` on two branches; the
  `formats.number_format` host call. This one is worth its own review round within
  the group.
- `filesizeformat` (`:899-940`) — `formats.number_format` plus `ngettext`, so it
  needs both host calls, and the corpus needs `USE_THOUSAND_SEPARATOR` in both
  states.

### Task 8: escaping and safety — 12 filters

`escape`, `force_escape`, `escapeseq`, `safe`, `safeseq`, `striptags`,
`escapejs`, `linebreaks`, `linebreaksbr`, `linenumbers`, `iriencode`, `urlencode`

- `escape` (`:457-461`) — `@stringfilter`, `is_safe=True`, body
  `conditional_escape(value)`. **This is a call to `FilterHost::conditional_escape`,
  not a Rust helper** — §D4, corrected after review. One host method serves the
  eight filters that need it.
- `force_escape` (`:474-483`) — `mark_safe(escape(value))`, where `escape` is the
  imported `django.utils.html.escape`, i.e. it escapes even a safe input. **That
  `escape` is Rust's** (`SafeString(html.escape(str(text)))`, a fixed
  five-replacement table, pure — §D2 does not put it in the 18), whereas
  `conditional_escape` next door is the host's. The pair is the sharpest
  illustration in M4 of the distinction: one is a table, the other is a protocol.
  The difference between `escape` and `force_escape` is one corpus row, and it is
  the row a `SafeData` input produces.
- `escapeseq` (`:464-472`), `safeseq` (`:518-526`) — list comprehensions, so the
  §D5 per-element type comparison is the assertion.
- `safe` (`:511-516`) — `mark_safe(value)`, and `mark_safe` of a **non-`str`**
  goes through `SafeString.__new__` and is `str()`d; measured on the pinned tree
  as `mark_safe(5)` returning the `str` subclass `SafeString`. So `safe` needs
  `FilterHost::mark_safe` to make that conversion, not a Rust cast.
- `escapejs` (`:78-83`) — `is_safe` **absent**, `@stringfilter`, and **the body is
  Rust's**: `mark_safe(str(value).translate(_js_escapes))` over a fixed ~30-entry
  table, which is why the derivation says pure and why an earlier revision of this
  plan wrongly had it as a host method. The absent flag is why it sits in this
  group rather than being assumed safe like its neighbours.
- `striptags` (`:528-538`) — `django.utils.html.strip_tags`, host — genuinely so,
  being `HTMLParser`-based with a depth limit that raises `SuspiciousOperation`
  (`html.py:238-272`); the corpus should carry a nested-tag case.
- `linebreaks` (`:485-495`), `linebreaksbr` (`:497-509`) — `needs_autoescape`;
  both branch on `autoescape` and use `linebreaks` from `django.utils.html`.
- `linenumbers` (`:230-245`) — `needs_autoescape`, `is_safe=True`, `escape=autoescape`,
  and it emits `<span>` markup around lines. Rust, over the host's
  `conditional_escape` and Rust's `escape`; the `autoescape=False` branch changes
  the output shape.
- `iriencode` (`:223-228`) — **the entire body is one call**:
  `return iri_to_uri(value)`. Rust owns the `try/except TypeError` → `""` wrapper
  and nothing else; the work is `FilterHost::iri_to_uri`. This filter is the
  clearest case in M4 of why the trait had to be derived rather than written from
  a reading — it was in the 18 from the start, and it had no method.
- `urlencode` (`:365-380`) — `arg=None` means `safe=None` means Django's default
  safe set; then `quote(value, safe=safe)`. Host for `quote`, Rust for the
  `safe`-set selection.

### Task 9: URLs and markup — 6 filters

`urlize`, `urlizetrunc`, `json_script`, `pprint`, `phone2numeric`, `slugify`

The group with the least Rust in it and the most to justify, which is why it is
its own task rather than folded into Task 8.

- `urlize` (`:382-387`) / `urlizetrunc` (`:389-401`) — `needs_autoescape`,
  `is_safe=True`; `urlizetrunc` takes `limit` and then `autoescape`, making it one
  of the two three-positional-parameter filters in the set. Both bodies are
  `django.utils.html.urlize`/`urlizetrunc`, which read
  `settings.URLIZE_ASSUME_HTTPS` and emit a `RemovedInDjango2028Warning` when it
  is unset (`django/utils/html.py:366`) — so the corpus needs the setting in both
  states **and** the warning is a behaviour of Django's that this port must
  decide about (§D2: the host owns it, so the warning comes along). **This is a
  permanent delegation (§D2), not a stage M10 ends** — an earlier revision said
  the M10 `utils` interposition would make porting it "porting it twice", which
  was wrong: M10's `utils` is `django/template/utils.py`. `urlize`'s Rust part is
  therefore the `limit`/`autoescape` arity and the flag behaviour, and nothing
  else, and Task 3's derivation test is what asserts that rather than a reader
  taking this paragraph's word for it.
- `json_script` (`:85-92`) — `element_id=None`, `is_safe=True`; escapes `<`, `>`
  and `&` in `json.dumps(value)` under `django.utils.html.json_script`. Host for
  `json_dumps`, Rust for the escaping and the `<script id=…>` assembly **if** the
  probe shows the assembly is separable from the dump; if it is not, the whole
  thing is the host call and the task says so rather than splitting it.
- `pprint` (`:990-996`) — **Review Focus 5**: no upstream test, so its oracle is
  `pprint.pformat` called directly in `tests/test_filter_parity.py` rather than
  through a template. Its `except Exception` arm returns
  `"Error in formatting: %s: %s"` with the exception class name, and that arm is
  reachable — an object whose `__repr__` raises. The corpus must include one.
- `phone2numeric` (`:984-988`) — `django.utils.text.phone2numeric`, a
  `str.translate` over a fixed 26-letter table; pure, so it is Rust's, and the
  table is in the module.
- `slugify` (`:266-275`) — `@stringfilter`, `is_safe=True`; `allow_unicode` was
  removed in Django 5.0, so the body is `_slugify(value)` from
  `django.utils.text`, which is `unicodedata.normalize("NFKD", …)` then a
  combining-mark strip, then `re.sub(r"[^\w\s-]", "")`, `.strip().lower()` and
  `re.sub(r"[-\s]+", "-", …)`. **`normalize` is the host's**
  (`unicode_normalize_nfkd`) because NFKD is a large Unicode table and §D2's rule
  covers it; **the two regexes and the ordering are Rust's**, and the `\w` class
  is M1's Python-faithful one, which is why `slugify("á")` and `slugify("²")`
  differ. **The `.lower()` is the host's `str_lower`** — same reason as the
  `lower` filter (Final_Sigma), and it is the second of the two consumers §D7.1
  and Task 1's "no `lower`" note point at. The corpus needs a slug that reaches
  it: `slugify("ΟΣ")` → `"ος"`, not `"οσ"`.

### Task 10: dates and elapsed time — 4 filters

`date`, `time`, `timesince`, `timeuntil`

The group where §D2's tiering is load-bearing, and the one the corpus cannot
prove with one settings state.

- `date` (`:783-795`) / `time` (`:797-809`) — `expects_localtime=True`,
  `is_safe=False`, `arg=None`. Rust owns the whole body: the `arg` handling, the
  `date_format`/`time_format` dispatch, the `arg is None` default, and
  `formats.date_format`/`time_format` are host calls. `expects_localtime` means
  the **FFI** applies `localtime` before the call (Task 3's loop), so this task's
  tests drive the loop, not the body, for that half.
- `timesince` (`:811-822`) / `timeuntil` (`:824-838`) — `arg=None`. With `arg`,
  it is the elapsed time between two datetimes; without, between `arg`-less
  `now` and the value — and `now` comes from `FilterHost::now()`, so the corpus
  row for the `arg=None` case is **time-dependent** and cannot be an equality row
  against a fixed literal. The corpus uses the two-argument form for equality and
  pins the one-argument form with a monotonicity property (the output for a
  value one hour older is the same string as for the value itself).

- [ ] **Step: after Task 10, update §10.3's M4 row** and write
  `.review/m4-summary.md` in the shape `.review/m3-summary.md` established.

---

## Self-Review

**Spec coverage.** §5.1's `filters` — "all 57 built-in filters": Tasks 4–10, 57
names accounted for above and asserted by `tests/test_filter_inventory.py` (§10.3's
M4 row). §5.1's `escape` — `conditional_escape`: Task 2, §D4, with the rest of the
module deferred to M8 and recorded. §5.2's `FilterFlags`: Task 2's `FilterFlags`
and Task 3's flag round-trip test. §5.2's `ExtensionHost::call_filter` and
`filter_flags`: **not implemented here**, and deliberately — those are the seam
for *host*-supplied filters, which is M6's (a user filter reaching Rust); M4 is
the mirror image (a Rust filter reaching the host) and uses `FilterHost`, a
different trait on the other side of the same boundary. §10.4's fan-out contract:
§D7 and the shared task shape. §10.4's "one file per filter": Task 2 creates 57
files. §10.3's "Filter corpus parity, incl. `FilterFlags` round-tripping": Tasks 3,
4–10.

**Gaps found and closed.** (a) `Value` had no owner in any milestone — M8's
`SafeData` describes the *protocol*, not the representation, so M4 has to define
it; §D1 does. (b) `conditional_escape` looked like M8's by §10.3 and M4's by
dependency, so an earlier revision had M4 implement a pure Rust version of it.
Review showed there is no pure piece — two of its three branches are Python
protocols (`Promise`, `__html__`) — so §D4 is now a host call and M4 takes
nothing from M8 at all. (c) `FilterHost` and `ExtensionHost` are easily confused
and the spec only names the second; the self-review note above is the
disambiguation, and Task 2's `host.rs` doc must repeat it. (d) **The spec claimed
a reach it does not have** — §6 said Rust owns "all built-in tags and filters" and
an earlier revision of this plan said the 18 delegating bodies would come home at
M10. Both are corrected in the spec, because a reader at M10 would otherwise hunt
for filter bodies nobody scheduled. (e) **The delegation count had no derivation**
and was given three different values across two sessions before being measured;
Task 3 Step 1b computes it — and computing it found a **second metric the first
one cannot see**, which moved the set from 18 to 20 (`lower` and `title`, via
CPython's context-sensitive case methods). Both are asserted separately.

**Five things in this plan were wrong on first writing, and review caught all
five.** The M10 `utils` claim in §D2 was a factual error about which module a
milestone interposes. The `stringfilter`-versus-`base.py:816` question in §D3 was
answered by the wrong measurement — "they converge" is true of a `SafeString` and
false of a lazy proxy. The `FilterHost` trait was written from a reading of the 57
bodies rather than derived from them, and five filters had no expressible method;
the fix is in Task 2 and the derivations are tabulated there. The fourth I found
while checking the review's `PartialEq` question: no body compares two `Value`s,
but `cut` compares one against the literal `";"`, which structural equality gets
wrong for a safe argument. The fifth is the largest: the trait's case operations
were sized for a *table*, and the four-method exhaustive diff the review asked for
showed two of them are **context**-sensitive, so `lower` and `title` left the pure
side and `sort_by_property`'s `prop` turned out to need `&Value` where it had
`&str`.

All five had the same shape — a plausible claim that nobody had executed — and
each was settled by running something (an AST walk for the imports, a probe for
the proxy, a closure walk for the trait's gaps, an AST walk for the `==` uses, a
four-method full-range diff plus 400,000 random strings for the case methods).
That is the discipline the earlier milestones established and it is the reason the
errors were cheap.

**Type consistency.** `Value`, `ValueId`, `FilterFlags`, `FilterCall`, `FilterFn`,
`FilterEntry`, `filter`, `filter_names` are spelled identically in Tasks 2, 3 and
4–10. `NoFilterHost` and `TestFilterHost` are distinct names for distinct things
(no host at all vs. a scripted one) and both are named in Task 2 or the shared
task shape. `Error::unimplemented_filter` is named in Task 2 and used in Tasks
4–10.

**Review Focus.** 1 → Tasks 1, 2 (`str_lower`/`str_title`) and 4; the exhaustive
half is Task 1's. 2 → Task 10's corpus, Task 3's loop and Task 3 Step 1b's two
derivations. 3 → Task 2's §D3 and Task 3's flag tests. 4 → Task 6. 5 → Task 9.
