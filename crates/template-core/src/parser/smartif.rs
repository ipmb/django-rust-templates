//! `django.template.smartif`: the `{% if %}` expression parser.
//!
//! A direct port of Django's top-down parser (`smartif.py`, 215 lines at
//! 6.0.9). Rust owns the parse and Python owns the tree that renders, so this
//! module builds the parse tree and detects syntax errors — and nothing in the
//! engine calls it yet: `{% if %}` is M5, so `smartif` is machinery with no
//! consumer at M3. The one caller Django has, `TemplateIfParser`
//! (`defaulttags.py:921`), differs from the base `IfParser` in exactly two
//! ways, both measured against the pinned tree and both pinned by tests here:
//!
//! - `error_class`: the base parser raises `ValueError`, the subclass raises
//!   `TemplateSyntaxError` (`smartif.py:157`, `defaulttags.py:922`);
//! - `create_var`/`display`: the base parser wraps the raw token and displays
//!   its `repr`, while the subclass compiles the token and displays it raw —
//!   so the subclass runs `compile_filter` during `__init__` and can fail
//!   before `parse` is reached.
//!
//! `nud`/`led` and the operator table are ported; `eval` (the boolean
//! semantics) is M5's, because nothing renders an `{% if %}` until then.

use crate::parser::desc::NodeRef;
use crate::parser::error::Error;
use crate::parser::tags::py_repr_str;

/// An index into an [`IfParser`]'s arena.
///
/// Django's nodes point at each other directly (`operator.first = left`); Rust
/// cannot, so a node is addressed by index into the arena the parser owns.
pub type NodeId = usize;

/// The arena index of the `EndToken` sentinel.
///
/// Django's `EndToken` is a module-level singleton (`EndToken = EndToken()`,
/// `smartif.py:153`) and `parse` compares against it with `is`.
pub const END: NodeId = 0;

/// The depth `expression` may nest before it refuses.
///
/// Django has **no** explicit bound. Measured against the pinned tree: 497
/// `not`s parse and 498 raises `RecursionError` (CPython's default recursion
/// limit is 1000, about two frames per prefix operator), while 100 000 `and`s
/// parse because infix parsing is a loop. Rust has no interpreter recursion
/// limit, so without a guard a hostile template overflows the native stack —
/// which aborts the process rather than raising, and so cannot be caught. This
/// makes the guard, not the stack, the thing that fires.
///
/// That abort was **observed**, not assumed, by disabling the guard below and
/// running the deep-nesting test: in a debug `cargo test` build 1010 and 2000
/// `not`s parse and 4000 aborts with `fatal runtime error: stack overflow` /
/// SIGABRT before the test can report anything. So the native limit for this
/// recursion sits between 2000 and 4000 frames *in that build*, and a value of
/// 1000 leaves the guard a factor of two to four clear of it. It is the only
/// place this port deliberately diverges from Django's observable behaviour:
/// Django refuses at ~498 and this engine parses to 1000. The direction is the
/// safe one — a too-permissive engine renders a template Django's interpreter
/// could not, rather than rejecting a valid one — and the value and the message
/// are deliberately not Django's, because Django's behaviour here is an
/// interpreter artifact, not a specification (see the review questions).
///
/// The frame cost is not a constant: it moves with the build (a release build's
/// frames are smaller), the thread's stack size and the enclosing call depth,
/// so 1000 is a chosen margin rather than a derived bound.
pub const MAX_NESTING_DEPTH: usize = 1000;

/// Which Python exception a `smartif` parse failure becomes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorClass {
    /// `smartif.IfParser.error_class = ValueError`.
    Value,
    /// `defaulttags.TemplateIfParser.error_class = TemplateSyntaxError`.
    TemplateSyntax,
}

/// A `smartif` parse failure: the message, and the class it is raised as.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IfError {
    pub class: ErrorClass,
    pub message: String,
}

impl IfError {
    fn new(class: ErrorClass, message: impl Into<String>) -> Self {
        Self {
            class,
            message: message.into(),
        }
    }
}

