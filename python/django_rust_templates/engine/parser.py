"""Materialise a Rust node descriptor into Django's real node classes.

Rust owns the parse; Python owns the tree that renders (design spec §5.5), and this
module is the only place the two meet. It is deliberately mechanical: look up a
class by name, call it with keyword arguments, attach ``token``/``origin``.
Django has no ``django/template/parser.py``, so this name shadows nothing upstream.
"""

from __future__ import annotations

from . import smartif
from .base import (
    FilterExpression,
    Node,
    NodeList,
    Template,
    TextNode,
    Token,
    TokenType,
    Variable,
    VariableNode,
)
from .engine import Engine
from .defaulttags import (
    AutoEscapeControlNode,
    CommentNode,
    CsrfTokenNode,
    CycleNode,
    DebugNode,
    FilterNode,
    FirstOfNode,
    ForNode,
    LoremNode,
    IfChangedNode,
    IfNode,
    NowNode,
    RegroupNode,
    ResetCycleNode,
    SpacelessNode,
    TemplateLiteral,
    TemplateTagNode,
    URLNode,
    VerbatimNode,
    WidthRatioNode,
    WithNode,
)
from .loader_tags import BlockNode, ExtendsNode, IncludeNode
from django_rust_templates.engine.templatetags.cache import CacheNode
from django_rust_templates.engine.templatetags.i18n import (
    BlockTranslateNode,
    GetAvailableLanguagesNode,
    GetCurrentLanguageBidiNode,
    GetCurrentLanguageNode,
    GetLanguageInfoListNode,
    GetLanguageInfoNode,
    LanguageNode,
    TranslateNode,
)
from django_rust_templates.engine.templatetags.l10n import LocalizeNode
from django_rust_templates.engine.templatetags.static import PrefixNode, StaticNode
from django_rust_templates.engine.templatetags.tz import (
    GetCurrentTimezoneNode,
    LocalTimeNode,
    TimezoneNode,
)
from django.utils.safestring import mark_safe

from django_rust_templates import _native

__all__ = (
    "HOST_NODE",
    "NODE_CLASSES",
    "check_filter",
    "compile_filter",
    "materialise",
    "materialise_node",
    "template_for",
)


# The reserved `class_name` of a descriptor that names a **host-owned** node: the
# Python object a user tag's compile function returned, which `template-core` stores
# behind a `NodeRef` and never reads (§5.2). It is the wire literal Rust's
# `desc::HOST_NODE` emits — asserted equal by
# `test_a_host_node_is_the_object_the_table_holds` — and a reserved *name* rather
# than a registry entry because there is nothing to build: the node already exists.
# The materialiser tests it before the registry lookup.
HOST_NODE = "HostNode"


def _ifchanged_factory(nodelist_true, nodelist_false, _varlist=()):
    """Build an `IfChangedNode`, splicing the values its `__init__` takes variadically.

    `IfChangedNode.__init__(self, nodelist_true, nodelist_false, *varlist)`
    (`defaulttags.py:262`) has no keyword for `varlist`, so `cls(**kwargs)` cannot
    express it (measured: `TypeError: … unexpected keyword argument '_varlist'`).
    The kwarg is named `_varlist` because that is Django's own attribute name
    (`defaulttags.py:265`), and the default matches Django's variadic default so an
    omitted value builds the empty case rather than raising.
    """
    return IfChangedNode(nodelist_true, nodelist_false, *_varlist)


