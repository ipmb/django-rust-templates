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
corpus calls it **85** times, and its lines 860-877 sat in the *gap* between
two pinned spans — so no pin would have moved if it had stayed invisible, and a
declared transcription region placed on those lines would have been accepted by
clause (4) with both sides of the comparison running Rust. The defect was
latent rather than live, which is the only reason it cost nothing to find late.

The rows are shaped like ``tests/test_shim.py``'s: each names the object it is
about and asserts on a measurement rather than on the table's contents, so the
instrument and the pin cannot agree with each other about a wrong number.
"""

import inspect
import os
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


def _passthrough(func):
    """A decorator that returns a wrapper written **here**, around ``func``."""
    import functools

    @functools.wraps(func)
    def inner(*args, **kwargs):
        return func(*args, **kwargs)

    return inner


# A member whose ``def`` is in this file and whose ``__wrapped__`` is not.
wrapped_foreign = _passthrough(os.path.join)


def _hand_rolled(self):
    return "hand-rolled"


class Subject:
    attribute = HandRolledDescriptor(_hand_rolled)


class ForeignKind:
    """A descriptor kind ``_invoked`` has no case for, defined in *this* file.

    The module under instrument in the row below is not this one, which is the
    shape every kind that has actually caused trouble has: ``staticmethod``,
    ``property``, ``cached_property`` and ``lru_cache`` are all defined in other
    modules, so ``_is_ours``'s fallback arm — "the descriptor's class was
    written here" — fires for none of them.
    """

    def __init__(self, func):
        self.func = func

    def __get__(self, instance, owner):
        return self.func.__get__(instance, owner)


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
        self.assertEqual(base_defs["FilterExpression.args_check"], (860, 877))
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
        region placed on 860-877 was accepted, and the parity comparison then
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
            (860, 877, "FilterExpression.args_check"),
            guard.COMPARED_PATH["base.py"],
            "the span this row is about is not in the table any more",
        )

        upstream, regions = guard.DECLARED["base.py"]
        # Same length as ours, so clause (5) — which requires prose for a
        # region whose two spans differ in length — has nothing to say and the
        # only clause with anything to report is the one under test.
        synthetic = ((860, 877), (855, 872), "args_check")
        guard.DECLARED["base.py"] = (upstream, regions + [synthetic])
        try:
            failures = guard.check()
        finally:
            guard.DECLARED["base.py"] = (upstream, regions)

        self.assertTrue(
            any(
                "FilterExpression.args_check (860-877)" in failure
                and "Rust against Rust" in failure
                for failure in failures
            ),
            "a declared region covering the compared path was not refused: "
            f"{failures}",
        )

    def test_a_descriptor_from_another_module_is_reported(self):
        """The reviewer's finding: the filter *was* the hole, one level up.

        ``residue`` used to ask ``_is_ours``, which asks ``_owning_file``, which
        asks ``_innermost`` — the table whose incompleteness ``residue`` exists
        to detect. The check was asking the thing it was checking, so an
        unhandled kind was invisible exactly when it was unhandleable: the
        descriptor's *instance* has no source of its own, ``getsourcefile``
        raises, and the fallback arm needed the descriptor's class to have been
        written in the module under instrument — true for none of
        ``staticmethod``, ``property``, ``cached_property`` or ``lru_cache``,
        which is to say true for none of the shapes that have ever mattered.

        Both halves are asserted, because either alone is satisfied by a wrong
        instrument: ``_is_ours`` is False, so the old filter could not have
        reported this member, and ``residue`` reports it anyway.

        The subject is built by ``exec`` into a fresh module rather than by
        assigning this file's classes to it, because ``classes()`` sweeps only
        classes whose ``__module__`` names the module being swept — so a class
        borrowed from here would be invisible to the sweep for a reason that has
        nothing to do with the predicate under test, and the row would pass or
        fail for the wrong cause.
        """
        from tests.compared_path import UnhandledMember, _is_ours, instrumented, residue

        subject = types.ModuleType("elsewhere")
        subject.__file__ = str(PROJECT_ROOT / "tests" / "elsewhere.py")
        subject.__dict__["ForeignKind"] = ForeignKind
        exec(
            compile(
                "def _reached(self):\n"
                "    return 'elsewhere'\n"
                "\n"
                "class Subject:\n"
                "    reached = ForeignKind(_reached)\n",
                subject.__file__,
                "exec",
            ),
            subject.__dict__,
        )

        value = vars(subject.Subject)["reached"]
        self.assertIsInstance(value, ForeignKind, "the premise of this row")
        self.assertFalse(
            _is_ours(value, subject),
            "the premise: with this answer the old filter could not report it",
        )
        self.assertEqual(residue(subject), ["Subject.reached"])

        with self.assertRaises(UnhandledMember) as caught:
            with instrumented(subject):
                pass  # pragma: no cover — the context manager raises on entry
        self.assertIn("Subject.reached", str(caught.exception))

    def test_a_builtin_member_is_not_reported(self):
        """The rule's builtin arm, which is load-bearing rather than tidy.

        ``TokenType._new_member_`` is ``object.__new__``: a C function with no
        Python source anywhere, so ``getsourcefile`` raises and the owning file
        is *unknown* — the same fact as the row above, and the rule now reports
        unknown. Without this arm the member would be reported and every
        instrumentation of ``base.py`` would raise, including every parity
        harness. ``TokenType.__new__`` is excluded one level down for the same
        reason (it is ``Enum.__new__``, written in ``enum.py``).
        """
        import django.template.base as base

        from tests.compared_path import _innermost, residue

        member = vars(base.TokenType)["_new_member_"]
        self.assertTrue(
            inspect.isbuiltin(_innermost(member, os.path.realpath(base.__file__))),
            "the premise: this member resolves to something with no source",
        )
        self.assertNotIn("TokenType._new_member_", residue(base))

    def test_a_function_dispatched_through_a_registry_is_counted(self):
        """The escape the binding sweep could not see, on the shape Django uses.

        ``bindings`` walks module namespaces, and Django does not dispatch
        through module namespaces: ``Parser.add_library`` *copies* a
        ``Library``'s ``tags``/``filters`` dicts into the parser
        (``base.py:668-670``), so ``parser.tags[command]`` is a dict value and
        the wrapper placed on the module global is never the object called.
        Measured on ``loader_tags.py`` the day it was interposed: the sweep read
        4 functions where call frames read 7, and the three it missed were
        ``do_block``/``do_extends``/``do_include`` — the tag compile functions,
        which the *reference* parse runs. The error direction is the unsafe one
        for clause (4), which exists so a declared region cannot sit on code the
        comparison runs.

        The fixture is registered in ``sys.modules`` under a ``django.`` name
        because that is the population the sweep visits — a module named
        anything else is deliberately not swept, and a row that dodged that
        would be testing a different function.
        """
        from tests.compared_path import instrumented

        class Registry:  # duck-typed like `Library`: two dicts of callables
            def __init__(self):
                self.tags = {}
                self.filters = {}

        def thing(parser, token):
            return "ran"

        subject = types.ModuleType("django.template.registry_probe")
        subject.__file__ = __file__
        registry = Registry()
        registry.tags["thing"] = thing
        subject.registry = registry
        subject.thing = thing

        sys.modules[subject.__name__] = subject
        try:
            with instrumented(subject) as counts:
                self.assertIsNot(
                    registry.tags["thing"],
                    thing,
                    "the premise: the wrapper must be *in the table*, because "
                    "the parser dispatches through a copy of it",
                )
                registry.tags["thing"](None, None)
                self.assertEqual(
                    counts["thing"],
                    1,
                    "a call through the registry is a call the module made",
                )
            self.assertIs(
                registry.tags["thing"], thing, "and the table is put back on exit"
            )
        finally:
            del sys.modules[subject.__name__]

    def test_a_wrapper_we_wrote_around_a_foreign_function_stays_ours(self):
        """The mirror of the ``@contextmanager`` case: the walk must not leave.

        ``functools.wraps`` sets ``__wrapped__`` and the walk follows it to reach
        the ``def`` behind a decorator artifact. Followed unconditionally it also
        walks *out* of our file when the wrapper **is** the ``def`` we wrote and
        the thing under it is somebody else's. Measured on the closed tree:
        ``getsourcefile`` says this file, ``_innermost`` returned
        ``os.path.join``, ``_is_ours`` said False, and ``definitions`` dropped
        the member — an owned, recognised, executed function missing from the
        compared path, which is the ``args_check`` under-count arriving *through*
        the fix for ``args_check``.

        The premise is asserted first, because a negative result from a fixture
        nobody showed to be positive is how both of this instrument's last two
        wrong readings happened.

        This row is about the *walk* only — which object the ownership question
        is asked of. The span is a second question about the same object, and
        the row below measures it: the walk stopping correctly is worth nothing
        if the read then leaves again.
        """
        from tests.compared_path import _defining_file, _innermost, _is_ours

        own = os.path.realpath(__file__)
        self.assertNotEqual(
            _defining_file(wrapped_foreign.__wrapped__),
            own,
            "the premise: whatever it wraps is somebody else's",
        )
        self.assertEqual(
            _defining_file(wrapped_foreign),
            own,
            "the premise: and the wrapper itself was written here",
        )

        subject = types.ModuleType("wrapper_owner")
        subject.__file__ = __file__
        subject.wrapped_foreign = wrapped_foreign

        self.assertTrue(
            _is_ours(wrapped_foreign, subject),
            "the walk must stop at the wrapper — it is the def we wrote, and "
            "following __wrapped__ out of the file made definitions drop an "
            "owned, recognised, executed function from the compared path",
        )
        self.assertEqual(
            _defining_file(_innermost(wrapped_foreign, own)),
            own,
            "following __wrapped__ left this file",
        )

    def test_a_span_read_from_another_file_is_refused(self):
        """A span is two integers, so nothing downstream can ask where it came from.

        ``COMPARED_PATH`` is a list of line ranges and clause (4) compares them
        to declared regions as integers, so a span read out of a foreign file is
        indistinguishable from one of ours, and a guard nobody has seen fire is
        a guard nobody knows works.

        This is the arm that still refuses, after the reviewer's second finding:
        **neither** the def the walk landed on **nor** the def behind it is this
        module's — the descriptor instance is ours by its class, and the function
        it hands back was written in the file this row's fixture came from. The
        row below is the arm that must *not* fire, which is why fixing the read
        could not simply mean reporting every foreign file it passes.
        """
        from tests.compared_path import ForeignSpan, definitions

        subject = types.ModuleType("span_owner")
        subject.__file__ = str(PROJECT_ROOT / "tests" / "span_owner.py")
        subject.__dict__["FOREIGN"] = _passthrough
        exec(
            compile(
                "class LocalStatic(staticmethod):\n"
                "    pass\n"
                "\n"
                "class C:\n"
                "    member = LocalStatic(FOREIGN)\n",
                subject.__file__,
                "exec",
            ),
            subject.__dict__,
        )

        with self.assertRaises(ForeignSpan) as caught:
            definitions(subject)
        self.assertIn("member", str(caught.exception))
        self.assertIn("span_owner.py", str(caught.exception))

    def test_a_wrapper_we_wrote_around_a_foreign_function_is_measured_here(self):
        """The same fixture, and the span lands on the def that runs.

        The reviewer's finding on the first version of the guard's fix: the walk
        stopped at the right function and the *read* then walked past it, so the
        member recovered by the row above could only ever become a
        :class:`ForeignSpan` — the recovery had no reachable success case.
        ``getsourcefile`` reads ``__code__.co_filename`` and ``getsourcelines``
        unwraps ``__wrapped__``, so ``span_of`` has to *choose* its subject:
        read the def whose own file is ours, unwrap only when it is not.

        Measured before the change: ``span_of`` gave 72-92 of ``<frozen
        posixpath>`` — a real span, of the wrong function, which is why the
        assertion that caught it quoted this test file's own lines 72-92 (two
        files can share a line number and a span is two integers). Measured now:
        this file's ``def inner``, and the lines read back as text are the
        wrapper — decorator included, exactly as any plain ``def`` of ours is
        recorded, because ``co_firstlineno`` of a decorated function is its
        first decorator.
        """
        from tests.compared_path import definitions

        subject = types.ModuleType("wrapper_owner")
        subject.__file__ = __file__
        subject.wrapped_foreign = wrapped_foreign

        span = definitions(subject)["wrapped_foreign"]
        lines = pathlib.Path(__file__).read_text().splitlines()
        written = "\n".join(lines[span[0] - 1 : span[1]])
        self.assertIn(
            "def inner",
            written,
            "the span must be the def that executes, and it is written here",
        )
        self.assertIn(
            "@functools.wraps",
            written,
            "decorators count, as they do for any other member",
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
