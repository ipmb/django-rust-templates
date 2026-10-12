# The native `{% include %}` lever Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the native render walk resolve and render a `{% include %}` itself, so a page
whose hot loop is an include stops paying five microseconds of Python machinery per
iteration.

**Architecture:** One new `Act` variant, `Act::Include`, carrying the template name read off
the descriptor at plan time. At render the walk resolves the target `Template` through
**Django's own node-keyed `render_context` cache**, enters the three `with` managers Django's
`IncludeNode.render` and `Template.render` enter — under an RAII guard — and recurses into the
included template's `_native_plan` slots with the same `Walk` — no second `Walk::new`, no
`Template.render` frame, no `IncludeNode.render` frame. Everything the slot does not reproduce
exactly declines to `Act::Delegate`, which is the Python path unchanged.

**Tech Stack:** Rust (PyO3 0.29) in `crates/template-py/src/lib.rs`; the descriptor side in
`crates/template-core`; the generated transcription `loader_tags.py` is **read, never
edited**.

**Spec:** `docs/superpowers/plans/2026-10-10-native-ir-compatibility-nodes.md` §12,
"Non-goals — The `{% include %}` lever", which defers this work in as many words: it
"belongs to whichever of S1 or S3 makes `IncludeNode` a thing the plan can express, and not
before." S1 shipped in `948375f`. This is that step.

**Round 1 of review is incorporated.** Five findings from `destination-reviewer`, all five
taken; the design below is the revised one, and §"What round 1 changed" records what moved.

## Global Constraints

- **Drop-in compatibility with Django 6.0.9 is the only load-bearing thing.** Byte-identical
  output against stock Django on every corpus row, and the Django test suite vendored in the
  gate, are the criterion.
- **`loader_tags.py` is generated**, not written: `scripts/check_transcriptions.py:752` and
  `scripts/gen_impl_transcription.py:134` ("it stays generated"). No edit to that file is
  available, which is *why* the divergence point is the plan.
- **A declined plan is a lost optimisation, never a different answer.** Every new arm needs a
  total fallback to `Act::Delegate`.
- Rust tests run only through `scripts/run_rust_unit_tests.sh`. `cargo` needs `source scripts/env.sh`.
- Never run `maturin develop` while a review is in flight — the reviewer shares the venv and the built `.so`.
- No pinned test counts anywhere. Write the structural check.
- Temp files in `$CLAUDE_JOB_DIR/tmp`, never `/tmp`.

## The measurement this plan is bought with

Two instruments, both on the shipped `948375f` build.

`scripts/bench.py` at `from_string`, rust vs jinja2, render mode (`docs/bench.html` cells are
`[2]=django [3]=rust [4]=jinja2`; row order compile, parse, render):

| row | rust | jinja2 | ratio |
|---|---|---|---|
| `real-world/complex` render | 482 | 352 | **1.37x** |
| `real-world/large` render | 236 | 208 | 1.13x |
| `micro/extends` render | 10.0 | 5.4 | 1.86x |
| `micro/loop-text` render | 10.0 | 5.7 | 1.74x |
| `micro/include` render | 7.1 | 6.2 | 1.14x |

Those five are the *whole* of the gap: compile and parse are won 2–10x everywhere, and
`real-world/normal` render is already 166 vs 177.

**`real-world/complex` is 81% include machinery.** Measured by monkeypatching
`IncludeNode.render` to `lambda self, context: ""` and re-timing the same handle:

    real-world/complex     with includes 475.7us   includes stubbed  92.0us   include total 383.7us
    real-world/normal      with includes 167.4us   includes stubbed 159.8us   include total   7.5us
    micro/include          with includes   7.0us   includes stubbed   1.9us   include total   5.0us

`COMPLEX` is 12 sections x 6 lines with **one `{% include %}` per inner iteration — 73
includes per render**, so that is **5.3 us per include**. `micro/include` pays 5.1 us for one
include of a three-variable template.

`cProfile` on `micro/include` shows where the 5.1 us goes — two Rust entries and three Python
dictionaries — and a call-order trace of one render gives the sequence the slot must
reproduce exactly:

```
Template.render(page)
  bind_template(page)                       # context.template was None
  _render -> render_plan(page_plan)         # native #1
    IncludeNode.render(context)
      template = self.template.resolve(context)          # the literal
      callable(getattr(template, "render", None))        # False: a str
      construct_relative_path(self.origin.template_name, template_name)
      cache = context.render_context.dicts[0].setdefault(self, {})
      cache.get(template_name) or context.template.engine.select_template(template_name)
      values = {}                                        # extra_context empty
      with context.push(**values):                       # ContextDict #2
        Template.render(footer)
          render_context.push_state(footer)              # ContextDict #3
          -- bind_template is NOT called: context.template is not None
          _render -> render_plan(footer_plan)            # native #2
```

The spec measured the same lever independently: "Native `{% include %}` measured 72 inlined
templates at 202 us against 72 includes at 422 us — a real 220 us on `real-world/complex`."

**Expected after this plan, at ~0.9 us of native slot per include:** `complex` 476 -> ~170
(jinja2: 350), `micro/include` 7.0 -> ~3.5 (jinja2: 6.27), `normal` 167 -> ~160 (jinja2: 172).
That is four of the five rows above; `micro/extends` and `micro/loop-text` are not this plan
(see Non-goals).

**Measured, and the estimate was 2.5x optimistic.** `2.28 us` per include, not `0.9`:

    real-world/complex  rust 408.6us  inlined 244.5us  machinery 164.1us over 72 includes (2.28us each)
                        jinja2 343.3us  django 855.0us

So `complex` went 476 -> 409 and not to ~170, and the row is still behind jinja2. The residual
is `resolve_include`'s six Python attribute reads per include — `self.template.resolve`,
`getattr(template, "render", None)`, the cache dict, `select_template`, the empty
`context.push(**values)` — which is the plan's declared non-goal: the slot removed the *render*
machinery, and the resolution step is the Python object graph the spec's S1 is for.

**The residual was Python-call-bound, so both follow-ups in Non-goals that waited on it were
tried and then taken back out.** The render-invariant prefix of the lookup moved onto the `Walk`
(`Walk::include_cache`), and the `(target, plan)` pair it resolves was remembered for the last
include (`Walk::include_memo`, cleared by `Walk::delegate`). That build measured, on the same
instruments:

    real-world/complex  rust 353.4us  inlined 241.2us  machinery 112.1us over 72 includes (1.557us each)
    micro/include       rust   6.2us  inlined   4.1us  machinery   2.1us over  1 include  (2.07us each)
    real-world/normal   rust 163.6us  inlined 160.6us  machinery   3.0us over  1 include  (2.99us each)

`complex` 409 -> 353 against a jinja2 that did not move (341-343), so the row is 12 us behind
rather than 66; `micro/include` and `normal` each hold one include, so nothing repeats and they
did not move — the control that says the instrument was not drifting. An include that has to
*resolve* costs 2.07-2.99 us and one the memo already knows costs 1.56 us.

**It is not in the tree, because the review reproduced four output divergences under it.** The
memo stood in for two lookups that Django re-runs on every render of the node: the cache in
`render_context.dicts[0]` (`loader_tags.py:197-201`) and `renders_through`'s verdict on
what came back. A third is `context.template.engine`, which under `{% extends %}` is the *child*
that started the render while a parent or block node executes — so keying the memo on node
identity quietly claimed the engine could not change mid-node.
Python the walk does not delegate runs between two iterations of a loop — the
sequence's own `__next__` — and `Walk::delegate` is not on that path. Replacing the node's cache
entry, replacing the cached target's `render`, replacing its `_native_plan`, and replacing
`dicts[0]` itself each gave native `xxx` where Django renders `xyy`. The hoist alone fails the
fourth of those. Fresh reads on every hit is the sound form, and it measured inside the noise
floor of this machine (418-435 us on `complex` across both arms), so there is nothing to keep.

