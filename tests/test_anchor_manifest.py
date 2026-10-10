"""The checker's own tests: each drives it with the defect it exists for.

A checker that has never been *seen* to fail is the defect it exists to catch,
one level up, so none of these is a green run over the tree. Each plants the
thing the tool is for and requires the tool to say so.

`check_python_anchors` is `scripts/check_python_anchors.py` imported by path,
because `scripts/` is not a package and is not on `sys.path`.
"""

import importlib.util
import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
SCRIPTS = PROJECT_ROOT / "scripts"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


anchor_manifest = _load("check_python_anchors", "check_python_anchors.py")


def anchor(name, number):
    """`name:N` — a citation as the *scanner* reports it, without backticks.

    This is the manifest key form: `check` looks up `match.group(0).strip("`")`.

    **The backtick is what makes an anchor a citation.** `check_python_anchors`
    reads text, so it cannot tell a citation from a quotation or a fixture, and
    this file needs both: fixtures that *are* anchors on purpose, and mentions of
    wrong numbers that are not claims about Django. So fixtures are built by
    these two helpers, and mentions are written unbackticked — one rule, applied
    two ways, instead of a per-file exemption, which is how a guard acquires
    holes.

    Measured when the rule was set: this file held ten anchor-shaped literals,
    and one fixture points past the end of its file, so **no** manifest entry
    could have made it pass. That is what a literal can do to a checker that only
    reads text.
    """
    return "%s:%s" % (name, number)


def cited(name, number):
    """`` `name:N` `` — the same citation as the *tree* spells it."""
    return "`%s`" % anchor(name, number)


def shorthand(number):
    """`` `:N` `` — a citation of whichever file the prose last named.

    The third spelling, and the one the migration is unfinished on: a bare
    line number whose referent is the nearest preceding citation. Built here
    rather than written literally for the same reason as the other two — a
    backticked `:N` in this file's own text is itself a shorthand, and the
    frozen gate fails on those. So a literal here would be a guard that
    cannot pass its own gate.
    """
    return "`:%d`" % number


