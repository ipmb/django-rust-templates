# M3 — `FilterExpression`, `Variable`, `smartif` and the primitive resolver

**Review target: `.django/` at Django 6.0.9.** An unpinned Django also exists on
this machine, so an unqualified "Django" is ambiguous — every claim below is
against the pinned checkout, and the summary says so where it matters.

This is the milestone round (§11). The unit of review is the milestone, not the
commit, so the whole of M3 is in scope, including the seven plan-correction
commits, which are themselves a finding — see "The plan-vs-tree defect rate".

## What M3 claims

1. `{{ … }}` parses. `filter_re` is assembled from the same pieces Django
   assembles it from, and `FilterExpression.__init__`'s match loop — the gap
   check, the variable part, each filter's arguments, the host's two filter
   checks, the remainder check — runs in Rust, in that order.
2. `Variable.__init__`'s whole body is ported, including Python's `int`/`float`
   grammar (surrounding whitespace, PEP 515 underscores, the 760-code-point `Nd`
   table), the assign-then-raise order that `2.` depends on, and
   `unescape_string_literal`'s two ordered replacements.
3. A descriptor materialises into **Django's own** `Variable` and
   `FilterExpression` objects, built with `__new__` plus slot assignment because
   neither constructor can be called without re-parsing the token in Python.
4. `parser/resolve.rs` ports `Variable._resolve_lookup`: the three arms, the two
   swallowed-exception sets, the `dir()` rule and the callable arm, driven
   directly by `_native.resolve` and by a render.
5. `parser/smartif.rs` ports `{% if %}`'s expression parser, with a deliberate
   and bounded divergence on nesting depth.
6. `AttrValue` gained the four variants M5's tags need (`Variable`, `List`,
   `Map`, `NodeListRef`), and the corpus now reaches variables — which was M2's
   recorded residue.

## Commits

| Commit | Task |
|---|---|
| `412cc8d` | M3 plan |
| `87efec7`, `34399f2`, `bc7741d` | Task 1 — one module per tag, registry, plan corrections, review |
| `6e871e9`, `3e580d6`, `68f69b9` | Task 2 — Python's `\w` as two spellings, plan correction, review |
| `85cb389`, `4aee058`, `55072bf`, `2f2e7ca` | Task 3 — `int`/`float` grammar, `Variable`, review, plan corrections |
| `4cf6936`, `2996e91` | Task 4 — wire containers and `NodeListRef`, plan corrections |
| `e214092` | Task 5 — `filter_re` and the `FilterExpression` loop |
| `605cc2e` | Task 6 — materialising `Variable` and `FilterExpression` |
| `b5246a0` | Task 7 — the primitive resolver and the first `_native.render` |
| `027f8d1` | Task 8 — the `smartif` parser |
| `9efa0d0` | Review follow-up — `string_if_invalid`'s two consumers, the `_impl` naming rule |
| `4c3e48f` | Task 9 — `token_kwargs` and `kwarg_re` |
| `45ac7e1` | registry: lifetime is the rule, and `load` is its own bucket |
| *(this commit)* | Task 10 — the milestone's gates and the §10.4 correction |

## Evidence

| Check | Result |
|---|---|
| `cargo test -p template-core` | 198 passed, 0 failed |
| `cargo clippy --workspace --all-targets -- -D warnings` | exit 0 |
| `cargo fmt --all -- --check` | exit 0 |
| `tests.test_parser_parity` | 16 tests — 72 sources × 2 lexer modes + 400 random × 2, 0 mismatches |
| `tests.test_materialise` | 45 tests |
| `tests.test_render_parity` | 15 tests |
| `tests.test_variable_parity` | 11 tests |
| `tests.test_filter_expr_parity` | 10 tests, 955 tokens against Django's own `FilterExpression` |
| `scripts/check.sh` (full) | exit 0, `Ran 1547 tests … OK (skipped=6)` |
| `scripts/differential.py` | 48 cases, identical |
| Progress meter | **25** modules still Django's — unchanged |

