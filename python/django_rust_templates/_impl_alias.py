"""The one name ``_impl`` can be reached by whether or not the shim is installed.

**The problem this exists for.** ``_impl`` is loaded under two names with
different parents: ``django.template`` under the finder, and
``django_rust_templates._impl`` without one. For most of the tree that is
harmless — a relative import is the same file under either. It is not harmless
for ``_impl/templatetags/*.py``, because the finder serves those as
``django.templatetags.*``, a **sibling** of ``django.template``: from
``django.templatetags.i18n``, ``..`` is ``django`` and ``...`` is the top level,
so *no relative spelling* reaches the engine package. Measured, as the
``ImportError`` that started M18.1::

    from .. import Library, Node, TemplateSyntaxError
    ImportError: cannot import name 'Library' from 'django'

and the spelling that would work under the finder, ``..template``, is the wrong
one standalone, where the engine package is ``..`` itself. One absolute literal
correct in both modes is the only option, so this module makes
``django_rust_templates._impl`` such a literal: ``install`` registers a finder
that resolves that whole subtree to the modules the shim actually serves.

**Why a finder and not a ``sys.modules`` loop.** The first version of this
aliased only the siblings ``_impl/__init__.py`` had already imported, and that
left ``django_rust_templates._impl.defaulttags`` to ``PathFinder``. One file,
two module objects, two ``Node`` classes — measured as **56 failures** of
``assertIsInstance(ours, django.template.defaulttags.CycleNode)``, every one of
them a node doing the right thing under the wrong identity. A finder is lazy and
total; a loop over ``sys.modules`` is neither.

**Why the mapping is read out of ``_shim`` rather than restated.**
``NAMESPACE_ROOTS`` maps ``django.templatetags`` to ``_impl/templatetags``, so the
name of ``_impl/templatetags/i18n.py`` is ``django.templatetags.i18n`` — **not**
``django.template.templatetags.i18n``, which the finder would also serve, from
the same file, as a second module object. That is not hypothetical either: the
parser reaching for the second copy while ``{% load i18n %}``'s
``import_string("django.templatetags.i18n")`` held the first is what made ten
``test_each_i18n_tag_compiles_to_our_node_class`` rows fail. Inverting the
declared table rather than copying it is what keeps a namespace move from
silently re-splitting the tree.

**Why ``insert(0, …)``.** ``sys.meta_path`` ends with ``PathFinder``, which
resolves ``django_rust_templates._impl`` against the real directory. An appended
finder is never consulted at all — the package then loads as a *second*
execution of ``_impl/__init__.py``, and the probe that found it printed
``django_rust_templates._impl`` and ``django.template`` both pointing at
``_impl/__init__.py`` while not being the same object.

**Why it is installed only in the mode that needs it.** ``install`` is called
with the name ``_impl`` was loaded under, and does nothing when that name is
already ``ALIAS``: standalone the real package resolves to itself, and a finder
that mapped a name onto itself would be a no-op with extra steps.

``_shim._served`` is deliberately *not* taught about these names. They are
therefore never displaced by ``install``/``uninstall``, which is the property
wanted: a name in this module keeps pointing at the same objects across a
shim cycle.
"""

from __future__ import annotations

import importlib
import sys

__all__ = ("ALIAS", "install")

# The stable name. A property of the package layout — `django_rust_templates`
# containing `_impl` — and not of whichever module happens to be executing.
ALIAS = "django_rust_templates.engine"


class _AliasLoader:
    """Hand back the engine's module for a name that only differs by its prefix."""

    def __init__(self, module):
        self._module = module

    def create_module(self, spec):
        # `module_from_spec` then calls `_init_module_attrs`, which with
        # `override=False` writes only attributes that are still `None` — so a
        # module that already has its `__name__`, `__spec__` and `__path__` comes
        # back untouched rather than renamed to the alias.
        return self._module

    def exec_module(self, module):
        """Nothing: the code has already run, under its own name."""

    def is_package(self, fullname):
        return hasattr(self._module, "__path__")


class _AliasFinder:
    _impl_alias = True

    def find_spec(self, fullname, path=None, target=None):
        if fullname != ALIAS and not fullname.startswith(ALIAS + "."):
            return None
        target_name = served_name(fullname[len(ALIAS) :].lstrip("."))
        module = sys.modules.get(target_name)
        if module is None:
            module = importlib.import_module(target_name)
        spec = importlib.util.spec_from_loader(fullname, _AliasLoader(module))
        spec.submodule_search_locations = getattr(module, "__path__", None)
        return spec


def served_name(relative: str) -> str:
    """The name ``_shim`` serves ``_impl/<relative>`` under.

    ``_impl``-relative posix path with dots, ``""`` for the package itself, and
    the inverse of ``_shim._relative_name``. Longest root wins, so a more deeply
    nested namespace would take precedence over ``django.template``'s empty root
    the same way ``_match_namespace`` gives it precedence in the other direction.
    """
    from django_rust_templates import _shim

    best = None
    for namespace, root in _shim.NAMESPACE_ROOTS.items():
        if not root:
            suffix = relative
        elif relative == root or relative.startswith(root + "."):
            suffix = relative[len(root) :].lstrip(".")
        else:
            continue
        if best is None or len(root) > best[0]:
            best = (len(root), namespace, suffix)
    if best is None:
        raise LookupError(f"no namespace under `_impl` serves {relative!r}")
    _, namespace, suffix = best
    return namespace if not suffix else namespace + "." + suffix


def install(loaded_as: str) -> None:
    """Make ``ALIAS`` name the package that loaded as ``loaded_as``.

    Idempotent across an ``uninstall``/``install`` cycle in a way that matters:
    re-importing ``django.template`` re-executes ``_impl/__init__.py`` and calls
    this again, and the guard is a marker attribute rather than an identity
    check, because the second call's ``_AliasFinder`` is a *different class*.
    """
    parent, _, leaf = ALIAS.rpartition(".")
    if parent in sys.modules:
        setattr(sys.modules[parent], leaf, sys.modules[loaded_as])
    if loaded_as == ALIAS:
        return
    if not any(getattr(finder, "_impl_alias", False) for finder in sys.meta_path):
        sys.meta_path.insert(0, _AliasFinder())
