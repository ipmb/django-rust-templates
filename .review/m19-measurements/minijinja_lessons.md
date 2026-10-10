# What minijinja does that makes it fast, and what transfers

Source read: `mitsuhiko/minijinja` @ `2a0f8a4ecc8a84ee36d8fe1e699558899f5342db`
(2026-10-09), cloned to `$CLAUDE_JOB_DIR/tmp/minijinja`. Every mechanism row below
carries a `file:line` from that clone. Claims I could not tie to source are in the
last section. Where the brief assumed a mechanism (slot-index name resolution,
compile-time escape decisions, a computed goto), I say what the search was that
failed to find it.

## The one-paragraph answer

minijinja is fast because **there is no Python-shaped work in the render path at
all**: a template compiles once into a flat `Vec<Instruction>` addressed by a
program counter, and rendering is one Rust `loop { match instr { … } pc += 1 }`
over that vector, where "text" is a borrowed `&str` slice of the source written
straight into one `String`, "a loop" is a *jump target* rather than a recursive
call, "a loop variable" is one insertion into a `BTreeMap<&str, Value>` in a
private frame that user code cannot see, "`loop.index`" is computed on demand
from a counter and never materialised into a dict, "a filter" is a resolved
`Value` cached in a per-render array indexed by a compile-time `u8`, "a value" is
a 24-byte Rust enum (inline `u64`, inline ≤22-byte string, `Arc<str>` otherwise)
so binding or emitting one is a memcpy and at most one refcount bump, and "an
include/extends" is a name-keyed cache lookup returning a borrowed compiled
object, with `extends` implemented as `state.instructions = parent; pc = 0;
continue` inside the *same* dispatch loop. None of it is clever — it is a
straightforward bytecode VM — and that is the point: the speed is bought by
choosing a representation in which the cheap operations are the ones a template
actually performs, not by micro-optimising the expensive ones.

## Mechanisms

