//! The node descriptor: the plain-data tree Rust hands to the Python shell.
//!
//! Rust owns the parse and Python owns the tree that renders (design spec §5.5),
//! so these types are the only thing that crosses between them. They are
//! deliberately plain: no trait objects, no Python, no lifetimes.
//!
//! **The descriptor *is* Rust's `Node`/`NodeList`, not a parallel model of
//! them.** Django's `Node` carries `must_be_first`, `child_nodelists` and a
//! `render`; only `must_be_first` is read during a parse, and `render` is
//! Python's (§5.5). Django's `NodeList` is a `list` subclass whose one piece of
//! parse-time state is `contains_nontext`, which [`NodeListDesc`] carries.
//! Inventing a second Rust type for either would mean a conversion layer with no
//! reader.

use crate::token::{Token, TokenType};

/// The Python class name of a text node, which `extend_nodelist` special-cases.
///
/// Django tests `isinstance(node, TextNode)`. The descriptor has only the name,
/// and no class in Django's template package subclasses `TextNode` (verified
/// against the pinned checkout), so comparing names is exact. Task 7 pins the
/// assumption, so a future Django that subclasses it fails loudly instead of
/// silently treating a text node as non-text.
pub const TEXT_NODE: &str = "TextNode";

/// An opaque handle to a host-owned value.
///
/// `template-core` stores it and hands it back; it never reads it. The first
/// real producer is M3's `FilterExpression`; M2 produces one only from
/// `#[cfg(test)]` stand-ins, because `{% resetcycle %}`'s operand — the other
/// intended producer — is unreachable until `{% cycle %}` exists (M5).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct NodeRef(pub u64);

/// A token, as it appears inside a descriptor.
///
/// The same four fields in the same order as `_native.lex` returns them, so the
/// shell builds a real `Token` from either without a second adapter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TokenDesc {
    pub token_type: TokenType,
    pub contents: String,
    /// Always present: a parse only ever sees lexed tokens.
    pub lineno: usize,
    /// Character indices, or `None` for the non-debug lexer.
    pub position: Option<(usize, usize)>,
}

impl TokenDesc {
    pub fn of(token: &Token) -> Self {
        Self {
            token_type: token.token_type,
            contents: token.contents.clone(),
            lineno: token.lineno,
            position: token.position,
        }
    }
}