class AnchorManifestTests(unittest.TestCase):
    def setUp(self):
        # Every test that calls `check` restores the real manifest afterwards,
        # so an earlier test cannot make a later one green by leaving a
        # hand-written table behind.
        self.addCleanup(setattr, anchor_manifest, "MANIFEST", anchor_manifest.MANIFEST)
        self.addCleanup(setattr, anchor_manifest, "OUR_OWN", anchor_manifest.OUR_OWN)

    def tree_with(self, *lines):
        """A one-file tree, as `check`'s `tree` argument wants it."""
        return [("tests/example.py", "\n".join(lines) + "\n")]

    def test_a_cited_line_that_does_not_contain_its_snippet_fails(self):
        """The whole value is failing on a *wrong number*, so this moves the
        number rather than trusting a clean sweep over the tree.

        `node.token = token` is at base.py:615; the entry below cites 616, which
        is the off-by-one the reviewer reported for this exact anchor and which
        two rounds of correction got wrong in both directions. The anchor in the
        tree and the anchor in the manifest are the **same string** — a test
        whose two spellings differ would pass on the missing-entry path instead,
        which is how a test for one defect ends up covering another.

        (The plan first wrote this key as `"base.py:node_token"`, a label the
        tree never contains. Measured: that version exits on "no manifest entry"
        and never reaches the snippet comparison, so it passes whether or not the
        checker can compare a snippet at all.)
        """
        anchor_manifest.MANIFEST = {
            anchor("base.py", 616): ("django/template/base.py", 616, "node.token = token", None)
        }
        with self.assertRaises(SystemExit):
            anchor_manifest.check(
                checkout=DJANGO_CHECKOUT, tree=self.tree_with(cited("base.py", 616))
            )

    def test_the_same_anchor_with_the_right_number_passes(self):
        # The control. Without it the test above passes on any anchor this tool
        # refuses for any reason, which would make the guard's own test a
        # no-op the day `check` starts rejecting everything.
        anchor_manifest.MANIFEST = {
            anchor("base.py", 615): ("django/template/base.py", 615, "node.token = token", None)
        }
        anchor_manifest.check(
            checkout=DJANGO_CHECKOUT, tree=self.tree_with(cited("base.py", 615))
        )

    def test_an_anchor_in_the_tree_with_no_manifest_entry_fails(self):
        """This is the half that catches drift introduced *after* this task: a
        fresh defaulttags.py:123 written into a comment fails here, which is what
        makes the check a gate rather than a snapshot of today's tree.

        The fixture is deliberately past the end of `defaulttags.py`, so it is
        the one anchor here that **no** manifest entry could make pass — which is
        why it is built by `anchor()` rather than written out.
        """
        anchor_manifest.MANIFEST = {}
        with self.assertRaises(SystemExit):
            anchor_manifest.check(
                checkout=DJANGO_CHECKOUT,
                tree=self.tree_with(cited("defaulttags.py", 99999)),
            )

    def test_update_leaves_an_entry_whose_claim_is_already_inside_its_span(self):
        """The reviewer's finding, pinned: `--check` accepts a snippet anywhere in
        the span (`_check_entry`), so a green entry has nothing to derive and
        `--update` must not move it.

        Measured on the committed manifest: the unconditional version would have
        moved **63** entries, of which **61 were correct anchors** and all 61 in
        exactly this state — a snippet inside the span but not on its first line.
        The row here is real: base.py:669-670 quotes
        `self.filters.update(lib.filters)`, which is on 670 — written without
        backticks, because a backtick is what makes an anchor a claim about Django
        and this is a mention of one.

        `base="django"` is named because the bare form is a dry run: a command
        that applies the routine repair to a Django row by accident would ratify
        a wrong claim about Django, so `--update` with no base reports and writes
        nothing (see `test_the_bare_update_reports_both_bases_and_writes_neither`).
        """
        key = anchor("base.py", "669-670")
        manifest = {
            key: ("django/template/base.py", 669, "self.filters.update(lib.filters)", None)
        }
        seen = []
        updated = anchor_manifest.update(
            manifest=manifest,
            checkout=DJANGO_CHECKOUT,
            report=seen.append,
            base="django",
        )
        self.assertEqual(updated[key][1], 669, "an in-span claim is left where it is")
        self.assertNotIn("→", " ".join(seen), "and it is not reported as moved")

    def test_update_moves_only_a_claim_outside_its_span_and_check_then_names_it(self):
        """The other direction, with the property that makes the move safe: a
        moved entry is never a silent green. Its number now disagrees with the
        tree's, so `--check` prints both numbers and the reader fixes the
        *citation* — which this tool cannot write, because the number lives in the
        prose. Without that, `--update` would be the mutation the reviewer found:
        a command that repairs by breaking."""
        key = anchor("base.py", "660-668")
        manifest = {
            key: ("django/template/base.py", 660, "self.filters.update(lib.filters)", None)
        }
        seen = []
        updated = anchor_manifest.update(
            manifest=manifest,
            checkout=DJANGO_CHECKOUT,
            report=seen.append,
            base="django",
        )
        self.assertEqual(updated[key][1], 670, "moved to where the claim actually is")
        self.assertIn("→", " ".join(seen), "and the move is reported")
        with self.assertRaises(SystemExit):
            anchor_manifest.check(
                checkout=DJANGO_CHECKOUT,
                tree=self.tree_with(cited("base.py", "660-668")),
                manifest=updated,
            )

    def test_the_bare_update_reports_both_bases_and_writes_neither(self):
        """The two bases mean opposite things when a row is off its span, so the
        bare form cannot act on either: a **django** row off its span means the
        pin moved or the citation was wrong — an investigation — while a
        **crates** row off its span means our file changed under the citation,
        which is routine. One command applying the routine repair would silently
        ratify a wrong claim about Django, so the bare form reports the two groups
        and touches neither.

        The count is printed per base rather than as one total because the two are
        not the same finding: on the live manifest both are **0** — measured right
        after the second base's rows were minted, 542 of 542 cited — and a single
        total would read as "the manifest is settled" the moment the routine half
        was empty and the Django half was not.
        """
        django = anchor("base.py", "660-668")
        crates = anchor("center.rs", "1")
        manifest = {
            django: (
                "django/template/base.py",
                660,
                "self.filters.update(lib.filters)",
                None,
            ),
            # `arg.unwrap_or(Value::None)` is on center.rs:86; the row cites 1, so
            # its claim is outside its span — the crates case.
            crates: (
                "crates/template-core/src/filters/center.rs",
                1,
                "arg.unwrap_or(Value::None)",
                None,
            ),
        }
        seen = []
        updated = anchor_manifest.update(
            manifest=manifest, checkout=DJANGO_CHECKOUT, report=seen.append
        )
        self.assertEqual(updated, manifest, "the bare form writes nothing")
        report = " ".join(seen)
        self.assertIn("django: 1 row(s)", report)
        self.assertIn("crates: 1 row(s)", report)
        self.assertIn("nothing written", report)

    def test_a_row_the_tree_stops_citing_is_counted_and_fatal_only_when_frozen(self):
        """The one defect `check` cannot see by construction: it walks the tree
        and looks *citations* up, so a stale row is never asked for, never
        compared, and never fails.

        Measured on the real manifest when this was written: two such rows, left
        behind by a re-pointing sweep, and one of them recorded a snippet that is
        not on its line — a wrong claim sitting in the manifest that no `--check`
        could reach. The stale row here is built the same way, with a snippet that
        is deliberately not on line 616, to pin that it is the *absence of a
        citation* being reported and not the snippet.

        Both halves are asserted, because "report it" and "fail on it" are
        separate decisions and only the second is a gate.
        """
        live = anchor("base.py", 615)
        stale = anchor("base.py", 616)
        anchor_manifest.MANIFEST = {
            live: ("django/template/base.py", 615, "node.token = token", None),
            stale: ("django/template/base.py", 616, "not on that line at all", None),
        }
        tree = self.tree_with(cited("base.py", 615))
        seen = []
        anchor_manifest.check(
            checkout=DJANGO_CHECKOUT, tree=tree, report=seen.append
        )
        self.assertIn("dead", " ".join(seen), "the stale row must be visible")
        with self.assertRaises(SystemExit):
            anchor_manifest.check(checkout=DJANGO_CHECKOUT, tree=tree, frozen=True)

    def test_prune_drops_the_stale_row_and_prints_the_claim_it_dropped(self):
        """`--prune` is the repair, and it prints each dropped row rather than a
        count: the snippet column is the only copy of that claim, so a row
        dropped by mistake is still recoverable from the terminal."""
        live = anchor("base.py", 615)
        stale = anchor("base.py", 616)
        manifest = {
            live: ("django/template/base.py", 615, "node.token = token", None),
            stale: ("django/template/base.py", 616, "not on that line at all", None),
        }
        seen = []
        pruned = anchor_manifest.prune(
            tree=self.tree_with(cited("base.py", 615)),
            manifest=manifest,
            report=seen.append,
        )
        self.assertEqual(sorted(pruned), [live], "only the cited row survives")
        self.assertIn(
            "not on that line at all",
            " ".join(seen),
            "the dropped snippet is printed, so a mis-prune is recoverable",
        )

    def test_a_basename_that_is_not_unique_needs_an_explicit_path(self):
        """38 files in the checkout are named `base.py`, 89 `formats.py`, 210
        `tests.py`. A bare basename for one of those is ambiguous, and ambiguity
        is an error rather than a guess — the guess is the defect."""
        with self.assertRaises(SystemExit):
            anchor_manifest.resolve("base.py", manifest={})

    def test_a_rust_basename_resolves_under_crates(self):
        """The second base's rule, and the one that makes a bare center.rs:86 a
        citation at all: `.rs` names are searched under `crates/`, **never** in
        the checkout, because nothing under `.django/` is Rust. The direction that
        matters is the failure one — a checkout search would resolve an `.rs`
        name to nothing and the whole second base would report "no such file"
        rather than drift.
        """
        self.assertEqual(
            anchor_manifest.resolve("center.rs"),
            anchor_manifest.CRATES / "template-core" / "src" / "filters" / "center.rs",
        )

    def test_a_rust_basename_that_is_not_unique_is_refused(self):
        """`host.rs` names two files, `filters/host.rs` and `parser/host.rs`, and
        the same rule as `base.py` applies: ambiguity is an error, not a guess.

        Measured when the second base opened: 26 `.rs` basenames are cited and
        this is the only one that is not unique, which is why the manifest's
        `filters/host.rs` rows carry the parent directory and resolve.
        """
        with self.assertRaises(SystemExit):
            anchor_manifest.resolve("host.rs")
        self.assertEqual(
            anchor_manifest.resolve("filters/host.rs"),
            anchor_manifest.CRATES / "template-core" / "src" / "filters" / "host.rs",
        )

    def test_the_scanner_reads_the_four_shapes_the_widening_added(self):
        """Three characters are the whole widening: `-` for the two crate
        directories, `.` for the `.django/`-prefixed citations, and `(?:py|rs)`
        for Rust. Measured when it was set: 952 occurrences against the
        committed scanner's 914, 26 distinct anchors new — 19 `.rs` and 7
        `.django/`-prefixed, every one a real citation that had been read as
        prose, and every one now carrying a row. The `.django/`-prefixed
        spelling is the one that could not be checked at all, because the
        directory it names is the checkout itself.
        """
        for spelling in (
            "center.rs:86",
            "crates/template-core/src/filters/cut.rs:34",
            "template-py/src/filters.rs:1587-1589",
            ".django/django/utils/text.py:313-344",
        ):
            with self.subTest(spelling=spelling):
                self.assertIsNotNone(
                    anchor_manifest.ANCHOR_RE.fullmatch("`%s`" % spelling),
                    "the widened pattern must read this as a citation",
                )
        self.assertEqual(
            anchor_manifest.resolve(".django/django/utils/text.py"),
            DJANGO_CHECKOUT / "django" / "utils" / "text.py",
        )

    def test_frozen_fails_on_a_bare_shorthand_inside_a_rust_file(self):
        """The shorthand half of the second base, tied to the widening by the
        **occurrence count** rather than by the failure.

        Pre-widening the pattern was `.py`-only, so this fixture's full anchor was
        not an anchor: the non-frozen run reported 0 occurrences while the
        shorthand was still collected, and the frozen failure would have looked
        identical for the wrong reason. Asserting the count asserts that the
        fixture is being read by the wider scanner.
        """
        anchor_manifest.MANIFEST = {
            anchor("center.rs", 86): (
                "crates/template-core/src/filters/center.rs",
                86,
                "arg.unwrap_or(Value::None)",
                None,
            )
        }
        tree = [
            (
                "crates/template-core/src/filters/center.rs",
                "// %s, and the same file again as %s.\n"
                % (cited("center.rs", 86), shorthand(90)),
            )
        ]
        seen = []
        anchor_manifest.check(checkout=DJANGO_CHECKOUT, tree=tree, report=seen.append)
        self.assertIn("1 anchor occurrence(s) checked", " ".join(seen))
        self.assertIn("unmigrated: 1 shorthand", " ".join(seen))
        with self.assertRaises(SystemExit):
            anchor_manifest.check(
                checkout=DJANGO_CHECKOUT, tree=tree, frozen=True, report=seen.append
            )

    def test_the_gate_asks_the_checker_for_frozen(self):
        """`--frozen` is only a gate if something passes it, and the flip is one
        line of a shell script — a claim no test reads, which is the defect this
        whole tool exists for. So the invocation is asserted here, as the text it
        is.

        Asserted as one argument list rather than as the flag occurring somewhere
        in the file: a script that passed `--frozen` to `--triage` would satisfy
        the weaker reading.
        """
        script = (PROJECT_ROOT / "scripts" / "check.sh").read_text()
        invocations = [
            line
            for line in script.splitlines()
            if "check_python_anchors.py" in line and not line.lstrip().startswith("#")
        ]
        self.assertEqual(len(invocations), 1, invocations)
        self.assertIn("--check --frozen", invocations[0])

    def test_the_two_anchors_that_were_wrong_twice_are_pinned(self):
        """base.py:518-519 and base.py:522-523 were reported wrong, repaired to
        517-518/521-522, and were correct all along. Named here so the
        regression is a row rather than a memory, and asserted against the
        checkout rather than against the manifest the tool would consult."""
        for anchor, snippet in (
            ("base.py:518-519", "if builtins is None:"),
            ("base.py:522-523", "for builtin in builtins:"),
        ):
            with self.subTest(anchor=anchor):
                line = int(anchor.split(":")[1].split("-")[0])
                text = (DJANGO_CHECKOUT / "django/template/base.py").read_text().splitlines()
                self.assertIn(snippet, text[line - 1] + text[line])

    def test_no_our_own_basename_exists_in_the_checkout(self):
        """`OUR_OWN` shadows: it stops a bare `name.py:N` before the checkout's
        search rather than inside it, so a name of ours cannot come back as
        Django's — the quiet wrong answer being the one direction the guard
        exists to prevent. Measured today: zero hits for all
        four, `parser.py` being the plausible one. (`_shim.py` was the fifth
        until M18 deleted the shim; it left no successor.) This assertion is what
        notices the bump, and it is green until then."""
        shadows = {}
        for basename in anchor_manifest.OUR_OWN:
            hits = [
                p
                for p in DJANGO_CHECKOUT.rglob(basename)
                if p.is_file() and "__pycache__" not in p.parts
            ]
            if hits:
                shadows[basename] = [str(p.relative_to(DJANGO_CHECKOUT)) for p in hits]
        self.assertEqual(
            shadows,
            {},
            "a basename in OUR_OWN now exists under .django/, so bare anchors "
            "naming it will resolve to ours: give it an explicit path in the "
            "manifest and drop it from OUR_OWN",
        )
        # The control: this `rglob` demonstrably finds a file that is there, so
        # the empty `shadows` above is the checkout's answer and not a walk that
        # returned nothing. It is the same call with the same filter, over a
        # basename known to exist.
        self.assertTrue(
            [
                p
                for p in DJANGO_CHECKOUT.rglob("base.py")
                if p.is_file() and "__pycache__" not in p.parts
            ],
            "rglob found no base.py under .django/, so the walk above is vacuous",
        )

    def test_a_cited_dependency_version_is_the_one_the_lockfile_pins(self):
        """The one citation that named a file **not in this tree**, replaced by
        the claim that can be checked.

        pyo3-0.29.3/src/marker.rs:414 pointed into the vendored PyO3 source,
        which is under the cargo registry and not in the repo: no base resolves
        it, so no manifest row could have made it pass and `--check` would fail
        on it the moment `.rs` became an anchor — and if it were exempted, the
        number would drift with the dependency with nothing watching.

        The durable claim is the *version*, which `Cargo.lock` pins, so the
        prose keeps `pyo3-0.29.3/src/marker.rs` and drops the line, and this
        asserts the two spellings agree. A citation to an out-of-tree line is
        not a claim this tool can verify; a citation to a pinned version is.
        """
        lock = (PROJECT_ROOT / "Cargo.lock").read_text()
        match = re.search(r'name = "pyo3"\nversion = "([^"]+)"', lock)
        self.assertIsNotNone(match, "pyo3 is no longer named in Cargo.lock")
        prose = (PROJECT_ROOT / "crates/template-py/src/lib.rs").read_text()
        self.assertIn(
            f"pyo3-{match.group(1)}",
            prose,
            "the prose cites a pyo3 version that Cargo.lock does not pin",
        )

    def test_no_manifest_row_lacks_a_citation_in_the_tree(self):
        """The thirtieth dead row, gated rather than remembered.

        `--check` cannot see one by construction — it walks the tree's citations
        and looks each up, so a row nothing cites is never asked for — and
        `--prune` runs only when someone remembers to run it. Two rows were left
        behind by the previous sweep, one of them recording a snippet that is not
        on its line, i.e. a claim that was false and unreachable.

        Asserted over the live manifest and the live tree, like the `.review/`
        dated-name test above, because no fixture can state a property of the
        manifest that ships. A repair that re-points a citation must replace the
        entry in the same commit; this is what says so.
        """
        cited_in_tree = set()
        # The control: the walk the subtraction reads has a non-empty result, so
        # an all-empty reading is the tree's and not the walk's. `scan_tree` is a
        # generator — truth-testing it directly would pass on an empty tree, and
        # the row would then assert that nothing is missing from nothing.
        walked = list(anchor_manifest.scan_tree())
        self.assertTrue(
            walked,
            "scan_tree() walked no files, so the subtraction below is vacuous",
        )
        for _path, text in walked:
            for match in anchor_manifest.ANCHOR_RE.finditer(text):
                cited_in_tree.add(match.group(0).strip("`"))
        self.assertEqual(
            sorted(set(anchor_manifest.MANIFEST) - cited_in_tree),
            [],
            "a manifest row the tree no longer cites: run --prune, or replace it "
            "with the row for the anchor the prose now writes",
        )

    def test_an_unbackticked_reference_is_prose_and_anchor_builds_a_real_one(self):
        """The escape rule as a property of the scanner, not a convention somebody
        remembers to apply.

        This file needs anchor-shaped text that is *not* a claim about Django:
        the fixtures, and every mention of a wrong number. The rule is that a
        backtick delimits a citation and nothing else does, so a mention is
        written plainly and a fixture is built by `anchor()`. Both halves are
        asserted here, because either alone would pass on a broken helper.
        """
        self.assertTrue(
            anchor_manifest.ANCHOR_RE.fullmatch(cited("base.py", 615)),
            "anchor() must build something the scanner reads as an anchor",
        )
        self.assertIsNone(
            anchor_manifest.ANCHOR_RE.search("base.py:615"),
            "an unbackticked reference is prose and must not be scanned",
        )

    def test_this_file_holds_no_anchor_the_scanner_would_read(self):
        """The rule above, enforced over this whole file rather than argued for.

        Measured when it was set: ten anchor-shaped literals here, of which one
        (defaulttags.py:99999) points past the end of its file, so **no**
        manifest entry could ever have made it pass. A per-file exemption was the
        tempting repair and is how a guard acquires holes; this test is what
        keeps the rule true instead, and it reddens the moment someone writes a
        literal anchor into this file.
        """
        self.assertEqual(
            anchor_manifest.ANCHOR_RE.findall(Path(__file__).read_text()),
            [],
            "this file writes an anchor-shaped literal; use anchor() for a "
            "fixture or drop the backticks to quote one",
        )

    def test_the_plans_exclusion_of_the_dated_plans_is_a_rule_not_a_glob(self):
        # `docs/superpowers/plans/` carries most of the whole-tree occurrences,
        # and a dated plan records what was believed on a date. Rewriting its
        # citations makes it a record of what is true now and loses that
        # distinction, so the exclusion has to be visible where the roots are.
        self.assertNotIn("docs", anchor_manifest.TREE_ROOTS)
        self.assertIn("docs/superpowers/plans/", anchor_manifest.NOT_SCANNED_BECAUSE)

    def test_the_review_records_are_excluded_for_the_same_reason_and_one_more(self):
        """`NOT_SCANNED_BECAUSE`'s second entry, asserted separately because its
        justification is stronger than the plans' and would be lost if the two
        were collapsed into one assertion.

        Every file under `.review/` is dated by construction — measured: all 17
        are `round-N`, `mN-summary`, `mN-dispatch`, `mN-brief`, `mN-seams` or
        `mN-recon`, and no filename lacks a milestone or round. The extra reason
        is that a review record quotes our anchors *verbatim*: this round's
        finding on engine/parser.py:716 carried the wrong number
        smartif.py:166-172 inside it, so scanning here fails the build on the
        reviewer's accurate quotation of our own defect.
        """
        self.assertNotIn(".review", anchor_manifest.TREE_ROOTS)
        self.assertIn(".review/", anchor_manifest.NOT_SCANNED_BECAUSE)
        every = sorted(p.name for p in (PROJECT_ROOT / ".review").glob("*") if p.is_file())
        stray = [
            name
            for name in every
            if not (
                anchor_manifest._DATED_REVIEW_RE.search(name)
            )
        ]
        self.assertEqual(
            stray,
            [],
            "a file in .review/ is not dated by its name, so the exclusion may "
            "now be hiding a live document: give it a home outside .review/ or "
            "scan it",
        )


if __name__ == "__main__":
    unittest.main()
