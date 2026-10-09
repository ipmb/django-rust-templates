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
2. **A descriptor class Python cannot materialise — or cannot *construct* — is
   silent until a corpus row hits it.** `NODE_CLASSES` is an explicit allow-list
   (`python/django_rust_templates/_impl/parser.py:60-72`) and `materialise_node`
   raises `LookupError` on a miss (`:117-122`) — but only for a node the corpus
   actually builds. A tag can emit `"ForrNode"` and every test stays green until
   someone writes a `{% for %}` template. The class name is derivable from the
   Rust source, so the check can be static. **And a name in the list is not
   enough:** `IfChangedNode`'s `__init__` is variadic, so a correct class name
   still raises `TypeError` under `cls(**kwargs)` (§E) — which is why §E's
   factory has its own seen-to-fail step in Task 5 and why A4 checks keys
   against the Rust source rather than trusting the list. **Owned by Task 1
   (A4) and Task 5 (the factory).**
3. **`{% if %}` and `{% cycle %}` are the first real producers of a wire handle,
   and the render path cannot resolve one.** Today `Ref` is exercised only by
   hand-built descriptors in `tests/test_materialise.py:178-206`; no parse
   produces one, because no implemented tag needs a host-owned value.
   `_native.render` materialises with `resolve_ref=None`
   (`crates/template-py/src/lib.rs:1529-1531`), so a handle reaching it raises
   `LookupError` (`parser.py:343-348`). M5 introduces the producers *and* the
   table; getting the table's lifetime wrong (per-parse vs persistent) makes
   handles ambiguous in exactly the way `PyTarget`'s doc warns about
   (`lib.rs:996-1000`). **Owned by Task 2; pinned by the `{% cycle … as %}` +
   `{% cycle c %}` identity assertion, which is *seen to fail* first.**
4. **`{% cycle %}`'s named-node identity is parser state, it must survive a
   *reused* name, and it is a render-time invariant rather than a tree-shape
   one.** Django stores the *node object* in `parser._named_cycle_nodes[name]`
   (`defaulttags.py:693-696`) and `{% resetcycle %}` reads it back (`:1437`);
   two `{% cycle a b as c %}` occurrences must yield *one* node, the second
   returning the first's (`:669-671`). The consequence is measured, not
   assumed: `CycleNode.render` keys `context.render_context` **by the node
   object** (`:106-112`), so two distinct objects get separate
   `itertools.cycle` iterators and the second mention **restarts** the sequence
   — a divergence no tree comparison can see. `template-core`'s `Parser`
   already carries `named_cycle_nodes` and `last_cycle_node`
   (`parser/mod.rs:71-72`) and `resetcycle.rs` already reads them, so M5's
   `cycle.rs` must *write* them and the wire must carry the reference as
   `AttrValue::NodeSlot` (§B), not as two equal descriptors. **Owned by Task 6;
   pinned by a repeated-name corpus row and the identity assertion Task 2 adds,
   which the descriptor-level check cannot substitute for.**
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

**Keyed to `M5_TAGS` and not to the implemented set, which makes A3 red from Task
1 alongside the switch** — the plan's Step 7 therefore expects two reds, not one.
Keyed to the implemented set it would be empty until Task 3 and thus a comment for
exactly the stretch where the plan claims to be replacing comments with gates.
The `ifchanged` exemption is derived from the pinned tree
(`compile_function_mentions`), not asserted in prose: measured, exactly ten of the
eleven mention `TemplateSyntaxError` in their registered compile function.

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

**Why the sharing is load-bearing, measured.** `CycleNode.render` keys its
iterator on **the node object**:

```python
if self not in context.render_context:
    context.render_context[self] = itertools_cycle(self.cyclevars)
cycle_iter = context.render_context[self]
value = next(cycle_iter).resolve(context)
```

(`defaulttags.py:106-112`; `reset` does the same at `:123-127`.) Two
equal-but-distinct objects do not merely duplicate structure: they get
**separate `itertools.cycle` iterators**, so a `{% cycle c %}` that should
*continue* the sequence **restarts it**, and `{% resetcycle c %}` resets an
iterator the visible `{% cycle %}` never advances. The sharing is a render-time
invariant, not a tree-shape one — which is why a mechanism that only produces
equal descriptors is not enough, and why the plan must say this sentence where a
reader deciding on the mechanism will see it.

**The design: one table, owned by the shell, threaded through the parse call.**

* The table is a single `Vec` per parse, indexed by `usize`. **Two `AttrValue`
  variants index it, and they differ in who fills an entry and when** — which is
  the meaning that must not be conflated:

  * `AttrValue::Ref(NodeRef)` (`desc.rs`, existing) — filled **at parse time by
    the host**. `{% if %}`'s condition.
  * **`AttrValue::NodeSlot(usize)` — new, and M5's one wire addition** — filled
    **at materialisation time by the materialiser**. `{% resetcycle %}`'s target
    and a reused `{% cycle name %}` descriptor.

  `NodeSlot` follows `AttrValue::NodeListRef(usize)` (`desc.rs:131-148`), which
  M3 added for `IfNode.conditions_nodelists` for exactly this reason: the wire is
  a tree and the thing it must express is not, so the tree carries an index into
  a side collection resolved at materialisation. The node slot is that idiom one
  level up — into a per-parse table rather than a node's own children.
* **`AttrValue::Int` was the first draft and is wrong.** An `Int` carries its
  meaning out of band: the materialiser would have to know that *for `CycleNode`*
  this attribute is a cache key. This project has already made that call the
  other way twice — `SafeStr` and `NodeRef` each got their own wire class
  precisely so the decoder needs no per-class knowledge — and M2's wire-form
  review named "an `Int` that sometimes means one thing and sometimes another"
  as the hazard to design out. One variant, and the decoder stays generic.
* The `Host` trait gains **`alloc_slot(&self) -> usize`** (push a placeholder,
  return its index) and **`compile_filter(&self, token: &str) -> Result<NodeRef,
  Error>`** (allocate, then have Python build the real object and store it). The
  latter is the same shape as the existing `check_filter` callback
  (`crates/template-py/src/lib.rs:930-938`), which already calls back into Python
  during parse — the mechanism is proven, only its return widens.
* **`cycle` itself needs no host call.** Its slot is filled by the materialiser,
  so `template-core` only needs an index; `resetcycle.rs`'s existing
  `AttrValue::Ref` (`:34`, `:42`) becomes `AttrValue::NodeSlot`.
* `_native.parse` returns the table alongside the tree. Its current return is
  `(nodes, contains_nontext)`; it becomes `(nodes, contains_nontext, refs)`, and
  the Python harnesses pass `resolve_ref=refs.__getitem__`,
  `register_ref=refs.__setitem__`. This closes the `resolve_ref=None` hole in the
  render path (§Review Focus 3): `_native.render` becomes able to resolve a
  handle it built.
* **The table is per-parse and meaningless outside it** — the same lifetime rule
  `PyTarget.arena` states for the other kind of handle (`lib.rs:996-1000`).
  An index is only ever valid against the table the producing parse returned.

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
`Map`, `NodeListRef` — **plus exactly one new one, `NodeSlot(usize)`** (§B),
which is the only wire addition M5 makes. The `if` condition is a `Ref` and a
node's sub-nodelists are named or `NodeListRef` (§D-general); `NodeSlot` exists
only for the two cycle back-references. The per-tag shapes below are the pinned
source read against those variants; each is a claim, and each task's first step
re-measures it against `.django/` @ 6.0.9 before writing code.

