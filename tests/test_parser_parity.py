"""M2's acceptance test: our parse tree is Django's parse tree.

Both sides are materialised into real Django nodes and compared structurally.
The target is Django's own `Parser` imported from `.django/` with no shim in the
path; `test_the_comparison_target_is_genuinely_djangos_parser` keeps it that way.

Two independent traversals are compared for every node: one reading `vars()`
directly (which catches a wrong attribute *name*), and one through
`get_nodes_by_type` (which walks `child_nodelists` — the same path Django's
rendering and `ExtendsNode.__init__` use, and the one that catches children
attached under a name nothing walks).

Two things here are not the obvious spelling and both were measured against the
pinned tree rather than assumed:

* the materialiser is loaded **from its file**, not imported as
  `django_rust_templates._impl.parser`. See the note below.
* Django's parser is given `builtins=Engine().template_builtins`. A bare
  `Parser(tokens)` registers *nothing* (`base.py:518-519`: `builtins=None`
  becomes `[]`), so it raises `Invalid block tag` on `{% csrf_token %}` and
  every other built-in — which would have made the whole corpus compare
  `ours=ok` against `theirs=error`. `Engine.template_builtins` is an *instance*
  attribute (`engine.py:63`), not a class attribute, and
  `get_template_builtins` is an instance method; `Engine().template_builtins`
  is the form that resolves. This is the same trap Task 6 hit.
"""

import importlib.util
import inspect
import random
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template.base import (  # noqa: E402
    DebugLexer,
    Lexer,
    Node,
    NodeList,
    Parser,
)
from django.template.engine import Engine  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from tests.parser_corpus import (  # noqa: E402
    RANDOM_CASES,
    RANDOM_SEED,
    TEMPLATES,
    random_template,
)

# Loaded from its file rather than imported, for the reason spelled out in
# `tests/test_materialise.py`: `_impl/__init__.py` is the shim's
# `django.template` and imports its own siblings (`from .engine import Engine`
# …), so importing the `_impl` *package* by its ordinary name executes that file
# and fails for want of `engine`. Loading the module directly is how this test
# reaches the materialiser without installing the shim, which is what keeps the
# node classes Django's own.
_MATERIALISER_SPEC = importlib.util.spec_from_file_location(
    "django_rust_templates._impl.parser",
    PROJECT_ROOT / "python" / "django_rust_templates" / "_impl" / "parser.py",
)
materialise_module = importlib.util.module_from_spec(_MATERIALISER_SPEC)
sys.modules[_MATERIALISER_SPEC.name] = materialise_module
_MATERIALISER_SPEC.loader.exec_module(materialise_module)
materialise = materialise_module.materialise

# Kept for symmetry with Task 6's module, where the same tuple describes a wire
# token from either side.
IGNORED_ATTRIBUTES = ("token", "origin")


def django_parse(source, debug=False):
    lexer = DebugLexer(source) if debug else Lexer(source)
    # `builtins` is not optional: a bare `Parser` knows no tags at all.
    return Parser(lexer.tokenize(), builtins=Engine().template_builtins).parse()


def ours_parse(source, debug=False):
    return materialise(_native.parse(source, debug))


def token_shape(token):
    if token is None:
        return None
    if isinstance(token, tuple):
        return token
    return (token.token_type.value, token.contents, token.lineno, token.position)


def describe(value):
    """A comparable description of a node tree, without node identity.

    `token` and `origin` are pulled out to their own key rather than dropped, so
    a node whose token is missing is distinguishable from one whose token differs.
    """
    if isinstance(value, NodeList):
        return {
            "type": "NodeList",
            "contains_nontext": value.contains_nontext,
            "nodes": [describe(node) for node in value],
        }
    if isinstance(value, Node):
        attributes = vars(value)
        return {
            "type": type(value).__name__,
            "attrs": {
                key: describe(item)
                for key, item in sorted(attributes.items())
                if key not in IGNORED_ATTRIBUTES
                and not isinstance(item, (Node, NodeList))
            },
            "nodelists": {
                key: describe(item)
                for key, item in sorted(attributes.items())
                if isinstance(item, (Node, NodeList))
            },
            "token": token_shape(getattr(value, "token", None)),
        }
    if isinstance(value, list):
        return [describe(item) for item in value]
    return value


def walked_subtree(node):
    """Type names of a node's subtree, reached through `child_nodelists`.

    The second traversal. `describe` reads the attributes it is given; this asks
    the node itself, which is what Django's renderer does.
    """
    return [type(child).__name__ for child in node.get_nodes_by_type(Node)]


