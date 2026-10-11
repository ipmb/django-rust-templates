//! The native materialiser: build the compatibility tree by walking the IR.
//!
//! `parser.materialise` does this in Python by walking a *wire* of nested tuples and
//! pyclasses; measured on `real-world/large` (9279 chars, 621 nodes) that walk is 1153 us
//! of a 1834 us compile — 63% — while the plan built from the same tree is 26 us. The
//! wire exists only to be decoded.
//!
//! **Three properties of `materialise_node` are contracts**, each the natural thing to
//! reorder when optimising and each silent when broken:
//!
//! 1. The registry is read **before** the children are built
//!    (`python/django_rust_templates/engine/parser.py:347` against `…:372`). A factory
//!    that rebinds `NODE_CLASSES["IfNode"]` while a child is built is observed by that
//!    child's siblings and *not* by the parent, and an unknown class name raises before
//!    any child work.
//! 2. Children are materialised **before** the parent is constructed, and the attributes
//!    are decoded **after** the children exist. `ExtendsNode.__init__` reads the nodelist
//!    it is handed, and an `AttrValue::NodeListRef` names a position in a list that must
//!    already be populated.
//! 3. `token` and `origin` are attached **after** construction. `__init__` runs first and
//!    some of them read the subtree.
//!
//! **The fallback is the design, not scaffolding**: every shape this module does not yet
//! build is handed to the Python decoder for that shape alone. The host-node arm keeps one
//! permanently, because its descriptor validation and error messages are asserted by tests
//! that should keep reading the strings they were written against.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};
use template_core::parser::desc::{AttrValue, NodeDesc, NodeListDesc};

/// Build a `NodeList` from a descriptor, children first.
pub fn materialise_native(
    py: Python<'_>,
    tree: &Bound<'_, PyAny>,
    desc: &NodeListDesc,
    origin: &Bound<'_, PyAny>,
    resolve_ref: &Bound<'_, PyAny>,
    register_ref: &Bound<'_, PyAny>,
    parser: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let nodelist = tree.getattr("NodeList")?.call0()?;
    // `contains_nontext` is a *class* attribute on `NodeList`, so leaving it alone is how
    // it reads False; only the True transition is written, so a second, text-only nodelist
    // reads False again.
    if desc.contains_nontext {
        nodelist.setattr("contains_nontext", true)?;
    }
    for node in &desc.nodes {
        // **`append` is bound before the node is built, and that is Python's evaluation
        // order rather than a micro-optimisation.** `materialise` reads
        // `nodelist.append(materialise_node(...))`, so `LOAD_METHOD` resolves `append`
        // first: a factory that rebinds `NodeList.append` while it builds a node is **not**
        // seen by that node's own append and *is* seen by every later one. Measured on a
        // one-node template whose `TextNode` factory rebinds it, Python calls the
        // replacement 0 times, the after-the-build spelling once.
        let append = nodelist.getattr("append")?;
        let built = materialise_one(py, tree, node, origin, resolve_ref, register_ref, parser)?;
        append.call1((built,))?;
    }
    Ok(nodelist.unbind())
}

