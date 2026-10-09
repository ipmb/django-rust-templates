# M15 — "§2 met with margin"

**Milestone row:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md:834`
**Recon:** `.review/m15-recon.md` (the definition, the clause table, the two probes, the ranked residue)
**Plan:** `docs/superpowers/plans/2026-10-09-m15-margin.md`
**Commits:** recon `866a294` → `4509af3` → `9568881`; plan `f98d55f` → `f069727` → `2c4972f`;
execution `ab9a56d` (Task 1) → `6121635` → `c16c4d3` (Task 2) → `50e038c` (Task 3) → this record.

---

## 1. The headline: §2's clauses, and what carries each one now

§2 is lines 14–66 of the spec. On entry every clause in the last three rows was
carried by prose; two of them are now refusals, and the one that is not a
transcript property is checked before the suite runs.

| §2 clause | line | carried by, before | carried by, now |
|---|---|---|---|
| 1547 run; 1541 pass; exactly 6 skip | 31 | prose | `scripts/assert_suite_outcome.py`, whole-line pin + `DECLARED_RAN` |
| zero failures, zero errors | 33 | the suite's exit status | the same whole-line pin, and the status |
| zero `xfail`ed | 33 | **prose, and the status is wrong about it** | the same whole-line pin — and it is the *only* mechanism (§2 below) |
| zero edited tests; the checkout is never modified | 33–34 | prose | `scripts/assert_checkout_pinned.py`, before the suite |
| the 6 skips are the enumerated structural ones | 35–45 | prose | `DECLARED_SKIPS`, by identity, plus the tie to §2's prose |
| any *new* skip is a regression and a failure | 66 | prose | the same identity check |

**One comparison settles five of those facts.** §2:31's two, §2:33's two and
"zero `xfail`ed" are all properties of one string — the run's summary line — so
`DECLARED_SUMMARY = "OK (skipped=6)"` compared exactly answers all five, and
`Ran 1547 tests in ` answers the sixth. Two of §2's rows stay outside it: the six
*identities* are not in the line, and §2:66's "any *new* skip" is only mostly in
it, since a swap that keeps the total at six leaves the line untouched. Both are
the identity check's job.

**The line is pinned whole because no exit status could answer it.** Measured
three ways (`@unittest.expectedFailure`, one row each, `python -I -m unittest`):

```
unexpected success alone    FAILED (unexpected successes=1)                   exit 1
expected failure alone      OK     (expected failures=1)                      exit 0
both in one run             FAILED (expected failures=1, unexpected successes=1)  exit 1
```

`expected failures=N` never sets the verdict, in any combination — and the pinned
interpreter's own source says why: `TestResult.wasSuccessful` is
`failures == errors == 0 and not unexpectedSuccesses`, with no `expectedFailures`
term at all (`inspect.getsource`, CPython 3.14.4). So no guard reading an exit
status could ever catch this clause. String comparison is the only mechanism
available, not the cheapest one.

## 2. What "margin" meant here

The spec defines it nowhere: `grep -ni margin` over the spec returns **one** line
— the M15 row itself. The reading this milestone adopted and recorded in the
recon §1:

> **Margin is how much could be false while the gate still passes**, and it is a
> *set*, not a predicate. A clause leaves the set when the gate **refuses** on it
> *and* the refusing instrument has been **shown to fail** when the clause is
> false.

The set form is what makes the residue a deliverable rather than an admission: a
predicate can only file the unclosed clauses as failure.

The set form also survives the count/identity split, and that split is worth its
own section because §2's own sentence is what makes it. §2:31 does not say "6
skip"; it says "exactly 6 skip, **and each of the 6 skips is one of the
structural cases enumerated below**". A number and an identity are two claims,
and only the second is what the sentence argues for.

## 3. The count is the weaker half of its own clause

Demonstrated, not argued. `.django/tests/template_tests/syntax_tests/test_numpy.py:13`
guards two real tests with `@skipIf(numpy is False, …)`, and §2:64–65 puts those
two in scope by installing Numpy. A skipped test is still *collected*, so losing
Numpy does not move `Ran` — it moves only the skip count. Probed by shadowing the
import:

```
$ PYTHONPATH=<blocker> bash scripts/run_django_suite.sh -v 2 template_tests.syntax_tests.test_numpy
Ran 2 tests in 0.000s
OK (skipped=2)          # 'Numpy must be installed to run these tests.'

