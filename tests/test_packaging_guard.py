"""The packaging guard: an installed wheel refuses a Django it was not proven against.

Why this file exists
--------------------

The engine is a *transcription* of one Django, pinned at 6.0.9 in §4 of the
design and in ``django.lock``. Everything the project checks — the acceptance
suite, the differential harness, the meter — is run against that checkout, so
none of it can speak about any other Django. Before this guard existed, two
measured outcomes fell out of that gap:

* **Django 6.1.2** — ``install()`` succeeded, ``unshimmed()`` was ``[]``, and a
  template rendered. Every gate the project has says green, on a transcription
  taken from a version that is not the one running. This is the dangerous
  direction, and it is silent.
* **Django 5.2.7** — ``install()`` died with ``cannot import name
  'django_file_prefixes' from 'django.utils.deprecation'``, an ``ImportError``
  that names Django for a problem that is ours. ``_shim``'s own module docstring
  already warns about this shape — the ``render.py``-at-``engine.py`` case, where
  "the error names Django, not the shim".

So the guard is not a convenience: it is the thing that makes the *rest* of this
project's evidence mean something outside the pinned tree.

The rows are over one population
--------------------------------

Every row below patches ``django.VERSION``, which is the same input the guard
reads, and the first row is the **positive control** — the transcription pin
itself, which must *not* raise. Without it, a guard that raised unconditionally
would satisfy every other row here, and the class does not otherwise assert that
the check has a passing branch at all.
"""

import sys
import unittest
from unittest import mock

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

import django  # noqa: E402  (path must be set up first)

from django_rust_templates import _shim  # noqa: E402


class DjangoVersionGuardTests(unittest.TestCase):
    """State discipline is ``test_shim``'s: restore the session, do not uninstall.

    ``run_unit_tests.sh`` installs the shim at interpreter start and every
    module discovered after ``test_shim`` depends on it, so a row that leaves the
    shim out quietly changes what those rows are testing. ``setUp`` captures the
    baseline and ``tearDown`` puts it back.
    """

    def setUp(self):
        self._installed_at_setup = _shim.installed()

    def tearDown(self):
        if self._installed_at_setup and not _shim.installed():
            _shim.install()
        elif not self._installed_at_setup and _shim.installed():
            _shim.uninstall()

    def test_the_guard_accepts_the_transcription_pin(self):
        """The positive control, and the row that keeps the others honest.

        The subject is the *pin* rather than whatever this checkout happens to
        be, so the row is a statement about the guard and not about the
        environment — the three rows below read the same attribute.
        """
        _shim.uninstall()
        with mock.patch.object(django, "VERSION", (6, 0, 9, "final", 0)):
            _shim.install()
        self.assertTrue(_shim.installed(), "the guard refused its own pin")

    def test_the_guard_refuses_a_later_django(self):
        """6.1.2 is the silent case, and the one the guard exists for.

        It used to install and render. What is asserted is the refusal *and*
        both version numbers in the message, because a refusal that does not
        name what it found sends the reader to the dependency resolver rather
        than to the pin.
        """
        _shim.uninstall()
        with mock.patch.object(django, "VERSION", (6, 1, 2, "final", 0)):
            with self.assertRaises(_shim.ShimError) as ctx:
                _shim.install()
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
        _shim.uninstall()
        with mock.patch.object(django, "VERSION", (5, 2, 7, "final", 0)):
            with self.assertRaises(_shim.ShimError) as ctx:
                _shim.install()
        message = str(ctx.exception)
        self.assertIn("6.0.9", message)
        self.assertIn("5.2.7", message)

    def test_a_refused_install_leaves_the_interpreter_untouched(self):
        """The check runs *before* the finder is inserted, not after.

        This is the property that makes the refusal safe to catch-and-report: a
        caller told "no" is left exactly where it started. It is asserted
        separately from the raise because the raise alone does not imply it —
        an install that checked at the end would raise with a finder already
        standing in ``sys.meta_path``.
        """
        _shim.uninstall()
        with mock.patch.object(django, "VERSION", (6, 1, 2, "final", 0)):
            with self.assertRaises(_shim.ShimError):
                _shim.install()
        self.assertFalse(_shim.installed(), "a refused install left a finder in place")
        self.assertFalse(
            any(isinstance(f, _shim._ShimFinder) for f in sys.meta_path),
            "a refused install inserted a finder by another route",
        )

    def test_the_escape_hatch_installs_against_a_mismatched_django(self):
        """A wall with no door would be worse than the gap it closes.

        The project itself will want to run against the next Django before it
        transcribes it, and the pin is a claim about *proven* behaviour rather
        than about what is permitted. The hatch is exercised on the same
        mismatched input the row above refuses, so the two differ in one thing.
        """
        _shim.uninstall()
        with mock.patch.object(django, "VERSION", (6, 1, 2, "final", 0)):
            _shim.install(allow_django_mismatch=True)
        self.assertTrue(_shim.installed(), "the hatch did not open")


# **The absent-Django row is not here, and its absence is deliberate.** The
# obvious lever — ``mock.patch.dict(sys.modules, {"django": None})`` — is a trap
# that was measured in this file's first draft: ``patch.dict`` snapshots the
# *whole* mapping on entry and on exit does ``clear()`` followed by
# ``update(snapshot)``, so every ``django.template`` module object created or
# re-placed inside the block is thrown away and the table is restored to a
# snapshot. ``_stale_captures`` then reports bindings pointing into a universe
# nothing else is in, and the damage lands on *later* rows: in the first draft
# it turned the positive control and both refusal rows into stale-capture
# failures, none of which named the thing they tested. A table-wide patch is not
# a narrow lever whatever it is spelled like.
#
# The behaviour is real and stays covered, at the population where it actually
# occurs: a fresh venv with this wheel and no Django at all, in
# ``scripts/check_packaging.sh``. That is also the only place it can be honestly
# asserted — ``install()`` here runs beside a Django that *is* importable, so
# patching it away measures the patch.


if __name__ == "__main__":
    unittest.main()