| mechanism | what minijinja does | file:line | what it costs them | our equivalent today | applicable? |
|---|---|---|---|---|---|
| Flat instruction stream | `Instructions { instructions: Vec<Instruction> }`, `pc: u32` | `compiler/instructions.rs:279`, `:357` | a bounds-checked `get` per dispatch | Python node tree; one method call per node (`base.py:1154`) | **yes** — the shape the walk must take |
| Instruction set | ~60 variants; `EmitRaw(&str)`, `Emit`, `Lookup(&str)`, `StoreLocal(&str)`, `ApplyFilter(name, argc, local_id)`, `PushLoop(flags)`, `Iterate(u32)`, `Jump(u32)`, `JumpIfFalse(u32)`, `LoadConst(Value)` | `instructions.rs:47-263` | 32-byte enum (`assert_eq!(size_of::<Instruction>(), 32)`, `:569`) | the descriptor `NodeDesc` + the spike's `Op` | **yes** |
| Dispatch | one `match instr`, `pc += 1`; `Jump`/`Iterate` set `pc` and `continue` | `vm/mod.rs:294-295` (loop + fetch), `:380` (match), `:914` (`pc += 1`), `:669-672` (Jump) | an indirect branch per instruction | `render_annotated` → `render` → Python frame | **yes**, within the set we own |
| No computed goto | **search that falsified it:** `grep -rniE "computed_goto\|jump_table\|threaded\|direct_threading" minijinja/src/` → 0 hits. No `#[repr(u8)]` on the enum (`instructions.rs:46-47`: only `#[derive(Clone)]`) | — | relies on rustc's lowering of the `match` | spec §2 says "a jump table" for node dispatch (`spec:363`) — that is what a Rust `match` on a 60-variant enum gives you, nothing more | **no work to budget** |
| Text | `EmitRaw(&'source str)` — a *slice of the template source*, written directly | `instructions.rs:49`; arm `vm/mod.rs:387-391` | the compiled object borrows the source (`Instructions<'source>`) | `AttrValue::Str(String)`; `TextNode.render_annotated` returns `self.s` into a list | **partly** — see non-transfer #1 |
| Output sink | one `Output { w: *mut dyn fmt::Write, target, capture_stack }`; `write_str` writes to the target | `output.rs:22-26`, `:99-101`, `:84-88` | one pointer indirection per write | `"".join([node.render_annotated(c) for node in self])` (`base.py:1154-1155`); `ForNode` appends to a list and joins (`defaulttags.py:207,256`) | **yes** |
| Capture (blocks, `filter`/`{% filter %}`) | a `capture_stack: Vec<Option<String>>`; `retarget()` swaps the writer pointer, no re-entry | `output.rs:51-81` | one `String` per capture level | Python recursion into a fresh `NodeList.render` | **yes** |
| Output preallocation | `String::with_capacity(buffer_size_hint)`, hint = `(raw_template_bytes * 2).next_power_of_two()` computed at compile | `template.rs:243`; `codegen.rs:1039-1045` | one alloc at render, sized from the source | none — we build a list of Python strings and join | **yes**, cheap |
| Loop control flow | `PushLoop(flags)`; `Iterate(jump_target)` pushes the next item or sets `pc = jump_target`; `PopLoopFrame`; no recursion | `vm/mod.rs:645-663`, `:1139-1183` | one `Value` pop from a `ValueIter` | `for node in self.nodelist_loop:` inside `ForNode.render` (`defaulttags.py:249-251`) | **yes** |
| Loop variable | one `StoreLocal(name)` → `ctx.store(name, item)` → `top.locals.insert(key, value)`, plus `frame.locals.clear()` at the top of the next iteration | `vm/mod.rs:405-412`, `:655-663`; `context.rs:218-230`, `:396-407` (`clear` at `:404`) | one map insert + one clear per iteration | `context[self.loopvars[0]] = item` (`defaulttags.py:248`) — one insert, but into the *public* Django `Context` | **yes**, same op, cheaper substrate |
| `loop.index` etc. | **not stored.** `loop` is synthesised on lookup: `if l.with_loop_var && key == "loop" { Value::from_dyn_object(l.object.clone()) }`; the `Loop` object computes `index0/index/revindex/first/last/depth` from an `AtomicUsize` and a length | `context.rs:293-297`; `loop_object.rs:193-230` (values), `:171-187` (keys) | one `Arc` bump per `loop` access; `len` from `size_hint` (`:37-40`); `cycle`/`changed` are `call_method` (`:144-169`) | `loop_dict` dict, 6 stores per iteration, unconditionally | **conditional** — see lesson 1 |
| `for` iteration | `ValueIter` is an enum (`Empty`/`Chars`/`Dyn(Box<dyn Iterator>)`), not a `Box` per item | `value/mod.rs:2110-2112`, `:2268-2272` | one boxed iterator per loop entry for object sequences | Python iterator protocol | **yes** |
| Value representation | 24-byte enum (`value/mod.rs:2371`); inline `U64/I64/F64`, `SmallStr` inline ≤22 bytes (`:542-549`), `Arc<str>` for longer, `DynObject` = hand-rolled 16-byte fat pointer (`type_erase.rs:14-17`) | `value/mod.rs:577-591` | 24 bytes copied on every stack push | `PyObject` — see non-transfer #2 | **no** (the repr); **yes** (the discipline) |
| Filter dispatch | `ApplyFilter(name, argc, local_id)` → `get_or_lookup_local(&mut loaded_filters, local_id, …)` into a `[Option<&Value>; MAX_LOCALS]` array, `MAX_LOCALS = 50`, `local_id == !0` means "don't cache" | `instructions.rs:148`, `:22`; `vm/mod.rs:262-263`, `:62-75`, `:712-729` | one array index vs one `BTreeMap` lookup on the filter name; the call itself is still an indirect call | filter-routing cache (`base.py:820-828`, `_rust_names`), keyed per compiled `FilterExpression` | **yes** — and ours caches a *route*, theirs caches the *callable* |
| Filter storage | `filters: Arc<BTreeMap<Cow<str>, Value>>`; a filter is a `Value` wrapping a boxed closure behind a vtable | `environment.rs:71`, `:794-796`; `functions.rs:353-360` (`BoxedFunction`), `:395-399` (`Object::call`); `value/mod.rs:1897-1899` (`Value::call`) | 2 indirect calls + `ArgType` extraction per filter call — **filters are *not* inlined** | `_native.filter` (Rust) for the 66 builtins, Django's callable otherwise (`base.py:862-880`) | **yes** — we are already ahead |
| Variable lookup | **by name, at runtime.** `Instruction::Lookup(&'source str)` → `State::lookup` → `Context::load`, which walks frames newest-first checking `frame.locals.get(key)` (a `BTreeMap<&str, Value>`), the `loop` special case, then the root `ctx.get_attr_fast(key)` | `vm/mod.rs:413-418`; `state.rs:276-282`; `context.rs:281-314` | a string compare per frame per lookup | `_native.resolve_lookups` (`base.py:1092`) behind `Python::attach` | **partly** — see the falsified-assumption note |
| No slot indices | **search that falsified it:** `grep -rniE "intern\|name_index\|var_index\|VarIndex\|debruijn\|slot" minijinja/src/compiler/ minijinja/src/vm/ minijinja/src/template.rs` → only `internal_debug`/`Internal` noise. Names stay `&'source str` through to the instruction. | `codegen.rs:681` (`Instruction::Lookup(v.id)`), `ast.rs` `Var { id: &str }` | name lookup per variable per render | same (we resolve by name) | **no** — the mechanism does not exist here to copy |
| Escaping | `write_escaped` dispatches on `AutoEscape`, then `write_with_html_escaping` has a native `u64/i64` fast path and a "needs escaping at all" byte scan before falling to a byte-wise escaper | `utils.rs:186-199`, `:123-165` (`u64`/`i64` fast path `:124-137`, integer-string path `:144-149`, scan `:151-156`), scan `:112-121`, table `:404-412` | a scan + a branch per emit | **landed**, slice 1: exact-`str` fast path into `_native.html_escape` with a `None` refusal (`base.py:1201-1204`) | **shape yes, table no** — see non-transfer #5 |
| Autoescape is *not* a compile-time decision | **search that falsified it:** `grep -rn "EmitSafe\|EmitEscaped\|EmitHtml\|EscapeHtml\|Instruction::Escape" minijinja/src/` → 0 hits. `Instruction::Emit` consults `state.auto_escape` at runtime; only the *initial* value is computed at compile, from the file extension | arm `vm/mod.rs:392-404`; `defaults.rs:30-45`; `template.rs:446`; `Push/PopAutoEscape` only when `{% autoescape %}` is used (`codegen.rs:371-379`) | a runtime branch per emit | ours is also runtime (`context.autoescape`, `base.py:1201`) | **no change needed** |
| Constant folding | `compile_expr` folds a whole subtree if `Expr::as_const()` yields a `Value`: `Const`, `List`, `Tuple`, `Map`, `UnaryOp` (`not`/`-`), `BinOp`, `Compare` | `codegen.rs:667-672`; `ast.rs:214-243` | nothing at render; some compile work | `FilterExpression` already holds literal values | **marginal** |
| Compile-time hoisting in the loop | `initial_auto_escape`, `undefined_behavior`, `strict_undefined` read once before the loop | `vm/mod.rs:233-238` | — | per-render Python attributes | **yes** |
| State stack | `Stack { values: Vec<Value> }` with `Vec::with_capacity(24)` | `context.rs:77-88` | one alloc per render | Python locals | **yes** |
| Template reuse | `Environment::get_template(name)` → `LoaderStore::get` → `BTreeMap<&str, Arc<CompiledTemplate>>`, returns a **borrowed** compiled object | `environment.rs:387-390`; `loader.rs:122-128`, `:325-358` | one map lookup per include/extends | loader-cache probe: `micro/extends` 30.5 → 9.45 µs cached (`.review/m19-measurements/loader_cache_probe.txt`) | **yes** |
| `{% include %}` | resolves the name, `with_execution_state` swaps instructions, recurses `eval_state` into the **same** `out` | `vm/mod.rs:928-990` (`get_template` `:951`, `with_execution_state` `:972`); `state.rs:161-235` | a recursive `eval` + a fresh `prepare_blocks` `BTreeMap` (`vm/mod.rs:52-60`) | `IncludeNode` re-parses per render unless the loader caches | **yes** |
| `{% extends %}` | `LoadBlocks` stashes the parent's instructions; at the end of the child's stream the loop **swaps `state.instructions` and sets `pc = 0`, then `continue`s** — no recursive render | `vm/mod.rs:299-318`, `:840-850`; `load_blocks` `:1056-1083` | one extra pass with `out.begin_capture(CaptureMode::Discard)` | `ExtendsNode.render` walks the parent nodelist **twice** (`engine/loader_tags.py:148,154`, per spec §1.3) | **yes** — this is the cheapest big win |
| Blocks / `super()` | `blocks: BTreeMap<&str, BlockStack>`, a per-name stack of instruction streams with a `depth` cursor; `{% block %}` is `CallBlock(name)`, skipped while discarding | `state.rs:644-683`; `vm/mod.rs:869-873`, `:1086-1105`, `:1006-1053` | a map lookup + a `push`/`pop` cursor | Python `BlockNode` with `render_context` | **yes** |
| Their own benchmark choices | render-to-`io::sink` to isolate engine cost from output; a 10 000-row `items` loop; a 200-iteration tuple loop; compile and render measured separately (compile 2.46 µs vs render 2.23 µs on `all_elements.html`) | `benchmarks/benches/templates.rs:40-65`; `benchmarks/README.md` | — | `scripts/bench.py` render/parse modes | **yes** — see lesson 8 |

