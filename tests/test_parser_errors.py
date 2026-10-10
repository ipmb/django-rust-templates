"""Syntax-error parity with Django's own parser.

Three things are compared per case: the message, the token the error is
attributed to (including its debug positions), and the fact that Django agrees
with the corpus's expectation.

The comparison target is the real `Parser` that `django.template.base` resolves
to, and `test_the_comparison_target_is_not_rust_owned` keeps that from becoming
a Rust-against-Rust comparison — see `tests/parity_target.py` for what that now
means, and why it is no longer "the target file is Django's".
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

# The vendored corpus; see the note in `tests/test_parser_parity.py`.
DJANGO_TESTS = PROJECT_ROOT / "django_suite"

for _path in (DJANGO_CHECKOUT, DJANGO_TESTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from django_rust_templates.engine import defaulttags  # noqa: E402
from django_rust_templates.engine.base import (  # noqa: E402
    UNKNOWN_SOURCE,
    DebugLexer,
    Lexer,
    Origin,
    Parser,
)
from django_rust_templates.engine.engine import Engine  # noqa: E402
from django_rust_templates.engine.exceptions import TemplateSyntaxError  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.host import ShellHost  # noqa: E402
from tests.parity_target import assert_target_is_not_rust_owned  # noqa: E402
from tests.parser_corpus import LIBRARIES  # noqa: E402
from tests.parser_error_corpus import ERRORS, ORIGIN_ERRORS  # noqa: E402
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


def django_failure(source, debug=False, origin_name=None):
    """The exception Django's own parser raises, or None.

    `origin_name` is `Origin.template_name`. A real `Parser` always carries an
    `Origin` — `Template.compile_nodelist` passes `self.origin`, which
    `Template.__init__` defaults to `Origin(UNKNOWN_SOURCE)` (`base.py:151-154`)
    — so the default here is the **unknown** origin, whose `template_name` is
    `None`, and not `origin=None`. That distinction became load-bearing in M7:
    with `Parser(..., origin=None)`, `do_extends`'s `parser.origin.template_name`
    raises `AttributeError: 'NoneType' object has no attribute 'template_name'`,
    which is a harness artifact and not something any template can reach.
    """
    parser = Parser(
        lexer(source, debug).tokenize(),
        _ENGINE.template_libraries,
        _ENGINE.template_builtins,
        Origin(UNKNOWN_SOURCE, origin_name),
    )
    try:
        parser.parse()
    except Exception as exc:  # noqa: BLE001  (StopIteration is deliberate)
        return exc
    return None


def ours_failure(source, debug=False, origin_name=None):
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

    `origin_name` crosses as the **attribute** and not as an `Origin`
    (design A3): Rust holds `Option<String>`, `None` means the unknown origin,
    and the `Origin` object itself stays on this side. The two `Parser`s above
    and below are therefore built the same way, `Origin` included — which they
    were not until M7's Task 5, and the difference was live: this one omitted
    the fourth argument, so `parser.origin` was `None` where Django's was an
    `Origin`, and `self.origin.template_name` (`base.py:603`, inside
    `extend_nodelist`) raised `AttributeError: 'NoneType' object has no
    attribute 'template_name'` for any host-compiled tag that reached
    `extend_nodelist` with `must_be_first` set or that read `parser.origin`.

    Nothing in the corpus reached it while `extends` and `include` are Rust's,
    and that is what made it worth fixing rather than working around: the row
    that first reaches it — any host tag reading `parser.origin`, which M13's
    `{% partialdef %}` work could easily introduce — would have diverged for a
    reason having nothing to do with the tag under test.
    """
    parser = Parser(
        lexer(source, debug).tokenize(),
        _ENGINE.template_libraries,
        _ENGINE.template_builtins,
        Origin(UNKNOWN_SOURCE, origin_name),
    )
    try:
        _native.parse(
            source,
            debug,
            origin_template_name=origin_name,
            compile_filter=parser.compile_filter,
            host=ShellHost(parser),
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
        # Django's own message formatting reads settings for exactly one row of
        # the corpus. The suite's own configuration (`scripts.filter_probe`),
        # reached here rather than at import time because a module-level
        # `settings.configure` changes what every test module
        # discovered after this one sees — and without this line the module only
        # passed because some *other* discovered module had configured settings
        # first: an order dependency, which is why the failure appears
        # standalone but not under `unittest discover`.
        #
        # The row is `{% block a %}x{% endblock b %}` and it is the only one of
        # the 95 that raises `ImproperlyConfigured` with settings unconfigured —
        # measured per row, with `Trans`'s function cache cleared between rows
        # because it caches the first resolved function and its docstring says a
        # `USE_I18N` change has no effect after that
        # (`.review/m7-measurements/m7_measure_gettextlist.py`).
        #
        # Why this row: its Django message is built by `Parser.invalid_block_tag`
        # -> `get_text_list(["'%s'" % p for p in parse_until], "or")`
        # (`base.py:632-648`, the `get_text_list` call inside it at 641), and it
        # is the first corpus row whose `parse_until` has **two** elements.
        # `get_text_list` returns at the top for an empty or one-element list,
        # and only the branch inside `utils/text.py:297-303` evaluates `_(", ")`
        # — *eagerly*, before `str.join` consumes the one-item generator it
        # separates. That call is what reaches `gettext` and asks for
        # `USE_I18N`; the separator it returns is then discarded. Every earlier
        # `parse_until` row passes one element (`{% spaceless %}`, line 60), so
        # this is the first row to get there at all.
        #
        # It is **not** the `last_word` default: `invalid_block_tag` passes the
        # literal `"or"`, so the `gettext_lazy("or")` at `utils/text.py:282` is
        # never evaluated, and the body's `str(last_word)` is on a plain `str`.
        # An earlier version of this comment claimed that default was the reach.
        # Both halves — the call count per row and the raising row — are
        # measured in the script above.
        from scripts.filter_probe import ensure_settings

        ensure_settings()

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

    def test_the_origin_carrying_rows_match_django(self):
        """The rows that need a template *name* on the origin — message and token.

        `construct_relative_path`'s last two failures are unreachable from a bare
        `from_string` origin, so nothing in `ERRORS` can cover them and the Rust
        side's unit tests are the only place their text is pinned until here.
        A Rust unit test pins *our* expectation; this pins Django's, which is the
        different claim the rest of the corpus makes.

        Both sides get the same name and reach it by different routes — Django
        through a real `Origin` on a real `Parser`, Rust through the bare
        `template_name` attribute — which is A3's whole content, so a row that
        passes here is evidence for the seam rather than only for the message.
        """
        for source, origin_name, expected in ORIGIN_ERRORS:
            with self.subTest(source=source, origin=origin_name):
                theirs = django_failure(source, origin_name=origin_name)
                ours = ours_failure(source, origin_name=origin_name)
                self.assertIsNotNone(theirs, "Django accepted this template")
                self.assertIsNotNone(ours, "we accepted this template")
                self.assertEqual(str(theirs), expected)
                self.assertEqual(str(ours), expected)
                self.assertEqual(token_shape(ours.token), token_shape(theirs.token))

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

    def test_the_comparison_target_is_not_rust_owned(self):
        """The Python side of this comparison must not itself be Rust.

        Restated at M9, not weakened. This row used to assert two things: that
        the target file was Django's, and that the shim was not installed in
        this process. The second is now false by construction — `check.sh`
        installs the shim for the whole unit-test session, deliberately, because
        otherwise a module binding `Parser` before the install compares against
        a different class object than one binding it after. The first stops
        holding the moment `base` is ours.

        Both were proxies for one claim, and that claim is asserted directly in
        `tests/parity_target.py`: the target is either Django's own file or a
        transcription gated against it, and the code this module actually calls
        lies outside the transcription's declared differences. That last clause
        is what still catches a leaked or newly interposed module, and it does
        so without depending on which frame installed what.
        """
        assert_target_is_not_rust_owned(self, Parser, "Parser")


if __name__ == "__main__":
    unittest.main()