The rows are in `tests/test_interposition.IncludeCacheFreshnessTests` — three divergences plus the
delegate window as the control. All four pass at `HEAD`; the three divergence rows fail under a
mutant that puts the memo and the hoist back, at exactly the outputs above, and the control passes
under both, which is what makes it a control. That is what
says the next attempt at this cannot land in the same shape.

Against this plan's own prediction (`476 -> ~170`, `0.9 us` an include) it landed at 409: the
estimate priced the *render* machinery the slot removes and not the *resolution* it replaces. The
1.56 us floor per include that the reverted build found is the honest number for the slot, and
`ContextLayers` is ~0.86 us of it — `context.push()` + `__exit__` 412.6 ns and
`render_context.push()` + `pop()` 369.2 ns, both Django's own `with context.push(**values)` and
`render_context.push_state(self)`, neither a redundant call.

### What round 4 cost, and why round 5 could not buy it back

Round 4 stopped reproducing the stock bodies and entered the objects the `with` statements called
— which is right, and which turned `render_context.push_state(self)` into `push_state(self)` plus
`__enter__` plus `__exit__`. `push_state` is `@contextmanager`-decorated, and the dance is not
free: measured in isolation, the transcribed body is **401 ns** where building the manager and
entering it is **947 ns**, a **546 ns** surcharge on every include. On `real-world/complex`
(72 includes) that alone was **+57 us, +13.2%**, against a jinja2 control that moved 1.2% — real,
not harness drift:

| row | round 3, transcribed (=`HEAD`) | round 4, managers entered | round 5, + stock guard | round 5, guard reverted | jinja2 |
|---|---|---|---|---|---|
| `micro/include` | 6.55 us | 7.19 | 6.82 | 7.19 | 6.12 / 6.21 / 6.15 |
| `real-world/normal` | 168.29 | 163.55 | 165.15 | 165.15 | 175.68 / 173.36 / 171.99 |
| `real-world/complex` | 431.77 | 488.59 | **446.34** | 488.59 | 346.98 / 351.25 / 348.20 |
| `micro/extends` | 9.73 | 9.63 | 9.59 | 9.63 | 5.10 / 5.14 / 5.11 |

**Round 5 bought 42 of those 57 us back and the review then took it away again.** The recovery was
`reproduce_push_state`: reproduce the *body* once more — read `template`, write the new one,
`push()` — but only while `reproduces_push_state` had confirmed that `render_context.push_state`
still resolves to `PUSH_STATE` **on this very render context**, anything unresolved answering "no".
That guard rejects an override, a subclass, a rebind, an instance attribute shadowing the class
function. What it cannot reject is the same function *behaving* differently, and three measured
shapes of that came out of the round-5 review:

- a `pop` that raises `StopIteration` escapes natively as `StopIteration`; inside the generator it
  becomes `RuntimeError("generator raised StopIteration")`, because `_GeneratorContextManager`
  converts it at the `__exit__`;
- a `pop` that reads `sys.exception()` during a failed include sees `None` natively and the
  render's `TemplateDoesNotExist` under delegation, because under delegation it is running inside
  `gen.throw`;
- rebinding `push_state.__wrapped__.__defaults__` to `(False,)` changes what the body does without
  changing the identity the guard tests.

None of the three is reachable by inspecting the function object, because the thing that actually
runs is a *generator*, and a generator's exception environment is created at the `yield`, not
carried on the function. Capture-on-import is the right answer for `render`/`_render`, which are
plain functions the walk replaces outright; it is not enough for a body that has to run.

So the transcription came out, `_stock.py` went back to `RENDER`/`PLAN`/`METHOD_TYPE`, and the
**+13.2% on `complex` relative to `HEAD` stands as the price of the protocol.** `HEAD` is the
transcribed build and it is the one with the four divergences round 4 was opened to close; the
number to beat next is 488.59, not 431.77.

### What rounds 5 and 6 cost, measured against `HEAD` on one instrument

The table above is three separate runs compared by hand, which is how the last two baselines
entered. This one is one instrument, `scripts/bench.py --json --engines django,rust`, four builds
built and measured inside one session, minutes apart, on `real-world/complex` render and
`micro/include` render. Same build, two runs: 541.7 and 525.1 on `complex` — a **3.2% scatter**, so
only the last column is a finding.

| build | `complex` render | `micro/include` render |
|---|---|---|
| `HEAD` — transcribed, and wrong | 433.7 | 6.4 |
| round-6 unwind, `manager.getattr` binding | 476.3 | 7.2 |
| **round-6 unwind, as shipped** | **525.1** | **7.7** |
| round-6 unwind, `PyMethod_New` binding | 504.8 | 7.4 |

Three things read off it. **The restructure is free**: 476.3 is *below* round 5's 488.59, so
resolving both `__enter__` and `__exit__` up front and driving the `__exit__` costs nothing over
round 5's `call_method0("__enter__")`. **The special-off-the-type binding is 9.3%** — 525.1 against
476.3, 0.68 us an include, well outside a 3.2% scatter — and it is 3 C calls where
`manager.getattr` is 1, two of them Python-level. **`PyMethod_New` does not buy it back** — 504.8
sits inside the same build's scatter of 525.1, for an `extern "C"` that `pyo3-ffi` omits on purpose.

So the exactness at the `with` boundary is 0.68 us an include and the alternative is a divergence
with a manager that shadows `__enter__` or `__exit__` in its own `__dict__`. Shipped exact; the
number is here so that the next person can weigh it rather than rediscover it.

## Review Focus

The seven things this plan can get wrong that no test it writes will name by itself. Rows 1
and 2 are the two round 1 found, and they are the two that would have shipped; rows 6 and 7 are
round 4's and round 5's.

1. **The context layers must unwind on every path.** `context.push()` and
   `render_context.push()/template=` are three pieces of state set one after another, and
   Rust's `?` does not run `finally`. Any `?` between the first push and the last pop — a
   failed `getattr`, a raising `setattr`, an error out of the child render — leaks a layer
   into a `Context` that a later render then reads. Must be an RAII guard, not inline calls.
2. **The recursion budget must not reset.** The walk has never recursed into a *different*
   template's plan before, and `MAX_WALK_DEPTH` (`lib.rs:2218`, 500) is passed *as a
   parameter*. A child started at depth 0 gets a fresh 500, and when a delegated node hands
   the render back to Python — which starts a fresh `Walk` at 0 again — the budget resets
   once more. `{% include "self.html" %}` in a template that also delegates is then unbounded
   Rust recursion and a **SIGSEGV** where Django raises `RecursionError`.
3. **Where the resolved template is remembered.** Django caches it in
   `render_context.dicts[0]`, keyed by the node — so its lifetime is the `Context`'s. Caching
   it on the plan makes it the `Template`'s, which diverges under a loader reset.
4. **Who counts as one node.** Django wraps the included template's output in
   `SafeString("".join(...))` of *its own* nodelist, so an include is **one element** in the
   parent's list. The walk's `self.nodes` is the ordinal a non-`str` piece is reported at
   (`push_python`, `lib.rs:3856`), and the ordinal is in the `TypeError`'s message. Sharing
   one accumulator makes that ordinal the outer one.
5. **The push that looks redundant.** `extra_context` is empty on our fast path, so
   `context.push()` pushes an *empty* dictionary. Dropping it is the obvious saving and it is
   wrong: `Context.__setitem__` writes to `dicts[-1]`, so a node in the included template that
   assigns at top level would leak out.
