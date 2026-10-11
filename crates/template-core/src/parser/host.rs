//! The host seam: the tags the parser must ask Python to compile, and the
//! filters it must ask Python to validate.
//!
//! `has_tag`/`call_tag` are two methods because `Parser.parse` tests membership
//! *before* calling, and one `Option`-returning method could not distinguish "no
//! such tag" from "that tag failed". `check_filter` is defaulted so
//! `cargo test -p template-core` stays runnable with no Python (§10.4).
//! `template-core` never sees a Python object: the `NodeDesc` that comes back is
//! already plain data.

use crate::parser::desc::{NodeDesc, NodeListDesc, NodeRef};
use crate::parser::error::Error;
use crate::token::Token;

pub trait Host {
    /// Whether `command` is a tag the **host** will compile.
    ///
    /// Django's own test, plus one clause. `Parser.add_library` updates
    /// `parser.tags` in place (`base.py:669-670`), so a library loaded *after*
    /// the builtins wins and the parse must ask the host first. But every builtin
    /// has an entry there too, so a bare membership test would route `{% for %}`
    /// to Python and Rust's compile functions would never run: the shell's
    /// `ShellHost.has_tag` excludes a name whose entry is still Django's own
    /// builtin callable *and* is Rust's.
    fn has_tag(&self, command: &str) -> bool;

    /// Whether `command` is a **library** tag the template has loaded — the one
    /// question [`has_tag`]'s `false` cannot be asked twice for.
    ///
    /// `has_tag` answers `false` both for a command the host's parser does not
    /// hold at all (`{% static %}` with no `{% load static %}`, which Django
    /// refuses) and for a loaded library tag Rust compiles whose entry is still
    /// Django's own; without this the parser cannot tell them apart. It is **not**
    /// "is this name in the host's tag dict" — `true` for every loaded name would
    /// claim the 10 library tags Rust does not compile and send them to
    /// `Invalid block tag`; the caller consults this and then the *registry*, which
    /// is what makes the pair safe. Defaulted to `false` for a host with no library
    /// concept, so `cargo test -p template-core` stays Python-free (§10.4).
    ///
    /// [`has_tag`]: Host::has_tag
    fn library_tag_loaded(&self, _command: &str) -> bool {
        false
    }

    /// Compile `command` in Python, against the parser's remaining tokens.
    ///
    /// `tokens` is everything the caller has not consumed, **in parse order**. The
    /// host may consume any prefix of it (a block tag's `parser.parse(...)`) and may
    /// push tokens back (`Parser.prepend_token`); the second element of the return is
    /// what is left, and the caller **replaces** its cursor with it rather than
    /// counting — which is what makes `prepend_token` correct without a special case
    /// (Django's `Parser.parse` leaves the end tag on the stack for
    /// `delete_first_token` to remove, `base.py:564-569`). The host holds no borrow
    /// of the caller's parser, which is why the tokens travel by value.
    fn call_tag(
        &self,
        command: &str,
        token: &Token,
        tokens: &[Token],
    ) -> Result<(NodeDesc, Vec<Token>), Error>;

    /// Django's two filter checks, in Django's order: `Parser.find_filter`
    /// (`base.py:678-682`) and then `FilterExpression.args_check` (`base.py:822-836`).
    ///
    /// **Called from inside `FilterExpression.__init__`'s match loop**, once per filter
    /// and at that point in the iteration (`base.py:771-772`), because the order is
    /// observable: the loop checks the *gap* before and after compiling a filter
    /// (`base.py:747`, `base.py:775`), so `x|does_not_exist||upper` reports `Invalid
    /// filter` while `x||does_not_exist` reports `Could not parse some characters`. It
    /// is called **after** the argument's `Variable` is built, not before —
    /// `x|does_not_exist:_bad` reports the *underscore* error (`Variable('_bad')`
    /// raises at `base.py:770`) while `x|does_not_exist:-5` reports `Invalid filter`.
    ///
    /// **Returns the filter Django bound, by handle.** `find_filter`'s result is what
    /// renders (`base.py:773`), and a *name* looked up again later differs whenever a
    /// `{% load %}` sits between the two moments: `add_library` is
    /// `self.filters.update(lib.filters)` (`base.py:669-670`) and `update` **rebinds**
    /// — measured, `{{ x|upper }}{% load custom %}{{ y|upper }}` binds token 1 to
    /// custom's `upper` where Django gave it the builtin
    /// (`tests/test_filter_rebinding.py`). So the shell looks the name up once, at
    /// Django's line, and returns its index into the arena [`Host::alloc_slot`] serves,
    /// as [`FilterCall::handle`].
    ///
    /// `arg_count` is all `args_check` reads (`plen = len(provided) + 1`,
    /// `base.py:823-826`); `None` means "no host, carry no handle", the shape
    /// `_native.parse(source, check_filter=None)` relies on.
    ///
    /// [`FilterCall::handle`]: crate::parser::desc::FilterCall::handle
    fn check_filter(&self, _name: &str, _arg_count: usize) -> Result<Option<NodeRef>, Error> {
        Ok(None)
    }

