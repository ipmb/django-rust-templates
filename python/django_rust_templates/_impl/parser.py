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


def materialise(nodelist_desc, origin=None, resolve_ref=None):
    """Build a real :class:`~django.template.base.NodeList` from a descriptor."""
    nodes, contains_nontext = nodelist_desc

    nodelist = NodeList()
    # Django's `contains_nontext` is a *class* attribute, so leaving it alone is
    # how it reads False. Only the True transition is ever written, which is what
    # makes a second, text-only nodelist read False again.
    if contains_nontext:
        nodelist.contains_nontext = True

    for desc in nodes:
        nodelist.append(materialise_node(desc, origin, resolve_ref))

    return nodelist


def materialise_node(desc, origin=None, resolve_ref=None):
    """Build one real node, children first.

    The ordering is load-bearing (Review Focus item 10): a node's ``__init__``
    runs **before** ``token``/``origin`` are attached, and some ``__init__``s read
    their own subtree — ``ExtendsNode.__init__`` builds ``self.blocks`` by walking
    the nodelist it is given. So children are materialised completely, then the
    parent is constructed, then the attributes are set.
    """
    class_name, attrs, nodelists, token = desc

    try:
        cls = NODE_CLASSES[class_name]
    except KeyError:
        raise LookupError(
            f"no materialiser for node class {class_name!r}; add it to NODE_CLASSES"
        ) from None

    kwargs = {name: _decode_value(value, resolve_ref) for name, value in attrs}
    for name, child in nodelists:
        kwargs[name] = materialise(child, origin, resolve_ref)

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


def _decode_value(value, resolve_ref):
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
    if isinstance(value, _native.SafeStr):
        return mark_safe(value.text)
    raise LookupError(
        f"cannot decode descriptor value {value!r} of type {type(value).__name__}"
    )
