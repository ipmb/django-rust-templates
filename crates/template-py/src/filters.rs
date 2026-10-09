//! The real [`FilterHost`]: Python on the other side of the seam.
//!
//! Every operation here is one Python call, plus the classification that decides
//! which Python object a Rust [`Value`] is. The rule that shaped the split
//! (§D2/§D7.1) is that **nothing here decides anything**: `conditional_escape` is
//! one call rather than `is_safe`-then-escape because the branch belongs to
//! Python, `sort_by_property` is one call because the exception classification
//! belongs to Python, and `mark_safe` is one call because the `__html__` protocol
//! belongs to Python. A method here that started making decisions would be a
//! second implementation of a Python behaviour, which is the failure this crate
//! exists to prevent.
//!
//! Two things are worth reading before changing anything.
//!
//! **The value interner.** A `Value` is a closed enum and an `Opaque` is a
//! `ValueId` — an index into the `Vec<Py<PyAny>>` this host owns for the length
//! of one `_native.filter` call. Ids are therefore meaningful only inside the
//! call that minted them, and a `Value` outliving its host is a bug rather than a
//! leak: the ids would still resolve, to the wrong objects.
//!
//! **The `filter` loop is `FilterExpression.resolve`'s, and only its.**
//! `base.py:803-819` does three things around a filter body — `template_localtime`
//! before the call when `expects_localtime` is set, the `autoescape` keyword when
//! `needs_autoescape` is set, and `mark_safe` after the call when `is_safe` is set
//! *and* the pre-filter value was `SafeData`. Only the first and third are here;
//! the `@stringfilter` coercion is inside each body (§D3), so the loop and the
//! bodies together are the whole of what Django does. The corpus exercises this
//! loop because it is the part no filter's own unit test can reach.

use pyo3::exceptions::{
    PyAttributeError, PyException, PyIndexError, PyKeyError, PyRuntimeError, PyValueError,
};
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PySlice, PyString, PyTuple};
use std::cell::RefCell;

use template_core::filters::host::{FilterCall, FilterHost, NumberFormatArgs};
use template_core::filters::value::{Value, ValueId};
use template_core::parser::error::{Error, PyErrorKind};
use template_core::pynum::IntValue;

/// Django's `SafeData`, cached.
///
/// `isinstance(value, SafeData)` is the question `escape`, `force_escape`,
/// `safeseq`, `cut` and `linebreaksbr` branch on, and the one `Value::Safe`
/// cannot answer for an object that is not a `str`. Imported rather than
/// reproduced because it is a Django class and not a convention: `markupsafe`
/// defines `__html__` **without** inheriting from it, and the two are treated
/// differently by `conditional_escape` (`html.py:115-127`).
static SAFE_DATA: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn safe_data(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    SAFE_DATA.import(py, "django.utils.safestring", "SafeData")
}

/// `django.utils.safestring.mark_safe`, cached.
static MARK_SAFE: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn mark_safe_fn(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    MARK_SAFE.import(py, "django.utils.safestring", "mark_safe")
}

/// The host: Python, plus the objects Rust is holding by reference.
pub struct PyFilterHost<'py> {
    py: Python<'py>,
    /// The `Opaque` table. Grows for the life of one `filter` call; ids are
    /// [`ValueId`] and are minted in push order.
    interner: RefCell<Vec<Py<PyAny>>>,
}

/// Why a value could not be turned back into the Python object it came from.
///
/// Reachable only through a `Value::Opaque` whose id was minted by a *different*
/// host — a programming error in this crate, not a Python one, since every id
/// reaches here from the interner that minted it. Its own message rather than a
/// panic so that the failure names the id.
const STALE_OPAQUE: &str = "an Opaque id did not resolve in the host that minted it";

impl<'py> PyFilterHost<'py> {
    pub fn new(py: Python<'py>) -> Self {
        Self {
            py,
            interner: RefCell::new(Vec::new()),
        }
    }

    /// Hold a Python object for the life of this host and return its id.
    fn intern(&self, obj: Py<PyAny>) -> Value {
        let mut table = self.interner.borrow_mut();
        table.push(obj);
        Value::Opaque((table.len() - 1) as ValueId)
    }

