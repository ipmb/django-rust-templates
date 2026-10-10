#!/usr/bin/env python
"""Named sides of the tree: build them, and run the differential corpus on one.

`frame_rewrite_ab.py build` builds exactly two sides — `prefix` and `fixed` — from
two source files, because the cost question is a pair. The provenance question is
not a pair: it asks what *each* tree in a series renders, so it needs N sides, and
it needs the same sides later for the cost A/B. This does both, with the one piece
of mechanics that is easy to get wrong spelled out:

  * the tree's `lib.rs` is swapped **and put back** — from a saved copy of the
    working file and not from `git`, because the working tree is the change under
    measurement and a `git checkout` would revert it;
  * every build is followed by an assertion on the **live artifact's** sha256,
    resolved the way the package resolves it (`django_rust_templates._native`)
    and not by `find _native*.so`, which a stale orphan beside the editable
    install answers instead;
  * `PATH` is set to the toolchain explicitly: `maturin` shells out to `cargo`,
    and the shell this runs in does not always carry it;
  * `PIP_CONFIG_FILE=/dev/null` for the install step, because the machine's global
    pip config carries `uploaded-prior-to = P3D` and every `pip install` fails on
    it. Worked around here rather than fixed: it is the user's config.

Usage:

    .venv/bin/python -I sides.py build --work W \\
        --side new=crates/template-py/src/lib.rs --side firstfix=/tmp/firstfix.rs
    .venv/bin/python -I sides.py diff --work W --label firstfix --out transcript.txt
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path("/Users/pete/projects/lincolnloop/django-rust-templates")
LIB_RS = ROOT / "crates/template-py/src/lib.rs"
SO = ROOT / "python/django_rust_templates/_native.cpython-314-darwin.so"
PY = ROOT / ".venv/bin/python"
TOOLCHAIN = pathlib.Path("/Users/pete/.rustup/toolchains/stable-aarch64-apple-darwin/bin")


def sha16(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def live_sha() -> str:
    out = subprocess.run(
        [
            str(PY), "-I", "-c",
            "import django_rust_templates._native as n, hashlib, pathlib;"
            "print(hashlib.sha256(pathlib.Path(n.__file__).resolve().read_bytes())"
            ".hexdigest()[:16])",
        ],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def build() -> None:
    """One `maturin develop --release` from the repo root, with the artifact asserted."""
    env = dict(os.environ)
    env["PATH"] = f"{TOOLCHAIN}:{env['PATH']}"
    env["PIP_CONFIG_FILE"] = "/dev/null"
    subprocess.run(
        [str(ROOT / ".venv/bin/maturin"), "develop", "--release"],
        cwd=ROOT, env=env, check=True, capture_output=True, text=True,
    )


def cmd_build(args) -> int:
    work = pathlib.Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    saved = LIB_RS.read_text()
    try:
        for spec in args.side:
            label, _, src = spec.partition("=")
            LIB_RS.write_text(pathlib.Path(src).read_text())
            build()
            shutil.copy(SO, work / f"{label}.so")
            print(f"{label:12} source {src} -> artifact sha256:{sha16(work / f'{label}.so')}")
    finally:
        LIB_RS.write_text(saved)
    build()
    got = live_sha()
    print(f"{'tree':12} restored -> artifact sha256:{got}")
    return 0


def cmd_diff(args) -> int:
    work = pathlib.Path(args.work)
    side = work / f"{args.label}.so"
    expect = sha16(side)
    saved = SO.read_bytes()
    try:
        shutil.copy(side, SO)
        got = live_sha()
        if got != expect:
            print(f"VOID: live artifact {got} != {expect}", file=sys.stderr)
            return 1
        proc = subprocess.run(
            [str(PY), str(ROOT / "scripts/differential.py")],
            cwd=ROOT, capture_output=True, text=True,
        )
        body = proc.stdout + proc.stderr
        header = f"=== differential.py on artifact sha256:{expect} (side {args.label})"
        print(header)
        print(body.rstrip())
        print(f"(exit status {proc.returncode})")
        if args.out:
            with open(args.out, "a") as fh:
                fh.write(f"{header}\n{body.rstrip()}\n(exit status {proc.returncode})\n\n")
    finally:
        SO.write_bytes(saved)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--work", required=True)
    p.add_argument("--side", action="append", required=True, help="label=path/to/lib.rs")
    p = sub.add_parser("diff")
    p.add_argument("--work", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--out", default=None)
    args = parser.parse_args()
    return {"build": cmd_build, "diff": cmd_diff}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
