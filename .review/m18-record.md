# M18 — the substitution is gone, and the package is a backend

**Design:** `docs/superpowers/specs/2026-10-09-rust-template-backend-design.md` (M18.0's spec)
**Milestone row:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` §10.3
**Measurements:** `.review/m18-measurements/`
**Commits:** `5612b44` … `14f5fe4` (M17 packaging + M18 spec + M18.0 slice 1) →
`511d79e`, `03038e6` (the engine's namespace) → `8435276` (the shim deleted, `OPTIONS`
is the way in) → **this window's work is uncommitted at the time of writing**.

---

## 1. The headline

The user retired the design in one message: *"No shims. No `_impl`. It's a Python
package with its own namespace. None of that magic should be necessary."* and then
bounded the acceptance criterion: *"the goal is that if I run Django's template
engine against this backend, it should still pass. You may need to do some magic to
get those tests to use this external backend. I'm ok with a little magic only for
testing. It should not touch the main code."*

What that deleted: `_shim.py`, `_impl/`, `install()`/`uninstall()`/`installed()`/
`unshimmed()`'s finder, `scripts/shimpath/`, `scripts/run_django_suite.sh`,
`scripts/run_component.sh`, `tests/test_shim.py`, `tests/test_acceptance_preflight.py`.
What replaced it: `python/django_rust_templates/engine/` (the engine, under our own
name), `python/django_rust_templates/rust.py` (`RustTemplates`), and
`django_suite/` (Django's `template_tests`, vendored by `scripts/gen_django_suite.py`).

**The one claim that has to survive the rewrite is that the 1547 tests still mean
something.** Under the shim they could mean nothing — every module falling through
produced a byte-identical run — and the guards against that were three: a
`sitecustomize` that exited loudly, a pre-flight that asserted a module of ours
resolved to our file, and `tests/test_shim.py`. All three are gone. The replacement
is not a guard but a property of the fixture: `django_suite/`'s tests import
`django_rust_templates.engine` **by name**, so there is no name under which they
could resolve to Django's modules instead. That is a stronger statement than the
three guards made, and it is checkable by reading one import.

## 2. What this window did

Three things, in the order they were forced.

### 2.1 The tree and the gate were inconsistent with each other

`check.sh` at `8435276` invoked `scripts/run_django_suite.sh` and a pre-flight, both
deleted by that same commit, and ran the suite at default verbosity. Two fixes:

* the acceptance block now runs `django_suite/run.py -v 2 template_tests`, and
  **`-v 2` is load-bearing**: at default verbosity the runner prints a dot per test
  and no skip lines, so `assert_suite_outcome.py` cannot attribute the six and
  refuses the healthy run with *"the summary says skipped=6 and 0 skip line(s) were
  attributed to a test"*. The gate was right and the invocation was wrong.
* the two M15 gates (`assert_checkout_pinned.py`, `assert_suite_outcome.py`) are
  wired into the block, and the transcript is printed as a digest.

### 2.2 `EngineHandler` names an engine from its backend path, and ours was not the obvious name

`.django/django/template/utils.py`: `default_name = tpl["BACKEND"].rsplit(".", 2)[-2]`.
For `django.template.backends.jinja2.Jinja2` that is `jinja2`, which is why templates
in the wild usually go unnamed. For the path this package shipped at the time,
`django_rust_templates.backends.RustTemplates`, it was **`backends`** — so an unnamed
entry was reachable as `engines["backends"]`, and `engines["rust"]` raised
`InvalidTemplateEngineError`.

Found by a packaging case failing with `KeyError: 'RustTemplates'` after
`check_packaging.sh` was rewritten to look the engine up by name. **It was a real
user-facing bug, not only a test-fixture one**: the README told a reader to write
`TEMPLATES` with no `NAME`, which works only by accident of that derivation. The first
repair spelled `"NAME"` everywhere — the README (snippet plus a paragraph), every
`TEMPLATES` fixture in `check_packaging.sh` (three: cases 1/1b, 2, 4),
`django_suite/suite_settings.py` (which needs `"NAME": "django"` because two modules
index `engines["django"]`), and the 10-09 spec's §1 snippet, which named a **path that
does not exist** (`django_rust_templates.backend.RustTemplates`, singular).

**That repair treated the symptom, and the symptom is one word in a filename.** With
it, every fixture spells a name the path already implies, and a reader whose
`TEMPLATES` omits `NAME` gets a working engine only by copying ours. So the module was
renamed: `python/django_rust_templates/backends.py` → **`rust.py`**. The path
`django_rust_templates.rust.RustTemplates` derives **`rust`** — the segment a reader
would guess — and an entry that names nothing is reachable as `engines["rust"]`. That
is the convention every Django backend already follows (`backends.django` → `django`,
`backends.jinja2` → `jinja2`); ours cannot mirror it with a subdirectory, because there
is exactly one backend and a directory for one file is the kind of thing this
milestone is deleting.

Every spelling of the old module moved with it, and they are not all prose: the file
itself (`git mv`), `scripts/gen_django_suite.py`'s `OUR_BACKEND` (so the vendored tree
re-derives — `--check` compares 215 files and they match), `tests/test_suite_isolation.py`'s
`OUR_BACKEND`, every string in `scripts/check_packaging.sh`,
`scripts/check_transcriptions.py`'s declared differing hunk, and the two design
documents' snippets. Where `"NAME"` survives a fixture, it survives for that fixture's
own reason and not to dodge the derivation: `suite_settings.py` needs `"django"` so
`engines["django"]` resolves, and `check_packaging.sh`'s case 1 needs `"rust"` spelled
in *both* its settings module and the `sed`-derived stock control, because one
`check_engine` command has to find the engine in both and the control's path derives
`django`. **Case 4 — the standalone source install — now names nothing**, so the
end-to-end proof that the derivation produces `rust` is a wheel built from the source
tree and a `TEMPLATES` entry copied from the README.

### 2.3 The census, re-pinned

`tests/test_collected_tests.py`'s `PINNED` went 492 → 463. The derivation is fully
written into the file's ledger; the short form is that M18 is a net **−29** whose
largest terms are deletions — `test_shim.py` whole (−23), `test_acceptance_preflight.py`
whole (−5), `test_interposition.py` −5, `test_milestone_ownership.py` −3, against
`test_packaging_guard.py` +6 (the guard's door moved from `install()` to
`RustTemplates.__init__`) and `test_compared_path_instrument.py` +1. The arithmetic
closes: `492 − 23 − 5 − 5 − 3 + 6 + 1 = 463`.

Cross-checked against `unittest.TestLoader().discover` per module, and against a
`^    def test_` proxy: **34 modules, zero mismatches**, so the proxy agrees with
discovery everywhere in the tree.

### 2.4 The compared-path count was stale in both directions

The 10-08 spec said `engine/base.py`'s compared path is *"28 functions spanning lines
116-1209"*. Measured from `check_transcriptions.py`'s own `COMPARED_PATH`: **30
functions spanning 117-1212**. Both halves had moved and for different reasons —
the span with M16's three-valued line shift, the count because the instrument wrapped
only `inspect.isfunction` and so could not see `FilterExpression.args_check`, which is
bound with `staticmethod(...)`. `check_transcriptions.py`'s comment already recorded
the correction; the spec had not. This is the second time in this project that a
number was right when written and wrong when read, so the spec's citation was
replaced with the current measurement plus a note naming where it lives.

## 3. The spec revision

Both design documents described the deleted mechanism in the present tense — 43
stale references in the 10-08 design, 26 in the 10-09 one. The treatment was not a
rewrite: §10.1/§10.3/§10.4 are the *record of how the milestones were sequenced*, and
rewriting them would destroy evidence. Instead:

* a new **§0** telling a reader how to read the rest, and a lead-in to §10 doing the
  same for the milestone ledger;
* targeted in-place amendments to §1, §2, §3, §4, §5.4, §5.5, §6, §7, §8, §9, §12, §13;
* **§7 split**: §7.1 is the backend as it is (the marker subclass and why, the
  `_require_django` door, the named `BaseEngine.__init__` and the `KeyError` that
  forces it); §7.2 is the finder, marked historical, kept because §10.3's gates
  cannot be read without it;
* §5.4 retitled off "Re-exposes `django.template.*`", §5.5 off "The shadow tree" —
  the name described a second tree wearing Django's names, and there is no shadow now.

## 4. A finding inside the code, not the prose

`python/django_rust_templates/rust.py`'s docstring said *"all three of
`django.template.autoreload`'s receivers return early unless it is"*. `autoreload.py`
has **two** `@receiver` functions. The three is the count of `isinstance(backend,
DjangoTemplates)` sites — `get_template_directories` once, `reset_loaders` twice —
and the two receivers call those two functions. Three checks, two receivers; the
sentence had one number standing for the other. Corrected in both `rust.py` and
the spec, with the distinction written out, because the tell (a count that matches a
nearby population's size) is the same one this project has been caught by before.

## 5. Residue

* **The plans are stale and are meant to be.** `docs/superpowers/plans/` names
  `run_django_suite.sh`, `shimpath`, `DJANGO_RUST_TEMPLATES` throughout. They are
  dated records of how each milestone was executed; the anchor gate does not scan
  them and `NOT_SCANNED_BECAUSE` gives the reason. The M18.1 plan also still names
  `django_rust_templates.backends` as the backend path, which §2.2 changed; left,
  for the same reason and by the same rule.
* **`OUR_OWN` is a refusal list, not a resolver, and its prose said otherwise.**
  `scripts/check_python_anchors.py`'s block comment described a bare `name.py:N`
  matching one of the four as resolving "to the repo root"; the branch returns
  `ROOT / name`, and all four live under `scripts/` and `tests/`, so it raises
  every time. Measured rather than read: `resolve("parser.py")` raises
  `SystemExit` whose message is ``"`parser.py` is in OUR_OWN but is not at
  <root>/parser.py"`` — the branch's own second arm, not a resolution. What it
  *does* is keep the name out of the checkout's
  search, which is the protection the neighbouring test is about — so the
  behaviour is defensible and the sentence was not, and **the sentence was fixed
  and the behaviour left alone**. Whether the branch ought to resolve (by
  searching its own roots for a unique hit) is not decided here: `--frozen`
  refuses a bare `:N` regardless, so the branch is close to unreachable, and
  changing it is a change to the gate rather than to a comment. Found by touching
  the comment for §2.2 and reading what it claimed.
* **`tests/milestone_ownership.py` keeps the name `unshimmed()`.** The function
  survives (`:440`); what changed is what it reads — two source trees walked
  directly rather than a finder asked what it would serve. A rename was considered
  and deferred: the name is now inaccurate, but every row and comment that cites it
  is accurate about the *function*, and a rename would have touched the 10-08 spec's
  §3 in the same breath as this revision.
* **§10.3's ledger still says `unshimmed()` where it means the meter**, and still
  quotes counts (1,421 anchors, 442 unit, 775 cargo) as of each milestone's close.
  Deliberate: those are dated measurements, and §10's lead-in now says so.
* **No wheel is published**, and the engine is slower than stock. Both are stated in
  the README and in §12's risk table.
* **M18.0 slice 2 is not built.** `{% for %}` in the Rust walk, with a Rust-side
  overlay scope. Slice 1 built the floor and its gate; the for-loop is the fork worth
  roughly forty points, and the two arms measured ~23% ahead and ~19% behind.

## 6. What the reviewer was asked to attack

Dispatched to `django-rust-templates-a8`, on this working tree (uncommitted):

1. §7.1's three claims about the backend — the marker subclass, the two `isinstance`
   callers, and whether the `KeyError: 'OPTIONS'` reason for naming
   `BaseEngine.__init__` is the real one or a plausible rationalisation.
2. §4's `EngineHandler` alias claim, against `utils.py` and every fixture.
3. The three number corrections in §2.3, §2.4 and §12 — each a place a *new* wrong
   number could have been introduced while removing an old one.
4. The §0 framing instruction: is there anywhere that "read the name as its
   successor" produces a false reading rather than a translation?
5. §7.2's claim that M18 replaced three procedural guards with one structural
   property — or whether that is a story that would survive the guards' deletion
   even if it were false.

## 7. The round it came back with

Answered on 2026-10-09, against the same uncommitted tree. Four of the five came
back as measurement, and the results split three ways.

**Confirmed as stated.** §7.1's `KeyError` reason is real, not a rationalisation —
the mechanism is four lines of the pin (`DjangoTemplates.__init__` pops `OPTIONS`
from the dict it is handed, and the parent's `pop` has no default). The
`EngineHandler` alias reproduces exactly: `django_rust_templates.backends.RustTemplates`
→ `'backends'`, `django.template.backends.django.DjangoTemplates` → `'django'`, and
every fixture carried a `NAME`. (Both spellings are as they were when this was run:
the module is `rust.py` since §2.2 and the derivation now gives `'rust'`, which is
the point of that rename, and the fixtures carry a `NAME` only where §2.2 gives each
one a reason.) The compared-path correction reproduces at
`base.py, 30 functions, span 117-1212`.

**Refuted by a search I should have run before dispatching.** §7.2 was said to rest
on nothing — "nothing checks the property it asserts", so a second bad row "would
arrive green exactly as the first did". That is false, and the check is in the
generator, not missing: `scripts/gen_django_suite.py` **derives** every file under
`django_suite/template_tests/` and `django_suite/i18n/`, `--check`
(`scripts/check.sh:79`) re-derives the tree and diffs it, and `rewrite()`'s own
`_verify` refuses a rewrite that renamed something Django's, one that left a name of
ours pointing at Django, and the two spellings of the backend class its import rule
cannot follow. The `test_partials.py` counterexample the reviewer offered was fixed
by **adding a rule to the generator** (`8435276`), not by hand-editing the file — and
the generator's comment already recorded the very blindness the reviewer named
(`ours()` is keyed on `engine/<rest>.py`, and the backend lives outside `engine/`, so
"a second such component would inherit the same blindness"). The reviewer's
subsidiary measurement was wrong in the other direction too: `from django.template…`
in `engine/` is **5**, not zero.

**The one thing that was right, and it was right about a different file.** The
proposal — assert the vendored tree's Django imports against a declared set — turned
out to be worth building, not because the generator is missing it, but because the
generator does not cover `django_suite/suite_settings.py`: `TREES` names
`template_tests/` and `i18n/`, `check()` walks only those two subtrees, so the file at
the suite's root is outside both the diff and the "in the tree but nothing in Django's
suite produces it" sweep. **That file names the `BACKEND` all 1547 tests run
against.** Renaming its one string to `django.template.backends.django.DjangoTemplates`
turns the acceptance run into a test of stock Django, and every gate in `check.sh`
stays green — including `gen_django_suite.py --check`. Built as
`tests/test_suite_isolation.py` (**+6 rows** at that point, `PINNED` 463 → 469; §8
below adds a seventh, → 470, and then two more — the §3 predicate and its control —
→ 472, nine rows in all).

## 8. What this window changed after the review

* **`tests/test_suite_isolation.py`** — new. Two halves. `SuiteSettingsTests` reads
  `suite_settings.py` statically with `ast` (importing it would run
  `settings.configure`), asserting every `TEMPLATES` entry names this package and
  that the entry `engines["django"]` resolves is ours. `VendoredImportTests` asserts
  the vendored tree's `django.template*` imports are exactly a **declared set of seven
  adapter statements**, keyed on `(path, module, names)` so a second file importing
  `django.template` for an unclassified reason fails. Both classes carry a control
  over fabricated input, and both were confirmed to fire by mutating the real tree —
  the `BACKEND` string, and a fabricated `from django.template.base import Node` —
  each restored byte-exact and checked by sha256.
  A **seventh** row was added in the review round, `PINNED` → 470:
  `test_every_declared_adapter_is_upstreams_own_import` re-derives each declared
  adapter against `.django/` and asserts every one is an import Django's own file
  makes. It exists because the declared set *reads* like a judgement — seven imports
  of the package whose whole point is not to depend on it — and the true answer is
  that nobody decided. **Two of the seven** are what is left of a parenthesised
  upstream block the generator **split** (`from django.template import (Context,
  Engine, …, loader)` → a statement of ours plus `from django.template import
  loader`), so the survivor reads as if written here; the other **five are untouched**,
  in upstream's own shape. A first draft of that docstring said "six of them arrive by
  splitting" — a number reached for rather than measured, wrong by three, and caught
  by re-deriving it against the pin rather than by rereading the sentence. The row's
  own control is that `.django/` must have been read at all, since an absent checkout
  makes every entry "not upstream" and reads the same as a genuine violation.
  **The next round found that row not doing the job, and the fix is one token.**
  It built each pair's *name* from the pin and its *module* from the statement under
  test — and every module the walk yields is a `django.template` path by
  construction, so the module half of the key was in the set whatever the pin
  contained. `from django.template import mock` passed a row whose whole job is
  refusing it. It is `node.module` now, and the two constructions were compared over
  the real tree and pin before the fix was believed
  (`.review/m18-measurements/probe_adapter_module_axis.py`, with its transcript):
  three pairs the old key admits and the new one refuses, and **no** change on the
  seven declared adapters.
  An **eighth** row and a ninth as its control close what the repair cannot reach,
  `PINNED` → 472 (the pair is the last of the module's nine):
  `from django.template.base import Node` is `.django/tests/template_tests/
  test_custom.py:4`, so it is upstream's own import *and* the engine, and the seventh
  row — however correctly it reads the pin — waves it through. The eighth asks §3's
  question instead (`PLUMBING` is `milestone_ownership.NOT_THE_ENGINE`, read and not
  restated) and refuses it, with `from django.template import engines` licensed by a
  value **derived from the pin** rather than typed here: the one name
  `django/template/__init__.py` binds to an `EngineHandler()` call. The ninth — the
  control, and a row rather than a comment because §8's other predicates are all
  driven by fabricated input too — runs that predicate over eight cases, one of them
  that upstream line verbatim.
* **`.review/m18-measurements/m18_census.py`** — the third quantity read
  `_impl/parser.py`, which M18.1 renamed to `engine/parser.py` (`511d79e`). It had
  been printing `NODE_CLASSES entries 0` and a false
  `(53, 44, 40) -> (53, 44, 0)` alarm ever since, and listing all 39 production
  literals as "not constructible". The table never moved: 40 keys either way. Now
  reads both spellings.
* **The 10-08 design's §5.5** — "43 classes' worth" was the pre-correction number a
  line-oriented search returns (the multi-line call at
  `crates/template-core/src/parser/tags/mod.rs:219`). Replaced with the three
  quantities and the instrument.
* **The 10-08 design's §7.2** — the structural claim rewritten to say *what* is
  structural, naming the generator's refusals and the `--check` diff, and stating the
  one blindness and the residual (the adapters reach Django's code and nothing
  asserts they route through the configured backend except the suite going green).
* **The 10-08 design's §5.5 and §13** — a clause the reviewer asked for: the namespace
  moved, the *protocol* did not. "Ours" means the name a caller imports; `.nodelist`,
  `render_annotated`, `token` and `origin` keep Django's shapes.
* **The 10-09 design's §2, §5 and §6** — per-section M18 notes, because §0's
  instruction translates *names* and §2's argument is about a *mechanism*. §5's note
  records a prediction the section got wrong: `host.py`'s four ambiguity helpers were
  said to go "only if the shim goes", the shim went, and all four are still there
  (`python/django_rust_templates/host.py:519`,
  `python/django_rust_templates/engine/base.py:829`) — the ambiguity they answer is
  "Django's object or ours?", which is a question about an object and not about a
  mechanism. **The note claims no more than that, and the review was right to
  narrow it**: the first draft said the ambiguity "does not depend on the activation
  mechanism", which is a counterfactual — there is exactly one mechanism now, so
  there is no second arm to measure. What the counts license is that the deletion
  did not silently retire the four; the rest is an argument, and the note now says
  which is which.
  The note's original support for "still on the hot path" was a **two-site audit**,
  which is an argument and not a measurement; it is now a count. Wrapping each helper
  in a counter and running the suite in that process
  (`.review/m18-measurements/probe_host_helpers.py`, kept with its transcript) gives
  `django_own_tags` **4361**, `django_own_filters` **2598**, `effective_autoescape`
  **2536**, `effective_use_tz` **2536** over the 1547. The falsifier is a zero. The
  `effective_*` pair being equal is the probe checking itself: `engine/base.py` reads
  both unconditionally on adjacent lines of one arm, so a difference there would mean
  a wrapper had not landed.
* **The 10-08 design's §7.2, second pass** — the residual paragraph gained the
  **inherited-layering** distinction the reviewer asked for, and then a correction
  that the reviewer's own framing had not gone far enough. The engine imports
  `django.template` five times; a first draft called two of them inherited and left
  the other three unexamined. Measured, **four of the five are inherited** and only
  one is a decision: `engine/engine.py`'s two local imports are upstream's own, the
  comment above them included (`django/template/engine.py:105-107`) — **and "comment
  and all" is a claim about those lines, not about the `get_default()` around them,
  which ours extends** with a third import and a second arm; `engine/loaders/cached.py` takes
  `copy_exception` from `django.template.backends.django`
  (`django/template/loaders/cached.py:9`); `engine/loaders/app_directories.py` takes
  `get_app_template_dirs` from `django.template.utils`
  (`django/template/loaders/app_directories.py:6`). Closing any of them would be a
  divergence from Django, not a cleanup. The last is load-bearing in a way worth
  naming: `get_app_template_dirs` caches, and
  `django.test.signals.update_installed_apps` (`django/test/signals.py:50`)
  invalidates that cache by calling `cache_clear()` **on that function object**, so a
  copy would hold a second cache no receiver could reach — the whole of
  `test_loaders.AppDirectoriesLoaderTests.test_not_installed`. The **fifth** is the one
  deliberate edge and points the other way: `engine/exceptions.py` re-exports
  `TemplateDoesNotExist` and `TemplateSyntaxError`
  (`python/django_rust_templates/engine/exceptions.py:38`) so our parser raises the
  *same class object* user code catches. That is precisely the edge "our own
  namespace" would have one delete. **What a deletion would leave is an
  `ImportError`, not two classes sharing a name** — the first draft had the
  consequence of the *replacement* standing in for the consequence of the deletion:
  re-exporting `django.template`'s class instead is what would put two same-named
  classes in the tree, and nobody proposed that. Five new manifest rows, `--seed` then corrected by hand: the seeder
  quoted `get_default()` from the surrounding prose, and the span it had to appear in
  was the import lines.

---

## 9. The build profile is half of a benchmark, and it was the half nobody printed

This section is here because **a number was measured, believed, and written into
the README and the status page before it was checked against a second run** — and
it was wrong in a way that looked like rigour.

**What happened.** `scripts/bench.py` had gone dead with the substitution M18.2
deleted: both of its documented invocations ran `DJANGO_RUST_TEMPLATES=…` with
`scripts/shimpath` on `PYTHONPATH`, and neither exists. A run of the re-based
harness (now `--side`, mandatory, with a refusal when the built engine's module is
not the one asked for) produced **2.4× to 6.0×** — read at the time as
"the port has regressed since M16's 1.35×–1.85×, and the old range was flattered
because its control shared this package's `i18n` library". Two claims, and the
second is false on the evidence: `.review/m16-measurements/transcript-bench-stock.txt`
shows the M16 control with a genuine stock engine at 1229.6 µs, which is today's
1232.9 µs to 0.3%.

**What was actually different.** The extension. `pip install` compiles
`--release`; `maturin develop` — what a checkout runs, and what every plan's
"run `maturin develop` after any Rust change" boilerplate installs — builds the
**dev** profile, and `Cargo.toml` sets `[profile.release]` to thin LTO with
`codegen-units = 1` while leaving `[profile.dev]` unset. The file in the tree was
**9,386,112 bytes, byte-for-byte `target/debug/lib_native.dylib`**; the release
build is **2,641,824**. Rebuilding with `--release` and re-running the same
harness on the same tree, with the same four output hashes, gives **1.341 / 1.347
/ 1.411 / 1.820**, against M16's **1.351 / 1.362 / 1.402 / 1.854** — within **1.9%**
on every row, the largest gap being `tiny/parse` (1.834% of M16's figure, 1.868% of
today's) and the smallest 0.642%. So the published range was right, the regression
was not real, and the 2.4×–6.0× was a faithful measurement of the wrong binary.

**The instrument now says which build it measured.** `native_artifact()` prints the
installed extension, its size, its `sha256`, and which build that hash corresponds
to — *inferred*, not declared: Python cannot read `cfg!(debug_assertions)`, so the
content is compared against `target/debug/lib_native.*` and
`target/release/lib_native.*`, and a file matching neither reports `unknown` rather
than guessing. Content rather than size, which was the first version: two builds of
different source can share a length, so a size match guesses and this line exists to
be exact about the file the table was measured on. What a hash between those two
files **cannot** do is report *freshness*: measured, a source edit changes neither
the installed file nor the `target/` candidate it is compared against, so the match
survives the edit. That much stands, and the wider sentence I first wrote beside it
— that *identification cannot see freshness* — is too broad, which the reviewer
caught: identification *between the artifact and the source* would see it, and the
shape is implementable (a digest recorded at build time and compared to the source
now) and simply not worth building. What makes the build below the right fix is a
sharper statement than either, and it is the reviewer's: **cargo's incremental
fingerprint already is the source-match assertion, and `maturin develop` is how the
gate reads it. The build does not substitute for the check; it *is* the check, run
rather than inferred** — at the measured price of ~1.2 s when the extension is
current. There is no cheaper route to the same claim, and no `build.rs` or embedded
source constant in either crate that would let an assertion make it without building. Both transcripts are on disk
so the pair can be read side by side rather than trusted:
`transcript-bench-release.txt` and `transcript-bench-debug.txt`, the second of
which is the same measurement as `transcript-bench-rebased.txt` taken again once
the harness could name its build (2.34×/2.49×/4.69×/6.01×, agreeing with the
earlier 2.4×/2.6×/4.8×/6.0×, which is what makes the older transcript's numbers
attributable after the fact). The three transcripts carry `native:` lines or an
annotation saying what they were measured on; a transcript that does not name its
build is the same defect as the table it holds.

**What this leaves open, and it is the uncomfortable half.** The tree's extension
is whatever was last installed, so "the gates are green" and "the acceptance
suite passes" are statements about *some* build, and it was the debug one for
anyone following the plan boilerplate. That is the same shape as the project's
rule that a claim about state needs its moment, and it is a **third axis** beside
the commit axis and the process axis: *which build*. Nothing here is a correctness
finding — the four sha256 agree, so both profiles render the same bytes — but any
future performance claim, and any future "why is the suite slow" question, has to
name it. The `.so` in the tree at this record's close is the **release** build,
because the last thing this sitting did was rebuild it to take the transcript —
and the gate no longer *depends* on that being true, which is the subject of the
last paragraph of this section.

**The residue, stated rather than left to be found.** `native_artifact()`'s
inference is **driven by no row**, and that is a measurement rather than an
impression: `git grep -n native_artifact -- tests/ scripts/` returns two hits, both
inside `scripts/bench.py` itself (`:189`, `:353`), and none under `tests/`, while
other `scripts/`
instruments are covered (`tests/test_anchor_manifest.py` loads the anchor checker,
`tests/test_compared_path_instrument.py` drives the compared-path probe), so this is
a gap about *this* file and not about the directory. Nothing asserts that a debug
extension reports `debug` or that an unmatched file reports `unknown` — the pair of
transcripts above is the only evidence, and they are evidence about one machine on
one date. The same is true of the `--side` refusal beside it, which is why the two
are worth naming together: both are instruments that fail *toward* a wrong answer
being legible rather than toward a green run, and neither can fail the build. A row
would have to import `bench.py`, which calls `filter_probe.ensure_settings()` at
module scope, and the settings rule (`tests/test_settings_configuration.py`) is that
exactly one file in `scripts/` configures them; a row is therefore a real decision
and not an oversight, and it is left as one.

**And the gate did not build it either — the larger finding, now fixed in this
tree.** `scripts/check.sh` ran `cargo test`, `cargo fmt --check` and `cargo clippy`
and no `maturin` step at all, measured rather than assumed. So the 472 unit rows and
the 1547 acceptance tests exercised whatever extension was installed in `.venv`,
which could be — and was — a build nobody ran this session. The two cases are not
the same and should not be merged: a build *profile* costs only speed — both
profiles render identical bytes, which the four sha256 above say — while a *stale*
extension costs correctness, because a row about Rust behaviour is a row about that
binary, and a binary older than the change under test passes rows the current source
does not satisfy, with `cargo test` green beside it because that is the one step in
the gate that reads the source. `check.sh` now runs **`maturin develop --release`
before the Python steps**: the subject is pinned by construction rather than by an
inference about freshness — an inference `native_artifact()` cannot make, measured
above — `--release` is the profile `pip install` builds and these figures describe,
and the step is incremental at ~1.2 s when the extension is current and ~11 s when
the source has moved. `tests/test_suite_isolation.py` pins the invocation **and its
position**, so a build placed after the steps that need it cannot satisfy the row.

**Three things that first version got wrong, and the reviewer found all three.** The
verdict on the fix is PASS; these are hardening items, and the second is the one that
closes the residue the whole round was about.

*The position row pinned a proxy.* It asserted the build preceded
`run_unit_tests.sh`, and that is equivalent to "before every consumer" only while the
unit run happens to be the first such line — which the row did not assert.
`differential.py`, `check_transcriptions.py` and the acceptance run all import
`_native`, so moving any of them above the build reopens the gap with the old row
still green. It now asserts the build precedes **every** line invoking the venv's
Python or a script under `scripts/` (`source env.sh` excepted by name, since it sets
the environment the build itself needs), with a positive control naming the
population first — the assertion is that a *late* set is empty, and an empty set is
also what a population that matched nothing would produce. **The four mutations the
first version was tested against were all edits to the build line and moved no
consumer**; a check written against a stand-in passes every mutation of the stand-in,
which is the same shape as the three wrong-side keys one layer up. Re-tested with
mutations that move consumers — a consumer inserted above the build, the build moved
to the end, the build removed, and the consumer spelling drifted so the population
empties — **all four now fail the row**, baseline green and the file restored
byte-identical.

*Nothing asserted the build landed where the suite imports from.* The row is textual
and cannot fail if `maturin` succeeds and installs somewhere the tests do not read —
the residual form of the bug. `scripts/assert_native_is_the_built_artifact.py` is the behavioural
half, one line after the build: it resolves `_native` the way a test does and requires
it to be byte-identical to an artifact under `target/release`. Green on this tree —
the imported `_native.cpython-314-darwin.so` is `sha256:1b3bf993eb2d136d` `==`
`target/release/lib_native.dylib` — so it costs one import, and it converts *we ran a
build* into *the artefact under test is the one we built*. Its negative branch is a
function rather than a branch inside `main()`, so `tests/test_native_is_the_built_artifact.py`
drives it with three rows instead of leaving it as the residue §9 complains about for
`native_artifact()`: a foreign build, the same-length file a size comparison would
admit, and the glob's `.d` exclusion. The fixtures are fabricated rather than read
from `target/`, because a row requiring a release build would fail for anyone running
`run_unit_tests.sh` after a plain `maturin develop`, which installs the *debug*
profile — and that is the correct loop.

*"installed in the venv" named the wrong place.* Measured, `_native.__file__` is **the
source tree** — `python/django_rust_templates/_native.cpython-314-darwin.so`, reached
through the editable install — and `django_rust_templates.__file__` is the source tree
too. Someone chasing a stale extension on that sentence looks in `site-packages`,
finds nothing, and concludes there is none. Corrected where it appeared, in
`scripts/check.sh`'s comment and the row's docstring.

**One more residue, small and checkable.** `.review/m16-record.md`'s numbers were
measured under the finder, on `_impl`, before M18.1 renamed the package and M18.2
deleted the substitution; they reproduce on M18's tree by re-running the harness,
but the *invocations in that record's §1 no longer work at all*. That record is a
dated record and is not rewritten; this paragraph is the pointer it needs.

---

**Counts at close:** `scripts/check.sh` exits **0** with `all checks passed`; unit
**472**; cargo **787**; the anchor census `1477 anchor occurrence(s) checked, 869
cited of 869 manifest entr(ies), 0 failure(s)`; the acceptance run `Ran 1547 tests …
OK (skipped=6)`.

Two of those five moved and three did not, and the difference matters because only
one of the two movements is M18's own arithmetic. **Unit** is. `PINNED` was 463 when
M18 opened, after the deletions §2 derives (492 − 23 − 5 − 5 − 3 + 6 + 1);
`tests/test_suite_isolation.py` did not exist yet, it holds **nine** rows, and they
arrived 6 / +1 / +2 across the review rounds — 463 + 9 = 472. That closes as
arithmetic over two recorded numbers rather than as a per-file diff, which is the
weaker of the two routes and the one actually taken. **The anchor census** is not
M18's to claim: it was 1472 / 867 at M16's close and is 1477 / 869 now, and the five
occurrences and two entries moved somewhere inside `m17:`–`m18.2` with no
per-sitting attribution available from here, so the two endpoints are the honest
statement and a delta would not be. **Cargo (787)** and the **acceptance count
(1547)** did not move at all, which is the expected shape for a round that is Python
rows and prose.

**After close, in review — so the 472 above is the close and not the current figure.**
The unit count moved twice more, and neither movement is M18's arithmetic. The round
that gave the gate its missing build took `PINNED` **472 → 474** (two rows in
`tests/test_suite_isolation.py`: `GateBuildTests`, and the closure's two-hop control
that the pin's own map cannot drive). The round that hardened that fix took it
**474 → 477** (three rows in the new `tests/test_native_is_the_built_artifact.py`, driving
`scripts/assert_native_is_the_built_artifact.py`). Both deltas carry their second measurement in
`tests/test_collected_tests.py`'s ledger, so `477` is the current figure and the three
numbers reconcile there. Cargo **787**, the anchor census **1477 / 869** and the
acceptance count **1547** did not move across either round.
