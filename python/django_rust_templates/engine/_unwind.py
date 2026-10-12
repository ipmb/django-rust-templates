"""The one piece of CPython's ``with`` statement Rust cannot stand in for.

A ``with`` whose body raised reaches ``__exit__`` through ``WITH_EXCEPT_START``, and that
opcode runs the call with the travelling failure as the *handled* exception: the one
``sys.exception()`` reads, and the one ``_PyErr_SetObject`` hangs a newly raised exception's
``__context__`` on. ``PyErr_Restore`` — the only handle PyO3 exposes — sets the *raised*
exception instead, which is a different slot. Measured, a ``context.push()`` manager whose
``__exit__`` records ``type(sys.exception()).__name__`` observes ``NoneType`` through a
native include and the render's own failure through delegation.

So the exceptional exit is not emulated. It is made from here, where an ``except`` clause
establishes that state, and Python chains the failures itself.
"""


def UNWIND(exit_method, kind, value, traceback):
    """Call the already-resolved ``__exit__`` the way CPython's ``WITH_EXCEPT_START`` does.

    ``exit_method`` is what ``BEFORE_WITH`` bound — ``type(manager).__exit__`` looked up on the
    type and bound to the instance — and the triple is the one CPython passes.

    **The answer is the truthiness of the return, computed here.** ``WITH_EXCEPT_START`` tests
    it before the handler pops, so a ``__bool__`` that reads ``sys.exception()`` sees the
    travelling failure; returning the raw object would move that read outside the state.

    **``raise value`` is how the state gets established, and it costs one traceback entry
    while ``exit_method`` runs** — the frame below. It is put back afterwards, so the value
    leaves with the traceback it arrived with; only a ``__exit__`` that reads
    ``sys.exception().__traceback__`` *during* the call can see the difference.
    """
    try:
        raise value
    except BaseException:
        try:
            return bool(exit_method(kind, value, traceback))
        finally:
            value.__traceback__ = traceback
