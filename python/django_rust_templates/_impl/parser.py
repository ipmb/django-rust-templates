"""Materialise a Rust node descriptor into Django's real node classes.

Rust owns the parse; Python owns the tree that renders (design spec §5.5). This
module is the only place the two meet, and it is deliberately mechanical: it
looks up a class by name, calls it with keyword arguments, and attaches
``token``/``origin`` afterwards.

The path is ``_impl/parser.py`` as the milestone plan specifies. Note the
consequence: ``_impl`` is on the shim's search path for ``django.template``, so
this file also makes ``django.template.parser`` importable — a name Django does
not have. Nothing imports it (the shell reaches it as
``django_rust_templates._impl.parser``), and ``_shim.unshimmed()`` compares
source trees, so it neither shadows anything nor perturbs the progress meter.
Flagged rather than silently relocated, because if Django ever grows a
``template/parser.py`` this name would shadow it.
"""

from __future__ import annotations

from django.template.base import Node, NodeList, TextNode, Token, TokenType
from django.template.defaulttags import (
    AutoEscapeControlNode,
    CommentNode,
    CsrfTokenNode,
    DebugNode,
    NowNode,
    ResetCycleNode,
    SpacelessNode,
    TemplateTagNode,
    VerbatimNode,
)
from django.utils.safestring import mark_safe

from django_rust_templates import _native

__all__ = ("NODE_CLASSES", "materialise", "materialise_node")

# An explicit registry rather than `getattr(base, class_name)`: a
# `getattr`-based lookup would resolve anything that happens to be in Django's
# modules, turning a Rust typo into a wrong node class instead of a loud
# failure, and it would silently pick up a class M7 must *not* materialise from
# Rust (block/extends/include are Python-owned).
NODE_CLASSES = {
    "TextNode": TextNode,
    "AutoEscapeControlNode": AutoEscapeControlNode,
    "CommentNode": CommentNode,
    "CsrfTokenNode": CsrfTokenNode,
    "DebugNode": DebugNode,
    "NowNode": NowNode,
    "ResetCycleNode": ResetCycleNode,
    "SpacelessNode": SpacelessNode,
    "TemplateTagNode": TemplateTagNode,
    "VerbatimNode": VerbatimNode,
}


def materialise(nodelist_desc, origin=None, resolve_ref=None, parser=None):
    """Build a real :class:`~django.template.base.NodeList` from a descriptor."""
    nodes, contains_nontext = nodelist_desc

    nodelist = NodeList()
    # Django's `contains_nontext` is a *class* attribute, so leaving it alone is
    # how it reads False. Only the True transition is ever written, which is what
    # makes a second, text-only nodelist read False again.
    if contains_nontext:
        nodelist.contains_nontext = True

    for desc in nodes:
        nodelist.append(materialise_node(desc, origin, resolve_ref, parser))

    return nodelist


def materialise_node(desc, origin=None, resolve_ref=None, parser=None):
    """Build one real node, children first.

    The ordering is load-bearing twice over (Review Focus item 10). A node's
    ``__init__`` runs **before** ``token``/``origin`` are attached, and some
    ``__init__``s read their own subtree — ``ExtendsNode.__init__`` builds
    ``self.blocks`` by walking the nodelist it is given. So children are
    materialised completely, then the parent is constructed, then the attributes
    are set.

    The same order is what makes an ``AttrValue::NodeListRef`` resolvable: the
    attributes are decoded *after* the child nodelists exist, so the position the
    reference carries is populated by the time it is read. That is proven by
    ``test_a_nodelist_ref_resolves_because_children_are_built_first`` rather than
    left as a comment, because reordering these three blocks is the natural thing
    to do when optimising and the failure would be an ``IndexError`` at best.

    A child nodelist whose **name is empty** is positional only: it is reachable
    through a ``NodeListRef`` but is not passed to ``cls`` as a keyword.
    ``IfNode`` is why — its ``__init__`` takes exactly one parameter,
    ``conditions_nodelists``, and holds the branch nodelists inside that list,
    so there is no parameter name to give them.
    """
    class_name, attrs, nodelists, token = desc

    try:
        cls = NODE_CLASSES[class_name]
    except KeyError:
        raise LookupError(
            f"no materialiser for node class {class_name!r}; add it to NODE_CLASSES"
        ) from None

    children = [
        (name, materialise(child, origin, resolve_ref, parser))
        for name, child in nodelists
    ]
    child_lists = [child for _name, child in children]

    kwargs = {name: child for name, child in children if name}
    for name, value in attrs:
        kwargs[name] = _decode_value(value, resolve_ref, parser, child_lists)

    node = cls(**kwargs)

    node.token = _decode_token(token) if token is not None else None
    node.origin = origin
    return node


