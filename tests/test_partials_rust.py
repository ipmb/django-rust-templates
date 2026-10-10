"""The partial pair's seam, from the Python side.

The Rust rows in `crates/template-core/src/parser/tags/partial*.rs` assert what
the *tag* passes across the seam. These assert what the shell does with it —
which is where the two behaviours the acceptance corpus cannot see live: the
mapping is **live** (looked up at render, so source order does not matter), and
the body's nodelist is **one object** shared by the `PartialTemplate` and the
`PartialDefNode`.

Task 2's rows call `partial_node` directly, because it takes only a name. The
rows that need a `NodeListDesc` — `register_partial_def`'s argument — could not
be written then: there was no public Python route to one, so a row would have
had to fabricate it rather than observe what Rust produced. **Task 4 is where
they arrive**, as `PartialDefSeamTests` below: `_native.parse` drives both
methods for real, and the four rows observe the host rather than a rendered
string.

Task 3's rows drive `{% partial %}` through `render.render_string`, which is the
first thing in this file to exercise the whole route — Rust's registry dispatching
to `partial.rs`, the tag calling `Host::partial_node`, and the shell building the
`PartialNode`. What they assert is the **move** of a failure (from compile-time
`UnimplementedTag` to render-time `TemplateSyntaxError`) plus the class the shell
hands back, neither of which the Rust rows — which parse under a `PartialHost`
and never materialise — can see.

**Task 5's rows are `ReferenceTableTests`, and they landed inside Task 4's
commit** because Task 4 is what needs them: `{% partialdef %}` materialises its
body *during* the parse, which is the first time anything asks the reference
table mid-parse. The plan had this task conditional on its identity row failing;
the condition was answered by the acceptance suite instead of by the row, and the
answer was "the table is load-bearing, not merely tidy" — see
`FilterCheckHost::refs` in `crates/template-py/src/lib.rs`. The rows here are
what pins the change.
"""

import sys
import unittest
from pathlib import Path

from django_rust_templates.engine import Engine
from django_rust_templates.engine.exceptions import TemplateSyntaxError
from django_rust_templates import _native, render
from django_rust_templates import host as host_module
from scripts.filter_probe import ensure_settings

# The *vendored* corpus on the path so `find_library` resolves
# `template_tests.templatetags.custom` — the fixture `CounterNode` lives in, and
# the same one `test_include.py:372-397` uses for "was this node built once". It
# is `django_suite` and not `.django/tests` because `CounterNode`'s module does
# `import django_rust_templates.engine as template`, and under M18 that is what
# makes it the engine under test rather than Django's. See the longer note in
# `tests/test_parser_parity.py`.
_DJANGO_TESTS = Path(__file__).resolve().parent.parent / "django_suite"
if str(_DJANGO_TESTS) not in sys.path:
    sys.path.insert(0, str(_DJANGO_TESTS))

# A `{% cycle %}` named *outside* the body and named *again* inside it. Both
# mentions are one slot (`alloc_slot` reserves it; `named_cycle_nodes` maps the
# name to it), so "one slot" has to mean "one node" — which it does only while
# the table is one object for the whole parse.
_CYCLE_ACROSS_PARTIAL = (
    "{% cycle 'a' 'b' as c %}"
    "{% partialdef p %}{% cycle c %}{% endpartialdef %}"
    "{% partial p %}{% partial p %}"
)

# A *stateful* library tag in a body: `do_counter` builds its node once while
# Rust parses the body, and each `{% partial %}` render advances it.
_COUNTER_ACROSS_PARTIAL = (
    "{% load custom %}{% partialdef p %}{% counter %}{% endpartialdef %}"
    "{% partial p %}{% partial p %}{% partial p %}"
)


def _counter_engine():
    """An engine that can `{% load custom %}` — `_DJANGO_TESTS` is why."""
    ensure_settings()
    return Engine(libraries={"custom": "template_tests.templatetags.custom"})