## Ranked lessons for our walk

Ordered by value to *our* walk. Each states the mechanism, so it can be argued
with. Line numbers in our tree are against `main` @ `b782f54`.

**1. The loop body must not rebuild a Python dict per iteration.**
*Mechanism:* minijinja stores the loop variable **once** per iteration and keeps
the counters in a `Loop` object that is only materialised when the name `loop` is
looked up (`vm/mod.rs:405` + `context.rs:293-297` + `loop_object.rs:193-230`).
Django's `ForNode.render` — and our transcription — writes `counter0`, `counter`,
`revcounter`, `revcounter0`, `first`, `last` into a dict **unconditionally**, six
stores per iteration, whether or not the body ever reads `forloop`
(`defaulttags.py:214-227`), and the measured consequence is the row that started
this milestone: `micro/loop-text` per-iteration 0.219 µs ours vs 0.041 jinja2
(`.review/m19-measurements/loop_decomposition.txt`), i.e. ~0.18 µs/row that is
pure bookkeeping, on a loop with **no variable in the body at all**.
*Our version:* the walk keeps a native loop frame (item, index, length) and
computes `forloop.counter` etc. on demand; `context["forloop"]` is materialised
as a real `dict` only at the moment a node we do not own can observe it.
*Hazard, and it is the reason this is not free:* Django's `forloop` is a **public
dict in a public `Context`**, and a third-party tag (or `{% include %}`'s
context push, or `{% with %}`) can read it. minijinja has no such obligation —
its `Context` is `pub(crate)`. So the elision is only sound when the loop body
contains **nothing** outside the delegated set: it is a compile-time question
answered by the delegation analysis in spec §3.4, not a property of the walk.
The `real-world/*` rows are exactly where an `{% include %}` sits inside a loop,
so this must be gated per-loop-body and reported as a column, not assumed.

