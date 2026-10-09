# M14 record

The milestone's row (§10.3):

> `unshimmed()` empty over the engine, in both of the meter's scopes —
> `django.template` and `django.templatetags` — with every name it still reports
> one §3 declares not the engine.
>
> **Acceptance:** full suite green with no fallback.

Pinned tree and interpreter, named by version: `.django/` @ 6.0.9 on
`.venv`/CPython 3.14.4. Closing tree: `f6c87a4`.

---

## The headline

**The criterion was already met when M14 opened.** Both meter scopes read `[]`
on M13's closing tree — `library` (`547bfb9`) was the engine's last name — so
M14 is not the milestone that empties the meter. It is the milestone that makes
the emptiness a **condition of the acceptance run** rather than a number printed
beside it, and the record of what that condition does and does not establish.

That distinction is the whole milestone, and `.review/m14-recon.md` §1 measured
it before the plan was written: on the clean checkout of `08f97fe` the suite and
the empty meter were both already true together, in one run. A Task 1 that
"emptied the meter" would have been a task with nothing to do.

## What the run now refuses

There are three guards against a silent fallback, and they cover three different
failures. Only the third sits at the acceptance run, and before this milestone it
was the weak one:

| guard | where | what it catches |
|---|---|---|
| install is fatal | `scripts/shimpath/sitecustomize.py:29-44` | the shim did not install at all (unimportable `django_rust_templates`, wrong `PYTHONPATH`, `-S`) — `os._exit(1)`, so a stock-Django run cannot report `OK` |
| the pre-flight | `scripts/assert_engine_installed.py`, called from `scripts/run_django_suite.sh:43-46` | a mistyped path or an unsynced venv, **and** a non-empty meter in either scope |
| the meter is empty | `tests/test_milestone_ownership.py:153` | a per-module fallback — but as a **unit test**, not at the suite run |

The gap the milestone closed: the pre-flight *computed and printed* the meter for
**one** scope (the default `TARGET`, `django.template`) and never refused on it.
So "no fallback" at the acceptance run was an inference from a unit row plus a
number covering half the population the criterion names, and the second scope was
not consulted there at all.

`main()` now runs both checks and refuses when either fails, iterating
`_shim.NAMESPACES` (`_shim.py:98`) rather than respelling the scope, so the
checked population cannot drift from the meter's. The refusal arm, driven
in-process with a fabricated meter (the negative control in
`tests/test_acceptance_preflight.py`):

```
django.template: 1 module(s) the engine owns are still served by Django:
  django.template.unregistered_m14_fabricated
refusing to run the acceptance suite: M14's criterion is unshimmed() empty in
both scopes, so a green run here would not be the acceptance criterion.
healthy -> {}
exit=1
```

and the healthy run, which is the acceptance path:

```
verifying the Rust engine is installed...
engine installed; unshimmed() is [] over django.template, django.templatetags
                  (10 more of django.template are declared not the engine)
Ran 1547 tests in 3.531s
OK (skipped=6)
```

## "No fallback" is a conjunction, and neither half is behaviour

The phrase stands for two claims, and the record states which half carries each:

* **file level, all of it** — `unshimmed()` compares **source trees**
  (`django_modules(scope) - ours`, `_shim.py:755`, `795`, `847`), by listing
  files rather than resolving imports, deliberately, so the meter is
  side-effect free. "Empty" therefore means *every name Django's package defines
  has a same-named file in `_impl`*.
* **runtime, all of it** — the pre-flight imports every engine module and asserts
  each resolves to a file under `_impl`. **This is the half M14 widened**: before
  the extraction it was a single module (`django.template.exceptions`); it is now
  **21 modules** (16 under `django.template`, 5 under `django.templatetags` —
  `django_modules(scope) - NOT_THE_ENGINE`, the same call the meter makes, so a
  module a version bump adds to Django's package is imported and checked rather
  than passing unseen).

The import is safe here and would not be in `unshimmed()`: the meter may be
called anywhere and importing can require settings, while the pre-flight runs
once, immediately before the suite, in the same interpreter and `PYTHONPATH`.
That asymmetry is the reason the two claims were not merged.

**What the pair still does not establish is behaviour.** It proves each module's
file is ours, not that the file is right — the 1,547 is what says that. A reader
who takes "no fallback" for "the engine works" has read one word for three
claims.

## The meter's second route to zero: §3 was the document nothing read

Found in Task 1's review and landed as Task 1b (`f6c87a4`). The ten names §3
declares not the engine live in three places:

```
spec:75-80                          §3's enumeration, ten names in a parenthetical
python/.../_shim.py:123-136         the frozenset            } tied by
tests/milestone_ownership.py:117    the table, with reasons  } test_the_declared_names_are_the_excluded_ones
```

Only the last two were tied to each other. **Either could drift from §3** — the
document the decision is recorded in, and the one M14's own criterion names. The
direction that matters is the meter's arithmetic: `unshimmed()` is
`django_modules - ours - NOT_THE_ENGINE`, so **widening the subtrahend empties
the meter exactly as interposing does**. A name moved out of the engine by
editing the shim and the reason beside it — which is what this project's
discipline asks for — would leave every row in the tree green while §3 still said
ten, and M14's empty meter would then be indistinguishable from the engine being
complete. This is the `utils.py` zero once more: a number reaching its target by
two routes, only one of which is the claim.