$ bash scripts/run_django_suite.sh -v 2 template_tests.syntax_tests.test_numpy
Ran 2 tests in 0.000s
OK                      # the control: both run
```

Scaled to the acceptance run that is `Ran 1547 tests … OK (skipped=8)`: §2's
exact count, with two tests §2 requires to run silently not running. An unpinned
`Ran 1547` is *satisfied by* the regression it looks like it guards.

## 4. The checkout clause, and why it is the one that matters most

It is the clause where a deliberate, well-intentioned edit — the usual way one
"fixes" a failing Django test — produces a fully green, fully meaningless gate.
Every other instrument in this tree reads the *output*; this one reads the input.

On entry: `.django/` was a live clone at `django.lock`'s sha with an empty
`git status --porcelain`, and nothing asserted either. `scripts/fetch_django.sh`
wrote the lock and nothing read it. The one partial protection was incidental —
`scripts/anchors.txt:856-857` snippet-pins two of the six skipped methods' lines,
which fails if the method *moves* and not if its `@unittest.skipUnless` decorator
is deleted.

Both halves are checked because either alone is satisfiable while the other is
false: a clean tree at the wrong tag is a different Django, and the right tag
with a patched file is not stock Django. `git status --porcelain` is read
*without* `-uno`, since a planted untracked file changes what the suite does as
surely as an edited line. A checkout with no `.git` at all — a tarball, a
hand-copied clone — is named rather than surfacing as a traceback.

## 5. The six controls

Each row asserts a set is empty, and each would stay green if its population
emptied. The rule the edits follow is `[[control-over-the-same-population]]`:
a control through the same function over different inputs is not a control. Three
of the six were wrong as first written, and two of those defects are invisible by
reading.

| row | the population the control draws from | the mutation that makes it fire |
|---|---|---|
| `tests/test_anchor_manifest.py:465` | the walk itself, materialised (`scan_tree()` is a **generator** — `assertTrue(scan_tree())` is true of an empty tree) | make `scan_tree` yield nothing |
| `tests/test_anchor_manifest.py:416` | the same `rglob` with the same filter, over `base.py` | break the `rglob` filter |
| `tests/test_materialise.py:1223` | the row's **own** subtraction, a fabricated name in the minuend, parenthesised | widen the subtrahend to include the fabricated name |
| `tests/test_materialise.py:1219` | the minuend, `tag_emitted_class_names()`, asserted non-empty | break the Rust class-name reader |
| `tests/test_pyunicode_case_parity.py:91` | `hasattr(_native, "python_upper")` — the sibling accessor | remove every accessor from `_native` |
| `tests/test_filter_inventory.py:1238` | **none exists** — see below | **none can be run** |

`|` binds *looser* than `-` in Python, so `A | {f} - B - C` parses as
`A | ({f} - B - C)`. Measured, what that breaks is not `f` — `f` is dropped by
both forms once `f ∈ B ∪ C` — but `A`: the union's left operand escapes both
subtractions, so the row silently stops asserting its own `A - B - C`.

**The sixth control is instrument-level and the row does not leave the set.**
`do_not_call_in_templates` and `alters_data` are on **0 of the 57** builtin
filters at 6.0.9; every carrier in the checkout is model-side. So the same
`hasattr` on the same object for `is_safe` (on 55 of the 57) shows the loop is
handed functions and that the instrument can see a flag — and neither half can
make the row *fire*, because no mutation in this tree can make the clause false.
Under §1's membership test the second half is unavailable, so the row moves from
*uncontrolled* to *instrument-controlled, subject-discrimination unproven*, and
it is carried in the residue (§8) rather than counted here. The tempting repair —
a fabricated object carrying the flag — would measure `hasattr`'s truth table
rather than this tree.

**A seventh row was caught by review rather than by execution.** The plan's first
version pinned `test_anchor_manifest.py`'s control with
`assertTrue(anchor_manifest.scan_tree())`, which is true of an empty generator;
and its filter-row control named `defaulttags.register.tags["if"]`, which carries
none of the five flags measured. Both were written from the rows' *descriptions*
rather than their *code*; measuring each carrier cost one `python -c` each.

## 6. The two identity mechanisms considered

The question was whether §2's six skip identities should be a literal in the gate
or parsed out of §2:35–45. §3's ten names were parseable because §3 writes them
as a backticked list; §2 writes its six as *composed* prose — a class name plus
three method names in parentheses, and a bare `test_using` belonging to two
classes named later in the same sentence. A mechanical reader would have to
reassemble fully-qualified names from a sentence that never spells one, and
getting it subtly wrong yields a **wrong set** rather than an error.

The review confirmed the split and generalised it into a rule: ***parse when the
document lists; pin when the document composes***. So `DECLARED_SKIPS` is a
literal on the instrument, and the tie to §2 is a row that asserts every
component name appears in the §2 frame. The fabricated seventh skip carries the
`unregistered_m15_` prefix, per `[[negative-fixtures-expire]]`, so a later
Django that ships that name cannot flip the row's meaning by accident.

The same rule then turned on this milestone's own literals: the summary line is
an **instrument** fact — Django's output format, which §2 describes in prose and
never spells — so it is pinned whole, while §2's six identities are the
**subject** and are tied to the document.

## 7. Numbers at the close

Measured on this tree, `bash scripts/check.sh`:

| what | number |
|---|---|
| gate | exit 0, `all checks passed` |
| acceptance | `OK (skipped=6) after 1547 tests, and the six declared skips by identity (scripts/run_django_suite.sh)` |
| unit (`tests/`, discovered) | **487** (was 464) |
| cargo | **787** (unchanged — M15 touched no Rust) |
| anchors | 1472 occurrences / 867 of 867 manifest entries / 0 failures |
| differential | 48 cases, identical |
| meter, both scopes | `unshimmed()` is `[]` over `django.template` and `django.templatetags` |

Two new gates, 23 new rows, one weaker pre-flight collapsed into the extracted
script (the inline block in `scripts/run_unit_tests.sh`, which named the meter and
did not refuse on it), and no production code changed: this milestone touched only
the instruments that establish §2.

## 8. Not owed — carried to M16

Ranked, with the reason each is not closable here.

1. **The 1547 is `template_tests` only.** `scripts/run_django_suite.sh:25` and
   `scripts/check.sh:139` name one label. Django's `timezones/` and `i18n/`
   suites assert Django behaviour and cover us not at all. The nine library
   filters those suites would reach all carry corpus cases in
   `tests/filter_cases.py`, so our own instruments reach them; the residue is the
   *rest* of those two suites' surface, which nothing enumerates. Closing it is a
   new label in the acceptance run, not a row.
2. **Reachability of the 23 `unreachable!()`s.** 100 `panic!` and 23
   `unreachable!` over 11 files; the probe that measures reachability (replace a
   body with `unreachable!()`, rebuild the `.so`, run the whole gate) belongs in
   a milestone that owns the Rust tree.
3. **`tests/test_filter_parity.py:430`** — documented vacuous by construction
   (`.review/m4-summary.md:386-389`); kept exactly as it is, because it is the
   deliberate-edit signal and "fixing" it removes the signal.
4. **`tests/test_filter_inventory.py:1238` is instrument-controlled, and its
   subject discrimination is unproven** (§5 above). Ranked last because the claim
   is true today and only a Django that sets one of those two flags on a builtin
   filter would expose it.
