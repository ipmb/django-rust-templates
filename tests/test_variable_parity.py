"""M3 Task 3's acceptance test: our `Variable` is Django's `Variable`.

Both sides are Django's own class — the target is `django.template.base.Variable`
imported from `.django/` with no shim in the path, and
`test_the_comparison_target_is_genuinely_djangos_variable` keeps it that way.

The comparison is over `(var, literal, lookups, translate)`, which is the whole
of what `Variable.__init__` computes and the whole of what a parse observes. The
literal is decoded through the materialiser's own `_decode_value`, so a wire
value that the materialiser cannot read is a failure here rather than a surprise
two tasks later.

Two things here are deliberate and both were measured rather than assumed:

* the literal comparison is on **identity of type** as well as equality. `5.0 ==
  5` is `True` in Python, so `assertEqual` alone cannot see an `int`/`float`
  confusion — which is exactly the mistake the `float(var)` branch invites.
* two inputs (`""` and `"_()"`) raise `IndexError` on Django and `ParseError`
  here. That is a documented divergence, pinned by name in its own test rather
  than left out of the corpus, so a reader can see it is a decision.
"""

import inspect
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.utils.safestring import SafeString  # noqa: E402

# **Django's `Variable`, held under a name the shim cannot take.** `from
# django.template.base import Variable` under the shim binds *our transcription*
# of the class — byte-for-byte upstream, which is why the comparison stayed
# meaningful, but `inspect.getsourcefile` resolves a class through
# `sys.modules[cls.__module__]`, so the guard below read whichever copy of
# `django.template.base` the test that ran before it had left installed. It was
# green in the full suite and red alone: measured, `OK` while `tests.test_shim`'s
# `tearDown` — which leaves the shim *out* — was the last thing to touch it, and
# `FAILED` as soon as a preceding module left the shim in, which M11 Task 3's
# `test_tag_inventory` interposition row does. A guard that is green because of a
# test alphabet is the failure this project keeps finding, so the target is read
# out of the pin instead, the way `tests/test_tag_inventory.py` and
# `tests/test_filter_inventory.py` do it. The class is verbatim upstream —
# `base.py`'s declared regions cover `FilterExpression.resolve` and
# `Variable._resolve_lookup`, not `Variable.__init__` (871-922) — so holding the
# pin costs nothing and removes the dependence altogether.
from tests.pinned_module import load_pinned  # noqa: E402

Variable = load_pinned("base.py").Variable

from django_rust_templates import _native  # noqa: E402

# **An ordinary import, and the by-path load it replaces was justified by a shim
# that no longer exists.** The old comment said `engine/__init__.py` was the shim's
# `django.template`, so importing the `engine` package "by name" failed for want of
# `engine`. `engine/__init__.py` now says its name "is not `django.template`", and
# `tests/pinned_module.py` above imports `base.py` out of that same package — the
# route works. The by-path load cost a **second** `parser` object in `sys.modules`
# under the ordinary name, which is the copy `render.materialiser()` may cache; see
# `test_materialiser_parity.py`'s import comment.
from django_rust_templates.engine import parser as materialiser  # noqa: E402

# The corpus. Generated from the grammar's own alphabet would be better than
# hand-picked, and Task 5 does that for `filter_re`; here the space is small
# enough to enumerate, and the rows that carry this task's real risk are named
# below so that a future reader deleting one knows which they were.
CASES = [
    # Ordinary variables, including the split on `.` and its empty fields.
    "abc",
    "a.b.c",
    "0x10",
    ".",
    "..",
    "5.5.5",
    "5_",
    "1__0",
    'a.b|default:"x"',
    # Quoted literals, and the translation marker in both of its shapes.
    '""',
    '"quoted"',
    "'q'",
    '_("x")',
    '_( "x" )',
    "_('x')",
    # Integers: whitespace, signs, underscores, and non-ASCII decimal digits.
    "5",
    " 5 ",
    "+5",
    "5\n",
    "-5",
    "١٢٣",
    "1_000",
    "١_٢",
    # **PEP 515 lexical rejections, not value questions.** `12_` here and `1__0`
    # above must stay `literal=None` with the whole token as a lookup. A
    # canonicaliser that stripped underscores *before* validating would accept
    # both and quietly turn two legal Django variable names into literals — a
    # live divergence, since `{{ 12_ }}` renders as a variable in Django.
    "12_",
    # And the same underscore rule on the **float** branch, which is a separate
    # code path: `1_0.5` is 10.5, so a float implementation that rejected `_`
    # would diverge here while every integer row above still passed.
    "1_0.5",
    # **`9223372036854775808` and `99999999999999999999` are the `BigInt` arm.**
    # Python's `int` is arbitrary-precision and these are valid templates that
    # render their own digits; saturating to `i64::MAX` renders a wrong number.
    "9223372036854775807",
    "9223372036854775808",
    "99999999999999999999",
    "-99999999999999999999",
    # Floats, in every spelling the branch can take.
    "1.5",
    ".5",
    "1e5",
    "1E5",
    "1.5e3",
    "1e400",
    "3.e5",
    # **`2.` is the assign-then-raise order.** Django converts, *then* rejects a
    # trailing dot, so the literal survives into the exception handler and the
    # token ends up with both a literal and lookups. A port that checked first
    # would leave `literal` as `None` and pass every other row here.
    "2.",
    "5.",
    # Tokens that look numeric and are not, so they reach the string branch.
    "inf",
    "nan",
    "Infinity",
    "e",
    "E",
]

