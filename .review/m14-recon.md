# M14 recon — the empty meter, both scopes, and what "no fallback" is asserted by

Read-only recon for M14, taken on the closing tree of M13 (`08f97fe`). The
milestone's row (§10.3) reads:

> `unshimmed()` empty over the engine, in both of the meter's scopes —
> `django.template` and `django.templatetags` — with every name it still reports
> one §3 declares not the engine.
>
> **Acceptance:** full suite green with no fallback.

Pinned tree and interpreter, named by version: `.django/` @ 6.0.9 on
`.venv`/CPython 3.14.4.

---

## 1. The criterion is met, and it already has a row under it

Measured, not quoted:

```
$ PYTHONPATH=scripts/shimpath:.django DJANGO_RUST_TEMPLATES=1 .venv/bin/python -c "…"
installed True
django.template     -> []
django.templatetags -> []
```

So M14 is not a milestone that *does* something to the tree to become true. It
is the milestone that **enforces and records** a state M13's last name
(`library`) already reached. That distinction is the whole of the recon: the recon
question is not "how do we empty the meter" but "what is still only *inferred*,
and what is *asserted*".

**And the acceptance run reproduces green on this tree**, measured, not quoted —
`bash scripts/run_django_suite.sh template_tests` over the same clean checkout:

```
verifying the Rust engine is installed...
engine installed; 0 engine module(s) still served by Django (10 more of
                  django.template are declared not the engine)
Ran 1547 tests in 3.532s
OK (skipped=6)
```

— so the two halves of M14's acceptance ("suite green", "empty meter") are both
already true together, on one run, before M14 does anything. What M14 adds is
that the second half becomes a condition of the run rather than a line beside it.

**The row that asserts it already exists**:
`tests/test_milestone_ownership.py:151`,
`test_the_engines_remaining_work_is_empty` — it asserts `pending()` empty and
`suffixes(unshimmed(...)) == set()` for **both** scopes, and it carries a positive
control drawn from the population rather than from the universe
(`owned_by_django == set(NOT_THE_ENGINE)` asserted *before* the claim, so a run
over an empty universe cannot pass it — the rule this project learned as
[the-control-must-be-over-the-same-population]). Its docstring already says what
this recon confirms: "M13 takes the last one, and the meter over the *first* scope
reads zero for the first time."

**Consequence for the plan.** M14 must not re-derive the empty meter as new work.
The milestone's deliverables are (a) the one place the claim is still only
*printed* and not *refused* (§3), and (b) the close-out record + the spec row. A
Task 1 that "empties the meter" would be a task with nothing to do.

## 2. "every name it still reports one §3 declares not the engine"

At an empty set this clause is `∀x∈∅` — true and uninformative. The binding
content is two rows that are *not* about the empty set:

- `test_the_table_partitions_the_universe` (`:92`) —
  `set(OWNERS) | set(NOT_THE_ENGINE) | interposed() == the_universe()`. Three
  buckets, both directions, and the third bucket (`NOT_THE_ENGINE`) is the scope
  correction's own direction: a name can now be settled by being *declined*.
- `test_the_declared_names_are_the_excluded_ones` (`:180`) —
  `set(NOT_THE_ENGINE) == set(_shim.NOT_THE_ENGINE)`, so the membership in the
  shim and the reasons in the table cannot drift.