**D-general — sub-nodelists and repeated names.** A named nodelist becomes a
`__init__` kwarg; an *empty-named* nodelist is positional-only and reachable
only through `AttrValue::NodeListRef(i)` into the node's own `nodelists`
(`parser.py:109-113`, `:130`; `desc.rs:131-148`). `token_kwargs` results are a
`Vec<(String, AttrValue)>` (`AttrValue::Map`), **not** a map: a repeated key
keeps the first position and the last value, and only `dict(pairs)` reproduces
it (`base.py:1196`; `desc.rs` doc).**D1 — `querystring` moves to M6 (evidence, not preference).** It is
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
(`:1687`) — the two legacy values are `None`, the modern data rides in
`extra_context`. **Measured correction (this is what Step 1 is for):** the
signature is `WithNode.__init__(self, var, name, nodelist, extra_context=None)`
(`:566-567`), and `materialise_node` constructs with `cls(**kwargs)`
(`parser.py:135`) — keyword-only. So the descriptor must emit kwargs **named
`var` and `name`**, each `AttrValue::None`; two unnamed positionals do not
exist on that path and the node would raise
`TypeError: missing 2 required positional arguments: 'var' and 'name'`. `name`
is not dead: `if name: self.extra_context[name] = var` (`:571-572`) is the
legacy `{% with a as b %}` form, which Django supports and the descriptor must
therefore be able to express. Compile calls
`token_kwargs(remaining_bits, parser, support_legacy=True)` (`:1676`), yielding
an `AttrValue::Map`. Two messages:
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
pushes the fresh node's **slot** into `named_cycle_nodes` and sets
`last_cycle_node` (`:693-700`); a repeated name returns the *existing* slot
(`:669-671`) and must **not** build a second descriptor — the descriptor it
emits carries `AttrValue::NodeSlot(slot)` and no other attribute, so the
materialiser returns the object built for the first occurrence (§B). Two
identical descriptors would pass every tree comparison and still restart the
sequence at render (§B's measured reason).

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

### §E — `NODE_CLASSES` entries become factories, because `ifchanged` is variadic

**This is the second defect the plan's own Step-1 re-measurement found, and it
is a mechanism, not a name.** `materialise_node` builds every node with
`cls(**kwargs)` (`parser.py:135`) — keyword-only. `IfChangedNode.__init__(self,
nodelist_true, nodelist_false, *varlist)` (`defaulttags.py:262`) has **no
keyword** for `varlist`, so no descriptor can build it through that path: it
raises `TypeError: __init__() missing 1 required positional argument` or, worse,
silently loses the values. A scan of all twelve M5 node classes found
`IfChangedNode` to be the **only** variadic one, so this is one adapter, not a
pattern.

**Decision: a `NODE_CLASSES` value is the callable that builds the node from its
kwarg set, not necessarily the class.** For every existing entry the class is
its own factory — a class is a callable taking keywords, so nothing changes, and
`NODE_CLASSES["SpyNode"] = SpyNode` (`test_materialise.py:140`) and
`set(NODE_CLASSES)` (`:484`) keep working unchanged. `IfChangedNode` gets an
adapter beside the dict:

```python
def _ifchanged_factory(nodelist_true, nodelist_false, _varlist=()):
    """`IfChangedNode`'s `__init__` is variadic, so it needs the splice.

    The kwarg is named `_varlist` because that is Django's own attribute name
    for it (`defaulttags.py:265`, `self._varlist = varlist`); the descriptor
    emits that name and this is the only place it is unpacked.
    """
    return IfChangedNode(nodelist_true, nodelist_false, *_varlist)
```

Rejected alternative: a reserved `"__args"` key in the descriptor vocabulary.
It would work, but it puts a construction detail into the descriptor language
and makes every reader of `desc.rs` learn a rule that exists for one node. The
adapter keeps it in the one file that knows about node construction.

`NODE_CLASSES`'s doc (`parser.py:55-72`) must say "factory" and state that the
class is the factory in every case but `IfChangedNode`; otherwise the next
person to add a variadic node writes a descriptor that cannot work. A4's static
check is unaffected — it compares **keys**.

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
- Leaves two pins that later tasks must move, both deliberately: the Rust literal
  in `registry.rs::every_entry_is_reachable_and_every_unimplemented_one_is_a_stub`
  (fifteen names → the four that survive M5) and `deferred` in
  `test_parser_parity.py` (`{"resetcycle"}` → `set()`). Task 7 moves the first and
  Task 6 the second, each in the commit that makes the old value false.

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
`builtin_tag_names` — **deriving it by calling each compile function**, not by
adding an `is_stub` field:

```rust
/// The registered names whose compile function is still a milestone stub, ascending.
pub fn builtin_tag_stubs() -> Vec<&'static str> {
    let probe = Token {
        token_type: TokenType::Block,
        contents: "x".to_string(),
        lineno: 1,
        position: None,
    };
    REGISTRY
        .iter()
        .filter(|(_, compile)| {
            let mut parser = Parser::new(Vec::new(), &NoHost);
            matches!(
                compile(&mut parser, &probe),
                Err(Error { kind: ErrorKind::Unimplemented(_), .. })
            )
        })
        .map(|(name, _)| *name)
        .collect()
}
```

**This replaces the draft's `is_stub` field, on a measurement.** The draft offered
a column in `Entry`, set at the fifteen stub sites — which is a hand-maintained
list wearing a struct. Every stub body is exactly `Err(unimplemented(name,
milestone))` and reads neither the parser nor the token, so a probe token tells
the two apart without anyone maintaining anything. Probed against the live table:
all fifteen stubs return `Unimplemented`, and the nine implemented tags return
`Ok`/`Syntax`/`Internal` and never `Unimplemented` — no panics, which matters
because `unimplemented` is called only from stub bodies, so `Unimplemented` *is*
the predicate.

The `Vec<&'static str>` and not the draft's `Vec<String>`: it matches
`builtin_tag_names`, and it copies nothing.

`ErrorKind`, `TokenType` and `NoHost` are now needed by production code in this
module, so the three imports at the top widen and the test module's now-redundant
`use crate::parser::error::ErrorKind;` goes (a glob plus an explicit import of the
same name is the `unused_imports` lint).

Step 1 also rewrites `every_entry_is_reachable_and_every_unimplemented_one_is_a_stub`
so its hardcoded fifteen names become a **pin** on the derivation —
`assert_eq!(builtin_tag_stubs(), stubs, …)` — and adds
`the_stub_derivation_is_not_a_copy_of_the_table`, which asserts stubs and
implementations partition `builtin_tag_names`. Both are seen to fail: mutating
`tags/widthratio.rs`'s body to return a `Syntax` error reddens the pin and names
`widthratio` as the disagreement.

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
`resetcycle` note):

```python
    def test_the_corpus_covers_every_implemented_tag_with_a_success_path(self):
        implemented = set(_native.builtin_tag_names()) - set(_native.builtin_tag_stubs())
        joined = "\n".join(TEMPLATES)
        # `\b` and not a bare prefix: `{% ifchanged %}` is not a `{% if %}` row.
        missing = {
            tag for tag in implemented
            if not re.search(r"\{%\s*" + re.escape(tag) + r"\b", joined)
        }
        deferred = {"resetcycle"}
        self.assertEqual(missing, deferred, f"corpus gaps that are not the declared "
                         f"exemption: {sorted(missing - deferred)}; declared gaps now "
                         f"covered: {sorted(deferred - missing)}")
```

**Two corrections to the draft, both found by running it.**

It asserts the exact set of *gaps* rather than looping `assertIn`, because a loop
can only report a tag it already knows to look for — so an exemption written as a
missing list entry is indistinguishable from an oversight, which was the original
defect. Naming the exemption makes it a pin instead.

And the exemption is real and the draft missed it: `resetcycle` **is** implemented
(`builtin_tag_names()` member, not a stub) but has no success row and cannot have
one yet, because every successful compile of it needs a preceding `{% cycle %}` —
`do_resetcycle` raises `"No cycles in template."` when `parser.last_cycle_node` is
unset (`defaulttags.py:1441-1443`), and `cycle` does not land until Task 6. So the
draft's derived loop would have gone red at Task 1 for a tag that is *finished*,
which is the sort of thing that gets a gate deleted rather than fixed. `deferred`
shrinks to `set()` in Task 6, in the same commit as the `{% cycle %}` corpus row.

The `\b` matters too: with a bare prefix, a `{% ifchanged %}` row would satisfy
`{% if` once `if` lands — a missing row reported as present, in the one direction
a coverage gate must never be wrong.

**Note on `test_the_corpus_covers_every_tag_with_a_success_path`'s name.** It is
renamed to `..._every_implemented_tag_...`, because "every tag" is now false of a
milestone that leaves four stubs. The old name is the vacuity: a test named for a
set it does not derive.

- [ ] **Step 5: Add the M5-done switch and the error-corpus coverage.**

New test in `test_tag_inventory.py`, in `RustRegistryTests` beside the other two
statements about the Rust registry — `M5_TAGS` and its only consumer read
together, and no cross-module import:

```python
    def test_no_m5_tag_is_still_a_stub(self):
        """M5's switch. Red until every M5 tag's body has landed."""
        still = set(_native.builtin_tag_stubs()) & M5_TAGS
        self.assertEqual(still, set(), f"M5 has not landed: {sorted(still)}")
```

