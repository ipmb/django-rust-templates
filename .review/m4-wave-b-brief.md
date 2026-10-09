# Task 9 wave B — `urlize`, `urlizetrunc`, `json_script`

**Derive everything against `.django/` at Django 6.0.9.** An unpinned Django also
exists on this machine; every claim below was measured on the pinned checkout, and
a claim you cannot re-measure there is one you should stop and report.

This brief is the work order for the three filter bodies Task 9 has left. It is
the first brief in the milestone written from a §D2 that has all three of its
metrics in it — the delegating set is 21 filters, derived in
`tests/test_filter_inventory.py`, and all three of these are in it.

## Ground rules

- **One group, three bodies, one commit** (or two if you split `json_script` from
  the `urlize` pair — your call; the pair share a seam and a module doc, so keep
  them together if you split).
- **Rows land with the body, in the same commit.** §10.4 records why: the
  1547-test gate's only call into this layer is `_native.parse`'s
  `check_filter`, which sees a filter's name and arity and never its body, and
  `_materialise_filter_expression` rebuilds every filter with Django's own
  registry function. The corpus is the only gate that can see a filter body, so a
  body landing without rows is a body nothing tests.
- **A stub's module doc is a claim, and it is what this brief replaces.** Delete
  the "Stub. §10.3's M4 row owns this filter…" paragraph in each of the three
  modules as you write the body: the body/doc pair is what makes a stub a stub
  (see `STILL_STUBBED` in `filters/mod.rs`).
- **Do not edit the plan** (`docs/superpowers/plans/2026-10-08-m4-built-in-filters.md`),
  the design spec, or `.review/*`. The coordinator does those after the wave
  lands. Do not touch `.django/` — it is never modified.
- **Report a gap rather than papering over one.** If something here is wrong,
  or the pinned source says something this brief does not, say so in your report
  and leave the code in the state the source supports. Wave A did exactly this
  and it is what produced §D2's third metric.
- Attribution: end the commit message with
  `Co-Authored-By: Claude Code <noreply@anthropic.com>`.

## The seam change you inherit — already landed

`d74192e` moved `FilterHost::urlize`'s limit from `Option<usize>` to
`Option<&IntValue>`, because the corpus reaches two answers a `usize` cannot
spell. The signature is:

```rust
fn urlize(&self, text: &str, limit: Option<&IntValue>, autoescape: bool) -> String;
```

`urlize.rs` is the only file that read the old one. `PyFilterHost` now rebuilds
`Small`/`Big` through Python's own `int()` and passes `None` through as
`trim_url_limit=None`; both test hosts take `Option<&IntValue>` and ignore it.

**The `int()` is yours, in `urlizetrunc`'s body**, not the seam's — Django does
it at `defaultfilters.py:395` (`int(limit)`), before the helper call. So
`call.host.to_int(&arg.unwrap_or(Value::None))?` is where `int("abc")`'s
`ValueError` and `int(None)`'s `TypeError` come from, and the seam is handed an
int or `None` only.

## `urlize` — `defaultfilters.py:382-386`

```python
@register.filter(is_safe=True, needs_autoescape=True)
@stringfilter
def urlize(value, autoescape=True):
    """Convert URLs in plain text into clickable links."""
    return mark_safe(_urlize(value, nofollow=True, autoescape=autoescape))
```

`_urlize` is `django.utils.html.urlize` (`html.py:496-499`), a
`@keep_lazy_text`-decorated wrapper over the `Urlizer` instance — so the helper,
the regex, and `settings.URLIZE_ASSUME_HTTPS` are the host's, and the body is the
seam call plus `mark_safe`.

**The body's four decisions, in this order:**

