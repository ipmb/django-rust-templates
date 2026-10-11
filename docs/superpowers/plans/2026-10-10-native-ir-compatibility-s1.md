# S1: the native materialiser — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the compatibility tree by walking the descriptor IR in Rust, replacing the Python decode walk (`parser.materialise` → `materialise_node` → `_decode_value`) one descriptor shape at a time, behind a switch, with a parity harness that proves each step identical to the walk it replaces.

**Architecture:** A new Rust module `crates/template-py/src/materialise.rs` walks `NodeListDesc`/`NodeDesc` children-first, reproducing `materialise_node`'s order *exactly* — including where it reads `NODE_CLASSES` relative to building children — builds each node's `kwargs` itself, and calls `NODE_CLASSES[class_name](**kwargs)`. Every descriptor shape it does not yet handle falls through to the *existing* Python decoder for that one shape. The Python path stays as the switch's other branch and as the parity oracle.

**Tech Stack:** Rust (pyo3 0.29, `crates/template-py`, `crates/template-core`), Python 3 (`django_rust_templates.engine.parser`), `unittest`, maturin.

**Spec:** `docs/superpowers/plans/2026-10-10-native-ir-compatibility-nodes.md`

## Round 1 review revisions

The reviewer read this plan and returned **revise before implementation**, with six
findings. All six were checked against source and all six were correct. Two of them
found factual errors in the code written here:

1. **The registry is read *before* the children.** `materialise_node` does
   `cls = NODE_CLASSES[class_name]` at `parser.py:347` and builds the children at
   `:372`. The first draft of `materialise_one` read the registry *after* its
   children, so a factory that rebinds `NODE_CLASSES["IfNode"]` while a child is
   built would be observed by the parent in one walk and not the other. Order
   restored, with a test (Task 4 Step 1) that rebinds mid-build.
2. **`smartif.Operator` is not a module attribute.** The operator classes are
   defined *inside* the `infix`/`prefix` factories (`smartif.py:44-93`), so every
   one of them is named `Operator` and none is reachable by name. The fingerprint
   now identifies an operator by looking its class up in `smartif.OPERATORS` —
   without which `a == b` and `a != b` fingerprint identically.
3. **The `_decode_value` three-tuple is a two-tuple.** `FilterExpression.filters`
   holds `(filter_func, args)` (`base.py:814`), and `args` holds `(is_var, operand)`
   pairs (`base.py:804`, `:806`). The first draft unpacked three names.
4. **The wire is built once, not twice.** `:5073` builds it and `:5080` re-wraps
   *that tuple* with the same table — idempotent, not a second build. Task 3's
   prose corrected.
5. **`unwrap_or(false)` is too broad.** It swallows a real extraction failure as
   well as a missing attribute. Narrowed to `AttributeError`, with a test that a
   present-but-not-a-bool constant is loud.
6. **The retained-IR claim was false.** S1 retains *nothing*: `desc_tree` is a
   local of `compile_template` and drops on both paths. Task 8 measures the
   compile saving and first-render latency only; retained memory is S2's question,
   and `tracemalloc` cannot see Rust allocations in any case.

## Round 2 review revisions

Round 2 returned **revise again**: "F1's ordering is fixed; the census still
rejects valid native execution, malformed tests miss dispatch, and the profiler
measures nested calls incorrectly." All of it is correct. Three of these were
errors of mine that the first round did not reach, and two of the three were
errors in *tests I had written to catch errors*.

> **Where these were superseded.** Round 3 replaced the census (item 4), the
> profiler's `plan` residual (item 2) and the nested-timing row (item 1) with
> something that can fail. `test_the_walk_is_timed_once_per_compile_not_once_per_subtree`
> is now `test_the_recorder_sees_one_sample_per_compile`;
> `test_the_attribute_fallback_is_gone_from_the_source` and the allow-list census
> are gone entirely, replaced by the `Bridge` enum and
> `the_bridge_list_is_pinned`; the `plan` identity is gone with the residual. The
> items below are kept as the record of what was wrong, not as a description of the
> file.

1. **The profiler timed every subtree.** `materialise` recurses: `materialise_node`
   resolves each child through the same patched global, so a wrapper appending on
   every call recorded every subtree of `large_template()` — hundreds of entries —
   and `median` reported the cost of a *typical subtree* as the cost of the walk.
   A plausible number answering the wrong question. Depth is now tracked and only
   the outermost call is recorded, pinned by
   `test_the_walk_is_timed_once_per_compile_not_once_per_subtree`, which asserts
   one entry for one compile of a three-deep template.
2. **The phases are independent medians, so `plan` can be negative.** The
   `test_every_phase_is_non_negative` row is deleted: it asserts a property of the
   arithmetic, not of the engine, and fails on a loaded machine. The identity
   `plan == (python - stubbed) - materialise` is what is actually claimed and it
   holds by construction.
3. **The shipping number comes from `bench.py`, uninstrumented.** This tool patches
   two module globals; the bench patches nothing. Task 1 and Task 8 both take the
   bench's figures as the claim and this tool's as the explanation.
4. **The census rejected valid native execution.** `HostNode` decodes its own
   `NodeRef` through `_decode_value` (`parser.py:333`), and `_materialise_variable`
   decodes every `Variable.literal` (`:416`) — three retained helpers this plan
   keeps on purpose. So `assertEqual(attrs, set())` fails on correct code, and
   allowing `NodeRef` globally would have concealed an unintended native reference
   fallback. The attribute count is gone entirely; the attribute fallback is now
   proven **structurally, by deletion** (`fallback_attr` removed in Task 7, with
   `test_the_attribute_fallback_is_gone_from_the_source` reading the Rust module),
   plus an allow-list census of the five coarse bridges that remains.
5. **`HostNodeIdentityTests` compared class names.** Two distinct
   `SimpleBlockNode`s share a class name, so the row passed for a walk that built a
   fresh object — it proved nothing about the one property it was named for. It now
   captures the object `SimpleBlockNode.__init__` produced, by subclassing the
   module global `compile_func` looks up (`library.py:229`), and asserts `assertIs`.
6. **The malformed Rust rows drove the helpers, not the arms.** `resolve_handle` is
   two lines below `AttrValue::NodeSlot(… => resolve_handle(…, true`, and calling
   the helper proves the branch but not the dispatch — the two arguments that
   differ between `Ref` and `NodeSlot` are the entire content of the arm.
   `mod malformed` now calls `attr_to_py` and `materialise_one`, with a control row
   per claim (a `NodeRef` over the same empty table is *not* loud; an in-range
   `NodeListRef` *is* the nodelist).
7. **The unknown-parent ordering test needed no injection after all.** Making the
   *child* an unknown class too turns the error message into the instrument: a walk
   that built children first would name `NoSuchChild`.
8. **The registry fixture did not run.** `TextNode.__init__(self, s)` takes an
   argument, so `real()` was a `TypeError`; `Marker` has to subclass the real
   `IfNode` *class*; and `real_if` came from a `setUp` the class does not have.
9. **The harness loaded `parser.py` twice.** `importlib.util.spec_from_file_location`
   plus a `sys.modules` install produced a second module object, so `with_native`
   flipped a global nothing read and every parity row compared the Python walk with
   itself. It is a plain import now. Same class of error as the stock-`Engine` mix-up
   that once made a probe report 7345 µs.
10. **`token_desc_to_py` does not exist.** The token producer is `token_to_py`
    (`lib.rs:753`), and it already exists, so nothing is extracted and no anchor
    moves.
11. **`NodeRef` wraps a `u64`, not a `usize`** (`desc.rs:49`). `resolve_handle` takes
    a `u64` and the `NodeSlot` arm casts, because a `usize` parameter would truncate
    a `NodeRef` on a 32-bit target.

## Round 3 review revisions

Verdict: **not ready as written — the S1 boundary is sound; the blockers are defects
in its tests and profiler.** Accepted in full. The recurring shape of these is worth
naming once: five separate rows in this plan could not fail, and every one of them was
written to catch a failure. That is now the thing this plan is *about*.

**F1. The source-read proof was both too weak and self-contradicting.** It asserted two
substrings were absent from `materialise.rs`, which a renamed fallback passes and a
harmless comment fails — and Task 7's own explanatory comment contains the string
`_decode_value`, so the row rejected the implementation the task asks for. Worse, the
runtime census beside it wrapped only the five names in `_BRIDGES` and then asserted
`called <= _BRIDGES`, which is true by construction and blind to a sixth bridge by
definition. **Both are replaced by a typed bridge enumeration**, so that a call into
Python cannot be written without naming a variant, and the count of variants is a row
rather than a substring.

**F2. The census list was also incomplete.** It omitted `mark_safe`
(`AttrValue::SafeStr`, reached by `{% verbatim %}`), `_unknown_node_class`, and the
two Task 5 message helpers — so even had the subset assertion had teeth, it named the
wrong set. The enumeration is derived from the Rust call sites, not from the list.

**F3. `_python_pass()` lost its branch selection.** It patched `materialise` but not
`USE_NATIVE_MATERIALISER`, so after Task 8 flips the default neither patch executes,
`outermost` stays empty, and the tool raises *"the walk was never called"* — a tool
that works in Task 1 and dies in Task 8. Both patches now run inside
`_on_path(False, …)`, and `decompose` reports `samples` so a test can see the recorder
rather than re-implementing it.

**F4. The derived `plan` residual is dropped.** It mixes two instrumented terms with
one clean one, the stub allocates a real `NodeList`, and the identity test cannot
validate any of that attribution. The tool reports the three timings it actually took.
The spec's §1 plan figure is reconciled from a direct measurement instead (§ Task 1
Step 5).

**F5. The nested-timing row tested a copy of the wrapper.** It defined its own `depth`
and `recording` and never ran `decompose`'s, so it would have stayed green through the
exact defect it was written for. It reads `decompose`'s own `samples` now.

**F6. Task 3's missing-constant row deleted the constant and then called `with_native`,
which reads it.** `with_native` does a bare `materialiser.USE_NATIVE_MATERIALISER`, so
the control tree raised `AttributeError` instead of comparing. The control is built
before the deletion.

**F7. The ordering fixture's child descriptor was malformed.** `("NoSuchParent", (), (("nodelist", ("hello",)),), None)`
has `("hello",)` where a nodelist descriptor goes — a `(nodes, contains_nontext)`
pair whose `nodes` is a tuple of *node* descriptors. A reordered walk would fail
unpacking `"hello"` and never reach the recording factory, so `built == []` would hold
for the code the row exists to reject.

**F8. `_callable_identity` still equated same-qualname closures.** Pinned by a
rebinding sandwich that asserts `assertIs` on both captured filters, not by a name.

**F9. `tree_module`'s replacements.** `py.import` yields `Bound<PyModule>`, so the
`sys.path` insertion moves **out** of the `guarded` block — previously-configured
settings do not establish the import path, and an earlier binary run would leave the
module unreachable. `Bound::into_any()` fixes the declared return type.

**F10. Task 8's first-render and retained-memory items named quantities with no
procedure.** Both are dropped, each replaced by the structural reason it would have
measured, and the step now takes **one** number.

## Code review revisions — Task 1

The three plan rounds are the *plan* item's budget and are spent. This is the first
round on the **code**, against `scripts/compile_profile.py` and
`tests/test_compile_profile.py` as written above. Verdict: **revise Task 1 before
committing** — two of the five findings were defects in the measurement itself, and
one of those had already produced a number that was pure machine. All accepted.

The shape is worth naming once, because it is the *third* time in this plan:
**the instrument was reporting something. It was just not the thing its label
said.** Round 3 caught five tests that could not fail; this round caught a
measurement that could not be *right*.

**C1. The stub's label was wrong in two directions.** The plan claimed `stubbed`
is "the Python path with `materialise` patched out" and that `plan_list` declines on
the length mismatch. It does decline — for a root *with nodes*. For an empty source
the lengths match and an empty plan is built; `contains_nontext` controls neither
decision; and because `{% partialdef %}` materialises during the parse, the stub
also changes those bodies and their parse-time state. The column is now labelled for
what it is — the whole compile with the walk stubbed, including the stub's own
allocation, both planner checks, wire cleanup and `Template` construction — and the
claim that the *plan's* cost can be read out of it is withdrawn rather than
softened. Step 5 already said the plan cannot be isolated; this stops the docstring
from contradicting it.

**C2. `samples` counted outermost calls, not compiles — the defect the round-3 fix
left behind.** Round 3's fix was to time only the outermost `materialise` rather than
every subtree, and it worked. But `{% partialdef p %}{{ a }}{% endpartialdef %}
{% partial p %}` calls `materialise` **during the parse** as well, so one compile
makes two non-overlapping outermost calls: the reviewer measured four samples for
`repeats=2`, and the median over those mixes a three-character partial body with a
full root and answers neither question. The recorder now attributes each outermost
call to the compile it belongs to, reports the **sum per compile**, and the median
is over the sums. Both counts are printed, so a template that makes more than one
call per compile says so. Pinned by `test_the_recorder_groups_the_calls_of_one_compile`,
which asserts `samples` is twice `compiles` — so the fixture is required to keep
discriminating, not merely to pass. (That row was written against `{% partialdef %}`
first. Round 3 is why it no longer is.)

**C3. Both paths were timed in one order, and the order was the measurement.** The
first version ran all the native repeats and then all the python ones. Before Task 3
those are byte-identical code, and the reviewer measured `native 1849.6 µs` against
`python 1742.3 µs` — **a saving of −107.3 µs out of nothing**, because whichever
block ran second inherited the load the first one caused. Each branch now gets one
discarded warm call and the two are alternated round by round; the same three rows
now read −3.2 / +0.2 / +12.0 µs, which is the noise floor rather than a result on a
tool that has nothing to measure yet.

**C4. The row that watched for that was a timing comparison.** `python > stubbed`
would have been **green** on the −107.3 µs run. It is replaced by a spy on
`_on_path` that records which branch each body actually observed, and the row now
asserts both branches were asked for and that every body saw the branch it was
pinned to. Deterministic, and it is the property the flip in Task 8 is able to break.

**C5. The `materialise` restore had no test.** `test_the_switch_is_restored_after_an_exception`
raises on the first `_compile`, which is the *warm* call on the native branch — before
`_python_pass` installs either patch. So it proved `_on_path`'s `finally` and said
nothing about the two `parser_mod.materialise = …` assignments, one of which is
`_stubbed` and one `_timed`. Added
`test_the_materialise_patch_is_restored_after_an_exception`, which raises only while
a patch is installed so the failure lands inside `_python_pass`'s own `try`.

Two smaller things the same review settled and this plan now records as **kept, not
fixed**: `_on_path` restoring *absence as absence* is the right spelling, and raising
`RuntimeError("the walk was never called")` out of `decompose` is right — it is a
failed measurement rather than a programmer assertion, and it stays loud under
`-O`.

### Round 2 on the same item

**C6. The summation was untested.** C2 made `materialise` the sum of a compile's
outermost calls, and the counts that came with it prove the recorder *grouped* its
calls — they say nothing about what it did with the durations. The reviewer found
this by mutating `live[0][0] += elapsed` to `live[0][0] = elapsed`: **all seven rows
stayed green.** "Median of the last call's duration" would have shipped under a label
promising the whole walk.

`test_the_walk_durations_are_summed_within_a_compile` pins it with a scripted clock
that advances one fixed step per read, so the assertion is exact rather than
statistical — no `assertGreater`, no tolerance. **What the row does not cover is
stated in the row**, and round 3 closed exactly that gap: a median over sums that
*differ* between compiles cannot be seen by a uniform clock over equal compiles, so
the scripted compile now makes the two compiles unequal — three calls and two — and
puts the longer one first. `_median` takes the upper of two, so the expectation is
order-independent, while a recorder that kept only the last compile reads the shorter
sum and is caught.

**C7. `samples` was still documented as "one per compile".** After C2 that contract
belongs to `compiles`. The docstring now says so, and names the shape that breaks the
old reading.

Also settled, **kept not fixed**: alternating the two paths round by round distributes
drift rather than guaranteeing its cancellation, and difference-of-medians answers
"the difference between typical branch timings" where a median of per-round
differences would better exploit the pairing. That is an improvement, not a
prerequisite, and the shipping measurement is the bench either way — so the plan does
not describe either estimator as removing machine noise. One discarded warm call per
branch stays, with "pays" qualified to "exercises": it runs the source and triggers
the lazy imports, and it cannot promise the allocator has settled.

### Round 3 on the same item

**C8. The two walk-count rows were asserting a property of the process, not of the
template, and both were red under the gate.** Each used
`{% partialdef p %}{{ a }}{% endpartialdef %}{% partial p %}` and required it to make
two outermost `materialise` calls per compile. Under `scripts/run_unit_tests.sh` it
makes **one**: `samples == compiles`, so `test_a_partial_definition_is_not_a_second_compile`
failed its own non-vacuity assertion and `test_the_walk_durations_are_summed_within_a_compile`
read 2 where it expected 4. Both rows were green when the module ran alone, which is
how they were written and checked.

The count is deterministic either way and differs by invocation: **two** in a fresh
process, **one** under the unit suite. The difference survives `-I`, a change of
working directory, re-importing every test module by hand, and importing them one at
a time interleaved with compiles — and `ShellHost.has_tag("partialdef")` answers
`False` in both settings, with the root nodelist `['PartialDefNode', 'PartialNode']`
in both, so the routing is not visibly the variable.

**The reviewer found the cause in round 3, and it is module identity rather than
engine behaviour.** `ShellHost.register_partial_def` materialises the parse-time body
through `render.materialiser()`, which caches `django_rust_templates.engine.parser` on
its **first** call. Test modules loaded a fresh copy of that file and installed it in
`sys.modules` under the same name. Under `run_unit_tests.sh` the
cache therefore ended up holding the **copy** while `compile_profile.parser_mod` was
the original, and the body's call landed on a module whose `materialise` was never
patched; the recorder saw one call per compile. A fresh process that compiled before
discovery cached the original and saw both. Measured directly:
`render.materialiser() is not compile_profile.parser_mod` after the suite, and `is`
before it.

**That cause has since been removed rather than worked around, and Task 2 is where.**
**Five** modules carried the by-path load, not the four an earlier draft of this plan
named: `test_materialise`, `test_filter_expr_parity`, `test_parser_parity`,
`test_variable_parity` and `test_ref_table`. (`test_filter_parity` and
`test_escape_protocol` were named here wrongly — they load `scripts/filter_probe.py`
under `django_rust_templates.filter_probe`, a different module, and never touched
`parser.py`.) Every one of the five was vestigial and cited the same obsolete reason:
the by-path route existed because this package once substituted itself for
`django.template` on `sys.meta_path`, and `django_rust_templates/__init__.py` now says
in as many words that it "does not impersonate Django's" — while `test_ref_table`
imported the `engine` package by name two lines above its own by-path load. All five
are ordinary imports now, and the count is deterministic under every invocation.
Measured after the whole suite has run: `render.materialiser() is compile_profile.parser_mod`
is `True`, where it was `False` before, and `sys.modules["…engine.parser"]` is the object
it held at import.

**The fix is not to pick a count but to stop asking the engine.** Both rows now drive
`decompose` with a scripted `_compile` that makes a known number of `materialise`
calls, through a genuine argument tuple captured from a real compile — so the calls
go through the production recorder and the real materialiser, and only the compile
around them is a stand-in, which is the one thing a fixture is entitled to replace.
The fixture is then deterministic by construction, under any invocation. Verified by
mutation against all three ways to get the summation wrong: `=` for `+=` (killed), a
median over the individual calls (killed), and keeping only the last compile
(killed). The third is new coverage — round 2's row said in its own docstring that it
could not see it.

**C9. `compile_profile.py` stated the `{% partialdef %}` count as a fact about the
template.** What the recorder has to survive is a *capability* — a compile may make
several outermost calls — and that is what the docstring says now, alongside the
module-identity cause. The tool groups by compile and sums unconditionally either
way; only the fixture changed.

**C10. `_a_real_walk_call` inherited the default switch.** The reviewer's blocking
finding: `_native.compile_template` is handed `parser_mod` as its tree module, so the
genuine compile's walk call comes back through the name this helper patches — but
**only on the Python branch**. After Task 8 flips the default the native branch never
calls `materialise`, `captured` stays empty, and `captured[0]` raises `IndexError`
from inside a fixture. It is F3 and C4 a third time, in a third place: a seam that
reads a module global without pinning it. The compile is now wrapped in
`_on_path(False, …)`, and an empty capture raises a message that says which two
things have diverged rather than a bare subscript error.

**C11. The summation row derived its own expectation.** It read `scripted.log[-2:]`
and expected `_median([count * step for count in timed])`, so a wrong `_median` would
move both the reported number and the expectation, and a change that reversed the two
compiles would go unnoticed. The reviewer asked for the counts and the number to be
written out. They are: `scripted.log[-2:] == [3, 2]`, `samples == 5`, and
`materialise == 30.0`. That couples the row to how many compiles the earlier passes
make, deliberately — a change there now fails loudly and has to be re-derived instead
of being absorbed.

**One thing the review left for Task 2.** The modules that install a fresh copy of
`parser.py` under its own name are a process-wide hazard: any instrument that patches
or measures that module by name can be looking at a different object than the engine
is. Task 2's parity harness is exactly such an instrument, so its first step was to
read those loaders rather than inherit them — and it found **five** of them, not the
four named here. All five are ordinary imports now, and the hazard is closed rather
than documented.

## Code review revisions — Task 2

A fresh three-round budget starts here, against `tests/materialiser_corpus.py` and
`tests/test_materialiser_parity.py`. Round 1's verdict: **revise Task 2 before Task 3 —
the loader repair is sound; the oracle still accepts a condition that evaluates
differently.** All five findings accepted, and the fifth was worse than reported.

The shape is the plan's recurring one, now for the fourth time: **the artefact was
doing something. It was just not the thing its label said.** Round 3 caught five tests
that could not fail; Task 1's round caught a measurement that could not be *right*;
this round caught an oracle that could not *see* — and, underneath it, an arm that had
never run at all.

**C1. The loader repair, and the cleanup the review then asked for.** Ordinary imports
are the right repair; the review reproduced this by shuffling the run and confirming
`NODE_CLASSES` came back exactly as found and `render.materialiser() is materialiser`
held. But the mutation helpers were `NODE_CLASSES[name] = cls` followed by
`addCleanup(NODE_CLASSES.pop, name)`, and **a pop destroys any pre-existing binding**: a
name that already had a registry entry would be deleted rather than restored, and the
failure would surface in whatever test ran *next*. Now a save-and-restore — remembered
presence, remembered value — in one `_register` helper that the two call sites share.
Task 2's harness is what made that fatal rather than theoretical, because the registry
is now the process's own.

**C2. The ignored switch establishes no native parity, and Task 3 owns the row that
does.** The parity row flips `USE_NATIVE_MATERIALISER` and compares, but before Task 3
*neither* branch reads it, so the two sides are the Python walk against itself. The
review's prescription: patch Python `materialise` to raise on a simple text template,
and require `native=False` to raise while `native=True` succeeds — then mutation-test it
by forcing the Rust side's switch selection to `false` and requiring the row to go red.
Carried into Task 3 as its own step; a switch with no dispatch-sensitive row is a
green light for a wire that was never connected.

**C3. The engine lifetime stays per-`_tree`, and the *claim* about it was wrong.** One
fresh `Engine(libraries=…)` per call is right: sharing one across the two branches lets
the first compile's effects reach the second. What the plan got wrong is the word
"isolation". A fresh engine does **not** isolate the libraries: `Engine(libraries=…)`
resolves each path through `import_module(...).register`, so the module-level `Library`
object is shared between engines and its mutations persist. What is isolated is the
engine's own `template_builtins` list, which is why the one row that registers a filter
appends its own `Library` instead of touching `defaultfilters` — and why a row that ever
mutated a *builtin* library would need explicit restoration. Corrected in the docstring.

**C4. The exclusion set.** Deleting it is right, and the review added a second reason
worth keeping: removing a dead exclusion leaves the check's present strength unchanged
— it removes misleading *policy*, which is the thing that gets copied into the next
file. The same reasoning then applied one screen up, where `_SKIPPED_NODE_ATTRS` sat as
an empty `frozenset()` filtering nothing in the node arm; it is gone, replaced by the
comment that says why nothing is skipped.

**C5. Three false greens in the oracle — and a fourth, dead arm underneath them.**
Reproduced by the review, and all three were real:

  * **`TemplateLiteral` and `Literal` were interchangeable.** `TemplateLiteral.eval`
    resolves its operand against the context; `Literal.eval` returns it verbatim, so
    with `a=None` one condition is false and the other is true. The class now rides in
    the fingerprint, and so does `TemplateLiteral.text`, which is what `display()`
    shows.
  * **A node was identified by its class *name*.** Two libraries can each define a
    `TextNode`, they render differently, and `NODE_CLASSES` — keyed by name — is where
    the collision lands. `type(value)` is carried instead, on the same reasoning as
    `_callable_identity`: `==` on a class is identity, so the comparison is exact and
    no address is printed. Two classes the operator table does not name are told apart
    the same way.
  * **`lookups` and `is_var` were normalised past their own types.** `tuple(value.lookups)`
    coerced a list into a tuple — the exact coercion the comment above it claimed to be
    preventing — and `is_var` was written into its tuple raw, where `False == 0` and
    `True == 1` are the collisions the primitive arms exist to prevent.

  **The fourth was not in the report and is worse than any of the three.** The
  `Literal` arm sat *below* the operator arm, and `Literal` is a `TokenBase` with
  `lbp = 0` whose `first` and `second` are `None`. The operator arm therefore claimed
  every literal in every tree and returned `['op', None, 0, ['none'], ['none']]` — the
  value discarded, and a literal indistinguishable from an operator class
  `_operator_id` could not name. `{% if a %}` and `{% if b %}`, whose condition *is* a
  `TemplateLiteral` with no operator at all, fingerprinted identically, and the literal
  arm was dead code that no row could reach. Fixed by ordering the arm first, and the
  new rows are what found it: `test_a_literal_carries_its_value` fails against the old
  fingerprint, which is the only reason to believe the fix.

  **A census of `TokenBase` subclasses says the ordering fix is complete.** The arm's
  predicate is `isinstance(value, TokenBase) and hasattr(value, "lbp")`, so every
  subclass carrying an `lbp` is claimed by it — measured, that is `Literal`,
  `TemplateLiteral`, `EndToken` and the thirteen `Operator` classes the factory builds.
  The thirteen are named by `smartif.OPERATORS`; `Literal` and `TemplateLiteral` now have
  their own arm above; `EndToken` is the only one left, and it is a parser sentinel that
  terminates an expression rather than appearing in a materialised tree. It would
  fingerprint as an operandless operator, and — with `type(value)` now carried — as a
  *distinguishable* one. No further arm is owed.

**C6. Two of the new rows did not discriminate, and one of them went green for the
wrong reason.** Both were caught by re-running every new assertion against the old
implementation rather than trusting a green suite:

  * The tree-level `{% if a %}` / `{% if b %}` row passed *before* the fix as well —
    an `IfNode` carries its own `{% if a %}` token, whose contents differ, so the trees
    differed while the two literals did not. It now fingerprints the **condition**.
  * The `is_var` row mutated `False` to `1`, and `False == 1` is `False`, so nothing
    collided and the row would have passed either way. It now runs both directions with
    the value that actually collides: `False`/`0` for a literal argument, `True`/`1` for
    a variable one.

  Eight assertions, each measured to fail against the pre-review fingerprint and pass
  against this one. A sensitivity row that has never been shown to fail is the same
  green bulb this section keeps finding.

### Round 2 on the same item

Round 2's verdict: *"A is fixed, but the promised registry restoration is absent from the
working file, and three typed flags still produce false greens. All 84 targeted tests
passed; repeated compiles of all 48 corpus sources fingerprinted equally. Task 2 is not
yet ready for Task 3."* Every finding accepted; the count was accurate and the first one
was a straight contradiction between this document and the working tree.

**C7. The registry restoration this section promised was not in the file.** C1 above
described a save-and-restore `_register`, and the code still had set-then-pop. **A
section of this document is not evidence that the change landed** — the review read the
working file, not the section, which is the only thing that could have caught it. Both
are now true of the file, and the row that pins it,
`test_registering_a_node_class_restores_the_registry_exactly`, runs both directions:
a name that was absent and a name that displaced an existing entry.

**C8. Three fields read a bare `bool(...)` and so could not see `1`.** `bool(1) is True`,
so `NodeList.contains_nontext`, `FilterExpression.is_var` and `Variable.translate` each
reported the same fingerprint for `True` and `1` — the flag arms one screen down exist to
tell exactly those apart, and none of them ever saw the field. `is_var` now walks *both*
of the field's reachable values, `True` for `{{ a }}` and `False` for `{{ "x" }}` — and
that is coverage of the field, **not** a second thing needed to catch the mutation:
`bool()` collapses `False`/`0` and `True`/`1` alike, so either direction alone turns the
row red, as round 3 pointed out against an earlier sentence of this section that claimed
otherwise. Mutation-tested by rewriting each of the three call sites back to `bool(...)`
one at a time and requiring the owner row to go red: all three do.

**A note on the harness, because it produced a false negative first.** The mutation
probe initially reported the `translate` row as non-discriminating. It was not: two
mutations of equal length, written within the same second, leave a `.pyc` whose stored
`(mtime, size)` is unchanged, so the subprocess re-ran the *previous* mutation's
bytecode. The probe now runs with `-B`. The general form is the one this whole plan keeps
running into — **a green result is only evidence about the code that actually ran** —
and it is the reason the manual re-run, not the probe's own verdict, is what the claim
rests on.

**C9. `_operator_id` returning `None` was silent.** An operator class the table does not
name would be fingerprinted as an operandless operator, its real operands never compared.
It now raises an `AssertionError` naming the class, with
`test_an_operator_class_the_table_does_not_name_is_loud` as the row. This is the same
failure C5 found by accident — the `Literal` arm was dead for exactly this reason — turned
into something that cannot happen quietly again.

**C10. The class-identity claim was unconditional, and it is not.** `==` on a class is
identity *for a class built by plain `type`*; a metaclass overriding `__eq__` would
substitute its own answer. No node in the registry does that, and none is expected to,
but the rule is now scoped to the classes this engine builds in both places it is
stated, rather than asserted about classes in general.

### Round 3 on the same item

Round 3's verdict: **"Yes — Task 2 is ready for Task 3."** It reproduced all three flag
mutations independently, in fresh `-B` subprocesses compiling modified source in memory
so the working files stayed untouched, and confirmed the registry mutation turns the
restoration row red with `KeyError: 'TextNode'`. It read the round-2 subsection against
the files and found no executable change missing in either direction.

**C11. One sentence was wrong, and it was mine, in both places I wrote it.** C8 said a
row that ran only one colliding direction "would still have been green against a
fingerprint that wrapped this field in `bool()`". It would not: `bool()` collapses
`False`/`0` and `True`/`1` alike, so either direction alone kills that mutation. Walking
both of `is_var`'s reachable values is coverage of the *field*, which is worth having,
and it is not a second thing that was needed to catch the regression. Corrected here and
in the row's own comment.

**A note on this round's budget.** Task 2's three rounds are now spent, as Task 1's were.
The pattern across both items is that the *first* round finds the shape of the failure
and the later rounds find the places it recurs — and in each item the last round found
something the earlier ones had left standing, including, here, a claim in the section
that was written to fix the previous round. The reviewer's own words on the method are
worth keeping: it re-derived every mutation in a **fresh process with bytecode writing
off**, which is the only reason it did not reproduce the false negative this item's own
probe hit.

## Code review revisions — Task 3

### Round 1 on the same item

Verdict: **no**, and it was right. Two findings were defects and three were claims in
this document that the working tree contradicted. The gate was green throughout, which
is the point worth keeping: **four of the five were invisible to every row in the
suite.**

**T3-C1 — `nodelist.append` was looked up after the node was built, and that is a real
divergence.** `materialise` reads `nodelist.append(materialise_node(desc, …))`, so
`LOAD_METHOD` resolves `append` and *then* evaluates the argument; a factory that
rebinds `NodeList.append` while it builds a node is not seen by that node's own append.
The first `materialise_native` built the node first and called
`nodelist.call_method1("append", …)` afterwards, so it used the replacement for the
very append the original was bound to serve. Reproduced against the shipped build
before the fix: with a one-node template whose `TextNode` factory rebinds
`NodeList.append`, **Python calls the replacement 0 times and native called it once.**
The fix is to bind `append` before `materialise_one`, and Step 6 carries the row plus
the mutation that proves the row can fail.

*Recorded because it cost time:* the first probe of this patched
`django.template.base.NodeList`. The engine has its **own** `NodeList`
(`django_rust_templates.engine.base`), so the patch could not fire on either path and
the run read as "no divergence" on both — a false negative of exactly the shape the
`-B` one had. The row's docstring names the class for that reason.

**T3-C2 — `Bridge::ALL` is gone from Task 3 and now lands in Task 8 with its reader.**
It was written enumerated and complete at Task 3, where all but one of its eleven variants
had no constructor and nothing read the list at all. It therefore needed an unconditional
`#[allow(dead_code)]` in *both* compilations to survive `-D warnings`, and the plan's
`cfg_attr(not(test), allow(dead_code))` would not even have done that, because
`mod walk_depth` is gated on `not(feature = "extension-module")` while `cargo test
--workspace` compiles with default features. The reviewer's phrasing is the rule now:
**an unread enumeration establishes no pin.** Each task declares the variants its own
arms call, and `ALL` and `the_bridge_list_is_pinned` land together in Task 8. The enum
went 11 variants → 1, and there is no `allow` anywhere in `materialise.rs`.

**T3-C3 — the docstring's "three contracts … are reproduced exactly" overclaimed.**
Agreed by both of us. At Task 3 every node goes through `materialise_node`, so the three
orderings are *inherited* from Python, not established here, and the module docstring
now says in as many words that Task 3 preserves them the easy way and pins none of
them. The one place the ordering is genuinely Rust's at Task 3 is the root loop, and
that is exactly where T3-C1 bit.

**T3-C4 — the measurement's causal story was wrong twice.** The reviewer counted
**461** top-level nodes and **621** total `materialise_node` calls, and I reproduced
both: the counts are identical on the two paths, and what differs is that native makes
its 461 entries across the boundary while Python makes one. So the wire is **not**
skipped (`node_desc_to_py` recursively encodes each fallback subtree), the recursion did
**not** move, and the crossings are 461 and not 621. The paragraph in Step 8 and the
Task 3 intro were both rewritten around the counts. The two cold readings are also no
longer offered as page-cache evidence, and `stubbed` is no longer claimed to prove
parse time unchanged — it can hide a regression inside the walk's own variance.

**T3-C5 — the mutation step's expected result was wrong.** Step 6 said "every other row
must stay green". Two rows go red: the dispatch row, and
`test_a_present_but_not_boolean_constant_is_loud`, because the mutation replaces the
whole `match` and so deletes the `getattr` that row tests. Measured: exactly those two.
Step 6 now says two and says why the second is legitimate.

**What round 1 did not find, stated so the next reader does not re-derive it.** The
reviewer checked the `resolve_ref`/`register_ref` derivation and agreed the Rust does
not need it, because `compile_template` always binds both; they checked the three
whole-file fences against the working files; and they found no third divergence. I
checked separately, by walking every corpus source's tree, that the `contains_nontext`
write is reached — **48 of 48** have at least one nodelist carrying the flag, so the
`setattr` line is covered by the parity row rather than passing for want of a source
that sets it.

### Round 2 on the same item

Verdict: **no**, on two findings, one a defect and one a document contradiction that
round 1's own rewrite had left standing. This is the same pattern as Tasks 1 and 2: the
last round of an item finds something the previous round's fix introduced or failed to
finish.

**T3-C6 — the row's cleanup leaked a class attribute, and it did so invisibly.**
`NodeList.append` is inherited from `list`; `"append" in NodeList.__dict__` is `False`.
So the round-1 row's `finally: nodelist_cls.append = real_append` did not restore
anything — it **created** a real attribute on a class every compiled template in the
process shares. Measured by the reviewer: `{'append'}` present in the class dict after
the row ran, with the row itself still green. Nothing inside the row can see its own
patch, which is why only an outside reader could catch it. The restore now remembers
*presence* and uses `delattr`, in the same shape as `_register` in
`tests/test_materialise.py`, and three assertions pin the restoration in both
directions. Mutation: put the leaky cleanup back and the row fails on
`assertEqual("append" in nodelist_cls.__dict__, had_append)` with `True != False`, with
every assertion above it still passing.

