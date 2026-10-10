#!/usr/bin/env bash
# Run the project's own unit suite.
#
# Usage:
#   scripts/run_unit_tests.sh                                  # the whole suite
#   scripts/run_unit_tests.sh tests.test_interposition -v
#   scripts/run_unit_tests.sh tests.test_filter_inventory.FilterInventoryTests
#
# Everything after the script name is handed to `unittest` unchanged, so a
# single class, a single row and a `-v` all work the way they would by hand.
#
# **What this script used to be for, and why that is gone.** It existed to
# install the substitution shim at *interpreter start* rather than by the first
# test that needed it: the shim loaded our file under Django's module name, so a
# test module that bound `Node` before `install()` held a different class object
# than one that bound it after, `isinstance` failed across the boundary, and
# `describe()`'s dispatch fell to its memory-address arm — measured as 537
# order-dependent failures in the parity harness, 8 with the shim installed
# mid-run and 0 at session start. `scripts/shimpath/sitecustomize.py` did the
# installing, gated on `DJANGO_RUST_TEMPLATES=1`, and this was the one place that
# environment was spelled out.
#
# M18 deleted all of it. The engine is `django_rust_templates.engine`, imported by
# name; there is one class object per class because there is one name per module,
# and no ordering rule to get right. So the invocation is now the bare one, and
# the script is a convenience rather than a correctness requirement — which is a
# change worth stating, because the three paragraphs above used to be a warning.
#
# **This script alone does not clear a commit.** It runs the unit suite and
# nothing else, and at least one class of defect is visible only to `check.sh`: a
# citation of a file the anchor manifest does not carry passes every row here and
# fails there. That is worth stating rather than leaving to be discovered, because
# the failure mode is a green run that a contributor reads as "done" — the same
# shape as `check.sh --fast` printing `all checks passed` while a module's rows are
# collected by nothing, which is the thing `tests/test_collected_tests.py` exists to
# make visible. The two entry points prove different things; this one's proof is
# the narrower.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$HERE/env.sh"

# Not for the interpreter's sake — the venv supplies Django — but because the
# pinned-file rows read `.django/` directly, and a missing checkout turns forty
# rows into forty failures instead of one sentence.
CHECKOUT="$PROJECT_ROOT/.django"
if [ ! -d "$CHECKOUT" ]; then
  echo "no Django checkout at $CHECKOUT — run scripts/fetch_django.sh" >&2
  exit 1
fi

# `discover` with the same arguments `check.sh` uses, so the pinned collected count
# (`tests/test_collected_tests.py`) is a statement about this command rather than
# about a copy of it.
if [ "$#" -eq 0 ]; then
  set -- discover -s "$PROJECT_ROOT/tests" -t "$PROJECT_ROOT" -p 'test_*.py'
fi

cd "$PROJECT_ROOT"
exec "$PROJECT_ROOT/.venv/bin/python" -m unittest "$@"
