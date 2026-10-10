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

step "cargo test, the crate that embeds an interpreter"
# `template-py`'s own tests are the walk's, and they are not in the step above.
# `cargo test --workspace` builds this crate with its default features, which
# include `extension-module` — the feature that tells PyO3 *not* to link
# libpython, because an extension module borrows the interpreter that loads it.
# A test binary has to start its own, so the walk's tests are gated on the
# absence of that feature and this step is the only thing that compiles them.
# Without it they would be neither run nor missed: `cargo test --workspace`
# reports an empty test target as a pass.
#
# The invocation lives in `run_rust_unit_tests.sh` for the reason the Python
# one does — it is more than a command line (a `--no-default-features`, a dyld
# search path taken from the interpreter's own `sysconfig`, and the 8 MiB of
# test-thread stack the depth guard's boundary row needs, none of which any of
# them can be inferred from the others) — and it is the single place those are
# written down.
"$HERE/run_rust_unit_tests.sh"

step "rustfmt"
cargo fmt --all -- --check

step "clippy"
cargo clippy --workspace --all-targets --quiet -- -D warnings
# Again with `extension-module` off, because that is the configuration the step
# above does not lint: the walk's tests are `#[cfg(not(feature = ...))]`-gated,
# so `--workspace` alone lints neither the tests nor `cargo test`'s ability to
# compile them. Same `-D warnings` as above and not a relaxed one — a lint
# budget that changes with the feature set is a lint budget nobody can read.
cargo clippy -p template-py --no-default-features --all-targets --quiet -- -D warnings

step "the extension the python suites exercise"
# The Python steps below are statements about an *artifact*, and until this line
# existed nothing tied that artifact to the source. `cargo test` reads the source;
# the unit suite and the acceptance run import `_native` and exercise whatever it
# resolves to — the source tree's `python/django_rust_templates/_native.<abi>.so`,
# reached through the editable install rather than from `site-packages`, which is
# whatever was built last — and measured, this script ran no build at all. The
# hazard is **staleness**, not profile: both profiles render identical bytes, so a
# debug extension costs only speed, while one built from *older source* answers
# questions about the old source and passes rows the current tree does not satisfy
# — with `cargo test` green beside it, because that is the one step that reads the
# source.
#
# What this line buys is cargo's own source-match check, *run* instead of inferred:
# an incremental fingerprint is exactly "has the source moved since this artifact",
# and `maturin develop` is how the gate reads it — measured at 1.2 s when the
# extension is current and ~11 s when the source has moved. Nothing cheaper makes
# the same claim: a digest against `target/{debug,release}` answers *which* build it
# is and not *whether it is current*, and the comparison that would — a digest
# recorded at build time against the source now — is the fingerprint cargo already
# keeps and this step re-runs.
#
# One line below turns that from an assumption into a check: `assert_native_is_the_built_artifact.py`
# resolves `_native` the way a test does and requires it to be the artifact just
# built, so "*we ran a build*" and "*the artifact under test is the one we built*"
# cannot come apart silently.
#
# `--release` rather than the default, and that is a second, smaller claim: it is
# the profile `pip install` builds and the one `.review/m18-record.md`'s benchmark
# figures describe, so a bench run after a gate run measures the shipping binary
# instead of the unoptimised one `maturin develop` installs by default — which is
# the reading that was published wrong for a round and then corrected.
"$PROJECT_ROOT/.venv/bin/maturin" develop --release
"$PROJECT_ROOT/.venv/bin/python" -I "$HERE/assert_native_is_the_built_artifact.py"

