#!/usr/bin/env python3
"""Refuse unless `.django/` is the checkout `django.lock` pins and is unmodified.

§2:33-34: "zero edited tests. Django's checkout is never modified; the suite is
run unmodified." Read against the tree in `.review/m15-recon.md` §3.2: nothing
asserted it. This is the clause where a deliberate, well-intentioned edit — the
usual way one "fixes" a failing Django test — produces a fully green, fully
meaningless gate, which is why it is checked before the suite rather than after.

Both halves are checked because either alone is satisfiable while the other is
false: a clean tree at the wrong tag is a different Django, and the right tag
with a patched file is not stock Django.

This runs for every invocation of `run_django_suite.sh`, shimmed or not: the
clause is about the suite, not about our engine.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
LOCK = ROOT / "django.lock"


def lock_values(text: str) -> dict[str, str]:
    """`django.lock`'s `key=value` lines. Pure, so a test can hand it a fake."""
    values = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def dirt(porcelain: str) -> list[str]:
    """`git status --porcelain`'s lines, verbatim.

    Deliberately *not* `-uno`: an untracked file planted in the checkout changes
    what the suite does just as surely as an edited line, and `??` is how it
    shows. A trailing newline is not an entry.
    """
    return [line for line in porcelain.splitlines() if line.strip()]


def refusals(lock: dict[str, str], head: str, porcelain: str) -> list[str]:
    """Every reason this checkout is not the pinned, unmodified one."""
    out = []
    if not lock:
        out.append("django.lock is empty or missing its k=v lines")
    if lock.get("sha") != head:
        out.append(
            f"the checkout is at {head} and django.lock records {lock.get('sha')!r}"
        )
    for line in dirt(porcelain):
        out.append(f"the checkout is modified: {line}")
    return out


def git_dir_problem(git_dir: Path) -> str | None:
    """The one check that runs before `git` can be trusted to answer.

    A checkout copied from a tarball, or a `fetch_django.sh` replaced by a manual
    clone-and-copy, has no `.git` — and then `git status` exits non-zero and says
    nothing about §2. Naming it here keeps the refusal readable instead of
    surfacing as an exception from a script whose job is to be boring.
    """
    if not git_dir.exists():
        return (
            f"there is no .git at {git_dir}; the checkout cannot be checked, and "
            "§2:33-34 is the clause a green run cannot see"
        )
    return None


def inspect_checkout(checkout: Path = CHECKOUT, git=subprocess.run) -> list[str]:
    """`refusals` against a live checkout. Both arguments are for the controls."""
    problem = git_dir_problem(checkout / ".git")
    if problem:
        return [problem]

    def run(*args: str) -> str:
        return git(
            ["git", "-C", str(checkout), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    try:
        head = run("rev-parse", "HEAD").strip()
        porcelain = run("status", "--porcelain")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return [f"git could not read the checkout: {exc}"]
    lock = lock_values(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}
    return refusals(lock, head, porcelain)


def main() -> int:
    problems = inspect_checkout()
    if problems:
        for line in problems:
            sys.stderr.write(line + "\n")
        sys.stderr.write(
            "refusing to run the acceptance suite: §2:33-34 pins the checkout, "
            "and it is the one thing a green run cannot tell you about.\n"
        )
        return 1
    sys.stderr.write("checkout pinned and unmodified\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
