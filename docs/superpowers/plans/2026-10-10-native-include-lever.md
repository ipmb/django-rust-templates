# The native `{% include %}` lever Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the native render walk resolve and render a `{% include %}` itself, so a page
whose hot loop is an include stops paying five microseconds of Python machinery per
iteration.

**Architecture:** One new `Act` variant, `Act::Include`, carrying the template name read off
the descriptor at plan time. At render the walk resolves the target `Template` through
**Django's own node-keyed `render_context` cache**, pushes the two context layers Django
pushes under an RAII guard, and recurses into the included template's `_native_plan` slots
with the same `Walk` — no second `Walk::new`, no `Template.render` frame, no
`IncludeNode.render` frame. Everything the slot does not reproduce exactly declines to
`Act::Delegate`, which is the Python path unchanged.

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

## Review Focus

The five things this plan can get wrong that no test it writes will name by itself. Rows 1
and 2 are the two round 1 found, and they are the two that would have shipped.

1. **The context layers must unwind on every path.** `context.push()` and
   `render_context.push()/template=` are three pieces of state set one after another, and
   Rust's `?` does not run `finally`. Any `?` between the first push and the last pop — a
   failed `getattr`, a raising `setattr`, an error out of the child render — leaks a layer
   into a `Context` that a later render then reads. Must be an RAII guard, not inline calls.
2. **The recursion budget must not reset.** The walk has never recursed into a *different*
   template's plan before, and `MAX_WALK_DEPTH` (`lib.rs:2302`, 500) is passed *as a
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
   (`push_python`, `lib.rs:3470`), and the ordinal is in the `TypeError`'s message. Sharing
   one accumulator makes that ordinal the outer one.
5. **The push that looks redundant.** `extra_context` is empty on our fast path, so
   `context.push()` pushes an *empty* dictionary. Dropping it is the obvious saving and it is
   wrong: `Context.__setitem__` writes to `dicts[-1]`, so a node in the included template that
   assigns at top level would leak out.

---

### Task 1: The `Act::Include` slot

**Files:**
- Modify: `crates/template-py/src/lib.rs` — `Act` (2407), `Slot` (2422), `plan_slot` (2908),
  `Walk::run` (3166), `push_python` (3455), `render_plan` (3891)
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
/// An `_native_plan` that is absent or is not a `NativePlan` is `Ok(None)`: the optimisation is
/// declined and the node goes back to Python whole.
fn resolve_include<'py>(
    py: Python<'py>,
    context: &Bound<'py, PyAny>,
    node: &Bound<'py, PyAny>,
    plan: &IncludePlan,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let name = PyTuple::new(py, [plan.name.as_str()])?;
    let render_context = context.getattr("render_context")?;
    let cache = render_context
        .getattr("dicts")?
        .get_item(0)?
        .call_method1("setdefault", (node, PyDict::new(py)))?;
    let cached = cache.get_item(&name)?;
    if !cached.is_none() {
        return Ok(Some(cached));
    }
    let template = context
        .getattr("template")?
        .getattr("engine")?
        .call_method1("select_template", (&name,))?;
    cache.set_item(&name, &template)?;
    Ok(Some(template))
}
```

`NativePlan`'s only field is `slots: Vec<Slot>` (`lib.rs:2948`), so the downcast is
`target.getattr("_native_plan")?.extract::<Py<NativePlan>>()` — step 8 shows it in place.
`getattr` failing at all, or extracting to the wrong type, is `Ok(false)`: an optimisation
must not turn a shape it does not understand into a compile error, and the node renders on
the Python path unchanged.

- [ ] **Step 6: The two context layers, under a guard (findings 1 and 5)**

```rust
/// The context layers `IncludeNode.render` and `Template.render` push, popped by `Drop`.
///
/// Rust's `?` runs no `finally`, so three pushes set one after another and popped inline leak
/// every layer after the first that raised — into a `Context` a later render then reads
/// (finding 1). Each flag says whether that layer is *on*, so an unwind part-way through
/// `push` still pops exactly what it pushed.
///
/// `Drop` cannot return the error a Python `finally` could raise; the only failure available
/// here is `ContextPopException`, which needs the stack to already be short, and that is a
/// lost error rather than a wrong answer.
struct ContextLayers<'py> {
    context: Bound<'py, PyAny>,
    render_context: Bound<'py, PyAny>,
    saved_template: Option<Bound<'py, PyAny>>,
    pushed_context: bool,
    pushed_render_context: bool,
}

