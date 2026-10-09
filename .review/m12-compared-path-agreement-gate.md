# The compared-path agreement gate

Written by the reviewer session (`django-rust-templates-a8`) on 2026-10-09, from
a prototype built in their own scratch space; it lands in this tree because they
are read-only there and because a second copy in a plan file is the two-places
drift this project keeps paying for. It is the **citation** for the gate that
Task 8 builds — before this file existed the gate was a name in a notes list
with no artifact behind it, which is the defect it would have caught in our own
tree.

Home: `.review/m12-compared-path-agreement-gate.md` (named for the milestone that
lands it, per the `.review/` convention; the instrument it cross-checks is
older). Its code lands in `tests/compared_path.py` beside `instrumented()`, with
the rows in `tests/test_compared_path_instrument.py`.

---

## What it is

A second observer of the compared path that watches **execution** rather than
**reachability-by-binding**. `tests/compared_path.py`'s `instrumented()` answers
*"a binding I could find was called"*; clause (4) asks *"did this code run"*.
Those are different questions, and the gap between them has been wrong twice —
once in the descriptor table, once in the registry walk, both times
under-reporting, which is the unsafe direction because an invisible function
reads as *not on the compared path* and clause (4) then accepts a declared
region over code the harness executes.

## What it does

It wraps nothing. During one run of the same corpus through the same entry point
(`django_parse`, both corpora, `RANDOM_SEED`), it installs `sys.setprofile` and
records `frame.f_code` for every `call` event. Each frame is attributed to a
label by the spans `definitions(module)` already produces: a frame counts for
`module` when `co_filename` is that module's file, and its label is the span
containing `co_firstlineno`. The result is the same shape as `measure()`'s — a
set of labels — produced without any binding being touched.

## What it asserts

For every module in `COMPARED_PATH`, one row: the label set
`measure(module, sources, django_parse)` reports **equals** the label set the
frame observer reports, over the same sources and the same entry point. A
disagreement in either direction is a failure, and the message names the labels
only one side saw.

## The two conditions that keep it honest

Without these the row can be green for the wrong reason:

1. **It must have seen something.** `sys.setprofile` is per-thread and a second
   profiler silently displaces it, so under coverage or in a threaded harness the
   observer returns nothing — and nothing agrees with *nothing is missing*.
   Assert the total frame count across all modules is non-zero before comparing
   any set. This is the `utils.py` positive-control argument, and the same
   refinement applies that `ec47f4f` surfaced: the control must be over the
   population being asserted, not merely through the same function.
2. **Its unattributed list must be empty.** Every frame whose `co_filename` is a
   pinned module's file must land inside some span. A frame that matches the file
   and no span is a function `definitions()` did not enumerate, which is precisely
   the defect the gate exists to find; swallowing it makes "they agree" mean "both
   blind to the same thing".

## The two shapes that motivate it

Both are already recorded in this tree; the doc cites them rather than
re-deriving them (see the corrections section for why that distinction is
load-bearing here).

- **`FilterExpression.args_check`**, a `staticmethod` — invisible to a sweep whose
  predicate was `inspect.isfunction`, executed **85 times** over the parity
  corpus. Recorded at `tests/compared_path.py:28-40` and `:698-706`, with
  `args_check` in the same sentence as `Origin.loader_name` (a `property`),
  `Engine.template_loaders` (a `cached_property`) and `get_app_template_dirs`
  (an `lru_cache`) — four shapes a class dict holds a function in, none of them a
  function. Closed by `_invoked` (`:193`) and `residue` (`:403`), and held by
  `check_transcriptions.py`'s `COMPARED_PATH` pin plus
  `tests/test_interposition.py::test_the_compared_path_is_the_measured_one`.
- **`loader_tags`'s registry dispatch** — recorded verbatim in `registries()`'s
  docstring (`tests/compared_path.py:607-638`): "the binding sweep read 4
  functions where call frames read 7, and the three it missed were exactly
  `do_block`/`do_extends`/`do_include`", because `Parser.add_library` does
  `self.tags.update(library.tags)` (`base.py:668-670`) so `parser.tags[command]`
  is a dict value and `bindings()` walks module attributes. Closed by
  `registries()` + `rebind_registries()` (`:653`). Frames are indifferent to
  which binding was invoked, so `do_block`, `do_extends` and `do_include` appear
  with no `registries()` analogue needed.

## One shape it does not catch, and why that is correct

