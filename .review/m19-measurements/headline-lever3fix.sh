#!/usr/bin/env bash
# The headline ABBA for slice 5.5 lever 3, RE-RUN against the repaired build.
#
# Same driver as headline3.sh, over a different pair:
#   prefix = 3d3e99e63eddc036  (lever 1 landed -- the "before" side, unchanged)
#   fixed  = the repaired lever-3 build      (caf1589798eac0cf at dispatch time)
#
# Why the re-run: the adversarial round's repair changed the fast path (an exact
# `list` check, a `PyDict` pre-pass, `break` instead of a bail on a truncated
# stack), so every figure keyed to the pre-repair artifact expired with it.
#
# Each swap places the artifact in BOTH places it is read from:
#   * `python/django_rust_templates/_native.<abi>.so` -- what the python suites
#     and `bench.py` actually import;
#   * `target/release/lib_native.dylib` -- what `bench.py`'s `native_artifact()`
#     hashes to name the *profile*. The dylib is cargo's output, so it is
#     restored at the end: leave the old one there and the next
#     `maturin develop` copies it back into the package, silently reverting the
#     tree with every gate green.
#
# A sha is asserted after every swap, in both places.
set -euo pipefail

cd /Users/pete/projects/lincolnloop/django-rust-templates
source scripts/env.sh

W=/Users/pete/projects/lincolnloop/django-rust-templates/.review/m19-measurements/root-read-fix
H=/Users/pete/.claude/jobs/627577b5/tmp/headline3fix
SO=python/django_rust_templates/_native.cpython-314-darwin.so
DYLIB=target/release/lib_native.dylib
mkdir -p "$H"

sha() { shasum -a 256 "$1" | cut -c1-16; }

place() {  # $1 = prefix | fixed
  cp "$W/$1.so" "$SO"
  cp "$W/$1.so" "$DYLIB"
  local exp got_s got_d
  exp=$(sha "$W/$1.so")
  got_s=$(sha "$SO")
  got_d=$(sha "$DYLIB")
  if [ "$got_s" != "$exp" ] || [ "$got_d" != "$exp" ]; then
    echo "VOID: placing $1 gave package=$got_s dylib=$got_d want=$exp" >&2
    exit 1
  fi
  echo "placed $1  package=$got_s  dylib=$got_d"
}

run() {  # $1 = side, $2 = label, $3 = extra args
  place "$1"
  # shellcheck disable=SC2086
  .venv/bin/python -I scripts/bench.py --json "$H/$2.json" ${3:-}
  echo "--- $2 done: $(sha "$SO")  $(date -u +%H:%M:%S) UTC"
}

run prefix a1
run fixed  b1 "--html docs/bench.html"
run fixed  b2
run prefix a2

cp "$W/fixed.so" "$SO"
cp "$W/fixed.so" "$DYLIB"
echo "restored fixed  package=$(sha "$SO")  dylib=$(sha "$DYLIB")"
