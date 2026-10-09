"""Installs the Rust-backed template engine at interpreter startup.

Placed on ``PYTHONPATH`` ahead of Django's checkout by
``scripts/run_django_suite.sh``. CPython imports ``sitecustomize`` automatically
once ``site`` initialises, which happens before Django's test runner runs — so
the substitution is in place before anything imports ``django.template``.

This is how Django's own ``runtests.py`` is driven without modifying Django's
tree: the entry point stays untouched, and the engine is swapped underneath it.

Failure is fatal
----------------

CPython's ``site.execsitecustomize`` wraps this module in a ``try``/``except``,
prints one line to stderr, and continues. Left alone, that means an unimportable
``django_rust_templates`` — a venv not synced, the extension not built, a wrong
``PYTHONPATH``, ``python -S`` — lets the whole suite run against stock Django and
report ``OK``. A working shim and an absent one are then indistinguishable, which
makes the suite useless as an acceptance gate.

So when ``DJANGO_RUST_TEMPLATES=1`` is set, the silent fallback is unreachable:
any failure here exits immediately and loudly.
"""

import os
import sys
import traceback

if os.environ.get("DJANGO_RUST_TEMPLATES") == "1":
    try:
        import django_rust_templates

        django_rust_templates.install()
    except BaseException:
        traceback.print_exc()
        sys.stderr.write(
            "\nsitecustomize: DJANGO_RUST_TEMPLATES=1 but the Rust engine could "
            "not be installed.\nRefusing to run the suite against stock Django, "
            "which would report success and mean nothing.\n"
        )
        sys.stderr.flush()
        # _exit rather than sys.exit: this runs inside site initialisation, and
        # an exception here would be swallowed by the very handler above.
        os._exit(1)
