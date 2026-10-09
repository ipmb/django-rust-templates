"""The per-parse reference table — the one side collection two tags share.

Two built-ins need a value built outside ``template-core``, and they need it at
two different *times*, which is why the table has two fillers:

* ``{% if %}`` — an ``AttrValue::Ref``, filled **at parse time by the host**:
  Django builds the ``FilterExpression`` while compiling the tag and raises
  there on a bad one (``TemplateIfParser.__init__`` → ``create_var`` →
  ``compile_filter``).
* ``{% cycle %}`` — a node slot, filled **at materialisation time by the
  materialiser**: the identity that must survive is a *materialised Python
  object*. ``{% cycle a b as c %}`` returns the node **and** stores that same
  object in ``parser._named_cycle_nodes`` (``defaulttags.py:693-696``), so a
  later ``{% cycle c %}`` returns it again (``:669-671``) and
  ``{% resetcycle c %}`` resets it (``:1439``).

Identity is a render-time invariant, not a tree-shape one: ``CycleNode.render``
keys ``context.render_context`` by the node object (``defaulttags.py:106-112``),
so two equal-but-distinct nodes get separate ``itertools.cycle`` iterators and
the second mention restarts the sequence instead of continuing it.

Measured on the pinned tree, which is what makes identity observable rather than
theoretical::

    Engine().from_string("{% cycle 'a' 'b' as c %}{% cycle c %}{% resetcycle c %}")
        .nodelist
    → [CycleNode, CycleNode, ResetCycleNode]
    → nodelist[0] is nodelist[1]            → True
    → [n.token.contents for n in …]         → ['cycle c', 'cycle c', 'resetcycle c']
    → nodelist[2].node is nodelist[0]       → True

The second line is the detail a "return the existing object and stop" shortcut
gets wrong: ``extend_nodelist`` sets ``node.token = token`` at **every** append
(``base.py:598``), so the shared object ends up carrying the *last* occurrence's
token and every position reads that one. The test below pins it, because the
shortcut passes without it.

**What this file cannot reach yet, and why that is narrower than it sounds.**
``{% cycle %}`` is still a stub — this plan's Task 6 writes its compile function
— so no *parsed template* here can produce a slot. These tests therefore drive
the table with hand-built descriptors, and ``CycleNode`` is patched into
``NODE_CLASSES`` in ``setUp`` (Task 6 registers it for real; until ``cycle.rs``
names it, the static emitter gate in ``tests/test_materialise.py`` would call a
registered-but-unemitted class dead). What is left for Task 6 is narrow and
stated: ``cycle.rs`` must write the descriptors this file already materialises,
and add the corpus row.

The one thing that *is* reached through the real pipeline here is the wire
shape — ``_native.parse`` returning the table, and a node descriptor carrying
its slot as a fifth element.
"""

# Task 6 landed `cycle.rs` and registered `CycleNode` in `NODE_CLASSES`, so the
# hand-built descriptors above are now checked against what the tag really
# writes: `ParsedCycleTests` parses the sources end to end. The module docstring
# above is kept in its original tense because it records *why* the hand-built
# half exists, and those tests still carry the empty-slot and
# token-overwriting cases a parsed template cannot produce.

import importlib.util
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template.base import Parser  # noqa: E402
from django.template.defaulttags import CycleNode, ResetCycleNode  # noqa: E402
from django.template.engine import Engine  # noqa: E402

from django_rust_templates import _native  # noqa: E402

_MATERIALISER_SPEC = importlib.util.spec_from_file_location(
    "django_rust_templates._impl.parser",
    PROJECT_ROOT / "python" / "django_rust_templates" / "_impl" / "parser.py",
)
materialiser = importlib.util.module_from_spec(_MATERIALISER_SPEC)
sys.modules[_MATERIALISER_SPEC.name] = materialiser
_MATERIALISER_SPEC.loader.exec_module(materialiser)


def parser():
    """A `Parser` with Django's real filter and tag registries.

    A bare `Parser(tokens)` registers nothing — `builtins=None` becomes `[]`
    (`base.py:518-519`) — so `find_filter` would raise for every name. Same
    helper, and same reason, as `tests/test_materialise.py`.
    """
    engine = Engine()
    return Parser([], engine.template_libraries, engine.template_builtins)


def cycle_definition(slot, name="c", token=None):
    """The descriptor `cycle.rs` writes for `{% cycle … as name %}`.

    `cyclevars` is `[]` rather than real `FilterExpression`s: this file is about
    the table, and building a `FilterExpr` would need a parser and a filter
    lookup that the rule under test never reads. The parameter names are
    `CycleNode.__init__`'s own (`defaulttags.py:97`), not a paraphrase.
    """
    return (
        "CycleNode",
        (("cyclevars", []), ("variable_name", name), ("silent", False)),
        (),
        token,
        _native.NodeSlot(slot),
    )


