#!/usr/bin/env python
"""The four engines this benchmark compares, behind one three-method interface.

    django      stock Django's DTL, the control every ratio is taken against
    rust        this package's engine
    jinja2      Jinja2, through Django's *own* ``Jinja2`` backend class
    minijinja   minijinja, through an equivalent backend written here

The interface is deliberately tiny — ``get``/``from_string``/``render`` — so that
the harness in ``bench.py`` never learns which engine it is driving and cannot
drift into per-engine special cases that quietly make one side's work smaller.

**Jinja2 is wired with Django's backend class and not a hand-rolled
``Environment``.** ``django.template.backends.jinja2.Jinja2`` is the canonical
wiring — it sets ``autoescape=True``, ``auto_reload=settings.DEBUG`` (False here)
and ``undefined=jinja2.Undefined`` — and re-deriving those three defaults by hand
is how a benchmark ends up measuring a stricter environment than a user would
ever run. It is driven from a ``DictLoader`` through ``OPTIONS['loader']``, which
is the documented hook for a non-filesystem loader.

**minijinja has no such class, so one is written here** and it is written to
mirror ``Jinja2``'s shape rather than to be convenient: ``BaseEngine`` subclass,
a ``Template`` wrapper, ``auto_escape_callback`` for the autoescape that the
binding leaves off by default. It lives in ``scripts/`` and not in the package
on purpose — it is benchmark scaffolding, and ``python/django_rust_templates/``
is the thing being shipped.

**Why these two are installed outside the venv.** ``scripts/env.sh`` and
``requirements-dev.txt`` both record the reason: ``requirements-dev.txt``
deliberately omits jinja2 because five ``template_tests`` rows skip when it is
absent, and ``scripts/assert_suite_outcome.py`` pins *exactly* those six skips by
identity. Three of the six are ``Jinja2TemplateReloadTests``, which skip because
``import jinja2`` fails. Install jinja2 into ``.venv`` and the acceptance run
ends ``OK (skipped=3)`` and the project's own gate goes red — the benchmark would
have broken the criterion it exists to measure against. So they go in
``.bench-deps`` (gitignored) and are put on ``sys.path`` by this module, and
``missing_engines`` below reports which ones are absent instead of quietly
running three engines.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BENCH_DEPS = PROJECT_ROOT / ".bench-deps"

# The install line, printed verbatim when an engine is missing, because a reader
# who has to reconstruct it from the traceback is a reader who will instead run
# the three-engine table and believe it.
INSTALL_HINT = (
    f"python -m pip install --target {BENCH_DEPS.name} jinja2 minijinja   "
    f"(from {PROJECT_ROOT})"
)

ENGINES = ("django", "rust", "jinja2", "minijinja")
CONTROL = "django"

# The class that must have built each engine, checked before a table is printed.
# A table is a claim about four engines, so which engine produced a column is the
# one thing that must never be inferred from the numbers.
IDENTITY = {
    "django": "django.template.engine.Engine",
    "rust": "django_rust_templates.engine.engine.Engine",
    "jinja2": "django.template.backends.jinja2.Jinja2",
    "minijinja": "django_rust_templates_bench.MiniJinja",
}


def _bench_deps_available() -> bool:
    return (BENCH_DEPS / "jinja2").is_dir() and (BENCH_DEPS / "minijinja").is_dir()


if _bench_deps_available():
    # Appended to ``sys.path`` rather than prepended: if a dependency of the
    # project proper ever gains the same name, the venv's copy is the one the
    # rest of the tree imports and this must not shadow it.
    if str(BENCH_DEPS) not in sys.path:
        sys.path.append(str(BENCH_DEPS))


def missing_engines() -> list[str]:
    """Which of the four cannot be built here, so the caller can say so."""
    out = []
    if not _bench_deps_available():
        out.extend(["jinja2", "minijinja"])
        return out
    try:
        import jinja2  # noqa: F401
    except ImportError:
        out.append("jinja2")
    try:
        import minijinja  # noqa: F401
    except ImportError:
        out.append("minijinja")
    return out


# --- the two Django-side engines -------------------------------------------------


class DjangoEngine:
    """Stock Django or this package's engine — same call shape, different modules.

    **A plain dict goes in, on every engine, and each one does its own wrapping.**
    ``Template.render`` in 6.0.9 does *not* call ``make_context`` — it reaches
    straight for ``context.render_context`` and raises ``AttributeError`` on a
    dict — so a caller must hand the Django engines a real ``Context``. The
    ``Context`` class is therefore carried here, and ``context()`` below is where
    the wrap happens: **once per (engine, row), outside the timed loop**, which is
    the choice the M16 harness made and therefore the one the baseline table this
    report is compared against was measured under. The alternative — building it
    per call — is what a Django *view* pays, and it costs both Django engines the
    same ~0.5 us; it is not measured here, and the report says so rather than
    leaving a reader to assume either way.

    The ``Context`` also has to be the *right* one: ours is
    ``django_rust_templates.engine.context.Context``, a transcription and not
    Django's class, so handing Django's to our engine is a type error deep inside a
    render rather than a comparable run.
    """

    def __init__(self, name, engine_cls, context_cls, loader_path, templates, libraries):
        self.name = name
        self.control = name == CONTROL
        self._context_cls = context_cls
        self._engine = engine_cls(
            loaders=[(loader_path, dict(templates))],
            libraries=dict(libraries),
        )

    @property
    def identity(self):
        cls = type(self._engine)
        return f"{cls.__module__}.{cls.__qualname__}"

    def get(self, name):
        return self._engine.get_template(name)

    def from_string(self, source):
        return self._engine.from_string(source)

    def context(self, data):
        return self._context_cls(data)

    def render(self, handle, context):
        return handle.render(context)

    def close(self):
        pass


# --- Jinja2, through Django's own backend ----------------------------------------


class Jinja2Engine:
    def __init__(self, templates):
        import jinja2

        from django.template.backends.jinja2 import Jinja2

        self.name = "jinja2"
        self.control = False
        self._backend = Jinja2(
            {
                "NAME": "bench-jinja2",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"loader": jinja2.DictLoader(dict(templates))},
            }
        )

    @property
    def identity(self):
        cls = type(self._backend)
        return f"{cls.__module__}.{cls.__qualname__}"

    def get(self, name):
        return self._backend.get_template(name)

    def from_string(self, source):
        return self._backend.from_string(source)

    def context(self, data):
        # Jinja2 has no ``Context``; the dict is the context. Passed through
        # unchanged rather than copied, so the copy is Jinja2's own to make.
        return data

    def render(self, handle, context):
        return handle.render(context)

    def close(self):
        pass


# --- minijinja, through the equivalent backend written here ---------------------


class _Inline:
    """A `from_string` handle for minijinja, which has no template object."""

    __slots__ = ("source",)

    def __init__(self, source):
        self.source = source


class MiniJinjaEngine:
    """minijinja behind the same interface as Django's ``Jinja2`` backend.

    **The binding exposes no template handle.** ``minijinja.Environment`` has
    ``add_template`` / ``render_template(name, **ctx)`` / ``render_str``, and no
    ``get_template``; the compiled template is not reachable from Python. So
    ``get`` returns the *name* and ``render`` looks it up again — and a
    *pre-compiled* render on this engine therefore carries one name-table lookup
    that the other three do not. It is small and it is not zero, so ``bench.py``
    prints it in the row's note rather than leaving a reader to discover it. The
    parse is not repeated: minijinja caches the compiled template in the
    environment, which is what ``add_template`` populated.
    """

    def __init__(self, templates):
        import minijinja

        self.name = "minijinja"
        self.control = False
        self._env = minijinja.Environment(
            loader=lambda name: self._sources[name],
            # The binding leaves autoescape off; Django's Jinja2 backend turns it
            # on. Matching the backend this is compared against is the point.
            auto_escape_callback=lambda _name: True,
        )
        self._sources = dict(templates)
        for name, source in self._sources.items():
            self._env.add_template(name, source)

    @property
    def identity(self):
        return "django_rust_templates_bench.MiniJinja"

    def get(self, name):
        return name

    def from_string(self, source):
        # Not `add_template`: it would key every inline source on one name and
        # raise on the second call. `render_str` compiles and renders in one
        # crossing, which is what a `from_string` caller gets on this engine.
        return _Inline(source)

    def context(self, data):
        return data

    def render(self, handle, context):
        if isinstance(handle, _Inline):
            return self._env.render_str(handle.source, **context)
        return self._env.render_template(handle, **context)

    def close(self):
        pass


# --- construction ----------------------------------------------------------------


def build(name: str, templates: dict, libraries: dict) -> object:
    """The engine `name` names, loading exactly `templates` and `libraries`."""
    if name == "django":
        from django.template.context import Context
        from django.template.engine import Engine

        return DjangoEngine(
            name,
            Engine,
            Context,
            "django.template.loaders.locmem.Loader",
            templates,
            libraries,
        )
    if name == "rust":
        from django_rust_templates.engine.context import Context
        from django_rust_templates.engine.engine import Engine

        return DjangoEngine(
            name,
            Engine,
            Context,
            "django_rust_templates.engine.loaders.locmem.Loader",
            templates,
            libraries,
        )
    if name == "jinja2":
        return Jinja2Engine(templates)
    if name == "minijinja":
        return MiniJinjaEngine(templates)
    raise ValueError(f"unknown engine {name!r}")
