# minijinja — Architectural Design

## 1. Overview

minijinja is a template engine written in Rust that reimplements Jinja2's syntax on its own runtime data model. Source read: a clone of `github.com/mitsuhiko/minijinja` at commit `a1aeca51911ee974b7d9ab9f6560a5fcfa7e3e2b` (2026-10-11), crate version **3.0.0** (`minijinja/Cargo.toml:3`); all citations are paths in that repository. The stated stance is a template engine "with minimal dependencies" — the default build has none — so "it should be possible to use some templates in Rust programs without the fear of pulling in complex dependencies for a small problem" (`README.md`). It compiles to a bytecode stream run by a stack VM, and borrows from the template source rather than copying wherever it can.

## 2. Compilation Pipeline

| Stage | Entry point | Produces |
|---|---|---|
| Tokenize | `Tokenizer::next_token` (`minijinja/src/compiler/lexer.rs:18`) | `(Token<'source>, Span)`, mostly borrowed `&str` |
| Parse | `Parser::parse` (`minijinja/src/compiler/parser.rs:271`) | `ast::Stmt<'source>` tree of `Spanned<T>` nodes |
| Constant fold | `Expr::as_const` (`minijinja/src/compiler/ast.rs:214`) | `Const` nodes for literal-only subexpressions |
| Codegen | `CodeGenerator::compile_stmt` (`minijinja/src/compiler/codegen.rs:118`) | `Instructions<'source>` + one per block |
| Assemble | `CompiledTemplate::new` (`minijinja/src/template.rs:423`) | `CompiledTemplate` |

The lexer is a hand-written state machine (`minijinja/src/compiler/lexer.rs:32`) scanning for start markers with `memchr` (`:81`); `{% raw %}` is handled entirely there, its body emitted as one `TemplateData` token (`:642`). The parser is recursive descent with precedence climbing from the `binop!`/`unaryop!` macros (`minijinja/src/compiler/parser.rs:188`), guarded against deep recursion (`:855`). Folding is not a separate pass: `compile_expr` tries `as_const()` first and emits `LoadConst` on success (`minijinja/src/compiler/codegen.rs:669`).

The instruction set is a flat enum of ~67 variants, `Instruction<'source>`, asserted to be 32 bytes (`minijinja/src/compiler/instructions.rs:47`, `:566`). It is not fixed-width opcodes: variants carry `&'source str` names, `Value` constants, `u32` jump targets, `Option<u16>` arg counts. Groups: output (`EmitRaw`, `Emit`), access (`Lookup`, `GetAttr`, `GetItem`, `Slice`), construction (`BuildMap`/`List`/`Tuple`/`Kwargs`), arithmetic and comparison, control flow (`Jump`, `JumpIfFalse`, `PushLoop`, `Iterate`, `PopLoopFrame`), calls (`ApplyFilter`, `PerformTest`, `CallFunction`, `CallMethod`, `CallObject`), capture (`BeginCapture`, `EndCapture`), plus feature-gated inheritance (`CallBlock`, `LoadBlocks`, `Include`, `FastSuper`) and macro (`BuildMacro`, `Return`, `Enclose`, `GetClosure`) opcodes. Filter and test names get a compile-time `LocalId` slot, capped at 50 per template (`minijinja/src/compiler/codegen.rs:25`).

## 3. Runtime Representation

A compiled template is `CompiledTemplate` (`minijinja/src/template.rs:396`): root `Instructions`, a `BTreeMap<&str, Instructions>` of blocks, a buffer size hint, the syntax config, and the initial autoescape mode. `Instructions` holds the instruction `Vec` plus run-length side tables — lines always, spans only under `debug` — searched by binary search (`minijinja/src/compiler/instructions.rs:279`, `:439`).

Instructions borrow out of the source: names, raw text chunks, and string literals are slices. A `Template` handle holds only references and is cheap to clone (`minijinja/src/template.rs:52`). Loader-owned templates pair the `String` source with the `Instructions` borrowing it through the vendored `self_cell` (`minijinja/src/loader.rs:146`).

Execution is a `loop` over `instructions.get(pc)` with a `match` (`minijinja/src/vm/mod.rs:294`, `:380`) — plain interpreter dispatch, no threading or jump table. The operand stack is a `Vec<Value>` preallocated to 24 slots (`minijinja/src/vm/context.rs:81`).

