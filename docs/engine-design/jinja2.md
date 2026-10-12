# Jinja2 — Architectural Design

## 1. Overview

Jinja2 3.1.6, read at `.bench-deps/jinja2/`, is a template engine in pure Python; its only accelerated component is MarkupSafe's C escaping routine. The central stance: a template is translated once into Python *source text*, which CPython compiles to a code object, and rendering is calling a generator function in that module. Name binding, escaping mode, constant folding and filter lookup are settled during code generation (`.bench-deps/jinja2/compiler.py:300`).

## 2. Compilation Pipeline

| Stage | Entry point | Produces |
|---|---|---|
| Preprocess | `Environment.preprocess` (`.bench-deps/jinja2/environment.py:645`) | source, after each extension's hook |
| Tokenize | `Lexer.tokeniter`/`wrap` (`.bench-deps/jinja2/lexer.py:669`, `:615`) | `(lineno, type, value)`, then `Token`s |
| Filter tokens | extensions' `filter_stream` (`.bench-deps/jinja2/environment.py:674`) | a `TokenStream` |
| Parse | `Parser.parse` → `subparse` (`.bench-deps/jinja2/parser.py:1045`, `:996`) | `nodes.Template` AST |
| Optimize | `Optimizer` (`.bench-deps/jinja2/optimizer.py:27`) | same AST, constants folded |
| Generate | `generate()` → `visit_Template` (`.bench-deps/jinja2/compiler.py:101`, `:829`) | Python **source text** |
| Compile | `Environment._compile` → builtin `compile()` (`.bench-deps/jinja2/environment.py:703`) | a `CodeType` |
| Instantiate | `Template.from_code` → `exec()` (`.bench-deps/jinja2/environment.py:1217`) | a `Template` |

The lexer is a regex state machine over a state-keyed rule table, with a brace-balancing stack so `}}` inside a dict literal does not close the tag; lexers are cached per delimiter set (`.bench-deps/jinja2/lexer.py:526`, `:790`, `:428`). The optimizer calls `as_const()` on every `Expr` and substitutes a `Const` on success (`.bench-deps/jinja2/optimizer.py:38`); filters and tests fold only when they take no context and are not async (`.bench-deps/jinja2/nodes.py:750`).

Code generation is a `NodeVisitor` writing text into a `StringIO` with manual indentation (`.bench-deps/jinja2/compiler.py:453`). Each template becomes a module with a `root` generator, one `block_<name>` generator per block, a `blocks` dict and a `debug_info` string. Under autoescape:

```python
def root(context, missing=missing, environment=environment):
    resolve = context.resolve_or_missing
    undefined = environment.undefined
    if 0: yield None
    l_0_items = resolve('items')
    t_1 = environment.filters['upper']        # inside try/except KeyError
    yield '<h1>Report</h1>\n'
    for l_1_item in (undefined(name='items') if l_0_items is missing else l_0_items):
        _loop_vars = {}
        yield '<li>'
        yield escape(t_1(environment.getattr(l_1_item, 'name')))
        yield '</li>'
    l_1_item = missing
```

The preamble comes from `write_commons` (`:723`); `if 0: yield None` forces generator-ness; filter lookups are hoisted to locals by `pull_dependencies` (`:538`); adjacent static text merges into single literals in `visit_Output` (`:1503`).

## 3. Runtime Representation

A compiled template is a Python module namespace. `Template._from_namespace` takes `root` as `root_render_func` and `blocks` as a dict of generator functions, and stores `__jinja_template__` in the module globals for traceback rewriting (`.bench-deps/jinja2/environment.py:1248`).

Per render: build a `Context`, call the root generator, join yielded strings with `environment.concat`, which is `"".join` (`:1290`, `:286`). No AST walk, no per-node dispatch.

Because `root` is a generator, `generate()` streams it directly and `stream()` wraps that in a buffering `TemplateStream` (`:1326`, `:1576`). Constructs needing a value — macros, `{% filter %}`, `{% set %}` blocks — switch the frame to a list buffer and `append` instead of `yield` (`.bench-deps/jinja2/compiler.py:422`). Async is a separate codegen mode, not a runtime flag: `choose_async` emits `async def`/`async for` and wraps every access, call, filter and test in `await auto_await(...)` (`:608`, `:1753`).

## 4. Language Model

Expressions are a Python-expression subset, parsed by a precedence cascade from `parse_condexpr` down through `or`, `and`, `not`, comparison, `+ -`, `~`, `* / %`, `**`, unary, primary, postfix (`.bench-deps/jinja2/parser.py:512`–`:641`).