ERROR_CASES = [
    # `(token, message)`. Django raises `TemplateSyntaxError` with exactly these
    # messages, and the order matters: the underscore rule runs before the
    # character rules, and `+` is reported before `-`.
    ("_x", "Variables and attributes may not begin with underscores: '_x'"),
    ("_.x", "Variables and attributes may not begin with underscores: '_.x'"),
    ("a._b", "Variables and attributes may not begin with underscores: 'a._b'"),
    ("_(", "Variables and attributes may not begin with underscores: '_('"),
    ("_()x", "Variables and attributes may not begin with underscores: '_()x'"),
    ("+", "Invalid character ('+') in variable name: '+'"),
    ("-", "Invalid character ('-') in variable name: '-'"),
    ("a+b", "Invalid character ('+') in variable name: 'a+b'"),
    ("--5", "Invalid character ('-') in variable name: '--5'"),
    ("a+b-c", "Invalid character ('+') in variable name: 'a+b-c'"),
    ("-a", "Invalid character ('-') in variable name: '-a'"),
    # **The interaction between the float branch and the character rules.**
    # `float('-2.')` succeeds, so `-2.` reaches the character loop with a literal
    # already assigned; the trailing dot makes it non-numeric, the string branch
    # finds neither a quote nor a valid name, and the `-` rule reports last. A
    # port that checked for the character rules *first* would still get this
    # message — which is why the pin that matters is `2.` above, and this row is
    # here so the two paths cannot be unified into one.
    ("-2.", "Invalid character ('-') in variable name: '-2.'"),
]


def ours(raw):
    """Our parse, as a plain tuple. Raises `_native.ParseError` on failure."""
    wire = _native.parse_variable(raw)
    return (
        wire.var,
        materialiser._decode_value(wire.literal, None),
        tuple(wire.lookups) if wire.lookups is not None else None,
        wire.translate,
    )


def theirs(raw):
    """Django's parse, as the same plain tuple."""
    variable = Variable(raw)
    return (variable.var, variable.literal, variable.lookups, variable.translate)