# An explicit registry rather than `getattr(base, class_name)`: a `getattr` lookup
# would resolve anything that happens to be in Django's modules, turning a Rust typo
# into a wrong node class instead of a loud failure.
#
# A value is the **callable that builds the node from its kwarg set**, the class
# itself everywhere but `IfChangedNode` — a class is callable taking keywords, so
# `NODE_CLASSES["SpacelessNode"] = SpacelessNode` is already a factory. Any future
# variadic node needs the same treatment as `_ifchanged_factory`; a descriptor that
# names a node not buildable this way is the failure this comment exists to prevent.
NODE_CLASSES = {
    "TextNode": TextNode,
    "AutoEscapeControlNode": AutoEscapeControlNode,
    "BlockNode": BlockNode,
    # `django.templatetags.static`'s and `django.templatetags.cache`'s — the first
    # entries from outside `django.template`, which is why the import block above
    # reaches past the package. Ordinary keyword constructors
    # (`StaticNode.__init__(self, varname=None, path=None)`, `static.py:98`), so the
    # bare class is the factory.
    "CacheNode": CacheNode,
    "CommentNode": CommentNode,
    "CsrfTokenNode": CsrfTokenNode,
    "CycleNode": CycleNode,
    "DebugNode": DebugNode,
    "ExtendsNode": ExtendsNode,
    "FilterNode": FilterNode,
    "FirstOfNode": FirstOfNode,
    "ForNode": ForNode,
    # `django.templatetags.i18n`'s whole node set. All are ordinary keyword
    # constructors except `IfChangedNode`, so the bare class is the factory.
    # `TranslateNode.__init__` rewrites a literal operand in place
    # (`i18n.py:80-82`) — this class's job, not Rust's — and the materialiser runs
    # the real constructor, so that rewrite happens exactly once, here.
    #
    # `BlockTranslateNode`'s `singular`/`plural` arrive as `AttrValue::Tokens`, a
    # list of real `Token`s rather than a subtree; `_decode_value`'s `Tokens` arm
    # turns them back into the objects `render_token_list` walks.
    "BlockTranslateNode": BlockTranslateNode,
    "GetAvailableLanguagesNode": GetAvailableLanguagesNode,
    "GetCurrentLanguageBidiNode": GetCurrentLanguageBidiNode,
    "GetCurrentLanguageNode": GetCurrentLanguageNode,
    # `django.templatetags.tz`'s, ordinary keyword constructors
    # (`GetCurrentTimezoneNode.__init__(self, variable)`, `tz.py:127`), so the bare
    # class is the factory. Its siblings `LocalTimeNode` and `TimezoneNode` are
    # further down in their own sort positions.
    "GetCurrentTimezoneNode": GetCurrentTimezoneNode,
    "GetLanguageInfoListNode": GetLanguageInfoListNode,
    "GetLanguageInfoNode": GetLanguageInfoNode,
    "IfChangedNode": _ifchanged_factory,
    "IfNode": IfNode,
    "IncludeNode": IncludeNode,
    "LanguageNode": LanguageNode,
    # `django.templatetags.l10n`'s one node, an ordinary keyword constructor
    # (`LocalizeNode.__init__(self, nodelist, use_l10n)`, `l10n.py:24`), so the bare
    # class is the factory. Only its compile function (`{% localize %}`'s) is
    # Rust's; `render` stays our transcription's Python.
    "LocalizeNode": LocalizeNode,
    "LocalTimeNode": LocalTimeNode,
    "LoremNode": LoremNode,
    "NowNode": NowNode,
    "PrefixNode": PrefixNode,
    "RegroupNode": RegroupNode,
    "ResetCycleNode": ResetCycleNode,
    "SpacelessNode": SpacelessNode,
    "StaticNode": StaticNode,
    "TemplateTagNode": TemplateTagNode,
    "TimezoneNode": TimezoneNode,
    "TranslateNode": TranslateNode,
    "URLNode": URLNode,
    "VerbatimNode": VerbatimNode,
    "VariableNode": VariableNode,
    "WidthRatioNode": WidthRatioNode,
    "WithNode": WithNode,
}


def materialise(nodelist_desc, origin=None, resolve_ref=None, register_ref=None, parser=None):
    """Build a real :class:`~django.template.base.NodeList` from a descriptor.

    ``nodelist_desc`` is ``(nodes, contains_nontext)`` — or, from ``_native.parse``,
    ``(nodes, contains_nontext, refs)``, where ``refs`` is the per-parse reference
    table. The third element is **optional and tolerated** rather than required:
    every hand-built fixture in ``tests/test_materialise.py`` predates it, and a tree
    that names no slots needs no table. When it is present the two callbacks are
    derived from it, since ``refs`` is already part of the parse result; explicit
    arguments still win, which is what those fixtures use.
    """
    nodes, contains_nontext, *rest = nodelist_desc
    refs = rest[0] if rest else None
    if resolve_ref is None and refs is not None:
        resolve_ref = refs.__getitem__
    if register_ref is None and refs is not None:
        register_ref = refs.__setitem__

    nodelist = NodeList()
    # Django's `contains_nontext` is a *class* attribute, so leaving it alone is how
    # it reads False; only the True transition is ever written, which is what makes a
    # second, text-only nodelist read False again.
    if contains_nontext:
        nodelist.contains_nontext = True

    for desc in nodes:
        nodelist.append(materialise_node(desc, origin, resolve_ref, register_ref, parser))

    return nodelist


