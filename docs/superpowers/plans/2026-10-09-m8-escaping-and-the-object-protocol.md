# M8 — Escaping and the Python object protocol: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the primitive resolver — call a resolved callable the way Django's `Variable._resolve_lookup` calls it, and substitute `string_if_invalid` where Django substitutes it — so that §10.3's M8 row ("`SafeData`, `Promise`, callables, `alters_data`, `do_not_call_in_templates`; resolver complete") is true of our engine rather than of Django's. **The `Promise` half of that row is a contract, not a change**: Task 3 measured that M8 has no forcing site to build, and the obligation is discharged by *not adding* one — the goal here said "force a `Promise` where Django forces it" until that measurement, and the phrase was struck rather than left standing.

**Architecture:** M3 built the resolver's *shape* and deliberately left two arms unimplemented. `CallArm` is a three-way enum (`crates/template-core/src/parser/resolve.rs:85`); `DoNotCall` ships because Django's branch is `pass` and needs no call, and `Call`/`AltersData` return `LookupOutcome::Unimplemented` naming M8 (`:243`, `:253`). M8 fills those two arms and adds the `Promise` obligation, **on the seam that already exists** — `LookupTarget` (`resolve.rs:107`, "nine methods, one per predicate Django actually evaluates") plus `FilterHost` (`crates/template-core/src/filters/host.rs:195`, which already carries `is_safe`/`mark_safe`/`conditional_escape`). No new trait is introduced. What makes this milestone different from M6 and M7 is its **gate**: the pinned suite cannot see any of it (Task 4), because the render path is Django's own `Variable._resolve_lookup` until M9 interposes `base`.

**Tech Stack:** Rust (edition 2021, no Python in `template-core`), PyO3 0.29 in `template-py`, Python 3.14 shell, Django 6.0.9 pinned at `.django/`.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` — read §5.2 (the seam, **rewritten by this milestone's authoring**: its trait names were proposals, and §D1 records what changed and why), §6 (ownership), §9 (the three test layers), §10.3 (the M8 row **and the vacuity rule**, whose scope paragraph assigns M8 the question it must answer), §10.4 (the M8 obligation). This plan argues from the spec; where they disagree the spec wins and this plan is wrong. Read `.review/m8-recon.md` before this file; it carries the measurements this plan's architecture rests on, and §D records where this plan corrects the recon.

## Global Constraints

Copied verbatim from the spec and the M4/M5/M6/M7 plans; every task's requirements implicitly include them.

- **Django 6.0.9**, pinned at `.django/`. Never modified. It is the
  specification, not a dependency.
- **`cargo test` does not run clippy.** `scripts/check.sh` gates on
  `cargo clippy --workspace --all-targets -- -D warnings`. Every task runs
  clippy explicitly before committing. Two lints have already bitten this
  project's plan-supplied code: `clippy::module_inception` and
  `clippy::type_complexity`. Name tuples with a `type` alias before writing
  them; never nest a module `x/x.rs`.
- **No indented non-Rust code in doc comments.** rustdoc compiles indented
  blocks as doctests and `cargo test` will run them; this already broke a build
  once. Quote code inline or fence it with ```` ```text ````.
- **`source scripts/env.sh` before any cargo or venv command.** `cargo` is not
  on `PATH` otherwise. (`scripts/env.sh` also sets `PIP_CONFIG_FILE=/dev/null`;
  the global pip config is broken and is deliberately not fixed.)
- **`maturin develop` after every Rust change** or the Python tests silently
  exercise a stale `.so`.
- **Python is never a dependency of `template-core`.** If the engine needs
  something from Python it goes behind a trait implemented in `template-py`
  (§5.2).
- **Django's Python source is not vendored.** Error strings, positions and reprs
  are ported verbatim; algorithms are written against Django as a spec (§6).
- **A change is not committed until its review round returns APPROVE or all
  blocker/major findings are resolved** (§11).
- **Every snippet in this plan is executed against the pinned tree before the
  plan is committed, and a plan is not reviewable until it is** (§11). Where a
  step's evidence is a mutation, the mutation is *run* — M2 lost three plan
  steps to predictions about what an edit produces that nobody had made.
  **M8 has measured its fixtures already**: `.review/m8-measurements/m8_measure_callarm.py`
  and `m8_measure_alters.py` execute stock Django's arm rather than quoting it,
  and the tables in Tasks 1 and 2 are those transcripts.
- **A task states its declared environment.** M2's and M3's defect rates were
  dominated by correct knowledge of Django combined with wrong assumptions about
  the harness. Each task below names what is constructible at that point, what
  imports resolve, and what the shim does and does not shadow.
- **Every guard is observed failing for its intended reason before it is kept**
  (§11), by disabling the guard and re-running. A guard nobody has seen fail is
  a comment.
- **The plan publishes derivations and pointers, not snapshots of them.** Every
  fixable plan defect in this project has been a derivable value written into
  prose; and a copied code block propagates like a copied citation. Where a fact
  is computed somewhere, this plan names *where* and does not restate it.
- **This milestone's own trap list.** `.review/m8-recon.md` §6 names four,
  measured: reach a filter through `register.filters[name]` and never through
  `defaultfilters.<name>` (six names differ; `escape` is one); put
  `alters_data`/`do_not_call_in_templates` on the **function**, not the class
  body, unless the test is deliberately about a callable *instance*; a `str()`
  in a *printer* forces a `Promise` the code did not; and a bare
  `settings.configure` without a `TEMPLATES` backend makes the pins error.
  Every task's tests inherit these.
- **`_shim.install()` comes last in any script that measures ownership**
  (`.review/m7-seams.md`): it rewrites the callables Django registered, so the
  identity clause reads "not Django's" for every name.

## Review Focus

The six inputs and conditions the spec implies but no single task's tests
exercise, most likely first. Each is pinned by a test in the task that owns the
code. (**Six, not five**: item 6 was added after the first draft, when the
`SafeStr` crossing turned out to have a producer-count question in it — and it
was *corrected* again when Task 3 found the count was two and not three.)

1. **The invalid-value substitution must not end the bit loop.** `base.py:996`
   assigns `current = context.template.engine.string_if_invalid` **inside**
   `for bit in self.lookups:`, so the next bit resolves against the invalid
   value. Measured discriminator (`m8_measure_alters.py`): with
   `string_if_invalid='<<invalid>>'` and `x.alters_data = True`, `{{ x.upper }}`
   renders `'&lt;&lt;INVALID&gt;&gt;'` — `.upper` ran on the invalid string. An
   implementation that substitutes and *stops* renders `'&lt;&lt;invalid&gt;&gt;'`,
   the value of the row above it, and the difference is invisible in every other
   input in this milestone. **Owned by Task 2; pinned by that exact pair of
   rows**, which only separate if the loop continued.
2. **The `TypeError` branch re-raises into the *outer* handler.** `base.py:1000-1013`
   is three nested tries whose innermost `else` is a bare `raise` (`:1010`),
   which does not stay in the callable arm — it reaches the outer
   `except Exception` (`:1016`), where only `silent_variable_failure` converts it.
   Measured: a zero-arg callable whose body raises `TypeError` **propagates**
   (`m8_measure_callarm.py`), while the same exception carrying
   `silent_variable_failure` renders the invalid value. The trap is that
   `signature()` and `bind()` both *succeed* for a zero-arg callable, so an
   implementation that treats "TypeError from the call" as "arguments were
   required" gets the right answer on the args case and silently swallows a real
   error on this one — wrong in the safe direction, which is why it survives
   review. **Owned by Task 1; pinned by both rows.**
3. **The flag is read off the *resolved object*, and a class-body flag is
   inert.** Measured (`.review/m8-recon.md` §4): `getattr(ClassAttr().save,
   'alters_data', False)` is `False` while `getattr(FnAttr().save, …)` is `True`,
   because the resolved value is a bound method and a bound method proxies to
   `__func__`. `do_not_call_in_templates = True` on a class body whose attribute
   is a method is likewise inert — `{{ x.name }}` renders `'ada'`, i.e. **the
   method ran**. One rule, not two spellings: read the flag off whatever the
   lookup produced. **Owned by Task 2 for the `alters_data` half and Task 1 for
   the `do_not_call_in_templates` half** — not an arbitrary split: the two halves
   fail in opposite directions. Task 1's inert-flag row must *call*, so a port
   that consults the owning class suppresses a call Django makes; Task 2's
   function-attribute pair must *substitute*, so a port that consults the owning
   class invokes a method Django suppresses — the failure the flag exists to
   prevent.
4. **The resolver must hand a `Promise` back unforced, and no render row can
   tell.** `Variable.resolve` (`base.py:924-940`) returns it unforced, and the
   claim the gate rests on is that **no forcing site is on the resolve path** —
   not an enumeration of Django's sites. **An earlier draft of this line claimed
   forcing "changes `{{ x }}` and `{{ x|escape }}` in opposite directions" — that
   is false and is corrected here.** Measured (`m8_measure_promise.py`): all
   eight render rows are identical either way, and `str()` on a proxy whose cast
   result was `mark_safe`'d returns the `SafeString` **unchanged**, so the
   safeness survives and `conditional_escape` still sees `__html__`. The real
   signal is a 57-filter sweep, where **consumers do distinguish** in both
   directions: `phone2numeric` (`is_safe=True`) escapes the unforced value and
   does not escape the forced one, and `pluralize` takes Django's **TypeError**
   branch (which consults `len`) for the proxy and its **ValueError** branch
   (which returns `""`) for the forced string — Django branching on the value's
   *type*, not only its content. All of it is latent until M9 puts our resolver
   on the render path, so **the row must be a direct-entry one**: the gate pins
   that `_native.resolve` returns the object it was given, by identity.
   **Owned by Task 3.**
