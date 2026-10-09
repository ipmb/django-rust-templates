"""M7's gate file: the host seam's three questions, each with the instrument that can fail.

Three tasks land here because each is a place where the *seam* — the pair of
callbacks in `python/django_rust_templates/host.py` — is what could be wrong
rather than the Rust, and where the 1547 cannot see it:

- **A2, the predicate.** Its one question is *"is this callable the one Rust
  implements?"*, and until M7 it was answered by two clauses: an exception list
  of three names, then an identity comparison against `defaulttags`' table. Task
  4 deletes the exception list, and the pair of tests here is what makes both
  halves of that visible — including the half that is a *regression* the list was
  hiding, which is why one of them is written to fail first.

  The three names are `loader_tags`', not `defaulttags`', which is what made the
  exception list look necessary: a predicate that only reads `defaulttags` says
  "not mine" for `block`/`extends`/`include` and routes them to the host. Both are
  Django built-ins — `Engine.default_builtins` (`engine.py:14-18`) lists
  `django.template.loader_tags` third, and `Parser.__init__` merges each through
  `add_library` (`base.py:668-670`) — so the fix is to widen the comparison's
  *table*, not to keep a list beside it.

- **A4, `must_be_first` inbound.** M6's seam carried every node field but that
  one, so a host node setting it was accepted where Django refuses.

- **A6, the include cache.** §10.3 names `test_include_cache` as M7's gate, and
  the `.django` row could not be it: the shim did not interpose `loader_tags`
  at M7, so the suite ran on stock Django's parser and would have passed whether
  or not M7 shipped. That reason expired at M11 Task 1 and the row is now real
  signal — measured by mutating our `IncludeNode.render`, see
  `IncludeCacheGateTests` below, which also says which of the two instruments is
  direct-entry and which is end-to-end.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
# `template_tests.templatetags.custom` is reached by name rather than
# re-implemented, so the rows below exercise Django's own fixture and not a copy
# of it that could drift — `tests/test_host_tags.py:36-41` is the precedent, and
# it needs the same second path entry.
for _path in (DJANGO_CHECKOUT, DJANGO_CHECKOUT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

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

    def test_the_wire_shows_which_side_compiled_each_name(self):
        """The same claim asked structurally, which is the detector that survives a predicate rewrite.

        A Rust-compiled tag arrives as a plain class name (`BlockNode`); a
        host-compiled one arrives as `HostNode` carrying a `NodeRef` to the node
        the host built. So *"which side compiled this"* is readable off the parse
        result without asking the predicate anything — and that is what makes
        this a second detector rather than a restatement of the pair above. The
        pair says *why* the routing is what it is and has to be rewritten when
        the predicate changes; this says *that* it is, and the routing is exactly
        what a change will alter. Measured use: Task 4's half-fix (the table
        un-widened) is invisible to every message-comparing corpus — 0 of 106
        error rows differ — and visible here, where `.django`'s `defaulttags`
        table rebound reads `has_tag -> True` and the wire reads `HostNode` for
        all three names.

        The table names the three because a valid source per tag cannot be
        generated, and that is the whole of the coupling: the assertion is about
        the wire's *shape*, not about how the predicate decides.
        """
        from django_rust_templates import _native

        engine = Engine(libraries={"custom": "template_tests.templatetags.custom"})
        for name, source, class_name in (
            ("block", "{% block a %}{% endblock %}", "BlockNode"),
            ("extends", '{% extends "a" %}', "ExtendsNode"),
            ("include", '{% include "a" %}', "IncludeNode"),
        ):
            with self.subTest(name=name):
                self.assertIs(ShellHost(parser_for("", engine)).has_tag(name), False)
                nodes, _contains_nontext, _refs = _native.parse(
                    source, host=ShellHost(parser_for(source, engine))
                )
                self.assertEqual(nodes[0][0], class_name)

        # The other direction, so the rows above are not satisfied by everything
        # being Rust's: `{% load %}` is Rust's host's, and so is what it brings
        # in (`test_the_host_declines_every_name_rust_owns`' third clause).
        nodes, _contains_nontext, _refs = _native.parse(
            "{% load custom %}{% counter %}", host=ShellHost(parser_for("", engine))
        )
        self.assertEqual([node[0] for node in nodes], ["HostNode", "HostNode"])


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

        from django_rust_templates.render import render_string

        engine = self._engine()
        # A leading `text` is a `TextNode` and flips nothing; `{% load %}` is
        # what flips the flag, on its own account. Measured:
        # `"{% load mylib %}{% first_tag %}"` refuses identically.
        source = "text{% load mylib %}{% first_tag %}"

        # Both sides now raise **the same class**, and that is M9's doing rather
        # than a coincidence: `render.parse_template` converts Rust's
        # `_native.ParseError` into `TemplateSyntaxError` in the commit that
        # makes `compile_nodelist` the suite's parse path. Before M9 this row
        # asserted `_native.ParseError` on our side and compared only the text,
        # because design decision 2 gave the message to Rust and the object to
        # Python; the object is Python's now, so the class is asserted too and
        # the text comparison stays as the thing that pins the *message*.
        with self.assertRaises(TemplateSyntaxError) as ours:
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

        from django_rust_templates.render import render_string

        engine = self._engine(builtin=True)

        self.assertEqual(render_string("{% first_tag %}", {}, engine), "F")
        self.assertEqual(engine.from_string("{% first_tag %}").render(Context({})), "F")

        second = "{% first_tag %}{% first_tag %}"
        with self.assertRaises(TemplateSyntaxError) as ours:
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
        itself (`base.py:602`). Since M9 both sides raise the *same* class, so
        the cross-engine assertion is `assertRaises` on both plus agreement in
        text — design decision 2 still gives the message to Rust, and
        `render.parse_template` is what turns the object into Django's.
        """
        from django.template import TemplateSyntaxError

        from django_rust_templates.render import render_string

        engine = self._engine()
        source = "{% load mylib %}{% bad_tag %}"

        with self.assertRaises(TemplateSyntaxError) as ours:
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

        # Stock Django refuses too, by accident: `extend_nodelist` reads
        # `node.must_be_first` unguarded (`base.py:602`) and raises
        # `AttributeError`. **That oracle can no longer be reached through
        # `Engine.from_string`** — since Task 2 that route is Rust's, and Rust
        # refuses explicitly before Django's line is ever reached — so the row
        # drives the transcription's own `Parser` instead, which is the object
        # `django.template.base.Parser` resolves to and the same one the parity
        # harness drives. Keeping it here is what makes the divergence above a
        # *choice* that is visible, rather than an error path that quietly
        # stopped having a second side.
        from django.template.base import UNKNOWN_SOURCE, Lexer, Origin, Parser

        parser = Parser(
            Lexer(source).tokenize(),
            engine.template_libraries,
            engine.template_builtins,
            Origin(UNKNOWN_SOURCE),
        )
        with self.assertRaises(AttributeError):
            parser.parse()