def _host(source=""):
    """A `ShellHost` over a freshly parsed template's parser.

    `ensure_settings()` is called here rather than at module level: a
    module-level `settings.configure` changes what every test module discovered
    *after* this one sees (`tests/test_host_tags.py`'s `setUpClass` records the
    same reason). The third argument to `parser_for` is left at its default
    `UNKNOWN_ORIGIN` — passing `None` explicitly is a different origin and would
    change the message a duplicate-definition row asserts.
    """
    ensure_settings()
    return host_module.ShellHost(render.parser_for(source, Engine()))


class PartialNodeSeamTests(unittest.TestCase):
    def test_partial_node_does_not_resolve_the_name(self):
        """The seam reads no state at all, which is the whole point of it.

        `DeferredSubDict.__getitem__` is where the lookup happens, and that is at
        *render* time. So this must succeed on a name that has never been defined
        anywhere — if `partial_node` raised, or read `extra_data["partials"]`,
        the snapshot bug would be exactly here.
        """
        node = _host().partial_node("never-defined")
        self.assertEqual(node.partial_name, "never-defined")

    def test_the_mapping_is_a_view_over_extra_data_and_not_a_copy(self):
        """The distinction `DeferredSubDict` exists for, and the row a copy fails.

        A snapshot taken at compile time satisfies the row above and fails this
        one: a definition written into `extra_data` *after* the node was built
        must be visible through the node's mapping.
        """
        host = _host()
        node = host.partial_node("p")
        host.parser.extra_data.setdefault("partials", {})["p"] = "sentinel"
        self.assertEqual(node.partial_mapping["p"], "sentinel")

    def test_the_mapping_reads_the_parser_it_was_built_from(self):
        """Two parsers, two mappings — the control for "live view".

        Without this, a `DeferredSubDict` wired to some module-global dict would
        pass both rows above. The population is two hosts, and the assertion is
        that each sees only its own `extra_data`.
        """
        first, second = _host(), _host()
        node = first.partial_node("p")
        first.parser.extra_data.setdefault("partials", {})["p"] = "one"
        second.parser.extra_data.setdefault("partials", {})["p"] = "two"
        self.assertEqual(node.partial_mapping["p"], "one")

    def test_the_tag_now_compiles_and_fails_at_render_instead(self):
        """The change Task 3 makes, stated as the *move* of one failure.

        Before: `{% partial p %}` raised `UnimplementedTag` from the registry, at
        compile. After: it compiles — Rust builds a host node for it, which means
        the whole route works, `Host::partial_node` included — and the failure,
        if the name is never defined, comes from `PartialNode.render`
        (`defaulttags.py:430-435`). So the row asserts **when**, not only what: an
        assertion on the message alone would pass on the old tree too, because
        the old tree also failed.

        `{% partial p %}` with no definition anywhere is a template Django
        *parses* (`tests/parser_corpus.py`'s row rests on the same fact); this is
        the render half of it, which is where the message lives.
        """
        with self.assertRaises(TemplateSyntaxError) as caught:
            render.render_string("{% partial nodice %}", {})
        self.assertEqual(
            str(caught.exception),
            "Partial 'nodice' is not defined in the current template.",
        )
        self.assertNotIsInstance(caught.exception, _native.UnimplementedTag)

    def test_the_argument_errors_are_djangos_two_messages_end_to_end(self):
        """The tag body's messages, through the whole shell not a unit row.

        `crates/template-core/src/parser/tags/partial.rs` asserts these two under
        `PartialHost`; this asserts the *class* the shell hands back for them,
        which is the half the Rust row cannot see. Same message for both, since
        Django's wildcard arm covers "too few" and "too many" alike — the row
        exists so a port that split them has to notice.
        """
        for source in [
            "{% partial %}",
            "{% partial p q %}",
        ]:
            with self.subTest(source=source):
                with self.assertRaises(TemplateSyntaxError) as caught:
                    render.render_string(source, {})
                self.assertEqual(
                    str(caught.exception),
                    "'partial' tag requires a single argument",
                )
                self.assertNotIsInstance(caught.exception, _native.UnimplementedTag)


