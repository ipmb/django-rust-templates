//! `timesince` — `defaultfilters.py:811-821`.
//!
//! Stub. §10.3's M4 row owns this filter and **Task 10** implements it; an
//! entry with a stub body rather than a missing registry row is what makes the
//! milestone's progress visible and keeps the registry a file no fan-out agent
//! edits.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `timesince`'s body. The signature is the contract every group codes against:
/// `run` is what the registry holds, the second parameter is the filter's one
/// optional argument, and `call` is the seam. The leading underscores are
/// deliberate — a stub uses none of the three, and `-D warnings` counts an
/// unused binding as a warning.
pub fn run(_value: Value, _arg: Option<Value>, _call: &FilterCall<'_>) -> Result<Value, Error> {
    Err(Error::unimplemented_filter("timesince"))
}
