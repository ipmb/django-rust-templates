//! The primitive resolver, ported from `Variable._resolve_lookup`.
//!
//! The **loop** lives here rather than in the host, so arm order and swallowed-exception
//! sets are testable in a bare shell. The host supplies only what Python knows (a `dir()`,
//! `__getitem__` presence, callability, the lookups) and no `repr`.

use crate::parser::desc::NodeRef;
use crate::pynum::{python_int, IntValue};

/// What one lookup arm did.
///
/// Three-way rather than `Option`, shared by all three arms: Django's distinction is the
/// same in each — a swallowed exception falls through, a *returned* `None` is a successful
/// lookup, anything else leaves the inner `try`.
pub enum Step {
    /// The arm found the next container.
    Found(Box<dyn LookupTarget>),
    /// An exception this arm swallows (`base.py:968`, `base.py:977`, `base.py:983-988`). The
    /// host must still **stash** it: on the attribute arm it may be the one Django re-raises,
    /// because that is where the `dir()` rule lives.
    Miss,
    /// An exception this arm does not swallow — Django's outer `except Exception`
    /// (`base.py:1014`), reached without consulting `dir()`, which is
    /// [`LookupOutcome::Escaped`], not a decision this crate can make. Measured: a
    /// `RuntimeError` from `__getattr__`, a `@property` or `__getitem__` propagates.
    Raised,
}

/// Written by hand because `Step::Found` holds a `dyn LookupTarget`, which is not
/// `Debug` and must not be forced to be — requiring it would spread a formatting
/// obligation through the PyO3 side. `Found` prints the handle, all this crate can read.
impl std::fmt::Debug for Step {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Step::Found(found) => write!(f, "Found({:?})", found.node()),
            Step::Miss => f.write_str("Miss"),
            Step::Raised => f.write_str("Raised"),
        }
    }
}

/// Which branch of Django's callable arm a value takes.
///
/// Three-way rather than a boolean, because the three branches do three different things
/// (`base.py:993-1013`). Measured: a plain lambda renders `'called'` (Call);
/// `do_not_call_in_templates = True` renders the function's repr (DoNotCall);
/// `alters_data = True` renders `''` (AltersData).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallArm {
    /// `current = current()` — Django calls the value.
    Call,
    /// `do_not_call_in_templates` is set: Django does nothing, leaving the callable as
    /// the resolved value.
    DoNotCall,
    /// `alters_data` is set: Django replaces the value with
    /// `context.template.engine.string_if_invalid` — neither a call nor a raise.
    AltersData,
}

/// Why a callable could not be called.
///
/// Django's arm has three outcomes (`base.py:1000-1013`): `signature()` raising `ValueError`
/// and `bind()` raising `TypeError` both substitute `string_if_invalid`, while everything
/// else is the bare `raise` at `base.py:1013`. Measured: a zero-arg callable whose body
/// raises `TypeError` **propagates**, so folding `Raised` into `ArgsRequired` would swallow
/// a real error.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallFailure {
    /// `inspect.signature` raised `ValueError` — Django's "No signature found".
    NoSignature,
    /// `signature(...).bind()` raised `TypeError` — arguments were required.
    ArgsRequired,
    /// Anything else. The host holds the exception by identity; it is Django's `raise`
    /// at `base.py:1013`, whose outer handler the host decides, as for
    /// [`LookupOutcome::Escaped`].
    Raised,
}

/// The host's read of `context.template.engine.string_if_invalid` raised, and the host
/// has stashed the exception.
///
/// A one-variant type rather than `()`, which reads as "nothing to do"; this means
/// "something escaped and the host is holding it", like [`LookupOutcome::Escaped`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PendingFailure;