def materialise_node(desc, origin=None, resolve_ref=None, register_ref=None, parser=None):
    """Build one real node, children first.

    The ordering is load-bearing twice over. A node's ``__init__`` runs **before**
    ``token``/``origin`` are attached, and some ``__init__``s read their own subtree —
    ``ExtendsNode.__init__`` builds ``self.blocks`` by walking the nodelist it is
    given. The same order makes an ``AttrValue::NodeListRef`` resolvable: attributes
    are decoded *after* the child nodelists exist, so the position the reference
    carries is populated by the time it is read.
    ``test_a_nodelist_ref_resolves_because_children_are_built_first`` proves it,
    because reordering these three blocks is the natural thing to do when optimising
    and the failure would be an ``IndexError`` at best.

    A child nodelist whose **name is empty** is positional only: reachable through a
    ``NodeListRef`` but not passed to ``cls`` as a keyword. ``IfNode`` is why — its
    ``__init__`` takes exactly one parameter, ``conditions_nodelists``, and holds the
    branch nodelists inside that list, so there is no parameter name to give them.

    A fifth element, when the wire carries one, is ``AttrValue::NodeSlot``: the
    reference-table slot this node **is**, read before anything else because a node
    already in the table is not built at all.
    """
    class_name, attrs, nodelists, token, *rest = desc
    slot = rest[0] if rest else None
    slot_index = slot.index if isinstance(slot, _native.NodeSlot) else None

    # `HostNode` is not a Django class and is deliberately not in `NODE_CLASSES`: it
    # names a node the *host* built, already in the reference table. Checked before the
    # registry so a later entry cannot capture the reserved name, and so a genuinely
    # unknown class name stays a `LookupError`.
    #
    # The shape is a contract, not a convention, so the *whole* of it is checked: the
    # attribute is found by *name* and type-checked rather than read positionally as
    # `attrs[0][1]`, which would turn a reordered descriptor — or one whose first value
    # is not the handle — into a silently wrong node. `NodeDesc::host` emits exactly
    # `node` and nothing else, and reading the handle out of whatever the list holds
    # would accept a descriptor carrying nodelists or extra attributes and then
    # silently drop them — a dropped child nodelist renders wrong rather than raising.
    #
    # The two attachment lines are the same ones every other node gets, and skipping
    # them is the failure this arm's shape invites: Rust's `extend_nodelist` sets
    # `NodeDesc.token` for a host descriptor as for any block node, so a bare
    # `return resolve_ref(...)` would hand back a node carrying whatever the *host*
    # left on it, rendering tokenless in the annotated-traceback path.
    if class_name == HOST_NODE:
        if nodelists:
            raise LookupError(
                f"a {HOST_NODE} descriptor carries no nodelists; "
                f"got {len(nodelists)}"
            )
        if len(attrs) != 1 or attrs[0][0] != "node":
            raise LookupError(
                f"a {HOST_NODE} descriptor carries exactly one attribute, "
                f"'node'; got {list(attrs)!r}"
            )
        ref = attrs[0][1]
        if not isinstance(ref, _native.NodeRef):
            raise LookupError(
                f"a {HOST_NODE} descriptor must carry a 'node' NodeRef; got {ref!r}"
            )
        node = _decode_value(ref, resolve_ref)
        # An empty slot is a valid descriptor over a broken table, and it is checked
        # here because its symptom is *worse* than the wrong-type case above rather
        # than merely different: `node.token = ...` on a `None` raises
        # `AttributeError: 'NoneType' object has no attribute 'token' and no __dict__
        # for setting new attributes` (measured), which names neither the descriptor,
        # the slot, nor the parse it came from.
        if node is None:
            raise _empty_slot_error(f"the {HOST_NODE} descriptor's {ref!r}")
        node.token = _decode_token(token) if token is not None else None
        node.origin = origin
        return node

    try:
        cls = NODE_CLASSES[class_name]
    except KeyError:
        _unknown_node_class(class_name)

    # `{% cycle … as c %}` and every later `{% cycle c %}` are one node in Django:
    # `do_cycle` returns the *identical* object (`defaulttags.py:669-671`) and
    # `CycleNode.render` keys `context.render_context` by that object
    # (`defaulttags.py:104-114`), so two equal-but-distinct nodes would cycle
    # separately. The table is what makes them one: the first mention reserves the
    # slot and stores what it built, and every later mention finds it here.
    #
    # The token is re-attached on the way out, and that is not tidiness: it is
    # `extend_nodelist` doing `node.token = token` at *every* append (`base.py:615`),
    # so the shared object carries the **last** occurrence's token and every position
    # reads that one (measured: `['cycle c', 'cycle c']`).
    if slot_index is not None:
        existing = resolve_ref(slot_index) if resolve_ref is not None else None
        if existing is not None:
            existing.token = _decode_token(token) if token is not None else None
            existing.origin = origin
            return existing

    children = [
        (name, materialise(child, origin, resolve_ref, register_ref, parser))
        for name, child in nodelists
    ]
    child_lists = [child for _name, child in children]

    kwargs = {name: child for name, child in children if name}
    for name, value in attrs:
        kwargs[name] = _decode_value(value, resolve_ref, parser, child_lists)

    node = cls(**kwargs)

    node.token = _decode_token(token) if token is not None else None
    node.origin = origin
    # Filled **after** the node exists, which is what makes a second mention of the
    # same cycle find this one. The slot is not a constructor argument —
    # `CycleNode.__init__` takes none — so it is stored here rather than passed to `cls`.
    if slot_index is not None:
        register_ref(slot_index, node)
    return node


