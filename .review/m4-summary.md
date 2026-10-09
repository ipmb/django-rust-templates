# M4 — the 57 built-in filters

**Review target: `.django/` at Django 6.0.9.** An unpinned Django also exists on
this machine, so an unqualified "Django" is ambiguous — every number below is
against the pinned checkout. The *interpreter* is pinned too: `28` against `95`
case-method divergences was the same measurement under two CPythons, and it is
the reason every count in this milestone names its interpreter.

This is the milestone round (§11). The unit of review is the milestone, not the
commit, which is why all 60 commits below — including the 20 whose subject is a
plan correction — are in scope. The plan corrections are themselves a finding;
see "The plan-vs-tree defect rate".

## What M4 claims

1. **All 57 built-in filters have Rust bodies.** Not "57 names parse" —
   [`REGISTRY`][reg] has one row per name and `every_registry_entry_is_a_stub_at_m4`
   shrank to nothing as the waves landed. `STILL_STUBBED` is the empty array, and
   `test_every_stub_is_named` asserts `len(stubs) == 0` as a **deliberate-edit
   signal** rather than a count: the zero is the milestone, so re-adding a stub
   fails the test by name.
2. **Each body owns the filter's control flow and delegates the algorithm**
   where §D2's three metrics say the answer lives outside the template — 21
   filters, derived from the source and asserted, not counted by hand.
3. **`Value`, `ValueId`, `FilterFlags`, `FilterHost` and the registry are the
   design gate's settled interface**, written once and never edited by a fan-out
   agent. An agent that needs a new host operation stops and reports (§D7).
4. **The FFI surface is one call**, `run_filter`, with `@stringfilter` applied
   once in the loop (§D3), and the *length* of the argument tuple carrying
   "no argument" versus "the argument `None`" — a distinction an `Option` cannot
   hold.
5. **The corpus is the verification.** 539 `CASES` + 40 `ERROR_CASES` in
   `tests/filter_cases.py`, driven by `tests/test_filter_parity.py` against
   `_native.filter` and against real Django. An `expected`-bearing row is
   checked *in addition to* the two-engine comparison, so it is self-verifying.
6. **`FilterFlags` round-trips** against the pinned registry: 36 `is_safe=True`,
   19 `is_safe=False`, and `cut`/`escapejs` carrying no attribute at all —
   asserted as an absence rather than folded into `false`.

[reg]: ../crates/template-core/src/filters/mod.rs

## Commits

60 commits, `4652830`..`7df817f`. Grouped by task; the plan corrections are
listed with the task they correct.

| Commits | Task |
|---|---|
| `4652830`, `81127f7`, `b0fb754`, `e5c9ae7`, `8192192`, `aaab0e1`, `918a5cd` | M4 plan, its two §6/§10.3 corrections, and the design-gate revisions |
| `854a513`, `ad61ad6` | Task 1 — `pyunicode`'s case mapping, and the generators' `--check` in the gate |
| `75950aa` | Task 2 — `Value`, `FilterFlags`, `FilterHost` (40 methods), the registry in full |
| `522ed56`, `cdd2ecf`, `8b73608` | Task 3 — the FFI surface, the corpus harness, the inventory test, and the corpus's seed (Step 4b replaces `upper` and `lower`'s stubs with bodies) |
| `84f12b2`, `1252277`, `ebb5f97` | Task 4 — case and simple text, 6 filters |
| `eaceab2`, `7d744e7`, `31ae3ad`, `c11a581` | Task 5 — padding and truncation, 8 filters |
| `3ab831e`, `c0f431b`, `e541da1` | Task 6 — sequences, 9 filters |
| `36b9e85`, `cc8ca88`, `060d7ca`, `24f91b6`, `b5fb7bd`, `df0d49a` | Task 7 — numbers and logic, 10 filters, in three waves |
| `47edea3`, `11e5650`, `515e5cb`, `9f79ed9`, `70ca183`, `ec0ccb5`, `a5d3233`, `ca634c2`, `550f597`, `b334396`, `3ae06db` | Task 7's corpus, docs and plan corrections |
| `b9ecdaa`, `e7ee889`, `222ac6d`, `7efc59c`, `6cc67dc`, `246ba73`, `fe1e749` | Task 8 — escaping and safety, 12 filters |
| `055c6fc`, `1a99278`, `6f10fa0`, `d74192e`, `ece8a3b`, `09a7774`, `713fc19` | Task 9 — URLs and markup, 6 filters; the `urlize` limit seam; the `json_dumps` defect |
| `82c334e`, `cf8d69a`, `c4027d0` | Task 10 — dates and elapsed time, 4 filters; the `timesince` `Result` and the two raw formatter methods |
| `76a9ec7` | the nine uncalled seam methods re-derived, and the 40-method sweep |
| `7df817f`, `c64990c` | the citation pass, and the harness-is-a-shared-input doc |

