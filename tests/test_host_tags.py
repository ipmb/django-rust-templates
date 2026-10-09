"""M6's blind spot, made visible: a host block tag's body is *Django-parsed*.

Rust hands a host-compiled tag the token cursor and takes back whatever the tag
did not consume. `Library.simple_block_tag` (`library.py:211-212`) does
``nodelist = parser.parse((end_name,)); parser.delete_first_token()`` against
that cursor, and `parser` is Django's own `Parser` (A2), so its `parse` runs
Django's tokenizer-free *node* loop over tokens Rust lexed: every tag in the
body is compiled by Django's `do_*`, not by our registry.

The consequence is a hole in every tree comparison, and it is worth naming
precisely because this project's parity tests are the thing that would normally
catch a divergence: **`assert_trees_match` over such a body compares Django's
work with Django's work.** A green row there is evidence about `simple_block_tag`
and nothing at all about our engine.

Error *text* cannot close the hole — our messages are ported verbatim from
Django's, so `{% for %}` with a missing argument raises identical text whichever
side compiled it. **Until M13 the discriminator was a tag only one side
implemented**: `{% partialdef %}` was a Rust stub, so a Rust-parsed body raised
`UnimplementedTag`, while a Django-parsed one ran `do_partialdef`, which stores
into `parser.extra_data["partials"]` (`defaulttags.py:1250-1251`). M13's Task 4
gave both tags Rust bodies (`builtin_tag_stub_names()` is empty now), so the
discriminator is the **route** rather than the tag: the claim is that
`ShellHost.register_partial_def` — the seam every Rust-compiled
`{% partialdef %}` crosses — is *not* reached from a body Django parses. The two
rows below are that claim and the control it cannot be read without: same host
class, same tag, one difference in the source. The contrast is asserted rather
than argued in this docstring, because a discriminator nobody has seen fail on
both sides is the same kind of comment this file exists to replace.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
DJANGO_TESTS = DJANGO_CHECKOUT / "tests"
for _path in (DJANGO_CHECKOUT, DJANGO_TESTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from django.template import Context, Engine, Library  # noqa: E402
from django.template.exceptions import TemplateSyntaxError  # noqa: E402

from django_rust_templates import _native, render  # noqa: E402
from django_rust_templates import host as host_module  # noqa: E402

# `{% partialdef %}` is the discriminator's tag; see the module docstring. It
# sits between the block tag's opening and closing tokens, so it is inside the
# region Django's own `parse` walks.
BODY_SOURCE = "{% wrap %}{% partialdef p %}x{% endpartialdef %}{% endwrap %}"
# The same tag with no host block around it: this one is Rust's parse, and the
# seam it crosses is what makes the difference measurable.
BARE_SOURCE = "{% partialdef p %}x{% endpartialdef %}"

# `.django`'s own templatetags package, which is what `Engine(libraries=…)`
# resolves through `find_library`. Reached by name rather than re-implemented,
# so the rows below exercise Django's fixture and not a copy of it that could
# drift from the thing `.django`'s `tests.py:148-155` asserts.
CUSTOM_LIBRARIES = {"custom": "template_tests.templatetags.custom"}
REENTRANT_LIBRARIES = {"tag_27584": "template_tests.templatetags.tag_27584"}


def _template(source, engine):
    """The shell's source-to-`Template` path, which `render_string` wraps."""
    return render.parse_template(source, engine)


def _render(source, engine=None):
    return render.render_string(source, {}, engine)


def _wrap_library():
    """A `simple_block_tag` — the shape that re-enters Django's parse.

    `library.py:211-212` is `nodelist = parser.parse((end_name,))` then
    `parser.delete_first_token()`, run against the parser the host owns.
    """
    library = Library()

    @library.simple_block_tag
    def wrap(content):
        return content

    return library


def _hosting_engine():
    engine = Engine()
    engine.template_builtins.append(_wrap_library())
    return engine


# `custom.py`'s two `simple_block_tag` registrations that `.django`'s own suite
# asserts an unclosed-tag message for, and a Rust-owned block tag for contrast.
UNCLOSED_HOST_SOURCE = "{% wrap %}body"
UNCLOSED_RUST_SOURCE = "{% for x in y %}body"


class HostBlockTagRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # `Engine()` reads settings. The suite's configuration, reached from
        # `setUpClass` rather than at import because a module-level
        # `settings.configure` changes what every test module discovered after
        # this one sees.
        from scripts.filter_probe import ensure_settings

        ensure_settings()

    def test_the_nested_region_never_reaches_the_partial_seam(self):
        """The host route's half, asserted on the seam rather than on a tag.

        `do_partialdef` ran — that is the row above — and it ran inside Django's
        `Parser.parse`, so it reached `engine/defaulttags.py`'s compile function
        and **not** `ShellHost.register_partial_def`. That is the hole this
        module documents, and it is now *per-route* rather than per-tag: M13 gave
        both tags Rust bodies, and this body is still Django's because
        `Library.simple_block_tag`'s compile function parses it host-side.

        A spy rather than an `UnimplementedTag` assertion, because the class
        assertion M13 removed was a claim about a *stub*, and the claim that
        survives is about a *route*.
        """
        engine = _hosting_engine()
        host = host_module.ShellHost(render.parser_for("", engine))
        calls = []
        original = host.register_partial_def

        def spy(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        host.register_partial_def = spy
        _native.parse(BODY_SOURCE, host=host)

        self.assertEqual(calls, [], "the host route reached the Rust partial seam")
        self.assertIn("p", host.parser.extra_data["partials"])

    def test_the_bare_template_reaches_the_seam_once(self):
        """The Rust route's half, over the same host class and the same tag.

        The control the row above needs: the spy *does* fire when Rust drives the
        parse, so `[]` there is a fact about the route and not about a spy that
        never fires. Same host, same tag, one argument's difference in the
        source.
        """
        engine = _hosting_engine()
        host = host_module.ShellHost(render.parser_for("", engine))
        calls = []
        original = host.register_partial_def

        def spy(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        host.register_partial_def = spy
        _native.parse(BARE_SOURCE, host=host)

        self.assertEqual(len(calls), 1)
        self.assertIn("p", host.parser.extra_data["partials"])


class HostParserStateTests(unittest.TestCase):
    """`parser.extra_data` survives to the `Template`, and only a successful parse.

    `Parser.__init__` sets `self.extra_data = {}` (`base.py:514`), so the parser
    always has it. What the *template* gets is `self.extra_data =
    parser.extra_data` — and the placement matters more than the line: it is
    inside `compile_nodelist`'s `try`, immediately after `parser.parse()` and
    before `return nodelist` (`base.py:198-200`), so a parse that raises leaves
    the attribute off the object. Our shell builds the `Template` by `__new__`
    and so never runs it; `template_for` owns the attribute list instead, and
    the parser is what carries the state across.
    """

    @classmethod
    def setUpClass(cls):
        from scripts.filter_probe import ensure_settings

        ensure_settings()

    def test_a_user_tags_extra_data_reaches_the_template(self):
        """`.django/tests/template_tests/templatetags/custom.py:396-399` writes
        `parser.extra_data["extra_data"] = "CUSTOM_DATA"`, and
        `.django/tests/template_tests/tests.py:148-155` asserts the template
        carries it.

        The tag is reached through a `{% load %}`, so this is the host path end
        to end: Django's `do_extra_data` runs against Task 3's parser, writes to
        it, and the shell's `Template` reads the same dict.
        """
        engine = Engine(libraries=CUSTOM_LIBRARIES)
        template = _template("{% load custom %}{% extra_data %}", engine)
        self.assertEqual(template.extra_data["extra_data"], "CUSTOM_DATA")

    def test_a_failed_parse_produces_no_template_and_the_writer_still_ran(self):
        """The other half of `base.py:198-200`, and the half nothing pins.

        `engine.py:194` reads `getattr(template, "extra_data", {})` precisely
        because the attribute can be absent, so a shell that pre-seeded it — or
        that assigned it outside the parse — would make `hasattr` true where
        Django's is false. Invisible to every render test; it changes what
        `{% partial %}` lookups do at M13.

        Asserted on the **parser** rather than on a template, because that is
        the only object that exists on the failure path: `parse_template` raises
        before it can build one. The `parser.extra_data` assertion is what keeps
        the row from being vacuous — it proves the fixture's tag really did run
        and write before the parse failed, so the reason no template carries the
        value is the ordering and not an inert fixture. Seen to fail by
        pre-seeding `template_for` with `extra_data = {}`: the row below then
        could not distinguish a successful parse from a failed one.

        `_native.ParseError` is correct **here and only here**, because this row
        calls `_native.compile_template` directly and never passes through
        `render.parse_template`, which is where M9's conversion to
        `TemplateSyntaxError` lives. The rows that go through `render_string`
        are the ones that see the converted class.
        """
        engine = Engine(libraries=CUSTOM_LIBRARIES)
        source = "{% load custom %}{% extra_data %}{% endfor %}"
        parser = render.parser_for(source, engine)
        host = host_module.ShellHost(parser)

        with self.assertRaises(_native.ParseError) as caught:
            _native.compile_template(
                source, parser, render.materialiser(), engine, host
            )
        self.assertIn("endfor", str(caught.exception))
        self.assertEqual(parser.extra_data, {"extra_data": "CUSTOM_DATA"})

    def test_a_host_tag_may_re_enter_the_parse_and_the_error_is_djangos(self):
        """`tag_27584.badtag` re-enters: `parser.parse(("endbadtag",))` then
        `delete_first_token()` (`.django/…/templatetags/tag_27584.py:8-9`).

        This is §5.2's contract and the reason `call_tag` passes tokens by value
        rather than lending `&mut Parser`. The expected message is **computed
        from Django's own engine on the same source**, not transcribed: Rust
        ports Django's strings verbatim, so an equality assertion against a live
        Django render stays honest as the fixture changes and a copied literal
        would not.

        The library has to be `{% load %}`ed and the plan's fixture did not do
        it — measured: without the load both sides raise *identical*
        `Invalid block tag … 'badtag'` text, so the row would have passed while
        exercising no re-entrancy at all. That is the failure mode this file
        exists for, and it is why the source below is not the plan's.

        Both sides must **render**: `BadNode.render` is what raises
        (`tag_27584.py:13-15`), and the parse that re-enters succeeds on both.
        Measured — Django's `from_string(source)` returns a `Template`, so a row
        that stopped at the parse would assert nothing on Django's side and
        would pass for the wrong reason in the other direction.
        """
        source = "{% load tag_27584 %}{% badtag %}{% endbadtag %}"
        libraries = REENTRANT_LIBRARIES

        with self.assertRaises(Exception) as ours:  # noqa: B017 — compared below
            _render(source, Engine(libraries=libraries))
        with self.assertRaises(Exception) as django:  # noqa: B017
            Engine(libraries=libraries).from_string(source).render(Context({}))

        self.assertIsInstance(django.exception, TemplateSyntaxError)
        self.assertEqual(str(ours.exception), str(django.exception))
        # `BadNode.render` raises `TemplateSyntaxError("error")`, which is
        # Django's own class on both sides — and since M9 it is *also* the class
        # the parse would produce, because `render.parse_template` converts
        # `_native.ParseError` into it. This is a *render* exception rather than
        # the parse's, so the two routes are indistinguishable here; the point of
        # pinning it is that the equality above cannot be satisfied by two empty
        # messages.
        self.assertEqual(str(ours.exception), "error")

    def test_a_simple_block_tag_delimits_its_body_with_djangos_own_parse(self):
        """`library.py:211-212`: `nodelist = parser.parse((end_name,))` then
        `parser.delete_first_token()`.

        Django's `parse` *leaves* the end tag on the stack and the tag removes
        it, so a host seam that consumed it — as Rust's own `parse` would, if it
        were driving — needs a token too many and the text after the block is
        eaten. Asserting the trailing text is what catches that.
        """
        self.assertEqual(_render("{% wrap %}a{% endwrap %}b", _hosting_engine()), "ab")

    def test_an_unclosed_host_block_tag_reports_djangos_message(self):
        """The seam's `Parser` must look to the tag as Django's would.

        `do_*` for a block tag calls `parser.parse(("endx",))`; when the tokens
        run out, `Parser.unclosed_block_tag` builds the message **from
        `self.command_stack.pop()`** (`base.py:651`). `Parser.parse` pushed that
        entry itself (`base.py:573`) — and Rust drives the outer loop and keeps
        its own stack, so Django's here is whatever `call_tag` maintains. It
        maintained nothing: measured, `{% wrap %}` with no `{% endwrap %}` gave
        `ParseError('pop from empty list')` in place of Django's message.

        Five assertions in the 1547 depend on the text, all at M9:
        `test_custom.py:418,422` (`div`, `simple_one_default_block`) and
        `test_partials.py:317,373,379`. The expected value is computed from
        Django on the same source rather than transcribed.
        """
        source = UNCLOSED_HOST_SOURCE

        with self.assertRaises(Exception) as ours:  # noqa: B017 — compared below
            _render(source, _hosting_engine())
        with self.assertRaises(TemplateSyntaxError) as django:
            _hosting_engine().from_string(source)

        self.assertEqual(str(ours.exception), str(django.exception))
        # Pinned, so the equality cannot be satisfied by two empty messages —
        # and so that the *type* mistake is visible if it comes back: the
        # message is Django's, assembled by Django's own `unclosed_block_tag`
        # inside the host, wrapped by Rust's `ParseError` at the FFI and turned
        # back into a `TemplateSyntaxError` by `render.parse_template` at M9 —
        # which is the row [`test_an_unclosed_rust_owned_block_tag_is_unaffected`]
        # says is reachable only through the shell.
        self.assertEqual(
            str(ours.exception),
            "Unclosed tag on line 1: 'wrap'. Looking for one of: endwrap.",
        )

    def test_an_unclosed_rust_owned_block_tag_is_unaffected(self):
        """The contrast, so the row above is known to be about the host.

        `{% for %}` is Rust's, so no host call happens and Django's
        `command_stack` is never read. Keeping this green while the row above
        goes red is what says the defect is in `call_tag` rather than in the
        shared error path.

        `TemplateSyntaxError` rather than `_native.ParseError` since M9: both
        rows reach the class through `render_string`, and M9 put the shell's
        conversion in `render.parse_template`. This file still pins
        `_native.ParseError` in the rows that call `_native.compile_template`
        directly — that path has no shell on it and is where the raw class is
        the correct thing to assert.
        """
        with self.assertRaises(TemplateSyntaxError) as caught:
            _render(UNCLOSED_RUST_SOURCE, Engine())
        self.assertIn("Unclosed tag", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