step "python unit tests"
# The invocation lives in `run_unit_tests.sh` rather than here because the plan's
# per-task commands need the same arguments, and written twice one of them is
# wrong. That script also pins the *discovery* — `tests/test_collected_tests.py`
# asserts the count this command collects, so a future `-p` change or a module
# named without `test_` fails a row instead of silently running fewer tests.
#
# Until M18 this step was a *correctness* requirement rather than a convenience,
# and the paragraph that said why is now history in that script: the runner had to
# put a `sitecustomize` on `PYTHONPATH` so the substitution shim was installed
# before the first test module bound a class. There is no shim to install.
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
# an edit being written, which is what a gate is for. The unquoted rows are
# *not* failed on: an unquoted row records no claim to falsify, and the ones left
# are cited only from files that never held a shorthand, so no naming pass will
# come back for them. They stood at 104 when `--frozen` was earned and are **157**
# after M12 Tasks 3, 4 and 5: Task 3's 23 `cache.py`/`static.py` anchors seed
# unquoted by design, Task 4's eighteen `tz.py` anchors mostly do too, and Task 5
# added two more — `_seed_snippet` requires the quoted span to occur on exactly
# *one* line of the file, and the spans our three new modules quote beside their
# anchors (`tokens[-1]`, `split_contents`, `use_tz = True`, `LocalizeNode.render`)
# recur throughout theirs. Task 3's and Task 4's seeded rows were *wrong* rather
# than merely unquoted in a couple of places and were repaired by hand, and Task
# 5's were the same: the seed drafted `l10n.py:52-53` and `l10n.py:52-57` with
# snippets that live on 55 and 58, and both were re-quoted by hand. The number in this
# comment is a measurement and moves with the tree; the property that matters is
# that `--check` counts them out loud rather than passing them as verified.
"$PROJECT_ROOT/.venv/bin/python" -I "$HERE/check_python_anchors.py" --check --frozen

step "the vendored suite is generated, not edited"
# `django_suite/` is a *copy* of the pinned `template_tests`, rewritten by
# `scripts/gen_django_suite.py` so that every import of a module this package owns
# points at `django_rust_templates.engine.*`. The copy is what makes the
# acceptance run a statement about this engine; a hand edit inside it would make
# the run a statement about the edit, and a green one either way. So the same
# shape as the transcription pair below: one script says what the copy is allowed
# to differ by, this one says the rest of it is still what the generator writes.
#
# Fast path: it reads text — 215 files and the checkout — and compiles nothing.
"$PROJECT_ROOT/.venv/bin/python" -I "$HERE/gen_django_suite.py" --check

step "suite counts carry their provenance"
# Every milestone is accepted on a *count*, and a count is the easiest claim to
# make vacuously: measured, running `.django/tests/runtests.py` directly produces a
# result byte-identical to stock Django's and still reports OK. The guard requires
# every `Ran N tests` in our prose to sit near something naming the run that
# produced it. On the fast path because it reads text and nothing else.
"$PROJECT_ROOT/.venv/bin/python" -I "$HERE/check_suite_counts.py"

step "transcriptions differ from upstream only where declared"
# A `# TRANSCRIBED:` file is a claim about the pinned tree, and the suite cannot
# falsify it: measured, `engine/base.py` as a verbatim copy and `engine/base.py`
# transcribing wrongly both keep all 1547 tests green. So the diff is the
# instrument, and this is where the parity harness's own warrant comes from —
# once `base` is interposed, `test_parser_parity` compares Rust against *our*
# transcription, and "our transcription is upstream" is exactly this step.
# Fast path: it reads text and needs only `.django/`.
"$PROJECT_ROOT/.venv/bin/python" -I "$HERE/check_transcriptions.py"

step "the transcriptions are generated, not edited"
# The other half of the claim above, and the reason it sits here rather than
# with the two generators on the slow path. `check_transcriptions.py` says
# *which* regions of a transcription may differ from the pin; this says the rest
# of each file is still what the generator produces, so a hand edit inside a
# declared region — the one change the guard permits by construction — is
# caught. Same inputs as the guard (four `engine` files and `.django/`), so it
# costs a read and belongs on the fast path; `gen_python_case_fixups.py` below
# does not, because it compiles a Rust probe.
#
# Both scripts document `--check` as *the* way to verify currency and this one
# was invoked by nothing, which is the failure mode the pair exists to close: a
# drift guard nobody runs. Measured before wiring it up — `gen_impl_transcription.py
# --check` was *already* failing on `base.py` when M10 found it, because `base.py`
# delegates to Rust by hand and the generator can only write "the pin plus a
# marker". That is now `HAND_WRITTEN`, and it refuses by name instead.
"$PROJECT_ROOT/.venv/bin/python" -I "$HERE/gen_impl_transcription.py" --check

step "differential harness"
"$PROJECT_ROOT/.venv/bin/python" "$HERE/differential.py"