    /// Classify a Python object into a [`Value`].
    ///
    /// The order of the tests is the whole of the correctness: `bool` before
    /// `int` (because `PyBool` **is** a `PyInt` subclass, so the reverse order
    /// turns every `True` into `1`), and the exact-`str` fast path before the
    /// subclass path (because a `str` subclass is where `SafeString` and
    /// markupsafe's `Markup` live, and they must not be conflated).
    ///
    /// A `str` subclass that is neither `SafeData` nor `__html__`-bearing
    /// classifies as `Value::Str`, which loses the subclass — deliberately, and
    /// measured: `type(x).__name__` would then disagree with Django's for such an
    /// object, so the harness compares types **in Python** (see
    /// `Value::type_name`). Anything that is not a `str`, `int`, `float`, `bool`,
    /// `list` or `None` is interned as an `Opaque` *including* tuples and dicts,
    /// because a `Value::List` that a tuple had been flattened into would answer
    /// `as_nested_sequence` and `get_index` with the wrong type's semantics.
    fn classify(&self, obj: &Bound<'py, PyAny>) -> PyResult<Value> {
        let py = self.py;
        if obj.is_none() {
            return Ok(Value::None);
        }
        if let Ok(flag) = obj.cast::<PyBool>() {
            return Ok(Value::Bool(flag.is_true()));
        }
        if obj.is_instance_of::<PyInt>() {
            return Ok(match obj.extract::<i64>() {
                Ok(small) => Value::Int(small),
                // Canonical decimal text, which is what `str(int)` gives and what
                // `IntValue::Big` is documented to hold.
                Err(_) => Value::BigInt(obj.str()?.to_string().into_boxed_str()),
            });
        }
        if obj.is_instance_of::<PyFloat>() {
            return Ok(Value::Float(obj.extract::<f64>()?));
        }
        if obj.is_instance_of::<PyList>() {
            let mut items = Vec::with_capacity(obj.len()?);
            for item in obj.try_iter()? {
                items.push(self.classify(&item?)?);
            }
            return Ok(Value::List(items));
        }
        if obj.is_instance_of::<PyString>() {
            let text = obj.cast::<PyString>()?.to_str()?.to_string();
            if obj.is_exact_instance_of::<PyString>() {
                return Ok(Value::Str(text.into_boxed_str()));
            }
            if obj.is_instance(safe_data(py)?)? {
                return Ok(Value::Safe(text.into_boxed_str()));
            }
            // A `str` subclass that answers `__html__` without being `SafeData`
            // is markupsafe's `Markup`, and `conditional_escape` returns it
            // **unchanged** where a `Value::Str` would be escaped. Interning it
            // is what keeps that branch Python's.
            if obj.hasattr("__html__")? {
                return Ok(self.intern(obj.clone().unbind()));
            }
            return Ok(Value::Str(text.into_boxed_str()));
        }
        Ok(self.intern(obj.clone().unbind()))
    }

    /// The Python object a [`Value`] names.
    fn object(&self, value: &Value) -> PyResult<Py<PyAny>> {
        let py = self.py;
        Ok(match value {
            Value::None => py.None(),
            Value::Bool(flag) => PyBool::new(py, *flag).to_owned().into_any().unbind(),
            Value::Int(number) => number.into_pyobject(py)?.into_any().unbind(),
            // Through Python's own parser, so a 400-digit integer is exact rather
            // than saturating. Django never saturates one, and neither may we.
            Value::BigInt(text) => py
                .get_type::<PyInt>()
                .call1((PyString::new(py, text),))?
                .unbind(),
            Value::Float(number) => number.into_pyobject(py)?.into_any().unbind(),
            Value::Str(text) => PyString::new(py, text).into_any().unbind(),
            Value::Safe(text) => mark_safe_fn(py)?
                .call1((PyString::new(py, text),))?
                .unbind(),
            Value::List(items) => {
                let mut converted = Vec::with_capacity(items.len());
                for item in items {
                    converted.push(self.object(item)?);
                }
                PyList::new(py, converted)?.into_any().unbind()
            }
            Value::Opaque(id) => {
                let table = self.interner.borrow();
                match table.get(*id as usize) {
                    Some(obj) => obj.clone_ref(py),
                    // A programming error in this crate — every id reaches here
                    // from the interner that minted it — so the message names the
                    // id rather than pretending Python said something.
                    None => {
                        return Err(PyRuntimeError::new_err(format!("{STALE_OPAQUE}: {id}")));
                    }
                }
            }
        })
    }

    /// A Python exception turned into the seam's error, keeping the **class**.
    ///
    /// The class is what survives, because the class is what Django's bodies
    /// match on: `first` catches `IndexError` and lets `TypeError` escape,
    /// `divisibleby` has no `try`, `add` catches bare `Exception`. A seam that
    /// flattened these to one error would make those three filters
    /// unimplementable — which is why [`PyErrorKind`] exists at all.
    fn host_error(&self, operation: &'static str, err: PyErr) -> Error {
        let py = self.py;
        let kind = py_error_kind(py, &err);
        // `str(e)`, not `repr(e)`: Django's own messages are the argument text,
        // and the class is already carried separately.
        let message = match err.value(py).str() {
            Ok(text) => text.to_string(),
            Err(_) => String::new(),
        };
        Error::filter_host(kind, operation, message)
    }