/// What the resolver needs from a context, supplied by the host.
///
/// The split is the point: **three attempt a lookup** — `get_item`, `get_attr`, `get_index`
/// *are* Django's three tries and return a [`Step`]; **four are predicates read around those
/// attempts** — `has_getitem`, `context_probes_attribute`, `is_context`, `in_dir`; **three
/// are the callable arm's outcomes** — `call_arm`, `call`, `string_if_invalid`; the last is
/// `node`.
///
/// There is no `repr` by decision: Django formats the missing-key message lazily in
/// `VariableDoesNotExist.__str__`, so the host hands back the container's **handle**. See
/// [`LookupOutcome::Missing`].
pub trait LookupTarget {
    /// `current[bit]`, attempted only when `has_getitem()`. A miss is Django's
    /// `TypeError, AttributeError, KeyError, ValueError, IndexError`; else [`Step::Raised`].
    fn get_item(&self, bit: &str) -> Step;

    /// `hasattr(type(current), "__getitem__")` — the guard on the dictionary
    /// arm, which is why a scalar skips straight to `getattr`.
    fn has_getitem(&self) -> bool;

    /// `getattr(current, bit)` — see [`Step`]. A **swallowed** exception is
    /// [`Step::Miss`], and the loop then consults [`Self::in_dir`] before re-raising.
    fn get_attr(&self, bit: &str) -> Step;

    /// Whether Django reads `current`'s **instance** attribute for `bit`; for a
    /// `BaseContext` that happens in exactly one case: the class attribute exists and is
    /// falsy (`base.py:972-976`).
    ///
    /// Not "the class attribute is set": Django's `getattr(type(current), bit)` raises its own
    /// `AttributeError` when the class attribute is truthy, and when `getattr` itself raises —
    /// the attribute is absent — that same `AttributeError` is caught one frame out
    /// (`base.py:977`). So for a context, absent and truthy both suppress the arm; only
    /// *present and falsy* lets it through.
    fn context_probes_attribute(&self, bit: &str) -> bool;

    /// `isinstance(current, BaseContext)` — part of the `dir()` rule and of the
    /// attribute-arm suppression, so it is read in two places.
    fn is_context(&self) -> bool;

    /// `bit in dir(current)` — consulted **only** for a swallowed attribute-arm exception
    /// (`base.py:979-980`), never for [`Step::Raised`], whose measured rows propagate
    /// whatever `dir()` says.
    fn in_dir(&self, bit: &str) -> bool;

    /// `current[int(bit)]` — the list-index arm. Takes [`IntValue`] rather than `i64`
    /// because Python's `int()` is unbounded and Task 3 already parses Python's grammar;
    /// re-parsing with Rust's would admit `5_0` and reject `٥`.
    fn get_index(&self, index: &IntValue) -> Step;

    /// Which branch of Django's callable arm `current` takes, or `None` when it does not
    /// run because `callable(current)` is false. The host folds all three of Django's
    /// tests in, because only it can read the flags and doing so here would need a second
    /// round trip.
    fn call_arm(&self) -> Option<CallArm>;

    /// Call the resolved callable and hand back the result as a new target.
    ///
    /// Django calls `current()` assuming no arguments (`base.py:1000`), then uses
    /// `signature()`/`bind()` to decide whether a `TypeError` meant "arguments required"
    /// or "the body raised". The host owns all three, and the exception must survive
    /// **by identity**.
    fn call(&self) -> Result<Box<dyn LookupTarget>, CallFailure>;

    /// `context.template.engine.string_if_invalid`, read **at the moment a substitution
    /// happens** — Django's three assignment sites (`base.py:997`, `base.py:1005`,
    /// `base.py:1011`) each re-read it, all inside the outer `try` at `base.py:958`.
    ///
    /// Measured (`m8_measure_invalid.py` §2): **lazy** — with no template bound the read
    /// raises `AttributeError`, so a port that fetched the invalid value up front would raise
    /// on the most ordinary template; and **fallible, the failure propagating** — the read is
    /// inside the outer `try`, so its `AttributeError` reaches the handler at `base.py:1014`.
    /// `Err` means the host has stashed that exception, and the caller returns
    /// [`LookupOutcome::Escaped`].
    fn string_if_invalid(&self) -> Result<Box<dyn LookupTarget>, PendingFailure>;

