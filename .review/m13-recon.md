# M13 recon — place `library`, and place `partial`/`partialdef`

Measured against `.django/` @ **6.0.9** on the closed M12 tree (`9115e61`,
`bash scripts/check.sh` green). Our side moves one commit at a time and the
pinned tree is a version, not a path, so re-run the instruments rather than
re-quoting their output.

M13 is the engine's last module and the last two tag stubs. It is also **two
jobs with different shapes** that share a commit boundary, and only one of them
is what the meter counts.

---

## 1. The headline

`unshimmed()` is one name — `['django.template.library']` — and after M13 it is
`[]` over the first scope. That is the whole of what the meter will record, and
it is the smaller half of the milestone:

| job | what moves | is it in the meter? |
|---|---|---|
| interpose `django/template/library.py` | `unshimmed()` 1 → 0 | yes |
| give `partial`/`partialdef` real compile functions | `builtin_tag_stub_names()` 2 → 0, and 52 of the 1,547 rows change route | **no** |

The second column is the risk. M12 had the same shape — its value was invisible
to every count this project accepts, and had to be measured with a routing
instrument and a seam probe rather than read off `unshimmed()`. M13's version of
that is sharper, because here the invisible job is *the one that can go silently
wrong*: the pair's bodies are the only two tags whose compile-time behaviour the
host, not Rust, has been providing, and 52 rows of Django's own partial tests
exercise that behaviour today.

And `library.py` is unlike the 25 modules before it in one way that decides the
plan's shape: **its engine-facing work is argument parsing and registration, not
rendering.** `parse_bits` (`:388-473`) decides every `{% simple_tag %}` /
`{% simple_block_tag %}` / `{% inclusion_tag %}`'s arguments and raises every
one of that family's syntax errors, and the four node classes it carries
(`SimpleNode`, `SimpleBlockNode`, `InclusionNode` over `TagHelperNode`) exist to
call what it produced. So the file's hard part is a **function**, and §6 exists
because "is that function reached" is the question a plan has to answer before
choosing whether Rust or the transcription owns it.

---

## 2. The census

`.review/m13-measurements/m13_measure_library_census.py` (run under `-I` on
purpose — under the shim `django.template.library` is ours):

| question | answer |
|---|---|
| lines | **492** (`splitlines`; newline count 492, so the two agree) |
| top-level classes | **6** — `InvalidTemplateLibrary`, `Library`, `TagHelperNode`, `SimpleNode`, `SimpleBlockNode`, `InclusionNode` |
| module-level `def`s | **2** — `parse_bits` (`:388-473`), `import_library` (`:476-492`) |
| `Library` methods | **8** — `__init__`, `tag`, `tag_function`, `filter`, `filter_function`, `simple_tag`, `simple_block_tag`, `inclusion_tag` |

Note what the two counts do *not* say, because this project has mis-quoted one
for the other before: **6 classes is not 6 nodes**. `InvalidTemplateLibrary` is
an exception and `Library` is a registry; the node classes are the four from
`TagHelperNode` down, and `TagHelperNode` is a base that is never constructed
directly. The `def`s are 2 at module level and 8 more as methods — three
spellings of "how much is in this file", and a plan that quotes one has to say
which.

---

## 3. What our side already has, and the one thing it does not

**Has.** The partials plumbing is largely in place and was measured, not read:

- `Parser.extra_data` crosses as a **real Python dict** and survives to the
  `Template` — `_impl/parser.py:548` (`template.extra_data = {} if parser is None
  else parser.extra_data`) and `_impl/base.py:205`. `host.py`'s module docstring
  states the division: the host owns `tags`, `filters`, `libraries`,
  `extra_data`.
- The engine's partial lookup is already **ours** — `_impl/engine.py:181-203` (within `get_template`, `:174-203`) is
  the `name#partial` partition, `extra_data["partials"]` read, `partial.engine =
  self`.
- `PartialTemplate` is already ours — `_impl/base.py:299`, with
  `find_partial_source` and the `source` property that warns under `RuntimeWarning`.
- `PartialDefNode`, `PartialNode` and both compile functions are already ours as a
  **transcription** — `_impl/defaulttags.py:415-437`, `:1207-1285` — and are what
  the host runs today.

