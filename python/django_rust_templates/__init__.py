"""Django's template engine, reimplemented in Rust.

Importing this package does not change anything. The engine is substituted for
``django.template`` only when :func:`install` is called, or when the
``DJANGO_RUST_TEMPLATES`` environment variable is set.

Substitution is deliberately incremental: any module under
``django.template`` that this package does not yet provide falls back to
Django's own implementation, so the test suite can be run at every milestone and
report real progress rather than an all-or-nothing result. :func:`unshimmed`
reports what is still falling back; milestone M15 requires it to be empty.
"""

from __future__ import annotations

from . import _shim

__all__ = ("install", "installed", "uninstall", "unshimmed", "engine_version")


def engine_version() -> str:
    """Version of the compiled Rust engine behind this package."""
    from . import _native

    return _native.engine_version()


def install(strict: bool = False) -> None:
    """Substitute this package's implementation for ``django.template``.

    Must be called after Django is importable and before any template is used.
    ``strict`` makes a subsequent :func:`unshimmed` non-empty result an error.
    """
    _shim.install(strict=strict)


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