    /// The handle this container *is*. `Found` names the value the loop ended on;
    /// `Missing` names the container it failed **in**, which is the object Django puts in
    /// `VariableDoesNotExist.params`.
    fn node(&self) -> NodeRef;
}

/// What one path resolved to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LookupOutcome {
    /// The path resolved. The host holds the value behind this handle.
    Found(NodeRef),
    /// `VariableDoesNotExist`, as Django passes it: the failing `bit` and the *container
    /// object* the lookup failed in. The message is not built here — `__str__` does the
    /// `%`, so `repr(container)` is never called unless someone stringifies it.
    Missing { bit: String, container: NodeRef },
    /// An exception the host holds **by identity** escaped the arms and reached Django's
    /// outer `except Exception` (`base.py:1014`). This crate only decides *that*; the host
    /// decides what the handler does, because both of its branches need the object: the
    /// handler logs, then substitutes `string_if_invalid` when the exception carries
    /// `silent_variable_failure` and otherwise re-raises (`base.py:1023-1026`).
    ///
    /// **The host must hand back the most recent failure**, stashed on the way out, and
    /// **`bit` is carried because the handler's first act is to log it** (`base.py:1016-1021`).
    Escaped { bit: String },
    /// Something M3 deliberately does not do. Loud rather than wrong (D3).
    Unimplemented(String),
}

/// Resolve a dotted path, in Django's arm order (`base.py:959-1028`).
pub fn resolve_lookup(lookups: &[String], mut current: Box<dyn LookupTarget>) -> LookupOutcome {
    for bit in lookups {
        current = match step_one(&*current, bit) {
            Ok(found) => found,
            Err(outcome) => return outcome,
        };

        // Django's callable arm is **inside the loop**, one bit at a time (`base.py:993`).
        // Measured: `{{ f.b }}` with `f = lambda: {"b": "B"}` renders `'B'`, because the
        // call happens after the first bit; a check after the loop would look up `b` on `f`.
        match current.call_arm() {
            // Not callable, or Django's `pass`: the value stands and the next bit looks
            // into it. `DoNotCall` needs no call, so it is implemented rather than deferred.
            None | Some(CallArm::DoNotCall) => {}
            // M8 owns the call. The alternative — returning the callable —
            // renders `<function …>`, which is wrong and quiet (D3).
            Some(CallArm::Call) => {
                current = match current.call() {
                    Ok(called) => called,
                    // Django assigns `string_if_invalid` and the loop goes on to the next
                    // bit with it (`base.py:997`, inside `for bit in …`).
                    Err(CallFailure::NoSignature | CallFailure::ArgsRequired) => {
                        match current.string_if_invalid() {
                            Ok(invalid) => invalid,
                            Err(PendingFailure) => {
                                return LookupOutcome::Escaped { bit: bit.clone() }
                            }
                        }
                    }
                    // Not a substitution: `base.py:1013` re-raises into the outer handler
                    // (`base.py:1014`), whose branches need the exception object, as for `Escaped`.
                    Err(CallFailure::Raised) => return LookupOutcome::Escaped { bit: bit.clone() },
                }
            }
            // Not a call and not a raise: Django replaces the value (`base.py:997`), and the
            // assignment is what makes the next bit resolve against the substituted value —
            // measured, `{{ x.upper }}` with `x.alters_data = True` renders the *string's*
            // `upper`. The context reaches the arm through the host (`string_if_invalid`).
            Some(CallArm::AltersData) => match current.string_if_invalid() {
                Ok(invalid) => current = invalid,
                // The read raised inside Django's outer `try` (`base.py:958`), so its
                // `AttributeError` reaches the handler at `base.py:1014`. The host applies it.
                Err(PendingFailure) => return LookupOutcome::Escaped { bit: bit.clone() },
            },
        }
    }

    LookupOutcome::Found(current.node())
}