class VariableParityTests(unittest.TestCase):
    def test_every_case_matches_django(self):
        for raw in CASES:
            with self.subTest(raw=raw):
                self.assertEqual(ours(raw), theirs(raw), f"parse differs for {raw!r}")

    def test_the_literal_matches_by_type_as_well_as_by_value(self):
        """`5.0 == 5` is `True`, so `assertEqual` alone cannot see a mix-up.

        The `float(var)` branch is chosen by `"." in var or "e" in var.lower()`,
        and `int` and `float` compare equal across each other, so the single most
        likely port error — converting an integer token to a float, or vice
        versa — is invisible to an equality assertion. It is also invisible to
        `_decode_value`, which passes both through unchanged.
        """
        for raw in CASES:
            with self.subTest(raw=raw):
                got = materialiser._decode_value(
                    _native.parse_variable(raw).literal, None
                )
                expected = Variable(raw).literal
                self.assertIs(
                    type(got),
                    type(expected),
                    f"literal type for {raw!r}: {type(got).__name__} "
                    f"vs {type(expected).__name__}",
                )

    def test_a_quoted_literal_is_safe_on_both_sides(self):
        """`mark_safe` is what makes a literal not-escaped when it renders.

        Asserted separately from the table because `SafeString` *is* a `str`, so
        every equality check above passes for a plain `str` — the difference is
        only visible through `isinstance`, and it is user-visible output
        (`{{ "<b>" }}` renders `<b>` rather than `&lt;b&gt;`).
        """
        for raw in ['"<b>"', "'<b>'", '_("<b>")']:
            with self.subTest(raw=raw):
                self.assertIsInstance(
                    materialiser._decode_value(
                        _native.parse_variable(raw).literal, None
                    ),
                    SafeString,
                )
                self.assertIsInstance(Variable(raw).literal, SafeString)

    def test_the_error_cases_raise_djangos_message(self):
        for raw, message in ERROR_CASES:
            with self.subTest(raw=raw):
                with self.assertRaises(Exception) as caught:
                    Variable(raw)
                self.assertEqual(str(caught.exception), message, "Django's own message")

                with self.assertRaises(_native.ParseError) as caught:
                    _native.parse_variable(raw)
                self.assertEqual(str(caught.exception), message, "our message")

    def test_an_unparseable_variable_is_not_an_unimplemented_tag(self):
        """The two FFI failure kinds stay distinguishable at this entry point.

        `Unimplemented` means "this engine has not built it"; a bad variable name
        is Django's own syntax error. A harness that bucketed them together would
        score our agreement with Django as a miss. `UnimplementedTag` is a
        *subclass*, so the assertion has to be `assertNotIsInstance`, not
        `assertNotEqual` on the classes alone.
        """
        with self.assertRaises(_native.ParseError) as caught:
            _native.parse_variable("a+b")
        self.assertNotIsInstance(caught.exception, _native.UnimplementedTag)

    def test_the_two_indexerror_inputs_are_a_documented_divergence(self):
        """`""` and `"_()"` are Django bugs we do not reproduce.

        Django indexes `var[0]` in the fall-through, so both raise **`IndexError`**
        — not `TemplateSyntaxError` — on the pinned tree. Unreachable from a
        template: `Parser.parse` rejects an empty `{{ }}` before this, and
        `filter_re`'s `var` group is `[\\w\\.\\+-]+`, which cannot match `_()` as
        a whole (measured: `FilterExpression("_()")` hands `Variable` the token
        `_` and reports the underscore error). So this side returns a
        `ParseError` carrying an `Internal` invariant message, and the divergence
        is pinned *here* so that it is a decision rather than an omission.
        """
        for raw in ["", "_()"]:
            with self.subTest(raw=raw):
                with self.assertRaises(IndexError):
                    Variable(raw)
                with self.assertRaises(_native.ParseError):
                    _native.parse_variable(raw)

    def test_the_comparison_target_is_genuinely_djangos_variable(self):
        """The pin makes this a claim rather than a snapshot of `sys.modules`.

        `Variable` is `load_pinned("base.py").Variable` — the class is defined by
        the file under `.django/`, executed under the private name `pinned_base`,
        so `getsourcefile` follows `Variable.__module__` to the checkout whatever
        the shim has done to `django.template.base` in the meantime.
        """
        source = inspect.getsourcefile(Variable)
        self.assertNotIn("engine", source)
        self.assertTrue(Path(source).resolve().is_relative_to(DJANGO_CHECKOUT.resolve()))

    def test_the_corpus_is_not_trivially_small(self):
        # A corpus that lost its rows would pass every assertion above. The
        # numbers are floors, not the current counts, so adding rows is free.
        self.assertGreaterEqual(len(CASES), 35)
        self.assertEqual(len(set(CASES)), len(CASES), "duplicate corpus entries")
        self.assertGreaterEqual(len(ERROR_CASES), 10)
        # And every row must actually parse on both sides, or the table is
        # comparing two exceptions.
        for raw in CASES:
            with self.subTest(raw=raw):
                theirs(raw)


