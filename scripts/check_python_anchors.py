#!/usr/bin/env python3
"""Every `` `name.py:N` `` anchor in our tree, checked against `.django/`.

The reviewer checked fourteen of our `base.py` 5xx–6xx citations and reported
seven wrong; applying the report broke two anchors that had been correct all
along, because two of the seven reports were themselves wrong. Sampling
afterwards found five more defects, in files nobody had re-read. So neither
"be careful" nor "trust the report" is a mechanism, and this is.

**The propagation case is why the manifest is keyed by snippet rather than by
site.** `smartif.py:166-172` is the `is not`/`not in` fold; `translate_token`,
the thing three of our comments claim it is, is at `:179-185`. That one wrong
number had been copied into `_impl/parser.py:716`, `host.rs:207` and
`desc.rs:216` — Rust and Python, three files, one defect — and the same review
that found the first of them found none of the other two. A per-site fix moves
three numbers and learns nothing; a snippet-keyed entry makes the wrong number
underivable and the same repair serve all three.

`scripts/anchors.txt` records, per anchor as it is written in the tree, the file
it names and **the source text the claim is about**. That direction is the whole
design: the snippet is the claim and the number is derived from it, so a wrong
number has exactly one repair. The reverse — record the line, compare whatever
is there — detects nothing, because at `defaulttags.py:1037` (cited for
`condition.eval(context)`, which is at `:326`) the text at the cited line is a
perfectly real line of Django.

    python -I scripts/check_python_anchors.py --check            # gate, exit 1 on drift
    python -I scripts/check_python_anchors.py --check --frozen   # also fails on shorthand
    python -I scripts/check_python_anchors.py --triage           # suspicion list, exit 0
    python -I scripts/check_python_anchors.py --update           # derive cited numbers

`--frozen` exists so the guard could land *before* the shorthand migration and be
closed after it: plain `--check` reports the shorthand as an `unmigrated` count
and does not fail on it, and `--frozen` fails on any shorthand at all. One flag,
because holding the checker until the migration is done removes the only
instrument that can verify the migration.

**What this does not check: that the snippet supports the claim beside it.** It
is a number-versus-text guard, not a reviewer. A green `--check` means the
citations point where they say, not that they are the right citations. The
`--triage` mode is the part that reads like a reviewer, and it is a heuristic —
it reports anchors with a code span quoted nearby that is not at the cited line
and leaves the judgement to a reader. Do not treat its silence as approval.

**The loose range is a sub-case of that limit, and it is the one a reader will
assume is covered.** A range whose endpoints both carry plausible snippets but
which spans the wrong region passes both `--check` and `--triage`: `if.rs` cites
`:1009-1018` for a claim about the optional `else` and the `endif` check, whose
real span is `:1007-1018`. Both ends look right, the anchor *is* a range, the
tool *does* check ranges — and it still passes.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
MANIFEST_PATH = ROOT / "scripts" / "anchors.txt"

# Ambiguous basenames, with how many of each the checkout holds. A bare
# basename for one of these cannot resolve, so the manifest names the path
# explicitly. `formats.py` is the awkward one — 89 candidates under
# `django/conf/locale/*/` — and the entry has to say which locale's formats the
# claim is about, which is a judgement the tool cannot make.
AMBIGUOUS = {
    "base.py": 38,
    "html.py": 2,
    "text.py": 2,
    "formats.py": 89,
    "custom.py": 2,
    "tests.py": 210,
    "test_base.py": 6,
    "test_partials.py": 2,
}

# Two forms, because the tree writes both: a bare basename (`` `base.py:615` ``)
# and a qualified path (`` `django/template/base.py:615` ``,
# `` `_impl/parser.py:210` ``). Resolution tries a qualified path under
# `.django/` then the repo root; a bare basename resolves through `resolve`.
ANCHOR_RE = re.compile(r"`([a-zA-Z_0-9/]+\.py):(\d+)(?:-(\d+))?`")

# The third form — a bare `` `:N` `` meaning "the file this comment is about" —
# is matched only to be *rejected*. A tool cannot resolve it without guessing
# the implied file, and there is nothing sound to guess from: 73% of the
# occurrences live in modules naming two or more Django files, and a module-level
# default would resolve those to whichever file the header named. `--frozen`
# fails on any of these; the migration is what removes them.
SHORTHAND_RE = re.compile(r"`:(\d+)(?:-(\d+))?`")

# The roots the gate covers. `docs/superpowers/plans/` and `.review/` are
# **excluded by an explicit rule, not by a glob that looks like an oversight** —
# see `NOT_SCANNED_BECAUSE`.
TREE_ROOTS = ["crates", "python", "tests"]
NOT_SCANNED_BECAUSE = {
    "docs/superpowers/plans/": (
        "A dated plan is a record of what was believed on a date and carries "
        "most of the whole-tree occurrences. Rewriting its citations makes it a "
        "record of what is true now and loses that distinction, which this "
        "project has already lost once. A stale plan anchor must never fail a "
        "build about live code. Holding the plans current would need the "
        "opposite treatment — verified once, frozen, never rewritten — and a "
        "report of their own."
    ),
    ".review/": (
        "Same class, and for one reason beyond churn: a review record is "
        "evidence about a date. The reviewer's next report on a wrong anchor "
        "quotes our wrong anchor *verbatim* — `smartif.py:166-172` at "
        "`_impl/parser.py:716` was reported exactly that way — so scanning this "
        "directory would fail the build on the reviewer's accurate quotation of "
        "our own defect, and the only way to make it green is to falsify the "
        "finding. Every file here is dated by construction (round-N, "
        "mN-summary/dispatch/brief/seams/recon); none is a live document."
    ),
}

# Basenames that are *ours*, so a bare `name.py:N` matching one resolves to the
# repo root rather than into `.django/`. **Measured: none of the five exists
# anywhere under `.django/`**, and `test_anchor_manifest.py` asserts that rather
# than leaving it to hold by coincidence — this is a rule that *shadows*. The
# day a Django bump adds `django/template/parser.py`, every bare `parser.py:N`
# in the tree silently resolves to ours and the guard goes quietly wrong in the
# one direction it exists to prevent. `parser.py` is the plausible one.
OUR_OWN = {"_shim.py", "differential.py", "parser.py", "test_materialise.py", "test_parser_parity.py"}

# `--triage`'s window: how far either side of an anchor a quoted code span is
# taken to be "the claim beside it". 200 characters is the plan's figure and it
# is generous on purpose — the cost of a wide window is noise a reader discards,
# and the cost of a narrow one is the defect going unreported.
TRIAGE_WINDOW = 200

# What makes a `.review/` file a record rather than a live document: its name
# carries the milestone or round it is about. `test_anchor_manifest.py` asserts
# every file there matches, so a live document landing in `.review/` reddens
# instead of being silently excluded. Deliberately loose after the prefix — the
# suffixes in use are `-summary`, `-dispatch`, `-brief`, `-seams`, `-recon`,
# `-response`, `-acceptance-blind-spot`, `-task-10-brief`, `-wave-b-brief`, and
# the convention is the date, not the noun.
_DATED_REVIEW_RE = re.compile(r"^(m\d+|round-\d+)(-\S+)?\.md$")

_SPAN_RE = re.compile(r"`([^`\n]+)`")


def _scan(checkout):
    """Every basename under `checkout`, so ambiguity is measured not assumed."""
    found = {}
    for path in checkout.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        found.setdefault(path.name, []).append(path)
    return found


def resolve(name, checkout=None, manifest=None):
    """The file an anchor names, or `SystemExit` when it cannot be decided.

    Each step is an error rather than a guess. A qualified path is tried under
    `.django/` then the repo root. A bare basename that is one of `OUR_OWN`
    resolves to ours; otherwise it is looked up under `.django/`, and exactly
    one hit resolves — more than one is ambiguous and zero is a missing file.
    `manifest` carries the explicit paths the author already decided, consulted
    before the search, because the decision belongs in a reviewable file rather
    than in a heuristic.
    """
    checkout = CHECKOUT if checkout is None else checkout
    explicit = (manifest or {}).get(name) if "/" not in name else None
    if explicit is not None:
        return Path(explicit) if Path(explicit).is_absolute() else ROOT / explicit
    if "/" in name:
        for base in (checkout, ROOT):
            candidate = base / name
            if candidate.is_file():
                return candidate
        raise SystemExit(f"anchor names `{name}`, which is in neither {checkout} nor {ROOT}")
    if name in OUR_OWN:
        candidate = ROOT / name
        if candidate.is_file():
            return candidate
        raise SystemExit(f"`{name}` is in OUR_OWN but is not at {candidate}")
    hits = [
        path
        for path in checkout.rglob(name)
        if path.is_file() and "__pycache__" not in path.parts
    ]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f"anchor names `{name}`, which is not under {checkout}")
    listing = "\n  ".join(str(path.relative_to(checkout)) for path in sorted(hits))
    raise SystemExit(
        f"anchor names `{name}`, which is ambiguous under {checkout} "
        f"({len(hits)} candidates):\n  {listing}\n"
        f"Give the path explicitly in scripts/anchors.txt."
    )


def scan_tree(roots=None):
    """`(path, text)` for every scannable file, in a stable order."""
    roots = TREE_ROOTS if roots is None else roots
    for root in roots:
        base = ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            yield path.relative_to(ROOT), text


def _line_of(path, number):
    lines = path.read_text().splitlines()
    if number < 1 or number > len(lines):
        return None
    return lines[number - 1]


def check(checkout=None, tree=None, manifest=None, frozen=False, report=None):
    """Verify every anchor in `tree` against its manifesto entry.

    Raises `SystemExit(1)` on any failure, so it is usable as a gate. The three
    failure kinds are separate because they have separate repairs: an anchor with
    no entry needs one authored; an entry whose snippet is not at the line is a
    wrong number *or* a wrong snippet, and the message prints both so the reader
    can tell which; a shorthand needs naming.
    """
    checkout = CHECKOUT if checkout is None else checkout
    manifest = MANIFEST if manifest is None else manifest
    tree = list(scan_tree() if tree is None else tree)
    report = (lambda text: print(text, file=sys.stderr)) if report is None else report

    failures = []
    shorthand = []
    occurrences = 0
    for path, text in tree:
        for match in ANCHOR_RE.finditer(text):
            occurrences += 1
            anchor = match.group(0).strip("`")
            entry = manifest.get(anchor)
            if entry is None:
                failures.append(f"{path}: `{anchor}` has no entry in scripts/anchors.txt")
                continue
            failures.extend(_check_entry(path, anchor, match, entry, checkout))
        for match in SHORTHAND_RE.finditer(text):
            shorthand.append(f"{path}: `{match.group(0).strip('`')}` names no file")

    if shorthand:
        # Reported either way, failed on only under `--frozen`: the guard lands
        # before the migration it verifies, or the migration has no instrument.
        report(
            f"unmigrated: {len(shorthand)} shorthand anchor(s) naming no file"
            + ("" if frozen else " (not a failure until --frozen)")
        )
        if frozen:
            failures.extend(shorthand)

    for failure in failures:
        report(failure)
    report(
        f"{occurrences} anchor occurrence(s) checked, "
        f"{len(manifest)} manifest entr(ies), {len(failures)} failure(s)"
    )
    if failures:
        raise SystemExit(1)


def _check_entry(tree_path, anchor, match, entry, checkout):
    """The failures for one anchor, as a list of messages."""
    named, line, snippet, end_snippet = entry
    start = int(match.group(2))
    end = int(match.group(3)) if match.group(3) else None
    problems = []
    if line != start:
        problems.append(
            f"{tree_path}: `{anchor}` — the tree says line {start}, "
            f"scripts/anchors.txt says {line}"
        )
        return problems
    try:
        path = resolve(named, checkout=checkout)
    except SystemExit as error:
        return [f"{tree_path}: `{anchor}` — {error}"]
    text = _line_of(path, start)
    if text is None:
        return [f"{tree_path}: `{anchor}` — {named} has no line {start}"]
    if snippet not in text:
        problems.append(
            f"{tree_path}: `{anchor}` — {named}:{start} is {text.strip()!r}, "
            f"which does not contain {snippet!r}"
        )
    if end is not None and end_snippet is not None:
        end_text = _line_of(path, end)
        if end_text is None or end_snippet not in end_text:
            problems.append(
                f"{tree_path}: `{anchor}` — {named}:{end} is "
                f"{(end_text or '<past the end>').strip()!r}, which does not "
                f"contain {end_snippet!r}"
            )
    return problems


def triage(checkout=None, tree=None, report=None):
    """Anchors with a nearby quotation that is not at the cited line.

    Ordered by file, not by suspicion: ordering by suspicion would encode a
    heuristic's confidence as though it were a finding. Every row is judged by a
    reader, which is the point. The known-wrong numbers are in this list.

    **Measured, and the number is the tool's indictment of itself**: of the 554
    anchor occurrences in the live tree, 305 do not resolve at all (a bare
    `base.py`, `tests.py` or `formats.py` with no manifest entry) and of the 249
    that do, **237 produce at least one row and only 12 come back clean**. So
    this mode fires on 95% of what it can see. It is a reading aid over a list
    of candidates and not a filter — the discrimination the plan hoped for is in
    `--check`, which compares an exact snippet to an exact line, and this is the
    part that still needs a person.
    """
    checkout = CHECKOUT if checkout is None else checkout
    tree = list(scan_tree() if tree is None else tree)
    report = print if report is None else report

    rows = 0
    for path, text in tree:
        for match in ANCHOR_RE.finditer(text):
            anchor = match.group(0).strip("`")
            named, start = match.group(1), int(match.group(2))
            try:
                source = _line_of(resolve(named, checkout=checkout), start)
            except SystemExit:
                continue
            if source is None:
                continue
            window = text[max(0, match.start() - TRIAGE_WINDOW) : match.end() + TRIAGE_WINDOW]
            for span in _SPAN_RE.findall(window):
                if ANCHOR_RE.fullmatch(f"`{span}`") or SHORTHAND_RE.fullmatch(f"`{span}`"):
                    continue
                if len(span) < 3 or "\n" in span:
                    continue
                if span in source:
                    continue
                report(f"{path}: `{anchor}` — nearby quote {span!r} is not at {named}:{start}")
                rows += 1
    report(f"{rows} row(s) to read; most are unrelated nearby backticks")


def load_manifest(path=None):
    """`scripts/anchors.txt` as `{anchor: (path, line, snippet, end_snippet)}`."""
    path = MANIFEST_PATH if path is None else path
    manifest = {}
    if not path.exists():
        return manifest
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        fields = raw.split("\t")
        if len(fields) != 6:
            raise SystemExit(f"{path}:{number}: expected 6 tab-separated fields, got {len(fields)}")
        anchor, named, line, end, snippet, end_snippet = fields
        manifest[anchor] = (
            named,
            int(line),
            snippet,
            None if end_snippet == "-" else end_snippet,
        )
    return manifest


def save_manifest(manifest, path=None):
    path = MANIFEST_PATH if path is None else path
    rows = []
    for anchor, (named, line, snippet, end_snippet) in manifest.items():
        end = anchor.split(":")[1]
        end = end.split("-")[1] if "-" in end else "-"
        rows.append(
            "\t".join([anchor, named, str(line), end, snippet, end_snippet or "-"])
        )
    rows.sort(key=lambda row: (row.split("\t")[1], int(row.split("\t")[2])))
    path.write_text("\n".join(rows) + "\n")


def update(manifest=None, checkout=None, report=None):
    """Rewrite each entry's number from where its snippet actually is."""
    checkout = CHECKOUT if checkout is None else checkout
    manifest = MANIFEST if manifest is None else manifest
    report = print if report is None else report
    changed = 0
    for anchor, (named, line, snippet, end_snippet) in list(manifest.items()):
        path = resolve(named, checkout=checkout)
        lines = path.read_text().splitlines()
        hits = [i for i, text in enumerate(lines, start=1) if snippet in text]
        if not hits:
            report(f"`{anchor}`: {snippet!r} is not in {named} at any line")
            continue
        if len(hits) > 1:
            report(f"`{anchor}`: {snippet!r} is at {hits} in {named}; pick one by hand")
            continue
        if hits[0] != line:
            report(f"`{anchor}`: {line} → {hits[0]}")
            manifest[anchor] = (named, hits[0], snippet, end_snippet)
            changed += 1
    report(f"{changed} entr(ies) moved")
    return manifest