/// One bit, through Django's three arms in order (`base.py:960-992`).
///
/// **Where Django's `dir()` rule lives** is the part worth writing down: not on "an exception
/// the lookup raised" but on the attribute arm's *swallowed* exception, and nowhere else. The
/// inner `except (TypeError, AttributeError)` guards **only** `getattr`, and inside it
/// `bit in dir(current)` is consulted, with the `BaseContext` exclusion. So a `ValueError` from
/// a `@property` never reaches the `dir()` line, while an `AttributeError` from the same
/// property is re-raised *because* its name is in `dir()`. Both re-raises land on the same
/// handler, hence one variant ([`LookupOutcome::Escaped`]). Measured, all four rows:
///
/// ```text
/// __getattr__ raises RuntimeError    not in dir -> RAISED RuntimeError
/// __getattr__ raises ValueError      not in dir -> RAISED ValueError
/// __getattr__ raises AttributeError  not in dir -> rendered ''
/// @property  raises AttributeError   in dir     -> RAISED AttributeError
/// ```
fn step_one(current: &dyn LookupTarget, bit: &str) -> Result<Box<dyn LookupTarget>, LookupOutcome> {
    // Every arm fails the same way and names the one bit being looked up (this function's
    // argument, never the caller's loop variable). Built once here.
    let escaped = || LookupOutcome::Escaped {
        bit: bit.to_string(),
    };

    // 1. Dictionary arm, guarded by `__getitem__`'s presence. Django raises a `TypeError` for
    //    the guard rather than skipping, and that `TypeError` is the arm's own first swallow —
    //    so the guard and the arm are one arm, and a scalar reaches `getattr`.
    if current.has_getitem() {
        match current.get_item(bit) {
            Step::Found(found) => return Ok(found),
            // A swallowed miss falls through to the attribute arm.
            Step::Miss => {}
            Step::Raised => return Err(escaped()),
        }
    }

    // 2. Attribute arm, with the `BaseContext` suppression. The suppression raises an
    //    `AttributeError` *inside* the inner `try`, so it is swallowed and survives the `dir()`
    //    line (a context is excluded there) — a suppressed attribute skips to the index arm,
    //    which is what the guard reproduces. An *absent* class attribute suppresses it too.
    if !(current.is_context() && !current.context_probes_attribute(bit)) {
        match current.get_attr(bit) {
            Step::Found(found) => return Ok(found),
            Step::Miss => {
                if !current.is_context() && current.in_dir(bit) {
                    return Err(escaped());
                }
            }
            Step::Raised => return Err(escaped()),
        }
    }

    // 3. List-index arm. `int(bit)` failing is the `ValueError` this arm swallows, so a
    //    non-numeric bit is a miss rather than a panic.
    if let Some(index) = python_int(bit) {
        match current.get_index(&index) {
            Step::Found(found) => return Ok(found),
            Step::Miss => {}
            Step::Raised => return Err(escaped()),
        }
    }

    Err(missing(bit, current))
}

