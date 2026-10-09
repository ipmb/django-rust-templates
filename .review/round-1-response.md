# Response to review round 1

Reviewed: spec `7635600`, M0 `39b9e48`, M1 plan.
Response written against: spec + plan + code as of `a2bbe64`.

Every finding was checked against the pinned tree (`.django/` @ **6.0.9**), not
against `/Users/pete/projects/tmp/django` @ `8ee7341527` (6.0.5a0) — see finding
14. All eight load-bearing claims I could test independently (blocker 1;
findings 3, 4, 5, 6, 7, 9, 12) held against 6.0.9.

Commit map:

| Commit | Contents |
|---|---|
| `21bf29b` | M0 fixes — blockers 4, majors 5, minors 13 |
| `a2bbe64` | M1 plan amendments — major 8, questions (b)(c)(d) |
| this commit | spec amendments — blockers 1–3, majors 6–7, minors 9–12 |

---

## Blockers

### 1. `setup_test_environment()` makes the Python node tree the render path — RESOLVED (spec §5.5)

Verified independently at `django/test/utils.py:108-114` and `base.py:1091`:
`Template._render = instrumented_test_render` is unconditional, and
`NodeList.render` is the join over `render_annotated`. Your correction of my
argument stands: my grep was true and tested the wrong proposition.

**Decision: option (b), extended** — the one you listed second.

- `NodeList.render` and `Node.render_annotated` are ported from Django
  **verbatim**. This keeps the annotation protocol in Python, where Django put
  it and where its statefulness is already right, instead of moving it into Rust
  (your objection to option (a)).
- A shadow node's `render(context)` delegates to Rust for its whole subtree.
  `NodeList.render` iterates top-level nodes in Python; each is one FFI entry.
- Rust renders interior subtrees from its own compiled form and does **not**
  re-enter Python `NodeList.render` for shadow children. The boundary is exactly
  where Django's own `parser.parse()` was called from Python.

Option (c) is rejected: `django.test.utils` is outside the namespace the finder
owns, and shadowing it would mean shadowing `django.test`, which §3 excludes.

Cost is one FFI hop per top-level node per render. §3 says compatibility-first,
so that is the right trade, and it is stated in the spec rather than left
implicit.

Knock-on: §10.3 now marks **M9 as the highest-risk milestone** with a real
done-when, and §10.4 records the obligations. You were right that one line was
not enough.

### 2. The `render_annotated` error-annotation protocol — RESOLVED (spec §5.2)

Adopted your fix essentially as proposed:

- `ExtensionHost::annotate_exception(&self, err: &Error, node: NodeRef, token: &Token)`,
  with the stateful cross-frame semantics documented at the call site.
- `Error` keeps host exceptions **by identity**: `Error::Host(HostError)`, opaque
  to `template-core`, which stores and re-raises but never inspects. The spec now
  says explicitly that flattening a Python exception into a Rust type loses
  `_culprit_node` and the object's identity.

One thing the design adds, which your finding implies but does not state:
because §5.5 keeps Django's `render_annotated` verbatim, Python-owned nodes get
their annotation from Django's own code, and `annotate_exception` exists for the
frames that have **no Python counterpart** — Rust-owned nodes. Both halves are
needed, and `test_nodelist.ErrorIndexTest` (Python tag inside Rust-owned
`{% for %}`) is cited as the test that pins the seam between them.

### 3. `ExtensionHost::call_tag(&mut Parser)` — RESOLVED (spec §5.2)

`ParserHandle` specified: interior mutability (`Rc<RefCell<Parser>>`-shaped),
cheap and copyable, with an explicit contract — a callback may drive `parse`,
`delete_first_token`, `skip_past`, `next_token`; **Rust never holds a direct
`&mut Parser` across a host call** and re-acquires the borrow afterwards.
Re-entrant `parse` returns a `NodeListRef` treated exactly like a `NodeRef`.

All three of your sub-requirements are in:

- `extra_data` is the host's real Python `dict`, not a Rust map.
- Arbitrary attributes proxy to a host-side bag (`do_block`'s
  `parser.__loaded_blocks`).
- The `extra_data`-absent-after-failed-parse case drove a signature change you
  did not ask for but which the test requires: the parse entry point returns
  `(NodeListRef, ParserHandle)`, so the Python shell can do
  `self.extra_data = parser.extra_data` inside *its own* `try`. Generalised in
  the spec: **the shell owns every construct whose observable state differs on
  the failure path.**

### 4. (M0) A green suite does not prove the shim ran — RESOLVED (`21bf29b`)

All three guards implemented, and then **verified by making them fail**, because
a guard that has never failed is not known to work:

1. `sitecustomize` wraps install in `try`/`except BaseException`, prints the
   traceback, and `os._exit(1)`s when `DJANGO_RUST_TEMPLATES=1`. `os._exit`
   rather than `sys.exit` because this runs inside `site` initialisation, where
   an exception is swallowed by the very handler being defended against.
   *Tested:* a deliberately broken `django_rust_templates` on `PYTHONPATH` →
   traceback printed, `exit=1`, `print('SHOULD NOT REACH')` never runs.
2. `run_django_suite.sh` pre-flight: asserts `_shim.installed()` **and** that
   `django.template.exceptions.__file__` contains `_impl`, else refuses to run.
   *Tested:* with the shim off the path → `shim is not installed; refusing to run
   the suite`, `exit=1`.
3. Suite startup prints `engine installed; 25 module(s) still served by Django`.

## Majors

### 5. (M0) Fallback does not survive a partial subpackage — RESOLVED (`21bf29b`)

Fixed as you specified: the finder rebuilds the search path at **every** level,
ours first and Django's second, via the new `_real_subdirs()`. A package we
provide now keeps its Django siblings resolvable.

Your point that `unshimmed()` "reports the opposite of what happens" was the
sharper half of this finding, so it is fixed too, and pinned by a test:
`test_a_partial_subpackage_still_falls_back_for_its_siblings` creates
`_impl/loaders/__init__.py`, asserts our module wins, asserts
`django.template.loaders.filesystem` still resolves, and asserts the meter
reports `loaders` ours while `loaders.filesystem` falls back. Cleanup is
`shutil.rmtree` because importing the package writes `__pycache__` into it.

### 6. `{% include %}` caching and `CounterNode` identity — RESOLVED (spec §5.5)

Specified: materialised **once per `Template` compile**, `NodeRef` identity-stable
for the template's life, reachable **by identity** from Rust. Re-materialisation is
documented as a correctness bug, not a perf issue, with your `"012345678"` →
`"000000000"` failure as the worked example. `test_include_cache` is now an
explicit **M7 gate** in the §10.3 table, not a footnote.

### 7. `ValueResolver` / `call_filter` granularity — RESOLVED (spec §5.2)

- `resolve_bit(&self, current: &Value, bit: &str) -> Result<Value, LookupError>`.
  Rust keeps the loop over bits and constructs the error; the host owns the
  cascade within a bit, because that cascade *is* Python exception semantics and
  cannot be modelled otherwise. `LookupError::Missing { bit, current_repr }`
  carries the host's `repr` so `"Failed lookup for key [%s] in %r"` is built in
  one place, and `LookupError::Reraise(Error)` carries the `@property` re-raise
  and preserves exception identity (blocker 2).
- `FilterFlags { is_safe, needs_autoescape, expects_localtime }` added, with
  `call_filter(..., flags: &FilterFlags, autoescape: bool)`. The spec notes that
  `needs_autoescape` changes the filter's **signature**, which is the detail most
  likely to be lost in a refactor. M4's done-when now names `FilterFlags`.

### 8. (M1) `split_contents` — RESOLVED (`a2bbe64`)

Pulled into **M1**, your first option: it belongs with `Token`, and the §10.3 M1
row now reads "`Token` + `Token.split_contents`, `smart_split`". Plan Task 4
implements it with the `_("`/`_('` rejoining loop and `StopIteration` parity,
validated against Django over 13 corpus cases and 4000 randomised inputs (20497
lines), byte-identical. §10.3 no longer narrows the milestone silently.

## Answers to the four questions

### (a) §10.2 — RESOLVED (spec §10.2), conclusion kept, reasoning replaced

I did not re-commit the spec for this at the time; it is folded in here.

- **My wrong fact is gone.** "engine.py: none" was wrong — you were right that
  `engine.py:7` imports `base` at module level, and that the deferral runs the
  other way (`base.py:148` imports `Engine` inside a method).
- **The `{base, context}` framing is not adopted**, per your point 4: `base` needs
  one name from `context`, `context.py` has zero intra-package imports, so there
  is no cycle. §10.2 now says the binding constraint is **completeness, not
  dependency**, in your words.
- **Your point 3 is recorded as the useful fact**: because `engine.py:7` is
  module-level and unconditional, Django's `Engine` cannot avoid picking up our
  `base` once interposed — which is good news, correctly identified.
- **Your closing point is now the emphasis of the section**: the real reason M9 is
  dangerous is finding 1, not the import graph.

### (b) M1's done-when — RESOLVED (`a2bbe64`)

The randomised parity pass is added, and it is the instrument for the class the
corpus cannot reach — generated input, not more hand-written cases. `verbatim` ×
lineno corpus entries added, since that interaction is where a hand-rolled
scanner is most likely to diverge from `DebugLexer`. Corpus × 2 modes is
byte-identical; `smart_split` validated over 4000 random inputs.

### (c) The `debug`-flag lexer — RESOLVED (`a2bbe64`)

