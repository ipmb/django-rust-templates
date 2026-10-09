# Round 4 dispatch — M3 milestone review

**Review target: `.django/` at Django 6.0.9.** An unpinned Django also exists on
this machine; every claim below is against the pinned checkout.

M3 is complete. This is the milestone round (§11), so the unit of review is the
milestone, not the commit. **`.review/m3-summary.md` is the artifact** — claims,
commit table, evidence table, guards-observed-failing, what I did not verify, and
six things I want attacked. This dispatch is the short version.

## What landed since your Task 8 verdict

**Task 8 — your finding, fixed and pinned. `78c2188`.** `expression` now owns the
increment and the single decrement, with the fallible body in `expression_body`,
so "no `?` in this function" is checkable by eye.

Your analysis of *why* it was unobservable is right and is the part I kept: one
parser is one parse, a discarded parse is never consulted again, so the safety was
a property of the callers. **Your conclusion that a test could not pin it is the
half I think is wrong** — `#[cfg(test)] mod tests` is a child of the module, so it
reads `IfParser::depth` directly, and
`the_nesting_counter_returns_to_zero_after_a_failed_parse` now drives three shapes
(a failure inside `nud`; a failure at top level after successful inner parses; the
refusal path). Observed failing for its intended reason by deleting the single
decrement: `a failed parse leaked nesting depth, left: 2, right: 0`, with
`long_infix_chains_are_iterative` failing alongside it.

Your second point — `TokenBase.second`'s implicit `None` is pinned by Django's own
`test_smartif.py:15` — accepted as-is and recorded in the module doc so nobody
later deletes the explicit `None` as redundant.

**Task 9 — `token_kwargs` and `kwarg_re`. `4c3e48f`.** Nothing calls it at M3, so
the tests drive it directly and the module doc says so. Both guards re-observed
individually for the milestone round: splicing the *contents* form
(`\p{L}\p{N}_`, no brackets) fails 6 of 8 tests; removing `match_start`'s anchor
filter fails exactly 1 — the test that names the divergence. That asymmetry is
the point: the word class is read by every behaviour row, the anchor by none of
them, which is why it is a test of its own.

**Task 10 — the milestone's gates. `efdfa42`.** The corpus now reaches variables
(M2's recorded residue): 154 of 400 randomised templates contain `{{`, floored
with the *shapes* counted separately. Three things had to be fixed to make it
run, none of them in the plan, all in the summary: the harness needed a
`filter_parser()`, `describe` needed `FilterExpression`/`Variable` arms (205
subtests were comparing two live objects by identity), and the plan's Step 1 test
is defective three ways.

## The one thing I most want attacked

**The §10.4 correction.** The gate named "a variable ahead of `{% extends %}`" and
M3 does not deliver it. Measured: the rule's input is any non-`TextNode`
(`{% csrf_token %}{% extends "a" %}` fires, `text{% extends "a" %}` does not), and
what M2 lacked was a `must_be_first` *tag*, of which the package has exactly one
— `{% extends %}`, which §10.3 puts in M7. Rust cannot produce the error at M3
without either putting a loader tag in Rust (§6 says M7) or checking the rule
ahead of dispatch, and the latter is wrong for `{{ x }}{% extends %}` (no
argument), which raises `'extends' takes one argument` because Django compiles
before it checks. So M3 holds variable-versus-text and variable-versus-tag
`contains_nontext`, and the `{% extends %}` case moves to M7.

**Is that right, or should M3 have done one of the three things I rejected?** The
summary's attack list has five more — `MAX_NESTING_DEPTH = 1000` as a behavioural
divergence rather than an implementation detail; whether `describe`'s new arms
weakened the parity test; whether describing `filters` by `func.__name__` is sound
given M6's `{% load %}` rebinding; the wire's constant-operand representation; and
whether the counter test reaching a private field makes the invariant look pinned.

Ask for executable findings where you can — a command and its expected output
rather than prose. Where a finding is about a number, please name the metric; six
count disputes in this project have each turned out to be two right answers to two
unstated questions.