Output goes through `Output` (`minijinja/src/output.rs:22`), a `dyn fmt::Write` wrapper with a capture stack; a capture pushes `Some(String)` or `None` (discard) and retargets a cached raw pointer, so writes stay one indirect call. Rendering to a `String` preallocates `raw_template_bytes * 2` rounded to a power of two (`minijinja/src/compiler/codegen.rs:1039`). `io::Write` targets go through `WriteWrapper`, which stashes the `io::Error` so the resulting `fmt::Error` can be upgraded back to it (`:150`).

## 4. Language Model

Expressions: literals (including `i128`/`u128` and tuples), variables, attribute/item access, slices, `not`/unary `-`, `+ - * / // % **`, `~` concat, chained comparisons, short-circuit `and`/`or`, inline `if`, calls with kwargs and `*`/`**` unpacking, list/tuple/map literals, filters, tests (`minijinja/src/compiler/ast.rs:132`).

Statements, complete set: `for` (with `recursive`, loop filter, `else`), `if`/`elif`/`else`, `with`, `set` and `{% set %}…{% endset %}`, `autoescape`, `filter`, `block`, `extends`, `include`, `import`, `from`, `macro`, `call`, `do`, `raw`, and `break`/`continue` behind `loop_controls` (`minijinja/src/compiler/parser.rs:873`). The keyword table is a closed `match`; anything else is a syntax error (`:912`).

Divergences from Jinja2, per `COMPATIBILITY.md`: no Python methods at all (`x.items()` fails; `|items` exists); macros have no `varargs`/`kwargs` or `catch_*` attributes; `include` omits `with context`/`without context`; `import` yields only exported locals, discarding rendered output; `%`-style string formatting is absent; `foo.bar` and `foo["bar"]` are identical rather than disambiguating; `|xmlattr`, `|urlize` and most filters' `attribute=` argument are missing; unicode identifiers need the `unicode` feature.

## 5. Variable Resolution and Data Model

`Value` wraps `ValueRepr` (`minijinja/src/value/mod.rs:577`), asserted to be 24 bytes (`:2371`):

| Variant | Storage |
|---|---|
| `None`, `Bool`, `U64`, `I64`, `F64` | inline |
| `U128`/`I128` | `Packed<T>`, `#[repr(packed)]` to avoid 16-byte alignment (`:523`) |
| `SmallStr` | inline buffer, up to 22 bytes (`:538`) |
| `String(Arc<str>, StringType)` | refcounted; `StringType::{Normal, Safe}` (`:486`) |
| `Bytes(Arc<Vec<u8>>)` | refcounted |
| `Undefined(UndefinedType, UndefinedOrigin)` | inline; origin is `(instructions_id, pc)`, debug builds only (`:498`) |
| `Invalid(Arc<Error>)` | deferred error, propagates on use |
| `Object(DynObject)` | type-erased `Arc` |

Everything non-scalar is an `Object` (`minijinja/src/value/object.rs:174`) — maps, sequences, iterables, macros, loops, host types. The trait surface is `repr`, `get_value`, `get_value_by_str`, `enumerate`, `enumerator_len`, `is_true`, `call`, `call_method`, `custom_cmp`, `render`; `ObjectRepr` says whether the engine treats it as `Plain`, `Map`, `Seq`, or `Iterable` (`:866`). `DynObject` comes from the `type_erase!` macro (`minijinja/src/value/type_erase.rs:5`), a hand-rolled `{ptr, vtable}` pair with a static vtable rather than `Arc<dyn Object>` — one word instead of a fat pointer, carrying `TypeId` for downcasting.

Attribute and index lookup converge. `GetAttr` calls `get_attr_fast`, which only succeeds on objects and returns `Option` instead of building an error (`minijinja/src/value/mod.rs:1556`, used at `minijinja/src/vm/mod.rs:419`). `GetItem` calls `get_item_opt`, switching on `ObjectRepr`: maps take the key as-is, sequences normalize negative indices, iterables walk to the nth element (`:1794`). Strings index by character, costing `chars().count()`. Misses route through `UndefinedBehavior::handle_undefined` (`minijinja/src/utils.rs:291`) in one of four modes: `Lenient` (default), `Chainable`, `SemiStrict`, `Strict` (`:250`).

