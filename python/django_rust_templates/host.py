"""The shell's half of the host seam: the parser Django's own tag code needs.

Rust owns the token cursor; **the host owns parser state** — `tags`, `filters`,
`libraries`, `extra_data`, and whatever a user tag sets on the parser. The
division is what §5.2's `ParserHandle` describes, realised the smaller of the two
ways it can be: rather than a Rust handle proxying into a shared `Parser`, one
real `django.template.base.Parser` is kept for the whole parse, and Rust hands it
the tokens it may consume. `Parser.parse` already leaves the end tag on the stack
for `delete_first_token` (`base.py:564-569`), so a callback that does
``nodelist = parser.parse((end_name,)); parser.delete_first_token()`` — which is
`Library.simple_block_tag` (`library.py:211-212`) and `tag_27584.badtag`
(`tag_27584.py:8-9`) verbatim — runs unmodified.

**A `has_tag` that answered `command in parser.tags` would break every built-in.**
Every one of Django's built-ins is in that dict, so Rust would be told to compile
nothing and the host would run Django's `do_for` etc. The identity clause below is
the whole of the discrimination: a name is the host's to compile when its entry is
*not* the callable Django itself registered for it. Until M7 an exception list of
three names sat in front of that clause; M7 deleted it, and the deletion is what
makes the clause whole rather than the last of three rules — a name in Rust's
registry reaches it and is declined, and the two built-ins Rust still does not own
(`load`, `querystring`) are answered by the registry's absence one clause earlier,
neither by a list.

Measured on the pinned tree before the identity clause was written down, since it
is the one fact the whole predicate rests on: with
``p = Parser(tokens, builtins=Engine().template_builtins)``,
``p.tags["comment"] is defaulttags.register.tags["comment"]`` is ``True``; after
``p.add_library(lib)`` for a library registering ``comment`` it is ``False``.
"""

from __future__ import annotations

from django.template import defaultfilters as _defaultfilters
from django.template import defaulttags as _defaulttags
from django.template import loader_tags as _loader_tags
from django.template.base import PartialTemplate, Token, TokenType
from django.template.exceptions import TemplateSyntaxError
from django.utils.datastructures import DeferredSubDict

from django_rust_templates import _native

__all__ = ("ShellHost",)


# The five `django.templatetags` libraries M12 interposes, as module paths.
_LIBRARY_TAG_MODULES = (
    "django.templatetags.cache",
    "django.templatetags.i18n",
    "django.templatetags.l10n",
    "django.templatetags.static",
    "django.templatetags.tz",
)


def _library_tag_tables():
    """The five libraries' `register.tags`, imported **lazily**.

    Lazy rather than at module level, because importing a library *executes* it,
    and these files capture names out of the modules M9 interposed. Measured in
    the pinned tree: ``django/templatetags/i18n.py:5`` is ``from
    django.template.base import TokenType, render_value_in_context``, and its
    line 4 takes ``Node`` from ``django.template`` — the two shapes
    ``_shim._stale_captures`` exists to refuse, a captured class making every
    ``isinstance`` false and a captured enum making every comparison false.

    **Deferred, the capture is of the served modules; deferred to import, it is
    of Django's.** ``_shim._CAPTURE_NAMESPACES`` already lists
    ``django.templatetags`` for exactly this reason, and ``install()`` raises
    when it finds a copy from the other universe — so executing one of these at
    ``host`` import time, whenever that import lands before ``install()``, is a
    loud failure rather than a silent one. Deferring the import to the first
    ``ShellHost`` — constructed by the parser, so after ``install()`` on every
    path the package supports — keeps the two universes apart instead of relying
    on the guard.
    """
    import importlib

    return tuple(
        importlib.import_module(name).register.tags for name in _LIBRARY_TAG_MODULES
    )