**T3-C7 — the round-1 measurement rewrite appended caveats without deleting the claims
they contradicted.** Step 8 still said `stubbed` was "unmoved from Task 1, so the parse
did not shift and the whole of the difference is the walk", and still attributed the
two cold outliers to "a cold page cache and a fresh `.so`". The reviewer's phrasing is
the reason this matters: **a later caveat does not remove a standing contradiction.** The
block is now one argument with four bullets — `stubbed` is a genuine control; steady
`stubbed` does not prove an unchanged parse, because a regression inside the walk's own
variance would leave it steady too; the difference is therefore not established to be
the walk; and "quote the spread" stands while the page-cache explanation is gone, with
the outliers' correlation to the rebuild recorded as suggestive and explicitly not
evidence.

**Also settled here:** the constructor and token ordering belongs to **Task 4**, which
is where `materialise_node` starts being replaced, and not to Task 5, whose identity
tests cover a different contract.

**T3-C8, caught by the round-3 review and fixed after it:** the same wire-encoder claim
survived in a **third** place. `## Global Constraints` still said Task 3 "removes by not
calling them at all on the native path" — the identical error corrected in the Task 3
intro and in Step 8. Two corrections in the task body did not reach the constraints list,
which is exactly the kind of place a claim goes to be quoted without being re-read. The
bullet now states the opposite and points at the measurement.

**The clippy gap is recorded rather than papered over.** Both configurations run green
here — `--workspace --all-targets` and `-p template-py --no-default-features
--all-targets`, both exit 0 — but the reviewer could not certify either, in any of the
three rounds, because the sandbox denies Cargo's build lock and cached diagnostics
cannot certify the current tree. That is a gap in the review, and it is written down
here so that a later reader does not mistake a green local run for an independent one.

## Code review revisions — Task 4

### Round 1

Verdict: **no** — one defect in the code, two gaps in the evidence, and two
contradictions between this document and the tree. Every row in the suite passed
throughout, so **all five were invisible to the tests**: the first defect changes the
*class* of a node whose name is unchanged, and the two evidence gaps were about rows
that did not exist yet.

**T4-C1 — `PyDict::get_item` bypasses a dict subclass's `__getitem__`, and that is a
real divergence.** Step 4 had `let registry = tree.getattr("NODE_CLASSES")?
.cast_into::<PyDict>()?` and then `registry.get_item(desc.class_name.as_str())`.
`PyDict::get_item` reads the concrete dict storage; `materialise_node` does
`NODE_CLASSES[class_name]` at `python/django_rust_templates/engine/parser.py:347`,
which is `PyObject_GetItem` and honours an override. **The reviewer measured it**: with
`NODE_CLASSES` replaced by a dict subclass, Python produced the override's value and the
native walk produced the real one — two trees differing in the class of a node with an
identical `__name__`, which no fingerprint and no `repr` in this file would have shown.
The fix is to stop casting the registry down at all: `getattr` it as a `Bound<PyAny>` and
call `get_item` on *that*, matching only `PyKeyError` so that anything else a
`__getitem__` raises propagates as itself — which is what `except KeyError` does.
`RegistrySubscriptionTests` carries the row and its Python control, and Step 2's table
carries the mutation that puts the cast back.

**T4-C2 — the Rust unknown-class arm had no row at all.** `UnknownClassTests` built
its descriptor by hand and called `materialiser.materialise_node(desc)` — Python's
function, not the walk. So `loud(tree, Bridge::UnknownNodeClass, …)` was reachable and
**nothing reached it**: a walk that never consulted the registry and a walk that
consulted it after its children passed every other row in the file. The reviewer
supplied the fixture: drop `IfNode` from the registry, compile an ordinary
`{% if a %}x{% endif %}` with a recording `TextNode` factory, and require the error
before that factory runs. `UnknownClassTests.test_the_native_walk_raises_before_any_child_is_built`
is that row, and Step 2's table carries its mutation.

**T4-C3 — the constructor/token ordering tests this plan promised were absent.**
The `materialise.rs` header says each of its three contracts "gets a row when the task
that could break it lands", and Task 4 is the task that builds a parent in Rust, so
contracts 2 and 3 were owed here. `ConstructionOrderTests` adds **three** rows, each
running `native in (True, False)` as `subTest`s: children before the parent, attributes
against a populated child list, and `token`/`origin` not handed to the constructor.
Step 2's table carries a mutation for each. Round 2 refused the reason I gave for
leaving the second one until Task 5; see T4-R3 below, which is where that row's
justification ended up.

**T4-C4 — the guard's comment claimed both `{% cycle %}` failures were silent, and one
of them raises.** The `Bridge::UnknownNodeClass` guard in `materialise.rs` is there
because `{% cycle %}` and `{% resetcycle %}` reach the registry through a path the
other tags do not. The first draft said the two failures were "silent in the tree",
which round 1 showed was wrong: deleting the guard makes `{% resetcycle %}` raise
loudly. The comment was rewritten. **The rewrite was wrong as well, and round 2 caught
it** — see T4-R2.

**T4-C5 — `_unknown_node_class`'s docstring claimed it "reproduces the previous
behaviour exactly", and it adds a traceback frame.** `raise … from None` does preserve
the message and the suppression — measured on both spellings, `__context__` is the
`KeyError` and `__suppress_context__` is True — but the frame list goes from
`['<module>', 'materialise_node']` to `['<module>', 'materialise_node',
'_unknown_node_class']`. The docstring now says what is preserved, what is not, and that
nothing asserts a frame list. The reviewer also checked for an orphaned anchor from
moving the string out of `materialise_node` and found none.

### Round 2

Verdict: **no** — "the implementation fixes hold, but four explanations still contradict
the working files. All 92 targeted tests pass; I did not rebuild the shared artifact."
There was no code finding in the round: **all four are prose I wrote in round 1**, and
three of them were corrections *I* had made to an earlier draft. They are recorded here
in full because a wrong reason attached to a right fix is how the next task inherits
the wrong reason.

**T4-R1 — the deferred-attribute-order claim was false.** T4-C3's write-up said
contract 2's second half would be "a row about nothing" until Task 5, since
`AttrValue::NodeListRef` is Task 5's arm. That is wrong about the *value*:
`NodeListRef` is not a primitive, so `attr_to_py` hands it to `_decode_value`
**today**, with the `child_lists` Task 4 built — and the reviewer measured it, recording
`NodeListRef(0)` decoded against a child list of length 1 on native
`{% if a %}x{% endif %}`. The arm is Task 5's; the ordering is Task 4's, and reaching the
fallback is not a reason to leave it unpinned. The row was **added**, not weakened:
`ConstructionOrderTests.test_the_attributes_resolve_against_a_populated_child_list`
asserts `tree[0].conditions_nodelists[0]` is `(condition, branch)` with `len(branch) == 1`
and `type(branch[0]) is NODE_CLASSES["TextNode"]`, on both paths. Its mutation binds
`child_lists` to `PyList::empty(py)` and gives
`LookupError: <NodeListRef 0> is out of range: the enclosing node has 0 nodelist(s)`
on the native subTest only. Task 5 replaces the arm and inherits the row.

**T4-R2 — the cycle-detection history was wrong, and the real one is stronger.**
The rewritten comment (T4-C4) said the `{% cycle %}` failure was silent "until two
renders disagree, and the first build caught it only because the corpus row renders".
**`TwoPathParityTests` never renders — it compares fingerprints.** Round 2 asked for
the real account, and deleting the guard and re-running gave it: `resetcycle` is loud
(`LookupError: <NodeSlot 0> is an empty reference-table slot`, out of `_decode_value` at
`python/django_rust_templates/engine/parser.py:750`), and `cycle_twice` was caught by the
**token** — Python's one shared object carries the *last* mention's token (`'cycle c'`,
because `extend_nodelist` writes `node.token` at every append) while two Rust-built nodes
leave the first with its own (`"cycle 'a' 'b' as c"`). That is exactly why `fingerprint`
carries `token.contents`. **The guard's justification is stronger than the claim it
replaced, not weaker:** nothing in this file would have seen the two nodes cycling
apart, which is the failure that matters at run time.

**T4-R3 — the deleted probe's reason was imprecise.** A row was written for contract 3
and then deleted, and the write-up gave the wrong reason for deleting it: "the instance
does not exist until `factory.call` returns". An instance *does* exist while `__init__`
runs. The sound argument is that **`materialise_one` is handed the instance only once
`factory.call` has returned** and holds no reference to it before then, so a probe
reading `self.token` inside the constructor cannot observe anything the walk did. Step 2
already holds these rows to "a row that cannot be made to fail is not evidence", so the
unfalsifiable row stayed deleted and the row that stayed asserts the falsifiable thing
instead — that `token` and `origin` are not handed to the constructor. Its mutation is
one `kwargs.set_item`.

**T4-R4 — the subscription fixture's description was wrong about its own class.**
The `Tagging` docstring said it had "the same `__name__` as the real `TextNode`"; it is a
subclass, so its name is `Tagging`. And `RegistrySubscriptionTests`' class docstring
claimed "no fingerprint and no `repr` would show it", which is the opposite of true:
`fingerprint` carries node classes **whole**, and
`test_two_node_classes_with_one_name_are_told_apart` exists precisely so that two classes
of one name are told apart — so a fingerprint *would* have reported the divergence.
**What actually let the defect through is that every other row in the file uses the real
`NODE_CLASSES`, and the corpus never substitutes one.** Both docstrings now say that, and
the rows are direct assertions on subscription rather than fingerprint comparisons, so
they name the mechanism instead of the symptom.

### Round 3

Verdict: **yes** — "Task 4 is ready for Task 5. All 36 parity tests pass; the new
populated-child-list row closes the remaining coverage gap, and the registry, cycle and
helper explanations are corrected." The reviewer read the four revisions against the
working files and re-ran `tests.test_materialiser_parity` (36 tests, OK) on the existing
artifact; no code finding, and no further round is owed.

One nonblocking prose remnant came back with the verdict, and it was a real one: the
comment at `tests/test_materialiser_parity.py:1231` still gave T4-R3's *superseded*
reason for dropping the unfalsifiable probe — "the instance does not exist before
`factory.call` returns". It is the same sentence the plan's fence had copied. Both now
carry the sound version: an instance does exist while `__init__` runs, but the walk is
handed it only once `factory.call` has returned. **The plan and the file were fixed
together, so the Step 1 fence is byte-exact again** — worth noting because a fix applied
to one copy and not the other is exactly the class of drift this section exists to
record.

**The clippy gap is unchanged, and it is still a gap.** Both configurations run green
here — `--workspace --all-targets` and `-p template-py --no-default-features
--all-targets`, both exit 0 — and the reviewer again could not certify either, because
the sandbox denies Cargo's build lock.

## Global Constraints

- **Every step that says "commit" is suspended, and the work stays in the working
  tree.** The plan was written with a commit per task; the instruction under which it
  is being executed is that nothing is committed or pushed until asked. Tasks 1-5 have
  been run this way — `crates/template-py/src/materialise.rs`,
  `tests/test_materialiser_parity.py`, `tests/materialiser_corpus.py`,
  `tests/test_compile_profile.py`, `scripts/compile_profile.py` and `docs/` are all
  still untracked in `git status`, and the switch is still off. Step 5 of Tasks 4 and
  5 say so in place; Tasks 6-8's commit blocks are read the same way. **Task 8 is the
  one whose commit would be a real deliverable** once the switch flips, so it is the
  one to re-confirm before taking.

- **The measured stake, on this machine, `real-world/large` (9279 chars, 621 nodes):**

  ```
  A  _native.compile_template, whole        1834.1 us   (min 1734.7)
      parser.materialise itself             1153.1 us   ( 62.9%)
  B  A with materialise stubbed              655.5 us   (min  639.0)  = parse + wire + template_for
      plan  = (A - B) - materialise            25.5 us
  ```

  **This block is the probe that motivated the plan and it is no longer the
  baseline.** Task 1 Step 5 replaced it with a run of the committed tool, which reads
  `922.5 us` of `1758.4 us` on a different build of the same tree — see there for the
  table, the run-to-run spread and the two counts. The block above is kept because the
  `plan = (A - B) - materialise` line is the residual the plan later **withdrew** (its
  arithmetic mixes two instrumented passes with one uninstrumented pass) and because
  the `25.5 us` is one of the two figures Task 1 Step 5 declines to choose between.
