#!/usr/bin/env bash
# `cargo test` for the crate whose tests need a Python interpreter of their own.
#
# The invocation lives here rather than in `check.sh` for the reason
# `run_unit_tests.sh` does: two places spell it and one of them is wrong.
#
# The two things this script adds are the two halves of one fact — that a test
# binary embeds the interpreter the extension module borrows:
#
#   * `--no-default-features` drops `extension-module`, which is the feature that
#     makes this crate a loadable `.so`. It is also the one that tells PyO3 not
#     to link libpython (right for a `cdylib`, fatal for a test binary), and it is
#     the cfg the interpreter-using tests are gated on — so without this flag
#     they are not compiled at all and the run reports zero tests as a pass.
#   * `DYLD_FALLBACK_LIBRARY_PATH` is the run-time half of that link. PyO3 emits
#     an rpath for some configurations and not this one: measured, the test
#     binary records `@rpath/libpython3.14.dylib` and resolves it against
#     `target/debug/deps`, so dyld aborts the process before the first test runs.
#     The directory comes from the interpreter's own `sysconfig` — the same
#     interpreter the venv runs the rest of the gate against — so a Python
#     upgrade moves it instead of breaking it.
#
# `RUST_MIN_STACK` is the third thing, and it is a measurement rather than a
# precaution. The walk's own test descends `MAX_WALK_DEPTH` (500) nested loops,
# which is 500 `Walk::run`/`Walk::run_for` pairs of real stack, and libtest runs
# each test on a thread whose stack a bare `cargo test` leaves at the default 2
# MiB. Probed by bisection: 500 levels need **more than 2 MiB and at most 4 MiB**
# in a debug build, and **more than 256 KiB and at most 512 KiB** in a release
# one — so ~4–8 KiB per nested level unoptimised, ~0.5–1 KiB optimised. Under 2
# MiB the test does not *fail*, it **stalls** at the deepest frame with the
# process alive and the sample showing it parked in `Walk::run_for`; that is worse
# than a crash, because a stall looks like a slow build. 8 MiB is the measured
# requirement rounded up, and the number is a property of the guard and not of
# the test: it is the same stack the shipped walk would need for a plan that deep.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$HERE/env.sh"

PY_LIBDIR="$("$VIRTUAL_ENV/bin/python" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
export DYLD_FALLBACK_LIBRARY_PATH="${PY_LIBDIR}${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
export LD_LIBRARY_PATH="${PY_LIBDIR}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# The fourth thing, and the reason is the same as the first: `Python::initialize`
# starts a *fresh* interpreter, which knows nothing of this venv, so `django`'s own
# imports fail at `asgiref` before any test runs. The path comes from the venv's
# own `site` module rather than a spelled-out `python3.14`, so a Python upgrade
# moves it.
PY_SITE="$("$VIRTUAL_ENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')"
export PYTHONPATH="${PY_SITE}${PYTHONPATH:+:$PYTHONPATH}"
export RUST_MIN_STACK=8388608

# **One test thread, and it is a correctness fix rather than a preference.** These
# tests each start from a bare interpreter and import `django_rust_templates`,
# whose chain reaches Django's own circular import —
# `django/template/__init__.py` imports `autoreload`, which imports
# `backends.django`, which imports `django.template` again. Single-threaded that
# cycle is ordinary; from two test threads at once CPython's per-module locks see
# each thread holding what the other wants and raise
# `_frozen_importlib._DeadlockError`. Observed on CI, in `materialise::malformed`,
# and green on the very next run of the same commit — a flake, not a failure, and
# one that reports as a red build on a commit that is fine.
#
# A mutex around the import would be the wrong fix: these threads hold the GIL,
# importlib releases it mid-import, and a thread blocking on a Rust lock while
# holding the GIL is a deadlock of its own. Serialising the tests removes the
# second thread instead. The crate is 12 tests in about half a second, so there is
# nothing to buy back.
exec cargo test -p template-py --no-default-features --quiet "$@" -- --test-threads=1
