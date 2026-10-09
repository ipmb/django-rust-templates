# Task 10 — `date`, `time`, `timesince`, `timeuntil`

**Derive everything against `.django/` at Django 6.0.9.** An unpinned Django also
exists on this machine; every claim below was measured on the pinned checkout, and
a claim you cannot re-measure there is one you should stop and report.

This is M4's last filter task: it empties `STILL_STUBBED`. Three of the four are
the milestone's only `expects_localtime` filters (the flag sits on `date` and
`time`; `timesince`/`timeuntil` do **not** carry it), and two of them — `date` and
`time` — are the only filters in the 57 whose body is a **fallback `try/except`
whose two branches call different functions**. Measured by walking
`defaultfilters.py` with `ast`: `cut` is the one body my decorator walk misses
(bare `@register.filter`) and it has no `try`; the bodies with two or more `try`
statements are `floatformat` (five, one sequential chain), `unordered_list`
(three), and `add`, `get_digit`, `date`, `pluralize`, `time` (two each) — and of
those, `get_digit`'s two branches are `int(value)` and `int(value, 10)`, the same
function twice, while `date` and `time` call `formats.date_format` **or**
`dateformat.format`, which is what makes a second seam method necessary rather
than tidy.

## Ground rules

- **One group, four bodies, one commit.** They share a seam change and a
  subject matter; splitting them would split the seam's doc.
- **Rows land with the body, in the same commit.** §10.4 records why: the
  1547-test gate's only call into this layer is `_native.parse`'s
  `check_filter`, which sees a filter's name and arity and never its body. The
  corpus is the only gate that can see a filter body.
- **Delete each stub's doc paragraph** ("Stub. §10.3's M4 row owns this filter…")
  as you write the body: the body/doc pair is what makes a stub a stub.
- **Do not edit the seam** (`crates/template-core/src/filters/host.rs`) — §10.4.
  The three changes this task needs are landed by the coordinator already (next
  section). If you find a fourth, stop and report it; that is how the three were
  found.
- **Do not edit the plan**
  (`docs/superpowers/plans/2026-10-08-m4-built-in-filters.md`), the design spec,
  or `.review/*`. Do not touch `.django/`.
- **Report a gap rather than papering over one.** If the pinned source says
  something this brief does not, say so and leave the code in the state the
  source supports.
- Attribution: end the commit message with
  `Co-Authored-By: Claude Code <noreply@anthropic.com>`.

## The seam changes you inherit — already landed

Three, all in the commit before yours. Each is a signature that **could not
express the source's own control flow**, which is the only reason this task is
allowed to move the file §10.4 freezes.

### 1. `timesince` gained a `Result`

```rust
fn timesince(&self, d: &Value, now: Option<&Value>, reversed: bool) -> Result<String, Error>;
```

`timesince_filter` (`:811-821`) wraps its call in
`except (ValueError, TypeError): return ""`. Two measured inputs reach that arm,
and one class escapes it:

```text
timesince(naive_dt, aware_dt)   TypeError: can't subtract offset-naive and offset-aware datetimes
timeuntil(naive_dt, aware_dt)   TypeError: can't subtract offset-naive and offset-aware datetimes
timesince("x")                  AttributeError: 'str' object has no attribute 'year'
timesince(naive_dt, "x")        AttributeError: 'str' object has no attribute 'year'
```

So the body must **catch two classes and let a third escape**, which an
infallible `-> String` cannot do in either direction. The old signature panicked
(`FilterHost::timesince raised …`), and the corpus's `assert_raises_same`
compares the class *and* the message, so `{{ "x"|timesince }}` would have been a
`PanicException` against `AttributeError`.

This was not a surprise finding: `crates/template-py/src/filters.rs:1461-1480`
carries an audit table of the operations that were `-> String`, with the row

```text
| `timesince` | not yet reachable — Task 10 lands its only caller |
```

and the rule *"an operation whose raising input is later found leaves this list
by gaining a `Result`"*. That row now names the input instead.