/// One of `smartif.OPERATORS`' thirteen keys.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Operator {
    Or,
    And,
    Not,
    In,
    NotIn,
    Is,
    IsNot,
    Eq,
    Ne,
    Gt,
    Gte,
    Lt,
    Lte,
}

impl Operator {
    /// `OPERATORS[key]` — `translate_token`'s lookup, `None` on the `KeyError`
    /// that Django then turns into `create_var`. (`translate_token` also
    /// catches `TypeError`, for an unhashable token; a `String` token cannot
    /// raise it, so that arm is unrepresentable here.)
    pub fn from_key(key: &str) -> Option<Self> {
        Some(match key {
            "or" => Self::Or,
            "and" => Self::And,
            "not" => Self::Not,
            "in" => Self::In,
            "not in" => Self::NotIn,
            "is" => Self::Is,
            "is not" => Self::IsNot,
            "==" => Self::Eq,
            "!=" => Self::Ne,
            ">" => Self::Gt,
            ">=" => Self::Gte,
            "<" => Self::Lt,
            "<=" => Self::Lte,
            _ => return None,
        })
    }

    /// `operator.id` — the `OPERATORS` key, interpolated into every message.
    pub fn key(self) -> &'static str {
        match self {
            Self::Or => "or",
            Self::And => "and",
            Self::Not => "not",
            Self::In => "in",
            Self::NotIn => "not in",
            Self::Is => "is",
            Self::IsNot => "is not",
            Self::Eq => "==",
            Self::Ne => "!=",
            Self::Gt => ">",
            Self::Gte => ">=",
            Self::Lt => "<",
            Self::Lte => "<=",
        }
    }

    /// `operator.lbp` — the binding power. Precedence follows Python's.
    pub fn bp(self) -> u8 {
        match self {
            Self::Or => 6,
            Self::And => 7,
            Self::Not => 8,
            Self::In | Self::NotIn => 9,
            Self::Is
            | Self::IsNot
            | Self::Eq
            | Self::Ne
            | Self::Gt
            | Self::Gte
            | Self::Lt
            | Self::Lte => 10,
        }
    }

    /// Whether this operator was made by `prefix` (`not`) rather than `infix`.
    ///
    /// `not` is the only prefix operator in `OPERATORS`; its `nud` is its own
    /// and its `led` is `TokenBase.led`. Every other operator is the reverse.
    pub fn is_prefix(self) -> bool {
        self == Self::Not
    }
}

/// `smartif.Literal.value` / `defaulttags.TemplateLiteral.value`.
#[derive(Debug, Clone, PartialEq)]
pub enum LiteralValue {
    /// `IfParser.create_var` is `Literal(value)`, with `value` the raw token;
    /// `eval` returns it unchanged. `smartif.py:214-215`.
    Raw(String),
    /// `TemplateIfParser.create_var` is
    /// `TemplateLiteral(self.template_parser.compile_filter(value), value)`.
    /// The compiled `FilterExpression` is host-owned, so it crosses as a
    /// [`NodeRef`] handle. `defaulttags.py:928-929`.
    Compiled(NodeRef),
}

/// `smartif.Literal` / `defaulttags.TemplateLiteral`.
///
/// `display` is pre-rendered because the base class's `display()` is Python's
/// `repr(self.value)` and the subclass's is `self.text`; deriving it from
/// `value` would lose the difference between them.
#[derive(Debug, Clone, PartialEq)]
pub struct Literal {
    pub value: LiteralValue,
    /// `Literal.display()` / `TemplateLiteral.display()`.
    pub display: String,
}

/// `smartif.TokenBase`: a translated token, and after `parse` a tree node.
///
/// Django has one class hierarchy for both — `translate` makes a token, `nud`
/// and `led` mutate it into a tree node, and `parse` returns the root — so this
/// one struct is both, with `first`/`second` addressing the arena.
#[derive(Debug, Clone, PartialEq)]
pub struct TokenBase {
    pub kind: IfToken,
    pub first: Option<NodeId>,
    pub second: Option<NodeId>,
}

