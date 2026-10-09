"""The partial pair's seam, from the Python side.

The Rust rows in `crates/template-core/src/parser/tags/partial*.rs` assert what
the *tag* passes across the seam. These assert what the shell does with it —
which is where the two behaviours the acceptance corpus cannot see live: the
mapping is **live** (looked up at render, so source order does not matter), and
the body's nodelist is **one object** shared by the `PartialTemplate` and the
`PartialDefNode`.

Task 2's rows call `partial_node` directly, because it takes only a name. The
rows that need a `NodeListDesc` — `register_partial_def`'s argument — cannot be
written yet: there is no public Python route to one, so a row would have to
fabricate it rather than observe what Rust produced. They arrive with the tasks
that make `{% partialdef %}` reach the seam, at which point `_native.parse`
drives both methods for real.

Task 3's rows drive `{% partial %}` through `render.render_string`, which is the
first thing in this file to exercise the whole route — Rust's registry dispatching
to `partial.rs`, the tag calling `Host::partial_node`, and the shell building the
`PartialNode`. What they assert is the **move** of a failure (from compile-time
`UnimplementedTag` to render-time `TemplateSyntaxError`) plus the class the shell
hands back, neither of which the Rust rows — which parse under a `PartialHost`
and never materialise — can see.
"""

import unittest

from django.template import Engine
from django.template.exceptions import TemplateSyntaxError
from django_rust_templates import _native, render
from django_rust_templates import host as host_module
from scripts.filter_probe import ensure_settings


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


if __name__ == "__main__":
    unittest.main()