def _library_filter_tables():
    """The same five libraries' `register.filters`, imported the same lazy way.

    The filter half of [`_library_tag_tables`], with the same deferral and for the
    same measured reason: the modules capture `TokenType` and `render_value_in_context`
    out of `django.template.base`, so executing one before `install()` would leave a
    stale-universe copy — loud under `_shim._stale_captures`, but the deferral keeps
    the two universes apart instead of relying on the guard. `cache` and `static`
    register no filters (measured, `register.filters` is empty in both), so this
    tuple contributes nine callables; they are listed anyway because the *module set*
    is the five libraries M12 interposes and a sixth library's filters should not
    have to remember to be added in two places.
    """
    import importlib

    return tuple(
        importlib.import_module(name).register.filters for name in _LIBRARY_TAG_MODULES
    )


_DJANGO_OWN_TAGS = None

# The registry objects the merge above was built from, in merge order. Kept for
# `django_own_filters`' reason one table over: `None` alone cannot tell "not
# built yet" from "built from a registry that is no longer in force", and the
# second is the case where the escape silently resolves the wrong way.
_DJANGO_OWN_TAGS_SOURCE = None


def django_own_tags():
    """The tag callables Rust ports — read from the modules it ports, once.

    The predicate's question is *"is this callable the one Rust implements?"*,
    and the only configuration-independent answer names the modules Rust ports:
    `django.template.defaulttags`, `django.template.loader_tags`, and — from M12,
    whether or not they are interposed yet — the five `django.templatetags`
    libraries. Cached at first use against the registry objects it was built
    from, so neither a `{% load %}` nor a caller mutating a `register` mid-parse
    can move it, and a *replaced* registry is rebuilt rather than served stale.

    **The five libraries are a precondition, not a tidy-up, and the reason is
    measured.** `has_tag` answers a Rust-owned name with
    `func is not self._django_tags.get(command)`, so for a library tag this table
    is the *other side of the comparison*: with the library's callable absent,
    `get` is `None`, `func is not None` is True, and the escape sends the tag to
    the host. Before Tasks 3-6 that is what we want — but it is also what
    *porting a name first* would silently keep, because widening Rust's registry
    does not touch this clause at all. Rust's new `do_trans` would never run and
    every row would stay green, which is the trap ``.review/m12-recon.md`` §3
    measured. So the order is widen-then-port: this table covers a library
    *before* Rust's registry claims any of its names.

    **Libraries merge *first*, defaulttags and loader_tags last, and that order
    is the safety property rather than a preference.** `update` gives the last
    writer the name, and what this table must never lose is Django's *own*
    callable for the 27 built-ins. Were a library to register `if`, a
    libraries-last merge would make this table's `if` the library's — so a
    `{% load %}`ed rebind of `if` would compare *equal* to it, `has_tag` would
    answer "Rust's", and Rust's `do_if` would compile over the caller's tag.
    Merging the libraries first makes that hypothetical collision resolve to
    Django's callable and leaves the clause correct for every Rust-owned name.
    The collision does not exist at 6.0.9 — measured, the five registries and
    the two share no name — so both halves are held by
    `LibraryTagPredicateTests`: the measurement, and the order that keeps the
    hazard from being a silent one if a future pin introduces it.

    **This replaced a snapshot of `parser.tags`, and the reason is measured.**
    The snapshot was taken at `ShellHost` construction, which is *after*
    `Parser.__init__` merged the engine's `builtins` — so it captured whatever
    the caller configured, not Django's own table. A caller whose
    `OPTIONS["builtins"]` registers a library providing `for` then got the
    opposite of the intent: `parser.tags["for"] is snapshot["for"]` was True, so
    `has_tag` said "Rust's" and Rust's `do_for` ran while the caller's tag was
    silently ignored. Measured against `.django` @ 6.0.9, and the inversion was
    the *stated reason* for the snapshot: the docstring justified it by a
    misattribution the snapshot itself causes and the defaulttags comparison
    does not — `mine is not defaulttags.do_for` routes to the host, correctly.

    Not reachable through the 1547 today (the only `builtins` use in
    `template_tests` is a *filter* library in `test_parser.py`), but
    `OPTIONS["builtins"]` is public, documented Django and M9 ships it.

    **M7 widened this to two modules, and it is the second one that matters.**
    A predicate that read only `defaulttags` answered "not mine" for
    `block`/`extends`/`include`, because those three are `loader_tags`' — so
    until M7 an exception list in front of the clause refused them by name. The
    measured facts that retire the list: `loader_tags.register.tags`'s keys are
    exactly `['block', 'extends', 'include']`, disjoint from `defaulttags`';
    and the three arrive in `parser.tags` at all only because
    `Engine.default_builtins` (`engine.py:14-18`) lists
    `django.template.loader_tags` third and `add_library` (`base.py:668-670`)
    merges it in. So for all three the clause reads `None` against a `func` that
    is not `None` — `func is not None` is True — and declines them as Rust's.

    The list was not merely redundant, it was **wrong in the direction that does
    not fail loudly**: it was consulted *before* identity, so a `{% load %}`ed
    library registering `block` lost (measured: the parser's `block` *is* the
    loaded callable, and `has_tag` returned `False`).

    **Rust's registry is a subset of this table, never equal to it, and this
    paragraph said "exactly the union" until M12 measured it.** Measured against
    `.django` @ 6.0.9: the union of `defaulttags`' and `loader_tags`' tables is
    **27** names, Rust's registry is **25** — it was 23 when M12 measured it, and
    M13's two tasks moved `partial` and `partialdef` into it — and `rust - django`
    is `[]`. The five libraries' 18 names are the *other* direction of the same
    inequality — in this table and not in Rust's registry at all, until Tasks 3-6
    port them. Containment is the only direction that has to hold, because the
    clause below compares against this table — and a Rust-owned name missing from
    it would put `None` on the right of an `is not`, so the escape resolves the
    wrong way and the host compiles a tag Rust owns, silently, with the suite
    green.

    The two names here that Rust does not own — `load` and `querystring` — never
    reach that comparison: Rust's registry has no `do_load`/`do_querystring`, so
    there is no Rust body to prefer and `has_tag`'s early return answers before
    this table is consulted at all. **Until M13's Task 4 the sentence here named
    four**, adding `partial` and `partialdef`, which were then keys with stub
    bodies and were subtracted by `_rust_owned` rather than by the registry. Both
    are Rust's now, so the exception list the subtraction built has shrunk to the
    pair whose absence is structural.

    The old sentence was wrong in both halves and had been for five milestones,
    and it was wrong in the direction that **hides** this: read as an equality,
    widening this table looks self-maintaining, so adding a library's tag names
    to Rust's registry appears to require widening it, when nothing forces that
    — the tag then stays Django's with every row green.
    `TagPredicateCoverageTests` in `tests/test_interposition.py` holds the
    containment now, and was added with this correction.
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

# The registry objects the map above was built from, in merge order. Kept because
# ``None`` alone cannot tell "not built yet" from "built against a registry that is
# no longer the one in force", and the second is the failure this milestone exists
# to end.
_DJANGO_OWN_FILTERS_SOURCE = None


def django_own_filters():
    """The filter callables Rust ports, keyed by **handle** — the registry inverted.

    The tag predicate answers "is this callable the one Rust implements?" with a
    name in hand, because `has_tag(command)` is handed one. `FilterExpression`
    is not: `_impl/parser.py` stores the `find_filter` *handle* rather than a
    name, deliberately — the name is what a `{% load %}` rebinds — so the tuple
    `resolve` walks holds a callable and nothing to look it up by. The map is
    therefore the registries inverted, and the inversion is faithful because it
    is **injective**: measured against `.django` @ 6.0.9, the merged table is **66**
    names over **66** *distinct* handles — the 57 built-ins plus the five
    libraries' 9 — so no name is lost and no two collide. (The measurement is a
    row, not a comment — a Django release that aliased two names to one function
    would otherwise pick a winner silently, and Rust would run the wrong body for
    one of them.)

    **Six registries merge, libraries first and `defaultfilters` last, and that
    order is the safety property rather than a preference.** Same rule and same
    reason as `django_own_tags`: `update` gives the last writer the name, and what
    this table must never lose is `defaultfilters`' *own* callable for the 57
    built-ins — were a library to register `lower`, a libraries-last merge would
    make this table's `lower` the library's, so a `{% load %}`ed rebind of `lower`
    would compare *equal* to it and Rust's body would run over the caller's
    library function. Merging the libraries first resolves that hypothetical
    collision to `defaultfilters`' callable and leaves the clause correct. The
    collision does not exist at 6.0.9 — measured, the six registries share no name
    — so both halves are held by `tests/test_interposition.py`: the measurement,
    and the order that keeps the hazard from being a silent one if a future pin
    introduces it.

    **Widened by M12 Task 7, and the widening is the whole of the routing.** Before
    it, a `language_name` handle was absent from this map, so `resolve` fell through
    to the Python loop and called the *transcribed* body — correct behaviour, and
    indistinguishable from routing, which is why the nine Rust bodies landed with
    the map widened in the same commit rather than the one after.

    **Handle identity, not name identity, is the same decision as the tags',
    restated for the table that has no names.** Two cases the name-keyed form
    could not express, both of them correct here:

    * A `{% load %}`ed library rebinding `lower` — `add_library` is
      `self.filters.update(lib.filters)` (`base.py:670`) — puts a *different*
      object in `parser.filters`, which is absent from this map, so the
      library's callable runs. That is Review Focus 1, and it is why the arm
      cannot be decided by name.
    * A library registering a name *for a built-in's own object*
      (`lib.add_filter("shout", lower)`) is *present*, so Rust runs. That is also
      correct: the library asked for the function Rust ports, and Rust's `lower`
      is a faithful implementation of it. A name-keyed arm would have had to
      guess which of the two names to pass Rust.

    Read once, at first use, and cached: the sources are `Library` registries,
    which `{% load %}` does not touch (`add_library` merges into the *parser*),
    so a frozen copy and a live read differ only if something mutates one of the
    six `register`s itself — the case `django_own_tags`'s import-time freeze
    guards against for the same reason. Reading it per filter instead would
    rebuild the 66-entry dict on every filter of every rendered variable.

    **Cached against the registry *objects*, not against ``None``.** A cache keyed
    on emptiness answers "already built" for a map built from registries that no
    longer exist, and the consequence is silent and total: re-executing
    `defaultfilters` (or one of the five libraries) — a reload, or the
    scratch-universe instrumentation M11's measurement scripts build — replaces
    that `register.filters` with fresh handles, every one of which is absent from
    the stale map, so every filter from that table falls through to the Python loop
    and **every test stays green**. That is precisely the state M11 exists to end,
    arriving through the guard rather than around it, so the identity of *all six*
    sources is part of the cache key — the same length-and-identity check
    [`django_own_tags`] uses.

    Both halves are pinned as rows in
    `tests/test_interposition.py::FilterRoutingTests`: the map names a handle from
    a registry object it has not seen before, and the ordinary call does not
    rebuild.
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