/// The `TokenBase` subclass a node is — Django's `id` and `value`, folded.
#[derive(Debug, Clone, PartialEq)]
pub enum IfToken {
    /// An `infix` or `prefix` operator instance.
    Operator(Operator),
    /// `Literal` / `TemplateLiteral`.
    Literal(Literal),
    /// `EndToken`.
    End,
}

/// A cheap copy of a node's kind, so `nud`/`led` can match on it and then
/// mutate the arena without holding a borrow of the kind.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Tag {
    Op(Operator),
    Literal,
    End,
}

/// The `parser` a `TemplateIfParser` is built with.
///
/// `TemplateIfParser.create_var` calls `self.template_parser.compile_filter(
/// value)` and stores the result. The result is a host-owned
/// `FilterExpression`, which `template-core` cannot hold, so it crosses as the
/// [`NodeRef`] handle `desc.rs` reserves. `template-core`'s real
/// `compile_filter` (M3 Tasks 2-3) is not wired here: nothing calls `smartif`
/// at M3, so the only M3 implementation is the test stand-in.
pub trait CompileFilter {
    /// `Parser.compile_filter(token)`.
    fn compile_filter(&self, token: &str) -> Result<NodeRef, Error>;
}

/// `smartif.IfParser` / `defaulttags.TemplateIfParser`.
pub struct IfParser {
    /// Every node: the `End` sentinel at [`END`], then one per token.
    arena: Vec<TokenBase>,
    /// The arena indices of the translated tokens, in source order —
    /// Django's `self.tokens`.
    tokens: Vec<NodeId>,
    /// `self.pos`.
    pos: usize,
    /// `self.current_token`, by arena index.
    current: NodeId,
    /// `self.error_class`.
    error_class: ErrorClass,
    /// Prefix-nesting depth, for [`MAX_NESTING_DEPTH`].
    depth: usize,
}

impl IfParser {
    /// `smartif.IfParser(tokens)` — `error_class = ValueError`.
    ///
    /// Infallible because the base `create_var` cannot fail: it is
    /// `Literal(value)`. The proposed contract had `Literal.__init__` calling
    /// `compile_filter`; the pinned source does not, and only
    /// `TemplateIfParser.create_var` does.
    pub fn new(tokens: Vec<String>) -> Self {
        let mut arena = vec![end_node()];
        let mut ids = Vec::new();
        for key in merge_tokens(&tokens) {
            let node = match Operator::from_key(&key) {
                Some(op) => operator_node(op),
                None => literal_node(Literal {
                    display: py_repr_str(&key),
                    value: LiteralValue::Raw(key),
                }),
            };
            ids.push(arena.len());
            arena.push(node);
        }
        Self::assemble(arena, ids, ErrorClass::Value)
    }

    /// `defaulttags.TemplateIfParser(parser, tokens)` — `error_class =
    /// TemplateSyntaxError`.
    ///
    /// Fallible because `compile_filter` runs during `__init__` (Django's
    /// `translate` -> `create_var`) and can raise.
    pub fn template(compiler: &dyn CompileFilter, tokens: Vec<String>) -> Result<Self, Error> {
        let mut arena = vec![end_node()];
        let mut ids = Vec::new();
        for key in merge_tokens(&tokens) {
            let node = match Operator::from_key(&key) {
                Some(op) => operator_node(op),
                None => literal_node(Literal {
                    value: LiteralValue::Compiled(compiler.compile_filter(&key)?),
                    display: key,
                }),
            };
            ids.push(arena.len());
            arena.push(node);
        }
        Ok(Self::assemble(arena, ids, ErrorClass::TemplateSyntax))
    }

    fn assemble(arena: Vec<TokenBase>, tokens: Vec<NodeId>, error_class: ErrorClass) -> Self {
        let mut parser = Self {
            arena,
            tokens,
            pos: 0,
            current: END,
            error_class,
            depth: 0,
        };
        // `IfParser.__init__` ends with `self.current_token = self.next_token()`.
        parser.current = parser.next_token();
        parser
    }

