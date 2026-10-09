//! The parser's error protocol, ported from `Parser.error` and its callers.

use crate::parser::desc::TokenDesc;

/// The Python exception classes a filter body is allowed to catch (§D5).
///
/// The taxonomy is not decoration. Django's filters catch *specific* classes and
/// let the rest escape — `first` catches `IndexError` and lets a generator's
/// `TypeError` out (`defaultfilters.py:602-608`), `divisibleby` has no `try` at
/// all so a `ZeroDivisionError` propagates (`:854-858`), `add` catches bare
/// `Exception` (`:745-754`), `get_digit` catches `ValueError` and returns the
/// original value (`:757-776`) — so "did it raise, and with what class" is part
/// of each body's observable behaviour. A seam that flattened every failure to
/// one error type would make those branches unimplementable, and the corpus could
/// not pin them.
///
/// **The six are the classes the 57 catch, and they are not the whole of what can
/// arrive.** `pprint` (`:990-996`) catches bare `Exception` and puts
/// `e.__class__.__name__` **into the string it returns**, so the class name is
/// part of that filter's *output* for any class at all — a `RecursionError` from
/// `pformat` has to render as `RecursionError`, and a closed six could not say
/// so. Hence [`PyErrorKind::Other`], which carries Python's `__name__`. It is a
/// seventh variant rather than a second `class: String` field beside `kind`,
/// because two representations of one fact can disagree, and `Other` cannot.
///
/// The named variants are spelled exactly as Python names the classes, and
/// [`PyErrorKind::name`] returns that text for all seven, so a failure message
/// and `pprint`'s output both come from one place.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PyErrorKind {
    AttributeError,
    IndexError,
    KeyError,
    TypeError,
    ValueError,
    ZeroDivisionError,
    /// Any other class, by Python's `__name__`.
    Other(Box<str>),
}

impl PyErrorKind {
    /// Python's `e.__class__.__name__`.
    ///
    /// Needed as *text* by exactly one body — `pprint` at `defaultfilters.py:996`
    /// — which is why it is here rather than left to `Debug`: `Debug` prints
    /// `Other("RecursionError")` and the filter has to print `RecursionError`.
    pub fn name(&self) -> &str {
        match self {
            PyErrorKind::AttributeError => "AttributeError",
            PyErrorKind::IndexError => "IndexError",
            PyErrorKind::KeyError => "KeyError",
            PyErrorKind::TypeError => "TypeError",
            PyErrorKind::ValueError => "ValueError",
            PyErrorKind::ZeroDivisionError => "ZeroDivisionError",
            PyErrorKind::Other(name) => name,
        }
    }
}

/// A Python exception a filter body caught on the host seam.
///
/// Carries the class separately from the message because a body matches on the
/// class — see [`PyErrorKind`] — and carries `operation`, the [`FilterHost`]
/// method that raised, so a mismatched match names the operation in the failure
/// rather than leaving a reader to infer it from the message.
///
/// [`FilterHost`]: crate::filters::host::FilterHost
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FilterHostError {
    pub kind: PyErrorKind,
    pub message: String,
    pub operation: &'static str,
}

/// A Python exception the **tag** seam let past, and what it was raised as.
///
/// The tag seam's twin of [`FilterHostError`], and boxed for the same reason
/// ([`ErrorKind::FilterHost`] records the measurement). It is a struct rather
/// than a bare `String` because the class is part of the observable behaviour
/// and was being thrown away: Django's `Parser.parse` re-raises the **same
/// object** after `Parser.error` attaches a token (`base.py:583-585`,
/// `:619-630`), so a tag whose compile function raises `TypeError` leaves the
/// parse as a `TypeError`. Measured on the pinned tree:
///
/// ```text
/// Engine(libraries={"cls": "lib"}).from_string("{% load cls %}{% boom %}")
///   with `boom` raising TypeError("not a syntax error")
/// → django: TypeError('not a syntax error'), token attached
/// → ours:   ParseError('not a syntax error'), and nothing named the class
/// ```
///
/// `class` is `Option` because the variant has a **second** producer that never
/// touched Python — [`Error::host`]'s "no host answers `to_str`" — and
/// fabricating a class there would make the field lie about where the answer
/// came from, which is the same objection that kept `FilterHost` a separate
/// variant.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostError {
    pub message: String,
    /// Python's `e.__class__.__name__`, when Python is who raised.
    pub class: Option<PyErrorKind>,
}