fn materialise_one(
    py: Python<'_>,
    tree: &Bound<'_, PyAny>,
    desc: &NodeDesc,
    origin: &Bound<'_, PyAny>,
    resolve_ref: &Bound<'_, PyAny>,
    register_ref: &Bound<'_, PyAny>,
    parser: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    // Checked before the registry so that the reserved name cannot be captured
    // by an entry someone adds later, and so the error for a genuinely unknown
    // class name stays a `LookupError` — the same order `materialise_node` uses.
    if desc.class_name == template_core::parser::desc::HOST_NODE {
        return fallback_node(py, tree, desc, origin, resolve_ref, register_ref, parser);
    }

    // **Before the children**: `materialise_node` reads the registry at
    // `python/django_rust_templates/engine/parser.py:347` and builds children at `…:372`,
    // so a factory that rebinds the registry while a child is built is seen by the child's
    // siblings and not by this parent. Reading it after the children would build a
    // different tree with the same class names.
    let registry = tree.getattr("NODE_CLASSES")?;
    // **Subscription on the object, never `PyDict::get_item`.** The two agree while
    // `NODE_CLASSES` is a plain dict and stop agreeing as soon as it is a dict **subclass**
    // overriding `__getitem__`: `PyDict::get_item` reads the concrete storage and never calls
    // the override, while `NODE_CLASSES[class_name]` (`…:347`) does — measured with a subclass
    // answering `"overridden"`, the two walks built different trees. `get_item` on
    // `Bound<PyAny>` is `PyObject_GetItem`, which *is* the subscription.
    let factory = match registry.get_item(desc.class_name.as_str()) {
        Ok(factory) => factory,
        // **`KeyError` only**, what `materialise_node`'s `except KeyError` catches; anything
        // else a `__getitem__` raises propagates as itself. The message is Python's, so the
        // error path calls the module rather than spelling it a second time.
        Err(error) if error.is_instance_of::<pyo3::exceptions::PyKeyError>(py) => {
            // The message belongs to Python — tests read the string — so the error path
            // calls into the module rather than spelling it a second time.
            return Err(loud(
                tree,
                Bridge::UnknownNodeClass,
                (desc.class_name.as_str(),),
            ));
        }
        Err(error) => return Err(error),
    };

    // `{% cycle a b as c %}` and every later `{% cycle c %}` are one node in Django:
    // `do_cycle` returns the *identical* object and `CycleNode.render` keys `render_context`
    // by it, so two equal-but-distinct nodes would cycle separately. The token is re-attached
    // on the way out because `extend_nodelist` does `node.token = token` at every append, so
    // the shared object carries the **last** occurrence's token.
    let mut slot_to_fill = None;
    if let Some(slot) = desc.slot {
        let existing = resolve_ref.call1((slot,))?;
        if !existing.is_none() {
            existing.setattr("token", decode_token_or_none(py, tree, desc)?)?;
            existing.setattr("origin", origin)?;
            return Ok(existing.unbind());
        }
        slot_to_fill = Some(slot);
    }

    // Children first, completely: `ExtendsNode.__init__` walks the nodelist it is handed,
    // and an `AttrValue::NodeListRef` names a position in `child_lists` below, so a node
    // built before its children read an empty subtree.
    let mut children: Vec<(String, Py<PyAny>)> = Vec::with_capacity(desc.nodelists.len());
    for (name, child) in &desc.nodelists {
        let built = materialise_native(py, tree, child, origin, resolve_ref, register_ref, parser)?;
        children.push((name.clone(), built));
    }
    let child_lists = PyList::new(py, children.iter().map(|(_, node)| node))?;

    let kwargs = PyDict::new(py);
    for (name, node) in &children {
        // A nodelist whose **name is empty** is positional only: reachable through a
        // `NodeListRef`, never passed to `cls`. `IfNode` is why — its `__init__` takes one
        // parameter, `conditions_nodelists`, and holds the branch nodelists inside that.
        if !name.is_empty() {
            kwargs.set_item(name, node)?;
        }
    }
    // Attributes after the children, and the order is load-bearing: a
    // `NodeListRef` resolves against `child_lists`, which must be populated.
    for (name, value) in &desc.attrs {
        kwargs.set_item(
            name,
            attr_to_py(py, tree, value, resolve_ref, parser, &child_lists)?,
        )?;
    }

    let node = factory.call((), Some(&kwargs))?;

    // After construction, never before: `__init__` runs first and some read the subtree.
    node.setattr("token", decode_token_or_none(py, tree, desc)?)?;
    node.setattr("origin", origin)?;
    if let Some(slot) = slot_to_fill {
        // Filled **after** the node exists, which makes a second mention of the same cycle
        // find this one. The slot is not a constructor argument — `CycleNode.__init__` takes
        // none — so it is stored here rather than passed to `cls`.
        register_ref.call1((slot, node.clone()))?;
    }
    Ok(node.unbind())
}

