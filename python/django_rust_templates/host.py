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


def django_own_tags():
    """The tag callables Rust ports — read from the modules it ports, once.

    The predicate's question is *"is this callable the one Rust implements?"*,
    and the only configuration-independent answer names the modules Rust ports:
    `django.template.defaulttags` and `django.template.loader_tags`. Frozen at
    import, so neither a `{% load %}` nor a caller mutating either `register`
    mid-parse can move it.

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
    loaded callable, and `has_tag` returned `False`). `load`/`querystring` are
    the two names still not here; they are answered by Rust's registry not
    containing them, and by nothing else. Measured: the 25 Rust-owned names are
    exactly the union of these two tables.
    """
    return {**_defaulttags.register.tags, **_loader_tags.register.tags}


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
        # `builtin_tag_names_including_stubs()` minus
        # `builtin_tag_stub_names()`: the names Rust has a
        # real compile function for. The first alone is the registry's *keys*,
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
        self._rust_owned = set(_native.builtin_tag_names_including_stubs()) - set(
            _native.builtin_tag_stub_names()
        )
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