def effective_autoescape(context, func):
    """The ``bool`` ``_native.filter`` must be handed for ``func``, upstream's laziness kept.

    The same rule as ``effective_use_tz`` and for the same reason, on upstream's
    *other* guarded branch: ``context.autoescape`` is read only inside
    ``if getattr(func, "needs_autoescape", False):`` (``base.py:812-815``), so
    every filter whose flag is unset must be handed a value without ``context``
    being touched at all.

    **Measured, and it is the acceptance suite that found it.** `resolve` is
    called with a plain ``dict`` by Django's own tests —
    ``.django/tests/template_tests/test_parser.py:207`` is
    ``FilterExpression(f"0|default:{num}", p).resolve({})`` — which is legal
    precisely because upstream's loop never reaches into ``context`` for a filter
    that sets neither flag. Reading ``context.autoescape`` for all 57 turned that
    one row into ``AttributeError: 'dict' object has no attribute 'autoescape'``
    while the other 1546 stayed green, so nothing smaller than the real suite
    would have shown it.

    The value for the fifty filters that do not read it is ``False``, which is
    not a guess: ``FilterCall::autoescape`` is documented in
    ``crates/template-core/src/filters/host.rs:44-46`` as "the ``autoescape`` in
    force, which the seven ``needs_autoescape`` filters" read, and the seven are
    the only Rust bodies that mention it (``join``, ``linebreaks``,
    ``linebreaksbr``, ``linenumbers``, ``unordered_list``, ``urlize``,
    ``urlizetrunc``).

    The ``getattr`` default is upstream's own, so a library filter registered
    without the flag behaves here as it does there.
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
    ``_native.filter``'s ``use_tz`` is a ``bool``, so the fallback has to happen
    before the FFI. Collapsing ``None`` to ``False`` instead is **not** the same
    value: with ``settings.USE_TZ`` true and the context's ``use_tz`` unset,
    upstream localizes and ``False`` would not.

    **The attribute is read only for a filter that asks for it, and the guard
    comes first for that reason.** Upstream reaches ``template_localtime`` — and
    so ``context.use_tz`` — only under
    ``if getattr(func, "expects_localtime", False):`` (``base.py:810-811``).
    Writing the test the other way round (``context.use_tz is None and
    getattr(...)``) reads ``context.use_tz`` for all 57 filters, which raises on
    the ``dict`` context Django's own suite passes; measured, that and the
    ``autoescape`` read next to it are what failed
    ``test_filter_numeric_argument_parsing``.

    **The read order between the two branches is upstream's and is observable.**
    ``base.py`` tests ``expects_localtime`` before ``needs_autoescape``, so for a
    ``dict`` context ``{{ x|date }}`` raises ``AttributeError: ... 'use_tz'``
    while ``{{ x|join }}`` raises ``... 'autoescape'`` — measured on the pinned
    tree. The arm therefore computes this one *first* and hands the results to
    the FFI in the FFI's own argument order, rather than writing the two calls
    as arguments.

    For every other filter the value is ``False`` and Rust never reads it:
    ``FilterCall::use_tz`` is documented in ``filters/host.rs`` as being "for the
    two ``expects_localtime`` filters", and no other body branches on it.
    """
    if not getattr(func, "expects_localtime", False):
        return False
    if context.use_tz is None:
        from django.conf import settings

        return settings.USE_TZ
    return bool(context.use_tz)


