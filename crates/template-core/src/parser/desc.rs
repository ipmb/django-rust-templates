//! The node descriptor: the plain-data tree Rust hands to the Python shell.
//!
//! Rust owns the parse and Python owns the rendering tree (§5.5), so these types
//! are the only thing crossing between them — deliberately plain: no trait
//! objects, no Python, no lifetimes. **The descriptor *is* Rust's
//! `Node`/`NodeList`, not a parallel model**: `must_be_first` is the only field
//! read during a parse, and `contains_nontext` the only `NodeList` state carried.

use crate::token::{Token, TokenType};

/// The Python class name of a text node, which `extend_nodelist` special-cases.
///
/// Django tests `isinstance(node, TextNode)`; comparing names is exact because no
/// class in Django's template package subclasses it (verified against the pinned
/// checkout). Task 7 pins the assumption.
pub const TEXT_NODE: &str = "TextNode";

/// The reserved `class_name` of a descriptor that names a **host-owned** node.
///
/// `template-core` cannot read a Python `Node` and does not try (§5.2, [`NodeRef`]);
/// the descriptor carries the slot, and the materialiser returns what the host
/// stored there. A reserved name rather than a new [`AttrValue`] variant because
/// such a node is otherwise ordinary (`token`/`origin` from `extend_nodelist`,
/// `describe` comparing it as the Python object it is). No Django 6.0.9 module
/// defines `HostNode` (measured), and `materialise_node` tests this name before
/// `NODE_CLASSES`.
pub const HOST_NODE: &str = "HostNode";

/// An opaque handle to a host-owned value: `template-core` stores it and hands it
/// back, never reading it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct NodeRef(pub u64);

