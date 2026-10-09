#!/usr/bin/env python
"""The library-table reads `host.py` makes on the hot path, timed in place.

`host._library_module`'s docstring quotes two figures — ``importlib.import_module``
at **0.285 us** against the ``sys.modules`` lookup at **0.036 us** — and
`_library_tag_tables`' quotes the reason they matter (both tables are read on the
hot path, once per tag and once per filter *application*). This is the probe
those numbers came from, committed so they can be re-run rather than believed.

It also times the whole `django_own_filters()` call and the arms it feeds, which
is what M16's record needs: the routing branch is the shim's per-filter tax, and
`django_own_filters()` is the largest single term in it.

Shim side only — `django_rust_templates.host` does not exist under stock Django:

    source scripts/env.sh
    DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \
        .venv/bin/python .review/m16-measurements/m16_measure_library_tables.py
"""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

# Same bootstrap as `scripts/bench.py`: one place configures settings, and
# `filter_probe` also supplies the `.django` `sys.path` insert under `-I`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import django  # noqa: E402
from django.template import Context, Engine  # noqa: E402
from django.template.defaultfilters import register  # noqa: E402
from django_rust_templates import _native  # noqa: E402
from django_rust_templates.host import (  # noqa: E402
    _LIBRARY_TAG_MODULES,
    _library_filter_tables,
    django_own_filters,
    effective_autoescape,
    effective_use_tz,
)

ITER = 200_000


def best_us(fn, iters: int = ITER) -> float:
    """Best-of-5 per-call microseconds — the same estimator `scripts/bench.py` uses."""
    for _ in range(1000):
        fn()
    best = float("inf")
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(iters):
            fn()
        best = min(best, (time.perf_counter() - start) / iters)
    return best * 1e6


def main() -> int:
    context = Context({"a": "x"})
    func = register.filters["upper"]
    own = django_own_filters()

    name = _LIBRARY_TAG_MODULES[1]  # i18n — the first one with filters
    importlib.import_module(name)  # put it in sys.modules, as the hot path has it

    rows = [
        ("sys.modules.get (hit)", lambda: sys.modules.get(name)),
        ("importlib.import_module", lambda: importlib.import_module(name)),
        ("_library_module", lambda: _library_module_public(name)),
        ("_library_filter_tables()", _library_filter_tables),
        ("django_own_filters()", django_own_filters),
        ("django_own_filters().get(func)", lambda: own.get(func)),
        ("effective_use_tz", lambda: effective_use_tz(context, func)),
        ("effective_autoescape", lambda: effective_autoescape(context, func)),
        ("_native.filter(upper)", lambda: _native.filter("upper", "x", (), True, False)),
        ("no-op lambda (floor)", lambda: None),
    ]
    for label, fn in rows:
        print(f"{label:32s} {best_us(fn):8.3f} us")

    def arm():
        f = func
        if (builtin := django_own_filters().get(f)) is not None:
            use_tz = effective_use_tz(context, f)
            autoescape = effective_autoescape(context, f)
            return _native.filter(builtin, "x", (), autoescape, use_tz)

    print(f"{'the whole routing arm':32s} {best_us(arm):8.3f} us")
    print()
    print(f"own_filters() is a dict of {len(own)} handles;")
    print(f"sources: {len(_library_filter_tables())} library tables + defaultfilters")

    # Control: reading the same registries without the merge machinery, so the
    # 1 us is attributable to the calls and not to the attribute reads.
    sysmod = sys.modules
    mods = tuple(sysmod.get(m) for m in _LIBRARY_TAG_MODULES)

    def raw_reads():
        return (mods[0].register.filters, mods[1].register.filters,
                mods[2].register.filters, mods[3].register.filters,
                mods[4].register.filters, register.filters)

    print(f"{'the six reads, inlined (control)':32s} {best_us(raw_reads):8.3f} us")

    engine = Engine()
    template = engine.from_string("{{ a|upper }}")
    template.render(context)
    print(f"{'Engine tiny render (context)':32s} {best_us(lambda: template.render(context), 20000):8.3f} us")
    return 0


def _library_module_public(name):
    from django_rust_templates.host import _library_module

    return _library_module(name)


if __name__ == "__main__":
    sys.exit(main())
