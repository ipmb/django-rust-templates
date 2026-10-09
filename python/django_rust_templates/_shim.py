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
        relative = _relative_name(fullname)
        if relative is None:
            return None
        if relative == "" and not _IMPL_INIT.is_file():
            raise ShimError(f"expected implementation package at {_IMPL_INIT}")

        base = _IMPL_DIR if not relative else _IMPL_DIR.joinpath(*relative.split("."))

        # A package we provide needs its own search path — ours first, Django's
        # second. Returning a spec without one would leave the package with
        # `__path__ == [our directory]` only, so the first time a half-finished
        # subpackage exists (say `_impl/loaders/__init__.py`), every sibling
        # (`django.template.loaders.filesystem`) stops resolving entirely. The
        # fallback has to be reapplied at every level, not just the top.
        init = base / "__init__.py"
        if init.is_file():
            return importlib.util.spec_from_file_location(
                fullname,
                init,
                loader=importlib.machinery.SourceFileLoader(fullname, str(init)),
                submodule_search_locations=[
                    str(base),
                    *_real_subdirs(relative, self.real_paths),
                ],
            )

        module = base.with_suffix(".py")
        if module.is_file():
            return importlib.util.spec_from_file_location(
                fullname,
                module,
                loader=importlib.machinery.SourceFileLoader(fullname, str(module)),
            )

        # Not ours: fall through to PathFinder, which resolves it against the
        # parent's __path__ — the one we built above.
        return None


def _relative_name(fullname: str) -> str | None:
    """``django.template.loaders`` → ``loaders``; the package itself → ``""``.

    Returns ``None`` for anything outside the ``django.template`` namespace,
    which is everything this finder must leave alone.
    """
    if fullname == TARGET:
        return ""
    if fullname.startswith(TARGET + "."):
        return fullname[len(TARGET) + 1:]
    return None


def _real_subdirs(relative: str, real_paths: list[str]) -> list[str]:
    """Django's own directories corresponding to one of our packages."""
    parts = relative.split(".") if relative else []
    return [
        str(candidate)
        for real_path in real_paths
        if (candidate := Path(real_path).joinpath(*parts)).is_dir()
    ]


def _real_search_paths() -> list[str]:
    """Search paths of Django's own template package.

    Uses ``find_spec`` rather than ``import_module``: the spec carries
    ``submodule_search_locations`` without executing the package, so nothing is
    imported merely to discover where Django's code lives, and no module that
    already bound a name via ``from django.template import X`` is left holding a
    stale reference to a package we then discard.
    """
    try:
        spec = importlib.util.find_spec(TARGET)
    except (ImportError, ValueError) as exc:  # pragma: no cover - hard dependency
        raise ShimError(f"cannot locate {TARGET}: {exc}") from exc

    if spec is None:  # pragma: no cover - Django is a hard dependency
        raise ShimError(f"cannot locate {TARGET}")

    paths = [str(p) for p in (spec.submodule_search_locations or [])]
    if not paths:
        raise ShimError(f"{TARGET} has no search locations; is it a package?")
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
