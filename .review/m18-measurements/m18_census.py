#!/usr/bin/env python
"""How many node kinds does the parser actually emit? Three answers, not one.

§3.1 of the M18 design spec sizes route (b) from these numbers, and the first two
drafts of it got them wrong — not by miscounting but by *measuring a different
quantity than the sentence claimed*. So this script counts all three and prints
them together, because any one of them alone is the wrong answer to two questions.

    emission call sites     how many places construct a NodeDesc  -> per-site work
    distinct literals       how many class names those calls name  -> per-variant work
    NODE_CLASSES entries    how many the materialiser can build

**The method is the whole point.** Every `git grep` for these literals is
line-oriented, and at least one call site is not:

    crates/template-core/src/parser/tags/mod.rs:219   NodeDesc::with_nodelist(
    crates/template-core/src/parser/tags/mod.rs:220       "StandinBlockNode",

so a per-line search returns 43 distinct names where the true count is 44. This
scans whole file text, so `\\s*` crosses newlines.

Counting is done on the *committed* tree (`git show REV:path`), not the working
tree, so a concurrent edit cannot move the number. Pass a rev as argv[1].

    source scripts/env.sh
    .venv/bin/python .review/m18-measurements/m18_census.py [rev]

Two earlier versions of this file are worth not repeating:

  * classifying a site as test by "after the first `#[cfg(test)]` in the file" is
    wrong, because `tags/mod.rs` has `#[cfg(test)] use` items at module scope with
    production functions *after* them;
  * classifying by `#[cfg(test)] mod` only is wrong the other way, because a
    `#[cfg(test)] fn` is a stand-in producer and is not inside a mod.

The rule used here is the right one: brace-match from each `#[cfg(test)]` attribute
to the end of *its own item*, whatever kind of item that is.

**And the output is a list, not only a count.** This census was counted wrong four
times by four methods — line-oriented missed a multi-line call (43 names instead of
44); whole-text then swallowed a *comment* that writes the constructor name in prose
(`lib.rs:2660` in a later working tree, absent at `bb7e53d`); and the check for that
used `git grep` with `\\|`, which is not alternation there and fails as a silent
empty. Each fix introduced the next failure. What converged was not a fifth method
but printing the sites, because `path:line <name> | <the line>` lets a reader see a
comment, a stand-in, or a multi-line call in one glance where no count can.

So `--list` prints every site with its source line, and any site whose line is a
comment is marked `COMMENT` and excluded from the totals (with a note, never
silently). Run it before trusting any number this script prints.

**This script is also the watcher for its own canary.** M18.0's spike adds a comment
to `crates/template-py/src/lib.rs` that names both constructor forms in prose while
warning that a literal-grep renderer "would silently miss" `TextNode`. So the commit
after `bb7e53d` contains a deliberately-placed piece of prose that looks like code,
in a tree where the right answer is known: a scanner reporting 55 sites there is
comment-blind, and one reporting 53 has demonstrated the exclusion. That only stays a
discriminator if something is watching, so the totals below are asserted, and a
mismatch is reported with the sites that moved rather than as a bare number.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from typing import NamedTuple

ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]
LIST = "--list" in sys.argv
REV = ARGS[0] if ARGS else "HEAD"

# Recorded totals: rev -> (sites, names, NODE_CLASSES keys). A rev listed here is
# pinned to exactly these; the totals of the oldest entry are also the baseline that
# every later rev is expected to preserve, because the spike adds a comment and no
# producers. A mismatch is not proof of a bug — it may be a genuine new producer —
# but it is always the moment to look, and `--list` on both revs says why.
EXPECTED = {
    "bb7e53d": (53, 44, 40),
}
BASELINE = "bb7e53d"

SITE = re.compile(r'NodeDesc::(new|with_nodelist)\(\s*"([A-Za-z_]+)"')
CFG = re.compile(r"#\[cfg\(test\)\]")
# the item a `#[cfg(test)]` attribute introduces, whatever keyword it starts with
ITEM = re.compile(r"\b(mod|fn|impl|struct|enum|trait|const|static|use|type)\b")


def git(*args: str, ok_codes: tuple[int, ...] = (0,)) -> str:
    """Run git, tolerating the listed exit codes.

    `git grep` exits 1 when it matches nothing, which is a legitimate answer here —
    a revision predating `crates/` has zero sites, not an error — so callers pass
    `ok_codes=(0, 1)`. Without that this crashed on the root commit, which is the
    good direction to fail but still the wrong one.
    """
    p = subprocess.run(["git", *args], capture_output=True, text=True)
    if p.returncode not in ok_codes:
        raise subprocess.CalledProcessError(p.returncode, p.args, p.stdout, p.stderr)
    return p.stdout


def item_end(src: str, i: int) -> int:
    """End offset of the item beginning at or after `i`: brace-matched, or to `;`."""
    m = ITEM.search(src, i)
    if not m:
        return i
    j = m.end()
    while j < len(src):
        if src[j] == "{":
            depth = 0
            while j < len(src):
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                    if depth == 0:
                        return j
                j += 1
            return j
        if src[j] == ";":
            return j
        j += 1
    return len(src)


class Census(NamedTuple):
    prod_names: frozenset[str]
    test_names: frozenset[str]
    prod_sites: int
    test_sites: int
    comment_sites: tuple[str, ...]
    mixed_sites: tuple[str, ...]
    listing: tuple[str, ...]
    # `path:line<TAB>name` — enough to say *what* moved when a total does, without
    # depending on the source line's text, which changes for unrelated reasons.
    site_keys: frozenset[str]
    keys: frozenset[str]
    keys_present: bool

    @property
    def sites(self) -> int:
        return self.prod_sites + self.test_sites

    @property
    def names(self) -> frozenset[str]:
        return self.prod_names | self.test_names

    @property
    def totals(self) -> tuple[int, int, int]:
        return (self.sites, len(self.names), len(self.keys))


def scan(rev: str) -> Census:
    paths = [
        f.split(":", 1)[1]
        for f in git(
            "grep",
            "-l",
            "-E",
            r"NodeDesc::(new|with_nodelist)\(",
            rev,
            "--",
            "crates/",
            ok_codes=(0, 1),
        ).split()
    ]

    prod_names: set[str] = set()
    test_names: set[str] = set()
    prod_sites = test_sites = 0
    comment_sites: list[str] = []
    mixed_sites: list[str] = []
    listing: list[str] = []
    site_keys: set[str] = set()

    for path in paths:
        # `paths` came from `git grep -l rev`, so the blob is always present at rev.
        src = git("show", f"{rev}:{path}")
        lines = src.split("\n")
        regions = [(m.start(), item_end(src, m.end())) for m in CFG.finditer(src)]
        for m in SITE.finditer(src):
            line_no = src.count("\n", 0, m.start()) + 1
            raw = lines[line_no - 1]
            text = raw.strip()
            # A site whose own line is a comment is prose, not an emission point.
            # This is the failure a whole-text scan introduces, so it is marked
            # rather than silently dropped.
            if text.startswith("//"):
                comment_sites.append(f"{path}:{line_no} | {text}")
                continue
            # A line carrying *both* code and a trailing `//` cannot be classified
            # here: deciding whether a match sits before or after the `//` is a Rust
            # lexer problem, because `//` occurs inside string literals (a URL, for
            # one). So such a site is counted — over-counting is the safe direction
            # for a census whose subject is things being missed — and reported, never
            # silently. Zero today, and printed on zero for the same reason the
            # exclusion is: a check that only speaks when it fires is indistinguishable
            # from one that is not running.
            slash = raw.find("//")
            if slash != -1:
                col = m.start() - (src.rfind("\n", 0, m.start()) + 1)
                after = "  <- match is AFTER the //, so likely prose" if col > slash else ""
                mixed_sites.append(f"{path}:{line_no} | {text}{after}")
            in_test = any(a <= m.start() <= b for a, b in regions)
            (test_names if in_test else prod_names).add(m.group(2))
            if in_test:
                test_sites += 1
            else:
                prod_sites += 1
            site_keys.add(f"{path}:{line_no}\t{m.group(2)}")
            listing.append(
                f"  {'test ' if in_test else 'prod '} {path}:{line_no:<5} "
                f"{m.group(2):<24} | {text}"
            )

    # A rev predating the materialiser has no `NODE_CLASSES` at all — zero keys is
    # the right answer, not a failure — but the empty set must not print as though
    # it were a successful read of a table, so the absence is recorded and shown.
    #
    # **Two spellings, because M18.1 moved the file.** The materialiser was
    # `_impl/parser.py` and is now `engine/parser.py` (`511d79e`); the table did
    # not move with it in any sense that matters — it still has 40 keys — but a
    # reader naming only the old path reports **zero keys at every rev after the
    # rename**, which prints as a *moved total* against the pinned baseline
    # rather than as a missing file. Measured 2026-10-09: the single-path reader
    # raised the `(53, 44, 40) -> (53, 44, 0)` alarm against a tree where the
    # table was intact. Reading both is what makes the third column a
    # measurement again at any revision, which is what this script claims.
    py = ""
    for candidate in (
        "python/django_rust_templates/engine/parser.py",
        "python/django_rust_templates/_impl/parser.py",
    ):
        try:
            py = git("show", f"{rev}:{candidate}")
            break
        except subprocess.CalledProcessError:
            continue
    keys: set[str] = set()
    for node in ast.walk(ast.parse(py)):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "NODE_CLASSES" for t in node.targets
        ):
            if isinstance(node.value, ast.Dict):
                keys = {ast.literal_eval(k) for k in node.value.keys}

    return Census(
        frozenset(prod_names),
        frozenset(test_names),
        prod_sites,
        test_sites,
        tuple(comment_sites),
        tuple(mixed_sites),
        tuple(sorted(listing)),
        frozenset(site_keys),
        frozenset(keys),
        bool(py),
    )


def report_movement(rev: str, got: Census, base_rev: str, base: Census) -> None:
    """Name the sites that differ, rather than leaving the reader a bare number."""
    added = got.site_keys - base.site_keys
    removed = base.site_keys - got.site_keys
    for label, moved in (("+ ", added), ("- ", removed)):
        for key in sorted(moved):
            where, name = key.split("\t")
            print(f"      {label}{where}  {name}")
    for c in got.comment_sites:
        if c not in base.comment_sites:
            print(f"      new comment-prose exclusion: {c}")
    print(f"      ({base_rev} -> {rev}; `--list` on both shows full context)")


def main() -> int:
    sha = git("rev-parse", "--short", REV).strip()
    dirty = bool(git("status", "--porcelain").strip())
    c = scan(REV)

    if LIST:
        print("\n".join(c.listing))
        print()

    print(f"tree: {REV}" + (f" = {sha}" if sha != REV else ""))
    print(f"  worktree dirty: {'yes' if dirty else 'no'}  (totals read the commit, not the worktree)")
    print(
        f"  emission call sites   {c.sites:>3}   "
        f"({c.prod_sites} production + {c.test_sites} #[cfg(test)])"
    )
    print(
        f"  distinct literals     {len(c.names):>3}   "
        f"({len(c.prod_names)} production + {len(c.test_names - c.prod_names)} test-only)"
    )
    print(
        f"  NODE_CLASSES entries  {len(c.keys):>3}"
        + ("" if c.keys_present else "   (no `_impl/parser.py` at this rev)")
    )
    # Always state the exclusion, even when it fired on nothing: "0 excluded" and
    # "did not look" are different claims and must not print the same.
    print(f"  comment-prose sites excluded: {len(c.comment_sites)}")
    for x in c.comment_sites:
        print(f"      {x}")
    print(f"  mixed-line sites (code + trailing //, counted): {len(c.mixed_sites)}")
    for x in c.mixed_sites:
        print(f"      {x}")
    print()
    print(f"  test-only literals: {sorted(c.test_names - c.prod_names)}")
    print(f"  production literals not constructible: {sorted(c.prod_names - c.keys) or 'none'}")
    print(f"  constructible with no literal producer: {sorted(c.keys - c.names)}")
    print()

    pinned = EXPECTED.get(sha) or EXPECTED.get(REV)
    want = pinned or EXPECTED[BASELINE]
    label = f"recorded at {sha}" if pinned else f"baseline {BASELINE}"
    if c.totals == want:
        print(f"ok: {c.totals} matches {label}")
        return 0

    print(f"! MOVED: {c.totals} != {want} ({label})")
    print(f"!   (sites, names, keys)  was {want}, now {c.totals}")
    base = scan(BASELINE)
    print(f"! what moved, against {BASELINE}:")
    report_movement(REV, c, BASELINE, base)
    print("! a moved total is not automatically a bug — a new producer is a legitimate")
    print("! reason — but it is where a comment-blind scanner would first lie.")
    return 1



if __name__ == "__main__":
    sys.exit(main())
