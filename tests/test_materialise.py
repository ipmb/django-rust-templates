"""Materialisation: descriptors become Django's own node objects.

These run against Django's real modules (``.django`` on ``sys.path``, no shim),
because the whole point is that the objects produced are Django's — the same
classes ``isinstance``, ``get_nodes_by_type`` and ``ExtendsNode.render`` test for.

The materialiser is loaded from its file rather than imported as
``django_rust_templates._impl.parser``: ``_impl/__init__.py`` is the shim's
``django.template`` and imports its own siblings (``from .engine import
Engine`` …), so importing the ``_impl`` *package* by its ordinary name executes
that file and fails for want of ``engine``. Loading the module directly is how
this test reaches the materialiser without installing the shim, which is what
keeps the node classes Django's own.
"""

import importlib.util
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template.base import (  # noqa: E402
    FilterExpression,
    Lexer,
    Node,
    NodeList,
    Parser,
    TextNode,
    Token,
    TokenType,
    Variable,
    VariableNode,
)
from django.template.defaulttags import (  # noqa: E402
    AutoEscapeControlNode,
    IfChangedNode,
    SpacelessNode,
    VerbatimNode,
)
from django.template.engine import Engine  # noqa: E402
from django.utils.functional import Promise  # noqa: E402
from django.utils.safestring import SafeString  # noqa: E402

from django_rust_templates import _native  # noqa: E402

_MATERIALISER_SPEC = importlib.util.spec_from_file_location(
    "django_rust_templates._impl.parser",
    PROJECT_ROOT / "python" / "django_rust_templates" / "_impl" / "parser.py",
)
materialiser = importlib.util.module_from_spec(_MATERIALISER_SPEC)
sys.modules[_MATERIALISER_SPEC.name] = materialiser
_MATERIALISER_SPEC.loader.exec_module(materialiser)

TEXT = ("TextNode", (("s", "hi"),), (), (0, "hi", 1, (0, 2)))


def parser():
    """A `Parser` with Django's real filter and tag registries.

    A bare `Parser(tokens)` registers **nothing** — `builtins=None` becomes `[]`
    (`base.py:518-519`) — so it knows neither `upper` nor `spaceless`, and
    `find_filter` raises `Invalid filter` for every name. This is the same trap
    `tests/test_parser_errors.py` records: the libraries have to come from an
    `Engine` instance, because `template_builtins` is set in `Engine.__init__`
    (`engine.py:62-63`) rather than on the class. This is the path
    `Engine.compile_template` itself takes.
    """
    engine = Engine()
    return Parser([], engine.template_libraries, engine.template_builtins)


def text_desc(contents, token=None):
    return ("TextNode", (("s", contents),), (), token)


def collect_class_names(nodelist_desc):
    """Every `class_name` in a descriptor tree, nested nodelists included.

    Reads the wire form directly rather than materialising, so it can report
    what the emitter produced even when the registry cannot build it.
    """
    names = set()
    stack = [nodelist_desc]
    while stack:
        nodes, _contains_nontext, *_refs = stack.pop()
        for class_name, _attrs, nodelists, _token, *_slot in nodes:
            names.add(class_name)
            stack.extend(nodelist for _name, nodelist in nodelists)
    return names