## Evidence

| Check | Result |
|---|---|
| `cargo test --workspace` | 560 passed, 0 failed |
| `cargo clippy --workspace --all-targets -- -D warnings` | exit 0 |
| `cargo fmt --all -- --check` | exit 0 |
| Python unit tests (`tests/`) | 173 tests, OK |
| `tests.test_filter_parity` | 539 `CASES` + 40 `ERROR_CASES`, every `expected` checked against Django and the engine |
| `tests.test_filter_inventory` | 57 registry names, 21 delegating, the three metrics, the flag round-trip |
| `scripts/differential.py` | 48 cases, identical |
| `scripts/check.sh` (full) | exit 0, `Ran 1547 tests … OK (skipped=6)` |
| Progress meter | **25** modules still Django's — unchanged from M3 |

The suite's 1547/6 is the acceptance criterion and it is *expected* to be
unchanged — and at M4 that is not a mild statement. See the next section.

## The acceptance blind spot — the gate cannot see a filter body

Established from the code path, not by inference, in
`.review/m4-acceptance-blind-spot.md`. **The only route from the 1547-test suite
into the Rust filter layer is `_native.parse`'s `check_filter`**
(`_impl/parser.py:246-264`), which runs Django's two *name and arity* checks and
nothing else. `_materialise_filter_expression` then rebuilds each
`FilterExpression` with `parser.find_filter(name)` — **Django's own registry
function, unconditionally** — so `{{ x|upper }}` and `{{ x|pprint }}` take the
same path and an implemented body and a stub are indistinguishable at render
time.

Spec §5.5 says why this is intended: for all 1547 tests the render entry point
is the Python nodelist. Filters resolve inside Python's own
`FilterExpression.resolve`.

The consequence is the milestone's central fact: **at M4 the Rust filter bodies
are a parallel implementation whose only verification is the corpus.** The suite
validates the parse and the host's callbacks; it cannot redden for a wrong body,
a missing body, or a body whose rows are too thin to reach the branch a defect
is in.

Measured, while every one of them was still a stub: `template_tests` names
`urlize` 88×, `date` 34×, `time` 32×, `timesince` 20×, `timeuntil` 16×,
`urlizetrunc` 11×, `slugify` 8×, `phone2numeric` 3×, `json_script` 2×,
`pprint` 0× — and under the shim `{{ x|pprint }}`, `{{ x|urlize }}` and
`{{ x|slugify }}` all render correctly with every one of them stubbed.

So **"landed" and "verified" are different states at M4, and only the corpus
separates them.** That is why rows land in the same commit as the body
(`713fc19`), and why `scripts/filter_probe.py` (§D6) is not a convenience but
the only source of truth for a body's expected answers. The blind spot is
transitional — it closes when `_native.render` stops delegating to Python's node
rendering — and it is not closed today.

## The design gate: the trait, the seam rule, and the six defects

§10.4 settles the trait before fan-out, and the gate did its job: **five filters
had no expressible host method** when `FilterHost` was first written from a
reading of the 57 bodies rather than derived from them (`81127f7`). Deriving it
turned up more:

- The four case operations were sized for a *table*. The exhaustive diff the
  reviewer asked for showed **two are context-sensitive** — `lower` and `title`,
  via `Final_Sigma` — so they left the pure side (`b0fb754`).
- **`sort_by_property`'s `prop` needs `&Value`, not `&str`** — the wrapped
  function takes an object.
- **`phone2numeric` is not pure** and §D2 could not see it (`713fc19`): it scores
  clean on both existing axes — it references nothing stateful and calls no
  divergent string method — and is still not Rust's, because `@keep_lazy_text`
  sits between the registry and the `def` and changes the *result*. §D2 gained a
  third clause, and the general rule: **the wrapped function is the *decorated*
  one, not the `def` body.** The set moved 18 → 20 → **21**.
- `%`-formatting had no expressible method: `"%" + spec` cannot carry
  `gettext("%s KB") % x` (`36b9e85`).

