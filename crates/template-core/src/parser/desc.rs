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

/// The reserved `class_name` of a descriptor that names a **host-owned** node.
///
/// A user tag's compile function runs in Python and returns a Python `Node`;
/// `template-core` cannot read it and does not try (§5.2, [`NodeRef`]). So the
/// descriptor carries the slot and nothing else, and the materialiser returns
/// what the host stored there.
///
/// A reserved name rather than a new [`AttrValue`] variant, because everything
/// else about such a node is ordinary: it is a node in a nodelist, it gets
/// `token`/`origin` from `extend_nodelist`, and `describe` compares it as the
/// Python object it is. No Django module at 6.0.9 defines `HostNode`
/// (measured: `grep -rn 'class HostNode' .django/` is empty), and
/// `materialise_node` tests this name before `NODE_CLASSES`, so a future
/// Django that grew one could not capture it.
pub const HOST_NODE: &str = "HostNode";

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
    /// A `FilterExpression` — the `{{ … }}` in a `VariableNode`.
    ///
    /// **Boxed, and the box is load-bearing**, for the reason
    /// [`AttrValue::Variable`]'s is: the cycle is
    /// `AttrValue → FilterExprDesc → FilterVar → AttrValue`, so an unboxed
    /// variant is an infinitely sized type (`E0072`). Boxed on the variant rather
    /// than inside `FilterVar` because a `FilterVar` is the *common* shape — every
    /// `{{ … }}` has a variable part and every filter call has arguments, while
    /// only a `{{ … }}` node is a `FilterExpression` attribute at all.
    FilterExpr(Box<FilterExprDesc>),
    /// A list of values — `IfNode.conditions_nodelists`, `ForNode.loopvars`,
    /// `FilterExpression`'s filter arguments.
    ///
    /// A nodelist inside one is [`AttrValue::NodeListRef`], never inlined; see
    /// that variant.
    List(Vec<AttrValue>),
    /// A **tuple** of values, and the container type is the whole point.
    ///
    /// `{% if %}` is the only producer: `IfNode.conditions_nodelists` is a list
    /// of `(condition, nodelist)` **tuples** (`defaulttags.py:995`), `do_if`
    /// builds them with tuple literals, and `IfNode.render` unpacks them
    /// (`defaulttags.py:323`). A list would render identically and be a
    /// different node — the same argument `IfChangedNode._varlist` is pinned by
    /// — and the parity suite can tell the two apart, because `describe`'s tuple
    /// arm carries a `"tuple"` marker the list arm deliberately does not have.
    ///
    /// It exists rather than a node-specific "branch" type because the pair is
    /// exactly a two-element tuple and nothing else: the condition half is
    /// [`AttrValue::IfCondition`], the nodelist half is
    /// [`AttrValue::NodeListRef`], and naming the *container* is all this
    /// variant adds. A second type would have to be decoded by a second arm
    /// while producing the same object.
    Tuple(Vec<AttrValue>),
    /// A `token_kwargs` result — `WithNode.extra_context`,
    /// `IncludeNode.extra_context`, `parse_bits`' kwargs.
    ///
    /// A `Vec` of pairs rather than a map type, so the descriptor keeps the order
    /// `token_kwargs` produced and the **materialiser** decides what Python
    /// object it becomes. There is exactly one place that decision is made and
    /// the repeated-key case is why: `token_kwargs` does `kwargs[key] = …`
    /// (`base.py:1202`), so a repeated key keeps its first position and takes the
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
    /// An index into the **per-parse reference table**, filled at
    /// materialisation time rather than at parse time.
    ///
    /// [`AttrValue::NodeListRef`] is the same idiom one level down: the wire is
    /// a tree and the thing being expressed is not, so the tree carries an index
    /// that is resolved once the side collection exists. [`AttrValue::Ref`]
    /// indexes the *same* table, but the **host** fills its entry during the
    /// parse — `{% if %}`'s `FilterExpression` is built while the tag compiles
    /// because that is where Django builds it. This one the **materialiser**
    /// fills as it builds, because the value that has to be shared is a
    /// materialised Python object and does not exist during the parse.
    ///
    /// `{% resetcycle %}`'s operand is the only producer, and it is an attribute
    /// *value*: `ResetCycleNode.node` is the cycle's object. The other half of
    /// the idiom — a descriptor whose whole identity *is* a slot — is
    /// [`NodeDesc::slot`].
    ///
    /// Its own type rather than a bare `int`, for `NodeListRef`'s reason: an
    /// integer attribute is a Python `int` too, and a materialiser dispatching
    /// on `isinstance(value, int)` could not tell them apart.
    NodeSlot(usize),
    /// One `{% if %}` condition, as the `smartif` tree `IfParser.parse`
    /// returned.
    ///
    /// **The tree, and not a handle to one, because `template-core` is what
    /// builds it.** `{% if %}`'s condition is the one attribute whose value is a
    /// parse *tree*: `do_if` runs `TemplateIfParser(parser, bits).parse()`
    /// (`defaulttags.py:993`) and `IfNode.render` then calls
    /// `condition.eval(context)` on the result (`defaulttags.py:326`). Python
    /// therefore needs real `smartif` nodes at render time, and the descriptor
    /// is how the shape gets there.
    ///
    /// The operators are named rather than rebuilt from Python's table by index:
    /// `smartif.OPERATORS[key]` is the class and `translate_token` instantiates
    /// it per occurrence (`smartif.py:179-185`), so a key plus two children is
    /// everything the materialiser needs — and an *index* into `OPERATORS` would
    /// be a second, order-dependent way to say the same thing.
    ///
    /// Its leaves are [`AttrValue::Ref`]s, not [`AttrValue::FilterExpr`]s: the
    /// `FilterExpression` behind a `TemplateLiteral` is built by the **host**
    /// during the parse, which is where Django builds it (`create_var` calls
    /// `self.template_parser.compile_filter(value)`, `defaulttags.py:929`), so a
    /// bad filter in a condition raises at parse time carrying Django's message.
    ///
    /// **Boxed**, for [`AttrValue::Variable`]'s reason: `IfConditionDesc` holds
    /// two more `IfConditionDesc`s, so an unboxed variant is an infinitely sized
    /// type (`E0072`).
    IfCondition(Box<IfConditionDesc>),
    /// A list of **tokens**, not of nodes — `BlockTranslateNode.singular` and
    /// `.plural`.
    ///
    /// The one attribute in the milestone whose value is a slice of the *token
    /// stream* rather than a subtree. `BlockTranslateNode` carries no nodelist at
    /// all: the body of a `{% blocktranslate %}` is scanned token by token
    /// (`i18n.py:560-578`) and handed to the node as the `Token` objects
    /// themselves, which `render_token_list` (`i18n.py:134-146`) then walks by
    /// `token.token_type` and `token.contents` to build the gettext message —
    /// TEXT contents verbatim (with `%` doubled) and VAR contents as a `%(name)s`
    /// placeholder. A nodelist would lose both facts: the message needs the raw
    /// *spelling* of each `{{ … }}` (the variable name as written, not a resolved
    /// value) and the text around it, and Django builds it before any context
    /// exists.
    ///
    /// **Its own variant, not [`AttrValue::List`].** The list arm converts
    /// element-wise through the existing arms and none of them produces an
    /// `AttrValue` that is a token, so a `List` of anything would arrive in
    /// Python as something else entirely — see the `Tokens` pyclass in
    /// `template-py`, which exists for [`AttrValue::NodeListRef`]'s reason: the
    /// wire types are told apart by pyclass, and a bare 4-tuple would decode
    /// through the *tuple* arm into a plain Python tuple whose `.token_type` is
    /// an `AttributeError` at render time, after a parse that looked fine.
    ///
    /// It carries [`TokenDesc`], which already exists as `NodeDesc.token`'s type;
    /// the descriptor's positional token field and an attribute-valued token list
    /// are the same four fields, and a second token type would be a second thing
    /// to keep in step.
    Tokens(Vec<TokenDesc>),
}