def cycle_reference(slot, token=None):
    """The descriptor for a second `{% cycle name %}` — that same node again.

    No constructor arguments at all: the node already exists, so the materialiser
    never reaches `cls(**kwargs)` here. `CycleNode.__init__` takes no slot
    parameter, which is why the slot is a property of the descriptor rather than
    an `attrs` entry.
    """
    return ("CycleNode", (), (), token, _native.NodeSlot(slot))


def resetcycle(slot):
    """The descriptor for `{% resetcycle name %}`.

    A *different* node class at a position that is not a pointer — so here the
    slot is an attribute **value** (`AttrValue::NodeSlot`), not the descriptor's
    own slot, and `ResetCycleNode.node` is Django's parameter name
    (`defaulttags.py:439`).
    """
    return ("ResetCycleNode", (("node", _native.NodeSlot(slot)),), (), None)


def materialise(nodes, slots):
    """Materialise `nodes` against a table of `slots` reserved entries.

    `slots` is what the shell's `Host::alloc_slot` does — reserve the index
    *before* anything can be stored at it. That is why the table arrives
    pre-sized and `register_ref` is `list.__setitem__` rather than `append`, and
    why the materialiser's rule reads "already filled" off a `None` placeholder.
    """
    refs = [None] * slots
    nodelist = materialiser.materialise(
        (nodes, False, refs),
        resolve_ref=refs.__getitem__,
        register_ref=refs.__setitem__,
        parser=parser(),
    )
    return nodelist, refs


class WireShapeTests(unittest.TestCase):
    """`_native.parse` returns `(nodes, contains_nontext, refs)`."""

    def test_a_parse_returns_the_table_it_allocated(self):
        nodes, contains_nontext, refs = _native.parse("ab")
        self.assertEqual(len(nodes), 1)
        self.assertFalse(contains_nontext)
        self.assertEqual(list(refs), [], "no producer yet, so no reserved slots")

    def test_each_parse_gets_its_own_table(self):
        first = _native.parse("ab")[2]
        second = _native.parse("ab")[2]
        self.assertIsNot(first, second, "the table is per-parse, not per-process")

    def test_a_node_descriptor_carries_its_slot_as_a_fifth_element(self):
        # The shape every reader of a descriptor depends on, asserted rather
        # than described: `materialise_node`'s tolerant unpack, `describe`'s
        # walker and `test_parser_parity`'s descriptor assertions all read it.
        nodes = _native.parse("ab")[0]
        self.assertEqual(len(nodes[0]), 5)
        class_name, attrs, nodelists, token, slot = nodes[0]
        self.assertEqual(class_name, "TextNode")
        self.assertIsNone(slot, "a node that is not a slot reports None, not 0")

    def test_the_table_can_be_used_without_being_passed_twice(self):
        # `refs` rides in the parse result, so `materialise` derives the two
        # callbacks from it. That is what keeps every existing
        # `materialise(_native.parse(source), parser=…)` call site working.
        nodelist = materialiser.materialise(_native.parse("ab"), parser=parser())
        self.assertEqual(nodelist[0].s, "ab")


