"""Does the suite's parse path reach Rust's filter bodies, or Django's Python ones?

M11 interposes `defaultfilters`. The question that decides what the milestone is
worth: with the shim installed *today*, when `{{ x|lower }}` is parsed, which
callable ends up in `FilterExpression.filters`? If it is Django's
`defaultfilters.lower`, the Rust filter layer M4 built is reached by nothing in
the 1547 and interposition is the commit that puts it on the path. If it is a
Rust-backed delegate, M11 changes the module without changing the path.

Run:  . scripts/env.sh && .venv/bin/python .review/m11-measurements/m11_measure_filter_path.py

**Not `-I`.** `-I` implies `-s` and ignores `PYTHONPATH`, and the shim is
installed by a `sitecustomize` on `PYTHONPATH` (`scripts/env.sh`): under `-I`
this script measures stock Django and would have "proved" the filter path is
Django's by measuring a run in which our engine was never installed.
"""

import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / ".django"))

import django  # noqa: E402
import django.template.defaultfilters as our_or_their  # noqa: E402

from django.template.base import Parser  # noqa: E402
from django.template.engine import Engine  # noqa: E402

print("shim installed :", "django_rust_templates" in (our_or_their.__file__ or ""))
print("defaultfilters :", our_or_their.__file__)
print("django.template.base:", sys.modules["django.template.base"].__file__)

engine = Engine()
parser = Parser([], builtins=engine.template_builtins)
for name in ("lower", "upper", "date", "escape", "urlize"):
    func = parser.find_filter(name)
    print(f"  find_filter({name!r}) -> {getattr(func, '__module__', '?')}.{getattr(func, '__name__', '?')}")

t = engine.from_string("{{ x|upper }}")
node = t.nodelist[0]
for func, args in node.filter_expression.filters:
    print(f"  FilterExpression filter -> {func!r}")

from django.template import defaultfilters as df  # noqa: E402

print("register.filters['lower'] ->", repr(df.register.filters["lower"]))