# The ambiguous basenames above, resolved once each rather than 176 times in the
# manifest — and **only for `--seed`**, never for `resolve`. `resolve` has to
# refuse a bare `base.py` (the test pins that), because the refusal is what makes
# the manifest carry an explicit path per entry. This table is the authoring
# shortcut that fills that column in, and every value in it is a decision about
# *our* prose rather than a guess about Django: our `base.py` anchors are about
# `Parser` and `Token`, our `formats.py` ones about `dateformat`, and so on.
BASENAMES = {
    "base.py": "django/template/base.py",
    "html.py": "django/utils/html.py",
    "text.py": "django/utils/text.py",
    "formats.py": "django/utils/formats.py",
    "custom.py": "tests/template_tests/templatetags/custom.py",
    "tests.py": "tests/template_tests/tests.py",
    "test_base.py": "tests/template_tests/test_base.py",
    "test_partials.py": "tests/template_tests/test_partials.py",
}

MANIFEST = load_manifest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="gate: exit 1 on drift")
    group.add_argument("--triage", action="store_true", help="print the suspicion list")
    group.add_argument("--update", action="store_true", help="derive cited numbers")
    parser.add_argument(
        "--frozen",
        action="store_true",
        help="with --check, also fail on a shorthand anchor naming no file",
    )
    args = parser.parse_args(argv)

    if args.triage:
        triage()
    elif args.update:
        save_manifest(update())
    else:
        check(frozen=args.frozen)


if __name__ == "__main__":
    main()
