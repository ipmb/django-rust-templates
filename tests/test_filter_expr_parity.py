"""M3 Task 5's acceptance test: our `FilterExpression` parse is Django's.

Both sides run on the pinned tree — Django's real `FilterExpression`, driven by
Django's real `Parser` registry, against `_native.parse_filter_expression`.

Three things this file is arranged around, all of them deliberate:

* **The corpus is generated from the pattern's own alphabet**, not hand-picked.
  `\\w`, `.`, `+`, `-`, `|`, `:`, both quote characters and the two separators is
  the whole of what `filter_raw_string` can match, and the divergences Task 2
  exists to prevent live at exactly the code points a hand-written corpus leaves
  out. Two of those are named in `ALPHABET_NOTES` below.
* **`find_filter` and `args_check` run on the Django side through a callable**,
  because that is where Django runs them — *inside* the match loop
  (`base.py:771-772`). `Host::check_filter` documents why no batched shell-side
  pass can reproduce that, and this file is the only place the real
  `find_filter` is exercised against the seam. The arity rows are therefore the
  test's centre of gravity: a port that validated filters outside the loop, or
  before the argument's `Variable` was built, fails here and nowhere else.
* **A constant operand is compared as a value and a variable operand as a
  `Variable`**, because that is the difference Django's `lookup` flag encodes.
  `_("…")` is the exception that shaped the wire format: Django's value there is
  a `gettext_lazy` proxy, which Rust cannot build, so the wire carries the
  `Variable` in *both* branches and the materialiser does the `resolve({})`.
  `("lazy", …)` is that case named rather than skipped.
"""

import importlib.util
import inspect
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import defaultfilters  # noqa: E402
from django.template import defaulttags  # noqa: E402
from django.template.base import FilterExpression, Parser, Variable  # noqa: E402
from django.utils.functional import Promise  # noqa: E402

from django_rust_templates import _native  # noqa: E402

# Loaded from its file for the reason `tests/test_materialise.py` spells out:
# `_impl/__init__.py` is the shim's `django.template` and imports its own
# siblings, so importing the `_impl` *package* by name executes that file and
# fails for want of `engine`.
_MATERIALISER_SPEC = importlib.util.spec_from_file_location(
    "django_rust_templates._impl.parser",
    PROJECT_ROOT / "python" / "django_rust_templates" / "_impl" / "parser.py",
)
materialiser = importlib.util.module_from_spec(_MATERIALISER_SPEC)
sys.modules[_MATERIALISER_SPEC.name] = materialiser
_MATERIALISER_SPEC.loader.exec_module(materialiser)


def _parser():
    """Django's filter registry, built the way `Engine` builds it.

    `Engine.__init__` puts `django.template.defaulttags` and
    `django.template.defaultfilters` at the front of `builtins`
    (`engine.py:15-16`), and `Parser.add_library` folds each library's `filters`
    dict in (`base.py:669-671`). Reaching the same dict through
    `defaultfilters.register` skips `Engine`, which would need Django settings
    configured — and the rest of this suite deliberately runs without them
    (`tests/test_materialise.py` records what configuring them does to
    `Node.__subclasses__`). The registry is the same object either way.
    """
    return Parser([], builtins=[defaulttags.register, defaultfilters.register])


PARSER = _parser()

# `(registry name, function)` by identity, so the filter names this file compares
# are the ones Django's own registry matched rather than a guess from
# `func.__name__`. Measured: seven default filters are *not* named after their
# functions (`escape`, `escapejs`, `linebreaks`, `phone2numeric`, `slice`,
# `timesince`, `timeuntil`), so `__name__` is a wrong oracle for exactly the
# rows a corpus is most likely to include.
FILTER_NAME_BY_ID = {id(func): name for name, func in PARSER.filters.items()}


def check_filter(name, arg_count):
    """Django's two checks, in Django's order, raising Django's exceptions.

    This is what Task 6's materialiser-equivalent host does, and it is passed
    into our parse as `_native.parse_filter_expression`'s `check_filter`. The
    `[None] * arg_count` is honest rather than a shortcut: `args_check` never
    reads an element (`base.py:822-826` takes `len(provided)` and stops), which
    is why the seam carries a count and not a list.
    """
    func = PARSER.find_filter(name)
    FilterExpression.args_check(name, func, [None] * arg_count)


# --------------------------------------------------------------------------
# The alphabet, and the corpus built from it.
# --------------------------------------------------------------------------