### 2. Two raw formatter methods, for the second branch of `date` and `time`

```rust
/// `django.utils.dateformat.format` (`dateformat.py:326-329`) — `date`'s
/// second branch.
fn dateformat_format(&self, value: &Value, format: &Value) -> Result<String, Error>;
/// `django.utils.dateformat.time_format` (`dateformat.py:332-335`) — `time`'s
/// second branch.
fn dateformat_time_format(&self, value: &Value, format: &Value) -> Result<String, Error>;
```

Each is a two-line wrapper: `DateFormat(value).format(format_string)` and the
`TimeFormat` twin. Neither resolves a format setting and neither is the
`formats.*` method above — that is the whole reason they exist.

**The two branches of `date` call two different functions.** Branch 1 is
`formats.date_format(value, arg)`, whose whole body is
`dateformat.format(value, get_format(format or "DATE_FORMAT"))`
(`formats.py:147-157`) — that resolution is what the existing `date_format`
seam method mirrors. Branch 2 is `format(value, arg)`, where `format` is
`django.utils.dateformat.format` imported at `defaultfilters.py:14` — the same
formatter **without `get_format`**, so the argument text is used literally.

The two therefore differ exactly when `str(arg)` is falsy or names a format
setting, *and* branch 1 raised. Measured on a value that carries the date-format
protocol but no `year` (so the resolved default `'N j, Y'` fails at `Y` while the
literal text does not):

```text
x|date                      -> ''                      (both branches fail)
x|date:"DATE_FORMAT"        -> 'ThuPMOctober_OctoberROctPM'   <- branch 2, literal
x|date:"SHORT_DATE_FORMAT"  -> 'th14R_ThuPMOctober_OctoberROctPM'
x|date:""                   -> ''
```

On a **second** fixture — `month`, `year`, `isocalendar()` and `tzname()` but no
`day`, so branch 1 fails at `j` while every character of `"None"` survives — the
absent argument reaches branch 2 and answers:

```text
x|date                      -> 'Oct.202610'   <- branch 2 fed str(None) = 'None'
x|date:"DATE_FORMAT"        -> ''             <- this fixture lacks O/R/M
x|date:5                    -> '5'
```

That first row is worth a corpus case of its own: it is the only one that pins
branch 2 receiving the **sentinel's text** rather than the resolved default.

A body that expressed branch 2 as `date_format(value, Some(text))` would answer
`''` for the middle two rows, because `get_format("DATE_FORMAT")` resolves to
`'N j, Y'` and that raises. So this is not tidiness: it is a **silent wrong
answer** for a class of input, and the existing seam cannot express the source.

**Why `&Value` and not `&str`.** `Formatter.format`'s first act is
`re_formatchars.split(str(formatstr))` (`dateformat.py:42`), so the wrapped
function's observable input is the argument's *text* — but the text is computed
by Python, from any object, and an absent argument does not make it empty.
Measured on `datetime(2026, 10, 8, 14, 30, 45)`:

```text
str(None)                       = 'None'
dateformat.format(dt, None)     = 'Oct.202610'      <- N/o/n/e are format chars
dateformat.format(dt, True)     = 'UTCThu, 08 Oct 2026 14:30:45 +0000000000'
dateformat.format(dt, 5)        = '5'
formats.date_format(dt, None)   = 'Oct. 8, 2026'    <- branch 1, by contrast
```

So a body holding `&str` would have to pass the literal `"None"` (and `"True"`,
and `"5"`) to reproduce branch 2 — that is the Rust side computing `str()`, the
same pattern this milestone rejected five times over. `&Value` lets Python do it
where Python does it; the host impl costs one line.

**State plainly that no corpus row can tell the two apart.** A Rust body that
passed the literal `"None"` answers exactly what `&Value` answers, so the choice
is the seam-signature rule about *who computes `str()`*, not an observable. It is
worth taking because the rule's value is that nobody has to check whether this
particular conversion happens to be invariant — §D7.1 says when `&Value` is
required, and `dateformat.format(value, formatstr)` takes any object.

