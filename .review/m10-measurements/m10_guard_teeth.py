"""Does the undeclared-file clause actually see a file in a subpackage?

The clause is "every file carrying a marker must be declared, and vice versa".
Before M10's Task 6 it read

    marked = sorted(path.name for path in IMPL.glob("*.py") if _marked(path))

and it has **two independent defects**, both latent only while `_impl` is flat:

* ``glob("*.py")`` does not descend, so `_impl/loaders/base.py` — the first file
  in a subpackage, which M10's Task 7 creates — is never opened, never diffed,
  and never has its regions checked, with every other clause green;
* ``path.name`` keys a file by its basename, so that same file keys as
  ``base.py``, which **is** a transcription — a different one. `DECLARED` answers,
  the regions returned are `base.py`'s, and the clauses that exist to catch a
  region on the compared path go quiet while checking the wrong file.

**Both halves are measured here, separately**, because a fix that took only one
of them would look like a fix: `rglob` alone still mis-keys, and basename-keying
alone still cannot see the file. The middle column below is that half-fix.

Three guard variants, and only the first is written down twice:

* ``before`` — restored by ``git show`` from :data:`BEFORE_REV`, not retyped. A
  retyped "before" is a claim about what the old code said, and the one thing
  this instrument is for is not taking that on trust.
* ``rglob-only`` — the post-fix source with the key mutated back to
  ``path.name``, one token, derived mechanically rather than hand-written. It
  exists to make the second defect visible *at all*: under the real `before`
  clause the file is invisible for the first reason, so the keying bug cannot be
  observed there and would otherwise be fixed blind.
* ``after`` — the working tree.

Each variant runs against a scratch copy of the real `_impl`, with the real
`DECLARED` and `COMPARED_PATH`, so the only differences between cells are the
guard revision and the one planted file.

Run it:

    .venv/bin/python -I .review/m10-measurements/m10_guard_teeth.py
"""

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
GUARD_SOURCE = ROOT / "scripts" / "check_transcriptions.py"
REAL_IMPL = ROOT / "python" / "django_rust_templates" / "_impl"

# The guard as it was at the commit Task 6 fixed — the last revision whose
# `marked` clause has both defects. A SHA rather than `HEAD~1`, because this
# script is re-run long after the commit it measures and `HEAD~1` would then be
# the fixed guard, quietly turning the "before" column into a second "after".
BEFORE_REV = "7f73841"

# A name Django does not have, so the planted file is nobody's shadow and
# `_shim.shadowing_files()` is unaffected — this is about the guard's own clause.
PROBE = "loaders/probe.py"
# The other half of the demonstration: a *real* transcription's contents at a
# subpackage path, which is the shape M10 Task 7 creates for real.
MISKEYED = "loaders/base.py"

results = []


def load_guard(label: str, source: str):
    """Exec ``source`` as a module whose location is the real guard's.

    ``__file__`` is set rather than left to the caller, because the guard derives
    both `ROOT` and `CHECKOUT` from it: a copy dropped in a temp directory would
    look for `.django` beside itself and fail for a reason that has nothing to do
    with the clause under test. Setting it also means `IMPL` can be repointed
    without touching anything else.
    """
    module = types.ModuleType(f"guard_{label}")
    module.__file__ = str(GUARD_SOURCE)
    exec(compile(source, str(GUARD_SOURCE), "exec"), module.__dict__)
    return module


def source_of(revision: str) -> str:
    shown = subprocess.run(
        ["git", "show", f"{revision}:scripts/check_transcriptions.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if shown.returncode != 0:
        sys.exit(f"cannot read the guard at {revision}: {shown.stderr.strip()}")
    return shown.stdout


def cell(label: str, guard, plant: str | None):
    """Run ``guard.check()`` over a scratch ``_impl``, optionally with a plant."""
    with tempfile.TemporaryDirectory() as tmp:
        impl = pathlib.Path(tmp) / "_impl"
        shutil.copytree(REAL_IMPL, impl)
        if plant is not None:
            planted = impl / plant
            planted.parent.mkdir(parents=True, exist_ok=True)
            # A real transcription's bytes under a subpackage path, or a marker
            # with a body, so the planted file is a transcription as far as every
            # clause here is concerned.
            source = (REAL_IMPL / "base.py").read_text()
            if plant == PROBE:
                source = (
                    "# TRANSCRIBED: django/template/loaders/probe.py "
                    "(Django 6.0.9)\n\nPROBE = 1\n"
                )
            planted.write_text(source)

        saved = guard.IMPL
        guard.IMPL = impl
        try:
            failures = guard.check()
        finally:
            guard.IMPL = saved

    named = [f for f in failures if plant is not None and plant in f]
    print(f"  {label}")
    if not failures:
        print("      no failures")
    for failure in failures:
        print(f"      {failure}")
    results.append((label, plant, failures, named))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--before", default=BEFORE_REV,
                        help="the revision whose guard has both defects")
    args = parser.parse_args()

    before = source_of(args.before)
    after = GUARD_SOURCE.read_text()
    # The half-fix: `rglob`, still keyed by basename. One token, mutated rather
    # than retyped, so it is the post-fix clause with exactly one defect left.
    half = after.replace(
        "path.relative_to(IMPL).as_posix() for path in IMPL.rglob", "path.name for path in IMPL.rglob"
    )
    if half == after:
        sys.exit("the keying mutation did not apply — the clause has moved")
    if half in before:
        sys.exit("the half-fix is already the 'before' guard — BEFORE_REV is wrong")

    for label, source in (
        (f"before ({args.before})", before),
        ("rglob-only (basename keys)", half),
        ("after (worktree)", after),
    ):
        print(f"=== {label} ===")
        guard = load_guard(label, source)
        cell("clean scratch tree", guard, None)
        cell(f"planted {PROBE}", guard, PROBE)
        cell(f"planted {MISKEYED} (a real transcription's bytes)", guard, MISKEYED)
        print()

    print("=== summary ===")
    print("A cell is a FAILURE if the guard stayed silent about the planted file,")
    print("or if it named it while checking the wrong declaration.")
    for label, plant, failures, named in results:
        if plant is None:
            verdict = "clean" if not failures else "DIRTY (baseline is not clean)"
        else:
            verdict = "named" if named else "SILENT"
        print(f"  {verdict:32s} {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
