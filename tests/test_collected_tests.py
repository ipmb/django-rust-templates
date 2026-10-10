"""The unit suite's own collection, pinned as a number.

``scripts/check.sh`` discovers with ``-p 'test_*.py'``. That pattern is a claim
about the tree that nothing checked — and M10 Task 2 is what it cost.
``tests/transcription_equivalence.py`` held a table and its seven rows, named
without the ``test_`` prefix, so **its rows were collected by nothing**. Every
other check was green, including ``check.sh --fast``'s ``all checks passed`` with
the file sitting in the tree, and ``ShadowingTests`` went on asserting that the
table existed and had rows: a guard checking that a guard exists, while the
content it was guarding never ran.

It was found by the unit count not moving — 323 where 330 was expected. That is
the detector, and this module is it written as a row instead of as a number in a
message. Same shape as the meter's pinned count in
``tests/milestone_ownership.py``: the number moves in the commit that changes it,
so the commit has to say by how much and why.

What a count sees that a sweep over file names cannot:

* a module that *is* collected but whose rows are all skipped at runtime;
* a class that does not inherit ``TestCase``, so the loader drops it;
* a subdirectory without an ``__init__.py``, which ``discover`` cannot recurse
  into whatever its files are called — ``tests/differential/`` is that shape, and
  is currently data-only.

None of those are visible to ``grep`` over names and all of them are visible
here. The pin is not a substitute for reading the tree; it is the thing that
tells you the tree changed underneath you.
"""

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The count `scripts/check.sh`'s discovery produces. Update it in the same commit
# as the change that moves it, and only after asking whether the *delta* is the
# one you intended — a jump of four where you expected seven is what this row is
# for.
#
# 370 → 371 is M11 Task 1's registry-escape row in
# `tests/test_compared_path_instrument.py`; 371 → 374 is the same task's
# `LoaderTagsInterpositionTests`, three rows. Both deltas are the rows added, so
# no module went uncollected on the way.
#
# 374 → 378 is M11 Task 2, four rows: `test_filter_inventory`'s
# "the interposed module registers the pinned registry", `test_filter_parity`'s
# "the comparison target is not rust owned", and two in
# `tests/test_filter_parity.py`'s neighbourhood that the same task needed —
# the delta is again the rows added and not a module falling out of the pattern.
#
# 378 → 382 is the rest of Task 2, and it is written down because the pin spent a
# while four behind: the four rows are `FilterRoutingTests`' three routing rows
# (Steps 6-8: the rebound filter, the `stringfilter` coercion, and the
# doubled-wrapper discriminator) plus the `needs_autoescape` row that Step 8's
# second bullet needed. Measured the other way as well — `git diff tests/` since
# the previous commit shows `+8` rows and no removals, and 374 + 8 = 382 — so
# the whole delta is rows and no module left the pattern. A pin updated from the
# failure message alone would have taken 382 without that second measurement.
#
# 382 → 383 is the row that pins the arm's *laziness*, added when the acceptance
# suite found the defect the unit suite could not see:
# `FilterExpression(...).resolve({})` — a `dict` context — raised
# `AttributeError: 'dict' object has no attribute 'autoescape'` for every
# built-in, because the arm read `context.autoescape` eagerly. One row, and the
# delta is the row. See the amendment in the M11 plan for why nothing smaller
# than the 1547 could have caught it.
# 383 → 389 is M11 Task 3, **six** rows. This note has been wrong about that
# number twice inside one task, which is the reason it is worth reading: it was
# first written as 387 against a four-row list — `DefaultTagsInterpositionTests`'
# module resolution, the node classes compared against the pinned file, the
# rendered `{% for %}` holding our node, and the tag-rebinding escape — then 388
# when `test_tag_inventory`'s "the interposed modules register the pinned
# registries" was added while fixing that module's guard, and now 389 for
# `test_interposition`'s "the builtin filter map tracks the registry it was built
# from", which the reviewer's source-keyed-cache finding added. Each time
# discovery had moved and the recorded number had not, and each time the
# correction came from the same place: `git diff tests/ | grep -c "^+    def
# test_"` is 6 and 383 + 6 = 389, so the whole delta is rows and no module left
# the pattern. The lesson is the one this pin exists for: a count taken from the
# failure message *and not from the diff* is a count taken at one moment.
#
# `tests/test_variable_parity.py` also changed in that commit and that is not
# part of the delta: its `Variable` moved from `from django.template.base import
# Variable` to `load_pinned("base.py").Variable`, a swap of one binding for
# another rather than a row. It moved because a row above left the shim
# installed where `tests.test_shim`'s `tearDown` used to leave it out, and the
# module's guard had been green on that accident — `getsourcefile` follows a
# class through `sys.modules[cls.__module__]`, so an interposed name answers for
# the class defined in it. Same class of defect as the row above it, found the
# same way: by an unrelated commit moving the ground under it.
#
# 389 → 390 is the M11 review's teardown finding, **one** row:
# `test_shim.py::test_tearDown_restores_the_state_it_found`. `git diff tests/ |
# grep -c "^+    def test_"` is 1 and 389 + 1 = 390. What that row is *about* is
# the reason it is here rather than a note somewhere: `test_shim`'s `tearDown`
# uninstalled the shim unconditionally, so every module discovered after it ran
# against stock Django — three of them today, none red, which is the state this
# pin's own comment calls the worse one. The fix is the two-method contract in
# that class; the row is what makes it checkable, and it fails on the old code
# and on an unconditional install at teardown alike.
#
# 390 → 392 is M12 Task 0b, `TagPredicateCoverageTests`, **two** rows: the
# containment itself and the control that shows it can fail. The invariant they
# hold (`_rust_owned` ⊆ `django_own_tags()`) was asserted in a docstring and
# nowhere else, and the docstring's version of it was false in both halves — so
# this is the delta that turns a sentence into a check.
#
# 392 → 397 is M12 Task 1, `test_shim.py::NamespaceTests`, **five** rows, and the
# delta is again measured rather than read off the failure: `git diff tests/`
# shows `+5` rows in one file and nothing else, and 392 + 5 = 397. Those five are
# the new namespace's mapping, the extension the name cannot decide, the meter's
# second scope, a library we do not provide still resolving as Django's, and the
# namespace package itself being declined. The fourth is Review Focus 3's row and
# the fifth guards a refusal that is deliberate rather than a side effect of no
# `__init__.py` existing.
#
# 397 → 403 is M12 Task 2, `test_interposition.py::LibraryTagPredicateTests`,
# **six** rows. `git diff tests/` since the previous commit is `+6` rows in that
# one file and no removals, so 397 + 6 = 403. The six are the coverage of all 18
# library tags, the measured disjointness the merge order rests on, the sentinel
# that stages a library shadowing a builtin name, the cache's tracking of the
# registries it was built from, the caller's `builtins` versus the parse's own
# table, and the staged reading of `has_tag`'s clause for a library tag. Five of
# the six redden when `django_own_tags()` is reverted to the two-module merge —
# the second does not, because it measures the registries rather than the table.
#
# 403 → 404 is the M12 review's `_our_modules_in` finding, **one** row in
# `test_shim.py::NamespaceTests`: a namespace that maps to `engine` itself walks
# the other namespace's files too. The row stages the tree, so it fails on the
# unfixed function today rather than only after Task 5 creates
# `engine/templatetags/`. `git diff tests/` is `+1` row, one file, no removals.
#
# 404 → 408 is M12 Task 4, **four** rows across two files: two in
# `test_interposition.py::TzInterpositionTests` (the module resolves to `engine`,
# and each of the three tags compiles to our node class — one row, three
# subtests) and two in `test_materialise.py` (the three tz node classes are
# registered and are ours; the `BlockTranslateNode` refusal is live until Task
# 6). `git diff tests/` since the previous commit is `+4` rows in two files and
# no removals, so 404 + 4 = 408 — measured by discovery, and the failure text
# before the pin moved read `discovery found 408 tests, the pin says 404`.
#
# 408 → 410 is the post-Task-4 review batch, **two** rows in
# `test_interposition.py::FabricatedNameTests`: the registry check that makes the
# `unregistered_` prefix trustworthy, and the control that shows it is neither
# vacuous over an empty registry nor blind to a name. The batch's other two
# pieces move no count — the `m12_not_a_tag` → `unregistered_tag` rename is a
# rename, and the Rust-side rows are `cargo test`'s, not this suite's. `git diff
# tests/` since `f89a9d4` is `+2` rows in one file, no removals.
#
# 410 → 414 is M12 Task 5, **four** rows in two files: three in
# `test_interposition.py::L10nInterpositionTests` (the module resolves to `engine`;
# the tag and its three accepted forms compile to our `LocalizeNode`; and the two
# filters resolve to our module — one row, the `localize`/`unlocalize` subtests)
# and one in `test_materialise.py` (`LocalizeNode` is registered and is ours).
# `git diff tests/` since `b83ec01` is `+4` rows in two files, no removals, so
# 410 + 4 = 414 — measured by discovery before the pin moved, and the failure text
# before the pin moved read `discovery found 414 tests, the pin says 410`.
#
# 414 → 426 is M12 Task 6a, **twelve** rows in three files:
# `test_interposition.py::I18nInterpositionTests` contributes **eleven** (the
# module resolves to `engine`; the eight names compile to our seven classes; the
# `as var` and `noop` render branches; the six refusal messages; `{% language %}`'s
# argument and body; and four rows for the argument scan's branches — the
# duplicate check, the `context` value set, the unvalidated `as` name, and the
# trailing-option message — plus the language argument-before-body order).
# `test_materialise.py` contributes **one** (`test_the_i18n_node_classes_are_registered_and_are_ours`,
# seven subtests over the seven names plus the `BlockTranslateNode` absence).
# `test_base_parity.py` and `test_interposition.py::LibraryTagPredicateTests` move
# no count: the first is a docstring fix, the second re-points its staged name
# from `trans` to `blocktranslate` because `trans` became Rust's in this commit —
# a modification, not an addition. `git diff tests/` since `b83ec01` is `+12` rows
# in three files, no removals (measured by discovery, and the failure text before
# the pin moved read `discovery found 426 tests, the pin says 414`).
#
# 426 → 432 is M12 Task 6b, a **net six** in three files — and it is a net
# because one row *left*. `test_interposition.py::I18nInterpositionTests`
# contributes **six** (the token stream and the absent nodelist;
# `{% blocktranslate %}` inside `{% block %}` with `{% plural %}`'s
# unregisteredness; the four message shapes plus the two end-tag ones; trap 2's
# own-token-before-exhaustion case; the nine option-scan messages with the
# malformed-`context` filter; and the render rows with the count-type refusal).
# `test_parser_parity.py` contributes **one**
# (`test_a_block_translate_body_is_a_list_of_real_tokens`) — the class the new
# corpus rows carry. `test_materialise.py` **loses one**:
# `test_the_refusal_is_live_for_block_translate_node_until_task_6` is deleted in
# the commit that registers the entry, which its own docstring said was its
# whole purpose; the registration row beside it gains `BlockTranslateNode`
# as a modification, not a row. `LibraryTagPredicateTests`' staged-name row is
# likewise repointed, not added. `git diff tests/` since `ec47f4f` is `+8/-1`
# rows in three files, so 426 + 8 - 1 = 432 — measured by discovery (and the
# failure text before the pin moved read
# `discovery found 432 tests, the pin says 426`).
#
# 432 → 437 is M12 Task 7, **five** rows in one file,
# `test_interposition.py::FilterRoutingTests`: the counter that the nine library
# filters reach Rust; the coverage row (nine names, same handle under each); the
# disjoint-and-injective row (66 names over 66 handles, no name in two of the six
# tables); the merge-order sentinel (a library's `lower` cannot take the name from
# `defaultfilters`); and the cache-tracks-a-library-registry row. The corpus rows
# and the registry constants that moved with this task are **modifications, not
# methods** — `tests/filter_cases.py` grows rows inside two lists and
# `tests/test_filter_parity.py` and `tests/test_filter_inventory.py` re-point
# existing assertions — so none of them moves this count. Measured by discovery:
# `git diff tests/` since `4e0cee6` adds exactly five `def test_` bodies.
#
# 437 → 440 is M12 Task 8's compared-path agreement gate, **three** rows in one
# file, `test_compared_path_instrument.py::ComparedPathAgreementTests`: the
# per-module cross-check of the pin against a profiler that wraps nothing, and
# the two rows that pin what the comparison's one tolerance does at each of its
# boundaries (a group of members sharing a span, and a frame no span covers).
# `tests/parser_corpus.py` gains `parity_sources()` (so that this gate and the
# pin's own re-measurement row measure the *same* corpus rather than two
# rebuildings of it), `tests/test_interposition.py`'s pin row now calls it, and
# `tests/compared_path.py` gains the observer — none of the three is a
# `def test_`, and none of them moves this count. Measured by discovery:
# `git diff tests/` since `a2bd289` adds exactly three `def test_` bodies and
# removes none.
#
# 440 → 442 is the scope correction, a **net two** in one file, and it is a net
# because one row *left*. `test_milestone_ownership.py` gains **three** —
# `test_the_engines_remaining_work_is_one_module` (the correction as a count,
# with the control that the subtraction bites),
# `test_the_declared_names_are_the_excluded_ones` (membership against
# `_shim.NOT_THE_ENGINE`, reasons here) and
# `test_the_declared_names_are_real_modules_the_meter_drops` (per name, so a
# failure names the module) — and **loses one**:
# `test_the_undecided_names_are_the_target_and_carry_their_reason` asserted
# `UNDECIDED == {"autoreload", "context_processors"}`, which was the best answer
# while the target was zero and is not the question any more; its subject moved
# to `NOT_THE_ENGINE` and its assertions to the rows above. Two other rows were
# *renamed* (`test_the_meter_is_the_owners_plus_the_undecided` →
# `..._the_claimed_minus_the_taken_minus_the_declined`, and the partition row's
# bucket list), which moves no count. Measured by discovery:
# `git diff tests/` since `2761a88` is `+3/-1` rows in that one file, so
# 440 + 3 - 1 = 442.
#
# 442 → 443 is M13 Task 1, a **net one** in one file, and it is a net for the
# same reason the scope correction's was: one row left as two arrived.
# `test_shim.py` loses `test_strict_mode_raises_while_falling_back` — its
# premise, that the meter still names a module, expired in the commit that took
# the meter to zero — and gains two:
# `test_strict_mode_accepts_a_complete_engine` (the new true state: a strict
# install now completes) and `test_strict_mode_raises_when_a_module_is_still_
# djangos` (the refusal branch re-triggered by appending a directory to
# `django.template.__path__`, which is a real lever rather than a patch).
# `test_milestone_ownership.py`'s `test_the_engines_remaining_work_is_one_module`
# was **renamed** to `..._is_empty`, which moves no count — the row and its
# assertions are the correction's, only the claim in the name moved. Measured by
# discovery: `git diff tests/` since `1d05fb1` is `+2/-1` rows, in `test_shim.py`
# alone, so 442 + 2 - 1 = 443.
#
# 443 → 444 is the M13 Task 1 review's strict-mode finding, **one** row in
# `test_shim.py`: `test_strict_mode_refuses_under_the_production_ordering`. The
# two rows the task added are each other's control but both `uninstall()` first,
# so neither has the shim installed when the strict call happens — and that is
# the state the sitecustomize shim (`scripts/shimpath/`, deleted with the rest of
# the substitution apparatus in M18) guaranteed in a real run,
# where the check used to sit behind `install`'s early return and could not fire
# at all. The row stages a module, installs non-strict, and asserts the refusal
# twice; the fix is `_refuse_if_incomplete()`, called from both paths. `git diff
# tests/` since `547bfb9` is `+1` row, in one file, no removals, so 443 + 1 =
# 444 — and the same diff is why the count moving by one here is the whole
# story: the fix in `_shim.py` is a function and two call sites, not rows.
#
# 444 → 447 is M13 Task 2, **three** rows and a **new module**, which is why the
# delta is worth measuring rather than reading: `tests/test_partials_rust.py` is
# this task's, and a new file is the one case where "the rows moved" and "a
# module entered or left the pattern" look alike from the failure text alone.
# `git diff tests/` since `08de820` is one new file with three `def test_` and no
# removals anywhere, so 444 + 3 = 447 and every one of the three is a row rather
# than a module's worth of rows arriving at once. The three are the seam's live
# view twice over — a name that was never defined compiles, a definition written
# *after* the node was built is visible through it, and two parsers see only
# their own `extra_data`.
#
# 447 → 449 is M13 Task 3, **two** net rows: `git diff tests/` since `b266741` is
# `+3/-1`, and the subtraction is a rename rather than a removal.
# `tests/test_partials_rust.py` gains
# `test_the_tag_now_compiles_and_fails_at_render_instead` (the tag's failure
# *moves* — compile-time `UnimplementedTag` to Django's render-time
# `TemplateSyntaxError`, `defaulttags.py:430-435`) and
# `test_the_argument_errors_are_djangos_two_messages_end_to_end` (the wildcard
# arm's one message, through the shell, for both arities). The `-1` is
# `tests/test_tag_inventory.py`'s
# `test_the_only_two_unimplemented_entries_are_the_m13_pair` becoming
# `test_the_last_unimplemented_entry_is_the_m13_pair_tail`: same row, but its
# literal moved too, `{"partial", "partialdef"}` → `{"partialdef"}`, which is the
# whole point of renaming it. So 447 + 3 - 1 = 449. Measured by discovery, not
# read from the failure text: that text says "2 rows" whether the two arrived as
# two additions or as three additions minus one rename, and only the second is
# what happened.
# 449 → 457 is M13 Task 4, **eight** net rows, and the diff's two numbers are both
# worth writing down: `git diff tests/` since `cea06d9` is `+12/-4`, so the net
# and "eight new rows and no rename" are the same +8 while the shape is not.
# `tests/test_partials_rust.py` gains **two classes, eight rows between them** —
# and this count was **read rather than run** on its first pass: the hand
# enumeration named `PartialDefSeamTests` and stopped, as though the file had
# gained one class, while `git diff cea06d9 24bed0f -- tests/ | grep -c '^+    def
# test_'` had said **12** all along — the command was right and the application
# of it was partial, which is the opposite of a tool with a blind spot and is
# fixed by running the command rather than by improving it. Discovery then
# disagreed by four, and the four are the second class. Both classes are Task
# 4's, because Task 4 is what needs them.
#
# `PartialDefSeamTests`, four rows and all of them the ones Tasks 2 and 3 could
# not write because neither tag existed end-to-end until this task: a use
# *before* its definition renders (the live mapping observed through the shell
# rather than through a Rust row), an `inline` definition renders at the point of
# use, a duplicate definition names the origin template
# (`defaulttags.py:1252-1256`, the check and the message quoting
# `parser.origin.name`), and the stored `PartialTemplate` and the returned
# `PartialDefNode` hold one nodelist **by identity** — the last one needs a spy on
# the seam, because two equal-but-not-identical trees render identically.
#
# `ReferenceTableTests`, four more, are Task 5's rows landing in Task 4's commit:
# Task 5 was written **conditional** on its identity row failing, and the
# condition was answered by the acceptance suite instead of by the row (27 red on
# a filter expression reaching a body with no reference table), so the rows and
# the change they pin are Task 4's by construction. A body containing a filter
# expression materialises at all; a partial body and the outer template share one
# `CycleNode` **by identity**, which output cannot witness; the same shape
# end-to-end renders the pinned tree's `'aaa'`; and a stateful library tag in a
# body is built once (`'012'`), which guards a *different* defect — a body
# re-parsed or re-materialised per render — and is named in the file as not the
# table's proof.
#
# The other four rows are three renames and one re-pairing: `tests/test_tag_inventory.py`'s
# `test_the_last_unimplemented_entry_is_the_m13_pair_tail` became
# `test_no_builtin_tag_is_still_a_stub`,
# `tests/test_parser_parity.py`'s
# `test_an_unimplemented_tag_is_its_own_kind_at_the_ffi` became
# `test_the_unimplemented_kind_has_no_producer_and_still_maps`, and
# `tests/test_host_tags.py`'s two rows became
# `test_the_nested_region_never_reaches_the_partial_seam` and
# `test_the_bare_template_reaches_the_seam_once`. Both of the last two are the
# same move — the discriminator stopped being "Rust compiles this tag" (there is
# no tag only one side compiles now) and became "the Rust *route* is not taken" —
# which is why it is the one change here that had to replace two rows with two
# rows rather than rename one. So 449 + 12 - 4 = 457.
# 457 → 462 is M14 Task 1, **five** rows and one new file:
# `tests/test_acceptance_preflight.py` drives `engine_fallbacks()` with a
# fabricated meter. The pre-flight's only other exercise is a healthy run, and a
# guard observed green is a guard that might be a no-op. Four rows walk the
# fabricated meter through the two scopes (empty reports nothing, one scope, the
# other scope, both); the fifth, `test_the_fabricated_names_are_not_real_modules`,
# is the control that says the fabricated name is drawn from the meter's own
# population and lies outside it — so the other four cannot be overtaken by a
# later milestone that registers the name for real. Discovery agrees exactly:
# 457 + 5 = 462, and there is no rename in this delta.
# 462 → 464 is M14 Task 3, **two** rows in `tests/test_milestone_ownership.py`,
# both about §3 rather than about the tree: `test_the_specs_declaration_is_the_shims`
# ties `_shim.NOT_THE_ENGINE` to the names §3 actually enumerates (§3 was the one
# document in that three-way tie nothing read), and
# `test_the_frame_reader_refuses_a_moved_anchor` is its control — the reader
# parses prose, and its failure arm is the one a green run never reaches.
# 462 + 2 = 464.
# 464 → 478 is M15 Task 1, **fourteen** rows and one new file:
# `tests/test_suite_outcome.py` drives `scripts/assert_suite_outcome.py` with
# fabricated transcripts. The gate exists because `.review/m15-recon.md` §3.1
# measured that nothing read the acceptance run's own summary line, so §2's
# central count was enforced by prose. Two rows hold the healthy side (the
# declared outcome is accepted; a refusal names the undeclared skip), four the
# summary line (a failure, an error, `expected failures=1` — which prints `OK`
# and exits 0, the reason the line is compared whole — and its mirror, an
# unexpected success), and the rest the parse: a truncated transcript, a doubled
# skip line, a swap that keeps the count at six, a changed run count, a skip on
# a docstringed test, teardown lines after the verdict, plus the tie to §2's
# prose and its control. Discovery agrees exactly: 464 + 14 = 478, no renames.
# 478 → 487 is M15 Task 2, **nine** rows and one new file:
# `tests/test_checkout_pinned.py` drives `scripts/assert_checkout_pinned.py`,
# which is §2:33-34 — "Django's checkout is never modified". Nine rather than the
# eight the plan's step predicted, which is the plan's count and not discovery's:
# three on `lock_values`/`dirt`, four on `refusals`' arms (clean, wrong sha, a
# modified tracked file, an untracked one), and two against the live checkout —
# the subject itself, and a path with no `.git`, which is the route a tarball
# takes and the one that used to surface as a traceback. 478 + 9 = 487.
# 487 → 492 is M17, **five** rows in one new file: `tests/test_packaging_guard.py`
# covers the version guard an *installed* wheel needs, which is the one thing
# every other gate here cannot see — they all run against the pinned checkout. The
# rows are the positive control (the transcription pin itself), the two refusals
# (`6.1.2`, which used to install cleanly and pass everything, and `5.2.7`, which
# used to fail in Django's name), the ordering contract (a refused install leaves
# no finder behind), and the escape hatch. Five rather than the six the design
# predicted: the absent-Django row was dropped *after* being measured, because its
# only hermetic lever — `mock.patch.dict(sys.modules, …)` — restores the entire
# module table on exit and turned four later rows into stale-capture failures that
# named nothing they tested. That case is covered end-to-end in
# `scripts/check_packaging.sh`, against a real venv with no Django, which is the
# population it actually occurs in. 487 + 5 = 492.
#
# 492 → 463 is M18, a **net −29**, and it is the first entry in this ledger whose
# largest terms are *deletions* rather than renames. M18 removed the substitution
# apparatus — `_shim.py`, `_impl/`, `install()`/`uninstall()`/`unshimmed()` and
# `scripts/shimpath/` — and moved the engine to `django_rust_templates.engine`, so the
# only activation left is `TEMPLATES[0]["BACKEND"]`. The rows followed their subjects:
#
# * `tests/test_shim.py`, deleted whole: **−23**. Its subject was the finder, and there
#   is no finder.
# * `tests/test_acceptance_preflight.py`, deleted whole: **−5**. Its one surviving
#   question — does anything of ours shadow a module of Django's? — is now
#   `test_interposition.py::test_our_package_shadows_nothing_and_djangos_siblings_are_intact`,
#   asserted against the filesystem rather than against a finder's mapping.
# * `tests/test_interposition.py`: **+3/−8**, a net **−5** (88 → 83). Each of the eight
#   read the finder's table — `test_utils_resolves_to_our_file`,
#   `test_the_engine_handler_class_is_not_djangos`,
#   `test_the_package_still_reaches_djangos_siblings`; the three that arrived are the
#   same claims about names that are ordinary package names now, among them
#   `test_every_empty_pin_is_a_measurement_and_not_a_silence`.
# * `tests/test_milestone_ownership.py`: **+4/−7**, a net **−3** (10 → 7). The seven had
#   `_shim.NOT_THE_ENGINE` or the meter for a subject (and the meter is gone: there is
#   nothing left to not-own); the four carry what survives — §3's declaration against
#   the *tables* rather than the shim's constant, and
#   `test_the_coverage_count_is_empty_in_both_scopes`.
# * `tests/test_packaging_guard.py`: **+8/−2**, a net **+6** (5 → 11). The one file that
#   grew, because its *door* moved: the guard used to be reached by a row calling
#   `install()` and is now reached by constructing `RustTemplates`, so the rows were
#   re-pointed at the constructor and the two `_shim` rows went with the ones it
#   replaced.
# * `tests/test_compared_path_instrument.py`: **+1/−0**, a net **+1** (16 → 17).
#
# So 492 − 23 − 5 − 5 − 3 + 6 + 1 = 463.
#
# **The whole delta is accounted for, and that is measured rather than asserted.**
# `git diff --name-status 14f5fe4 -- tests/` is two deletions and thirty-seven
# modifications; running this ledger's own command over every one of those modified
# modules — its `^    def test_` count now against `git show 14f5fe4:<path>` — prints
# exactly the four names above and no others. A file that had moved its row count
# without appearing in that list would be a delta arriving from nowhere, which is the
# failure the M13 Task 4 entry below is long enough to have seen.
#
# This entry leans harder than its predecessors on one thing worth naming: the four
# deltas, and both deleted files, are counts of `^    def test_` lines — a *proxy* for
# what discovery collects, not the thing itself. It is not assumed. Discovery over the
# present tree was compared against that grep per module, for all 34 modules, and the
# two agree: 463 either way, no module differing. A class that did not inherit
# `TestCase`, or a row assembled in a loop, would separate them; separating them is
# what the row below this comment is for. On this tree the proxy and the count are the
# same number, which is what makes the deltas above the deltas discovery reports.
#
# 463 → 470 is **+7**, one new file and nothing else: `tests/test_suite_isolation.py`,
# seven rows. It is not part of M18's −29 because it is not a row following a subject
# that moved — it is a row for a hole M18 *left*, found while revising the spec rather
# than while building the milestone. The seventh arrived a turn later, in the review of
# that revision: the first six assert the declared adapter set, and the seventh
# re-derives each entry against the pinned checkout, so the set is **inheritance** —
# each is an import Django's own file makes — rather than a licence somebody granted.
#
# The hole: `django_suite/suite_settings.py` names the `BACKEND` the whole acceptance
# run uses, and **no gate read it**. `gen_django_suite.py`'s `TREES` covers
# `django_suite/template_tests` and `django_suite/i18n` only, so `check()` walks those
# two subtrees and neither diffs nor sweeps the file at the suite's root. Changing its
# one `BACKEND` string to Django's turns the 1547 into a test of stock Django, with
# `gen_django_suite.py --check` still exiting 0 and every other gate still green —
# the arrival `test_interposition.py`'s docstring names, in the one place where it
# costs the most. The other three rows close the second half: the vendored tree's
# remaining imports of `django.template*` are asserted against a **declared closed
# set** of seven adapter statements, so a new one fails instead of arriving green.
#
# Both halves are driven by a fabricated input as well as by the real file, because a
# row that only ever reads the real file cannot show it would refuse a wrong one —
# and both were confirmed to fire by mutating the real tree (the `BACKEND` string, and
# a fabricated `from django.template.base import Node`), each restored byte-exact and
# checked by sha256. Counts at close: 470 collected, and the `^    def test_` proxy
# agrees with discovery for all **36** modules. That 36 is measured per module and not
# derived from the deltas above: `tests/test_*.py` is 36 files and discovery collects
# from 36, a bijection with no module either way. The earlier entries in this ledger
# say 34, and that number is not re-derived here — it belongs to the tree it was taken
# on, and this entry is about a different one.
#
# 470 → 472 is **+2**, in the same file and in the same round, and the two are **the
# eighth row and its control** — the ninth. A first draft of this sentence said "one a
# fix and one a row", which is the arithmetic failing: the fix is a one-token change to
# the *seventh* row and adds no test, so a round described as one fix plus one row can
# move the count by one, not two. The module holds nine rows and 463 + 9 = 472, which
# is what settles it. **The fix first, because it is the one that changes a verdict.**
# The seventh
# row re-derives each declared adapter against the pin, and it built each pair's
# *name* from the pin while taking its *module* from the statement under test. Every
# module `django_imports_in` yields is a `django.template` path by construction of the
# walk, so the module half of the key was in the set whatever the pin contained:
# `from django.template import mock` — `unittest`'s name, bound by no Django module —
# passed a row whose entire job is refusing exactly that. One token, `node.module`.
# Re-measured over the real tree and the real pin
# (`.review/m18-measurements/probe_adapter_module_axis.py`, with its transcript), the
# two constructions differ on three pairs — which the old key admits and the new one
# refuses — and on **none** of the seven declared adapters, so this is a change of
# question that the set happens to answer the same way, not a change of policy that
# leaves it green.
#
# **The eighth row is the other finding, and it is not a fix to the seventh**: no
# amount of repair to "is this import Django's own?" makes it ask "is this import the
# engine?". `from django.template.base import Node` is
# `.django/tests/template_tests/test_custom.py:4` — upstream's own line — so the
# seventh row passes it, and it is the one statement the declared set exists to
# refuse. A person adding it to `DECLARED_ADAPTERS` would have been waved through by
# the row written to catch them. The two questions now have a row each: *inherited?*
# and *plumbing rather than engine?* The second reads §3's declaration out of
# `milestone_ownership.NOT_THE_ENGINE` instead of restating eleven names here, and
# derives its one non-module licence — `from django.template import engines`, an
# attribute of the package root, which no module key can hold — from the pin's
# `engines = EngineHandler()` rather than from a name typed in this file. Its control
# drives the predicate with eight cases, and one of them is that upstream line
# verbatim. Counts at close: 472 collected, proxy and discovery agreeing for all
# **36** modules — the same 36, since neither row arrived in a new file.
#
# 472 → 474 is **+2**, both in `tests/test_suite_isolation.py`, and the delta is the
# rows: `GateBuildTests`' "the gate builds the extension before every consumer" (named
# "…before the python suites" when it landed, and renamed by the entry below), and
# `VendoredImportTests`' "the closure propagates a name across two hops". Neither
# is a repair to an existing row, so the count moves by exactly the two — measured
# the other way as well, since that is what this ledger's own rule asks: the module
# now holds **11** rows where the previous entry records nine, and 472 + 2 = 474.
# Both came from a review that failed on the *gate* rather than on the predicate: the
# first because `check.sh` ran no build at all, so the 472 unit rows and the 1547
# acceptance tests exercised whatever `.so` was installed — a **debug** build, for
# several rounds of green runs — and the second because the fixed point's propagation
# branch is empty on the pin, so nothing could tell the loop from a single pass.
# Counts at close: 474 collected, proxy and discovery agreeing for all **36**
# modules, the same 36 again.
#
# 474 → 477 is **+3**, all in one **new** module, `tests/test_native_is_the_built_artifact.py`,
# and the three are the rows that drive `scripts/assert_native_is_the_built_artifact.py`: the
# positive control, the refusal (twice over — a foreign build, and the same-length
# file a *size* comparison would admit), and the release-profile glob's `.d`
# exclusion. Measured the other way, as this ledger's rule asks: the new module holds
# **3** rows, `tests/test_suite_isolation.py` still holds 11 — the round renamed and
# strengthened its build row rather than adding one — and 474 + 3 = 477.
#
# **The round is the behavioural half of the previous one's fix, and the reason it
# needed a new file is the reason the previous entry is not enough.** `GateBuildTests`
# pins the `maturin develop --release` invocation and its position, and it is
# *textual*: it cannot fail if the build succeeds and installs somewhere the tests do
# not read. So the count moves by the rows that check the artifact rather than the
# script, and the build row itself moved without adding a test — from "before the
# python suites", which named one step, to "before every consumer", which asserts the
# build precedes the first line invoking the venv's Python or a script under
# `scripts/`. The old form pinned a proxy: it was equivalent to the property only
# while the unit run happened to be the first consumer, which it did not assert, and
# `differential.py`, `check_transcriptions.py` and the acceptance run all read the
# extension. Counts at close: 477 collected, proxy and discovery agreeing.
#
# 477 → 479 is **+2**, both in `tests/test_render_parity.py` and both about one
# input class: `2.`, which `Variable.__init__` gives a `2.0` literal *and* the
# lookups `("2", "")`. `RenderParityTests.test_a_trailing_dot_numeral` asserts the
# bytes and `ResolverParityTests.test_a_trailing_dot_numeral_is_a_path_not_a_number`
# asserts the dispatch, against Django's `resolve` in the first case and its
# `_resolve_lookup` in the second. The first would pass an engine that refused every
# trailing-dot numeral; the second would pass an engine that returned a number. Measured
# the other way, as this ledger's rule asks: no module lost a row, no other module
# gained one, and 477 + 2 = 479. See `.review/m19-measurements/lookup_roundtrip.txt`.
#
# 479 → 484 is **+5**, all in `tests/test_base_parity.py`, and they are the two
# halves the reviewer asked for before step 2's boundary widened plus one the gate
# asked for. Three are `NativeEscapeTests` — `_native.html_escape` against
# `django.utils.html.escape` on the strings it answers, `None` rather than a raise
# for a lone surrogate, and the refusal read the way an arm reads it, on input
# where a dropped escape would show. The fourth is `RenderValueInContextTests`'
# `test_the_int_arm_declines_when_grouping_is_on`, which is the only row in the
# tree that runs the `int` arm's predicate with `USE_THOUSAND_SEPARATOR=True`, so
# it is the only one that can see the `settings` read at all. The fifth,
# `UnconfiguredSettingsTests`, arrived from `scripts/differential.py` going red:
# with no settings configured the two engines refuse to render an `int` and the
# `int` arm makes them name different settings in the refusal, and the harness now
# configures settings — so the row is where that difference stays visible.
# Measured the other way, as this ledger's rule asks: `git diff -U0 tests/` for
# this round adds **5** `def test_` lines and removes none, and no module but this
# one is touched by the added rows — the other test files in the diff changed a
# comment and two line numbers. The grep that counts them returns **six** matches
# on this commit, because the sentence you are reading contains the string it
# searches for; the count is the five `def`s and the sixth is this prose.
# 479 + 5 = 484.
#
# 484 → 487 is **+3**, all in `tests/test_base_parity.py` and all in one new
# class, `FilterSeamRefusalTests`, which is slice 1b's own subject: a Python
# `str` holding a lone surrogate, which Rust's `&str` cannot hold at all. The
# first row is the shell's read — six templates through both engines across
# four surrogates and both autoescape settings, **48 renders, 0 diverge** — and
# it is the row that would have caught the sentinel leaking into `obj`, because
# a leaked sentinel renders as a string rather than raising. The second is the
# one the reviewer's finding asked for: `_native.filter` returns a singleton for
# a refusal, and a `None` from `default` on a falsy value is an *answer*, so the
# row asserts both readings off one call site rather than asserting "not None".
# The third is the positive control over the same six templates with an ordinary
# value, because a row asserting equality on a refusal says nothing about the
# path a caller takes when the seam *does* answer.
# Measured the other way, as this ledger's rule asks: `git diff -U0 tests/` adds
# **3** `def test_` lines and removes none, and no module but this one gained a
# row — the other two test files in the diff changed one line each, a re-keyed
# citation in `test_filter_inventory.py` and `test_interposition.py`. 484 + 3 = 487.
#
# Counts at close: 487 collected, proxy and discovery agreeing.
PINNED = 487