class MaterialiseTests(unittest.TestCase):
    def test_a_text_node_is_a_real_text_node(self):
        nodelist = materialiser.materialise(([TEXT], False))
        self.assertIsInstance(nodelist, NodeList)
        self.assertEqual(len(nodelist), 1)
        node = nodelist[0]
        self.assertIsInstance(node, TextNode)
        self.assertEqual(node.s, "hi")

    def test_the_token_is_a_real_token(self):
        node = materialiser.materialise_node(TEXT)
        self.assertIsInstance(node.token, Token)
        self.assertEqual(node.token.token_type, TokenType.TEXT)
        self.assertEqual(node.token.contents, "hi")
        self.assertEqual(node.token.lineno, 1)
        self.assertEqual(node.token.position, (0, 2))
        self.assertIsNone(node.origin)

    def test_attrs_are_passed_by_keyword_and_kept_by_type(self):
        desc = ("AutoEscapeControlNode", (("setting", True),), (("nodelist", ([], False)),), None)
        node = materialiser.materialise_node(desc)
        self.assertIsInstance(node, AutoEscapeControlNode)
        self.assertIs(node.setting, True)
        self.assertIsInstance(node.setting, bool)
        self.assertIsInstance(node.nodelist, NodeList)

    def test_a_false_setting_is_not_confused_with_none(self):
        # The wire has `bool` and `None` and no discriminant: this is the case
        # an `isinstance(value, int)` test ordered before `bool` would flatten,
        # and it is the one `{% autoescape off %}` produces.
        desc = ("AutoEscapeControlNode", (("setting", False),), (("nodelist", ([], False)),), None)
        self.assertIs(materialiser.materialise_node(desc).setting, False)

    def test_children_are_built_before_the_parent_and_token_comes_after(self):
        class SpyNode(Node):
            def __init__(self, nodelist):
                self.received = nodelist
                self.seen = [type(child).__name__ for child in nodelist]
                # `Node.token` is a *class* attribute defaulting to None
                # (`base.py:1036`), so "not attached yet" reads as None, not as
                # a missing attribute.
                self.token_at_construction = getattr(self, "token", None)

        materialiser.NODE_CLASSES["SpyNode"] = SpyNode
        self.addCleanup(materialiser.NODE_CLASSES.pop, "SpyNode")

        desc = ("SpyNode", (), (("nodelist", ([text_desc("in")], False)),), (2, "spy", 1, None))
        node = materialiser.materialise_node(desc)

        # A real `NodeList`, not a generator or a plain list, and populated
        # before construction: a lazily-materialised child list fails one of
        # these, exactly as `ExtendsNode.__init__`'s
        # `nodelist.get_nodes_by_type(BlockNode)` would at M7.
        self.assertIsInstance(
            node.received, NodeList,
            "the constructor must receive a real NodeList, not a lazy list",
        )
        self.assertEqual(
            node.seen, ["TextNode"], "the constructor must receive a complete child list"
        )
        self.assertIsNone(
            node.token_at_construction,
            "token must be attached after construction, not before",
        )
        self.assertEqual(node.token.contents, "spy")

    def test_contains_nontext_maps_both_ways(self):
        self.assertFalse(materialiser.materialise(([], False)).contains_nontext)
        self.assertTrue(materialiser.materialise(([], True)).contains_nontext)

    def test_a_second_nodelist_does_not_inherit_the_flag(self):
        # `contains_nontext` is a class attribute on NodeList, so an
        # implementation that set it on the class rather than the instance would
        # make every later nodelist read True.
        materialiser.materialise(([text_desc("x")], True))
        self.assertFalse(materialiser.materialise(([text_desc("y")], False)).contains_nontext)

    def _register(self, name, cls):
        materialiser.NODE_CLASSES[name] = cls
        self.addCleanup(materialiser.NODE_CLASSES.pop, name)

    def test_a_ref_is_resolved_through_the_callback(self):
        class RefNode(Node):
            def __init__(self, thing):
                self.thing = thing

        self._register("RefNode", RefNode)

        desc = ("RefNode", (("thing", _native.NodeRef(7)),), (), None)
        seen = []

        def resolve(index):
            seen.append(index)
            return index

        node = materialiser.materialise_node(desc, resolve_ref=resolve)
        self.assertEqual(seen, [7], "the index must reach the resolver unchanged")
        self.assertEqual(node.thing, 7)

    def test_a_ref_without_a_resolver_is_loud(self):
        class RefNode(Node):
            def __init__(self, thing):
                self.thing = thing

        self._register("RefNode", RefNode)

        desc = ("RefNode", (("thing", _native.NodeRef(7)),), (), None)
        with self.assertRaises(LookupError) as caught:
            materialiser.materialise_node(desc)
        self.assertIn("resolve_ref", str(caught.exception))

    def test_a_host_node_is_the_object_the_table_holds(self):
        """`HostNode` is a *reserved descriptor name*, not a Django class.

        §5.2: a `NodeRef` is "an opaque handle to a Python-owned `Node` returned
        by a user tag callback. `template-core` never dereferences it." So the
        descriptor's job for one is to name the slot; the materialiser returns
        what the host stored there, with `token`/`origin` attached the way
        `extend_nodelist` attaches them to every other node.
        """
        held = TextNode("held")
        desc = ("HostNode", (("node", _native.NodeRef(0)),), (), None)

        node = materialiser.materialise_node(desc, resolve_ref=lambda index: held)
        self.assertIs(node, held)

        # The literal above is the wire, and `NodeDesc::host` emits it; the
        # module constant is this side's name for it, so the two must agree.
        self.assertEqual(materialiser.HOST_NODE, "HostNode")

        # `extend_nodelist` sets `NodeDesc.token` for a host descriptor exactly
        # as it does for any other block node, so materialisation has to
        # re-attach it. A bare `return resolve_ref(...)` instead hands back a
        # node carrying whatever token the *host* left on it, and a user tag's
        # node is then the one shape that renders tokenless in the debug
        # traceback path.
        tokened = ("HostNode", (("node", _native.NodeRef(0)),), (), (2, "echo", 1, (0, 4)))
        node = materialiser.materialise_node(
            tokened, origin="origin.html", resolve_ref=lambda index: held
        )
        self.assertIs(node.token.token_type, TokenType.BLOCK)
        self.assertEqual(node.token.contents, "echo")
        self.assertEqual(node.origin, "origin.html")

    def test_a_host_node_descriptor_of_the_wrong_shape_is_loud(self):
        """The descriptor's shape is a contract; a violation must not be dropped.

        `NodeDesc::host` emits exactly one attribute, `node`, and no children.
        The arm above the registry reads the handle out of the attribute list,
        and the first draft read it out of *whatever* list it was handed — so a
        descriptor carrying a second attribute, or the wrong attribute name, or
        a child nodelist, materialised the held node and discarded the rest in
        silence. Measured before the guard was written: all three returned the
        held `TextNode`. A dropped nodelist is the dangerous one, because the
        node then renders as though the children were never there.
        """
        held = TextNode("held")
        resolve = lambda index: held  # noqa: E731 — short and local, as above

        cases = {
            "a second attribute": (
                ("HostNode", (("node", _native.NodeRef(0)), ("extra", 1)), (), None),
                "exactly one attribute",
            ),
            "the wrong attribute name": (
                ("HostNode", (("handle", _native.NodeRef(0)),), (), None),
                "exactly one attribute",
            ),
            "a child nodelist": (
                (
                    "HostNode",
                    (("node", _native.NodeRef(0)),),
                    (("", ([], False)),),
                    None,
                ),
                "no nodelists",
            ),
        }
        for label, (desc, expected) in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(LookupError) as caught:
                    materialiser.materialise_node(desc, resolve_ref=resolve)
                self.assertIn(expected, str(caught.exception))

    def test_a_host_node_over_an_empty_slot_is_loud(self):
        """A broken *table* is a different failure from a broken descriptor.

        The wrong-type case above raises a `LookupError` naming the descriptor;
        the empty-slot case used to raise
        `AttributeError: 'NoneType' object has no attribute 'token' and no
        __dict__ for setting new attributes` (measured), which names neither the
        descriptor, the slot, nor the parse. Same style as the `NodeSlot` check
        in `_decode_value`.
        """
        desc = ("HostNode", (("node", _native.NodeRef(0)),), (), None)
        with self.assertRaises(LookupError) as caught:
            materialiser.materialise_node(desc, resolve_ref=lambda index: None)
        self.assertIn("empty reference-table slot", str(caught.exception))

    def test_the_host_node_sentinel_is_not_a_django_class(self):
        """`desc::HOST_NODE` must not name a class Django could materialise.

        Two guards, and they are not the same claim. The loop below is the
        *upstream* one: if Django ever gained a class by this name, the sentinel
        would be shadowing it. The `NODE_CLASSES` assertion is the one that
        matches the stated failure mode — the arm tests the sentinel *before*
        the registry lookup, so a `NODE_CLASSES` entry under this name would
        never be reached, and every descriptor naming it would silently
        materialise *our* node instead of the registered class. That is the
        invisible failure; the loop alone cannot see it.
        """
        from django.template import base, defaulttags, loader_tags

        for module in (base, defaulttags, loader_tags):
            self.assertFalse(
                hasattr(module, "HostNode"), f"django {module.__name__} defines HostNode"
            )
        self.assertNotIn(materialiser.HOST_NODE, materialiser.NODE_CLASSES)

    def test_a_safe_str_becomes_a_safe_string(self):
        desc = ("VerbatimNode", (("content", _native.SafeStr("{{ raw }}")),), (), None)
        node = materialiser.materialise_node(desc)
        self.assertIsInstance(node, VerbatimNode)
        self.assertIsInstance(node.content, SafeString)
        self.assertEqual(node.content, "{{ raw }}")

    def test_an_unknown_class_name_names_itself(self):
        with self.assertRaises(LookupError) as caught:
            materialiser.materialise_node(("NoSuchNode", (), (), None))
        self.assertIn("NoSuchNode", str(caught.exception))

    def test_an_unknown_value_type_is_loud(self):
        desc = ("TextNode", (("s", object()),), (), None)
        with self.assertRaises(LookupError):
            materialiser.materialise_node(desc)

    def test_a_nodelist_ref_resolves_because_children_are_built_first(self):
        # The claim `AttrValue::NodeListRef` rests on: a node's attributes are
        # decoded *after* its child nodelists are materialised, so the index is
        # populated by the time it is read. If `materialise_node` ever reorders
        # — constructing the parent before the children — this fails loudly
        # rather than silently resolving a nodelist that is not there yet.
        #
        # The shape of that failure is pinned rather than described:
        # `test_a_nodelist_ref_out_of_range_is_loud` is the same call with an
        # index nothing can satisfy, so a reordering turns this test into that
        # one's `LookupError` rather than into a quiet wrong answer.
        class RefToListNode(Node):
            def __init__(self, child, nodelist=None):
                self.child = child
                self.nodelist = nodelist

        self._register("RefToListNode", RefToListNode)

        desc = (
            "RefToListNode",
            (("child", _native.NodeListRef(0)),),
            (("nodelist", ([text_desc("a")], False)),),
            None,
        )
        node = materialiser.materialise_node(desc)
        self.assertIsInstance(node.child, NodeList)
        self.assertEqual(len(node.child), 1)
        self.assertIsInstance(node.child[0], TextNode)

    def test_a_reference_resolves_to_the_nodelist_at_its_index(self):
        # Not just "a nodelist": the *right* one. With one nodelist every index
        # bug resolves to something, so this needs two.
        class TwoChildNode(Node):
            def __init__(self, first, second, nodelist=None, other=None):
                self.first = first
                self.second = second
                self.nodelist = nodelist
                self.other = other

        self._register("TwoChildNode", TwoChildNode)

        desc = (
            "TwoChildNode",
            (("first", _native.NodeListRef(1)), ("second", _native.NodeListRef(0))),
            (
                ("nodelist", ([text_desc("a")], False)),
                ("other", ([text_desc("b")], False)),
            ),
            None,
        )
        node = materialiser.materialise_node(desc)
        self.assertEqual(node.first[0].s, "b", "index 1 is the second nodelist")
        self.assertEqual(node.second[0].s, "a", "index 0 is the first nodelist")
        # A named nodelist is both a keyword argument *and* referenceable by
        # position; the two views must agree, or the ref is resolving elsewhere.
        self.assertIs(node.nodelist, node.second)
        self.assertIs(node.other, node.first)

    def test_a_positional_only_nodelist_is_reachable_but_not_a_kwarg(self):
        # `IfNode.__init__` takes exactly one parameter, `conditions_nodelists`,
        # and holds its branch nodelists inside that list — `nodelist` is a
        # computed property returning `NodeList(self)`. So a branch nodelist
        # cannot be passed by keyword, and the descriptor names it with the empty
        # string. `PairNode.__init__` here accepts only `pairs`, so a materialiser
        # that passed the child anyway would raise `TypeError` rather than pass.
        class PairNode(Node):
            def __init__(self, pairs):
                self.pairs = pairs

        self._register("PairNode", PairNode)

        desc = (
            "PairNode",
            (("pairs", [[None, _native.NodeListRef(0)]]),),
            (("", ([text_desc("a")], False)),),
            None,
        )
        node = materialiser.materialise_node(desc)
        condition, nodelist = node.pairs[0]
        self.assertIsNone(condition)
        self.assertIsInstance(nodelist, NodeList)
        self.assertEqual(len(nodelist), 1)

    def test_an_integer_attribute_is_not_a_nodelist_reference(self):
        # The ambiguity the `NodeListRef` pyclass exists to remove. Both arrive in
        # Python as an `int` in an earlier design, so a materialiser dispatching
        # on `isinstance(value, int)` would resolve an integer attribute against
        # the node's nodelists and hand back the wrong object. The negative half
        # is the half that matters, hence a nodelist sitting right beside it.
        class CountNode(Node):
            def __init__(self, count, nodelist=None):
                self.count = count
                self.nodelist = nodelist

        self._register("CountNode", CountNode)

        desc = (
            "CountNode",
            (("count", 3),),
            (("nodelist", ([text_desc("a")], False)),),
            None,
        )
        node = materialiser.materialise_node(desc)
        self.assertIsInstance(node.count, int)
        self.assertNotIsInstance(node.count, NodeList)
        self.assertEqual(node.count, 3)
        self.assertIsInstance(node.nodelist, NodeList, "the nodelist still arrived")

    def test_a_nodelist_ref_out_of_range_is_loud(self):
        class RefToListNode(Node):
            def __init__(self, child, nodelist=None):
                self.child = child
                self.nodelist = nodelist

        self._register("RefToListNode", RefToListNode)

        desc = (
            "RefToListNode",
            (("child", _native.NodeListRef(4)),),
            (("nodelist", ([], False)),),
            None,
        )
        with self.assertRaises(LookupError) as caught:
            materialiser.materialise_node(desc)
        self.assertIn("out of range", str(caught.exception))

    def test_a_nodelist_ref_without_the_enclosing_node_is_loud(self):
        # `_decode_value` reached directly — a filter argument, say — has no
        # enclosing node and so nothing to resolve against. Loud rather than
        # `None`, which is the shape the `resolve_ref` arm already uses.
        with self.assertRaises(LookupError) as caught:
            materialiser._decode_value(_native.NodeListRef(0))
        self.assertIn("nodelists", str(caught.exception))

    def test_a_list_decodes_its_elements_rather_than_returning_them(self):
        # The Rust side converts the whole tree eagerly, so a `SafeStr` inside a
        # container arrives as a `SafeStr` and still needs `mark_safe`; a list
        # returned as-is would escape the content when the node renders.
        decoded = materialiser._decode_value(
            [_native.SafeStr("<b>"), [1, _native.SafeStr("x")]]
        )
        self.assertIsInstance(decoded[0], SafeString)
        self.assertIsInstance(decoded[1][1], SafeString)

    def test_a_map_decodes_its_values_and_stays_a_dict(self):
        # `AttrValue::Map` becomes a real `dict` on the Rust side, because
        # `token_kwargs` does `kwargs[key] = …` and every consumer wants a
        # mapping. Only the values need decoding here.
        decoded = materialiser._decode_value({"a": _native.SafeStr("<b>"), "b": 2})
        self.assertIsInstance(decoded, dict)
        self.assertIsInstance(decoded["a"], SafeString)
        self.assertEqual(decoded["b"], 2)

    def test_a_variadic_node_is_built_by_a_factory_and_keeps_its_tuple(self):
        """`IfChangedNode` is the one M5 node `cls(**kwargs)` cannot build.

        `IfChangedNode.__init__(self, nodelist_true, nodelist_false, *varlist)`
        (`defaulttags.py:262`) takes its values **positionally**, so there is no
        keyword for them and a descriptor that reached a bare `cls(**kwargs)`
        raised `TypeError: __init__() takes 3 positional arguments but 4 were
        given` — which is why `NODE_CLASSES["IfChangedNode"]` is a factory and
        not the class. Everywhere else the class *is* its own factory.

        The kwarg is `_varlist` because that is Django's own attribute name for
        the tuple (`:265`); the descriptor emits that name and the factory is the
        only place it is unpacked.
        """
        desc = (
            "IfChangedNode",
            (
                (
                    "_varlist",
                    [
                        _native.parse_filter_expression("a"),
                        _native.parse_filter_expression("b"),
                    ],
                ),
            ),
            (
                ("nodelist_true", ([text_desc("x")], False)),
                ("nodelist_false", ([], False)),
            ),
            (1, "ifchanged a b", 1, None),
        )
        node = materialiser.materialise_node(desc, parser=parser())
        self.assertIsInstance(node, IfChangedNode)
        # A tuple, because Django's `*varlist` builds one — a list here would
        # compare unequal against Django's own tree in the parity harness.
        self.assertIsInstance(node._varlist, tuple)
        self.assertEqual([var.token for var in node._varlist], ["a", "b"])
        # The two nodelists are separate named children, and the false one is an
        # empty `NodeList`, not absent.
        self.assertEqual(len(node.nodelist_true), 1)
        self.assertEqual(len(node.nodelist_false), 0)


