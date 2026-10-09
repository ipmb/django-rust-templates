# M10 recon — interposing `context`, `engine`, `utils`, `loaders`

Written 2026-10-09, before M10's plan. Pinned tree: `.django/` at **6.0.9**
(`ea10f2e`, "Bumped version for 6.0.9 release"). Closed predecessor: M9 at
`97c366e`, `unshimmed()` **23**.

This recon's first job was to size the milestone, and sizing it turned up a
scope error in §10.3 before any code was written. §1 is that error. §2 is the
meter arithmetic it changes — and, once checked against the whole meter rather
than the one name the row is ambiguous about, three more names with no owner at
all. §3 is the surveyed surface, §3a the blast radius it sits on, and §3b the
pre-flight that interposed the whole milestone verbatim in a scratch universe
and found it green. §4–§6 are what the milestone has to decide, with the
measured ones marked as measured and the surveyed ones marked as surveyed.

## 1. §10.3's M10 row names five modules; one of them does not exist

The row reads:

> | M10 | Interpose `context`, `engine`, `utils`, `loaders`, `origin` | `test_context`, `test_engine`, `test_loaders`, `test_origin`; project-side `FileSystemLoader` case-sensitivity test (§2) |

**`origin` is not a module at 6.0.9.** `django/template/origin.py` — the module
the row's name implies — is absent, and `Origin` is defined in
`django/template/base.py:113`, which **M9 already interposed**. Measured five
independent ways, because a claim that a module does not exist is exactly the
kind this project has learned to distrust:

1. `ls .django/django/template/origin.py` → *No such file or directory*;
2. `find .django/django/template -maxdepth 1 -name '*.py'` → fifteen files, no
   `origin.py`;
3. `grep -rn '^class Origin' .django/django/` → two hits, `template/base.py:113`
   and `template/backends/jinja2.py:81` (the latter a *different*, jinja2-local
   class), so no third definition is hiding in a package;
4. `importlib.util.find_spec("django.template.origin")` → `None`;
5. `unshimmed()`'s own list of 23 — the meter that decides milestone progress —
   does not contain `django.template.origin`, because it enumerates the real
   tree's files.

The fifth is the one that matters for planning: **the meter was already telling
the truth and the milestone table was not.** It is the same class of error as
M9's `964 → 961` (`.review/m9-recon.md` §1b): a number in a document, correct
for the tree it was written against, that no measurement had re-checked. There
`origin.py` was a *count*; here it is a *name*.

Unlike M9's, this one costs nothing to fix and has no blast radius — no
transcription, no anchor, no plan cites `django.template.origin` as a module.
Every "origin" in the repo outside `.django/` is the `template.origin`
*attribute* (`base.py:1060-1063`, `_impl/parser.py:470`, the anchors at
`scripts/anchors.txt:194-195`), which is unaffected.

**Consequence for M10's plan: four modules, not five, and §10.3's row is
corrected in the same commit that closes M10.** Whether `origin` was ever a real
module in an older Django is not worth asserting from here — the pinned
checkout is a shallow clone (`git rev-parse --is-shallow-repository` → `true`),
so its history cannot date the change, and the 6.0 release notes do not mention
it. The correction should say what is measured (*absent at 6.0.9; `Origin` lives
in `base.py`*) and not guess at when.

## 2. The meter arithmetic: the row does not partition the meter

`unshimmed()` counts names, and a package contributes one name per module under
it, so *module* and *meter entry* are two different counting units. §10.3's rows
use the first and the progress number uses the second.

For M10 the difference is 5 → 9: `context`, `engine` and `utils` are one name
each, `loaders` is six (`loaders` itself plus `base`, `filesystem`,
`app_directories`, `cached`, `locmem`), and `origin` is **zero**, because §1. So
M10 takes the meter **23 → 14**.