The milestone's headline finding came out of a defect. **`json_dumps` was
transcribed wrong** (`ece8a3b`): `PyFilterHost` passed `separators=(",", ":")`
and omitted `cls`. `django.utils.html.json_script` is
`json.dumps(value, cls=encoder or DjangoJSONEncoder)` — default separators, and
the encoder is what makes a `date`, `Decimal` or `UUID` serialisable at all.
Measured: `json_script({"a": 1})` is `'{"a": 1}'` (with the space), and
`json_script({"d": date(2026, 10, 8)})` is `'{"d": "2026-10-08"}'`; tight
separators give `'{"a":1}'` and a plain encoder raises `TypeError`.

**Nothing could have caught it.** `json_script` was a stub and this method's only
caller, so the whole of `json_dumps` was dead code behind a live doc comment. No
unit test reached it, `cargo test` never ran it, the corpus could not name it.
What found it was writing the Task 9 brief and re-deriving the seam method
**against the source it claims to wrap instead of against its own claim.**

That generalises to the rule now in §10.4: **an uncalled seam method is
unverified code behind a verified doc comment, and the doc is the artifact a
brief is written from** — so the derivation must be against the pinned source.
The reviewer enumerated the eight other uncalled methods and asked for one pass
rather than eight more chances to carry a wrong claim into a brief.

**The nine, re-derived against `.django/` @ 6.0.9 (`76a9ec7`): three defects and
one wrong doc claim.**

| seam | verdict |
|---|---|
| `json_dumps` | **defect, fixed** `ece8a3b` |
| `date_format`, `time_format` | **defect**: `&str` cannot express Django's `None`; the substitute diverges in any locale with a format module |
| `now` | **defect**: the doc names a caller, and following it reddens |
| `localtime` | call clean, **doc wrong** — it passes `context.use_tz`, not `settings.USE_TZ` |
| `slugify`, `urlize`, `pformat`, `timesince` | clean |

`date_format`/`time_format`'s defect is the sharpest: `formats.date_format(v,
None)` and `date_format(v, "DATE_FORMAT")` are the *same call*, and
`settings.DATE_FORMAT` is *neither*. With `LANGUAGE_CODE="de"`,
`{{ d|date }}` is `'8. Oktober 2026'` and `date_format(d, settings.DATE_FORMAT)`
is `'Okt. 8, 2026'` — so a body that resolves the default itself is right in
`en-us` and wrong in `de`. The trait doc said only "the active language's
formats", which is the half a reader checks and is true: the `json_dumps` shape
exactly.

`now`'s defect is the inverse — a doc that names a caller *that must not use
it*. `timesince` fills the clock **internally** (`timesince.py:66-68`), and a
caller supplying `host.now()` takes the aware/naive branch: `timesince(naive,
None, reversed=True)` is `'2 hours, 59 minutes'` while `timesince(naive,
timezone.now(), reversed=True)` **raises** `TypeError`, which `timeuntil_filter`
catches and answers `''`. So the doc's own instruction turns a real answer into
an empty string. **`FilterHost::now()` has no caller in the filter layer at
all.**

The rule the sweep predicted, and then confirmed mechanically: **a seam's
signature should be the wrapped function's signature, defaults and sentinels
included.** Wherever the Rust side computes what Python would have computed, the
divergence lives. Swept over all 40 trait methods: **exactly two are narrow and
wrong** — `date_format` and `time_format`, both now fixed — plus `now`, a third
defect of a different shape (its signature is not narrow; it has no parameters).
The five that *look* narrow and are not are each written down with the
measurement that clears them; the most valuable is `to_float(&Value) -> f64`,
which is safe only because of a wave-2 fix — calling the `float` **type** rather
than extracting a pyo3 `f64` — so the wave-2 fix is what protects it. Two
findings closing on each other across waves is the evidence that the rule is
structural rather than a list of incidents.

**And a rule that the reviewer's own message then strengthened.** The
`json_dumps` rule was justified by a host method possibly being wrong. It now has
a second justification, and it is the stronger one: **a wrong trait doc does not
stay in the trait.** It is the highest-leverage text in the crate — more than the
plan, which an agent reads once, because these get *copied*. The citation pass
(`7df817f`) is the proof; see the next section.

## The citation pass, and where the drift actually came from

A sweep of every qualified `file:line` citation across the filter modules
found a set that did not land on the thing they named. **The origin was
`host.rs`, not the wave-B agents.**

Wave B's module docs had copied two wrong citations verbatim from the trait docs
— `html.py:412-415` (actually `smart_urlquote`; `Urlizer.trim_url` is `:407-410`)
and `defaultfilters.py:395` (actually an argument-name line; `int(limit)` is
`:399`). Wave A sampled clean **not because its agent was more careful but
because its filters do not cite those host methods at all.**

**The reviewer had diagnosed from the symptom's distribution rather than its
propagation path** — which files are wrong told them "wave B"; where the text
came from would have told them "host.rs" — and the remedy they proposed (scope
the pass to three files) would have left the source in place to re-propagate
into Task 10's modules. It did not, because I swept rather than took the
scoping. The reviewer's own reading of this is in their Task 10 message and is
better than mine: the implication is not about citations, it is that **the trait
docs are a propagation source, so every module that calls a method inherits
whatever its doc asserts.**

Fixed in `7df817f` across three files (`host.rs`, `urlize.rs`, `urlizetrunc.rs`
— 29 lines changed between them), including one range that *excluded the
behaviour the sentence named*: `timesince.py:60-70` described the reversed swap
at `:72-73` and stopped before it.

## The `safe_input` collapse — a decision, recorded here on purpose

`Urlizer.__call__` computes `safe_input = isinstance(text, SafeData)`
(`html.py:319`), and `handle_word` (`:337-405`) uses it in exactly four places:
two `if autoescape and not safe_input:` guards and two `if safe_input: return
mark_safe(word)` branches.

The seam takes `&str`, so the marker cannot cross it. **Both `urlize.rs` and
`urlizetrunc.rs` pass `call.autoescape && !call.host.is_safe(&value)`, collapsing
the pair in the body.** That is text-equivalent for every input: the first pair
is the substitution verbatim, and the second pair differ only in *safety* at the
word level, which `"".join(urlized_words)` discards for either — and the filter's
final safety is the `def` body's own `mark_safe` regardless. Measured over 32
cases (`{plain, mark_safe} × {autoescape on, off} × 8` inputs, one with all four
of `<`, `&`, `'`, `"`), the collapse changes no answer.

