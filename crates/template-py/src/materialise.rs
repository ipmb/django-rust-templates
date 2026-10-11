//! The native materialiser: build the compatibility tree by walking the IR.
//!
//! `parser.materialise` does this in Python by walking a *wire* of nested tuples
//! and pyclasses. Measured on `real-world/large` (9279 chars, 621 nodes) that
//! walk is 1153 us of a 1834 us compile — 63% — while the plan built from the
//! same tree is 26 us. The wire exists only to be decoded.
//!
//! **Three properties of `materialise_node` are contracts.** Each is the natural
//! thing to reorder when optimising and the failures are silent, so each is named
//! here, and each gets a row when the task that could break it lands.
//!
//! **Task 3 preserves all three the easy way and pins none of them.** Every node
//! still goes through `materialise_node`, so the orderings below are inherited from
//! Python rather than established here; nothing in this file yet reads the registry,
//! builds a descendant, or attaches a token. What *is* Rust's at Task 3 is the
//! root-list loop, and that loop had to be written to Python's evaluation order —
//! see [`materialise_native`], where binding `append` before the build is the
//! whole content of the first contract at this stage. Tasks 4-7 move the
//! construction down arm by arm, and that is when the three become this module's
//! promises rather than Python's.
//!
//! 1. The registry is read **before** the children are built
//!    (`python/django_rust_templates/engine/parser.py:347` against
//!    `python/django_rust_templates/engine/parser.py:372`). A factory that rebinds
//!    `NODE_CLASSES["IfNode"]` while a child is built is observed by that child's
//!    siblings and *not* by the parent, and an unknown class name raises before any
//!    child work.
//! 2. Children are materialised **before** the parent is constructed, and the
//!    attributes are decoded **after** the children exist. `ExtendsNode.__init__`
//!    reads the nodelist it is handed, and an `AttrValue::NodeListRef` names a
//!    position in a list that must already be populated.
//! 3. `token` and `origin` are attached **after** construction. `__init__` runs
//!    first and some of them read the subtree.
//!
//! **The fallback is the design, not scaffolding.** Every shape this module does
//! not yet build is handed to the Python decoder for that shape alone. Tasks 4-7
//! remove fallbacks one group at a time; the host-node arm keeps one permanently,
//! because it is a rare node whose descriptor validation and error messages are
//! asserted by tests that should keep reading the strings they were written
//! against.

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
    // `contains_nontext` is a *class* attribute on `NodeList`, so leaving it
    // alone is how it reads False. Only the True transition is written, which is
    // what makes a second, text-only nodelist read False again.
    if desc.contains_nontext {
        nodelist.setattr("contains_nontext", true)?;
    }
    for node in &desc.nodes {
        // **`append` is bound before the node is built, and that is Python's
        // evaluation order rather than a micro-optimisation.** `materialise` reads
        // `nodelist.append(materialise_node(...))`, so `LOAD_METHOD` resolves
        // `append` first and only then evaluates the argument. A factory that
        // rebinds `NodeList.append` while it builds a node is therefore **not**
        // seen by that node's own append, and *is* seen by every later one.
        //
        // The obvious spelling — `nodelist.call_method1("append", (built,))` after
        // the build — uses the replacement for the very append the original was
        // bound to serve. Measured on a one-node template whose `TextNode` factory
        // rebinds `NodeList.append`: Python calls the replacement 0 times, the
        // after-the-build spelling calls it once.
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

    // **Before the children**, which is the review's finding: `materialise_node`
    // reads the registry at
    // `python/django_rust_templates/engine/parser.py:347` and builds children at
    // `python/django_rust_templates/engine/parser.py:372`, so a factory that
    // rebinds the registry while a child is built is seen by the child's siblings
    // and not by this parent. Reading it after the children would build a
    // different tree with the same class names.
    let registry = tree.getattr("NODE_CLASSES")?;
    // **Subscription on the object, never `PyDict::get_item`.** `NODE_CLASSES` is a
    // plain dict today, so the two agree on every input the corpus reaches — and they
    // stop agreeing as soon as it is a dict **subclass** that overrides `__getitem__`:
    // `PyDict::get_item` reads the concrete dict storage and never calls the override,
    // while `NODE_CLASSES[class_name]` at
    // `python/django_rust_templates/engine/parser.py:347` does. Measured with a
    // subclass answering `"overridden"` for every key: the Python path built the
    // override's value, a `get_item` walk built the real node, and the two trees
    // differed in the class of a node with the same name. `get_item` on
    // `Bound<PyAny>` is `PyObject_GetItem`, which *is* the subscription — so the
    // registry is never cast down to `PyDict` here.
    let factory = match registry.get_item(desc.class_name.as_str()) {
        Ok(factory) => factory,
        // **`KeyError` only**, which is exactly what `materialise_node`'s
        // `except KeyError` catches; anything else a `__getitem__` raises propagates
        // as itself rather than being rewritten into the registry message.
        Err(error) if error.is_instance_of::<pyo3::exceptions::PyKeyError>(py) => {
            // The message belongs to Python — it is asserted by tests that should
            // keep reading the string they were written against — so the error path
            // calls into the module rather than spelling it a second time.
            return Err(loud(
                tree,
                Bridge::UnknownNodeClass,
                (desc.class_name.as_str(),),
            ));
        }
        Err(error) => return Err(error),
    };

    // `{% cycle a b as c %}` and every later `{% cycle c %}` are one node in
    // Django: `do_cycle` returns the *identical* object, and `CycleNode.render`
    // keys `render_context` by it, so two equal-but-distinct nodes would cycle
    // separately. The table is what makes them one.
    //
    // The token is re-attached on the way out, and that is not tidiness — it is
    // `extend_nodelist` doing `node.token = token` at every append, so the shared
    // object carries the **last** occurrence's token and every position reads
    // that one.
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

    // Children first, completely. `ExtendsNode.__init__` walks the nodelist it is
    // handed, and an `AttrValue::NodeListRef` names a position in `child_lists`
    // below, so a node built before its children is a node that read an empty
    // subtree.
    let mut children: Vec<(String, Py<PyAny>)> = Vec::with_capacity(desc.nodelists.len());
    for (name, child) in &desc.nodelists {
        let built = materialise_native(py, tree, child, origin, resolve_ref, register_ref, parser)?;
        children.push((name.clone(), built));
    }
    let child_lists = PyList::new(py, children.iter().map(|(_, node)| node))?;

    let kwargs = PyDict::new(py);
    for (name, node) in &children {
        // A child nodelist whose **name is empty** is positional only: it is
        // reachable through a `NodeListRef` and is not passed to `cls`.
        // `IfNode` is why — its `__init__` takes exactly one parameter,
        // `conditions_nodelists`, and holds the branch nodelists inside that.
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

    // After construction, never before: `__init__` runs first and some of them
    // read the subtree.
    node.setattr("token", decode_token_or_none(py, tree, desc)?)?;
    node.setattr("origin", origin)?;
    if let Some(slot) = slot_to_fill {
        // Filled **after** the node exists, which is what makes a second mention
        // of the same cycle find this one rather than build its own. The slot is
        // not a constructor argument — `CycleNode.__init__` takes none — so it is
        // stored here rather than passed to `cls`.
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
    // `bool` before `int`, and it is not an optimisation: `bool` is an `int`
    // subclass, and an arm that caught it as an integer would turn
    // `{% autoescape on %}`'s `setting` into `0`/`1` — which compares equal and
    // passes any `assertEqual` while being the wrong type.
    match value {
        AttrValue::Bool(flag) => Ok((*flag).into_pyobject(py)?.to_owned().into_any().unbind()),
        AttrValue::Int(number) => Ok((*number).into_pyobject(py)?.into_any().unbind()),
        AttrValue::Float(number) => Ok((*number).into_pyobject(py)?.into_any().unbind()),
        AttrValue::Str(text) => Ok(text.as_str().into_pyobject(py)?.into_any().unbind()),
        AttrValue::None => Ok(py.None()),
        // A `SafeString`, and the subtype is the whole point: a `{% verbatim %}`
        // body's text must not be escaped when the node finally renders. This is
        // the same call `_decode_value` makes for the same variant
        // (`python/django_rust_templates/engine/parser.py:757`), reached without
        // building the wire object first.
        AttrValue::SafeStr(text) => call_bridge(tree, Bridge::MarkSafe, (text.as_str(),)),
        AttrValue::BigInt(text) => {
            // `builtins.int(text)`, **never** `py.eval`. The text is a validated
            // decimal produced by `pynum::python_int` — an optional ASCII sign
            // then ASCII digits — so the only thing it can denote is a number.
            // The tempting `py.eval("int(%s)" % text)` would accept the text as
            // *code*, which no lint catches; this form cannot, whatever it says.
            Ok(py
                .import("builtins")?
                .getattr("int")?
                .call1((text.as_str(),))?
                .unbind())
        }
        // Each is loud when its **resolver** is missing, and `NodeSlot` is loud again
        // when the slot it names is empty. `Ref` is deliberately not: its slot is
        // filled by the *host*, so `None` there means "the host ran no code" — an
        // answer, not a failure, and the host arm checks for it itself. That
        // difference is what `empty_is_loud` carries. Where it does apply, the
        // loudness is the value: a silent `None` renders an empty string and reports
        // nothing. The messages come from `parser.py` — one spelling, called from both
        // walkers — so these arms return the helper's own exception rather than a
        // `LookupError` built here.
        AttrValue::Ref(handle) => resolve_handle(
            tree,
            resolve_ref,
            format!("<NodeRef {}>", handle.0),
            handle.0,
            false,
        ),
        // `AttrValue::NodeSlot` holds a `usize` and `NodeRef` a `u64`, so the cast
        // is here rather than in the helper: a `usize` parameter would silently
        // truncate a `NodeRef` on a 32-bit target, and the index is handed to
        // Python either way, where `u64` is the wider and therefore safer spelling.
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
        // Containers are built here rather than left to the fallback because the
        // *container type* is the contract, not the contents: `IfNode.render`
        // unpacks each pair (`defaulttags.py:323`), and a list would unpack too
        // while being a different node. `fingerprint` tells the two apart.
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
        // `dict(pairs)` and not a list of pairs: `token_kwargs` does
        // `kwargs[key] = …` (`base.py:1202`), so a repeated key keeps its **first**
        // position and takes the **last** value. Insertion into a dict reproduces
        // that; a list of pairs does not.
        //
        // **The dedup comes first, and the order is load-bearing.** The Python
        // path builds the wire `dict` first — `set_item` is where that rule is
        // applied — and decodes only the surviving values, in first-key order. So
        // the value a repeated key discards is **never decoded**, and decoding it
        // here would diverge with teeth: on `{% include 'p.html' with a=1 b=2 a=3 %}`
        // a helper that raised on the discarded `1` would make this walk fail where
        // Python succeeds, on a template that renders.
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
        // The four expression arms keep their Python helpers. What moves is the
        // *dispatch*: a Rust `match` on a variant replaces an `isinstance` ladder
        // walked once per attribute, and the ladder's order — which is
        // load-bearing, since a `FilterExpr` reaching the `list` arm would be
        // passed through as an object and nothing would report it — becomes a
        // compile-time property instead of a comment.
        //
        // Each builds the **wire** pyclass and then calls the same Python helper
        // the Python decoder calls, with the same arguments. Building the final
        // object here instead would mean a second implementation of `smartif` and
        // of `Variable.__new__`.
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
/// `empty_is_loud` is the whole difference between the two handles, and it is not
/// a detail: `AttrValue::Ref`'s slot is filled by the **host** during the parse, so
/// a `None` there is a legitimate "the host ran no code" answer that `HostNode`
/// checks for itself — while `AttrValue::NodeSlot`'s is filled by **this walk**,
/// so an empty one is a wire bug. Same table, opposite answers.
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

/// Every Python entry point this walk is allowed to call, and the only place
/// they are named.
///
/// **This enum is the census's teeth, and it exists because two earlier attempts
/// at the same proof had none.** The first read `materialise.rs` as text and
/// asserted that certain substrings were absent — which a renamed fallback walks
/// straight past, and which a *comment* can trip: the row rejected the very
/// implementation Task 7 writes. The second wrapped a hand-written list of names
/// in `tests/test_materialiser_parity.py` and asserted the called set was a subset
/// of that list, which is true by construction and cannot see a name that is not
/// on it. **A bridge cannot be called without a variant here** — `call_bridge` takes
/// a `Bridge` and nothing else — and Task 8 adds
/// `the_bridge_list_is_pinned` to `walk_depth`, which asserts the enumerated list's
/// length, its uniqueness and that every name exists on the module. Adding an eleventh
/// Python bridge is then a red row in Rust, in the crate that owns the walk.
///
/// The Python-side census keeps a different claim, and the two are not redundant:
/// it records **which** bridges the corpus actually reaches, so a bridge that is
/// never exercised is visible rather than merely permitted.
///
/// **`NODE_CLASSES` is read with a bare `getattr` and is not a bridge**, because it
/// is a dict the walk reads rather than a function it calls — there is nothing to
/// dispatch and nothing to return an exception.
///
/// **A variant is declared by the task that calls it, and not before.** This enum
/// was first written whole — every bridge the finished walk reaches — and that
/// was wrong twice over. All but two had no constructor in *either*
/// compilation of the crate, so they needed a blanket `#[allow(dead_code)]` to
/// survive `-D warnings`; and an enumeration nothing calls establishes no pin, since
/// it is a list that can be left stale by exactly the edit it exists to catch. An
/// unreachable variant that a reader trusts is worse than no variant. So the census
/// is built the same way it is enforced: **one variant per arm, added by the task
/// that adds the arm**, and Task 8's `the_bridge_list_is_pinned` counts what is
/// actually there.
///
/// **What the finished walk reaches, for a reader of Tasks 4-8**: `materialise_node`
/// (the host node, permanent), `_materialise_variable` and `_materialise_filter_expression`
/// (kept whole rather than reimplemented — the *dispatch* is Rust's, what they do once
/// reached is Python's), `_decode_condition`, `_decode_token` (which has one spelling,
/// shared with `_native.lex`), `mark_safe` (so a `{% verbatim %}` body does not escape
/// itself), and the four error helpers `_unknown_node_class`,
/// `_missing_resolver_error`, `_empty_slot_error` and
/// `_nodelist_ref_out_of_range_error`, called rather than re-spelled so that a message
/// existing tests read stays the only copy of itself. That is ten.
///
/// **Which task declares which**, so a reader of Tasks 4-8 can check the enum against
/// this list rather than trusting it: Task 4 adds `DecodeValue`, `DecodeToken`,
/// `MarkSafe` and `UnknownNodeClass`; Task 5 adds the three reference and arity errors;
/// Task 6 adds none, because it extends `attr_to_py` and introduces no new call; Task 7
/// adds `MaterialiseVariable`, `MaterialiseFilterExpression` and `DecodeCondition`, and
/// **deletes `DecodeValue`** with `fallback_attr`. That is eight declared before Task 7
/// and ten after, which is the number Task 8's pin asserts.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Bridge {
    /// The host node's own object. The only bridge that exists at Task 3, and the
    /// only node-level one that is permanent: the host node's descriptor validation
    /// and error messages are asserted by tests that should keep reading the strings
    /// they were written against.
    MaterialiseNode,
    /// `_decode_token`, so a node's `token` is the same `Token` the wire path builds.
    DecodeToken,
    /// `_materialise_variable` (`python/django_rust_templates/engine/parser.py:394`),
    /// for an `AttrValue::Variable`. The helper is kept whole rather than
    /// reimplemented — what it does once reached is Python's; only the dispatch is
    /// this walk's.
    ///
    /// **The one variant no template dispatches.** A bare `AttrValue::Variable` is not
    /// a node attribute any parser produces: `variable_of`
    /// (`template-core/src/parser/filter_expr.rs:250`) is
    /// its only constructor and it puts the `Variable` inside a `FilterExprDesc`. So
    /// this arm is here because the `match` is exhaustive and a hand-built descriptor
    /// can carry it, and its row is `mod malformed`, not a template.
    ///
    /// **The helper is reached nonetheless**, by `_materialise_operand`
    /// (`python/django_rust_templates/engine/parser.py:448`), which `_materialise_filter_expression` calls for every
    /// operand — so Task 8's census sees this *name* fire and cannot tell the two
    /// callers apart. That is the census's stated limit and the reason the enum, not
    /// the census, is what proves the dispatch.
    MaterialiseVariable,
    /// `_materialise_filter_expression`
    /// (`python/django_rust_templates/engine/parser.py:610`), for an
    /// `AttrValue::FilterExpr`.
    MaterialiseFilterExpression,
    /// `_decode_condition` (`python/django_rust_templates/engine/parser.py:819`),
    /// for an `AttrValue::IfCondition`.
    DecodeCondition,
    /// `mark_safe`, for an `AttrValue::SafeStr`. A `{% verbatim %}` body that
    /// arrived as a plain `str` would escape itself at render time.
    MarkSafe,
    /// `_unknown_node_class`, so the registry message has one spelling.
    UnknownNodeClass,
    /// `_missing_resolver_error` (`python/django_rust_templates/engine/parser.py:928`),
    /// for a handle with no `resolve_ref` to look it up in.
    MissingResolverError,
    /// `_empty_slot_error` (`python/django_rust_templates/engine/parser.py:935`), for
    /// a `NodeSlot` over a slot this walk never filled.
    EmptySlotError,
    /// `_nodelist_ref_out_of_range_error`
    /// (`python/django_rust_templates/engine/parser.py:960`), for an index past the
    /// end of the enclosing node's own nodelists.
    NodelistRefOutOfRangeError,
}

impl Bridge {
    /// Every bridge, enumerated, so that `the_bridge_list_is_pinned` can count it.
    ///
    /// **This constant and the row that reads it are one edit and land together.**
    /// Enumerated without a reader it needs a blanket `#[allow(dead_code)]` in both
    /// compilations of this crate, and an allow that wide would hide a genuinely
    /// unused variant as readily as an unread constant.
    ///
    /// **Ten, and the number is meant to change exactly once**, in the commit that
    /// says so: Task 7 deleted `Bridge::DecodeValue` with `fallback_attr` and added
    /// three, so it went eight to ten.
    ///
    /// **What this list cannot see, stated here rather than in the row's comment
    /// alone:** it is hand-written and its length derives from nothing, so adding a
    /// variant to the enum and not to the list is *not* a compile error. What catches
    /// that is `name()` — exhaustive, so the compiler sends the author to it — and the
    /// review of that edit. The count is a tripwire, not a proof of completeness.
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

/// The walk's only call into the parser module.
///
/// Deliberately small. Its value is not that it saves anything — `getattr` per
/// node is not the cost — but that every Python call in this file goes through one
/// function that takes a [`Bridge`], so the set of them is a type rather than a
/// convention.
fn call_bridge<'py>(
    tree: &Bound<'py, PyAny>,
    bridge: Bridge,
    args: impl pyo3::call::PyCallArgs<'py>,
) -> PyResult<Py<PyAny>> {
    Ok(tree.getattr(bridge.name())?.call1(args)?.unbind())
}

/// Build one node with the Python materialiser, from a wire built for it alone.
///
/// `node_desc_to_py` is the same producer `with_ref_table` uses, so the wire
/// still has exactly one spelling — this is not a second encoding, it is the
/// existing one called one node at a time.
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
/// The `Ok` arm is `unreachable!` rather than a silent success: a helper that
/// returned would mean the message had stopped being one spelling, and a walk
/// that carried on holding a value where an error was owed is the failure this
/// whole module exists to avoid.
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
    //! `{% resetcycle %}{% resetcycle c %}` is refused by Django before a
    //! descriptor exists, and a `HostNode` carrying a nodelist cannot be built by
    //! the parser at all. So these arms are reachable only from a *malformed*
    //! wire, which is exactly the case a Rust walk must be as loud about as the
    //! Python one — and exactly the case no Python-driven test can reach.
    //!
    //! **They call `materialise_one` and `attr_to_py`, never the helpers they
    //! reach.** Driving `resolve_handle` directly would prove that helper's branch
    //! and say nothing about whether `AttrValue::NodeSlot` dispatches to it with
    //! `empty_is_loud = true`. Exhaustive matching forces an arm to *exist*; it
    //! does not force the arm to be *right*. The two arguments that differ between
    //! the handles — `empty_is_loud`, and which helper is called — are the whole
    //! content of the arm, so the arm is the thing under test.
    //!
    //! **One row here is not about a malformed descriptor and stays anyway.** The
    //! `AttrValue::Variable` arm is unreachable from a *well-formed* tree as well
    //! — its only constructor, `variable_of`, puts the `Variable` inside a
    //! `FilterExprDesc` — so `tests/test_materialiser_parity.py` cannot drive it
    //! from a template either. It lives here because `tree_module` and its Django
    //! bootstrap do, and a second copy of that bootstrap in a second module is a
    //! second thing to keep in step.

    use super::*;
    use template_core::parser::desc::{NodeRef, VariableDesc};

    /// The shipped `engine.parser`, which is where the messages live.
    ///
    /// The interpreter in this test binary has no Django settings configured and
    /// `python/` is not on its path, so both are set up here. The path insert is
    /// unconditional and membership-checked; only the settings call is guarded,
    /// because `settings.configured` says nothing about whether *this*
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
        // The **dispatch**, not the helper: `AttrValue::NodeSlot(0)` through
        // `attr_to_py` with a real resolver returning `None`. `NodeRef` over the
        // same table is *not* loud — its `empty_is_loud` is `false` — and this row
        // and the next are what hold those two apart.
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
        // The control for the row above, and the reason `empty_is_loud` is an
        // argument rather than a constant: `NodeRef`'s slot is filled by the
        // *host*, so `None` there is a legitimate "the host ran no code" answer
        // that the host arm checks for itself.
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
        // **The one claim the Python census cannot make for itself.** A census that
        // wraps a list of names and asserts the calls were a subset of that list is a
        // tautology: it can only ever see the names it was handed. The set of Python
        // entry points the walk may call is a *type* instead, and this is the row that
        // makes a rename or a deletion loud. Adding a variant to the enum and not to
        // `ALL` is caught by `name()` becoming non-exhaustive and by the review of
        // that edit, **not** here — `ALL` is hand-written and its length derives from
        // nothing. `ALL`'s own comment says so.
        //
        // **Scoped to the walk, and the scope is stated rather than implied.**
        // `compile_template` does one `getattr("materialise")` of its own (`lib.rs`)
        // on the *non-native* arm — that is the A/B branch itself, not a fallback, and
        // it is the one Python call in this crate that is not a `Bridge`. It is also
        // the call the switch exists to choose, so it cannot be moved behind the
        // switch it implements.
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
        // **A row for an arm no template can produce, and the reason it is here.**
        // `variable_of` (`template-core/src/parser/filter_expr.rs:250`) is the only
        // constructor of `AttrValue::Variable`, and its three call sites are all
        // inside `parse_filter_expression`, where the `Variable` becomes a
        // `FilterVar` — `FilterExprDesc.var`, or one of its `filters[].args`. So the
        // attribute `attr_to_py` is handed is always the `FilterExpr` around it, and
        // a template-driven row would be green for the filter-expression arm's
        // reason. Measured, not assumed: replacing this arm's body with a `panic!`
        // reddens no row in `scripts/run_unit_tests.sh`.
        //
        // The arm still has to exist, because the `match` is exhaustive, and it
        // still has to be *right*, because a hand-built descriptor can carry it.
        // The one thing it must not do is re-parse the token in Python:
        // `Variable.__new__` plus slot assignment is where Rust's parse survives, so
        // the wire object goes over and the helper fills the slots.
        //
        // **Every field below is one a re-parse of `var` would get wrong**, which is
        // what makes the row evidence for that rather than for "the object exists".
        // `lookups` is `("x", "y")` on a `var` of `"a.b"`, and `translate` is true —
        // `Variable("a.b")` yields `("a", "b")` and `False`.
        Python::initialize();
        Python::attach(|py| {
            let tree = tree_module(py).unwrap();
            // The **port's** `Variable` (`django_rust_templates/engine/base.py`),
            // which is what `parser.py` imports and what
            // `FilterExpression.is_var` tests against — not
            // `django.template.base.Variable`.
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
        // **The arm no row above this one reaches.** Every hand-built descriptor in this
        // module until now leaves `slot` at `None`, so all of them would stay green if
        // the slot arm were deleted outright — exactly the "dead code with a
        // live-looking call site" the plan's Task 5 note warns about. `{% cycle %}`'s
        // shared identity is the behaviour this pins.
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
        // The order, which is the whole of the arm: the slot is **probed** before the
        // children are built and **registered** after, so a child that fills the same
        // slot is overwritten by its parent and the table ends up holding the outer
        // node. Registering before the children would leave the table holding the
        // child, and every later mention of the cycle would find the wrong object.
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

            // **Which node came back, not merely that one did.** If the probe were moved
            // below child construction the child would register slot 7 first, the outer
            // probe would then find *the child* and return it — so `table[7]` and the
            // return value would still be one and the same object, and a pointer
            // comparison alone would stay green while the walk returned the wrong node.
            // Naming them is what separates the two.
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