class CycleIdentityTests(unittest.TestCase):
    """The slot's rule: if the table holds it, hand back what is there."""

    def test_a_reused_named_cycle_materialises_one_object(self):
        nodelist, refs = materialise(
            [cycle_definition(0), cycle_reference(0)], slots=1
        )
        self.assertIsInstance(nodelist[0], CycleNode)
        self.assertEqual(nodelist[0].variable_name, "c")
        self.assertIs(
            nodelist[0], nodelist[1], "the second {% cycle c %} is the first's node"
        )
        self.assertIs(refs[0], nodelist[0], "the slot holds the object it built")

    def test_the_shared_object_carries_the_last_occurrences_token(self):
        # `extend_nodelist` assigns `node.token` on every append (`base.py:598`),
        # so the second occurrence overwrites the first's token on the object
        # they share — and both positions then read the second's. Measured above.
        first_token = (0, "cycle 'a' 'b' as c", 1, None)
        second_token = (0, "cycle c", 1, None)
        nodelist, _ = materialise(
            [cycle_definition(0, token=first_token), cycle_reference(0, token=second_token)],
            slots=1,
        )
        self.assertEqual(nodelist[0].token.contents, "cycle c")
        self.assertEqual(nodelist[1].token.contents, "cycle c")

    def test_resetcycle_targets_the_cycles_node(self):
        nodelist, _ = materialise([cycle_definition(0), resetcycle(0)], slots=1)
        self.assertIsInstance(nodelist[1], ResetCycleNode)
        self.assertIsNot(nodelist[1], nodelist[0], "resetcycle is its own node")
        self.assertIs(nodelist[1].node, nodelist[0])

    def test_two_definitions_are_two_nodes_in_two_slots(self):
        # `{% cycle a b as c %}{% cycle d e as f %}`: Django rebinds the name
        # rather than reusing the object (`defaulttags.py:693-696`), so the two
        # definitions must not collide on one slot.
        nodelist, refs = materialise(
            [cycle_definition(0, "c"), cycle_definition(1, "f")], slots=2
        )
        self.assertIsNot(nodelist[0], nodelist[1])
        self.assertEqual(nodelist[0].variable_name, "c")
        self.assertEqual(nodelist[1].variable_name, "f")
        self.assertIs(refs[0], nodelist[0])
        self.assertIs(refs[1], nodelist[1])

    def test_an_empty_slot_in_an_attribute_is_loud(self):
        # `{% resetcycle c %}` with no `{% cycle … as c %}`: Django raises at
        # compile time (`defaulttags.py:1440-1443`), so this cannot come from a
        # template — but a wire bug that reserved a slot and never filled it must
        # not reach `ResetCycleNode.node` as a silent `None`.
        with self.assertRaises(LookupError) as caught:
            materialise([resetcycle(0)], slots=1)
        self.assertIn("slot", str(caught.exception))


class ParsedCycleTests(unittest.TestCase):
    """Task 6's end-to-end half: `cycle.rs` writes the descriptors above.

    Every source here goes through `_native.parse` and the real materialiser, so
    a slot that `cycle.rs` reserved but filled wrongly shows up as a wrong
    *object*, not a wrong descriptor.
    """

    def ours(self, source):
        return materialiser.materialise(_native.parse(source), parser=parser())

    def theirs(self, source):
        return Engine().from_string(source).nodelist

    def test_a_named_cycle_and_its_mentions_are_one_object(self):
        source = "{% cycle 'a' 'b' as c %}{% cycle c %}{% resetcycle c %}"
        ours = self.ours(source)
        theirs = self.theirs(source)

        self.assertIs(ours[0], ours[1], "the mention returns the definition's node")
        self.assertIs(ours[2].node, ours[0], "resetcycle targets that same object")

        # And the identity Django produces is the identity we produce — the
        # claim the hand-built tests above cannot make, because they choose the
        # slots themselves.
        self.assertIs(theirs[0], theirs[1])
        self.assertIs(theirs[2].node, theirs[0])
        self.assertEqual(
            [n.variable_name for n in ours[:2]], [n.variable_name for n in theirs[:2]]
        )

    def test_a_mention_does_not_retarget_bare_resetcycle(self):
        # `cycle.rs` returns early for the `len(args) == 2` form, so
        # `last_cycle_node` still points at the last *definition*. Measured on
        # the pinned tree: this template's `{% resetcycle %}` targets `f`.
        source = "{% cycle a b as c %}{% cycle d e as f %}{% cycle c %}{% resetcycle %}"
        ours = self.ours(source)
        theirs = self.theirs(source)
        self.assertIsNot(ours[0], ours[1])
        self.assertIs(ours[2], ours[0])
        self.assertIs(ours[3].node, ours[1], "targets f, the last definition")
        self.assertEqual(ours[3].node.variable_name, theirs[3].node.variable_name)

    def test_the_two_anonymous_forms_are_their_own_nodes(self):
        source = "{% cycle 'a' 'b' %}{% cycle 'c' 'd' %}"
        ours = self.ours(source)
        self.assertIsNot(ours[0], ours[1])
        self.assertIsNone(ours[0].variable_name)
        self.assertFalse(ours[1].silent)
        self.assertEqual(
            [expression.token for expression in ours[1].cyclevars],
            [expression.token for expression in self.theirs(source)[1].cyclevars],
        )

    def test_silent_is_read_off_the_parsed_node(self):
        ours = self.ours("{% cycle 'a' 'b' as c silent %}")
        self.assertTrue(ours[0].silent)
        self.assertEqual(ours[0].variable_name, "c")

    def test_a_cycle_inside_a_loop_is_a_child_node(self):
        source = "{% for x in y %}{% cycle 'a' 'b' %}{% endfor %}"
        ours = self.ours(source)
        theirs = self.theirs(source)
        self.assertIsInstance(ours[0].nodelist_loop[0], CycleNode)
        self.assertEqual(
            type(ours[0].nodelist_loop[0]).__name__,
            type(theirs[0].nodelist_loop[0]).__name__,
        )


if __name__ == "__main__":
    unittest.main()
