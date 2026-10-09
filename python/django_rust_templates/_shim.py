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

A naming constraint on ``_impl``
--------------------------------

Because the finder serves **any** ``_impl/<name>.py`` as ``django.template.
<name>``, a file placed there under the name of a real Django module shadows it
— for Django's internal imports as much as for anyone else's. Measured, and it
is not hypothetical: putting the shell's ``render.py`` at ``_impl/engine.py``
(tempting, since it builds an ``Engine``) makes ``_impl/__init__.py``'s own
``from .engine import Engine`` resolve to that file, and ``install()`` then
fails with::

    ImportError: cannot import name 'Engine' from 'django.template.engine'

The error names Django, not the shim, which is why the rule is stated here:
**no ``_impl`` file may take the name of a real ``django.template`` module.**
The shell modules that only need Rust and the real Django live outside
``_impl`` entirely (``django_rust_templates/render.py``), and the ones that
must be inside are named for what Django does not have (``parser.py``, whose
sibling ``django/template/parser.py`` does not exist at 6.0.9 — if it ever
appears, that name starts shadowing too).
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


def _served(fullname: str) -> bool:
    """Would the finder serve ``fullname`` from ``_impl``?

    Asked of the filesystem rather than of the finder, because the callers run
    while the finder is not installed. It has to answer exactly what
    ``_ShimFinder.find_spec`` answers, or the two disagree about which
    ``sys.modules`` entries are ours — a package we provide is an
    ``__init__.py``, anything else a ``.py``.
    """
    relative = _relative_name(fullname)
    if relative is None:
        return False
    base = _IMPL_DIR if not relative else _IMPL_DIR.joinpath(*relative.split("."))
    return (base / "__init__.py").is_file() or base.with_suffix(".py").is_file()


def _is_ours(module) -> bool:
    """Was ``module`` executed from ``_impl``?"""
    return str(getattr(module, "__file__", "") or "").startswith(str(_IMPL_DIR))


# Modules we have executed, kept across install/uninstall cycles, and Django's
# own modules we displaced for the names the finder serves.
#
# **This cache is a correctness requirement, not a speed one.** ``install()``
# and ``uninstall()`` used to drop every ``django.template`` module and import
# it again, which re-executes the file: a module that was already executed
# yields a *new* module object with *new* class objects, while anything that
# captured the old ones keeps holding them. Two failures, both measured in one
# session after a single ``test_shim`` cycle:
#
#     render.TemplateSyntaxError is django.template.exceptions.TemplateSyntaxError
#     -> False
#     host.django_own_tags()["if"] is defaulttags.register.tags["if"] -> False
#
# The first makes `assertRaises(TemplateSyntaxError)` miss the exception
# `render.django_exception` raises; the second makes `ShellHost.has_tag` decide
# that `{% if %}` is a tag Django owns and hand it back to Python's `do_if`, so
# Rust stops parsing the condition. Neither is visible in the accepted run, and
# the reason is ordering: `run_django_suite.sh` installs once at interpreter
# start, and `unittest discover` happens to run `test_interposition` before
# `test_shim` because "i" sorts before "s". A row that is green because of the
# alphabet is the failure this project keeps finding.
#
# So a module is executed at most once per process. Non-served modules are not
# touched at all across a cycle, and served ones are put back from the cache.
_our_modules: dict[str, object] = {}
_django_modules: dict[str, object] = {}


def _place(name: str, module) -> None:
    """Put ``module`` in ``sys.modules[name]`` *and* on its parent package.

    The second half is not optional. ``import django.template`` binds the name
    ``django`` and the caller then reads ``django.template`` — the **attribute
    on the parent**, which the import system sets once, when the submodule is
    first loaded, and which a bare ``sys.modules`` assignment never touches.
    Restoring from the cache without this leaves ``sys.modules["django.template"]``
    ours while ``django.template`` is still Django's, and the symptom is a
    ``test_internal_imports_are_interposed`` failure that names the file rather
    than the mechanism. The old purge-and-reimport did not need this because the
    re-import ran the import machinery, which set the attribute as a side effect.

    Parents are placed before children by the caller's ordering, so a child's
    parent attribute lands on the module that is already in ``sys.modules``.
    """
    sys.modules[name] = module
    parent, _, leaf = name.rpartition(".")
    if parent in sys.modules:
        try:
            setattr(sys.modules[parent], leaf, module)
        except (AttributeError, TypeError):  # pragma: no cover - read-only parent
            pass


def _by_depth(names) -> list[str]:
    """``names`` shortest-first, so a parent exists before its children."""
    return sorted(names, key=lambda name: (name.count("."), name))


def install(strict: bool = False) -> None:
    global _installed_finder

    if installed():
        return

    # Capture Django's own paths before we take the name over.
    real_paths = _real_search_paths()

    finder = _ShimFinder(real_paths)
    sys.meta_path.insert(0, finder)
    _installed_finder = finder

    # Displace only the names the finder serves. Everything else under
    # `django.template` is Django's own code and stays exactly as it is —
    # dropping it here is what re-executed `defaulttags` and invalidated
    # `django_own_tags()`.
    for name in list(sys.modules):
        if not _served(name):
            continue
        module = sys.modules[name]
        if _is_ours(module):
            _our_modules[name] = module
        else:
            _django_modules[name] = module
        del sys.modules[name]

    for name in _by_depth(_our_modules):
        _place(name, _our_modules[name])

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

    # Mirror of `install`: our served modules go back in the cache, Django's own
    # come back out of it. The non-served modules were never removed, so they
    # are still the objects every other module is holding.
    for name in list(sys.modules):
        if _served(name):
            _our_modules[name] = sys.modules[name]
            del sys.modules[name]
    for name in _by_depth(_django_modules):
        if _served(name):
            _place(name, _django_modules[name])

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
