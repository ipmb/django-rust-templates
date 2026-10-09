"""The shell entry ``_native.compile_template`` needs (D5), and the loader for it.

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

from django.template.base import (
    UNKNOWN_SOURCE,
    DebugLexer,
    Lexer,
    Origin,
    Parser,
    Token,
    TokenType,
)
from django.template.context import Context
from django.template.engine import Engine
from django.template.exceptions import TemplateSyntaxError

from django_rust_templates import _native
from django_rust_templates.host import ShellHost

__all__ = (
    "UNKNOWN_ORIGIN",
    "django_exception",
    "django_token",
    "materialiser",
    "parse_template",
    "parser_for",
    "render_string",
)

# The origin a parse has when nothing loaded it — `Template.__init__`'s own
# default (`base.py:151-152`), spelled once here.
#
# **It is an `Origin` and not `None`,** and that is A3's correction rather than a
# convenience: under Design B a *host*-compiled block tag's nested `parser.parse()`
# reaches Django's own `do_extends` and `extend_nodelist`, which read
# `parser.origin.template_name`. Django never has `None` there, because every
# `Parser` Django builds itself comes from `Template.__init__`, which guarantees
# this value. The **Rust** side is unaffected either way:
# `Origin(UNKNOWN_SOURCE).template_name` is `None`, and the attribute is what
# crosses the seam.
UNKNOWN_ORIGIN = Origin(UNKNOWN_SOURCE)

# `django_rust_templates._impl.parser`, as a file. Resolved from this module's
# own location rather than from the working directory, so importing this from
# anywhere finds the same file.
_MATERIALISER_PATH = Path(__file__).resolve().parent / "_impl" / "parser.py"

_CACHED = None


def materialiser():
    """The module that turns Rust's node descriptors into Django's nodes.

    Returned as a module rather than as three functions because
    ``_native.compile_template`` wants all three — ``check_filter(parser)`` for
    the parse, ``compile_filter(parser)`` for each ``{% if %}`` operand (M5's
    ``if``, which Django also builds during the parse), and
    ``materialise(desc, None, None, parser)`` for the tree — and handing over one
    object keeps the seam to a single argument.

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


def django_token(wire):
    """Django's `Token` for `_native`'s four-field wire tuple, or `None`.

    The same construction `ShellHost.call_tag` uses on the same tuple, and it
    has to be the same: the wire order is `(type, contents, lineno, position)`
    while `Token.__init__` takes `(token_type, contents, position, lineno)`, so
    this is one of the two places the two orders meet.

    `token_type` is reconstructed through the `TokenType` enum rather than left
    as the wire's `int`, because `Token.__repr__` reads `self.token_type.name`
    and Django's debug path prints it.
    """
    if wire is None:
        return None
    kind, contents, lineno, position = wire
    return Token(TokenType(kind), contents, position, lineno)


