//! The primitive resolver, ported from `Variable._resolve_lookup`.
//!
//! The **loop** lives here rather than in the host, so the arm order and the
//! swallowed-exception sets are testable in a bare shell. The host supplies only
//! what Python knows: a `dir()`, `__getitem__` presence, class-attribute
//! truthiness, callability, and the lookups themselves. It supplies no `repr`:
//! Django formats the missing-key message lazily, in `__str__`.
//!
//! Two placements in here are not obvious from reading `base.py` once, and both
//! were measured rather than reasoned about; the comments at [`step_one`] and in
//! [`resolve_lookup`]'s callable arm carry the transcripts.

use crate::parser::desc::NodeRef;
use crate::pynum::{python_int, IntValue};

/// What one lookup arm did.
///
/// Three-way rather than `Option`, and **shared by all three arms**, because
/// Django distinguishes the cases and the distinction is the same in each: a
/// swallowed exception falls through to the next arm, a *returned* `None` is a
/// successful lookup, and any other exception leaves the inner `try` entirely.
/// `get_item` and `get_index` return this too, because a `__getitem__` that
/// raises something outside Django's five is not reachable through `Option` at
/// all.
///
/// What the loop does with each variant is **not** the same in every arm, and
/// that asymmetry is Django's, not a shortcut here: see [`step_one`].
pub enum Step {
    /// The arm found the next container.
    Found(Box<dyn LookupTarget>),
    /// The arm's exception is one this arm swallows — `TypeError,
    /// AttributeError, KeyError, ValueError, IndexError` on the item arm,
    /// `TypeError, AttributeError` on the attribute arm, `IndexError,
    /// ValueError, KeyError, TypeError` on the index arm (all three read off
    /// `base.py:968`, `base.py:977` and `base.py:983-988`).
    ///
    /// The host must still have **stashed** the exception: on the attribute arm
    /// a swallowed exception can be the one Django re-raises, because that is
    /// where the `dir()` rule lives.
    Miss,
    /// An exception this arm does not swallow. It leaves Django's inner `try`
    /// for the outer `except Exception` — the one Django comments *catch-all for
    /// silent variable failures* (`base.py:1014`) — without consulting `dir()`,
    /// and what happens there is [`LookupOutcome::Escaped`], not a decision this
    /// crate can make. Measured: a `RuntimeError` raised by `__getattr__`, by a
    /// `@property`, or by `__getitem__` propagates even though the name is not in
    /// `dir()`.
    Raised,
}

/// Written by hand because `Step::Found` holds a `dyn LookupTarget`, which is
/// not `Debug` and must not be forced to be: the trait exists so the host can
/// answer predicates about a Python object, and requiring `Debug` of every host
/// would spread a formatting obligation through the PyO3 side for the sake of a
/// derive. The `Found` arm prints the handle instead of the container, which is
/// the only part of it this crate can read anyway.
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
/// Three-way rather than the "is it callable" boolean the plan first specified,
/// because the three branches do three different things (`base.py:993-1013`) and
/// a boolean folds two of them into a behaviour Django never produces. All three
/// rows were measured:
///
/// ```text
/// f            = lambda: 'called'   ->  rendered 'called'          (Call)
/// f.do_not_call_in_templates = True ->  rendered '<function …>'    (DoNotCall)
/// o.run.alters_data = True          ->  rendered ''                (AltersData)
/// ```
///
/// `DoNotCall` is the one M3 can honour for free — Django's branch is `pass`, so
/// the callable stays in place and the *next* bit looks into it — and honouring
/// it is strictly better than raising `Unimplemented`, because the correct
/// answer needs no call. The other two are M8's; see [`resolve_lookup`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallArm {
    /// `current = current()` — Django calls the value.
    Call,
    /// `do_not_call_in_templates` is set: Django does nothing, leaving the
    /// callable as the resolved value.
    DoNotCall,
    /// `alters_data` is set: Django replaces the value with
    /// `context.template.engine.string_if_invalid` — neither a call nor a
    /// raise, and it needs a `Context` this resolver does not have.
    AltersData,
}

