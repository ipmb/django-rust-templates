"""Substitution of ``django.template`` with the Rust-backed implementation.

How it works
------------

A ``MetaPathFinder`` takes over the single name ``django.template`` and loads
``django_rust_templates/_impl/__init__.py`` in its place, with a ``__path__``
that searches our implementations *first* and the real Django template directory
*second*.

Because submodules resolve through that ``__path__``, any module we have not
written yet is served by Django's own code, and because Django's modules are
still loaded under their real names (``django.template.base`` and so on), our
modules are interposed for Django's internal imports too — not just for direct
imports. That is what lets us reimplement one module at a time.

A meta-path finder rather than ``sys.modules`` assignment, because Django's own
internals import these modules by path, and dict assignment does not intercept
submodule imports.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

TARGET = "django.template"

_IMPL_DIR = Path(__file__).parent / "_impl"
_IMPL_INIT = _IMPL_DIR / "__init__.py"

_installed_finder: "_ShimFinder | None" = None


class ShimError(RuntimeError):
    """Raised when substitution cannot be set up correctly."""


class _ShimFinder(importlib.abc.MetaPathFinder):
    def __init__(self, real_paths: list[str]) -> None:
        self.real_paths = real_paths

    def find_spec(self, fullname, path=None, target=None):
        if fullname != TARGET:
            return None
        if not _IMPL_INIT.is_file():
            raise ShimError(f"expected implementation package at {_IMPL_INIT}")
        return importlib.util.spec_from_file_location(
            fullname,
            _IMPL_INIT,
            loader=importlib.machinery.SourceFileLoader(fullname, str(_IMPL_INIT)),
            # Ours first so our modules win; Django's second so anything we have
            # not written yet still resolves.
            submodule_search_locations=[str(_IMPL_DIR), *self.real_paths],
        )


def _real_search_paths() -> list[str]:
    """Search paths of Django's own template package.

    Must be called while the real package is imported, or not yet imported.
    """
    try:
        real = importlib.import_module(TARGET)
    except ImportError as exc:  # pragma: no cover - Django is a hard dependency
        raise ShimError(f"cannot import {TARGET}: {exc}") from exc

    paths = [str(p) for p in getattr(real, "__path__", [])]
    if not paths:
        raise ShimError(f"{TARGET} has no __path__; is it a package?")
    if str(_IMPL_DIR) in paths:
        raise ShimError("already shimmed; uninstall before reinstalling")
    return paths


def _purge() -> None:
    """Drop cached modules so the next import goes through the finder."""
    for name in list(sys.modules):
        if name == TARGET or name.startswith(TARGET + "."):
            del sys.modules[name]


def install(strict: bool = False) -> None:
    global _installed_finder

    if installed():
        return

    # Capture Django's own paths before we take the name over.
    real_paths = _real_search_paths()

    finder = _ShimFinder(real_paths)
    sys.meta_path.insert(0, finder)
    _purge()
    _installed_finder = finder

    importlib.import_module(TARGET)

    if strict:
        remaining = unshimmed()
        if remaining:
            raise ShimError(
                "strict mode: still served by Django's own code: "
                + ", ".join(remaining)
            )


def uninstall() -> None:
    global _installed_finder

    if _installed_finder is not None:
        try:
            sys.meta_path.remove(_installed_finder)
        except ValueError:
            pass
        _installed_finder = None

    _purge()
    importlib.import_module(TARGET)


def installed() -> bool:
    return any(isinstance(f, _ShimFinder) for f in sys.meta_path)


def unshimmed() -> list[str]:
    """Modules under ``django.template`` still served by Django's own code.

    This is the project's progress meter: it is empty exactly when every module
    under ``django.template`` is provided by this package.

    Computed by comparing the two source trees rather than by importing, so it
    has no side effects — importing Django's template modules can require
    configured settings, and a progress meter must be safe to call anywhere.
    """
    if not installed():
        raise ShimError("not installed; call install() first")

    assert _installed_finder is not None
    ours = _dotted_module_names(_IMPL_DIR)
    theirs: set[str] = set()
    for real_path in _installed_finder.real_paths:
        theirs |= _dotted_module_names(Path(real_path))

    return sorted(f"{TARGET}.{name}" for name in theirs - ours)


def _dotted_module_names(root: Path) -> set[str]:
    """Dotted suffixes of every module and package under ``root``.

    ``root/base.py`` yields ``base``; ``root/loaders/app_directories.py`` yields
    ``loaders.app_directories``; ``root/loaders/__init__.py`` makes ``loaders`` a
    package rather than a module, but either way the name is ``loaders``.
    """
    names: set[str] = set()
    if not root.is_dir():
        return names

    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        parts = list(relative.parts)

        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][: -len(".py")]

        if not parts:
            # The root package itself, which is ours by construction.
            continue
        names.add(".".join(parts))

    return names