- **S1's ceiling is the decode walk plus the wire-*building* share of the parse
  figure** (`nodelist_desc_to_py`, `node_desc_to_py`, `attr_value_to_py`).
  **Task 3 does not start collecting it, and an earlier version of this bullet said it
  did.** The native walk still calls `node_desc_to_py` once per node — through
  `fallback_node` — so the encoders are entered *more* often at Task 3 than on the path
  it replaces, just in smaller pieces; the round-1 review measured this (461 calls
  against the Python path's one, over the same 621 nodes) and the round-3 review caught
  the sentence surviving here after being fixed in two other places. The share is
  collected by Tasks 4-7, when the fallback arms stop being taken; Task 3's own reading
  is **negative** for that reason, and Step 8 of that task says so.
- **Django-visible compatibility is the only load-bearing thing.** The tree must
  be identical node-for-node: same classes, same attributes (by name *and* type),
  same child nodelists, same `token`, same `origin`, and the same *objects* where
  Django relies on identity (`{% cycle %}`).
- **No pinned counts.** Every "we covered everything" check is a structural
  predicate over a live source of truth (`NODE_CLASSES`, the corpus), never a
  literal integer.
- **Line-shift hazard, and the gate order that goes with it.** `scripts/anchors.txt`
  carries line-number anchors into `crates/template-py/src/lib.rs` and
  `python/.../engine/parser.py`. Insertions at the *end* of a file shift nothing;
  insertions anywhere else do. **`cargo fmt --all` runs *before* the anchor check**,
  because `rustfmt` reflows the regions this plan edits and would move an anchor
  after the check had passed. Every task's gate is, in this order:
  `scripts/run_unit_tests.sh` → `cargo fmt --all` → `cargo clippy …` →
  `python -I scripts/check_python_anchors.py --check`, repairing drift with
  `--update=crates`.
- **The default path does not change until Task 8.** `USE_NATIVE_MATERIALISER` is
  `False` at every commit before it, so the suite is green at every step and a
  bisect lands on one shape.
- **Test style is `unittest`**, run through `scripts/run_unit_tests.sh`. Rust tests
  live in `#[cfg(all(test, not(feature = "extension-module")))] mod …` blocks and run
  through `scripts/run_rust_unit_tests.sh`.
- **Commit messages end with** `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- **Reviews go to the `destination-reviewer` herdr pane, never to a subagent, and
  they are submitted through `$CLAUDE_JOB_DIR/tmp/review.sh`.** `herdr agent prompt …
  --wait --until idle` does not work from this workflow: every round is submitted from
  an already-idle pane, and `--wait` "matches idle … the first matching state observed
  after submission", so it can match the state that was there *before* the prompt
  landed. Measured 2026-10-10: two such processes were still alive 8m22s and 2m26s
  after their agents had answered and gone idle, so the background task never
  notified and a finished review looked like a running one. `review.sh` submits
  without `--wait`, then polls `herdr agent get` for `agent_status` until the reviewer
  is idle, writes the answer to `$CLAUDE_JOB_DIR/tmp/review-last-answer.md`, and
  **exits** — which is what makes a background task notify. Use it for every round.
  **Never run `maturin develop` while a review is in flight**; the reviewer shares
  this venv and the built `.so`.

## Review Focus

Five input classes the spec implies, no task's ordinary steps exercise, and that
would bite a user of the shipped engine. Each gets its test in the task that owns
the code.

1. **A host tag's node, built by Python, reached through the Rust walk.** A user's
   `{% mytag %}` compiles to a `HostNode` descriptor whose only attribute is a
   `NodeRef` to the object the host already built. A Rust walk that constructs a
   *new* node here, or drops the `token`/`origin` attachment, silently changes what
   `{% mytag %}` renders and what a debug traceback says. Expected: the object the
   host built, with this occurrence's token on it. **Identity, not equality** —
   `fingerprint` cannot see the difference.
2. **A second `{% cycle c %}` after `{% cycle a b as c %}`.** Django returns the
   *identical* object, and `CycleNode.render` keys `render_context` by that
   identity. A Rust walk that builds a second node makes the two cycle
   independently — output that is right on the first iteration and wrong on the
   second.
3. **A descriptor attribute whose Python object is an `int` on both sides of an
   intended distinction.** `AttrValue::Int` and `AttrValue::NodeListRef` are both
   integers on the wire; `AttrValue::Bool` is an `int` subclass; `SafeStr` is a
   `str` subclass. Expected: `{% autoescape on %}` carries `True` (not `1`), a
   nodelist reference resolves to a `NodeList` (not an index), and a `{% verbatim %}`
   body stays `SafeString`. A fingerprint that returned raw Python values would
   pass on all three wrong answers at once.
4. **A materialiser reached with no `resolve_ref`.** `_decode_value` is loud about
   this, and the loudness is the whole value. Expected: the same `LookupError`, not
   a silent `None` that renders an empty string.
5. **A malformed descriptor.** A `HostNode` carrying a nodelist, an empty reference
   slot under a `NodeSlot`, a `NodeListRef` past the end. All unreachable from any
   template — Django refuses the source first — and all loud today. Expected: the
   same `LookupError`, from the Rust walk as from the Python one.

---

## File Structure

| Path | Responsibility |
|---|---|
| `crates/template-py/src/materialise.rs` | **Create.** The Rust walk. One public entry point, one function per descriptor shape, the `Bridge` enumeration every Python call goes through, and its own Rust tests for the malformed descriptors no template can reach. |
| `crates/template-py/src/lib.rs` | **Modify.** Declare the module; select the path at the `compile_template` call site. |
| `python/django_rust_templates/engine/parser.py` | **Modify (append only, end of file).** The switch constant, the three message helpers, and the registry-error helper. |
| `scripts/compile_profile.py` | **Create.** The decomposition, as a repo tool rather than a one-off probe, measuring both paths. |
| `tests/materialiser_corpus.py` | **Create.** The parity corpus, with the "every `NODE_CLASSES` entry is exercised" structural check. |
| `tests/test_materialiser_parity.py` | **Create.** The fingerprint, its sensitivity rows, and the two-path parity and census assertions. |

---

### Task 1: The decomposition instrument, and the recorded baseline

The spec's §1 residual is labelled "a subtraction, unmeasured". This task replaces
the *subtraction* with a tool and records what S1 is worth before a line of it
exists. It does **not** replace the plan phase's number: Task 1 Step 5 says why that
one cannot be isolated from Python at all, and leaves the spec's ~115 µs recorded as
unverified rather than adopting it.

**It measures both paths**, because after Task 8 the default *is* the native path
and a tool that patched `parser.materialise` would record nothing and index an
empty list. The shape is therefore: `whole` on both paths (the number the decision
is made from), and the phase split on the Python path only (where `materialise`
exists to be stubbed and timed).

**Files:**
- Create: `scripts/compile_profile.py`
- Test: `tests/test_compile_profile.py`

**Interfaces:**
- Produces: `compile_profile.decompose(source, engine, repeats=25) -> dict` with
  keys `"native"`, `"python"`, `"stubbed"`, `"materialise"`, `"samples"` — the first
  four in microseconds, `samples` a count. The saving is `python - native`, and the
  report prints it.
- Consumes: `django_rust_templates.render.parser_for`, `render.ShellHost`,
  `render.UNKNOWN_ORIGIN`, `_native.compile_template`, and the switch constant.

- [ ] **Step 1: Write the failing test**

```python
"""The decomposition reports the shape it claims to.

Timings are not asserted — only that every phase is present, that both paths were
actually measured, and that the walk was grouped and summed per compile rather than
recorded once per subtree or once per call.

**Nothing here asserts a phase is non-negative, and that is deliberate.** The phases
are independent medians of independently timed runs, and a row that fails on a
loaded machine is a row that fails for being a measurement.

**And there is no fourth timing derived from the other three.** An earlier draft
reported `(python - stubbed) - materialise` as the plan's cost; it is an arithmetic
coincidence between three separately instrumented medians rather than a measurement,
and the step that would have reconciled it (Task 1 Step 5) says why the plan cannot
be isolated from Python at all.

**The walk-count rows drive a scripted compile rather than `{% partialdef %}`.**
There is a template that makes `materialise` run twice for one compile — and whether
a row can *see* the second call turns on module identity, not on the engine.
`ShellHost.register_partial_def` materialises the parse-time body through
`render.materialiser()`, which caches the `django_rust_templates.engine.parser`
module on its first call. Five modules used to load a *fresh copy* of that file by
path and install it in `sys.modules` under the same name — `test_materialise`,
`test_filter_expr_parity`, `test_parser_parity`, `test_variable_parity`,
`test_ref_table` — so under `scripts/run_unit_tests.sh` the cache ended up holding the
copy while `compile_profile.parser_mod` was the original: the body's call went to a
module whose `materialise` was never patched, and the recorder saw one call per
compile. The same template compiled by hand in a fresh process, before discovery,
cached the original and showed both.

**That hazard is gone rather than dodged.** All five are ordinary imports now, so
`render.materialiser()` and `compile_profile.parser_mod` are one object before *and*
after discovery, and the count is two per compile under either invocation. The rows
below were never borrowing a count from the engine and still do not — a row that asked
the engine how many calls it made would be measuring the instrument with the
instrument — so what changes here is only the reason, not the fixture.
"""

import contextlib
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import compile_profile  # noqa: E402

from django_rust_templates.engine import Engine  # noqa: E402
from django_rust_templates.engine import parser as parser_mod  # noqa: E402


class _Clock:
    """Monotonic, one fixed step per read."""

    def __init__(self, step_us):
        self.step_us = step_us
        self.reads = 0

    def perf_counter(self):
        self.reads += 1
        return self.reads * (self.step_us / 1e6)


class _ScriptedCompile:
    """A stand-in for `compile_template` making a scripted number of walk calls.

    **The engine's own count is not a usable fixture.** Whether `{% partialdef %}`
    materialises its body during the parse depends on the invocation — two
    outermost calls in a fresh process, one inside `scripts/run_unit_tests.sh` —
    so any number a row read from the engine would be wrong under the other one.

    **The calls it makes are real.** `call` is a genuine `materialise` argument
    tuple captured from an actual compile, so each replayed call goes through the
    production recorder and the real materialiser. Only the compile around them is
    a stand-in, which is the one thing this fixture is entitled to replace.
    """

    def __init__(self, call, counts):
        self.call = call
        self.counts = counts
        self.log = []

    def __call__(self, parser, host, source, engine):
        count = self.counts[len(self.log) % len(self.counts)]
        self.log.append(count)
        for _ in range(count):
            parser_mod.materialise(*self.call)


def _a_real_walk_call(engine):
    """One genuine `materialise` call's arguments, captured from a real compile.

    Hand-building a descriptor would be guessing at the wire; asking the engine for
    one costs a compile and cannot drift from the format.
    """
    real = parser_mod.materialise
    captured = []

    def grab(descriptor, origin=None, resolve_ref=None, register_ref=None,
             parser=None):
        captured.append((descriptor, origin, resolve_ref, register_ref, parser))
        return real(descriptor, origin, resolve_ref, register_ref, parser)

    def compile_once():
        parser = compile_profile._fresh(source, engine)
        compile_profile._compile(parser, compile_profile._host(parser), source, engine)

    source = "{{ a }}"
    parser_mod.materialise = grab
    try:
        # **The switch is pinned to Python, and that is what keeps this working
        # after Task 8.** `_native.compile_template` is handed `parser_mod` as its
        # tree module, so the root's walk call comes back through the name patched
        # above — but only on the Python branch. Once the flip lands, the native
        # branch never calls `materialise`, `captured` stays empty, and the
        # subscripts below raise `IndexError` from inside a fixture.
        compile_profile._on_path(False, compile_once)
    finally:
        parser_mod.materialise = real
    if not captured:
        raise RuntimeError(
            "the pinned compile made no walk call, so there is nothing to replay: "
            "`_a_real_walk_call` and `compile_profile._compile` have diverged"
        )
    return captured[0]


@contextlib.contextmanager
def _scripted(engine, counts, step_us=10.0):
    """`decompose` driven by a scripted compile and a fixed clock.

    The genuine call is captured before the clock is replaced, so the compile that
    produces it is timed by the real one.
    """
    call = _a_real_walk_call(engine)
    scripted = _ScriptedCompile(call, counts)
    real_time = compile_profile.time
    real_compile = compile_profile._compile
    compile_profile.time = _Clock(step_us)
    compile_profile._compile = scripted
    try:
        yield scripted
    finally:
        compile_profile.time = real_time
        compile_profile._compile = real_compile


class DecomposeTests(unittest.TestCase):
    def test_the_report_names_every_phase(self):
        report = compile_profile.decompose("{{ a }}", Engine(), repeats=3)
        self.assertEqual(
            {*report},
            {"native", "python", "stubbed", "materialise", "samples", "compiles"},
        )

    def test_both_paths_are_measured_whatever_the_switch_says(self):
        """The falsifier for the tool outliving the flip — as a spy, not a timing.

        Once Task 8 sets the constant to True, a tool that read it once at import
        would report the native number in both columns: a saving of zero, printed
        as a result.

        **This row used to assert `python > stubbed`, and that was the defect.** An
        earlier draft ran all the native repeats and then all the python ones —
        before S1 exists those are byte-identical code, and the reviewer measured
        `native 1849.6 us` against `python 1742.3 us` out of exactly that ordering.
        A timing inequality would have been green on a tool whose "saving" was
        entirely the machine. It now reads which branch each body *observed*.

        **It is also the regression guard for `_python_pass` losing its branch
        selection.** `stubbed` and `materialise` are taken with `materialise`
        patched; if the pass ran on the native branch, the patches would not take.
        """
        observed = []
        real_on_path = compile_profile._on_path

        def spy(native, body):
            seen = []

            def probe():
                seen.append(getattr(parser_mod, "USE_NATIVE_MATERIALISER", "<absent>"))
                return body()

            try:
                return real_on_path(native, probe)
            finally:
                observed.append((native, seen[0] if seen else None))

        compile_profile._on_path = spy
        try:
            report = compile_profile.decompose("{{ a }}", Engine(), repeats=3)
        finally:
            compile_profile._on_path = real_on_path

        asked = {native for native, _ in observed}
        self.assertEqual(asked, {True, False}, "one of the two paths was never selected")
        for native, seen_value in observed:
            self.assertEqual(
                seen_value, native,
                "a body ran on a branch other than the one it was pinned to",
            )
        self.assertEqual(report["samples"], 3)
        self.assertEqual(report["compiles"], 3)

    def test_the_recorder_sees_one_sample_per_compile(self):
        """The nested-call defect, pinned through the production recorder.

        `materialise` recurses: `materialise_node` resolves each child through
        the same patched global. A wrapper that appended on every call therefore
        recorded *every subtree* — on `large_template()` that is hundreds of
        entries, whose median is the cost of a typical subtree and not of the
        compile's materialisation at all. The first draft did exactly that, which
        is a plausible-looking number that is simply the wrong quantity.

        **An earlier version of this row built its own copy of the wrapper and
        measured that**, so it would have stayed green through the defect it was
        written for. It reads `decompose`'s count of its own recorder now. The
        template is three deep, so a per-subtree recorder reports far more than
        `repeats`.

        No switch flip is needed here, and that is the point: `_python_pass` selects
        the Python branch itself. A row that had to wrap `decompose` in
        `_on_path(False, …)` to get a sample would be a row testing a tool that
        cannot do its job after Task 8.
        """
        source = "{% for x in xs %}{% for y in x %}{% if y %}{{ y }}{% endif %}{% endfor %}{% endfor %}"
        report = compile_profile.decompose(source, Engine(), repeats=3)
        self.assertEqual(report["samples"], 3, "the walk was timed per subtree, not per compile")
        self.assertEqual(report["compiles"], 3)

    def test_the_recorder_groups_the_calls_of_one_compile(self):
        """An outermost call is not a compile, and the two counts are separate.

        The defect this pins: the first recorder counted *outermost calls* and
        medianed over those, so a compile making two of them contributed two
        entries and the median mixed a three-character partial body with a full
        root — a column that answered neither question and looked like a number.
        Fixing it left `samples` as the only count, which is not one per compile.

        The contract is now `compiles == repeats` always, with `samples` free to
        exceed it, and the two are reported separately so a template in that shape
        says so rather than passing quietly.
        """
        engine = Engine()
        with _scripted(engine, counts=[2]) as scripted:
            report = compile_profile.decompose("{{ a }}", engine, repeats=2)
        self.assertEqual(report["compiles"], 2, "one entry per compile is the contract")
        self.assertEqual(report["samples"], 2 * report["compiles"])
        self.assertEqual(scripted.log[-2:], [2, 2], "the fixture stopped making two calls")

    def test_the_walk_durations_are_summed_within_a_compile(self):
        """That `materialise` is a **sum**, which no count can show.

        The counts in the row above prove the recorder grouped its calls
        correctly; they say nothing about what it did with the durations. The code
        review found the gap by mutating `live[0][0] += elapsed` to
        `live[0][0] = elapsed` — **all seven rows stayed green**, so the summation
        was untested and "the median of the last call's duration" would have shipped
        under a label promising the whole walk.

        **The two compiles are made deliberately unequal, and the longer one
        first.** A uniform step makes the arithmetic exact without pinning how many
        times the recorder reads the clock, which is the brittleness this plan
        rejected in round 3. Unequal totals are what separate a median over
        *compiles* from one over *calls*, which the earlier fixture — whose compiles
        were identical — could not, and putting the longer sum first is what
        separates either from a recorder that kept only the last compile. `_median`
        takes the upper of two (`values[len(values) // 2]`), so three calls and then
        two reads 30 µs.

        **The two counts and the expected 30 µs are written out, not derived from the
        script log or from `_median`.** Deriving them would make the row agree with
        the recorder by construction — a wrong `_median` would move both sides, and a
        change that reversed the two compiles would go unnoticed. Written out, the
        log assertion fails loudly instead. It is deliberately coupled to how many
        compiles the earlier passes make, so a change there has to be looked at
        rather than absorbed.

        Three mutations die here: `=` for `+=` reads one step, a median over the five
        individual calls reads one step, and keeping only the last compile reads 20 µs.
        """
        step_us = 10.0
        engine = Engine()
        with _scripted(engine, counts=[3, 2], step_us=step_us) as scripted:
            report = compile_profile.decompose("{{ a }}", engine, repeats=2)

        self.assertEqual(
            scripted.log[-2:], [3, 2],
            "the recorder's two compiles are not the three-call-then-two-call pair "
            "this row is written against: an earlier pass changed how many compiles "
            "it makes, and the expectation below has to be re-derived",
        )
        self.assertEqual(report["compiles"], 2)
        self.assertEqual(report["samples"], 5)
        self.assertAlmostEqual(
            report["materialise"], 30.0, places=3,
            msg="the walk's durations were not summed per compile and then "
                "medianed: `=` for `+=`, a median over the individual calls, or "
                "keeping only the last compile each read a different number here",
        )

    def test_the_switch_is_restored_after_an_exception(self):
        # **Read through `getattr`, because this row runs before the constant
        # exists.** Task 3 is what adds `USE_NATIVE_MATERIALISER` to `parser.py`;
        # a bare attribute read here would make this file fail to run at all until
        # then, which is the same mistake `_on_path` is written to avoid.
        before = getattr(parser_mod, "USE_NATIVE_MATERIALISER", None)
        had = hasattr(parser_mod, "USE_NATIVE_MATERIALISER")
        real_compile = compile_profile._compile
        compile_profile._compile = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with self.assertRaises(RuntimeError):
                compile_profile.decompose("{{ a }}", Engine(), repeats=1)
        finally:
            compile_profile._compile = real_compile
        if had:
            self.assertEqual(parser_mod.USE_NATIVE_MATERIALISER, before)
        else:
            self.assertFalse(hasattr(parser_mod, "USE_NATIVE_MATERIALISER"))

    def test_the_materialise_patch_is_restored_after_an_exception(self):
        """The other restore, and the one the row above does **not** reach.

        That row raises on the first `_compile`, which is the warm call on the
        native branch — before `_python_pass` has installed either patch. So it
        proved `_on_path`'s `finally` and said nothing about the two
        `parser_mod.materialise = …` assignments. This one raises **only while a
        patch is installed**, so the failure lands inside `_python_pass`'s own
        `try`, and a missing `finally` there would leave the suite running against
        a stub for every test that followed.
        """
        real_materialise = parser_mod.materialise
        real_compile = compile_profile._compile

        def boom(*a, **k):
            if parser_mod.materialise is not real_materialise:
                raise RuntimeError("boom")
            return real_compile(*a, **k)

        compile_profile._compile = boom
        try:
            with self.assertRaises(RuntimeError):
                compile_profile.decompose("{{ a }}", Engine(), repeats=2)
        finally:
            compile_profile._compile = real_compile
        self.assertIs(
            parser_mod.materialise, real_materialise,
            "a failed pass left `materialise` patched for every later test",
        )

    def test_the_switch_is_restored_when_it_started_absent(self):
        # The other half: `getattr(…, None)` reads a *missing* constant as off, and
        # the `finally` must put absence back rather than leaving a `False` behind —
        # a test that set it `False` would then be testing a different module than
        # the one it started with.
        #
        # Before Task 3 this starts out true for free, since nothing defines the
        # constant yet; from Task 3 it is the real case. Same row either way.
        had = hasattr(parser_mod, "USE_NATIVE_MATERIALISER")
        real = getattr(parser_mod, "USE_NATIVE_MATERIALISER", None)
        if had:
            del parser_mod.USE_NATIVE_MATERIALISER
        try:
            compile_profile.decompose("{{ a }}", Engine(), repeats=2)
            self.assertFalse(hasattr(parser_mod, "USE_NATIVE_MATERIALISER"))
        finally:
            if had:
                parser_mod.USE_NATIVE_MATERIALISER = real


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_unit_tests.sh tests.test_compile_profile -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'compile_profile'`

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python
"""Where `_native.compile_template`'s time goes, on both materialiser paths.

    source scripts/env.sh
    maturin develop --release
    .venv/bin/python -I scripts/compile_profile.py            # every bench row
    .venv/bin/python -I scripts/compile_profile.py large      # one row

`scripts/bench.py` answers "how fast is the engine" and reports one number per
mode. This answers "which part of a compile is the part to work on", and it needs
a second measurement the bench does not take: the same compile with
`parser.materialise` replaced by a stub returning an empty `NodeList`.

    native      `compile_template` with the Rust walk selected, **uninstrumented**.
    python      the same call with the Python walk selected, **uninstrumented**.
                `python - native` is what S1 is worth, and it is the number the
                decision is made from.
    stubbed     the Python path with `materialise` patched out: **the whole compile
                with the walk stubbed, not "the compile minus the walk"**. It still
                allocates a real `NodeList`, still runs both planner checks, still
                cleans up the wire and still builds the `Template`. `plan_list`
                returns `None` for a root with nodes — the stub's `NodeList` has the
                wrong length — and `plan_block_bodies` then returns immediately; for
                an **empty** source the lengths match and an empty plan is built. The
                stub's `contains_nontext` controls neither decision. And because
                `{% partialdef %}` materialises its body during the *parse*, the stub
                changes those bodies and their parse-time state too.
    materialise the **outermost** `materialise` calls, **summed per compile**, so the
                walk is its own number rather than a difference of two totals.

    samples     how many outermost `materialise` calls the recorder saw, **across all
                compiles**. This is not one per compile: a body the parser builds
                during the parse — `{% partialdef %}`'s is one — is materialised by
                the host before the root is, so one compile can make several
                non-overlapping outermost calls. Both counts are reported, so a
                template in that shape says so rather than passing quietly.
    compiles    how many compiles the recorder opened. **One per compile is the
                contract, and it is `compiles` that carries it**, not `samples`.

**There is no fourth timing and no derived `plan`.** An earlier draft reported
`(python - stubbed) - materialise` as the plan's cost. It is not a measurement: the
term mixes two passes with a global patched against one pass with nothing patched,
the `stubbed` stub allocates a real `NodeList` that the unpatched run does not, and
the `_timed` wrapper adds a call per compile to the run it measures. The three errors
point in three directions at once, and no test can validate an attribution that is
really an arithmetic coincidence between independent medians. **The plan's cost cannot
be measured from here at all** — `plan_list` (`lib.rs:3918`) and `plan_block_bodies`
(`:4017`) are private functions inside `compile_template`, not exported to Python, so
the only Python-visible quantity containing them is `stubbed`, which also contains the
parse, the wire and `template_for`. Isolating the plan is S3's, which is the
sub-project that changes `plan_list` and can time it where it lives.

**`materialise` recurses, and the first version of this file got that wrong.**
`materialise_node` resolves each child through the same patched global, so a
wrapper that appended on every call recorded *every subtree* — hundreds of entries
on `large_template()`, whose median is the cost of a typical subtree and not of the
compile's materialisation at all. It looked like a number and was the wrong
quantity. Depth is tracked and only the outermost call is recorded; the *clock* is
read only on the outermost call too, so a nested call costs one list read rather than
a `perf_counter` — small, but it is a cost this tool adds to the thing it measures,
and it costs nothing to not add it.

**And an outermost call is still not a compile, which the second version got wrong.**
A body the parser builds during the parse is materialised by the host before the root
is, so one compile can make two or more non-overlapping outermost calls.
`{% partialdef p %}{{ a }}{% endpartialdef %}{% partial p %}` is the smallest such
template, and `repeats=2` recorded **four** calls where a per-compile count would be
two. A median over those four mixes a three-character partial body with a full root
and answers neither question. The recorder therefore attributes each outermost call to
the compile it belongs to and reports the **sum per compile**, and the median is over
those sums. `compiles` and `samples` are both reported, so a template that produces
more than one call per compile says so rather than passing quietly.

**Whether a test can see that second call turns on module identity, and that is why
no row in `tests/test_compile_profile.py` reads a walk count out of the engine.** The
parse-time body is materialised through `render.materialiser()`, which caches
`django_rust_templates.engine.parser` on its first call. Five test modules used to load
a fresh copy of that file *by path* and install it in `sys.modules` under the same name
— `test_materialise`, `test_filter_expr_parity`, `test_parser_parity`,
`test_variable_parity` and `test_ref_table` — so under `scripts/run_unit_tests.sh` the
cache could end up holding the copy while this file's `parser_mod` was the original:
`ShellHost.register_partial_def`'s call then landed on a module whose `materialise` was
never patched, and one call per compile is all the recorder saw. The same source
compiled by hand in a fresh process, before discovery caches anything, shows both.

**All five are ordinary imports now, and the measurement that recorded this reads the
other way.** `render.materialiser() is not compile_profile.parser_mod` was true after
the suite had run and `is` before it; it is `is` on **both** sides now, and
`sys.modules["django_rust_templates.engine.parser"]` is the object it was at import.
The loaders were vestigial, and identically so: every one of them justified itself by
`engine/__init__.py` being the shim's `django.template`, whose own `from .engine import
Engine` then failed — while `engine/__init__.py` itself says its name "is not
`django.template`", and `test_ref_table` imported the `engine` package by name two
lines above its own by-path load.

**None of that changes what this tool has to do** — group by compile and sum,
unconditionally — and it does not change the fixture decision either, though the reason
moves. A row that asked the engine how many calls it made would be measuring the
instrument with the instrument. The two rows that pin the grouping and the summation
script their own compile instead, through a real `materialise` argument tuple captured
from a real one.

**Only `native` and `python` are measured on every call**, by flipping the switch,
rather than once at import. After S1 ships the constant is `True`, and a tool that
read it once would print the native number in both columns: a saving of zero,
reported as a result. The flip is restored in a `finally`, including when the
constant started out absent.

**The two paths are interleaved and warmed, not run as two long blocks.** An earlier
draft ran `repeats` native compiles and then `repeats` python ones. Before S1 exists
the two are the *same code*, so any difference it printed was the machine — and it
printed one: the reviewer measured `native 1849.6 us` against `python 1742.3 us`, a
saving of −107.3 us from two identical paths. Whichever block ran second inherited
the load the first one caused. Each branch now gets one discarded warm call, and the
two are alternated round by round.

**`stubbed` and `materialise` are taken with the switch pinned to Python.** This is
not tidiness: patching `parser.materialise` does nothing on the native branch, so once
Task 8 flips the default the pass would run the Rust walk, record nothing, and raise
"the walk was never called". The branch is selected rather than inherited, and
`test_both_paths_are_measured_whatever_the_switch_says` is the row that keeps it
selected — **by spying on which branch the body actually observed, not by comparing
two timings.** A timing inequality would have passed on the −107.3 us run above.

**The two paths are timed with nothing instrumented, in separate passes.** Patching
`materialise` for the `stubbed` and `materialise` numbers changes what the other two
would measure, so they are taken before any patch is installed and the patch is
removed before the report is printed. **For the number that ships, take
`scripts/bench.py` on the same release build** — this tool's job is to explain a
saving that the bench has already measured, not to be the source of it.

**The boundary is `_native.compile_template`, and the bench's is `from_string`.**
They are not the same call. This file builds the `Parser` and the `ShellHost`
*before* the clock starts, because those are per-request setup and folding them into
the timed region would put them inside `stubbed` — whose whole meaning is "what is
left when the walk is gone". `bench.py`'s `compile` mode times `Engine.from_string`,
which is that setup plus this call, plus the `Template` and `Origin` construction at
the far end.

Measured on `real-world/large`, release build, medians of 25:

    from_string          1879.3 us
    compile_template     1761.2 us

**A difference of about 118 us, and "observed" is as far as that single pair
supports.** It is not a demonstrated fixed offset: the two tools warm differently and
this file's medians of individual calls on `normal` and `complex` read *higher* than
the bench's cells for the same rows, so the sign is not even stable across the corpus.
What survives is the qualitative claim — the setup sits on both paths, so it cancels
in the saving and does not cancel in the columns — and that is why the two tools may
be compared on `python - native` and not on the columns themselves. The bench's
`large compile` cell (1897.2 us) and this file's `python` column (1727.4 us) are the
same compile seen from either end of that setup, and neither is wrong.
"""

import sys
import time
from pathlib import Path

# Settings are configured in exactly one place in this tree, and this file is not
# it: `tests/test_settings_configuration.py` refuses a second `settings.configure`
# under `scripts/` or `tests/`, because it is a no-op once settings exist, so
# *which module runs first* would decide the values. `filter_probe` also inserts
# the pinned `.django` checkout on `sys.path`, which is what makes `import django`
# work at all under `-I` (the flag implies `-E`, so `PYTHONPATH` is ignored), and
# it drops this directory back on `sys.path` for `bench_corpus`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

from django_rust_templates import _native, render  # noqa: E402
from django_rust_templates.engine import parser as parser_mod  # noqa: E402
from django_rust_templates.engine.base import NodeList  # noqa: E402

UNKNOWN_ORIGIN = render.UNKNOWN_ORIGIN


def _fresh(source, engine):
    """A parser for one compile, built the way `render` builds one."""
    return render.parser_for(source, engine, UNKNOWN_ORIGIN)


def _host(parser):
    return render.ShellHost(parser)


def _compile(parser, host, source, engine):
    _native.compile_template(source, parser, parser_mod, engine, host, UNKNOWN_ORIGIN)


def _stub_materialise(nodelist_desc, origin=None, resolve_ref=None,
                      register_ref=None, parser=None):
    """A real but empty `NodeList`.

    For a root with nodes, `plan_list` then returns `None` on the length mismatch
    and `plan_block_bodies` returns immediately; for an empty source the lengths
    match and an empty plan is built. Either way this is a *stub*, not a removal:
    the allocation, both planner checks, the wire cleanup and the `Template` all
    still happen, which is why the column is labelled as it is.
    """
    nodelist = NodeList()
    nodelist.contains_nontext = True
    return nodelist


def _on_path(native, body):
    real = getattr(parser_mod, "USE_NATIVE_MATERIALISER", None)
    had = hasattr(parser_mod, "USE_NATIVE_MATERIALISER")
    parser_mod.USE_NATIVE_MATERIALISER = native
    try:
        return body()
    finally:
        # Restore *absence* as absence, not as `False`: a test that started with
        # the constant missing must end that way, or it has silently changed which
        # module the next test is looking at.
        if had:
            parser_mod.USE_NATIVE_MATERIALISER = real
        else:
            del parser_mod.USE_NATIVE_MATERIALISER


def _once(engine, source):
    """One compile on whichever branch is selected right now, in microseconds.

    The `Parser` and the `ShellHost` are built **before** the clock starts: they
    are per-request setup, and folding them in would put them inside `stubbed`.
    """
    parser = _fresh(source, engine)
    host = _host(parser)
    start = time.perf_counter()
    _compile(parser, host, source, engine)
    return (time.perf_counter() - start) * 1e6


def _median(values):
    values.sort()
    return values[len(values) // 2]


def _both_paths(engine, source, repeats):
    """`(native, python)` medians, warmed and interleaved.

    **Interleaved because the two paths are the same code until Task 3.** Two long
    blocks hand whichever runs second the load the first one caused, and on an idle
    machine that is invisible; the reviewer measured −107.3 µs of "saving" out of
    two paths that were byte-identical. Alternating round by round makes a drift in
    machine load land on both columns instead of on one.

    **Warmed because `compile_template` is not the first thing a process does.**
    One discarded call per branch pays the allocator's first-touch cost and the
    interpreter's lazy imports before anything is measured.
    """
    _on_path(True, lambda: _once(engine, source))
    _on_path(False, lambda: _once(engine, source))

    native, python = [], []
    for round_index in range(repeats):
        first, second = (True, False) if round_index % 2 == 0 else (False, True)
        for native_flag in (first, second):
            elapsed = _on_path(native_flag, lambda: _once(engine, source))
            (native if native_flag else python).append(elapsed)
    return _median(native), _median(python)


def decompose(source, engine, repeats=25):
    """Four timings in microseconds, and both counts of the walk.

    Nothing is instrumented while `native` and `python` are timed.
    """
    native, python = _both_paths(engine, source, repeats)

    real = parser_mod.materialise
    per_compile = []
    calls = [0]
    depth = [0]
    live = [None]

    def _attribute(elapsed):
        # **An outermost call is not a compile.** `{% partialdef %}` materialises
        # its body during the parse, so one compile can make several
        # non-overlapping outermost calls; a median over those mixes a partial body
        # with a full root. They are summed per compile and the median is over the
        # sums.
        calls[0] += 1
        if live[0] is None:
            live[0] = [0.0]
            per_compile.append(live[0])
        live[0][0] += elapsed

    def _timed(nodelist_desc, origin=None, resolve_ref=None, register_ref=None,
               parser=None):
        # **The nested case is not timed at all.** `materialise_node` resolves its
        # children through this same global, so this wrapper runs once per subtree;
        # only the outermost call is the walk. The clock is read on the outermost
        # call only, so a nested call costs one list index and nothing else.
        if depth[0]:
            depth[0] += 1
            try:
                return real(nodelist_desc, origin, resolve_ref, register_ref, parser)
            finally:
                depth[0] -= 1
        depth[0] = 1
        start = time.perf_counter()
        try:
            return real(nodelist_desc, origin, resolve_ref, register_ref, parser)
        finally:
            elapsed = (time.perf_counter() - start) * 1e6
            depth[0] = 0
            _attribute(elapsed)

    def _opening(parser, host, source, engine):
        # Opens a compile for the recorder. Called inside the clock, so it is one
        # list store rather than a `perf_counter`.
        live[0] = None
        _compile(parser, host, source, engine)

    def _python_pass():
        # **The branch is selected, not inherited.** Patching `materialise` is a
        # no-op on the native branch, so after Task 8 flips the default this pass
        # would run the Rust walk, record nothing, and raise below — a tool that
        # works in Task 1 and dies in Task 8.
        def run():
            parser_mod.materialise = _stub_materialise
            try:
                stubbed = _median([_once(engine, source) for _ in range(repeats)])
            finally:
                parser_mod.materialise = real

            parser_mod.materialise = _timed
            try:
                for _ in range(repeats):
                    parser = _fresh(source, engine)
                    host = _host(parser)
                    _opening(parser, host, source, engine)
            finally:
                parser_mod.materialise = real
            return stubbed

        stubbed = _on_path(False, run)

        if not per_compile:
            raise RuntimeError("the walk was never called; the patch did not take")
        return stubbed, _median([entry[0] for entry in per_compile]), calls[0], \
            len(per_compile)

    stubbed, materialise, samples, compiles = _python_pass()

    return {
        "native": native,
        "python": python,
        "stubbed": stubbed,
        "materialise": materialise,
        "samples": samples,
        "compiles": compiles,
    }


def report(source, label, engine, repeats=25):
    numbers = decompose(source, engine, repeats)
    saving = numbers["python"] - numbers["native"]
    print(f"{label}  ({len(source)} chars)")
    print(f"  native compile_template               {numbers['native']:9.1f} us")
    print(f"  python compile_template               {numbers['python']:9.1f} us")
    print(f"  S1 saving (python - native)           {saving:9.1f} us"
          f"   ({saving / numbers['python']:5.1%})")
    print(f"  materialise itself                    {numbers['materialise']:9.1f} us"
          f"   ({numbers['materialise'] / numbers['python']:5.1%} of python)")
    print(f"  compile with the walk stubbed         {numbers['stubbed']:9.1f} us")
    print(f"  (walk: {numbers['samples']} outermost call(s) over "
          f"{numbers['compiles']} compile(s) of {repeats})")
    print()


def main(argv):
    import bench_corpus

    from django_rust_templates.engine import Engine

    engine = Engine()
    wanted = argv[1] if len(argv) > 1 else None
    rows = [
        ("real-world/normal", bench_corpus.NORMAL),
        ("real-world/complex", bench_corpus.COMPLEX),
        ("real-world/large", bench_corpus.large_template()),
    ]
    for label, source in rows:
        if wanted and wanted not in label:
            continue
        report(source, label, engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

Before Task 3 the constant does not exist, and `_on_path` above already handles
that: it reads the constant with `getattr(…, None)` plus a `hasattr` flag and
restores **absence as absence**. That `getattr` stays after Task 3 rather than being
tightened, and the reason is the tool's job rather than laziness: `compile_profile`
is the one caller that has to run *before* the constant exists and *after*, so a bare
attribute read would make the baseline commit — the whole point of Task 1 — fail to
import. The Rust side is where a missing constant must be tolerated and a wrong one
must be loud, and Task 3 Step 4 is where that is written.

- [ ] **Step 4: Run the test to verify it passes**

Run: `scripts/run_unit_tests.sh tests.test_compile_profile -v`
Expected: PASS, with no skips and no rows reporting a zero-length run.

- [ ] **Step 5: Record the baseline — the bench's number, and the decoder's**

```bash
source scripts/env.sh && maturin develop --release
.venv/bin/python -I scripts/bench.py
.venv/bin/python -I scripts/compile_profile.py
```

**Both, and the bench's is the one that counts.** `compile_profile` patches two
module globals to split the phases; `bench.py` patches nothing. The first is how
the saving is *explained*; the second is how it is *claimed*, because an
instrumented number is a number about the instrument as well as about the engine.
Record both under `Baseline:` — and record the bench's `compile` mode figures for
`normal`, `complex` and `large` verbatim, since those are the only numbers a later
task may be compared against.

**The two tools do not time the same call, and the plan says so here rather than
leaving it to be rediscovered as a contradiction.** This file's boundary is
`_native.compile_template`, with the `Parser` and the `ShellHost` built outside the
clock; `bench.py`'s `compile` mode times `Engine.from_string`, which is that setup
plus this call. Measured on `real-world/large`, release build, medians of 25:

    from_string          1879.3 us
    compile_template     1761.2 us

**A difference of about 118 µs, and "observed" is as far as one pair supports.** The
code review was right to refuse the stronger claim: the two tools warm differently,
and this file's medians of individual calls read *higher* than the bench's cells on
`normal` and `complex`, so the sign is not even stable across the corpus. What
survives is the qualitative part — the setup sits on both paths, so it cancels in the
saving and does not cancel in the columns — and that is why the two tools may be
compared on `python - native` and not on the columns. The setup is kept outside the
clock deliberately: folding it in would put it inside `stubbed`, whose whole meaning
is "what is left when the walk is gone".

**The baseline, taken on this build.** The four columns and both counts, then the
bench's `compile` cells for the same three rows. **These are one run, and the
columns move ±2% between runs** — the `stubbed` and `materialise` figures drifted
2–4% across three consecutive runs while the two path medians moved by less. Only the
bench's row is a committed number.

    row                 native    python   stubbed   materialise   calls/compiles
    real-world/normal    160.5     162.7      62.3          60.8       25/25
    real-world/complex   107.2     107.3      43.6          39.7       25/25
    real-world/large    1769.2    1758.4     636.7         922.5       25/25

    bench.py compile mode, rust column:  normal 125.1   complex 90.6   large 1897.2

At this point `native` and `python` are the same code path, so **the saving column is
the instrument's noise floor and nothing else** — ±3 µs, or 2.0% of the smallest row.
Read it that way and not as a result. An earlier, unwarmed, order-fixed version of
this file read **−107.3 µs of saving out of two byte-identical paths** (the code
review's measurement, reproduced here before the fix), which is the whole reason
Step 1's branch row is a spy rather than a timing comparison.

**The spec's ~115 µs plan figure cannot be checked from here, and this step says so
rather than inventing a procedure.** `plan_list` (`lib.rs:3918`) and
`plan_block_bodies` (`:4017`) are private functions inside `compile_template`; they
are not exported to Python, so the only Python-visible quantity that contains the plan
is `stubbed`, which also contains the parse, the wire and `template_for`. Task 1
therefore records **three** numbers, all of them measured, and writes beside the spec's
figure: *unverified from Python — the candidate readings are ~115 µs (spec §1, method
unrecorded) and 25.5 µs (this plan's S1 probe, taken as a residual over `large`), and
they disagree by more than the quantity is worth.* Task 8 Step 5 carries that line
forward instead of quietly adopting either number. **Isolating the plan is S3's**, which
is the sub-project that changes `plan_list` and can time it where it lives.

- [ ] **Step 6: Commit**

```bash
git add scripts/compile_profile.py tests/test_compile_profile.py
git commit -m "perf: the compile decomposition, measuring both materialiser paths

Baseline (uninstrumented bench.py, compile mode):
<paste normal / complex / large here>
Baseline (compile_profile.py, instrumented):
<paste the four timings and the sample count here>
"
```

---

### Task 2: The fingerprint, and the proof it can fail

Parity is the only thing standing between this rewrite and a silently different
engine, so the oracle is built and falsified before the rewrite starts.

**One prerequisite, carried in from Task 1's round-3 review, and it turned out to be
a repair rather than a workaround — and a bigger one than the review first sized.**
**Five** modules loaded a **fresh copy** of
`python/django_rust_templates/engine/parser.py` by path and registered it in
`sys.modules` under the name it already had: `tests/test_materialise.py:53-59`,
`test_filter_expr_parity.py:52-57`, `test_parser_parity.py:97-103`,
`test_variable_parity.py:63-69` and `test_ref_table.py:82-88`. The reason all five gave
was that `engine/__init__.py` is a shim for `django.template`. There is no shim:
`django_rust_templates/__init__.py` says the package "does not impersonate Django's"
and that no module of Django's "is replaced, patched or shadowed at any point";
`engine/__init__.py` says its own name "is not `django.template`"; `render.py`'s
docstring already recorded the by-path route as obsolete; and `test_ref_table.py` and
`test_variable_parity.py` each imported the `engine` **package** by name a few lines
above their own by-path load, which is the very thing the load was there to avoid. The
second copy is the whole of Task 1's round-3 mystery: `render.materialiser()` caches
whatever `sys.modules` holds on its first call, so the cache and an ordinary import were
*different objects*, and an instrument patching one of them measured nothing while
reporting a number. Any **one** of the five was enough to keep that true, which is why
stopping at the two the review named would have left the hazard exactly where it was.
`test_filter_parity.py` and `test_escape_protocol.py` were named in that review too and
do **not** belong here — they load `scripts/filter_probe.py` under
`django_rust_templates.filter_probe`, a different module with a different name.

All five are ordinary imports now, so the hazard is gone at the source. Measured after
the whole suite has run: `render.materialiser() is compile_profile.parser_mod` is `True`
where it was `False`, and `sys.modules["django_rust_templates.engine.parser"]` is the
object it held at import. Task 2 is a process-wide instrument of exactly that shape, so
it binds the one module by ordinary import rather than inheriting whichever object won a
race — and the count that started all this is deterministic under every invocation.

**Files:**
- Create: `tests/materialiser_corpus.py`
- Create: `tests/test_materialiser_parity.py`
- Modify: `tests/test_materialise.py:7-13,16,53-59` and the `_register` helper at the
  foot of `MaterialiseTests` — the by-path loader becomes an ordinary import, the
  docstring is corrected, and the registry helper learns to restore rather than pop
  (Step 5)
- Modify: `tests/test_filter_expr_parity.py:28,47-58` — the same, so that there is one
  `parser` module in the process
- Modify: `tests/test_parser_parity.py:90-104` — the same, and `import importlib.util`
  goes with it
- Modify: `tests/test_variable_parity.py:23,59-69` — the same
- Modify: `tests/test_ref_table.py:61,82-88` — the same

**Interfaces:**
- Produces: `materialiser_corpus.SOURCES` — a list of `(label, source)`;
  `materialiser_corpus.LIBRARIES`; `materialiser_corpus.corpus_engine()`.
  `test_materialiser_parity.fingerprint(value)`; `_tree(source, engine=None,
  native=None)`; `with_native(body, enabled=True)`; `_collect(nodelist)`.
- Consumes: `django_rust_templates.engine.{base,smartif,engine}`, `_native`,
  `render`, `scripts/bench_corpus.py`.

- [ ] **Step 1: Write the corpus**

```python
"""Templates the materialiser is exercised on, and the check that they cover it.

Two families, and the second is the one that matters. The bench rows are the
real-world shapes; the tag templates are one per `{% tag %}` so that a class in
`NODE_CLASSES` with no template is a test failure rather than an untested arm.

The coverage check is a predicate over `NODE_CLASSES`, not a count. A count pins
churn — adding a tag would fail a number that means nothing — while "every entry
in the registry is produced by some source here" is the property that has to hold,
and it fails for exactly one reason.

**This module carries no `def test_` and no `TestCase`, and that is load-bearing.**
`tests/test_collected_tests.py::test_no_module_the_pattern_misses_holds_test_rows`
fails for any module outside the `test_*.py` pattern that holds a row, which is
how the tree catches a test file discovery drops. A corpus module with a row in it
would be that failure, correctly.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import bench_corpus  # noqa: E402

from tests.parser_corpus import LIBRARIES as _PARSER_LIBRARIES  # noqa: E402

# **The two libraries these rows reach, and the `i18n` path is borrowed rather
# than respelled.** `parser_corpus`'s own comment names the failure a second
# spelling causes ("two spellings is how a corpus stops grading the engine"), so
# the `i18n` entry is read from that map instead of duplicated here.
#
# **The rest of that map is deliberately *not* taken.** Its other entries are
# `template_tests.*`, which resolve only with `django_suite/` on `sys.path` —
# `test_parser_parity.py` arranges that at its own import, and without it
# `Engine(libraries=…)` raises `Invalid template library … No module named
# 'template_tests'` (measured). This corpus is about node classes, none of which
# needs those five, so it does not take on that path arrangement to carry them.
LIBRARIES = {
    "i18n": _PARSER_LIBRARIES["i18n"],
    # **These four are this package's transcriptions and not `django.templatetags.*`,
    # and the difference is a node class, not a spelling.** `parser.py` builds
    # `NODE_CLASSES` from `django_rust_templates.engine.templatetags.*`, so a
    # `{% load %}` naming *Django's* library hands the tag to a compile function
    # that builds *Django's* class instead — measured, as
    # `fingerprint fell through to an address-bearing repr for TimezoneNode:
    # '<django.templatetags.tz.TimezoneNode object at 0x…>'`, which is also how the
    # oracle caught it. `parser_corpus` reaches its `i18n` path the same way.
    "static": "django_rust_templates.engine.templatetags.static",
    "tz": "django_rust_templates.engine.templatetags.tz",
    "l10n": "django_rust_templates.engine.templatetags.l10n",
    "cache": "django_rust_templates.engine.templatetags.cache",
}

# One source per node class the registry carries. `{% cycle %}` appears twice on
# purpose: the second occurrence is the identity case, and a corpus whose only
# cycle template has one occurrence cannot see it.
TAG_SOURCES = [
    ("text", "hello {{ name }}"),
    ("if", "{% if a %}x{% elif b %}y{% else %}z{% endif %}"),
    ("if_nested", "{% if a %}{% if b %}n{% endif %}{% endif %}"),
    ("if_operators", "{% if a == b %}e{% endif %}{% if a != b %}n{% endif %}"),
    ("for", "{% for x in xs %}{{ x }}{% empty %}none{% endfor %}"),
    ("for_nested", "{% for x in xs %}{% for y in x %}{{ y }}{% endfor %}{% endfor %}"),
    # Django's `{% with %}` takes the kwarg form (`a=1 b=2`) or the `as` form
    # (`a as b`); `{% with total = a b %}` is neither, and dies at
    # `ParseError: 'with' received an invalid token: 'b'` (measured).
    ("with", "{% with a=1 b=2 %}{{ a }}{{ b }}{% endwith %}"),
    ("autoescape", "{% autoescape off %}{{ a }}{% endautoescape %}"),
    ("verbatim", "{% verbatim %}{{ not_a_var }}{% endverbatim %}"),
    ("cycle_twice", "{% cycle 'a' 'b' as c %}{% cycle c %}"),
    ("resetcycle", "{% cycle 'a' 'b' as c %}{% resetcycle c %}"),
    ("firstof", "{% firstof a b 'fallback' %}"),
    ("spaceless", "{% spaceless %} <b> a </b> {% endspaceless %}"),
    ("ifchanged", "{% for x in xs %}{% ifchanged %}{{ x }}{% endifchanged %}{% endfor %}"),
    ("templatetag", "{% templatetag openblock %}"),
    ("url_ish", "{% url 'view' a=1 b=2 %}"),
    # `{% blocktranslate %}` is `django.templatetags.i18n`'s and is **not a builtin**:
    # the `{% load %}` is load-bearing, not decoration. Measured — without it this
    # row dies at `ParseError: Invalid block tag on line 1: 'blocktranslate'`.
    ("blocktranslate", "{% load i18n %}{% blocktranslate %}Hello {{ name }}"
                       "{% endblocktranslate %}"),
    ("blocktranslate_plural", "{% load i18n %}{% blocktranslate count n=items %}"
                              "{{ n }} item{% plural %}{{ n }} items"
                              "{% endblocktranslate %}"),
    ("filter_args", "{{ a|default:'x' }} {{ b|join:', ' }}"),
    ("string_if_invalid", "{{ missing }}"),
    ("literal", "{{ 1.5 }} {{ 99999999999999999999 }} {{ True }} {{ None }}"),
    ("extends", "{% extends 'base.html' %}{% block b %}{{ a }}{% endblock %}"),
    ("include", "{% include 'p.html' with a=1 only %}"),
    # The twenty entries the coverage row named when only the rows above existed.
    # Each `{% load %}` is required: `template_libraries` is what makes a `{% load %}`
    # resolve at all, and these tags are not builtins.
    ("cache", "{% load cache %}{% cache 500 key %}x{% endcache %}"),
    ("csrf_token", "{% csrf_token %}"),
    ("debug", "{% debug %}"),
    ("filter", "{% filter upper %}x{% endfilter %}"),
    ("get_available_languages", "{% load i18n %}{% get_available_languages as langs %}"),
    ("get_current_language", "{% load i18n %}{% get_current_language as lang %}"),
    ("get_current_language_bidi", "{% load i18n %}{% get_current_language_bidi as bidi %}"),
    ("get_current_timezone", "{% load tz %}{% get_current_timezone as zone %}"),
    ("get_language_info", "{% load i18n %}{% get_language_info for 'en' as li %}"),
    ("get_language_info_list", "{% load i18n %}{% get_language_info_list for xs as infos %}"),
    ("language", "{% load i18n %}{% language 'en' %}x{% endlanguage %}"),
    ("localtime", "{% load tz %}{% localtime on %}x{% endlocaltime %}"),
    ("localize", "{% load l10n %}{% localize on %}x{% endlocalize %}"),
    ("lorem", "{% lorem 2 p %}"),
    ("now", "{% now 'Y' %}"),
    ("get_static_prefix", "{% load static %}{% get_static_prefix %}"),
    ("regroup", "{% regroup xs by g as grouped %}"),
    ("timezone", "{% load tz %}{% timezone 'UTC' %}x{% endtimezone %}"),
    ("translate", "{% load i18n %}{% translate 'x' %}"),
    ("widthratio", "{% widthratio a b 100 %}"),
    ("comment", "{% comment %}hidden{% endcomment %}"),
    ("load", "{% load static %}{% static 'x.css' %}"),
]

BENCH_SOURCES = [
    ("bench/normal", bench_corpus.NORMAL),
    ("bench/complex", bench_corpus.COMPLEX),
    ("bench/large", bench_corpus.large_template()),
]

SOURCES = TAG_SOURCES + BENCH_SOURCES


def corpus_engine():
    """The engine the parity rows parse through — the libraries belong to it.

    **On the engine and not in the source.** `{% load %}` resolves through
    `Engine.template_libraries`, so a bare `Engine()` raises `'static' is not a
    registered tag library` and `Invalid block tag on line 1: 'blocktranslate'`
    (both measured). Three `NODE_CLASSES` entries are reachable only through
    those two libraries, so the coverage row cannot be satisfied without this.

    A **fresh** engine per call, because an engine carries mutable filter and tag
    registries: one row that rebinds a filter must not be visible to the next.
    """
    from django_rust_templates.engine.engine import Engine

    return Engine(libraries=LIBRARIES)
```

- [ ] **Step 2: Write the harness**

```python
"""The Rust walk and the Python walk must build the same tree, and the same
objects where Django relies on identity.

`fingerprint` is the oracle for the tree. Its rules are the ones a review round
found the first version missing, and each is a shape where Python's `==` says
"same" about two things this engine must tell apart:

  * **primitives carry their type.** `True == 1 == 1.0` and `"x" == mark_safe("x")`
    in Python. A fingerprint returning raw values passes on all four wrong
    answers. `{% autoescape on %}` is a `bool`, a literal is an `int`, and a
    `{% verbatim %}` body is a `SafeString`.
  * **a real `dict` keeps its order.** `token_kwargs` gives a repeated key its
    first position and its last value; sorting erases the first half of that.
    A node's own `vars()` *is* sorted by name, because that order is CPython's
    and not a contract — the two are different questions and get different arms.
  * **an operator carries its id.** `a == b` and `a != b` are the same class:
    `infix`/`prefix` define `class Operator` inside the factory
    (`smartif.py:45-92`), so all thirteen are named `Operator` and none is a
    module attribute. The class is looked up in `smartif.OPERATORS`, which is
    the only table that can tell them apart.
  * **a class is carried whole, not by its name.** `type(value).__name__` calls a
    node a `TextNode` whether or not it is the one `NODE_CLASSES` holds, and two
    libraries can define one name between them. The class *object* is carried
    instead, so the comparison is exact and no address is printed. It is exact
    *for the classes this engine builds*: `type` on any node here is an ordinary
    `type`, and `==` between two of those is identity. A node whose class came
    from a metaclass that overrides `__eq__` would compare by that override and
    this arm would inherit its answer — no node in the registry does that today,
    and none is expected to, but the claim is about these classes and not about
    classes in general. Two classes the operator table does not name are told
    apart the same way.
  * **a `Literal` is not an operator, and the arm order is what makes that true.**
    `Literal` is a `TokenBase` with `lbp = 0`, and `TokenBase` carries `first` and
    `second` as `None`, so an operator arm placed above the literal arm swallowed
    every literal in the tree — `{% if a %}` and `{% if b %}` fingerprinted alike,
    value gone. `TemplateLiteral` subclasses `Literal`, so the whole `{% if %}`
    operand family was invisible, and `TemplateLiteral.eval` *resolves* its operand
    where `Literal.eval` returns it verbatim. The class and `text` both ride along.
  * **a callable carries a name**, because a filter function is not
    fingerprinted by address. `__qualname__` alone is not enough — two closures
    from one factory share it — so the callable itself is carried as the last
    element of the identity: `==` on two functions is identity, so the comparison
    is exact and no address is ever printed. `_callable_identity`'s docstring has
    the reasoning; `test_two_filters_with_one_qualname_are_told_apart` is the row.

**Every value in a tuple goes through `fingerprint` or through an arm that already
carries its type.** An earlier draft wrote `is_var` and `tuple(value.lookups)` into
its tuples raw, and `False == 0` and `True == 1` are exactly what the primitive arms
one screen down exist to prevent — the same collision, committed in the two arms that
had stopped calling `fingerprint` on their own contents. The `Variable` comment
claimed to be *preserving* the tuple shape while its `tuple(...)` call coerced a list
into one.

`Var`iables and `FilterExpression`s are fingerprinted by their slots, and
`_rust_names` is excluded: it is a lazily-filled cache of names derived from
`filters`, so it is a *consequence* of what is already compared.

Identities are asserted separately, in `CycleIdentityTests` and
`HostNodeIdentityTests`. A fingerprint cannot see them, and that is the point of
having both.
"""

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from tests import materialiser_corpus  # noqa: E402
from django_rust_templates import _native, render  # noqa: E402
from django_rust_templates.engine import smartif  # noqa: E402
from django_rust_templates.engine.base import (  # noqa: E402
    FilterExpression,
    Node,
    NodeList,
    Token,
    Variable,
)
from django_rust_templates.engine.engine import Engine  # noqa: E402
# **A plain import, and it matters.** An earlier draft loaded `parser.py` by path
# through `importlib.util.spec_from_file_location` and installed it into
# `sys.modules` under its own name. That produces a *second* module object: the
# one this file patches is then not the one `render`, the engine and `_CACHED`
# hold, so `with_native` flips a global nothing reads and every parity row passes
# by comparing the Python walk with itself. It is the same class of error as the
# stock-`django.template.Engine` mix-up that made an earlier probe report 7345 µs.
# There is exactly one `parser` module; import it. **Four other test modules had the
# same load and the same obsolete justification** — `test_materialise`,
# `test_filter_expr_parity`, `test_parser_parity`, `test_variable_parity`,
# `test_ref_table` — and all five were converted together, because one survivor is
# enough to re-install the second object. `test_materialise.py`'s docstring has the
# measurement.
from django_rust_templates.engine import parser as materialiser  # noqa: E402
from django_rust_templates.engine import library as library_mod  # noqa: E402
from django_rust_templates.engine import defaulttags  # noqa: E402
from django.utils.safestring import SafeString  # noqa: E402

_ADDRESS = re.compile(r"\bat 0x[0-9a-fA-F]+")

# **No skip list, and that is a decision rather than an omission.** Nothing in a
# node's `vars()` is dropped: `origin` is compared because it is what the annotation
# protocol matches on, so a walk that lost it would build a tree that never annotates,
# and `token` is compared for the message a `TemplateSyntaxError` would carry. An
# earlier draft carried a `_SKIPPED_NODE_ATTRS = frozenset()` beside these lines: an
# exclusion that excluded nothing, which reads as a policy and holds none.


def _callable_identity(func):
    """A filter function, named *and* held.

    **The name alone is not enough, and the review is why.** `__module__` plus
    `__qualname__` equates two *different* closures produced by the same factory —
    reachable whenever a library rebinds a filter mid-parse — and the fingerprint
    would then call two trees identical while `{{ a|f }}` renders differently in
    each. The callable itself rides as the last element: `==` on two functions is
    identity, so the comparison is exact, and no address is ever *printed* — which
    is what `fingerprint`'s guard refuses, not what it compares.
    """
    return [
        "callable",
        getattr(func, "__module__", None),
        getattr(func, "__qualname__", None) or getattr(func, "__name__", None),
        func,
    ]


def _operator_id(operator):
    """Which `smartif.OPERATORS` entry built this, by class identity.

    The classes are defined inside the `infix`/`prefix` factories, so they are
    all named `Operator` and none is a module attribute. The table is the only
    place they are reachable from, and without this `a == b` and `a != b`
    fingerprint identically — two conditions that evaluate differently on every
    input.
    """
    for key, factory in smartif.OPERATORS.items():
        if type(operator) is factory:
            return key
    return None


def fingerprint(value):
    """A structural description of a materialised tree.

    Raises rather than returns when it falls through to `repr` of something
    carrying an address: a fingerprint that *contains* an address compares
    unequal for a reason that has nothing to do with the two trees, which turns
    a real divergence and a memory layout into the same red row. The project has
    lost to this once — `describe()`'s dispatch falling to its memory-address arm
    measured 537 order-dependent failures in the parser harness.
    """
    if isinstance(value, NodeList):
        # `contains_nontext` through `fingerprint` rather than `bool(...)`: the flag is
        # a `bool`, and `bool(1) is True` is the normalisation the primitive arms exist
        # to prevent. Three fields in this file had it — this one, `FilterExpression`'s
        # `is_var` and `Variable`'s `translate` — and each was invisible to a mutation
        # that swapped the boolean for its equal integer.
        return [
            "nodelist",
            fingerprint(value.contains_nontext),
            [fingerprint(n) for n in value],
        ]
    if isinstance(value, Node):
        # **The class object, not its name.** `type(value).__name__` calls two
        # different classes "TextNode" — reachable from a library that defines a
        # node under a name another library already uses — and the two render
        # differently. Same trick as `_callable_identity`: the object itself rides
        # in the tuple, and for a class built by plain `type` the `==` the
        # comparison eventually runs is identity, so no address is printed. See the
        # rule list in the module docstring for where that guarantee stops.
        return [
            type(value),
            sorted(
                ((name, fingerprint(attr)) for name, attr in vars(value).items()),
                key=lambda pair: pair[0],
            ),
        ]
    if isinstance(value, Token):
        return ["token", value.token_type.name, value.contents, value.position, value.lineno]
    if isinstance(value, FilterExpression):
        return [
            "filterexpr",
            # `filters` is `[(filter_func, args)]` and `args` is
            # `[(is_var, operand)]` (`base.py:768-781`). Two elements per pair,
            # not three: an earlier draft unpacked three names and would have
            # raised on every `{{ a|default:'x' }}` in the corpus. And `is_var`
            # goes through `fingerprint` rather than into the list bare, because
            # it is a `bool` and `True == 1` — an earlier draft compared a
            # literal-argument filter against a variable-argument one as equal.
            [
                [
                    _callable_identity(func),
                    [[fingerprint(is_var), fingerprint(arg)] for is_var, arg in args],
                ]
                for func, args in value.filters
            ],
            value.token,
            fingerprint(value.is_var),
            fingerprint(value.var),
        ]
    if isinstance(value, Variable):
        return [
            "variable",
            value.var,
            # **`lookups` through `fingerprint`, not `tuple(...)`, and the
            # difference is the point.** `Variable.__init__` splits the name with
            # `tuple(var.split(...))`, so a *tuple* is what the parser produces and
            # the type is a shape the render walk reads — which is what an earlier
            # draft's comment claimed while its `tuple(...)` call did the opposite:
            # it coerced a list into a tuple and made the two compare equal. Passing
            # the value through keeps the shape and reports it.
            fingerprint(value.lookups),
            fingerprint(value.literal),
            fingerprint(value.translate),
            value.message_context,
        ]
    if isinstance(value, smartif.Literal):
        # **This arm has to come before the operator arm, and that ordering is a
        # review finding.** `Literal` is a `TokenBase` with `lbp = 0`, and
        # `TokenBase` carries `first` and `second` as `None`, so the operator arm
        # below used to swallow every literal in the tree: `smartif.Literal("x")`
        # fingerprinted as `['op', None, 0, ['none'], ['none']]` — the value gone,
        # and a literal indistinguishable from an operator class `_operator_id`
        # could not name. `TemplateLiteral` is a `Literal` subclass, so the whole
        # `{% if %}` operand family was invisible.
        #
        # `text` and the class are both carried: `TemplateLiteral.eval` resolves
        # `value`, while `Literal.eval` returns it verbatim, so `a=None` gives
        # `False` for one and `True` for the other, and `text` is what
        # `display()` shows in an error message.
        return [
            "literal",
            type(value),
            fingerprint(value.value),
            fingerprint(getattr(value, "text", None)),
        ]
    if isinstance(value, smartif.TokenBase) and hasattr(value, "lbp"):
        # An operator, infix or prefix. `second is None` is the prefix shape and
        # is part of the fingerprint because it is part of the class. The class
        # rides along with `_operator_id`'s key so that two classes the table does
        # not name are still told apart.
        #
        # **An unnamed class is loud rather than silent.** `TokenBase` carries `first`
        # and `second` as `None`, so an unnamed subclass reaching this arm is read as an
        # operandless operator — its real operands never compared. `Literal` and
        # `TemplateLiteral` used to land here; `EndToken`, a parser sentinel that does
        # not appear in a tree, is the only remaining subclass this arm can claim.
        operator_id = _operator_id(value)
        if operator_id is None:
            raise AssertionError(
                f"{type(value).__name__} reached the operator arm and smartif.OPERATORS "
                f"does not name it, so `first`/`second` may not be its operands. "
                f"Give it an arm above this one, or name the class."
            )
        return [
            "op",
            type(value),
            operator_id,
            value.lbp,
            fingerprint(value.first),
            fingerprint(value.second),
        ]
    if isinstance(value, dict):
        # Insertion order, and not sorted: a `dict` here is `token_kwargs`'
        # result, where a repeated key keeps its first position.
        return ["dict", [[key, fingerprint(item)] for key, item in value.items()]]
    if isinstance(value, (list, tuple)):
        return [type(value).__name__, [fingerprint(item) for item in value]]
    # Primitives carry their type, and the subclass checks come first: `bool` is
    # an `int`, `SafeString` is a `str`, and a fingerprint that returned the bare
    # value would call `True`, `1` and `1.0` the same tree.
    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, SafeString):
        return ["safe", str(value)]
    if isinstance(value, int):
        return ["int", value]
    if isinstance(value, float):
        return ["float", value]
    if isinstance(value, str):
        return ["str", value]
    rendered = repr(value)
    if _ADDRESS.search(rendered):
        raise AssertionError(
            f"fingerprint fell through to an address-bearing repr for "
            f"{type(value).__name__}: {rendered!r}. Add an arm for it."
        )
    return [type(value).__name__, rendered]


def _tree(source, engine=None, native=None):
    """The materialised `NodeList` for `source`.

    **Through `_native.compile_template` and not through `materialise`
    directly**, because that is the only Python-reachable entry point holding the
    *Rust* `NodeListDesc` the native walk takes. There is no way to hand a
    `NodeListDesc` across the boundary, so the alternative would be a test-only
    API on the shipped extension. Driving the real entry point is stronger
    anyway: it is the object a request actually builds.

    `native=None` means **leave the switch alone** — the module default, whatever
    Task 8 leaves it at. Passing `False` explicitly is how the parity row asks
    for the Python path, and a default of `False` would mean the module default
    could never select native in any test.
    """
    engine = engine or materialiser_corpus.corpus_engine()
    parser = render.parser_for(source, engine, render.UNKNOWN_ORIGIN)
    host = render.ShellHost(parser)
    compile_it = lambda: _native.compile_template(  # noqa: E731
        source, parser, materialiser, engine, host, render.UNKNOWN_ORIGIN
    )
    template = compile_it() if native is None else with_native(compile_it, enabled=native)
    return template.nodelist


def with_native(body, enabled=True):
    """Run `body` with a named branch selected, and restore the switch after.

    The constant is the *default*; a test that needs a specific branch sets it
    explicitly rather than depending on what Task 8 leaves it at, so every test
    here keeps meaning the same thing on both sides of the flip.

    **Read through `getattr` and restored as absence, because this file runs
    before Task 3 adds the constant.** Task 3 appends `USE_NATIVE_MATERIALISER`
    to `parser.py`; a bare read here would make this whole module fail to import
    until then, which is the same defect `compile_profile._on_path` is written to
    avoid. Setting an attribute nothing reads yet is harmless — `compile_template`
    ignores the name until Task 3's call site consults it — so the parity row is
    genuinely green on both sides of the flip rather than skipped.
    """
    real = getattr(materialiser, "USE_NATIVE_MATERIALISER", None)
    had = hasattr(materialiser, "USE_NATIVE_MATERIALISER")
    materialiser.USE_NATIVE_MATERIALISER = enabled
    try:
        return body()
    finally:
        if had:
            materialiser.USE_NATIVE_MATERIALISER = real
        else:
            del materialiser.USE_NATIVE_MATERIALISER


def _collect(nodelist):
    """Every node in a tree, including the ones inside `{% if %}`'s pairs.

    `IfNode` inherits `child_nodelists == ('nodelist',)` from `Node` while
    `nodelist` is a computed property returning `NodeList(self)`, so an
    `IfNode`'s children are reached through `conditions_nodelists` and not
    through the inherited name. Both are walked here.
    """
    for node in nodelist:
        yield node
        children = [getattr(node, name, None) for name in node.child_nodelists]
        for pair in getattr(node, "conditions_nodelists", None) or ():
            if isinstance(pair, tuple):
                children.append(pair[1])
        for child in children:
            if isinstance(child, NodeList):
                yield from _collect(child)


```

- [ ] **Step 3: Write the sensitivity rows**

```python
class FingerprintSensitivityTests(unittest.TestCase):
    """Mutations the oracle must see, each one a shape Python calls equal.

    A structural oracle that cannot fail is a green light with no bulb in it, and
    the failure is silent: every later task passes and the tree is wrong. Most of
    these rows are review findings, one per review round.
    """

    def test_a_changed_text_is_seen(self):
        self.assertNotEqual(fingerprint(_tree("hello")), fingerprint(_tree("goodbye")))

    def test_a_reordered_nodelist_is_seen(self):
        self.assertNotEqual(
            fingerprint(_tree("{{ a }}{{ b }}")), fingerprint(_tree("{{ b }}{{ a }}"))
        )

    def test_true_and_one_are_not_the_same_tree(self):
        # `True == 1` in Python, and `{% autoescape on %}`'s `setting` is a
        # `bool` while a literal `1` is an `int`. A fingerprint that returned
        # raw values would pass on the wrong one.
        self.assertNotEqual(fingerprint(True), fingerprint(1))
        self.assertNotEqual(fingerprint(1), fingerprint(1.0))

    def test_a_safe_string_is_not_a_plain_string(self):
        # `mark_safe("x") == "x"`. A `{% verbatim %}` body's value is a
        # `SafeString`, and returning it as a `str` would pass while making the
        # node escape its own literal.
        from django.utils.safestring import mark_safe

        self.assertNotEqual(fingerprint(mark_safe("x")), fingerprint("x"))

    def test_two_operators_with_one_class_name_are_told_apart(self):
        # The review's finding: every operator is `class Operator`, defined
        # inside its factory, so class *name* cannot distinguish them and the
        # class is not a module attribute either.
        equal = _tree("{% if a == b %}x{% endif %}")[0].conditions_nodelists[0][0]
        differs = _tree("{% if a != b %}x{% endif %}")[0].conditions_nodelists[0][0]
        self.assertEqual(type(equal).__name__, type(differs).__name__)
        self.assertNotEqual(fingerprint(equal), fingerprint(differs))

    def test_a_literal_carries_its_value(self):
        # **A dead arm, and this row is what caught it.** `Literal` is a
        # `TokenBase` with `lbp = 0`, and `TokenBase` carries `first` and `second`
        # as `None`, so the operator arm claimed every literal before the literal
        # arm below it could run: `{% if a %}` and `{% if b %}` — whose condition
        # *is* a `TemplateLiteral`, with no operator at all — fingerprinted
        # identically, as `['op', None, 0, ['none'], ['none']]`.
        self.assertNotEqual(
            fingerprint(smartif.Literal("x")), fingerprint(smartif.Literal("y"))
        )
        self.assertNotEqual(fingerprint(smartif.Literal("x")), fingerprint(smartif.Literal(1)))
        # The **condition**, and not the whole tree. A tree-level version of this
        # row passed against the broken fingerprint for an unrelated reason: an
        # `IfNode` carries its own `{% if a %}` token, whose contents differ, so the
        # two trees differed while the two literals did not.
        self.assertNotEqual(
            fingerprint(_tree("{% if a %}x{% endif %}")[0].conditions_nodelists[0][0]),
            fingerprint(_tree("{% if b %}x{% endif %}")[0].conditions_nodelists[0][0]),
        )

    def test_a_template_literal_is_not_a_bare_literal(self):
        # The review's repro, and the two evaluate differently rather than merely
        # looking different. `TemplateLiteral.eval` resolves its operand against
        # the context; `Literal.eval` returns it verbatim (`smartif.py:139-140`,
        # `defaulttags.py:917-918`). With `a=None` one condition is false and the
        # other is true, and an earlier fingerprint called them the same tree.
        expression = _tree("{{ a }}")[0].filter_expression
        template_literal = defaulttags.TemplateLiteral(expression, "a")
        plain = smartif.Literal(expression)
        self.assertNotEqual(fingerprint(template_literal), fingerprint(plain))

    def test_two_literals_with_different_source_text_are_told_apart(self):
        # `text` is what `TemplateLiteral.display()` shows in an error message, and
        # it is not derivable from `value`: `{% if a %}` and `{% if a %}` written
        # around different spellings of one operand carry the same expression.
        expression = _tree("{{ a }}")[0].filter_expression
        self.assertNotEqual(
            fingerprint(defaulttags.TemplateLiteral(expression, "a")),
            fingerprint(defaulttags.TemplateLiteral(expression, "a.b")),
        )

    def test_two_node_classes_with_one_name_are_told_apart(self):
        # A node class is not identified by its name. Two libraries can each define
        # a node called `TextNode`, they render differently, and `NODE_CLASSES` — a
        # dict keyed by name — is where the collision surfaces.
        class First(Node):
            def __init__(self):
                self.s = "x"

        Second = type("First", (Node,), {"__init__": First.__init__})
        self.assertEqual(type(First()).__name__, type(Second()).__name__)
        self.assertIsNot(type(First()), type(Second()))
        self.assertNotEqual(fingerprint(First()), fingerprint(Second()))

    def test_a_list_lookups_is_not_a_tuple_lookups(self):
        # `Variable.__init__` builds a tuple, and the render walk reads that shape.
        # An earlier fingerprint wrote `tuple(value.lookups)`, which coerced a list
        # into a tuple and made the two compare equal — normalising away the very
        # difference its own comment claimed to be protecting.
        variable = Variable("a.b")
        self.assertEqual(variable.lookups, ("a", "b"))
        self.assertIsInstance(variable.lookups, tuple)
        coerced = Variable("a.b")
        coerced.lookups = list(coerced.lookups)
        self.assertNotEqual(fingerprint(Variable("a.b")), fingerprint(coerced))

    def test_a_literal_filter_argument_is_not_a_variable_one(self):
        # `FilterExpression.filters` holds `(func, [(is_var, operand)])` and `is_var`
        # is a `bool`. `False == 0` and `True == 1`, so an earlier fingerprint put a
        # literal argument and a variable argument on the same side of the
        # comparison — the same collision the primitive arms exist to prevent,
        # committed in the one arm that wrote a raw value into the tuple instead of
        # calling `fingerprint` on it.
        #
        # **Both directions, and the colliding value in each.** A first draft mutated
        # `False` to `1`, which does not collide with anything and so passed against
        # the broken fingerprint — a row that goes green for the wrong reason.
        for source, is_var, collision in (
            ("{{ a|default:'x' }}", False, 0),
            ("{{ a|default:b }}", True, 1),
        ):
            with self.subTest(source=source):
                node = _tree(source)[0]
                before = fingerprint(node)
                func, args = node.filter_expression.filters[0]
                self.assertIs(type(args[0][0]), bool)
                self.assertIs(args[0][0], is_var)
                node.filter_expression.filters[0] = (func, [(collision, args[0][1])])
                self.assertNotEqual(before, fingerprint(node))

    def test_a_swapped_child_between_two_parents_is_seen(self):
        whole = _tree("{% if a %}x{% endif %}{% if b %}y{% endif %}")
        before = fingerprint(whole)
        whole[0].conditions_nodelists[0][1][0].s = "y"
        whole[1].conditions_nodelists[0][1][0].s = "x"
        self.assertNotEqual(before, fingerprint(whole))

    def test_a_dropped_token_is_seen(self):
        tree = _tree("{% if a %}{{ b }}{% endif %}")
        before = fingerprint(tree)
        tree[0].token = None
        self.assertNotEqual(before, fingerprint(tree))

    def test_a_dropped_origin_is_seen(self):
        tree = _tree("{% if a %}{{ b }}{% endif %}")
        before = fingerprint(tree)
        tree[0].origin = None
        self.assertNotEqual(before, fingerprint(tree))

    def test_the_typed_flags_are_not_normalised_by_bool(self):
        """Three fields that read a bare `bool(...)` and so could not see `1`.

        `bool(1) is True`, so a fingerprint that wraps a flag in `bool()` reports the
        same value whether the walk produced `True` or `1`, and the arms one screen
        down that exist to tell exactly those apart never see the field. Found by
        mutating each flag to its equal integer and getting an unchanged
        fingerprint — three rows' worth of hidden green.
        """
        nodelist = _tree("hello")
        self.assertIs(nodelist.contains_nontext, False)
        nodelist.contains_nontext = 0
        self.assertNotEqual(fingerprint(_tree("hello")), fingerprint(nodelist))

        # Both reachable values of the field, and **not** because one alone would
        # miss the mutation: `bool()` collapses `False`/`0` *and* `True`/`1`, so
        # either direction already kills it. Running both is coverage of the field
        # rather than of the regression — `is_var` is `True` for `{{ a }}` and
        # `False` for `{{ "x" }}`, and a row that only walked one of those was
        # asserting about half of what the arm carries.
        is_var = _tree('{{ "x" }}')[0]
        self.assertIs(is_var.filter_expression.is_var, False)
        before = fingerprint(is_var)
        is_var.filter_expression.is_var = 0
        self.assertNotEqual(before, fingerprint(is_var))

        is_a_var = _tree("{{ a }}")[0]
        self.assertIs(is_a_var.filter_expression.is_var, True)
        before = fingerprint(is_a_var)
        is_a_var.filter_expression.is_var = 1
        self.assertNotEqual(before, fingerprint(is_a_var))

    def test_a_translate_flag_is_not_an_integer(self):
        # `Variable.translate` is set by `_('…')` and drives a `gettext` call at
        # render time, so a `1` where a `True` belongs is a different node.
        plain = Variable("a")
        self.assertIs(plain.translate, False)
        translated = Variable("a")
        translated.translate = 0
        self.assertNotEqual(fingerprint(plain), fingerprint(translated))

    def test_an_operator_class_the_table_does_not_name_is_loud(self):
        # `TokenBase` carries `first` and `second` as `None`, so an unnamed operator
        # class would be fingerprinted as an operandless operator with its real
        # operands never compared. `Literal` and `TemplateLiteral` were exactly that
        # until they got an arm of their own; this makes the next one fail loudly
        # instead of quietly.
        class Rogue(smartif.TokenBase):
            lbp = 3

            def __init__(self):
                self.first = None
                self.second = None

        with self.assertRaises(AssertionError) as caught:
            fingerprint(Rogue())
        self.assertIn("OPERATORS", str(caught.exception))

    def test_two_filters_with_one_qualname_are_told_apart(self):
        # The collision a name-only identity cannot see, and it is reachable in
        # production: a library that rebinds a filter mid-parse leaves two
        # *different* closures under one module and one qualname.
        #
        # A **rebinding sandwich**, because a row that only compared two
        # fingerprints would pass if the factory returned the same object twice —
        # which is not the case here and is what the `assertIsNot` rules out. It
        # asserts the collision is real (`assertEqual` on the identity) before
        # asserting the fingerprints are not, so a row that went green for the
        # wrong reason is a failure rather than a pass.
        def make(marker):
            def filter_fn(value):
                return f"{marker}{value}"

            return filter_fn

        first = make("a")
        second = make("a")
        self.assertIsNot(first, second)
        self.assertEqual(
            _callable_identity(first)[:3], _callable_identity(second)[:3],
            "the fixture is not a collision: the names already differ",
        )

        # A filter library registered on the engine is visible to the parse
        # (`python/django_rust_templates/render.py:345`, where `parser_for` hands
        # `engine.template_builtins` to Django's `Parser`), which is the only way
        # to put a closure where the compiler will find it. The engine's own
        # `defaultfilters` are *not* mutated: that would leak a `rebound` filter
        # into every later test in the process.
        engine = Engine()
        engine.template_builtins.append(library_mod.Library())
        library = engine.template_builtins[-1]

        library.filters["rebound"] = first
        before = fingerprint(_tree("{{ a|rebound }}", engine))
        library.filters["rebound"] = second
        after = fingerprint(_tree("{{ a|rebound }}", engine))
        self.assertNotEqual(before, after)

    def test_the_fingerprint_refuses_an_address_bearing_repr(self):
        class Opaque:
            __slots__ = ()

        with self.assertRaises(AssertionError):
            fingerprint(Opaque())


```

- [ ] **Step 4: Write the parity and coverage rows**

```python
class TwoPathParityTests(unittest.TestCase):
    """Every corpus source, fingerprinted through both branches of the switch.

    **Two different branches, explicitly.** An earlier draft compared
    `_tree(source)` with `_tree(source)` — the Python path with itself, which
    passes for a trivial reason and would go on passing after every task in this
    plan. The two calls below name their branch.
    """

    def test_the_two_paths_agree_on_every_corpus_source(self):
        for label, source in materialiser_corpus.SOURCES:
            with self.subTest(source=label):
                self.assertEqual(
                    fingerprint(_tree(source, native=False)),
                    fingerprint(_tree(source, native=True)),
                )

    def test_every_registry_entry_is_exercised_by_the_corpus(self):
        # A structural check and not a count: a count pins churn, while this
        # fails for exactly one reason — a node class no template here builds.
        #
        # **No exclusion set, and it was not obvious.** An earlier draft carved
        # out `{"HostNode", "TemplateNode"}`, on the reasoning that a reserved name
        # and the root class could not be built. Both are in fact absent from
        # `NODE_CLASSES` — measured, `HostNode in NODE_CLASSES` is `False`, and
        # `parser.py` says why in as many words: it "is not a class", it is "a
        # reserved *name*". So the carve-out excluded nothing, and an exclusion
        # that cannot fire is the same green bulb as no check at all. Deleting it
        # leaves a predicate with no exceptions in it.
        built = set()
        for _label, source in materialiser_corpus.SOURCES:
            for node in _collect(_tree(source)):
                built.add(type(node).__name__)
        unexercised = sorted(
            name for name in materialiser.NODE_CLASSES if name not in built
        )
        self.assertEqual(
            unexercised,
            [],
            "add a template to materialiser_corpus.TAG_SOURCES for each of these",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: Make the mutation helpers restore the registry**

`tests/test_materialise.py` needs a node class registered to exercise the walk, and the
registry it registers into is now the process's own — the loader repair in Step 1 is what
made that true. That changes what a cleanup owes.

```python
class MaterialiseTests(unittest.TestCase):
    def _register(self, name, cls):
        """Register a node class, and put the registry back **exactly** as it was.

        **A save and restore, not a set-then-pop.** `NODE_CLASSES` is the process's
        registry, and since this file stopped loading `parser.py` by path it is the
        one the engine walks — see the module docstring. `addCleanup(NODE_CLASSES.pop,
        name)` is only safe while no real entry can carry the name; if one did, the
        pop would delete the registry's own binding rather than restore it, and the
        damage would surface in whatever test ran *next*.
        """
        had = name in materialiser.NODE_CLASSES
        previous = materialiser.NODE_CLASSES.get(name)

        def restore():
            if had:
                materialiser.NODE_CLASSES[name] = previous
            else:
                materialiser.NODE_CLASSES.pop(name, None)

        materialiser.NODE_CLASSES[name] = cls
        self.addCleanup(restore)

    def test_registering_a_node_class_restores_the_registry_exactly(self):
        """Both directions: a name that was absent, and a name that was taken.

        **The second is the one that matters, and an earlier draft failed it.** The
        cleanup was `addCleanup(NODE_CLASSES.pop, name)`, which restores absence and
        nothing else — so registering over an existing entry deleted the entry instead
        of putting it back, and the loss surfaced in whatever test ran next. That is
        only reachable because this file's `parser` is now the process's own module
        rather than a private copy.
        """
        self.assertNotIn("RestoredNode", materialiser.NODE_CLASSES)
        self._register("RestoredNode", type("RestoredNode", (Node,), {}))
        self.assertIn("RestoredNode", materialiser.NODE_CLASSES)
        # `addCleanup` runs most-recent-first, so this fires the restore registered
        # by `_register` without ending the test.
        self.doCleanups()
        self.assertNotIn("RestoredNode", materialiser.NODE_CLASSES)

        original = materialiser.NODE_CLASSES.get("TextNode")
        self.assertIsNotNone(original, "the registry has no TextNode to displace")
        self._register("TextNode", type("TextNode", (Node,), {}))
        self.assertIsNot(materialiser.NODE_CLASSES["TextNode"], original)
        self.doCleanups()
        self.assertIs(materialiser.NODE_CLASSES["TextNode"], original)
```

Run: `scripts/run_unit_tests.sh tests.test_materialise -v`
Expected: PASS, and `test_registering_a_node_class_restores_the_registry_exactly` is the
row that pins it. **Mutation-test it**: put `addCleanup(NODE_CLASSES.pop, name)` back and
require the row to go red. A save-and-restore that no row distinguishes from a pop is the
green bulb this plan keeps finding.

- [ ] **Step 6: Run the tests**

Run: `scripts/run_unit_tests.sh tests.test_materialiser_parity -v`
Expected: **every row in the module PASS.** Every sensitivity row passes on the first
run — the guard is in the oracle, so `test_the_fingerprint_refuses_an_address_bearing_repr`
passes rather than fails. The parity row passes trivially, because **both branches are
the Python walk until Task 3**, and that is the point: the harness is proven green
before it is asked to prove anything. The row count is deliberately not written down
here: it moves every time a sensitivity row is added, and a number that has to be
edited on every addition stops being read.

The coverage row is the one that has to be *worked*, and it was. Run as first written
it named twenty classes — `CacheNode`, `CsrfTokenNode`, `DebugNode`, `FilterNode`,
the eight i18n nodes, `LocalTimeNode`, `LocaleNode`'s siblings, `LoremNode`, `NowNode`,
`PrefixNode`, `RegroupNode`, `TranslateNode`, `WidthRatioNode` — and the corpus now has
one `TAG_SOURCES` entry per name. Four of them taught something worth keeping in the
file, and each is recorded at its row:

  * **`{% load %}` is load-bearing for three of them.** `{% blocktranslate %}` is
    `django.templatetags.i18n`'s and is not a builtin; without the `{% load %}` the row
    dies at `ParseError: Invalid block tag on line 1: 'blocktranslate'`.
  * **The library has to be this package's transcription.** `parser.py` builds
    `NODE_CLASSES` from `django_rust_templates.engine.templatetags.*`, so a `{% load %}`
    naming *Django's* `tz` hands the tag to a compile function that builds *Django's*
    `TimezoneNode` — which the oracle caught, loudly, as
    `fingerprint fell through to an address-bearing repr for TimezoneNode:
    '<django.templatetags.tz.TimezoneNode object at 0x…>'`.
  * **`{% with %}` takes `a=1 b=2` or `a as b` and not `total = a b`.** Both wrong
    spellings were tried and both raise (`'with' expected at least one variable
    assignment`, then `'with' received an invalid token: 'b'`), so this is measured.
  * **The libraries belong to the engine, not the source.** A bare `Engine()` raises
    `'static' is not a registered tag library`; `materialiser_corpus.corpus_engine()`
    is the engine the parity and coverage rows parse through.

Every entry in `NODE_CLASSES` must be built by some source here, and the row fails
naming the ones that are not. Do not add a count, and **do not add an exclusion set**:
an earlier draft carved out `{"HostNode", "TemplateNode"}` on the reasoning that a
reserved name and the root class could not be built, and both are simply absent from
the registry — the carve-out excluded nothing while reading as policy. If a class is
genuinely unreachable, the honest fix is a template that reaches it, or a note saying
why no template can; a name in an exclusion set that cannot fire is the same green bulb
as no row at all. Add the template, not an exception.

- [ ] **Step 7: Commit**

```bash
git add tests/materialiser_corpus.py tests/test_materialiser_parity.py \
        tests/test_materialise.py tests/test_filter_expr_parity.py \
        tests/test_parser_parity.py tests/test_variable_parity.py tests/test_ref_table.py
git commit -m "test: the materialiser parity oracle, and the mutations it must see"
```

---

### Task 3: The Rust walk, and the switch

The root loop and `NodeList` assembly land here, with every node still built by
Python.

**The wire build is *not* skipped on this path, and the first version of this
sentence said it was.** `fallback_node` calls `node_desc_to_py`, which recursively
encodes each node's whole subtree, so the same nodes are encoded as 461 separate
calls rather than one. What moves at Task 3 is the *loop*, not the encode and not the
recursion — see Step 8's measurement, where the reviewer's correction to this
paragraph is recorded with the counts that establish it. The encode is skipped when
the fallback stops being taken, which is Tasks 4-7.

**Files:**
- Create: `crates/template-py/src/materialise.rs`
- Modify: `crates/template-py/src/lib.rs` (module declaration at end of file; the `compile_template` call site)
- Modify: `python/django_rust_templates/engine/parser.py` (**append at end of file only**)
- Modify: `tests/test_materialiser_parity.py` (the `SwitchTests` class, appended)
- Modify: `scripts/compile_profile.py` (no change; the switch is read already)

**Interfaces:**
- Produces: `materialise::materialise_native(py, tree, desc, origin, resolve_ref, register_ref, parser) -> PyResult<Py<PyAny>>`, returning a `NodeList`.
- Consumes: `parser.NODE_CLASSES`, `parser.materialise_node`, `parser.NodeList`, `parser.HOST_NODE`, `crate::node_desc_to_py`.

- [ ] **Step 1: Append the switch to `parser.py`**

At the very end of the file. **Nothing may be inserted above**, or every anchor
into `parser.py` drifts.

```python
# Whether `_native.compile_template` builds the tree with the Rust walk in
# `crates/template-py/src/materialise.rs` instead of `materialise` above.
#
# **At the end of the file, and that is load-bearing.** `scripts/anchors.txt`
# carries anchors into this file by line number, so a name inserted near the top
# moves every one of them; appending a name below every anchor moves none.
#
# Read by Rust as a **bool or nothing**: a missing attribute reads `False`, so a
# Rust tree newer than the Python module beside it does not fail a compile — but
# a value that is *present and not a bool* raises, because that is a bug in this
# file rather than a version skew.
USE_NATIVE_MATERIALISER = False
```

- [ ] **Step 2: Write the walk**

```rust
//! The native materialiser: build the compatibility tree by walking the IR.
//!
//! `parser.materialise` does this in Python by walking a *wire* of nested tuples
//! and pyclasses. Measured on `real-world/large` (9279 chars, 621 nodes) that
//! walk is 1153 us of a 1834 us compile — 63% — while the plan built from the
//! same tree is 26 us. The wire exists only to be decoded.
//!
//! **Three properties of `materialise_node` are contracts.** Each is the natural
//! thing to reorder when optimising and the failures are silent, so each is named
//! here, and each gets a row when the task that could break it lands.
//!
//! **Task 3 preserves all three the easy way and pins none of them.** Every node
//! still goes through `materialise_node`, so the orderings below are inherited from
//! Python rather than established here; nothing in this file yet reads the registry,
//! builds a descendant, or attaches a token. What *is* Rust's at Task 3 is the
//! root-list loop, and that loop had to be written to Python's evaluation order —
//! see [`materialise_native`], where binding `append` before the build is the
//! whole content of the first contract at this stage. Tasks 4-7 move the
//! construction down arm by arm, and that is when the three become this module's
//! promises rather than Python's.
//!
//! 1. The registry is read **before** the children are built
//!    (`python/django_rust_templates/engine/parser.py:347` against
//!    `python/django_rust_templates/engine/parser.py:372`). A factory that rebinds
//!    `NODE_CLASSES["IfNode"]` while a child is built is observed by that child's
//!    siblings and *not* by the parent, and an unknown class name raises before any
//!    child work.
//! 2. Children are materialised **before** the parent is constructed, and the
//!    attributes are decoded **after** the children exist. `ExtendsNode.__init__`
//!    reads the nodelist it is handed, and an `AttrValue::NodeListRef` names a
//!    position in a list that must already be populated.
//! 3. `token` and `origin` are attached **after** construction. `__init__` runs
//!    first and some of them read the subtree.
//!
//! **The fallback is the design, not scaffolding.** Every shape this module does
//! not yet build is handed to the Python decoder for that shape alone. Tasks 4-7
//! remove fallbacks one group at a time; the host-node arm keeps one permanently,
//! because it is a rare node whose descriptor validation and error messages are
//! asserted by tests that should keep reading the strings they were written
//! against.

use pyo3::prelude::*;
use template_core::parser::desc::{NodeDesc, NodeListDesc};

/// Build a `NodeList` from a descriptor, children first.
pub fn materialise_native(
    py: Python<'_>,
    tree: &Bound<'_, PyAny>,
    desc: &NodeListDesc,
    origin: &Bound<'_, PyAny>,
    resolve_ref: &Bound<'_, PyAny>,
    register_ref: &Bound<'_, PyAny>,
    parser: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let nodelist = tree.getattr("NodeList")?.call0()?;
    // `contains_nontext` is a *class* attribute on `NodeList`, so leaving it
    // alone is how it reads False. Only the True transition is written, which is
    // what makes a second, text-only nodelist read False again.
    if desc.contains_nontext {
        nodelist.setattr("contains_nontext", true)?;
    }
    for node in &desc.nodes {
        // **`append` is bound before the node is built, and that is Python's
        // evaluation order rather than a micro-optimisation.** `materialise` reads
        // `nodelist.append(materialise_node(...))`, so `LOAD_METHOD` resolves
        // `append` first and only then evaluates the argument. A factory that
        // rebinds `NodeList.append` while it builds a node is therefore **not**
        // seen by that node's own append, and *is* seen by every later one.
        //
        // The obvious spelling — `nodelist.call_method1("append", (built,))` after
        // the build — uses the replacement for the very append the original was
        // bound to serve. Measured on a one-node template whose `TextNode` factory
        // rebinds `NodeList.append`: Python calls the replacement 0 times, the
        // after-the-build spelling calls it once.
        let append = nodelist.getattr("append")?;
        let built = materialise_one(py, tree, node, origin, resolve_ref, register_ref, parser)?;
        append.call1((built,))?;
    }
    Ok(nodelist.unbind())
}

fn materialise_one(
    py: Python<'_>,
    tree: &Bound<'_, PyAny>,
    desc: &NodeDesc,
    origin: &Bound<'_, PyAny>,
    resolve_ref: &Bound<'_, PyAny>,
    register_ref: &Bound<'_, PyAny>,
    parser: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    if desc.class_name == template_core::parser::desc::HOST_NODE {
        return fallback_node(py, tree, desc, origin, resolve_ref, register_ref, parser);
    }
    // Task 3: the walk above is Rust's; the node is Python's.
    // Tasks 4-7 move the construction down arm by arm.
    fallback_node(py, tree, desc, origin, resolve_ref, register_ref, parser)
}

/// Every Python entry point this walk is allowed to call, and the only place
/// they are named.
///
/// **This enum is the census's teeth, and it exists because two earlier attempts
/// at the same proof had none.** The first read `materialise.rs` as text and
/// asserted that certain substrings were absent — which a renamed fallback walks
/// straight past, and which a *comment* can trip: the row rejected the very
/// implementation Task 7 writes. The second wrapped a hand-written list of names
/// in `tests/test_materialiser_parity.py` and asserted the called set was a subset
/// of that list, which is true by construction and cannot see a name that is not
/// on it. **A bridge cannot be called without a variant here** — `call_bridge` takes
/// a `Bridge` and nothing else — and Task 8 adds
/// `the_bridge_list_is_pinned` to `walk_depth`, which asserts the enumerated list's
/// length, its uniqueness and that every name exists on the module. Adding a sixth
/// Python bridge is then a red row in Rust, in the crate that owns the walk.
///
/// The Python-side census keeps a different claim, and the two are not redundant:
/// it records **which** bridges the corpus actually reaches, so a bridge that is
/// never exercised is visible rather than merely permitted.
///
/// **`NODE_CLASSES` is read with a bare `getattr` and is not a bridge**, because it
/// is a dict the walk reads rather than a function it calls — there is nothing to
/// dispatch and nothing to return an exception.
///
/// **A variant is declared by the task that calls it, and not before.** This enum
/// was first written whole — every bridge the finished walk was then expected to reach,
/// eleven of them counting `DecodeValue`, which Task 7 went on to delete — and that
/// was wrong twice over. All but one had no constructor in *either*
/// compilation of the crate, so they needed a blanket `#[allow(dead_code)]` to
/// survive `-D warnings`; and an enumeration nothing calls establishes no pin, since
/// it is a list that can be left stale by exactly the edit it exists to catch. An
/// unreachable variant that a reader trusts is worse than no variant. So the census
/// is built the same way it is enforced: **one variant per arm, added by the task
/// that adds the arm**, and Task 8's `the_bridge_list_is_pinned` counts what is
/// actually there.
///
/// **What the finished walk reaches, for a reader of Tasks 4-7**: `materialise_node`
/// (the host node, permanent), `_decode_value` (the per-value fallback Task 7
/// removes), `_materialise_variable` and `_materialise_filter_expression` (kept whole
/// rather than reimplemented — the *dispatch* is Rust's, what they do once reached is
/// Python's), `_decode_condition`, `_decode_token` (which has one spelling, shared
/// with `_native.lex`), `mark_safe` (so a `{% verbatim %}` body does not escape
/// itself), and the four error helpers `_unknown_node_class`,
/// `_missing_resolver_error`, `_empty_slot_error` and
/// `_nodelist_ref_out_of_range_error`, called rather than re-spelled so that a message
/// existing tests read stays the only copy of itself.
///
/// **Which task declares which**, so a reader of Tasks 4-7 can check the enum against
/// this list rather than trusting it: Task 4 adds `DecodeValue`, `DecodeToken`,
/// `MarkSafe` and `UnknownNodeClass`; Task 5 adds the three reference and arity errors;
/// Task 6 adds none, because it extends `attr_to_py` and introduces no new call; Task 7
/// adds `MaterialiseVariable`, `MaterialiseFilterExpression` and `DecodeCondition`, and
/// **deletes `DecodeValue`** with `fallback_attr`. That is eight declared before Task 7
/// and ten after, which is the number Task 8's pin asserts.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Bridge {
    /// The host node's own object. The only bridge that exists at Task 3, and the
    /// only node-level one that is permanent: the host node's descriptor validation
    /// and error messages are asserted by tests that should keep reading the strings
    /// they were written against.
    MaterialiseNode,
}

impl Bridge {
    /// The name of the Python entry point this variant stands for.
    ///
    /// The enumeration Task 8's pin counts is **not** here, and the comment on
    /// [`Bridge`] says why. When it lands it will read **eight before Task 7 and
    /// ten after**, because that task deletes [`Bridge::DecodeValue`] and adds three; the row's
    /// number changing is the point, since it is one edit in Rust that a reviewer
    /// reads rather than a substring that has to be kept out of a comment.
    pub(crate) fn name(self) -> &'static str {
        match self {
            Bridge::MaterialiseNode => "materialise_node",
        }
    }
}

/// The walk's only call into the parser module.
///
/// Deliberately small. Its value is not that it saves anything — `getattr` per
/// node is not the cost — but that every Python call in this file goes through one
/// function that takes a [`Bridge`], so the set of them is a type rather than a
/// convention.
fn call_bridge<'py>(
    tree: &Bound<'py, PyAny>,
    bridge: Bridge,
    args: impl pyo3::call::PyCallArgs<'py>,
) -> PyResult<Py<PyAny>> {
    Ok(tree.getattr(bridge.name())?.call1(args)?.unbind())
}

/// Build one node with the Python materialiser, from a wire built for it alone.
///
/// `node_desc_to_py` is the same producer `with_ref_table` uses, so the wire
/// still has exactly one spelling — this is not a second encoding, it is the
/// existing one called one node at a time.
fn fallback_node(
    py: Python<'_>,
    tree: &Bound<'_, PyAny>,
    desc: &NodeDesc,
    origin: &Bound<'_, PyAny>,
    resolve_ref: &Bound<'_, PyAny>,
    register_ref: &Bound<'_, PyAny>,
    parser: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let wire = crate::node_desc_to_py(py, desc)?;
    call_bridge(
        tree,
        Bridge::MaterialiseNode,
        (wire, origin, resolve_ref, register_ref, parser),
    )
}
```

**Write those two citations out in full, both of them.** `scripts/anchors.txt` is
checked with `--check --frozen`, and `--frozen` does not accept the `parser.py:350`
shorthand — a citation with no directory is reported as `unmigrated: 1 shorthand
anchor(s) naming no file` and fails the gate, while the same string with its full
path passes. The manifest also has to be re-seeded once
(`python -I scripts/check_python_anchors.py --seed`) because these are new lines it
has never seen. `cargo fmt --all` does not touch comments, so the full path survives
formatting.

- [ ] **Step 3: Declare the module, at the end of `lib.rs`**

```rust
// Declared here rather than at the top of the file **on purpose**: every anchor
// into `lib.rs` in `scripts/anchors.txt` is a line number, and a `mod` above them
// moves all of them. Rust does not care where a `mod` is declared.
mod materialise;
```

- [ ] **Step 4: Select the path in `compile_template`**

Read `lib.rs:5073-5095` before editing. The wire is built **once**, at `:5073`;
`:5080` re-wraps *that tuple* with the same reference table, which is idempotent
rather than a second build. The `refs`/`resolve_ref`/`register_ref` lines at
`:5077-5079` sit between the two.

Replace the `:5073` line with the flag read:

```rust
    // A **bool or nothing**: a missing attribute reads `False`, so a Rust tree
    // newer than the Python module beside it does not fail a compile. A value
    // that is present and not a bool raises, because that is a bug in the Python
    // half rather than a version skew — `unwrap_or(false)` would have swallowed
    // it along with every other error `getattr` can raise.
    let native: bool = match tree.getattr("USE_NATIVE_MATERIALISER") {
        Ok(flag) => flag.extract()?,
        Err(error) if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) => false,
        Err(error) => return Err(error),
    };
```

replace the `:5080` line with:

```rust
    // The wire and its wrapper tuple are built **only** on the Python path.
    // `nodelist_desc_to_py` and `with_ref_table` are part of what S1 removes, and
    // a line that built them "just in case" would leave them measurable while
    // the walk they feed never runs.
    let desc = if native {
        py.None()
    } else {
        with_ref_table(py, nodelist_desc_to_py(py, &desc_tree)?, &host)?
    };
```

**`py.None()` and not `py.None().into_bound(py)`**, which is what this fence said
before it was built. `with_ref_table` returns `Py<PyAny>`, so the `else` arm fixes
the type of `desc` — and a `Bound` on the `if` arm is `E0308`, two arms with
incompatible types. `.into_bound(py)` belongs on neither arm: both `materialise`
and the native call take the value as it is.

The reference table itself is still allocated on both paths — `refs` and the two
bound methods are what the native walk takes, so `host.ref_list(py)` stays
unconditional. It is the *wire* that is skipped, and `resolve_ref`/`register_ref`
are passed straight to `materialise_native` instead of being attached to a tuple
for Python to find.

Then the `materialise` call at `:5091` becomes:

```rust
    let nodelist = if native {
        materialise::materialise_native(
            py,
            &tree,
            &desc_tree,
            &origin,
            &resolve_ref,
            &register_ref,
            parser.bind(py),
        )?
        .into_bound(py)
    } else {
        tree.getattr("materialise")?
            .call1((desc, &origin, resolve_ref, register_ref, parser.bind(py)))?
    };
```

- [ ] **Step 5: Add the loudness row for the switch**

In `tests/test_materialiser_parity.py`:

```python
class SwitchTests(unittest.TestCase):
    """The switch reads a bool or nothing, and says which it got.

    `unwrap_or(false)` was the first version and it is wrong twice over: it
    swallows a genuine extraction failure as if it were a missing attribute, and
    it makes a typo in the constant's name read as "off" instead of failing.
    """

    def test_a_missing_constant_reads_as_off(self):
        # **The control is built before the deletion, and that is not stylistic.**
        # `_tree(..., native=False)` goes through `with_native`, which does a bare
        # `materialiser.USE_NATIVE_MATERIALISER` read to save and restore it — so
        # asking for the Python path *after* deleting the constant raises
        # `AttributeError` from the harness and never reaches the assertion. An
        # earlier draft did exactly that and would have failed on correct code.
        control = fingerprint(_tree("{{ a }}", native=False))
        real = materialiser.USE_NATIVE_MATERIALISER
        del materialiser.USE_NATIVE_MATERIALISER
        try:
            # `native=None` leaves the switch alone, so this is the module with no
            # constant at all — which the Rust read must treat as off.
            self.assertEqual(fingerprint(_tree("{{ a }}")), control)
        finally:
            materialiser.USE_NATIVE_MATERIALISER = real

    def test_a_present_but_not_boolean_constant_is_loud(self):
        real = materialiser.USE_NATIVE_MATERIALISER
        materialiser.USE_NATIVE_MATERIALISER = "yes"
        try:
            with self.assertRaises(TypeError):
                _tree("{{ a }}")
        finally:
            materialiser.USE_NATIVE_MATERIALISER = real

    def test_the_native_branch_does_not_call_python_materialise(self):
        """The one row here that separates the branches instead of comparing them.

        **Equality cannot say which branch ran, and until this row exists nothing
        does.** Every other row in this file passes whether or not the Rust walk is
        ever entered, because on both sides of the switch the tree is built the same
        way — the Python walk and, before the walk exists, the Python walk again.
        Booby-trapping the Python `materialise` makes dispatch observable: on the
        Python path it must raise, and on the native path it must not be reached.

        It works only because there is exactly one `parser` module in the process.
        Patching this module's attribute is what the Rust side looks up
        (`tree.getattr("materialise")`), so a second module object under the same name
        would leave this row passing while the real path went unpatched — the same
        trap the loader repair in Task 2 closed.
        """

        def explode(*args, **kwargs):
            raise AssertionError("the Python walk was reached through the native branch")

        real = materialiser.materialise
        materialiser.materialise = explode
        try:
            with self.assertRaises(AssertionError):
                _tree("{{ a }}", native=False)
            native = fingerprint(_tree("{{ a }}", native=True))
        finally:
            materialiser.materialise = real
        # Both branches agree, and the control is taken *after* the patch is lifted
        # so that the Python side is not the same broken call that just raised.
        self.assertEqual(native, fingerprint(_tree("{{ a }}", native=False)))
```

- [ ] **Step 6: Pin the evaluation order of `nodelist.append`**

**This step is here because the walk got it wrong the first time and the reviewer's
round 1 caught it, and it is the only thing in Task 3 that a corpus-wide parity row
could not have caught.** `materialise` reads
`nodelist.append(materialise_node(desc, …))`: `LOAD_METHOD` resolves `append` and
*then* evaluates the argument. The first version of `materialise_native` built the
node first and called `nodelist.call_method1("append", …)` afterwards, which looks the
method up after the build and so uses a *replacement* for the very append the original
was bound to serve. Both paths agreed on all 48 corpus sources either way — the
divergence needs a factory that rebinds `NodeList.append` mid-build, and no corpus
source does.

Append to `TwoPathParityTests` in `tests/test_materialiser_parity.py`:

```python
    def test_append_is_bound_before_the_node_is_built_on_both_paths(self):
        """`nodelist.append` is resolved *before* the node's own construction.

        `materialise` reads `nodelist.append(materialise_node(desc, ...))`, so
        `LOAD_METHOD` resolves `append` first and only then evaluates the argument.
        A factory that rebinds `NodeList.append` while it builds a node is therefore
        **not** seen by that node's own append — and *is* seen by every later one.

        **This row exists because the Rust walk got it wrong, and a reviewer found
        it, not a test.** The first version built the node and then called
        `nodelist.call_method1("append", …)`, which looks the method up *after* the
        build, so it uses the replacement for the very append the original was bound
        to serve. Measured before the fix: Python 0 calls, native 1 call. The two
        paths agreed on all 48 corpus sources either way, which is why the parity row
        could not see it — the divergence needs a factory that mutates the class
        mid-build, and no corpus source does.

        **The engine's own `NodeList`, not `django.template.base.NodeList`.** They are
        different classes (`django_rust_templates.engine.base`), and patching the
        Django one makes this row pass on both paths for a reason that has nothing to
        do with the ordering — which is how the first draft of the probe was fooled.
        """
        nodelist_cls = materialiser.NodeList
        # **Presence is remembered, not just the value.** `append` is inherited from
        # `list` and is *not* in `NodeList.__dict__`, so saving it and assigning it
        # back is not a restoration: it leaves a real class attribute behind, on a
        # class every compiled template in the process shares. A round-2 review
        # measured exactly that leak. The same shape as `_register` in
        # `tests/test_materialise.py`, and for the same reason.
        had_append = "append" in nodelist_cls.__dict__
        real_append = nodelist_cls.append
        real_text = materialiser.NODE_CLASSES["TextNode"]

        for native, source in ((False, "hello"), (True, "world")):
            # Two sources rather than one, so that a compiled-template cache — if
            # one is ever added between `compile_template` and here — cannot let the
            # second branch reuse the first branch's tree and never call the factory.
            replacements = []

            def recorder(self, node):
                replacements.append(node)
                return real_append(self, node)

            def rebinding_text(*args, **kwargs):
                node = real_text(*args, **kwargs)
                nodelist_cls.append = recorder
                return node

            materialiser.NODE_CLASSES["TextNode"] = rebinding_text
            try:
                with self.subTest(native=native, source=source):
                    _tree(source, native=native)
                    self.assertEqual(
                        replacements,
                        [],
                        "append was looked up after the node was built",
                    )
            finally:
                if had_append:
                    nodelist_cls.append = real_append
                else:
                    del nodelist_cls.append
                materialiser.NODE_CLASSES["TextNode"] = real_text

        # The restoration is pinned in both directions, because a cleanup that leaves
        # a class attribute behind is invisible to every assertion above it — the row
        # still passes, and the next test in the process inherits the patch.
        self.assertEqual("append" in nodelist_cls.__dict__, had_append)
        self.assertIs(nodelist_cls.append, real_append)
        self.assertTrue(hasattr(nodelist_cls, "append"))
```

**Two patches on one class, and the cleanup is the part that is easy to get wrong.**
`append` is inherited from `list` and is **not** in `NodeList.__dict__`, so saving it
and assigning it back is not a restoration: it leaves a real class attribute behind, on
a class every compiled template in the process shares. A round-2 review measured exactly
that leak — `{'append'}` added to the class dict after the row ran — while the row
itself stayed green, because nothing in it can see its own patch. Hence the
presence-aware restore and the three assertions at the foot, which pin the restoration
in both directions. The mutation for *that* is to put the leaky cleanup back:

```python
            finally:
                nodelist_cls.append = real_append
                materialiser.NODE_CLASSES["TextNode"] = real_text
```

Measured: the row then fails on
`assertEqual("append" in nodelist_cls.__dict__, had_append)` with `True != False`, while
everything above it still passes — which is precisely how the leak got through the
first time.

Then **mutation-test the ordering itself**, because a row that has only ever been green
is a row nobody has read. Put `lib.rs` back to the after-the-build spelling —

```rust
        let built = materialise_one(py, tree, node, origin, resolve_ref, register_ref, parser)?;
        nodelist.call_method1("append", (built,))?;
```

— rebuild, and confirm this row **fails** with `['<TextNode: 'world'>']` on the
`native=True` subtest and passes on `native=False`. Measured: exactly that, one
failure and one pass, which is the whole point of running both branches rather than
just the new one. Revert the edit, rebuild, and confirm green again.

- [ ] **Step 7: Build and run the parity test through the native branch**

```bash
source scripts/env.sh && maturin develop --release
scripts/run_unit_tests.sh tests.test_materialiser_parity -v
```
Expected: PASS. This is the first run where the two branches are genuinely
different code, so a red row here is a real divergence in the walk — most likely
ordering or `contains_nontext` — and not an oracle problem.

**Then mutation-test the dispatch row, because a green suite is not evidence that
either branch was entered.** With the build in place, edit `lib.rs:5073` to

```rust
    let native: bool = false;
```

and re-run `scripts/run_unit_tests.sh tests.test_materialiser_parity`. Expected:
`test_the_native_branch_does_not_call_python_materialise` **fails** — its
`native=True` call now runs the Python walk, which is booby-trapped, so the
`assertRaises(AssertionError)` swallows the wrong one and the control comparison
carries the raising call.

**Two rows fail and not one, and the second is legitimate.** An earlier draft of this
step said "every other row must stay green", which is wrong:
`test_a_present_but_not_boolean_constant_is_loud` also fails, because the mutation
deletes the `getattr("USE_NATIVE_MATERIALISER")` that row exists to test — it replaces
the whole `match`, not just the value it yields. Measured: exactly those two, and
nothing else. A row that goes red *without* a story like that one is a row asserting
something about dispatch it did not name. Revert the edit.

- [ ] **Step 8: Confirm the saving moves off zero**

```bash
.venv/bin/python -I scripts/compile_profile.py large
```

At Task 1 both columns read the same number and the saving was 0.0 µs. Now the two
branches are different code, so this is the first reading where `native` and
`python` differ — and the first chance to see that the walk's number moved rather
than the parse's.

**As built, the first reading is negative, and that is the correct reading rather
than a defect.** On `real-world/large`, over seven warm runs across two builds (the
second after the `append`-order fix): `native` 1859-1887 µs, `python` 1755-1783 µs — a
saving of **−88 to −113 µs (−5.0% to −6.4%)**; `materialise` itself 914-933 µs,
51.6-52.6% of the `python` column; `stubbed` 636-650 µs.

**What this step may and may not conclude — an earlier draft of this paragraph got
all four of these wrong, in the direction of claiming more than the instrument
supports, and the round-2 review caught the survivors of the round-1 rewrite.**

  * `stubbed` **is** a genuine Python-path control: it is the same compile with the
    walk replaced, so a parse-side regression would move it. It being steady across
    two builds is consistent with the parse not having shifted.
  * `stubbed` being steady does **not** prove the parse did not shift. A regression
    small enough to sit inside the walk's own run-to-run variance would leave it
    steady too. The measurement bounds a parse regression; it does not exclude one.
  * The whole of the difference is therefore **not** established to be the walk. The
    `native` column also does more work than the `python` column it is compared
    against — 461 boundary crossings against one, and the encode still happening —
    so the two are not one shape of work with a term removed.
  * **"Quote the spread and not a single run" stands; the page-cache explanation does
    not.** The first two readings taken immediately after `maturin develop` were
    `−294.9 µs (−16.3%)` and `−136.6 µs (−7.1%)`, against a steady state of −5% to
    −6.4%. That the outliers follow a rebuild is suggestive and is not evidence of
    cause; a cold page cache was never demonstrated to be why. What is established is
    the spread itself — seven warm runs agreeing to within 50 µs — and that is the
    only form in which the number should be quoted. This matters more than usual
    because the number is *negative*: a noisy reading in either direction is easy to
    mistake for the lever working or not working.

**Why it is slower: Task 3 crosses the boundary 461 times where the path it replaces
crosses once — and it skips nothing.** An earlier draft of this paragraph said the
encode was skipped and the recursion had moved. **Round 1 of this task's review
measured both and both were wrong**, and the correction is worth more than the
sentence it replaces. Instrumenting `materialise_node` counts the same **621** calls
on both paths for `bench/large`:

  * **`python`** — `materialise` is entered once and loops over the **461**
    top-level nodes; its own recursion builds the remaining **160** descendants.
    One Python-level entry point per compile.
  * **`native`** — the Rust loop makes **461** Rust→Python crossings, one per
    top-level node; Python still builds the same **160** descendants underneath
    each of them. The wire is **not** skipped: `fallback_node` calls
    `node_desc_to_py`, which *recursively encodes that node's whole subtree*, so the
    same 621 nodes are encoded as 461 separate calls instead of one.

So Task 3 adds 460 boundary crossings and 460 extra encode entry points, removes
nothing, and moves no recursion. That is not a defect to fix — it is what "the walk
is Rust's and the node is Python's" costs before any arm exists. Tasks 4-7 are what
move the construction down arm by arm until the fallback stops being taken; the
crossings fall and the encode finally does get skipped, and the saving turns positive
when the arms outnumber the boundary. **Do not read this number as the lever's
value.** It is the price of the switch being in place, and the floor the later tasks
have to beat.

**Two caveats on the columns, both of which an earlier draft of this step
overstated.** `stubbed` is a genuine *Python-path* control — it is the same compile
with the walk replaced, so a parse-side regression would move it — but a stable
`stubbed` does **not** prove parse time was unchanged, because a regression small
enough to hide inside the walk's own variance would not move it either. And the
`native` column does *more* work than the `python` column it is compared against, so
the two are not one shape of work with a term removed; the comparison supports "this
is not yet a win" and nothing finer. The claim that the two cold readings were
page-cache noise is also unproven: they correlate with the rebuild, which is
suggestive and not evidence. What is established is the spread itself — four warm
runs agreeing to within 25 µs — and that is the only form in which the number should
be quoted.

- [ ] **Step 9: Run the gate, in order**

```bash
scripts/run_unit_tests.sh
cargo fmt --all
cargo clippy --workspace --all-targets --quiet -- -D warnings
cargo clippy -p template-py --no-default-features --all-targets --quiet -- -D warnings
python -I scripts/check_python_anchors.py --check --frozen
```
The second `clippy` is not a repeat: `--no-default-features` turns the
`extension-module` feature *off*, which is the only configuration in which
`mod walk_depth` exists, and that is the module Task 8 puts the bridge pin in.

`--check --frozen` and not a bare `--check`, because `--frozen` is the mode the gate
actually runs in and it is strictly stronger: a bare `--check` reports the bare-path
shorthand as a non-failing `unmigrated` count, so a citation this task writes as
`parser.py:350` passes here and fails in CI.
If the anchor gate reports `crates/template-py/src/lib.rs` drift, run
`python -I scripts/check_python_anchors.py --update=crates` and re-run `--check`.

- [ ] **Step 10: Commit**

```bash
git add crates/template-py/src/materialise.rs crates/template-py/src/lib.rs \
        python/django_rust_templates/engine/parser.py scripts/compile_profile.py \
        tests/test_materialiser_parity.py
git commit -m "perf: the Rust materialiser walk, behind a switch that is off"
```

---

### Task 4: `kwargs`, primitives, `token` and `origin`

Node construction moves into Rust for the primitive attribute shapes — where most
of the Python frames are: every `TextNode` and every `VariableNode` attribute is
one of these.

**The registry order is the review's second finding and this task is where it
lands.** `NODE_CLASSES[class_name]` is read **before** the children, and the
unknown-class error is raised before any child work.

**Declares:** `Bridge::DecodeValue`, `Bridge::DecodeToken`, `Bridge::MarkSafe` and
`Bridge::UnknownNodeClass` — added to the enum in the same edit as the arm that
calls each, per the rule on `Bridge` in `materialise.rs`. Do not add them early: a
variant with no arm is dead code that needs an allow to compile and is the exact
failure round 1 of Task 3's review removed.

**Files:**
- Modify: `crates/template-py/src/materialise.rs`
- Modify: `python/django_rust_templates/engine/parser.py` (append one helper,
  `_unknown_node_class`, and point `materialise_node`'s `except KeyError` arm at it)
- Test: `tests/test_materialiser_parity.py`

**Interfaces:**
- Produces: `materialise_one` reads the registry, builds `kwargs`, calls
  `factory(**kwargs)`, then attaches `token`/`origin`. Produces `attr_to_py` with
  a `_ =>` fallback arm, `fallback_attr`, `decode_token_or_none`.
- Consumes: `crate::attr_value_to_py`, `tree._decode_value`, `tree._unknown_node_class`.

- [ ] **Step 1: Write the failing tests**

```python
class RegistryOrderTests(unittest.TestCase):
    """The registry is read *before* the children, and the review is why.

    `materialise_node` does `cls = NODE_CLASSES[class_name]` at
    `python/django_rust_templates/engine/parser.py:347` and builds the children
    at `python/django_rust_templates/engine/parser.py:372`. So a factory that
    rebinds the registry while a *child* is built is observed by that child's
    siblings and by anything deeper, and **not** by the parent whose class was
    already captured. A walk that read the registry after its children would build
    a different tree, and the difference is invisible to a fingerprint — the two
    nodes have the same class name.

    The fixture is awkward on purpose, and two earlier drafts of it did not run:
    `TextNode.__init__(self, s)` takes the text as its one argument, so `real()`
    with no arguments is a `TypeError`; and `Marker` has to subclass the real
    `IfNode` *class* (`class Marker(real_if)`), not `type(real_if())`, because the
    claim is "the parent built the class the registry held at the top", and a
    freshly constructed instance is a different thing from the class.

    **The `native=True` row is a regression guard rather than a failing-first
    test.** It already passes with the Task 3 walk in place, because Task 3 hands
    each top-level node to `materialise_node` whole, and *that* reads the registry
    in Python's order. It earns its keep from Task 4 on, when `materialise_one`
    builds the parent in Rust and is the only thing left holding the ordering.
    What proves it discriminates is the mutation, not the order it was written
    in: with `tree.getattr("NODE_CLASSES")` moved below the child loop this row
    goes red and `test_the_python_path_is_the_control` stays green.
    """

    def test_a_rebind_during_a_child_is_not_seen_by_the_parent(self):
        real_text = materialiser.NODE_CLASSES["TextNode"]
        real_if = materialiser.NODE_CLASSES["IfNode"]

        class Marker(real_if):
            """Distinguishable from `real_if` by identity of the *class*."""

        rebinds = []

        def rebinding_text(*args, **kwargs):
            # The text is passed by keyword (`cls(**kwargs)`), so this is the
            # shape the walk actually calls and not a convenience.
            node = real_text(*args, **kwargs)
            if materialiser.NODE_CLASSES.get("IfNode") is not Marker:
                materialiser.NODE_CLASSES["IfNode"] = Marker
                rebinds.append(True)
            return node

        materialiser.NODE_CLASSES["TextNode"] = rebinding_text
        try:
            tree = _tree("{% if a %}x{% endif %}", native=True)
        finally:
            # Restored from locals captured at the top of this method. An earlier
            # draft restored from a `setUp` that this class does not have, which
            # is a `NameError` rather than a passing row.
            materialiser.NODE_CLASSES["TextNode"] = real_text
            materialiser.NODE_CLASSES["IfNode"] = real_if

        self.assertTrue(rebinds, "the fixture never rebound the registry")
        self.assertIs(type(tree[0]), real_if, "the parent read the registry after its children")

    def test_the_python_path_is_the_control(self):
        # If this fails the fixture is wrong, not the walk: the ordering under
        # test is one `materialise_node` already has, so the same rebind must
        # leave it alone too.
        real_text = materialiser.NODE_CLASSES["TextNode"]
        real_if = materialiser.NODE_CLASSES["IfNode"]

        class Marker(real_if):
            pass

        rebinds = []

        def rebinding_text(*args, **kwargs):
            node = real_text(*args, **kwargs)
            materialiser.NODE_CLASSES["IfNode"] = Marker
            rebinds.append(True)
            return node

        materialiser.NODE_CLASSES["TextNode"] = rebinding_text
        try:
            tree = _tree("{% if a %}x{% endif %}", native=False)
        finally:
            materialiser.NODE_CLASSES["TextNode"] = real_text
            materialiser.NODE_CLASSES["IfNode"] = real_if

        self.assertTrue(rebinds)
        self.assertIs(type(tree[0]), real_if)


class UnknownClassTests(unittest.TestCase):
    """An unknown class name raises before any child is built.

    Two claims, and the second is the one the Rust arm can get wrong. Raising is
    easy; raising *before the children exist* is what a walk that built its
    subtree first would not do, and the observable difference is that the child's
    constructor never ran.
    """

    def test_the_error_names_the_class_and_the_registry(self):
        with self.assertRaises(LookupError) as caught:
            materialiser._unknown_node_class("NoSuchNode")
        self.assertIn("NoSuchNode", str(caught.exception))
        self.assertIn("NODE_CLASSES", str(caught.exception))

    def test_the_error_comes_before_any_child_is_built(self):
        built = []
        real_text = materialiser.NODE_CLASSES["TextNode"]

        def recording_text(*args, **kwargs):
            built.append(kwargs)
            return real_text(*args, **kwargs)

        materialiser.NODE_CLASSES["TextNode"] = recording_text
        try:
            # An unknown *parent* whose child is a perfectly good `TextNode`. The
            # child is reachable from the descriptor, so a walk that built its
            # children before consulting the registry would run `recording_text`
            # and only then fail — leaving a constructed node for a parent that
            # does not exist.
            #
            # **The child is a real node descriptor, and an earlier draft of this
            # row is why that is spelled out.** It wrote `(("nodelist", ("hello",)),)`
            # — a bare string where a *nodelist* descriptor goes. A nodelist is a
            # `(nodes, contains_nontext)` pair whose first element is a tuple of
            # node descriptors (`nodelist_desc_to_py`,
            # `crates/template-py/src/lib.rs:1104`), and a node descriptor is
            # `(class_name, attrs, nodelists, token)`. With the
            # malformed form, a walk that built children *first* raised
            # `ValueError: not enough values to unpack` while iterating `"hello"` —
            # before `recording_text` could run — so `built == []` held for exactly
            # the ordering the row exists to reject. The malformed fixture was
            # indistinguishable from the fix.
            child = ("TextNode", (("s", "hello"),), (), None)
            desc = ("NoSuchParent", (), (("nodelist", ((child,), False)),), None)
            with self.assertRaises(LookupError) as caught:
                materialiser.materialise_node(desc)
            self.assertIn("NoSuchParent", str(caught.exception))
        finally:
            materialiser.NODE_CLASSES["TextNode"] = real_text
        self.assertEqual(built, [], "the parent's children were built before the registry was read")

    def test_the_native_walk_raises_before_any_child_is_built(self):
        """The same claim, driven through the *Rust* walk rather than Python.

        The row above is a good row and it is not evidence for the arm it sits
        beside: it calls `materialise_node`, so it pins Python's order and says
        nothing about `materialise_one`'s. The Rust arm's unknown-class path is
        `loud(tree, Bridge::UnknownNodeClass, …)`, and until this row existed
        **nothing reached it at all** — a walk that never consulted the registry
        and a walk that consulted it after its children both passed every other
        row in this file.

        The fixture is the review's: an ordinary `{% if a %}` whose *own* class is
        removed from the registry, with the body's `TextNode` factory recording.
        If the registry read moves below the child loop, `recording_text` runs and
        `built` is non-empty; the template need not be a malformed one, because
        the point is the ordering and not the descriptor shape.

        **A regression guard, like `RegistryOrderTests`:** at Task 3 this passes
        for Python's order, since the whole node is still handed to
        `materialise_node`. The mutation is what proves it discriminates.
        """
        built = []
        real_text = materialiser.NODE_CLASSES["TextNode"]
        real_if = materialiser.NODE_CLASSES.pop("IfNode")

        def recording_text(*args, **kwargs):
            built.append(kwargs)
            return real_text(*args, **kwargs)

        materialiser.NODE_CLASSES["TextNode"] = recording_text
        try:
            with self.assertRaises(LookupError) as caught:
                _tree("{% if a %}x{% endif %}", native=True)
            self.assertIn("IfNode", str(caught.exception))
            self.assertIn("NODE_CLASSES", str(caught.exception))
        finally:
            materialiser.NODE_CLASSES["TextNode"] = real_text
            materialiser.NODE_CLASSES["IfNode"] = real_if
        self.assertEqual(built, [], "the parent's children were built before the registry was read")


class RegistrySubscriptionTests(unittest.TestCase):
    """`NODE_CLASSES[class_name]`, not `dict.__getitem__`.

    `materialise_node` subscribes — `cls = NODE_CLASSES[class_name]` at
    `python/django_rust_templates/engine/parser.py:347` — and subscription goes
    through the *type's* `__getitem__`. `NODE_CLASSES` is a plain dict in this tree,
    so the two spellings agree on every input the corpus reaches; they stop
    agreeing the moment it is a dict **subclass** that overrides `__getitem__`,
    which is exactly the thing a project is allowed to substitute. PyO3's
    `PyDict::get_item` reads the concrete dict storage and never calls the
    override; `Bound::<PyAny>::get_item` is `PyObject_GetItem` and does.

    **Measured on the round-1 build, which is why the rust walk no longer casts the
    registry down:** with a subclass whose `__getitem__` answers with a substituted
    class for `"TextNode"`, the Python path built the substitution and the native
    path built the real node.

    **Why nothing in this file caught that, stated correctly.** An earlier draft of
    this docstring said no fingerprint could show it "because the names are
    identical". Both halves are wrong. `Tagging` is a *subclass*, so it is a distinct
    class object with its own `__name__`, and this file's `fingerprint` carries node
    classes **whole** — `test_two_node_classes_with_one_name_are_told_apart` is the
    row that proves it, and it exists precisely so two classes of one name are told
    apart. A fingerprint *would* have shown the divergence. What let it through was
    that **every other row in this file uses the real `NODE_CLASSES`**, and the corpus
    never substitutes one; the defect needed a row that swapped the mapping out, and
    there was none. That is the gap this class closes, and the two rows below are
    direct assertions on subscription rather than a fingerprint comparison, so they
    name the mechanism rather than the symptom.
    """

    def _substituted(self, native):
        """Build `"hello"` with `NODE_CLASSES` replaced by an overriding subclass.

        Returns the keys the override was asked for and the class the node came
        out as, so a row can distinguish "the override ran and returned the real
        class" from "the override was never consulted" — which is the whole
        difference between subscription and `PyDict::get_item`, and the reason the
        second is not good enough.
        """
        seen = []
        real_registry = materialiser.NODE_CLASSES
        real_text = real_registry["TextNode"]

        class Tagging(real_text):
            """A *subclass* of the real `TextNode`, so a distinct class object.

            Reachable from the registry only through the override — nothing else in
            the tree hands it out — which is what makes `type(tree[0])` a witness to
            whether `__getitem__` ran.
            """

        class Overriding(dict):
            def __getitem__(self, key):
                seen.append(key)
                if key == "TextNode":
                    return Tagging
                return super().__getitem__(key)

        materialiser.NODE_CLASSES = Overriding(real_registry)
        try:
            tree = _tree("hello", native=native)
        finally:
            materialiser.NODE_CLASSES = real_registry
        return seen, Tagging, type(tree[0])

    def test_the_native_walk_reaches_an_overridden_getitem(self):
        seen, tagging, built = self._substituted(native=True)
        self.assertIn("TextNode", seen, "the registry was read without calling __getitem__")
        self.assertIs(built, tagging, "the override's answer was ignored")

    def test_the_native_walk_lets_a_non_keyerror_out_of_getitem(self):
        # `materialise_node` catches `KeyError` and nothing else, so a `__getitem__`
        # that raises something else must not be rewritten into the registry
        # message. A walk that caught every `PyErr` there would report
        # "no materialiser for node class 'TextNode'" for a `RuntimeError` from a
        # registry that plainly has one.
        real_registry = materialiser.NODE_CLASSES

        class Raising(dict):
            def __getitem__(self, key):
                if key == "TextNode":
                    raise RuntimeError("the registry is closed")
                return super().__getitem__(key)

        materialiser.NODE_CLASSES = Raising(real_registry)
        try:
            with self.assertRaises(RuntimeError) as caught:
                _tree("hello", native=True)
        finally:
            materialiser.NODE_CLASSES = real_registry
        self.assertIn("the registry is closed", str(caught.exception))

    def test_the_python_path_is_the_control(self):
        # If this fails the fixture is wrong, not the walk: Python's subscription
        # is the behaviour the native one has to match, and it reaches the override
        # by construction.
        seen, tagging, built = self._substituted(native=False)
        self.assertIn("TextNode", seen)
        self.assertIs(built, tagging)


class ConstructionOrderTests(unittest.TestCase):
    """The three contract orderings in `materialise.rs`, through **both** branches.

    `materialise.rs` names three orderings as contracts and says each "gets a row
    when the task that could break it lands". Task 3 pinned the first one only
    indirectly, in the root-list loop. Task 4 is the task that builds a parent in
    Rust, so this is where the other two stop being Python's promise and become
    this module's:

    2. children are materialised **before** the parent is constructed;
    3. `token` and `origin` are attached **after** construction, and not passed to it.

    **Contract 2's second half — attributes decoded after the children exist — runs
    through Task 4 already, by way of the fallback.** `AttrValue::NodeListRef` is not
    one of the primitive arms, so `attr_to_py` hands it to `_decode_value` — with the
    `child_lists` this function built. So the ordering is not deferred to Task 5's arm;
    Task 5 inherits it, and the third row below pins it now. **An earlier draft of this
    docstring said a row here would be "a row about nothing" and that was wrong**, which
    is worth recording because the reasoning looked right: the `NodeListRef` arm is
    Task 5's, but the *value* reaches `_decode_value` today.

    **Contract 3 gets one row, and a second one was written and deleted.** The probe
    that reads `self.token` inside `__init__` looks like the natural way to express
    "attached after", and it cannot fail against this walk. The narrow reason matters:
    an instance does exist while `__init__` runs, so the claim is not that there is
    nothing to probe — it is that **the Rust path is handed the instance only once
    `factory.call` has returned**, and holds no reference to it before then, so no edit
    to `materialise_one` can have put a value on it earlier. What *is* falsifiable is the
    call, and that is what the surviving row asserts.

    Every row runs `native in (True, False)` as `subTest`s rather than twice in two
    methods: the claim is that the two paths agree, and a row that only ever ran one
    of them would still pass on the day they stopped agreeing.
    """

    def test_the_children_exist_before_the_parent_is_constructed(self):
        # `{% for %}` rather than `{% if %}` on purpose. `ForNode`'s body nodelist is
        # **named** (`nodelist_loop`), where `IfNode`'s is positional-only, so a walk
        # that constructed the parent first would hand `ForNode` an *empty* nodelist
        # and get a clean, informative failure — `("parent", 0)` — instead of the
        # `TypeError` `IfNode` gives for a missing `conditions_nodelists`. A row whose
        # mutation is a `TypeError` proves the reordering happened; a row whose
        # mutation is the wrong *number* proves the ordering the contract is about.
        for native in (True, False):
            with self.subTest(native=native):
                events = []
                real_for = materialiser.NODE_CLASSES["ForNode"]
                real_text = materialiser.NODE_CLASSES["TextNode"]

                class Watching(real_for):
                    def __init__(self, **kwargs):
                        # Read the body *inside* the constructor, which is what
                        # `ExtendsNode.__init__` does and what makes the ordering
                        # load-bearing rather than cosmetic.
                        events.append(("parent", len(kwargs["nodelist_loop"])))
                        super().__init__(**kwargs)

                def watching_text(*args, **kwargs):
                    events.append(("child", None))
                    return real_text(*args, **kwargs)

                materialiser.NODE_CLASSES["TextNode"] = watching_text
                materialiser.NODE_CLASSES["ForNode"] = Watching
                try:
                    _tree("{% for x in y %}z{% endfor %}", native=native)
                finally:
                    materialiser.NODE_CLASSES["TextNode"] = real_text
                    materialiser.NODE_CLASSES["ForNode"] = real_for

                self.assertEqual(
                    events,
                    [("child", None), ("parent", 1)],
                    "the parent was constructed before its children were built",
                )

    def test_the_attributes_resolve_against_a_populated_child_list(self):
        """Contract 2's second half, through `attr_to_py`'s fallback arm.

        `{% if %}` is the shape that can see this at all: its branch nodelists are
        **positional** — empty name, not passed to `cls` — so `IfNode` reaches them
        through `conditions_nodelists`, which carries `AttrValue::NodeListRef` for
        each branch rather than the list itself (`crates/template-core/src/parser/
        tags/if.rs:121`). Decoded against a `child_lists` that is still empty, that
        reference is not an empty answer but a loud one — measured, with the mutation
        below: `LookupError: <NodeListRef 0> is out of range: the enclosing node has
        0 nodelist(s)`.

        **`NodeListRef` is Task 5's arm and the ordering is Task 4's**, which is why
        this row belongs here: the value travels `attr_to_py` → `fallback_attr` →
        `Bridge::DecodeValue` → `_decode_value` with the `child_lists` built above it,
        and the fallback is not a reason to leave the ordering unpinned. Task 5
        replaces the *arm* and inherits the *row*.

        The Python path is the control throughout, and the same claim is asserted one
        level down in `tests/test_materialise.py:464`
        (`test_a_nodelist_ref_resolves_because_children_are_built_first`), which calls
        `materialise_node` with a hand-built descriptor. This row is that claim driven
        through `_native.compile_template`, which is the only way to reach the Rust
        walk.
        """
        for native in (True, False):
            with self.subTest(native=native):
                tree = _tree("{% if a %}x{% endif %}", native=native)

                self.assertIs(type(tree[0]), materialiser.NODE_CLASSES["IfNode"])
                condition, branch = tree[0].conditions_nodelists[0]
                self.assertTrue(hasattr(condition, "eval"), "the condition half is not a condition")
                # The witness: a real, *populated* nodelist, not the empty one an
                # attributes-before-children walk would have resolved to — or failed
                # on outright, which is the measured failure above.
                self.assertIsInstance(branch, NodeList)
                self.assertEqual(len(branch), 1, "the reference resolved to an empty child list")
                self.assertIs(type(branch[0]), materialiser.NODE_CLASSES["TextNode"])

    def test_the_token_and_origin_are_attached_after_the_constructor_runs(self):
        for native in (True, False):
            with self.subTest(native=native):
                seen = []
                real_text = materialiser.NODE_CLASSES["TextNode"]

                class Probing(real_text):
                    def __init__(self, *args, **kwargs):
                        # `Node` declares `token = None` as a *class* attribute, so a
                        # probe built on `hasattr(self, "token")` is True on a bare
                        # instance and cannot fail. What the walk cannot do is have the
                        # values present when `cls(**kwargs)` is called — and the way to
                        # see that is to look at what it passes, read from inside the
                        # constructor the way the walk's own call would run it.
                        seen.append(sorted(kwargs))
                        super().__init__(*args, **kwargs)

                materialiser.NODE_CLASSES["TextNode"] = Probing
                try:
                    tree = _tree("hello", native=native)
                finally:
                    materialiser.NODE_CLASSES["TextNode"] = real_text

                # Contract 3 stated as a fact about the *call*: `token` and `origin`
                # are the walk's to attach afterwards, never constructor arguments.
                # Passing them is the realistic edit — one `kwargs.set_item` in the
                # attribute loop — and it is what this row refuses. A row that instead
                # read `self.token` *inside* `__init__` would assert something no edit
                # to the walk can break: an instance does exist while `__init__` runs,
                # but the walk is handed it only once `factory.call` has returned and
                # holds no reference before then. That row was written, found
                # unfalsifiable and dropped, which is the standard Step 2 holds these to.
                self.assertEqual(seen, [["s"]], "the constructor was handed more than the descriptor's attributes")
                # And the pair really is on the node once the walk is done, so this is
                # "attached later" rather than "never attached".
                self.assertIsInstance(tree[0].token, Token)
                self.assertIs(tree[0].origin, render.UNKNOWN_ORIGIN)
```

- [ ] **Step 2: Run to verify failure**

Run: `scripts/run_unit_tests.sh tests.test_materialiser_parity.RegistryOrderTests tests.test_materialiser_parity.UnknownClassTests -v`

**Measured: every row passes on arrival, and the failure this step asks for was
pre-empted.** Step 3 was done first, so `_unknown_node_class` already exists and the
`UnknownClassTests` rows have their target. The `RegistryOrderTests` rows pass for the
reason the class docstring gives — the ordering they assert is one `materialise_node`
already has, and at Task 3 every node still goes through it. **A row that cannot be
made to fail by the change it guards is not evidence**, so the discrimination is
established the other way: by mutation. Each was run against this tree on its own,
with the crate rebuilt each time and `materialise.rs` restored from a byte copy
afterwards, and the *unmutated* build is 36 rows, OK.

| row | mutation (the smallest edit that reintroduces the defect) | outcome |
| --- | --- | --- |
| `RegistryOrderTests.test_a_rebind_during_a_child_is_not_seen_by_the_parent` | move the whole registry read below the children loop | red — `Marker is not …IfNode : the parent read the registry after its children`; the Python control row stays green |
| `UnknownClassTests.test_the_native_walk_raises_before_any_child_is_built` | the same move | red — `[{'s': 'x'}] != [] : the parent's children were built before the registry was read` |
| `RegistrySubscriptionTests.test_the_native_walk_reaches_an_overridden_getitem` | put `cast_into::<PyDict>()` and `PyDict::get_item` back | red — `'TextNode' not found in [] : the registry was read without calling __getitem__`; the Python control row stays green |
| `RegistrySubscriptionTests.test_the_native_walk_lets_a_non_keyerror_out_of_getitem` | the same | red — `RuntimeError not raised` |
| `ConstructionOrderTests.test_the_children_exist_before_the_parent_is_constructed` | build the children as empty nodelists and construct the parent from them | red — `[('parent', 0)] != [('child', None), ('parent', 1)]`, and only the `native=True` subTest |
| `ConstructionOrderTests.test_the_attributes_resolve_against_a_populated_child_list` | bind `child_lists` to `PyList::empty(py)` instead of the built children | red — `LookupError: <NodeListRef 0> is out of range: the enclosing node has 0 nodelist(s)`, and only the `native=True` subTest |
| `ConstructionOrderTests.test_the_token_and_origin_are_attached_after_the_constructor_runs` | add `kwargs.set_item("token", py.None())` before the factory call | red — `TypeError: TextNode.__init__() got an unexpected keyword argument 'token'` |

**And the guard, which is the one mutation here that is not a synthetic reordering but a
preview of Task 5.** Deleting `if desc.slot.is_some() { … }` outright — the edit Task 5
makes on purpose — turns exactly two corpus rows red, `cycle_twice` and `resetcycle`, and
**it is worth reading what each says**, because the first draft of this plan described
both wrongly. `resetcycle` raises `LookupError: <NodeSlot 0> is an empty reference-table
slot`. `cycle_twice` is a *fingerprint* disagreement about a token string — Python
`'cycle c'`, native `"cycle 'a' 'b' as c"` — so the corpus row caught it through
`token.contents` and not, as an earlier draft claimed, because "the corpus row renders".
`TwoPathParityTests` never renders. The two nodes cycling apart is real and **invisible to
every row in this file**.

**The third and fourth rows are the ones round 1 of this task's review asked for**, and
the second is the row it said the fixture for. The `UnknownClassTests` native row in
particular had **no Rust-side coverage before**: the row above it calls
`materialise_node`, so nothing in the file reached `loud(tree, Bridge::UnknownNodeClass,
…)` at all, and a walk that never consulted the registry passed everything.

- [ ] **Step 3: Append the registry-error helper to `parser.py`**

At the end of the file.

```python
def _unknown_node_class(class_name):
    """Raise the registry error, from the one place that spells it.

    `materialise_node` raises this message and the Rust walk raises the same
    condition; the string lives here so that a test which asserts it asserts one
    string rather than two that agree today.

    **`from None` is load-bearing.** The caller is `materialise_node`'s
    `except KeyError` arm, so a bare `raise` here would chain the `KeyError` as
    `__context__` and print it under the `LookupError` — which is not what the
    inline version did. `raise … from None` sets `__suppress_context__` wherever it
    is raised, so the *message* and the *suppression* come out exactly as the inline
    version's did; measured on both spellings, `__context__` is the `KeyError` and
    `__suppress_context__` is True.

    **The traceback is not identical, and this frame is the difference.**
    Extracted, the helper adds `_unknown_node_class` to the end of the frame list —
    `['<module>', 'materialise_node', '_unknown_node_class']` where the inline
    version gave `['<module>', 'materialise_node']`. That is the price of spelling
    the message once. The exception type, the message and the chaining are what the
    contract is about and all three are unchanged; nothing asserts a frame list.
    """
    raise LookupError(
        f"no materialiser for node class {class_name!r}; add it to NODE_CLASSES"
    ) from None
```

Point `materialise_node`'s `except KeyError` arm at it, so there is one spelling.

- [ ] **Step 4: Implement**

Three edits to `crates/template-py/src/materialise.rs`.

**4a. The imports.** Replace

```rust
use pyo3::prelude::*;
use template_core::parser::desc::{NodeDesc, NodeListDesc};
```

with

```rust
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use template_core::parser::desc::{AttrValue, NodeDesc, NodeListDesc};
```

**4b. Declare the four variants.** Replace the `Bridge` enum and its `name` with
these — the variants and the arms that call them are one edit, per the rule in the
enum's own doc comment. `Bridge::MaterialiseNode` is already there from Task 3.

```rust
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Bridge {
    /// The host node's own object. The only bridge that exists at Task 3, and the
    /// only node-level one that is permanent: the host node's descriptor validation
    /// and error messages are asserted by tests that should keep reading the strings
    /// they were written against.
    MaterialiseNode,
    /// `_decode_value`, for the attribute shapes [`attr_to_py`] does not build.
    /// Task 7 deletes this variant with [`fallback_attr`].
    DecodeValue,
    /// `_decode_token`, so a node's `token` is the same `Token` the wire path builds.
    DecodeToken,
    /// `mark_safe`, for an `AttrValue::SafeStr`. A `{% verbatim %}` body that
    /// arrived as a plain `str` would escape itself at render time.
    MarkSafe,
    /// `_unknown_node_class`, so the registry message has one spelling.
    UnknownNodeClass,
}

impl Bridge {
    /// The name of the Python entry point this variant stands for.
    ///
    /// The enumeration Task 8's pin counts is **not** here, and the comment on
    /// [`Bridge`] says why. When it lands it will read **eight before Task 7 and
    /// ten after**, because that task deletes [`Bridge::DecodeValue`] and adds three; the row's
    /// number changing is the point, since it is one edit in Rust that a reviewer
    /// reads rather than a substring that has to be kept out of a comment.
    pub(crate) fn name(self) -> &'static str {
        match self {
            Bridge::MaterialiseNode => "materialise_node",
            Bridge::DecodeValue => "_decode_value",
            Bridge::DecodeToken => "_decode_token",
            Bridge::MarkSafe => "mark_safe",
            Bridge::UnknownNodeClass => "_unknown_node_class",
        }
    }
}
```

**4c. The three attribute helpers, the rewritten `materialise_one`, and `loud`.**
Replace `materialise_one`'s body and append the rest beside `fallback_node`.

```rust
fn materialise_one(
    py: Python<'_>,
    tree: &Bound<'_, PyAny>,
    desc: &NodeDesc,
    origin: &Bound<'_, PyAny>,
    resolve_ref: &Bound<'_, PyAny>,
    register_ref: &Bound<'_, PyAny>,
    parser: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    // Checked before the registry so that the reserved name cannot be captured
    // by an entry someone adds later, and so the error for a genuinely unknown
    // class name stays a `LookupError` — the same order `materialise_node` uses.
    if desc.class_name == template_core::parser::desc::HOST_NODE {
        return fallback_node(py, tree, desc, origin, resolve_ref, register_ref, parser);
    }

    // **Task 4's guard, and Task 5 deletes it.** `{% cycle %}`'s slot table is
    // Task 5's work, but it is part of `materialise_node`'s contract from the
    // moment this module builds *any* node. Left to Rust, `{% cycle a b as c %}`
    // would be a second `CycleNode` instead of the shared one — `CycleNode.render`
    // keys `context.render_context` by the object, so the two would cycle
    // separately — and `{% resetcycle %}`'s `NodeSlot` attribute would read an
    // empty table and raise. **The two fail differently, and only one of them is
    // visible to this tree at all.**
    //
    // The `{% resetcycle %}` one is **loud**: measured with this guard deleted, it
    // is `LookupError: <NodeSlot 0> is an empty reference-table slot` out of
    // `_decode_value` (`python/django_rust_templates/engine/parser.py:750`). The
    // `{% cycle %}` one is **quiet at render time and visible only through the
    // token**: `extend_nodelist` writes `node.token` at every append, so Python's
    // one shared object ends up carrying the *last* mention's token (`'cycle c'`),
    // where two Rust-built nodes leave the first with its own
    // (`"cycle 'a' 'b' as c"`). Measured, the two corpus rows that went red when
    // this guard was removed were `cycle_twice` and `resetcycle`, and **the
    // `cycle_twice` row was the fingerprint disagreeing about that string** —
    // `TwoPathParityTests` never renders, so nothing here would have seen the two
    // nodes cycling apart, which is the bug that actually matters at run time.
    // Handing the node back to Python is the fallback design applied to the one
    // shape whose *identity* is not local to the node.
    if desc.slot.is_some() {
        return fallback_node(py, tree, desc, origin, resolve_ref, register_ref, parser);
    }

    // **Before the children**, which is the review's finding: `materialise_node`
    // reads the registry at
    // `python/django_rust_templates/engine/parser.py:347` and builds children at
    // `python/django_rust_templates/engine/parser.py:372`, so a factory that
    // rebinds the registry while a child is built is seen by the child's siblings
    // and not by this parent. Reading it after the children would build a
    // different tree with the same class names.
    let registry = tree.getattr("NODE_CLASSES")?;
    // **Subscription on the object, never `PyDict::get_item`.** `NODE_CLASSES` is a
    // plain dict today, so the two agree on every input the corpus reaches — and they
    // stop agreeing as soon as it is a dict **subclass** that overrides `__getitem__`:
    // `PyDict::get_item` reads the concrete dict storage and never calls the override,
    // while `NODE_CLASSES[class_name]` at
    // `python/django_rust_templates/engine/parser.py:347` does. Measured with a
    // subclass answering `"overridden"` for every key: the Python path built the
    // override's value, a `get_item` walk built the real node, and the two trees
    // differed in the class of a node with the same name. `get_item` on
    // `Bound<PyAny>` is `PyObject_GetItem`, which *is* the subscription — so the
    // registry is never cast down to `PyDict` here.
    let factory = match registry.get_item(desc.class_name.as_str()) {
        Ok(factory) => factory,
        // **`KeyError` only**, which is exactly what `materialise_node`'s
        // `except KeyError` catches; anything else a `__getitem__` raises propagates
        // as itself rather than being rewritten into the registry message.
        Err(error) if error.is_instance_of::<pyo3::exceptions::PyKeyError>(py) => {
            // The message belongs to Python — it is asserted by tests that should
            // keep reading the string they were written against — so the error path
            // calls into the module rather than spelling it a second time.
            return Err(loud(
                tree,
                Bridge::UnknownNodeClass,
                (desc.class_name.as_str(),),
            ));
        }
        Err(error) => return Err(error),
    };

    // Task 5 inserts the `{% cycle %}` slot arm here, between the registry read
    // and the children, because `materialise_node` checks the table before
    // building anything.

    // Children first, completely. `ExtendsNode.__init__` walks the nodelist it is
    // handed, and an `AttrValue::NodeListRef` names a position in `child_lists`
    // below, so a node built before its children is a node that read an empty
    // subtree.
    let mut children: Vec<(String, Py<PyAny>)> = Vec::with_capacity(desc.nodelists.len());
    for (name, child) in &desc.nodelists {
        let built = materialise_native(py, tree, child, origin, resolve_ref, register_ref, parser)?;
        children.push((name.clone(), built));
    }
    let child_lists = PyList::new(py, children.iter().map(|(_, node)| node))?;

    let kwargs = PyDict::new(py);
    for (name, node) in &children {
        // A child nodelist whose **name is empty** is positional only: it is
        // reachable through a `NodeListRef` and is not passed to `cls`.
        // `IfNode` is why — its `__init__` takes exactly one parameter,
        // `conditions_nodelists`, and holds the branch nodelists inside that.
        if !name.is_empty() {
            kwargs.set_item(name, node)?;
        }
    }
    // Attributes after the children, and the order is load-bearing: a
    // `NodeListRef` resolves against `child_lists`, which must be populated.
    for (name, value) in &desc.attrs {
        kwargs.set_item(
            name,
            attr_to_py(py, tree, value, resolve_ref, parser, &child_lists)?,
        )?;
    }

    let node = factory.call((), Some(&kwargs))?;

    // After construction, never before: `__init__` runs first and some of them
    // read the subtree.
    node.setattr("token", decode_token_or_none(py, tree, desc)?)?;
    node.setattr("origin", origin)?;
    // `register_ref` is reached only by the host-node arm above at this stage;
    // Task 5 registers the `{% cycle %}` slot with it, in the block marked above.
    Ok(node.unbind())
}

/// A descriptor attribute as the Python object the node's `__init__` wants.
fn attr_to_py(
    py: Python<'_>,
    tree: &Bound<'_, PyAny>,
    value: &AttrValue,
    resolve_ref: &Bound<'_, PyAny>,
    parser: &Bound<'_, PyAny>,
    child_lists: &Bound<'_, PyList>,
) -> PyResult<Py<PyAny>> {
    // `bool` before `int`, and it is not an optimisation: `bool` is an `int`
    // subclass, and an arm that caught it as an integer would turn
    // `{% autoescape on %}`'s `setting` into `0`/`1` — which compares equal and
    // passes any `assertEqual` while being the wrong type.
    match value {
        AttrValue::Bool(flag) => Ok((*flag).into_pyobject(py)?.to_owned().into_any().unbind()),
        AttrValue::Int(number) => Ok((*number).into_pyobject(py)?.into_any().unbind()),
        AttrValue::Float(number) => Ok((*number).into_pyobject(py)?.into_any().unbind()),
        AttrValue::Str(text) => Ok(text.as_str().into_pyobject(py)?.into_any().unbind()),
        AttrValue::None => Ok(py.None()),
        // A `SafeString`, and the subtype is the whole point: a `{% verbatim %}`
        // body's text must not be escaped when the node finally renders. This is
        // the same call `_decode_value` makes for the same variant
        // (`python/django_rust_templates/engine/parser.py:757`), reached without
        // building the wire object first.
        AttrValue::SafeStr(text) => call_bridge(tree, Bridge::MarkSafe, (text.as_str(),)),
        AttrValue::BigInt(text) => {
            // `builtins.int(text)`, **never** `py.eval`. The text is a validated
            // decimal produced by `pynum::python_int` — an optional ASCII sign
            // then ASCII digits — so the only thing it can denote is a number.
            // The tempting `py.eval("int(%s)" % text)` would accept the text as
            // *code*, which no lint catches; this form cannot, whatever it says.
            Ok(py
                .import("builtins")?
                .getattr("int")?
                .call1((text.as_str(),))?
                .unbind())
        }
        other => fallback_attr(py, tree, other, resolve_ref, parser, child_lists),
    }
}

/// The shapes this module does not build yet, handed to the Python decoder.
///
/// The wire object is built for this one value by the *existing* producer and
/// decoded by the *existing* decoder, so a shape still on the fallback has
/// exactly the behaviour it had before this module existed.
///
/// **Task 7 deletes this function and `Bridge::DecodeValue` together.** It is the
/// only bridge that is a per-value fallback rather than a coarse whole-object one,
/// and after it goes there is no arm anywhere in the `match` above that says "let
/// Python do this one".
fn fallback_attr(
    py: Python<'_>,
    tree: &Bound<'_, PyAny>,
    value: &AttrValue,
    resolve_ref: &Bound<'_, PyAny>,
    parser: &Bound<'_, PyAny>,
    child_lists: &Bound<'_, PyList>,
) -> PyResult<Py<PyAny>> {
    let wire = crate::attr_value_to_py(py, value)?;
    call_bridge(
        tree,
        Bridge::DecodeValue,
        (wire, resolve_ref, parser, child_lists),
    )
}

fn decode_token_or_none(
    py: Python<'_>,
    tree: &Bound<'_, PyAny>,
    desc: &NodeDesc,
) -> PyResult<Py<PyAny>> {
    match &desc.token {
        None => Ok(py.None()),
        Some(token) => call_bridge(tree, Bridge::DecodeToken, (crate::token_to_py(py, token)?,)),
    }
}
```

Add `loud` beside `fallback_node`:

```rust
/// Call a Python helper that always raises, and hand back what it raised.
///
/// The `Ok` arm is `unreachable!` rather than a silent success: a helper that
/// returned would mean the message had stopped being one spelling, and a walk
/// that carried on holding a value where an error was owed is the failure this
/// whole module exists to avoid.
fn loud<'py>(
    tree: &Bound<'py, PyAny>,
    bridge: Bridge,
    args: impl pyo3::call::PyCallArgs<'py>,
) -> PyErr {
    match call_bridge(tree, bridge, args) {
        Err(error) => error,
        Ok(_) => unreachable!("{} always raises", bridge.name()),
    }
}
```

**`loud` takes a `Bridge` and not a name**, so that the three error helpers it
reaches are enumerated with the rest. A `&str` parameter would have been the one hole
in the enumeration big enough to drive a fallback through.

**Four things in this step were written from the API rather than from the tree, and
the compiler was the least of the three checks they got.** Two are real compile
errors; one is a real semantic defect that compiles cleanly; one was checked and is
not an error at all. All four were re-measured against the tree after being written
here, because an earlier draft claimed the compiler had caught every API mistake in
this step and that was not true.

- `Bound::cast_into::<PyDict>()`, **not** `downcast_into` — a real E0599, and the one
  the compiler caught, on the first `cargo check`. **The line is gone now**, and not
  because it was fixed: round 1 of this task's review found that casting the registry
  down at all was the defect, which is the next bullet.
- **`PyDict::get_item` compiles and is wrong**, and no compiler or lint can say so.
  It reads the concrete dict storage, so it never calls a dict subclass's
  `__getitem__` — where `NODE_CLASSES[class_name]` at
  `python/django_rust_templates/engine/parser.py:347` does. **Round 1 measured the
  divergence**: with `NODE_CLASSES` replaced by a subclass, the Python path built the
  override's class and this step's walk built the real one, two trees differing in the
  class of a node with an identical name. The step now takes `getattr("NODE_CLASSES")`
  as a `Bound<PyAny>` and calls `get_item` on *that* — `PyObject_GetItem`, which is the
  subscription — matching only `PyKeyError` so a non-`KeyError` from a `__getitem__`
  propagates as itself, which is what `materialise_node`'s `except KeyError` does. The
  cast is what had to go; `PyDict` is still imported for `kwargs`.
- `attr_to_py(…, parser, …)`, **not** `parser.bind(py)`: `materialise_one` already
  holds `parser` as a `&Bound<PyAny>`, and `bind` is a method of `Py<T>` that `Bound`
  does not have. Real E0599; verified by putting the plan's spelling back.
- `fn loud<'py>(…, args: impl PyCallArgs<'py>)`, **not** `impl PyCallArgs<'_>`. Real
  E0658 — **anonymous lifetimes in `impl Trait` are unstable**, which is the actual
  reason and not the lifetime-unification one an earlier draft of this paragraph gave.
  The named `'py` is then needed on `tree` as well, which is why the two carry it
  together.
- **`PyDict::get_item(&desc.class_name)` takes a `&String` as happily as a `&str`.**
  An earlier draft of this step claimed it must be `desc.class_name.as_str()`; it does
  not. Verified by putting the `&String` form back and running `cargo check` — exit 0.
  Struck from the list of *errors* and kept here only so that the plan is not read as
  having claimed a constraint the compiler does not impose.

`crate::token_to_py` already exists at `crates/template-py/src/lib.rs:753` — it is the
producer `node_desc_to_py` uses for the same field
(`crates/template-py/src/lib.rs:1058`). Nothing is extracted and nothing moves, so
this step touches no anchor: the token wire form keeps one spelling by *calling* the
one producer rather than by growing a second. `attr_value_to_py`
(`crates/template-py/src/lib.rs:763`), `node_desc_to_py`
(`crates/template-py/src/lib.rs:1058`) and `nodelist_desc_to_py`
(`crates/template-py/src/lib.rs:1104`) are private `fn`s in the same crate and
reachable from this module as they are.

- [ ] **Step 5: Run the tests, then the gate**

Run: `scripts/run_unit_tests.sh tests.test_materialiser_parity -v`, then the
four-command gate from Task 3 Step 8.

**Measured after this step**, all on the tree as left by the edits above:

- `scripts/run_unit_tests.sh tests.test_materialiser_parity` — OK. **The row count is
  deliberately not recorded**, here or anywhere: it moves with every row this plan
  adds, so a number would be churn rather than a check. The structural claim is the
  one that holds — this file is OK, and every row in it is a row some task's Step 1
  wrote.
- Full suite: one failure, and it is the pre-existing `.review/minijinja.md` anchor
  row (`test_the_review_records_are_excluded_for_the_same_reason_and_one_more`)
  that was red before any of this work.
- `cargo fmt --all --check` — clean.
- `cargo clippy --workspace --all-targets -- -D warnings` — exit 0;
  `cargo clippy -p template-py --no-default-features --all-targets -- -D warnings`
  — exit 0.
- `python -I scripts/check_python_anchors.py --check --frozen` — 1513 occurrences,
  875 of 875 manifest entries cited, **0 failures**, exit 0. Four entries were added
  by `--seed` (`python/django_rust_templates/engine/parser.py:750` and `:770`,
  `crates/template-py/src/lib.rs:1104` and `tests/test_materialise.py:464`); every
  citation this task wrote uses a full path, because `--frozen` does not accept the
  `parser.py:350` shorthand or a bare `:376` continuation. **The seed guesses the
  quoted text from the citing line and got `:750` wrong** — it is the `NodeSlot`
  branch, so the entry was corrected by hand to `is an empty reference-table slot`;
  a seeded entry that no test reads is still an entry the gate checks.

**No commit.** The switch is still `USE_NATIVE_MATERIALISER = False`, so nothing
here changes any default behaviour; the work stays in the working tree with the
rest of the plan's tasks.

---

### Task 5: References, the cycle slot, and the malformed descriptors

The three handle shapes, plus the `{% cycle %}` identity arm — Review Focus items
2 and 5. This is the task where the tree stops being structurally identical and
starts depending on *which* object is handed back.

**Declares:** `Bridge::MissingResolverError`, `Bridge::EmptySlotError` and
`Bridge::NodelistRefOutOfRangeError` — with `loud`, which Task 4 already introduced,
so only the three variants and their call sites are new here.

**Deletes Task 4's `desc.slot.is_some()` guard**, and that deletion is part of this
task's deliverable rather than cleanup. Task 4 could not build any node without it —
the guard is what keeps `{% cycle %}`'s shared object and `{% resetcycle %}`'s slot
attribute correct while Rust builds everything else — but with the slot arm in place
the guard is the one thing that would make the new arm unreachable. **The arm's own
tests are the check that the guard is gone**: `CycleIdentityTests` asserts the two
`{% cycle %}` mentions are the *same object*, which a surviving guard would satisfy
by handing the whole subtree back to Python, so the arm is also pinned from the Rust
side by `mod malformed`'s slot rows. If the guard is still there, the arm is dead code
with a live-looking call site.

**Files:**
- Modify: `crates/template-py/src/materialise.rs` (the arms, and `mod tests`)
- Modify: `python/django_rust_templates/engine/parser.py` (append four helpers)
- Test: `tests/test_materialiser_parity.py`

**Interfaces:**
- Produces: `attr_to_py` handles `Ref`, `NodeSlot`, `NodeListRef`; `resolve_handle`.
  `materialise_one` handles `desc.slot`. A `#[cfg(all(test, not(feature =
  "extension-module")))] mod malformed` of Rust rows.
- Consumes: `tree._missing_resolver_error`, `tree._empty_slot_error`,
  `tree._nodelist_ref_out_of_range_error`.

- [ ] **Step 1: Write the failing tests**

```python
class CycleIdentityTests(unittest.TestCase):
    """The second `{% cycle c %}` must be the *same object* as the first.

    `do_cycle` returns the identical node and `CycleNode.render` keys
    `context.render_context` by that object, so two equal-but-distinct nodes cycle
    independently — output that is right on the first iteration and wrong on the
    second. **A fingerprint cannot see this**: the two nodes fingerprint
    identically. That is why identity has its own rows.
    """

    def test_the_second_mention_is_the_first_object(self):
        tree = _tree("{% cycle 'a' 'b' as c %}{% cycle c %}", native=True)
        self.assertIs(tree[0], tree[1])

    def test_the_python_path_still_shares_the_object(self):
        # The control. If this fails the fixture is wrong, not the walk.
        tree = _tree("{% cycle 'a' 'b' as c %}{% cycle c %}", native=False)
        self.assertIs(tree[0], tree[1])

    def test_the_shared_object_carries_the_last_occurrence_token(self):
        # `extend_nodelist` does `node.token = token` at *every* append, so the
        # shared object ends up carrying the last occurrence's token and every
        # position reads that one.
        tree = _tree("{% cycle 'a' 'b' as c %}{% cycle c %}", native=True)
        self.assertEqual([node.token.contents for node in tree], ["cycle c", "cycle c"])

    def _cycle_with_the_node_arm_trapped(self, native):
        """Build the two-cycle fixture with `materialise_node` replaced by a trap.

        `materialise_node` is the one entry point `fallback_node` calls, and
        `call_bridge` reads it back off the module by name on every call — which is
        what makes it trappable, and what makes "the fallback did it" an error
        instead of a pass.
        """

        def refuse(*_args, **_kwargs):
            raise AssertionError("the walk fell back to `materialise_node`")

        real = materialiser.materialise_node
        materialiser.materialise_node = refuse
        try:
            return _tree("{% cycle 'a' 'b' as c %}{% cycle c %}", native=native)
        finally:
            materialiser.materialise_node = real

    def test_the_native_walk_shares_it_without_the_python_node_arm(self):
        # **The row that makes the three above mean something.** Identity alone
        # proves nothing about *which* walk produced it: the Python walk shares the
        # object too, so a fallback that rebuilt every node would leave them all
        # green — the gap the plan's Task 5 note names, and the reason `mod
        # malformed` carries slot rows on the Rust side.
        #
        # If the native branch is not live at all, `compile_template` takes the Python
        # arm, which reaches the same name — so this row is green only when the native
        # walk ran *and* did not fall back for either cycle.
        tree = self._cycle_with_the_node_arm_trapped(native=True)
        self.assertIs(tree[0], tree[1])

    def test_the_trap_fires_on_the_python_path(self):
        # The control, and the row that keeps the one above from being vacuous: a
        # trap that never fires is an assertion about nothing. The Python path *does*
        # reach `materialise_node`, so the same trap on it must raise.
        with self.assertRaises(AssertionError):
            self._cycle_with_the_node_arm_trapped(native=False)


class HostNodeIdentityTests(unittest.TestCase):
    """Review Focus item 1. Identity, and the token that must survive it.

    A host tag's node is built by Python during the parse and stored in the
    reference table. The walk must hand back **that object** — not a copy, not a
    new one — with this occurrence's token attached.

    **The claim is identity, so the assertion is `assertIs`**, and to make it the
    test has to know which object the host built — which means capturing it at
    construction. `SimpleBlockNode` is looked up as a module global by
    `compile_func`, so a subclass installed there records every one the tag builds.
    """

    def _held_node(self, source, native):
        """The object the host's tag factory built, and the tree that should hold it."""
        held = []

        class Held(SimpleBlockNode):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                held.append(self)

        real = library_mod.SimpleBlockNode
        library_mod.SimpleBlockNode = Held
        try:
            tree = _tree(source, engine=_hosting_engine(), native=native)
        finally:
            library_mod.SimpleBlockNode = real
        self.assertEqual(len(held), 1, "the fixture built no host node")
        return held[0], tree

    def test_the_walk_hands_back_the_hosts_own_object(self):
        held, tree = self._held_node("{% wrap %}body{% endwrap %}", native=True)
        self.assertIs(tree[0], held)

    def test_the_host_node_carries_this_occurrences_token_and_origin(self):
        held, tree = self._held_node("{% wrap %}body{% endwrap %}", native=True)
        # `extend_nodelist` attaches the token to a host descriptor exactly as it
        # does to any other block node, so materialisation has to re-attach it. A
        # bare `return resolve_ref(index)` hands back a node carrying whatever token
        # the *host* left on it — `None` — and a user tag's node is then the one
        # shape that renders tokenless in the debug traceback path.
        self.assertIsNotNone(held.token)
        self.assertEqual(held.token.contents, "wrap")
        self.assertIsNotNone(held.origin)
        self.assertIs(tree[0].token, held.token)

    def test_the_python_path_shares_the_object_too(self):
        # The control. If this fails the fixture is wrong, not the walk — the Python
        # materialiser returns `resolve_ref(index)` for a host descriptor, so
        # identity is the behaviour being preserved rather than introduced.
        held, tree = self._held_node("{% wrap %}body{% endwrap %}", native=False)
        self.assertIs(tree[0], held)
```

```python
class MalformedDescriptorTests(unittest.TestCase):
    """Review Focus item 5. The messages have one spelling, and both walkers use it.

    None of these is reachable from a template — Django refuses the source first —
    so the rows drive the helpers directly. What they cannot show is that the
    *Rust* arms call them; that is `mod malformed` in `materialise.rs`, and Task 8's
    census.

    **Four messages, five sites**, and three of the sites are already pinned by
    `tests/test_materialise.py` — as *fragments*, which is why the rows below assert
    the complete strings beside them.
    """

    def test_an_empty_slot_names_the_slot(self):
        with self.assertRaises(LookupError) as caught:
            materialiser._empty_slot_error("<NodeSlot 0>")
        self.assertEqual(
            str(caught.exception), "<NodeSlot 0> is an empty reference-table slot"
        )

    def test_a_missing_resolver_names_the_handle(self):
        with self.assertRaises(LookupError) as caught:
            materialiser._missing_resolver_error("<NodeRef 3>")
        self.assertEqual(
            str(caught.exception),
            "<NodeRef 3> needs a resolve_ref callback; none was passed to materialise",
        )

    def test_a_missing_enclosing_node_names_itself_not_materialise(self):
        # The odd one out, and pinned as odd: this path is reached from
        # `_decode_value` directly — a filter argument, say — where there is no
        # enclosing node at all, so the message names `_decode_value` and not
        # `materialise`.
        with self.assertRaises(LookupError) as caught:
            materialiser._missing_child_lists_error("<NodeListRef 0>")
        self.assertIn("none were passed to _decode_value", str(caught.exception))

    def test_an_out_of_range_nodelist_ref_names_the_arity(self):
        with self.assertRaises(LookupError) as caught:
            materialiser._nodelist_ref_out_of_range_error("<NodeListRef 2>", 1)
        self.assertEqual(
            str(caught.exception),
            "<NodeListRef 2> is out of range: the enclosing node has 1 nodelist(s)",
        )

    def test_the_host_arm_keeps_its_own_phrasing(self):
        # The host arm's empty-slot message says *which* descriptor, because the
        # failure is a broken table rather than a broken wire and the reader needs
        # to know which side to look at. Unifying it into the `NodeSlot` wording
        # would still pass `test_materialise.py`'s `assertIn("empty
        # reference-table slot")` while losing the distinction.
        with self.assertRaises(LookupError) as caught:
            materialiser._empty_slot_error("the HostNode descriptor's <NodeRef 0>")
        self.assertIn("the HostNode descriptor's", str(caught.exception))

    def test_the_python_decoder_uses_the_same_message(self):
        # The other half of "one spelling": these are not new strings added
        # *beside* `_decode_value`'s.
        with self.assertRaises(LookupError) as caught:
            materialiser._decode_value(_native.NodeSlot(0), None, None, None)
        self.assertEqual(
            str(caught.exception),
            "<NodeSlot 0> needs a resolve_ref callback; none was passed to materialise",
        )

    def test_an_empty_slot_over_a_real_table_is_loud(self):
        with self.assertRaises(LookupError) as caught:
            materialiser._decode_value(_native.NodeSlot(0), lambda index: None, None, None)
        self.assertEqual(
            str(caught.exception), "<NodeSlot 0> is an empty reference-table slot"
        )

    def test_a_nodelist_ref_with_no_enclosing_node_keeps_its_own_message(self):
        # The fifth site, driven through the decoder rather than the helper, so that
        # a helper wired to the wrong arm is red here.
        with self.assertRaises(LookupError) as caught:
            materialiser._decode_value(_native.NodeListRef(0))
        self.assertIn("none were passed to _decode_value", str(caught.exception))

    def test_an_out_of_range_ref_through_the_decoder_says_out_of_range(self):
        with self.assertRaises(LookupError) as caught:
            materialiser._decode_value(_native.NodeListRef(3), None, None, [None])
        self.assertIn("is out of range", str(caught.exception))
        self.assertIn("1 nodelist(s)", str(caught.exception))

    def test_the_out_of_range_context_is_suppressed(self):
        # Asserted on the exception object, because `str()` is identical either
        # way and a traceback is not what a test can hold. The helpers *raise*
        # rather than return, which is why `from None` belongs inside
        # `_nodelist_ref_out_of_range_error` and not at its call site: that site
        # sits in an `except IndexError:`, so a `raise helper(...) from None`
        # there never runs — the helper raises first — and the `IndexError`
        # survives as `__context__`, printing `list index out of range` under a
        # message that has already said what went wrong.
        with self.assertRaises(LookupError) as caught:
            materialiser._decode_value(_native.NodeListRef(3), None, None, [None])
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
```

and in `materialise.rs`, beside the `walk_depth` module's idiom:

```rust
#[cfg(all(test, not(feature = "extension-module")))]
mod malformed {
    //! The descriptors no template can produce.
    //!
    //! `{% resetcycle %}{% resetcycle c %}` is refused by Django before a
    //! descriptor exists, and a `HostNode` carrying a nodelist cannot be built by
    //! the parser at all. So these arms are reachable only from a *malformed*
    //! wire, which is exactly the case a Rust walk must be as loud about as the
    //! Python one — and exactly the case no Python-driven test can reach.
    //!
    //! **They call `materialise_one` and `attr_to_py`, never the helpers they
    //! reach.** Driving `resolve_handle` directly would prove that helper's branch
    //! and say nothing about whether `AttrValue::NodeSlot` dispatches to it with
    //! `empty_is_loud = true`. Exhaustive matching forces an arm to *exist*; it
    //! does not force the arm to be *right*. The two arguments that differ between
    //! the handles — `empty_is_loud`, and which helper is called — are the whole
    //! content of the arm, so the arm is the thing under test.

    use super::*;
    use template_core::parser::desc::NodeRef;

    /// The shipped `engine.parser`, which is where the messages live.
    ///
    /// The interpreter in this test binary has no Django settings configured and
    /// `python/` is not on its path, so both are set up here. The path insert is
    /// unconditional and membership-checked; only the settings call is guarded,
    /// because `settings.configured` says nothing about whether *this*
    /// interpreter can import `django_rust_templates`.
    fn tree_module(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(|path| path.parent())
            .expect("the crate lives two levels below the repo root");
        let sys_path = py.import("sys")?.getattr("path")?;
        for entry in [root.join("python"), root.join(".django")] {
            let entry = entry.to_str().expect("a UTF-8 path").to_string();
            if !sys_path.contains(&entry)? {
                sys_path.call_method1("insert", (0, &entry))?;
            }
        }
        let settings = py.import("django.conf")?.getattr("settings")?;
        if !settings.getattr("configured")?.extract::<bool>()? {
            let kwargs = PyDict::new(py);
            kwargs.set_item("DEBUG", false)?;
            kwargs.set_item("USE_I18N", false)?;
            kwargs.set_item("TEMPLATES", PyList::empty(py))?;
            settings.call_method("configure", (), Some(&kwargs))?;
            py.import("django")?.call_method0("setup")?;
        }
        py.import("django_rust_templates.engine.parser")
            .map(Bound::into_any)
    }

    fn nodelist(nodes: Vec<NodeDesc>) -> NodeListDesc {
        NodeListDesc {
            nodes,
            contains_nontext: true,
        }
    }

    fn leaf(class_name: &str) -> NodeDesc {
        NodeDesc {
            class_name: class_name.to_string(),
            attrs: Vec::new(),
            nodelists: Vec::new(),
            must_be_first: false,
            token: None,
            slot: None,
        }
    }

    fn run_one(py: Python<'_>, desc: &NodeDesc) -> PyResult<Py<PyAny>> {
        let tree = tree_module(py).unwrap();
        let none = py.None().into_bound(py);
        materialise_one(py, &tree, desc, &none, &none, &none, &tree)
    }

    /// A real reference table, and the two callables `materialise` derives from it.
    ///
    /// A Python **list**, with its own bound `__getitem__`/`__setitem__`, because that
    /// is what the compiler allocates: `Host::alloc_slot` appends `None` and returns
    /// the index, so a slot that is reserved and not yet filled reads `None` rather
    /// than raising. A `dict` would raise `KeyError` there and the slot arm's probe —
    /// a check for exactly that `None` — could not be reached at all.
    ///
    /// `reserved` seeds the placeholders; the index a test uses must be below it.
    fn ref_table<'py>(
        py: Python<'py>,
        reserved: usize,
    ) -> (Bound<'py, PyList>, Bound<'py, PyAny>, Bound<'py, PyAny>) {
        let table = PyList::new(py, std::iter::repeat_with(|| py.None()).take(reserved)).unwrap();
        let get = table.getattr("__getitem__").unwrap();
        let set = table.getattr("__setitem__").unwrap();
        (table, get, set)
    }

    #[test]
    fn an_unknown_parent_is_loud_before_its_children_are_built() {
        // **The child does not exist either**, and that is the instrument: if the
        // walk built its subtree first, the error would name `NoSuchChild`; if it
        // reads the registry first, it names `NoSuchParent`. The message
        // distinguishes the two orders with no injection needed to read it.
        Python::initialize();
        Python::attach(|py| {
            let mut parent = leaf("NoSuchParent");
            parent.nodelists = vec![("nodelist".to_string(), nodelist(vec![leaf("NoSuchChild")]))];
            let error = run_one(py, &parent).expect_err("an unknown parent is loud");
            assert!(error.is_instance_of::<pyo3::exceptions::PyLookupError>(py));
            let rendered = error.to_string();
            assert!(rendered.contains("NoSuchParent"), "{rendered}");
            assert!(
                !rendered.contains("NoSuchChild"),
                "the children were built before the registry was read: {rendered}"
            );
        });
    }

    #[test]
    fn a_node_slot_over_an_empty_table_is_loud() {
        // The **dispatch**, not the helper: `AttrValue::NodeSlot(0)` through
        // `attr_to_py` with a real resolver returning `None`. `NodeRef` over the
        // same table is *not* loud — its `empty_is_loud` is `false` — and this row
        // and the next are what hold those two apart.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let empty = PyList::new(py, [py.None()]).unwrap().into_any();
            let lookup = empty.getattr("__getitem__").unwrap();
            let error = attr_to_py(
                py,
                &tree,
                &AttrValue::NodeSlot(0),
                &lookup,
                &tree,
                &PyList::empty(py),
            )
            .expect_err("an empty slot is loud");
            assert!(error.is_instance_of::<pyo3::exceptions::PyLookupError>(py));
            assert!(
                error
                    .to_string()
                    .contains("is an empty reference-table slot"),
                "{error}"
            );
        });
    }

    #[test]
    fn a_node_ref_over_the_same_empty_table_is_not_loud() {
        // The control for the row above, and the reason `empty_is_loud` is an
        // argument rather than a constant: `NodeRef`'s slot is filled by the
        // *host*, so `None` there is a legitimate "the host ran no code" answer
        // that the host arm checks for itself.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let empty = PyList::new(py, [py.None()]).unwrap().into_any();
            let lookup = empty.getattr("__getitem__").unwrap();
            let found = attr_to_py(
                py,
                &tree,
                &AttrValue::Ref(NodeRef(0)),
                &lookup,
                &tree,
                &PyList::empty(py),
            )
            .expect("a `NodeRef` over an empty slot is an answer, not an error");
            assert!(found.bind(py).is_none());
        });
    }

    #[test]
    fn a_handle_with_no_resolver_is_loud_for_both_shapes() {
        // Both arms, because the two `resolve_ref.is_none()` checks are separate
        // lines and only one of them being present is a plausible edit.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let none = py.None().into_bound(py);
            let lists = PyList::empty(py);
            for value in [AttrValue::Ref(NodeRef(0)), AttrValue::NodeSlot(0)] {
                let error = attr_to_py(py, &tree, &value, &none, &tree, &lists)
                    .expect_err("a missing resolver is loud");
                assert!(error.is_instance_of::<pyo3::exceptions::PyLookupError>(py));
                assert!(
                    error.to_string().contains("needs a resolve_ref callback"),
                    "{value:?}: {error}"
                );
            }
        });
    }

    #[test]
    fn a_nodelist_ref_past_the_end_is_loud_and_names_the_arity() {
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let none = py.None().into_bound(py);
            let lists = PyList::new(py, [none.clone()]).unwrap();
            let error = attr_to_py(py, &tree, &AttrValue::NodeListRef(3), &none, &tree, &lists)
                .expect_err("an index past the end is loud");
            assert!(error.is_instance_of::<pyo3::exceptions::PyLookupError>(py));
            let rendered = error.to_string();
            assert!(rendered.contains("is out of range"), "{rendered}");
            assert!(rendered.contains("1 nodelist(s)"), "{rendered}");
        });
    }

    #[test]
    fn a_nodelist_ref_inside_the_range_is_the_nodelist_itself() {
        // The control, and not decoration: it is the row that would fail if the
        // error mapping above were widened to swallow `Ok` as well, which is the
        // shape a `match … { Err(_) => … }` written as a bare `match` produces.
        //
        // The element is a **real** nodelist rather than `None`. A `None` in the
        // list is also what an arm that resolved nothing would return, so the
        // pointer comparison would have held for the wrong reason.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let none = py.None().into_bound(py);
            let (_, get, set) = ref_table(py, 1);
            let real = materialise_native(
                py,
                &tree,
                &nodelist(vec![leaf("CommentNode")]),
                &none,
                &get,
                &set,
                &tree,
            )
            .expect("a one-leaf nodelist materialises");
            let expected = real.bind(py).as_ptr();
            let lists = PyList::new(py, [real]).unwrap();
            let found = attr_to_py(py, &tree, &AttrValue::NodeListRef(0), &none, &tree, &lists)
                .expect("an in-range index resolves");
            assert_eq!(
                found.bind(py).as_ptr(),
                expected,
                "an in-range `NodeListRef` did not return the nodelist at that index"
            );
        });
    }

    #[test]
    fn a_slot_is_probed_first_and_the_second_mention_is_the_object_it_found() {
        // **The arm no row above this one reaches.** Every hand-built descriptor in this
        // module until now leaves `slot` at `None`, so all of them would stay green if
        // the slot arm were deleted outright — exactly the "dead code with a
        // live-looking call site" the plan's Task 5 note warns about. `{% cycle %}`'s
        // shared identity is the behaviour this pins.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let none = py.None().into_bound(py);
            let (table, get, set) = ref_table(py, 8);

            let mut first = leaf("CommentNode");
            first.slot = Some(7);
            let built = materialise_one(py, &tree, &first, &none, &get, &set, &tree)
                .expect("a slotted leaf builds");
            assert!(
                !table.get_item(7).unwrap().is_none(),
                "the slot was never registered, so a second mention has nothing to find"
            );

            let mut second = leaf("TextNode");
            second.slot = Some(7);
            let again = materialise_one(py, &tree, &second, &none, &get, &set, &tree)
                .expect("the second mention resolves from the table");
            assert_eq!(
                built.bind(py).as_ptr(),
                again.bind(py).as_ptr(),
                "the second mention built a node of its own instead of reusing the slot"
            );
        });
    }

    #[test]
    fn a_slot_a_nested_build_filled_is_overwritten_by_the_outer_one() {
        // The order, which is the whole of the arm: the slot is **probed** before the
        // children are built and **registered** after, so a child that fills the same
        // slot is overwritten by its parent and the table ends up holding the outer
        // node. Registering before the children would leave the table holding the
        // child, and every later mention of the cycle would find the wrong object.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let none = py.None().into_bound(py);
            let (table, get, set) = ref_table(py, 8);

            let mut inner = leaf("BlockNode");
            inner.attrs = vec![("name".to_string(), AttrValue::Str("inner".to_string()))];
            inner.nodelists = vec![("nodelist".to_string(), nodelist(Vec::new()))];
            inner.slot = Some(7);

            let mut outer = leaf("BlockNode");
            outer.attrs = vec![("name".to_string(), AttrValue::Str("outer".to_string()))];
            outer.nodelists = vec![("nodelist".to_string(), nodelist(vec![inner]))];
            outer.slot = Some(7);

            let built = materialise_one(py, &tree, &outer, &none, &get, &set, &tree)
                .expect("the outer node builds");
            let built = built.bind(py);

            // **Which node came back, not merely that one did.** If the probe were moved
            // below child construction the child would register slot 7 first, the outer
            // probe would then find *the child* and return it — so `table[7]` and the
            // return value would still be one and the same object, and a pointer
            // comparison alone would stay green while the walk returned the wrong node.
            // Naming them is what separates the two.
            let name = |node: &Bound<'_, PyAny>| -> String {
                node.getattr("name").unwrap().extract().unwrap()
            };
            assert_eq!(
                name(built),
                "outer",
                "the walk returned the child, not the parent"
            );
            let inner_built = built.getattr("nodelist").unwrap().get_item(0).unwrap();
            assert_eq!(name(&inner_built), "inner");
            assert_ne!(
                inner_built.as_ptr(),
                built.as_ptr(),
                "the outer node is the child it was supposed to contain"
            );

            let held = table.get_item(7).unwrap();
            assert!(!held.is_none(), "the outer node registered its slot");
            assert_eq!(
                held.as_ptr(),
                built.as_ptr(),
                "the child's registration outlived the outer node's, so the slot was \
                 filled before the children were built"
            );
        });
    }
}
```

`attr_to_py` and `materialise_one` are private to this module, so `use super::*`
reaches them; `AttrValue` and `NodeRef` come from `template_core::parser::desc`.
`NodeRef(0)` is a tuple struct — check `desc.rs` for whether the field is `pub`
and whether `AttrValue::Ref` wraps it by value; the compiler will say, and the
`leaf`/`nodelist` builders above are the only place a field order matters.

- [ ] **Step 2: Run to verify failure**

Run: `scripts/run_unit_tests.sh tests.test_materialiser_parity -v` and
`scripts/run_rust_unit_tests.sh malformed`
Expected: the Python rows FAIL on `AttributeError: … has no attribute '_empty_slot_error'`; `test_the_second_mention_is_the_first_object` FAILS on the native branch with two distinct objects; the Rust module does not compile.

- [ ] **Step 3: Append the three message helpers to `parser.py`, and point `_decode_value` at them**

At the end of the file.

```python
def _missing_resolver_error(rendered):
    """The one spelling of the no-resolver `LookupError`.

    Called from `_decode_value`'s `NodeRef` and `NodeSlot` arms and from the Rust
    walk, so a shape still on the fallback and one that has moved cannot hold two
    messages that agree today and drift later.
    """
    raise LookupError(
        f"{rendered} needs a resolve_ref callback; none was passed to materialise"
    )


