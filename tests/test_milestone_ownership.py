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
from django_rust_templates._shim import NAMESPACE_ROOTS
from tests.milestone_ownership import (
    INTERPOSED,
    NOT_THE_ENGINE,
    OWNERS,
    between,
    claimed,
    interposed,
    pending,
    spec_milestones,
    spec_not_the_engine,
)

TARGET = "django.template"
# The second population, the one M12 Task 1 gave the meter. Named rather than
# spelled: `NAMESPACE_ROOTS` is the shim's table of which scope lives where.
TEMPLATETAGS = "django.templatetags"
TEMPLATETAGS_ROOT = NAMESPACE_ROOTS[TEMPLATETAGS]


def suffixes(names, scope=TARGET) -> set:
    """Dotted suffixes of full ``<scope>.x`` names.

    `unshimmed()` answers in full dotted names because that is what a reader of
    the progress meter wants; the table is keyed by suffix because that is what
    `dotted_suffix()` produces from a file path, and comparing two spellings of
    the same name would be the drift this module exists to catch.
    """
    return {name[len(scope) + 1 :] for name in names}


def table_spelling(names, scope) -> set:
    """A scope's suffixes, in the ``_impl``-relative spelling the tables use.

    The two scopes differ and that is the whole of it: `django.template`'s root
    is `""`, so `base` is already the key, while `django.templatetags`'s files
    live under `_impl/templatetags/` and its keys carry that prefix — the same
    relation `dotted_suffix` inverts from a path. Derived from
    `NAMESPACE_ROOTS` rather than a literal, so the prefix cannot drift from the
    root the shim actually serves.
    """
    root = NAMESPACE_ROOTS[scope]
    return {f"{root}.{name}" if root else name for name in names}


def the_universe() -> set:
    """Every name the table partitions, in the tables' spelling — both scopes."""
    return table_spelling(_shim.django_modules(TARGET), TARGET) | table_spelling(
        _shim.django_modules(TEMPLATETAGS), TEMPLATETAGS
    )


def the_meter() -> set:
    """``unshimmed()`` over both scopes, in the tables' spelling.

    Both, because the table partitions both: M12 made the meter a count over two
    populations and a row that read only the first would report agreement while
    the second scope's names sat in a bucket the meter never reached.

    The meter answers in *full dotted names* (`django.templatetags.tz`), so the
    scope prefix comes off first and the `_impl`-relative root goes on — two
    steps, because the two spellings differ in exactly that way and skipping
    either is how `templatetags.django.templatetags.tz` gets built.
    """
    return table_spelling(
        suffixes(_shim.unshimmed(TARGET), TARGET), TARGET
    ) | table_spelling(
        suffixes(_shim.unshimmed(TEMPLATETAGS), TEMPLATETAGS), TEMPLATETAGS
    )


class OwnershipTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _shim.install()

    def test_the_table_partitions_the_universe(self):
        """Both directions, and this is the row that makes the table binding.

        `django_modules()` is every name Django's template package defines — 26
        at 6.0.9 for `django.template` and 5 for `django.templatetags`, the two
        populations M12's meter counts. A name in the tree and in no bucket is a
        module no milestone has agreed to take, which is the failure §2 found
        three of. A name in a bucket that is not in the tree is a row about
        nothing — `origin`, as M10's row had it, and as this row would have
        caught.

        Three buckets since the scope correction, and the third is the new
        direction: a name can now be settled by being declared *not* the engine,
        which is `NOT_THE_ENGINE` below. The equality is what makes the partition
        complete — a name leaving `OWNERS` must arrive in one of the other two.
        """
        self.assertEqual(
            set(OWNERS) | set(NOT_THE_ENGINE) | interposed(),
            the_universe(),
        )

    def test_no_name_is_both_owned_and_taken(self):
        """A name in two buckets is a table that cannot be read as a partition.

        It is also the specific mistake this milestone makes possible: a task
        that interposes a module and forgets to move it out of `OWNERS` leaves
        the meter and the table disagreeing, and
        `test_the_meter_is_the_claimed_minus_the_taken` would then fail with
        the arithmetic rather than the reason.

        The second pair is the new one and it is the scope correction's own
        failure mode: a name that is both promised to a milestone and declared
        not the engine is a table answering one question two ways, and the
        `pending` row below would silently read one of them.
        """
        self.assertEqual(set(OWNERS) & interposed(), set())
        self.assertEqual(set(NOT_THE_ENGINE) & interposed(), set())
        self.assertEqual(set(OWNERS) & set(NOT_THE_ENGINE), set())

    def test_the_meter_is_the_claimed_minus_the_taken_minus_the_declined(self):
        """The progress number and the table, as two sets with this between them.

        `unshimmed()` is the count every milestone is accepted on. If it can
        drift from the table, the table is decoration — and if the table can
        drift from it, the next reader plans against a schedule that is wrong.
        Both scopes, since M12: `the_meter()` is the two counts in one spelling.

        **Two subtractions, since the scope correction, and the second is what
        the correction added.** A name leaves the meter by being taken *or* by
        being declared not the engine, so the meter is `claimed() - interposed()`
        further minus `NOT_THE_ENGINE` — which is `pending()`, the engine's
        remaining work. Both are asserted: the first is the count a milestone is
        accepted on, and the second is the same sentence with the declared set
        left in, so the subtraction is visible in the row rather than derived
        from it.
        """
        self.assertEqual(the_meter(), pending())
        self.assertEqual(claimed() - interposed(), pending() | set(NOT_THE_ENGINE))

    def test_the_engines_remaining_work_is_empty(self):
        """The scope correction, closed: the engine's remaining work is none.

        `pending()` is `OWNERS` minus what is taken. M9 through M12 took thirteen
        of the fourteen names this correction left; M13 takes the last one, and
        the meter over the *first* scope reads zero for the first time.

        **The positive control is drawn from this population, not from the
        universe.** `unshimmed() - ours` passes happily on an empty universe: if
        `django_modules(TARGET)` returned nothing, `owned_by_django` would be
        empty, `pending()` would be empty, and every assertion below would be
        green while the instrument measured nothing. So the row states the
        population's members — `NOT_THE_ENGINE` is non-empty, and
        `owned_by_django` is exactly it, so the meter is measuring a set that has
        fourteen names in it (ten declared out of scope, and `library` until this
        commit) rather than one that has none.
        """
        _shim.install()
        owned_by_django = set(_shim.django_modules(TARGET)) - _shim._our_modules_in(TARGET)
        # The control, before the claim: the population the subtraction runs
        # over is non-empty, and the declared set is exactly the difference.
        self.assertEqual(owned_by_django, set(NOT_THE_ENGINE))
        self.assertGreater(len(NOT_THE_ENGINE), 0, "nothing is declared out of scope")
        # The claim.
        self.assertEqual(pending(TARGET), set())
        self.assertEqual(suffixes(_shim.unshimmed(TARGET), TARGET), set())
        self.assertEqual(pending(TEMPLATETAGS), set())
        self.assertEqual(suffixes(_shim.unshimmed(TEMPLATETAGS), TEMPLATETAGS), set())

    def test_the_declared_names_are_the_excluded_ones(self):
        """The membership lives in the shim; the reasons live here.

        One set, two files, and the reason is not symmetry: strict mode needs to
        subtract it at *runtime*, and `_shim` must not import test code — the
        same split `SHADOWING` (membership, derived from the files on disk) has
        against `INTERPOSED` (attribution). A name that leaves the meter in
        `_shim` without its reason being revisited here would otherwise be a
        silent widening of the scope this table exists to fix.

        The reasons are asserted only for being non-stubs — the prose is
        reviewed, not measured — and the length check is deliberately weak.
        """
        self.assertEqual(set(NOT_THE_ENGINE), set(_shim.NOT_THE_ENGINE))
        for name, reason in sorted(NOT_THE_ENGINE.items()):
            with self.subTest(name=name):
                self.assertGreater(len(reason), 80, f"{name}'s reason is a stub")

    def test_the_specs_declaration_is_the_shims(self):
        """§3 declares the ten, and the shim subtracts the ten it declares.

        `test_the_declared_names_are_the_excluded_ones` ties `_shim.NOT_THE_ENGINE`
        to this file's table. Neither was tied to **§3**, and the gap has a
        direction that matters: the meter is
        `django_modules - ours - NOT_THE_ENGINE`, so growing the subtrahend
        empties it *exactly as interposing does* — a module moved out of the
        engine by editing the shim and the reason beside it (which is what the
        discipline asks for) would leave every row in the tree green while §3
        still said ten, and M14's empty meter would be indistinguishable from
        the engine being complete. This is the shape
        `test_every_owner_is_a_milestone_the_spec_defines` already has one
        section over, applied to the other table.
        """
        declared = spec_not_the_engine()
        # The reader's own guard, the way the §10.3 reader has one: a frame that
        # stopped matching would otherwise reach `assertEqual` as an empty set,
        # and an empty set against a non-empty frozenset is a failure that says
        # "the spec drifted" when the instrument is what moved.
        self.assertGreaterEqual(len(declared), 10, sorted(declared))
        self.assertEqual(
            declared,
            set(_shim.NOT_THE_ENGINE),
            f"§3's enumeration and `_shim.NOT_THE_ENGINE` disagree, and the "
            f"reader found {sorted(declared)} in the frame — if those look like "
            "the right ten the shim moved, and if they do not the frame did",
        )

    def test_the_frame_reader_refuses_a_moved_anchor(self):
        """The control for the row above: a reworded frame raises, not answers.

        `between` exists because `partition` cannot fail. Without its checks a
        moved anchor returns the tail of §3 as the frame, and the row above then
        answers with a *wrong set* — a failure that reads as the spec drifting
        when the instrument moved. Driven on a literal rather than on §3, so the
        control is over the function the row actually depends on, and it is the
        one arm of that function a green run never reaches.
        """
        self.assertEqual(between("a FRAME b", "a ", " b"), "FRAME")
        for start, end in (("gone ", " b"), ("a ", " gone")):
            with self.subTest(start=start, end=end):
                with self.assertRaises(AssertionError):
                    between("a FRAME b", start, end)

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

    def test_the_declared_names_are_real_modules_the_meter_drops(self):
        """Per name, so a failure names the module instead of printing two sets.

        The partition row covers membership in one equality over three buckets;
        this one says it ten times and adds the direction that is specific to
        this bucket: a declared name is still a module Django's package defines —
        the `origin` failure of M10's row, in the bucket that is easiest to widen
        by accident — *and* it is one the meter no longer reports. The second
        half is the scope correction itself, asserted name by name, which no
        other row does.

        Both halves read the shim's own answers, so neither can be satisfied by
        editing this table; and the count assertion keeps the loop from being
        vacuously true over an empty bucket.
        """
        self.assertGreater(len(NOT_THE_ENGINE), 0, "nothing is declared out of scope")
        universe = {f"{TARGET}.{name}" for name in _shim.django_modules(TARGET)}
        reported = set(_shim.unshimmed(TARGET))
        for name in sorted(NOT_THE_ENGINE):
            with self.subTest(name=name):
                self.assertIn(f"{TARGET}.{name}", universe)
                self.assertNotIn(f"{TARGET}.{name}", reported)


if __name__ == "__main__":
    unittest.main()
