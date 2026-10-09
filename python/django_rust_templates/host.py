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
from django.template.base import Token, TokenType

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
    **27** names, Rust's registry is **23**, and `rust - django` is `[]`. The
    five libraries' 18 names are the *other* direction of the same inequality —
    in this table and not in Rust's registry at all, until Tasks 3-6 port them.
    Containment is the only direction that has to hold, because the clause below
    compares against this table — and a Rust-owned name missing from it would put
    `None` on the right of an `is not`, so the escape resolves the wrong way and
    the host compiles a tag Rust owns, silently, with the suite green.

    The four names here that Rust does not own — `load`, `querystring`,
    `partial`, `partialdef` — never reach that comparison: `load` and
    `querystring` because Rust's registry has no `do_load`/`do_querystring`, and
    the two stubs because `_rust_owned` subtracts `builtin_tag_stub_names()`.
    All four are answered by `has_tag`'s early return, before this table is
    consulted at all.

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

# The registry object the map above was built from. Kept because ``None`` alone
# cannot tell "not built yet" from "built against a registry that is no longer
# the one in force", and the second is the failure this milestone exists to end.
_DJANGO_OWN_FILTERS_SOURCE = None


def django_own_filters():
    """The filter callables Rust ports, keyed by **handle** — the registry inverted.

    The tag predicate answers "is this callable the one Rust implements?" with a
    name in hand, because `has_tag(command)` is handed one. `FilterExpression`
    is not: `_impl/parser.py` stores the `find_filter` *handle* rather than a
    name, deliberately — the name is what a `{% load %}` rebinds — so the tuple
    `resolve` walks holds a callable and nothing to look it up by. The map is
    therefore `defaultfilters.register.filters` inverted, and the inversion is
    faithful because it is **injective**: measured against `.django` @ 6.0.9,
    the registry is 57 names over 57 *distinct* handles, so no name is lost and
    no two collide. (The measurement is a row, not a comment — a Django release
    that aliased two names to one function would otherwise pick a winner
    silently, and Rust would run the wrong body for one of them.)

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

    Read once, at first use, and cached: the source is a `Library`'s registry,
    which `{% load %}` does not touch (`add_library` merges into the *parser*),
    so a frozen copy and a live read differ only if something mutates
    `defaultfilters.register` itself — the case `django_own_tags`'s import-time
    freeze guards against for the same reason. Reading it per filter instead
    would rebuild the 57-entry dict on every filter of every rendered variable.

    **Cached against the registry *object*, not against ``None``.** A cache keyed
    on emptiness answers "already built" for a map built from a registry that no
    longer exists, and the consequence is silent and total: re-executing
    `defaultfilters` — a reload, or the scratch-universe instrumentation M11's
    measurement scripts build — replaces `register.filters` with 57 fresh
    handles, every one of which is absent from the stale map, so all 57 filters
    fall through to the Python loop and **every test stays green**. That is
    precisely the state M11 exists to end, arriving through the guard rather than
    around it, so the identity of the source is part of the cache key.

    Both halves are pinned as rows in
    `tests/test_interposition.py::FilterRoutingTests`: the map names a handle from
    a registry object it has not seen before, and the ordinary call does not
    rebuild.
    """
    global _DJANGO_OWN_FILTERS, _DJANGO_OWN_FILTERS_SOURCE
    source = _defaultfilters.register.filters
    if _DJANGO_OWN_FILTERS is None or _DJANGO_OWN_FILTERS_SOURCE is not source:
        _DJANGO_OWN_FILTERS = {func: name for name, func in source.items()}
        _DJANGO_OWN_FILTERS_SOURCE = source
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
        # it whole claims `partial` and `partialdef` for Rust and the identity
        # clause below then declines them on the host's behalf — measured, that
        # is 245 failures in the 1547, every one of them
        # `'partial' is a built-in tag this engine does not implement yet (M13)`
        # where `template_tests.syntax_tests.test_partials` expected either a
        # render or Django's own syntax error.
        #
        # The subtraction is not a new rule, it is the one the rest of the tree
        # already uses: `tests/test_parser_parity.py` derives "implemented" as
        # `set(builtin_tag_names_including_stubs()) -
        # set(builtin_tag_stub_names())`, and
        # `tests/test_tag_inventory.py` pins the stub set to those two names. The
        # host was the one place that read the keys as the registry.
        # M12 widens this with the `django.templatetags` names Rust has a real
        # body for — `cache`, `static` and the two prefix tags, plus `tz`'s three
        # tag names as of Task 4, `l10n`'s one (`localize`) as of Task 5, and
        # `i18n`'s eight as of Task 6a. That is sixteen of the eighteen.
        # **Those, not the 18 the five libraries
        # register**, and the difference is a
        # correctness boundary rather than bookkeeping: `library_tag_loaded`
        # below is `command in parser.tags`, which is true for *every* name a
        # loaded library contributes, and the Rust arm it feeds refuses a name
        # `LIBRARY_REGISTRY` has no body for. Claiming `blocktranslate` here
        # would route a loaded `{% blocktranslate %}` past `has_tag`'s "the
        # registered callable is
        # still Django's, so the host compiles it" clause and into Rust's
        # `Invalid block tag` — a template Django renders, refused by us. The
        # two names left unclaimed are exactly Task 6b's
        # (`blocktranslate`/`blocktrans`).
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
            # `load`, `querystring`, the two stub tags, and everything a
            # `{% load %}` brought in. `load` and `querystring` are answered by
            # the registry not containing them; `partial` and `partialdef` by
            # their bodies being stubs — see `__init__` for why the registry's
            # keys are not the same question as its implementations.
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
        18 library names, **2 of which are Python's as of M12 Task 6a** (the
        library table's 18 minus the sixteen Rust bodies `LIBRARY_REGISTRY` has
        — `blocktranslate` and `blocktrans`, which are Task 6b's) —
        see `__init__` for why `_rust_owned` carries the sixteen that have Rust
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