**Does not have.** `_impl/library.py`. And this is measurable rather than
obvious: the module is reachable **only under the shim name**.

```
$ PYTHONPATH="$PWD/.django:$PWD" .venv/bin/python -c \
      'import django_rust_templates._impl.engine'
  File ".../python/django_rust_templates/_impl/engine.py", line 12, in <module>
    from .library import import_library
ModuleNotFoundError: No module named 'django_rust_templates._impl.library'
```

**The invocation is part of the claim.** Drop `.django` from the path and the
same command fails one import earlier with `ModuleNotFoundError: No module named
'django'` — a different fact about a different thing. The transcript above is
the one that demonstrates the missing `_impl/library.py`.

`_impl/engine.py:12` is `from .library import import_library`. That resolves
when the module is loaded *as* `django.template.engine`, because the package's
`__path__` searches `_impl` first and Django's own `template/` second — and it
fails outright under the internal name, since `_impl/library.py` does not exist.
So today the relative import silently binds **Django's** `import_library`, and
five of our modules do the same:

| our module | binds |
|---|---|
| `_impl/__init__.py:54` | `Library` |
| `_impl/defaulttags.py:44` | `Library` |
| `_impl/defaultfilters.py:30` | `Library` |
| `_impl/loader_tags.py:9` | `Library` |
| `_impl/engine.py:12` | `import_library` |

M13's file is what those five resolve to afterwards, and the second section of
§7 is the trap that follows from it.

---

## 4. The partials question: the registry's rule and the spec's row disagree

This is the milestone's real decision, and it is a design question the plan must
answer rather than inherit.

**The rule, as written.** `crates/template-core/src/parser/registry.rs:117-132`
flags the pair explicitly and gives the test: a tag is the host's when its
compile touches host parse state that outlives the parse. It calls `partial`
"the stronger of the two" and records why in one sentence — `partial_func`
constructs `DeferredSubDict(extra_data, "partials")`, **a live view over the
parser's dict**, and hands it to `PartialNode`, so the compiled node closes over
parse state that outlives the parse: *"a value a `NodeDesc` cannot represent at
all."* Under the rule, `partialdef` is the same case (it writes
`parser.extra_data["partials"]` at `.django/django/template/defaulttags.py:1251` —
the pinned base, stated because our transcription's copy of the same statement
sits two lines lower, at `_impl/defaulttags.py:1253` — and builds the
`PartialTemplate` there (`:1259`)).

**The spec's row, and M9's record, expect the opposite.** §10.3's M13 row is
"Partials and `library`". M9's record states it as a behaviour change with a
mechanism: a `{% verbatim %}` block inside a `{% partialdef %}` body reaches
Django's `do_verbatim` *"because `partialdef` is one of the two names in
`builtin_tag_stub_names()`, and it stops doing so when M13 supplies that compile
function."*

**What is actually at stake, measured.** The disagreement is not cosmetic,
because the route decides who compiles a partial's body. Today the host compiles
`{% partialdef %}` and its compile function calls Django's `Parser.parse`, so
**every tag inside every partial body is compiled by Django, not by Rust** —
the same hole `tests/test_host_tags.py` exists to document, and the reason
`test_host_tags.py:130` uses `partialdef` as its discriminator. That hole is
what M13 can close.

Three shapes, with the cost of each:

