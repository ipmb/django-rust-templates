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
"$PROJECT_ROOT/.venv/bin/python" -m unittest discover -s "$PROJECT_ROOT/tests" -t "$PROJECT_ROOT" -p 'test_*.py'

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
  #
  # The last line is not a generator but a `--check` of the same shape, and it is
  # here for the same reason: every `` `name.py:N` `` in our prose is a claim
  # about the pinned checkout, and a claim nothing re-measures is what four
  # rounds of this task got wrong. It compiles nothing and needs only `.django/`,
  # so it costs one interpreter start. Plain `--check` here, because the
  # shorthand (`:518`, naming no file) is still present; Step 5 of M6 switches
  # this to `--check --frozen`, which fails on the shorthand, once it is gone.
  step "generated files are current"
  "$PROJECT_ROOT/.venv/bin/python" -I "$HERE/gen_python_word_ranges.py" --check
  "$PROJECT_ROOT/.venv/bin/python" -I "$HERE/gen_python_case_fixups.py" --check
  "$PROJECT_ROOT/.venv/bin/python" -I "$HERE/check_python_anchors.py" --check

  step "django template_tests (rust engine)"
  "$HERE/run_django_suite.sh" template_tests
fi

printf '\nall checks passed\n'