Recorded as an M9 obligation in the plan and in §10.4: the Rust-side merge of
`Lexer`/`DebugLexer` must preserve `token.lineno` and `token.position` exactly,
because `template_debug` reports both. No behaviour is invented at M1 that M9
would have to walk back.

### (d) Missing Review Focus modes — RESOLVED (`a2bbe64`)

Your list is folded in as items 6–11, each with the test that pins it attached to
the owning task.

## Minor

### 9. (M0) `_impl/__init__.py` — RESOLVED (spec §6)

Your conclusion is adopted rather than my framing: **§6's policy was wrong at the
boundary, not the file.** §6 now distinguishes *re-deriving an interface contract*
(required, must be faithful — signatures, defaults, export lists, import order)
from *copying implementation logic* (not done). It names `_impl/__init__.py` as a
transcription with your import-order justification, and `_impl/exceptions.py` as
the fresh-written contrast.

Acted on, not just documented: the file now carries a
`# TRANSCRIBED: django/template/__init__.py (Django 6.0.9)` header telling the
next reader to **diff it against upstream on every Django bump** rather than
review it as original work — your actual ask.

### 10. `unshimmed()` — RESOLVED (spec §7)

Trade-off accepted and stated. Both caveats recorded: (i) finding 5 is fixed, so
it is no longer *positively* wrong on nested subpackages, and a test pins that;
(ii) it measures file existence, not behaviour, so the count is an **upper bound**
on progress — with your `from django.template.base import *` example as the
illustration — and the behavioural check is the differential harness, not the
meter. Your ruling on strict mode is adopted verbatim: what fails early is the
shim *not being installed*, which is always wrong.

### 11. The 1547 baseline — RESOLVED (spec §2)

Adopted your wording's intent. §2 now says the measurement establishes the
**target** and nothing more, that at M0 it is indistinguishable from stock Django,
that it is not evidence about the mechanism, and that the suite only becomes an
acceptance gate at M9.

### 12. The `test_case_sensitivity` skip reason — RESOLVED (spec §2)

You were right and my explanation was wrong. §2 now records it as skipped on
**all POSIX platforms** by an upstream guard, quotes the `skipUnless`, states that
`normcase` is the identity on POSIX so the filesystem is irrelevant and a
case-sensitive APFS volume would not recover it, and classifies it as structural
rather than environmental.

Your coverage-hole point is carried through as a scheduled obligation, not a
note: §10.3's M10 row and §12's risk table both require a project-side
`FileSystemLoader` case-sensitivity test, living outside Django's tree so the
no-edit rule holds.

### 13. (M0) `install()` importing the real package — RESOLVED (`21bf29b`)

Fixed exactly as you suggested: `_real_search_paths()` now uses
`importlib.util.find_spec`, which carries `submodule_search_locations` without
executing the package. `_purge()`'s stale-reference window is closed. Your
`autoreload`/`dispatch_uid` check is noted and relied upon by
`test_install_is_idempotent`.

### 14. The review tree is not the pinned version — NOTED, no action for me

Your own finding 14 asked that every finding be re-checked against 6.0.9. I did
that, since it is my acceptance criterion to meet and I should not accept a
verdict measured against a different tree. `.django/` is at 6.0.9; every
load-bearing claim I re-tested held (blocker 1; findings 3, 4, 5, 6, 7, 9, 12).
No finding was withdrawn and none changed severity, so this round's verdict
stands as written.

The dispatch note for this round names the pinned path explicitly.

---

## What I did not do

- **Option (c) for blocker 1** (shadow `django.test.utils`) — rejected, §3.
- **Replacing §10.2's conclusion** with `{base, context}` — rejected on your own
  evidence, point 4.
- **`strict=True` failing on an active fallback** — rejected on your ruling;
  unusable until M14.

## Verification of this round

- `tests/test_shim.py`: 11 tests, OK.
- Full suite with pre-flight: `engine installed; 25 module(s) still served by
  Django`, `Ran 1547 tests in 3.581s`, `OK (skipped=6)` — unchanged from the §2
  baseline, including after the transcription header.
- Both new guards demonstrated **failing**, since a guard that has never failed
  is not known to work. With the shim off `PYTHONPATH`:

  ```
  shim is not installed; refusing to run the suite
  exit=1
  ```

  With a deliberately broken `django_rust_templates` shadowing the real one, to
  exercise `sitecustomize`'s handler:

  ```
    File "/tmp/scbroken/django_rust_templates.py", line 1, in <module>
      raise RuntimeError("simulated broken install")
  RuntimeError: simulated broken install

  sitecustomize: DJANGO_RUST_TEMPLATES=1 but the Rust engine could not be installed.
  Refusing to run the suite against stock Django, which would report success and mean nothing.
  exit=1
  ```

  In the second case `print('SHOULD NOT REACH')`, which the interpreter would
  have run next, never executed — the process is gone before it can.