**The `time` twin differs from the `date` twin on exactly this input**, which is
the second reason the method returns a `Result`:

```text
dateformat.format(dt, None)        = 'Oct.202610'
dateformat.time_format(t, None)    -> AttributeError: 'TimeFormat' object has no attribute 'N'
```

`TimeFormat` has no `N`, so the absent argument *raises* there. Both classes are
already the caller's business — `date` catches only `AttributeError` and `time`
catches `(AttributeError, TypeError)` — and both are reachable only through this
pair, so a `String` return could not express either.

**The asymmetry with `date_format`'s `Option<&str>` is deliberate, and the doc
should say so**, because the next reader will see two spellings for two format
arguments. `formats.date_format` hands its argument to
`get_format(arg or "DATE_FORMAT")`, where it is a **lookup key** and `None` is a
sentinel selecting the default key — `Option<&str>` mirrors that wrapped
signature exactly. `dateformat.format` `str()`s **any object** and splits the
text — `&Value` mirrors that one. Two different wrapped signatures, two seams;
the rule produced the difference rather than surviving it.

### 3. Nothing else. `date_format`/`time_format` are unchanged

They already take `Option<&str>`, and `None` means "the default, resolved in the
active language" rather than "the text `DATE_FORMAT`" — the `de` rows in the seam
doc settle it. Branch 1 of each body therefore passes:

```rust
let format = match arg {
    Some(arg) if call.host.is_true(arg)? => Some(call.host.to_str(arg)?),
    _ => None,
};
```

`is_true` before `to_str`, because Django's `format or "DATE_FORMAT"` reads the
argument's **truthiness**, not its presence. Measured on `date(2026, 10, 8)`:
`date:0`, `date:""` and `date` all answer `'Oct. 8, 2026'`, while `date:5`
answers `'5'` (a bare number is a `var_arg` and arrives as the `int` 5, which
`get_format` stringifies itself).

## The one narrowing this task takes, and it is `date`/`time`'s first line

`if value in (None, "")` is Python `==` against two sentinels, and the seam has
no equality operation. Rust expresses it as

```rust
fn is_none_or_empty(value: &Value) -> bool {
    matches!(value, Value::None) || matches!(value, Value::Str(s) | Value::Safe(s) if s.is_empty())
}
```

**It is not `bool(value)`.** Measured — `0`, `False`, `[]`, `{}`, `()`, `0.0` are
all *falsy* and all pass `in (None, "")`, and they diverge from a truthiness test
as soon as the argument is non-empty:

```text
x|date:5 with 0 / False / [] / {} / () / 0.0   -> '5'   <- proceeds; truthiness gives ''
x|date   with any of those                     -> ''    <- both agree (refused later)
x|date:"" with any of those                    -> ''    <- both agree
x|date:5 with ""                               -> ''    <- both agree (the one value caught)
```

So the rows agree on `arg`-less calls and on `{{ 0|date:"Y" }}` (both `''`), and
diverge on `{{ 0|date:5 }}`: a truthiness guard answers `''` where Django answers
`'5'`, for every falsy non-`None`, non-`""` value rather than only for one that
looks date-like.

**The residual gap, stated with its trigger.** The narrowing is exact unless a
value's `__eq__` answers `True` for `None` or `""` *and* the value carries the
date-format protocol — the only way to reach a difference. No fixture in
`tests/filter_cases.py` is that object today (`DOT_ATTR`, the class that defines
`__eq__`, scopes it to `isinstance`), so **if the corpus ever gains an object
fixture defining `__eq__`, this guard is re-checkable** — that is the condition
that would make the note false. The module docs should carry that trigger rather
than a standing "unreachable".

## `date` — `defaultfilters.py:783-794`