/// Why a callable could not be called.
///
/// Three outcomes, because Django's arm has three (`base.py:1000-1013`) and
/// they are not two: `signature()` raising `ValueError` and `bind()` raising
/// `TypeError` both substitute `string_if_invalid`, while everything else is the
/// bare `raise` at `base.py:1013`, which leaves this arm entirely and reaches the
/// outer handler at `base.py:1014`. Measured: a zero-arg callable whose body raises
/// `TypeError` **propagates** — both of the first two checks succeed for it — so
/// folding `Raised` into `ArgsRequired` would swallow a real error.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallFailure {
    /// `inspect.signature` raised `ValueError` — Django's "No signature found".
    NoSignature,
    /// `signature(...).bind()` raised `TypeError` — arguments were required.
    ArgsRequired,
    /// Anything else. The host holds the exception by identity; it is Django's
    /// `raise` at `base.py:1013`, and the host decides between the outer
    /// handler's two branches, exactly as it does for [`LookupOutcome::Escaped`].
    Raised,
}

/// The host's read of `context.template.engine.string_if_invalid` raised, and
/// the host has stashed the exception.
///
/// A one-variant type rather than `()` — `Err(())` at a call site reads as
/// "nothing to do", while this means "something escaped and the host is holding
/// it", which is [`LookupOutcome::Escaped`]'s contract. Not folded into
/// [`CallFailure`] because it is not a call failure; not a `bool` because "no
/// invalid value is configured" is not a state Django has (its default is `""`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PendingFailure;

/// What the resolver needs from a context, supplied by the host.
///
/// Eleven methods, and the split is the point. **Three attempt a lookup** —
/// `get_item`, `get_attr` and `get_index` *are* Django's `current[bit]`,
/// `getattr(current, bit)` and `current[int(bit)]`, in that order, and each
/// returns a [`Step`] rather than a bool because the attempt has already
/// happened. **Four are predicates read around those attempts** —
/// `has_getitem`, `context_probes_attribute`, `is_context`, `in_dir` — and
/// collapsing any two of those merges two of Django's arms, which is wrong in a
/// case this milestone measured. **Three are the callable arm's outcomes** —
/// `call_arm`, `call` and `string_if_invalid` — which are not predicates but
/// the three things Django's arm can do to the value. The last is `node`, the
/// handle the loop ended on.
///
/// The count is written down because it went stale: M8 added `call` and
/// `string_if_invalid` in Tasks 1 and 2 and this line still said "nine" until
/// the close-out read it, which is the same failure as a name in a code block
/// that outlives its referent. **The split then went wrong in that same edit**:
/// the first fix called all seven non-callable methods predicates, which loses
/// the one distinction the trait exists to carry — which of Django's three
/// tries a method is — and it survived a read-through because seven is a
/// number. Corrected by review, checked against `base.py:969-992`.
///
/// There is no `repr` here, and its absence is a decision: Django formats the
/// missing-key message lazily in `VariableDoesNotExist.__str__`, so the host
/// hands back the container's **handle** and Python does the rest. See
/// [`LookupOutcome::Missing`].
pub trait LookupTarget {
    /// `current[bit]`, attempted only when `has_getitem()`. A miss is any of
    /// Django's `TypeError, AttributeError, KeyError, ValueError, IndexError`;
    /// anything else is [`Step::Raised`].
    fn get_item(&self, bit: &str) -> Step;

    /// `hasattr(type(current), "__getitem__")` — the guard on the dictionary
    /// arm, which is why a scalar skips straight to `getattr`.
    fn has_getitem(&self) -> bool;

    /// `getattr(current, bit)` — see [`Step`]. A **swallowed** exception here is
    /// [`Step::Miss`], and the loop then consults [`Self::in_dir`] before
    /// deciding between a re-raise and the index arm.
    fn get_attr(&self, bit: &str) -> Step;

    /// Whether Django reads `current`'s **instance** attribute for `bit`, which
    /// for a `BaseContext` happens in exactly one case: the class attribute
    /// exists and is falsy (`base.py:972-976`).
    ///
    /// The predicate is not "the class attribute is set". Django's condition is
    /// `isinstance(current, BaseContext) and getattr(type(current), bit)`, and it
    /// raises its own `AttributeError` when that is truthy; when `getattr` itself
    /// raises — the attribute is *absent* — that same `AttributeError` is caught
    /// one frame out (`base.py:977`) and the instance attribute is never read
    /// either. So for a context, absent and truthy both suppress the arm, and
    /// only *present and falsy* lets it through. Both halves are measured:
    /// `Context().template` is an instance attribute with no class attribute,
    /// and `{{ c.template }}` renders `''` rather than `'None'`; a subclass with
    /// `zero = 0` and an instance `zero = 7` renders `7`.
    fn context_probes_attribute(&self, bit: &str) -> bool;