Cost of `a.b.c`: two `GetAttr` instructions, each a `match` on `ValueRepr`, one indirect vtable call, one `Value` clone (24-byte copy plus possibly an atomic increment). The engine hashes nothing itself; `get_value_by_str` exists so string-keyed objects avoid building a temporary `Value` (`minijinja/src/value/object.rs:193`). Rust types enter via `From`/`from_object`; serde conversion is opt-in and explicit through the `Serde` wrapper (`minijinja/src/value/mod.rs:85`). Filter and function parameters are converted by `ArgType` (`minijinja/src/value/argtypes.rs:145`).

## 6. Context and Scoping

`State` (`minijinja/src/vm/state.rs:43`) is the per-render object: context, current instructions, autoescape mode, block stacks, loaded-template set, macro closures, temps, typed extension slots, and (debug only) the instruction streams undefined values point into.

`Context` is a `Vec<Frame>` preallocated to 40 (`minijinja/src/vm/context.rs:132`). A `Frame` holds `locals: BTreeMap<&'env str, Value>`, a `ctx: Value`, an optional `current_loop`, and optional closure ids. `load` walks frames innermost-outward checking locals, then `loop`, then the frame's closure, then `ctx.get_attr_fast(key)`, falling through to environment globals (`:281`). The user's whole context is one `Value` on the root frame — never flattened into a map. Frames are pushed by `PushLoop`, `PushWith`, macro calls and includes, with `recursion_limit` checked on push (`:449`); includes cost 10, macro calls 4 (`minijinja/src/vm/mod.rs:43`). Loop iterations clear the frame's locals rather than push a frame (`minijinja/src/vm/context.rs:396`).

The `loop` object is an `Arc<Loop>` with an atomic index, a length from the iterator's size hint when exact, and a recursion jump target (`minijinja/src/vm/loop_object.rs:25`). Under `adjacent_loop_items` the iterator sits behind a `Mutex` inside it so `previtem`/`nextitem` can peek; with the feature off it sits on the frame and no mutex is taken (`:18`).

Macros capture a closure: once a macro is declared in a frame, every later `store` into that frame is duplicated into the closure map, shared by all macros at that level, "to emulate the behavior of how scopes work in Jinja2 in Python" (`minijinja/src/vm/context.rs:232`). Closures are region-allocated for the life of the `State` and never truncated, because macro values can escape through namespaces or temps (`minijinja/src/vm/state.rs:62`). Blocks have no closures; a block body sees the context as it stands at render time.

## 7. Template Composition

`{% extends %}` compiles to `LoadBlocks`, which stashes the parent's instructions; the child keeps running with output discarded — so `set` and macro declarations still take effect — and the VM switches streams when it falls off the child's end (`minijinja/src/vm/mod.rs:300`, `:816`). The comment there also records the consequence: a parent's name wins over a child's, because blocks lack closures.

Blocks compile to separate `Instructions` streams, each becoming a `BlockStack` at render time (`minijinja/src/vm/mod.rs:52`); `super()` compiles to `FastSuper`, rendering the next entry down the stack straight to the output with no intermediate capture (`:809`). `{% include %}` takes a name or a candidate list plus `ignore missing`, evaluates against the *current* state with the block set replaced, and wraps failures in `ErrorKind::BadInclude` with the original as source (`:928`); the macro closure is taken away for the duration so an include cannot mutate the parent's closures (`minijinja/src/vm/context.rs:251`). `{% import %}` runs the target and exports its root-frame locals as a module (`ExportLocals`, `minijinja/src/vm/mod.rs:857`).

A loader is one callback, `Fn(&str) -> Result<Option<TemplateSource>, Error>` (`minijinja/src/loader.rs:19`); `path_loader` is the filesystem one (`:443`). A `TemplateSource` may carry an up-to-date closure for auto-reload (`:68`). Replaced cache entries move to a `stale` list instead of being dropped, keeping outstanding borrows valid (`:194`). Relative names go through an optional `path_join_callback` (`minijinja/src/environment.rs:299`). Inheritance and includes require a template borrowed from the environment; one compiled from a string errors with "cannot extend or include template not borrowed from environment" (`minijinja/src/template.rs:364`).

## 8. Escaping and Safety

Autoescaping is per-template, decided at compile time by a callback (`minijinja/src/template.rs:24`). The default maps `.html`, `.htm`, `.xml` to HTML and, with the `json` feature, `.json`/`.json5`/`.js`/`.yaml`/`.yml` to JSON, after stripping a `.j2`/`.jinja`/`.jinja2` suffix (`minijinja/src/defaults.rs:31`). `AutoEscape` has four cases: `None`, `Html`, `Json`, and `Custom(Cow<'static, str>)`, which the default formatter refuses and which requires a custom formatter (`minijinja/src/utils.rs:210`).

