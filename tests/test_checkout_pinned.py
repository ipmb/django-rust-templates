"""§2:33-34 — "Django's checkout is never modified; the suite is run unmodified".

Measured in `.review/m15-recon.md` §3.2: `.django/` is a live git clone at
`django.lock`'s sha with an empty `git status --porcelain`, nothing asserts
either, and `fetch_django.sh:21-24` is the only thing that reads the lock it
writes. The one partial protection is incidental — `scripts/anchors.txt:856-857`
snippet-pins two of the six skipped methods' lines, which fails if the method
moves and not if its `@unittest.skipUnless` decorator is deleted.

The controls here are fabricated lock/porcelain text, so the gate's conditions
are exercised without dirtying a checkout that is pinned.
"""

import unittest
from pathlib import Path

from scripts.assert_checkout_pinned import dirt, inspect_checkout, lock_values, refusals

LOCK = "tag=6.0.9\nsha=ea10f2ee50995ae9202dd18aa1a9206a8dab7b1a\n"
HEAD = "ea10f2ee50995ae9202dd18aa1a9206a8dab7b1a"


class LockTests(unittest.TestCase):
    def test_the_lock_reads_as_a_pair(self):
        self.assertEqual(lock_values(LOCK)["tag"], "6.0.9")
        self.assertEqual(lock_values(LOCK)["sha"], HEAD)

    def test_an_empty_porcelain_is_no_dirt(self):
        self.assertEqual(dirt(""), [])

    def test_an_untracked_file_is_dirt(self):
        """`--porcelain` without `-uno`: a planted file edits the checkout too."""
        self.assertTrue(dirt("?? conftest.py\n"))


class RefusalTests(unittest.TestCase):
    def test_a_clean_pinned_checkout_is_accepted(self):
        self.assertEqual(refusals(lock_values(LOCK), HEAD, ""), [])

    def test_a_lock_whose_sha_is_not_the_checkouts_is_refused(self):
        wrong = lock_values(LOCK.replace(HEAD, "0" * 40))
        self.assertTrue(any("lock" in r for r in refusals(wrong, HEAD, "")))

    def test_a_modified_file_is_refused(self):
        self.assertTrue(refusals(lock_values(LOCK), HEAD, " M django/template/base.py\n"))

    def test_an_untracked_file_is_refused(self):
        self.assertTrue(refusals(lock_values(LOCK), HEAD, "?? conftest.py\n"))


class LiveCheckoutTests(unittest.TestCase):
    """The subject, as it is on this machine — measured, not assumed."""

    def test_the_pinned_checkout_is_clean_and_at_the_recorded_sha(self):
        self.assertEqual(inspect_checkout(), [])

    def test_a_checkout_without_git_is_refused(self):
        """A tarball or a copied tree: the check must say so, not traceback.

        The control is over the same function and the same guard as the row
        above — a path whose `.git` does not exist — rather than a fabricated
        string handed to `refusals`, so it exercises the `git status` route a
        tarball would actually take.
        """
        refusals_out = inspect_checkout(checkout=Path("/nonexistent"))
        self.assertTrue(any("git" in r for r in refusals_out), refusals_out)
