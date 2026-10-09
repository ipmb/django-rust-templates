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

        `node.token = token` is at `base.py:615`; the entry below cites `616`,
        which is the off-by-one the reviewer reported for this exact anchor and
        which two rounds of correction got wrong in both directions. The anchor
        in the tree and the anchor in the manifest are the **same string** — a
        test whose two spellings differ would pass on the missing-entry path
        instead, which is how a test for one defect ends up covering another.

        (The plan first wrote this key as `"base.py:node_token"`, a label the
        tree never contains. Measured: that version exits on "no manifest entry
        for `base.py:616`" and never reaches the snippet comparison, so it
        passes whether or not the checker can compare a snippet at all.)
        """
        anchor_manifest.MANIFEST = {
            "base.py:616": ("django/template/base.py", 616, "node.token = token", None)
        }
        with self.assertRaises(SystemExit):
            anchor_manifest.check(
                checkout=DJANGO_CHECKOUT, tree=self.tree_with("`base.py:616`")
            )

    def test_the_same_anchor_with_the_right_number_passes(self):
        # The control. Without it the test above passes on any anchor this tool
        # refuses for any reason, which would make the guard's own test a
        # no-op the day `check` starts rejecting everything.
        anchor_manifest.MANIFEST = {
            "base.py:615": ("django/template/base.py", 615, "node.token = token", None)
        }
        anchor_manifest.check(checkout=DJANGO_CHECKOUT, tree=self.tree_with("`base.py:615`"))

    def test_an_anchor_in_the_tree_with_no_manifest_entry_fails(self):
        """This is the half that catches drift introduced *after* this task: a
        fresh `` `defaulttags.py:123` `` written into a comment fails here, which
        is what makes the check a gate rather than a snapshot of today's tree."""
        anchor_manifest.MANIFEST = {}
        with self.assertRaises(SystemExit):
            anchor_manifest.check(
                checkout=DJANGO_CHECKOUT, tree=self.tree_with("`defaulttags.py:99999`")
            )

    def test_a_basename_that_is_not_unique_needs_an_explicit_path(self):
        """38 files in the checkout are named `base.py`, 89 `formats.py`, 210
        `tests.py`. A bare basename for one of those is ambiguous, and ambiguity
        is an error rather than a guess — the guess is the defect."""
        with self.assertRaises(SystemExit):
            anchor_manifest.resolve("base.py", manifest={})

    def test_the_two_anchors_that_were_wrong_twice_are_pinned(self):
        """`base.py:518-519` and `base.py:522-523` were reported wrong, repaired
        to `517-518`/`521-522`, and were correct all along. Named here so the
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
        finding on `_impl/parser.py:716` carried the wrong number
        `smartif.py:166-172` inside it, so scanning here fails the build on the
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
