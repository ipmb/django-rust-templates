"""The shell's half of the host seam: the parser Django's own tag code needs.

Rust owns the token cursor; **the host owns parser state** — `tags`, `filters`,
`libraries`, `extra_data`, and whatever a user tag sets on the parser. One real
`django.template.base.Parser` is kept for the whole parse and Rust hands it the
tokens it may consume. `Parser.parse` already leaves the end tag on the stack for
`delete_first_token` (`base.py:564-569`), so `Library.simple_block_tag`'s
``nodelist = parser.parse((end_name,)); parser.delete_first_token()``
(`library.py:211-212`) runs unmodified.

**A `has_tag` that answered `command in parser.tags` would break every built-in**,
since every built-in is in that dict: Rust would be told to compile nothing and the
host would run Django's `do_for` etc. The discrimination is whether the entry *is*
the callable Django itself registered. Measured on the pinned tree:
``p.tags["comment"] is defaulttags.register.tags["comment"]`` is ``True``, and
``False`` after ``p.add_library(lib)`` for a library registering ``comment``.
"""

from __future__ import annotations

import importlib
import sys
import types

from django.utils.datastructures import DeferredSubDict

from django_rust_templates import _native
from django_rust_templates.engine import defaultfilters as _defaultfilters
from django_rust_templates.engine import defaulttags as _defaulttags
from django_rust_templates.engine import loader_tags as _loader_tags
from django_rust_templates.engine.base import PartialTemplate, Token, TokenType
from django_rust_templates.engine.exceptions import TemplateSyntaxError

__all__ = ("ShellHost",)


# The five `django.templatetags` libraries M12 interposes, as module paths.
_LIBRARY_TAG_MODULES = (
    "django_rust_templates.engine.templatetags.cache",
    "django_rust_templates.engine.templatetags.i18n",
    "django_rust_templates.engine.templatetags.l10n",
    "django_rust_templates.engine.templatetags.static",
    "django_rust_templates.engine.templatetags.tz",
)


def _library_tag_tables():
    """The five libraries' `register.tags`, imported **lazily**.

    The deferral is order, not identity: ``host`` is imported from inside
    ``engine/base.py``, so executing five library modules while ``base`` is on the
    stack is a cycle this file does not have to take.

    **The read is still fresh per call; only ``importlib``'s machinery is skipped.**
    ``sys.modules.get`` returns the module *currently* registered, exactly as
    ``import_module`` would, so a re-executed library is still seen — the identity
    property `django_own_filters` depends on. Measured: **0.275 us** for
    ``importlib.import_module`` against **0.046 us** for the ``sys.modules`` lookup
    it ends in, a **6x** difference (re-run by
    `.review/m16-measurements/m16_measure_library_tables.py`).
    """
    return tuple(_library_module(name).register.tags for name in _LIBRARY_TAG_MODULES)


def _library_module(name):
    """The library module named ``name``, importing it if this is the first ask.

    A `sys.modules` lookup rather than ``importlib.import_module``: ~6x slower
    (0.275 us vs 0.046 us) and the same object, since the import system has already
    registered it on every call but the first. The ``None`` branch is what keeps the
    import lazy, so the deferral [`_library_tag_tables`] documents still holds.
    """

    module = sys.modules.get(name)
    if module is None:
        module = importlib.import_module(name)
    return module


def _library_filter_tables():
    """The same five libraries' `register.filters`, imported the same lazy way.

    `cache` and `static` register no filters (measured, `register.filters` is empty
    in both), so this tuple contributes nine callables; the *module set* is shared
    with [`_library_tag_tables`] so a sixth library need not be added in two places.
    """
    return tuple(
        _library_module(name).register.filters for name in _LIBRARY_TAG_MODULES
    )


_DJANGO_OWN_TAGS = None

# The registry objects `django_own_tags` was built from, in merge order: ``None``
# alone cannot tell "not built yet" from "built from a registry no longer in force".
_DJANGO_OWN_TAGS_SOURCE = None


