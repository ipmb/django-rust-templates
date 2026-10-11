# The native IR and the compatibility nodes

**Status:** design, not approved. Written 2026-10-10 against `7ab2422`.

**Scope:** the compile path of `django-rust-templates`. The render path is out of
scope: it is already won (rust/stock 0.33–0.79× on the render row), and nothing
here may make it slower.

**Why this document exists.** A first proposal to take the compile step off the
"build the whole Python tree, then plan against it" shape was sent to adversarial
review and failed on two blocking points. The second round asked whether the
*destination* was right or only the transport, and returned: the destination is
agreed — **immutable IR, bound refs, a native plan, and identity-memoised
compatibility nodes at the delegation boundaries** — but it is a *coupled*
rewrite, not a layer stacked on the current one, and it requires three contracts
written down before any code: lifetime, planning, error. This is that document.

Three earlier claims of mine are withdrawn and are recorded here only so the
argument is not rebuilt on them:

1. **"74–94% of compile is the Python object graph."** Wrong. `_native.parse`
   also lexes, parses, allocates Rust descriptors and calls back into Python, so
   summing it with materialisation measured four things at once.
2. **"Defer building `Parser`/`ShellHost`."** Dead. `ShellHost` does not snapshot
   `parser.tags` — the docstring in `render.py::parse_template` that says it does
   is stale and is what misled me — and Rust consults `self.host.has_tag(&command)`
   before *every* block-tag dispatch, native tags included
   (`crates/template-core/src/parser/mod.rs:421`), while `compile_template`
   resolves `tree.check_filter`/`compile_filter` against the parser *before*
   parsing. Nothing is deferrable but `django_own_tags()` on a template with no
   tags at all.
3. **"`compile_template` already retains the descriptor tree for the plan."**
   It retains it *within* the call and drops it on return. Retention is new work
   (§4).

A third review round attacked the contracts themselves and returned six more
findings, all of which are folded in and four of which changed the design rather
than the wording:

- **The `__clear__` fallback was an empty tree.** Wrong direction for a template
  engine; §4.2 now specifies a terminal `Cleared` that fails loudly.
- **`Failed` did not cover the error path.** Leaving an error-path projection
  `Absent` resurrects the corrupt-retry this design exists to prevent; §6.3c now
  journals and rolls back the ref-table writes, and the lost annotation is chained
  rather than dropped.
- **"Construction stays live" was a hole, not a safeguard.** Plan time and build
  time are no longer the same instant, so live construction newly *observes* a
  registry replacement; §5.1 now freezes construction for a lazy template.
- **`normal` was listed as lazy while carrying a delegate**, contradicting §5.2's
  own rule, and laziness there would only move work. The corpus claim is now
  `large` alone — and §5.2 states the memory/latency measurement that could kill
  the design outright, which no earlier draft named.

---

## 1. The measured problem

Everything below is `real-world/large` — 9279 characters, 621 nodes — because it
is the row the benchmark's `parse` mode is thinnest on (rust/stock 0.918), and
because compile is 1895 of that row's 1995 µs.

Compile decomposes as:

| component | µs | how it was measured |
|---|---|---|
| Python decoding the descriptor wire | ~904 | `probe_materialise_split.py` — wrap every `NODE_CLASSES.__init__` |
| the rest of the parse step | ~700 | **residual** — see the caveat below |
| Python node constructors' own bodies | ~126 | same wrapper: time *inside* `__init__` |
| the plan | ~115 | `plan_list` + `plan_block_bodies` — **method unrecorded, see below** |
| `parser_for` and `template_for` | small | `parser_for` is 1.30 µs since `7ab2422` |

**One row of that table is a subtraction, not a measurement, and round-2 review
was right to say so.** The ~700 is "whatever is left of the parse step once the
constructors are removed", and the parse step contains the Rust lexer, the Rust
parser, descriptor allocation and the Python callbacks — not only wire
serialisation. So "~1600 µs of representation overhead" overstates by exactly the
part of that residual which is genuine parsing work. What is *measured* is ~904 µs
of Python-side decode and ~126 µs of constructors; the 700 is the size of the
prize's upper bound, not a decomposition of it. S1's benchmark (§9) is what
replaces this number with a measurement.

**A second row cannot be reproduced at all, and the S1 plan says so rather than
inheriting it.** The ~115 µs plan figure names the two functions it is attributed to
and no procedure, and none can exist from Python: `plan_list` (`lib.rs:3918`) and
`plan_block_bodies` (`:4017`) are private to `compile_template`, so the only
Python-visible quantity containing them is the whole `stubbed` total, which also
contains the parse, the wire and `template_for`. A later probe took the plan as a
residual over `large` and got 25.5 µs — five times smaller, from an instrument that
cannot attribute what it measures. **S1's Task 1 Step 5 records the figure as
unverified and carries it forward as unverified** (`docs/superpowers/plans/2026-10-10-native-ir-compatibility-s1.md`);
S3 owns it, and can time it where it lives. Nothing in §9's kill-criterion or in
S2 should be read off ~115 µs until then.

`_native.parse` itself is linear in two axes, fitted on two synthetic templates
that move them independently (9279 chars of plain text → 1 node; `{{ x }}`×310 →
2170 chars, 621 nodes):

| term | fit | on `large` |
|---|---|---|
| per character | 0.0037 µs/char | 35 µs — 5% |
| per node | 0.88 µs/node | 550–695 µs |
| Python callbacks inside the parse (20 `compile_filter` operands) | measured by shadowing | 65 µs — 8.6% |

**And the tree that all of this buys is, on this row, never read.** Counting
nodes built against nodes render-dispatched — patching
`django_rust_templates.engine.base.Node`, *not* `django.template.base.Node`, which
is a different hierarchy and reports zero everywhere and is silently meaningless:

```
real-world/large   621 built = 321 Text + 260 Var + 20 If + 20 For,  all plan-owned,  0 dispatched
real-world/normal   29 built,  ~26 plan-owned,  4 dispatched
real-world/complex  16 built,  IncludeNode + ExtendsNode,  79 dispatched (include recursion)
```

Dispatch happens only at a delegation boundary.

---

## 2. What the plan is today, and why it cannot simply be made lazy

The current shape is:

```
source ──lex+parse──▶ NodeListDesc (Rust, local)
             │
             ├─ nodelist_desc_to_py ─▶ Python tuple wire ─┐
             │                                             │
             └─ (desc kept in-scope)                        ▼
                                                    materialise ─▶ NodeList
             │                                             │
             └──────── plan_list(&desc_tree, &nodelist) ───┘ ─▶ NativePlan
```

**The arrow that matters is the one into `plan_list`.** The plan is derived
*from the materialised tree*, not from the descriptor:

- `plan_list` refuses unless `nodelist.len() == desc.nodes.len()`
  (`lib.rs:3923`) and then reads the tree positionally (`lib.rs:3928`).
- `plan_slot` reads `type(node).__name__` to pair a descriptor with a node
  (`lib.rs:3877`).
- `plan_for` reads `node.sequence`, `node.is_reversed`, `node.loopvars` and both
  child nodelists off the built `ForNode` (`lib.rs:3842–3866`).
- `plan_if` reads `node.conditions_nodelists` (`lib.rs:3903`).
- `plan_block_bodies` descends the *node* tree and writes `_native_plan` onto
  built `NodeList` objects (`lib.rs:4017–4065`).
- `plan_steps` reads the materialised `FilterExpression` — and says why: "the
  *function* each name was bound to (`FilterCall::handle` is a reference-table
  index, and the table is not reachable from a plan)" (`lib.rs:3603`).

So "make `nodelist` lazy underneath the existing plan" does not work. The plan
needs the tree, so anything that builds the plan builds the tree. **The
dependency has to be inverted first, or there is nothing to make lazy.**

