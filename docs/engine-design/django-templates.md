# Django Templates — Architectural Design

## 1. Overview

The Django Template Language (DTL) is the template engine shipped inside Django, written in pure Python. This document describes Django 6.0.9 (`.django/django/__init__.py:3`), reading `django/template/`. A compiled template is not generated code: it is a tree of Python `Node` objects, each with a `render(context)` method, interpreted on every render (`.django/django/template/base.py:11-24`). The expression language is deliberately small — one variable path plus a chain of single-argument filters — with everything else pushed into tags written in Python.

Paths are relative to the repo root; after a section's first full citation, `base.py:N` abbreviates `.django/django/template/base.py:N`.

## 2. Compilation Pipeline

| Stage | Entry point | Produces |
| --- | --- | --- |
| Lex | `Lexer.tokenize()` `.django/django/template/base.py:418` | flat `list[Token]` |
| Lex (debug) | `DebugLexer.tokenize()` `base.py:482` | same, plus `(start, end)` offsets |
| Parse | `Parser.parse()` `base.py:529` | `NodeList` |
| Expression parse | `FilterExpression` `base.py:722`, `Variable` `base.py:850` | resolvable objects stored on nodes |
| `{% if %}` parse | `IfParser` `smartif.py:156`, `TemplateIfParser` `defaulttags.py:921` | operator tree with `eval(context)` |

Lexing is a single regex split of the source on `({%.*?%}|{{.*?}}|{#.*?#})` (`base.py:92`), with an alternating `in_tag` flag deciding whether each fragment is markup or text (`:425-429`). There is no character-level scanner; `{% verbatim %}` is lexer state (`:447-455`).

Parsing pops from a reversed token list, so `next_token`/`prepend_token` are O(1) (`base.py:505`, `:659-666`). TEXT becomes `TextNode`, VAR becomes `VariableNode`, BLOCK dispatches on the first word through a dict of registered compile functions (`:577`); COMMENT tokens are discarded. Compile functions receive the parser and may recursively call `parser.parse(parse_until)`, so block structure is defined by tags, not by a grammar.

Two runtime concerns resolve at compile time: filter functions are bound into the `FilterExpression` (`base.py:771`) and arity-checked with `inspect.getfullargspec` (`:822-839`), and `{% load %}` mutates the parser's tag/filter tables mid-parse (`defaulttags.py:1125-1130`), so the grammar changes as the file is read.

## 3. Runtime Representation

A compiled template is a `Template` holding a `NodeList`, a plain `list` subclass (`.django/django/template/base.py:1085`, `:157`); each node carries the `Token` and `Origin` attached to it after construction (`:600-617`). `NodeList.render` builds a list comprehension of `node.render_annotated(context)` and `"".join`s it into a `SafeString` (`base.py:1090-1091`). `render_annotated` is a per-node try/except that attaches debug info on failure (`:1044-1068`); `TextNode` overrides it to return its string directly (`:1113-1120`).

Nodes are shared across renders and threads, so per-render state lives in a separate `RenderContext` keyed by node instance: cycle iterators (`defaulttags.py:105-108`), include caches (`loader_tags.py:197`), `ifchanged` state (`defaulttags.py:292-304`). `BlockNode` clones itself before attaching a context, explicitly for thread safety (`loader_tags.py:62-65`).

## 4. Language Model

- Two constructs: `{{ expression }}` and `{% tag %}`, plus `{# comment #}`. Delimiters are constants compiled into one regex (`.django/django/template/base.py:76-92`); not configurable.
- An expression is one literal or one dotted variable path, followed by filters taking at most one argument each (`filter_re`, `base.py:701-717`). No parentheses, no subscripts (`a.0` indexes), no list or dict literals, and no arithmetic — `+` and `-` are rejected inside names (`:917-921`).
- Callables are invoked implicitly with zero arguments (`:993-1013`); a template cannot pass arguments to a method.
- `{% if %}` is the one real expression parser: Pratt-style, Python precedence, over `or and not in "not in" is "is not" == != > >= < <=` (`smartif.py:98-112`). Operands are FilterExpressions, so arithmetic is still absent.
- Assignment exists only as tag syntax: `{% with k=v %}` (`defaulttags.py:566`) and `... as var` on `simple_tag` (`library.py:129-132`), `cycle`, `firstof`, `url`, `regroup`.
- Control flow: `if`/`elif`/`else`, `for` with `empty`, tuple unpacking, and a `forloop` dict (`defaulttags.py:212-246`). No `while`, no `break`/`continue`.

## 5. Variable Resolution and Data Model

`Variable.__init__` classifies the token once, at compile time (`.django/django/template/base.py:871-922`): int/float literal; `_("...")` marked for render-time translation; quoted string, unescaped and `mark_safe`d; otherwise a tuple of dotted segments. A leading `_` is a syntax error (`:910-914`).

