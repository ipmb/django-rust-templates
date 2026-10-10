"""The pinned checkout's copy of a module we have transcribed.

A transcription under ``engine`` is a second copy of a real module, so
``importlib.import_module("django.template.context")`` reaches Django's file and
never ours, and importing ours never reaches Django's. That is the point of the
two namespaces, and it is also why a parity row cannot hold both objects by
importing either one. The only way to hold both at once is to read and execute
the pinned file directly, and that is what this does.

**Under a private module name, with ``__package__`` set to the real package.**
Several of these files import relatively (``engine.py``'s ``from .base import
Template``, ``loaders/app_directories.py``'s ``from .filesystem import Loader``),
and a relative import needs a package to resolve against. What those imports bind
is *our* siblings — deliberately, and not a compromise: the only thing a caller
reads off the returned module is the class or function objects it defines
*itself*, and a class is defined by the file that lexically contains it.

``import_module`` is deliberately not used: it would hand back ours and the
comparison would be against itself, which is the failure ``tests/parity_target.py``
exists to refuse one level up.

**The module is registered in ``sys.modules`` under that private name, and the
private name is why that is safe.** Without the entry, ``inspect.getsourcefile``
on a *class* read out of the pin raises ``TypeError: is a built-in class``:
``getfile`` resolves a class through ``sys.modules[cls.__module__]``, and
``cls.__module__`` is ``pinned_base``. That reads as a bug in the guard rather
than as a fact about the target, so ``tests/test_filter_parity.py``'s guard —
which has to name a pinned class — could not use
``assert_target_is_not_rust_owned`` at all when the entry was missing. The name
is ``pinned_*``, which no real module of either tree holds and no row of
``milestone_ownership`` counts, so nothing in the tree resolves through it.
"""

import importlib.util
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT = PROJECT_ROOT / ".django"


def load_pinned(relative: str):
    """The module at ``.django/django/template/<relative>``, executed fresh.

    ``relative`` is ``engine``-relative and posix: ``context.py``,
    ``loaders/base.py``.
    """
    path = CHECKOUT / "django" / "template" / relative
    if not path.is_file():
        raise SystemExit(f"no pinned module at {path}")

    package = "django.template"
    if "/" in relative:
        package += "." + relative.rsplit("/", 1)[0].replace("/", ".")

    name = "pinned_" + relative[: -len(".py")].replace("/", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = package
    # Registered before executing, the order `importlib` itself uses, so a
    # module that imported *itself* (none does today) would find the partial
    # object rather than re-executing. Replaced on a second call for the same
    # file, which is harmless: `inspect` reads `__file__` off whichever entry is
    # current, and both name the same path.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