That inversion is the whole of this design:

> **The plan is built from `(IR, ref table)`. The Python tree is a projection of
> the plan's input, memoised, and forced only where a Python object is genuinely
> required.**

---

## 3. The target architecture

```
                     ┌────────────────────────────────────────────────┐
   source ──parse──▶ │ CompiledTemplate              (#[pyclass])     │
                     │   ir:      Arc<NodeListDesc>    immutable      │
                     │   refs:    Py<PyList>           the parse's    │
                     │   eligible: {name -> factory}   §5.1 — gate    │
                     │   origin, source, engine                       │
                     │   plan:    Option<NativePlan>   from the IR    │
                     │   lazy:    bool                 §5.2 — at build │
                     │   projection: Absent|Building|Ready           │
                     │              |Failed|Cleared    §4.1, §4.2      │
                     └────────────────────────────────────────────────┘
                            │                             ▲
                 .nodelist  │  (memoised, forces once)    │ plan is a pure
                            ▼                             │ function of ir+refs
                        NodeList  ── materialise(ir) ────┘
```

`NativePlan` loses its per-slot Python handle. `Slot` becomes:

```rust
struct Slot {
    act: Act,
    class_name: String,
    /// The **occurrence** of the node this slot stands for (§3.1). Present on
    /// every slot, not only on delegates — see §6.2.
    node_id: NodeId,
}
```

and `Act::Delegate` becomes a bare marker with no target of its own — it renders
whatever `Slot.node_id` names, like every other arm.

### 3.1 `NodeId` is an occurrence of the IR, not a step of the projection

The first draft of this section defined `NodeId` as "the index of a node in the
deterministic materialisation order, because `materialise_node` builds children
before parents". **That is wrong, and round-3 review was right to call it
blocking.** The materialiser's order is *not* a function of the IR, because
materialisation short-circuits:

- `parser.py:317–347` — the host-node arm returns a node **the parse already
  built** and stored in the reference table. Nothing is constructed at this
  position.
- `parser.py:366–372` — a `{% cycle %}`'s second and later occurrences return
  `existing` **before** any child traversal, so a subtree that would have been
  walked is not walked.
- `{% partialdef %}`'s body was materialised *during the parse*, so its nodes
  predate the projection entirely.

A vector indexed by construction order therefore cannot align with an IR position,
and the failure mode is a delegate call rendering the wrong node — silently.

`NodeId` must instead be assigned to **every `NodeDesc` occurrence in the IR,
independently of execution**. A pre-order walk of the IR numbers each occurrence;
that numbering is fixed the moment the parse ends and is a pure function of the
tree. The projection then records, for each occurrence it produced, the object it
produced — **including aliases**: a cycle's second occurrence records the same
`Py<PyAny>` as its first, and a host node records the object the table holds. The
map is `occ -> Py<PyAny>` and it is many-to-one exactly where the materialiser is.

The distinction that matters: **occurrence identity is what the plan names;
object identity is what the projection records.** Three aliasing cases have to be
tested explicitly (§10.7), because each is a place where the two differ.

**Skipped descendants, which round-3 review's second pass is right to demand.**
An early return does not merely alias one occurrence — it skips an entire
subtree. When `{% cycle %}`'s second occurrence returns `existing` at
`parser.py:370`, none of its descendants are constructed at that occurrence, so a
naive `occ -> object` map has **holes**, and a slot that names a hole indexes
nothing.

The rule, and it is a rule rather than an incidental detail:

> **No slot may name an occurrence whose object the projection did not produce.
> Every other occurrence in the same subtree resolves through its nearest
> produced ancestor.**

This is well-defined because the IR is a tree and the skipped subtree has exactly
one entry point — the occurrence that returned early — whose object *was*
produced (it is the object that was returned). So the fill is: when the projection
records occurrence `o` as an alias of object `x`, it records `o`'s whole IR subtree
by descending `x`'s already-built children, which are the same objects the first
occurrence built. That walk is over built objects only, it costs nothing on the
happy path because it only runs when a projection runs, and it is the
projection's own traversal rather than a second materialisation.

**The fill is over IR occurrences and never invents one.** Round-3's third pass
asked for this to be said plainly, because the two halves of the rule pull in
opposite directions. An occurrence exists the moment the parse ends, whether or not
the projection produced an object for it; what a short-circuit denies is the
*object*, not the numbering. And a host node — `call_tag`'s, `partial_node`'s, the
`{% cycle %}` whose object came from the table — contributes exactly the
descendants its `NodeDesc` has, no more: the IR is the only source of occurrences,
and the fill descends it rather than walking the Python node's `child_nodelists`,
which is a different graph and can be a superset.

The plan, meanwhile, must not *depend* on the distinction: a slot inside a shared
subtree names its own occurrence, which the fill has populated. What the plan
must never do is name an occurrence outside any materialised subtree, and by
construction it cannot — every `Slot` comes from a `NodeDesc` the projection also
visits.