5. **M8's gate cannot be the pinned suite, and a direct-entry corpus fixes only
   half of it.** Measured: the ten M8-relevant `.django` modules run 88 tests
   with 0 failures while `_native.resolve` raises on every callable path
   (`m8_measure_corpus.py`) — the render path is Django's until M9. §10.3's scope
   paragraph says M8's row "reads out of scope here and owes that second question
   an answer of its own": the gate's *instrument*, not its vacuity. **Owned by
   Task 4.** But the reviewer's caution is the load-bearing half, and it is
   measured: **the property `alters_data` protects is "the method was not
   called", and that is not a property of the output.** With
   `string_if_invalid=''` and an `alters_data` callable returning `''`, a port
   that calls it produces the identical row — measured, `calls=0`,
   `('value', '')` (`m8_measure_alters.py` §2). So **at least one M8 row must
   observe the call** — a side-effect counter, not a comparison — and that is the
   non-parity row this milestone owes. It is the fifth appearance of the rule
   that a parity instrument blind to a class of defect needs a non-parity row
   beside it.
6. **Every value M8 newly produces must name its crossing.** The `SafeStr` bridge
   is one tested crossing (`_decode_value`, `_impl/parser.py:666-667`, pinned at
   `tests/test_materialise.py:318` and `:470-477`), and recon §3 was written
   calling it a live defect anyway — by the person who had just read it. M8 adds
   two producers that are **not** the materialiser, both landing as `current`
   inside Rust's arena: **a called callable's return** and **the substituted
   invalid value**. **Owned by Task 1 and Task 2, one row each**, in
   `test_a_safe_str_becomes_a_safe_string`'s shape. The obligation is totality,
   not repair: "does `SafeStr` need fixing" is answered, while "is every producer
   bridged" gets a new wrong answer each time a producer is added.
   **A third producer — "a forced `Promise`" — was named in recon §4 and §Q2 and
   is not one.** Task 3 measured that M8 forces nothing at all (no forcing site
   exists to build), so the list is two, and the count is corrected here rather
   than left as a third row nobody could write a test for.

## File Structure

| file | change | why |
|---|---|---|
| `crates/template-core/src/parser/resolve.rs` | modify | the two arms, `CallFailure`, and the two `LookupTarget` additions |
| `crates/template-py/src/lib.rs` | modify | `PyTarget`: the `call` impl, the pre-fetched `invalid` target, and `_native.resolve`'s plumbing |
| `crates/template-core/src/filters/host.rs` | **not modified** | `force_promise` was the expected change here; Task 3 measured that M8 has no forcing site to build, so the row is retired rather than left conditional |
| `tests/test_resolve_callarm.py` | create | Task 1's gate: the callable arm, per branch, against the pinned tree |
| `tests/test_resolve_invalid.py` | create | Task 2's gate: the substitution, and the loop-continuation pair |
| `tests/test_escape_protocol.py` | create | Task 3's gate: `Promise`, `SafeData`, `__html__` at the boundary |
| `tests/test_m8_side_effects.py` | create | Task 4: the non-parity rows — the counter that observes the call |
| `scripts/anchors.txt` | **already modified** | seeded during plan authoring for §5.2's rewritten names and Task 3's file; Task 3 Step 4 seeds its own file's four citations |
| `docs/superpowers/specs/…-design.md` | modify | §5.2's trait names (§D1) |

