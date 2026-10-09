#!/usr/bin/env bash
# Everything that must be green before a commit.
#
# Usage:
#   scripts/check.sh            # full check, including the Django suite
#   scripts/check.sh --fast     # skip the Django suite (still runs cargo + unit)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$HERE/env.sh"

FAST=0
if [ "${1:-}" = "--fast" ]; then
  FAST=1
fi

step() { printf '\n=== %s ===\n' "$1"; }

step "cargo test"
cargo test --workspace --quiet

step "rustfmt"
cargo fmt --all -- --check

step "clippy"
cargo clippy --workspace --all-targets --quiet -- -D warnings

step "python unit tests"
# Shimmed, like the Django suite, and for the same reason: the shim loads our
# file under Django's module name, so a test module that binds `Node` before
# `install()` holds a *different class object* than one that binds it after,
# `isinstance` fails across the boundary, and `describe()`'s dispatch falls to
# its memory-address arm — measured as 537 order-dependent failures in the parity
# harness, and 8 once the shim is installed mid-run.
#
# The invocation lives in `run_unit_tests.sh` rather than here, because the
# environment it needs is three lines long and the plan's per-task commands need
# the same three lines: written twice, one of them is wrong. That script also
# pins the *discovery* — `tests/test_collected_tests.py` asserts the count this
# command collects, so a future `-p` change or a module named without `test_`
# fails a row instead of silently running fewer tests.
"$HERE/run_unit_tests.sh"

step "python anchor manifest"
# Every `` `name.py:N` `` in our prose is a claim about the pinned checkout, and a
# claim nothing re-measures is what four rounds of this task got wrong. It is on
# the *fast* path deliberately, unlike the generators below: it compiles nothing
# and needs only `.django/`, and its value is in catching a citation written in
# the commit being written rather than one discovered a commit late.
#
# `--check --frozen`, which the migration has now earned: `unmigrated` reached 0
# when the last three bare `:N` citations were named, so the two things `--frozen`
# adds — a shorthand, and a row the tree no longer cites — can now only come from
# an edit being written, which is what a gate is for. The 104 unquoted rows are
# *not* failed on: an unquoted row records no claim to falsify, and the ones left
# are cited only from files that never held a shorthand, so no naming pass will
# come back for them.
"$PROJECT_ROOT/.venv/bin/python" -I "$HERE/check_python_anchors.py" --check --frozen

step "suite counts carry their provenance"
# Every milestone is accepted on a *count*, and a count is the easiest claim to
# make vacuously: measured, running `.django/tests/runtests.py` directly with
# `DJANGO_RUST_TEMPLATES` unset produces a result byte-identical to stock
# Django's and still reports OK. The guard requires every `Ran N tests` in our
# prose to sit near something naming the run that produced it. On the fast path
# because it reads text and nothing else.
"$PROJECT_ROOT/.venv/bin/python" -I "$HERE/check_suite_counts.py"

step "transcriptions differ from upstream only where declared"
# A `# TRANSCRIBED:` file is a claim about the pinned tree, and the suite cannot
# falsify it: measured, `_impl/base.py` as a verbatim copy and `_impl/base.py`
# transcribing wrongly both keep all 1547 tests green. So the diff is the
# instrument, and this is where the parity harness's own warrant comes from —
# once `base` is interposed, `test_parser_parity` compares Rust against *our*
# transcription, and "our transcription is upstream" is exactly this step.
# Fast path: it reads text and needs only `.django/`.
"$PROJECT_ROOT/.venv/bin/python" -I "$HERE/check_transcriptions.py"

step "differential harness"
"$PROJECT_ROOT/.venv/bin/python" "$HERE/differential.py"

if [ "$FAST" -eq 0 ]; then
  # `--check` regenerates and diffs, so it says *what* differs, and it is
  # self-maintaining: a legitimate move (a new `rustc`, a new CPython, a Django
  # pin bump) is fixed by re-running the generator, not by editing a count here.
  # Both generators document `--check` as *the* way to verify currency and
  # neither was invoked anywhere, which is the failure mode this closes: a drift
  # guard nobody runs. Not on the `--fast` path because the case generator
  # compiles a Rust probe.
  step "generated files are current"
  "$PROJECT_ROOT/.venv/bin/python" -I "$HERE/gen_python_word_ranges.py" --check
  "$PROJECT_ROOT/.venv/bin/python" -I "$HERE/gen_python_case_fixups.py" --check

  # Not "(rust engine)" — but the reason has changed, and it is now measured
  # rather than assumed. On 2026-10-09 this suite called `_native` zero times,
  # which made the label assert what the run did not do. Re-measured on
  # 2026-10-09 after M9 interposed `base` (instrument:
  # `.review/m9-measurements/m9_measure_native_calls.py`): 23,263 calls from
  # 1,176 of the 1,547 tests — `resolve` 10,180; `compile_template`,
  # `builtin_tag_names_including_stubs` and `builtin_tag_stub_names` 4,361
  # each. So Rust *is* on the
  # suite's path now, and the reason the label stays is narrower: `lex` is still
  # reached zero times (`base.Lexer` is Python at M9) and `engine.py` is still
  # Django's, so "rust engine" would assert more than the run does. The
  # equivalent claim in `differential.py` no longer has to be taken on trust —
  # that harness now counts the calls and fails if the shim run reaches none.
  step "django template_tests (shim installed)"
  "$HERE/run_django_suite.sh" template_tests
fi

printf '\nall checks passed\n'
