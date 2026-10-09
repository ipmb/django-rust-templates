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

cd "$CHECKOUT/tests"
exec "$PROJECT_ROOT/.venv/bin/python" runtests.py "${LABELS[@]}"
