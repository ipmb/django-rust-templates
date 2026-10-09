# Django Template Engine in Rust — Design

**Date:** 2026-10-08
**Status:** Draft for review
**Author:** implementing session (`django-rust-templates-92`)
**Reviewer:** `rusty-reviewer` (`django-rust-templates-a8`)

## 1. Goal

Reimplement Django's template engine in Rust as a drop-in replacement, validated by
running Django's own `tests/template_tests/` suite, unmodified, against the Rust
implementation.

## 2. Success criteria

Measured baseline, established in M0 by running the suite with the shim active
but every module still falling through to Django:

> `Ran 1547 tests` … `OK (skipped=6)`

That measurement establishes the **target**, and nothing more. At M0 every
module falls through, so the run is indistinguishable from stock Django — it is
not evidence that the substitution mechanism works, and must not be read as
validation of it. The mechanism is evidenced by `tests/test_shim.py` and by the
pre-flight in `scripts/run_django_suite.sh`, which refuse to run the suite
unless a module we provide resolved to our file. From M9 the suite itself
becomes the acceptance gate, and from then on it is meaningful.

The acceptance criterion is:

- **1547 tests run; 1541 pass; exactly 6 skip**, and each of the 6 skips is one
  of the structural cases enumerated below — not a test we skipped.
- **Zero failures, zero errors, zero `xfail`ed, zero edited tests.** Django's
  checkout is never modified; the suite is run unmodified.
- The 6 permitted skips are:
  - **5** jinja2-backend tests, which exercise a different template backend that
    this project does not reimplement: `Jinja2TemplateReloadTests`
    (`test_get_template_directories`, `test_reset_all_loaders`,
    `test_watch_for_template_changes`) and `test_using` in both
    `SimpleTemplateResponseTest` and `TemplateResponseTest`. These skip
    automatically because Jinja2 is deliberately not installed.
  - **1** `test_loaders.FileSystemLoaderTests.test_case_sensitivity`, skipped on
    **all POSIX platforms** by an upstream guard, not by this machine:

    ```python
    @unittest.skipUnless(
        os.path.normcase("/TEST") == os.path.normpath("/test"),
        "This test only runs on case-sensitive file systems.",
    )
    ```

    `os.path.normcase` is the identity function on POSIX, so the condition is
    `"/TEST" == "/test"` → `False` everywhere except Windows, where `normcase`
    lowercases. The filesystem is irrelevant: a case-sensitive APFS volume would
    not recover the test. Structural, not environmental.

    The consequence is a coverage hole — `FileSystemLoader`'s case-sensitivity
    is unexercised by the suite on any platform we will run, and that is loader
    code we own at M10. A project-side test, living outside Django's tree so the
    no-edit rule holds, closes it; it is scheduled at M10.

  Both Numpy array-index tests, which also skip in a bare environment, are made
  to run by installing Numpy — they are genuine Django template tests and are
  in scope.

Any *new* skip appearing later is a regression, and is treated as a failure.

## 3. Non-goals

- Optimising for speed. This is a **compatibility-first** build; performance is a
  possible later phase, not an acceptance criterion.
- Reimplementing the Jinja2 backend.
- Reimplementing Django outside `django.template` (ORM, views, forms). We depend
  on the real Django for everything else.

## 4. Constraints and pinned versions