**Decision: keep the collapse in the body; do not widen the seam.** The
alternative — a `safe_input` flag on the trait — would put a Django-internal
predicate on the interface for a body that already has the value it needs. It is
the same collapse `django.utils.html.linebreaks` writes in the source itself
(`defaultfilters.py:493`), which is why `linebreaks.rs` carries one too. Recorded
here rather than only in the module doc because it is the one place where "Rust
owns the filter" is achieved by *folding two Python predicates into one Rust
expression*, and a reviewer should be able to reject it.

## The review rounds and dispositions

The M4 review ran as task-level rounds plus this milestone round. Dispositions,
with the commit that carries each.

**MAJOR — the `json_dumps` transcription. Found by me while writing Task 9's
brief; accepted and fixed (`ece8a3b`).** Recorded above.

**MAJOR — the five filters with no expressible `FilterHost` method. Accepted;
the trait was rewritten (`81127f7`).** A trait written from a reading rather
than a derivation. The reviewer's question was the right one; the fix was to
derive it from the bodies and then diff.

**MAJOR — `lower` and `title` are context-sensitive, not table-sized.
Accepted; both left the pure side (`b0fb754`), and §D2's metric gained its
second axis.** The four-method exhaustive diff the reviewer asked for is what
found it; the plan had sized the case operations for a table.

**MAJOR — `phone2numeric` is not Rust's. Accepted as a ruling, and it corrected
the reviewer's own sweep, not just the plan (`09a7774`, `713fc19`).** The
reviewer's sweep bounded the class at one by a mechanism that was wrong in both
count and reason: fourteen helpers is fifteen, and "every other such filter has
`str(first)` run first" is not the cut, because **five** filters reach a
lazy-decorated helper with no `@stringfilter`. Four are safe for a *different*
reason — `mark_safe` is a host method calling Django's own decorated function,
so `keep_lazy` rides along. The bound survives, the reason is better, and the
five are now derived and asserted so a Django bump moves a test rather than a
paragraph.

**MAJOR — `date_format`/`time_format` and `now` (`c4027d0`, `cf8d69a`).** Both
accepted; the signatures changed rather than the docs, because the divergence is
reachable.