impl<'py> ContextLayers<'py> {
    /// `with context.push(**values)` — `values` is empty, `constant_include_name` refused every
    /// `with`, and the layer is pushed anyway because `Context.__setitem__` writes to
    /// `dicts[-1]` (finding 5).
    ///
    /// Then `Template.render`'s `with context.render_context.push_state(self)`, whose own order
    /// is `template` first and `push` second. `bind_template` is *not* called: `context.template`
    /// is not `None` inside a walk, and the caller checked.
    /// No `py` token is held: every call below is a `Bound` method, and none needs it.
    fn push(context: &Bound<'py, PyAny>, template: &Bound<'py, PyAny>) -> PyResult<Self> {
        let render_context = context.getattr("render_context")?;
        let mut layers = Self {
            context: context.clone(),
            render_context,
            saved_template: None,
            pushed_context: false,
            pushed_render_context: false,
        };
        layers.context.call_method0("push")?;
        layers.pushed_context = true;
        layers.saved_template = Some(layers.render_context.getattr("template")?);
        layers.render_context.setattr("template", template)?;
        layers.render_context.call_method0("push")?;
        layers.pushed_render_context = true;
        Ok(layers)
    }
}

impl Drop for ContextLayers<'_> {
    fn drop(&mut self) {
        if self.pushed_render_context {
            let _ = self.render_context.call_method0("pop");
        }
        if let Some(saved) = self.saved_template.take() {
            let _ = self.render_context.setattr("template", saved);
        }
        if self.pushed_context {
            let _ = self.context.call_method0("pop");
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
    /// Render one `{% include %}` natively, or say `false` so the caller delegates.
    ///
    /// The sequence is `IncludeNode.render`'s for the shape `constant_include_name` accepted,
    /// then `Template.render`'s non-binding branch — the trace in this plan's header.
    fn run_include(&mut self, slot: &Slot, plan: &IncludePlan, depth: usize) -> PyResult<bool> {
        let py = self.py;
        // `context.template` is never `None` inside a walk, because `_render` is only reached
        // through `Template.render` and through `ExtendsNode`'s `_render`, both inside a binding.
        // Checked rather than assumed: the other branch would need `bind_template`.
        if self.context.getattr("template")?.is_none() {
            return Ok(false);
        }
        let Some(target) = resolve_include(py, &self.context, slot.node.bind(py), plan)? else {
            return Ok(false);
        };
        let Ok(child) = target.getattr("_native_plan").and_then(|p| p.extract::<Py<NativePlan>>())
        else {
            return Ok(false);
        };
        let Some(_frame) = WalkFrame::enter() else {
            return Ok(false);
        };
        let layers = ContextLayers::push(&self.context, &target)?;

        // The included template's output is `SafeString("".join(...))` of *its own* nodelist, so
        // it is one element of the parent's list and the ordinal a non-`str` piece is reported at
        // is the index in that inner list. A frame of its own, swapped in and out (finding 4).
        let outer_out = std::mem::take(&mut self.out);
        let outer_spill = self.spill.take();
        let outer_nodes = std::mem::replace(&mut self.nodes, 0);

        let child = child.bind(py);
        let child = child.borrow();
        let rendered = self.run(&child.slots, depth + 1);

        let inner_out = std::mem::replace(&mut self.out, outer_out);
        let inner_spill = self.spill.take();
        self.spill = outer_spill;
        self.nodes = outer_nodes;

        let inner = rendered.and_then(|()| match inner_spill {
            None => Ok(PyString::new(py, &inner_out).into_any().unbind()),
            Some(pieces) => Ok(PyString::new(py, "")
                .call_method1("join", (PyList::new(py, pieces)?,))?
                .unbind()),
        });
        // The two `finally` blocks run before the piece is appended, and before the error
        // leaves — `with context.push(...): return template.render(context)`.
        drop(layers);
        match inner {
            Ok(piece) => {
                self.push_python(piece)?;
                Ok(true)
            }
            Err(error) => Err(error),
        }
    }
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
  ~0.4% of `complex`. Fold it into whichever plan next touches `run_for`.
- **Making `render_context.dicts[0]` identical in shape.** Django's `setdefault(self, {})` writes
  the *IncludeNode* into that dict. We write the same key, so this is now free — but it is a
  consequence of finding 4, not a separate goal.
- **Hoisting the cache chain onto the `Walk`.** `resolve_include` runs six Python attribute
  calls per include — `render_context`, `dicts`, `get_item(0)`, `setdefault`, `get_item(name)`,
  plus `template` in `run_include` — and three of those (`render_context`, `dicts`, the
  `dicts[0]` list object) are render-invariant: `push_state` appends to that list and never
  replaces it, and index 0 is never popped during a render. Hoisting them to the `Walk` is
  worth roughly half the per-include Python traffic. **Not in this plan**: it is a second pass
  on this slot, it adds an invariant that has to hold for the whole render, and the first pass
  should be measured before it is tuned. Take it only if Task 2's numbers say the residual is
  still Python-call-bound.
- **Everything S2–S4 already claim.** `CompiledTemplate`, the retained IR, the by-`node_id`
  projection, the lazy `nodelist` property. This plan touches none of it and is not a substitute
  for it.