class ParseToMaterialiseTests(unittest.TestCase):
    """`_native.parse` and the materialiser together, on real M2 templates."""

    def test_text_round_trips(self):
        nodelist = materialiser.materialise(_native.parse("a\nb"))
        self.assertEqual([node.s for node in nodelist], ["a\nb"])

    def test_a_real_tag_produces_its_real_class(self):
        nodelist = materialiser.materialise(_native.parse("{% spaceless %} x {% endspaceless %}"))
        self.assertEqual(len(nodelist), 1)
        node = nodelist[0]
        self.assertIsInstance(node, SpacelessNode)
        self.assertIsInstance(node.nodelist, NodeList)
        self.assertEqual([child.s for child in node.nodelist], [" x "])
        self.assertEqual(node.token.contents, "spaceless")

    def test_verbatim_content_is_safe(self):
        nodelist = materialiser.materialise(_native.parse("{% verbatim %}{{ x }}{% endverbatim %}"))
        self.assertIsInstance(nodelist[0].content, SafeString)
        self.assertEqual(nodelist[0].content, "{{ x }}")

    def test_a_parse_error_carries_a_token(self):
        with self.assertRaises(_native.ParseError) as caught:
            _native.parse("\n{% nope %}")
        self.assertEqual(
            str(caught.exception),
            "Invalid block tag on line 2: 'nope'. "
            "Did you forget to register or load this tag?",
        )
        self.assertEqual(caught.exception.token[2], 2)
        self.assertEqual(caught.exception.token[1], "nope")

    def test_an_unterminated_marker_is_a_stop_iteration(self):
        # Django's own behaviour at this point: `next(bits)` on an exhausted
        # generator, caught by `except Exception` only to attach the token.
        with self.assertRaises(StopIteration) as caught:
            _native.parse('{% now _("unterminated %}')
        self.assertEqual(str(caught.exception), "")
        self.assertEqual(caught.exception.token[2], 1)

    def test_the_wire_form_is_plain_data(self):
        """Nothing Python reaches Rust, and nothing Rust-shaped reaches Python.

        `refs` — the parse's reference table, third element of the result — is a
        `list` rather than a tuple, because the materialiser writes into it
        (`register_ref` is `list.__setitem__`). It is walked like the rest: its
        entries are `None` placeholders until something fills them.
        """
        def walk(value):
            if isinstance(value, (tuple, list)):
                for item in value:
                    walk(item)
                return
            if isinstance(value, (_native.NodeRef, _native.NodeSlot, _native.SafeStr)):
                return
            self.assertIsInstance(value, (str, int, bool, type(None)), f"odd {value!r}")

        walk(_native.parse("{% templatetag openblock %}", debug=True))

    def test_debug_positions_cross_the_wire(self):
        # `Lexer` gives no positions, `DebugLexer` does; the shell must be able
        # to reconstruct both, because M9 exposes the two as distinct classes.
        debug_node = materialiser.materialise(_native.parse("{% debug %}", debug=True))[0]
        self.assertEqual(debug_node.token.position, (0, 11))
        plain_node = materialiser.materialise(_native.parse("{% debug %}"))[0]
        self.assertIsNone(plain_node.token.position)

    # One template per class the M2 emitter can produce. `ResetCycleNode` is
    # deliberately absent because both of its paths raise at M2 —
    # `{% resetcycle %}` wants a prior `{% cycle %}`, and `{% cycle %}` needs
    # `FilterExpression`, so it is M3's. Measured, not assumed: `{% resetcycle %}`
    # reports "No cycles in template." and `{% resetcycle foo %}` reports
    # "Named cycle 'foo' does not exist." Its descriptor shape is pinned by a
    # unit test in `crates/template-core` instead.
    EMITTING_TEMPLATES = {
        "TextNode": "hello",
        "AutoEscapeControlNode": "{% autoescape on %}x{% endautoescape %}",
        "CommentNode": "{% comment %}c{% endcomment %}",
        "CsrfTokenNode": "{% csrf_token %}",
        "DebugNode": "{% debug %}",
        "NowNode": '{% now "Y" %}',
        "SpacelessNode": "{% spaceless %}x{% endspaceless %}",
        "TemplateTagNode": "{% templatetag openblock %}",
        "VerbatimNode": "{% verbatim %}v{% endverbatim %}",
    }

    def test_every_class_the_emitter_produces_is_registered(self):
        """The registry's real obligation, stated as a test.

        Deliberately *not* a count of Django's `Node` subclasses. A
        `Node.__subclasses__()` walk measures import state rather than
        inventory, and it moves as modules load. Measured on the pinned tree in
        a process that had imported only `django.template.base`, then with
        settings configured:

            after importing base only       6
            after django.setup()           42
              + django.templatetags.tz     45
              + django.templatetags.cache  46
              + django.templatetags.l10n   47

        So "34 Node subclasses", a figure this project briefly carried, was an
        artifact of *when* the walk ran rather than a fact about Django. The
        reproducible figures are 32 for `django/template/*.py`, 15 for
        `django/templatetags/*.py`, and 47 for both.

        What the registry owes is narrower and closed: every `class_name` the
        Rust emitter can produce resolves to a node class. That set is ours, it
        does not depend on Django's import graph, and it fails for the right
        reason — a tag whose emitter names a class nobody registered.
        """
        registry = set(materialiser.NODE_CLASSES)

        for class_name, source in self.EMITTING_TEMPLATES.items():
            with self.subTest(class_name=class_name):
                emitted = collect_class_names(_native.parse(source))
                self.assertIn(
                    class_name, emitted, f"{source!r} did not emit {class_name}"
                )
                self.assertLessEqual(
                    emitted,
                    registry,
                    f"{source!r} emits {sorted(emitted - registry)}, "
                    "which the materialiser cannot build",
                )

        # And the same templates must survive the path a real template takes —
        # `materialise` is what raises when the registry falls behind.
        for source in self.EMITTING_TEMPLATES.values():
            with self.subTest(source=source):
                materialiser.materialise(_native.parse(source))