/// A token, as it appears inside a descriptor: the same four fields in the same
/// order as `_native.lex` returns them, so the shell builds a real `Token` from
/// either without a second adapter.
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
    /// A string that is already safe; materialises through `mark_safe`.
    ///
    /// `{% verbatim %}` renders its body at *parse* time and keeps a
    /// `SafeString`; a plain `str` here would be escaped when the node finally
    /// renders (Review Focus item 8).
    SafeStr(String),
    Bool(bool),
    Int(i64),
    /// A float literal — `Variable.__init__`'s `float(var)` branch.
    ///
    /// `f64` rather than the decimal text: `1e400` is `inf`, and no text
    /// round-trip reproduces that without a second (Python) parse.
    Float(f64),
    /// An integer literal that does not fit `i64`; materialises as `int(text)`.
    ///
    /// Python's `int` is arbitrary-precision, so saturating to `i64::MAX` would
    /// render a wrong number. `text` is the canonical decimal from
    /// `pynum::python_int` (ASCII `-` then digits, no underscores, no whitespace),
    /// so `int()` of it cannot execute anything.
    BigInt(String),
    None,
    /// A host-owned value, held by handle. See [`NodeRef`].
    Ref(NodeRef),
    /// A `Variable` — a `FilterExpression` argument, or a `{% with %}` target.
    ///
    /// Nested rather than flattened: a flattened form would have to be reassembled
    /// in the materialiser, the re-parsing §5.5 forbids.
    ///
    /// **Boxed, and the box is load-bearing**: `VariableDesc.literal` is itself an
    /// `Option<AttrValue>`, so the types are mutually recursive (`E0072`). Boxed
    /// here rather than on `literal` because this is the rarer shape — every
    /// `Variable` has a `literal` slot.
    Variable(Box<VariableDesc>),
    /// A `FilterExpression` — the `{{ … }}` in a `VariableNode`.
    ///
    /// **Boxed, and the box is load-bearing** (`E0072`), for
    /// [`AttrValue::Variable`]'s reason: the cycle is
    /// `AttrValue → FilterExprDesc → FilterVar → AttrValue`. Boxed on the variant
    /// rather than inside `FilterVar`, which is the *common* shape.
    FilterExpr(Box<FilterExprDesc>),
    /// A list of values — `IfNode.conditions_nodelists`, `ForNode.loopvars`,
    /// `CycleNode.cyclevars`, `IfChangedNode._varlist`, `FirstOfNode.variables`,
    /// `CacheNode.vary_on`, `URLNode.args`.
    ///
    /// Not a `FilterExpression`'s filter arguments, which are a `Vec<FilterVar>`
    /// inside [`FilterExprDesc`], never a node attribute; a nodelist inside one is
    /// [`AttrValue::NodeListRef`], never inlined.
    List(Vec<AttrValue>),
    /// A **tuple** of values, and the container type is the whole point.
    ///
    /// `{% if %}` is the only producer: `IfNode.conditions_nodelists` is a list of
    /// `(condition, nodelist)` **tuples** (`defaulttags.py:995`), and `IfNode.render`
    /// unpacks them as such (`defaulttags.py:323`). A list would render identically
    /// but be a different node, and the parity suite can tell the two apart because
    /// `describe`'s tuple arm carries a `"tuple"` marker the list arm does not.
    Tuple(Vec<AttrValue>),
    /// A `token_kwargs` result — `WithNode.extra_context`,
    /// `IncludeNode.extra_context`, `parse_bits`' kwargs.
    ///
    /// A `Vec` of pairs rather than a map type, so the descriptor keeps the order
    /// `token_kwargs` produced and the **materialiser** decides what Python object
    /// it becomes: `token_kwargs` does `kwargs[key] = …` (`base.py:1202`), so a
    /// repeated key keeps its first position and takes the last value, and only
    /// `dict(pairs)` reproduces that.
    Map(Vec<(String, AttrValue)>),
    /// A position in this node's own `NodeDesc.nodelists`.
    ///
    /// The nodelist half of an `{% if %}` pair must be a **real Python `NodeList`**
    /// held inside the pairs: `IfNode` does not override `child_nodelists` (it
    /// inherits `('nodelist',)` from `Node`) while `nodelist` is a computed property
    /// returning `NodeList(self)`, so `get_nodes_by_type` reaches its children only
    /// through the pairs. Inlining a `NodeListDesc` would build a second,
    /// identity-different tree.
    ///
    /// **Its own wire type, not a bare `int`**: [`AttrValue::Int`] is *also* a
    /// Python `int`, so the two would be indistinguishable and the failure a
    /// silently wrong nodelist.
    NodeListRef(usize),
    /// An index into the **per-parse reference table**, filled at materialisation
    /// time rather than at parse time: the value to be shared is a materialised
    /// Python object and does not exist during the parse. [`AttrValue::Ref`]
    /// indexes the *same* table, but the **host** fills its entry during the parse.
    ///
    /// `{% resetcycle %}`'s operand is the only producer (an attribute *value*);
    /// the other half of the idiom — a descriptor whose whole identity *is* a slot
    /// — is [`NodeDesc::slot`]. Its own type rather than a bare `int`, for
    /// `NodeListRef`'s reason.
    NodeSlot(usize),
    /// One `{% if %}` condition, as the `smartif` tree `IfParser.parse` returned.
    ///
    /// **The tree, not a handle**: `do_if` runs `TemplateIfParser(parser, bits).parse()`
    /// (`defaulttags.py:993`) and `IfNode.render` calls `condition.eval(context)` on
    /// the result (`defaulttags.py:326`), so Python needs real `smartif` nodes at
    /// render time. Operators are named rather than indexed into `OPERATORS`
    /// (`smartif.py:179-185`), so a key plus two children is everything the
    /// materialiser needs.
    ///
    /// Its leaves are [`AttrValue::Ref`]s, not [`AttrValue::FilterExpr`]s: the host
    /// builds the `FilterExpression` during the parse, which is where Django builds
    /// it (`defaulttags.py:929`), so a bad filter raises at parse time carrying
    /// Django's message. **Boxed** (`E0072`), for [`AttrValue::Variable`]'s reason.
    IfCondition(Box<IfConditionDesc>),
    /// A list of **tokens**, not of nodes — `BlockTranslateNode.singular` and
    /// `.plural`.
    ///
    /// The body of a `{% blocktranslate %}` is scanned token by token
    /// (`i18n.py:560-578`) and handed to the node as `Token` objects, which
    /// `render_token_list` (`i18n.py:134-146`) walks to build the gettext message
    /// — TEXT verbatim (with `%` doubled), VAR as a `%(name)s` placeholder. A
    /// nodelist would lose both the raw *spelling* of each `{{ … }}` and the text
    /// around it, and Django builds the message before any context exists.
    ///
    /// **Its own variant, not [`AttrValue::List`]**: no list element arm produces a
    /// token, and the `Tokens` pyclass in `template-py` is what tells a wire token
    /// list from a plain tuple (a bare 4-tuple would decode as a tuple whose
    /// `.token_type` is an `AttributeError` at render time). It carries
    /// [`TokenDesc`], the same four fields as `NodeDesc.token`.
    Tokens(Vec<TokenDesc>),
}