def _materialise_variable(desc, parser=None):
    """A real :class:`Variable` from a descriptor, without re-parsing.

    ``__new__`` plus slot assignment rather than ``Variable(desc.var)``: the
    constructor would parse the token a second time in Python and discard Rust's
    parse — including the error messages and the token each carries, which are
    Rust's to produce (§6). ``Variable.__slots__`` is
    ``("var", "literal", "lookups", "translate", "message_context")`` and
    ``__init__`` assigns four of them; ``message_context`` is set to ``None`` there
    and never assigned during a parse, so setting it here is what Django itself does.
    ``isinstance(result, Variable)`` is what ``FilterExpression.is_var`` reads, so
    this is a real ``Variable`` and not a lookalike.
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

    ``(lookup, value)`` is the shape Django holds — ``args`` is a list of pairs and
    ``resolve`` unpacks it (`base.py:804-809`), and ``args_check`` counts it
    (`base.py:822-826`).

    ``False`` means a **constant**, and Django holds its *value*:
    ``Variable(constant).resolve({})`` (`base.py:755`, `base.py:768`). For a plain
    quoted string that value is a ``SafeString``; for ``_("…")`` it is
    ``gettext_lazy(msgid)`` — a lazy proxy, which is why the wire carries a
    ``Variable`` and this flag rather than a resolved value: Rust cannot build a
    proxy, and ``{{ _('Yes') }}`` renders ``"Oui"`` only through one.

    ``True`` means a lookup, and the ``Variable`` is kept for the context —
    ``resolve`` reads this flag to choose between ``mark_safe(arg)`` and
    ``arg.resolve(context)`` (`base.py:804-809`).

    ``resolve({})`` cannot raise ``VariableDoesNotExist`` for a constant: that is
    raised by ``_resolve_lookup``, reached only ``if self.lookups is not None``
    (`base.py:926-928`), and a constant has no lookups — so Django's surrounding
    ``try``/``except`` is dead code and is deliberately not copied.
    """
    if value is None:
        # Only reachable for an empty token, where Django's `var_obj` stays
        # `None` (`FilterExpression("")` is valid) and nothing resolves it.
        return (lookup, None)
    variable = _materialise_variable(value, parser)
    return (lookup, variable if lookup else variable.resolve({}))


def template_for(nodelist, engine=None, parser=None, origin=None, source=None, plan=None):
    """A real :class:`~django.template.base.Template` around a materialised tree.

    The render goes through this rather than ``nodelist.render(context)`` directly,
    because **rendering a nodelist requires a bound context** and binding is a
    `Template`-level act: `Template.render` enters `context.bind_template(self)`, and
    the render path reads `context.template.engine` in six places — `string_if_invalid`
    for a failed lookup (`base.py:793,997,1005,1011,1024`) and `engine.debug` for the
    annotated traceback (`base.py:1054`). With `context.template` left `None`,
    `{{ nope }}` raises `AttributeError: 'NoneType' object has no attribute 'engine'`
    instead of rendering `string_if_invalid` (measured).

    Built by ``__new__`` and direct assignment rather than ``Template(source,
    engine=…)``, because that constructor *parses* in Python and would discard the
    tree Rust just built. The cost is that this function owns the attribute list:

    * ``nodelist`` — `Template._render`.
    * ``engine`` — the six reads above.
    * ``name`` — `Template.render` assigns it to `context.template_name`.
    * ``origin`` — the debug exception path (`get_exception_info`,
      `base.py:1060-1063`), and a *comparison* one step earlier: `Node.render_annotated`
      attaches `template_debug` only when `context.render_context.template.origin ==
      e._culprit_node.origin` (`base.py:1058-1062`). Node origins come from the same
      object, so passing one value to both makes them equal by construction.
      ``None`` (the default, and what every hand-built fixture gets) is that
      comparison holding trivially, which is why the parameter defaults.
    * ``source`` — the debug exception path, and not optional in practice:
      `get_exception_info` (`base.py:1060-1063`) reads ``token.position`` *and* slices
      ``self.source``, so an empty source returns a `template_debug` whose
      ``before``/``during``/``after``/``source_lines``/``line``/``total`` are empty or
      wrong rather than failing loudly. ``None`` means "the caller had none".
    * ``extra_data`` — ``self.extra_data = parser.extra_data``, but **not** in
      `Template.__init__`: it is inside `compile_nodelist`'s `try`, after
      `parser.parse()` and before `return nodelist` (`base.py:198-200`). `{% partial %}`
      reads it at M13 (`engine.py:194`); a shell that builds the Template by `__new__`
      skips the line, and the failure would be silent — a partial lookup that always
      misses.

    ``parser`` is a parameter because the parser is per-parse and this function has no
    other way to see it; ``None`` gives `{}`, the value `Parser.__init__` sets
    (`base.py:514`).

    ``engine`` is the caller's, or a fresh `Engine()`: the render path *reads* it — the
    six reads above — so a fresh one would render a different answer than the engine
    the caller chose (measured: `Engine(string_if_invalid="<<invalid>>")` renders
    `{{ nope }}` as `'&lt;&lt;invalid&gt;&gt;'`, where a shell that built its own
    rendered `''`).
    """
    if engine is None:
        engine = Engine()
    template = Template.__new__(Template)
    template.nodelist = nodelist
    template.engine = engine
    template.name = None
    template.origin = origin
    template.source = "" if source is None else source
    template.extra_data = {} if parser is None else parser.extra_data
    # The only attribute whose absence is *safe*: `_native_plan` defaults to `None`
    # on the class, and `None` is the Python render path. Set unconditionally rather
    # than by `if plan is not None` so the assignment always happens rather than
    # leaving the class default as the only spelling.
    template._native_plan = plan
    return template