    /// `IfParser.parse()`.
    pub fn parse(&mut self) -> Result<NodeId, IfError> {
        let root = self.expression(0)?;
        if self.current != END {
            return Err(IfError::new(
                self.error_class,
                format!(
                    "Unused '{}' at end of if expression.",
                    self.display(self.current),
                ),
            ));
        }
        Ok(root)
    }

    /// The arena node `id`.
    pub fn node(&self, id: NodeId) -> &TokenBase {
        &self.arena[id]
    }

    /// `self.error_class`.
    pub fn error_class(&self) -> ErrorClass {
        self.error_class
    }

    fn tag(&self, id: NodeId) -> Tag {
        match &self.arena[id].kind {
            IfToken::Operator(op) => Tag::Op(*op),
            IfToken::Literal(_) => Tag::Literal,
            IfToken::End => Tag::End,
        }
    }

    /// `IfParser.next_token` — `EndToken` once the list is exhausted, and it
    /// does not advance, so repeated calls return it.
    fn next_token(&mut self) -> NodeId {
        if self.pos >= self.tokens.len() {
            END
        } else {
            let id = self.tokens[self.pos];
            self.pos += 1;
            id
        }
    }

    /// `IfParser.expression(rbp=0)`.
    fn expression(&mut self, rbp: u8) -> Result<NodeId, IfError> {
        self.depth += 1;
        if self.depth > MAX_NESTING_DEPTH {
            self.depth -= 1;
            return Err(IfError::new(
                self.error_class,
                format!("If expression is nested more than {MAX_NESTING_DEPTH} deep in if tag."),
            ));
        }

        let t = self.current;
        self.current = self.next_token();
        let mut left = self.nud(t)?;

        while rbp < self.lbp(self.current) {
            let t = self.current;
            self.current = self.next_token();
            left = self.led(t, left)?;
        }

        self.depth -= 1;
        Ok(left)
    }

    /// `TokenBase.nud` / `prefix.Operator.nud` / `Literal.nud` / `EndToken.nud`.
    fn nud(&mut self, t: NodeId) -> Result<NodeId, IfError> {
        match self.tag(t) {
            Tag::Op(op) if op.is_prefix() => {
                let first = self.expression(op.bp())?;
                self.arena[t].first = Some(first);
                self.arena[t].second = None;
                Ok(t)
            }
            Tag::Op(op) => Err(IfError::new(
                self.error_class,
                format!("Not expecting '{}' in this position in if tag.", op.key()),
            )),
            Tag::Literal => Ok(t),
            Tag::End => Err(IfError::new(
                self.error_class,
                "Unexpected end of expression in if tag.".to_string(),
            )),
        }
    }

    /// `infix.Operator.led` / `TokenBase.led`.
    ///
    /// Only the first two arms are reachable from `expression`: the loop guard
    /// is `rbp < lbp(current)`, and a literal and `EndToken` both have
    /// `lbp == 0`. The other two mirror `TokenBase.led` for completeness.
    fn led(&mut self, t: NodeId, left: NodeId) -> Result<NodeId, IfError> {
        match self.tag(t) {
            Tag::Op(op) if !op.is_prefix() => {
                let second = self.expression(op.bp())?;
                self.arena[t].first = Some(left);
                self.arena[t].second = Some(second);
                Ok(t)
            }
            Tag::Op(op) => Err(IfError::new(
                self.error_class,
                format!("Not expecting '{}' as infix operator in if tag.", op.key()),
            )),
            Tag::Literal => Err(IfError::new(
                self.error_class,
                "Not expecting 'literal' as infix operator in if tag.".to_string(),
            )),
            Tag::End => Err(IfError::new(
                self.error_class,
                "Not expecting 'None' as infix operator in if tag.".to_string(),
            )),
        }
    }

