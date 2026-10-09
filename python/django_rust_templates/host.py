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
(`:8-9`) verbatim — runs unmodified.

**A `has_tag` that answered `command in parser.tags` would break every built-in.**
Every one of Django's built-ins is in that dict, so Rust would be told to compile
nothing and the host would run Django's `do_for` etc. The identity clause below is
the whole of the discrimination: a name is the host's to compile when its entry is
*not* Django's own builtin callable, or when it is a built-in Rust does not own
(`block`, `extends`, `include`, `load`, `querystring`).

Measured on the pinned tree before the identity clause was written down, since it
is the one fact the whole predicate rests on: with
``p = Parser(tokens, builtins=Engine().template_builtins)``,
``p.tags["comment"] is <the engine's builtin>`` is ``True``; after
``p.add_library(lib)`` for a library registering ``comment`` it is ``False``.
"""

from __future__ import annotations

from django.template.base import Token, TokenType

from django_rust_templates import _native

__all__ = ("ShellHost",)

# A5: M7's three. They are Django's builtins and not Rust's, so without this they
# would reach the host. The ordering check itself is *not* the reason — Rust
# ports `extend_nodelist` (`crates/template-core/src/parser/mod.rs:392-418`) and
# runs it for a host descriptor like any other block node. The reason is that
# `NodeDesc.must_be_first` does not cross the seam, so `{% extends %}` would be
# accepted *and* uncheckable — and accepting a tag whose render path is M7's is
# worse than today's honest `Invalid block tag`. Measured:
# `ExtendsNode.must_be_first is True` (`BlockNode`/`IncludeNode` are `False`) and
# `{{ x }}{% extends "y" %}` raises under stock Django. M7 removes this, carries
# `must_be_first` with the node — one `bool` — and gets `parser.__loaded_blocks`
# for free, since the handle *is* Django's `Parser`.
_M7_OWNED = frozenset({"block", "extends", "include"})


def _engine_builtin_tags(parser):
    """The tag callables `Parser.__init__` merged from `builtins`.

    Taken as a **snapshot of the parser's table at construction**, not rebuilt
    from `Engine()`. Two reasons, and the second is a bug the first draft had:
    `parser_for` is handed the *caller's* engine, so a caller that registered
    its own library in `builtins` would have its tags mistaken for Django's; and
    a library is merged into `parser.tags` only by `{% load %}` (`base.py:521`
    stores `libraries` and does not merge it), so at construction the table
    **is** the builtins and nothing else.
    """
    return dict(parser.tags)


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
        self._builtins = _engine_builtin_tags(parser)

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
        # Rust's, unless a `{% load %}` replaced Django's own callable for the
        # name — `add_library` is `self.tags.update(lib.tags)`
        # (`base.py:669-670`), so the load wins and this is where we notice.
        return func is not self._builtins.get(command)

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
        node = parser.tags[command](
            parser, Token(TokenType(kind), contents, position, lineno)
        )
        left = [_wire_token(token) for token in reversed(parser.tokens)]
        return node, left