| Capability | Status |
|---|---|
| Literals: int, float, str, tuple, list, dict, `true/false/none` | yes (`.bench-deps/jinja2/parser.py:643`) |
| Arithmetic, chained comparison, boolean, `in`/`not in` | yes (`:560`) |
| Bit operators, lambdas, comprehensions, walrus | absent from the operator table (`.bench-deps/jinja2/lexer.py:114`) |
| Calls with `*args`/`**kwargs`, slicing | yes (`.bench-deps/jinja2/compiler.py:481`, `:1764`) |
| Conditional expression | yes; missing `else` yields `Undefined` (`:1866`) |
| Assignment | `{% set x = e %}`, `{% set %}…{% endset %}` with optional filter (`.bench-deps/jinja2/parser.py:225`) |
| Tuple unpacking | in `set`, `for`, `with` targets (`:685`) |
| Mutating an outer scope | only via `namespace()` and `ns.attr = v` (`.bench-deps/jinja2/compiler.py:1662`) |
| Control flow | `if`/`elif`/`else`, `for`/`else`, `for … if`, `recursive`, `with` (`.bench-deps/jinja2/parser.py:20`) |
| `break`/`continue`, `{% do %}` | only with the `loopcontrols` / `do` extensions (`.bench-deps/jinja2/ext.py:603`, `:590`) |

The built-in statement keyword set is fixed and small (`.bench-deps/jinja2/parser.py:20`); every other tag is an extension.

## 5. Variable Resolution and Data Model

Name resolution is a compile-time problem. `idtracking.Symbols` assigns each name a Python local `l_<level>_<name>` plus one of four load instructions (`.bench-deps/jinja2/idtracking.py:56`, `:9`), emitted at frame entry (`.bench-deps/jinja2/compiler.py:584`): `param` (nothing — it is an argument), `resolve` (`l_0_x = resolve('x')`), `alias` (`l_1_x = l_0_x`), `undefined` (`l_1_x = missing`). A variable is read from the `Context` at most once per frame entry. Reads emit a guard, `(undefined(name='x') if l_0_x is missing else l_0_x)`, skipped for known-bound parameters (`:1655`).

`Environment.getattr`/`getitem` differ only in order (`.bench-deps/jinja2/environment.py:485`, `:466`): `a.b` tries `getattr` then `a['b']`; `a['b']` tries the subscript then `getattr`. Both return `environment.undefined(obj=…, name=…)` instead of raising. Callables are never auto-invoked.

Undefined is pluggable: the default prints as `""`, has length 0, iterates empty, and raises `UndefinedError` otherwise (`.bench-deps/jinja2/runtime.py:796`); variants are `ChainableUndefined` (`:970`), `DebugUndefined` (`:1007`), `StrictUndefined` (`:1039`) and `make_logging_undefined` (`:912`).

## 6. Context and Scoping

`Context` holds `parent` (globals plus render arguments, read-only), `vars` (top-level assignments written by generated code), `blocks` (name → list of block functions), `eval_ctx` and `exported_vars`; lookup is `vars` then `parent`, returning a `missing` sentinel (`.bench-deps/jinja2/runtime.py:145`, `:229`).

Scoping is decided by the compiler's `Frame`, which pairs a `Symbols` table with flags — `toplevel`, `rootlevel`, `loop_frame`, `block_frame`, `soft_frame`, `buffer`, `require_output_check` (`.bench-deps/jinja2/compiler.py:165`). `inner()` creates a child symbol level; `soft()` serves `if` bodies, which share the parent's locals. Branch merging is explicit: `branch_update` unions the arms' stores and demotes any name set in only some arms to a `resolve` or `alias` load (`.bench-deps/jinja2/idtracking.py:123`).

- **Loop body** — child frame; locals clear to `missing` on exit so assignments do not escape, but are mirrored into `_loop_vars` for `pass_context` callables (`.bench-deps/jinja2/compiler.py:600`, `:800`).
- **Block** — a separate top-level function whose frame is deliberately *not* a child of the template frame (`:925`), so it resolves from the context; `scoped` blocks get a derived context carrying enclosing locals (`:964`).
- **Macro** — a nested function closing over its defining frame (`:614`).
- **Top level** — assignments go to a local, to `context.vars`, and (unless underscore-prefixed) to `context.exported_vars` (`:784`).

Only `derived()` contexts, `{% include with context %}` and `scoped` blocks allocate contexts at runtime.

## 7. Template Composition

