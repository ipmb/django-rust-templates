# M7 recon — inheritance (`{% block %}`, `{% extends %}`, `{% include %}`)

Measured on 2026-10-09 against `.django/` @ 6.0.9 (pinned), over `HEAD` =
`df18e5f`. §5.1's label correction landed at `99775f0`. Every number below was
produced by a command run during this task, not copied from a sibling record —
which is the one procedural change M6's close-out argued for, and this is the
first milestone that starts with it.

`LT` = `.django/django/template/loader_tags.py`, `B` =
`.django/django/template/base.py`, `C` = `.django/django/template/context.py`.

---

## 1. What M7 changes, and the exclusion it removes

Rust refuses all three tags today. Measured through `_native.parse` directly:

```text
'{% block x %}b{% endblock %}'       FAIL ParseError: Invalid block tag on line 1: 'block'. …
'{% extends "y" %}'                  FAIL ParseError: Invalid block tag on line 1: 'extends'. …
'{% include "z" %}'                  FAIL ParseError: Invalid block tag on line 1: 'include'. …
'A{% block x %}b{% endblock %}C'     FAIL ParseError: Invalid block tag on line 1: 'block'. …
```

Two mechanisms produce that, and M7 removes both:

- **The registry has no compile function for any of the three.**
  `crates/template-core/src/parser/registry.rs:272-277` asserts
  `builtin_tag_names().len() == 22`, "27 built-in names minus `block`, `extends`,
  `include`, `load` and `querystring`", and `:280-284` asserts all five are
  `builtin_tag(absent).is_none()`.
- **The host refuses them even though Django's registry has them.**
  `python/django_rust_templates/host.py:48` is
  `_M7_OWNED = frozenset({"block", "extends", "include"})`, and `has_tag`
  (`:117-122`) returns `False` for a `_M7_OWNED` command *before* the identity
  clause that decides everything else. So the exclusion is not a consequence of
  the ownership predicate — it is a carve-out in front of it, and it is three
  names wide.

The recorded reason for the carve-out is the seam, not the tags: `host.py:40-47`
says a host-compiled `{% extends %}` "would be *accepted* and *uncheckable* —
its must-be-first rule silently absent", and "accepting a tag whose ordering rule
does not exist here is worse than today's honest refusal".

---

## 2. The seam, as it stands: `must_be_first`

**Rust has the field and does not send it.** `NodeDesc.must_be_first`
(`crates/template-core/src/parser/desc.rs:406-407`) is documented in place as
"Read by `extend_nodelist` during the parse. **Not carried on the wire.**" It is
defaulted `false` (`:445`) and set by the `first()` builder (`:472-479`), whose
doc says "Django's `Node.must_be_first`, which exactly one built-in sets:
`ExtendsNode` (`loader_tags.py:87`)."

**The wire is a 5-tuple**, built by `node_desc_to_py`
(`crates/template-py/src/lib.rs:871-911`): `(class_name, attrs, nodelists,
token, slot)`. Pinned from Python by
`tests/test_ref_table.py:173-181`, which asserts `len(nodes[0]) == 5`. Carrying
`must_be_first` is therefore an arity change to a shape with a test on it — the
ticket is to decide the sixth element or a second channel, not to add a field.

**The rule itself already ships and is already tested.** `extend_nodelist` is
ported at `crates/template-core/src/parser/mod.rs:496` (doc from `:484`), and
its check (`:502`) is Django's `if node.must_be_first and nodelist.contains_nontext`
(`B:602`) with the message at `:512-518` reproducing `B:606-609`. It is
exercised by a `#[cfg(test)]` stand-in, `standin_first`
(`crates/template-core/src/parser/tags/mod.rs:196-200`), registered under the
test-only command `standin_first` (`:209-215`). Its doc comment states the
scope exactly: "in M2 the failure is unreachable for the nine tags (none sets
`must_be_first`) and is reached only by Task 3's stand-in."

**Which is what makes the M6 carve-out's *reason* precise.** Removing
`_M7_OWNED` without the wire change would accept `{% extends %}` in a position
Django rejects, because the check lives in Rust's `extend_nodelist` and reads a
`NodeDesc` field the host's descriptor never set. The M6 note calls the
resulting divergence "accepted *and* uncheckable"; the stronger form, measured in
§2 of the M6 record, is that `{{ x }}{% extends "y" %}` raises under stock Django
and would silently compile here.