**But the row sits one character from a second name, and it does not say which
it meant.** `unshimmed()` lists `django.template.loaders` (the package, six
names) *and* `django.template.loader` — the singular **public facade**:
`get_template`, `select_template`, `render_to_string`, `_engine_list`, which
delegate to `engines.all()`/`engines[using]` and exist to accumulate the
`TemplateDoesNotExist` chain. §10.3's M10 row says `loaders`, plural. If it
meant the package only, `loader` is unowned and M10 is 23 → 14; if it meant
both, M10 is 23 → 13.

The evidence for which: `loader` is framework-coupled in exactly the sense §6
gives for keeping a module in Python — it reaches templates through `engines`,
i.e. through settings — and its only importer *inside the template package* is
`response.py`, which M13 owns (`forms/renderers.py` and
`contrib/admin/templatetags/admin_list.py` import it from outside). **So
`loader` belongs with M13 and M10 is 23 → 14** — but that is a judgement the row
should state, not leave the next reader to resolve to whichever they thought of
first.

### The rows do not partition the meter at all — three names have no owner

That judgement is worth checking for the whole meter rather than for the one
name the reviewer happened to notice. Mapping all 23 names in `unshimmed()`
against §10.3's rows:

| owner row | meter names | count |
| --- | --- | --- |
| M10 | `context`, `engine`, `utils`, `loaders` + 5 submodules | 9 |
| M11 | `defaulttags`, `defaultfilters`, `loader_tags` | 3 |
| M12 | *(none)* | 0 |
| M13 | `library`, `backends` + 5, `response` | 8 |
| **no row at all** | **`autoreload`, `context_processors`, `loader`** | **3** |
| | | **23** |

M12 is zero rather than omitted from the count: `i18n`, `l10n`, `static`, `tz`
and `cache` are `django/templatetags/*` libraries, outside the `django.template`
namespace the meter covers, so that milestone moves tag *bodies* and retires no
meter entry.

**The three unowned names are verified, not eyeballed**: `autoreload`,
`context_processors` and `loader` appear nowhere in §10.3's rows or in §10.4's
obligations — and `context_processors` appears **nowhere in the spec at all**
(0 matches). `autoreload` has one mention, in the substitution section, and it is
about `from . import autoreload` registering signal receivers, not about who
interposes it.

Since M14 is "`unshimmed()` empty", those three are M14's by default, which
makes M14 carry three unplanned interpositions — including `context_processors`,
the module that reads the *project's* settings for CSRF and request processors,
which is framework-coupled in §6's sense and is the kind of thing a milestone
table exists to schedule. This is the `origin` defect generalized: not one wrong
name, but **the rows and the meter being two different sets with nobody
asserting they are equal**. The fix is the same shape in both cases — make the
mapping explicit and checkable — and it is the same artifact the reviewer
proposed for the `_impl` naming rule (§6).

`utils` here is `django/template/utils.py` — the `EngineHandler` module — **not
`django.utils.*`**. §3 of the spec already carries this warning explicitly
(the paragraph beginning *"M10's `utils` is `django/template/utils.py`"*), and
it exists because an earlier draft and M4's plan both misread it. It is repeated
here only so a reader of this file does not re-make it.

## 3. What each module is — surveyed, then to be verified per task

**Status: `context`/`engine`/`utils`/`loaders` were surveyed by a subagent, whose
report is data and not authority.** The line ranges and importer lists below are
its claims; the ones M10's plan leans on must be re-read off the tree in the
task that uses them, the way M9's Task 5 re-read `base.py:1014-1026` rather than
trusting a prose citation. What *has* been verified directly is noted inline.

- **`context`** (`django/template/context.py`, 307 lines): `BaseContext` (dict
  stack), `Context` (+ `render_context`, scalars), `RenderContext`,
  `RequestContext`, `ContextDict`, `make_context`. *Verified*: `Context` holds
  no lazily-created built-ins in this version — `BaseContext._reset_dicts`
  builds `{"True": True, "False": False, "None": None}` as a local and sets
  `self.dicts`; there is no `self.builtins`. So the module is **pure Python
  state and fully interposable**, with one runtime-dependent corner:
  `RequestContext.bind_template`, which reaches
  `template.engine.template_context_processors`.
