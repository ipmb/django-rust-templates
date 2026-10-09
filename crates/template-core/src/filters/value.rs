//! The filter value model (§D1).
//!
//! `Value` carries Python's common scalars natively and everything else by
//! handle. The forcing constraint is §10.4's fan-out contract rather than
//! performance: a fan-out agent must be able to prove its filter with
//! `cargo test -p template-core` in a bare shell — no `.so`, no
//! `maturin develop`, no venv, no `.django`. A value that is only an opaque host
//! handle makes every filter untestable there, and every filter's `#[cfg(test)]`
//! unit test would become an integration test against Python.

/// An index into the host's value table.
///
/// Owned by the **host**, for the duration of one top-level filter call — not
/// the parse-scoped `NodeRef` table the resolver uses. Filter values are created
/// *by filters* (`dictsort` returns a fresh list, `template_localtime` may
/// return a new object) and outlive no single node, so the two lifetimes are
/// genuinely different and sharing one table would give the resolver's identity
/// guarantees to values that have no identity to guarantee.
pub type ValueId = u32;

/// A value crossing the filter seam.
///
/// **The `PartialEq` is the derived, structural one, deliberately.** Python's
/// `SafeString("x") == "x"` is `True`, so a folded equality looks like the
/// faithful choice — and it is the wrong one here, because the asymmetry that
/// matters runs the other way: every fan-out agent's unit tests compare a result
/// against an expected `Value`, and under a folded equality a *dropped safety
/// marker* becomes invisible (`Value::Str("x")` would satisfy an assertion
/// written as `Value::Safe("x")`). Structural equality is what makes "the marker
/// cannot be dropped separately from the text" testable at all.
///
/// The cost is real and lands on exactly one filter, `cut`, which compares a
/// `Value` against a literal: under structural equality `Value::Safe(";") !=
/// Value::Str(";")` where Python's `SafeString(";") != ";"` is false, which would
/// flip `cut`'s `mark_safe` branch (`defaultfilters.py:447-450`). So the rule
/// that contains it, and it is a rule for every body rather than a note about
/// `cut`:
///
/// > A filter body compares **converted primitives** — `String`, `IntValue`,
/// > `f64`, `char` — never two `Value`s. This `PartialEq` is for tests, not for
/// > bodies.
///
/// `cut` therefore tests `to_str(arg)? != ";"`, which is `str.__ne__` and so
/// `SafeString`-faithful by construction.
#[derive(Clone, PartialEq)]
pub enum Value {
    None,
    Bool(bool),
    Int(i64),
    /// A Python `int` outside `i64`, as canonical decimal text — the same
    /// representation `AttrValue::BigInt` uses, for the same reason.
    BigInt(Box<str>),
    Float(f64),
    Str(Box<str>),
    /// A `SafeString`: the text and the marker in **one** variant, so no filter
    /// can produce the text while dropping the marker by forgetting a second
    /// field. That is what makes §D1's "the marker cannot be dropped separately
    /// from the text" testable rather than aspirational.
    Safe(Box<str>),
    List(Vec<Value>),
    /// Anything else the host owns — a `datetime`, a `Decimal`, a `dict`, a
    /// tuple, a model instance, a `Promise`.
    Opaque(ValueId),
}

impl Value {
    /// Python's `type(value).__name__`, which the corpus's §D5 comparison uses.
    ///
    /// **`Safe` reports `"SafeString"`, not `"str"`, and that is the whole point
    /// of the method.** §D5 exists because a filter's result *type* is part of
    /// its behaviour: `escape` returning an ordinary `str` instead of a
    /// `SafeString` is invisible to a text comparison and is exactly the bug that
    /// costs a template its escaping. Python's own name for the correct answer is
    /// `"SafeString"` (`type(mark_safe("x")).__name__`, measured), so anything
    /// shorter here would make the wrong answer indistinguishable from the right
    /// one at the one place built to tell them apart.
    ///
    /// `BigInt` reports `"int"`: the variant is a Rust representation of a Python
    /// `int` outside `i64`, and `("int", …)` is the expectation Django has.
    ///
    /// **`Opaque` reports `"object"`, which is a known limitation rather than an
    /// answer.** The host owns that type and a `&'static str` cannot ask it, so a
    /// corpus row whose result is genuinely an `Opaque` (a `datetime` returned by
    /// `first`, say) would compare `"object"` against `"datetime"` and report a
    /// mismatch that is not one. Task 3's harness must therefore route an
    /// `Opaque` result's name through the host, or drop the type half of the
    /// comparison for that row — not "fix" it here, which would mean this method
    /// returning a `String` and a host call on every filter result.
    pub fn type_name(&self) -> &'static str {
        match self {
            Value::None => "NoneType",
            Value::Bool(_) => "bool",
            Value::Int(_) | Value::BigInt(_) => "int",
            Value::Float(_) => "float",
            Value::Str(_) => "str",
            Value::Safe(_) => "SafeString",
            Value::List(_) => "list",
            Value::Opaque(_) => "object",
        }
    }
}

