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

from django_rust_templates.engine.base import DebugLexer, Lexer, Token, TokenType  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from tests.lexer_corpus import CORPUS  # noqa: E402
from tests.parity_target import assert_target_is_not_rust_owned  # noqa: E402

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
    #
    # All four separators, deliberately. `WS_CLASS` is reachable *only* through
    # `smart_split`, and nothing else in the suite reads it — not the Rust unit
    # tests, and not the randomised source pass, which exercises the lexer's
    # `strip`. A `WS_CLASS` written `\s\x{1C}-\x{1D}\x{1F}`, dropping U+001E,
    # passed cargo and every other test; these two entries are what catch it.
    "if\x1c x",
    "a\x1cb",
    "a \x1f b",
    "\x1c",
    " \x1c ",
    'a "b\x1cc" d',
    "a\x1eb",
    "\x1c\x1d\x1e\x1f",
    "{% if x %}",
]

# Pinned so a randomised failure reproduces exactly.
RANDOM_SEED = 20261008
RANDOM_CASES = 3000
# Includes U+001C-U+001F: Python calls those whitespace and Rust does not, so
# any trimming or splitting that uses the Rust tables diverges only ever on
# inputs containing one. They belong in the alphabet for that to be reachable —
# all four, not three. This pass exercises the lexer (`pyunicode::strip`); the
# regex class is reached by `RANDOM_SPLIT_ALPHABET` below instead.
RANDOM_ALPHABET = "{}%# \naé🎉\x1c\x1d\x1e\x1f"
RANDOM_MAX_LEN = 40

# Separate alphabet for the `split_contents` pass. `_` is omitted so that a
# random concatenation cannot form `_(`, whose unterminated form raises
# `StopIteration` and is pinned deliberately elsewhere.
RANDOM_SPLIT_ALPHABET = " \t\n'\"\\ab|:.,()\x1c\x1d\x1e\x1f"


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

    def test_whitespace_table_is_exactly_pythons(self):
        """The Rust table is `White_Space | {U+001C..U+001F}` — pin it whole.

        `pyunicode::is_space` is written as `c.is_whitespace() || ...`, so it
        *inherits* Rust's table — and `char::is_whitespace` follows the Unicode
        version bundled with the toolchain, while Python follows CPython's. A
        future Unicode revision adding a `White_Space` code point would be
        picked up by the two at different times, and `is_space` would then
        silently over-trim. No `cargo test` can see that: there is no Python
        there. The guard has to live here, where both languages exist, which is
        why `_native` exports the table.

        The comparison is over the whole table, not the four separators alone.
        Asserting only that the four are whitespace — which the Rust-side unit
        test already does — cannot fail when Rust gains a code point Python does
        not have, because it never looks at anything else.

        Surrogates (U+D800-U+DFFF) are excluded rather than compared. Python's
        `str` may hold one and answers `False`; a Rust `&str` cannot, so PyO3
        rejects the argument — the divergence
        `test_lone_surrogate_raises_at_the_ffi_boundary` already records. They
        are unobservable to the engine either way, so excluding them loses
        nothing; a per-code-point `is_space` predicate would raise here instead.
        """
        rust_table = set(_native.whitespace_codepoints())
        python_table = {
            code
            for code in range(0x110000)
            if not (0xD800 <= code <= 0xDFFF) and chr(code).isspace()
        }
        self.assertEqual(
            sorted(rust_table ^ python_table),
            [],
            "code points where Rust's whitespace table and Python's disagree",
        )

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

    def test_randomised_split_contents_match(self):
        """`smart_split`'s regex over generated inputs, not hand-picked ones.

        The `WS_CLASS` substitution is the one part of the M1 whitespace fix
        that cannot be validated from Python: there the two spellings denote the
        same 29 code points, so the substitution is a no-op and *any* comparison
        passes. Only a Rust-side differential run can see it — and its sole
        coverage used to be the hand-written `SPLIT_CORPUS`, which shares an
        author with the pattern it is checking. A `WS_CLASS` missing one
        separator passed the entire suite until this was added.

        `split_contents` is `Token`'s method, so it is M1's to get right even
        though nothing renders yet.
        """
        rng = random.Random(RANDOM_SEED + 1)
        for _ in range(RANDOM_CASES):
            length = rng.randrange(RANDOM_MAX_LEN + 1)
            contents = "".join(rng.choice(RANDOM_SPLIT_ALPHABET) for _ in range(length))
            with self.subTest(contents=contents):
                self.assertEqual(
                    list(_native.split_contents(contents)),
                    Token(TokenType.BLOCK, contents).split_contents(),
                    f"contents: {contents!r}",
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

    def test_the_comparison_target_is_not_rust_owned(self):
        """Guard against this test silently becoming a self-comparison.

        M9 interposed `django.template.base`, so `Lexer` now resolves to our
        transcription rather than to Django's file — which is what the previous
        form of this row asserted, and why it had to be restated rather than
        deleted. The hazard it named is unchanged: a `Lexer` that merely
        delegates to Rust would be compared against Rust and pass no matter what
        Rust does.

        `tests/parity_target.py` asserts what this row is actually for — that
        the target is Django's file or a transcription gated against it, and
        that `Lexer`'s own code lies outside the transcription's declared
        differences. That last clause is what would catch a `Lexer.tokenize`
        delegated to Rust, which is the failure this docstring describes.
        """
        assert_target_is_not_rust_owned(self, Lexer, "Lexer")

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
