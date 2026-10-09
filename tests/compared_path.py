"""The code a parity harness actually executes, measured rather than listed.

``tests/parity_target.py`` guards that no declared transcription region covers
the code a parity comparison runs — otherwise both sides of the comparison are
Rust and a green suite means nothing.

The first version of that guard took the compared path to be the lines of the
single target the row names, and the reviewer's objection
(``.review/m9-measurements/``) was that this is a *proxy*: ``Parser.parse`` calls
``extend_nodelist``, ``compile_filter``, ``invalid_block_tag`` and every tag's
compile function, so a region declared inside a helper is on the compared path
while sitting outside ``parse``'s own lines — and the guard's output cannot tell
the two cases apart.

So it is measured. Every function the module defines is wrapped with a counter,
the corpus is parsed through the harness's own entry point, and the functions
that ran, with the lines they occupy, *are* the compared path:

    28 of 66 function(s) the module defines were executed, spanning 116-1181

which is why the answer matters — the reach covers nearly the whole file, not
one function in it. (The span is the first start to the last end of the
executed functions. It was 116-1209 when `base.py` was a verbatim copy and
shrank as Tasks 2 and 5 replaced bodies with calls; the number is quoted from
the instrument rather than from this docstring, and the pin below is what the
row re-measures.)

``scripts/check_transcriptions.py`` pins that result in ``COMPARED_PATH``, and
``tests/test_interposition.py::test_the_compared_path_is_the_measured_one``
re-runs this module and asserts the pin still matches, so the table cannot go
stale quietly. The instrument and the pin are separate so that the guard is
cheap on every ``check.sh`` and the measurement happens once, where a failure
says which of the two moved.
"""

import collections
import contextlib
import inspect
import sys


def span_of(func):
    """The ``(first, last)`` lines ``func`` occupies, 1-based, or ``None``."""
    try:
        lines, first = inspect.getsourcelines(func)
    except (OSError, TypeError):
        return None
    return (first, first + len(lines) - 1)


def _one_level(value):
    """``value``, or the values of the module-level container holding it."""
    if isinstance(value, dict):
        yield from value.values()
    elif isinstance(value, (list, tuple, set, frozenset)):
        yield from value
    else:
        yield value


def label_of(cls):
    """``cls``'s name, or its qualified name when it was made inside a function.

    ``smartif``'s operator classes are built by ``infix()``/``prefix()``, so
    ``__qualname__`` is ``infix.<locals>.Operator``. All the classes one factory
    makes share that name and share the span their methods occupy, so collapsing
    them under one label loses nothing: the label answers "does the corpus reach
    a method this factory generated".
    """
    return cls.__name__ if cls.__qualname__ == cls.__name__ else cls.__qualname__


def classes(module):
    """Every class ``module`` defines, including ones held in a container.

    **This closes the instrument's one blind spot.** ``smartif`` binds its
    operator classes into ``OPERATORS``, a module-level dict, so a sweep of the
    module namespace's own attributes cannot see them — measured, the methods
    that evaluate ``and``, ``or``, ``in`` and every comparison were invisible
    while the guard's clause 4 would have accepted a declared region sitting on
    them. A class in a module-level dict is still a class the module defines, so
    the sweep looks one level into containers, and one level is enough: the
    factory's output is bound directly.
    """
    for value in vars(module).values():
        for candidate in _one_level(value):
            if (
                inspect.isclass(candidate)
                and candidate.__module__ == module.__name__
            ):
                yield candidate


def definitions(module):
    """Qualified name -> ``(first, last)`` for every function ``module`` defines.

    Nested functions are not reachable from the module namespace and so are not
    listed; the ones on the parse path are called from their enclosing function
    and their lines lie inside it, which is the span the guard needs. The
    exception is a *class* made by a factory, whose methods are listed under the
    factory's qualified name — see `classes`.
    """
    out = {}
    for name, obj in list(vars(module).items()):
        if inspect.isfunction(obj) and obj.__module__ == module.__name__:
            out[name] = span_of(obj)
    for cls in classes(module):
        for attr, value in list(vars(cls).items()):
            if inspect.isfunction(value):
                out[f"{label_of(cls)}.{attr}"] = span_of(value)
    return out