6. **The three `with` blocks are protocols, not sequences.** Each of `context.push(**values)`,
   `render_context.push_state(self)` and `context.bind_template(self)` *returns an object*, and
   what the block does is `__enter__`/`__exit__` on it — including an `__exit__` that returns true
   and **swallows** the failure, leaving the frame to fall off its end. Reproducing what the stock
   bodies do is not the same thing: a subclass's override is never called, and a suppression turns
   into a propagated error. Enter the objects the calls returned, and unwind them in Python's
   order, innermost first.
7. **The rules of the unwind are CPython's, not the managers'.** Three of them, none visible in
   any stock manager: an `__exit__` on the ordinary path is called for its side effect and its
   return value is *never* read, so a manager answering with an object whose `__bool__` raises is
   fine on the happy path and fatal on the unhappy one; a truthy `__exit__` on the failure path is
   a `with` that does not propagate, so the frame falls off its end with **no** value; and a
   failure *raised during the unwind* cancels the body's value even when a manager outside it
   later suppresses that failure. Round 5 found the first and the third by measurement.

---

### Task 1: The `Act::Include` slot

**Files:**
- Modify: `crates/template-py/src/lib.rs` — `Act` (2288), `Slot` (2305), `plan_slot` (3277),
  `Walk::run` (3567), `push_python` (3856), `render_plan` (4254)
- Test: `tests/test_interposition.py` — the census idiom already lives there
- Run and keep green, not edited: `tests/test_inheritance_gate.py`

**Interfaces:**
- Consumes: `NodeDesc`/`AttrValue::FilterExpr`/`FilterExprDesc` (`crates/template-core/src/parser/desc.rs:255,289`),
  and the already-built `IncludeNode` descriptor (`crates/template-core/src/parser/tags/include.rs:113`).
- Produces: `Act::Include(Box<IncludePlan>)`, `IncludePlan::name`, `MAX_WALK_FRAMES`.

**The descriptor is the whole detection rule.** `tags/include.rs` already ran
`construct_relative_path` on `bits[1]` *before* `compile_filter`, so by the time we see the
descriptor a relative name has been rewritten to its final target — there is no `./`-prefix
case left to decline.

- [ ] **Step 1: Write the failing structural test**

**The instrument is the census**, not a new helper. `_native.census_take()` returns
`(handled, delegated, delegated_len, filters, body_crossings)` as `(class_name, count)` rows,
and a node the walk renders itself bumps `handled` while one it delegates bumps `delegated`.
So the check is the project's own: a name that moves columns. No counts are pinned.

The census is a process-global, so `census_start`/`census_take` bracket the render in a
`try`/`finally` — the idiom `tests/test_interposition.py:1870` already uses, and in the
`finally` for the reason its comment gives: a raising render must not leave the census on
for the next module's rows. Both `{% include 123 %}` and `{% include "" %}` raise on stock
Django, so a row must survive the render raising; the census is still readable from the
`finally`, which is what lets those two rows be asserted at all.

```python
class IncludeSlotTests(unittest.TestCase):
    """A literal `{% include %}` reaches the walk's own slot, and no other shape does.

    **The census, not the output.** A delegated `IncludeNode` renders Django's bytes too,
    so only the column the node lands in separates the two. `handled` and `delegated` are
    `(class_name, count)` lists and every assertion here is membership, so no count is
    pinned.
    """

    TEMPLATES = {"a.html": "x"}

    def census_of(self, source, data=None, templates=None):
        from django_rust_templates import _native
        from django_rust_templates.engine import Engine
        from django_rust_templates.render import render_string

        engine = Engine(
            loaders=[
                (
                    "django_rust_templates.engine.loaders.locmem.Loader",
                    self.TEMPLATES if templates is None else templates,
                ),
            ],
        )
        _native.census_start()
        error = None
        try:
            render_string(source, data or {}, engine)
        except Exception as exc:  # noqa: BLE001 -- `{% include 123 %}` raises on stock too
            error = exc
        finally:
            handled, delegated, _, _, _ = _native.census_take()
        return dict(handled), dict(delegated), error

    def test_a_literal_include_stops_going_back_to_python(self):
        """The one thing that makes the fast path exist at all."""
        handled, delegated, error = self.census_of('{% include "a.html" %}')
        self.assertIsNone(error)
        self.assertIn(
            "IncludeNode",
            handled,
            "a literal `{% include %}` was not planned, so `IncludeNode.render` "
            "rendered it and the five microseconds are still there",
        )
        self.assertNotIn("IncludeNode", delegated)

    def test_a_quoted_numeric_name_is_a_literal_like_any_other(self):
        """`Variable('"123"')` tries `int()` on the quoted text, fails, and unescapes,
        so the literal is the *string* `"123"` -- a name, and the slot takes it."""
        handled, delegated, error = self.census_of(
            '{% include "123" %}', templates={"123": "x"},
        )
        self.assertIsNone(error)
        self.assertIn("IncludeNode", handled)
        self.assertNotIn("IncludeNode", delegated)

    def test_every_other_include_shape_still_delegates(self):
        """Each is a shape whose resolution touches the context, or that the slot does
        not reproduce, so it must not claim it. Three of the five raise on stock Django;
        `error` is not asserted here, because the census is the claim and the output is
        `TwoPathParityTests`'."""
        for source in [
            "{% include template_name %}",      # a bare name: resolved per render
            '{% include "a.html" with b=1 %}',  # extra_context non-empty
            '{% include "a.html" only %}',      # isolated_context
            "{% include 123 %}",                # literal is an Int: `tuple(123)` is a TypeError
            '{% include "" %}',                 # `"" or ()` is `()`, not `("",)`
        ]:
            with self.subTest(source=source):
                handled, delegated, _ = self.census_of(
                    source, {"template_name": "a.html"},
                )
                self.assertIn("IncludeNode", delegated, source)
                self.assertNotIn("IncludeNode", handled, source)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -I -m pytest tests/test_interposition.py -k IncludeSlot -q`
Expected: FAIL on the first row — `IncludeNode` is in `delegated` and not in `handled`, which
is the whole behaviour this task changes.

- [ ] **Step 3: Add the act, the plan struct, and the class-name const**

Three declarations. First, `INCLUDE_NODE` goes in the const block at `lib.rs:2284-2292`,
which holds one const per class the walk renders itself:

```rust
const INCLUDE_NODE: &str = "IncludeNode";
```

`std::cell::Cell` is not yet imported (`lib.rs:2280` imports only the atomic and `Mutex`
from `std`), so add it beside those — step 7's thread-local needs it.

Beside `Act::For`:

```rust
    /// An `{% include %}` the walk resolves and renders itself.
    Include(Box<IncludePlan>),
```

Beside `ForPlan`:

```rust
/// A `{% include %}` with a constant name, no `with` bindings and no `only`.
///
/// **The name is all it holds.** Where the *template* is remembered is Django's business and
/// stays Django's: `IncludeNode.render` caches it in `render_context.dicts[0]` keyed by the
/// node, so its lifetime is the `Context`'s (finding 3). A copy here would outlive a loader
/// reset and answer with a template the engine no longer serves.
struct IncludePlan {
    /// Read off the descriptor, not the node: `tags/include.rs` ran `construct_relative_path`
    /// before compiling the filter, so a relative name is already its final target.
    name: String,
}
```

- [ ] **Step 4: Detect it in `plan_slot`**

`plan_slot` already has `desc: &NodeDesc` (`lib.rs:2908`). Add a branch and the predicate:

```rust
    } else if class_name == INCLUDE_NODE {
        match constant_include_name(desc) {
            Some(name) => Act::Include(Box::new(IncludePlan { name: name.to_string() })),
            None => Act::Delegate,
        }
    }
```