The suite's 1547/6 is the acceptance criterion and it is *expected* to be
unchanged. M3 adds engine code that nothing in the suite's path imports yet
(§10.1 — engine progress and integration progress move independently), so the
suite's job at M3 is to prove M3 broke nothing.

The progress meter is printed by `scripts/run_django_suite.sh:60`. The plan's
Step 4 lists `python scripts/progress.py`, which does not exist — a fifth
plan-text defect, noted here rather than worked around.

## The spec correction, and why it is the milestone's one deviation

§10.4's M3 gate said *"a variable ahead of `{% extends %}` … M3 adds that case
to the corpus explicitly."* Re-measured against the pinned tree before adding
anything, with `Origin("t.html", "t.html", None)`:

| source | outcome |
| --- | --- |
| `{{ x }}{% extends "a" %}` | `TemplateSyntaxError: {% extends "a" %} must be the first tag in 't.html'.` |
| `{% csrf_token %}{% extends "a" %}` | the same error |
| `text{% extends "a" %}` | OK — `['TextNode', 'ExtendsNode']` |
| `{% extends "a" %}{{ x }}` | OK — `['ExtendsNode']` |
| `{{ x }}{% include "a" %}` | OK — `['VariableNode', 'IncludeNode']` |
| `{{ x }}{% extends %}` | `TemplateSyntaxError: 'extends' takes one argument` |
| `{% extends "a" %}` with no origin | `AttributeError: 'NoneType' object has no attribute 'template_name'` |

So the rule's input is **any non-`TextNode`** — `contains_nontext` is set only
when the node is not a `TextNode` (`base.py:611`) — and what M2 actually lacked
was not a variable but a `must_be_first` **tag**, of which the package has
exactly one (`loader_tags.py:87`): `{% extends %}`, which §10.3 puts in **M7**.

Rust cannot produce the ordering error for `{{ x }}{% extends %}` at M3, and
both routes to it are worse than the gap. Django compiles the tag *first* and
checks the ordering *after* (`base.py:600-610`), so `{{ x }}{% extends %}` — no
argument — raises the argument error instead; an ordering check ahead of
dispatch would be wrong for an input any user can write. The other route puts a
loader tag in Rust, which §6 assigns to M7.

M3 therefore delivers the half it can hold — variable-versus-text and
variable-versus-tag `contains_nontext` — and the `{% extends %}`-specific case
moves to M7. The rule's *machinery* is already Rust's and already tested:
`extend_nodelist`'s `must_be_first && contains_nontext` check
(`crates/template-core/src/parser/mod.rs:336`) is exercised against a stand-in
`must_be_first` node (`parser/tags/mod.rs:195`). M7 supplies a tag, not a rule.
The spec entry keeps its original reading next to the correction rather than
being replaced, because a gate narrowed silently is worse than one narrowed with
its evidence attached.

**This is the one place M3 does not do what the spec says.** It is flagged here
for the reviewer rather than quietly narrowed, and it is the finding I most want
attacked.

## Round-4 verdict and dispositions

The milestone round returned **APPROVE WITH CHANGES** on Task 8 — one confirmed
defect, the one I pointed at, and one worry that turned out to be pinned already.

**MAJOR — `expression`'s nesting counter leaks on the `?` paths. Accepted,
verified, fixed, and pinned by a test that did not exist before.** The counter
had four exits and two decrements: the guard path and the success path
decremented, while `self.nud(t)?` and `self.led(t, left)?` returned without
decrementing. Any failure inside `nud` or `led` left the counter inflated.

The reviewer's analysis of *why* it was unobservable is right and is the
interesting part: nothing recovers from an `IfError` within one parser, and
Django constructs a fresh `TemplateIfParser` per `{% if %}` tag, so one parser is
one parse and a discarded parse never has its counter read again. It was latent
in a property of the *callers* rather than of the function — and M5 is where
callers multiply.

