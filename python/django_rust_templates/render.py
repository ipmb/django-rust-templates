"""The shell entry ``_native.render`` needs (D5), and the loader for its tree.

Rust parses; Python owns the node tree (§6, §5.5). Materialising a
``FilterExpression`` calls ``parser.find_filter(name)`` and the real
``FilterExpression.args_check``, so the parser has to be a real
``django.template.base.Parser`` — and building one inside Rust would mean reaching
for ``Engine().template_builtins``, which is a *host callback* and therefore M6's.
So the caller supplies it.

**This module is not under ``_impl``, and that is a correction to the plan.**
The plan put it at ``python/django_rust_templates/_impl/engine.py``. That path is
a defect, and it was measured rather than reasoned about: ``_shim``'s finder
serves *any* ``_impl/<name>.py`` as ``django.template.<name>``, so a file named
``engine.py`` there is what ``_impl/__init__.py``'s ``from .engine import
Engine`` resolves to once the shim is installed, and installing it then fails
with::

    ImportError: cannot import name 'Engine' from 'django.template.engine'
    (/Users/…/python/django_rust_templates/_impl/engine.py)

``_impl/parser.py`` gets away with the same trick only because Django has no
``template/parser.py`` to shadow — which is why the rule is "no ``_impl`` file
may take the name of a real ``django.template`` module", not "``_impl`` is
closed". ``django_rust_templates.render`` breaks nothing: it is outside the
shimmed package entirely.

**``materialiser()`` loads its module from a file, and that is not laziness
either.** ``_impl/__init__.py`` is the shim's ``django.template`` and imports its
own siblings (``from .engine import Engine``), so ``import
django_rust_templates._impl.parser`` executes that file and fails for want of
``engine``::

    ModuleNotFoundError: No module named 'django_rust_templates._impl.engine'

Under the shim the name ``django.template.parser`` *would* resolve, so the lookup
is tried first and the file is the fallback. That ordering makes M9 a no-op here:
once the shim is installed for real, the first branch is taken and nothing else
changes.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

from django.template.base import Lexer, Parser
from django.template.context import Context
from django.template.engine import Engine

from django_rust_templates import _native
from django_rust_templates.host import ShellHost

__all__ = ("materialiser", "parser_for", "render_string")

# `django_rust_templates._impl.parser`, as a file. Resolved from this module's
# own location rather than from the working directory, so importing this from
# anywhere finds the same file.
_MATERIALISER_PATH = Path(__file__).resolve().parent / "_impl" / "parser.py"

_CACHED = None


def materialiser():
    """The module that turns Rust's node descriptors into Django's nodes.

    Returned as a module rather than as two functions because ``_native.render``
    wants both halves — ``check_filter(parser)`` for the parse and
    ``materialise(desc, None, None, parser)`` for the tree — and handing over one
    object keeps the seam to a single argument. It wants a third since M5's
    ``if``: ``compile_filter(parser)``, the ``FilterExpression`` behind each
    ``{% if %}`` operand, which Django also builds during the parse.

    Idempotent: both branches go through ``sys.modules``, so the file is executed
    once however many times this is called.
    """
    global _CACHED
    if _CACHED is not None:
        return _CACHED

    if importlib.util.find_spec("django.template.parser") is not None:
        # Only once the shim is installed: Django 6.0.9 has no
        # `django/template/parser.py`, so this is `None` on the pinned tree
        # without it. A probe on the spec rather than a `try: import` around the
        # module body, which would also swallow an ImportError raised *inside*
        # the materialiser.
        _CACHED = importlib.import_module("django.template.parser")
        return _CACHED

    # The name is the one the tests already use for it. It is deliberately not
    # registered as a submodule of a package that would be imported: the loader
    # does not touch `django_rust_templates._impl`, which is the whole point.
    name = "django_rust_templates._impl.parser"
    spec = importlib.util.spec_from_file_location(name, _MATERIALISER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    _CACHED = module
    return _CACHED


def parser_for(source, engine=None):
    """Django's own `Parser`, built the way Django builds one.

    The tokens are Django's because `Parser` is Django's, and `find_filter`
    reads `self.filters`, which comes from `builtins`. Nothing in this path
    parses with it: Rust owns the parse, and this object is a filter registry
    that happens to be Django's `Parser`.

    `engine` defaults to a fresh `Engine()` — which reads settings — because most
    callers have none to offer; when they do, it is the *caller's* engine whose
    `template_builtins` are used, so a filter library the caller registered is
    visible to the parse. That is the same reason `_native.render` takes the
    parser rather than building one: Python knows which engine, and Rust cannot.

    `libraries` is the third thing the caller's engine decides, and it was
    missing until M6 — measured, `{% load custom %}` raised
    `'custom' is not a registered tag library` because `find_library` reads
    `parser.libraries` (`defaulttags.py:1065-1075`) and `Parser.__init__` fills it
    from `Engine.template_libraries`. Nothing but a `{% load %}` reads it, which
    is why its absence went unnoticed for five milestones.
    """
    if engine is None:
        engine = Engine()
    return Parser(
        Lexer(source).tokenize(),
        builtins=engine.template_builtins,
        libraries=engine.template_libraries,
    )


def render_string(source, context, engine=None):
    """Render `source` with the Rust engine, Django supplying the filters.

    `context` is a Django `Context`, or a plain mapping that is wrapped in one.
    The wrapping is a shell convenience and it is what makes the parity test a
    parity test: a caller can hand the *same* mapping to both engines instead of
    building the two sides' inputs differently. It is not a claim about
    `Template.render`, which takes a `Context` and is M9's.

    **`engine` reaches the render path itself**, not only the parser, and leaving
    it out was a measured divergence rather than an omission: `Template.render`
    reads `context.template.engine.string_if_invalid` for a failed lookup, so a
    shell that always built a default `Engine` rendered `''` where stock Django
    rendered the caller's `string_if_invalid` —
    `tests/test_render_parity.py::test_the_engine_the_caller_passes_is_the_one_that_renders`
    is the row. A `None` engine is a fresh `Engine()`, which is what a caller with
    no engine to offer gets.

    No exception handling: the parse's `TemplateSyntaxError` and the render's
    own exceptions must both reach the caller as themselves, because
    `tests/test_render_parity.py` compares types and messages as well as output.

    **The `ShellHost` is built here, before the parse, and that timing is
    load-bearing**: it snapshots `parser.tags` at construction to tell Django's
    own builtin callables from anything a `{% load %}` puts there. Built after
    the parse it would see the post-load table and every loaded tag would read as
    a builtin.
    """
    if engine is None:
        engine = Engine()
    if isinstance(context, dict):
        context = Context(context)
    parser = parser_for(source, engine)
    return _native.render(
        source,
        context,
        parser,
        materialiser(),
        engine,
        ShellHost(parser),
    )