# Every character class `[%(var_chars)s]+` can match, plus the two characters
# Django's own pattern gets wrong if the port uses the `regex` crate's `\w`.
# Mapped to what Django's `\w` — that is, Python's — matches as the *variable
# part* of `<text>|upper`, measured, because the two diverge in opposite
# directions:
#
#   "²"        U+00B2, category `No`. `str.isalnum()` is true, so Python's `\w`
#              matches it whole; the crate's `\w` does not (it wants Alphabetic,
#              `\p{M}`, `\p{Nd}` or `\p{Pc}`), so `²|upper` is a Django-accepted
#              template a naive port would reject at the variable part.
#   "á"  `a` followed by U+0301 COMBINING ACUTE ACCENT, category `Mn`. The
#              crate's `\w` includes `\p{M}`, so it matches *both* characters;
#              Python's `\w` stops after the `a`, leaving the accent as a
#              skipped run. The port must reproduce that error, not swallow it.
#
# Task 2's `PYTHON_WORD_*` constants are what make these two rows agree, and no
# other row in the alphabet can see a wrong spelling of `\w`.
ALPHABET_WORD_ROWS = {"²": "²", "á": "a"}

WORD_HEADS = [
    "x",
    "abc",
    "X9_",
    "0",  # a digit is a *variable* here, not a constant: see `constant_string`
    "9.5",
    "+5",  # `+` is a var char in this pattern, so this is `[\\w.+_-]+`, not a number
    "-5",
    ".",
    "..",
    *ALPHABET_WORD_ROWS,
    "́",
    "é",
    "_x",  # the underscore rule, reached through `Variable`
    '""',
    '"x"',
    "'x'",
    '_("x")',
    "_('x')",
]

# Real filter names with real arities, so `args_check` has something to accept
# and something to reject. Measured against the pinned registry:
# `upper(value)` and `add(value, arg)` have no defaults, while
# `floatformat(value, arg=-1)` has one — so 0, 1 and 2 provided arguments give
# an accept/accept/reject and an accept/reject/reject pair from the same corpus
# generator.
FILTERS = ["upper", "lower", "default", "join", "floatformat", "slice", "add", "nope"]
ARGUMENTS = ["", ':1', ':"s"', ":y", ":a.b", ':1:"s"']


def _corpus():
    """Every row the generators below produce, in a fixed order."""
    rows = list(WORD_HEADS)

    # The variable part with each filter and each argument count. The `nope` rows
    # are `find_filter` failures; the arity mismatches are `args_check` failures.
    for head in WORD_HEADS:
        for name in FILTERS:
            for argument in ARGUMENTS:
                rows.append(f"{head}|{name}{argument}")

    # Separator and whitespace spelling, and the chained-filter shape.
    rows += [
        "",
        "|",
        "|upper",
        "x|",
        "x||upper",
        "x|upper|",
        "x|upper||lower",
        "x|upper:",
        "x|upper::1",
        "x | upper",
        "x  |  upper   :  1",
        "x\x1c|upper",  # U+001C: `str.isspace()`, and not `\p{White_Space}`
        "x\x0b|upper",
        "x​|upper",  # zero-width space: neither, so a skipped run
        "x|u pper",
        # **The filter-*name* position of `\\w`.** The two divergences above are
        # carried in the variable position, where a wrong spelling is caught by
        # a *real* filter name following them; these rows put the same two code
        # points in the name, which is the only way to reach `filter_name` at
        # all — no real filter is named `u²`, so a corpus of real names cannot
        # see this spelling. Measured on the pinned tree, Django gives
        # `Invalid filter: 'u²'` and `Invalid filter: 'u'` respectively, against
        # a bare `\\w`'s `Invalid filter: 'u'` and `Invalid filter: 'u\\u0301'`.
        "x|u²",
        "x|ú",
        "x|²",
        "x|́",
        "x|2u",
        "x|_a",
        "x|upper!!",
        "x|nope:_bad",
        "x|nope:-5",
        "x|upper:5",
        'x|default:"d"|join:y',
        "x|upper:x|lower:y|slice:z",
        "x|upper|nope",
        "x|nope|upper",
    ]
    return rows


CASES = _corpus()


# --------------------------------------------------------------------------
# Both sides, reduced to the same plain tuple.
# --------------------------------------------------------------------------