1. **Any argument refuses.** The inner `def`'s second parameter is *named*
   `autoescape`, and Django's loop calls
   `func(obj, autoescape=context.autoescape, *arg_vals)` (`base.py:813`) — keyword
   before positional — so a positional argument binds to the same name and
   **CPython** raises. `args_check` (`base.py:822`) counts `autoescape` among the
   filter's parameters, so the *parse accepts* `{{ x|urlize:5 }}`; the refusal is
   at call time. It is a `TypeError` and not a `TemplateSyntaxError`, which is why
   an implementation that rejected the token while parsing would be wrong.

   This is the fifth of the five collision filters (`linebreaks`,
   `linebreaksbr`, `linenumbers`, `unordered_list`, this one) and its refusal is
   each **body's**, not the seam's. Copy the pattern from `linebreaks.rs:81-86`:
   a private `fn autoescape_collision() -> Error` holding the constant, with the
   operation and the message naming the **callable**. For this filter the
   registered name and the `def` name coincide, so it is `urlize` — measured:

   ```
   {{ x|urlize:5 }} -> TypeError: urlize() got multiple values for argument 'autoescape'
   ```

   The refusal does not consult the argument: `{{ x|urlize:None }}` collides too,
   so the test is the argument's *presence* (`arg.is_some()`), not its value.
   The `ERROR_CASES` preamble's paragraph on the five
   (`tests/filter_cases.py:3799-3840`) says `urlize`'s row is "not here yet …
   [it] lands with the body, under Task 9" — the row lands now, and that
   paragraph and the "four rows at the foot of this list" phrase move with it.

2. **`to_str` the value, then the host call with `limit = None`.** The
   `@stringfilter` coercion itself happens once, in `run_filter` — the body calls
   `call.host.to_str(&value)` exactly as the landed bodies do (`slugify.rs:99`,
   `linebreaksbr.rs`, `striptags.rs`). `urlize`'s own call site passes no limit
   (`:383`), and `nofollow=True` is the seam's constant.

3. **`mark_safe` the result.** The `def` body does it explicitly, and the corpus
   compares safety **separately from text** (`test_filter_parity.py`'s
   `assert_same`: "safety differs — the text is identical either way, so this is
   the comparison a rendered template cannot make"). Every measured row below is
   `safe=True`; a body that returned `Value::Str` would pass a value comparison
   and fail that one. `call.host.mark_safe(Value::Str(rendered.into()))`.

4. **Nothing else.** No escaping decision, no `nofollow`, no protocol choice, no
   `trim_url` — `autoescape` goes to the host as `call.autoescape`.

**Measured rows, through `FilterExpression.resolve` on the pinned tree.**
`URLIZE_ASSUME_HTTPS` unset unless said otherwise:

```text
"see https://example.com/x now"
  -> SafeString('see <a href="https://example.com/x" rel="nofollow">https://example.com/x</a> now')
"visit www.example.com please"
  -> SafeString('visit <a href="http://www.example.com" rel="nofollow">www.example.com</a> please')
     and emits RemovedInDjango2028Warning
"visit www.example.com please", URLIZE_ASSUME_HTTPS=True
  -> SafeString('visit <a href="https://www.example.com" rel="nofollow">www.example.com</a> please')
     and emits nothing
"mail me at a@b.com"
  -> SafeString('mail me at <a href="mailto:a@b.com">a@b.com</a>')     (a mailto has no rel)
"no links here"
  -> SafeString('no links here')
"<b>bold</b> https://e.com", autoescape on
  -> SafeString('&lt;b&gt;bold&lt;/b&gt; <a href="https://e.com" rel="nofollow">https://e.com</a>')
     (the surrounding text is escaped, the link is not)
mark_safe("<b>bold</b> https://e.com"), autoescape on
  -> SafeString('<b>bold</b> <a href="https://e.com" rel="nofollow">https://e.com</a>')
     (`safe_input` — a Safe input is not escaped)
"<b>bold</b> https://e.com", context autoescape off
  -> SafeString('<b>bold</b> <a href="https://e.com" rel="nofollow">https://e.com</a>')
{{ x|urlize:5 }}  -> TypeError: urlize() got multiple values for argument 'autoescape'
```

**The `RemovedInDjango2028Warning` is real and the corpus cannot see it.** The
host emits it because the host makes the call; Python warnings are not part of
any answer `_native.filter` returns, so no row can assert it. What the rows *can*
pin is its effect — `http://` versus `https://` under the two settings states —
and the module doc should say which half is pinned and which half is only
delegated. Two rows under `override_settings` (`URLIZE_ASSUME_HTTPS` False and
True) are the shape; `tests/filter_cases.py`'s `settings` key is how a row does
it. Do not write a row whose only content is the warning.

## `urlizetrunc` — `defaultfilters.py:389-400`

```python
@register.filter(is_safe=True, needs_autoescape=True)
@stringfilter
def urlizetrunc(value, limit, autoescape=True):
    return mark_safe(
        _urlize(value, trim_url_limit=int(limit), nofollow=True, autoescape=autoescape)
    )
```

**No collision, and this is the contrast the `ERROR_CASES` preamble draws:**
`limit` comes *before* `autoescape`, so a positional argument binds to `limit`
and nothing collides. This filter is one of the two three-positional-parameter
filters in the set (`join` is the other).

The body is: the `int()` first and with no `try` (the `wordwrap` pattern —
`to_int(&arg.unwrap_or(Value::None))?`, so an absent argument reaches the
conversion rather than panicking; the arity check makes that input unreachable
from a template, but `implemented_filters()` in `test_filter_parity.py` calls
**every** filter with `_native.filter(name, None, ())` to measure the stub set,
so a body that panicked on the absent argument would break the gate), then
`to_str`, then `call.host.urlize(&text, Some(&limit), call.autoescape)`, then
`mark_safe`.

**Measured rows, on `"see https://example.com/very/long/path now"`:**
`URL` below is that string; every answer is a `SafeString` and wraps the link as
`<a href="https://example.com/very/long/path" rel="nofollow">…</a>`:

```text
{{ x|urlizetrunc:5 }}        -> 'see <a href="…" rel="nofollow">http…</a> now'
{{ x|urlizetrunc:"5" }}      -> the same answer as :5 — `int("5")` succeeds
{{ x|urlizetrunc:n }}, n=5   -> the same answer again (a variable argument)
{{ x|urlizetrunc:0 }}        -> '…'          (x[:max(0,-1)] is empty)
{{ x|urlizetrunc:-1 }}       -> '…'          <- the row the `usize` seam could not carry
{{ x|urlizetrunc:-5 }}       -> '…'
{{ x|urlizetrunc:1 }}        -> '…'
{{ x|urlizetrunc:3 }}        -> 'ht…'
{{ x|urlizetrunc:1000 }}     -> untruncated: the whole URL, no ellipsis
{{ x|urlizetrunc:<10**29> }} -> untruncated (`len(x) <= limit` is True for any length)
{{ x|urlizetrunc:None }}     -> TypeError: int() argument must be a string, a bytes-like object
                                or a real number, not 'NoneType'
{{ x|urlizetrunc:"abc" }}    -> ValueError: invalid literal for int() with base 10: 'abc'
```

`{{ x|urlizetrunc }}` (bare) is a `TemplateSyntaxError: urlizetrunc requires 2
arguments, 1 provided` **at parse time**, from the host's `check_filter` reading
Django's own arity — it needs no code here and no `ERROR_CASES` row (those rows
run through `_native.filter`, which is handed values rather than template text).
Confirm it is already green rather than adding anything for it.