```python
@register.filter(expects_localtime=True, is_safe=False)
def date(value, arg=None):
    """Format a date according to the given format."""
    if value in (None, ""):
        return ""
    try:
        return formats.date_format(value, arg)
    except AttributeError:
        try:
            return format(value, arg)
        except AttributeError:
            return ""
```

**`expects_localtime=True` is not yours.** `run_filter` applies
`localtime(value, use_tz)` before the body, and `FilterCall::use_tz` is the
context's flag. Do **not** call `localtime` in the body — two calls are two
answers, and the flag is what decides whether it happens at all. The row that
pins this is below.

The body is then: guard → branch 1 with the resolved format → on
`AttributeError` branch 2 with the literal text → on `AttributeError` `""`.
`Value::Str` for every return; the flag pair is `is_safe=False` with no
`@stringfilter`, so nothing marks the result and the corpus's type comparison
expects `"str"` everywhere.

Rows, every one measured:

| token | value | answer |
|---|---|---|
| `x\|date` | `date(2026,10,8)` | `'Oct. 8, 2026'` |
| `x\|date:"DATE_FORMAT"` | `date(2026,10,8)` | `'Oct. 8, 2026'` |
| `x\|date:""` | `date(2026,10,8)` | `'Oct. 8, 2026'` |
| `x\|date:0` | `date(2026,10,8)` | `'Oct. 8, 2026'` |
| `x\|date:5` | `date(2026,10,8)` | `'5'` |
| `x\|date:"x"` | `date(2026,10,8)` | `'x'` |
| `x\|date:"Y-m-d"` | `date(2026,10,8)` | `'2026-10-08'` |
| `x\|date:"jS"` | `date(2026,10,8)` | `'8th'` |
| `x\|date:"\\Y"` | `date(2026,10,8)` | `'Y'` |
| `x\|date:"H"` | `date(2026,10,8)` | **raises** `TypeError: The format for date objects may not contain time-related format specifiers (found 'H').` |
| `x\|date:"TIME_FORMAT"` | `date(2026,10,8)` | **raises** `TypeError: … (found 'P').` |
| `x\|date:True` | `date(2026,10,8)` | **raises** `TypeError: … (found 'T').` |
| `x\|date` | `"x"`, `5`, `None`, `""`, `mark_safe("")`, `0`, `False`, `[]`, `{}` | `''` |
| `x\|date:"Y"` | `datetime(2026,10,8,14,30,45)` | `'2026'` |
| `x\|date:"Y-m-d"` | `mark_safe("2026-10-08")` | `''` |

Three things worth reading twice.

- **The `TypeError` rows raise out of the filter.** The guard is in
  `Formatter.format` (`dateformat.py:44-48`) and `date` catches **only**
  `AttributeError`, so `{{ d|date:"H" }}` propagates where `{{ d|time:"H" }}`
  answers `''` (next section). Both are real rows and the pair is the point.
- **`x|date:5` is not a mistake.** A bare `5` is a `var_arg`, so the filter is
  handed the `int` 5; `get_format` does `str(format_type)` and `'5'` is not a
  format setting, so it comes back as itself and formats to `'5'`.
- **`mark_safe("2026-10-08")|date` is `''`** — a `SafeString` has no `.year`
  either, so both branches fail. The *value*'s safety is not the *result*'s.

## `time` — `defaultfilters.py:797-808`

```python
@register.filter(expects_localtime=True, is_safe=False)
def time(value, arg=None):
    """Format a time according to the given format."""
    if value in (None, ""):
        return ""
    try:
        return formats.time_format(value, arg)
    except (AttributeError, TypeError):
        try:
            return time_format(value, arg)
        except (AttributeError, TypeError):
            return ""
```

Same skeleton as `date` with **three** differences, and each is a row:

1. the resolved default is `TIME_FORMAT` (`formats.time_format`, `:160-169`) —
   `'P'` in `en-us`;
2. **both** branches catch `TypeError` as well as `AttributeError`;
3. the fallback is `dateformat.time_format`, hence `dateformat_time_format`.