/// A hand-written `Debug`, because the derived one is unusable for the two
/// container-shaped variants.
///
/// `Value::List` is the reason: a filter test that fails prints its values, and
/// a 10,000-element list rendered in full buries the assertion. The precedent is
/// `parser/resolve.rs`'s `Step::Found`, which prints the handle rather than the
/// container. Text is **not** truncated — a value's text is what a filter test
/// is about, and `Str`/`Safe` are the two variants a failure message needs
/// verbatim — while `List` shows its length and its first few elements, and
/// `Opaque` shows its id, which is the only thing it has.
impl std::fmt::Debug for Value {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        /// How many elements of a list a failure message shows.
        const SHOWN: usize = 8;

        match self {
            Value::None => write!(f, "None"),
            Value::Bool(b) => write!(f, "{b}"),
            Value::Int(i) => write!(f, "{i}"),
            Value::BigInt(text) => write!(f, "BigInt({text})"),
            Value::Float(x) => write!(f, "{x:?}"),
            Value::Str(text) => write!(f, "Str({text:?})"),
            Value::Safe(text) => write!(f, "Safe({text:?})"),
            Value::List(items) => {
                write!(f, "List[{}]", items.len())?;
                f.debug_list().entries(items.iter().take(SHOWN)).finish()?;
                if items.len() > SHOWN {
                    write!(f, "…+{}", items.len() - SHOWN)?;
                }
                Ok(())
            }
            Value::Opaque(id) => write!(f, "Opaque(#{id})"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Structural, so a dropped safety marker fails an assertion.
    ///
    /// This is the guard for the `PartialEq` decision above, and it is the whole
    /// reason that decision went the way it did: if this assertion were
    /// `assert_eq!`, a filter body that returned `Value::Str` where it owed
    /// `Value::Safe` would satisfy every test written the obvious way.
    #[test]
    fn safe_and_str_are_not_equal() {
        assert_ne!(
            Value::Safe("x".into()),
            Value::Str("x".into()),
            "folded equality would make a dropped safety marker invisible"
        );
        // The same text in the same variant *is* equal, so the assertion above
        // is about the marker and not about `Value` being incomparable.
        assert_eq!(Value::Safe("x".into()), Value::Safe("x".into()));
        assert_eq!(Value::Str("x".into()), Value::Str("x".into()));
    }

    /// `BigInt` reports Python's name, not the Rust variant's, and `Safe` reports
    /// `SafeString` rather than `str` — the latter is the guard that makes §D5's
    /// type comparison able to see a dropped safety marker at all.
    #[test]
    fn type_name_is_pythons_name() {
        assert_eq!(Value::None.type_name(), "NoneType");
        assert_eq!(Value::Bool(false).type_name(), "bool");
        assert_eq!(Value::Int(0).type_name(), "int");
        assert_eq!(Value::BigInt("1".repeat(30).into()).type_name(), "int");
        assert_eq!(Value::Float(0.0).type_name(), "float");
        assert_eq!(Value::Str("".into()).type_name(), "str");
        assert_eq!(Value::Safe("".into()).type_name(), "SafeString");
        assert_eq!(Value::List(vec![]).type_name(), "list");
        assert_eq!(Value::Opaque(7).type_name(), "object");

        // The one that matters: a filter body that drops the marker reports a
        // different name, so a corpus row for `escape` cannot pass on text alone.
        assert_ne!(
            Value::Safe("x".into()).type_name(),
            Value::Str("x".into()).type_name()
        );
    }

    /// `Bool` and `Int` are different types in Python and must stay different
    /// here: `isinstance(True, int)` is true in Python, but `type(True).__name__`
    /// is `bool`, and §D5 compares type names.
    #[test]
    fn a_bool_is_not_an_int() {
        assert_ne!(Value::Bool(true), Value::Int(1));
        assert_ne!(Value::Bool(true).type_name(), Value::Int(1).type_name());
    }

    /// A long list is summarized rather than dumped, and the text variants are
    /// not truncated. Observed by formatting, which is the only way to test a
    /// `Debug`.
    #[test]
    fn debug_summarizes_lists_and_keeps_text_verbatim() {
        let long = Value::List((0..100).map(Value::Int).collect());
        let rendered = format!("{long:?}");
        assert!(rendered.starts_with("List[100]"), "{rendered}");
        assert!(rendered.ends_with("…+92"), "{rendered}");
        assert_eq!(format!("{:?}", Value::Opaque(3)), "Opaque(#3)");

        let text = "a".repeat(500);
        assert_eq!(
            format!("{:?}", Value::Str(text.clone().into())).len(),
            500 + 7
        );
    }
}