The reviewer judged that a test could not pin it ("you would have to reach into
the parser's private counter") and therefore recommended fixing the structure
and documenting. **Both halves are right except the last**: `#[cfg(test)] mod
tests` is a child of the module, so it *can* read the private field, and a test
is available after all. So the fix is both structural and pinned:

- `expression` now owns the increment and the single decrement, with the
  fallible body in `expression_body`. "No `?` in this function" is checkable by
  eye, which is what the split buys over an inner closure.
- `the_nesting_counter_returns_to_zero_after_a_failed_parse` drives three
  shapes — a failure inside `nud` (`["not"]`, counter 2 when the error unwinds),
  a failure at top level after successful inner parses (`["not","not","not","x","y"]`),
  and the refusal path — and asserts `parser.depth == 0` after each.

The reviewer's second point, that `TokenBase.second`'s implicit `None` is
already pinned by Django's own suite (`test_smartif.py:15`'s
`"(not (literal False))"`), was accepted as-is; it is recorded in the module doc
so nobody later decides the explicit `None` is redundant.

**Not a defect — the `Engine.template_parser` accessor my brief reached for.**
Confirmed absent at 6.0.9; `do_if` passes the `Parser` from its own compile
function, and `Parser(Lexer("").tokenize(), builtins=engine.template_builtins)`
is what that is. Same class as the plan citing `LookupError::Missing::current_repr`:
a brief naming something that does not exist.

**Accepted, no change — the guard measurement's shape.** 2000 `not`s parse and
4000 abort with SIGABRT before libtest reports, so the bound is empirical and
build-specific and `MAX_NESTING_DEPTH = 1000` is a *chosen margin*. The doc says
so rather than dressing it as derived, and the test drives both
`MAX_NESTING_DEPTH + 10` and 100 000 so the guard, not the input size, is what
fires.

## Guards made to fail, not just read

Each was observed failing for its intended reason before being kept. Where the
observation is recorded only in a commit message rather than re-run here, it says
so.

- **Task 5's three `filter_re` guards** (`e214092`): the arg-before-filter order
  (`x|nope:_bad`), the `\s` substitution (`x\x1c|upper`), and both `\w` spellings
  (`²`, `á`, plus `x|u²` for the name position, which no real filter name can
  reach).
- **Task 2's three replacement guards** (`68f69b9`): the bracket shape
  (`[{IN_CLASS}]` reconstructing `STANDALONE`), the precedence claim
  (`\p{L}\p{N}_+` rejecting `upper`), and the "simplify the constant to
  `\p{Alphabetic}`" guard. They replace a probe the reviewer correctly called
  unfalsifiable.
- **Task 3's Rust unit module, written red first**: 14 failures under `todo!()`,
  then 17 green (`4aee058`).
- **Task 4's `NodeListRef` guard**: `test_an_integer_attribute_is_not_a_nodelist_reference`
  exists because the plan's `isinstance(value, int)` sketch would have handed a
  node a *wrong* nodelist whenever it also had an integer attribute.
- **Task 8's `MAX_NESTING_DEPTH`**: with the guard disabled, `MAX_NESTING_DEPTH + 10`
  and 4000 `not`s abort with `fatal runtime error: stack overflow` (SIGABRT)
  where 2000 parse — so the native limit is between 2000 and 4000 frames in a
  debug build and 1000 is a margin under it.
- **Task 9's `kwarg_re`**, re-observed for this summary, each sabotage on its
  own: splicing the *contents* form (`\p{L}\p{N}_` with no brackets) fails 6 of
  8 tests, and removing `match_start`'s anchor filter fails exactly 1 — the test
  that names the divergence. Applied together they fail 7 of 8. The asymmetry is
  the right shape: the word class is read by every behaviour row, while the
  anchor is invisible to all of them by construction (every reachable `bit`
  starts with a non-whitespace character), which is why that guard is a test of
  its own rather than a row.
- **Task 10's generator floor**, observed this session: deleting the `VARIABLES`
  arm from `random_piece` reports `0 not greater than 100 : the generator barely
  reached variables`.
- **Task 10's `contains_nontext` guard**, observed this session by suppressing
  the flip for `VariableNode` in `extend_nodelist` and rebuilding: 133 subtests
  fail, the named one with `[False, False] != [True, False]`.
