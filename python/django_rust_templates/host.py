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
*not* the callable `django.template.defaulttags` registered for it, or when it is a
built-in Rust does not own (`block`, `extends`, `include`, `load`, `querystring`).

Measured on the pinned tree before the identity clause was written down, since it
is the one fact the whole predicate rests on: with
``p = Parser(tokens, builtins=Engine().template_builtins)``,
``p.tags["comment"] is defaulttags.register.tags["comment"]`` is ``True``; after
``p.add_library(lib)`` for a library registering ``comment`` it is ``False``.
"""

from __future__ import annotations

from django.template import defaulttags as _defaulttags
from django.template.base import Token, TokenType

from django_rust_templates import _native

__all__ = ("ShellHost",)

# A5: M7's three. They are Django's builtins and not Rust's, so without this they
# would reach the host. The ordering check itself is *not* the reason — Rust
# ports `extend_nodelist` itself (`crates/template-core/src/parser/mod.rs:483`) and
# runs it for a host descriptor like any other block node. The reason is that
# `NodeDesc.must_be_first` does not cross the seam, so `{% extends %}` would be
# accepted *and* uncheckable — and accepting a tag whose render path is M7's is
# worse than today's honest `Invalid block tag`. Measured:
# `ExtendsNode.must_be_first is True` (`BlockNode`/`IncludeNode` are `False`) and
# `{{ x }}{% extends "y" %}` raises under stock Django. M7 removes this, carries
# `must_be_first` with the node — one `bool` — and gets `parser.__loaded_blocks`
# for free, since the handle *is* Django's `Parser`.
_M7_OWNED = frozenset({"block", "extends", "include"})


def django_own_tags():
    """The tag callables Rust ports — read from the module it ports, once.

    The predicate's question is *"is this callable the one Rust implements?"*,
    and the only configuration-independent answer names the module Rust ports:
    `django.template.defaulttags`. Frozen at import, so neither a `{% load %}`
    nor a caller mutating `defaulttags.register` mid-parse can move it.

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

    `loader_tags` is not here because its three names are `_M7_OWNED` and are
    refused before the identity clause; `load`/`querystring` are not in
    `_rust_owned` and return True before it. Measured: the 22 Rust-owned names
    are exactly `defaulttags.register.tags` minus `load` and `querystring`.
    """
    return dict(_defaulttags.register.tags)


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
        self._rust_owned = set(_native.builtin_tag_names())
        self._django_tags = django_own_tags()

    def has_tag(self, command):
        func = self.parser.tags.get(command)
        if func is None:
            return False
        if command in _M7_OWNED:
            # A5. Excluded until M7, which must also carry `must_be_first`
            # across the seam — `NodeDesc.must_be_first` defaults false and is
            # not on the wire, so reaching the host would *accept*
            # `{% extends %}` in a position Django rejects.
            return False
        if command not in self._rust_owned:
            # `load`, `querystring`, and everything a `{% load %}` brought in.
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
            raise
        parser.command_stack.pop()
        left = [_wire_token(tok) for tok in reversed(parser.tokens)]
        return node, left