**The origin half is already wired.** The message's second slot is
`origin_template_repr` (`parser/mod.rs:63`, `:436-439`), supplied from Python by
`_native.parse`'s `origin_template_repr` argument (`lib.rs:1446-1457`), documented
at `lib.rs:1499` as "Python's `repr()` of `Origin.template_name`, or…". The
`"the template"` fallback (`parser/mod.rs:438`) is Django's `B:606`.

**Correction, added 2026-10-09 (M7 Task 2):** that is the state this recon
measured, and it is no longer the shipping one. The field is
`Parser::origin_template_name: Option<String>` and holds the **raw**
`Origin.template_name`, because `{% extends %}` compares it against a path and
interpolates it with `%s` — a `repr` is wrong in all three of
`construct_relative_path`'s messages (plan A3). `extend_nodelist` derives the
`repr` through `Host::repr`. The fallback is no longer a `match` on `None`
either: Django's test is truthiness (`B:603`), so an empty name says "the
template" as well.

---

## 3. Django's machinery, cited

### 3.1 `do_block` / `BlockNode` / `BlockContext`

- `do_block` — `LT:215-243`. Reads `parser.__loaded_blocks` (`LT:228`), appends
  (`LT:232`), and creates it in an `except AttributeError` branch (`LT:233-234`).
  Because `do_block` is a module-level function, `__loaded_blocks` is the
  literal attribute name — no class-name mangling applies.
- `BlockNode` — `LT:42-83`. `__init__` `LT:43-46` (`name, nodelist, parent=None`);
  `render` `LT:51-69`; `super` `LT:71-83`.
- `BlockContext` — `LT:14-39`: `add_blocks` `LT:22-24` (insert at front), `pop`
  `LT:26-30`, `push` `LT:32-33`, `get_block` `LT:35-39` (peek at the end).
- `BLOCK_CONTEXT_KEY = "block_context"` — `LT:11`. Installed at `LT:137-138`,
  read at `LT:52`.

**`must_be_first` does not appear anywhere in `do_block` or `BlockNode`.** The
only three occurrences in `django/template/` are `LT:87` (`ExtendsNode`),
`B:1034` (`Node`'s default `False`, commented `B:1032-1033`), and `B:602` (the
check). Verified by exhaustive grep, so "exactly one built-in sets it" is a
measurement and not a figure of speech.

### 3.2 `ExtendsNode` / `do_extends`

- `ExtendsNode` — `LT:86-160`. `must_be_first = True` `LT:87`;
  `context_key = "extends_context"` `LT:88` (read at `LT:107`).
- `__init__` `LT:90-94`; **`LT:94`** is
  `self.blocks = {n.name: n for n in nodelist.get_nodes_by_type(BlockNode)}` —
  the reason the node reads its own subtree at construction.
- `find_template` `LT:99-115`; history is
  `context.render_context.setdefault(self.context_key, [self.origin])` (`LT:106-109`),
  and `Engine.find_template(…, skip=history)` (`LT:110-113`).
- `get_parent` `LT:117-132`; error `LT:120-125`
  (`"Invalid template name in 'extends' tag: %r."`).
- `render` `LT:134-160`. **`LT:159`** is
  `with context.render_context.push_state(compiled_parent, isolated_context=False):`
  — `False`, so the parent shares the render_context top dict and
  `BLOCK_CONTEXT_KEY` survives the parent's render. `LT:160` calls
  `compiled_parent._render(context)` explicitly rather than `render`.
- `do_extends` — `LT:292-312`. Arity message `LT:303-304`; relative path
  `LT:305`; the second-extends refusal `LT:308-311`; returns `LT:312`.

### 3.3 `IncludeNode` / `do_include` / `construct_relative_path`

- `IncludeNode` — `LT:163-211`. **`LT:164`** is `context_key = "__include_context"`,
  and it is **defined and never read** — grep over `django/` returns the
  definition and no use. The cache is keyed on the node object instead.
- `render` `LT:177-211`. **`LT:197`** is
  `cache = context.render_context.dicts[0].setdefault(self, {})` — `dicts[0]`,
  the bottom builtins dict, so the cache survives `push`/`pop`; keyed on
  `self`, the Python `IncludeNode` instance. `LT:199-201` fills on a miss via
  `context.template.engine.select_template(template_name)`. `LT:208-209` is the
  isolated branch (`template.render(context.new(values))`); `LT:210-211` the
  default one.
- `do_include` — `LT:316-370`. `LT:361-365` passes `allow_recursion=True`, unlike
  `do_extends` — the one asymmetry between the two path constructions.
