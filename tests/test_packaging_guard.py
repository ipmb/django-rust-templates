"""The packaging guard: the backend refuses a Django it was not proven against.

Why this file exists
--------------------

The engine is a *transcription* of one Django, pinned at 6.0.9 in §4 of the
design and in ``django.lock``. Everything the project checks — the acceptance
suite, the differential harness, the meter — is run against that checkout, so
none of it can speak about any other Django. Before this guard existed, two
measured outcomes fell out of that gap:

* **Django 6.1.2** — the engine loaded, rendered a template, and said nothing
  about the transcription and the running Django being different versions. Every
  gate the project has says green, on a transcription taken from a version that is
  not the one running. This is the dangerous direction, and it is silent.
* **Django 5.2.7** — import died with ``cannot import name
  'django_file_prefixes' from 'django.utils.deprecation'``, an ``ImportError``
  that names Django for a problem that is ours.

So the guard is not a convenience: it is the thing that makes the *rest* of this
project's evidence mean something outside the pinned tree.

The subject is the backend, not a function
------------------------------------------

``_require_django`` is reached from ``RustTemplates.__init__``, and that is the
only door in — ``EngineHandler`` constructs the backend when a project first asks
for an engine, so one call there covers every route a project has. ``check_packaging.sh``
drives the same claim through a real settings module and a real venv; every row
here constructs the backend from a ``TEMPLATES``-shaped dict, which is the same
door without the wheel. A row calling ``_require_django`` directly would be testing
a name rather than the route, so only the two rows that *are* about the function —
the positive control and the hatch — do that, and a third row exists to show the
constructor reaches it.

The rows are over one population
--------------------------------

Every row below patches ``django.VERSION``, which is the same input the guard
reads, and the first row is the **positive control** — the transcription pin
itself, which must *not* raise. Without it, a guard that raised unconditionally
would satisfy every other row here, and the class does not otherwise assert that
the check has a passing branch at all.

**What M18 took away from this file.** These rows used to run ``install()``,
``_shim.installed()`` and ``_shim.ShimError``, and to assert that a refused install
left no finder on ``sys.meta_path``. There is no finder to leave: the guard raises
before ``BaseEngine.__init__`` and before the engine is built, so the "leaves the
interpreter untouched" row is now about ``sys.meta_path`` and ``sys.modules`` not
being touched at all, which is a stronger statement than the one it replaces.
"""

import sys
import unittest
from unittest import mock

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

import django  # noqa: E402

from django.template.backends.django import DjangoTemplates  # noqa: E402

from django_rust_templates import TRANSCRIBED_DJANGO  # noqa: E402
from django_rust_templates import _require_django  # noqa: E402
from django_rust_templates import rust  # noqa: E402