**`trim_url` is the host's and its shape is why the seam changed:**
`html.py:412-415` is `if limit is None or len(x) <= limit: return x` then
`"%s…" % x[: max(0, limit - 1)]` — the limit is compared *and sliced*, which is
why `-1` is `'…'` and a 30-digit literal truncates nothing, and why a `usize`
signature was a divergence waiting for a user to write `{{ x|urlizetrunc:-1 }}`.

## `json_script` — `defaultfilters.py:85-91`

```python
@register.filter(is_safe=True)
def json_script(value, element_id=None):
    return _json_script(value, element_id)
```

`_json_script` is `django.utils.html.json_script` (`html.py:95-112`), and
**Rust owns its assembly** — separating the dump from the assembly is what the
probe behind `FilterHost::json_dumps`' doc sentence did ("The escaping and the
`<script>` wrapping are the caller's"):

```python
_json_script_escapes = {ord(">"): "\\u003E", ord("<"): "\\u003C", ord("&"): "\\u0026"}

json_str = json.dumps(value, cls=encoder or DjangoJSONEncoder).translate(_json_script_escapes)
if element_id:
    template = '<script id="{}" type="application/json">{}</script>'
    args = (element_id, mark_safe(json_str))
else:
    template = '<script type="application/json">{}</script>'
    args = (mark_safe(json_str),)
return format_html(template, *args)
```

So the four pieces, and **three of them are the body's**:

- `call.host.json_dumps(&value)?` returns the raw `json.dumps` text — not
  escaped, not wrapped.
