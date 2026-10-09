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

from django.template.base import (
    FilterExpression,
    Node,
    NodeList,
    TextNode,
    Token,
    TokenType,
    Variable,
    VariableNode,
)
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

__all__ = (
    "NODE_CLASSES",
    "check_filter",
    "materialise",
    "materialise_node",
)

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
    "VariableNode": VariableNode,
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


def _materialise_variable(desc, parser=None):
    """A real :class:`Variable` from a descriptor, without re-parsing.

    ``__new__`` plus slot assignment rather than ``Variable(desc.var)``: calling
    the constructor would parse the token a second time in Python and discard
    Rust's parse — including the error messages and the token each carries,
    which are Rust's to produce (§6). ``Variable.__slots__`` is
    ``("var", "literal", "lookups", "translate", "message_context")`` and
    ``__init__`` assigns four of them; ``message_context`` is set to ``None``
    there and never assigned during a parse, so setting it here is what Django
    itself does and not a stub.

    ``isinstance(result, Variable)`` is what ``FilterExpression.is_var`` reads,
    so this is a real ``Variable`` and not a lookalike.
    """
    variable = Variable.__new__(Variable)
    variable.var = desc.var
    variable.literal = _decode_value(desc.literal, None, parser)
    # A tuple on Django's side (`tuple(var.split("."))`), a list on the wire.
    variable.lookups = tuple(desc.lookups) if desc.lookups is not None else None
    variable.translate = desc.translate
    variable.message_context = None
    return variable


def _materialise_operand(lookup, value, parser=None):
    """One of a ``FilterExpression``'s operands, as Django's own 2-tuple.

    ``(lookup, value)`` is the shape Django holds — ``args`` is a list of pairs
    and ``resolve`` unpacks it (`base.py:804-810`), and ``args_check`` counts it
    (`:822-826`) — so this returns the pair rather than the value.

    ``False`` means a **constant**, and Django holds its *value*:
    ``Variable(constant).resolve({})`` (`base.py:745-749` for the variable part,
    `:757-761` for an argument). For a plain quoted string that value is a
    ``SafeString``; for ``_("…")`` it is ``gettext_lazy(msgid)`` — a lazy proxy,
    which is exactly why the wire carries a ``Variable`` and this flag rather
    than a resolved value: Rust cannot build a proxy, and
    ``django/tests/template_tests/syntax_tests/i18n/test_underscore_syntax.py``
    renders ``{{ _('Yes') }}`` to ``"Oui"``, which only a proxy produces.

    ``True`` means a lookup, and the ``Variable`` is kept for the context —
    ``resolve`` reads this flag to choose between ``mark_safe(arg)`` and
    ``arg.resolve(context)`` (`base.py:804-810`).

    ``resolve({})`` cannot raise ``VariableDoesNotExist`` for a constant: that
    is raised by ``_resolve_lookup``, reached only ``if self.lookups is not
    None`` (`base.py:926-928`), and a constant has no lookups. So Django's
    surrounding ``try``/``except`` is dead code and is deliberately not copied.
    """
    if value is None:
        # Only reachable for an empty token, where Django's `var_obj` stays
        # `None` (`FilterExpression("")` is valid) and nothing resolves it.
        return (lookup, None)
    variable = _materialise_variable(value, parser)
    return (lookup, variable if lookup else variable.resolve({}))


def check_filter(parser):
    """The ``check_filter`` callable for ``_native.parse``, for a ``Parser``.

    Django's two checks, in Django's order and at Django's point in the loop:
    ``Parser.find_filter`` then ``FilterExpression.args_check``
    (`base.py:771-772`). They are passed into the *parse* rather than run here
    because no batched shell-side pass reproduces their ordering — see
    ``Host::check_filter`` in ``template-core``, which carries the measurements.

    ``[None] * arg_count`` is honest rather than a shortcut: ``args_check``
    reads only ``len(provided)`` (`base.py:822-826`), which is why the seam
    carries a count and not a list of arguments.
    """

    def check(name, arg_count):
        func = parser.find_filter(name)
        FilterExpression.args_check(name, func, [None] * arg_count)

    return check


def _materialise_filter_expression(desc, parser=None):
    """A real :class:`FilterExpression` from a descriptor, without re-parsing.

    ``FilterExpression.__slots__`` is ``("token", "filters", "var", "is_var")``
    and ``__init__`` parses and computes all four, so the constructor is out of
    the question for the same reason as ``Variable``'s.

    The filter *functions* are looked up a second time here — Rust asked the
    host for them during the parse and kept only the names. **That double lookup
    is wrong for `{% load %}`** and is scheduled to become a handle at M6:
    `add_library` is `self.filters.update(...)` (`base.py:668-670`), so a load
    *after* this token's position rebinds the name, and this call happens after
    the whole parse. ``Host::check_filter`` carries the measurement and the fix.

    ``is_var`` is recomputed as ``isinstance(self.var, Variable)`` rather than
    carried on the wire, because that expression is Django's own definition
    (`base.py:777`) and a flag would be a second source of truth for it.
    """
    if parser is None:
        raise LookupError(
            "materialising a FilterExpression needs the Parser that owns the "
            "filter registry; none was passed"
        )

    expression = FilterExpression.__new__(FilterExpression)
    expression.token = desc.token

    filters = []
    for name, wire_args in desc.filters:
        args = [
            _materialise_operand(lookup, value, parser) for lookup, value in wire_args
        ]
        # `find_filter`, then `args_check`, then append — `base.py:771-774`.
        func = parser.find_filter(name)
        FilterExpression.args_check(name, func, args)
        filters.append((func, args))

    expression.filters = filters
    expression.var = _materialise_operand(desc.var[0], desc.var[1], parser)[1]
    expression.is_var = isinstance(expression.var, Variable)
    return expression


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
    # Both expressions are `#[pyclass]`es, so neither is an instance of the other
    # and their relative order is free. What is *not* free is that they come
    # before the sequence arms below: a descriptor that reached
    # `isinstance(value, list)` first would be passed through as an object
    # rather than materialised, and nothing would report it.
    if isinstance(value, _native.FilterExpr):
        return _materialise_filter_expression(value, parser)
    if isinstance(value, _native.Variable):
        return _materialise_variable(value, parser)
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