/// One node of a `{% if %}` condition — `smartif.TokenBase` and its two
/// subclasses.
///
/// `defaulttags.TemplateLiteral` is the only subclass that occurs here, and it
/// is a `Literal` with a `text` (`defaulttags.py:909-918`); the base
/// `smartif.Literal` is unreachable through `{% if %}` because
/// `TemplateIfParser.create_var` overrides `create_var`. So the two arms below
/// are the whole set `do_if` can produce.
#[derive(Debug, Clone, PartialEq)]
pub enum IfConditionDesc {
    /// An `infix`/`prefix` `Operator` instance, with its two operands.
    ///
    /// `second` is `None` for a prefix operator: `nud` sets `self.second = None`
    /// (`smartif.py:83`) where `led` sets both (`smartif.py:55-56`). Emitted
    /// rather than omitted so the materialiser assigns it, which is what Django
    /// does.
    Operator {
        /// One of `smartif.OPERATORS`' thirteen keys — `and`, `not in`, `==`, …
        key: String,
        first: Box<IfConditionDesc>,
        second: Option<Box<IfConditionDesc>>,
    },
    /// A `TemplateLiteral`: the compiled `FilterExpression` and the raw token.
    ///
    /// `text` is `TemplateLiteral.text`, which `display()` returns
    /// (`defaulttags.py:914-915`). The message that reads it — `"Unused '%s' at
    /// end of if expression."` (`smartif.py:200`) — is raised in Rust, so the
    /// text is carried for the materialised object's own sake: it is what
    /// `display()` returns, and a `TemplateLiteral` without it would raise
    /// `AttributeError` the first time anything asked.
    Literal {
        text: String,
        /// The host's `FilterExpression`, by handle.
        expression: NodeRef,
    },
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

/// One of a `FilterExpression`'s operands: its variable part, or one argument.
///
/// Django's own pair — `(lookup, value)` — is built for a filter argument at
/// `base.py:768` and `base.py:770`, and the operand's variable part has the same
/// two shapes without the pair: `base.py:755` resolves a constant eagerly and
/// `base.py:763` holds a `Variable` to resolve per render. `lookup` is read at
/// **render** time, where `if not lookup: arg_vals.append(mark_safe(arg))`
/// (`base.py:806-807`) decides between a constant that is already a value and a
/// variable to resolve against the context, so it is not decorative.
///
/// **The value is always an [`AttrValue::Variable`], in both branches.** That
/// looks like it makes the flag redundant and it does not:
///
/// - `(true, Variable)` is Django's `(True, Variable(var_arg))` — the `Variable`
///   itself, resolved per render;
/// - `(false, Variable)` is Django's `(False, Variable(constant).resolve({}))` —
///   the descriptor carries the `Variable` and the **materialiser** performs the
///   `resolve({})`.
///
/// The second is not a shortcut around an emulation, it is the only faithful
/// form. `Variable.resolve` for a `_("…")`-marked literal returns
/// `gettext_lazy(msgid)` (`base.py:932-939`) — a *lazy proxy*, not a string — and
/// `tests/template_tests/syntax_tests/i18n/test_underscore_syntax.py:16`
/// asserts `Template("{{ _('Yes') }}").render(...) == "Oui"` on exactly that
/// object. A descriptor carrying the resolved value has no way to hold one, so a
/// `{{ _("literal") }}` built from it renders untranslated and fails that test.
/// Rust cannot construct the proxy either, so the resolve has to happen in
/// Python, where Django's own line is.
///
/// A named alias rather than an inline tuple because `clippy::type_complexity`
/// has already bitten this project twice.
pub type FilterVar = (bool, AttrValue);

/// One `|name:arg` of a `FilterExpression`.
#[derive(Debug, Clone, PartialEq)]
pub struct FilterCall {
    /// The filter's name as written — what Django's messages name
    /// (`Invalid filter: 'x'`) and what [`crate::parser::tags::filter`] reads.
    ///
    /// **Not what renders.** The function is Python's and
    /// `FilterExpression.args_check` inspects its signature (`base.py:822-839`),
    /// so no Rust type can carry one; it crosses as [`Self::handle`] instead.
    pub name: String,
    /// At most one, because `filter_re`'s argument group allows zero or one
    /// (`base.py:706-711`). A `Vec` rather than an `Option` because it *is*
    /// Django's list, and a future Django that allowed more would otherwise need
    /// a wire change.
    pub args: Vec<FilterVar>,
    /// The function Django bound to this name **at this token's position**, by
    /// handle into the per-parse reference table.
    ///
    /// `filters.append((filter_func, args))` (`base.py:773`) is what renders, and
    /// the name is not a stable way to reach it: `Parser.add_library` is
    /// `self.filters.update(lib.filters)` (`base.py:669-670`), which **rebinds**
    /// a name already in the table. Our materialisation runs after the whole
    /// parse, so without this a `{% load %}` later in the template would change
    /// what an earlier `{{ x|name }}` renders. `tests/test_filter_rebinding.py`
    /// is the sandwich; `Host::check_filter` carries the measurement.
    ///
    /// `None` means the host ran no check — the permissive default
    /// `_native.parse(source)` without a `check_filter` callable takes, which is
    /// what the descriptor-shape tests and the parity corpus's "no host" rows
    /// want. A host with no registry has no `{% load %}` to rebind a name
    /// either, so the shell's lookup at materialisation is Django's own answer
    /// on that path.
    pub handle: Option<NodeRef>,
}

/// A `FilterExpression` as it crosses the wire (`base.py:722-783`).
#[derive(Debug, Clone, PartialEq)]
pub struct FilterExprDesc {
    /// `FilterExpression.token` — the `{{ … }}` contents as they arrived.
    pub token: String,
    /// `FilterExpression.var`, **before materialisation**.
    ///
    /// Django does not carry `is_var` on the wire and neither does this: it
    /// computes `is_var = isinstance(var_obj, Variable)` (`base.py:783`) from the
    /// *materialised* object, and the shell recomputes it the same way. The flag
    /// here is `lookup`, which happens to agree with `is_var` for the variable
    /// part and is a different question for an argument.
    pub var: FilterVar,
    /// `FilterExpression.filters`, in source order.
    pub filters: Vec<FilterCall>,
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
    /// The reference-table slot this node **is**, or `None`.
    ///
    /// `{% cycle %}` is the only producer and it sets this on *both* forms of
    /// the tag, which is what makes the field worth having:
    ///
    /// - `{% cycle a b as c %}` — the definition. The materialiser builds a real
    ///   `CycleNode` and stores it at this index.
    /// - `{% cycle c %}` — a later mention. Django's `do_cycle` returns the
    ///   identical object (`defaulttags.py:693-696` and
    ///   `defaulttags.py:669-671`), so this descriptor has no constructor
    ///   arguments at all and exists only to name the slot the already-built
    ///   node is.
    ///
    /// The materialiser's rule is therefore one sentence: **if the table already
    /// holds this slot, hand back what is there; otherwise build the node and
    /// store it there.** The index is stable across both occurrences because the
    /// host reserves it before either descriptor is built ([`Host::alloc_slot`]).
    ///
    /// **Not an `attrs` entry.** It is a property of the *node*, not of one of
    /// its constructor arguments — and on the second form there are no
    /// constructor arguments. `CycleNode.__init__(cyclevars, variable_name=None,
    /// silent=False)` takes no slot parameter, so leaving it in `attrs` would
    /// reach `cls(**kwargs)` and raise `TypeError`; a reserved attribute *name*
    /// would work only by convention, and no reader or type could see it.
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
    /// `NodeList.get_nodes_by_type(nodetype)` — `base.py:1093-1098`, whose
    /// per-node half is `Node.get_nodes_by_type` (`base.py:1070-1082`).
    ///
    /// **Pre-order, document order, and that is the contract** rather than an
    /// incidental of the loop: `Node.get_nodes_by_type` appends `self` *before*
    /// extending with its children's results, and `NodeList`'s maps over the
    /// list in order. `ExtendsNode.__init__` folds the result into
    /// `self.blocks = {n.name: n …}` (`loader_tags.py:94`), and that dict's key
    /// order is this list's — `test_parser_parity`'s `describe` compares it with
    /// `.items()`, unsorted — so a walk that visited children first, or that
    /// sorted, materialises a different `blocks` and the parity comparison says
    /// so.
    ///
    /// The fold's *other* observable, "a repeated name keeps the last node", is
    /// **not** reachable from a tree Rust builds: `{% block %}`'s own
    /// duplicate-name check (`parser.loaded_blocks`) refuses the repeat inside
    /// one Rust parse, and the duplicate can only arrive through a
    /// host-compiled block — the fork A5 records, which this walk cannot see
    /// because a host node carries no nodelists.
    ///
    /// **Descending into every nodelist is the same traversal as Django's
    /// `child_nodelists` walk, for every node Rust builds** — and the reason is
    /// worth stating, because the obvious reading of the two walks says
    /// otherwise. Django's `Node.get_nodes_by_type` iterates
    /// `self.child_nodelists`, a per-class tuple of *attribute names*
    /// (`base.py:1035`), so it descends only into the attributes that tuple
    /// lists; this function descends into all of them, whatever they are
    /// called. The two agree because a descriptor's nodelist names are exactly
    /// the constructor parameters the materialiser passes as kwargs, and a node
    /// class that takes a nodelist as a kwarg names it in `child_nodelists` —
    /// `BlockNode`, `ExtendsNode` and `IncludeNode` are `("nodelist",)`,
    /// `ForNode` is `("nodelist_loop", "nodelist_empty")`, `IfChangedNode` is
    /// `("nodelist_true", "nodelist_false")`.
    ///
    /// `IfNode` is the case that breaks that reading and still agrees, so it is
    /// written down rather than left to the pattern: it overrides neither
    /// `child_nodelists` — inheriting `("nodelist",)` — nor `nodelist`, which is
    /// a **property** returning `NodeList(self)` over an `__iter__` that yields
    /// every branch's nodes (`defaulttags.py:314-320`). Its branch lists live in
    /// `conditions_nodelists`, which is *not* in `child_nodelists`, and Django
    /// reaches them through the property. Rust emits those branches as
    /// **empty-named** nodelists — the materialiser's marker for "positional
    /// only", see below — and this function descends into a nodelist by
    /// position and not by name, so it reaches the same nodes. A walk that
    /// skipped empty-named nodelists would be the one divergence here, which is
    /// what Task 3's `get_nodes_by_type_reaches_a_node_inside_an_if_branch`
    /// exists to catch.
    ///
    /// **And `IfNode` is the only such class, which is measured rather than
    /// generalised from — over a population larger than the first sweep's.**
    /// Every `Node` subclass reachable from the pinned tree's template modules
    /// was classified by where each `child_nodelists` name resolves
    /// (`.review/m7-measurements/m7_measure_child_nodelists.py`): **exactly one
    /// names a property** — `IfNode` — and the rest name either an instance
    /// attribute set in `__init__` or an attribute the class never sets at all.
    ///
    /// **The count is 34 or 47 depending on what is imported, and both are
    /// right answers to different questions.** `Node.__subclasses__` is
    /// transitive but not global, so the population is whatever the sweep's
    /// imports pulled in: `base` + `defaulttags` + `loader_tags` — which
    /// *includes* `library`, via `defaulttags` — is **34**; adding the tag
    /// libraries (`cache`, `i18n`, `static`, `tz`, `l10n`) is **47**, and the
    /// extra ones are the i18n and cache nodes (`BlockTranslateNode`,
    /// `TranslateNode`, `CacheNode`, `StaticNode`, …). The verdict survives the
    /// widening: `IfNode` is still the only property. The script prints both
    /// numbers, because "all 34 subclasses in the pinned tree" is a *bound
    /// stated as a total*, and a reader who imports one more module finds a
    /// number the sentence cannot explain.
    ///
    /// The class-attribute/absent split is the other half of the equivalence and
    /// is worth naming, because it is where the two walks *look* different
    /// without differing: Django reads `getattr(self, attr, None)` and then
    /// tests **truthiness** (`base.py:1079-1080`), so a class whose
    /// `child_nodelists` names an attribute it never assigns — `CycleNode`,
    /// `DebugNode`, `FirstOfNode`, `IncludeNode`, `LoremNode`, `NowNode`,
    /// `PartialNode`, `PrefixNode`, `RegroupNode`, `ResetCycleNode`,
    /// `TagHelperNode`, `TemplateTagNode`, `VerbatimNode`, `WidthRatioNode` —
    /// contributes nothing to Django's walk and has no such nodelist in Rust for
    /// this one to descend into. An absent nodelist and an empty one are the
    /// same to *this* function's result for the same reason: both contribute no
    /// nodes.
    ///
    /// **There is a third shape, and the two groups above cannot hold it: a
    /// class that *stores* a nodelist it does not declare.** `SimpleBlockNode`
    /// (`library.py:334`) sets `self.nodelist = nodelist` in `__init__` (`library.py:337`)
    /// and inherits `child_nodelists = ()` from `SimpleNode` (`library.py:317`), so
    /// **Django does not descend into a `simple_block_tag`'s body** — the
    /// inverse of `IfNode`, which declares a name it synthesises. Rust does not
    /// descend either, and the measurement says why, because "both decline" is
    /// two answers that would be indistinguishable from the outside: the
    /// descriptor for `{% wrap %}x{% endwrap %}` is
    /// `('HostNode', (('node', NodeRef 0),), (), …)` — the nodelist tuple is
    /// **empty**, and the ref is a real `SimpleBlockNode` holding its body.
    /// So the two sides agree *by accident*: Django declines on the class's
    /// declaration, Rust declines because a host node carries no nodelists at
    /// all. Neither reason is the other's, and either side can move — if a host
    /// node class declared `('nodelist',)` Django would start descending while
    /// this still would not, and if host bodies were ever materialised into
    /// descriptors this would start descending while Django still would not.
    /// The agreement is not observable today, because this function's only
    /// production caller is `{% extends %}`'s duplicate scan (`extends.rs:71`)
    /// and `ExtendsNode.blocks` is Django's own walk over *materialised* nodes.
    /// A future caller inherits the accident rather than the equivalence.
    ///
    /// **One recorded divergence, measured zero in the pinned suite.** A
    /// descriptor whose `class_name` is [`HOST_NODE`] carries no nodelists at
    /// all (see `NodeDesc::host`), so the traversal is a no-op on it and the
    /// only way the two walks can differ is by *matching*: a host tag that
    /// returned a real `ExtendsNode` or `BlockNode` would match Django's
    /// `isinstance` and cannot match this, which compares class **names**.
    /// Measured on the pin, so that this is a bound and not a hope: no
    /// `class <name>(ExtendsNode|BlockNode|IncludeNode)` exists anywhere under
    /// `tests/template_tests`, and `{% extends %}` nested inside a host-owned
    /// block-level tag — the shape that would put one there — occurs 0 times
    /// over 1046 string literals and 62 fixture templates
    /// (`.review/m7-measurements/m7_measure_nest2.py`). The *neighbouring* case
    /// is not zero: a nested `{% block %}` occurs once, in
    /// `test_blocktranslate.py::test_with_block`; see A5.
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
    fn a_host_node_carries_the_slot_under_the_name_the_materialiser_reads() {
        // The Python side looks the attribute up **by name** (`"node"`), so the
        // name is part of the contract exactly as `TextNode`'s `"s"` is, and the
        // value must be a `Ref` because it is type-checked as one. Renaming or
        // retyping either end fails `test_a_host_node_is_the_object_the_table_holds`.
        // `{% load %}` (Task 3) is the first production caller of this builder.
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
        // `token_kwargs` does `kwargs[key] = …` (`base.py:1202`), so a repeated
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

    #[test]
    fn a_filter_expressions_operands_are_variables_and_a_lookup_flag() {
        // The shape the materialiser reads: every operand is
        // `AttrValue::Variable`, in both the `lookup` and the constant branch,
        // and only the flag differs. Pinned because a variant that flattened the
        // constant to its *value* would look tidier and would lose
        // `{{ _("literal") }}` — the value is a `gettext_lazy` proxy that Rust
        // cannot build. See `FilterVar`'s doc.
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
