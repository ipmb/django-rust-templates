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

### 5.2 The seam — two traits

The only place Python is modelled, and it lives in `template-core` as traits.
Every signature here exists because the naive version was measured wrong; the
reasoning is recorded with each, because these are the shapes most likely to be
"simplified" back into breakage.

```rust
/// Resolve a variable against a value. Fast paths for primitives; delegates to
/// the host for anything opaque.
pub trait ValueResolver {
    /// **One path component, not a whole dotted path.**
    ///
    /// Django's `_resolve_lookup` (`base.py:948-1000`) is a per-bit loop whose
    /// control flow is Python-exception-driven: it tries `current[bit]`, then
    /// `getattr`, re-raising when a `@property` raised rather than swallowing
    /// it, with `isinstance(current, BaseContext)` special cases that suppress
    /// class attributes. It then auto-calls results unless
    /// `do_not_call_in_templates` or `alters_data`.
    ///
    /// If the host owns that loop, Python owns `VariableDoesNotExist` and its
    /// message, contradicting §6. So Rust keeps the loop *over bits* and
    /// constructs the error; the host owns the cascade *within* a bit, because
    /// that is Python exception semantics and cannot be modelled otherwise.
    fn resolve_bit(&self, current: &Value, bit: &str)
        -> Result<Value, LookupError>;

    fn force_promise(&self, value: &Value) -> Value;      // lazy / Promise
    fn is_safe(&self, value: &Value) -> bool;             // SafeData
    fn is_callable(&self, value: &Value) -> bool;
    fn call(&self, value: &Value, args: &[Value]) -> Result<Value, Error>;
}

/// Why a single path component failed to resolve.
pub enum LookupError {
    /// Every strategy failed. The `repr` of the intermediate object comes from
    /// the host (it is Python), but the *message* is built by Rust, so
    /// `"Failed lookup for key [%s] in %r"` with `(bit, current)` lives in one
    /// place — the same place as the rest of the error strings §6 makes Rust's.
    Missing { bit: String, current_repr: String },
    /// A `@property` raised. Django re-raises rather than converting this into a
    /// failed lookup, so the original exception must survive to the caller.
    Reraise(Error),
}

/// Per-filter attributes Django reads when calling a filter
/// (`base.py:805-820`). Not inferable from the name, and each changes the call
/// or its result: `expects_localtime` runs the output through `localtime`;
/// `needs_autoescape` changes the *signature* (the filter receives an
/// `autoescape=` kwarg); `is_safe` changes output escaping when the input
/// `isinstance(obj, SafeData)`. `library.py:85` propagates all three, so they
/// have to cross the seam.
pub struct FilterFlags {
    pub is_safe: bool,
    pub needs_autoescape: bool,
    pub expects_localtime: bool,
}

/// Parse-time and render-time callbacks into user Python code.
pub trait ExtensionHost {
    /// Takes a `ParserHandle`, **not** `&mut Parser` — see below.
    fn call_tag(&self, name: &str, parser: ParserHandle, token: &Token)
        -> Result<NodeRef, Error>;

    fn render_foreign_node(&self, node: NodeRef, ctx: &mut Context)
        -> Result<String, Error>;

    fn filter_flags(&self, name: &str) -> FilterFlags;

    fn call_filter(&self, name: &str, value: Value, arg: Option<Value>,
                   flags: &FilterFlags, autoescape: bool)
        -> Result<Value, Error>;

    /// Django's `Node.render_annotated` protocol (`base.py:1044-1070`), which
    /// mutates the *Python* exception object and reads from the *Python* node.
    ///
    /// On any render-time exception with `engine.debug`, Django attaches
    /// `_culprit_node` — innermost node wins — and then attaches
    /// `template_debug` only at the frame whose
    /// `context.render_context.template.origin` matches the culprit's. That is
    /// stateful across nested renders, and it is keyed on the exception's
    /// identity, so it cannot be reconstructed after the fact.
    ///
    /// Rust-owned nodes have no Python frame of their own, so Rust calls this
    /// for them; Python-owned nodes get it from Django's own unmodified
    /// `render_annotated` (see §5.5). `test_nodelist.ErrorIndexTest` asserts
    /// exact `(start, end)` pairs for a Python tag nested in a Rust-owned
    /// `{% for %}`, which is why both halves are needed.
    fn annotate_exception(&self, err: &Error, node: NodeRef, token: &Token);
}
```

