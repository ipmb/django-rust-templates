#!/usr/bin/env python3
"""Every `` `name.py:N` `` anchor in our tree, checked against `.django/`.

The reviewer checked fourteen of our `base.py` 5xx–6xx citations and reported
seven wrong; applying the report broke two anchors that had been correct all
along, because two of the seven reports were themselves wrong. Sampling
afterwards found five more defects, in files nobody had re-read. So neither
"be careful" nor "trust the report" is a mechanism, and this is.

**The propagation case is why the manifest is keyed by snippet rather than by
site.** smartif.py:166-172 is the `is not`/`not in` fold; `translate_token`,
the thing three of our comments claim it is, is at `:179-185`. That one wrong
number had been copied into `_impl/parser.py:716`, `host.rs:207` and
`desc.rs:216` — Rust and Python, three files, one defect — and the same review
that found the first of them found none of the other two. A per-site fix moves
three numbers and learns nothing; a snippet-keyed entry makes the wrong number
underivable and the same repair serve all three.

**THE ESCAPE, and it is the delimiter itself.** A backtick is what makes an
anchor a citation. This tool reads text and cannot tell a citation from a
quotation or a test fixture, so anything anchor-shaped that is *not* a claim
about Django is written some other way:

  - **quoting a defect** — write it without backticks, as the paragraph above
    writes smartif.py:166-172. It reads as prose because it *is* prose.
  - **anchor-shaped test data** — build the string at runtime; see `anchor()`
    in `tests/test_anchor_manifest.py`, which exists for exactly this and says
    so.

Measured, because the size of this is not obvious: the checker's own test file
held **ten** anchor-shaped literals — two known-wrong quotations and eight
fixtures — and one fixture, `defaulttags.py:99999`, is deliberately past the end
of its file, so *no* manifest entry can make it pass. A per-file exemption would
have been the tempting repair and is how a guard acquires holes; this rule is
one spelling that any file can use.

`scripts/anchors.txt` records, per anchor as it is written in the tree, the file
it names and **the source text the claim is about**. That direction is the whole
design: the snippet is the claim and the number is derived from it, so a wrong
number has exactly one repair. The reverse — record the line, compare whatever
is there — detects nothing, because at `defaulttags.py:1037` (cited for
`condition.eval(context)`, which is at `:326`) the text at the cited line is a
perfectly real line of Django.

**An anchor's granularity follows the sentence's subject, so one call can carry
two correct numbers.** A claim about a *call* cites the call head: `host.rs`
lists `defaultfilters.py:186`, `:214` and `:912` as the three
`formats.number_format(` call sites, and 912 is a one-liner where head and
argument share a line. A claim about an *argument* cites the argument line:
`floatformat.rs` compares `number` against `"%d" % int(d)`, which is 187's
shape, so it cites `:215` where the call head is `:214`. Both files are right —
the test is which line holds the text the sentence names — and a sweep that
makes them agree is unmaking a measurement.

    python -I scripts/check_python_anchors.py --check            # gate, exit 1 on drift
    python -I scripts/check_python_anchors.py --check --frozen   # also fails on shorthand
    python -I scripts/check_python_anchors.py --triage           # suspicion list, exit 0
    python -I scripts/check_python_anchors.py --update           # report both bases, write nothing
    python -I scripts/check_python_anchors.py --update=django    # move Django's cited numbers
    python -I scripts/check_python_anchors.py --update=crates    # …or ours
    python -I scripts/check_python_anchors.py --seed             # fill missing entries
    python -I scripts/check_python_anchors.py --prune            # drop rows nothing cites

`--seed` writes a first draft of the manifest from the tree: it takes the number
as the tree already says it and the snippet from the code the prose quotes
beside it. It does not verify anything, and it cannot — a snippet read off the
cited line would agree with it by construction. The draft exists to be failed by
`--check`, which is where the real defects surface.

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
which spans the wrong region passes both `--check` and `--triage`: if.rs cited
1009-1018 for a claim about the optional `else` and the `endif` check, whose real
span is 1007-1018. Both ends looked right, the anchor *is* a range, the tool
*does* check ranges — and it passed for as long as it stood, because one line of
a function looks much like the next. It is in the past tense here because it was
read and repaired, not because anything below this line would have caught it.

**A snippet that is the subject's own name is a second sub-case, and the one that
looks most like verification.** Where the prose quotes no code, `--seed` falls
back to the name the sentence is about — `urlizetrunc`, `center`, `striptags` —
and such an entry passes `--check`, passes `--update`, survives any later line
move inside the span, and verifies nothing: the name occurs in the span by
construction, so the comparison cannot fail. It is *quoted*, so it counts in the
cited total and `0 failures` presents it as verified, which no unquoted row can
claim. `--check` counts them separately (`name-only`), because a number written
here goes stale the moment the manifest moves and a number in output cannot.
The repair is not a tool change but the rule that the snippet be authored from
the sentence's own claim in the same edit that names the file.

**The authoring rule, and it is the only thing that puts a snippet in a row:**
*when a sentence argues from a citation, quote the code it argues from, in that
sentence, in the same edit that names the file.* The quote is what `--seed`
records and what `--check` can later falsify; a row seeded for coverage that no
sentence ever argues from stays unquoted, and that is correct rather than a
backlog. Bulk-quoting the unquoted rows instead would ratify each sentence's
subject along with its number — the misreading class below made permanent, one
row per sentence, with `0 failures` to show for it. Measured at `b83ec01` over
the 155 snippetless rows there: **59** have a citing file that already quotes a
selective span beside the citation (28 ranges, 31 single lines), so a requote
could take them from the prose as written; the other **96** have no quote
anywhere in their citers and cannot be repaired without re-reading the claim.

**Three ways a number goes wrong, and only two of them are repairs a tool can
make.** Worth separating, because the remedy differs and the wrong remedy is
applied with confidence:

  - **Drift.** The citation was right when written; the line moved under it.
    `--update` derives the new number, and this is the class the manifest's
    snippet-keying was built for.
  - **A misreading.** The number never pointed at the claim — the sentence names
    `translate_token` and cites the fold above it. Wants a re-read from the
    sentence, not a derived number, because deriving one would ratify the
    sentence's subject along with its number.
  - **Stale at birth.** The citing commit is *also* the commit that edits the
    cited file, so the number was measured against text that the same diff
    replaced. Nothing drifted and nothing was misread: the measurement was
    taken from the right file a moment too early. Measured instance: mod.rs
    392-418 was `extend_nodelist` at `52f1cb1^` (the function is at 392, its
    last line 418), and `52f1cb1` is the commit that wrote the citation into
    `host.py` **and** moved the function to 429, leaving 392-418 as
    `invalid_block_tag`'s tail. The remedy is a rule rather than a repair — a
    citation into a file the same commit edits is measured against the post-edit
    file — and it is now closed by construction: `--check --frozen` runs on the
    commit, so a citation stale with respect to the file the commit leaves
    behind reddens the build before it lands.

**Every limit above is a limit of the per-file policy, which has no terminating
condition of its own.** Naming is done a file at a time, and the file is visited
because it holds shorthand; an unquoted entry in a file that never held any is
therefore never requoted, and `unmigrated: 0` — the number the migration is
tracked by — says nothing about it. So `--check` counts those entries separately:
an unquoted row is *stranded* when every file citing it is already
shorthand-free. They are not failures and `--frozen` does not fail on them;
they are the part of the backlog the migration's own progress metric cannot see.

**Two of those limits select for the same anchors, and the largest is not a limit
of this tool at all.** Unquoted entries are not a random half: an entry is
unquoted exactly when its cited line is unremarkable (a blank, a bare delimiter,
or a range spanning a whole function), which is the population the loose range
above selects for too, so the residual risk is concentrated rather than spread —
**and it has a shape.** `_check_entry` with a `None` snippet tests one thing, that
the line exists, so an unquoted row is blind *specifically to the wrong-file
class*: any file long enough has the line. Measured as an experiment rather than
argued — a row naming `library.py` when the claim is `tag_27584.py` returns zero
failures unquoted and one failure once the same row carries the source text. That
is the defect class with the most instances in this project (the filename
inheritances), and it is the one class an unquoted row cannot catch. And **prose
that cites nothing cannot be anchored at all**: a wrong statement about Django
with no `name.py:N` beside it is invisible to every mode here, which is how this
task's misquoted bodies and its one function name that exists nowhere in the
checkout survived four rounds of checking it. A green `--check` is a statement
about the entries that carry a
claim, not about our prose.

**A row the tree no longer cites is not checked at all**, and that direction is
worth stating because it looks like coverage. `--check` walks the *tree* and
looks each citation up, so an entry whose anchor has been re-pointed is never
asked for, never compared, and never fails — it just sits in the manifest,
counted in the entry total, holding a claim nothing re-measures. Measured when
this paragraph was written: two such rows, left by the previous sweep, and one
of them recorded a snippet that is not on its line at all — a wrong claim no
`--check` could reach. So `--check` now splits the count into cited and dead,
`--frozen` fails on a dead row, and `--prune` is the repair.
"""

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
MANIFEST_PATH = ROOT / "scripts" / "anchors.txt"