def _empty_slot_error(rendered):
    """The one spelling of the empty-slot `LookupError`.

    Empty is loud rather than `None`: a `ResetCycleNode` holding `None` would
    fail later and elsewhere, inside `render`, as `None.reset(context)` — which
    names neither the slot nor the parse it came from.

    `rendered` is a whole phrase rather than a `NodeSlot` repr, because the host
    arm reaches this too and its message must keep saying *which* descriptor
    failed: `parser.py:340` passes
    `f"the {HOST_NODE} descriptor's {ref!r}"`. Two callers, one suffix, two
    subjects.
    """
    raise LookupError(f"{rendered} is an empty reference-table slot")


def _missing_child_lists_error(rendered):
    """The one spelling of the no-enclosing-node `LookupError`.

    **This one names `_decode_value` and not `materialise`**, unlike the two
    above, and the difference is a fact rather than an oversight: this path is
    reached when the decoder is called *directly* with no enclosing node at all,
    which only happens outside a walk. Unifying the two phrasings would break
    `tests/test_materialise.py::…::test_a_nodelist_ref_without_the_enclosing_node_is_loud`,
    which reads the word "nodelists".

    **And the Rust walk cannot reach it**, which is why no `AttrValue` arm calls
    it: `attr_to_py` is only ever handed a `&PyList` of the enclosing node's
    children — it is built at the top of `materialise_one` and passed down — so the
    `child_lists is None` case does not exist on that side. Its Rust-side
    equivalent is the out-of-range message, and the distinction between the two is
    "the caller never had the lists" versus "the caller had them and the index was
    wrong". A helper extracted here for symmetry would be a branch no walk can
    take.
    """
    raise LookupError(
        f"{rendered} needs the enclosing node's nodelists; "
        "none were passed to _decode_value"
    )


