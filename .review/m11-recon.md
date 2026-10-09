# M11 recon — interpose `defaulttags`, `defaultfilters`, `loader_tags`

Written before the plan, so the plan can argue from measurements rather than
from §10.3's one-line row. Every number here is re-runnable from
`.review/m11-measurements/` (see that directory's `README.md`), and every claim
below says which instrument produced it.

The milestone's row is:

> **M11** | Interpose `defaulttags`, `defaultfilters`, `loader_tags` | bulk of
> `syntax_tests` and `filter_tests`, **and `test_url`**

---

## 1. The headline: the Rust filter layer is reached by nothing in the 1547

`filter_tests` is **454 rows** (measured, `unittest.discover` over
`.django/tests/template_tests/filter_tests/test*.py`) and every one of them is
green today — against **Django's** filter bodies. Measured with the shim
installed (`m11_measure_filter_path.py`):

    django.template.base          -> python/django_rust_templates/_impl/base.py   (ours)
    django.template.defaultfilters-> .django/django/template/defaultfilters.py    (Django's)
    Parser.find_filter('lower')   -> django.template.defaultfilters.lower
    FilterExpression filter       -> <function upper at 0x10749eae0>   (Django's)

and our transcription of `FilterExpression.resolve` is upstream's text
(`_impl/base.py:790-819`, the loop at `:808`), whose loop calls `func(obj, *arg_vals)` on whatever
`parser.find_filter` handed it. So the 454 rows say **nothing** about
`crates/template-core`'s 57 filter bodies — they are the "acceptance blind spot"
M4 recorded, still open three milestones later, and M11 is the milestone that
closes it.

**What M11 is therefore worth, stated as a measurement rather than a hope:**
after this milestone, 454 rows that currently exercise Django's filters exercise
ours, and `syntax_tests`' tag rows move from Django's node classes to our
transcription of them. That is the second half of the sentence §10.3's M11 row
leaves implicit — it names the gate modules and not what changes inside them.

**And the corollary is the M9 finding-3 shape, which the plan has to answer.**
A *verbatim* transcription of all three files — the pin plus a marker, nothing
declared — also keeps all 1547 green and moves the meter. It is not obvious from
any count which of the two the milestone shipped. So the plan owes an instrument
that distinguishes them, the way M9 owed four; the cheapest honest one is a row
that asserts a built-in filter reached through a *rendered template* arrives at
`_native.filter`, by counting calls (the `differential.py` census already counts
`_native` calls, so this is an extension rather than a new instrument).

## 2. The routing question, and why it is the whole design

`_native.filter`'s contract is not "run a filter body". Its own docstring
(`crates/template-py/src/filters.rs:1569`): *"Run one built-in filter over one
value: `FilterExpression.resolve`'s loop."* It applies `expects_localtime`
before the body, `@stringfilter`'s coercion, the `autoescape` keyword, and
`mark_safe` after — the four things Django does *around* a body, measured as
Rust's own loop (`stringfilter_filters()` is 29 of the 57, `filter_flags(name)`
is the `(is_safe, needs_autoescape, expects_localtime)` triple).

That makes the design a fork, and the two arms fail differently:

* **Route in `FilterExpression.resolve` by name.** Wrong, and the tree already
  has the measurement: `_impl/parser.py:535-549` builds `self.filters` from the
  *handle* `find_filter` returned, because `{% load %}`'s
  `self.filters.update(lib.filters)` (`base.py:670`) rebinds a name to a
  caller's function. `tests/test_filter_rebinding.py` is the sandwich. A second
  lookup by name at render would run our body for a rebound filter.
* **Route by the identity of the handle.** `check_filter` already carries the
  handle from `find_filter`, and `host.py`'s tag predicate is the same shape one
  level over: a filter is Rust's iff it *is* the callable
  `defaultfilters.register.filters[name]` holds. Decided at parse time (where
  the handle is chosen) or at render time (where the loop is) — the plan picks,
  and the pick is the load-bearing decision the reviewer should see.

Three constraints on either arm, all measured:

1. **`resolve`'s loop must not run twice.** If a Rust-backed callable is invoked
   from Python's loop, `stringfilter` coerces twice and `mark_safe` fires twice.
   So the loop is either Python's *or* Rust's for a given filter, never both.
2. **The flags must be the same objects Django's are.** `FilterExpression.resolve`
   reads `func.expects_localtime`, `func.needs_autoescape` and `func.is_safe`
   *off the function*, so a delegate has to carry them — which is what
   `filter_flags` exists for, and what M4's "FilterFlags round-tripping against
   the pinned registry" pinned.
3. **`register.filters[name]` must stay identity-meaningful.** `Library.filter`
   stores the function itself, so the identity test is available without a new
   table; if M11 introduces a delegate, `register.filters[name]` becomes the
   delegate and the predicate must be written against *that*.

## 3. Size and shape of the three files

Measured on the pin (`.django` @ 6.0.9):

| file | lines | classes | module-level defs |
|---|---|---|---|
| `defaulttags.py` | 1687 | 25 | 26 |
| `defaultfilters.py` | 996 | 0 | 59 |
| `loader_tags.py` | 370 | 4 | 4 |

For contrast, M9's `base.py` is 1181 lines and M10's nine files total 3203 — so
M11 is the largest single transcription milestone so far, and the only one whose
subject is *three* files at once.

## 4. What is already Rust's, and therefore what transcribing these files can and cannot change

* **The 25 built-in tags.** Rust compiles them; the host declines them by
  identity (`host.py:43-88`, `django_own_tags()` = `defaulttags.register.tags` ∪
  `loader_tags.register.tags`). So a verbatim `defaulttags.py` changes the
  *registry's owner* and the *node classes' owner*, not the parse. The `do_*`
  bodies in our transcription become unreachable through the shim for those 25
  names — reachable only if a `{% load %}`ed library rebinds the name, at which
  point the loaded callable runs, not ours.
* **The `do_*` bodies are still needed**: they are what `Parser.tags` merges and
  what the identity predicate reads. Deleting them would delete the predicate.
* **`loader_tags`' three tags** are Rust-compiled since M7 (same predicate), and
  its four classes render in Python — `BlockNode.render` builds a node at render
  time (§7's note at spec:530), and M7's record already measured that the
  render-time logic stays Python.
* **The filters.** Nothing of the render path is Rust's today (§1).

## 5. Import graph and the M9 universe-capture hazard

Measured module-level imports that matter:

* `loader_tags.py:6` — `from .base import Node, Template, TemplateSyntaxError,
  TextNode, Variable, token_kwargs`
* `defaulttags.py:20` — a multi-line `from .base import (...)`; `:41`
  `from .defaultfilters import date`; `:42` `from .library import Library`
* `defaultfilters.py:27` — `from .base import VARIABLE_ATTRIBUTE_SEPARATOR`;
  `:28` `from .library import Library`

Two consequences:

1. **These are the modules M9's hazard (b) was measured on.**
   `tests/test_base_parity.py::test_the_modules_that_copied_it_hold_ours` exists
   because `defaulttags.py:37` and `django/templatetags/i18n.py:5` bind
   `render_value_in_context` at import. Interposing `defaulttags` and
   `loader_tags` makes that binding ours by construction rather than by
   interpreter-start ordering — which is the *durable* half of this milestone,
   and it is invisible to the 1547 for the same reason it was invisible before.
2. **`library.py` enters the graph and is M13's.** `defaulttags` and
   `defaultfilters` both import it. Interposing them does not require
   interposing it (the shim serves Django's `library`, whose own `from
   django.template.base import ...` resolves to ours), but the plan should say
   so explicitly rather than leave the reader to wonder whether M11 has quietly
   grown a fourth module.

## 6. The gate, read through §10.3's vacuity rule

The rule (§10.3, beside the milestone table): a gate naming a `.django/tests/`
module is a gate only for the milestone that *interposes the behaviour it
exercises*.

* `filter_tests` (454) — **not vacuous at M11, and vacuous at every milestone
  before it.** Today it runs Django's filters; after M11 it runs ours. This is
  the strongest gate the project has had, and the one to be most suspicious of:
  454 rows turning real at once is 454 chances for the routing to be wrong.
* `syntax_tests` — its tag rows currently exercise Django's node classes with
  our parser. After M11 they exercise our transcription. Not vacuous.
* `test_url` — moved here from M12 by M5's Scope section *because* it imports
  `URLNode` from `django.template.defaulttags`, so it becomes real signal in the
  commit that interposes that module. Not vacuous.
* `test_include_cache` (M7's near-miss) — **still vacuous at M11 for the reason
  M7 recorded**: the shim does not interpose `loader_tags`... except that M11
  does. So M7's stated reason for the row being vacuous *expires* at this
  milestone, and the row should be re-read then: if it becomes real, M7's
  `test_inheritance_gate.py` and the `.django` row are two instruments for one
  question and the plan should say which is which rather than leave both.

## 7. Open questions the plan must answer, not inherit

1. **Where the built-in decision is made** — parse time (the descriptor carries
   it, next to the handle) or render time (an identity test in `resolve`'s
   loop). §2 above is the measurement set for both.
2. **What `_impl/defaultfilters.py` is** — verbatim pin+marker, or declared
   delegating bodies. A verbatim file leaves 59 bodies unreachable and the
   routing entirely in `base.py`; delegating bodies move the decision into the
   module and put 59 declared regions on the record. The plan picks one and
   says what the other costs.
3. **Whether `defaulttags`' 26 `do_*` bodies are transcribed verbatim** even
   though the 25 Rust-owned ones are unreachable, or declared as
   delegation-to-Rust stubs. (Verbatim is the smaller diff; unreachable code is
   the larger lie.)
4. **What the new rows are.** The meter moving 14 → 11 is not evidence of
   anything (M9 finding 3). At minimum: a call-count row proving `filter_tests`
   reaches `_native.filter`, and a row that a `{% load %}`-rebound filter does
   **not** reach it.

---

## 8. The review of this recon, appended

`PASS`, with three additions and one correction of emphasis. The headline
reproduced independently, including `find_filter('lower') is
defaultfilters.lower` and `FilterExpression.resolve` defined at
`_impl/base.py:790` — so §1 is not a reading of the guard's table but a
measurement of the live interpreter. The fork is the right call.

The additions, each re-measured here before it was written down (a claim that
names a Python builtin or a line number gets measured, whichever of us makes
it):

1. **§2's fork has a consequence the recon does not draw, and it changes a
   task.** A chain can mix arms — `{{ x|lower|mycustom }}` after a
   `{% load %}` — so the delegation is per-filter and never whole-loop, and
   upstream's `resolve` applies `needs_autoescape`, the `stringfilter` coercion
   and `mark_safe` around *every* arm. Therefore
   **`_impl/base.py`'s `FilterExpression.resolve` stops being upstream's text**,
   and M11 declares its first render-path region in `base.py`.
2. **That region sits outside clause (4)'s reach, measured.** Re-measured here
   on the closed tree: `COMPARED_PATH["base.py"]` holds 29 entries, its
   `FilterExpression.*` ones are `__init__` (744-788) and `args_check`
   (827-844), and `FilterExpression.resolve` **is not among them**, because the
   compared path measures the parse harness and `resolve` is render-time. The
   guard permits the region correctly and silently, so the entry itself has to
   say it is under-protected — the `utils.py`-zero discipline, not a new clause.
3. **The routing table must be named.** `defaultfilters.register.filters`, read
   at the module the shim serves, never `parser.filters` — which is the table
   that reads natural at the call site and is post-merge. This is
   `django_own_tags()`'s recorded defect one level over: the predicate's first
   version snapshotted `parser.tags` *after* `Parser.__init__` merged the
   engine's builtins, so a caller's `OPTIONS["builtins"]` entry compared equal
   to the snapshot and its `for` was silently ignored. Re-measured:
   `register.filters` is 57 entries and `register.filters["lower"] is
   defaultfilters.lower` is True.
4. **The instrument pair is right and incomplete** — both proposed rows pass if
   the loop runs twice, which is §2's constraint 1 and M5's `Host::repr` shape
   (a seam with a caller and no discriminating input). `escape` discriminates
   (`escape("&")` → `&amp;`, `escape(escape("&"))` → `&amp;amp;`, measured), and
   the `stringfilter` table gets compared **by name**: 29 on each side today
   (`grep -c '^@stringfilter'` on the pin, and
   `_native.stringfilter_filters()`, both measured), which a count cannot
   distinguish from 29 matching names.

And the caution on §6: M7's vacuity reason expiring means the row must be
re-read, not that either instrument may be retired on the strength of "now
covered". Which of the two is redundant is a measurement — break the behaviour
and see which fails.

