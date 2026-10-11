"""The pin under the Rust root read, and the control that says it is live.

``PyTarget::root_item`` in ``crates/template-py/src/lib.rs`` transcribes
``BaseContext.__getitem__`` — the ``reversed(self.dicts)`` walk, membership before
subscription, ``KeyError`` when no layer holds the key — so that a root lookup
stops entering Python. §5.5's third cost lever. The transcription runs only while

    ``_PyType_Lookup(type(context), "__getitem__") is BASE_CONTEXT_GETITEM``

holds, evaluated on **every** read, and the question this file answers is the one
that licence turns on: is ``BASE_CONTEXT_GETITEM`` the function the class
*actually runs*, or merely a function that was equal to it at some point?

**Which lookup that is, is the second review round's finding.** The comparison
used to read ``type(context).__getitem__`` as an *attribute*, and an attribute
read is not the lookup ``obj[key]`` performs: the subscript slot resolves the name
with ``_PyType_Lookup`` (CPython's ``typeobject.c``: the generic
``slot_mp_subscript`` → ``vectorcall_method`` → ``lookup_method_ex``), which
consults neither the metaclass nor a descriptor's ``__get__``. So three fixtures
can pass the attribute read while ``obj[key]`` answers elsewhere or not at all: a
metaclass ``__getattribute__`` that returns the pin over a subclass's real
override; a metaclass ``__getattr__`` that fabricates the name for a class whose
MRO has none, making ``obj[key]`` a ``TypeError``; and a class-dict entry that is
a custom descriptor, which answers ``__get__(None, cls)`` for the attribute read
while the slot binds ``__get__(obj, cls)``. All three are wrong renders, all three
are measured, and ``RootReadSlotEntryTests`` is the three rows — each a fixture
built to catch the old spelling, and each failing on the artifact before this
guard.

Both halves are needed and they are the two halves of one claim
--------------------------------------------------------------

A guard of that shape is a comparison, so it is worth exactly what its right-hand
side is worth. Two spellings of the right-hand side are possible and they differ
in *when they resolve*:

* ``engine/base.py`` binds ``BASE_CONTEXT_GETITEM = BaseContext.__getitem__``
  during the **package's own first import** — ``engine/__init__.py:26``'s
  ``from .engine import Engine`` reaches ``engine.py:7``'s ``from .base import
  Template``, so this module's body runs, and its last line records the original,
  before any route to ``BaseContext`` from outside the package can complete. The
  import order is the invariant, not this module's own ``from .context import
  BaseContext``, which is downstream of it.
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


def mro_entry(cls):
    """``cls``'s ``__getitem__`` as the *slot* would find it: the first entry in
    the MRO dictionaries, read raw.

    ``base.__dict__`` is the type's dictionary (a ``mappingproxy``): reading a key
    out of it runs no descriptor and asks no metaclass, which is what makes it the
    same lookup the guard asks through ``_PyType_Lookup``. ``getattr(cls,
    "__getitem__")`` is *not* that lookup — ``RootReadSlotEntryTests`` is the three
    fixtures that separate them — so a row about the guard must not spell it the
    second way.
    """
    for base in cls.__mro__:
        if "__getitem__" in base.__dict__:
            return base.__dict__["__getitem__"]
    return None


class RootReadPinTests(unittest.TestCase):
    """The right-hand side of the guard is the class's subscript entry, not a
    lookalike."""

    def test_the_pin_is_the_entry_the_slot_would_run(self):
        self.assertIs(
            mro_entry(BaseContext),
            PIN,
            "BASE_CONTEXT_GETITEM is not the function BaseContext's subscript slot "
            "resolves, so the guard licenses the transcription against something "
            "else",
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
        self.assertIs(mro_entry(Context), PIN)
        self.assertIs(mro_entry(RequestContext), PIN)

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

    def test_a_truncated_dicts_ends_the_search_where_django_ends_it(self):
        """A layer whose ``__contains__`` shortens ``dicts`` is **not** an uncertain
        case, and treating it as one is a wrong render rather than a slow path.

        ``for d in reversed(self.dicts)`` is driven by a ``list_reverseiterator``,
        which re-reads the list's length on every step: a layer that truncates the
        stack makes the next step find ``it_index >= PyList_GET_SIZE(seq)``, the
        iterator stops, and the ``for`` falls through to ``raise KeyError(key)``.
        The transcription ends the search the same way. Measured against **stock
        Django 6.0.9**, which answers ``''`` here: a bail instead re-walks the
        stack the walk has already partly consumed, finds the key an
        already-examined layer gained, and answers ``HIT``."""
        calls = []

        def build():
            class Shortening(dict):
                def __contains__(self, key):
                    calls.append(key)
                    self.owner.dicts[1]["k"] = "HIT"
                    del self.owner.dicts[2:]
                    return dict.__contains__(self, key)

            context = Context({})
            context.dicts = [context.dicts[0], {"a": 1}, {"b": 2}, Shortening({})]
            context.dicts[3].owner = context
            return context

        with self.assertRaises(KeyError):
            build()["k"]
        del calls[:]
        self.assertEqual(read("{{ k }}", build()), "")
        self.assertEqual(calls, ["k"], "each layer is probed exactly once")

    def test_a_list_subclass_that_overrides_reversed_is_not_transcribed(self):
        """Condition 3 is *exactly* a ``list``, not a subclass of one.

        ``reversed()`` dispatches to ``list.__reversed__``, so a subclass that
        overrides it decides which layers Django walks. Measured against **stock
        Django 6.0.9**, which answers ``FROM_REVERSED``: positional indexing from
        the list's own length answers ``FROM_SECOND``, the layer below the one
        ``__reversed__`` names. The walk must fall back — the entry count is what
        says it did, since the two readings differ in value here as well."""
        calls = []

        class Rev(list):
            def __reversed__(self):
                calls.append(1)
                return iter([{"x": "FROM_REVERSED"}])

        context = Context({"x": "FROM_DICTS"})
        context.dicts = Rev(context.dicts + [{"x": "FROM_SECOND"}])
        self.assertEqual(context["x"], "FROM_REVERSED")
        out, entered = counted("{{ x }}", context)
        self.assertEqual(out, "FROM_REVERSED")
        self.assertEqual(entered, 1, "the walk indexed the list instead of bailing")

    def test_a_bailed_walk_does_not_probe_a_layer_twice(self):
        """A non-``dict`` layer is a bail, and a bail runs Django's own method over
        the whole stack — so the walk must not have probed anything first, or every
        layer above the non-``dict`` one has its ``__contains__`` called twice.
        ``__contains__`` is a user method and its calls are observable, so the
        counts are the assertion and Django's own method is the reference."""

        def build(log):
            class Probe(dict):
                def __contains__(self, key):
                    log.append(key)
                    return False

            context = Context({})
            context.dicts = [context.dicts[0], UserDict({"y": 1}), Probe({})]
            return context

        reference_log = []
        with self.assertRaises(KeyError):
            build(reference_log)["nope"]

        ours_log = []
        self.assertEqual(read("{{ nope }}", build(ours_log)), "")
        self.assertEqual(ours_log, reference_log)