    /// `mark_safe`, able to report a failure.
    ///
    /// Split from the trait's [`FilterHost::mark_safe`] because the trait's
    /// signature cannot fail and Django's call can: `mark_safe` on a non-`str`
    /// computes `str(v)`, and a `__str__` that raises propagates out of
    /// `FilterExpression.resolve`. The loop below uses this one, so that path
    /// stays exact; the trait method wraps it and is what a *body* calls, where
    /// the value is one the body assembled.
    fn mark_safe_checked(&self, value: Value) -> PyResult<Value> {
        let py = self.py;
        let object = self.object(&value)?;
        let marked = mark_safe_fn(py)?.call1((object,))?;
        self.classify(&marked)
    }

    /// `str()` of a value, with the two variants that are already text short-
    /// circuited.
    fn text_of(&self, value: &Value) -> PyResult<String> {
        match value {
            Value::Str(text) | Value::Safe(text) => Ok(text.to_string()),
            other => Ok(self.object(other)?.bind(self.py).str()?.to_string()),
        }
    }

    /// `int(value)` through Python, which is not a parse: `int(" 5 ")`, `int("5_0")`
    /// and `int("٠٥")` are all 5, and `int` is arbitrary-precision.
    fn int_of(&self, value: &Value) -> PyResult<IntValue> {
        match value {
            Value::Int(number) => return Ok(IntValue::Small(*number)),
            Value::BigInt(text) => return Ok(IntValue::Big(text.to_string())),
            Value::Bool(flag) => return Ok(IntValue::Small(i64::from(*flag))),
            _ => {}
        }
        let py = self.py;
        let parsed = py.get_type::<PyInt>().call1((self.object(value)?,))?;
        Ok(match parsed.extract::<i64>() {
            Ok(small) => IntValue::Small(small),
            Err(_) => IntValue::Big(parsed.str()?.to_string()),
        })
    }

    /// `float(value)` through Python, for the same reason `int_of` exists.
    fn float_of(&self, value: &Value) -> PyResult<f64> {
        match value {
            Value::Float(number) => return Ok(*number),
            Value::Int(number) => return Ok(*number as f64),
            Value::Bool(flag) => return Ok(if *flag { 1.0 } else { 0.0 }),
            _ => {}
        }
        self.object(value)?.bind(self.py).extract::<f64>()
    }

    /// `bool(value)`, short-circuiting the variants Rust can answer and asking
    /// Python for an `Opaque` (whose `__bool__` or `__len__` Rust cannot see).
    fn truth_of(&self, value: &Value) -> PyResult<bool> {
        Ok(match value {
            Value::None => false,
            Value::Bool(flag) => *flag,
            Value::Int(number) => *number != 0,
            // `0` fits in `i64`, so a `BigInt` is never zero.
            Value::BigInt(_) => true,
            Value::Float(number) => *number != 0.0,
            Value::Str(text) | Value::Safe(text) => !text.is_empty(),
            Value::List(items) => !items.is_empty(),
            Value::Opaque(_) => self.object(value)?.bind(self.py).is_truthy()?,
        })
    }

    /// `len(value)`, counting **code points** for text — Python's `len` on a
    /// `str` is not a byte count, and `Value::Str` holds UTF-8.
    fn length_of(&self, value: &Value) -> PyResult<usize> {
        Ok(match value {
            Value::Str(text) | Value::Safe(text) => text.chars().count(),
            Value::List(items) => items.len(),
            other => self.object(other)?.bind(self.py).len()?,
        })
    }

    /// `list(value)`.
    fn items_of(&self, value: &Value) -> PyResult<Vec<Value>> {
        if let Value::List(items) = value {
            return Ok(items.clone());
        }
        let py = self.py;
        let listed = py.get_type::<PyList>().call1((self.object(value)?,))?;
        let mut items = Vec::with_capacity(listed.len()?);
        for item in listed.try_iter()? {
            items.push(self.classify(&item?)?);
        }
        Ok(items)
    }

    /// `value[index]`, with **Python's** negative-index and error semantics.
    ///
    /// `first` and `last` are `value[0]` / `value[-1]` inside
    /// `try: … except IndexError`, and Django lets a `TypeError` out of that
    /// handler. A Rust `Value::List` answers both halves; everything else goes to
    /// Python so that the exception class is Python's.
    fn index_of(&self, value: &Value, index: i64) -> PyResult<Value> {
        if let Value::List(items) = value {
            let len = items.len() as i64;
            let at = if index < 0 { index + len } else { index };
            return match items.get(usize::try_from(at).unwrap_or(usize::MAX)) {
                Some(item) => Ok(item.clone()),
                None => Err(PyIndexError::new_err(format!(
                    "list index out of range ({index})"
                ))),
            };
        }
        let py = self.py;
        let got = self.object(value)?.bind(py).get_item(index)?;
        self.classify(&got)
    }