`{% extends %}` is runtime-resolved but compile-time-specialized. Generated code sets `parent_template = environment.get_template(...)`, appends the parent's blocks onto `context.blocks`, and ends `root` with `yield from parent_template.root_render_func(context)` (`.bench-deps/jinja2/compiler.py:1025`, `:903`); the parent is an expression, so inheritance is dynamic. A root-level `extends` sets `has_known_extends` and the compiler stops emitting top-level output (`:1036`, `:1507`); a conditional one wraps each output in `if parent_template is None:` (`:1505`).

Blocks are flat module-level functions registered in `blocks` (`:946`); `context.blocks[name]` is a list — child first, ancestors appended — so `super()` is a `BlockReference` indexing it (`.bench-deps/jinja2/runtime.py:344`, `:186`). `{% block x required %}` emits a runtime length check (`.bench-deps/jinja2/compiler.py:969`).

`{% include %}` yields the included stream inline; `with context` builds a new context from `context.get_all()` plus frame locals, otherwise it reuses the included template's cached rendered `_body_stream` (`:1074`, `:1095`). `{% import %}` binds a `TemplateModule` (`.bench-deps/jinja2/environment.py:1517`); `{% from … import %}` substitutes a positional-message `Undefined` for missing exports (`.bench-deps/jinja2/compiler.py:1126`). Macros compile to a buffered inner function wrapped in `runtime.Macro`, which binds arguments and injects `caller`/`varargs`/`kwargs` at call time (`.bench-deps/jinja2/runtime.py:662`); `{% call %}` compiles its body to an anonymous macro named `caller` and forwards it as a keyword (`.bench-deps/jinja2/compiler.py:1351`).

## 8. Escaping and Safety

`EvalContext.autoescape` is computed once per template from `environment.autoescape`, a bool or a callable taking the template name (`.bench-deps/jinja2/nodes.py:76`). `visit_Output` then emits a literal `escape(...)` or `str(...)`, falling back to `(escape if context.eval_ctx.autoescape else str)(...)` only when the eval context is *volatile* (`.bench-deps/jinja2/compiler.py:1476`), which a non-constant `{% autoescape %}` causes (`:1975`). Constant output is escaped during code generation and baked in as a string literal (`:1455`).

Safety is MarkupSafe's contract: `Markup` is a `str` subclass whose operators re-escape operands, and `escape()` honours `__html__` (`.bench-deps/markupsafe/__init__.py:84`, `:24`); the inner replace loop is a C extension with a Python fallback (`:7`).

`finalize` is a per-environment hook over every output expression. `_make_finalize` builds it once, splitting it into a compile-time function for constants and the source fragment `environment.finalize(` for runtime nodes (`.bench-deps/jinja2/compiler.py:1400`); a context-taking `finalize` disables output folding (`:1521`).

The sandbox is an `Environment` subclass overriding `getattr`/`getitem` to consult `is_safe_attribute` (underscore and internal attributes rejected) and `call` to consult `is_safe_callable` (`.bench-deps/jinja2/sandbox.py:177`, `:248`, `:257`, `:388`). `environment.sandboxed` is read during code generation, so it changes emitted code: calls become `environment.call(context, …)`, intercepted operators `environment.call_binop(...)` (`.bench-deps/jinja2/compiler.py:1887`, `:64`). `ImmutableSandboxedEnvironment` also blocks mutating container methods, and `str.format` is wrapped so its field access is sandboxed too (`.bench-deps/jinja2/sandbox.py:404`, `:344`).

## 9. Extensibility

| Point | Mechanism | Can / cannot |
|---|---|---|
| Filters | `environment.filters` (`.bench-deps/jinja2/environment.py:351`) | any callable; `pass_context`/`pass_eval_context`/`pass_environment` inject state (`.bench-deps/jinja2/utils.py:38`). Must exist at compile time, except inside `if`/conditional expressions where the check defers to runtime (`.bench-deps/jinja2/compiler.py:538`, `:1811`) |
| Tests | `environment.tests` (`:352`) | same rules; no block body |
| Globals | `environment.globals` (`:353`) | join the context `parent`; per-template override via `globals=` |
| Policies | `environment.policies` (`.bench-deps/jinja2/defaults.py:39`) | tunes built-in filters (`urlize.rel`, `truncate.leeway`, …); cannot add behaviour |
| Extensions | `Extension` subclass (`.bench-deps/jinja2/ext.py:55`) | hooks: `preprocess`, `filter_stream`, `parse` for declared `tags`, AST injection via `attr`/`call_method`. Cannot define new node classes (`NodeType.__new__` raises, `.bench-deps/jinja2/nodes.py:1200`) or add codegen visitors without subclassing `CodeGenerator` |
| Loaders | `BaseLoader.get_source` (`.bench-deps/jinja2/loaders.py:75`) | returns `(source, filename, uptodate)`; `load()` already handles caching and compilation (`:108`) |
| Bytecode caches | `load_bytecode`/`dump_bytecode` (`.bench-deps/jinja2/bccache.py:132`) | marshalled code keyed by source SHA-1 (`:163`); filesystem, memcached and module variants ship |
| `Environment` subclass | `code_generator_class`, `context_class`, `concat`, `_compile`, `_generate`, `join_path`, `handle_exception` (`.bench-deps/jinja2/environment.py:284`) | replaces whole stages |
| `NativeEnvironment` | swaps `code_generator_class` and `concat` (`.bench-deps/jinja2/nativetypes.py:88`) | drops the `str()` around output and `literal_eval`s the join, so a template can return an `int` or list |

