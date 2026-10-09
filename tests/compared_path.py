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

    29 of 68 function(s) the module defines were executed, spanning 116-1181

which is why the answer matters — the reach covers nearly the whole file, not
one function in it. (The span is the first start to the last end of the
executed functions. It was 116-1209 when `base.py` was a verbatim copy and
shrank as Tasks 2 and 5 replaced bodies with calls; the number is quoted from
the instrument rather than from this docstring, and the pin below is what the
row re-measures.)

**What "every function the module defines" means took two passes to get
right.** The first sweep wrapped what `inspect.isfunction` accepts, which is the
plain case and only the plain case: `FilterExpression.args_check` is a
`staticmethod`, `Origin.loader_name` a `property`, `Engine.template_loaders` a
`cached_property`, `get_app_template_dirs` an `lru_cache` — four shapes a class
dict holds a function in, none of them a function. `args_check` alone runs 85
times over the parity corpus. The sweep now asks `_invoked` what a member
invokes and how to put it back in the same kind, and `residue` fails loudly on
any kind `_invoked` has no case for, so the table of kinds cannot go stale
quietly — an uncounted function is one the guard's clause (4) will let a
declared transcription region sit on.

``scripts/check_transcriptions.py`` pins that result in ``COMPARED_PATH``, and
``tests/test_interposition.py::test_the_compared_path_is_the_measured_one``
re-runs this module and asserts the pin still matches, so the table cannot go
stale quietly. The instrument and the pin are separate so that the guard is
cheap on every ``check.sh`` and the measurement happens once, where a failure
says which of the two moved.
"""

import collections
import contextlib
import functools
import inspect
import os
import sys


class UnhandledMember(RuntimeError):
    """A member that holds a function this module wrote and no sweep recognised.

    Raised rather than logged, because the failure it prevents is silent: an
    uncounted function is a function the guard's clause (4) will let a declared
    transcription region sit on, and a parity row comparing Rust against Rust
    then passes whatever Rust does.
    """


class ForeignSpan(RuntimeError):
    """A ``definitions`` span read out of a file the module does not own.

    The companion of :class:`UnhandledMember` for the other way this instrument
    can lie: a member recognised, owned, and measured — at somebody else's line
    numbers. ``COMPARED_PATH`` is a list of integers and nothing downstream can
    tell where they came from, so the check has to happen where the span is
    taken.
    """


_CACHED_PROPERTY_KINDS = frozenset(
    {
        ("functools", "cached_property"),
        ("django.utils.functional", "cached_property"),
    }
)
# `functools.cache` is `lru_cache(maxsize=None)` and has the same type, so one
# name covers both.
_LRU_CACHE_WRAPPER = getattr(functools, "_lru_cache_wrapper", None)


def _is_cached_property(value):
    """Whether ``value`` is a ``cached_property``, from either implementation.

    By the *type's* module and name rather than by ``isinstance``: the two
    implementations live in different modules, and importing Django here would
    make this file's importability depend on the checkout being on ``sys.path``
    — true of every caller today, and not something an instrument should
    require.
    """
    kind = type(value)
    return (kind.__module__, kind.__name__) in _CACHED_PROPERTY_KINDS


def _innermost(value, own_file):
    """The ``def`` a member was written as, under any decorator or descriptor.

    ``Engine.get_default`` is ``@staticmethod`` over ``@functools.lru_cache``
    over a plain ``def``, so one layer is not enough: the ownership question —
    did this module write it — is about the ``def``, and it is two wrappers
    down. ``None`` for a member that holds no function at all.

    ``own_file`` is the realpath of the file the module under instrument was
    loaded from, and it is what stops the ``__wrapped__`` arm walking *out* of
    that file — see :func:`_written_elsewhere`. It is a required argument rather
    than an optional one because there is no useful answer without it: the walk
    is a question about whose file a function is in, and a caller that has no
    module in hand cannot ask it.
    """
    for _ in range(8):
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        elif isinstance(value, property):
            if value.fget is None:
                return None
            value = value.fget
        elif _is_cached_property(value):
            value = value.func
        elif _LRU_CACHE_WRAPPER is not None and isinstance(value, _LRU_CACHE_WRAPPER):
            value = value.__wrapped__
        elif _written_elsewhere(value, own_file) and getattr(
            value, "__wrapped__", None
        ) is not None:
            value = value.__wrapped__
        else:
            return value
    return None  # pragma: no cover — no real member nests eight deep


def _written_elsewhere(value, own_file):
    """Whether ``value`` is a function some *other* file wrote.

    This is the reviewer's second finding, and it is the mirror of the one the
    ``__wrapped__`` arm was added for. ``functools.wraps`` sets ``__wrapped__``,
    and whether that is a decorator artifact to look through depends on whose
    file the *current* function is in:

    * ``@contextlib.contextmanager`` — the class dict holds a helper created in
      ``contextlib.py``, so the object is real, ``isfunction``-true, and written
      in another file. Looking through lands on the ``def`` ``context.py`` wrote,
      which is the answer.
    * a wrapper **we** wrote around a foreign function::

          def deco(f):
              @functools.wraps(f)
              def inner(*a, **k):
                  return f(*a, **k)
              return inner

          joined = deco(os.path.join)

      — the ``def`` that executes *is* ``inner``, written in our file, and
      following ``__wrapped__`` leaves the file. Measured on the closed tree
      before this was fixed: ``getsourcefile(joined)`` is the subject's own
      file, but ``_innermost`` returned ``os.path.join``, so ``_is_ours`` said
      False, ``definitions`` dropped the member, and ``residue`` stayed quiet —
      the ``args_check`` under-count arriving *through* the fix for
      ``args_check``.

    So the arm follows only while the current function is not already ours.

    **The two ``inspect`` functions differ in exactly this, and the difference is
    why the walk exists.** ``inspect.getsourcelines`` unwraps ``__wrapped__``
    internally, so a span read from a ``@contextmanager`` member was *always*
    ``context.py``'s lines — measured: ``Context.bind_template`` reports
    ``co_firstlineno`` 303 in ``contextlib.py`` and a span of 157-165 in
    ``context.py``. ``inspect.getsourcefile`` reads ``__code__.co_filename``
    directly and does not unwrap, so *ownership* is the question the walk
    answers, and it is the only one.
    """
    if not inspect.isfunction(value):
        return False
    return _defining_file(value) != own_file


def _plain(wrapper):
    """``rebox`` for a member that was not boxed: put the wrapper in as it is."""
    return wrapper


def _invoked(value):
    """``(callable, rebox)`` for a member holding a function, or ``None``.

    ``callable`` is what a caller ends up invoking, and ``rebox(wrapper)``
    rebuilds the **same kind of member** around it, so putting a counter in its
    place does not change how Python binds it. The re-boxing is the whole point.
    ``FilterExpression.args_check`` is bound with ``staticmethod(...)``, and
    ``getattr`` on a staticmethod already hands back the plain function — so a
    sweep that read the member back and wrote a plain function in its place
    would silently turn it into an *instance method*, and every call through
    ``self`` would pass the instance as ``name``. Measured on the pinned tree:
    ``args_check`` runs **85** times over the parity corpus, and the sweep knew
    only ``inspect.isfunction``, so not one of them was counted.

    An ``lru_cache`` wrapper is counted **outside** the cache — the wrapper
    itself is wrapped rather than the function under it, because ``maxsize`` is
    not recoverable from the wrapper and re-boxing it with a guess would change
    the caching the compared code depends on. A cache hit therefore counts as
    reached too; it is a walk of the function that returned early, which is the
    question this table asks.
    """
    if inspect.isfunction(value):
        return value, _plain
    if isinstance(value, staticmethod):
        return value.__func__, staticmethod
    if isinstance(value, classmethod):
        return value.__func__, classmethod
    if isinstance(value, property):
        if value.fget is None:
            return None
        return value.fget, lambda wrapper: property(
            wrapper, value.fset, value.fdel, value.__doc__
        )
    if _is_cached_property(value):
        return value.func, type(value)
    if _LRU_CACHE_WRAPPER is not None and isinstance(value, _LRU_CACHE_WRAPPER):
        return value, _plain
    return None


def _defining_file(written):
    """The file ``written`` was defined in, or ``None`` if ``inspect`` cannot say.

    The ``None`` is deliberately the *only* answer for both of the facts
    ``inspect`` can fail on — "this is not a function" and "I cannot find its
    source" — because for :func:`_owning_file` they want the same treatment.
    :func:`_is_foreign` is the one caller that has to tell them apart, and it
    does so by asking :func:`_innermost` and ``inspect.isbuiltin`` first rather
    than by reading a second meaning into this ``None``.
    """
    try:
        return os.path.realpath(inspect.getsourcefile(written))
    except (OSError, TypeError):
        return None


def _owning_file(value, own_file):
    """The file the function behind ``value`` was written in, or ``None``."""
    written = _innermost(value, own_file)
    if written is None:
        return None
    return _defining_file(written)


def _is_ours(value, module):
    """Whether ``value`` is a function **this module wrote**.

    By the file the ``def`` sits in, not by ``__module__``: ``django_file_
    prefixes`` is bound in ``base.py``'s namespace — ``from django.utils.
    deprecation import django_file_prefixes`` — and its ``__module__`` names the
    definition, so the file is what discriminates. The file is also the only
    test that still works through a decorator, since ``functools.wraps`` copies
    ``__module__`` across and the definition is the thing being asked about.

    The second arm covers a hand-written descriptor class: its *instance* has no
    source of its own, so ``getsourcefile`` raises — but the class was written
    here, so a function it hands back is this module's function whatever
    ``getsourcefile`` can see.
    """
    own = os.path.realpath(module.__file__)
    return _owning_file(value, own) == own or (
        hasattr(type(value), "__get__")
        and getattr(type(value), "__module__", None) == module.__name__
    )


def _is_foreign(value, module):
    """Whether an unrecognised member is **positively** somebody else's.

    This is the reviewer's finding, and it is the same defect as ``args_check``
    one level up. :func:`residue` used to filter by :func:`_is_ours`, which asks
    :func:`_owning_file` — which asks :func:`_innermost`, *the table whose
    incompleteness residue exists to detect*. The check was asking the thing it
    was checking. Measured, with a descriptor class defined in another module
    holding a function the subject module wrote (``Lazy``, in the row that now
    pins this):

        _innermost -> the Lazy *instance*      _invoked -> None
        _callable_member -> True               _is_ours -> False
        residue -> []                          definitions -> {}
        instrumented -> no complaint

    Silently uncounted, no ``UnhandledMember`` — ``args_check`` again, surviving
    inside the guard written to make ``args_check`` impossible.

    Why it happened: for an unhandled member ``_innermost`` falls through to
    ``return value`` — the descriptor *instance* — and ``getsourcefile`` on an
    instance raises, so the owning file is unknown and `_is_ours`'s first arm is
    False; the second arm needs the descriptor **class** to have been written in
    the module being measured. But every kind that has actually caused trouble
    here — ``staticmethod``, ``property``, ``cached_property``, ``lru_cache`` —
    is defined *elsewhere*, so that arm fires only for the rarest shape there
    is: a descriptor class written inside the file being measured.

    The precise statement is that :func:`_owning_file` returns ``None`` for two
    different facts — "this holds no function I can find" and "I cannot see
    through this kind" — and the old arm read both as *not ours*. For
    :func:`definitions` that conflation is harmless. For :func:`residue`, which
    is the one place where *I don't know* has to be loud, it was the whole
    defect.

    So the polarity is inverted: report unless positively foreign.

    1. ``ismemberdescriptor`` / ``isgetsetdescriptor`` — C slot wrappers and
       ``__dict__``/``__weakref__``, which cannot hold a Python function at all;
    2. a builtin — there is no Python source anywhere for it, so it cannot be a
       ``def`` this module wrote. This is what excludes ``TokenType._new_member_``
       (``object.__new__``), the same family as the ``__new__``
       :func:`_innermost` already drops deliberately;
    3. a known owning file that is not this module's — somebody else's ``def``;
    4. **an unknown owning file is reported**, which is the arm that was
       missing. A shape this table cannot see through is exactly what residue is
       for, so it cannot also be a reason to stay quiet.

    **Do not reach for ``inspect.isroutine`` here.** It includes
    ``ismethoddescriptor``, which is true for any object with ``__get__`` and no
    ``__set__`` — so it swallows precisely the case being hunted. Measured by the
    reviewer on the example above: ``inspect.isroutine(Lazy_instance)`` is
    ``True``, and a draft of this rule using it returned ``[]`` for the very case
    it was written to catch.

    **A ``functools.partial`` member is loud, and that is this arm working.**
    The reviewer raised it as the one shape whose polarity they were unsure of:
    a class-level ``partial(os.path.join, "/tmp")`` written here has no
    ``__func__``-shaped ``def`` to reach and no source of its own, so
    ``_owning_file`` returns ``None`` — and arm 4 turns that into a report.
    Measured: ``_is_ours`` False, ``_is_foreign`` False, ``residue``
    ``['holder']``, ``definitions`` ``{}``. Reported rather than silently
    dropped is the right direction, and if a real module ever grows one the
    answer is to add ``partial.func`` to :func:`_innermost`'s table — a new row
    for a new shape — not to soften this arm.
    """
    if inspect.ismemberdescriptor(value) or inspect.isgetsetdescriptor(value):
        return True
    own = os.path.realpath(module.__file__)
    written = _innermost(value, own)
    if written is None or inspect.isbuiltin(written):
        return True
    source = _defining_file(written)
    if source is None:
        return False
    return source != own


def _callable_member(value):
    """Whether ``value`` is the sort of member that could be holding a function.

    A class is excluded: its methods are swept under their own names as it is,
    and without this the ``TokenType`` in ``base.py``'s namespace would make
    every enum in the tree a suspicion. What is left is something callable, or
    something that is a descriptor — the two shapes a class dict stores a
    function in.
    """
    return not isinstance(value, type) and (
        callable(value) or hasattr(type(value), "__get__")
    )


def members(module):
    """``(owner, name, value)`` for every member the sweeps must consider.

    The module namespace, then every class dict :func:`classes` finds. One
    iterator and three readers — :func:`definitions`, :func:`instrumented` and
    :func:`residue` — so the set the three of them see cannot drift apart, which
    is exactly the drift that let ``args_check`` be invisible to a sweep that
    ``definitions`` agreed with.
    """
    for name, value in list(vars(module).items()):
        yield module, name, value
    for cls in classes(module):
        for name, value in list(vars(cls).items()):
            yield cls, name, value


def residue(module):
    """Members that hold a function this module wrote and no sweep recognises.

    The sweep is a *table* of the shapes a class dict uses to hold a function,
    and a table is a claim about the world that nothing in the world checks.
    This is the check: anything callable-ish that this module wrote and
    :func:`_invoked` has no case for is reported, so a descriptor kind nobody
    has written a case for fails loudly rather than quietly going uncounted —
    which is how ``args_check`` went uncounted for two milestones.

    **The filter is :func:`_is_foreign` and not :func:`_is_ours`**, which is the
    reviewer's second finding: asking ``_is_ours`` made this function consult
    :func:`_innermost`, the table it exists to check, and the shape it could not
    see through was therefore also the shape it stayed quiet about. The
    polarity matters because *unknown* has to be loud here and quiet in
    :func:`definitions`, and one predicate cannot mean both.
    """
    return sorted(
        name if owner is module else f"{label_of(owner)}.{name}"
        for owner, name, value in members(module)
        if _invoked(value) is None
        and _callable_member(value)
        and not _is_foreign(value, module)
    )


def span_of(func, own_file):
    """The span ``func`` occupies, and the file those lines were read out of.

    ``((first, last), path)`` — 1-based lines, and ``os.path.realpath`` of the
    file they came from — or ``(None, None)`` when ``inspect`` can find no
    source for the subject at all.

    **The subject is chosen, because the two ``inspect`` functions disagree
    about ``__wrapped__``**: ``getsourcefile`` reads ``__code__.co_filename``
    and does not unwrap, ``getsourcelines`` unwraps before it looks. Reading
    whatever ``getsourcelines`` happens to unwrap to is what the reviewer's
    second finding was: for a ``functools.wraps`` wrapper we wrote around a
    foreign function, ``_innermost`` correctly stops at our wrapper and the
    read then walks *past* it and returns lines out of ``<frozen posixpath>`` —
    so the member the ownership walk had just recovered could only ever become a
    :class:`ForeignSpan`, never an entry. Recovering something in order to
    refuse it one function later is not a recovery.

    So: **if the def's own file is ours, read the def; only otherwise unwrap.**
    The two shapes that matters for:

    * a ``@contextlib.contextmanager`` member — the wrapper in the class dict
      was created in ``contextlib.py``, so its own file is *not* ours and
      unwrapping is exactly right: the ``def`` behind it is, and the span is
      ``context.py``'s. This is the reason the ownership walk exists at all.
    * a wrapper we wrote around a foreign function — the wrapper's own file is
      ours, so it is the subject, and the span is ours. Measured: ``subjI.py``'s
      ``deco(os.path.join)`` spans 6-8 of ``subjI.py``, where the read used to
      give 72-92 of ``<frozen posixpath>``.

    The read goes through ``__code__`` rather than the function so that
    ``getsourcelines`` *cannot* unwrap a second time — for a function with no
    ``__wrapped__`` the two spellings are the same call (``findsource`` does
    ``object = object.__code__`` itself), so this changes nothing except the one
    shape above. A span read from another file is a bare pair of integers that
    nothing downstream can distinguish from one of ours, which is what
    :class:`ForeignSpan` exists to refuse — so the file travels with the lines.
    """
    subject = func if _defining_file(func) == own_file else inspect.unwrap(func)
    target = subject.__code__ if inspect.isfunction(subject) else subject
    try:
        lines, first = inspect.getsourcelines(target)
    except (OSError, TypeError):
        return None, None
    return (first, first + len(lines) - 1), _defining_file(subject)


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

    A function a class dict holds behind a descriptor is listed under the name a
    caller reaches it by — ``FilterExpression.args_check``, ``Origin.loader_
    name`` — and spans the ``def`` it was written as, decorators included, since
    the span comes from the innermost function. Reading the *member* instead
    would give ``None`` for anything but the plain case: ``inspect`` cannot
    recover source lines from an ``lru_cache`` wrapper or a ``cached_property``,
    and a label whose span is ``None`` is dropped by :func:`measure`, which is
    the measurement quietly going missing rather than the instrument saying so.

    **And the span's own file is asserted to be this module's**, because a span
    is a bare pair of integers and the only consumers of it are ``COMPARED_PATH``
    and clause (4), which compare integers. A span read from another file is
    numerically indistinguishable from one of ours.

    The check has to ask about the file :func:`span_of` actually read from, and
    the first version of this guard asked the wrong question twice over. It
    compared ``_defining_file(written)`` — the file of the def the walk stopped
    on — while the span itself came from ``inspect.unwrap(written)``, so a
    ``functools.wraps`` wrapper we wrote around a foreign function passed the
    check while its lines came out of ``<frozen posixpath>``. Fixing *that* by
    reporting the read file then made the recovered member unmeasurable: the
    walk stopped correctly at our wrapper and the read walked past it again, so
    the arm could only ever raise. Both halves are one question — *which object
    is this span about* — and :func:`span_of` now answers it before it reads,
    so ``read_from`` is the file of the def the span describes.

    Measured on the closed tree: no span of any of the twelve comes from a
    foreign file, so the class remains *absent* in the corpus — and the rows in
    ``test_compared_path_instrument`` execute the raise, both with a span that
    unwraps out of the file and with one that never was in it, because a guard
    nobody has seen fire is a guard nobody knows works.
    """
    own = os.path.realpath(module.__file__)
    out = {}
    for owner, name, value in members(module):
        if _invoked(value) is None or not _is_ours(value, module):
            continue
        written = _innermost(value, own)
        span, read_from = span_of(written, own)
        if span is not None and read_from != own:
            raise ForeignSpan(
                f"{name}: span {span} was read from {read_from}, not {own}"
            )
        label = name if owner is module else f"{label_of(owner)}.{name}"
        out[label] = span
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

    **And the sweep wraps everything that holds a function, not everything that
    *is* one.** `args_check`, `Origin.loader_name`, `Engine.template_loaders` and
    `get_app_template_dirs` are held by a `staticmethod`, a `property`, a
    `cached_property` and an `lru_cache`; a sweep that knew only
    `inspect.isfunction` counted none of them, and the first of those runs 85
    times over the parity corpus. :func:`_invoked` is the table of kinds and
    :func:`residue` fails loudly on any kind it has no case for, so the table
    cannot go stale quietly — which is the shape of the defect this closed.
    """
    escaped = residue(module)
    if escaped:
        raise UnhandledMember(
            f"{module.__name__} writes function-bearing member(s) this instrument "
            f"has no case for: {escaped}. Add the kind to `_invoked` — unwrap it "
            "and re-box it in the same kind — or `definitions` will hand the "
            "guard a span it never measured and clause (4) will accept a "
            "declared region on code the parity harness runs."
        )

    counts = collections.Counter()
    made = []  # (owner, name, wrapper, original, invoked), in wrap order

    def wrap(owner, name, label, value):
        invoked, rebox = _invoked(value)
        original = value

        def counting(*args, **kwargs):
            counts[label] += 1
            return invoked(*args, **kwargs)

        counting.__name__ = name
        counting.__doc__ = getattr(invoked, "__doc__", None)
        counting.__wrapped__ = invoked
        replacement = rebox(counting)
        setattr(owner, name, replacement)
        # A `cached_property` builds its storage key in `__set_name__`, which
        # Python calls at class-creation time and therefore not for a member
        # installed afterwards. Both implementations fall back to the function's
        # own name, so this is belt-and-braces rather than load-bearing — but it
        # is the difference between a cache keyed by `template_loaders` and one
        # keyed by the wrapper's name, which is the same string today and would
        # not be if `counting.__name__` were ever set from anything else.
        set_name = getattr(replacement, "__set_name__", None)
        if set_name is not None and owner is not module:
            set_name(owner, name)
        made.append((owner, name, replacement, original, invoked))
        rebind(invoked, replacement)  # consumers that imported the name

    for owner, name, value in members(module):
        if _invoked(value) is None or not _is_ours(value, module):
            continue
        wrap(owner, name, name if owner is module else f"{label_of(owner)}.{name}", value)

    try:
        yield counts
    finally:
        # Where the wrappers were placed, including class dicts no identity
        # sweep reaches, then the sweep for consumers that hold the name. The
        # *original member* goes back, not `getattr`'s answer: for a
        # `staticmethod` those differ, and writing back the plain function would
        # leave the method demoted for the rest of the session.
        for owner, name, _wrapper, original, _invoked_ in made:
            try:
                setattr(owner, name, original)
            except (AttributeError, TypeError):  # pragma: no cover
                continue
        # The second identity sweep, over `sys.modules` as it is *now*. A module
        # imported during the window holds the wrapper, and a list captured on
        # the way in would never visit it.
        for _owner, _name, wrapper, _original, invoked in made:
            rebind(wrapper, invoked)


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