**2. Output is written into one buffer, never collected and joined.**
*Mechanism:* `Output` holds a raw `*mut dyn fmt::Write` and `write_str` forwards
to it (`output.rs:22-26`, `:84-101`); the whole template writes into a single
`String` pre-sized from the source (`template.rs:243`). Capture — for blocks,
`{% filter %}`, `{% macro %}` — pushes a `String` and re-points the target
(`output.rs:51-81`), so even a capture does not re-enter the walk.
*Ours:* `NodeList.render` is `SafeString("".join([node.render_annotated(context)
for node in self]))` (`base.py:1154-1155`); `ForNode` builds a list and
`mark_safe("".join(nodelist))` (`defaulttags.py:207,256`). Per node that is one
list append and one Python frame; per nodelist, one `join`.
*Lesson:* the Rust walk owns the sink; a value that is an exact `str` is a
`push_str`, a delegated node's return value is one `push_str` of its `str()`, and
the `SafeString` wrapper is applied **once** at the outer boundary rather than per
nodelist. This is the change that makes `micro/text` and `micro/loop-text` stop
paying a Python frame per node (`spec:361` prices those at 0.16 µs/node).

**3. Control flow is a jump target on a flat plan, not a recursive tree walk.**
*Mechanism:* `Iterate(jump_target)` is a *forward* `pc` assignment when the
iterator is exhausted (`vm/mod.rs:655-663`); `Jump`/`JumpIfFalse` likewise
(`:669-695`); there is no call/return per loop or per `{% if %}`. Blocks and
macros are the only things that recurse into `eval_state`, and they do so by
swapping `state.instructions`.
*Ours:* the M18.0 spike already built both forms and measured the difference —
`plan_nodelist` → `Vec<Op>` walked by `render_floor` vs `render_desc` walking the
descriptor tree by `class_name` string compare (`crates/template-py/src/lib.rs:2820-2863`,
`:2956`, `:3182`). **The spike's own result is the caution**: the flat floor still
cost 0.936–0.948 µs/variable (spec §1.3, `:228`), because the floor inherits the
two Python calls it shares with `render_desc` (the resolver and
`render_value_in_context`, `lib.rs:2834-2838`). So the flat plan is necessary and
**not sufficient**: it only pays once steps 1/3 (`LookupTarget` takes `py`,
natively-resolved lookups) have removed those calls. Do not schedule the plan as
the win; schedule it as the thing that makes the other wins reachable.
*And the plan is a parse-mode cost:* built at compile time it is invisible in
`render` mode (amortised over `iters`) and fully visible in `parse` mode, which
compiles every iteration — the hazard spec §4 item 9 and §5.2 name explicitly.
Report the render/parse **pair** for this slice.

