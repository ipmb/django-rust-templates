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
| residual inside the bracket (PyO3 call overhead) | 0.1 | 0.2% |
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

### 4.1 In-tree proof that the risk is real

`engine/templatetags/i18n.py:564,575` — `do_block_translate` — reads the stream directly:

```python
while parser.tokens:
    token = parser.next_token()
```

It is a plain `@register.tag`, so the classifier below excludes it by construction. It is
retained as a regression test because it demonstrates the failure mode exactly: handed an
**empty** list it would compile silently and wrongly, with `singular` left empty and no
exception anywhere.

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

### 5.2 Trusted implementations, captured once

At host-module import, after the relevant modules are initialised, capture:

- the `__code__` objects of `simple_tag`'s and `inclusion_tag`'s generated `compile_func`,
  obtained by registering throwaway tags on a throwaway `Library` — never by editing
  `library.py`;
- the `__globals__` dict of those generated functions;
- `defaulttags.load` itself;
- the trusted implementations of everything the eligible compilers reach, **per population**,
  because the two have different dependency sets:

| population | trusted implementations that must be unchanged |
|---|---|
| `simple_tag` / `inclusion_tag` | `Parser.compile_filter` (`base.py:711`), `Parser.find_filter` (`base.py:717`), `Token.split_contents` (`base.py:429`), `library.parse_bits` (`library.py:390`), `base.token_kwargs` (`base.py:1372`), `base.FilterExpression` **and** `FilterExpression.args_check` (`base.py:943`), `library.SimpleNode` (`:318`), `library.InclusionNode` (`:353`) |
| `defaulttags.load` | `Parser.add_library` (`base.py:707`), `defaulttags.load_from_library` (`defaulttags.py:1080`), `defaulttags.LoadNode`, `Token.split_contents` (`base.py:429`) |

`Parser.add_library` is in that second row because `load`'s identity matching says nothing
about it: an instance or subclass override of `add_library` can read or consume the stream
while `load` itself is untouched.

Code objects are derived per interpreter and never persisted: nothing requires a 3.12 code
object to equal a 3.13 or 3.14 one. A module reload that changes the code conservatively
loses eligibility until the trusted set is rebuilt.

### 5.3 The test

A callable is stream-free only if **all** hold:

1. `func.__code__` is one of the captured code objects, **and** `func.__globals__` is the
   captured globals dict — or `func` is `defaulttags.load` itself;
2. every **effective** parser method in that population's row of §5.2 is the captured
   implementation — checked against the resolved attribute, so that an instance override, a
   `Parser` subclass override, *and* a rebinding of the base-class attribute are all caught;
3. the module-level helpers in that row are still the captured objects.

Condition 1's globals clause is not pedantry. A function carrying the genuine code object
and closure but a different `__globals__` resolves `parse_bits` through *its own* globals
(`library.py:135`), so it can consume tokens while `library.parse_bits` is untouched and a
code-identity test passes.

Condition 2's "effective" wording is likewise load-bearing: checking
`type(parser).compile_filter is Parser.compile_filter` plus the absence of an instance
override passes for a `Parser` subclass that overrides only `find_filter`, which then reads
the stream from inside `FilterExpression.__init__` (`base.py:810`).

**This is a compatibility classifier, not a security boundary.** It exists to avoid
misclassifying reconstructed functions and callable lookalikes, not to withstand deliberate
monkeypatching. The spec says so where it is documented.

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

`tag_is_stream_free` is a new `Host` trait method with an explicit PyO3 signature, and
`NoHost`/`FilterCheckHost` implement it. `has_tag` keeps its existing boolean contract
(`host.py:386-400`) — its `False` already conflates several situations whose core routing
differs, and smuggling a third state through the bool extraction is not acceptable.

Whether one extra crossing per host tag is cheaper than the transfer it avoids on *short*
streams is a measurement, not an assumption; §8 covers it.

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

A tripped sentinel is a genuine integration failure. The callback is **not** replayed on the
ordinary path: it may already have mutated parser state.

### 7.1 Stated behaviour change

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

- median, p90 and max shapes: compile time before and after;
- an **all-native** control: no measurable regression;
- a **tiny one-host** template: the extra classification crossing must not cost more than
  the transfer it avoids;
- debug and non-debug paths separately;
- the existing benchmark corpus, for the default path.

If the tiny-template control regresses, the classification result may be memoised per
callable for the life of the parse — subject to §5.3's invalidation — or the feature gated on
stream length. That decision is deferred to the measurement.

## 9. What this does not fix

A compile function that parses a body cannot be stream-free. `django-includecontents`
compiles components through exactly that shape, so this design leaves the component round
trip intact — the 46% row in §8. It accelerates the *other* host calls in such a template,
notably its `{% load %}`, so component-heavy compiles improve without becoming linear.

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
- **A claimed 70/30 Python/Rust cost split**: withdrawn. It rested on a synthetic matching a
  net excess between two different pipelines, which is not a partition; §3 is the disjoint-span
  replacement, and it reports its residual as residual.

## 11. Acceptance

- Tokens materialised per compile is O(tokens) for the covered populations, asserted with the
  counting instrument from §2 rather than inferred from timings.
- §8's measurements recorded, including the two controls, before ship.
- The Django suite stays green; `tests/test_host_tags.py`'s route assertions
  (`:115-142`) are unchanged — a host block tag's body is still Django-parsed.
- `scripts/check_transcriptions.py` passes with no new declared region: no transcribed file
  is edited.
- Classification tests on all three supported Python versions, covering both
  `takes_context` values, varied signatures and `as name` syntax.
- Negative tests, each asserting fallback **before** the callback runs:
  an overridden `{% load %}`; a `Parser` subclass overriding only `find_filter`; a subclass
  overriding only `add_library`, against `{% load %}`; an instance override of
  `compile_filter` installed by an *earlier* host callback in the same parse; a function
  rebuilt with the genuine code object and closure but foreign `__globals__`; a `{% load %}`
  that rebinds a `simple_tag` name to a block compiler mid-parse; `simple_block_tag`, which
  must never classify as stream-free.
- `do_block_translate` (§4.1) compiles correctly and is never classified stream-free.
- Sentinel tests: every listed operation raises; a tripped sentinel preserves the original
  exception rather than replacing it with a repr failure.
- Error-path tests: exception identity restoration (`render.py:223-227`) and the token
  attached to the error are unchanged on the stream-free path, including a boundary error
  carrying no token.

## 12. Non-goals

- Making component-heavy compiles linear (§9).
- Any opt-in restricted contract for body-consuming tags.
- Porting any Python tag into Rust.
- Withstanding deliberate monkeypatching (§5.3).
- Changing `ShellHost.call_tag`'s existing contract for direct callers.