def django_own_tags():
    """The tag callables Rust ports — read from the modules it ports, once.

    The predicate's question is *"is this callable the one Rust implements?"*,
    and the only configuration-independent answer names the modules Rust ports:
    `django.template.defaulttags`, `django.template.loader_tags`, and the five
    `django.templatetags` libraries. Cached at first use against the registry
    objects it was built from, so neither a `{% load %}` nor a caller mutating a
    `register` mid-parse can move it, and a *replaced* registry is rebuilt rather
    than served stale.

    **Libraries merge *first*, defaulttags and loader_tags last, and that order
    is the safety property rather than a preference.** `update` gives the last
    writer the name, and what this table must never lose is Django's *own*
    callable for the 27 built-ins. Were a library to register `if`, a
    libraries-last merge would make this table's `if` the library's — so a
    `{% load %}`ed rebind of `if` would compare *equal* to it, `has_tag` would
    answer "Rust's", and Rust's `do_if` would compile over the caller's tag. The
    collision does not exist at 6.0.9 — measured, the five registries and the two
    share no name — and `LibraryTagPredicateTests` holds both halves.

    **Rust's registry is a subset of this table, never equal to it.** Measured
    against `.django` @ 6.0.9: the union of `defaulttags`' and `loader_tags`'
    tables is **27** names and Rust's registry is **25**, with `rust - django`
    empty; the five libraries' 18 names are in this table and not in Rust's.
    Containment is the only direction that has to hold, because the clause below
    compares against this table — a Rust-owned name missing from it would put
    `None` on the right of an `is not`, so the escape resolves the wrong way and
    the host compiles a tag Rust owns, silently, with the suite green.
    `TagPredicateCoverageTests` in `tests/test_interposition.py` holds it.

    The two names here that Rust does not own — `load` and `querystring` — never
    reach that comparison: Rust has no `do_load`/`do_querystring`, so `has_tag`'s
    early return answers before this table is consulted at all.
    """
    global _DJANGO_OWN_TAGS, _DJANGO_OWN_TAGS_SOURCE
    sources = (
        *_library_tag_tables(),
        _defaulttags.register.tags,
        _loader_tags.register.tags,
    )
    cached = _DJANGO_OWN_TAGS_SOURCE
    if (
        _DJANGO_OWN_TAGS is None
        or cached is None
        or len(cached) != len(sources)
        or any(old is not new for old, new in zip(cached, sources))
    ):
        merged = {}
        for table in sources:
            merged.update(table)
        _DJANGO_OWN_TAGS = merged
        _DJANGO_OWN_TAGS_SOURCE = sources
    return _DJANGO_OWN_TAGS


_DJANGO_OWN_FILTERS = None

# The registry objects the map above was built from, in merge order: ``None`` alone
# cannot tell "not built yet" from "built against a registry no longer in force".
_DJANGO_OWN_FILTERS_SOURCE = None


def django_own_filters():
    """The filter callables Rust ports, keyed by **handle** — the registry inverted.

    `FilterExpression` holds the `find_filter` *handle*, not a name (the name is
    what a `{% load %}` rebinds), so nothing is available to look a callable up by.
    The inversion is faithful because it is **injective**: measured against
    `.django` @ 6.0.9, 66 names over 66 *distinct* handles, so no name is lost and
    no two collide. That measurement is a row, not a comment — an aliased pair
    would otherwise pick a winner silently and Rust would run the wrong body.

    **Six registries merge, libraries first and `defaultfilters` last, and that
    order is the safety property rather than a preference** — the same rule as
    `django_own_tags`, for the same reason. `update` gives the last writer the
    name, and this table must never lose `defaultfilters`' own callable for the 57
    built-ins: were a library to register `lower`, a libraries-last merge would put
    the library's object under that name, and Rust would run its body over the
    caller's function. The collision does not exist at 6.0.9 — the six registries
    share no name — and `tests/test_interposition.py` holds both halves.

    **Handle identity, not name identity, is the tags' decision restated for the
    table that has no names.** A `{% load %}`ed library rebinding `lower`
    (`add_library` is `self.filters.update(lib.filters)`, `base.py:670`) puts a
    *different* object in `parser.filters`, absent from this map, so the library's
    callable runs. A library registering a name *for a built-in's own object*
    (`lib.add_filter("shout", lower)`) is *present*, so Rust runs — also correct,
    since the library asked for the function Rust implements. A name-keyed arm
    could express neither.

    **Cached against the registry *objects*, not against ``None``.** A cache keyed
    on emptiness answers "already built" for a map built from registries that no
    longer exist, and the consequence is silent and total: re-executing
    `defaultfilters` (or a library) replaces `register.filters` with fresh handles,
    all absent from the stale map, so every filter falls through to the Python loop
    and **every test stays green**. The identity of all six sources is therefore
    part of the cache key, as in [`django_own_tags`]. Read once, at first use: the
    sources are `Library` registries, which `{% load %}` does not touch, so a
    per-filter read would only rebuild the 66-entry dict on every filter.
    `FilterRoutingTests` in `tests/test_interposition.py` pins both halves.
    """
    global _DJANGO_OWN_FILTERS, _DJANGO_OWN_FILTERS_SOURCE
    sources = (*_library_filter_tables(), _defaultfilters.register.filters)
    cached = _DJANGO_OWN_FILTERS_SOURCE
    if (
        _DJANGO_OWN_FILTERS is None
        or cached is None
        or len(cached) != len(sources)
        or any(old is not new for old, new in zip(cached, sources))
    ):
        merged = {}
        for table in sources:
            merged.update(table)
        _DJANGO_OWN_FILTERS = {func: name for name, func in merged.items()}
        _DJANGO_OWN_FILTERS_SOURCE = sources
    return _DJANGO_OWN_FILTERS