**On `large` this plan holds zero Python nodes** — but, corrected by §6.2, that
is a statement about *holding*, not about *capability*. All 621 slots are
Text/Emit/If/For, `plan_head` is already descriptor-driven (`lib.rs:3567`, "read
from the descriptor … the unfiltered case is the common one"), and `ForPlan.sequence`
and `Emit.head`/`steps` hold *values* and filter callables rather than nodes. No
node object is retained. The projection is still *reachable* from any slot and is
forced on demand.

### 3.2 What has to become descriptor-driven

| today | after | note |
|---|---|---|
| `plan_slot`'s `type(node).__name__` pairing | `node_desc.class_name` **plus the factory registry at compile time** | the descriptor is authoritative for *shape*; the registry gates *eligibility* — see §5.1 |
| `plan_for`'s node attribute reads | `ForDesc` fields | `sequence` is a `FilterExprDesc`, resolved through the ref table |
| `plan_if`'s `conditions_nodelists` | the condition descriptors | ditto |
| `plan_steps`' `FilterExpression.filters` | `FilterCallDesc.handle` resolved through `refs` | the handle *is* the ref index; this is the read the current comment says is impossible only because the plan does not hold the table — after §4 it does |
| `plan_block_bodies`' node-tree descent | an IR descent, planning each block body | the bodies are still built as `NodeList`s at projection time; what changes is that the *plan* for them is computed without them |

---

## 4. Contract: lifetime

**Question the review asked:** who owns the descriptor and the ref table once
`compile_template` returns?

**Answer.** Three objects, and only the third is new in kind:

1. **The IR.** `NodeListDesc` is deliberately plain owned data — the module doc
   in `crates/template-core/src/parser/desc.rs` says "no trait objects, no Python,
   no lifetimes". There is therefore no lifetime obstacle to retaining it;
   `Arc<NodeListDesc>` costs nothing but the decision. This is the *only* reason
   the descriptor can outlive the parse, and it is worth stating because it is
   the one thing that could have made the whole design impossible.

2. **The origin, source and engine.** Already retained: `template_for` assigns
   `template.origin`, `template.source` and `template.engine`
   (`engine/parser.py:540–547`).

   **The parser is not, and my first draft said it was.** `template_for`'s only
   use of `parser` is `template.extra_data = {} if parser is None else
   parser.extra_data` (`engine/parser.py:546`) — it copies the *dict*, and the
   parser itself is dropped when `compile_template` returns. The projection needs
   the parser in at least one place: `_materialise_filter_expression` calls
   `parser.find_filter(name)` for a filter that arrived as a name rather than a
   handle (`engine/parser.py:677`), and raises if `parser is None` and a handle is
   present (`:650`). So the design must either **retain the parser too** — and pay
   for a whole parse's state held per compiled template — or **establish that the
   `find_filter` arm is unreachable when a ref table is present**, which is the
   claim that the table is total over filter bindings. That is an open question,
   not a decided one, and it is why it appears in §11 rather than here.

3. **The reference table.** This is the hard one, and the review is right that it
   is not free. `FilterCheckHost.refs` is a Python `list` whose contract
   (`lib.rs:1329–1356`) says: *"Its lifetime is the parse and the materialisation
   that follows it, exactly as `PyTarget.arena`'s is the `resolve` call — a handle
   is meaningless outside the pair."*

   The entries are filled from two sides:

   - **during the parse** — `FilterCheckHost::fill_slot` (`lib.rs:1404`): a
     `{% if %}`'s `check_filter` handle, a `call_tag` host node, a `partial_node`,
     and the `PartialDefNode` the shell returns from `register_partial_def`. All
     final when the parse ends. Safe to read later.
   - **by the materialiser** — `register_ref(slot_index, node)`
     (`parser.py:395`), which is `{% cycle %}`'s shared node. The first mention
     reserves the slot (`alloc_slot`, leaving a `None` placeholder) and the
     materialiser stores what it built; every later mention finds it
     (`parser.py:370`).

   Retaining the table is a `clone_ref` of the same `Py<PyList>`
   (`FilterCheckHost::ref_list`, `lib.rs:1394`) held by `CompiledTemplate`. The
   lifetime contract is not *broken* by that — it is **extended from "the parse
   plus one materialisation" to "the parse plus the compiled template's whole
   life"**.

### 4.1 The projection is a transaction, and a `OnceCell` is not

Round-3 review's blocking item 4 is right and this is the correction.

"Memoised and whole-or-nothing" is not a mechanism. A projection can **fail
part-way**: a factory raises, a filter callable raises, a `Variable` misses with a
non-default `string_if_invalid` that is not a string. When that happens the
`OnceCell` is still empty — so a later read retries — but `refs` has *already* been
mutated, because `register_ref` writes the `{% cycle %}` slot as soon as the node
exists (`parser.py:395`). The retry then finds `existing = resolve_ref(slot_index)`
(`parser.py:370`) and adopts a node built by the **failed** run, whose `.token` and
`.origin` were set from a walk that did not finish. That is a wrong tree, not an
exception.

Nor is the GIL a substitute: it is neither a reentrancy guard nor an atomicity
guarantee, and a Python callback reached during projection — a `FilterExpression`
resolve, a replaced factory, a `Node.render` that itself renders another template
— can re-enter projection for the same `CompiledTemplate`.

The projection therefore has explicit states, and every transition is tested
(§10.7):

| state | meaning | on entry |
|---|---|---|
| `Absent` | never attempted | run the projection |
| `Building` | in progress on this thread | **re-entrant access fails loudly**, with a named error; it never recurses and never returns a partial tree |
| `Ready(nodelist, occ_map)` | complete | return the memo |
| `Failed(error)` | attempted and raised | **re-raise the cached error**; never retry |
| `Cleared` | `__clear__` ran during cycle collection (§4.2) | **fail loudly**; never rebuild, never return an empty tree |

`Failed` is cached rather than retried because a retry is exactly the corrupting
adoption above.

**But `Failed` is for projection failures only, and round-3's second pass is right
that this distinction is required.** A filter callable that raises once during a
*render* is not a projection failure — nothing was being projected, `refs` is
untouched, and an ordinary render error is retryable in Django exactly as it is
today. Latching those into `Failed` would turn a working program into a broken
one. So:

| what raised | where it is caught | state |
|---|---|---|
| a node factory, mid-projection | the projection | `Failed`, cached, re-raised |
| a filter callable, mid-render | the walk's error arm (`Walk::culprit`) | not a projection; propagates as today |

The residual hazard, stated rather than solved: a `Failed` projection leaves
`refs` mutated, so the table is no longer pristine for *any* later use. Nothing
else may use it — which is guaranteed by the one-table-per-compiled-template rule
below, since the only other user was the parse, and the parse is over. **And
because §5.2 sends every template with an untrusted or raising factory down the
eager path, `Failed` is reachable only from a template whose factories were
trusted at plan time and raised anyway — which is a bug, not a contract.**

### 4.2 Reference cycles: a `#[pyclass]` holding `Py<PyAny>` is opaque to the collector

Round-3's second pass, and it is a plain omission from the first two drafts.
A Rust `#[pyclass]` holding `Py<PyAny>` fields does **not** expose those edges to
Python's cyclic collector. Reference counting still works — the objects are not
freed early — but a cycle that passes *through* the `CompiledTemplate` is
uncollectable, and a `CompiledTemplate` reachable only from such a cycle leaks.

`CompiledTemplate` therefore implements `__traverse__` and `__clear__` over every
Python object it owns: `refs`, the eligibility map's callables, the plan's Python
values (`Emit.head`, `ForPlan.sequence`, `FilterArg::Constant`, `Head::Value`), the
retained `origin`, `source` and `engine`, and — the one that is easy to miss —
**the `Failed` payload, which holds a traceback and therefore frames and their
locals.** A cached exception is a GC root.

**`Cleared` is a fifth state, and round-3's third pass is right that the first
draft's "an empty tree" was wrong.** CPython does not call `__clear__` on an
externally reachable object, so a live template cannot normally be cleared — but
the collection of a cycle can **resurrect** a member during finalization, and a
cleared object must stay well-behaved while the interpreter tears the rest of the
cycle down. An empty tree would make such a template *silently render nothing*,
which is the worst possible failure for a template engine.

So `__clear__` moves the projection to a terminal `Cleared` — distinct from
`Absent`, which means "not yet projected" — and clearing drops the IR so no
rebuild is possible. A read or render in `Cleared` **raises a named error** rather
than returning an empty list. `__clear__` is idempotent, and tolerates being called
on an already-`Cleared` or `Failed` object. §10.7 tests collection, resurrection,
idempotent clearing, and that access after clearing fails loudly.

### 4.3 The invariants that must be asserted, not assumed

- **One table per compiled template.** Today the table is created inside
  `compile_template` and cannot escape; after this change it is reachable from the
  `Template`, so the rule becomes assertable and must be asserted. A second
  `compile_template` never sees a first one's table.
- **`{% partialdef %}`'s mid-parse materialisation is unchanged.** It writes into
  *this parse's* table (`host.py:829–845`), exactly as it does now. Retaining the
  table is what keeps it working; the design does not make partials lazy.
- **A partially-failed projection may have mutated an object the parse already
  published.** `materialise_node` writes `existing.token` and `existing.origin`
  onto a shared `{% cycle %}` node (`parser.py:371–373`). §6.3c's journal restores
  the ref-table *slot* on failure, which is what makes that node unreachable again
  through the table — but it does not un-write a `.token` on an object a caller
  already holds a reference to. That residue is what §5.2 keeps out of reach: a
  template whose parts can raise is never lazy, so the projection that could do
  this is never the one that runs.

---

## 5. Contract: planning

**Question the review asked:** does a plan built against the IR stay valid when
boundary nodes are built later?

**Answer: the question dissolves under the inversion, and that is the main
argument for doing the inversion rather than making the current plan lazy.**

Today the plan and the tree are built in that order and the plan is a *function
of the tree*, so any change to when the tree is built is a change to the plan.
After the inversion the plan is a pure function of `(IR, refs)` and the projection
is a pure function of `(IR, refs)`; neither reads the other. A node built later
cannot invalidate a plan, because the plan never asked about a node.

What the plan does depend on, all of it retained or immutable:

- the IR — `Arc`, immutable by construction;
- the ref table — retained per §4; the only mutation to it after compile is a
  `{% cycle %}` slot being filled *by the projection the plan is about to index*,
  which is the same protocol the current code runs, merely later in time;
- the origin, source and engine — retained per §4;
- **the factory registry, snapshotted at compile time — see §5.1.**

### 5.1 The descriptor is not authoritative over the factories

Round-3 review's blocking item 6, and my first draft had this backwards. I wrote
that `plan_slot`'s `type(node).__name__` read becomes unnecessary "because the
descriptor is already authoritative". It is not.

`NODE_CLASSES` is a module-level dict mapping a class name to **the callable that
builds the node** (`engine/parser.py:134`, and the value is a factory rather than
a class precisely because `IfChangedNode.__init__` is variadic — `:99–112`). The
materialiser reads it live at every construction (`cls = NODE_CLASSES[class_name]`,
`:350`). So a caller who replaces `NODE_CLASSES["TextNode"]` with their own factory
changes what is built without changing what the descriptor says, and a plan built
from the descriptor alone would render `Act::Text` for a node that no longer
renders text. Today `plan_slot` catches exactly this by comparing the planned name
against `type(node).__name__` and declining the plan.

**The contract, corrected again by round-3's second pass.** My first correction
said the projection should build through a **snapshot** of the registry. That is
too strong, and review is right about why: `materialise_node` reads
`NODE_CLASSES[class_name]` **live at every construction** (`parser.py:350`), so a
factory that replaces *another* registry entry during construction is observed by
the nodes built after it. A snapshot suppresses that — it changes behaviour rather
than preserving it. Callable identity is also weaker than it looks: it freezes
neither a class's mutable methods nor a factory's closure state.

Round-3's third pass found the hole that made "live construction" wrong, and it is
the one this design creates: **plan time and build time are no longer the same
instant.** Today `plan_slot` reads `type(node).__name__` on a tree that was built
microseconds earlier, so plan and construction cannot disagree. Under a lazy
projection the plan is built when `compile_template` returns and the tree is built
at first `.nodelist` — an unbounded window, and the whole point of the design. A
caller who replaces `NODE_CLASSES["TextNode"]` inside that window gets a plan that
says `Act::Text` and a projection that builds the replacement. Live construction
newly *observes* a replacement that today is invisible, and a build-time re-check
cannot repair it: earlier slots have already rendered natively from the plan.

So the rule is per template, not per read:

- **A lazy template freezes construction.** If every factory was trusted at plan
  time (§5.2), the projection builds through the **snapshot**, not the live dict.
  A replacement after compile is not observed — which is exactly today's behaviour,
  because today the tree was built before `compile_template` returned.
- **An eager template keeps live construction, unchanged.** A template that is not
  lazy projects at compile time through `NODE_CLASSES[class_name]`
  (`parser.py:350`), with plan and construction at the same instant, so
  interposition during a build is observed because it is observed today.
- **Eligibility is per class, not per node.** A replaced factory disqualifies every
  node of that class in that template, and those slots become `Act::Delegate`.
- **Hand-built trees are unaffected.** A `NodeList` built by hand or by a fixture
  carries no `CompiledTemplate`, never enters this path, and keeps today's
  behaviour exactly.

In one line: **the snapshot is not a weaker substitute for the live registry — it
is the correct source for a template whose construction has been deferred, because
freezing is what reproduces the pre-change timing.**

### 5.2 When a template may be lazy at all

These two rounds of review converge on a rule that is narrower than "the tree is
never built", and it is the honest thesis of this document:

> **A template is lazy only if every node in it is planned, and every factory it
> needs is a trusted native one. A single delegate disqualifies it. Otherwise it
> projects eagerly, at compile time, and behaves exactly as it does today.**

Nothing in an eager template changes: the tree exists when `compile_template`
returns, `Slot.node` could even be kept for it, and interposition, host tags,
partials, raising factories and all the timing contracts are untouched. Laziness
is a property of the *benchmark corpus*, not a universal.

The reason this is a rule and not a heuristic is review's observation that
**deferring a factory failure changes when the error surfaces**. Today a raising
factory raises inside `materialise`, inside `compile_template`, so
`Engine.from_string()` raises. Under a lazy projection it would raise at first
`.nodelist`. That is an observable change to a public contract, and no amount of
lazy success on the happy path buys back a changed error path.

Applying the rule to the corpus:

| row | planned? | trusted factories? | lazy? |
|---|---|---|---|
| `real-world/large` | all 621 nodes | Text/Variable/If/For, all native | **yes** |
| `real-world/normal` | 26 of 29 — a root `ExtendsNode` delegate | yes, but *not* every node is planned | **no** — see below |
| `real-world/complex` | 16 of 95, `IncludeNode` recursion | untrusted shape | eager |
| micro rows | mostly | mixed | mostly eager |
| anything with a replaced factory | n/a | no | eager |

Round-3's third pass caught the table contradicting the rule it sits under, and it
is right: `normal` has a delegate, so it is not lazy, and the first draft's "yes,
forced at the delegate" was a way of having it both ways. That matters, because
the delegate would force the projection at *first render* — which in `parse` mode
(compile + render, one call) is the same call. **It would move the ~26-node build
from compile to render and save nothing.** A row that pays the same total and adds
a state machine is not a win, and saying "lazy" about it was the kind of claim §2
is supposed to have retired.

So the honest corpus claim is narrower still: **`large` alone among the real-world
rows qualifies, and the other rows are exactly what they are today.** §2 measured
the deficit on `large`; this design addresses `large`; whether that is a business
case is the next point.

**And it is not a proven business case — it is a measurement to be taken.** The
first five drafts of this document argued the case from compile time alone, which
is the one axis where laziness cannot lose. It can lose on two others, and neither
has been measured:

- **Retained memory.** Today the descriptor tree and the ref table are freed the
  moment `compile_template` returns; §4 makes them the compiled template's
  property for its whole life, together with the eligibility snapshot, the plan and
  the occurrence map. Laziness buys compile time by holding the *whole parse* per
  compiled template. On a process that compiles many templates and renders each
  once, that is a straight trade against the cache, and it is the one number a
  perf change of this shape is most likely to be rejected for.
- **First-render latency.** The work moves, whenever it moves at all. A row with
  any delegate pays its whole tree at first render instead of at compile; in
  `parse` mode that is the same call, and in a long-lived-worker `render` mode it
  is a latency spike on the request that happens to touch the template first.

**So the gate is: incremental compile saving *after* S1, retained bytes per
compiled template, and first-render latency on a representative page — and if the
first does not clear the other two, the design stops at S1.** S1 is measurable on
its own (§9) precisely so that this question can be answered without having
written S2–S4. Stating the falsifier up front is the point of §11; stating it here
is the point of this paragraph, because a design that will not name its own
kill-criterion has no business being implemented.

**The forcing boundaries, and what they cost.** The projection is forced where a
real Python object is required (§6). `Act::Delegate` is the coarse one, and it is
honest to say so:

- `ExtendsNode.__init__` builds `self.blocks = {n.name: n for n in
  nodelist.get_nodes_by_type(BlockNode)}` — it walks its **whole** child nodelist
  at construction (`engine/loader_tags.py:92–96`). So forcing the root
  `ExtendsNode` forces that template's entire body.
- `BlockNode` likewise holds a real `NodeList`.

That is not a regression — today *everything* is forced — and it is bounded by
the size of the template's **own** body, not the inheritance chain: an
`{% extends %}` parent is a *separate template*, loaded by the loader and
compiled independently, with its own IR, its own table and its own plan. There is
no recursive projection through a parent chain, which was the hazard the review
named; the recursion is in the loader, where it already is.

So the cost model is: **for a lazy template, the tree is paid for if and only if
some slot reaches a node at render time** — a delegate, a refusal (§6.2), or an
error under debug (§6.3c). On `large` the first two never fire on the happy path
and the third requires a raise, so the projection stays `Absent`. Every other row
is eager, which is to say it pays at compile, exactly as today. **The refusal arm
is the one that is decided by data rather than by shape**, which is why §11 carries
a risk row for it and §10 has no static gate for it.

---

## 6. Contract: error

**Question the review asked:** `_culprit_node` identity when nothing has been
built.

**Round-3 review's blocking item 1 is accepted in full, and it collapses this
section into something simpler than I first wrote.** My first draft argued that a
`debug = False` compile makes `annotate_culprit` unreachable, so a non-debug plan
needs no node. Two things are wrong with that.

### 6.1 The debug gate is read at error time, not at compile time

`compile_template` reads `engine.debug` once (`lib.rs:5062`) to choose
`DebugLexer`. But `annotate_culprit` **re-reads it at the moment of the failure**,
off the live context (`lib.rs:4092–4097`):

```rust
let debug: bool = context.getattr("template")?.getattr("engine")?
    .getattr("debug")?.extract()?;
if !debug { return Ok(()); }
```

So the compile-time flag is not the gate. `engine.debug` is a plain attribute:
setting it `True` after compiling with `False` reaches `Slot.node`; and a nested
render can carry an outer debug template in `context.template`. A census over
compile-time flags (§10.3 in the first draft) cannot prove anything about a
render-time read.

### 6.2 Any slot can need a node at render time — not just a delegate

The second half of the same finding, and it is worse than the debug question.
`Walk::run` (`lib.rs:4226–4246`) has **three** arms that reach `self.delegate(slot)`
— which reads `slot.node`:

```rust
Act::Delegate          => self.delegate(slot)?,
Act::Emit(emit)        => match self.run_emit(slot, emit) {
    Ok(Rendered::Refused) => self.delegate(slot)?,   // ← not a Delegate slot
    ...
},
Act::If(plan)          => match self.run_if(plan, depth + 1) {
    Ok(Rendered::Refused) => self.delegate(slot)?,   // ← not a Delegate slot
    ...
},
```

A `Refused` is a *value a filter returned* — the walk declines mid-render and
hands the node back to Python. So an `Act::Emit` or `Act::If` slot needs a node
object on a path that is decided **at render time, by data**. On `large` that is
260 Variable slots and 20 If slots, any of which may refuse.

**Consequence: `node_id` is on every slot, not `Option`.** The plan is *capable*
of producing a node for any slot; it *holds* none. That is the corrected form of
§3's claim, and it is what makes this section short: there is **one** mechanism —
`project(node_id)` — and the three readers below are three callers of it.

### 6.3 The three readers

**(a) The delegate target — `Walk::delegate`, `lib.rs:4265`.**
`project(node_id)` then `render_annotated(context)`. Forced on the first delegate.

**(b) A refusal — the same method, from §6.2.** Identical mechanism.

**(c) The exception protocol — `annotate_culprit`, `lib.rs:4100–4114`.**
`error_value.setattr("_culprit_node", project(node_id))`, after which Django reads
`.origin` and `.token` off it and `Template.get_exception_info` reads
`token.position` and `self.source`. **Projecting the real node is the only
answer that preserves identity** — a surrogate carrier with `.token`/`.origin`
would satisfy `annotate_culprit` but not the weaker guarantee that whatever the
test suite names as `e._culprit_node` is the node. `materialise_node` sets both
attributes (`parser.py:388–389`), so a projected node is exactly what today's code
attaches.

This is a *forced projection on an error path*, which is a new mutation of the ref
table (§4.1) during a render. Two rules make it safe, and the first is not enough
on its own:

> **The original error always wins, but the loss is not silent.** If projecting
> the culprit raises — a factory, a filter, anything — the original exception
> propagates, which is the same rule the existing code already states for its own
> reads (`lib.rs:4087–4089`). The secondary error is **not discarded**: it is
> chained as the original's `__context__` and noted (`add_note`), so a `debug=True`
> render that stock Django would have annotated can be *seen* to have lost its
> annotation rather than silently differing.

Round-3's third pass is right that the first draft stopped there and shipped a bug
with the rule. Three consequences, all tested (§10.7):

- **A failed projection is a failed projection, whatever forced it.** My first
  draft said the error-path projection, uniquely, may fall back to `Absent` so the
  template stays usable. That resurrects exactly the corrupt-retry bug §4.1 exists
  to forbid: `refs` may already have been written by the run that raised, and
  `Absent` invites a retry that adopts a node built by a walk that did not finish.
  **A precedence rule is not rollback.** So the projection **journals the ref-table
  writes it makes** — the `{% cycle %}` slot indices `register_ref` touches
  (`parser.py:395`) — and on failure restores them to their prior values before
  latching `Failed`. Rollback is what makes the state honest; `Failed` is then
  correct on every path, error path included, and the template is not silently
  left half-built for a later read.
- **Concurrent `Building` access is a real case, not a theoretical one.** The GIL
  serialises bytecode but does not serialise re-entry: a Python callback reached
  during projection — a filter's `__call__`, a factory, a `Node.render` that
  renders another template — can read the same `nodelist` from the same thread.
  On a free-threaded build it can be another thread. `Building` is therefore not a
  same-thread assertion; it is a guard that must hold under both, and the error it
  raises must name the template rather than deadlocking.
- **An escaped shared `{% cycle %}` object is observable.** It is reachable from
  the parse's `extra_data` before any projection, so a projection that mutates its
  `.token` changes something a caller may already hold. §4.3 records that this is
  not undone; §5.2 is what keeps it out of reach.

### 6.4 The one read that cannot be gated, and what it must carry

**(d) The `string_if_invalid` `%s` path — `absorbed_error`, `lib.rs:4797`.**
Reads `slot.node.filter_expression.var` on a lookup miss when `string_if_invalid`
contains `%s`. The comment says why it reads the node — "`%s` on it calls
`Variable.__str__`, which is the lookup text … it is the operand Django uses".

Round-3 review's SHOULD-FIX is right on both halves, and my first draft was wrong
on both:

- **Carry `desc.var` verbatim, not the joined lookup segments.**
  `VariableDesc.var` is documented as "the token as it arrived, kept for
  `__str__`/`__repr__`", and is deliberately the **original** —
  `str(Variable('_("x")'))` is `_("x")`, not `"x"` (`desc.rs:308–314`). The
  `Head::Lookups { lookups }` field is the *split* form and is not the same string.
- **Carry the `Variable`, not a `str`.** `invalid % var` calls `invalid.__mod__`
  with the `Variable` object; a custom `str` subclass can override `__mod__` and
  observe the operand. So the plan keeps the operand object and hands that,
  exactly as today.

  **And the eligibility for it is decided at error time, not at compile time** —
  which is round-3's second pass catching the same class of mistake as §6.1.
  `string_if_invalid` lives on the engine, the engine is mutable, and the `%s`
  test happens where the miss happens. A compile-time decision to skip building
  the operand is therefore wrong the moment `engine.string_if_invalid` is set to
  `'%s'` after compiling, or the template is rendered under a different engine.

  The operand is therefore built **when the plan is built, for every
  `{{ name }}` slot whose expression is a plain variable** — unconditionally, not
  gated on `%s`. It is one `_materialise_variable(desc)` per emit slot
  (`engine/parser.py:399`), on templates that are lazy, which by §5.2 are the ones
  whose every node is native. That is the honest cost of not freezing a mutable
  engine setting, and it is bounded: on `large` it is the 260 `Variable` slots —
  and it is still strictly less than the 621-node tree it replaces.

---

## 7. The two forcing sites

The review named two places where the lazy build is forced by code that is not a
render. Both are real and both are in the design's path.

### 7.1 `compile_nodelist`'s `return parsed.nodelist`

`Template.__init__` calls `self.nodelist = self.compile_nodelist()`
(`engine/base.py:169`), and `compile_nodelist` ends `return parsed.nodelist`
(`engine/base.py:235`), copying `_native_plan` across on the way
(`engine/base.py:234`). This is the benchmark's own `from_string` path, so under
the current code the tree is forced there no matter what.

**Change:** `Template.nodelist` becomes a memoised property backed by the
`CompiledTemplate`, and `compile_nodelist` transfers the *compiled artifact*
rather than the built list. The projection lives on the `CompiledTemplate`, so
`template.nodelist` and `compiled.nodelist` are the same object the second time.

Checking the vendored suite's eight `.nodelist` reads is not enough on its own —
the audit in §8 shows all eight are access-triggered, but the engine's own
construction path is a read too, and that is this line.

**`nodelist` is assigned, not only read.** Round-3's second pass is right that the
first draft said nothing about this, and it is a compatibility contract, not a
detail: `template_for` does `template.nodelist = nodelist` (`parser.py:541`),
`Template.__init__` does the same (`engine/base.py:169`), `PartialTemplate.__init__`
too (`:336`), and the suite constructs `PartialTemplate(NodeList(), ...)`
directly. A read-only property would break all four.

So `nodelist` is a **property with a setter**, and the setter is the invalidation
rule:

- **Setting it detaches the projection.** A `CompiledTemplate` whose
  `template.nodelist` has been assigned a different list stops being the source of
  truth for that attribute; the template renders what it was given. This is what
  today's code does by construction, because assignment is the only way the
  attribute is ever populated.
- **The plan is not invalidated by the assignment, but it is not used either.** A
  detached template renders through `NodeList.render`, exactly as a template with
  no plan does today (`Template._render`'s `_native_plan is None` arm). The plan is
  bound to the IR, and the IR is no longer what is being rendered.
- **Mutating the list in place drops the plan too.** Round-3's third pass is right
  that the first draft's justification for leaving this alone was wrong. I wrote
  that an appended node "has no slot, which is exactly today's behaviour for a node
  the planner declined" — but a planner-declined *node* is `Act::Delegate` and is
  rendered, while a planner-declined *list* is `_native_plan = None` and the whole
  Python path runs. Those are not the same, and the honest description of an
  appended node on a plan-carrying nodelist is **silently not rendered**. That is
  in fact today's behaviour too — `Template._render` prefers `self._native_plan`,
  so an `append` to a planned template's nodelist is invisible today as well — but
  "today has the same trap" is not a reason to keep it, and the setter rule above
  makes it inconsistent: a write by assignment drops the plan, so a write in place
  must too.

  So the projected `NodeList` overrides the mutators — `append`, `extend`,
  `insert`, `__setitem__`, `__delitem__`, `pop`, `remove`, `clear`, `__iadd__`,
  `__imul__`, `sort`, `reverse` — and any of them drops `_native_plan` and detaches
  the projection, so the render falls back to `NodeList.render` and *every* element
  is rendered. The invalidation is armed only at `Ready`: `materialise` appends
  while it builds, and a build must not invalidate itself.
- **Editing a node's own attributes is not observed, and is not new.** `Act::Text`
  holds the string (`lib.rs:3241`) and `Emit` holds its head, so setting
  `node.s = "x"` after compile already does not change what a planned nodelist
  renders. §10.7 pins this as today's behaviour rather than as a rule introduced
  here — the design does not make it worse, and it does not fix it.

The rule in one line: **the projection is the default value of `nodelist`, and
assignment overrides it exactly as it overrides a default anywhere else.**

### 7.2 The `{% extends %}` boundary

Covered in §5: `ExtendsNode.__init__` forces its child nodelist, so the forcing
propagates one level from any delegate. It stops there — the parent is a
different template — but the design must not pretend the propagation is zero.

---

## 8. The compatibility surface

A forced projection is only acceptable if every existing reader of a compiled node
object is satisfied by a memoised one. The surface was audited exhaustively
(`.nodelist`, `get_nodes_by_type`, `conditions_nodelists`,
`nodelist_loop/true/false/empty`, `.blocks`, `child_nodelists`, across
`crates/`, `python/`, `tests/`, `django_suite/` and the `.django/` pin).

**Result: every reader in the suite is access-triggered**, which is the property
the design needs. The load-bearing ones:

| site | read | why memoisation satisfies it |
|---|---|---|
| `django_suite/template_tests/test_nodelist.py:12–37` | `get_nodes_by_type(VariableNode/IfNode/TextNode)`, `repr(texts[0])` | access-triggered; a memoised `NodeList` is a real `list` of real nodes |
| `django_suite/template_tests/tests.py:213–220` (`test_node_origin`) | `for node in template.nodelist: node.origin == template.origin` | iteration triggers the projection; `materialise_node` already sets `.origin` (`parser.py:389`) |
| `django_suite/template_tests/test_custom.py:846–854` (`test_no_render_side_effect`) | node count before and after a render | passes *because* of memoisation — the second read is the same list |
| `django_suite/template_tests/syntax_tests/test_cache.py:169–174` | `t.nodelist[1].fragment_name` | indexing triggers it |
| `django_suite/template_tests/syntax_tests/test_partials.py:251–263` | `PartialTemplate(NodeList(), …)` | needs `NodeList()` constructible empty — unchanged; a hand-built `NodeList` never carries a projection |
| `tests/test_materialise.py`, `tests/test_interposition.py`, `tests/test_partials_rust.py` | `isinstance(node.nodelist, NodeList)`, `assertIs(stored.nodelist, captured[0].nodelist)` **by identity** | the memo guarantees identity, which a per-read build would not |
| `tests/test_interposition.py:3702,4113` | `template.nodelist[0].conditions_nodelists`, `get_nodes_by_type(type(node))` | access-triggered |

Two reads the review invited me to kill and I could not:

- **Identity, not just equality.** `tests/test_partials_rust.py:250` asserts
  `assertIs(stored.nodelist, captured[0].nodelist)`. That is a stronger
  requirement than "the values are right", and it is precisely an argument *for*
  memoisation — a projection that ran twice would fail it.
- **`test_no_render_side_effect`** is satisfied by memoisation and by nothing
  weaker.

The render-time consumers (`ExtendsNode.render`'s `compiled_parent.nodelist` walk,
`BlockNode.render`'s `block.nodelist`) are all reached through a node that already
exists, because reaching them required a delegate.

**The audit above is over reads, and `nodelist` is also *assigned*.** That is a gap
this table cannot close by adding rows, because assignment is not a reader — it is
the writer that defines what the default is. §7.1 carries the rule: a property with
a setter, where assignment detaches the projection and the template renders what it
was given. Four sites assign it (`parser.py:541`, `engine/base.py:169`, `:336`, and
`PartialTemplate(NodeList(), …)` in the suite), and a read-only property would break
all four silently at the first render.

---

## 9. Staging

The review's round-2 verdict was that stage 1 helps but is not a prerequisite, and
that the destination is a coupled rewrite. The staging below is ordered so that
each step is independently shippable, independently reversible, and each is
measurable against `real-world/large` before the next begins.

**S1 — Direct construction of the compatibility tree.**
Rust walks `desc_tree` and calls the existing `NODE_CLASSES` factories with
complete child lists, keeping factory ordering, `ExtendsNode`'s child-walking
`__init__` and the eager tree contract. This removes the tuple wire and the
Python decode walk without pretending the Python constructor work disappears. It
is a pure *replacement of the transport*, so the entire compatibility surface in
§8 is untouched and the Django suite is the regression test.

*Measurable, and this is where §1's residual gets replaced by a number:* the
constructor floor is ~126 µs and the measured Python-side decode is ~904 µs. What
S1 actually buys depends on how much of the ~700 µs residual is wire
serialisation and how much is the parse itself, and **that is unmeasured**. If S1
comes in near the parse floor, it is worth doing and S3 inherits a cheap
materialiser; if it comes in near zero, S1 should be folded into S3 and not
shipped alone. Either way the measurement comes first — the review's round-3
point that "S1 removes transport, not the unmeasured Rust parse cost" is right,
and the answer is to measure, not to argue.

**S2 — `CompiledTemplate` and the retained IR (a development checkpoint, not a
release).**
Introduce the `#[pyclass]` holding `Arc<NodeListDesc>`, `refs`, the eligibility
gate (§5.1), `origin`, `source`, `engine`, `lazy` (§5.2) and the projection state
machine (§4.1), with `__traverse__`/`__clear__` (§4.2). No behaviour change: the
projection is forced immediately by `template.nodelist`, and `lazy` is `False` for
every template until S4 flips it. This is the step that does the *ownership work*
§4 describes, and it is the step where the per-table invariants get asserted.
**The parser is retained here only if §11's open question resolves that way** — if
the ref table is total over filter bindings, it is dropped.

**Round-3's third pass is right that S2 is not independently justified as a
performance release, and the staging must say so.** It ships no speedup and adds a
state machine, a GC contract and a retained parse to every compiled template. So it
ships *behind a flag* — the ownership work lands and is exercised by the tests, but
a template only gains a `CompiledTemplate` when the flag is on, and with it off the
compile path is S1's, byte for byte. "No behaviour change" is a claim about
semantics, not about cost: §5.2's first measurement bullet (retained bytes per
compiled template) is taken here, against S1, and if S2 costs memory with no
path to S4 clearing it, S2 is reverted rather than carried.

**S3 — The plan is built from the IR.**
`plan_list`, `plan_block_bodies`, `plan_slot`, `plan_for`, `plan_if`, `plan_steps`
are rewritten as descriptor walks, with §5.1's factory-eligibility test replacing
the per-node class comparison. `Slot.node` is replaced by `Slot.node_id` (§3) —
present on every slot. Gate: on `large`, `plan_list` must produce 621 slots and
the plan must hold no `Py<PyAny>` node between them.

**S4 — The projection becomes lazy; every reader is rerouted.**
`Template.nodelist` becomes a property (§7.1). `Walk::delegate` and the two
refusal arms project by `node_id` (§6.2–6.3). `annotate_culprit` projects (§6.3c).
`absorbed_error`'s `%s` path is rerouted to the plan's retained operand (§6.4).

**S5 — is not a stage.**
My first draft kept the eager path "permanently, behind `engine.debug`". Round-3
review is right that this is wrong: the debug flag that matters is read at
*render* time (§6.1), so there is no compile-time value to fork on, and a design
that kept two code paths for the exception protocol would be forking the one
thing that has to stay in one place. **There is one path, and it projects.** What
may be retained is a *fallback* for a template whose plan is declined — which
already exists today, is the `_native_plan = None` class default, and is not a
staging artifact at all.

---

## 10. Verification

The project's rule is **no pinned test counts** — a count pin is churn. The checks
below are structural.

**10.1 — The plan holds no node on the common path.** A Rust unit test over the
`large` corpus row asserting the plan retains no `Py<PyAny>` node — every slot's
`Act` is `Text`/`Emit`/`If`/`For`, and `node_id` names an occurrence rather than an
object. This is the claim the whole design rests on (§2), so it is asserted
directly rather than inferred from a benchmark. Note what it does *not* assert:
that the plan cannot **produce** a node, which §6.2 requires it to be able to do
from any slot.

**10.2 — Every forcing read is access-triggered.** The §8 table is the audit;
the check is a test that builds a `Template` from a template with no delegate,
asserts the projection is `Absent`, then touches `.nodelist` and asserts it
reaches `Ready` exactly once and that a second read is the same object
(`assertIs`).

**10.3 — The debug read is honoured wherever it happens.** Not a census over
compile-time flags — the first draft's version of this check was void, because
the read is at `lib.rs:4092–4097` and happens at error time. The check is
behavioural: compile one template with `Engine(debug=False)`, raise inside it,
then flip `engine.debug = True` and raise again, and assert the second raise
produces `template_debug` with the same `during`/`before`/`after` as stock Django.
Same for a nested render under an outer debug template.

**10.4 — The ref-table invariants.** Tests that one `CompiledTemplate` owns
exactly one table; that a `{% cycle %}` mentioned twice in a lazily-projected
template yields **one** node (`is`); and that the node carries the *last*
occurrence's token (`parser.py:364–370` records the measured reason and the
measured value: `['cycle c', 'cycle c']`).

**10.5 — Behavioural parity.** The Django suite (1547 tests, 6 skipped) is run
unchanged at every stage. Plus `bench.py` in `parse`, `compile` and `render`
modes over all corpus rows, with the render row required not to regress.

**10.6 — Nothing here is sized by a per-character or per-node figure alone.**
The corpus is small (NORMAL 443 chars/21 tags, COMPLEX 328, `large` 9279/340) and
every claim above is stated per-row against the row's own deficit.

**10.7 — Every review finding that names a mechanism, as its own test.** Each of
these is a claim the design would be wrong without; none is covered by behavioural
parity, because none of them changes what a passing template renders.

- **Occurrence identity (§3.1).** A template mixing a host node, a `{% cycle %}`
  used twice, a `{% partialdef %}` body and a delegate *after* them, asserting each
  slot's `node_id` resolves to the object it should — including the two occurrences
  of the cycle mapping to one object.
- **Skipped descendants are filled, not invented (§3.1).** Three separate tests,
  one per early return: a host node (`parser.py:317–347` returns from the table), a
  repeated `{% cycle %}` (`:364–370` returns the existing node before traversing
  children), and a `{% partialdef %}` whose body was built during the parse. In
  each, the early-returning node is followed by a delegate. Two assertions per
  test, and they are the two halves of the rule: (i) every IR occurrence in the
  skipped subtree resolves to the object the *first* occurrence built — the same
  object, by identity, not a rebuilt one; and (ii) the number of populated
  occurrences equals the number of IR occurrences, so no descendant was invented
  and none is a hole. The delegate's `node_id` must name its own occurrence, which
  is populated by the fill. This is the test that a pre-order numbering is correct
  only because occurrences are *numbered* by the IR while *objects* are produced
  where the projection produces them — conflating the two fails all three.
- **Projection re-entrancy (§4.1).** A factory or filter callable that triggers a
  second read of `nodelist` during projection; assert it fails loudly in
  `Building` rather than recursing or returning a partial tree.
- **Projection failure (§4.1).** A factory that raises part-way; assert the state
  latches `Failed`, that a second read re-raises the *same* error, and that a
  `{% cycle %}` slot written by the failed run is not adopted by anything.
- **A render error is not a projection failure (§4.1, §6.3c).** A filter callable
  that raises the *first* time it is called and succeeds afterwards, inside a
  lazily-projected template: assert the first render raises, the projection is
  still `Ready`, and the second render succeeds. This is the boundary between the
  two failure kinds — caching a *render* error would turn a working program into a
  broken one, which is why `Failed` caches projection errors only.
- **A failed annotation keeps the original error and *says* it failed (§6.3c).**
  A lazy template whose error-path projection raises: assert the raised exception
  is the original one, that its `__context__` is the projection error, that a note
  naming the lost annotation is attached, and that the projection is `Failed` — not
  `Absent`. Then a second read, asserting it re-raises the cached failure rather
  than projecting again. This is the test that the first draft's "fall back to
  `Absent`" would have failed.
- **The ref-table journal rolls back (§6.3c).** A projection that writes a
  `{% cycle %}` slot and then raises; assert the slot holds its pre-projection value
  afterwards, so no later read can adopt a node from a walk that did not finish.
- **The projection is collectable, resurrectable and terminally clearable (§4.2).**
  Build a cycle that runs `template.nodelist -> node -> ... -> compiled artifact ->
  template` under `gc.collect()`, with a live `refs` entry, a plan carrying a Python
  value, and a latched `Failed` exception holding a traceback, and assert the cycle
  is collected after dropping the external reference — on weakref death or
  `gc.garbage`, never on a count, because a `#[pyclass]` without `__traverse__`
  fails this by leaking rather than by erroring. Then: `__clear__` is idempotent,
  tolerates an already-`Failed` object, and a render or `.nodelist` read afterwards
  **raises a named error** rather than rendering an empty tree.
- **`nodelist` is written, not only read (§7.1).** Three tests. Assignment
  (`template.nodelist = other`) renders `other` and reads back `other` by identity.
  A `PartialTemplate(NodeList(), ...)` constructed directly, pinning the setter.
  And in-place mutation — `append`, `__delitem__`, `insert` — which must drop the
  plan so the whole Python path runs and the appended node *is* rendered. The third
  is the one the first draft got wrong.
- **A node-attribute edit is unobserved, as today (§7.1).** `nodelist[0].s = "x"`
  after compile does not change a planned render. Pinned as the existing behaviour
  so that it is a documented property rather than an accident.
- **Factory interposition, on both sides of the new window (§5.1).** Replace
  `NODE_CLASSES["TextNode"]` *before* compile and assert the plan declines those
  slots. Replace it *between* compile and the first `.nodelist` read, on a template
  that is lazy, and assert the frozen snapshot still builds the native factory —
  the case round-3's third pass found. Replace it after a template has already
  rendered natively once, and assert the earlier render is not retroactively
  changed. On an *eager* template, replace it during construction and assert the
  interposition *is* observed, because it is observed today. Plus a reference cycle
  through the replaced factory, to show nothing is leaked.

---

## 11. Risks, and what would falsify the design

| risk | why it is credible | falsifier |
|---|---|---|
| **The ref table is not total over filter bindings.** If it is not, `_materialise_filter_expression`'s `parser.find_filter(name)` arm (`parser.py:677`) is reachable from a projection, and the parser has to be retained after all — a whole parse's state held per compiled template. | `template_for` does **not** retain the parser (§4), so this is a new decision either way, and the arm exists today. | Trace whether a `FilterCallDesc` with no `handle` can survive a parse that has a host. |
| **A reader is not access-triggered.** The audit found none, but it read the trees it was told to. | `test_no_render_side_effect` is close to this shape already. | Any test that reads a node count or a node identity from a path the projection does not intercept. |
| **An `Act` arm refuses more often than expected**, forcing projections on the hot path. §6.2 makes refusal a *data* decision, so it is not statically bounded. | The refusal arms are `Rendered::Refused` from `run_emit`/`run_if`; nobody has counted them on `large`. | Instrument refusals per row in `parse` mode. If refusals are common, the plan should carry the node after all and the design is wrong. |
| **Direct construction (S1) is not much of a win.** Moving the decode walk from Python into Rust is not free — PyO3 marshalling per node per attribute. | The 904 µs is Python-side; the Rust-side replacement is unmeasured, and §1's ~700 µs residual is the upper bound, not the wire. | S1's benchmark, before S2 is written. |
| **The plan's descriptor walk is slower than the tree walk it replaces.** The tree walk reads cached attributes; the descriptor walk re-derives them. | `plan_head` already chose the descriptor for this exact reason and measured it (`lib.rs:3561–3566`), but `plan_steps` and `plan_for` are heavier. | S3's benchmark against S2. |
| **`ExtendsNode` forces more than expected** on a template whose root is not an `ExtendsNode` but which contains one. | `get_nodes_by_type` on a child nodelist walks only that child. | Instrument the projection against `real-world/normal` and `complex`. |
| **Occurrences are numbered where nodes are *visited*, not where objects are *produced*.** Then a short-circuited slot names an occurrence that no object ever filled, and the failure is a delegate rendering the wrong node — silent, on a template shape the corpus does not contain. | The materialiser has three early returns (host nodes, repeated cycles, prebuilt partial bodies), and a pre-order walk of the IR sees all of them. | §10.7's three skipped-descendant tests. If any delegate's `node_id` resolves to a neighbour, or the populated count differs from the IR count, the numbering is wrong. |
| **The whole design costs more memory than the compile time it saves.** The descriptor, the ref table, the eligibility snapshot, the plan and the occurrence map are held per compiled template for its whole life; today all but the plan are freed when `compile_template` returns. On a process that compiles many templates and renders each once, laziness is a straight trade against the template cache. | This is the first design in the thread whose win is measured on one axis and whose cost is on another, and no draft before this one named the cost at all. | §5.2's measurement: incremental compile saving *after* S1, retained bytes per compiled template, and first-render latency. If the first does not clear the other two, **stop at S1**. This row is the design's own kill-criterion. |
| **The journal does not cover every write a failed projection makes.** It restores the ref-table slots `register_ref` touches; `materialise_node` also writes `existing.token` and `existing.origin` onto the shared object (`parser.py:371–373`), which no slot restore can unwrite. | The residue is real and §4.3 documents it rather than denying it — which is only honest if the test checks the boundary rather than assuming it is empty. | §10.7's rollback test, asserting the slot is restored *and* recording the object residue as the known remainder. A third write site the journal misses is the falsifier. |
| **In-place mutation invalidation is incomplete.** A `list` subclass cannot intercept every mutation — a slice assignment, `+=`, or any C-level mutation from another module can bypass the overridden methods, and a plan that survives one of those silently skips the node. | The overrides enumerate the Python-level API, not the C one, and the trees have not been audited for who mutates a `nodelist` after it is built. | §10.7's mutation tests extended to slice-assign and `+=`, plus an audit for `nodelist` mutation outside the materialiser. |
| **`Cleared` is either unreachable or reachable on a live template.** If CPython never clears a reachable object the state is dead code; if resurrection can leave a reachable referent, the design must fail loudly on every access path, not only on `.nodelist`. | The first draft's answer — an empty tree — was wrong in the worst direction, which is evidence the case has not been thought about carefully. | §10.7's collection/resurrection test, and an audit that every reader of a projected artifact, not just the property, goes through the `Cleared` guard. |
| **`Failed` caches a *render* error.** A filter that raises once and succeeds afterwards becomes permanently broken — a worse regression than the one being fixed, and one that behavioural parity would catch only if some vendored test happens to retry. | The state machine has one failure kind and the code path from `filter_expression.resolve` can raise either kind. | §10.7's retryability test. |
| **`CompiledTemplate` leaks.** A `#[pyclass]` holding `Py<PyAny>` is opaque to the cyclic collector, so a cycle through it — and the latched `Failed` traceback is itself a cycle root — is never freed. | PyO3 does not synthesise `__traverse__`; it must be written, and the `Failed` payload is the branch that is easy to omit. | §10.7's `gc.collect()` test, asserting on collection rather than on a count. |
| **`nodelist` as a read-only property breaks an assignment.** Four sites assign it, including a `PartialTemplate(NodeList(), ...)` the suite constructs directly. | The first draft named only reads; the §8 audit is over reads. | §10.7's assignment test; §7.1 is the setter rule. |

---

## 12. Non-goals

- **The render path.** Not touched. Its rust/stock is 0.33–0.79× and no step here
  may move it.
- **`{% partialdef %}`.** It materialises during the parse by design; the table
  is retained so that it keeps working, not so that it becomes lazy.
- **The `{% include %}` lever.** Native `{% include %}` measured 72 inlined
  templates at 202 µs against 72 includes at 422 µs — a real 220 µs on
  `real-world/complex` — but `large`, `normal` and every micro row have no
  includes, so it is a separate, narrower change. It belongs to whichever of S1
  or S3 makes `IncludeNode` a thing the plan can express, and not before.
- **Removing the plan's Python *values*.** `Emit.head`, `ForPlan.sequence`,
  `FilterArg::Constant` hold Python objects. They are values, not nodes, they are
  read on every render, and they are not what the ~904 µs of measured decode buys.