class ExpressionMaterialisationTests(unittest.TestCase):
    """`_materialise_variable` / `_materialise_filter_expression`.

    `Variable` and `FilterExpression` are both built with `__new__` plus slot
    assignment rather than through the class, and the reason is `__init__` — not
    `__slots__`. Measured: `Variable(**{"var": "a.b"})` constructs fine and gives
    `lookups == ("a", "b")`, because `__slots__` restricts which *attributes*
    exist, not how they are set. The constructors are unusable because both take
    the **source string** and re-parse it: `Variable.__init__` re-runs the
    character rules and every branch, and `FilterExpression.__init__` re-runs the
    whole match loop with `find_filter` and `args_check` in it. Calling either
    would have Python redo the parse Rust just finished — discarding Rust's error
    messages and the tokens they carry (§6), and for a `FilterExpression` firing
    the host checks a second time in an order no longer tied to the parse.

    Every assertion below is against Django's *own* construction of the same
    token, so a divergence is a divergence from Django rather than from a
    description of Django.
    """

    TOKENS = ["a.b.c", "x", "5", "1.5", '"quoted"', "'q'", "_(\"x\")", " 5 ", "12_"]

    def test_a_materialised_variable_matches_djangos_own(self):
        for raw in self.TOKENS:
            with self.subTest(raw=raw):
                ours = materialiser._materialise_variable(
                    _native.parse_variable(raw), parser()
                )
                theirs = Variable(raw)
                for slot in (
                    "var",
                    "literal",
                    "lookups",
                    "translate",
                    "message_context",
                ):
                    self.assertEqual(
                        getattr(ours, slot),
                        getattr(theirs, slot),
                        f"{slot} differs for {raw!r}",
                    )
                # `isinstance` is what `FilterExpression.is_var` reads, so it is
                # asserted rather than inferred from the slot values.
                self.assertIsInstance(ours, Variable)

    def test_a_quoted_literal_is_a_safe_string_on_both_sides(self):
        # `SafeString` *is* a `str`, so every equality above passes for a plain
        # `str`; only `isinstance` sees it, and it is user-visible output.
        for raw in ['"<b>"', "'<b>'", '_("<b>")']:
            with self.subTest(raw=raw):
                ours = materialiser._materialise_variable(
                    _native.parse_variable(raw), parser()
                )
                self.assertIsInstance(ours.literal, SafeString)
                self.assertIsInstance(Variable(raw).literal, SafeString)

    def test_the_materialiser_builds_by_new_and_never_by_calling_the_constructor(
        self,
    ):
        """The `__new__` decision, asserted rather than described.

        A subclass of Django's own `Variable` stands in for it: it records
        `__new__` and raises if the constructor is ever *called*. Calling it is
        the mistake this test exists to prevent, and it is invisible to every
        other assertion here — a re-parsed variable has the same slots.

        The spy must be a **class**, not an instance with a `__new__`: an
        instance's `__new__` is only reached through construction, so
        `spy.__new__(spy)` silently resolves to `object.__new__` and the patch
        would prove nothing. Measured, which is why the class is written this
        way.
        """
        real = materialiser.Variable
        made = []

        class Spy(real):
            __slots__ = ()

            def __new__(cls, *args, **kwargs):
                # `Variable.__new__(Variable)`: the materialiser reaches `__new__`
                # through the attribute and passes the class explicitly — which
                # lands in `cls`, because `__new__` is implicitly a staticmethod.
                # So a constructed `Spy(token)` and the materialiser's call are
                # distinguishable: only the first puts `token` in `args`.
                made.append((cls, args, kwargs))
                return object.__new__(cls)

            def __init__(self, *args, **kwargs):
                raise AssertionError(
                    "the constructor was called, so the token was parsed a "
                    "second time in Python and Rust's parse was discarded"
                )

        with unittest.mock.patch.object(materialiser, "Variable", Spy):
            variable = materialiser._materialise_variable(
                _native.parse_variable("a.b.c"), parser()
            )

        self.assertEqual(made, [(Spy, (), {})], "`__new__` must get the class")
        self.assertIsInstance(variable, real)
        self.assertEqual(variable.lookups, ("a", "b", "c"))
        self.assertEqual(variable.var, "a.b.c")

    def test_a_variable_descriptor_decodes_to_a_real_variable(self):
        """`_decode_value`'s `_native.Variable` arm.

        Task 4's plan put this arm in Step 4; it could not be added then because
        `_materialise_variable` is this task's. Nothing in the current emitter
        reaches it — `AttrValue::Variable` is only produced inside a
        `FilterExpression` — so the arm is pinned by calling `_decode_value`
        directly, which is the only way it can be observed at all.
        """
        decoded = materialiser._decode_value(
            _native.parse_variable("a.b"), None, parser()
        )
        self.assertIsInstance(decoded, Variable)
        self.assertEqual((decoded.var, decoded.lookups), ("a.b", ("a", "b")))

    def test_a_materialised_filter_expression_holds_real_filter_functions(self):
        ours = materialiser._materialise_filter_expression(
            _native.parse_filter_expression("x|upper"), parser()
        )
        theirs = FilterExpression("x|upper", parser())
        self.assertEqual(len(ours.filters), 1)
        self.assertIs(
            ours.filters[0][0],
            theirs.filters[0][0],
            "the filter must be Django's function, by identity",
        )
        self.assertTrue(ours.is_var)
        self.assertEqual(ours.token, "x|upper")
        self.assertEqual(ours.var.var, theirs.var.var)

    def test_the_arguments_are_djangos_pairs_with_the_lookup_flag(self):
        raw = 'x|default:"d"|join:y'
        ours = materialiser._materialise_filter_expression(
            _native.parse_filter_expression(raw), parser()
        )
        theirs = FilterExpression(raw, parser())

        (our_func, our_args), (their_func, their_args) = ours.filters[0], theirs.filters[0]
        self.assertEqual(our_func.__name__, their_func.__name__)
        self.assertEqual(
            [lookup for lookup, _value in our_args],
            [lookup for lookup, _value in their_args],
            "the lookup flags must be Django's",
        )
        # A constant argument is the *value*, and a safe one.
        self.assertEqual(our_args[0][1], their_args[0][1])
        self.assertIsInstance(our_args[0][1], SafeString)
        self.assertNotIsInstance(our_args[0][1], Variable)

        # A lookup argument keeps a real `Variable`, which is what lets
        # `resolve` call `arg.resolve(context)` on it. `args` is a *list* of
        # pairs — `filters.append((filter_func, args))` (`base.py:773`) — so the
        # pair is reached through `[0]`.
        self.assertEqual(our_args[0][0], False)
        _func, second_args = ours.filters[1]
        lookup, variable = second_args[0]
        self.assertEqual(lookup, True)
        self.assertIsInstance(variable, Variable)
        self.assertEqual(variable.var, "y")

    def test_a_constant_operand_is_resolved_and_a_translated_one_is_a_proxy(self):
        """The wire's `lookup` flag, and why Rust cannot carry the value.

        Django resolves a constant operand at parse time —
        `Variable(constant).resolve({})` — and for the `_("…")` marker that value
        is `gettext_lazy(msgid)`, a lazy `__proxy__`. Rust cannot construct one,
        so it sends the `Variable` plus the flag and the materialiser resolves.
        Asserted on `_args` because `repr` on a proxy calls `gettext`, which
        needs Django settings configured — and this suite deliberately runs
        without them.
        """
        ours = materialiser._materialise_filter_expression(
            _native.parse_filter_expression('_("Yes")'), parser()
        )
        self.assertIsInstance(ours.var, Promise, "a translated constant is a lazy proxy")
        self.assertNotIsInstance(ours.var, Variable)
        self.assertEqual(ours.var._args, ("Yes",))
        self.assertFalse(ours.is_var)
        self.assertEqual(ours.var._args, FilterExpression('_("Yes")', parser()).var._args)

        plain = materialiser._materialise_filter_expression(
            _native.parse_filter_expression('"x"'), parser()
        )
        self.assertEqual(plain.var, "x")
        self.assertIsInstance(plain.var, SafeString)
        self.assertFalse(plain.is_var)

    def test_args_check_runs_where_the_function_is(self):
        """Review Focus item 2: a Rust-side check cannot reproduce
        `inspect.getfullargspec` plus decorator unwrapping, so the *shell*
        raises, through the real `args_check`.

        Every message is Django's own, from `FilterExpression` on the same token,
        rather than transcribed — the counts are not the obvious ones.
        `args_check` does `plen = len(provided) + 1` (`base.py:822-826`), so
        "provided" **includes the value being filtered**: `x|cut` has one
        provided and needs two, and `x|upper:5` has two and needs one. It is also
        called *inside* the match loop, so the arity error beats the remainder
        error for `x|upper:1:2` — the last row below, where `:2` is left over and
        never reached.
        """
        for token in ("x|cut", "x|cut:'a'", "x|upper:5", 'x|default:"d"'):
            with self.subTest(token=token):
                self.assertEqual(
                    self._error(token),
                    self._django_error(token),
                    "materialisation-time check differs from Django's",
                )
        # Two rows where the arity error must **beat** the remainder error, and
        # `:2` is genuinely left over, so that is only possible with the host
        # inside the parse loop.
        check = materialiser.check_filter(parser())
        for token in ("x|upper:1:2", 'x|upper:1:"s"'):
            with self.subTest(token=token, where="parse"):
                self.assertEqual(self._error(token, check), self._django_error(token))

    def test_an_unknown_filter_is_djangos_own_error(self):
        with self.assertRaises(Exception) as caught:
            materialiser._materialise_filter_expression(
                _native.parse_filter_expression("x|nope"), parser()
            )
        self.assertEqual(str(caught.exception), "Invalid filter: 'nope'")

    def test_the_two_checks_happen_in_djangos_order(self):
        """`find_filter` before `args_check` (`base.py:771-772`).

        Both orders are wrong for at least one row here, so the messages name
        which ran first: `x|nope:1:2` would be an arity error under the wrong
        order, and it is the *more* plausible one, since `:2` is genuinely left
        over — while Django reports the filter name.

        The last row shows the other side of the boundary: `_bad` raises while
        the argument's `Variable` is built (`base.py:768`), which is before
        `find_filter`, so for that row the underscore message wins. Both are
        compared against Django on the same token.
        """
        check = materialiser.check_filter(parser())
        for token in ("x|nope:1:2", "x|nope", "x|nope:_bad", "x|nope:-5", "x|upper:1:2"):
            with self.subTest(token=token):
                self.assertEqual(self._error(token, check), self._django_error(token))

    def test_the_host_is_what_makes_djangos_ordering_possible(self):
        """The same token, with and without the seam — the seam's whole point.

        `x|nope:1:2` has *two* things wrong with it: the filter does not exist,
        and `:2` is left over. Django reports the filter, because `find_filter`
        runs inside the loop and the remainder check runs after it. Without a
        host the parse runs to the end of the loop and reports the remainder
        instead — a different message for the same token, which is why the host
        could not be a batched shell-side pass over the finished parse.
        """
        with self.assertRaises(Exception) as without:
            _native.parse_filter_expression("x|nope:1:2")
        self.assertIn("remainder", str(without.exception))

        with self.assertRaises(Exception) as with_host:
            _native.parse_filter_expression(
                "x|nope:1:2", materialiser.check_filter(parser())
            )
        self.assertEqual(str(with_host.exception), "Invalid filter: 'nope'")

        self.assertEqual(str(with_host.exception), self._django_error("x|nope:1:2"))

    def _error(self, token, check=None):
        """The message for `token`, or `None` if it is accepted.

        `_native.parse_filter_expression` is called with whatever `check` is
        given, so a caller can decide *where* the two filter checks run — at
        parse time (the host) or at materialisation.
        """
        try:
            materialiser._materialise_filter_expression(
                _native.parse_filter_expression(token, check), parser()
            )
        except Exception as error:  # noqa: BLE001 - the message is the comparison
            return str(error)
        return None

    def _django_error(self, token):
        try:
            FilterExpression(token, parser())
        except Exception as error:  # noqa: BLE001
            return str(error)
        return None

    def test_materialising_a_filter_expression_without_a_parser_is_loud(self):
        # A `FilterExpression` holds a Python function, so there is nothing to
        # build without the registry that owns one. Loud rather than a node that
        # renders as the empty string.
        with self.assertRaises(LookupError):
            materialiser._materialise_filter_expression(
                _native.parse_filter_expression("x|upper")
            )

    def test_the_check_filter_callable_raises_djangos_two_errors(self):
        """`check_filter` is what `_native.parse` needs, and it is the *same*
        two calls — so a parse-time check and a materialisation-time check
        cannot drift apart."""
        check = materialiser.check_filter(parser())
        # `upper(value)`: one provided, and that is what it takes.
        check("upper", 0)
        with self.assertRaises(Exception) as caught:
            check("nope", 0)
        self.assertEqual(str(caught.exception), "Invalid filter: 'nope'")
        # `cut(value, arg)`: one provided, one short. The count in the message
        # includes the value, so this is "1 provided" and not "0".
        with self.assertRaises(Exception) as caught:
            check("cut", 0)
        self.assertEqual(str(caught.exception), "cut requires 2 arguments, 1 provided")
        # And the other direction, for a filter that takes none.
        with self.assertRaises(Exception) as caught:
            check("upper", 1)
        self.assertEqual(str(caught.exception), "upper requires 1 arguments, 2 provided")

    def test_materialising_a_filter_expression_does_not_reparse_either(self):
        raw = "x|upper|lower"
        ours = materialiser._materialise_filter_expression(
            _native.parse_filter_expression(raw), parser()
        )
        self.assertEqual(ours.token, raw)
        self.assertEqual(
            [func.__name__ for func, _args in ours.filters], ["upper", "lower"]
        )