    /// Python's `repr()` of a `str`, for the messages Django builds with `%r`.
    ///
    /// Two tags interpolate **user text** through `%r` — `{% with %}`'s
    /// `"%r received an invalid token: %r"` (`defaulttags.py:1682-1684`) and
    /// `{% resetcycle %}`'s tag name — so the escape rules matter. Python escapes
    /// every non-`Py_UNICODE_ISPRINTABLE` code point (Cf, Cs, Co, Cn, Zl, Zp, Zs)
    /// while Rust's `char::is_control()` is Cc alone; measured,
    /// `{% with a=1 foo<U+00AD>bar %}` renders `'foo\xadbar'`.
    ///
    /// `py_repr_str`, exact on ASCII and asserting it, is the **default** rather
    /// than the implementation: the shell overrides this with the real `repr()`, so
    /// a Rust-only test needing a non-ASCII token here must supply a host rather than
    /// widen `py_repr_str` (which its own doc comment forbids).
    fn repr(&self, s: &str) -> String {
        crate::parser::tags::py_repr_str(s)
    }

    /// Reserve an entry in the per-parse reference table and return its index.
    ///
    /// The table is the one side collection two tags share. **It reserves and does
    /// not fill**: `{% cycle %}` reserves here during the *parse* and the
    /// *materialiser* stores the object later ([`NodeDesc::slot`]), which is what
    /// lets a second `{% cycle c %}` find the first one's node instead of building a
    /// second. (`{% if %}`'s `FilterExpression` is the other filler, built during the
    /// parse because that is where Django builds it.)
    ///
    /// `&self` rather than `&mut self`, because the parser owns its host by shared
    /// reference and the shell keeps the table behind interior mutability, so a Rust
    /// host needing a counter must use a `Cell` — fine for a test host, not for
    /// [`NoHost`], whose `&NoHost` is const-promoted to `'static`.
    ///
    /// Reserved-before-use is why the shell's table arrives *sized*: `register_ref` is
    /// `list.__setitem__`, not `append`, so a reserved-but-unfilled entry reads `None`.
    fn alloc_slot(&self) -> usize;

    /// `TemplateIfParser.create_var`'s `self.template_parser.compile_filter(value)`
    /// (`defaulttags.py:929`) — the `FilterExpression` behind one `{% if %}` operand.
    ///
    /// **A handle, not a descriptor**, unlike
    /// [`Parser::compile_filter`](crate::parser::Parser::compile_filter): a `{{ … }}`
    /// crosses as an `AttrValue::FilterExpr` and the *materialiser* builds it, while a
    /// condition's operand is the host's own object, built while the tag compiles. They
    /// are not interchangeable — `TemplateLiteral.value` has to answer
    /// `value.resolve(context, ignore_failures=True)` (`defaulttags.py:917-918`), and
    /// the handle is what makes the object answering it the *host's* rather than one
    /// rebuilt to look like it.
    ///
    /// `token` is one **merged** bit, not the whole condition: `smartif`'s
    /// `merge_tokens` has already folded `is not` and `not in`, and an operator bit
    /// never reaches here (`TemplateIfParser.translate_token` tries `OPERATORS[token]`
    /// first, `smartif.py:179-185`). So the calls run in Django's order, before the
    /// operator algorithm, and a bad filter in a *later* operand beats an operator
    /// error: measured, `{% if a and and b|nope %}` reports the filter error on both
    /// engines. The error is the host's, flattened like [`Host::check_filter`]'s —
    /// `{% if a|does_not_exist %}` reports `Invalid filter: 'does_not_exist'` and
    /// `{% if 1>2 %}` reports `Could not parse the remainder: '>2' from '1>2'`.
    fn compile_filter(&self, token: &str) -> Result<NodeRef, Error>;