    /// `value[start:stop:step]`, built as a real `slice` object so that `None`
    /// survives: `slice(None, None, None)` is `[:]` and has no `isize` spelling.
    fn slice_of(
        &self,
        value: &Value,
        start: Option<i64>,
        stop: Option<i64>,
        step: Option<i64>,
    ) -> PyResult<Value> {
        let py = self.py;
        let component = |part: Option<i64>| -> PyResult<Py<PyAny>> {
            Ok(match part {
                Some(number) => number.into_pyobject(py)?.into_any().unbind(),
                None => py.None(),
            })
        };
        let slice = py.get_type::<PySlice>().call1((
            component(start)?,
            component(stop)?,
            component(step)?,
        ))?;
        let sliced = self.object(value)?.bind(py).get_item(slice)?;
        self.classify(&sliced)
    }

    /// `a + b` through Python, which is the only side that can answer it: `+` is
    /// `__add__`, and no `Value` variant carries one.
    fn add_values_checked(&self, a: &Value, b: &Value) -> PyResult<Value> {
        let py = self.py;
        let left = self.object(a)?.bind(py).clone();
        let right = self.object(b)?.bind(py).clone();
        let int_type = py.get_type::<PyInt>();
        // `int(value) + int(arg)`, in that order: Python evaluates `int(value)`
        // first, so an `int(arg)` that would raise is never reached when `int(value)`
        // already has.
        let numeric = int_type
            .call1((left.clone(),))
            .and_then(|x| x.add(int_type.call1((right.clone(),))?));
        match numeric {
            Ok(sum) => return self.classify(&sum),
            Err(err) => {
                // Django's first arm catches `(ValueError, TypeError)` only, so
                // anything else is the caller's problem.
                if !matches!(
                    py_error_kind(py, &err),
                    PyErrorKind::ValueError | PyErrorKind::TypeError
                ) {
                    return Err(err);
                }
            }
        }
        match left.add(right) {
            Ok(sum) => self.classify(&sum),
            Err(err) => {
                // The second arm catches bare `Exception` and yields `""`. A
                // `BaseException` — `KeyboardInterrupt`, `SystemExit` — is not an
                // `Exception` and must not be swallowed.
                if err.is_instance_of::<PyException>(py) {
                    Ok(Value::Str(Box::from("")))
                } else {
                    Err(err)
                }
            }
        }
    }

    /// The key callable Django's `_property_resolver` returns for the string
    /// branch — `defaultfilters.py:558-570`.
    ///
    /// A Python callable rather than a Rust comparison because the *ordering*
    /// must stay Python's: `sorted` on arbitrary interned objects compares with
    /// `__lt__`, which Rust cannot see. So Rust extracts the key and Python sorts.
    fn property_resolver(&self, parts: Vec<String>) -> PyResult<Py<PyAny>> {
        Ok(Py::new(self.py, PropertyResolver { parts })?.into_any())
    }

    /// `sorted(value, key=…, reverse=…)` with the key Django's `dictsort` builds.
    ///
    /// The whole of `_property_resolver`, including the two branches that are not
    /// sorting at all: `float(arg)` succeeding means `operator.itemgetter(arg)`
    /// (so `dictsort(x, 1)` is the list and `dictsort(x, "1")` is a `TypeError`),
    /// and the string branch refuses a private name with the `AttributeError`
    /// Django refuses it with — which `dictsort`'s own `(AttributeError,
    /// TypeError)` handler then turns into `""`.
    fn sort_by_property_checked(
        &self,
        value: &Value,
        prop: &Value,
        reverse: bool,
    ) -> PyResult<Value> {
        let py = self.py;
        let key_arg = self.object(prop)?;
        let key = match py.get_type::<PyFloat>().call1((key_arg.bind(py).clone(),)) {
            Ok(_) => py
                .import("operator")?
                .getattr("itemgetter")?
                .call1((key_arg,))?
                .unbind(),
            Err(err) if err.is_instance_of::<PyValueError>(py) => {
                let text = key_arg.bind(py).extract::<String>()?;
                if text.contains("._") || text.starts_with('_') {
                    return Err(PyAttributeError::new_err(
                        "Access to private variables is forbidden.",
                    ));
                }
                if text.is_empty() {
                    // Django indexes `arg[0]` unguarded, so an empty argument
                    // raises `IndexError` out of the resolver and past
                    // `dictsort`'s handler. Reproduced rather than corrected.
                    return Err(PyIndexError::new_err("string index out of range"));
                }
                let parts = text.split('.').map(str::to_string).collect::<Vec<_>>();
                self.property_resolver(parts)?
            }
            Err(err) => return Err(err),
        };
        let kwargs = PyDict::new(py);
        kwargs.set_item("key", key)?;
        kwargs.set_item("reverse", reverse)?;
        let sorted = py
            .import("builtins")?
            .getattr("sorted")?
            .call((self.object(value)?,), Some(&kwargs))?;
        self.classify(&sorted)
    }

