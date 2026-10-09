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

Twenty-three rows have passed through; ten were wrong or cited for the wrong
line. Six were wrong in a *span's* end (`base.py:959-1029` ends on a blank line,
`:963-995` runs past the arms it claims, `:999-1029` misses two of its three
branches, `html.py:412-415` is a neighbouring `@cached_property`, and
`base.py:771-774` / `base.py:804-810` each end one statement past the construct
they enumerate) and four were wrong in the line itself (`html.py:246` is blank,
`base.py:999` is not the guard it is cited as, `base.py:972` is right for one of
its two sites and wrong for the other, and `base.py:697-701` is a regex rather
than the `compile_filter` its sentence is about). All ten came from a file whose
citations had never been read in context, and the span ones are invisible to a
check that only asks whether some line inside the span exists.

The order of the sweep decides when each row becomes visible: a row cited only
from files swept last surfaces last. So the list still has to be read once, at
the end — but the end is when the last row becomes visible, not when the
cleanup is done.
