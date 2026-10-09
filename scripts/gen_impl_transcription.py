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

Not every transcribed module opens with a docstring, and M10's nine are where
that stopped being hypothetical: ``context.py``, ``engine.py``, ``utils.py`` and
``loaders/base.py`` open with imports, and ``loaders/__init__.py`` is 0 bytes
upstream. For those the marker is a comment on line 1 followed by a blank line —
:data:`render` picks the form from the source, and ``check_transcriptions.py``'s
``MARKER_WINDOW`` accepts both. **Either way the insertion is exactly two
lines**, which is what the declared regions and every measured ``COMPARED_PATH``
span for these files are written against.

Keys are file names (``"base.py"``), not module stems, because that is the
spelling ``DECLARED``, ``COMPARED_PATH`` and ``_shim.SHADOWING`` all use: four
tables, one spelling of a file's identity.

Usage:
    python -I scripts/gen_impl_transcription.py base.py smartif.py
    python -I scripts/gen_impl_transcription.py --check
"""

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
IMPL = ROOT / "python" / "django_rust_templates" / "_impl"

MODULES = {
    "context.py": "django/template/context.py",
    "engine.py": "django/template/engine.py",
    "smartif.py": "django/template/smartif.py",
    "utils.py": "django/template/utils.py",
    # M10's six loader files. The first keys in this table that are not a single
    # segment, which is the point: a key is an `_impl`-relative posix path here as
    # it is in `DECLARED`, `COMPARED_PATH` and `_shim.SHADOWING`, and the value is
    # `django/template/` + the key for every row in the table — including these,
    # where the key carries the subdirectory the value has to carry too.
    "loaders/__init__.py": "django/template/loaders/__init__.py",
    "loaders/base.py": "django/template/loaders/base.py",
    "loaders/filesystem.py": "django/template/loaders/filesystem.py",
    "loaders/app_directories.py": "django/template/loaders/app_directories.py",
    "loaders/cached.py": "django/template/loaders/cached.py",
    "loaders/locmem.py": "django/template/loaders/locmem.py",
    # M11's first, and the only one this table names yet. Unlike M10's loader
    # files it is a module the tag and filter corpus renders through, so a
    # verbatim transcription here moves the *registry's* owner and the node
    # classes' owner; the routing that puts the filter bodies on Rust's path is
    # `base.py`'s and is hand-written.
    #
    # `defaultfilters.py` and `defaulttags.py` are M11's other two and are
    # deliberately **not** listed: this table is also what `--check` walks, and a
    # name here for a file `_impl` does not have fails the generator step in
    # every commit that runs before the task which creates it. Each is added by
    # its own task, in the same commit as the file it generates — measured, in
    # the commit that interposed `loader_tags` alone.
    "loader_tags.py": "django/template/loader_tags.py",
}

# Transcriptions this generator must **not** write, because their difference from
# the pin is more than the marker: `base.py` delegates `Template.compile_nodelist`
# to Rust, in a declared region. Regenerating it would silently replace that
# delegation with upstream's text — a change `check_transcriptions.py` permits
# only because it is declared, and one this tool cannot express.
#
# That is the whole partition, and it is why this list exists rather than a
# per-file flag: **this tool produces "the pin plus a marker", and a file whose
# difference is more than that is the guard's, not this one's.** Keeping
# `base.py` in `MODULES` is what made this script stale for two milestones
# without anyone noticing — `--check` had been failing on `base.py` since M9
# wrote the delegation by hand, and nothing invoked it. M10 measured it and puts
# it on the fast path, which is why the failure could no longer be ignored.
HAND_WRITTEN = {
    "base.py": "django/template/base.py",
}

# The pinned version, so a transcription says which upstream it transcribes and
# a bump has one place to change rather than one per file.
DJANGO_VERSION = "6.0.9"


def render(name: str) -> str:
    """The exact contents ``_impl/<name>`` must have."""
    if name in HAND_WRITTEN:
        raise SystemExit(
            f"{name} differs from the pin by more than the marker — its declared "
            "region carries code upstream does not have, so this generator would "
            "overwrite it. Edit it by hand and let check_transcriptions.py check it."
        )
    if name not in MODULES:
        raise SystemExit(f"{name}: not a transcription; known: {sorted(MODULES)}")
    source = (CHECKOUT / MODULES[name]).read_text()
    marker = f"# TRANSCRIBED: {MODULES[name]} (Django {DJANGO_VERSION})\n"
    if source.startswith('"""\n'):
        return '"""\n' + marker + "\n" + source[4:]
    # No docstring to sit in, so the marker is a comment on line 1 with a blank
    # line after it — the same shape the docstring form has, two lines either
    # way, which is the property the span arithmetic depends on. This branch
    # used to be a SystemExit reading "does not open with a bare docstring; the
    # marker's insertion point is text-anchored and needs fixing by hand", which
    # was right while `base.py` was the only transcription and M10 made wrong:
    # four of its nine files open with imports and one is empty.
    return marker + "\n" + source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="report instead of writing; exit 1 on any mismatch")
    parser.add_argument("module", nargs="*", default=sorted(MODULES))
    args = parser.parse_args()

    failures = 0
    for name in args.module:
        want = render(name)
        path = IMPL / name
        if args.check:
            got = path.read_text() if path.is_file() else ""
            if got != want:
                failures += 1
                print(f"{name} is not what the generator produces "
                      f"(run: scripts/gen_impl_transcription.py {name})")
            else:
                print(f"{name} matches the generator")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(want)
            print(f"wrote {path.relative_to(ROOT)} ({len(want.splitlines())} lines)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
