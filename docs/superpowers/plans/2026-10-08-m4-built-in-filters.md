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
   point to *itself* where Rust maps it to another character. The 28 are **two
   families**, and the split is measured with `unicodedata.name()` rather than
   with a count, because a count cannot tell them apart:
   * **2 are retro-fitted mappings.** `U+A7D3` (LATIN SMALL LETTER DOUBLE THORN)
     is `Ll` in Python 3.14.4's Unicode 16.0.0 and `.upper()` returns itself,
     while Rust 1.98's `char::to_uppercase` returns `U+A7D2`; `U+A7D5` is the
     same shape. Both small letters are **already named in 16.0.0** and their
     capitals are `unassigned` there — 17.0 assigned the capitals and
     retro-fitted the mappings. This is the family that makes the table a
     *mapping* table rather than a list of characters Python never heard of.
   * **26 are new assignments**, with no name at all in 16.0.0: 25 are the
     **Beria Erfe** (Zaghawa) lowercase letters U+16EBB..U+16ED3, added in
     Unicode **17.0** (an earlier revision of this list said 16.0), and the
     26th is U+A7CF.
   So Rust's tables are **ahead** of Python's
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
`floatformat`'s **200-digit refusal, `arg`-suffix dispatch, control flow and
digit assembly** are Rust while `formats.number_format` **and the whole decimal
engine** — `Decimal`, `scaleb`, `prec = max(getcontext().prec, prec)` at `:200`,
`quantize` — are host calls. **An earlier revision of this paragraph said
`floatformat`'s "rounding, exponent arithmetic and 200-digit refusal" were Rust;
Task 7's design round refuted that by measurement, and §D2's own rule had already
named "what a `decimal.Context` is set to" as host state, so the example
contradicted the rule it illustrates.** The amendment at the end of Task 7
records the two measured routes by which the context reaches the answer —
`scaleb`'s clamp to `0E-Etiny` below `2*(Emax+prec)` and its `InvalidOperation`
above it — and the host contract that crosses the seam with it.

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
reimplements as `Value::Safe`); **27** reference a utility living in a module that
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

**D3 — `@stringfilter` is a shared wrapper the FFI applies once, and
`FilterFlags` keeps §5.2's three fields.** The wrapper
(`defaultfilters.py:38-52`) coerces with `str()`, **passes the coerced value to
the body**, and then marks the result safe iff that coerced value was `SafeData`
and the inner function's `is_safe` is true. It is carried as a fourth,
*structural* field — `stringfilter: bool` on `FilterEntry` — and deliberately not
on `FilterFlags`, which stays Django's three measured attributes so Task 3's
round-trip against the pinned registry still pins three and only three.

An earlier revision of this section put the coercion inside each filter's body
and rejected the registry field, on the argument that a `stringfilter: bool`
would move wrapper behaviour into a *shared* file that §10.4 says a fan-out agent
cannot reach — and that the agent implementing `cut` is exactly the one who needs
`cut`'s special case. Both halves of that were wrong, and the correction is the
rest of this section: the registry field is *data*, not code an agent has to
reach, and `cut`'s special case is `cut`'s own body behaviour
(`mark_safe` only when `arg != ";"`, `defaultfilters.py:447-450`) rather than
something a wrapper has to be talked out of. What the field buys is that the
wrapper's two-line rule is written **once**, on the Python side of the seam,
instead of in 29 files written by several different agents.

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
wrapper must test the **coerced** value, exactly as `defaultfilters.py:44-48`
does. A body that tested the incoming value would be right on every case anyone
would think to write and wrong on the only case where the distinction exists.
`__proxy__` is already on the wire from Task 6, so this is reachable, and Task 3
carries the case as `lower/lazy-proxy-whose-str-is-safe` in
`tests/filter_cases.py`.

**Open, and blocking the fan-out: no body can check it as the trait stands.** The
paragraph above is a requirement, not an implementation, and Task 3 measured the
gap rather than closing it:

```text
proxy = lazy(lambda s: mark_safe(s), str)("<b>hi</b>")
  lower:  Django -> (SafeString, '<b>hi</b>')   ours -> (str, '<b>hi</b>')   DIVERGES
  upper:  Django -> (str, '<B>HI</B>')          ours -> (str, '<B>HI</B>')   agrees
```

`upper` agrees because `is_safe=False` disables both halves; `lower` diverges
because `@stringfilter`'s half fires on the *coerced* value while the loop's half
(`base.py:816`) tests the value the filter was handed, which for a lazy operand is
not `SafeData`. The claim that "was the coerced value safe" is decidable at the
call because `Value::Safe` is a variant holds for a `str` subclass and **fails
here**: a body receives `to_str`'s Rust `String`, which carries no safety, and the
incoming `Value` is an interned `Opaque`. So the requirement needs a seam
operation, and every `@stringfilter` body needs it — which is why this lands
before the fan-out rather than during it.

Two shapes were considered:

* **`FilterHost::coerced_is_safe(&Value) -> bool`, called by each of the 29
  `@stringfilter` bodies.** Keeps `FilterFlags` as Django's three measured flags,
  and needs no new registry field. But it puts one subtle rule in 29 files written
  by several agents, which is the risk this very section warns about, multiplied.
* **A `stringfilter: bool` on `FilterEntry`, with the FFI applying the wrapper
  once.** This is what Django does — `@stringfilter` *is* a shared wrapper — and
  it makes the rule single-site, with the flag as data in the registry file no
  fan-out agent edits. The cost is a fourth field on the registry entry whose
  business is not one of the three measured flags; it is kept off `FilterFlags`
  for exactly that reason, so Task 3's flag round-trip still pins Django's three.

The second was taken, and review then found the half of it that the first draft
got wrong. **The wrapper's coercion has to *replace the value the body is
handed*, not merely report a safety flag to the loop.** Django's wrapper rebinds
`first = str(first)` before calling the body (`defaultfilters.py:44`), so all 29
bodies genuinely receive the coerced value — and `cut` reads its safety off it:

```python
@register.filter
@stringfilter
def cut(value, arg):
    safe = isinstance(value, SafeData)     # `value` is already `str(first)`
```

If the wrapper only computed `coerced_is_safe` for the loop and passed the raw
`Value` to the body, `cut` would have to re-derive the rule — exactly one body
remembering the thing this design exists to make single-site. So the FFI coerces
into a **safety-carrying `Value`** (`Value::Str` / `Value::Safe`) and hands *that*
to the body:

```text
was_safe = is_safe(value)                 # the value the filter is *given*
if stringfilter: value = the classified str(value)
coerced_is_safe = matches!(value, Value::Safe(_))
result = run(value, arg, call)
if is_safe and (was_safe or coerced_is_safe): result = mark_safe(result)
```

`Value::Safe` **is** the coerced-value test, because `classify` puts a `str` there
only after asking `isinstance(x, SafeData)` — so no host method was added and
`coerced_is_safe` is not a second `isinstance` that could drift from the one the
rest of the crate uses. A body reads the coerced value's safety as
`matches!(value, Value::Safe(_))`, which is `cut`'s `isinstance(value, SafeData)`
by construction. The 28 other `stringfilter` bodies are unaffected: they take
their text through `to_str` (now a pure accessor on an already-coerced value) and
none of them looks at safety.

**Observation status, and one obligation it creates.** The *safe-marking* half is
observed — `lower/lazy-proxy-whose-str-is-safe` in `tests/filter_cases.py` fails
on safety alone when the union's second disjunct is dropped. The *coercion* half
is **not yet independently observable**: no landed filter reads the coerced
value's safety, and `upper`/`lower` produce the same answer with or without it
because their bodies go through `to_str` either way. Its first observable
consequence is `cut`'s `safe` variable, so **Task 4's `cut` rows must include a
lazy `__proxy__` whose `str()` is safe, alongside a plain and a `SafeString`
input** — with those three the coercion cannot be reverted without a red test.
Until then this paragraph is the only record that the coercion half is pinned by
nothing, which is why it is written down rather than assumed.

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
- Modify: `crates/template-py/src/lib.rs` (one `#[pyfunction]`: `python_upper`, returning `String`)

**Interfaces:**
- Consumes: nothing from earlier milestones.
- Produces: `template_core::pyunicode::case::upper(ch: char) -> impl Iterator<Item = char>`, `upper_str(&str) -> String`, and `const UNCASED: [char; 28]`. **No `lower`.** The `lower` filter delegates (Task 4) and `slugify`'s `.lower()` goes through the host (Task 9), so a per-code-point `lower` would have no consumer — and it would be *silently wrong* for the strings that reach it, since Python's `str.lower()` applies Final_Sigma and a `char`-level table cannot. Shipping a function that exists, looks right, and is wrong at its obvious call site is worse than shipping none; the generator still measures the lower divergence and records it in the module doc as the reason there is no `lower`.

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

Run this under `.venv/bin/python` — the numbers are a property of the
interpreter's own Unicode tables as much as of Rust's, and the system python3 on
this machine disagrees with the pinned 3.14.4 on 67 of them. Run it after a
`rustc` upgrade or a Django pin bump. It writes
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

**It must run under `.venv/bin/python`, and that is load-bearing rather than
stylistic.** `str.upper()`'s answer comes from the interpreter's compiled-in
Unicode tables, so the divergence count is a property of *two* version pairs, not
one. Measured on this machine on 2026-10-08 with `rustc 1.98.0` fixed and only the
interpreter varying:

| interpreter | `unicodedata` | upper | lower |
|---|---|---|---|
| `.venv/bin/python` (CPython 3.14.4) | 16.0.0 | **28** | **28** |
| the system `/usr/bin/python3` | 13.0.0 | 95 | 95 |

Zero code points diverge the other way in either pairing — Rust is never *behind*
Python here — so the table is a pure suppression list. Running the generator under
the wrong interpreter emits 67 extra entries, and the parity test does catch it,
because over-suppressing means Rust returns the code point itself where Python
maps it away: `upper differs at U+A7CF`, which is a real failure and not a silent
one. `tests/test_pyunicode_case_parity.py` is also what catches a `rustc` bump
moving Rust's side of the same pair.

**The generator's `--check` is wired into `check.sh`, and that is a guard, not
housekeeping.** `--check` regenerates and diffs, so it says *what* differs, and
it is self-maintaining — a legitimate move is fixed by re-running the generator,
never by editing a number. Both this generator and M3's
`gen_python_word_ranges.py` documented `--check` as *the* way to verify currency
and **neither was invoked anywhere**, which made the drift guard for both
generated artifacts a guard nobody ran. It sits on the non-`--fast` path because
this one compiles a Rust probe.

**What `--check` closes that the parity test cannot: an edit that is
behaviourally inert.** Duplicating an entry (`[char; 28]` → `[char; 29]` with a
repeat) leaves the parity test green, because suppression is idempotent, and
leaves `every_entry_is_one_rust_would_map` green, because the duplicate names a
code point Rust maps. It is the one edit that escapes both the behavioural and
the property guard, and it is exactly the edit that makes the file stop being
what the generator emits. So the cover is **three** guards, and the division of
labour is: parity pins behaviour, the property pins non-inertness, `--check`
pins file-equals-generator.

**The 28 are two families, and the split is measured with `unicodedata.name()`,
not counted** — so the generator refuses if it moves (see `EXPECTED_BERIA_ERFE`,
`EXPECTED_UNNAMED_OTHER`, `EXPECTED_NAMED`). 25 Beria Erfe letters plus U+A7CF
have **no name** in 16.0.0 and are new *assignments*; U+A7D3 and U+A7D5 **are
named** in 16.0.0 and only their capitals and mappings arrived with 17.0, which
is the family the module doc's interesting paragraph is about. An earlier
revision of that doc said "25 Beria Erfe, plus U+A7CF, U+A7D3, U+A7D5" — 25 + 3
where the measurement says 26 + 2, wrong in the direction that *overstates* the
interesting case.

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