And in `tests/test_parser_errors.py`, the error-side coverage. **This one is
keyed to `M5_TAGS` and not to the stub set, and it is therefore red at Task 1** —
the draft said "an analogous assertion" without saying which side of the
subtraction, and the two choices are not equivalent. Keyed to stubs it is empty
from Task 1 until Task 3 and so a comment at exactly the moment the plan is
closing vacuous gates; keyed to the plan it is red from Task 1 and turns green one
tag at a time, the same shape as the switch:

```python
    def test_the_error_corpus_covers_every_m5_tag_that_can_fail_to_compile(self):
        for tag in sorted(M5_TAGS - {"ifchanged"}):
            with self.subTest(tag=tag):
                covered = any(
                    re.search(r"\{%\s*" + re.escape(tag) + r"\b", source)
                    for source, _ in ERRORS
                )
                self.assertTrue(covered, f"{tag} can fail at compile time but no ERRORS row covers it")
```

The `ifchanged` exemption is *derived*, not asserted in prose, and getting that
derivation right took two measurements:

```python
def compile_function_mentions(tag, name):
    func = inspect.unwrap(defaulttags.register.tags[tag])
    module = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return any(
        (isinstance(n, ast.Name) and n.id == name)
        or (isinstance(n, ast.Attribute) and n.attr == name)
        for n in ast.walk(module)
    )
```

```python
    def test_the_m5_error_exemption_is_derived_from_djangos_source(self):
        raising = {t for t in M5_TAGS if compile_function_mentions(t, "TemplateSyntaxError")}
        self.assertEqual(M5_TAGS - raising, {"ifchanged"})
```

**The first attempt at this was wrong and the failure was informative.** It looked
the function up by a `do_<tag>` naming convention and found no top-level
`do_url` — because the convention does not hold: `cycle` (`defaulttags.py:612`)
and `url` (`:1515`) are top-level functions under their own names with no prefix.
A guess that had *not* crashed would have reported "raises nothing" for both and
quietly widened the exempt set from one tag to three, which is precisely the class
of error this project keeps paying for — a plausible convention standing in for a
measurement. Hence the registry lookup, and the AST rather than a substring
search so a name in a comment is not a raise.

Measured, it yields exactly the ten tags §D gives message literals for; `ifchanged`
is the only M5 tag whose compile function never raises `TemplateSyntaxError`
itself, every failure it can produce coming from the base parser M3 already pins.

- [ ] **Step 6: A4 — the static `NODE_CLASSES` check, in `test_materialise.py`.**

The draft's helper cut each file at its first `#[cfg(test)]`
(`body = path.read_text().split("#[cfg(test)]")[0]`). **That is wrong, and the
measurement is the reason:** the markers are interleaved with production code —
`tags/mod.rs` puts its stand-ins under `#[cfg(test)]` from `:207` onward while
real helper code sits at `:48-59` and beyond — so a prefix cut drops a region
silently, and a silently dropped region is a class name the check cannot see. The
replacement is a brace-matching scan:

```python
_CFG_TEST = "#[cfg(test)]"

def strip_cfg_test(source):
    lines = source.splitlines()
    kept: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() != _CFG_TEST:
            kept.append(lines[index])
            index += 1
            continue
        index += 1
        depth = 0
        saw_brace = False
        while index < len(lines):
            line = lines[index]
            index += 1
            depth += line.count("{") - line.count("}")
            saw_brace = saw_brace or "{" in line
            if saw_brace and depth <= 0:
                break
            if not saw_brace and line.rstrip().endswith(";"):
                break
    return "\n".join(kept)
```

Its correctness is asserted rather than assumed, and the two assertions bracket it
from both sides: `TEST_ONLY_CLASSES` (`StandinBlockNode`, `StandinFirstNode`,
`StandinRefNode`, `HostNode`) must be *absent* after the strip, which a no-op
stripper fails; and `test_every_node_class_is_emitted_somewhere` fails if it cut
real code. A stripper wrong in either direction reddens one of the two.

```python
TAG_MODULES = PROJECT_ROOT / "crates" / "template-core" / "src" / "parser" / "tags"
_CLASS = re.compile(r'NodeDesc::(?:new|with_nodelist)\s*\(\s*"([A-Za-z_][A-Za-z0-9_]*)"')
_TEXT = re.compile(r"NodeDesc::text\s*\(")

# `TextNode` by `desc.rs::NodeDesc::text`, `VariableNode` by `parser/mod.rs:186`.
CORE_EMITTED_CLASSES = {"TextNode", "VariableNode"}
TEST_ONLY_CLASSES = {"StandinBlockNode", "StandinFirstNode", "StandinRefNode", "HostNode"}


def tag_emitted_class_names():
    names = set()
    for path in sorted(TAG_MODULES.glob("*.rs")):
        body = strip_cfg_test(path.read_text(encoding="utf-8"))
        names.update(_CLASS.findall(body))
        if _TEXT.search(body):
            names.add("TextNode")
    return names
```

then:

```python
    def test_the_stripper_removes_only_test_code(self):
        self.assertEqual(
            {n for n in TEST_ONLY_CLASSES if n not in tag_emitted_class_names()},
            TEST_ONLY_CLASSES,
        )

    def test_every_tag_emitted_class_is_materialisable(self):
        missing = tag_emitted_class_names() - set(materialiser.NODE_CLASSES)
        self.assertEqual(missing, set(), f"NODE_CLASSES is missing {sorted(missing)}")

    def test_every_node_class_is_emitted_somewhere(self):
        dead = set(materialiser.NODE_CLASSES) - tag_emitted_class_names() - CORE_EMITTED_CLASSES
        self.assertEqual(dead, set(), f"unreachable NODE_CLASSES entries: {sorted(dead)}")
```

`\s*` spans newlines, which is not incidental: `tags/mod.rs:182` starts a
`NodeDesc::with_nodelist(` whose class-name literal is on the following line.

- [ ] **Step 7: Run the new guards, and confirm the *first* one fails for its
  intended reason — then run each mutation and restore with `cp`, never
  `git checkout` (§ the work is uncommitted).**

Run: `source scripts/env.sh && maturin develop && ./scripts/check.sh`
Expected: **two reds, not one** — `test_no_m5_tag_is_still_a_stub` naming all
eleven tags, and `test_the_error_corpus_covers_every_m5_tag_that_can_fail_to_compile`
naming ten (all but `ifchanged`). The draft said the switch was the only expected
failure; keying A3 to the plan rather than to the stub set makes it red too, and
that is the intended shape rather than a defect. Ten is the right number because
§D gives exactly ten of the eleven a compile-time message literal, and the derived
exemption test above is what establishes `ifchanged` as the eleventh.

`test_every_tag_emitted_class_is_materialisable` and
`test_the_corpus_covers_every_implemented_tag_with_a_success_path` are green (no
M5 body exists yet, so nothing is demanded of the success corpus).

Then, in turn, each mutation, run and restored:

```bash
cp python/django_rust_templates/_impl/parser.py /tmp/parser.py.bak
# delete the `"DebugNode": DebugNode,` line -> A4-reds, naming DebugNode
# restore
```

