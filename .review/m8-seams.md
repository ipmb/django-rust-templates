# M8 — the object protocol, and the gate with no implementation

Forcing event: **the resolver was not complete, and one of the two arms it was
missing is not a call.** §10.3's M8 row claims "`SafeData`, `Promise`, callables,
`alters_data`, `do_not_call_in_templates`; resolver complete" — and of the two
stubs M3 left, `CallArm::AltersData` **substitutes a value** rather than
invoking one, so a port that implements it as a call is wrong in the one place
the flag exists to protect. Closed: both arms are wired, and the substitution is
an assignment *inside* `for bit in self.lookups:` (`base.py:997`), which is the
part a substitute-and-stop port gets wrong.

The headline finding is the other half. **M8's third obligation did not exist.**
§5.2 named `force_promise` and the recon carried it forward as "`Promise`
forcing"; measured, nothing needs one — no forcing site is on the resolve path —
so the deliverable is a *contract* (hand the promise back unforced) and the
artifact is a gate with no production change behind it.

Every number below comes from `.review/m8-measurements/`, re-run at close-out;
nothing is restated from the section that measured it earlier. Derived against
`.django/` @ 6.0.9.

## The rule

M5's: **a verdict here is dated.** It is a number only for as long as the thing
it numbered holds still. This is a one-time audit, not a gate, and each row names
a **symbol** rather than a line wherever a symbol will do.

M7's, and the one that decides this milestone: **the vacuity rule** (spec
§10.3). A gate naming a `.django/tests/` module is a gate only for the milestone
that *interposes* the behaviour it exercises. **M8 is the first milestone where
the rule's unresolved second half is the whole story**: escaping and the object
protocol are exactly where both engines call `conditional_escape`, `SafeData` and
`Promise`, so a parity corpus is blind to a defect both sides share. §10.3 says
M8's row "reads out of scope here and owes that second question an answer of its
own" — the answer is `tests/test_m8_side_effects.py`, and it is a counter.

## The seam inventory

The trait did not move; two methods were added to it, and no new trait was
introduced. `LookupTarget` (`parser/resolve.rs`) went from nine methods to
**eleven**: seven predicates Django evaluates, the callable arm's three outcomes
(`call_arm`, `call`, `string_if_invalid`), and `node`.

What M8 wired, per Django's path. Five paths reach a substitution or the
handler; two are M8's, and the other three already shipped.

| Django | our variant | whose |
|---|---|---|
| `if callable(current):` (`base.py:993`) | `PyTarget::call_arm` folds `callable` and both flags | M3 |
| `do_not_call_in_templates` → `pass` (`:994-995`) | `CallArm::DoNotCall`, which is *no arm* | M3 |
| `alters_data` → assign `string_if_invalid` (`:997`) | `CallArm::AltersData` → `string_if_invalid()`, **assign, not return** | **M8** |
| `current()` (`:1000`) | `CallArm::Call` → `PyTarget::call`, `value.call0()` | **M8** |
| `signature()` raises `ValueError` (`:1004`) | `CallFailure::NoSignature` | **M8** |
| `bind()` raises `TypeError` (`:1009`) | `CallFailure::ArgsRequired` | **M8** |
| bare `raise` (`:1013`) | `CallFailure::Raised`, the **original** exception, stashed | **M8** |
| outer handler, `silent_variable_failure` (`:1023`) | `outcome_to_py`'s `Escaped` arm reads the stash | M3, extended |

