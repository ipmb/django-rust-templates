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
