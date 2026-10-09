"""What a ``fresh`` ``_impl`` file owes, stated as rows rather than as a promise.

Every file under ``_impl`` that wears a real module's name is one of two things
(``_shim.SHADOWING``): **transcribed** — a copy of the pinned file, differing
only where ``scripts/check_transcriptions.py``'s ``DECLARED`` says — or
**fresh** — written from scratch with Django as the specification. The two carry
different obligations, and until M10 only the first had a guard:

* a transcription is **falsified** by the diff against the pin, which is why
  ``scripts/check_transcriptions.py`` exists at all;
* a fresh file has no diff of its own, and the suite cannot stand in for one —
  measured as M9's finding 3, where a file whose behaviour is upstream's keeps
  all 1,547 tests green either way.

So ``fresh`` is the honest choice only when the equivalence can be **stated as a
row**, and this module is where it is stated. ``EQUIVALENCE`` maps an ``_impl``
file to the test case that states its equivalence;
``ShadowingTests.test_each_kind_is_the_transcription_guards_own_answer`` fails
when a ``fresh`` file is missing from the table, and the rows below are what make
the entry mean something.

``exceptions.py`` is the only ``fresh`` file in the tree, and it is the one that
made the rule necessary: written from scratch at M0, behaviourally upstream's,
and the only reason anyone knows that is that a reviewer diffed it by hand — an
instrument nothing re-runs.

**Why this file is named ``test_*`` even though it is mostly a table.**
``check.sh`` discovers with ``-p 'test_*.py'``, so a module named
``transcription_equivalence.py`` would never be collected and these rows would
never run — the table would be a warrant nothing executes, which is the exact
failure this module exists to avoid one level down. The first version of this
file was named that way and the unit count did not move; that is how it was
found. The table and the rows that give it teeth therefore live in one
discovered module, and ``EQUIVALENCE`` names this module rather than a
data-only sibling.
"""

import pickle
import unittest

from tests.pinned_module import load_pinned

EQUIVALENCE = {
    # `_impl`-relative path → (importable module, TestCase class name).
    "exceptions.py": (
        "tests.test_transcription_equivalence",
        "TemplateExceptionsEquivalenceTests",
    ),
}


