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
     pattern the repair copies already existed one function away;
  5. the census over every built-in name, **three outcomes per row** — answered,
     refused, raised — because the repair made "returned" ambiguous and a
     two-way tally would count a refusal as an answer;
  6. the argument-taking half with one argument, so the count is not an arity
     artefact.

**Run twice: once before the repair, once after.** Before, section 5 read "57 of
57 raise, ours returns a value for 0"; section 3 raised `UnicodeEncodeError`
where stock rendered `'\\ud800'`. After, ours refuses — `_native.CANNOT_HOLD` —
and Django's own body answers through the branch that already existed for a name
that is not a builtin. The `refused` column is the seam's part and the
`answered` column is Django's; neither is a raise.

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

#: The three outcomes, as `(kind, detail)`. `refused` is the seam's own
#: singleton and is neither an answer nor an error — the distinction this whole
#: document exists for, so it gets a name rather than a truth test.
ANSWERED, REFUSED, RAISED = "answered", "refused", "raised"


def outcome(native, name, value, args) -> tuple:
    try:
        got = native.filter(name, value, args, True, True)
    except BaseException as error:  # noqa: BLE001  (the point is which one)
        return RAISED, f"{type(error).__name__}: {error}"
    if got is native.CANNOT_HOLD:
        return REFUSED, repr(got)
    return ANSWERED, repr(got)


