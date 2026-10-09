//! `utc` — `tz.py:29-34`.
//!
//! ```python
//! @register.filter
//! def utc(value):
//!     return do_timezone(value, UTC)
//! ```
//!
//! [`localtime`](crate::filters::localtime)'s twin: the same transcribed Python
//! body, the other fixed zone. The source's argument is `datetime.UTC` — the
//! constant, not a lookup — and the host resolves it and calls the transcribed
//! `do_timezone` exactly as `localtime`'s row does. See that module for why this
//! is `ours/theirs` at the seam and not a second Rust implementation of
//! `do_timezone`.
//!
//! `UTC` is imported at the top of the transcribed `tz.py` (`from datetime import
//! UTC, datetime, tzinfo`), so the name the host's `datetime.UTC` resolves is the
//! same object the source's `utc` would have passed.

use crate::filters::host::{FilterCall, TimezoneSource};
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `utc`'s body: reach the transcribed `do_timezone` with `datetime.UTC`.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    call.host.do_timezone(value, TimezoneSource::Utc)
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

    /// The body is one seam call, and it is the same seam `localtime` uses — the
    /// enum's two variants are the only difference between the modules.
    #[test]
    fn it_reaches_the_one_timezone_seam() {
        let h = TestFilterHost {
            do_timezone: Some(Value::Str("UTC".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Opaque(0), None, &call(&h)).unwrap(),
            Value::Str("UTC".into())
        );
        assert_eq!(h.calls(), vec!["do_timezone"]);
    }

    /// The non-datetime answer is the transcribed body's `""`, handed straight
    /// back: this filter catches nothing and translates nothing.
    #[test]
    fn an_empty_answer_is_returned_unchanged() {
        let h = TestFilterHost {
            do_timezone: Some(Value::Str(String::new().into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Str("not a datetime".into()), None, &call(&h)).unwrap(),
            Value::Str(String::new().into())
        );
    }
}
