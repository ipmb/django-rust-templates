//! `timezone` — `tz.py:37-38`, which *is* `do_timezone` under a registered alias.
//!
//! ```python
//! @register.filter("timezone")
//! def do_timezone(value, arg):
//!     ...
//! ```
//!
//! The third of the three names over one Python body, and the one where the alias
//! is the whole story: `register.filter("timezone")` registers the function
//! `do_timezone` under the name `timezone`, so `{{ d|timezone:"Europe/Paris" }}`
//! calls `do_timezone(d, "Europe/Paris")` and nothing else. This Rust body hands
//! the template's own argument across unchanged
//! ([`TimezoneSource::Given`]) — the argument is a `tzinfo`, a zone name, or
//! `None`, and *which* of those it is is the transcribed body's decision — the
//! `if isinstance(arg, tzinfo):` chain (`tz.py:59-67`) — not Rust's.
//!
//! **This name is `ours/theirs` at the seam** exactly as `localtime` and `utc`
//! are: the routing is Rust's and the algorithm is Python's, and the commit that
//! lands this says so rather than counting it as a ninth-of-nine Rust body.
//!
//! Bare `@register.filter` — no `is_safe` — so the answer (a `datetime` subclass
//! with `convert_to_local_time = False`, or `""`) is unmarked. The body adds no
//! branch of its own: the `isinstance` chain that decides the zone, the naive-
//! datetime handling and the bare `except Exception` are all `do_timezone`'s
//! (`tz.py:48-86`).
//!
//! **The argument is required.** `do_timezone(value, arg)` has two positional
//! parameters, so `args_check` (`base.py:822`) refuses `{{ d|timezone }}` at
//! parse time — the `Given(None)` arm is Django's own missing-argument call kept
//! reachable rather than a shape the template can write, and it raises the
//! `TypeError` Python's signature raises.

use crate::filters::host::{FilterCall, TimezoneSource};
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `timezone`'s body: reach the transcribed `do_timezone` with the argument.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    call.host
        .do_timezone(value, TimezoneSource::Given(arg.as_ref()))
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

    /// The body is one seam call, and the argument it reaches with is the
    /// template's — the enum's `Given` arm, not the fixed zone of its neighbours.
    #[test]
    fn it_reaches_the_one_timezone_seam_with_the_argument() {
        let h = TestFilterHost {
            do_timezone: Some(Value::Str("PARIS".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(
                Value::Opaque(0),
                Some(Value::Str("Europe/Paris".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("PARIS".into())
        );
        assert_eq!(h.calls(), vec!["do_timezone"]);
    }

    /// The argument is forwarded, **not inspected**: a body that pre-resolved the
    /// zone name (or refused an unknown one) would be a second `do_timezone`. The
    /// `Opaque` argument is a `tzinfo` instance the Rust side could not classify
    /// at all, and the body still reaches the seam once.
    #[test]
    fn it_does_not_inspect_the_argument() {
        let h = TestFilterHost {
            do_timezone: Some(Value::Str(String::new().into())),
            ..TestFilterHost::default()
        };
        run(Value::Opaque(0), Some(Value::Opaque(1)), &call(&h)).unwrap();
        assert_eq!(h.calls(), vec!["do_timezone"]);
    }

    /// The absent-argument arm exists and reaches the seam as `None`: it is the
    /// call Python's own signature would raise on, kept expressible rather than
    /// forbidden (see the trait's doc for `TimezoneSource::Given`).
    #[test]
    fn the_absent_argument_still_reaches_the_seam() {
        let h = TestFilterHost {
            do_timezone: Some(Value::Str(String::new().into())),
            ..TestFilterHost::default()
        };
        run(Value::Opaque(0), None, &call(&h)).unwrap();
        assert_eq!(h.calls(), vec!["do_timezone"]);
    }
}