class RootReadSlotEntryTests(unittest.TestCase):
    """Fixtures on which the *attribute* read and the subscript *slot* disagree —
    and the slot is right, because the slot is what runs.

    What the first three rows have in common: ``getattr(type(context),
    "__getitem__") is BASE_CONTEXT_GETITEM`` is ``True``, so an attribute-based
    guard licenses the transcription (0 entries into ``BaseContext.__getitem__``)
    and answers out of the context's own layer — which is the transcription's
    answer — while the class's ``obj[key]`` answers something else. Measured on
    the artifact *before* this guard, sha ``caf15897``, stock Django 6.0.9 in the
    right-hand column and ours beside it:

    ==== ============== ========
    form ours, pre-fix   stock
    ==== ============== ========
    A    ``FROM_DICTS`` ``SNEAKY``
    B    ``FROM_FAKE``  ``''``
    C    ``FROM_DICTS`` ``SNEAKY``
    D    ``''``         ``''``
    ==== ============== ========

    D is in the table for the opposite reason: it is the row that did *not* break,
    and it is what says the entry lookup answers only half the question. Django's
    decision is the attribute read, taken before any subscription, so a metaclass
    that refuses the name keeps both engines on the attribute arm — a guard that
    asked the slot and nothing else would print ``FROM_DICTS`` where Django prints
    ``''``. That half is transcribed in its own place, ``PyTarget::has_getitem``.

    Every row compares our render with that same stock render, which is the
    reference the other classes here use.

    Three rows and not one because the two lookups come apart three ways — a
    metaclass that lies with ``__getattribute__``, a metaclass that fabricates
    with ``__getattr__`` (so ``obj[key]`` raises), and a class-dict descriptor
    whose ``__get__`` answers the class and the instance differently — and because
    the third needs no metaclass at all, which is what rules out the fix that
    would have been one line: *bail unless the metaclass is exactly* ``type``.
    The last row is the other side of that: a context whose metaclass is exotic
    but which leaves ``__getitem__`` alone is still correct to transcribe, and it
    must not be bailed on.
    """

    SOURCE = "{{ a }}"

    @staticmethod
    def stock_pin():
        """Stock Django's own ``BaseContext.__getitem__``, found the same way the
        guard finds ours — which also checks that this file's reader is a reader
        of Django's classes and not of ours."""
        from django.template.context import BaseContext as StockBaseContext

        return mro_entry(StockBaseContext)

    @staticmethod
    def stock_render(source, context):
        from django.template import Engine as StockEngine

        return StockEngine().from_string(source).render(context)

    def test_a_metaclass_getattribute_that_lies_about_getitem(self):
        """Form A. The class has a real override; the metaclass hides it from the
        attribute read and shows the pin instead.

        The trap is asserted rather than assumed: ``type(context).__getitem__``
        *is* the pin, and the entry the slot resolves is the class's own function.
        The row's evidence is the value Django answers and the count on that own
        function — it is entered, so the walk fell back to the subscription
        instead of transcribing."""

        def lying(pin, base, answer):
            class Meta(type):
                def __getattribute__(cls, name):
                    if name == "__getitem__":
                        return pin
                    return super().__getattribute__(name)

            class Sneaky(base, metaclass=Meta):
                def __getitem__(self, key):
                    return answer

            return Sneaky, Sneaky.__dict__["__getitem__"]

        from django.template import Context as StockContext

        our_class, our_own = lying(PIN, Context, "SNEAKY")
        stock_class, _ = lying(self.stock_pin(), StockContext, "SNEAKY")
        self.assertIs(our_class.__getitem__, PIN, "the fixture is not a trap")
        self.assertIs(mro_entry(our_class), our_own, "the fixture's MRO entry moved")

        out, own_entered = counted(self.SOURCE, our_class({"a": "FROM_DICTS"}),
                                   function=our_own)
        self.assertGreater(
            own_entered,
            0,
            "the class's own __getitem__ never ran, so the walk answered without "
            "subscripting",
        )
        self.assertEqual(out, "SNEAKY")
        self.assertEqual(out, self.stock_render(self.SOURCE,
                                                stock_class({"a": "FROM_DICTS"})))

    def test_a_metaclass_getattr_that_fabricates_getitem(self):
        """Form B. No ``__getitem__`` anywhere in the MRO — the metaclass makes
        the *name* exist, so ``obj[key]`` is a ``TypeError``.

        Django's ``_resolve_lookup`` tests the subscript at `base.py:963` and
        catches the ``TypeError`` at `base.py:968`, raising
        ``VariableDoesNotExist``, which renders ``''``. Our bail lands in the same
        place, which is the whole point: a bail is not a special case, it is
        Django's own method."""
        from django.template.context import RenderContext as StockRenderContext

        from django_rust_templates.engine.context import RenderContext as OurRenderContext

        def fabricating(pin, render_context_cls):
            class Meta(type):
                def __getattr__(cls, name):
                    if name == "__getitem__":
                        return pin
                    raise AttributeError(name)

            class Fabricated(metaclass=Meta):
                pass

            def build(template):
                context = Fabricated()
                context.render_context = render_context_cls()
                context.template = template
                context.template_name = template.name
                context.autoescape = True
                context.dicts = [{"a": "FROM_FAKE"}]
                context.use_l10n = False
                context.use_tz = False
                return context

            return Fabricated, build

        our_class, our_build = fabricating(PIN, OurRenderContext)
        stock_class, stock_build = fabricating(self.stock_pin(), StockRenderContext)
        self.assertIs(our_class.__getitem__, PIN, "the fixture is not a trap")
        self.assertIsNone(mro_entry(our_class), "the fixture's MRO gained an entry")
        with self.assertRaises(TypeError):
            our_class()["a"]

        our_template = ENGINE.from_string(self.SOURCE)
        from django.template import Engine as StockEngine

        stock_template = StockEngine().from_string(self.SOURCE)
        expected = self.stock_render(self.SOURCE, stock_build(stock_template))
        self.assertEqual(expected, "", "the fixture is not a wrong-answer trap")
        self.assertEqual(our_template.render(our_build(our_template)), expected)

    def test_a_class_dict_descriptor_that_answers_the_class_differently(self):
        """Form C, and the row that rules out bailing on a metaclass.

        ``__getitem__`` is a custom descriptor in the class's own dictionary: no
        metaclass is involved and ``type(context) is type``. The attribute read
        runs ``__get__(None, cls)`` — the pin — while the slot binds
        ``__get__(obj, cls)``, which is a different answer. Both reads are honest
        and they still disagree, so *which lookup* is asked is the whole fix."""
        from django.template import Context as StockContext

        def describing(pin, base, answer):
            class Descriptor:
                def __get__(self, obj, owner=None):
                    if obj is None:
                        return pin
                    return lambda key: answer

            class Described(base):
                __getitem__ = Descriptor()

            return Described

        our_class = describing(PIN, Context, "SNEAKY")
        stock_class = describing(self.stock_pin(), StockContext, "SNEAKY")
        self.assertIs(our_class.__getitem__, PIN, "the fixture is not a trap")
        self.assertIsNot(mro_entry(our_class), PIN, "the MRO entry is not a trap")

        out = read(self.SOURCE, our_class({"a": "FROM_DICTS"}))
        self.assertEqual(out, "SNEAKY")
        self.assertEqual(out, self.stock_render(self.SOURCE,
                                               stock_class({"a": "FROM_DICTS"})))

    def test_a_metaclass_that_refuses_the_name_is_honoured(self):
        """Form D, and the reason the entry lookup is *not* the whole licence.

        Django's decision is the **attribute** read — ``hasattr(type(current),
        "__getitem__")`` at `base.py:963` — and it happens
        before any subscription, so a metaclass that *refuses* that name sends
        Django down the attribute arm even though ``obj[key]`` would have run the
        pin: the MRO entry here **is** the pin, and the guard's condition 2 holds.

        Measured on both artifacts, ``''`` for ours and ``''`` for stock, which is
        the assertion below. The decision is transcribed in its own place
        (``PyTarget::has_getitem``, from that same line), and this row is what says
        the two halves stay separate: the entry lookup answers *"will the pin be
        what runs"*, and that question is only asked once Django has already said
        it will subscript."""
        from django.template import Context as StockContext

        def refusing(base):
            class Meta(type):
                def __getattribute__(cls, name):
                    if name == "__getitem__":
                        raise AttributeError(name)
                    return super().__getattribute__(name)

            class Refusing(base, metaclass=Meta):
                pass

            return Refusing

        our_class = refusing(Context)
        stock_class = refusing(StockContext)
        self.assertIs(mro_entry(our_class), PIN, "the fixture's MRO entry moved")
        self.assertFalse(hasattr(our_class, "__getitem__"), "the fixture is not a trap")

        out = read(self.SOURCE, our_class({"a": "FROM_DICTS"}))
        self.assertEqual(out, "")
        self.assertEqual(out, self.stock_render(self.SOURCE,
                                               stock_class({"a": "FROM_DICTS"})))

    def test_a_context_with_an_unrelated_metaclass_still_transcribes(self):
        """The other direction, and the reason the fix is *not* the one-line
        metaclass check: a class whose metaclass is not ``type`` but which never
        touches ``__getitem__`` has the pin as its MRO entry, so ``obj[key]`` runs
        the pin and transcribing is exactly right. Measured: 0 entries into the
        pin. A guard that bailed on any metaclass would print 1 here, and the
        *value* would be unchanged — so the count is the assertion, and it is a
        cost row rather than a correctness row."""

        class Meta(type):
            def __repr__(cls):
                return "<Metaclass that leaves __getitem__ alone>"

        class Metered(Context, metaclass=Meta):
            pass

        out, entered = counted(self.SOURCE, Metered({"a": "FROM_DICTS"}))
        self.assertEqual(out, "FROM_DICTS")
        self.assertEqual(entered, 0, "the walk bailed on a harmless metaclass")


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