```rust
/// The template name of an `{% include %}` the slot may render itself, or `None`.
///
/// `None` for every shape whose resolution touches the context, which is every shape the slot
/// does not reproduce exactly.
fn constant_include_name(desc: &NodeDesc) -> Option<&str> {
    if !matches!(find_attr(desc, "isolated_context"), Some(AttrValue::Bool(false))) {
        return None;
    }
    if !matches!(find_attr(desc, "extra_context"), Some(AttrValue::Map(m)) if m.is_empty()) {
        return None;
    }
    let Some(AttrValue::FilterExpr(expr)) = find_attr(desc, "template") else {
        return None;
    };
    if !expr.filters.is_empty() {
        return None;
    }
    // `is_var` false is Django's `isinstance(var_obj, Variable)` being false.
    let (lookup, value) = &expr.var;
    if *lookup {
        return None;
    }
    let AttrValue::Variable(var) = value else {
        return None;
    };
    // `_("x")` translates at render time, and Django's `is_var` is false only when `lookups`
    // is falsy — `is_none()` and not `is_empty()`, so a hand-built descriptor with an empty
    // list is refused rather than trusted.
    if var.translate || var.lookups.is_some() {
        return None;
    }
    match var.literal.as_ref()? {
        // `SafeStr` and not `Str`: `parse_variable` wraps `unescape_string_literal`'s output in
        // `AttrValue::SafeStr` because Django wraps it in `mark_safe`
        // (`crates/template-core/src/parser/variable.rs:70`). Matching on `Str` here compiles and
        // never fires.
        //
        // An empty literal is `"" or ()`, i.e. `select_template(())`, not `("",)`.
        AttrValue::SafeStr(name) if !name.is_empty() => Some(name),
        _ => None,
    }
}
```

- [ ] **Step 5: Resolve through Django's own cache (findings 3 and 4)**

```rust
/// `IncludeNode.render`'s lookup, run against the `Context`'s own cache and nothing else.
///
/// The `setdefault` / `get` / store are Django's three lines, and writing the *store* is what
/// makes declining cheap: a target with no plan is found on the next render's `get` instead of
/// being selected a second time on the Python path.
///
/// `TemplateDoesNotExist` from `select_template` propagates, which is where Django raises it.
/// Whether the target has a usable plan is not decided here: `constant_include_name` already
/// refused the shapes that have none, so this returns the template and nothing else.
fn resolve_include<'py>(
    py: Python<'py>,
    context: &Bound<'py, PyAny>,
    node: &Bound<'py, PyAny>,
    plan: &IncludePlan,
) -> PyResult<Bound<'py, PyAny>> {
    let name = PyTuple::new(py, [plan.name.as_str()])?;
    let render_context = context.getattr("render_context")?;
    let cache = render_context
        .getattr("dicts")?
        .get_item(0)?
        .call_method1("setdefault", (node, PyDict::new(py)))?;
    // `cache.get(name)`, not `cache[name]`: the first render of any include would raise `KeyError`.
    let cached = cache.call_method1("get", (&name,))?;
    if !cached.is_none() {
        return Ok(cached);
    }
    let template = context
        .getattr("template")?
        .getattr("engine")?
        .call_method1("select_template", (&name,))?;
    cache.set_item(&name, &template)?;
    Ok(template)
}
```

`NativePlan`'s only field is `slots: Vec<Slot>` (`lib.rs:2948`), so the downcast is
`target.getattr("_native_plan")?.extract::<Py<NativePlan>>()` — step 8 shows it in place.
`getattr` failing at all, or extracting to the wrong type, is `Ok(false)`: an optimisation
must not turn a shape it does not understand into a compile error, and the node renders on
the Python path unchanged.

- [ ] **Step 6: The three context managers, entered and driven (findings 1 and 5)**

```rust
/// The three `with` blocks an `{% include %}` puts a context through, entered here and left in
/// Python's order.
///
/// Rust's `?` runs no `finally`, so three pushes set one after another and popped inline leak
/// every layer after the first that raised — into a `Context` a later render then reads
/// (finding 1). **A manager is recorded only once its `__enter__` returned**, so an unwind part-way
/// through still leaves exactly what was entered.
///
/// `Drop` cannot return the error a Python `finally` could raise; the only failure available here
/// is `ContextPopException`, which needs the stack to already be short, and that is a lost error
/// rather than a wrong answer.
struct ContextLayers<'py> {
    context: Bound<'py, PyAny>,
    /// What `context.push()` returned, entered and not yet exited.
    pushed: Option<Bound<'py, PyAny>>,
    /// What `context.render_context.push_state(target)` returned, likewise.
    pushed_state: Option<Bound<'py, PyAny>>,
    /// What `context.bind_template(target)` returned, likewise.
    bound_template: Option<Bound<'py, PyAny>>,
    /// Whether anything was ever travelling through the unwind — the body's failure, or one an
    /// `__exit__` raised. A failure that leaves cancels the body's *value*, and a manager outside
    /// it that later swallows the failure does not bring the value back (finding 7).
    tripped: bool,
    done: bool,
}

impl<'py> ContextLayers<'py> {
    /// `with context.push(**values)` (`loader_tags.py:210`) — the expression, then the enter.
    ///
    /// `values` is empty: `constant_include_name` refused every `with`, and the layer is pushed
    /// anyway because `Context.__setitem__` writes to `dicts[-1]` (finding 5).
    ///
    /// **The two pushes are two calls and not one** ("What round 2 changed"). `IncludeNode.render`
    /// pushes the context layer and *then* calls `template.render(context)`, which is where
    /// `push_state` pushes the second, and Python reads a name off the target after each. A
    /// refusal reached in either gap is answered with the call that belongs in that gap, which is
    /// only possible while the gap is still open.
    fn push_context(context: &Bound<'py, PyAny>) -> PyResult<Self> { … }

    /// `with context.render_context.push_state(self)` (`base.py:170`).
    ///
    /// **The render context is read here and not earlier**, because this is where Python reads it:
    /// `IncludeNode.render` has already pushed its own layer, so a `Context.push` override that
    /// rebinds `context.render_context` is seen.
    ///
    /// **And the object's own `push_state` is called, and what it returns is entered.** It is a
    /// `@contextmanager`, so the manager is a `_GeneratorContextManager` over a generator, and
    /// only driving it reproduces the three things the body's own frame carries: a `finally` that
    /// runs with the failure *at the `yield`* — which is what `sys.exception()` reads inside it —
    /// a `StopIteration` out of the body becoming `RuntimeError`, and a subclass's `raise`
    /// happening at the `__enter__`. Measured, entering it costs **546 ns** an include where
    /// calling the body directly costs 401 ns, and that is the price; round 5 tried to buy it back
    /// and could not ("What round 4 cost, and why round 5 could not buy it back").
    fn enter_render_context(&mut self, template: &Bound<'py, PyAny>) -> PyResult<()> {
        let render_context = self.context.getattr("render_context")?;
        let manager = render_context.call_method1("push_state", (template,))?;
        manager.call_method0("__enter__")?;
        self.pushed_state = Some(manager);
        Ok(())
    }

    /// `with context.bind_template(self)` (`base.py:172`), the branch `base.py:171` picks: the
    /// context's own method — `RequestContext` overrides it (`context.py:254`) — entered, then
    /// `context.template_name = template.name` as the `with` body's line (`base.py:173`).
    fn enter_bind_template(&mut self, template: &Bound<'py, PyAny>) -> PyResult<()> { … }

    /// Leave the layers with `outcome` — what the body produced — in flight, and answer with what
    /// the frame actually yields: [`pop_all`]'s remaining failure, or `None` when a manager on the
    /// way out swallowed it, or `outcome` itself when nothing was in flight.
    fn finish(mut self, outcome: PyResult<Py<PyAny>>) -> PyResult<Py<PyAny>> { … }

    /// Run the three `__exit__`s innermost first and answer with the failure still in flight.
    /// A failure in one does not stop the others; one that raises replaces what it carried
    /// ([`supersede`]).
    fn pop_all(&mut self, last: Option<PyErr>) -> Option<PyErr> { … }
}

/// Exit one manager with the failure in flight, and swallow it if the manager says to: a truthy
/// return is a `with` that does not propagate. A manager that raises replaces the failure.
///
/// **The return is only interrogated when something is in flight.** A `with` on its ordinary path
/// calls `__exit__(None, None, None)` and drops the result — CPython reaches the truthiness test
/// only on the path an exception took — so a manager that answers with an object whose `__bool__`
/// raises renders fine on the happy path and fails on the unhappy one, exactly as Python does.
fn exit(manager: &Bound<'_, PyAny>, last: &mut Option<PyErr>, py: Python<'_>) { … }

/// Report `error` as the failure that leaves, chaining what it replaced onto it as `__context__`.
fn supersede(last: &mut Option<PyErr>, error: PyErr, py: Python<'_>) { … }

impl Drop for ContextLayers<'_> {
    fn drop(&mut self) {
        // The emergency net only. `finish` is fallible and is the ordinary path.
        if !self.done {
            let _ = self.pop_all(None);
        }
    }
}
```

