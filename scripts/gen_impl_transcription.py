"""Regenerate an ``_impl`` transcription of a pinned ``django/template`` module.

Two relations are checkable here and this script owns the first: its output must
equal the committed file. The second — the committed file minus the pinned
source falls inside the declared regions — belongs to
``check_transcriptions.py``, and the two together are what make a 1207-line
transcription reviewable at all. The surface becomes "these regions differ",
which is a statement someone can check, where "we rewrote ``base.py``" is not.

``--check`` regenerates and diffs, like ``gen_python_word_ranges.py``, so a
legitimate move (a pin bump) is fixed by re-running the generator rather than by
editing a file by hand — and a hand edit to ``_impl/base.py`` fails here.

The marker goes immediately after the module docstring's opening ``\"\"\"``:
**text, not a line number.** Anchoring to a line would make our file a function
of Django's line numbering, so the next pin bump would change it for a reason
that is not ours — the drift class this project has spent two milestones
removing, reintroduced in the one place where it would look like a legitimate
regeneration.

Usage:
    python -I scripts/gen_impl_transcription.py base smartif
    python -I scripts/gen_impl_transcription.py --check
"""

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
IMPL = ROOT / "python" / "django_rust_templates" / "_impl"

MODULES = {"base": "django/template/base.py", "smartif": "django/template/smartif.py"}

# The pinned version, so a transcription says which upstream it transcribes and
# a bump has one place to change rather than one per file.
DJANGO_VERSION = "6.0.9"


def render(name: str) -> str:
    """The exact contents ``_impl/<name>.py`` must have."""
    if name not in MODULES:
        raise SystemExit(f"{name}: not a transcription; known: {sorted(MODULES)}")
    source = (CHECKOUT / MODULES[name]).read_text()
    marker = f"# TRANSCRIBED: {MODULES[name]} (Django {DJANGO_VERSION})\n"
    if not source.startswith('"""\n'):
        raise SystemExit(
            f"{MODULES[name]} does not open with a bare docstring; the marker's "
            "insertion point is text-anchored and needs fixing by hand"
        )
    return '"""\n' + marker + "\n" + source[4:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="report instead of writing; exit 1 on any mismatch")
    parser.add_argument("module", nargs="*", default=sorted(MODULES))
    args = parser.parse_args()

    failures = 0
    for name in args.module:
        want = render(name)
        path = IMPL / f"{name}.py"
        if args.check:
            got = path.read_text() if path.is_file() else ""
            if got != want:
                failures += 1
                print(f"{name}.py is not what the generator produces "
                      f"(run: scripts/gen_impl_transcription.py {name})")
            else:
                print(f"{name}.py matches the generator")
        else:
            path.write_text(want)
            print(f"wrote {path.relative_to(ROOT)} ({len(want.splitlines())} lines)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
