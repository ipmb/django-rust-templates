//! Fallible space padding, shared by `center`, `ljust` and `rjust`.
//!
//! The three padding bodies all build `left spaces + text + right spaces`, and
//! all three must build it **fallibly**. The obvious `" ".repeat(margin)` cannot:
//! `String::repeat` allocates infallibly, so when the allocator refuses a margin
//! it calls `handle_alloc_error` and **aborts the process** — not a panic, which
//! `catch_unwind` would see, but a signal that takes the interpreter down with
//! it. Measured here: `" ".repeat(10usize.pow(18))` dies by `SIGABRT`, where
//! Django's `center("ab", 10**18)` raises a catchable `MemoryError`.
//!
//! `try_reserve_exact` is the fix, and it is the *right* fix rather than merely
//! an available one, because it **fails where the allocator fails** — and that is
//! the constraint CPython's own allocation is subject to. Measured side by side,
//! same machine, same moment:
//!
//! ```text
//!   n             " ".repeat(n)   try_reserve_exact(n)   Django's center(x, n)
//!   2**31         ok              ok                     ok
//!   10**18        SIGABRT         Err                    MemoryError
//! ```
//!
//! *Where the allocator is the binding constraint*, the two agree by
//! construction rather than by calibration — both ask the same allocator and get
//! the same answer. They are **not** guaranteed to agree everywhere: on a system
//! that overcommits, `reserve` (like `malloc`) can succeed past the point where
//! the pages can be backed, and then neither language raises — the OOM killer
//! arrives on write instead. So read this as "fails when the allocator refuses",
//! not as "raises exactly what CPython raises"; the gain over `repeat` is that an
//! unallocatable width becomes a catchable error rather than a process abort, and
//! that holds in every case, overcommitting or not.
//!
//! So this needs **no chosen threshold**, and that is the point rather than a
//! convenience. A bound in the shape of `parser::smartif::MAX_NESTING_DEPTH`
//! would be the wrong shape here, and the distinction generalizes: a chosen
//! ceiling is right where the limit is a *policy* (how deep are we willing to let
//! this recurse — recursion depth has no physical bound, so someone must pick
//! one) and wrong where the limit is a *resource*, which has exactly one bound
//! and it is the machine. Where recursion depth has a defensible chosen ceiling,
//! allocation has none — the threshold is deliberately not ours to pick.
//!
//! The error is `PyErrorKind::Other("MemoryError")` with an **empty message**,
//! which is what CPython raises for `" " * 10**18` (measured: `str(exc)` is `""`),
//! so a caller that renders the error matches Django's.

use crate::parser::error::{Error, PyErrorKind};

/// `left` spaces, then `text`, then `right` spaces — or the `MemoryError` the
/// same expression would raise in Python.
///
/// `operation` is the host operation whose failure this mirrors, so an error
/// names the call site (`str.format` for `center`, `str.ljust` and `str.rjust`
/// for the other two) rather than the shared helper they all pass through.
pub(crate) fn pad_spaces(
    operation: &'static str,
    text: &str,
    left: usize,
    right: usize,
) -> Result<String, Error> {
    // Saturating, so a margin that cannot be represented is an allocation
    // failure rather than a wrap to a small number that would silently succeed.
    let capacity = left.saturating_add(right).saturating_add(text.len());
    let mut padded = String::new();
    padded
        .try_reserve_exact(capacity)
        .map_err(|_| Error::filter_host(PyErrorKind::Other("MemoryError".into()), operation, ""))?;
    for _ in 0..left {
        padded.push(' ');
    }
    padded.push_str(text);
    for _ in 0..right {
        padded.push(' ');
    }
    Ok(padded)
}