def check_filter(parser):
    """The ``check_filter`` callable for ``_native.parse``, for a ``Parser``.

    Django's two checks, in Django's order and at Django's point in the loop:
    ``Parser.find_filter`` then ``FilterExpression.args_check`` (`base.py:771-772`).
    They are passed into the *parse* rather than run here because no batched
    shell-side pass reproduces their ordering — see ``Host::check_filter`` in
    ``template-core`` for the measurements. ``[None] * arg_count`` is honest: Django
    reads only ``len(provided)`` (`base.py:822-826`), which is why the seam carries a
    count and not a list.

    **The function is returned, not discarded**, and that return is the point of the
    seam: ``filters.append((filter_func, args))`` (`base.py:773`) is what renders, and
    the *name* is not a stable way to reach it again, since ``Parser.add_library``
    rebinds it (`base.py:669-670`). The parse keeps it by handle, at the position of
    the token that named it, and materialisation resolves the handle rather than
    asking for the name a second time — the observable difference being a
    ``{% load %}`` between the two. See ``tests/test_filter_rebinding.py``.
    """

    def check(name, arg_count):
        func = parser.find_filter(name)
        FilterExpression.args_check(name, func, [None] * arg_count)
        return func

    return check


def compile_filter(parser):
    """The ``compile_filter`` callable for ``_native.parse``, for a ``Parser``.

    This is ``TemplateIfParser.create_var``'s ``self.template_parser.compile_filter(value)``
    (`defaulttags.py:929`) — the ``FilterExpression`` behind one ``{% if %}`` operand.
    Django's own ``Parser.compile_filter`` *is* that call (``base.py:676``), so this
    hands the bound method over rather than wrapping it.

    **A peer of ``check_filter``, not a variant of it**: ``check_filter`` is the
    ``find_filter`` + ``args_check`` pair Django runs *inside* ``FilterExpression.__init__``'s
    loop (``base.py:771-772``), while this is the whole ``FilterExpression``, built
    once per operand before the operator algorithm runs. A condition's operand cannot
    be validated and left as a name — ``TemplateLiteral.eval`` calls
    ``self.value.resolve(context, ignore_failures=True)`` (``defaulttags.py:917-918``)
    at render time, so a real object has to exist.

    ``None`` is not a permissive default here, unlike ``check_filter``'s — see
    ``Host::compile_filter`` in ``template-core``. A caller that has a ``Parser``
    should pass it; ``_native.compile_template`` builds its own from the parser it was
    handed, so the shell's end-to-end path cannot forget.
    """
    return parser.compile_filter


