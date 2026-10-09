# M9 recon — interposing `base` and `smartif`

**Status:** recon complete, plan not yet written. Every number below was
produced by a script in `.review/m9-measurements/`, run from the repo root
against `.django/` @ 6.0.9.

**What M9 is.** Write `python/django_rust_templates/_impl/base.py` and
`_impl/smartif.py` so that `django.template.base` and `django.template.smartif`
resolve to us; `_shim.unshimmed()` goes 25 → 23. Both modules become ours for
Django's internal imports too, not only for direct ones.

| module | lines | top-level symbols | framework importers | suite importers |
|---|---|---|---|---|
| `base.py` | 1207 | 37 | 34 sites / 23 names | 28 sites / 15 names |
| `smartif.py` | 215 | 8 | 2 sites / 2 names | 1 site / 1 name |

Our own tree already imports 13 names from `django.template.base` across 15
sites (`_impl/__init__.py`, `_impl/parser.py`, the shell modules).

---

## 1. The blast radius, measured — 964 of the 1547

`.review/m9-measurements/m9_measure_render_blast_radius.py`, one suite run:

```
NodeList.render calls: 9630
distinct tests that rendered at least once: 964
```

**964 is a lower bound, not a count.** Interposition happens per module at
startup, so every `NodeList` is ours, including ones `test_nodelist.py`
constructs directly — the reviewer's point, and it errs the safe way for a
milestone number whose only job is "how much does this put at risk". A test that
reached our render path without calling `NodeList.render` would make the true
figure higher, never lower. Labelled because the next reader will subtract from
it.

62.3% of the suite. That is `setup_test_environment`
(`django/test/utils.py:108-114`) setting `Template._render =
instrumented_test_render`, whose body is a signal send and then

```python
return self.nodelist.render(context)
```

so every `template.render()` in the run — including the ones inside Django's own
view, form, model and admin tests — enters the Python nodelist. The count is a
count of *tests that render*, so it does not depend on which class is bound to
`NodeList`; it is the same 964 before and after M9.

The same run measures the other half of the interposition, the annotation
protocol, and reports it **by calling frame**:

```
Template.get_exception_info calls, by calling frame:
    340  compile_nodelist
     84  render_annotated
      6  get_exception_info
```

**84 render-time annotations across 41 tests.** The first draft of this
measurement reported "430 annotations across 192 tests" and that number was
three call sites wearing one label — `base.py:204` (compile time), `base.py:312`
(the `PartialTemplate` delegation) and `base.py:1063` (the render-time
protocol). The breakdown is printed rather than only the wanted figure, so the
conflation cannot recur silently.

**And the repair had the same defect one level in** — the reviewer's catch, then
measured. `base.py:310-312` is a *delegation*:

```python
def get_exception_info(self, exception, token):          # PartialTemplate
    template = self.origin.loader.get_template(self.origin.template_name)
    return template.get_exception_info(exception, token)
```

so one partial-template annotation produces two frames: an outer one attributed
to `compile_nodelist` or `render_annotated`, and an inner one attributed to
`get_exception_info`. Counting *frames* is not counting *annotations*. Measured
by `.review/m9-measurements/m9_measure_partial_annotation_nesting.py`:

```
total frames: 430
of which nested inside an annotating frame: 4
distinct annotations (frames minus nested): 426
```

**426 annotations over 430 frames.** The reviewer predicted 424, assuming all six
`:312` frames nested; four do. The other two are

```
get_exception_info <- test_partial_template_get_exception_info_delegation <- …
```

— the pinned suite already has a row that calls the delegation **directly**, so
those two are annotations in their own right, reached from neither seam. The
first run of that measurement used a 25-frame window and reported the two as
"no annotating frame", which is the shape of a window too small rather than a
fact; it was widened to 200 before the number was believed.

The figures this milestone argues from survive the correction unaltered — **340
compile-seam annotations** (no nesting there) and **84 render-seam outer
annotations** (four of which delegate inward). What changed is the total, 430 →
426, and the reason to prefer annotation counts to frame counts.

**Why this matters for M9's acceptance.** The milestone's headline evidence is
`Ran 1547 tests … OK (skipped=6)` from `scripts/run_django_suite.sh`, with the
meter at 23. Finding 3 below is that this evidence is *vacuous on its own*: a
verbatim copy of `base.py` also passes.
The 964/84 figures are what a reader needs to size the risk, and the M9 close-out
needs delegation-specific evidence to go with them.

## 1a. The parse seam is wider than the render seam — 1170 of the 1547

The same script and the same suite run:

```
Template.compile_nodelist calls: 4360
distinct tests that parsed at least once: 1170
distinct tests that both parsed and rendered: 958
```