/// One node of a `{% if %}` condition — `smartif.TokenBase` and its two
/// subclasses.
///
/// `defaulttags.TemplateLiteral` is the only subclass that occurs here, a
/// `Literal` with a `text` (`defaulttags.py:909-918`); the base `smartif.Literal`
/// is unreachable through `{% if %}` because `TemplateIfParser.create_var`
/// overrides `create_var`. So the two arms below are the whole set `do_if`
/// produces.
#[derive(Debug, Clone, PartialEq)]
pub enum IfConditionDesc {
    /// An `infix`/`prefix` `Operator` instance, with its two operands.
    ///
    /// `second` is `None` for a prefix operator (`nud` sets `self.second = None`
    /// at `smartif.py:83`; `led` sets both at `smartif.py:55-56`), emitted rather
    /// than omitted so the materialiser assigns it.
    Operator {
        /// One of `smartif.OPERATORS`' thirteen keys — `and`, `not in`, `==`, …
        key: String,
        first: Box<IfConditionDesc>,
        second: Option<Box<IfConditionDesc>>,
    },
    /// A `TemplateLiteral`: the compiled `FilterExpression` and the raw token.
    ///
    /// `text` is what `display()` returns (`defaulttags.py:914-915`); carried for
    /// the materialised object's own sake, since a `TemplateLiteral` without it
    /// would raise `AttributeError` the first time anything asked.
    Literal {
        text: String,
        /// The host's `FilterExpression`, by handle.
        expression: NodeRef,
    },
}

/// A `Variable` as it crosses the wire (`django.template.base.Variable`).
///
/// The pair `(literal, lookups)` is the discriminator, exactly as in Django:
/// `lookups: Some(_)` means a real variable, `lookups: None` a literal whose value
/// is in `literal`. `Option<AttrValue>` alone could not express that, since
/// `literal` is legitimately `None` for *every* real variable (see
/// `variable::parse_variable`).
#[derive(Debug, Clone, PartialEq)]
pub struct VariableDesc {
    /// `Variable.var` — the token as it arrived, kept for `__str__`/`__repr__`.
    ///
    /// The **original**, not the form the parse worked on: stripping the `_("…")`
    /// marker writes only a local, so `str(Variable('_("x")'))` is `_("x")`.
    pub var: String,
    /// `Variable.literal`, already converted: `Int`/`BigInt`/`Float`/`SafeStr`
    /// for the four kinds of literal, and `None` for everything else.
    pub literal: Option<AttrValue>,
    /// `Variable.lookups` — the token split on `.`. `Some` exactly when this is
    /// a real variable; `None` for a literal.
    pub lookups: Option<Vec<String>>,
    /// `Variable.translate` — the token was wrapped in the `_("…")` marker. Set
    /// **independently** of what it wrapped: a marker around something that
    /// unescapes has a literal and no lookups, otherwise lookups plus this flag.
    pub translate: bool,
}

/// One of a `FilterExpression`'s operands: its variable part, or one argument.
///
/// `lookup` is Django's `(lookup, value)` flag, read at **render** time, where
/// `if not lookup: arg_vals.append(mark_safe(arg))` (`base.py:806-807`) decides
/// between a constant that is already a value and a variable to resolve.
///
/// **The value is always an [`AttrValue::Variable`], in both branches**, and the
/// flag is still needed: `(false, Variable)` is Django's
/// `(False, Variable(constant).resolve({}))`, and the materialiser must perform
/// that `resolve({})` because a `_("…")`-marked literal resolves to a
/// `gettext_lazy` proxy (`base.py:932-939`) that Rust cannot build — a descriptor
/// carrying the resolved value would render `{{ _("literal") }}` untranslated.
///
/// A named alias rather than an inline tuple for `clippy::type_complexity`.
pub type FilterVar = (bool, AttrValue);

/// One `|name:arg` of a `FilterExpression`.
#[derive(Debug, Clone, PartialEq)]
pub struct FilterCall {
    /// The filter's name as written — what Django's messages name
    /// (`Invalid filter: 'x'`) and what [`crate::parser::tags::filter`] reads.
    ///
    /// **Not what renders**: the function is Python's and
    /// `FilterExpression.args_check` inspects its signature (`base.py:822-839`),
    /// so no Rust type can carry one; it crosses as [`Self::handle`] instead.
    pub name: String,
    /// At most one (`filter_re`'s argument group allows zero or one,
    /// `base.py:706-711`); a `Vec` rather than an `Option` because it *is*
    /// Django's list, and a future Django allowing more would otherwise need a
    /// wire change.
    pub args: Vec<FilterVar>,
    /// The function Django bound to this name **at this token's position**, by
    /// handle into the per-parse reference table.
    ///
    /// `filters.append((filter_func, args))` (`base.py:773`) is what renders, and
    /// the name is not a stable way to reach it: `add_library` is
    /// `self.filters.update(lib.filters)` (`base.py:669-670`), which **rebinds** a
    /// name. Our materialisation runs after the whole parse, so without this a
    /// `{% load %}` later in the template would change what an earlier
    /// `{{ x|name }}` renders (`tests/test_filter_rebinding.py` is the sandwich).
    ///
    /// `None` means the host ran no check — the permissive default
    /// `_native.parse(source)` without a `check_filter` callable takes.
    pub handle: Option<NodeRef>,
}