def django_own_filter_names(filters):
    """Rust's name for each handle in ``filters``, ``None`` where the handle is not ours.

    Computed **once per expression** rather than once per filter *application*.
    The handles are frozen at compile time and the question is a property of the
    handle alone, so an expression has one answer; asking per application cost
    1.074 us a call — 69.8 us of ``real-world/normal``'s 620.8 us render, 23.4% of
    ``micro/filter``'s 5.34 us (best of 7 x 20000, CPython 3.14.4, release ``.so``).

    ``django_own_filters()`` itself is unchanged, so what is dropped is a *re*-read
    after this expression's handles were bound. A replacement landing in that
    window changes only which of two bodies runs for the *same* handle — the Rust
    one, or the transcribed Python one ``tests/test_filter_parity.py`` holds
    byte-identical to it — never the answer's kind, which still runs on a handle
    and never a name.

    **Filled lazily, on first ``resolve``, and that is not a style choice.** The
    descriptor materialiser (``engine/parser.py``) builds its object with
    ``__new__`` and never calls ``__init__``, and ``FilterExpression.__init__`` is
    on the parity harness's **compared path** (``check_transcriptions.COMPARED_PATH``),
    where an added line would be a declared difference inside an executed function.
    ``resolve`` is not on it, and one fill site there covers both routes. The
    caller guards with ``if self.filters:`` because the fill costs one
    ``AttributeError`` per expression: measured before the guard,
    ``real-world/large`` — which has no filters — went 2900 us to 3114/3469 us in
    ``parse`` mode while its ``render`` row did not move.
    """
    if not filters:
        return {}
    own = django_own_filters()
    return {func: own.get(func) for func, _ in filters}


def effective_autoescape(context, func):
    """The ``bool`` ``_native.filter`` must be handed for ``func``, upstream's laziness kept.

    The rule is `effective_use_tz`'s, on upstream's other guarded branch:
    ``context.autoescape`` is read only inside
    ``if getattr(func, "needs_autoescape", False):`` (``base.py:812-815``), so a
    filter whose flag is unset must be handed a value without touching
    ``context``. Django's own suite calls `resolve` with a plain ``dict``
    (``.django/tests/template_tests/test_parser.py:207``), legal precisely because
    upstream never reaches into ``context`` there; reading it for all 57 turned
    that row into ``AttributeError: 'dict' object has no attribute 'autoescape'``
    while the other 1546 stayed green.

    ``False`` for the filters that do not read it, which is not a guess:
    ``FilterCall::autoescape`` (``crates/template-core/src/filters/host.rs:33-35``)
    is read by exactly the seven ``needs_autoescape`` bodies. The ``getattr``
    default is upstream's own, so a library filter registered without the flag
    behaves here as it does there.
    """
    if getattr(func, "needs_autoescape", False):
        return context.autoescape
    return False


def effective_use_tz(context, func):
    """The ``bool`` ``_native.filter`` must be handed for ``func``, upstream's laziness kept.

    ``Context.use_tz`` is **three-valued**, and the third value is the one that
    matters: ``None`` means "ask ``settings.USE_TZ``". Upstream's
    ``template_localtime`` (``django/utils/timezone.py:146-161``) is where that
    fallback happens — ``settings.USE_TZ if use_tz is None else use_tz`` — and
    ``_native.filter``'s ``use_tz`` is a ``bool``, so it must happen before the
    FFI. Collapsing ``None`` to ``False`` is **not** the same value: with
    ``settings.USE_TZ`` true and the context's ``use_tz`` unset, upstream
    localizes and ``False`` would not.

    **The guard comes first because the attribute is read only for a filter that
    asks for it.** Upstream reaches ``context.use_tz`` only under
    ``if getattr(func, "expects_localtime", False):`` (``base.py:810-811``).
    Writing the test the other way round (``context.use_tz is None and
    getattr(...)``) reads ``context.use_tz`` for all 57 filters, which raises on
    the ``dict`` context Django's own suite passes; that and the ``autoescape``
    read next to it are what failed ``test_filter_numeric_argument_parsing``.

    **The read order between the two branches is upstream's and is observable.**
    ``base.py`` tests ``expects_localtime`` before ``needs_autoescape``, so for a
    ``dict`` context ``{{ x|date }}`` raises ``AttributeError: ... 'use_tz'`` while
    ``{{ x|join }}`` raises ``... 'autoescape'``. The arm therefore computes this
    one *first* and hands the results to the FFI in the FFI's own argument order.

    For every other filter the value is ``False`` and Rust never reads it: no
    body other than the two ``expects_localtime`` filters branches on
    ``FilterCall::use_tz`` (``filters/host.rs``).
    """
    if not getattr(func, "expects_localtime", False):
        return False
    if context.use_tz is None:
        from django.conf import settings

        return settings.USE_TZ
    return bool(context.use_tz)