# The second base. `CHECKOUT` is Django's Python; this is our own tree, and the
# two are the populations `--update` separates. Ours is mostly Rust, but it is
# not only Rust — `python/django_rust_templates/` and the files in `OUR_OWN` are
# cited the same way — so a row is classified by *which base its path resolves
# under*, not by its extension.
CRATES = ROOT / "crates"

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

# Our own package, which is the third base a qualified anchor may be relative
# to: `_impl/parser.py:716` is a spelling our prose uses throughout and means
# `python/django_rust_templates/_impl/parser.py`.
OWN_PACKAGE = ROOT / "python" / "django_rust_templates"

# Directories a suffix search must not walk. `.django` is here because the
# checkout is searched separately and first, so leaving it in would make every
# Django anchor ambiguous with itself; the rest are build and vendor trees.
_SKIP_DIRS = {".git", ".venv", "target", "__pycache__", ".django", "node_modules"}

# `_index`'s walk cache, keyed by base directory. `resolve` is called once per
# anchor occurrence and the tree does not change under it, so walking twice is
# pure waste — and the difference was 49 seconds against 0.4.
_INDEX_CACHE = {}

# Two forms, because the tree writes both: a bare basename (`` `base.py:615` ``)
# and a qualified path (`` `django/template/base.py:615` ``,
# `` `_impl/parser.py:210` ``). A qualified path resolves against the bases
# `resolve` documents; a bare basename resolves through the same function's
# basename rules.
#
# **The path class widened, and that is the second base.** Ours cites our own
# Rust as well as Django's Python, and before this the whole `.rs` corpus was
# outside the pattern — `crates/template-*` was dropped by the missing hyphen,
# and a bare `:N` following a `.rs` path was re-attributed to the nearest
# preceding `.py` anchor, which is how five filename-inheritance mis-attributions
# got into the tree. `-` is for `template-core`, `.` is for `foo.rs` **and** for
# the seven citations written with the `.django/` checkout prefix (a leading dot
# the old class could not match, in the `.py` population this tool claimed to
# cover completely). Measured before writing it: 952 matches against the 914 the
# old pattern saw, 26 new distinct — 19 `.rs` and 7 `.django/`-prefixed — every
# one a real citation and nothing that is not one.
ANCHOR_RE = re.compile(r"`([a-zA-Z_0-9/.\-]+\.(?:py|rs)):(\d+)(?:-(\d+))?`")

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
#
# `docs/superpowers/specs/` is **in**, and that is a correction: it was excluded
# by omission until it was measured. The two exclusions below are both "this file
# is a dated record"; the spec is the opposite — it is §2's acceptance reference
# and outlives every plan, so neither reason reaches it, and `NOT_SCANNED_BECAUSE`
# said nothing about it.
#
# What the widening found, measured as `ANCHOR_RE` occurrences/distinct at the
# commit that added the root: the spec **42/39**, `docs/superpowers/plans/`
# **415/297**, `.review/` **550/384**. 830 of those distinct anchors are outside
# the gate and are counted here rather than quietly dropped; the spec's 39 are
# not. **29 of the spec's 39 had no manifest entry** — every one of them a claim
# nothing re-measured, which is the hole the omission left. Two of the 29 were
# *wrong numbers*, not merely unchecked ones: `parser/mod.rs:336` named the
# `else` branch of a registry dispatch for a check that is at `:435` (a line
# number correct against an M2-era tree and cited as live), and
# `base.py:948-1000` stopped twelve lines short of the end of `_resolve_lookup`,
# which the sentence beside it describes in full. Both numbers are corrected in
# the spec by the commit that added this root. The other 27 resolved and matched.
# Three of the spec's anchors were bare `:N` shorthands; they are now named, so
# the spec is shorthand-free and `--frozen` covers it.
TREE_ROOTS = ["crates", "python", "tests", "docs/superpowers/specs"]
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
        "quotes our wrong anchor *verbatim* — smartif.py:166-172 at "
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