- **`engine`** (234 lines): one top-level symbol, `Engine`, whose loaders are
  named by **string path** (`...loaders.filesystem.Loader` etc. at its lines
  38/40/41) and resolved through `import_string` — *verified* in that the
  survey's claim matches §3's description of `EngineHandler`. That is why
  interposing `loaders` needs no import surgery: nothing imports the loaders by
  name.
- **`utils`** (110 lines): `EngineHandler`, `InvalidTemplateEngineError`,
  `get_app_template_dirs` (an `lru_cache`d function reading `django.apps.apps`).
- **`loaders/`**: `__init__.py` is **0 bytes** — *verified by `wc -c`*. That
  matters for the finder: an empty `_impl/loaders/__init__.py` is what the
  finder needs to serve the subpackage with our directory first and Django's
  second (`_shim.py`'s `find_spec`, the "package we provide needs its own search
  path" branch), and an empty file is a faithful transcription of an empty file.

## 3a. The blast radius, measured — 1232 of the 1547, and distributed nothing like the module names

Instrument: `.review/m10-measurements/m10_measure_module_blast_radius.py`, run
the way the suite is run (`python -I …`, paths inserted by the script because
`-I` drops `PYTHONPATH`). It wraps every function *defined in* each target
module — labelled `Class.method`, not `method`, because `__init__` is four
classes in `context` and six in `loaders` and one label over several call sites
is precisely M9's finding 3 — and attributes each call to the running test.

| module | tests that reached it | calls |
| --- | --- | --- |
| `context` | **984** | 95,051 |
| `engine` | **1166** | 41,987 |
| `loaders.cached` | 1038 | 25,249 |
| `loaders.locmem` | 974 | 10,854 |
| `loaders.base` | 1053 | 10,282 |
| `loaders.filesystem` | **85** | 676 |
| `utils` | **46** | 164 |
| `loaders.app_directories` | **34** | 58 |
| **any of the four** | **1232** (79.6%) | — |

**The headline is the last row: interposing these four modules puts 1232 of the
1547 tests on code we own** — the §10.1 premise, measured, and the same shape as
M9's `NodeList.render` (a seam that is nearly the whole suite rather than a
feature's worth of tests).

Two things about the table are traps, and both are scope statements rather than
corrections:

1. **`loaders.base`'s 1053 counts *inherited* methods, so it is not "1053 tests
   use the base loader".** The label is the module where the function is
   *defined*, not the loader a test configured: `Loader.get_template` and
   `Loader.__init__` are called through `locmem`, `cached` and `filesystem`
   instances. Read it as "the base loader's code runs for 1053 tests", which is
   true and is not the same claim as the row's neighbour.
2. **The suite's dominant loader is `locmem`, and the file loader is nearly
   untouched — the opposite of what the module list suggests.**
   `tests/template_tests/utils.py:36-40` gives every `@setup` engine a
   `cached.Loader` whose inner loader is `locmem.Loader` carrying the test's
   inline templates; that is why `locmem` is on 974 tests and
   `filesystem` on 85 while `app_directories` — the loader a real project leans
   on — is on 34. So "M10 makes the suite exercise our file loading" is false,
   and a plan that sized `loaders` by the file loader would size the wrong
   thing.

`utils` being the *narrowest* module (46 tests, 164 calls) is worth recording
next to §6's decision to keep it in Python for framework coupling: it is the
smallest thing M10 owns and the least exercised, which is an argument for
owning it without ceremony rather than for ceremony.

What this does **not** show: it is a pre-interposition measurement, so it sizes
the path a future interposition sits on, not the effect of interposing (the
script prints that caveat on stderr). Nor does it answer the vacuity question —
`check_transcriptions.py` does that — and it cannot show that any of these tests
would *fail* if our module were wrong, only that they run through it.

## 3b. The pre-flight: the whole milestone, interposed verbatim, keeps all 1,547 green

Instrument: `.review/m10-measurements/m10_probe_verbatim_interposition.py`. It
builds a scratch universe — a copy of `_impl` plus verbatim copies of the nine
files M10 would add — repoints `_shim._IMPL_DIR` at it before `install()`, and
runs `runtests.py template_tests --parallel 1` in-process, the same labels
`scripts/run_django_suite.sh` uses, against the scratch universe's modules.
Nothing in the real tree is touched.

```
unshimmed: 23 -> 14
Ran 1547 tests in 3.755s
OK (skipped=6)
```

Three things follow, and the third is the one that shapes the plan:

1. **The meter lands exactly where §2 said.** 14 is §2's arithmetic — 23 minus
   the 9 M10 takes — computed independently of the run, so the run is a check
   on the partition rather than the source of it. The 14 left over are M11's
   three, M13's eight, and the three orphans §2 found unowned.
2. **`loaders` interposes with no import surgery**, as §3 said: `Engine` names
   its loaders by dotted string, so `_impl/loaders/*` is picked up through the
   string and not through a Python import.
3. **M10 can be *done* today by copying nine files**, which is the whole of its
   hazard and none of its value. The milestone's evidence therefore cannot be
   the suite — §3a's 1232 tests now run through our modules and would run
   identically through Django's — so the plan's real content is per file: which
   of the nine are transcriptions (§6's "behaviour *is* its structure") and
   which are written fresh (§6's "algorithms, control flow and internal
   structure are written against Django as a spec"), plus the direct-entry rows
   that make the interposition visible at all — M9's finding 3, which this
   probe reproduces at the scale of a whole milestone rather than one function.

The probe is also the plan's fallback: if a fresh file for one of these modules
turns out to be wrong in a way the suite cannot see, this is the oracle that
says what the correct behaviour was, and it is one command.

## 4. The vacuity question M9 bequeathed, applied to M10

M9's finding 3 stands as the rule for every interposition milestone after it:
**a verbatim transcription of the module keeps all 1,547 green**, so a green
suite is not evidence that anything is ours. M10 inherits the rule and must
answer it the same way — with an instrument that shows the code on the seam is
ours, not merely that the rows pass.

**An earlier draft of this section asked the wrong question, and §6 is what
corrects it.** That draft argued that because these four modules are mostly pure
Python state, M10 might be "dressing a transcription up as progress", and that
the honest question was *is anything here Rust's at all*. §6's ownership split
answers it before the plan has to:

> **Python owns:** the `Library` registry and dotted-path loading; `Engine` and
> the configured loader classes (framework-coupled: `override_settings`,
> `INSTALLED_APPS`, `origin.loader_name`); `Context` and context processors;
> `Origin`; `TemplateDoesNotExist` traceback hygiene; `logging` integration; …

**Nothing in M10 is meant to be Rust's, and that is a design decision with a
stated reason** — framework coupling — not an omission. So M10 is not a Rust
milestone; it is the one that takes the last Django code out of the loop, and
what it buys is exactly what §2's acceptance criterion asks for: `unshimmed()`
empty. The meter *is* the deliverable here, which is why §3's row names four
test modules and a project-side test rather than a seam, and why §7's missing
blast-radius count matters less at M10 than it did at M9 — though it still
matters, because "which tests are real signal now" is the premise of interposing
at all (§10.1).

The vacuity rule does not go away; it moves. The instrument that answers it is
the same one M9 built: `scripts/check_transcriptions.py`, where each transcribed
file declares its differing hunks and the guard asserts the diff equals the
declaration. But §6's vendoring policy adds a duty the plan must not skip —
Django's Python source is **not vendored**, the shell is written fresh with
Django's implementation as the specification — so the plan has to say, per file,
whether it is a marked transcription (`# TRANSCRIBED:`) or fresh work, because
those are different obligations and the policy forbids treating the second as
the first.

## 5. The §2 case-sensitivity hole — the guard is in the *test*, not the loader

§2 says `FileSystemLoader`'s case-sensitivity is unexercised by the suite on any
platform we run, and schedules a project-side test at M10. The recon checked
where the behaviour actually lives, because "unexercised loader behaviour" and
"unexercised test" are different repairs:

- **There is no `os.path.normcase` in `django/template/loaders/filesystem.py`.**
  *Verified*: `grep -n normcase` over `django/template/` returns nothing; the
  loader is `safe_join(template_dir, template_name)` inside
  `get_template_sources`, catching `SuspiciousFileOperation`.
- The `normcase` comparison is a **`skipUnless` guard in the test**:
  `tests/template_tests/test_loaders.py:203-206` —
  `os.path.normcase("/TEST") == os.path.normpath("/test")`. On POSIX `normcase`
  is the identity, so the guard is `False` and `test_case_sensitivity` never
  runs; on Windows it lowercases and the guard is `True`.

So the project-side test must **reproduce the guard's own reasoning**, not port
a loader behaviour that does not exist. §2 already states this correctly
("Structural, not environmental"); this note is here so M10's task does not go
looking for a case-folding branch in the loader to test.

## 6. The `_impl` naming rule is over-broad, and M10 quadruples its cost

`_shim.py`'s docstring, under *"A naming constraint on `_impl`"*, ends:

> **no `_impl` file may take the name of a real `django.template` module.**

**The tree already violates that three times**, and did so before the rule was
written:

| `_impl/` file | real module exists? | when the shadow appeared |
| --- | --- | --- |
| `base.py` | yes (`django/template/base.py`) | M9 |
| `smartif.py` | yes | M9 |
| `exceptions.py` | yes | **M0 bootstrap (`39b9e48`)** |

The rule was added by a review follow-up (`9efa0d0`, "…and the `_impl` naming
rule") on 2026-10-08 — the same day `exceptions.py` was created — so it was
already contradicted by the tree when it was written. Its *intent* is right and
its *example* is right: putting the shell's `render.py` at `_impl/engine.py`
does break `_impl/__init__.py`'s `from .engine import Engine`. What is wrong is
the quantifier: **the prohibition is on a file that is not that module's
implementation taking its name**, and the interposition itself is exactly the
case the rule must permit.

M10 would make this load-bearing rather than academic — it adds `context.py`,
`engine.py`, `utils.py` and the `loaders/` package, all under real module names,
and `_impl/__init__.py` imports `Engine` from `.engine` and `EngineHandler` from
`.utils` — so the plan must state which of those are *interpositions* (permitted)
and which would be shell code under a real name (forbidden).

**Done ahead of M10 rather than inside it** (the reviewer's call, and the right
one — the correction is not M10's business and M10 should inherit an artifact
rather than build one). `_shim.py` now states the rule as *a file may take a
real module's name only when it implements that module*, and declares the set
in `SHADOWING` — `transcribed` or `fresh` per file, checked against
`check_transcriptions.DECLARED` by `tests/test_interposition.py`, which also
asserts the set of colliding files on disk equals the table's keys. So M10's
duty is four rows in an existing table, and an accidental collision is now a
test failure in the commit that causes it instead of an `ImportError` naming
Django three files from the cause.

One number moved and must not be silently inherited: the unit suite is now
**317**, not the 315 §10.3's M9 row reports, because the guard is two rows. The
M9 row keeps 315 — it is that milestone's close-out against `6ff127d`, and
rewriting it would falsify what M9 measured — so **M10's row has to state its
own unit count** rather than repeating the one above it.

## 7. What this recon has not done

Named so the plan does not read silence as a clean bill:

- **~~No blast-radius count yet.~~ Measured in §3a** (1232 of 1547, 79.6%), and
  the pre-flight in §3b is the related but different number: not which tests
  *reach* these modules but what happens to the suite when all of them are ours.
  §4 says why neither number is M10's *evidence* — the meter is, for a
  Python-owned module — but §10.1's premise ("its Django tests become real
  signal") is a claim about the first of them, and it is now a measurement.
- **~~No check of what `Engine.from_string`/`get_template` do once interposed.~~**
  §3b answers the load path: with all nine files ours, the suite is green, so
  the load path moves without a break. What it still does *not* say is which
  tests would notice a wrong loader, which is the direct-entry gap §3b point 3
  names and the plan has to close.
- **The survey's line ranges and importer lists are unverified** (§3).