def _wire_token(token):
    """A Django `Token` as the four-field tuple Rust's `_native.lex` emits.

    Same order as `_impl/parser.py::_decode_token`: Django's signature is
    `Token(token_type, contents, position=None, lineno=None)`, so the *wire* is
    `(token_type, contents, lineno, position)` and the constructor call below
    reorders it.
    """
    return (
        token.token_type.value,
        token.contents,
        token.lineno,
        token.position,
    )


class ShellHost:
    """The `has_tag`/`call_tag` pair for one parse.

    One instance per parse, holding the one `Parser` the whole parse uses, so
    that `{% load %}`'s `add_library` and `{% extra_data %}`'s
    `parser.extra_data[...]` are visible to every later token **and** to the
    template afterwards (`test_compile_tag_extra_data`).
    """

    def __init__(self, parser):
        self.parser = parser
        # The names Rust has a real compile function for, which as of M12 is the
        # registry in *two* tables: `builtin_tag_names_including_stubs()` minus
        # `builtin_tag_stub_names()` — the built-ins — unioned with
        # `library_tag_names()`, the `django.templatetags` tags Rust compiles. The first alone is the built-in registry's *keys*,
        # and a stub is a key with a body that raises `Unimplemented`, so taking
        # it whole claimed `partial` and `partialdef` for Rust and the identity
        # clause below then declined them on the host's behalf — measured, that
        # is 245 failures in the 1547, every one of them
        # `'partial' is a built-in tag this engine does not implement yet (M13)`
        # where `template_tests.syntax_tests.test_partials` expected either a
        # render or Django's own syntax error.
        #
        # The subtraction is not a new rule, it is the one the rest of the tree
        # already uses: `tests/test_parser_parity.py` derives "implemented" as
        # `set(builtin_tag_names_including_stubs()) -
        # set(builtin_tag_stub_names())`, and `tests/test_tag_inventory.py`
        # pins the stub set. The host was the one place that read the keys as the
        # registry.
        #
        # **The subtraction is a no-op as of M13's Task 4**, because the stub set
        # is empty: every built-in in the table has a real body now. It stays
        # written this way rather than collapsing to the keys, because the
        # derivation is what makes the two agree by construction — a future
        # milestone that adds a half-built entry to the table gets the correct
        # answer from this line without anyone remembering to come back to it.
        # The subtrahend's derivation is pinned in Rust, one layer down:
        # `crates/template-core/src/parser/registry.rs`'s
        # `the_stub_derivation_is_not_a_copy_of_the_table` derives the stubs by
        # *asking each compile function* rather than reading a list, and hands
        # that derivation a fabricated table containing one stub so the row still
        # distinguishes it from a copy — with the live table's stub set empty, the
        # derivation and a hardcoded empty list give the same answer, which is the
        # one thing that row is for.
        # M12 widens this with the `django.templatetags` names Rust has a real
        # body for — `cache`, `static` and the two prefix tags, plus `tz`'s three
        # tag names as of Task 4, `l10n`'s one (`localize`) as of Task 5,
        # `i18n`'s eight as of Task 6a, and `blocktranslate`/`blocktrans` as of
        # Task 6b. That is the full eighteen: `library_tag_names()` is now every
        # name the five libraries register, so the library half of this union is
        # **0 of 18 Python's / eighteen Rust bodies**.
        #
        # **The union and the table are the same set now, and that was not so a
        # commit ago.** `library_tag_loaded` below answers `command in
        # parser.tags`, true for *every* name a loaded library contributes, and
        # the Rust arm it feeds compiles exactly when `LIBRARY_REGISTRY` has a
        # body for the name. Until Task 6b the two disagreed by
        # `blocktranslate`/`blocktrans`, and claiming them would have routed a
        # loaded `{% blocktranslate %}` past `has_tag`'s "the registered callable
        # is still Django's, so the host compiles it" clause into Rust's
        # `Invalid block tag` — a template Django renders, refused by us. Both
        # bodies are here now, so the coarse question and the body question give
        # the same answer for every library name.
        self._rust_owned = (
            set(_native.builtin_tag_names_including_stubs())
            - set(_native.builtin_tag_stub_names())
        ) | set(_native.library_tag_names())
        self._django_tags = django_own_tags()
        # The exception object `call_tag` last let past, for the shell to
        # re-raise by identity. `None` until one does. See `call_tag`.
        self.last_error = None

    def has_tag(self, command):
        func = self.parser.tags.get(command)
        if func is None:
            return False
        if command not in self._rust_owned:
            # `load`, `querystring`, and everything a `{% load %}` brought in.
            # The first two are answered by the registry not containing them —
            # there is no Rust body to prefer. **This arm carried two more names
            # until M13's Task 4**, `partial` and `partialdef`, which were keys
            # with stub bodies and were subtracted by `_rust_owned`; both are
            # Rust's now, so the only names left here are the ones whose absence
            # from the table is structural. See `__init__` for why the
            # registry's keys are not the same question as its implementations.
            return True
        # Rust's, unless something replaced `defaulttags`' callable for the
        # name — `add_library` is `self.tags.update(lib.tags)`
        # (`base.py:669-670`), so a `{% load %}` wins, and a caller's
        # `OPTIONS["builtins"]` wins at construction, which is the case the
        # parser-snapshot predicate got backwards. See `django_own_tags`.
        return func is not self._django_tags.get(command)

    def library_tag_loaded(self, command):
        """Whether the template loaded a library that provides `command`.

        The second half of a two-part answer, and it exists because `has_tag`'s
        `False` conflates two situations whose correct outcomes are opposite:

        * **No such tag.** Nothing registered `command`, so Django raises
          ``Invalid block tag on line 1: 'static'. Did you forget to register
          or load this tag?`` — and Rust must refuse it too, because a Rust body
          that ran here would render a template Django rejects.
        * **A library tag Rust owns, and the template loaded it.**
          ``{% load static %}`` then ``{% static %}``: the registered callable
          is still ``static.py``'s own, so `has_tag` compares it equal to
          `django_own_tags()` and answers `False` — the same `False` as above,
          but Rust must compile this one.

        ``parser.tags`` is what tells them apart, and it is the *live* parser's:
        `add_library` merges a loaded library's tags into it
        (`base.py:668-670`), so the name is present in exactly the second case.
        Answering from `_library_tag_tables()` instead would say `True` for all
        18 library names, **0 of which are Python's as of M12 Task 6b** (the
        library table's 18 minus the eighteen Rust bodies `LIBRARY_REGISTRY` has
        — the full set, `blocktranslate` and `blocktrans` included since 6b) —
        see `__init__` for why `_rust_owned` carries the eighteen that have Rust
        bodies and this method is the coarser question that only the Rust arm may
        narrow.
        """
        return command in self.parser.tags

    def call_tag(self, command, token_wire, tokens):
        """Run Django's own compile function for `command`.

        `token_wire` and `tokens` are four-field tuples in the order
        `_native.lex` uses; `tokens` arrives in parse order and is reversed onto
        the parser's stack the way `Parser.__init__` does (`base.py:504-505`),
        and what is left is reversed back. **Replacing the caller's cursor with
        the result, not counting a delta, is what makes `prepend_token`
        correct** — Django's `Parser.parse` prepends the end tag it stopped on
        (`base.py:564-569`) and the tag removes it with `delete_first_token`, and
        a count would have to know that dance rather than observe it.
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
        # (`base.py:651`) and raises `IndexError: pop from empty list` in place
        # of Django's message. **Measured** before this was written: `{% wrap %}`
        # with no `{% endwrap %}` gave `ParseError('pop from empty list')` where
        # stock Django gives "Unclosed tag on line 1: 'wrap'. Looking for one of:
        # endwrap." Five assertions in the 1547 depend on that text
        # (`test_custom.py:418,422`, `test_partials.py:317,373,379`), all
        # unreachable until M9 routes the suite through this seam.
        #
        # One entry is the whole of it: `unclosed_block_tag` pops only the
        # innermost, which is this tag — Rust's enclosing entries are not
        # mirrored and are not read. Popped on success only, as Django does: an
        # exception out of the compile leaves the entry and the parse dies with
        # it, and the `Parser` is per-parse.
        parser.command_stack.append((command, token))
        try:
            node = parser.tags[command](parser, token)
        except Exception as error:
            # Django's wrapper, at Django's line (`base.py:583-585`):
            # `Parser.parse` catches `Exception` and re-raises through
            # `Parser.error`, which sets `.token` on the first exception to reach
            # it and never replaces one already set (`base.py:619-630`).
            #
            # Rust drives the outer loop, so that wrapper is not on this call
            # path and the token has to be picked up here. Two producers, one
            # clause: a tag that raises directly gets *its own* token attached
            # (Django attaches the same one, one frame up, so this is a no-op
            # there), and a failure inside the tag's body arrives already
            # carrying the **innermost** token, set by the nested `parser.parse`
            # the tag itself ran. Measured on the pinned tree, the difference is
            # the whole point:
            #
            #     {% load custom %}{% div %}{% now a b %}{% enddiv %}
            #     → Django blames `now a b`; without the transport below, Rust's
            #       `Parser::error` sees no token and attaches `div`
            #
            # `error = parser.error(token, error)` is rebinding rather than
            # wrapping: for an `Exception` — which the clause above guarantees —
            # `Parser.error` returns the object it was handed, so the bare
            # `raise` re-raises the same one, traceback intact. `token_wire` is
            # not Django's attribute; it is this seam's transport, the same
            # four-field tuple `_native.lex` emits, because the other side may
            # not name a Django `Token`. Read by `python_host_error` in
            # `crates/template-py/src/lib.rs`.
            error = parser.error(token, error)
            error.token_wire = _wire_token(error.token)
            # Kept as the **object**, not as its class name, so the shell can
            # re-raise this exact instance — which is what Django does:
            # `Parser.parse` re-raises the object it caught
            # (`base.py:583-585`), so a tag whose body raises `RuntimeError`
            # leaves the parse as that `RuntimeError` with its `__traceback__`
            # and its `_culprit_node` attached. Rust can only carry the class's
            # *name* across the FFI (`ErrorKind::Host`), and rebuilding an
            # exception from a name and a message would lose both. See
            # `render.django_exception`, which reads this.
            #
            # The parse aborts on the first failure, so the last host call to
            # raise is the one the parse died on. Overwritten rather than
            # appended for that reason — a list would invite reading an earlier
            # entry.
            self.last_error = error
            raise
        parser.command_stack.pop()
        left = [_wire_token(tok) for tok in reversed(parser.tokens)]
        return node, left

    def partial_node(self, name):
        """`partial_func`'s second arm (`defaulttags.py:1277-1281`).

        `DeferredSubDict(extra_data, "partials")` is a **live view**, not a copy
        — the subscript happens at render time — so this method must not resolve
        `name` and must not read `partials` at all. The failure it would cause is
        `{% partial p %}{% partialdef p %}…`: that is a template Django renders,
        and a compile-time lookup raises
        `Partial 'p' is not defined in the current template.` on it.

        **`PartialNode` is `defaulttags`'s, not `base`'s.** The plan for this
        task imported it from `django.template.base` inside the method, with a
        reason about capturing the module at the wrong moment; the first half of
        that is simply false — measured, `from django.template.base import
        PartialNode` is `ImportError: cannot import name 'PartialNode'`, since
        the class is `defaulttags.py:423` — and the second half is aimed at a
        risk this module does not have: `_defaulttags` is already bound at module
        level (`python/django_rust_templates/host.py:35`) and is how `register.tags` is reached, so a *second*,
        deferred route to the same module would be a difference that reads like a
        reason. Bound at module level, as every other Django class here is, and
        for the reason the shim's own contract gives: the module is resolved once
        at interpreter start, after `sitecustomize` has installed it, so the
        binding and the suite's `isinstance` rows name one object.
        """
        return _defaulttags.PartialNode(
            name, partial_mapping=DeferredSubDict(self.parser.extra_data, "partials")
        )

    def register_partial_def(
        self, token_wire, name, inline, body_desc, source_start, source_end
    ):
        """`partialdef_func`'s tail (`defaulttags.py:1250-1265`), in its order.

        Four things happen here and the order is Django's: the body is
        materialised, the duplicate check runs against `extra_data["partials"]`
        **after** the body exists but **before** anything is stored, the
        `PartialTemplate` is written, and the node is built **over the same
        nodelist object** (`defaulttags.py:1258` and `defaulttags.py:1265` are the same
        argument — `PartialDefNode(nodelist)` and `PartialTemplate(nodelist)`
        hold one object, and a shell that materialised twice would give the
        inline and deferred render paths different trees).

        `inline` is Django's: `partialdef_func` passes `parser.parse(…)`'s
        `contains_nontext` in as the second positional argument
        (`defaulttags.py:1265`), so a body of nothing but text registers as an
        inline partial and one with a node in it does not. It reaches here as a
        flag rather than as the nodelist's own attribute because the descriptor
        is what crossed the FFI, and `materialise` is what turns it back into a
        `NodeList` that carries it.

        The duplicate check is here and not in Rust because its message names
        `parser.origin.name`, which is Python state. Its **class** is what the
        caller observes, and that survives the FFI through
        `python_host_error`'s `py_error_kind`.

        The error dance is `call_tag`'s, and it is required rather than
        defensive: `render.django_exception` re-raises `self.last_error` **by
        identity** when its class matches, so unless this method attaches the
        token and stores the object, the `TemplateSyntaxError` a caller catches
        is a *rebuilt* one with no `.token` — and `Template.get_exception_info`
        reads exactly that attribute.

        `materialiser` is imported here rather than at module level because
        `render` imports `ShellHost` from this module (`python/django_rust_templates/render.py:62`), so a
        module-level import would be a cycle: `import django_rust_templates.host`
        would reach `render`, which would find `ShellHost` not yet defined. The
        other names are module-level, as everywhere else in this file —
        `PartialTemplate` is `django.template.base`'s and `PartialDefNode` is
        `_defaulttags`'s, both already bound there.
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