def _materialise_filter_expression(desc, parser=None, resolve_ref=None):
    """A real :class:`FilterExpression` from a descriptor, without re-parsing.

    ``FilterExpression.__slots__`` is ``("token", "filters", "var", "is_var",
    "_rust_names")`` and ``__init__`` sets the first four, so the constructor is out
    of the question for the same reason as ``Variable``'s. The fifth is ours and is
    **not filled here**: ``FilterExpression.resolve`` fills it on first use, the one
    site both construction routes pass through. See ``host.django_own_filter_names``
    for why the answer belongs to the expression rather than to the application.

    **The filter function comes from the handle the parse carried, not from a second
    lookup by name.** ``find_filter``'s result is what renders (``base.py:773``) and
    ``Parser.add_library`` rebinds the name (``base.py:669-670``), and this call
    happens after the whole parse — when a late lookup would go wrong.
    ``Host::check_filter`` carries the measurement and ``tests/test_filter_rebinding.py``
    is the sandwich. When the desc carries no handle — a host with no registry, as the
    descriptor-shape tests and the parity corpus's "no host" rows use — the name is
    looked up here with ``args_check``, exactly as Django would: a host that ran no
    check has no ``{% load %}`` to rebind a name either.

    ``is_var`` is recomputed as ``isinstance(self.var, Variable)`` rather than carried
    on the wire, because that expression is Django's own definition (``base.py:783``)
    and a flag would be a second source of truth for it.

    A handle with no way to resolve it is **loud rather than silently
    late-looked-up**: the silent version of that mistake is the rebinding bug this
    function exists to avoid.
    """
    if parser is None:
        raise LookupError(
            "materialising a FilterExpression needs the Parser that owns the "
            "filter registry; none was passed"
        )
    if desc.refs is not None:
        # A descriptor from `parse_filter_expression` owns the table its handles index
        # into, and that table travels attached to it. It **wins over any `resolve_ref`
        # the caller passed**: a handle is an index, meaningful only against its own
        # table, so resolving these against a different parse's table returns a valid
        # function that is the *wrong* one — silently, the shape of the late-lookup bug
        # this function exists to kill. The whole-template path never builds such a
        # descriptor (`AttrValue::FilterExpr` sends `refs=None`), so its `resolve_ref`
        # still resolves every other reference in the tree.
        resolve_ref = desc.refs.__getitem__

    expression = FilterExpression.__new__(FilterExpression)
    expression.token = desc.token

    filters = []
    for (name, wire_args), handle in desc.filters:
        args = [
            _materialise_operand(lookup, value, parser) for lookup, value in wire_args
        ]
        if handle is None:
            # `find_filter`, then `args_check`, then append — `base.py:771-773`.
            func = parser.find_filter(name)
            FilterExpression.args_check(name, func, args)
        elif resolve_ref is None:
            raise LookupError(
                f"the filter {name!r} arrived as {handle!r} but no resolve_ref was "
                "passed to materialise, so the function Django bound at "
                "base.py:771 cannot be reached"
            )
        else:
            func = resolve_ref(handle.index)
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
    ``isinstance(value, int)`` arm ahead of it would turn every boolean attribute —
    ``{% autoescape on %}``'s ``setting`` — into ``0``/``1``, which compares equal and
    passes any ``assertEqual`` while being the wrong type.

    ``float`` rides alongside ``int`` and ``str`` because it is already the Python
    object the descriptor meant: ``Variable.literal`` for the ``float(var)`` branch is
    a Python ``float`` on Django's side too, so there is nothing to convert. Without
    the arm the value falls through to the ``LookupError`` below, which is how a
    missing arm announces itself.

    The arguments are defaults because this is also the entry point
    ``tests/test_variable_parity.py`` drives directly with a bare literal, where there
    is no enclosing node to resolve a reference against. A reference reaching it
    without them is loud rather than silently ``None``.
    """
    if isinstance(value, bool):
        return value
    if value is None or isinstance(value, (int, float, str)):
        return value
    if isinstance(value, _native.NodeRef):
        if resolve_ref is None:
            raise _missing_resolver_error(repr(value))
        return resolve_ref(value.index)
    if isinstance(value, _native.NodeSlot):
        # `AttrValue::NodeSlot` as an attribute *value*: the object at that slot. The
        # same table `NodeRef` indexes, reached the other way round — a `NodeRef` entry
        # is filled by the host during the parse, a `NodeSlot` entry by the materialiser
        # as it builds. `ResetCycleNode.node` is the only attribute that holds one.
        #
        # Empty is loud rather than `None`: Django raises at compile time for a
        # `{% resetcycle %}` with no `{% cycle %}` (`defaulttags.py:1441-1443`), so an
        # empty slot here is a wire bug, and a `ResetCycleNode` holding `None` would
        # fail later and elsewhere — inside `render`, as `None.reset(context)`.
        if resolve_ref is None:
            raise _missing_resolver_error(repr(value))
        found = resolve_ref(value.index)
        if found is None:
            raise _empty_slot_error(repr(value))
        return found
    if isinstance(value, _native.NodeListRef):
        # `AttrValue::NodeListRef`: a position in the *enclosing* node's own nodelists,
        # already materialised by `materialise_node`. Its own type rather than an `int`
        # because an integer attribute is also an `int` on the wire, and the failure
        # mode would be a node holding a wrong nodelist.
        if child_lists is None:
            raise _missing_child_lists_error(repr(value))
        try:
            return child_lists[value.index]
        except IndexError:
            raise _nodelist_ref_out_of_range_error(repr(value), len(child_lists))
    if isinstance(value, _native.SafeStr):
        return mark_safe(value.text)
    if isinstance(value, _native.Tokens):
        # `AttrValue::Tokens` — `BlockTranslateNode.singular`/`.plural`, the one
        # attribute that is a slice of the *token stream* rather than a subtree, and
        # the reason it is its own wire class: a bare list of 4-tuples would decode
        # through the `list` arm below into plain tuples, and `render_token_list`'s
        # `token.token_type` would be an `AttributeError` at *render* time, after a
        # parse that looked fine.
        return [_decode_token(wire) for wire in value.tokens]
    # Both expressions are `#[pyclass]`es, so neither is an instance of the other and
    # their relative order is free. What is *not* free is that they come before the
    # sequence arms below: a descriptor reaching `isinstance(value, list)` first would
    # be passed through as an object rather than materialised, and nothing would report
    # it.
    if isinstance(value, _native.FilterExpr):
        # `resolve_ref` is what reaches the *function* Django bound for each filter, by
        # the handle the parse carried: see `_materialise_filter_expression`.
        return _materialise_filter_expression(value, parser, resolve_ref)
    if isinstance(value, _native.Variable):
        return _materialise_variable(value, parser)
    if isinstance(value, _native.IfCondition):
        # `AttrValue::IfCondition`: one `{% if %}` condition, as the `smartif` tree. It
        # comes first among the container-shaped arms because it is its own `#[pyclass]`
        # and not a sequence — but it sits here rather than with `NodeRef` because its
        # *leaf* is a reference, so it needs `resolve_ref` for the same reason.
        return _decode_condition(value, resolve_ref)
    if isinstance(value, tuple):
        # `AttrValue::Tuple` — `IfNode.conditions_nodelists`' `(condition, nodelist)`
        # pairs. A real `tuple` and not a list: `IfNode.render` *unpacks* each pair
        # (`defaulttags.py:323`), and the parity suite can tell the two containers
        # apart, because `describe`'s tuple arm carries a marker the list arm does not.
        return tuple(
            _decode_value(item, resolve_ref, parser, child_lists) for item in value
        )
    if isinstance(value, list):
        # `AttrValue::List`. Recursed rather than returned as-is: the Rust side
        # converts eagerly, so the elements are already Python objects of the wire
        # types above — a `SafeStr` inside an `IfNode` condition list still needs
        # `mark_safe`, and a nested `NodeListRef` still needs its nodelist.
        return [
            _decode_value(item, resolve_ref, parser, child_lists) for item in value
        ]
    if isinstance(value, dict):
        # `AttrValue::Map`, already a real `dict` with `token_kwargs`' own repeated-key
        # behaviour (first position, last value) applied on the Rust side. Only the
        # *values* need decoding.
        return {
            key: _decode_value(item, resolve_ref, parser, child_lists)
            for key, item in value.items()
        }
    raise LookupError(
        f"cannot decode descriptor value {value!r} of type {type(value).__name__}"
    )


def _decode_condition(condition, resolve_ref=None):
    """One ``{% if %}`` condition node, as a real ``smartif`` object.

    **Real ones, not look-alikes**, because ``IfNode.render`` calls
    ``condition.eval(context)`` on each branch's first element
    (``defaulttags.py:326``): ``Operator.eval`` recurses through its own
    ``self.first``/``self.second`` and ``TemplateLiteral.eval`` calls
    ``self.value.resolve(context, ignore_failures=True)`` (``defaulttags.py:917-918``).

    ``OPERATORS[key]()`` rather than a class kept on the wire, because that is exactly
    what ``IfParser.translate_token`` does (``smartif.py:179-185``) — one instance per
    occurrence, whose ``id`` the module-level loop set (``smartif.py:115-116``).
    ``first``/``second`` are then **assigned** rather than passed, which is what ``led``
    and ``nud`` do (``smartif.py:54-56``, ``smartif.py:81-83``): ``Operator`` is built inside a
    closure in ``infix``/``prefix`` and its signature is not something we may rely on.

    Both error paths are wire bugs rather than template bugs, so they are loud. Django
    cannot produce either — ``create_var`` always compiles a real ``FilterExpression``,
    and ``nud``/``led`` always set ``first`` — so a condition reaching here without
    them means the Rust side emitted a shape it does not build.
    """
    if condition.kind == "literal":
        if resolve_ref is None:
            raise LookupError(
                f"{condition!r} needs a resolve_ref callback; none was passed "
                "to materialise"
            )
        expression = resolve_ref(condition.expression.index)
        if expression is None:
            # An *empty* slot rather than a missing callback: the host reserves the
            # handle and fills it in the same call, so this means the table handed to
            # the materialiser is not the one the parse filled.
            raise LookupError(
                f"{condition!r} names an empty reference-table slot; the refs "
                "passed to materialise are not the parse's own"
            )
        # `TemplateLiteral(value, text)` (`defaulttags.py:909-911`) — the
        # `FilterExpression` first, the raw token second.
        return TemplateLiteral(expression, condition.text)

    if condition.first is None:
        raise LookupError(
            f"{condition!r} is an operator with no `first` operand; every "
            "smartif operator sets one (`nud` at smartif.py:81, `led` at :54)"
        )
    try:
        make = smartif.OPERATORS[condition.key]
    except KeyError:
        raise LookupError(
            f"{condition!r} names an operator {condition.key!r} that "
            "smartif.OPERATORS does not have"
        ) from None
    node = make()
    node.first = _decode_condition(condition.first, resolve_ref)
    # `None` on a prefix operator, and that is `nud`'s own line rather than an
    # omission — see `IfConditionDesc::Operator::second`.
    node.second = (
        None
        if condition.second is None
        else _decode_condition(condition.second, resolve_ref)
    )
    return node


# Whether `_native.compile_template` builds the tree with the Rust walk in
# `crates/template-py/src/materialise.rs` instead of `materialise` above.
#
# **At the end of the file, and that is load-bearing.** `scripts/anchors.txt` carries
# anchors into this file by line number, so a name inserted near the top moves every
# one of them; appending below every anchor moves none.
#
# Read by Rust as a **bool or nothing**: a missing attribute reads `False`, so a Rust
# tree newer than the Python module beside it does not fail a compile — but a value
# that is *present and not a bool* raises, because that is a bug in this file rather
# than a version skew.
#
# Kept rather than deleted because it is the A/B seam `scripts/compile_profile.py`
# measures through: with it off the walk is the Python one, and the two numbers are
# one subtraction apart.
USE_NATIVE_MATERIALISER = True


def _unknown_node_class(class_name):
    """Raise the registry error, from the one place that spells it.

    `materialise_node` raises this message and the Rust walk raises the same condition;
    the string lives here so that a test which asserts it asserts one string rather than
    two that agree today.

    **`from None` is load-bearing.** The caller is `materialise_node`'s `except KeyError`
    arm, so a bare `raise` here would chain the `KeyError` as `__context__` and print it
    under the `LookupError`; `raise … from None` sets `__suppress_context__` wherever it
    is raised, so the message and the suppression come out as they did inline (measured:
    `__context__` is the `KeyError`, `__suppress_context__` is True).

    **The traceback is not identical, and this frame is the difference**: extracted, the
    helper appends `_unknown_node_class` to the frame list. That is the price of
    spelling the message once; the exception type, the message and the chaining all
    hold, and nothing asserts a frame list.
    """
    raise LookupError(
        f"no materialiser for node class {class_name!r}; add it to NODE_CLASSES"
    ) from None


def _missing_resolver_error(rendered):
    """The one spelling of the no-resolver `LookupError`."""
    raise LookupError(
        f"{rendered} needs a resolve_ref callback; none was passed to materialise"
    )


def _empty_slot_error(rendered):
    """The one spelling of the empty-slot `LookupError`.

    `rendered` is a whole phrase rather than a `NodeSlot` repr, because the host arm
    reaches this too and its message must keep saying *which* descriptor failed.
    """
    raise LookupError(f"{rendered} is an empty reference-table slot")


def _missing_child_lists_error(rendered):
    """The one spelling of the no-enclosing-node `LookupError`.

    This one names `_decode_value` and not `materialise`, unlike the others, and the
    difference is a fact rather than an oversight: the path is reached when the decoder
    is called directly with no enclosing node. The Rust walk cannot reach it — `attr_to_py`
    is only ever handed the enclosing node's children — so no `AttrValue` arm calls it.
    """
    raise LookupError(
        f"{rendered} needs the enclosing node's nodelists; "
        "none were passed to _decode_value"
    )


def _nodelist_ref_out_of_range_error(rendered, count):
    """The one spelling of the out-of-range `LookupError`.

    `from None` is *inside* here, not at the call site, and that is the point of the
    helper raising rather than returning: the only call site sits in an
    `except IndexError:`, so a `raise … from None` there never runs — the helper raises
    first — and the `IndexError` stays on as `__context__`, showing the user
    `list index out of range` under a message that already says what went wrong.
    """
    raise LookupError(
        f"{rendered} is out of range: the enclosing node has {count} nodelist(s)"
    ) from None