def _nodelist_ref_out_of_range_error(rendered, count):
    """The one spelling of the out-of-range `LookupError`.

    `from None` is *inside* here, not at the call site, and that is the whole
    point of the helper raising rather than returning. The only call site sits in
    an `except IndexError:`, so a `raise _nodelist_ref_out_of_range_error(...) from
    None` there never runs — the helper raises first — and the `IndexError` stays
    on as `__context__`, so the user is shown `list index out of range` under a
    message that already says what went wrong.
    """
    raise LookupError(
        f"{rendered} is out of range: the enclosing node has {count} nodelist(s)"
    ) from None
```

Then point `_decode_value`'s **five** raise sites at them, exactly:

| `parser.py` line | arm | becomes |
|---|---|---|
| `:725` | `NodeRef`, no resolver | `raise _missing_resolver_error(repr(value))` |
| `:740` | `NodeSlot`, no resolver | `raise _missing_resolver_error(repr(value))` |
| `:743` | `NodeSlot`, slot empty | `raise _empty_slot_error(repr(value))` |
| `:751` | `NodeListRef`, no child lists | `raise _missing_child_lists_error(repr(value))` |
| `:755` | `NodeListRef`, index past the end | `raise _nodelist_ref_out_of_range_error(repr(value), len(child_lists))` |

and point the **host arm** at `:341` at `_empty_slot_error(f"the {HOST_NODE} descriptor's {ref!r}")`.

The last row carries **no** `from None`, and that is deliberate: this site sits inside an
`except IndexError:`, and the helper raises, so a suffix here would never run and the
`IndexError` would surface as `__context__`. The suppression lives inside
`_nodelist_ref_out_of_range_error` instead — see its docstring above.

Then run the three existing tests that read these strings before touching anything
else — they are the contract this step is editing, and they are the reason the message
text is fixed at all (a reader who sees `list index out of range` under a message that
already says what went wrong has been told twice, once uselessly):

```bash
scripts/run_unit_tests.sh tests.test_materialise tests.test_ref_table tests.test_variable_parity -v
```

Expected: PASS, with the strings byte-identical to what they were. The reviewer
named the three rows that pin them, and they pin **fragments** rather than whole
strings — `test_a_ref_without_a_resolver_is_loud` reads `"resolve_ref"`,
`test_a_nodelist_ref_out_of_range_is_loud` reads `"out of range"`, and
`test_ref_table.test_an_empty_slot_in_an_attribute_is_loud` reads `"slot"` — which is
why they would survive a careless rewrite and why the new rows in Step 1 assert the
**complete** strings beside them. **A diff in any of these three is the failure this
step is arranged to make impossible**: compare the helpers against the table above
rather than trusting the edit.

- [ ] **Step 4: Implement the arms**

```rust
        // --- handles and references ---
        //
        // Each is loud when its resolver is missing or its slot is empty, and the
        // loudness is the value: a silent `None` here renders an empty string and
        // reports nothing. The messages come from `parser.py` — one spelling,
        // called from both walkers — so these arms return the helper's own
        // exception rather than a `LookupError` built here.
        AttrValue::Ref(handle) => resolve_handle(
            tree,
            resolve_ref,
            format!("<NodeRef {}>", handle.0),
            handle.0,
            false,
        ),
        // `AttrValue::NodeSlot` holds a `usize` and `NodeRef` a `u64`
        // (`desc.rs:49`), so the cast is here rather than in the helper: a
        // `usize` parameter would silently truncate a `NodeRef` on a 32-bit
        // target, and the index is handed to Python either way, where `u64` is
        // the wider and therefore the safer spelling.
        AttrValue::NodeSlot(index) => resolve_handle(
            tree,
            resolve_ref,
            format!("<NodeSlot {index}>"),
            *index as u64,
            true,
        ),
        AttrValue::NodeListRef(index) => match child_lists.get_item(*index) {
            Ok(node) => Ok(node.unbind()),
            Err(_) => Err(loud(
                tree,
                Bridge::NodelistRefOutOfRangeError,
                (format!("<NodeListRef {index}>"), child_lists.len()),
            )),
        },
