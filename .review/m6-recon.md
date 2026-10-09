# M6 recon — `Library`, the extension host, `{% load %}`, `querystring`

Measured against `.django/` @ 6.0.9, 2026-10-09, before the M6 plan is written.
Every claim below is a command's output or a named source line. This file exists
so the plan can be argued from measurements rather than from the spec's prose,
which it corrects in three places.

## The three spec errors this recon found

`docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md`:

1. **Line 989, the M6 gate: "`Library.add_tag`/`add_library` last-registration-wins
   semantics".** `Library` has **no** `add_tag` and **no** `add_library`. Its
   entire public surface is `tag`, `tag_function`, `filter`, `filter_function`,
   `simple_tag`, `simple_block_tag`, `inclusion_tag` (`library.py:29-292`,
   measured by reading the class body). `add_library` is a method of **`Parser`**
   (`base.py:669`). The spec contradicts itself: lines 538-539 of the same
   document get this right ("consumed via `add_library` and **not stored**";
   "There is **no** `add_libraries`"). The behaviour the gate argues for is real —
   see below — but the sentence names two methods that do not exist, and a plan
   built from it would look for a seam that is not there.

2. **`LibraryParseError` does not exist.** `grep -rn LibraryParseError .django/`
   → 0 hits. The library-loading exception is `InvalidTemplateLibrary`
   (`library.py:13`), raised by `import_library` (`library.py:476-492`). (The
   name does not appear in our docs either, so this is recorded to stop it
   arriving.)

3. **§5.2's "arbitrary attributes must work", argued from
   `loader_tags.do_block:230-233`.** The citation is correct — measured,
   `do_block` does `parser.__loaded_blocks.append(...)` and, on `AttributeError`,
   `parser.__loaded_blocks = [block_name]` (`loader_tags.py:228-234`) — but it is
   **M7's** requirement, not M6's. No M6 fixture writes an arbitrary attribute to
   the parser: the only fixture that writes to the parser at all is
   `custom.py:398`'s `parser.extra_data["extra_data"] = "CUSTOM_DATA"`, which is a
   namespaced key inside the real dict, not a new attribute. Measured:
   `grep -rn 'parser\.[a-zA-Z_]* *=' tests/template_tests/templatetags/` → one
   hit, the `extra_data` line.

   The consequence for M6's plan is a **scope boundary**, and one subtlety worth
   carrying to M7: `do_block`'s `except AttributeError` branch means the handle
   must *raise* `AttributeError` for an attribute it does not have, rather than
   returning `None`. A proxy that invents attributes silently skips the branch
   that builds the list.

## What the suite actually tests — the M6 surface, counted

| module | `def test_` | what it pins |
|---|---|---|
| `template_tests/test_library.py` | 28 | the `Library` registry **alone** — no `Engine`, no `Template`, no `Parser`, no render. Registration API and its `ValueError` strings |
| `template_tests/test_custom.py` | 39 | user tags/filters end to end: `filter`, `simple_tag`, `simple_block_tag`, `inclusion_tag`, `{% load %}` errors |
| `syntax_tests/test_load.py` | 11 | `{% load %}`, both forms, and its two error messages |
| `syntax_tests/test_querystring.py` | 28 | `{% querystring %}` — the built-in that *is* a `simple_tag` |

`test_library.py` is the earliest gate available: it reaches registration and
nothing else, so it can go green before the render path exists.

**Fixtures that define user tags/filters** (`template_tests/templatetags/`):
`custom.py` (48 registrations — 3 filters, 22 `simple_block_tag`, ~19
`simple_tag`, 2 `@register.tag`), `testtags.py` (`echo`, `other_echo`, `upper`),
`bad_tag.py` (compile-time and render-time `RuntimeError`), `tag_27584.py`
(**re-entrant parse**: `parser.parse(("endbadtag",))` then
`delete_first_token()`, `tag_27584.py:8-9`), `subpackage/echo.py`
(`simple_tag echo2`), `inclusion.py` (25 `inclusion_tag`, no filters).

## The behaviours M6 must reproduce, measured

### `Parser.add_library` is the only merge, and it is last-wins

`Parser.__init__` (`base.py:502-524`) sets `tags`/`filters` empty, then merges
**only `builtins`** (`base.py:522-523`). The `libraries` argument is *stored*
(`base.py:521`) and not merged — measured directly:

```
Parser([], libraries={"custom": lib}, builtins=[...]).tags["comment"] is lib's shadow  → False
p.add_library(lib)                                                                     → True
```

So a library reaches the tag table **only** through `{% load %}` →
`parser.add_library` (`defaulttags.py:1125,1130`) → `self.tags.update(lib.tags)`
(`base.py:669-670`). Because `builtins` merge first and libraries merge later,
**a loaded library shadows a built-in of the same name** — that is the spec's
gate, and it holds, by a route that involves neither `Library.add_tag` nor
`Library.add_library`.

The same `update` **rebinds** a filter name — measured, `filters["upper"]`
changes identity across `add_library` — which is exactly the hazard
`Host::check_filter`'s doc records. This is why that seam's return type must
become a handle in M6: our materialisation happens after the whole parse, so a
`{% load %}` anywhere in the template would otherwise retroactively rebind an
*earlier* token's filter.

**No test in `.django/tests/` pins the shadowing behaviour.** Searched for:
`register.(tag|filter|simple_tag|…)(<builtin name>)`, `add_to_builtins`,
`add_library` (one hit, `test_parser.py:166`), and collision/redefine word
searches. The closest thing is library-*name* precedence in
`template_backends/test_django.py:62-105` and the E003 check in
`check_framework/test_templates.py` — both about `Engine.libraries`, i.e. dotted
paths, not tag names. **So the spec's M6 gate is a test we must write, and the
suite will not catch a regression in it.**

### `{% load %}` — Django's own, and it is small

`defaulttags.load` (`defaulttags.py:1102-1131`) uses `token.contents.split()`, the
**raw** split and deliberately not `split_contents` (comment at `:1117-1118`).
Two forms: `{% load a b %}` loops `find_library` + `parser.add_library`
(`:1128-1130`); `{% load x y from lib %}` builds a fresh `Library()` subset
(`load_from_library`, `:1078-1099`) and adds that (`:1120-1125`). It returns a
`LoadNode` whose `render` is `""` (`:390-394`).

Two error messages, both pinned by `test_load.py`:
- `"'%s' is not a registered tag library. Must be one of:\n%s"` — `find_library`,
  `:1069-1075`, with the names **sorted** and newline-joined.
- `"'%s' is not a valid tag or filter in tag library '%s'"` — `load_from_library`,
  `:1092-1098`.

`{% load %}` is a tag that **mutates the registry the host owns** (§5.2). That is
the M6 decision `load.rs`'s stub defers: Rust compile function calling a host
`add_library`, or host-owned outright. Measured, `find_library` and
`load_from_library` both need `parser.libraries`, which is a Python dict of
`Library` objects — so the *lookup* is Python's either way; the question is only
who calls `add_library`.

### `querystring` — a `simple_tag` whose errors are raised at render time

`@register.simple_tag(name="querystring", takes_context=True)`
(`defaulttags.py:1286-1287`), body `:1325-1349`. It is `takes_context=True`,
unbounded `*args, **kwargs`, reads `context.request.GET` when `args` is empty,
and raises `TemplateSyntaxError` **from inside render** (not at compile time) for
a non-mapping positional or a non-string key. Its tests are
`syntax_tests/test_querystring.py` (28).

`querystring.rs`'s stub already states M6's obligation exactly: parsing it needs
`SimpleNode` in `NODE_CLASSES`, a host handle to the Python callable, and
`parse_bits` driven by that callable's signature. A bespoke node would fail
parity because Django's tree holds a `SimpleNode` and the comparison is by class
name.

