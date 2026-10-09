# M10 recon — interposing `context`, `engine`, `utils`, `loaders`

Written 2026-10-09, before M10's plan. Pinned tree: `.django/` at **6.0.9**
(`ea10f2e`, "Bumped version for 6.0.9 release"). Closed predecessor: M9 at
`97c366e`, `unshimmed()` **23**.

This recon's first job was to size the milestone, and sizing it turned up a
scope error in §10.3 before any code was written. §1 is that error. §2 is the
meter arithmetic it changes. §3–§6 are what the milestone has to decide, with
the measured ones marked as measured and the surveyed ones marked as surveyed.

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

## 2. The meter arithmetic: nine entries, not five

`unshimmed()` counts names, and a package contributes one name per module under
it. M10's real target set, read off the closed tree's list:

| module | meter names retired |
| --- | --- |
| `context` | `django.template.context` (1) |
| `engine` | `django.template.engine` (1) |
| `utils` | `django.template.utils` (1) |
| `loaders` | `django.template.loaders`, `.loaders.base`, `.loaders.filesystem`, `.loaders.app_directories`, `.loaders.cached`, `.loaders.locmem` (6) |
| ~~`origin`~~ | **0 — not a name in the tree** |

So M10 takes the meter **23 → 14**, not 23 → 18, and the row's five names are
nine meter entries. This is the meter-scope lesson from
`define-the-metric-before-comparing-counts` applied before the fact: *module* and
*meter entry* are two different counting units, and the table uses the first
while the progress number uses the second.

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

M10 makes this load-bearing rather than academic: it adds `context.py`,
`engine.py`, `utils.py` and the `loaders/` package, all under real module names,
and `_impl/__init__.py` imports `Engine` from `.engine` and `EngineHandler` from
`.utils` — so the first thing M10's plan must state is which of those are
*interpositions* (permitted) and which would be shell code under a real name
(forbidden). The docstring should be corrected to say the second thing, and the
correction is M10's, because M10 is where the rule stops being a curiosity.

## 7. What this recon has not done

Named so the plan does not read silence as a clean bill:

- **No blast-radius count yet.** M9 sized its seams by running the suite with
  instruments (`m9_measure_*.py`); M10 has not. Whether `test_context` and
  friends are *gated* by our modules in the M9 sense — or are, like M9's
  `test_engine` module name suggests, merely *named* for them — is unmeasured.
  §4 now says why this number is not M10's *evidence* — the meter is, for a
  Python-owned module — but unmeasured is still unmeasured, and §10.1's premise
  ("its Django tests become real signal") is a claim about this number.
- **No check of what `Engine.from_string`/`get_template` do once interposed.**
  M9 left the Python node tree as the render path; how much of the *load* path
  M10 actually moves is unmeasured.
- **The survey's line ranges and importer lists are unverified** (§3).
