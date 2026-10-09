"""Pre-flight for the acceptance suite: the engine is installed and complete.

`run_django_suite.sh` runs this before spending a suite run, and refuses to
continue if it fails. Two claims are checked, and together they are "no fallback":

* **runtime** — every module the engine owns, imported, resolves to our file
  under `engine`, not to Django's;
* **file level** — `unshimmed()` is empty in both scopes, i.e. no name Django's
  package defines lacks a same-named file in `engine`.

**Correctness is dated to the engine being complete.** Refusing on a non-empty
meter would have blocked every suite run through M10–M13, when the meter was
non-empty by design. It is right *now* because the engine is finished — and it
will refuse on the first commit of any future milestone that interposes a new
engine module. That refusal is this script doing its job, not a regression; if you
are reading this because a milestone run stopped here, the milestone is the thing
to look at.

The runtime half imports every engine module, which `unshimmed()` deliberately
does not (it may be called anywhere, and importing can require settings). The
pre-flight has no such constraint — it runs once, immediately before the suite, in
the same interpreter the suite uses.
"""

import sys


def engine_fallbacks(unshimmed, scopes):
    """``{scope: names}`` for every scope whose meter is non-empty.

    Pure, so a test can drive it with a fabricated meter: the guard's only other
    exercise is a healthy run, which cannot show that it *would* refuse.
    """
    return {scope: names for scope in scopes if (names := unshimmed(scope))}


def runtime_fallbacks(import_module, django_modules, not_the_engine, scopes):
    """``{module: file}`` for every engine module not imported from under ``engine``.

    Total over the engine: the population is `django_modules(scope) - not_the_engine`
    — the same call the meter makes — so a module a version bump adds to Django's
    package is imported and checked rather than passing unseen.
    """
    wrong = {}
    for scope in scopes:
        for name in django_modules(scope) - not_the_engine:
            dotted = f"{scope}.{name}"
            module = import_module(dotted)
            file = getattr(module, "__file__", "") or ""
            if "engine" not in file:
                wrong[dotted] = file or "<no __file__>"
    return wrong


def main() -> int:
    import importlib

    from django_rust_templates import _shim

    if not _shim.installed():
        sys.stderr.write("shim is not installed; refusing to run the suite\n")
        return 1

    wrong = runtime_fallbacks(
        importlib.import_module,
        _shim.django_modules,
        _shim.NOT_THE_ENGINE,
        _shim.NAMESPACES,
    )
    if wrong:
        for dotted, file in sorted(wrong.items()):
            sys.stderr.write(f"{dotted} resolved to {file}, not to our implementation\n")
        sys.stderr.write(
            "refusing to run the acceptance suite: the engine is not fully installed.\n"
        )
        return 1

    # Both scopes, derived from the shim's own table rather than respelled, so the
    # checked population cannot drift from the meter's.
    remaining = engine_fallbacks(_shim.unshimmed, _shim.NAMESPACES)
    if remaining:
        for scope, names in sorted(remaining.items()):
            sys.stderr.write(
                f"{scope}: {len(names)} module(s) the engine owns are still "
                "served by Django:\n"
            )
            for name in names:
                sys.stderr.write(f"  {name}\n")
        sys.stderr.write(
            "refusing to run the acceptance suite: M14's criterion is unshimmed() "
            "empty in both scopes, so a green run here would not be the acceptance "
            "criterion.\n"
        )
        return 1

    sys.stderr.write(
        f"engine installed; unshimmed() is [] over {', '.join(sorted(_shim.NAMESPACES))} "
        f"({len(_shim.NOT_THE_ENGINE)} more of django.template are declared not the "
        "engine)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