/// A descriptor attribute as the Python object the node's `__init__` wants.
fn attr_to_py(
    py: Python<'_>,
    tree: &Bound<'_, PyAny>,
    value: &AttrValue,
    resolve_ref: &Bound<'_, PyAny>,
    parser: &Bound<'_, PyAny>,
    child_lists: &Bound<'_, PyList>,
) -> PyResult<Py<PyAny>> {
    // `bool` before `int`, and not as an optimisation: `bool` **is** an `int` subclass, so an
    // arm that caught it as an integer would turn `{% autoescape on %}`'s `setting` into
    // `0`/`1` — comparing equal and passing any `assertEqual` while being the wrong type.
    match value {
        AttrValue::Bool(flag) => Ok((*flag).into_pyobject(py)?.to_owned().into_any().unbind()),
        AttrValue::Int(number) => Ok((*number).into_pyobject(py)?.into_any().unbind()),
        AttrValue::Float(number) => Ok((*number).into_pyobject(py)?.into_any().unbind()),
        AttrValue::Str(text) => Ok(text.as_str().into_pyobject(py)?.into_any().unbind()),
        AttrValue::None => Ok(py.None()),
        // A `SafeString`, and the subtype is the point: a `{% verbatim %}` body's text must
        // not be escaped when the node renders. The same call `_decode_value` makes for this
        // variant (`python/django_rust_templates/engine/parser.py:757`).
        AttrValue::SafeStr(text) => call_bridge(tree, Bridge::MarkSafe, (text.as_str(),)),
        AttrValue::BigInt(text) => {
            // `builtins.int(text)`, **never** `py.eval`: the text is a validated decimal from
            // `pynum::python_int` (optional ASCII sign, ASCII digits), so this form cannot
            // treat it as code the way `eval("int(%s)")` would.
            Ok(py
                .import("builtins")?
                .getattr("int")?
                .call1((text.as_str(),))?
                .unbind())
        }
        // Each handle is loud when its **resolver** is missing, and `NodeSlot` is loud again
        // when the slot it names is empty. `Ref` deliberately is not: its slot is filled by the
        // *host*, so `None` there means "the host ran no code", an answer — that difference is
        // what `empty_is_loud` carries. The messages come from `parser.py`, one spelling called
        // from both walkers, so these arms return the helper's own exception.
        AttrValue::Ref(handle) => resolve_handle(
            tree,
            resolve_ref,
            format!("<NodeRef {}>", handle.0),
            handle.0,
            false,
        ),
        // `AttrValue::NodeSlot` holds a `usize` and `NodeRef` a `u64`, so the cast is here
        // rather than in the helper: a `usize` parameter would silently truncate a `NodeRef`
        // on a 32-bit target.
        AttrValue::NodeSlot(index) => resolve_handle(
            tree,
            resolve_ref,
            format!("<NodeSlot {index}>"),
            *index as u64,
            true,
        ),
        AttrValue::NodeListRef(index) => match child_lists.get_item(*index) {
            Ok(node) => Ok(node.unbind()),
            Err(_) => Err(loud(
                tree,
                Bridge::NodelistRefOutOfRangeError,
                (format!("<NodeListRef {index}>"), child_lists.len()),
            )),
        },
        // Containers are built here because the *container type* is the contract, not the
        // contents: `IfNode.render` unpacks each pair (`defaulttags.py:323`), and a list would
        // unpack too while being a different node (`fingerprint` tells the two apart).
        AttrValue::List(items) => {
            let built = items
                .iter()
                .map(|item| attr_to_py(py, tree, item, resolve_ref, parser, child_lists))
                .collect::<PyResult<Vec<_>>>()?;
            Ok(PyList::new(py, built)?.into_any().unbind())
        }
        AttrValue::Tuple(items) => {
            let built = items
                .iter()
                .map(|item| attr_to_py(py, tree, item, resolve_ref, parser, child_lists))
                .collect::<PyResult<Vec<_>>>()?;
            Ok(PyTuple::new(py, built)?.into_any().unbind())
        }
        // `dict(pairs)`, not a list of pairs: `token_kwargs` does `kwargs[key] = …`
        // (`base.py:1202`), so a repeated key keeps its **first** position and takes the
        // **last** value. The dedup must come first: the Python path builds the wire dict
        // first and decodes only the surviving values, so decoding a discarded value here
        // could raise on `{% include 'p.html' with a=1 b=2 a=3 %}` where Python succeeds.
        AttrValue::Map(entries) => {
            let mut surviving: Vec<(&String, &AttrValue)> = Vec::with_capacity(entries.len());
            for (key, item) in entries {
                match surviving.iter_mut().find(|(name, _)| *name == key) {
                    Some(slot) => slot.1 = item,
                    None => surviving.push((key, item)),
                }
            }
            let built = PyDict::new(py);
            for (key, item) in surviving {
                built.set_item(
                    key,
                    attr_to_py(py, tree, item, resolve_ref, parser, child_lists)?,
                )?;
            }
            Ok(built.into_any().unbind())
        }
        // The four expression arms keep their Python helpers; what moves is the *dispatch* — a
        // Rust `match` replaces an `isinstance` ladder walked once per attribute, so the ladder's
        // load-bearing order becomes a compile-time property. Each builds the **wire** pyclass
        // and calls the same Python helper with the same arguments; building the final object
        // here would be a second implementation of `smartif` and `Variable.__new__`.
        AttrValue::FilterExpr(expr) => {
            let wire = crate::filter_expr_desc_to_py(py, expr, None)?;
            call_bridge(
                tree,
                Bridge::MaterialiseFilterExpression,
                (wire, parser, resolve_ref),
            )
        }
        AttrValue::Variable(var) => {
            let wire = crate::variable_desc_to_py(py, var)?;
            call_bridge(tree, Bridge::MaterialiseVariable, (wire, parser))
        }
        AttrValue::IfCondition(condition) => {
            let wire = crate::if_condition_to_py(py, condition)?;
            call_bridge(tree, Bridge::DecodeCondition, (wire, resolve_ref))
        }
        // `BlockTranslateNode.singular`/`.plural`. Each element is rebuilt with the
        // token decoder, so a token's wire form stays single-spelt.
        AttrValue::Tokens(tokens) => {
            let built = tokens
                .iter()
                .map(|token| {
                    call_bridge(tree, Bridge::DecodeToken, (crate::token_to_py(py, token)?,))
                })
                .collect::<PyResult<Vec<_>>>()?;
            Ok(PyList::new(py, built)?.into_any().unbind())
        }
    }
}

/// A reference-table lookup, or the loud error that says why it failed.
///
/// `empty_is_loud` is the whole difference between the handles: `AttrValue::Ref`'s slot is
/// filled by the **host** during the parse, so a `None` there is a legitimate "the host ran
/// no code" answer, while `AttrValue::NodeSlot`'s is filled by **this walk**, so an empty one
/// is a wire bug. Same table, opposite answers.
fn resolve_handle(
    tree: &Bound<'_, PyAny>,
    resolve_ref: &Bound<'_, PyAny>,
    rendered: String,
    index: u64,
    empty_is_loud: bool,
) -> PyResult<Py<PyAny>> {
    if resolve_ref.is_none() {
        return Err(loud(tree, Bridge::MissingResolverError, (rendered,)));
    }
    let found = resolve_ref.call1((index,))?;
    if empty_is_loud && found.is_none() {
        return Err(loud(tree, Bridge::EmptySlotError, (rendered,)));
    }
    Ok(found.unbind())
}