/// One constructor argument of a node.
#[derive(Debug, Clone, PartialEq)]
pub enum AttrValue {
    Str(String),
    /// A string that is already safe. Materialises through `mark_safe`.
    ///
    /// `{% verbatim %}` renders its body at *parse* time and keeps a
    /// `SafeString`; a plain `str` here would be escaped when the node finally
    /// renders, which is Review Focus item 8.
    SafeStr(String),
    Bool(bool),
    Int(i64),
    /// A float literal — `Variable.__init__`'s `float(var)` branch.
    ///
    /// `f64` rather than the decimal text. Django's `literal` for `1.5` is a
    /// Python `float`, and `1e400` is `inf`: no text round-trip reproduces that
    /// without a second parse, and the second parse would be Python's.
    Float(f64),
    /// An integer literal that does not fit `i64`. Materialises as `int(text)`.
    ///
    /// Python's `int` is arbitrary-precision, and `{{ 99999999999999999999 }}`
    /// is a valid template that renders its own digits, so saturating to
    /// `i64::MAX` would render a wrong number. `text` is the **canonical**
    /// decimal form produced by `pynum::python_int` — an ASCII `-` if negative,
    /// then ASCII digits, no underscores, no whitespace — so it is a validated
    /// decimal and the materialiser hands it to `int()`. `int()` of that cannot
    /// execute anything; the text never reaches `eval`, and the doc comment on
    /// the FFI arm says so, because a reader cannot check it from here.
    BigInt(String),
    None,
    /// A host-owned value, held by handle. See [`NodeRef`].
    Ref(NodeRef),
    /// A `Variable` — a `FilterExpression` argument, or a `{% with %}` target.
    ///
    /// Nested rather than flattened because `Variable` is itself a parsed thing
    /// with four slots, and a flattened form would have to be reassembled in the
    /// materialiser — which is the re-parsing §5.5 forbids.
    ///
    /// **Boxed, and the box is load-bearing.** `VariableDesc.literal` is itself
    /// an `Option<AttrValue>`, so the two types are mutually recursive and an
    /// unboxed variant is an infinitely sized type. Boxed here rather than on
    /// `literal` because this is the rarer shape: every `Variable` has a
    /// `literal` slot and only a filter argument or a `{% with %}` target is a
    /// `Variable` at all.
    Variable(Box<VariableDesc>),
    /// A list of values — `IfNode.conditions_nodelists`, `ForNode.loopvars`,
    /// `FilterExpression`'s filter arguments.
    ///
    /// A nodelist inside one is [`AttrValue::NodeListRef`], never inlined; see
    /// that variant.
    List(Vec<AttrValue>),
    /// A `token_kwargs` result — `WithNode.extra_context`,
    /// `IncludeNode.extra_context`, `parse_bits`' kwargs.
    ///
    /// A `Vec` of pairs rather than a map type, so the descriptor keeps the order
    /// `token_kwargs` produced and the **materialiser** decides what Python
    /// object it becomes. There is exactly one place that decision is made and
    /// the repeated-key case is why: `token_kwargs` does `kwargs[key] = …`
    /// (`base.py:1196`), so a repeated key keeps its first position and takes the
    /// last value, and only `dict(pairs)` reproduces that.
    Map(Vec<(String, AttrValue)>),
    /// A position in this node's own `NodeDesc.nodelists`.
    ///
    /// `IfNode.conditions_nodelists` is a list of `(condition, nodelist)` pairs,
    /// and the nodelist half must be a **real Python `NodeList`** held inside the
    /// pairs: `IfNode` does not override `child_nodelists` (it inherits
    /// `('nodelist',)` from `Node`) while `nodelist` is a computed property
    /// returning `NodeList(self)`, so `get_nodes_by_type` reaches an `IfNode`'s
    /// children only through the pairs. Inlining a `NodeListDesc` would build a
    /// second, identity-different tree.
    ///
    /// **It crosses the wire as its own type, not as a bare `int`.** An earlier
    /// draft of the plan had `_decode_value` recognise `isinstance(value, int)`
    /// — which cannot work, because [`AttrValue::Int`] is *also* a Python `int`
    /// and no node with both an integer attribute and a nodelist reference could
    /// be materialised. The two would be indistinguishable and the failure would
    /// be a silently wrong nodelist, so the FFI has a `NodeListRef` pyclass in
    /// the same style as `NodeRef` and `SafeStr`.
    NodeListRef(usize),
}

/// A `Variable` as it crosses the wire (`django.template.base.Variable`).
///
/// The pair `(literal, lookups)` is the discriminator, exactly as in Django:
/// `lookups: Some(_)` means a real variable, `lookups: None` means a literal
/// whose value is in `literal`. `Option<AttrValue>` alone could not express
/// that, because `literal` is legitimately `None` for *every* real variable —
/// and, one case further out, `Some` for one that also has `lookups` (see
/// `variable::parse_variable`).
#[derive(Debug, Clone, PartialEq)]
pub struct VariableDesc {
    /// `Variable.var` — the token as it arrived, kept for `__str__`/`__repr__`.
    ///
    /// The **original**, not the form the parse worked on:
    /// `Variable.__init__` reassigns its local `var` when it strips the
    /// `_("…")` marker and never writes the slot again, so
    /// `str(Variable('_("x")'))` is `_("x")` and not `"x"`.
    pub var: String,
    /// `Variable.literal`, already converted: `Int`/`BigInt`/`Float`/`SafeStr`
    /// for the four kinds of literal, and `None` for everything else.
    pub literal: Option<AttrValue>,
    /// `Variable.lookups` — the token split on `.`. `Some` exactly when this is
    /// a real variable; `None` for a literal.
    pub lookups: Option<Vec<String>>,
    /// `Variable.translate` — the token was wrapped in Django's `_("…")`
    /// translation marker. Set **independently** of what the marker wrapped: a
    /// marker around something that unescapes has a literal and no lookups,
    /// while one around anything else keeps its lookups and gains the flag.
    pub translate: bool,
}