| | what it costs | what it buys |
|---|---|---|
| **(a) Rust compiles both** | two new `Host` seam methods — one to register a partial (materialise the body's sub-descriptor, build the `PartialTemplate`, write `extra_data`) and one to build a `PartialNode` over the live `DeferredSubDict` — both returning a `NodeRef`, the handle `NodeDesc::host` already carries | partial bodies become Rust-parsed; `builtin_tag_stub_names()` empties; the pair stops being a special case in `host.py:484-490` |
| **(b) Rust compiles `partialdef` only** | the same seam, half of it | the body hole closes; `{% partial %}` stays the rule's case verbatim |
| **(c) both stay host-compiled by design** | nothing on the Rust side; the pair moves out of `REGISTRY` into a declared third table with rows | the rule as written is honoured exactly; the file's Rust work is then only `parse_bits` |

**Recommendation: (a).** The reason is the hole, not the rule, and it is the
one thing here that is measurable rather than arguable: under (c), every tag
inside a partial body stays Django's work forever, and the corpus cannot see it
because `assert_trees_match` over such a body compares Django with Django —
the same vacuous-gate failure `tests/test_host_tags.py` was written to make
visible, and it would now be *load-bearing* rather than documented. The seam
this needs is smaller than it first looks: `extra_data` stays host-owned (no new
channel on the wire), the descriptor carries the handle it already knows how to
carry, and both new methods are call-and-return during the parse, the shape
`call_tag` already has. (c) is the honest fallback if the seam proves to widen;
the plan should say which it took and why, and if it takes (c) it owes the
registry comment an amendment rather than a silent contradiction.

**One fact that constrains all three, and it surprised me.** `NODE_CLASSES`
(`_impl/parser.py:136`) has **40** entries and **none of them is a `Partial*`** —
measured. The partial nodes work today because they arrive through the
materialiser's *host-node* arm (`NodeDesc::host`, read at `_impl/parser.py:289`),
not because a class was registered. So "give Rust a compile function for this
tag" does not automatically mean "add a `NODE_CLASSES` row": for a node whose
constructor takes live host state (`PartialNode.partial_mapping`), the host-node
arm may be the *only* correct route, and a `NODE_CLASSES` entry would need the
mapping to arrive as a handle anyway.

---

## 5. Two expiry classes, and only one of them is measurable

M13 removes the last two stubs, so every row whose subject or route is "the pair
is not Rust's" changes meaning. There are two kinds and they are not the same
kind:

**(a) Rows that rest on the route.** Measured, not read —
`.review/m13-measurements/m13_measure_stub_expiry.py` reports
`builtin_tag_stub_names()` as empty, which puts both names back on Rust's side of
`ShellHost._rust_owned` (`host.py:471-474`) and so routes them to the registry
bodies, which are still stubs. The run is the instrument's own
`runtests.py template_tests` invocation — not `check.sh`, and not a
`scripts/run_django_suite.sh` line — made with **the engine installed**
(`_shim.installed()` asserted before the runner starts) and `--parallel 1`, and
it prints

```
Ran 1547 tests   FAILED (failures=73, errors=167, skipped=6)   240 red
```

Every one of the 240 is inside two modules, split by re-running the same
mutation scoped to each label:

| module | test rows | red | as |
|---|---|---|---|
| `template_tests.test_partials` | 28 | 24 | 2 failures + 22 errors |
| `template_tests.syntax_tests.test_partials` | 24 | 216 | 71 failures + 145 errors |
| | **52** | **240** | 73 + 167 |

**The red count is not a row count, and the difference is not noise.** 52 rows
produce 240 reports because a failing `subTest` body is counted individually
(both modules loop inside `subTest` — 5 sites in the first, 6 in the second),
which is why the syntax module's 24 rows yield 216. Two numbers, one question
each: **52** is how many Django test rows depend on the pair's route, and
**240** is what the runner prints. §8's gate reading uses the first; the
mutation's output uses the second.

That 52-row population is the milestone's real acceptance surface: with working
bodies in place, all 240 reports must be green again.

**(b) Rows whose subject is the stub itself.** Three, named because the mutation
above *cannot* find them — it leaves the bodies stubbed, so anything asserting
"this raises `UnimplementedTag`" still passes:

| row | asserts | after M13 |
|---|---|---|
| `tests/test_tag_inventory.py:345` (`test_the_only_two_unimplemented_entries_are_the_m13_pair`), assert at `:355` | `builtin_tag_stub_names() == {"partial", "partialdef"}` | the set is `set()`, and the row must say so **with a positive control** (an empty set is satisfied by a registry that lost its entries) |
| `tests/test_parser_parity.py:857` (`test_an_unimplemented_tag_is_its_own_kind_at_the_ffi`), asserts at `:876-880` | `_native.parse("{% partialdef x %}")` raises `UnimplementedTag` with token `"partialdef x"` | no stub is left; the row needs a different subject or a different shape |
| `tests/test_host_tags.py:130` (`test_the_discriminator_is_Rust_compiling_the_same_tag`), raise at `:138` | the same call raises `UnimplementedTag` | same |

These are **read, not measured**, and the plan must re-derive each rather than
carry this table — a Python-side mutation cannot change a Rust compile body, and
saying so is the honest form of the claim.

**One of the three already declares its own expiry, which is worth knowing
before the plan treats it as a surprise.** `test_parser_parity.py`'s docstring
names the pair as a deliberately enumerated example and ends: *"it names a stub,
so every milestone that implements the last stub, or hands one over, has to move
it. M13 implements the pair that is left."* That row was written to be moved, so
M13 owes it a new subject rather than a new argument — and the subject has to be
a name that is still `Unimplemented`, of which after M13 there are none, which
is why it is a **shape** change and not a rename.

**(c) One row that must keep passing, whose stated reason expires.** The positive
half of the sandwich, `tests/test_host_tags.py:108`, asserts
`"p" in host.parser.extra_data["partials"]` *because* `do_partialdef` ran. That
assertion survives M13 — inside a host tag's body Django's parser still compiles
`{% partialdef %}` with `_impl/defaulttags.py`'s transcription, which still
writes `extra_data` — but its docstring's premise ("Rust's entry for the name
returns `UnimplementedTag`") dies with (b). So the row needs a *reason* edit
even though it needs no behaviour edit, which is the pattern
[[negative-fixtures-expire]] records.

The module docstring of `tests/test_host_tags.py:19-22` and the row
`test_parser_errors.py:129`'s parenthetical both name the pair as a future
discriminator site; both are comments and both must move. The docstring also
carries an unqualified citation — `` `defaulttags.py:1250-1251` `` at `:22` —
which is the pinned file's span: our transcription's copy of the same statement
is at `_impl/defaulttags.py:1253`, so a reader who assumes the base is ours will
look two lines early. M13 edits that docstring anyway; it should leave the
citation's base named.

---

## 6. The reach, measured

`.review/m13-measurements/m13_measure_parse_bits_reach.py` installs counters on
`library.py`'s compile-time surface and runs the 1,547 under the shim. Calls and
the number of distinct test files that made them:

| name | calls | files |
|---|---|---|
| `parse_bits` | **250** | 6 |
| `Library.tag` | 194 | 11 |
| `Library.filter` | 168 | 7 |
| `Library.simple_tag` | 36 | 7 |
| `Library.inclusion_tag` | 29 | 2 |
| `Library.simple_block_tag` | 28 | 2 |
| `SimpleNode.render` / `.get_resolved_arguments` | 332 | 6 |
| `InclusionNode.render` / `.get_resolved_arguments` | 33 | 1 |
| `SimpleBlockNode.render` / `.get_resolved_arguments` | 29 | 1 |

The six files that reach `parse_bits` are `syntax_tests/test_load.py` (11),
`syntax_tests/test_partials.py` (24), `syntax_tests/test_querystring.py` (28),
`syntax_tests/test_simple_tag.py` (3), `test_custom.py` (39) and
`test_nodelist.py` (5) — **110 rows**; `test_library.py` (28) and
`test_partials.py` (28) reach `Library` but never `parse_bits`. The library-facing
population is therefore **166 rows**, and the counts are per-file rows rather
than per-call, which is the granularity the M6 plan already used when it drew
its "does not claim" line at `test_custom`'s groups.

`Library.tag`/`Library.filter` reach 11 and 7 files, one of which is
`runtests.py` itself — Django's own runner registers libraries. That is a
measurement about the *fixture*, not about us, and a plan that quotes "11 files"
without it will overstate the engine's reach by one.

---

## 7. The import graph, and the capture hazard that comes with it

`library.py` imports from `.base` (`Node`, `Template`, `token_kwargs`) and
`.exceptions` (`TemplateSyntaxError`), plus `django.utils.html` and
`django.utils.inspect`.

**Who imports *it* is five sites, not two, and I had this wrong until I looked**
— the corrected list, each measured by `grep -rn` over the pinned tree:

| importer | takes |
|---|---|
| `django/template/__init__.py:70` | `Library` |
| `django/template/engine.py:10` | `import_library` |
| `django/template/backends/django.py:11` | `InvalidTemplateLibrary` |
| `django/contrib/admin/templatetags/base.py:3` | `InclusionNode`, `parse_bits` |
| `tests/template_tests/test_custom.py:5` (the suite) | `InvalidTemplateLibrary` |
| our `_impl/__init__.py:54`, `defaulttags.py:44`, `defaultfilters.py:30`, `loader_tags.py:9`, `engine.py:12` | `Library`, `Library`, `Library`, `Library`, `import_library` |

Two facts follow that the two-site version hid. **`InvalidTemplateLibrary` is
imported from outside the module** — by an engine *backend* and by the suite
itself — so it is a name whose identity the tree's `assertRaises` rows observe,
not an internal detail. And **`parse_bits` and `InclusionNode` have a consumer
outside the engine entirely**: Django's own admin imports both. That consumer is
outside the acceptance labels ([[acceptance-suite-is-template-tests-only]]), so
it will never appear in a red run — but it means `parse_bits`' signature is
public API, and a Rust-owned `parse_bits` that returns the wrong arity breaks a
package the 1,547 does not cover.

Two consequences, both measured:

**The name resolves only under the shim.** §3's `ModuleNotFoundError` is the
sharp form: `.library` works as a relative import only because
`django.template.__path__` carries Django's directory as its second entry. An
`_impl/library.py` fixes that, and the fix is *required*, not cosmetic — the
module is only ever loaded under the shim name today.

**A module-attribute patch cannot see a name bound at import — and my own
instrument demonstrated it on its first run.** The reach instrument reported:

```
django.template.library.import_library     calls=0        files=0
engine.import_library (bound name)         calls=11050    files=26
```

Same function, two spellings, and the difference is the M9 hazard
([[review-verify-against-pinned-tree]]'s universe capture) in the reporting
direction: `_impl/engine.py:12` bound Django's function object at import, so
wrapping the module attribute afterwards sees nothing. `11050` is the honest
number for `Engine.get_template_libraries`'s use of it, and `0` is the answer to
a question nobody asked.

The rule that follows for the plan: **`_impl/library.py` must define its names,
not re-export Django's.** A `from django.template.library import Library` in that
file would leave `_impl/__init__.py:54`, `defaulttags.py:44`, `defaultfilters.py:30`,
`loader_tags.py:9` and `engine.py:12` all holding Django's objects, `unshimmed()`
reading 0, and every `isinstance(lib, Library)` test in the suite answering about
the wrong class — the silent shape, with the meter green.

---

## 8. The gate, read through §10.3's vacuity rule

§10.3's M13 row is `test_partials`, `test_library`. Read through the rule — *a
gate naming a `.django/tests/` module is a gate only for the milestone that
interposes the behaviour it exercises*:

- **Not vacuous.** Both modules' behaviour flips onto our modules in this
  milestone: `test_library`'s 28 exercise `Library`'s registration API directly,
  and `test_partials`' 28 exercise the tags and the `name#partial` engine lookup.
  §5(a) is the measurement that the second group is load-bearing.
- **The row names half the gate, and the half it names is the quieter one.**
  `syntax_tests/test_partials.py` is **24 rows** against `test_partials`' 28 —
  fewer rows — but it carries the traffic: the mutation reddens 216 of its
  reports against 24 of the other's (§5(a)). The row names only the second. A
  plan that gates on `test_partials` alone gates on 10% of the milestone's
  measured exposure.
- **`test_library`'s 28 test no rendering.** They are registration semantics —
  decorator forms, `name=`, `filters`/`tags` population, `InvalidTemplateLibrary`,
  the `@wraps` `__wrapped__` identity, and **three** `skipUnless(PY314)` rows for
  deferred annotations (`test_library.py:83`, `:117`, `:166` — three, not two). The M6 plan already recorded this shape ("a gate on
  Python behaviour M6 must not break, not work M6 does"); at M13 they become the
  gate on *our transcription*, and the plan should say which of them is the
  interposition's evidence and which is merely passing.

---

## 9. Open questions the plan must answer, not inherit

Each is a decision with a measured input rather than a blank:

1. **Partials placement** — §4's (a)/(b)/(c), with the body-hole measurement as
   the reason. If (c), `registry.rs:117-132`'s comment needs amending rather than
   contradicting.
2. **`parse_bits` in Rust or in Python.** 250 calls from 6 files, and it is the
   file's only real compile-time logic. If Rust owns it, what crosses back is a
   pair of argument lists the host materialises into a real `SimpleNode` — and
   the tests that drive it are `test_custom.py`'s error rows, which assert
   Django's exact messages for unexpected keywords, repeated keywords, positional
   after keyword, too many positional, and the missing-value(s) error — see
   item 4 for the count and the line of each.
3. **What `import_library` is.** It calls `importlib.import_module` and reads
   `module.register`; both are Python by nature, so the likely answer is
   "transcription, and the meter counts the module rather than the function" —
   but the plan should say so rather than leaving the file half-claimed.
4. **The message surface, counted.** `parse_bits` raises **six**
   `TemplateSyntaxError`s (`library.py:409`, `:427`, `:432`, `:448`, `:460`,
   `:469` — takes_context, unexpected keyword, multiple values, positional after
   keyword, too many positional, missing value(s)) and `import_library` raises
   **two** `InvalidTemplateLibrary`s (`:483`, `:490`). These are strings the
   suite asserts — `test_custom.py`'s error rows take the `TemplateSyntaxError`
   ones, `test_custom.py:877`/`:887` the `InvalidTemplateLibrary` ones — so
   whether Rust owns them is the same question as (2).
5. **What replaces `test_host_tags.py`'s discriminator** (§5(b)). The replacement
   has to be a tag *one* side compiles, and after M13 the registry has no stubs —
   so the candidate is a tag the test itself registers, which is a different kind
   of fixture and needs its own justification.
6. **What pins the transcription's shape.** `library.py` is not a transcription
   of the `_impl/base.py` kind (one body at a time), because Rust may own only
   `parse_bits`. Does the file get a `check_transcriptions.py` declaration, and
   with which hunk?

---

## 10. Instrument traps found while measuring this

Three, all mine, all worth one line each so the next user does not pay for them.

**1. A green suite is not a working instrument.** The expiry instrument's first
run printed `Ran 1547 tests … OK (skipped=6)` while measuring **stock Django**:
`scripts/shimpath/sitecustomize.py` installs only when `DJANGO_RUST_TEMPLATES=1`,
and the script's re-exec chain did not set it. `sitecustomize`'s own docstring
warns about exactly this ("a working shim and an absent one are then
indistinguishable") and I still walked into it. The script now sets the variable
**and** asserts `_shim.installed()` before the runner starts.

**2. A spawn child does not inherit an in-process mutation.** With the shim
fixed, the mutated run was green *again*: this checkout's `runtests.py` defaults
`--parallel` to auto (`runtests.py:386-389`), the run went to 10 processes, and
macOS's `multiprocessing` **spawns** rather than forks. The script now passes
`--parallel 1` (which `DiscoverRunner` treats as serial) and asserts the mutated
function was consulted at least once, exiting if it was not — because "0
consultations" and "no failures" are indistinguishable in the output.

**3. A module-attribute patch cannot see an import-time binding.** §7's
`0` vs `11050`. The instrument prints both spellings rather than summing them,
and the script records why the second import must be spelled
`django.template.engine` and not `django_rust_templates._impl.engine` — the
latter raises `ModuleNotFoundError` for the reason §3 gives.

---

## Numbers at the recon

Measured on the closed M12 tree (`9115e61`), `bash scripts/check.sh` exit 0 /
`all checks passed`:

| instrument | value |
|---|---|
| cargo (`cargo test --workspace`) | 775 passed |
| unit (`run_unit_tests.sh`) | 442 OK |
| anchors | 1,422 occurrences, 838 of 838 cited, 0 failures |
| anchors backlog | unquoted 205, name-only 43 |
| differential | 48 cases, identical |
| Django `template_tests` | **1,547 OK (skipped=6)** |
| `unshimmed()` | `['django.template.library']` — 1 |
| `unshimmed(scope="django.templatetags")` | `[]` — 0 |
| `NOT_THE_ENGINE` / `SHADOWING` | 10 / 21 |
| `builtin_tag_stub_names()` | `['partial', 'partialdef']` |
| `builtin_tag_names_including_stubs()` | 25 |
| `NODE_CLASSES` | 40, of which 0 are `Partial*` |
| library census | 492 lines, 6 classes, 2 module-level defs, 8 `Library` methods |
| expiry mutation | 240 red (73 failures + 167 errors) in 2 modules |
| reach | `parse_bits` 250 calls / 6 files; library-facing population 166 rows |