def _wire_token(token):
    """A Django `Token` as the four-field tuple Rust's `_native.lex` emits.

    Same order as `engine/parser.py::_decode_token`: Django's signature is
    `Token(token_type, contents, position=None, lineno=None)`, so the wire is
    `(token_type, contents, lineno, position)` and the constructor calls reorder it.
    """
    return (
        token.token_type.value,
        token.contents,
        token.lineno,
        token.position,
    )


# `_rust_owned` — the names Rust has a real compile function for — computed
# **once at import** rather than once per parse.
#
# A module-level constant because nothing in it varies: all three `_native` calls
# are argument-free reads of compile-time-static Rust tables, so the set is the
# same for every `ShellHost`. Built in `ShellHost.__init__` it cost **4.86 us** on
# every compile of every template, against a stock-Django floor of ~1.45 us;
# hoisting took the empty-template compile from **9.89 us to 4.38 us** (6.8x stock
# to 3.0x). `__init__` binds `self._rust_owned` to this constant, so the tests
# that read `host._rust_owned` still see it. It is *not* a cache and must not
# become one: the tables are constants of the built artefact. If a future
# milestone makes any of the three calls depend on runtime state, this hoist is
# the line that goes wrong.

# The built-in half is the registry's keys *minus* the stub names, not the keys
# whole: a stub is a key with a body that raises `Unimplemented`, so taking the
# keys as the registry claimed `partial`/`partialdef` for Rust and the identity
# clause then declined them — measured, 245 of the 1547 failures. The subtraction
# is the rule the rest of the tree already uses (`tests/test_parser_parity.py`),
# and it stays written this way rather than collapsing to the keys so that a
# future half-built entry gets the correct answer by construction. The live stub
# set is empty; the subtrahend's derivation is pinned one layer down in
# `crates/template-core/src/parser/registry.rs`.
#
# The library half is the eighteen `django.templatetags` names Rust has bodies
# for, so `library_tag_names()` is now every name the five libraries register.
# The union and the table are therefore the same set: `library_tag_loaded` answers
# `command in parser.tags` — true for every loaded library name — and the Rust arm
# it feeds compiles exactly when `LIBRARY_REGISTRY` has a body, so a name claimed
# without a body (as `blocktranslate`/`blocktrans` were) would route a loaded tag
# past `has_tag` into Rust's `Invalid block tag`, refusing a template Django
# renders.
_RUST_OWNED = (
    set(_native.builtin_tag_names_including_stubs())
    - set(_native.builtin_tag_stub_names())
) | set(_native.library_tag_names())




# --------------------------------------------------------------------------
# Stream-free host calls: the classifier.
#
# `call_tag` ships every remaining token in and takes every remaining token
# back, so a template with N host tags builds O(N x tokens) Python objects
# where Django builds O(tokens). A compile function that provably never reads
# `parser.tokens` can skip the transfer in both directions.
#
# "Provably" is the whole of the difficulty, and it is **transitive**: a stock
# `simple_tag` compiler reaches `parse_bits` -> `compile_filter` ->
# `find_filter`, every one of them overridable. Identity of the compile
# function alone was measured to be insufficient. See
# `docs/superpowers/specs/2026-10-13-stream-free-host-calls-design.md` §5.
# --------------------------------------------------------------------------


class StreamFreeContractViolation(RuntimeError):
    """A stream-free compile function reached for the token stream anyway.

    An ordinary Python host error, deliberately: it travels through
    `python_host_error`, is stashed in `last_error`, and is restored by identity
    in `render.django_exception` like any compile function's exception. A second
    mechanism would be a second thing to get right.
    """


class _UnavailableStream:
    """What `parser.tokens` holds during a stream-free call: a thing that raises.

    Armed in **production as well as debug**, because neither alternative is
    safe. An empty list lets a misclassified compile function run
    ``while parser.tokens:`` and silently skip its body. Leaving the attribute
    alone is worse: `_DeferredTokenParser` deliberately has no `tokens` before
    the first host call (`python/django_rust_templates/render.py:275`), so the first read lexes the whole
    source and hands back the *entire original template*, already-consumed
    tokens included.

    **Not a `list` subclass, and that is the point** — there is no operation it
    can satisfy quietly. A tripped sentinel is an integration failure, and the
    callback is never replayed on the ordinary path: it may already have mutated
    `parser.tags` or `extra_data`.
    """

    __slots__ = ("_command",)

    def __init__(self, command):
        self._command = command

    def _refuse(self, operation):
        raise StreamFreeContractViolation(
            f"{operation} on parser.tokens during the stream-free compile of "
            f"{self._command!r}: this tag was classified as never reading the "
            f"token stream, so no stream was supplied. See "
            f"docs/superpowers/specs/2026-10-13-stream-free-host-calls-design.md."
        )

    # `__repr__` refuses like the rest, and `Parser.__repr__` renders
    # `self.tokens` with `%r` (`base.py:566`) — so a diagnostic that reprs the
    # parser raises a second time from inside the first exception's handling.
    # Nothing in the error path may repr either object.
    def __bool__(self):
        self._refuse("bool()")

    def __len__(self):
        self._refuse("len()")

    def __iter__(self):
        self._refuse("iteration")

    def __getitem__(self, index):
        self._refuse("subscript")

    def __delitem__(self, index):
        self._refuse("del")

    def __repr__(self):
        self._refuse("repr()")

    def pop(self, *args):
        self._refuse("pop()")

    def append(self, item):
        self._refuse("append()")