class VariableNodeTests(unittest.TestCase):
    """`{{ … }}` end to end: `_native.parse` → registry → real `FilterExpression`."""

    def test_a_variable_node_carries_a_real_filter_expression(self):
        nodelist = materialiser.materialise(_native.parse("a{{ x|upper }}b"), parser=parser())
        self.assertEqual(len(nodelist), 3)
        node = nodelist[1]
        self.assertIsInstance(node, VariableNode)
        self.assertIsInstance(node.filter_expression, FilterExpression)
        self.assertTrue(node.filter_expression.is_var)
        self.assertEqual(node.filter_expression.token, "x|upper")
        # And the filter is Django's own function, so the node can render.
        func, args = node.filter_expression.filters[0]
        self.assertEqual(func.__name__, "upper")
        self.assertEqual(args, [])
        self.assertEqual(node.token.contents, "x|upper")

    def test_a_variable_node_matches_djangos_own_construction(self):
        source = 'a{{ "x"|default:"d" }}b'
        ours = materialiser.materialise(_native.parse(source), parser=parser())[1]

        engine = Engine()
        django_parser = Parser(
            Lexer(source).tokenize(),
            engine.template_libraries,
            engine.template_builtins,
        )
        theirs = next(
            node for node in django_parser.parse() if isinstance(node, VariableNode)
        )

        self.assertEqual(ours.filter_expression.token, theirs.filter_expression.token)
        self.assertEqual(ours.filter_expression.is_var, theirs.filter_expression.is_var)
        self.assertEqual(ours.filter_expression.var, theirs.filter_expression.var)
        self.assertEqual(
            [func.__name__ for func, _args in ours.filter_expression.filters],
            [func.__name__ for func, _args in theirs.filter_expression.filters],
        )
        self.assertEqual(ours.filter_expression.filters[0][1][0][1], "d")

    def test_a_filter_expression_attribute_is_materialised_not_passed_through(self):
        """The `_native.FilterExpr` arm must precede the sequence arms.

        A descriptor that reached `isinstance(value, list)` first — or that had no
        arm at all — would hand the node a wire object, and nothing would report
        it: the object has the same three attribute *names*.
        """
        decoded = materialiser._decode_value(
            _native.parse_filter_expression("x|upper"), None, parser()
        )
        self.assertIsInstance(decoded, FilterExpression)
        self.assertNotIsInstance(decoded, _native.FilterExpr)