def _ours_operand(lookup, wire):
    """One operand, as Django's `(lookup, value)` means it.

    A lookup operand is a `Variable` on both sides, so its four parse-time slots
    are compared. A constant operand is a *value* on Django's side and a
    `Variable` on the wire — see the module docstring — so the value is decoded
    out of the wire here, which is what the materialiser will do.
    """
    if lookup:
        return (
            True,
            wire.var,
            materialiser._decode_value(wire.literal, None),
            tuple(wire.lookups) if wire.lookups is not None else None,
            wire.translate,
        )
    if wire is None:
        # Reachable only through Django's `var_obj = None` sentinel
        # (`base.py:747-749`), which is unreachable for every shape the
        # `constant` group can match. Carried so the wire's `(false, None)` —
        # `var.unwrap_or((false, AttrValue::None))` — compares instead of
        # raising `AttributeError` on `None.translate`.
        return (False, None)
    if wire.translate:
        # `_("…")`: Django's value is `gettext_lazy(msgid)`, a `__proxy__`.
        return (False, "lazy")
    return (False, materialiser._decode_value(wire.literal, None))


def _django_operand(lookup, value):
    """The same reduction, from Django's side."""
    if lookup:
        return (True, value.var, value.literal, value.lookups, value.translate)
    if isinstance(value, Promise):
        # A lazy proxy, which is not a `str` and cannot be compared by value
        # without activating translations — which needs Django settings.
        return (False, "lazy")
    return (False, value)


def ours(token):
    """Our parse as `("ok", is_var, var, filters)` or `("error", message)`."""
    try:
        wire = _native.parse_filter_expression(token, check_filter)
    except _native.ParseError as error:
        return ("error", str(error))
    lookup, var = wire.var
    return (
        "ok",
        lookup,
        _ours_operand(lookup, var),
        [
            (
                name,
                [_ours_operand(arg_lookup, arg) for arg_lookup, arg in args],
            )
            for name, args in wire.filters
        ],
    )


def theirs(token):
    """Django's parse, in the same shape."""
    try:
        expression = FilterExpression(token, PARSER)
    except Exception as error:  # noqa: BLE001 - the message *is* the comparison
        return ("error", str(error))
    return (
        "ok",
        expression.is_var,
        _django_operand(expression.is_var, expression.var),
        [
            (
                FILTER_NAME_BY_ID[id(func)],
                [_django_operand(lookup, value) for lookup, value in args],
            )
            for func, args in expression.filters
        ],
    )