    /// Build the `PartialNode` for `{% partial name %}` — `partial_func`'s
    /// second arm (`defaulttags.py:1277-1281`).
    ///
    /// **A handle, not a descriptor.** `partial_func` builds
    /// `PartialNode(name, partial_mapping=DeferredSubDict(parser.extra_data,
    /// "partials"))`, a **live view over a dict that outlives the parse**: it is read
    /// at *render* time, so `{% partial p %}` may precede `{% partialdef p %}` and
    /// still render. A `NodeDesc` cannot represent a value re-read later; the host can,
    /// because it owns the dict. The name is not resolved here — resolution is
    /// `PartialNode.render`'s `self.partial_mapping[self.partial_name]`
    /// (`defaulttags.py:430-435`), so a compile-time lookup would raise the *render*
    /// message at the wrong moment.
    ///
    /// **Defaulted to an error rather than a handle.** `NoHost` has no `extra_data`
    /// and no materialiser, so `NodeRef(0)` would be a lie whose failure is a silent
    /// empty slot at materialisation. The tag's argument-count failures still raise
    /// before this call, so they stay testable under `NoHost`.
    fn partial_node(&self, _name: &str) -> Result<NodeRef, Error> {
        Err(Error::internal(
            "this host cannot build a PartialNode: implement Host::partial_node",
        ))
    }

    /// Register `{% partialdef name %}`'s body and return the node —
    /// `partialdef_func`'s tail (`defaulttags.py:1250-1265`).
    ///
    /// The body arrives as the **descriptor** Rust parsed, and the host materialises
    /// it **exactly once**: `nodelist` is shared, by identity, between the
    /// `PartialTemplate` stored in `extra_data` and the `PartialDefNode` returned
    /// (`defaulttags.py:1258`, `defaulttags.py:1265`), so a second build would give two
    /// objects where Django has one.
    ///
    /// `source_start`/`source_end` are the character offsets `PartialTemplate` keeps
    /// for `find_partial_source` (`base.py:314-322`), read from the two tokens'
    /// `position` attributes — `None` under the non-debug lexer, hence `Option`.
    ///
    /// The duplicate check is the **host's**, not the caller's: it raises Django's
    /// `TemplateSyntaxError` naming `parser.origin.name`, Python state Rust does not
    /// hold. `token` is passed for `Host::call_tag`'s reason — the shell attaches it to
    /// the exception object it stores for `django_exception` to re-raise by identity.
    fn register_partial_def(
        &self,
        _token: &Token,
        _name: &str,
        _inline: bool,
        _body: &NodeListDesc,
        _source_start: Option<usize>,
        _source_end: Option<usize>,
    ) -> Result<NodeRef, Error> {
        Err(Error::internal(
            "this host cannot register a partial definition: implement \
             Host::register_partial_def",
        ))
    }
}

/// A host with no tags at all.
///
/// Used by `_native.parse` when no registry is supplied, and by every Rust unit
/// test that is not specifically testing dispatch order.
pub struct NoHost;

impl Host for NoHost {
    fn has_tag(&self, _command: &str) -> bool {
        false
    }

    fn call_tag(
        &self,
        _command: &str,
        _token: &Token,
        _tokens: &[Token],
    ) -> Result<(NodeDesc, Vec<Token>), Error> {
        // Unreachable through `Parser::parse`, which consults `has_tag` first: a
        // panic rather than a silent `Err`, so a future dispatch bug is loud.
        unreachable!("NoHost::call_tag is never reached: has_tag always returns false")
    }

    fn alloc_slot(&self) -> usize {
        // Always 0: `NoHost` is the *null* host with no table, so it has no second
        // index to give — and nothing built under it is ever materialised. The
        // `unreachable!` alternative would be a lie, because this *is* reachable (a
        // tag that allocates a slot is compiled under `NoHost` in every Rust test
        // that parses it). A test needing two distinct indices needs a host with a
        // counter, which belongs in `tags::support`: `NoHost` must stay free of
        // interior mutability so that `&NoHost` remains const-promotable.
        0
    }

    fn compile_filter(&self, _token: &str) -> Result<NodeRef, Error> {
        // `NodeRef(0)`, for `alloc_slot`'s reason: the null host has no table, so
        // the handle names nothing, and nothing built under it is ever materialised.
        // It accepts every token, the same blanket acceptance `check_filter`'s
        // default gives — so a Rust unit test cannot see a *filter* error in a
        // condition; the descriptor-shape tests are about the tree, and message
        // parity for those errors belongs to `_native.parse` with the shell's host.
        Ok(NodeRef(0))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_host_without_the_partial_seam_says_so_rather_than_returning_a_handle() {
        // `NoHost` is the default every Rust-only row parses under, and the failure
        // mode pinned here is *silent*: a default returning `NodeRef(0)` would hand
        // the materialiser a handle into a table that does not exist, surfacing much
        // later as an empty slot. So the default is loud, like `NoHost::call_tag`'s
        // `unreachable!`.
        let error = NoHost
            .partial_node("p")
            .expect_err("NoHost cannot build a PartialNode");
        assert!(error.message().contains("partial_node"), "{error:?}");
    }
}
