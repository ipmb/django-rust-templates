# M12 close-out

What the milestone owes that no test carries: the sentences its instruments
exist to support, the instruments' own open defects, and every number re-measured
at the close rather than carried from a plan or a brief. The scratch it replaces
(`/tmp/m12-8-closeout/notes.md`) is absorbed rather than copied — its tree fixes
and its gate document all landed, and are listed at the end so a reader can tell
what is still owed from what was discharged.

**Everything here is dated.** `.django/` is pinned at 6.0.9 and our side moves
one commit at a time, so the numbers are for the tree that names them and the
instruments are re-run, not re-quoted.

---

## The sentences

### 1. For a ported library tag the transcription contributes its node class and its filters, and its compile function is carried, guarded, never run

Stated **per name**, never per file — which is the whole reason the sentence
needs writing down. The instrument is
`.review/m12-measurements/m12_measure_library_tag_seam.py`: two mutually
exclusive signals, a recording `NODE_CLASSES` (Rust's descriptor path) and a
recorder on the module global the transcription's last line reads. For every
name Rust owns, `NODE_CLASSES` is consulted, the transcribed body reports
`(not called)`, and the positive control fires.

Per *file* the sentence is false for exactly one task, which is what makes it the
one that gets quoted wrongly. Between Task 6a (`ec47f4f`) and Task 6b
(`cc89297`), `{% blocktranslate %}` still fell to the host and reached the
transcription's `do_block_translate`, because at `ec47f4f` `BlockTranslateNode`
was deliberately absent from `NODE_CLASSES` (`_impl/parser.py:167` there, the
comment naming 6b). In that one file the partition is: seven tag bodies dead
throughout, `do_block_translate` live or dead depending on the task, and the four
filters live. At the close all eighteen names across the five files report the
same verdict, and the `{% blocktranslate %}` entry is carried at
`_impl/parser.py:181`.

The positive control is not decoration: "(not called)" is otherwise as
consistent with a recorder that never fires as with Rust having compiled the tag.

### 2. The routing instrument's verdict follows the module, not the Rust body

`m12_measure_tag_routing.py` decides `owner()` from
`"_impl" in sys.modules[obj.__module__].__file__` — the *defining module's* file.
So from Task 6a's file move onward it reads `18 ours/ours` while two of the
eighteen names are not yet Rust's, and at the close it reads the same `18
ours/ours` for a different reason. Both are true:

- the instrument's tally: **18 ours/ours** at the close, against 10
  theirs/theirs + 8 ours/ours over the same eighteen at `ba64a52`, and 11/7
  before M12's library tasks;
- the plan's `2/16 after 6a`, which is a count of Rust **bodies** — right, and
  about something this instrument does not report.

`m12_measure_instrument_blindspot.py` is why the seam probe had to exist, and it
shows the dependency is one attribute wide: repointing `__module__.__file__` at
an `_impl` path makes all ten of i18n's names read ours, and repointing it away
makes all ten read theirs — nothing imported, interposed or written.

### 3. One message text straddles the scope correction

*(owed by the reviewer's `e731196` review; recorded here, no tree change)*

`django/template/engine.py:224` and `django/template/loader.py:49` raise the
**identical** `TemplateDoesNotExist("No template names provided")` — one on each
side of the boundary the scope correction drew, since `engine` is ours (M10) and
`loader` is declared not the engine. It does not weaken the call: the line is not
between distinct behaviours, and at least one behaviour exists on both sides. It
qualifies the argument for it, and it is a hazard for the next row of that shape:
an assertion on that message says nothing about which module produced it unless
the route is fixed.

The one row that reaches it, `test_include.py:302`
(`test_include_template_none`, the message at `:306`), does name its route —
`Engine.get_default()` and `from_string`, so it is `engine.py:224`'s copy, pinned
by the path it takes rather than by the text.

### 4. Strict mode's claim is weaker than it was, and its message now says so

*(owed by the reviewer's `e731196` review; landed in the milestone commit)*

Before the scope correction, `install(strict=True)` meant "no module in
`django.template` fell back to Django". Since the correction it means "no
**engine** module fell back" — the meter subtracts `_shim.NOT_THE_ENGINE`, so
eleven names a reader might expect are outside the count. The subtraction is
right and the claim is weaker; a user reading `strict=True` would not infer the
second from the first.

The tree side is one word. `_shim.py`'s message said "…these modules are still
served by Django's own code", which reads as *every* module Django still serves,
where one is listed; it now says "these **engine** modules", the same scope word
the preflight line in `run_django_suite.sh:60` already carries. The row that
asserts the raise (`tests/test_shim.py`'s `test_strict_mode_raises_while_falling_back`)
now also asserts the scope word *and* the named module, so the message and the
meter cannot drift apart. Nothing asserted the text before, which is why the
statement of what it covered could move without anything going red.

---

## The boundary, in its strongest form

*(the reviewer's `e731196` review supplied this argument; recorded because a
boundary's justification is the part that gets attacked, and "every body
delegates" is the weaker claim that invites the objection)*

`loader` is the closest of the ten modules declared not the engine, and it is not
pure delegation — it owns four raises:

```
:19  raise TemplateDoesNotExist(template_name, chain=chain)
:31  raise TypeError(…)                      argument validation
:47  raise TemplateDoesNotExist(", ".join(template_name_list), chain=chain)
:49  raise TemplateDoesNotExist("No template names provided")
```

That is the falsifier shape — behaviour of its own, therefore engine behaviour —
and it is the opposite. **Every one of the four is multi-engine by construction.**
`chain` accumulates `TemplateDoesNotExist`s across the engines `_engine_list(using)`
selected; the comma-joined name formats a list only an adapter holds; `using=` is
engine *selection*. An engine cannot produce any of them, because an engine does
not know other engines exist. So loader's own behaviour is exactly the behaviour
that makes it not the engine — a claim nothing can object to the way "every body
delegates" can be objected to by `:19`.

The subtraction's control is drawn from the population, and was measured by
mutation rather than read off:

| emptied | result |
|---|---|
| the shipped tree | failed = 0 |
| `_shim.NOT_THE_ENGINE` | failed = 1, naming `backends.base` |
| both it and `tests/milestone_ownership.py`'s table | failed = 1, naming `backends.base` |

It cannot be gamed because `pending()` is `set(OWNERS) - interposed()` and the ten
adapters are not in `OWNERS` at all: dropping the exclusion grows `unshimmed()` to
eleven while `pending()` stays `{"library"}`, and the first assertion fails before
the literal is reached. The literal `{"library"}` is a second, independent
backstop rather than the only one.

---

## The instruments' own defects, still open

### The `--requote` repair was never landed, and its numbers are not reproducible

A prototype that would re-quote an unquoted manifest row was drafted in scratch
and did not make it into the tree, so `check_python_anchors.py` today has
`--check`, `--triage`, `--update`, `--seed`, `--prune` and no re-quote pass. Its
**defect is recorded even though the code is not**: it compared against
`lines[line - 1]` while `_check_entry` compares within `_span_of(anchor, lines)`
— different spans — so its reported split was never a measured answer, and a
repair that fixes `lines[line-1]` but leaves `_span_of` disagreeing is not a
repair (it must count both endpoints).

Its scope claim does not reproduce either, which is the sharper finding. The
scratch said "the gate's `TREE_ROOTS` sees 38 candidates; adding `.review` and
`docs/superpowers/plans` makes 59". Measured against the script's own constants
(`TREE_ROOTS = ["crates", "python", "tests", "docs/superpowers/specs"]`,
`:279`):

| scope | files scanned | files carrying a citation |
|---|---|---|
| `TREE_ROOTS` | 198 | 140 |
| `+ .review` and `docs/superpowers/plans` | 283 | 198 |

Neither 38 nor 59 is either of those, and no third reading of "candidate" is
recoverable from the script. So the number is dropped rather than quoted: it was
a measurement of a prototype, by a metric the prototype did not record, in a file
that does not survive. Whoever lands the pass derives the scope fresh.

### The unquoted series

Both counts are printed by `check_python_anchors.py --check` and **neither fails
the gate** — they are the backlog of manifest rows that name a line without
quoting one, and the count of those whose "quote" is a bare identifier:

| measurement | unquoted | name-only |
|---|---|---|
| M6 | 104 | — |
| M10 | 122 | — |
| `ec47f4f` | 182 | 37 |
| `cc89297` / `da2ee67` | 191 | 38 |
| **close** | **205** | **43** |

What the count does and does not mean: it measures **manifest rows**, not
citations and not occurrences (the same check reports 1,422 occurrences over 838
of 838 rows cited, 0 failures). The series rises because every milestone adds
rows faster than it quotes them, and a rise is not a regression — but it is the
reason the naming pass exists, and the reason the number is reported rather than
asserted.

### `--seed`'s two warts

Both hit during Task 6a, both worth one line so the next user does not have to
re-derive them:

- **`--seed` can draft a row whose claim is wrong**, because it takes the nearest
  line matching its pattern rather than the line the sentence argues from. Three
  drafts had to be discarded: `i18n.py:213` quoted `var.translate`,
  `:414-417` quoted `while remaining:`, `:400-450` quoted a prose `, so ` — none
  of them the code being argued about.
- **`--seed` records `_shim.py` as a bare path**, which `resolve` rejects:
  `OUR_OWN` puts that file at the repo root, where it does not live.

Neither is a reason not to use it; both are reasons to read what it wrote.

---

## Numbers at the close

Measured on the closing tree, `bash scripts/check.sh` green at exit 0 /
`all checks passed`:

| instrument | value |
|---|---|
| cargo (`cargo test`) | 775 passed |
| unit (`run_unit_tests.sh`) | 442 OK |
| anchors | 1,422 occurrences, 838 of 838 cited, 0 failures |
| differential | 48 cases, identical (stock vs shim-installed) |
| Django `template_tests` | **1,547 OK (skipped=6)** |
| `unshimmed()` | `['django.template.library']` — 1 |
| `unshimmed(scope="django.templatetags")` | `[]` — 0 |
| `NOT_THE_ENGINE` / `SHADOWING` | 10 / 21 |
| routing instrument | 18 ours/ours over 18 names; 9 of 9 filters routed |
| seam probe | every ported name: `NODE_CLASSES` consulted, body `(not called)`, control fires |
| census | 1,155 lines, 16 classes, 15 `Node` subclasses, 26 module-level defs, 18 tags, 9 filters |

The second meter scope is `5 → 0` and the first does not move for this
milestone's work (11 before, 11 after, 1 after the scope correction): M12 is the
first milestone no existing number counted, which is why its value had to be
measured by the routing instrument and the seam probe rather than read off the
meter.

The universe the second zero is taken over, written out rather than counted —
`django_modules("django.templatetags")` is `["cache", "i18n", "l10n", "static",
"tz"]`, and the pin for it is at `tests/test_shim.py:646-649`, inside
`test_the_meter_keeps_its_meaning_and_gains_a_second_population` (`:589`): an
empty universe satisfies a zero trivially, so the denominator is the row, and
forcing the lookup empty was measured to leave both assertions green without it.

**Acceptance is one label.** `LABELS=(template_tests)`
(`scripts/run_django_suite.sh:25`) — a test in `tests/timezones/` or
`tests/i18n/` asserts Django's behaviour and covers this milestone not at all.
That is why two of the five libraries (`l10n`, `tz`) and five of the nine filters
have no gate in the 1,547 and their evidence is the parity corpus.

---

## Not owed — landed earlier

Kept as the record of what was discharged, so nothing here reads as open:

- the four tree fixes (F1–F4: the interposition docstring's "four", the seam
  README's per-name sentence, the second scope's denominator pin, and
  `check_transcriptions.py`'s four under-counted spans) — **all landed in
  `da2ee67`**, with a fifth the same commit (`registry.rs`'s `library_tag_names()`
  doc claimed the same set as `django_own_tags()`, which holds 45 against the
  table's 18);
- the M11 agreement gate document, `.review/m12-compared-path-agreement-gate.md`
  — `0a6727b`, with the reviewer's four justification corrections appended;
- the gate's code, the observer beside `instrumented()` and its per-module rows —
  `2761a88`;
- the Task 7 brief — `98a3df0`.