No file is created that a task does not fill, and no module is split: `resolve.rs`
is 800-odd lines with one responsibility (Django's `_resolve_lookup`, ported) and
Task 1 adds one arm to it.

---

### Task 1: The callable arm — `CallArm::Call`

**Files:**
- Modify: `crates/template-core/src/parser/resolve.rs`
- Modify: `crates/template-py/src/lib.rs`
- Test: `tests/test_resolve_callarm.py` (create)
- Test: `crates/template-core/src/parser/resolve.rs` (the in-crate tests, which is where M3's `CallArm` tests already live at `:680`, `:705`, `:725`)

**Interfaces:**
- Consumes: `LookupTarget` (`resolve.rs:107`), `CallArm` (`:85`), `LookupOutcome` (`:170`), `resolve_lookup` (`:221`), and M3's `LookupOutcome::Escaped`, whose two-branch contract is already written down (`:186-215`) and which this arm feeds.
- Produces: `pub enum CallFailure`, `LookupTarget::call`, and `LookupTarget::string_if_invalid`. **This task owns `string_if_invalid` rather than Task 2**, because the args-required branch below needs it — and because its *laziness* is a correctness property of the method, not of one caller: the attribute is `context.template.engine.string_if_invalid`, and an **eager** read raises `AttributeError` on an unbound context where Django returns the value (measured: `Variable("x")._resolve_lookup(Context({"x": "plain"}))` → `('value', 'plain')` while the attribute itself raises `"'NoneType' object has no attribute 'engine'"`). Task 2 consumes both for the `AltersData` arm.

**Declared environment.** `cargo test -p template-core` runs the in-crate tests
with no Python at all; `tests/test_resolve_callarm.py` runs under
`.venv/bin/python -I` with `.django/` on the path, and needs `maturin develop`
first. The shim interposes three files (`__init__`, `exceptions`, `parser`) and
`unshimmed()` is 25, so nothing here is reached through `django.template`'s
public import — the test calls `_native.resolve` directly.

**The fixture table, and both columns are executed** — the left from
`.review/m8-measurements/m8_measure_callarm.py`, the right from running Step 1's
snippet against the pinned tree. The right column is the one that matters,
because it is `Variable._resolve_lookup` answering, not a transcription:

| fixture | stock render, `string_if_invalid='<<invalid>>'` | `_resolve_lookup` (the oracle) | ours today |
|---|---|---|---|
| zero-arg function | `'NO_ARGS'` | `('value', 'NO_ARGS')` | `('raised', NotImplementedError, …M8's)` |
| function requiring one arg | `'&lt;&lt;invalid&gt;&gt;'` | `('value', '<<invalid>>')` | same stub |
| callable instance (`__call__`) | `'INSTANCE_CALLED'` | `('value', 'INSTANCE_CALLED')` | same stub |
| zero-arg function whose body raises `TypeError` | **propagates** | `('raised', TypeError, 'boom from the body')` | `('raised', NotImplementedError, …M8's)` |
| the same, with `silent_variable_failure` | `'&lt;&lt;invalid&gt;&gt;'` | `('value', '<<invalid>>')` | same stub |
| `do_not_call_in_templates` on a *class body*, `{{ x.name }}` | `'ada'` | `('value', 'ada')` | `('raised', …, M8's)` — **the arm runs because the flag is inert** |

The fourth row is the pair to read twice: **both sides raise**, so it fails on
the exception *type* rather than on raised-versus-value. Any port that
substitutes instead of re-raising turns that row into a value and fails it — the
row that Review Focus 2 is about.

Two rows are **not** here, and their absence is a boundary rather than an
omission: every fixture whose value is reached through `CallArm::AltersData`
(substitution, not a call) is Task 2's, in Task 2's file. A test owned by a task
that cannot make it pass is a test the task's own Step 6 would have to skip.
`do_not_call_in_templates` stays because the flag being *inert* is what routes
the row through this arm: M3's `call_arm` (`lib.rs:1825`) already classifies it
`Call`, so only the call is missing.

The last row is asserted to *call*, which is the point: `getattr(bound_method,
'do_not_call_in_templates')` is `False`, so a later "fix" that consults the
owning class would suppress a call Django makes and fail this row loudly. Its
mirror image — `alters_data` written on the function beside one written on the
class body, where the *wrong* direction is invoking a method Django suppresses —
is Task 2's, because that is where the substitution is.

- [ ] **Step 1: Write the failing test**

The rows are **parity rows against `Variable._resolve_lookup`**, in the idiom
`tests/test_render_parity.py`'s `ResolverParityTests` already establishes —
compared with Django, never with recorded output, because a literal expectation
pins what this engine does today and keeps passing after the pinned tree moves.

Parity has signal here for a reason worth stating, since `.review/m8-recon.md` §2
is a case where it did not: **the two sides run different code.** The recon's
blind rows were blind because *only Django ran*; here the Rust arm and Python's
arm are separate implementations of one rule, so a wrong branch is a difference.

```python
"""M8 Task 1: the callable arm, compared with Django's.

`_resolve_lookup`, not `resolve`: the port mirrors the private method. And the
contexts are bound to a template, because two rows below reach
`context.template.engine.string_if_invalid` and would otherwise measure an
`AttributeError` about an unbound context — both of these are properties
`ResolverParityTests` documents and this class inherits rather than restates.
"""

import unittest

from tests.test_render_parity import _ParityTest, _outcome


class CallArmParityTests(_ParityTest):
    def assert_calls_like_django(self, value, path="x", invalid="<<invalid>>"):
        from django.template import Context
        from django.template.base import Variable

        from django_rust_templates import _native

        engine = self.engine.__class__(string_if_invalid=invalid)
        ours_context = Context({"x": value})
        theirs_context = Context({"x": value})
        with ours_context.bind_template(engine.from_string("")):
            with theirs_context.bind_template(engine.from_string("")):
                ours = _outcome(lambda: _native.resolve(path, ours_context))
                theirs = _outcome(
                    lambda: Variable(path)._resolve_lookup(theirs_context)
                )
        self.assertEqual(ours, theirs, f"value: {value!r}")

    def test_a_zero_arg_callable(self):
        def no_args():
            return "NO_ARGS"

        self.assert_calls_like_django(no_args)

    def test_a_callable_instance(self):
        class Callable:
            def __call__(self):
                return "INSTANCE_CALLED"

        self.assert_calls_like_django(Callable())

    def test_a_callable_requiring_an_argument(self):
        def needs_one_arg(a):
            return a

        # Both sides substitute the invalid value rather than raising.
        self.assert_calls_like_django(needs_one_arg)

    def test_a_body_type_error_propagates_on_both_sides(self):
        def body_raises():
            raise TypeError("boom from the body")

        # `_outcome` captures the exception *class and message* on both sides, so
        # this row separates "re-raised" from "substituted" without asserting
        # which — the oracle decides.
        self.assert_calls_like_django(body_raises)

    def test_a_silent_type_error_is_substituted(self):
        class Silent:
            def __call__(self):
                exc = TypeError("silent boom")
                exc.silent_variable_failure = True
                raise exc

        self.assert_calls_like_django(Silent())

    def test_a_class_body_flag_on_a_method_is_inert(self):
        """A flag written where the lookup cannot see it does not suppress the call.

        `getattr(bound_method, "do_not_call_in_templates")` reads through to
        `__func__` and not to the class, so Django **calls** this method.
        Asserted to call, so a later "fix" that consults the owning class
        suppresses a call Django makes and fails here loudly. The `alters_data`
        half of this rule is Task 2's, because it substitutes rather than calls.
        """

        class Inert:
            do_not_call_in_templates = True

            def name(self):
                return "ada"

        # The method must have run for both sides to agree, and the oracle makes
        # that visible without this test asserting the literal.
        self.assert_calls_like_django(Inert().name, path="x")

    def test_the_invalid_read_is_lazy(self):
        """A plain value, on a context with **no template bound**.

        Not `assert_calls_like_django`: that helper binds a template, and
        binding one is exactly what this row must not do — it is the only row
        in the milestone whose subject is the *absence* of a template. Django
        returns the value and never reaches `context.template.engine`; so must
        we.
        """
        from django.template import Context
        from django.template.base import Variable

        from django_rust_templates import _native

        context = Context({"x": "plain"})
        self.assertEqual(
            _outcome(lambda: _native.resolve("x", context)),
            _outcome(lambda: Variable("x")._resolve_lookup(context)),
        )

    def test_a_failed_invalid_read_propagates(self):
        """Where the substitution *is* reached and no template is bound.

        Django reads the attribute inside the outer `try` (`base.py:957`), so
        the `AttributeError` lands in the handler at `:1015`, which finds no
        `silent_variable_failure` and re-raises. A port that swallowed it and
        substituted `""` is quiet where Django is loud.
        """
        from django.template import Context
        from django.template.base import Variable

        from django_rust_templates import _native

        class NeedsOneArg:
            def __call__(self, a):
                return a

        context = Context({"x": NeedsOneArg()})
        self.assertEqual(
            _outcome(lambda: _native.resolve("x", context)),
            _outcome(lambda: Variable("x")._resolve_lookup(context)),
        )
```

- [ ] **Step 2: Run the test to verify it fails, and for the intended reason**

Run:
```bash
source scripts/env.sh && maturin develop --manifest-path crates/template-py/Cargo.toml \
  && .venv/bin/python -m unittest tests.test_resolve_callarm -v
```
Expected: **seven of the eight rows FAIL** — six on
`('raised', NotImplementedError, "calling a resolved callable is M8's")` against
Django's value, and `test_a_body_type_error_propagates_on_both_sides` on the
exception *type* (`NotImplementedError` against Django's `TypeError`). Measured
transcript of both sides in the fixture table above; put the count in the commit
message rather than paraphrasing it.

**The eighth row passes already, and that is the point of it.**
`test_the_invalid_read_is_lazy` is green today (`('value', 'plain')` on both
sides) because nothing has reached the read yet. It is a **regression guard
against this task**, which is where an eager fetch would be introduced — so
§11's "every guard is observed failing for its intended reason" cannot be
satisfied by its absence. Satisfy it by **mutation**: add an eager
`let _ = current.string_if_invalid();` at the top of `resolve_lookup`'s loop
(throwaway, not committed), confirm this row turns red with
`AttributeError: 'NoneType' object has no attribute 'engine'` while the other
seven are unaffected, and put that transcript in the commit message too.

**And run the guard's falsification**: comment out the `Call` arm's
`Unimplemented` return so the value falls through unchanged, re-run, and confirm
the rows now differ as `<function …>` against Django's called value — a
*different* reason. An arm that fails for the wrong reason is not yet a guard
(§11); both transcripts go in the commit message.

- [ ] **Step 3: Add `CallFailure` and the trait method**

In `crates/template-core/src/parser/resolve.rs`, beside `CallArm`:

```rust
/// Why a callable could not be called.
///
/// Three outcomes, because Django's arm has three (`base.py:1000-1013`) and
/// they are not two: `signature()` raising `ValueError` and `bind()` raising
/// `TypeError` both substitute `string_if_invalid`, while everything else is the
/// bare `raise` at `:1010`, which leaves this arm entirely and reaches the outer
/// handler at `:1016`. Measured: a zero-arg callable whose body raises
/// `TypeError` **propagates** — both of the first two checks succeed for it — so
/// folding `Raised` into `ArgsRequired` would swallow a real error.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallFailure {
    /// `inspect.signature` raised `ValueError` — Django's "No signature found".
    NoSignature,
    /// `signature(...).bind()` raised `TypeError` — arguments were required.
    ArgsRequired,
    /// Anything else. The host holds the exception by identity; it is Django's
    /// `raise` at `base.py:1010`, and the host decides between the outer
    /// handler's two branches, exactly as it does for [`LookupOutcome::Escaped`].
    Raised,
}
```

and on `LookupTarget`:

```rust
    /// Call the resolved callable and hand back the result as a new target.
    ///
    /// Django calls `current()` assuming no arguments (`base.py:1000`) and falls
    /// back to `signature()`/`bind()` to decide whether the `TypeError` meant
    /// "arguments were required" or "the body raised". The host owns all three
    /// because all three are Python (`inspect.signature` is Python's, and the
    /// exception must survive **by identity**).
    fn call(&self) -> Result<Box<dyn LookupTarget>, CallFailure>;

    /// `context.template.engine.string_if_invalid`, read **at the moment a
    /// substitution happens** — Django's three assignment sites (`base.py:996`,
    /// `:1002`, `:1009`) each re-read it, all inside `for bit in self.lookups:`
    /// and all inside the outer `try` at `:957`.
    ///
    /// Both halves of that are load-bearing, and both are measured
    /// (`m8_measure_invalid.py` §2):
    ///
    /// * **Lazy.** With no template bound the read raises
    ///   `AttributeError: 'NoneType' object has no attribute 'engine'`, while
    ///   `{{ x }}` with a plain value returns the value — the read is never
    ///   reached. A port that fetched the invalid value up front would raise on
    ///   the most ordinary template there is.
    /// * **Fallible, and the failure propagates.** The read is inside the outer
    ///   `try`, so its `AttributeError` reaches the handler at `:1015`, which
    ///   finds no `silent_variable_failure` and re-raises. Measured: an
    ///   `alters_data` callable on an unbound context raises `AttributeError`;
    ///   it does not substitute.
    ///
    /// So `Err` means *the host has stashed that exception*: the caller returns
    /// [`LookupOutcome::Escaped`] and the host's outer-handler rule decides,
    /// exactly as for an arm that raised.
    fn string_if_invalid(&self) -> Result<Box<dyn LookupTarget>, PendingFailure>;
```

and, beside `CallFailure`:

```rust
/// The host's read of `context.template.engine.string_if_invalid` raised, and
/// the host has stashed the exception.
///
/// A one-variant type rather than `()` — `Err(())` at a call site reads as
/// "nothing to do", while this means "something escaped and the host is holding
/// it", which is [`LookupOutcome::Escaped`]'s contract. Not folded into
/// [`CallFailure`] because it is not a call failure; not a `bool` because "no
/// invalid value is configured" is not a state Django has (its default is `""`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PendingFailure;
```

**`string_if_invalid` is not new work on the host side.** `template-py` already
has a free function that performs exactly this read (`lib.rs:1940`), written for
`LookupOutcome::Escaped`'s silent branch, and its doc comment already states the
two measured properties above — laziness and the two `AttributeError` shapes.
This task wires that existing read to the trait; it does not re-derive it.

- [ ] **Step 4: Wire the arm**

Replace the `Some(CallArm::Call) => return LookupOutcome::Unimplemented(…)` body
(`resolve.rs:243`) with the two-branch form. `NoSignature` and `ArgsRequired`
assign the invalid value and **stay in the loop** — the assignment is Task 2's
`string_if_invalid()`, and writing the loop-continuation here rather than there
is what Review Focus 1 is about:

```rust
            Some(CallArm::Call) => match current.call() {
                Ok(called) => called,
                // Django assigns `string_if_invalid` and the loop goes on to the
                // next bit with it (`base.py:996`, inside `for bit in …`).
                Err(CallFailure::NoSignature | CallFailure::ArgsRequired) => {
                    current = current.string_if_invalid()
                }
                // Not a substitution: `base.py:1010` re-raises into the outer
                // `except Exception` (`:1016`), whose two branches need the
                // exception object. The host owns that decision, as for `Escaped`.
                Err(CallFailure::Raised) => return LookupOutcome::Escaped,
            },
```

- [ ] **Step 5: Implement `PyTarget::call` in `template-py`**

```rust
    fn call(&self) -> Result<Box<dyn LookupTarget>, CallFailure> {
        Python::attach(|py| {
            let value = self.value.bind(py);
            match value.call0() {
                Ok(called) => Ok(self.target_for(called.unbind())),
                Err(err) if err.is_instance_of::<PyTypeError>(py) => {
                    // Django's fallback: signature() then bind() (`base.py:1002-1012`).
                    let inspect = py.import("inspect")?;
                    match inspect.getattr("signature").and_then(|f| f.call1((value,))) {
                        Err(e) if e.is_instance_of::<PyValueError>(py) => {
                            Err(CallFailure::NoSignature)
                        }
                        Err(_) => Err(CallFailure::Raised),
                        Ok(sig) => match sig.call_method0("bind") {
                            Err(e) if e.is_instance_of::<PyTypeError>(py) => {
                                Err(CallFailure::ArgsRequired)
                            }
                            Err(_) => Err(CallFailure::Raised),
                            // Neither check failed, so the `TypeError` came from
                            // the body: Django re-raises the **original** error,
                            // so the host must stash `err`, not the bind error.
                            Ok(_) => {
                                self.pending.set(Some(err));
                                Err(CallFailure::Raised)
                            }
                        },
                    }
                }
                Err(err) => {
                    self.pending.set(Some(err));
                    Err(CallFailure::Raised)
                }
            }
        })
    }
```

with `fn target_for(&self, value: Py<PyAny>) -> Box<dyn LookupTarget>` factored out
of the existing arena registration so `call` and `string_if_invalid` share it.

```rust
    fn string_if_invalid(&self) -> Result<Box<dyn LookupTarget>, PendingFailure> {
        Python::attach(|py| {
            // The read happens here, at the substitution site, and not in
            // `PyTarget::new` — Django re-reads the attribute at each of its
            // three assignments, and a plain value on an unbound context must
            // never reach it (measured; `m8_measure_invalid.py` §2).
            match string_if_invalid(self.context.bind(py)) {
                Ok(value) => Ok(self.target_for(value)),
                // `PendingFailure` means "the host holds the exception", so it
                // must actually be holding it; `outcome_to_py` reads `pending`.
                Err(error) => {
                    self.pending.set(Some(error));
                    Err(PendingFailure)
                }
            }
        })
    }
```

`self.context` is a **new field on `PyTarget`**, and the reason it is a field at
all is that the substitution is reached from wherever the loop is standing, not
from the root:

```rust
    /// The context this lookup runs against, shared with every target the loop
    /// produces. `string_if_invalid` is read from a target the loop has already
    /// walked into, so the root's copy is not enough.
    ///
    /// `Rc<Py<PyAny>>` rather than a cache filled on first use: Django re-reads
    /// the attribute at **every** substitution, and a cache would be an
    /// optimisation this port has to justify rather than a faithful default.
    /// Nothing measured needs one, and YAGNI is the rule that says so.
    context: Rc<Py<PyAny>>,
```

Thread it through the three places that build a `PyTarget`: `new` takes it as a
fourth `&Rc<Py<PyAny>>`, the hand-written `Clone` adds
`context: Rc::clone(&self.context)` (no token needed — it is not a `Py`
clone), `step`'s `Self::new(found, &self.arena, &self.pending)` gains
`&self.context`, and `resolve` constructs it beside `arena` and `pending` from
the `context` it already binds:

```rust
    let context = context.bind(py);
    let context_rc = Rc::new(context.clone().unbind());
    let root = PyTarget::new(context.clone().unbind(), &arena, &pending, &context_rc);
```

`target_for` is the fourth place, and it is a method rather than an inline
`Self::new` because `call` and `string_if_invalid` both produce a fresh value
mid-loop and sharing the four `Rc`s is the whole contract:

```rust
    /// A target over `value`, sharing this call's arena, `pending` and context.
    fn target_for(&self, value: Py<PyAny>) -> Box<dyn LookupTarget> {
        Box::new(Self::new(value, &self.arena, &self.pending, &self.context))
    }
```

**`self.pending.set(err)` before returning `Raised` is not optional**: the
`Escaped` contract (`resolve.rs:186-215`) says the host hands back *the most
recent* failure, and `outcome_to_py` reads it. The same holds for
`string_if_invalid`'s `Err` path above.

- [ ] **Step 6: Run the tests to verify they pass**

Run:
```bash
source scripts/env.sh && maturin develop --manifest-path crates/template-py/Cargo.toml \
  && cargo test -p template-core \
  && .venv/bin/python -m unittest tests.test_resolve_callarm -v \
  && cargo clippy --workspace --all-targets -- -D warnings
```
Expected: PASS, and M3's in-crate `CallArm` tests need two edits:

* `the_callable_arm_runs_after_every_bit_not_once_at_the_end` (`resolve.rs:680`,
  asserting `message.contains("M8")`) **must be rewritten**: the arm it pins no
  longer returns `Unimplemented`, and the row it was written for — the callable
  is bit 1's *result*, so the call happens mid-loop — is exactly what the fixed
  arm now does. Deleting an assertion is a finding: the replacement asserts the
  new behaviour (`Found` of the *called* value, then `b` resolved on it), and the
  commit message says which assertion died and why.
* `a_do_not_call_value_is_returned_rather_than_deferred` (`:705`) **must not
  change** — it is green today and green after. A diff there is a bug.
* `the_alters_data_substitution_is_m8s_and_says_so` (`:725`) is **Task 2's** and
  stays failing-to-compile-against nothing until then; if it is the only thing
  blocking this task's `cargo test`, change its *name and body* to the new
  behaviour and say so, rather than leaving a test asserting an M8 stub.

`Fake`, the in-crate double (`:504`), gains both new methods and two fields —
`call: Option<Result<Fake, CallFailure>>` and
`invalid: Option<Result<Fake, PendingFailure>>` — with the `Fake` value inside
standing for the value the call or the read produced. Both fields are exercised
by this task: the substitution branch is driven by a `Call` arm answering
`ArgsRequired`, which is one of Task 1's own rows.

- [ ] **Step 7: Commit**

```bash
git add crates/template-core/src/parser/resolve.rs crates/template-py/src/lib.rs \
        tests/test_resolve_callarm.py
git commit -m "M8 Task 1: call the resolved callable, with Django's signature fallback

call_arm decides the branch on the host side because only it can read the
two flags, and the port re-raises the call's TypeError into Django's outer
handler rather than reading it as 'arguments were required' — measured:
signature() and bind() both succeed for a zero-arg callable whose body
raises TypeError, so that reading gets the args case right and swallows a
real error on the other.

Co-Authored-By: Claude Code <noreply@anthropic.com>
```

---

### Task 2: The substitution — `CallArm::AltersData`, and the loop it must not end

**Files:**
- Modify: `crates/template-core/src/parser/resolve.rs` (the arm at `:253`; the in-crate test at `:725`)
- Test: `tests/test_resolve_invalid.py` (create)

**Interfaces:**
- Consumes: `LookupTarget::string_if_invalid` and `PendingFailure` (Task 1),
  `CallArm::AltersData` (`resolve.rs:93`), and M3's `call_arm`
  classification — which already routes the flag correctly
  (`lib.rs:1825`), so this task adds no flag logic at all. That is the whole
  reason the arm is one assignment rather than a branch: **the classification
  was measured at M3, the substitution was deferred, and only the substitution
  is missing.**
- Produces: nothing new outward. The arm stops being an early `return` and
  becomes an assignment, which is a *behaviour* later tasks depend on: Task 3's
  `{{ x|escape }}` row and Task 4's gate both stand on a loop that keeps going.

**Declared environment.** `cargo test -p template-core` covers the in-crate
double with no Python. `tests/test_resolve_invalid.py` runs under
`.venv/bin/python -I` with `.django/` on the path, after `maturin develop`.
Task 1 must be committed first: this file's middle row resolves `upper` on the
substituted string, which is Task 1's call arm. Nothing here goes through the
shim's interposed modules.

**The fixture table — the oracle column executed** (`m8_measure_invalid.py` §1,
§2, run 2026-10-09 against `.django/` @ 6.0.9). Every row is `x.alters_data =
True` on a callable instance, `string_if_invalid='<<invalid>>'`:

| path | `Variable._resolve_lookup` (the oracle) | what it decides |
|---|---|---|
| `x` | `('value', '<<invalid>>')` | the substitution happens at all |
| `x.upper` | `('value', '<<INVALID>>')` | **the loop continued and `.upper` ran on the invalid string** |
| `x.name` | `('raised', VariableDoesNotExist, "Failed lookup for key [name] in '<<invalid>>'")` | the loop continued *and* the container in the message is the substituted string |
| `x`, unbound context | `('raised', AttributeError, "'NoneType' object has no attribute 'engine'")` | the read's failure escapes; it is not substituted away |

The same trio through a **render** is `'&lt;&lt;invalid&gt;&gt;'`,
`'&lt;&lt;INVALID&gt;&gt;'`, `'&lt;&lt;invalid&gt;&gt;'` — note the third row
differs from the oracle, by design: `FilterExpression.resolve` substitutes
`string_if_invalid` for a missing key one frame out, which is the shell's
substitution and not this one. **That divergence is why this task compares
against `_resolve_lookup` and never against a render.** The fixture object's
`upper` must return something distinguishable — the measurement's returns
`"NOT THE STRING ONE"` — so the middle row cannot pass by calling it.

- [ ] **Step 1: Write the failing test**

```python
"""M8 Task 2: the `string_if_invalid` substitution, compared with Django's.

The middle row of `test_the_substitution_continues_the_loop` is the only one
that separates "substitute and continue" from "substitute and stop", and it is
the only row in this milestone that does. Compared against
`Variable._resolve_lookup`, not against a render: `FilterExpression.resolve`
substitutes a missing key one frame out, so a render row would hide the third
path behind a second, unrelated substitution.
"""

import unittest

from tests.test_render_parity import _ParityTest, _outcome


class InvalidSubstitutionParityTests(_ParityTest):
    def assert_substitutes_like_django(self, value, path="x", invalid="<<invalid>>"):
        from django.template import Context
        from django.template.base import Variable

        from django_rust_templates import _native

        engine = self.engine.__class__(string_if_invalid=invalid)
        ours_context = Context({"x": value})
        theirs_context = Context({"x": value})
        with ours_context.bind_template(engine.from_string("")):
            with theirs_context.bind_template(engine.from_string("")):
                ours = _outcome(lambda: _native.resolve(path, ours_context))
                theirs = _outcome(
                    lambda: Variable(path)._resolve_lookup(theirs_context)
                )
        self.assertEqual(ours, theirs, f"value: {value!r} path: {path!r}")

    def test_the_substitution_continues_the_loop(self):
        """`x.upper` is the discriminator; the other two rows guard it.

        Django assigns `string_if_invalid` **inside** `for bit in self.lookups:`
        (`base.py:996`), so `upper` is resolved on the substituted string and
        the string's own `upper` runs. A port that substituted and stopped
        renders the row above it — `'<<invalid>>'` — and every other input in
        this milestone agrees with Django either way.
        """

        class Alters:
            alters_data = True
            name = "ada"

            def __call__(self):
                return "SIDE EFFECT"

            def upper(self):
                # Distinguishable from `str.upper`, so the middle row cannot
                # pass by calling this.
                return "NOT THE STRING ONE"

        for path in ("x", "x.upper", "x.name"):
            self.assert_substitutes_like_django(Alters(), path=path)

    def test_the_flag_is_read_off_the_resolved_object(self):
        """The one `alters_data` rule, in the shape that makes both spellings fall out.

        Two fixtures that differ only in *where* the flag is written, and they
        are two different **resolved objects**: a bound method, which proxies
        attribute lookup to `__func__` and so sees only a function attribute,
        and a callable instance, where a class attribute is visible. A port that
        consults the owning class gets the first wrong — and gets it wrong in the
        dangerous direction, invoking a method Django suppresses.
        """

        class OnTheFunction:
            def save(self):
                return "SIDE EFFECT"

            save.alters_data = True

        class OnTheInstance:
            alters_data = True

            def __call__(self):
                return "CALLED"

        self.assert_substitutes_like_django(OnTheFunction().save)
        self.assert_substitutes_like_django(OnTheInstance())

    def test_a_failed_read_escapes_rather_than_substituting(self):
        """No template bound, and the substitution *is* reached.

        Not `assert_substitutes_like_django`: that helper binds a template,
        which is precisely the condition this row must not create.
        """
        from django.template import Context
        from django.template.base import Variable

        from django_rust_templates import _native

        class Alters:
            alters_data = True

            def __call__(self):
                return "SIDE EFFECT"

        context = Context({"x": Alters()})
        self.assertEqual(
            _outcome(lambda: _native.resolve("x", context)),
            _outcome(lambda: Variable("x")._resolve_lookup(context)),
        )
```

- [ ] **Step 2: Run the test to verify it fails, and for the intended reason**

Run:
```bash
source scripts/env.sh && maturin develop --manifest-path crates/template-py/Cargo.toml \
  && .venv/bin/python -m unittest tests.test_resolve_invalid -v
```
Expected: **all six rows FAIL** on
`('raised', NotImplementedError, "the alters_data substitution is M8's")`
against Django's value — every row here reaches the arm, including the last one,
which reaches it and *then* fails to read. Record the transcript.

**And observe the loop-continuation row failing for its own reason**, which is
the falsification Review Focus 1 owes. Temporarily make the arm substitute and
stop —

```rust
            Some(CallArm::AltersData) => match current.string_if_invalid() {
                Ok(invalid) => return LookupOutcome::Found(invalid.node()),
                Err(PendingFailure) => return LookupOutcome::Escaped,
            },
```

— re-run, and confirm `x` still passes while `x.upper` and `x.name` go red.
**That pair of results is the evidence that the middle row carries the
property**, and it goes in the commit message: a port can substitute correctly
and still be wrong here, and one row out of three says so.

- [ ] **Step 3: Replace the arm**

The stub at `resolve.rs:253`:

```rust
            // Also M8's, and it is not a call: Django substitutes
            // `context.template.engine.string_if_invalid`, which needs the
            // `Context` this resolver is deliberately not given. Measured:
            // `{{ o.run }}` with `o.run.alters_data = True` renders `''`, and
            // with `string_if_invalid = "<<invalid>>"` renders that instead.
            Some(CallArm::AltersData) => {
                return LookupOutcome::Unimplemented(
                    "the alters_data substitution is M8's".to_string(),
                )
            }
```

becomes an assignment — **no `return`, and the absence is the behaviour**:

```rust
            // Not a call and not a raise: Django replaces the value
            // (`base.py:996`). The assignment is what makes the next bit
            // resolve against the substituted value — measured, `{{ x.upper }}`
            // with `x.alters_data = True` renders the *string's* `upper`,
            // `'<<INVALID>>'`, not the object's.
            Some(CallArm::AltersData) => match current.string_if_invalid() {
                Ok(invalid) => current = invalid,
                // The read itself raised, inside Django's outer `try`
                // (`base.py:957`), so its `AttributeError` reaches the handler at
                // `:1015`. The host has stashed it and applies that handler.
                Err(PendingFailure) => return LookupOutcome::Escaped,
            },
```

The comment the stub carried — *"needs the `Context` this resolver is
deliberately not given"* — stops being true in this task and must not be left
behind. The context now reaches the arm through the host, as a
`LookupTarget` method, which is §5.2's rule (Python behind a trait) rather than
an exception to it. **Say that in the replacement comment rather than deleting
the sentence.**

- [ ] **Step 4: Rewrite the in-crate test the stub left**

`the_alters_data_substitution_is_m8s_and_says_so` (`resolve.rs:725`) asserts the
message contains `"M8"`; both the name and the assertion are now wrong. Two
tests replace it, and the first is the in-crate half of Review Focus 1 — it can
only show that the *substitution took effect*, because `Fake` has no Python and
so no `str.upper`; the loop actually continuing is the Python row's job:

```rust
    #[test]
    fn the_alters_data_substitution_replaces_the_value() {
        let alters = Fake {
            call_arm: Some(CallArm::AltersData),
            invalid: Some(Ok(Fake::scalar(4, "<<invalid>>"))),
            ..Fake::scalar(3, "<bound method>")
        };
        let holder = Fake {
            attrs: vec![("x", alters)],
            ..Fake::scalar(99, "<Obj object>")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["x"]), Box::new(holder)),
            LookupOutcome::Found(NodeRef(4)),
        );
    }

    #[test]
    fn a_failed_invalid_read_escapes_instead_of_substituting() {
        // The read is inside Django's outer `try` (`base.py:957`), so its
        // failure is the handler's problem at `:1015` — the same variant as an
        // arm that raised, and not a substitution.
        let alters = Fake {
            call_arm: Some(CallArm::AltersData),
            invalid: Some(Err(PendingFailure)),
            ..Fake::scalar(3, "<bound method>")
        };
        let holder = Fake {
            attrs: vec![("x", alters)],
            ..Fake::scalar(99, "<Obj object>")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["x"]), Box::new(holder)),
            LookupOutcome::Escaped,
        );
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
source scripts/env.sh && maturin develop --manifest-path crates/template-py/Cargo.toml \
  && cargo test -p template-core \
  && .venv/bin/python -m unittest tests.test_resolve_invalid -v \
  && cargo clippy --workspace --all-targets -- -D warnings
```
Expected: PASS. Then the milestone's own gate, unchanged so far:
`bash scripts/check.sh` → `all checks passed`, `Ran 1547 tests … OK
(skipped=6)`, `unshimmed()` still 25. M8 adds no shim coverage, so a change in
any of those three numbers is a finding, not progress.

- [ ] **Step 6: Commit**

```bash
git add crates/template-core/src/parser/resolve.rs tests/test_resolve_invalid.py
git commit -m "M8 Task 2: substitute string_if_invalid without ending the bit loop

The assignment is inside 'for bit in self.lookups:' (base.py:996), so the
next bit resolves against the invalid value; measured discriminator is
{{ x.upper }} with alters_data set, which renders the INVALID string
uppercased. The read is lazy and its failure propagates: an unbound
context returns a plain value while the attribute read raises
AttributeError, and that failure is caught by the outer handler rather
than turned into a substitution.

Co-Authored-By: Claude Code <noreply@anthropic.com>
```

---

### Task 3: The `Promise` contract and the escaping protocol — a gate with no production change

**Files:**
- Create: `tests/test_escape_protocol.py`
- Modify: **nothing.**

**Interfaces:**
- Consumes: `_native.resolve`, `_native.filter`; and
  `scripts/filter_probe.py`'s `django_answer`, `rust_answer`, `describe`,
  `NO_ARG`, `ensure_settings` — loaded by path, the idiom
  `tests/test_filter_parity.py` already uses.
- Produces: **no production artifact.** It produces a *contract* that Tasks 4
  and 5 cite: our resolver hands back the object it was given, by identity, and
  the escaping protocol at `_native.filter` is Django's.

**There is no production change in this task, and that is its finding.** §5.2
lists a seam method

```rust
fn force_promise(&self, value: &Value) -> Value;      // lazy / Promise
```

and the recon carried it forward as obligation 3 ("`Promise` forcing"). It is
wrong twice over. The name has **zero occurrences in the tree**, and — measured
(`m8_measure_promise.py`) — **nothing needs one**.

**The claim this rests on is that no forcing site is on the *resolve* path, and
that is the only form of it the gate asserts.** Directly checkable, and it cannot
be wrong by omission. What follows is *evidence* for it, not an enumeration to
build on: `render_value_in_context` (`base.py:1123-1136`) forces, via the
conversion test `issubclass(type(value), str)` — a `Promise` is not a `str`, so it
is stringified there, one line before `conditional_escape` sees it — and
`conditional_escape` (`html.py:115-126`) forces too, with its own
`isinstance(text, Promise)` branch, reached by `|escape`. Both are on the render
path, which is Django's until M9. **A third forcing site may exist** — in
`formats.localize`, in a filter, or anywhere the resolve path does not touch — and
this task is unaffected if it does, because a site off the resolve path cannot
make our resolver force. An earlier draft of this paragraph said Django forces "in
exactly two places", which is an exhaustiveness claim; this project's record on
those is its worst of any class (26 versus 27 tags, 14 versus 15 lazy helpers,
`_(", ")`'s reach at two rather than three), and each one was load-bearing.
The enumeration stays in the recon as evidence and is not depended on here.

Our resolver returns the proxy **unforced**, which is what `Variable.resolve`
(`base.py:924-940`) does. So the obligation is discharged by *not adding* a
forcing site — and that is precisely the kind of claim that has to be pinned,
because the next person to touch `PyTarget` can add one in a line. Hence a gate
with no implementation: **the deliverable is the contract, and the gate is what
keeps it.**

**This also corrects Review Focus 6's producer list.** M8 adds two producers of
a value that crosses into Rust's arena — a called callable's return (Task 1) and
the substituted invalid value (Task 2). It does not add a third, because it
does not force. Recon §4 and §Q2 both name "a forced `Promise`" as one of the
two; the correction is recorded in Review Focus 6 rather than here.

**What the gate pins, and why identity rather than equality.** Measured on the
pinned tree, for both probe proxies:

```text
gettext_lazy       str()==proxy: True   str() is proxy: False  type=str
lazy->SafeString   str()==proxy: True   str() is proxy: False  type=SafeString
```

`==` is **true** on a forced copy of both, so an equality row would pass on
exactly the resolver this task exists to refuse. `is` is the only assertion that
separates them. This gate has three parts: the resolver's identity contract
(the two probes, through a real `Context` and through a plain mapping); a
`Promise`'s non-callability, which is what makes Django's callable arm
(`base.py:993`) not fire at all; and the `SafeData`/`__html__` protocol through
`_native.filter`, compared against `FilterExpression.resolve` — never
`defaultfilters.<name>` (trap T1: six names differ, and `escape` is one).

- [ ] **Step 1: Write the gate**

```python
"""The `Promise` and `SafeData` protocols at the resolver and filter seams.

**A gate with no implementation beside it.** §5.2 lists `force_promise` as a
seam method and nothing in the tree needs one: **no forcing site is on the
resolve path**, which is the claim this gate asserts and the only one it needs.
(`render_value_in_context` and `conditional_escape` do force, and both are on the
render path M9 interposes — but the gate does not depend on that being all of
them.) So this milestone's obligation is discharged by **not** adding a forcing
site — a claim the next edit to `PyTarget` can falsify in one line, which is why
it is pinned rather than asserted in prose.

Every row is a *direct-entry* row. The render path today is Django's, so a
`{{ x }}` renders identically whether our resolver forces or not — measured, all
eight render rows in `.review/m8-measurements/m8_measure_promise.py` §3 are
`same`. A render-based gate would be green and blind; only the entry points can
see the difference.

Both sides run against the pinned tree. Django's filter side is
`FilterExpression.resolve` (`base.py:785-818`, including the `is_safe` /
`needs_autoescape` post-processing the registry function alone does not do);
ours is `_native.filter`. The probe is loaded by path so the instrument behind
M4's gate is the instrument here — two comparisons would drift, and the drift
would be invisible in the worst way.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import Context  # noqa: E402
from django.template.base import Variable  # noqa: E402
from django.utils.functional import Promise, lazy  # noqa: E402
from django.utils.safestring import SafeData, mark_safe  # noqa: E402
from django.utils.translation import gettext_lazy  # noqa: E402

from django_rust_templates import _native  # noqa: E402

_PROBE_SPEC = importlib.util.spec_from_file_location(
    "django_rust_templates.filter_probe",
    PROJECT_ROOT / "scripts" / "filter_probe.py",
)
probe = importlib.util.module_from_spec(_PROBE_SPEC)
sys.modules[_PROBE_SPEC.name] = probe
_PROBE_SPEC.loader.exec_module(probe)


class HasHtml:
    """A `__html__` object that is *not* a `str`.

    The naive expectation is that `|escape` honours `__html__`. It does not:
    `escape_filter` is `@stringfilter` (`defaultfilters.py:457-459`), so the
    decorator's `str()` runs before `conditional_escape` ever sees the object and
    `__html__` is never consulted. Keeping the default `repr` is deliberate — it
    is what makes the row show the protocol was *skipped* rather than reached.
    """

    def __html__(self):
        return "<b>already safe</b>"


class SafeHtml(str):
    """A `str` subclass carrying `__html__` — the shape that *does* reach it."""

    def __html__(self):
        return "<b>already safe</b>"


class ThePromiseIsHandedBackUnforced(unittest.TestCase):
    """Review Focus 4, at the entry point, because no render row can see it."""

    # `lazy(f, str)` returns a **wrapper function**, not a proxy — the obvious
    # spelling hands every row a plain function, which is callable and not a
    # `Promise`. That is trap T2's shape (a fixture inert in the arm under test)
    # and it was measured before it was believed.
    PROXIES = (
        ("gettext_lazy", lambda: gettext_lazy("lazy & value")),
        ("lazy -> SafeString", lambda: lazy(lambda s: mark_safe(s), str)("<b>")),
    )

    def test_the_resolver_returns_the_object_it_was_given(self):
        # Identity, not equality: `str(proxy)` for a `str`-resultclass proxy is
        # `==` the proxy (measured above), so an equality row passes on a
        # resolver that forced every promise — the defect this row exists for.
        for label, make in self.PROXIES:
            value = make()
            context = Context({"x": value})
            with self.subTest(label):
                self.assertIs(_native.resolve("x", context), value)
                self.assertIs(Variable("x").resolve(context), value)
                self.assertIsInstance(_native.resolve("x", {"x": value}), Promise)

    def test_a_resolved_promise_is_still_not_callable(self):
        # Django's callable arm is entered on `callable(current)` and nothing
        # else (`base.py:993`), so an unforced promise takes no arm at all. This
        # row is the *other* half of the contract: it fails if a forcing site is
        # added, and it also fails if `PyTarget::call_arm` drops its
        # `is_callable` guard — `__proxy__` defines no `__call__`, so the
        # failure arrives as a `TypeError` out of our resolver rather than as a
        # wrong string. Two tasks, one row, opposite mistakes.
        for label, make in self.PROXIES:
            value = make()
            with self.subTest(label):
                self.assertFalse(callable(value))
                self.assertIs(_native.resolve("x", Context({"x": value})), value)


class TheEscapingProtocolThroughTheFilterSeam(unittest.TestCase):
    """`SafeData`/`__html__` at `_native.filter`, against Django's own pipeline."""

    FILTERS = ("escape", "force_escape", "safe", "escapejs")

    @classmethod
    def setUpClass(cls):
        # The loop's `use_tz` question needs settings: `Context.use_tz` is `None`
        # and `effective_use_tz` falls back to `settings.USE_TZ`. Called here
        # rather than at module scope because `settings.configure` changes what
        # every module imported afterwards sees.
        probe.ensure_settings()

    def values(self):
        yield "plain str", "a & b"
        yield "SafeString", mark_safe("a & b")
        yield "__html__ object", HasHtml()
        yield "SafeHtml (str + __html__)", SafeHtml("a & b")
        yield "Promise (gettext_lazy)", gettext_lazy("a & b")

    def test_the_two_sides_agree_on_type_value_and_safety(self):
        # `describe` is `(type name, repr)` plus `isinstance(..., SafeData)`,
        # because for 36 of the 57 filters a correct value with the wrong safety
        # is still a wrong answer — the classes that matter here are `str` and
        # `SafeString`, which hold the same text.
        for name in self.FILTERS:
            for label, value in self.values():
                stock, _, context = probe.django_answer(name, value, probe.NO_ARG, {})
                ours = probe.rust_answer(name, value, probe.NO_ARG, context)
                with self.subTest(filter=name, value=label):
                    self.assertEqual(probe.describe(stock), probe.describe(ours))

    def test_a_stringfilter_skips_html_rather_than_honouring_it(self):
        # The row above would pass even if BOTH sides honoured `__html__`, so the
        # behaviour itself is pinned here: the object is escaped by its `repr`,
        # i.e. `__html__` was never consulted.
        #
        # **One object, both sides.** The repr carries the id, and the first
        # draft built `HasHtml()` twice — so it compared `0x1073d0830` against
        # `0x1017e1810` and failed on a correct pair. The failure was the
        # assertion's, not the code's.
        obj = HasHtml()
        stock, _, context = probe.django_answer("escape", obj, probe.NO_ARG, {})
        ours = probe.rust_answer("escape", obj, probe.NO_ARG, context)
        self.assertIn("&lt;", str(stock))
        self.assertIn("object at", str(stock))
        self.assertEqual(str(stock), str(ours))

    def test_the_safe_str_subclass_is_where_html_is_reachable(self):
        # `conditional_escape`'s `__html__` branch (`html.py:121-122`) is reached
        # through a *str* subclass only: `@stringfilter`'s `str()` preserves the
        # subclass, so the protocol survives here where it was skipped above.
        for name in self.FILTERS:
            value = SafeHtml("a & b")
            stock, _, context = probe.django_answer(name, value, probe.NO_ARG, {})
            ours = probe.rust_answer(name, value, probe.NO_ARG, context)
            with self.subTest(filter=name):
                self.assertEqual(probe.describe(stock), probe.describe(ours))
                self.assertIsInstance(stock, SafeData)
```

- [ ] **Step 2: Run it, and expect it to pass on the first run**

Run:
```bash
source scripts/env.sh && .venv/bin/python -m unittest tests.test_escape_protocol -v
```
Expected: `Ran 5 tests … OK`. **This is the milestone's only task whose first
run is green**, and §11's rule ("every guard is observed failing for its
intended reason before it is kept") is met by Step 3's mutations rather than by
a red first run. The distinction is real, not a waiver: the contract test asserts
a property that already holds, so there is no implementation to write and no
moment at which the row is legitimately red — the red is *manufactured*, which
is what a mutation is for.

- [ ] **Step 3: Observe each part of the contract failing for its intended reason**

Two throwaway edits, each reverted immediately. Neither is committed; both are
run, because a prediction about what an edit produces is what M2 lost three plan
steps to.

**(a) Add a forcing site.** In `crates/template-py/src/lib.rs`, `outcome_to_py`
(`:1971`), force every value handed back:

```rust
        LookupOutcome::Found(node) => value_behind(node).and_then(|v| Ok(v.bind(py).str()?.unbind())),
```

Then `maturin develop …` and re-run. Expected: `test_the_resolver_returns_the_object_it_was_given`
and `test_a_resolved_promise_is_still_not_callable` both **FAIL** on
`assertIs`, while the filter rows stay green — the forcing happens at the
resolver, not in the filter path, which is exactly the separation this task's
three parts exist to have. **Revert with `git diff` + a targeted `Edit`**, never
`git checkout`: the work is uncommitted and `checkout` would take Task 1 and 2's
changes with it.

**(b) Drop the `is_callable` guard.** In `PyTarget::call_arm` (`:1828`), delete

```rust
            if !value.is_callable() {
                return None;
            }
```

and re-run. Expected: `test_a_resolved_promise_is_still_not_callable` **FAILS**
with a `TypeError` naming `'__proxy__' object is not callable` — not with a
wrong string. That the failure *shape* is an exception rather than a mismatch is
the fact the row's docstring claims, and this is where it is checked rather than
asserted. Revert as above.

- [ ] **Step 4: Run the milestone's gate**

Run:
```bash
source scripts/env.sh && bash scripts/check.sh
```
Expected: `all checks passed`, `Ran 1547 tests … OK (skipped=6)`, `unshimmed()`
still 25. M8 adds no shim coverage, so any change in those numbers is a finding.

**This task's citations are already in the manifest**, because the gate is a
build gate and the plan would otherwise commit a tree that fails it: the new
names (`parser/resolve.rs:107`, `parser/host.rs:22`, `filters/host.rs:195`,
`filters/mod.rs:118`, `resolve.rs:28`, `resolve.rs:170`,
`template-py/src/lib.rs:117`, `desc.rs:49`, `desc.rs:181`, `html.py:115-126`,
`tests/template_tests/test_nodelist.py:44-86`) were seeded and the gate run
during plan authoring:

```bash
source scripts/env.sh && .venv/bin/python -I scripts/check_python_anchors.py --check --frozen
```

Expected: `640 cited of 640 manifest entr(ies), 0 failure(s)`. **Task 3 does not
re-run it** — it is listed here because this is the step where a reader would
ask. The seeding found one thing worth recording: `Variable.resolve` is
**`base.py:924-940`, not `918-940`**, a number this plan had copied from the M8
recon into a claim about the resolver's unforced contract. `918` is inside
`Variable.__init__`'s validation loop; the method starts at `924`. Corrected in
the spec and here; **left uncorrected in `.review/m8-measurements/m8_measure_promise.py`
on purpose**, because that directory is excluded from the scan as a dated record
and rewriting it would make a 2026-10-09 record say something it did not say.

**Step 1's file is new and `tests/` *is* scanned**, so the four citations inside
it — `defaultfilters.py:457-459`, `html.py:121-122`, `base.py:993`,
`base.py:785-818` — need seeding the moment the file exists, in this step:

```bash
source scripts/env.sh && .venv/bin/python -I scripts/check_python_anchors.py --seed \
  && .venv/bin/python -I scripts/check_python_anchors.py --check --frozen
```

**Check the quotes the seed guessed** (`--seed` takes the nearest backticked
phrase, which for a docstring is often a symbol the line does not contain, and
`--check` then fails on it): each row's quote must be text actually present at
the anchor. `--triage` lists the ones that are not, and it is a list to read,
not to obey — it is 200 characters wide on purpose and mostly noise.

- [ ] **Step 5: Commit**

```bash
git add tests/test_escape_protocol.py
git commit -m "M8 Task 3: pin the unforced-Promise and SafeData contracts at the entry points

Both sides of every row run on the pinned tree: the resolver's identity
contract and a Promise's non-callability through _native.resolve, and the
escaping protocol through _native.filter against FilterExpression.resolve.
No production change — the finding is that M8 has no forcing site to build,
which is what makes the gate the deliverable.

Co-Authored-By: Claude Code <noreply@anthropic.com>
```


---

### Task 4: The non-parity rows — what M8 protects that no output can show

**Files:**
- Create: `tests/test_m8_side_effects.py`
- Modify: **nothing.** No production change.

**Interfaces:**
- Consumes: `_native.resolve` (Tasks 1–2's arms), and `Variable._resolve_lookup`
  as the oracle.
- Produces: the milestone's non-parity evidence, and the transcript that says
  M8's rows are not vacuous.

**Why this module exists, and why it is not another parity test.** Review Focus 5
carries the reviewer's caution as a measurement: **the property `alters_data`
protects is "the method was not called", and that is not a property of the
output.** Measured (`m8_measure_invalid.py` §3): with `string_if_invalid=''` and
an `alters_data` callable returning `''`, a port that *calls* it produces the
identical row — `('value', '')`, `calls delta = 0` for the correct one and the
same `''` for the broken one. Every parity instrument in this milestone is blind
to that class of defect, so at least one row must observe the call.

**The direct-entry corpus is not re-created here, on purpose.** `tests/test_render_parity.py`
already owns that harness — `assert_resolves_like_django`, running our
`_native.resolve` against Django's `Variable._resolve_lookup` on the pinned tree
— and Tasks 1–3 own their per-branch rows. A second corpus would be the drift
`tests/test_filter_parity.py`'s docstring warns about, in the one direction where
drift is invisible: the duplicate agrees with our side for a reason the gate does
not share.

**`do_not_call_in_templates` returns the object, not `""`.** Django's branch is
`pass` (`base.py:993-994`) — the callable *is* the value the loop ended on, and
it becomes a string one layer later in `render_value_in_context`. The first draft
of this module asserted `== ""` for that row and **failed against correct stock
Django**; the row asserts identity instead. That is why the two `DoNotCall` rows
read the way they do.

- [ ] **Step 1: Write the module**

```python
"""M8's non-parity rows: the property is that the method was *not called*.

Everything else in this milestone compares outputs, and this class of defect has
no output to compare. Measured with `string_if_invalid=''` on an `alters_data`
callable that returns `''`: a port that calls it renders exactly what a port that
suppresses it renders — `('value', '')`. So a counter observes the call, and the
counter needs a **positive control**, or the whole module passes on a counter
that is simply never incremented.

There is deliberately no parity corpus here. `tests/test_render_parity.py` owns
that harness (`assert_resolves_like_django` over `_native.resolve` against
`Variable._resolve_lookup`), and a second copy would agree with our side for a
reason this gate does not share.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import Context, Engine  # noqa: E402
from django.template.base import Variable  # noqa: E402

from django_rust_templates import _native  # noqa: E402


class Suppressed:
    """A callable whose only observable effect is that it ran.

    `alters_data = True`, and `__call__` returns `""` — the same value
    `string_if_invalid=''` substitutes. The output is `""` either way.
    """

    alters_data = True
    calls = 0

    def __call__(self):
        type(self).calls += 1
        return ""

    def reset(self):
        type(self).calls = 0


class DoNotCall:
    """`do_not_call_in_templates` — and note the arm is `pass`: the *object* is
    the value, not `""`."""

    do_not_call_in_templates = True
    calls = 0

    def __call__(self):
        type(self).calls += 1
        return ""

    def reset(self):
        type(self).calls = 0


class Control:
    """No flags, so Django *does* call it. The counter must see this one."""

    calls = 0

    def __call__(self):
        type(self).calls += 1
        return ""

    def reset(self):
        type(self).calls = 0


def _bound(value, invalid=""):
    """A context bound to an engine — both sides need the binding, because the
    `alters_data` arm reads `context.template.engine.string_if_invalid`."""
    engine = Engine(string_if_invalid=invalid)
    ctx = Context({"x": value})
    return ctx, ctx.bind_template(engine.from_string(""))


class ThePropertyNoOutputCanShow(unittest.TestCase):
    def _run(self, value, call):
        ctx, binding = _bound(value)
        with binding:
            return call(ctx)

    def test_the_positive_control_the_counter_can_see_a_call(self):
        # Without this row every assertion below also holds for a counter that is
        # never incremented — a guard that cannot fail. Stock Django calls an
        # unflagged callable, so `calls` must reach 1.
        value = Control()
        value.reset()
        self._run(value, lambda c: Variable("x")._resolve_lookup(c))
        self.assertEqual(value.calls, 1, "stock Django did not call the control")

    def test_alters_data_suppresses_the_call_stock(self):
        value = Suppressed()
        value.reset()
        got = self._run(value, lambda c: Variable("x")._resolve_lookup(c))
        self.assertEqual(got, "")
        self.assertEqual(value.calls, 0)

    def test_do_not_call_suppresses_the_call_stock(self):
        # **Not `""` — the object.** Django's branch is `pass` (`base.py:993-994`),
        # so the callable is left as the value, and it only becomes a string one
        # layer later in `render_value_in_context`. The first draft of this module
        # asserted `== ""` and failed against correct stock Django.
        value = DoNotCall()
        value.reset()
        got = self._run(value, lambda c: Variable("x")._resolve_lookup(c))
        self.assertIs(got, value)
        self.assertEqual(value.calls, 0)

    def test_alters_data_suppresses_the_call_ours(self):
        value = Suppressed()
        value.reset()
        got = self._run(value, lambda c: _native.resolve("x", c))
        self.assertEqual(got, "")
        self.assertEqual(value.calls, 0)

    def test_do_not_call_suppresses_the_call_ours(self):
        value = DoNotCall()
        value.reset()
        got = self._run(value, lambda c: _native.resolve("x", c))
        self.assertIs(got, value)
        self.assertEqual(value.calls, 0)

    def test_the_control_is_called_ours(self):
        value = Control()
        value.reset()
        got = self._run(value, lambda c: _native.resolve("x", c))
        self.assertEqual(got, "")
        self.assertEqual(value.calls, 1)
```

- [ ] **Step 2: Run it and check the two halves separate**

Run:
```bash
source scripts/env.sh && .venv/bin/python -m unittest tests.test_m8_side_effects -v
```

**Measured on the tree as it stands while this plan was written — both arms still
M8's stubs — this is `Ran 6 tests … FAILED (errors=2)`, and the two errors are
the two stub messages:**

```text
ERROR: test_alters_data_suppresses_the_call_ours
  NotImplementedError: the alters_data substitution is M8's
ERROR: test_the_control_is_called_ours
  NotImplementedError: calling a resolved callable is M8's
```

The other four pass. That split is the point and it is why this task needs no
manufactured red of its own: **the four stock rows and the one already-shipped
`DoNotCall` row pass, and exactly the two rows that depend on M8's arms fail,
each naming the arm it needs.** Task 4 executes after Tasks 1 and 2, so the run
in *this* step is the green one (`Ran 6 tests … OK`); the transcript above is the
observation that the rows are not vacuous, and it belongs here because it was
made before the arms existed.

- [ ] **Step 3: Observe the non-parity property itself, by mutation**

Green rows do not show that a row is *non-parity*, only that it passes. One
throwaway edit, reverted immediately: in `crates/template-core/src/parser/resolve.rs`,
route the `AltersData` arm to the call branch — i.e. replace

```rust
        Some(CallArm::AltersData) => match current.string_if_invalid() {
```

with

```rust
        Some(CallArm::AltersData) => match current.call() {
```

(or the equivalent edit at the arm's own site if Task 2 shapes it differently).
Then `maturin develop …` and re-run. Expected: **`test_alters_data_suppresses_the_call_ours`
FAILS on `value.calls == 0` while its value assertion `got == ""` still passes** —
the callable ran and returned `""`, so the row is separated by the counter alone.
That is the whole claim of this task, and it is the one thing no other M8 row can
show. Revert with a targeted `Edit`, never `git checkout`: Tasks 1 and 2's work is
uncommitted and would go with it.

- [ ] **Step 4: Run the milestone's gate**

Run:
```bash
source scripts/env.sh && bash scripts/check.sh
```
Expected: `all checks passed`, `Ran 1547 tests … OK (skipped=6)`, `unshimmed()`
still 25.

- [ ] **Step 5: Commit**

```bash
git add tests/test_m8_side_effects.py
git commit -m "M8 Task 4: observe the call, not the output

alters_data protects a property no parity row can see: with
string_if_invalid='' and a callable returning '', a port that calls it
renders exactly what a port that suppresses it renders. The counter is the
only instrument, so the module carries a positive control — without one
every assertion also holds for a counter that is never incremented.

Co-Authored-By: Claude Code <noreply@anthropic.com>
```

---

### Task 5: Close-out — the M9 handoff, the record, and the anchors

**Files:**
- Create: `.review/m8-seams.md`
- Verify: `scripts/anchors.txt`, `bash scripts/check.sh`

**Interfaces:**
- Consumes: every task above.
- Produces: the milestone record, and the explicit list of what M8 does **not**
  deliver — which is the only thing M9 can be handed.

**Why the handoff is a deliverable and not a paragraph.** This milestone's
characteristic failure is a name or an obligation written down as if it were
done. §5.2 named four seams that do not exist (Task 3 and §D1); the recon named a
producer that does not exist (Review Focus 6); this plan's own Goal named a
forcing site that does not exist. Every one was found by *measuring*, and none by
reading. So the close-out states what is owed, in one place, with the milestone
that owes it — because an obligation that is only implied is an obligation the
next session re-derives from scratch or, worse, assumes.

- [ ] **Step 1: Write the milestone record**

`.review/m8-seams.md`, in the shape `.review/m7-seams.md` established: what was
built, what was measured, what was **decided**, and what was falsified. It must
carry, at minimum:

- the two arms, and the substitution's loop-continuation pair (Task 2) as the
  thing that a substitute-and-stop port gets wrong;
- **the falsified claims**, each with who found it: this plan's Goal's "force a
  `Promise`" (Task 3), Review Focus 4's "opposite directions" (measurement),
  Review Focus 6's third producer (Task 3), §5.2's four non-existent seam names
  (§D1), and `Variable.resolve`'s span (§D3) — five, and the count is the point;
- the **gate's shape** and why the pinned suite cannot be it (Task 4's
  transcript: 4 pass, 2 errors naming the two stubs).

- [ ] **Step 2: Write the M9 handoff into the record**

Three items, each with its owner, and **none of them M8's to build**:

| owed | who | why it is not M8's |
|---|---|---|
| `annotate_exception` — `Node.render_annotated`'s `_culprit_node`/`template_debug` protocol (`base.py:1044-1070`) | M9 | goes live only when `setup_test_environment` routes the suite through `NodeList.render`; nothing supplies it today (spec §5.2, *Open obligations*) |
| `render_value_in_context`'s sequence — `template_localtime` → `localize` → the autoescape branch (`base.py:1123-1136`), with `issubclass(type(value), str)` as the *conversion* test | M9 | it is on the render path, which is Django's until M9 interposes `base`. Recon obligation 5, and **no M8 task delivers it** — this row is where that is said out loud |
| the forced-vs-unforced `Promise` distinction | M9 | latent today: Task 3 measured that consumers *do* distinguish (57-filter sweep) but that no render row can see it, because the render path is Django's |

- [ ] **Step 3: Run the milestone's gate, for real**

Run:
```bash
source scripts/env.sh && bash scripts/check.sh
```
Expected: `all checks passed`, `Ran 1547 tests … OK (skipped=6)`, `unshimmed()`
still **25**, `cargo test -p template-core` green, clippy clean. **M8 adds no
shim coverage**, so any change in those numbers is a finding, not progress.

Then the anchor gate, which is the last thing that can be wrong about this
milestone's own prose:
```bash
source scripts/env.sh && .venv/bin/python -I scripts/check_python_anchors.py --check --frozen
```
Expected: `0 failure(s)`. The spec, the plan and Task 3's new test file all add
citations; Task 3 seeds its own file's four, so this step is a verification rather
than a fix-up.

- [ ] **Step 4: Commit**

```bash
git add .review/m8-seams.md scripts/anchors.txt \
        docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md
git commit -m "M8 close-out: the record, the spec correction, and the M9 handoff

The handoff is a deliverable: annotate_exception, render_value_in_context's
sequence, and the forced-vs-unforced Promise distinction are all owed at M9
and none is M8's to build. An obligation that is only implied is one the
next session re-derives from scratch or assumes.

Co-Authored-By: Claude Code <noreply@anthropic.com>

git add docs/superpowers/plans/2026-10-09-m8-escaping-and-the-object-protocol.md
git commit -m "M8: the implementation plan

Co-Authored-By: Claude Code <noreply@anthropic.com>
```

The plan is committed **separately and last**, so that the spec correction and the
record do not depend on the plan's own hash, and so a reader can see the plan as
the artifact it is.

---

## §D — Corrections, and where they were made

The three §D sections this plan's header refers to. They are corrections to
**other** documents, recorded here because a correction that lives only in its
target is invisible to the next reader of *this* file.

### §D1 — §5.2 was rewritten, in the spec, during this milestone's authoring

**Applied**, not merely proposed. §5.2's trait names were design-time proposals
and three of them describe code that does not exist; the section now says so, and
a reader who takes a name out of it without checking is reading an intention as
an implementation. The rewrite:

- **retires four names** (`ValueResolver::resolve_bit`, `ValueResolver::force_promise`,
  `ExtensionHost::call_filter`, `ExtensionHost::render_foreign_node`) into prose, in
  the past tense, **with the reason** — and removes them from the Rust block,
  because a name inside a code block propagates regardless of the annotation
  beside it (the M6 citation sweep's lesson: a copied block propagates like a
  copied citation);
- **separates the two buckets**, so `render_foreign_node` (retired, replacement in
  service) and `annotate_exception` (open, owed at M9) do not read alike. A single
  bucket makes a reader stop at the second;
- **names the real traits** with their paths — `LookupTarget`
  (`parser/resolve.rs:107`), `Host` (`parser/host.rs:22`), `FilterHost`
  (`filters/host.rs:195`) — and keeps the reasoning in the traits' own doc
  comments rather than copying it back into the spec;
- **adds the header clause**: the section is design-time, its tables say which
  entries are descriptions and which were proposals, and each renamed symbol
  carries its actual name.

### §D2 — the recon, corrected

Two items, each falsified by measurement rather than by argument:

- **§3's `SafeStr` "live defect"** — already corrected in place by the recon
  itself, same day, before the plan; nothing further.
- **§4/§Q2's "a forced `Promise`" as one of M8's two new producers** — **wrong**:
  M8 forces nothing (Task 3), so the producers are the called callable's return
  and the substituted invalid value. Recorded in Review Focus 6.
- **`Variable.resolve` at `base.py:918-940`** — **wrong number**, found by the
  anchor gate while seeding this plan's citations: the method starts at `924`, and
  `918` is inside `Variable.__init__`'s validation loop. Corrected in the spec and
  in this plan; deliberately left in `.review/m8-measurements/m8_measure_promise.py`,
  which is a dated record excluded from the scan.

### §D3 — anchors

`scripts/anchors.txt` was seeded during authoring (the gate is a build gate, and
a plan that commits a tree failing it is worse than a plan with a stale number).
`--check --frozen` is `640 cited of 640, 0 failures`. The seeding is also what
found §D2's wrong span — the instrument earned its place a second time.

**Task 1 execution added a reading heuristic, not a tool.** Four of that task's
cited line numbers were wrong, and two are the same sub-shape — cite the block
*header* and mean its *body*: `base.py:996` is the `elif`, and the assignment
is at `:997`; `base.py:957` is `current = context`, and the `try` is at `:958`.
That is now at least five instances across M5–M8 (`filesizeformat`'s `:905→906`,
`floatformat`'s `:160→161` and `:162→163`). The guard cannot catch it, and the
reason is worth stating: **a quoted single-line manifest row with this defect is
already red**, so the shape only survives where the prose does not quote — which
is why `--frozen`'s `name-only` and unquoted entries are exactly where to look.
Also measured in Task 1: the gate reaches a *plan* citation only through the
code the plan produced, since `docs/superpowers/plans/` is excluded by rule.
All four moved rows were caught, because Task 1's edits moved the code they
pointed at (`parser/resolve.rs:107→139`, `resolve.rs:170→236`,
`template-py/src/lib.rs:117→119`).

### §D4 — a rule this milestone adds to the no-op family

The project already carries one: **a mutation is a no-op when the test inputs do
not reach the state the two spellings disagree about** (`.unwrap_or(false)`; the
`IncludeNode` copy). Task 3 found the sibling at authoring time rather than after
shipping, which is the first time:

> **An assertion is a no-op when the comparison it uses cannot distinguish the
> two states.**

The canonical case is `__eq__` on a proxy. Measured: `str(proxy) == proxy` is
**True** for both of Task 3's probes, because `str()` on a proxy whose
`resultclass` is `str` returns the cast result, which compares equal to the proxy.
So `assertEqual(_native.resolve("x", ctx), value)` passes on exactly the resolver
the task exists to refuse — a row that is green in both the correct and the
broken state, and green for a reason no reader would suspect. `assertIs` is the
fix, and the reason the row is written with identity is not stylistic.

This is worth carrying beyond M8 because the failure is invisible in the usual
way: the assertion *runs*, it *can* fail in principle, and it fails on inputs
nobody will ever pass it. The check that catches it is the one Task 3 ran —
**ask what the two states look like to the comparison**, not whether the
comparison is a real assertion.

**Task 1 executed found a third cause, and it is the one that will not look
wrong in the code.** The plan's Step 2 named a mutation for the lazy-read
guard — an eager `let _ = current.string_if_invalid();` at the top of the loop
— and predicted that `test_the_invalid_read_is_lazy` would turn red. Run, it
did not: all eight rows stayed green. The reason is not the test and not the
guard. `PyTarget`'s error channel **stashes** rather than raises (the `pending`
cell, §5.2's by-identity rule), so a mutation whose only effect is to *cause*
an error is absorbed before anything observes it. The mutation that reproduces
is one that **acts on** the failure:

> **A mutation is a no-op when its effect is absorbed before anything observes
> it.** The first two causes are defects in a test; this one is a consequence
> of correct production code.

That distinction is what makes it dangerous. It applies to *every* row that
reads through `PyTarget` — all of M8's, and everything M9 adds once the
resolver is on the render path — and the stash is right, so nothing about it
will ever look wrong. The operative rule for the rest of this milestone: **a
mutation against the resolver must change what happens *to* a failure, not
whether one occurs.** And a green result under mutation now has two readings —
"the guard is vacuous" and "the mutation never landed" — so before concluding
the first, check the second.
