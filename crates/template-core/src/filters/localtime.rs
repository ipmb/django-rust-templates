//! `localtime` — `tz.py:19-26`.
//!
//! ```python
//! @register.filter
//! def localtime(value):
//!     return do_timezone(value, timezone.get_current_timezone())
//! ```
//!
//! **The body stays Python-side, under the seam, and this module is the shim
//! that reaches it.** `do_timezone` (`tz.py:38-84`) is one transcribed Python
//! function under three registered names; M12 Task 4 measured the three `tz`
//! *tag* bodies calling it zero times — its only callers are the `localtime` and
//! `utc` **filters** (`tz.py:26`, `tz.py:34`) — and committed the transcription
//! as its decision 1 in `f89a9d4`. So this Rust body is the two-line source
//! expression performed one layer down: [`FilterHost::do_timezone`] is handed
//! [`TimezoneSource::Current`] and the host resolves
//! `timezone.get_current_timezone()` — the source's own argument — and calls the
//! transcribed `do_timezone`.
//!
//! **This name is `ours/theirs` at the seam, not a ninth-of-nine Rust body.** The
//! routing is Rust's and the algorithm is Python's, which is the honest
//! description; three Rust bodies that each rebuilt `do_timezone` would be three
//! chances to diverge from a function the 1547 exercises **zero** times (measured
//! at `ec47f4f`: `localtime` appears once in the suite and `utc` not at all).
//!
//! Bare `@register.filter` — no `is_safe`, so `do_timezone`'s answer (a
//! `datetime`, or `""` for a non-datetime) is unmarked, and this body adds
//! nothing.

use crate::filters::host::{FilterCall, TimezoneSource};
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `localtime`'s body: reach the transcribed `do_timezone` with the current zone.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    call.host.do_timezone(value, TimezoneSource::Current)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: false,
        }
    }

    /// The body is one seam call and nothing else — the whole of it.
    #[test]
    fn it_reaches_the_one_timezone_seam() {
        let h = TestFilterHost {
            do_timezone: Some(Value::Str("LOCAL".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Opaque(0), None, &call(&h)).unwrap(),
            Value::Str("LOCAL".into())
        );
        assert_eq!(h.calls(), vec!["do_timezone"]);
    }

    /// **The `timezone` filter's argument is not read here.** `localtime` takes
    /// no argument, so a body that forwarded its own `arg` would diverge the
    /// moment one appeared — the parse-time `args_check` forbids one, but the
    /// body does not rely on that. Passing `Some` through changes nothing.
    #[test]
    fn it_ignores_any_argument() {
        let h = TestFilterHost {
            do_timezone: Some(Value::None),
            ..TestFilterHost::default()
        };
        run(Value::Opaque(0), Some(Value::Str("UTC".into())), &call(&h)).unwrap();
        assert_eq!(h.calls(), vec!["do_timezone"]);
    }
}