    /// `isinstance(value, SafeData)` — answered by Python for an `Opaque`, which
    /// is the only variant where the answer is not already known.
    ///
    /// `Value::Safe` is `true` by construction: the classifier puts a `str`
    /// subclass there only after asking `isinstance(x, SafeData)`. A failure to
    /// import or to call is `false`, which is the safe direction — `is_safe`
    /// gates `mark_safe`, so guessing "safe" would skip escaping.
    fn is_safe_value(&self, value: &Value) -> bool {
        match value {
            Value::Safe(_) => true,
            Value::Opaque(_) => {
                let Ok(object) = self.object(value) else {
                    return false;
                };
                let Ok(class) = safe_data(self.py) else {
                    return false;
                };
                object.bind(self.py).is_instance(class).unwrap_or(false)
            }
            _ => false,
        }
    }
}

/// Django's string branch key: walk `.`-separated parts, preferring `[]` and
/// falling back to attribute access, exactly as `_property_resolver` does.
///
/// A `#[pyclass]` rather than a Rust closure because `sorted` needs a *Python*
/// callable, and the five-exception fallback is the part of `_property_resolver`
/// that must not be re-derived on the Rust side of the seam.
#[pyclass]
struct PropertyResolver {
    parts: Vec<String>,
}

#[pymethods]
impl PropertyResolver {
    fn __call__(&self, py: Python<'_>, value: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let mut current = value;
        for part in &self.parts {
            let key = PyString::new(py, part);
            current = match current.get_item(&key) {
                Ok(item) => item,
                Err(err)
                    if matches!(
                        py_error_kind(py, &err),
                        PyErrorKind::AttributeError
                            | PyErrorKind::IndexError
                            | PyErrorKind::KeyError
                            | PyErrorKind::TypeError
                            | PyErrorKind::ValueError
                    ) =>
                {
                    current.getattr(part.as_str())?
                }
                Err(err) => return Err(err),
            };
        }
        Ok(current.unbind())
    }
}

/// `PyErr`'s class, by Python's own name, as the seam's taxonomy.
///
/// By name and not by `is_instance_of`, because the interesting answer for a
/// class this crate does not know is *its name* — `pprint` prints
/// `type(e).__name__` and `defaultfilters.py:996` is why
/// [`PyErrorKind::name`] exists.
fn py_error_kind(py: Python<'_>, err: &PyErr) -> PyErrorKind {
    let name = err
        .get_type(py)
        .name()
        .map(|name| name.to_string())
        .unwrap_or_default();
    match name.as_str() {
        "AttributeError" => PyErrorKind::AttributeError,
        "IndexError" => PyErrorKind::IndexError,
        "KeyError" => PyErrorKind::KeyError,
        "TypeError" => PyErrorKind::TypeError,
        "ValueError" => PyErrorKind::ValueError,
        "ZeroDivisionError" => PyErrorKind::ZeroDivisionError,
        _ => PyErrorKind::Other(name.into_boxed_str()),
    }
}