_CFG_TEST = "#[cfg(test)]"
_TAG_MODULES = PROJECT_ROOT / "crates" / "template-core" / "src" / "parser" / "tags"

# `NodeDesc::new("X")` / `NodeDesc::with_nodelist("X", ...)`. `\s*` spans the
# newline in `tags/mod.rs:182`, where the class name starts on the following line.
_CLASS = re.compile(r'NodeDesc::(?:new|with_nodelist)\s*\(\s*"([A-Za-z_][A-Za-z0-9_]*)"')
_TEXT = re.compile(r"NodeDesc::text\s*\(")

# Emitted by the parse core rather than by a tag module: `TextNode` by
# `desc.rs::NodeDesc::text` and `VariableNode` by `parser/mod.rs:186`.
CORE_EMITTED_CLASSES = {"TextNode", "VariableNode"}

# Names that exist **only** under `#[cfg(test)]`. The stripper below is asserted
# to remove them, which is the only way to know it removed the right region:
# a stripper that silently cut real code would fail the second assertion instead,
# so the pair brackets it from both sides.
TEST_ONLY_CLASSES = {"StandinBlockNode", "StandinFirstNode", "StandinRefNode", "StandinHostNode"}


def strip_cfg_test(source):
    """`source` with every `#[cfg(test)]` item removed.

    A brace-matching scan and not a split on the first marker. The markers are
    interleaved with production code — `tags/mod.rs` puts stand-ins under
    `#[cfg(test)]` from `:207` onward while real helper code follows at `:48-59`
    and beyond — so a prefix cut would drop a region silently, and a silently
    dropped region is a class name this check cannot see. That is the failure
    direction worth engineering against; the other direction is covered by
    `test_every_node_class_is_emitted_somewhere`.
    """
    lines = source.splitlines()
    kept: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() != _CFG_TEST:
            kept.append(lines[index])
            index += 1
            continue
        index += 1  # the attribute itself
        depth = 0
        saw_brace = False
        while index < len(lines):
            line = lines[index]
            index += 1
            depth += line.count("{") - line.count("}")
            saw_brace = saw_brace or "{" in line
            if saw_brace and depth <= 0:
                break  # a block item's closing brace
            if not saw_brace and line.rstrip().endswith(";"):
                break  # `use …;`, `pub(crate) use …;` and the like
    return "\n".join(kept)