Safety rides in the value, not a wrapper: `String(Arc<str>, StringType)` with `StringType::{Normal, Safe}`. `SmallStr` has no safety bit, so a safe string always heap-allocates (`minijinja/src/value/mod.rs:1078`). Escaping happens at `Emit`, in `write_escaped` (`minijinja/src/utils.rs:186`): a safe string short-circuits to `write_str`, otherwise the value dispatches on mode, and the HTML path checks integers, bools and "needs no escaping" before allocating (`:123`). Captured output — `{% filter %}`, `{% set %}` blocks, macro bodies — is marked safe on capture end when autoescape is active (`minijinja/src/output.rs:61`).

There is no sandbox; the word does not appear in the crate. The posture is structural: templates reach only what the host exposed as an `Object`, `Object::call_method` is the single method-dispatch path, and the engine performs no I/O. For untrusted templates what exists is cost bounding — `recursion_limit` (`minijinja/src/environment.rs:651`) and the optional `fuel` feature, which decrements a budget per non-bookkeeping instruction (`minijinja/src/vm/fuel.rs:43`).

## 9. Extensibility

| Point | API | Can / cannot |
|---|---|---|
| Filters / tests | `add_filter`, `add_test` (`minijinja/src/environment.rs:708`, `:728`) | Any `Fn` with `ArgType` params, optionally taking `&State`/`&mut State`. No syntax changes. |
| Globals | `add_function`, `add_global` (`:752`, `:763`) | Values in the global map, consulted last in the lookup chain. |
| Host types | `impl Object` (`minijinja/src/value/object.rs:174`) | Attributes, indexing, iteration, truthiness, calling, methods, rendering, ordering. No operator overloads beyond comparison. |
| Loaders | `set_loader`, `set_path_join_callback` (`:228`, `:299`) | Any name→source function and relative-name policy. |
| Delimiters | `SyntaxConfig` (`minijinja/src/syntax.rs:1221`) | Block/variable/comment delimiters, line statements, `trim_blocks`, `lstrip_blocks`, `keep_trailing_newline`. `COMPATIBILITY.md` calls these "largely discouraged". |
| Formatter | `set_formatter` (`:546`) | Replaces how every emitted value is written; the only way to implement `AutoEscape::Custom`. |
| Unknown methods | `set_unknown_method_callback` (`:334`) | Catch-all for methods objects don't handle; `minijinja-contrib`'s `pycompat` uses it to supply Python methods. |
| Undefined | `set_undefined_behavior` (`:499`) | Picks one of four behaviors; the undefined type is not replaceable. |
| Render-local state | `State::get_or_insert_extension`, `set_temp` (`minijinja/src/vm/state.rs:537`, `:480`) | Typed per-render storage for filters and objects. |

Not extensible: statements/tags, the instruction set, the lexer, the AST, `ValueRepr`. There is no plugin object; a new "tag" has to become a filter, function, or callable `Object`.

Cargo features are the other configurability axis and are themselves a design decision (`minijinja/Cargo.toml:20`). Default: `builtins`, `debug`, `macros`, `multi_template`, `adjacent_loop_items`, `std_collections`. Each removes real code — dropping `multi_template` removes `{% block %}`, `{% extends %}`, `{% include %}`, `{% import %}`, `{% from %}`, their instructions, their AST nodes, and the block fields of `State`. Off by default: `serde`, `loop_controls`, `fuel`, `unicode`, `preserve_order`, `json`, `urlencode`, `stacker`, `speedups`. `speedups` swaps in `v_htmlescape`, `zmij` and `itoa` and is documented not to change output.

## 10. Error Handling and Debugging

`Error` is one struct with a boxed `ErrorRepr` (`minijinja/src/error.rs:40`), keeping `Result<_, Error>` pointer-wide. The repr carries kind, detail, template name, line, optional `Span`, a chained `source`, and under `debug` a `DebugInfo` and an `UndefinedOrigin`. `ErrorKind` is a flat non-exhaustive enum of 22 variants (`:95`): `SyntaxError`, `TemplateNotFound`, `UndefinedError`, `UnknownFilter`/`Test`/`Function`/`Method`/`Block`, `InvalidOperation`, `BadInclude`, `EvalBlock`, `CannotUnpack`, `WriteFailure`, `OutOfFuel`, and others.