class FilterExpressionParityTests(unittest.TestCase):
    def test_every_case_matches_django(self):
        for token in CASES:
            with self.subTest(token=token):
                self.assertEqual(ours(token), theirs(token), f"parse differs for {token!r}")

    def test_the_alphabet_divergence_rows_actually_reach_a_word_position(self):
        """`²` and `a\\u0301` are only worth carrying if they are *word* rows.

        Both are asserted against Django's own `filter_re` rather than against
        our answer, and both divergences are named: the crate's `\\w` is
        **narrower** than Python's on `²` (it is `No`, not Alphabetic, `\\p{M}`,
        `\\p{Nd}` or `\\p{Pc}`), so a port using it would find no variable part
        at position 0 and report `Could not find variable at start of …`; and
        **wider** on `a\\u0301` (it includes `\\p{M}`), so a port using it would
        swallow the combining accent instead of reporting the skipped run. One
        constant wrong in either direction fails one of these two rows.
        """
        import re

        from django.template.base import filter_raw_string

        pattern = re.compile(filter_raw_string, re.VERBOSE)
        for text, matched in ALPHABET_WORD_ROWS.items():
            with self.subTest(text=text):
                first = next(pattern.finditer(f"{text}|upper"))
                self.assertEqual(first.group("var"), matched)

        # And the two outcomes, by name, so a reader can see what the difference
        # costs: one accepted template, one Django syntax error whose message
        # carries the skipped accent.
        self.assertEqual(ours("²|upper")[0], "ok")
        self.assertTrue(ours("²|upper")[1], "² is a Variable, not a constant")
        self.assertEqual(
            ours("á|upper"),
            theirs("á|upper"),
        )
        self.assertEqual(
            ours("á|upper")[1],
            "Could not parse some characters: a|́||upper",
        )

    def test_the_arity_rows_produce_djangos_two_check_messages(self):
        """The seam's whole reason for existing, pinned by message.

        `find_filter` runs before `args_check`, and both run *after* the
        argument's `Variable` is built — so `x|nope:_bad` reports the underscore
        error while `x|nope:-5` reports `Invalid filter`. The assertions below
        are on Django's own text, so they move if Django's does.
        """
        self.assertEqual(
            theirs("x|nope"),
            (
                "error",
                "Invalid filter: 'nope'",
            ),
        )
        self.assertEqual(ours("x|nope"), theirs("x|nope"))

        self.assertEqual(
            theirs("x|upper:5"),
            ("error", "upper requires 1 arguments, 2 provided"),
        )
        self.assertEqual(ours("x|upper:5"), theirs("x|upper:5"))

        self.assertEqual(
            theirs("x|upper:1:2"),
            ("error", "upper requires 1 arguments, 2 provided"),
        )
        self.assertEqual(ours("x|upper:1:2"), theirs("x|upper:1:2"))

        # `_bad` raises while the *argument's* `Variable` is built, which is
        # before `find_filter` is reached — so the underscore message wins even
        # though the filter name is bad too.
        self.assertEqual(
            theirs("x|nope:_bad"),
            (
                "error",
                "Variables and attributes may not begin with underscores: '_bad'",
            ),
        )
        self.assertEqual(ours("x|nope:_bad"), theirs("x|nope:_bad"))

        # `-5` parses as a literal, so control *does* reach `find_filter`, and
        # the filter error wins.
        self.assertEqual(ours("x|nope:-5"), ("error", "Invalid filter: 'nope'"))

    def test_a_lookup_argument_stays_a_variable_and_a_constant_is_resolved(self):
        """The `lookup` flag is the wire's whole reason for a `Variable` in both
        branches, and it is invisible to a length comparison."""
        ok, is_var, var, filters = ours('x|default:"d"|join:y')
        self.assertEqual((ok, is_var), ("ok", True))
        self.assertEqual(var[0], True, "the first operand is a Variable")
        # `Variable("x")`'s lookups are `("x",)` — the whole token, split on `.`.
        self.assertEqual(var[1:], ("x", None, ("x",), False))

        name, args = filters[0]
        # `(False, value)`: a constant argument, already resolved.
        self.assertEqual(name, "default")
        self.assertEqual(args[0], (False, "d"))

        name, args = filters[1]
        # `(True, Variable)`: a lookup argument, kept for the context.
        self.assertEqual(name, "join")
        self.assertEqual(args[0], (True, "y", None, ("y",), False))

    def test_a_quoted_first_operand_is_not_a_var(self):
        ok, is_var, var, filters = ours('"x"')
        self.assertEqual((ok, is_var, var), ("ok", False, (False, "x")))
        self.assertEqual(filters, [])

    def test_the_comparison_target_is_genuinely_djangos(self):
        for target in (FilterExpression, Parser, Variable):
            with self.subTest(target=target.__name__):
                source = inspect.getsourcefile(target)
                self.assertNotIn("_impl", source)
                self.assertTrue(
                    Path(source).resolve().is_relative_to(DJANGO_CHECKOUT.resolve())
                )

    def test_the_filter_names_come_from_djangos_registry(self):
        """A reverse map is only an oracle if it is injective."""
        self.assertGreaterEqual(len(PARSER.filters), 50, "the registry is not loaded")
        self.assertEqual(
            len(FILTER_NAME_BY_ID),
            len(PARSER.filters),
            "two filter names share a function object, so the reverse map is "
            "ambiguous and the name comparison above is not well defined",
        )
        # And every name the corpus uses is one the registry has, apart from the
        # deliberately-bad `nope` rows.
        for name in FILTERS:
            if name != "nope":
                with self.subTest(name=name):
                    self.assertIn(name, PARSER.filters)

    def test_the_corpus_is_not_trivially_small(self):
        # Floors, not the current counts, so adding rows is free.
        self.assertGreaterEqual(len(CASES), 400)
        self.assertEqual(len(set(CASES)), len(CASES), "duplicate corpus entries")
        # Every row must be decided by *both* sides, or the table is comparing
        # two exceptions — and an all-error corpus would pass every assertion
        # above without ever exercising a successful parse.
        ok_rows = [token for token in CASES if ours(token)[0] == "ok"]
        error_rows = [token for token in CASES if ours(token)[0] != "ok"]
        self.assertGreaterEqual(len(ok_rows), 100)
        self.assertGreaterEqual(len(error_rows), 20)
        for token in CASES:
            with self.subTest(token=token):
                theirs(token)

    def test_an_unparseable_token_is_not_an_unimplemented_tag(self):
        with self.assertRaises(_native.ParseError) as caught:
            _native.parse_filter_expression("|upper", check_filter)
        self.assertNotIsInstance(caught.exception, _native.UnimplementedTag)

    def test_without_a_host_every_filter_name_is_accepted(self):
        """The defaulted half of the seam, which keeps `cargo test
        -p template-core` runnable with no Python in the process (§10.4).

        The names are still compared — only the two Django checks are skipped —
        so this is not a weaker parse, it is the same parse minus an oracle.
        """
        with_host = ours("x|nope:1")
        self.assertEqual(with_host[0], "error")
        without = _native.parse_filter_expression("x|nope:1")
        self.assertEqual(without.filters[0][0], "nope")
        self.assertEqual(len(without.filters[0][1]), 1)


if __name__ == "__main__":
    unittest.main()
