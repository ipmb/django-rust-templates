"""M4's filter corpus: the inputs the parity test compares Django and Rust on.

Each row is one `FilterExpression.resolve` on each side — Django's real
`FilterExpression` over a real `Context`, and `_native.filter` over the same
input — compared on `(type name, value)` and, separately, on safety.

**Why a corpus file at all, when Django's own `template_tests` is the acceptance
criterion.** Two reasons, and neither is redundancy:

* the suite runs against the *shim*, so it tests the engine through
  `django.template`; this compares the filter seam directly, which is what makes
  a failure name the filter and the input instead of a test module and a line;
* and it compares **safety** and **type**, which a rendered template mostly does
  not. `upper` and `lower` produce the same text either way, and differ in
  whether the result is a `SafeString` — a difference that shows up only later,
  as an escaping decision, in a test that then looks like it is about escaping.

**An absent `arg` is the call `func(obj)` and `"arg": None` is the call
`func(obj, None)`** — two different calls, not two spellings of one, because
Django builds `arg_vals` (`base.py:804-809`) and spreads it
(`base.py:813`/`base.py:815`), and the list's *length* is the distinction.
So the key's presence is the whole
signal, which is why the harness reads it through `tests/test_filter_parity.py`'s
`case_arg` — `case["arg"]` when the key is there, `NO_ARG` when it is not —
rather than `.get("arg")`, which collapsed the two and scored a raise as an
answer. Measured on the pinned tree, **three** of the 23 landed filters that take
an argument answered where Django raises: `floatformat` and `pluralize`, whose
bodies were handed the absent-argument value in place of the argument, and
`unordered_list`, whose raise is a different artefact entirely (`base.py:813`;
see [`ERROR_CASES`]). The two tokens are `x|f` and `x|f:None`, and
`scripts/filter_probe.py` takes the choice from the command line the same way —
omitting the argument is the first, passing the literal `None` the second.

**An `arg` that is not a `str` is a bare token and arrives unmarked**, which is
a different input from the same text quoted. Measured on the pinned 6.0.9:
`x|ljust:5` hands the filter the `int` 5, where `x|ljust:"5"` hands it
`SafeString('5')` — a quoted argument is a *constant* and `resolve`'s loop
`mark_safe`s it (`base.py:800-806`), while a bare one is a `var_arg` resolved by
`Variable`. `filter_probe.rust_answer` mirrors both, so a row may declare an
`int` and have both sides receive an `int`. It did not, until Task 5: the
harness marked every argument, and no row could see it because every earlier
row's argument was a string.

**The `value` is a real Python object, not a literal to be parsed.** That is what
lets a row be `mark_safe("AbC")` — and the safety rows are the point of this
file. A textual format would have needed a second, private encoding for
`safestring` and then a reason to trust it.

**The optional keys change what the row *is*, and each exists because the default
would quietly score a different question.** They are `value_factory`, `context`,
`settings`, `arg_variable`, `membership` and `expected`, and they are **named here
rather than counted**: this sentence said "four" while the file read six, and a
number in prose that nothing checks is a defect this corpus has been fixed for
more than once. `value_factory` replaces `value` for a **one-shot** input — a
generator is consumed by whichever side reads it first, so a shared one makes the
second side's answer a statement about an exhausted iterator; see [`case_value`].
`context` overrides the `Context` the loop reads `autoescape` and `use_tz` off,
which is how the `needs_autoescape` filters get both branches: `join` with and
without escaping answers different text from the same input, so nothing but the
key can tell the two rows apart. `arg_variable` carries an argument of a shape a
template cannot spell as a constant, which is what `pluralize`'s `[1]` and `[","]`
need (see `arg_token`); `membership` replaces the equality assertion with a
population test for the filters whose answer is chosen at random.

**`settings` is the key whose rows must carry `expected`.** It is an
`override_settings` entered around **both** engines' calls
— the only way a setting can be an input at all, since a body cannot see one: its
whole contact with the outside world is the host trait. Because both sides get
the same override, a misspelt setting name is a **silent no-op** that moves both
answers together and leaves every comparison green — measured, `USE_THOUSAND_SEPERATOR`
(the natural typo) moved nothing anywhere — so on these rows the pinned literal
is the test and not a nicety. `floatformat`'s separator, `filesizeformat`'s unit
words and `yesno`'s default arguments are the three seams the key reaches; see
`scripts/filter_probe.py` for why the harness runs with `USE_I18N=True` and
`django.setup()`, without which the last two are invisible.

**`expected` pins the literal, and it is for the rows a comparison cannot fail.**
The comparison is two engines agreeing; where a broken body moves **both** engines
the same way it moves the answer and the comparison together, and the row stays
green. That is exactly what a lost `context` key does — a body that never reads
`autoescape` agrees with a Django also told to ignore it — so such a row carries
the text it must produce and the harness asserts the literal **as well as** the
comparison. This is the same rule, for the same reason, as the four `expected`
strings in `tests/test_render_parity.py`: comparing two runs that changed
together would keep passing, and on these rows the string is the whole claim.
Measured: dropping the key escapes the items on both sides of
`join/autoescape-false-does-not-escape`, and escapes every level of
`unordered_list/autoescape-false-nested` — those two are pinned below.

**Carrying a key is not depending on it**, and only the rows that *depend* are
pinned. `join/typeerror-returns-the-value-not-escaping` has an `autoescape` key
and answers the int `5` whether it is read or not, so a literal there would pin
nothing the comparison does not already see — the row is the `autoescape=False`
half of the failing branch, not a detector. Measured, dropping the key from each
of the three `context` rows changes the answer for **two** of them and not for
this one.

**A `CASES` row cannot carry an exception, and an error therefore has two homes
rather than one.** `resolve` propagates an exception rather than returning it, so
a `CASES` row whose *point* is that Django raises would abort the comparison
instead of making it — which is why there is an [`ERROR_CASES`] list below. The
list does not, however, *replace* the body tests the filters already have, and
the split between the two homes is by **who authors the message**:

* **A message passed through from CPython** — a real host call's refusal, like
  `first(5)`'s `'int' object is not subscriptable` or `random({...})`'s
  `KeyError: 0` — is Python's on both sides, so no comparison of the two can be
  wrong about its wording. What a *body* test adds is **propagation**: that the
  body lets the host's error escape rather than swallowing it into `""` or a
  default. That test asserts the class and never the message, and it is the only
  place an agent can see the behaviour — §10.4 gives an agent
  `cargo test -p template-core` and never `maturin develop`, so a Python-side row
  is invisible to the party writing the filter. `ERROR_CASES` then adds what only
  the integration can see: the real host's text, against Django's, pinned.
* **A message synthesised in Rust** — `cut.rs`'s `not_a_string`, which exists
  because no host operation performs the `str.replace` — is a value this engine
  *composes* rather than *obtains*. No body test can validate one: the body
  produces the string and the test restates it, so the two can only agree.
  `ERROR_CASES` is the only check that exists on such a message, and the body test
  beside it is a restatement to **delete** rather than to keep.

**That is the third form of one rule, and it is the rule that governs this whole
file: parity validates agreement, never correctness.** Shared inputs are invisible
to it — a row handing both engines one generator scores the second against an
exhausted iterator, and a dropped `context` key moves both engines together (see
[`case_value`] and `expected`) — and so are values one side composes, because
there is nothing on the other side for them to agree with. Each time this has
bitten M4 it has arrived looking like a new problem. It is the same one.

**The fourth form is not about a row at all, and it is the one that was live for
two waves: the harness's own configuration is a shared input too.** Until Task 7's
third wave, `filter_probe.ensure_settings` configured `USE_I18N=False` and never
called `django.setup()`, so every `gettext`/`ngettext` returned its msgid — to
*both* engines. A body that inlined the English literal therefore scored exactly
as well as one that asked the host, and every `gettext`-dependent row in waves 1
and 2 was not weak but **inert**: it would have stayed green through a body that
deleted the host call outright. No row can carry this, because the row is not
where the input lives. The defense is the one below — a pinned literal on every
settings-bearing row — and the reason it is stated as a rule rather than as a
nicety is that the failure mode is a *silent* one: measured,
`USE_THOUSAND_SEPERATOR=True` (one letter transposed) moves nothing anywhere, so
a typo'd setting name leaves a row green and asserting nothing.

The rows whose input survives the filter's own `try` are a different thing and
stay here, where they are common: the `dictsort` rows that end in `""` are three
different caught arms.

Rows for a filter arrive in **the commit that lands its group** (§D6), so the
set of filters named here is a statement about M4's progress as well as about
correctness — which is why `test_filter_parity.py` checks both directions of it
rather than only comparing the rows it is given.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from django.utils.functional import lazy
from django.utils.safestring import mark_safe

# Django's own lazy proxy: **not** `SafeData`, but `str()` of it is a `SafeString`.
#
# This object is the seam `@stringfilter` exists on, and the only operand where
# Django's two safe-marking sites disagree. `Variable.resolve` hands a filter the
# `__proxy__` itself, so the loop's test at `base.py:816` is `False`; the wrapper
# then coerces `str(first)` and tests *that*, which is `True`. A body handed the
# raw value — or a wrapper that tested the raw value — answers plain `str` where
# Django answers `SafeString`, and the two render identically, so nothing but a
# comparison of this kind can see it.
#
# Built with `lazy(...)()` rather than written as a small class, because the
# interesting thing is that this is Django's own object: a hand-written
# not-`SafeData`-but-`str()`-is stand-in would pin our reading of the contract
# rather than the contract. It constructs with settings unconfigured, which is
# what lets it live at module scope here.
LAZY_SAFE_PROXY = lazy(lambda: mark_safe("AbC"), str)()


class DOT_ATTR:
    """An object with an attribute and **no** `__getitem__`.

    `dictsort`'s resolver tries `value[part]` first and falls back to `getattr`,
    so the fallback is only reachable by an object whose subscript *raises* — a
    dict and a list both answer the first branch. Measured: `dictsort` over these
    sorts by `.x`, so a body that never fell back would raise `TypeError` here.
    """

    def __init__(self, x):
        self.x = x

    def __repr__(self):
        return f"DOT_ATTR({self.x!r})"

    def __eq__(self, other):
        return isinstance(other, DOT_ATTR) and other.x == self.x


class STR_NOT_REPR:
    """An object whose `__str__` is not its `__repr__`.

    `unordered_list`'s escaper is `"%s" % item`, which is `__str__`. A body that
    rendered the `repr` — or that escaped the `Value` rather than its string —
    answers `REPR-SENTINEL` here. Django's own `test_ulitem` is this row.
    """

    def __str__(self):
        return "STR-SENTINEL"

    def __repr__(self):
        return "REPR-SENTINEL"

    def __eq__(self, other):
        return isinstance(other, STR_NOT_REPR)


class LOUD_STR:
    """An object whose `__str__` raises a class `builtins` does not hold.

    `@stringfilter`'s coercion is `str(value)` (`defaultfilters.py:38-52`), so
    the exception this raises is the filter's answer, and Django propagates it
    unchanged. The class is defined *here* — a test module's own — precisely
    because it is not in `builtins`: the FFI used to rebuild an exception from
    its class *name*, and a name `builtins` cannot instantiate came back as a
    plain `Exception` whose text began with the name (measured on the pinned
    tree: `builtins.Exception: BoomError: loud` where Django raises `BoomError`).
    See `ERROR_CASES`'s `upper/raising-str` row and
    `crates/template-py/src/filters.rs`'s `host_error`.
    """

    class BoomError(Exception):
        """The class that cannot be found by name."""

    def __str__(self):
        raise LOUD_STR.BoomError("loud")

    def __repr__(self):
        return "LOUD_STR()"


class LOUD_REPR:
    """An object whose `repr()` raises — the input `pprint`'s `except` arm renders.

    `pprint` (`defaultfilters.py:990-996`) is `pformat(value)` inside a
    `try`, and the arm answers
    `"Error in formatting: %s: %s" % (e.__class__.__name__, e)`. Reaching it needs
    a `__repr__` that raises, which is why [`LOUD_STR`] cannot stand in: its
    `__repr__` returns a string, so `pformat` *succeeds* on it and a row built on
    it would assert the successful path twice while looking like it tested the
    arm. The class name is `BoomError`, the same as `upper/raising-str`'s, so both
    rows say the same thing about the name the seam carries — it is the
    exception's own `__class__.__name__`, not a name `builtins` can instantiate.
    """

    class BoomError(ValueError):
        """An `Exception` subclass, so `except Exception` catches it."""

    def __repr__(self):
        raise LOUD_REPR.BoomError("boom repr")


class DATE_WITHOUT_DAY:
    """The value that survives only `date`'s **second** branch.

    `date`'s branch 1 is `formats.date_format(value, arg)`, whose default
    resolves `get_format(None)` to `'N j, Y'` (`en-us`), and this object has no
    `day`, so branch 1 raises `AttributeError` at `j`. Its branch 2 is
    `dateformat.format(value, arg)` — the **raw** formatter, with no `get_format`
    — so an absent argument is Python's `None` `str()`d to `'None'`, whose four
    characters are all live format chars: `'N'` → `'Oct.'`, `'o'` → the ISO year,
    `'n'` → the month number, `'e'` → empty for a non-`datetime`. Measured on the
    pinned 6.0.9: `{{ x|date }}` is `'Oct.202610'`.

    **This is the only row that pins branch 2 receiving the sentinel's text.**
    A body that spelled branch 2 as `date_format(value, Some("DATE_FORMAT"))`
    resolves the default and answers `''` here — a silent wrong answer for this
    class of input, not untidiness, which is why the seam's `dateformat_format`
    takes `&Value` rather than `&str`.

    `isocalendar()` returns a real `IsoCalendarDate` rather than a bare tuple,
    because `DateFormat.o` reads `.isocalendar().year`: a tuple raises
    `AttributeError` and the row would answer `''` for the wrong reason —
    measured while writing this row, and the reason the fixture is written this
    way rather than the obvious way.
    """

    month = 10
    year = 2026

    def isocalendar(self):
        return date(2026, 10, 8).isocalendar()

    def tzname(self):
        return "UTC"


def case_value(case):
    """The row's input, **built fresh for each side**.

    A row declares `value_factory` instead of `value` when the input cannot be
    shared, and there is exactly one such input class: **a one-shot iterator**.
    A generator is consumed by whichever side reads it first, so handing the same
    object to Django and then to Rust makes the second side's answer a statement
    about an exhausted generator rather than about the filter — measured, and it
    cost a false divergence report: `["a", (x for x in ["b"])]` nests under
    Django and, with the *same spent object*, does not under ours. The filter was
    right the whole time; the row was the bug.

    `value_factory` rather than "call `value` if it is callable", because a
    callable is a legitimate filter *input* — `x|default:func` resolves a
    function — and a row that meant to test one would be silently read as a
    factory. The two are different keys so they cannot be confused.
    """
    if "value_factory" in case:
        return case["value_factory"]()
    return case["value"]


def _typeerror_from_membership(value) -> str:
    """CPython's own text for ``"-" not in value`` — **derived from the value**.

    The [`ERROR_CASES`] row `language_name/non-string-value` pins Django's
    refusal of a non-`str` language code, and *that* text is **CPython's**, not
    Django's: Django supplies only the call site
    (`django/utils/translation/__init__.py:279`, `if "-" not in lang_code:`),
    whose expression this function reproduces on the row's own value.
    **Derived, not frozen, and the reason is ownership, not stability**: this
    text is a *carrier* — the claim is that it travelled through the port
    unchanged — where Django's `KeyError` text is the row's *subject* and is
    frozen. CPython re-worded it at **3.14** (3.9-3.13: `is not iterable`;
    3.14.4: `is not a container or iterable`), which made the question visible.

    **The value is a parameter, not a literal, because the message interpolates
    the input's type name.** `7` gives `'int'`, `7.5` gives `'float'`, and so on,
    so a literal here would hold the row's input in two places with nothing to
    keep them together: change the row to `7.5` and it would redden on a type
    name for a reason unrelated to transport. The pair
    `_LANGUAGE_NAME_NON_STRING_VALUE` / the derived text is written once and
    used by both the row's `value` and its `message`, which is the same rule the
    differential harness applies to `value_factory`.

    (Illustrate with *values*, never class objects: `"-" not in float` and
    `"-" not in object` interpolate `'type'`, not `'float'`/`'object'`, and
    `"-" not in NoneType` is a `NameError` — `NoneType` is `type(None).__name__`,
    not a name bound anywhere.)

    Two adjacent line numbers, kept apart because different trees own them:
    :279 is the `in` test that raises the `TypeError` (this function's
    expression), and :280 is `raise KeyError("Unknown language code %s." %
    lang_code)`, which produces the `KeyError` half. That text *is* Django's, so
    its row freezes it literally.
    """
    try:
        "-" not in value  # mirrors translation/__init__.py:279, on the row's value  # noqa: B018
    except TypeError as error:
        return str(error)
    raise AssertionError(
        f"`'-' not in {value!r}` did not raise `TypeError`; the interpreter's "
        f"behaviour changed and this derivation must be re-measured against it"
    )


#: The non-string value the `language_name/non-string-value` row feeds the filter
#: **and** the value its expected `TypeError` text is derived from — one name for
#: both, so the input's type name in the message cannot drift from the input.
_LANGUAGE_NAME_NON_STRING_VALUE = 7
_LANGUAGE_NAME_NON_STRING_TYPEERROR = _typeerror_from_membership(
    _LANGUAGE_NAME_NON_STRING_VALUE
)


# `lower` is `is_safe=True` and `upper` is `is_safe=False` — measured, and the
# reason the two are in this file together rather than one per group. Same
# operand, same shape of answer, opposite safety: a table where only one of them
# is `mark_safe`d is what catches a loop that applied the flag unconditionally,
# or read it off the *result* rather than the value the filter was handed.
CASES: list[dict] = [
    # --- upper: `str.upper`, per code point, is_safe=False -----------------
    {
        "name": "upper/ascii",
        "filter": "upper",
        "value": "abc",
    },
    {
        "name": "upper/empty",
        "filter": "upper",
        "value": "",
    },
    {
        # `ß` uppercases to `SS`: the answer is longer than the input, so a body
        # that mapped code point to code point would be right for every ASCII
        # input and wrong here.
        "name": "upper/growing-mapping",
        "filter": "upper",
        "value": "straße",
    },
    {
        # The `@stringfilter` coercion (§D3): `upper(5)` is `"5"`, not a
        # `TypeError`. Neither this row nor the next can be expressed as a
        # literal-parse of a template, because the *type* of the input is the
        # thing under test.
        "name": "upper/int-coerces",
        "filter": "upper",
        "value": 5,
    },
    {
        "name": "upper/none-coerces",
        "filter": "upper",
        "value": None,
    },
    {
        # Beria Erfe U+16EBB, one of the 26 code points Unicode 17.0 assigned:
        # Python 3.14.4 has no name for it and maps it to itself, Rust's tables
        # map it to U+16EA0, and `pyunicode::case` suppresses Rust's answer. This
        # row is the one that fails if a body ever reaches for the raw table, so
        # it is not decoration — `upper_str` and `char::to_uppercase` differ here
        # and nowhere a casual corpus would look.
        "name": "upper/beria-erfe-assignment",
        "filter": "upper",
        "value": "\U00016ebb",
    },
    {
        # U+A7D3, one of the 2 retro-fitted mappings: Python *names* the
        # character, and maps it to itself; 17.0 assigned the capital U+A7D2 and
        # retro-fitted the mapping. A different mechanism from the row above,
        # with the same shape of answer.
        "name": "upper/retro-fitted-mapping",
        "filter": "upper",
        "value": "ꟓ",
    },
    {
        # Safe in, plain out: `upper` is `is_safe=False`, so neither half of the
        # safe-marking (`@stringfilter`'s or the loop's) may fire. Compared on
        # `type(x).__name__`, this row is `("str", "ABC")` and the `lower` row
        # below is `("SafeString", "abc")`.
        "name": "upper/safe-input-stays-plain",
        "filter": "upper",
        "value": mark_safe("AbC"),
    },

    # --- lower: the host's `str.lower`, Final_Sigma, is_safe=True ----------
    {
        "name": "lower/ascii",
        "filter": "lower",
        "value": "AbC",
    },
    {
        "name": "lower/empty",
        "filter": "lower",
        "value": "",
    },
    {
        # Final_Sigma, and the finding that changed the design: `"ΟΣ".lower()` is
        # `"ος"` and not `"οσ"`, so the answer depends on what surrounds the `Σ`
        # and no per-code-point table can express it. This is why there is no
        # `lower` in `pyunicode::case` and why the body must go through the host.
        "name": "lower/final-sigma",
        "filter": "lower",
        "value": "ΟΣ",
    },
    {
        # The same rule where it does *not* fire: the first `Σ` is word-internal,
        # so it is `σ` while the second is `ς`. A body that special-cased "the
        # last character" rather than the context passes the row above and fails
        # this one.
        "name": "lower/final-sigma-only-final",
        "filter": "lower",
        "value": "ΣΣ",
    },
    {
        # Dotted capital I: two code points out for one in, through the *lower*
        # direction, which is the case `pyunicode::case`'s single-`char` return
        # could not have carried even if `lower` had stayed Rust's.
        "name": "lower/dotted-i-grows",
        "filter": "lower",
        "value": "İ",
    },
    {
        "name": "lower/int-coerces",
        "filter": "lower",
        "value": 5,
    },
    {
        # Safe in, safe out: `lower` is `is_safe=True`, so the loop marks the
        # result. Together with `upper/safe-input-stays-plain` this is the pair
        # that pins `FilterFlags::is_safe` reaching the loop at all, which is the
        # "incl. `FilterFlags` round-tripping" half of M4's acceptance criterion.
        "name": "lower/safe-input-stays-safe",
        "filter": "lower",
        "value": mark_safe("AbC"),
    },
    {
        # The lazy proxy, and the only row that fails if the `@stringfilter`
        # coercion is asked of the wrong value. Django answers
        # `SafeString("abc")`: the loop's `isinstance(obj, SafeData)` is `False`
        # (a `__proxy__` is not `SafeData`) and the wrapper's
        # `isinstance(str(first), SafeData)` is `True`. Dropping the wrapper's
        # half — or asking it of the uncoerced value — gives plain `str("abc")`:
        # the same text, the same `==`, and a different answer to what the
        # template does with it next. This is a safety row in a file whose whole
        # reason for existing is the safety half.
        "name": "lower/lazy-proxy-whose-str-is-safe",
        "filter": "lower",
        "value": LAZY_SAFE_PROXY,
    },
    # --- capfirst: `value[0].upper() + value[1:]`, is_safe=True --------------
    {
        # `value and …`: the empty string is falsy, so it is returned as-is
        # rather than rebuilt. A body that unconditionally did
        # `first.upper() + rest` would raise on the empty string, so this row is
        # a crash guard as much as a value one.
        "name": "capfirst/empty",
        "filter": "capfirst",
        "value": "",
    },
    {
        "name": "capfirst/ascii",
        "filter": "capfirst",
        "value": "hello",
    },
    {
        # `value[0].upper()` is a **code point** mapping, so the answer can be
        # longer than the input. A body that mapped code point to code point
        # would be right for every ASCII input and wrong here.
        "name": "capfirst/growing-mapping",
        "filter": "capfirst",
        "value": "ßx",
    },
    {
        # U+A7D3 is one of `pyunicode::case::UNCASED`'s 28 entries: Python 3.14.4
        # maps it to itself (measured) where Rust's tables map it to U+A7D2, so the
        # shared table suppresses Rust's answer and the input comes back
        # unchanged. It is a *second* code point from `upper/beria-erfe-assignment`'s
        # list rather than the same one — that row is U+16EBB, a Unicode 17.0
        # assignment — which is what makes the suppression reachable from two
        # filters and not just one body's test.
        "name": "capfirst/suppressed-code-point",
        "filter": "capfirst",
        "value": "ꟓ",
    },
    {
        "name": "capfirst/int-coerces",
        "filter": "capfirst",
        "value": 5,
    },
    {
        # `capfirst` is `is_safe=True`, so the *loop* marks the result: the body
        # returns a plain `Value::Str` and never calls `mark_safe` itself.
        "name": "capfirst/safe-input-stays-safe",
        "filter": "capfirst",
        "value": mark_safe("abc"),
    },
    {
        # The lazy proxy through a second `is_safe=True` body. This comment
        # originally claimed the row observed the wrapper's *replacement* half;
        # the §11 mutation measured that it does not. Handing the body the raw
        # value while the marking still asks the coerced one left **this row
        # green** and took `cut/lazy-proxy…` red — `capfirst` never reads its
        # input's safety, so nothing about which value the body received can
        # reach its answer. What the row observes is the marking half: the loop's
        # condition is `was_safe || coerced_is_safe`, and on a `__proxy__` only
        # the second disjunct is true. `lower/lazy-proxy…` observes the same line
        # through another body; both are kept because a row is cheap and this
        # pair is what makes the disjunct's reach filter-independent rather than
        # a property of one name. The replacement half has exactly one observer
        # in M4 — `cut`, the only body in Django that reads `isinstance(value,
        # SafeData)` — and that row is `cut/lazy-proxy-whose-str-is-safe`.
        "name": "capfirst/lazy-proxy-whose-str-is-safe",
        "filter": "capfirst",
        "value": LAZY_SAFE_PROXY,
    },
    # --- title: the host's `str.title()`, then two Rust regexes, is_safe=True -
    {
        "name": "title/ascii",
        "filter": "title",
        "value": "hello world",
    },
    {
        # The Final_Sigma answer, and the reason `title` is a host call: a Rust
        # per-code-point rule cannot produce `ς` — it needs the context, and it
        # needs Unicode's `Cased` property and titlecase table besides. A body
        # that reimplemented `str.title` passes every ASCII row and fails this.
        "name": "title/final-sigma",
        "filter": "title",
        "value": "ΟΣ",
    },
    {
        "name": "title/final-sigma-mid-word",
        "filter": "title",
        "value": "ΟΣΑ",
    },
    {
        "name": "title/apostrophe",
        "filter": "title",
        "value": "o'connor",
    },
    {
        # The first regex's actual work: `str.title()` answers `"They'Re"`, and
        # `([a-z])'([A-Z])` lowercases the whole three-character match. Dropping
        # that pass leaves this row red while every non-apostrophe row stays green.
        "name": "title/apostrophe-uppercase-after",
        "filter": "title",
        "value": "they're",
    },
    {
        # The second regex: `"abc1Def".title()` is `"Abc1Def"` and `\d([A-Z])`
        # lowercases the `"1D"`. `\d` is Unicode `\p{Nd}` in Rust's regex as in
        # Python's, which is why the two select the same 760 code points.
        "name": "title/digit-then-uppercase",
        "filter": "title",
        "value": "abc1Def",
    },
    {
        "name": "title/int-coerces",
        "filter": "title",
        "value": 5,
    },
    # --- cut: the only body that reads its input's safety, is_safe absent ----
    {
        # A plain `str` is not `SafeData`, so the result is a plain `str` even
        # though the argument is not `";"`. A body that marked safe whenever it
        # had replaced something fails here.
        "name": "cut/plain-string",
        "filter": "cut",
        "value": "AbC",
        "arg": "b",
    },
    {
        # Safe in, safe out, for the ordinary argument. This row is a *control*
        # for the proxy row below rather than a guard: `was_safe` is already true
        # here, so handing the body the uncoerced value changes nothing. Keep it —
        # it covers the body's marking path — but the guard is the proxy.
        "name": "cut/safe-string-is-marked-safe",
        "filter": "cut",
        "value": mark_safe("AbC"),
        "arg": "b",
    },
    {
        # The `arg != ";"` half of the condition, which is the half a body is
        # most likely to drop: the value *is* safe, and the answer is still a
        # plain `str`. Note it also pins the comparison against a converted
        # primitive: `SafeString(";") != ";"` is `False` in Python, so a body
        # comparing the raw values would flip this branch.
        "name": "cut/safe-string-semicolon-argument",
        "filter": "cut",
        "value": mark_safe("a;b"),
        "arg": ";",
    },
    {
        # **The guard for the wrapper's other half**, and the reason this row set
        # cannot be trimmed for speed. Django answers `SafeString("AC")`: the
        # loop's `isinstance(obj, SafeData)` is `False` on a `__proxy__`, and the
        # wrapper's `isinstance(str(first), SafeData)` is `True`, so `cut`'s
        # `safe` is true only because the body was handed the *coerced* value.
        # Hand the body the raw proxy and it answers plain `"AC"` — same text,
        # same `==`, different type — which is the entire observable difference
        # the coercion makes anywhere in M4 until a body reads its own input's
        # safety, and `cut` is the only body in Django that does.
        "name": "cut/lazy-proxy-whose-str-is-safe",
        "filter": "cut",
        "value": LAZY_SAFE_PROXY,
        "arg": "b",
    },
    {
        # The `@stringfilter` coercion of the *value*: `cut(5, "5")` is `""`,
        # because the body's input is `"5"` before it replaces anything. A body
        # that read `arg` out of the value would fail this and pass the row above.
        "name": "cut/int-value-coerces",
        "filter": "cut",
        "value": 5,
        "arg": "5",
    },
    # --- addslashes: three `replace`s, is_safe=True --------------------------
    {
        "name": "addslashes/nothing-to-escape",
        "filter": "addslashes",
        "value": "abc",
    },
    {
        "name": "addslashes/apostrophe",
        "filter": "addslashes",
        "value": "I'm",
    },
    {
        "name": "addslashes/quotes",
        "filter": "addslashes",
        "value": 'say "hi"',
    },
    {
        # The ordering guard: a backslash *before* a quote. Escaping the quote
        # first and the backslash second would escape the backslash this step
        # introduced, giving one more `\` than Django. The pairs are what make the
        # order observable at all — a single-character input cannot see it.
        "name": "addslashes/backslash-then-quote",
        "filter": "addslashes",
        "value": 'a\\"b',
    },
    # --- make_list: `list(str(value))`, is_safe=False ------------------------
    {
        "name": "make_list/empty",
        "filter": "make_list",
        "value": "",
    },
    {
        "name": "make_list/ascii",
        "filter": "make_list",
        "value": "abc",
    },
    {
        # Code points, not graphemes and not bytes: `e` + U+0301 is two
        # elements. A body that split on `bytes` or on grapheme clusters is right
        # for every ASCII row and wrong here.
        "name": "make_list/combining-mark-is-two-elements",
        "filter": "make_list",
        "value": "é",
    },
    {
        "name": "make_list/int-coerces",
        "filter": "make_list",
        "value": 5,
    },
    # --- wordcount: `len(value.split())`, is_safe=False ----------------------
    {
        "name": "wordcount/empty",
        "filter": "wordcount",
        "value": "",
    },
    {
        "name": "wordcount/collapses-runs",
        "filter": "wordcount",
        "value": "  a  b  ",
    },
    {
        # The four information separators U+001C..U+001F are separate characters
        # for Python's `str.split()` (measured: `"a\x1cb".split()` is `["a","b"]`)
        # and **not** whitespace for Rust's `char::is_whitespace`, which follows
        # Unicode's `White_Space` property (measured: `"a\u{1c}b"
        # .split_whitespace()` is one item). So these rows are the pair that
        # separates the host's splitter from `split_whitespace` — a body using the
        # latter answers `1` where Django answers `2`.
        "name": "wordcount/information-separator-u001c",
        "filter": "wordcount",
        "value": "a\u001cb",
    },
    {
        "name": "wordcount/information-separator-u001f",
        "filter": "wordcount",
        "value": "a\u001fb",
    },
    {
        # U+00A0 **is** a separator for Python's `str.split()` — measured,
        # `"a\xa0b".split()` is `["a","b"]` — which is what a splitter built on
        # `is_ascii_whitespace`, or a plain `split(' ')`, gets wrong. Rust's
        # `split_whitespace` happens to agree here (NBSP is `White_Space`), so this
        # row guards the *ASCII-only* mistake rather than the `split_whitespace`
        # one the two rows above guard.
        "name": "wordcount/nbsp-separates",
        "filter": "wordcount",
        "value": "a b",
    },
    {
        "name": "wordcount/int-coerces",
        "filter": "wordcount",
        "value": 5,
    },

    # --- Task 5: padding and truncation, all is_safe=True --------------------
    #
    # **This group's rows carry a weight the earlier ones did not.** The four
    # truncation filters and `wordwrap` are *delegating* bodies: they compute
    # integers in Rust and hand the text to a host call, and `TestFilterHost`
    # records the method **name** and not its arguments. Measured by mutation —
    # flipping `html: true` to `false` in `truncatewords_html` leaves all six of
    # its unit tests green. So the arguments that distinguish the four
    # `truncate` filters from each other are pinned *here* or nowhere: one row
    # per distinct triple, plus one for `wrap`'s width, and each is named for
    # the argument it protects.
    #
    # Safety: all eight are `is_safe=True`, so the loop's `mark_safe` applies to
    # each. Four rows below cover it — one per distinct call shape a body has
    # (padding, `truncate` words=false, `truncate` words=true, `wrap`) — rather
    # than eight, because the flag reaches the loop by the same line for every
    # `is_safe=True` filter and the remaining four share a shape with one of
    # these. That is the one place in this file where a row stands for another.
    # --- center (`defaultfilters.py:431-438`): the format spec `^`, NOT `str.center`
    {
        # **The row the plan mandates, and the only one that separates the two
        # rules.** CPython's format spec puts the extra space on the **right**
        # when the margin and the width are both odd (`left = margin // 2`);
        # `str.center` carries a `margin & width & 1` term that moves it left.
        # Django answers `' ab  '` and `'ab'.center(5)` answers `'  ab '` — a
        # body written from the builtin's name passes every even-margin row
        # above and fails only here. Measured against the pinned 6.0.9.
        "name": "center/both-odd-width",
        "filter": "center",
        "value": "ab",
        "arg": 5,
    },
    {
        # The control: margin 3 at width 6, where both rules agree. Kept beside
        # the row above so a future reader can see which one carries the
        # discriminating power rather than having to re-derive it.
        "name": "center/even-margin",
        "filter": "center",
        "value": "abc",
        "arg": 6,
    },
    {
        # A 1-code-point, 2-byte value at width 4 → margin 3 → `' é  '`. A body
        # counting bytes gets margin 2 and answers 3 wide.
        "name": "center/counts-code-points-not-bytes",
        "filter": "center",
        "value": "é",
        "arg": 4,
    },
    {
        "name": "center/empty-value",
        "filter": "center",
        "value": "",
        "arg": 5,
    },
    {
        # `width <= 0` returns the value **unchanged**
        # (`defaultfilters.py:435-437`), and the guard runs *after* `int(arg)`,
        # so `center("abc", 0)` is `'abc'` and `center("abc", "x")` raises
        # rather than returning. The pair below
        # pins the value half; the raising half is a Rust body test, because a
        # corpus row has no exception path to compare.
        "name": "center/zero-width-is-the-value",
        "filter": "center",
        "value": "abc",
        "arg": 0,
    },
    {
        "name": "center/negative-width-is-the-value",
        "filter": "center",
        "value": "abc",
        "arg": -3,
    },
    {
        # The `@stringfilter` coercion, as in the `upper` rows but through a
        # body that also parses the argument: `center(5, 3)` is `' 5 '`.
        "name": "center/int-coerces",
        "filter": "center",
        "value": 5,
        "arg": 3,
    },
    {
        # Width **4**, deliberately: at width 5 this row would be a second
        # both-odd case and would silently share the split-rule observation with
        # `center/both-odd-width` above. Measured — the §11 mutation of
        # `left = margin / 2` into `str.center`'s rule moved exactly two rows
        # until this argument was changed, and exactly one after. A row that
        # observes two things makes the mutation count say "2" for a defect with
        # one named observer, which is the ambiguity this project has paid for
        # four times already.
        "name": "center/safe-input-stays-safe",
        "filter": "center",
        "value": mark_safe("ab"),
        "arg": 4,
    },
    # --- ljust / rjust (`defaultfilters.py:417-428`): the builtins, with no
    #     `<= 0` guard
    {
        # **The argument is Python's `int`, not Rust's `str::parse`.** `int`
        # accepts PEP 515 underscores, so `ljust("abc", "5_0")` is a 50-wide
        # string. A body using `parse::<usize>()` answers `'abc'` here and
        # agrees on every row where the argument is a plain number — which is
        # every other row in this file.
        "name": "ljust/argument-is-python-int",
        "filter": "ljust",
        "value": "abc",
        "arg": "5_0",
    },
    {
        # `int(" 5 ")` is 5: Python strips whitespace, `parse` does not.
        "name": "ljust/argument-tolerates-whitespace",
        "filter": "ljust",
        "value": "abc",
        "arg": " 5 ",
    },
    {
        # **This row is what makes `filter_probe.rust_answer`'s argument-shape
        # rule observable.** A non-`str` argument is a bare token and arrives as
        # the `float` 5.0, which `int()` converts; a harness that `mark_safe`d
        # every argument would hand the Rust side `SafeString('5.0')`, whose
        # `to_int` raises `ValueError` — and `ljust` has **no** `try`, so the
        # row fails loudly instead of comparing two different inputs. It is the
        # only row here whose argument is a `float` rather than an `int`.
        "name": "ljust/float-argument-is-resolved-not-quoted",
        "filter": "ljust",
        "value": "abc",
        "arg": 5.0,
    },
    {
        # Code points, not bytes: `"e" + U+0301` is one grapheme, two code
        # points, three bytes, and pads to 5 as two code points plus three
        # spaces. Written with the escape because an invisible combining mark is
        # exactly what an editor mangles.
        "name": "ljust/counts-code-points-not-bytes",
        "filter": "ljust",
        "value": "éé",
        "arg": 5,
    },
    {
        # `str.ljust` returns the string unchanged for any width at or below the
        # length — the reason `ljust` needs no `<= 0` guard where `center` has
        # one. Three rows, because the three cases reach that clamp by different
        # arithmetic and a body that clamped only one would pass the others.
        "name": "ljust/width-below-length-is-the-value",
        "filter": "ljust",
        "value": "abc",
        "arg": 2,
    },
    {
        "name": "ljust/zero-width-is-the-value",
        "filter": "ljust",
        "value": "abc",
        "arg": 0,
    },
    {
        "name": "ljust/negative-width-is-the-value",
        "filter": "ljust",
        "value": "abc",
        "arg": -1,
    },
    {
        "name": "ljust/empty-value-at-a-negative-width",
        "filter": "ljust",
        "value": "",
        "arg": -5,
    },
    {
        "name": "rjust/pads-on-the-left",
        "filter": "rjust",
        "value": "abc",
        "arg": 6,
    },
    {
        "name": "rjust/zero-width-is-the-value",
        "filter": "rjust",
        "value": "abc",
        "arg": 0,
    },
    {
        "name": "rjust/negative-width-is-the-value",
        "filter": "rjust",
        "value": "abc",
        "arg": -5,
    },
    {
        "name": "rjust/empty-value-at-a-negative-width",
        "filter": "rjust",
        "value": "",
        "arg": -5,
    },
    # --- truncatechars / truncatewords
    #     (`defaultfilters.py:305-313`, `defaultfilters.py:330-341`)
    {
        # The ellipsis **counts toward the length**: 3 gives two characters plus
        # `…`. A body that truncated to `length` and then appended would be one
        # character long here.
        "name": "truncatechars/ellipsis-counts-toward-the-length",
        "filter": "truncatechars",
        "value": "abcdefgh",
        "arg": 3,
    },
    {
        "name": "truncatechars/ascii",
        "filter": "truncatechars",
        "value": "abcdefgh",
        "arg": 5,
    },
    {
        # `Truncator` normalises to NFC and does not count combining marks
        # (`text.py:205`), so the decomposed pair answers as the composed one:
        # `'éé'` at length 2, not the ellipsis. A Rust `chars()` count answers
        # `'…'` — the third Unicode table the plan declines to write.
        "name": "truncatechars/combining-marks-do-not-count",
        "filter": "truncatechars",
        "value": "éé",
        "arg": 2,
    },
    {
        # `int(arg)` in a `try/except ValueError` that **returns the value
        # unchanged**. It is a corpus row rather than a body test precisely
        # because the failure is silent: there is no exception to compare, and
        # the silence *is* the observable.
        "name": "truncatechars/non-integer-argument-is-silent",
        "filter": "truncatechars",
        "value": "abcdefgh",
        "arg": "x",
    },
    {
        "name": "truncatechars/zero-length-is-empty",
        "filter": "truncatechars",
        "value": "abcdefgh",
        "arg": 0,
    },
    {
        "name": "truncatechars/negative-length-is-empty",
        "filter": "truncatechars",
        "value": "abcdefgh",
        "arg": -1,
    },
    {
        "name": "truncatechars/int-coerces",
        "filter": "truncatechars",
        "value": 5,
        "arg": 3,
    },
    {
        "name": "truncatechars/safe-input-stays-safe",
        "filter": "truncatechars",
        "value": mark_safe("abcdefgh"),
        "arg": 5,
    },
    {
        # The `words=true` half of the triple, and the row that separates this
        # filter's arguments from `truncatechars`'s: the truncation text is
        # `" …"` with a leading space, where `chars` uses a bare `"…"`.
        "name": "truncatewords/ascii",
        "filter": "truncatewords",
        "value": "one two three",
        "arg": 2,
    },
    {
        "name": "truncatewords/zero-words-is-empty",
        "filter": "truncatewords",
        "value": "one two three",
        "arg": 0,
    },
    {
        "name": "truncatewords/non-integer-argument-is-silent",
        "filter": "truncatewords",
        "value": "one two three",
        "arg": "x",
    },
    {
        "name": "truncatewords/safe-input-stays-safe",
        "filter": "truncatewords",
        "value": mark_safe("one two three"),
        "arg": 2,
    },
    # --- the html pair: the same host call with `html: true` -----------------
    {
        # The `html=true` half of the triple. Measured by the §11 mutation of
        # `truncatechars_html`'s `html: true` to `false`: **all three** rows
        # below move, because truncating the raw string and truncating its text
        # while re-closing tags differ on every one of these inputs. Not so for
        # `truncatewords_html`, where only one of the two moves — which is why
        # each count was measured rather than assumed from this one.
        "name": "truncatechars_html/balanced-tag-is-closed",
        "filter": "truncatechars_html",
        "value": "<b>abcdef</b>",
        "arg": 5,
    },
    {
        # An unbalanced tag: `HTMLParser` synthesises the closing `</b>`.
        "name": "truncatechars_html/unbalanced-tag-is-synthesised",
        "filter": "truncatechars_html",
        "value": "<b>abcdef",
        "arg": 4,
    },
    {
        # A `<` in the *text* where no tag is open: not a tag, so not closed.
        # A body that counted `<` would answer `'a…'` or worse.
        "name": "truncatechars_html/lone-angle-bracket-is-text",
        "filter": "truncatechars_html",
        "value": "a<b>",
        "arg": 3,
    },
    {
        # **This row does not observe the `html` flag, and saying so is the
        # point.** Measured by the §11 mutation of `truncatewords_html`'s
        # `html: true` to `false`: this row stayed **green**, because with the
        # flag off the first two words of `"<b>one two</b> three"` are
        # `"<b>one"` and `"two</b>"` and their join is the same string the html
        # path produces. It is kept for the `words=true` half of the triple —
        # it fails if `words` is wrong — but the flag's observer is the row
        # below, and only that one.
        "name": "truncatewords_html/balanced-tag-is-closed",
        "filter": "truncatewords_html",
        "value": "<b>one two</b> three",
        "arg": 2,
    },
    {
        # The `words=true, html=true` triple, and **the flag's only observer**:
        # the unbalanced tag is where the html path's synthesised `</p>` shows.
        # Measured by the same mutation — flipping the flag moves this row and
        # no other. It is also the row the whole argument-blindness argument
        # rests on: flipping it leaves every one of the six Rust unit tests
        # green, because `TestFilterHost` records the method name and not its
        # arguments.
        "name": "truncatewords_html/unbalanced-tag-is-synthesised",
        "filter": "truncatewords_html",
        "value": "<p>one two",
        "arg": 1,
    },
    # --- wordwrap (`defaultfilters.py:410-414`): `textwrap` with
    #     `break_long_words=False`
    {
        # The `wrap` width argument, which no unit test can see. A body passing
        # a different width answers differently on every row below.
        "name": "wordwrap/wraps-at-the-given-width",
        "filter": "wordwrap",
        "value": "a b c d",
        "arg": 3,
    },
    {
        # `break_long_words=False`: a word longer than the width is *not* split,
        # so a 7-character word at width 3 comes back whole. A body that used
        # Rust's `textwrap`-equivalent default breaks it into three lines.
        "name": "wordwrap/long-word-is-not-broken",
        "filter": "wordwrap",
        "value": "abcdefg",
        "arg": 3,
    },
    {
        # `textwrap` collapses the run of spaces (and `replace_whitespace`
        # normalises them), so the doubled space does not survive as padding.
        "name": "wordwrap/collapses-whitespace-runs",
        "filter": "wordwrap",
        "value": "a  b c",
        "arg": 3,
    },
    {
        "name": "wordwrap/safe-input-stays-safe",
        "filter": "wordwrap",
        "value": mark_safe("a b c d"),
        "arg": 3,
    },
    # --- first (`defaultfilters.py:602-608`, `value[0]`, catching **only**
    #     IndexError)
    #
    # `first` and `last` have the same body shape and **opposite** `is_safe`
    # (`first` is `False`, `last` is `True`), so the pair is what catches a loop
    # that read the flag off the result rather than the filter. The two raising
    # rows each filter has — an `int`'s `TypeError`, a `dict`'s `KeyError` — are
    # body tests: a corpus row has no exception path to compare.
    {
        "name": "first/list",
        "filter": "first",
        "value": ["a", "b"],
    },
    {
        # The `except IndexError` arm: an empty sequence is `""`, not an error.
        "name": "first/empty-list-is-the-empty-string",
        "filter": "first",
        "value": [],
    },
    {
        "name": "first/string",
        "filter": "first",
        "value": "ab",
    },
    {
        "name": "first/empty-string-is-the-empty-string",
        "filter": "first",
        "value": "",
    },
    {
        # A first item that is **`None`** is returned as `None`, not `""` — so a
        # body that answered `""` for anything falsy passes every row above and
        # fails this one. Measured: `(NoneType, None)`.
        "name": "first/none-item-is-not-the-empty-string",
        "filter": "first",
        "value": [None, 1],
    },
    {
        # `is_safe=False`: the same `mark_safe`d operand the `last` row below
        # keeps safe. One of the two rows is what catches the flag being read off
        # the result.
        "name": "first/safe-input-is-not-safe",
        "filter": "first",
        "value": mark_safe("ab"),
    },
    # --- last (`defaultfilters.py:624-630`, `value[-1]`, is_safe=True)
    {
        "name": "last/list",
        "filter": "last",
        "value": ["a", "b"],
    },
    {
        # The index is `-1` where `first`'s is `0`; this row and `last/list`
        # together pin it, and a body that copied `first`'s index fails.
        "name": "last/empty-list-is-the-empty-string",
        "filter": "last",
        "value": [],
    },
    {
        "name": "last/safe-input-stays-safe",
        "filter": "last",
        "value": mark_safe("ab"),
    },
    # --- length (`defaultfilters.py:633-639`, `len(value)` on
    #     `(ValueError, TypeError)` -> 0)
    {
        "name": "length/list",
        "filter": "length",
        "value": [1, 2, 3],
    },
    {
        "name": "length/string",
        "filter": "length",
        "value": "abc",
    },
    {
        "name": "length/dict-counts-keys",
        "filter": "length",
        "value": {"a": 1, "b": 2},
    },
    {
        "name": "length/int-is-zero",
        "filter": "length",
        "value": 5,
    },
    {
        # `None` is the `TypeError` arm, `""` is a genuine `0`: two different
        # nothings, and a body that answered `0` for anything falsy would pass
        # the `None` row and this one alike while getting the reason wrong.
        "name": "length/empty-string-is-zero",
        "filter": "length",
        "value": "",
    },
    {
        # A generator has no `len` at all — `TypeError`, so `0`. This is the row
        # that tells "asked the host for a length" from "iterated and counted",
        # because the second answers 2. One-shot: built fresh per side.
        "name": "length/generator-has-no-len",
        "filter": "length",
        "value_factory": lambda: (x for x in [1, 2]),
    },
    # --- slice (`defaultfilters.py:651-666`, Rust parses the bits, the host
    #     subscripts)
    #
    # The **type** half of §D5 is the point of the first row: `"abcdef"|slice:":3"`
    # is the *string* `"abc"`, where an index route (get + Rust slicing) yields a
    # list. The two render alike and differ in everything downstream.
    {
        "name": "slice/stop-is-a-string-not-a-list",
        "filter": "slice",
        "value": "abcdef",
        "arg": ":3",
    },
    {
        # **This row is what pins that a lone component is the *stop*.** `"1"`
        # splits to `["1"]` and reaches `slice(1)` = `slice(None, 1, None)`, so
        # the answer is `"a"` — a body that read a single bit as `start` answers
        # `"bcdef"`.
        "name": "slice/single-component-is-the-stop",
        "filter": "slice",
        "value": "abcdef",
        "arg": "1",
    },
    {
        "name": "slice/two-indices",
        "filter": "slice",
        "value": "abcdef",
        "arg": "2:5",
    },
    {
        "name": "slice/negative-index",
        "filter": "slice",
        "value": "abcdef",
        "arg": "-3:",
    },
    {
        "name": "slice/step",
        "filter": "slice",
        "value": "abcdef",
        "arg": "::2",
    },
    {
        "name": "slice/negative-step",
        "filter": "slice",
        "value": "abcdef",
        "arg": "::-1",
    },
    {
        # A missing *middle* component is the only shape where a `None` bit is
        # neither leading nor trailing.
        "name": "slice/missing-middle-component",
        "filter": "slice",
        "value": "abcdef",
        "arg": "1::2",
    },
    {
        "name": "slice/three-components",
        "filter": "slice",
        "value": "abcdef",
        "arg": "1:5:2",
    },
    {
        "name": "slice/out-of-range-is-empty",
        "filter": "slice",
        "value": "abc",
        "arg": "5:9",
    },
    {
        # Every bit `None`, one step before the subscript.
        "name": "slice/empty-arg-is-every-bit-none",
        "filter": "slice",
        "value": "abcdef",
        "arg": "",
    },
    {
        # **Two catch arms, and the plan ran them together.** Four *numeric*
        # components reach `slice(*bits)` and raise `TypeError` there; a
        # non-numeric one raises `ValueError` at `int(x)` and never gets that
        # far. Both return `value`. A body that caught only one of the two passes
        # one row and fails the other.
        "name": "slice/four-components-is-a-caught-typeerror",
        "filter": "slice",
        "value": "abcdef",
        "arg": "1:2:3:4",
    },
    {
        "name": "slice/non-numeric-component-is-a-caught-valueerror",
        "filter": "slice",
        "value": "abcdef",
        "arg": "x:3",
    },
    {
        # The subscript itself refuses: `TypeError` again, and `value` comes
        # back. This is the *third* catch site, in the same `except`.
        "name": "slice/non-subscriptable-value-is-returned",
        "filter": "slice",
        "value": 5,
        "arg": ":3",
    },
    # --- join (`defaultfilters.py:611-621`, needs_autoescape, is_safe=True)
    #
    # The pair the per-row `context` key exists for. Both are `safe=True` and
    # differ only in the text, so nothing but the key can tell them apart.
    {
        "name": "join/autoescape-true-escapes-items",
        "filter": "join",
        "value": ["a", "<b>"],
        "arg": ", ",
    },
    {
        # **Pinned**, because this row is the group's `autoescape=False`
        # detector and parity alone cannot fail on it: drop the key and both
        # engines escape, so a body that never read `autoescape` stays green.
        # The literal is what reddens. See the module doc on `expected`.
        "name": "join/autoescape-false-does-not-escape",
        "filter": "join",
        "value": ["a", "<b>"],
        "arg": ", ",
        "context": {"autoescape": False},
        "expected": "a, <b>",
    },
    {
        "name": "join/empty-list-is-the-empty-string",
        "filter": "join",
        "value": [],
        "arg": ", ",
    },
    {
        # **The group's trap**: on `TypeError` the filter returns **`value`, the
        # unjoined operand**, not `""`. A body that returned `""` here passes
        # every row above.
        "name": "join/typeerror-returns-the-value",
        "filter": "join",
        "value": 5,
        "arg": ", ",
    },
    {
        # Carries an `autoescape` key and does **not** depend on it: `join(5,
        # ", ")` raises `TypeError` and returns the value in both branches, so
        # this answers the int `5` either way (measured). It is here as the
        # failing branch's non-escaping half and is deliberately **not** pinned
        # — an `expected` here would assert something the comparison already
        # sees. See the module doc on `expected`.
        "name": "join/typeerror-returns-the-value-not-escaping",
        "filter": "join",
        "value": 5,
        "arg": ", ",
        "context": {"autoescape": False},
    },
    {
        # A non-string separator splits the two branches: escaping wraps it in
        # `conditional_escape`, which is what gives it a `.join` at all. Without
        # escaping it is a bare `5.join`, an `AttributeError` the body does *not*
        # catch (it catches `TypeError`), so that half is a body test.
        "name": "join/int-separator-escaping-makes-it-a-string",
        "filter": "join",
        "value": "abc",
        "arg": 5,
    },
    {
        # A `SafeString` item is not double-escaped even with autoescape on.
        "name": "join/safe-items",
        "filter": "join",
        "value": [mark_safe("<b>"), "x"],
        "arg": ", ",
    },
    {
        "name": "join/safe-separator",
        "filter": "join",
        "value": ["a", "b"],
        "arg": mark_safe("<i>"),
    },
    {
        # A generator as the whole value, one-shot and so built per side.
        "name": "join/generator-value",
        "filter": "join",
        "value_factory": lambda: (x for x in ["a", "b"]),
        "arg": ", ",
    },
    # --- random (`defaultfilters.py:642-648`, `random.choice` on IndexError -> "")
    #
    # **Membership, not equality** (Review Focus 4): the answer is chosen at
    # random, so an equality row would be green roughly one time in three. The
    # row says what it asserts in its name, and the harness checks both sides
    # against the *population* rather than against each other.
    {
        "name": "random/result-is-a-member-of-the-population",
        "filter": "random",
        "value": ["a", "b", "c"],
        "membership": True,
    },
    {
        "name": "random/empty-list-is-the-empty-string",
        "filter": "random",
        "value": [],
    },
    {
        # The `is_safe=True` half of `random`, and it is a membership row for the
        # same reason the one above is: the answer is still chosen at random, so
        # only the *safety* of it can be compared exactly.
        "name": "random/safe-member-stays-safe",
        "filter": "random",
        "value": mark_safe("abc"),
        "membership": True,
    },
    # --- dictsort / dictsortreversed
    #     (`defaultfilters.py:578-587`/`defaultfilters.py:590-599`)
    #
    # One host call each, with `(AttributeError, TypeError)` -> `""`. The
    # argument's *shape* decides the resolver, and this is the pair that shows it:
    # a bare int reaches `float(1)` and becomes `itemgetter(1)` — a list index —
    # where a quoted digit is a string key. Both are `float()`-convertible, so
    # nothing but the token shape separates them.
    {
        "name": "dictsort/by-key",
        "filter": "dictsort",
        "value": [{"n": 2}, {"n": 1}],
        "arg": "n",
    },
    {
        "name": "dictsort/bare-int-arg-is-a-list-index",
        "filter": "dictsort",
        "value": [[9, "b"], [8, "a"]],
        "arg": 1,
    },
    {
        "name": "dictsort/quoted-digit-arg-is-a-string-key",
        "filter": "dictsort",
        "value": [{"1": "b"}, {"1": "a"}],
        "arg": "1",
    },
    {
        "name": "dictsort/dotted-path-into-a-dict",
        "filter": "dictsort",
        "value": [{"a": {"b": 2}}, {"a": {"b": 1}}],
        "arg": "a.b",
    },
    {
        # The `getattr` fallback, reachable only from an object whose subscript
        # *raises* — a dict and a list both answer the first branch.
        "name": "dictsort/getattr-when-subscript-raises",
        "filter": "dictsort",
        "value": [DOT_ATTR(2), DOT_ATTR(1)],
        "arg": "x",
    },
    {
        # The refusal is on **any** underscore-prefixed bit, not only a leading
        # one: the test is `"._" in arg or arg[0] == "_"`, so `"a._b"` is refused
        # exactly as `"_a"` is. Two rows, because a body that tested only `arg[0]`
        # passes the first and fails the second.
        "name": "dictsort/leading-underscore-arg-is-refused",
        "filter": "dictsort",
        "value": [{"_a": 1}],
        "arg": "_a",
    },
    {
        "name": "dictsort/internal-underscore-bit-is-refused",
        "filter": "dictsort",
        "value": [{"a": {"_b": 1}}],
        "arg": "a._b",
    },
    {
        # Three ways the sort refuses, all becoming `""` — the outer `try`'s
        # not-iterable arm, and two from the resolver.
        "name": "dictsort/non-iterable-is-the-empty-string",
        "filter": "dictsort",
        "value": 5,
        "arg": "a",
    },
    {
        "name": "dictsort/missing-key-is-the-empty-string",
        "filter": "dictsort",
        "value": [{"a": 1}, {"b": 2}],
        "arg": "a",
    },
    {
        "name": "dictsortreversed/by-key",
        "filter": "dictsortreversed",
        "value": [{"n": 1}, {"n": 2}],
        "arg": "n",
    },
    {
        "name": "dictsortreversed/getattr-when-subscript-raises",
        "filter": "dictsortreversed",
        "value": [DOT_ATTR(1), DOT_ATTR(2)],
        "arg": "x",
    },
    {
        "name": "dictsortreversed/leading-underscore-arg-is-refused",
        "filter": "dictsortreversed",
        "value": [{"_a": 1}],
        "arg": "_a",
    },
    # --- unordered_list (`defaultfilters.py:669-737`, needs_autoescape,
    #     recursive, tabs)
    {
        "name": "unordered_list/single",
        "filter": "unordered_list",
        "value": ["a"],
    },
    {
        "name": "unordered_list/empty-is-the-empty-string",
        "filter": "unordered_list",
        "value": [],
    },
    {
        # **Four levels**: a one-level case passes with the recursion missing
        # entirely, and the tab depth is what shows it.
        "name": "unordered_list/nested-four-levels",
        "filter": "unordered_list",
        "value": ["a", ["b", ["c", ["d"]]]],
    },
    {
        # The nesting test is `isinstance(next_item, (list, tuple,
        # GeneratorType))` **and then** `iter()`, and both halves are
        # load-bearing. A tuple is in the triple and nests; a dict iterates but
        # is not in the triple, so it does not; `None` is neither, so it does not
        # either and becomes a **second item** instead. Three rows, one per
        # decision, and the `None` row is the one that separates "not nested" from
        # "dropped".
        "name": "unordered_list/tuple-nests",
        "filter": "unordered_list",
        "value": ["a", ("b", "c")],
    },
    {
        "name": "unordered_list/dict-does-not-nest",
        "filter": "unordered_list",
        "value": ["a", {"b": 1}],
    },
    {
        "name": "unordered_list/none-child-does-not-nest",
        "filter": "unordered_list",
        "value": ["a", None],
    },
    {
        "name": "unordered_list/empty-list-child-does-not-nest",
        "filter": "unordered_list",
        "value": ["a", []],
    },
    {
        # A generator as the **whole value** pins that the host's view of it is
        # `list(v)`; a generator as an *element* takes the third arm of the
        # isinstance test. Both are one-shot, so both are built per side.
        "name": "unordered_list/generator-value",
        "filter": "unordered_list",
        "value_factory": lambda: (x for x in ["a", ["b", "c"]]),
    },
    {
        "name": "unordered_list/nested-generator-nests",
        "filter": "unordered_list",
        "value_factory": lambda: ["a", (x for x in ["b", "c"])],
    },
    {
        # **The row that forced `NestedSequence.truthy`.** `if children:` tests
        # the *object*, and a generator is truthy even yielding nothing, so an
        # empty nested generator gets an empty `<ul>` where an empty nested list
        # gets none — the same emptiness, two answers. Measured Django:
        # `"\t<li>a\n\t<ul>\n\n\t</ul>\n\t</li>"`.
        "name": "unordered_list/empty-nested-generator-still-opens-a-ul",
        "filter": "unordered_list",
        "value_factory": lambda: ["a", (x for x in [])],
    },
    {
        # `__str__` is not `__repr__`: the escaper is `"%s" % item`, so a body
        # that escaped the `repr` answers `REPR-SENTINEL` here.
        "name": "unordered_list/escapes-str-not-repr",
        "filter": "unordered_list",
        "value": [STR_NOT_REPR()],
    },
    {
        "name": "unordered_list/int-item-is-stringified",
        "filter": "unordered_list",
        "value": [1, 2],
    },
    {
        # The group's second `context` row, and the only one that shows the
        # escaper being *chosen* rather than applied: nested, so the recursion
        # must carry the choice down rather than re-deciding per level.
        #
        # **Pinned**, and for the same reason as the join row above: with the
        # key ignored every level escapes on both sides, so this row is a
        # detector only if the literal is asserted. See the module doc.
        "name": "unordered_list/autoescape-false-nested",
        "filter": "unordered_list",
        "value": ["<a>", ["<b>"]],
        "context": {"autoescape": False},
        "expected": "\t<li><a>\n\t<ul>\n\t\t<li><b></li>\n\t</ul>\n\t</li>",
    },
    {
        "name": "unordered_list/autoescape-true-nested",
        "filter": "unordered_list",
        "value": ["<a>", ["<b>"]],
    },
    # --- Task 7, wave 1: add, divisibleby, get_digit, stringformat ---------
    #
    # `add`'s three arms, and the safety asymmetry that is the reason two of
    # these rows exist. `is_safe=False` means the loop never marks the answer,
    # but the *value* can still carry a mark: `SafeString.__add__` returns a
    # `SafeString`, while `str.__add__` on a plain left operand returns a plain
    # `str` even when the argument is a `SafeString`. The pair below is the two
    # directions of that, and a body that read the flag alone would get both
    # wrong the same way.
    {
        "name": "add/ints",
        "filter": "add",
        "value": 1,
        "arg": 2,
    },
    {
        "name": "add/numeric-strings",
        "filter": "add",
        "value": "1",
        "arg": "2",
    },
    {
        # `int(1.5) + int(1)` is `2`, not `2.5`: the first arm truncates.
        "name": "add/floats-truncate",
        "filter": "add",
        "value": 1.5,
        "arg": 1,
    },
    {
        "name": "add/bool-is-an-int",
        "filter": "add",
        "value": True,
        "arg": 1,
    },
    {
        # An `IntValue::Big` on both sides — canonical decimal text, so a body
        # that ran `i64` arithmetic would overflow here.
        "name": "add/big-ints",
        "filter": "add",
        "value": 2**70,
        "arg": 2,
    },
    {
        # Arm 2, the one no `Value` variant can express in Rust: `str.__add__`.
        "name": "add/strings-concatenate",
        "filter": "add",
        "value": "a",
        "arg": "b",
    },
    {
        # Arm 3's bare `except Exception` — reached because `int("2.5")` is a
        # `ValueError` and `1 + "2.5"` is a `TypeError`.
        "name": "add/unparseable-string-is-empty",
        "filter": "add",
        "value": 1,
        "arg": "2.5",
    },
    {
        "name": "add/str-plus-int-is-empty",
        "filter": "add",
        "value": "a",
        "arg": 1,
    },
    {
        "name": "add/list-plus-int-is-empty",
        "filter": "add",
        "value": [1],
        "arg": 2,
    },
    {
        "name": "add/none-is-empty",
        "filter": "add",
        "value": None,
        "arg": 1,
    },
    {
        # `mark_safe("a") + "b"` is a `SafeString`, so the answer is marked
        # although `is_safe=False`. The **mark is the value's**, not the flag's.
        "name": "add/safe-left-operand-keeps-the-mark",
        "filter": "add",
        "value": mark_safe("a"),
        "arg": "b",
    },
    {
        # The mirror: a plain `str` on the left wins, so `str.__add__` returns a
        # plain `str` and the argument's mark is dropped.
        "name": "add/plain-left-operand-drops-the-marks",
        "filter": "add",
        "value": "a",
        "arg": mark_safe("b"),
    },

    # `divisibleby` has **no `try`**, so its failures escape and cannot be rows
    # above; what is left to pin is that every surviving arm answers a Python
    # `bool`. Measured rows only.
    {
        "name": "divisibleby/divisible",
        "filter": "divisibleby",
        "value": 4,
        "arg": 2,
    },
    {
        "name": "divisibleby/not-divisible",
        "filter": "divisibleby",
        "value": 5,
        "arg": 2,
    },
    {
        "name": "divisibleby/zero-is-divisible",
        "filter": "divisibleby",
        "value": 0,
        "arg": 2,
    },
    {
        # `int(4.9)` is `4`, so this is the truncating conversion, not a modulo.
        "name": "divisibleby/float-truncates",
        "filter": "divisibleby",
        "value": 4.9,
        "arg": 2,
    },
    {
        "name": "divisibleby/numeric-string",
        "filter": "divisibleby",
        "value": "4",
        "arg": 2,
    },
    {
        "name": "divisibleby/big-int",
        "filter": "divisibleby",
        "value": 2**70,
        "arg": 2,
    },
    {
        "name": "divisibleby/negative",
        "filter": "divisibleby",
        "value": -4,
        "arg": 2,
    },

    # `get_digit`'s two early returns and its `IndexError` arm. The `mark_safe`
    # rows are the ones that show the early return hands back *the input as it
    # was*, marking included.
    {
        "name": "get_digit/ones",
        "filter": "get_digit",
        "value": 123,
        "arg": 1,
    },
    {
        "name": "get_digit/hundreds",
        "filter": "get_digit",
        "value": 123,
        "arg": 3,
    },
    {
        # Past the end: `str(123)[-4]` is an `IndexError`, answered `0`.
        "name": "get_digit/past-the-end-is-zero",
        "filter": "get_digit",
        "value": 123,
        "arg": 4,
    },
    {
        # `arg < 1` returns the value unchanged — an `int` 123, not a string.
        "name": "get_digit/zero-arg-returns-the-value",
        "filter": "get_digit",
        "value": 123,
        "arg": 0,
    },
    {
        "name": "get_digit/negative-arg-returns-the-value",
        "filter": "get_digit",
        "value": 123,
        "arg": -1,
    },
    {
        # The sign is a character in `str(-123)`, so digit 1 is `3` and digit 3
        # is `1`; the `-` is only reachable at `arg == 4`.
        "name": "get_digit/negative-value-indexes-digits",
        "filter": "get_digit",
        "value": -123,
        "arg": 1,
    },
    {
        # `int("abc")` is the caught `ValueError`, so the value comes back as it
        # was — a `SafeString` here, which is the marking half of the early
        # return.
        "name": "get_digit/non-numeric-value-returns-it-marked",
        "filter": "get_digit",
        "value": mark_safe("abc"),
        "arg": 1,
    },
    {
        "name": "get_digit/string-digits",
        "filter": "get_digit",
        "value": "0123",
        "arg": 1,
    },
    {
        "name": "get_digit/float-arg-truncates",
        "filter": "get_digit",
        "value": 123,
        "arg": 1.9,
    },
    {
        "name": "get_digit/bool-value",
        "filter": "get_digit",
        "value": True,
        "arg": 1,
    },
    {
        # A `Big` value: `str(10**30)` has 31 digits, so digit 1 is `0` and the
        # conversion must not go through `i64`.
        "name": "get_digit/big-value",
        "filter": "get_digit",
        "value": 10**30,
        "arg": 1,
    },
    {
        "name": "get_digit/big-arg",
        "filter": "get_digit",
        "value": 123,
        "arg": 10**30,
    },

    # `stringformat` is `is_safe=True`, so the loop marks the answer when the
    # **pre-filter** value is `SafeData`. The tuple rows are the ones `is_tuple`
    # exists for, and they are expressible here as a real Python tuple value.
    {
        "name": "stringformat/str",
        "filter": "stringformat",
        "value": "AbC",
        "arg": "s",
    },
    {
        "name": "stringformat/width",
        "filter": "stringformat",
        "value": "AbC",
        "arg": "10s",
    },
    {
        "name": "stringformat/int-decimal",
        "filter": "stringformat",
        "value": 42,
        "arg": "d",
    },
    {
        "name": "stringformat/float-two-places",
        "filter": "stringformat",
        "value": 1.23456,
        "arg": ".2f",
    },
    {
        "name": "stringformat/hex",
        "filter": "stringformat",
        "value": 42,
        "arg": "x",
    },
    {
        "name": "stringformat/char",
        "filter": "stringformat",
        "value": 65,
        "arg": "c",
    },
    {
        "name": "stringformat/octal",
        "filter": "stringformat",
        "value": 42,
        "arg": "o",
    },
    {
        # `None` is not special to `%s`: it is `str(None)`.
        "name": "stringformat/none-is-none",
        "filter": "stringformat",
        "value": None,
        "arg": "s",
    },
    {
        # A list is **not** unpacked — CPython unpacks a tuple and treats every
        # other sequence as a single operand.
        "name": "stringformat/list-is-one-operand",
        "filter": "stringformat",
        "value": [1, None],
        "arg": "s",
    },
    {
        # The `isinstance(value, tuple)` branch: stringified first, so `%s`
        # receives `"(1, 2)"` where the bare operator would raise `TypeError:
        # not all arguments converted during string formatting`.
        "name": "stringformat/tuple-is-stringified",
        "filter": "stringformat",
        "value": (1, 2),
        "arg": "s",
    },
    {
        "name": "stringformat/tuple-with-width",
        "filter": "stringformat",
        "value": (1, 2),
        "arg": "10s",
    },
    {
        # The one-element tuple keeps its trailing comma in `repr`.
        "name": "stringformat/one-element-tuple",
        "filter": "stringformat",
        "value": (1,),
        "arg": "s",
    },
    {
        "name": "stringformat/empty-tuple",
        "filter": "stringformat",
        "value": (),
        "arg": "s",
    },
    {
        # The refusal arm: `%d` of a `str` is a `TypeError`, caught, answered `""`.
        "name": "stringformat/bad-spec-is-empty",
        "filter": "stringformat",
        "value": "abc",
        "arg": "d",
    },
    {
        # `SafeString` in, `SafeString` out: the loop marks because the
        # pre-filter value is `SafeData`, not because the body did.
        "name": "stringformat/safe-value-marks-the-result",
        "filter": "stringformat",
        "value": mark_safe("AbC"),
        "arg": "s",
    },
    {
        # The mirror of the row above, and the sharpest one: `mark_safe(42)` is
        # the `SafeString` `"42"`, whose `%d` fails — and the resulting empty
        # string is **marked**, because the loop tests the pre-filter value. With
        # a plain `42` the same failure is an unmarked `""`.
        "name": "stringformat/safe-value-marks-even-the-failure",
        "filter": "stringformat",
        "value": mark_safe(42),
        "arg": "d",
    },
    {
        "name": "stringformat/plain-value-is-not-marked",
        "filter": "stringformat",
        "value": "AbC",
        "arg": "s",
    },
    # --- Task 7, wave 2: pluralize, yesno, default, default_if_none --------
    #
    # `default` and `default_if_none` exist as a pair *because* they differ, and
    # that difference is the pair's whole content: one asks truthiness, the
    # other asks `is None`. Four inputs separate them — `0`, `""`, `[]` and
    # `False` — and each appears below on both filters. A body that implemented
    # either one as the other passes one column alone, so the columns are only
    # meaningful together.
    #
    # **Both return the argument *object*, so their safety is the operand's and
    # not the filter's.** Both are `is_safe=False`, so the loop never marks the
    # answer; what carries a mark is whatever the body handed back. A quoted
    # constant argument is `mark_safe`d by `resolve` (`base.py:788-792`), which
    # is the whole of why `default(0, "x")` is a `SafeString` while
    # `default_if_none(0, "x")` is the `int` 0 — same argument text, different
    # row, and `assert_same`'s safety half is the only thing that sees it. A
    # body that built a fresh value instead of returning the operand loses the
    # flag on one and gains it on the other, in the same direction both times.
    {
        "name": "default/zero-takes-the-argument",
        "filter": "default",
        "value": 0,
        "arg": "x",
    },
    {
        "name": "default/empty-string-takes-the-argument",
        "filter": "default",
        "value": "",
        "arg": "x",
    },
    {
        "name": "default/empty-list-takes-the-argument",
        "filter": "default",
        "value": [],
        "arg": "x",
    },
    {
        "name": "default/empty-dict-takes-the-argument",
        "filter": "default",
        "value": {},
        "arg": "x",
    },
    {
        "name": "default/none-takes-the-argument",
        "filter": "default",
        "value": None,
        "arg": "x",
    },
    {
        "name": "default/false-takes-the-argument",
        "filter": "default",
        "value": False,
        "arg": "x",
    },
    {
        "name": "default/truthy-int-passes-through",
        "filter": "default",
        "value": 1,
        "arg": "x",
    },
    {
        "name": "default/truthy-string-passes-through",
        "filter": "default",
        "value": "abc",
        "arg": "x",
    },
    {
        "name": "default/truthy-list-passes-through",
        "filter": "default",
        "value": [1],
        "arg": "x",
    },
    {
        # The argument is *not* returned, so its mark cannot be what the row
        # passes on: a body that marked its result unconditionally fails here
        # while passing every `takes-the-argument` row above.
        "name": "default/unused-argument-is-not-returned",
        "filter": "default",
        "value": "z",
        "arg": "y",
    },
    {
        # A bare token, so an unmarked `int` — the contrast to the `SafeString`
        # rows above. Nothing in the value decides this; the argument's *shape*
        # does.
        "name": "default/numeric-argument-is-unmarked",
        "filter": "default",
        "value": 0,
        "arg": 5,
    },
    {
        # `"arg": None` is the explicit `None` token, not an absent argument, so
        # this is `0 or None` — and the answer is `None`, which is falsy and
        # still returned. A body guarding the argument against `None` answers
        # `0` here.
        "name": "default/none-argument-is-an-argument",
        "filter": "default",
        "value": 0,
        "arg": None,
    },
    {
        "name": "default_if_none/none-takes-the-argument",
        "filter": "default_if_none",
        "value": None,
        "arg": "x",
    },
    {
        # The four rows that separate this filter from `default`, and they are
        # the reason both exist.
        "name": "default_if_none/zero-passes-through",
        "filter": "default_if_none",
        "value": 0,
        "arg": "x",
    },
    {
        "name": "default_if_none/empty-string-passes-through",
        "filter": "default_if_none",
        "value": "",
        "arg": "x",
    },
    {
        "name": "default_if_none/empty-list-passes-through",
        "filter": "default_if_none",
        "value": [],
        "arg": "x",
    },
    {
        "name": "default_if_none/false-passes-through",
        "filter": "default_if_none",
        "value": False,
        "arg": "x",
    },
    {
        "name": "default_if_none/truthy-passes-through",
        "filter": "default_if_none",
        "value": 1,
        "arg": "x",
    },
    {
        "name": "default_if_none/numeric-argument-is-unmarked",
        "filter": "default_if_none",
        "value": None,
        "arg": 5,
    },
    {
        "name": "default_if_none/none-argument-is-an-argument",
        "filter": "default_if_none",
        "value": None,
        "arg": None,
    },
    # `yesno`'s default argument is `gettext("yes,no,maybe")` — a **translation
    # lookup**, not a literal — so its three rows state their i18n state by
    # running under the same unconfigured-i18n settings the rest of the corpus
    # uses, where `gettext` is the identity and the suffix is the English
    # string. That is a real host call on both sides rather than a constant.
    {
        "name": "yesno/default-argument-true",
        "filter": "yesno",
        "value": True,
    },
    {
        "name": "yesno/default-argument-false",
        "filter": "yesno",
        "value": False,
    },
    {
        "name": "yesno/default-argument-none-is-maybe",
        "filter": "yesno",
        "value": None,
    },
    {
        "name": "yesno/two-bits-true",
        "filter": "yesno",
        "value": True,
        "arg": "y,n",
    },
    {
        "name": "yesno/two-bits-false",
        "filter": "yesno",
        "value": False,
        "arg": "y,n",
    },
    {
        # Two bits leave `maybe` unset, and the source's fallback makes it the
        # *no* string — so `None` and `False` answer alike here and the
        # three-bit row below is the only one that separates them.
        "name": "yesno/two-bits-none-is-no",
        "filter": "yesno",
        "value": None,
        "arg": "y,n",
    },
    {
        "name": "yesno/three-bits-true",
        "filter": "yesno",
        "value": True,
        "arg": "y,n,m",
    },
    {
        "name": "yesno/three-bits-false",
        "filter": "yesno",
        "value": False,
        "arg": "y,n,m",
    },
    {
        "name": "yesno/three-bits-none-is-maybe",
        "filter": "yesno",
        "value": None,
        "arg": "y,n,m",
    },
    {
        "name": "yesno/truthy-string-is-yes",
        "filter": "yesno",
        "value": "x",
        "arg": "y,n",
    },
    {
        # `None` is `maybe` and `0` is *no* — the pair that pins `is None`
        # against truthiness in the branch, which is the same distinction
        # `default_if_none` makes.
        "name": "yesno/zero-is-no",
        "filter": "yesno",
        "value": 0,
        "arg": "y,n",
    },
    {
        "name": "yesno/empty-list-is-no",
        "filter": "yesno",
        "value": [],
        "arg": "y,n",
    },
    {
        # Fewer than two bits returns the **value**, not `""` — measured
        # `yesno(None, "y") -> None`. A body that answered with the argument's
        # first bit, or with an empty string, fails here; the row's value is
        # chosen to be neither.
        "name": "yesno/short-argument-returns-the-value",
        "filter": "yesno",
        "value": "v",
        "arg": "y",
    },
    {
        "name": "yesno/short-argument-returns-none",
        "filter": "yesno",
        "value": None,
        "arg": "y",
    },
    {
        # The empty argument is one bit, not zero, so it takes the same arm as
        # the pair above: `"".split(",")` is `[""]`.
        "name": "yesno/empty-argument-is-one-bit",
        "filter": "yesno",
        "value": "v",
        "arg": "",
    },
    {
        # Four bits fail the three-way unpack, and the source's `except
        # ValueError` falls back to `bits[0], bits[1], bits[1]` — so the third,
        # fourth and any further bits are all discarded and `maybe` becomes the
        # *no* string. This is the arm a body written as an `if len == 3` would
        # get wrong.
        "name": "yesno/four-bits-uses-the-first-two",
        "filter": "yesno",
        "value": True,
        "arg": "a,b,c,d",
    },
    {
        "name": "yesno/four-bits-none-uses-the-second",
        "filter": "yesno",
        "value": None,
        "arg": "a,b,c,d",
    },
    {
        # `is_safe=False`, so the loop does not mark — and both returns here are
        # freshly built `str`s, so nothing can carry a mark in either direction.
        "name": "yesno/safe-value-is-not-marked",
        "filter": "yesno",
        "value": mark_safe("x"),
        "arg": "y,n",
    },
    {
        "name": "yesno/safe-value-with-the-default-argument",
        "filter": "yesno",
        "value": mark_safe("x"),
    },
    # `pluralize`'s argument handling is the seam's `comma_bits`, and the first
    # four rows below are why it is one operation rather than a `contains`, a
    # `concat` and a `split`: the argument's *type* decides which of the three
    # raises, and with which class. See `ERROR_CASES` for the five inputs that
    # make that visible.
    {
        # The argument defaults to `"s"`, `","` is prepended because it holds no
        # comma, and `",s".split(",")` is `["", "s"]` — so the *singular* suffix
        # is the empty string. `pluralize(1)` is `""`, not `"s"`. The plan's
        # draft of this filter said otherwise; this row is the correction.
        "name": "pluralize/one-is-singular",
        "filter": "pluralize",
        "value": 1,
    },
    {
        "name": "pluralize/two-is-plural",
        "filter": "pluralize",
        "value": 2,
    },
    {
        "name": "pluralize/zero-is-plural",
        "filter": "pluralize",
        "value": 0,
    },
    {
        "name": "pluralize/explicit-bits-singular",
        "filter": "pluralize",
        "value": 1,
        "arg": "s,p",
    },
    {
        "name": "pluralize/explicit-bits-plural",
        "filter": "pluralize",
        "value": 2,
        "arg": "s,p",
    },
    {
        "name": "pluralize/float-one-is-singular",
        "filter": "pluralize",
        "value": 1.0,
    },
    {
        "name": "pluralize/float-not-one-is-plural",
        "filter": "pluralize",
        "value": 1.5,
    },
    {
        # More than two bits returns `""` *before* the value is looked at, so
        # this row would pass even for a body that never called `to_float`.
        "name": "pluralize/three-bits-is-empty",
        "filter": "pluralize",
        "value": 1,
        "arg": "a,b,c",
    },
    {
        # **The row that separates the `ValueError` arm from the `TypeError`
        # one.** `float("abc")` raises `ValueError`, which the source lets fall
        # through to `return ""` *without asking `len`* — so a body that read a
        # non-numeric operand as "not a number, try `len`" answers `"s"` here
        # (`len("abc") == 3`). The plan's bullet for this filter stated the
        # wrong rule and this row is the measurement that refutes it; no list
        # row can, because a list reaches the `len` arm legitimately.
        "name": "pluralize/non-numeric-string-is-not-len-based",
        "filter": "pluralize",
        "value": "abc",
    },
    {
        # **The second route to the coincidence, and the measurement that
        # refutes what the `"1"` row used to claim.** An input is blind to the
        # `__float__` defect exactly when `len(v) == 1` and "the float arm
        # answers the singular suffix" agree — and the float arm answers
        # singular *two* ways: `float(v) == 1`, and `float(v)` raising
        # **`ValueError`**, because the source `pass`es that and falls out to
        # `return ""`, which is the singular suffix. Every one-character
        # non-numeric string coincides by the second route (`"a"`, `"!"`, `"."`),
        # answering `""` on both sides. Kept, like `"1"`, as the coinciding twin
        # of a failing row — of `"abc"` immediately above — because the pair is
        # what shows the arm is not on its own what discriminates: the length has
        # to differ from 1 as well. Like `"1"` it cannot separate the arms by
        # itself, so it is here as a statement of the rule and not as a trap.
        "name": "pluralize/one-char-non-numeric-string-is-one",
        "filter": "pluralize",
        "value": "a",
    },
    {
        "name": "pluralize/empty-string-is-not-len-based",
        "filter": "pluralize",
        "value": "",
    },
    {
        # A numeric *string* goes through `float`, so it is a number and not a
        # length — and `"1"` is the row where that is invisible: `float("1")` is
        # `1.0` and `len("1") == 1` select the same empty singular suffix, so a
        # body that took the `len` arm by mistake agrees here. It is kept even
        # though it cannot separate the arms on its own: it is the coinciding
        # twin of `"2"` below, and a set with `"2"` alone would not show that the
        # other single digit had been *agreeing*.
        #
        # **It is not the only one-character string that coincides**, which is
        # what this comment claimed before it was measured. The coincidence has
        # two routes and `"1"` is one of them; the other is a one-character
        # string that does not parse, and it has its own row on
        # `pluralize/one-char-non-numeric-string-is-one`.
        "name": "pluralize/numeric-string-one",
        "filter": "pluralize",
        "value": "1",
    },
    {
        # **The row that exposed a defect in the seam, and it is `"1"`'s
        # twin.** `"2"` is the first input where the two arms disagree
        # (`float("2") != 1` is plural; `len("2") == 1` is singular), and it
        # failed: the host's `to_float` was `PyFloat_AsDouble` — `__float__` —
        # which refuses a `str` with a `TypeError` where Python's `float()`
        # parses it, so every numeric string took the wrong arm. The fix is in
        # `template-py/src/filters.rs`'s `float_of`; this row is the only thing
        # that could have found it, because §10.4 gives an agent no way to run
        # the real host.
        "name": "pluralize/numeric-string-two",
        "filter": "pluralize",
        "value": "2",
    },
    {
        # The other direction of the same disagreement, and a *signed* string:
        # `float("+1")` is `1.0` (singular) while `len("+1") == 2` is plural, so
        # this is a numeric string that must answer the **singular** suffix —
        # where `"2"` must answer the plural one. Together they pin that the
        # answer comes from a parse and not from a character count, in both
        # directions; `"2"` alone would be satisfied by a body that treated any
        # one-character operand as a number.
        "name": "pluralize/signed-numeric-string-is-one",
        "filter": "pluralize",
        "value": "+1",
    },
    {
        # And the syntax a character count has no way to see at all: an exponent.
        # `float("1e0")` is `1.0`; `len("1e0")` is 3. This is the sharpest of the
        # string rows, because unlike `"+1"` there is nothing in the text's
        # *shape* that suggests a number.
        "name": "pluralize/exponent-numeric-string-is-one",
        "filter": "pluralize",
        "value": "1e0",
    },
    {
        # A list *is* `len`-able, so this is the `TypeError` arm answering
        # legitimately: `len([1]) == 1` is singular. Paired with the row below,
        # which is the same arm reaching the other suffix.
        "name": "pluralize/list-of-one-is-len-based",
        "filter": "pluralize",
        "value": [1],
    },
    {
        "name": "pluralize/empty-list-is-plural",
        "filter": "pluralize",
        "value": [],
    },
    {
        # `float(None)` is a `TypeError` and `len(None)` is another one, and the
        # second escaping is what makes this `""` rather than a raise.
        "name": "pluralize/none-has-no-length-either",
        "filter": "pluralize",
        "value": None,
    },
    {
        # `float(True)` is `1.0`, so a `bool` is a number here and not a length.
        "name": "pluralize/true-is-one",
        "filter": "pluralize",
        "value": True,
    },
    {
        # A `SafeString` argument is a `str`, so it survives the `,` test and
        # the concatenation — and the concatenation's result is what `.split`
        # is called on, which is why the argument's safety cannot change the
        # answer for a *valid* argument.
        "name": "pluralize/safe-argument-still-splits",
        "filter": "pluralize",
        "value": 2,
        "arg": mark_safe("s"),
    },
    # --- wave 3: `floatformat` ---------------------------------------------
    #
    # `floatformat` has **five return sites** and the rows below are grouped by
    # which one answers rather than by input shape, because two of them emit the
    # same characters: `floatformat(10**199)` is `SafeString('1' + '0' * 199)`
    # and `floatformat(10**200)` is the same digits as a plain `str`. The
    # comparison above cannot see the difference at all — the safety assertion is
    # what does — and the pairing is deliberate for that reason.
    #
    # Three of the five sites are the *same* line: `return input_val`
    # (`defaultfilters.py:163`, `defaultfilters.py:177`), reached by an
    # argument that will not parse, by a value
    # whose digits-and-exponent sum exceeds 200, and by a non-finite value whose
    # `as_tuple()` exponent is a letter. A body that got any one of them right and
    # collapsed the others into it would pass a corpus that held one row per
    # *text*, so the rows are one per route.
    {
        # `arg=-1` (`defaultfilters.py:95`) on a value with nothing after the
        # point: `m` is falsy and `p <= 0`, so this is the integral arm
        # (`defaultfilters.py:184-192`) and the text is
        # `"%d" % int(d)`. The `mark_safe` is the first of two, and this row is a
        # `SafeString` while the refusal rows below are not.
        "name": "floatformat/no-argument-integers-are-whole",
        "filter": "floatformat",
        "value": 34.0,
    },
    {
        # The same arm on the same default with a fractional value: still `-1`
        # place, and `quantize`'s `ROUND_HALF_UP` rounds *away from zero* here.
        "name": "floatformat/no-argument-rounds-half-up",
        "filter": "floatformat",
        "value": 34.26,
    },
    {
        # **`0` is an argument and not the absence of one**, which is the whole
        # row: `p = 0` is not `p = -1`, so the same value that answers `'34.3'`
        # above answers `'34'` here. A body reading a falsy argument as the
        # default agrees on `floatformat` and diverges on this token.
        "name": "floatformat/zero-argument-is-not-no-argument",
        "filter": "floatformat",
        "value": 34.26,
        "arg": 0,
    },
    {
        # A negative `p` rounds to the left of the point and pads with zeros to
        # `abs(p)` places, which is what separates it from `3`: `abs(p)` is used
        # for the *scale* and the sign only decides the direction.
        "name": "floatformat/negative-argument-rounds-left-of-the-point",
        "filter": "floatformat",
        "value": 34.26,
        "arg": -3,
    },
    {
        # The ordinary right-hand case, and the base the `'g'` rows below are
        # read against.
        "name": "floatformat/two-argument-keeps-two-places",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": 2,
    },
    {
        # `abs(p)` large enough that the zero padding is visible, and the row
        # that shows the padding is string repetition over `digits` rather than a
        # formatted precision — `prec` is raised past `getcontext().prec` for it.
        "name": "floatformat/twenty-argument-is-long-form",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": 20,
    },
    {
        # `'g'` sets `force_grouping=True` *and* leaves `use_l10n=True`
        # (`defaultfilters.py:146-148`), so the separator appears **whatever the
        # settings say** — which is why no `USE_THOUSAND_SEPARATOR` row sits on
        # this shape and one sits on the no-flag shape below.
        "name": "floatformat/g-flag-groups-thousands",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": "g",
    },
    {
        # The digit and the flag together: `arg[:-1] or -1` strips the flag
        # *before* the `int()`, so the digit survives and the flag is still set.
        "name": "floatformat/g-flag-with-a-digit",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": "2g",
    },
    {
        # **The flag match is case-sensitive**, and this is the row that says so:
        # `'G'` is not `'g'`, so no flag is stripped, `int('G')` is a `ValueError`
        # and the answer is the *unmarked* `str(value)` — `'6666.6666'`, not the
        # `'6,666.7'` a case-insensitive body would produce. Measured, and it
        # refutes what this row was first written to claim.
        "name": "floatformat/upper-G-is-not-the-g-flag",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": "G",
    },
    {
        # `'g0'` and `'2G'` are the same row from the other side: the suffix test
        # reads the **last** character, so a string that has the flag earlier is
        # not a flagged argument at all.
        "name": "floatformat/flag-not-at-the-end-is-not-a-flag",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": "g0",
    },
    {
        # `'u'` turns localization off (`defaultfilters.py:149-151`), and the row
        # exists to be compared with `g-flag-groups-thousands` above: same value,
        # same digits, and the separator is the difference. It is also the row
        # that would move under `LANGUAGE_CODE` if `use_l10n` were not being
        # passed through.
        "name": "floatformat/u-flag-suppresses-grouping",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": "u",
    },
    {
        # Both flags, `'u'` first — and the measured answer is **ungrouped**,
        # even though `force_grouping` is set: the grouping is `use_l10n and
        # USE_THOUSAND_SEPARATOR` *or* `force_grouping`, and the second term
        # reaches a separator only when the first is on. The two orders are
        # separate rows because `arg[-2:] in {"gu", "ug"}` is a set membership
        # while the digit test reads one character, so a body keyed on order
        # would pass one and fail the other.
        "name": "floatformat/u-then-g-still-ungrouped",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": "ug",
    },
    {
        "name": "floatformat/g-then-u-still-ungrouped",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": "gu",
    },
    {
        # A digit with `'u'`: two places, and `use_l10n=False` means the locale
        # cannot move it.
        "name": "floatformat/digit-then-u-keeps-the-digit",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": "2u",
    },
    {
        # The argument will not parse (`int('x')` at `defaultfilters.py:161`), so
        # the value comes back as `str(value)` and **unmarked** where every row
        # above is a `SafeString`. The text is the one a safe arm could have
        # produced, which is exactly why the flag and not the string is this
        # row's content.
        "name": "floatformat/unparseable-argument-returns-the-value",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": "x",
    },
    {
        # A *number* that is not an integer: `int('2.5')` is a `ValueError` too,
        # so this is the same arm as the row above by a different route — and the
        # pair separates "not a number" from "not an int".
        "name": "floatformat/non-integer-argument-returns-the-value",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": "2.5",
    },
    {
        # `Decimal(str(text))` raises inside `defaultfilters.py:157-159` and
        # `float(text)` raises as well, so `.pop()` leaves `input_val` empty:
        # `''`, unmarked, and the shortest of the five exits.
        "name": "floatformat/non-numeric-value-returns-empty",
        "filter": "floatformat",
        "value": "abc",
    },
    {
        # `None` reaches the same unparseable pair through `str(None)`.
        "name": "floatformat/none-value-returns-empty",
        "filter": "floatformat",
        "value": None,
    },
    {
        # `Decimal("1e300")` parses — so this is *not* the unparseable arm — but
        # one digit and an exponent of 300 sum past 200, so
        # `defaultfilters.py:177` returns the
        # value. A body that parsed the value as a float would agree on the text
        # and lose the flag.
        "name": "floatformat/scientific-string-returns-the-value",
        "filter": "floatformat",
        "value": "1e300",
    },
    {
        # **200 digits is inside the limit**, because the test is `> 200`: this
        # is the integral arm and the answer is a `SafeString` of 200
        # characters. It is here rather than beside a shorter input because at
        # this value the arm and the refusal below emit the *same* 200
        # characters, so an off-by-one in the condition moves only the safety
        # flag — and the flag is the half of the comparison a rendered template
        # cannot make.
        "name": "floatformat/at-the-digit-limit-the-integral-arm-answers",
        "filter": "floatformat",
        "value": "1" + "0" * 199,
    },
    {
        # One digit over, so `defaultfilters.py:177` returns the input
        # **unmarked**: the same
        # digits, the same length story, and the opposite flag.
        "name": "floatformat/one-digit-over-the-limit-returns-the-value",
        "filter": "floatformat",
        "value": "1" + "0" * 200,
    },
    {
        # The same boundary reached from an `int` rather than from a string —
        # `str()` happens during the parse in one case and before it in the
        # other, and the sum is over `Decimal.as_tuple()` either way.
        "name": "floatformat/wide-int-at-the-limit-is-safe",
        "filter": "floatformat",
        "value": 10**199,
    },
    {
        "name": "floatformat/wide-int-over-the-limit-returns-the-value",
        "filter": "floatformat",
        "value": 10**200,
    },
    {
        # **`'F'` is two source arms at once.** `abs(exponent)` on a letter tuple
        # is the `TypeError` the sum catches at `defaultfilters.py:168-170`, *and*
        # `int(d)` on an infinity is the `OverflowError` caught at
        # `defaultfilters.py:180-182` — both mean `return input_val`, and a body
        # that tested only one of them would agree on `inf` and diverge on
        # `nan` (`'n'`), so the pair is the measurement.
        "name": "floatformat/infinity-returns-the-value",
        "filter": "floatformat",
        "value": float("inf"),
    },
    {
        # The sign survives the round trip through `str(value)`, which is what
        # makes this a row rather than a repeat.
        "name": "floatformat/negative-infinity-returns-the-value",
        "filter": "floatformat",
        "value": float("-inf"),
    },
    {
        "name": "floatformat/nan-returns-the-value",
        "filter": "floatformat",
        "value": float("nan"),
    },
    {
        # A **finite** float whose `str()` is exponential, so this reaches the
        # exponent refusal rather than the non-finite one: `'1e+308'`, unmarked.
        # The two refusals are different routes to the same line, and this is the
        # row that shows a float can take either.
        "name": "floatformat/float-beyond-decimal-repr-returns-the-value",
        "filter": "floatformat",
        "value": 1e308,
    },
    {
        # The same refusal from the other end of the range — the smallest
        # denormal, whose `str()` is `'5e-324'` and whose exponent is 324.
        "name": "floatformat/tiny-float-returns-the-value",
        "filter": "floatformat",
        "value": 5e-324,
    },
    {
        # **`m` is the *unrounded* value's polarity and `rounded_d` is the
        # rounded one's**, and here they disagree: the value is not whole (so the
        # integral arm is skipped) and the rounded value is zero (so `if sign and
        # rounded_d` at `defaultfilters.py:210` drops the minus). A body that
        # tested the sign of the value it was rounding, or that read `integral`
        # where the seam exposes `truthy`, emits `'-0.00'`.
        "name": "floatformat/negative-rounding-to-zero-drops-the-sign",
        "filter": "floatformat",
        "value": -0.0001,
        "arg": 2,
    },
    {
        # The same expression with both halves true, so the minus survives — the
        # pair is what makes the row above a statement about the polarity rather
        # than about a dropped sign.
        "name": "floatformat/negative-keeps-its-sign",
        "filter": "floatformat",
        "value": -0.5,
        "arg": 0,
    },
    # --- wave 3: `filesizeformat` ------------------------------------------
    #
    # One `int()` inside a `try` for three classes, one negation, and a six-arm
    # branch compared on the **absolute** value — the negation at
    # `defaultfilters.py:922` sits just above the branch
    # (`defaultfilters.py:924-935`) and reads like part of the output formatting.
    # Every answer here is a plain `str` whatever the
    # settings: the filter's
    # `is_safe=True` is the registry's flag and not the body's, which is why the
    # safety half of the comparison is a constant on this filter.
    #
    # The unit words come from `ngettext`/`gettext`, so under the harness's
    # default `en-us` they are the msgids and a body that inlined the English
    # text scores identically. The rows that can tell the two apart are the
    # `LANGUAGE_CODE` ones at the end of this block, and they are the reason
    # `scripts/filter_probe.py` configures `USE_I18N=True` and calls
    # `django.setup()`.
    {
        # The fallback branch: `int()` refused the value, so the answer is
        # `ngettext(..., 0)`'s plural with a zero — and `avoid_wrapping` runs on
        # **every** branch including this one, so even `'0 bytes'` is spelled
        # with a non-breaking space. A body that applied the wrap only to the
        # unit branches agrees on nine rows out of ten and diverges here.
        "name": "filesizeformat/zero",
        "filter": "filesizeformat",
        "value": 0,
    },
    {
        # `ngettext`'s singular, so the count that reaches the choice is the
        # magnitude and not the digits' spelling.
        "name": "filesizeformat/one-byte-is-singular",
        "filter": "filesizeformat",
        "value": 1,
    },
    {
        # The plural half of the same call.
        "name": "filesizeformat/two-bytes-is-plural",
        "filter": "filesizeformat",
        "value": 2,
    },
    {
        # The byte branch's upper edge, where the next row takes the unit branch
        # — `bytes_ < KB` is **strict**, so 1024 belongs to KB.
        "name": "filesizeformat/just-below-one-KB",
        "filter": "filesizeformat",
        "value": 1023,
    },
    {
        "name": "filesizeformat/exactly-one-KB",
        "filter": "filesizeformat",
        "value": 1024,
    },
    {
        # And it stays in that branch, so the boundary is not a rounding
        # artifact of the division.
        "name": "filesizeformat/one-KB-plus-one",
        "filter": "filesizeformat",
        "value": 1025,
    },
    {
        # Still KB, and the mantissa is `1024.0` rather than a rounded `1.0` —
        # which is what shows the division happens *before* `round(value, 1)`.
        "name": "filesizeformat/just-below-one-MB",
        "filter": "filesizeformat",
        "value": 1048575,
    },
    {
        "name": "filesizeformat/exactly-one-MB",
        "filter": "filesizeformat",
        "value": 1 << 20,
    },
    {
        "name": "filesizeformat/exactly-one-GB",
        "filter": "filesizeformat",
        "value": 1 << 30,
    },
    {
        "name": "filesizeformat/exactly-one-TB",
        "filter": "filesizeformat",
        "value": 1 << 40,
    },
    {
        # `PB`, the last arm — there is no threshold above it, so the source's
        # `else` is `"%s PB"` and not a sixth comparison.
        "name": "filesizeformat/exactly-one-PB",
        "filter": "filesizeformat",
        "value": 1 << 50,
    },
    {
        # The last threshold's other side, which is TB.
        "name": "filesizeformat/just-below-one-PB",
        "filter": "filesizeformat",
        "value": (1 << 50) - 1,
    },
    {
        "name": "filesizeformat/beyond-one-PB",
        "filter": "filesizeformat",
        "value": (1 << 50) + 1,
    },
    {
        # `int("1024.5")` is a `ValueError`, so this is the **fallback** branch:
        # `'0 bytes'`. Paired with the row below, where the same digits as a
        # `float` give `'1.0 KB'` — the pair is the measurement that fixes `int()`
        # as the converter and rules out a parse.
        "name": "filesizeformat/numeric-string-is-converted-then-truncated",
        "filter": "filesizeformat",
        "value": "1024.5",
    },
    {
        # `int(1024.5)` truncates to `1024`, so the value lands in the KB branch
        # — off the same digits as the row above and one class apart.
        "name": "filesizeformat/float-is-truncated-not-refused",
        "filter": "filesizeformat",
        "value": 1024.5,
    },
    {
        # `int()` does not accept exponent syntax at all, so this is
        # `'0 bytes'` rather than an `OverflowError`: a body that parsed the
        # string as a float would answer `inf` and then raise the *wrong* error
        # for the same input.
        "name": "filesizeformat/exponent-string-is-not-an-integer",
        "filter": "filesizeformat",
        "value": "1e400",
    },
    {
        # A `TypeError` from `int()`, which the `except` names — the fallback
        # branch rather than an escape.
        "name": "filesizeformat/list-is-not-an-integer",
        "filter": "filesizeformat",
        "value": [1, 2],
    },
    {
        # The same for a mapping, and a second row because the two are different
        # `int()` refusals in CPython.
        "name": "filesizeformat/dict-is-not-an-integer",
        "filter": "filesizeformat",
        "value": {"a": 1},
    },
    {
        # `int(True)` is `1`, so a `bool` is **not** the fallback — and the
        # singular is chosen from it, which is the row that shows the byte arm's
        # count is the int and not the object.
        "name": "filesizeformat/bool-is-an-integer",
        "filter": "filesizeformat",
        "value": True,
    },
    {
        # `int(nan)` raises `ValueError`, which **is** in the `except` tuple, so
        # this answers `'0 bytes'` where the `inf` row in `ERROR_CASES` escapes —
        # the same converter, one class apart, and the class is the whole
        # distinction.
        "name": "filesizeformat/nan-is-caught",
        "filter": "filesizeformat",
        "value": float("nan"),
    },
    {
        # **The negation, at its shortest distance from the boundary.** The byte
        # branch takes the singular because `ngettext` is handed the *negated*
        # (positive) `1`, and the minus is prefixed afterwards — three steps, and
        # this is the only row where the sign reaches the count at all.
        "name": "filesizeformat/negative-one-byte",
        "filter": "filesizeformat",
        "value": -1,
    },
    {
        # Still the byte branch and plural, so the count that reaches `ngettext`
        # is again the negated one.
        "name": "filesizeformat/negative-below-one-KB",
        "filter": "filesizeformat",
        "value": -500,
    },
    {
        # **The row that catches a body comparing the signed value.**
        # `-1025` is below `KB` *signed*, so such a body takes the byte branch
        # and answers `'-1025 bytes'`; Django negates first and answers
        # `'-1.0 KB'`. Every negative below `KB` in magnitude agrees, which is
        # why the corpus holds three at three distances rather than one.
        "name": "filesizeformat/negative-just-above-one-KB",
        "filter": "filesizeformat",
        "value": -1025,
    },
    {
        # The same defect further from the boundary, and therefore harder to
        # argue away: signed, `'-2048 bytes'`; Django, `'-2.0 KB'`.
        "name": "filesizeformat/negative-two-KB",
        "filter": "filesizeformat",
        "value": -2048,
    },
    {
        # And far from it — `'-1000.0 KB'` against a signed body's
        # `'-1024000 bytes'`.
        "name": "filesizeformat/negative-thousand-KB",
        "filter": "filesizeformat",
        "value": -1024000,
    },
    {
        # A unit boundary from below zero, where an off-by-one in the negation
        # shows as the wrong *unit* rather than the wrong text.
        "name": "filesizeformat/negative-exactly-one-MB",
        "filter": "filesizeformat",
        "value": -(1 << 20),
    },
    {
        # **The row Rust cannot answer by negating in place.** `-(2**63)` is
        # `i64::MIN`, so `to_int` returns `Small(i64::MIN)` and a Rust `-x`
        # overflows — a panic in debug and a wrap in release — where Django
        # answers `'-8192.0 PB'` because Python's `int` is unbounded. The
        # negation is a representation question and not an arithmetic one.
        "name": "filesizeformat/negative-i64-min",
        "filter": "filesizeformat",
        "value": -(2**63),
    },
    {
        # A magnitude that is a `Big` on both sides of the negation, so the sign
        # is carried in the text rather than in a register.
        "name": "filesizeformat/negative-beyond-i64",
        "filter": "filesizeformat",
        "value": -(2**100),
    },
    {
        # Just past `f64::MAX` in digits and nowhere near it in value:
        # `10**309 / 2**50` is finite, so the division answers, while
        # `float(10**309)` raises. That asymmetry is why the seam divides rather
        # than converting — and the answer is `'8.8e+293 PB'`, because the
        # quotient's `str()` carries an `'e'`, so `numberformat.format` sends it
        # through `Decimal(str(...))` and then formats a decimal whose exponent
        # plus digits exceed its 200-digit cut-off with `"{:e}"`.
        "name": "filesizeformat/wide-int-that-is-not-a-float",
        "filter": "filesizeformat",
        "value": 10**309,
    },
    # --- wave 3: the settings axis -----------------------------------------
    #
    # **Every row here carries `expected`, and on these rows the literal is the
    # whole test.** `test_filter_parity.settings_for` hands both engines the same
    # override, so a setting that reaches neither — or a name misspelt into a
    # silent no-op, which is the failure mode measured for
    # `USE_THOUSAND_SEPERATOR` — moves both answers together and leaves every
    # comparison above green. Only the pinned literal can tell a live setting
    # from a dead one.
    #
    # `USE_THOUSAND_SEPARATOR` has leverage on **two** row shapes, and the first
    # claim here was wrong about that: it is not only `floatformat` without
    # `'g'`. `filesizeformat(1024000)` is the second — `'1000.0 KB'` becomes
    # `'1,000.0 KB'` — because the separator has nothing to do until a mantissa
    # reaches four digits, which happens only inside
    # `[1000 * 2**shift, 1024 * 2**shift)` for each unit. `filesizeformat(2048)`,
    # `filesizeformat(1048576)` and `floatformat(6666.6666, "g")` are each
    # unmoved by it, and are not rows.
    {
        # The locale reaches the host's `number_format` through `get_format`, and
        # both the thousands separator and the decimal comma move. A body
        # formatting in Rust answers `'1,234,567.5'` under every locale; this
        # literal is what separates it from the one that asks.
        "name": "floatformat/locale-changes-the-separator",
        "filter": "floatformat",
        "value": 1234567.5,
        "arg": "g",
        "settings": {"LANGUAGE_CODE": "de"},
        "expected": "1.234.567,5",
    },
    {
        # The same locale on the **no-flag** path, where grouping is off and only
        # the decimal comma moves. Separate from the row above because the two
        # reach `number_format` with different `force_grouping`, and a body that
        # passed one flag and not the other would move one and not the other.
        "name": "floatformat/locale-changes-the-decimal-comma-on-plain-digits",
        "filter": "floatformat",
        "value": 1234567.5,
        "arg": 2,
        "settings": {"LANGUAGE_CODE": "de"},
        "expected": "1234567,50",
    },
    {
        # The setting's first shape: without `'g'` there is no `force_grouping`,
        # so `USE_THOUSAND_SEPARATOR` is what puts the commas in — and `arg=2`
        # rather than `'g'` is precisely what makes this a different row from
        # `g-flag-groups-thousands` above rather than a duplicate of it.
        "name": "floatformat/thousands-separator-needs-the-no-flag-path",
        "filter": "floatformat",
        "value": 1234567.5,
        "arg": 2,
        "settings": {"USE_THOUSAND_SEPARATOR": True},
        "expected": "1,234,567.50",
    },
    {
        # The locale reaches `filesizeformat`'s `number_format` too, and the row
        # proves the number went through the host rather than through a Rust
        # `format!("{:.1}")`, which answers `'1.1'` in every locale.
        "name": "filesizeformat/locale-changes-the-decimal-comma",
        "filter": "filesizeformat",
        "value": 1234567890,
        "settings": {"LANGUAGE_CODE": "de"},
        "expected": "1,1\xa0GB",
    },
    {
        # **The row that tests the translation seam**, and the one the harness
        # could not make before `ensure_settings` turned i18n on: the unit word
        # is `gettext`'s, the German catalog spells it `Bytes` with a capital
        # `B`, and a body with the English literal in it is correct under the
        # default settings and wrong here. `500` is plural, so this is
        # `ngettext`'s second form rather than its first.
        "name": "filesizeformat/locale-translates-the-byte-unit",
        "filter": "filesizeformat",
        "value": 500,
        "settings": {"LANGUAGE_CODE": "de"},
        "expected": "500\xa0Bytes",
    },
    {
        # And the singular, because `ngettext` chooses on the count: a body that
        # asked the host for the plural and kept the singular as a literal would
        # pass the row above and fail here.
        "name": "filesizeformat/locale-translates-the-singular-unit",
        "filter": "filesizeformat",
        "value": 1,
        "settings": {"LANGUAGE_CODE": "de"},
        "expected": "1\xa0Byte",
    },
    {
        # `USE_THOUSAND_SEPARATOR`'s second row shape, and the four-digit
        # mantissa band that makes it possible: the grouping lands *inside* the
        # number `filesizeformat` composed, which only a body that handed the
        # quotient to the host's formatter can produce.
        "name": "filesizeformat/thousands-separator-in-the-four-digit-mantissa",
        "filter": "filesizeformat",
        "value": 1024000,
        "settings": {"USE_THOUSAND_SEPARATOR": True},
        "expected": "1,000.0\xa0KB",
    },
    {
        # `yesno`'s default argument *is* `gettext("yes,no,maybe")`
        # (`defaultfilters.py:878`), so the wave-2 rows with no argument are the
        # harness's second
        # translation seam: under `en-us` a body that returned `"maybe"` without
        # asking the host is indistinguishable from one that asked.
        "name": "yesno/locale-translates-the-default-arguments",
        "filter": "yesno",
        "value": None,
        "settings": {"LANGUAGE_CODE": "de"},
        "expected": "Vielleicht",
    },
    {
        # The row that shows the translation is the *default* and not a step the
        # filter performs: an explicit argument is used as it is, comma-split in
        # whatever language it arrives in. `meh` is the third element either way,
        # so the literal is the negative half of the pair above.
        "name": "yesno/locale-does-not-touch-an-explicit-argument",
        "filter": "yesno",
        "value": None,
        "arg": "yeah,nope,meh",
        "settings": {"LANGUAGE_CODE": "de"},
        "expected": "meh",
    },
    # --- Task 8, wave A: `escape`, `force_escape`, `escapeseq`, `safe`,
    #     `safeseq`, `escapejs` ---------------------------------------------
    #
    # Two pairs and two singletons, and the rows are chosen for what they
    # separate rather than for breadth:
    #
    # * `escape` and `force_escape` differ in **which escape** they run.
    #   `escape` is `conditional_escape` — the host *protocol*, which asks
    #   `__html__` first (`html.py:115-127`) — so a `SafeData` input
    #   passes through it unchanged, while `force_escape` runs
    #   `django.utils.html.escape`'s five replacements whatever it is handed. The
    #   safe-input rows are the pair no single body can satisfy, and each
    #   filter's other rows are the same table reached by the other route.
    # * `escapeseq` and `safeseq` are the same walk with a different per-element
    #   operation, so their rows carry an input the two operations treat
    #   differently: the `int` 5, which `escape`/`escapejs` coerce to the string
    #   `'5'` and `mark_safe` turns into a `SafeString` `'5'`. A body that walked
    #   with the other operation, or that marked the *list* rather than its
    #   items, is caught by the item types the comparison checks separately.
    # * `escapejs` is a Rust table (§D2) with no host call past the coercion, and
    #   its rows are the table's edges: `\n`, `\x00` and `\x1f` from the
    #   `range(32)` update that spells them as **four uppercase** hex digits, the
    #   twelve entries the explicit dict holds (`\\ "'><&=;-` plus the two line
    #   terminators, `html.py:63-75`), and text with nothing to escape at all.
    {
        # The protocol on a plain `str`: escaped **and** marked, which is the
        # loop's `is_safe` plus its `mark_safe`, since `escape` is `is_safe=True`.
        "name": "escape/plain-text-escapes-and-marks",
        "filter": "escape",
        "value": 'a<b>&"c',
    },
    {
        # The `__html__` branch, and the row `force_escape` below cannot pass:
        # a `SafeData` input is returned **unchanged** and marked, so a body that
        # always ran the table answers `&lt;b&gt;` here.
        "name": "escape/safe-input-passes-through",
        "filter": "escape",
        "value": mark_safe("<b>"),
    },
    {
        # §D3's lazy case: a `__proxy__` whose `str()` is a `SafeString`. The
        # protocol's `__html__` is reached *through* the proxy, so this is the
        # same answer as the row above by a different route — and it is the row
        # that separates "the protocol" from "`isinstance(value, SafeData)`",
        # which a proxy is not.
        "name": "escape/lazy-safe-proxy-is-safe",
        "filter": "escape",
        "value": LAZY_SAFE_PROXY,
    },
    {
        # The coercion: `@stringfilter`'s `str(value)`, so the table is run over
        # a `str` even for an `int` input.
        "name": "escape/non-string-is-coerced",
        "filter": "escape",
        "value": 5,
    },
    {
        # The same coercion to the other spelling, and the row that keeps a body
        # from taking the "nothing to escape" path too early: `None` has no
        # `__html__` and is not empty.
        "name": "escape/none-is-the-string-none",
        "filter": "escape",
        "value": None,
    },
    {
        # **`force_escape` escapes a safe value**, which is the whole distinction
        # from `escape`: same input, `&lt;b&gt;` instead of `<b>`.
        "name": "force_escape/a-safe-input-is-escaped-anyway",
        "filter": "force_escape",
        "value": mark_safe("<b>"),
    },
    {
        # `&` is the first replacement, so an entity is escaped *again* rather
        # than left alone — `escape` behaves the same way, and the pair of rows
        # says the difference between the two filters is the protocol and not the
        # table.
        "name": "force_escape/an-entity-is-escaped-again",
        "filter": "force_escape",
        "value": "&lt;",
    },
    {
        # The coercion again, from the side where the table's own output is what
        # is marked: `force_escape` has no host call to mark it.
        "name": "force_escape/none-is-the-string-none",
        "filter": "force_escape",
        "value": None,
    },
    {
        # One `conditional_escape` per item, and the element types are half the
        # answer: the items are `SafeString`s while the **list** is not safe.
        "name": "escapeseq/each-item-is-escaped-and-marked",
        "filter": "escapeseq",
        "value": ["a", "<b>"],
    },
    {
        # The empty sequence, which is also the row that fixes the answer's type:
        # a `list`, never the tuple it may have been handed.
        "name": "escapeseq/empty-sequence-answers-an-empty-list",
        "filter": "escapeseq",
        "value": [],
    },
    {
        # A tuple in, a list out, with the same per-item treatment — so a body
        # that returned its input unchanged is caught here and not above.
        "name": "escapeseq/a-tuple-becomes-a-list",
        "filter": "escapeseq",
        "value": ("<b>", "c"),
    },
    {
        # `mark_safe` and not a Rust `Value::Safe(to_str(…))`: the `str` case is
        # where the two are indistinguishable, which is why it is not the row
        # that settles it.
        "name": "safe/a-string-is-marked",
        "filter": "safe",
        "value": "abc",
    },
    {
        # The row that settles it: `mark_safe` on a non-`str` computes `str(v)`
        # and answers a `SafeString`, where a body building the value in Rust
        # would have to reproduce both halves.
        "name": "safe/a-non-string-is-converted-and-marked",
        "filter": "safe",
        "value": 5,
    },
    {
        # `None` is not "nothing": `str(None)` is `'None'`, and a body that
        # treated a null value as empty would answer `''` here.
        "name": "safe/none-is-the-string-none",
        "filter": "safe",
        "value": None,
    },
    {
        # `mark_safe` per item, and the `int` is the row's point: it becomes the
        # **`SafeString`** `'5'`, which `escapeseq`'s identical walk would have
        # left as a plain `'5'` inside an escaped item.
        "name": "safeseq/each-item-is-marked-including-the-int",
        "filter": "safeseq",
        "value": ["a", 5, "<b>"],
    },
    {
        # The empty sequence again: the answer is a `list` here too, so the two
        # sequence filters are compared on the same shape.
        "name": "safeseq/empty-sequence-answers-an-empty-list",
        "filter": "safeseq",
        "value": [],
    },
    {
        # A `SafeData` input is *not* special to `escapejs`: it has no flags at
        # all (`@register.filter("escapejs")`), so `str`-coerce and run the table
        # happens to a marked string exactly as to a plain one — the opposite of
        # `escape`'s row above, on the same input.
        "name": "escapejs/a-safe-input-is-escaped-too",
        "filter": "escapejs",
        "value": mark_safe("<b>"),
    },
    {
        # Four uppercase hex digits from the `range(32)` update — `\n` is
        # `\\u000A` and not `\\u000a`, and not the two-character `\\n` a body
        # writing its own table would reach for.
        "name": "escapejs/newline-is-the-four-digit-form",
        "filter": "escapejs",
        "value": "\n",
    },
    {
        # The bottom and the top of that range, which bound the update: `\x00`
        # is the first byte it rewrites and `\x1f` the last, so a body whose
        # table started at 1 or stopped at 31 fails one of the two.
        "name": "escapejs/the-control-range-has-both-ends",
        "filter": "escapejs",
        "value": "\x00\x1f",
    },
    {
        # Five of the dict's ten ASCII entries at once — `&`, `<`, `>`, `=`,
        # `;`. They are the *dict*'s rather than the range's, which is why they
        # are pinned as a set: a body that built the table from `range(32)` alone
        # leaves every one of them unescaped.
        "name": "escapejs/the-explicit-dict-characters",
        "filter": "escapejs",
        "value": "&<>=;",
    },
    {
        # The four that exist because a JavaScript string literal needs them —
        # backslash, apostrophe, quote, backtick — and the row where a body that
        # escaped *HTML* instead (or that reused `html_escape`'s table) differs
        # on all four. `-` rides along in the middle, the fifth of the ten.
        "name": "escapejs/quote-apostrophe-backslash-backtick",
        "filter": "escapejs",
        "value": "'\"\\`-",
    },
    {
        # The two line terminators JavaScript treats as newlines, and the reason
        # the table cannot be a `range(32)` update alone: they are above 32.
        "name": "escapejs/the-line-terminators",
        "filter": "escapejs",
        "value": "  ",
    },
    {
        # Text with nothing to escape comes back unchanged — the row that fails
        # for a body that escaped by a rule (say, everything non-alphanumeric)
        # rather than by the measured table.
        "name": "escapejs/plain-text-is-unchanged",
        "filter": "escapejs",
        "value": "plain text",
    },
    {
        # `escapejs` is `is_safe=False`, so the loop marks nothing and the
        # `SafeString` here is the **body's** own marking — the row separates
        # "the loop marked it" from "the table's output was marked".
        "name": "escapejs/the-marking-is-the-bodys-own",
        "filter": "escapejs",
        "value": None,
    },
    # --- striptags (`defaultfilters.py:528-532`, is_safe=True, `@stringfilter`)
    #
    # The whole body is the host's `strip_tags`, so what these rows pin is the
    # Python tag grammar rather than Rust: the stripper is an `HTMLParser` fed
    # `convert_charrefs=False` (`html.py:181-197`), and the two rows that matter
    # most are the entity one (a body that *decoded* entities would differ on a
    # row that looks like it should round-trip) and the deep-nesting one.
    {
        "name": "striptags/basic",
        "filter": "striptags",
        "value": "<b>hi</b> <i>there</i>",
    },
    {
        # **Not** decoded: `&#65;` stays `&#65;` and is not `A`. This is the row
        # a hand-written Rust stripper fails while looking right.
        "name": "striptags/entities-are-kept-verbatim",
        "filter": "striptags",
        "value": "a &amp; b &lt;c&gt; &#65;",
    },
    {
        "name": "striptags/comment",
        "filter": "striptags",
        "value": "a <!-- c --> <b>x</b>",
    },
    {
        # No `>` anywhere, so the iteration loop never runs and the text is the
        # answer unchanged — the malformed input Django leaves alone.
        "name": "striptags/unclosed-tag-without-gt",
        "filter": "striptags",
        "value": "a <b b",
    },
    {
        "name": "striptags/nested",
        "filter": "striptags",
        "value": "<div><span><b>x</b></span></div>",
    },
    {
        # **The row the depth guard must not fire on.** 50 balanced tags go in
        # *one* `_strip_once` iteration, so the iteration counter — which is what
        # `MAX_STRIP_TAGS_DEPTH = 50` (`html.py:44`) bounds — reaches 1. A
        # body that counted nesting instead would raise here and answer nothing.
        "name": "striptags/balanced-50-deep",
        "filter": "striptags",
        "value": "<d>" * 50 + "x" + "</d>" * 50,
    },
    {
        "name": "striptags/balanced-100-deep",
        "filter": "striptags",
        "value": "<d>" * 100 + "x" + "</d>" * 100,
    },
    {
        # `is_safe=True` with a `SafeData` input: the marking is the FFI loop's
        # (`base.py:816`), so the body returns a plain `str` and this row is what
        # says the loop still marks it.
        "name": "striptags/safe-input-marks-the-result",
        "filter": "striptags",
        "value": mark_safe("<b>hi</b>"),
    },
    # --- linebreaks (`defaultfilters.py:485-494`, is_safe=True, needs_autoescape)
    {
        # Escaping, the `\n{2,}` paragraph split and the escape-**before**-`<br>`
        # order, all in one row: `'more'.` is `&#x27;more&#x27;` and the single
        # `\n` inside a paragraph is a `<br>`, while `\n\n` is a new `<p>`.
        "name": "linebreaks/escapes-and-splits-paragraphs",
        "filter": "linebreaks",
        "value": "First line.\nSecond <line> & 'more'.\n\nNew paragraph here.",
    },
    {
        # **Pinned**, because this row is the group's `autoescape=False`
        # detector and parity alone cannot fail on it: drop the key and both
        # engines escape, so a body that never read `autoescape` stays green.
        # The literal is what reddens. See the module doc on `expected`.
        "name": "linebreaks/autoescape-false-does-not-escape",
        "filter": "linebreaks",
        "value": "First line.\nSecond <line> & 'more'.\n\nNew paragraph here.",
        "context": {"autoescape": False},
        "expected": "<p>First line.<br>Second <line> & 'more'.</p>\n\n<p>New paragraph here.</p>",
    },
    {
        # `autoescape and not isinstance(value, SafeData)` — the second half of
        # the same expression, which no `context` key can reach.
        "name": "linebreaks/safe-data-skips-escaping",
        "filter": "linebreaks",
        "value": mark_safe("First line.\nSecond <line> & 'more'.\n\nNew paragraph here."),
    },
    {
        # `normalize_newlines` runs **before** the split, so `\r\n` is one `\n`
        # and a lone `\r` is another: a body that split first would answer
        # `'<p>a</p>\n\n<p>b</p>…'`.
        "name": "linebreaks/normalizes-crlf-and-cr",
        "filter": "linebreaks",
        "value": "a\r\nb\rc\n\nd",
    },
    {
        # `re.split("\n{2,}")` consumes the whole **run**: three newlines are one
        # paragraph break, not a break plus a leading newline. The body that fails
        # this is a plain `split("\n\n")`, which answers `'<p>a</p>\n\n<p><br>b</p>'`.
        "name": "linebreaks/run-of-newlines-is-one-break",
        "filter": "linebreaks",
        "value": "a\n\n\nb",
    },
    {
        "name": "linebreaks/trailing-newline",
        "filter": "linebreaks",
        "value": "a\n",
    },
    {
        "name": "linebreaks/all-newlines",
        "filter": "linebreaks",
        "value": "\n\n",
    },
    {
        "name": "linebreaks/empty",
        "filter": "linebreaks",
        "value": "",
    },
    {
        "name": "linebreaks/single-line",
        "filter": "linebreaks",
        "value": "just one line",
    },
    # --- linebreaksbr (`defaultfilters.py:497-508`, is_safe=True, needs_autoescape)
    {
        # No paragraph split here at all: `\n\n` is two `<br>`s.
        "name": "linebreaksbr/escapes-newlines",
        "filter": "linebreaksbr",
        "value": "First line.\nSecond <line> & 'more'.\n\nNew paragraph here.",
    },
    {
        # **Pinned**, for `linebreaks`' reason above.
        "name": "linebreaksbr/autoescape-false-does-not-escape",
        "filter": "linebreaksbr",
        "value": "First line.\nSecond <line> & 'more'.\n\nNew paragraph here.",
        "context": {"autoescape": False},
        "expected": "First line.<br>Second <line> & 'more'.<br><br>New paragraph here.",
    },
    {
        "name": "linebreaksbr/safe-data-skips-escaping",
        "filter": "linebreaksbr",
        "value": mark_safe("First line.\nSecond <line> & 'more'.\n\nNew paragraph here."),
    },
    {
        "name": "linebreaksbr/normalizes-crlf",
        "filter": "linebreaksbr",
        "value": "a\r\nb",
    },
    {
        # `"a\n"` is `'a<br>'`: the trailing newline becomes a break like any
        # other, which is the row a body using `split`/`join` gets wrong.
        "name": "linebreaksbr/trailing-newline",
        "filter": "linebreaksbr",
        "value": "a\n",
    },
    {
        # All five characters `escape` touches, in one row, with the `\n` that
        # must survive as the only thing replaced: `&#x27;` is the apostrophe
        # and `&quot;` is the quote, so a body that escaped only `<>&` differs.
        "name": "linebreaksbr/all-five-escapes-before-the-br",
        "filter": "linebreaksbr",
        "value": "<>&''\n\"",
    },
    # --- linenumbers (`defaultfilters.py:230-244`, is_safe=True, needs_autoescape)
    {
        # 12 lines, so the width is `len("12") == 2` — and the padding is the
        # row: `'01. line 1'`, not `'1. line 1'`.
        "name": "linenumbers/pads-to-the-digit-count",
        "filter": "linenumbers",
        "value": "\n".join(f"line {i}" for i in range(1, 13)),
    },
    {
        # **Pinned**, for `linebreaks`' reason above.
        "name": "linenumbers/autoescape-false-does-not-escape",
        "filter": "linenumbers",
        "value": "\n".join(f"line {i}" for i in range(1, 13)),
        "context": {"autoescape": False},
        "expected": "\n".join(f"{i:02}. line {i}" for i in range(1, 13)),
    },
    {
        # 100 lines makes the width three, so the *same* first line is
        # `'001. line 1'` — the row that separates a computed width from a
        # constant one.
        "name": "linenumbers/hundred-lines-pad-to-three",
        "filter": "linenumbers",
        "value": "\n".join(f"line {i}" for i in range(1, 101)),
    },
    {
        "name": "linenumbers/escapes-each-line",
        "filter": "linenumbers",
        "value": "<a>\n<b>",
    },
    {
        "name": "linenumbers/safe-data-skips-escaping",
        "filter": "linenumbers",
        "value": mark_safe("<a>\n<b>"),
    },
    {
        "name": "linenumbers/single-line",
        "filter": "linenumbers",
        "value": "only",
    },
    {
        # One empty line, so `'1. '` — with the trailing space. A body that
        # answered `''` passes every row above.
        "name": "linenumbers/empty-is-one-empty-line",
        "filter": "linenumbers",
        "value": "",
    },
    {
        # **There is no `normalize_newlines` in this body.** `"a\r\nb"` keeps its
        # `\r` and numbers two lines, where `linebreaks` on the same input would
        # have folded it — the row that catches a body sharing the wrong helper
        # with its neighbours in Task 8.
        "name": "linenumbers/keeps-carriage-return",
        "filter": "linenumbers",
        "value": "a\r\nb",
    },
    # --- iriencode (`defaultfilters.py:223-227`, is_safe=True, `@stringfilter`)
    #
    # The whole body is `iri_to_uri`, so these rows pin the host's function
    # rather than anything Rust decides.
    {
        "name": "iriencode/plain",
        "filter": "iriencode",
        "value": "abc",
    },
    {
        # `/` and `?`, `=`, `&`, `#` are all left alone by `iri_to_uri` while
        # the space and the non-ASCII are quoted — so this row separates
        # `iri_to_uri` from `quote`, which the filter below uses.
        "name": "iriencode/mixed",
        "filter": "iriencode",
        "value": "a b/c?d=e&f#g h iéü",
    },
    {
        "name": "iriencode/already-quoted-is-idempotent",
        "filter": "iriencode",
        "value": "a%20b",
    },
    {
        "name": "iriencode/int-coerces",
        "filter": "iriencode",
        "value": 5,
    },
    {
        "name": "iriencode/safe-input-marks-the-result",
        "filter": "iriencode",
        "value": mark_safe("abc"),
    },
    # --- urlencode (`defaultfilters.py:365-379`, is_safe=False, `@stringfilter`)
    #
    # Django's body is two lines — `kwargs["safe"] = safe` unless it is `None`,
    # then `quote(value, **kwargs)` — and the rows below are the whole of what
    # those two lines do. The `arg_variable` rows are the point of the group:
    # `quote`'s non-`str` branch is `bytes([c for c in safe if c < 128])`
    # (`urllib/parse.py`, `quote_from_bytes`), which **answers** for an
    # all-`int` container and for `bytes`, so a body that classified the
    # argument in Rust and raised would redden three of these rows.
    {
        "name": "urlencode/default-safe-is-slash",
        "filter": "urlencode",
        "value": "a b/c?d=e&f#g",
    },
    {
        # `safe=''` escapes the `/` too — the only difference from the default.
        "name": "urlencode/safe-empty-quotes-the-slash",
        "filter": "urlencode",
        "value": "a b/c?d=e&f#g",
        "arg": "",
    },
    {
        # Non-empty but none of `a`,`b`,`c` occurs in the input, so this row's
        # answer equals `safe=''`'s — which is why it is here beside it: the two
        # rows together say the selection is a *set membership*, not a string
        # comparison.
        "name": "urlencode/safe-abc-is-not-in-the-input",
        "filter": "urlencode",
        "value": "a b/c?d=e&f#g",
        "arg": "abc",
    },
    {
        # An explicit `None` is the absent argument: Django's `if safe is not
        # None` leaves `kwargs` empty, so `quote`'s own `'/'` default applies.
        "name": "urlencode/safe-none-is-the-default",
        "filter": "urlencode",
        "value": "a b/c?d=e&f#g",
        "arg": None,
    },
    {
        "name": "urlencode/safe-slash-explicit",
        "filter": "urlencode",
        "value": "a b/c",
        "arg": "/",
    },
    {
        # `is_safe=False`, so nothing is marked and the `SafeString` input comes
        # back as a plain `str`.
        "name": "urlencode/safe-value-is-never-marked",
        "filter": "urlencode",
        "value": mark_safe("a b/c"),
    },
    {
        "name": "urlencode/int-coerces",
        "filter": "urlencode",
        "value": 5,
    },
    {
        "name": "urlencode/unicode",
        "filter": "urlencode",
        "value": "éü 中",
    },
    {
        # `safe=[1]` **answers**: the comprehension keeps `1`, `bytes([1])` is
        # `b'\x01'`, and `'\x01'` is not in the input, so the answer is
        # `safe=''`'s. A body that synthesised a refusal for a non-`str`
        # argument fails here rather than passing.
        "name": "urlencode/safe-int-list-answers",
        "filter": "urlencode",
        "value": "a b/c",
        "arg_variable": [1],
    },
    {
        # `safe=[]` keeps nothing, so this too is `safe=''`'s answer.
        "name": "urlencode/safe-empty-list-answers",
        "filter": "urlencode",
        "value": "a b/c",
        "arg_variable": [],
    },
    {
        # `bytes` is not a `str`, so it takes the comprehension — over the
        # *integers* `b'/'` yields, `47 < 128` holds, and `bytes([47])` is
        # `b'/'`: the `/` stays. The row that would fail for `quote` being
        # handed a Rust `&str` of the argument rather than the object.
        "name": "urlencode/safe-bytes-answers",
        "filter": "urlencode",
        "value": "a b/c",
        "arg_variable": b"/",
    },
    # --- Task 9, wave A: `slugify`, `phone2numeric`, `pprint` ----------------
    #
    # All three bodies are thin on purpose, and that is why these three groups
    # are long. Each delegates its real work to a host operation and owns only
    # the part no host performs: `slugify` (`defaultfilters.py:266-274`) and
    # `phone2numeric` (`defaultfilters.py:984-987`) wrap `django.utils.text`
    # helpers, and `pprint` (`defaultfilters.py:990-996`) owns its `except` arm
    # and nothing else. So no row here asserts "the algorithm is right" — the
    # host's own suite does that — only *which*
    # operation the body chose and *what it handed it*, one measured input per
    # branch, which is the only thing a thin body can get wrong.
    #
    # **`slugify` is `@stringfilter` and `is_safe=True`.** The decorator runs
    # `str(value)` (`defaultfilters.py:38-52`) before the body sees anything, so
    # the `5` and `None` rows below are statements about that coercion rather
    # than about this body, and no refusal is available to it.
    {
        "name": "slugify/ascii-punctuation",
        "filter": "slugify",
        "value": "Hello, World!",
    },
    {
        "name": "slugify/collapses-space-runs",
        "filter": "slugify",
        "value": "  hello  world  ",
    },
    {
        "name": "slugify/collapses-hyphen-runs",
        "filter": "slugify",
        "value": "a--b",
    },
    {
        "name": "slugify/strips-leading-trailing",
        "filter": "slugify",
        "value": "-a-b-",
    },
    {
        # `\w` holds `_`, so the underscore is neither punctuation nor a
        # whitespace run — the one character that survives both `re.sub`s.
        "name": "slugify/underscore-survives",
        "filter": "slugify",
        "value": "a_b",
    },
    {
        # NFKD decomposes `ß` to `ss`, so the ascii drop never sees it; the row
        # that fails for a body lowering *before* normalising.
        "name": "slugify/ascii-fold",
        "filter": "slugify",
        "value": "Straße",
    },
    {
        "name": "slugify/combining-mark-drops",
        "filter": "slugify",
        "value": "á",
    },
    {
        # NFKD maps the superscript to a plain digit — the row that pins the
        # normalisation step rather than the two `re.sub`s.
        "name": "slugify/superscript-folds-to-digit",
        "filter": "slugify",
        "value": "²",
    },
    {
        # Greek is not decomposed by NFKD and the ascii encode drops it whole,
        # so the answer is `""` — not `"ος"`, and not a refusal.
        "name": "slugify/non-ascii-drops-to-empty",
        "filter": "slugify",
        "value": "ΟΣ",
    },
    {
        "name": "slugify/email",
        "filter": "slugify",
        "value": "hello@example.com",
    },
    {
        "name": "slugify/mixed-case-and-digit",
        "filter": "slugify",
        "value": "CamelCase 42",
    },
    {
        # Tab and newline are `\s`, so they join the space run and collapse with
        # it: `[-\s]+` is one character class, not two passes.
        "name": "slugify/whitespace-runs-including-tabs",
        "filter": "slugify",
        "value": "a b\tc\nd",
    },
    {
        # `@stringfilter`: an `int` arrives already `str()`d.
        "name": "slugify/coerces-non-string",
        "filter": "slugify",
        "value": 5,
    },
    {
        "name": "slugify/coerces-none",
        "filter": "slugify",
        "value": None,
    },
    {
        # Safe in, safe out — by the loop's `is_safe` rule, not the body's.
        "name": "slugify/safe-input",
        "filter": "slugify",
        "value": mark_safe("AbC dEf"),
    },
    # **`phone2numeric` has no `@stringfilter`**, and that is its whole shape:
    # `phone.lower()` (`text.py:344`) is a `str` method, so the argument's *type*
    # is observed and a non-`str` must raise rather than become `str(value)`.
    # Those refusals are `ERROR_CASES` rows. The rows that matter here are the
    # two lowercasing ones — `final-sigma` and `rust-only-lower-code-point` —
    # each of which fails for a body that lowered in Rust instead of delegating.
    {
        "name": "phone2numeric/letters-and-punctuation",
        "filter": "phone2numeric",
        "value": "800-COLLECT",
    },
    {
        "name": "phone2numeric/1-800-flowers",
        "filter": "phone2numeric",
        "value": "1-800-FLOWERS",
    },
    {
        "name": "phone2numeric/mixed-case",
        "filter": "phone2numeric",
        "value": "aBc",
    },
    {
        "name": "phone2numeric/letters-then-upper-tail",
        "filter": "phone2numeric",
        "value": "abcXYZ",
    },
    {
        # Punctuation and the underscore are not in the table, so
        # `char2number.get(c, c)` returns them unchanged.
        "name": "phone2numeric/punctuation-passes-through",
        "filter": "phone2numeric",
        "value": "a-b_c",
    },
    {
        "name": "phone2numeric/digits-pass-through",
        "filter": "phone2numeric",
        "value": "123-456",
    },
    {
        "name": "phone2numeric/empty",
        "filter": "phone2numeric",
        "value": "",
    },
    {
        "name": "phone2numeric/non-ascii-not-mapped",
        "filter": "phone2numeric",
        "value": "éè",
    },
    {
        "name": "phone2numeric/safe-input",
        "filter": "phone2numeric",
        "value": mark_safe("800-COLLECT"),
    },
    {
        # **The decorator, visible as a type.** A `Promise` in gives a *lazy*
        # result out — measured, `phone2numeric(lazy(...)())` is a `__proxy__`
        # and not a `str` — so both sides answer a proxy here rather than text.
        # This is the row a split body cannot produce: owning the table means
        # returning `Value::Str`, whose class name is `str`, and no amount of
        # table-correctness reaches the wrapper.
        "name": "phone2numeric/lazy-input",
        "filter": "phone2numeric",
        "value": LAZY_SAFE_PROXY,
    },
    {
        # **Final_Sigma**: CPython's `str.lower()` answers `"ος"` (final form),
        # where Rust's `char::to_lowercase` answers `"οσ"`. The scripted test in
        # `phone2numeric.rs` pins the same input; this row pins it through the
        # engine, where the host is whatever `str_lower` really calls.
        "name": "phone2numeric/final-sigma",
        "filter": "phone2numeric",
        "value": "ΟΣ",
    },
    {
        # **U+A7CE** is one of the 28 code points an exhaustive 0..0x10FFFF diff
        # found where Rust's `char::to_lowercase` maps and CPython's `str.lower`
        # does not — this CPython has no name for the character at all, which is
        # what a Unicode-table version gap looks like. So the answer is the
        # character itself, and the row distinguishes "the host lowers" from
        # "something lowers": a Rust `to_lowercase` would answer a *different*
        # character here, still outside the a–z table. Written as an escape
        # because the codepoint is the whole point of the row.
        "name": "phone2numeric/rust-only-lower-code-point",
        "filter": "phone2numeric",
        "value": "꟎",
    },
    # **`pprint` is `pformat(value)` and its `except` arm**, so its rows are the
    # input classes `pformat` renders differently plus the single one that makes
    # it raise. `is_safe=True` marks through the loop, so the body returns a
    # plain `str` and the safe row below is the loop's doing.
    {
        "name": "pprint/list",
        "filter": "pprint",
        "value": [1, 2, 3],
    },
    {
        "name": "pprint/dict",
        "filter": "pprint",
        "value": {"a": 1, "b": 2},
    },
    {
        "name": "pprint/string",
        "filter": "pprint",
        "value": "abc",
    },
    {
        "name": "pprint/int",
        "filter": "pprint",
        "value": 5,
    },
    {
        "name": "pprint/none",
        "filter": "pprint",
        "value": None,
    },
    {
        "name": "pprint/bool",
        "filter": "pprint",
        "value": True,
    },
    {
        "name": "pprint/empty-list",
        "filter": "pprint",
        "value": [],
    },
    {
        "name": "pprint/empty-dict",
        "filter": "pprint",
        "value": {},
    },
    {
        "name": "pprint/tuple",
        "filter": "pprint",
        "value": (1, 2),
    },
    {
        "name": "pprint/safe-input",
        "filter": "pprint",
        "value": mark_safe("abc"),
    },
    {
        # The reachable `except Exception` arm: `pformat` calls `__repr__`, which
        # raises, and Django *answers* with the formatted error string rather
        # than propagating. `LOUD_REPR` and not `LOUD_STR` — see its docstring.
        "name": "pprint/repr-raises",
        "filter": "pprint",
        "value": LOUD_REPR(),
    },
    # **`urlize` (`defaultfilters.py:382-386`) and `urlizetrunc`
    # (`defaultfilters.py:389-400`).** The helper is `django.utils.html.urlize` —
    # the host's — so the body is the
    # seam call plus `mark_safe`, and the rows below are about what the body
    # hands it. Two of them carry `settings` (`URLIZE_ASSUME_HTTPS`) and are
    # therefore pinned with `expected`; see the module doc on why a settings row
    # cannot be a comparison alone.
    {
        "name": "urlize/https-link",
        "filter": "urlize",
        "value": "see https://example.com/x now",
    },
    {
        # `settings.URLIZE_ASSUME_HTTPS` chooses the protocol for a bare domain,
        # and it is read *live* from `settings` inside `Urlizer.handle_word`
        # (`html.py:363-366`) rather than cached — measured in one process, the
        # two overrides below move the answer. `False` is `global_settings`'
        # own value (`global_settings.py:676`) and the state the warning
        # (`RemovedInDjango2028Warning`) is emitted under; the warning itself is
        # not an answer and no row can carry it, so the *effect* is what these
        # two pin.
        "name": "urlize/www-bare-domain-default-http",
        "filter": "urlize",
        "value": "visit www.example.com please",
        "settings": {"URLIZE_ASSUME_HTTPS": False},
        "expected": (
            'visit <a href="http://www.example.com" rel="nofollow">'
            "www.example.com</a> please"
        ),
    },
    {
        "name": "urlize/www-bare-domain-assume-https",
        "filter": "urlize",
        "value": "visit www.example.com please",
        "settings": {"URLIZE_ASSUME_HTTPS": True},
        "expected": (
            'visit <a href="https://www.example.com" rel="nofollow">'
            "www.example.com</a> please"
        ),
    },
    {
        # A `mailto` link carries no `rel` — `Urlizer.handle_word` sets
        # `nofollow_attr = ""` on the e-mail branch (`html.py:383`).
        "name": "urlize/email",
        "filter": "urlize",
        "value": "mail me at a@b.com",
    },
    {
        # The answer is the input: `Urlizer.__call__` joins the words back and
        # nothing matched. A safe row too — the body's own `mark_safe`.
        "name": "urlize/no-links",
        "filter": "urlize",
        "value": "no links here",
    },
    {
        # The surrounding text is escaped, the link is not: escaping happens on
        # `lead`/`trail`/`trimmed`, never on the `href`.
        "name": "urlize/escapes-the-surrounding-text",
        "filter": "urlize",
        "value": "<b>bold</b> https://e.com",
    },
    {
        # **The `safe_input` row.** `Urlizer.__call__` reads
        # `safe_input = isinstance(text, SafeData)` (`html.py:319`), and with it
        # the `<b>` is *not* escaped, where the row above escapes the same text.
        # `mark_safe` at the *value* key, not `arg`: this is the filter's input.
        #
        # The seam takes `&str`, so the marker cannot cross it — the body
        # collapses `autoescape and not safe_input` itself, exactly as Django's
        # own `linebreaks` def does (`defaultfilters.py:486`) and as
        # `linebreaks.rs` carries it. This row is what pins that collapse: a body
        # handing the seam `call.autoescape` unchanged escapes here.
        "name": "urlize/safe-input-is-not-escaped",
        "filter": "urlize",
        "value": mark_safe("<b>bold</b> https://e.com"),
    },
    {
        # `needs_autoescape`: the flag is read off the `Context`, so this key is
        # the only way to reach the other branch. It *depends* on the key —
        # dropping it escapes the `<b>` on both engines — so the literal is
        # pinned as well as the comparison.
        "name": "urlize/autoescape-off-does-not-escape",
        "filter": "urlize",
        "value": "<b>bold</b> https://e.com",
        "context": {"autoescape": False},
        "expected": (
            '<b>bold</b> <a href="https://e.com" rel="nofollow">https://e.com</a>'
        ),
    },
    {
        # `@stringfilter`: an `int` arrives already `str()`d, so there is no URL
        # to find and the answer is the text of the value.
        "name": "urlize/coerces-non-string",
        "filter": "urlize",
        "value": 5,
    },
    {
        "name": "urlize/coerces-none",
        "filter": "urlize",
        "value": None,
    },
    # `urlizetrunc`, on a URL long enough to truncate at every limit below. The
    # link is always `<a href="https://example.com/very/long/path"
    # rel="nofollow">…</a>`; `trim_url` (`html.py:407-410`) is what decides the
    # text between the tags.
    {
        "name": "urlizetrunc/truncates-at-five",
        "filter": "urlizetrunc",
        "value": "see https://example.com/very/long/path now",
        "arg": 5,
    },
    {
        # A quoted argument is a *constant* (`base.py:788-792`) and a `str`; the
        # body's `int(limit)` is what turns it into the int the seam takes.
        "name": "urlizetrunc/quoted-limit",
        "filter": "urlizetrunc",
        "value": "see https://example.com/very/long/path now",
        "arg": "5",
    },
    {
        # A **variable** argument: a bare name, resolved through the context as
        # an `int`. `arg_variable` rather than `arg` because a template's `x|f:5`
        # is already the bare-token constant case; this is the lookup.
        "name": "urlizetrunc/variable-limit",
        "filter": "urlizetrunc",
        "value": "see https://example.com/very/long/path now",
        "arg_variable": 5,
    },
    {
        # `x[: max(0, limit - 1)]` is empty for `0`, so the link text is the
        # ellipsis alone.
        "name": "urlizetrunc/zero-limit",
        "filter": "urlizetrunc",
        "value": "see https://example.com/very/long/path now",
        "arg": 0,
    },
    {
        # **The row the `Option<usize>` seam could not carry** (the change
        # `d74192e` landed): a negative limit is not rejected, clamped or
        # sign-flipped — `len(x) <= -1` is false and `max(0, -2)` is `0`, so the
        # answer is `'…'`, the same as `0`.
        "name": "urlizetrunc/negative-limit",
        "filter": "urlizetrunc",
        "value": "see https://example.com/very/long/path now",
        "arg": -1,
    },
    {
        "name": "urlizetrunc/negative-limit-beyond-zero",
        "filter": "urlizetrunc",
        "value": "see https://example.com/very/long/path now",
        "arg": -5,
    },
    {
        # `max(0, 0)` and `max(0, 2)` are the boundary either side of the
        # ellipsis: one character of the URL survives at `3`, none at `1`.
        "name": "urlizetrunc/limit-one",
        "filter": "urlizetrunc",
        "value": "see https://example.com/very/long/path now",
        "arg": 1,
    },
    {
        "name": "urlizetrunc/limit-three",
        "filter": "urlizetrunc",
        "value": "see https://example.com/very/long/path now",
        "arg": 3,
    },
    {
        # `len(x) <= limit`, so no ellipsis and the whole URL.
        "name": "urlizetrunc/limit-beyond-the-url",
        "filter": "urlizetrunc",
        "value": "see https://example.com/very/long/path now",
        "arg": 1000,
    },
    {
        # **A `Big` limit is not an overflow.** The seam rebuilds it through
        # Python's own `int()` and `len(x) <= limit` holds, so it is
        # indistinguishable from `1000` — and nothing in Rust decided it.
        "name": "urlizetrunc/ten-to-the-twentyninth",
        "filter": "urlizetrunc",
        "value": "see https://example.com/very/long/path now",
        "arg": 10**29,
    },
    {
        # The `safe_input` collapse again, on the truncating call site: the
        # surrounding `<b>` is *not* escaped because the value is safe, where a
        # plain `str` would have it escaped.
        "name": "urlizetrunc/safe-input-is-not-escaped",
        "filter": "urlizetrunc",
        "value": mark_safe("<b>bold</b> https://e.com"),
        "arg": 5,
    },
    {
        "name": "urlizetrunc/autoescape-off-does-not-escape",
        "filter": "urlizetrunc",
        "value": "<b>bold</b> https://e.com",
        "arg": 5,
        "context": {"autoescape": False},
        "expected": (
            '<b>bold</b> <a href="https://e.com" rel="nofollow">http…</a>'
        ),
    },
    # **`json_script` (`defaultfilters.py:85-91`).** The body owns the
    # `_json_script` assembly (`html.py:95-112`): `json_dumps` is the host's, and
    # the three `_json_script_escapes` entries, the template choice and the
    # `mark_safe` are Rust's. Every answer is a `SafeString`.
    {
        # The space after the colon is `json.dumps`' *default* separator
        # (`", "`/`": "`), which an earlier revision of the seam got wrong —
        # `ece8a3b`.
        "name": "json_script/dict",
        "filter": "json_script",
        "value": {"a": 1},
    },
    {
        "name": "json_script/list-with-id",
        "filter": "json_script",
        "value": [1, 2],
        "arg": "i",
    },
    {
        # **All three `_json_script_escapes` entries in one answer**, and the
        # JSON's own `"` quoting *not* among them: the map has three entries.
        "name": "json_script/escapes-the-payload",
        "filter": "json_script",
        "value": {"a": "<b>&c"},
    },
    {
        # `json.dumps`' own quoting survives the `.translate` untouched — the
        # backslashes are the encoder's, not the escapes'.
        "name": "json_script/preserves-json-quoting",
        "filter": "json_script",
        "value": {"a": 'he said "hi"'},
    },
    {
        # **`cls=DjangoJSONEncoder` is the whole reason these four rows exist.**
        # A plain `json.dumps` raises `TypeError` on a `date`, a `Decimal` or a
        # `UUID`; the encoder is what makes them strings. The defect `ece8a3b`
        # found and no gate could see, because `json_script` was a stub and
        # `json_dumps` had no other caller.
        "name": "json_script/date",
        "filter": "json_script",
        "value": {"d": date(2026, 10, 8)},
    },
    {
        # A string, and the trailing zero survives — `Decimal`'s `str`, not a
        # float's `repr`.
        "name": "json_script/decimal-keeps-trailing-zero",
        "filter": "json_script",
        "value": Decimal("1.50"),
    },
    {
        "name": "json_script/uuid",
        "filter": "json_script",
        "value": UUID(int=0),
    },
    {
        "name": "json_script/none",
        "filter": "json_script",
        "value": None,
    },
    {
        # **`ensure_ascii` is `json.dumps`' own default and is neither set nor
        # needed**: `é` leaves as the six-character escape `é`. (The brief's
        # table for this row shows the character itself; measured on the pinned
        # tree, the encoder escapes it. See the module doc.)
        "name": "json_script/non-ascii-is-escaped",
        "filter": "json_script",
        "value": {"k": "é"},
    },
    {
        # A constant `id` arrives `mark_safe`d from `resolve` (`base.py:788-792`),
        # so `conditional_escape` leaves it alone: `a<b`, not `a&lt;b`. **This is
        # the row that pins `conditional_escape` over `escape`.** A `str`
        # argument is the only shape `filter_cases.py` renders as a quoted
        # constant, which is why the constant and the variable form of this id
        # are not the same row; the variable forms are refused by `arg_token`
        # (see the module doc's note on the two unexpressible ones).
        "name": "json_script/constant-id-is-not-escaped",
        "filter": "json_script",
        "value": [1, 2],
        "arg": "a<b",
    },
    {
        # A non-`str` id is not escaped either — it is `str()`ed by the format
        # and spliced between the quotes.
        "name": "json_script/int-id-is-stringified",
        "filter": "json_script",
        "value": [1, 2],
        "arg": 7,
    },
    {
        # **`if element_id:`, not `if element_id is not None:`** — a falsy id
        # drops the attribute. `""` is a quoted constant, so it arrives
        # `mark_safe`d and still falsy.
        "name": "json_script/empty-id-drops-the-attribute",
        "filter": "json_script",
        "value": [1, 2],
        "arg": "",
    },
    {
        # The falsy id a `is not None` test would get wrong: `0` is an id.
        "name": "json_script/zero-id-drops-the-attribute",
        "filter": "json_script",
        "value": [1, 2],
        "arg": 0,
    },
    # --- date: two branches, two formatters, three catch-lists --------------
    #
    # `date` is `if value in (None, "")` → `formats.date_format(value, arg)` →
    # on `AttributeError` `django.utils.dateformat.format(value, arg)` → on
    # `AttributeError` `""`. The two branches call **different functions**: the
    # first resolves `get_format(arg or "DATE_FORMAT")`, the second uses the
    # argument's text literally. The rows below separate them and pin the guard's
    # two sentinels rather than truthiness.
    {
        "name": "date/default-format",
        "filter": "date",
        "value": date(2026, 10, 8),
    },
    {
        "name": "date/format-name-is-resolved",
        "filter": "date",
        "value": date(2026, 10, 8),
        "arg": "DATE_FORMAT",
    },
    {
        # A `str` constant, so it arrives `mark_safe`d — and `mark_safe("")` is
        # still falsy, so `arg or "DATE_FORMAT"` selects the default. Same answer
        # as the row above, a different call.
        "name": "date/empty-string-argument",
        "filter": "date",
        "value": date(2026, 10, 8),
        "arg": "",
    },
    {
        # `0` is a `var_arg`, so the filter is handed the `int` 0 — also falsy,
        # so the default key again. The pair with `date:5` below is
        # `get_format`'s truthiness test rather than a presence test.
        "name": "date/zero-argument",
        "filter": "date",
        "value": date(2026, 10, 8),
        "arg": 0,
    },
    {
        # **`x|date:5` is not a mistake.** A bare `5` is a `var_arg`, so the
        # filter receives the `int` 5, and `get_format` stringifies it itself
        # (`formats.py:113`, `format_type = str(format_type)`): `'5'` is not a
        # format setting, so it comes back as itself and formats to `'5'`.
        "name": "date/numeric-argument",
        "filter": "date",
        "value": date(2026, 10, 8),
        "arg": 5,
    },
    {
        # The same literal path with a character that is not a format char.
        "name": "date/literal-text-argument",
        "filter": "date",
        "value": date(2026, 10, 8),
        "arg": "x",
    },
    {
        "name": "date/iso-format",
        "filter": "date",
        "value": date(2026, 10, 8),
        "arg": "Y-m-d",
    },
    {
        # `jS` is the day with its ordinal suffix — a body that formatted the
        # day alone answers `'8'`.
        "name": "date/day-suffix",
        "filter": "date",
        "value": date(2026, 10, 8),
        "arg": "jS",
    },
    {
        # The backslash escapes the format char, so the answer is the literal
        # `Y` (`re_escaped.sub(r"\1", piece)`).
        "name": "date/escaped-format-char",
        "filter": "date",
        "value": date(2026, 10, 8),
        "arg": "\\Y",
    },
    {
        "name": "date/datetime-value",
        "filter": "date",
        "value": datetime(2026, 10, 8, 14, 30, 45),
        "arg": "Y",
    },
    {
        # A `SafeString` has no `.year`, so **both** branches fail and the answer
        # is `''`. The *value*'s safety is not the *result*'s: `date` is
        # `is_safe=False` with no `@stringfilter`.
        "name": "date/safe-string-value",
        "filter": "date",
        "value": mark_safe("2026-10-08"),
        "arg": "Y-m-d",
    },
    # **The guard is `==` and not truthiness.** `0`, `False`, `[]`, `{}`, `()`
    # and `0.0` are all *falsy* and all pass `value in (None, "")`, so with a
    # non-empty argument they proceed and the literal text is the answer. A
    # truthiness guard answers `''` for every one of them.
    {
        "name": "date/falsy-zero-value-proceeds",
        "filter": "date",
        "value": 0,
        "arg": 5,
    },
    {
        "name": "date/falsy-false-value-proceeds",
        "filter": "date",
        "value": False,
        "arg": 5,
    },
    {
        "name": "date/falsy-empty-list-value-proceeds",
        "filter": "date",
        "value": [],
        "arg": 5,
    },
    {
        "name": "date/falsy-empty-dict-value-proceeds",
        "filter": "date",
        "value": {},
        "arg": 5,
    },
    {
        "name": "date/falsy-empty-tuple-value-proceeds",
        "filter": "date",
        "value": (),
        "arg": 5,
    },
    {
        "name": "date/falsy-float-value-proceeds",
        "filter": "date",
        "value": 0.0,
        "arg": 5,
    },
    # ... and the other half of the guard: the *two* sentinels answer `""`
    # without reaching either branch, while every other falsy value is refused
    # only later by the formatter — both engines agree, so the row is the guard's
    # sentinel list rather than the divergence above.
    {
        "name": "date/none-value",
        "filter": "date",
        "value": None,
    },
    {
        "name": "date/empty-string-value",
        "filter": "date",
        "value": "",
    },
    {
        "name": "date/safe-empty-string-value",
        "filter": "date",
        "value": mark_safe(""),
    },
    # Values that are neither sentinel nor date-like: branch 1 raises, branch 2
    # raises too (`"None"`'s `N` needs `.month`), and the answer is `''`.
    {
        "name": "date/string-value",
        "filter": "date",
        "value": "x",
    },
    {
        "name": "date/int-value",
        "filter": "date",
        "value": 5,
    },
    {
        "name": "date/list-value",
        "filter": "date",
        "value": [],
    },
    {
        "name": "date/dict-value",
        "filter": "date",
        "value": {},
    },
    # **Branch 2's sentinel text**, the only row that pins it; see
    # [`DATE_WITHOUT_DAY`].
    {
        "name": "date/branch-two-absent-argument",
        "filter": "date",
        "value": DATE_WITHOUT_DAY(),
    },
    {
        # The fixture lacks `O`/`R`/`M`/`D`/`A`/`T`/`E`, so this time branch 2
        # *also* raises and the answer is `''` — the negative control beside the
        # row above.
        "name": "date/branch-two-named-format-still-fails",
        "filter": "date",
        "value": DATE_WITHOUT_DAY(),
        "arg": "DATE_FORMAT",
    },
    {
        # Branch 1 *succeeds* here: `get_format("5")` is `"5"`, whose single
        # character is literal.
        "name": "date/branch-two-fixture-numeric-argument",
        "filter": "date",
        "value": DATE_WITHOUT_DAY(),
        "arg": 5,
    },
    # `LANGUAGE_CODE` selects the format module, so the resolved default is the
    # German one and neither answer is `settings.DATE_FORMAT`. Both rows carry
    # `expected`, because a `settings` row can otherwise move both engines
    # together on a misspelt name and stay green.
    {
        "name": "date/german-default-format",
        "filter": "date",
        "value": date(2026, 10, 8),
        "settings": {"LANGUAGE_CODE": "de"},
        "expected": "8. Oktober 2026",
    },
    {
        "name": "date/german-short-format-name",
        "filter": "date",
        "value": date(2026, 10, 8),
        "arg": "SHORT_DATE_FORMAT",
        "settings": {"LANGUAGE_CODE": "de"},
        "expected": "08.10.2026",
    },
    # --- time: the same skeleton, three differences --------------------------
    #
    # The default key is `TIME_FORMAT` (`'P'`), **both** branches catch
    # `(AttributeError, TypeError)`, and the fallback is `dateformat.time_format`.
    # The `date`-valued rows are the sharp pair: branch 1 raises the same
    # `TypeError` the `date` filter propagates, `time` catches it, branch 2
    # raises it again and the second catch answers `''`.
    {
        "name": "time/default-format",
        "filter": "time",
        "value": time(14, 30, 45),
    },
    {
        "name": "time/format-H-i",
        "filter": "time",
        "value": time(14, 30, 45),
        "arg": "H:i",
    },
    {
        "name": "time/format-H-i-s",
        "filter": "time",
        "value": time(14, 30, 45),
        "arg": "H:i:s",
    },
    {
        "name": "time/format-P-is-the-default",
        "filter": "time",
        "value": time(14, 30, 45),
        "arg": "P",
    },
    {
        "name": "time/datetime-value",
        "filter": "time",
        "value": datetime(2026, 10, 8, 14, 30, 45),
        "arg": "H:i",
    },
    {
        # **The sharp row.** A real `date` has no time-related attribute, so both
        # branches raise and both raises are caught: `''` where `x|date:"H"`
        # propagates.
        "name": "time/date-value-hours-only",
        "filter": "time",
        "value": date(2026, 10, 8),
        "arg": "H",
    },
    {
        "name": "time/date-value-H-i",
        "filter": "time",
        "value": date(2026, 10, 8),
        "arg": "H:i",
    },
    {
        "name": "time/date-value-default-format",
        "filter": "time",
        "value": date(2026, 10, 8),
    },
    # The same equality guard as `date`: a falsy non-sentinel proceeds and the
    # literal `'5'` is the answer.
    {
        "name": "time/falsy-zero-value-proceeds",
        "filter": "time",
        "value": 0,
        "arg": 5,
    },
    {
        "name": "time/none-value",
        "filter": "time",
        "value": None,
    },
    {
        "name": "time/empty-string-value",
        "filter": "time",
        "value": "",
    },
    {
        "name": "time/string-value",
        "filter": "time",
        "value": "x",
    },
    # --- timesince: truthiness guards, one seam call, a caught pair ----------
    #
    # `A = datetime(2026, 10, 8, 22, 30)` and `B = datetime(2026, 10, 8, 23, 51)`,
    # both naive, so the text is deterministic with no `now()` in it. The argument
    # is a `datetime`, which `template_literal` cannot spell as a template
    # constant, so every row carries it as `arg_variable`.
    {
        "name": "timesince/hour-and-minute",
        "filter": "timesince",
        "value": datetime(2026, 10, 8, 22, 30),
        "arg_variable": datetime(2026, 10, 8, 23, 51),
    },
    {
        # A value in the future is `'0\xa0minutes'` — the helper's "stop
        # processing" arm, not a negative interval.
        "name": "timesince/future-is-zero-minutes",
        "filter": "timesince",
        "value": datetime(2026, 10, 8, 23, 51),
        "arg_variable": datetime(2026, 10, 8, 22, 30),
    },
    {
        # A `date` against a `datetime` works: the helper coerces the `date` up.
        "name": "timesince/date-value-against-a-datetime",
        "filter": "timesince",
        "value": date(2026, 10, 8),
        "arg_variable": datetime(2026, 10, 8, 22, 30),
    },
    # **The caught `TypeError`, and it belongs in `CASES` with the empty answer.**
    # The helper refuses a mixed-naive/aware pair and `except (ValueError,
    # TypeError)` turns it into `''`. The `"x"` rows go the other way, into
    # `ERROR_CASES`: `AttributeError` is not caught.
    {
        "name": "timesince/aware-value-naive-argument",
        "filter": "timesince",
        "value": datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc),
        "arg_variable": datetime(2026, 10, 8, 22, 30),
    },
    {
        "name": "timesince/naive-value-aware-argument",
        "filter": "timesince",
        "value": datetime(2026, 10, 8, 22, 30),
        "arg_variable": datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc),
    },
    # `if not value` is truthiness, not `date`'s two sentinels: `None`, `0`, `""`
    # and `[]` all answer `''`.
    {
        "name": "timesince/none-value",
        "filter": "timesince",
        "value": None,
    },
    {
        "name": "timesince/zero-value",
        "filter": "timesince",
        "value": 0,
    },
    {
        "name": "timesince/empty-string-value",
        "filter": "timesince",
        "value": "",
    },
    {
        "name": "timesince/empty-list-value",
        "filter": "timesince",
        "value": [],
    },
    {
        # The one row safe without an explicit `o`: the helper calls
        # `datetime.now()` inside each engine's own call, so any nearer row is a
        # race at a unit boundary. `'0\xa0minutes'` holds for the next three
        # thousand days.
        "name": "timesince/far-future-is-zero-minutes",
        "filter": "timesince",
        "value": datetime.now() + timedelta(days=3650),
    },
    # --- timeuntil: one flag difference and one guard difference -------------
    #
    # `timeuntil(value, arg)` is `timesince(value, arg, reversed=True)`, and the
    # flag is a flag rather than an operand swap: Django normalises the
    # timezones *after* the swap. The first two rows below are the swap read out
    # loud — `timeuntil(A, o=B)` is `timesince(B, o=A)` — and no caller-side swap
    # reproduces them along with `timesince`'s own rows.
    {
        "name": "timeuntil/past-is-zero-minutes",
        "filter": "timeuntil",
        "value": datetime(2026, 10, 8, 22, 30),
        "arg_variable": datetime(2026, 10, 8, 23, 51),
    },
    {
        "name": "timeuntil/future-is-an-interval",
        "filter": "timeuntil",
        "value": datetime(2026, 10, 8, 23, 51),
        "arg_variable": datetime(2026, 10, 8, 22, 30),
    },
    {
        "name": "timeuntil/date-value-against-a-datetime",
        "filter": "timeuntil",
        "value": date(2026, 10, 8),
        "arg_variable": datetime(2026, 10, 8, 22, 30),
    },
    {
        "name": "timeuntil/aware-value-naive-argument",
        "filter": "timeuntil",
        "value": datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc),
        "arg_variable": datetime(2026, 10, 8, 22, 30),
    },
    {
        "name": "timeuntil/none-value",
        "filter": "timeuntil",
        "value": None,
    },
    {
        "name": "timeuntil/zero-value",
        "filter": "timeuntil",
        "value": 0,
    },
    {
        "name": "timeuntil/empty-string-value",
        "filter": "timeuntil",
        "value": "",
    },
    {
        "name": "timeuntil/empty-list-value",
        "filter": "timeuntil",
        "value": [],
    },
    # --- the `expects_localtime` rows -----------------------------------------
    #
    # `date` and `time` are the only two filters carrying the flag, and these are
    # the corpus's only evidence of it: `run_filter` applies `localtime(value,
    # use_tz)` before the body. Both sides read `use_tz` off the `Context` and the
    # zone off settings, so a row needs the `context` **and** `settings` keys —
    # and, because a `settings` row can move both engines together, an `expected`
    # literal. Measured under `override_settings(USE_TZ=True,
    # TIME_ZONE="Europe/Paris")` with the value `2026-10-08 22:30 UTC`. A body
    # that called `localtime` itself, or that ignored `use_tz`, moves one of
    # these.
    {
        "name": "date/localtime-use-tz-true-converts",
        "filter": "date",
        "value": datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc),
        "arg": "Y-m-d H:i",
        "context": {"use_tz": True},
        "settings": {"USE_TZ": True, "TIME_ZONE": "Europe/Paris"},
        "expected": "2026-10-09 00:30",
    },
    {
        # The pair to the row above: `False` on the context means "do not
        # convert", a different fact from the settings' `USE_TZ=True`.
        "name": "date/localtime-use-tz-false-does-not-convert",
        "filter": "date",
        "value": datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc),
        "arg": "Y-m-d H:i",
        "context": {"use_tz": False},
        "settings": {"USE_TZ": True, "TIME_ZONE": "Europe/Paris"},
        "expected": "2026-10-08 22:30",
    },
    {
        # The negative control: a naive value is not converted whatever the flag
        # says, so a body that converted unconditionally moves this row.
        "name": "date/localtime-naive-value-is-not-converted",
        "filter": "date",
        "value": datetime(2026, 10, 8, 22, 30),
        "arg": "Y-m-d H:i",
        "context": {"use_tz": True},
        "settings": {"USE_TZ": True, "TIME_ZONE": "Europe/Paris"},
        "expected": "2026-10-08 22:30",
    },
    {
        "name": "time/localtime-use-tz-true-converts",
        "filter": "time",
        "value": datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc),
        "arg": "H:i",
        "context": {"use_tz": True},
        "settings": {"USE_TZ": True, "TIME_ZONE": "Europe/Paris"},
        "expected": "00:30",
    },
    {
        # `None` on the context means "the settings' `USE_TZ`", a different fact
        # from `True`; no `context` key, so `Context()`'s own `use_tz=None`
        # stands and the settings' `True` is what converts.
        "name": "time/localtime-context-none-reads-the-setting",
        "filter": "time",
        "value": datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc),
        "arg": "H:i",
        "settings": {"USE_TZ": True, "TIME_ZONE": "Europe/Paris"},
        "expected": "00:30",
    },
    # =====================================================================
    # M12 Task 7: the nine `django.templatetags` library filters.
    #
    # These are the corpus's first rows whose filter comes from a **library**
    # rather than `django.template.defaultfilters`, and they carry a `libraries`
    # key for that reason alone: `Engine().template_libraries` is `{}`
    # (measured), so `parser.filters` has no `language_name` until something
    # `{% load %}`s `django.templatetags.i18n` — and a row without the key would
    # raise `Invalid filter` on Django's side while ours answered, which is a
    # loud failure rather than a silent one and therefore needs no `expected` to
    # keep it honest. The key is the faithful equivalent of the `{% load i18n %}`
    # a template would write; see `scripts/filter_probe.py`'s `load_libraries`.
    #
    # The nine are `ours/theirs` in one respect and `theirs` in another: the
    # **bodies** are Rust (`crates/template-core/src/filters/{language_*,
    # localize,unlocalize,localtime,utc,timezone}.rs`), but the three `tz` names
    # reach the transcribed Python `do_timezone` under the seam, because M12
    # Task 4 (`f89a9d4`) decided the tz algorithm stays Python's. That asymmetry
    # is not visible to a parity row — both sides call the same function — which
    # is exactly why the *bodies* carry it in prose and this comment repeats it.
    # --- i18n: the four `language_*` filters (`i18n.py:292-310`) --------------
    {
        # The `["name"]` of `get_language_info`'s dict — the English name.
        "name": "language_name/hungarian",
        "filter": "language_name",
        "value": "hu",
        "libraries": ["django.templatetags.i18n"],
    },
    {
        "name": "language_name/german",
        "filter": "language_name",
        "value": "de",
        "libraries": ["django.templatetags.i18n"],
    },
    {
        # `["name_local"]` — "Magyar", not "Hungarian". A body that answered the
        # English name here would be green on the row above and red on this one.
        "name": "language_name_local/hungarian",
        "filter": "language_name_local",
        "value": "hu",
        "libraries": ["django.templatetags.i18n"],
    },
    {
        "name": "language_bidi/ltr-hungarian",
        "filter": "language_bidi",
        "value": "hu",
        "libraries": ["django.templatetags.i18n"],
    },
    {
        # The `True` half. A body that hardcoded `False` would pass the row above.
        "name": "language_bidi/rtl-arabic",
        "filter": "language_bidi",
        "value": "ar",
        "libraries": ["django.templatetags.i18n"],
    },
    {
        # `gettext` of the **English** name (`i18n.py:298-300`), under the
        # default `en-us` locale where `gettext` is the identity — so this row
        # alone cannot tell a body that asked the host from one that returned
        # `info.name`. Its twin below is what does.
        "name": "language_name_translated/english-locale-is-the-english-name",
        "filter": "language_name_translated",
        "value": "hu",
        "libraries": ["django.templatetags.i18n"],
    },
    {
        # **The row that pins the `gettext` call.** Under `LANGUAGE_CODE="de"`
        # the English name "Hungarian" translates to "Ungarisch", so a body that
        # returned `info.name` (or skipped the host's `gettext`) answers
        # "Hungarian" and fails here while passing the row above. Measured on the
        # pinned tree.
        "name": "language_name_translated/german-locale-translates-the-name",
        "filter": "language_name_translated",
        "value": "hu",
        "libraries": ["django.templatetags.i18n"],
        "settings": {"LANGUAGE_CODE": "de"},
        "expected": "Ungarisch",
    },
    # --- l10n: `localize` / `unlocalize` (`l10n.py:7-21`) ---------------------
    #
    # Both bodies are `str(formats.localize(value, use_l10n=<bool>))`, and the
    # `str()` is the whole reason they are host operations rather than a shared
    # helper: `localize` on a **lazy** or **safe** value passes the proxy through
    # the formatter and then stringifies it, so the answer's type is the
    # formatter's to decide and not Rust's. The safe-string row below is that
    # seam.
    {
        "name": "localize/float-default-locale",
        "filter": "localize",
        "value": 1234.5,
        "libraries": ["django.templatetags.l10n"],
    },
    {
        "name": "localize/int-default-locale",
        "filter": "localize",
        "value": 1234567,
        "libraries": ["django.templatetags.l10n"],
    },
    {
        "name": "localize/bool-value",
        "filter": "localize",
        "value": True,
        "libraries": ["django.templatetags.l10n"],
    },
    {
        # A `SafeString` in, and a `SafeString` out — measured, both sides. The
        # body cannot produce one by stringifying in Rust, so this row is what
        # pins the `str()` + re-classify fusion in `PyFilterHost::localize`.
        "name": "localize/safe-string-value",
        "filter": "localize",
        "value": mark_safe("123"),
        "libraries": ["django.templatetags.l10n"],
    },
    {
        # The **German** locale is what makes `localize` observable: `1234.5`
        # localizes to `1234,5`, so a body that ignored `use_l10n=True` (or the
        # `LANGUAGE_CODE`) answers `1234.5` and fails here.
        "name": "localize/german-locale-uses-the-decimal-comma",
        "filter": "localize",
        "value": 1234.5,
        "libraries": ["django.templatetags.l10n"],
        "settings": {"LANGUAGE_CODE": "de"},
        "expected": "1234,5",
    },
    {
        # The mirror: `unlocalize` forces the **non**-localized form, so under
        # the same German locale it keeps the point. A body that wired the two
        # names to the same call would pass one of this pair and fail the other.
        "name": "unlocalize/german-locale-keeps-the-point",
        "filter": "unlocalize",
        "value": 1234.5,
        "libraries": ["django.templatetags.l10n"],
        "settings": {"LANGUAGE_CODE": "de"},
        "expected": "1234.5",
    },
    {
        "name": "unlocalize/string-value",
        "filter": "unlocalize",
        "value": "1234.5",
        "libraries": ["django.templatetags.l10n"],
    },
    # --- tz: `localtime` / `utc` / `timezone` (`tz.py:19-38`) -----------------
    #
    # **Two groups, and they exist for different reasons.** The rows below pin
    # the three names' *good* inputs across the zone boundary; the group after
    # them is ported from Django's own suite (see its comment).
    {
        # A naive datetime is made aware in the **default** time zone, then
        # converted to the *current* one — which here is the same zone, so the
        # wall clock is unchanged and only the `tzinfo` appears. `expected` pins
        # the wall clock, which is what a wrong `TIME_ZONE` moves.
        "name": "localtime/naive-value-in-the-current-zone",
        "filter": "localtime",
        "value": datetime(2026, 10, 8, 22, 30),
        "libraries": ["django.templatetags.tz"],
        "settings": {"USE_TZ": True, "TIME_ZONE": "Europe/Paris"},
        "expected": datetime(2026, 10, 8, 22, 30, tzinfo=ZoneInfo("Europe/Paris")),
    },
    {
        # An aware UTC value **converted**: 22:30Z is 00:30 the next day in
        # Paris (CEST, +02:00). A body that returned its input unchanged answers
        # 22:30 and fails both the comparison and the literal.
        "name": "localtime/aware-utc-value-converts",
        "filter": "localtime",
        "value": datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc),
        "libraries": ["django.templatetags.tz"],
        "settings": {"USE_TZ": True, "TIME_ZONE": "Europe/Paris"},
        "expected": datetime(2026, 10, 9, 0, 30, tzinfo=ZoneInfo("Europe/Paris")),
    },
    {
        # `utc`'s zone is fixed (`datetime.UTC`, `tz.py:34`) and does **not**
        # read `TIME_ZONE`; the setting is here only so the naive twin below has
        # a zone to assume.
        "name": "utc/aware-paris-value-converts-to-utc",
        "filter": "utc",
        "value": datetime(2026, 10, 8, 22, 30, tzinfo=ZoneInfo("Europe/Paris")),
        "libraries": ["django.templatetags.tz"],
        "settings": {"USE_TZ": True, "TIME_ZONE": "Europe/Paris"},
        "expected": datetime(2026, 10, 8, 20, 30, tzinfo=timezone.utc),
    },
    {
        # A **naive** value, assumed to be in the current zone and converted to
        # UTC. The same input as `localtime/naive-...` above with the opposite
        # destination, so the pair proves the two bodies reach *different* fixed
        # zones rather than one shared conversion.
        "name": "utc/naive-value-assumed-local-converts-to-utc",
        "filter": "utc",
        "value": datetime(2026, 10, 8, 22, 30),
        "libraries": ["django.templatetags.tz"],
        "settings": {"USE_TZ": True, "TIME_ZONE": "Europe/Paris"},
        "expected": datetime(2026, 10, 8, 20, 30, tzinfo=timezone.utc),
    },
    {
        # The one name whose zone comes from the **template** (`tz.py:37-38` is
        # `do_timezone` registered under `timezone`). The zone name arrives as a
        # quoted constant, so Django's loop hands it over `mark_safe`d — a
        # `SafeString`, which is a `str` and so reaches the body's `isinstance`
        # branch unchanged.
        "name": "timezone/named-zone-paris",
        "filter": "timezone",
        "value": datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc),
        "arg": "Europe/Paris",
        "libraries": ["django.templatetags.tz"],
        "settings": {"USE_TZ": True},
    },
    {
        # The `"UTC"` zone *name* (a `str`), distinct from `utc`'s `datetime.UTC`
        # *object* — the same answer through a different branch of `do_timezone`.
        "name": "timezone/utc-zone-name",
        "filter": "timezone",
        "value": datetime(2026, 10, 8, 22, 30, tzinfo=ZoneInfo("Europe/Paris")),
        "arg": "UTC",
        "libraries": ["django.templatetags.tz"],
        "settings": {"USE_TZ": True},
    },
    # **The ported half, and why a port was needed at all.** Django's own
    # assertions for these three filters on *bad* inputs live in
    # `.django/tests/timezones/tests.py:1051-1071`
    # (`test_localtime_filters_do_not_raise_exceptions`, `def` at :1051,
    # `end_lineno` :1071), under `USE_TZ=True`, and they are **two templates
    # and not one** — a distinction that reads as one shape until the arguments
    # are counted:
    #
    #     :1055  {% load tz %}{{ dt }}|{{ dt|localtime }}|{{ dt|utc }}|{{ dt|timezone:tz }}
    #     :1060    dt=None          -> "None|||"
    #     :1062    dt="not a date"  -> "not a date|||"
    #     :1065  {% load tz %}{{ dt|timezone:tz }}          <- one filter, not four
    #     :1066    dt=..., tz=None  -> ""
    #     :1068    dt=..., tz="not a tz" -> ""   (assertion at :1071)
    #
    # So the four-pipe form carries the first two cases and the single-filter
    # form the last two. That test is **not** in the
    # acceptance suite — `scripts/run_django_suite.sh:25` is
    # `LABELS=(template_tests)` and `scripts/check.sh` passes `template_tests`
    # explicitly, so nothing in `scripts/` or `tests/` ever runs the `timezones`
    # label. It is therefore not a guard we can lean on, and these four
    # assertions are **ported here** rather than assumed: same values, same
    # `USE_TZ=True`, same expected `""`s. The precedent is
    # `tests/test_loader_case_sensitivity.py`, which exists because §2's upstream
    # row is skipped on every platform we run; this one is the same remedy for a
    # different reason — it is *unguarded* and simply outside the labels we pass,
    # so a later reader looking for the `skipUnless` that would explain its
    # absence will not find one.
    #
    # The template's bare `{{ dt }}` is not a filter and has no row; the four
    # `""`s the *filters* produce are what is ported. `ICT` there is
    # `timezone.get_fixed_timezone(420)`, a fixed +07:00 `tzinfo`; here it is
    # `timezone(timedelta(hours=7))`, the same object by value, passed as a
    # **`tzinfo`** so the row keeps upstream's shape (`tz` is a context variable
    # there, so it is an `arg_variable` here).
    {
        "name": "localtime/none-value-answers-the-empty-string",
        "filter": "localtime",
        "value": None,
        "libraries": ["django.templatetags.tz"],
        "settings": {"USE_TZ": True},
        "expected": "",
    },
    {
        "name": "utc/none-value-answers-the-empty-string",
        "filter": "utc",
        "value": None,
        "libraries": ["django.templatetags.tz"],
        "settings": {"USE_TZ": True},
        "expected": "",
    },
    {
        # `{{ dt|timezone:tz }}` with `dt=None` — the `not isinstance(value,
        # datetime)` guard (`tz.py:46-47`) answers before the argument is read.
        "name": "timezone/none-value-with-a-zone-answers-the-empty-string",
        "filter": "timezone",
        "value": None,
        "arg_variable": timezone(timedelta(hours=7)),
        "libraries": ["django.templatetags.tz"],
        "settings": {"USE_TZ": True},
        "expected": "",
    },
    {
        # The same guard on a `str`: `"not a date"` is not a `datetime`, so the
        # body answers `""` **without raising** — this is the "filters must never
        # raise exceptions" the upstream docstring names.
        "name": "timezone/non-datetime-value-answers-the-empty-string",
        "filter": "timezone",
        "value": "not a date",
        "arg_variable": timezone(timedelta(hours=7)),
        "libraries": ["django.templatetags.tz"],
        "settings": {"USE_TZ": True},
        "expected": "",
    },
    {
        # The **argument** is `None` (a present argument, `do_timezone(d, None)`),
        # not an absent one: it is neither a `tzinfo` nor a `str`, so it falls to
        # the final `else` of the `if isinstance(arg, tzinfo):` chain
        # (`tz.py:59-67`) and answers `""`. Distinct from `{{ d|timezone }}`,
        # which `args_check`
        # refuses at parse time — see `timezone.rs`'s doc.
        "name": "timezone/none-argument-answers-the-empty-string",
        "filter": "timezone",
        "value": datetime(2011, 9, 1, 13, 20, 30),
        "arg": None,
        "libraries": ["django.templatetags.tz"],
        "settings": {"USE_TZ": True},
        "expected": "",
    },
    {
        # A `str` argument that is not a zone name: `zoneinfo.ZoneInfo("not a
        # tz")` raises `ZoneInfoNotFoundError`, which the
        # `except zoneinfo.ZoneInfoNotFoundError:` arm (`tz.py:64-65`) catches
        # into `""`. The **only** branch where the body swallows a *string* the
        # template author wrote, and so the one a `timezone` row can reach that
        # the two rows above cannot.
        "name": "timezone/unknown-zone-name-answers-the-empty-string",
        "filter": "timezone",
        "value": datetime(2011, 9, 1, 13, 20, 30),
        "arg": "not a tz",
        "libraries": ["django.templatetags.tz"],
        "settings": {"USE_TZ": True},
        "expected": "",
    },
]


# The inputs whose answer is an **exception**, kept apart from [`CASES`] because a
# `CASES` row cannot carry one: `resolve` propagates it, so the comparison every
# `CASES` row makes is replaced by an abort (see the module doc). Nothing about
# the input differs — same `FilterExpression`, same `_native.filter`, same
# argument resolution, same default `Context` — only the assertion does: both
# engines must raise, with the same class **and the same message**.
#
# **The message is pinned as a literal, and pinning it is the point.** A class
# compared alone would be satisfied by two engines agreeing on the wrong
# question: hand `cut` a `str` where the row meant an `int` and neither raises
# the `TypeError` this row is about — they would agree on a different answer, or
# on no exception at all. The literal is the measurement.
#
# It is also a *strong* claim rather than a tautology, because the two sides get
# their text from different places on **both** of the shapes this list holds.
# `cut`'s is synthesised in Rust (`cut.rs`'s `not_a_string`, since no host
# operation performs the replacement) and Django's is CPython's `str.replace`, so
# this list is the only place the synthesis is pinned to the wording a template
# sees. `pluralize`'s and `yesno`'s are CPython's **on both sides** — the class
# and the text both cross the seam out of the real `in`/`+`/`str.split` — so
# what those rows claim is that the seam preserved them, which is a claim about
# `comma_bits` rather than about a string literal. The two shapes sit in one list
# because the assertion here is the same one, and they part company in the
# *other* home the module doc's rule assigns: `cut`'s rows need no body test, and
# the wave-2 rows do — `pluralize.rs`'s and `yesno.rs`'s
# `a_comma_bits_refusal_escapes`, which assert the class, assert that it escapes,
# and deliberately never touch the wording.
#
# Keys are `CASES`' keys with the same meanings: an **absent** `arg` means the
# filter was written without one, `"arg": None` means the explicit `None` token
# (`{{ x|cut:None }}`), and `arg_variable` holds an argument of a shape a
# template cannot spell as a constant — the bare name the token carries resolves
# through the context, as `test_filter_parity.py`'s `arg_token` explains.
# `value_factory` is available for the one reason it is above — a one-shot
# input — and `raises`/`message` are the row's assertion.
#
# **A row here is not a replacement for a body test.** They check different
# things and the split is by who authors the message: a message passed through
# from CPython is checked here for its *wording* and in the body for
# *propagation* (the class alone), where a message synthesised in Rust is checked
# only here — the module doc's rule. Deleting a propagation test because a row
# covers the same input would trade a check an agent can run for one it cannot.
#
# **The five `needs_autoescape` filters whose own second parameter is named
# `autoescape` can never be given an argument, and the five rows at the foot of
# this list are that refusal.** They are `linebreaks`, `linebreaksbr`,
# `linenumbers`, `unordered_list` and `urlize`. Django's loop calls
# `func(obj, autoescape=..., *arg_vals)` (`base.py:813`), so the positional
# argument binds to `autoescape` as well and **every** such token raises
# `TypeError: <py_func>() got multiple values for argument 'autoescape'`
# (measured over an `int`, `None`, `str`, `float`, `bool` and a resolved
# variable, one message per filter; `<py_func>` is `linebreaks_filter` for
# `linebreaks`, and the filter name for the other four). `args_check`
# (`base.py:822`) counts `autoescape` among the filter's own parameters, so the
# parse **accepts** the token and the refusal is at call time, which is why the
# class is a `TypeError` and not a `TemplateSyntaxError`.
#
# **These five were absent by decision until the M4 review, and the decision was
# wrong in a form worth keeping.** The argument for excluding them was that
# `_native.filter` takes `autoescape` as a *value*, so it has no keyword to
# collide with, and that the message needs the *callable's* Python name, which a
# name-keyed seam does not have; the collision was therefore CPython's binding of
# the *call* and not a body's decision, so it belonged to whoever makes the call
# — M9, when the shim exposes these filters as Python functions carrying Django's
# own signatures. Both premises are true and the conclusion did not follow: they
# are true *of the seam* and false *of the body*, and a body is what stands where
# the refusal happens. The argument was rejected by pointing at a row this list
# already holds — `cut/non-string-argument` above, whose message is
# Rust-synthesised (`cut.rs`'s `not_a_string`) because no host operation produces
# it. "The seam cannot produce this text" is exactly that row's situation, so the
# argument proves too much. The unknown callable name is a cost instead: it is a
# per-module constant in each refusing filter, and §10.4 records the consequence
# — a Django release could rename `linebreaks_filter` without changing any
# behaviour, and a message-bearing row is what fails when it does.
#
# Each of the five carries a plain `arg` and no `arg_variable`, deliberately and
# not by omission: the refusal never consults the argument, so a second,
# variable-shaped row for the same filter would pin this module's token plumbing
# rather than a body. The `autoescape` branches these filters *do* have are pinned
# in `CASES` by the `context` key, which needs no argument.
#
# **`urlize` was the fifth row's promise, and it is kept above.** Until Task 9
# wave B this paragraph said the row "is not here yet … [it] lands with the body,
# under Task 9" — a stage rather than a second decision, because a row whose body
# is a stub reddens the gate without describing anything. The body has landed, so
# the row is in the quintet above and there is nothing staged here any more.
ERROR_CASES: list[dict] = [
    # --- cut: `str.replace`'s argument refusal, which no host performs -------
    {
        # `{{ x|cut:1 }}` — a bare token, so the argument arrives as the `int` 1
        # rather than as the string `"1"`, and `str.replace` refuses it. The
        # message names the *type* (`not int`), which is what makes this row a
        # statement about the argument's type rather than about its text.
        "name": "cut/non-string-argument",
        "filter": "cut",
        "value": "a1b",
        "arg": 1,
        "raises": "TypeError",
        "message": "replace() argument 1 must be str, not int",
    },
    {
        # `{{ x|cut:None }}` — reachable where `{{ x|cut }}` is not: the arity
        # check (`base.py:819`) turns a *missing* argument into a
        # `TemplateSyntaxError` before any body runs, so this token is the only
        # way a body sees `None` here.
        #
        # **The FFI used to hand the body `Option::None` for this, the same value
        # it used for a missing argument, and this comment used to say that was
        # why the reachable case was worth pinning.** It was the mis-generalization
        # the shape invites: `cut`'s body raises the same `TypeError` either way,
        # so the row passed and the conflation was invisible *here*. On
        # `floatformat` it was not — `int(None)` raises where the bare token
        # answers — and that is the row that forced the split. The FFI now takes
        # `arg_vals` as a tuple of length 0 or 1, so this row and
        # `floatformat/none-argument` pin the two halves of one distinction.
        #
        # The message says **`None`**, not `NoneType`: `Value::None.type_name()`
        # would put the wrong word in it, and nothing else in M4 would notice.
        "name": "cut/none-argument",
        "filter": "cut",
        "value": "abc",
        "arg": None,
        "raises": "TypeError",
        "message": "replace() argument 1 must be str, not None",
    },
    # --- the wave-2 half: `comma_bits`'s five inputs, and the two classes the
    #     same "not a string" produces -----------------------------------------
    #
    # All five are the *same three lines* of `defaultfilters.py` — `if
    # prepend_comma and "," not in arg: arg = "," + arg` then `arg.split(",")` —
    # reached through two filters whose argument handling differs by one flag.
    # Five rows for one expression is not redundancy: the argument's **type**
    # decides which operation raises and with which class, and the `[1]`/`[","]`
    # pair is what proves nothing shorter can stand in for it. `[","]` is the
    # only argument that gets **past** the membership test — a list can answer
    # `"," not in [","]`, it is `False` — and dies one step later at the
    # `.split`. So the same "argument is not a string" produces a `TypeError`
    # through one list and an `AttributeError` through another, and a Rust side
    # holding `contains`, `concat` and `split` as separate primitives could not
    # tell them apart: which one raises is a fact about the operand's class that
    # only the Python side can see. Hence one method.
    {
        # `{{ x|yesno:5 }}`. `yesno` passes `prepend_comma = false`, so there is
        # no `in` to fail first and the `int` reaches `.split` directly — an
        # `AttributeError`. Its `pluralize` twin below is the *other* class for
        # the same input, and the two together are the measurement that forces
        # the flag rather than a comment explaining it.
        "name": "yesno/non-string-argument",
        "filter": "yesno",
        "value": True,
        "arg": 5,
        "raises": "AttributeError",
        "message": "'int' object has no attribute 'split'",
    },
    {
        # `{{ x|pluralize:5 }}`, and the mirror of the row above: the `in` runs
        # first here and refuses the `int` itself.
        "name": "pluralize/non-string-argument",
        "filter": "pluralize",
        "value": 1,
        "arg": 5,
        "raises": "TypeError",
        "message": "argument of type 'int' is not a container or iterable",
    },
    {
        # `"," not in [1]` is `True`, so the prepend runs and `"," + [1]` is the
        # concatenation failure. Reachable only as a **variable** argument:
        # a template has no list literal, and `template_literal` cannot render
        # one as a constant.
        "name": "pluralize/list-argument-fails-the-concatenation",
        "filter": "pluralize",
        "value": 1,
        "arg_variable": [1],
        "raises": "TypeError",
        "message": 'can only concatenate str (not "list") to str',
    },
    {
        # The pair's other half, and the reason the whole row set exists:
        # `"," not in [","]` is `False`, so **no** concatenation happens and the
        # list survives to `.split` — a different class from the row above, off
        # the same "a list is not a string" premise. A seam that fused
        # `contains`/`concat`/`split` into one Rust-side sequence would have to
        # choose one of these two messages for both.
        "name": "pluralize/list-of-separators-fails-the-split",
        "filter": "pluralize",
        "value": 1,
        "arg_variable": [","],
        "raises": "AttributeError",
        "message": "'list' object has no attribute 'split'",
    },
    {
        # A third distinct class for a third distinct operand — and the one that
        # says the message is CPython's rather than a Rust template: it names
        # `object`, which is the argument's own type, and only the Python side
        # knows it.
        "name": "pluralize/object-argument",
        "filter": "pluralize",
        "value": 1,
        "arg_variable": object(),
        "raises": "TypeError",
        "message": "argument of type 'object' is not a container or iterable",
    },
    # --- wave 3: the third filter in the list whose message Rust authors ------
    {
        # `{{ x|floatformat:"" }}` — `arg[-1]` at `defaultfilters.py:141`,
        # before the parse and before any host call. It is an `IndexError`
        # rather than a syntax error
        # because the arity check counts arguments and this one is present.
        #
        # **This message is synthesised in Rust**, like `cut`'s: no host
        # operation performs `arg[-1]`, so `floatformat.rs` raises
        # `index_out_of_range` with the wording CPython would have used. By the
        # module doc's rule that makes this list the *only* check on the text —
        # the body test beside it asserts the class and that the seam was never
        # reached, and deliberately does not restate the message.
        "name": "floatformat/empty-string-argument",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": "",
        "raises": "IndexError",
        "message": "string index out of range",
    },
    {
        # `{{ x|floatformat:None }}` — `int(arg)` at `defaultfilters.py:161`,
        # where the bare `{{ x|floatformat }}` never gets: its default is
        # `arg=-1`, so the token
        # without an argument is not this call. **This row is the one that forced
        # the FFI's argument list to be a list.** Until it was, `_native.filter`
        # took an `Option` and a Python `None` argument became "no argument", so
        # this row answered `'6666.7'` where Django raises — an accident a user
        # reaches by writing `{{ price|floatformat:places }}` with `places = None`.
        #
        # The message names `NoneType` and not `None`, which is the opposite of
        # `cut/none-argument` above and the reason both rows exist: `str.replace`
        # names the object it was handed, `int()` names its type. Both come from
        # CPython, so both are propagation claims by the module doc's rule — and
        # `floatformat.rs`'s `a_parse_refusal_escapes` is that propagation test,
        # for exactly this seam. **Nothing a body test can add here would be more
        # than a restatement**: `to_int` is called on both branches, only with a
        # different `Value`, and `TestFilterHost` records the operation and not
        # its argument. The distinction this row carries is the FFI's, and this is
        # the only instrument on it.
        "name": "floatformat/none-argument",
        "filter": "floatformat",
        "value": 6666.6666,
        "arg": None,
        "raises": "TypeError",
        "message": (
            "int() argument must be a string, a bytes-like object or a real "
            "number, not 'NoneType'"
        ),
    },
    {
        # `int(inf)` raises `OverflowError`, which is **not** in the `except`
        # tuple (`TypeError`, `ValueError`, `UnicodeDecodeError`), so it escapes
        # the filter — where `nan` in the corpus above is caught by the same
        # `int()` call. The class and not the number is what the pair separates,
        # and the message is CPython's on both sides.
        "name": "filesizeformat/infinity-is-not-caught",
        "filter": "filesizeformat",
        "value": float("inf"),
        "raises": "OverflowError",
        "message": "cannot convert float infinity to integer",
    },
    {
        # The division's own message, from **outside** the `try` — and the reason
        # the seam divides instead of converting: `float(10**400)` raises
        # `OverflowError: int too large to convert to float`, a *different text*
        # for the same class, so a body reaching for the float seam would give
        # the right class and the wrong message. `10**309` in the corpus above is
        # the row on the other side of this line, where the division still
        # answers.
        "name": "filesizeformat/beyond-float-range-in-the-division",
        "filter": "filesizeformat",
        "value": 10**400,
        "raises": "OverflowError",
        "message": "integer division result too large for a float",
    },
    # --- the exception **object**, which is not its class name ---------------
    {
        # `{{ x|upper }}` where `str(x)` itself raises. The coercion is
        # `@stringfilter`'s `str(value)` (`defaultfilters.py:38-52`), so Django's
        # answer to this row is that exception, unchanged — and the class is a
        # test module's own, which is the whole point of the row: **a class
        # `builtins` does not hold**.
        #
        # It was measured failing before the FFI kept the exception object: the
        # host converted the `PyErr` to a core `Error` carrying the class *name*
        # and the message, and the name was all that came back, so this row
        # answered `builtins.Exception: BoomError: loud` where Django raises
        # `BoomError`. `SuspiciousOperation` — what `strip_tags` raises past its
        # depth limit, and what wave B's `striptags` row will assert — is the
        # same shape out of a library class rather than a test's.
        #
        # The message is `"loud"`, so a name-only reconstruction is caught by the
        # **class** and not by the text: it happens to prefix the name onto the
        # message, which is why a row comparing only text would have passed.
        "name": "upper/raising-str",
        "filter": "upper",
        "value_factory": LOUD_STR,
        "raises": "BoomError",
        "message": "loud",
    },
    # --- `base.py:813`'s collision: a `needs_autoescape` filter whose own second
    #     parameter is called `autoescape` cannot take an argument at all --------
    #
    # One row per filter and one message per filter, because the text names the
    # *callable* and not the argument — measured over an `int`, `None`, `str`,
    # `float`, `bool` and a resolved variable, all six giving the same string. So
    # the `arg` below is arbitrary and `5` is chosen only to match the code block
    # in `crates/template-py/src/lib.rs`; a row with a different argument would
    # assert the same bytes. The full statement, including why these were absent
    # until the M4 review, is the `ERROR_CASES` preamble above.
    {
        # The callable's name is `linebreaks_filter`, not the registered
        # `linebreaks`, so this row is also the one place the module's own name
        # for a filter is pinned — a pin bump could rename the `def` without
        # changing behaviour, and this row would fail (§10.4).
        "name": "linebreaks/argument",
        "filter": "linebreaks",
        "value": "x",
        "arg": 5,
        "raises": "TypeError",
        "message": "linebreaks_filter() got multiple values for argument 'autoescape'",
    },
    {
        # The other four name the registered name, because for them the two
        # coincide — which is what makes this quintet a check on the pairing
        # rather than five copies of one string.
        "name": "linebreaksbr/argument",
        "filter": "linebreaksbr",
        "value": "x",
        "arg": 5,
        "raises": "TypeError",
        "message": "linebreaksbr() got multiple values for argument 'autoescape'",
    },
    {
        "name": "linenumbers/argument",
        "filter": "linenumbers",
        "value": "x",
        "arg": 5,
        "raises": "TypeError",
        "message": "linenumbers() got multiple values for argument 'autoescape'",
    },
    {
        # `unordered_list` was landed under Task 6 and answered an argument until
        # this row's body change, which is why it is the one of the five whose
        # refusal is not part of a filter's first landing.
        "name": "unordered_list/argument",
        "filter": "unordered_list",
        "value": "x",
        "arg": 5,
        "raises": "TypeError",
        "message": "unordered_list() got multiple values for argument 'autoescape'",
    },
    {
        # **The fifth, landed with its body under Task 9 wave B.** Its registered
        # name and its `def` name coincide, so it names `urlize` where
        # `linebreaks` names `linebreaks_filter`. The preamble above carries the
        # statement; this row is the measurement.
        "name": "urlize/argument",
        "filter": "urlize",
        "value": "x",
        "arg": 5,
        "raises": "TypeError",
        "message": "urlize() got multiple values for argument 'autoescape'",
    },
    # --- striptags (`strip_tags`, `html.py:210-229`) -------------------------
    {
        # **The one row in this list whose exception class is not a builtin.**
        # `strip_tags` counts iterations and raises bare — `raise
        # SuspiciousOperation` at `html.py:222`, with no argument, which is why
        # the message below is the empty string and not a sentence. The class is
        # `django.core.exceptions`', not Rust's to synthesise, and the row works
        # because the seam keeps the exception *object* across the FFI
        # (`PyFilterHost::take_raised`), so it arrives as itself and
        # `assert_raises_same` compares the name it really has.
        #
        # The value is `<` **51** times then `a>` 51 times, measured: 50 answers
        # `''` and 51 raises. The check is at the *top* of the loop, so with 50
        # the final strip empties the value and `while "<" in value` exits first;
        # with 51 one pair is left and the counter is already 50. The balanced
        # 100-deep row above is the contrast: balanced tags go in one iteration.
        #
        # (`html.py:214`'s other guard, `long_open_tag_without_closing_re =
        # r"<[a-zA-Z][^>]{1000,}"`, is not reachable from here — it wants a
        # single 1000-character tag, and this value has none.)
        "name": "striptags/depth-limit-raises-suspiciousoperation",
        "filter": "striptags",
        "value": "<" * 51 + "a>" * 51,
        "raises": "SuspiciousOperation",
        "message": "",
    },
    {
        # **The *other* guard**, and the reason both rows exist: `strip_tags` has
        # two, they are not the same check, and each is reachable only from its
        # own shape. This one is caught before the loop starts, by
        # `long_open_tag_without_closing_re = r"<[a-zA-Z][^>]{1000,}"`
        # (`html.py:47`, `html.py:214`): one `<` followed by a letter and then 1000+
        # characters with no `>` in them — here `<a` × 501, which is 1002
        # characters and 501 `<`. A body that ported only the iteration counter
        # answers `''` on this row.
        #
        # Taken from `.django/tests/utils_tests/test_html.py:204-217`, the same
        # place the row above comes from.
        "name": "striptags/long-open-tag-raises-suspiciousoperation",
        "filter": "striptags",
        "value": ">" + "<a" * 501,
        "raises": "SuspiciousOperation",
        "message": "",
    },
    {
        # The same guard, at the boundary shape Django's own test uses: 50 `<a`
        # pairs followed by 950 bare `a`s — so the *count* of `<` is exactly 50
        # and the regex's `[^>]{1000,}` still holds, since the 950 `a`s extend
        # the run past 1000 characters. The row separates "50 or more `<`" from
        # "50 or more `<` **in one unclosed run**", which is what the guard
        # actually tests.
        "name": "striptags/long-open-tag-at-fifty-raises",
        "filter": "striptags",
        "value": "<a" * 50 + "a" * 950,
        "raises": "SuspiciousOperation",
        "message": "",
    },
    # --- urlencode (`quote`, `urllib/parse.py`) ------------------------------
    #
    # Everything here is Python's own refusal, passed through unchanged, and the
    # four rows below are the ones that *do* refuse. The shapes that **answer**
    # (`safe=[1]`, `safe=[]`, `safe=b"/"`) are `CASES` rows above — an earlier
    # revision listed them here as divergences, which was backwards: this body
    # has no `try`, so a refusal reaching a template author is Python's, and a
    # shape Django renders must render.
    {
        # Not iterable, so `quote_from_bytes` never reaches the comprehension:
        # `'int' object is not iterable`, the same refusal `str(int)` would give.
        "name": "urlencode/non-iterable-int-argument",
        "filter": "urlencode",
        "value": "a b/c",
        "arg": 5,
        "raises": "TypeError",
        "message": "'int' object is not iterable",
    },
    {
        # `bool` is an `int` subclass and is *also* not iterable — the row that
        # separates "not a `str`" from "not iterable", since a body branching on
        # `isinstance(safe, str)` and then raising would have to pick one of these
        # two messages for both.
        "name": "urlencode/non-iterable-bool-argument",
        "filter": "urlencode",
        "value": "a b/c",
        "arg": True,
        "raises": "TypeError",
        "message": "'bool' object is not iterable",
    },
    {
        "name": "urlencode/non-iterable-float-argument",
        "filter": "urlencode",
        "value": "a b/c",
        "arg": 5.0,
        "raises": "TypeError",
        "message": "'float' object is not iterable",
    },
    {
        # **Iterable, and still refusing** — but inside the comprehension, not
        # at iteration: `c < 128` compares a `str` to an `int`, so the message
        # names the comparison rather than the iteration. This is the row that
        # makes a `Vec<i64>`-shaped seam wrong: `quote`'s `safe` is not a
        # sequence of small integers, it is whatever the author wrote, and
        # `["/"]` gets one comparison in before it fails.
        "name": "urlencode/iterable-of-str-argument",
        "filter": "urlencode",
        "value": "a b/c",
        "arg_variable": ["/"],
        "raises": "TypeError",
        "message": "'<' not supported between instances of 'str' and 'int'",
    },
    # --- phone2numeric: `str.lower`'s refusal, on a filter with no `@stringfilter`
    #
    # `phone2numeric` (`defaultfilters.py:984-987`) is one of the four filters the
    # 29 exclude from `@stringfilter` (`tests/test_filter_inventory.py`'s
    # `STRINGFILTER`), so its
    # argument is **not** coerced: `"".join(char2number.get(c, c) for c in
    # phone.lower())` (`text.py:344`) calls a `str` method on whatever it was
    # handed, and the refusal is CPython's `AttributeError`. The class name is
    # the message's whole content — `type(value).__name__` — which is why these
    # rows assert it rather than the text around it, and why `True` is here
    # beside `5`: it is an `int` subclass whose own name is what Python prints.
    #
    # The body synthesises the message rather than passing one through, because
    # no host operation performs `str.lower` on a whole value (`str_lower` takes
    # a `&str`, which only a `str` can supply) — the same shape as
    # `cut/non-string-argument`, and Rust-authored for the same reason.
    {
        "name": "phone2numeric/int",
        "filter": "phone2numeric",
        "value": 5,
        "raises": "AttributeError",
        "message": "'int' object has no attribute 'lower'",
    },
    {
        # `bool` is an `int` subclass and is *still* named `'bool'` — the row
        # that fails for a body classifying the value as an integer.
        "name": "phone2numeric/bool",
        "filter": "phone2numeric",
        "value": True,
        "raises": "AttributeError",
        "message": "'bool' object has no attribute 'lower'",
    },
    {
        "name": "phone2numeric/float",
        "filter": "phone2numeric",
        "value": 5.0,
        "raises": "AttributeError",
        "message": "'float' object has no attribute 'lower'",
    },
    {
        # **`None` is not special-cased here**, unlike `date`/`time`, which answer
        # `""` for it (§10.4). This path has no `if value is None`: the
        # generator calls `.lower()` on it and CPython refuses.
        "name": "phone2numeric/none",
        "filter": "phone2numeric",
        "value": None,
        "raises": "AttributeError",
        "message": "'NoneType' object has no attribute 'lower'",
    },
    {
        "name": "phone2numeric/list",
        "filter": "phone2numeric",
        "value": ["a"],
        "raises": "AttributeError",
        "message": "'list' object has no attribute 'lower'",
    },
    {
        # **The class name a Rust classification cannot produce.** Every object
        # this crate cannot name is `Value::Opaque`, and `type_name` answers
        # `"object"` for all of them — its own doc says that is "not an answer".
        # So a body that matched `Value` itself and synthesised the message
        # writes `'object' object has no attribute 'lower'` here; only asking
        # Django's helper lets CPython name the class. This row and the next are
        # the reason the helper is delegated whole (§D2's third clause).
        "name": "phone2numeric/dict",
        "filter": "phone2numeric",
        "value": {"a": 1},
        "raises": "AttributeError",
        "message": "'dict' object has no attribute 'lower'",
    },
    {
        "name": "phone2numeric/tuple",
        "filter": "phone2numeric",
        "value": (1, 2),
        "raises": "AttributeError",
        "message": "'tuple' object has no attribute 'lower'",
    },
    # --- urlizetrunc: `int(limit)`, the body's first call and its own refusal -
    #
    # `defaultfilters.py:399` is `int(limit)` **before** the helper call, and the
    # body does it first and with no `try` (`urlizetrunc.rs`, the `wordwrap`
    # pattern). Both messages are CPython's on **both** sides — the class and the
    # text both cross the seam out of the real `int` — so what these rows claim
    # is that the body let the refusal escape *before reading the text*, which is
    # the ordering the body's own test pins by class alone. The wording is
    # pinned here because it is the only place it can be.
    {
        # `{{ x|urlizetrunc:None }}` — the explicit `None` token, which is a
        # different call from the bare `{{ x|urlizetrunc }}` (a `TemplateSyntaxError`
        # at parse time, from `args_check`) and the only way a body sees `None`.
        "name": "urlizetrunc/none-argument",
        "filter": "urlizetrunc",
        "value": "see https://example.com/very/long/path now",
        "arg": None,
        "raises": "TypeError",
        "message": (
            "int() argument must be a string, a bytes-like object or a real "
            "number, not 'NoneType'"
        ),
    },
    {
        # A quoted constant, so the argument arrives as `SafeString("abc")` —
        # `int` accepts a `str` *subclass* and refuses its contents, which is why
        # the message names the base-10 literal rather than a type.
        "name": "urlizetrunc/non-numeric-string-argument",
        "filter": "urlizetrunc",
        "value": "see https://example.com/very/long/path now",
        "arg": "abc",
        "raises": "ValueError",
        "message": "invalid literal for int() with base 10: 'abc'",
    },
    # --- json_script: `json.dumps`' refusal, before the id is looked at --------
    {
        # `html.py:103` is `json.dumps(value, cls=encoder or DjangoJSONEncoder)`
        # and it is the body's **first** call, so this `TypeError` escapes before
        # `element_id` is read — the ordering the body's own test pins by
        # asserting the host was called exactly once. CPython's message on both
        # sides, since the host makes Django's own call.
        "name": "json_script/set-is-not-serializable",
        "filter": "json_script",
        "value": {1, 2},
        "raises": "TypeError",
        "message": "Object of type set is not JSON serializable",
    },
    # --- date: the `TypeError` the filter does not catch -----------------------
    #
    # `Formatter.format` refuses a time-related specifier on a real `date`
    # (`dateformat.py:44-48`), and `date` catches **only** `AttributeError`, so it
    # propagates out of the filter. `time`'s identical `'H'` row answers `''` in
    # `CASES` — the catch-lists are the whole difference and the pair is the
    # measurement of it. The message is CPython's on both sides (the host calls
    # Django's own `formats.date_format`), so pinning the wording here is a claim
    # that the seam carried it, and each body's own test asserts the class alone.
    {
        "name": "date/time-related-specifier",
        "filter": "date",
        "value": date(2026, 10, 8),
        "arg": "H",
        "raises": "TypeError",
        "message": (
            "The format for date objects may not contain time-related format "
            "specifiers (found 'H')."
        ),
    },
    {
        # A quoted constant naming a *setting*, resolved by `get_format` to
        # `'P'` — so the message names `P`, not the argument's text.
        "name": "date/time-format-name",
        "filter": "date",
        "value": date(2026, 10, 8),
        "arg": "TIME_FORMAT",
        "raises": "TypeError",
        "message": (
            "The format for date objects may not contain time-related format "
            "specifiers (found 'P')."
        ),
    },
    {
        # A bare `True` is a `var_arg`, so `get_format` stringifies it to
        # `'True'` — and `T` is a time-related specifier. One row for
        # `get_format`'s `str(format_type)` and one for the catch-list, from the
        # same line.
        "name": "date/true-argument",
        "filter": "date",
        "value": date(2026, 10, 8),
        "arg": True,
        "raises": "TypeError",
        "message": (
            "The format for date objects may not contain time-related format "
            "specifiers (found 'T')."
        ),
    },
    # --- timesince / timeuntil: the `AttributeError` the catch misses ----------
    #
    # The helper cannot read `.year` off a `str`, and `except (ValueError,
    # TypeError)` is not `AttributeError`, so the refusal escapes the filter. Its
    # twin — the mixed-awareness `TypeError` — is caught and answers `''` in
    # `CASES`; the two classes leave the *same* filter in opposite directions,
    # which is the reason the seam returns a `Result` at all. The message is
    # CPython's on both sides, and `"x"` is truthy so it passes `if not value`.
    {
        "name": "timesince/non-date-value",
        "filter": "timesince",
        "value": "x",
        "raises": "AttributeError",
        "message": "'str' object has no attribute 'year'",
    },
    {
        # The same input with the argument present: `if arg` is satisfied, so
        # the helper is called with `now`, and it still cannot read `.year`.
        "name": "timesince/non-date-value-with-argument",
        "filter": "timesince",
        "value": "x",
        "arg_variable": datetime(2026, 10, 8, 22, 30),
        "raises": "AttributeError",
        "message": "'str' object has no attribute 'year'",
    },
    {
        "name": "timeuntil/non-date-value",
        "filter": "timeuntil",
        "value": "x",
        "raises": "AttributeError",
        "message": "'str' object has no attribute 'year'",
    },
    # --- M12 Task 7: the i18n four **propagate** where the tz three swallow ----
    #
    # **This is a regression pin on `PyFilterHost::host_error`, and the two rows
    # are one per half of what it has to preserve.** `language_name`'s body is a
    # single host call — `translation.get_language_info(lang_code)["name"]`
    # (`i18n.py:294`) — and `get_language_info` refuses an unknown code with a
    # `KeyError` and a non-string with a `TypeError`. Both must reach the
    # template author **as themselves**, with the exact message, and they do
    # **by construction**: `host_error` keeps the exact `PyErr` and `report()`
    # re-raises that instance, so a body cannot wrap or re-word one. What these
    # rows add is not a defect fixed but a pin on the two halves of that
    # construction — the **class** (from the first) and the **message text**
    # (from the second, which names CPython's operand type). A future edit that
    # swapped `host_error` for a generic `Error::Message` would keep every
    # `language_name` *good*-input row green and fail here.
    #
    # **The two messages come from two different trees, and they do not have the
    # same durability — so the two rows assert them differently.**
    #
    # * The `KeyError`'s text is **Django's**: `django/utils/translation/__init__.py:280`
    #   is `raise KeyError("Unknown language code %s." % lang_code)`. Pinned to
    #   `.django/` @ **6.0.9**, so a Django bump is the only thing that moves it —
    #   and a change in it *is* a divergence we want to catch. The row freezes the
    #   literal.
    # * The `TypeError`'s text is **CPython's**, not Django's: it is
    #   character-for-character what `"-" not in 7` produces, and Django supplies
    #   only the call site — `django/utils/translation/__init__.py:279`,
    #   `if "-" not in lang_code:`. (Two line numbers, not one: :279 is the `in`
    #   test that raises the `TypeError`, :280 is the `raise` that produces the
    #   `KeyError`.)
    #
    #   **This half is the fragile one, because the wording is the interpreter's
    #   and it changed one minor version ago.** Measured here:
    #
    #       3.9.6  3.9.21  3.10.16  3.11.9  3.12.9  3.13.7  ->  "argument of type 'int' is not iterable"
    #       3.14.4                                             ->  "argument of type 'int' is not a container or iterable"
    #
    #   So its row **derives** the expectation instead of freezing it —
    #   `_LANGUAGE_NAME_NON_STRING_TYPEERROR`, computed from the interpreter above the
    #   list — and asserts the **class** (`"raises": "TypeError"`) as the part
    #   that must not drift. The derivation's docstring records why, and why the
    #   agreement is transport fidelity rather than a tautology.
    #
    # **The asymmetry with the tz three is the milestone's most quotable fact,
    # and it is upstream's, not a choice we made.** `i18n.py` does not guard its
    # four `language_*` filters at all, so their host refusal escapes; `tz.py`'s
    # `do_timezone` is written to never raise — the bare `except Exception`
    # (`tz.py:54-56`) and the `""` returns inside the `isinstance` chain
    # (`tz.py:59-67`) — so `localtime`/`utc`/`timezone` answer `""` on every bad
    # input. The four `CASES` rows ported from `timezones/tests.py` above are
    # that swallow's pin, and there is deliberately no `ERROR_CASES` row for
    # them. Two policies, both Django's: **the four i18n names propagate; the
    # three tz names swallow.**
    {
        # The message is Django's **repr of the key**, not the key: Python's
        # `str(KeyError("x"))` is `"'x'"`, quotes included, and the harness
        # compares `str(caught)` as a literal — so the pinned text is what the
        # template author actually sees, `Unknown language code zz.` with the
        # surrounding quotes being `KeyError`'s own.
        "name": "language_name/unknown-language-code",
        "filter": "language_name",
        "value": "zz",
        "libraries": ["django.templatetags.i18n"],
        "raises": "KeyError",
        "message": "'Unknown language code zz.'",
    },
    {
        # The other half: `get_language_info(7)` reaches the membership test with
        # an `int` and CPython refuses it. `7` is a bare token, so the value
        # arrives as the `int` 7 rather than the string `"7"` — and `"7"` is a
        # legal (if unknown) *code*, a different row's input, which is why the
        # type is named in the message.
        #
        # **The expected text is *derived*, not frozen** — `_LANGUAGE_NAME_NON_STRING_TYPEERROR`
        # (and the `value` above, `_LANGUAGE_NAME_NON_STRING_VALUE`) is computed
        # from the interpreter by `_typeerror_from_membership`, because it is
        # CPython's wording and CPython changed it at 3.14; see that function's
        # docstring for why a literal here would be the wrong kind of red and for
        # why the derivation takes the *value* rather than a second literal. The
        # class **is** frozen (`"raises": "TypeError"`), which is the half
        # `host_error` must carry: `Error::filter_host(kind, operation, message)`
        # transports a kind as well as a message, so a same-text error of the
        # wrong kind would otherwise pass.
        #
        # **Why the agreement is not tautological, even though both strings come
        # from this interpreter.** The two sides travel different paths: ours is
        # raised at Django's call site *inside the engine*, stringified by
        # `host_error`'s `str(e)` into a Rust `String`, carried over the FFI and
        # re-raised as a Python `TypeError`; the derived one never leaves the
        # interpreter that made it. Their agreeing is evidence of transport
        # fidelity — the Python→Rust→Python round trip and the kind mapping —
        # which is the whole claim of the row.
        "name": "language_name/non-string-value",
        "filter": "language_name",
        "value": _LANGUAGE_NAME_NON_STRING_VALUE,
        "libraries": ["django.templatetags.i18n"],
        "raises": "TypeError",
        "message": _LANGUAGE_NAME_NON_STRING_TYPEERROR,
    },
]
