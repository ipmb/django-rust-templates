#!/usr/bin/env bash
# Run the project's own unit suite against the Rust-backed engine.
#
# Usage:
#   scripts/run_unit_tests.sh                                  # the whole suite
#   scripts/run_unit_tests.sh tests.test_interposition -v
#   scripts/run_unit_tests.sh tests.test_shim.SubstituteDjangoTemplateTests
#
# Everything after the script name is handed to `unittest` unchanged, so a
# single class, a single row and a `-v` all work the way they would by hand.
#
# Why this exists rather than a line in the plan: the invocation is not just
# `python -m unittest`. Our shim has to be installed at *interpreter start*, not
# by the first test that needs it — it loads our file under Django's module name,
# so a test module that binds `Node` before `install()` holds a different class
# object than one that binds it after, `isinstance` fails across the boundary, and
# `describe()`'s dispatch falls to its memory-address arm. Measured as 537
# order-dependent failures in the parity harness, 8 once the shim is installed
# mid-run, and 0 when it is installed at session start.
#
# `shimpath` is what makes that happen: it holds a `sitecustomize` that installs
# the shim, and only when DJANGO_RUST_TEMPLATES=1. `scripts/check.sh` needs
# exactly the same invocation, and it and `run_django_suite.sh` used to spell the
# environment out separately — so this is the one place it is written, and both
# callers source it rather than retyping it.
#
# Set DJANGO_RUST_TEMPLATES=0 to run the same labels against stock Django, which
# is how you tell a failure of ours from a failure of the harness's.
#
# **This script alone does not clear a commit.** It runs the unit suite and the
# shim check and nothing else, and at least one class of defect is visible only
# to `check.sh`: a citation of a file the anchor manifest does not carry passes
# every row here and fails there. That is worth stating rather than leaving to be
# discovered, because the failure mode is a green run that a contributor reads as
# "done" — the same shape as `check.sh --fast` printing `all checks passed` while
# a module's rows are collected by nothing, which is the thing
# `tests/test_collected_tests.py` exists to make visible. The two entry points
# prove different things; this one's proof is the narrower.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$HERE/env.sh"

CHECKOUT="$PROJECT_ROOT/.django"
if [ ! -d "$CHECKOUT" ]; then
  echo "no Django checkout at $CHECKOUT — run scripts/fetch_django.sh" >&2
  exit 1
fi

# Order matters: our `sitecustomize` must be found before the checkout's own
# top-level modules, and the checkout must be importable as `django`.
export PYTHONPATH="$HERE/shimpath:$CHECKOUT:${PYTHONPATH:-}"

# One pre-flight, not two: this block used to be a weaker copy of
# `assert_engine_installed.py` — it named the meter and did not refuse on it —
# which is the "written twice, one of them is wrong" failure this script's own
# docstring warns about, one level up.
if [ "${DJANGO_RUST_TEMPLATES:-1}" = "1" ]; then
  "$PROJECT_ROOT/.venv/bin/python" "$HERE/assert_engine_installed.py"
fi

# `discover` with the same arguments `check.sh` used before this script existed,
# so the pinned collected count (`tests/test_collected_tests.py`) is a statement
# about this command rather than about a copy of it.
if [ "$#" -eq 0 ]; then
  set -- discover -s "$PROJECT_ROOT/tests" -t "$PROJECT_ROOT" -p 'test_*.py'
fi

cd "$PROJECT_ROOT"
exec "$PROJECT_ROOT/.venv/bin/python" -m unittest "$@"