- **Task 8's counter, after the fix**, observed this session by deleting the
  single decrement: `a failed parse leaked nesting depth, left: 2, right: 0`,
  with `long_infix_chains_are_iterative` failing alongside it — which is itself
  evidence the counter is load-bearing and not decoration.

## The plan-vs-tree defect rate — five more, and a new class

M2's summary classified its seven into environment, count and reasoning. M3's
add five, and **three of them are one new class: a test snippet that cannot run
at all**, which is neither a wrong belief about the tree nor a wrong prediction
about an edit.

| Class | Task | What it believed | What happens |
|---|---|---|---|
| broken snippet | 10 | `ours[0].nodelist` on `"{{ x }}{% csrf_token %}"` | `AttributeError` — that node is a `VariableNode` and has no `nodelist` |
| broken snippet | 10 | `index = 0 if "autoescape" not in source else 0` | a no-op expression, in a test whose whole point is per-body flags |
| broken snippet | 9 | `AttrValue::FilterExpr(e)` with no `Box` and no host | does not compile; the real call is `parse_filter_expression(s, host).map(\|e\| AttrValue::FilterExpr(Box::new(e)))` |
| environment | 9 | `token_kwargs`' tests can call `compile_filter` bare | the seam needs a host; the plan's own Task 5 supplies one |
| environment | 10 | the corpus can hold variables once Task 5 lands | `_materialise_filter_expression` raises `LookupError` without a `Parser`, and `describe` had no arm for `FilterExpression`/`Variable`, so 205 subtests compared two live objects by identity |
| count | 10 | `{% extends %}` with no argument says "takes at least one" | it says `'extends' takes one argument` |
| count | 10 | the missing-origin `AttributeError` is special to the ordering case | every compiling `{% extends %}` raises it; only the no-argument form reaches the argument error first |
| environment | 10 | `python scripts/progress.py` runs the progress meter | no such file; `scripts/run_django_suite.sh:60` prints it |

The broken-snippet class is worth naming separately from M2's environment class
because it is *cheaper to catch and was not caught*: three of these do not
compile or raise on their first line, so nothing about the harness had to be
understood to see it — only running them. M2's conclusion was that executing
snippets before committing is the sole remedy for the "prediction about an edit"
class and overkill for the static class. These five are all in the static class,
and all five would have been caught by executing. So the refinement is narrower
than M2 drew it: **a declared environment tells you what exists; it does not tell
you the snippet is syntactically and structurally sound, and only running does.**

## What I did not verify

- **`{% if %}` semantics.** `eval`, the boolean evaluation, is M5. `smartif`'s
  tree is built and its errors are Django's, but nothing renders it, and
  `CompileFilter` has no M3 implementation — the tests drive `IfParser` with
  constructed tokens directly.
- **The resolver's correctness is not proven by a green render.** Deliberately
  the opposite: every render case's lookup still runs through Django's
  `Variable.resolve`, because `base` is not interposed until M9. What a green
  `tests/test_render_parity.py` proves is the Rust *parse* and the host's
  callbacks. The resolver's evidence is `_native.resolve` driven directly plus
  `tests/differential/cases.py`. This is §D5 of the plan and it is repeated here
  because a reader deciding whether M3 is done should not have to open Task 7.
- **`token_kwargs` has no caller.** `{% with %}`, `{% include %}`'s `with` and
  `parse_bits` arrive at M5–M7, so Task 9's tests drive it directly, and the
  "nothing calls this" fact is in its module doc rather than left to be
  discovered.
- **`check_filter`'s double filter lookup is wrong for `{% load %}`.**
  `Parser.add_library` uses `update`, so a load after this token's position
  rebinds the name while the materialiser resolves it after the whole parse.
  Unobservable until M6, which must return a handle before `{% load %}` ships;
  `Host::check_filter` carries the measurement and the fix.