**4. `{% extends %}` must be an instruction swap, not a second render.**
*Mechanism:* minijinja stashes the parent's `Instructions` at `LoadBlocks` and,
at the end of the child's stream, does `state.instructions = parent; pc = 0;
continue` in the same loop (`vm/mod.rs:299-318`), with the child's non-block
output captured to a `Discard` sink (`:849`). Blocks resolve by name through a
`BTreeMap<&str, BlockStack>` with a depth cursor for `super()`
(`state.rs:644-683`, `vm/mod.rs:1006-1053`).
*Ours:* `ExtendsNode.render` walks `compiled_parent.nodelist` **twice**
(`engine/loader_tags.py:148`, `:154`) — so slices 2–3 benchmark as a regression on
exactly the `real-world/*` rows the spec headlines (spec §1.3, `:232`). Inheritance
also cannot be deferred: until it is native, every realistic-page render
materialises the parent tree.
*Mechanism to copy:* a block-name → instruction-range table on the compiled
object, and a parent pointer the walk follows by `pc = 0` rather than by calling
back into Python. This is the single cheapest large win on the three
`real-world/*` rows.

**5. The compiled object must be cached by template identity, and the walk must
be reused across renders.** *Mechanism:* minijinja's `Template` is a *handle* —
it holds `&Environment` plus a borrowed `CompiledTemplate` (`template.rs:52-55`),
and `get_template` is a `BTreeMap` lookup returning a borrow (`environment.rs:387`,
`loader.rs:325`). Nothing about a compiled template is rebuilt per render except
the `String` and the `State`.
*Ours:* `micro/extends` 30.5 → 9.45 µs with a cached loader
(`.review/m19-measurements/loader_cache_probe.txt`) — the number is already in
hand and it is mostly parse-per-render.
*Mechanism to copy:* key the compiled walk object on the *same* identity the
loader already uses, hand it back by reference, and make `{% include %}` a lookup
into that cache rather than a compile. Note this is also what keeps lesson 3's
plan cost off the per-render path.

**6. Escape and filter work belongs *inside* the walk, and the per-site
resolution is cached, not repeated.** *Mechanism:* two separate things —
(a) minijinja's emitter does the whole job natively: type test, "needs escaping"
scan, then a byte-wise escape that writes into the sink (`utils.rs:123-165`);
(b) the *resolved filter* is cached per instruction site in a
`[Option<&Value>; MAX_LOCALS]` array indexed by a compile-time `u8`
(`vm/mod.rs:262-263`, `:62-75`), so the name→callable `BTreeMap` lookup happens
once per render per site, not once per use.
*Ours:* (a) landed as slice 1 (`base.py:1201-1204`) but still costs a Python
frame; (b) landed as a *route* cache (`base.py:820-828`) which answers "is this a
builtin" per compiled `FilterExpression`, where minijinja caches the callable
itself.
*Mechanism to copy:* make the plan's filter entry a resolved enum
(`Builtin(FilterId)` / `Delegate(PyObject)`) instead of a name, so neither the
lookup nor the frame happens per use. Note minijinja still pays 2 indirect calls
per filter (`Value::call` → vtable → closure, `value/mod.rs:1897`, `:395-399`):
the win is not "inline the filter", it is "stop re-resolving it".

**7. Hoist the render-invariant decisions and size the sink at compile time.**
*Mechanism:* `initial_auto_escape`, `undefined_behavior` and `strict_undefined`
are read once before the dispatch loop (`vm/mod.rs:233-238`); the output
`String`'s capacity is a compile-time constant derived from the source byte count
(`codegen.rs:1039-1045`). *Ours:* `context.autoescape` and friends are Python
attribute reads on every variable. *Mechanism to copy:* compute the walk's
per-render constants once at walk entry (this is what our `_rust_names` guard
already does for the filter table) and carry them in a Rust struct for the whole
walk.

**8. Measure render-to-a-null-sink, and measure compile separately.**
*Mechanism:* minijinja's loop benchmarks render to `std::io::sink()`
(`benchmarks/benches/templates.rs:44-50`), which isolates engine cost from output
allocation; and their bench reports `compile` and `render` as separate rows,
which is why the compile/render ratio is visible at all (2.46 vs 2.23 µs on their
`all_elements.html`, `benchmarks/README.md`). *Ours:* `scripts/bench.py` already
has the render/parse pair and that is the right instrument for lesson 3's hazard;
the additional value here is a null-sink row, which would separate "the walk got
faster" from "the join got cheaper" when lessons 1 and 2 land together.

## What does NOT transfer

These are choices that are available to minijinja and unavailable to us, named
specifically rather than as "we have Python".

1. **`&'source str` instructions.** `Instructions<'source>` borrows the template
   source, so `EmitRaw(&'source str)` is a pointer into it
   (`instructions.rs:49`, `:279`) and a text chunk costs nothing to compile. Our
   source arrives as a Python `str` owned by Python, and a compiled object that
   outlives the render cannot borrow it. Our `Instruction`-equivalent must own
   its text (`Arc<str>`/`String`; the descriptor already stores
   `AttrValue::Str(String)`), so text is copied once at parse and a shared text
   chunk is a refcount bump per render. This is a small tax, not a blocker — but
   it means we cannot copy the lifetime signature, and any plan that stores
   `&'source str` will not compile.

2. **A native context and a native value.** minijinja's `Value` is a 24-byte
   Rust enum and the context is a native map: `Frame.locals` is a
   `BTreeMap<&'env str, Value>` (`context.rs:13`), the root context is a
   `ValueMap` = `IndexMap<Value, Value>`/`BTreeMap<Value, Value>`
   (`value/mod.rs:330`, `:333`), and `loop.index` is `Value::from(idx + 1)` — an
   inline `u64`, no allocation, no refcount (`loop_object.rs:202-204`). **Every
   value in our walk that came out of the context is a `PyObject`**, so: a lookup
   is a Python `dict` walk behind `Python::attach` (`spec §3.1` item 2); binding
   a loop variable into a Rust frame is at minimum an incref and its drop is a
   decref; and `forloop.counter` the moment anything reads it is a `PyLong`. The
   honest form of lesson 1 is therefore *"do not create the ints at all when
   nothing reads them"*, not *"make creating them cheap"* — the cheap version is
   unavailable to us. Likewise `SmallStr`'s 22 inline bytes
   (`value/mod.rs:542-549`) have no analogue.

3. **A private context.** minijinja's `Context`/`Frame` are `pub(crate)`
   (`context.rs:132`), so no filter, function or extension written by a user can
   observe the frame stack, and the engine is free to keep the loop variable out
   of any user-visible structure. Django's `Context` is the API third-party tags
   are *written against* — `context["forloop"]` is a dict, `context.push()`/
   `pop()` are load-bearing, and our 1547-test oracle exercises them. This is
   what makes lesson 1 *conditional*: the substitute for "invisible state" is
   "materialise on first observation by a node we do not own", which is a
   property of the delegation set, not of the walk. It also means we cannot adopt
   minijinja's `with_execution_state` (a save/restore of the whole
   instructions/blocks/autoescape set, `state.rs:161-235`) as a wholesale swap:
   the Django `Context` must be *in* the state we swap, or a delegated node inside
   an `{% include %}` sees the wrong `dicts` stack.

4. **Escaping semantics.** minijinja's table escapes `/` → `&#x2f;`
   (`utils.rs:404-412`), via a fast pre-check that treats `/` as an escaping
   trigger (`utils.rs:112-121`). Django 6.0.9's `escape` is
   `SafeString(html.escape(str(text)))` (`.django/django/utils/html.py:51-60`),
   and `html.escape` does not touch `/`; our escape is
   `FilterHost::conditional_escape`. minijinja also renders `True`/`False`/`None`
   for bools and `None`. So the *table* and the trigger set cannot be copied —
   only the *shape* (an exact-type fast path, a "needs escaping at all" scan, the
   write straight into the sink), which slice 1 already landed. Copying the
   minijinja scan verbatim would flip `{{ "a/b" }}` from `a/b` to `a&#x2f;b`
   against a hard byte-identical requirement.

5. **`{{ }}` with no `localize` step.** minijinja's `Instruction::Emit` is
   `write_escaped(out, &state.auto_escape, &value)` and nothing else
   (`vm/mod.rs:392-404`). Ours must run Django's `localize` first, which is why
   spec §1.5's `int` row exists at all (3.34 → 0.39 µs). A per-emit native path
   therefore has to be gated on a predicate that is *wider* than minijinja's
   value-kind test: exact `str`, and separately the `int`/`localize` case. The
   minijinja emitter cannot be transcribed; only its structure can.

6. **No GIL, no `PyObject` in the instruction stream.** minijinja's `Value`
   is `Send + Sync` and its `Loop` uses `AtomicUsize`/`Mutex`
   (`loop_object.rs:112-120`) because a `Value` may escape to another thread. Our
   instructions cannot hold a `Py<PyAny>` for a long-lived compiled object
   without pinning a `Python` token and a drop path, and adding atomics to
   something render-local would be a cost with no benefit (our render is
   single-threaded under one GIL). Do not copy the `AtomicUsize`; a plain
   `Cell`/`usize` in a render-local frame is enough.

7. **Their benchmark harness's *context*.** `context!{ … }` builds a
   `ValueMap` at the Rust level (`benchmarks/benches/templates.rs:19-38`), so
   their `real-world`-shaped rows never cross into a host language for a variable.
   `scripts/bench_corpus.py` renders the same shapes through Django's `Context`.
   The gap on the `real-world/*` rows is therefore not one mechanism but the sum
   of lessons 1–5, and no single minijinja mechanism closes it.

## Unverified claims

- **"rustc lowers the `match` to a jump table."** I did not compile minijinja or
  read MIR/asm. The verified facts are that the enum has no `#[repr]`
  (`instructions.rs:46-47`) and that no computed-goto-like construct exists in
  the source (`grep -rniE "computed_goto|jump_table|threaded|direct_threading"
  minijinja/src/` → 0 hits). The "jump table" in spec §2's table (`spec:363`) is
  my description of what a `match` on a 60-variant enum normally becomes, not a
  measured property of this build.
- **Per-instruction cost.** I did not benchmark minijinja. "~5–20 ns per
  instruction" appears nowhere in this report for that reason; the only numbers
  quoted are theirs (`benchmarks/README.md`) or ours (`.review/m19-measurements/`).
- **The exact per-iteration instruction count** for `{% for r in rows %}{{ r
  }}{% endfor %}` (I reasoned it to `Iterate`, `StoreLocal`, `Lookup`, `Emit`,
  `Jump` from `codegen.rs:204-236`, `:644-656`, `:667-690`). It is a
  reconstruction from the compiler, not a disassembly.
- **`with_loop_var` is always true.** `compile_for_loop` calls
  `start_for_loop(true, …)` in both branches (`codegen.rs:190`, `:203`), and the
  only reader of the flag is `get_referenced_names` (`instructions.rs:501`). I did
  not find a pass that clears it, but I did not search the whole repo for one
  either; if one exists the `loop` special case is dead in practice.
- **`prepare_blocks` is per-render.** Read from the call sites I found
  (`template.rs:339`, `vm/mod.rs:972`) and the function body (`vm/mod.rs:52-60`);
  I did not trace every entry point (`call_block`, macros) to be sure.
- **`ValueIter` cost for a `Vec<Value>`.** `ValueIterImpl::Dyn(Box<dyn
  Iterator>)` (`value/mod.rs:2271`) is a boxed iterator, so I inferred one box per
  loop entry for an object sequence; I did not confirm what `Value::from(vec)`
  produces or whether a `Vec`-backed sequence takes a cheaper `Enumerator`
  variant.
- **minijinja's `Instruction` size test is `#[cfg(target_pointer_width = "64")]`**
  (`instructions.rs:567-570`), so the 32-byte figure is a 64-bit claim only.
- **The claim that Django's `html.escape` does not escape `/`** is verified from
  the pinned `.django/` at 6.0.9 (`.django/django/utils/html.py:51-60` calls
  `html.escape`); I did not read CPython 3.14's `html.escape` source in this
  session — I am relying on its documented table, which is a claim about the
  interpreter rather than about this repo.
- **Nothing here was run.** No `maturin`, no test suite, no benchmark, per the
  brief. All our-side numbers are quoted from files already in the tree.
