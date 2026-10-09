"""The checker's own tests: each drives it with the defect it exists for.

A checker that has never been *seen* to fail is the defect it exists to catch,
one level up, so none of these is a green run over the tree. Each plants the
thing the tool is for and requires the tool to say so.

`check_python_anchors` is `scripts/check_python_anchors.py` imported by path,
because `scripts/` is not a package and is not on `sys.path`.
"""

import importlib.util
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
        """
        key = anchor("base.py", "669-670")
        manifest = {
            key: ("django/template/base.py", 669, "self.filters.update(lib.filters)", None)
        }
        seen = []
        updated = anchor_manifest.update(
            manifest=manifest, checkout=DJANGO_CHECKOUT, report=seen.append
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
            manifest=manifest, checkout=DJANGO_CHECKOUT, report=seen.append
        )
        self.assertEqual(updated[key][1], 670, "moved to where the claim actually is")
        self.assertIn("→", " ".join(seen), "and the move is reported")
        with self.assertRaises(SystemExit):
            anchor_manifest.check(
                checkout=DJANGO_CHECKOUT,
                tree=self.tree_with(cited("base.py", "660-668")),
                manifest=updated,
            )

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
        """`OUR_OWN` shadows: it makes a bare `name.py:N` resolve to the repo root
        instead of into `.django/`, so the day Django ships a file with one of
        those names the guard resolves it to *ours* and goes quietly wrong in the
        one direction it exists to prevent. Measured today: zero hits for all
        five, `parser.py` being the plausible one. This assertion is what notices
        the bump, and it is green until then."""
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
        finding on _impl/parser.py:716 carried the wrong number
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