# A `TEMPLATES` entry with the parts `EngineHandler` fills in made explicit, so the
# constructor below is handed the same dict shape a project's settings produce.
PARAMS = {"NAME": "rust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}


def params(**options):
    return {**PARAMS, "OPTIONS": options}


class DjangoVersionGuardTests(unittest.TestCase):
    """The guard's own branch, driven with a patched ``django.VERSION``."""

    def test_the_guard_accepts_the_transcription_pin(self):
        """The positive control, and the row that keeps the others honest.

        The subject is the *pin* rather than whatever this checkout happens to
        be, so the row is a statement about the guard and not about the
        environment — the three rows below read the same attribute.
        """
        pin = (*TRANSCRIBED_DJANGO, "final", 0)
        with mock.patch.object(django, "VERSION", pin):
            self.assertIsNone(_require_django(), "the guard refused its own pin")

    def test_the_constructor_is_the_door_the_guard_is_behind(self):
        """``_require_django`` runs from ``RustTemplates.__init__``, first.

        Every refusal row below constructs a backend, and what makes that a
        statement about the *route* rather than about a function is this row: the
        constructor reaches the check, with the value of ``allow_django_mismatch``
        that ``OPTIONS`` carried. The recorder raises, so the constructor stops
        where the check is — which is also what it does in production on a
        mismatch.
        """
        reached = []

        def recorder(allow_mismatch=False):
            reached.append(allow_mismatch)
            raise AssertionError("reached the guard")

        for options, expected in (({}, False), ({"allow_django_mismatch": True}, True)):
            with self.subTest(options=options):
                reached.clear()
                with mock.patch.object(rust, "_require_django", recorder):
                    with self.assertRaises(AssertionError):
                        rust.RustTemplates(params(**options))
                self.assertEqual(reached, [expected])

    def test_the_guard_refuses_a_later_django(self):
        """6.1.2 is the silent case, and the one the guard exists for.

        It used to load and render. What is asserted is the refusal *and* both
        version numbers in the message, because a refusal that does not name what
        it found sends the reader to the dependency resolver rather than to the pin.
        """
        with mock.patch.object(django, "VERSION", (6, 1, 2, "final", 0)):
            with self.assertRaises(Exception) as ctx:
                rust.RustTemplates(params())
        message = str(ctx.exception)
        self.assertIn("6.0.9", message, "the pin is not named")
        self.assertIn("6.1.2", message, "the version found is not named")

    def test_the_guard_refuses_an_earlier_django(self):
        """5.2.7 is the loud case — but loud in the wrong voice.

        Left alone it raises ``ImportError: cannot import name
        'django_file_prefixes'``, which blames Django. The subject here is that
        our own error comes first, so the reader is told the engine is pinned
        rather than sent looking for a missing name in Django's tree.
        """
        with mock.patch.object(django, "VERSION", (5, 2, 7, "final", 0)):
            with self.assertRaises(Exception) as ctx:
                rust.RustTemplates(params())
        message = str(ctx.exception)
        self.assertIn("6.0.9", message)
        self.assertIn("5.2.7", message)

    def test_the_refusal_names_the_way_out(self):
        """A wall with no door trains the reader to reach for the wrong lever.

        The hatch is an ``OPTIONS`` key, so the message has to spell it: a reader
        who is told only that the versions differ has three bad options — pin
        Django down, patch the check, or stop using the backend — and no way to
        tell which one is intended.
        """
        with mock.patch.object(django, "VERSION", (6, 1, 2, "final", 0)):
            with self.assertRaises(Exception) as ctx:
                rust.RustTemplates(params())
        self.assertIn("allow_django_mismatch", str(ctx.exception))

    def test_the_escape_hatch_opens_against_a_mismatched_django(self):
        """A wall with no door would be worse than the gap it closes.

        The project itself will want to run against the next Django before it
        transcribes it, and the pin is a claim about *proven* behaviour rather
        than about what is permitted. The hatch is exercised on the same
        mismatched input the rows above refuse, so the two differ in one thing.
        """
        with mock.patch.object(django, "VERSION", (6, 1, 2, "final", 0)):
            self.assertIsNone(_require_django(allow_mismatch=True))

    def test_a_refused_construction_leaves_the_interpreter_untouched(self):
        """The check runs *before* anything is built, not after.

        This is the property that makes the refusal safe to catch-and-report: a
        caller told "no" is left exactly where it started. Asserted separately from
        the raise because the raise alone does not imply it — a check at the *end*
        of ``__init__`` would raise with an ``Engine`` already constructed, and a
        check anywhere at all would be reached only after this module's own
        imports. ``sys.meta_path`` is the sharper half: the M18 rewrite deleted the
        finder that used to sit on it, and a green run here is the statement that
        nothing puts one back.
        """
        before_meta = list(sys.meta_path)
        before_modules = dict(sys.modules)
        with mock.patch.object(django, "VERSION", (6, 1, 2, "final", 0)):
            with self.assertRaises(Exception):
                rust.RustTemplates(params())
        self.assertEqual(sys.meta_path, before_meta, "a refused call added a finder")
        self.assertEqual(
            {k: v for k, v in sys.modules.items() if k not in before_modules},
            {},
            "a refused call imported a module",
        )

    def test_djangos_own_names_are_still_djangos(self):
        """The package replaces nothing, and this is where that is checked.

        The rows above are about a guard inside this package. This one is about
        the claim the whole rewrite rests on and the one a render cannot show,
        because the render is identical either way: ``django.template.base`` is
        Django's file and ``RustTemplates`` is not a second copy of anything.
        """
        import django.template.base as base
        import django.template.engine as engine_module

        self.assertTrue(base.__file__.endswith("django/template/base.py"), base.__file__)
        self.assertNotIn("django_rust_templates", base.__file__, base.__file__)
        self.assertEqual(engine_module.Engine.__module__, "django.template.engine")


class BackendContainmentTests(unittest.TestCase):
    """``RustTemplates`` overrides all of ``DjangoTemplates``; the subclass is a marker.

    ``DjangoTemplates``' body builds *Django's* ``Engine`` and pops ``OPTIONS``
    itself, so anything of its implementation that a subclass failed to override
    would either be reached by accident or raise. The class is kept as a base for
    exactly one reason — ``isinstance``, which ``django.contrib.admin.checks`` and
    ``django.template.autoreload`` both ask — so the containment is the whole
    contract and is asserted rather than described.
    """

    def test_every_public_attribute_of_djangotemplates_is_overridden(self):
        theirs = {name for name in vars(DjangoTemplates) if not name.startswith("__")}
        ours = set(vars(rust.RustTemplates))
        # The positive control: an empty `theirs` would make the row below pass on
        # a class that overrides nothing, which is the vacuity this file exists to
        # catch everywhere else.
        self.assertGreaterEqual(len(theirs), 6, sorted(theirs))
        self.assertEqual(theirs - ours, set())
        # ...and the row can fail. A fabricated name carried by the *same* population
        # comes back out of the same expression, so the emptiness above is a
        # measurement rather than a comparison that cannot come out otherwise. The
        # `unregistered_` prefix is this repository's rule for a name no real tree
        # contains, so the control cannot be overtaken by a later refactor.
        self.assertEqual(
            (theirs | {"unregistered_not_overridden"}) - ours,
            {"unregistered_not_overridden"},
        )

    def test_the_subclass_is_kept_for_isinstance(self):
        """The one thing the base buys, stated as a row rather than as a comment."""
        self.assertTrue(issubclass(rust.RustTemplates, DjangoTemplates))
        self.assertEqual(rust.RustTemplates.app_dirname, "templates")

    def test_the_package_root_exports_no_activation_story(self):
        """There is no `install`, no `uninstall`, no `unshimmed` and no `_shim`.

        Each of those existed to arrange a substitution that M18 deleted, and each
        would be a way for a reader to believe the package is activated by calling
        something. A row, rather than a promise, because the deletions are the kind
        of change a later refactor reintroduces by accident.
        """
        import django_rust_templates

        for gone in ("install", "uninstall", "installed", "unshimmed", "_shim"):
            with self.subTest(name=gone):
                self.assertFalse(hasattr(django_rust_templates, gone))


if __name__ == "__main__":
    unittest.main()
