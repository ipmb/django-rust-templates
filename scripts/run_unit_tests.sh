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

if [ "${DJANGO_RUST_TEMPLATES:-1}" = "1" ]; then
  "$PROJECT_ROOT/.venv/bin/python" - <<'PY'
import sys

import django.template.exceptions

from django_rust_templates import _shim

if not _shim.installed():
    sys.exit("shim is not installed; refusing to run the suite")

module_file = django.template.exceptions.__file__ or ""
if "_impl" not in module_file:
    sys.exit(
        f"django.template.exceptions resolved to {module_file}, "
        "not to our implementation; refusing to run the suite"
    )

print(
    f"engine installed; {len(_shim.unshimmed())} engine module(s) still served by "
    f"Django ({len(_shim.NOT_THE_ENGINE)} more of django.template are declared "
    "not the engine)",
    file=sys.stderr,
)
PY
fi

# `discover` with the same arguments `check.sh` used before this script existed,
# so the pinned collected count (`tests/test_collected_tests.py`) is a statement
# about this command rather than about a copy of it.
if [ "$#" -eq 0 ]; then
  set -- discover -s "$PROJECT_ROOT/tests" -t "$PROJECT_ROOT" -p 'test_*.py'
fi

cd "$PROJECT_ROOT"
exec "$PROJECT_ROOT/.venv/bin/python" -m unittest "$@"