Measured:

| token | value | answer |
|---|---|---|
| `x\|time` | `time(14,30,45)` | `'2:30 p.m.'` |
| `x\|time:"H:i"` | `time(14,30,45)` | `'14:30'` |
| `x\|time:"H:i:s"` | `time(14,30,45)` | `'14:30:45'` |
| `x\|time:"P"` | `time(14,30,45)` | `'2:30 p.m.'` |
| `x\|time:"H:i"` | `datetime(2026,10,8,14,30,45)` | `'14:30'` |
| `x\|time:"H"` | `date(2026,10,8)` | `''` |
| `x\|time:"H:i"` | `date(2026,10,8)` | `''` |
| `x\|time` | `date(2026,10,8)` | `''` |
| `x\|time` | `"x"`, `None`, `""` | `''` |

`{{ date(2026,10,8)|time:"H" }}` is the sharp one: branch 1 raises the *same*
`TypeError` the `date` row above propagates, `time` catches it, branch 2 raises
it again (a `date` has no `.hour`), and the second catch answers `''`. A body
that caught only `AttributeError` answers nothing here — it raises.

## `timesince` — `defaultfilters.py:811-821`

```python
@register.filter("timesince", is_safe=False)
def timesince_filter(value, arg=None):
    """Format a date as the time since that date (i.e. "4 days, 6 hours")."""
    if not value:
        return ""
    try:
        if arg:
            return timesince(value, arg)
        return timesince(value)
    except (ValueError, TypeError):
        return ""
```

- `if not value` is **truthiness** — `is_true`, unlike `date`'s two sentinels.
  Measured: `None`, `0`, `""` and `[]` all answer `''`.
- `if arg` is also truthiness. Measured `timesince:0` and `timesince:""` are the
  same answer as no argument, because a falsy `now` falls through to
  `datetime.now()` inside the helper (`timesince.py:67-68`). Mirror the source
  anyway: `timesince(value)` and `timesince(value, None)` are two calls in the
  source and one call in the seam, and the guard is one line.
- **`now` is `Option<&Value>` and `None` is the right value.** The helper fills
  `now` from `d.tzinfo` when it is falsy, and passing `timezone.now()` instead is
  the measured `TypeError` above — the seam's `now` doc has the whole argument.
- **Do not catch `AttributeError`.** It escapes the filter, and the corpus's
  error rows assert the class *and* the message text, so the message must be
  Python's: `'str' object has no attribute 'year'`.

Rows. `A = datetime(2026, 10, 8, 22, 30)`, `B = datetime(2026, 10, 8, 23, 51)`,
both naive, so the text is deterministic with no `now()` in it:

| token | value | `o` | answer |
|---|---|---|---|
| `x\|timesince:o` | `A` | `B` | `'1\xa0hour, 21\xa0minutes'` |
| `x\|timesince:o` | `B` | `A` | `'0\xa0minutes'` |
| `x\|timesince:o` | `date(2026,10,8)` | `A` | `'22\xa0hours, 30\xa0minutes'` |
| `x\|timesince:o` | `A` with `tzinfo=utc` | `A` | `''` — the helper's `TypeError`, **caught by the filter** |
| `x\|timesince:o` | `A` | `A` with `tzinfo=utc` | `''` — the same, the other way round |
| `x\|timesince` | `None`, `0`, `""`, `[]` | — | `''` |
| `x\|timesince` | `now() + 3650 days` | — | `'0\xa0minutes'` |
| `x\|timesince` | `"x"` | — | **raises** `AttributeError: 'str' object has no attribute 'year'` |
| `x\|timesince:o` | `"x"` | `A` | **raises** the same |

