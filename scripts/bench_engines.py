#!/usr/bin/env python
"""The three engines this benchmark compares, behind one three-method interface.

    django      stock Django's DTL, the control every ratio is taken against
    rust        this package's engine
    jinja2      Jinja2, through Django's *own* ``Jinja2`` backend class

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

**minijinja was the fourth engine and is gone.** It was wired through a backend
written here to mirror ``Jinja2``'s shape. The benchmark is now the two
comparisons that matter for this package — the engine it replaces, and the one
it is most often weighed against — and the removal is recorded rather than
silent, because a reader comparing against an earlier report will see a column
disappear. Two things it cost, both now carried in the corpus instead of hidden:
minijinja was the only one of the four that escaped the solidus, and its
``{% else %}`` was a second arm on the `for`-loop split. Every exclusion the
corpus still makes is re-measured over the three that remain
(`.review/m19-measurements/subset_probe.py`).

**Both Django-side engines get a *cached* loader, and that is the default's own
shape.** ``Engine(loaders=None)`` — what every real project gets for not setting
the key — builds the filesystem and app-directories loaders and wraps them in
``cached.Loader``, so a partial is parsed once and afterwards only rendered. This
module used to hand the two engines a bare ``locmem.Loader`` instead, which
re-parses ``{% extends %}``'s parent and every ``{% include %}`` on **every
render**; Jinja2 caches compiled templates by construction (its ``DictLoader``
goes through the environment's template cache), so the corpus was making one
side's work larger than the other's — the per-engine asymmetry the paragraph
above says the harness cannot allow itself. Measured, it is not small: the
``extends`` row goes 18.4 -> 9.7 us for stock and 30.5 -> 9.5 us for this package
(``.review/m19-measurements/loader_cache_probe.py``). The nesting below is
Django's own — ``[(cached.Loader, [(locmem.Loader, templates)])]`` — spelled with
each engine's *own* loader classes, for the same reason the ``Context`` is.

**Why Jinja2 is installed outside the venv.** ``scripts/env.sh`` and
``requirements-dev.txt`` both record the reason: ``requirements-dev.txt``
deliberately omits jinja2 because five ``template_tests`` rows skip when it is
absent, and ``scripts/assert_suite_outcome.py`` pins *exactly* those six skips by
identity. Three of the six are ``Jinja2TemplateReloadTests``, which skip because
``import jinja2`` fails. Install jinja2 into ``.venv`` and the acceptance run
ends ``OK (skipped=3)`` and the project's own gate goes red — the benchmark would
have broken the criterion it exists to measure against. So it goes in
``.bench-deps`` (gitignored) and is put on ``sys.path`` by this module, and
``missing_engines`` below reports what is absent instead of quietly printing a
two-engine table.
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
    f"python -m pip install --target {BENCH_DEPS.name} jinja2   "
    f"(from {PROJECT_ROOT})"
)

ENGINES = ("django", "rust", "jinja2")
CONTROL = "django"

# The class that must have built each engine, checked before a table is printed.
# A table is a claim about three engines, so which engine produced a column is the
# one thing that must never be inferred from the numbers.
IDENTITY = {
    "django": "django.template.engine.Engine",
    "rust": "django_rust_templates.engine.engine.Engine",
    "jinja2": "django.template.backends.jinja2.Jinja2",
}


def _bench_deps_available() -> bool:
    return (BENCH_DEPS / "jinja2").is_dir()


if _bench_deps_available():
    # Appended to ``sys.path`` rather than prepended: if a dependency of the
    # project proper ever gains the same name, the venv's copy is the one the
    # rest of the tree imports and this must not shadow it.
    if str(BENCH_DEPS) not in sys.path:
        sys.path.append(str(BENCH_DEPS))


def missing_engines() -> list[str]:
    """Which of the three cannot be built here, so the caller can say so."""
    out = []
    if not _bench_deps_available():
        out.append("jinja2")
        return out
    try:
        import jinja2  # noqa: F401
    except ImportError:
        out.append("jinja2")
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

    def __init__(
        self,
        name,
        engine_cls,
        context_cls,
        loader_path,
        cached_path,
        templates,
        libraries,
    ):
        self.name = name
        self.control = name == CONTROL
        self._context_cls = context_cls
        self._engine = engine_cls(
            loaders=[(cached_path, [(loader_path, dict(templates))])],
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
                "OPTIONS": {
                    "loader": jinja2.DictLoader(dict(templates)),
                    # `{% trans %}` is an extension in Jinja2 where it is a
                    # built-in library in Django. Loaded unconditionally so the
                    # translation row has something to compare; it costs a row
                    # that does not translate nothing, because Jinja2 only emits
                    # gettext calls for templates that use the tag.
                    "extensions": ["jinja2.ext.i18n"],
                },
            }
        )
        # **Django's own gettext, not Jinja2's null translations**, which is what
        # a real Django project running the Jinja2 backend installs. The
        # difference is not cosmetic: measured, `install_null_translations`
        # returns each msgid immediately while Django's `gettext` performs a
        # catalogue lookup that misses, and the null path is **1.80x cheaper**.
        # Installing it would hand Jinja2 a shortcut this engine and Django's own
        # both pay for, and the translation row would be reporting that gap as a
        # property of the engines.
        from django.utils import translation as _django_translation

        self._backend.env.install_gettext_translations(
            _django_translation, newstyle=True
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
            "django.template.loaders.cached.Loader",
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
            "django_rust_templates.engine.loaders.cached.Loader",
            templates,
            libraries,
        )
    if name == "jinja2":
        return Jinja2Engine(templates)
    raise ValueError(f"unknown engine {name!r}")