**1170 tests parse, 964 render, 958 do both.** The parse seam is the wider one,
and it is the one `base.Template` alone controls: every `Engine.from_string`
builds a `Template`, whose `__init__` ends in
`self.nodelist = self.compile_nodelist()` (`base.py:155`), so an interposed
`Template.compile_nodelist` flips the whole suite's parsing onto Rust **without
`engine.py` being touched** — which matters, because `engine.py` is M10's module
and M9 may not reach for it.

Two consequences for the plan:

- The suite count is a weak instrument on *both* seams, not just the render one.
  A `compile_nodelist` that quietly called Django's `Lexer`/`Parser` would keep
  all 1547 green.
- `compile_nodelist` is where `extra_data`, `get_exception_info` and the
  `Template: <name>, <msg>` message rewriting live (`base.py:201-213`), and the
  suite exercises the annotation half of it hard: **340 `get_exception_info`
  calls arrive from `compile_nodelist`** over the run, eight times the render-time
  figure. A Rust-backed rewrite of this method has to keep all three, which is
  why it wants to be a *delegating* transcription rather than a new
  implementation.

## 2. `run_django_suite.sh` is the only meaningful invocation

Measured, and it was this project's own process failure during M9 recon: running
`.django/tests/runtests.py` directly produced output **byte-identical to stock
Django's** and printed `Ran 1547 tests … OK (skipped=6)`. The shim was never
installed, because `scripts/shimpath/sitecustomize.py` installs it only when
`DJANGO_RUST_TEMPLATES == "1"`, and only `run_django_suite.sh` exports that —
after which it runs a pre-flight in a subprocess that asserts a module we provide
resolved to our file, and refuses the run otherwise.

A bare `Ran 1547 tests` in prose is therefore an unfalsifiable claim, which is
now a gate rather than a note: **`scripts/check_suite_counts.py`**, wired into
`check.sh` (fast path; 44 count lines, all with provenance). It requires every
count to sit within twelve lines of a marker naming the run — the command, the
progress meter, or `stock Django`. Two red states were measured before shipping
it: a bare count added to `.review/` fails, and an empty scope fails rather than
reporting a clean pass.

## 3. A verbatim `base.py` keeps the suite green — so the suite count is not M9's evidence

Done during recon, then reverted: with `_impl/base.py` a byte-for-byte copy of
Django's, `unshimmed()` reports 24, the whole suite is green, and
`tests/test_shim.py` catches the change with exactly one failure —
`assertIn("django.template.base", remaining)` at `tests/test_shim.py:95`. That
row pins the progress meter and **must move at M9**; the replacement should
assert the count as well (23), so the meter cannot drift unnoticed.

The consequence for acceptance: interposition alone is invisible. Every
delegation M9 introduces needs a row that would fail if the delegation were
removed or the transcription wrong — a direct-entry module, not the suite.

## 4. Wiring `Variable._resolve_lookup` to Rust leaves exactly two failures

Recon experiment (reverted): with `_impl/base.py` present and
`Variable._resolve_lookup`'s body replaced by a delegation to `_native`, the
suite reports `Ran 1547 tests … OK (skipped=6)` minus two (run as
`scripts/run_django_suite.sh template_tests`, which is the only invocation that
prints the progress meter and therefore the only one whose count means anything):

- `template_tests.test_logging.VariableResolveLoggingTests.test_log_on_variable_does_not_exist_not_silent`
- `template_tests.test_logging.VariableResolveLoggingTests.test_log_on_variable_does_not_exist_silent`

Both are the same defect: Django's outer handler (`base.py:1014-1018`) emits

```python
logger.debug(
    "Exception while resolving variable '%s' in template '%s'.",
    bit, template_name, exc_info=True,
)
```

and `bit` is not on the wire — `LookupOutcome::Escaped` carries the exception and
the frame but not the variable bit that failed. So M9's resolver seam needs the
bit in the escaping outcome, and the log line emitted from Python where the
logger lives, rather than a Rust-side imitation.

Two details that cost time in recon and should be in the plan:

- `_native.resolve(self.var, context)` raises
  `NotImplementedError("resolving a translated variable is M12's")`. `_("x")`
  sets **both** `translate=True` and `lookups`, and `Variable.resolve` translates
  the *result* — so the correct wiring is
  `_native.resolve(".".join(self.lookups), context)`, with translate handled after
  the lookup, exactly as Django does it.
- The contexts in the two failing rows are bound, because the `alters_data` arm
  reads `context.template.engine.string_if_invalid`.

## 5. An M8 defect, found here and fixed

`PyTarget::call` implemented Django's `TypeError`-then-`signature()` fallback as
`py.import("inspect")` + `inspect.signature`, where Django imports its own
wrapper (`base.py:63` → `django/utils/inspect.py:126-134`). On Python 3.14 the
wrapper passes `annotation_format=Format.FORWARDREF`, so a *deferred* annotation
is never evaluated; the bare call evaluates it.

Measured through `_native.resolve` against `Variable._resolve_lookup` on a
callable annotated with a name that exists only under `TYPE_CHECKING`:

```
ours   ('raised', 'NameError', "name 'AnnotatedKwarg' is not defined")
theirs ('value', '')
```

Committed at `f9aa0d8` with a direct-entry row in `tests/test_render_parity.py`,
whose **red state was measured against the pre-fix build**. That measurement
earned its keep: the row's first draft annotated the parameter with a *string*,
which no `signature` evaluates, so it passed against the broken Rust and would
have shipped as another vacuous green.

The reason M8's gate could not see it: the only row in the pinned tree that
reaches the arm is `tests/template_tests/tests.py:231-243`, which runs inside a
suite whose result was reported as a count. Same failure mode as finding 3.

## 6. §5.2's `annotate_exception` obligation, pinned to the character

`.review/m9-measurements/m9_measure_render_annotated.py`, against the five
`(start, end)` rows of `template_tests/test_nodelist.py:44-86`:

```
case  culprit-node        culprit-origin     attaches  (start,end)  expected
   1  SimpleNode       <unknown source>         1  (38, 56)     (38, 56)  OK
   2  SimpleNode       <unknown source>         1  (58, 76)     (58, 76)  OK
   3  SimpleNode       <unknown source>         1  (38, 56)     (38, 56)  OK
   4  ForNode          <unknown source>         1  (38, 57)     (38, 57)  OK
   5  ForNode          <unknown source>         1  (18, 37)     (18, 37)  OK

render_annotated propagated 10 exception(s) and
5 frame(s) passed the origin comparison across those five cases
```

Ten exceptions through `render_annotated`, five of which annotated. That ratio
*is* the protocol: the innermost raising node wins `_culprit_node` (the `hasattr`
guard), and only the frame whose `context.render_context.template.origin` matches
attaches `template_debug` — the other five frames pass the exception on
untouched.

**But the ratio cannot discriminate depth, and a row built on it would be
vacuous.** The reviewer's sharpening: if Rust attaches at the *wrong* node the
ratio stays 5, because the `hasattr` guard admits exactly one annotation per
propagating chain. A port that annotates five wrong nodes passes "five frames
annotate" identically. **The `(start, end)` pairs are the assertion; the ratio is
context.** M9's annotation rows therefore assert the pairs, not the count.

A third thing, adjacent, from the reviewer: `django/template/backends/django.py:120-121`
**copies** `template_debug` onto a re-raised exception at the backend boundary.
It is not a second annotator (this protocol's only render-time writer is
`render_annotated`, confirmed by enumerating every writer in `django/`), but it
is a second path by which an annotated exception reaches a test — and being a
copy it is transparent, so a backend-level assertion can tell *present* from
*absent* and can never tell *correct* from *incorrect*. Any backend-level
`template_debug` row is a presence test whatever it looks like.

Case 4 is the one that reaches §5.2's actual obligation. Source:

```
{% load bad_tag %}{% for i in range %}{% for j in five %}{% badsimpletag %}{% endfor %}{% endfor %}
```

The culprit is the **inner** `{% for %}`, a Rust-owned node nested inside another
Rust-owned node, and `(38, 57)` covers `{% for j in five %}`. Under §5.5 Rust
renders interior subtrees from its own compiled form without calling back into
Python `NodeList.render` for shadow-nodelist children — so that inner node has no
Python frame of its own, and Rust must attach `_culprit_node` and reach the
correct frame for `get_exception_info`. Nothing supplies that today; it is
discharged when the suite flips onto our `base` at M9.

Cases 1-3 pin the *other* direction: the culprit is `SimpleNode`, a Python custom
tag, nested inside a Rust-owned `{% for %}` — the `render_foreign_node` path.

## 7. §5.2's `render_value_in_context` obligation

Still owed, unchanged, and structural: it is a module-level function in `base.py`
(`base.py:1123-1136`), nothing under `crates/` or `python/` implements it or its
conversion test, and Django's own runs on all 9630 renders above. It becomes ours
the moment `base` is interposed. The conversion test is
`issubclass(type(value), str)`, **not** `isinstance`.

## 8. What falls through and must keep resolving

`_impl/base.py` is not an island: the modules that still fall through to Django
import **23 distinct names** from it across 34 sites, and the pinned suite imports
**15 distinct names** across 28 sites. A partial file fails at import, loudly, so
this is a completeness constraint on the transcription rather than a risk — but
it is also the list of names the M9 plan must not rename. `smartif` is the small
side: 215 lines, 8 symbols, 2 framework importers (`defaulttags.py:43` takes
`IfParser` and `Literal`).

Two naming constraints already on the books:

- `_impl/` must not contain a file named after a real `django.template` module
  that we do not intend to shadow — `_shim.py`'s docstring records the measured
  `ImportError` from an `_impl/engine.py` experiment.
- `_impl/parser.py` is named for a module Django does not have; `base.py` and
  `smartif.py` are names Django *does* have, which is the point.