if [ "$FAST" -eq 0 ]; then
  # `--check` regenerates and diffs, so it says *what* differs, and it is
  # self-maintaining: a legitimate move (a new `rustc`, a new CPython, a Django
  # pin bump) is fixed by re-running the generator, not by editing a count here.
  # These generators document `--check` as *the* way to verify currency and
  # neither was invoked anywhere, which is the failure mode this closes: a drift
  # guard nobody runs. Not on the `--fast` path because the case generator
  # compiles a Rust probe; the transcription generator reads text and is on the
  # fast path above, with the guard whose claim it completes.
  step "generated files are current"
  "$PROJECT_ROOT/.venv/bin/python" -I "$HERE/gen_python_word_ranges.py" --check
  "$PROJECT_ROOT/.venv/bin/python" -I "$HERE/gen_python_case_fixups.py" --check

  # §2:31-34's acceptance run. Three commands, and each one closes a hole the
  # other two cannot see:
  #
  # * `assert_checkout_pinned.py` refuses unless `.django/` is the checkout
  #   `django.lock` pins and is unmodified — §2:33-34's "zero edited tests", the
  #   clause where a well-intentioned fix to a failing Django test produces a
  #   fully green and fully meaningless gate.
  # * `django_suite/run.py` runs the *vendored* copy, whose imports the generator
  #   rewrote to `django_rust_templates.engine`. The label is "vendored" and not
  #   "(rust engine)" for the reason M9 measured on the old label: the suite calls
  #   `_native` 23,263 times but reaches `lex` zero times (`base.Lexer` is Python),
  #   so "rust engine" would assert more than the run does.
  # * `assert_suite_outcome.py` reads the transcript, because the exit status
  #   cannot carry §2's whole clause: `Ran 1547 tests … OK (skipped=8)` passes the
  #   count and the status while two tests §2:64-65 requires have silently
  #   stopped running. It pins the summary **line whole** and the six skips by
  #   identity.
  #
  # The transcript is kept rather than dropped — it is the only place the skip
  # reasons and a failure's traceback appear — and the status is checked after the
  # gate has had its say, so a red run reports *both* the numbers and the exit.
  #
  # **`-v 2` is load-bearing and was measured the hard way.** At the default
  # verbosity the runner prints a dot per test and *no skip line at all*, so this
  # gate — whose whole subject is §2:31's "a count of six is satisfied by a different
  # six" — refused the healthy run with `the summary says skipped=6 and 0 skip
  # line(s) were attributed to a test`. Two ways to read that refusal and only one is
  # right: not "the six changed" but "the transcript cannot say". `-v 2` costs 1547
  # lines of transcript and buys the six identities.
  #
  # The transcript is therefore printed as a *digest* rather than whole: every skip
  # line, and the verdict with its teardown. A red run prints all of it, because a
  # failure's traceback sits above the verdict where `tail` would hide it.
  step "checkout pinned"
  "$PROJECT_ROOT/.venv/bin/python" -I "$HERE/assert_checkout_pinned.py"

  step "django template_tests (vendored)"
  TRANSCRIPT="$(mktemp -t django-rust-suite)"
  trap 'rm -f "$TRANSCRIPT"' EXIT
  STATUS=0
  "$PROJECT_ROOT/.venv/bin/python" -I "$PROJECT_ROOT/django_suite/run.py" -v 2 template_tests \
    >"$TRANSCRIPT" 2>&1 || STATUS=$?
  grep -E "skipped '" "$TRANSCRIPT" || true
  if [ "$STATUS" -ne 0 ]; then
    cat "$TRANSCRIPT"
  else
    tail -n 8 "$TRANSCRIPT"
  fi
  "$PROJECT_ROOT/.venv/bin/python" -I "$HERE/assert_suite_outcome.py" "$TRANSCRIPT"
  if [ "$STATUS" -ne 0 ]; then
    echo "the acceptance suite exited $STATUS" >&2
    exit "$STATUS"
  fi

  step "packaging"
  # A wheel, three virtualenvs and a `pip install` — the one check here that
  # leaves the checkout and the only one that can see the *distribution* rather
  # than the source tree. It belongs on the slow path because it builds and
  # installs, and it is on the gate at all rather than being a script somebody
  # remembers to run for the reason this file keeps rediscovering: a drift guard
  # nobody runs is not a guard. It needs the network once to warm a wheel cache
  # under `target/`, then runs offline; a cold cache with no network fails loudly
  # rather than passing quietly.
  "$HERE/check_packaging.sh"
fi

printf '\nall checks passed\n'
