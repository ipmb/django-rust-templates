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
**a file under ``_impl`` may take a real module's name only when it implements
that module — any other file must be named for something Django does not have.**

The prohibition is on *what the file is*, not on the name, and the first draft
of this paragraph got that wrong. It read "no ``_impl`` file may take the name
of a real ``django.template`` module", which the tree already contradicted four
times on the day it was written: ``__init__.py``, ``base.py`` and ``smartif.py``
each wear a real module's name *because interposing that module is the whole
mechanism*, and ``exceptions.py`` (M0 bootstrap) wears one as our own
implementation of it. Under the literal rule the entire shipped mechanism was a
defect.

So the set is declared rather than prohibited. :data:`SHADOWING` names every
``_impl`` file whose name collides with a module of the pinned checkout, and
``tests/test_interposition.py`` asserts that the files on disk are exactly its
keys. A new collision then costs one deliberate, reviewable line; an accidental
one — the ``render.py``-at-``engine.py`` case — is a test failure in the commit
that causes it, rather than an ``ImportError`` that blames Django three files
away. The table's ``kind`` is checked against the guard that owns each kind's
question — ``scripts/check_transcriptions.py`` for a transcription's diff against
the pin, ``tests/test_transcription_equivalence.py`` for a fresh file's stated
equivalence — so it cannot rot into a second, disagreeing account of which files
are transcriptions.

The shell modules that only need Rust and the real Django live outside ``_impl``
entirely (``django_rust_templates/render.py``), and the ones that must be inside
are named for what Django does not have (``parser.py``, whose sibling
``django/template/parser.py`` does not exist at 6.0.9 — if it ever appears, that
name starts shadowing too, and :func:`shadowing_files` will say so).
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

# The `_impl` files whose name is also the name of a module in the pinned
# checkout — every one of them an interposition, and this table is the record
# that each was meant. Keyed by path relative to `_impl`; the value is how the
# file came to exist:
#
#     "transcribed"  copied from Django's module of the same name and differing
#                    only where it declares, carrying a `# TRANSCRIBED:` marker
#     "fresh"        written from scratch, implementing the same module
#
# The kind is not free text. `tests/test_interposition.py` checks each one
# against the file's own marker, so "transcribed" without a marker — or "fresh"
# with one — is a failure, and this table cannot drift into a second account of
# which files are transcriptions. It is also deliberately *not* a list of every
# `_impl` file: `parser.py` is absent because no `django/template/parser.py`
# exists to collide with, and a file that collides without being listed here is
# exactly what the table exists to catch.
#
# **The two kinds carry different obligations, and both are checked.** A
# `transcribed` file is falsified by `scripts/check_transcriptions.py`: the diff
# against the pin is its instrument. A `fresh` file has no diff of its own, so
# its instrument is a stated equivalence —
# `tests/test_transcription_equivalence.py`'s `EQUIVALENCE` — and
# `ShadowingTests.test_each_kind_is_the_transcription_guards_own_answer` refuses
# a `fresh` name that is missing from it. Without that second clause `fresh` is
# the free option: changing one word here would move a file out of every guard in
# the tree with everything green, which is M9's finding 3 one level up.
SHADOWING = {
    "__init__.py": "transcribed",
    "base.py": "transcribed",
    "exceptions.py": "fresh",
    "smartif.py": "transcribed",
    "utils.py": "transcribed",
}


def _django_template_dirs() -> list[str]:
    """Django's own template directories, from wherever they are knowable.

    The installed finder is preferred, because it holds the paths captured at
    :func:`install` — asking it answers precisely the question the finder answers
    when it resolves a name. Before installation there is no finder, so ask
    :func:`_real_search_paths`, which is what :func:`install` does itself.
    """
    if _installed_finder is not None:
        return _installed_finder.real_paths
    return _real_search_paths()


def shadowing_files() -> list[str]:
    """``_impl`` files whose name the pinned checkout also defines.

    The question is the finder's own — *would this file be served under a name
    Django already has?* — asked of the filesystem against Django's template
    directories, so it stays true as modules are added rather than being a
    second, hand-maintained list that can disagree (the failure
    ``.review/m10-recon.md`` §2 found in the milestone table).

    Paths are relative to ``_impl`` and posix-style, so a subpackage reports
    ``loaders/__init__.py``. ``__pycache__`` is skipped: it holds no module
    anyone wrote.
    """
    real_paths = _django_template_dirs()
    clashes = []
    for path in sorted(_IMPL_DIR.rglob("*.py")):
        relative = path.relative_to(_IMPL_DIR)
        if "__pycache__" in relative.parts:
            continue
        if any(Path(real).joinpath(relative).is_file() for real in real_paths):
            clashes.append(relative.as_posix())
    return clashes


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


_CAPTURE_NAMESPACES = ("django.template", "django.templatetags")


def _in_capture_namespace(name: str) -> bool:
    """Is ``name`` a module whose globals can hold a class from ours?"""
    return any(
        name == namespace or name.startswith(namespace + ".")
        for namespace in _CAPTURE_NAMESPACES
    )


