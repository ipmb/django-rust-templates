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

## 3. The wire defect: `SafeStr` is opaque to Django's protocol

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

Django's escaping protocol reads two things (`django/utils/html.py:115-126`):

```python
def conditional_escape(text):
    if isinstance(text, Promise):
        text = str(text)
    if hasattr(text, "__html__"):
        return text.__html__()
    else:
        return escape(text)
```

and `render_value_in_context` (`base.py:1123-1136`) reads a third:

```python
if not issubclass(type(value), str):
    value = str(value)
return conditional_escape(value)
```

`SafeStr` satisfies none of the three. Its `str()` is a **debug repr**, so a
`SafeStr` that reaches a template renders `&lt;SafeStr &quot;…&quot;&gt;` —
escaped garbage, silently. That is a D3-class defect (wrong and quiet) and it is
**live on the wire**, not merely latent: unlike the callable stub, nothing has
to be interposed for it to reach a render.

Where it reaches one today is not yet measured and is the first open question
for the plan (§6). The two consumers in our own tree are
`python/django_rust_templates/_impl/parser.py:666`, which tests `isinstance(value,
_native.SafeStr)` — so the shell already knows the type exists and converts
somewhere — and the Rust side, where `AttrValue::SafeStr` carries `Variable.literal`
(`parser/variable.rs:98,203`) and `{% verbatim %}` content
(`parser/tags/verbatim.rs:44`). **A `{% verbatim %}` body and a string literal in
an `{% if %}` are the two places a `SafeStr` is definitely produced**, which
makes "does either reach `render_value_in_context` unconverted?" a question with
a definite answer and a cheap measurement.

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
This is not a footnote — it produced this recon's first false divergence, and
the way it failed is worth keeping: the measurement was correct and the
*conclusion* was wrong, because the entry point was. Against the registry, all
six rows agree:

```text
  escape       plain stock='a &amp; b'  ours='a &amp; b'  same
  escape       safe  stock='a & b'      ours='a & b'      same
  force_escape plain stock='a &amp; b'  ours='a &amp; b'  same
  force_escape safe  stock='a &amp; b'  ours='a &amp; b'  same
  safe         plain stock='a & b'      ours='a & b'      same
  safe         safe  stock='a & b'      ours='a & b'      same
```

**Any stock-vs-ours comparison in M8 reaches the filter through
`register.filters[name]` or through a real render — never through
`defaultfilters.<name>`. Five of the six shadowed names are filters this project
still owes**, so the trap is not confined to M8's own rows.

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
4. **The `SafeStr` bridge** — the one live defect. Either the type participates
   in the protocol (`str` subclass + `SafeData` + `__html__`) or it never
   reaches `render_value_in_context`; which one is correct depends on §6's open
   question, and the answer changes the FFI.
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

**Q2.** For the `SafeStr` bridge: making it a real `str` subclass with
`SafeData` in its MRO puts the type in Django's protocol but makes it a Python
type the Rust side must keep in sync; keeping it opaque means the conversion
must be total, and totality is the property that fails quietly. Which failure do
you want recorded as the one M8 chose — and is there a third shape where the
wire carries a plain `str` plus a marker the shell reads?

**Q3.** §5.2: correct the names in place (`ValueResolver` → `LookupTarget`), or
mark the section as design-time and point at the traits? The spec is under the
anchor guard and D3′ says a correction in an unscanned document carries no
replacement number — but this one is in a scanned document, so I think it is a
plain correction with a new anchor row. Confirm.
