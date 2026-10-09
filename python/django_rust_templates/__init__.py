"""Django's template engine, reimplemented in Rust.

Importing this package does not change anything. The engine is substituted for
``django.template`` only when :func:`install` is called.

**Calling :func:`install` is the whole of the activation story for an installed
package, and this docstring used to say otherwise.** It read "…or when the
``DJANGO_RUST_TEMPLATES`` environment variable is set", which is true of this
repository and false of the wheel: the only reader of that variable is
``scripts/shimpath/sitecustomize.py``, which lives in the source tree, is not
shipped, and needs ``scripts/shimpath`` on ``PYTHONPATH``. A claim in a shipped
docstring that the shipped artifact does not honour is worse than no claim — it
is the reader's first instruction, and it fails silently. So, from an installed
wheel, the single way in is to call :func:`install` from your *entry point* —
``manage.py``, ``wsgi.py``, ``asgi.py`` — before Django is imported.

**Not from your settings module, and not from an ``AppConfig.ready()``.** Both
were recommended here until they were measured against a real project, and both
are too late by construction: ``django.setup()`` runs
``from django.urls import set_script_prefix`` before it reads the settings
module (the settings module is read on the ``configure_logging`` line that
follows) and before it calls ``apps.populate`` (which is what invokes
``ready()``), and ``django.urls`` imports every module under ``django.template``
on the way in. :func:`install` refuses rather than silently accepting a
half-substituted interpreter, so the failure is loud — but the instruction that
produced it should not have been there.

Substitution is deliberately incremental: any module under
``django.template`` that this package does not yet provide falls back to
Django's own implementation, so the test suite can be run at every milestone and
report real progress rather than an all-or-nothing result. :func:`unshimmed`
reports what is still falling back; it is empty as of milestone M14.
"""

from __future__ import annotations

from . import _shim

__all__ = ("install", "installed", "uninstall", "unshimmed", "engine_version")


def engine_version() -> str:
    """Version of the compiled Rust engine behind this package."""
    from . import _native

    return _native.engine_version()


def install(strict: bool = False, allow_django_mismatch: bool = False) -> None:
    """Substitute this package's implementation for ``django.template``.

    Must be called once Django is importable and before ``django.template`` is
    imported — in a Django project, from the entry point and ahead of
    ``django.setup()``. See the module docstring for why the settings module is
    too late. Raising a :class:`ShimError` is the intended outcome when called
    after the fact; see ``_shim._stale_captures`` for what it detects.

    ``strict`` makes a subsequent :func:`unshimmed` non-empty result an error.

    ``allow_django_mismatch`` skips the check that the installed Django is the
    one this engine was transcribed from. That check is on by default and
    refuses anything outside ``6.0.x``; it exists because a mismatched Django can
    otherwise install cleanly and pass every gate this project has, which is
    exactly the failure the guard is for. Pass it only to *test* a transcription
    against a Django it was not taken from.
    """
    _shim.install(strict=strict, allow_django_mismatch=allow_django_mismatch)


def uninstall() -> None:
    """Undo :func:`install`, restoring Django's own implementation."""
    _shim.uninstall()


def installed() -> bool:
    """Whether this package is currently substituted for ``django.template``."""
    return _shim.installed()


def unshimmed() -> list[str]:
    """Modules of *the engine* under ``django.template`` still Django's own code.

    Not every module under ``django.template``: the ones §3 declares are not the
    engine — the backend contract, the loading facade, responses and the two
    pieces of glue — are subtracted, because they will never be ours.
    """
    return _shim.unshimmed()