- **`MAX_NESTING_DEPTH = 1000` is a chosen margin, not a bound.** Django refuses
  at ~498 (`RecursionError`); this engine parses to 1000. That is the only place
  this port deliberately diverges from Django's *observable* behaviour, and the
  direction is the safe one — a too-permissive engine renders a template Django's
  interpreter could not, rather than rejecting a valid one. No timing was done.
- **The randomised corpus still samples no category-C code points.** "Non-ASCII"
  means one Latin-1 letter and one astral emoji. `py_repr_str`'s divergence had
  to be found by sweep; the corpus cannot find that class.
- **The `{% extends %}` ordering case is not covered at M3**, by design — see the
  spec correction above.

## Carried findings

**`_impl/engine.py` would silently shadow Django's `engine.py`.** Measured, not
hypothetical: putting the shell's `render.py` at `_impl/engine.py` makes
`_impl/__init__.py`'s own `from .engine import Engine` resolve to that file, and
`install()` fails with `ImportError: cannot import name 'Engine' from
'django.template.engine'` — an error naming Django rather than the shim. The
rule is now in `_shim.py`'s docstring: no `_impl` file may take the name of a
real `django.template` module. The next one at risk is `_impl/parser.py`, whose
sibling `django/template/parser.py` does not exist at 6.0.9.

**`string_if_invalid` has two consumers and they differ.** The miss path and the
silent-failure path are not the same path, and both substitute the value
*escaped*: with `string_if_invalid="<b>%s</b>"`, `{{ nope }}` renders
`&lt;b&gt;nope&lt;/b&gt;` (no interpolation) while `{{ p.boom }}` renders
`&lt;b&gt;%s&lt;/b&gt;` (the literal `%s`, uninterpolated) and
`{{ p.boom|upper }}` renders `&lt;B&gt;%S&lt;/B&gt;`. Pinned by a test that
asserts both against Django and against the literal, so two runs that change
together cannot keep agreeing.

**`_resolve_lookup` returns the invalid value raw.** `_native.resolve` on
`obj.boom` returns `"<%s>"` unsubstituted, so a port that substituted one frame
too early passes today and double-substitutes the moment M9 puts the resolver
under `FilterExpression.resolve`.

## What I want attacked

1. **The §10.4 correction.** Is moving the `{% extends %}` case to M7 right, or
   should M3 have done something else — the ordering check without the tag, a
   Rust-owned stand-in in the corpus, or extending the harness with an `Origin`
   and accepting the divergence on `{{ x }}{% extends %}`? This is the
   milestone's one deviation from the spec and the one I am least sure of.
2. **`MAX_NESTING_DEPTH` is a behavioural divergence, not just an implementation
   detail.** Django *raises* at ~498; we parse to 1000 and refuse past it. A
   template that renders here and raises there is a drop-in-replacement
   difference, whatever the direction. Is 1000 the right place, and should the
   message be Django-shaped?
3. **`describe`'s new arms could have weakened the parity test rather than
   strengthened it.** `SafeData` is described as `{"safe_str": …}` precisely so a
   marked-safe literal cannot compare equal to a plain one; check that the arms
   do not admit a description in which two different trees agree — in
   particular, that describing `filters` by `func.__name__` is sound when two
   libraries could register different functions under one name (M6's case).
4. **The wire's constant-operand representation.** A constant filter argument
   travels as `(lookup=false, Variable(…))` and the materialiser does the
   `resolve({})`, because `_("…")` must stay a `gettext_lazy` proxy. Is carrying
   a `Variable` for a *constant* the right seam, or should the wire carry a
   discriminant naming the three result types (`__proxy__`, `SafeString`, `int`)?
5. **The counter test reads a private field.** Is reaching past the module
   boundary into `IfParser::depth` acceptable, or does it make the invariant
   look pinned when a future caller could still leak through a new exit added to
   `expression` itself?
6. **The five broken-snippet plan defects.** Are they a coincidence of one task,
   or is the pattern in how Task 10 was written — the milestone's last task,
   written before its own corpus existed?

Ask for executable findings where you can — a command and its expected output
rather than prose. Where a finding is about a number, please name the metric;
six count disputes in this project have each turned out to be two right answers
to two unstated questions.