`_resolve_lookup` (`:948-1028`) applies each segment in fixed order, using exceptions as control flow:

1. `current[bit]`, guarded by `hasattr(type(current), "__getitem__")` (`:963-965`).
2. `getattr(current, bit)`; class attributes are skipped when `current` is a `BaseContext` (`:972-976`), and exceptions raised inside a `@property` are re-raised rather than swallowed (`:979-980`).
3. `current[int(bit)]` (`:982`).
4. Otherwise `VariableDoesNotExist` (`:989`).

After each segment a callable is called with no arguments, unless it carries `do_not_call_in_templates` (`:994`) or `alters_data`, which yields `string_if_invalid` (`:996-997`). A `TypeError` from the call triggers signature inspection to separate "needed arguments" from an error inside the method (`:1001-1013`).

`FilterExpression.resolve` turns `VariableDoesNotExist` into `engine.string_if_invalid` (default `""`), or `None` under `ignore_failures=True` (`:785-800`); exceptions with `silent_variable_failure` are absorbed (`:1023`) and failures log at DEBUG (`:1016-1021`). Output conversion runs localtime, l10n and escaping in `render_value_in_context` (`:1123-1136`).

## 6. Context and Scoping

`BaseContext` is a list of dicts (`.django/django/template/context.py:28-38`); `dicts[0]` holds `True`/`False`/`None`. Lookup scans in reverse, testing membership then indexing (`:88-91`). Writes go only to the top frame (`:67-69`); `set_upward` writes to the nearest frame already holding the key (`:71-81`), used by `{% cycle ... as %}`.

`push()` returns a `ContextDict` that appends on construction and pops on `__exit__` (`:14-25`), so tags write `with context.push():`. Scoping is therefore by convention: `{% for %}` and `{% with %}` push (`defaulttags.py:196`, `:578-582`), but `{% include %}` without `only` renders into the caller's whole stack (`loader_tags.py:210-211`), while `only` builds a fresh stack via `context.new()` (`loader_tags.py:209`, `context.py:113-120`).

`RenderContext` is a parallel stack whose reads see only the top frame (`context.py:180-209`), pushed once per template render (`base.py:170`) — node-local state that never enters variable space. `RequestContext.bind_template` runs the engine's context processors at bind time into a reserved stack index, cleared afterwards (`:254-279`), so values pushed later still win.

## 7. Template Composition

**Inheritance.** `{% extends %}` must be the first non-text node (`must_be_first`, `.django/django/template/loader_tags.py:87`, enforced in `base.py:601-610`). At render time `ExtendsNode` loads the parent, creates or extends a `BlockContext` — a dict of per-name FIFO queues (`loader_tags.py:14-39`) — inserts the child's blocks at the front (`:22-24`), adds the root template's blocks when the parent does not itself extend (`:146-155`), then renders the *parent's* nodelist (`:159-160`). Rendering is parent-driven: child content outside any block is never emitted. `BlockNode.render` pops the most-derived block for its name, clones it, renders, and pushes the popped entry back (`:51-69`); `{{ block.super }}` renders again, popping the next queue entry (`:71-83`). Recursion is prevented by an origin history kept in the render context and passed to loaders as `skip` (`:99-115`, `loaders/base.py:18-20`).

**Inclusion.** `IncludeNode` resolves the name at render time and caches the loaded template in the bottom render-context frame keyed by the node, so loops do not reload (`loader_tags.py:177-211`). Relative `./` and `../` names resolve at compile time (`:246-288`). `inclusion_tag` instead renders a template fixed at registration from the dict a Python function returns, in an isolated `context.new()` plus a copied CSRF token (`library.py:243-292`, `:356-385`).

**Partials.** `{% partialdef %}` stores a `PartialTemplate` in `parser.extra_data["partials"]` (`defaulttags.py:1300-1315`), reachable via `{% partial %}` or externally as `"template.html#name"` (`engine.py:179-201`).

## 8. Escaping and Safety

Autoescaping is on by default at engine and context (`.django/django/template/engine.py:31`, `context.py:145`). Safety is carried by type: `SafeString`, a `str` subclass, and the `SafeData` marker exposing `__html__` (`.django/django/utils/safestring.py:13-31`), produced by `mark_safe` (`:65`). `SafeString.__add__` keeps the result safe only if both operands are safe (`:33-47`).