```

```rust
/// A reference-table lookup, or the loud error that says why it failed.
///
/// `empty_is_loud` is the whole difference between the two handles, and it is not
/// a detail: `AttrValue::Ref`'s slot is filled by the **host** during the parse, so
/// a `None` there is a legitimate "the host ran no code" answer that `HostNode`
/// checks for itself — while `AttrValue::NodeSlot`'s is filled by **this walk**,
/// so an empty one is a wire bug. Same table, opposite answers.
fn resolve_handle(
    tree: &Bound<'_, PyAny>,
    resolve_ref: &Bound<'_, PyAny>,
    rendered: String,
    index: u64,
    empty_is_loud: bool,
) -> PyResult<Py<PyAny>> {
    if resolve_ref.is_none() {
        return Err(loud(tree, Bridge::MissingResolverError, (rendered,)));
    }
    let found = resolve_ref.call1((index,))?;
    if empty_is_loud && found.is_none() {
        return Err(loud(tree, Bridge::EmptySlotError, (rendered,)));
    }
    Ok(found.unbind())
}
```

Add the cycle arm to `materialise_one`, **between the registry read and the
children** — replacing the `// Task 5 inserts the … here` comment — **and delete
Task 4's guard, which sits above the registry read**:

```rust
    if desc.slot.is_some() {
        return fallback_node(py, tree, desc, origin, resolve_ref, register_ref, parser);
    }
```