Location is attached lazily on the way out, in `process_err` (`minijinja/src/vm/mod.rs:1233`): if the error has no line, binary-search the span table (debug) or line table (always) for the current `pc`. Compile-time errors get the full source attached by `attach_basic_debug_info` (`minijinja/src/error.rs:444`).

The `debug` feature adds per-instruction spans, a `DebugInfo` carrying a source snapshot plus the values of variables referenced near the failure (`minijinja/src/debug.rs:68`, driven by `Instructions::get_referenced_names`, `minijinja/src/compiler/instructions.rs:479`), and undefined provenance. The last post-dates `COMPATIBILITY.md`'s claim that undefined is "a singleton without further information": the value carries `(instructions_id, pc)`, resolved into messages like "`user.name` is undefined (from line 12)" (`minijinja/src/vm/mod.rs:1245`). `{}` prints the debug block after the message, `{:#}` reverses the halves (`minijinja/src/error.rs:82`, `:195`); debug value printing is depth- and size-limited via a thread-local (`minijinja/src/debug.rs:18`).

## 11. Performance Characteristics

**Compile.** One lexer pass over `memchr` scans, one recursive-descent parse allocating a boxed `Spanned` per node, one codegen pass. `Instructions` preallocates 256 slots and the line/span tables 128 each (`minijinja/src/compiler/instructions.rs:321`); codegen scratch vectors come from thread-local pools so repeated compiles do not re-allocate them (`minijinja/src/compiler/codegen.rs:68`). Dropping `debug` removes the span table.

**Per render.** No copy of the program. `State::new` allocates the frame vector (40), the operand stack (24), and a few empty `BTreeMap`s. Static text is one `write_str` of a borrowed slice — never copied out of the template.

**Value cloning.** A 24-byte copy plus one atomic increment for `String`/`Bytes`/`Object`. Short strings — most attribute values and keys — fit the inline `SmallStr` and clone without touching the allocator.

**Dispatch.** A `match` over a 32-byte enum and one bounds-checked index per instruction. Filters and tests resolve by `LocalId` into a per-render `[Option<&Value>; 50]`, so the environment's `BTreeMap` is consulted once per name per render, not per call (`minijinja/src/vm/mod.rs:62`, `:712`); both caches are flushed when `extends` switches streams, because the ids change meaning (`:315`).

**Structural slow paths.** String indexing is O(n) in characters; indexing an `Iterable` with no `get_value` walks the iterator (`minijinja/src/value/mod.rs:1806`). `{% filter %}`, `{% set %}` blocks and macro bodies allocate a capture `String`, which `super()` and recursive loops avoid via `FastSuper`/`FastRecurse`. With `adjacent_loop_items` on (the default) every loop iteration takes a mutex — the docs note removing it "can provide faster template execution when a lot of loops are involved" (`minijinja/src/lib.rs:149`). Extending discards the child's output through a null writer rather than skipping execution, so the child body runs in full.

## 12. Design Decisions

