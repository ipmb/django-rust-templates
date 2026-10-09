"""Parity between the Rust lexer and Django's own.

This is M1's acceptance test. It runs Django's real `Lexer` and `DebugLexer`
imported straight from the pinned checkout — no shim involved — and compares
their token stream against `_native.lex` tuple for tuple.

Comparing the *debug* lexer matters most: it pins character positions, which is
where a byte-offset implementation diverges silently.
"""

import inspect
import random
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template.base import DebugLexer, Lexer, Token, TokenType  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from tests.lexer_corpus import CORPUS  # noqa: E402

# Token contents for `split_contents`, kept separate from CORPUS because these
# are tag *contents*, not whole templates.
SPLIT_CORPUS = [
    'sometag _("Page not found") value|yesno:_("yes,no")',
    "if x > 1",
    "for i in items",
    "",
    "   ",
    'a "b c" d',
    "a 'b c' d",
    r'a "b\"c" d',
    r'a"b',
    'value|default:"a b"',
    "_(\"outer _('inner') outer\")",
    'x _("a") _("b") y',
    # `smart_split` is `re.finditer` over a pattern written with `\s`/`\S`.
    # Python's `\s` is `isspace()`-based and so matches U+001C-U+001F; the
    # `regex` crate's is White_Space and does not. These pin the difference.
    "if\x1c x",
    "a\x1cb",
    "a \x1f b",
    "\x1c",
    " \x1c ",
    'a "b\x1cc" d',
    "{% if x %}",
]

# Pinned so a randomised failure reproduces exactly.
RANDOM_SEED = 20261008
RANDOM_CASES = 3000
# Includes U+001C-U+001F: Python calls those whitespace and Rust does not, so
# any trimming or splitting that uses the Rust tables diverges only ever on
# inputs containing one. They belong in the alphabet for that to be reachable.
RANDOM_ALPHABET = "{}%# \naé🎉\x1c\x1d\x1f"
RANDOM_MAX_LEN = 40


def django_tokens(source, debug):
    lexer = DebugLexer(source) if debug else Lexer(source)
    return [
        (t.token_type.value, t.contents, t.lineno, t.position)
        for t in lexer.tokenize()
    ]


def rust_tokens(source, debug):
    return [tuple(t) for t in _native.lex(source, debug)]


class LexerParityTests(unittest.TestCase):
    def assert_matches(self, source, debug):
        expected = django_tokens(source, debug)
        actual = rust_tokens(source, debug)
        self.assertEqual(
            actual,
            expected,
            f"\nsource:   {source!r}\n"
            f"debug:    {debug}\n"
            f"django:   {expected}\n"
            f"rust:     {actual}",
        )

    def test_corpus_matches_non_debug(self):
        for source in CORPUS:
            with self.subTest(source=source):
                self.assert_matches(source, debug=False)

    def test_corpus_matches_debug(self):
        for source in CORPUS:
            with self.subTest(source=source):
                self.assert_matches(source, debug=True)

    def test_the_suite_own_lexer_case_matches(self):
        """The exact string Django's test_base.py asserts on."""
        source = (
            "text\n"
            "{% if test %}{{ varvalue }}{% endif %}"
            "{#comment {{not a var}} {%not a block%} #}"
            "end text"
        )
        self.assert_matches(source, debug=False)
        self.assert_matches(source, debug=True)

    def test_corpus_is_not_trivially_small(self):
        self.assertGreaterEqual(len(CORPUS), 40)

    def test_randomised_sources_match(self):
        """Generated sources, because the corpus shares an author with the lexer.

        CORPUS only probes divergences its author thought of; a divergence
        neither author imagined passes it silently. Generating sources from a
        small alphabet of the characters that matter — tag delimiters, percent,
        hash, newline, space, and characters that are multi-byte in UTF-8 — is
        the only mechanism here that can reach that class.

        The seed is pinned so a failure reproduces exactly, and mismatch reports
        come from ``assert_matches``, which prints the source.
        """
        rng = random.Random(RANDOM_SEED)
        for _ in range(RANDOM_CASES):
            length = rng.randrange(RANDOM_MAX_LEN + 1)
            source = "".join(rng.choice(RANDOM_ALPHABET) for _ in range(length))
            for debug in (False, True):
                with self.subTest(source=source, debug=debug):
                    self.assert_matches(source, debug)

    def test_token_type_names_round_trip(self):
        """The FFI carries an int; the name is part of the contract.

        ``Token.__repr__`` renders ``token_type.name.capitalize()``, and that repr
        is asserted by ``test_parser.py:36`` and
        ``i18n/test_blocktranslate.py:881``. The int is the right wire format —
        this pins that it maps back to the right name.
        """
        names = [
            TokenType(token[0]).name.capitalize()
            for token in _native.lex("a{% b %}{{ c }}{# d #}")
        ]
        self.assertEqual(names, ["Text", "Block", "Var", "Comment"])

    def test_lone_surrogate_raises_at_the_ffi_boundary(self):
        """A documented difference between the two implementations.

        A Python ``str`` may legally contain a lone surrogate; a Rust ``&str``
        cannot, so PyO3 rejects the argument where Django's lexer processes it.
        Not expected anywhere in the suite — recorded so that it is recognised
        rather than rediscovered, and so the difference is a decision rather
        than a surprise. ``ValueError`` covers PyO3's decode/encode errors,
        which are both subclasses.
        """
        with self.assertRaises(ValueError):
            _native.lex("a\ud800b")

    def test_split_contents_matches(self):
        """`split_contents` is a `Token` method, so it is M1's to get right."""
        for contents in SPLIT_CORPUS:
            with self.subTest(contents=contents):
                self.assertEqual(
                    list(_native.split_contents(contents)),
                    Token(TokenType.BLOCK, contents).split_contents(),
                )

    def test_split_contents_unterminated_marker_raises_the_same_way(self):
        """Django lets `next(bits)` raise `StopIteration` here; so must we.

        An obscure path, but it is the one place `split_contents` can fail, and
        matching the exception type is cheaper than discovering the difference
        in a traceback later.
        """
        contents = 'tag _("unterminated'
        with self.assertRaises(StopIteration):
            Token(TokenType.BLOCK, contents).split_contents()
        with self.assertRaises(StopIteration):
            _native.split_contents(contents)

    def test_comparison_target_is_genuinely_djangos_lexer(self):
        """Guard against this test silently becoming a self-comparison.

        Once M9 interposes `django.template.base`, importing `Lexer` below will
        resolve to our shell instead of Django's, and the parity test would then
        be comparing the Rust lexer against Python that merely delegates to it —
        passing no matter what the Rust lexer does. Fail loudly at that point so
        the comparison is rebuilt against the real source file.
        """
        source_file = inspect.getsourcefile(Lexer)
        self.assertTrue(
            source_file.startswith(str(DJANGO_CHECKOUT)),
            f"parity target is not the pinned checkout: {source_file}",
        )

    def test_a_lexer_error_is_not_silently_swallowed(self):
        """Both implementations must agree on error behaviour too.

        `Lexer(None)` raises on `len()`; ours receives a `str` only, so the
        interesting case is that neither crashes on the corpus above. This test
        pins that errors surface rather than produce empty output.
        """
        with self.assertRaises(TypeError):
            _native.lex(None)


if __name__ == "__main__":
    unittest.main()