### The `ParserHandle` contracts, each pinned by a named test

- **Re-entrant parse** — `tag_27584.py:8-9`, driven by `tests.py:125,136`.
  `simple_block_tag` is the same shape at `library.py:211-212`. This is the
  contract that forces interior mutability: Rust must never hold `&mut Parser`
  across a host call.
- **`extra_data` is a real Python dict** — written at `custom.py:398`, asserted at
  `tests.py:148-155` (`t.extra_data["extra_data"] == "CUSTOM_DATA"`).
- **`extra_data` exists on the parser always, and on the `Template` only after a
  successful parse** — corrected after reading the tree, because the first
  version of this bullet named the wrong site. `Parser.__init__` sets
  `self.extra_data = {}` (`base.py:514`), so the parser never lacks it. The
  assignment to the template is in **`Template.__init__`**, not
  `compile_nodelist`: `nodelist = parser.parse(); self.extra_data =
  parser.extra_data` **inside the `try`** (`base.py:198-200`), so a parse that
  raises leaves the *template* without the attribute. Consumers tolerate that
  with `getattr(template, "extra_data", {})` (`engine.py:194`).
  `test_partials.py:171` (`test_template_without_extra_data_attribute`) pins the
  **consumer** side — it passes a mock template lacking the attribute and
  expects `TemplateDoesNotExist` — so the **producer** contract (the assignment
  being inside the `try`) is pinned by no test in the checkout, and a regression
  there is invisible. M6 must pin both halves itself. §5.2's
  `parse(source) -> (NodeListRef, ParserHandle)` is satisfied trivially in this
  plan's shape: the shell *constructed* the parser, so it already holds the
  handle and needs nothing returned.
- **Arbitrary attributes** — `do_block`'s `__loaded_blocks`, M7 (see error 3
  above). **Out of M6's scope, but the `AttributeError` semantics are not**
  negotiable when M7 arrives.

### Registration must preserve the decorated function

`test_custom.py`'s `TagTestCase.verify_tag` (`:40-43`) asserts `__name__`,
`__doc__` and arbitrary `__dict__` attributes survive registration, and
`test_library.py` adds `functools.lru_cache`'s `__wrapped__`
(`test_inclusion_tag_wrapped`). The flags `expects_localtime`/`is_safe`/
`needs_autoescape` are set on **both** the function and its `unwrap`-ed inner
one (`library.py:85-92`) — the same three flags `FilterFlags` already carries.

## Scope for M6, decided from the above

**In:** the `Library` registry (Python-owned, per §6, so: **verified through the
engine, not reimplemented** — `test_library.py` is a gate on behaviour we must not
break); the extension host's `has_tag`/`call_tag` made real in the shell; the
`check_filter` return becoming a handle; a wire form for a host-owned node;
`ParserHandle` with re-entrant parse, `extra_data` and the failure-path return;
`{% load %}`; `querystring`; `parse_bits`'s five error messages.

**Out, and named so the boundary is testable:** `{% block %}`'s arbitrary parser
attributes (M7); `inclusion_tag`'s *rendering* — `InclusionNode.render` renders a
template by name, which is loader machinery (`.django/tests/.../inclusion.py`'s
25 registrations and `test_custom.py`'s `InclusionTagTests` are therefore M7's
end-to-end, though M6 may register them). `test_custom.py`'s 39 will not all go
green in M6; the plan must say which ones it claims rather than asserting the
module.

## Open question the plan must answer

Whether `{% load %}` is a Rust compile function calling a host method, or a host
tag outright. Measured input: both error paths need `parser.libraries` (Python
dict of `Library`), and `load_from_library` builds a `Library` in Python. The
`LoadNode` it returns is a Django node the materialiser would have to know. This
is the one genuine design choice in M6 and it belongs in the plan's architecture
section, argued from those measurements rather than from preference.