/// A compiled nodelist: Django's `NodeList` as far as a parse can observe it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct NodeListDesc {
    pub nodes: Vec<NodeDesc>,
    /// Django's `NodeList.contains_nontext`, a *class* attribute shadowed per
    /// instance by `extend_nodelist`. It is per-nodelist — not per-parser and
    /// not per-node — and `{% extends %}`'s ordering rule depends on it being
    /// reset for every new list while still surviving nesting
    /// (Review Focus item 1).
    pub contains_nontext: bool,
}

/// A compiled node.
#[derive(Debug, Clone, PartialEq)]
pub struct NodeDesc {
    /// The Python class to materialise, e.g. `"TextNode"`, `"AutoEscapeControlNode"`.
    pub class_name: String,
    /// Constructor arguments **by parameter name**, so the materialiser can
    /// build `kwargs` and call `cls(**kwargs)`; see `materialise_node`.
    pub attrs: Vec<(String, AttrValue)>,
    /// Child nodelists, by the attribute name the class's `__init__` takes.
    pub nodelists: Vec<(String, NodeListDesc)>,
    /// Read by `extend_nodelist` during the parse. Not carried on the wire.
    pub must_be_first: bool,
    /// Attached by `extend_nodelist` **after** construction, which is why it is
    /// not one of the `attrs` (Review Focus item 10).
    pub token: Option<TokenDesc>,
}

impl NodeDesc {
    /// A node with no arguments and no child nodelists.
    pub fn new(class_name: impl Into<String>) -> Self {
        Self {
            class_name: class_name.into(),
            attrs: Vec::new(),
            nodelists: Vec::new(),
            must_be_first: false,
            token: None,
        }
    }

    /// A node whose only child nodelist is `name`.
    pub fn with_nodelist(
        class_name: impl Into<String>,
        name: impl Into<String>,
        nodelist: NodeListDesc,
    ) -> Self {
        Self::new(class_name).nodelist(name, nodelist)
    }

    #[must_use]
    pub fn attr(mut self, name: impl Into<String>, value: AttrValue) -> Self {
        self.attrs.push((name.into(), value));
        self
    }

    #[must_use]
    pub fn nodelist(mut self, name: impl Into<String>, nodelist: NodeListDesc) -> Self {
        self.nodelists.push((name.into(), nodelist));
        self
    }

    /// Django's `Node.must_be_first`, which exactly one built-in sets:
    /// `ExtendsNode` (`loader_tags.py:87`). Nothing in M2 sets it; the
    /// `#[cfg(test)]` stand-in in Task 3 does, so the rule is tested before M7
    /// depends on it.
    #[must_use]
    pub fn first(mut self) -> Self {
        self.must_be_first = true;
        self
    }