    /// `isinstance(current, BaseContext)` — part of the `dir()` rule and of the
    /// attribute-arm suppression, so it is read in two places and is a
    /// predicate rather than a convenience.
    fn is_context(&self) -> bool;

    /// `bit in dir(current)` — consulted **only** for a swallowed attribute-arm
    /// exception (`base.py:979-980`). It is *not* consulted for
    /// [`Step::Raised`]: the measured `RuntimeError` rows propagate whatever
    /// `dir()` says.
    fn in_dir(&self, bit: &str) -> bool;

    /// `current[int(bit)]` — the list-index arm. Takes [`IntValue`] rather than
    /// `i64` because Python's `int()` is unbounded, and Task 3 already parses
    /// exactly Python's grammar; re-parsing here with Rust's would admit `5_0`
    /// and reject `٥`.
    fn get_index(&self, index: &IntValue) -> Step;

    /// Which branch of Django's callable arm `current` takes, or `None` when the
    /// arm does not run at all because `callable(current)` is false. The host
    /// folds all three of Django's tests in (`callable`, and the two flags),
    /// because only it can read the flags and reading them here would need a
    /// second round trip.
    fn call_arm(&self) -> Option<CallArm>;

    /// Call the resolved callable and hand back the result as a new target.
    ///
    /// Django calls `current()` assuming no arguments (`base.py:1000`) and falls
    /// back to `signature()`/`bind()` to decide whether the `TypeError` meant
    /// "arguments were required" or "the body raised". The host owns all three
    /// because all three are Python (`inspect.signature` is Python's, and the
    /// exception must survive **by identity**).
    fn call(&self) -> Result<Box<dyn LookupTarget>, CallFailure>;

    /// `context.template.engine.string_if_invalid`, read **at the moment a
    /// substitution happens** — Django's three assignment sites
    /// (`base.py:997`, `base.py:1005`, `base.py:1011`) each re-read it, all
    /// inside `for bit in self.lookups:` (`base.py:959`) and all inside the
    /// outer `try` at `base.py:958`.
    ///
    /// Both halves of that are load-bearing, and both are measured
    /// (`m8_measure_invalid.py` §2):
    ///
    /// * **Lazy.** With no template bound the read raises
    ///   `AttributeError: 'NoneType' object has no attribute 'engine'`, while
    ///   `{{ x }}` with a plain value returns the value — the read is never
    ///   reached. A port that fetched the invalid value up front would raise on
    ///   the most ordinary template there is.
    /// * **Fallible, and the failure propagates.** The read is inside the outer
    ///   `try`, so its `AttributeError` reaches the handler at `base.py:1014`, which
    ///   finds no `silent_variable_failure` and re-raises. Measured: an
    ///   `alters_data` callable on an unbound context raises `AttributeError`;
    ///   it does not substitute.
    ///
    /// So `Err` means *the host has stashed that exception*: the caller returns
    /// [`LookupOutcome::Escaped`] and the host's outer-handler rule decides,
    /// exactly as for an arm that raised.
    fn string_if_invalid(&self) -> Result<Box<dyn LookupTarget>, PendingFailure>;

    /// The handle this container *is*. `Found` names the value the loop ended
    /// on; `Missing` names the container it failed **in**, which is the object
    /// Django puts in `VariableDoesNotExist.params`.
    fn node(&self) -> NodeRef;
}

