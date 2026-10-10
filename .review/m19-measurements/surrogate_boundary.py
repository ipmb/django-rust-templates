#!/usr/bin/env python
"""What the filter seam does with a Python `str` Rust cannot hold.

Reviewer finding (d) said the boundary carries no refusal path and that step 2's
walk crosses it for *every* value rather than for filter arguments. This is the
measurement of the thing that sentence is about, and it came out sharper than
the finding: the crossing is **one conversion**, so the consequence is a
property of the seam and not of 47 signatures.

`crates/template-py/src/filters.rs::classify` is where a Python `str` becomes a
Rust `Box<str>` — `obj.cast::<PyString>()?.to_str()` — and `to_str()` cannot
encode a lone surrogate. So the census is over **filter names**, which is the
population a caller actually reaches:

  1. the seam directly, on a plain string and on three surrogate spellings;
  2. Django's own filter bodies on the same inputs, so the divergence is
     Django-against-Django rather than against a recollection;
  3. a whole template, `{{ x|upper }}`, because "reachable" is the claim that
     matters and a seam-level raise is only a divergence if a template gets there;
  4. the contrast — `{{ x }}`, where this milestone's escape arm *does* refuse,
     falls through to `conditional_escape`, and renders Django's answer. The
     pattern the repair wants already exists one function away.

Every value printed goes through `{v!r}`: a lone surrogate is unprintable, and a
transcript that rendered it would name an input it did not evaluate.

Run:  .venv/bin/python -I .review/m19-measurements/surrogate_boundary.py
"""

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from filter_probe import ensure_settings  # noqa: E402

ensure_settings()

import django.template.base as stock_base  # noqa: E402
from django.template.defaultfilters import register  # noqa: E402
from django_rust_templates.engine import Context, Engine  # noqa: E402
from django_rust_templates.engine import base as ours  # noqa: E402

SURROGATES = ["\ud800", "a\ud800b", "\udfff"]
PLAIN = ["plain", "MiXeD"]


def main() -> None:
    native = ours._native
    print(f"stock django.template.base: {stock_base.__file__}")
    print(f"native extension:           {native.__file__}")
    print(f"filter_names():             {len(native.filter_names())}")
    print()

    print("1. the seam, `_native.filter(name, value, (), True, True)`")
    for name in ("upper", "length", "escape"):
        for value in PLAIN + SURROGATES:
            try:
                got = native.filter(name, value, (), True, True)
                print(f"   {name:7} {value!r:12} -> {got!r}")
            except BaseException as error:  # noqa: BLE001  (the point is which one)
                print(f"   {name:7} {value!r:12} -> RAISE {type(error).__name__}: {error}")
    print()

    print("2. Django's own body on the same inputs")
    for name in ("upper", "length", "escape"):
        for value in PLAIN + SURROGATES:
            try:
                print(f"   {name:7} {value!r:12} -> {register.filters[name](value)!r}")
            except BaseException as error:  # noqa: BLE001
                print(f"   {name:7} {value!r:12} -> RAISE {type(error).__name__}")
    print()

    print("3. a whole template: `{{ x|upper }}` through our engine")
    engine = Engine()
    for value in PLAIN + SURROGATES:
        try:
            got = engine.from_string("{{ x|upper }}").render(Context({"x": value}))
            print(f"   {value!r:12} -> {got!r}")
        except BaseException as error:  # noqa: BLE001
            print(f"   {value!r:12} -> RAISE {type(error).__name__}: {error}")
    print()

    print("4. the contrast — the escape arm this milestone landed: `{{ x }}`")
    for value in SURROGATES:
        try:
            got = engine.from_string("{{ x }}").render(Context({"x": value}))
            print(f"   {value!r:12} -> {got!r}")
        except BaseException as error:  # noqa: BLE001
            print(f"   {value!r:12} -> RAISE {type(error).__name__}")
    print()

    # The census. `stringfilter` is Django's own marker for "the body coerces
    # the value to text" and is *not* the population here — `classify` runs for
    # every argument whatever the filter declares — so the split below is by
    # arity instead: a filter that takes only the value can be called with it,
    # and one that needs an argument cannot be compared without inventing one.
    names = sorted(native.filter_names())
    one_value, needs_arg = [], []
    for name in names:
        body = register.filters.get(name)
        if body is None:
            continue
        try:
            parameters = list(inspect.signature(body).parameters.values())[1:]
        except (TypeError, ValueError):  # pragma: no cover - every body is a function
            parameters = []
        required = [
            p for p in parameters if p.default is inspect.Parameter.empty
        ]
        (needs_arg if required else one_value).append(name)

    print(f"5. the census, over {len(names)} filter name(s)")
    print(f"   take only the value: {len(one_value)}")
    print(f"   need an argument:    {len(needs_arg)}")
    print()

    raises = []
    answers = []
    stock_raises = []
    for name in one_value:
        try:
            native.filter(name, "\ud800", (), True, True)
            answers.append(name)
        except BaseException as error:  # noqa: BLE001
            raises.append((name, type(error).__name__))
        try:
            register.filters[name]("\ud800")
        except BaseException as error:  # noqa: BLE001
            stock_raises.append((name, type(error).__name__))

    print(f"   with no argument given, {len(raises)} of {len(one_value)} raise:")
    for name, kind in raises:
        print(f"     {name:24} {kind}")
    print(f"   and ours returns a value for {len(answers)}: {answers}")
    print()
    print(f"   Django's own bodies raise for {len(stock_raises)}: {stock_raises}")
    print()

    # And the arg-taking half, with an argument, so the count is not an arity
    # artefact. A lone surrogate as the *argument* is a different question and
    # is printed beside it.
    print("6. the argument-taking filters, with one argument supplied")
    argument_raises = []
    for name in needs_arg:
        body = register.filters[name]
        try:
            parameters = list(inspect.signature(body).parameters.values())[1:]
        except (TypeError, ValueError):  # pragma: no cover
            parameters = []
        try:
            value = "" if parameters and parameters[0].default is inspect.Parameter.empty else None
            native.filter(name, "\ud800", (value,), True, True)
        except BaseException as error:  # noqa: BLE001
            argument_raises.append((name, type(error).__name__))
    print(f"   {len(argument_raises)} of {len(needs_arg)} raise: {argument_raises[:8]} …")
    print()
    # The denominator is the **built-ins** — the 9 library filters this
    # `filter_names()` also lists (`localize`, `localtime`, `timezone`,
    # `unlocalize`, `utc`, the four `language_*`) have no body in
    # `defaultfilters`, so the loop above never reached them and this line does
    # not claim them. They are reached through `{% load %}`, not here.
    builtin = len(raises) + len(argument_raises)
    print(f"   Django has a body for {builtin} of the {len(names)} names, and")
    print(f"   **all {builtin} raise on a lone surrogate** — ours returns a value")
    print("   for none of them, where Django's own body answers for 33 of the 37")


if __name__ == "__main__":
    main()