class IncludeCacheGateTests(unittest.TestCase):
    """§5.5 / §10.3 / A6: the included template is resolved once, node identity stable.

    `syntax_tests/test_include.py:371-397` is Django's own row and the reason
    this one exists: it asserts that
    `{% include %}` keeps resolved templates constant (#27974) by rendering a
    loop whose body includes a template that increments a counter. If the node is
    re-created per render the counter restarts and the output repeats digits.

    The `.django` row could not be M7's gate, and that was the whole of A6: the
    shim did not yet interpose `loader_tags`, so the pinned suite ran that row on
    **stock Django's** parser — it passed whether or not M7 shipped. This is the
    same instrument pointed at our engine, and the same reason the two engines
    are compared in one row.

    **That reason expired at M11 Task 1, and the re-read is a measurement
    rather than a re-reading of this paragraph.** With `IncludeNode.render`'s
    cache bypassed in `_impl/loader_tags.py`, Django's row at
    `syntax_tests/test_include.py:371-397` fails `'000000000' != '012345678'` — so
    it is now real signal and no longer a row about the schedule. **Both
    instruments stay.** Which is which: Django's row is the *end-to-end* one
    (it reaches the cache through `render_to_string` and a real loader, the path
    a Django project takes); the counter row below is the *direct-entry* one,
    because it calls our `render_string` directly and renders stock in the same
    row, which is the only way to see our engine disagreeing with Django about
    one input. `test_the_loop_body_holds_exactly_one_include_node` is the third
    and the one neither counter row can replace: two nodes rendering identical
    output pass both of them. The general form, worth keeping beside the row:
    **a vacuity justification is a claim about the tree on a date, and
    interposing the module is precisely the event that expires one.**

    The cache is Python's own: `IncludeNode.render` keys
    `render_context.dicts[0]` on `self` (`loader_tags.py:197`), so identity is
    the mechanism, and a materialiser that rebuilt the node per render would miss
    every time. Our side renders the outer source inline while stock renders it
    from the loader; that difference does not touch what is measured — the
    identity of the node the *loop body* re-enters — and the loader is what
    resolves `include`/`next` on both sides.
    """

    SOURCE = '{% for x in vars %}{% include "include" %}{% endfor %}'

    def _engine(self):
        from django.template import Engine

        return Engine(
            loaders=[
                (
                    "django.template.loaders.locmem.Loader",
                    {
                        "template": self.SOURCE,
                        "include": '{% include "next" %}',
                        "next": "{% load custom %}{% counter %}",
                    },
                ),
            ],
            libraries={"custom": "template_tests.templatetags.custom"},
        )

    def test_the_included_template_is_resolved_once(self):
        from django.template import Context

        from django_rust_templates.render import parse_template, render_string

        engine = self._engine()
        # `range(9)` is Django's, and it is what makes the instrument able to
        # fail: a restarting counter prints `0` nine times, not a different
        # string of the same shape.
        self.assertEqual(render_string(self.SOURCE, {"vars": range(9)}, engine), "012345678")
        self.assertEqual(
            engine.render_to_string("template", {"vars": range(9)}), "012345678"
        )
        # The stock side in the same row, rendered through the same engine, so a
        # row that stopped measuring anything would be visible as agreement on
        # the wrong value rather than as a pass.
        self.assertEqual(
            engine.from_string(self.SOURCE).render(Context({"vars": range(9)})),
            "012345678",
        )

    def test_the_loop_body_holds_exactly_one_include_node(self):
        """The mechanism, asserted as shape — and the only assertion here a mutation of ours can fail.

        **A copy in `materialise_node` does not fail the row above, measured.**
        Materialisation runs once per *parse*, so a node copied there is still one
        object across the loop's nine iterations and the cache still hits. The
        plan's Step 2 says to make the `IncludeNode` arm return a copy and watch
        the counter restart; it does not restart, and nothing on our side can make
        it, because "one node per tag" is structural rather than something the
        materialiser chooses. Breaking Django's cache instead — popping
        `render_context.dicts[0][self]` before every render — gives `'000000000'`
        on **both** engines, which shows the row above is sensitive and that its
        sensitivity is not ours.

        So the counter row is a parity instrument: it detects our engine
        disagreeing with stock about what it handed the renderer. This row
        detects the *shape* of what it handed over — one `IncludeNode`, and it is
        the loop body's only node — which is the fact the counter row can only
        observe indirectly. Django's own walk provides it
        (`NodeList.get_nodes_by_type`, `base.py:1093-1098`), over the
        materialised tree, so a second node or a stray empty one in the body
        fails here while leaving the output identical.
        """
        from django.template import loader_tags

        from django_rust_templates.render import parse_template

        template = parse_template(self.SOURCE, self._engine())
        includes = template.nodelist.get_nodes_by_type(loader_tags.IncludeNode)
        self.assertEqual(len(includes), 1)
        # `ForNode.child_nodelists` is `('nodelist_loop', 'nodelist_empty')` —
        # the walk reaches the body through Django's own declaration, and this
        # line names the pinned attribute rather than walking it again.
        body = template.nodelist[0].nodelist_loop
        self.assertEqual(len(body), 1)
        self.assertIs(body[0], includes[0])


if __name__ == "__main__":
    unittest.main()