**Answered: host-owned.** `load` leaves `REGISTRY`. See the plan's §A1. The
`LoadNode` it returns is not a problem the materialiser has to know about,
because §5.2 already provides for it — a host-owned node crosses as an opaque
handle (the plan's §A3).

## Follow-up measurement, 2026-10-09: `querystring` is host-owned too, and `parse_bits` is not ported

`querystring.rs`'s stub says parsing it needs "a host handle to the Python
callable, and `parse_bits` driven by that callable's signature". The callable is
reachable — `inspect.getclosurevars` on the registered `compile_func` returns
`func`, `params`, `varargs`, `varkw`, `defaults`, `kwonly`, `kwonly_defaults`,
`takes_context`, `function_name`, because `simple_tag` computes them once at
registration (`library.py:120-128`) and closes over them; and
`compile_func.__dict__["__wrapped__"] is func`, set by the `@wraps(func)` at
`library.py:185`.

**Reachable is not the same as ours to drive.** Driving `parse_bits` from Rust
would mean re-deriving, in Rust, everything `simple_tag`/`simple_block_tag`/
`inclusion_tag` do around it, because the values live in a closure and not on an
object:

- the closure's **variable names** become a contract with a pinned
  implementation detail of `library.py`;
- `simple_block_tag` derives `tag_params` from `params` + `takes_context` at
  compile time and raises two `TemplateSyntaxError`s doing it
  (`library.py:186-203`);
- it computes `end_name = end_name or f"end{function_name}"` (`:181-182`) and
  parses the body **before** `parse_bits` (`:206-212`);
- all three split the `as <var>` target before `parse_bits` (`:174-178`).

That is a reimplementation of `Library` in Rust, against §6, which assigns
`Library` to Python. And the payoff would be nil: the five `parse_bits` error
strings are *more* strongly gated by running Django's own function through the
host — the corpus compares the raised message either way — than by a port that
could drift.

**So: every `Library`-registered tag is host-compiled, `querystring` included.**
`querystring` leaves `REGISTRY` with `load`; `parse_bits` is not ported in M6 and
`SimpleNode` needs no `NODE_CLASSES` entry (the host node covers it). §D1's move
of `querystring` from M5 to M6 is vindicated — it needs the extension host — and
the mechanism is the host seam rather than a Rust compile function.

`builtin_tag_names()` therefore drops from 24 to 22, and
`builtin_tag_stubs()` from `['load', 'partial', 'partialdef', 'querystring']` to
`['partial', 'partialdef']`.

## What the host registry must *not* contain at M6

`block`, `extends`, `include` are in `parser.tags` (they are Django's builtins)
and are not in `RUST_OWNED`, so a naive `has_tag` would hand them to Django.

**This section's first version gave the wrong reason, and the reviewer falsified
it by reading the code.** It said `{% extends %}` would skip the ordering check
"because `Parser.extend_nodelist` … a line our parse never runs". Our parse runs
it: `crates/template-core/src/parser/mod.rs:392-418` is a full port of
`extend_nodelist`, `must_be_first && contains_nontext` and Django's message
included, called from `Parser::parse` at `:218`, `:252` and `:304`. A host tag's
descriptor reaches `:304` like any other block node.

The real mechanism is one level down and *smaller*: `NodeDesc.must_be_first` is
documented "Not carried on the wire" (`desc.rs:369`) and defaults to `false`
(`:407`), so a host-built node arrives with the flag cleared, the check runs, and
it passes. Measured: `ExtendsNode.must_be_first is True`, `BlockNode` and
`IncludeNode` are `False`, and `{{ x }}{% extends "a" %}` raises "must be the
first tag in the template" under stock Django.

**The exclusion survives, on a different argument** — and the distinction
matters, because "the check never runs" implies a `bool` cannot fix it:

**M6 must not newly accept a tag it cannot render.** Today `{% extends %}` raises
`Invalid block tag` — an honest "not yet". Let it reach the host and M6 accepts
it at parse time while `ExtendsNode.render` still needs `render_context` and
`Template._render` (M7, M9). The divergence would move from loud to silent. Same
for `{% block %}` (`do_block` writes `parser.__loaded_blocks`,
`loader_tags.py:228-234`) and `{% include %}` (`render_context` cache keyed by
template identity).

So `ShellHost.has_tag` **excludes all three at M6**, leaving them exactly as they
are today. Task 7 records for M7: remove the exclusion, carry `must_be_first`
with the host node (one `bool`), and note that M6's design already gives M7 its
arbitrary-attribute requirement for free — the handle handed to a compile
function *is* Django's `Parser`, so `parser.__loaded_blocks` works with no proxy
and no `AttributeError` emulation.

## The stranded list is a detector, not a backlog

`unmigrated` says how much naming is left. The stranded list says something the
other counts cannot: an unquoted row whose every citing file is already
shorthand-free will never be requoted by any pass, so if it is wrong it stays
wrong and green. That is not residue — it is the only instrument that fires on
anchored-but-wrong rows cited exclusively from files that have finished.

It fires when a *file* finishes, which is why its size says nothing. The whole
history so far, as entries rather than as a size that has sat in the high 70s
since the first file finished:

| entered at | file that went shorthand-free | rows that entered | read? |
| --- | --- | --- | --- |
| `0876b22` | `template-py/src/filters.rs`, `template-core/src/filters/mod.rs` | `html.py:246`, `html.py:412-415`, `defaultfilters.py:38-52`, `:48`, `:558-570` | 5 read; **2 wrong** (`:246` is a blank line; `:412-415` is `wrapping_punctuation_openings`, not `trim_url`) — fixed in `0876b22`, the three correct ones requoted in `a73dccb` |
| `a3798f3` | `template-core/src/parser/resolve.rs` | `base.py:1014`, `:959-1029`, `:963-995`, `:972`, `:999`, `:999-1029` | 6 read; **5 wrong** (`959-1029` ends on a blank line; `963-995` runs past the arms it claims; `999` is not the callable arm's guard; `999-1029` misses two of its three branches and ends past the handler; `972` is right for the predicate but was cited for a `raise` at 980) — fixed in `c24d657` |
| the `lib.rs` `InvalidOperation` commit | `template-py/src/lib.rs` | `base.py:109-110`, `base.py:972-976`, `smartif.py:54-56` | 3 read; **0 wrong** — and the reason is worth keeping: `lib.rs`'s citations had already been read in context by its own naming pass, so by the time they were stranded they had been looked at once. This batch is the counter-example that shows the detector's ratio is about *which pass read the row first*, not about the bucket |
| `363140f` | `python/django_rust_templates/_impl/parser.py` | `base.py:514`, `base.py:697-701`, `base.py:771-774`, `base.py:804-810`, `defaulttags.py:323`, `defaulttags.py:909-911`, `engine.py:194`, `smartif.py:115-116` | 8 read; **3 wrong** (`697-701` is inside `filter_raw_string`, a regex — `compile_filter`'s quoted `return` is 676; `771-774` and `804-810` each end one line past the construct they name) — fixed in `1546c36`. The other five are right, and `697-701` was cited from **two** files |
| `02348ff` | `crates/template-core/src/filters/urlize.rs` | `defaultfilters.py:493`, `html.py:492` | 2 read; **0 wrong** — both had just been read in context by the naming pass that rewrote the sentences citing them |
| `3f5a7fa` | `crates/template-core/src/parser/tags/regroup.rs` | `base.py:75`, `defaulttags.py:1352-1419` | 2 read; **1 wrong** (`1352-1419` ends two blank lines past `regroup`, whose last statement is 1417) — fixed in `061990a`. `base.py:75` is `VARIABLE_ATTRIBUTE_SEPARATOR = "."`, right |
| `436747d` + `9eb488d` | `crates/template-core/src/parser/tags/url.rs` | *none entered* | **0 rows read, 0 wrong** — the first batch that stranded nothing at all, and the mechanism is worth keeping. `url.rs`'s five rows were all **quoted** by the naming pass, and a quoted row is never in the stranded bucket; the one unquoted row it had (`defaulttags.py:1514-1586`) was pruned by the correction commit (it is now `1514-1584`) before it could enter. So this batch neither confirms nor refutes the "clean batch" prediction — it never reached the list |
| `93bc8ba` | `crates/template-core/src/filters/urlizetrunc.rs` | *none entered* | **0 rows read, 0 wrong** — the second batch to strand nothing, and the same mechanism: the module's three anchors were `defaultfilters.py:386`, `:389-400` and `:399`, all already **quoted** by `urlize.rs`, `host.rs` and `filter_cases.py`, and the one row it added (`defaultfilters.py:398`) is cited by the pass that added it. Two batches in a row now strand nothing, and neither is evidence for the "clean batch" prediction — both are evidence that a file whose rows are *all* quoted cannot feed the list, which is the list's own limit, not the file's quality |
| `b8f72be` + `b5d05f7` | `crates/template-core/src/parser/tags/ifchanged.rs` | `defaulttags.py:1023-1073` | **1 row read, 1 wrong** — and it is the first entry the list has caught since the ten blank-ended rows, so it is worth its own note (below). Corrected to `1023-1062` in `b5d05f7`, which requotes the row, so it did not stay on the list |
| `d09f40c` + `3f0aac0` | `tests/test_materialise.py` | `base.py:1036`, `defaulttags.py:262`, `engine.py:62-63` | **3 rows read, 1 wrong** — `engine.py:62-63` is over-wide by one line (the claimed `template_builtins` is set at 63; 62 sets `self.builtins`, which the claim never mentions, and `template_libraries` at 61 is not in the span either), fixed in `98587b3`. The other two are right: `base.py:1036` is `token = None` at class level, `defaulttags.py:262` is `IfChangedNode.__init__`. All three stayed unquoted, so they remain on the list — see below |

Thirty-one rows have passed through; thirteen were wrong or cited for the wrong
line. (Two batches, `url.rs` and `urlizetrunc.rs`, stranded none — see the
table.) Eight were wrong in a *span's* end (`base.py:959-1029` ends on a blank
line, `:963-995` runs past the arms it claims, `:999-1029` misses two of its
three branches, `html.py:412-415` is a neighbouring `@cached_property`,
`base.py:771-774` / `base.py:804-810` each end one statement past the construct
they enumerate, `defaulttags.py:1352-1419` ends two blank lines past its
function, and `defaulttags.py:1023-1073` ends eleven lines past its function,
inside the next one), four were wrong in the line itself (`html.py:246` is
blank, `base.py:999` is not the guard it is cited as, `base.py:972` is right for
one of its two sites and wrong for the other, and `base.py:697-701` is a regex
rather than the `compile_filter` its sentence is about), and **one is the
thirteenth shape: over-wide at the *start*** (`engine.py:62-63`). That one is
worth the separate clause because the other twelve can all be described as "the
span reaches past what the sentence names", which suggests a rule about ends;
`engine.py:62-63` shows the same defect can be a line at the front, where a
detector looking for overruns will not look. All thirteen came from a file whose
citations had never been read in context, and the span ones are invisible to a
check that only asks whether some line inside the span exists.

`defaulttags.py:1023-1073` is the interesting one, because
it is the first defect the stranded list has caught since the ten blank-ended
rows — the other twelve came from a naming pass or from the blank-end sweep.
Its end line, `"\n".join(sorted(parser.libraries)),` at 1073, is *real code*, so
the blank-end detector could not fire on it; the overrun is invisible until you
look at what 1073 belongs to, which is `find_library`'s `raise`, eleven lines
past `ifchanged`'s `return`. That is the list's whole value stated as one case:
the detectors that read *within* a span cannot see a span that is simply too
long, and only the file finishing makes it worth reading.

The order of the sweep decides when each row becomes visible: a row cited only
from files swept last surfaces last. So the list still has to be read once, at
the end — but the end is when the last row becomes visible, not when the
cleanup is done.

**The list is not the only instrument, and the naming pass has one of its own.**
Naming a file forces each sentence citing it to be read in context, which
surfaces claims no bucket holds and no check sees. Three so far, all counts:

- `parser.py:385` said the render path reads `context.template.engine` "in five
  places" and then enumerated six (fixed in `1546c36`).
- `urlize.rs:54-56` said `handle_word` uses `safe_input` "in exactly four
  places: the two `if autoescape and not safe_input:` guards … and the two …
  branches" when the tree has one guard and two branches (fixed in `cfa14e1`).
- `defaultfilters.py:417-430`, "the **three** padding filters" — the span held
  two, because it stopped before `center`'s decorator at 431 (`ljust` is
  417-421, `rjust` 424-428, `center` 431-438; the corrected row ends at 438).
  This one is the useful case, because the *count* was the half that needed no
  line measured: a sentence that says three and a span that holds two disagree
  on their face. It is checked in as `6d124dc`, and until now it was recorded
  only in that commit message, under no heading that would lead anyone back to
  it.

All three were held back from their naming commits, because a naming commit
never moves a number. None is reachable from the stranded list: the lines
already named a file, so the pass that caught them is the one that rewrote the
line beside them, not the one that requotes a row.

The class has a searchable signature — a number word next to an enumeration of
Django lines — so it is rule-findable, like the relative-line grep, and it has
already produced three without being run as a sweep.

**Naming a file also reads its *numbers*, and that found four more.** `url.rs`'s
nine shorthands were all into `defaulttags.py`, and reading each claim against
the tree turned up four wrong ones on lines the naming pass was rewriting
(corrected in `9eb488d`):

- `defaulttags.py:1514-1586` -> `1514-1584` — two blank lines past the function;
- `:1568-1571` -> `1570-1572` — the quoted `if` is 1570 and the strip it claims
  is `bits = bits[:-2]` at 1572, outside a span that opened on `asvar = None`;
- `:1581` -> `1579` — `if name:` is 1579, and 1581 is `else:`;
- `:1585` -> `1584` — 1585 is a blank line and the passing is the return at 1584.

Two of those four are the same shape twice over, and they are the shape this
recon keeps meeting: **a span that ends on a line which does not carry its
claim.** But this batch produced a mechanical signal the earlier ones did not:
the manifest had *already recorded the symptom*. `defaulttags.py:1514-1586` was
an **unquoted** row — no snippet — and it had to be, because a blank end line
carries no text a row can assert. So an unquoted row is worth reading as a
signal in its own right: it may mean nobody quoted it, or it may mean the span
ends where nothing can be quoted. The second reading is a defect. (`--check`
still passes on such a row, because it only asks whether *some* line inside the
span exists.)

## The fourth detector: `unquoted` rows whose span ends where nothing can be asserted

The signal above is runnable, and running it is cheap — 131 unquoted rows, 87 of
them ranges, and for each one a single question of the end line: does it carry
text? Ten did not (corrected in `ec4739f`):

| row | old end | end line was | corrected |
| --- | --- | --- | --- |
| `base.py:669-671` | 671 | blank | `668-670` |
| `base.py:822-833` | 833 | `# Not enough OR Too many` | `822-839` |
| `defaultfilters.py:78-83` | 83 | blank | `78-82` |
| `defaultfilters.py:447-450` | 450 | blank | `447-448` |
| `defaultfilters.py:511-516` | 516 | blank | `511-515` |
| `defaultfilters.py:578-588` | 588 | blank | `578-587` |
| `defaultfilters.py:669-743` | 743 | blank, three past `unordered_list` | `669-737` |
| `defaulttags.py:724-757` | 757 | blank | `724-755` |
| `defaulttags.py:759-805` | 805 | blank | `758-804` |
| `smartif.py:40-43` | 43 | blank | `40-42` |

Nine are the one-line-past shape. The tenth is not: `base.py:822-833` ends on a
*comment*, and a comment is not a slip — it is a line somebody chose, and it
belongs to the `if` at 834 rather than to the `args_check` the sentence names.
So the repair is not "back up one", it is "take the unit the sentence names",
which is 822-839. That is the reason the blank/comment distinction is worth
keeping in the detector rather than collapsing it.

Two more things fell out of the batch:

- **Every one of the ten became a quoted row**, which is the point — the end
  line is now one a row can assert, so the same signal cannot fire on it twice.
- **A fourth blind spot: bare `` `N-M` `` numbers.** `smartif.rs` cited
  `Literal.__repr__` as `` `smartif.py:40-43`, `142-143` `` — the second has no
  colon, so it is not a `SHORTHAND_RE` match, so `unmigrated` never counted it
  and no pass would have reached it. A tree-wide sweep found exactly **two**
  such spans in the whole tree, both in that file: `142-143` and `214-215`. Both
  are **right** (`Literal.__repr__` and `IfParser.create_var`), and both are
  named now — so the class cost nothing here. It is listed because it is
  invisible to every count the project has: neither `unmigrated` nor the
  stranded list nor `--check` can see a citation that never had a colon. The
  wrong anchor in that sentence was the one *with* the colon (`40-43`).

One row was read and **left alone**, and it matters because it is the precedent
for how wide a span should be: `base.py:1060-1063`, the `origin`/`source` read
in the debug exception path. It ends on
`context.render_context.template.get_exception_info(`, which is real code, and
all four of its lines are part of the claim (1060 reads `origin`; 1063 opens the
`get_exception_info` call). So it passes: the **quotation decides where** inside
the span, and the **syntactic unit decides how wide**. Applied to the pair that
prompted it, `:1562-1564` (the three-line `raise`) stands and `base.py:672-676`
was wrong — not because a quotation was present, but because widening it pulled
in a signature and a docstring the claim was silent about.

**Four detectors now, and three were discovered rather than designed:** the
stranded list, the naming read (prose and numbers), and this blank-end signal.
None of the three was in the plan; the plan had only the manifest and its
`--check`. Worth saying in the M6 write-up.

**The blank shape is a ranged-row phenomenon, and that is the complete
treatment.** The two mirrors — a range whose *end* is blank and one whose *start*
is blank — were extended to single-line rows, expecting `firstof.rs`'s `:805` to
have siblings. It has none: **1 hit in 227 single-line rows**, and it is
`python/django_rust_templates/_impl/parser.py:285` pointing at a comment
citation into our own tree, plausibly deliberate. The reason is mechanical and
worth the line: **a range's end is arrived at by counting, a single line by
pointing**, and counting lands on blanks while pointing does not. So the
blank-end and blank-start mirrors are the whole treatment; there is no third
mirror to build.

**And none of the five detectors can see a shorthand at all.** `firstof.rs`'s
`:805` — a blank line cited as a `return` — was a *shorthand*: it had no manifest
row, so the blank mirrors, the AST boundary rule, the stranded list and the
agreement census are all structurally blind to it. That generalises into the
scheduling fact this stretch turned on: **the detectors' coverage grows only as
`unmigrated` falls.** The 22 rows still unmigrated are outside every instrument,
and the rows named in the last several commits *entered* the detectable
population without anyone re-running the sweeps over them — the blank mirrors
were last run against a manifest that held fewer quoted rows, the AST rule
against a smaller one still. Two consequences. The five detectors want re-running
at close-out over the **final** manifest, not only once each when they were
built. And the per-file naming read is doing double duty, being both the
migration and the only instrument that can see a shorthand — which is why it
keeps finding what the detectors do not. That is not the detectors
underperforming; it is them being blind to the half of the tree still
unmigrated. The cheap version at close-out: re-run all five and publish the
combined hit count. Zero is the strongest evidence available that the sweep is
complete; anything else is rows no pass ever read.

## Naming rewraps, and the wrapper had one bug the numbered lists could show

Every naming pass rewraps the paragraphs it widens — naming costs 14 to 17
columns, so a paragraph near the file's width goes over. The helper has been
reused six times unchanged (`url.rs`, `for.rs`, the ten blank-ended rows,
`smartif.rs`): flatten the words, merge backticked spans until backtick parity
is even, greedy-wrap at the file's width, then guard the round-trip (the words
come back identical, every line keeps the lead, no line is wider than the lead
plus the widest token). It takes a single lead, and six times that was enough.

`urlizetrunc.rs` is where it was not. Two of its paragraphs are numbered items
in `//!` prose, and Django's own continuation indent — `//! 1. ` on the first
line, `//!    ` on the rest — is not decoration: it aligns the continuation
under the text, and flattening it to `//! ` silently unindents the whole item.
The first run of that pass did exactly that, and the diff showed it. The fix is
a second lead, `wrap(words, first_lead, cont_lead)`, and it is worth recording
because it is the one failure mode the round-trip guard cannot catch: the words
come back identical and every line still starts with *a* lead. Only reading the
diff catches it, which is the same lesson as the rest of this recon — a
guard checks what it was told to check.

That pass (`93bc8ba`) named seven anchors, all correct, and stranded nothing.
It is the first batch where the naming read found no error at all, after
`url.rs`'s four and `for.rs`'s eight.

## The fifth detector: a span that leaves its definition

The four above all wait for a file to *finish* (the stranded list) or read its
prose (the naming read) or look at one line (the blank-end signal). The
reviewer's rule is **leading** — it does not wait for anything, and it reads no
prose. Stated verbatim:

> The signature is a span that leaves its definition. `ast.parse` the Django
> file, map every `FunctionDef`/`ClassDef` range, and flag any anchor whose
> start and end lie in different definitions.

with two exemptions, both of which the reviewer supplied because the unfiltered
rule is useless without them:

- **the end's definition is nested inside the start's** — not a hit. This is the
  whole-regex, whole-class, whole-`__init__` shape the project deliberately
  cites: `FilterExpression`→`__init__`, `Variable`→`resolve`,
  `stringfilter`→`_dec`, `_property_resolver`→`resolve`.
- **the end is that definition's last line** — not a hit. A span that
  deliberately covers several definitions ends *on* one, never *inside* one;
  this is what keeps `defaultfilters.py:417-438` (ending on `center`'s last
  line) out.

It is about twenty lines of `ast` and it does not guess. Its blind spots are
named, not implied: single-line anchors, bare `` `N-M` `` spans with no colon
(the fourth blind spot above), and — the one that mattered here — a span that
over-runs *inside a single definition*, which no definition-comparison can see.

**Measured, not accepted.** Run independently against `scripts/anchors.txt`,
the counts depend on one definition, and it is worth naming because the two
numbers in this paragraph are otherwise "two right answers to two questions":

| "a definition" means | unfiltered | hits |
| --- | --- | --- |
| any `def`, methods included | 15 | 3 |
| module-level `def`/`class` only | 6 | 3 |

The reviewer reported 7 and 2, and neither is a third granularity. The 7 was
never "all spans crossing a boundary": it was "crossing, *and not ending on the
owner's last line*" — the second exemption already applied — so it is not
comparable to either unfiltered column above, and the comparable figure under
their mapping is the 15. The 2 was an undercount, from two defects in their
script, and each is worth naming because each is a way a span detector loses
hits **in silence**:

- `ast` puts `FunctionDef.lineno` on the `def`, not on the decorator — decorators
  live in `decorator_list` with their own linenos — so a decorator line maps to
  *no* definition at all. Their `owner()` returned `None` and the anchor
  `continue`d out of the run. That is exactly `html.py:210-232`, the third hit.
  This is the strongest argument for the mapping fix in the note below: a rule
  that drops anchors without recording the omission is worse than one that
  reports a false positive, because nothing in the output says the run was
  incomplete.
- They compared definitions **by name** rather than by identity (their
  `os_[2] != oe[2]`), so a span crossing from `Variable.resolve` into
  `FilterExpression.resolve` compares equal and is skipped. Two same-named
  methods are the common case in this file set, so this is an independent hole,
  not a variant of the first.

So the rule is sound and their two spans are two of the three; what they need is
the mapping fix below, which their own two defects describe better than the count
does.

The three, each read against `.django/` @ 6.0.9 before being believed:

| anchor | cited as | measured | corrected to |
| --- | --- | --- | --- |
| `defaulttags.py:1099-1105` | `do_load` "treats every bit after the command as a library name when there is no `from`" | the span is `return subset`, two blanks, `@register.tag`, `def load(parser, token):`, one docstring line | `defaulttags.py:1126-1130` — the `else` branch |
| `library.py:392-480` | "`parse_bits` … where a `simple_tag`'s arguments are bound" | *starts* on `varargs,` in the signature, *ends* on `try:` in `import_library`; `parse_bits` is 388-473 and `return args, kwargs` (473) is never reached | `library.py:388-473` |
| `html.py:210-232` | "striptags (`strip_tags`, …)" | `strip_tags` is 210-229; 232 is the **decorator** of `strip_spaces_between_tags` | `html.py:210-229` |

The third is why the counts differ, and it is a real defect rather than a
metric artifact. `strip_spaces_between_tags` *is* 232-235 under a
decorator-inclusive start, so 232 lies inside a definition and is not that
definition's last line — a hit by the rule as stated. But if you map
`child.lineno` and ignore decorators, 232 belongs to no definition at all and
the anchor falls out of the run. So the exemptions are not quite complete: the
mirror of "the end is its definition's last line" is **"the end is the next
definition's first line"**, and 232 is exactly that. Either fix the mapping to
include decorators (as here) or exempt the first line; do one, not neither.

**The fourth hit is the read's, not the rule's**, and it is the evidence that
the rule is necessary but not sufficient. The same comment block cites
`defaulttags.py:1086-1095` for "The *other* raise site … and it is a different
sentence". The whole span lies inside `load_from_library`, so every exemption
and every comparison passes it — and it is wrong: it opens five lines early, on
the subset-building lookup, and ends on `name,`, **cutting the message in
half** (`label,` is 1096, the closing `)` is 1098). The raise statement is
1092-1098. A definition-comparison cannot see a span that over-runs within one
definition, and this is that shape.

So the fifth detector joins the list, and so does its limit. Five detectors now,
and four were discovered rather than designed: the stranded list, the naming
read, the blank-end signal, and this one were all found while working; only the
manifest and its `--check` were planned. Corrected in `fd8759f` — four
replacements, all the same width as what they replaced, so nothing rewrapped,
and the first correction whose repair was a *reuse* of an existing neighbour
rather than a new row.

## The end line and the start line, asked of the rows nothing was asking

The fourth detector asked one question — does a row's end line carry text? — of
the 131 **unquoted** rows. It was never asked of the quoted ones, and it cannot
be: `_check_entry` requires the snippet to appear *anywhere* in the span, so a
row with a snippet passes whatever its end line is. Asking all 276 range rows
instead found **six**, all quoted, all the same one-line-past shape the ten
unquoted ones were (PEP 8's two blank lines after a top-level `def`; the span
reaches into them):

| row | old end | corrected | construct |
| --- | --- | --- | --- |
| `defaultfilters.py:590-600` | blank | `590-599` | `dictsortreversed` |
| `defaultfilters.py:757-776` | blank | `757-775` | `get_digit` |
| `defaultfilters.py:854-858` | blank | `854-857` | `divisibleby` |
| `defaultfilters.py:942-982` | blank | `942-981` | `pluralize` |
| `defaulttags.py:1134-1179` | blank | `1134-1178` | `lorem` |
| `defaulttags.py:1610-1651` | blank | `1610-1650` | `widthratio` |

Two are cited from `parser/error.rs`, which is how the sweep started; the other
four are cited from files still in the migration queue, so all four would have
been asked eventually — or never, since all four are quoted. Corrected in
`c700c1e`; each end was read against `.django/` @ 6.0.9 rather than backed up on
the arithmetic of the blank line.

**And one row the checker could not have caught at all:** `defaulttags.py:1654-1690`,
cited from `with.rs` for `defaulttags.do_with`, names three lines that do not
exist — `do_with` is 1654-1687 and the file ends at 1687. `--check` is silent
because `_check_entry` tests `snippet in "\n".join(source[start - 1 : end])`, and
a slice whose stop is past the end is silently *short* rather than an error: the
snippet at 1679 is well inside, the row counts as cited, and the run reports 0
failures while the manifest holds an anchor no reading of the file resolves.
Nothing else bounds a span against its file's length either. Corrected in
`e4bc17e`.

**The mirror, and its blank branch.** The reviewer mirrored the detector onto
start lines and reported six hits, all comment starts, with the comment that a
comment start — unlike a blank end — has a real legitimate class. Reproduced
here, and the branch they report as empty is not: `defaulttags.py:97` starts on
a **blank** line, which is the `html.py:246` shape (wrong in the line itself)
rather than a span defect, and it was invisible for the same reason the blank-end
rows were — the row records no snippet, so only its line number is checked, and
`97` is blank while the prose citing it says "`CycleNode.__init__`'s own" (the
`def` is 99). Two of the six comment starts were wrong the same way:

- `smartif.py:160-171` (`smartif.rs:554`) is cited for "`IfParser.__init__`'s
  token merge … and nothing else" and stops two lines short of the loop it
  names: the merge's own `mapped_tokens.append(...)` is 172 and the loop's
  closing `i += 1` is 173. A span cited for the merge did not contain the line
  that performs it. Corrected to the `while` loop, `164-173`.
- `timesince.py:60-70` (`timeuntil.rs:19`) and `timesince.py:60-73`
  (`host.rs:677`) are two rows for one construct that disagree about the end —
  **and both are wrong at the front**, opening on the comment for the *date*
  conversion at 61-62 rather than the timezone block both sentences name
  ("timezones" and "normalising the timezones"). The timezone unit is 67-70, so
  one span ends where the unit does and the other reaches one line past it into
  the unrelated `if reversed: d, now = now, d` swap — and the disagreement about
  the end is the *visible* half of a defect whose cause is the start. Once the
  span is the unit named, both are `67-70` and there is one row. Corrected in
  `5c017bb`.

The other four comment starts are the legitimate class: `base.py:504-505` and
`defaulttags.py:1250-1251` open on a comment that *states* the claim, and
`defaulttags.py:106-112` needs a judgement about which unit its sentence names
(`CycleNode.render` is 104-114) that is better made when the three files citing
it are named. So the mirror is a suspicion list where the blank-end detector had
none — the difference the reviewer predicted, and the reason it is worth keeping
as two questions rather than one signal.

## Two more shorthand classes, both of which the counts conflate

`SHORTHAND_RE` is `r"`:(\d+)(?:-(\d+))?`"` — it matches a backticked `:N` and
nothing else, so `unmigrated` counts three things as one. Sweeping all 56
remaining occurrences and reading them separates them:

- **Citation-shaped prose that is not a citation.** Four in
  `tests/test_materialise.py`, all the trailing argument of the template token
  `x|upper:1:2` (`` `:2` is left over ``). No file can ever be named, so no
  naming pass can clear them, and `--frozen` could never be reached while they
  stood. The repair is to the *prose*, not to a number: each became "the `2`",
  which cannot be read as a line number (`3f0aac0`). The convention ("a
  backticked `:N` is line N of the file named nearby") is load-bearing — it is
  what lets a reader resolve a shorthand and what lets the metric count one — and
  prose that borrows the shape without the meaning breaks it even though a human
  reader, who can see the full token three words earlier, is not confused. So the
  tool is right to fire and the prose is what changes.
- **Shorthands citing a *repo* file rather than a Django one.** Two known, both
  handled the same way but for a different reason. `tests/test_materialise.py:1070`
  cited `:207` and `:48-59` for `tags/mod.rs` — and both numbers were also wrong
  against the file, so the sentence was rewritten with `parser/tags/mod.rs`
  throughout (`d09f40c`). `filters/striptags.rs:55` cites
  `crates/template-py/src/filters.rs:854`, `` `:248` `` and `` `:1528` `` for the
  `strip_tags` → `host_error` → `report` path; that file's pass is still pending,
  and a first read is the reason it needs one rather than a rename — `:248` is
  `fn host_error` and right, but the *named* `:854` is inside `comma_bits`
  (`for bit in argument.call_method1("split", (",",))?.try_iter()?`), nowhere
  near the sentence's subject, so at least one number on that line is wrong.
  A `.rs` path is invisible to `ANCHOR_RE` by design — the manifest is Django
  anchors — so naming the file is the whole repair, which is exactly what the
  metric asks for; the numbers still have to be read.

Both classes are the same observation from two sides: `unmigrated` is a regex
count, and a regex cannot tell a Django line citation from a citation-shaped
string. That is not a defect in the metric — "shorthand anchor(s) naming no file"
is a *true* description of all three kinds — but it means the count is a
triage queue, not a to-do list, and the read is what divides it.

## Five notes from the review that outlive their findings

- **A withdrawn finding, and its cause.** The reviewer briefly reported
  `defaulttags.py:1060-1063` and withdrew it once measured: the anchor is
  `base.py:1060-1063`, and they took the filename from *my* message, which had
  written `defaulttags.py:1060-1063` and `1400-1401` side by side — the second
  `defaulttags.py`. So the miscitation came from the sender, and it is the exact
  class this sweep exists to remove: two files under one filename. Worth the
  paragraph in the write-up, because it is the sweep's own failure mode
  occurring in the conversation *about* the sweep. The rule it yields is
  narrower and more useful than "name the file": **never write a bare `:N`
  next to a different file's anchor.** The reviewer's `:410` was
  `defaultfilters.py:410` written immediately after a `smartif.py` anchor, so
  it inherited smartif as its file — and `smartif.py` has no line 410. Two
  instances now, one in each direction, both in prose *about* the sweep.
- **An internal disagreement between two citations of one line is itself a
  signature, but its rate cannot be measured against the manifest.** It has two
  instances, both found by reading rather than by any tool: the `timesince` pair
  above (`60-70` vs `60-73`, one construct, neither end the unit) and
  `ifchanged.rs` below. Found in
  `ifchanged.rs`, whose two citations of Django's `nodelist_false = NodeList()`
  gave it as `` `:1057-1058` `` and `` `:1058` ``, disagreeing with each other —
  and both were wrong; the line is 1060. Mechanically the signature is two sites
  whose snippets are the same text at different line numbers. The reviewer tried
  to hand over the false-positive rate and the attempt is the finding: grouping
  manifest rows by `(file, snippet)` gives 20 texts appearing at more than one
  start line out of 335, and nearly all are legitimate — the text genuinely
  occurs several times (`return input_val` at 163/177/182), or one row is a span
  and the other a line inside it (`defaultfilters.py:44-48` vs `:46`). Filter
  both and essentially nothing survives — but that is not a low rate, it is
  **the wrong population**: the manifest holds only rows that pass `--check`, and
  a row with a wrong number cannot be in it, while the disagreement the
  signature is for is precisely two *wrong* numbers for one line.
  `ifchanged.rs`'s two were never manifest rows; they were prose citations, both
  wrong, with the line at 1060. So the signature's domain is citations in the
  tree **before** naming, and its rate can only be collected during a sweep, by
  noting each time two sentences cite one construct at different numbers and
  whether that disagreement meant a defect. It stays a candidate; the reason is
  stronger than "nobody has measured it" — the artifact anyone would reach for
  to measure it cannot contain the phenomenon.
- **Publish the denominator with the ratio.** "28 rows / 12 wrong" is only a
  fact when the 28 is the row count that entered the stranded list and the 12
  is the number of those whose spans were wrong. A ratio without its base is
  the same mistake as a count without its metric.
- **Where a ratio is available, publish the ratio too — it survives the unit
  being wrong.** The fifth disputed count was the first with a ratio on both
  sides, and the counts differed by 20% (434 vs 519, 222 vs 275) while the
  ratios differed by 2 points (54.6 vs 56.6, 27.9 vs 30.0). The absolute
  numbers did not survive the choice of unit; the ratios did. That is weaker
  than naming the population first — it cannot tell you *which* unit is
  meant — but it is cheaper to say and it degrades gracefully when somebody
  forgets, which is the failure mode every count in this series has had.
- **Close-out verification runs by citation count descending, not by file.**
  The migration stays per-file, because the prose has to be read in context to
  know whether the citation is right; but *verifying* an anchor does not depend
  on which file cites it, and an anchor with fourteen citers is wrong in
  fourteen places at once. Ordering by citation count puts `base.py:816` (14
  files) and `base.py:813` (7) first, where one correction pays the most, and
  it is why the agreement census was worth building at all. **The order is
  computed and published now**, in `.review/m6-verification-order.md` — 163
  anchors, sorted by citing-file count, with the line each holds — and it needs
  no recomputation as the migration finishes, because the census is invariant
  under naming. Its head was verified against the pinned tree on 2026-10-09:
  `base.py:816`, `:813`, `:669-670`, `:773`, `defaultfilters.py:46`,
  `defaulttags.py:929`, all six correct.

## One paragraph, four wrong lines, and where the wrong ones landed

`crates/template-core/src/parser/host.rs` names `base.py` once (line 64) and
`defaulttags.py` once (line 189), then cites five more Django spans as bare
`` `:N` ``. Two of the five were right. Four things were wrong, all of them in
the two paragraphs about the filter loop's ordering, all measured with the
pinned interpreter (`.venv/bin/python` 3.14.4, `.django/` @ 6.0.9) by catching
each rejection's traceback rather than by reading:

| cited | its claim | actually at | what the cited line holds |
|---|---|---|---|
| `:743` | `upto != start` | 747 | 743 is `filters = []` |
| `:774` | `upto != len(token)` | 775 | 774 is `upto = match.end()` |
| `:768` | `Variable('_bad')` raises | 770 | 768 is the `constant_arg` branch, which `_bad` never reaches |
| `base.py:757-772` | the per-iteration order | 747-772 | 757 is inside the constant branch |

The last one is the one the sentence refutes about itself: it lists the order as
gap → argument `Variable` → `find_filter` → `args_check`, then says "the first
two are separable in outcome" — but the gap check it names *first* (747) is
outside a span that starts at 757, so the span cannot show the separability it
was cited for.

Two things about the shape. First, every error is on the loop's **edges** — the
gap check, the remainder check, the argument's `Variable` — and none is on a
line the paragraph is *about* (`find_filter` at 771 and the `append` at 773 are
both right). A reader checks the line under discussion, not its periphery, and
whatever produced these numbers did the same. Second, two of the four land on a
line *adjacent to* the construct (774 is the assignment that feeds 775) or on
its *sibling branch* (768 is the other `args.append`) — the shape a number takes
when it was read off a nearby line rather than searched for.

`filter_expr.rs:665` carried the same off-by-one for the same Django line
(`` `base.py:774` `` for the remainder check), and this is not a second opinion —
the two files **agreed**. 774 was cited twice and wrong twice, which is why it
was corrected in the same commit as host.rs's: leaving one of two agreeing
citations at the old number would have turned a shared error into an apparent
disagreement, and the disagreement would have been the *corrected* half. The
same fact is the second instance of the case below.

**The corrections went in first, and that is the rule paying off.** A naming
commit must never move a number, so the numbers were settled in `217a094` and
the naming followed in `a11f24c`. The other order would make the naming commit's
diff unreadable as "lexical only" — which is the property that lets a reader
audit it at all.

**A correction commit must also leave the tree's citations and the manifest in
agreement.** Minting `base.py:747` alongside the correction was wrong: nothing
cites `base.py:747` until the shorthand on line 73 is *named*, so that commit
would have ended with a dead row. It moved to the naming commit. This is the
first time the "no dead rows" invariant has constrained the *sequencing* of two
commits rather than the content of one.

`error.rs` is what makes this file readable as an outlier: five shorthands, five
spans that contained their claim, zero corrections. `host.rs` is the second file
with defects and the first where the defect is a pattern inside one paragraph.

## The tool narrows its input silently, and that is all four holes so far

Not one of the four holes found in `check_python_anchors.py` has been a wrong
answer about input the tool saw:

- `ANCHOR_RE` cannot match a decorator, so a decorated `def` is never seen
- its path class is `[a-zA-Z_0-9/]`, which cannot match a hyphen, so every
  `template-core` / `template-py` citation is dropped
- `_check_entry` slices `source[start - 1 : end]`, and a slice past the end is
  silently *short*, so an anchor naming lines that do not exist is "cited"
- **and hole #2 does not merely drop its own citation — it hands any bare `:N`
  after it to a different file.** `filters/striptags.rs:55` cites
  `crates/template-py/src/filters.rs` as `:871`, `:248`, `:1644-1645`. That path
  is not an anchor, so the checker resolves the two bare spans against the
  nearest `.py` anchor above — `html.py`, forty lines up — and reports them as
  "names no file", which is true but not the interesting part. The interesting
  part is that they look resolvable. This is also where the hole stops being
  theoretical: the two numbers into our own tree drifted to 854 and 1528, lines
  that hold a `split(",")` loop and a doc comment, because **nothing checks our
  own tree** — Django's is pinned and ours moves every commit.

Each is invisible: no match means not counted, not checked, not reported. Four
instances is enough to call it the tool's characteristic failure mode, and the
discipline that follows is cheaper than finding the fifth by hand — every place
the tool slices, truncates, or pattern-matches should assert it consumed what it
was given. A slice that runs past the end should raise; a regex that matches
nothing where something anchor-shaped exists should report.

## Sizing the `.rs` extension, and where its ambiguity actually is

34 `` `*.rs:N` `` citations over `crates/`, `python/`, `tests/`, `scripts/`; 25
under `ANCHOR_RE`'s own character class; 9 lost to the hyphen. Of the nine, one
(`pyo3-0.29.3/src/marker.rs:414`) contains a dot as well as a hyphen, so
widening the class to admit `-` would still leave it unmatched — the fix has to
be `-` **and** `.`, or the extension ships a second silent drop inside the fix
for the first.

A resolvability census of the 34, which is the number that sizes the work:

| how the path is written | count | resolves to |
|---|---|---|
| full path from the root | 3 | itself |
| partial path | 27 | exactly one file by suffix |
| bare `host.rs` | 3 | **two** files — `filters/host.rs`, `parser/host.rs` |
| a cargo-registry path | 1 | nothing in the repo |

So a resolver has to handle duplicates for **3 occurrences of 1 basename** —
not the 10 bare-basename occurrences first reported here, and not the 5 the
reviewer first reported. Fourth disputed count in this project, and the third
time it has been because three people measured three different things: "written
as a bare basename", "needs a basename map", and "a naive resolver cannot settle
it" are three questions with three answers (10, 3, 5). The metric is not
pedantry here; it is the whole of the disagreement.

The last row is a third target class — Django, ourselves, and a **vendored
dependency** pinned by `Cargo.lock` rather than by `.django/`, resolvable only
against a cargo home that is not in the repo. It needs an explicit decision,
because the alternative is an unresolvable row that someone later exempts by
hand and nobody re-examines.

## "Agreement is not evidence"

Two files citing one construct at the same wrong number are not corroboration;
they are one reader's choice copied twice — every copy raises the apparent
confidence while adding no evidence. Measured over the tree (524 distinct
anchors are cited; 163 of them from two or more files), it has two instances:

| anchor | cited from | wrong because |
|---|---|---|
| `base.py:774` | 2 files — `parser/host.rs:73`, `parser/filter_expr.rs:665` | 774 is `upto = match.end()`; the remainder check is 775 |
| `defaulttags.py:106-112` | 3 files — `parser/tags/cycle.rs`, `_impl/parser.py`, `tests/test_ref_table.py` | 106 is the comment `# First time the node is rendered in template`; the unit is `CycleNode.render`, 104-114 |

The second is why the row is held back rather than named: three files will have
to be corrected together when that unit is settled, and correcting them one file
at a time would leave the other two asserting a span the first has already
disowned.

What the count *does not* say is that agreement is a defect detector. The
most-agreed anchors in the tree are agreed-upon and right: `base.py:816` is
cited from **14** files, `base.py:813` from 7, `defaulttags.py:46` and
`base.py:773` from 6 each, and the `smartif` pair my earlier draft of this
section named — `smartif.py:55-56` (2 files) and `smartif.py:81-83` (3 files) —
was verified against the pinned tree and is correct on both (`self.first = left`
/ `self.second = parser.expression(bp)` in `led`; `self.first = …` /
`self.second = None` in `nud`). So agreement is **not** a detector at all: it is
a prior that says *the number was copied, not independently read*, which lowers
rather than raises the cost of checking it.

**The census has two right readings, one per unit.** Measured on this tree: 524
distinct anchors are cited, by 917 occurrences (`file`, `line`) forming 795
relations (`anchor`, citing file). Counted by *relations*, 434 anchors are cited
from two or more files and 222 from three or more; counted by *occurrences*, the
same anchors cover 519 and 275. The reviewer reported the first pair, I the
second — the fifth count in this series to be two right answers to two
questions, and the first with a ratio on both sides to show the two agree about
the shape: 163 anchors (31.1%) cover 56.6% of occurrences or 54.6% of relations,
and the 57 anchors cited from three or more (10.9%) cover 30.0% or 27.9%. Two
properties make this census usable where the ones before it were not. It is
**invariant under naming** — it canonicalises a shorthand and a full path to the
same key, so a figure taken before a migration pass still holds after it, which
is why the same 163 and 434 reproduce now that five files have been named. And
it is a **prior, not a detector**: it says where a wrong number is expensive, not
where one is.

This belongs beside the detector list because it is the one statement here that
says what *not* to trust. It inverts the disagreement detector correctly:
disagreement between two citations of one construct is a reason to look, and
agreement is no reason to stop.

## The gap detector, built and measured

The reviewer proposed a detector for the shape host.rs had four of: a shorthand
whose quotation sits on a line *near* the number rather than on it. Built
(`/tmp/gap_detector.py`), run over the then-unmigrated shorthands, and measured
rather than adopted — it is a **reading aid with a 2-in-3 hit rate and a large
blind spot**, not a filter:

```
17 shorthand(s) got as far as a unique probe
  4 the probe is inside the span
 13 the probe is OUTSIDE the span
skipped: probe not unique in the target: 22
skipped: no quotation precedes it: 1
```

Ten of the thirteen are the script's own resolution failures (it lacks the
checker's `BASENAMES` map, so basenames like `html.py` raise), leaving **three
real candidates, of which two are genuine defects** — both in files no naming
pass had reached yet:

| shorthand | prose said | the quoted line is at | what the cited line holds |
|---|---|---|---|
| `widthratio.rs:5` | `parser.compile_filter(max_width)` (`:1649`) | 1648 | `asvar=asvar,` |
| `lorem.rs:24` | `common = bits[-1] != "random"` (`:1160`) | 1162 | `tagname = bits[0]` |

The third, `lorem.rs:6`'s `:1160-1175`, is a **false positive**: the prose quotes
the docstring's usage syntax (`{% lorem [count] [method] [random] %}`, 1141)
while citing the implementation, so "the quotation is on the cited line" is the
wrong question to have asked. Whether that span is the right *unit* is a separate
question, and the reviewer measured it when that file's pass reached it:

```
1160  tagname = bits[0]      <- out: a standalone statement, inside no pop's conditional
1161  # Random bit          <- out: a comment
1162  common = bits[-1] != "random"
1163  if not common:
1164      bits.pop()
1166  if bits[-1] in ("w", "p", "b"):
1167      method = bits.pop()
1168  else:                  <- not a pop, and in the interior: no span can exclude it
1169      method = "b"
1170  # Count bit
1171  if len(bits) > 1:
1172      count = bits.pop()
1173  else:
1174      count = "1"
1175  count = parser.compile_filter(count)   <- out: a separate statement
```

The span is loose at the front by **two** (1160, 1161) and at the tail by **one**
(1175) — not three, which is what this note said before the reviewer's second
pass. The reason three was wrong is worth more than the correction. It came from
asking "is this line a `pop()`?" and counting every line that is not: 1173
(`else:`), 1174 (`count = "1"`) and 1175. But that test fails **on the span's own
interior** — 1168 (`else:`) and 1169 (`method = "b"`) are not pops either, and no
contiguous span can exclude its middle. A boundary test that returns an
actionable verdict at the edges and an impossible one in the middle is not
measuring the subject; it is measuring position. The rule is the **syntactic
unit**, and `if len(bits) > 1: … else: …` is one statement, so 1171-1174 are in
and only 1175 is out.

The span stays where it is regardless, because "parsed by popping" can reasonably
name the parse *region* rather than the statements that do the popping — and
**the boundary rule only decides when the subject is unambiguous**. That
precondition belongs beside the rule: without it, "narrow the span to its
subject" reads as licence to narrow every span, and somebody will. This note is
its own illustration of the failure it warns about, twice over — first by
recording the wrong number while citing the rule as the reason, then by
"correcting" the reviewer with an incoherent test.

And the detector **missed defects it should have caught**. `widthratio.rs:9`'s
`:543-545` does not contain the raise at 548 — but the quoted message wraps
across two source lines, so the line-based `QUOTE` regex never sees a closed
span and the shorthand is skipped as "no quotation precedes it". And
`widthratio.rs:35`'s `:1647-1648` is a span cited for unpacks that contains
neither unpack — but the quotation before it is `` `tag` ``, which occurs all
over `defaulttags.py`, so the shorthand was skipped as "probe not unique"
rather than flagged; that one was found by reading the prose's own words
("unpacks it into an unused `tag` **both times**") against the pinned tree. Its
three blind spots, then, are nameable exactly, one mechanical and two domain:
**quotations that wrap a line**; **a probe word that is not unique in the
target**; and **quotations that name something other than the cited code** —
usage strings, docstrings, error messages. Its two true positives did front-load
corrections that reading would have found one file at a time, so it earns a
place in `--triage` as three printed lines per shorthand (cited line, probe
line, gap) and **not** as a gate.

**And the series needs its clean-file count beside its defect total.** Of the
five files migrated so far, two carried no defect at all: `parser/error.rs` and
`parser/tags/with.rs`, five shorthands each, every one right against the pinned
tree. "28 rows, 12 wrong" and "2 files with nothing wrong" are different facts
about the same sweep, and the second is the one that says the tree is not
uniformly bad — which is what decides whether the remaining passes need the
detectors at all, or only care.