class PartialDefSeamTests(unittest.TestCase):
    def test_a_partial_before_its_definition_renders(self):
        """Review Focus 1, end to end — the row Tasks 2 and 3 had to defer.

        The definition appears *after* the use, and the template renders, because
        `partial_mapping` is looked up at render. This could not be written
        before this task: `{% partialdef %}` was a stub until now, so the source
        raised before either tag's shell code ran.
        """
        source = "{% partial p %}{% partialdef p %}body{% endpartialdef %}"
        self.assertEqual(render.render_string(source, {}), "body")

    def test_an_inline_definition_renders_at_the_point_of_use(self):
        source = "{% partialdef p inline %}body{% endpartialdef %}"
        self.assertEqual(render.render_string(source, {}), "body")

    def test_a_duplicate_definition_names_the_origin_template(self):
        """Review Focus 2: the message names the origin, and the *second* raises.

        Django's check is "in template order" — `if partial_name in partials` —
        so the first definition wins and the second raises, quoting
        `parser.origin.name` rather than anything in the tag. That name is Python
        state the Rust side does not hold, which is why the check lives in
        `ShellHost.register_partial_def` and not in the tag body.

        The expected string is **measured against the pinned tree, not read off
        this project's own output**: `Engine().from_string(<the same source>)`
        raises exactly this, because `Template.__init__`'s `origin` default is
        `Origin(UNKNOWN_SOURCE)` and `UNKNOWN_SOURCE` is the literal
        `'<unknown source>'`. The plan for this task wrote `'None'`, which is
        what `parser.origin.name` would be if the origin were the *host's*
        default; it is the template's, and it is Django's string on both sides.
        """
        with self.assertRaises(TemplateSyntaxError) as caught:
            render.render_string(
                "{% partialdef p %}a{% endpartialdef %}"
                "{% partialdef p %}b{% endpartialdef %}",
                {},
            )
        self.assertEqual(
            str(caught.exception),
            "Partial 'p' is already defined in the '<unknown source>' template.",
        )

    def test_the_stored_partial_and_the_node_share_one_nodelist(self):
        """`defaulttags.py:1250` and `defaulttags.py:1265` are the *same* argument, by identity.

        The `PartialTemplate` in `extra_data` and the `PartialDefNode` returned
        hold one nodelist object, so an inline render and a deferred render walk
        one tree. A shell that materialised the descriptor twice would pass every
        other row in this file — the two trees would be *equal* but not
        *identical*, and nothing about the rendered output can tell them apart.
        So the node is captured rather than rendered: `_native.parse(source,
        host=host)` drives the real call and a spy on the seam keeps the return
        value, which `render_string` would have built and discarded. (The same
        spy idiom `tests/test_host_tags.py` uses on the same seam, for the
        opposite purpose — the route it must *not* be reached from.)
        """
        host = _host()
        captured = []
        original = host.register_partial_def

        def spy(*args, **kwargs):
            node = original(*args, **kwargs)
            captured.append(node)
            return node

        host.register_partial_def = spy
        _native.parse("{% partialdef p inline %}body{% endpartialdef %}", host=host)

        stored = host.parser.extra_data["partials"]["p"]
        self.assertEqual(len(captured), 1, "the seam was not called exactly once")
        self.assertIs(stored.nodelist, captured[0].nodelist)