def _decode_token(wire):
    """A real :class:`Token` from the four-field wire form.

    Same order as ``_native.lex``'s tuples: Django's signature is
    ``Token(token_type, contents, position=None, lineno=None)``.
    """
    token_type, contents, lineno, position = wire
    return Token(TokenType(token_type), contents, position, lineno)


def _decode_value(value, resolve_ref=None, parser=None, child_lists=None):
    """A descriptor attribute as a Python object.

    ``bool`` is tested first because ``bool`` subclasses ``int``: an
    ``isinstance(value, int)`` arm ahead of it would turn every boolean
    attribute — ``{% autoescape on %}``'s ``setting`` — into ``0``/``1``, which
    compares equal and passes any ``assertEqual`` while being the wrong type.

    ``float`` rides alongside ``int`` and ``str`` because it is already the
    Python object the descriptor meant: ``Variable.literal`` for the
    ``float(var)`` branch is a Python ``float`` on Django's side too, so there is
    nothing to convert. Without the arm the value falls through to the
    ``LookupError`` below, which is how a missing arm announces itself.

    The three arguments are all defaults because this is also the entry point
    ``tests/test_variable_parity.py`` drives directly with a bare literal, where
    there is no enclosing node to resolve a reference against. A reference
    reaching it without them is loud rather than silently ``None``.
    """
    if isinstance(value, bool):
        return value
    if value is None or isinstance(value, (int, float, str)):
        return value
    if isinstance(value, _native.NodeRef):
        if resolve_ref is None:
            raise LookupError(
                f"{value!r} needs a resolve_ref callback; none was passed to materialise"
            )
        return resolve_ref(value.index)
    if isinstance(value, _native.NodeListRef):
        # `AttrValue::NodeListRef`: a position in the *enclosing* node's own
        # nodelists, already materialised by `materialise_node`. Its own type
        # rather than an `int` because an integer attribute is also an `int` on
        # the wire, and the failure mode would be a node holding a wrong nodelist.
        if child_lists is None:
            raise LookupError(
                f"{value!r} needs the enclosing node's nodelists; "
                "none were passed to _decode_value"
            )
        try:
            return child_lists[value.index]
        except IndexError:
            raise LookupError(
                f"{value!r} is out of range: the enclosing node has "
                f"{len(child_lists)} nodelist(s)"
            ) from None
    if isinstance(value, _native.SafeStr):
        return mark_safe(value.text)
    if isinstance(value, list):
        # `AttrValue::List`. Recursed rather than returned as-is: the Rust side
        # converts eagerly, so the elements are already Python objects of the
        # wire types above — a `SafeStr` inside an `IfNode` condition list still
        # needs `mark_safe`, and a nested `NodeListRef` still needs its nodelist.
        return [
            _decode_value(item, resolve_ref, parser, child_lists) for item in value
        ]
    if isinstance(value, dict):
        # `AttrValue::Map`, already a real `dict` with `token_kwargs`' own
        # repeated-key behaviour (first position, last value) applied on the Rust
        # side. Only the *values* need decoding.
        return {
            key: _decode_value(item, resolve_ref, parser, child_lists)
            for key, item in value.items()
        }
    raise LookupError(
        f"cannot decode descriptor value {value!r} of type {type(value).__name__}"
    )