- `construct_relative_path` — `LT:246-288`, three `TemplateSyntaxError`s
  (`LT:263-266`, `LT:275-278`, `LT:280-284`), re-quoting at `LT:285-288`.

### 3.4 The render chain

`Template.render` (`B:168-176`) → `push_state(self)` (`B:170`) → `_render`
(`B:165-166`) → `NodeList.render` (`B:1090-1091`) → `Node.render_annotated`
(`B:1044-1068`) → `ExtendsNode.render` (`LT:134`), which recurses into
`compiled_parent._render(context)` at `LT:160`.

`RenderContext` is `C:179-221`; `__contains__`/`__getitem__`/`get` read **only
the top dict** (`C:201-208`), which is why `IncludeNode` reaches past them to
`dicts[0]` and why `ExtendsNode` must pass `isolated_context=False`.

---

## 4. The test surface

### 4.1 Modules, and the baseline to preserve

Every count below was reproduced here. Runs are
`./scripts/run_django_suite.sh <label>`.

| module | classes (line) | run |
|---|---|---|
| `test_extends.py` | `ExtendsBehaviorTests` (L11) | 10 |
| `test_extends_relative.py` | `ExtendsRelativeBehaviorTests` (L11), `IncludeRelativeBehaviorTests` (L64), `ExtendsMixedBehaviorTests` (L93) | 8 + 4 + 3 = 15 |
| `test_loaders.py` | `CachedLoaderTests` (L15), `FileSystemLoaderTests` (L121), `AppDirectoriesLoaderTests` (L237), `LocmemLoaderTests` (L258) | 7 + 11 + 2 + 1 = 21 |
| `tests.py` | `TemplateTestMixin` (L16) driven by `TemplateTests` (L246) and `DebugTemplateTests` (L250) | 34 executed (17 × 2) |
| `syntax_tests/test_extends.py` | `InheritanceTests` (L89), `ExtendsNodeTests` (L457) | 45 + 1 = 46 |
| `syntax_tests/test_include.py` | `IncludeTagTests` (L20), `IncludeTests` (L232), `IncludeNodeTests` (L400) | 26 + 10 + 1 = 37 |
| `syntax_tests/test_named_endblock.py` | `NamedEndblockTests` (L7) | 7 |
| `syntax_tests/test_exceptions.py` | `ExceptionsTests` (L8) | 6 |

Measured runs, all green: `test_extends` + `test_extends_relative` +
`test_loaders` + `tests` → `Ran 80 tests … OK (skipped=1)`; the four
`syntax_tests` modules → `Ran 96 tests … OK`. **`80 + 96 = 176`**, which is
**11.4%** of the 1547.

Modules that touch the tags incidentally and are **not** counted in 176:
`test_partials.py` (28), `syntax_tests/test_partials.py` (24), `test_context.py`
(25), `test_custom.py` (39, incl. `InclusionTagTests` and the
`inclusion_extends1`/`inclusion_extends2` pair at L864),
`syntax_tests/test_basic.py` (47, incl. `BlockContextTests` at L397),
`syntax_tests/test_cycle.py` (25), `syntax_tests/test_for.py` (34),
`syntax_tests/test_if_changed.py` (22). Adding those takes the surface to 420,
**27.1%**.

`test_loaders.py` contains **no inheritance tags itself** — it is on the list
because loader resolution is the mechanism the two others depend on.

### 4.2 The two message tests, verbatim

`test_extends.py:188` and `:193` — the pair M7's message text must satisfy:

```python
msg = "{% extends 'base.html' %} must be the first tag in 'index.html'."   # L188-189
msg = "{% extends 'base.html' %} must be the first tag in the template."   # L194
```

Both from the same template string
`"{% block content %}B{% endblock %}{% extends 'base.html' %}"`. The first goes
through `get_template`, so the origin repr is `'index.html'`; the second through
`Engine().from_string`, so it is `"the template"` (`B:603-606`). **These are the
two corpus entries M7 owes**, and the first is the one that needs the harness to
pass an `Origin`.

### 4.3 Fixtures

19 files under `templates/` carry inheritance tags; `other_templates/` carries
none. Two further trees are load-bearing and are **not** under either:
`relative_templates/` (18 files, all of `test_extends_relative.py`) and
`recursive_templates/` (9 files, `test_extend_recursive` and friends).

---

## 5. Materialisation lifetime, and the gate restated

`test_include_cache` is `syntax_tests/test_include.py:371-397`, in `IncludeTests`
(L232). It renders `{% for x in vars %}{% include "include" %}{% endfor %}` over
an innermost `{% load custom %}{% counter %}` and asserts `"012345678"`. The
`CounterNode` is `custom.py:385-393`, holding mutable `self.count`:

```python
class CounterNode(template.Node):
    def __init__(self):
        self.count = 0
    def render(self, context):
        count = self.count
        self.count = count + 1
        return str(count)
```

It passes only if the single `CounterNode` is constructed **once** and
re-entered nine times. The caching that makes that true is `LT:197` keyed on the
node object, in `dicts[0]`.

### 5.1 The gate as the spec states it is vacuous today, and that is measured

The spec (§5.5, §10.3) calls `test_include_cache` "an explicit **M7 gate**". It
passes now — `Found 1 / Ran 1 / OK` — and it passes for reasons that have nothing
to do with our code, because **the suite does not call Rust at all**.

Measured, not inferred. With the shim installed
(`engine installed; 25 module(s) still served by Django`) and `_native.parse`
and `_native.compile_template` replaced by spies:

```text
Engine().from_string("{% if a|lower %}yes{% endif %}").render(...)
  → render: yes,  _native.parse calls: 0
whole 48-case differential corpus through the harness's own path
  → cases: 48,  _native calls: {'parse': 0, 'compile_template': 0}
```

Only `django.template` (the package init) and `django.template.exceptions` are
ours; `base`, `engine`, `defaulttags`, `loader_tags`, `context` and the loaders
are all still Django's, and `unshimmed()` is 25. So the 1547-test suite and the
48-case differential harness are **regression guards**, and the engine signal
lives in `tests/`, which drives `_native.parse` / `render_string` directly. This
is the plan working as written — §10.1's "M1–M8 is developed but not yet
interposed", `base` at M9, `loader_tags` at M11 — so it is not a defect in the
schedule. It is a defect in two *labels*:

- the differential harness printed "48 cases, identical (stock vs rust engine)",
  and the rust side ran Django. Its `ENGINE_MARKER` proves
  `django_rust_templates.installed()` — that the finder is up — which is not the
  claim the sentence makes;
- the spec's M7 gate says "`test_include_cache` passes, proving the shadow tree is
  materialised once and node identity is stable". Today the sentence is satisfied
  by a test whose node was built by Django.