- [ ] **Step 7: The recursion budget (finding 2)**

Three pieces, because there are three ways to get here.

- [ ] The child runs at `depth + 1`, not 0, so the walk's own `MAX_WALK_DEPTH` counts include
  nesting the same way it counts `{% for %}` nesting — one counter, as Python has one.
- [x] Add a **thread-local frame count over both recursions** — native includes *and* walks —
  and decline past `MAX_WALK_FRAMES`:

```rust
/// How deep this crate may recurse into itself — native includes and walks together, *across
/// re-entrant walks*, on one thread.
const MAX_WALK_FRAMES: usize = 64;

thread_local! {
    static LIVE_FRAMES: Cell<usize> = const { Cell::new(0) };
}

/// One live frame of Rust recursion — a walk (`render_plan`) or a native include
/// (`Walk::run_include`) — released on every path out including an unwind.
struct WalkFrame;

impl WalkFrame {
    fn enter() -> Option<Self> {
        LIVE_FRAMES.with(|n| {
            if n.get() >= MAX_WALK_FRAMES {
                None
            } else {
                n.set(n.get() + 1);
                Some(WalkFrame)
            }
        })
    }
}

impl Drop for WalkFrame {
    fn drop(&mut self) {
        LIVE_FRAMES.with(|n| n.set(n.get() - 1));
    }
}
```

**Corrected after the build, and this is the correction that matters.** The first version counted
only includes, at 128, and it does not hold: a *declined* include hands the render back to Python,
which re-enters `render_plan`, and that walk nests inside the frame of the one that declined it. So
the include cap bounds one recursion and leaves the other unbounded, and the two responses must
both exist — `run_include` declining is not enough without `render_plan` declining too. `render_plan`
therefore returns `Option<Py<PyAny>>`, `None` meaning "walk declined", and both `Template._render`
and `NodeList._render_with_plan` fall through to the Python path on it.

`64`, not `128`, and measured: a self-include given a worker thread of **96 KiB on CPython 3.12.9**
and **128 KiB on 3.14.4** now raises `RecursionError`, where with the include cap alone it died
with `SIGBUS` on 3.12 at 512 KiB, 2 MiB and 768 KiB and survived only at 8 MiB. Neither floor is
this crate's: below them the *pure-Python* path dies too.

- [ ] **Step 8: Render it (finding 4)**

Add the arm to `Walk::run`, and the method:

```rust
                Act::Include(plan) => match self.run_include(slot, plan, depth) {
                    Ok(true) => census_bump(&mut self.census, &slot.class_name, false),
                    Ok(false) => self.delegate(slot)?,
                    Err(error) => return Err(self.culprit(slot, error)),
                },
```

```rust
    /// Render one `{% include %}` natively, or answer `false` so the caller delegates.
    ///
    /// **The refusal is only free before the first push.** Answering `false` hands the node back
    /// to Python, which runs `IncludeNode.render` from the top and pushes these layers a second
    /// time. So the order below is Django's order and a refusal that sits after the first push
    /// renders the target *here* instead, calling what Python would have called from that point.
    ///
    /// **One exit.** `include_inside` is the whole of `IncludeNode.render` and the
    /// `Template.render` it calls, and it has no exit of its own: the value it returns and the
    /// failure it returns both travel out through `ContextLayers::finish`.
    fn run_include(&mut self, slot: &Slot, plan: &IncludePlan, depth: usize) -> PyResult<bool> {
        let py = self.py;
        let target = resolve_include(py, &self.context, slot.node.bind(py), plan)?;
        // The frame is taken before any layer is pushed, so a refusal here still repeats nothing.
        let Some(_frame) = WalkFrame::enter() else {
            return Ok(false);
        };

        // `with context.push(**values)` (`loader_tags.py:210-211`).
        let mut layers = ContextLayers::push_context(&self.context, self.bypassed)?;
        let outcome = self.include_inside(&target, depth, &mut layers);
        self.push_python(layers.finish(outcome)?)?;
        Ok(true)
    }

    /// `IncludeNode.render` from its first read to the string it returns, and the
    /// `Template.render` it calls — with no exit of its own. The caller unwinds, once, after.
    ///
    /// **What is read, and where.** `render` is read after `context.push` and before
    /// `push_state`, which is where Python reads it; `context.template`, `_render` and
    /// `_native_plan` are read after `push_state`, which is where Python reads those. **Every read
    /// after the first push is made once and kept**, so the call a refusal makes is the one Python
    /// would have made from there.
    fn include_inside(
        &mut self,
        target: &Bound<'py, PyAny>,
        depth: usize,
        layers: &mut ContextLayers<'py>,
    ) -> PyResult<Py<PyAny>> {
        let py = self.py;

        // `template.render(context)`: the attribute read at `loader_tags.py:211`, after
        // `context.push` and before `Template.render` enters `push_state`.
        let render = target.getattr(Renders::Render.name())?;
        if !self.renders_through(&render, target, Renders::Render) {
            return Ok(render.call1((&self.context,))?.unbind());
        }

        // `with context.render_context.push_state(self)` (`base.py:170`).
        layers.enter_render_context(target)?;

        // `if context.template is None:` (`base.py:171`), asked here because this is where Python
        // asks it — after `push_state`, whose `render_context.push` may have cleared it.
        if self.context.getattr("template")?.is_none() {
            layers.enter_bind_template(target)?;
        }

        // `self._render(context)` (`base.py:174`, `base.py:176`), the second name read once.
        let plan_read = target.getattr(Renders::Plan.name())?;
        if !self.renders_through(&plan_read, target, Renders::Plan) {
            return Ok(plan_read.call1((&self.context,))?.unbind());
        }

        // `_native_plan` is the first line of `_render` (`engine/base.py:177-194`).
        // `AttributeError` is the only lookup failure that means "not one of ours"; anything else
        // is the object's own exception and belongs to the caller.
        let child = match target.getattr("_native_plan") {
            Ok(held) => match held.extract::<Py<NativePlan>>() {
                Ok(child) => child,
                Err(_) => return Ok(plan_read.call1((&self.context,))?.unbind()),
            },
            Err(error) if error.is_instance_of::<PyAttributeError>(py) => {
                return Ok(plan_read.call1((&self.context,))?.unbind())
            }
            Err(error) => return Err(error),
        };
        …
    }

    /// Whether `bound` is the attribute the stock implementation put on `target` — checked
    /// without a second read, so an object answering differently on each read sees one read.
    fn renders_through(
        &self,
        bound: &Bound<'py, PyAny>,
        target: &Bound<'py, PyAny>,
        which: Renders,
    ) -> bool { … }
```

- [ ] **Step 9: Run the structural test**

