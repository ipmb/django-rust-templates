# M15 recon — "§2 met with margin"

**Milestone row:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md:834`
**State on entry (M14's closing tree, `8b14f70`):** `bash scripts/check.sh` exit 0,
`all checks passed`; `Ran 1547 tests … OK (skipped=6)`; unit 464; cargo 787;
anchors 1472 occurrences / 867 of 867 / 0 failures; differential 48 identical;
`unshimmed()` `[]` in both scopes.

---

## 1. "Margin" is undefined in the spec, so defining it is this milestone's first act

Measured, not remembered: `grep -ni margin` over the spec returns **one** line —
834, the M15 row itself. The word appears nowhere in §2, §10.3's prose, or the
risks section. So M15 has a name and no criterion, and this section is the
criterion, recorded here so the reviewer can overrule it before a plan is written.

**The reading I am adopting:**

> **Margin is how much could be false while the gate still passes**, and it is a
> *set*, not a predicate. M15's work is to shrink that set and enumerate what
> remains. A clause leaves the set when the gate **refuses** on it *and* the
> refusing instrument has been **shown to fail** when the clause is false.

The set form is the review's correction to a first draft that read as a
predicate ("§2 is met with margin when *each* of its clauses…"), and the
correction is right for a reason worth keeping: this milestone carries a ranked
residue it will not close, and a binary reading can only file that residue as
failure — which would make §5, the milestone's most useful artefact, read as an
admission instead of as the deliverable. The predicate survives inside the set as
its membership test, and the second half of that test ("shown to fail") is the
half this project has spent thirteen milestones on.

The word is doing real work in that sentence: margin is a **distance**, and the
milestone is hard iff it can say how wide the distance is and narrow it. A green
run with a wide margin is green for a reason nobody has checked; a green run with
a narrow one is green because the thing was done. M14 is the worked example — its
criterion was already met, and what that milestone added was the *refusal*, which
is margin being removed from one clause.

The measurement that follows from the reading is not "does it pass" but: **for
each clause of §2, if the clause were false, which row goes red?** A clause with
no such row has margin equal to its whole scope.

### 1.1 Three sources of margin in this tree

Every clause of §2 is carried by one of three things, and each fails differently:

| carrier | fails when | margin |
|---|---|---|
| an assertion with a demonstrated failure arm | the claim is false | **zero** |
| a number printed beside a green exit | a human reads it | the whole clause |
| a clause stated in prose only | — | the whole clause |

The third row is not a defect of any one artifact; it is what happens when a
criterion is written down before the instrument that enforces it exists. §2 was
written at M0, when there was nothing to enforce it with. M15 is where the
instrument catches up.

### 1.2 Done-when

M15 is done when: **(a)** every §2 clause that *can* be enforced is a refusal;
**(b)** each new enforcement has a failure arm demonstrated on a fabricated
input, not merely a healthy run; **(c)** the residue — the clauses and rows that
are not closable on this tree — is written down with its rank and the reason.
(a) without (b) is the vacuity M4, M10, M12 and M14 each had to re-find; (c)
without (a) is a wish list.

---

## 2. §2 clause by clause, against what enforces it

§2 is lines 14–66. Its clause list is 31–66. Read against the tree:

| §2 clause | line | what enforces it today | margin |
|---|---|---|---|
| 1547 run; 1541 pass; exactly 6 skip | 31 | **nothing** (§3.1) | whole clause |
| zero failures, zero errors | 33 | the suite's exit status (`check.sh:139`, `set -e`) | zero |
| zero `xfail`ed | 33 | **nothing, and the exit status is actively misleading** (§3.3) | whole clause |
| zero edited tests; the checkout is never modified | 33–34 | **nothing** (§3.2) | whole clause |
| the 6 skips are the enumerated structural ones | 35–45 | **nothing** (§3.1) | whole clause |
| any *new* skip is a regression and a failure | 66 | **nothing** (§3.1) | whole clause |

Everything else §2 says (the M0 baseline is not validation; the mechanism is
evidenced by `tests/test_shim.py` and the pre-flight) is already an instrument
with a control — M0 and M14 built those, and §2 says so.

Three of those rows are not three holes but **one**, and the review is what named
it: §2:31's two facts, §2:33's two, and §2:33's "zero `xfail`ed" are each a
property of the same string — the run's summary line — and no instrument reads
that line. An exact comparison of that line therefore settles **five facts**
(1547 run, zero failures, zero errors, zero expected failures, `skipped=6`)
while being one assertion, which is the argument for §7's deliverable 1 pinning
the line *whole* rather than its count. Two of §2's rows stay outside it: the six
*identities* (§2:35-45) are not in the line at all, and §2:66's "any *new* skip"
is only mostly in it — a swap that keeps the total at 6 leaves the line
untouched, so the identity check is what closes that one.

---

## 3. The three clauses with no enforcement at all

### 3.1 The acceptance run does not pin its own outcome

`check.sh:138-139` runs the suite and reads its exit status. The transcript
(`Ran 1547 tests in 3.53s` / `OK (skipped=6)`) is printed and discarded.

Measured from both ends:

* `grep -rn -E "skipped=6|Ran 1547"` over the tree excluding `.django/` returns
  **115 hits, and not one of them is an assertion.** 112 are `.review/` or
  `docs/` prose; the three in code files are three docstring lines in two files
  (`scripts/check_suite_counts.py:7` and two in
  `.review/m13-measurements/m13_measure_stub_expiry.py`). Nothing under
  `scripts/` or `tests/` reads the number.
* `scripts/check_suite_counts.py` states the hole in its own words: *"It does not
  check that the number is *right* — that is the suite's job"* (`:24-26`), and its
  scope is `.md` under `docs/` and `.review/` (`:114-118`) by construction.

So §2's central number, and the sentence "any *new* skip appearing later is a
regression, and is treated as a failure" (`:66`), are carried by prose. The
failure mode is exactly the one M9 measured and this gate exists to name: a run
that reports `OK` while being **not ours** — and its sibling, which nobody has
measured: a run that is ours and has quietly grown a skip.

**What the run's own report contains, measured** (`scripts/run_django_suite.sh
-v 2 template_tests`, 2026-10-09, exit 0):

```
Ran 1547 tests in 3.611s
OK (skipped=6)
Destroying test database for alias 'default' ('default_1.sqlite3')...
Destroying test database for alias 'default' ('default_2.sqlite3')...
... 9 more
```

Two measurements in that block, both of which the gate's shape depends on. The
elapsed time varies (`3.603s` and `3.611s` across two runs) while the count does
not, so the `Ran` line is pinned as a **prefix**. And the summary is **not the
transcript's last line**: `runtests.py` tears down its parallel workers'
databases after printing it, so eleven `Destroying test database for alias …`
lines follow. A gate that reads `lines[-1]` fails on a healthy run; the verdict
is the last line *that matches the verdict's shape*.

and, at verbosity 2, the six by name:

| # | skip | reason printed |
|---|---|---|
| 1 | `template_tests.test_autoreloader.Jinja2TemplateReloadTests.test_get_template_directories` | `this test requires jinja2` |
| 2 | `…Jinja2TemplateReloadTests.test_reset_all_loaders` | `this test requires jinja2` |
| 3 | `…Jinja2TemplateReloadTests.test_watch_for_template_changes` | `this test requires jinja2` |
| 4 | `template_tests.test_loaders.FileSystemLoaderTests.test_case_sensitivity` | `This test only runs on case-sensitive file systems.` |
| 5 | `template_tests.test_response.SimpleTemplateResponseTest.test_using` | `this test requires jinja2` |
| 6 | `template_tests.test_response.TemplateResponseTest.test_using` | `this test requires jinja2` |

The identities match §2's enumeration exactly — which is the point: the
enumeration is *right*, and being right is not the same as being enforced.

#### The count alone is not enough, and this is not a hypothetical

`Ran 1547` and `skipped=6` are independent. Demonstrated, not argued.
`.django/tests/template_tests/syntax_tests/test_numpy.py:13` guards two real
tests with `@skipIf(numpy is False, "Numpy must be installed to run these
tests.")`, and §2:64-65 makes those two in scope by installing Numpy. A skipped
test is still *collected*, so losing Numpy does not move `Ran` — it moves only
the skip count. Probed by shadowing the import (`numpy.py` raising `ImportError`
on `PYTHONPATH`, ahead of the venv's site-packages):

```
$ PYTHONPATH=<blocker> bash scripts/run_django_suite.sh -v 2 template_tests.syntax_tests.test_numpy
Ran 2 tests in 0.000s
OK (skipped=2)          # 'Numpy must be installed to run these tests.'

$ bash scripts/run_django_suite.sh -v 2 template_tests.syntax_tests.test_numpy
Ran 2 tests in 0.000s
OK                      # the control: both run
```

Scaled to the acceptance run that is `Ran 1547 tests … OK (skipped=8)`: the exact
count §2 pins, with two tests §2 requires to run silently not running. An
unpinned `Ran 1547` is *satisfied by* the regression it looks like it guards —
which is why §2:31 says "each of the 6 skips is one of the structural cases
enumerated below" rather than "6 skip". The count is the weaker half of its own
clause.

**One thing already exists and nothing knows it.** `scripts/anchors.txt:856-857`
records snippet pins on two of the six:

```
test_response.py:141   tests/template_tests/test_response.py  141   -   def test_using(self):   -
test_response.py:296   tests/template_tests/test_response.py  296   -   def test_using(self):   -
```

`check_python_anchors.py --check --frozen` fails if that *line* stops reading
`def test_using(self):`. It does **not** fail if the `@unittest.skipUnless`
decorator above it is deleted — the pinned line is unchanged, the test starts
running, and (if it passes) nothing anywhere notices that the skip count moved.
The other four are not anchor-cited at all. So the tree holds a partial,
incidental check on one clause of §2 and no statement of what it covers.

### 3.2 The checkout is never modified — and nothing checks

`§2:33-34`: *"zero edited tests. Django's checkout is never modified; the suite
is run unmodified."*

Measured today: `.django/` is a live git clone at
`ea10f2ee50995ae9202dd18aa1a9206a8dab7b1a`, `git status --porcelain` is **empty**
(0 lines), and that SHA equals `django.lock`'s `sha=`. Both facts are true, and
neither is asserted.

* `scripts/fetch_django.sh:21-24` **writes** `django.lock`. Measured by
  `grep -rn django.lock` over the tree excluding `.django/`: the only other hits
  are the spec's own table (`:96`, `:730`) and the script's comment. **Nothing
  reads it.** A checkout fetched at a different tag with the lock hand-edited
  would be accepted.
* `git -C .django status --porcelain` appears nowhere in `scripts/` or `tests/`.

The one partial protection is again the anchor gate, and again incidental: a
citation into `.django` whose line changes fails. An edit to an *uncited* line,
or to a test file at an uncited line — which is precisely how one "fixes" a
failing Django test — is invisible to every instrument in the tree.

This is the highest-value clause to close, because it is the one where a
deliberate, well-intentioned edit produces a fully green, fully meaningless gate.
It is also the cheapest: two assertions, both already measured above.

### 3.3 Zero `xfail`ed — the clause the exit status is actively wrong about

§2:33's "zero `xfail`ed" is the third clause carried only by prose, and the worst
of the three: the mechanism that carries the other two (the exit status) does not
merely say nothing here, it **reports the wrong answer**. Measured, independently
of the review that raised it:

```
$ .venv/bin/python -I -m unittest discover -s "$D/probe" -t "$D/probe" -p 'xfail_probe.py' -v
test_that_fails (xfail_probe.T.test_that_fails) ... expected failure
test_that_passes (xfail_probe.T.test_that_passes) ... ok

----------------------------------------------------------------------
Ran 2 tests in 0.000s

OK (expected failures=1)
exit=0
```

(`$D` is a scratch directory outside the repo; the probe is two rows — one
`@unittest.expectedFailure` wrapping `assertEqual(1, 2)`, one passing. `-I`
because it is a probe, and `discover` because `-I` closes the route to a
path-argument import; `-m unittest` is also what `check_suite_counts.py` reads as
this transcript's provenance.)

`unittest` prints `expected failures=N` and exits 0, so one decorator turns a
**failing** Django test green. The other two clauses mean a test vanished; this
one means a test is failing while the gate says `OK`.

The clause is true today, and that is measured too:
`grep -rn expectedFailure .django/tests/template_tests/` is **0**. It is not
exotic in the tree at large — the same grep over `.django/tests/` hits
`sessions_tests`, `queries`, `postgres_tests`, `test_runner_apps` and
`model_inheritance_regress`, suites we do not run — so the regression is one
decorator away.

**Why this decides deliverable 1's shape.** §3.2's checkout check catches an
`xfail` introduced by editing `.django/` — the realistic path — and nothing else:
not a `conftest`, not a runner change, not a Django that ships one. Reading the
summary line catches all of them. So the assertion is the line, not the count —
the two strings `scripts/run_django_suite.sh` ends on, as the acceptance run
prints them:

```
Ran 1547 tests in 3.5s      <- the prefix is pinned; the elapsed time varies
OK (skipped=6)              <- pinned exactly
```

Two comparisons, and five of §2's clauses leave the set at once. The six
*identities* remain their own mechanism, because the line says `skipped=6` and
not which six.

---

## 4. Rows that assert an absence with no failure arm

A falsifiability audit of `tests/`, `scripts/` and `.review/` was run over the
tree (read-only; ~90 files) and produced the inventory below. I verified each
load-bearing row by reading it; the line numbers are mine.

These are **not** defects — each row's claim is true today. They are margin: the
row would stay green if its population emptied, which is the class
`[[control-over-the-same-population]]` names.

Ordered by blast radius, which is the review's ranking and is right: the two
`test_anchor_manifest.py` rows are the guard-of-guards — a hole in the thing that
checks the checkers propagates to everything it checks — so they come first, and
`test_filter_parity.py`'s documented vacuity comes last.

| row | claim | the missing control |
|---|---|---|
| `tests/test_anchor_manifest.py:465` | `set(MANIFEST) - cited_in_tree == []` | `scan_tree()` yields > 0 |
| `tests/test_anchor_manifest.py:416` | `shadows == {}` (`OUR_OWN` basenames under `.django/`) | a positive `rglob` hit, proving the denominator is non-empty |
| `tests/test_materialise.py:1223` | `dead == set()` over `NODE_CLASSES` | a fabricated class name in the subtraction, shown to land in `dead` |
| `tests/test_materialise.py:1219` | `missing == set()` over `tag_emitted_class_names()` | same shape; the stripper row at `:1207` guards only `TEST_ONLY_CLASSES` |
| `tests/test_filter_inventory.py:1238` | neither flag on any of the 66 filters | a positive `hasattr` on a tag-side object, proving the loop can see one |
| `tests/test_pyunicode_case_parity.py:91` | `not hasattr(_native, "python_lower")` | `hasattr(_native, "python_upper")` — one line, same instrument |
| `tests/test_filter_parity.py:430` | `stubs == set()` | **already documented as vacuous by construction** (`.review/m4-summary.md:386-389`); do not "fix" — it is the deliberate-edit signal |

The audit also flagged `tests/test_interposition.py:1808` and `:2237` (registry
disjointness over a real registry) as weaker-than-claimed. I verified the two
spans name the rows the audit says they name
(`test_the_six_filter_registries_are_disjoint_and_the_inversion_injective`,
`test_the_five_libraries_share_no_name_with_the_two_builtin_modules`) but not the
claim that their failure arms are missing; they are listed here as **claims
verified by span only**, and should be measured before being acted on — the same
rule that made me read the rest.

**Instrument-level, and deliberately not closed by this milestone:** the
`.review/m*-measurements/*.py` scripts are print-only. `.review/m11-measurements/README.md:3-5`
says so in its own words — *"none of them asserts, so the record is the output
rather than a pass."* Converting them into gates would freeze measurements that
exist to be re-taken. Their margin is a *reader*, and the fix is the record, not
a row.

---

## 5. What this milestone does not close, ranked

Written down because (c) of §1.2 is a deliverable, and because a residue with no
rank is indistinguishable from an oversight.

1. **The 1547 is `template_tests` only.** Measured (`acceptance-suite-is-template-tests-only`):
   `run_django_suite.sh:25` and `check.sh:139` name one label. Django's
   `timezones/` and `i18n/` suites assert Django behaviour and cover us not at
   all. *Partly mitigated, and now measured:* the nine library filters that those
   suites would reach — `tz`'s `localtime`/`utc`/`timezone`, `i18n`'s four
   `language_*`, `l10n`'s `localize`/`unlocalize` — all carry corpus cases in
   `tests/filter_cases.py` (counts 3/3/6/4/2/1/2/5/2 by name), so our own
   instruments reach them even though the acceptance suite does not. The residue
   is the *rest* of those two suites' surface, which nothing enumerates. Closing
   it is a new label in the acceptance run, not a row — out of M15's shape.
2. **Reachability of the 23 `unreachable!()`s.** Measured by
   `git grep -ohE "panic!|unreachable!" -- crates | sort | uniq -c`: **100
   `panic!`, 23 `unreachable!`**, the latter over 11 files. (A bare `\|`
   alternation returns a false zero here — see §6.2.) `refused_panic`
   (`crates/template-core/src/filters/host.rs:937`) is by design. The probe that
   measures reachability (replace the body with `unreachable!()`, rebuild the
   `.so`, run the whole gate) is real work and belongs in a milestone that owns
   the Rust tree, not in the one that owns §2's enforcement.
3. **`test_filter_parity.py:430`** — kept exactly as it is, for the reason in §4.

---

## 6. Two measurement traps met while writing this, recorded because they cost time

### 6.1 `git grep` does not search a gitignored tree, and `.django/` is one

My first search for the tz/i18n filters was

```
git grep -nE "timezone|localtime|utc|i18n|language" -- .django/tests/template_tests
```

which returned **nothing**, and I nearly wrote down that `template_tests` never
touches i18n. `.django/.gitignore` is `.gitignore:14` → `.django/`, and
`git ls-files .django | wc -l` is **0**, so `git grep` had no files to search.
The true count, from `grep -rniE` (with a positive control over all of
`.django/tests/`, which returns 684 for the narrower `localtime|timezone` alone),
is **342** hits inside `template_tests`, including the whole
`template_tests/syntax_tests/i18n/` package.

This is the family `[[review-verify-against-pinned-tree]]` warns about — an
unstated tool making a zero a lie — but a mechanism the memory did not carry:
`git grep`'s scope is *tracked files*, so it silently answers "not in this tree"
for every fetched dependency, vendored archive or build output. A zero from
`git grep` over `.django/` is not a measurement.

### 6.2 The `\|` false zero reproduces, and the fix is `-E`

`git grep -n "todo!\|unimplemented!\|unreachable!\|panic!(" -- crates` returns
nothing (POSIX BRE has no `\|` alternation under this `git`), while
`git grep -nE "…"` returns 123. Same class, second instance, and worth recording
because the two instances have *different* causes and the same symptom.

---

## 7. Proposed shape, and the one question for the reviewer

Three deliverables, in this order — the first two are §2 clauses becoming
refusals, the third is the record:

1. **The acceptance run pins its own outcome.** The transcript
   `scripts/run_django_suite.sh` already produces is now parsed, and the run is
   refused unless the summary line is exactly
   `OK (skipped=6)`, the `Ran` line starts `Ran 1547 tests in `, and the set of
   skipped tests equals the six named in §2 — *by identity*, not by count,
   because §2:31 says "each of the 6 skips is one of the structural cases
   enumerated below", and a swap that keeps the count at 6 satisfies a count.
   The summary is pinned as a whole string rather than dissected: §2's five
   summary-line facts (§2 above) come free with that one comparison.
2. **The checkout is pinned and unmodified.** `git -C .django status --porcelain`
   empty ∧ `HEAD` equals `django.lock`'s `sha`. Both true today (§3.2), neither
   asserted.
3. **The six unfalsified rows in §4 get controls**, and `.review/m15-record.md`
   carries the definition, the controls' demonstrations, and the ranked residue.

**Question for the reviewer — judgement, not measurement (I have measured the
alternatives):** deliverable 1's six identities can be held as a literal in the
gate, or parsed out of §2:35-45. §3's ten names were parseable because §3 writes
them as a backticked list; §2 writes its six as *composed* prose — a class name
(`Jinja2TemplateReloadTests`) plus three method names in parentheses, and a bare
`test_using` that belongs to two different classes named later in the sentence.
A mechanical reader would have to reassemble fully-qualified names from a
sentence that never spells one, and getting it subtly wrong yields a *wrong set*
rather than an error — the failure `[[a-subtraction-has-two-routes-to-zero]]`
names. My read is that the literal belongs on the **instrument** (the gate's
declared set, with a control row driving its refusal on a fabricated seventh
skip) and the *tie* to §2 belongs on a row that asserts each component name
appears in the §2 frame — the split M14's review settled. Confirm or overrule.

**The review's answer: confirmed, and generalised into the rule.** It named the
split ***"parse when the document lists; pin when the document composes"*** — the
same rule that decides §3's ten names (a backticked list → parsed) against §2's
six (a class name plus parenthesised methods → a declared literal, tied back by a
frame reader). The fabricated seventh skip carries the `unregistered_` prefix,
per `[[negative-fixtures-expire]]`, so a later milestone that ports that name
cannot flip the row's meaning by accident. The review then turned the rule on
this milestone's own literals, which is the sharper half: the summary line is an
**instrument** fact — Django's output format, which §2 describes in prose and
never spells — so it is pinned whole, while §2's six identities are the
**subject** and are tied to the document.