**MINOR — `localtime`'s doc. Accepted; doc corrected.**

**MINOR — `index_of`'s synthesised `IndexError` message. The reviewer ruled the
fix was to the *claim*, not the string** (`template-py/src/filters.rs`). The
comment said the message was CPython's; it is not, and both callers catch
`IndexError` and answer `""`, so nothing observes it. **My first instinct was to
strip the index from the message** — which would have fixed the symptom and left
the claim standing. The reviewer's ruling is better and is the record now.

**MINOR — the `Markup` row in `.review/m4-seams.md` was not measured on this
machine. Accepted; row deleted.** `markupsafe` arrives only with Jinja2, which §2
excludes, so the input is unreachable, and the mechanism it claimed was wrong
too. It had been load-bearing in the argument for the `phone2numeric` fix. **The
second instance of "a claim that read as true and had never been run", and the
first written by the coordinator rather than by a brief.**

**MINOR — the `as_nested_sequence`/`GeneratorType` suspicion. Checked and
refuted.** `filters.rs:792-794` tests all three types and the trait doc names the
triple against the pinned source. No defect.

**No change — the `safe_input` collapse; the `timesince` "far future is an exact
equality" reading; the three declared `TestFilterHost` coverage gaps.**

**Accepted with push-back — the reviewer's wave-B scoping conclusion**, corrected
by the `host.rs` origin finding above.

**Carried, not fixed — the five `needs_autoescape` collisions.** `base.py:813`
passes `autoescape` as a keyword *before* `*arg_vals`, so a filter whose own
second parameter is named `autoescape` cannot be given an argument at all:
`linebreaks`, `linebreaksbr`, `linenumbers`, `unordered_list`, `urlize`. Our
engine takes `autoescape` as a *value*, so there is no keyword to collide with
and the argument would otherwise be silently ignored — answering where Django
raises, which this project treats as a defect. **Decided to document rather than
synthesise** — it is CPython's binding of the call, not a body's decision, and
the message needs the *callable's* Python name, which a name-keyed seam does not
have. `urlize`'s body refuses anyway, because its collision is reachable through
the same call; the other four are recorded on `filter` in `lib.rs`. M9 inherits
this exactly when the shim exposes our filters as Python functions with Django's
signatures.

## Guards made to fail, not just read

Each observed failing for its intended reason before being kept. Where the
observation is recorded only in a commit message rather than re-run here, it
says so.

- **Task 3's stub guard** (`522ed56`): `every_registry_entry_is_a_stub_at_m4`
  was observed failing as each wave landed and naming what was left. It is now
  vacuous by construction, and `test_every_stub_is_named`'s `len(stubs) == 0` is
  the zero as a deliberate-edit signal.
- **`e7ee889`'s exception identity**: `{{ x|upper }}` where `str(x)` raises a
  caller-defined `BoomError` reported `builtins.Exception: BoomError: loud` from
  our engine against Django's `__main__.BoomError`. New corpus row
  `upper/raising-str`, observed failing before the fix. The row uses a class the
  test module defines, so it is about *identity* rather than about a class
  `builtins` happens to have — and `striptags`' `SuspiciousOperation` is the
  second instance, which is why the seam keeps the `PyErr` rather than a name.
- **`7efc59c`'s argument-length seam**: measured before the fix, three of the 23
  landed argument-taking filters answered where Django raises (`floatformat`,
  `pluralize`, `unordered_list`); re-measured after, **1 of 23 diverges** and it
  is the `autoescape` collision, not a `None` case.
- **Task 5's padding**: `7d744e7` — the fallible allocation was observed
  aborting, and `try_reserve` chosen over a bound because the bound is not
  measurable.
- **Task 7's pinned literals** (`c0f431b`): two rows, not three, each pinned to
  a literal a wrong comparison cannot accidentally satisfy.
- **The precision cap** (`c11a581`, `31ae3ad`): measured at `65536`, refuting
  the plan's negative-cast hypothesis.
- **The three case-method guards** (`b0fb754`): the exhaustive diff plus 400,000
  random strings, which is what made `lower`/`title`'s context-sensitivity a
  measurement rather than a suspicion.
- **`713fc19`'s five lazy-helper pairs**: derived and asserted, so the next bump
  moves a test.

## The plan-vs-tree defect rate — fifteen, and one new class

M2 classified its seven; M3 added eight and found the broken-snippet class. M4
adds **fifteen**, and the shape has shifted again: the largest class here is
**a count produced by a structure nobody asked the structure about.**