/// What the parser failed with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ErrorKind {
    /// A syntax error. Rust owns the message (§6); the exception *object* is
    /// Python's (design decision 2).
    Syntax(String),
    /// An exception raised by a host tag, or a host that answered nothing.
    ///
    /// M2 has no host tag that can fail, so the payload was the host's message.
    /// M4 **added `FilterHost` beside this rather than reshaping it**, because the
    /// two answer different questions: `FilterHost` is "Python raised class C",
    /// `Host` is "nobody answered". A host that never touched Python has no
    /// [`PyErrorKind`] to report, and fabricating one would make a body's `kind`
    /// match lie about where the answer came from. The exception *object* held by
    /// identity is still owed: flattening a Python exception to a string loses
    /// `_culprit_node` and its `__traceback__`, which the annotation protocol
    /// depends on (§5.2). An enum variant that must change is preferable to a
    /// `String` that looks settled.
    ///
    /// **M6 takes the `String` up on that**, at the point the tag seam acquired
    /// its first producer (the reviewer's finding of 2026-10-09): the payload is
    /// [`HostError`], which is `message` plus the class Python raised. The class
    /// is not decoration — see that struct for the measurement — and the
    /// `Option` on it keeps the "nobody answered" producers honest rather than
    /// inventing a class for them. Boxed, for [`ErrorKind::FilterHost`]'s
    /// measured reason.
    Host(Box<HostError>),
    /// A Python exception raised while a **filter body** asked the host for
    /// something (§D5).
    ///
    /// **Boxed, and it has to be.** Written as an inline
    /// `{ kind, message, operation }` this variant is 64 bytes, which takes
    /// [`Error`] to exactly 128 — the threshold `clippy::result_large_err` fires
    /// at — and the crate then fails to build at **122 unrelated call sites**,
    /// every one of them a `Result<_, Error>` that had been fine for three
    /// milestones. `Error` is now 96 bytes and `error.rs` pins that, so the next
    /// field added here fails one local test instead of 122 far-away ones.
    ///
    /// The payload is a struct rather than a boxed `message` alone because a box
    /// around only the `String` saves 8 bytes and leaves no headroom, which would
    /// make the same 122-error rebuild the next variant's problem too.
    FilterHost(Box<FilterHostError>),
    /// `Token::split_contents` reached Django's `next(bits)` on an exhausted
    /// iterator — an unterminated `_("…` marker.
    ///
    /// Django lets `StopIteration` propagate out of the parse (only `Exception`
    /// is caught, to attach `.token`), so this is **not** a `Syntax`: the shell
    /// raises `StopIteration`, matching what M1's `_native.split_contents`
    /// already does at the same boundary.
    UnterminatedMarker,
    /// A **`ValueError`** Django lets out of a compile function, message and all.
    ///
    /// Not a `Syntax`, for [`ErrorKind::UnterminatedMarker`]'s reason: it is not
    /// a verdict on the template but a CPython exception from a tag body, and
    /// Django's `parse` re-raises the *same object* after attaching `.token`
    /// (`base.py:585` catches `Exception`, not `TemplateSyntaxError`). A
    /// drop-in replacement raises the same class, so the shell needs to be told
    /// which one — see the FFI arm.
    ///
    /// **There is exactly one producer and it is an accident of the source.**
    /// `do_filter` writes `_, rest = token.contents.split(None, 1)`
    /// (`defaulttags.py:744`) with no length guard, so `{% filter %}` — where
    /// `contents` has no separating run — unpacks a one-element list and CPython
    /// raises. Measured on the pinned tree:
    ///
    /// ```text
    /// Engine().from_string("{% filter %}x{% endfilter %}")
    /// → ValueError: not enough values to unpack (expected 2, got 1)
    ///   with .token set to the `filter` block token
    /// ```
    ///
    /// Reproduced rather than converted to a `TemplateSyntaxError` because a
    /// `Syntax` with the same text passes every corpus test in
    /// `tests/test_parser_errors.py` — the message and the token would match —
    /// so the divergent class would be invisible. The parity test that pins it is
    /// the FFI twin of `test_an_unterminated_marker_is_a_stop_iteration_for_both`.
    ValueError(String),
    /// Something M2 deliberately does not do yet. Removed by the milestone that
    /// supplies it, so the list of these is the milestone's remaining work.
    Unimplemented(String),
    /// An invariant the parser relies on was violated.
    ///
    /// Every use documents why it is believed unreachable, and it exists so a
    /// broken invariant becomes an `Error` rather than a panic unwinding
    /// through the FFI. Django's equivalent is an `IndexError`.
    Internal(String),
}