fn decode_token_or_none(
    py: Python<'_>,
    tree: &Bound<'_, PyAny>,
    desc: &NodeDesc,
) -> PyResult<Py<PyAny>> {
    match &desc.token {
        None => Ok(py.None()),
        Some(token) => call_bridge(tree, Bridge::DecodeToken, (crate::token_to_py(py, token)?,)),
    }
}

/// Every Python entry point this walk is allowed to call, and the only place they are named.
///
/// **This enum is the census's teeth.** A bridge cannot be called without a variant here —
/// `call_bridge` takes a `Bridge` and nothing else — so a rename or a deletion is loud, where
/// reading `materialise.rs` as text (a renamed fallback walks past, and a *comment* can trip
/// it) or wrapping a hand-written name list (a subset assertion is a tautology) is not.
/// `the_bridge_list_is_pinned` asserts `ALL`'s length, its uniqueness, and that every name
/// exists on the module. The Python-side census keeps a different claim: **which** bridges the
/// corpus actually reaches, so one never exercised is visible rather than merely permitted.
///
/// **`NODE_CLASSES` is read with a bare `getattr` and is not a bridge**, because it is a dict
/// the walk reads rather than a function it calls. **A variant is declared by the task that
/// calls it, and not before**: an enumeration written whole needs a blanket
/// `#[allow(dead_code)]`, and an unreachable variant a reader trusts is worse than none.
///
/// The finished walk reaches ten: `materialise_node` (the host node, permanent),
/// `_materialise_variable`, `_materialise_filter_expression` (kept whole rather than
/// reimplemented — only the dispatch is Rust's), `_decode_condition`, `_decode_token` (one
/// spelling, shared with `_native.lex`), `mark_safe`, and the four error helpers
/// `_unknown_node_class`, `_missing_resolver_error`, `_empty_slot_error` and
/// `_nodelist_ref_out_of_range_error`, called rather than re-spelled so a message existing
/// tests read stays the only copy of itself.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Bridge {
    /// The host node's own object. The only node-level bridge that is permanent: its
    /// descriptor validation and error messages are asserted by tests that should keep
    /// reading the strings they were written against.
    MaterialiseNode,
    /// `_decode_token`, so a node's `token` is the same `Token` the wire path builds.
    DecodeToken,
    /// `_materialise_variable` (`python/django_rust_templates/engine/parser.py:394`), for an
    /// `AttrValue::Variable`; the helper is kept whole, only the dispatch is this walk's.
    ///
    /// **The one variant no template dispatches**: `variable_of`
    /// (`template-core/src/parser/filter_expr.rs:250`) is its only constructor and puts the
    /// `Variable` inside a `FilterExprDesc`, so the arm exists only because a hand-built
    /// descriptor can carry it (its row is `mod malformed`). The helper is reached anyway, by
    /// `_materialise_operand` (`…/parser.py:448`), which `_materialise_filter_expression` calls
    /// for every operand — so the census sees this *name* fire and cannot tell the two callers
    /// apart, which is why the enum, not the census, proves the dispatch.
    MaterialiseVariable,
    /// `_materialise_filter_expression`
    /// (`python/django_rust_templates/engine/parser.py:610`), for an `AttrValue::FilterExpr`.
    MaterialiseFilterExpression,
    /// `_decode_condition` (`python/django_rust_templates/engine/parser.py:819`), for an
    /// `AttrValue::IfCondition`.
    DecodeCondition,
    /// `mark_safe`, for an `AttrValue::SafeStr`. A `{% verbatim %}` body that arrived as a
    /// plain `str` would escape itself at render time.
    MarkSafe,
    /// `_unknown_node_class`, so the registry message has one spelling.
    UnknownNodeClass,
    /// `_missing_resolver_error` (`python/django_rust_templates/engine/parser.py:796`), for
    /// a handle with no `resolve_ref` to look it up in.
    MissingResolverError,
    /// `_empty_slot_error` (`python/django_rust_templates/engine/parser.py:803`), for a
    /// `NodeSlot` over a slot this walk never filled.
    EmptySlotError,
    /// `_nodelist_ref_out_of_range_error`
    /// (`python/django_rust_templates/engine/parser.py:826`), for an index past the end of
    /// the enclosing node's own nodelists.
    NodelistRefOutOfRangeError,
}

impl Bridge {
    /// Every bridge, enumerated, so `the_bridge_list_is_pinned` can count it.
    ///
    /// **This constant and the row that reads it are one edit.** The list is hand-written and
    /// its length derives from nothing, so adding a variant to the enum and not to the list is
    /// *not* a compile error; `name()` becoming non-exhaustive, and the review of that edit, are
    /// what catch it. The count is a tripwire, not a proof of completeness.
    #[cfg(all(test, not(feature = "extension-module")))]
    pub(crate) const ALL: [Bridge; 10] = [
        Bridge::MaterialiseNode,
        Bridge::MaterialiseVariable,
        Bridge::MaterialiseFilterExpression,
        Bridge::DecodeCondition,
        Bridge::DecodeToken,
        Bridge::MarkSafe,
        Bridge::UnknownNodeClass,
        Bridge::MissingResolverError,
        Bridge::EmptySlotError,
        Bridge::NodelistRefOutOfRangeError,
    ];