def bindings(reference):
    """Every ``(module, attribute)`` in ``sys.modules`` whose value *is* it.

    Identity, not equality: ``counting`` closes over its original, and two
    distinct wrappers are never ``==``, but the point is to find the exact
    binding rather than a similar one. Only ``django.*`` modules are visited —
    nothing else imports from ``django.template``, and rewriting a stranger's
    namespace would be a side effect this instrument has no business having.
    """
    for other in list(sys.modules.values()):
        if other is None or not getattr(other, "__name__", "").startswith("django."):
            continue
        for attr, value in list(vars(other).items()):
            if value is reference:
                yield other, attr


def rebind(old, new):
    """Point every binding of ``old`` at ``new``, per entry.

    One setattr that raises must not abandon the rest: a half-restored module is
    worse than a consistently wrapped one, because the symptom would be a later
    row running against a wrapper nobody is counting.
    """
    for owner, attr in bindings(old):
        try:
            setattr(owner, attr, new)
        except (AttributeError, TypeError):
            continue


@contextlib.contextmanager
def instrumented(module):
    """Count calls to every function ``module`` defines, restoring on exit.

    Yields the ``collections.Counter`` of qualified names.

    **Both sweeps are identity sweeps, and the restore sweep is a second
    one.** Wrapping the defining module only counts calls that go through
    *that* module's namespace. A function the module defines and another module
    imported by name — ``token_kwargs``, which ``django.template.library``
    reaches with ``from django.template.base import token_kwargs`` — is called
    through the *consumer's* binding, and the wrapper never runs. Measured:
    ``token_kwargs`` showed up in the first run of the measurement script and
    vanished in the second, and the only difference was that the first wrapped
    before the parity module imported. A reach set that depends on import order
    is not a measurement.

    The restore cannot use a list captured going in, for the same reason one
    step later: a module imported *during* the window binds the wrapper, and a
    captured list never visits it — so it would keep a live wrapper (and a live
    counter) for the rest of the session, and the symptom would be a later row
    silently running wrapped code. Restoring is therefore "find every binding
    that *is* one of my wrappers and put the original back", which is complete
    by construction because the wrappers are objects this function made.

    **The identity sweep is not sufficient for a class the module holds in a
    container.** `rebind` walks module namespaces, and an operator class built by
    `smartif.infix()` is bound nowhere but inside `OPERATORS`; the wrapper lives
    in the class's own `__dict__`, where no identity sweep will find it. So each
    wrap also records where it was placed, and the restore puts those back
    explicitly, before the sweep.
    """
    counts = collections.Counter()
    made = []  # (owner, name, wrapper, original), in wrap order

    def wrap(owner, name, label):
        original = getattr(owner, name)

        def counting(*args, **kwargs):
            counts[label] += 1
            return original(*args, **kwargs)

        counting.__name__ = name
        counting.__doc__ = original.__doc__
        counting.__wrapped__ = original
        setattr(owner, name, counting)
        made.append((owner, name, counting, original))
        rebind(original, counting)  # consumers that imported the name

    for name, obj in list(vars(module).items()):
        if inspect.isfunction(obj) and obj.__module__ == module.__name__:
            wrap(module, name, name)
    for cls in classes(module):
        for attr, value in list(vars(cls).items()):
            if inspect.isfunction(value):
                wrap(cls, attr, f"{label_of(cls)}.{attr}")

    try:
        yield counts
    finally:
        # Where the wrappers were placed, including class dicts no identity
        # sweep reaches, then the sweep for consumers that hold the name.
        for owner, name, _wrapper, original in made:
            try:
                setattr(owner, name, original)
            except (AttributeError, TypeError):  # pragma: no cover
                continue
        # The second identity sweep, over `sys.modules` as it is *now*. A module
        # imported during the window holds the wrapper, and a list captured on
        # the way in would never visit it.
        for _owner, _name, wrapper, original in made:
            rebind(wrapper, original)


def measure(module, sources, calls) -> dict:
    """``{qualified name: (first, last)}`` for the functions ``calls`` reached.

    ``sources`` is the corpus and ``calls`` is the harness entry point, one
    call per source. A source that raises still walked whatever path it walked,
    so the exception is swallowed deliberately: the counters already recorded it.
    """
    defs = definitions(module)
    with instrumented(module) as counts:
        for source in sources:
            try:
                calls(source)
            except Exception:
                pass
        reached = set(counts)
    return {
        label: defs[label]
        for label in sorted(reached, key=lambda label: (defs[label] or (0, 0)))
        if defs.get(label) is not None
    }