`NOT_THE_ENGINE` is ten names (`_shim.py:123-136`): `autoreload`, `backends`,
`backends.{base,django,dummy,jinja2,utils}`, `context_processors`, `loader`,
`response`. The reasons per name live in `tests/milestone_ownership.py:117`.
So "the declaration is a set with rows under it" is already true; M14 inherits it
rather than building it. (It was built at M10 and corrected at M13's close, per
§10.3's amendment paragraph.)

## 3. "full suite green with no fallback" — the one thing still only printed

There are **three** guards against a silent fallback, and they cover three
different failures. Only the third is at the acceptance run, and it is the weak
one:

| guard | where | what it catches |
|---|---|---|
| install is fatal | `scripts/shimpath/sitecustomize.py:29-44` | the shim did not install at all (unimportable `django_rust_templates`, wrong `PYTHONPATH`, `-S`) — `os._exit(1)`, so a stock-Django run cannot report `OK` |
| one module resolves ours | `scripts/run_django_suite.sh` pre-flight | a mistyped path or an unsynced venv — asserts `_shim.installed()` and that `django.template.exceptions.__file__` contains `_impl` |
| the meter is empty | `tests/test_milestone_ownership.py:151` | a *per-module* fallback (a `django.template` name we never interposed) — but as a **unit test**, not at the suite run |

**The gap.** `run_django_suite.sh`'s pre-flight *computes and prints* the meter —
and only for **one** scope (`_shim.unshimmed()` takes the default `TARGET`,
`django.template`):

```
engine installed; N engine module(s) still served by Django (…)
```

— and never refuses on `N`. So "no fallback" at the acceptance run is currently
an **inference from a unit row plus a printed number**, and the printed number
covers half the population M14's criterion names. If a future change removed an
`_impl` file (or added a `django/template/*.py` to the pin on a version bump) the
suite would keep printing green while the meter went non-empty, and only
`cargo test`/the unit suite would notice — and the unit suite is not the
acceptance criteria. The second scope (`django.templatetags`) is not consulted at
the run at all.

**The M14 change, then, is one condition in the pre-flight**: refuse to run the
acceptance suite when **either** scope's `unshimmed()` is non-empty, with a
message naming the offending modules. That converts the acceptance claim from
"the run printed 0 for the first scope and the unit suite asserts 0 for both" into
"the run refuses unless both are 0". This is the milestone's *own* sentence —
"full suite green with no fallback" — moved from recorded to enforced, and it is
the same move M10's row made when it turned a remembered number into
`tests/milestone_ownership.py`.

**A caveat the record must state, because the phrase overstates otherwise.**
`unshimmed()` compares **source trees** (`django_modules(scope) - ours`), by
listing files, not by resolving imports — deliberately, so the meter is
side-effect free (`_shim.py:777-779`). So "empty" is a *file-level* claim: every
name Django's package defines has a same-named file in `_impl`. The *runtime*
claim — that the import system actually serves ours — is carried separately by
`_ShimFinder` and by `SHADOWING`'s assertion against the files on disk
(`test_interposition.py`). "No fallback" is therefore true only as the
conjunction, and the record should say which half each guard carries rather than
letting the four-word phrase stand for both.

## 4. Not in scope

- **`autoreload` and `context_processors`** are `NOT_THE_ENGINE` by the user's
  one-sentence answer (§3's non-goals bullet, §10.3's amendment paragraph), so
  they are not M14 work and are not a second amendment. `library` left the meter
  at M13. Nothing remains.
- **`backends.*`, `response`, `loader`** — likewise declined, per the same
  correction. M14 keeps *empty* over the engine rather than over the package.

## 5. Proposed shape (one commit, per §10.3's "each milestone ends with one commit")

1. **Task 1 — the pre-flight refuses a non-empty meter.** Add the condition to
   `run_django_suite.sh`, with a row in the project's own suite (`tests/test_shim.py`
   or a small new `tests/test_acceptance_preflight.py`) that runs the pre-flight
   script against a *fabricated* non-empty meter and asserts the non-zero exit —
   a negative control, so the new guard is exercised with something in the set
   rather than only observed green. Then run the full `template_tests` suite and
   record `Ran 1547 tests … OK (skipped=6)` with the meter empty.
2. **Task 2 — close-out.** The spec's M14 row (line 833) to ✅ with the counts
   measured on the closing tree; a `.review/m14-record.md` carrying §3's table,
   the file-level-vs-runtime caveat, and the "the criterion was already met, the
   milestone enforced it" framing. `docs/feature-checklist.html` re-stamped.

**The reviewer question this recon cannot settle alone**: whether "no fallback"
should be enforced in `run_django_suite.sh` (the acceptance path) or in
`scripts/check.sh` (the gate that calls it). The runner is the closer site — it is
where the suite actually starts — but `check.sh` already owns the gate's steps and
is where a reader looks for "what is asserted". Recommend the runner, with the
guard tested from `check.sh`'s unit suite.
