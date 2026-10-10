"""The pin under the Rust root read, and the control that says it is live.

``PyTarget::root_item`` in ``crates/template-py/src/lib.rs`` transcribes
``BaseContext.__getitem__`` — the ``reversed(self.dicts)`` walk, membership before
subscription, ``KeyError`` when no layer holds the key — so that a root lookup
stops entering Python. §5.5's third cost lever. The transcription runs only while

    ``type(context).__getitem__ is BASE_CONTEXT_GETITEM``

holds, evaluated on **every** read, and the question this file answers is the one
that licence turns on: is ``BASE_CONTEXT_GETITEM`` the function the class
*actually runs*, or merely a function that was equal to it at some point?

Both halves are needed and they are the two halves of one claim
--------------------------------------------------------------

A guard of that shape is a comparison, so it is worth exactly what its right-hand
side is worth. Two spellings of the right-hand side are possible and they differ
in *when they resolve*:

* ``engine/base.py`` binds ``BASE_CONTEXT_GETITEM = BaseContext.__getitem__``
  during its own import — the statement that hands ``BaseContext`` to whoever
  wants to rebind it is the same statement that records the original.
* The Rust side could instead capture ``type(context).__getitem__`` on first use.
  That spelling is **unsound**, and not in a corner: a project that rebinds
  ``BaseContext.__getitem__`` before its first render pins the *replacement*, the
  guard then compares the replacement against itself and passes, and the walk
  answers a lookup the class no longer performs — silently, and green everywhere,
  because the fallback and the transcription agree on every input that is not
  about the rebinding.

``PinSoundnessProcessTests`` is that row. It cannot run in this process — every row
above it has already rendered, so a lazy pin would have been taken long ago — so
it re-runs the first render in a fresh interpreter with the class attribute moved
*before* it.

The instrument, and why it is the only one available
----------------------------------------------------

The transcription is a transcription: it is the same function, so no input
distinguishes "the Rust path answered" from "Django's method answered". What
distinguishes them is a Python frame. ``BaseContext.__getitem__`` is a Python
function, and a call to it fires a ``call`` event under ``sys.setprofile`` whether
the caller is Python or C, while the Rust path performs the same read **without
entering a frame at all**. So the instrument is a counting profile hook filtered
to one code object, and it reads the same quantity in both rows below.

The two rows below that use it are over the same population — one helper, one
template, one context — and differ in the class attribute and nothing else. The
**non-zero** one is the control for the zero one: a zero is what a broken
instrument prints, so without a row that makes the hook fire on this exact code
object, "the walk never enters Python" and "the hook never fires" are the same
observation, and a build whose fast path never ran at all would satisfy every row
in this file.

What this file does not claim
----------------------------

That a project *may* rebind ``BaseContext.__getitem__`` and that we handle it. The
engine is a drop-in replacement and that is Django's class, so we do not get to
decide; the rows below state what happens when someone does. The subprocess row
covers the realistic hazard — a rebind after the engine is imported and before the
first render. It does not cover a rebind *during* ``import
django_rust_templates.engine``, which the package's own import order forbids and
which nothing in this file could construct anyway.
"""

import subprocess
import sys
import unittest
from collections import UserDict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django_rust_templates.engine import Context, Engine, RequestContext  # noqa: E402
from django_rust_templates.engine import base as engine_base  # noqa: E402
from django_rust_templates.engine.base import BaseContext  # noqa: E402

PIN = engine_base.BASE_CONTEXT_GETITEM
ORIGINAL = BaseContext.__getitem__
ENGINE = Engine()


def read(source, context):
    """One render through the engine — the population every row here shares."""
    return ENGINE.from_string(source).render(context)


def counted(source, context, function=ORIGINAL):
    """``read``, and the number of times ``function`` was entered while it ran.

    Filtered on the code object rather than on the name: a row below installs a
    *different* function under the same name, and counting by name would report
    that one's entries as the original's.
    """
    code = function.__code__
    entered = []

    def hook(frame, event, arg):
        if event == "call" and frame.f_code is code:
            entered.append(frame)

    previous = sys.getprofile()
    sys.setprofile(hook)
    try:
        out = read(source, context)
    finally:
        sys.setprofile(previous)
    return out, len(entered)