Run: `.venv/bin/python -I -m pytest tests/test_interposition.py -k IncludeSlot -q`
Expected: PASS.

- [ ] **Step 10: The three rows the census cannot see**

Each of these is a failure that renders *correctly*. The census is the only row that sees
the fast path exist; these are the only rows that see what it costs when it is wrong.

- [ ] **Balanced layers (finding 1).** Add to `tests/test_interposition.py`. A leaked layer
  is invisible until it is not: `Context.__setitem__` writes to `dicts[-1]`, so it is the
  *next* render that reads the wrong frame.

```python
class IncludeLayerBalanceTests(unittest.TestCase):
    """The two context layers an include pushes are popped on every path out.

    Depth, not identity: `context.push()` appends a dict and `pop` removes it, so the
    length is the whole observable difference between a balanced render and a leaked one.
    """

    SOURCE = '{% include "a.html" %}'

    def _engine(self, templates):
        from django_rust_templates.engine import Engine

        return Engine(
            loaders=[
                ("django_rust_templates.engine.loaders.locmem.Loader", templates),
            ],
        )

    def _depths(self, context):
        return len(context.dicts), len(context.render_context.dicts)

    def test_a_rendered_include_leaves_the_context_as_it_found_it(self):
        from django_rust_templates.engine import Context

        engine = self._engine({"a.html": "{{ x }}"})
        template = engine.from_string(self.SOURCE)
        context = Context({"x": "v"})
        before = self._depths(context)
        self.assertEqual(template.render(context), "v")
        self.assertEqual(self._depths(context), before)
```

```python
    def test_a_raising_include_leaves_the_context_as_it_found_it(self):
        """The error path is the one a `?` between the pushes would leak.

        Rendered twice, because a leak that pushes twice and pops once shows on the
        second render and not the first -- and the first is where a single-render row
        would stop looking.
        """
        from django.template import TemplateDoesNotExist

        from django_rust_templates.engine import Context

        engine = self._engine({"a.html": '{% include "missing.html" %}'})
        template = engine.from_string(self.SOURCE)
        context = Context({})
        before = self._depths(context)
        for _ in range(2):
            with self.assertRaises(TemplateDoesNotExist):
                template.render(context)
            self.assertEqual(self._depths(context), before)
```

- [ ] **The resolution cache (finding 3).** No new row, and that is the point:
  `tests/test_inheritance_gate.py::IncludeCacheGateTests::test_the_included_template_is_resolved_once`
  already pins it, as a **parity** row — its `{% for %}` body is an include, and a restarted
  `{% counter %}` prints `000000000` on both engines if the cache is bypassed. That body now
  runs through `Act::Include`, so the row changes meaning without changing a character: it
  goes from pinning *Django's* cache to pinning *ours*. **Run it, and if it goes red the slot
  is re-selecting** — which is exactly the defect finding 3 named. Do not add a second row;
  run that one and say so in the commit.

- [ ] **Stack safety (finding 2).** Add to `tests/test_interposition.py`. The failure this
  guards against is a SIGSEGV, which kills the runner rather than failing a row, so the
  assertion has to be about a child process's exit status — the only place a segfault is
  visible. This is `tests/test_loop_read_guard.py:698`'s script shape, including the
  `ensure_settings()` bootstrap a `-I -c` child needs.

```python
class IncludeRecursionTests(unittest.TestCase):
    """A self-including template raises `RecursionError` instead of running the stack out.

    Both the cap and the thread-local that enforces it across re-entrant walks are
    invisible in a passing build; this is the row that fails when either is removed.
    """

    SCRIPT = """
import sys
sys.path.insert(0, {root!r})
sys.path.insert(0, {checkout!r})
from scripts.filter_probe import ensure_settings
ensure_settings()
from django_rust_templates.engine import Context, Engine

engine = Engine(loaders=[(
    "django_rust_templates.engine.loaders.locmem.Loader",
    {{"self.html": '{{% include "self.html" %}}'}},
)])
try:
    engine.from_string('{{% include "self.html" %}}').render(Context({{}}))
except RecursionError:
    raise SystemExit(0)
raise SystemExit(3)
"""

    def test_a_self_include_raises_rather_than_exhausting_the_stack(self):
        done = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                self.SCRIPT.format(root=str(PROJECT_ROOT), checkout=str(DJANGO_CHECKOUT)),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            done.returncode,
            0,
            f"a self-include did not raise `RecursionError` "
            f"(exit {done.returncode}; -11 is SIGSEGV, 3 is no exception at all, "
            f"1 is an import that failed):\n{done.stdout}\n{done.stderr}",
        )
```

  Both constants are already module-level in `tests/test_loop_read_guard.py:94-95`; copy
  the `PROJECT_ROOT` / `DJANGO_CHECKOUT` pair and the `subprocess` / `sys` imports.

- [ ] `{% include "no-such-template.html" %}` still raises `TemplateDoesNotExist` — covered
  by the raising row above, whose included template does exactly that one level down.

- [ ] **Step 11: Commit**

```bash
git add crates/template-py/src/lib.rs tests/test_interposition.py
git commit -m "perf: the walk renders a literal \`{% include %}\` itself"
```

---

### Task 2: Take the measurement and write it down

- [ ] **Step 1: Gate first.** `scripts/check.sh` → `all checks passed`. This is the only thing
  standing between a fast render and a wrong one, and it runs *before* the benchmark, not after.
- [ ] **Step 2: Measure.** `maturin develop --release`, then
  `.venv/bin/python -I scripts/bench.py --html docs/bench.html`. Record rust-vs-jinja2 on the
  five rows in the header table and rust before/after on the same five.
- [ ] **Step 3: Re-take the include-isolation number.** Re-run the `IncludeNode.render` stub
  experiment on `real-world/complex`. Includes should fall from 81% of the render to the cost
  of the slot, and that cost is what the plan's expected figures were computed from.
- [ ] **Step 4: Correct this plan's expected numbers in place if they are wrong.** The
  predictions above (476 -> ~170 etc.) are predictions. Replace them with the measured ones and
  say which way they missed — in the doc, not only in a commit message, because the next plan
  argues from these.
- [ ] **Step 5: Commit.**

```bash
git add docs/bench.html docs/superpowers/plans/2026-10-10-native-include-lever.md
git commit -m "perf: the include lever, measured"
```

## What round 2 changed

Round 2 reproduced one further divergence, and it was in the slot's *order* rather than in a
cache: `run_include` read the target's `render`, `_render` and `_native_plan` **before** it pushed
anything, and Python reads them after.

    IncludeNode.render          loader_tags.py:196-211
      template = select_template(...)      # cache read, then the loader
      with context.push(**values):         # the FIRST push
        return template.render(context)    # `render` is read HERE, at the call
    Template.render              base.py:168-176
      with context.render_context.push_state(self):   # the SECOND push
        return self._render(context)       # `_render`, and `_native_plan` inside it, read HERE

Because `template.render` is evaluated before `Template.render` runs, a `Context` subclass whose
`push` rewrites the cached target is seen by Django on the very next line and was seen by the slot
not at all. Two probes: `push` replacing `target.render` gave native `x` against Django's
`OVERRIDE`; `push` replacing `target._native_plan` gave native `x` against Django's `y`.

**The order is now Django's, and the two pushes are two calls instead of one.** `ContextLayers`
splits into `push_context` and `enter_render_context`, with the reads between them, and a refusal
that lands after a push is answered *in place* — `target.render(context)` or
`target._render(context)`, whichever call that point in Django's sequence makes — rather than by
handing the node back, which would run `IncludeNode.render` from the top and push both layers a
second time. `Renders::Render` and `Renders::Plan` name the two points; `renders_through` takes one
at a time.