/// What one path resolved to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LookupOutcome {
    /// The path resolved. The host holds the value behind this handle.
    Found(NodeRef),
    /// `VariableDoesNotExist`, as the two arguments Django passes: the failing
    /// `bit` and the *container object* the lookup failed in. The message is
    /// not built here — `VariableDoesNotExist.__str__` does the `%`, so the
    /// host constructs `VariableDoesNotExist(msg, params=(bit, obj))` and
    /// `repr(container)` is never called unless someone stringifies it.
    Missing { bit: String, container: NodeRef },
    /// An exception the host holds **by identity** escaped the arms and reached
    /// Django's outer `except Exception` (`base.py:1014`). This crate only
    /// decides *that*; the host decides what the handler does, because both of
    /// its branches need the exception object.
    ///
    /// **Two branches, and the variant was called `Reraise` until one of them was
    /// measured.** Django's handler logs, then:
    ///
    /// ```text
    /// base.py:1023   if getattr(e, "silent_variable_failure", False):
    /// base.py:1024       current = context.template.engine.string_if_invalid
    /// base.py:1026   else: raise
    /// ```
    ///
    /// So an exception carrying `silent_variable_failure` is **substituted**, not
    /// raised — Django's own documented idiom, and the reason
    /// `django.core.exceptions.ObjectDoesNotExist` sets the flag. Measured with a
    /// `@property` raising `class Silent(Exception): silent_variable_failure =
    /// True`: stock Django substitutes, and a port that re-raised diverged on
    /// both that row and the same exception from `__getitem__`.
    ///
    /// The substitution reads `context.template.engine`, so the host needs the
    /// context as well as the exception; this crate has neither, which is why the
    /// branch is not a variant of its own. The host must also keep Django's
    /// **plain attribute access** rather than a `getattr` default, so an unbound
    /// context fails on Django's terms.
    ///
    /// Flattening the exception to a string would lose `_culprit_node` and
    /// `__traceback__` — the reason `ErrorKind::Host` carries a message today and
    /// is scheduled to carry an object at M4.
    ///
    /// **The host must hand back the most recent failure**, because this variant
    /// is returned only immediately after an arm failed and the host stashed
    /// that failure on the way out. `template-core` cannot check that; the
    /// invariant is stated here so the host's `step`/`pending` pair has
    /// somewhere to point.
    ///
    /// **`bit` is carried because the handler's first act is to log it**, and
    /// this is the only place it exists: the host holds the exception, the
    /// context and the path, but not the position in the path the loop had
    /// reached. Django's line is `logger.debug("Exception while resolving
    /// variable '%s' in template '%s'.", bit, template_name, exc_info=True)`
    /// (`base.py:1016-1021`), and `test_logging` reads the formatted message —
    /// the row asserts `'author'` for `Variable("article.author")` over
    /// `{"article": {"section": "News"}}`, whose *last* bit is `author` and
    /// whose *failing* bit is also `author`. `{{ a.b.c }}` with `b` raising is
    /// the pair that separates the two, and it is why the bit cannot be
    /// recovered from the path one frame out.
    ///
    /// It is the bit the loop was *on*, which is the same for a
    /// [`LookupOutcome::Missing`] and is why that variant carries one too. The
    /// variant was payload-free until M9 wired the host's handler to it; the
    /// payload is what the handler reads.
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

        // Django's callable arm is **inside the loop**, one bit at a time
        // (`base.py:993`), not once at the end — and the difference is a value,
        // not an error. Measured: `{{ f.b }}` with `f = lambda: {"b": "B"}`
        // renders `'B'`, because Django calls `f` after the first bit and then
        // looks up `b` on the result. A check after the loop would look up `b`
        // on the *function* and report a missing key.
        match current.call_arm() {
            // Not callable, or Django's `pass`: either way the value stands and
            // the next bit looks into it. `DoNotCall` is the branch `pass`
            // describes, and it is implemented rather than deferred because it
            // needs no call at all — measured, `{{ f }}` with
            // `f.do_not_call_in_templates = True` renders the function's repr.
            None | Some(CallArm::DoNotCall) => {}
            // M8 owns the call. The alternative — returning the callable —
            // renders `<function …>`, which is wrong and quiet (D3).
            Some(CallArm::Call) => {
                current = match current.call() {
                    Ok(called) => called,
                    // Django assigns `string_if_invalid` and the loop goes on to
                    // the next bit with it (`base.py:997`, inside `for bit in …`).
                    Err(CallFailure::NoSignature | CallFailure::ArgsRequired) => {
                        match current.string_if_invalid() {
                            Ok(invalid) => invalid,
                            Err(PendingFailure) => {
                                return LookupOutcome::Escaped { bit: bit.clone() }
                            }
                        }
                    }
                    // Not a substitution: `base.py:1013` re-raises into the outer
                    // `except Exception` (`base.py:1014`), whose two branches need
                    // the
                    // exception object. The host owns that decision, as for
                    // `Escaped`.
                    Err(CallFailure::Raised) => return LookupOutcome::Escaped { bit: bit.clone() },
                }
            }
            // Not a call and not a raise: Django replaces the value
            // (`base.py:997`). The assignment is what makes the next bit
            // resolve against the substituted value — measured, `{{ x.upper }}`
            // with `x.alters_data = True` renders the *string's* `upper`,
            // `'<<INVALID>>'`, not the object's.
            //
            // The stub here said this branch "needs the `Context` this resolver
            // is deliberately not given". That is no longer true, and the
            // context did not have to be passed as a `Context`: it reaches the
            // arm through the host, as `LookupTarget::string_if_invalid`, which
            // is §5.2's rule (Python behind a trait) rather than an exception
            // to it.
            Some(CallArm::AltersData) => match current.string_if_invalid() {
                Ok(invalid) => current = invalid,
                // The read itself raised, inside Django's outer `try`
                // (`base.py:958`), so its `AttributeError` reaches the handler at
                // `base.py:1014`. The host has stashed it and applies that
                // handler.
                Err(PendingFailure) => return LookupOutcome::Escaped { bit: bit.clone() },
            },
        }
    }

    LookupOutcome::Found(current.node())
}