/// A `FilterExpression` as it crosses the wire (`base.py:722-783`).
#[derive(Debug, Clone, PartialEq)]
pub struct FilterExprDesc {
    /// `FilterExpression.token` — the `{{ … }}` contents as they arrived.
    pub token: String,
    /// `FilterExpression.var`, **before materialisation**.
    ///
    /// `is_var` is not carried: Django computes `isinstance(var_obj, Variable)`
    /// (`base.py:783`) from the *materialised* object, and the shell recomputes it
    /// the same way. The flag here is `lookup`, a different question for an
    /// argument.
    pub var: FilterVar,
    /// `FilterExpression.filters`, in source order.
    pub filters: Vec<FilterCall>,
}

/// A compiled nodelist: Django's `NodeList` as far as a parse can observe it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct NodeListDesc {
    pub nodes: Vec<NodeDesc>,
    /// Django's `NodeList.contains_nontext`, a *class* attribute shadowed per
    /// instance by `extend_nodelist` — per-nodelist, not per-parser or per-node.
    /// `{% extends %}`'s ordering rule needs it reset for every new list while
    /// surviving nesting (Review Focus item 1).
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
    /// The reference-table slot this node **is**, or `None`.
    ///
    /// `{% cycle %}` is the only producer and sets it on *both* forms: the
    /// definition builds a real `CycleNode` and stores it at this index, and a
    /// later `{% cycle c %}` returns the identical object
    /// (`defaulttags.py:693-696`), so it has no constructor arguments at all and
    /// exists only to name the slot. The materialiser's rule: **if the table
    /// already holds this slot, hand back what is there; otherwise build the node
    /// and store it there.** The index is stable because the host reserves it
    /// before either descriptor is built ([`Host::alloc_slot`]).
    ///
    /// **Not an `attrs` entry**: `CycleNode.__init__` takes no slot parameter, so
    /// it would reach `cls(**kwargs)` and raise `TypeError`.
    pub slot: Option<usize>,
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
            slot: None,
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
    /// `ExtendsNode` (`loader_tags.py:87`).
    #[must_use]
    pub fn first(mut self) -> Self {
        self.must_be_first = true;
        self
    }

    /// Django's `TextNode(token.contents)`, whose parameter is named `s`.
    pub fn text(contents: &str) -> Self {
        Self::new(TEXT_NODE).attr("s", AttrValue::Str(contents.to_string()))
    }

    /// The reference-table slot this node is; see [`NodeDesc::slot`].
    #[must_use]
    pub fn slot(mut self, index: usize) -> Self {
        self.slot = Some(index);
        self
    }

    /// A descriptor for a node the **host** built. `template-core` stores the
    /// handle and never reads it; see [`HOST_NODE`].
    pub fn host(node: NodeRef) -> Self {
        Self::new(HOST_NODE).attr("node", AttrValue::Ref(node))
    }
}

