# M4 — the nine uncalled seam methods, re-derived

Forcing event: the `json_dumps` defect (`ece8a3b`). Its method had no caller
in any filter body, so no gate — not `cargo test`, not the corpus, not the
1547-test suite — could see it, while its doc comment read as true. The
reviewer enumerated the eight others in the same position and asked for one
pass against the pinned source rather than eight more chances to carry a
wrong claim into a task brief.

The set, as it stood: `date_format json_dumps localtime now pformat slugify
time_format timesince urlize`.

Derived against `.django/` @ 6.0.9. Every verdict below is a number, not a
reading.

## Verdicts

| seam | verdict |
|---|---|
| `json_dumps` | **defect, fixed** `ece8a3b` |
| `date_format` | **defect**: `&str` cannot express Django's `None`; the substitute diverges |
| `time_format` | **defect**, same shape |
| `now` | **defect, and the method is deleted** — see below: the doc named a caller, and following it reddens |
| `localtime` | call clean, **doc wrong** |
| `slugify` | clean |
| `urlize` | clean |
| `pformat` | clean |
| `timesince` | call clean, **doc wrong** — "Django's own signature" is five parameters, the trait expresses three |

**These verdicts are dated, not permanent.** A disposition recorded against one
shape of a method expires when the method changes shape. `timesince`'s
"clean" was first written against a `-> String` seam that carried a separate
`now()` method; the signature is now `-> Result<String, Error>` with
`now: Option<&Value>` and `now()` deleted, and re-deriving it against the
current shape is what turned up the doc defect in the row above. A verdict is a
number only for as long as the thing it numbered holds still.

## `date_format` / `time_format` — the sentinel the signature cannot spell

`formats.date_format(value, format=None, …)` is
`dateformat.format(value, get_format(format or "DATE_FORMAT", …))`
(`formats.py:155-157`). Two consequences the trait's `format: &str` obscures:
`None` and `"DATE_FORMAT"` are the *same call*, and **`settings.DATE_FORMAT`
is not either of them**.

`LANGUAGE_CODE="de"`, `d = date(2026, 10, 8)`:

```
settings.DATE_FORMAT         = 'N j, Y'
get_format("DATE_FORMAT")    = 'j. F Y'            ← the de format module
date_format(d, None)         = '8. Oktober 2026'
date_format(d, "DATE_FORMAT")= '8. Oktober 2026'
date_format(d, settings.DATE_FORMAT) = 'Okt. 8, 2026'    ← diverges
{{ d|date }}                 = '8. Oktober 2026'
```

A body that resolves the default itself is right in `en-us` and wrong in `de`.
The trait doc says only "the active language's formats" — the half a reader
checks is true, which is the `json_dumps` shape exactly.

## `now` — a doc that names a caller, where following it reddens

The doc: "`django.utils.timezone.now`, for `timeuntil`'s default argument."

`timeuntil_filter` → `timeuntil(value, arg)` → `timesince(d, now,
reversed=True)`, and `timesince` fills `now` **internally** (`timesince.py:67-68`)
as `datetime.datetime.now(d.tzinfo if is_aware(d) else None)`. A caller that
supplies `host.now()` instead takes the `elif is_aware(now) and is_aware(d)`
branch:

```
timesince(naive, None,           reversed=True) = '2 hours, 59 minutes'
timesince(naive, timezone.now(), reversed=True) = RAISES TypeError:
    can't subtract offset-naive and offset-aware datetimes
{{ naive_dt|timeuntil }}                        = '2 hours, 59 minutes'
```

`timeuntil_filter` catches `(ValueError, TypeError)` and returns `''`, so the
doc's own instruction turns a real answer into an empty string. The filter
layer needs no clock at all.

The one caller that does is `NowNode.render` (`defaulttags.py:404`), and it
needs `datetime.now(tz=get_current_timezone() if settings.USE_TZ else None)` —
**not** `timezone.now()`. `TIME_ZONE="Europe/Paris"`, `USE_TZ=True`:

```
{% now "H:i:s" %}                     = 07:28:58
time_format(timezone.now(), "H:i:s")  = 05:28:58
```

**Disposition: the method is deleted, not carried.** The reviewer's argument is
the one that settles it — this is the third `repr`. `repr` and
`unicode_normalize_nfkd` were both removed for having no caller in a §D7-frozen
trait, and `now`'s only *stated* caller turned out to be one that must not use
it. Its real caller is `{% now %}`, which belongs to a tag host that does not
exist yet; carrying it into M5 would mean M4 shipping a method whose whole
documentation is a caller that measurement refuted. The measurement above is
what M5 needs and it is recorded here, so M5 writes `now` against the tag's own
requirement rather than inheriting a wrapper of `timezone.now()` that answers
five timezones' worth of wrong. `host.rs` drops from 46 methods to 45; the
`Result` half of the trait in `TestFilterHost`'s refusal test drops 25 → 24.

## `localtime` — clean call, wrong doc

The engine calls `template_localtime(obj, context.use_tz)` (`base.py:811`),
not `settings.USE_TZ`: `{% localtime off %}` flips `context.use_tz`, which is
that tag's whole purpose. The doc's "this is `settings.USE_TZ`" is the claim a
Task 10 brief would carry.

## The clean four

```
django.utils.text.slugify("Straße Ünicode")  = 'strae-unicode'
{{ "Straße Ünicode"|slugify }}               = 'strae-unicode'
```

`urlize`: the seam's `trim_url_limit`/`nofollow=True`/`autoescape` reproduce
both call sites, `urlize` and `urlizetrunc`, and `int(limit)` is
corpus-reachable — `{{ s|urlizetrunc:"5" }}` == `{{ s|urlizetrunc:5 }}`.
`pformat(value)` takes `pprint`'s defaults on both sides.

### `timesince`, re-derived against the current signature

Not "the same call" — the verdict has to be re-derived, because the seam it was
recorded against no longer exists. Django's helper is
`timesince(d, now=None, reversed=False, time_strings=None, depth=2)`
(`timesince.py:28`): **five** parameters. The trait expresses **three** —
`FilterHost::timesince` is `(d, now: Option<&Value>, reversed: bool)`. (No
`host.rs` line number: an earlier draft of this paragraph cited `host.rs:678`
and the doc edit two rows up moved it to `692` in the same breath, which is the
staleness this file exists to catch. Cite the symbol.)

