//! The host seam: the tags the parser must ask Python to compile, and the
//! filters it must ask Python to validate.
//!
//! Ports the tag half of §5.2's `ExtensionHost`. `has_tag`/`call_tag` are two
//! methods rather than one because `Parser.parse` tests membership *before*
//! calling — a single `Option`-returning method would have to distinguish "no
//! such tag" from "the tag failed", which the error type does not.
//!
//! `check_filter` is the filter half and is **defaulted**, which is not a
//! convenience: it is what keeps `cargo test -p template-core` runnable with no
//! Python in the process (§10.4). Its default accepts every name, and the shell's
//! host is where Django's `find_filter` and `args_check` live.
//!
//! `template-core` never sees a Python object here: the `NodeDesc` that comes
//! back is already plain data, and the host is the only thing that touches
//! Python.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::token::Token;

pub trait Host {
    /// Whether `command` is a tag the host can compile.
    ///
    /// Called for every block tag that Rust has no compile function for, which
    /// at M2 is every block tag until `builtin_tag` answers.
    fn has_tag(&self, command: &str) -> bool;

    /// Compile `command`, given the token dispatch matched on.
    ///
    /// Called only after `has_tag` returned true for the same `command`.
    fn call_tag(&self, command: &str, token: &Token) -> Result<NodeDesc, Error>;

    /// Django's two filter checks, in Django's order: `Parser.find_filter`
    /// (`base.py:684-692`, which raises `Invalid filter: '%s'`) and then
    /// `FilterExpression.args_check` (`base.py:822-833`, which raises
    /// `%s requires %d arguments, %d provided`).
    ///
    /// **Called from inside `FilterExpression.__init__`'s match loop**, once per
    /// filter and at that point in the iteration, because that is where Django
    /// calls them (`base.py:771-772`). Deferring the check to the shell is not an
    /// option, and the reason is an order rather than a preference: the loop
    /// checks the *gap* before it compiles a filter and *after* it (`upto !=
    /// start` at `:743`, `upto != len(token)` at `:774`), so measured,
    /// `x|does_not_exist||upper` reports `Invalid filter: 'does_not_exist'`
    /// while `x||does_not_exist` reports `Could not parse some characters`. No
    /// batched ordering produces both. The second reason is larger: an error
    /// raised while *materialising* cannot precede one raised while parsing, so
    /// a `{{ x|bad }}` on an earlier line would lose to a later line's unmatched
    /// tag.
    ///
    /// **Validate-only: the return value is `()` and Django's is not.**
    /// `find_filter`'s result is retained — `filters.append((filter_func, args))`
    /// (`base.py:773`) is what renders — so the shell looks the name up a second
    /// time when it materialises the `FilterExpression`. That is safe: the lookup
    /// is a dict read, and nothing mutates `parser.filters` between a token's
    /// compile and its materialisation.
    ///
    /// `arg_count` rather than the arguments themselves, because it is all
    /// `args_check` reads: it does `provided = list(provided)`, then
    /// `plen = len(provided) + 1` (`base.py:823-826`), and consumes `provided`
    /// no further. The pin is `{{ x|upper:5 }}` → `upper requires 1 arguments, 2
    /// provided`, which stops passing the day an element is read.
    ///
    /// **Called after the argument's `Variable` is built, not before.** Django's
    /// per-iteration order is gap → argument `Variable` → `find_filter` →
    /// `args_check` (`base.py:757-772`), and the first two are separable in
    /// outcome: measured, `x|does_not_exist:_bad` reports the *underscore* error
    /// because `Variable('_bad')` raises at `:768` and `find_filter` is never
    /// reached, while `x|does_not_exist:-5` reports `Invalid filter` because
    /// `-5` parses as a literal and control does reach it. An implementation that
    /// asked the host first — tempting, since the filter name is known earlier —
    /// swaps those two.
    fn check_filter(&self, _name: &str, _arg_count: usize) -> Result<(), Error> {
        Ok(())
    }
}

/// A host with no tags at all.
///
/// Used by `_native.parse` until M6 supplies a real registry, and by every Rust
/// unit test that is not specifically testing dispatch order.
pub struct NoHost;

impl Host for NoHost {
    fn has_tag(&self, _command: &str) -> bool {
        false
    }

    fn call_tag(&self, _command: &str, _token: &Token) -> Result<NodeDesc, Error> {
        // Unreachable through `Parser::parse`, which consults `has_tag` first.
        // A panic here rather than a silent `Err` so that a future dispatch bug
        // is loud; `parse` never lets it out of a Rust test.
        unreachable!("NoHost::call_tag is never reached: has_tag always returns false")
    }
}