    /// The name of the Python entry point this variant stands for.
    pub(crate) fn name(self) -> &'static str {
        match self {
            Bridge::MaterialiseNode => "materialise_node",
            Bridge::DecodeToken => "_decode_token",
            Bridge::MaterialiseVariable => "_materialise_variable",
            Bridge::MaterialiseFilterExpression => "_materialise_filter_expression",
            Bridge::DecodeCondition => "_decode_condition",
            Bridge::MarkSafe => "mark_safe",
            Bridge::UnknownNodeClass => "_unknown_node_class",
            Bridge::MissingResolverError => "_missing_resolver_error",
            Bridge::EmptySlotError => "_empty_slot_error",
            Bridge::NodelistRefOutOfRangeError => "_nodelist_ref_out_of_range_error",
        }
    }
}

/// The walk's only call into the parser module: every Python call in this file goes through
/// one function taking a [`Bridge`], so the set of them is a type rather than a convention
/// (the `getattr` per node is not the cost this saves).
fn call_bridge<'py>(
    tree: &Bound<'py, PyAny>,
    bridge: Bridge,
    args: impl pyo3::call::PyCallArgs<'py>,
) -> PyResult<Py<PyAny>> {
    Ok(tree.getattr(bridge.name())?.call1(args)?.unbind())
}

/// Build one node with the Python materialiser, from a wire built for it alone.
///
/// `node_desc_to_py` is the same producer `with_ref_table` uses, so the wire keeps exactly one
/// spelling — this is that encoding called one node at a time, not a second one.
fn fallback_node(
    py: Python<'_>,
    tree: &Bound<'_, PyAny>,
    desc: &NodeDesc,
    origin: &Bound<'_, PyAny>,
    resolve_ref: &Bound<'_, PyAny>,
    register_ref: &Bound<'_, PyAny>,
    parser: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let wire = crate::node_desc_to_py(py, desc)?;
    call_bridge(
        tree,
        Bridge::MaterialiseNode,
        (wire, origin, resolve_ref, register_ref, parser),
    )
}

/// Call a Python helper that always raises, and hand back what it raised.
///
/// The `Ok` arm is `unreachable!` rather than a silent success: a helper that returned would
/// mean the message had stopped being one spelling, and a walk carrying on holding a value
/// where an error was owed is the failure this module exists to avoid.
fn loud<'py>(
    tree: &Bound<'py, PyAny>,
    bridge: Bridge,
    args: impl pyo3::call::PyCallArgs<'py>,
) -> PyErr {
    match call_bridge(tree, bridge, args) {
        Err(error) => error,
        Ok(_) => unreachable!("{} always raises", bridge.name()),
    }
}

#[cfg(all(test, not(feature = "extension-module")))]
mod malformed {
    //! The descriptors no template can produce.
    //!
    //! `{% resetcycle %}{% resetcycle c %}` is refused by Django before a descriptor exists,
    //! and a `HostNode` carrying a nodelist cannot be built by the parser at all, so these arms
    //! are reachable only from a *malformed* wire — exactly the case a Rust walk must be as
    //! loud about as the Python one.
    //!
    //! **They call `materialise_one` and `attr_to_py`, never the helpers they reach.** Driving
    //! `resolve_handle` directly would prove that helper's branch and say nothing about whether
    //! `AttrValue::NodeSlot` dispatches to it with `empty_is_loud = true`; exhaustive matching
    //! forces an arm to *exist*, not to be *right*. The `AttrValue::Variable` arm is unreachable
    //! from a well-formed tree too, and lives here because `tree_module` and its Django bootstrap
    //! do.

    use super::*;
    use template_core::parser::desc::{NodeRef, VariableDesc};

    /// The shipped `engine.parser`, which is where the messages live.
    ///
    /// The test interpreter has no Django settings configured and `python/` is not on its path,
    /// so both are set up here. The path insert is unconditional and membership-checked; only the
    /// settings call is guarded, because `settings.configured` says nothing about whether *this*
    /// interpreter can import `django_rust_templates`.
    fn tree_module(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(|path| path.parent())
            .expect("the crate lives two levels below the repo root");
        let sys_path = py.import("sys")?.getattr("path")?;
        for entry in [root.join("python"), root.join(".django")] {
            let entry = entry.to_str().expect("a UTF-8 path").to_string();
            if !sys_path.contains(&entry)? {
                sys_path.call_method1("insert", (0, &entry))?;
            }
        }
        let settings = py.import("django.conf")?.getattr("settings")?;
        if !settings.getattr("configured")?.extract::<bool>()? {
            let kwargs = PyDict::new(py);
            kwargs.set_item("DEBUG", false)?;
            kwargs.set_item("USE_I18N", false)?;
            kwargs.set_item("TEMPLATES", PyList::empty(py))?;
            settings.call_method("configure", (), Some(&kwargs))?;
            py.import("django")?.call_method0("setup")?;
        }
        py.import("django_rust_templates.engine.parser")
            .map(Bound::into_any)
    }