impl NodeListDesc {
    /// `NodeList.get_nodes_by_type(nodetype)` — `base.py:1093-1098`, per-node half
    /// `Node.get_nodes_by_type` (`base.py:1070-1082`).
    ///
    /// **Pre-order, document order, and that is the contract**: `ExtendsNode` folds
    /// the result into `self.blocks = {n.name: n …}` (`loader_tags.py:94`), and the
    /// parity comparison reads that dict unsorted — so a sorted or child-first walk
    /// materialises different blocks.
    ///
    /// **Descending into every nodelist equals Django's `child_nodelists` walk, for
    /// every node Rust builds.** Django iterates a per-class tuple of attribute
    /// *names* (`base.py:1035`) and tests truthiness (`base.py:1079-1080`); this
    /// descends into all nodelists by position. They agree because a descriptor's
    /// nodelist names are exactly the materialiser's kwargs. `IfNode` breaks that
    /// reading and still agrees: it declares `("nodelist",)` but synthesises the
    /// attribute as a **property** over `conditions_nodelists`
    /// (`defaulttags.py:314-320`), and Rust emits its branches as **empty-named**
    /// nodelists, which this walk reaches by position (Task 3's
    /// `get_nodes_by_type_reaches_a_node_inside_an_if_branch`). Measured: `IfNode` is
    /// the only class whose `child_nodelists` names a property.
    ///
    /// **One divergence, measured zero in the pinned suite**: a [`HOST_NODE`]
    /// descriptor carries no nodelists, so the walks can differ only by *matching* —
    /// a host tag returning a real `ExtendsNode`/`BlockNode` would match Django's
    /// `isinstance` and not this name comparison (A5).
    pub fn get_nodes_by_type(&self, class_name: &str) -> Vec<&NodeDesc> {
        let mut found = Vec::new();
        for node in &self.nodes {
            if node.class_name == class_name {
                found.push(node);
            }
            for (_name, child) in &node.nodelists {
                found.extend(child.get_nodes_by_type(class_name));
            }
        }
        found
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn text_node_uses_djangos_parameter_name() {
        // `TextNode.__init__(self, s)`, and the materialiser constructs by keyword,
        // so the name is contract; renaming it fails at materialisation.
        let node = NodeDesc::text("hi");
        assert_eq!(node.class_name, "TextNode");
        assert_eq!(
            node.attrs,
            vec![("s".to_string(), AttrValue::Str("hi".to_string()))],
        );
    }

    #[test]
    fn a_host_node_carries_the_slot_under_the_name_the_materialiser_reads() {
        // The Python side looks the attribute up **by name** (`"node"`) and
        // type-checks it as a `Ref`; renaming or retyping either end fails
        // `test_a_host_node_is_the_object_the_table_holds`.
        let node = NodeDesc::host(NodeRef(3));
        assert_eq!(node.class_name, HOST_NODE);
        assert_eq!(
            node.attrs,
            vec![("node".to_string(), AttrValue::Ref(NodeRef(3)))],
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
        // A `first()` builder that forgot to set the `must_be_first` field would
        // be invisible to every other test here.
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
        // The nodelist stays in `NodeDesc.nodelists` and the attribute carries a
        // position; an inline `NodeListDesc` would duplicate the subtree and break
        // identity.
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
        // `IfNode.__init__` takes one parameter, `conditions_nodelists`, holding
        // its branch nodelists inside that list, so they cannot be kwargs. The
        // empty name is how a descriptor says "positional only"; the materialiser
        // reads it (`test_materialise.py`).
        let node = NodeDesc::new("IfNode")
            .nodelist("", NodeListDesc::default())
            .nodelist("", NodeListDesc::default());
        assert_eq!(node.nodelists.len(), 2);
        assert!(node.nodelists.iter().all(|(name, _)| name.is_empty()));
    }

    #[test]
    fn a_map_keeps_a_repeated_key_as_two_entries() {
        // A repeated key keeps its *first position* and takes the *last value*
        // (`base.py:1202`); Rust keeps both entries in order and the FFI's one
        // `PyDict::set_item` per entry collapses them. Asserted here because a
        // dropped earlier entry would make the position wrong, invisible to Python.
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

    #[test]
    fn a_filter_expressions_operands_are_variables_and_a_lookup_flag() {
        // Every operand is `AttrValue::Variable` in both branches, only the flag
        // differing: flattening the constant to its value would lose
        // `{{ _("literal") }}`, a `gettext_lazy` proxy Rust cannot build (see `FilterVar`).
        let variable = |name: &str| {
            AttrValue::Variable(Box::new(VariableDesc {
                var: name.to_string(),
                literal: None,
                lookups: Some(vec![name.to_string()]),
                translate: false,
            }))
        };
        let expr = AttrValue::FilterExpr(Box::new(FilterExprDesc {
            token: "x|default:y".to_string(),
            var: (true, variable("x")),
            filters: vec![FilterCall {
                name: "default".to_string(),
                args: vec![(true, variable("y"))],
                handle: None,
            }],
        }));

        let AttrValue::FilterExpr(expr) = &expr else {
            unreachable!()
        };
        assert!(expr.var.0, "a bare name is a lookup");
        assert!(matches!(expr.var.1, AttrValue::Variable(_)));
        assert_eq!(expr.filters[0].name, "default");
        assert_eq!(expr.filters[0].args.len(), 1, "at most one, and here one");
    }
}
