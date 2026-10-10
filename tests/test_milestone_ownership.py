"""The ownership table, checked against the tree it claims to partition.

Each row below is a direction the table can be wrong in. Together they are the
repair `.review/m10-recon.md` §8 asks for — "the rows and the meter become two
sets with an assertion between them, which is the thing that was missing in the
first place" — which is why they live in the milestone that noticed the problem
even though they are about every milestone after it.

The failures they exist for, both measured in the recon: `origin` sat in M10's
row although no ``django/template/origin.py`` exists at 6.0.9, and `autoreload`,
`context_processors` and `loader` sat in no row at all. A table with an
assertion under it cannot do either.

**Eight rows became six in the M18 rewiring**, and the two that left are the two
whose subject was the substitution rather than the partition: `OWNERS` is empty
because every name it held has been taken or declined, so a row asserting that
`OWNERS` and `INTERPOSED` are disjoint, and a row asserting that nothing is
still owed, were rows over a set with no members. The rule the vacuity rows in
`test_interposition.py` are held to applies here: a claim about an empty set is
a claim about nothing unless it is drawn from a population that has members, and
there is no such population left. What replaces them is `interposed()`'s check
against the **filesystem**, one row down, which is membership and has teeth.
"""

import unittest

from tests.milestone_ownership import (
    INTERPOSED,
    NOT_THE_ENGINE,
    OWNERS,
    SCOPES,
    TARGET,
    TEMPLATETAGS,
    between,
    claimed,
    django_modules,
    dotted_suffix,
    interposed,
    our_modules,
    shadowing_files,
    spec_milestones,
    spec_not_the_engine,
    unshimmed,
)


def suffixes(names, scope=TARGET) -> set:
    """Dotted suffixes of full ``<scope>.x`` names.

    `unshimmed()` answers in full dotted names because that is what a reader of
    the coverage count wants; the table is keyed by suffix because that is what
    `dotted_suffix()` produces from a file path, and comparing two spellings of
    the same name would be the drift this module exists to catch.
    """
    return {name[len(scope) + 1 :] for name in names}


def table_spelling(names, scope) -> set:
    """A scope's suffixes, in the ``engine``-relative spelling the tables use.

    The two scopes differ and that is the whole of it: `django.template`'s root
    is `""`, so `base` is already the key, while `django.templatetags`'s files
    live under `engine/templatetags/` and its keys carry that prefix — the same
    relation `dotted_suffix` inverts from a path. Derived from `SCOPES` rather
    than a literal, so the prefix cannot drift from the root the files are
    actually under.
    """
    root = SCOPES[scope][0]
    return {f"{root}.{name}" if root else name for name in names}


def the_universe() -> set:
    """Every name the table partitions, in the tables' spelling — both scopes."""
    return table_spelling(django_modules(TARGET), TARGET) | table_spelling(
        django_modules(TEMPLATETAGS), TEMPLATETAGS
    )


def the_coverage_count() -> set:
    """``unshimmed()`` over both scopes, in the tables' spelling.

    Both, because the table partitions both: a row that read only the first
    would report agreement while the second scope's names sat in a bucket the
    count never reached.

    The count answers in *full dotted names* (``django.templatetags.tz``), so the
    scope prefix comes off first and the ``engine``-relative root goes on — two
    steps, because the two spellings differ in exactly that way and skipping
    either is how ``templatetags.django.templatetags.tz`` gets built.
    """
    return table_spelling(
        suffixes(unshimmed(TARGET), TARGET), TARGET
    ) | table_spelling(
        suffixes(unshimmed(TEMPLATETAGS), TEMPLATETAGS), TEMPLATETAGS
    )