class ReferenceTableTests(unittest.TestCase):
    """One parse, one reference table — the property Task 4's route turned into
    a requirement rather than a tidiness.

    A `NodeRef`/`NodeSlot` handle is an **index**, so it is meaningful only
    against one table: two readers of one index must subscript one object. Until
    Task 4 nothing materialised mid-parse, so a table built per read was
    indistinguishable from one built per parse. `register_partial_def`
    materialises the body *during* the parse, and at that point the difference
    stops being invisible in two directions at once — the body's handles have to
    resolve at all, and a slot the body fills has to be filled for the outer
    tree.
    """

    def test_a_body_containing_a_filter_expression_materialises(self):
        """The blunter half, and the one that was measured red.

        A filter expression's *function* is resolved by the host at parse time
        and crosses as a handle, so a body containing any filter — here, the
        `{{ x|default:'d' }}` of a plain `{% if %}`-free expression — could not
        be materialised at all while the body's descriptor arrived without the
        parse's table. The tree the shell got was the exception:

            LookupError: the filter 'default' arrived as <NodeRef 0> but no
            resolve_ref was passed to materialise, so the function Django bound
            at base.py:771 cannot be reached

        Measured on the pinned tree: 27 of the 1,547 went red on exactly this,
        because `template_tests` uses partials with filters in their bodies.

        The assertion is on the *rendered value* rather than on the absence of
        the exception, so the row cannot pass on a body that materialised to
        nothing.
        """
        source = (
            "{% partialdef p %}{{ x|default:'d' }}{% endpartialdef %}"
            "{% partial p %}"
        )
        self.assertEqual(render.render_string(source, {}), "d")

    def test_a_partial_body_and_the_outer_template_share_one_cycle_node(self):
        """One slot, one node — the identity the table exists to keep.

        `{% cycle 'a' 'b' as c %}` outside names the node; the body's
        `{% cycle c %}` names the same **slot**. `alloc_slot` reserves one slot
        and `named_cycle_nodes` maps the name to it, so a live table
        materialises one `CycleNode` and both mentions point at it. A *snapshot*
        table materialises the body mid-parse against a list that is then
        discarded, and the outer materialisation builds a second — two trees
        that are equal and not identical.

        Identity is asserted rather than output, because output **cannot** see
        this: `PartialTemplate.render` does
        `with context.render_context.push_state(self)` (`base.py:340-346`), so a
        named cycle restarts inside every partial render either way — measured,
        `.review/m13-measurements/m13_measure_cycle_across_partials.py`, whose
        `'aaa'` is the row below. And the reason no *behavioural* row can replace
        this one is
        `.review/m13-measurements/m13_measure_ref_fill_sides.py`: a slot the
        materialiser fills is the only kind of table entry a snapshot can lose,
        and `cycle.rs` is `alloc_slot`'s sole production caller.

        The materialise call is `_native.parse`'s **three-tuple**, handed
        straight back — `materialise` reads the two callbacks off it, which is
        the same path `render.parse_template` takes. `parser=` is required for a
        body carrying a filter expression, and the node classes are compared by
        name because this file does not import `django.template.defaulttags`.
        """
        host = _host()
        tree_desc = _native.parse(_CYCLE_ACROSS_PARTIAL, host=host)
        tree = render.materialiser().materialise(tree_desc, parser=host.parser)

        outer = [n for n in tree if type(n).__name__ == "CycleNode"]
        body = host.parser.extra_data["partials"]["p"].nodelist
        inner = [n for n in body if type(n).__name__ == "CycleNode"]

        self.assertEqual(len(outer), 1, "the outer template has no single cycle")
        self.assertEqual(len(inner), 1, "the body has no single cycle")
        self.assertIs(outer[0], inner[0])

    def test_a_named_cycle_across_a_partial_renders_djangos_string(self):
        """The same shape end to end, against the pinned tree's own output.

        `'aaa'`: one `a` from the outer `{% cycle %}` and one from each
        `{% partial p %}` — both partials render `a` because the named cycle
        restarts inside a partial render. A single monotonically advancing node
        would give `'aba'`, which is what the plan first predicted; the measured
        value is not that, and re-running the instrument is how to re-derive it.

        This is a regression guard rather than the reference table's proof — the
        row above is that. It is here because a table that resolved handles to
        the *wrong* node would show up in output before it showed up here.
        """
        self.assertEqual(render.render_string(_CYCLE_ACROSS_PARTIAL, {}), "aaa")

    def test_a_stateful_library_tag_in_a_body_is_built_once(self):
        """The behavioural half, and explicitly *not* the reference table's proof.

        `'012'`: `{% counter %}` is a library tag, so `do_counter` builds its
        node once while Rust parses the body, and the three `{% partial p %}`
        renders advance it. A body re-parsed or re-materialised per render gives
        `'000'` — which is also what three independent mentions give, measured
        (`.review/m13-measurements/m13_measure_counter_across_partials.py`).
        That is a real defect and a user-visible one, which is why the row is
        here; it says nothing about the reference table, because the counter's
        entry is filled by the **host** during the parse and is therefore
        non-`None` in every list built afterwards.
        """
        self.assertEqual(
            render.render_string(_COUNTER_ACROSS_PARTIAL, {}, _counter_engine()),
            "012",
        )


if __name__ == "__main__":
    unittest.main()