class TemplateExceptionsEquivalenceTests(unittest.TestCase):
    """``_impl/exceptions.py`` behaves as ``django/template/exceptions.py`` does.

    The equivalence is stated as rows about the *published surface*: the names,
    the base chain, and the construction protocol over every argument shape the
    class accepts. What it deliberately does not claim is the file's text — the
    differences are docstrings and one expression (``[] if tried is None else
    tried`` against ``if tried is None: tried = []``), which is exactly the class
    of change a transcription would have to declare and a fresh file does not.

    Every row compares our class against ``load_pinned``'s copy of the same
    class. That is the only way to compare against Django's, for the reason
    `tests/pinned_module.py` gives.
    """

    # Built once, outside the rows: a passed list must come back *identical*, so
    # the two constructions have to be handed the same object rather than two
    # equal ones.
    SENTINEL_LIST = [("origin", "not found")]
    SENTINEL_BACKEND = type("Backend", (), {})

    @classmethod
    def setUpClass(cls):
        import django.template.exceptions as ours

        cls.ours = ours
        cls.pinned = load_pinned("exceptions.py")

    def shapes(self):
        """The argument shapes, built once so both sides see the same objects."""
        return [
            (("nope",), {}),
            (("nope", self.SENTINEL_LIST), {}),
            (("nope",), {"tried": self.SENTINEL_LIST}),
            (("nope",), {"backend": self.SENTINEL_BACKEND}),
            (("nope", self.SENTINEL_LIST, self.SENTINEL_BACKEND, []), {}),
            ((), {}),
        ]

    def observed(self, cls, args, kwargs):
        """What one construction produces, or the error it produces instead.

        A ``TypeError`` from the wrong argument shape is an observation like any
        other — the row is that both classes do the *same* thing, and for the
        shapes a class refuses, the same thing is a refusal. The attribute reads
        are inside the ``try`` on purpose: `TemplateSyntaxError` accepts no
        `tried`, and the ``AttributeError`` is part of what this compares.
        """
        try:
            exc = cls(*args, **kwargs)
            return (
                "constructed",
                exc.args,
                exc.tried,
                exc.backend,
                exc.chain,
                [type(error).__name__ for error in exc.chain],
                str(exc),
                repr(exc),
            )
        except Exception as error:  # noqa: BLE001 - the disagreement is the data
            return ("raised", type(error).__name__, str(error))

    def test_the_public_names_are_the_same_set(self):
        ours = {name for name in vars(self.ours) if not name.startswith("_")}
        theirs = {name for name in vars(self.pinned) if not name.startswith("_")}
        self.assertEqual(ours, theirs)

    def test_each_class_has_the_same_base_chain(self):
        for name in ("TemplateDoesNotExist", "TemplateSyntaxError"):
            with self.subTest(name=name):
                ours = [c.__name__ for c in getattr(self.ours, name).__mro__]
                theirs = [c.__name__ for c in getattr(self.pinned, name).__mro__]
                self.assertEqual(ours, theirs)

    def test_construction_agrees_on_every_argument_shape(self):
        for name in ("TemplateDoesNotExist", "TemplateSyntaxError"):
            for args, kwargs in self.shapes():
                with self.subTest(name=name, args=args, kwargs=sorted(kwargs)):
                    self.assertEqual(
                        self.observed(getattr(self.ours, name), args, kwargs),
                        self.observed(getattr(self.pinned, name), args, kwargs),
                    )

    def test_a_passed_list_is_kept_by_identity(self):
        """The one expression the two files spell differently, pinned as behaviour.

        Ours is ``[] if tried is None else tried`` and Django's is
        ``if tried is None: tried = []`` — equal by value either way, and
        different only if a rewrite copied the list or defaulted to a shared
        literal. Identity is the assertion a value comparison cannot make.
        """
        given = [("origin", "x")]
        ours = self.ours.TemplateDoesNotExist("nope", given)
        self.assertIs(ours.tried, given)

    def test_template_does_not_exist_reports_only_its_message(self):
        """The custom ``__init__`` is visible in ``args``, and only here.

        ``TemplateDoesNotExist.__init__`` takes three optional arguments and
        calls ``super().__init__(msg)``, so ``args`` is the message **alone**
        however much was passed — which is what ``str()``, ``repr()`` and every
        ``assertRaisesMessage`` compare against.

        The first draft of this row asserted the same for both classes and
        failed, and the failure is the useful part: ``TemplateSyntaxError``
        declares no ``__init__`` at all, so its ``args`` are whatever was passed.
        That difference is now a row of its own below rather than an assumption
        inside this one.
        """
        ours = self.ours.TemplateDoesNotExist(
            "nope", self.SENTINEL_LIST, self.SENTINEL_BACKEND, []
        )
        self.assertEqual(ours.args, ("nope",))

    def test_template_syntax_error_declares_no_initialiser(self):
        """``TemplateSyntaxError`` is ``Exception`` with a docstring.

        The equivalence that matters for it is negative: it must *not* acquire
        the machinery `TemplateDoesNotExist` has. A rewrite that gave it the same
        signature — plausible, since the two are always mentioned together —
        would silently drop the arguments a caller passed, and the rows above
        that compare construction would not notice, because they compare against
        the pin and the pin has no such signature to compare with.
        """
        self.assertIs(self.pinned.TemplateSyntaxError.__init__, Exception.__init__)
        self.assertIs(self.ours.TemplateSyntaxError.__init__, Exception.__init__)
        self.assertEqual(
            self.ours.TemplateSyntaxError("nope", self.SENTINEL_LIST).args,
            ("nope", self.SENTINEL_LIST),
        )

    def test_both_classes_pickle_round_trip(self):
        for name in ("TemplateDoesNotExist", "TemplateSyntaxError"):
            with self.subTest(name=name):
                ours = getattr(self.ours, name)("nope")
                restored = pickle.loads(pickle.dumps(ours))
                self.assertIs(type(restored), type(ours))
                self.assertEqual(restored.args, ("nope",))
                if hasattr(ours, "tried"):
                    self.assertEqual(restored.tried, [])


if __name__ == "__main__":
    unittest.main()