class OwnershipTableTests(unittest.TestCase):
    def test_the_table_partitions_the_universe(self):
        """Both directions, and this is the row that makes the table binding.

        `django_modules()` is every name Django's template package defines — 26
        at 6.0.9 for `django.template` and 5 for `django.templatetags`, the two
        populations the count covers. A name in the tree and in no bucket is a
        module nobody has decided about, which is the failure §2 found three of.
        A name in a bucket that is not in the tree is a row about nothing —
        `origin`, as M10's row had it, and as this row would have caught.

        Three buckets, and the third is the direction that makes the partition
        complete rather than merely non-overlapping: a name can be settled by
        being declared *not* the engine, which is `NOT_THE_ENGINE` below. The
        equality is what it buys — a name leaving `OWNERS` must arrive in one of
        the other two.

        **This row is about the two tables and not about the tree, and the
        rewiring measured that rather than assuming it.** With `OWNERS` empty it
        asserts that `INTERPOSED`'s names plus the declined ones are all 31. A
        name declined *and* attributed passes, because a union swallows the
        duplicate — the row that catches that is the coverage row below, which
        subtracts. Deleting `engine/context.py` passes too, because `interposed()`
        is a table and the file is on the other row. Both were driven; what
        remains is the real teeth: a name in `INTERPOSED` that Django's tree does
        not have, or a Django name in no bucket at all.
        """
        self.assertEqual(
            interposed() | claimed(),
            the_universe(),
        )
        self.assertEqual(
            set(OWNERS),
            set(),
            "OWNERS is the *bucket a future milestone writes into*; a name here "
            "is work someone has claimed and not done, and the coverage row "
            "below would then be asserting a count that cannot be zero",
        )

    def test_interposed_is_the_filesystem(self):
        """`INTERPOSED` adds the milestone attribution and no new membership.

        Which modules are ours is the **filesystem's** to state: `shadowing_files()`
        is every ``engine`` file wearing a name Django's tree also defines, asked
        of the two trees rather than read from a list. A second list of membership
        here would be the drift this module exists to catch, so this row compares
        the two and `INTERPOSED` carries only *when*.

        It was `_shim.SHADOWING`'s to state until the M18 rewiring and the claim
        is unchanged — the table it is compared against was derived from the files
        on disk then too, which is why deleting the shim cost this row nothing but
        its mechanism. What *did* change is that `SHADOWING` also carried a
        transcribed/fresh column, and that column is gone rather than migrated:
        it was a second spelling of `check_transcriptions.py`'s `DECLARED`, which
        already answers the same question with the regions attached.

        **`__init__.py` is the one file the two sides spell differently**, and
        this row is where the filter lives: `shadowing_files()` reports it — it is
        a colliding file, and the guard declares it — while `dotted_suffix` gives
        it `""`, the package itself, which no ownership table carries. The `if
        name` below is that filter, and it is the same one the pre-rewiring row
        had; what is new is the reason being written down, because the two
        answers differ by exactly one and a reader meeting the asymmetry would
        otherwise have to guess which side was wrong.
        """
        self.assertEqual(
            interposed(),
            {dotted_suffix(name) for name in shadowing_files()} - {""},
        )
        self.assertGreater(len(shadowing_files()), 0, "no file shadows anything")

    def test_the_coverage_count_is_empty_in_both_scopes(self):
        """The scope correction, closed: every Django name is written or declined.

        This is the row M14 was accepted on, with its mechanism changed and its
        claim intact. It used to read ``unshimmed()``, the substitution's progress
        meter; it now reads a function of the same name that is a *coverage*
        statement — the scope's Django modules minus ours minus the declined —
        and the arithmetic is identical because the question was always the same
        set.

        **The positive control is drawn from this population, not from the
        universe.** A subtraction passes happily on an empty operand: if
        `django_modules(TARGET)` returned nothing, if `our_modules(TARGET)`
        returned nothing, or if `NOT_THE_ENGINE` were everything, every assertion
        below would be green while the instrument measured nothing. The three
        non-emptiness rows are that control, each over one operand, and each is
        *stated with its size* so a reader can see the population rather than
        infer it from a `>`.

        **This is also the row that catches a file going missing**, which the
        partition one section up does not — it holds the tables, and this one
        subtracts the tree. Driven: with ``our_modules`` short by ``context`` the
        partition row stays green and this one reports
        ``['django.template.context']``.

        The last assertion is the other route to zero: a name we transcribe **and**
        leave declared. That widens the subtrahend and looks exactly like progress,
        and the partition row cannot see it — ``interposed() | claimed()`` still
        equals the universe either way. `utils` is the live instance in the other
        direction; this is the direction that would have caught it, and it did not
        exist.
        """
        # The control, before the claim: all three operands have members.
        self.assertGreater(
            len(django_modules(TARGET)), 20, sorted(django_modules(TARGET))
        )
        self.assertGreater(len(our_modules(TARGET)), 10, sorted(our_modules(TARGET)))
        self.assertGreater(len(NOT_THE_ENGINE), 0, "nothing is declared out of scope")
        # The other route to zero: transcribed *and* declined, in either scope.
        self.assertEqual(
            set(NOT_THE_ENGINE) & (our_modules(TARGET) | our_modules(TEMPLATETAGS)),
            set(),
            "a declared name is also a file we ship, so the count is empty "
            "because the subtrahend grew and not because the engine is whole",
        )
        # The claim.
        self.assertEqual(unshimmed(TARGET), [])
        self.assertEqual(unshimmed(TEMPLATETAGS), [])
        self.assertEqual(the_coverage_count(), set())

    def test_the_declared_names_are_the_excluded_ones(self):
        """Per name, so a failure names the module instead of printing two sets.

        The partition row covers membership in one equality over three buckets;
        this one says it eleven times and adds the direction that is specific to
        this bucket: a declined name is still a module Django's package defines —
        the `origin` failure of M10's row, in the bucket that is easiest to widen
        by accident — *and* it is one the count no longer reports. The second half
        is the scope correction itself, asserted name by name, which no other row
        does.

        Both halves read the tree's own answers, so neither can be satisfied by
        editing this table; and the count assertion keeps the loop from being
        vacuously true over an empty bucket. The reasons are asserted only for
        being non-stubs — the prose is reviewed, not measured — and the length
        check is deliberately weak.
        """
        self.assertGreater(len(NOT_THE_ENGINE), 0, "nothing is declared out of scope")
        for name in sorted(NOT_THE_ENGINE):
            with self.subTest(name=name):
                reason = NOT_THE_ENGINE[name]
                self.assertGreater(len(reason), 80, f"{name}'s reason is a stub")
                scope = TEMPLATETAGS if name.startswith("templatetags.") else TARGET
                local = name[len("templatetags.") :] if scope == TEMPLATETAGS else name
                self.assertIn(local, django_modules(scope))
                self.assertNotIn(f"{scope}.{local}", unshimmed(scope))

    def test_the_specs_declaration_is_the_tables(self):
        """§3 declares the eleven, and this table declines the eleven it declares.

        Neither bucket was tied to **§3** until M14's Task 1b, and the gap has a
        direction that matters: the count is
        `django_modules - our_modules - NOT_THE_ENGINE`, so growing the subtrahend
        empties it *exactly as transcribing does* — a module moved out of the
        engine by editing this table and the reason beside it (which is what the
        discipline asks for) would leave every row in the tree green while §3
        still said ten, and the empty count would be indistinguishable from the
        engine being complete. This is the shape
        `test_every_owner_is_a_milestone_the_spec_defines` already has one
        section over, applied to the other table.

        **`utils` is that failure arriving**, one architectural rewiring later
        rather than one careless edit later, and the row is what forced §3 and
        the table to move together: the name was in `INTERPOSED[M10]` and its file
        was deleted in `03038e6`, and until both documents were corrected this row
        read eleven against ten.
        """
        declared = spec_not_the_engine()
        # The reader's own guard, the way the §10.3 reader has one: a frame that
        # stopped matching would otherwise reach `assertEqual` as an empty set,
        # and an empty set against a non-empty frozenset is a failure that says
        # "the spec drifted" when the instrument is what moved. Measured, in this
        # rewiring: it fired on the closing anchor and named it.
        self.assertGreaterEqual(len(declared), 11, sorted(declared))
        self.assertEqual(
            declared,
            set(NOT_THE_ENGINE),
            f"§3's enumeration and `NOT_THE_ENGINE` disagree, and the reader "
            f"found {sorted(declared)} in the frame — if those look like the "
            "right eleven the table moved, and if they do not the frame did",
        )

    def test_the_frame_reader_refuses_a_moved_anchor(self):
        """The control for the row above: a reworded frame raises, not answers.

        `between` exists because `partition` cannot fail. Without its checks a
        moved anchor returns the tail of §3 as the frame, and the row above then
        answers with a *wrong set* — a failure that reads as the spec drifting
        when the instrument moved. Driven on a literal rather than on §3, so the
        control is over the function the row actually depends on, and it is the
        one arm of that function a green run never reaches.

        This is not hypothetical here: moving §3's closing anchor is exactly what
        the `utils` correction did, and the refusal above is what said so.
        """
        self.assertEqual(between("a FRAME b", "a ", " b"), "FRAME")
        for start, end in (("gone ", " b"), ("a ", " gone")):
            with self.subTest(start=start, end=end):
                with self.assertRaises(AssertionError):
                    between("a FRAME b", start, end)

    def test_every_attribution_is_a_milestone_the_spec_defines(self):
        """The labels are read from §10.3 rather than restated here.

        A second list of milestone names would agree with itself, so a typo
        (`M9` for `M13`) would be invisible. `spec_milestones()` parses the
        spec's own table, which makes the label a claim about that document.

        **Which of the two tables carries the label decides which cell is read,
        and reading the wrong one is silent.** `INTERPOSED` is keyed by label and
        `OWNERS` by name, so `INTERPOSED`'s *keys* are the labels and its values
        are the modules — a version of this row that took `INTERPOSED[label]` would
        be asserting that a list of module names appears in §10.3's table. Only
        the keys are read here, and the module names are covered by the partition
        row and the filesystem row instead. Driven: renaming M13's key to `M99`
        fails it.
        """
        defined = spec_milestones()
        self.assertGreaterEqual(len(defined), 15, sorted(defined))
        for label in sorted(INTERPOSED):
            with self.subTest(label=label):
                self.assertIn(label, defined)
        for name, label in sorted(OWNERS.items()):
            with self.subTest(name=name):
                self.assertIn(label, defined, f"{name} is owned by {label!r}")


if __name__ == "__main__":
    unittest.main()