class PinOrderProcessTests(unittest.TestCase):
    """The invariant the pin's soundness rests on, and it is an import order.

    ``engine/base.py`` binds the pin while its module body runs, and no project can
    run earlier — *because* ``django_rust_templates/engine/__init__.py`` reaches
    ``engine/base.py`` as its **first** import: ``from .engine import Engine``
    (line 26) pulls ``engine/engine.py``, whose ``from .base import Template``
    (line 7) is what runs the module the pin lives in. ``base.py``'s own
    ``from .context import BaseContext`` is *downstream* of that and is not the
    reason, which matters because the reason is what tells a future reader which
    line to protect.

    The row exists because that reason was prose only. If the package's first
    import were deferred or reordered, a project's ``import
    django_rust_templates.engine.context`` could rebind the class attribute before
    ``base.py`` ever ran, the pin would capture the replacement, and the guard
    would compare it with itself and pass — silently. **Every other row in this
    file would still pass in that world**, because they all import
    ``django_rust_templates.engine`` first, which is precisely the statement that
    would have moved. So the measurement is taken from the *other* door."""

    SCRIPT = """
import sys
sys.path.insert(0, {root!r})
sys.path.insert(0, {checkout!r})
import django_rust_templates.engine.context as context_module
print("django_rust_templates.engine.base" in sys.modules)
import django_rust_templates.engine.base as engine_base
from django_rust_templates.engine.context import BaseContext
print(engine_base.BASE_CONTEXT_GETITEM is BaseContext.__getitem__)
"""

    def test_reaching_the_class_has_already_run_the_module_that_binds_the_pin(self):
        """Expected ``True`` then ``True``.

        The process imports the *class*'s module and nothing else of ours. If
        ``engine/base.py`` had not run by then, the first line would print
        ``False``; if the pin had somehow captured a later binding, the second
        would. No settings are configured and nothing renders, so the probe cannot
        be reading a state the engine reached by another route."""
        done = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                self.SCRIPT.format(
                    root=str(PROJECT_ROOT), checkout=str(DJANGO_CHECKOUT)
                ),
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
        self.assertEqual(done.stdout.split(), ["True", "True"])


if __name__ == "__main__":
    unittest.main()
