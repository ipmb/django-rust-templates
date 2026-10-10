"""Does the adapter row's ``(module, name)`` key compare its module half at all?

`tests/test_suite_isolation.py`'s ``test_every_declared_adapter_is_upstreams_own_import``
builds a set of ``(module, name)`` pairs out of the pinned checkout and asks whether
every declared adapter is in it. The first version of that set took its **module**
from the vendored statement being checked and only its **name** from the pin::

    for relative, module, names, _ in django_imports_in(SUITE):
        ...
        upstream.add((module, alias.name))          # module is the *vendored* module

The row's stated job is refusing a declared import whose *module* Django does not
have that name in — `from django.template import mock` — and a set built that way
cannot do it, because every vendored module is a ``django.template`` path by
construction of the walk, so the module half is in the set no matter what the pin
says. The fix is one token, ``node.module``.

**This measures the fix rather than arguing it.** Two claims, both about the real
tree and the real pin:

1. the old key admits a pair whose name is not in the pin's module at all, and the
   new one does not;
2. the verdict on the seven **declared** adapters is the same either way — so the
   fix is not a change of policy that happens to leave the set green, it is a
   change of *question* that the set happens to answer the same way.

Claim 2 is the one that could have gone the other way, and it is why the fix was
run against the whole declared set before it was believed.

**Why this imports the real module and does not restate it.** The walk, the
declared set and the pin path are all read out of `tests/test_suite_isolation.py`
by importing it, so the probe cannot agree with a copy of the code while
disagreeing with the code. That import needs `tests/__init__.py`, which configures
settings, so this runs under the project venv::

    .venv/bin/python -I .review/m18-measurements/probe_adapter_module_axis.py

`-I` keeps the interpreter's directory and the user site off `sys.path`; the
insert below is what puts the repository root back on it, which is the one thing
`-I` cannot be allowed to take away.

**What the numbers are not.** ``upstream`` here is the pin's absolute
``ImportFrom`` names over the pin files that mirror vendored files — the row's own
population, not every line in `.django/tests/`. A pair missing from it may still
exist elsewhere in Django's suite; the row's claim is narrower than that and so is
this.
"""

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.test_suite_isolation import (  # noqa: E402
    DECLARED_ADAPTERS,
    SUITE,
    UPSTREAM,
    django_imports_in,
)


def upstream_pairs(key_module_on):
    """The pin's ``(module, name)`` pairs, over the files vendored from it.

    ``key_module_on`` is the whole probe: ``"pin"`` takes the module from the pin's
    own node (the row as fixed), ``"vendored"`` from the statement being checked
    (the row as first written).
    """
    out = set()
    for relative, module, names, _ in django_imports_in(SUITE):
        pin = UPSTREAM / relative.removeprefix("django_suite/")
        if not pin.is_file():
            continue
        for node in ast.walk(ast.parse(pin.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom) or node.level:
                continue
            for alias in node.names:
                key_module = node.module if key_module_on == "pin" else module
                out.add((key_module, alias.name))
    return out


def main():
    both = {k: upstream_pairs(k) for k in ("pin", "vendored")}
    print(f"pin files read           {len({r for r, *_ in django_imports_in(SUITE)})}")
    print(f"pairs, module from pin   {len(both['pin'])}")
    print(f"pairs, module from vendored {len(both['vendored'])}")

    print()
    print("--- claim 1: pairs whose name the pin's module does not have ---")
    # `mock` and `render_to_string` are `unittest`'s and `django.template.loader`'s;
    # neither is bound by `django/template/__init__.py`. `engines` is bound there.
    for module, name in (
        ("django.template", "mock"),
        ("django.template", "render_to_string"),
        ("django.template.loader", "engines"),
    ):
        print(
            f"  {module!r} + {name!r}:  vendored-keyed {((module, name) in both['vendored'])!r}"
            f"   pin-keyed {((module, name) in both['pin'])!r}"
        )

    print()
    print("--- claim 2: the seven declared adapters, under both keys ---")
    for key in ("vendored", "pin"):
        missing = sorted(
            (path, module, names)
            for path, module, names in DECLARED_ADAPTERS
            if not all((module, name) in both[key] for name in names)
        )
        print(f"  module from {key:8}  not upstream: {len(missing)}  {missing!r}")

    print()
    print("--- the pin's own line, which claim 1's first pair is about ---")
    init = ROOT / ".django" / "django" / "template" / "__init__.py"
    bound = set()
    for node in ast.parse(init.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ImportFrom):
            bound.update(a.name for a in node.names)
        elif isinstance(node, ast.Assign):
            bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
    for name in ("mock", "render_to_string", "engines"):
        print(f"  django/template/__init__.py binds {name!r}: {name in bound!r}")


if __name__ == "__main__":
    main()