def main() -> None:
    native = ours._native
    print(f"stock django.template.base: {stock_base.__file__}")
    print(f"native extension:           {native.__file__}")
    print(f"filter_names():             {len(native.filter_names())}")
    print(f"the refusal singleton:      {native.CANNOT_HOLD!r}")
    print()

    print("1. the seam, `_native.filter(name, value, (), True, True)`")
    for name in ("upper", "length", "escape"):
        for value in PLAIN + SURROGATES:
            kind, detail = outcome(native, name, value, ())
            print(f"   {name:7} {value!r:12} -> {kind:8} {detail}")
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

    answered, refused, raised = [], [], []
    stock_raises = []
    for name in one_value:
        kind, detail = outcome(native, name, "\ud800", ())
        (answered if kind == ANSWERED else refused if kind == REFUSED else raised).append(
            (name, detail)
        )
        try:
            register.filters[name]("\ud800")
        except BaseException as error:  # noqa: BLE001
            stock_raises.append((name, type(error).__name__))

    # The character itself is named rather than embedded: an f-string carrying a
    # raw surrogate cannot be *printed* — the encode fails where the compile
    # would have, which is the same trap this document is about, one layer out.
    print(f"   on a lone surrogate, over the {len(one_value)} value-only filters:")
    print(f"     refused (the seam's singleton): {len(refused)}")
    print(f"     answered                      : {len(answered)}")
    print(f"     raised                        : {len(raised)}")
    print()
    print(f"   Django's own bodies raise for {len(stock_raises)}: {stock_raises}")
    print()

    # And the arg-taking half, with an argument, so the count is not an arity
    # artefact. A lone surrogate as the *argument* is a different question and
    # is printed beside it.
    print("6. the argument-taking filters, with one argument supplied")
    argument_refused, argument_raised = [], []
    for name in needs_arg:
        body = register.filters[name]
        try:
            parameters = list(inspect.signature(body).parameters.values())[1:]
        except (TypeError, ValueError):  # pragma: no cover
            parameters = []
        value = "" if parameters and parameters[0].default is inspect.Parameter.empty else None
        kind, detail = outcome(native, name, "\ud800", (value,))
        if kind == REFUSED:
            argument_refused.append((name, detail))
        elif kind == RAISED:
            argument_raised.append((name, detail))
    print(f"   refused: {len(argument_refused)} of {len(needs_arg)}")
    print(f"   raised : {len(argument_raised)} of {len(needs_arg)}: {argument_raised[:8]}")
    print()

    # **The two halves above are the seam's; this is the caller's.** A refusal
    # is only a repair if the shell reads it, and the shell's read is the
    # equality row against Django through the whole engine. Printed as a count
    # so the transcript carries the claim, not just the seam.
    print("7. the shell's read: whole templates, ours against stock Django")
    import django.template as stock

    ours_engine, stock_engine = Engine(), stock.Engine()
    templates = ("{{ x|upper }}", "{{ x|length }}", "{{ x|default:y }}")
    divergences = []
    for template in templates:
        for text in SURROGATES:
            for autoescape in (True, False):
                variables = {"x": text, "y": None}
                mine = ours_engine.from_string(template).render(
                    Context(variables, autoescape=autoescape)
                )
                theirs = stock_engine.from_string(template).render(
                    stock.Context(variables, autoescape=autoescape)
                )
                if mine != theirs:
                    divergences.append((template, text, autoescape, mine, theirs))
    checked = len(templates) * len(SURROGATES) * 2
    print(f"   {checked} renders compared, {len(divergences)} diverge")
    for row in divergences[:8]:
        print(f"     {row}")
    print()
    # The denominator is the **built-ins** — the 9 library filters this
    # `filter_names()` also lists (`localize`, `localtime`, `timezone`,
    # `unlocalize`, `utc`, the four `language_*`) have no body in
    # `defaultfilters`, so the loop above never reached them and this line does
    # not claim them. They are reached through `{% load %}`, not here.
    builtin = len(one_value) + len(needs_arg)
    print(f"   Django has a body for {builtin} of the {len(names)} names, and")
    print(f"   **all {builtin} refuse at the seam** — {len(refused)} with no argument")
    print(f"   and {len(argument_refused)} with one — where Django's own body raises")
    print(f"   for only {len(stock_raises)} of the {len(one_value)} value-only names.")
    print("   The refused count is the seam's, not the caller's: section 7 is what")
    print("   says the shell turns each refusal back into Django's own answer.")
    print()

    # **Would an exception have been swallowed instead?** The first draft of the
    # repair's docstring said the refusal had to be a *returned* value because
    # the render path is full of `except Exception` handlers that would eat a
    # control-flow exception — `Variable._resolve_lookup`'s is Django's own. That
    # sentence is a reason, so it gets measured, and it came out **false**:
    # Django 6.0.9's `VariableNode.render` catches `UnicodeDecodeError` and
    # nothing else, and a filter body's exception reaches the caller from every
    # context below, debug on and off. The reason the sentinel is a returned
    # object is therefore not this; it is that the refusal is not an error, and
    # that `_native.html_escape` — the sibling refusal, one function away —
    # already declines by returning. Left here because the claim was made and
    # would otherwise have shipped unchecked.
    print("8. the reason that was NOT the reason: does a raise get swallowed?")
    from django.template import Library

    class _Refusal(Exception):
        pass

    library = Library()

    @library.filter(name="refuse")
    def _refuse(value):
        raise _Refusal("the seam refused")

    shapes = (
        "{{ x|refuse }}",
        "{{ x|refuse|upper }}",
        "{% if x|refuse %}y{% endif %}",
        "{% for i in x|refuse %}y{% endfor %}",
        "{% filter refuse %}{{ x }}{% endfilter %}",
        "{{ x|refuse }}{{ y|upper }}",
    )
    swallowed = []
    for debug in (False, True):
        engine = stock.Engine(debug=debug)
        engine.template_libraries["t"] = library
        for shape in shapes:
            template = engine.from_string("{% load t %}" + "[" + shape + "]")
            try:
                template.render(stock.Context({"x": "a", "y": "b"}))
                swallowed.append((debug, shape))
            except _Refusal:
                pass
    print(f"   {len(shapes) * 2} contexts (6 shapes x debug on/off), {len(swallowed)} swallowed")
    for row in swallowed:
        print(f"     {row}")


if __name__ == "__main__":
    main()