The call is clean, and the clean-ness is bounded by the call site rather than
by the signature. `time_strings` and `depth` are absent because the helper has
exactly two Rust callers, and the argument triple is visible in each call:
`timesince.rs`'s `call.host.timesince(&value, now, false)` and `timeuntil.rs`'s
`call.host.timesince(&value, arg.as_ref(), true)` — three arguments, always.
The template side reaches the helper only through `timesince_filter`
(`defaultfilters.py:812`) and `timeuntil_filter` (`:825`), and neither sets the
last two. So the reachable parameter set is complete and the omission is the
`ngettext`-`u64` shape: sound for the callers that exist, a thing to widen
before a caller that wants a custom `depth`. The `now: Option` spelling is the
repair this file already records (sweep instance #4 below), and `Result` is
*forced*, not chosen: `AttributeError` escapes the filter's
`except (ValueError, TypeError)` and only the host sees the raised class.

**The doc was not clean.** It read "`django.utils.timesince.timesince`, **with
Django's own signature**" — a claim a reader checks against `timesince.py:28`
and finds false by two parameters, and the exact claim a Task brief would
carry. Fixed in the doc to name the reachable parameter set and why the other
two are absent. The call was never wrong; the sentence describing it was, which
is `localtime`'s shape, not `date_format`'s.

## The rule, and the sweep it predicts

The reviewer's framing, which is better than mine: these are all one shape —
**the seam reproduced something Django does internally instead of passing it
through.** Five instances now, in order of discovery:

| seam | what it reproduced | how it failed |
|---|---|---|
| `json_dumps` | `separators`, and omitted `cls` | wrong output; wrong failures |
| `percent_format` | `"%" + spec` | could not express `gettext("%s KB") % x` |
| `quote` | the `str`/non-`str` classification | refused inputs Django answers |
| `timesince` | the `now=None` default | raises where Django answers — **repaired**: `now` is `Option<&Value>`, `now()` deleted |
| `date_format` | the default's resolution | wrong in any locale with a format module |

The predictive statement: **a seam's signature should be the wrapped function's
signature, defaults and sentinels included.** Wherever the Rust side computes
what Python would have computed, that is where the divergence lives.

So the sweep is mechanical — for each method, is its signature narrower than
the callable its doc cites? Done over all 40 in the trait:

**Narrow, and wrong: exactly two, `date_format` and `time_format`.** `now` is a
third defect of a different shape: its signature is not narrow (it has no
parameters at all), but its doc names a caller that must not use it, and the
caller that needs a clock needs a different function.

**Looks narrow, is not — the five worth writing down, because each is the
question being asked and answering it took a measurement:**

- `to_float(&Value) -> f64`. Python's `float` is unbounded in its input and
  raises `OverflowError` above `1.8e308`; an `f64` return looks like the
  reproduction. It is not, because the method calls the `float` **type**
  (`filters.rs:335-347`) rather than parsing, so a huge int raises instead of
  answering `inf`, and the doc's own `filesizeformat(10**400)` rows are what
  pin it. The `Value::Int` short-circuit above it **agrees with CPython's
  rounding** rather than being lossless — `i64 as f64` loses precision above
  2^53 exactly as `float(int)` does, which is the claim that survives someone
  testing `float(2**53 + 1)`.
- **The reason this one is the most valuable false positive:** it is the method
  that looks most like the reproduction pattern, and what makes it safe is a
  fix from an earlier wave — calling the `float` type instead of extracting a
  pyo3 `f64`. So the wave-2 fix is what protects this one, and the doc's
  `filesizeformat(10**400)` table is where that is recorded. Two findings
  closing on each other across waves is the evidence that the rule is
  structural rather than a list of incidents.
- `ngettext(…, n: u64)`. Python's `n` is an arbitrary-precision int. Both call
  sites are in `filesizeformat` (`:908`, `:925`) and the only non-literal one
  is the `bytes_ < KB` branch, so `n < 1024`; the other is a literal `0`.
  Bounded, but by the call site rather than by the signature — worth the one
  line in the doc that says which bound holds it.
- `get_index(…, index: i64)`. Python's subscript takes any int. Both callers
  are `first`/`last` with a literal `0`/`-1` (`:602-608`, `:624-630`).
- `comma_bits(…, prepend_comma: bool)`. Not a wrapped callable at all — the
  flag *is* Django's own call-site difference: `pluralize` opens with
  `if "," not in arg: arg = "," + arg` (`:965-966`) and `yesno` does not
  (`:879`). Passing the difference is mirroring the source's call sites, not
  reproducing its internals.
- `int_divide_by_power_of_two(&IntValue, …)` and `decimal_quantize(…)`. These
  take a converted operand where Python's statement takes a `Value`, which is
  the narrow direction — and it is deliberate and argued: both sit at the
  source's own statement boundary, so a `&Value` would make the body convert,
  then the method convert again, which is two `int()` calls for an object whose
  `__int__` has side effects.

**One inconsistency found that is not a defect, and the fix the reviewer chose
was to the claim rather than the string:** `index_of`'s synthesized
`IndexError` reads `list index out of range (5)` where CPython's is `list index
out of range` (`template-py/src/filters.rs:401-403`). Both callers catch
`IndexError` and answer `""`, so nothing can observe it — and the comment
claimed the message was Python's too. Correcting the *claim* costs one line and
leaves the more useful index in place: a doc that reads as true is what misleads
the next reader, and a third caller routing through `get_index` without catching
`IndexError` would inherit a synthetic message it believed was CPython's. My
first instinct was to strip the index from the message, which would have fixed
the symptom and left the claim standing.

**One more note for whoever writes the collision rows:**
`phone2numeric`'s callable is `phone2numeric_filter` while its registered name
is `phone2numeric` — the same registered-name/callable-name split as
`linebreaks`, and the second instance of it. It is harmless here because
`phone2numeric` has no `needs_autoescape`, so no synthesized message carries a
callable name; `linebreaks` is where that split bit.

**One recorded suspicion checked and refuted:** an earlier debugging note
suggested `as_nested_sequence` might omit `types.GeneratorType` from its
`isinstance` triple. It does not — `filters.rs:792-794` tests all three, and
the trait doc at `host.rs:325-328` names the triple against the pinned source.

## The `now` defect has a deterministic row, which is not obvious

`timesince`/`timeuntil` with no argument are clock-dependent, so the corpus
cannot normally carry them. One shape is an exception, and it is the shape that
catches this defect. `timesince` stops at `if since <= 0: return
avoid_wrapping(gettext("0 minutes"))`, so **any future `d` answers
`'0\xa0minutes'` forever** — a constant, not a clock reading. Under
`USE_TZ=True` with a *naive* `d`, the misuse the trait doc invites is a
`TypeError` that the filter swallows:

```
USE_TZ=True, d = naive datetime.now() + 3650 days
{{ d|timesince }}                 = '0\xa0minutes'
timesince(d, timezone.now(), False) = TypeError: can't subtract
                                      offset-naive and offset-aware datetimes
                                 -> caught by the filter -> ''
```

So one row with `settings: {"USE_TZ": True}`, a `value_factory` building a
far-future naive datetime, and `expected` pinned to `"0\xa0minutes"` reddens a
body that supplies its own clock — and stays true however long the project
runs. The past-dated variant (`'9 years, 11 months'`) is *not* safe to pin: the
literal moves as the wall clock crosses a month boundary.

Also measured for Task 10's rows: `{{ "x"|date }}` and `{{ 5|date }}` are both
`''`, `{{ v|date:"DATE_FORMAT" }}` is `'Oct. 8, 2026'` in `en-us`, and
`{{ v|date }}` under `LANGUAGE_CODE="de"` is `'8. Oktober 2026'` — the last
being the only shape that can pin the sentinel, and it works through the
corpus's own `override_settings`, which fires Django's `LANGUAGE_CODE`
receiver and re-activates the language.

## Sixth instance, and the first one an agent found

Wave A's `phone2numeric.rs` reported a gap rather than papering over it, which
is the §D7 contract working as written. Re-measured on this machine:

```
phone2numeric(mark_safe("800-COLLECT")) = '800-2655328'    (plain str, not SafeString)
phone2numeric(Promise)                  = '222'            (__proxy__ — laziness preserved)
phone2numeric({'a': 1})                 -> AttributeError: 'dict' object has no attribute 'lower'
{{ v|phone2numeric }}, v = "ΟΣ"         -> (SafeString, 'ος')
```

**A `Markup` row stood here, and it was not measured on this machine — so it has
been removed rather than re-derived.** `markupsafe` arrives only with Jinja2,
which §2 excludes, so the input is unreachable; and the mechanism it claimed was
wrong as well. `@keep_lazy_text` marks nothing for a non-`Promise`, and the
engine's own marking at `base.py:816` requires `isinstance(obj, SafeData)`, which
`Markup` is not **by construction** — that is precisely why `conditional_escape`
had to become a host method (`__html__` without `SafeData`). It was
load-bearing in the argument for the fix, which is the same defect as the
`json_dumps` kwarg: a claim that read as true and had never been run. Second
instance, and this one written by the coordinator rather than by a brief.

Django's helper is `@keep_lazy_text`-decorated (`text.py:313`), so the
decorated function is not the `def` body: a lazy input yields a lazy result.
Separately `Value::Opaque` cannot name its Python class, so a `dict`/`tuple`
input gets `'object' object has no attribute 'lower'` where Django says
`'dict'`.

**Ruling: (a) — delegate the helper whole.** A host method calls
`django.utils.text.phone2numeric` on the `&Value`; wave A's 26-entry table and
its synthesised `AttributeError` are deleted, because a table that was never
reachable is a cheaper loss than a seam that was never right. The two legs that
carry the case are the *reachable* ones — the `Opaque` class name, and the lazy
`__proxy__` result, which the corpus sees as a type difference even though the
rendered text matches — not the `Markup` one. (b) is structurally incomplete
rather than merely partial: `@keep_lazy_text` changes the *result*, which no
argument-level operation can reach.

**And the class is bounded at one — swept, and the first sweep of it was wrong in
both its count and its mechanism.** The claim was: fourteen helpers in
`django/utils` carry `@keep_lazy`/`@keep_lazy_text`, and exactly one filter
reaches one without `@stringfilter` in between, because "every other such filter
has `str(first)` run first". Re-measured against `.django/` @ 6.0.9:

```
lazy-decorated helpers in django/utils/*.py          15
of those, referenced by a filter body                10
(filter, helper) pairs reaching one                  23
pairs in bodies with no @stringfilter                 5
   floatformat      -> mark_safe
   join             -> mark_safe
   phone2numeric    -> phone2numeric
   safeseq          -> mark_safe
   unordered_list   -> mark_safe
```

So the cut is not `@stringfilter` alone — that leaves five. Four of those five
are safe for a different reason: `mark_safe` is a **host method**
(`template-py/src/filters.rs:69-71`) calling Django's own decorated function, so
`keep_lazy` is *inherited* rather than reproduced. That is what makes the class
one, and it is a better rule than the one it replaces: **a body that calls the
host's method for a helper gets whatever sits between the registry and the `def`;
a body that owns the helper's algorithm owns the wrapper too.** It also makes the
retraction of the plan's purity bullet *"delegate one more helper"* rather than
*"the classification was wrong"*, and `tests/test_filter_inventory.py` now
derives the five and asserts a verdict per candidate, so the bound is checked
rather than asserted in prose.

## The third clause the §D2 metric was missing

The classification metric measured two axes: references into `django.utils`, and
string methods whose Rust and CPython answers diverge. `phone2numeric` scored
clean on **both** — it references nothing stateful and calls no divergent string
method — and was still not pure. A result-changing decorator is a third axis,
orthogonal to the other two, and it belongs in the rule rather than in this one
bullet, so that the next derivation asks the question instead of rediscovering
it:

> a filter's body must **call** the host for a helper whose `def` carries a
> result-changing decorator, rather than reproduce it — a body that owns the
> algorithm owns the wrapper's behaviour too.

**The generalisation this extends, which is the part worth keeping, and which
belongs in §D2 rather than here: the wrapped function is the *decorated* one,
not the `def` body.** `@stringfilter` and `@keep_lazy_text` both sit between the
registry and the code, so a seam derived from the `def` line is right about the
arguments and wrong about the wrapper — and `@stringfilter` is already the reason
`cut`'s argument takes a `&Value`.

## The finding this is an instance of

An uncalled seam method is **unverified code behind a verified doc comment**.
The doc is the artifact a brief is written from, so the derivation has to be
against the pinned source, not against the doc or the body — and timing it
per-task does not help, because the wrong doc is what misleads the task's
author. Base rate over the nine: three defects and one wrong doc claim.
