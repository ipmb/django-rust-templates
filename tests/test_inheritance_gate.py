"""A2's matrix, as two tests: the predicate says "Rust's" for all three, and a `{% load %}` still wins.

The host seam's one question is *"is this callable the one Rust implements?"*
(`python/django_rust_templates/host.py`), and until M7 it was answered by two
clauses: an exception list of three names, then an identity comparison against
`defaulttags`' table. Task 4 deletes the exception list, and the pair of tests
here is what makes both halves of that visible — including the half that is a
*regression* the list was hiding, which is why one of them is written to fail
first.

The three names are `loader_tags`', not `defaulttags`', which is what made the
exception list look necessary: a predicate that only reads `defaulttags` says
"not mine" for `block`/`extends`/`include` and routes them to the host. Both are
Django built-ins — `Engine.default_builtins` (`engine.py:14-18`) lists
`django.template.loader_tags` third, and `Parser.__init__` merges each through
`add_library` (`base.py:668-670`) — so the fix is to widen the comparison's
*table*, not to keep a list beside it.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import Engine, Library, loader_tags  # noqa: E402

from django_rust_templates.host import ShellHost  # noqa: E402
from django_rust_templates.render import parser_for  # noqa: E402


class HostPredicateTests(unittest.TestCase):
    def test_the_host_declines_every_name_rust_owns(self):
        """A2: the predicate must say 'Rust's' for all three, with no exception list.

        This is row 4 of the plan's A2 matrix, and it is deliberately two
        assertions: that the parser's `block` really is `loader_tags`' callable
        (row 3's precondition — the identity clause can only work if the callable
        it compares against is the one it finds), and that the host declines.

        **It does not fail before Step 5's edits, and it is not meant to.** The
        `_M7_OWNED` clause returned `False` first, so the test passes on the
        pre-M7 tree too; it goes red only if the guard is deleted *without* the
        table being widened (A2's rows 2 and 3). It is a pin on the end state,
        not a red-then-green step.
        """
        parser = parser_for("")
        host = ShellHost(parser)
        for name in ("block", "extends", "include"):
            with self.subTest(name=name):
                self.assertIs(parser.tags[name], loader_tags.register.tags[name])
                self.assertIs(host.has_tag(name), False)

    def test_a_loaded_library_still_shadows_a_built_in(self):
        """A2's rows 5 and 6: the identity clause reaches a `{% load %}`ed override.

        **This is Task 4's failing test.** Before the exception list is deleted
        it returns `False` — the list is consulted before identity, so a library
        registering `block` loses — and deleting the list is what makes it
        `True`. Note the shadowing is established *for* these three names, not
        preserved for them; the same override of `comment` already worked (M6's
        gate, `tests/test_load_shadowing.py`).
        """

        def mine(parser, token):
            raise NotImplementedError

        lib = Library()
        lib.tag("block", mine)
        engine = Engine()
        engine.template_libraries["custom"] = lib
        parser = parser_for("", engine)
        parser.add_library(lib)
        self.assertIs(ShellHost(parser).has_tag("block"), True)


if __name__ == "__main__":
    unittest.main()
