# M5 — The remaining built-in tags: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rust owns the *parse* of Django's remaining hand-written tags — those whose compile functions reach `parser.compile_filter` — proven by corpus parity against the pinned tree, and by a gate that is four derived, enumerated, mutation-checked assertions rather than the phrase "syntax corpus parity".

**Architecture:** M3 established the parse/render split: Rust builds a `NodeDesc` tree, the Python shell materialises it into Django's *real* node classes, and Django renders them (§5.5). M5 is therefore a parse-only milestone — `forloop`, `render_context`, `set_upward` and every render-time behaviour stay Django's own code, unported. What M5 adds to `template-core` is (a) eleven tag modules that build descriptors, and (b) the one piece of plumbing the twelve tags uniquely need: a **host-owned reference table**, because `{% if %}`'s conditions and `{% cycle %}`'s node identity are values `template-core` cannot hold and must pass by handle.

**Tech Stack:** Rust (edition 2021, `regex`, no Python dependency in `template-core`), PyO3 0.29 in `template-py`, Python 3.14 shell, Django 6.0.9 pinned at `.django/`.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` — read §5.1, §5.2 (the seam), §5.5 (the shadow tree), §6 (ownership), §9 (the three test layers), §10.3, §10.4. This plan argues from the spec; where they disagree the spec wins and this plan is wrong.

## Global Constraints

Copied verbatim from the spec and M4's plan; every task's requirements implicitly include them.

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
  on `PATH` otherwise. (`scripts/env.sh` also sets `PIP_CONFIG_FILE=/dev/null`;
  the global pip config is broken and is deliberately not fixed.)
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
- **The plan publishes derivations and pointers, not snapshots of them.** Every
  fixable plan defect in this project has been a derivable value written into
  prose; and a copied code block propagates like a copied citation (wave B
  copied a stale `html.py:412-415` range out of `host.rs`, `7df817f`). Where a
  fact is computed somewhere, this plan names *where* and does not restate it.

## Review Focus

The five inputs and conditions the spec implies but no single task's tests
exercise. Each is pinned by a test in the task that owns the code, most likely
first.

1. **The corpus gate passes vacuously today, and that is the whole risk M5
   carries.** `test_the_corpus_covers_every_tag_with_a_success_path` hardcodes
   the eight M2 tags (`tests/test_parser_parity.py:284-299`), and there is no
   `M5_TAGS` constant anywhere in the tree. So every M5 tag could parse
   *wrongly into an empty tree* and the parity test would still be green,
   because no corpus template names it. M4's lesson — "a gate named in prose is a
   gate nobody can fail" — applies at full strength: "syntax corpus parity" is
   not a gate until something *derives the set and iterates it*. **Owned by
   Task 1 (A1-A3); pinned by the three mutations in Task 1 Step 7, each run and
   restored.**
2. **A descriptor class Python cannot materialise is silent until a corpus row
   hits it.** `NODE_CLASSES` is an explicit allow-list
   (`python/django_rust_templates/_impl/parser.py:60-72`) and `materialise_node`
   raises `LookupError` on a miss (`:117-122`) — but only for a node the corpus
   actually builds. A tag can emit `"ForrNode"` and every test stays green until
   someone writes a `{% for %}` template. The class name is derivable from the
   Rust source, so the check can be static. **Owned by Task 1 (A4).**
3. **`{% if %}` and `{% cycle %}` are the first real producers of
   `AttrValue::Ref`, and the render path cannot resolve one.** Today `Ref` is
   exercised only by hand-built descriptors in `tests/test_materialise.py:178-206`;
   no parse produces one, because no implemented tag needs a host-owned value.
   `_native.render` materialises with `resolve_ref=None`
   (`crates/template-py/src/lib.rs:1529-1531`), so a `Ref` reaching it raises
   `LookupError` (`parser.py:343-348`). M5 introduces the producers *and* the
   table; getting the table's lifetime wrong (per-parse vs persistent) makes
   handles ambiguous in exactly the way `PyTarget`'s doc warns about
   (`lib.rs:996-1000`). **Owned by Task 2; pinned by the `{% cycle … as %}` +
   `{% resetcycle %}` round-trip, which is *seen to fail* first.**
4. **`{% cycle %}`'s named-node identity is parser state, not descriptor
   state, and it must survive a *reused* name.** Django stores the *node object*
   in `parser._named_cycle_nodes[name]` (`defaulttags.py:693-696`) and
   `{% resetcycle %}` reads it back (`:1437`); two `{% cycle a b as c %}`
   occurrences must yield *one* node, the second returning the first's
   (`:669-671`). `template-core`'s `Parser` already carries
   `named_cycle_nodes: HashMap<String, NodeRef>` and `last_cycle_node`
   (`parser/mod.rs:71-72`), and `resetcycle.rs` already reads them — so M5's
   `cycle.rs` must *write* them, and the handle it stores must be the same one
   the descriptor emits. A second `CycleNode` built for a repeated name is a
   silent divergence with no error and no tree-shape difference the parity test
   would see unless the corpus has the repeated-name template. **Owned by
   Task 6; pinned by a repeated-name corpus row and a descriptor test.**
5. **`{% url %}` compiles *and* reverses nothing at parse time; a body that
   reverses early is wrong only under a settings state the corpus must set.**
   `url`'s compile builds FilterExpressions and returns `URLNode`; `reverse` is
   imported and called *inside* `render` (`defaulttags.py:495-511`), reading
   `context.request.current_app`. A Rust body that touched reverse at parse
   would be unobservable in a corpus that never renders — the parse tests compare
   trees. **Owned by Task 4; pinned by a corpus row parsed with no URLconf
   configured** (the parity harness's `DJANGO_CHECKOUT` state, which has none), so
   a parse-time reversal raises on *both* sides instead of only ours, and by the
   assertion that `url.rs` names no reverse entry point.

---

## Scope of M5

M5 is the second half of the tag layer. M2 built the nine tags that touch no
filter machinery (`M2_TAGS`); the rest wait on dependencies other rows claim.
The spec's M5 row (`§10.3`) is *"Remaining built-in tags (`for`, `if`, `with`,
`cycle`, …)"* — a prose ellipsis — and the milestone's whole point is that a
scope named by an ellipsis and gated by a phrase cannot be failed on either
axis. So the scope is **derived here, two independent ways, and the two must
agree.** They do, on **eleven**.

**Derivation 1 — the FilterExpression partition.** From
`tests/test_tag_inventory.py`'s existing split of the 27 registered names:

```

NEEDS_FILTER_EXPRESSION (14) - {extends, include, querystring}
    = {cycle, filter, firstof, for, if, ifchanged, lorem,
       regroup, url, widthratio, with}                              # 11
```

`extends` and `include` are in `NEEDS_FILTER_EXPRESSION` but are **Python-owned**
(§6): they are the loader tags, M7's row. `querystring` is removed by §D1.
This subtraction is pinned from the other side by
`RustRegistryTests.test_the_rust_registry_matches_the_loader_tags_subtraction`
(`tests/test_tag_inventory.py:200-206`).

**Derivation 2 — the registry minus the tags other rows claim, each exclusion
citing the row that takes it:**

```
27  registered                                    (test_tag_inventory.py:47-52)
−9  M2's row     autoescape comment csrf_token debug now resetcycle
                 spaceless templatetag verbatim
