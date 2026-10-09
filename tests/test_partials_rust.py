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
"""

import unittest

from django.template import Engine
from django_rust_templates import render
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


if __name__ == "__main__":
    unittest.main()