    /// `TokenBase.lbp` — the binding power, 0 for a literal or `EndToken`.
    fn lbp(&self, id: NodeId) -> u8 {
        match &self.arena[id].kind {
            IfToken::Operator(op) => op.bp(),
            IfToken::Literal(_) | IfToken::End => 0,
        }
    }

    /// `TokenBase.display()`.
    fn display(&self, id: NodeId) -> String {
        match &self.arena[id].kind {
            IfToken::Operator(op) => op.key().to_string(),
            IfToken::Literal(lit) => lit.display.clone(),
            // `EndToken.display()` is `TokenBase.display()` -> `self.id`, which
            // is `None`. `parse` checks identity against `END` first, so this is
            // unreachable; it mirrors `"%s" % None`.
            IfToken::End => "None".to_string(),
        }
    }
}

/// `IfParser.__init__`'s token merge: `is`+`not` -> `is not`, `not`+`in` ->
/// `not in`, and nothing else (`smartif.py:160-171`).
fn merge_tokens(tokens: &[String]) -> Vec<String> {
    let mut out = Vec::with_capacity(tokens.len());
    let mut i = 0;
    while i < tokens.len() {
        let token = &tokens[i];
        if token == "is" && i + 1 < tokens.len() && tokens[i + 1] == "not" {
            out.push("is not".to_string());
            i += 2;
        } else if token == "not" && i + 1 < tokens.len() && tokens[i + 1] == "in" {
            out.push("not in".to_string());
            i += 2;
        } else {
            out.push(token.clone());
            i += 1;
        }
    }
    out
}

fn end_node() -> TokenBase {
    TokenBase {
        kind: IfToken::End,
        first: None,
        second: None,
    }
}

fn operator_node(op: Operator) -> TokenBase {
    TokenBase {
        kind: IfToken::Operator(op),
        first: None,
        second: None,
    }
}