# A snippet that is the subject's own name — `slugify`, `center` — occurs in the
# span by construction, so the comparison cannot fail. Counted, because the
# header describes the class and a number stored in a docstring goes stale.
_NAME_ONLY_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def _index(base):
    """`(basenames, relative paths)` under `base`, walked once and cached.

    Pruned **during** the walk rather than filtered after it, because `rglob`
    descends into `.venv` and `target` anyway: an unpruned per-anchor walk
    measured **49 seconds** over this tree, which is not a price a gate can pay
    for a check that runs on every commit. Cached because `resolve` is called
    once per anchor occurrence and the tree does not change under it.
    """
    key = str(base)
    if key not in _INDEX_CACHE:
        names, paths = {}, []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            for filename in sorted(filenames):
                path = Path(dirpath) / filename
                relative = path.relative_to(base).as_posix()
                paths.append(relative)
                names.setdefault(filename, []).append(relative)
        _INDEX_CACHE[key] = (names, paths)
    return _INDEX_CACHE[key]


def _suffix_match(name, base):
    """Relative paths under `base` ending with `name`."""
    _, paths = _index(base)
    return [relative for relative in paths if relative.endswith("/" + name)]


def resolve(name, checkout=None, manifest=None):
    """The file an anchor names, or `SystemExit` when it cannot be decided.

    Each step is an error rather than a guess. A **qualified** path is first
    tried against `checkout` and the repo root, which covers the fully-spelled
    forms (`django/template/base.py`, `python/django_rust_templates/…`).

    What remains are **suffix** citations, not prefix ones, and that is a
    measurement rather than a style: `filter_tests/test_dictsort.py` is
    `tests/template_tests/filter_tests/test_dictsort.py` while
    `i18n/test_blocktranslate.py` is
    `tests/template_tests/syntax_tests/i18n/test_blocktranslate.py`, so **no
    single base makes both resolve** — the ellipsis sits at a different depth in
    each. So a name that resolves exactly nowhere is matched against path
    suffixes under `checkout` then the repo root, and exactly one hit resolves;
    more than one is ambiguous. A *wrong* prefix still fails here, which is the
    point: `.django/tests/` is where Django's suite lives, so
    `django/tests/filter_tests/…` has no suffix match either and
    `desc.rs:319`'s `django/tests/…` was caught exactly this way.

    A bare basename that is one of `OUR_OWN` resolves to ours; otherwise it is
    looked up under `checkout`, and exactly one hit resolves — more than one is
    ambiguous and zero is a missing file. `manifest` carries the explicit paths
    the author already decided, consulted before the search, because the
    decision belongs in a reviewable file rather than in a heuristic.

    **A bare `.rs` basename searches ours, never the checkout** — nothing under
    `.django/` is Rust, so the basename cannot be Django's and the search needs
    no preference rule. Uniqueness is still required: `host.rs` names two files
    under `crates/`, and an ambiguity is an error rather than a guess, which is
    why those citations carry a suffix path. **The base makes a *named* path
    checkable; it never makes an unnamed one optional** — a bare `:N` is
    `SHORTHAND_RE`'s business and stays a failure under `--frozen` whatever a
    base could resolve it to.
    """
    checkout = CHECKOUT if checkout is None else checkout
    explicit = (manifest or {}).get(name) if "/" not in name else None
    if explicit is not None:
        return Path(explicit) if Path(explicit).is_absolute() else ROOT / explicit
    if "/" in name:
        bases = (checkout, ROOT, OWN_PACKAGE)
        for base in bases:
            candidate = base / name
            if candidate.is_file():
                return candidate
        for base in (checkout, ROOT):
            hits = _suffix_match(name, base)
            if len(hits) == 1:
                return base / hits[0]
            if len(hits) > 1:
                raise SystemExit(
                    f"anchor names `{name}`, which {len(hits)} files under {base} "
                    f"end with:\n  "
                    + "\n  ".join(sorted(hits))
                    + "\nGive the path explicitly in scripts/anchors.txt."
                )
        raise SystemExit(
            f"anchor names `{name}`, which is no file under {checkout} and no "
            f"path suffix under {ROOT}"
        )
    if name.endswith(".rs"):
        names, _ = _index(CRATES)
        hits = names.get(name, [])
        if len(hits) == 1:
            return CRATES / hits[0]
        if not hits:
            raise SystemExit(f"anchor names `{name}`, which is not under {CRATES}")
        raise SystemExit(
            f"anchor names `{name}`, which is ambiguous under {CRATES} "
            f"({len(hits)} candidates):\n  "
            + "\n  ".join(sorted(hits))
            + "\nGive the path explicitly in scripts/anchors.txt."
        )
    if name in OUR_OWN:
        candidate = ROOT / name
        if candidate.is_file():
            return candidate
        raise SystemExit(f"`{name}` is in OUR_OWN but is not at {candidate}")
    names, _ = _index(checkout)
    hits = names.get(name, [])
    if len(hits) == 1:
        return checkout / hits[0]
    if not hits:
        raise SystemExit(f"anchor names `{name}`, which is not under {checkout}")
    raise SystemExit(
        f"anchor names `{name}`, which is ambiguous under {checkout} "
        f"({len(hits)} candidates):\n  "
        + "\n  ".join(hits)
        + "\nGive the path explicitly in scripts/anchors.txt."
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


def _numbers_of(anchor):
    """`(start, end)` as the anchor itself spells them, `end == start` for one line.

    The numbers live in the *key*, which is the anchor as the tree writes it, so
    this is the pair `_check_entry` compares the manifest's `line` against.
    """
    tail = anchor.split(":", 1)[1]
    first, _, last = tail.partition("-")
    return int(first), int(last) if last else int(first)


def _span_of(anchor, lines):
    """The source text the anchor's own numbers span, or `None` past the end.

    **The one acceptance rule this tool has, called by both halves.** `--check`
    accepts a snippet that occurs anywhere in here; `--update` moves a number only
    when the snippet occurs nowhere in here. It is a function rather than a
    comment because the two modes were written with the rule duplicated, and the
    disagreement was measured: running `--update` on this manifest reddened 61 of
    its 160 quoted entries — every one a *correct* anchor whose snippet sits
    inside its span but not on the span's first line (`base.py:198-200` quotes
    `parser.parse()`, which is at 199).
    """
    first, last = _numbers_of(anchor)
    if first < 1 or first > len(lines):
        return None
    return "\n".join(lines[first - 1 : last])


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
    unquoted = set()
    name_only = set()
    cited = set()
    citers = {}
    shorthand_free = set()
    occurrences = 0
    for path, text in tree:
        here = [match for match in SHORTHAND_RE.finditer(text)]
        if not here:
            shorthand_free.add(path)
        for match in ANCHOR_RE.finditer(text):
            occurrences += 1
            anchor = match.group(0).strip("`")
            cited.add(anchor)
            citers.setdefault(anchor, set()).add(path)
            entry = manifest.get(anchor)
            if entry is None:
                failures.append(f"{path}: `{anchor}` has no entry in scripts/anchors.txt")
                continue
            if entry[2] is None:
                unquoted.add(anchor)
            elif _NAME_ONLY_RE.fullmatch(entry[2]):
                name_only.add(anchor)
            failures.extend(_check_entry(path, anchor, match, entry, checkout))
        for match in here:
            shorthand.append(f"{path}: `{match.group(0).strip('`')}` names no file")

    dead = sorted(set(manifest) - cited)

    # An unquoted entry is requoted by the naming pass over the file that cites
    # it, so one whose citing files are *all* already shorthand-free is one no
    # pass will revisit: the migration can end without it, and `unmigrated: 0`
    # is then not the same statement as "every row records a claim". Counted
    # separately for that reason, by citation rather than by file, because the
    # per-file policy is what terminates.
    stranded = sorted(a for a in unquoted if citers.get(a, set()) <= shorthand_free)

    if unquoted:
        # An entry with no snippet is *unverified*, not a failure: `--seed`
        # writes one where our prose quotes no code, and recording the cited
        # line's own text would be the direction that detects nothing. Counted
        # so the backlog is visible rather than silently green.
        report(
            f"unquoted: {len(unquoted)} entr(ies) record no source text, so only "
            f"the line number is checked (not a failure)"
        )
        if stranded:
            report(
                f"  {len(stranded)} of them are cited only from files with no "
                f"remaining shorthand, so no naming pass will requote them "
                f"(--frozen does not fail on these)"
            )

    if name_only:
        report(
            f"name-only: {len(name_only)} entr(ies) quote a bare identifier where "
            f"a line of source was meant — its span contains it by construction, "
            f"so the comparison cannot fail (not a failure)"
        )

    if shorthand:
        # Reported either way, failed on only under `--frozen`: the guard lands
        # before the migration it verifies, or the migration has no instrument.
        report(
            f"unmigrated: {len(shorthand)} shorthand anchor(s) naming no file"
            + ("" if frozen else " (not a failure until --frozen)")
        )
        if frozen:
            failures.extend(shorthand)

    if dead:
        # A dead row is unchecked by construction — `check` looks entries up by
        # the tree's citations, so nothing ever compares one. Reported always, so
        # the manifest cannot quietly overstate its coverage, and failed on under
        # `--frozen`, where the manifest is meant to be settled. `--prune` drops
        # them and prints each, because the snippet is the only copy of that
        # claim.
        report(
            f"dead: {len(dead)} manifest entr(ies) are cited nowhere in the tree"
            + ("" if frozen else " (not a failure until --frozen)")
        )
        if frozen:
            failures.extend(
                f"scripts/anchors.txt: `{anchor}` is cited nowhere in the tree"
                for anchor in dead
            )

    for failure in failures:
        report(failure)
    report(
        f"{occurrences} anchor occurrence(s) checked, "
        f"{len(manifest) - len(dead)} cited of {len(manifest)} manifest entr(ies), "
        f"{len(failures)} failure(s)"
    )
    if failures:
        raise SystemExit(1)


def _check_entry(tree_path, anchor, match, entry, checkout):
    """The failures for one anchor, as a list of messages.

    `snippet` is checked **within the anchor's span**, not at its first line.
    The common form of an anchor here is a whole function, and its prose is
    about the function body while its first line is a decorator: requiring the
    quotation on line `start` failed 178 true claims of that shape and caught
    nothing the in-span rule misses except a start that is too *early* — which
    the number and `end_snippet` still constrain. A guard that fails on true
    claims is one that gets deleted.

    A `None` snippet means the entry records no claim about the source text, so
    only the line's *existence* is checked. That is a weaker guarantee than the
    rest of this tool and it is labelled as such rather than dressed up: it
    catches a renamed file or a shrunken one, and nothing else.
    """
    named, line, snippet, end_snippet = entry
    start = int(match.group(2))
    end = int(match.group(3)) if match.group(3) else None
    problems = []
    if line != start:
        problems.append(
            f"{tree_path}: `{anchor}` — the tree cites line {start}, "
            f"scripts/anchors.txt records {line}. `--update` writes the line the "
            f"entry's snippet is on, so one of these two numbers is wrong"
        )
        return problems
    try:
        path = resolve(named, checkout=checkout)
    except SystemExit as error:
        return [f"{tree_path}: `{anchor}` — {error}"]
    lines = path.read_text().splitlines()
    span = _span_of(anchor, lines)
    if span is None:
        return [f"{tree_path}: `{anchor}` — {named} has no line {start}"]
    if snippet is not None and snippet not in span:
        where = f"{named}:{start}" if end is None else f"{named}:{start}-{end}"
        problems.append(
            f"{tree_path}: `{anchor}` — {where} does not contain {snippet!r} "
            f"(it starts {_line_of(path, start).strip()!r})"
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
    """`scripts/anchors.txt` as `{anchor: (path, line, snippet, end_snippet)}`.

    `-` in the snippet column means **no claim about the source text** — the
    entry records only that the line exists. That is a weaker guarantee than the
    rest of this tool and it is spelled as a value rather than left as an empty
    field, because `"" in line` is vacuously true and would make an unstated
    entry indistinguishable from a satisfied one.
    """
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
            None if snippet == "-" else snippet,
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
            "\t".join(
                [anchor, named, str(line), end, snippet or "-", end_snippet or "-"]
            )
        )
    rows.sort(key=lambda row: (row.split("\t")[1], int(row.split("\t")[2])))
    path.write_text("\n".join(rows) + "\n")


def prune(tree=None, manifest=None, report=None):
    """Drop the rows the tree no longer cites, printing each one it drops.

    The repair for the one defect `--check` cannot see by construction: an entry
    is only ever looked up through a citation, so a row whose anchor moved is
    never compared and never fails. It prints each dropped row rather than the
    count alone, because the recorded snippet is the only copy of that claim —
    if the row was dropped by mistake, the terminal still has it.
    """
    manifest = MANIFEST if manifest is None else manifest
    tree = list(scan_tree() if tree is None else tree)
    report = print if report is None else report
    cited = set()
    for _path, text in tree:
        for match in ANCHOR_RE.finditer(text):
            cited.add(match.group(0).strip("`"))
    dropped = sorted(set(manifest) - cited)
    for anchor in dropped:
        named, line, snippet, _end_snippet = manifest.pop(anchor)
        report(f"dropped `{anchor}` — {named}:{line} {snippet or '-'}")
    report(f"pruned {len(dropped)} entr(ies) cited nowhere in the tree")
    return manifest


def seed(checkout=None, tree=None, manifest=None, report=None):
    """Fill missing entries from the prose's own quotation of the code.

    **The snippet is the claim and the number is whatever the tree already
    says**, so a seeded entry whose quotation is not inside the cited span fails
    `--check` — which is the point. Seeding decides *what each anchor claims*; it
    does not decide whether the claim is true, and it must not, because a snippet
    read off the cited line would agree with it by construction and detect
    nothing.

    Where the prose quotes nothing selective near the anchor, the entry records
    `-`: no claim about the source text, only that the line exists. Those are
    counted by `--check` as `unquoted` rather than passed off as verified.

    **Selective is the load-bearing word, and it was measured.** The first draft
    of this function took the *nearest* backticked span found anywhere in the
    file, and produced 430 failures over 544 occurrences — a list nobody can
    read, and a manifest of claims the prose never made. What the prose quotes
    beside an anchor is usually its own shorthand — the filter's name, `None`,
    `try`, `SafeString` — and those are not claims about a line. Requiring the
    span to occur on exactly **one** line of the named file removes the shorthand
    (it recurs by construction) and leaves 107 rows, which is a list a person can
    read and which contains the defects the tool exists for.
    """
    checkout = CHECKOUT if checkout is None else checkout
    manifest = {} if manifest is None else manifest
    tree = list(scan_tree() if tree is None else tree)
    report = print if report is None else report

    quoted = unquoted = 0
    for path, text in tree:
        for match in ANCHOR_RE.finditer(text):
            anchor = match.group(0).strip("`")
            if anchor in manifest:
                continue
            named, start = match.group(1), int(match.group(2))
            path_for_named = _seed_path(named, checkout)
            snippet = _seed_snippet(text, match, path_for_named, checkout)
            manifest[anchor] = (path_for_named or named, start, snippet, None)
            if snippet is None:
                unquoted += 1
            else:
                quoted += 1
    report(f"seeded {quoted} quoted and {unquoted} unquoted entr(ies)")
    return manifest


def _seed_path(named, checkout):
    """The path to record for `named`, or `None` if it cannot be decided.

    A bare basename that is ambiguous is resolved through `BASENAMES`, which is
    a decision about *our* prose (`base.py` in a template-engine port means
    `django/template/base.py`) and not a guess about Django. Anything else is
    recorded as the path `resolve` found, so the manifest carries an explicit
    path rather than an ambiguity the tool would have to re-decide.
    """
    candidate = BASENAMES.get(named, named)
    try:
        path = resolve(candidate, checkout=checkout)
    except SystemExit:
        return None
    for base in (checkout, ROOT):
        if path.is_relative_to(base):
            return path.relative_to(base).as_posix()
    return None


def _seed_snippet(text, match, path, checkout):
    """The nearest quoted span the named file uses on exactly one line.

    Three rules, and each was measured rather than assumed — the first two
    drafts of this function produced 430 and then 311 failures, lists nobody
    reads.

    **Nearest, on the anchor's own line first.** Our prose convention is
    `` `code` (`file.py:N`) `` — the quotation and its citation on one line —
    so a span on another line belongs to another sentence. The first draft
    sorted by distance computed across two coordinate systems (a position
    inside the window against a position in the whole file), which sorted by
    *position in the window* and so took the first backtick rather than the
    nearest one; that alone was most of the 430.

    **Unique in the named file.** This is what separates a citation from a
    name. `None`, `def`, `SafeString`, `try` are words the prose repeats and
    lines of the file repeat; a span the file uses once is the kind of thing a
    claim is about. Requiring it also drops Rust quoted in a Rust doc comment
    (`Value::Str`) and our own syntax (`{% partial %}`), neither of which is a
    claim about Django at all.

    **The end column is left `-`**, not read off the end line: that is the
    circular direction this function exists to avoid, and an author who wants
    the far boundary pinned by text writes it.
    """
    if path is None:
        return None
    lines = resolve(path, checkout=checkout).read_text().splitlines()
    start = max(0, match.start() - TRIAGE_WINDOW)
    window = text[start : match.end() + TRIAGE_WINDOW]
    anchor_line = text.count("\n", 0, match.start())
    candidates = []
    for found in _SPAN_RE.finditer(window):
        span = found.group(1)
        if len(span) < 3 or "\n" in span:
            continue
        if ANCHOR_RE.fullmatch(f"`{span}`") or SHORTHAND_RE.fullmatch(f"`{span}`"):
            continue
        position = start + found.start()
        candidates.append(
            (0 if text.count("\n", 0, position) == anchor_line else 1,
             abs(position - match.start()),
             span)
        )
    for _, _, span in sorted(candidates):
        if sum(1 for line in lines if span in line) == 1:
            return span
    return None


def _base_of(named, checkout):
    """Which of the two bases a row's path lives in: `"django"` or `"crates"`.

    By the *resolved* path, not by the spelling: `.django/django/utils/text.py`
    and `django/utils/text.py` are the same row's home, and `_impl/parser.py`
    resolves under `OWN_PACKAGE`, which is ours. A path under neither base would
    have failed `resolve` already.
    """
    path = resolve(named, checkout=checkout)
    return "django" if path.is_relative_to(checkout) else "crates"


def update(manifest=None, checkout=None, report=None, base="?"):
    """Move the entries whose claim is *outside* their cited span, and no others.

    The second half of "the snippet is the claim and the number is derived from
    it" — but the derivation is only meaningful where the claim and the number
    disagree, and that is what `_span_of` decides, shared with `--check`.

    **The unconditional version of this was a mutation, not a repair**, and it is
    the one defect in this tool the reviewer found by reading rather than by
    running: an entry whose snippet sits inside its span but not on its first
    line is *green* — `_check_entry` accepts the snippet anywhere in the span —
    and rewriting its number to `hits[0]` moved the manifest off the tree's
    number, failing `--check` on the entry it had just "repaired". Now it moves
    only a claim that is outside its span, which is the one case where the number
    is genuinely wrong.

    **`base` decides which tree may move, and the bare form writes nothing.**
    `--update=django` and `--update=crates` write; plain `--update` reports what
    each would move and saves neither. The two are one command because the same
    message means opposite things: a **Django** row off its span means "the pin
    moved or the citation was wrong", which is an investigation, while a
    **crates** row off its span means "our file changed under the citation",
    which is routine. A command that applied the routine repair to the first
    case would silently ratify a wrong claim about Django, so there is no bare
    form that touches a number in either tree.

    An entry whose snippet is `-` records no claim, so there is nothing to derive
    a number from and it is skipped. A move is never a silent success either: the
    entry now disagrees with the tree's number, so `--check` names the citation
    whose number is wrong — which is the repair this command cannot make, because
    the number lives in the prose, not in the manifest.
    """
    checkout = CHECKOUT if checkout is None else checkout
    manifest = MANIFEST if manifest is None else manifest
    report = print if report is None else report
    dry = base in (None, "?")
    updated = dict(manifest)
    moves = {"django": [], "crates": []}
    for anchor, (named, line, snippet, end_snippet) in manifest.items():
        if snippet is None:
            continue
        which = _base_of(named, checkout)
        if not dry and which != base:
            continue
        path = resolve(named, checkout=checkout)
        lines = path.read_text().splitlines()
        span = _span_of(anchor, lines)
        if span is not None and snippet in span:
            continue
        # The whole file, deliberately, and not a window around `line`. Of the
        # drifts this migration measured, one moved *backward* — `tags/mod.rs`
        # 182 → 180, lines deleted above the target — so "scan near the cited
        # line and widen only on a miss" would have been written as a forward
        # scan by anyone who had seen only the others, and would lose it
        # silently. A forward-only window is the optimisation this line is
        # shaped to prevent.
        hits = [i for i, text in enumerate(lines, start=1) if snippet in text]
        if not hits:
            report(f"`{anchor}`: {snippet!r} is not in {named} at any line")
            continue
        if len(hits) > 1:
            report(f"`{anchor}`: {snippet!r} is at {hits} in {named}; pick one by hand")
            continue
        if hits[0] != line:
            moves[which].append((anchor, line, hits[0]))
            if not dry:
                report(f"`{anchor}`: {line} → {hits[0]} (outside the cited span)")
                updated[anchor] = (named, hits[0], snippet, end_snippet)
    if dry:
        for label, note in (
            ("django", "the pin moved or the citation was wrong — investigate"),
            ("crates", "our file changed under the citation — routine"),
        ):
            report(f"{label}: {len(moves[label])} row(s) whose claim is outside its span ({note})")
            for anchor, was, now in moves[label]:
                report(f"  `{anchor}`: {was} -> {now}")
        report("nothing written; give a base, --update=django or --update=crates")
        return updated
    report(f"{len(moves[base])} entr(ies) moved; their spans now disagree with the cited number")
    return updated


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
    # M12's two, and the first rows here whose basename has a *near* neighbour
    # under `.django/` rather than only far ones: `cache.py` matches five files
    # (`views/decorators/cache.py`, `utils/cache.py`, `middleware/cache.py`,
    # `contrib/sessions/backends/cache.py`) and `static.py` two. Ours is the
    # templatetags one in both cases, because that is the module the citation is
    # about — `cache.rs` and `static.rs` say so in their first line — and the
    # other four are reachable from no template tag at all.
    "cache.py": "django/templatetags/cache.py",
    "static.py": "django/templatetags/static.py",
}

MANIFEST = load_manifest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="gate: exit 1 on drift")
    group.add_argument("--triage", action="store_true", help="print the suspicion list")
    group.add_argument(
        "--update",
        nargs="?",
        const="?",
        default=None,
        metavar="BASE",
        help="derive cited numbers for one base: --update=django or --update=crates; "
        "bare --update reports both and writes neither",
    )
    group.add_argument(
        "--seed", action="store_true", help="fill missing entries from the prose"
    )
    group.add_argument(
        "--prune", action="store_true", help="drop manifest rows nothing cites"
    )
    parser.add_argument(
        "--frozen",
        action="store_true",
        help="with --check, also fail on a shorthand anchor naming no file",
    )
    args = parser.parse_args(argv)

    if args.triage:
        triage()
    elif args.update is not None:
        # The bare form is the dry run: it prints what each base would move and
        # saves nothing, so a routine repair can never be applied to a Django row
        # by accident.
        updated = update(base=args.update)
        if args.update != "?":
            save_manifest(updated)
    elif args.seed:
        save_manifest(seed(manifest=MANIFEST))
    elif args.prune:
        save_manifest(prune(manifest=MANIFEST))
    else:
        check(frozen=args.frozen)


if __name__ == "__main__":
    main()