def django_exception(error, host=None):
    """`_native`'s parse failure, as the exception Django would have raised.

    Rust raises **one** class for everything a parse rejects. That is design
    decision 2 — Rust owns the message, Python owns the exception *object*,
    because `assertRaisesMessage`, `Template.get_exception_info` and Django's
    own test runner all inspect the object rather than the message — and this is
    the place the object is chosen.

    **It is not optional at M9, and the plan omitted it.** Until M9 the suite's
    parses went through Django's real `Parser`, which raises `TemplateSyntaxError`
    itself, so nothing ever had to convert. M9's delegation makes
    `Template.compile_nodelist` the suite's only parse path, and without this the
    first run dies three ways at once, all measured: upstream's `except` arm
    hands `e.token` to `get_exception_info`, which reads `token.position` off the
    wire tuple and raises `AttributeError: 'tuple' object has no attribute
    'position'`; `assertRaises(TemplateSyntaxError)` sees a class it does not
    know; and Django's parallel runner cannot pickle the result —
    `Can't pickle <class '_native.UnimplementedTag'>: No module named '_native'`.

    **A host exception is restored by identity, not rebuilt.** When a tag's own
    code raised, `error.class_name` names the class Python raised, and the
    *object* is still on the host that ran it (``host.last_error``), so it is
    re-raised as itself — the same instance, with its `__traceback__`. That is
    what Django does: `Parser.parse` re-raises the object it caught
    (`base.py:583-585`). Rebuilding from a name and a message would lose the
    traceback, and matching on the name keeps this from handing back an object
    from an *earlier* host call than the one that failed.

    **A stale ``last_error`` cannot be matched here, and it is structural rather
    than lucky** — the reviewer asked, so the reasoning is written down instead
    of being re-derived. Three facts, each checked in the tree:

    * `ErrorKind::Host` is constructed at exactly one place,
      `python_host_error` (`crates/template-py/src/lib.rs:1104`), called only
      from the `call_tag` boundary (`crates/template-py/src/lib.rs:1251`), and
      ``class_name`` is read straight off that error — so a name in the wire
      error always came from a `call_tag` failure;
    * ``last_error`` is assigned in one place, `ShellHost.call_tag`
      (`python/django_rust_templates/host.py:547`), on the way out — so it
      always names a `call_tag` failure too;
    * a host error is **terminal**: the parse loop's arm is
      `Err(error) => Err(error)` (`parser/mod.rs:394-403`) and its caller is
      `?`, so no `call_tag` failure is caught and survived, and the failing call
      is necessarily the last one to have touched either field.

    **The fourth fact is measured, not structural, and saying so is the
    correction** — the reviewer asked, and they were right to. The first three
    are properties of this repository's own code. The fourth is a claim about
    control flow *above* Rust that Rust cannot enforce: a host tag's compile
    function runs in Python, calls `parser.parse(...)` itself, and could wrap
    that call in `try/except`, in which case the `call_tag` failure would be
    survived, the parse would continue, and ``last_error`` would be stale for a
    later, unrelated match. Nothing in Rust's loop prevents it, because the
    swallowing would happen above Rust.

    Measured at 6.0.9 rather than argued (``.review/m9-measurements/
    m9_measure_host_error_terminality.py``, which names its metric: a
    *function* in the pinned tree whose parse call sits inside a try block).
    Exactly one function in Django's tree does that, and it is
    compile_nodelist's own try (`django/template/base.py:198`), whose except arm
    re-raises after optionally annotating. No compile function swallows a nested
    parse, and there is no contextlib.suppress site. **That script must be
    re-run at M11**, where the population stops being the pinned tree: a
    third-party ``{% load %}``ed library catching its own body's errors is
    ordinary, and it would invalidate the identity restore without any file in
    this repository changing.

    ``host.last_error`` is therefore not cleared on read — clearing it would
    suggest a staleness that cannot arise today and would hide a *real* future
    regression in which the parse learned to survive a host error. If the
    measurement above ever comes back non-empty, the guard is an assertion that
    the stash is consumed exactly once per `Err` — not a clear-on-read, which
    would turn "the right exception" into ``None``, a different wrong answer
    rather than no answer.

    The host is per parse as well — `parse_template` builds a fresh
    `ShellHost` — so nothing survives between parses either. A second `call_tag`
    that succeeds leaves `last_error` at `None`, which is the only other state it
    can be in.

    **Otherwise only the class is chosen.** `args` rides across untouched, so
    the message stays Rust's, and `.token` is the same wire tuple turned into a
    real `Token`, which is what makes `e.token` usable by `get_exception_info`
    and by the `{% include %}` paths that read it. `UnimplementedTag` is a
    `ParseError` subclass and is converted with the rest: outside this repository
    the shell is a drop-in replacement, and a class Django has never heard of
    escaping a parse is not one. Nothing is lost by that — the parity harnesses
    that bucket on `UnimplementedTag` call `_native.parse` directly and never
    come through here.

    ``host`` is optional because the conversion is meaningful without one: a
    caller that drove `_native.compile_template` directly has no `ShellHost` to
    consult, and gets the class-only conversion.
    """
    class_name = getattr(error, "class_name", None)
    if class_name is not None and host is not None:
        raised = host.last_error
        if raised is not None and type(raised).__name__ == class_name:
            return raised

    converted = TemplateSyntaxError(*error.args)
    converted.token = django_token(getattr(error, "token", None))
    return converted