| Thing | Choice |
|---|---|
| Django | `6.0.9` (latest 6.0.x patch), resolved SHA recorded in `django.lock` |
| Checkout | `.django/` — gitignored, fetched by `scripts/fetch_django.sh`, never committed |
| Python | 3.14 (matches the machine's `mise`-managed 3.14.4) |
| Rust | stable |
| Binding | PyO3 + maturin |

Django 6.0 requires Python 3.12+, so 3.14 is in support. `tests/template_tests/`
runs under Django's own `runtests.py` (unittest-based, not pytest).

**Every Unicode table here is a two-version artifact, and that is a rule rather
than a footnote repeated three times.** Three instances so far, and the two halves
of each are built from Unicode versions that move independently:

| table | Rust side | Python side |
|---|---|---|
| whitespace | `char::is_whitespace` (stdlib) | `str.isspace()` (CPython) |
| `\w` | `\p{L}\p{N}` (regex-syntax) | `re` / `str.isalnum` (CPython) |
| case | `char::to_uppercase` (stdlib) | `str.upper()` (CPython) |

Neither side is pinned hard — "Rust stable" is a `rust-version = "1.82"` floor
with no `rust-toolchain` file, and Python is fixed at 3.14.4 only by the venv. The
divergences do not even share a direction: Rust is **behind** Python on `\w` and
**ahead** of it on case, and the case count itself is a function of *which*
interpreter you ask (28 code points per direction under the pinned 3.14.4, 95
under this machine's system `python3`, measured 2026-10-08). So the rule is:
**never compute a table from one side and assert it — generate it from the two
live implementations and pin it with a test that compares them exhaustively.**
`scripts/gen_python_word_ranges.py` and `scripts/gen_python_case_fixups.py` are
the generators; `tests/test_pyregex_parity.py` and
`tests/test_pyunicode_case_parity.py` are the guards. The absent `rust-toolchain`
file is therefore a choice and not an oversight: a `rustc` upgrade fails a parity
test with a named code point rather than silently changing what a template
renders, and that test — not a version file — is the pin.

## 5. Architecture

Three artifacts.

### 5.1 `crates/template-core` — the engine, pure Rust

No PyO3, no Python types. Contains:

- `lexer` — `Lexer`, `DebugLexer`, `TokenType`, token positions (`lineno`, `position`)
- `parser` — `Parser`, tag dispatch, `parse_until`, `token_kwargs`, verbatim/comment
- `expr` — `FilterExpression`, `Variable`, literal/numeric-literal parsing,
  the underscore rule, `args_check` arity checking
- `smartif` — the `{% if %}` Pratt grammar and its AST
- `nodes` — every built-in tag
- `filters` — all 57 built-in filters
- `escape` — `conditional_escape` and the `SafeData`/`Promise` protocol
- `render` — the render loop, context stack, autoescape state, inheritance

Because it has no Python dependency, it is testable with plain `cargo test`. That
keeps the engine's own correctness suite independent of the FFI, which matters:
when the Django suite fails, we need to know whether the engine is wrong or the
seam is wrong.

### 5.2 The seam — the traits Python is modelled behind

**This section is design-time, and the tables below say which of its entries are
descriptions and which were proposals.** It was written before the code. Where a
name here has no counterpart there it is either a **retired** seam — replaced,
with the reason, in the prose that follows — or an **open obligation**, named
with the milestone that owes it. The code is the authority: a name lifted out of
this section without checking the table reads the design's *intention* as if it
were the implementation. Two review rounds read it that way.

Everything Python is modelled behind traits in `template-core`. Each method's
reasoning — why the shape is what it is and what the naive version measured —
lives in that method's own doc comment, which is where it has to be kept; this
section points at it rather than restating it, because a copied code block
propagates like a copied citation.

| trait | where | what it is |
|---|---|---|
| `LookupTarget` | `parser/resolve.rs:156` | one resolved value, and the per-bit cascade Django's `_resolve_lookup` runs against it |
| `Host` | `parser/host.rs:22` | the parser's callbacks — `has_tag`, `call_tag`, and a **defaulted** `check_filter` |
| `FilterHost` | `filters/host.rs:195` | the Python value protocol a filter body needs: `to_str`, `to_int`, `to_float`, `len`, `iter_values`, `get_index`, `get_slice`, `add_values`, … |
| `FilterFlags` | `filters/mod.rs:118` | the three per-filter attributes Django reads when calling a filter (`base.py:805-820`); `library.py:85` propagates all three, so they cross the seam |

#### What this section proposed, and what exists

| proposed here | actual | status |
|---|---|---|
| `ValueResolver` | `LookupTarget` | **renamed**; the per-bit loop it describes is right, and `LookupTarget` is where it lives |
| `ValueResolver::resolve_bit` | — | **retired** |
| `ValueResolver::force_promise` | — | **retired** |
| `ValueResolver::is_safe`, `is_callable`, `call` | `LookupTarget::call_arm`, plus the host's value protocol | **replaced**; `call_arm` folds Django's `callable`, `do_not_call_in_templates` and `alters_data` into one round trip, because only the host can read the flags |
| `LookupError` | `Step` (`resolve.rs:28`) + `LookupOutcome` (`resolve.rs:253`) | **split**; a per-bit verdict and a whole-path verdict are different lifetimes |
| `ExtensionHost` | `Host` + `FilterHost` | **split**; the tag half and the value half have no method in common |
| `ExtensionHost::call_tag` | `Host::call_tag` | **real**, in service |
| `ExtensionHost::filter_flags` | `_native.filter_flags` (`template-py/src/lib.rs:125`) | **real**, but a free function: `template-core` never reads a flag, the binding does |
| `ExtensionHost::call_filter` | — | **retired** |
| `ExtensionHost::render_foreign_node` | — | **retired** |
| `ExtensionHost::annotate_exception` | — | **open obligation** |

#### Retired seams

In prose, in the past tense, with the reason — and deliberately not left inside
the Rust block above, because a name in a code block propagates regardless of
the annotation beside it.

- **`ValueResolver::resolve_bit`** proposed the whole per-bit cascade as one host
  call. It was never built and the direction reversed: Rust keeps the loop *over
  bits* and the host owns the cascade *within* a bit. §6 gives the reason —
  Python owns `VariableDoesNotExist` and its message, but Rust owns the loop that
  constructs it — and it survives as `LookupTarget`'s method set
  (`get_item`/`get_attr`/`in_dir`/`get_index`), not as this name.
- **`ValueResolver::force_promise`** proposed forcing a lazy `Promise` at the
  seam. It was never built because **nothing needs it.** The claim it rests on is
  that **no forcing site is on the resolve path**, which is what is checked; the
  evidence is that the sites Django does have are both on the render path —
  `render_value_in_context` (`base.py:1123-1136`, whose conversion test is
  `issubclass(type(value), str)`) and `conditional_escape` (`html.py:115-127`,
  reached by `|escape`) — and the resolver's contract is to hand the object back
  **unforced**, which is what `Variable.resolve` (`base.py:924-940`) does. A
  third site elsewhere would not change that. Kept that way by a gate at the
  entry points (`tests/test_escape_protocol.py`, M8 Task 3).
- **`ExtensionHost::call_filter`** proposed running a filter body through the
  host. Bodies run through `_native.filter` → `filters::run_filter`, and what
  they need from Rust is the *value protocol*, not a callback: `FilterHost`.
- **`ExtensionHost::render_foreign_node`** proposed rendering a Python-owned node
  from Rust, taking a context. Rendering is Django's until M9 interposes the
  shadow tree, and the replacement is already in service: a Python node crosses
  as an opaque handle (`NodeRef`, `desc.rs:49`; `NodeListRef`, `desc.rs:181`),
  materialised into Django's own node objects by `_impl/parser.py` (§5.5). The
  handle is **not** a render callback and does not take a context.

#### Open obligations

Not retired and not built. A separate bucket, because the two fail in opposite
ways: a retired seam is work that was designed away, and an open obligation is
work still owed. A reader who meets the second where the first belongs stops.

- **`annotate_exception`** is **owed at M9.** It is Django's
  `Node.render_annotated` protocol (`base.py:1044-1068`): on any render-time
  exception with `engine.debug`, attach `_culprit_node` — innermost node wins —
  and then attach `template_debug` only at the frame whose
  `context.render_context.template.origin` matches the culprit's. That is stateful
  across nested renders and keyed on the exception's **identity**, so it cannot be
  reconstructed after the fact. Rust-owned nodes have no Python frame of their
  own, so Rust must call it for them; Python-owned nodes get it from Django's own
  unmodified `render_annotated` (§5.5). It goes live when
  `setup_test_environment` routes the suite's 1547 tests through
  `NodeList.render`, and **nothing supplies it today.** Pinned by
  `tests/template_tests/test_nodelist.py:44-86`
  (`ErrorIndexTest.test_correct_exception_index`, exact `(start, end)` pairs for a
  Python tag nested in a Rust-owned `{% for %}`) and `tests.py:146,166-172`.
- **`render_value_in_context`'s sequence** is **owed at M9, and no M8 task
  *could* have delivered it** — the claim is structural rather than a census of
  M8's five tasks. It is a module-level function in `base.py`, which is not
  interposed until M9, so Django's own runs on every render today and there is
  no site of ours for M8 to have filled; nothing under `crates/` or `python/`
  implements it or its conversion test. It is Django's `template_localtime` → `localize` → the
  autoescape branch (`base.py:1123-1136`), whose conversion test is
  `issubclass(type(value), str)` and **not** `isinstance` — a `SafeString` is a
  `str` and is not converted, while a `Promise` is not and is. It is on the
  render path, which is Django's until M9 interposes `base`. M8 measured the
  *consequence* of that — no forcing site is on the resolve path, so the
  resolver hands the object back unforced — without owing the port, and the
  forced-vs-unforced `Promise` distinction is latent for the same reason: M8's
  identity gate at `_native.resolve` (`tests/test_escape_protocol.py`) is what
  holds the contract in the meantime.

```rust
/// Why this shape and not a flattened Python exception. Kept as a block because
/// the type is real; the *methods* that consume it are in the trait table above.
pub enum Error {
    /// An exception raised by the host. Opaque to `template-core`, which stores
    /// and re-raises it but never inspects it. Flattening a Python exception
    /// into a Rust type loses the object's identity and its `_culprit_node`,
    /// which the annotation protocol above depends on — an exception that has
    /// been through a lossy conversion can no longer be annotated correctly.
    Host(HostError),
    Syntax(TemplateSyntaxError),
    // …
}
```

`NodeRef` and `NodeListRef` are opaque handles to Python-owned nodes returned by
a user tag callback. `template-core` never dereferences them — it stores them,
renders them via the host, and asks the host for their identity when building the
shadow tree.

#### `ParserHandle` — re-entrancy is the contract

`call_tag` cannot take `&mut Parser`. Python callbacks in the suite re-enter the
parser *mid-parse*:

- `Library.simple_block_tag` (`library.py:211-212`) calls
  `parser.parse((end_name,))` and then `parser.delete_first_token()`. `Library`
  is Python-owned per §6, so this path is Python. `templatetags/custom.py`
  registers three such tags (`div`, `div_custom_end`, `one_param_block`),
  exercised by `test_custom.py`.
- That nested parse dispatches tags which may re-enter Python again — mutual
  recursion across the FFI, unbounded.

An aliasing `&mut Parser` cannot be handed to PyO3 and survive that: Rust would
have to hold it across the callback, and the callback needs it simultaneously.
So the parser lives behind interior mutability — shared ownership with a
borrow-checked cell, `Rc<RefCell<Parser>>`-shaped, `Arc<Mutex<…>>` if it ever
needs to be `Send` — and `ParserHandle` is a cheap copyable handle to it.

**Contract:** inside a host callback a `ParserHandle` may drive `parse`,
`delete_first_token`, `skip_past` and `next_token`. Rust must never hold a
direct `&mut Parser` across a host call; it re-acquires the borrow afterwards.
Re-entrant `parse` returns a `NodeListRef` that Rust treats exactly like a
`NodeRef` — opaque, host-owned, rendered via the host.

Two more requirements on the same handle, both pinned by tests:

- **`extra_data` is a real Python `dict`** holding Python objects, not a Rust
  `HashMap<String, Value>`. `custom.py:398` stores a string in it
  (asserted at `tests.py:148-155`); `{% partialdef %}` stores a
  `PartialTemplate` proxy, read at 20+ sites in `test_partials.py`.
- **Arbitrary attributes must work.** `loader_tags.do_block:228-234` reads
  `parser.__loaded_blocks`, catches the `AttributeError` the first time, and
  assigns the list on the parser (a module-level function, so no name
  mangling). The handle proxies unknown attributes to a host-side bag. **This
  requirement is M7's, not this milestone's** — it is `{% block %}`'s compile
  function, and the host does not compile `block` until M7. One half of it is
  not negotiable and travels with the requirement: the handle must *raise*
  `AttributeError` for an attribute it does not have. `do_block`'s `except`
  branch (`loader_tags.py:233-234`) is the only place `__loaded_blocks` is
  created, so a handle that answered every name would move that list's lifetime
  out of the parse and into the bag, and Django's duplicate-block-name check
  would then be asserting something about the bag rather than about the
  template.
- **`extra_data` is absent after a failed parse.** `compile_nodelist`
  (`base.py`) assigns `self.extra_data = parser.extra_data` *inside* its `try`,
  so the attribute never appears on failure, and `test_partials.py:171` tests
  that absence. Therefore the parse entry point returns the handle alongside the
  result — `parse(source) -> (NodeListRef, ParserHandle)` — so the Python shell
  can do that assignment inside its own `try`.

That last point is the general shape: **the shell owns every construct whose
observable state differs on the failure path.** Rust returns values; Python
decides what has been assigned when an exception unwinds through it.

### 5.3 `crates/template-py` — the binding

Implements both traits over `PyObject` and exposes the `_native` module via PyO3.
This crate is where all `unsafe` FFI concerns live.

### 5.4 `python/django_rust_templates/` — the shell

Pure Python. Re-exposes `django.template.*`.

### 5.5 The shadow tree

Rust parses once and returns a **node descriptor tree** — names, attributes,
`origin`, child structure. Python materialises real `Node`/`NodeList` objects
from it, so that `template.nodelist`, `NodeList.get_nodes_by_type`,
`node.origin`, and `repr(node)` behave exactly as Django's.

#### The render path is Python's, and that is not optional

An earlier draft justified the shadow tree by noting that **zero tests call
`node.render(context)` directly** — true, and verified: an exhaustive grep for
`(node|Node|nodelist|NodeList)\w*\.(render|render_annotated)\(` across
`tests/template_tests/` returns one hit, a docstring in
`syntax_tests/test_for.py:334`. But that is a claim about *test* code, and the
coupling comes from *framework* code the tests run under, so it argues the wrong
proposition.

Django's test runner calls `setup_test_environment()`
(`django/test/utils.py:108-114`), which unconditionally sets

```python
Template._render = instrumented_test_render      # and PartialTemplate too
```

where `instrumented_test_render` calls `self.nodelist.render(context)`, and
`NodeList.render` (`base.py:1091`) is
`SafeString("".join([node.render_annotated(context) for node in self]))`.

**For all 1547 tests, the render entry point is therefore the Python nodelist.**
Inert descriptor nodes would render every test empty. The suite is green at M0
only because `base` still falls through; this fires at M9, when the entire suite
flips onto our `base` at once.

**Decision: keep Django's `NodeList.render` and `Node.render_annotated` verbatim,
and make shadow nodes genuinely renderable in Python.** Concretely:

- `NodeList.render` and `Node.render_annotated` are ported from Django unchanged
  (they are interface contract — §6's transcription rule applies). Keeping them
  verbatim keeps Django's exception-annotation protocol in Python, where Django
  put it and where its statefulness is already correct, instead of
  reimplementing it in Rust (§5.2's `annotate_exception` covers the frames that
  have no Python counterpart).
- A shadow node's `render(context)` delegates to Rust for that node's subtree:
  `NodeList.render` iterates top-level shadow nodes in Python, and each one's
  `render` is one FFI entry that renders the whole subtree beneath it.
- Rust renders interior subtrees from its own compiled form and does **not**
  call back into Python `NodeList.render` for **shadow-nodelist** children. The
  boundary is **per node, not per subtree**: every node is either Rust-owned
  (constructed by Rust for a built-in tag) or Python-owned (constructed by
  Python — a user or custom tag, a `simple_block_tag`, or one of our ported
  `loader_tags` classes). A Rust-owned node's `render` delegates to Rust; a
  Python-owned node is never rendered inline by Rust, it is handed to the host
  through `render_foreign_node`, which goes through that node's
  `render_annotated`.

This is what makes the mixed case work in both directions. A Python custom tag
compiled via `parser.parse()` owns a shadow nodelist, and its `render` reaches
Rust through Django's own `NodeList.render`. Symmetrically, a Rust-owned
`{% for %}` whose body contains `{% include %}` hands that Python node to the
host rather than rendering it inline — so `IncludeNode.render` calling
`template.render(context)` re-enters Python mid-Rust, which is correct and
intended rather than a leak.

Cost is one FFI hop **per node entered from Python**. For a Rust-owned spine
that is one hop per top-level node; where Python-owned nodes nest, it is one per
level. This is a **compatibility-first** build (§3), so that is the right trade.

Consequence for planning: **M9 is the highest-risk step in the project** — the
point where the whole suite flips at once — and §10.3 must budget for it
accordingly rather than giving it one line.

#### Materialisation lifetime

The tree is materialised **once per `Template` compile**, and `NodeRef` handles
are **identity-stable for that template's life**. Re-materialisation — per
render, per `{% include %}`, or lazily on attribute access — is a correctness
bug, not merely a performance one:

`IncludeNode.render` caches the resolved template in
`context.render_context.dicts[0].setdefault(self, {})` — a Python dict keyed on
the **Python `IncludeNode` instance**. `test_include_cache`
(`syntax_tests/test_include.py:371-397`) renders
`{% for x in vars %}{% include "include" %}{% endfor %}` over an innermost
`{% load custom %}{% counter %}`, and asserts `"012345678"`. That passes only if
the single Python `CounterNode` — with mutable `self.count`
(`custom.py:386-393`) — is constructed **once** and re-entered nine times. Any
fresh materialisation resets `count` and yields `"000000000"`.

So `test_include_cache` is an explicit **M7 gate**, and the shadow tree must be
reachable by identity from Rust for the template's lifetime.

Materialisation must also reproduce `Parser.extend_nodelist` exactly — that is
where each node acquires `token` and `origin`, and `render_annotated` reads both.
The contract is written out in §6; it is a correctness requirement for blocker 2's
annotation protocol, not a detail.

A structural-equality test per milestone guards against drift.

## 6. Ownership split

**Rust owns:** tokenization and positions; `Parser` and tag dispatch;
`FilterExpression`/`Variable` compilation; the smart-if grammar; variable
resolution and auto-calling; `conditional_escape`; the **filter layer** — dispatch,
`FilterFlags`, arity, the escaping protocol — for all 57 built-in filters;
**compiling** the inheritance structure — which nodes are blocks, the `blocks`
mapping, `get_nodes_by_type` results, and the `must_be_first` ordering rule.

**"All built-in tags and filters" was too strong, and the exception is permanent,
not deferred.** An earlier draft of this section said Rust owns "all built-in tags
and filters" and M4's plan then assumed seven filter bodies would come home to
Rust at M10. Both were wrong, in the same way: **M10's `utils` is
`django/template/utils.py` (the `EngineHandler` module), because `_shim.py:52`
sets `TARGET = "django.template"` and the finder takes over exactly one
namespace.** `django.utils.dateformat`, `django.utils.formats`,
`django.utils.html`, `django.utils.text`, `django.utils.timesince` and
`django.utils.translation` are therefore **unreachable by any milestone in §10.3**.
That is §3 working as written — "We depend on the real Django for everything else"
— not a hole in the plan, and it needs to be stated here so that a reader at M10
does not go looking for filter bodies nobody scheduled.

Measured, and **derived in tests rather than enumerated** the way §10.3's tag
inventory derives 27. There are **four** delegating metrics, because the first
one is not the question: a filter delegates when (A) the utility its body calls
bottoms out, within its own module, in runtime state Rust cannot hold (settings,
locale, the active language, the clock) or in a Python-stdlib table-or-parser
port (`HTMLParser`, `textwrap`, `json`, `unicodedata`, and — outside
`django.utils` — `pprint`, `random`, `urllib.parse`); (B) its body calls a `str`
method whose result is not a function of the code point alone (`lower`, via
Final_Sigma, and `title`); or (C) its body **reproduces** a helper whose `def`
carries a result-changing lazy decorator (`phone2numeric`'s `@keep_lazy_text`),
where the wrapped function is the *decorated* one rather than the `def` body.
The metric that matters most is (D), which asks our own Rust bodies the same
question by reading which trait sections their host calls sit in, because A–C
are read off `defaultfilters.py` and so answer a question about Django; only D
reddens when a body is rewritten to stop delegating.

**The sets are asserted, not restated here** —
`tests/test_filter_inventory.py::test_the_delegation_set_is_derived` derives each
metric and asserts the union against the Rust-derived set in both directions, so
this paragraph does not carry a number that can go stale. It carried `26` for the
module-granularity count and `18` for the set; the module count is **27**, and
the set is larger than 18 for the reasons above.

The neighbouring counts are each a right answer to a different question, which is
why the metric is written down: 57 registered filters; **31** reference *any*
`django.utils` name (which includes `mark_safe`, a primitive Rust reimplements as
`Value::Safe`); **27** reference a utility in a module that *imports* something
stateful (which wrongly sweeps in `escape`, `join` and `linenumbers` because
`strip_tags` lives in the same file).

For every delegating filter the filter **layer** is still Rust's (dispatch, `FilterFlags`, the
`safe` marking, the `int(arg)` conversions and the failure paths) and the
**body's answer** comes from Django. The parity target at M4 is that the
delegation is *correct*, not that the logic is Rust's. One consequence, written
down because it otherwise reads as an oversight later: `{{ x|date:"Y" }}` crosses
the FFI on every render, permanently. §3 makes performance a non-criterion, so
that is a choice rather than a cost.

**Python owns the render-time inheritance dance**, and this is a correction to an
earlier draft that assigned "block/extends/include resolution" to Rust wholesale.
`BlockNode.render`, `ExtendsNode.render` and `IncludeNode.render` are coupled to
a protocol that lives in Python:

- `ExtendsNode.render` reads and mutates `context.render_context`
  (`BLOCK_CONTEXT_KEY`, `setdefault`, `push_state(compiled_parent,
  isolated_context=False)`) and calls `compiled_parent._render(context)` — the
  very method `setup_test_environment` replaces (§5.5).
- `BlockNode.render` pops and pushes on a `BlockContext`, and constructs a fresh
  instance via `type(self)(block.name, block.nodelist)`.
- `IncludeNode.render` caches in `context.render_context.dicts[0].setdefault(self, {})`,
  keyed on the Python node instance.

Modelling all of that across the seam would mean Rust owning a Python object's
mutation protocol for no gain. Under §5.5's decision the boundary is already at
Python frames, so these three node classes are **Python shell code, ported
verbatim** — which is also why the Python side is larger than an earlier draft
implied (§13). Rust supplies the compiled structure they operate on.

**Compiling the structure means reproducing `Parser.extend_nodelist`
(`base.py:600-617`) exactly**, since it — not each node's `__init__` — is where a
node acquires its position:

```python
def extend_nodelist(self, nodelist, node, token):
    if node.must_be_first and nodelist.contains_nontext:
        ...  raise "{%% %s %%} must be the first tag in %s."
    if not isinstance(node, TextNode):
        nodelist.contains_nontext = True
    # Set origin and token here since we can't modify the node __init__()
    # method.
    node.token = token
    node.origin = self.origin
    nodelist.append(node)
```

The shadow tree must run the equivalent for **every node `render_annotated` can
reach** — not for every node unconditionally. The operationally checkable form
of that phrase is **every node appended to a `NodeList`**, which is decidable at
parse time; "reachable by `render_annotated`" is the property that matters but
it is stated in terms of a render-time call graph. The two agree wherever it
counts: `extend_nodelist` is the sole appender, and the `BlockNode` copy below
is exempt under either reading. Django draws that line itself:
`BlockNode.render` (`loader_tags.py:63`) builds a node at *render* time with
`type(self)(block.name, block.nodelist)`, which never passes through
`extend_nodelist` and so carries neither attribute. That is safe only because
the result is used for `block.nodelist.render(context)` and nothing else, and
those inner nodes did come from `extend_nodelist`. An invariant phrased as
"every materialised node has `token` and `origin`" would therefore fire on the
verbatim-ported `BlockNode.render` at M7 — a false alarm in the one place the
missing pair is deliberate.

The two attributes are not equally dangerous, and that asymmetry is the reason
the property is worth stating rather than leaving implied:

- a missing `token` is survivable — `get_exception_info(e, None)` accepts it;
- a missing `origin` is fatal. `render_annotated` evaluates
  `context.render_context.template.origin == e._culprit_node.origin`
  (`base.py:1060`) *inside* its `except` block, and `Node` supplies no
  class-level `origin`, where `token` does have one (`token = None`,
  `base.py:1036`). The `AttributeError` is therefore raised while handling the
  original exception — the "Django bug, not a seam bug" symptom.

`contains_nontext` and `must_be_first` are the `{% extends %}` ordering rule,
with that exact message.

`must_be_first` is `True` in exactly one place in the whole template package —
`ExtendsNode` (`loader_tags.py:87`). Everything else inherits `Node`'s `False`.

**The `Parser` surface, verified against the pinned source** (M2 builds this
exactly; recorded here because §6's ownership claim is only meaningful with the
contract written down):

- `__init__(tokens, libraries=None, builtins=None, origin=None)` sets `tokens` as
  `list(reversed(tokens))` — so `next_token()` is `tokens.pop()` and
  `delete_first_token()` is `del tokens[-1]`, both O(1) at the end — plus `tags`,
  `filters`, `command_stack`, `extra_data`, `libraries`, `origin`. `builtins` is
  consumed via `add_library` and **not stored**.
- There is **no `add_libraries`**; only `add_library(lib)`, which does
  `tags.update(lib.tags)` and `filters.update(lib.filters)`.
- `compile_filter(token)` takes **one** argument — there is no flags or
  `ignore_invalid` parameter in 6.0.9. Every call site passes a bare string.
- `error(token, e)` **returns** the exception rather than raising it, wrapping a
  non-`Exception` in `TemplateSyntaxError`, and sets `e.token = token` **only if
  the attribute is absent**. So the *innermost* token wins, which is what makes a
  compile error inside an `{% if %}` body point at the inner tag. Rust owns the
  message; the shell owns the Python exception object.
- `parse_until` termination is `prepend_token` (put it back) plus `return`; an
  exhausted token list with a non-empty `parse_until` reaches
  `unclosed_block_tag`, which pops `command_stack` for the message.
- No tag in `defaulttags.py` or `loader_tags.py` calls `parser.error` directly.
  The only two calls to `invalid_block_tag` are `{% partialdef %}` and
  `{% block %}`; everything else raises `TemplateSyntaxError` itself, and
  `parse`'s `except Exception as e: raise self.error(token, e)` is what attaches
  the token.

**Python owns:** the `Library` registry and dotted-path loading; `Engine` and the
configured loader classes (framework-coupled: `override_settings`,
`INSTALLED_APPS`, `origin.loader_name`); `Context` and context processors;
`Origin`; `TemplateDoesNotExist` traceback hygiene; `logging` integration;
`PartialTemplate`; `backends.django.DjangoTemplates` and `response`; the
parse-time and render-time callback plumbing.

**Vendoring policy:** Django's Python source is **not** vendored. The shell is
written fresh, with Django's implementation treated as the specification — error
strings, token positions, and reprs are ported *verbatim* and pinned as test
fixtures. A drop-in replacement that ships Django's own Python would be
self-defeating; behaviour fidelity is non-negotiable because the suite pins exact
strings.

The boundary needs to be stated precisely, because "written fresh" cannot mean
"written different". There are two distinct things:

- **Re-deriving an interface contract** — required, and must be faithful.
  Signatures, attribute defaults, export lists, and import *order* are the
  observable interface. A file whose behaviour *is* its structure has no
  independent implementation to write.
- **Copying implementation logic** — not done. Algorithms, control flow, and
  internal structure are written against Django as a spec, not transcribed.

`_impl/__init__.py` sits on the first side and is a **transcription**: it is
Django's `django/template/__init__.py` with the docstring and the
`# NOQA isort:skip` comments stripped, and nothing else changed. That is
correct, not a lapse — the export list *is* the behaviour. Import order is
load-bearing (`engines = EngineHandler()` must precede the later imports;
`from . import autoreload` registers signal receivers), so no "fresh" version
could differ and still be right. `_impl/exceptions.py` is on the other side and
is written fresh — restructured `__init__`, rewritten docstrings — which is the
policy working as intended.

**Transcribed files are marked as such** in a
`# TRANSCRIBED: <upstream path> (Django <version>)` header naming the upstream
path, so they are diffed against upstream on every Django bump rather than
reviewed as original work. A transcription reviewed as if it were original is
how a bump silently changes behaviour.

The marker is not decoration and is not trusted as prose. `scripts/check_transcriptions.py`
parses it and requires that the path it names is the one the file is declared
against and the version it names is the version of the checkout — so a
transcription taken from a different release, or pointed at the wrong upstream
file, fails rather than passing quietly. The same script asserts three
properties of the declaration itself:

1. every differing line falls inside a declared region, and no region covers
   nothing;
2. a region containing executable lines declares a **snippet** naming every one
   of them, so a widened line range cannot swallow a statement unread;
3. no region intersects the code a parity harness *executes*. That set is
   measured, not inferred — for `_impl/base.py` it is 28 functions spanning
   lines 116-1209 — because the compared path runs through helpers
   (`extend_nodelist`, `compile_filter`, every tag's compile function) that a
   region can sit inside while looking like it is outside `Parser.parse`.

## 7. Substitution mechanism

A `MetaPathFinder` intercepts the `django.template` namespace when
`DJANGO_RUST_TEMPLATES=1` is set, installed before Django's test runner starts.

A meta-path finder rather than `sys.modules` assignment, because Django's own
internals — `django.test`, `django.template.backends.django`,
`django.template.loader` — import submodules by path, and dict assignment does
not intercept those.

The finder returns, for a package we provide, a search path of **our directory
first and Django's second, rebuilt at every level**. Returning a spec without
one would leave a package with `__path__ == [our directory]` alone, so the first
time a half-finished subpackage exists (`_impl/loaders/__init__.py`),
`django.template.loaders.filesystem` stops resolving entirely — the fallback
vanishing exactly when a milestone is mid-flight, which is when it is needed
most. `loaders/` and `backends/` are both walked into by §10.3.

Django's own search paths are read with `importlib.util.find_spec`, which
carries `submodule_search_locations` **without executing the package** — nothing
is imported merely to be discarded.

**Installation happens in `sitecustomize`**, on `PYTHONPATH` ahead of Django's
checkout, so the substitution is in place before anything imports
`django.template` and Django's own `runtests.py` is driven without its tree
being modified. CPython's `site.execsitecustomize` catches every exception this
module raises, prints one line, and continues — so an unimportable
`django_rust_templates` (venv not synced, extension not built, wrong
`PYTHONPATH`, `python -S`) would let the whole suite run against stock Django and
report `OK`, byte-identical to success. Under `DJANGO_RUST_TEMPLATES=1` that
path is unreachable: any failure prints its traceback and `os._exit(1)`s.

Three guards stand between "the suite passed" and "the suite passed against our
code":

1. `sitecustomize` exits loudly rather than falling back (§7).
2. `scripts/run_django_suite.sh` runs a pre-flight in a subprocess that asserts a
   module we provide resolved to our file, and refuses to run the suite
   otherwise.
3. `tests/test_shim.py` tests the mechanism directly, including the
   nested-subpackage fallback.

The suite is only an acceptance gate from M9, when modules we own are the ones
being exercised. Before that it measures the target, not the mechanism (§2).

`unshimmed()` — the progress meter — has one honest limitation: it compares
source **file existence**, not behaviour. An `_impl/base.py` consisting of
`from django.template.base import *` would drop out of the list while changing
nothing. That is the right trade (a static comparison has no import side effects,
which matters for a meter callable anywhere), but the count is therefore an
upper bound on progress, and the behavioural check that matters is the
differential harness (§9), not the meter. `strict=True` failing when a fallback
is active is deliberately unusable until M14; what fails early and loudly is the
*shim not being installed*, which is the condition that is always wrong.

`django.test` itself renders templates (`assertTemplateUsed`, response
rendering), so the shim must satisfy framework-level use, not only
`template_tests`.

## 8. Repo layout

```
Cargo.toml                          workspace
pyproject.toml                      maturin
crates/template-core/
crates/template-py/
python/django_rust_templates/
scripts/fetch_django.sh             → .django/ at pinned tag
scripts/run_django_suite.sh         → full suite
scripts/run_component.sh            → one test module, fast loop
django.lock                          pinned tag + SHA
.review/                             round-N.md exchanges
docs/superpowers/specs/
docs/superpowers/plans/
```

## 9. Test strategy — three layers

1. **Rust unit tests** (`cargo test`) — engine semantics in isolation. Fast, and
   the primary development driver.
2. **Differential harness** — renders a corpus twice and compares, including
   error types and messages. Two modes:
   - **shim vs stock** — proves the substitution layer does not change Django's
     behaviour. Meaningful from M0, when everything still falls back.
   - **rust-native vs stock** — calls the Rust engine directly through `_native`
     and compares against Django. This is how the engine is measured *before* it
     is interposed, and it is the main day-to-day signal.
3. **The Django suite** — the acceptance criterion. Run in full at every
   milestone boundary; per-module once the modules a test needs are ours.

## 10. Development strategy and milestones

### 10.1 Why modules, not test files, are the unit

Interposition is atomic: a module under `django.template` is either Django's or
ours, never partly ours. `_impl/base.py` either exists and is complete, or does
not exist and Django's `base.py` serves every importer.

This invalidates "unlock these test files" as a milestone unit. `test_base.py`
imports `Template`, `Variable` and `Context` and so needs `base`, `context` *and*
`engine` to all be ours before it can pass — even though it tests the lexer,
which is a small part of `base`. A milestone framed around it would be
unmeasurable for most of its life.

So the project runs **engine-first**:

- The Rust engine is built and measured **directly**, through native entry points
  (`_native.lex`, `_native.parse`, `_native.render`), driven by `cargo test` and
  the rust-native differential mode. Progress is visible from M1 onward, per case
  rather than per test module.
- A module is **interposed** only when it is complete. At that point its Django
  tests become real signal rather than noise.
- The Django suite runs throughout, in both modes. The acceptance gate is §2,
  reached when `unshimmed()` is empty and strict mode is clean.

`unshimmed()` therefore measures *integration* progress, while the differential
corpus measures *engine* progress. Both matter; neither alone is the project's
status.

### 10.2 Consequence for `base.py`

`base.py` is 1100 lines and contains the lexer, parser, expression classes,
`Template`, `Node`, `NodeList` and `Origin`.

The reason it interposes late is **completeness, not dependency**. `base` is one
module, so it interposes atomically (§10.1) — the import graph does not constrain
it. At import time it needs exactly two intra-package names, and both may be
Django's:

```
base.py:59    from django.template.context import BaseContext
base.py:70    from .exceptions import TemplateSyntaxError
```

`context.py` has zero intra-package imports, so there is no cycle and no mutual
dependency: `context` and `base` are independently interposable in either order.
The one cycle that exists — `base`'s reference to `Engine` (`base.py:148`) — is
deferred inside a method, and it is this direction that is broken;
`engine.py:7`'s `from .base import Template` is module-level and unconditional.
That last fact is the useful one: because that import is unconditional, Django's
`Engine` *cannot* avoid picking up our `base` once it is interposed, provided
ours is API-compatible. Interposition transparently re-points every importer.

The binding constraint is that `base` contains `Variable.resolve`, which cannot
be finished until M8 resolves `SafeData`, `Promise`, callables and
`alters_data`. So `base` lands after the resolver, as one unit, and everything in
M1–M8 is developed but not yet interposed.

**The real risk in M9 is not the import graph — it is §5.5.** Interposing `base`
means owning `Node.render_annotated` and `NodeList.render`, which is exactly what
`setup_test_environment()` routes all 1547 tests through. That is what makes M9
the highest-risk milestone, and §5.5 is where the answer to it lives.

### 10.3 Milestones

| | Component | Done when |
|---|---|---|
| M0 | Bootstrap, substitution mechanism, harness | ✅ 1547 run / 6 structural skips; full suite green with `exceptions` interposed |
| M1 | Rust lexer, `Token` + `Token.split_contents`, `smart_split`, `TokenType`, `DebugLexer`, `_native.lex` | ✅ token-stream parity with Django's real `Lexer`/`DebugLexer` over `tests/lexer_corpus.py` (51 sources × both lexers), debug character positions included, plus `split_contents` parity and the 3000-source randomised pass; progress meter unchanged from M0 at 25 |
| M2 | Rust parser, node AST and descriptor format — `Parser`, `NodeList`, `extend_nodelist`, the error protocol, tag dispatch, `_native.parse`, and the materialisation of descriptors into real Django nodes. Compile functions for the **nine** built-in tags that need no `FilterExpression`; syntax-error parity with Django's parser. The other eighteen built-in tags are scheduled with their dependencies (M3, M6, M7, M13) — see the M2 plan's scope note for the mapping. | Awaiting review: parity with Django's real `Parser` over a 59-source corpus and 400 seeded random templates in both lexer modes, compared node for node after materialisation; syntax-error parity over a 32-case corpus; `scripts/check.sh` green with the suite at `Ran 1547 tests … OK (skipped=6)`; progress meter unchanged at 25 |
| M3 | `FilterExpression`, `Variable`, `smartif`, primitive resolver | ✅ `{{ … }}` parses and materialises into Django's own `Variable`/`FilterExpression` over a 72-source corpus and 400 seeded random templates in both lexer modes; `_native.resolve` and the first `_native.render`; `token_kwargs`/`kwarg_re` for M5–M7; `smartif` ported with one bounded divergence on nesting depth. `scripts/check.sh` green with the suite at `Ran 1547 tests … OK (skipped=6)`; progress meter unchanged at 25. **One deliberate deviation from §10.4's gate**, corrected and approved at the M3 round: what moves to M7 is Django's `{% extends %}` message and the one real `must_be_first` tag, **not** the refusal rule, which ships and is exercised against a stand-in — see that entry |
| M4 | All 57 built-in filters | ✅ all 57 names have Rust bodies, asserted as a *set* by `tests/test_filter_inventory.py` (`STILL_STUBBED` is the empty array); corpus parity over `tests/filter_cases.py` — 539 `CASES` + 40 `ERROR_CASES`, every `expected` verified against Django as well as against the engine; `FilterFlags` round-tripping against the pinned registry; the delegating bodies derived by `tests/test_filter_inventory.py`'s four metrics — the set is not stated here, because the only assertion that keeps it current is the one that derives it from both the source and the Rust bodies. `scripts/check.sh` green with the suite at `Ran 1547 tests … OK (skipped=6)`; progress meter unchanged at 25. **The 1547-test gate cannot see a single filter body** — its only route into the Rust filter layer is `check_filter`'s name-and-arity check — so the corpus, not the acceptance criterion, is what separates a landed body from a verified one; see the M4 summary's "acceptance blind spot" |
| M5 | The eleven remaining built-in tags. **The set is cited, not copied**: it is `M5_TAGS` in `tests/test_tag_inventory.py`, and the assertion that keeps it current is the one that derives it — a list here would go stale the moment a tag moved | ✅ each of the eleven bodies landed, asserted as a *set* (`builtin_tag_stub_names() & M5_TAGS == set()`); success corpus `tests/parser_corpus.py` (142 sources) + error corpus (78), every `expected` verified against Django as well as against the engine; `scripts/check.sh` green with the suite at `Ran 1547 tests … OK (skipped=6)`; progress meter unchanged at 25. **The milestone's seam audit is `.review/m5-seams.md`** — it found `Host::repr`'s shell override and `IfParser::condition`'s `Raw` arm each guarded by a comment rather than by an input that discriminates |
| M6 | `Library`, extension host callbacks; **and `querystring`**, moved here from M5 by the M5 plan's §D1 — it is `@register.simple_tag(…, takes_context=True)`, the sole non-`do_` registration among the built-ins, so it needs the extension host M5 does not build | ✅ Python-defined tags/filters work through the engine, incl. re-entrant `ParserHandle.parse`. Ownership, measured: `builtin_tag_names_including_stubs()` is **22** and `builtin_tag_stub_names()` is `['partial', 'partialdef']` — `load` and `querystring` are **host-owned**, so Rust implements neither, and `partial`/`partialdef` are names the registry knows with no compile function behind them yet. `unshimmed()` is unchanged at 25; `cargo test` is 645. Gates: `scripts/check.sh` green with the suite at `Ran 1547 tests … OK (skipped=6)`; the parity corpora are `tests/parser_corpus.py` (152 sources) and `tests/parser_error_corpus.py` (91 errors), plus the 48-case differential harness. **`block`/`extends`/`include` are excluded from the host at M6, so `{% extends %}` still raises `Invalid block tag`** — a divergence from Django, owned by M7, which owes both the removal *and* `must_be_first` across the seam |
| M7 | Inheritance: `block`, `extends`, `include` | ✅ Rust compiles all three, so `builtin_tag_names_including_stubs()` is **25** and the host compiles only `load` and `querystring` (measured: `parser.tags` holds 27 names, Rust's 25 are exactly the registry's set). **The gate is `tests/test_inheritance_gate.py`, and what it takes from `test_include_cache` is the row's *shape*, not the row.** The `.django/` row cannot be M7's gate: the shim does not interpose `loader_tags`, so it runs on stock Django's parser and passes whether or not M7 shipped — §10.3's vacuity rule below. Both engines are compared in one row, and the row is paired with a **structural** one because the counter row is a *parity* instrument: the include cache is Django's own, both engines read it, and breaking it gives the same wrong output on both sides (`IncludeNode.render`, keyed on the node). `unshimmed()` unchanged at **25**; `scripts/check.sh` green with `Ran 1547 tests … OK (skipped=6)` |
| M8 | Escaping and the Python object protocol | ✅ both remaining resolver arms are wired, and the second is not a call: `CallArm::Call` invokes, `CallArm::AltersData` **substitutes `string_if_invalid` by assignment inside `for bit in self.lookups:`** (`base.py:997`), which is the part a substitute-and-stop port gets wrong — measured with `string_if_invalid='<<invalid>>'`, `{{ x.upper }}` runs `.upper` on the *invalid* string, so a port that returns instead renders `x` right and `x.upper` wrong. `LookupTarget` gained two methods (`call`, `string_if_invalid`), nine → **eleven**. **Nothing forces a `Promise`** — measured as a *contract* rather than by enumerating Django's forcing sites, which would be an exhaustiveness claim: one lazy proxy comes back the **identical object** through all five shapes the resolve path has (a context value, an attribute, a dict key, an integer index, and a called method's return — the callable arm), asserted by identity, because `str(proxy) == proxy` is `True` while `str(proxy) is proxy` is `False`. So §5.2's third obligation is not a forcing site to build but a *contract* — hand the promise back unforced. **The row is out of scope of the vacuity rule and owes its second question an answer** (below), and the answer is measured: the ten `.django` modules naming M8's surface run **88 tests, 0 failures while `_native.resolve` raised on every callable path**, because the render path is Django's own `_resolve_lookup` until M9. So the gate is four **direct-entry** modules — `tests/test_resolve_callarm.py`, `tests/test_resolve_invalid.py`, `tests/test_escape_protocol.py`, `tests/test_m8_side_effects.py` — comparing `_native.resolve`/`_native.filter` against `Variable._resolve_lookup`/`FilterExpression.resolve`. **And parity is only half of it**: what `alters_data` protects is "the method was not called", which is not a property of the output — with `string_if_invalid=''` a port that calls the callable renders the identical row — so `test_m8_side_effects.py` is a **counter with a positive control**, the milestone's only non-parity instrument. `unshimmed()` unchanged at **25**; `scripts/check.sh` green with `Ran 1547 tests … OK (skipped=6)`; `cargo test -p template-core` 682 |
| M9 | Interpose `base`, `smartif` | ✅ **the milestone was the highest-risk one, and the risk turned out bounded rather than survived by luck.** `_impl/base.py` lands as a transcription (`2eedab2`), then delegates one body at a time: `Template.compile_nodelist` to the Rust parse (`fdb4eb0`), the render-time annotation protocol (`6c7b231`), `Variable._resolve_lookup` (`d0a1e3a`); `_impl/smartif.py` (`b34b2b0`) is the second module. `unshimmed()` **25 → 23**. **The three seams, measured on the closed tree (`6ff127d`) rather than argued from §5.5** — parse **1,170 tests / 4,360 `compile_nodelist` calls**; render **961 tests / 9,600 `NodeList.render` calls**, with the annotation protocol inside it at **84 render-time annotations across 41 tests**; resolve **10,180 `_native.resolve` calls**, out of 23,263 `_native` calls from 1,176 of the 1,547 tests — one wrapper over one population, so `resolve` is one of the 17 entry points and a *share* of that same total rather than a second count, and the census re-run at the close-out reproduces all three figures exactly (instruments: `m9_measure_render_blast_radius.py`, `m9_measure_native_calls.py`). **The render figure is 961 / 9,600, not the recon's 964 / 9,630, and the correction is the milestone's own lesson about metrics**: recon claimed the render count was interposition-independent and it is not — all 30 missing calls, across 8 tests, were Django's `verbatim` tag rendering the block *at parse time* (`defaulttags.py:1607`, `return VerbatimNode(nodelist.render(Context()))`), which Rust's `verbatim` no longer does; `.review/m9-recon.md` §1b carries the call chains, and the row that proves the reading rather than agreeing with it is `test_verbatim_tag04`, whose body renders nothing and only asserts an error, so every call it ever had was parse-time. The seam also survives where the parse is still Python's — but the precise statement is *inside a tag whose Rust compile function is still a stub*, which names what changes and when: a `{% verbatim %}` block inside a `{% partialdef %}` body reaches Django's `do_verbatim` because `partialdef` is one of the two names in `builtin_tag_stub_names()`, and it stops doing so when M13 supplies that compile function. **The row is not vacuous under the rule above** (it names `test_base`, `test_parser`, `test_nodelist` including `ErrorIndexTest`, and `test_smartif`, all four of which this milestone flips onto our modules) **but it owes the second question an answer, and finding 3 is why**: a *verbatim* transcription of `base.py` also keeps all 1,547 green, so interposition is invisible to every count this project accepts. M9's answer is four instruments instead of one — `scripts/check_transcriptions.py`, where each transcribed file declares its differing hunks and the guard asserts the diff equals the declaration, with the **compared path measured** (28 of the 66 functions `base.py` defines, spanning 116–1181, nearly the whole file) rather than inferred from the one target a row names; `tests/test_interposition.py` for the seam rows (internal imports interposed, the parse path, the annotation protocol, the compared-path pin re-measured); `tests/test_base_parity.py` for `render_value_in_context` and the annotation protocol, pinned by *behaviour* rather than text; and `scripts/differential.py`, which now counts `_native` calls and fails if the shim run reaches none. `unshimmed()` at **23**; `scripts/check.sh` green with the suite at `Ran 1547 tests … OK (skipped=6)`, the unit suite at **315**, and `cargo test -p template-core` at **682**. **Two things M9 proved and did not implement, stated rather than implied**: §5.5's interior-subtree rendering is **deferred** (the Python node tree remains the render path, as §5.5 argues it must) and `render_value_in_context`'s equivalence — measured against Django's own over the whole render blast radius, and pinned by rows — is a *pinned Python function*, not yet a Rust one on the suite's path. **And the registry's two names are renamed**: `builtin_tag_names()` → `builtin_tag_names_including_stubs()` and `builtin_tag_stubs()` → `builtin_tag_stub_names()`, with the subtraction written at each call site, because the ambiguity had produced a wrong answer three times |
| M10 | Interpose `context`, `engine`, `utils`, `loaders`, `origin` | `test_context`, `test_engine`, `test_loaders`, `test_origin`; project-side `FileSystemLoader` case-sensitivity test (§2) |
| M11 | Interpose `defaulttags`, `defaultfilters`, `loader_tags` | bulk of `syntax_tests` and `filter_tests`, **and `test_url`** — moved here from M12 by the M5 plan's Scope section (measured: `syntax_tests/test_url.py` uses only `{% url %}`, `{% autoescape %}` and no `{% load %}`, and imports `URLNode` from `django.template.defaulttags`, so it becomes real signal when this milestone interposes that module) |
| M12 | `i18n`, `l10n`, `static`, `tz`, `cache` libraries | `syntax_tests/i18n/*`, `test_cache` |
| M13 | Partials, `library`, `backends`, `response` | `test_partials`, `test_response` |
| M14 | `unshimmed()` empty under strict mode | full suite green with no fallback |
| M15 | Hardening | §2 met with margin |

Each milestone ends with its stated check green and one commit.

**The vacuity rule, and every gate above is read through it.** A gate that names
a `.django/tests/` module is only a gate for the milestone that *interposes* the
behaviour it exercises. Until then the module runs on stock Django and passes
whether or not the milestone shipped — so the row is evidence about the shim's
schedule, not about the engine. The mechanical question, asked once per row:

> **Is the module whose behaviour this gate exercises interposed by this
> milestone?** If not, the gate is vacuous and the milestone owes a gate of its
> own, pointing at our engine.

M7 is the worked example and the reason the rule is stated rather than assumed:
`test_include_cache` is named in the spec as an M7 gate, and at M7 it is vacuous,
because `loader_tags` interposition is M11's. The obligation is discharged in
`tests/`, against our engine, by copying the row's *shape*.

**Scope, and it is a limit rather than a caveat.** This rule decides one thing:
whether a gate names a module a *later* milestone interposes. A row naming no
`.django` module is **out of scope** — the rule does not pronounce on it, and
"not vacuous" is not a verdict it is entitled to return. The second question, the
one this rule cannot answer, is whether the gate's *instrument* can see the
defect it is for: a parity corpus comparing our engine with stock Django is blind
to a defect both sides share, because they call the same helpers. M8's gate was
the first row of that kind — escaping and the object protocol are exactly where
both engines call `conditional_escape`, `SafeData` and `Promise` — so its row
read out of scope here and owed that second question an answer of its own. **M8
discharged it with a non-parity row**, because the property is not in the output
at all: `tests/test_m8_side_effects.py` counts the call and carries a positive
control, and the M8 row above records why a parity corpus could not have seen it.

This is the question a reader at the next milestone should be able to answer
without re-reading a plan, and it is why the rule lives here beside the table
rather than in prose about M7.

**M9 is called out because it is the one milestone where the suite can go from
green to comprehensively red in a single interposition**, and where a failure is
ambiguous between "the engine is wrong" and "the seam is wrong". Both M8 and M9
should be run in the rust-native differential mode (§9) *before* interposing, so
that the suite's verdict at M9 is about integration alone.

### 10.4 Obligations to carry into later milestones

Recorded here while they are cheap, so they are not rediscovered at M9:

- **Fan-out structure for M3/M4 — one file per tag and per filter.** The human
  asked for the tag and filter work to be parallelised once the general
  foundation is in place. The foundation is M2 **plus M3**: 14 of the 27 tags and
  every filter sit behind `FilterExpression`, so there is nothing to fan out
  over until M3 lands. Before it does, the layout must change, because today all
  nine M2 tags live in the single file `parser/tags.rs` and parallel agents
  editing one file clobber each other. M3 therefore delivers
  `parser/tags/<name>.rs` and `filters/<name>.rs`, each module owning exactly
  one tag or filter, behind a registry written **once, up front, in full** so
  that no fan-out agent ever edits a shared file.
  The fan-out contract, because build state is shared too: an agent owns one
  module and writes no other file, runs `cargo test -p template-core` and
  **never** `maturin develop`, and never commits. There is one `.so`, one
  `target/`, and one git index, and concurrent writers race on all three — so
  the extension is rebuilt and the commit made centrally, after the agents'
  files land.
  The contract is not "an agent cannot build": `template-core` has exactly one
  dependency (`regex`) and **no PyO3**, so `cargo test -p template-core` builds
  and runs in a bare shell — no `.so`, no `maturin develop`, no venv, no
  `.django`. That is what keeps the project's standing discipline intact under
  fan-out: a test is not known to work until it has been seen to fail, and an
  agent that can run `cargo test` can see its own test fail. The crate must be
  named with `-p template-core` and never `--workspace`, which pulls in
  `template-py` and with it PyO3. Cargo's `target/` file lock then serialises
  concurrent invocations for free, so no build lock is needed.

- **"The registry, written once in full" is scoped to the `django/template/`
  set — 32 node classes — not to every node class forever.** Measured on the
  pinned tree: 32 in `django/template/*.py` (2 `base`, 23 `defaulttags`, 4
  `library`, 3 `loader_tags`), 15 more in `django/templatetags/*.py` (8 `i18n`,
  3 `tz`, 2 `static`, 1 `cache`, 1 `l10n`), 47 in total. The 15 belong to M12's
  libraries and are not in scope for M3–M7; a promise that read as "all node
  classes" would be a promise this milestone split cannot keep.
  Do not measure this set with `Node.__subclasses__()`: that walks *import
  state*, and it moves as modules load (6 with only `base` imported, 42 after
  `django.setup()`, then 45/46/47 as the templatetag modules follow). The 32
  above is that walk's answer *once the five modules are imported*, and a source
  scan gives a different answer again depending on what it counts: 32 is the
  transitive closure, while a scan for a direct `class X(Node)` base finds
  **29** — the difference being `InclusionNode`, `SimpleNode` and
  `SimpleBlockNode`, which extend another node class rather than `Node`. Quote a
  number from this bullet only with its definition attached; this is the
  fourth time in this project that two right answers to two unstated questions
  have collided.
  What matters more than the count: of the 47, only **12** are observable to
  Django's own suite — the ones `tests/template_tests` imports by name
  (`BlockNode`, `BlockTranslateNode`, `ExtendsNode`, `ForNode`, `IfNode`,
  `IncludeNode`, `LocalizeNode`, `StaticNode`, `TextNode`, `URLNode`,
  `VariableNode`, `WithNode`). A rename of any of those fails at import, loudly,
  before an assertion runs; the other 35 are Django-internal and no test
  observes them. So a standing assertion over the inventory would carry a
  deferred list that rots in order to guard classes that are mostly not
  contract.
  Instead: **re-run the per-module file scan as a diff when the Django pin
  moves**, as a pin-bump checklist item. That catches the case that can actually
  change — upstream adding or renaming a class — at the moment it can change. A
  stale deferred list reads as coverage and is worse than no list.
  And assert the closed obligation instead — every `class_name` the Rust emitter
  can produce resolves. That is what `tests/test_materialise.py` pins.

- **`py_repr_str` is exact for ASCII only — enforced, and owed a fix at M5 and
  M7.** Python's `repr` escapes a character when `Py_UNICODE_ISPRINTABLE` is
  false, which covers every non-ASCII category-C code point plus `Zl`, `Zp` and
  `Zs`; `char::is_control()` is **Cc only**. Sweeping every BMP code point for
  "Python escapes it and a `Cc` test does not" gives **7,893** of them — 43
  `Cf`, 1,432 `Cn`, 6,400 `Co`, 1 `Zl`, 1 `Zp` and 16 `Zs` — with the 2,048
  surrogates excluded because a Rust `char` cannot hold one. (A cruder sweep
  reads 7,895; the two extras are `U+0022` and `U+0027`, category `Po`, which
  diverge over *quoting* rather than printability and which this function
  handles correctly. Name the predicate or the number is not falsifiable.)
  `is_ascii()` is the boundary because the function is exact for **all** ASCII,
  verified by diffing it against `repr()` across all 128 code points — `\x0b`
  and `\x7f` included — so a narrower assertion would reject inputs it gets
  right.
  Both M2 call sites are ASCII *by construction*, which is why M2 is correct
  rather than lucky: `{% resetcycle %}`'s `args[0]` is the tag name dispatch
  matched against the registry, and `{% templatetag %}`'s list is a constant.
  The day either sees user text the function is wrong, and that day is dated:
  **M5** (`defaulttags.py:1683`, `{% with %}`'s `remaining_bits[0]`) and **M7**
  (`loader_tags.py:356`, `{% include %}`'s `option`). Measured on the pinned
  tree, a template whose `{% include %}` option is `U+00AD` followed by
  `option` raises `Unknown argument for 'include' tag: '\xadoption'.`, and
  `{% with a=1 foo<U+00AD>bar %}` raises `'with' received an invalid token:
  'foo\xadbar'` — Django escapes the `Cf`, a `Cc`-only escape emits it raw.
  **M5 and M7 route both messages through the host's `repr`** rather than
  widening this function. A `debug_assert!` in it now fails the test suite the
  moment either is wired up, so that is a choice someone makes rather than a
  default nobody noticed.

- **Rust's `\w` is not Python's `\w`, and `filter_re` is built on it.** Python's
  `filter_re` uses `(?P<var>[\w\.\+-]+)` and `(?P<filter_name>\w+)`
  (`base.py:703-719`), and for `str` patterns Python's `\w` is exactly
  `isalnum() or '_'`. Rust's `regex` `\w` is
  `[\p{Alphabetic}\p{M}\p{Nd}\p{Pc}\p{Join_Control}]`, a different set: measured
  over the whole code space, Rust matches **2,642** code points Python does not
  (Mn 2,020, Mc 468, So 130, Me 13, Pc 9, Cf 2) and misses **915** that Python
  matches (all `No`). The exact replacement is **`[\p{L}\p{N}_]`**, verified
  byte-for-byte identical to Python's set — 142,940 code points, equal over
  U+0000..U+10FFFF. This is M1's whitespace-table trap in a second place, and it
  is user-visible in *both* directions against the pinned tree: `{{ ² }}`
  renders `''` on Django (U+00B2 is `No`, a Python word character) while
  `{{ á }}` written as `a`+U+0301 **raises** `Could not parse the remainder:
  '́' from 'á'` (U+0301 is `Mn`, a Rust word character but not a Python one).
  So M3 uses the explicit class with an exhaustive guard test, as `pyunicode`
  does for whitespace. Note which spelling the two traps share: an implicit
  escape class was correct in neither case, and the fix in both is to name the
  set rather than trust the engine's idea of it.

- **Whether the descriptor wire form needs container values is an M3 decision,
  taken before fan-out, not now.** `AttrValue` carries `Str`/`SafeStr`/`Bool`/
  `Int`/`None`/`Ref`, and the milestone claim that the format is general enough
  for the tags that arrive later has no evidence behind it for lists or dicts.
  Later milestones do need them — measured against the pinned tree,
  `ForNode.loopvars` is a list of strings, `CycleNode.cyclevars` a list,
  `WithNode.extra_context` a dict — but a plain `AttrValue::List` would **not**
  be enough, which is the reason to defer rather than to guess. There are three
  independent reasons, and they are why the guess is not worth making:
  `IfNode.conditions_nodelists` is a list of `(condition, nodelist)` tuples
  (`defaulttags.py:995-1020`), so one element of each tuple is a *nodelist*,
  which lives in `NodeDesc.nodelists` rather than in an attribute value;
  `IfNode` does **not** override `child_nodelists`, so it inherits
  `("nodelist",)` while its `nodelist` is a *computed property* —
  `return NodeList(self)`, flattening every branch through `__iter__` — which
  means the materialised `IfNode` must hold real Python `NodeList` objects
  *inside the tuples* for `get_nodes_by_type` to reach its children at all; and
  the other half of the tuple is a `smartif` AST node rather than a
  `FilterExpression`, so what it looks like on the wire is M3's output.
  §10.3 puts `if` in **M5**, alongside `for`, `with` and `cycle`, so the
  container case and the `IfNode` case land in the *same* milestone: a shape
  guessed now would be reopened at M5, not merely "later". This was a review
  finding, and it strengthened the case for deferral rather than weakening it.
  So M3 decides it with `FilterExpression` (the `Ref` producer) and the M5 node
  shapes both in hand, and adds the variant, its `attr_value_to_py` arm and its
  `_decode_value` arm in one commit. The shape to evaluate at M3 is
  `AttrValue::NodeListRef(usize)` — an index into the `NodeDesc`'s existing
  `nodelists`, which resolves because the materialiser already builds children
  before parents — giving `List[Tuple[Ref|None, NodeListRef(i)]]`.

- **M3 gate: a variable ahead of `{% extends %}` — corrected at M3, and the
  `{% extends %}` half moved to M7.** The gate read *"M3 adds that case to the
  corpus explicitly."* This entry keeps its original reading below and then
  records what measuring it changed, because a gate narrowed silently is worse
  than one narrowed with its evidence attached.

  The original reading: **at M2**, every source in the corpus and all 400
  randomised templates were variable-free outside `{% verbatim %}` and
  `{% comment %}`, because `{{ … }}` needs M3's `FilterExpression`. So
  `contains_nontext` and `must_be_first` — the ordering rule the whole
  `extend_nodelist` contract exists for (§6) — looked exercised only on
  text-versus-tag. M3 adds that case to the corpus explicitly.

  Measured against the pinned tree at **6.0.9**, with `Origin("t.html",
  "t.html", None)` supplied to the parser:

  | source | outcome |
  | --- | --- |
  | `{{ x }}{% extends "a" %}` | `TemplateSyntaxError: {% extends "a" %} must be the first tag in 't.html'.` |
  | `{% csrf_token %}{% extends "a" %}` | the same error |
  | `text{% extends "a" %}` | OK — `['TextNode', 'ExtendsNode']` |
  | `{% extends "a" %}{{ x }}` | OK — `['ExtendsNode']` |
  | `{{ x }}{% include "a" %}` | OK — `['VariableNode', 'IncludeNode']` |

  Three things follow. The rule's input is **any non-`TextNode`**, not a
  variable specifically: `contains_nontext` is set only when the node is not a
  `TextNode` (`base.py:611`), so `text{% extends %}` parses, and
  `{% csrf_token %}{% extends %}` fires. The M2 corpus's text-versus-tag
  coverage was therefore never testing the ordering rule *at all* — it was
  testing that the rule does not fire. And what M2 actually lacked was not a
  variable but a `must_be_first` **tag**, of which the template package has
  exactly one (`loader_tags.py:87`): `{% extends %}`, which §10.3 puts in M7.

  **Rust cannot produce the ordering error for `{{ x }}{% extends %}` at M3,
  and both ways to make it would be worse than the gap.** Django compiles the
  tag *first* and checks the ordering *after* (`base.py:564-569`), so
  `{{ x }}{% extends %}` — no argument — raises `'extends' takes one argument`
  rather than the ordering error, and an ordering check placed ahead of dispatch
  would be wrong for that input, which any user can write. The other route,
  implementing `{% extends %}`'s compile function early, puts a loader tag in
  Rust, which §6 assigns to M7.

  Two further measurements, because the plan text this correction came from had
  neither: the argument message is `'extends' takes one argument` (**not** "at
  least one"), and **every** `{% extends %}` that compiles needs an origin — a
  bare `Parser(...).parse()` raises `AttributeError: 'NoneType' object has no
  attribute 'template_name'` from `ExtendsNode.__init__`, not only on the
  ordering path. A corpus entry for this therefore needs the harness to pass an
  `Origin`, and `origin.template_name`'s repr has to agree with Rust's
  `origin_template_repr` for the comparison to mean anything.

  So M3 delivers the half it can hold — **variable-versus-text and
  variable-versus-tag `contains_nontext`**, the part that was unexercised — and
  the `{% extends %}`-specific case moves to M7. The rule's *machinery* is
  already Rust's and already tested: `extend_nodelist`'s
  `must_be_first && contains_nontext` check (`parser/mod.rs:502`) is exercised
  against a stand-in `must_be_first` node (`parser/tags/mod.rs:198`). M7
  supplies a tag, not a rule.

  **What M7 owed, stated exactly, because the loose version invites a reader at
  M7 to think the rule arrives untested:** Django's `{% extends %}` compile
  function, its message text `"{% %s %} must be the first tag in %s."`, and the
  corpus entry that reaches it — which additionally needs the harness to pass an
  `Origin` whose `template_name` agrees with the parser's. It did **not** owe the
  refusal path: `extend_nodelist`'s check ships at M3 and is exercised, by a
  stand-in, on both the refusing and the permitting branch.

  **Three corrections to how that obligation was stated, and the first changes
  what crosses the seam:**

  - **The second slot holds the *raw* `Origin.template_name`, not a repr.**
    `{% extends %}` compares it against a path and interpolates it with plain
    `%s` in three messages (`loader_tags.py:246-288`), so a repr is wrong in all
    three. The field is `origin_template_name` and the repr is derived at the one
    place a message needs it, through the host's `repr` seam. The paragraph above
    said "the origin's repr in the second slot", which is the shape that was
    wrong.
  - **`must_be_first` crosses the seam *inbound*, and that is an M6 defect M7
    inherited rather than an M7 feature.** Rust has the field and the 5-tuple
    descriptor wire does not carry it — it is read off the *Python node the host
    returned*, before `node.unbind()`. So the refusal can fire for a
    host-compiled node; the outbound 5-tuple is untouched. The recon's §2 framed
    it on the outbound wire, which would have required an arity change to a shape
    `tests/test_ref_table.py` pins.
  - **The gate is `tests/test_inheritance_gate.py`, not the `.django/` row** —
    §10.3's vacuity rule, worked through in the M7 row above.

  **M7 also removed a rule rather than adding one.** `ShellHost.has_tag` had an
  exception list of three names (`block`, `extends`, `include`) *in front of* its
  identity clause, and the list was wrong in the direction that does not fail
  loudly: consulted first, it made a `{% load %}`ed library that registers
  `block` lose — the parser's `block` was the loaded callable, and `has_tag`
  still answered "Rust's". Widening the clause to read `loader_tags`' table as
  well as `defaulttags`' makes for all three the same test every other built-in
  gets, and the two names Rust still does not own (`load`, `querystring`) are
  answered by the registry's *absence* one clause earlier — by no list. Measured:
  the registry's 25 names and the names `has_tag` declines are the same set.

- **M6 gate: a library that redefines a built-in tag name.** Host-first
  dispatch order is argued from last-registration-wins semantics rather than
  measured — and **the mechanism is not on `Library`**: there is no
  `Library.add_tag` and no `Library.add_library`; a registration updates one
  dict each, and the merge into the parser's tables is `Parser.add_library`
  (`base.py:669-670`, called once per builtin library from `Parser.__init__` at
  `base.py:523`). §6's own text gets this right — "consumed via `add_library`
  and **not stored**" — and this sentence contradicted it. M6 pins the behaviour
  with a template that loads a library registering a name already in Django's
  builtins, and asserts the user's definition is the one that runs.

  **Two ownership decisions are M6's, and they are recorded here rather than
  only in the plan.** `load` and `querystring` are **host-owned** — a library is
  a Python module, and `querystring` is the one built-in registered
  `takes_context=True` — so Rust compiles neither. Measured: `builtin_tag_names_including_stubs()`
  is 22, `builtin_tag_stub_names()` is `['partial', 'partialdef']`. And `block`,
  `extends` and `include` are **excluded from the host** at M6. They are Django's
  builtins but not Rust's, and `NodeDesc.must_be_first` does not cross the seam
  (`desc.rs:407`, defaulted `false` at `desc.rs:445`), so a host-compiled
  `{% extends %}` would be accepted **and** uncheckable — its must-be-first rule
  silently absent.
  Accepting a tag whose ordering rule does not exist here is worse than today's
  honest refusal. **So `{% extends %}` still raises `Invalid block tag` at M6,
  and that is a divergence from Django, not coverage of it.** M7 removes the
  exclusion, carries the `bool` across the seam, and gets
  `parser.__loaded_blocks` for free, since the handle *is* Django's own `Parser`.

- **`MAX_NESTING_DEPTH` diverges in the direction that matters, and only the
  differential corpus can pin it.** Django's bound is not a value to match: it
  is `sys.getrecursionlimit()` times about two frames per prefix operator times
  the enclosing call depth. Measured against the pinned tree — 200, 400 and 498
  `not`s parse, 600 raises `RecursionError`, limit 1000 — so a reader who moves
  the limit moves Django and not this engine. Rust must guard *something*
  (disabling the guard aborts the process with a native stack overflow at 4000
  `not`s, observed), and 1000 sits a factor of two to four clear of that in a
  debug build. The consequence to carry: **this engine accepts 600–1000 nested
  `not`s that stock Django rejects**, which is the wrong direction for a
  drop-in — a template authored here can fail on Django. No suite test reaches
  either side. The half that *could* agree is the exception type (`RecursionError`
  against `IfError`'s class); it is unchanged at M3 deliberately, because a type
  change nothing pins would trade a documented divergence for an undocumented
  one. §9's differential corpus owns both the type and the band. M4's filter
  work does not touch this.

  **The eventual fix is known, small and bounded, and is recorded here so the
  corpus work does not re-derive it.** The band splits in two. **Above the
  recursion limit** (measured: 600 and up) both engines refuse and *only the
  exception type differs*, so changing the type makes that band agree exactly —
  one change, complete for that band. **Between about 600 and 1000** the two
  engines genuinely differ in behaviour, not merely in type: this engine parses
  where Django raises. No type change helps there and no guard value fixes it,
  because Django's bound is a function of `sys.getrecursionlimit()` and the
  ambient call depth rather than a constant. So the corpus should expect
  agreement above the limit once the type is aligned, and expect a *documented*
  divergence in the middle band — two different assertions, which is why the
  band is written down before the corpus is written rather than discovered
  while writing it.

- **§5.5's render path** — `NodeList.render`/`Node.render_annotated` kept
  verbatim, shadow nodes renderable; M9 gate.
- **§5.2's `annotate_exception`** — needed for Rust-owned culprits; verified by
  `test_nodelist.ErrorIndexTest`, whose Rust-owned `{% for %}` wraps a Python tag.
- **§5.2's `ParserHandle`** — interior mutability, Python-dict `extra_data`,
  arbitrary attributes, and the `(NodeListRef, ParserHandle)` parse return so the
  shell can assign `self.extra_data` inside its own `try`; M6, and
  `test_partials.py:171` tests the failure path.
- **§5.2's `FilterFlags`** — `needs_autoescape` changes the filter's *signature*;
  M4.
- **§5.5's materialisation lifetime** — once per compile, identity-stable;
  M7 via `test_include_cache`.
- **The `debug`-flag lexer decision** (§9's `DebugLexer`) — the Rust-side merge
  of `Lexer`/`DebugLexer` must preserve `token.lineno` and
  `token.position` exactly, since `template_debug` reports both.
- **§2's case-sensitivity coverage hole** — project-side test at M10.
- **§6's `extend_nodelist` contract** — `token`/`origin` attached at parse time
  for every node `render_annotated` can reach (not unconditionally; §6 records
  why `BlockNode.render`'s render-time construction is exempt), plus
  `contains_nontext`/`must_be_first`; M2, but its consequence lands at M9 via
  `render_annotated`. `origin` has no class-level fallback, so a miss there is an
  `AttributeError` raised *inside* the exception handler rather than a graceful
  degradation — and `BlockNode.render`'s exemption is specific to that one node,
  not licence to skip the pair elsewhere.
- **§6's ownership correction** — `block`/`extends`/`include` render-time logic is
  Python, ported verbatim; Rust supplies the compiled structure. Affects M7's and
  M11's sizing, and §6's estimate of the Python side.
- **Materialisation must yield real Python instances, not opaque handles**
  (found while surveying M2's design). §5.5 describes shadow nodes as objects
  with a Python `render`, but the requirement is stronger than that: Python
  render-time code in `loader_tags` *isinstance-tests* the materialised tree.
  `ExtendsNode.render:147-156` walks `compiled_parent.nodelist` and branches on
  `isinstance(node, TextNode)` then `isinstance(node, ExtendsNode)`, and calls
  `compiled_parent.nodelist.get_nodes_by_type(BlockNode)` to build the block
  mapping. So the materialised top-level nodelist must contain genuine
  instances of the shell's `TextNode`, `ExtendsNode` and `BlockNode` — a single
  generic node class carrying a Rust handle would fail here while passing §5.5's
  stated tests. Nothing in `tests/template_tests/` isinstance-tests a node, so
  the suite would not catch it directly; the failure surfaces in inheritance
  rendering. M2 owns the descriptor; M7 is where it bites.
  (`base.py:611`'s `isinstance(node, TextNode)` is the same test in
  `extend_nodelist`, which §6 makes Rust's, so that one is a Rust-side check.)
- **`{% verbatim %}` renders at *parse* time** (found surveying M2). Its compile
  function is `VerbatimNode(nodelist.render(Context()))` — the only built-in tag
  whose compilation performs a render. It is benign rather than a hole in §6:
  inside verbatim the lexer emits everything as TEXT, and
  `TextNode.render_annotated` (`base.py:1113`) returns `self.s` without
  annotation, so the result is exactly `SafeString("".join(text contents))`. Rust
  can compute that directly, but it must produce a **safe** string: `VerbatimNode.render`
  returns `self.content` unchanged, so losing the safeness flag would start
  escaping verbatim output. M5 (the tag) and M8 (`SafeData`).
- **`TextNode.render_annotated` overrides the base implementation**
  (`base.py:1113-1120`) and returns `self.s` — text nodes are never annotated.
  This keeps §5.5's per-node boundary cheap for the commonest node type, but it
  also means an exception whose innermost culprit is a TextNode cannot arise, so
  `ErrorIndexTest`-style `(start, end)` pairs never point at text.
- **`Lexer.verbatim` is a public attribute, not private state** (found during M1
  close-out). Django's lexer holds `verbatim` as `False` or a `str` such as
  `"endverbatim foo"`, and `Lexer.__repr__` renders it, so it is observable
  without tokenizing — `test_parser.py:49` asserts
  `'<Lexer template_string="{% for i in 1 %}{{ a...", verbatim=False>'` on a
  freshly constructed lexer. The Rust side models the same state as
  `Option<&str>` (a test-facing accessor, `None` ≡ `False`), so the shell must
  translate `None` → `False` and reproduce the 20-character `template_string`
  truncation in `__repr__`. M9, via the shell's `Lexer`.
  The merged Rust model is sufficient for the rest: `DebugLexer`'s only public
  member beyond `Lexer` is its own `tokenize`; `_tag_re_split` and
  `_tag_re_split_positions` are private and have no caller anywhere in the
  tree, so they need not be modelled.

- **A seam method whose only caller is still a stub is unverified code with a
  verified doc comment, and the two can disagree indefinitely.** Measured:
  `PyFilterHost::json_dumps` passed `separators=(",", ":")` and `ensure_ascii`
  where `django.utils.html.json_script` (`html.py:103`) passes neither, and
  omitted `cls=DjangoJSONEncoder` — so `json_script({"a": 1})` would have come
  back `'{"a":1}'` where Django answers `'{"a": 1}'`, and a `date` inside the
  value would have raised `TypeError` where Django serialises it. It survived
  from Task 2 to Task 9 because `json_script` was a stub and no other body calls
  the method: no unit test can reach it, `cargo test` never ran it, and the
  milestone's own corpus could not name it. **What found it was writing the
  brief for the task that would consume it** — reading the source the seam method
  claims to wrap, rather than the seam method's claim about the source. The rule
  this gives: when a task's brief names a host method, re-derive that method
  against the pinned source before the task starts, because the corpus that would
  otherwise catch it is written from the same claim.

- **Every exception this engine synthesises names Django's own function, and a
  Django release can rename one.** `Error::filter_host`'s message is a
  transcription of what the pinned tree raises, so it is only as stable as the
  Python name inside it. The measured case is `base.py:813`'s collision, whose
  message is `<callable>() got multiple values for argument 'autoescape'` and
  whose `<callable>` is `linebreaks_filter` for the registered `linebreaks`: a
  release could change that name without changing any behaviour, because it is
  the `def`'s own name and not the registry key. The exposure is the same one
  `ljust`'s and `center`'s `OverflowError` texts and `cut`'s `str.replace`
  `TypeError` carry, and no diff of upstream source guards it. What guards it is
  the assertion on the message: `tests/test_filter_parity.py`'s
  `test_every_error_case` calls both engines live and compares `str(caught)`, so
  a rename fails on the Django side of the row before our side is consulted.
  Re-run the message-bearing `ERROR_CASES` rows — the five collision rows
  (`unordered_list`, `linebreaks`, `linebreaksbr`, `linenumbers`, `urlize`) and
  every other row that pins a text — whenever the Django pin moves: a
  synthesised message is correct for one pin at a time.
  **And say which kind of pin-bump item each one is, because the checklist mixes
  two that are not run the same way.** An item that **fails loudly on a bump** —
  this row, the parity corpus, the inventory test, `py_repr_str`'s ASCII diff —
  is watched by the gate and needs no one to remember it; an item that **needs
  manual re-measurement** — §10.4's per-module class scan, the Unicode tables the
  generators write — compares generated or scanned data and stays green until
  someone re-runs it, so its trigger has to be written down. A checklist where a
  reader cannot tell the two apart reads as coverage and is worse than none, for
  the reason the class-scan bullet above gives about stale deferred lists.

- **The 1547-test acceptance gate cannot see a single filter body, so "the suite
  is green" is not evidence that a filter works.** Measured at M4: the gate's only
  call into the Rust filter layer is `_native.parse`'s `check_filter`, which reads
  the registry for a name and an arity and nothing else, and
  `_materialise_filter_expression` rebuilds every filter as Django's own registry
  function — so the Rust body is not on the render path the suite exercises. A
  body can be a stub, or wrong in every row, and the suite still reports `OK
  (skipped=6)`. This is the §2 criterion working as designed rather than a defect
  in it: the gate is *"the suite is unchanged"*, and an unchanged suite is exactly
  what a filter body that never runs produces.
  **What separates them is the corpus** — `tests/test_filter_parity.py` driving
  `_native.filter` with Django's own filter on the other side — which makes the
  corpus a gate in its own right for every milestone that lands a filter or a tag
  body, not a supporting test. M4's review found the shape this hides: a seam
  method whose only caller is still a stub is unverified code behind a verified
  doc comment (the `json_dumps` bullet above), and the corpus cannot name a
  method whose filter has no rows yet.
  **The standing obligation: rows land with the body, in the same task or the
  same wave, for every filter and every tag from here on.** A body landed without
  its rows is not "done, pending tests" — it is unverified in a way no other gate
  in this project can turn into signal, and the milestone that lands it is the
  only one that can. `STILL_STUBBED` in the filter registry and the stub count in
  `tests/test_filter_parity.py` are the mechanical half: they name what is left,
  and the count is meant to be edited deliberately as bodies land.

- **What M9 leaves behind, and to whom.** M9 closed with two things *proved but
  not implemented*, and four carried forward. Recorded here because each one is
  invisible from the code it will be needed in.

  - **§5.5's interior-subtree rendering is deferred, deliberately.** The Python
    node tree remains the render path: M9's `NodeList.render` and
    `Node.render_annotated` are transcriptions that delegate nothing, and the
    suite's 9,600 render calls go through them in Python. A later milestone that
    moves rendering into Rust inherits the hard half §5.5 names — a subtree's
    nodes must be materialised, rendered and annotated as a unit — and inherits
    it **without** M9's safety net, because by then the transcription will have
    been replaced rather than extended. This is the one place where the
    milestone *chose* not to go further, and the choice is on the record rather
    than in a commit message.
  - **`render_value_in_context`'s equivalence is a result, not an
    implementation.** Measured equivalent to Django's own across the render
    blast radius and pinned by `tests/test_base_parity.py`, so a future hand-edit
    or partial port fails there — but the function the suite runs is still
    Python's. "Equivalent and pinned" is weaker than "ours", and the difference
    is exactly what this project keeps finding in its own counts.
  - **The resolver seam has one comparison point where it could have two.** The
    handler (`base.py:1014-1020`, the `bit`-and-`exc_info` logging) lives in the
    transcription, so the parity harnesses compare `_native.resolve` against
    *our* `Variable._resolve_lookup`. Moving the handler verbatim into the shell
    would give an arm-level row calling `_native.resolve` directly plus a
    behaviour row at `Variable.resolve` — two points instead of one — at the cost
    of retargeting five harnesses (`tests/differential/cases.py`,
    `tests/test_render_parity.py`, `tests/test_resolve_invalid.py`,
    `tests/test_resolve_callarm.py`, `tests/test_m8_side_effects.py`).
    `tests/parity_target.py` does **not** forbid it: it guards the *target*,
    which stays `pinned_resolve_lookup()`. Not paid at M9 because new teeth would
    have been asserted rather than measured inside a commit whose job was a
    correction.
  - **A moved *handler* is still invisible to the transcription guard, and the
    guard now says so instead of pretending otherwise.** It sees a moved *line*;
    a body replaced by a call is one hunk whatever moved with it. The achievable
    half shipped in M9 — the shape-change clause, where the guard compares the
    two spans it already holds and fails a region whose upstream text is many
    lines and whose ours is a call, unless the declaration carries a note. It
    does not say *what* moved; it says something did.
  - **The verbatim-at-parse-time cost survives wherever the parse is still
    Python's, and one such place is M13's.** Django's `{% verbatim %}` renders its
    block through `NodeList.render` *at compile time*
    (`defaulttags.py:1607`) — which is also why the render seam measures 9,600
    rather than 9,630 (`.review/m9-recon.md` §1b). A verbatim block inside a
    `{% partialdef %}` body still takes that path on the closed tree, because the
    host drives that parse; M13 owns `partialdef` and inherits the behaviour,
    which is Django's on both sides and therefore not a defect to fix.
  - **`install()` now refuses to interpose over a capture, and every later
    milestone inherits the refusal.** `_shim.install()` fails with a `ShimError`
    naming the stale binding when a `django.template*` or `django.templatetags*`
    module already imported a name from a module the substitution replaces
    (`6ff127d`), because a captured class makes every `isinstance` false and a
    captured enum member every comparison; and `uninstall()` is a no-op when the
    finder is already out, since the swap would otherwise file Django's modules
    under the *ours* cache and report 24 correct bindings as stale. Both were
    found by measurement inside M9's own unit suite, and both are load-bearing
    for M10–M14: install before importing `django.template`, or start a fresh
    interpreter.

## 11. Review workflow

The human has delegated review authority to `rusty-reviewer` and will not review
until the project completes. Every design artifact, plan, and code change is
reviewed before acceptance. Findings are recorded as `.review/round-N.md`;
responses as `.review/round-N-response.md`.

A change is not committed until its review round returns `APPROVE` or all
`blocker`/`major` findings are resolved.

**The unit of review is the milestone, not the commit.** A milestone's task
commits land on the branch as they are written; the milestone close-out carries
the review round, and *that* commit is the one the rule above holds. The literal
reading — a round per commit — is not workable: fifteen milestones of roughly
eight tasks each is a hundred review rounds for one reviewer session, which puts
the review rate rather than the work on the critical path.

**A section a plan marks as reviewable before code exists gates its
implementing tasks.** M2's "Design decision 1" is the pattern — it says outright
that it is stated "so the reviewer can reject it before any code exists". Tasks
implementing such a section wait for the round, even though the rule above
otherwise holds only the milestone close-out. This is the one place a commit
genuinely must precede a review round's existence.

**Every snippet in a plan is executed against the pinned tree before the plan is
committed, and a plan is not reviewable until it is.** This is a rule because
the opposite was measured three tasks in a row: M2's Task 5, Task 6 (four
verified defects) and Task 7 (three) each contained snippets that could not have
run — a bare `Parser` that registers no builtins, `split_whitespace` where the
plan's own test required Python's separator set, an import of a package whose
`__init__` cannot execute under that name, a tag count that was 59 written as
"60-odd". None was a design error; every one was a snippet written from
recollection rather than run. The reviewer found the *design* problems in those
same tasks and would have had to find these too, which spends the scarce
resource (review) on the cheap one (measurement).

The rule is operational, not aspirational: before a plan is committed, the
author runs each snippet's commands and pastes the real output into the plan's
"Expected" lines. Where a snippet cannot be run in isolation (it depends on code
a later task writes), the fragment that *can* be checked — the citation, the
count, the API signature — is checked against `.django/` or the crate and the
result recorded beside it. "Verified against the tree" in a plan is a claim with
evidence attached, not a formality.

**Known environment deviation:** `rusty-reviewer` runs as a peer Claude session
(`django-rust-templates-a8`), not as a subagent of this session. A
`.claude/agents/rusty-reviewer.md` definition was written in M0 but is not
loadable mid-session. Reviews are therefore dispatched by writing the artifact to
disk and handing the peer session its path. This is a wiring detail, not a
reduction in review coverage.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Exact fidelity across the FFI — token positions, error strings, reprs | Port strings verbatim from Django source; pin as fixtures; differential harness from M0 |
| **M9 flips all 1547 tests onto our render path at once** (§5.5, §10.2) | Decide the render path explicitly (§5.5: Django's `NodeList.render` kept verbatim, shadow nodes renderable); run M8/M9 in rust-native differential mode *before* interposing, so the suite's verdict is about integration alone; treat M9 as its own milestone with budget, not a one-line step |
| Shadow tree drifting from Rust's compiled form | Structural-equality test per milestone; the tree is the introspection surface, but it is **also** the render entry point (§5.5), so drift is a correctness bug, not a cosmetic one |
| Shadow tree re-materialised, resetting node state | Materialisation once per `Template` compile with identity-stable `NodeRef`; `test_include_cache` as an M7 gate (§5.5) |
| Python exceptions flattened into Rust errors, losing identity | `Error::Host` keeps the host exception by identity; `annotate_exception` channel (§5.2) |
| Re-entrant parse from Python callbacks (`simple_block_tag`) | `ParserHandle` with interior mutability and an explicit contract; never hold `&mut Parser` across a host call (§5.2) |
| `python -S`, an unbuilt extension, or a wrong `PYTHONPATH` yielding a green suite that proves nothing | `sitecustomize` exits loudly; suite pre-flight asserts interposition (§7) |
| A partially-implemented subpackage silently cutting off its Django siblings | Finder rebuilds the fallback search path at every level (§7) |
| `django.test` renders templates framework-wide | Shim must cover framework-level use, not just `template_tests` |
| Python 3.14 deferred-annotation path in `test_parser.py` | Live on this machine; will be exercised, not skipped |
| Meta-path finder interacting badly with Django's import machinery | Differential harness surfaces this in M0, before any engine work depends on it |
| Long tail of edge cases in 1501 tests | Milestone granularity with per-module green gates |
| `FileSystemLoader` case-sensitivity unexercised by the suite on any platform we run (§2) | Project-side test at M10, outside Django's tree |

## 13. Open questions

None blocking. The vendoring policy (§6) and milestone order (§10) were decided
by the implementing session under delegated authority and are open to reviewer
challenge.

Round 1 left two questions that are answered here rather than deferred, because
the answer changes work already scheduled:

- **The shadow tree is not introspection-only** (§5.5). It was originally
  justified as a read surface, with Rust rendering from its own tree. Django's
  `setup_test_environment()` makes it the render entry point for the entire
  suite, so the decision is now explicit: Django's `render`/`render_annotated`
  stay verbatim, shadow nodes delegate to Rust per subtree.
- **The Python side is materially larger than §6 first implied** (§5.2, §6).
  Measured at M9, before further commitments are made against the estimate.

Reviewer findings are recorded as `.review/round-N.md` and answered in
`.review/round-N-response.md`; the response is the authority on how each finding
was resolved, and this spec is amended to match it rather than the reverse.