**Both labels are corrected as of `99775f0`** (`differential.py` now prints
"identical (stock vs shim-installed)" and carries the reason at the marker;
`check.sh`'s step is "(shim installed)"), and M9's obligation is recorded where
it can be discharged: replace `installed()` with a call counter on the `_native`
entry points and assert `> 0` for the shim run, `== 0` for stock. That is the
commit where "the finder is up" and "Rust ran" coincide. Until then the harness
is truthful by assertion rather than by prose, and it is a regression guard.

### 5.2 The pattern, because this is the third instance

Three instruments in this project have now had a **label claiming more than its
measurement**:

| instrument | the label | the measurement |
|---|---|---|
| the stranded-anchor count | residue, i.e. stale citations | a detector — it counts citations that would break |
| the unquoted-anchor count | uniform weakness | shaped — concentrated in one construct class |
| `ENGINE_MARKER` / the suite step label | "rust engine" / "Rust ran" | `installed()` — the finder is up |

The mechanism is the same each time, and it is not carelessness: **a label is
written when the instrument is designed, the measurement is what survives
review, and nothing re-reads the label afterwards.** The checker has a manifest
that pins every citation; it has nothing for an output string, which is prose in
a script asserting a fact.

The cheap discipline this argues for: **when an instrument's measurement is
narrowed or redefined in review, re-read its output string in the same commit.**
All three would have been caught by that, and nothing else caught any of them.
It is also why the correction above is two files and not one — fixing
`differential.py` and leaving `check.sh`'s sibling label would reproduce the
locality failure the same record names elsewhere.

**M7 therefore owes the gate in a form that can fail.** Two candidate forms, and
the choice is M7's to make and record: reproduce the assertion through
`render_string` (Rust parse → shell materialise → Django render) so the
`CounterNode` identity is ours, or interpose enough for the suite's own run to
reach our parser. The first is available today; the second is M9/M11's schedule.
§5.5's own argument — `IncludeNode.render` keys `dicts[0]` on the Python
`IncludeNode` **instance** — is the reason the first is sufficient: the identity
that must survive is the materialised Python object's, and `render_string`
materialises it.

---

## 6. What M7 owes, exactly

Restating the spec's list (§10.4, spec lines 988-994, 1019-1022) with the
measurements that bound each item:

1. **Remove `_M7_OWNED`** (`host.py:48`) and the branch that reads it
   (`:118-121`). Three names.
2. **Carry `must_be_first` across the seam.** `desc.rs:406-407` says it is not on
   the wire; `node_desc_to_py` (`lib.rs:871-911`) builds a 5-tuple that
   `tests/test_ref_table.py:173-181` pins at length 5.
3. **`{% extends %}`'s compile function, and its message**
   `"{% %s %} must be the first tag in %s."` (`B:606-609`; Rust's port at
   `parser/mod.rs:512-518`), with the origin supplied (`lib.rs:1446-1457`).
   *Superseded:* Task 2 shipped it as `origin_template_name`, the raw attribute,
   with the repr derived in `extend_nodelist` — see the correction in §2.
4. **The corpus entries that reach it** — `test_extends.py:188` and `:193`, whose
   two expected strings differ only in the origin slot. `tests/parser_corpus.py:238-241`
   already carries the `contains_nontext` rows that set the rule's input and
   says of itself "(The `{% extends %}`-specific case is M7's)".
5. **`test_include_cache` in a form that can fail**, per §5.1.
6. **The other two compile functions** — `do_block` and `do_include` — plus
   `construct_relative_path` (`LT:246-288`), whose three errors and
   `allow_recursion` asymmetry (`LT:305` vs `LT:361-365`) are the parts easiest
   to get wrong quietly.

**What M7 does not owe:** the refusal path. `extend_nodelist`'s check ships
(`parser/mod.rs:502`) and is exercised on both branches by `standin_first`
(`tags/mod.rs:196-215`). M7 changes the *reason it can fire*, not the rule.

**Correction, added 2026-10-09 (M7 close-out).** Two defects in the list above,
and one of them is the one the plan's D2 names.

*It contradicts itself.* Item 2 asks M7 to carry `must_be_first` with the node;
item 6 asks it to write `do_block`/`do_include`/`construct_relative_path`. The
first is Design A's prerequisite and the second is Design B's deliverable — an
M7 that did both would have built both halves of two designs, and a reader
following this list as a settled fact would have tried to. **Resolved in favour
of Design B**, and item 2 was kept as a *separate* task anyway (plan Task 5),
because it is a real M6 defect that is independent of which design wins: a
library tag returning a `must_be_first` node is accepted today, in a position
Django refuses, whichever side compiles `{% extends %}`.

*Its spans were stale.* `desc.rs:404-407` named `nodelists` and one line of the
`must_be_first` doc; the field and its whole doc comment are `:406-407`.
`parser/mod.rs:436-444` and `:435` both pointed into `Parser::error` and
`invalid_block_tag`, which moved when Task 1 added `loaded_blocks`; the message
is `:512-518` and the check `:502`. `tags/mod.rs:194-212` began two lines above
`standin_first` and ended two below its registration. All corrected above.

**Worth keeping, because it is the third time this record has had it:** a span
that was right when it was written and a claim that was wrong when it was
written fail the same way, and only re-reading the tree tells them apart. The
plan's D3 is the same defect one layer up, and measured: its subject's line *was*
the check when D3 was written, and Task 1's `loaded_blocks` moved it. The spec
tracked the move at every commit; the plan did not, because the plan is a dated
record and nothing scans it. A number is not made durable by being a correction.

**This file is a dated record and its spans are dated with it.** The corrections
above are the values at the close-out commit; the *live* values are the
`scripts/anchors.txt` rows for those symbols, which is where `--check` guards
them and `--update` moves them. The numbers are kept here rather than deleted
because a record that cannot be read is not a record — but nothing should take a
span from this file as current.

---

## 7. Deliberately not measured

- **Which of the two forms §5.1 should take.** It is a design decision with a
  cost on each side, and it belongs in the plan rather than the recon.
- **Whether `{% include %}`'s recursion guard needs its own corpus row.** Django
  bounds it by `Engine.find_template`'s `skip=history` (`LT:110-113`) and
  `MAX_NESTING_DEPTH`; the existing recursion fixtures exist but no measurement
  here says which of them our loader path must reproduce.
- **The `parent=` parameter of `BlockNode.__init__`** (`LT:43`). It is stored
  (`LT:45`) and never read during rendering. Whether M7 carries it at all is a
  wire-shape question, not a behaviour one — recorded so that the omission is a
  decision rather than an oversight.
- **`IncludeNode.context_key` (`LT:164`) is dead in Django.** Recorded because a
  faithful port would carry an attribute nothing reads, and the honest options
  are to carry it with a comment or to leave it out and say why.