| # | Task | What it believed | What is true |
|---|---|---|---|
| 1 | plan | the delegating set is 7 | **18**, derived; then 20, then **21** |
| 2 | plan | `FilterHost` expressible from a reading of the 57 bodies | **five filters had no method** |
| 3 | Task 1 | the case divergence is a table | **two methods are context-sensitive** |
| 4 | Task 9 | `json_dumps`' kwargs | **wrong; see above** |
| 5 | Task 5 | the width overflow is a negative cast | **cap is 65536** |
| 6 | Task 7 | `pluralize`'s coincidence rule | corrected (`9f79ed9`) |
| 7 | Task 7 | `ERROR_CASES` — neither home needs a body test | wrong; comment corrected (`11e5650`) |
| 8 | Task 7 | `join`'s `__html__` result type | divergence recorded (`ca634c2`) |
| 9 | Task 8 | §D4's escaping table is eight filters | **four** (`246ba73`) |
| 10 | Task 8 | the five collisions are excluded | **refused, not excluded** (`fe1e749`) |
| 11 | Task 7 | `join`'s argument rule | refuted by the reviewer's "a" (`ca634c2`) |
| 12 | Task 3 | "~90 `CASES` rows" | **64** — nested `context`/`settings` dicts counted as rows |
| 13 | Task 10 | a row named `date/sentinel-text` | does not exist; it is `date/branch-two-absent-argument` |
| 14 | Task 10 | `timesince`'s far-future row is a monotonicity property | an **exact equality** (`'0\xa0minutes'`) |
| 15 | plan | the group-sizing table's Task 4 row: **8** filters, `lower` delegated | Task 4 **dispatched 6** — `upper` and `lower`'s bodies landed a task earlier (Task 3 Step 4b) |

**#15 I found while self-checking this summary, which is the point.** The sizing
table (§"Tasks 4–10", the artifact a reader uses to check the fan-out) sums to 57
by counting each filter under its *theme*; the task headings sum to 55 by
counting what each task *dispatched*. Both numbers are right and neither says
which question it is answering, so the table lists `lower` under Task 4's
delegated filters when Task 3 landed it. This is the same defect as #12 one
layer up — and it is the reason I re-measured every count in this summary rather
than transcribing the plan's: **a document that reviews a count-error record has
to expect one in itself.** The plan is left as-is and the reconciliation is
written here, because a plan edited during its own milestone review makes the
review target move.

**#12 is the eleventh count dispute in this project and the shape is now
boring, which is the point**: a number derived from a structure nobody asked the
structure about — 90 against 64, because nested `context`/`settings` dicts
counted as rows. It was corrected by amending the commit (`c746589` → `82c334e`),
not by a follow-up, because a wrong number in a commit message is a wrong number
in the record.

**#13 is the same class one layer down.** The hand-back named the row correctly
(`date/branch-two-absent-argument`); the *doc prose* cited `date/sentinel-text`,
which does not exist. Found by an automated sweep of every Rust doc for row
references, not by reading — which is the only way it was going to be found.

**#6–#11 have the same shape as M3's**: a plausible claim nobody had executed,
each settled by running something. The refinement M3 drew — *a declared
environment tells you what exists; it does not tell you the snippet is
syntactically and structurally sound* — held again here, but the bigger lever at
M4 was **deriving instead of reading**: #1, #2 and #3 are all cases where a
derivation from the pinned source replaced a plausible reading, and all three
moved a number the plan had already published.

## What I did not verify

- **The 1547-test gate says nothing about a filter body.** Stated at length
  above; repeated in this list because a reader deciding whether M4 is done
  should not have to reach the earlier section.
- **The two verdict tables in `tests/test_filter_inventory.py` are
  hand-written, and nothing mechanical checks their `True`/`False` against the
  Rust bodies.** `UTILITY_VERDICTS` and `LAZY_HELPER_VERDICTS` are
  totality-checked (`test_every_utility_verdict_is_referenced_and_total`,
  `test_the_lazy_decorator_metric_cuts_its_five_to_one`): every referenced
  utility is classified and every classification is referenced. **Correctness is
  not checked.** I audited all 28 entries by hand — the 23 in `UTILITY_VERDICTS`
  (14 `True`, 9 `False`) and the 5 in `LAZY_HELPER_VERDICTS` — against the shipped
  bodies and they hold: every `True` utility's filter body calls the corresponding
  host method, and every `False` one does not. But the audit is manual and nothing
  re-runs it. **A test would have to hand-type the utility→host-method map,
  which is the same class of artifact the tables exist to avoid**, so I declined
  it and am naming the gap instead. The corpus and the reviewer's read are what
  check these.
