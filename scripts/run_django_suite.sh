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

# §2:33-34, checked before the suite rather than after it: a transcript cannot
# tell you the checkout it came from was stock.
"$PROJECT_ROOT/.venv/bin/python" "$HERE/assert_checkout_pinned.py"

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

# Pre-flight: prove the engine is installed and complete before spending a suite
# run on it. This runs the same interpreter with the same PYTHONPATH, in a
# subprocess. Without it, a mistyped path or an unsynced venv yields a green suite
# that proves nothing about our code — and since M14 it also refuses when the meter
# is non-empty in either scope, because "full suite green with no fallback" is the
# acceptance criterion and a printed number is not a refusal. See
# `scripts/assert_engine_installed.py`; its correctness is dated to the engine
# being complete.
if [ "${DJANGO_RUST_TEMPLATES:-1}" = "1" ]; then
  echo "verifying the Rust engine is installed..." >&2
  "$PROJECT_ROOT/.venv/bin/python" "$HERE/assert_engine_installed.py"
fi

cd "$CHECKOUT/tests"

# The acceptance invocation is `template_tests` alone (`:25`), and it is the one
# whose transcript §2 pins. It is captured rather than streamed because the six
# skips are only named at verbosity 2, and printed back on success as the
# one-line summary a reader wants. Every other label set streams as before.
if [ "${LABELS[*]}" = "template_tests" ]; then
  TRANSCRIPT="$(mktemp "${TMPDIR:-/tmp}/django-suite.XXXXXX")"
  trap 'rm -f "$TRANSCRIPT"' EXIT
  set +e
  "$PROJECT_ROOT/.venv/bin/python" runtests.py -v 2 "${LABELS[@]}" \
    >"$TRANSCRIPT" 2>&1
  STATUS=$?
  set -e
  if [ "$STATUS" -ne 0 ]; then
    cat "$TRANSCRIPT"
    exit "$STATUS"
  fi
  "$PROJECT_ROOT/.venv/bin/python" "$HERE/assert_suite_outcome.py" "$TRANSCRIPT"
  # Needed on success as well as failure: `set -e` propagates a refusal, and
  # without this the script would fall through to the `exec` below and run the
  # whole suite a second time.
  exit $?
fi

exec "$PROJECT_ROOT/.venv/bin/python" runtests.py "${LABELS[@]}"
