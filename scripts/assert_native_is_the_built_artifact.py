#!/usr/bin/env python3
"""The build's *claim*, checked rather than assumed.

`tests/test_suite_isolation.py::GateBuildTests` pins the `maturin develop
--release` invocation and its position in `scripts/check.sh`, and that row is
**entirely textual**: it reads the script and cannot fail if the build succeeds
and installs somewhere the tests do not read. That is the residual form of the
bug the build step closes — "we ran a build" is not "the artifact under test is
the one we built" — and this is the behavioural half, one line after the build.

The comparison is right for two facts about this tree, both measured:

* the editable install means `django_rust_templates._native.__file__` is **the
  source tree** — `python/django_rust_templates/_native.<abi>.so` — and not
  `site-packages`. Someone chasing a stale extension who looks in `site-packages`
  finds nothing and concludes there is none; naming the real path is the point.
* `maturin develop` copies `target/release/lib_native.<ext>` to that path, so a
  digest over both is what "the artifact the suite imports is the one just built"
  means. The glob skips cargo's `.d` dep files.

What this does **not** answer is "is the extension current with the source" —
cargo's incremental fingerprint answers that, and `maturin develop` is how the
gate reads it (its ~1.2 s when current *is* that assertion's price). An edit to
the source changes neither the installed `.so` nor the `target/` one, so a
comparison *between* them matches either way; this script asserts only that the
gate read the right file.

The negative branch is a function rather than a branch inside `main()` so that
`tests/test_native_is_the_built_artifact.py` can drive it — an instrument that has never been
*seen* to fail is the defect it exists to catch, one level up.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RELEASE_DIR = PROJECT_ROOT / "target" / "release"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def release_artifacts(release_dir: Path = RELEASE_DIR) -> list[Path]:
    """What cargo produced under the release profile, minus its `.d` dep files."""
    return sorted(p for p in release_dir.glob("lib_native.*") if p.suffix != ".d")


def matching(installed: Path, built: Iterable[Path]) -> Path | None:
    """The first built artifact byte-identical to `installed`, or `None`.

    A digest rather than a size: two builds of different source can share a
    length, so a size match guesses and this line exists to be exact about the
    file that was measured.
    """
    want = digest(installed)
    return next((c for c in built if digest(c) == want), None)


def main() -> int:
    import django_rust_templates._native as native

    installed = Path(native.__file__)
    if not installed.is_file():
        print(
            f"the imported extension does not exist on disk: {installed}",
            file=sys.stderr,
        )
        return 1

    built = release_artifacts()
    if not built:
        print(
            f"no release artifact under {RELEASE_DIR} — the build step did not run, "
            "or wrote somewhere else",
            file=sys.stderr,
        )
        return 1

    hit = matching(installed, built)
    if hit is not None:
        print(f"the extension under test is the one just built: {installed.name}")
        print(f"  sha256:{digest(installed)[:16]}  ==  {hit.relative_to(PROJECT_ROOT)}")
        return 0

    print(
        "the extension the tests import is NOT the artifact the gate built:",
        file=sys.stderr,
    )
    print(f"  imported: {installed}  sha256:{digest(installed)[:16]}", file=sys.stderr)
    for candidate in built:
        print(
            f"  built:    {candidate.relative_to(PROJECT_ROOT)}  "
            f"sha256:{digest(candidate)[:16]}",
            file=sys.stderr,
        )
    print(
        "the suite below would be a statement about an older or foreign binary",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
