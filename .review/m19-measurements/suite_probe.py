#!/usr/bin/env python
"""Run the vendored acceptance suite under a given artifact.

Same swap discipline as `row_probe.py`: copy the side's `.so` in, assert the live
artifact's sha256 as the package resolves it, run `django_suite/run.py -v 2
template_tests`, restore the shipped artifact from a saved copy.

Usage: .venv/bin/python -I suite_probe.py --work W --side mutant --side mutant_push
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
            proc = subprocess.run(
                [str(PY), "-I", str(ROOT / "django_suite/run.py"), "-v", "2", "template_tests"],
                cwd=ROOT, capture_output=True, text=True,
            )
            body = proc.stdout + proc.stderr
            tail = [ln for ln in body.splitlines() if ln.startswith("Ran ") or ln.strip() == "OK"
                    or ln.startswith("FAILED") or "skipped=" in ln]
            fails = [ln for ln in body.splitlines() if ln.startswith("FAIL:") or ln.startswith("ERROR:")]
            lines.append(f"=== artifact sha256:{expect} (side {label}) exit {proc.returncode}")
            for ln in tail:
                lines.append(f"  {ln.strip()}")
            for ln in fails[:8]:
                lines.append(f"  {ln.strip()}")
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
