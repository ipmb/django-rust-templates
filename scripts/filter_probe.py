#!/usr/bin/env python3
"""A filter oracle: Django's answer and ours, side by side.

    scripts/filter_probe.py upper "'a'"     ->  ("str", 'A') safe=True
    scripts/filter_probe.py add 3 4         ->  ("int", 7)
    scripts/filter_probe.py join "['a','b']" "', '"

**An oracle, not a test.** `tests/test_filter_parity.py` owns the corpus and the
assertions; this is what you run while writing a body, when you want Django's
answer for one input rather than for sixty. Neither replaces the other: the test
is the gate, this is the instrument.

**Both sides go through `FilterExpression.resolve`'s loop** (`base.py:785-818`),
and that is the whole reason this script builds a real `Engine` and `Context`
rather than calling `defaultfilters.register.filters[name]` directly. The direct
call would print the *body's* answer and silently drop everything the loop does
around it:

* `expects_localtime` — `date`/`time` convert the value before the body sees it;
* `needs_autoescape` — 7 filters take an `autoescape` keyword the body branches on;
* `is_safe and isinstance(obj, SafeData)` — the `mark_safe` of the result, where
  the test is on the **pre-filter** value and `obj` is rebound either way.

A probe that skipped those would agree with a Rust side that skipped them too,
which is exactly the agreement §10.3's "incl. `FilterFlags` round-tripping" is
asking us *not* to mistake for parity. So the flags are exercised here, and the
comparison below prints `safe=` because for 36 of the 57 filters a correct value
with the wrong safety is still a wrong answer.

The argument, if given, is a **Python literal** and is rendered into the template
token as a template constant — `add 3 4` becomes the token `x|add:4`, and
`join "['a','b']" "', '"` becomes `x|join:', '`. That round trip is deliberate:
Django's own `filter_re` decides whether the argument is a constant or a variable
lookup, and `resolve` then `mark_safe`s the constant ones
(`base.py:788-792`). Passing the argument straight into the body would bypass
that, and the difference is visible on the filters whose answer **is** the
argument — measured, `default 0 "'x'"` is `(SafeString, 'x') safe=True` while
`default 0 0` is `(int, 0) safe=False`, because a quoted constant comes back
marked where an int one does not. (The first revision of this paragraph used
`add:4` against `add:"4"` as the example and claimed they answer `7` and `"34"`;
measured, both are `7` — `int("4")` succeeds, so the safety of the argument never
reaches the answer. The claim was wrong and the example is the one filter where
it holds.)

`DJANGO_RUST_TEMPLATES=1` is set by `scripts/env.sh` and is irrelevant here:
the shim is installed by `scripts/shimpath/sitecustomize.py`, which is only on
`PYTHONPATH` when `scripts/run_django_suite.sh` puts it there. Importing
`django_rust_templates` on its own changes nothing, so this script — like
`tests/test_filter_parity.py` — has Django's real registry and `_native` in one
process, which is the only way the two can be compared at all.

Exit status is 0 when the two agree and 1 when they do not, so this doubles as a
one-case gate when a body is being written.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import Context, Engine  # noqa: E402
from django.template.base import FilterExpression, Parser  # noqa: E402
from django.template.exceptions import TemplateSyntaxError  # noqa: E402
from django.utils.safestring import SafeData, mark_safe  # noqa: E402

from django_rust_templates import _native  # noqa: E402


def ensure_settings() -> None:
    """Configure minimal settings, once, and only if nothing has yet.

    `Context()` itself needs nothing, but the loop's `use_tz` question does, and
    the answer is genuinely subtle: `Context.use_tz` **defaults to `None`**, and
    `resolve` passes that `None` straight into `template_localtime` — which then
    falls back to `settings.USE_TZ` itself. So the value the loop acts on is not
    the one on the context, and a probe that read the context field and handed
    `None` to `_native.filter` would be measuring a default nobody uses. See
    [`effective_use_tz`].

    Guarded rather than unconditional for the reason `tests/test_render_parity.py`
    gives: a module-scope `settings.configure` changes what any module imported
    afterwards sees.
    """
    from django.conf import settings

    if not settings.configured:
        settings.configure(USE_I18N=False)


def effective_use_tz(context) -> bool:
    """The `use_tz` the loop's `template_localtime` call actually acts on.

    `None` on the context means "ask the settings", which
    `django.utils.timezone.template_localtime` does in its own first two lines.
    Resolving it here rather than passing `None` on is what keeps this an FFI
    for a `bool`: the Rust side must not have to know that Django's context has
    a tri-state where it looks like it has one.
    """
    from django.conf import settings

    return settings.USE_TZ if context.use_tz is None else context.use_tz


def template_literal(value) -> str:
    """Render a Python value as the template constant that produces it.

    Only the three shapes `filter_re`'s `constant_string` and `var_chars` accept:
    a quoted string, a bare number, and the three bare keywords. A `str` is
    quoted, which makes it a `constant_arg` — and so a `mark_safe`d one; anything
    else is a bare token, which makes it a `var_arg` resolving through `Variable`.
    Both are real inputs, and which one you get is Django's decision rather than
    this script's, so the rendering is faithful rather than convenient.
    """
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if value is None:
        return "None"
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, (int, float)):
        return repr(value)
    raise SystemExit(
        f"cannot render {value!r} as a template constant — the corpus holds "
        f"only str, int, float, bool and None arguments"
    )


def django_answer(name: str, value, given: str | None, context_kwargs: dict | None = None):
    """Django's result for `value|name[:arg]`, through `resolve`'s loop.

    The `FilterExpression` is built from the same token text a template would
    carry, so the parse and the arity check (`args_check`, `base.py:819`) are
    Django's. `Context()` defaults to `autoescape=True, use_tz=True`, which is
    what `_native.filter` defaults to as well; both are read off the context by
    the loop, not passed by it. `context_kwargs` is a row's `context` key
    (`{"autoescape": False}`), and it is the only way a corpus row can reach the
    other branch of a `needs_autoescape` filter — the flag is read off the
    context rather than passed as an argument, so a row cannot express it any
    other way. Absent means `Context()`'s defaults, as every earlier row wants.

    Returns the result, the expression and the context — the expression so the
    caller can print the token Django's own `filter_re` actually matched rather
    than the one this script meant to write, and the context because the loop
    reads `autoescape` and `use_tz` off it and the Rust side must be given the
    same two values.
    """
    engine = Engine()
    parser = Parser(
        tokens=[],
        libraries=engine.template_libraries,
        builtins=engine.template_builtins,
    )
    token = f"x|{name}" if given is None else f"x|{name}:{given}"
    expression = FilterExpression(token, parser)
    context = Context({"x": value}, **(context_kwargs or {}))
    return expression.resolve(context), expression, context


def rust_answer(name: str, value, arg, context):
    """Our result for the same case, with the loop the FFI owes the corpus.

    The argument is resolved and safe-marked the way `resolve` did it, because
    `_native.filter` is handed *values*, not template text — the loop that turns
    one into the other is the caller's half by design (Task 3: "arg values are
    resolved by the caller and passed in").

    **Which of those two things happens depends on the argument's shape, and that
    is measured rather than stylistic.** `resolve` (`base.py:800-806`)
    `mark_safe`s an argument only when it holds it as a *constant*
    (`lookup=False`), which a token can only be by being **quoted**; a bare
    number, name, `None` or `True` is a `var_arg` resolved by `Variable` and
    handed over unmarked. Measured on the pinned 6.0.9, same filter throughout:

        x|ljust:"5"   -> SafeString('5')     (constant, and so mark_safe'd)
        x|ljust:5     -> int 5               (var_arg, bare)
        x|ljust:5.0   -> float 5.0
        x|ljust:None  -> None
        x|ljust:True  -> True

    A `str` is the one shape the corpus can declare that renders as a quoted
    token, so it is the one shape that is a constant here; everything else is the
    bare value. `mark_safe`ing every argument made every numeric row compare two
    different inputs — an `int` against a `SafeString` — which is the raw-token
    defect `tests/test_filter_parity.py` had, one layer down. It is observable
    through any body that reads its argument's *type*: prepared as `mark_safe(5)`,
    our `cut` answers `'abc'` where Django raises `TypeError: replace() argument
    1 must be str, not int`; prepared as `5` it raises the same thing.

    `None` keeps its meaning of "no argument", and Django's body sees `None` for
    both `x|f` and `x|f:None` — the first never reaches a body, `base.py:819`'s
    arity check having raised — so the two are the same input here as they are
    there, and a row asks for one by declaring `None`.
    """
    if arg is None:
        resolved_arg = None
    elif isinstance(arg, str):
        resolved_arg = mark_safe(arg)
    else:
        resolved_arg = arg
    return _native.filter(
        name, value, resolved_arg, context.autoescape, effective_use_tz(context)
    )


def describe(result) -> str:
    """`(type, value)` plus safety, which is half of what a filter returns."""
    value = repr(result)
    if len(value) > 60:
        value = value[:57] + "..."
    return f"({type(result).__name__}, {value}) safe={isinstance(result, SafeData)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print Django's answer and ours for one filter case.",
        usage="filter_probe.py <filter> <value-literal> [<arg-literal>]",
    )
    parser.add_argument("filter", help="the filter's registry name, e.g. `upper`")
    parser.add_argument("value", help="the filter input, as a Python literal")
    parser.add_argument(
        "arg",
        nargs="?",
        default=None,
        help="the filter argument, as a Python literal",
    )
    args = parser.parse_args(argv)

    ensure_settings()

    try:
        value = ast.literal_eval(args.value)
        arg = None if args.arg is None else ast.literal_eval(args.arg)
    except (ValueError, SyntaxError) as error:
        print(f"filter_probe: not a Python literal: {error}", file=sys.stderr)
        return 2

    try:
        django_result, expression, context = django_answer(
            args.filter, value, args.arg
        )
    except TemplateSyntaxError as error:
        # Three different mistakes reach here — a name that is not a filter, a
        # filter that takes no argument, and one that takes a different one.
        # Django's own message names which, so it is printed rather than
        # summarised into something that would lose the distinction.
        print(f"filter_probe: {error}", file=sys.stderr)
        return 2
    print(f"  token   {expression.token}")
    print(f"  django  {describe(django_result)}")

    try:
        rust_result = rust_answer(args.filter, value, arg, context)
    except _native.UnimplementedTag:
        # A stub is not a mismatch, and saying so is worth the four lines: most
        # of the 57 are stubs for most of M4, so "not ours yet" is the answer
        # this script gives more often than anything else. Django's answer above
        # is still the answer to record in `CASES`.
        print("  rust    (stub — this filter's group has not landed yet)")
        return 0
    print(f"  rust    {describe(rust_result)}")

    if type(django_result) is not type(rust_result) or django_result != rust_result:
        print("  MISMATCH")
        return 1
    if isinstance(django_result, SafeData) != isinstance(rust_result, SafeData):
        print("  MISMATCH (safety only)")
        return 1
    print("  match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
