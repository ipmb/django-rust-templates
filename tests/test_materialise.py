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
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template.base import (  # noqa: E402
    Node,
    NodeList,
    TextNode,
    Token,
    TokenType,
)
from django.template.defaulttags import (  # noqa: E402
    AutoEscapeControlNode,
    SpacelessNode,
    VerbatimNode,
)
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


def text_desc(contents, token=None):
    return ("TextNode", (("s", contents),), (), token)


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
        """Nothing Python reaches Rust, and nothing Rust-shaped reaches Python."""
        def walk(value):
            if isinstance(value, tuple):
                for item in value:
                    walk(item)
                return
            if isinstance(value, (_native.NodeRef, _native.SafeStr)):
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


if __name__ == "__main__":
    unittest.main()