class RootReadPinTests(unittest.TestCase):
    """The right-hand side of the guard is the class attribute, not a lookalike."""

    def test_the_pin_is_the_function_the_class_binds(self):
        self.assertIs(
            PIN,
            BaseContext.__getitem__,
            "BASE_CONTEXT_GETITEM is not the function BaseContext runs, so the "
            "guard licenses the transcription against something else",
        )

    def test_every_class_that_reaches_the_walk_shares_that_one_function(self):
        """The guard is an identity test, so it admits whichever classes still
        inherit ``__getitem__``. ``Context`` and ``RequestContext`` both do, which
        means a plain ``Context`` and a ``TEMPLATES``-driven ``RequestContext``
        both take the transcription.

        A class added later that overrides the method is not a defect: the guard
        reads per use and would send it to the fallback. This row is here so that
        the day one of them starts overriding is a red row rather than a silent
        narrowing of what the transcription covers."""
        self.assertIs(Context.__getitem__, PIN)
        self.assertIs(RequestContext.__getitem__, PIN)

    def test_the_module_binds_the_pin_when_it_is_imported(self):
        """The static half of the soundness claim, and the cheap one: the binding
        is in the module's namespace, so it happened when the module body ran —
        which is before any code outside the package holds ``BaseContext``. The
        order that makes that true is the import graph, and
        ``PinSoundnessProcessTests`` is the dynamic half."""
        self.assertIn("BASE_CONTEXT_GETITEM", vars(engine_base))
        self.assertEqual(PIN.__module__, "django_rust_templates.engine.context")
        self.assertEqual(PIN.__name__, "__getitem__")


class RootReadTranscriptionTests(unittest.TestCase):
    """The transcription answers what the method it transcribes answers."""

    def setUp(self):
        self.engine = ENGINE

    def tearDown(self):
        BaseContext.__getitem__ = ORIGINAL

    def test_a_root_read_never_enters_python(self):
        out, entered = counted("{{ a }}", Context({"a": 1}))
        self.assertEqual(out, "1")
        self.assertEqual(
            entered,
            0,
            "the walk entered BaseContext.__getitem__, so the transcription is "
            "not the path a root read takes",
        )

    def test_a_loop_variable_read_never_enters_python(self):
        """§5.5's sentence is about loop-variable reads and then widens to every
        root lookup; this is the narrow end of it, and ``P`` in
        ``.review/m19-measurements/root_read_ab.py`` is the wide end."""
        out, entered = counted(
            "{% for i in items %}{{ i }}{% endfor %}", Context({"items": [1, 2, 3]})
        )
        self.assertEqual(out, "123")
        self.assertEqual(entered, 0)

    def test_rebinding_the_class_attribute_takes_the_walk_off_the_transcription(self):
        """The control for the two rows above, and it is the *hook* it controls.

        One class attribute moved and nothing else: same helper, same template,
        same context, same engine, same instrument. With the attribute moved the
        guard fails, the walk falls back to the class attribute, the replacement
        runs — and it *delegates*, so ``ORIGINAL`` is entered and the very same hook
        that reported ``0`` above reports non-zero here. Without this row, "the
        transcription answered" and "the hook never fires on this code object" are
        one observation, and a build that never entered the fast path at all would
        satisfy both rows above.

        ``entered.count("i")`` and not ``len(entered)``: the walk resolves the
        loop's own sequence at the root too, so the list holds an ``items`` read
        that has nothing to do with the loop variable. Counting the key this row is
        about keeps the expectation independent of that."""
        entered = []

        def moved(self, key):
            entered.append(key)
            return ORIGINAL(self, key)

        BaseContext.__getitem__ = moved
        out, hits = counted(
            "{% for i in items %}{{ i }}{% endfor %}", Context({"items": [1, 2, 3]})
        )
        self.assertEqual(out, "123")
        self.assertGreater(
            hits,
            0,
            "the hook did not fire on the original entry point even with the class "
            "attribute moved, so the two rows above are not evidence of anything",
        )
        self.assertEqual(
            entered.count("i"),
            3,
            f"three iterations, so three loop-variable reads; got {entered!r}",
        )

    def test_a_moved_class_attribute_decides_the_answer(self):
        """The row above read for its consequence rather than its route: the
        replacement answers something else, and the render says so. A guard
        comparing against a lazily captured pin would have answered ``1``."""

        def replacement(self, key):
            raise RuntimeError("rebound __getitem__ ran")

        BaseContext.__getitem__ = replacement
        with self.assertRaises(RuntimeError) as caught:
            self.engine.from_string("{{ a }}").render(Context({"a": 1}))
        self.assertEqual(str(caught.exception), "rebound __getitem__ ran")

    def test_a_subclass_that_overrides_getitem_is_honoured(self):
        """The other way to move the attribute, and the one a project is likelier
        to do than assigning to ``BaseContext``: subclass the context."""

        class Answering(Context):
            def __getitem__(self, key):
                return "SENTINEL"

        self.assertEqual(
            self.engine.from_string("{{ a }}").render(Answering({"a": 1})),
            "SENTINEL",
        )

    def test_the_search_goes_upward_past_a_layer_that_does_not_hold_the_key(self):
        """``reversed(self.dicts)``, and the half of it a one-layer fixture cannot
        reach: a root read must walk *past* a layer that does not hold the key.

        Written through ``push`` rather than by poking ``dicts``, because the layer
        ``push`` appends is ``ContextDict`` — a ``dict`` subclass — which is the
        shape the guard has to admit for ``{% with %}`` and ``{% include %}`` to
        keep the fast path at all."""
        context = Context({"a": 1})
        layer = context.push()
        # By `__module__` and not by `__name__`: stock Django's `context.py` defines
        # a `ContextDict` too, so the bare name is true on both sides and would pass
        # even if `push` handed back Django's. The *file* is the discriminator.
        self.assertTrue(
            type(layer).__module__.startswith("django_rust_templates"),
            f"push() gave a {type(layer).__module__}.ContextDict",
        )
        self.assertIsInstance(layer, dict)
        try:
            self.assertEqual(self.engine.from_string("{{ a }}").render(context), "1")
        finally:
            context.pop()

    def test_the_innermost_layer_wins(self):
        context = Context({"a": 1})
        context.push({"a": 2})
        try:
            self.assertEqual(self.engine.from_string("{{ a }}").render(context), "2")
        finally:
            context.pop()

    def test_a_layer_that_is_not_a_dict_still_resolves(self):
        """Condition 4 of the guard. ``UserDict`` is a mapping and not a ``dict``,
        so the whole lookup goes back to Python — and has to give the same answer.
        Nothing about the transcription is licensed here, which is the point: the
        guard's fallback is not a special case, it is the shipped behaviour."""
        self.assertEqual(
            self.engine.from_string("{{ x }}").render(Context(UserDict({"x": 1}))),
            "1",
        )

    def test_a_dict_subclass_that_claims_a_key_it_does_not_hold(self):
        """Why the transcription tests membership and subscribes through the
        *object protocol* rather than through ``PyDict::contains``/``::get_item``.

        ``BaseContext.__getitem__`` tests ``key in d`` before it evaluates
        ``d[key]``, so ``__missing__`` alone can never fire through it: measured, a
        subclass defining only ``__missing__`` raises ``KeyError`` here just as
        Django does. A subclass that *also* overrides ``__contains__`` to claim a
        key it does not hold is the case that separates the two readings —
        membership passes, the subscription misses, and ``d[key]`` reaches
        ``__missing__`` through CPython's ``dict_subscript``, which is what
        ``dict.__getitem__`` is. The raw C lookup does not, and this row would
        render ``""`` instead."""

        class Claiming(dict):
            def __contains__(self, key):
                return True

            def __missing__(self, key):
                return "MISSING-" + key

        context = Context(Claiming({"y": 2}))
        reference = context["x"]
        self.assertEqual(reference, "MISSING-x")
        self.assertEqual(self.engine.from_string("{{ x }}").render(context), reference)

    def test_a_key_no_layer_holds_is_a_miss_rather_than_an_error(self):
        """The transcription raises ``KeyError(bit)``, and the walk's arm has to
        classify it as a miss — the same reading ``PyObject_GetItem`` raising the
        class error produced before it. Django's own method is the reference for
        both halves."""
        context = Context({"a": 1})
        with self.assertRaises(KeyError) as caught:
            context["nope"]
        self.assertEqual(caught.exception.args, ("nope",))
        self.assertEqual(self.engine.from_string("{{ nope }}").render(context), "")