## 10. Error Handling and Debugging

Compile-time errors are `TemplateSyntaxError` from lexer and parser (`.bench-deps/jinja2/lexer.py:866`, `.bench-deps/jinja2/parser.py:76`) and `TemplateAssertionError` from the code generator for statically-wrong-but-parseable input — unknown filter, duplicate block, `extends` outside top level, assigning to `loop` (`.bench-deps/jinja2/compiler.py:380`, `:856`, `:1003`, `:1246`).

Runtime errors are ordinary Python exceptions, funnelled into `environment.handle_exception()` → `debug.rewrite_traceback_stack` (`.bench-deps/jinja2/environment.py:936`, `.bench-deps/jinja2/debug.py:14`). That walks the traceback and, for each frame whose globals hold `__jinja_template__`, synthesizes a replacement by `compile`-ing `"\n" * (lineno - 1) + "raise __jinja_exception__"` against the template filename and `exec`-ing it (`:105`). Locals are rebuilt from the context plus the highest-depth `l_<n>_<name>` values (`:150`), the code name becomes `top-level template code` or `block 'x'`, and `@internalcode` frames are dropped (`:51`).

Line mapping comes from `debug_info`, a `template_line=code_line` string accumulated as the generator emits newlines (`.bench-deps/jinja2/compiler.py:474`, `:948`) and searched in reverse by `get_corresponding_lineno` (`.bench-deps/jinja2/environment.py:1482`). It is per-statement, not per-expression.

## 11. Performance Characteristics

**Per render.** Build a `Context` (one dict merge, `.bench-deps/jinja2/runtime.py:93`), call a generator, `"".join` the output. The hot loop is CPython running a normal function — local loads and `yield` of string literals; per-node dispatch was paid once at compile time.

**Per output.** A dynamic `{{ }}` costs one `escape()`/`str()` call plus a `missing` test. An `a.b` costs an `environment.getattr` call — a Python function with try/except, the main residual per-access overhead. A filter is a call through a hoisted local, not a dict lookup. The root yields many small strings joined by `"".join` (`.bench-deps/jinja2/environment.py:286`), and adjacent static text merges at compile time (`.bench-deps/jinja2/compiler.py:1539`), so static-heavy templates yield few large pieces.

**Two cache layers.** `Environment.cache` is an LRU of `Template` objects keyed by `(loader_ref, name)`, default 400, revalidated via each loader's `uptodate` callable when `auto_reload` is set (`.bench-deps/jinja2/environment.py:957`, `:314`). `BytecodeCache` persists marshalled code across processes keyed by source SHA-1 (`.bench-deps/jinja2/bccache.py:167`), skipping lex/parse/codegen but not `exec`; `ModuleLoader` imports precompiled modules instead (`.bench-deps/jinja2/loaders.py:620`). Compilation — regex lexing, AST, optimizer pass, string-building codegen, then CPython's own compiler — is why both layers and `compile_templates` exist (`.bench-deps/jinja2/environment.py:818`).

**Structural slow paths.** Async wraps every access, call, filter and test in `await auto_await(...)` — a coroutine per operation even for plain values (`.bench-deps/jinja2/compiler.py:1753`). A volatile eval context moves escaping to runtime and disables folding (`:50`). `loop.length` on a generator materializes the iterable (`.bench-deps/jinja2/runtime.py:434`). `{% include with context %}` re-renders per include where a plain include reuses a cached body (`.bench-deps/jinja2/compiler.py:1095`). Sandboxing routes every call and intercepted operator through Python-level dispatch (`:1887`).

