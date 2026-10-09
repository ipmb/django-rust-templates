"""Is `test_render_parity.py`'s "stock Django" side still stock?

`tests/test_render_parity.py::RenderParityTests.assert_renders_like_django`
compares two renders of the same source:

    ours = render_string(source, context)
    theirs = self.engine.from_string(source).render(Context(context))

Its module docstring says the second is "stock Django" and that a passing case
is therefore evidence about the Rust *parse*. That was true while
`django.template.base` was Django's: `Engine.from_string` built Django's
`Template`, whose `__init__` ended in Django's `compile_nodelist` and Django's
`Lexer`/`Parser`.

M9 Task 2 interposes `base`, so `Engine.from_string` now builds *our*
`Template`, whose `compile_nodelist` delegates to `render.parse_template` — the
same function `render_string` calls. If that is right, both sides of every row
in that class now parse with Rust and the comparison is Rust against Rust: the
vacuous-gate failure this project has hit before, and the one
`tests/parity_target.py` exists to refuse for the parser harness.

**The measurement is a call count, not a reading of the source.** It enters
`render.parse_template` through a counter and asks each side to render the same
template, then asserts the two reached it the same number of times and that the
objects they built are the same class.

Run it the way the suite is run, because `django.template.base` must be ours:

    . scripts/env.sh
    PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \\
        python .review/m9-measurements/m9_measure_render_parity_oracle.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHECKOUT = os.path.join(ROOT, ".django")

if os.environ.get("DJANGO_RUST_TEMPLATES") != "1":
    sys.exit("DJANGO_RUST_TEMPLATES is not 1; the shim would not be installed")

sys.path.insert(0, os.path.join(ROOT, "scripts", "shimpath"))
sys.path.insert(0, CHECKOUT)
sys.path.insert(0, ROOT)

import django.template.base as base_module  # noqa: E402
from django.template import Context, Engine  # noqa: E402

from django_rust_templates import render  # noqa: E402

import django_rust_templates  # noqa: E402

SOURCE = "{% if a %}{{ a }}{% else %}none{% endif %}"

print("=== is `django.template.base` ours? ===")
print(f"django.template.base.Template -> {base_module.Template.__module__}")
print(f"  file: {sys.modules[base_module.Template.__module__].__file__}")

calls = []


def counting(*args, **kwargs):
    calls.append(args[0] if args else None)
    return _real(*args, **kwargs)


_real = render.parse_template
# Both sides reach it as `render.parse_template` (imported inside
# `_impl/base.py::compile_nodelist`, and called directly by `render_string`), so
# one patch intercepts both.
render.parse_template = counting
try:
    print()
    print("=== the two sides of `assert_renders_like_django` ===")

    calls.clear()
    ours = render.render_string(SOURCE, {"a": "A"})
    ours_calls = len(calls)
    print(f"render_string(...)             -> {ours!r}  ({ours_calls} parse call(s))")

    calls.clear()
    theirs = Engine().from_string(SOURCE).render(Context({"a": "A"}))
    theirs_calls = len(calls)
    print(f"Engine().from_string(...)      -> {theirs!r}  ({theirs_calls} parse call(s))")

    print()
    print("=== does the second side touch Rust's parse at all? ===")
    print(f"identical outputs:            {ours == theirs}")
    print(f"both entered render.parse_template: {bool(ours_calls and theirs_calls)}")
    print(
        "same Template class on both sides: "
        f"{type(render.parse_template).__name__} -> "
        f"{type(base_module.Template(SOURCE, engine=Engine())).__name__}"
    )
finally:
    render.parse_template = _real

print()
print("=== what the count means ===")
if ours_calls and theirs_calls:
    print(
        "VACUOUS: `Engine.from_string` reaches the Rust parse, so both sides of\n"
        "every RenderParityTests row parse and compile identically. The row is\n"
        "Rust against Rust and its green says nothing about the parse."
    )
else:
    print(
        "not vacuous: exactly one side reaches the Rust parse, so the row still\n"
        "compares Rust's parse against Django's."
    )
