"""Syntax-error parity with Django's own parser.

Three things are compared per case: the message, the token the error is
attributed to (including its debug positions), and the fact that Django agrees
with the corpus's expectation.

The comparison target is Django's real `Parser` imported from `.django/` with no
shim in the path, and `test_the_comparison_target_is_genuinely_djangos_parser`
keeps it that way — including by checking that no other test module has left the
shim installed in this process.
"""

import inspect
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template.base import DebugLexer, Lexer, Parser  # noqa: E402
from django.template.engine import Engine  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from tests.parser_error_corpus import ERRORS  # noqa: E402

# A bare `Parser(tokens)` registers **no** tags: Django's built-ins live in
# `defaulttags`/`loader_tags` libraries, which `Engine` hands to `Parser` as
# `template_builtins`. Constructing `Parser` without them makes every built-in
# tag report `Invalid block tag`, so the comparison would be against the wrong
# Django and the corpus's real expectations (which the test checks against
# Django) would all fail. This is the same path `Engine.compile_template` takes.
# (The plan's helper omitted the libraries; corrected against Django 6.0.9,
# `base.py:522-523` — `for builtin in builtins: self.add_library(builtin)`,
# with `builtins=None` defaulting to `[]`.)
_ENGINE = Engine()


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
    """The exception `_native.parse` raises, or None."""
    try:
        _native.parse(source, debug)
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


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