`GroupedResult`'s namedtuple `_tuplegetter` accessors are implemented in C. They
raise `c_call` rather than `call`, and they have no `co_firstlineno` in any module
of ours — so there is no span for a declared transcription region to overlap, and
clause (4) has nothing to protect. The gate is silent there by construction, and
should say so rather than appear to cover it. This is the boundary of the
instrument: **it observes Python frames in our files, and a member with no Python
frame in our file is outside the question it answers.**

## What it does not replace

The binding sweep stays as clause (4)'s input, because `COMPARED_PATH` needs
*labels with spans* and `definitions()` is where those come from — and
`definitions()` is not the half that has been wrong. The gate is a cross-check on
`instrumented()`, not a substitute for `measure()`.

## Where it goes

The observer beside its counterpart in `tests/compared_path.py`; the per-module
rows in `tests/test_compared_path_instrument.py`, where the instrument's other
self-checks already live. Falsify it the way that file falsifies the rest:
disable a `_invoked` case and watch the row redden with the labels only the
frames saw.

## Why it is a row and not an out-of-band script

Three instruments in this tree have rotted from being runnable-but-unrun. The
gate's value is catching the **next** container shape at the commit that
introduces it; a script nobody runs catches it at the milestone that depends on
it.

---

## Corrections on receipt, measured at `ec47f4f`, 2026-10-09

Appended rather than edited, per this project's rule that a plan's or a record's
corrections are sections and not rewrites. Four, all in the *justification* —
the instrument design below them is sound, and the two demonstrations are
already this tree's own text.

**C1 — the `args_check` span was a dated verdict quoted as current, not a
computed one.** The document gave the span as `_impl/base.py:827-844`.
Measured, `827-844` is exactly what this tree's own pin recorded for that
function at M10's close-out — `COMPARED_PATH`'s `FilterExpression.args_check` row
reads `(827, 844)` at `6f3ab07` — and it was a correct span then: at `3a5986f`
(M11's pre-Task-2 tip) the def is at `:827`, `return True` at `:844`, and
`args_check = staticmethod(args_check)` at `:846`. At `ec47f4f` the same function
is `:860-877` (`staticmethod` at `:879`). The number was true when it was
measured and false at the commit the document cites, so the defect is a **dated
verdict quoted as current** — and the rule that follows is **re-measure at the
commit you are citing**, because a correct measurement expires when anything
above it moves.

Two things make this section longer than the correction. My first-pass
explanation was itself reasoned rather than measured: I wrote that the number
"looks like Django's own span plus the transcription's marker offset", and
Django's `def args_check` is at `base.py:822` (`args_check = staticmethod(...)`
at `:841`), so the arithmetic gives `824`, not `827`. And the same defect had a
third live instance in our tree — which is why this document is now also the
place that explains why two numbers for one span both existed. It read (the
block is now `scripts/check_transcriptions.py:90-105`, corrected at `da2ee67`;
see the landing note below):

- `scripts/check_transcriptions.py` said the filter seam "adds nineteen
  lines … which shifts every function below it by nineteen: `args_check` 827-844
  → 846-863 … `token_kwargs` 1137-1181 → 1156-1200". Measured: M11 Task 2's
  commit (`679e315`) inserted **thirty-three** lines in one hunk
  (`@@ -812,6 +812,39 @@`), and this file's own `DECLARED` table gives that region
  as `(815, 847)` — thirty-three lines, seventeen executable, which the DECLARED
  comment beside it already says. The nineteen is the **arm** (at `ec47f4f` the
  identity test at `:829` through the `continue` at `:847`), not the insertion:
  the fourteen lines above it — the explanatory comment and the lazy imports —
  move these functions too. The arithmetic counted the region someone had named
  instead of what the commit did, and the pin has been right throughout —
  `args_check` `(860, 877)`, `token_kwargs` `(1170, 1214)`, each exactly `+33`
  from M10's value.
- The same block says "eleven of them are not what M10 pinned". Measured against
  `6f3ab07`'s pin, matching rows by name across every `(start, end, "name")` row
  in the file: **ten** rows moved, every one by exactly `+33`, and 52 rows have
  been added since.

The corrected text elsewhere in this document cites the tree's own sentences,
which give the finding and the 85 calls without a span — the two things that were
never in doubt.

### Landing note — the third instance is now fixed, `da2ee67`, 2026-10-09

