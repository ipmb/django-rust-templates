# M8 — escaping and the Python object protocol

Forcing event: **there isn't one yet.** M8 differs from M6 and M7 in that it was
not opened by a divergence found in the wild. It was opened by the source
itself — `CallArm` is a three-way enum in `crates/template-core/src/parser/resolve.rs`,
M3 implemented one arm and wrote the other two down as M8's:

```rust
Some(CallArm::Call) => return LookupOutcome::Unimplemented(
    "calling a resolved callable is M8's".to_string()),
Some(CallArm::AltersData) => return LookupOutcome::Unimplemented(
    "the alters_data substitution is M8's".to_string()),
```

(`resolve.rs:243` and `:253`.) So M8 is the first milestone whose scope is
**named in a `todo` that a prior milestone left deliberately**, and the question
this recon has to answer before a plan is written is not "what is broken" but
**"is the thing M8 will change observable from anywhere a test can stand?"**

Measured answer: **no, not today.** That is the headline, and it is a vacuity
result rather than a defect report.

Every number below comes from `.review/m8-measurements/`, run 2026-10-09 against
`.django/` @ 6.0.9. Each script bootstraps its own `sys.path` because `-I`
implies `-E`; each is invoked as
`.venv/bin/python -I .review/m8-measurements/<name>.py`.

## The rule this recon is read under

M5's rule, unchanged: **a verdict here is dated.** It is a number only for as
long as the thing it numbered holds still.

M7's addition, and the one that does the work here: **the vacuity rule** (spec
§10.3). A gate naming a `.django/tests/` module is a gate only for the milestone
that *interposes* the behaviour it exercises. The rule decides only
forward-reference vacuity; a row naming no `.django` module is out of scope, and
**the rule does not decide whether a gate's instrument can see the defect.**
M8 is the first milestone where that unresolved half is the whole story, and
§10.3 says so in as many words.

## 1. The surface, measured

`_native` exposes 26 public names (`m8_measure_baseline.py`). Of the six the
spec's seam names, **one exists**:

```text
  spec §5.2 `resolve`: True
  spec §5.2 `force_promise`: False
  spec §5.2 `is_safe`:      False
  spec §5.2 `is_callable`:  False
  spec §5.2 `call`:         False
  spec §5.2 `render`:       False
```

**That table is real and it is not evidence of a missing seam** — see §5 below,
where the reading it invites is measured and found wrong. The binding is not the
trait, and §5.2's names are Rust names.

What the resolver does today, per value (`m8_measure_baseline.py` §2):

```text
  plain str                  -> str 'hello & bye'
  safe str                   -> SafeString 'hello & bye'
  lazy gettext               -> __proxy__ 'lazy & value'
  bound method               -> NotImplementedError: calling a resolved callable is M8's
  obj.alters_data=class-attr -> AltersDataClassAttr 'obj'
  obj.alters_data=fn-attr    -> AltersDataFnAttr 'obj'
  obj.do_not_call=True       -> DoNotCall 'dontcall'
```

Two rows to read carefully. `lazy gettext` returns a **`__proxy__`**, not a
`str`: the Promise is not forced, it is merely printed through `str()` by the
reporting line — the distinction the first draft of this script lost by
printing `str(got)` (`Promise` forcing is an M8 obligation, and a measurement
that forces it in the *printer* cannot see it). And `bound method` raises, which
is M3's deliberate stub.

## 2. The corpus is green, and the corpus is blind

88 tests across the ten `.django` modules that name M8's surface
(`m8_measure_corpus.py`):

```text
  modules: 10
  tests  : 88 run, 0 failures, 0 errors, 0 skipped
```

They pass **while our own resolver raises on every path that reaches a
callable**:

```text
  {{ x }} with a plain lambda   stock='CALLED'  ours='CALLED'
  -> the Call arm is not reached (both engines agree) through a render
  our own entry point, same value:
    _native.resolve('x', ...) -> NotImplementedError: calling a resolved callable is M8's
```

The mechanism is the one recorded at M6 and again at M7: **the render path is
Django's.** `render_string` → `parse_template` → `_native.compile_template` →
Django's `Template` → Django's `NodeList.render` → Django's `VariableNode.render`
→ Django's `FilterExpression.resolve` → Django's `render_value_in_context`. Every
bit of resolution the rows exercise is executed by `base.py:948`, not by
`resolve.rs`. So the ten modules are not a gate for M8 in the way §10.3's rule
describes — they are a gate that **cannot see M8's defect while it is a
non-interposed stub**.