def _capture_trusted():
    """Provenance, read once at import and never again.

    The generated compile functions are reached by **registering throwaway tags
    on a throwaway `Library`** rather than by editing `library.py`, whose
    `simple_tag` is a `COMPARED_PATH` span (`scripts/check_transcriptions.py:250`)
    and so cannot carry a declared divergence at all.

    Code objects are derived per interpreter and never persisted: nothing
    requires a 3.12 code object to equal a 3.13 or 3.14 one.
    """
    from django_rust_templates.engine.library import Library

    probe = Library()

    @probe.simple_tag
    def _simple():  # pragma: no cover - never called
        return ""

    @probe.inclusion_tag("_probe.html")
    def _inclusion():  # pragma: no cover - never called
        return {}

    simple, inclusion = probe.tags["_simple"], probe.tags["_inclusion"]
    return {
        "codes": frozenset({simple.__code__, inclusion.__code__}),
        # `compile_func` is defined in `library.py`, so its globals are that
        # module's dict. A function carrying the genuine code object but
        # *foreign* globals resolves `parse_bits` through its own and can consume
        # tokens while `library.parse_bits` is untouched — measured.
        "globals": simple.__globals__,
    }


_TRUSTED = _capture_trusted()


def _captured(module, name):
    return getattr(module, name)


def _class_binding(module, name, methods):
    """A guarded class: the binding **and** the methods that run at compile time.

    Each alone is insufficient, and both failures were measured. Keeping
    `SimpleNode` bound to one class says nothing about its `__init__`; but
    replacing it with a *subclass* that inherits the captured `__init__` and adds
    a `__new__` leaves the initializer identity `True` while the compiler calls
    the replacement.
    """
    cls = getattr(module, name)
    return (module, name, cls, {m: getattr(cls, m, None) for m in methods})


def _guard_tables():
    from django_rust_templates.engine import base as _base
    from django_rust_templates.engine import library as _library

    generated_functions = [
        (_base.Parser, "compile_filter"),
        (_base.Parser, "find_filter"),
        (_library, "parse_bits"),
        # The name `parse_bits` actually resolves (`python/django_rust_templates/engine/library.py:423`), which is
        # *not* `base.token_kwargs`: the two point at one object until someone
        # rebinds this one, and then only this check sees it.
        (_library, "token_kwargs"),
        (_base, "smart_split"),
    ]
    generated_classes = [
        _class_binding(_library, "SimpleNode", ("__init__", "__new__")),
        _class_binding(_library, "InclusionNode", ("__init__", "__new__")),
        _class_binding(_library, "TagHelperNode", ("__init__",)),
        _class_binding(_base, "FilterExpression", ("__init__", "args_check")),
        _class_binding(_base, "Variable", ("__init__", "resolve")),
        # **This module's own `Token`, not `base.Token`.** `call_tag_stream_free`
        # instantiates the name bound *here* to build the dispatched token, and
        # the compile function then calls `split_contents()` on it. Guarding
        # `base.Token` instead is the alias mistake `token_kwargs` already
        # taught: the two share an object until exactly one of them is replaced,
        # and then only the executed one matters.
        _class_binding(sys.modules[__name__], "Token", ("__init__", "__new__", "split_contents")),
    ]
    load_functions = [
        (_base.Parser, "add_library"),
        (_defaulttags, "find_library"),
        (_defaulttags, "load_from_library"),
    ]
    load_classes = [
        _class_binding(_defaulttags, "LoadNode", ("__init__", "__new__")),
        # `defaulttags.Library`, because that is the name `load_from_library`
        # resolves when it constructs one. `library.Library` is a different name
        # for the same object and rebinding one does not rebind the other.
        _class_binding(_defaulttags, "Library", ("__init__", "__new__")),
        # Shared with the generated row: `call_tag_stream_free` builds the
        # dispatched token for every population, at `host.py`'s own `Token`.
        # `split_contents` is absent here because `load` reads
        # `token.contents.split()` instead (`defaulttags.py:1121`), so
        # requiring it would be a restriction this population does not earn.
        _class_binding(sys.modules[__name__], "Token", ("__init__", "__new__")),
    ]
    return {
        "generated": {
            "methods": ("compile_filter", "find_filter"),
            "functions": [(o, n, _captured(o, n)) for o, n in generated_functions],
            "classes": generated_classes,
        },
        "load": {
            "methods": ("add_library",),
            "functions": [(o, n, _captured(o, n)) for o, n in load_functions],
            "classes": load_classes,
        },
    }