def fingerprint(nodelist):
    return {
        "tree": describe(nodelist),
        "subtrees": [walked_subtree(node) for node in nodelist],
    }


def outcome(parse, source, debug):
    """(kind, payload) for either implementation, so failures compare too."""
    try:
        return ("ok", fingerprint(parse(source, debug)))
    except Exception as exc:  # noqa: BLE001  (StopIteration is deliberate)
        return ("error", str(exc), token_shape(getattr(exc, "token", None)))


class CorpusParityTests(unittest.TestCase):
    def assert_trees_match(self, source, debug):
        theirs = django_parse(source, debug)
        ours = ours_parse(source, debug)
        self.assertEqual(
            describe(ours),
            describe(theirs),
            f"tree differs for {source!r} (debug={debug})",
        )
        self.assertEqual(
            [walked_subtree(node) for node in ours],
            [walked_subtree(node) for node in theirs],
            f"subtree walk differs for {source!r} (debug={debug})",
        )

    def test_corpus_matches_without_debug_positions(self):
        for source in TEMPLATES:
            with self.subTest(source=source):
                self.assert_trees_match(source, debug=False)

    def test_corpus_matches_with_debug_positions(self):
        for source in TEMPLATES:
            with self.subTest(source=source):
                self.assert_trees_match(source, debug=True)

    def test_the_corpus_is_not_trivially_small(self):
        self.assertGreaterEqual(len(TEMPLATES), 40)
        self.assertGreaterEqual(len(set(TEMPLATES)), 40, "duplicate corpus entries")

    def test_the_corpus_covers_every_tag_with_a_success_path(self):
        joined = "\n".join(TEMPLATES)
        for tag in [
            "autoescape",
            "comment",
            "csrf_token",
            "debug",
            "now",
            "spaceless",
            "templatetag",
            "verbatim",
        ]:
            self.assertIn(f"{{% {tag}", joined, f"{tag} is not in the corpus")
        # `resetcycle` is absent on purpose: both of its paths are errors at M2,
        # so it belongs to Task 6's corpus and has no success tree to compare.
        # Its descriptor shape is pinned by a unit test in Task 5 instead.


class RandomisedParityTests(unittest.TestCase):
    def test_randomised_templates_match(self):
        rng = random.Random(RANDOM_SEED)
        outcomes = {"ok": 0, "error": 0}

        for index in range(RANDOM_CASES):
            source = random_template(rng)
            for debug in (False, True):
                with self.subTest(case=index, source=source, debug=debug):
                    theirs = outcome(django_parse, source, debug)
                    ours = outcome(ours_parse, source, debug)
                    self.assertEqual(ours, theirs, f"outcome differs for {source!r}")
                    outcomes[theirs[0]] += 1

        # A generator that only ever produced failures would compare error
        # messages and never a tree, so pin that most cases parse.
        self.assertGreater(
            outcomes["ok"],
            RANDOM_CASES,
            f"the generator barely produced successful parses: {outcomes}",
        )

    def test_the_generator_reaches_the_splitting_path(self):
        """The randomised pass must exercise `smart_split`, not only text nodes.

        `RANDOM_ALPHABET` carries U+001C..U+001F because Python's `str.isspace()`
        accepts them and Rust's `char::is_whitespace()` does not — the M1
        divergence. That table is read in `smart_split`, so separators inside a
        *text node* prove nothing; they have to land in a tag argument. Measured
        with the current seed: 400 templates, 196 containing an
        `{% autoescape %}` tag, 307 arguments, 105 of them carrying a
        Python-only separator, 71 a quote, 21 empty.

        There is deliberately **no** floor on the raw argument *count*. An
        earlier version asserted `len(arguments) > 100` and it was false: the
        regex matches the fixed `{% autoescape on %}` block tags as well, so the
        number passed even after the argument path was deleted. A floor that
        survives deletion of the thing it floors reads as coverage and provides
        none. The two floors below measure what they name, because `on` and
        `off` carry neither a separator nor a quote.

        The numbers above are the evidence for those floors' headroom. Changing
        the seed invalidates them: re-run the measurement rather than assume it.
        """
        rng = random.Random(RANDOM_SEED)
        arguments = []
        for _ in range(RANDOM_CASES):
            source = random_template(rng)
            for match in re.finditer(r"\{% autoescape (.*?) %\}", source, re.S):
                arguments.append(match.group(1))

        self.assertGreater(
            sum(1 for a in arguments if any(c in a for c in "\u001c\u001d\u001e\u001f")),
            10,
            "no argument carried a Python-only separator, so the M1 divergence "
            "is not being exercised where it is read",
        )
        self.assertGreater(
            sum(1 for a in arguments if "'" in a or '"' in a),
            5,
            "no argument carried a quote, so `smart_split`'s quoting arm is idle",
        )