`Error` keeps host exceptions **by identity**:

```rust
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

`NodeRef` is an opaque handle to a Python-owned `Node` returned by a user tag
callback. `template-core` never dereferences it — it stores it, renders it via
the host, and asks the host for its identity when building the shadow tree.

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
- **Arbitrary attributes must work.** `loader_tags.do_block:230-233` sets
  `parser.__loaded_blocks` on the parser (a module-level function, so no name
  mangling). The handle proxies unknown attributes to a host-side bag.
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
resolution and auto-calling; `conditional_escape`; all built-in tags and filters;
**compiling** the inheritance structure — which nodes are blocks, the `blocks`
mapping, `get_nodes_by_type` results, and the `must_be_first` ordering rule.

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
reach** — not for every node unconditionally. Django draws that line itself:
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

**Transcribed files are marked as such** in a `# TRANSCRIBED FROM django/...`
header naming the upstream path, so they are diffed against upstream on every
Django bump rather than reviewed as original work. A transcription reviewed as
if it were original is how a bump silently changes behaviour.

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
| M2 | Rust parser, node AST, `_native.parse` | Parse tree descriptor for every built-in tag; syntax-error parity |
| M3 | `FilterExpression`, `Variable`, `smartif`, primitive resolver | First `_native.render`; text, variables, dotted/index lookup, filters |
| M4 | All 57 built-in filters | Filter corpus parity, incl. `FilterFlags` round-tripping |
| M5 | Remaining built-in tags (`for`, `if`, `with`, `cycle`, …) | Syntax corpus parity |
| M6 | `Library`, extension host callbacks | Python-defined tags/filters work through the engine, incl. re-entrant `ParserHandle.parse` |
| M7 | Inheritance: `block`, `extends`, `include` | Inheritance corpus parity; **`test_include_cache` passes**, proving the shadow tree is materialised once and node identity is stable (§5.5) |
| M8 | Escaping and the Python object protocol | `SafeData`, `Promise`, callables, `alters_data`, `do_not_call_in_templates`; resolver complete |
| M9 | Interpose `base`, `smartif` | ⚠️ **highest-risk milestone.** All 1547 tests flip onto our `NodeList.render`/`Node.render_annotated` at once (§5.5). Done when `test_base`, `test_parser`, `test_nodelist` (incl. `ErrorIndexTest`), `test_smartif` are real signal **and** the full suite is unchanged from §2 — budget accordingly, this is not a one-line step |
| M10 | Interpose `context`, `engine`, `utils`, `loaders`, `origin` | `test_context`, `test_engine`, `test_loaders`, `test_origin`; project-side `FileSystemLoader` case-sensitivity test (§2) |
| M11 | Interpose `defaulttags`, `defaultfilters`, `loader_tags` | bulk of `syntax_tests` and `filter_tests` |
| M12 | `i18n`, `l10n`, `static`, `tz`, `cache` libraries | `syntax_tests/i18n/*`, `test_cache`, `test_url` |
| M13 | Partials, `library`, `backends`, `response` | `test_partials`, `test_response` |
| M14 | `unshimmed()` empty under strict mode | full suite green with no fallback |
| M15 | Hardening | §2 met with margin |

Each milestone ends with its stated check green and one commit.

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
  module and writes no other file, and does not run `maturin develop` or commit.
  There is one `.so`, one `target/`, and one git index, and concurrent writers
  race on all three — so the extension is rebuilt and the commit made centrally,
  after the agents' files land.

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