_GUARDS = _guard_tables()
_LOAD = _defaulttags.load

# The two parser classes whose attribute resolution this package controls.
# Filled lazily: `render` imports this module, so naming it at import is a cycle.
_SUPPORTED_PARSERS = None


def _supported_parsers():
    global _SUPPORTED_PARSERS
    if _SUPPORTED_PARSERS is None:
        from django_rust_templates.engine.base import Parser
        from django_rust_templates.render import _DeferredTokenParser

        _SUPPORTED_PARSERS = (Parser, _DeferredTokenParser)
    return _SUPPORTED_PARSERS


def _population(func):
    """Which guard table `func` must satisfy, or `None` for "not eligible".

    The `FunctionType` test runs **before any attribute of `func` is read**: a
    callable object can expose `__code__` and `__globals__` while executing an
    entirely different `__call__`, and even `getattr(..., None)` on one can run
    its custom lookup.
    """
    if func is _LOAD:
        return "load"
    if type(func) is not types.FunctionType:
        return None
    if func.__code__ in _TRUSTED["codes"] and func.__globals__ is _TRUSTED["globals"]:
        return "generated"
    return None


def _environment_is_trusted(parser, population):
    guards = _GUARDS[population]

    # 1 — a parser whose attribute resolution we control. An *exact* type test,
    # so an unknown subclass or metaclass is rejected without its lookup being
    # invoked at all.
    base, deferred = _supported_parsers()
    if type(parser) is not base and type(parser) is not deferred:
        return False

    # 2 and 3 — no instance override, and no rebinding on *either* class. The
    # second clause is not belt-and-braces: `_DeferredTokenParser` is itself a
    # `Parser` subclass, so its own `__dict__` wins during resolution, and
    # assigning there passes conditions 1 and 2 and an identity check against
    # `Parser.__dict__` while the resolved method is the replacement. Measured.
    instance = vars(parser)
    for name in guards["methods"]:
        if name in instance or name in deferred.__dict__:
            return False

    for owner, name, original in guards["functions"]:
        if getattr(owner, name, None) is not original:
            return False

    for module, name, cls, methods in guards["classes"]:
        if getattr(module, name, None) is not cls:
            return False
        for method, original in methods.items():
            if getattr(cls, method, None) is not original:
                return False

    return True


