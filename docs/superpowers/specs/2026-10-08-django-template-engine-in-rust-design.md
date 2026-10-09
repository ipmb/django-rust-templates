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

- The `django`-backend portion of Django 6.0.9's `tests/template_tests/` passes
  against the Rust implementation, with **zero** skipped, `xfail`ed, or edited
  tests. Target: ~1501 test methods across 122 files.
- `jinja2/` is excluded: it is a single non-test fixture file (`using.html`),
  referenced only by two `@require_jinja2` tests that auto-skip when Jinja2 is
  not installed. This is an exclusion, not a skip — we do not install Jinja2.
- Django's own source is not modified. The pinned checkout stays pristine.

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

The only place Python is modelled, and it lives in `template-core` as traits:

```rust
/// Resolve a variable path against a value. Fast paths for primitives;
/// delegates to the host for anything opaque.
pub trait ValueResolver {
    fn resolve(&self, value: &Value, path: &str) -> Result<Value, VariableDoesNotExist>;
    fn force_promise(&self, value: &Value) -> Value;      // lazy / Promise
    fn is_safe(&self, value: &Value) -> bool;             // SafeData
    fn is_callable(&self, value: &Value) -> bool;
    fn call(&self, value: &Value, args: &[Value]) -> Result<Value, Error>;
}

/// Parse-time and render-time callbacks into user Python code.
pub trait ExtensionHost {
    fn call_tag(&self, name: &str, parser: &mut Parser, token: &Token)
        -> Result<NodeRef, Error>;
    fn render_foreign_node(&self, node: NodeRef, ctx: &mut Context) -> Result<String, Error>;
    fn call_filter(&self, name: &str, value: Value, arg: Option<Value>)
        -> Result<Value, Error>;
}
```

`NodeRef` is an opaque handle to a Python-owned `Node` returned by a user tag
callback. `template-core` never dereferences it — it stores it, renders it via
the host, and asks the host for its identity when building the shadow tree.

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

Rust renders from its own compiled form. The two trees are never required to
agree at *render* time, only structurally — this is sound because **zero tests in
the suite call `node.render(context)` directly**. Every render in the suite enters
through `Template.render()` or `Engine.render_to_string()`.

A structural-equality test per milestone guards against drift.

## 6. Ownership split

**Rust owns:** tokenization and positions; `Parser` and tag dispatch;
`FilterExpression`/`Variable` compilation; the smart-if grammar; variable
resolution and auto-calling; `conditional_escape`; all built-in tags and filters;
block/extends/include resolution.

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

## 7. Substitution mechanism

A `MetaPathFinder` intercepts the `django.template` namespace when
`DJANGO_RUST_TEMPLATES=1` is set, installed before Django's test runner starts.

A meta-path finder rather than `sys.modules` assignment, because Django's own
internals — `django.test`, `django.template.backends.django`,
`django.template.loader` — import submodules by path, and dict assignment does
not intercept those.

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

1. **Rust unit tests** (`cargo test`) — engine semantics in isolation. Fast.
2. **Differential harness** — render a corpus of templates through both stock
   Django and our shim, diff the output. Built in M0. Gives a fast signal long
   before the Django suite is green, and pinpoints regressions to a template
   rather than a test file.
3. **The Django suite** — the acceptance criterion. Run in full at every
   milestone boundary; per-module during development.

## 10. Milestone sequence

Ordered to maximise green tests early: ~58 of 122 files are filter tests.

| | Component | Unlocks |
|---|---|---|
| M0 | Bootstrap: toolchain, workspace, maturin, venv, pinned Django, shim skeleton, runner scripts, differential harness, reviewer wiring. Prove `{{ 1\|add:2 }}` end-to-end. | — |
| M1 | Lexer, `Token`, `DebugLexer` | `test_base` |
| M2 | `Parser`, `Node`/`NodeList`, `TextNode`, shadow tree | `test_parser`, `test_nodelist` |
| M3 | `FilterExpression`, `Variable` | `test_parser` |
| M4 | All 57 filters | 58 `filter_tests/*` |
| M5 | smart-if, `{% if %}`, `{% ifchanged %}` | `test_smartif`, `test_if` |
| M6 | `for`, `with`, `firstof`, `cycle`, `regroup`, `spaceless`, `autoescape`, `verbatim`, `comment`, `now`, `lorem`, `widthratio`, `templatetag`, `filter`, `debug`, `resetcycle` | most of `syntax_tests` |
| M7 | `Library`, custom tags/filters, `simple_tag`, `inclusion_tag`, callbacks | `test_library`, `test_custom` |
| M8 | `{% block %}`, `{% extends %}`, `{% include %}` | `test_extends`, `test_include` |
| M9 | `Engine`, loaders, `Origin`, autoreload | `test_engine`, `test_loaders`, `test_origin`, `test_autoreloader` |
| M10 | `Context`, context processors | `test_context` |
| M11 | Escaping: `SafeData`, `Promise`, `alters_data`, `do_not_call_in_templates`, logging | `test_callables`, `test_logging`, lazy filter cases |
| M12 | `i18n`/`l10n`/`static`/`tz`/`cache` tags | `syntax_tests/i18n/*`, `test_cache`, `test_url` |
| M13 | Partials | `test_partials` |
| M14 | Backends, response, framework integration | `test_response`, `tests.py` |
| M15 | Full-suite hardening | all |

Each milestone ends with its target modules green and one commit.

## 11. Review workflow

The human has delegated review authority to `rusty-reviewer` and will not review
until the project completes. Every design artifact, plan, and code change is
reviewed before acceptance. Findings are recorded as `.review/round-N.md`;
responses as `.review/round-N-response.md`.

A change is not committed until its review round returns `APPROVE` or all
`blocker`/`major` findings are resolved.

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
| Shadow tree drifting from Rust's tree | Structural-equality test per milestone; tree is introspection-only |
| `django.test` renders templates framework-wide | Shim must cover framework-level use, not just `template_tests` |
| Python 3.14 deferred-annotation path in `test_parser.py` | Live on this machine; will be exercised, not skipped |
| Meta-path finder interacting badly with Django's import machinery | Differential harness surfaces this in M0, before any engine work depends on it |
| Long tail of edge cases in 1501 tests | Milestone granularity with per-module green gates |

## 13. Open questions

None blocking. The vendoring policy (§6) and milestone order (§10) were decided
by the implementing session under delegated authority and are open to reviewer
challenge.