Escaping happens in one place — output — inside `render_value_in_context` via `conditional_escape` (`base.py:1131-1134`, `.django/django/utils/html.py:115-127`), which honours `__html__` and so interoperates with markupsafe. `escape` is `html.escape`, covering `& < > " '` (`html.py:51-60`). Filters that build markup escape their own inputs and return `mark_safe` (e.g. `join`, `defaultfilters.py:611-621`); they declare `is_safe` (re-mark safe if the input was), `needs_autoescape` and `expects_localtime` at registration (`library.py:85-92`), which `FilterExpression.resolve` consults (`base.py:810-819`). `{% autoescape off %}` flips the flag on the live context for the block (`defaulttags.py:55-63`); `{% filter %}` refuses `escape` and `safe` to avoid confusion with it (`:746-751`).

There is no sandbox. Templates reach anything reachable from context objects; the only access restriction is the leading-underscore rule (`base.py:910`), and `alters_data`/`do_not_call_in_templates` are advisory opt-ins on the Python side. The enforced boundary is the filesystem: loaders use `safe_join` against directory traversal (`loaders/filesystem.py:35`, `backends/base.py:79`).

## 9. Extensibility

| Extension point | Can do | Cannot do |
| --- | --- | --- |
| `register.tag` `.django/django/template/library.py:29` | gets `Parser` + `Token`; consume arbitrary tokens, define block forms, return any `Node` | change delimiters; see source offsets outside debug mode |
| `register.filter` `library.py:57` | transform a value; declare the safety/locale flags | take more than one argument (`base.py:701-717`, checked at `:822`) |
| `simple_tag`, `simple_block_tag` `library.py:104`, `:159` | positional/keyword args, `takes_context`, `as var` | alter parsing beyond its end tag |
| `inclusion_tag` `library.py:243` | render a named template from a returned dict | vary the template per call (fixed at registration) |
| Loaders `loaders/base.py:4` | supply origins and source from any medium | hook into parsing or rendering |
| `cached.Loader` `loaders/cached.py:14` | memoize compiled templates and misses | invalidate itself; reset comes from autoreload (`autoreload.py:54-61`) |
| Context processors `context.py:260-272` | inject request-derived values | run outside `RequestContext` |
| Engine backends `backends/base.py:7` | plug a non-DTL engine behind `get_template`/`render` | share DTL tags or loaders |
| `parser.extra_data` `base.py:514` | pass compile-time data to the `Template` object | be read during another template's parse |

Libraries are discovered from each installed app's `templatetags` package (`backends/django.py:133-164`), scoped per template by `{% load %}` or made global via the `builtins` option (`engine.py:62`).

## 10. Error Handling and Debugging

Compile-time failures raise `TemplateSyntaxError` (`.django/django/template/exceptions.py:39`). The parser annotates it with the offending token, keeping the innermost one under recursion (`base.py:619-630`); `compile_nodelist` prefixes the template name and attaches `template_debug` when debugging (`:202-213`). Render-time failures pass through `render_annotated`, which records `_culprit_node`, builds `template_debug` when debugging, and re-raises (`:1044-1068`). `TemplateDoesNotExist` carries a `tried` list of `(origin, reason)` pairs accumulated across loaders (`exceptions.py:28-36`, `engine.py:156-163`).

Positions are tracked at two resolutions: line numbers always (`:427`), character offsets only under `DebugLexer` (`:482-498`). `get_exception_info` reconstructs ±10 source lines from those offsets (`:215-291`), so the detailed error page exists only when `debug=True`.

The silent-failure policy is broad: unknown variables become `string_if_invalid` (`base.py:793-800`); any exception inside an `{% if %}` operator evaluates to `False` (`smartif.py:62-67`); `IfNode` absorbs `VariableDoesNotExist` (`defaulttags.py:325-328`); `VariableNode` swallows `UnicodeDecodeError` and emits `""` (`base.py:1151-1155`); a method needing arguments yields `string_if_invalid` (`:1009-1011`).

## 11. Performance Characteristics

**Caching.** `cached.Loader` is on by default and memoizes compiled templates by name, with a hash of the extends chain in the key (`.django/django/template/engine.py:38-41`, `loaders/cached.py:47-93`); misses are cached too, as a class in production and a detached exception copy in debug. Loaders, context processors and app template dirs are memoized on the engine (`engine.py:88`, `:114`, `:129`, `utils.py:97`), and `IncludeNode`/`InclusionNode` cache resolved templates per render (`loader_tags.py:197`, `library.py:365`).

**Per-render work.** Each node costs at least one Python method call (`render_annotated` → `render`), and each nodelist allocates a list and performs a `"".join` (`base.py:1090-1091`), so nesting materializes intermediate strings and re-joins them at every level instead of appending to one buffer. The single hot-path optimization is `TextNode.render_annotated` bypassing the try/except (`:1113-1120`).