−3  M7's row     block extends include             (§10.3, "Inheritance")
−2  M13's row    partial partialdef                (§10.3, "Partials, …")
−1  M6's row     load                              (§10.3, "Library, …")
−1  M6, by §D1   querystring                       (this plan §D1)
=11
```

The two derivations produce the same eleven names, which is the cross-check:
one reads Django's *filter* dependency, the other reads the *milestone table*,
and a disagreement would mean one of the two is wrong.

**`url` is M5's, and the row that also claims it is corrected here.** M12's gate
lists `test_url` (§10.3). That is the *test file*
`.django/tests/template_tests/syntax_tests/test_url.py` — not under
`syntax_tests/i18n/`, which contains no `test_url` (measured: the i18n directory
holds `test_blocktranslate.py` … `test_underscore_syntax.py`, thirteen files, no
url test). The file exercises **only** `{% url %}` and imports `URLNode` from
`django.template.defaulttags` (`test_url.py:2`), so it becomes real signal when
**M11** interposes `defaulttags` — nothing in it needs an i18n, l10n, static, tz
or cache library. So the tag is M5's (§D8) and the file is M11's; M12's row
naming `test_url` is a stray, and Task 9's close-out moves it to M11's row.
Recorded as a spec correction, not applied silently.

The other three stubs in `parser/registry.rs` are **not** M5: `load` is M6
(`load.rs:20`), `partial`/`partialdef` are M13 (`partial.rs:16`,
`partialdef.rs:14`). M5 edits **no** shared file for these; each stub file
already says so in its own doc.

The **gate** is defined in §A below: four assertions, each derived and each
mutation-checked, so that "syntax corpus parity" names something a build can
fail.

---

## Design decisions this plan makes, and why

### §A — What "syntax corpus parity" is measured by

The phrase is not a gate. A gate is four assertions, all green, each reddening
on a nameable edit; the mutation for each is **run** before the plan is
committed (§11). The `TEMPLATES` corpus
(`tests/parser_corpus.py`) and the `ERRORS` corpus
(`tests/parser_error_corpus.py`) already exist and already drive real
comparisons against Django's own `Parser` — M5 does not invent an oracle, it
makes the *coverage* of the existing oracle falsifiable.

**A1 — the set is derived *and* enumerated.** `tests/test_tag_inventory.py`
gains:

```python
M5_TAGS = NEEDS_FILTER_EXPRESSION - {"extends", "include", "querystring"}
```

asserted equal, by name, to a literal set — the same two-sided shape
`NEEDS_FILTER_EXPRESSION` already uses (`:61-64` plus the name assertions at
`:156-162`). A Django pin bump that adds or removes a FilterExpression tag
reddens here rather than silently leaving the milestone table stale, which is
exactly the failure the module's own docstring records three occurrences of.

**A2 — every *implemented* tag has a corpus success path, and "implemented" is
derived from the engine.** `test_the_corpus_covers_every_tag_with_a_success_path`
currently hardcodes eight names (`test_parser_parity.py:284-299`) — a list that
cannot fail for any tag it does not name. It is rewritten to iterate
`set(_native.builtin_tag_names()) - set(_native.builtin_tag_stubs())`, where
`builtin_tag_stubs()` is new and **derived in Rust from the registry** (a name is
a stub iff its `CompileFn` body is `Err(unimplemented(...))`), not a Python
literal. The list of "which tags are done" is then the engine's own answer, and
M5's own switch is its complement:

```python
self.assertEqual(set(_native.builtin_tag_stubs()) & M5_TAGS, set())
```

That assertion is **red from Task 1 until Task 7**, tag group by tag group. It is
the milestone's gate and it is a set difference, not a phrase.

**A3 — every tag that can raise has an error-path row.** Django raises a literal
`TemplateSyntaxError` from most of these compile functions (§D lists each
message verbatim). Each gets ≥1 `(source, expected_message)` row in `ERRORS`,
and the coverage assertion iterates `M5_TAGS` minus the tags §D marks as having
no compile-time message of their own (`ifchanged`), so a forgotten tag is a
failure rather than an omission. The existing
`ErrorParityTests.test_the_corpus_matches_django` already verifies each expected
message against Django's real parser, so a wrong expectation fails loudly.

**A4 — no tag can emit a class Python cannot materialise.** A new test derives
each `class_name` a tag module can construct, by scanning
`crates/template-core/src/parser/tags/*.rs` for `NodeDesc::new("<C>")` and
`.with_nodelist("<C>", …)`, and asserts every `<C>` is a key of `NODE_CLASSES`.
In the other direction, every `NODE_CLASSES` key *not* emitted by a tag module
is asserted to be in a declared, commented allow-list (`TextNode`,
`VariableNode` — emitted by the parse core, not a tag). This is the static
version of a check that today only fires at runtime and only for a node the
corpus happens to build (§Review Focus 2).

The four assertions together are what the milestone table's M5 row means by
"syntax corpus parity", and the plan's §10.3 row is updated to cite Task 1
rather than the phrase (§M5 Close-out).

### §B — The host-owned reference table

`AttrValue::Ref(NodeRef)` is "an opaque handle to a host-owned value carried
inside a descriptor" (`crates/template-py/src/lib.rs:358-366`);
`template-core` stores the index and never reads it. Two M5 tags produce one:

* **`{% if %}`.** `smartif`'s `TemplateIfParser.create_var` calls
  `self.template_parser.compile_filter(value)` and stores the result
  (`smartif.rs` doc, `defaulttags.py:928-929`). The result is a Python
  `FilterExpression` wrapped in a `TemplateLiteral`, which `template-core`
  cannot hold — `IfParser::template(compiler, …)` takes a
  `dyn CompileFilter` whose `compile_filter(&self, token) -> Result<NodeRef,
  Error>` returns the handle (`crates/template-core/src/parser/smartif.rs:254-265`,
  `:313`). Its doc already states the M5 obligation: *"`template-core`'s real
  `compile_filter` (M3 Tasks 2-3) is not wired here: nothing calls `smartif` at
  M3, so the only M3 implementation is the test stand-in."* M5 supplies the real
  one.
* **`{% cycle %}`.** A named `CycleNode` stored in `parser._named_cycle_nodes`
  and read back by `{% resetcycle %}` (`defaulttags.py:693-696`, `:1437`). The
  handle must resolve to the *same Python `CycleNode` object*, so that
  `resetcycle` resets the very node `cycle` renders from.

**The design: one table, owned by the shell, threaded through the parse call.**

* The `Host` trait's parse-side impl in `template-py` gains a method that takes
  a filter *token string*, has Python build the real object (Django's
  `Parser.compile_filter` for `if`; the already-built `CycleNode` for `cycle`),
  pushes it onto a `Vec<Py<PyAny>>`, and returns `NodeRef(index)`. This is the
  same shape as the existing `check_filter` callback
  (`crates/template-py/src/lib.rs:930-938`), which already calls back into
  Python during parse — so the mechanism is proven, only its return widens.
* `_native.parse` returns the table alongside the tree. Its current return is
  `(nodes, contains_nontext)`; it becomes `(nodes, contains_nontext, refs)`, and
  the Python harnesses pass `resolve_ref=refs.__getitem__` into `materialise`.
  This closes the `resolve_ref=None` hole in the render path (§Review Focus 3):
  `_native.render` becomes able to resolve a `Ref` it built.
* **The table is per-parse and meaningless outside it** — the same lifetime rule
  `PyTarget.arena` states for the other kind of handle (`lib.rs:996-1000`).
  `NodeRef.index` is only ever valid against the table the producing parse
  returned.

**Why not a second structural variant instead of a handle.** `{% if %}`'s
condition *could* cross as `AttrValue::FilterExpr` (structural, like every other
tag's filter args). It does not, because Django builds the `FilterExpression`
— and *raises* on a bad one — at **parse** time (`CreateVar` → `compile_filter`,
inside `TemplateIfParser.__init__`, `smartif.rs:313` doc). Validating at
materialise time would move `{% if x|nope %}`'s error from parse to render, and
the error corpus compares parse-time messages. The handle keeps the validation
where Django puts it.

### §C — Why the descriptor completeness check can be static

`NODE_CLASSES` is an explicit dict, not `getattr`, deliberately:

> "a `getattr`-based lookup would resolve anything that happens to be in
> Django's modules, turning a Rust typo into a wrong node class instead of a
> loud failure" (`parser.py:56-60`).

The loud failure is `materialise_node`'s `LookupError` — but it fires only when
a descriptor of that class is materialised, i.e. only when the corpus builds
one. The class name is a string literal in the Rust source, so A4 checks it
statically, in both directions, and the Rust tag module set is the same
`FILTER_MODULES`-style glob M4's inventory test already uses
(`tests/test_filter_inventory.py`). Two allow-listed core-emitted names are
declared with their reason rather than left implicit.

### §D — Per-tag descriptors, and the two that need more than `AttrValue`

Every descriptor is built from the variants `desc.rs` already has
(`crates/template-core/src/parser/desc.rs:61-149`): `Str`, `SafeStr`, `Bool`,
`Int`, `Float`, `BigInt`, `None`, `Ref`, `Variable`, `FilterExpr`, `List`,
`Map`, `NodeListRef`. **No new `AttrValue` variant is needed for M5** — the
`if` condition is a `Ref` (§B) and a node's sub-nodelists are named or
`NodeListRef` (§D-general). The per-tag shapes below are the pinned source read
against those variants; each is a claim, and each task's first step re-measures
it against `.django/` @ 6.0.9 before writing code.

**D-general — sub-nodelists and repeated names.** A named nodelist becomes a
`__init__` kwarg; an *empty-named* nodelist is positional-only and reachable
only through `AttrValue::NodeListRef(i)` into the node's own `nodelists`
(`parser.py:109-113`, `:130`; `desc.rs:131-148`). `token_kwargs` results are a
`Vec<(String, AttrValue)>` (`AttrValue::Map`), **not** a map: a repeated key
keeps the first position and the last value, and only `dict(pairs)` reproduces
it (`base.py:1196`; `desc.rs` doc).

**D1 — `querystring` moves to M6 (evidence, not preference).** It is
`@register.simple_tag(name="querystring", takes_context=True)`
(`defaulttags.py:1286`) — the **only** `simple_tag` in `defaulttags.py` (the
other 25 registrations are `@register.tag`). It has no `do_querystring`;
`simple_tag` synthesises a compile function returning
`SimpleNode(func, takes_context, args, kwargs, target_var)`
(`library.py:127-145`). So parsing it in Rust requires (a) `SimpleNode` in
`NODE_CLASSES`, (b) a host handle to the Python callable `func`, and (c)
`parse_bits` (`library.py:388`) driven by that function's signature
(`(context, *args, **kwargs)`, `takes_context=True`). All three are the
`Library`/`simple_tag` machinery M6 owns. Hand-writing a bespoke node would
fail parity, because Django's own tree holds a `SimpleNode` and
`assert_trees_match` compares class names. **Decision: `M5_TAGS` excludes
`querystring`; `querystring.rs`'s stub milestone changes `"M5"` → `"M6"`; the
plan's §10.3 rows for M5 and M6 change to eleven (M5) and the M6 row is noted
to gain `querystring`.** This is the one scope change in the plan and it is
flagged for the reviewer.

**D2 — `filter`.** `FilterNode(filter_expr, nodelist)`
(`defaulttags.py:137-139`). Compile splits the raw token: `_, rest =
token.contents.split(None, 1)`; `filter_expr =
parser.compile_filter("var|%s" % rest)` (`:744-745`). So the descriptor's
`filter_expr` is `AttrValue::FilterExpr` built from the **synthetic source
`"var|" + rest`**, and `nodelist` is the named child. Refuses `escape`/`safe` by
reading `func._filter_name` (`:746-750`), message
`'"filter %s" is not permitted. Use the "autoescape" tag instead.'`. `var` is
the internal bound name; there is no `as`.

**D3 — `firstof`.** `FirstOfNode(variables, asvar=None)` (`:149-151`).
`variables` is an `AttrValue::List` of `FilterExpr`; `asvar` is a `Str` or
absent. Message `"'firstof' statement requires at least one argument"`
(`:799`); `{% firstof a b as var %}` detected at `:801-803`.

**D4 — `with`.** `WithNode(None, None, nodelist, extra_context=extra_context)`
(`:1687`) — the two legacy positionals are `None`, the modern data rides in
`extra_context`. Compile calls `token_kwargs(remaining_bits, parser,
support_legacy=True)` (`:1676`), yielding an `AttrValue::Map`. Two messages:
`"%r expected at least one variable assignment"` (`:1678-1680`) and
`"%r received an invalid token: %r" % (bits[0], remaining_bits[0])`
(`:1682-1684`). **The second goes through the host's `repr`, not
`py_repr_str`** — §10.4 of the spec already records this obligation, because
`remaining_bits[0]` is arbitrary user text and `py_repr_str` is exact on ASCII
only (`tags/mod.rs:114-150`, `debug_assert!(s.is_ascii())`).

**D5 — `widthratio`.** `WidthRatioNode(val_expr, max_expr, max_width,
asvar=None)` (`:534-538`). Three `FilterExpr` (the third is compiled, not a
literal — `parser.compile_filter(bits[3])`, so `{% widthratio a b c|add:1 %}`
is legal) plus `asvar`. Two compile messages: `"Invalid syntax in widthratio
tag. Expecting 'as' keyword"` (`:1639-1641`) and `"widthratio takes at least
three arguments"` (`:1643`). The render-time
`"widthratio final argument must be a number"` (`:548`) is Django's, unported.

**D6 — `lorem`.** `LoremNode(count, method, common)` (`:339-342`): `count` a
`FilterExpr` (from the *string* `"1"` default or the given bit, `:1171-1175`),
`method` a `Str` in `{"w","p","b"}`, `common` a `Bool` from the trailing
`random` keyword (`:1160-1169`). Message `"Incorrect format for %r tag" %
tagname` (`:1177`).

**D7 — `regroup`.** `RegroupNode(target, expression, var_name)` (`:362-365`),
all three compiled. Strict six-bit grammar; three messages:
`"'regroup' tag takes five arguments"` (`:1401`), `"second argument to 'regroup'
tag must be 'by'"` (`:1404`), `"next-to-last argument to 'regroup' tag must be
'as'"` (`:1406`). The `expression` is compiled from `var_name + "." + bits[3]`
(`:1414-1416`), so the descriptor's third `FilterExpr` is **not** `bits[3]` but
the `var_name`-prefixed string — a detail a body written from the tag's surface
syntax gets wrong.

**D8 — `url`.** `URLNode(view_name, args, kwargs, asvar)` (`:479-483`):
`view_name` a `FilterExpr`; `args` an `AttrValue::List` of `FilterExpr`;
`kwargs` an `AttrValue::Map` of `FilterExpr`; `asvar` a `Str` or absent.
`kwarg_re` (already `pub`, `kwargs.rs:41`) classifies each bit. Messages:
`"'%s' takes at least one argument, a URL pattern name." % bits[0]`
(`:1562-1564`) and `"Malformed arguments to url tag"` (`:1577`). **Compile does
not call `reverse`** (§Review Focus 5).

**D9 — `ifchanged`.** `IfChangedNode(nodelist_true, nodelist_false, *varlist)`
(`:262-265`); two named nodelists and `_varlist` as an `AttrValue::List` of
`FilterExpr`. No compile-time message of its own; the parser raises on an
unexpected block tag during `parser.parse(("else", "endifchanged"))`
(`:1054`). The `else` clause is a second sub-nodelist.

**D10 — `for`.** `ForNode(loopvars, sequence, is_reversed, nodelist_loop,
nodelist_empty=None)` (`:169-171`): `loopvars` an `AttrValue::List` of `Str`
(`re.split(r" *, *", " ".join(bits[1:in_index]))`, `:886`), `sequence` a
`FilterExpr`, `is_reversed` a `Bool`, two named nodelists. Three compile
messages, each naming the whole token contents:
`"'for' statements should have at least four words: %s"` (`:873-875`),
`"'for' statements should use the format 'for x in y': %s"` (`:880-883`),
`"'for' tag received an invalid argument: %s"` (`:889-891`). The render-time
unpacking `ValueError` (`:237-241`) is Django's, unported.

**D11 — `cycle`.** `CycleNode(cyclevars, variable_name=None, silent=False)`
(`:99-102`): `cyclevars` an `AttrValue::List` of `FilterExpr`; `variable_name` a
`Str` or absent; `silent` a `Bool`. Four messages (`:660`, `:666-668`,
`:670`, `:679-682`). **Writes parser state** (§Review Focus 4): a new name
pushes the fresh node into `named_cycle_nodes` and sets `last_cycle_node`
(`:693-700`); a repeated name returns the *existing* node's handle (`:669-671`)
and must **not** build a second descriptor.

**D12 — `if`.** `IfNode(conditions_nodelists)`: an `AttrValue::List` whose
elements are `AttrValue::List([condition, NodeListRef(i)])`, where `condition` is
an `AttrValue::Ref` (a `TemplateLiteral`, built host-side per §B) or
`AttrValue::None` for the `else` branch, and each branch nodelist is an
empty-named entry in `desc.nodelists` reached by `NodeListRef`. Built by walking
`smartif`'s arena after `IfParser::template(...).parse()` (`smartif.rs:313`,
`:345`). The `elif`/`else`/`endif` parse loop is Django's (`:994-1008`) and
mirrors `do_if`. One message of its own —
`'Malformed template tag at line {}: "{}"'.format(token.lineno, token.contents)`
(`:1014-1017`); the smartif parser raises the rest through
`ErrorClass::TemplateSyntax`.

---

## Task 1: The gate — four derived assertions, each seen to fail

**Files:**
- Modify: `tests/test_tag_inventory.py` (add `M5_TAGS`; two assertions)
- Modify: `tests/test_parser_parity.py` (`test_the_corpus_covers_every_tag_with_a_success_path` → derived)
- Modify: `tests/test_parser_errors.py` (add the error-corpus coverage assertion)
- Modify: `tests/test_materialise.py` (A4: the static `NODE_CLASSES` check)
- Modify: `crates/template-core/src/parser/registry.rs` (lift the stub predicate to `pub fn builtin_tag_stubs`)
- Modify: `crates/template-py/src/lib.rs` (expose `builtin_tag_stubs`)

**Interfaces:**
- Consumes: `_native.builtin_tag_names()` (exists, `lib.rs:158`-ish).
- Produces: `_native.builtin_tag_stubs() -> list[str]`, the names whose registry
  `CompileFn` is still `Err(unimplemented(...))`. **This is the milestone's
  switch**: M5 is done iff `set(builtin_tag_stubs()) & M5_TAGS == set()`.

**Why a Rust-side stub list and not a Python literal.** A hardcoded list of
"which tags are done" is the same defect one layer down: it is right until it is
not, and nothing fails when it drifts. The registry already knows — every stub's
body is `Err(unimplemented(name, milestone))` — so the check derives from that,
and the existing Rust test
`every_entry_is_reachable_and_every_unimplemented_one_is_a_stub`
(`registry.rs:196-265`) keeps asserting the *names*. When M5 lands a tag, its
name leaves `builtin_tag_stubs()` and the corpus-coverage test starts demanding
a row for it — automatically.

- [ ] **Step 1: Lift the stub predicate in `registry.rs`.**

The stub set is currently inline in the test. Move it to a `pub fn` beside
`builtin_tag_names`:

```rust
/// The registered names whose compile function is still a milestone stub.
///
/// Derived from the table, not from a list: a name is a stub iff its
/// `CompileFn` is the one `unimplemented` body. M5's gate is "this set and
/// `M5_TAGS` are disjoint"; a list would make that gate a comment.
pub fn builtin_tag_stubs() -> Vec<String> {
    REGISTRY
        .iter()
        .filter(|entry| entry.is_stub)
        .map(|entry| entry.name.to_string())
        .collect()
}
```

(If `Entry` has no `is_stub` field, add one and set it at the fifteen stub
sites; the existing test then asserts `builtin_tag_stubs()` has 15 names and
the same names it hardcodes today.)

- [ ] **Step 2: Expose it through PyO3.**

In `crates/template-py/src/lib.rs`, beside the `builtin_tag_names` registration
(the module `add_function`s at `:1541-1564`):

```rust
#[pyfunction]
fn builtin_tag_stubs() -> Vec<String> {
    template_core::parser::registry::builtin_tag_stubs()
}
```

and add `builtin_tag_stubs` to the module's `add_function` block.

- [ ] **Step 3: Add `M5_TAGS` and its two assertions.**

In `tests/test_tag_inventory.py`, after `DEFERRED_NO_FILTER_EXPRESSION`
(`:76`):

```python
# M5's eleven: every FilterExpression tag that is not Python-owned, minus the
# one the M5 plan's §D1 moves to M6 (`querystring`, a `simple_tag`).
M5_TAGS = {
    "cycle", "filter", "firstof", "for", "if", "ifchanged", "lorem",
    "regroup", "url", "widthratio", "with",
}
```

and, in `TagInventoryTests`:

```python
    def test_m5s_eleven_tags_and_the_two_derivations_agree(self):
        """The set, derived two ways, and the two must agree.

        Derivation 1 is the filter partition; derivation 2 is the registry minus
        every tag a later milestone's §10.3 row claims. The equality below is
        derivation 2, executable: M2's nine plus M5's eleven are exactly the
        twenty registered names no later row takes.
        """
        self.assertEqual(
            M5_TAGS, NEEDS_FILTER_EXPRESSION - {"extends", "include", "querystring"}
        )
        self.assertEqual(len(M5_TAGS), 11)
        self.assertEqual(
            M5_TAGS | M2_TAGS,
            REGISTERED - {"block", "extends", "include", "load",
                          "partial", "partialdef", "querystring"},
        )
```

- [ ] **Step 4: Rewrite the corpus-coverage test to derive.**

Replace the hardcoded list at `test_parser_parity.py:284-299` (keeping its
docstring's `resetcycle` note):

```python
    def test_the_corpus_covers_every_implemented_tag_with_a_success_path(self):
        """Every tag the engine can *parse* has a happy-path corpus row.

        Enumerated from the engine, not hardcoded: a tag leaves
        `builtin_tag_stubs()` when its body lands, and this test then demands a
        row for it. M2's list was eight names and could not fail for any tag it
        did not name — the vacuous gate this plan exists to close.
        """
        from tests.test_tag_inventory import M5_TAGS
        from django_rust_templates import _native
        implemented = set(_native.builtin_tag_names()) - set(_native.builtin_tag_stubs())
        joined = "\n".join(TEMPLATES)
        for tag in sorted(implemented):
            self.assertIn(f"{{% {tag}", joined, f"{tag} is implemented but not in the corpus")
```

(`_native` is imported at `test_parser_parity.py:53` as
`from django_rust_templates import _native`; `M5_TAGS` and `TEMPLATES` come from
the file's existing imports. The local imports in the snippet above are only how
this plan names them — use the module-level ones.)

**Note on `test_the_corpus_covers_every_tag_with_a_success_path`'s name.** It is
renamed to `..._every_implemented_tag_...`, because "every tag" is now false of a
milestone that leaves four stubs. The old name is the vacuity: a test named for a
set it does not derive.

- [ ] **Step 5: Add the M5-done switch and the error-corpus coverage.**

New test in `test_parser_parity.py` (or `test_tag_inventory.py`):

```python
    def test_no_m5_tag_is_still_a_stub(self):
        """M5's switch. Red until every M5 tag's body has landed."""
        from tests.test_tag_inventory import M5_TAGS
        from django_rust_templates import _native
        still = set(_native.builtin_tag_stubs()) & M5_TAGS
        self.assertEqual(still, set(), f"M5 has not landed: {sorted(still)}")
```

And in `tests/test_parser_errors.py`, an analogous assertion iterating
`M5_TAGS` against the sources in `ERRORS` for the tags §D gives a literal
message (all but `ifchanged`, whose only compile-time failures are the parser's
own).

- [ ] **Step 6: A4 — the static `NODE_CLASSES` check, in `test_materialise.py`.**

```python
TAG_MODULES = PROJECT_ROOT / "crates" / "template-core" / "src" / "parser" / "tags"
_CLASS = re.compile(r'NodeDesc::(?:new|with_nodelist)\s*\(\s*"([A-Za-z_][A-Za-z0-9_]*)"')
_TEXT = re.compile(r"NodeDesc::text\s*\(")

# Emitted by the parse core, not by a tag module: `TextNode` by
# `desc.rs::NodeDesc::text`, `VariableNode` by `parser/mod.rs`.
CORE_EMITTED_CLASSES = {"TextNode", "VariableNode"}


def tag_emitted_class_names():
    names = set()
    for path in TAG_MODULES.glob("*.rs"):
        body = path.read_text(encoding="utf-8").split("#[cfg(test)]")[0]
        body = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("//")
        )
        names.update(_CLASS.findall(body))
        if _TEXT.search(body):
            names.add("TextNode")
    return names
```

then:

```python
    def test_every_tag_emitted_class_is_materialisable(self):
        """No tag can name a node class Python cannot build.

        Today the only guard is `materialise_node`'s runtime `LookupError`, which
        fires only for a node the corpus actually builds. The class name is a
        string literal in the Rust source, so the check is static and the corpus
        cannot hide a typo.
        """
        missing = tag_emitted_class_names() - set(materialiser.NODE_CLASSES)
        self.assertEqual(missing, set(), f"NODE_CLASSES is missing {sorted(missing)}")

    def test_every_node_class_is_emitted_somewhere(self):
        dead = set(materialiser.NODE_CLASSES) - tag_emitted_class_names() - CORE_EMITTED_CLASSES
        self.assertEqual(dead, set(), f"unreachable NODE_CLASSES entries: {sorted(dead)}")
```

- [ ] **Step 7: Run the new guards, and confirm the *first* one fails for its
  intended reason — then run each mutation and restore with `cp`, never
  `git checkout` (§ the work is uncommitted).**

Run: `source scripts/env.sh && maturin develop && ./scripts/check.sh`
Expected: `test_no_m5_tag_is_still_a_stub` **FAILS**, naming all eleven tags.
That failure is the milestone's red. `test_every_tag_emitted_class_is_materialisable`
and `test_the_corpus_covers_every_implemented_tag_with_a_success_path` are green
(no M5 body exists yet, so nothing is demanded of the corpus).

Then, in turn, each mutation, run and restored:

```bash
cp python/django_rust_templates/_impl/parser.py /tmp/parser.py.bak
# delete the "DebugNode": DebugNode, line  -> A4-reds (DebugNode is tag-emitted)
# restore
```

```bash
cp tests/parser_corpus.py /tmp/corpus.py.bak
# delete the "{% csrf_token %}" row -> coverage-reds, naming csrf_token
# restore
```

```bash
cp tests/test_tag_inventory.py /tmp/inv.py.bak
# drop "url" from the M5_TAGS literal -> derivation-equality reds
# restore
```

- [ ] **Step 8: Commit.**

```bash
git add tests/test_tag_inventory.py tests/test_parser_parity.py tests/test_parser_errors.py \
        tests/test_materialise.py crates/template-core/src/parser/registry.rs \
        crates/template-py/src/lib.rs
git commit -m "tags: M5's gate — the scope and the coverage derived, both seen to fail"
```

The tree is red at this commit **on purpose**: `test_no_m5_tag_is_still_a_stub`
is the milestone's switch, and Tasks 3-7 turn it green tag by tag. `check.sh` is
run at each of those tasks, not here; this commit is the one exception the plan
declares, because a gate nobody has seen fail is a comment.

---

## Task 2: The host-owned reference table

**Files:**
- Modify: `crates/template-core/src/parser/host.rs` (the `Host` trait gains two methods)
- Modify: `crates/template-py/src/lib.rs` (`FilterCheckHost` gains the table; `parse` returns it; `render` threads it)
- Modify: `python/django_rust_templates/_impl/parser.py` (`materialise`/`materialise_node` accept and fill the table)
- Modify: `tests/test_parser_parity.py` (pass `resolve_ref` from the returned table)
- Modify: `tests/test_materialise.py`
- Test: `tests/test_ref_table.py` (new)

**Interfaces:**
- Produces: `Host::alloc_ref(&self) -> NodeRef` and
  `Host::compile_filter(&self, token: &str) -> Result<NodeRef, Error>`;
  `_native.parse` → `(nodes, contains_nontext, refs)`, where `refs` is a
  list-like of host objects the shell can read and fill;
  `materialise(desc, origin=None, refs=None, parser=None)`.
- Consumes: `AttrValue::Ref(NodeRef)` (exists), `smartif::CompileFilter` (exists).

**Design, and the one part most likely to need review.** Two tags need a
host-owned value at two different *times*, and the table serves both:

* `{% if %}` — Django builds the `FilterExpression` at **parse** time and raises
  on a bad one (`TemplateIfParser.__init__` → `create_var` → `compile_filter`).
  So `compile_filter` fills its slot during the parse, and the slot is read-only
  afterwards.
* `{% cycle %}` — the identity that must survive is a *materialised Python
  object*: `{% cycle a b as c %}` returns the node **and** stores that same
  object in `parser._named_cycle_nodes` (`defaulttags.py:693-696`), so a later
  `{% cycle c %}` returns the same object (`:669-671`) and `{% resetcycle c %}`
  resets it (`:1437`). Two `CycleNode`s with equal fields are *not* faithful:
  they render identically until a `resetcycle` separates them, which is a
  divergence the parse-time parity test **cannot see** (it compares
  descriptions, not identity). So the descriptor carries the slot index and the
  materialiser fills the slot on first construction:

  - a `CycleNode` descriptor carries an extra `AttrValue::Int` attr `"__slot"`;
  - `materialise_node`, on seeing `__slot=k`, returns `refs[k]` if it is
    already filled, else builds the node normally, stores it at `refs[k]`, and
    returns it;
  - `{% resetcycle %}`'s existing `node: AttrValue::Ref(slot)` then resolves,
    through the same table, to that object.

  Order is safe because materialisation is in document order and Django already
  requires the named cycle to exist before any reference to it
  (`defaulttags.py:665-671`).

The table is **per-parse** and meaningless outside it — the same lifetime rule
`PyTarget.arena` states for the other kind of handle (`lib.rs:996-1000`). This
also closes the render-path hole (§Review Focus 3): `_native.render` builds its
own table and passes it, so a `Ref` it created resolves instead of raising
`LookupError` (`parser.py:343-348`).

- [ ] **Step 1: Write the failing test.**

`tests/test_ref_table.py`:

```python
def ours_parse(template):
    """Parse through the Rust engine and materialise into Django nodes.

    Returns `(nodes, refs)`. `refs` is the per-parse reference table Task 2
    threads through, so a test can assert an `AttrValue::Ref` resolved to the
    same object the host built.
    """
    nodes, _contains_nontext, refs = _native.parse(template)
    nodelist = materialise(
        (nodes, _contains_nontext, refs),
        resolve_ref=refs.__getitem__,
        register_ref=refs.__setitem__,
        parser=filter_parser(),
    )
    return nodelist, refs


def test_a_repeated_named_cycle_is_one_object():
    """`{% cycle a b as c %}{% cycle c %}` materialises ONE CycleNode.

    Django returns the same object both times (`defaulttags.py:693-696`,
    `:669-671`), and `{% resetcycle c %}` resets that object. Two equal-but-
    distinct nodes pass a tree comparison and diverge only at render.
    """
    nodes, _ = ours_parse("{% cycle 'a' 'b' as c %}{% cycle c %}")
    first, second = nodes[0], nodes[1]
    assert first is second, "the second {% cycle c %} must be the first's node"


def test_resetcycle_targets_the_cycles_node():
    nodes, _ = ours_parse("{% cycle 'a' 'b' as c %}{% resetcycle c %}")
    assert nodes[1].node is nodes[0]
```

- [ ] **Step 2: Run it; expect a loud failure.**

Run: `./scripts/check.sh` (or `pytest tests/test_ref_table.py -v`)
Expected: **FAIL** — `LookupError: ... needs a resolve_ref callback; none was
passed to materialise` (`parser.py:344-348`), because nothing produces or
threads a ref yet. This is the "seen to fail" for §Review Focus 3.

- [ ] **Step 3: Add the two `Host` methods and the shell table.**

`crates/template-core/src/parser/host.rs`:

```rust
    /// Allocate an empty slot in the shell's per-parse reference table.
    fn alloc_ref(&self) -> NodeRef;
    /// `Parser.compile_filter(token)` — build the host's `FilterExpression` and
    /// return its handle. Called at *parse* time, so a bad filter raises here,
    /// which is where Django raises it.
    fn compile_filter(&self, token: &str) -> Result<NodeRef, Error>;
```

`crates/template-py/src/lib.rs`: `FilterCheckHost` (and the `render` host) gains
`refs: RefCell<Vec<Py<PyAny>>>` with `alloc_ref` pushing `None`-placeholder
entries and `compile_filter` calling Python's `parser.compile_filter(token)`
(the existing `check_filter` callback's sibling) and storing the result.
`parse_source` returns `(nodes, contains_nontext, refs)`.

- [ ] **Step 4: Thread it through `materialise` — the smallest diff that works.**

`python/django_rust_templates/_impl/parser.py` already has
`materialise(nodelist_desc, origin=None, resolve_ref=None, parser=None)` (`:75`)
and `materialise_node(desc, origin=None, resolve_ref=None, parser=None)` (`:92`),
and `resolve_ref` is already read (`:344-348`). **Do not rename it** —
`tests/test_materialise.py`'s hand-built descriptors pass `resolve_ref=` and
Task 2 is not the place to churn them. Three edits:

1. `materialise`'s unpack (`:77`) becomes tolerant of both arities, so every
   existing 2-tuple caller keeps working:

```python
    nodes, contains_nontext, *rest = nodelist_desc
    refs = rest[0] if rest else None
```

2. `materialise` and `materialise_node` gain `register_ref=None` after
   `resolve_ref`, threaded through both recursive calls (`:87`, `:125`) and into
   `_decode_value`.
3. `materialise_node` handles `__slot` beside the existing `token` attachment
   (`:133`-ish): read `attrs` for `"__slot"`, and if `register_ref` is not
   `None`

```python
    slot = kwargs.pop("__slot", None)
    if slot is not None:
        existing = resolve_ref(slot) if resolve_ref else None
        if existing is not None:
            return existing          # {% cycle name %} — the same object
        node = cls(**kwargs)
        register_ref(slot, node)
        return node                  # first construction fills the slot
```

   with the rest of `materialise_node` unchanged for every other node.

`tests/test_parser_parity.py`'s `ours_parse` (`:107-108`) passes the third
return value and the two callables:

```python
def ours_parse(source, debug=False):
    nodes, contains_nontext, refs = _native.parse(source, debug)
    return materialise(
        (nodes, contains_nontext, refs),
        resolve_ref=refs.__getitem__,
        register_ref=refs.__setitem__,
        parser=filter_parser(),
    )
```

- [ ] **Step 5: Run the test; expect it to pass.**

Run: `./scripts/check.sh`
Expected: `test_ref_table.py` PASS; the whole suite green **except** Task 1's
`test_no_m5_tag_is_still_a_stub`, which is still red until the tags land.

- [ ] **Step 6: Commit.**

```bash
git add crates/template-core/src/parser/host.rs crates/template-py/src/lib.rs \
        python/django_rust_templates/_impl/parser.py tests/test_ref_table.py \
        tests/test_parser_parity.py tests/test_materialise.py
git commit -m "tags: the per-parse reference table, so a Ref resolves instead of raising"
```

---

## The task shape for Tasks 3-7

Each of Tasks 3-7 is one tag group and follows the same seven steps. The steps
are stated once here because they are identical; **everything tag-specific —
the descriptor, the corpus rows, the error rows, the messages — is written out
in each task below, in full.** A tag's block is complete on its own; nothing in
it says "as in Task N".

**Step 1 — re-measure the descriptor against `.django/` @ 6.0.9.** §D's
per-tag shape is a claim written from a source read. Before writing code, print
the tag's compile function and confirm, in order: the node class name and its
`__init__` parameter names (`crates/template-core/src/parser/desc.rs` gives the
kwarg names), which arguments are `compile_filter`ed and from *what string*
(D7's `regroup` compiles `var_name + "." + bits[3]`, not `bits[3]`), and each
message literal character for character. The command:

```bash
sed -n '<start>,<end>p' .django/django/template/defaulttags.py
```

A mismatch is a plan defect: fix §D and this task before writing code, and say
so in the commit message.

**Step 2 — add the happy-path rows to `tests/parser_corpus.py`.** One row per
distinct parse shape the tag has, not one per tag: the `as` form and the bare
form are different trees. `TEMPLATES` is a flat list of source strings, so a row
is one line.

**Step 3 — run and watch the coverage test demand this tag.** The test derives
its tag list from `builtin_tag_stubs()` (Task 1 Step 4), so the moment the
corpus rows are in and the body is still a stub, it is green — the row is
present. The *red* this step expects is `test_no_m5_tag_is_still_a_stub`
naming the group's tags. That is the point: the switch is red for exactly the
tags that have not landed.

Run: `./scripts/check.sh`
Expected: FAIL, `test_no_m5_tag_is_still_a_stub` — `M5 has not landed: [...]`.

**Step 4 — implement the compile function** in
`crates/template-core/src/parser/tags/<name>.rs`, replacing
`Err(unimplemented("<name>", "M5"))` (`tags/<name>.rs:14`-ish; `querystring.rs`
is `:18` but it is not in this milestone). Build the `NodeDesc` from §D's shape.
Every message is `Error::TemplateSyntax` with the literal from the task's
message table; where the literal interpolates user text, interpolate the *same*
Python `repr` Django does (§D4's `with`, §D5's `lorem` `%r`, §D10's `for` `%s`
of the whole token) — `tags/mod.rs`'s `py_repr_str` is exact on ASCII only.

**Step 5 — run and watch the switch go green for this group.**

Run: `./scripts/check.sh`
Expected: PASS — `Ran <N> tests … OK (skipped=6)`, with `N` grown by the rows
added in Step 2 and the tests added in Step 6.

**Step 6 — add the error rows, and see each message fail once.** Add the task's
`(source, expected message)` rows to `tests/parser_error_corpus.py::ERRORS`.
Then, for each row, deliberately break the message in the Rust source (change a
word), run, and confirm the corpus reports *that exact row* failing against
Django; restore with `cp` — **never `git checkout`**, the work is uncommitted.
A message that has never been seen to fail is a string, not a check.

**Step 7 — commit**, with the tag group's own message: each task below ends
with the exact `git commit -m` to use, and each names the files it touched. The
shared part is only that the message states which messages were seen to fail.

---

## Task 3: The five flat tags — `filter`, `firstof`, `with`, `widthratio`, `lorem`

**Files:**
- Modify: `crates/template-core/src/parser/tags/{filter,firstof,with,widthratio,lorem}.rs`
- Modify: `tests/parser_corpus.py`, `tests/parser_error_corpus.py`

**Interfaces:**
- Consumes: `NodeDesc::new`/`.with_nodelist`/`.attr`/`.nodelist`
  (`desc.rs`), `AttrValue::{Str, Bool, FilterExpr, List, Map, None}`,
  `Error::TemplateSyntax`.
- Produces: nothing later tasks call. These five have no shared state.

**`filter`** — `FilterNode(filter_expr, nodelist)` (`defaulttags.py:725-757`).
`filter_expr` = `parser.compile_filter("var|" + rest)` where `rest` is
`token.contents.split(None, 1)[1]` (`:744-745`) — the **synthetic source**, so
the descriptor's `FilterExpr` is built from `"var|" + rest` and not from any
substring of the token as written. `nodelist` is `parser.parse(("endfilter",))`,
a named child.

| message | source |
|---|---|
| `'"filter %s" is not permitted. Use the "autoescape" tag instead.'` | `:748-750`, `% filter_name` |

The refusal reads `func._filter_name` off the *compiled* filter (`:746-747`), so
it must run after `compile_filter`, against the filter names the host returns —
not by re-parsing the text. Corpus:

```python
    "{% filter lower %}X{% endfilter %}",
    "{% filter lower|capfirst %}X{% endfilter %}",
    "{% filter lower %}{% endfilter %}",
    "{% filter lower %}{{ x }}{% endfilter %}",
```

Errors: `("{% filter escape %}x{% endfilter %}", '"filter escape" is not permitted. Use the "autoescape" tag instead.')`
and the `safe` twin.

**`firstof`** — `FirstOfNode(variables, asvar=None)` (`:759-806`). `variables`
is `AttrValue::List` of `FilterExpr`; `asvar` is `Str` or absent. The `as` form
is `bits[-2] == "as"` on `bits = split_contents()[1:]` (`:800-802`), so
`{% firstof a as b c %}` leaves `c` in `variables` — only a **trailing** `as`.
Corpus:

```python
    "{% firstof a %}",
    "{% firstof a b c %}",
    "{% firstof a b as v %}",
    '{% firstof a "fallback" %}',
    "{% firstof a|lower b|upper %}",
    "{% firstof a b as v %}x",
```

Error: `("{% firstof %}", "'firstof' statement requires at least one argument")`
(`:799`, no `%s` — the literal is the whole message).

**`with`** — `WithNode(None, None, nodelist, extra_context=extra_context)`
(`:1687`). The two leading positionals are literally `None` (`:1687`); the
modern data is the `extra_context` kwarg, built by
`token_kwargs(remaining_bits, parser, support_legacy=True)` (`:1676`) — an
`AttrValue::Map`, i.e. an ordered pair list, not a hash map (§D-general).
`nodelist` is `parser.parse(("endwith",))`.

| message | source |
|---|---|
| `"%r expected at least one variable assignment" % bits[0]` | `:1678-1680` |
| `"%r received an invalid token: %r" % (bits[0], remaining_bits[0])` | `:1682-1684` |

Both interpolate through Python `repr` (§D4): the tag name and the offending
token. Corpus:

```python
    "{% with a=1 %}{{ a }}{% endwith %}",
    "{% with a=1 b=2 %}{{ a }}{% endwith %}",
    "{% with a=1 %}{% endwith %}",
    "{% with a=1 b=2 %}{{ a }}{{ b }}{% endwith %}",
    "{% with a=1|add:2 %}{{ a }}{% endwith %}",   # token_kwargs compiles the value
    "{% with %}x{% endwith %}",                    # legacy form: no extra_context
```

Errors: `("{% with %}{% endwith %}", "'with' expected at least one variable assignment")`
and a malformed-token row, e.g.
`("{% with a=1 b %}{% endwith %}", "'with' received an invalid token: 'b'")`.

**`widthratio`** — `WidthRatioNode(val_expr, max_expr, max_width, asvar=None)`
(`:1611-1651`). **All three are `FilterExpr`** — the third is
`parser.compile_filter(max_width)`, not an integer literal, so
`{% widthratio a b 100|add:1 %}` parses (`:1647-1649`). Arity is exact: four
bits or six (with `as`); anything else is
`"widthratio takes at least three arguments"` (`:1643`).

| message | source |
|---|---|
| `"Invalid syntax in widthratio tag. Expecting 'as' keyword"` | `:1639-1641` |
| `"widthratio takes at least three arguments"` | `:1643` |

Corpus:

```python
    "{% widthratio a b 100 %}",
    "{% widthratio a b 100 as w %}",
    "{% widthratio a|add:1 b 100 %}",
    "{% widthratio a b c|add:1 %}",
```

Errors: `("{% widthratio a b %}", "widthratio takes at least three arguments")`
and `("{% widthratio a b c d e %}", "widthratio takes at least three arguments")`
(five bits falls to the `else`, not to the `as` check) and
`("{% widthratio a b c nope w %}", "Invalid syntax in widthratio tag. Expecting 'as' keyword")`.

**`lorem`** — `LoremNode(count, method, common)` (`:1135-1179`). `count` is a
`FilterExpr` compiled from a **string** — the literal `"1"` when no count bit is
given, else the bit (`:1171-1175`); `method` is a `Str` in `{"w","p","b"}`,
defaulting to `"b"` (`:1164-1168`); `common` is a `Bool` = `bits[-1] != "random"`
(`:1160-1162`). Note the count bit is popped only when `len(bits) > 1` after the
method and random bits are removed, so `{% lorem w %}` reads `w` as the method
and the count as `"1"`.

| message | source |
|---|---|
| `"Incorrect format for %r tag" % tagname` | `:1177` |

`%r` of the tag name string → `Incorrect format for 'lorem' tag`. Corpus:

```python
    "{% lorem %}",
    "{% lorem 3 %}",
    "{% lorem 2 p %}",
    "{% lorem 2 w random %}",
    "{% lorem w %}",
    "{% lorem random %}",
```

Errors: `("{% lorem 3 p nope %}", "Incorrect format for 'lorem' tag")`.

- [ ] Run the seven steps above for all five tags in this one task, in the order
  `filter`, `firstof`, `with`, `widthratio`, `lorem` (simplest descriptor first,
  so the shape is settled before `filter`'s synthetic source). Then commit:

```bash
git add crates/template-core/src/parser/tags/filter.rs \
        crates/template-core/src/parser/tags/firstof.rs \
        crates/template-core/src/parser/tags/with.rs \
        crates/template-core/src/parser/tags/widthratio.rs \
        crates/template-core/src/parser/tags/lorem.rs \
        tests/parser_corpus.py tests/parser_error_corpus.py
git commit -m "tags: filter, firstof, with, widthratio and lorem, with every message seen to fail once"
```

---

## Task 4: The two that compile a derived string — `regroup`, `url`

**Files:**
- Modify: `crates/template-core/src/parser/tags/{regroup,url}.rs`
- Modify: `crates/template-core/src/parser/kwargs.rs` (only if `kwarg_re` needs a Rust-visible accessor)
- Modify: `tests/parser_corpus.py`, `tests/parser_error_corpus.py`

**Interfaces:**
- Consumes: `kwarg_re` (`kwargs.rs:41`, already `pub`), `AttrValue::{Str, FilterExpr, List, Map, None}`.

**`regroup`** — `RegroupNode(target, expression, var_name)`
(`:1353-1419`). Strict six-bit grammar (`len(bits) != 6`, `:1400-1401`).
`target = compile_filter(bits[1])` (`:1402`); **`expression =
compile_filter(var_name + "." + bits[3])`** (`:1414-1416`) — the descriptor's
third value is built from `bits[5] + "." + bits[3]`, *not* from `bits[3]`. This
is §D7's flagged detail and the one a body written from the tag's surface syntax
gets wrong; Step 1 re-measures it and the corpus row
`{% regroup m|dictsort:"k" by k as g %}` must produce a `FilterExpr` whose source
is `g.k`.

| message | source |
|---|---|
| `"'regroup' tag takes five arguments"` | `:1400-1401` |
| `"second argument to 'regroup' tag must be 'by'"` | `:1403-1404` |
| `"next-to-last argument to 'regroup' tag must be 'as'"` | `:1405-1406` |

Corpus:

```python
    "{% regroup m by k as g %}",
    '{% regroup m|dictsort:"k" by k as g %}',
    "{% regroup m by k as g %}{{ g }}",
```

Errors: the three arity/keyword rows, e.g.
`("{% regroup m by k %}", "'regroup' tag takes five arguments")`,
`("{% regroup m ny k as g %}", "second argument to 'regroup' tag must be 'by'")`,
`("{% regroup m by k is g %}", "next-to-last argument to 'regroup' tag must be 'as'")`.

**`url`** — `URLNode(view_name, args, kwargs, asvar)` (`:1515-1586`).
`view_name = compile_filter(bits[1])` (`:1565`); a trailing `as` on
`bits = bits[2:]` (`:1568-1571`); each remaining bit is classified by
`kwarg_re.match` (`kwargs.rs:41`), a name→`compile_filter(value)` into `kwargs`
(`AttrValue::Map`), a bare name→`compile_filter(bit)` appended to `args`
(`AttrValue::List`) (`:1574-1584`). **Compile calls no reversal** — the
`ViewDoesNotExist`/`NoReverseMatch` failures are `URLNode.render`'s and are
unported (§Review Focus 5); a body that resolves the view name at parse time
diverges on the first template whose URLconf is not loaded yet.

| message | source |
|---|---|
| `"'%s' takes at least one argument, a URL pattern name." % bits[0]` | `:1562-1564` |
| `"Malformed arguments to url tag"` | `:1576-1577` |

Corpus:

```python
    "{% url 'view' %}",
    "{% url 'view' a b %}",
    "{% url 'view' k=v %}",
    "{% url 'view' a k=v %}",
    "{% url 'view' as u %}",
    "{% url 'view' a k=v as u %}",
    "{% url 'view' k=v|default:'x' %}",
```

Errors: `("{% url %}", "'url' takes at least one argument, a URL pattern name.")`
and `("{% url 'view' k=v nope= %}", "Malformed arguments to url tag")`.

- [ ] Run the seven steps for both. The two share a task because both compile a
  string that is *not* a substring of the token — the property Step 1 exists to
  check.

```bash
git add crates/template-core/src/parser/tags/regroup.rs \
        crates/template-core/src/parser/tags/url.rs \
        tests/parser_corpus.py tests/parser_error_corpus.py
git commit -m "tags: regroup and url — both compile a derived string, not a token substring"
```

---

## Task 5: The two with a block body — `ifchanged`, `for`

**Files:**
- Modify: `crates/template-core/src/parser/tags/{ifchanged,for}.rs`
- Modify: `tests/parser_corpus.py`, `tests/parser_error_corpus.py`

**Interfaces:**
- Consumes: the parser's block-parsing entry point (`parser.parse(("else",
  "endifchanged"))` and `parse(("empty", "endfor"))` have Rust equivalents in
  `parser/mod.rs`; Step 1 confirms the names), `AttrValue::{Str, Bool, FilterExpr, List}`.

**`ifchanged`** — `IfChangedNode(nodelist_true, nodelist_false, *varlist)`
(`:1024-1073`). **Argument order is true, false, then the values.** Two *named*
nodelists; without an `else`, `nodelist_false` is an **empty `NodeList()`**
(`:1057-1058`), not absent — a descriptor that omits it materialises a node
whose `nodelist_false` is missing and `describe` differs. `varlist` is an
`AttrValue::List` of `FilterExpr` over `bits[1:]` (`:1059`). No message of its
own; the two block tags raise through the parser.

Corpus:

```python
    "{% ifchanged %}x{% endifchanged %}",
    "{% ifchanged %}x{% else %}y{% endifchanged %}",
    "{% ifchanged a %}x{% endifchanged %}",
    "{% ifchanged a b %}x{% else %}y{% endifchanged %}",
```

Errors: none of its own — but a row proving the *else* is required to be the
first block tag: `("{% ifchanged %}{% else %}{% else %}{% endifchanged %}",
"Invalid block tag on line 1: 'else', expected 'endifchanged'.")` — Step 1
measures Django's exact wording for the second `else` and this row is written
from that measurement, not from this plan.

**`for`** — `ForNode(loopvars, sequence, is_reversed, nodelist_loop,
nodelist_empty=None)` (`:808-931`). `loopvars` is `AttrValue::List` of `Str` from
`re.split(r" *, *", " ".join(bits[1:in_index]))` (`:886`) — note the split is on
the **joined** bits, so `{% for a , b in x %}` yields `["a", "b"]` and the
`invalid_chars` check (`:887-891`) is what rejects a filter separator or a quote
inside a loop variable. `is_reversed = bits[-1] == "reversed"`, and `in_index`
is `-3` when reversed, `-2` otherwise (`:877-879`). `nodelist_empty` comes from
the `{% empty %}` branch and is `None` when absent (`:891-930`).

| message | source |
|---|---|
| `"'for' statements should have at least four words: %s" % token.contents` | `:872-875` |
| `"'for' statements should use the format 'for x in y': %s" % token.contents` | `:879-883` |
| `"'for' tag received an invalid argument: %s" % token.contents` | `:888-891` |

All three `%s` the **whole `token.contents`**, verbatim — not a bit. Corpus:

```python
    "{% for x in y %}{{ x }}{% endfor %}",
    "{% for x in y reversed %}{{ x }}{% endfor %}",
    "{% for x in y %}{{ x }}{% empty %}none{% endfor %}",
    "{% for a, b in y %}{{ a }}{% endfor %}",
    "{% for a , b in y %}{{ a }}{% endfor %}",
    "{% for x in y|slice:':2' %}{{ x }}{% endfor %}",
```

Errors: one per message, e.g. `("{% for x in %}", "'for' statements should have at least four words: for x in")`,
`("{% for x of y %}", "'for' statements should use the format 'for x in y': for x of y")`,
`("{% for a|b in y %}{% endfor %}", "'for' tag received an invalid argument: for a|b in y")`.

- [ ] Run the seven steps for both.

```bash
git add crates/template-core/src/parser/tags/ifchanged.rs \
        crates/template-core/src/parser/tags/for.rs \
        tests/parser_corpus.py tests/parser_error_corpus.py
git commit -m "tags: ifchanged and for, and the empty-nodelist default they share"
```

---

## Task 6: `cycle` and the identity it must keep — plus `resetcycle`'s messages

**Files:**
- Modify: `crates/template-core/src/parser/tags/cycle.rs`
- Modify: `crates/template-core/src/parser/tags/resetcycle.rs` (messages only; it already emits `AttrValue::Ref`, `:34`, `:42`)
- Modify: `tests/parser_corpus.py`, `tests/parser_error_corpus.py`, `tests/test_ref_table.py`

**Interfaces:**
- Consumes: `parser.named_cycle_nodes` / `parser.last_cycle_node`
  (`resetcycle.rs:34`, `:42` already read them), `AttrValue::{Str, Bool, FilterExpr, List, Ref, Int}`, and the `__slot` mechanism from Task 2.
- Produces: the `__slot` attr convention every `CycleNode` descriptor carries.

**`cycle`** — `CycleNode(cyclevars, variable_name=None, silent=False)`
(`:612-702`). Five parse shapes, and the shapes are the design:

1. `len(args) < 2` → refuse (`:657-658`).
2. `len(args) == 2` → **a name reference**: return the *existing* node
   (`:660-671`), or refuse. The descriptor for this form is the *reuse*
   descriptor — it carries the `__slot` of the node the name was bound to and
   Task 2's materialiser returns the already-built object. It must **not** build
   a second descriptor with the same values; two equal `CycleNode`s pass a tree
   comparison and diverge at the first `{% resetcycle %}` (§Review Focus 4).
3. `len(args) > 4` with `args[-3] == "as"` → `... as name silent` (`:675-686`).
4. `len(args) > 4` with `args[-2] == "as"` → `... as name` (`:687-688`).
5. otherwise → the anonymous form (`:698-700`).

The values are `args[1:-2]` in the `as` forms and `args[1:]` otherwise
(`:691`, `:699`) — **`as` consumes two bits.** Every shape sets
`parser.last_cycle_node = node` (`:700`), which is what a bare
`{% resetcycle %}` reads.

| message | source |
|---|---|
| `"'cycle' tag requires at least two arguments"` | `:657-658` |
| `"No named cycles in template. '%s' is not defined" % name` | `:663-665` |
| `"Named cycle '%s' does not exist" % name` | `:666-667` |
| `"Only 'silent' flag is allowed after cycle's name, not '%s'." % args[-1]` | `:678-682` |

**`resetcycle`** — `ResetCycleNode(node)` (`:1421-1443`). Already emitting a
`Ref`; this task adds its three messages, which were not part of M2's scope:

| message | source |
|---|---|
| `"%r tag accepts at most one argument." % args[0]` | `:1431-1432` |
| `"Named cycle '%s' does not exist." % name` | `:1438-1439` |
| `"No cycles in template."` | `:1440-1443` |

Corpus:

```python
    "{% cycle 'a' 'b' %}",
    "{% cycle 'a' 'b' 'b' %}",
    "{% cycle 'a' 'b' as c %}",
    "{% cycle 'a' 'b' as c silent %}",
    "{% cycle 'a' 'b' as c %}{% cycle c %}",
    "{% cycle 'a' 'b' as c %}{% resetcycle %}",
    "{% cycle 'a' 'b' as c %}{% resetcycle c %}",
    "{% for x in y %}{% cycle 'a' 'b' %}{% endfor %}",
```

Errors: `("{% cycle %}", "'cycle' tag requires at least two arguments")`,
`("{% cycle foo %}", "No named cycles in template. 'foo' is not defined")`,
`('{% cycle "a" "b" as c %}{% cycle c2 %}', "Named cycle 'c2' does not exist")`,
`('{% cycle "a" "b" c as nope %}', "Only 'silent' flag is allowed after cycle's name, not 'nope'.")`,
`("{% resetcycle a b %}{% cycle 'x' 'y' %}", "'resetcycle' tag accepts at most one argument.")`,
`("{% resetcycle nope %}{% cycle 'x' 'y' %}", "Named cycle 'nope' does not exist.")`,
`("{% resetcycle %}", "No cycles in template.")`.

- [ ] **Step A: extend `tests/test_ref_table.py`** with the case that proves the
  *reuse* descriptor does not rebuild — the second node must be the first
  object, and a `{% resetcycle c %}` between them must reset that object:

```python
def test_cycle_reuse_and_resetcycle_share_one_object():
    nodes, _ = ours_parse(
        "{% cycle 'a' 'b' as c %}{% cycle c %}{% resetcycle c %}"
    )
    assert nodes[0] is nodes[1] is nodes[2].node
```

- [ ] **Step B: run it against the stub** — it fails with the `unimplemented`
  message, which is the "seen to fail" for the shape, not just the tag.
- [ ] **Step C: run the seven steps** for `cycle`, and the message steps for
  `resetcycle`. The two are one task because `resetcycle`'s `Ref` is only
  meaningful once a `CycleNode` descriptor fills its slot: the `no cycles in
  template` message and the identity mechanism are the same parser state.
- [ ] **Step D: commit.**

```bash
git add crates/template-core/src/parser/tags/cycle.rs \
        crates/template-core/src/parser/tags/resetcycle.rs \
        tests/parser_corpus.py tests/parser_error_corpus.py tests/test_ref_table.py
git commit -m "tags: cycle's five shapes, and the one object they must all share"
```

---

## Task 7: `if` — the tag whose condition is a host object

**Files:**
- Modify: `crates/template-core/src/parser/tags/if.rs`
- Modify: `crates/template-core/src/parser/smartif.rs` (wire the real `CompileFilter`)
- Modify: `tests/parser_corpus.py`, `tests/parser_error_corpus.py`

**Interfaces:**
- Consumes: `IfParser::template(compiler, tokens)` (`smartif.rs:313`) and
  `IfParser::parse()` (`:345`); the `CompileFilter` impl over `Host::compile_filter` from Task 2.
- Produces: nothing later tasks call; this is M5's last tag.

`do_if` (`:933-1021`) drives `IfParser` and then walks its arena into
`IfNode(conditions_nodelists)`, where each element is
`(condition, nodelist)` (`:1005-1018`). The `elif`/`else`/`endif` loop
(`:995-1008`) and the smartif operator/lexer are Django's own code already
ported at M3; what M5 adds is the **real** `CompileFilter` — today
`smartif.rs:262-265` says "nothing calls `smartif` at M3, so the only M3
implementation is the test stand-in". That stand-in is now replaced by
`Host::compile_filter` (Task 2), which is also why a bad condition raises at
*parse* time, where Django raises it.

| message | source |
|---|---|
| `'Malformed template tag at line {}: "{}"'.format(token.lineno, token.contents)` | `:1013-1017` |

Every other failure is smartif's, raised through `ErrorClass::TemplateSyntax`
with Django's own strings (`smartif.rs` carries them).

Corpus:

```python
    "{% if a %}x{% endif %}",
    "{% if a %}x{% else %}y{% endif %}",
    "{% if a %}x{% elif b %}y{% endif %}",
    "{% if a %}x{% elif b %}y{% elif c %}z{% else %}w{% endif %}",
    "{% if a and b or not c %}x{% endif %}",
    "{% if a|lower == 'x' %}x{% endif %}",
    "{% if a > 1 and b < 2 %}x{% endif %}",
    "{% if not a %}x{% endif %}",
```

Errors: `("{% if a %}{% endif %}{% endif %}", 'Malformed template tag at line 1: "endif"')`
and the smartif rows already in `ERRORS` from M3 — this task adds only the
`do_if`-level row, because the smartif rows are already proven.

- [ ] **Step A: assert the condition is a host object, not a re-parse.** In
  `tests/test_ref_table.py`, the `{% if %}` branch's condition must be the
  object Python's `parser.compile_filter` returned:

```python
def test_if_conditions_are_the_hosts_filter_expressions():
    nodes, refs = ours_parse("{% if a|lower %}x{% endif %}")
    condition = nodes[0].conditions_nodelists[0][0]
    assert len(refs) == 1, "one condition, one host-built FilterExpression"
    assert condition is refs[0], "the condition must BE the host's object"
```

  One condition, so exactly one slot; identity, so a Rust-side re-parse that
  happens to stringify the same would still fail. Fill nothing in — the counts
  are derived from the template in the test.
- [ ] **Step B: run it against the stub** — red.
- [ ] **Step C: run the seven steps.**

```bash
git add crates/template-core/src/parser/tags/if.rs \
        crates/template-core/src/parser/smartif.rs \
        tests/parser_corpus.py tests/parser_error_corpus.py tests/test_ref_table.py
git commit -m "tags: if, and the real compile_filter behind its condition"
```
- [ ] **Step D: run the switch and confirm M5 is closed.**

Run: `./scripts/check.sh`
Expected: `test_no_m5_tag_is_still_a_stub` **PASS** — `builtin_tag_stubs()`
still names `block`, `load`, `partial`, `partialdef` (M6/M7/M13) and no longer
names any of M5's eleven. `Ran <N> tests … OK (skipped=6)`.

---

## Task 8: Close-out — the count, the rows, and the review record

**Files:**
- Modify: `docs/superpowers/plans/2026-10-09-m5-remaining-built-in-tags.md` (this file: tick the boxes, record the final count and every Step-1 mismatch found)
- Modify: `docs/superpowers/specs/*` (§10.3's M5 and M6 rows — §D1's scope change)
- Create: `.review/m5-seams.md`

- [ ] **Step 1: Re-measure the count.** Do not transcribe any number from this
  plan. Run `./scripts/check.sh` and record the actual
  `Ran <N> tests`, the corpus counts (`CASES`, `ERROR_CASES`), and `cargo test`.

- [ ] **Step 2: Record the Step-1 mismatches.** Every place a tag's real
  descriptor differed from §D is evidence the re-measure step is load-bearing —
  list them in this file's close-out, and if there were none, say that too and
  note that §D was written from the same source.

- [ ] **Step 3: Update the spec's `§10.3` rows.** M5's row becomes the eleven
  names (`M5_TAGS`, cited to `tests/test_tag_inventory.py`, not copied); M6's row
  gains `querystring`; the `test_url` file moves from M12's row to M11's, per the
  Scope section's resolution. Each row cites the test that derives it.

- [ ] **Step 4: Write `.review/m5-seams.md`** — the milestone's audit record,
  same shape as `.review/m4-seams.md`: one row per tag, the verdict, the source
  line, and the general rule at the top that **a one-time audit's verdict
  expires when the thing audited changes**. Cite tags by symbol, never by a
  `tags/*.rs` line number — the §D-editing in Task 4 already moves them.

- [ ] **Step 5: Commit and send to the reviewer.**

```bash
git add docs/superpowers .review/m5-seams.md
git commit -m "tags: M5 close-out — the count re-measured, the scope change recorded"
```

---