def _stale_captures() -> list[str]:
    """Bindings in ``sys.modules`` that name a class the defining module lost.

    **The hazard.** ``from django.template.base import Node`` is a copy taken
    when the importing module is executed, and nothing done to
    ``django.template.base`` afterwards can change it. A module executed while
    *Django's* template modules were current keeps Django's ``Node`` for the rest
    of the process, so after we interpose ``base`` that module builds Django
    nodes while everything else builds ours. Measured, in the two shapes the
    design spec names: a captured **class** makes every ``isinstance`` false
    (``django/template/defaulttags.py:30-38`` takes nine names including ``Node``
    and ``NodeList``), and a captured **enum member** makes every comparison
    false (``django/templatetags/i18n.py:5`` imports ``TokenType``). A captured
    *function* is the mild one — stale but working.

    **Why this and not a precondition on ``sys.modules``.** Refusing whenever a
    ``django.template*`` module is already imported would refuse the ordinary
    ``uninstall()``/``install()`` cycle, where the modules we serve come back
    *from the cache* — the same objects, so their captures are still valid. What
    is actually wrong is narrower, and it is a property of the binding rather
    than of the module's presence: a global whose value is no longer what its
    defining module binds. So this compares identities, after the substitution
    has been made, and reports only the bindings that are now pointing at a
    universe nothing else is in.

    Compared **by name, not by the local spelling**: ``import Node as N`` is
    legal, and the local attribute name then says nothing about which name in the
    defining module the copy came from.
    """
    stale = []
    for name, module in list(sys.modules.items()):
        if module is None or not _in_capture_namespace(name):
            continue
        if _served(name) or _is_ours(module):
            continue
        for attr, value in list(vars(module).items()):
            owner = getattr(value, "__module__", None)
            if not isinstance(owner, str) or not _served(owner):
                continue
            defining = sys.modules.get(owner)
            if defining is None:
                continue
            value_name = getattr(value, "__name__", None)
            if value_name is None:
                continue
            if getattr(defining, value_name, None) is not value:
                stale.append(
                    f"{name}.{attr} is a {value_name} from before the "
                    f"substitution, while {owner}.{value_name} is ours"
                )
    return stale


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

    # The substitution is in place, so a capture from the previous universe is
    # now tellable from a live binding — see `_stale_captures`. Undo before
    # raising, so a caller that catches this is left where it started rather
    # than half-interposed.
    stale = _stale_captures()
    if stale:
        uninstall()
        raise ShimError(
            "these modules imported names from Django's template modules before "
            "this package was installed, and nothing can rebind a copy — "
            "install before importing `django.template`, or start a fresh "
            "interpreter:\n  " + "\n  ".join(stale)
        )

    if strict:
        remaining = unshimmed()
        if remaining:
            raise ShimError(
                "strict mode: still served by Django's own code: "
                + ", ".join(remaining)
            )


def uninstall() -> None:
    global _installed_finder

    # **Not a no-op to skip.** With the finder already out, the swap below runs
    # against Django's own modules and files them under the *ours* cache — so the
    # next `install()` places Django's `base` under our name, and every module
    # holding *our* `Node` is then reported stale by the check above, because the
    # binding it is compared against has become Django's. Measured in
    # `test_shim`'s own run: 24 bindings reported, each of them correct until a
    # `uninstall()` was issued for a shim that was already out. `test_shim`'s
    # `tearDown` uninstalls and its rows call `uninstall()` themselves, so the
    # redundant call is the ordinary path here, not a corner.
    if _installed_finder is None:
        return

    try:
        sys.meta_path.remove(_installed_finder)
    except ValueError:
        pass
    _installed_finder = None

    # Mirror of `install`: our served modules go back in the cache, Django's own
    # come back out of it. The non-served modules were never removed, so they
    # are still the objects every other module is holding.
    #
    # Everything served that is in `sys.modules` here is ours: `install` places
    # our copy of every served name it has cached, and anything it has not is
    # imported through the finder. The guard above is what keeps that true —
    # without it this loop files Django's modules under the *ours* cache.
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

    ours = _dotted_module_names(_IMPL_DIR)
    return sorted(f"{TARGET}.{name}" for name in django_modules() - ours)


def django_modules() -> set[str]:
    """Every name Django's own template package defines.

    The universe both the meter and the ownership table are counted over — 26
    names at 6.0.9. Read from Django's directories rather than from a list here,
    because the failure it exists to catch is a name nobody thought about:
    `autoreload`, `context_processors` and `loader` were absent from every
    milestone row precisely because no list mentioned them
    (`.review/m10-recon.md` §2).

    Callable before ``install()``, which is what lets
    `tests/test_milestone_ownership.py` state the partition without depending on
    the order the suite happens to install the shim in: `_django_template_dirs`
    falls back to `_real_search_paths`, which is the same set of paths the finder
    captures at install.
    """
    names: set[str] = set()
    for real_path in _django_template_dirs():
        names |= _dotted_module_names(Path(real_path))
    return names


def dotted_suffix(relative: str) -> str:
    """``_impl``-relative posix path → the dotted suffix it is served under.

    ``base.py`` → ``base``; ``loaders/__init__.py`` → ``loaders``;
    ``loaders/base.py`` → ``loaders.base``; ``__init__.py`` → ``""``, the
    package itself, which is the one name no table in this tree carries.

    One function rather than four spellings: the file path is how a table finds
    the file, the dotted name is how :func:`unshimmed` and the ownership table
    count it, and a derivation written twice is how ``loaders/base.py`` becomes
    ``loaders.base.py`` in one of them.
    """
    parts = relative.split("/")
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts)


def _dotted_module_names(root: Path) -> set[str]:
    """Dotted suffixes of every module and package under ``root``.

    ``root/base.py`` yields ``base``; ``root/loaders/app_directories.py`` yields
    ``loaders.app_directories``; ``root/loaders/__init__.py`` makes ``loaders`` a
    package rather than a module, but either way the name is ``loaders``. The
    derivation is :func:`dotted_suffix`'s, so the walk and the tables agree by
    construction.
    """
    names: set[str] = set()
    if not root.is_dir():
        return names

    for path in root.rglob("*.py"):
        name = dotted_suffix(path.relative_to(root).as_posix())
        if name:
            # The root package itself, which is ours by construction.
            names.add(name)

    return names