**The naive/aware rows answer `''`, not a raise.** This is the trap in this
section: the two error classes go opposite ways through the *same* filter. The
helper raises `TypeError` for a mixed-awareness pair and the filter's
`except (ValueError, TypeError)` turns it into `''`, so those rows belong in
`CASES` with the empty answer; `"x"` raises `AttributeError`, which the filter
does **not** catch, so those rows belong in `ERROR_CASES`. A body that
propagated both, or swallowed both, passes one half of the pair and fails the
other — which is the whole reason `timesince`'s seam signature is a `Result`.

**Flakiness, and it is the reason the rows above carry `o`.** With no `now`, the
helper calls `datetime.now()` *inside the Rust call and inside Django's call*,
microseconds apart, so any row without an explicit `o` is a race at a unit
boundary. Only the far-future row is safe without one (it is `'0\xa0minutes'` for
the next three thousand days). Everywhere else pass `o` through `arg_variable` —
a `datetime`, aware or not, is not one of the shapes
`scripts/filter_probe.template_literal` can spell as a constant, which is exactly
what that key is for.

## `timeuntil` — `defaultfilters.py:824-832`

```python
@register.filter("timeuntil", is_safe=False)
def timeuntil_filter(value, arg=None):
    """Format a date as the time until that date (i.e. "4 days, 6 hours")."""
    if not value:
        return ""
    try:
        return timeuntil(value, arg)
    except (ValueError, TypeError):
        return ""
```

Two differences from `timesince_filter`, and both are deliberate in the source:

- **`arg` is passed unconditionally** — no `if arg` guard. The two spellings are
  behaviourally identical for every falsy argument (both end at `now()` inside
  the helper) and the source is the tiebreak: pass `Some(arg_or_none)` through.
