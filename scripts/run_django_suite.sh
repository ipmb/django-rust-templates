#!/usr/bin/env bash
# Run Django's own test suite against the Rust-backed template engine.
#
# Usage:
#   scripts/run_django_suite.sh                    # full template_tests
#   scripts/run_django_suite.sh template_tests.test_origin
#   scripts/run_django_suite.sh template_tests.filter_tests.test_add
#
# Set DJANGO_RUST_TEMPLATES=0 to run the same labels against stock Django, which
# is useful for confirming that a failure is ours and not upstream's.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$HERE/env.sh"

CHECKOUT="$PROJECT_ROOT/.django"
if [ ! -d "$CHECKOUT" ]; then
  echo "no Django checkout at $CHECKOUT — run scripts/fetch_django.sh" >&2
  exit 1
fi

LABELS=("$@")
if [ ${#LABELS[@]} -eq 0 ]; then
  LABELS=(template_tests)
fi

# Order matters: our sitecustomize must be found before the checkout's own
# top-level modules, and the checkout must be importable as `django`.
#
# The runner lives in tests/ but `import django` must resolve from the checkout
# root, so the working directory and PYTHONPATH differ.
export PYTHONPATH="$HERE/shimpath:$CHECKOUT:${PYTHONPATH:-}"

# Pre-flight: prove the shim is actually installed before spending a suite run on
# it. This runs the same interpreter with the same PYTHONPATH, in a subprocess,
# and asserts that a module we provide resolved to our file rather than Django's.
# Without it, a mistyped path or an unsynced venv yields a green suite that
# proves nothing about our code.
if [ "${DJANGO_RUST_TEMPLATES:-1}" = "1" ]; then
  echo "verifying the Rust engine is installed..." >&2
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

cd "$CHECKOUT/tests"
exec "$PROJECT_ROOT/.venv/bin/python" runtests.py "${LABELS[@]}"