## 12. Design Decisions

| Decision | Rationale | Consequences (+) | Consequences (−) |
|---|---|---|---|
| Compile to Python **source text**, not bytecode or an IR (`.bench-deps/jinja2/compiler.py:1`) | Reuse CPython's compiler | Render speed near hand-written Python; readable output | Slow compilation; `exec` required |
| Names resolved to numbered Python locals at compile time (`.bench-deps/jinja2/idtracking.py:56`) | Avoid a dict lookup per reference | One context lookup per name per frame | Needs a symbol pass and explicit branch merging (`:123`) |
| Root render function is a generator (`.bench-deps/jinja2/compiler.py:874`) | Streaming without a second code path | `render`, `generate`, `stream` share one body | Value-producing constructs need list buffers (`:389`) — two output modes |
| Autoescaping decided at compile time (`.bench-deps/jinja2/nodes.py:76`) | Emit `escape(x)` instead of a runtime check | No per-output branch; constants pre-escaped | Dynamic `{% autoescape %}` goes volatile and disables folding (`.bench-deps/jinja2/compiler.py:1975`) |
| Safety carried by MarkupSafe's `Markup` str subclass (`.bench-deps/markupsafe/__init__.py:84`) | Safety travels with the value | Filters and user objects interoperate via `__html__`; C escaping | Every path must preserve the subclass; `Markup` ops cost more |
| `getattr`/`getitem` fall back to each other (`.bench-deps/jinja2/environment.py:466`) | One syntax for objects, dicts, sequences | Authors need not know the container type | Two attempts and exception handling on misses; a Python call per `a.b` |
| Missing values become `Undefined`, not errors (`.bench-deps/jinja2/runtime.py:796`) | Templates should not abort on a missing key | Partial data renders; strictness is opt-in | Typos fail silently; errors surface far from the cause |
| Blocks are top-level functions with non-nested frames (`.bench-deps/jinja2/compiler.py:925`) | Blocks are replaceable, so cannot close over one parent | Uniform inheritance; `super()` is a list index | No enclosing locals unless `scoped` |
| Inheritance resolved at runtime, specialized at compile time (`:1000`) | `extends` takes an arbitrary expression | Dynamic parents; root-level `extends` skips output emission | Conditional `extends` guards every top-level output (`:1505`) |
| Constant folding is one scope-unaware pass (`.bench-deps/jinja2/optimizer.py:4`) | Stated: the AST carries no scope data | Cheap and safe | No loop unrolling, dead-branch removal or propagation |
| Async is a separate codegen mode (`.bench-deps/jinja2/compiler.py:608`) | Avoid a sync/async branch per operation | Each mode's code is direct | One environment cannot serve both; `await auto_await` per operation |
| Two cache layers: `Template` LRU and source-hashed bytecode cache (`.bench-deps/jinja2/environment.py:957`, `.bench-deps/jinja2/bccache.py:163`) | Compilation dominates cost | Steady state never recompiles; cold start can skip codegen | Invalidation relies on loader `uptodate`; marshal is version-specific |
| Tracebacks rewritten via synthesized frames (`.bench-deps/jinja2/debug.py:76`) | Generated line numbers differ from template lines | Traces point at template files with template-level locals | `compile`+`exec` per frame; per-statement mapping loses columns |

## 13. Deliberate Non-Goals

- **Arbitrary Python in templates** — no bit operators in the lexer table, no statement grammar beyond a fixed keyword set (`.bench-deps/jinja2/lexer.py:114`, `.bench-deps/jinja2/parser.py:20`).
- **Security by default** — the base `Environment` allows unrestricted attribute access and calls; sandboxing is an opt-in subclass (`.bench-deps/jinja2/sandbox.py:177`).
- **Custom AST node types** — `NodeType.__new__` is replaced with a raising function (`.bench-deps/jinja2/nodes.py:1200`).
- **Inner scopes mutating outer ones** — loop and block frames clear locals on exit (`.bench-deps/jinja2/compiler.py:600`); `namespace()` exists because the normal path forbids it (`.bench-deps/jinja2/utils.py:745`).
- **Deep optimization** — loop unrolling is stated out of reach without a scope-aware second tree (`.bench-deps/jinja2/optimizer.py:4`).
- **Mixed sync/async per environment** — `is_async` is read during code generation (`.bench-deps/jinja2/compiler.py:608`).
- **Running without `exec`** — templates are code objects `exec`'d into a namespace (`.bench-deps/jinja2/environment.py:1228`).
