#!/usr/bin/env python
"""Which of the fix's two test rows fail on which tree.

The rows that pin a fix are themselves claims, and the claim "this row pins it"
is falsified by running the row on the *wrong* fix — a row that passes on the fix
and on every mutant catches nothing.  So each side's `.so` is copied into the
package, the live artifact's sha is asserted (resolved the way the package
resolves it, not by globbing), the two rows are run, and the shipped artifact is
put back from a saved copy — never from `git`, because the working tree is the
change under measurement.

Usage:
    .venv/bin/python -I row_probe.py --work W --side new --side mutant --side mutant_push [--out F]
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path("/Users/pete/projects/lincolnloop/django-rust-templates")
SO = ROOT / "python/django_rust_templates/_native.cpython-314-darwin.so"
PY = ROOT / ".venv/bin/python"

ROWS = (
    "tests.test_interposition.FilterRoutingTests"
    ".test_a_read_before_the_delegate_is_served_from_the_frame",
    "tests.test_interposition.FilterRoutingTests"
    ".test_a_loop_whose_sequence_can_write_the_context_still_seals",
)
SHORT = {
    ROWS[0]: "a_read_before_the_delegate_is_served_from_the_frame",
    ROWS[1]: "a_loop_whose_sequence_can_write_the_context_still_seals",
}


def sha16(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def live_sha() -> str:
    out = subprocess.run(
        [str(PY), "-I", "-c",
         "import django_rust_templates._native as n, hashlib, pathlib;"
         "print(hashlib.sha256(pathlib.Path(n.__file__).resolve().read_bytes())"
         ".hexdigest()[:16])"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--side", action="append", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    work = pathlib.Path(args.work)
    lines = []
    saved = SO.read_bytes()
    try:
        for label in args.side:
            side = work / f"{label}.so"
            expect = sha16(side)
            shutil.copy(side, SO)
            got = live_sha()
            if got != expect:
                print(f"VOID: live artifact {got} != {expect}", file=sys.stderr)
                return 1
            lines.append(f"=== artifact sha256:{expect} (side {label})")
            for row in ROWS:
                proc = subprocess.run(
                    [str(PY), "-m", "unittest", row, "-v"],
                    cwd=ROOT, capture_output=True, text=True,
                )
                status = "PASS" if proc.returncode == 0 else "FAIL"
                lines.append(f"  {status}  {SHORT[row]}")
                if proc.returncode != 0:
                    for ln in (proc.stdout + proc.stderr).splitlines():
                        if "AssertionError" in ln or "Error:" in ln:
                            lines.append(f"        {ln.strip()[:200]}")
            lines.append("")
    finally:
        SO.write_bytes(saved)
    lines.append(f"restored: live artifact sha256:{live_sha()}")
    text = "\n".join(lines)
    print(text)
    if args.out:
        with open(args.out, "a") as fh:
            fh.write(text + "\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