impl FilterHost for PyFilterHost<'_> {
    fn to_str(&self, value: &Value) -> Result<String, Error> {
        self.text_of(value)
            .map_err(|e| self.host_error("to_str", e))
    }

    fn to_int(&self, value: &Value) -> Result<IntValue, Error> {
        self.int_of(value).map_err(|e| self.host_error("to_int", e))
    }

    fn to_float(&self, value: &Value) -> Result<f64, Error> {
        self.float_of(value)
            .map_err(|e| self.host_error("to_float", e))
    }

    fn is_true(&self, value: &Value) -> Result<bool, Error> {
        self.truth_of(value)
            .map_err(|e| self.host_error("is_true", e))
    }

    fn len(&self, value: &Value) -> Result<usize, Error> {
        self.length_of(value).map_err(|e| self.host_error("len", e))
    }

    fn iter_values(&self, value: &Value) -> Result<Vec<Value>, Error> {
        self.items_of(value)
            .map_err(|e| self.host_error("iter_values", e))
    }

    fn get_index(&self, value: &Value, index: i64) -> Result<Value, Error> {
        self.index_of(value, index)
            .map_err(|e| self.host_error("get_index", e))
    }

    fn get_slice(
        &self,
        value: &Value,
        start: Option<i64>,
        stop: Option<i64>,
        step: Option<i64>,
    ) -> Result<Value, Error> {
        self.slice_of(value, start, stop, step)
            .map_err(|e| self.host_error("get_slice", e))
    }

    fn add_values(&self, a: &Value, b: &Value) -> Result<Value, Error> {
        self.add_values_checked(a, b)
            .map_err(|e| self.host_error("add_values", e))
    }

    fn percent_format(&self, value: &Value, spec: &str) -> Result<Value, Error> {
        let py = self.py;
        let outcome = (|| {
            let format = PyString::new(py, &format!("%{spec}"));
            let formatted = format.call_method1("__mod__", (self.object(value)?,))?;
            self.classify(&formatted)
        })();
        outcome.map_err(|e| self.host_error("percent_format", e))
    }

    fn is_safe(&self, value: &Value) -> bool {
        self.is_safe_value(value)
    }

    fn mark_safe(&self, value: Value) -> Value {
        // Infallible because the trait says so; see `mark_safe_checked` for the
        // loop's error-reporting path. The fallback is reachable only for an
        // `Opaque` whose `__str__` raises — Django would propagate that out of
        // `resolve`, and the trait cannot, so the value passes through unchanged
        // rather than the process dying.
        match self.mark_safe_checked(value.clone()) {
            Ok(marked) => marked,
            Err(_) => value,
        }
    }

    fn conditional_escape(&self, value: &Value) -> Result<Value, Error> {
        let py = self.py;
        let outcome = (|| {
            let escaped = py
                .import("django.utils.html")?
                .getattr("conditional_escape")?
                .call1((self.object(value)?,))?;
            self.classify(&escaped)
        })();
        outcome.map_err(|e| self.host_error("conditional_escape", e))
    }

    fn sort_by_property(&self, value: &Value, prop: &Value, reverse: bool) -> Result<Value, Error> {
        self.sort_by_property_checked(value, prop, reverse)
            .map_err(|e| self.host_error("sort_by_property", e))
    }

    fn as_nested_sequence(&self, value: &Value) -> Result<Option<Vec<Value>>, Error> {
        let py = self.py;
        let outcome = (|| {
            let object = self.object(value)?;
            let object = object.bind(py);
            let nested = object.is_instance(&py.get_type::<PyList>())?
                || object.is_instance(&py.get_type::<PyTuple>())?
                || object.is_instance(&py.import("types")?.getattr("GeneratorType")?)?;
            if !nested {
                return Ok(None);
            }
            // The second half of the test is `iter(item) does not raise`, which
            // `list(item)` answers by construction — and for a generator it is
            // also what the body is about to do, so consuming it here loses
            // nothing.
            match self.items_of(value) {
                Ok(items) => Ok(Some(items)),
                Err(_) => Ok(None),
            }
        })();
        outcome.map_err(|e| self.host_error("as_nested_sequence", e))
    }

    fn str_lower(&self, text: &str) -> String {
        self.call_text("str_lower", text, "lower")
    }

    fn str_title(&self, text: &str) -> String {
        self.call_text("str_title", text, "title")
    }

    fn strip_tags(&self, text: &str) -> String {
        self.utility_text("strip_tags", "django.utils.html", "strip_tags", text)
    }

    fn slugify(&self, text: &str) -> String {
        self.utility_text("slugify", "django.utils.text", "slugify", text)
    }

    fn urlize(&self, text: &str, limit: Option<usize>, autoescape: bool) -> String {
        let py = self.py;
        let outcome = (|| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("trim_url_limit", limit)?;
            // `nofollow=True` is not a caller's choice: `urlize` and `urlizetrunc`
            // both hardcode it (`defaultfilters.py:383`, `:395`), and the trait's
            // signature carries no `nofollow` for that reason.
            kwargs.set_item("nofollow", true)?;
            kwargs.set_item("autoescape", autoescape)?;
            let result = py
                .import("django.utils.html")?
                .getattr("urlize")?
                .call((PyString::new(py, text),), Some(&kwargs))?;
            result.extract::<String>()
        })();
        outcome.unwrap_or_else(|e| self.infallible_failure("urlize", e))
    }

    fn truncate(
        &self,
        text: &str,
        length: usize,
        words: bool,
        html: bool,
        truncate_text: Option<&str>,
    ) -> String {
        let py = self.py;
        let outcome = (|| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("truncate", truncate_text)?;
            kwargs.set_item("html", html)?;
            let truncator = py
                .import("django.utils.text")?
                .getattr("Truncator")?
                .call1((PyString::new(py, text),))?;
            let method = if words { "words" } else { "chars" };
            let truncated = truncator.call_method(method, (length,), Some(&kwargs))?;
            truncated.extract::<String>()
        })();
        outcome.unwrap_or_else(|e| self.infallible_failure("truncate", e))
    }

    fn wrap(&self, text: &str, width: usize) -> String {
        let py = self.py;
        let outcome = (|| {
            let wrapped = py
                .import("django.utils.text")?
                .getattr("wrap")?
                .call1((PyString::new(py, text), width))?;
            wrapped.extract::<String>()
        })();
        outcome.unwrap_or_else(|e| self.infallible_failure("wrap", e))
    }

    fn iri_to_uri(&self, text: &str) -> String {
        self.utility_text("iri_to_uri", "django.utils.encoding", "iri_to_uri", text)
    }

    fn quote(&self, text: &str, safe: &str) -> String {
        let py = self.py;
        let outcome = (|| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("safe", safe)?;
            let quoted = py
                .import("urllib.parse")?
                .getattr("quote")?
                .call((PyString::new(py, text),), Some(&kwargs))?;
            quoted.extract::<String>()
        })();
        outcome.unwrap_or_else(|e| self.infallible_failure("quote", e))
    }

    fn json_dumps(&self, value: &Value) -> Result<String, Error> {
        let py = self.py;
        let outcome = (|| {
            let kwargs = PyDict::new(py);
            // Django's own choice for `json_script` (`defaultfilters.py:82-91`):
            // no spaces, and `\uXXXX` for non-ASCII so the script body stays
            // ASCII whatever the page's encoding.
            kwargs.set_item("separators", (",", ":"))?;
            kwargs.set_item("ensure_ascii", true)?;
            let dumped = py
                .import("json")?
                .getattr("dumps")?
                .call((self.object(value)?,), Some(&kwargs))?;
            dumped.extract::<String>()
        })();
        outcome.map_err(|e| self.host_error("json_dumps", e))
    }

    fn pformat(&self, value: &Value) -> Result<String, Error> {
        let py = self.py;
        let outcome = (|| {
            let formatted = py
                .import("pprint")?
                .getattr("pformat")?
                .call1((self.object(value)?,))?;
            formatted.extract::<String>()
        })();
        outcome.map_err(|e| self.host_error("pformat", e))
    }

    fn random_choice(&self, value: &Value) -> Result<Value, Error> {
        let py = self.py;
        let outcome = (|| {
            let chosen = py
                .import("random")?
                .getattr("choice")?
                .call1((self.object(value)?,))?;
            self.classify(&chosen)
        })();
        outcome.map_err(|e| self.host_error("random_choice", e))
    }

    fn localtime(&self, value: Value, use_tz: bool) -> Result<Value, Error> {
        let py = self.py;
        let outcome = (|| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("use_tz", use_tz)?;
            let local = py
                .import("django.utils.timezone")?
                .getattr("template_localtime")?
                .call((self.object(&value)?,), Some(&kwargs))?;
            self.classify(&local)
        })();
        outcome.map_err(|e| self.host_error("localtime", e))
    }

    fn date_format(&self, value: &Value, format: &str) -> Result<String, Error> {
        self.formats_text("date_format", value, format)
    }

    fn time_format(&self, value: &Value, format: &str) -> Result<String, Error> {
        self.formats_text("time_format", value, format)
    }

    fn timesince(&self, d: &Value, now: Option<&Value>, reversed: bool) -> String {
        let py = self.py;
        let outcome = (|| {
            let now_object = match now {
                Some(value) => self.object(value)?,
                None => py.None(),
            };
            let elapsed = py
                .import("django.utils.timesince")?
                .getattr("timesince")?
                .call1((self.object(d)?, now_object, reversed))?;
            elapsed.extract::<String>()
        })();
        outcome.unwrap_or_else(|e| self.infallible_failure("timesince", e))
    }

    fn number_format(&self, args: NumberFormatArgs<'_>) -> String {
        let py = self.py;
        let outcome = (|| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("decimal_pos", args.decimal_pos)?;
            kwargs.set_item("use_l10n", args.use_l10n)?;
            kwargs.set_item("force_grouping", args.force_grouping)?;
            let formatted = py
                .import("django.utils.formats")?
                .getattr("number_format")?
                .call((self.object(args.value)?,), Some(&kwargs))?;
            formatted.extract::<String>()
        })();
        outcome.unwrap_or_else(|e| self.infallible_failure("number_format", e))
    }

    fn gettext(&self, text: &str) -> String {
        self.utility_text("gettext", "django.utils.translation", "gettext", text)
    }

    fn ngettext(&self, singular: &str, plural: &str, n: u64) -> String {
        let py = self.py;
        let outcome = (|| {
            let translated = py
                .import("django.utils.translation")?
                .getattr("ngettext")?
                .call1((singular, plural, n))?;
            translated.extract::<String>()
        })();
        outcome.unwrap_or_else(|e| self.infallible_failure("ngettext", e))
    }

    fn now(&self) -> Result<Value, Error> {
        let py = self.py;
        let outcome = (|| {
            let now = py
                .import("django.utils.timezone")?
                .getattr("now")?
                .call0()?;
            self.classify(&now)
        })();
        outcome.map_err(|e| self.host_error("now", e))
    }
}