- **`FilterFlags` round-tripping is against the pinned registry, not against
  behaviour.** `test_the_flags_round_trip` proves the Rust table equals Django's
  attributes; it does not prove the flags are *used* correctly. Their consumers
  are `base.py:816`'s marking and `args_check`, neither of which is on M4's
  render path.
- **Three `TestFilterHost` coverage gaps, each declared rather than papered
  over.** (a) `is_true` is a single `Option<bool>`, so it gives one scripted
  answer for both calls in `date`/`time`; no row separates the two. (b)
  `record()` pushes only the operation *name*, so `timesince`'s `now` and
  `reversed` are unobservable from a body test. (c) **No row separates `&Value`
  from `&str` for the raw formatter** — the reviewer tried to break this last
  round and could not, because branch 1 reaches `str(arg)` through `get_format`
  anyway, so the gap is established from two directions rather than asserted.
- **The `RemovedInDjango2028Warning` `urlize` emits for a bare `www.` host is
  delegated-only.** A Python warning is not part of any answer `_native.filter`
  returns, so no corpus row can assert it. What the rows pin is its *effect* —
  `http://` versus `https://` under the two `URLIZE_ASSUME_HTTPS` states.
- **The `safe_input` collapse is argued and measured over 32 cases, not proven.**
  It holds wherever `safe_input`'s two uses are the four sites named; a fifth
  use added by a Django bump would not be caught by the corpus unless a row
  reaches it.

## Carried findings

**The trait docs are a propagation source, and they are copied.** Not a defect —
a property that changes where review effort goes. A wrong trait doc does not stay
in the trait; the citation pass found two that had been copied verbatim into
wave B's modules. The remedy is the `json_dumps` rule (re-derive against the
pinned source) and the recognition that these docs carry more weight than the
plan.

**`phone2numeric`'s registered name and callable name differ**
(`phone2numeric` vs `phone2numeric_filter`), the second instance of that split
after `linebreaks`. Harmless here because `phone2numeric` has no
`needs_autoescape`, so no synthesised message carries a callable name.

**`Value::Opaque` cannot name its Python class**, so a `dict` or `tuple` input to
a host call gets `'object' object has no attribute 'lower'` where Django says
`'dict'`. Bounded to messages no corpus row asserts today, and it is the reason
the `phone2numeric` ruling's first leg is the *reachable* one.

## What I want attacked

1. **The acceptance blind spot is the milestone.** If the corpus is the only
   thing separating a landed body from a verified one, is 539 + 40 rows the
   right instrument, and is there a class of body defect that no row can reach —
   the way `json_dumps` was reachable by no gate at all? I believe the seam rule
   covers the seam half and only the corpus covers the body half; tell me where
   that is wrong.
2. **The `safe_input` collapse.** I am folding two Django predicates into one
   Rust expression and calling it text-equivalent. Is the equivalence argument
   complete, or is there an input where `mark_safe(word)` versus `word` at the
   word level survives `"".join`?
3. **The verdict tables.** I declined the mechanical cross-check and audited by
   hand. Is that the right call, or is there a derivation I did not see that
   would check a verdict against the body without re-introducing a hand-typed
   map?
4. **The five `needs_autoescape` collisions.** I documented rather than
   synthesised, because the message needs the callable's Python name and a
   name-keyed seam does not have it. Is documenting right, or should the seam
   carry the callable name now so M9 does not inherit a silent divergence?
5. **`FilterHost::now()` has no caller.** The seam carries a method the filter
   layer must not use, and its doc now says so. Should it be removed and re-added
   when `{% now %}` needs it, or kept with the warning?
6. **The fifteen plan defects.** #1/#2/#3 are one class — a count or an
   interface published from a reading and later moved by a derivation — and #15
   is #12 one layer up. Is that a process failure with a fix, or the irreducible
   cost of planning before measuring?

Ask for executable findings where you can — a command and its expected output
rather than prose. Where a finding is about a number, please name the metric and
the interpreter; this project's count disputes have each turned out to be two
right answers to two unstated questions, and M4 added an interpreter axis to the
list.
