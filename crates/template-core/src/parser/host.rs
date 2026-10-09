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
    /// **Validate-only: the return value is `()` and Django's is not, and that
    /// is known to be wrong for `{% load %}`.** Stated as a defect rather than a
    /// trade-off, because the sentence that used to stand here — "nothing mutates
    /// `parser.filters` between a token's compile and its materialisation" — was
    /// true about the wrong interval and would have been believed at M6.
    ///
    /// `find_filter`'s result is retained — `filters.append((filter_func, args))`
    /// (`base.py:773`) is what renders — so the shell looks the name up a second
    /// time when it materialises the `FilterExpression`. That lookup is a dict
    /// read, but not a *stable* one: `Parser.add_library` is
    /// `self.filters.update(lib.filters)` (`base.py:668-670`), and `update`
    /// **rebinds** a name that is already there. Measured on the pinned tree:
    ///
    /// ```text
    /// parser.filters["upper"] before load: the builtin
    ///                         after  load: 'SHADOWED'   rebound = True
    /// ```
    ///
    /// The interval that matters is compile → materialisation, and our
    /// materialisation happens after the entire parse. So
    ///
    /// ```text
    /// {{ x|upper }}{% load custom %}{{ y|upper }}
    /// ```
    ///
    /// binds token 1 to custom's `upper` here, where Django bound it to the
    /// builtin at `:771`. Unobservable today only because `{% load %}` is M6's —
    /// so this is a prerequisite of M6, not a bug in the committed tree: **the
    /// return type becomes a handle before `{% load %}` ships.**
    ///
    /// The handle is cheap because the table already exists. The shell keeps the
    /// function it looked up in the same arena that holds `{% resetcycle %}`'s
    /// `Ref` and puts its index on the wire as `AttrValue::Ref`, so the
    /// descriptor carries the *function* Django would have carried and the name
    /// is looked up exactly once, at `:771`. The M6 test is a sandwich — a
    /// builtin use, a `{% load %}` that shadows the name, then a shadowed use,
    /// asserting the **first** rendered with the builtin. A test with the
    /// shadowed use alone passes under either design, because its token is after
    /// the load.
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

    /// Reserve an entry in the per-parse reference table and return its index.
    ///
    /// The table is the one side collection two tags share, and this is the only
    /// way to get an index into it. **It reserves and does not fill**, and that
    /// split is the whole point: `{% cycle %}` reserves here during the *parse*,
    /// and the *materialiser* stores the object later ([`NodeDesc::slot`]) —
    /// which is what lets a second `{% cycle c %}` find the first one's node
    /// instead of building a second one. The other filler is `{% if %}`'s
    /// `FilterExpression`, which the host builds during the parse because that is
    /// where Django builds it.
    ///
    /// `&self` rather than `&mut self` because there is one table per parse and
    /// the parser owns its host by shared reference; the shell keeps the table
    /// behind interior mutability. A Rust implementation that needs a counter
    /// must therefore use a `Cell` — which is fine for a test host and is not
    /// fine for [`NoHost`], whose `&NoHost` is const-promoted to `'static` in
    /// every `Parser::new(tokens, &NoHost)`.
    ///
    /// Reserved-before-use is why the shell's table arrives *sized*: the
    /// materialiser's `register_ref` is `list.__setitem__`, not `append`, and an
    /// entry that has been reserved but not filled is a `None` placeholder.
    fn alloc_slot(&self) -> usize;
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

    fn alloc_slot(&self) -> usize {
        // Always 0, and stated rather than hidden: `NoHost` is the *null* host —
        // it has no table, so it has no second index to give. Nothing built
        // under it is ever materialised (`_native.parse` always builds the
        // shell's host; `NoHost` appears only in Rust unit tests and in
        // `builtin_tag_stubs`'s probe), so a collision cannot be observed here.
        //
        // The `unreachable!` alternative would be a lie: unlike `call_tag`
        // above, this *is* reachable — a tag that allocates a slot is compiled
        // under `NoHost` in every Rust test that parses it. A test that needs
        // two distinct indices needs a host with a counter, and that host
        // belongs in `tags::support`, not here — `NoHost` must stay free of
        // interior mutability so that `&NoHost` remains const-promotable.
        0
    }
}