That whole block goes, with its comment. It is not dead code being tidied away: it is
the branch that would make the arm below unreachable, and leaving it in means the two
`{% cycle %}` mentions are still built by Python while the new arm's tests pass. The
guard's comment is also the record of *why* it existed — including which of the two
failures was silent and which was loud — so that reason is written up in **T4-C4** of
`## Code review revisions — Task 4` rather than left in a line that no longer compiles
against the tree.

```rust
    // `{% cycle a b as c %}` and every later `{% cycle c %}` are one node in
    // Django: `do_cycle` returns the *identical* object, and `CycleNode.render`
    // keys `render_context` by it, so two equal-but-distinct nodes would cycle
    // separately. The table is what makes them one.
    //
    // The token is re-attached on the way out, and that is not tidiness — it is
    // `extend_nodelist` doing `node.token = token` at every append, so the shared
    // object carries the **last** occurrence's token and every position reads
    // that one.
    let mut slot_to_fill = None;
    if let Some(slot) = desc.slot {
        let existing = resolve_ref.call1((slot,))?;
        if !existing.is_none() {
            existing.setattr("token", decode_token_or_none(py, tree, desc)?)?;
            existing.setattr("origin", origin)?;
            return Ok(existing.unbind());
        }
        slot_to_fill = Some(slot);
    }
```

and at the end of the function, **replacing the two-line comment Task 4 left there**
(`// \`register_ref\` is reached only by the host-node arm above at this stage; /
// Task 5 registers the \`{% cycle %}\` slot with it, in the block marked above.`):

```rust
    if let Some(slot) = slot_to_fill {
        // Filled **after** the node exists, which is what makes a second mention
        // of the same cycle find this one rather than build its own. The slot is
        // not a constructor argument — `CycleNode.__init__` takes none — so it is
        // stored here rather than passed to `cls`.
        register_ref.call1((slot, node.clone()))?;
    }
```

- [ ] **Step 5: Run both suites and the gate**

```bash
scripts/run_unit_tests.sh tests.test_materialiser_parity -v
scripts/run_rust_unit_tests.sh
```
then the four-command gate from Task 3 Step 8.

**No commit**, for the same reason as Task 4 Step 5: the switch is still `False`, so
no default behaviour changes and the work stays in the working tree.

---

### Task 6: The container arms

`List`, `Tuple`, `Map`, recursively. Small, but the tuple arm is Review Focus
item 3's second half — `{% if %}`'s `conditions_nodelists` *unpacks* its pairs, and
a list would render identically and be a different node.

**Declares:** nothing. This task extends `attr_to_py`'s existing `match` and adds no
Python entry point, so `Bridge` is unchanged — which is worth saying, because a task
that adds arms to a `match` over the bridges is exactly where a reader would expect
one to appear.

**Files:**
- Modify: `crates/template-py/src/materialise.rs`

- [ ] **Step 1: Write the failing test**

```python
class ContainerShapeTests(unittest.TestCase):
    """A tuple is not a list, and a dict's order is the repeated-key rule.

    The three contract rows below cannot fail while the container arms are on
    the fallback — the fallback decodes them correctly. They pin the contract;
    the fourth row is the mover.
    """

    def test_if_conditions_are_tuples(self):
        tree = _tree("{% if a %}x{% endif %}", native=True)
        for pair in tree[0].conditions_nodelists:
            self.assertIsInstance(pair, tuple)
            self.assertNotIsInstance(pair, list)

    def test_with_extra_context_is_a_dict(self):
        # `{% with a=1 %}` and **not** `{% with a = 1 %}`: `split_contents` makes
        # the spaced form three bits and `parse_bits` rejects it — upstream Django
        # raises `'with' expected at least one variable assignment` too.
        tree = _tree("{% with a=1 %}{{ a }}{% endwith %}", native=True)
        self.assertIsInstance(tree[0].extra_context, dict)
        self.assertEqual(list(tree[0].extra_context), ["a"])

    def test_a_repeated_keyword_keeps_first_position_and_last_value(self):
        # `token_kwargs` does `kwargs[key] = parser.compile_filter(value)`
        # (`base.py:1202`), so a repeated key keeps its first position and takes
        # the last value. `dict(pairs)` reproduces that; a list of pairs does not.
        #
        # The value is a `FilterExpression`, not `3`: `Variable.__init__` stores the
        # expression and resolves at render time, so the claim is on `.token`.
        tree = _tree("{% include 'p.html' with a=1 b=2 a=3 %}", native=True)
        self.assertEqual(list(tree[0].extra_context), ["a", "b"])
        self.assertEqual(tree[0].extra_context["a"].token, "3")

    def test_a_discarded_duplicate_keyword_is_never_decoded(self):
        """The dedup precedes the decode, and the survivors are decoded in Python's order.

        The row above checks the finished dict, which is the same either way. This
        one checks **what was decoded on the way there**, and it is the row that
        caught the bug: an arm that recursed entry-by-entry would decode the `1`
        that the repeated `a` discards, so a helper raising on it would make this
        walk fail where Python succeeds — on a template that renders.

        Measured before the fix: Python recorded `["'p.html'", "3", "2"]` and native
        recorded `["'p.html'", "1", "2", "3"]`.
        """

        def decoded_tokens(native):
            tokens = []
            real = materialiser._materialise_filter_expression

            def recording(desc, *rest):
                tokens.append(desc.token)
                return real(desc, *rest)

            materialiser._materialise_filter_expression = recording
            try:
                _tree("{% include 'p.html' with a=1 b=2 a=3 %}", native=native)
            finally:
                materialiser._materialise_filter_expression = real
            return tokens

        self.assertEqual(decoded_tokens(True), decoded_tokens(False))

    def test_no_container_reaches_the_python_decoder(self):
        """The mover: `List`, `Tuple` and `Map` are built in Rust, not handed over.

        The three rows above pin the *contract*, and the fallback satisfies it —
        they pass on either path. This one is the census claim. `fallback_attr`
        reaches `_decode_value` through `Bridge::DecodeValue`, resolved by name off
        the module on every call, so wrapping that name records exactly what the
        Rust walk gave up on. A `list`, `tuple` or `dict` arriving there is a
        container that fell back, whatever it then built.

        The leaves still arrive — `FilterExpr` and `IfCondition` are Task 7's, and
        the fallback re-enters `_decode_value` for a `Variable`'s `literal` — so
        the record is not empty and the row is not vacuous.
        """
        seen = []
        real = materialiser._decode_value

        def recording(value, *args, **kwargs):
            seen.append(type(value).__name__)
            return real(value, *args, **kwargs)

        materialiser._decode_value = recording
        try:
            for source in (
                "{% if a %}x{% endif %}",
                "{% with a=1 %}{{ a }}{% endwith %}",
                "{% include 'p.html' with a=1 b=2 a=3 %}",
                "{% for x in y %}{{ x }}{% endfor %}",
            ):
                _tree(source, native=True)
        finally:
            materialiser._decode_value = real

        self.assertNotEqual(seen, [], "the walk never reached the decoder; fixture moved")
        self.assertEqual(
            [name for name in seen if name in ("list", "tuple", "dict")],
            [],
            "a container attribute was handed to the Python decoder",
        )
```

The fourth row is the one that earns its keep. Its sensitivity was measured, not
assumed: stripping the three arms and rebuilding gives

```
AssertionError: Lists differ: ['list', 'tuple', 'dict', 'dict', 'list'] != []
```

— five container instances handed over — while the three contract rows stay green,
which is exactly the plan's own claim about them.

- [ ] **Step 2: Run to verify the three contract rows pass before the change**

Expected: PASS on both branches, and the fourth row **RED** — with the arms absent,
the containers are what the fallback sees.

- [ ] **Step 3: Implement**

Inserted into `attr_to_py`'s `match`, immediately **above** the `other => fallback_attr(…)`
arm, and `PyTuple` added to the `pyo3::types` import:

```rust
        // Containers are built here rather than left to the fallback because the
        // *container type* is the contract, not the contents: `IfNode.render`
        // unpacks each pair (`defaulttags.py:323`), and a list would unpack too
        // while being a different node. `fingerprint` tells the two apart.
        AttrValue::List(items) => {
            let built = items
                .iter()
                .map(|item| attr_to_py(py, tree, item, resolve_ref, parser, child_lists))
                .collect::<PyResult<Vec<_>>>()?;
            Ok(PyList::new(py, built)?.into_any().unbind())
        }
        AttrValue::Tuple(items) => {
            let built = items
                .iter()
                .map(|item| attr_to_py(py, tree, item, resolve_ref, parser, child_lists))
                .collect::<PyResult<Vec<_>>>()?;
            Ok(PyTuple::new(py, built)?.into_any().unbind())
        }
        // `dict(pairs)` and not a list of pairs: `token_kwargs` does
        // `kwargs[key] = …` (`base.py:1202`), so a repeated key keeps its **first**
        // position and takes the **last** value. Insertion into a dict reproduces
        // that; a list of pairs does not.
        //
        // **The dedup comes first, and the order is load-bearing.** See below.
        AttrValue::Map(entries) => {
            let mut surviving: Vec<(&String, &AttrValue)> = Vec::with_capacity(entries.len());
            for (key, item) in entries {
                match surviving.iter_mut().find(|(name, _)| *name == key) {
                    Some(slot) => slot.1 = item,
                    None => surviving.push((key, item)),
                }
            }
            let built = PyDict::new(py);
            for (key, item) in surviving {
                built.set_item(
                    key,
                    attr_to_py(py, tree, item, resolve_ref, parser, child_lists)?,
                )?;
            }
            Ok(built.into_any().unbind())
        }
```

The recursion is the point, and it is not symmetry for its own sake. Each of the
three arms is reached by a **real** attribute, and the shapes nest:

- `List` — `{% if %}`'s `conditions_nodelists`, `{% for %}`'s `loopvars`,
  `{% cycle %}`'s `cyclevars`, `{% ifchanged %}`'s `_varlist`, `{% firstof %}`'s
  `variables`, `{% cache %}`'s `vary_on`, `{% url %}`'s `args`;
- `Tuple` — the `(condition, nodelist)` pair *inside* the first of those;
- `Map` — `{% with %}`'s and `{% include %}`'s `extra_context`.

**`FilterExpression`'s filter arguments are not a `List`**, and an earlier draft of
this paragraph said they were. They are a `Vec<FilterVar>` inside `FilterExprDesc`
(`desc.rs:338`), reached by `_materialise_filter_expression`, never by an
`AttrValue::List` arm — so a reader who took the old sentence for the shape would be
looking for the container in the wrong place.

Each level must reach the *same* `attr_to_py`, so that a nested `SafeStr` still goes
through `mark_safe` and a nested `NodeListRef` still resolves against this node's own
`child_lists`.

**The `Map` arm dedups before it decodes, and the order is load-bearing.** The
Python path builds the wire `dict` first — `set_item` is where the first-position,
last-value rule is applied — and decodes only the surviving values, in first-key
order. So a value a repeated key discards is **never decoded**. Recursing
entry-by-entry instead looks equivalent, because the finished dict is identical, and
is not: on `{% include 'p.html' with a=1 b=2 a=3 %}` it decodes the `1` that `a`
discards, so a helper that raised on it would make the walk fail where Python
succeeds, on a template that renders. Measured: Python records
`["'p.html'", "3", "2"]`, the entry-by-entry form records `["'p.html'", "1", "2", "3"]`.
Step 1's last row is the control for it, in both walks.

- [ ] **Step 4: Run the tests, the gate, and commit**