def tag_emitted_class_names():
    """Every node class name a tag module's *production* code can emit.

    Static, so it sees a name no corpus row happens to exercise — which is the
    whole point: the existing runtime guard is `materialise_node`'s `LookupError`,
    and it can only fire for a node the corpus actually builds.
    """
    names = set()
    for path in sorted(_TAG_MODULES.glob("*.rs")):
        body = strip_cfg_test(path.read_text(encoding="utf-8"))
        names.update(_CLASS.findall(body))
        if _TEXT.search(body):
            names.add("TextNode")
    return names


class StaticEmitterRegistryTests(unittest.TestCase):
    """`NODE_CLASSES` against the class names the Rust emitters name.

    The runtime guard is a `LookupError` from `materialise_node`, which fires
    only for a node the corpus builds. A tag whose emitter names a class nobody
    registered, on a path no corpus row reaches, is invisible to it — and the
    class name is a string literal in the Rust source, so a typo is statically
    visible and the corpus cannot hide it.
    """

    def test_the_stripper_removes_only_test_code(self):
        removed = {
            name
            for name in TEST_ONLY_CLASSES
            if name not in tag_emitted_class_names()
        }
        self.assertEqual(
            removed,
            TEST_ONLY_CLASSES,
            f"test-only names survived the strip: {sorted(TEST_ONLY_CLASSES - removed)}",
        )

    def test_every_tag_emitted_class_is_materialisable(self):
        missing = tag_emitted_class_names() - set(materialiser.NODE_CLASSES)
        self.assertEqual(missing, set(), f"NODE_CLASSES is missing {sorted(missing)}")

    def test_every_node_class_is_emitted_somewhere(self):
        dead = (
            set(materialiser.NODE_CLASSES)
            - tag_emitted_class_names()
            - CORE_EMITTED_CLASSES
        )
        self.assertEqual(dead, set(), f"unreachable NODE_CLASSES entries: {sorted(dead)}")


if __name__ == "__main__":
    unittest.main()