- **The three escapes and the `.translate` are Rust's**, one constant map.
- **The template choice is Rust's**, on `element_id`'s Python truth — the trait
  has `is_true` (`host.rs:207`, `bool(v)`, `__bool__`/`__len__`) for exactly this
  question, and it is `if element_id:`, not `if element_id is not None:`.
- **The `id`'s escaping is the host's**, one call: `conditional_escape`
  (`host.rs:280`, §D4 — it has three branches and two of them are not `is_safe`
  questions, `isinstance(text, Promise)` and `hasattr(text, "__html__")`). There
  is **no `format_html` method on the trait**, and you do not need one: the
  source's `format_html(template, *args)` is `template.format(*[conditional_escape(a)
  for a in args])` wrapped in `mark_safe`, with `mark_safe(json_str)` already
  applied to the JSON argument — so `conditional_escape(element_id)` is the
  whole host half and the `format!` is Rust's. If you conclude some other
  operation is needed, **stop and report** — `host.rs` is settled before the
  fan-out (§10.4) and a body does not add to it.

**Where the `id`'s escaping comes from — the row that took a measurement to
understand.** A filter argument that is a *template constant* is `mark_safe`d by
`resolve` before the body sees it (`base.py:800-806`), a **bare variable** is not,
and `conditional_escape` respects `SafeData`. Measured:

```text
{{ x|json_script:"a<b" }}  with x = 1   -> '<script id="a<b" type="application/json">1</script>'
                                            (the constant arrives marked, so NOT escaped)
{{ x|json_script:i }}, i = "a<b"        -> '<script id="a&lt;b" type="application/json">1</script>'
                                            (a variable arrives bare, so escaped)
{{ x|json_script:i }}, i = mark_safe("a<b")
                                        -> '<script id="a<b" type="application/json">1</script>'
{{ x|json_script:7 }}      with x = 1   -> '<script id="7" type="application/json">1</script>'
                                            (a non-str id is `str()`ed by the format)
{{ x|json_script:"" }}     with x = 1   -> '<script type="application/json">1</script>'
                                            (falsy `element_id` drops the attribute)
{{ x|json_script:0 }}      with x = 1   -> '<script type="application/json">1</script>'
```

The corpus can express all six: `"a<b"` is a quoted constant (the row's `arg`
key), and the two variable shapes go through `arg_variable` with a `context`.
**The third of those — a variable holding `mark_safe("a<b")` — is the row that
distinguishes `conditional_escape` from `escape`,** and it is worth carrying for
that reason alone; note in the row's comment that a `str` argument is the only
shape `filter_cases.py` renders as a quoted constant, so the *unmarked* string
case has to arrive as a variable.

**Measured body rows:**

```text
{{ x|json_script }}, x = {"a": 1}     -> '<script type="application/json">{"a": 1}</script>'
                                         (the space after the colon is `json.dumps`' default
                                          separator, and it is a row because an earlier
                                          revision of the seam got it wrong — `ece8a3b`)
{{ x|json_script:"i" }}, x = [1, 2]   -> '<script id="i" type="application/json">[1, 2]</script>'
{{ x|json_script }}, x = {"a": "<b>&c"}
                                      -> '<script type="application/json">{"a": "<b>&c"}</script>'
                                         (the three `_json_script_escapes` entries, all three in
                                          one row; the JSON quotes are NOT escaped)
{{ x|json_script }}, x = {"a": 'he said "hi"'}
                                      -> '…{"a": "he said \"hi\""}…'   (`json.dumps`' own quoting)
{{ x|json_script }}, x = {"d": date(2026, 10, 8)}
                                      -> '…{"d": "2026-10-08"}…'
{{ x|json_script }}, x = Decimal("1.50")
                                      -> '…"1.50"…'      (a string, and the trailing zero survives)
{{ x|json_script }}, x = UUID(int=0)  -> '…"00000000-0000-0000-0000-000000000000"…'
{{ x|json_script }}, x = None         -> '…null…'
{{ x|json_script }}, x = {"k": "é"}   -> '…{"k": "é"}…'  (ensure_ascii is `json.dumps`' default)
{{ x|json_script }}, x = {1, 2}       -> TypeError: Object of type set is not JSON serializable
```

Every answer is a `SafeString`; `is_safe` is `True` and the def body does **not**
call `mark_safe` itself, so check whether the loop's marking
(`run_filter`: `is_safe && (was_safe || coerced_is_safe)`) is what produces it —
for a bare `str`/`dict` input neither flag fires, so the body's own
`call.host.mark_safe(...)` is what the corpus's safety comparison will be
looking at. Measure it rather than assuming: `format_html` returns a
`SafeString` already, so the honest question is whether the host's format method
returns a `Value::Safe` and whether a second `mark_safe` is a no-op. **Whichever
way it lands, the row's `safe=` is what pins it.**

The date/Decimal/UUID/`set` rows are the ones that hold `json_dumps`' `cls=
DjangoJSONEncoder` — the defect `ece8a3b` found and no gate could see, because
`json_script` was a stub and that method had no other caller. **They are the
reason this filter's rows matter more than its body's size suggests.**

## Traps this wave has that wave A's brief did not

- **Three rows the corpus cannot express, and two it can only express through
  `settings`.** `URLIZE_ASSUME_HTTPS` and the warning (above). Say in the module
  doc which is which rather than implying the rows cover the warning.
- **`args_check` versus the corpus.** The collision refusal is at call time, so
  the row asserts `TypeError` and **not** `TemplateSyntaxError`; the bare
  `urlizetrunc` is a `TemplateSyntaxError` and has no row. If either feels
  backwards, the measurement above is the tiebreak.
- **A `Big` limit is not an overflow.** `IntValue::Big(digits)` for
  `urlizetrunc` reaches the host as a Python int of full width, so nothing in
  Rust decides "too big" — the same reason `wordwrap.rs:76-79` can only say
  "longer than any line" for a positive `Big`.
- **`Value::Opaque` has no class name.** Nothing in this wave needs it, but if a
  row's expected error message names a Python class, check whether the value
  reaching the body was interned as `Opaque` — the message then says `'object'`.

## Definition of done

1. Three bodies with their module docs rewritten: the delegating reasoning, the
   measured row tables, and the refusals (for `urlize`, the collision; for
   `urlizetrunc`, the `int()` order; for `json_script`, the three escapes and the
   two templates).
2. `#[cfg(test)] mod tests` per module in the landed pattern —
   `phone2numeric.rs:104-198` and `slugify.rs`/`wordwrap.rs` are the models: a
   `TestFilterHost` scripted per case, `assert_called`, and one test per
   *decision* rather than per input. For these three the decisions are: the
   argument is refused / not consulted (urlize); the `int()` runs before the text
   is read, and a `Big`/negative limit reaches the seam as it is (urlizetrunc);
   each escape entry and each template branch, and that the escaping is the
   host's rather than reimplemented (json_script).
3. `STILL_STUBBED` in `crates/template-core/src/filters/mod.rs` drops to four
   (`date`, `time`, `timesince`, `timeuntil`) and the doc paragraph above it
   gains "Task 9 wave B's three — `json_script`, `urlize`, `urlizetrunc`". The
   array's length is the count; do not put a number in that prose.
4. `tests/test_filter_parity.py`'s stub-count comment and its literal `7` move to
   4, in the same style — subtracting Task 9's wave B of three from the running
   subtraction, so the comment still reads as one arithmetic statement.
5. `tests/filter_cases.py`: the `CASES` rows above, the `urlize/argument`
   `ERROR_CASES` row, and the preamble paragraph at `:3837-3840` retracted (the
   row it promised is now above it) with "the four rows at the foot of this list"
   at `:3800` moving to five.
6. `source scripts/env.sh && maturin develop --release` before any Python test —
   the bodies are Rust.
7. `./scripts/check.sh` green: `cargo test --workspace`, `rustfmt --check`,
   `clippy -D warnings`, the Python unit tests (which include the corpus and
   `test_filter_inventory`), the differential harness, the generated-file checks,
   and `Ran 1547 tests … OK (skipped=6)`.

   Run `cargo fmt --all` before the first `check.sh`, not after — a rustfmt diff
   in the gate output is the one failure that costs a whole re-run.
8. Commit, with the wave's identity in the message and the attribution line.

## What to report back

- The commit hash(es).
- The gate's last three lines.
- Anything in the pinned source that contradicts this brief, with the
  measurement.
- For each of the three: the one decision you made that this brief did not
  specify (there will be at least one — the `mark_safe` question for
  `json_script` is the obvious candidate), and how you measured it.
- The rows you wanted and could not express, and why.