/// One bit, through Django's three arms in order (`base.py:960-992`).
///
/// The arm order is the obvious part. The part worth writing down is **where
/// Django's `dir()` rule lives**, because reading `base.py` quickly suggests it
/// belongs to "an exception the lookup raised" and it does not: it belongs to
/// the attribute arm's *swallowed* exception, and nowhere else.
///
/// The structure of `base.py:960-992` is three nested `try`s. The inner
/// `except (TypeError, AttributeError)` guards **only** `getattr`, and it is
/// inside it that `bit in dir(current)` is consulted:
///
/// ```text
/// try:                                        # the arms
///     try: current[bit]
///     except (TypeError, AttributeError, KeyError, ValueError, IndexError):
///         try: getattr(current, bit)
///         except (TypeError, AttributeError):         # only these two
///             if not isinstance(current, BaseContext) and bit in dir(current):
///                 raise                                # → escaped
///             try: current[int(bit)]
///             except (IndexError, ValueError, KeyError, TypeError):
///                 raise VariableDoesNotExist(...)       # → missing
/// except Exception as e:                       # everything else
///     ... if e.silent_variable_failure: ...    # → substituted
///     ... else: raise                          # → escaped, no dir() check
/// ```
///
/// So a `ValueError` from a `@property` never reaches the `dir()` line — it goes
/// to the outer handler, which re-raises it — while an `AttributeError` from the
/// same property is caught by the inner one and re-raised *because* its name is
/// in `dir()`. Both re-raises land on the same outer handler, which is why they
/// are one variant ([`LookupOutcome::Escaped`]) and not two: what the handler does
/// with them is the host's, and it does not depend on which arm got there.
/// Measured, all four rows in one run:
///
/// ```text
/// __getattr__ raises RuntimeError       not in dir -> RAISED RuntimeError
/// __getattr__ raises ValueError         not in dir -> RAISED ValueError
/// __getattr__ raises AttributeError     not in dir -> rendered ''
/// @property  raises AttributeError      in dir     -> RAISED AttributeError
/// ```
///
/// The last two rows are the same arm and differ only in `dir()`; the first two
/// are a different arm and no `dir()` answer changes them.
fn step_one(current: &dyn LookupTarget, bit: &str) -> Result<Box<dyn LookupTarget>, LookupOutcome> {
    // Every arm below fails the same way and names the same bit — the one being
    // looked up, which is this function's argument and never the caller's loop
    // variable. Built once here so the four sites cannot drift apart.
    let escaped = || LookupOutcome::Escaped {
        bit: bit.to_string(),
    };

    // 1. Dictionary arm, guarded by `__getitem__`'s presence. Django raises a
    //    `TypeError` for the guard rather than skipping the arm, and that
    //    `TypeError` is the first thing this arm's own `except` swallows — so
    //    the guard and the arm are one arm, and a scalar reaches `getattr`.
    if current.has_getitem() {
        match current.get_item(bit) {
            Step::Found(found) => return Ok(found),
            // A swallowed miss falls through to the attribute arm.
            Step::Miss => {}
            Step::Raised => return Err(escaped()),
        }
    }

    // 2. Attribute arm, with the `BaseContext` suppression. The suppression
    //    raises an `AttributeError` *inside* the inner `try`, so it is swallowed
    //    by the same `except` and then survives the `dir()` line (a context is
    //    excluded there) — i.e. a suppressed attribute skips to the index arm,
    //    which is what the guard below reproduces. The condition reads the
    //    positive predicate and negates it because an *absent* class attribute
    //    suppresses the arm too.
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

    // 3. List-index arm. `int(bit)` failing is the `ValueError` this arm
    //    swallows, so a non-numeric bit is a miss rather than a panic.
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

    /// The host, standing in. One field per trait method that Python would
    /// answer, so no arm needs a real `Context`.
    ///
    /// `Debug` is implemented by hand rather than derived: `label` is read only
    /// by that impl, and rustc's `dead_code` lint deliberately ignores derived
    /// impls, so a derived `Debug` plus `-D warnings` would reject the field.
    #[derive(Clone)]
    struct Fake {
        node: NodeRef,
        /// A human label, read by `Debug` and by nothing else: it exists so an
        /// assertion failure prints `Fake(NodeRef(1) @ "'A'")` instead of a
        /// bare index.
        label: &'static str,
        map: Option<Vec<(&'static str, Fake)>>,
        seq: Vec<Fake>,
        attrs: Vec<(&'static str, Fake)>,
        /// Names whose `getattr` raises an exception in Django's attr-arm
        /// swallowing set — `TypeError`/`AttributeError`, which is what a
        /// `@property` raises and what `getattr` raises for a missing name.
        swallowed: Vec<&'static str>,
        /// Names whose `getattr` raises anything else. Those never reach the
        /// `dir()` line: they leave the inner `try` and are re-raised.
        unswallowable: Vec<&'static str>,
        /// Whether `__getitem__` raises something outside its swallowing set,
        /// which is how the item arm reaches [`Step::Raised`] — a `RuntimeError`
        /// from `__getitem__`, measured to propagate whatever `dir()` says.
        item_unswallowable: bool,
        /// Names in `dir(self)`. For an object with `__getattr__`, `None`.
        dir: Vec<&'static str>,
        /// Class attributes that exist **and are falsy** — the only case in
        /// which a `BaseContext` reads the instance attribute. A name that is
        /// absent and a name that is truthy are both the same answer here, and
        /// both suppress the arm.
        falsy_class_attrs: Vec<&'static str>,
        is_context: bool,
        call_arm: Option<CallArm>,
        /// What `call()` answers, when the fixture drives the callable arm. The
        /// `Fake` inside stands for the value the call produced, and it is
        /// **boxed** because `Option<Result<Fake, _>>` would make this type
        /// contain itself by value.
        call: Option<Result<Box<Fake>, CallFailure>>,
        /// What `string_if_invalid()` answers, when the fixture drives a
        /// substitution. `Err` is the host's stashed-exception case.
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

    /// Hand-written, and the reason is mechanical: this is the only reader of
    /// `label`, and a *derived* `Debug` is ignored by `dead_code`.
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
            // A `Big` index is a `TypeError` on a Python list, which this arm
            // swallows — so it is a miss, not a panic.
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
                // `Box<Fake>` unsizes to `Box<dyn LookupTarget>` in return
                // position, so the clone is the whole conversion.
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

        // Missing: the failing bit, and the **container's handle** — index 99,
        // not the child's 1, because it is `current` that Django puts in
        // `VariableDoesNotExist.params`. No repr appears here: `__str__` builds
        // the message later, if ever.
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
        // Four rows, all measured above. The first two share an arm with the
        // third and differ from it only in `dir()`; the fourth is a different
        // arm, where no `dir()` answer changes anything.
        //
        // `@property` raising AttributeError, name in `dir()` → Django re-raises
        // on the `dir()` guard (`base.py:979-980`). Note the fixture: `get_attr`
        // returns `Step::Miss`, *not* `Step::Raised` — the exception is inside
        // the attr arm's swallowing set, and the re-raise is the `dir()` line's
        // doing. A fixture that modelled this as `Raised` would pass against a
        // resolver that had the rule in the wrong place.
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

        // `__getattr__` raising AttributeError, name not in `dir()` → the index
        // arm, whose `int("boom")` fails → `VariableDoesNotExist`.
        let getattr_not_in_dir = Fake {
            swallowed: vec!["boom"],
            dir: vec![],
            ..Fake::scalar(99, "<A object>")
        };
        assert!(matches!(
            resolve_lookup(&lookups(&["boom"]), Box::new(getattr_not_in_dir)),
            LookupOutcome::Missing { .. },
        ));

        // The row that keeps the rule in one place: a *non-swallowed* exception
        // propagates **without** consulting `dir()`. Both directions are here,
        // because "not in dir" is the half a misplaced rule gets wrong — a
        // resolver that ran the `dir()` check on every failed attribute lookup
        // would report a missing key and render `string_if_invalid`, where
        // Django raises.
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

        // ... and the item arm's failures never reach the `dir()` line at all:
        // a `RuntimeError` from `__getitem__` leaves Django's inner `try`
        // immediately. Both `dir()` answers are asserted, because the misplaced
        // rule would consult it here too.
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

        // A context never re-raises a swallowed attribute exception, even with
        // the name in `dir()`: `is_context()` is half of the measured predicate
        // (`base.py:972`), not a convenience.
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
        // A `BaseContext` has two rules, not one, and the plan's first predicate
        // had both wrong. Measured: `Context().template` is an *instance*
        // attribute with no class attribute, so `{{ c.template }}` renders `''`
        // and not `'None'` — Django's `getattr(type(current), bit)` raises
        // before the instance is read, and that AttributeError is caught one
        // frame out (`base.py:977`).
        let absent_class_attr = Fake {
            attrs: vec![("template", Fake::scalar(1, "<Engine>"))],
            ..Fake::context(0, "<Context>")
        };
        assert!(matches!(
            resolve_lookup(&lookups(&["template"]), Box::new(absent_class_attr)),
            LookupOutcome::Missing { .. },
        ));

        // The other direction, measured on a `Context` subclass with
        // `zero = 0` and an instance `zero = 7`: a *present and falsy* class
        // attribute lets the instance attribute through, so `{{ c.zero }}` is
        // `7`. A predicate written as "the class attribute is set" suppresses
        // this too and renders `''`.
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
        // Measured: `{{ f.b }}` with `f = lambda: {"b": "B"}` renders `'B'`.
        // The callable is bit 1's *result*, so a resolver that checked
        // callability only after the loop would look up `b` on the function.
        //
        // This replaced an assertion that the `Call` arm returned
        // `Unimplemented("… M8's")`. That assertion pinned the stub, and M8
        // removed the stub — the row it was written for is what the fixed arm
        // now does, so the replacement asserts the *called* value's handle and
        // then the second bit resolved on it. If the call ran after the loop
        // instead of inside it, bit `b` would be looked up on `f` and miss.
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
        // The `Call` arm's args-required branch: Django assigns
        // `string_if_invalid` and **stays in the loop** (`base.py:997`), so the
        // next bit resolves against the invalid value. Here the read itself
        // fails — the host's stashed-exception case — and the arm's contract is
        // `Escaped`, not a silent `""` (measured; `m8_measure_invalid.py` §2).
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
        // Review Focus 1, in-crate: `string_if_invalid('<<invalid>>')` is
        // assigned and the loop goes on, so `{{ x.upper }}` resolves `.upper`
        // *on the invalid string*. The second row is the discriminator — a port
        // that substituted and stopped would end on the invalid value's own
        // handle and never reach `upper`.
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

        // And with a `dir`-answered second bit on the invalid value, the loop
        // really did continue: `.upper` is read off the substituted target.
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
        // Measured: `{{ f }}` with `f.do_not_call_in_templates = True` renders
        // the function's repr — Django's branch is `pass`, so the callable is
        // the resolved value. This is M3-work the plan had deferred to M8, and
        // implementing it costs nothing: no call happens.
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
        // Measured: `{{ o.run }}` with `o.run.alters_data = True` renders
        // `string_if_invalid` — not a call, and not a raise. This replaced an
        // assertion that the arm returned `Unimplemented("… M8's")`; M8 removed
        // the stub, so the assertion died with it.
        //
        // In-crate this can only show that the substitution *took effect*: the
        // double has no Python and so no `str.upper`. That the loop actually
        // continues — the half Review Focus 1 is about — is the Python row's
        // job, in `tests/test_resolve_invalid.py`.
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
        // The read is inside Django's outer `try` (`base.py:958`), so its
        // failure is the handler's problem at `base.py:1014` — the same variant
        // as an arm that raised, and not a substitution.
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