Two mutants were built and run. The old order fails with `[('x', 1), ('OVERRIDE', 1)]` and
`[('x', 1), ('y', 1)]`. The new order with `return Ok(false)` at the post-push refusals keeps both
*strings* right and is caught only by the push count, `[('OVERRIDE', 2), ('OVERRIDE', 1)]` — which
is what the reviewer meant by "simply delegating the IncludeNode would repeat side effects".
`tests/test_interposition.IncludePushOrderTests` is the pair of rows, each rendering one source
twice so Django is the oracle, with a `Context` subclass counting its own pushes. The reorder
measured flat (`complex` 417-433 against `HEAD`'s 416-435, `normal` and `micro/include` unmoved).

**One residual was disclosed here and not closed** — a `RenderContext` subclass whose `push`
clears `context.template` makes Django take `Template.render`'s `bind_template` branch *after*
`push_state`. It was closed in the next pass, below.

## What round 3 changed

The next pass reproduced four more divergences, all of them inside the two pushes whose *order*
the previous pass had just fixed. All four are closed.

1. **`render_context` was read before `Context.push`.** `ContextLayers::push_context` captured it,
   so a `push` that replaced `context.render_context` left the slot writing `template` and
   pushing the render stack on an object nothing else was looking at. `push_state` reads the
   attribute itself now, after the push, because that is where `push_state` reads it.
2. **The binding branch.** `Template.render` reads `context.template` *after* `push_state` and
   takes `with context.bind_template(self)` when it is `None` (`base.py:171-174`). The slot's read
   was before the push, and its `Renders::Plan` continuation was `target._render(context)` with
   nothing bound. `enter_bind_template` enters the context's own `bind_template(target)` — not a
   transcription of it, because `RequestContext` overrides the method (`context.py:254`) — keeps
   the manager for `__exit__`, writes `context.template_name`, and only then reads `_render`. The
   old pre-push refusal is deleted, so the branch is chosen at one point and it is Python's.
3. **The refusal read the name twice.** `renders_through` looked `render`/`_render` up and then
   `render_from` looked it up again to call it, so an object answering differently on each read
   saw two reads where Python makes one. `renders_through` takes the already-read attribute;
   `render_from` calls that object.
4. **A post-push error skipped the `finally` blocks.** `_native_plan` raising propagated straight
   out and left `Drop` to pop, and a `Drop` cannot report what `pop` finds — so a cleanup failure
   was swallowed and the wrong exception reported. `ContextLayers::unwind_with` runs
   `finish(Some(&error))` and answers with whichever failure actually reaches the caller, and
   `pop_all` runs the bind `__exit__` innermost with the in-flight exception triple. *(Round 4
   deleted `unwind_with` and `render_from` outright: once every exit runs through one `finish`,
   neither the per-exit unwind nor the per-refusal continuation has anything left to do.)*

Four single-revert mutants, one per fix, each rebuilt and run: each fails exactly its own row and
no other. `[native]` against `[delegated]` on the failing row —

    1. [("'x'", [])]                 vs [("'x'", [('push','a.html'),('pop',None)])]
    2. [("'x'", None)]               vs [("'x'", 'a.html')]
    3. [("'CALL2'", 2)]              vs [("'CALL1'", 1)]
    4. [('RuntimeError<=NoneType')]  vs [('ValueError<=RuntimeError')]

`tests/test_interposition.IncludeCallbackTests` grew round 3's four rows here. Each renders one source twice —
`{% include "a.html" %}` for the slot, `{% include which %}` for `Act::Delegate` — so Django is the
oracle and the two arms have to agree on the string *and* on what the hook observed. Measured
flat: `micro/include` 6.35-6.43 us, `real-world/complex` 414-439, `real-world/normal` 161-169.

## What round 4 changed

Round 4 reproduced three divergences, all of them in *how* the slot drives the two pushes, and one
change closes all three: the slot now **drives the real `with` managers instead of transcribing
their bodies**.

1. **The bind manager's suppression was ignored.** `__exit__` returning true is a `with` that does
   not propagate, and the slot discarded the return.
2. **An overridden `push_state` was bypassed.** Its body was transcribed, so a `RenderContext`
   subclass overriding the method was never called.
3. **`Context.push`'s manager was discarded.** Only its side effect was reproduced, so an object
   with its own `__enter__`/`__exit__` was never entered.

**The managers, not their effects.** `ContextLayers` holds the object each call returned —
`push_context`, `enter_render_context`, `enter_bind_template` — enters it with `__enter__`, and
records it only once the enter returned. `finish` runs one exit and answers with what the frame
actually yields; `pop_all` unwinds innermost first; `exit` calls `__exit__` with CPython's exception
triple and **clears the failure in flight when the return is truthy**, so a manager that swallows
ends the failure there and `finish` answers `None` — the include's output is `None` and the parent's
`''.join` is what raises.

The transcription is deleted along with what it needed: `saved_template`, the two flag fields,
`unwind_with` and `render_from`. `push_state` is `@contextmanager`-decorated, so a subclass's
`raise` happens at the `__enter__`; entering is the only way to see it.

**Round 5 put one transcription back, behind an identity guard. It came out again, and the cost it
was buying back stands.** The `@contextmanager` dance is 546 ns an include (947 ns entered against
401 ns for the body), 13.2% on `real-world/complex`, and the guard — reproduce the stock body only
while `render_context.push_state` is still `PUSH_STATE` on *this* render context — recovered 42 of
the 57 us. It is not a guard that can be made right: the body is a *generator*, and the generator's
environment is not a property of the function object. Round 5 closes with the measurement and the
two shapes that break it. `_stock.py` is back to `RENDER`/`PLAN`/`METHOD_TYPE`; nothing reads
`PUSH_STATE`.

**Evidence.** Single-slice reverts, each rebuilt with `maturin develop --release` and run against
the whole interposition module — 134 rows, so "and no other" is observed rather than asserted.
Two of the five are sub-slices, because two of the mechanisms are shared by more than one row:

| the revert | the rows that fail |
|---|---|
| `push_context` skips the manager's `__enter__` | `test_the_manager_a_push_returns_is_the_one_entered` |
| the ordinary path interrogates the return it must not read | `test_a_successful_exits_return_is_not_read` |
| `self.tripped \|= last.is_some()` dropped, so a failure raised during the unwind does not cancel the return | `test_a_cancelled_return_stays_cancelled_when_suppressed` |
| the truthy branch of `exit` ignored (`Ok(true) => {}`) | `test_a_manager_that_swallows_the_failure_renders_none`, `test_a_cancelled_return_stays_cancelled_when_suppressed` |
| `tripped` dropped whole (`None if self.tripped` arm removed) | `test_a_manager_that_swallows_the_failure_renders_none`, `test_a_cancelled_return_stays_cancelled_when_suppressed` |

The last two rows are the two that are not single-slice, and the reason is the point: both rows
end with a manager swallowing something and they differ only in whether the frame still had a
*value* when the failure started travelling. `tripped` is one mechanism serving both, which is why
dropping it whole fails two rows while dropping only the `|=` fails one.

**`test_the_render_contexts_own_push_state_is_the_one_entered` has no revert left to write.** Its
mutant was the transcription itself, guarded by `reproduces_push_state`, and both are gone; the
only way left to bypass a `push_state` override is to not call it, and every spelling of that in
the current tree either does nothing to this row (looking the name up on the subclass, which finds
the override anyway) or takes the whole layer out and fails twenty-one unrelated rows. The row
stands on the four lines it pins — `render_context.getattr` → `call_method1("push_state", …)` →
`__enter__` — read against `base.py:170`.

`tests/test_interposition.IncludeCallbackTests` is nine rows at this point, each rendering one
source twice — `{% include "a.html" %}` for the slot, `{% include which %}` for `Act::Delegate` — so
Django is the oracle and the two arms have to agree on the string *and* on what the hook observed.
The last two rows are the `with` statement's own rules rather than the managers': an `__exit__` on
the ordinary path is never read, and a failure raised during the unwind cancels the frame's value
even when an outer manager suppresses it. Neither is visible in a stock manager, which is why
round 4 shipped without them.

## What round 6 changed

Round 6 reproduced three more divergences, all of them in **what state `__exit__` runs under** and
all three closed by *delegating the one call that has no Rust spelling*.

1. **An exceptional `__exit__` was not running with the failure being handled.** CPython reaches it
   through `WITH_EXCEPT_START`, which runs the call with the travelling failure as the *handled*
   exception — what `sys.exception()` reads, and what `_PyErr_SetObject` chains a newly raised
   exception's `__context__` onto. `PyErr::restore`, the only handle PyO3 exposes, sets the
   *raised* exception, a different slot. A `push` manager recording `type(sys.exception()).__name__`
   logged `NoneType` here and `TemplateDoesNotExist` under delegation.
2. **`supersede` appended the replaced failure at the tail of the newcomer's chain.** With no
   handled state established, a raise inside `__exit__` left the error's own `__context__` in place,
   so the travelling failure had to be spliced on somewhere — and the tail is not where Python puts
   it. Measured, a manager raising with a `KeyError` already as its context chained
   `RuntimeError → KeyError → TemplateDoesNotExist` here against delegation's
   `RuntimeError → TemplateDoesNotExist`.
3. **The exit callable was resolved after the enter.** `BEFORE_WITH` resolves both names with
   `_PyObject_LookupSpecial` — off the type, skipping the instance `__dict__`, and *before*
   `__enter__` runs — so a manager whose `__enter__` replaces its class's `__exit__` is still exited
   by the original.

**The fix is a deletion plus one Python function.** `supersede` and everything it needed are gone;
`exit` splits into an ordinary path that calls `__exit__(None, None, None)` for its side effect, and
an exceptional path that goes through `engine/_unwind.py::UNWIND`, which establishes the handled
state with a real `except` clause and lets Python build the chain. `enter` resolves `__enter__` and
`__exit__` off the type and binds both before entering. The chaining is Python's, which is why
nothing in `lib.rs` writes `__context__` any more.

**What it costs is in "What rounds 5 and 6 cost", above: 0.68 us an include for the off-the-type
binding, and nothing for the rest.** The restructure was free against round 5.

**Evidence.** Two reverts, each rebuilt with `maturin develop --release` and run against the whole
interposition module — 137 rows, so "and no other" is observed:

| the revert | the rows that fail |
|---|---|
| both names resolved *after* the enter (`bind("__exit__")` moved below `call0`) | `test_the_exit_callable_is_resolved_before_the_enter` |
| the exceptional exit called from Rust instead of through `UNWIND` | `test_an_exceptional_exit_runs_with_the_failure_being_handled`, `test_an_exit_that_raises_chains_onto_the_failure_in_flight`, `test_a_cleanup_failure_keeps_the_chain_python_built`, `test_a_post_push_error_leaves_through_the_finally`, `test_the_outer_cleanup_failure_is_the_one_that_leaves` |

The second revert takes five rows, three of them older than this round, which is the finding: the
handled state is one mechanism that the chain rows and the `sys.exception()` row both stand on.

**One row was written wrong first and had to be re-derived.** `_ChainWalk` originally set its
context with `try: raise KeyError / except KeyError:`, and both arms then produced
`RuntimeError → KeyError → TemplateDoesNotExist` — the row passed under the bug it was written to
catch. `raise` in CPython calls `PyException_SetContext` **unconditionally** when something is
being handled, overwriting a context that was already there; the original fixture had the
`KeyError` getting its context from the handled failure in both arms, so the two spellings
converged. Setting `error.__context__` by hand instead puts the divergence back where it belongs.
`tests/test_interposition._chain` is what reads it, and the row is named for what it now measures
rather than what it was meant to.

`IncludeCallbackTests` is twelve rows: the nine above plus the three the round-6 findings produced,
which are the only rows about CPython's `with` that are about its *exception state* rather than its
call order.

## What round 1 changed

Round 1 raised five findings; all five were taken, and two were defects that would have shipped.

1. **Cleanup ordering** — `run_include` popped inline after the pushes, so any `?` between them
   leaked a context layer. Now a `ContextLayers` guard whose `Drop` pops exactly the layers its
   flags say are on. *This would have shipped.*
2. **The predicate** — `{% include "123" %}` is a *string* literal (`Variable` tries `int()` on
   the quoted text, fails, and unescapes), so the slot **should** take it; the round 0 draft had
   it on the "must decline" list. `lookups` is now required `is_none()` rather than merely empty.
3. **The recursion budget** — a per-`Walk` counter is reset by every Python re-entry, so the
   worst case is not 64 levels but a function of the stack. Now a thread-local count that is not
   reset, plus `depth + 1` into the child so `MAX_WALK_DEPTH` stays honest. *This would have
   shipped, as a SIGSEGV.*
4. **The cache lifetime** — plan-owned `OnceLock` replaced by Django's own
   `render_context.dicts[0]`, node-keyed, which also removes the double `select_template` on the
   decline path and the plan-to-plan reference cycle the round 0 draft introduced.
5. **The output boundary** — the child now runs into its own `out`/`spill`/`nodes` frame, so an
   include is one element of the parent's list and a non-`str` piece is reported at its inner
   ordinal, as `NodeList.render` reports it.

## Non-goals

- **`{% extends %}`.** `micro/extends` is 10.0 vs 5.4 — a 1.86x ratio worth 4.6 us. `ExtendsNode`
  carries block-context surgery (`add_blocks`, `push_state(isolated_context=False)`, the
  `contains_nontext` ordering rule) that no micro row pays for, and `real-world/*` already wins
  its render. Not bang for the buck; a separate plan if the ratio is wanted.
- **`micro/loop-text`.** 10.0 vs 5.7, a 4.3 us gap over 50 iterations, i.e. ~86 ns per iteration —
  one Python `__setitem__` per row where the walk could write `dicts[-1]` directly. Cheap, worth
  ~0.4% of `complex`. Fold it into whichever plan next touches `run_for`. **Still not taken, and
  re-priced.** `context[key] = value` from Python is 70.9 ns and `pushed[key] = value` is 29.2 ns, so
  the saving is ~55 ns an iteration once the `PyObject_SetItem` fallback is priced in: 2.8 us on
  `micro/loop-text` (10.0 -> 7.3 against a jinja2 at 5.6, so the row still loses) and 4.6 us, 1.3%, on
  `complex`. It needs a stock `BaseContext.__setitem__` captured at import to decline a `Context`
  subclass that overrides it, which is more machinery than either number pays for.
- **Making `render_context.dicts[0]` identical in shape.** Django's `setdefault(self, {})` writes
  the *IncludeNode* into that dict. We write the same key, so this is now free — but it is a
  consequence of finding 4, not a separate goal.
- **Hoisting the cache chain onto the `Walk`, and memoising the resolution.** `resolve_include`
  runs six Python attribute calls per include — `render_context`, `dicts`, `get_item(0)`,
  `setdefault`, `get_item(name)`, plus `template` in `run_include` — and three of them
  (`render_context`, `dicts`, the `dicts[0]` dict) look render-invariant, because `push_state`
  appends to that list and never replaces it and index 0 is never popped during a render. Both
  were built, measured at `complex` 409 -> 353, and reverted: the invariance is false the moment a
  loop sequence's own `__next__` runs, which the walk does not delegate. See "The measurement this
  plan is bought with". `IncludeCacheFreshnessTests` is what is left of it.
- **Everything S2–S4 already claim.** `CompiledTemplate`, the retained IR, the by-`node_id`
  projection, the lazy `nodelist` property. This plan touches none of it and is not a substitute
  for it.
