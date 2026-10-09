"""The pinned checkout's copy of a module we have interposed.

A transcription under ``_impl`` wears a real module's name, so
``importlib.import_module("django.template.context")`` cannot reach Django's file
— that is the whole point of the shim. The only way to hold both objects at once
is to read and execute the pinned file directly, and that is what this does.

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
"""

import importlib.util
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT = PROJECT_ROOT / ".django"


def load_pinned(relative: str):
    """The module at ``.django/django/template/<relative>``, executed fresh.

    ``relative`` is ``_impl``-relative and posix: ``context.py``,
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
    spec.loader.exec_module(module)
    return module