class DecimalDigitTableTests(unittest.TestCase):
    """`pynum`'s `Nd` table against CPython's `unicodedata`.

    The *only* check that can see either table move: a Rust `char` cannot hold a
    surrogate and CPython's table is not readable from Rust, so the comparison
    has to run where both languages exist.

    It fires for two independent reasons, and neither is pinned anywhere else in
    this repository:

    * a **CPython upgrade** changes `unicodedata`, and nothing here records its
      version (deliberately — see `test_pyregex_parity.py`'s note on
      `unidata_version`, which is a proxy that is wrong in both directions);
    * a **`pynum.rs` edit** that transcribes the table wrongly, which is what the
      Rust-side unit tests cannot see, because they compare the table to itself.

    **Add this to the pin-bump checklist** (§10.4) beside the whitespace guard: a
    guard whose trigger is undocumented is a guard nobody runs.
    """

    def test_the_decimal_digit_table_is_exactly_pythons(self):
        import unicodedata

        theirs = {
            code
            for code in range(0x110000)
            if unicodedata.category(chr(code)) == "Nd"
        }
        ours_table = set(_native.decimal_digit_codepoints())
        self.assertEqual(
            sorted(ours_table - theirs),
            [],
            "code points our table calls a digit and Python does not, for "
            f"unicodedata {unicodedata.unidata_version}",
        )
        self.assertEqual(
            sorted(theirs - ours_table),
            [],
            "code points Python calls a digit and our table does not, for "
            f"unicodedata {unicodedata.unidata_version}",
        )
        # The counts, so a `pynum.rs` edit that dropped a range fires even if a
        # CPython upgrade replaced it with an equivalent one.
        self.assertEqual(len(ours_table), 760 if unicodedata.unidata_version == "16.0.0" else 680, "our Nd table's size")

    # Chunks read through the parser rather than a new binding: a run of `Nd`
    # code points is a literal whose value *is* the digit reading of each one,
    # concatenated. The middle two name the only two ranges in Python's table
    # that hold more than one digit cycle — measured over all 0x110000 code
    # points, `U+116D0..U+116E3` holds two cycles and `U+1D7CE..U+1D7FF` five —
    # and they are the only chunks here that can fail: reading the value as
    # `code - first` rather than `(code - first) % 10` gives 1011 and 1023 where
    # the truth is 1 and 9, and every single-cycle range agrees either way.
    DIGIT_CHUNKS = (
        "١٢٣",  # Arabic-Indic, one cycle: the plain case
        "\U000116d0\U000116d1",  # Myanmar Pao, first cycle
        "\U000116da\U000116db",  # the same range's second cycle
        "\U0001d7ce\U0001d7d9",  # mathematical bold 0 then double-struck 1
        "\U0001d7f6\U0001d7ff",  # monospace 0 through bold 5's range's last
    )

    def test_the_digits_our_table_names_are_the_ones_python_names(self):
        """`decimal_digit_value` is a *value* claim, and the table is not.

        A range can hold several digit cycles (`U+1D7CE..U+1D7FF` is five of
        them), so the value is read as `(code - first) % 10` rather than
        `code - first`. That is a claim about each code point, not about the
        ranges, and the only oracle for it is `unicodedata.decimal`.
        """
        import unicodedata

        for chunk in self.DIGIT_CHUNKS:
            with self.subTest(chunk=chunk):
                # Named explicitly: a chunk that drifted onto a non-digit — as
                # these three did before they were measured — raises here rather
                # than silently dropping out of the corpus.
                if not all(unicodedata.category(ch) == "Nd" for ch in chunk):
                    # Unicode 16 added the Myanmar Pao digits. Earlier interpreters
                    # must parse these as variable names, rather than integers.
                    self.assertIsNone(_native.parse_variable(chunk).literal)
                    continue
                expected = int(
                    "".join(str(unicodedata.decimal(ch)) for ch in chunk)
                )
                self.assertEqual(
                    materialiser._decode_value(
                        _native.parse_variable(chunk).literal, None
                    ),
                    expected,
                )

    def test_the_corpus_actually_exercises_the_modulo(self):
        """The multi-cycle rows are the test's whole point, so pin them.

        If a `pynum.rs` edit collapsed each range to `code - first`, every other
        assertion in this file still passes — the single-cycle chunks agree
        under both readings. These two rows are what makes the difference
        observable, and the expected values are restated here so that a future
        edit to `DIGIT_CHUNKS` cannot quietly remove the only failing input.
        """
        # `code - first` over this range's second cycle would read these two
        # code points as 10 and 11, giving the literal 1011 rather than 1.
        self.assertEqual(
            materialiser._decode_value(
                _native.parse_variable("\U000116da\U000116db").literal, None
            ),
            1 if __import__("unicodedata").unidata_version == "16.0.0" else None,
        )
        self.assertEqual(
            materialiser._decode_value(
                _native.parse_variable("\U0001d7f6\U0001d7ff").literal, None
            ),
            9,
        )


if __name__ == "__main__":
    unittest.main()