- `timeuntil(value, arg)` is `timesince(value, arg, reversed=True)`
  (`timesince.py:138-143`, a one-line delegation).
  Use the seam's `reversed` flag; do **not** swap the operands, which normalises
  the timezones the other way and reports different months for two aware
  datetimes in different zones (the seam's `timesince` doc has the measurement).

Rows, with `A`/`B` as above:

| token | value | `o` | answer |
|---|---|---|---|
| `x\|timeuntil:o` | `A` | `B` | `'0\xa0minutes'` |
| `x\|timeuntil:o` | `B` | `A` | `'1\xa0hour, 21\xa0minutes'` |
| `x\|timeuntil:o` | `date(2026,10,8)` | `A` | `'0\xa0minutes'` |
| `x\|timeuntil:o` | `A` with `tzinfo=utc` | `A` | `''` — the caught `TypeError` |
| `x\|timeuntil` | `None`, `0`, `""`, `[]` | — | `''` |
| `x\|timeuntil` | `"x"` | — | **raises** `AttributeError: 'str' object has no attribute 'year'` |

The first two rows are the swap read out loud: `timeuntil(A, o=B)` is
`timesince(B, o=A)`, and `'0\xa0minutes'` where `timesince` said
`'1\xa0hour, 21\xa0minutes'` is that identity, not a bug in either body.

`timeuntil` has no `now()`-free deterministic row of the `'0 minutes'` kind on
its own terms — the helper's "in the future, stop processing" arm is on the
*reversed* side of the swap, so a past value is what answers `'0\xa0minutes'`
and a future value is a real interval. Pin it with a fixed `o`, as above, or do
not pin it.

## The `expects_localtime` rows — the ones `date`/`time` need and no earlier filter could have

`run_filter` already applies `localtime` when the flag is set, so these rows are
the only corpus evidence that the two filters carry the flag at all. Both sides
read `use_tz` off the `Context`, and the zone off settings, so the row needs the
`context` **and** `settings` keys and — because `settings` rows can move both
engines together — an `expected` literal.

```python
{
    "filter": "date",
    "value": datetime.datetime(2026, 10, 8, 22, 30, tzinfo=datetime.timezone.utc),
    "arg": "Y-m-d H:i",
    "context": {"use_tz": True},
    "settings": {"USE_TZ": True, "TIME_ZONE": "Europe/Paris"},
    "expected": "2026-10-09 00:30",
}
```

Measured under `override_settings(USE_TZ=True, TIME_ZONE="Europe/Paris")`, with
the value `2026-10-08 22:30 UTC`:

```text
{{ v|date:"Y-m-d H:i" }}   context.use_tz=True   -> '2026-10-09 00:30'
{{ v|date:"Y-m-d H:i" }}   context.use_tz=False  -> '2026-10-08 22:30'
{{ v|time:"H:i" }}         context.use_tz=True   -> '00:30'
{{ v|time:"H:i" }}         context.use_tz=None   -> '00:30'   (USE_TZ=True)
{{ v|date:"Y-m-d H:i" }}   naive value           -> '2026-10-08 22:30'
```

The third and fourth rows are the pair to land: `None` means "the settings'
`USE_TZ`", which is a different fact from `True`, and the flag's whole job is
that difference. The naive row is the negative control — a body that called
`localtime` itself, or that ignored `use_tz`, moves one of the three.

## Two things the plan says about this task that the pinned tree contradicts

The plan's Task 10 paragraph
(`docs/superpowers/plans/2026-10-08-m4-built-in-filters.md:3370-3393`) carries two
claims you should not implement. Do not edit the plan — the coordinator corrects
it after your commit lands — but if you find a third, report it.

1. **"`now` comes from `FilterHost::now()`, so the corpus row for the `arg=None`
   case is time-dependent."** It does not, and it is not.
   `timesince_filter(value)` calls `timesince(value)`, and the helper fills `now`
   from `d.tzinfo` itself (`timesince.py:67-68`) — `FilterHost::now`'s own doc
   records that supplying `timezone.now()` instead turns `{{ naive|timeuntil }}`
   into `''` through the filter's catch. The `arg=None` case is time-dependent
   only in the sense that `datetime.now()` is *called inside the helper*; a value
   far enough in the future is a stable `'0\xa0minutes'` for the next three
   thousand days, which is an equality row like any other.
2. **"The corpus … pins the one-argument form with a monotonicity property."**
   No. The two-argument form is exact equality for any pair of fixed datetimes
   (the tables above), and the one-argument form is exact equality on the
   far-future row. A property row would be a second, weaker instrument over
   answers this corpus can compare outright — `random` is the only filter that
   needed one, and it needed it because its answer is genuinely chosen at
   random.

The plan's line ranges for these four start correctly but run past the body. It
gives `date` as `:783-795`, `time` as `:797-809`, `timesince` as `:811-822` —
each ending on the blank line *after* the body, one past it — and `timeuntil` as
`:824-838`, which overshoots the body by six and reaches the `####` separator at
835. Measured on the pinned 6.0.9 file: `date` is `:783-794`, `time` is
`:797-808`, `timesince_filter` is `:811-821`, `timeuntil_filter` is `:824-832`.
Cite the file, not the plan.

## Traps this task has that wave B's brief did not

- **The two `format` functions.** `defaultfilters.py:14` is
  `from django.utils.dateformat import format, time_format`, so `date`'s
  fallback is `django.utils.dateformat.format` — not the builtin `format`, not
  `str.format`, and not `formats.date_format`. This was mis-derived twice before
  it was measured; do not re-derive it.
- **`get_format` stringifies.** `formats.py:113` is `format_type = str(format_type)`,
  so a non-`str` argument is legal and its text is what the formatter sees.
  `date:5` → `'5'` and `date:True` → a `TypeError` naming `'T'` both follow from
  that one line.
- **`TypeError` is not `AttributeError` in either direction.** `date` catches
  one, `time` catches both, `timesince`/`timeuntil` catch `(ValueError, TypeError)`
  and let `AttributeError` escape. Four filters, three catch-lists, and the
  corpus rows disagree with each other on purpose.
- **Text, not truth, in three places.** `timesince` of a value in the future is
  `'0\xa0minutes'`; `timeuntil` of the same value is a real interval. The
  separator inside the unit strings is `\xa0` (NBSP), it comes from Python's own
  `TIMESINCE_CHUNKS`/`ngettext`, and a hand-built `'0 minutes'` fails the corpus
  by one byte. Nothing in this task should build a time string: the host answers
  the whole thing.
- **`now()` has no caller here, and must not get one.** `timesince`/`timeuntil`
  fill their own `now` from `d.tzinfo`; the seam's `now` doc records that
  supplying `timezone.now()` turns `{{ naive|timeuntil }}` into `''`.
- **The `expected`/`settings` rule.** A `settings` row without `expected` is a
  row that passes when the setting name is a typo, because both engines move
  together. Every row in the `expects_localtime` section carries one.
- **Language.** `date`'s default is resolved per `LANGUAGE_CODE`, so the row
  `{{ d|date }}` under `de` is `'8. Oktober 2026'` where `en-us` gives
  `'Oct. 8, 2026'`, and `date:"SHORT_DATE_FORMAT"` under `de` is `'08.10.2026'`.
  The corpus runs in `en-us`; a `settings` row with `LANGUAGE_CODE` is available
  if you want the `de` row, and it needs `expected`.

## Definition of done

1. Four bodies with module docs rewritten: the delegating reasoning, the
   measured tables above (trimmed to your rows), the catch-lists, and — for
   `date`/`time` — why the second branch is a second seam method.
2. `#[cfg(test)] mod tests` per module in the landed pattern (`phone2numeric.rs`,
   `filesizeformat.rs`, `wordwrap.rs`): a `TestFilterHost` scripted per case,
   `assert_called`, one test per *decision*. For these four the decisions are:
   the sentinel guard is equality and not truthiness (`date`, `time`); the
   fallback is reached only on the caught class, and the class differs between
   `date` and `time`; the argument's truthiness is read before its text; the
   second branch calls the *raw* method and not `date_format`; `timesince`
   catches `(ValueError, TypeError)` and re-raises `AttributeError`; the `arg`
   guard is present in `timesince` and absent in `timeuntil`; `reversed` is a
   flag and not an operand swap.
3. `STILL_STUBBED` in `crates/template-core/src/filters/mod.rs` is empty, and
   the doc paragraph above it gains "and Task 10's four — `date`, `time`,
   `timesince`, `timeuntil`". The array's length is the count; no number in that
   prose.
4. `tests/test_filter_parity.py`: the stub-count literal and its comment reach
   **0**, and the running-subtraction comment's shape cannot survive that — say
   what the zero means (`M4's last group has landed`) instead of subtracting
   Task 10 from a list that is now empty.
5. `tests/filter_cases.py`: the `CASES` rows and the `ERROR_CASES` rows above,
   with the `value_factory`/`arg_variable` keys where the table says so.
6. The stub modules' `run:` lines and any `filters/mod.rs` dispatch that names
   them are consistent — `stub_error` matches the kind *and* the name.
7. `source scripts/env.sh && maturin develop --release` before any Python test.
8. `cargo fmt --all` **before** the first `./scripts/check.sh`, not after.
9. `./scripts/check.sh` green: `cargo test --workspace`, `rustfmt --check`,
   `clippy -D warnings`, the Python unit tests (corpus and
   `test_filter_inventory` included), the differential harness, the
   generated-file checks, and `Ran 1547 tests … OK (skipped=6)`.
10. One commit, with the group's identity in the message and the attribution
    line.

## What to report back

- The commit hash.
- The gate's last three lines.
- Anything in the pinned source that contradicts this brief, with the
  measurement.
- For each of the four: the one decision this brief did not specify, and how you
  measured it. `date`/`time`'s raw-method argument is settled below (`&Value`) —
  if you measured something that makes `&str` right, say so with the row; the
  `arg_variable` shapes are the candidate for the other two.
- The rows you wanted and could not express, and why.
- Whether any fifth seam change is needed — with the input that forces it, not
  an argument that it would be nicer.
