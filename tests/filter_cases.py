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

**Three optional keys change what the row is, and each exists because the
default would quietly score a different question.** `value_factory` replaces
`value` for a **one-shot** input — a generator is consumed by whichever side
reads it first, so a shared one makes the second side's answer a statement about
an exhausted iterator; see [`case_value`]. `context` overrides the `Context` the
loop reads `autoescape` and `use_tz` off, which is how the `needs_autoescape`
filters get both branches: `join` with and without escaping answers different
text from the same input, so nothing but the key can tell the two rows apart.

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

The rows whose input survives the filter's own `try` are a different thing and
stay here, where they are common: the `dictsort` rows that end in `""` are three
different caught arms.

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
    # --- first (`:700`, `value[0]`, catching **only** IndexError) -----------
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
    # --- last (`:706`, `value[-1]`, is_safe=True) --------------------------
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
    # --- length (`:712`, `len(value)` on `(ValueError, TypeError)` -> 0) ---
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
    # --- slice (`:640`, Rust parses the bits, the host subscripts) ---------
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
    # --- join (`:604`, needs_autoescape, is_safe=True) ---------------------
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
    # --- random (`:590`, `random.choice` on IndexError -> "") --------------
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
    # --- dictsort / dictsortreversed (`:580`/`:560`) ----------------------
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
    # --- unordered_list (`:669`, needs_autoescape, recursive, tabs) -------
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
        "name": "pluralize/empty-string-is-not-len-based",
        "filter": "pluralize",
        "value": "",
    },
    {
        # A numeric *string* goes through `float`, so it is a number and not a
        # length — and `"1"` is the row where that is invisible: `float("1")` is
        # `1.0` and `len("1") == 1` select the same empty singular suffix, so a
        # body that took the `len` arm by mistake agrees here. It is the *only*
        # one-character string that coincides (a one-character string is
        # singular through `len`, and singular through `float` only for `"1"`),
        # which is why it is kept even though it can never fail on its own: it is
        # the coinciding twin of `"2"` below, and a set with `"2"` alone would not
        # show that the other single digit had been *agreeing*.
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
        # way a body sees `None` here. The FFI hands the body `Option::None` for
        # it, the same value it uses for a missing argument, which is exactly why
        # the reachable case is the one worth pinning.
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
]