fn missing(bit: &str, current: &dyn LookupTarget) -> LookupOutcome {
    LookupOutcome::Missing {
        bit: bit.to_string(),
        container: current.node(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lookups(bits: &[&str]) -> Vec<String> {
        bits.iter().map(|b| b.to_string()).collect()
    }

    use std::fmt;

    /// The host, standing in. One field per trait method Python would answer, so no arm
    /// needs a real `Context`. `Debug` is hand-written (see the impl below), because
    /// rustc's `dead_code` lint ignores derived impls, so a derived `Debug` under
    /// `-D warnings` would reject `label`.
    #[derive(Clone)]
    struct Fake {
        node: NodeRef,
        /// Read by `Debug` and by nothing else, so a failure prints
        /// `Fake(NodeRef(1) @ "'A'")` instead of a bare index.
        label: &'static str,
        map: Option<Vec<(&'static str, Fake)>>,
        seq: Vec<Fake>,
        attrs: Vec<(&'static str, Fake)>,
        /// Names whose `getattr` raises in Django's attr-arm swallowing set
        /// (`TypeError`/`AttributeError`, i.e. a `@property` or a missing name).
        swallowed: Vec<&'static str>,
        /// Names whose `getattr` raises anything else; those leave the inner `try` and are
        /// re-raised, never reaching the `dir()` line.
        unswallowable: Vec<&'static str>,
        /// Whether `__getitem__` raises outside its swallowing set — the item arm's route
        /// to [`Step::Raised`] (measured to propagate whatever `dir()` says).
        item_unswallowable: bool,
        /// Names in `dir(self)`. For an object with `__getattr__`, `None`.
        dir: Vec<&'static str>,
        /// Class attributes that exist **and are falsy** — the only case a `BaseContext`
        /// reads the instance attribute. Absent and truthy are the same answer here: both
        /// suppress the arm.
        falsy_class_attrs: Vec<&'static str>,
        is_context: bool,
        call_arm: Option<CallArm>,
        /// What `call()` answers when the fixture drives the callable arm; the `Fake` inside
        /// is the called value, **boxed** because `Option<Result<Fake, _>>` contains itself.
        call: Option<Result<Box<Fake>, CallFailure>>,
        /// What `string_if_invalid()` answers, when the fixture drives a substitution.
        /// `Err` is the host's stashed-exception case.
        invalid: Option<Result<Box<Fake>, PendingFailure>>,
    }

    impl Fake {
        /// A leaf: no `[]`, no attributes.
        fn scalar(node: u64, label: &'static str) -> Self {
            Self {
                node: NodeRef(node),
                label,
                map: None,
                seq: Vec::new(),
                attrs: Vec::new(),
                swallowed: Vec::new(),
                unswallowable: Vec::new(),
                item_unswallowable: false,
                dir: Vec::new(),
                falsy_class_attrs: Vec::new(),
                is_context: false,
                call_arm: None,
                call: None,
                invalid: None,
            }
        }

        /// A `BaseContext`-like container: attribute lookup reaches the instance
        /// only for the names in `falsy_class_attrs`.
        fn context(node: u64, label: &'static str) -> Self {
            Self {
                is_context: true,
                ..Self::scalar(node, label)
            }
        }
    }

    /// Hand-written: the only reader of `label`, and `dead_code` ignores derived impls.
    impl fmt::Debug for Fake {
        fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            write!(f, "Fake({:?} @ {})", self.node, self.label)
        }
    }

    impl LookupTarget for Fake {
        fn get_item(&self, bit: &str) -> Step {
            let Some(map) = self.map.as_ref() else {
                return Step::Miss;
            };
            if self.item_unswallowable {
                return Step::Raised;
            }
            match map.iter().find(|(name, _)| *name == bit) {
                Some((_, found)) => Step::Found(Box::new(found.clone())),
                None => Step::Miss,
            }
        }

        fn has_getitem(&self) -> bool {
            self.map.is_some()
        }

        fn get_attr(&self, bit: &str) -> Step {
            if self.unswallowable.contains(&bit) {
                return Step::Raised;
            }
            if self.swallowed.contains(&bit) {
                return Step::Miss;
            }
            match self.attrs.iter().find(|(name, _)| *name == bit) {
                Some((_, found)) => Step::Found(Box::new(found.clone())),
                None => Step::Miss,
            }
        }

        fn context_probes_attribute(&self, bit: &str) -> bool {
            self.falsy_class_attrs.contains(&bit)
        }

        fn is_context(&self) -> bool {
            self.is_context
        }

        fn in_dir(&self, bit: &str) -> bool {
            self.dir.contains(&bit)
        }

        fn get_index(&self, index: &IntValue) -> Step {
            // A `Big` index is a `TypeError` on a Python list, which this arm swallows — a miss, not a panic.
            let IntValue::Small(index) = index else {
                return Step::Miss;
            };
            let Ok(index) = usize::try_from(*index) else {
                return Step::Miss;
            };
            match self.seq.get(index) {
                Some(found) => Step::Found(Box::new(found.clone())),
                None => Step::Miss,
            }
        }

        fn call_arm(&self) -> Option<CallArm> {
            self.call_arm
        }

        fn call(&self) -> Result<Box<dyn LookupTarget>, CallFailure> {
            match self.call.as_ref() {
                // `Box<Fake>` unsizes to `Box<dyn LookupTarget>` in return position; the clone is the whole conversion.
                Some(Ok(called)) => Ok(called.clone()),
                Some(Err(failure)) => Err(*failure),
                None => panic!("this fixture does not drive the callable arm"),
            }
        }

        fn string_if_invalid(&self) -> Result<Box<dyn LookupTarget>, PendingFailure> {
            match self.invalid.as_ref() {
                Some(Ok(invalid)) => Ok(invalid.clone()),
                Some(Err(failure)) => Err(*failure),
                None => panic!("this fixture does not drive the substitution"),
            }
        }

        fn node(&self) -> NodeRef {
            self.node
        }
    }

    #[test]
    fn the_measured_arms() {
        // Dictionary arm. The container the loop ends on is what `Found` names.
        let mapping = Fake {
            map: Some(vec![("a", Fake::scalar(1, "'A'"))]),
            ..Fake::scalar(99, "{'a': 'A'}")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["a"]), Box::new(mapping)),
            LookupOutcome::Found(NodeRef(1)),
        );

        // Missing: the failing bit and the **container's handle** (Django puts `current` in
        // `VariableDoesNotExist.params`, not the child); no repr, which `__str__` builds later if ever.
        let mapping = Fake {
            map: Some(vec![("a", Fake::scalar(1, "'A'"))]),
            ..Fake::scalar(99, "{'a': 'A'}")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["b"]), Box::new(mapping)),
            LookupOutcome::Missing {
                bit: "b".to_string(),
                container: NodeRef(99),
            },
        );

        // List-index arm: `current[int("1")]`, through Task 3's `python_int`.
        let seq = Fake {
            seq: vec![Fake::scalar(10, "10"), Fake::scalar(20, "20")],
            ..Fake::scalar(99, "[10, 20]")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["1"]), Box::new(seq)),
            LookupOutcome::Found(NodeRef(20)),
        );

        // A bit that is not an integer never reaches the index arm: Django's
        // `int(bit)` raises `ValueError`, which that arm swallows.
        let seq = Fake {
            seq: vec![Fake::scalar(10, "10")],
            ..Fake::scalar(99, "[10]")
        };
        assert!(matches!(
            resolve_lookup(&lookups(&["x"]), Box::new(seq)),
            LookupOutcome::Missing { .. },
        ));
    }

    #[test]
    fn the_dir_rule_lives_on_the_attribute_arms_swallowed_exception() {
        // Rows 1–2 share the attr arm and differ only in `dir()`; row 3 is a different arm.
        //
        // `@property` raising AttributeError, name in `dir()` → Django re-raises on the
        // `dir()` guard (`base.py:979-980`). The fixture gives `Step::Miss` (the exception is
        // inside the arm's swallowing set), so a fixture modelled as `Raised` would pass
        // against a resolver with the rule in the wrong place.
        let property_in_dir = Fake {
            swallowed: vec!["boom"],
            dir: vec!["boom"],
            ..Fake::scalar(99, "<A object>")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["boom"]), Box::new(property_in_dir)),
            LookupOutcome::Escaped {
                bit: "boom".to_string()
            }
        );

        // `__getattr__` raising AttributeError, name not in `dir()` → index arm, `int("boom")`
        // fails → `VariableDoesNotExist`.
        let getattr_not_in_dir = Fake {
            swallowed: vec!["boom"],
            dir: vec![],
            ..Fake::scalar(99, "<A object>")
        };
        assert!(matches!(
            resolve_lookup(&lookups(&["boom"]), Box::new(getattr_not_in_dir)),
            LookupOutcome::Missing { .. },
        ));

        // A *non-swallowed* exception propagates **without** consulting `dir()`. Both `dir()`
        // answers are asserted: a resolver that ran the `dir()` check on every failed attribute
        // lookup would report a missing key where Django raises.
        for dir in [vec!["boom"], vec![]] {
            let runtime = Fake {
                unswallowable: vec!["boom"],
                dir,
                ..Fake::scalar(99, "<A object>")
            };
            assert_eq!(
                resolve_lookup(&lookups(&["boom"]), Box::new(runtime)),
                LookupOutcome::Escaped {
                    bit: "boom".to_string()
                }
            );
        }

        // ... and the item arm's failures never reach the `dir()` line: a `RuntimeError` from
        // `__getitem__` leaves Django's inner `try` immediately. Both `dir()` answers asserted.
        for dir in [vec!["boom"], vec![]] {
            let item = Fake {
                map: Some(vec![]),
                item_unswallowable: true,
                dir,
                ..Fake::scalar(99, "<A object>")
            };
            assert_eq!(
                resolve_lookup(&lookups(&["boom"]), Box::new(item)),
                LookupOutcome::Escaped {
                    bit: "boom".to_string()
                }
            );
        }

        // A context never re-raises a swallowed attribute exception, even with the name in
        // `dir()`: `is_context()` is half of the measured predicate (`base.py:972`).
        let suppressed = Fake {
            swallowed: vec!["boom"],
            dir: vec!["boom"],
            ..Fake::context(99, "<Context>")
        };
        assert!(matches!(
            resolve_lookup(&lookups(&["boom"]), Box::new(suppressed)),
            LookupOutcome::Missing { .. },
        ));
    }

    #[test]
    fn the_property_rule_applies_to_a_context_only_as_djangos_condition_reads_it() {
        // Measured: `Context().template` is an *instance* attribute with no class attribute, so
        // `{{ c.template }}` renders `''` — Django's `getattr(type(current), bit)` raises before
        // the instance is read, and that AttributeError is caught a frame out (`base.py:977`).
        let absent_class_attr = Fake {
            attrs: vec![("template", Fake::scalar(1, "<Engine>"))],
            ..Fake::context(0, "<Context>")
        };
        assert!(matches!(
            resolve_lookup(&lookups(&["template"]), Box::new(absent_class_attr)),
            LookupOutcome::Missing { .. },
        ));

        // Measured on a `Context` subclass with class `zero = 0` and instance `zero = 7`: a
        // *present and falsy* class attribute lets the instance attribute through, so
        // `{{ c.zero }}` is `7`. A predicate of "the class attribute is set" suppresses it too.
        let falsy_class_attr = Fake {
            attrs: vec![("zero", Fake::scalar(1, "7"))],
            falsy_class_attrs: vec!["zero"],
            ..Fake::context(0, "<FalsyCtx>")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["zero"]), Box::new(falsy_class_attr)),
            LookupOutcome::Found(NodeRef(1)),
        );
    }

    #[test]
    fn the_callable_arm_runs_after_every_bit_not_once_at_the_end() {
        // Measured: `{{ f.b }}` with `f = lambda: {"b": "B"}` renders `'B'`. The callable is bit
        // 1's *result*, so a resolver that checked callability only after the loop would look up
        // `b` on the function.
        let called = Fake {
            map: Some(vec![("b", Fake::scalar(2, "'B'"))]),
            ..Fake::scalar(1, "{'b': 'B'}")
        };
        let mid_path = Fake {
            map: Some(vec![(
                "f",
                Fake {
                    call_arm: Some(CallArm::Call),
                    call: Some(Ok(Box::new(called))),
                    ..Fake::scalar(1, "f")
                },
            )]),
            ..Fake::scalar(99, "{'f': f}")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["f", "b"]), Box::new(mid_path)),
            LookupOutcome::Found(NodeRef(2)),
        );
    }

    #[test]
    fn a_substitution_that_cannot_read_the_context_escapes() {
        // The `Call` arm's args-required branch: Django assigns `string_if_invalid` and
        // **stays in the loop** (`base.py:997`), so the next bit resolves against the invalid
        // value. Here the read itself fails (the host's stashed-exception case) and the arm's
        // contract is `Escaped`, not a silent `""` (measured; `m8_measure_invalid.py` §2).
        let needs_an_arg = Fake {
            call_arm: Some(CallArm::Call),
            call: Some(Err(CallFailure::ArgsRequired)),
            invalid: Some(Err(PendingFailure)),
            ..Fake::scalar(3, "f")
        };
        let holder = Fake {
            attrs: vec![("f", needs_an_arg)],
            ..Fake::scalar(99, "{'f': f}")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["f"]), Box::new(holder)),
            LookupOutcome::Escaped {
                bit: "f".to_string()
            },
        );
    }

    #[test]
    fn a_substitution_continues_the_loop_with_the_invalid_value() {
        // `string_if_invalid('<<invalid>>')` is assigned and the loop goes on, so `{{ x.upper }}`
        // resolves `.upper` *on the invalid string*. The second row is the discriminator: a port
        // that substituted and stopped would end on the invalid value's own handle.
        let needs_an_arg = Fake {
            call_arm: Some(CallArm::Call),
            call: Some(Err(CallFailure::ArgsRequired)),
            invalid: Some(Ok(Box::new(Fake::scalar(4, "<<invalid>>")))),
            ..Fake::scalar(3, "f")
        };
        let holder = Fake {
            attrs: vec![("f", needs_an_arg)],
            ..Fake::scalar(99, "{'f': f}")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["f"]), Box::new(holder)),
            LookupOutcome::Found(NodeRef(4)),
        );

        // And with a `dir`-answered second bit on the invalid value, the loop really did
        // continue: `.upper` is read off the substituted target.
        let invalid_with_upper = Fake {
            attrs: vec![("upper", Fake::scalar(5, "<<INVALID>>"))],
            ..Fake::scalar(4, "<<invalid>>")
        };
        let holder = Fake {
            attrs: vec![(
                "f",
                Fake {
                    call_arm: Some(CallArm::Call),
                    call: Some(Err(CallFailure::ArgsRequired)),
                    invalid: Some(Ok(Box::new(invalid_with_upper))),
                    ..Fake::scalar(3, "f")
                },
            )],
            ..Fake::scalar(99, "{'f': f}")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["f", "upper"]), Box::new(holder)),
            LookupOutcome::Found(NodeRef(5)),
        );
    }

    #[test]
    fn a_do_not_call_value_is_returned_rather_than_deferred() {
        // Measured: `{{ f }}` with `f.do_not_call_in_templates = True` renders the function's
        // repr — Django's branch is `pass`, so the callable is the resolved value.
        let value = Fake {
            call_arm: Some(CallArm::DoNotCall),
            ..Fake::scalar(7, "<function f>")
        };
        let holder = Fake {
            attrs: vec![("f", value)],
            ..Fake::scalar(99, "{'f': f}")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["f"]), Box::new(holder)),
            LookupOutcome::Found(NodeRef(7)),
        );
    }

    #[test]
    fn the_alters_data_substitution_replaces_the_value() {
        // Measured: `{{ o.run }}` with `o.run.alters_data = True` renders `string_if_invalid` —
        // not a call, and not a raise.
        //
        // In-crate this can only show the substitution *took effect* (the double has no Python,
        // so no `str.upper`); that the loop continues is the Python row's job, in
        // `tests/test_resolve_invalid.py`.
        let alters = Fake {
            call_arm: Some(CallArm::AltersData),
            invalid: Some(Ok(Box::new(Fake::scalar(4, "<<invalid>>")))),
            ..Fake::scalar(3, "<bound method>")
        };
        let holder = Fake {
            attrs: vec![("x", alters)],
            ..Fake::scalar(99, "<Obj object>")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["x"]), Box::new(holder)),
            LookupOutcome::Found(NodeRef(4)),
        );
    }

    #[test]
    fn a_failed_invalid_read_escapes_instead_of_substituting() {
        // The read is inside Django's outer `try` (`base.py:958`), so its failure is the
        // handler's problem at `base.py:1014` — the same variant as an arm that raised.
        let alters = Fake {
            call_arm: Some(CallArm::AltersData),
            invalid: Some(Err(PendingFailure)),
            ..Fake::scalar(3, "<bound method>")
        };
        let holder = Fake {
            attrs: vec![("x", alters)],
            ..Fake::scalar(99, "<Obj object>")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["x"]), Box::new(holder)),
            LookupOutcome::Escaped {
                bit: "x".to_string()
            },
        );
    }
}