```bash
cp tests/parser_corpus.py /tmp/corpus.py.bak
# delete EVERY `{% csrf_token` row, not one -> coverage-reds, naming csrf_token
# restore
```

**Not "the `{% csrf_token %}` row".** The draft's mutation removed one row and
the test stayed green — correctly, because the gate is "at least one row mentions
the tag" and eight other rows mention `csrf_token`. Recorded because the mutation
not reddening is information: it says the gate's granularity is per-tag coverage,
not per-row presence, which is the claim its docstring makes and the reason its
failure message distinguishes "gaps that are not the declared exemption" from
"declared gaps now covered".

```bash
cp tests/test_tag_inventory.py /tmp/inv.py.bak
# drop "url" from the M5_TAGS literal -> derivation-equality reds, naming url
# restore
```

The `M5_TAGS` edit must be anchored past `NEEDS_FILTER_EXPRESSION`, which ends
with the same four names on one line; unanchored, `str.replace(…, 1)` hits that
set first and reddens `test_the_filter_expression_partition` instead — a mutation
that proves something about a different test.

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
- Modify: `crates/template-core/src/parser/desc.rs` (add `AttrValue::NodeSlot(usize)` and `NodeDesc::slot` — §B)
- Modify: `crates/template-core/src/parser/host.rs` (the `Host` trait gains `alloc_slot`)
- Modify: `crates/template-core/src/parser/mod.rs` (`last_cycle_node`/`named_cycle_nodes` become slot indices)
- Modify: `crates/template-core/src/parser/tags/resetcycle.rs` (the two arms emit `AttrValue::NodeSlot`)
- Modify: `crates/template-py/src/lib.rs` (`FilterCheckHost` gains the table; `parse` returns it; `render` threads it; `NodeSlot` pyclass)
- Modify: `python/django_rust_templates/_impl/parser.py` (`materialise`/`materialise_node` gain `register_ref`, the slot rule and the `NodeSlot` arm)
- Modify: `tests/test_parser_parity.py`, `tests/test_materialise.py` (the five-element descriptor)
- Test: `tests/test_ref_table.py` (new — the table's wire test, and the identity assertion that closes §Review Focus 3)

**Interfaces:**
- Produces: `AttrValue::NodeSlot(usize)`; `NodeDesc::slot: Option<usize>`;
  `Host::alloc_slot(&self) -> usize`;
  `_native.parse` → `(nodes, contains_nontext, refs)`;
  `materialise(nodelist_desc, origin=None, resolve_ref=None, register_ref=None, parser=None)`.
- Consumes: `AttrValue::Ref(NodeRef)` (exists), `AttrValue::NodeListRef` (exists, the precedent).

**Design, and the one part most likely to need review.** Two tags need a value
that is built outside `template-core`, at two different *times*, and one table
serves both. The variants differ in who fills an entry and when — §B is the full
argument, this is the summary:

* `{% if %}` — `AttrValue::Ref(NodeRef)`, filled **at parse time by the host**.
  Django builds the `FilterExpression` at parse time and raises on a bad one
  (`TemplateIfParser.__init__` → `create_var` → `compile_filter`), so
  `compile_filter` fills its entry during the parse.
* `{% cycle %}` — `AttrValue::NodeSlot(usize)`, filled **at materialisation time
  by the materialiser**. The identity that must survive is a materialised Python
  object: `{% cycle a b as c %}` returns the node **and** stores that same object
  in `parser._named_cycle_nodes` (`defaulttags.py:693-696`), so a later
  `{% cycle c %}` returns the same object (`:669-671`) and `{% resetcycle c %}`
  resets it (`:1437`). So the descriptor carries the slot index and the
  materialiser fills the entry on first construction:

  - a `CycleNode` descriptor carries `AttrValue::NodeSlot(k)`;
  - `materialise_node`, on decoding `NodeSlot(k)`, returns `refs[k]` if it is
    already filled, else builds the node, stores it at `refs[k]`, and returns it;

**What is and is not testable, measured — the first draft of this plan got this
wrong and overstated it.** Two links, and both are testable:

* **The descriptor carries the slot, visibly.** `_native.parse` returns each
  node as `(class_name, attrs, nodelists, token)` (`test_parser_parity.py:504-529`
  reads exactly that), so a test can assert that two `{% cycle %}` occurrences
  carry the **same** slot value. Note it is the *wire* descriptor that shows it,
  not `describe()`: `describe` walks `vars()` of a **materialised** node
  (`:131-147`), and the slot cannot survive into one — `CycleNode.__init__` takes
  no such parameter, so `cls(**kwargs)` would raise. Popping it before
  construction is required, not incidental.
* **The materialiser honoured the slot.** `tests/test_materialise.py` can assert
  `nodes[0] is nodes[1]` **today** — it is the direct statement of the property,
  Python-side, and it does not wait for Task 6 or for a corpus row.

So the untestable part is narrower than "the mechanism": it is only *whether the
two objects a render would iterate are the same object*, and that is exactly what
the identity assertion covers. This is why Task 2 adds it rather than deferring
it — a claim that something is untestable is the kind that gets quoted later, and
it should be as small as the evidence makes it.
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
threads a table yet. This is the "seen to fail" for §Review Focus 3.

- [ ] **Step 3: Add the `NodeSlot` variant, the two `Host` methods, and the shell
  table.**

`crates/template-core/src/parser/desc.rs` — beside `NodeListRef` (`:131-148`),
with a doc that says which table and who fills it:

```rust
    /// An index into the **per-parse node-slot table**, filled at
    /// materialisation time rather than at parse time.
    ///
    /// `NodeListRef` is the same idiom one level down: the wire is a tree and
    /// the thing being expressed is not, so the tree carries an index that is
    /// resolved once the side collection exists. A `Ref` indexes the same table
    /// but the *host* fills it during the parse; this one the materialiser fills
    /// as it builds. `{% cycle %}` is the only producer, twice over.
    NodeSlot(usize),
```

`crates/template-core/src/parser/host.rs`:

```rust
    /// Allocate an empty entry in the shell's per-parse reference table and
    /// return its index. The table is filled at parse time for `Ref` and at
    /// materialisation time for `NodeSlot`; this only reserves the index.
    fn alloc_slot(&self) -> usize;
    /// `Parser.compile_filter(token)` — build the host's `FilterExpression` and
    /// return its handle. Called at *parse* time, so a bad filter raises here,
    /// which is where Django raises it.
    fn compile_filter(&self, token: &str) -> Result<NodeRef, Error>;
```

`crates/template-py/src/lib.rs`: `FilterCheckHost` (and the `render` host) gains
`refs: RefCell<Vec<Py<PyAny>>>` with `alloc_slot` pushing a `None`-placeholder
entry, `alloc_ref` being `alloc_slot` for the `Ref` case, and `compile_filter`
calling Python's `parser.compile_filter(token)` (the existing `check_filter`
callback's sibling) and storing the result into the reserved slot.
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
3. `materialise_node` gains a `NodeSlot` arm in the attribute loop (`:131-133`),
   beside where `token` is attached. It is **not** a kwarg — `CycleNode.__init__`
   takes no such parameter — so it is handled where the attribute value is
   decoded, not passed on:

```python
    node = None
    for name, value in attrs:
        if isinstance(value, _native.NodeSlot):
            existing = resolve_ref(value.index) if resolve_ref else None
            if existing is not None:
                return existing          # {% cycle name %} — the same object
            node = None                  # first construction: build below, fill after
            _pending_slot = value.index
            continue
        kwargs[name] = _decode_value(value, resolve_ref, parser, child_lists)

    node = cls(**kwargs)
    if _pending_slot is not None:
        register_ref(_pending_slot, node)
    return node
```

   with the rest of `materialise_node` unchanged for every other node. Written
   this way the slot is filled *after* the node exists, which is what makes the
   second occurrence return the first's object rather than a second one; and the
   slot never reaches `cls(**kwargs)`, which would raise.

- [ ] **Step 4b: Add the identity assertion in `tests/test_ref_table.py`, beside
  `ours_parse`, so it runs the moment the table exists.** This is the property
  stated directly, and it is why "a divergence with no test" is too strong a
  claim:

```python
def test_a_reused_named_cycle_materialises_one_object():
    """`{% cycle a b as c %}{% cycle c %}` — one object, two nodelist entries.

    Identity is a render-time invariant, not a tree-shape one: `CycleNode.render`
    keys `context.render_context` by the node object (`defaulttags.py:106-112`),
    so two equal-but-distinct nodes get separate `itertools.cycle` iterators and
    the second `{% cycle c %}` restarts the sequence instead of continuing.
    """
    nodes, refs = ours_parse("{% cycle 'a' 'b' as c %}{% cycle c %}")
    assert nodes[0] is nodes[1]
    assert refs[0] is nodes[0], "the slot holds the object it built"
```

  It lives here rather than in `tests/test_materialise.py` only because
  `test_materialise.py` has no `ours_parse`; the file is not what makes it
  runnable. What makes it runnable is that it needs no tag code — Task 6's
  `cycle.rs` writes the descriptor, but the *table* and this assertion are
  Task 2's, so the link is closed now instead of declared untestable until
  Task 6.

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
git add crates/template-core/src/parser/desc.rs \
        crates/template-core/src/parser/host.rs \
        crates/template-core/src/parser/mod.rs \
        crates/template-core/src/parser/filter_expr.rs \
        crates/template-core/src/parser/tags/mod.rs \
        crates/template-core/src/parser/tags/resetcycle.rs \
        crates/template-py/src/lib.rs \
        python/django_rust_templates/_impl/parser.py tests/test_ref_table.py \
        tests/test_parser_parity.py tests/test_materialise.py \
        docs/superpowers/plans/2026-10-09-m5-remaining-built-in-tags.md
git commit -m "tags: the per-parse table and NodeSlot, so a cycle's second mention is the same node"
```

**Seven corrections to the draft, every one of them found by running it.**

*1. Step 1 and Step 2's `{% cycle %}` test cannot run, and the red is not the one
the draft predicted.* The plan has the identity assertion reached through
`ours_parse("{% cycle … %}")`. It cannot be: `cycle.rs` is still
`Err(unimplemented(...))` until Task 6. Measured, the failure is
`ValueError: not enough values to unpack (expected 3, got 2)` — the *old*
two-element parse result — before it is anything about a slot, and once the wire
grows it is `IndexError` / `AssertionError: 4 != 5`. The draft's predicted
`LookupError: … needs a resolve_ref callback` is not reachable from a template
at all. So `tests/test_ref_table.py` drives the table with **hand-built
descriptors** that are exactly the ones `cycle.rs` must write, and states that
limitation in its module docstring. What Task 6 owes is therefore narrow and
written down: write those descriptors and add the corpus row.

*2. `CycleNode` is patched into `NODE_CLASSES` in the test's `setUp`, not
registered.* Registering it for real would redden `tests/test_materialise.py`'s
static gate `test_every_node_class_is_emitted_somewhere` — a registered class no
`class_name` in the corpus ever emits is exactly the dead-registration that gate
exists to catch, and it stays dead until `cycle.rs` names it. `setUp` inserts it
and `addCleanup` removes it, so the patch cannot leak; Task 6 registers it for
real in the same commit as the corpus row.

*3. The materialiser rule the draft wrote is wrong, and the fix is a descriptor
field.* The draft says: "`materialise_node`, on decoding `NodeSlot(k)`, returns
`refs[k]` if it is already filled, else builds the node". That is one rule for
two different things, and it breaks `{% resetcycle %}`: `ResetCycleNode`'s
`node` attribute is *also* a `NodeSlot(k)`, so decoding it would hand back the
`CycleNode` as the value of `ResetCycleNode.node` **and** return it in place of
the `ResetCycleNode`. The two cases are not the same question:

* **"this node IS the object at slot k"** — a property of the node, so it is a
  field on the descriptor: `NodeDesc.slot: Option<usize>`, carried on the wire as
  the descriptor's **fifth element** (`None`, or a `_native.NodeSlot`). Only
  `{% cycle %}` produces it, and only for a *reused* named cycle.
* **"this attribute's value IS the object at slot k"** — a property of the value,
  so it is an `AttrValue` variant: `AttrValue::NodeSlot(usize)`
  (`ResetCycleNode.node` is its only producer).

The draft's reserved-attribute-name sentinel is dropped for the same reason: it
would be an entry in a table of constructor kwargs that `CycleNode.__init__`
(`defaulttags.py:97`) does not accept, so `cls(**kwargs)` would raise, and on the
reuse form there is no attribute list to put it in at all.

*4. The draft missed the token re-attachment.* Django's `extend_nodelist` does
`node.token = token` at **every** append (`base.py:598`), not only on
construction, so the one shared object ends up carrying the *last* occurrence's
token and **both** positions read it. Measured on the pinned tree:

    [n.token.contents for n in nodelist]  →  ['cycle c', 'cycle c', 'resetcycle c']

A "return the existing object and stop" shortcut passes without this, so it is
pinned separately: `test_the_shared_object_carries_the_last_occurrences_token`.

*5. `Host::compile_filter` is deferred to Task 7.* The draft declares it as
Task 2's other product. Nothing calls it until `{% if %}`, so shipping it here
would be an unexercised seam — the shape this project keeps removing. Task 2
adds `alloc_slot` alone; `compile_filter` is declared and consumed in Task 7.

*6. `NoHost::alloc_slot` returns `0`.* `NoHost` must stay free of interior
mutability — no `Cell` — because `Parser::new(tokens, &NoHost)` const-promotes
`&NoHost` to `'static`. `0` is a lie about a counter and an honest statement
about the null host: nothing built under it is ever materialised. The consequence
for Task 6 is written down there: a Rust test that needs two distinct slots needs
a *counting* test host, which is a new `#[cfg(test)]` host beside `TestHost` in
`tags/mod.rs`.

*7. Every reader of a descriptor changed shape with it, and the Files list did
not name them.* A node descriptor is now five elements, so
`materialise_node`'s unpack, `describe`'s walker, `test_materialise.py`'s
`collect_class_names` and `test_parser_parity.py`'s descriptor assertions all
move in the same commit — the two files the draft's Files list did not name are
now in this task's, along with `resetcycle.rs`, whose two `AttrValue::Ref` arms
become `AttrValue::NodeSlot`. `tests/test_ref_table.py`'s `materialise` helper
passes the callbacks explicitly, but `_impl/parser.py` also derives them from the
table's third element, which is what keeps every existing
`materialise(_native.parse(s), …)` call site source-compatible; the two in
`VariableNodeTests` that passed `parser()` positionally become keyword arguments.

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

Expected: **not a full PASS.** A3's coverage gate
(`test_the_error_corpus_covers_every_m5_tag_that_can_fail_to_compile`) is keyed
to `M5_TAGS` and is red from Task 1 until Task 7 (§A), so no task in 3-6 can
reach `OK (skipped=6)`. The deliverable this step checks is narrower and exact:
the *only* reds are the two declared Task-1 reds — `test_no_m5_tag_is_still_a_stub`
and the A3 coverage gate — and this group's tags appear in **neither** list. A
full PASS is Task 7's, and only Task 7's.

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
- Modify (added by measurement, see this task's corrections): the shared seams
  the five tags needed — `crates/template-core/src/pyunicode.rs` (`split_once`),
  `crates/template-core/src/parser/{error.rs,host.rs,mod.rs,registry.rs}`,
  `crates/template-py/src/lib.rs`,
  `python/django_rust_templates/_impl/parser.py` (the five node classes),
  `tests/test_parser_errors.py`, `tests/test_parser_parity.py`.
  (`tags/mod.rs`'s `py_repr_str` is the ASCII default this task reads but does
  not change; it landed with Task 1's gate.)

**Interfaces:**
- Consumes: `NodeDesc::new`/`.with_nodelist`/`.attr`/`.nodelist`
  (`desc.rs`), `AttrValue::{Str, Bool, FilterExpr, List, Map, None}`,
  `Error::TemplateSyntax`; and, as this task adds them, `Parser::compile_filter`,
  `Parser::py_repr`, `Host::repr`, `ErrorKind::ValueError`, `pyunicode::split_once`.
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
    "{% with a as b %}{{ b }}{% endwith %}",        # legacy form: name is not dead (§D4)
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

**Three corrections to the draft, every one of them found by running it.**

*1. `{% filter %}` with no argument is a bare `ValueError`, a shape the draft
never anticipated.* `_, rest = token.contents.split(None, 1)` (`:744`) has no
length check, so `{% filter %}` — whose contents hold no separating whitespace
run — raises `ValueError: not enough values to unpack (expected 2, got 1)` out of
Django's parser, with the token still attached. The draft listed only the
`escape`/`safe` refusal. This is reproduced as a new
`ErrorKind::ValueError(String)` rather than converted to `Syntax` on
`UnterminatedMarker`'s precedent: a `Syntax` carrying the same text passes every
*message* comparison and so would hide the divergent *class*, which the shell
turns into `PyValueError` vs `PySyntaxError`. Measured, `{% filter %}` and
`{% filter  %}` are the two reachable spellings.

*2. The widthratio five-bit and six-bit cases were the wrong way round.* The
draft's Step-3 note says `{% widthratio a b c d e %}` is the "at least three
arguments" case. Measured on the pinned tree it is the opposite: **five** bits
(`{% widthratio a b c d %}`) falls to the `else` and reports "takes at least
three arguments", while **six** bits (`{% widthratio a b c d e %}`) enters the
`as` branch, finds `bits[4] == "d"`, and reports `Invalid syntax … Expecting 'as'
keyword` (`:1639-1643`). Counting the tag name is the whole difference, and the
unit test `the_arity_branches_are_measured_not_derived` pins both.

*3. `{% with %}x{% endwith %}` is an error, not a corpus row.* The draft's
success list included it. Measured, `token_kwargs` removes nothing, so the
emptiness check fires first and it reports `'with' expected at least one variable
assignment`. It moved to `ERRORS`; `{% with %}{% endwith %}` and `{% with a
%}{% endwith %}` are its two reachable spellings.

**Shared seams this task had to add, none of them named in the draft's Files
list.** The five tags needed four pieces of infrastructure that did not exist:
`Parser::compile_filter` (a `&str` → `AttrValue::FilterExpr`, borrowed immutably
so the tags can compile operands without holding `&mut Parser`), `Parser::py_repr`
plus `Host::repr` (Django interpolates `%r` of user text in `{% with %}` and
`{% lorem %}`; the default is the ASCII-only Rust repr, the shell overrides it
with Python's `str.__repr__`), `ErrorKind::ValueError` (above), and
`pyunicode::split_once` (Python's `str.split(None, 1)`, which preserves the
remainder's interior whitespace). `{% with %}`'s `extra_context` also forced
`describe()` in `tests/test_parser_parity.py` to grow a `dict` arm: it is the
first dict-valued node attribute, and a live dict of `FilterExpression`s has no
`__eq__`, so two structurally identical parses compared unequal.

---

## Task 4: The two that compile a derived string — `regroup`, `url`

**Files:**
- Modify: `crates/template-core/src/parser/tags/{regroup,url}.rs`
- Modify: `crates/template-core/src/parser/kwargs.rs` (this task did need a
  Rust-visible accessor: `match_kwarg`, see the corrections below)
- Modify: `python/django_rust_templates/_impl/parser.py` (`RegroupNode` and
  `URLNode` into `NODE_CLASSES` and its import list — the draft's file list
  omitted it)
- Modify: `tests/parser_corpus.py`, `tests/parser_error_corpus.py`

**Interfaces:**
- Consumes: `kwarg_re` (`kwargs.rs:41`, already `pub`), `AttrValue::{Str, FilterExpr, List, Map, None}`.
- Produces: `kwargs::match_kwarg(bit) -> Option<(Option<&str>, &str)>`, a new
  public helper in `kwargs.rs`.

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
and ~~`("{% url 'view' k=v nope= %}", "Malformed arguments to url tag")`~~ —
corrected below, both the expectation and the belief that the Malformed branch is
reachable.

- [ ] Run the seven steps for both. The two share a task because both compile a
  string that is *not* a substring of the token — the property Step 1 exists to
  check.

```bash
git add crates/template-core/src/parser/tags/regroup.rs \
        crates/template-core/src/parser/tags/url.rs \
        tests/parser_corpus.py tests/parser_error_corpus.py
git commit -m "tags: regroup and url — both compile a derived string, not a token substring"
```

**Two corrections to the draft, both found by measuring before writing code.**

*1. `{% url %}`'s "Malformed arguments to url tag" branch is unreachable, and the
draft's error row is wrong.* `kwarg_re`'s value group is `(.+)`, and Python's `.`
does not cross `\n` without `DOTALL`, so `kwarg_re.match(bit)` returns `None` only
for a bit that is empty or starts with `\n`. Every bit `token.split_contents`
yields starts with a quote (`smart_split`'s quoted alternative) or a
non-whitespace character (its `\S+` alternative), so **no token reaches the
branch** — Django's own "Malformed" raise is dead code in the pinned tree. The
draft's row `("{% url 'view' k=v nope= %}", "Malformed arguments to url tag")` is
therefore wrong twice over: `kwarg_re.match("nope=")` keys nothing and takes
`"nope="` as the *value*, so `compile_filter` raises
`Could not parse the remainder: '=' from 'nope='` instead. The corrected row
carries that message; the branch is still ported (it is in Django's source and
its message is part of the tag's surface) and `tags/url.rs`'s unit test drives
`classify_bits` with a `\n`-leading bit — the only way to exercise it.

*2. The url pattern-name message keeps its literal quotes.* `"'%s' takes at least
one argument, a URL pattern name." % bits[0]` (`:1562-1564`) has the quotes **in
the format string**, so the rendered message is `'url' takes …` — my first
implementation used bare `{}` and produced `url takes …`, which every
message-comparison row then rejected. `%s` of the command name adds no escaping
of its own.

**One interface added by measurement.** `{% url %}` is the tree's second
`kwarg_re` consumer and needs the per-bit classification Django gets from
`match.groups()`, so `kwargs.rs` gained a public `match_kwarg(bit) ->
Option<(Option<&str>, &str)>` beside the existing `token_kwargs`. It is the third
and last consumer of `kwarg_re` (the design spec's count), so nothing further is
expected from it.

---

## Task 5: The two with a block body — `ifchanged`, `for`

**Files:**
- Modify: `crates/template-core/src/parser/tags/{ifchanged,for}.rs`
- Modify: `crates/template-core/src/parser/registry.rs` (the stub pin — added by
  measurement, see this task's corrections)
- Modify: `python/django_rust_templates/_impl/parser.py` (the `IfChangedNode` factory and the `NODE_CLASSES` doc — §E)
- Modify: `tests/parser_corpus.py`, `tests/parser_error_corpus.py`, `tests/test_materialise.py`
- Modify (added by measurement): `crates/template-core/src/parser/mod.rs` and
  `tests/test_parser_parity.py` — two tests that used `{% for %}` as their
  example of an unimplemented built-in

**Interfaces:**
- Consumes: `Parser::parse(&["else", "endifchanged"])` / `parse(&["empty",
  "endfor"])`, `Parser::next_token` (`parser/mod.rs:110`),
  `Parser::delete_first_token` (`:126`) — **measured, they exist** — plus
  `AttrValue::{Str, Bool, FilterExpr, List}` and the §E factory indirection.

**`ifchanged`** — `IfChangedNode(nodelist_true, nodelist_false, *varlist)`
(`:1024-1073`). **Argument order is true, false, then the values.** Two *named*
nodelists; without an `else`, `nodelist_false` is an **empty `NodeList()`**
(`:1057-1058`), not absent — a descriptor that omits it materialises a node
whose `nodelist_false` is missing and `describe` differs. `varlist` is an
`AttrValue::List` of `FilterExpr` over `bits[1:]` (`:1059`), emitted under the
kwarg name **`_varlist`** and spliced by the `NODE_CLASSES` adapter §E
describes. This is the only M5 tag whose node cannot be built by `cls(**kwargs)`
directly, and adding `"IfChangedNode"` to `NODE_CLASSES` *without* §E's adapter
is the failure this task exists to prevent. No message of its own; the two block
tags raise through the parser.

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

- [ ] **Step A: add the `IfChangedNode` factory *first*, and see it fail on its
  own.** In `tests/test_materialise.py`, a test that a descriptor with
  `_varlist` of two `FilterExpr`s builds an `IfChangedNode` whose `_varlist` is
  a 2-tuple. Run it *before* the factory exists: expected **FAIL**,
  `TypeError: __init__() takes 3 positional arguments but 4 were given` — which
  is the proof that `cls(**kwargs)` cannot express this node (§E). Then add the
  factory and the `NODE_CLASSES` entry; run again, expected PASS.
- [ ] **Step B: then run the seven steps** for `ifchanged` and `for`.

**Four corrections to the draft, every one of them found by running it.**

*1. Step A's predicted `TypeError` wording is interpreter-version-dependent.* The
plan predicts `TypeError: __init__() takes 3 positional arguments but 4 were
given`. Measured on this machine's Python 3.14 with `"IfChangedNode"` registered
as the *bare class*: `TypeError: IfChangedNode.__init__() got an unexpected
keyword argument '_varlist'`. The **class** of failure is what the step claims
and it holds — `cls(**kwargs)` cannot express a variadic `__init__` — but the
wording is CPython's, so the step should assert the failure happens, not its
text. (The first red seen was neither: with `IfChangedNode` absent from
`NODE_CLASSES` entirely, the materialiser raised `LookupError` from
`materialise_node`'s unknown-class guard, which is a *different* proof and not
the one this step is about. The plan's Step A ordering — register the factory,
then run — is what isolates the right failure.)

*2. The two-`else` row needs Django's `parse_until` suffix, and the message is the
**parser's**, not the tag's.* The draft gives
`"Invalid block tag on line 1: 'else', expected 'endifchanged'."` Measured on the
pinned tree, the real message is
`Invalid block tag on line 1: 'else', expected 'endifchanged'. Did you forget to
register or load this tag?` — `invalid_block_tag` appends the suffix whenever
`parse_until` is non-empty, which it always is here. Nothing in `ifchanged.rs`
produces this text: it is `Parser::parse(&["endifchanged"])` refusing the second
`else`, which is why the plan's own coverage test exempts `ifchanged` (Task 1's
derivation). The row is kept because it pins that the tag *does not* accept a
second `else` — seen to fail by changing the false branch's terminator to
`&["else", "endifchanged"]`, which flips both the message and the token.

*3. `for`'s checks are ordered arity → `in` → loop variables, and `{% for a b c %}`
is the discriminator.* The draft's error example for the *format* message is
`{% for x of y %}`, which does work, but the interesting case is the ordering the
plan does not state: `{% for a b c %}` fails the **format** check (because
`bits[-2]` is `b`) even though `a b` is also an invalid loop variable, and
`{% for a|b in %}` fails the **arity** check even though both the loop variable
and the sequence are bad. Both are in the corpus now.

*4. `{% for a b in y %}` is the invalid-argument case, not a two-variable loop.*
The join-then-split means no comma produces the **single** loop variable `"a b"`,
which the space then rejects; the plan's prose says the `invalid_chars` check
"rejects a filter separator or a quote inside a loop variable" and omits the space
case, which is the one a naive `split_whitespace` port would get wrong.

**One file the draft did not list, and two tests it did not name.** The registry's
stub pin (`crates/template-core/src/parser/registry.rs`) holds a literal that must
shrink to `["cycle", "if", "load", "partial", "partialdef", "querystring"]` in
this commit, exactly as Tasks 3 and 4 shrank it. Two tests named `{% for %}` as
their *example* of an unimplemented built-in —
`parser::tests::an_unimplemented_builtin_says_so_rather_than_denying_the_tag_exists`
and `tests/test_parser_parity.py::test_an_unimplemented_tag_is_its_own_kind_at_the_ffi`
— and both moved to `{% if %}`, the last still-stubbed M5 tag but `cycle`. Any
tag that has landed breaks these; the plan's "no other file changes" framing hid
that, and a task landing the *last* tag should expect to rewrite them again.


```bash
git add crates/template-core/src/parser/tags/ifchanged.rs \
        crates/template-core/src/parser/tags/for.rs \
        python/django_rust_templates/_impl/parser.py \
        tests/parser_corpus.py tests/parser_error_corpus.py tests/test_materialise.py
git commit -m "tags: ifchanged and for, and the factory NODE_CLASSES needed for a variadic node"
```

---

## Task 6: `cycle` and the identity it must keep — plus `resetcycle`'s messages

**Files:**
- Modify: `crates/template-core/src/parser/tags/cycle.rs`
- Modify: `crates/template-core/src/parser/tags/mod.rs` (a **counting** test host for the `#[cfg(test)]` cycle tests — `Host::alloc_slot` must hand out two distinct indices for two definitions, and `TestHost` (`:445`), like `NoHost`, returns `0` for everything)
- Modify: `python/django_rust_templates/_impl/parser.py` (`NODE_CLASSES` gains `"CycleNode"` for real — Task 2's `tests/test_ref_table.py` patches it in `setUp` and this task removes the need)
- Modify: `crates/template-core/src/parser/tags/resetcycle.rs` — its three messages **and** its wire form: `AttrValue::Ref` (`:34`, `:42`) becomes `AttrValue::NodeSlot`, because the thing it points at is a node built at materialisation, not a host object. Its own `#[cfg(test)]` fixtures assert `AttrValue::Ref` (`:90`, `:99`) and go with it, as does the comment at `:83` that says the `Ref` "is what M5 will need"
- Modify: `crates/template-core/src/parser/mod.rs` — `last_cycle_node: Option<NodeRef>` and `named_cycle_nodes: HashMap<String, NodeRef>` (`:71-72`) become slot indices, and their doc comment changes with them: it currently says "Both hold host-owned `CycleNode`s, so they are handles here. M2 only ever reads them and always finds them empty" — the first clause is the sentence this task falsifies
- Modify: `tests/parser_corpus.py`, `tests/parser_error_corpus.py`, `tests/test_ref_table.py` (drop the `setUp` patch, drop `"resetcycle"` from the error-corpus `deferred` set, and add the `{% cycle %}` success rows)

**Interfaces:**
- Consumes: `parser.named_cycle_nodes` / `parser.last_cycle_node`
  (`resetcycle.rs:34`, `:42` already read them), `AttrValue::{Str, Bool, FilterExpr, List, NodeSlot}`, `NodeDesc::slot`, and `Host::alloc_slot` from Task 2.
- Produces: the `NodeDesc::slot` / `AttrValue::NodeSlot` wire forms every named-`CycleNode` descriptor carries — the exact descriptors `tests/test_ref_table.py` already materialises from hand.

**`cycle`** — `CycleNode(cyclevars, variable_name=None, silent=False)`
(`:612-702`). Five parse shapes, and the shapes are the design:

1. `len(args) < 2` → refuse (`:657-658`).
2. `len(args) == 2` → **a name reference**: return the *existing* node
   (`:660-671`), or refuse. The descriptor for this form is the *reuse*
   descriptor — it carries the `AttrValue::NodeSlot` of the node the name was
   bound to and Task 2's materialiser returns the already-built object. It must
   **not** build a second descriptor with the same values; two equal `CycleNode`s
   pass a tree comparison and get separate `itertools.cycle` iterators at render
   (`defaulttags.py:106-112`), so the second mention restarts the sequence
   (§Review Focus 4).
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

This is also the step that **empties `deferred` in
`test_parser_parity.py`** — `{"resetcycle"}` → `set()`, in this same commit. The
corpus rows above are what make the old value false: `{% cycle 'a' 'b' as c
%}{% resetcycle %}` is the first template in which `resetcycle` has a success
path at all, because every other one raises `"No cycles in template."`. Task 1
declared the pin; this is where it moves, and leaving it would keep a finished
tag reported as an unfinished one.

**Four corrections to the draft, every one of them found by running it.**

*1. `resetcycle.rs` was already done — by Task 2, not by this one.* The draft's
Files list has this task changing its three messages **and** its wire form from
`AttrValue::Ref` to `AttrValue::NodeSlot`, and rewriting `parser/mod.rs`'s
`last_cycle_node`/`named_cycle_nodes` from `Option<NodeRef>` to slot indices.
All of that landed in `56df62a` ("the per-parse table and NodeSlot"), together
with `Parser::alloc_slot`. What is left here is the corpus rows — which are new,
and which is why the three messages were still broken-and-restored in this task
to see each row fail once.

*2. The `'Only silent'` error row in the draft is wrong, and wrong in the
interesting direction.* The draft has
`('{% cycle "a" "b" c as nope %}', "Only 'silent' flag is allowed after cycle's
name, not 'nope'.")`. Measured on the pinned tree, that template **succeeds**:
`args[-3]` is `c`, not `as`, so `args[-2] == "as"` selects the *non-silent* `as`
form, and it compiles a `CycleNode` named `nope` with values `['"a"', '"b"', 'c']`.
The branch needs `args[-3] == "as"` **and** `args[-1] != "silent"`, which is
`{% cycle a b as c d %}` → `… not 'd'.`. Both cases are now corpus rows, one
error and one success.

*3. `len(args) > 4` is the guard that makes two shapes surprising, and both are
pinned.* `{% cycle a as b %}` has four bits, so it never enters the `as` branches
at all: it is an anonymous three-value cycle whose middle value is the literal
`as`. And `{% cycle a b as silent %}` has `args[-3] == "b"`, so it is the
*non-silent* `as` form named `silent` — not the silent flag. Both measured; both
are success rows, because the plan's prose ("`as` consumes two bits") is true but
does not by itself tell you which of these is which.

*4. Restoring a broken literal is not enough — the extension must be rebuilt
before the next run.* The break-each-message probe restores the source after
each case; the run after the last case therefore executed a stale
`maturin develop` build and `./scripts/check.sh` reported
`'No cycles in TEMPLATE.' != 'No cycles in template.'` on `{% resetcycle %}`,
which was neither a real red nor one of the declared Task-1 reds. The probe now
belongs with the rebuild **after** the restore, and a red whose message matches
one of the mutations just applied is a stale build until proven otherwise.

```bash
git add crates/template-core/src/parser/tags/cycle.rs \
        crates/template-core/src/parser/tags/mod.rs \
        crates/template-core/src/parser/registry.rs \
        tests/parser_corpus.py tests/parser_error_corpus.py tests/test_ref_table.py \
        tests/test_parser_parity.py
git commit -m "tags: cycle's five shapes, and the one object they must all share"
```

---

## Task 7: `if` — the tag whose condition is a host object

**Files:**
- Modify: `crates/template-core/src/parser/tags/if.rs`
- Modify: `crates/template-core/src/parser/smartif.rs` (wire the real `CompileFilter`)
- Modify: `tests/parser_corpus.py`, `tests/parser_error_corpus.py`

**Interfaces:**
- Consumes: `Host::alloc_slot` (Task 2), `IfParser::template(compiler, tokens)`
  (`smartif.rs:313`) and `IfParser::parse()` (`:345`).
- Produces: `Host::compile_filter(&self, token: &str) -> Result<NodeRef, Error>`
  — declared here rather than in Task 2, because this task is its first and only
  caller (Task 2 correction 5).

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

- [x] **Step A: assert the condition is a host object, not a re-parse.** In
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
- [x] **Step B: run it against the stub** — red.
- [x] **Step C: run the seven steps.**

```bash
git add crates/template-core/src/parser/tags/if.rs \
        crates/template-core/src/parser/smartif.rs \
        tests/parser_corpus.py tests/parser_error_corpus.py tests/test_ref_table.py
git commit -m "tags: if, and the real compile_filter behind its condition"
```
- [x] **Step D: run the switch and confirm M5 is closed.**

Run: `./scripts/check.sh`
Expected: `test_no_m5_tag_is_still_a_stub` **PASS** — `builtin_tag_stubs()`
still names **`load`, `partial`, `partialdef`, `querystring`** and no longer
names any of M5's eleven. `Ran <N> tests … OK (skipped=6)`.

**That is four names and the earlier draft's list was wrong:** it said "`block`,
`load`, `partial`, `partialdef`". `block` can never appear here — it is one of
the three names `registry.rs` deliberately does *not* register (§6 assigns it to
Python), and `builtin_tag_stubs()` iterates the registry, so a name outside it is
not merely absent but unrepresentable. The fourth name is `querystring`, which
§D1 moves to M6 and which therefore stays a stub through M5.

This step also moves the two pins Task 1 declared, and both must move *here*:
the `stubs` literal in
`registry.rs::every_entry_is_reachable_and_every_unimplemented_one_is_a_stub`
from fifteen names to these four, and `deferred` in
`test_parser_parity.py` from `{"resetcycle"}` to `set()` — the latter in Task 6's
commit, with the `{% cycle %}` corpus row. A pin that did not have to move would
not have been a pin, and leaving either unmoved is a red `check.sh`, which is the
point.

**Six corrections to the draft, every one of them found by running it.**

*1. Step A asserts the identity at the wrong layer, and the draft's own wording
is the clue.* The draft asserts `condition is refs[0]` on the parse result.
Measured, that is **False**: on the wire a branch's condition is an `IfCondition`
whose `.expression` is a `_native.NodeRef` — an *index* into the table,
deliberately not the `FilterExpression`. The identity is real one layer down:
after materialisation the condition is Django's `TemplateLiteral` and
`condition.value is refs[0]` is `True`. Both are pinned in
`test_ref_table.py::IfConditionRefTests`, the handle shape *and* the object it
resolves to — asserting only the identity would pass for a shell that resolved
the index eagerly into a copy, which is precisely the re-parse the handle exists
to make impossible.

*2. The Errors row the draft predicted is not the error that template
produces.* The draft has
`("{% if a %}{% endif %}{% endif %}", 'Malformed template tag at line 1: "endif"')`.
Measured on the pinned tree that template raises `Invalid block tag on line 1:
'endif'. Did you forget to register or load this tag?` — the first `endif` closes
the tag and the second is an ordinary unknown tag, so no `do_if` code path sees
it. The Malformed literal is reached by the *terminator's trailing bits*:
`{% if a %}x{% endif foo %}` → `… "endif foo"` and
`{% if a %}x{% else foo %}y{% endif %}` → `… "else foo"`.

*3. "the smartif rows already in `ERRORS` from M3" — there were none.* The
draft's error paragraph says this task adds only the `do_if`-level row "because
the smartif rows are already proven". Measured: `ERRORS` carried no `{% if %}`
row at all, which is exactly why the error-side coverage gate was one of the two
reds Task 1 declared. **Eleven** rows went in, and they arrive by four different
routes — smartif's expression parser (four: two share the `EndToken.nud` raise
site, one is `Unused …` from `IfParser.parse`, one is prefix-`and`), the host's
`compile_filter` (two: the filter lookup and `Variable`'s remainder error), the
base parser (three: the unclosed-tag terminator list, the unknown `elif`, and a
second `endif` that is an ordinary unknown tag), and `do_if` (two Malformed
spellings from its **one** raise site at `defaulttags.py:1015` — `grep Malformed
.django/django/template/` is a single hit in `defaulttags`, so the draft's
attribution of `else foo` to smartif was wrong).

*4. The real `CompileFilter` did not go into `smartif.rs`, which the draft's
Files list says it does.* Measured, the production seam is
`Host::compile_filter` on the `Host` trait — implemented by `host.rs`'s default,
by the counting and filter hosts in `tags/mod.rs`, and by the FFI's
`FilterCheckHost` — and `smartif.rs`'s `CompileFilter` trait keeps its two test
stand-ins, because that module's own tests are about the *parser* and not about
the host. What `if.rs` supplies is `HostCompiler`, an adapter over
`Host::compile_filter`, and what this task added to `smartif.rs` is
`IfParser::condition` (the arena → `IfConditionDesc` walk, which the draft's
Interfaces block does not mention) plus a rewritten module doc. So the file did
change — but the draft describes the wrong change in it. The `git add` list
below is the measured one, which is also why it is longer than the draft's: the
draft omits `desc.rs`, `host.rs`, `tags/mod.rs`, `registry.rs`,
`template-py/src/lib.rs`, `_impl/parser.py`, `render.py`,
`test_parser_parity.py` and `test_parser_errors.py`, every one of which this task
changes.

*5. Every real caller has to pass `compile_filter`, and `None` is not a
permissive default.* Unlike `check_filter` — where `None` means "do not check",
and is what the earlier corpus rows were measured against — there is no
permissive answer here: the object has to exist, because `TemplateLiteral.eval`
resolves it at render time (`defaulttags.py:917-918`). So the FFI raises
`ErrorKind::Internal` naming the missing argument, which means three call sites
had to move with the tag: `_native.render` builds its own from the parser it was
handed (so the shell's end-to-end path cannot forget),
`tests/test_parser_parity.ours_parse` and `tests/test_parser_errors.ours_failure`.
Both tests pass **only** `compile_filter`, leaving `check_filter` at its
permissive `None`, so no earlier row's filter-error ordering moved. And
`test_parser_parity`'s `describe()` needed an arm for smartif's nodes — nothing
in smartif defines `__eq__`, so without one every `{% if %}` compared two live
objects by identity. The operator arm keys on `.id`, because all fourteen
operator classes are a local `Operator` and `type(...).__name__` is `"Operator"`
for every one of them.

*6. The seven-step task shape is missing a step, and `check.sh`'s order hides
it.* The shape for Tasks 3–7 (Step 5 "run `check.sh`") has no formatting step,
but `check.sh` runs `cargo fmt --all -- --check` (step 2) **before** clippy and
the Python tests (steps 3 and 4), so a
non-`rustfmt`-clean file reports a wall of diffs and no test results at all —
which reads like a code red and is not. `if.rs` was not clean, and the first
acceptance run of this task stopped there. The fix is `cargo fmt --all` before
Step 5's run, and the signal to recognise is a `Diff in …` header with no
`Ran <N> tests` line after it.

```bash
git add crates/template-core/src/parser/desc.rs \
        crates/template-core/src/parser/filter_expr.rs \
        crates/template-core/src/parser/host.rs \
        crates/template-core/src/parser/mod.rs \
        crates/template-core/src/parser/registry.rs \
        crates/template-core/src/parser/smartif.rs \
        crates/template-core/src/parser/tags/if.rs \
        crates/template-core/src/parser/tags/mod.rs \
        crates/template-py/src/lib.rs \
        python/django_rust_templates/_impl/parser.py \
        python/django_rust_templates/render.py \
        tests/parser_corpus.py tests/parser_error_corpus.py \
        tests/test_parser_errors.py tests/test_parser_parity.py \
        tests/test_ref_table.py \
        docs/superpowers/plans/2026-10-09-m5-remaining-built-in-tags.md
git commit -m "tags: if, and the real compile_filter behind its condition"
```

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
  note that §D was written from the same source. **Two are already known, both
  found during this plan's own self-review rather than at implementation time:**

  1. §D4's `with` said "the two legacy positionals are `None`", and the real
     signature is `WithNode.__init__(self, var, name, nodelist,
     extra_context=None)` with `cls(**kwargs)` construction, so the descriptor
     must emit kwargs *named* `var` and `name`; unnamed positionals raise
     `TypeError`.
  2. §D9's `ifchanged` was described as an ordinary descriptor, and
     `IfChangedNode.__init__(self, nodelist_true, nodelist_false, *varlist)` is
     variadic, so `cls(**kwargs)` **cannot build it at all** — §E adds the
     factory indirection, and it is the only M5 node that needs one.

  The other ten node signatures were measured and match §D. Record these two
  first, and keep measuring — a plan whose only found defects are the ones it
  went looking for has not been checked.

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
