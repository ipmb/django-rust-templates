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


class MustBeFirstGateTests(unittest.TestCase):
    """A4: the flag crosses inbound, off the Python node the host built.

    `extend_nodelist` reads `node.must_be_first` for **every** node it appends
    (`base.py:602`), and M6's seam carried every other node field but this one —
    so a host tag returning a node class with `must_be_first = True` was
    *accepted* in a position Django refuses. It is the one M6 defect M7 inherits
    rather than introduces, and it is invisible from Rust's side: the descriptor
    Rust builds for a host node has `must_be_first = false` because nobody read
    the Python attribute.

    The stock comparison is in the same row rather than in prose: the two engines
    must produce the same *message*, and the message is asserted literally beside
    it so a shared regression cannot pass by agreeing on the wrong text.

    **The two directions need two registration routes, and the reason is the
    rule being tested.** `extend_nodelist` sets `nodelist.contains_nontext = True`
    the first time a non-`TextNode` is appended and never clears it
    (`base.py:611-612`, class default `base.py:1088`) — and `{% load %}` appends a
    `LoadNode`. So a *loaded* library's `must_be_first` tag can never be first,
    on either engine (the refusal row, measured). Only a **builtin** reaches the
    parser with no node of its own, through `Parser.__init__`'s merge of
    `Engine.template_builtins` (`engine.py:63`) — so the accepted row registers
    one that way. `get_template_builtins` only runs `import_library` over that
    list (`engine.py:120-121`, `library.py:476-491`), which resolves a dotted
    path to a module's `register`; appending the `Library` directly is what
    `OPTIONS["builtins"]` produces with the module indirection removed, and both
    routes were measured to agree (`.review/m7-measurements/m7_measureB.py`,
    section (d)).
    """

    def _library(self):
        from django.template import Library
        from django.template.base import Node

        class FirstNode(Node):
            must_be_first = True

            def render(self, context):
                return "F"

        lib = Library()

        @lib.tag("first_tag")
        def do_first(parser, token):
            return FirstNode()

        # A tag that returns something that is not a node at all. `Library.tag`
        # stores whatever callable it is handed without checking, so this is a
        # user error Django's own `extend_nodelist` catches by accident, on the
        # `node.must_be_first` read (`base.py:602`, measured: `AttributeError`).
        lib.tag("bad_tag", lambda parser, token: object())

        return lib

    def _engine(self, builtin=False):
        from django.template import Engine

        engine = Engine()
        lib = self._library()
        if builtin:
            engine.template_builtins.append(lib)
        else:
            engine.template_libraries["mylib"] = lib
        return engine

    def test_a_host_node_that_must_be_first_is_refused_after_text(self):
        from django.template import Context, TemplateSyntaxError

        from django_rust_templates import _native
        from django_rust_templates.render import render_string

        engine = self._engine()
        # A leading `text` is a `TextNode` and flips nothing; `{% load %}` is
        # what flips the flag, on its own account. Measured:
        # `"{% load mylib %}{% first_tag %}"` refuses identically.
        source = "text{% load mylib %}{% first_tag %}"

        # The two *types* differ on purpose (design decision 2, and
        # `test_parser_errors.py`'s note that the shell converts
        # `_native.ParseError` to `TemplateSyntaxError` at M9): Rust owns the
        # message, Python owns the exception object. So the class assertion here
        # is on our own class, and the *message* is what is compared across the
        # seam — which is the whole of what this row is about.
        with self.assertRaises(_native.ParseError) as ours:
            render_string(source, {}, engine)
        with self.assertRaises(TemplateSyntaxError) as stock:
            engine.from_string(source).render(Context({}))
        self.assertEqual(str(ours.exception), str(stock.exception))
        self.assertEqual(
            str(ours.exception),
            "{% first_tag %} must be the first tag in the template.",
        )

    def test_a_host_node_that_must_be_first_is_accepted_first(self):
        """The other direction, and the same engine: position is the only variable.

        Two nodes rather than one, so the refusal comes from the *flag* and not
        from anything the first tag did — the second `{% first_tag %}` is refused
        because a non-`TextNode` already sits in the nodelist, which is exactly
        the condition `base.py:602` states. Both halves run against the stock
        engine too, so a row that stopped refusing would be visible as a
        divergence rather than as a test that quietly got easier.
        """
        from django.template import Context, TemplateSyntaxError

        from django_rust_templates import _native
        from django_rust_templates.render import render_string

        engine = self._engine(builtin=True)

        self.assertEqual(render_string("{% first_tag %}", {}, engine), "F")
        self.assertEqual(engine.from_string("{% first_tag %}").render(Context({})), "F")

        second = "{% first_tag %}{% first_tag %}"
        with self.assertRaises(_native.ParseError) as ours:
            render_string(second, {}, engine)
        with self.assertRaises(TemplateSyntaxError) as stock:
            engine.from_string(second).render(Context({}))
        self.assertEqual(str(ours.exception), str(stock.exception))

    def test_a_host_tag_that_returns_no_node_is_refused_at_the_seam(self):
        """The `?`, which neither row above can see — and the plan's Step 4 could not either.

        The two spellings of this read differ **only** when the attribute is
        absent: a node carrying `must_be_first` answers the same under `?` and
        under `.unwrap_or(false)`, so the plan's Step 4 mutation — swap one for
        the other and watch the refusal test fail — is a no-op. Measured: the
        four tests above stay green under the mutation. This row is the
        difference, and it is the whole justification for raising.

        With `?` the seam refuses the bad tag where it finds it. With
        `.unwrap_or(false)` it is *accepted*, and the failure surfaces later and
        elsewhere — in the materialiser, as
        `AttributeError: 'object' object has no attribute 'token'` from
        `_impl/parser.py`, a message about a file the author of the bad tag never
        touched. Both spellings refuse eventually, which is why the plan's
        observation looked plausible; only one names the contract.

        The stock engine refuses too, by accident: it reads `node.must_be_first`
        itself (`base.py:602`). The types and texts differ on purpose — design
        decision 2 gives the exception object to Python and the *message* to
        Rust — so the cross-engine assertion here is that both refuse, not that
        they agree.
        """
        from django_rust_templates import _native
        from django_rust_templates.render import render_string

        engine = self._engine()
        source = "{% load mylib %}{% bad_tag %}"

        with self.assertRaises(_native.ParseError) as ours:
            render_string(source, {}, engine)
        message = str(ours.exception)
        self.assertTrue(
            message.startswith(
                'ShellHost.call_tag("bad_tag") returned a node without a bool '
                "`must_be_first`: "
            ),
            message,
        )
        self.assertIn("must_be_first", message)

        # Stock refuses as well, so this is a divergence in *text*, not in
        # outcome — the same shape as the two rows above.
        with self.assertRaises(AttributeError):
            engine.from_string(source)


if __name__ == "__main__":
    unittest.main()