def collected() -> unittest.TestSuite:
    """The suite ``check.sh`` runs, discovered the way ``check.sh`` runs it."""
    return unittest.TestLoader().discover(
        start_dir=str(PROJECT_ROOT / "tests"),
        pattern="test_*.py",
        top_level_dir=str(PROJECT_ROOT),
    )


class CollectionIsPinned(unittest.TestCase):
    def test_the_collected_count_is_the_pinned_one(self):
        found = collected().countTestCases()
        self.assertEqual(
            found,
            PINNED,
            f"discovery found {found} tests, the pin says {PINNED}. If the delta "
            "is smaller than the rows you added, a module's name is not "
            "matching `-p 'test_*.py'` and its rows are running for nobody — "
            "which is exactly what `check.sh --fast` cannot tell you, because "
            "it prints `all checks passed` either way.",
        )

    def test_no_module_the_pattern_misses_holds_test_rows(self):
        """The count above is the detector; this is the diagnosis.

        A count tells you something is wrong and not what. This walks the same
        directory for modules the pattern skips and fails if one of them defines
        a ``TestCase`` — turning "the number is off by seven" into the name of
        the file whose rows nobody runs.
        """
        import ast

        offenders = []
        for path in sorted((PROJECT_ROOT / "tests").glob("*.py")):
            if path.name.startswith("test_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and any(
                    isinstance(base, ast.Attribute) and base.attr == "TestCase"
                    for base in node.bases
                ):
                    offenders.append(f"{path.name}:{node.lineno} {node.name}")
        self.assertEqual(
            offenders,
            [],
            "these modules define test cases but do not match `-p 'test_*.py'`, "
            f"so nothing collects them: {offenders}",
        )

    def test_every_test_directory_is_a_package(self):
        """The shape the count *cannot* see, and the reason this row exists.

        ``discover`` recurses only into directories that are importable packages.
        A ``tests/subdir/test_thing.py`` with no ``tests/subdir/__init__.py`` is
        therefore collected by nothing — and, unlike the top-level case above,
        **the pinned count does not move when it happens**, so the detector is
        blind to it by construction. `tests/differential/` has that shape today
        and is data-only, which is fine; the row fires the moment one of its
        files grows a ``TestCase``.
        """
        import ast

        offenders = []
        for subdir in sorted(p for p in (PROJECT_ROOT / "tests").iterdir() if p.is_dir()):
            if subdir.name == "__pycache__":
                continue
            holds_rows = any(
                path.name.startswith("test_")
                or "TestCase" in path.read_text(encoding="utf-8", errors="replace")
                for path in sorted(subdir.glob("*.py"))
            )
            if holds_rows and not (subdir / "__init__.py").is_file():
                offenders.append(subdir.name)
        self.assertEqual(
            offenders,
            [],
            "these directories hold test code but are not packages, so "
            f"`discover` cannot recurse into them: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