Appended by the main session when this document landed, because a correction
section that reports a defect as live in the tree one commit after the tree
stops having it is the same dated-verdict defect the section is about. The
`check_transcriptions.py` block named above was corrected in the commit
immediately preceding this file's, and the correction went past the two numbers
this section names: the block's four errors were the file length (`1200` → the
1214 it is), the moved-row count (`eleven` → `ten`), the insertion size (`19` →
`33`, the arm being nineteen of the thirty-three), and the two derived spans
(`846-863`/`1156-1200` → `860-877`/`1170-1214`). The block also now carries the
lesson rather than only the number: *a shift belongs to the commit, not to the
block you have in mind*, with `git show <commit> -- <file> | grep '^@@'` as the
measurement. The reviewer's text above is left as written so the defect's shape
stays legible; the only in-place change is the citation, which pointed at a
block that no longer reads that way.

**C2 — "the pin did not move" was dropped.** It is not a claim this tree
supports: extending `_invoked` changes the labels `measure()` reports, so the
`COMPARED_PATH` pin is exactly what *would* move when the sweep learns a new
kind. What is true, and is in the tree at `compared_path.py:698-706`, is that
clause (4) would have accepted a declared region over an uncounted function.
The corrected sentence says that instead.

**C3 — the rotted-instruments paragraph had one example already fixed and one
that is not a rot.**

- `gen_impl_transcription.py --check` **is wired in**: `scripts/check.sh:107`
  invokes it, and `check.sh:103` records the rot it fixed ("drift guard nobody
  runs. Measured before wiring it up"). Past tense, with that citation.
- `tests/transcription_equivalence.py` was the pre-rename name; the file in the
  tree is `tests/test_transcription_equivalence.py`, which discovery collects —
  `scripts/run_unit_tests.sh:74` is the `discover -s tests -t . -p 'test_*.py'`
  invocation (the document put that pattern in `check.sh`, which calls this
  script rather than discovering itself). The tree's own account is
  `tests/test_collected_tests.py:5` and
  `tests/test_transcription_equivalence.py:29`. Past tense either way.
- `scripts/progress.py` is absent at `ec47f4f` (`git ls-tree`), so "never
  existing" holds — but a file that never existed did not *rot from being
  runnable-but-unrun*; it is a promise never built, which is a different failure
  and does not support the row-over-script argument the way a rot does.

The argument the paragraph serves is unaffected and is stronger stated
historically: three instruments in this tree have been unrun — one fixed by
wiring it in, one fixed by renaming it into the discovery pattern, one never
built — and each was found by a later milestone needing it, which is exactly the
cost the gate's row avoids.

**C4 — the falsification recipe exercises a row that already exists.** "Disable a
`_invoked` case and watch the row redden" does not redden the *agreement* row:
removing a case makes the member residue, and `instrumented()` raises
`UnhandledMember` before `measure()` is ever compared
(`tests/compared_path.py:708-714`) — a path this tree already pins
(`tests/test_compared_path_instrument.py:40,78`, the two foreign-kind classes;
the rows that exercise them are `test_the_residue_check_fires_on_a_kind_with_no_case`
at `:212` and `test_a_descriptor_from_another_module_is_reported` at `:282`). The lever that reddens the
agreement row is the one the `loader_tags` demonstration describes: drop the
registry rebinding from the wrap loop (the `rebind_registries(invoked,
replacement)` at `:746`, restored at `:761`), so the `Library` copies keep the
originals and the parse calls unwrapped bodies — the sweep then reports the 4
and the frames the 7, and the row fails naming exactly
`do_block`/`do_extends`/`do_include`. That is a **prediction** about a row that
does not exist yet, and it should be confirmed when the row is written rather
than quoted after.

## Verified on receipt, not corrected

Two mechanism claims in the document are about Python and CPython internals
rather than about this tree, so they were measured under the pinned interpreter
(`.venv/bin/python` CPython 3.14.4, `-I`, stdlib only):

- **The `inspect.isfunction` gap is real and is an access-path gap as well as a
  predicate gap.** `inspect.isfunction(vars(C)["s"])` is **False** for a
  `@staticmethod` while `inspect.isfunction(C.s)` is **True** — so a sweep over a
  class `__dict__` misses staticmethods, and one over `getattr` does not. Both
  halves matter: the historical miss is the `vars()` half.
- **A namedtuple field access raises `c_call` and nothing else.** `sys.setprofile`
  over `t.a` on a `collections.namedtuple` yields event kinds `['c_call']` — no
  `call`, no Python frame with a `co_firstlineno` in our file — so the boundary
  section's "silent there by construction" is measured rather than argued, and
  the boundary is exactly where it says it is.

