"""Syntax-error parity with Django's own parser.

Three things are compared per case: the message, the token the error is
attributed to (including its debug positions), and the fact that Django agrees
with the corpus's expectation.

The comparison target is Django's real `Parser` imported from `.django/` with no
shim in the path, and `test_the_comparison_target_is_genuinely_djangos_parser`
keeps it that way — including by checking that no other test module has left the
shim installed in this process.
"""

import ast
import inspect
import re
import sys
import textwrap
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

DJANGO_TESTS = DJANGO_CHECKOUT / "tests"

for _path in (DJANGO_CHECKOUT, DJANGO_TESTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from django.template import defaulttags  # noqa: E402
from django.template.base import DebugLexer, Lexer, Parser  # noqa: E402
from django.template.engine import Engine  # noqa: E402
from django.template.exceptions import TemplateSyntaxError  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.host import ShellHost  # noqa: E402
from tests.parser_corpus import LIBRARIES  # noqa: E402
from tests.parser_error_corpus import ERRORS  # noqa: E402
from tests.test_tag_inventory import M5_TAGS  # noqa: E402

# A bare `Parser(tokens)` registers **no** tags: Django's built-ins live in
# `defaulttags`/`loader_tags` libraries, which `Engine` hands to `Parser` as
# `template_builtins`. Constructing `Parser` without them makes every built-in
# tag report `Invalid block tag`, so the comparison would be against the wrong
# Django and the corpus's real expectations (which the test checks against
# Django) would all fail. This is the same path `Engine.compile_template` takes.
# (The plan's helper omitted the libraries; corrected against Django 6.0.9,
# `base.py:522-523` — `for builtin in builtins: self.add_library(builtin)`,
# with `builtins=None` defaulting to `[]`.)
#
# `libraries=` is the second half of the same omission and arrived with M6's
# Task 5: `Parser.__init__` stores `libraries` without merging it (`base.py:521`)
# and only `{% load %}` reads it, so a row that loads a library needs the engine
# to name one. The dict is `parser_corpus.LIBRARIES`, shared with the success
# corpus, because two spellings of it is how one of them stops being updated.
_ENGINE = Engine(libraries=LIBRARIES)


def lexer(source, debug):
    return DebugLexer(source) if debug else Lexer(source)


def django_failure(source, debug=False):
    """The exception Django's own parser raises, or None."""
    parser = Parser(
        lexer(source, debug).tokenize(),
        _ENGINE.template_libraries,
        _ENGINE.template_builtins,
    )
    try:
        parser.parse()
    except Exception as exc:  # noqa: BLE001  (StopIteration is deliberate)
        return exc
    return None


def ours_failure(source, debug=False):
    """The exception `_native.parse` raises, or None.

    `compile_filter` is passed because M5's `{% if %}` needs it: the operand's
    `FilterExpression` is built *during* the parse, by the host, and the host
    refuses to guess one (`Host::compile_filter`). `check_filter` stays out —
    only one of the two seams is new here, and leaving the other `None` (its
    permissive default) keeps every earlier row's filter-error ordering exactly
    as it was measured.

    The parser is a filter registry and nothing else: Rust owns the tokens, so
    the ones this `Parser` is built over are irrelevant, and the same
    construction as `django_failure`'s is the one that has the engine's filters.

    `host` arrived with M6's Task 5, for the reason `ours_parse` in
    `test_parser_parity` records: without it `{% load %}` is not a tag Rust
    knows, so every M6 error row would fail with `Invalid block tag on line 1:
    'load'` instead of the message under test — a corpus that agrees with itself
    about the wrong thing.
    """
    parser = Parser(
        lexer(source, debug).tokenize(),
        _ENGINE.template_libraries,
        _ENGINE.template_builtins,
    )
    try:
        _native.parse(
            source, debug, compile_filter=parser.compile_filter, host=ShellHost(parser)
        )
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


def compile_function_mentions(tag, name):
    """Whether the compile function Django registers for `tag` mentions `name`.

    Located through Django's own registry and then read out of the pinned source
    by line, **not** by a `do_<tag>` naming guess — the convention does not hold,
    and the guess failed on exactly the two tags that would have hidden it:
    `cycle` (`defaulttags.py:612`) and `url` (`defaulttags.py:1515`) are
    top-level functions under their own names with no `do_` prefix, so guessing
    would have reported "raises nothing" for both and quietly widened the exempt
    set from one tag to three. That is this project's recurring shape — a
    plausible convention standing in for a measurement — and it is why the lookup
    is by registry.

    The AST rather than a substring search, so the name has to appear as an
    identifier: `TemplateSyntaxError` in a comment or a docstring is not a raise,
    and this is an exemption's justification, where the difference matters.
    """
    func = inspect.unwrap(defaulttags.register.tags[tag])
    module = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return any(
        (isinstance(node, ast.Name) and node.id == name)
        or (isinstance(node, ast.Attribute) and node.attr == name)
        for node in ast.walk(module)
    )


def token_shape(token):
    """The comparable fields of a token, from either implementation.

    Ours crosses the FFI as the same 4-tuple `_native.lex` returns; Django's is
    a real `Token`, whose type is a plain `Enum` rather than an `IntEnum` (so
    `.value` is the comparable part).
    """
    if token is None:
        return None
    if isinstance(token, tuple):
        return token
    return (token.token_type.value, token.contents, token.lineno, token.position)


class ErrorParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # `Engine()` reads settings, and so does Django's own message formatting
        # for one row of the corpus. Configured here rather than at import time
        # because a module-level `settings.configure` changes what every test
        # module discovered after this one sees.
        #
        # The row that needs it is `{% block a %}x{% endblock b %}`, whose
        # Django message is built by `Parser.invalid_block_tag` ->
        # `get_text_list(["'%s'" % p for p in parse_until], "or")`
        # (`base.py:632-648`, the `get_text_list` call inside it is at line 641).
        # `get_text_list` defaults `last_word` to `gettext_lazy("or")`
        # (`utils/text.py:282`) and joins with `_(", ")`, so *formatting*
        # the lazy string is what reaches the translation layer and asks for
        # `USE_I18N`. Every earlier corpus row passed a one-element list, which
        # returns at the top of the function before any of that runs
        # (`utils/text.py:297-303`) — so this is the first row that needs
        # settings, and without this line the module only passed because some
        # *other* discovered module had configured them first (an order
        # dependency, which is why the failure appears standalone but not under
        # `unittest discover`).
        from django.conf import settings

        if not settings.configured:
            settings.configure(USE_I18N=False)

    def test_the_corpus_matches_django(self):
        """The corpus's expectations are Django's, not the author's."""
        for source, expected in ERRORS:
            with self.subTest(source=source):
                failure = django_failure(source)
                self.assertIsNotNone(failure, "Django accepted this template")
                self.assertEqual(str(failure), expected)

    def test_every_case_raises_the_same_message(self):
        for source, expected in ERRORS:
            with self.subTest(source=source):
                ours = ours_failure(source)
                self.assertIsNotNone(ours, "we accepted this template")
                self.assertEqual(str(ours), expected)

    def test_every_case_carries_the_same_token(self):
        for source, _ in ERRORS:
            with self.subTest(source=source):
                self.assertEqual(
                    token_shape(ours_failure(source).token),
                    token_shape(django_failure(source).token),
                )

    def test_every_case_carries_the_same_token_in_debug_mode(self):
        # Positions only exist in debug mode, which is what makes this a
        # different test rather than a slower one.
        for source, _ in ERRORS:
            with self.subTest(source=source):
                ours = token_shape(ours_failure(source, debug=True).token)
                theirs = token_shape(django_failure(source, debug=True).token)
                self.assertEqual(ours, theirs)
                self.assertIsNotNone(ours, "debug parsing must still fail")

    def test_the_exception_types_differ_by_design(self):
        # Design decision 2: Rust owns the message and the token; Python owns the
        # exception object. `_native` raises its own `ParseError` and the shell
        # converts it to `TemplateSyntaxError` at M9, so the *type names* differ
        # on purpose while everything a test reads about them agrees.
        self.assertEqual(type(django_failure("{% nope %}")).__name__, "TemplateSyntaxError")
        self.assertIs(ours_failure("{% nope %}").__class__, _native.ParseError)

    def test_the_innermost_token_wins(self):
        # The outer token is on line 1 and the message alone cannot tell the two
        # apart, so this is the case a naive `error()` passes (Review Focus
        # item 5).
        source = "{% autoescape on %}\n{% now a b %}\n{% endautoescape %}"
        self.assertEqual(django_failure(source).token.lineno, 2)
        self.assertEqual(ours_failure(source).token[2], 2)

    def test_an_unterminated_marker_is_a_stop_iteration_for_both(self):
        # Django reaches `next(bits)` on an exhausted generator and lets
        # `StopIteration` out of `parse`, caught by `except Exception` only so the
        # token is attached. Ours raises it from the FFI boundary with no
        # arguments, so `str()` is empty on both sides.
        source = '{% now _("unterminated %}'
        theirs = django_failure(source)
        ours = ours_failure(source)
        self.assertIsInstance(theirs, StopIteration)
        self.assertIsInstance(ours, StopIteration)
        self.assertEqual(str(theirs), str(ours))
        self.assertEqual(token_shape(theirs.token), token_shape(ours.token))

    def test_a_filter_tag_with_no_argument_is_a_value_error_for_both(self):
        # The second exception whose *class* is Django's rather than ours, and
        # for the same reason as the `StopIteration` above: `do_filter` unpacks
        # `token.contents.split(None, 1)` with no length check
        # (`defaulttags.py:744`), and Django's `parse` catches `Exception`, not
        # `TemplateSyntaxError`, so the class survives the trip out of the
        # parser.
        #
        # The assertion is needed *in addition* to the `ERRORS` row, and this is
        # the point of it: the corpus compares `str()` and the token, so a
        # `TemplateSyntaxError` carrying the same text — which is what a port
        # that converted the failure would raise — passes every comparison
        # there. Only the class names the divergence.
        source = "{% filter %}x{% endfilter %}"
        theirs = django_failure(source)
        ours = ours_failure(source)
        self.assertIsInstance(theirs, ValueError)
        self.assertIsInstance(ours, ValueError)
        self.assertNotIsInstance(ours, _native.ParseError, "not a parse error here")
        self.assertEqual(str(theirs), str(ours))
        self.assertEqual(token_shape(theirs.token), token_shape(ours.token))

    def test_a_host_exception_keeps_its_class_across_the_seam(self):
        """`badtag` raises `RuntimeError`, and nothing that reads text can tell.

        Django's `Parser.parse` re-raises the *same object* after
        `Parser.error` attaches a token (`base.py:583-585`), so a compile
        function that raises something other than `TemplateSyntaxError` leaves
        Django as that class. The seam used to flatten it — every assertion
        above compares `str()` or the token, and `"I am a bad tag"` is the same
        text either way — so this is the only test here that can see it.

        The stakes are the acceptance suite, not taste:
        `.django/tests/template_tests/tests.py:114-123` wraps
        `Engine.from_string("{% load bad_tag %}{% badtag %}")` in
        `assertRaises(RuntimeError)`. Unreachable until M9 routes
        `Engine.from_string` through this parser; the class crosses the FFI as
        data (`ParseError.class_name`) until then, which is what is asserted.
        """
        source = "{% load bad_tag %}{% badtag %}"
        theirs = django_failure(source)
        ours = ours_failure(source)
        self.assertIsInstance(theirs, RuntimeError)
        self.assertNotIsInstance(theirs, TemplateSyntaxError)
        self.assertIs(ours.__class__, _native.ParseError)
        self.assertEqual(
            ours.class_name,
            type(theirs).__name__,
            "the class Django raises has to survive the seam by name, because "
            "design decision 2 keeps the exception object on the Python side",
        )
        self.assertEqual(str(theirs), str(ours))
        self.assertEqual(token_shape(theirs.token), token_shape(ours.token))

    def test_a_host_exception_with_no_class_of_its_own_says_so(self):
        # The control for the row above, and the reason `HostError::class` is an
        # `Option`: an error this side raised without Python — `Syntax` here —
        # has no class to name, and `None` says that rather than guessing
        # `"TemplateSyntaxError"`, which is the M9 shell's mapping to make and
        # not the parser's to presume.
        self.assertIsNone(ours_failure("{% nope %}").class_name)

    def test_the_error_corpus_covers_every_m5_tag_that_can_fail_to_compile(self):
        """The error-side mirror of the success-corpus coverage test.

        Derived from `M5_TAGS` and **not** from the stub set, which is the one
        place this deliberately differs from its success-side twin. A tag's
        message literal is written by the task that lands the tag, so demanding
        the row only once the tag is implemented would make this assertion empty
        exactly while the plan was claiming to close vacuous gates. Keyed to the
        plan instead, it is red from the plan's first task and turns green one
        tag at a time — the same shape as the milestone switch, and for the same
        reason.

        A row per tag, not a row per message: exact wording is what
        `test_the_corpus_matches_django` and each implementing task's own steps
        pin, and a coverage gate is a different claim from a wording claim.
        """
        for tag in sorted(M5_TAGS - {"ifchanged"}):
            with self.subTest(tag=tag):
                covered = any(
                    re.search(r"\{%\s*" + re.escape(tag) + r"\b", source)
                    for source, _ in ERRORS
                )
                self.assertTrue(
                    covered, f"{tag} can fail at compile time but no ERRORS row covers it"
                )

    def test_the_m5_error_exemption_is_derived_from_djangos_source(self):
        """`ifchanged` is exempt because Django's own compile raises nothing.

        Not because the corpus forgot it: every compile-time failure `ifchanged`
        can produce comes from the base parser it drives (`parser.parse`,
        `parser.next_token`), which M3 already pins, so it has no message literal
        of its own for `ERRORS` to carry. Derived from the pinned source rather
        than asserted in prose, so a pin bump that gives `ifchanged`'s compile
        function a raise of its own reddens here and the exemption gets revisited — which is the
        only way an exemption stays honest.
        """
        raising = {
            tag
            for tag in M5_TAGS
            if compile_function_mentions(tag, "TemplateSyntaxError")
        }
        self.assertEqual(M5_TAGS - raising, {"ifchanged"})

    def test_successful_templates_raise_nothing(self):
        # The control: a corpus that every parser rejects proves nothing about
        # messages, so this pins that these four do parse.
        for source in ["", "hello", "{% csrf_token %}", "{% verbatim %}x{% endverbatim %}"]:
            with self.subTest(source=source):
                self.assertIsNone(django_failure(source))
                self.assertIsNone(ours_failure(source))

    def test_the_comparison_target_is_genuinely_djangos_parser(self):
        from django_rust_templates import _shim

        source = inspect.getsourcefile(Parser)
        self.assertNotIn("_impl", source)
        self.assertTrue(Path(source).resolve().is_relative_to(DJANGO_CHECKOUT.resolve()))
        # A leaked install from another test module would make this comparison
        # circular the moment we provide a `base` module, which M3 does.
        self.assertFalse(_shim.installed(), "the shim is installed in this process")


if __name__ == "__main__":
    unittest.main()
