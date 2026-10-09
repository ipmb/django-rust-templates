"""The compared-path instrument's table of member kinds, and its residue check.

``tests/compared_path.py`` decides what "the code a parity harness executes"
means, and ``scripts/check_transcriptions.py``'s ``COMPARED_PATH`` is its pinned
answer. The rows here are about the instrument itself, because the failure it
had was in the instrument and not in the pin: the sweep wrapped what
``inspect.isfunction`` accepts, which is a plain function and only a plain
function, while a class dict holds a function in five shapes — plain,
``staticmethod``, ``classmethod``, ``property``, ``cached_property``, and under
an ``lru_cache``. Four of those were invisible.

Measured on the pinned tree before anything changed, by the reviewer's
reproduction: ``FilterExpression.args_check`` is a ``staticmethod``, the parity
corpus calls it **85** times, and its lines 827-844 sat in the *gap* between
two pinned spans — so no pin would have moved if it had stayed invisible, and a
declared transcription region placed on those lines would have been accepted by
clause (4) with both sides of the comparison running Rust. The defect was
latent rather than live, which is the only reason it cost nothing to find late.

The rows are shaped like ``tests/test_shim.py``'s: each names the object it is
about and asserts on a measurement rather than on the table's contents, so the
instrument and the pin cannot agree with each other about a wrong number.
"""

import pathlib
import sys
import types
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT / ".django") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / ".django"))

from django_rust_templates import _shim  # noqa: E402


class HandRolledDescriptor:
    """A descriptor kind ``compared_path._invoked`` has no case for.

    Defined here rather than imported, because the residue check's second arm
    asks whether the *descriptor's class* was written in the module under
    instrument, and this file is the module under instrument in the row below.
    """

    def __init__(self, func):
        self.func = func

    def __get__(self, instance, owner):
        return self.func.__get__(instance, owner)


def _hand_rolled(self):
    return "hand-rolled"


class Subject:
    attribute = HandRolledDescriptor(_hand_rolled)


class DescriptorCountingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _shim.install()

    def test_a_staticmethod_is_counted_and_called_correctly(self):
        """The case that was invisible, exercised the way ``base.py`` calls it.

        ``FilterExpression.__init__`` calls ``self.args_check(...)``. A sweep
        that read the member back with ``getattr`` and wrote a plain function in
        its place would demote the staticmethod to an instance method, and that
        call would then pass the ``FilterExpression`` as ``name`` and raise
        ``TypeError`` — a *loud* failure, which is why it would not have gone
        unnoticed for two milestones. The silent half is the counting.
        """
        import django.template.base as base

        from django.template import Engine

        from tests.compared_path import instrumented

        with instrumented(base) as counts:
            Engine().from_string("{{ x|default:'y' }}")
        self.assertGreaterEqual(counts["FilterExpression.args_check"], 1, counts)

    def test_the_restore_puts_back_the_member_and_not_getattrs_answer(self):
        """``getattr`` on a staticmethod is the function, and writing *that*
        back leaves a method demoted for the rest of the session.

        So this is not a tidiness row: the restore is the only thing standing
        between the instrument and every later row in the session running
        against a ``FilterExpression`` whose ``args_check`` is an instance
        method.
        """
        import django.template.base as base

        from tests.compared_path import instrumented

        before = vars(base.FilterExpression)["args_check"]
        self.assertIsInstance(before, staticmethod, "the premise of this row")

        with instrumented(base):
            pass

        after = vars(base.FilterExpression)["args_check"]
        self.assertIs(after, before)
        self.assertIsInstance(after, staticmethod)

    def test_the_other_three_kinds_are_counted(self):
        """``property``, ``cached_property`` and ``lru_cache``, one call each.

        ``Origin.loader_name``, ``Engine.template_loaders`` and
        ``get_app_template_dirs`` — three members of three modules, each the
        first of its kind in the tree, and none of them counted before.
        """
        import django.template.base as base
        import django.template.engine as engine
        import django.template.utils as utils

        from tests.compared_path import instrumented

        origin = base.Origin(name="a.html", template_name="a.html")
        with instrumented(base) as base_counts:
            origin.loader_name
        self.assertEqual(base_counts["Origin.loader_name"], 1, base_counts)

        with instrumented(engine) as engine_counts:
            # On an *instance*: reading the member off the class hands back the
            # `cached_property` object itself and runs no code, which is the
            # distinction between "the member exists" and "the function ran".
            engine.Engine().template_loaders
        self.assertEqual(
            engine_counts["Engine.template_loaders"], 1, engine_counts
        )

        with instrumented(utils) as utils_counts:
            utils.get_app_template_dirs("templates")
        self.assertEqual(
            utils_counts["get_app_template_dirs"], 1, utils_counts
        )

    def test_the_definitions_carry_spans_from_the_right_file(self):
        """A descriptor member's span is the ``def`` this module wrote.

        Read from the member instead of from the innermost function and
        ``inspect`` cannot recover lines from a ``cached_property`` at all —
        ``span_of`` returns ``None``, ``measure`` drops the label, and the
        measurement goes missing rather than failing. Read from the wrong
        layer and the number is a line in somebody else's file, which is what
        the previous sweep did for ``context.py``'s three
        ``@contextlib.contextmanager`` methods.
        """
        import django.template.base as base
        import django.template.context as context
        import django.template.engine as engine

        from tests.compared_path import definitions

        base_defs = definitions(base)
        self.assertEqual(base_defs["FilterExpression.args_check"], (827, 844))
        self.assertEqual(base_defs["Origin.loader_name"][0], 134)
        self.assertIsNone(base_defs.get("TokenType.__new__"))

        for label in ("Context.bind_template", "RenderContext.push_state"):
            with self.subTest(label=label):
                span = definitions(context)[label]
                source = pathlib.Path(context.__file__).read_text().splitlines()
                self.assertIn(
                    "def " + label.rsplit(".", 1)[1],
                    "\n".join(source[span[0] - 1 : span[1]]),
                    f"{label} spans {span}, which does not contain its own def",
                )

        self.assertEqual(
            definitions(engine)["Engine.get_default"][0], 89, definitions(engine)
        )

    def test_the_residue_check_fires_on_a_kind_with_no_case(self):
        """The loud half, and it is the half that makes the table safe.

        A table of kinds that nothing checks is a claim about the world; a
        descriptor written by hand lands in it and quietly goes uncounted, which
        is the shape of the defect this whole module is about. So the sweep
        reports what it does not recognise, and the report names the member.
        """
        from tests.compared_path import UnhandledMember, instrumented, residue

        subject = types.ModuleType("subject")
        subject.__name__ = __name__
        subject.__file__ = __file__
        subject.Subject = Subject

        self.assertEqual(
            residue(subject),
            ["Subject.attribute"],
            "the hand-rolled descriptor was not reported",
        )
        with self.assertRaises(UnhandledMember) as caught:
            with instrumented(subject):
                pass  # pragma: no cover — the context manager raises on entry
        self.assertIn("Subject.attribute", str(caught.exception))

    def test_a_declared_region_on_args_check_is_refused(self):
        """The reviewer's scenario, run against the guard rather than argued.

        ``scripts/check_transcriptions.py``'s clause (4) refuses a transcription
        whose declared regions cover the code the parity harness executes, and
        before this fix ``args_check`` was not in the table it consults — so a
        region placed on 827-844 was accepted, and the parity comparison then
        ran Rust against Rust with both sides agreeing. That is the whole reason
        this module exists.

        The **declaration is the input**, which is what makes this a row rather
        than a mock: `DECLARED` is the thing a person edits, and the question is
        whether the guard refuses what they wrote. It is restored in `finally`,
        because `check()` is also every parity module's warrant and this row
        runs inside the same process as them.
        """
        import scripts.check_transcriptions as guard

        self.assertIn(
            (827, 844, "FilterExpression.args_check"),
            guard.COMPARED_PATH["base.py"],
            "the span this row is about is not in the table any more",
        )

        upstream, regions = guard.DECLARED["base.py"]
        # Same length as ours, so clause (5) — which requires prose for a
        # region whose two spans differ in length — has nothing to say and the
        # only clause with anything to report is the one under test.
        synthetic = ((827, 844), (822, 839), "args_check")
        guard.DECLARED["base.py"] = (upstream, regions + [synthetic])
        try:
            failures = guard.check()
        finally:
            guard.DECLARED["base.py"] = (upstream, regions)

        self.assertTrue(
            any(
                "FilterExpression.args_check (827-844)" in failure
                and "Rust against Rust" in failure
                for failure in failures
            ),
            "a declared region covering the compared path was not refused: "
            f"{failures}",
        )

    def test_the_residue_check_is_silent_on_the_transcriptions(self):
        """The control for the row above, and the reason it is not vacuous.

        A residue check that fired on everything would be an instrument nobody
        could use; one that fired on nothing would be the defect again. So the
        whole interposed tree is swept: every member a sweep must consider, of
        thirteen modules, and no report — which is a measurement about the table
        being complete, not about the check being broken, because the row above
        executes the same function on a member it does not know.
        """
        import importlib

        import scripts.check_transcriptions as guard

        from tests.compared_path import residue

        swept = 0
        for name in sorted(guard.COMPARED_PATH):
            module = importlib.import_module(guard.module_of(name))
            with self.subTest(module=name):
                self.assertEqual(residue(module), [])
            swept += 1
        self.assertGreater(swept, 0, "no module was swept")