    /// Django's `TextNode(token.contents)`, whose parameter is named `s`.
    pub fn text(contents: &str) -> Self {
        Self::new(TEXT_NODE).attr("s", AttrValue::Str(contents.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn text_node_uses_djangos_parameter_name() {
        // `TextNode.__init__(self, s)`, and the materialiser constructs by
        // keyword, so the name is part of the contract rather than an internal
        // choice. Renaming it here fails at M4's materialisation test.
        let node = NodeDesc::text("hi");
        assert_eq!(node.class_name, "TextNode");
        assert_eq!(
            node.attrs,
            vec![("s".to_string(), AttrValue::Str("hi".to_string()))],
        );
    }

    #[test]
    fn builders_keep_insertion_order() {
        let node = NodeDesc::new("X")
            .attr("a", AttrValue::Int(1))
            .attr("b", AttrValue::Bool(true));
        assert_eq!(
            node.attrs,
            vec![
                ("a".to_string(), AttrValue::Int(1)),
                ("b".to_string(), AttrValue::Bool(true)),
            ],
        );
    }

    #[test]
    fn a_new_node_is_not_required_to_be_first_and_has_no_token() {
        let node = NodeDesc::new("X");
        assert!(!node.must_be_first);
        assert_eq!(node.token, None);
        assert!(node.nodelists.is_empty());
    }

    #[test]
    fn the_field_and_the_builder_are_independent() {
        // `NodeDesc` has both a `must_be_first` field and a `first()` builder,
        // so a builder that forgot to set the field would be invisible to every
        // other test here.
        assert!(NodeDesc::new("X").first().must_be_first);
    }

    #[test]
    fn contains_nontext_defaults_to_false_per_nodelist() {
        assert!(!NodeListDesc::default().contains_nontext);
        assert!(NodeListDesc::default().nodes.is_empty());
    }

    #[test]
    fn token_desc_round_trips_every_field() {
        let token = Token {
            token_type: TokenType::Block,
            contents: "if x".to_string(),
            lineno: 3,
            position: Some((4, 9)),
        };
        assert_eq!(
            TokenDesc::of(&token),
            TokenDesc {
                token_type: TokenType::Block,
                contents: "if x".to_string(),
                lineno: 3,
                position: Some((4, 9)),
            },
        );
    }

    #[test]
    fn with_nodelist_is_one_named_nodelist_and_nothing_else() {
        let node = NodeDesc::with_nodelist("SpacelessNode", "nodelist", NodeListDesc::default());
        assert_eq!(node.class_name, "SpacelessNode");
        assert_eq!(
            node.nodelists,
            vec![("nodelist".to_string(), NodeListDesc::default())],
        );
        assert!(node.attrs.is_empty());
    }

    #[test]
    fn containers_keep_insertion_order_and_nest() {
        let value = AttrValue::List(vec![
            AttrValue::Int(1),
            AttrValue::List(vec![AttrValue::Str("x".to_string())]),
        ]);
        let AttrValue::List(outer) = &value else {
            unreachable!()
        };
        assert_eq!(outer.len(), 2);
        assert!(matches!(outer[0], AttrValue::Int(1)));

        let mapping = AttrValue::Map(vec![("a".to_string(), AttrValue::Bool(true))]);
        let AttrValue::Map(entries) = &mapping else {
            unreachable!()
        };
        assert_eq!(entries[0].0, "a");
    }

    #[test]
    fn a_nodelist_ref_is_an_index_and_not_a_copy() {
        // The whole point of the variant: the nodelist stays in
        // `NodeDesc.nodelists`, where `materialise_node` already builds
        // children, and the attribute carries a position. A `NodeListDesc`
        // inline would duplicate the subtree and break identity.
        let node = NodeDesc::new("IfNode").attr(
            "conditions_nodelists",
            AttrValue::List(vec![AttrValue::List(vec![
                AttrValue::None,
                AttrValue::NodeListRef(0),
            ])]),
        );
        assert!(
            node.nodelists.is_empty(),
            "the ref must not inline a nodelist"
        );
    }

    #[test]
    fn a_positional_only_nodelist_is_named_with_the_empty_string() {
        // `IfNode.__init__` takes exactly one parameter, `conditions_nodelists`,
        // and holds its child nodelists *inside* that list — `nodelist` is a
        // computed property returning `NodeList(self)` and there is no
        // `nodelist` parameter to pass one to. So the branch nodelists cannot be
        // kwargs, and the empty name is how a descriptor says "positional only".
        // The materialiser reads this; see `test_materialise.py`'s
        // `test_a_positional_only_nodelist_is_reachable_but_not_a_kwarg`.
        let node = NodeDesc::new("IfNode")
            .nodelist("", NodeListDesc::default())
            .nodelist("", NodeListDesc::default());
        assert_eq!(node.nodelists.len(), 2);
        assert!(node.nodelists.iter().all(|(name, _)| name.is_empty()));
    }

    #[test]
    fn a_map_keeps_a_repeated_key_as_two_entries() {
        // `token_kwargs` does `kwargs[key] = …` (`base.py:1196`), so a repeated
        // key keeps its *first position* and takes the *last value*. Rust keeps
        // both entries and in order; the collapsing is the FFI's, one
        // `PyDict::set_item` per entry, which is where Django's own behaviour
        // falls out for free rather than needing a rule. Asserted here because
        // a descriptor that dropped the earlier entry would make the position
        // wrong and no Python-side test could see the difference.
        let mapping = AttrValue::Map(vec![
            ("a".to_string(), AttrValue::Int(1)),
            ("b".to_string(), AttrValue::Int(2)),
            ("a".to_string(), AttrValue::Int(3)),
        ]);
        let AttrValue::Map(entries) = &mapping else {
            unreachable!()
        };
        assert_eq!(entries.len(), 3, "both `a` entries survive Rust");
        assert_eq!(entries[0].0, "a");
        assert_eq!(entries[2].0, "a");
    }
}
