"""The ownership table, checked against the tree it claims to partition.

Each row below is a direction the table can be wrong in. Together they are the
repair `.review/m10-recon.md` §8 asks for — "the rows and the meter become two
sets with an assertion between them, which is the thing that was missing in the
first place" — which is why they live in the milestone that noticed the problem
even though they are about every milestone after it.

The failures they exist for, both measured in the recon: `origin` sat in M10's
row although no `django/template/origin.py` exists at 6.0.9, and `autoreload`,
`context_processors` and `loader` sat in no row at all. A table with an
assertion under it cannot do either.
"""

import unittest

from django_rust_templates import _shim
from tests.milestone_ownership import (
    INTERPOSED,
    OWNERS,
    UNDECIDED,
    interposed,
    spec_milestones,
)

TARGET = "django.template"


def suffixes(names) -> set:
    """Dotted suffixes of full ``django.template.x`` names.

    `unshimmed()` answers in full dotted names because that is what a reader of
    the progress meter wants; the table is keyed by suffix because that is what
    `dotted_suffix()` produces from a file path, and comparing two spellings of
    the same name would be the drift this module exists to catch.
    """
    return {name[len(TARGET) + 1 :] for name in names}


class OwnershipTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _shim.install()

    def test_the_table_partitions_the_universe(self):
        """Both directions, and this is the row that makes the table binding.

        `django_modules()` is every name Django's template package defines — 26
        at 6.0.9, of which 23 are still Django's and 3 are ours. A name in the
        tree and in no bucket is a module no milestone has agreed to take, which
        is the failure §2 found three of. A name in a bucket that is not in the
        tree is a row about nothing — `origin`, as M10's row had it, and as this
        row would have caught.
        """
        self.assertEqual(
            set(OWNERS) | set(UNDECIDED) | interposed(),
            _shim.django_modules(),
        )

    def test_no_name_is_both_owned_and_taken(self):
        """A name in two buckets is a table that cannot be read as a partition.

        It is also the specific mistake this milestone makes possible: a task
        that interposes a module and forgets to move it out of `OWNERS` leaves
        the meter and the table disagreeing, and
        `test_the_meter_is_the_owners_plus_the_undecided` would then fail with
        the arithmetic rather than the reason.
        """
        self.assertEqual(set(OWNERS) & interposed(), set())
        self.assertEqual(set(UNDECIDED) & interposed(), set())
        self.assertEqual(set(OWNERS) & set(UNDECIDED), set())

    def test_the_meter_is_the_owners_plus_the_undecided(self):
        """The progress number and the table, as two sets with this between them.

        `unshimmed()` is the count every milestone is accepted on. If it can
        drift from the table, the table is decoration — and if the table can
        drift from it, the next reader plans against a schedule that is wrong.
        """
        expected = (set(OWNERS) | set(UNDECIDED)) - interposed()
        self.assertEqual(suffixes(_shim.unshimmed()), expected)

    def test_interposed_is_the_shadowing_table(self):
        """`INTERPOSED` adds the milestone attribution and no new membership.

        Which modules are ours is `_shim.SHADOWING`'s to state: it is derived
        from the files on disk (`shadowing_files()`) and compared against the
        guard's `DECLARED` by the rows in `test_interposition.py`. A second list
        of membership here would be the drift that comment warns against, so
        this row compares the two and `INTERPOSED` carries only *when*.
        """
        self.assertEqual(
            interposed(),
            {name for name in map(_shim.dotted_suffix, _shim.SHADOWING) if name},
        )

    def test_every_owner_is_a_milestone_the_spec_defines(self):
        """The labels are read from §10.3 rather than restated here.

        A second list of milestone names would agree with itself, so a typo
        (`M9` for `M13`) would be invisible. `spec_milestones()` parses the
        spec's own table, which makes the label a claim about that document.
        """
        defined = spec_milestones()
        self.assertGreaterEqual(len(defined), 15, sorted(defined))
        for name, owner in sorted(OWNERS.items()):
            with self.subTest(name=name):
                self.assertIn(owner, defined, f"{name} is owned by {owner!r}")
        for label in sorted(INTERPOSED):
            with self.subTest(label=label):
                self.assertIn(label, defined)

    def test_the_undecided_names_are_the_target_and_carry_their_reason(self):
        """§2's end state, as a set with a reason per name.

        The spec states the target as *zero* in two places (§10.1 and §10.3's
        M14 row), and §2 measured that no row reaches zero without interposing
        glue §6 forbids. So the end state M14 is held to is the `UNDECIDED` set,
        and this row pins which names are in it — the assertion is what makes
        "M14's by default" unavailable, and the reason field is what makes the
        set arguable rather than silent.
        `test_the_meter_is_the_owners_plus_the_undecided` is what gives it
        force: these are the only two names this table declines to own.

        The length check is a weak guard and is written as one: it refuses a
        placeholder, and the prose is reviewed rather than measured.
        """
        self.assertEqual(set(UNDECIDED), {"autoreload", "context_processors"})
        for name, reason in sorted(UNDECIDED.items()):
            with self.subTest(name=name):
                self.assertGreater(len(reason), 80, f"{name}'s reason is a stub")


if __name__ == "__main__":
    unittest.main()