Run `.venv/bin/python -I scripts/gen_python_case_fixups.py` and commit its
output — the interpreter is part of the measurement, and the generator refuses
if the counts move (see Step 1's table). It applies `rustfmt` to its output, so
the committed file is fmt-clean by construction rather than by a template that
predicts rustfmt's layout for this table's length. Then the
accessors in `case.rs`:

```rust
pub fn upper(ch: char) -> impl Iterator<Item = char> {
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

- [ ] **Step 5: Observe each guard failing, for its own reason**

Three observations, and the *third* is the one that decides this step's design.
The first two were run and are recorded here as they came out; the second and
third are the pair that shows why a property assertion beats a checksum.

**A missing entry fails the parity test.** Drop `U+A7D3` (its length is in the
type, so `[char; 28]` becomes `[char; 27]` — the entry cannot be removed without
that), rebuild, and the parity test fails with `upper differs at U+A7D3`: Rust
returns `U+A7D2`, Python returns `U+A7D3`. Over-suppression of this kind is
always loud, which is the consolation §D2 records.

**A bogus entry does not — and that is the silent direction.** This is where the
step as first written was wrong twice. Substituting `U+0041` for the `U+A7CF`
entry does not isolate anything: it adds an inert entry *and* removes real
coverage, so the parity test fails at `U+A7CF` and tells you nothing about the
inert entry. The isolated experiment is to **add** `U+0041` alongside every real
entry (`[char; 29]`, all 28 kept), and then the parity test passes all four of
its tests while the Rust-side property assertion fails. Both halves matter: it is
the evidence that an inert entry is invisible to the only test that runs on every
`cargo test`, and therefore that something else has to see it.

**So the guard is a property, not a count and a hash.** An entry is *inert* when
Rust maps the code point to itself, and an inert entry changes nothing —
suppressing a code point that Rust already leaves alone is a no-op — so the
parity test cannot look at it and a hash of the contents would only say "this
changed", not "this is wrong" (and would have to be regenerated by hand every
time the table legitimately moves, fighting the generator). Instead
`case.rs` asserts `every_entry_is_one_rust_would_map`, which names the offending
code point and the reason. The two tables of edits are then covered between them:

| edit | caught by | how it reads |
|---|---|---|
| an entry removed | the parity test | `upper differs at U+A7D3` |
| an entry that Rust maps to itself | the property assertion | `U+0041 is inert` |
| an entry Rust and Python both move | the parity test | `upper differs at U+…` |

That is a closed argument: any edit either changes behaviour (parity) or does
not (property). It is also why the assertion lives in `case.rs` and not in the
parity test — it is a property of the Rust table alone, and the Python half is
the only part that needs both languages present.

**The closed half of the obligation is live.** Add a `python_lower`
`#[pyfunction]` returning `String`, rebuild, and `test_there_is_no_lower_accessor`
fails. Delete it again. That is the guard against someone helpfully restoring the
per-code-point `lower` §D2 removed.

**And a trap for the generator's own output, which cost a pass.** `cargo fmt`
silently *skips* a `.rs` file that is not reachable from the crate root, so
`cargo fmt --all -- --check` reporting this file clean meant nothing until
`pub mod case;` existed — the file was formatted for the first time only when it
was wired in, and then rustfmt wanted a diff. Two consequences, both now in the
generator: it runs its output through `rustfmt` (applied, not checked) so the
layout cannot be wrong for a different table length, and Step 6's gate is what
confirms the file is reachable. Do not "verify" formatting of a generated file
before it is wired in.

**One instance, not three, and the general statement is the useful one:
`cargo fmt`, `cargo clippy` and `cargo test` all skip an unreachable `.rs` file.**
The formatter only happens to be the one that noticed. So the window between
writing a file and adding its `pub mod` is a window in which *every* Rust gate is
silently vacuous — no formatting check, no lint, and no `#[test]` run — which is
the same failure shape as the unwired `--check` above, in a different tool. A
step that adds a `.rs` file is not done when the file exists; it is done when the
crate root names it.

A Python analogue was checked and there is no live instance: `check.sh` discovers
`test_*.py` under `tests/`, and the four files there that do not match —
`differential/cases.py`, `lexer_corpus.py`, `parser_corpus.py`,
`parser_error_corpus.py` — are corpora rather than tests, so the pattern is doing
its job. Worth knowing a future `corpus_tests.py` would be skipped in silence.

**A third instance of the same shape, found while declining a test-only probe
filter.** `cargo test -p template-py --no-run` *succeeds* and prints a path under
`target/debug/deps/`, which reads as "the binding crate's tests link". The binary
it produces holds `0 tests, 0 benchmarks`: with no `#[test]` rooting a pyo3 call,
`-Wl,-dead_strip` collects every reference and the undefined `_Py*` symbols never
have to resolve. The check is green for a reason unrelated to the claim, and it
stays green until the first test that actually attaches — which fails the link
with `Undefined symbols for architecture arm64: _Py_InitializeEx,
_PyGILState_Ensure, …`, because `extension-module` adds the dynamic-lookup flag to
the **cdylib** link only, never to a test executable. So `template-py` has no
Rust-side test surface as declared, and moving `extension-module` behind a default
feature to create one would change what `maturin develop` ships — the artifact
every Python-side test in this project depends on, which is the wrong thing to
reconfigure in order to test it. Recorded rather than fixed: the wrapper's
coercion half is pinned by `cut`'s lazy-proxy corpus row instead (§D3, Task 4).

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
- Create: `crates/template-core/src/filters/escape.rs` (the `escape` **filter**, one of the 57 — *not* a `conditional_escape` helper; §D4 says M4 adds no escape module and calls the host's `conditional_escape` instead. An earlier revision of this line said "the `conditional_escape` of §D4", which contradicted its own section.)
- Modify: `crates/template-core/src/filters/mod.rs` (rewrite: `FilterFlags`, `FilterFn`, `FilterEntry`, the registry, `filter_names`, `filter`)
- Modify: `crates/template-core/src/parser/error.rs` (**add** `PyErrorKind`, `ErrorKind::FilterHost` and `Error::filter_host`; add `Error::unimplemented_filter`; add one arm to `message()`. §D5 says why this is additive and not a reshape of the existing `ErrorKind::Host`.)
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

/// The arguments of `django.utils.formats.number_format`
/// (`formats.py:172`, six-argument and keyword-called from exactly two filters
/// in three shapes: `defaultfilters.py:186`, `:214`, `:912`).
///
/// A struct rather than four positional parameters because
/// `clippy::type_complexity` has already bitten this project, and because the
/// call sites differ only in `use_l10n` and `force_grouping` — which are the two
/// things worth being able to name at a call site.
pub struct NumberFormatArgs<'a> {
    /// The value to format: an `int`, a `float`, or a **string** of digits.
    /// `floatformat`'s second call passes its own assembled decimal text
    /// (`:214` passes `number`, not `rounded_d`), so this cannot be an `f64`.
    pub value: &'a Value,
    /// Django's `decimal_pos`; `None` is its default.
    pub decimal_pos: Option<i64>,
    /// Django's `use_l10n`, which is **three-valued**: `None` means "localize",
    /// because `number_format` maps it to `True` itself (`formats.py:179-180`),
    /// while `Some(false)` means the template asked for `floatformat:…g`.
    /// `None` and `Some(true)` are behaviourally identical, so a host may
    /// collapse them — but the caller must not, since that would hide which
    /// branch `floatformat` took.
    pub use_l10n: Option<bool>,
    /// Django's `force_grouping`.
    ///
    /// **An earlier revision of this doc said `False` on every built-in call
    /// path, and that is wrong.** `floatformat`'s `g`/`gu`/`ug` suffixes set it
    /// (`:142-148`), and the difference is observable. Measured, each pair
    /// holding precision fixed so that only grouping moves: `floatformat(10000,
    /// "g")` is `'10,000'` where `floatformat(10000)` is `'10000'`, and
    /// `floatformat(6666.6666, "2g")` is `'6,666.67'` where
    /// `floatformat(6666.6666, "2")` is `'6666.67'`. All four are `SafeString`,
    /// so only the text can see the difference. (An earlier revision paired
    /// `"2g"` against the *default* argument `'6666.7'`, which is `p = -1` —
    /// that pair moved precision and grouping together and so demonstrated
    /// neither. The reviewer caught it; `"2"` is the isolating partner.)
    /// `filesizeformat`'s one call (`:912`) is the `false` case.
    pub force_grouping: bool,
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
    /// `list(v)`, **not** an index walk: `unordered_list`'s whole-value case is a
    /// generator (`test_unordered_list.py:141`), which has no `[]`. `first`/`last`
    /// are the mirror image — they are `value[0]`/`value[-1]` and must go through
    /// `get_index`, because Django's `except IndexError` lets a generator's
    /// `TypeError` escape, and an `iter_values`-based implementation would return
    /// a value where Django raises.
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

    // --- the one type question Rust cannot answer ---------------------------
    /// `unordered_list`'s nested-sequence test (`:695-706`). `Some(items)` when
    /// **both** halves hold — `isinstance(next_item, (list, tuple,
    /// types.GeneratorType))` *and* `iter(next_item)` does not raise — and `None`
    /// otherwise, meaning "a plain item".
    ///
    /// One method, not a predicate plus an iteration, by §D7.1's argument: Rust
    /// cannot answer the isinstance for an `Opaque` element (a tuple and a
    /// generator both arrive as `Opaque`; neither is a `Value::List`), and
    /// `iter_values -> Vec<Value>` cannot represent a generator without
    /// *consuming* it — which is the very branch being consulted. Splitting them
    /// would put Python's `TypeError` classification on the Rust side of the
    /// seam, which is what the seam exists to avoid.
    ///
    /// Measured, not inferred: the suite pins both halves, and an implementation
    /// without this method gets one of them wrong.
    /// `filter_tests/test_unordered_list.py:141` is
    /// `unordered_list(item_generator())` — the **whole value** is a generator,
    /// so `iter_values` must be `list(v)` and must not index — and `:150`'s
    /// `test_nested_generators` is a list whose **element** is a generator, which
    /// must nest: `"\t<li>A\n\t<ul>\n\t\t<li>B</li>…"`. A flat output there is a
    /// silently wrong template, not an error.
    fn as_nested_sequence(&self, value: &Value) -> Result<Option<Vec<Value>>, Error>;

    // --- CPython string-method semantics ------------------------------------
    /// `str.lower()`. **Not a per-code-point table**, and that is the finding:
    /// this is the project's fifth Unicode surprise after `\w`, whitespace,
    /// `combining` and the case fixup, and the first that is not a table at all.
    /// Measured: `"ΟΣ".lower()` is `"ος"`, not `"οσ"` — Unicode's
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
    /// `django.utils.timesince.timesince`, with **Django's own signature**.
    /// `timeuntil` is not a caller-side argument swap: Django swaps `d` and
    /// `now` *after* normalising the timezones (`timesince.py:60-70`), and the
    /// calendar fields `total_months` reads (`:83`) are zone-dependent, so a
    /// swap performed here would report different months for two aware
    /// datetimes in different zones. `now` is an `Option` for the same reason:
    /// `timeuntil_filter` passes `arg=None` through and `now` is filled from
    /// `d.tzinfo` before the swap.
    fn timesince(&self, d: &Value, now: Option<&Value>, reversed: bool) -> String;
    fn number_format(&self, args: NumberFormatArgs<'_>) -> String;
    fn gettext(&self, text: &str) -> String;
    fn ngettext(&self, singular: &str, plural: &str, n: u64) -> String;
    fn now(&self) -> Result<Value, Error>;
}
```

**Three implementations of that trait, all written here**: the FFI's real one
(Task 3), `NoFilterHost` (every method `Err(Error::host("<name>"))`), and
`TestFilterHost` (scripted answers, recorded calls) — the last two in this task's
`host.rs`, per Step 2. They are deliberately three names and not two: "no host at
all" and "a host that answers what the test says" fail a test in opposite
directions, and collapsing them would make a body that wrongly needs the host pass
a bare-shell unit test.

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
`Error::FilterHost(Box<FilterHostError>)` — `{ kind, message, operation }`,
**boxed** — with `PyErrorKind` ∈
`{AttributeError, IndexError, KeyError, TypeError, ValueError, ZeroDivisionError}`,
plus `Other(Box<str>)` for the one body that needs a class name it cannot name in
advance (`pprint`, `:990-996`), and a body matches on `kind` rather than on a
message. Task 6's `first(5)` row
asserts the *escape*, not a value, and that is only expressible if the class
survives the seam.

**The `Box` is not stylistic and this paragraph records why it was added in Task
2's own commit.** Written inline, `FilterHost` is 64 bytes and takes `Error` to
exactly 128 — the threshold `clippy::result_large_err` fires at — which failed the
build at **122 call sites**, every one a `Result<_, Error>` in a file the change
never touched. Boxed, `Error` is 96 bytes, and `error.rs` pins `size_of::<Error>()
< 128` so the next field added there fails one local test rather than 122 distant
ones. The same lesson as the two-version Unicode tables: a shared type's layout is
a project-wide constraint, so the cost of growing it must land where the growth
happened.

**It is a new variant, not a reshape of `ErrorKind::Host`, and the reason is
churn.** M1–M3's `ErrorKind::Host(String)` is the *parse-time* host's refusal —
`filter_expr.rs:303`'s `Invalid filter: '…'`, `tags/mod.rs`'s "the host
refused" — and it has no Python class to carry, by design (`resolve.rs:207` says
so). Reshaping it would mean editing a frozen two-milestone-old surface, two
`#[cfg(test)]` assertions that compare `ErrorKind::Host("the host refused"
.to_string())`, and the `Host` trait's doc, in service of nothing M4 needs. So
`PyErrorKind` and `ErrorKind::FilterHost` are **added** in `parser/error.rs`,
`message()`'s match gains one arm, and the existing variant, its constructor and
its three test assertions are untouched. The filter seam's constructor is
`Error::filter_host(kind, operation, message)`, and `operation` is a
`&'static str` naming the `FilterHost` method, so a mismatched match in a body
names the operation in the failure.

**`NoFilterHost` and `TestFilterHost`'s unset field use `Error::host`, not
`Error::filter_host`, and that is deliberate.** They are hosts that *refuse*, with
no Python exception class behind the refusal — the same shape `ErrorKind::Host`
was built for, and fabricating a `PyErrorKind` for a host that never touched
Python would make a `kind` match lie about where the answer came from. So the two
variants split on their real question: `FilterHost` is "Python raised class C",
`Host` is "nobody answered". A test can tell them apart, which is what Step 3's
mutation check relies on.

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
| 6 | `unordered_list` | `:695-706` tests `isinstance(next_item, (list, tuple, types.GeneratorType))` and then `iter()`, to decide whether an element nests; a tuple and a generator both reach Rust as `Opaque`, and `iter_values` cannot see a generator without consuming the branch it is being asked about. Both halves are in the suite (`test_unordered_list.py:141` whole-value generator, `:150` element generator that must nest) | `as_nested_sequence` (one method, not a predicate plus an iteration) |

The fifth is the one that changes a milestone boundary, and it changes it in the
direction of *removing* a reach rather than taking one: there is no small pure
piece of `conditional_escape` to implement in Rust, because two of its three
branches are Python-protocol questions. §D4 now says so. The sixth arrived in the
second review round, after the trait was otherwise settled, and it is the one that
would have produced a **silently wrong template** rather than a failure: an
`unordered_list` without it renders a nested list flat.

**And the trait is now walked against all 57, not five.** The second round
re-checked the remaining filters for anything needing a method an agent cannot
reach and found nothing else: `escapeseq`/`safeseq` are `iter_values` plus
`conditional_escape`/`mark_safe`; `add`'s int-then-concat-then-`""` cascade is
expressible through the taxonomy; `json_script` is `json_dumps` plus the escape
table; `first`/`last` need `get_index` and not `iter_values`, which is agent
discipline rather than a missing method. That claim is the reason the trait can
now be frozen — it is a walk, not a reading, and it is recorded here so a later
review can re-run it.

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
`PartialEq` deriving structurally (with §D1's "compares converted primitives"
rule in that derive's doc, because a rule stated only in a plan is a rule the
next reader does not have). Add a `pub fn type_name(&self) -> &'static str`
returning Python's name — `"NoneType"`, `"bool"`, `"int"`, `"float"`, `"str"`,
`"SafeString"`, `"list"`, and `"object"` for `Opaque` — which the corpus's §D5
type comparison uses.

Two things about that list are load-bearing and not cosmetic:

- **`Safe` reports `"SafeString"`, not `"str"`.** §D5 folds `SafeData` into the
  type name, and Python's own name for the correct answer is `SafeString`
  (`type(mark_safe("x")).__name__`, measured). Reporting `"str"` here would make
  `escape` returning an *unsafe* string indistinguishable from returning a safe
  one — the exact failure §D5 exists to catch, in the one place built to catch it.
- **`Opaque` reports `"object"`, which is a limitation, not an answer.** A
  `&'static str` cannot ask the host what type it owns, so a row whose result is
  genuinely an `Opaque` (a `datetime` out of `first`) compares `"object"` against
  `"datetime"` and reports a false mismatch. Task 3's harness owns that: route
  the name through the host, or drop the type half for that row.

`FilterFlags` and `FilterFn` live in `filters/mod.rs` with the registry they
describe, not in `value.rs`; `FilterCall` and `NumberFormatArgs` live in
`host.rs` with the trait. The stub files' import lines (`use
crate::filters::host::FilterCall;` and so on) are the map, and they are what the
fan-out agents read.

- [ ] **Step 2: Write `host.rs`**

The trait above, plus `NoFilterHost` **and `TestFilterHost`**. Each method's doc
comment carries the Django call site it stands for and the reason it is on the
host rather than in Rust — §D2's rule is the reason, and the *specific* reason is
`settings`, the active language, or the clock. `clippy::type_complexity` has bitten this project;
name the `number_format` parameter list with a struct if it warns.

`TestFilterHost` is **not optional and not the agents' to write**: the shared task
shape below tells every agent to use it, §D7 forbids an agent from editing
`host.rs`, and Task 3's corpus needs a Rust-side host whose answers a test can
dictate. So it lands here, in the one commit that owns the file.

```rust
/// A scripted host for a filter module's own `#[cfg(test)] mod tests`.
///
/// Every operation answers from its own `Option` field when set, and otherwise
/// returns `Err(Error::host("<name>"))` — the same failure `NoFilterHost` gives,
/// so a test cannot pass by leaving a field unset. Every call is recorded, so a
/// test can assert the body *reached* the seam rather than asserting a value the
/// body could have produced on its own. The second half is what §11 needs: a
/// delegating filter whose test never checks `calls()` can be reimplemented in
/// Rust and still pass.
#[derive(Default)]
pub struct TestFilterHost {
    calls: RefCell<Vec<&'static str>>,
    pub str_lower: Option<String>,
    pub str_title: Option<String>,
    pub truncate: Option<String>,
    pub strip_tags: Option<String>,
    pub slugify: Option<String>,
    pub urlize: Option<String>,
    pub conditional_escape: Option<Value>,
    pub sort_by_property: Option<Value>,
    // …one `Option` per host operation, in the trait's own order…
}

impl TestFilterHost {
    /// The operation names this host was asked for, in order.
    pub fn calls(&self) -> Vec<&'static str> { … }
    /// Panics naming `name` if it is absent, and prints the calls it did see.
    pub fn assert_called(&self, name: &str) { … }
}
```

Two properties, both observable in `host.rs`'s own `#[cfg(test)] mod tests` in
this task: an unset field produces `Err(Error::host(name))` rather than a default
value, and `calls()` is appended **before** the field is read, so recording
cannot depend on what the scripted answer happened to be. Observe them by
mutation, as the other two impls' tests do: give `truncate` a default answer and
watch the "unset means error" assertion go green wrongly; move the `calls()` push
below the field read and watch a test that asserts the call happened on an
unset field fail. Restore both.

**A third property is a limit rather than a feature, and Task 5 is where it was
measured: `calls` records the operation's *name* and never its arguments.** The
four truncation filters and `wordwrap` are bodies whose distinguishing behaviour
lives entirely in the arguments they pass — `truncate`'s `words`/`html`/
`truncate_text` triple and `wrap`'s width — so a module test cannot see it:
flipping `truncatewords_html`'s `html: true` to `false` leaves all six of its
unit tests green. **That makes a rule for every remaining group, not just Task 5:
for each delegating filter the corpus carries at least one row per distinct
argument set, named for the argument it protects.** And because a row can *look*
like an observer of an argument without being one — Task 5's two `html`-flag
mutations, on two filters sharing one host call, moved 1 and 3 rows — the number
of rows that actually observe each argument is measured by mutating the argument
and counting, never inferred from the filter's shape.

**Recording arguments as well was considered and declined, for a reason sharper
than "the corpus is enough".** A test asserting the body passed the arguments the
test scripted is a restatement of the body rather than a check of it: it can only
fail if both sides are changed together, which is the same shape as the 205
identity-compared subtests this project already paid for. The corpus fails when
the arguments are wrong **against Django**, which is the contract; a second
mechanism pinning the same thing would pin the wrong thing more precisely.

But "never" would be too strong, and the measurement above is why — a wrong
argument moving **1** row on one filter and **3** on another means 0 is reachable.
So the rule is a trigger rather than a standing judgement: **the corpus is the
contract, and Step 8.5's count is the detector — a mutation that moves 0 rows is
the signal that a local pin is needed.** That makes it a thing measured, from a
number already collected, instead of a matter of taste.

- [ ] **Step 3: Write the 57 stubs**

`crates/template-core/src/filters/mod.rs` declares all 57 modules and the
`REGISTRY` table of 57 entries, ascending by name, each with its flags — the
measured values, which are not guessed. The table has only four distinct shapes —
`NONE` (19), `SAFE` (29), `SAFE_AUTOESCAPE` (7), `LOCALTIME` (2) — so
`FilterFlags` carries them as four associated constants and each registry entry
names one. That keeps an entry to a single line, so only the 11 filters that carry
a second flag stand out:

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

pub fn run(_value: Value, _arg: Option<Value>, _call: &FilterCall<'_>) -> Result<Value, Error> {
    Err(Error::unimplemented_filter("upper"))
}
```

The underscore on all three parameters is not cosmetic and the first draft of this
snippet was wrong without it: the gate runs `cargo clippy … -- -D warnings`, and a
stub binds three values it never reads, so `unused_variables` is an error. (The
imports stay, because the signature uses them.) The same applies to every one of
the 57, so a group that replaces a body renames the parameters it actually uses
and leaves the rest underscored.

Add, to `parser/error.rs` and **without touching `ErrorKind::Host`**:

- `pub enum PyErrorKind { AttributeError, IndexError, KeyError, TypeError,
  ValueError, ZeroDivisionError, Other(Box<str>) }` (§D5's taxonomy, plus the
  seventh variant `pprint`'s `e.__class__.__name__` needs) with a `name()` method
  returning Python's `__name__` for all seven.
- `pub struct FilterHostError { kind: PyErrorKind, message: String,
  operation: &'static str }` and `ErrorKind::FilterHost(Box<FilterHostError>)`,
  one new arm in `message()` returning the message **verbatim** (it is Python's
  exception text and the shell re-raises it; `operation` stays on the struct for
  `Debug`), and the constructor `Error::filter_host(kind, operation, message)`.
  The `Box` is required, not stylistic — see §D5's paragraph above.
- `Error::unimplemented_filter(name)`, alongside the existing `Error::host`, with
  the same `#[cfg(test)]`-visible kind, and a test in `error.rs` that the message
  names the filter *and* the milestone that owns it — the `UnimplementedTag`
  precedent.
- A `size_of::<Error>() < 128` test, so the next payload that would re-break 122
  call sites fails here instead.

Three tests in `error.rs` compare `ErrorKind::Host(….)` and must still pass
unchanged; that they do is the evidence that this step is additive, and it is
worth saying in the commit message rather than leaving a reviewer to notice.

- [ ] **Step 4: Write the `m3_owns_no_filters` replacement**

`filters/mod.rs`'s current test asserts `filter_names().is_empty()`. Replace it
with three tests:

- `the_registry_is_the_measured_57`, asserting the exact sorted name list against
  a literal. Comparing to a *sorted* literal also pins the "ascending by name"
  claim, so there is no separate ordering test to drift from it.
- `the_flags_are_the_measured_ones`, checking each entry's three flags against the
  Step 3 table's sets, spelled out in the test rather than derived from the same
  constants the registry uses — otherwise the test agrees with whatever the
  registry says.
- `every_registry_entry_is_a_stub_at_m4`, which is what makes each group's Task
  4–10 deliverable *visible*. Two details the first draft got wrong:
  - A body counts as a stub only if its error is an `Unimplemented` naming **its
    own filter**, not merely if it returned `Err`. "Any error means still stubbed"
    is wrong in both directions: a real `add` given `Value::None` raises, so an
    implemented filter would look stubbed, and a copy-pasted `run:` line in the
    registry would look implemented.
  - The expectation is a `STILL_STUBBED` list checked in **both** directions, so
    the failure names which way the two disagree. While all 57 are stubs it is an
    alias of the name list — a second literal copy of the same 57 names could
    neither be wrong nor be right — and the commit that lands the first group
    replaces it with the literal remainder. An `assert_eq!` of two 57-name lists
    would make a reader find the difference by eye.

- [ ] **Step 5: Observe both guards failing**

(a) Delete `("upper", …)` from `REGISTRY`. Expected:
`the_registry_is_the_measured_57` fails naming `upper`.
(b) Implement `upper`'s body in place of the stub. Expected:
`every_registry_entry_is_a_stub_at_m4` fails with
`declared but already implemented: ["upper"]`.
(c) Also observe the other direction: trim `upper` from `STILL_STUBBED` while its
body is still a stub, and expect `still stubbed but not declared: ["upper"]`.
Restore all three.

Note what (a) shows by *not* failing: `every_registry_entry_is_a_stub_at_m4`
passes when a row is deleted, because both sides lose the name. That is the
division of labour, not a gap — (a)'s guard is what sees a missing row, and
(b)'s is what sees a landed body.

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
- Modify: `crates/template-core/src/filters/upper.rs`, `lower.rs` (stub → body; see Step 4b)
- Modify: `crates/template-core/src/filters/mod.rs` (shrink `STILL_STUBBED` to the literal 55)
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

**The stub set is measured, not restated.** `every_implemented_filter_has_cases`
compares the corpus's filter names against the filters whose body is no longer a
stub, where "is a stub" is *observed* — `_native.filter(name, None)` raising
`UnimplementedTag` naming that filter, the same rule the Rust guard's
`stub_error` applies from the other side. `every_stub_is_named` then pins the
**count** (55 at Task 3, decreasing by each group's size in the commit that lands
it) and prints the names in its failure message.

There is deliberately **no Python list of 55 names**, which is a deviation from
this plan's earlier wording. Which filters are stubs is a Rust fact; its
authoritative statement is `STILL_STUBBED` in `filters/mod.rs`, where
`every_registry_entry_is_a_stub_at_m4` checks it against the bodies themselves in
both directions. A Python literal would be a second copy of that fact, and its
only possible failure — drift between the copies — is caught by neither side's
mechanism so much as by whichever the author happened to edit: it would cost a
debugging session at commit time and catch nothing a body-level guard does not.

- [ ] **Step 4b: Land `upper` and `lower`, and shrink the stub list**

These two bodies are Step 4's dependency, not Task 4's: the corpus compares them,
and Step 5's gate has to be green before the commit, so a Task-3 commit whose
corpus holds two filters whose bodies are still stubs cannot pass its own
`every_implemented_filter_has_cases`. `upper` and `lower` are also the pair that
proves the seam in both directions — `upper` is entirely Rust (`pyunicode::case`,
`is_safe=False`), `lower` is entirely the host (`str_lower`, `is_safe=True`) — so
landing them here is what makes Task 3's FFI a *demonstrated* surface rather than
a declared one.

`upper`'s body is `call.host.to_str` then `pyunicode::case::upper_str`;
`lower`'s is `call.host.to_str` then `call.host.str_lower`, with no Rust
fallback, because there is no per-code-point `lower` to call (Task 1).

**`lower` is `is_safe=True`, not the `False` Task 4's bullet says** — measured:
`@register.filter(is_safe=True)` at `defaultfilters.py:247`, and the flag table
below already lists it among the 36. `upper`'s decorator passes
`is_safe=False` explicitly (`:358`), which is why the two differ.

In `mod.rs`, `STILL_STUBBED` stops being an alias of `MEASURED_NAMES` and becomes
the literal remainder, `[&str; 55]`. The guard is observed failing in the
intended direction first: put `"upper"` back in the literal while its body is
real, and it reports `declared but already implemented: ["upper"]`.

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
`floatformat`'s thread-local decimal context. And `floatformat` — 127 lines, the
longest body in M4 — is dispatched as its **own unit inside Task 7 and reviewed
alone**, which is what "worth its own review round within the group" below means
concretely.

### Why the fan-out is serial, and why Task 4 goes first

**Integration is serial anyway.** Every group ends in the same three central
steps — Step 8's `maturin develop`, the corpus rows, the one commit — and those
edit files no agent owns (`filters/mod.rs`, `tests/filter_cases.py`,
`tests/test_filter_inventory.py`). Dispatching seven groups at once would not
parallelise them; it would only move the serialisation to the point where the
orchestrator is reconciling seven half-written registries at the same time, with
no group's corpus rows yet proving the shape the next group is asked to follow.

**Task 4 is a pilot.** It is the first dispatch under §D7, so part of its job is
finding out what the contract forgot — which is why Step 7 asks for two reports
rather than one. What the first group's report says the contract got wrong is
cheap to fix after one group and expensive after seven, and it is only fixable at
all while the rest are still undispatched. That is the reason this fan-out is not
a fan-out, and it is the one that survives someone later asking why.

Two mechanical facts also decide it, recorded because they would bite anyone who
later reasons that the work is parallel so the dispatch should be:

- `.django/` is gitignored, so an `isolation: "worktree"` copy does not contain
  the pinned 6.0.9 checkout every group's Step 1 measures against — worktree
  isolation makes a group's first step impossible, not merely inconvenient.
- In one shared tree, a compile error in one agent's half-written module fails
  every other agent's `cargo test -p template-core`, since Rust compiles a crate
  as a unit; parallel agents cannot all run their own Step 3.

### What the pilot found in the central harness

Recorded here because it is the one class of defect no group can see from inside
the module it owns — and it is why the pilot's Step 7 report is worth asking for
in full rather than as a summary.

`render_django` in `tests/test_filter_parity.py` passed `case["arg"]` to the probe
**raw**, but `django_answer` takes *template text*: a `str` argument has to be
handed over rendered as the constant `"b"`, so that `resolve` sees a
`constant_arg` and `mark_safe`s it (`base.py:788-792`). A bare `b` is a variable
*lookup*. Every corpus row before Task 4 had no argument at all, so the path had
never executed — and in the ordinary case that is not a crash but a **silently
different question**: `x|cut:b` scored against the corpus's declared `x|cut:"b"`.
Task 4 contributes the corpus's first argument, which is why the first group with
one is where a harness defect older than the group surfaced. The fix is one line,
`probe.template_literal(case["arg"])`, and its guard was observed failing for its
intended reason before it was kept (§11): the raw-argument version dies with
`VariableDoesNotExist: Failed lookup for key [b]` while `test_every_case` asserts
in the same breath that the token is the constant — the two halves of the harness
disagreeing about what was compared is exactly what a row with an argument exists
to detect, and neither half alone would have caught it.

### The shared task shape

- [ ] **Step 1: Measure.** For each filter in the group, run
  `python scripts/filter_probe.py <name> <value> [<arg>]` over the group's input
  list and record the printed `(type, value)` pairs. **Do not predict a row.**
  M2 and M3 between them lost eight plan rows to predictions nobody had run.

  **And for any filter wrapping a Python builtin, one row must sit at that
  builtin's edge** — the input where it does something other than the obvious
  thing — with the answer taken from the probe rather than inferred from the
  builtin's name. "Test the edges" is not actionable; naming the edge is. This
  rule exists because the project has now hit the same shape four times: a Rust
  primitive that looks equivalent to a Python one and agrees everywhere a
  reasonable corpus looks. Case mapping (`ß`, Final_Sigma), `\w`, `title`'s
  `\d`, and `center`, where `str.center` is the *wrong reference* for a filter
  that is spelled like it. Three of those four were caught by a plan bullet that
  named the specific input; `center` was caught by measuring the filter instead
  of the builtin whose name it shares.
- [ ] **Step 2: Write the Rust unit tests** in each `filters/<name>.rs`'s
  `#[cfg(test)] mod tests`, using `NoFilterHost` for the pure cases and a
  `TestFilterHost` (written centrally in Task 2 Step 2, in `filters/host.rs`,
  **before** the group is dispatched) for the ones that need the seam. A test for
  a delegating filter also asserts `host.assert_called(…)`, so the body cannot be
  silently reimplemented in Rust and still pass. Each test asserts the measured
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

  Two reports are required, and both exist because the plan's own doc comments
  name a Django call site per trait method while nothing yet names a *caller*:

  * **every trait method each body actually calls**, filter by filter. The 35
    doc comments say why each method exists; the agents' reports are the other
    half, and the pair is what says at close-out whether any method is a second
    `repr` — written once, uncalled, unpinned, and frozen by §10.4.
  * **every operation the group needed and did not find.** A "no" is a result
    here and must be said out loud, not left as silence.
- [ ] **Step 8: Central**: `maturin develop`, paste the group's rows into
  `tests/filter_cases.py`, shrink the stub assertion in
  `tests/test_filter_inventory.py`, run `bash scripts/check.sh`, commit.
- [ ] **Step 8.5: Mutate once, centrally, and record which rows moved.** One
  mutation aimed at the group's riskiest behaviour — not one per body, which
  Step 5 already does inside the module — run against the whole corpus, with the
  **count** of rows that changed written into the commit. The output is not "the
  guard works"; it is the number, because the number is what says whether the
  rows are coverage or furniture. Task 4's mutation inverted the wrapper's
  replacement half and moved **1 of 49** rows; that figure is what makes the
  other 48's blindness a measured fact rather than a claim, and §D3 rests on it.

  **§11 is why this is a step and not a nicety.** Task 4's mutation also
  falsified a row's *docstring* — `capfirst/lazy-proxy-whose-str-is-safe` was
  green, correct, and documented as observing something it cannot observe — and
  nothing in a passing suite could have said so. That is this project's
  recurring defect — the fourth instance, after the `héllo` repr case, the
  `len(arguments) > 100` floor, and the 205 identity-compared subtests — and the
  mutation is the only instrument that asks the question mechanically.

  **The corpus compares `(type, value)` and safety as three separate assertions
  because the mutation proved the type half load-bearing**: in Task 4's run the
  *text* was identical in all 49 rows, so a corpus comparing `==` would have been
  entirely green while the thing it was built to catch was inverted. That is
  recorded here as a rule with its measurement rather than as a preference.

  **Task 5's run, and the finding that the count must be measured per filter
  rather than inferred from the shape.** Three mutations against the 90-row
  corpus, each restored from a `cp` backup and md5-verified before the next:

  | mutation | rows moved | which |
  |---|---|---|
  | `center`'s `left = margin / 2` → `str.center`'s `margin & width & 1` | **1** | `center/both-odd-width` |
  | `truncatewords_html`'s `html: true` → `false` | **1** | `truncatewords_html/unbalanced-tag-is-synthesised` |
  | `truncatechars_html`'s `html: true` → `false` | **3** | all three of its rows |

  The last two are the *same kind* of mutation on two filters that share one
  host call, and they differ by a factor of three: `"<b>one two</b> three"` at
  two words answers identically with the flag off, because the join of
  `"<b>one"` and `"two</b>"` is the same string the html path produces. So a row
  can look like an observer of an argument and not be one, and the only way to
  know which rows observe which argument is to move the argument and count. That
  is why the corpus's `truncatewords_html` rows now say out loud which of the two
  observes the flag and which does not.

  **The count also caught a row that observed two things at once.** Mutation A
  moved **2** rows on its first run, not 1: `center/safe-input-stays-safe` was
  written at width 5 on a two-code-point value, which is a both-odd margin — so
  it was silently a second observer of the split rule. Its argument is now width
  4, where the two rules agree, and the mutation moves exactly the one named row.
  A count that says "2" for a defect with one named observer is the ambiguity this
  project has paid for four times; the corpus row carries the measurement in its
  comment so the next reader does not re-introduce it.

  **That is the first time the count was the signal rather than the colour.**
  Every other §11 finding here arrived as a *failure* — a red row, a falsified
  docstring, a wrapper whose replacement half was inverted. This one arrived as
  `2` where `1` was expected, in a suite that was **green**: nothing was failing,
  and nobody was looking for it. So the number is not bookkeeping that
  accompanies the mutation; it is a second instrument, and the only one of the
  two that can find a defect with no symptom. Collect it even when the guard is
  already known to work.

  **Restoring a mutated file is `cp`, never git.** The fan-out agents' work in
  this tree is uncommitted, so `git checkout` on a mutated file does not restore
  it — it reverts to the last commit and destroys everything the agent wrote
  since. That happened once, during Task 5's run. The body came back byte-exact,
  but only because the agent had independently `cp`'d it outside the repo and the
  pre-mutation `md5` was still on hand to check the restore against; without
  either, "restored" would have been a re-typing rather than a verification. So a
  §11 run copies the file out with `cp`, restores with `cp`, and compares `md5`
  before and after. **The stronger remedy costs nothing and removes the class
  entirely: commit the group before running 8.5**, since the mutation is reverted
  either way and a committed body is recoverable from git rather than only from a
  scratch copy.

---

### Task 4: case and simple text — 6 filters

`capfirst`, `title`, `cut`, `addslashes`, `make_list`, `wordcount`

The group Task 1 unblocks. **`upper` and `lower` shipped in Task 3** as the
corpus's seed (Step 4b there), which is why this group is six and not eight: what
remains is the case work that needs no new seam — except `title`, which is
entirely the host's.

- `capfirst` (`:71-76`) — `value and value[0].upper() + value[1:]`. The empty
  string returns itself, not `""`-built-from-nothing; `value[0]` is a **code
  point**, so `capfirst("ßx")` is `"SSx"` and the string grows by one character.
  Measured, not derived. It is the second consumer of Task 1's case table — but
  it calls the **per-code-point iterator** `pyunicode::case::upper(ch)`, not
  `upper_str`: Python's `value[0].upper()` is *one code point* whose mapping may
  yield several characters, and `collect()` on the iterator is that shape, where
  `upper_str` is a whole-string function that would have to be handed a
  one-character slice. Task 3's `upper` is therefore the only caller of
  `upper_str`. The suppression of the 28 code points is in the shared table and
  applies either way — so `capfirst("ꟓ")` is itself, not `"꟒"`, and the
  corpus row `capfirst/suppressed-code-point` pins it (a *second* code point from
  `upper/beria-erfe-assignment`'s list, which is what makes the suppression
  reachable from two filters rather than one body's test). This wording was
  corrected at Task 4's integration: the bullet originally said capfirst was the
  second and last caller of `upper_str`, which is what the body *would* have been
  written against had the code point and the string been the same thing.
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
  and Task 1's `is_cased` helper is **not** written. The two regexes are the one
  part of `title` that *is* Rust's, and for a measured reason worth recording so
  nobody "fixes" it later: enumerating every code point in both patterns shows
  the only cased characters a match can hold are ASCII `a-z` and `A-Z` (`\d`
  matches 760 code points and **none** of them is cased), so `.lower()` on a
  match has no Final_Sigma question to answer — unlike `str.title()`'s own
  run-remainder lowercasing, inside the host call, which is where the 444
  failures came from.
- `cut` (`:441-450`) — `@stringfilter`, `is_safe` **absent**. Body carries three
  measured subtleties: `arg` is **not** coerced by `str()` (only `value` is, by
  the wrapper) — and the reason is now measured rather than stylistic, because a
  non-`str` `arg` raises **TypeError** out of `str.replace` and it escapes, so a
  body that coerced `arg` would turn Django's `TypeError` into an answer; an
  empty `arg` is a **no-op**, `"AbC".replace("", "")` being `"AbC"`; and the
  result is `mark_safe`d only when the *pre-replacement* value was `SafeData`
  **and** `arg != ";"`.

  **Correction, measured against the pinned 6.0.9 source.** An earlier revision
  of this bullet said `str.replace` of an empty `arg` raises `ValueError`. It
  does not: `cut("AbC", "")` returns `"AbC"` for a plain `str`, a `SafeString`
  and a `__proxy__` alike, and sweeping eight value shapes against six `arg`s
  (`"AbC", "", mark_safe("AbC"), __proxy__, 5, None, ["a"]` ×
  `"", ";", "b", 0, None, "''"`) produces no `ValueError` at all — the only
  escaping exception is `TypeError` for a non-`str` `arg`. That claim was a
  prediction nobody had run, which is the failure mode Step 1 exists to catch,
  and it is left visible here because a body written to satisfy it would carry a
  branch no input can reach.

  The `TypeError` is **not** a corpus row, and at integration it stopped being an
  open question: `test_filter_parity.py`'s `assert_same` compares `(type, value)`
  and safety with no exception path, so the harness cannot express an answer that
  is a `raise`. `cut.rs` carries it instead —
  `a_non_string_argument_raises_type_error` and
  `a_none_argument_is_the_replace_type_error` assert the class and the exact
  message (`not int`, and `not None` where the obvious
  `Value::None.type_name()` spelling would say `not NoneType`). **So the corpus
  should not grow an expected-exception key**, which is the question an earlier
  revision of this bullet left open: one filter raising one exception is pinned
  more sharply in that filter's own module, next to the body, than a corpus row
  could pin it — and the corpus's job is the comparison a rendered template
  cannot make, which a body test already covers here. If a second filter later
  raises, the question reopens; today the key would have exactly one user.

  The same three answers were then confirmed **end to end**, through the FFI and
  through the shim, and each is byte-identical to stock Django's: `{{ x|cut }}` is
  the parser's `TemplateSyntaxError: cut requires 2 arguments, 1 provided` (so the
  arity error is raised before any body, which is what makes the `None` branch
  above the reachable one), `{{ x|cut:None }}` is
  `TypeError: replace() argument 1 must be str, not None`, and
  `_native.filter("cut", "abc", 5)` is `... not int`. The last of those is the one
  no template can express — it needs the seam directly — and it is the proof that
  the class and the message survive `filter_py_error` on the way out.

  **This is the filter that reads the coerced value's safety**, so it is the one
  whose corpus rows close the wrapper's coercion half (§D3): `cut`'s rows must
  include a plain `str`, a `mark_safe`d `SafeString`, **and a lazy `__proxy__`
  whose `str()` is a `SafeString`**. The body's `safe` is
  `matches!(&value, Value::Safe(_))` — the FFI has already coerced the value it
  hands over, so no host call is needed and the `__proxy__` row is what fails if
  the wrapper ever stops replacing the value the body receives. Reuse the
  module-scope `LAZY_SAFE_PROXY` in `tests/filter_cases.py` rather than building
  a second one.

  **Two of those three rows are controls and the third is the guard, which is
  why the set cannot be trimmed for speed.** With a `mark_safe`d `SafeString`,
  `was_safe` is already true, so handing the body `current` instead of `coerced`
  gives byte-identical output; with a plain `str` both values are unsafe, and
  likewise. The lazy `__proxy__` is the only one of the three where the pre- and
  post-coercion values differ in safety — the loop's own test is `False` on it and
  the wrapper's is `True` — so it alone fails when the replacement is removed. The
  other two exist to prove the ordinary paths still answer correctly, and deleting
  them would cost correctness coverage; deleting the proxy row would cost the
  guard.

  **The mutation was then run, and it is the measurement of §D3's claim rather
  than a restatement of it.** `run_filter` was changed to hand every body
  `current` while the marking half still asked the coerced value — one token, and
  the marking half deliberately left alone so the two halves could be told apart —
  then rebuilt and swept over the corpus. **Exactly one row of the 49 went red**,
  the guard: `cut/lazy-proxy-whose-str-is-safe`, `AssertionError: 'SafeString' !=
  'str'` from the type comparison, the text identical either way (which is the
  whole point — a corpus comparing `==` would have stayed green). **The other 48
  rows are blind to the mutation**, and that number is §D3's "unobservable until a
  body reads its own input's safety" measured: the wrapper's replacement half is
  invisible to every filter in Django's 57 except `cut`.

  One of those 48 was a comment of this plan's that the mutation falsified:
  `capfirst/lazy-proxy-whose-str-is-safe` **stayed green**, because `capfirst`
  never reads its input's safety, so nothing about which value its body received
  can reach its answer. The row observes the loop's **marking** half
  (`was_safe || coerced_is_safe` — only the second disjunct is true on a
  `__proxy__`), the same line `lower/lazy-proxy…` observes. Its comment claimed a
  second observer of the replacement half and has been rewritten from the
  measurement. The replacement half has **exactly one** observer in M4, and it is
  `cut`'s proxy row.
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
- `center` (`:431-438`) — `width = int(arg)` **before** the `width <= 0` early
  return, and then `f"{value:^{width}}"`. The trap is that **`str.center` is the
  wrong reference**: the filter uses CPython's format-spec `^`, which puts the
  extra space on the **right** (`left = marg // 2`), where `str.center` carries a
  `marg & width & 1` term that moves it to the left whenever `marg` and `width`
  are both odd. Measured against the pinned 6.0.9:

  | call | result | split |
  |---|---|---|
  | `"ab"\|center:5` | `' ab  '` | left 1, right 2 — **the filter** |
  | `"ab".center(5)` | `'  ab '` | left 2, right 1 — `str.center` |
  | `"ab"\|center:3` | `'ab '` | left 0, right 1 — the filter |
  | `"ab".center(3)` | `' ab'` | left 1, right 0 — `str.center` |

  An earlier revision of this bullet said the two rules were "the same today" at
  `width=5`. They are not, and the divergence sits exactly where a test written
  from the obvious rule is blind. **One corpus row must sit at a both-odd width**
  — a 2-code-point value at width 5 — and the early return is pinned separately
  (`width` 0 and negative, value unchanged), including the *order*, since
  `int(arg)` runs before the test. Rust's `format!("{:^width$}")` was measured to
  agree with Python's `^` on 11 inputs — nine ASCII, plus a 2-byte `é` and a
  combining `e`+U+0301 — and to pad by **code points**. **That agreement is real
  and the body still may not use it**, for the reason the next bullet gives: they
  agree on every value a small test would reach and not on the widths a template
  may legally ask for. The row's expected answer comes from the probe, never from
  the builtin's name.
- **The non-positive width, for all three padding filters, and the Rust width cap
  underneath it.** `center` guards explicitly — `if width <= 0: return value`
  (`:434-435`, after `int(arg)`) — while `ljust`/`rjust` (`:417-430`) have no
  guard at all and rely on `str.ljust`/`str.rjust` returning the string unchanged
  for any width below its length, negatives included. Three different routes,
  one answer: measured through the oracle, all three return the value unchanged
  at width `0`, `-1` and `-5`, empty string included. What the guard buys is the
  *error type*, and only for `center`, because it decides whether the width
  reaches the format spec at all:

  | filter | width | through `resolve` on the pinned 6.0.9 |
  |---|---|---|
  | `center` | `10**30` | `ValueError: Too many decimal digits in format string` |
  | `center` | `-10**30` | `'ab'` — the guard short-circuits, the spec never sees it |
  | `ljust` / `rjust` | either sign | `OverflowError: Python int too large to convert to C ssize_t` |

  So the **sign** of a huge width decides which error you get, or none, and only
  for `center`. And the builtin is the wrong reference one more time:
  `'ab'.center(10**30)` is an `OverflowError` where the *filter* is a
  `ValueError`, because the filter's positive branch is the format spec and not
  `str.center`.

  The Rust hazard sits under all three, and it is a **panic rather than a
  divergence**, which is why it is a body test and not a corpus row:
  `format!("{:^width$}", "ab", width = 65535)` is fine, `width = 65536` **panics**
  with `Formatting argument out of range`, and so does `width = (-5i64) as usize`
  (1.8e19). Rust's formatter caps the width at `u16::MAX`; CPython's format spec
  has no such cap and answers 65536 and 70000 happily (measured). A body that
  pads via the format spec therefore panics on a legal `{{ x|center:70000 }}` and
  on *every* negative width, so the three bodies pad manually
  (`" ".repeat(…)`) and each carries one test at width 65536 asserting the
  padded length — a `result.len()` assertion, not a 64KB literal — to pin that
  the format spec is not on the path.
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
  a four-component all-numeric arg (`"1:2:3:4"`, which gets past `int(x)` and
  raises `TypeError` from `slice(*bits)`, returning `value`).

  **The three catch arms are three rows, not one**, measured on the pinned
  6.0.9: `"1:2:3:4"` reaches `slice(*bits)` and is the `TypeError` arm;
  `"a:b:c:d"` raises `ValueError` at `int("a")` and never gets that far, so it is
  the `ValueError` arm; `5|slice:":3"` is the `TypeError` arm again but from the
  *subscript* rather than the slice call, and `KeyError` is unreachable from a
  sequence. A body that caught one arm and not the others passes the rows for
  the arm it caught, so each row is named for the arm it pins.
- `join` (`:611-621`) — `needs_autoescape`, `is_safe=True`, and the group's
  trap: on `TypeError` it returns **`value`, the unjoined list**, not `""`. With
  `autoescape=True` it is `conditional_escape(arg).join([conditional_escape(v)
  for v in value])`; with `False` it is a bare `arg.join(value)`. Result is
  `mark_safe`d. The corpus needs both `autoescape` states — **and it cannot
  express either one today.** See the harness note below.

  **A measured divergence, recorded rather than fixed: `join`'s result *type*
  when the separator carries `__html__`.** Django's `mark_safe` is a **no-op on
  anything with `__html__`** — `django/utils/safestring.py:74-75` is
  `if hasattr(s, "__html__"): return s` — so a `Markup`-shaped separator survives
  `join` as itself. markupsafe's `Markup` is the real-world instance: a `str`
  subclass with `__html__` that is deliberately **not** `SafeData` (the reason
  `conditional_escape` is a host call, `:1156`).

  ```text
    separator = MyMarkup(", ")    # str subclass; __html__ returns self
    join(["a", "b"], separator)   ->  MyMarkup     safe=False   (Django)
                                  ->  SafeString   safe=True    (ours)
    join(["a", "b"], ", ")        ->  SafeString   safe=True    (both)
  ```

  The text is identical, so §D5's safety half is the only thing that can see it —
  which makes this the one place in M4 where that half is *wrong* rather than
  merely unexercised. It is recorded rather than fixed because it is not reachable
  here: markupsafe is not installed, and no template can construct such an object
  (it takes a context variable holding a third-party `__html__` type). The fix
  belongs to M8 with the rest of the `__html__`/`Promise` protocol (`:158`,
  `:950`), not in `join`'s body — the divergence is `mark_safe`'s, and every
  `mark_safe` site would need the same treatment.

**Harness prerequisite for this group, found by reading the render path rather
than by hitting it: the corpus has no per-row context.** `scripts/filter_probe.py`'s
`django_answer` builds `Context({"x": value})` with defaults, and
`tests/test_filter_parity.py`'s `render_django` passes it no knobs at all, so
every row is scored under `autoescape=True, use_tz=<settings>`. That is fine for
Tasks 3 and 4 — no filter in them reads either — and it is fine for most of this
group, but `join` and `unordered_list` are `needs_autoescape` (`:1301`), and the
date/time groups are `expects_localtime`. So **the central integration for this
group adds a per-row `context` key** to `tests/filter_cases.py`, honoured by both
sides: `Context({"x": value}, autoescape=…, use_tz=…)` on the Django side
(`context.py:144` takes both) and the same two values into `_native.filter`,
which already takes them.

The key is absent from a row that wants the defaults, and the change is §11'd the
same way everything else is: `join` with `autoescape=False` must be **observed
failing for its intended reason** under the old fixed-context path — the row
would be scored under `autoescape=True` and answer the escaped join, which is a
different question rather than a crash, exactly the shape of the two harness
defects Task 4 exposed. Do not add the key before the row that needs it: an
unused knob is the `repr` pattern at the harness level, and this group's first
row with `autoescape=False` is what makes it observable.

**The same rule covers the other two capabilities this plan commits to that the
harness does not have yet** — the two *settings* states for `urlize`'s
`URLIZE_ASSUME_HTTPS` (Global Constraints) and the *membership* comparison for
the filters whose answer is chosen at random (`Review Focus 4`). Each is added
by the commit whose first row needs it, each observed failing for its intended
reason under the old path first, and none speculatively. What matters about all
three is the failure mode they prevent, and it is the same one both times it has
already happened: a row that is silently scoring a *different question* against
a green assertion.
- `random` (`:642-648`) — `random_module.choice(value)`, catching `IndexError` →
  `""`. **Review Focus 4**: the corpus row asserts membership, and the test says
  so in its name, because an equality row would be green 1 time in 57.
- `dictsort` (`:578-588`) / `dictsortreversed` (`:590-600`) — **one host call,
  `FilterHost::sort_by_property(value, prop, reverse)`**, not a resolver plus a
  sort. `_property_resolver` (`:540-575`) splits `arg` on
  `VARIABLE_ATTRIBUTE_SEPARATOR`, routes a float-convertible `arg` to
  `itemgetter` and everything else to a `__getitem__`-then-`getattr` chain whose
  except clause names **five** classes, and raises
  `AttributeError("Access to private variables is forbidden.")` for an
  underscore-prefixed bit — and the test is `"._" in arg or arg[0] == "_"`, so
  it is **not** only a *leading* underscore: `"a._b"` is refused exactly as
  `"_a"` is, measured. Splitting that across the FFI would mean Rust
  reimplementing Python's exception classification, and `sorted` over `Opaque`
  values is not expressible in Rust at all. **What Rust owns is the filter's own
  behaviour**: the `(AttributeError, TypeError) → ""` catch, which is a match on
  `Error::FilterHost { kind, .. }` — the taxonomy above is what makes that a one-line
  match rather than a string comparison.
- `unordered_list` (`:669-743`) — `needs_autoescape`, recursive, and the largest
  body in the group. Its `escaper` is `conditional_escape` or identity depending
  on `autoescape`, and its output indents with tabs. **The nesting decision is
  `FilterHost::as_nested_sequence`, not `iter_values`** — `walk_items` asks
  `isinstance(next_item, (list, tuple, types.GeneratorType))` and then `iter()`,
  and both halves are load-bearing (a `dict` iterates but is not in the triple, so
  the isinstance decides; a tuple is in the triple but is not a `Value::List`, so
  the host decides). Rust owns the pairwise walk, the `StopIteration`-as-loop-end,
  the two output formats and the recursion. The corpus needs **four** rows, all
  from the suite: `test_nested_multiple` (`:94`, four levels deep — a one-level
  case passes with the recursion missing), `unordered_list(item_generator())`
  (`:141`, whole value is a generator — this is what pins `iter_values` as
  `list(v)`), `test_nested_generators` (`:150`, an element that is a generator and
  must nest), and `test_ulitem` (`:122`, an object whose `__str__` is not its
  `repr`, which pins that the escaper goes through `to_str`).

**What Task 6's integration found, and the two corrections it makes to this
section.** All three are measurements on the pinned 6.0.9, taken while landing the
group; the first two were *wrong in this plan* and are corrected here rather than
quietly fixed in code.

1. **The `context` key's failure mode is *vacuous green*, not red.** The paragraph
   above predicted that a row scored under the old fixed-context path would
   "answer the escaped join … a different question rather than a crash". True, but
   it is worse than that: because **both** sides read the context, a dropped key
   makes both of them answer the escaped value and **agree**. Measured, with the
   key ignored: `join/autoescape-false-does-not-escape` gives Django `'a, &lt;b&gt;'`
   and ours `'a, &lt;b&gt;'` — a green row asserting nothing; and
   `unordered_list/autoescape-false-nested` likewise. So there is no red row to
   watch go green, and the key must be §11'd by *mutation or direct measurement*
   instead. This is the same class the paragraph itself names — a row silently
   scoring a different question — and it shows the class is not caught by
   observing a failing row, because the failure is agreement.
2. **A one-shot iterator cannot be a shared `value`.** A generator is consumed by
   whichever side reads it first, so a row holding one scores the *other* side
   against a spent iterator. Measured: `["a", (x for x in ["b"])]` nests under
   Django (`"\t<li>a\n\t<ul>\n\t\t<li>b</li>\n\t</ul>\n\t</li>"`) and, with the
   *same object* passed on, does not under ours; reversing the order flips which
   side is wrong. This produced a **false divergence report** against
   `unordered_list` mid-task — the filter was correct the whole time and the row
   was the bug. The corpus therefore gained `case_value` and a `value_factory`
   key, and the five generator rows use it. It is a distinct key rather than
   "call `value` if it is callable", because a callable is a legitimate input
   (`x|default:func`) that would otherwise be silently read as a factory.
3. **A corpus row cannot express an exception — and an error therefore has two
   homes, not one.** `resolve` propagates it rather than returning it, so a
   `CASES` row whose *point* is that Django raises would abort the comparison
   instead of making it. Nine candidate rows were measured and dropped for this
   reason, and their inputs became **body tests**: the raising rows of
   `first`/`last`/`random` (`first(5)`'s `TypeError`, `first({})`'s `KeyError`) and
   `join`'s `autoescape=False` non-str-separator `AttributeError`. Dropping them is
   nonetheless wrong — an exception is an **answer**, since the class is what a
   filter body's own `except` branches on — so those inputs also get
   `ERROR_CASES`, the second list in `tests/filter_cases.py`, where both engines
   must raise the same class **and the same message**. Its first two rows are
   `cut`'s `TypeError`s (`47edea3`).

   **Both homes stay, and the split is by who authors the message** — the
   reviewer's resolution of the question this section had left open. A message
   *passed through from CPython* is Python's on both sides, so the body test
   checks **propagation** (the class, never the message) and `ERROR_CASES` checks
   the wording against Django; the body test is also the only signal available to
   the agent writing the filter, because §10.4 gives an agent `cargo test -p
   template-core` and never `maturin develop`, so a Python-side row is invisible
   to it. A message *synthesised in Rust* (`cut.rs`'s `not_a_string`, because no
   host operation performs the `str.replace`) is checked **only** by
   `ERROR_CASES`, since a body test asserting the string the body also produces
   can only agree with it — so `cut`'s two body error tests are restatements to
   delete, to be removed when `cut.rs` and `tests/test_filter_parity.py` are next
   open in the same commit.

   This is the third form of one rule, now stated as the governing principle in
   `tests/filter_cases.py`'s module doc: **parity validates agreement, never
   correctness.** Shared inputs are invisible to it (a shared generator, or a
   dropped `context` key that moves both engines together); two runs that change
   together are invisible to it (hence the pinned literals); and values one side
   *composes* are invisible to it, because there is nothing on the other side to
   agree with. Each has arrived looking like a new problem.

**And the one body this section got wrong, found by the same measurement.** The
`if children:` rule above is **not** `!children.is_empty()`: Django tests the
*object*, and a generator object is truthy even when it yields nothing, so a
nested **empty** generator builds `<ul>\n\n\t</ul>` where a nested empty *list*
builds nothing — the same emptiness, two answers (measured
`unordered_list(["a", (x for x in [])])` is `"\t<li>a\n\t<ul>\n\n\t</ul>\n\t</li>"`).
A materialised `Vec<Value>` cannot tell the two apart, so `as_nested_sequence`
returns a `NestedSequence { items, truthy }` and the body tests `truthy`. It is
reachable from a template (`["a", (y for y in [])]` is a legal value) and it is
one corpus row: `unordered_list/empty-nested-generator-still-opens-a-ul`.

**§11 for this group.** The observation for the `context` key is the 2×2 above —
both sides agree on the escaped answer when the key is dropped, so the key's
detector is that measurement and not a red row. The central mutation, counted:

```text
  rows moved
      1   unordered_list: `if children.truthy` -> `!children.items.is_empty()`
          (reverts the fix; moves empty-nested-generator-still-opens-a-ul)
      1   slice: a lone component becomes the start
          (moves slice/single-component-is-the-stop: 'a' vs 'bcdef')
      1   join: element escaping forced on
          (moves join/autoescape-false-does-not-escape)
      2   join: TypeError returns "" instead of the value  <- the central one
          (moves both join/typeerror-returns-the-value rows, the group's trap)
```

No mutation moved 0 rows, so no local pin is needed; each mutation asserted its
pattern was found **and** the file's md5 changed before it ran, because a silent
no-op mutation in this project already produced one false "passed".

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
  `arg` here is already a resolved value. **The spec crosses the seam whole, and
  that is what closes Task 5's width cap on the precision axis**: Rust's
  `format!` panics with the same `Formatting argument out of range` past a
  runtime *precision* of 65535 (`Formatter` caps width and precision alike —
  measured), and `{{ x|stringformat:"70000d" }}` is a legal template Django
  answers with a 70000-character pad (measured: `"70000d"` → 70000 chars,
  `".70000f"` → 70002). But CPython performs the `%`, so no Rust side ever holds
  the precision and there is nothing to guard. Recorded because it is a *closed*
  route, not an open one — the next reader should find why it is safe rather than
  re-deriving it.
- `pluralize` (`:942-982`) — `arg="s"`; a `","` is prepended when absent; more
  than two bits returns `""`; `float(value) == 1` decides, falling back on
  `len(value)` for a `TypeError`. **Three arms, and the `len` fallback is only in
  the `TypeError` one** — an earlier revision of this bullet said a `ValueError`
  means "not a number, try `len`", and it does not: a `ValueError` falls through
  to `return ""` without asking (measured `pluralize("abc") -> ""` where a
  `len`-based body answers `"s"`, and `pluralize([]) -> "s"` where `len` is how
  it got there). `float(value)` is `to_float`; the arms are number-ish → compare,
  `TypeError` → `len` (whose own `TypeError` → `""`), `ValueError` → `""`.
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
  the group. **Its precision is the one live route to Task 5's `u16` cap**: unlike
  `stringformat`, the Rust half assembles the decimal text, so if it reaches for
  `format!("{:.*}", p, x)` a legal `{{ x|floatformat:70000 }}` panics where Django
  answers 70002 characters (measured), and the 200-digit refusal does **not**
  catch it — `floatformat("1.5", 70000)` has `len(digits) + abs(exponent)` of 3.
  Assemble by string/repetition, not through a runtime precision.
- `filesizeformat` (`:899-940`) — `formats.number_format` plus `ngettext`, so it
  needs both host calls, and the corpus needs `USE_THOUSAND_SEPARATOR` in both
  states.

**What Task 7's design round found, and the three corrections it makes to this
section.** Written before the bodies, because the first changes what the group's
largest body is made of.

**1. The sentence in §D2 about `floatformat` is false, and §D2's own rule is
what refutes it.** `defaultfilters.py:200` is

```python
prec = max(getcontext().prec, prec)
```

— a direct read of the global decimal context, three lines after
`Decimal(1).scaleb(-abs(p))` at `:194`. So "`floatformat`'s rounding, exponent
arithmetic and 200-digit refusal are Rust" is wrong on two of its three nouns,
and §D2 had already said so: **"what a `decimal.Context` is set to" is named in
the rule itself as host state.** The correction is to §D2's *example*, not its
rule, and §D2 has been amended in place.

**The subtler half is that the context is observable through the answer**, which
is why this is not bookkeeping about where a line lives. Two measured routes:

* **Below the boundary `scaleb` clamps, and it clamps to a zero.** For large `p`
  the "exponent" is not `1E-p`: `Decimal(1).scaleb(-abs(p))` silently returns
  `Decimal('0E-' + Etiny)`, with `Etiny = Emin - prec + 1`. Measured at `prec=1`
  with `p=1000027`: `scaleb` gives `Decimal('0E-999999')`, so `quantize` rounds
  at **999999** places and the digit assembly is 1000001 chars — while the
  answer is **1000029**, because `formats.number_format(number, abs(p))` pads the
  fraction out to `abs(p)` decimals. The final width is `number_format`'s; the
  rounding position is the context's. A Rust port that computed `10^-p` for real
  would round at `p` where Django rounds at 999999.
* **Past `2*(Emax + prec)` it raises, out of a `try`.** `scaleb` raises
  `InvalidOperation` at `abs(p) = 2*(Emax + prec) + 1` and clamps below it, and
  the boundary is exact to the integer and moves with `prec` — measured
  `prec=1 -> 2000000` clamps / `2000001` raises, `28 -> 2000054/2000055`,
  `1000 -> 2001998/2001999`, `100000 -> 2199998/2199999`, each matched by a
  transition at exactly that integer. End to end: **`floatformat(1.5, 2000054)`
  is 2000056 chars at the default `prec=28` and raises `InvalidOperation` at
  `prec=1`** — a template input Django accepts, whose answer is a property of
  thread-local state. (The exception *class* moves too: at the boundary itself
  the operation reports `Overflow` where a Rust reimplementation would say
  `InvalidOperation`. Not worth reproducing, and not corpus-reachable, since a
  raising row cannot be a corpus row.)

So `floatformat`'s **decimal engine is host**, and it crosses the seam whole:
parse, `scaleb`, the `prec` computation and `quantize` are none of them a
function of the filter's arguments. What stays Rust is the control flow, the
`arg`-suffix dispatch, the 200-digit refusal, the `not m and p <= 0` integer
branch, the digit assembly, the `.` insertion and the sign.

**2. The host contract this adds.** Six methods, all of them for `floatformat`
and `filesizeformat` — the rest of the group needs none of them, and the one
method the wave-2 half does need is in **6** below — the two `%` methods because
measurement showed `filesizeformat`'s operands are not shapes `percent_format`
can take (below), and `decimal_quantize` gaining a fourth field for the reason in
**4**:

```rust
/// `Decimal(str(value))`, then the `Decimal(str(float(value)))` retry
/// (`:152-159`). `Unparseable` is the source's `return ""`.
fn decimal_parse(&self, value: &Value) -> Result<DecimalParse, Error>;

pub enum DecimalParse {
    Parsed(ParsedDecimal),
    Unparseable,
}

pub struct ParsedDecimal {
    /// The `Decimal` itself, as an `Opaque`, for `decimal_quantize`. It is a
    /// handle rather than a re-parse so `str(text)` happens once.
    pub value: Value,
    /// `str(text)` — the payload of all three `return input_val` arms
    /// (`:163`, `:177`, `:182`). A plain `str`, and **not** safe.
    pub input_val: String,
    /// `d.as_tuple()`'s parts, so the refusal and the assembly need no second
    /// round trip. Most-significant digit first.
    pub sign: bool,
    pub digits: Vec<u8>,
    /// `None` for `"F"`/`"n"`/`"N"` (inf and nan). That is the `TypeError` arm
    /// at `:168-170` **and** the reason `int(d)` raises at `:180`; both mean
    /// `return input_val`, so the body tests this once and never reads a
    /// meaningless `integral`. `None` also covers an integer exponent too large
    /// for `i64`, where `:176` refuses for the same reason.
    pub exponent: Option<i64>,
    /// `not (int(d) - d)` — the source's `m` (`:180`) — and `false` when that
    /// raised, which is exactly when `exponent` is `None`.
    pub integral: bool,
}

/// `:194-205`: `exp = Decimal(1).scaleb(-abs(p))`, the `units`/`prec`
/// computation and `d.quantize(exp, ROUND_HALF_UP, Context(prec=prec))`.
/// Returns `rounded_d.as_tuple()`.
///
/// One method rather than three, because every step reads or is bounded by the
/// thread-local context; a Rust caller computing its own `prec` is the bug this
/// seam exists to prevent. `places` is `abs(p)`; `integral` is **`not m`** —
/// whether the *unrounded* value is a whole number — passed rather than
/// recomputed so the source's single `int(d) - d` stays single. (`:198` is
/// `-tupl[2] if m else tupl[2]`, so `m` truthy is `integral == false`; this is
/// the one place the seam's polarity is inverted from the source's own name.)
fn decimal_quantize(
    &self,
    decimal: &Value,
    places: u64,
    integral: bool,
) -> Result<DecimalTuple, Error>;

pub struct DecimalTuple {
    pub sign: bool,
    /// The **digit values**, not a count: `:206` is
    /// `[str(digit) for digit in reversed(digits)]`.
    pub digits: Vec<u8>,
    pub exponent: i64,
    /// `bool(rounded_d)` — the second half of `:210`'s `if sign and rounded_d`.
    /// A separate field from `integral`, which asks whether the *unrounded*
    /// value is a whole number (`not m`); see **4**.
    pub truthy: bool,
}

/// `formats.number_format(round(value, 1), 1)` whole (`:911-912`).
///
/// Not `number_format` with a Rust-rounded argument: Python's two-argument
/// `round` on a **float** rounds against the exact binary value, not the decimal
/// one. Measured `round(0.35, 1)` is `0.3` (the exact value is
/// `0.34999999999999997780…`) where `Decimal(str(0.35)).quantize(Decimal("0.1"),
/// ROUND_HALF_EVEN)` is `0.4`. A Rust `round` over the decimal text — what the
/// rest of this crate does — answers `0.4` where Django answers `0.3`.
fn filesize_number_format(&self, value: f64) -> String;

/// `int(value) % int(arg) == 0` — `divisibleby` (`:854-858`), which has **no**
/// `try`: `arg == 0` is `ZeroDivisionError`, a non-numeric operand is
/// `ValueError` or `TypeError`, and all three escape. Rust compares the result
/// to zero, so `IntValue::Big` is never zero and only `Small(0)` matters.
fn int_modulo(&self, a: &Value, b: &Value) -> Result<IntValue, Error>;
```

`add`'s first arm needs no new method: `int(value) + int(arg)` **is** Python `+`
on two ints, so it is the existing `add_values` over the two coerced
`IntValue`s, with both `to_int` calls made in source order (`value` first, so a
`ValueError` from it wins). That needs an `IntValue -> Value` conversion —
`Small(n) -> Value::Int(n)`, `Big(text) -> Value::BigInt(text.into_boxed_str())`
— because `add_values` takes `&Value`. The alternative, an `int_add` on
`IntValue`, is worse for the reason `add_values` exists at all: `IntValue::Big`
is canonical decimal *text*, so `Big % Big` and a carrying `i64 + i64` have no
Rust expression and the arithmetic is host territory either way.

`filesizeformat` therefore needs **three** host calls, and not the four an
earlier revision of this section claimed: `filesize_number_format`,
`percent_format` and `percent_mapping`. `avoid_wrapping` is **verified pure** and
stays Rust: `django/utils/html.py:502-507` is `return value.replace(" ", "\xa0")`
on a `str`.

**The four-call version was wrong about `percent_format`, and measurement says
why.** It read as though `ngettext` and `gettext` were the two other calls and
`percent_format` applied the `%` to their answers — but `filesizeformat`'s two
`%` operations have operand shapes its old signature could not take, and one of
them it cannot take at all:

* **The byte branch's operand is a dict, which no `Value` can be.** `:925` is
  `ngettext("%(size)d byte", "%(size)d bytes", bytes_) % {"size": bytes_}`.
  Measured on the pinned tree: `"%(size)d bytes" % {"size": 1023}` is
  `"1023 bytes"`, while the same expression over a **list**, a **tuple**, an
  **int** or a **str** raises `TypeError: format requires a mapping`. `classify`
  interns a dict as an `Opaque`, so Rust can neither build the operand nor pass a
  `Value` in its place. That is `percent_mapping`, and the docstring `%(size)d`
  is why it takes a name rather than the raw dict text.
* **The unit branches' format string is a translation, not a literal.** `:927` is
  `gettext("%s KB") % filesize_number_format(bytes_ / KB)` — the `%` applies to
  the **translation**, and a catalog may move the `%s`. So the method had to stop
  taking `stringformat`'s `"%" + spec` suffix and take the whole format string:
  under English the two agree exactly (measured `("%" + "s KB") % "1.0"` is
  `"1.0 KB"`, same as `"%s KB" % "1.0"`), which is precisely why an
  English-only corpus could never catch the difference. Nothing implemented
  `percent_format` yet, so generalising it cost nothing; `stringformat` now builds
  `format!("%{}", to_str(arg))`, which is the source's own `"%" + str(arg)`.

So `ngettext` and `gettext` are still called by `filesizeformat` — for the format
string — and `percent_format`/`percent_mapping` are the operator. Six new
methods, two of them the `%`'s two operand shapes.

**A seventh is a live question, and the division is what raises it.** The five
unit branches divide by a power of two (`:927-935`) — **outside** the `try`, so an
`OverflowError` there escapes while the same class from `int()` above is caught.
Measured:

```text
  int(float("inf"))       OverflowError: cannot convert float infinity to integer
  int(float("nan"))       ValueError: cannot convert float NaN to integer  -> caught, "0\xa0bytes"
  10**400 / 2**50         OverflowError: integer division result too large for a float
  float(10**400)          OverflowError: int too large to convert to float  <- a different message
  number_format(inf, 1)   'inf.0'
```

`filesize_number_format` takes an `f64`, so the body must convert
`IntValue::Big(text)` itself — and a Rust parse of a >308-digit decimal answers
`inf` rather than raising. **The three-call seam therefore cannot express the
source's behaviour for `|bytes_| > f64::MAX`**, and the predicted divergence is
`'inf.0\xa0PB'` where Django raises. The `inf`/`nan` rows do **not** depend on
this: they are `int()`'s, and `to_int` already propagates them (measured through
`int_modulo`, whose `OverflowError` crosses the FFI with its class intact). Only
the >308-digit case does. Two candidate fixes: a seventh method doing
`int(value) / (1 << shift)` whole, or folding the division into
`filesize_number_format`'s signature. The first leaves that method's doc intact —
a doc whose whole content is that `round` on a **float** rounds against the binary
value, not the decimal one. Decide when the body lands and the divergence is
measured rather than predicted, not before.

**3. What measurement says about the individual bullets above**, three of which
the plan got wrong and the rest of which it got only partly:

* **`pluralize`: the `len` fallback is only in the `TypeError` arm.** The bullet
  above says "a `ValueError` means 'not a number, try `len`'". It does not — a
  `ValueError` falls through to `return ""` and never asks. Measured
  `pluralize("abc") -> ""` (a `len`-based body answers `"s"`), `pluralize("") ->
  ""`, `pluralize([1]) -> ""` (that one *is* `len`), `pluralize([]) -> "s"`,
  `pluralize("1") -> ""` (the singular suffix, not a failure). The corpus needs a
  **non-numeric string** row for this; no list row reaches it.
* **`get_digit`: the `except` names one class, so a `TypeError` escapes.**
  `int(None)` is a `TypeError` where the source catches `ValueError` only —
  measured `get_digit(None, 1)` raises. Reachable (`{{ x|get_digit:1 }}` with `x`
  unset), and a **body test**, not a corpus row. Also measured: `get_digit(123,
  9) -> 0` (the `IndexError` arm), `get_digit(123, 0) -> 123`, `get_digit("-123",
  1) -> 3`.
* **`floatformat`'s two `arg` traps.** The suffix dispatch reads `arg[-1]`
  **before** any parse, so an empty-string arg is `IndexError: string index out
  of range` and escapes — measured `floatformat(6666.6666, "")` raises (a body
  guarding with `is_empty()` would answer where Django raises). And `int(arg)`'s
  `ValueError` is `return input_val`: measured `floatformat(6666.6666, "x") ->
  "6666.6666"`, plain `str`. The measured suffix rows are `"2g" -> "6,666.67"`,
  `"2u" -> "6666.67"`, `"g" -> "6,666.7"`, `"gu"`/`"ug" -> "6666.7"`.
* **`floatformat`'s integer branch is the *integral* test, not `exponent < 0`.**
  `not m and p <= 0` asks whether the value is a whole number — measured
  `floatformat(1.0, -1) -> "1"` and `floatformat("1.0", -1) -> "1"`, both with
  exponent `-1`. A body testing the exponent would take the quantize path here.
* **`floatformat` rounds `ROUND_HALF_UP` on the exact decimal**, not banker's:
  measured `floatformat(0.5, 0) -> "1"` and `floatformat(2.5, 0) -> "3"`, where
  `round(2.5)` is `2`.
* **The 200-digit refusal is Rust, its boundary is `digits + |exponent| <= 200`
  inclusive, and the no-arg boundary row is what earns the safety check.**
  Measured, with the arm that answered:

  ```text
    input                    arg    arm              text-len   safe
    '1'+'0'*199  (sum 200)   none   integral :185      200      True
    '1'+'0'*199  (sum 200)   2      quantize :213      203      True
    '1'+'0'*200  (sum 201)   none   input_val :176     201      False
    10**199      (sum 200)   none   integral :185      200      True
    10**200      (sum 201)   none   input_val :176     201      False
  ```

  **The `none` row is the one that needs the flag, and the reason is an
  off-by-one port.** At sum 200 the selected arm and the `input_val` arm emit
  *the same 200 characters* — `"%d" % int(d)` is the digits, and `input_val` is
  `str(text)`, which for an integer string is the same text — so a body whose
  cutoff read `>= 200` for `> 200` sends that input down the `input_val` arm and
  **only the flag moves**. `==` cannot see it; §D5's safety half is the only
  thing that can. The `arg=2` row cannot serve that purpose: the quantize arm
  appends `.00`, so its 203 characters differ from `input_val`'s 200 and a
  mis-routed body is caught by text alone. An earlier revision of this bullet
  cited that 203 without saying which argument produced it, which is what made
  it read as a contradiction of the no-arg measurement.

  `"1e300"` and `"1e-400"` refuse (sum 301 and 401); `inf`/`-inf`/`nan` reach the
  `int(d)` arm and return `"inf"`/`"-inf"`/`"nan"` unmarked; `"abc"` and `None`
  are the `Unparseable` arm and answer `""`. The exponent being `"F"`/`"n"`/`"N"`
  makes the sum 0 rather than raising, which is why the refusal does not fire for
  the non-finites.
* **And the unmarked arms are reached by a *float*, so a body keying the flag off
  the operand's type is wrong.** Measured: `floatformat(1e308) -> "1e+308"`
  unmarked (`Decimal("1e+308")` is sum 309, so `:176` fires on a float),
  `floatformat(float("inf")) -> "inf"` and `floatformat(float("nan")) -> "nan"`
  unmarked (`:182`'s `int(d)` overflow) — while `floatformat(34.0) -> "34"` is
  safe. The two outcomes are not separable by "was the value a string".
* **`default`/`default_if_none` return the argument *object***, so their safety
  is the argument's and not the filter's: measured `default(0, "x")` is
  `SafeString("x")` — the quoted constant was `mark_safe`d by `resolve` — where
  `default_if_none(0, "x")` is the int `0`. A body that built a new value
  instead of returning the operand loses that flag. Both are `is_safe=False`.
* **`yesno`'s short-arg arm returns the *value*.** Measured `yesno(None, "y") ->
  None`, not `""`. The default-arg row is a `gettext("yes,no,maybe")` lookup, so
  the corpus must state its i18n state; measured untranslated `yesno(True, None)
  -> "yes"`.
* **`add`'s three arms are all corpus-reachable, but not as this section's
  examples suggest.** Arm 1 `add(1, 2) -> 3` and `add(1.5, 1) -> 2` (so it really
  is `int()` on both); arm 2 `add("a", "b") -> "ab"` — **not** two lists, because
  an `arg` is a template constant and a template has no list literal; arm 3
  `add({}, 1) -> ""` and `add([1], 2) -> ""`, where `value + arg` is the caught
  `TypeError`. The `bare except Exception` is what makes arm 3 `""`.
* **`stringformat`'s refusal arm** is measured `stringformat("abc", "d") -> ""`,
  `(None, "d") -> ""`, `(42, "z") -> ""`, and `([1], "s") -> "[1]"` — so the
  refusal is the `%` operator's, and `str(arg)` alone is Rust's.

* **Two filters, the same `is_safe=True`, and bodies that differ on safety.**
  `floatformat` **`mark_safe`s in the body** (`:185`, `:213`), so its two normal
  returns are `SafeString` while its three `return input_val` arms are plain
  `str` — measured `floatformat(34.0) -> '34'` safe, `floatformat('NaN') ->
  'NaN'` and `floatformat(1.5, 'abc') -> '1.5'` unsafed. `filesizeformat` does
  **not**: its `avoid_wrapping` is a plain `str.replace`
  (`django/utils/html.py:502-507`) and its input is an `int`, which is not
  `SafeData`, so every one of its rows is an unsafed `str` — measured
  `filesizeformat(1024) -> '1.0\xa0KB'`, unsafed. A body mirrored from the other
  gets the safety wrong in both directions, and §D5's safety half is the only
  thing that can see it.
* **`yesno` and `pluralize` can raise from the `arg`'s *type*, and the two
  classes differ for the same input.** Measured `yesno(True, 5)` raises
  `AttributeError: 'int' object has no attribute 'split'`, where `pluralize(1, 5)`
  raises `TypeError: argument of type 'int' is not a container or iterable` out
  of `"," not in arg`. The revision of this bullet that said a Rust body must
  **mint** the class is superseded, and the supersession is an improvement: with
  `comma_bits` the class is CPython's own and crosses the seam intact, which is
  strictly better than `cut.rs`'s synthesis — and that synthesis stays only
  because no host operation performs `str.replace`, which is not true here. See
  **6** for the seam and for why the split cannot be Rust's. The rows live in
  `tests/filter_cases.py`'s `ERROR_CASES`, not among the `CASES` above: a `CASES`
  row cannot carry an exception (`resolve` propagates it and the comparison
  aborts), so the inputs whose *answer* is an exception get their own list, where
  both engines must raise the same class **and the same message**.
* **The deferred `floatformat(1.5, "-999999")` question is closed: there is no
  anomaly.** Measured, the quoted-constant row and the bare-int row are
  **identical** — 1,000,001 characters each, same digest — because both reach
  `p = int(arg)` and what fixes the rounding position is the `2*(Emax + prec)`
  clamp, not the argument's shape. The row is kept out of the corpus for
  **size**, not for disagreement, and the same path is covered by the small
  negative rows (`-3`).

**4. `DecimalTuple` needs the rounded value's truthiness, and `integral` does not
cover it — a review finding.** `:210` is `if sign and rounded_d`, a truthiness
test on the value **after** the quantization, three lines after `m` asked the
same question of the value **before** it. The two disagree exactly where the sign
is decided, and the corpus can see it because it only takes a plain template:

```text
  floatformat("-0.0001", 2)  ->  '0.00'    m=Decimal('0.0001') truthy
                                           rounded=Decimal('-0.00') falsy   ← minus dropped
  floatformat("-0.004",  2)  ->  '0.00'    same shape
  floatformat("-0.5",    0)  ->  '-1'      rounded=Decimal('-0.50') truthy  ← minus kept
```

A `DecimalTuple` of `(sign, digits, exponent)` would emit `-0.00` for the first
two, so the tuple carries `truthy` too. It is not derivable from the parse side:
`ParsedDecimal::integral` is **`not m`**, so it is `false` for `-0.0001` — the
unrounded value is not whole — while the rounded `Decimal('-0.00')` is falsy.
Reading `integral` as `m`'s own truthiness is the same mistake in the other
direction, and a body that made it would emit `-0.00`.

The same review round resolved the `scaleb` disagreement that had been left open,
and the resolution is worth recording because it is the seventh instance of the
same pattern: **the two readings were two right answers to two unstated
questions, and the unstated question was the sign.** `Decimal(1).scaleb(n)`
raises `InvalidOperation` for `|n| > 2*(Emax + prec)` in either direction, but
*inside* that boundary the directions differ — positive overflows upward
(`Overflow` at `prec=1, n=2000000`) where negative clamps to `Etiny`
(`Decimal('0E-999999')` at the same magnitude). `floatformat` calls
`scaleb(-abs(p))`, always the negative arm, so the clamp measurement is the one
that describes the filter and the `Overflow` reading described a direction it
never takes. It was **not** an interpreter difference; the 3.9 hypothesis would
have been a wrong fix for a real disagreement.

**5. The wave-1 dispatch found one more type question, and `is_tuple` is the
seam's first addition after Phase A.** Measuring `stringformat`'s tuple branch
before dispatching showed `FilterHost` could not ask `isinstance(value, tuple)`
at all (`:289`), and the branch is load-bearing rather than defensive:

```text
  stringformat((1, 2, 3), "s")  ->  '(1, 2, 3)'    through the tuple test
  "%s" % (1, 2, 3)              ->  TypeError: not all arguments converted
  stringformat([1, None], "s")  ->  '[1, None]'    and needs no test at all
```

CPython unpacks a **tuple** operand and treats every other sequence as a single
value, so the source tests for `tuple` and not `Sequence` — and a Rust
`matches!(value, Value::List(_))` would be exactly backwards. A tuple crosses the
FFI as `Value::Opaque` (`classify` interns it as one *deliberately*, so that
`as_nested_sequence` and `get_index` keep list semantics off it), so this is a
`__class__` question and §D2 puts it on the host.

**§10.4's admissibility rule, stated by the reviewer: an addition is admissible
when it comes with the measurement that forced it, and inadmissible when it comes
with a reason.** `is_tuple` came with the rows above, so it is traceable to the
same evidence a test would carry. The alternative was dispatching an agent into a
guaranteed stop-and-report — the gap found by the party with the least context,
under pressure to ship a module, with a §D7 workaround as the path of least
resistance.

It is a **bare** predicate and is not fused with anything, unlike
`as_nested_sequence` below it: that fusion is justified because its `isinstance`
and its `iter()`/`TypeError` guard are *inseparable* — splitting them would make
Rust reimplement Python's exception classification — whereas `is_tuple` has no
exception interplay and `to_str` already stands beside it. Fusing would buy one
FFI crossing and cost a method whose name describes a conditional rather than an
operation. The trait is **41 methods, 17 of them non-`Result`**.

**§11 for this group.** The central mutation is on the seam itself and it is the
one §11's "moves 0 rows" rule exists for:

```text
  rows moved
      ?   floatformat: the host's `prec = max(getcontext().prec, prec)` -> `prec`
          (the host stops reading the context — and no corpus row can see it,
           because the corpus runs at the default prec=28 and no row sits near
           the 2*(Emax+prec) boundary, and a raising row cannot be a corpus row)
      ?   floatformat: ROUND_HALF_UP -> ROUND_HALF_EVEN
          (moves floatformat/rounds-half-up-on-the-exact-decimal)
      ?   floatformat: `truthy` -> `true` in `decimal_quantize`
          (moves the two negative-rounds-to-zero rows above — and it is the
           mutation that says whether the field earns its place)
      ?   pluralize: the ValueError arm folded into the TypeError one
          (moves pluralize/non-numeric-string-is-not-len-based)
```

If the first moves 0 rows, that is the signal and the remedy is a **local pin**,
not a weaker mutation: a Rust unit test over a `TestFilterHost` whose
`decimal_quantize` is scripted cannot see it either, so the pin has to be a real
host call with `getcontext().prec` set to 1 and a `places` past the boundary —
i.e. the same shape as the measurement above. The counts are recorded in the
group's commit, and each mutation asserts its pattern was found **and** that the
file's md5 changed before it runs.

**6. The wave-2 dispatch found the argument split two filters share, and it is
the seam's second addition after Phase A.** `comma_bits(&value, prepend_comma)`
is three lines of `defaultfilters.py`:

```python
if prepend_comma and "," not in arg:
    arg = "," + arg
return arg.split(",")
```

— `pluralize`'s argument handling (`:965-967`) with `prepend_comma = true`, and
`yesno`'s (`:879`) with `false`. It is **one method rather than three
primitives** (`contains`, `concat`, `split`), and what forces the fusion is
§D7.1's rule: splitting them apart would put Python's exception classification
on the Rust side, because *which* of the three raises, and with which class, is a
function of the argument's type:

```text
  yesno(True, 5)          AttributeError: 'int' object has no attribute 'split'
  pluralize(1, 5)         TypeError: argument of type 'int' is not a container or iterable
  pluralize(1, [1])       TypeError: can only concatenate str (not "list") to str
  pluralize(1, [","])     AttributeError: 'list' object has no attribute 'split'
  pluralize(1, object())  TypeError: argument of type 'object' is not a container or iterable
```

The `[1]`/`[","]` pair is what settles it, and it is why five rows for one
expression are not redundant: `[","]` is the only argument that gets **past** the
`in` — a membership test a list can answer — and dies at the `.split`, so the two
rows carry different classes from the same "not a string" input. A `concat`
split out as its own operation would hand Rust a value whose next use it cannot
see, which is exactly the classification this seam exists to keep on the Python
side. Both templates that reach the raising arms are real: Django 6 parses a bare
filter argument as a literal, so `{{ x|yesno:5 }}` and `{{ x|pluralize:5 }}` are
legal syntax and hand the filter the `int` 5.

**The flag is measurement-forced, not a convenience, and the first two rows are
why.** Both filters perform the same three operations on the same input class and
disagree about the class they raise, because `pluralize` runs the `in` first and
`yesno` goes straight to the `split`: measured, an `int` argument is a
`TypeError` through one and an `AttributeError` through the other. So the flag is
the only thing selecting between two measured classes, and `false` does not mean
"skip a step" — it means this filter's source has no such line.

The rest of the wave's seams were all pre-existing, which is worth stating
because it is the opposite of the `floatformat` half: `pluralize` reaches
`to_float` and `len`, `yesno` reaches `gettext` and `is_true`, `default` reaches
`is_true`, and **`default_if_none` reaches no host at all** — a pure
`Value::None` test, and the only M4 body whose proof of purity is that it
answers correctly under `NoFilterHost` rather than panicking into it. All four
are `is_safe=False`, so none calls `mark_safe`.

The trait is **42 methods, 25 of them `Result`**.

**7. The wave landed, and it found a defect in the seam that no body test could
have found.** The four bodies, 56 `CASES` rows and 5 `ERROR_CASES` rows are in;
the stub count went 28 → 24.

Three rows disagreed on the first run, all `pluralize`, all numeric or
alphanumeric *strings* — and they were one bug in `float_of`
(`template-py/src/filters.rs`), which was `self.object(value)?.extract::<f64>()`.
A pyo3 `f64` extraction is `PyFloat_AsDouble`, i.e. `__float__`; a `str` has no
`__float__`, so it raised a **`TypeError`** where Python's `float()` parses the
string and raises a **`ValueError`** for a bad one. `int_of` beside it had always
called the `int` *type*, so only the float half was wrong.

The class is the whole contract here, which is why this was a wrong answer and
not a missed one: `pluralize` catches `(ValueError, TypeError)` and **branches
differently** on the two — `ValueError` falls through to `""` without asking
`len`, `TypeError` asks. So `{{ "abc"|pluralize }}` answered `"s"` (the `len` arm,
`len("abc") == 3`) where Django answers `""`, and `{{ "2"|pluralize }}` answered
`""` (`len("2") == 1`, the singular suffix) where Django answers `"s"`.

**Why only the corpus could see it.** §10.4 gives an agent `cargo test -p
template-core` and never `maturin develop`, so the fan-out's `pluralize` body
tests script a `TestFilterHost` — they pin that the body *dispatches* on the
class it is handed, and they were right about that. The class it is handed is
the FFI's, and only a real host call over a real `str` can observe it. This is
the case §10.4's rule exists for, and it is worth recording that the rule
produced a finding rather than only a constraint.

**And the coincidence is a property of the *string*, not a quirk of `"1"`.**
An input is blind to the defect exactly when

```text
  len(v) == 1   <=>   the float arm answers the singular suffix
```

since those two selections are what the arms make, and a string on which they
agree is one where the defect has nothing to show. **"The float arm answers
singular" is itself two conditions, and saying it as one cost a correction:**
it is `float(v) == 1` *or* `float(v)` raising **`ValueError`**, because the
source `pass`es the `ValueError` and falls out to `return ""`, which is the
singular suffix (`defaultfilters.py:969-978`). A rule phrased as "`float(v) ==
1.0` and `len(v) == 1` agree" therefore mispredicts *every* `ValueError` row —
measured, 6 of 23 shapes, and the six are exactly the `ValueError` ones. The
four cells, each with the corpus row that occupies it:

```text
                      len(v) == 1                len(v) != 1
  float(v) == 1       agree      "1"            differ   "+1"  "1e0"  "1."  " 1 "
  float(v) raises     agree      "a"            differ   "abc"  ""
  float(v) != 1       differ     "2"  "0"       agree    "-1"  "10"  "nan" "inf"
```

The discriminating set splits both ways — `"2"` and `"0"` take the plural arm
that `len` would not, and the singular half takes the singular arm that `len`
would not — and the singular half is the interesting one, because those are
precisely the strings `float()` accepts and a character count cannot see: a
sign, an exponent, a trailing point, surrounding whitespace. `"1"` and `"a"`
earn their places as the *coinciding* twins of `"2"` and of `"abc"`: without
them a reader cannot see that it is the pair — which arm, at which length —
that decides, and a set holding only the failing rows would suggest the arm was
the whole story.

**The corpus also needed an argument no template can spell.** `[1]`, `[","]` and
`object()` are the three of `comma_bits`'s five rows that settle the fusion, and
a template has no list or object literal — so `CASES`/`ERROR_CASES` gained
`arg_variable`, whose object the token reaches as a bare name resolved through
the context. Measured, and it is the reason the harness refuses a `str` there:
`FilterExpression("x|pluralize:argvar", p).filters[0][1][0][0]` is
`lookup=True` where `x|pluralize:"s"` is `False`, and `resolve` marks only the
`False` case (`base.py:803-806`). A `str` under `arg_variable` would therefore
reach Django unmarked and ours marked — two engines given *different inputs*,
which is the raw-token defect this corpus has already been fixed for once.

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
  `re.sub(r"[-\s]+", "-", …)`. **The whole of `_slugify` is the host's**
  (`slugify`), because NFKD is a large Unicode table and §D2's rule covers it, and
  because the `.lower()` inside it is Final_Sigma — so the host call is one
  method rather than a normalise plus a `str_lower`. There is deliberately **no
  `unicode_normalize_nfkd` method**: `slugify` is its only would-be caller, and a
  second uncalled method is the `repr` mistake one along. **The two regexes and
  the ordering are Rust's**, and the `\w` class is M1's Python-faithful one,
  which is why `slugify("á")` and `slugify("²")` differ. The corpus needs a slug
  that reaches the host's lowercasing: `slugify("ΟΣ")` → `"ος"`, not `"οσ"`.

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