/// An error, plus the token Django's `Parser.error` attached to it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    pub kind: ErrorKind,
    /// Set by the **first** `error()` call to see this error, never replaced.
    ///
    /// That is Django's `if not hasattr(e, "token")`, and it is what makes the
    /// **innermost** token win when a compile error unwinds through nested
    /// `parse` frames (Review Focus item 5).
    ///
    /// [`Error::host_exception`] is the one producer that sets it ahead of any
    /// `error()` call, and for the same reason from the other end: the host is
    /// the *innermost* frame, and once an exception has crossed the seam Rust's
    /// `error()` frames are all outer.
    pub token: Option<TokenDesc>,
}

impl Error {
    pub fn syntax(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Syntax(message.into()),
            token: None,
        }
    }

    /// A host that **answered nothing** — a refusal, or a seam bug.
    ///
    /// No class and no token, and both are claims rather than omissions: see
    /// [`HostError::class`] for why a class would be a lie here, and
    /// [`Error::host_exception`] for the constructor a Python exception uses.
    /// Kept as the two-argument-free form because the "nobody answered"
    /// producers — [`NoFilterHost`]'s refusals and their parser-side twins —
    /// have neither to give, and a caller that has one has the other.
    ///
    /// [`NoFilterHost`]: crate::filters::host::NoFilterHost
    pub fn host(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Host(Box::new(HostError {
                message: message.into(),
                class: None,
            })),
            token: None,
        }
    }

    /// A Python exception from the **tag** seam: its message, the class it was
    /// raised as, and the token Django attached to it.
    ///
    /// [`Error::host`]'s twin, and a second constructor rather than `Option`
    /// arguments on the first, because the two are different claims: `host` is
    /// "the host raised and had nothing to give", this one is "Python raised
    /// class C and Django would have blamed *this* token". A caller holding a
    /// class and passing it through `host` would be indistinguishable from one
    /// holding none, which is the state this exists to end.
    ///
    /// Both are set **at construction**, so they are already present when the
    /// first `error()` frame sees the error and the never-replace rules keep
    /// them — the innermost token wins because the host is the innermost frame.
    /// Why the tag seam and not the filter one: a tag's compile function can
    /// re-enter the parse (`{% div %}`'s body is a `parser.parse`), so a failure
    /// inside it is a failure in a *nested* frame this side never sees. Measured
    /// on the pinned tree, where each of the two moves independently:
    ///
    /// ```text
    /// {% load custom %}{% div %}{% now a b %}{% enddiv %}
    /// → Django: "'now' statement takes one argument", token (2, 'now a b', 1, None)
    /// → ours:   the same message,                    token (2, 'div', 1, None)
    /// ```
    ///
    /// and, for the class, a library tag whose compile raises `TypeError`:
    ///
    /// ```text
    /// {% load cls %}{% boom %}     # boom raises TypeError("not a syntax error")
    /// → Django: TypeError('not a syntax error')
    /// → ours:   ParseError('not a syntax error')
    /// ```
    ///
    /// The class is carried as data rather than raised here because design
    /// decision 2 gives the exception *object* to Python: `_native` raises its
    /// own classes and the shell converts them (M9), so what has to survive the
    /// seam is which class to convert to.
    pub fn host_exception(
        message: impl Into<String>,
        class: PyErrorKind,
        token: Option<TokenDesc>,
    ) -> Self {
        Self {
            kind: ErrorKind::Host(Box::new(HostError {
                message: message.into(),
                class: Some(class),
            })),
            token,
        }
    }

    /// A Python exception, raised by `operation` on the filter seam.
    ///
    /// The class is a separate argument from the message because a body matches
    /// on it, and `operation` is the `FilterHost` method's name — with one
    /// measured exception, where a body raises an error the seam has no method
    /// for and names instead the CPython operation it stands in for
    /// ([`cut`](crate::filters::cut) raising `str.replace`'s `TypeError`, since
    /// no `FilterHost` method performs a replacement). The field is diagnostics
    /// only — it is never part of the message a template author sees — so a
    /// wrong value here misleads a reader of a `Debug` dump rather than a user,
    /// which is exactly why it is worth keeping honest.
    pub fn filter_host(
        kind: PyErrorKind,
        operation: &'static str,
        message: impl Into<String>,
    ) -> Self {
        Self {
            kind: ErrorKind::FilterHost(Box::new(FilterHostError {
                kind,
                message: message.into(),
                operation,
            })),
            token: None,
        }
    }

    /// The error a filter stub returns.
    ///
    /// An `Unimplemented` rather than a `Syntax` or a `Host`, for the reason
    /// `registry::unimplemented` gives about tag stubs: the filter is real and
    /// Django would run it, so this is a statement about the engine, not a verdict
    /// on the template. The milestone is baked in rather than passed because M4
    /// owns all 57 (§10.3) — a filter that slips to a later milestone is exactly
    /// the event that should edit this line.
    pub fn unimplemented_filter(name: &str) -> Self {
        Self::unimplemented(format!(
            "'{name}' is a built-in filter this engine does not implement yet (M4)"
        ))
    }

    pub fn unterminated_marker() -> Self {
        Self {
            kind: ErrorKind::UnterminatedMarker,
            token: None,
        }
    }

    /// The error a tag returns when Django would let a `ValueError` out.
    ///
    /// See [`ErrorKind::ValueError`] for the one producer and why the class is
    /// carried rather than flattened into a `Syntax`.
    pub fn value_error(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::ValueError(message.into()),
            token: None,
        }
    }

    pub fn unimplemented(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Unimplemented(message.into()),
            token: None,
        }
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Internal(message.into()),
            token: None,
        }
    }

    /// The message, whatever kind this is.
    ///
    /// `UnterminatedMarker` has no message of its own — Python raises a bare
    /// `StopIteration` with no arguments — so it is named after its cause. Only
    /// diagnostic paths read it.
    pub fn message(&self) -> String {
        match &self.kind {
            ErrorKind::Syntax(message)
            | ErrorKind::ValueError(message)
            | ErrorKind::Unimplemented(message)
            | ErrorKind::Internal(message) => message.clone(),
            // Verbatim, **not** prefixed with the operation: this is the text
            // Python would have put on the exception, and the shell turns it back
            // into one. `operation` is on the variant for diagnostics, where
            // `Debug` shows it, so a mismatched `kind` match still names the
            // method that raised without corrupting the message.
            ErrorKind::FilterHost(error) => error.message.clone(),
            ErrorKind::Host(error) => error.message.clone(),
            ErrorKind::UnterminatedMarker => "unterminated translation marker".to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::token::TokenType;

    fn marker(contents: &str, lineno: usize) -> TokenDesc {
        TokenDesc {
            token_type: TokenType::Block,
            contents: contents.to_string(),
            lineno,
            position: None,
        }
    }

    #[test]
    fn constructors_start_without_a_token() {
        // Load-bearing: `error()` uses `token.is_none()` as its `hasattr` test,
        // so a constructor that pre-filled the token would make the innermost
        // rule silently never apply.
        assert_eq!(Error::syntax("a").token, None);
        assert_eq!(Error::host("a").token, None);
        assert_eq!(Error::value_error("a").token, None);
        assert_eq!(Error::unterminated_marker().token, None);
        assert_eq!(Error::unimplemented("a").token, None);
        assert_eq!(Error::internal("a").token, None);
        assert_eq!(
            Error::filter_host(PyErrorKind::ValueError, "to_int", "a").token,
            None
        );
        assert_eq!(Error::unimplemented_filter("upper").token, None);
    }

    #[test]
    fn message_preserves_every_kinds_text() {
        assert_eq!(Error::syntax("s").message(), "s");
        assert_eq!(Error::host("h").message(), "h");
        assert_eq!(Error::unimplemented("u").message(), "u");
        assert_eq!(Error::internal("i").message(), "i");
        assert_eq!(
            Error::unterminated_marker().message(),
            "unterminated translation marker",
        );
    }

    #[test]
    fn a_token_can_be_attached_to_any_kind() {
        // `parse` attaches the token to whatever a compile function returned,
        // including `Host` — the branch that matters at M4.
        let host_error = Error::host("refused");
        let attached = Error {
            kind: host_error.kind,
            token: Some(marker("hosty", 1)),
        };
        assert_eq!(attached.token.unwrap().contents, "hosty");
    }

    /// `FilterHost` is a **sibling** of `Host`, not a widening of it, and the two
    /// carry different information. This is the guard for §D5's decision: a
    /// `FilterHost` knows which Python class raised and which seam operation did
    /// it, and a `Host` knows neither.
    #[test]
    fn a_filter_host_error_carries_a_class_and_an_operation() {
        let error = Error::filter_host(
            PyErrorKind::IndexError,
            "get_index",
            "list index out of range",
        );
        assert_eq!(error.token, None);
        assert_eq!(
            error.kind,
            ErrorKind::FilterHost(Box::new(FilterHostError {
                kind: PyErrorKind::IndexError,
                message: "list index out of range".to_string(),
                operation: "get_index",
            })),
        );
        // The message is Python's text verbatim, so the shell can re-raise it as
        // the exception it came from; the operation is on the variant, and
        // `Debug` is where a reader sees it.
        assert_eq!(error.message(), "list index out of range");
        assert!(
            format!("{error:?}").contains("get_index"),
            "the operation must be visible in a diagnostic: {error:?}"
        );
    }

    /// The two refusal shapes are distinguishable, which is what lets a
    /// `TestFilterHost` test prove that "nobody answered" is not being reported
    /// as "Python raised".
    #[test]
    fn a_refusing_host_is_not_a_python_exception() {
        let refused = Error::host("no host answers `to_str`");
        assert_eq!(refused.kind, Error::host("no host answers `to_str`").kind);
        assert_ne!(
            refused.kind,
            Error::filter_host(PyErrorKind::TypeError, "to_str", "no host answers `to_str`").kind,
        );
    }

    /// The stub error names the filter *and* the milestone, so a `cargo test`
    /// failure during the fan-out says which filter is unbuilt and who owns it —
    /// the `registry::unimplemented` precedent.
    #[test]
    fn an_unimplemented_filter_names_itself_and_its_milestone() {
        assert_eq!(
            Error::unimplemented_filter("upper").kind,
            ErrorKind::Unimplemented(
                "'upper' is a built-in filter this engine does not implement yet (M4)".to_string(),
            ),
        );
    }

    /// `Error` stays under `clippy::result_large_err`'s threshold.
    ///
    /// `Error` is the `Err` of every `Result` in the crate, so a payload added to
    /// one variant is paid for by every signature — and the payment is not
    /// proportional. Taking `Error` to exactly 128 bytes during M4 Task 2 failed
    /// the build at **122 call sites**, all of them in files the change never
    /// touched. This test turns the next field that does it into one red test in
    /// this file. The fix is always the same: box the payload, as
    /// [`ErrorKind::FilterHost`] does.
    #[test]
    fn the_error_type_stays_under_clippys_threshold() {
        let size = std::mem::size_of::<Error>();
        assert!(
            size < 128,
            "`Error` is {size} bytes and `clippy::result_large_err` fires at 128, \
             which fails every `Result<_, Error>` signature in the crate"
        );
    }
}