fn literal_node(literal: Literal) -> TokenBase {
    TokenBase {
        kind: IfToken::Literal(literal),
        first: None,
        second: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;

    /// One row of a measured error table: `(tokens, expected message)`.
    /// Named because `clippy::type_complexity` rejects the anonymous tuple.
    type ErrorCase = (&'static [&'static str], &'static str);

    fn tokens(case: &[&str]) -> Vec<String> {
        case.iter().map(|s| (*s).to_string()).collect()
    }

    /// `TokenBase.__repr__` / `Literal.__repr__` (`smartif.py:40-43`,
    /// `142-143`), so a parsed tree can be compared with Django's own `repr`.
    fn repr(parser: &IfParser, id: NodeId) -> String {
        match &parser.node(id).kind {
            IfToken::End => "()".to_string(),
            IfToken::Literal(lit) => match &lit.value {
                LiteralValue::Raw(value) => format!("(literal {})", py_repr_str(value)),
                LiteralValue::Compiled(_) => "(literal <compiled>)".to_string(),
            },
            IfToken::Operator(op) => {
                let mut parts = vec![op.key().to_string()];
                if let Some(first) = parser.node(id).first {
                    parts.push(repr(parser, first));
                }
                if let Some(second) = parser.node(id).second {
                    parts.push(repr(parser, second));
                }
                format!("({})", parts.join(" "))
            }
        }
    }

    /// An `IfParser` built from `case` and parsed; the base entry point.
    fn parse_base(case: &[&str]) -> Result<NodeId, IfError> {
        IfParser::new(tokens(case)).parse()
    }

    /// A lenient `compile_filter`: every token compiles to the same handle.
    ///
    /// Lenient on purpose — the Rust tests stand in for Django's real
    /// `compile_filter`, and Django's rejects tokens like `+` during
    /// `__init__`. The template-parser rows below use tokens Django's accepts,
    /// so the stand-in is faithful for those rows.
    struct FixedCompiler;

    impl CompileFilter for FixedCompiler {
        fn compile_filter(&self, _token: &str) -> Result<NodeRef, Error> {
            Ok(NodeRef(41))
        }
    }

    /// A `compile_filter` that refuses, to pin the construction-time failure.
    struct RefusingCompiler;

    impl CompileFilter for RefusingCompiler {
        fn compile_filter(&self, token: &str) -> Result<NodeRef, Error> {
            Err(Error::syntax(format!("Invalid filter: '{token}'")))
        }
    }

    /// The template entry point, with the lenient compiler.
    fn parse_template(case: &[&str]) -> Result<NodeId, IfError> {
        IfParser::template(&FixedCompiler, tokens(case))
            .expect("FixedCompiler compiles every token")
            .parse()
    }

    #[test]
    fn operator_table_matches_the_pinned_tree() {
        // Every key of `smartif.OPERATORS` at 6.0.9, its binding power, and
        // whether it came from `prefix` (`not`) or `infix` (the other 12).
        let table: [(Operator, &str, u8, bool); 13] = [
            (Operator::Or, "or", 6, false),
            (Operator::And, "and", 7, false),
            (Operator::Not, "not", 8, true),
            (Operator::In, "in", 9, false),
            (Operator::NotIn, "not in", 9, false),
            (Operator::Is, "is", 10, false),
            (Operator::IsNot, "is not", 10, false),
            (Operator::Eq, "==", 10, false),
            (Operator::Ne, "!=", 10, false),
            (Operator::Gt, ">", 10, false),
            (Operator::Gte, ">=", 10, false),
            (Operator::Lt, "<", 10, false),
            (Operator::Lte, "<=", 10, false),
        ];
        for (op, key, bp, prefix) in table {
            assert_eq!(Operator::from_key(key), Some(op), "from_key({key:?})");
            assert_eq!(op.key(), key, "key of {op:?}");
            assert_eq!(op.bp(), bp, "bp of {key:?}");
            assert_eq!(op.is_prefix(), prefix, "prefix of {key:?}");
        }
        // `KeyError` in `translate_token`: any token that is not a key.
        assert_eq!(Operator::from_key("xor"), None);
        assert_eq!(Operator::from_key(""), None);
        assert_eq!(Operator::from_key("literal"), None);
    }

    #[test]
    fn translate_merges_is_not_and_not_in_into_one_token() {
        // `IfParser.__init__` folds exactly these two pairs before the lookup.
        let parser = IfParser::new(tokens(&["is", "not"]));
        assert_eq!(parser.tokens.len(), 1);
        assert_eq!(
            parser.node(parser.tokens[0]).kind,
            IfToken::Operator(Operator::IsNot),
        );

        let parser = IfParser::new(tokens(&["not", "in"]));
        assert_eq!(parser.tokens.len(), 1);
        assert_eq!(
            parser.node(parser.tokens[0]).kind,
            IfToken::Operator(Operator::NotIn),
        );

        // The merge is exactly those two pairs, not "is" + any second word.
        let parser = IfParser::new(tokens(&["is", "in"]));
        assert_eq!(parser.tokens.len(), 2);
        assert_eq!(
            parser.node(parser.tokens[0]).kind,
            IfToken::Operator(Operator::Is),
        );
        assert_eq!(
            parser.node(parser.tokens[1]).kind,
            IfToken::Operator(Operator::In),
        );

        let parser = IfParser::new(tokens(&["not", "not"]));
        assert_eq!(parser.tokens.len(), 2);
    }

    #[test]
    fn translate_maps_operator_keys_and_turns_everything_else_into_a_literal() {
        let parser = IfParser::new(tokens(&["or", "foo", "(", "+"]));
        assert_eq!(
            parser.node(parser.tokens[0]).kind,
            IfToken::Operator(Operator::Or),
        );
        for (id, raw) in [
            (parser.tokens[1], "foo"),
            (parser.tokens[2], "("),
            (parser.tokens[3], "+"),
        ] {
            assert_eq!(
                parser.node(id).kind,
                IfToken::Literal(Literal {
                    value: LiteralValue::Raw(raw.to_string()),
                    display: py_repr_str(raw),
                }),
            );
        }
    }

    #[test]
    fn parse_builds_the_tree_djangos_operator_precedence_produces() {
        // Each expected string is Django's own `repr()` of the parsed tree,
        // measured on the pinned tree.
        for (case, expected) in [
            (
                &["1", "or", "2", "and", "3"][..],
                "(or (literal '1') (and (literal '2') (literal '3')))",
            ),
            (
                &["a", "and", "b", "or", "c"][..],
                "(or (and (literal 'a') (literal 'b')) (literal 'c'))",
            ),
            (&["not", "not", "x"][..], "(not (not (literal 'x')))"),
            (
                &["1", "is", "not", "2"][..],
                "(is not (literal '1') (literal '2'))",
            ),
            (
                &["1", "is", "not", "not", "2"][..],
                "(is not (literal '1') (not (literal '2')))",
            ),
        ] {
            let mut parser = IfParser::new(tokens(case));
            let root = parser.parse().expect("parses");
            assert_eq!(repr(&parser, root), expected, "tree for {case:?}");
        }
    }

    #[test]
    fn parse_errors_match_the_pinned_trees_messages() {
        // The full measured table for the base `IfParser`. Strings are
        // Django's, verbatim, including the doubled quotes in `Unused ''x''`.
        let cases: [ErrorCase; 12] = [
            (&[], "Unexpected end of expression in if tag."),
            (&["not"], "Unexpected end of expression in if tag."),
            (&["1", "and"], "Unexpected end of expression in if tag."),
            (&["x", "or"], "Unexpected end of expression in if tag."),
            (&["1", "+"], "Unused ''+'' at end of if expression."),
            (&["(", "1"], "Unused ''1'' at end of if expression."),
            (&["1", ")"], "Unused '')'' at end of if expression."),
            (&["foo", "bar"], "Unused ''bar'' at end of if expression."),
            (
                &["foo", "xor", "bar"],
                "Unused ''xor'' at end of if expression.",
            ),
            (
                &["1", "and", "2", "3"],
                "Unused ''3'' at end of if expression.",
            ),
            (
                &["1", "not", "2"],
                "Not expecting 'not' as infix operator in if tag.",
            ),
            (
                &["or", "1"],
                "Not expecting 'or' in this position in if tag.",
            ),
        ];
        for (case, message) in cases {
            let error = parse_base(case).expect_err("expected a parse failure");
            assert_eq!(error.class, ErrorClass::Value, "class for {case:?}");
            assert_eq!(error.message, message, "message for {case:?}");
        }
    }

    #[test]
    fn a_single_empty_string_token_is_a_literal_not_the_end_token() {
        // A *constructed* `[""]` is one `Literal("")`; only the empty list is
        // the `EndToken` case. Django: `repr(IfParser([""]).parse())` is
        // `(literal '')`.
        let mut parser = IfParser::new(tokens(&[""]));
        let root = parser.parse().expect("parses");
        assert_eq!(repr(&parser, root), "(literal '')");
    }

    #[test]
    fn error_class_differs_between_the_two_entry_points_but_the_message_does_not() {
        // The point of the task. Each case's message interpolates no `Literal`
        // display, so the two classes agree on the message and only the class
        // differs.
        let cases: [&[&str]; 4] = [&[], &["not"], &["1", "and"], &["or", "1"]];
        for case in cases {
            let base = parse_base(case).expect_err("base fails");
            let template = parse_template(case).expect_err("template fails");
            assert_eq!(base.class, ErrorClass::Value, "base class for {case:?}");
            assert_eq!(
                template.class,
                ErrorClass::TemplateSyntax,
                "template class for {case:?}",
            );
            assert_eq!(base.message, template.message, "message for {case:?}");
        }
    }

    #[test]
    fn the_leftover_literal_display_differs_between_the_two_parsers() {
        // The second difference, and why `Literal` stores `display`: the base
        // `Literal.display()` is `repr(value)`, the subclass's is `self.text`.
        let base = parse_base(&["foo", "bar"]).expect_err("base fails");
        assert_eq!(base.message, "Unused ''bar'' at end of if expression.");

        // `FixedCompiler` accepts `bar`, and Django's real `compile_filter`
        // accepts `bar` too (a valid variable), so this row is faithful.
        let template = parse_template(&["foo", "bar"]).expect_err("template fails");
        assert_eq!(template.message, "Unused 'bar' at end of if expression.");
    }

    #[test]
    fn the_template_parser_compiles_every_literal_during_construction() {
        // `TemplateIfParser.__init__` runs `create_var` for every non-operator
        // token, and `create_var` is where `compile_filter` lives — so a bad
        // filter chain fails before `parse`, not during it.
        let error = IfParser::template(&RefusingCompiler, tokens(&["foo"]))
            .err()
            .expect("compile_filter refuses");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax("Invalid filter: 'foo'".to_string()),
        );

        // A successful compile stores the handle, and `display` keeps the raw
        // token rather than its repr.
        let parser = IfParser::template(&FixedCompiler, tokens(&["foo"]))
            .expect("FixedCompiler compiles every token");
        assert_eq!(
            parser.node(parser.tokens[0]).kind,
            IfToken::Literal(Literal {
                value: LiteralValue::Compiled(NodeRef(41)),
                display: "foo".to_string(),
            }),
        );
    }

    #[test]
    fn long_infix_chains_are_iterative() {
        // `expression`'s loop consumes infix operators, so depth does not grow
        // with the chain. Django parses 100 000 `and`s (measured); this uses
        // 10 000, still far past `MAX_NESTING_DEPTH`.
        let mut case: Vec<String> = Vec::new();
        for _ in 0..10_000 {
            case.push("a".to_string());
            case.push("and".to_string());
        }
        case.push("a".to_string());

        let mut parser = IfParser::new(case);
        let root = parser.parse().expect("parses");
        assert_eq!(
            parser.node(root).kind,
            IfToken::Operator(Operator::And),
            "left-associated chain roots at the last `and`",
        );
    }

    #[test]
    fn deep_prefix_nesting_is_refused_instead_of_overflowing_the_stack() {
        // Django has no explicit bound (497 `not`s parse, 498 raises
        // RecursionError). Rust's limit is the native stack, and crossing it is
        // an abort, not an error, so `expression` refuses past
        // `MAX_NESTING_DEPTH` instead.
        let mut small: Vec<String> = vec!["not".to_string(); 10];
        small.push("x".to_string());
        assert!(IfParser::new(small).parse().is_ok());

        // Both sizes are far past the native limit for this recursion, which
        // was measured by disabling the guard: `MAX_NESTING_DEPTH + 10` and
        // 4000 `not`s both abort the process with `fatal runtime error: stack
        // overflow`, while 2000 parses. The 100 000 case is 25x past that
        // abort point and still returns the guard's own error, which is what
        // shows the guard — not the input size — is what fires.
        for depth in [MAX_NESTING_DEPTH + 10, 100_000] {
            let mut deep: Vec<String> = vec!["not".to_string(); depth];
            deep.push("x".to_string());
            let error = IfParser::new(deep).parse().expect_err("refuses");
            assert_eq!(error.class, ErrorClass::Value, "class at depth {depth}");
            assert_eq!(
                error.message,
                format!("If expression is nested more than {MAX_NESTING_DEPTH} deep in if tag."),
                "message at depth {depth}",
            );
        }
    }

    #[test]
    fn a_literal_and_the_end_token_have_binding_power_zero() {
        // `Literal.lbp = 0` and `EndToken.lbp = 0`, which is why the
        // `expression` loop stops on them and `led` never sees either.
        let parser = IfParser::new(tokens(&["foo"]));
        assert_eq!(parser.lbp(parser.tokens[0]), 0);
        assert_eq!(parser.lbp(END), 0);
        assert_eq!(parser.node(END).kind, IfToken::End);
    }
}
