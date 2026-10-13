# Stream-free host calls — Design

**Goal:** stop shipping the remaining token stream across the host seam for compile
functions that provably never read it. A template with N host-compiled tags currently
builds O(N × tokens) Python objects at compile time where Django builds O(tokens); for the
compile functions covered here, the transfer becomes zero in both directions.

**Scope:** this is the bounded half of the host-seam defect. It does **not** make
component-heavy compiles linear — see §9, which states that limit precisely, because the
issue that prompted this investigation (#13, django-includecontents) is in the half this
design does not touch.

**Status:** design only. Four adversarial review rounds are incorporated (§10). The
performance claim is **not** established yet; §8 is a pre-ship gate, not a result.

## 1. Global constraints

- **Drop-in compatibility with Django 6.0.9 is the only load-bearing thing.** Byte-identical
  output on every corpus row, and the vendored Django suite in the gate, are the criterion.
- **A declined classification is a lost optimisation, never a different answer.** Every
  recognised deviation must select the existing path **before the callback runs**. There is
  no retry: a partially executed compile function may already have mutated
  `parser.tags`, `parser.extra_data` or the filter registry.
- **No transcribed file is edited.** `engine/library.py`, `engine/base.py` and
  `engine/defaulttags.py` are read, never written. Adding a marker to `Library.simple_tag`
  would intersect `scripts/check_transcriptions.py:250` and its enforcement at `:1295-1306`.
  Every mechanism below lives in `host.py` and the Rust seam.
- Rust tests run only through `scripts/run_rust_unit_tests.sh`; `cargo` needs
  `source scripts/env.sh`.
- Never run `maturin develop` while a review is in flight — the reviewer shares the venv and
  the built `.so`.
- No pinned test counts. Write the structural check.
- Temp files in the session scratchpad, never `/tmp`.

## 2. The defect

`ShellHost.call_tag` (`python/django_rust_templates/host.py:422-494`) receives every
remaining token and returns every remaining token. The Rust side is
`crates/template-py/src/lib.rs:1054-1130`, driven from
`crates/template-core/src/parser/mod.rs:356-368`, which calls `remaining_tokens()`
(`mod.rs:249`) before each host tag and `replace_tokens(left)` (`mod.rs:257`) after.

Counted by instrumenting `call_tag`, flat `{% wrap %}…{% endwrap %}` pairs:

| N | calls | tokens in | tokens out | in/call |
|---:|---:|---:|---:|---:|
| 1 | 1 | 4 | 0 | 4.0 |
| 10 | 10 | 265 | 225 | 26.5 |
| 50 | 50 | 6,325 | 6,125 | 126.5 |
| 200 | 200 | 100,300 | 99,500 | 501.5 |

`tokens_in` ≈ 2.5N². Tokens-per-call rises linearly with N, which is the quadratic stated
directly rather than inferred from a timing curve.

`{% load %}` is the universal case. It is host-compiled, it sits at the top of the file so it
sees the largest stream in the template, and it consumes nothing. On a 200-tag template a
single `{% load %}` moves 1,000 tokens.

## 3. Where the time goes

Disjoint spans, one run, bracketed by the whole of `call_tag`, timer overhead subtracted,
residual reported as residual. N=200, compile 45.5 ms:

| span | ms | % of compile |
|---|---:|---:|
| inbound `Token` build (`host.py:435-438`) | 24.5 | 54% |
| outbound wire serialisation (`host.py:493`) | 11.3 | 25% |
| the tag's own compile function (`host.py:454`) | 1.8 | 4% |
| residual inside the bracket (PyO3 call overhead **+ unmeasured**) | 0.1 | 0.2% |
| **outside** the bracket | 7.7 | 17% |

Perturbation against an uninstrumented control: +2.7%.

**`outside` is not attributed.** It contains the `mod.rs:249` clone, the `lib.rs:1067-1075`
`PyList`, the `lib.rs:1115-1133` extraction and the `mod.rs:363` replacement, but *also*
native compilation of the bodies and dispatch. It is an upper bound on Rust-side
marshalling, not a measurement of it.

The reading that matters: for a host-heavy template, ~79% of compile is building and
tearing down token objects that the compile function touches for 4%.

## 4. What is eligible

A compile function is **stream-free** when it provably never reads the token stack. Three
populations qualify, and the third is excluded explicitly because it is the near miss:

| | shares one `__code__` | stream-free | why |
|---|---|---|---|
| `Library.simple_tag` → `compile_func` (`library.py:129`) | yes | **yes** | reads `token.split_contents()` and calls `parse_bits`; never touches `parser.tokens` |
| `Library.inclusion_tag` → `compile_func` (`library.py:269`) | yes | **yes** | same shape (`library.py:270-271`) |
| `Library.simple_block_tag` → `compile_func` (`library.py:188`) | yes | **no** | calls `parser.parse()` and `delete_first_token()` at `library.py:213-214` |
| `defaulttags.load` (`defaulttags.py:1105`) | n/a | **yes** | splits its own token, resolves libraries, calls `parser.add_library()`, returns a `LoadNode` |

Verified in this tree: `simple_tag`'s `compile_func` shares one `__code__` object across
different `Library` instances, different wrapped-function signatures and both values of
`takes_context`. `inclusion_tag` and `simple_block_tag` each have their own distinct shared
`__code__`, so the third is excludable by the same test.

**Names are not usable as provenance.** `@wraps(func)` (`library.py:128`) rewrites
`__qualname__` to the wrapped function's, leaves `__wrapped__` in place, and copies module
and name metadata. Measured: a tag registered as `a` reports `__qualname__ == "a"` while
`__code__.co_name` remains `compile_func`. Qualname strings, closure shapes and any
user-copyable marker attribute are therefore all unsound. `__code__` is not copied by
`@wraps`.

### 4.1 In-tree proof that direct stream reads exist

`engine/templatetags/i18n.py:564,575` — `do_block_translate` — reads the stream directly:

```python
while parser.tokens:
    token = parser.next_token()
```

It is a plain `@register.tag`, so the classifier below excludes it by construction, and it is
retained as a stream-access exclusion test.

**It is not an example of silent miscompilation.** Handed an empty list it raises, because it
validates the dispatched token against the expected end tag at `i18n.py:581-585`:

```
TemplateSyntaxError: 'blocktranslate' doesn't allow other block tags (seen 'blocktranslate') inside it
```

An earlier draft claimed it would succeed silently. That was wrong. The silent-skipping
fixture is instead a purpose-built compile function that loops on `while parser.tokens:` with
no end-tag validation — written for the suite, since no in-tree tag has that shape.

## 5. Classification

Eligibility is a property of the **live callable and the current environment**, resolved at
the moment of dispatch. It is not cached by command name.

### 5.1 Why not by command name

`{% load %}` mutates `parser.tags` mid-parse (`base.py:707-709`), and later registrations
win — which is exactly what `has_tag` already honours (`host.py:386-400`). A template may
compile a `simple_tag` named `widget`, load a library that rebinds `widget` to a block
compiler, and compile `widget` again. A `True` cached against the name would then route a
body-consuming compiler through the stream-free seam. This is ordinary supported shadowing.

Any memoisation must therefore be keyed on the callable object actually selected, and must
be invalidated by every supported mutation of the environment in §5.3.

### 5.2 The supported environment, declared

An earlier draft promised to guard "everything the eligible compilers reach" while also
disclaiming deliberate monkeypatching. Those are inconsistent, and the inconsistency is
resolved here rather than left to code review.

**Inside the supported environment** — a deviation must cause fallback, and each is a
captured-identity check:

| population | guarded bindings |
|---|---|
| `simple_tag` / `inclusion_tag` | **functions:** `Parser.compile_filter` (`base.py:711`), `Parser.find_filter` (`base.py:717`), `Token.split_contents` (`base.py:429`), `library.parse_bits` (`library.py:390`), `library.token_kwargs` (`library.py:11`, resolved at `:423`), `base.smart_split`.<br>**class bindings _and_ their methods:** `library.SimpleNode` + `__init__`/`__new__` (`:321`), `library.InclusionNode` + `__init__`/`__new__` (`:354`), `library.TagHelperNode` + `__init__` (`:304`), `base.FilterExpression` + `__init__`/`args_check` (`base.py:943`), `base.Variable` + `__init__`/`resolve`, the `Token` class used for the dispatched token |
| `defaulttags.load` | **functions:** `Parser.add_library` (`base.py:707`), `defaulttags.find_library` (called in both branches, `:1125`/`:1131`), `defaulttags.load_from_library` (`:1080`).<br>**class bindings _and_ their methods:** `defaulttags.LoadNode` + `__init__`/`__new__` (`:392`), `defaulttags.Library` + `__init__`/`__new__` (`library.py:27`, constructed at `defaulttags.py:1084`) |

**Outside the supported environment** — not guarded, and a deviation there is undefined
rather than detected: CPython's stdlib (`inspect.unwrap`, `inspect.getfullargspec`, `re`) and
`django.utils` helpers reached below our own code. Guarding those is unbounded and they are
not plausible extension points.

Three corrections to an earlier draft, each demonstrated rather than argued:

- **`library.token_kwargs`, not `base.token_kwargs`.** `parse_bits` resolves `token_kwargs`
  through `library.py`'s own globals (`library.py:423`, and `parse_bits.__globals__ is
  library.__dict__`). The two names currently point at one object, so an identity check on
  `base.token_kwargs` passes today — but rebinding only `library.token_kwargs` to a
  token-consuming wrapper leaves `base.token_kwargs` untouched and defeats it. Guard the name
  actually resolved.
- **`load` does not call `Token.split_contents`.** It uses `token.contents.split()`
  (`defaulttags.py:1121`); `split_contents` appears in that function only inside the comment
  at `:1119-1120` explaining why it is *not* used. Guarding it there is an unnecessary restriction.
  It also calls `find_library` in **both** syntax branches (`:1125`, `:1131`).
- **Class identity and implementation identity are each insufficient alone — guard both.**
  Keeping `SimpleNode` bound to the same class says nothing about its `__init__`, which
  reaches `TagHelperNode.__init__` through `super()`. But the converse also fails: replacing
  `library.SimpleNode` with a *subclass* that inherits the captured `__init__` untouched and
  adds a `__new__` leaves `SimpleNode.__init__ is captured` **True**, while the generated
  compiler calls the replacement class at `library.py:147` and its `__new__` reads the
  suffix. Measured. So the rows above pin the class binding, its `__init__` **and** its
  `__new__`.

Capture happens once at host-module import, after the relevant modules are initialised. Code
objects are derived per interpreter by registering throwaway tags on a throwaway `Library` —
never by editing `library.py`, whose `simple_tag` is a `COMPARED_PATH` span
(`scripts/check_transcriptions.py:250`) and therefore cannot carry a declared divergence at
all. Nothing is persisted across processes: no 3.12 code object need equal a 3.13 or 3.14 one.

### 5.3 The test

A dispatch is stream-free only if **all four** hold. Each is O(1) and exact.

**1 — The parser is one we support.** `type(parser) is Parser or type(parser) is
_DeferredTokenParser`. An exact type check, not `isinstance`.

This condition rejects **foreign** parser implementations — an unknown `Parser` subclass
overriding `find_filter` or `add_library`, a metaclass, a custom `__getattribute__` — without
invoking their attribute lookup.

It does **not** prove the lookup machinery of the two accepted classes is itself unchanged:
rebinding `__getattribute__` or installing a descriptor on `Parser` or `_DeferredTokenParser`
is outside what any identity check here establishes. That is declared outside the supported
environment in §5.2 rather than claimed as proven. `_DeferredTokenParser.__getattr__` stays supported and is
never triggered by this guard, since it fires only for a missing `tokens`
(`render.py:277-284`).

**2 — No instance override.** For each guarded parser method in the population's row,
`name not in vars(parser)`.

**3 — No class rebinding, on either accepted class.** For each guarded method,
`Parser.__dict__[name] is` the captured function **and** `name not in
_DeferredTokenParser.__dict__`.

The second clause is not belt-and-braces. `_DeferredTokenParser` is itself a `Parser`
subclass (`render.py:234`), so its own `__dict__` wins during resolution. Measured: assigning
`compile_filter` on `_DeferredTokenParser` passes the exact-type check, passes
`not in vars(parser)`, passes `Parser.__dict__['compile_filter'] is captured` — and the
resolved method is still the replacement. One known inheritance level, which the guard has to
account for by name.

Conditions 1–3 together make the resolved attribute *provably* the captured implementation,
without comparing it. That matters: `parser.compile_filter is Parser.compile_filter` is
**`False` on a completely standard parser**, because the resolved attribute is a bound method
and the captured one is a plain function. A spec that compared them directly would have
rejected every template and silently disabled the feature. Where a comparison is unavoidable
elsewhere, it is `resolved.__func__ is captured and resolved.__self__ is parser` — the
receiver clause included, so a trusted method bound to a *different* parser cannot qualify.

**4 — The callable is the one we captured.**

- For the generated compilers: `type(func) is types.FunctionType`, **and** `func.__code__` is
  one of the captured code objects, **and** `func.__globals__` is the captured globals dict.
  The `FunctionType` clause is load-bearing — a callable object can expose `__code__` and
  `__globals__` attributes while executing an entirely different `__call__`. The globals
  clause is equally so: a function carrying the genuine code object and closure but foreign
  globals resolves `parse_bits` through *its own* globals (`library.py:135`) and can consume
  tokens while `library.parse_bits` is untouched.
- For `load`: `func is` the **captured** load object — not whatever currently occupies
  `defaulttags.load`.
- The `type(func) is types.FunctionType` test runs **first**, before any attribute of `func`
  is read. `getattr(..., None)` on a callable object can itself execute custom lookup, so the
  type gate precedes the metadata read rather than accompanying it. An ordinary
  callable-object tag is simply not eligible, and declines without executing anything.
- Plus the module-binding checks for that population's row in §5.2.

**This is a compatibility classifier, not a security boundary.** It exists to avoid
misclassifying reconstructed functions and callable lookalikes, not to withstand deliberate
monkeypatching. §5.2 states which layer that promise covers.

### 5.4 No caching in the first implementation

Classification runs live, per dispatch. Provenance facts from §5.2 are captured once at
import and are immutable; nothing about the *live* environment is memoised.

§5.1's shadowing case is why: eligibility is a property of the callable currently bound to
the command plus the current environment, and an earlier host callback in the same parse can
change either. If §8's tiny-template control shows the live check costs more than it saves,
memoisation may be added keyed on the callable object with explicit invalidation — but it is
not in the first implementation, and immutable provenance must stay separate from live
environment checking.

Classification and invocation operate on the **same** selected callable. The trait's boolean
return cannot transport it, so `ShellHost` holds the callable selected by
`tag_is_stream_free` in a single-slot **pending selection**, which `call_tag_stream_free`
consumes and clears. It is cleared on every exit, success or raise. An implementation that
instead looked `parser.tags[command]` up a second time at invocation would not satisfy this,
because an earlier callback in the same parse can rebind the name between the two crossings.

That slot is *dispatch state*, not eligibility memoisation — it lives for one dispatch and
says nothing about the next. No concurrent-mutation guarantee is implied or needed: the model
is synchronous.

## 6. The seam

Classification happens **inside the `has_tag`-true arm** (`mod.rs:351`) and **before**
`remaining_tokens()` (`mod.rs:356`), so an all-native template pays nothing and an eligible
tag skips the Rust clone and the `PyList` as well as both Python halves. A decision taken
only inside `ShellHost` could not undo the inbound cost.

```rust
let compiled = if self.host.has_tag(&command) {
    if self.host.tag_is_stream_free(&command) {
        // No take, no replace: self.tokens is untouched.
        self.host.call_tag_stream_free(&command, wire(&token))
    } else {
        let remaining = self.remaining_tokens();   // today's path, unchanged
        match self.host.call_tag(&command, &token, &remaining) {
            Ok((desc, left)) => { self.replace_tokens(left); Ok(desc) }
            Err(error) => Err(error),
        }
    }
} else if let Some(compile) = registry::builtin_tag(&command) {
    …
};
let node = compiled.map_err(|error| self.error(&token, error))?;
```

Three properties of that shape are deliberate:

- **No empty remainder.** `mod.rs:361-364` replaces the parser's stack with whatever comes
  back, so returning an empty `Vec` would discard the rest of the template. The stream-free
  method returns a `NodeDesc` only.
- **No `?` on the host call.** The result stays inside `compiled` so it reaches the shared
  `map_err(|error| self.error(&token, error))` at `mod.rs:380`. The existing code avoids `?`
  in this arm for exactly that reason (`mod.rs:357-366`); an early return would strip the
  token annotation from boundary errors that carry none, including the counterpart of the
  node-validation error at `lib.rs:1106-1113`.
- **Skipping both halves is value-preserving.** `remaining_tokens()` reverses a clone
  (`mod.rs:249-250`) and `replace_tokens()` reverses it back (`mod.rs:257-258`); for a
  genuinely stream-free callback, doing neither is equivalent.

The sketch above is architectural, not literal. Two things it elides:

- **`wire()` belongs in the PyO3 adapter, not `template-core`.** Both new trait methods take
  a core `&Token`, exactly as today's `call_tag` does (`parser/host.rs:55-60`); the wire
  conversion happens in `crates/template-py`.
- **Both methods get default implementations** — `tag_is_stream_free` returns `false` and
  `call_tag_stream_free` is then unreachable — so `NoHost` and the ~8 core test hosts stay on
  today's path untouched, the way `library_tag_loaded` already does.

  **`FilterCheckHost` is not among them.** It is the *production* adapter: it forwards to
  `ShellHost` (`lib.rs:1026-1037`) and `_native.compile_template` constructs it at
  `lib.rs:5277-5282` before handing it to `parse_nodelist`. There is no separate adapter for
  a "PyO3 implementation" to live in. `FilterCheckHost` must therefore **override both
  methods**, returning `false` from the classifier only when it holds no Python host. An
  earlier draft said it could keep the defaults; that would have left the whole feature
  inert, passing every test except the zero-transfer assertion.

```rust
fn tag_is_stream_free(&self, _command: &str) -> bool { false }

fn call_tag_stream_free(
    &self, command: &str, token: &Token,
) -> Result<NodeDesc, Error> { unreachable!("gated on tag_is_stream_free") }
```

The PyO3 implementation of `call_tag_stream_free` performs the **same** node handling as
`call_tag`: `must_be_first` extraction (`lib.rs:1106-1113`), reference-table insertion, and
the `Error::internal` boundary error when the host returns something that is not a node. Only
the token vector is absent.

`has_tag` keeps its existing boolean contract (`host.py:386-400`) — its `False` already
conflates several situations whose core routing differs, and smuggling a third state through
the bool extraction is not acceptable.

Whether one extra crossing per host tag is cheaper than the transfer it avoids on *short*
streams is a measurement, not an assumption; §8 covers it, including the case that pays the
crossing and receives nothing.

## 7. `parser.tokens` during a stream-free call

The sentinel is armed in **both** debug and production.

`_UnavailableStream` raises on `__bool__`, `__len__`, `__iter__`, `__getitem__`, `__delitem__`,
`pop`, `append` and `__repr__`.

Neither alternative is safe:

- **An empty list** lets a misclassified callback run `while parser.tokens:` and silently
  skip its body — §4.1 is a real in-tree instance of that loop.
- **Leaving the attribute alone** is worse. Before the first host call
  `_DeferredTokenParser` deliberately has **no** `tokens` attribute (`render.py:272-275`), so
  the first read triggers a full-source Python lex (`render.py:277-284`) and hands the
  callback the *entire original template*, including tokens already consumed. After a prior
  host call it is a stale suffix instead.

`Parser.__repr__` formats `self.tokens` with `%r` (`base.py:566`), and the sentinel's repr
raises by design. Diagnostic and cleanup paths must therefore never let a repr attempt
replace the original compiler exception; a sentinel violation is reported as a contract
failure that preserves the error it was raised during.

### 7.1 The exception

Every sentinel operation raises `StreamFreeContractViolation`, a `RuntimeError` subclass
defined in `host.py`. It is **an ordinary Python host error**, not a dedicated boundary
error: it travels through `python_host_error`, is stashed in `host.last_error`
(`host.py:479-491`), and is restored by identity in `django_exception`
(`render.py:223-227`) with its traceback intact — the same route any compile function's
exception takes. That consistency is the point; a second mechanism would be a second thing to
get right.

Its message names the operation (`"len()"`, `"pop()"`, `"iteration"`, …) and the command, and
**never formats the sentinel itself**. `Parser.__repr__` renders `self.tokens` with `%r`
(`base.py:566`), so any diagnostic that reprs the parser would raise a second time from
inside the first exception's handling. Tests assert that the original exception and its
attached token survive, in both debug modes, through the full compile-and-convert path —
not merely that the sentinel's methods raise when called directly.

A tripped sentinel is a genuine integration failure. The callback is **not** replayed on the
ordinary path: it may already have mutated parser state.

### 7.2 Stated behaviour change

Today a streamful call leaves the real leftover list on `parser.tokens`
(`host.py:493-494`). A stream-free call leaves the sentinel. This is documented as a limit of
the stream-free seam rather than advertised as drop-in equivalence. `ShellHost.call_tag`
itself is unchanged for existing direct callers. No in-tree caller reads `parser.tokens`
after a host call; `render.parse_template` compiles and returns without inspecting the stack.

## 8. Pre-ship gate — measurement, not claim

Transfer volume eliminated, on templates shaped like bakerydemo (median 8 tags/template, p90
22, max 59):

| shape | host calls | stream-free | tokens saved | share |
|---|---:|---:|---:|---:|
| median (8) | 3 | 3 | 78 | 100% |
| p90 (22) | 8 | 7 | 416 | 85% |
| max (59) | 20 | 17 | 2,051 | 81% |
| component-heavy | 31 | 11 | 1,755 | 46% |

**This table measures eliminated token volume, not elapsed time, and does not include
classification overhead.** The N=200 flat-block-tag workload in §3 mostly measures the path
this design does *not* optimise. Neither establishes a wall-clock saving on a realistic
cached template.

Before this ships, all of the following must be measured end-to-end and recorded, with the
probe scripts committed under `.review/` so the numbers are checkable rather than asserted:

| # | workload | what it proves |
|---|---|---|
| 1 | median / p90 / max shapes | the gain this is bought with |
| 2 | **all-native** control | no regression where no host tag exists — pays nothing, gains nothing |
| 3 | **tiny one-host** control | the extra classification crossing is cheaper than the transfer it avoids |
| 4 | **all-streamful** control (§3's flat block-tag shape) | the rejected-host path pays the crossing and receives **no** saving — the only workload that is pure overhead |
| 5 | **mixed transitions** — stream-free → streamful → stream-free | classification is correct and cheap across route changes |
| 6 | the existing benchmark corpus | the default path is unmoved |

Workload 4 is the one an earlier draft missed, and it is the one most likely to regress.

Every row runs in **both** debug modes, at a realistic size and a stress size, paired against
the unchanged baseline in the same process. Report **dispersion**, not an isolated best time:
median and interquartile range over at least 15 runs. "No measurable regression" means the
post-change median is within the baseline's interquartile range, and any regression beyond
that is reported rather than absorbed.

The component-heavy row in the table above is a **volume illustration, not a latency gate**.
If its benefit becomes part of the argument for shipping, its wall-clock result joins the
required set — and even then it would not prove anything about component forms this work has
not inspected.

If the tiny-template or all-streamful control regresses, §5.4's memoisation is the lever,
subject to its invalidation requirement. That decision is deferred to the measurement.

## 9. What this does not fix

A compile function that parses a body cannot be stream-free. `django-includecontents`
compiles components through exactly that shape, so this design leaves the component round
trip intact — the 46% row in §8. It accelerates the *other* host calls in such a template,
notably its `{% load %}`, so component-heavy compiles would be **expected** to improve
without becoming linear. That is a prediction, not a result: §8 establishes no latency gain
for this shape, and none is claimed.

The remaining half needs a boundary design this spec does not attempt: `Parser.parse()`
calls `self.tags[command](parser, token)` directly (`base.py:580-622`) without returning
through `ShellHost`, so a consenting outer tag would hand a non-consenting nested third-party
tag whatever stream object it was given. That is tracked separately and is **not** a
prerequisite for anything here.

The pinned `django-includecontents` implementation has not been inspected for this claim;
confirm which component forms consume a body before restating it.

## 10. What review changed

Four adversarial rounds. The first three killed two earlier designs outright and are recorded
because the dead ends are the reason this one is shaped as it is.

- **A lazy cursor object** standing in for `parser.tokens`: killed. `isinstance(x, list)` and
  `list.pop(x)` cannot be intercepted, so a callback can branch on the type check and
  silently compile something different.
- **One shared Token list, sliced per call**: killed, empirically. Today every call builds
  *fresh* Tokens, so mutating a retained Token from one call is invisible to the next;
  sharing cached objects changes that. Separately, a callback can pop a token and append a
  replacement, leaving the length identical and the stream different — so Rust can never
  learn the result from a count.
- **Classification by callable identity alone**: killed. A stock `simple_tag` compiler
  reaches `parse_bits` → `compile_filter` → `find_filter`, all overridable; the reviewer
  overrode `compile_filter` on a parser instance and watched the stock compiler observe the
  token count. §5.3 is the transitive replacement.
- **Caching eligibility by command name**: killed by §5.1's shadowing case.
- **`call_tag_stream_free(...)?`**: killed by §6 — it bypasses the shared token annotation.
- **Leaving `parser.tokens` alone outside debug**: killed by §7's deferred-lexer case.
- **Comparing `parser.compile_filter` against `Parser.compile_filter`**: killed, and it would
  have disabled the feature outright rather than loosened it. The resolved attribute is a
  bound method and the captured one is a plain function, so that identity is `False` on a
  *standard* parser. §5.3 conditions 1-3 prove the binding instead of comparing it.
- **Guarding `base.token_kwargs`**: killed. `parse_bits` resolves the name through
  `library.py`'s globals, so rebinding only `library.token_kwargs` defeats it while the
  `base` identity still passes.
- **Guarding classes rather than callables**: killed. `SimpleNode` can keep its identity while
  its `__init__` changes.
- **Claiming `do_block_translate` would silently miscompile**: withdrawn, having been run. It
  raises, because it validates its end tag (§4.1).
- **Requiring `Token.split_contents` for `load`**: withdrawn. `load` uses
  `token.contents.split()`; `split_contents` appears only in the comment explaining why not.
- **A claimed 70/30 Python/Rust cost split**: withdrawn. It rested on a synthetic matching a
  net excess between two different pipelines, which is not a partition; §3 is the disjoint-span
  replacement, and it reports its residual as residual.

## 11. Acceptance

**Narrowed from an earlier draft.** "Tokens materialised per compile is O(tokens)" is not
true of an arbitrary mixed template: one containing many *streamful* callbacks still
materialises quadratic suffixes, because this design does not touch them. The two assertions
that are true:

- **zero** suffix transfer on every eligible call, and
- linear dispatched-token construction for templates whose host tags are all eligible.

The old `call_tag` counter cannot prove this on its own, since eligible calls no longer go
through it. Instrument the construction and transfer sites on **both** routes.

Further:

- §8's six workloads recorded, with dispersion, before ship.
- The Django suite stays green; `tests/test_host_tags.py`'s route assertions (`:115-142`) are
  unchanged — a host block tag's body is still Django-parsed.
- `scripts/check_transcriptions.py` passes with no new declared region: no transcribed file
  is edited.
- Classification tests on all three supported Python versions, covering both `takes_context`
  values, varied signatures and `as name` syntax.

**Negative tests.** Each asserts fallback **before the callback runs**, and each asserts the
callback received the ordinary stream and produced output matching the unchanged baseline —
not merely that eligibility returned `False`:

| deviation | population |
|---|---|
| an overridden `{% load %}` | load |
| a `Parser` subclass overriding only `find_filter` | generated |
| a `Parser` subclass overriding only `add_library` | load |
| instance override of `compile_filter` installed by an **earlier** host callback in the same parse | generated |
| rebinding `Parser.compile_filter` on the class itself | generated |
| **shadowing a guarded method on `_DeferredTokenParser` itself** — the accepted subclass | both |
| **replacing `library.SimpleNode` with a subclass inheriting the captured `__init__`, adding a `__new__`** | generated |
| `Library.__init__` changed in place, against selective `{% load x from y %}` | load |
| **`library.token_kwargs` rebound while `base.token_kwargs` is untouched** | generated |
| `defaulttags.find_library` rebound | load |
| `SimpleNode.__init__` or `FilterExpression.args_check` changed while the class object is unchanged | generated |
| a function rebuilt with the genuine code object and closure but foreign `__globals__` | generated |
| a callable object exposing `__code__`/`__globals__` but a different `__call__` | generated |
| a callable tag with no `__code__` at all — must decline cleanly, not raise | generated |
| a `{% load %}` that rebinds a `simple_tag` name to a block compiler mid-parse | both |
| a non-exact parser type — rejected **without invoking** its attribute lookup | both |
| `simple_block_tag`, which must never classify as stream-free | — |

Plus:

- `do_block_translate` (§4.1) compiles correctly and is never classified stream-free.
- The purpose-built silent-skipping fixture from §4.1 appears in **two** tests with
  deliberately opposite outcomes, and the distinction is stated so acceptance does not ask
  for both at once: under *ordinary* dispatch it is rejected by §5.3 and gets the full
  stream like any other tag; only when *deliberately injected* onto the stream-free method
  does it trip the sentinel. The first is the contract working; the second is the contract
  being violated on purpose.
- Sentinel tests: every listed operation raises `StreamFreeContractViolation`; the original
  exception and its attached token survive the full compile-and-convert path in both debug
  modes; no diagnostic path reprs the parser.
- Error-path tests: exception identity restoration (`render.py:223-227`) and the token
  attached to the error are unchanged on the stream-free path, including a boundary error
  carrying no token.

### 11.1 Test order

Written first, because these are where an implementation is most likely to go wrong:

1. **Guard correctness against the real production parser.** An unmodified
   `_DeferredTokenParser` must *qualify* — the failure mode is a guard so strict it disables
   the feature, which no other test catches. Then every deviation above declines, each
   asserted through the real generated compiler with baseline-equivalent output.
2. **End-to-end activation and route transitions**, through `_native.compile_template` rather
   than `ShellHost` directly, proving zero suffix transfer via `FilterCheckHost`. Sequence:
   `load` → eligible simple tag → streamful block → eligible inclusion tag, checking trailing
   text survives, registry shadowing is honoured, and the host-body route assertion holds.
   This is what catches an inert adapter and stale dispatch state.
3. **Failures through both modes and both seams.** Exception object and token preserved for a
   generated-compiler error and an injected sentinel violation; core token attachment for a
   boundary error carrying none; no replay; no repr-induced replacement; command-stack and
   reference handling intact on success.

## 12. Non-goals

- Making component-heavy compiles linear (§9).
- Any opt-in restricted contract for body-consuming tags.
- Porting any Python tag into Rust.
- Withstanding deliberate monkeypatching (§5.3).
- Changing `ShellHost.call_tag`'s existing contract for direct callers.