impl PyFilterHost<'_> {
    /// `module.name(text)` → `str`, for the operations that are one such call.
    fn utility_text(
        &self,
        operation: &'static str,
        module: &str,
        name: &str,
        text: &str,
    ) -> String {
        let py = self.py;
        let outcome = (|| {
            let result = py
                .import(module)?
                .getattr(name)?
                .call1((PyString::new(py, text),))?;
            result.extract::<String>()
        })();
        outcome.unwrap_or_else(|e| self.infallible_failure(operation, e))
    }

    /// `text.<method>()` → `str`, for `str.lower` and `str.title`.
    ///
    /// A method name rather than a closure because a closure's return type would
    /// have to name the lifetime of the receiver it borrows, which is more
    /// machinery than two callers justify.
    fn call_text(&self, operation: &'static str, text: &str, method: &str) -> String {
        let py = self.py;
        let outcome = (|| {
            let lowered = PyString::new(py, text).call_method0(method)?;
            lowered.extract::<String>()
        })();
        outcome.unwrap_or_else(|e| self.infallible_failure(operation, e))
    }

    /// `django.utils.formats.<name>(value, format)` → `str`.
    fn formats_text(
        &self,
        operation: &'static str,
        value: &Value,
        format: &str,
    ) -> Result<String, Error> {
        let py = self.py;
        let outcome = (|| {
            let formatted = py
                .import("django.utils.formats")?
                .getattr(operation)?
                .call1((self.object(value)?, format))?;
            formatted.extract::<String>()
        })();
        outcome.map_err(|e| self.host_error(operation, e))
    }

    /// What an **infallible** operation does when Python nevertheless raises.
    ///
    /// The trait declares 15 operations as returning a bare value, which means
    /// they cannot report a failure — a decision taken and reviewed before the
    /// fan-out, and the reason this path exists at all. It is not a swallow: the
    /// panic names the operation and carries Python's message, so a filter whose
    /// host call failed says so rather than producing a plausible wrong string.
    /// Every one of these is reachable only by an input Django would raise on,
    /// which the corpus would then see as a difference rather than as silence.
    fn infallible_failure(&self, operation: &'static str, err: PyErr) -> String {
        let message = match err.value(self.py).str() {
            Ok(text) => text.to_string(),
            Err(_) => String::new(),
        };
        panic!("FilterHost::{operation} raised {message}");
    }
}