def parser_for(source, engine=None, origin=UNKNOWN_ORIGIN):
    """Django's own `Parser`, built the way Django builds one.

    The tokens are Django's because `Parser` is Django's, and `find_filter`
    reads `self.filters`, which comes from `builtins`. Nothing in this path
    parses with it: Rust owns the parse, and this object is a filter registry
    that happens to be Django's `Parser`.

    `engine` defaults to a fresh `Engine()` — which reads settings — because most
    callers have none to offer; when they do, it is the *caller's* engine whose
    `template_builtins` are used, so a filter library the caller registered is
    visible to the parse. That is the same reason `_native.compile_template` takes
    the parser rather than building one: Python knows which engine, and Rust
    cannot.

    `libraries` is the third thing the caller's engine decides, and it was
    missing until M6 — measured, `{% load custom %}` raised
    `'custom' is not a registered tag library` because `find_library` reads
    `parser.libraries` (`defaulttags.py:1065-1075`) and `Parser.__init__` fills it
    from `Engine.template_libraries`. Nothing but a `{% load %}` reads it, which
    is why its absence went unnoticed for five milestones.

    `origin` is M7's fourth, and it defaults to [`UNKNOWN_ORIGIN`] rather than to
    `None` for the reason that constant records. It reaches Django's `Parser`
    only: what Rust receives is `origin.template_name`, read in
    `_native.compile_template`.

    **The lexer is `DebugLexer` when the engine is in debug, and that arm was
    missing until M9's Task 6.** Upstream reads `self.engine.debug` at the top of
    `compile_nodelist` to choose between the two (`base.py:183-186`), and the
    difference is `Token.position` — which is not a debug nicety, it is what
    `Template.get_exception_info` unpacks (`start, end = token.position`) for
    every `template_debug` it builds. With a plain `Lexer` those tokens carry
    `None` and the annotation path dies inside its own `except`, *replacing* the
    exception being annotated with `TypeError: cannot unpack non-iterable
    NoneType object`. Measured on `tests/test_render_parity.py::django_render`,
    which parses with this parser and therefore lost the very annotation it was
    being asked about.

    Invisible on the Rust path, and that is why it survived: Rust lexes with its
    own lexer and materialises tokens that already carry correct positions
    (measured — a `{% for %}` node at ``(9, 25)``), so this parser's tokens are
    read only for `find_filter` and `extra_data` there. It bites exactly the
    callers that parse *with* it, which are the parity harnesses.
    """
    if engine is None:
        engine = Engine()
    lexer = DebugLexer(source) if engine.debug else Lexer(source)
    return Parser(
        lexer.tokenize(),
        builtins=engine.template_builtins,
        libraries=engine.template_libraries,
        origin=origin,
    )


def parse_template(source, engine=None, origin=UNKNOWN_ORIGIN):
    """Parse `source` with the Rust engine and return Django's `Template`.

    This is the shell's one source-to-`Template` path, and it is the shape
    `Engine.from_string` needs at M9 — hence returning the object rather than a
    rendered string. `render_string` is this plus `Template.render`.

    The `Parser` and the `ShellHost` are built here, before the parse, for the
    reason `render_string` records: the host snapshots `parser.tags` at
    construction to tell Django's own builtin callables from anything a
    `{% load %}` puts there.

    `extra_data` is **not** set here. It belongs to
    `_impl/parser.py::template_for`, which owns the whole attribute list because
    a `Template` built by `__new__` never runs `Template.__init__`'s
    `self.extra_data = parser.extra_data` (`base.py:198-200`); the parser reaches
    it as an argument. Setting it in two places is how the two would drift.

    `origin` reaches three places from here — the `Parser`, the Rust parse (as
    `origin.template_name`) and the materialised tree (as every node's `origin`
    and the `Template`'s) — and `_native.compile_template`'s doc records why the
    node's and the template's have to be the same object.

    **A parse failure leaves as `TemplateSyntaxError`, not as `_native.ParseError`.**
    This is the shell's only source-to-`Template` path, so it is the one place
    the conversion has to happen, and [`django_exception`] is it; putting it
    inside `compile_nodelist` instead would have meant editing upstream's method
    rather than transcribing it, which is the one thing M9 must not do.
    """
    if engine is None:
        engine = Engine()
    parser = parser_for(source, engine, origin)
    host = ShellHost(parser)
    try:
        return _native.compile_template(
            source,
            parser,
            materialiser(),
            engine,
            host,
            origin,
        )
    except _native.ParseError as error:
        # `from None`, because Django's own `Parser.parse` re-raises the *same*
        # object and therefore has no chain at all: letting CPython add one here
        # would print a `_native.ParseError` frame that stock Django's traceback
        # does not have.
        raise django_exception(error, host) from None


def render_string(source, context, engine=None, origin=UNKNOWN_ORIGIN):
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

    `origin` is forwarded to [`parse_template`], and through it to the reader
    that makes it observable: `{% extends %}` resolves a parent relative to
    `origin.template_name`, and `{% include %}` reads it at render time.
    """
    if isinstance(context, dict):
        context = Context(context)
    return parse_template(source, engine, origin).render(context)