    fn nodelist(nodes: Vec<NodeDesc>) -> NodeListDesc {
        NodeListDesc {
            nodes,
            contains_nontext: true,
        }
    }

    fn leaf(class_name: &str) -> NodeDesc {
        NodeDesc {
            class_name: class_name.to_string(),
            attrs: Vec::new(),
            nodelists: Vec::new(),
            must_be_first: false,
            token: None,
            slot: None,
        }
    }

    fn run_one(py: Python<'_>, desc: &NodeDesc) -> PyResult<Py<PyAny>> {
        let tree = tree_module(py).unwrap();
        let none = py.None().into_bound(py);
        materialise_one(py, &tree, desc, &none, &none, &none, &tree)
    }

    /// A real reference table, and the two callables `materialise` derives from it.
    ///
    /// A Python **list**, with its own bound `__getitem__`/`__setitem__`, because that
    /// is what the compiler allocates: `Host::alloc_slot` appends `None` and returns
    /// the index, so a slot that is reserved and not yet filled reads `None` rather
    /// than raising. A `dict` would raise `KeyError` there and the slot arm's probe —
    /// a check for exactly that `None` — could not be reached at all.
    ///
    /// `reserved` seeds the placeholders; the index a test uses must be below it.
    fn ref_table<'py>(
        py: Python<'py>,
        reserved: usize,
    ) -> (Bound<'py, PyList>, Bound<'py, PyAny>, Bound<'py, PyAny>) {
        let table = PyList::new(py, std::iter::repeat_with(|| py.None()).take(reserved)).unwrap();
        let get = table.getattr("__getitem__").unwrap();
        let set = table.getattr("__setitem__").unwrap();
        (table, get, set)
    }

    #[test]
    fn an_unknown_parent_is_loud_before_its_children_are_built() {
        // **The child does not exist either**, and that is the instrument: if the
        // walk built its subtree first, the error would name `NoSuchChild`; if it
        // reads the registry first, it names `NoSuchParent`. The message
        // distinguishes the two orders with no injection needed to read it.
        Python::initialize();
        Python::attach(|py| {
            let mut parent = leaf("NoSuchParent");
            parent.nodelists = vec![("nodelist".to_string(), nodelist(vec![leaf("NoSuchChild")]))];
            let error = run_one(py, &parent).expect_err("an unknown parent is loud");
            assert!(error.is_instance_of::<pyo3::exceptions::PyLookupError>(py));
            let rendered = error.to_string();
            assert!(rendered.contains("NoSuchParent"), "{rendered}");
            assert!(
                !rendered.contains("NoSuchChild"),
                "the children were built before the registry was read: {rendered}"
            );
        });
    }

    #[test]
    fn a_node_slot_over_an_empty_table_is_loud() {
        // The **dispatch**, not the helper: `AttrValue::NodeSlot(0)` through `attr_to_py` with a
        // real resolver returning `None`. `NodeRef` over the same table is *not* loud — its
        // `empty_is_loud` is `false` — and this row and the next hold those two apart.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let empty = PyList::new(py, [py.None()]).unwrap().into_any();
            let lookup = empty.getattr("__getitem__").unwrap();
            let error = attr_to_py(
                py,
                &tree,
                &AttrValue::NodeSlot(0),
                &lookup,
                &tree,
                &PyList::empty(py),
            )
            .expect_err("an empty slot is loud");
            assert!(error.is_instance_of::<pyo3::exceptions::PyLookupError>(py));
            assert!(
                error
                    .to_string()
                    .contains("is an empty reference-table slot"),
                "{error}"
            );
        });
    }

    #[test]
    fn a_node_ref_over_the_same_empty_table_is_not_loud() {
        // The control for the row above, and the reason `empty_is_loud` is an argument rather than
        // a constant: `NodeRef`'s slot is filled by the *host*, so `None` there is a legitimate
        // "the host ran no code" answer.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let empty = PyList::new(py, [py.None()]).unwrap().into_any();
            let lookup = empty.getattr("__getitem__").unwrap();
            let found = attr_to_py(
                py,
                &tree,
                &AttrValue::Ref(NodeRef(0)),
                &lookup,
                &tree,
                &PyList::empty(py),
            )
            .expect("a `NodeRef` over an empty slot is an answer, not an error");
            assert!(found.bind(py).is_none());
        });
    }

    #[test]
    fn a_handle_with_no_resolver_is_loud_for_both_shapes() {
        // Both arms, because the two `resolve_ref.is_none()` checks are separate
        // lines and only one of them being present is a plausible edit.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let none = py.None().into_bound(py);
            let lists = PyList::empty(py);
            for value in [AttrValue::Ref(NodeRef(0)), AttrValue::NodeSlot(0)] {
                let error = attr_to_py(py, &tree, &value, &none, &tree, &lists)
                    .expect_err("a missing resolver is loud");
                assert!(error.is_instance_of::<pyo3::exceptions::PyLookupError>(py));
                assert!(
                    error.to_string().contains("needs a resolve_ref callback"),
                    "{value:?}: {error}"
                );
            }
        });
    }

    #[test]
    fn the_bridge_list_is_pinned() {
        // **The one claim the Python census cannot make for itself.** A census wrapping a list of
        // names and asserting the calls were a subset is a tautology — it can only see names it was
        // handed. The set of entry points the walk may call is a *type* instead, and this row makes
        // a rename or deletion loud (adding a variant to the enum but not to `ALL` is caught by
        // `name()` becoming non-exhaustive, not here).
        //
        // **Scoped to the walk.** `compile_template` does one `getattr("materialise")` of its own
        // (`lib.rs`) on the *non-native* arm — the A/B branch itself, not a fallback, and the one
        // Python call in this crate that is not a `Bridge`.
        Python::initialize();
        Python::attach(|py| {
            assert_eq!(Bridge::ALL.len(), 10);
            let tree = tree_module(py).unwrap();
            let mut names: Vec<&str> = Bridge::ALL.iter().map(|bridge| bridge.name()).collect();
            names.sort_unstable();
            names.dedup();
            assert_eq!(names.len(), Bridge::ALL.len(), "two bridges share a name");
            for bridge in Bridge::ALL {
                assert!(
                    tree.hasattr(bridge.name()).unwrap(),
                    "{} is not on the parser module",
                    bridge.name()
                );
            }
        });
    }

    #[test]
    fn the_variable_arm_is_the_one_no_template_reaches() {
        // **A row for an arm no template can produce.** `variable_of`
        // (`template-core/src/parser/filter_expr.rs:250`) is the only constructor of
        // `AttrValue::Variable`, and its call sites are all inside `parse_filter_expression`, where
        // the `Variable` becomes a `FilterVar` — a `FilterExprDesc.var` or one of its
        // `filters[].args`. So `attr_to_py` is always handed the `FilterExpr` around it, and a
        // template-driven row would be green for the filter-expression arm's reason (measured:
        // replacing this arm's body with a `panic!` reddens no row). The arm still has to exist —
        // the `match` is exhaustive — and still has to be *right*, because a hand-built descriptor
        // can carry it. What it must not do is re-parse the token in Python: `Variable.__new__`
        // plus slot assignment is where Rust's parse survives, so the wire object goes over and
        // the helper fills the slots.
        //
        // **Every field below is one a re-parse of `var` would get wrong**: `lookups` is
        // `("x", "y")` on a `var` of `"a.b"`, and `translate` is true, where `Variable("a.b")`
        // yields `("a", "b")` and `False`.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            // The **port's** `Variable` (`django_rust_templates/engine/base.py`), which `parser.py`
            // imports and `FilterExpression.is_var` tests against — not `django.template.base.Variable`.
            let variable_class = py
                .import("django_rust_templates.engine.base")
                .unwrap()
                .getattr("Variable")
                .unwrap();
            let none = py.None().into_bound(py);
            let desc = VariableDesc {
                var: "a.b".to_string(),
                literal: None,
                lookups: Some(vec!["x".to_string(), "y".to_string()]),
                translate: true,
            };
            let built = attr_to_py(
                py,
                &tree,
                &AttrValue::Variable(Box::new(desc)),
                &none,
                &tree,
                &PyList::empty(py),
            )
            .expect("a `Variable` attribute materialises");
            let built = built.bind(py);
            // `isinstance(result, Variable)` is what `FilterExpression.is_var`
            // reads, so a lookalike would render differently.
            assert!(built.is_instance(&variable_class).unwrap());
            assert_eq!(
                built.getattr("var").unwrap().extract::<String>().unwrap(),
                "a.b"
            );
            // A **tuple**, Django's `tuple(var.split("."))` — and a list on the wire,
            // so this is also the row that says the helper converted it rather than
            // assigning what Rust handed over. The values are the wire's, not
            // `var`'s, so a re-parse cannot produce them.
            let expected = PyTuple::new(py, ["x", "y"]).unwrap();
            assert!(
                built.getattr("lookups").unwrap().eq(&expected).unwrap(),
                "the lookups came back as Rust's wire list, not Django's tuple"
            );
            assert!(
                built.getattr("translate").unwrap().is_truthy().unwrap(),
                "`translate` was dropped rather than forwarded from the wire"
            );
            assert!(built.getattr("literal").unwrap().is_none());
            // `__slots__` rather than `__dict__`, so an unassigned slot raises here
            // instead of reading back as absent.
            assert!(built.getattr("message_context").unwrap().is_none());

            // `literal` is the one field the first descriptor leaves empty, so it takes
            // a second — and it is made discriminating the same way: the literal is not
            // `var`'s, so `Variable("2")` would give `2` and not `7`.
            let literal = VariableDesc {
                var: "2".to_string(),
                literal: Some(AttrValue::Int(7)),
                lookups: None,
                translate: false,
            };
            let built = attr_to_py(
                py,
                &tree,
                &AttrValue::Variable(Box::new(literal)),
                &none,
                &tree,
                &PyList::empty(py),
            )
            .expect("a literal `Variable` materialises");
            let built = built.bind(py);
            assert_eq!(
                built.getattr("literal").unwrap().extract::<i64>().unwrap(),
                7
            );
            assert!(built.getattr("lookups").unwrap().is_none());
        });
    }

    #[test]
    fn a_nodelist_ref_past_the_end_is_loud_and_names_the_arity() {
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let none = py.None().into_bound(py);
            let lists = PyList::new(py, [none.clone()]).unwrap();
            let error = attr_to_py(py, &tree, &AttrValue::NodeListRef(3), &none, &tree, &lists)
                .expect_err("an index past the end is loud");
            assert!(error.is_instance_of::<pyo3::exceptions::PyLookupError>(py));
            let rendered = error.to_string();
            assert!(rendered.contains("is out of range"), "{rendered}");
            assert!(rendered.contains("1 nodelist(s)"), "{rendered}");
        });
    }

    #[test]
    fn a_nodelist_ref_inside_the_range_is_the_nodelist_itself() {
        // The control, and not decoration: it is the row that would fail if the
        // error mapping above were widened to swallow `Ok` as well, which is the
        // shape a `match … { Err(_) => … }` written as a bare `match` produces.
        //
        // The element is a **real** nodelist rather than `None`. A `None` in the
        // list is also what an arm that resolved nothing would return, so the
        // pointer comparison would have held for the wrong reason.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let none = py.None().into_bound(py);
            let (_, get, set) = ref_table(py, 1);
            let real = materialise_native(
                py,
                &tree,
                &nodelist(vec![leaf("CommentNode")]),
                &none,
                &get,
                &set,
                &tree,
            )
            .expect("a one-leaf nodelist materialises");
            let expected = real.bind(py).as_ptr();
            let lists = PyList::new(py, [real]).unwrap();
            let found = attr_to_py(py, &tree, &AttrValue::NodeListRef(0), &none, &tree, &lists)
                .expect("an in-range index resolves");
            assert_eq!(
                found.bind(py).as_ptr(),
                expected,
                "an in-range `NodeListRef` did not return the nodelist at that index"
            );
        });
    }

    #[test]
    fn a_slot_is_probed_first_and_the_second_mention_is_the_object_it_found() {
        // **The arm no row above reaches.** Every hand-built descriptor here leaves
        // `slot` at `None`, so this is the only row pinning `{% cycle %}`'s shared identity.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let none = py.None().into_bound(py);
            let (table, get, set) = ref_table(py, 8);

            let mut first = leaf("CommentNode");
            first.slot = Some(7);
            let built = materialise_one(py, &tree, &first, &none, &get, &set, &tree)
                .expect("a slotted leaf builds");
            assert!(
                !table.get_item(7).unwrap().is_none(),
                "the slot was never registered, so a second mention has nothing to find"
            );

            let mut second = leaf("TextNode");
            second.slot = Some(7);
            let again = materialise_one(py, &tree, &second, &none, &get, &set, &tree)
                .expect("the second mention resolves from the table");
            assert_eq!(
                built.bind(py).as_ptr(),
                again.bind(py).as_ptr(),
                "the second mention built a node of its own instead of reusing the slot"
            );
        });
    }

    #[test]
    fn a_slot_a_nested_build_filled_is_overwritten_by_the_outer_one() {
        // The slot is **probed** before the children are built and **registered**
        // after, so a child filling the same slot is overwritten by its parent.
        // Registering before the children would leave the table holding the child.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            let none = py.None().into_bound(py);
            let (table, get, set) = ref_table(py, 8);

            let mut inner = leaf("BlockNode");
            inner.attrs = vec![("name".to_string(), AttrValue::Str("inner".to_string()))];
            inner.nodelists = vec![("nodelist".to_string(), nodelist(Vec::new()))];
            inner.slot = Some(7);

            let mut outer = leaf("BlockNode");
            outer.attrs = vec![("name".to_string(), AttrValue::Str("outer".to_string()))];
            outer.nodelists = vec![("nodelist".to_string(), nodelist(vec![inner]))];
            outer.slot = Some(7);

            let built = materialise_one(py, &tree, &outer, &none, &get, &set, &tree)
                .expect("the outer node builds");
            let built = built.bind(py);

            // **Which node came back, not merely that one did.** Moving the probe below
            // child construction would return the child while a pointer check stayed green.
            let name = |node: &Bound<'_, PyAny>| -> String {
                node.getattr("name").unwrap().extract().unwrap()
            };
            assert_eq!(
                name(built),
                "outer",
                "the walk returned the child, not the parent"
            );
            let inner_built = built.getattr("nodelist").unwrap().get_item(0).unwrap();
            assert_eq!(name(&inner_built), "inner");
            assert_ne!(
                inner_built.as_ptr(),
                built.as_ptr(),
                "the outer node is the child it was supposed to contain"
            );

            let held = table.get_item(7).unwrap();
            assert!(!held.is_none(), "the outer node registered its slot");
            assert_eq!(
                held.as_ptr(),
                built.as_ptr(),
                "the child's registration outlived the outer node's, so the slot was \
                 filled before the children were built"
            );
        });
    }
}