**Five ways to reach `string_if_invalid`, and `base.py:1004` is the one a port
drops.**
Django's fallback asks two questions — does `signature()` raise `ValueError`
("No signature found") and does `bind()` raise `TypeError` ("Arguments *were*
required") — and the comment on the second is the one a reader remembers, so
`bind()` reads as the whole test. A port that dropped the first is right on every
fixture that *has* a signature, which is all of them except a C-level callable
without `__text_signature__`. Pinned by
`test_a_callable_with_no_signature_is_substituted`.

## What was decided

**The substitution's loop-continuation shape is the arm's whole behaviour.** The
Python is `current = context.template.engine.string_if_invalid` — an assignment
— so the *next* bit resolves against the invalid value. Our arm therefore has no
`return`, and the absence is the behaviour. The discriminating pair, measured
(`m8_measure_alters.py`, `string_if_invalid='<<invalid>>'`):

```text
  {{ x }}          -> '&lt;&lt;invalid&gt;&gt;'
  {{ x.upper }}    -> '&lt;&lt;INVALID&gt;&gt;'      <- .upper ran on the INVALID string
  {{ x.name }}     -> '&lt;&lt;invalid&gt;&gt;'      <- raises in _resolve_lookup; the render substitutes one frame out
```

Mutation-confirmed: routing the arm to the call branch (`Ok(called) => current =
called`) reddens `x.upper` and `x.name` and leaves `x` green —
`FAILED (failures=2)`. A port can substitute correctly and still be wrong here,
and one row in three says so.

**The context reaches the arm through the host, not through the core.** The stub
said the substitution "needs the `Context` this resolver is deliberately not
given", which stops being true in M8: `LookupTarget::string_if_invalid` returns
the substituted target, and the host reads `context.template.engine` itself.
That is §5.2's rule (Python behind a trait), not an exception to it.

**Errors are stashed, not raised.** `PyTarget` carries
`pending: Rc<Cell<Option<PyErr>>>`; an arm that fails to read sets it and the
caller returns `LookupOutcome::Escaped`, which `outcome_to_py` converts with
Django's own handler. The reason is that `LookupOutcome` derives `PartialEq` and
is compared in `template-core`'s tests, where no Python exists — so the exception
cannot travel in the enum. The consequence is worth knowing and is recorded as a
corrections rule: **a mutation is a no-op when its effect is absorbed before
anything observes it** — a mutation whose only effect is to *cause* a failure is
invisible, because the stash absorbs it.

**Nothing forces a `Promise`.** With no forcing site on the resolve path, the
contract is discharged by *not adding* one. Measured, both proxies: `str(proxy)
== proxy` is `True` and `str(proxy) is proxy` is `False`, so the gate asserts
**identity**; an equality row would pass on exactly the resolver it exists to
refuse, and the mutation's own failure message says so —
`AssertionError: 'lazy & value' is not 'lazy & value'`.

## What was falsified

Five claims, each by measurement rather than by argument, and the count is the
point — this project's worst class of error is an exhaustiveness claim.

1. **This plan's Goal, "force a `Promise` where Django forces it."** Falsified by
   Task 3 / `m8_measure_promise.py`: no forcing site is on the resolve path, so
   there is nothing to build. The phrase was struck from the Goal.
2. **Review Focus 4's "forcing changes `{{ x }}` and `{{ x|escape }}` in opposite
   directions."** Falsified by measurement: all **eight** render rows are `same`
   either way, and `str()` on a proxy whose cast result was `mark_safe`'d returns
   the `SafeString` **unchanged**, so safeness survives. The real signal is a
   57-filter sweep, where consumers *do* distinguish (`phone2numeric` escapes the
   unforced value and not the forced one; `pluralize` takes Django's `TypeError`
   branch for the proxy and its `ValueError` branch for the forced string) — a
   different claim, and a weaker one.
3. **Review Focus 6's third producer, "a forced `Promise`".** Falsified by Task 3
   for the same reason: M8 forces nothing, so it adds **two** producers (a called
   callable's return, the substituted invalid value), not three.
4. **§5.2's four non-existent seam names** — `ValueResolver::resolve_bit`,
   `ValueResolver::force_promise`, `ExtensionHost::call_filter`,
   `ExtensionHost::render_foreign_node`. Measured by recon §5 and corrected by
   §D1: the binding is not the trait, and §5.2's names are Rust names. One of the
   four (`force_promise`) turned out to be work that does not exist; the other
   three were already replaced.
5. **`Variable.resolve` at `base.py:918-940`.** Wrong by six lines — `918` is
   inside `Variable.__init__`'s validation loop and the method starts at `924`.
   Found by the anchor gate while seeding this milestone's citations (§D2/§D3);
   deliberately **not** corrected in `.review/m8-measurements/m8_measure_promise.py`,
   which is a dated record excluded from the scan.

## The gate's shape, and why the pinned suite cannot be it

Measured (`m8_measure_corpus.py`): the ten `.django` modules that name M8's
surface run **88 tests, 0 failures** while `_native.resolve` raised on every
callable path. The corpus is green and *blind* — the render path is Django's own
`_resolve_lookup` until M9 — so the gate had to be a direct-entry module:
`_native.resolve` against `Variable._resolve_lookup`, and `_native.filter`
against `FilterExpression.resolve`.

**And a direct-entry parity corpus fixes only half of it.** The reviewer's
caution is the load-bearing half, and it is measured: **the property `alters_data`
protects is "the method was not called", and that is not a property of the
output.** With `string_if_invalid=''` and an `alters_data` callable returning
`''`, a port that calls it produces the identical row — `('value', '')`,
`calls delta = 0` either way. So one row must observe the call, and
`tests/test_m8_side_effects.py` is that row: a counter, plus a **positive
control** without which every assertion also holds for a counter that is never
incremented.

The module's Step 2 transcript is the evidence that its rows are not vacuous.
It was taken during plan authoring — **against the tree while both arms were
still M3's stubs**, which is why it is a stronger statement than a green run and
why the plan kept it rather than the later one:

```text
Ran 6 tests ... FAILED (errors=2)
ERROR: test_alters_data_suppresses_the_call_ours
  NotImplementedError: the alters_data substitution is M8's
ERROR: test_the_control_is_called_ours
  NotImplementedError: calling a resolved callable is M8's
```

Four pass, and exactly the two that depend on M8's arms fail, each naming the arm
it needs. Task 3's gate is the only gate in the milestone whose first run is
**green**; there is no implementation to write, so the red was manufactured by
mutation instead, and both mutations were run and reverted.

## What M8 changes about the seam, in one line

It adds **methods, not a trait**: `LookupTarget` gains `call` and
`string_if_invalid`, `PyTarget` gains the stash and the bound context, and
nothing new is interposed — `unshimmed()` is **25**, unchanged since M6, and any
movement there is a defect rather than a milestone result.

## The M9 handoff

Three items, each owed at M9 and **none of them M8's to build**. An obligation
that is only implied is one the next session re-derives from scratch or assumes.

| owed | who | why it is not M8's |
|---|---|---|
| `annotate_exception` — `Node.render_annotated`'s `_culprit_node`/`template_debug` protocol (`base.py:1044-1068`) | M9 | goes live only when `setup_test_environment` routes the suite through `NodeList.render`; nothing supplies it today (spec §5.2, *Open obligations*). It is stateful across nested renders and keyed on the exception's **identity**, so it cannot be reconstructed after the fact |
| `render_value_in_context`'s sequence — `template_localtime` → `localize` → the autoescape branch (`base.py:1123-1136`), with `issubclass(type(value), str)` as the *conversion* test | M9 | it is on the render path, which is Django's until M9 interposes `base`. Recon obligation 5, and **no M8 task delivered it** — this row is where that is said out loud |
| the forced-vs-unforced `Promise` distinction | M9 | latent today: Task 3 measured that consumers **do** distinguish (the 57-filter sweep) but that no render row can see it, because the render path is Django's. The identity gate at `_native.resolve` is what holds the contract until then |

**The plan's own handoff table cited `base.py:1044-1070` for `render_annotated`,
and that span overshoots the method by two lines** — `1069` is blank and `1070`
is `def get_nodes_by_type`. Corrected in the spec's *Open obligations* and here,
to `1044-1068`; the plan is a dated record and keeps its number. The same
close-out corrected the spec's `conditional_escape` span from `html.py:115-126`
to `115-127` — the function's last line is `return escape(text)` — which brought
the spec into line with the four places our own code already cited `115-127`.

## The re-run, at close-out

**2026-10-09, against `.django/` @ 6.0.9.** All eight scripts in
`.review/m8-measurements/`, each as
`.venv/bin/python -I .review/m8-measurements/<name>.py`. Every one ran; the
figures this document rests on, quoted as printed:

```text
m8_measure_corpus.py     modules: 10   tests: 88 run, 0 failures, 0 errors
                         the Call arm is not reached through a render (both engines agree)
m8_measure_alters.py     invalid='<<invalid>>'  {{ x.upper }} -> '&lt;&lt;INVALID&gt;&gt;'
                         invalid=''              {{ x.upper }} -> ''
m8_measure_invalid.py    lookup {{ x.upper }} -> ('value', '<<INVALID>>')
                         lookup {{ x.name }}  -> ('raised', VariableDoesNotExist,
                                                  "Failed lookup for key [name] in '<<invalid>>'")
                         unbound: Variable('x')._resolve_lookup -> ('value', 'plain')  (the read is lazy)
                         unbound + alters_data -> ('raised', AttributeError, no attribute 'engine')
m8_measure_callarm.py    no-arg -> 'NO_ARGS'; needs one arg -> the invalid value
                         body raises TypeError -> RAISED TypeError: boom from the body
m8_measure_promise.py    all 8 render rows: same
                         sweep: gettext_lazy 57 filters (21 skipped), 3 differ
                                lazy->SafeString 57 filters (21 skipped), 8 differ
m8_measure_escape.py     register['escape'] is df.escape_filter: True / is html_escape: False
m8_measure_filter_names.py  registered filters: 57; attribute is a DIFFERENT object: 6
m8_measure_baseline.py   _native exposes 26 public names; §5.2's six: resolve True, the rest False
```

**One baseline row changed since the recon printed it, and that is the re-run
earning its place.** Recon §1's table shows `bound method -> NotImplementedError:
calling a resolved callable is M8's`; it now prints `bound method -> str 'ada'`,
and `obj.alters_data=class-attr` no longer raises either. The recon's figure was
correct on the day and is a dated record; the change is Tasks 1 and 2 landing,
which is the one thing a restated-from-memory figure could not have shown.

**And the `type=` columns are trustworthy only because they print the object and
not its `str()`.** Recon §1 records the first draft of the same script losing the
distinction exactly that way: `print(str(got))` forces the promise *in the
printer*, so a script that meant to measure "is this forced" answered its own
question. Every `type=` figure above reads off the returned object, which is why
`__proxy__` is a result rather than a formatting artefact — and why
`_native.SafeStr`'s `__mro__` is `(SafeStr, object)`, not a `SafeData` subclass,
which is recon §3's "opaque by design" and not a defect.

## The close-out gate

`bash scripts/check.sh` → `all checks passed`, `Ran 1547 tests … OK
(skipped=6)`, `25 module(s) still served by Django`, clippy clean,
`cargo test -p template-core` **682 passed**. The anchor gate:
`1134 anchor occurrence(s) checked, 651 cited of 651 manifest entr(ies), 0
failure(s)`. M8 adds no shim coverage, so none of the three suite numbers may
move — and none did.

**The anchor manifest grew 640 → 651 rows across this milestone, and moved five
of them** — eleven net new rows is one metric and five moves is another, and they
are not the same eleven. The moves: three in Task 1 (`parser/resolve.rs:107→139`,
`resolve.rs:170→236`, `template-py/src/lib.rs:117→119`) and two at close-out
(`parser/resolve.rs:139→147`, `resolve.rs:236→244`).

**The last two moves are the interesting ones, and they are why the gate earns
its place.** They were caused by correcting the `LookupTarget` doc comment's
stale "nine methods, one per predicate Django actually evaluates" — the count was
true before M8 and false after Task 1 added two methods, and nobody updated it
when they added them. The corrected comment adds nine lines above the trait, so
`pub trait LookupTarget {` moved 139 → 147 and `pub enum LookupOutcome {` moved
236 → 244, and the gate failed on both by comparing the recorded *snippet*
against the cited line rather than the number alone. **A comment fix is a
citation move**, and a number-only guard would have stayed green.
