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
    # --- center (`:431-438`): the format spec `^`, NOT `str.center` ----------
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
        # `width <= 0` returns the value **unchanged** (`:435`), and the guard
        # runs *after* `int(arg)`, so `center("abc", 0)` is `'abc'` and
        # `center("abc", "x")` raises rather than returning. The pair below
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
    # --- ljust / rjust (`:417-430`): the builtins, with no `<= 0` guard ------
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
    # --- truncatechars / truncatewords (`:305-314`, `:330-337`) --------------
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
    # --- wordwrap (`:410-415`): `textwrap` with `break_long_words=False` -----
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
]