/// The loop `FilterExpression.resolve` runs around a filter body
/// (`base.py:803-819`), and the only part of a filter Django does *not* do inside
/// the filter itself.
///
/// Split out from the `#[pyfunction]` so it can be exercised without Python's
/// calling convention and so its order is visible in one place:
///
/// ```text
/// if expects_localtime: value = localtime(value, use_tz)
/// is_safe_was = is_safe(value)          # the value the filter is *given*
/// result = run(value, arg, call)
/// if is_safe and is_safe_was: result = mark_safe(result)
/// ```
///
/// The `is_safe` is read **before** the filter runs, which is `base.py:816`'s
/// `isinstance(obj, SafeData)` — `obj` still holds the pre-filter value there
/// because the result went to a separate `new_obj`. Reading it after the call
/// would make every filter that returns a `SafeString` mark its own output, and
/// would make `escape`'s `is_safe=True` mean nothing.
pub fn run_filter(
    py: Python<'_>,
    name: &str,
    value: &Bound<'_, PyAny>,
    arg: Option<&Bound<'_, PyAny>>,
    autoescape: bool,
    use_tz: bool,
) -> PyResult<Py<PyAny>> {
    let entry = template_core::filters::filter(name)
        .ok_or_else(|| PyKeyError::new_err(format!("`{name}` is not a built-in filter")))?;
    let host = PyFilterHost::new(py);
    let mut current = host.classify(value)?;
    // A Python `None` argument is `Option::None` and not `Value::None`: Django
    // has one object for "no argument" and "the argument `None`", and every
    // filter body that tests `arg is None` is testing this same question.
    let argument = match arg {
        Some(object) if !object.is_none() => Some(host.classify(object)?),
        _ => None,
    };
    if entry.flags.expects_localtime {
        current = host
            .localtime(current, use_tz)
            .map_err(|error| crate::filter_py_error(&error))?;
    }
    let was_safe = host.is_safe(&current);
    let call = FilterCall {
        host: &host,
        autoescape,
        use_tz,
    };
    let result =
        (entry.run)(current, argument, &call).map_err(|error| crate::filter_py_error(&error))?;
    let result = if entry.flags.is_safe && was_safe {
        host.mark_safe_checked(result)
            .map_err(|error| host.host_error("mark_safe", error))
            .map_err(|error| crate::filter_py_error(&error))?
    } else {
        result
    };
    host.object(&result)
}