| Decision | Rationale | Consequences (+) | Consequences (−) |
|---|---|---|---|
| Bytecode stream run by a stack VM | Rust cannot `exec` generated source (inferred) | Cheap one-pass compile; no runtime codegen; instructions are inspectable and fuel-countable | Per-operation dispatch a native-code target would not pay |
| Zero required dependencies; `serde` optional and off | Stated: usable "without the fear of pulling in complex dependencies for a small problem" (`README.md`) | One crate in `cargo tree`; fast builds; easy to vendor | Reimplements its own data model, HTML escaping and `self_cell` (`minijinja/src/vendor/`) |
| Instructions borrow `&'source str` from the source | Avoids copying names and literal text (inferred) | No per-name allocation; static text emits a borrowed slice | Lifetime plumbing through `Environment<'source>`/`Template<'env,'source>`; owned templates need `self_cell` |
| `Value` fixed at 24 bytes with a 22-byte inline string | Stated in the comment deriving `SMALL_STR_CAP` from that budget (`minijinja/src/value/mod.rs:538`) | Short strings allocate nothing; `Value` is cheap to clone | Longer strings always heap-allocate; safe strings cannot use the inline path |
| Everything non-scalar is an `Object` behind one trait | One shape for containers, macros, loops and host types (inferred, `minijinja/src/value/object.rs:866`) | Host types are indistinguishable from built-in containers; no boundary conversion | Every container access is an indirect vtable call; no specialization for maps |
| Hand-rolled `{ptr, vtable}` erasure, not `Arc<dyn Object>` | Keeps an object value one word (inferred, `minijinja/src/value/type_erase.rs:5`) | `Value` stays 24 bytes with objects included | Unchecked unsafe code; manual refcount vtable entries |
| Context is a `Vec<Frame>` of `BTreeMap` locals, searched outward | Mirrors Jinja2 scoping without compile-time slot binding (inferred) | Dynamic scoping across includes, macros, extends; no resolution pass | Every lookup walks frames and does a tree lookup per frame |
| User context stays one `Value`, never copied into a map | Stated: Jinja2's context-as-data-source optimization "is not particularly useful in MiniJinja" (`COMPATIBILITY.md`) | A large host-side context costs nothing until read | Resolution cost moves into the host's `get_value_by_str` |
| Filters/tests cached per render in a 50-slot array by `LocalId` | Avoids a map lookup per invocation (`minijinja/src/compiler/codegen.rs:24`) | Repeated filter use in a loop costs an array index | Past 50 distinct names it falls back to map lookup; flushed on `extends` |
| Safety is a bit on the string variant, not a wrapper type | Lets `Emit` short-circuit on safe strings with one match (`minijinja/src/utils.rs:192`) | No allocation or wrapper for marked-safe output | Safety is lost through any conversion not preserving `StringType` |
| Autoescape fixed per template at compile time by filename | Matches Jinja2's `select_autoescape`, extended to non-HTML modes (`minijinja/src/defaults.rs:31`) | Mode known before render; `{% autoescape %}` only pushes a flag | Escaping depends on the template's name; `Custom` is inert without a custom formatter |
| Language features behind Cargo features that delete code, AST and opcodes | Stated: "internally modular so unnecessary bits and pieces can be removed" (`minijinja/src/lib.rs:122`) | Minimal builds drop inheritance, macros and builtins entirely | Combinations multiply the test surface; behavior differs between builds of one version |
| No sandbox; bounded by recursion limit and optional fuel | The data model is already closed (inferred; "sandbox" appears nowhere) | No attribute allowlist to maintain; no sandbox-escape surface | A host exposing a broad `Object` has no second line of defense; cost bounding is opt-in |
| Extending runs the child with output discarded, then jumps to the parent | Stated: inherited from Jinja2 so pre-`extends` `set`/macros stay visible in blocks (`minijinja/src/vm/mod.rs:816`) | `{% import %}` before `{% extends %}` works as users expect | The child body runs purely for side effects; parent names override child names |

## 13. Deliberate Non-Goals

- **Python semantics.** "MiniJinja does not implement _any_ Python methods. In particular this means you cannot do `x.items()`" (`COMPATIBILITY.md`); compatibility is pushed out to `minijinja-contrib`'s `pycompat` via `set_unknown_method_callback`.
- **Full Jinja2 filter coverage.** "MiniJinja supports many common Jinja2 filters but leaves out some … It's a soft goal to increase the number" — `|xmlattr`, `|urlize`, and most filters' `attribute=` argument are named as absent (`COMPATIBILITY.md`).
- **`with context` / `without context` on includes.** "intentionally not supported. The context system of MiniJinja is different" (`COMPATIBILITY.md`).
- **`varargs`/`kwargs` macros** and the `catch_kwargs`/`catch_varargs` introspection attributes (`COMPATIBILITY.md`).
- **Keeping rendered output from `{% import %}`.** "the returned item is a map of the exported local variables. This means that the rendered content of the template is lost" (`COMPATIBILITY.md`).
- **Custom tags.** The keyword table is a closed `match` ending in `unknown statement {}` (`minijinja/src/compiler/parser.rs:912`); the parser has no extension hook.
- **Mandatory dependencies, serde included.** The README's one-line `cargo tree` is the stated goal; serde "is disabled by default" (`minijinja/src/lib.rs:157`).
- **Encouraging custom delimiters.** Supported but "largely discouraged" (`COMPATIBILITY.md`), on the README's reasoning that staying close to Jinja2 leverages "an already existing ecosystem of editor integrations".
- **Unicode identifiers by default.** "MiniJinja by default does not allow unicode identifiers" (`COMPATIBILITY.md`).
- **Unbounded recursion.** Runtime recursion past the default limit needs `stacker`, which "does not affect the maximum recursion at parsing time which is always limited" (`minijinja/src/lib.rs:182`).