The fix is the precedent one function away in the same file. `spec_milestones()`
(`tests/milestone_ownership.py:312`) reads §10.3's labels out of the spec, with
the reason stated there as *"a second list would agree with itself and a typo in
`OWNERS` would be invisible."* The same move against §3:

* `spec_section("3")` (`:322`) — §3's body up to the next `## `. Scoped rather
  than searched in the document: §3 backticks `django.template` three times
  outside its list, and the phrase occurs in a dozen other sections.
* `between(text, start, end)` (`:336`) — the frame between the enumeration's two
  anchors, **with both anchors checked**, because `partition` cannot fail: a
  reworded anchor would silently return the tail of §3 as the frame and the
  reader would answer with a *wrong set*, a failure that reads as the spec
  drifting when the instrument moved.
* `spec_not_the_engine()` (`:353`) — the backticked spans between
  `stay Django's for good:` and `The decision is the`. Measured: exactly the ten.

Two rows (`tests/test_milestone_ownership.py:200`, `:229`): the equality against
`_shim.NOT_THE_ENGINE` with a floor guard (so a reader that stopped matching
cannot arrive at the equality as an empty set) and, as its control, the reader's
refusal arm driven on a literal — the one arm a green run never reaches.

**One deviation from the review's advice, recorded so it can be overruled.** The
review asked for the extracted set to be pinned as a literal once. The rows
instead print the extraction in the failure message, because freezing a carrier
is the thing this project's rule warns against and §3 is prose a reader is
allowed to reword: a literal pin would fail on a rewording that keeps all ten
names, and the diagnostic it buys — separating "the shim moved" from "the frame
moved" — is available from the printed set.

## The guard is dated to the engine being complete

A pre-flight refusing on a non-empty meter would have blocked **every** suite run
through M10–M13, when the meter was non-empty by design. It is correct now
because the engine is finished, and it will refuse on the first commit of any
future milestone that interposes a new engine module. That refusal is the guard
doing its job rather than a regression, and both the script's docstring
(`scripts/assert_engine_installed.py:11-17`) and its refusal message say so, so
the first person to hit it is not left to guess.

## The instruments, and their invocation traps

* **The pre-flight runs in the same interpreter and `PYTHONPATH` as the suite** —
  the runner sets `PYTHONPATH` before calling it, and the script is a subprocess
  of the same `.venv` python, so the check and the run cannot disagree about
  which tree they are looking at.
* **`DJANGO_RUST_TEMPLATES=0` still bypasses it.** The runner is used
  deliberately against stock Django to prove a failure is Django's and not ours;
  the guard sits inside `if [ "${DJANGO_RUST_TEMPLATES:-1}" = "1" ]`
  (`run_django_suite.sh:43`). A guard that fired on the comparison run would make
  the one debugging tool unusable.
* **The negative control is drawn from the meter's own population.** The
  fabricated name carries the `unregistered_` marker, and
  `test_the_fabricated_names_are_not_real_modules` asserts it is not in
  `django_modules(scope) - NOT_THE_ENGINE` in either scope — so a later milestone
  that registers the name for real cannot quietly turn the control positive.
* **A trap for a reader re-measuring this record**: `.review/m14-recon.md`'s
  three citations (`:151`, `:92`, `:180`) are correct for the tree it names
  (`08f97fe`) and are now two lines low, because Task 1b added two names to
  `tests/test_milestone_ownership.py`'s import block. The current lines are
  `:153` (the engines'-remaining-work row), `:94` and `:182`. The recon is a
  dated document and was left as written.

## Numbers at the close

Measured on the closing tree, not quoted:

| | |
|---|---|
| `bash scripts/check.sh` | exit 0, `all checks passed` |
| Django acceptance | `Ran 1547 tests … OK (skipped=6)` |
| unit suite | **464** rows |
| `cargo test` | **787** |
| `scripts/check_python_anchors.py --check --frozen` | **1472** occurrences / **867 of 867** cited / **0 failures** |
| differential harness | **48** cases identical |
| `unshimmed()` | `[]` in **both** scopes |
| runtime-checked engine modules | **21** (16 + 5) |
| `NOT_THE_ENGINE` | **10** |
| `builtin_tag_stub_names()` | `[]` |

The unit suite moves 457 → 464 across the milestone's two commits: five rows in
`tests/test_acceptance_preflight.py` (Task 1) and two in
`tests/test_milestone_ownership.py` (Task 1b).

## Not owed — carried to M15

**M15's criterion is undefined and the milestone opens on defining it.** §10.3's
row reads "Hardening | §2 met with margin", and "margin" appears nowhere else in
the spec. The reading this milestone did **not** adopt, recorded so it can be
overruled: *the acceptance set is met, and the instruments that establish it
would still fail if it were not* — margin as the gap between "green" and "green
for a reason", which is the shape every milestone since M9 has actually been
closing on (instruments 4 → 5 → the seam probes, the partition rows, the
controls). M14 is one more instance rather than the definition, and the
definition belongs in M15's own recon.