class PinSoundnessProcessTests(unittest.TestCase):
    """The only row in this file that can tell the two spellings of the pin apart.

    ``sys.setprofile`` and the rows above all run in a process that has rendered
    already, so a lazily captured pin would have been taken before any of them.
    This row puts the rebinding *first*, before the first render, in a process
    where nothing has rendered yet.
    """

    SCRIPT = """
import sys
sys.path.insert(0, {root!r})
sys.path.insert(0, {checkout!r})
from scripts.filter_probe import ensure_settings
ensure_settings()
from django_rust_templates.engine import Context, Engine
from django_rust_templates.engine.base import BaseContext


def replacement(self, key):
    return "REBOUND"


BaseContext.__getitem__ = replacement
print(Engine().from_string("{{{{ a }}}}").render(Context({{"a": 1}})))
"""

    def test_the_pin_was_bound_before_a_project_could_rebind_the_class(self):
        """Expected ``REBOUND``.

        The engine is imported, then ``BaseContext.__getitem__`` is moved, and only
        then does anything render. ``engine/base.py`` bound the pin during the
        first of those steps, so the guard compares against the original, the
        comparison fails, the walk falls back to the class attribute — which is now
        the replacement — and the replacement answers.

        **``1`` is the failure reading**, and it is what a pin captured lazily on
        the Rust side would print: the guard would compare the replacement with
        itself, pass, and transcribe. There would be no warning and no second
        symptom; the template renders, and it renders a value the class no longer
        produces."""
        done = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                self.SCRIPT.format(root=str(PROJECT_ROOT), checkout=str(DJANGO_CHECKOUT)),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            done.returncode,
            0,
            f"the probe did not run:\n{done.stdout}\n{done.stderr}",
        )
        self.assertEqual(done.stdout.strip(), "REBOUND")


if __name__ == "__main__":
    unittest.main()