**Variable lookup** dominates. Each dotted segment may raise and catch an exception from the dict attempt before falling to `getattr`, and another before integer indexing (`:959-992`) — exception handling is the normal path for attribute access. Every resolved segment also pays a `callable()` test (`:993`), every filter application three `getattr(func, flag, False)` probes (`:810-816`), and every variable node a `conditional_escape` with its `isinstance`/`hasattr` checks (`html.py:122-127`).

**Context lookup** is a reverse linear scan doing a membership test then an index — two hash lookups per frame examined (`context.py:88-91`). The stack deepens with every `for`, `with`, `block` and non-isolated `include`, so misses cost more in deeply nested templates.

**Compilation** is one regex split plus one `Token` and one `Node` allocation per construct; `DebugLexer` is slower because it uses `finditer` with per-token slicing (`base.py:468-480`). Inheritance also costs per render: `ExtendsNode.render` walks the parent nodelist and may run the recursive `get_nodes_by_type` every time (`loader_tags.py:146-154`).

## 12. Design Decisions

| Decision | Rationale | Consequences (+) | Consequences (−) |
| --- | --- | --- | --- |
| Interpret a tree of `Node` objects rather than generate code (`base.py:11-24`) | Module docstring: nodes are the extension unit; plugins define their own | Tags are ordinary Python classes; introspection works on the live tree | One Python call per node per render; no constant folding |
| Restricted expression grammar: one variable, single-argument filters (`base.py:701-717`, `:917-921`) | Keeps logic out of templates; filters are the whole vocabulary | Readable by non-programmers; the filter set is auditable | No arithmetic; compound or multi-argument work needs a Python tag or filter |
| Unknown variables resolve to `string_if_invalid`, default `""` (`base.py:793-800`) | Rendering should not break a page over one missing value | Pages degrade instead of 500ing | Typos are invisible; output and `{% if %}` silently produce nothing |
| Callables invoked implicitly with no arguments (`base.py:993-1013`) | `{{ obj.method }}` and `{{ obj.attr }}` are written identically | Methods and properties are interchangeable | Accidental access can trigger side effects or queries; `alters_data` is opt-in |
| Exception-driven lookup cascade: dict → attribute → index (`base.py:959-992`) | One syntax must cover mappings, objects and sequences | `a.b` works uniformly across data shapes | Misses cost raised exceptions; a dict key shadows a same-named attribute |
| Context is a stack of plain dicts (`context.py:28-91`) | Push/pop matches block nesting directly | Cheap to build; any dict can be pushed | Lookup is linear in depth; no compile-time name resolution or slots |
| Scope isolation is opt-in, not structural (`loader_tags.py:209-211`) | Backwards compatibility with "includes see everything" | Includes need no argument passing | Caller/callee name collisions; data flow invisible in the template |
| Separate `RenderContext` for node-local state (`context.py:180-222`) | Stated: nodes need state between `render` calls without polluting variables | Shared `Node` instances stay immutable and thread-safe | A second stack to reason about; state keyed by node identity |
| `{% extends %}` renders the parent, blocks in FIFO queues (`loader_tags.py:134-160`) | Children must override at arbitrary depth; `block.super` needs the next one | Multi-level inheritance and `block.super` fall out of one structure | Child content outside blocks is dropped; resolution repeats every render |
| Autoescape on by default, carried by a `str` subclass (`engine.py:31`, `safestring.py:25`) | XSS-safe default without annotating call sites | Safety travels with the value through filters and joins; `__html__` interop | Every output node pays a type check; a wrong `mark_safe` is unreviewed |
| Compiled templates cached by name in a wrapping loader (`loaders/cached.py:14-66`) | Parsing is the expensive phase; renders reuse the tree | Parse cost paid once per process; misses cached too | Per-process cache, invalidated only by autoreload; stale after out-of-band edits |

## 13. Deliberate Non-Goals

- **No code generation.** The pipeline ends at an object tree, by design (`base.py:11-13`).
- **No arbitrary Python.** No arithmetic, call arguments, or literals beyond numbers and strings (`base.py:701-717`, `:917-921`).
- **No sandbox for untrusted templates.** Attribute access is unrestricted apart from the leading-underscore rule; `alters_data` is advisory (`base.py:910`, `:996`).
- **No streaming output.** `NodeList.render` materializes the full string before returning (`base.py:1090-1091`).
- **No async rendering.** Every `render` in the package is synchronous.
- **No loop control.** No `break`, `continue` or `while` (`defaulttags.py:191-256`).
- **No dynamic block names.** `{% block %}` takes a bare token (`loader_tags.py:221-224`).
- **No configurable delimiters.** Module constants compiled into one regex (`base.py:76-92`).
- **No ambition to be the only engine.** The backend API exists so other engines plug in alongside DTL rather than DTL absorbing their features (`.django/django/template/__init__.py:4-13`, `backends/base.py:7`).
