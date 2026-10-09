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

**`arg` is absent when the filter takes none** rather than written as `None`,
because the two are different inputs: an explicit `None` argument would make the
token `x|default:None`, which Django resolves as a `None` constant. Rows are
declared with the keys they need; the harness uses `.get("arg")`.

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

Rows for a filter arrive in **the commit that lands its group** (§D6), so the
set of filters named here is a statement about M4's progress as well as about
correctness — which is why `test_filter_parity.py` checks both directions of it
rather than only comparing the rows it is given.
"""

from __future__ import annotations

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
]