**This is the answer to the question M7's scope paragraph refused to decide.**
It is worth stating plainly because it inverts the usual reading: a green corpus
here is not weak evidence that M8 is done; it is evidence about *where the code
is on the render path*, which is the thing M9 changes. The consequence for the
plan is a constraint, not a warning: **M8's gate cannot be the pinned suite.**
It must be a module that calls our entry points directly — `_native.resolve`,
`_native.filter`, `render_string` against a Rust-compiled node — which is the
same conclusion M7 reached from the other direction, and the second milestone in
a row to reach it.

## 3. The wire type: `SafeStr` is opaque **by design**, and the bridge is tested

> **Correction (2026-10-09, same day, before the plan).** The first version of
> this section called `SafeStr`'s opacity a *live defect*. **That was wrong**,
> and wrong in the way this project keeps recording: the measurement was right
> and the conclusion outran it. `SafeStr` is opaque *as a type*, and the shell
> decodes it at the boundary — the mechanism is below, with the tests that
> already pin it. What survives is a narrower and still-real question, at the
> end of this section. The claim is corrected here rather than deleted, because
> the reason it was tempting is the M8 trap: a type that participates in no
> protocol *looks* like a defect until you read the code that consumes it.

`AttrValue::SafeStr` crosses the wire as `_native.SafeStr`
(`crates/template-py/src/lib.rs:592`). Measured (`m8_measure_escape.py`,
`probe` in the same file's transcript):

```text
  type          : <class 'builtins.SafeStr'>
  is str        : False
  is SafeData   : False
  has __html__  : False
  str(s)        : '<SafeStr "a & b">'
  conditional_escape(s) : '&lt;SafeStr &quot;a &amp; b&quot;&gt;'
  {{ x }} stock, x = SafeStr('a & b') : '&lt;SafeStr &quot;a &amp; b&quot;&gt;'
  safe == 'a & b'       : False
```

All true, and all of it is about a value that **never reaches a template in that
state**. Django's escaping protocol reads three things
(`django/utils/html.py:115-126`, `base.py:1123-1136`):

```python
if isinstance(text, Promise): text = str(text)      # conditional_escape
if hasattr(text, "__html__"): return text.__html__()
if not issubclass(type(value), str): value = str(value)   # render_value_in_context
```

`SafeStr` satisfies none of them — so the bridge lives on the Python side, at
`_impl/parser.py:600`'s `_decode_value`:

```python
if isinstance(value, _native.SafeStr):
    return mark_safe(value.text)
```

(`python/django_rust_templates/_impl/parser.py:666-667`.) And `_decode_value` is
called on every path a descriptor's value can travel: the node itself (`:259`),
a node's kwargs (`:310`), a variable's literal (`:342`), and recursively into
sequences and mappings (`:696`, `:704`, `:711`). The two ends are pinned —
`tests/test_materialise.py:318` (`test_a_safe_str_becomes_a_safe_string`) and
`:470-477`, whose comment states the container case outright: *"a `SafeStr`
inside a container arrives as a `SafeStr` and still needs `mark_safe`; a list
returned as-is would escape the content when the node renders."*

So the design is: **Rust's `Value::Safe` is a marker, not a string subclass, and
`_decode_value` is the single crossing where it becomes Django's `SafeString`.**
The opacity is the *point* — `filters/value.rs:56-59` records that the marker
cannot be dropped separately from the text.

**What survives as a real M8 question is totality, not correctness.** The bridge
covers what the *materialiser* sees. M8 adds two producers that are not the
materialiser: `Promise` forcing and a *called callable's* return value — both of
which originate in the resolver or the filter layer, and both of which could
carry a safe or promise-wrapped value that never passes `_decode_value`. That is
a question about M8's new paths, and it is cheap to answer once the paths exist:
**for each value M8 newly produces, name the crossing.** The obligation recorded
for the plan is therefore *"M8 adds no producer that bypasses `_decode_value`
without saying so"*, not *"repair `SafeStr`"*.

## 4. `alters_data` and `do_not_call_in_templates` are read off the *resolved object*

Measured, and the framing here is the reviewer's, verified against the pinned
tree (`m8_measure_baseline.py` §1b, §3b):

```text
  getattr(ClassAttr().save, 'alters_data', False) = False
  getattr(FnAttr().save,    'alters_data', False) = True
  getattr(ClassAttr,        'alters_data', False) = True
  getattr(FnAttr,           'alters_data', False) = False
```

| row (`{{ x.attr }}`) | stock | ours |
|---|---|---|
| `AltersData(class attr).save` | `'SIDE EFFECT'` | `'SIDE EFFECT'` |
| `AltersData(fn attr).save` | `''` | `''` |
| `DoNotCall.name` | `'ada'` | `'ada'` |

**The last row is the trap, and it is the same trap as the first.** With
`do_not_call_in_templates = True` written on the *class body*, `{{ x.name }}`
renders `'ada'` — the method **was called**. It has to be: Django evaluates
`getattr(current, "do_not_call_in_templates", False)` (`base.py:994`) where
`current` is the **bound method**, and a bound method proxies attribute lookup to
`__func__`, not to the class. So a fixture that puts either flag on the class
body of a class whose attribute is a *method* is testing nothing.

This is **one rule, not two spellings**, and the two fixtures in Django's own
suite are two different *resolved objects*:

- `django/db/models/utils.py:55-69` — `AltersData.__init_subclass__` copies the
  flag **onto the function** (`fn.alters_data = base_fn.alters_data`, `:66`),
  because the resolved value for `{{ obj.save }}` is a bound method and the
  function attribute is the one it can see.
- `tests/template_tests/test_callables.py:41-58` — `alters_data = True` as a
  **class attribute**, and the resolved value is the *instance itself* (a
  callable object), where class attributes are visible. The comment at `:56`,
  *"Since `my_doodad.alters_data` is True"*, is the tell: it names the instance,
  not a method.

So the obligation is narrower than "handle both spellings" and sharper:
**the lookup's result is the subject**, and any implementation that reaches past
it to the owning class or to `__func__` is reconstructing a rule Django does not
have. The two ways to get it wrong are not symmetric — consulting the *owning
class* suppresses a call Django makes (safe direction), while special-casing
*bound methods* invokes a method Django suppresses, which is the failure the flag
exists to prevent.

The three-way `CallArm` M3 built is already the right shape for this;
`CallArm::AltersData` exists precisely because a boolean would have folded
`DoNotCall` and `AltersData` into one behaviour Django never produces.

## 5. §5.2 names a trait that exists nowhere, and the code has two others

The reviewer's standing concern — *a seam named in the spec and absent from the
code* — is right to raise here and **wrong in the mechanism**, and the
correction is the useful part. `ValueResolver` and `resolve_bit`:

```text
  $ grep -rn "ValueResolver" --include=*.rs --include=*.py . | grep -v .django/
  docs/superpowers/specs/2026-10-08-...-design.md:147:pub trait ValueResolver {
  .claude/agents/rusty-reviewer.md:37
  .review/round-1.md:174   .review/round-1-response.md:132

  $ grep -rn "resolve_bit" ...
  (nothing)
```

Zero implementations. But the four methods that read as missing are named in
§5.2 as **methods on that Rust trait**, not as `_native` bindings — so
"absent from `_native`" was never evidence about them, and the §1 table's
`False` column measures the binding surface, not the seam. What the code
actually has:

- **`LookupTarget`** (`crates/template-core/src/parser/resolve.rs:107`) — "nine
  methods, one per predicate Django actually evaluates", with the count defended
  in the doc comment: *"collapsing any two of them merges two of Django's arms,
  and the merged behaviour is wrong in a case this milestone measured."* This is
  §5.2's `ValueResolver`, renamed and re-shaped by measurement.
- **`FilterHost`** (`crates/template-core/src/filters/host.rs:195`) — carries the
  escaping protocol as `is_safe` (`:272`), `mark_safe` (`:278`) and
  `conditional_escape` (`:287`).

So §5.2 is a design-time sketch whose names did not survive contact with the
implementation, and the spec is a **live-claims document under the anchor
guard** (D3′). That makes a §5.2 correction an M8 deliverable: the section
should name `LookupTarget` and `FilterHost`, or say explicitly that it is the
design-time shape and name where the truth now lives. Recording it as "the seam
is missing" would put a false claim in the document the next session reads.

## 6. Traps the plan must carry, each measured

**T1 — `defaultfilters.<name>` is not the filter of that name.** Six of 57
registered filters are shadowed by a different module attribute
(`m8_measure_filter_names.py`):

```text
  escape         attr=escape          reg=escape_filter
  escapejs       attr=escapejs        reg=escapejs_filter
  linebreaks     attr=linebreaks      reg=linebreaks_filter
  phone2numeric  attr=phone2numeric   reg=phone2numeric_filter
  timesince      attr=timesince       reg=timesince_filter
  timeuntil      attr=timeuntil       reg=timeuntil_filter
```

`defaultfilters.escape` is `django.utils.html.escape`, **the unconditional one**;
a template's `|escape` reaches `escape_filter`, which is `conditional_escape`.
Against the registry, all six rows agree:

```text
  escape       plain stock='a &amp; b'  ours='a &amp; b'  same
  escape       safe  stock='a & b'      ours='a & b'      same
  force_escape plain stock='a &amp; b'  ours='a &amp; b'  same
  force_escape safe  stock='a &amp; b'  ours='a &amp; b'  same
  safe         plain stock='a & b'      ours='a & b'      same
  safe         safe  stock='a & b'      ours='a & b'      same
```

**This reconnaissance did not discover T1 — it rediscovered it, and the
rediscovery is the part worth recording.** `tests/test_filter_inventory.py:779`
is `test_escape_is_not_the_registered_escape`, whose docstring already says
*"anything that reaches for `defaultfilters.escape` to compare against has
reached for the wrong object"*. This recon wrote a script to measure a fact our
own suite pins, then wrote it up as a finding — the exact failure the project's
"consult the tree before claiming" rule exists to prevent, and worse than being
wrong, because the answer was one `grep` away the whole time.

The trap is real and the row stays, for a reason that has nothing to do with
novelty: it produced this recon's **first false divergence**, and the way it
failed is the part a script re-run cannot recover — the measurement was correct
and the *conclusion* was wrong, because the entry point was. **Any stock-vs-ours
comparison in M8 reaches the filter through `register.filters[name]` or through
a real render — never through `defaultfilters.<name>`.** Five of the six
shadowed names are filters this project still owes, so the trap is not confined
to M8's own rows.

**T2 — a flag set where the lookup cannot see it is inert.** §4. The rule for
every fixture M8 writes: put `alters_data` / `do_not_call_in_templates` on the
**function**, unless the test is deliberately about a callable *instance*.

**T3 — the printer can force what the code did not.** §1's `__proxy__` row. A
measurement that renders a value with `str()` cannot see whether a `Promise` was
forced.

**T4 — a bare `settings.configure` is not enough to run the pins.** The corpus
run reported `1 error` on its first execution: `test_context` calls
`Engine.get_default()` and raises `ImproperlyConfigured` without a `TEMPLATES`
backend. That is the harness, not the corpus, and M7's close-out rule already
covers it — **a figure restated from an earlier session is the thing this
milestone kept being corrected for** — but it is the first time the harness
itself produced the wrong number rather than the prose.

## 7. What M8 has to deliver

Five obligations, in the order the measurement puts them:

1. **`CallArm::Call`** — call the resolved callable, with Django's
   `signature(current)` / `bind()` fallback (`base.py:1000-1013`) for the
   `TypeError` case.
2. **`CallArm::AltersData`** — substitute
   `context.template.engine.string_if_invalid`, which needs the `Context` the
   resolver is deliberately not given; the shape of that threading is a design
   decision for the plan.
3. **`Promise` forcing** — §5.2's `force_promise`, on the trait that exists.
   Measured target: `isinstance(lazy_value, Promise) = True` and
   `conditional_escape` forces it (`html.py:121-122`).
4. **`SafeStr` stays opaque; M8 adds no producer that bypasses
   `_decode_value`.** Not a repair — the bridge exists and is tested (§3). The
   obligation is a naming discipline: for each value M8 newly produces (a forced
   `Promise`, a called callable's return), state the crossing it takes.
5. **`render_value_in_context`'s sequence** — `template_localtime` →
   `localize` → the autoescape branch (`base.py:1123-1136`), including
   `issubclass(type(value), str)` as the *conversion* test rather than
   `isinstance` (a `SafeString` is a `str` and is not converted; a `Promise` is
   not and is).

## Open questions for the reviewer

**Q1.** I read the 88-green/0-failure corpus as *blind* rather than *passing*,
and therefore as an argument that M8's gate must be a direct-entry-point module.
Is that the right conclusion from §10.3's unresolved half, or does the rule's
"a row naming no `.django` module is out of scope" mean I should not be drawing
a M8 gate conclusion from the pinned suite at all?

**Q2.** With §3 corrected, this is no longer "which `SafeStr` shape" — the shape
is settled and tested. The question is the one §3 ends on: **is "name the
crossing for every value M8 newly produces" enough of a rule, or does it need a
test?** A rule is checkable only by a reader, and the two new producers (a forced
`Promise`, a called callable's return) both route through code M8 is about to
write. I would rather pin it: one test per new producer asserting the value is
protocol-visible after M8's path, in the shape of
`test_a_safe_str_becomes_a_safe_string`. Say if you think that is one test too
many for a boundary that has not yet been crossed wrongly.

**Q3.** §5.2: correct the names in place (`ValueResolver` → `LookupTarget`), or
mark the section as design-time and point at the traits? The spec is under the
anchor guard and D3′ says a correction in an unscanned document carries no
replacement number — but this one is in a scanned document, so I think it is a
plain correction with a new anchor row. Confirm.