Run: `scripts/run_unit_tests.sh tests.test_materialiser_parity -v` and
`scripts/run_rust_unit_tests.sh`, then the four-command gate from Task 3 Step 8.

```bash
git commit -m "perf: the container attribute arms move to Rust"
```

---

### Task 7: The expression arms, and closing the attribute fallback

`Variable`, `FilterExpr`, `IfCondition`, `Tokens`. Each still calls its existing
Python helper — those build real `smartif` and `Variable` objects and
reimplementing them is not this plan's business. What moves is the *dispatch*: a
Rust `match` replaces a nine-arm `isinstance` ladder walked once per attribute.

**`Variable` is a defensive arm, not a reachable one.** No parser-produced tree carries
a bare `AttrValue::Variable` — its only constructor, `variable_of`, puts it inside a
`FilterExprDesc` — so three of the four arms are what this task's template-driven rows
cover and the fourth gets a `mod malformed` row. Step 1 says how that was measured.

**Declares:** `Bridge::MaterialiseVariable`, `Bridge::MaterialiseFilterExpression`
and `Bridge::DecodeCondition`, and **deletes `Bridge::DecodeValue`** with
`fallback_attr`. Eight before this task and ten after; Task 8's pin asserts the ten.

**Files:**
- Modify: `crates/template-py/src/materialise.rs`

- [ ] **Step 1: Write the failing test**

```python
class ExpressionArmTests(unittest.TestCase):
    """Three of the four arms, driven by a template. The fourth cannot be.

    `_materialise_variable` uses `__new__` plus slot assignment rather than the
    constructor, because calling `Variable(desc.var)` would parse the token a
    second time in Python and discard Rust's parse — including the error messages
    and the token each carries, which are Rust's to produce.

    **`AttrValue::Variable` is not one of the three, and no template reaches it.**
    `variable_of` (`template-core/src/parser/filter_expr.rs:250`) is its only
    constructor and each of its three call sites is inside
    `parse_filter_expression`, where the `Variable` becomes a `FilterVar` —
    `FilterExprDesc.var` or one of its `filters[].args`. So the attribute
    `attr_to_py` is handed is always the `FilterExpr` around it. Its row is
    `mod malformed` in `materialise.rs`, for the reason the reference arms' rows
    are: a row here would be green for the filter-expression arm's reason.
    """

    def test_a_filter_expressions_variable_keeps_its_rust_parse(self):
        # The *filter-expression* arm, not the `Variable` arm: `{{ a.b.c }}` is a
        # `FilterExpression` attribute, and it is `_materialise_filter_expression`
        # that builds the `Variable` nested in it.
        tree = _tree("{{ a.b.c }}", native=True)
        self.assertEqual(tree[0].filter_expression.var.lookups, ("a", "b", "c"))

    def test_a_filter_expression_keeps_its_function_and_args(self):
        tree = _tree("{{ a|default:'x' }}", native=True)
        # `filters` is `[(filter_func, args)]` and `args` is `[(is_var, operand)]`
        # — two elements per pair, and the first arg is a *constant* here.
        ((func, args),) = tree[0].filter_expression.filters
        self.assertEqual(getattr(func, "__name__", None), "default")
        self.assertEqual([is_var for is_var, _arg in args], [False])

    def test_a_condition_operator_survives_the_walk(self):
        tree = _tree("{% if a == b %}x{% endif %}", native=True)
        condition = tree[0].conditions_nodelists[0][0]
        self.assertEqual(
            fingerprint(condition),
            fingerprint(_tree("{% if a == b %}x{% endif %}", native=False)[0].conditions_nodelists[0][0]),
        )

    def test_a_blocktranslate_keeps_token_types(self):
        # `{% load i18n %}` first: `blocktranslate` is not a builtin, so the load
        # node is `tree[0]` and the block is the last node.
        tree = _tree(
            "{% load i18n %}{% blocktranslate %}Hi {{ name }}{% endblocktranslate %}",
            native=True,
        )
        self.assertEqual([token.token_type.name for token in tree[-1].singular], ["TEXT", "VAR"])
```

The last row's fence was wrong as first drafted, in two ways, and both were caught by
running it before the Rust changed: `{% blocktranslate %}` without `{% load i18n %}`
is `ParseError: Invalid block tag`, and with the load the block is `tree[-1]`, not
`tree[0]` — `tree[0]` is the `LoadNode`.

**The `Variable` arm's row is in `materialise.rs`, not here**, and this is what a
round-1 review caught. `AttrValue::Variable` has exactly one constructor — `variable_of`
(`filter_expr.rs:250`) — and all three of its call sites are inside
`parse_filter_expression`, where the value becomes a `FilterVar`. So no
parser-produced tree carries a bare `Variable` attribute, and the `{{ a.b.c }}` row
above is green for the *filter-expression* arm's reason. Measured: replacing the
`Variable` arm's body with a `panic!` reddens no row in
`scripts/run_unit_tests.sh`. The arm exists because the `match` is exhaustive and
a hand-built descriptor can carry it, so its row drives `attr_to_py` directly, in
`mod malformed`, beside the reference arms' rows.

```rust
    #[test]
    fn the_variable_arm_is_the_one_no_template_reaches() {
        // A row for an arm no template can produce. The arm still has to exist,
        // because the `match` is exhaustive, and it still has to be *right*,
        // because a hand-built descriptor can carry it. The one thing it must not
        // do is re-parse the token in Python: `Variable.__new__` plus slot
        // assignment is where Rust's parse survives.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            // The **port's** `Variable` (`django_rust_templates/engine/base.py`),
            // which is what `parser.py` imports and what
            // `FilterExpression.is_var` tests against — not
            // `django.template.base.Variable`.
            let variable_class = py
                .import("django_rust_templates.engine.base")
                .unwrap()
                .getattr("Variable")
                .unwrap();
            let none = py.None().into_bound(py);
            let desc = VariableDesc {
                var: "a.b".to_string(),
                literal: None,
                lookups: Some(vec!["a".to_string(), "b".to_string()]),
                translate: false,
            };
            let built = attr_to_py(
                py,
                &tree,
                &AttrValue::Variable(Box::new(desc)),
                &none,
                &tree,
                &PyList::empty(py),
            )
            .expect("a `Variable` attribute materialises");
            let built = built.bind(py);
            assert!(built.is_instance(&variable_class).unwrap());
            assert_eq!(built.getattr("var").unwrap().extract::<String>().unwrap(), "a.b");
            // A **tuple**, Django's `tuple(var.split("."))` — and a list on the wire,
            // so this is also the row that says the helper converted it rather than
            // assigning what Rust handed over.
            let expected = PyTuple::new(py, ["a", "b"]).unwrap();
            assert!(
                built.getattr("lookups").unwrap().eq(&expected).unwrap(),
                "the lookups came back as Rust's wire list, not Django's tuple"
            );
            assert!(built.getattr("literal").unwrap().is_none());
        });
    }
```

- [ ] **Step 2: Run to verify they pass before the change**

Expected: PASS on both branches — the fallback is correct. These rows pin the
contract. **Task 8's census row is the mover**, and the plan's Task 8 text says why
`_decode_value` is deliberately not its instrument; an arm that dispatched but built
the wrong object is red here, and an arm that stopped dispatching is what
`Bridge::ALL` catches.

The arms' *reachability* was checked at implementation time by mutation instead, since
no permanent row here can see it — and the mutation had to be run **per arm**, because
one mutation over all four is not evidence for any one of them. Replacing the
`FilterExpr`, `IfCondition` and `Tokens` bodies with `Ok(py.None())` — dispatch still
compiled away, no fallback added back — makes **four rows** red: one on a fingerprint
diff (the condition) and three on `AttributeError` (two filter-expression rows and the
blocktranslate row, because the attributes come back as bare `None` rather than as
objects). Restored, green. **The fourth arm is measured by its own row in
`mod malformed`**, which is the only row that can see it: replaced with `Ok(py.None())`
it is red and the other nine Rust rows stay green.

- [ ] **Step 3: Implement**

```rust
        // The four expression arms keep their Python helpers. What moves is the
        // *dispatch*: a Rust `match` on a variant replaces an `isinstance` ladder
        // walked once per attribute, and the ladder's order — which is
        // load-bearing, since a `FilterExpr` reaching the `list` arm would be
        // passed through as an object and nothing would report it — becomes a
        // compile-time property instead of a comment.
        //
        // Each builds the **wire** pyclass and then calls the same Python helper
        // the Python decoder calls, with the same arguments. Building the final
        // object here instead would mean a second implementation of `smartif` and
        // of `Variable.__new__`.
        AttrValue::FilterExpr(expr) => {
            let wire = crate::filter_expr_desc_to_py(py, expr, None)?;
            call_bridge(
                tree,
                Bridge::MaterialiseFilterExpression,
                (wire, parser, resolve_ref),
            )
        }
        AttrValue::Variable(var) => {
            let wire = crate::variable_desc_to_py(py, var)?;
            call_bridge(tree, Bridge::MaterialiseVariable, (wire, parser))
        }
        AttrValue::IfCondition(condition) => {
            let wire = crate::if_condition_to_py(py, condition)?;
            call_bridge(tree, Bridge::DecodeCondition, (wire, resolve_ref))
        }
        // `BlockTranslateNode.singular`/`.plural`. Each element is rebuilt with the
        // token decoder, so a token's wire form stays single-spelt.
        AttrValue::Tokens(tokens) => {
            let built = tokens
                .iter()
                .map(|token| {
                    call_bridge(tree, Bridge::DecodeToken, (crate::token_to_py(py, token)?,))
                })
                .collect::<PyResult<Vec<_>>>()?;
            Ok(PyList::new(py, built)?.into_any().unbind())
        }
```

Then **delete `fallback_attr` and `Bridge::DecodeValue`**, and with them the
`_ =>` arm that called it: `attr_to_py`'s `match` now covers all seventeen
`AttrValue` variants, so the compiler proves the dispatch is total.

**Three separate things say the per-value fallback is gone, and none of them is a
substring search.** Exhaustiveness is the first: a `_ =>` arm would have to be
written deliberately. The bridge enum is the second, and it is what makes the first
non-silent — a fallback that calls Python needs a `Bridge` variant, so `ALL` drops
`DecodeValue` and grows by the three the arms above call, landing at **ten**, which
`the_bridge_list_is_pinned` reads. The third is the Python census, which wraps every
remaining bridge and records which the corpus reaches.

**`_decode_value` is deliberately not the instrument, and an earlier draft of this
paragraph got that wrong.** It said the census records that `_decode_value` is never
reached through the walk — which is false, and Task 8's census docstring works through
why: `_materialise_filter_expression` and `_decode_condition` call it for their
operands, and those two helpers are *kept*. So it fires on every template with a filter
expression. What the enum proves is the narrower and sufficient thing: no *attribute*
can be handed to it, because no arm dispatches to it any more.

**`_materialise_variable` stays in `_BRIDGES`, and the first draft of this paragraph
put it in the wrong one.** The `Bridge::MaterialiseVariable` *variant* is unreachable
from a parser-produced tree (see Step 1) — but the *function* is not, and the census
measures module attributes, not variants: `_materialise_operand` calls it for every
`FilterExpression` operand (`parser.py:448`). So `test_the_unreachable_bridges_are_unreachable`
went red on the draft that excluded it, which is the row doing its job. What that
leaves is a limit worth stating rather than hiding: for this one name the census cannot
distinguish "the walk dispatched it" from "a retained helper called it", which is
precisely why the enum — and not the census — is what proves the dispatch.

An earlier draft of this step proved the fallback gone by asserting the *string*
`_decode_value` was absent from `materialise.rs` — which this very paragraph, and the
doc comment above, would have failed. The `SafeStr` arm moved with the primitives in
Task 4 and is not repeated here — an earlier draft claimed exhaustiveness while
`SafeStr` was in neither arm set, which is exactly the kind of claim the compiler
settles.

`_materialise_filter_expression` takes `(desc, parser, resolve_ref)` and
`_decode_condition` takes `(condition, resolve_ref)`. Read the Python decoder's call
sites for the exact argument order rather than trusting this block; the wire objects
are the same ones it passes.

- [ ] **Step 4: Run the tests, the gate, and commit**

```bash
scripts/run_unit_tests.sh
cargo fmt --all
cargo clippy --workspace --all-targets --quiet -- -D warnings
python -I scripts/check_python_anchors.py --check
git commit -m "perf: the expression dispatch arms move to Rust"
```

---

### Task 8: Close the fallback, flip the switch, and take the measurement

The decision point. The spec's §5.2 kill-criterion is explicit: if the saving does
not clear the costs it introduces, **stop at S1**.

**Files:**
- Modify: `python/django_rust_templates/engine/parser.py` (the constant's value)
- Modify: `tests/test_materialiser_parity.py` (the census rows)

- [ ] **Step 1: Write the census rows**

Counting `materialise_node` is not enough: from Task 4 on, the node-level call is
gone while *attributes* still fall back, and from Task 6 on those are gone too.
The census therefore wraps **every** bridge and records which the corpus reached.

**Two rows, two different claims, and neither makes the other's.** The Python rows
say *which* bridges the corpus reaches, so a bridge that is never exercised is
visible. They **cannot** say there is no bridge they do not know about — that is
what `Bridge` in `materialise.rs` is for, and `the_bridge_list_is_pinned` in its
`walk_depth` module is the row that reads it. An earlier draft had this list trying
to make both claims and it made neither: it wrapped exactly the names below and then
asserted `called <= _BRIDGES`, which is true of any list whatsoever.

**`Bridge::ALL` is declared here, not at Task 3, and that is a round-1 review
correction.** Task 3 first carried it enumerated and complete, with an unconditional
`#[allow(dead_code)]` to survive `-D warnings`; all but one of its eleven variants had
no constructor and nothing read the list. An enumeration with no reader establishes no
pin — it is a list that can be left stale by exactly the edit it exists to catch — so
it was taken back out and each task now declares the variants its own arms call.
`ALL` and its reader land together, here. At this point the variants are exactly the
ten below, because Task 7 has already deleted `Bridge::DecodeValue`.

```rust
impl Bridge {
    /// Every bridge, enumerated, so that `the_bridge_list_is_pinned` can count it.
    ///
    /// **This constant and the row below are one edit and land together**, which is
    /// the whole reason neither is in Task 3. Enumerated without a reader it needed
    /// a blanket `#[allow(dead_code)]` in both compilations of this crate, and an
    /// allow that wide would have hidden a genuinely unused variant as readily as an
    /// unread constant.
    ///
    /// **Ten and not eleven**, because Task 7 deleted [`Bridge::DecodeValue`] along
    /// with `fallback_attr`. The number is meant to change exactly once, in a commit
    /// that says so.
    pub(crate) const ALL: [Bridge; 10] = [
        Bridge::MaterialiseNode,
        Bridge::MaterialiseVariable,
        Bridge::MaterialiseFilterExpression,
        Bridge::DecodeCondition,
        Bridge::DecodeToken,
        Bridge::MarkSafe,
        Bridge::UnknownNodeClass,
        Bridge::MissingResolverError,
        Bridge::EmptySlotError,
        Bridge::NodelistRefOutOfRangeError,
    ];
}
```

Add this Rust row to `mod malformed` in `materialise.rs` — **not** to `walk_depth` in
`lib.rs`, despite it not being about a malformed descriptor: `tree_module` and its
Django bootstrap live in `mod malformed`, and a second copy of that bootstrap in a
second module is a second thing to keep in step. Say so in the module's `//!` header
rather than leaving the placement looking accidental.

```rust
#[test]
fn the_bridge_list_is_pinned() {
    // **The one claim the Python census cannot make for itself.** A census that
    // wraps a list of names and asserts the calls were a subset of that list is a
    // tautology: it can only ever see the names it was handed. The set of Python
    // entry points the walk may call is a *type* instead, and this is the row that
    // makes adding one loud:
    //
    //   * a variant deleted from the enum and left in `ALL` does not compile;
    //   * a rename on the Python side is red on the `hasattr` below;
    //   * two variants sharing a name is red on the dedup;
    //   * **a variant added to the enum and not to `ALL` is not caught here.** It is
    //     caught one step earlier: `name()` is exhaustive, so the compiler sends the
    //     author to that match, and the review of that edit is the pin. An earlier
    //     draft of this comment claimed `ALL` itself made the omission fail to
    //     compile, which is false — `ALL` is a hand-written list whose length nothing
    //     derives from the enum, so it would have gone on saying ten. The count below
    //     is a tripwire for an unintended change, not a proof of completeness, and it
    //     is written here as what it is.
    //
    // **Scoped to the walk, and the scope is stated rather than implied.**
    // `compile_template` does one `getattr("materialise")` of its own (`lib.rs:5091`)
    // on the *non-native* arm — that is the A/B branch itself, not a fallback, and it
    // is the one Python call in this crate that is not a `Bridge`. It is also the call
    // the switch exists to choose, so it cannot be moved behind the switch it
    // implements.
    //
    // **Eight before Task 7, ten after**, because that task deletes
    // `Bridge::DecodeValue` with `fallback_attr` and adds three. The number is meant to change
    // exactly once, in a commit that says so.
    Python::initialize();
    Python::attach(|py| {
        assert_eq!(crate::materialise::Bridge::ALL.len(), 10);
        let tree = tree_module(py).unwrap();
        let mut names: Vec<&str> = crate::materialise::Bridge::ALL
            .iter()
            .map(|bridge| bridge.name())
            .collect();
        names.sort_unstable();
        names.dedup();
        assert_eq!(
            names.len(),
            crate::materialise::Bridge::ALL.len(),
            "two bridges share a name"
        );
        for bridge in crate::materialise::Bridge::ALL {
            assert!(
                tree.hasattr(bridge.name()).unwrap(),
                "{} is not on the parser module",
                bridge.name()
            );
        }
    });
}
```

and in `tests/test_materialiser_parity.py`:

```python
# The Python entry points the Rust walk calls. **Kept in step with `Bridge` in
# `materialise.rs` by hand, and safe to keep that way**, because this list is not
# what makes "no unlisted bridge" true — `Bridge` is. This list's job is the
# opposite direction: to prove each of these is *reached*, so that a bridge which
# silently stopped firing is red rather than merely permitted.
#
# The previous draft had it make both claims. It was also missing `mark_safe`,
# `_unknown_node_class`, `_missing_resolver_error`, `_empty_slot_error` and
# `_nodelist_ref_out_of_range_error`.
_BRIDGES = {
    # Host nodes only: the object is the host's, and its descriptor validation
    # and error messages belong to the parse.
    "materialise_node",
    # Kept whole rather than reimplemented, because reimplementing means a second
    # `Variable.__new__` and a second `smartif`. Their *dispatch* is Rust's.
    "_materialise_filter_expression",
    "_decode_condition",
    # The token wire form has one spelling, shared with `_native.lex`.
    "_decode_token",
    # `{% verbatim %}`'s body is a `SafeString`.
    "mark_safe",
    # **Reached, but not through its own `Bridge` variant** — and this is the one
    # place the census cannot tell the two apart, so it is written down rather than
    # left to look like the walk dispatched it. `_materialise_operand` calls this for
    # every `FilterExpression` operand (`parser.py:448`), so wrapping the *module
    # attribute* records it as reached on any template with a filter expression. The
    # Rust arm that names `Bridge::MaterialiseVariable` is a different thing and no
    # parser-produced tree reaches it: `variable_of`
    # (`template-core/src/parser/filter_expr.rs:250`) is the only constructor of
    # `AttrValue::Variable` and it puts the value inside a `FilterExprDesc`. That arm's
    # row is `mod malformed` in `materialise.rs`. Measured: replacing it with a
    # `panic!` leaves this suite at its one pre-existing failure and reddens only that
    # Rust row.
    "_materialise_variable",
}

# The bridges a *valid* corpus cannot reach, and why each is excluded rather than
# forgotten. Every one of them is an error path, so no template produces it — which
# is exactly what `UnknownClassTests`, `MalformedDescriptorTests` and `mod
# malformed` cover instead.
_UNREACHABLE_BRIDGES = {
    # Raised by `UnknownClassTests`, dispatched in Rust from the registry read.
    "_unknown_node_class",
    # The reference-handle and arity errors, raised by `MalformedDescriptorTests`
    # and, for the dispatch itself, by `mod malformed`.
    "_missing_resolver_error",
    "_empty_slot_error",
    "_nodelist_ref_out_of_range_error",
}


class FallbackCensusTests(unittest.TestCase):
    """What still goes through Python, recorded at the boundary.

    The walk moving to Rust is invisible in the tree — by construction the tree is
    identical — so it is measured at the boundary instead.

    **`_decode_value` is deliberately not the instrument.** An earlier draft
    wrapped it and asserted no calls, on the theory that any call meant an
    attribute still on the fallback. That is false: `_materialise_variable` calls
    it for `Variable.literal`, and `_materialise_filter_expression` and
    `_decode_condition` reach it too — three helpers this plan *keeps* on purpose.
    So the count is non-zero on every template with a variable, and the assertion
    would fail on correct code.

    The per-value fallback is proven gone **by the bridge enum** instead: Task 7
    deletes `Bridge::DecodeValue` and adds three, so `ALL` goes from eight to ten, which
    `the_bridge_list_is_pinned` reads. A fallback cannot call Python without a
    variant, so the *deletion* is what proves the fallback gone, and a substring
    search is not — the one this replaced would have rejected Task 7's own comment.

    **The count is not the proof, and round 2 of review found this docstring claiming it
    was.** Add a variant *with* its `name()` arm and omit it from `ALL`, and
    `the_bridge_list_is_pinned` stays green — the count derives from a hand-written
    list, as `ALL`'s own comment says. What the enum establishes is that no *argument
    to `call_bridge`* exists for a deleted capability. Dispatch at runtime is
    established by mutation, per arm. Two different claims; only the first is a
    property of the type.
    """

    def test_only_host_nodes_reach_the_python_node_materialiser(self):
        nodes = set()
        real = materialiser.materialise_node

        def recording_node(desc, *rest):
            nodes.add(desc[0])
            return real(desc, *rest)

        materialiser.materialise_node = recording_node
        try:
            for _label, source in materialiser_corpus.SOURCES:
                _tree(source, native=True)
        finally:
            materialiser.materialise_node = real

        self.assertTrue(
            nodes <= {materialiser.HOST_NODE},
            "these node classes are still on the fallback: "
            f"{sorted(nodes - {materialiser.HOST_NODE})}",
        )
        # And the corpus really does reach it, so the line above is not a statement
        # about the empty set: `{% url %}` and `{% static %}` are host tags, but a
        # corpus of `{% if %}` and `{{ var }}` alone would leave `nodes` empty and
        # this row green with nothing in it.
        self.assertTrue(nodes, "no host node was materialised; the census is vacuous")

    def test_every_reachable_bridge_is_reached_by_the_corpus(self):
        """The listed bridges all fire.

        `called == _BRIDGES`, and **only one of the two directions is real**:
        `_record_bridge_calls` wraps exactly the names it is handed, so `called` is a
        subset of `_BRIDGES` by construction and "reached but unlisted" can never
        appear. The equality therefore carries the *unreached* direction — a bridge
        that should fire and does not, which is the failure mode of a walk that
        quietly stopped calling one. Measured, by adding a real but non-firing name:
        `_record_bridge_calls(_BRIDGES | {"_nodelist_ref_out_of_range_error"})` returns
        the six and the row goes red on it.

        The other direction — a bridge the walk calls that is on neither list — is
        `Bridge`'s to prove and this file cannot, which is what `ALL`'s comment in
        `materialise.rs` says.
        """
        called = _record_bridge_calls(_BRIDGES)
        self.assertEqual(called, _BRIDGES, f"unreached: {sorted(_BRIDGES - called)}")

    def test_the_unreachable_bridges_are_unreachable(self):
        """The other half of the list, and the reason it is a list at all.

        `_UNREACHABLE_BRIDGES` names four bridges a valid corpus cannot reach
        because they are error paths. If one of them *is* reached by a valid
        template then the exclusion is simply wrong and that bridge belongs in
        `_BRIDGES` — a fact about the walk, not a cosmetic tidy-up.
        """
        called = _record_bridge_calls(_UNREACHABLE_BRIDGES)
        self.assertEqual(called, set(), f"the corpus reaches these after all: {sorted(called)}")


def _record_bridge_calls(names):
    """Run the corpus native with `names` wrapped, and return the ones that fired.

    One implementation for both rows above, so that a wrapper that stopped
    recording cannot make one row quiet while the other stays green.
    """
    called = set()
    saved = {}
    for name in names:
        real = getattr(materialiser, name)
        saved[name] = real

        def recorder(*args, _real=real, _name=name, **kwargs):
            called.add(_name)
            return _real(*args, **kwargs)

        setattr(materialiser, name, recorder)
    try:
        for _label, source in materialiser_corpus.SOURCES:
            _tree(source, native=True)
    finally:
        for name, real in saved.items():
            setattr(materialiser, name, real)
    return called
```

- [ ] **Step 2: Run it**

Run: `scripts/run_unit_tests.sh tests.test_materialiser_parity.FallbackCensusTests -v`,
then `scripts/run_rust_unit_tests.sh` for `the_bridge_list_is_pinned`.

Expected: PASS. If `test_every_reachable_bridge_is_reached_by_the_corpus` names a
bridge the corpus does not reach, the corpus is short a template — add it to
`materialiser_corpus.TAG_SOURCES`. If `test_the_unreachable_bridges_are_unreachable`
names one, the exclusion is wrong: move that name into `_BRIDGES`. If the Rust row
fails on the count, that is Task 7 not having deleted `Bridge::DecodeValue`.

- [ ] **Step 3: Flip the switch**

```python
USE_NATIVE_MATERIALISER = True
```

and append to its comment:

```python
# **On by default since S1.** Kept rather than deleted because it is the A/B seam
# `scripts/compile_profile.py` measures through: with it off the walk is the
# Python one, and the two numbers are one subtraction apart. Deleting it would
# make the parity harness unprovable and the next change unmeasurable.
```

- [ ] **Step 4: Run the whole gate**

```bash
scripts/run_unit_tests.sh
cargo fmt --all
cargo clippy --workspace --all-targets --quiet -- -D warnings
python -I scripts/check_python_anchors.py --check
scripts/check.sh
```
Expected: green, including the Django suite. **This is the regression gate for the
entire plan** — the suite's rows against the real Django tests are what say the
tree is still the tree.

- [ ] **Step 5: Take the measurement**

```bash
source scripts/env.sh && maturin develop --release
.venv/bin/python -I scripts/bench.py            # the number that ships
.venv/bin/python -I scripts/compile_profile.py  # the number that explains it
```

**One number, and it is the compile saving.** An earlier draft listed three, and the
other two named quantities with no procedure attached: "first-render latency, confirm
rather than assume", and "retained bytes, measure it to confirm the reasoning". A
number a step cannot say how to take is not a measurement; it is a heading. Both are
dropped, each replaced by the thing that actually settles it.

1. **The compile saving on every row, as `python - native` from `compile_profile.py`
   and confirmed at `bench.py`'s entry point.** S1's ceiling on `large`, taken at Task 1
   Step 5 on this build, is **922.5 µs of 1758.4 µs** (52.5%; the earlier probe read
   1153 of 1834 on a different build — take Task 1's, which is the one this plan's own
   commits bracket). **Measured: 268.1 µs of 1805.3 µs, 14.9%.** So the ceiling held as
   a bound and the capture did not — the Rust walk still costs most of what the Python
   walk cost. The plan anticipated a residual ("kwargs assembly, one Python call per
   node, one `append` per node") but not at this size, and **S2 is where the residual
   is the subject rather than a note**.

   **`stubbed` is not a subtraction operand, and a round-2 review found this section
   subtracting it.** `stubbed` runs the *Python* path with a stub that still allocates
   a real `NodeList`, still runs both planner checks, still builds the `Template`, and
   — because `{% partialdef %}` materialises during the *parse* — changes parse-time
   state too; `native` skips the whole-tree wire entirely. So `python - stubbed` and
   `native - stubbed` are **compile-minus-stub residuals**, not measured walk times,
   and the ratio between them is an arithmetic coincidence between independent medians
   — the exact thing `scripts/compile_profile.py`'s own header forbids. `_both_paths`
   patches no `materialise`; it selects a branch. **Do not carry that decomposition
   into S2's rationale.**

   The bench reports the same direction at `from_string` (a different entry point,
   ~a hundred microseconds of setup apart, and that gap was measured *unstable* across
   builds): the `real-world` compile ratio falls from 1.206/1.134/1.363 to
   1.034/0.936/1.036 on normal/complex/large, medians of three runs, and `complex` now
   compiles *faster* than stock Django. The ratios are comparable across the two
   instruments; the absolute microseconds are each instrument's own.
2. **First render is not a separate measurement, because the tree is not a separate
   question.** Anything render-time could show about this change it could only show
   *through* the tree, and `TwoPathParityTests` already asserts the two trees are
   identical on every corpus source — structurally, node for node, by an oracle whose
   sensitivity has its own rows. A render-latency comparison on top of that would be a
   slower, noisier copy of a check that has already run. What render latency *would*
   catch — a node built with the wrong attribute — is a fingerprint difference and is
   red in Task 3.
3. **Retention is structural, not empirical.** `desc_tree` is a local of
   `compile_template` (`lib.rs:5060`) and drops at the end of it on **both** paths, so
   S1 adds no retention by construction. There is nothing here for a `tracemalloc` or
   an RSS reading to find, and a reading that found nothing would not be evidence for
   the reason the reasoning is: the drop is in the source. **S2 is where retention
   becomes a real question**, because that is where a `CompiledTemplate` holds the IR
   — and S2's plan carries the procedure, because there it has something to measure.

- [ ] **Step 6: Decide, and write the decision down**

- **If the compile saving is real:** commit, and the next plan is S2
  (`CompiledTemplate` + retained IR), which this plan's measurement now justifies.
- **If the saving does not clear the costs:** commit anyway — the numbers are the
  deliverable — and **stop**. Record in the spec's §5.2 that the kill-criterion
  fired, with the numbers, and do not write S2. A design that costs more than it
  saves is a design that should not be built, and the measurement is worth more
  than the rewrite.

```bash
git add -A
git commit -m "perf: S1 ships — the materialiser is native by default

Baseline (bench.py, uninstrumented):  <normal / complex / large>
After S1 (bench.py, uninstrumented):  <normal / complex / large>
Baseline (compile_profile.py):        <the four rows>
After S1 (compile_profile.py):        <the four rows>
Plan phase:                           unverified — see Task 1 Step 5"
```

---

## Self-Review

**Spec coverage.** The spec's §3, §4, §6 and §7 (target architecture, lifetime,
error, the two forcing sites) belong to S2-S4 and are not this plan's; this plan
implements §9's **S1** and the part of §10's verification surface S1 reaches.
§10.7's finding-derived rows are S2+'s, since they name projection and `Cleared`.
§1's residual — the one the spec labelled "a subtraction, unmeasured" and put at
~115 µs — **is not measured by this plan either**, and Task 1 Step 5 says why:
`plan_list` and `plan_block_bodies` are private to `compile_template`, so the phase
cannot be isolated from Python. Task 8's commit body records it as unverified rather
than adopting a figure, and S3 owns it.

**Placeholder scan.** One place names a lookup rather than a literal, and it is
deliberate: Task 4's expression arms point at the Python decoder's call sites for
the three retained helpers' argument order, because those helpers are already written
and copying their signatures is not a decision. `mod malformed`'s `tree_module` notes
that `NodeRef`'s field order is the compiler's to confirm. Everything else is
literal. No "handle edge cases", no "similar to Task N", no TBD.

**One thing this plan deliberately does not fix.** `materialise_one` ignores
`NodeDesc::must_be_first`. That is correct and not an omission: `desc.rs:436`
records it as read by `extend_nodelist` during the parse and not carried on the
wire, and the materialiser has never read it. Task 3's parity row is what says so.

**Type consistency.** `materialise_native` and `materialise_one` take the same seven
arguments in the same order in every task that shows them. `attr_to_py` and
`resolve_handle` likewise — and `resolve_handle`'s `index` is a **`u64`**, matching
`NodeRef`'s field, with one `as u64` cast at the `NodeSlot` arm. `loud` is defined in
Task 4 and used in Tasks 5 and 7. `decode_token_or_none` is defined in Task 4 and
used in Task 5. `crate::token_to_py` (existing, `lib.rs:753`) is called by Task 4 and
again by Task 7. `crate::attr_value_to_py` (`:763`),
`crate::node_desc_to_py` (`:1058`), `crate::nodelist_desc_to_py` (`:1104`),
`crate::filter_expr_desc_to_py` (`:1020`, three arguments),
`crate::variable_desc_to_py` (`:919`) and `crate::if_condition_to_py` (`:852`) all
already exist. The four message helpers are named identically in `parser.py` (Task 5
Step 3) and in the Rust arms and tests (Task 5 Steps 1 and 4), and Task 5's table of
the five call sites is the one place they are enumerated.

**Review Focus.** Item 1 (a host tag's node) — the arm stays in Python by design;
`HostNodeIdentityTests` (Task 5) asserts `assertIs` against the captured object,
plus token and origin, and Task 8's census asserts it is the *only* shape left
there. Item 2 (cycle identity) — `CycleIdentityTests`, Task 5. Item 3 (a type that
is an `int`/`str` subclass on both sides) — `test_true_and_one_are_not_the_same_tree`,
`test_a_safe_string_is_not_a_plain_string` (Task 2) and `ContainerShapeTests`
(Task 6). Item 4 (no `resolve_ref`) — `MalformedDescriptorTests` (Task 5) and
`a_handle_with_no_resolver_is_loud_for_both_shapes` (Task 5's `mod malformed`), the
second driving the *dispatch* rather than the helper. Item 5 (a malformed
descriptor) — `mod malformed` and `MalformedDescriptorTests`, Task 5, with a control
row beside each claim.

**Every claim in this plan has a row that fails if it is wrong.** That sentence was
false when it was first written, and false again after the round-2 fixes: round 2
found five rows that could not fail, round 3 found six more, and the ones round 3
found were mostly *replacements written in round 2 to fix round 2's* — a profiler row
that re-implemented the wrapper it was testing, a census that wrapped a list and
asserted the calls were in the list, a source assertion that Task 7's own comment
tripped, and a fixture whose malformed child made the bug it tested for
indistinguishable from the fix. The shape of the error is the same every time: a test
whose *expectation* is derived from the thing it is testing. Each is now derived from
somewhere else — the bridge *type*, the production recorder, the corpus — and
`What this plan does not do` below names the two claims that are **not** tested
because they cannot be measured from here.

**Task 2's own two rounds ran the experiment again on the oracle itself and got the
same answer.** Round 1: five rows that could not fail, plus a *dead arm* underneath
them — the operator arm swallowed every `Literal` because `Literal` is a `TokenBase`
with `lbp = 0`, so `{% if a %}` and `{% if b %}` fingerprinted alike and the literal arm
was unreachable code. Round 2: three flags read through `bool(...)` and so blind to `1`,
a save-and-restore that the document promised and the file did not have, and an
`_operator_id` that returned `None` in silence. Both rounds found them the same way —
mutate the implementation, require the owner row to go red — and that procedure, not a
green suite, is what each of these claims rests on.

## What this plan does not do

- **It does not delete the Python materialiser.** `materialise`, `materialise_node`
  and `_decode_value` stay: they are the switch's other branch, the parity oracle,
  the host-node arm, and the reference the census measures against. When they can go
  is a question for after S2, and the answer depends on whether the flag is kept.
- **It does not measure the plan phase.** `plan_list` and `plan_block_bodies` are
  private to `compile_template`, so no Python-side procedure can separate them from
  the parse, the wire and `template_for` that share their `stubbed` total. Task 1
  Step 5 says so and Task 8 Step 5 records the spec's ~115 µs as unverified rather
  than adopting it or the 25.5 µs this plan's probe once produced. **S3 owns it**,
  and can time it where it lives.
- **It does not measure retention, or first render.** Retention is structural —
  `desc_tree` is a local of `compile_template` and drops on both paths — so there is
  nothing to read, and S2 is where a `CompiledTemplate` makes it a real question.
  First render is the tree, and the tree is `TwoPathParityTests`.
- **It does not retain the IR.** `desc_tree` drops at the end of `compile_template`
  exactly as today. S2 is where a `CompiledTemplate` holds it, and S2's plan carries
  the measurement of what that costs.
- **It does not build the plan from the IR.** `plan_list` still reads the materialised
  tree positionally (`lib.rs:3923`, `lib.rs:3877`). S3.
