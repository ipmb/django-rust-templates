#!/usr/bin/env python
"""Run the vendored copy of Django's ``template_tests`` against this engine.

Usage::

    python django_suite/run.py                    # the whole suite
    python django_suite/run.py -v 2              # one line per test
    python django_suite/run.py template_tests.test_engine

Like ``scripts/run_django_suite.sh`` — which this replaces — the acceptance
invocation is the bare one, and its summary line is what a milestone pins. Unlike
it, this runs **the tree you are looking at**: the vendored tests import
``django_rust_templates.engine`` directly, there is no finder to install, no
``sitecustomize``, no ``PYTHONPATH`` to get wrong, and no way for the label to
resolve to Django's own modules by accident. The suite is a copy rather than a
substitution, so a green run here is a statement about this package and nothing
else.

**It is serial, and that is a measurement rather than a preference.** Django's
parallel runner cannot report a failure on CPython 3.14 without ``tblib``:
``RemoteTestResult.addFailure`` calls ``_confirm_picklable(sys.exc_info())``, a
traceback is not picklable, and the worker re-raises instead of reporting. Measured
on the pinned interpreter with no engine in the picture at all — two trivial
``SimpleTestCase`` classes, one failing, one passing, ``parallel=2`` — both workers
printed Django's own "you should install tblib" notice and the run exited 1 with
no failure report. ``tblib`` is a declared dependency of Django's test suite
(``tests/requirements/py3.txt``) that this venv does not have, which is why the
substituted runner died the same way the moment the flip made two tests fail. The
whole suite runs in about four seconds here, so serial costs nothing and buys
correct tracebacks.

**The ``__main__`` guard is load-bearing.** ``multiprocessing``'s default start
method on macOS is ``spawn``, which re-imports the main module in every worker; a
``run.py`` whose body called ``run_tests()`` at import time would re-run the entire
suite inside each worker, recursively. Measured while probing the above.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# **`HERE` and only `HERE`.** The suite's own labels are `template_tests.*` and its
# fixtures name libraries like `template_tests.templatetags.custom`, so
# `template_tests` has to be importable as a *top-level* package, which means this
# directory goes on the path. The repository root deliberately does not: adding it
# would make `django_suite.template_tests` a second spelling of the same directory
# and therefore a second module object for every file under it — the dual-identity
# hazard that ``assertIsInstance`` and ``assertRaises`` fail on silently, and
# selectively. `django_rust_templates` is imported by name and resolves through the
# venv, which points at the working tree.
sys.path.insert(0, str(HERE))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "suite_settings")


def instrument_our_templates():
    """Make ``Client`` record renders of *this engine's* templates.

    ``django.test.utils.setup_test_environment()`` installs
    ``instrumented_test_render`` — the function that sends ``template_rendered``
    — on ``Template._render`` and ``PartialTemplate._render``, naming those two
    classes the way ``django/test/utils.py:27`` imports them: ``from
    django.template import PartialTemplate, Template``. Those are *Django's* class
    objects. This engine's ``Template`` has the same ``_render`` body and is a
    different class, so the assignment lands on a class this suite never
    instantiates: measured, ``template_rendered`` was never sent,
    ``Client.store_rendered_templates`` never ran, and
    ``test_partials.PartialTagsTests.test_context_available_in_response_for_partial_template``
    read ``response.context`` as ``None``.

    **This is harness magic and belongs here rather than in the package.** It is
    a fact about Django's test client, not about the engine: ``Client`` learns
    what was rendered by patching DTL's own ``Template``, so it is blind to every
    third-party backend, and a project that wants ``response.context`` from this
    engine is asking for something Django's client does not offer any backend but
    its own.

    **What is patched is this engine's ``_render``, wrapped, not upstream's
    function object.** Django substitutes a fixed body
    (``return self.nodelist.render(context)``); this wraps whatever ``_render`` is
    already bound to. The two are identical today, and the difference is what
    happens the day they are not: substituting would let the row pass by running
    *Django's* body under this engine's name, which is the one false green a
    vendored suite exists to rule out. The signal is the whole contract, and the
    signal object is Django's either way.
    """
    from django.test.signals import template_rendered

    from django_rust_templates.engine import PartialTemplate, Template

    def instrumented(render):
        def _render(self, context):
            template_rendered.send(sender=self, template=self, context=context)
            return render(self, context)

        return _render

    for cls in (Template, PartialTemplate):
        cls._render = instrumented(cls._render)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "labels",
        nargs="*",
        default=["template_tests"],
        help="test labels (default: template_tests)",
    )
    parser.add_argument(
        "-v",
        "--verbosity",
        type=int,
        default=1,
        help="0 for the summary line only, 2 for one line per test",
    )
    parser.add_argument(
        "--serial",
        action="store_true",
        help="accepted and ignored; this runner is always serial (see the module docstring)",
    )
    args = parser.parse_args(argv)

    import django

    django.setup()

    # Before the runner installs its own instrumentation, and it does not matter
    # which order: `setup_test_environment` only ever assigns Django's classes,
    # so the two patches do not touch each other.
    instrument_our_templates()

    from django.test.runner import DiscoverRunner

    runner = DiscoverRunner(verbosity=args.verbosity, parallel=1)
    return runner.run_tests(args.labels or ["template_tests"])


if __name__ == "__main__":
    sys.exit(main())
