"""Installs the Rust-backed template engine at interpreter startup.

Placed on ``PYTHONPATH`` ahead of Django's checkout by
``scripts/run_django_suite.sh``. CPython imports ``sitecustomize`` automatically
once ``site`` initialises, which happens before Django's test runner runs — so
the substitution is in place before anything imports ``django.template``.

This is how Django's own ``runtests.py`` is driven without modifying Django's
tree: the entry point stays untouched, and the engine is swapped underneath it.
"""

import os

if os.environ.get("DJANGO_RUST_TEMPLATES") == "1":
    import django_rust_templates

    django_rust_templates.install()