class ShapeTests(unittest.TestCase):
    """Claims about the tree that a diff against Django would not isolate."""

    def test_text_nodes_are_real_text_nodes(self):
        # Django's `extend_nodelist` and `ExtendsNode.render` both isinstance-test
        # TextNode (Review Focus item 7), so the materialiser must not produce a
        # generic node for text — and it must not produce a TextNode for anything
        # else either, since `ExtendsNode.render` skips TextNode children.
        from django.template.base import TextNode

        nodes = ours_parse("a{% csrf_token %}b")
        self.assertEqual([type(node).__name__ for node in nodes],
                         ["TextNode", "CsrfTokenNode", "TextNode"])
        self.assertIsInstance(nodes[0], TextNode)
        self.assertNotIsInstance(nodes[1], TextNode)
        self.assertEqual(nodes[2].s, "b")

    def test_every_node_carries_a_real_token_and_an_origin(self):
        for node in ours_parse("a{% spaceless %}b{% endspaceless %}{% debug %}"):
            self.assertIsNotNone(node.token, f"{type(node).__name__} has no token")
            self.assertTrue(hasattr(node, "origin"))

    def test_verbatim_content_is_safe_and_rendered_at_parse_time(self):
        from django.utils.safestring import SafeString

        node = ours_parse("{% verbatim %}{{ x }}{% endverbatim %}")[0]
        self.assertIsInstance(node.content, SafeString)
        self.assertEqual(node.content, "{{ x }}")
        self.assertEqual(
            node.content,
            django_parse("{% verbatim %}{{ x }}{% endverbatim %}")[0].content,
        )

    def test_contains_nontext_is_per_nodelist(self):
        ours = ours_parse(
            "{% autoescape on %}{% csrf_token %}{% endautoescape %}"
            "{% autoescape on %}text{% endautoescape %}"
        )
        theirs = django_parse(
            "{% autoescape on %}{% csrf_token %}{% endautoescape %}"
            "{% autoescape on %}text{% endautoescape %}"
        )
        for index in (0, 1):
            self.assertEqual(
                ours[index].nodelist.contains_nontext,
                theirs[index].nodelist.contains_nontext,
                f"body {index}",
            )
        self.assertTrue(ours[0].nodelist.contains_nontext)
        self.assertFalse(ours[1].nodelist.contains_nontext)

    def test_a_variable_is_reported_rather_than_dropped(self):
        # The M3 gap, asserted from the shell's side: a variable must be loud,
        # because a silently-empty node would render wrong output rather than
        # fail.
        with self.assertRaises(_native.ParseError) as caught:
            _native.parse("a{{ x }}b")
        self.assertIn("M3", str(caught.exception))
        self.assertEqual(caught.exception.token[2], 1)

    def test_an_unimplemented_tag_is_its_own_kind_at_the_ffi(self):
        """`Unimplemented` is not just a `ParseError` with a different message.

        Django compiles `{% for %}` successfully, so our refusing it is a fact
        about *this engine*. A harness that compared messages would match our
        text against Django's "Did you forget to register or load this tag?" and
        score the one case where we are wrong as a pass. The class carries it
        instead — and as a subclass, so the shell's single `except ParseError`
        still converts it to `TemplateSyntaxError` exactly as before.
        """
        with self.assertRaises(_native.UnimplementedTag) as caught:
            _native.parse("{% for x in y %}{% endfor %}")
        self.assertIsInstance(caught.exception, _native.ParseError)
        self.assertEqual(caught.exception.token[1], "for x in y")

        # The classes are genuinely different, and a syntax error is not
        # silently swept into the "not built yet" bucket.
        self.assertNotEqual(_native.UnimplementedTag, _native.ParseError)
        self.assertTrue(issubclass(_native.UnimplementedTag, _native.ParseError))
        with self.assertRaises(_native.ParseError) as syntax:
            _native.parse("{% endfor %}")
        self.assertNotIsInstance(syntax.exception, _native.UnimplementedTag)

    def test_the_comparison_target_is_genuinely_djangos_parser(self):
        from django_rust_templates import _shim

        source = inspect.getsourcefile(Parser)
        self.assertNotIn("_impl", source)
        self.assertTrue(Path(source).resolve().is_relative_to(DJANGO_CHECKOUT.resolve()))
        self.assertFalse(_shim.installed(), "the shim is installed in this process")


if __name__ == "__main__":
    unittest.main()
