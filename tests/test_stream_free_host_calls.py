"""The classifier that decides a host tag never reads the token stream.

`ShellHost.call_tag` ships every remaining token in and takes every remaining
token back (`python/django_rust_templates/host.py:422-494`), so a template with N host-compiled tags builds
O(N x tokens) Python objects where Django builds O(tokens). Compile functions
that provably never touch the stack can skip the transfer entirely.

**The first row below is the one that matters most, and it is the one a careless
implementation fails silently.** Every other test here asserts that some
deviation *declines*; a guard that declines everything passes all of them while
delivering nothing. `test_an_unmodified_parser_with_a_stock_simple_tag_qualifies`
is the only row that fails when the feature is merely inert, which is why the
spec names it first (§11.1).

Design: `docs/superpowers/specs/2026-10-13-stream-free-host-calls-design.md`.
"""

import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
DJANGO_TESTS = PROJECT_ROOT / "django_suite"
for _path in (DJANGO_CHECKOUT, DJANGO_TESTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from django_rust_templates import host as host_module  # noqa: E402
from django_rust_templates import render  # noqa: E402
from django_rust_templates.engine import Engine, Library  # noqa: E402
from django_rust_templates.engine import base as engine_base  # noqa: E402
from django_rust_templates.engine import defaulttags  # noqa: E402
from django_rust_templates.engine import library as engine_library  # noqa: E402


#: Trailing filler that puts a fixture over `STREAM_FREE_MIN_TOKENS`.
#: Routing tests care which seam a tag crosses, not how long the suffix is, so
#: they append this rather than relying on an incidental length.
FILLER = "{% if x %}a{% endif %}" * 6


def stock_library():
    """One library holding the three registration shapes the spec classifies."""
    library = Library()

    @library.simple_tag
    def pageurl(target):
        return "/u/"

    @library.simple_tag(takes_context=True)
    def ctx_tag(context):
        return ""

    @library.inclusion_tag("t.html")
    def card():
        return {}

    @library.simple_block_tag
    def wrap(content):
        return content

    # Rebound to a block compiler by `tests/stream_free_shadow.py`, so that the
    # eligible-to-streamful transition §5.1 describes can actually be performed.
    @library.simple_tag
    def shifty():
        return ""

    return library


class ClassifierTests(unittest.TestCase):
    """§5.3's four conditions, against the parser compilation actually builds."""

    @classmethod
    def setUpClass(cls):
        from scripts.filter_probe import ensure_settings

        ensure_settings()

    def setUp(self):
        self.library = stock_library()
        self.engine = Engine()
        self.engine.template_builtins.append(self.library)
        # `parser_for` builds the `_DeferredTokenParser` that real compilation
        # uses, rather than a bare `Parser` — the distinction finding 1 of review
        # round 6 turned on, since it is a `Parser` subclass whose own `__dict__`
        # wins during attribute resolution.
        self.parser = render.parser_for("", self.engine, render.UNKNOWN_ORIGIN)
        self.host = host_module.ShellHost(self.parser)

    def is_stream_free(self, command):
        return self.host.tag_is_stream_free(command)

    # ---------------------------------------------------------------- positive

    def test_an_unmodified_parser_with_a_stock_simple_tag_qualifies(self):
        """The row that fails when the guard is merely inert.

        Every negative row below passes for a classifier that answers `False`
        unconditionally. This one does not, so it is the only evidence that the
        optimisation is reachable at all.
        """
        self.assertTrue(self.is_stream_free("pageurl"))

    def test_takes_context_does_not_change_eligibility(self):
        self.assertTrue(self.is_stream_free("ctx_tag"))

    def test_an_inclusion_tag_qualifies(self):
        self.assertTrue(self.is_stream_free("card"))

    def test_load_qualifies(self):
        self.assertTrue(self.is_stream_free("load"))

    # ---------------------------------------------------------------- negative

    def test_a_simple_block_tag_never_qualifies(self):
        """It calls `parser.parse()` and `delete_first_token()` (`python/django_rust_templates/engine/library.py:213-214`)."""
        self.assertFalse(self.is_stream_free("wrap"))

    def test_a_plain_register_tag_does_not_qualify(self):
        """`do_block_translate` reads `parser.tokens` directly (`python/django_rust_templates/engine/templatetags/i18n.py:564`)."""
        library = Library()

        @library.tag("handrolled")
        def handrolled(parser, token):
            while parser.tokens:
                parser.next_token()
            return engine_base.Node()

        self.engine.template_builtins.append(library)
        parser = render.parser_for("", self.engine, render.UNKNOWN_ORIGIN)
        self.assertFalse(host_module.ShellHost(parser).tag_is_stream_free("handrolled"))

    def test_an_instance_override_of_compile_filter_declines(self):
        self.parser.compile_filter = lambda token: None
        self.assertFalse(self.is_stream_free("pageurl"))

    def test_rebinding_compile_filter_on_the_parser_class_declines(self):
        original = engine_base.Parser.__dict__["compile_filter"]
        engine_base.Parser.compile_filter = lambda self, token: None
        try:
            self.assertFalse(self.is_stream_free("pageurl"))
        finally:
            engine_base.Parser.compile_filter = original

    def test_shadowing_a_guarded_method_on_the_deferred_subclass_declines(self):
        """Round 6, finding 1 — measured to defeat the first draft's guard.

        `_DeferredTokenParser` is itself a `Parser` subclass (`python/django_rust_templates/render.py:234`), so
        its own `__dict__` wins during resolution. Assigning here passes an exact
        type check, passes `not in vars(parser)`, and passes an identity check
        against `Parser.__dict__` — while the resolved method is the replacement.
        """
        deferred = type(self.parser)
        deferred.compile_filter = lambda self, token: None
        try:
            self.assertFalse(self.is_stream_free("pageurl"))
        finally:
            del deferred.compile_filter

    def test_rebinding_library_token_kwargs_declines(self):
        """Round 5 — `parse_bits` resolves the name through `library.py` globals.

        Guarding `base.token_kwargs` instead passes while this rebinding stands,
        because the two names point at one object until exactly this happens.
        """
        original = engine_library.token_kwargs
        engine_library.token_kwargs = lambda bits, parser, support_legacy=False: {}
        try:
            self.assertFalse(self.is_stream_free("pageurl"))
        finally:
            engine_library.token_kwargs = original

    def test_replacing_simplenode_with_a_subclass_keeping_its_init_declines(self):
        """Round 6, finding 2 — initializer identity stays True through this."""
        original = engine_library.SimpleNode

        class Sneaky(original):
            def __new__(cls, *args, **kwargs):
                return super().__new__(cls)

        self.assertIs(Sneaky.__init__, original.__init__)
        engine_library.SimpleNode = Sneaky
        try:
            self.assertFalse(self.is_stream_free("pageurl"))
        finally:
            engine_library.SimpleNode = original

    def test_rebinding_find_library_declines_load(self):
        """`load` calls it in both syntax branches (`defaulttags.py:1125,1131`)."""
        original = defaulttags.find_library
        defaulttags.find_library = lambda parser, name: None
        try:
            self.assertFalse(self.is_stream_free("load"))
        finally:
            defaulttags.find_library = original

    def test_a_foreign_globals_compiler_declines(self):
        """Round 4 — genuine code object and closure, different globals.

        Such a function resolves `parse_bits` through its *own* globals
        (`python/django_rust_templates/engine/library.py:135`), so it can consume tokens while `library.parse_bits`
        is untouched and a code-identity test passes.
        """
        genuine = self.parser.tags["pageurl"]
        foreign = types.FunctionType(
            genuine.__code__,
            dict(genuine.__globals__),
            genuine.__name__,
            genuine.__defaults__,
            genuine.__closure__,
        )
        self.parser.tags["pageurl"] = foreign
        self.assertFalse(self.is_stream_free("pageurl"))

    def test_a_callable_object_exposing_code_and_globals_declines(self):
        """`type(func) is FunctionType` runs before any attribute of it is read."""
        genuine = self.parser.tags["pageurl"]

        class Lookalike:
            __code__ = genuine.__code__
            __globals__ = genuine.__globals__

            def __call__(self, parser, token):
                while parser.tokens:
                    parser.next_token()
                return engine_base.Node()

        self.parser.tags["pageurl"] = Lookalike()
        self.assertFalse(self.is_stream_free("pageurl"))

    def test_a_callable_without_code_declines_without_raising(self):
        class NoMetadata:
            def __call__(self, parser, token):
                return engine_base.Node()

        self.parser.tags["pageurl"] = NoMetadata()
        self.assertFalse(self.is_stream_free("pageurl"))

    def test_an_unknown_parser_subclass_declines(self):
        class Foreign(type(self.parser)):
            pass

        self.parser.__class__ = Foreign
        self.assertFalse(self.is_stream_free("pageurl"))

    def test_a_command_with_no_registered_tag_declines(self):
        self.assertFalse(self.is_stream_free("no_such_tag"))


if __name__ == "__main__":
    unittest.main()


class StreamFreeInvocationTests(unittest.TestCase):
    """`call_tag_stream_free`: same node handling, no stream."""

    @classmethod
    def setUpClass(cls):
        from scripts.filter_probe import ensure_settings

        ensure_settings()

    def setUp(self):
        self.library = stock_library()
        self.engine = Engine()
        self.engine.template_builtins.append(self.library)
        self.parser = render.parser_for("", self.engine, render.UNKNOWN_ORIGIN)
        self.host = host_module.ShellHost(self.parser)

    def wire(self, contents):
        return (engine_base.TokenType.BLOCK.value, contents, 1, (0, len(contents) + 6))

    def test_it_returns_the_node_a_stock_simple_tag_compiles(self):
        self.assertTrue(self.host.tag_is_stream_free("pageurl"))
        node = self.host.call_tag_stream_free("pageurl", self.wire("pageurl x"))
        self.assertIsInstance(node, engine_library.SimpleNode)

    def test_load_registers_its_library_through_the_stream_free_path(self):
        """The side effect `{% load %}` exists for must still happen."""
        engine = Engine(libraries={"custom": "template_tests.templatetags.custom"})
        parser = render.parser_for("", engine, render.UNKNOWN_ORIGIN)
        host = host_module.ShellHost(parser)
        self.assertNotIn("one_param", parser.tags)
        self.assertTrue(host.tag_is_stream_free("load"))
        host.call_tag_stream_free("load", self.wire("load custom"))
        self.assertIn("one_param", parser.tags)

    def test_the_command_stack_is_popped_on_success(self):
        self.host.tag_is_stream_free("pageurl")
        self.host.call_tag_stream_free("pageurl", self.wire("pageurl x"))
        self.assertEqual(self.parser.command_stack, [])

    def test_the_command_stack_keeps_its_entry_when_the_tag_raises(self):
        """Django leaves it too: the parse dies with the entry in place."""
        self.host.tag_is_stream_free("pageurl")
        with self.assertRaises(Exception):
            self.host.call_tag_stream_free("pageurl", self.wire("pageurl"))
        self.assertEqual([c for c, _ in self.parser.command_stack], ["pageurl"])

    def test_a_compile_error_is_stashed_for_identity_restoration(self):
        """`render.django_exception` re-raises `last_error` itself, not a copy."""
        self.host.tag_is_stream_free("pageurl")
        with self.assertRaises(Exception) as caught:
            self.host.call_tag_stream_free("pageurl", self.wire("pageurl"))
        self.assertIs(self.host.last_error, caught.exception)
        self.assertIsNotNone(getattr(caught.exception, "token_wire", None))

    def test_the_pending_selection_is_cleared_on_success(self):
        self.host.tag_is_stream_free("pageurl")
        self.host.call_tag_stream_free("pageurl", self.wire("pageurl x"))
        self.assertIsNone(self.host._pending)

    def test_the_pending_selection_is_cleared_when_the_tag_raises(self):
        self.host.tag_is_stream_free("pageurl")
        with self.assertRaises(Exception):
            self.host.call_tag_stream_free("pageurl", self.wire("pageurl"))
        self.assertIsNone(self.host._pending)


class SentinelTests(unittest.TestCase):
    """`parser.tokens` during a stream-free call raises, in every mode.

    An empty list would let a misclassified callback run `while parser.tokens:`
    and silently skip its body; leaving the attribute alone is worse still,
    because `_DeferredTokenParser` has none before the first host call and the
    first read would lex the whole source and hand back the *entire original
    template* (`python/django_rust_templates/render.py:277-284`).
    """

    @classmethod
    def setUpClass(cls):
        from scripts.filter_probe import ensure_settings

        ensure_settings()

    def sentinel(self):
        return host_module._UnavailableStream("pageurl")

    def test_every_guarded_operation_raises(self):
        s = self.sentinel()
        cases = {
            "bool": lambda: bool(s),
            "len": lambda: len(s),
            "iter": lambda: list(s),
            "getitem": lambda: s[0],
            "delitem": lambda: s.__delitem__(-1),
            "pop": lambda: s.pop(),
            "append": lambda: s.append(None),
            "repr": lambda: repr(s),
        }
        for name, operation in cases.items():
            with self.subTest(operation=name):
                with self.assertRaises(host_module.StreamFreeContractViolation):
                    operation()

    def test_the_message_names_the_operation_and_the_command(self):
        with self.assertRaises(host_module.StreamFreeContractViolation) as caught:
            len(self.sentinel())
        self.assertIn("pageurl", str(caught.exception))
        self.assertIn("len", str(caught.exception))

    def test_a_tag_that_reads_the_stream_trips_it(self):
        """Deliberately injected onto the stream-free path (§11, the second row).

        Under *ordinary* dispatch this compile function is rejected by §5.3 and
        gets the full stream; only injected here does it reach the sentinel.
        """
        engine = Engine()
        parser = render.parser_for("", engine, render.UNKNOWN_ORIGIN)
        host = host_module.ShellHost(parser)

        def reads_the_stream(parser, token):
            while parser.tokens:
                parser.next_token()
            return engine_base.Node()

        parser.tags["injected"] = reads_the_stream
        host._pending = reads_the_stream
        wire = (engine_base.TokenType.BLOCK.value, "injected", 1, (0, 12))
        with self.assertRaises(host_module.StreamFreeContractViolation):
            host.call_tag_stream_free("injected", wire)


class EndToEndTests(unittest.TestCase):
    """Through `_native.compile_template`, not `ShellHost` directly.

    This is the group that catches an inert adapter: every classifier test above
    passes whether or not the Rust seam ever consults it. `FilterCheckHost` is
    the production adapter, and inheriting the trait's `false` default would
    leave the whole feature unreachable while the suite stayed green.
    """

    @classmethod
    def setUpClass(cls):
        from scripts.filter_probe import ensure_settings

        ensure_settings()

    def setUp(self):
        self.engine = Engine(libraries={"custom": "template_tests.templatetags.custom"})
        self.engine.template_builtins.append(stock_library())

    def census(self, source):
        """Compile `source`, counting tokens crossing each seam."""
        from django_rust_templates import _native

        class Census(host_module.ShellHost):
            def __init__(inner, parser):
                super().__init__(parser)
                inner.streamful = []
                inner.stream_free = []

            def call_tag(inner, command, token_wire, tokens):
                inner.streamful.append((command, len(tokens)))
                return super().call_tag(command, token_wire, tokens)

            def call_tag_stream_free(inner, command, token_wire):
                inner.stream_free.append(command)
                return super().call_tag_stream_free(command, token_wire)

        parser = render.parser_for(source, self.engine, render.UNKNOWN_ORIGIN)
        host = Census(parser)
        template = _native.compile_template(
            source, parser, render.materialiser(), self.engine, host,
            render.UNKNOWN_ORIGIN,
        )
        return host, template

    def test_a_leading_load_transfers_zero_tokens(self):
        """The universal case: `{% load %}` sees the largest stream and reads none."""
        body = "".join("{% if x %}a{% endif %}" for _ in range(40))
        host, _ = self.census("{% load custom %}" + body)
        self.assertEqual(host.stream_free, ["load"])
        self.assertEqual(
            host.streamful, [], "a stream-free call must not reach the streamful seam"
        )

    def test_eligible_tags_take_the_stream_free_route_and_block_tags_do_not(self):
        source = (
            "{% load custom %}"
            "{% pageurl x %}"
            "{% wrap %}body{% endwrap %}"
            "{% card %}" + FILLER
        )
        host, _ = self.census(source)
        self.assertEqual(host.stream_free, ["load", "pageurl", "card"])
        self.assertEqual([c for c, _ in host.streamful], ["wrap"])

    def test_output_is_identical_to_the_streamful_route(self):
        """The only claim that matters: a faster route, not a different answer."""
        from django_rust_templates.engine.context import Context

        source = (
            "{% load custom %}<p>{% pageurl x %}</p>"
            "{% wrap %}<i>{{ name }}</i>{% endwrap %}"
            "{% if name %}<b>{{ name }}</b>{% endif %}tail"
        )
        ctx = {"name": "world", "x": 1}
        _, fast = self.census(source)
        fast_out = fast.render(Context(dict(ctx)))

        # The same source with the optimisation disabled at the classifier.
        original = host_module.ShellHost.tag_is_stream_free
        host_module.ShellHost.tag_is_stream_free = lambda self, command: False
        try:
            host, slow = self.census(source)
            self.assertEqual(host.stream_free, [], "control must take the old route")
            slow_out = slow.render(Context(dict(ctx)))
        finally:
            host_module.ShellHost.tag_is_stream_free = original

        self.assertEqual(fast_out, slow_out)
        self.assertIn("tail", fast_out)

    def test_a_load_that_rebinds_a_name_mid_parse_is_honoured(self):
        """§5.1 — eligibility is a property of the live callable, not the name.

        `custom` registers a `div` **block** tag. A template that uses the name
        only after loading it must compile through the streamful seam, even
        though nothing about the name itself changed.
        """
        host, _ = self.census(
            "{% load custom %}{% div %}body{% enddiv %}" + FILLER
        )
        self.assertEqual(host.stream_free, ["load"])
        self.assertEqual([c for c, _ in host.streamful], ["div"])

    def test_a_syntax_error_in_a_stream_free_tag_keeps_its_message(self):
        with self.assertRaises(Exception) as caught:
            self.census("{% load no_such_library %}")
        self.assertIn("no_such_library", str(caught.exception))


class NegativeCacheTests(unittest.TestCase):
    """A declined command is asked once per parse; an eligible one is asked every time.

    §8's all-streamful control measured the classification crossing as a ~3%
    cost on templates that gain nothing from it. Caching the *negative* answer
    removes all but one of those crossings and is safe in the one direction that
    matters: a command can only move from ineligible to eligible through a
    `{% load %}` rebinding, and missing that is a lost optimisation rather than
    a wrong answer. Caching a *positive* would be the unsafe direction -- §5.1's
    shadowing case, where a `simple_tag` name is rebound to a block compiler
    mid-parse -- and is therefore not done.
    """

    @classmethod
    def setUpClass(cls):
        from scripts.filter_probe import ensure_settings

        ensure_settings()

    def asked(self, source):
        from django_rust_templates import _native

        engine = Engine(libraries={"custom": "template_tests.templatetags.custom"})
        engine.template_builtins.append(stock_library())

        class Counting(host_module.ShellHost):
            def __init__(inner, parser):
                super().__init__(parser)
                inner.questions = []

            def tag_is_stream_free(inner, command):
                inner.questions.append(command)
                return super().tag_is_stream_free(command)

        parser = render.parser_for(source, engine, render.UNKNOWN_ORIGIN)
        host = Counting(parser)
        _native.compile_template(
            source, parser, render.materialiser(), engine, host,
            render.UNKNOWN_ORIGIN,
        )
        return host.questions

    def test_a_declined_command_is_classified_once_per_parse(self):
        source = "".join("{% wrap %}b{% endwrap %}" for _ in range(8))
        self.assertEqual(self.asked(source), ["wrap"])

    def test_an_eligible_command_is_classified_every_time(self):
        """The live check §5.1 requires: a `{% load %}` can rebind the name."""
        source = (
            "{% load custom %}"
            + "".join("{% pageurl x %}" + FILLER for _ in range(5))
        )
        self.assertEqual(self.asked(source), ["load"] + ["pageurl"] * 5)

    def test_an_eligible_name_rebound_to_a_block_compiler_is_honoured(self):
        """The scenario §5.1 exists for, actually performed.

        An earlier version of this test loaded `custom` and used its `div` twice.
        `div` was a block tag both times, so the eligible-to-streamful transition
        it claimed to cover never occurred, and it passed whether or not
        shadowing worked. Here `shifty` is a genuine `simple_tag` compiled
        *before* the load, and the same name is a block compiler after it.
        """
        from django_rust_templates import _native

        engine = Engine(libraries={"shadow": "tests.stream_free_shadow"})
        engine.template_builtins.append(stock_library())

        source = (
            "{% shifty %}" + FILLER
            + "{% load shadow %}" + FILLER
            + "{% shifty %}body{% endshifty %}" + FILLER
        )

        class Counting(host_module.ShellHost):
            def __init__(inner, parser):
                super().__init__(parser)
                inner.questions = []
                inner.routes = []

            def tag_is_stream_free(inner, command):
                answer = super().tag_is_stream_free(command)
                inner.questions.append((command, answer))
                return answer

            def call_tag_stream_free(inner, command, token_wire):
                inner.routes.append(("free", command))
                return super().call_tag_stream_free(command, token_wire)

            def call_tag(inner, command, token_wire, tokens):
                inner.routes.append(("full", command))
                return super().call_tag(command, token_wire, tokens)

        parser = render.parser_for(source, engine, render.UNKNOWN_ORIGIN)
        host = Counting(parser)
        _native.compile_template(
            source, parser, render.materialiser(), engine, host,
            render.UNKNOWN_ORIGIN,
        )
        # First `shifty` is the stock simple_tag and takes the fast route; after
        # the load the *same name* is a block compiler and must take the full one.
        self.assertEqual(
            host.routes,
            [("free", "shifty"), ("free", "load"), ("full", "shifty")],
        )
        self.assertIn(("shifty", True), host.questions)
        self.assertIn(("shifty", False), host.questions)


class ConversionTests(unittest.TestCase):
    """Errors and output through the *optimised* seam, in both debug modes.

    An earlier version of this class proved nothing. Every fixture ended at the
    offending token, so the remaining suffix was shorter than
    `STREAM_FREE_MIN_TOKENS` and all three ran the ordinary route -- the
    output-parity test compared Tier 0 with Tier 0, and the two error tests
    would have passed with stream-free conversion broken outright. Every
    fixture here therefore carries `FILLER`, and every test asserts the
    specialised seam was actually entered.
    """

    @classmethod
    def setUpClass(cls):
        from scripts.filter_probe import ensure_settings

        ensure_settings()

    def engine(self, debug):
        e = Engine(
            debug=debug, libraries={"custom": "template_tests.templatetags.custom"}
        )
        e.template_builtins.append(stock_library())
        return e

    def compile_watching(self, source, engine):
        """Compile, recording which seam each host tag crossed."""
        from django_rust_templates import _native

        class Watching(host_module.ShellHost):
            def __init__(inner, parser):
                super().__init__(parser)
                inner.routes = []

            def call_tag_stream_free(inner, command, token_wire):
                inner.routes.append(("free", command))
                return super().call_tag_stream_free(command, token_wire)

            def call_tag(inner, command, token_wire, tokens):
                inner.routes.append(("full", command))
                return super().call_tag(command, token_wire, tokens)

        parser = render.parser_for(source, engine, render.UNKNOWN_ORIGIN)
        host = Watching(parser)
        # The host is attached to the exception so a caller can assert identity
        # restoration. Without it the failing path discards the only object that
        # could falsify the claim, which is how the earlier version of this
        # class passed with `django_exception` deliberately rebuilding.
        try:
            template = _native.compile_template(
                source, parser, render.materialiser(), engine, host,
                render.UNKNOWN_ORIGIN,
            )
        except _native.ParseError as error:
            converted = render.django_exception(error, host)
            converted._test_host = host
            raise converted from None
        return host, template

    def test_a_compile_error_arrives_as_the_same_object_with_its_token(self):
        """Restored by identity through `django_exception`, not rebuilt."""
        for debug in (False, True):
            with self.subTest(debug=debug):
                engine = self.engine(debug)
                source = "x{% pageurl %}" + FILLER
                with self.assertRaises(Exception) as caught:
                    self.compile_watching(source, engine)
                error = caught.exception
                self.assertIsInstance(error, engine_base.TemplateSyntaxError)
                self.assertIsNotNone(getattr(error, "token", None))
                self.assertEqual(error.token.contents, "pageurl")
                # **The object, not a copy of it.** Django's `Parser.parse`
                # re-raises what it caught, so a rebuilt equivalent would lose
                # the traceback. Asserting class and token alone cannot tell the
                # two apart -- measured, by replacing `django_exception` with a
                # faithful rebuilder and watching this class stay green.
                self.assertIs(error, error._test_host.last_error)

    def test_the_failing_tag_really_took_the_stream_free_route(self):
        """Without this the test above says nothing about the new path."""
        engine = self.engine(False)
        seen = []

        class Recording(host_module.ShellHost):
            def call_tag_stream_free(inner, command, token_wire):
                seen.append(command)
                return super().call_tag_stream_free(command, token_wire)

        from django_rust_templates import _native

        source = "x{% pageurl %}" + FILLER
        parser = render.parser_for(source, engine, render.UNKNOWN_ORIGIN)
        host = Recording(parser)
        with self.assertRaises(_native.ParseError):
            _native.compile_template(
                source, parser, render.materialiser(), engine, host,
                render.UNKNOWN_ORIGIN,
            )
        self.assertEqual(seen, ["pageurl"])
        self.assertIsNotNone(host.last_error, "the object must be stashed")

    def test_debug_annotates_the_source_location(self):
        engine = self.engine(True)
        with self.assertRaises(Exception) as caught:
            engine_base.Template("line one\n{% pageurl %}" + FILLER, engine=engine)
        debug_info = getattr(caught.exception, "template_debug", None)
        self.assertIsNotNone(debug_info, "debug=True must annotate the error")
        self.assertEqual(debug_info["during"], "{% pageurl %}")
        # The offending tag is on the second line of the fixture.
        self.assertEqual(debug_info["line"], 2)

    def test_output_matches_the_full_route_in_both_modes(self):
        from django_rust_templates.engine.context import Context

        source = (
            "{% load custom %}" + FILLER
            + "<a>{% pageurl x %}</a>" + FILLER
            + "{{ name }}tail"
        )
        context = {"name": "n", "x": 1}
        for debug in (False, True):
            with self.subTest(debug=debug):
                engine = self.engine(debug)
                fast_host, fast_t = self.compile_watching(source, engine)
                fast = fast_t.render(Context(dict(context)))
                # The enabled arm must actually use the specialised route...
                self.assertEqual(
                    fast_host.routes, [("free", "load"), ("free", "pageurl")]
                )

                original = host_module.ShellHost.tag_is_stream_free
                host_module.ShellHost.tag_is_stream_free = lambda self, c: False
                try:
                    slow_host, slow_t = self.compile_watching(source, engine)
                    slow = slow_t.render(Context(dict(context)))
                finally:
                    host_module.ShellHost.tag_is_stream_free = original
                # ...and the control must not.
                self.assertEqual(
                    slow_host.routes, [("full", "load"), ("full", "pageurl")]
                )

                self.assertEqual(fast, slow)
                self.assertIn("tail", fast)


class ShortStreamTests(unittest.TestCase):
    """Classification is skipped when there is almost nothing to save.

    The saving scales with the length of the suffix that would have crossed;
    the classifier crossing costs the same either way. Measured against main,
    a one-host template of a few tokens was **+4.9% slower** — a real cost,
    outside the noise, on a template with nothing to gain. Below the threshold
    the ordinary path is taken without asking, which is always correct: the
    classifier is an optimisation gate, never a correctness one.
    """

    @classmethod
    def setUpClass(cls):
        from scripts.filter_probe import ensure_settings

        ensure_settings()

    def questions(self, source):
        from django_rust_templates import _native

        engine = Engine(libraries={"custom": "template_tests.templatetags.custom"})
        engine.template_builtins.append(stock_library())

        class Counting(host_module.ShellHost):
            def __init__(inner, parser):
                super().__init__(parser)
                inner.asked = []

            def tag_is_stream_free(inner, command):
                inner.asked.append(command)
                return super().tag_is_stream_free(command)

        parser = render.parser_for(source, engine, render.UNKNOWN_ORIGIN)
        host = Counting(parser)
        _native.compile_template(
            source, parser, render.materialiser(), engine, host,
            render.UNKNOWN_ORIGIN,
        )
        return host.asked

    def test_a_tag_with_almost_no_suffix_is_not_classified(self):
        self.assertEqual(self.questions("{% load custom %}<p>x</p>"), [])

    def test_a_tag_with_a_long_suffix_is_classified(self):
        body = "".join("{% if x %}a{% endif %}" for _ in range(10))
        self.assertEqual(self.questions("{% load custom %}" + body), ["load"])

    def test_skipping_classification_still_compiles_correctly(self):
        from django_rust_templates.engine.context import Context

        engine = Engine(libraries={"custom": "template_tests.templatetags.custom"})
        engine.template_builtins.append(stock_library())
        out = render.parse_template(
            "{% load custom %}<p>{{ name }}</p>", engine
        ).render(Context({"name": "x"}))
        self.assertEqual(out, "<p>x</p>")


class ResolvedBindingTests(unittest.TestCase):
    """The binding the code *executes*, not an alias of it that happens to share.

    Three revisions of the classifier guarded the wrong name here, and each was
    demonstrated exploitable before it was fixed: `base.token_kwargs` where
    `parse_bits` resolves `library.token_kwargs`; `library.Library` where
    `load_from_library` constructs `defaulttags.Library`; and `base.Token`
    where `call_tag_stream_free` instantiates the name bound in `host.py`. An
    alias check passes while the executed name is replaced, so these rows
    replace the executed binding and assert the classifier declines.
    """

    @classmethod
    def setUpClass(cls):
        from scripts.filter_probe import ensure_settings

        ensure_settings()

    def setUp(self):
        self.engine = Engine(libraries={"custom": "template_tests.templatetags.custom"})
        self.engine.template_builtins.append(stock_library())
        self.parser = render.parser_for("", self.engine, render.UNKNOWN_ORIGIN)
        self.host = host_module.ShellHost(self.parser)

    def test_rebinding_defaulttags_Library_declines_load(self):
        """`load_from_library` resolves `defaulttags.Library` (`defaulttags.py:1084`)."""
        original = defaulttags.Library

        class Watching(original):
            def __init__(inner, *a, **kw):
                super().__init__(*a, **kw)

        self.assertIs(engine_library.Library, original, "the alias still shares")
        defaulttags.Library = Watching
        try:
            self.assertFalse(self.host.tag_is_stream_free("load"))
        finally:
            defaulttags.Library = original

    def test_rebinding_the_dispatched_token_class_declines_a_generated_tag(self):
        original = host_module.Token

        class Watching(original):
            pass

        host_module.Token = Watching
        try:
            self.assertFalse(self.host.tag_is_stream_free("pageurl"))
        finally:
            host_module.Token = original

    def test_rebinding_the_dispatched_token_class_declines_load(self):
        """Round 8 — the dispatched token is built for *both* populations.

        `call_tag_stream_free` constructs `host.Token` before the `try` that
        stashes a compile failure, so a replaced constructor that reaches for
        the stream raises outside the stash and `last_error` never sees it.
        Guarding it only for generated tags left `load` exposed.
        """
        original = host_module.Token

        class Watching(original):
            pass

        host_module.Token = Watching
        try:
            self.assertFalse(self.host.tag_is_stream_free("load"))
        finally:
            host_module.Token = original

    def test_a_replaced_token_constructor_reaching_for_the_stream_falls_back(self):
        """End to end: the deviation must select the ordinary route and compile."""
        from django_rust_templates import _native
        from django_rust_templates.engine.context import Context

        original = host_module.Token
        seen = []
        source = "{% load custom %}<p>{{ name }}</p>" + FILLER
        parser = render.parser_for(source, self.engine, render.UNKNOWN_ORIGIN)

        class Reads(original):
            def __init__(inner, *a, **kw):
                super().__init__(*a, **kw)
                # **The parser the compile is actually running on.** Reading
                # `self.parser` here -- the empty one from `setUp` -- was the
                # defect: it never touches the stream under test, so the row
                # could not distinguish the sentinel from a real list.
                seen.append(parser.tokens)

        host = host_module.ShellHost(parser)
        host_module.Token = Reads
        try:
            template = _native.compile_template(
                source, parser, render.materialiser(), self.engine, host,
                render.UNKNOWN_ORIGIN,
            )
        finally:
            host_module.Token = original
        # It compiled, and the replacement saw a real list rather than raising
        # against the sentinel.
        self.assertIn("<p>n</p>", template.render(Context({"name": "n", "x": 1})))
        # Every observation must be a real list, never the sentinel: the
        # deviation selected the ordinary route before the callback ran.
        self.assertTrue(seen, "the replaced constructor never ran")
        for observed in seen:
            self.assertIsInstance(observed, list)


class ForeignHostCompatibilityTests(unittest.TestCase):
    """A Python host predating this optimisation must still compile, not panic.

    `_native.compile_template` takes a caller-supplied host. The trait's
    conservative default protects Rust hosts, but `FilterCheckHost` forwards to
    whatever Python object it was handed, so a host with only the pre-change
    methods must read as "not stream-free" rather than crash.
    """

    @classmethod
    def setUpClass(cls):
        from scripts.filter_probe import ensure_settings

        ensure_settings()

    def test_a_host_without_the_classifier_method_falls_back(self):
        from django_rust_templates import _native
        from django_rust_templates.engine.context import Context

        engine = Engine()
        engine.template_builtins.append(stock_library())
        source = "{% pageurl x %}" + FILLER
        parser = render.parser_for(source, engine, render.UNKNOWN_ORIGIN)
        inner = host_module.ShellHost(parser)

        class LegacyHost:
            """Only the methods the seam had before this change."""

            def __init__(self, inner):
                self._inner = inner
                self.last_error = None

            def has_tag(self, command):
                return self._inner.has_tag(command)

            def library_tag_loaded(self, command):
                return self._inner.library_tag_loaded(command)

            def call_tag(self, command, token_wire, tokens):
                return self._inner.call_tag(command, token_wire, tokens)

            def partial_node(self, name):
                return self._inner.partial_node(name)

            def register_partial_def(self, *args, **kwargs):
                return self._inner.register_partial_def(*args, **kwargs)

        template = _native.compile_template(
            source, parser, render.materialiser(), engine,
            LegacyHost(inner), render.UNKNOWN_ORIGIN,
        )
        self.assertIn("/u/", template.render(Context({"x": 1})))

    def test_a_classifier_that_raises_on_lookup_is_not_silently_declined(self):
        """Absence declines; a lookup that *raises* must stay loud."""
        from django_rust_templates import _native

        engine = Engine()
        engine.template_builtins.append(stock_library())
        source = "{% pageurl x %}" + FILLER
        parser = render.parser_for(source, engine, render.UNKNOWN_ORIGIN)
        inner = host_module.ShellHost(parser)

        class ExplodingLookup:
            def __init__(self, inner):
                self._inner = inner
                self.last_error = None

            def __getattr__(self, name):
                if name == "tag_is_stream_free":
                    raise RuntimeError("lookup exploded")
                return getattr(self._inner, name)

        # PyO3 surfaces the panic as `PanicException`, which derives from
        # `BaseException` rather than `Exception` -- loud by construction, and
        # deliberately not something an ordinary `except Exception` swallows.
        with self.assertRaises(BaseException) as caught:
            _native.compile_template(
                source, parser, render.materialiser(), engine,
                ExplodingLookup(inner), render.UNKNOWN_ORIGIN,
            )
        self.assertNotIsInstance(caught.exception, AttributeError)
        self.assertIn("lookup exploded", str(caught.exception))