class ShellHost:
    """The `has_tag`/`call_tag` pair for one parse.

    One instance per parse, holding the one `Parser` the whole parse uses, so
    that `{% load %}`'s `add_library` and `{% extra_data %}`'s
    `parser.extra_data[...]` are visible to every later token **and** to the
    template afterwards (`test_compile_tag_extra_data`).
    """

    def __init__(self, parser):
        self.parser = parser
        # Kept as an instance binding so `has_tag`'s membership test is an
        # instance-dict lookup, and so tests staging a name into
        # `host._rust_owned` keep working. See `_RUST_OWNED` for the derivation.
        self._rust_owned = _RUST_OWNED
        self._django_tags = django_own_tags()
        # The exception object `call_tag` last let past, for the shell to
        # re-raise by identity. `None` until one does. See `call_tag`.
        self.last_error = None
        # The callable `tag_is_stream_free` approved, for `call_tag_stream_free`
        # to invoke. **Dispatch state, not memoisation**: it lives for one
        # dispatch and says nothing about the next. The trait's boolean return
        # cannot carry the callable, and looking `parser.tags[command]` up a
        # second time at invocation would not be the same object — an earlier
        # callback in this parse can rebind the name between the two crossings.
        self._pending = None

    def tag_is_stream_free(self, command):
        """Whether `command`'s compile function provably never reads the stream.

        Asked only inside the host arm (`crates/template-core/src/parser/mod.rs:372`) and **before**
        `remaining_tokens()`, so an all-native template never reaches this and an
        eligible tag skips the Rust clone and the inbound `PyList` as well as both
        Python halves.

        Live, per dispatch: §5.1's shadowing case — a `{% load %}` rebinding a
        `simple_tag` name to a block compiler mid-parse — makes any answer cached
        against the *name* wrong. Nothing about the live environment is memoised;
        only §5.2's provenance, which is immutable, is captured once.
        """
        func = self.parser.tags.get(command)
        if func is None:
            return False
        population = _population(func)
        if population is None:
            return False
        if not _environment_is_trusted(self.parser, population):
            return False
        self._pending = func
        return True

    def has_tag(self, command):
        func = self.parser.tags.get(command)
        if func is None:
            return False
        if command not in self._rust_owned:
            # `load`, `querystring`, and everything a `{% load %}` brought in:
            # the registry does not contain them, so there is no Rust body to
            # prefer. See `__init__` for why the registry's keys are not the
            # same question as its implementations.
            return True
        # Rust's, unless something replaced `defaulttags`' callable for the
        # name — `add_library` is `self.tags.update(lib.tags)`
        # (`base.py:669-670`), so a `{% load %}` wins, and a caller's
        # `OPTIONS["builtins"]` wins at construction. See `django_own_tags`.
        return func is not self._django_tags.get(command)

    def library_tag_loaded(self, command):
        """Whether the template loaded a library that provides `command`.

        `has_tag`'s `False` conflates two situations whose correct outcomes are
        opposite: **no such tag** — nothing registered `command`, so Django raises
        ``Invalid block tag on line 1: 'static'. Did you forget to register or
        load this tag?`` and Rust must refuse it too — and **a library tag Rust
        owns, loaded**: ``{% load static %}`` then ``{% static %}`` leaves
        ``static.py``'s own callable registered, so `has_tag` compares it equal to
        `django_own_tags()` and answers `False`, yet Rust must compile this one.

        ``parser.tags`` tells them apart, and it is the *live* parser's:
        `add_library` merges a loaded library's tags into it (`base.py:668-670`),
        so the name is present in exactly the second case. Answering from
        `_library_tag_tables()` instead would say `True` for all 18 library names,
        none of which are Python's — this is the coarser question that only the
        Rust arm may narrow.
        """
        return command in self.parser.tags

    def call_tag(self, command, token_wire, tokens):
        """Run Django's own compile function for `command`.

        `token_wire` and `tokens` are four-field tuples in the order `_native.lex`
        uses; `tokens` arrives in parse order and is reversed onto the parser's
        stack the way `Parser.__init__` does (`base.py:504-505`), and what is left
        is reversed back. **Replacing the caller's cursor with the result, not
        counting a delta, is what makes `prepend_token` correct** — Django's
        `Parser.parse` prepends the end tag it stopped on (`base.py:564-569`) and
        the tag removes it with `delete_first_token`, and a count would have to
        know that dance rather than observe it.
        """
        parser = self.parser
        parser.tokens = [
            Token(TokenType(kind), contents, position, lineno)
            for kind, contents, lineno, position in reversed(tokens)
        ]
        kind, contents, lineno, position = token_wire
        token = Token(TokenType(kind), contents, position, lineno)
        # Django's `Parser.parse` pushes `(command, token)` *before* dispatching
        # (`base.py:573`) and pops after the compile returns (`base.py:587-588`).
        # Rust drives the outer loop and keeps its own stack
        # (`crates/template-core/src/parser/mod.rs:52,304,343`), so Django's is
        # empty here — and a host block tag whose body is unclosed reaches
        # `Parser.unclosed_block_tag`, which is `self.command_stack.pop()`
        # (`base.py:651`) and would raise `IndexError: pop from empty list` in
        # place of Django's "Unclosed tag on line 1: 'wrap'." message. One entry
        # is the whole of it: `unclosed_block_tag` pops only the innermost, which
        # is this tag. Popped on success only, as Django does: an exception out of
        # the compile leaves the entry and the parse dies with it.
        parser.command_stack.append((command, token))
        try:
            node = parser.tags[command](parser, token)
        except Exception as error:
            # Django's wrapper, at Django's line (`base.py:583-585`):
            # `Parser.parse` catches `Exception` and re-raises through
            # `Parser.error`, which sets `.token` on the first exception to reach it
            # and never replaces one already set (`base.py:619-630`). Rust drives
            # the outer loop, so the token has to be picked up here. A tag that
            # raises directly gets *its own* token attached (a no-op there, where
            # Django attaches the same one a frame up), and a failure inside the
            # tag's body arrives already carrying the **innermost** token set by
            # the nested `parser.parse` the tag ran. Measured on the pinned tree,
            # that difference is the whole point:
            #
            #     {% load custom %}{% div %}{% now a b %}{% enddiv %}
            #     → Django blames `now a b`; without the transport below, Rust's
            #       `Parser::error` sees no token and attaches `div`
            #
            # `error = parser.error(token, error)` is rebinding rather than
            # wrapping: for an `Exception` — which the clause above guarantees —
            # `Parser.error` returns the object it was handed, so the bare `raise`
            # re-raises the same one, traceback intact. `token_wire` is not
            # Django's attribute; it is this seam's transport, the same four-field
            # tuple `_native.lex` emits, because the other side may not name a
            # Django `Token`. Read by `python_host_error` in
            # `crates/template-py/src/lib.rs`.
            error = parser.error(token, error)
            error.token_wire = _wire_token(error.token)
            # Kept as the **object**, not its class name, so the shell can re-raise
            # this exact instance — as Django does (`base.py:583-585`), leaving the
            # `RuntimeError` with its `__traceback__` and `_culprit_node` attached.
            # Rust can only carry the class's *name* across the FFI
            # (`ErrorKind::Host`), and rebuilding from a name and a message would
            # lose both. See `render.django_exception`, which reads this. The parse
            # aborts on the first failure, so the last host call to raise is the one
            # it died on; overwritten rather than appended so nothing invites
            # reading an earlier entry.
            self.last_error = error
            raise
        parser.command_stack.pop()
        left = [_wire_token(tok) for tok in reversed(parser.tokens)]
        return node, left

    def call_tag_stream_free(self, command, token_wire):
        """Run a classified compile function without handing it the stream.

        The counterpart of `call_tag` for tags `tag_is_stream_free` approved, and
        deliberately the *same* code either side of the call: the command stack is
        pushed before and popped only on success, and a raising tag has its token
        attached and the object stashed for `render.django_exception` to restore
        by identity. Only the token vector is absent.

        The callable is the one classification approved, taken from `_pending`
        rather than looked up again — an earlier callback in this parse can
        rebind the name between the two crossings.
        """
        parser = self.parser
        func, self._pending = self._pending, None
        if func is None:
            raise RuntimeError(
                f"call_tag_stream_free({command!r}) with no approved callable: "
                f"tag_is_stream_free must be consulted first."
            )
        parser.tokens = _UnavailableStream(command)
        kind, contents, lineno, position = token_wire
        token = Token(TokenType(kind), contents, position, lineno)
        parser.command_stack.append((command, token))
        try:
            node = func(parser, token)
        except Exception as error:
            # `call_tag`'s arm, for `call_tag`'s reasons (`base.py:583-585`).
            error = parser.error(token, error)
            error.token_wire = _wire_token(error.token)
            self.last_error = error
            raise
        parser.command_stack.pop()
        return node

    def partial_node(self, name):
        """`partial_func`'s second arm (`defaulttags.py:1277-1281`).

        `DeferredSubDict(extra_data, "partials")` is a **live view**, not a copy —
        the subscript happens at render time — so this method must not resolve
        `name` and must not read `partials`. The failure it would cause is
        `{% partial p %}{% partialdef p %}…`: a template Django renders, on which a
        compile-time lookup raises
        `Partial 'p' is not defined in the current template.`

        **`PartialNode` is `defaulttags`'s, not `base`'s** (`defaulttags.py:423`),
        and it is bound at module level like every other Django class here: the
        binding and the suite's `isinstance` rows then name one object by
        construction rather than by timing.
        """
        return _defaulttags.PartialNode(
            name, partial_mapping=DeferredSubDict(self.parser.extra_data, "partials")
        )

    def register_partial_def(
        self, token_wire, name, inline, body_desc, source_start, source_end
    ):
        """`partialdef_func`'s tail (`defaulttags.py:1250-1265`), in its order.

        The order is Django's: the body is materialised, the duplicate check runs
        against `extra_data["partials"]` **after** the body exists but **before**
        anything is stored, the `PartialTemplate` is written, and the node is built
        **over the same nodelist object** — `defaulttags.py:1258` and `defaulttags.py:1265` are
        the same argument, and a shell that materialised twice would give the
        inline and deferred render paths different trees.

        `inline` reaches here as a flag rather than as the nodelist's own attribute
        because the descriptor is what crossed the FFI (`partialdef_func` passes
        `parser.parse(…)`'s `contains_nontext` in as the second positional
        argument, `defaulttags.py:1265`).

        The duplicate check is here and not in Rust because its message names
        `parser.origin.name`, which is Python state; its **class** survives the FFI
        through `python_host_error`'s `py_error_kind`.

        The error dance is `call_tag`'s, and required rather than defensive:
        `render.django_exception` re-raises `self.last_error` **by identity** when
        its class matches, so without it the `TemplateSyntaxError` a caller catches
        is a *rebuilt* one with no `.token` — and
        `Template.get_exception_info` reads exactly that attribute.

        `materialiser` is imported here rather than at module level because
        `render` imports `ShellHost` from this module, so a module-level import
        would be a cycle.
        """
        from django_rust_templates.render import materialiser

        parser = self.parser
        kind, contents, lineno, position = token_wire
        token = Token(TokenType(kind), contents, position, lineno)
        try:
            nodelist = materialiser().materialise(
                body_desc, parser.origin, None, None, parser
            )
            partials = parser.extra_data.setdefault("partials", {})
            if name in partials:
                raise TemplateSyntaxError(
                    f"Partial '{name}' is already defined in the "
                    f"'{parser.origin.name}' template."
                )
            partials[name] = PartialTemplate(
                nodelist,
                parser.origin,
                name,
                source_start=source_start,
                source_end=source_end,
            )
            return _defaulttags.PartialDefNode(name, inline, nodelist)
        except Exception as error:
            error = parser.error(token, error)
            error.token_wire = _wire_token(error.token)
            self.last_error = error
            raise
