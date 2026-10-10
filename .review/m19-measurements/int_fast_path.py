#!/usr/bin/env python
"""The `int` arm of `render_value_in_context`: what it costs, how often it fires.

M19's second slice of step 2. The reviewer's finding was that
`render_value_in_context(12345)` cost ~3.46 us and that ~3.10 of it was
`number_format`, which for an exact `int` with grouping off does nothing but
`return mark_safe(number)` — after `get_language()` and three `get_format()`
calls. That would make it the largest per-value cost in the engine, and the arm
takes it out for every `int` whose grouping cannot change the answer. This probe
is what the claim was measured with, and it is kept because §2 quotes it.

Five measurements:

  1. **Faithfulness.** Every candidate value through stock's shipped route and
     through ours, comparing `(repr, type)` pairs — **under both autoescape
     settings**. A fast path that agrees on `str` but not on `SafeString` is a
     different engine, and the first draft of this section ran one context and
     said `agree` on every row; `autoescape=False` is where the `int` arm parted
     from upstream, and it was a type, not a character.
  2. **Absolute cost** per value type — the table §1.5 carries, re-measured on
     both columns — so the `int` row collapsing is visible beside the rows that
     did not move.
  3. **The decomposition**, over Django's own functions — `template_localtime`,
     `localize`, `number_format`, `get_language`, `get_format` — so the ~3.1 is
     attributed rather than asserted. **The three `get_format` rows each pay
     `get_language()` themselves** (they are called with `lang=None`), which is
     why they come out at ~1.99 against each other and against `get_language`'s
     1.92; inside `number_format`, `get_language` is paid once and the same three
     calls are dict-cache hits. Read as a decomposition of the *standalone*
     calls they would not sum, and they are not meant to.
  4. **The predicate**, by evaluating the shipped condition for four settings
     states × two autoescape settings. The refusal direction is the one that
     matters: the arm must *decline* whenever grouping could change the answer,
     and `test_base_parity.py`'s `test_the_int_arm_declines_when_grouping_is_on`
     is that row. `faithful=` beside `sound=` is the type check.
  5. **The census**: every value a real-world page actually resolves, by
     instrumenting `render_value_in_context` and rendering the real-world
     workloads. **This engine's own render resolves 1330 values and 385 of them
     (28.9%) are exact `int`**; stock's, on the same corpus, resolves 1077 and
     185 (17.2%). Two columns, two populations, and the larger one is the size of
     the win.

Every value printed goes through `{v!r}` and never `type(v).__name__`: a
transcript that names an input it did not evaluate cannot be pasted back.

Run:  .venv/bin/python -I .review/m19-measurements/int_fast_path.py
"""

import hashlib
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import ast  # noqa: E402
import django  # noqa: E402
from django.conf import settings  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from django.template.context import Context as StockContext  # noqa: E402
from django.utils import formats as dj_formats  # noqa: E402
from django.utils import numberformat as dj_numberformat  # noqa: E402
from django.utils import timezone as dj_timezone  # noqa: E402
from django.utils.formats import get_format, localize, number_format  # noqa: E402
from django.utils.safestring import SafeString, mark_safe  # noqa: E402
from django.utils.timezone import template_localtime  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.engine import base as eng_base  # noqa: E402
from django_rust_templates.engine import Context as RustContext  # noqa: E402

import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

REPEATS = 7

PINNED = ROOT / ".django" / "django" / "template" / "base.py"


def timed(fn, iters=20000):
    best = float("inf")
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        for _ in range(iters):
            fn()
        dt = (time.perf_counter() - t0) / iters
        best = min(best, dt)
    return best * 1e6  # us


print("interpreter : %s" % sys.version.split()[0])
print("extension   : %s" % Path(_native.__file__).name)
print("sha256      : %s" % hashlib.sha256(Path(_native.__file__).read_bytes()).hexdigest()[:16])
print("django      : %s" % django.__version__)
print(
    "recorded    : one run. This is a timing probe, so a second run differs in most\n"
    "              of its numbers. The sha above pins the *artifact*; it does not\n"
    "              make the digits repeat."
)
print(
    "settings    : USE_I18N=%r USE_THOUSAND_SEPARATOR=%r LANGUAGE_CODE=%r"
    % (settings.USE_I18N, settings.USE_THOUSAND_SEPARATOR, settings.LANGUAGE_CODE)
)

def pinned(name):
    """Django's own `name`, exec'd from the pinned file into our globals.

    What the "stock" column is. Not a copy of upstream's body: the point of an
    A/B here is that one side is upstream's byte-for-byte, and a retyped oracle
    would be this file's idea of upstream.
    """
    source = PINNED.read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            namespace = dict(eng_base.__dict__)
            exec(compile(ast.Module([node], []), "<pinned>", "exec"), namespace)
            return namespace[name]
    raise LookupError(name)


# **The stock column is the pinned file, not an import.** `django.template.base`
# is not interposed any more — M18 removed the shim — so an import would give the
# same answer today, but reading the body out of `.django/` makes the two columns
# different *by construction*, and a two-sided measurement whose sides can turn
# out to be one object is not two-sided. It also holds the stock column at the
# pinned version if the venv's Django ever moves.
stock_rvic = pinned("render_value_in_context")
ours_rvic = eng_base.render_value_in_context


# ---------------------------------------------------------------- the predicate


def use_grouping_now(context):
    """The exact condition `numberformat.format` builds, for the int arm.

    Three steps, and the first draft of this probe dropped the middle one:
    `localize` short-circuits on `use_l10n is False` *before* `number_format` is
    entered; `number_format` then coerces `None -> True`; only then does `format`
    build `use_l10n and settings.USE_THOUSAND_SEPARATOR and grouping != 0`.
    Reading `bool(context.use_l10n)` skips the coercion and answers False for the
    default context, which is the one setting where the answer matters.
    """
    use_l10n = context.use_l10n
    if use_l10n is False:
        return False, None, "localize short-circuits"
    if use_l10n is None:
        use_l10n = True
    lang = dj_formats.get_language() if use_l10n else None
    grouping = get_format("NUMBER_GROUPING", lang, use_l10n=use_l10n)
    return (
        bool(use_l10n) and settings.USE_THOUSAND_SEPARATOR and grouping != 0,
        grouping,
        "numberformat.format",
    )


def arm_took(value, context):
    """The shipped predicate, verbatim, so the probe reads the code it measures."""
    return type(value) is int and (
        context.use_l10n is False or not settings.USE_THOUSAND_SEPARATOR
    )


class SubInt(int):
    pass


class WeirdInt(int):
    def __str__(self):
        return f"<w{int(self)}>"


CANDIDATES = [
    ("small int", 12345),
    ("zero", 0),
    ("negative", -7),
    ("big int", 10**30),
    ("bool True", True),
    ("bool False", False),
    ("float", 3.5),
    ("Decimal", Decimal("3.50")),
    ("str", "hi"),
    ("str markup", "a & b < c"),
    ("SafeString", mark_safe("hi")),
    ("int subclass", SubInt(12)),
    ("int subclass __str__", WeirdInt(12)),
    ("None", None),
    ("list", [1, 2]),
]

print("\n1. faithfulness: stock vs ours, by (repr, type)")
# **Both autoescape settings, and that is the correction this section owes.**
# The first draft ran the default context only — `autoescape=True` — and printed
# `agree` on every row. With `autoescape=False` an exact `int` parts the two
# engines *in type*: upstream's final statement is `str(localize(value))`, and
# for anything but `use_l10n is False` that is `str(mark_safe(number))`, which is
# the `SafeString` itself. The arm returned a plain `str`. Same characters, so
# no render can see it; only a type comparison can.
for _ae in (True, False):
    sctx = StockContext(autoescape=_ae)
    rctx = RustContext(autoescape=_ae)
    print(f"   -- context.autoescape={_ae!r}")
    print(f"   {'value':22} {'stock':>20} {'ours':>20}  agree")
    for label, v in CANDIDATES:
        try:
            got_stock = stock_rvic(v, sctx)
        except Exception as e:  # noqa: BLE001
            got_stock = f"<{type(e).__name__}: {e}>"
        try:
            got_ours = ours_rvic(v, rctx)
        except Exception as e:  # noqa: BLE001
            got_ours = f"<{type(e).__name__}: {e}>"
        a = (repr(got_stock), type(got_stock))
        b = (repr(got_ours), type(got_ours))
        print(f"   {label:22} {a!r:>20} {b!r:>20}  {'yes' if a == b else 'NO'}")
    sctx = StockContext()
    rctx = RustContext()

# -------------------------------------------------------------- 2. absolute cost

print(f"\n2. per-value cost, stock vs ours  (best of {REPEATS})")
print(f"   {'value':22} {'stock us':>9} {'ours us':>9} {'arm?':>6}")
for label, v in CANDIDATES:
    try:
        s = timed(lambda v=v: stock_rvic(v, sctx), iters=5000)
    except Exception:  # noqa: BLE001
        s = float("nan")
    try:
        o = timed(lambda v=v: ours_rvic(v, rctx), iters=5000)
    except Exception:  # noqa: BLE001
        o = float("nan")
    takes = arm_took(v, rctx) if type(v) is int else False
    print(f"   {label:22} {s:9.3f} {o:9.3f} {str(takes):>6}")

# ----------------------------------------------------------------- 3. the pieces

print(f"\n3. where the int cost goes, over Django's own functions  (best of {REPEATS})")
pieces = [
    ("template_localtime(12345)", lambda: template_localtime(12345, use_tz=None)),
    ("  ...isinstance gate only", lambda: isinstance(12345, dj_timezone.datetime)),
    ("localize(12345)", lambda: localize(12345, use_l10n=None)),
    ("number_format(12345)", lambda: number_format(12345, use_l10n=None)),
    ("  get_language()", lambda: dj_formats.get_language()),
    ("  get_format(DECIMAL_SEPARATOR)", lambda: get_format("DECIMAL_SEPARATOR", None, use_l10n=None)),
    ("  get_format(NUMBER_GROUPING)", lambda: get_format("NUMBER_GROUPING", None, use_l10n=None)),
    ("  get_format(THOUSAND_SEPARATOR)", lambda: get_format("THOUSAND_SEPARATOR", None, use_l10n=None)),
    (
        "  numberformat.format inner",
        lambda: dj_numberformat.format(12345, ".", None, 3, ",", force_grouping=False, use_l10n=None),
    ),
    ("str(12345)", lambda: str(12345)),
    ("_native.html_escape('12345')", lambda: _native.html_escape("12345")),
    ("settings.USE_THOUSAND_SEPARATOR", lambda: settings.USE_THOUSAND_SEPARATOR),
    ("context.use_l10n", lambda: rctx.use_l10n),
    ("type(v) is int", lambda: type(12345) is int),
]
for label, fn in pieces:
    print(f"   {label:34} {timed(fn):9.3f}")

# ------------------------------------------------------------------ 4. predicate


def predicate_row(thousand, l10n_ctx, autoescape):
    with override_settings(USE_THOUSAND_SEPARATOR=thousand):
        c = StockContext(use_l10n=l10n_ctx, autoescape=autoescape)
        rc = RustContext(use_l10n=l10n_ctx, autoescape=autoescape)
        ug, grouping, route = use_grouping_now(c)
        upstream = stock_rvic(12345, c)
        ours = ours_rvic(12345, rc)
        # The condition `numberformat.format` would itself compute, verbatim.
        if l10n_ctx is False:
            inner = False
        else:
            coerced = True if l10n_ctx is None else l10n_ctx
            inner = bool(coerced and settings.USE_THOUSAND_SEPARATOR) and grouping != 0
        return (
            ug,
            grouping,
            upstream,
            ours,
            inner,
            route,
            arm_took(12345, rc),
        )


print("\n4. the predicate, per settings state")
for thousand in (False, True):
    for l10n in (None, False):
        for ae in (True, False):
            ug, grouping, out, ours, inner, route, took = predicate_row(
                thousand, l10n, ae
            )
            agree = "agree" if ug == inner else "DISAGREE"
            sound = "yes" if took == (not inner) else "NO"
            # Both columns carry their type: the predicate's job is to be sound,
            # and "sound" is a claim about the answer's *(repr, type)*, not its
            # characters.
            faithful = "yes" if (repr(out), type(out)) == (repr(ours), type(ours)) else "NO"
            print(
                f"   USE_THOUSAND_SEPARATOR={thousand!r:<5} use_l10n={l10n!r:<5} "
                f"autoescape={ae!r:<5} grouping={grouping!r:<4} use_grouping={ug!r:<5} "
                f"inner={inner!r:<5} stock={out!r:<10} {type(out).__name__:<11} "
                f"ours={ours!r:<10} {type(ours).__name__:<11} arm={took!r:<5} "
                f"sound={sound} faithful={faithful} [{route}] {agree}"
            )

# ------------------------------------------------------------------- 5. census

print("\n5. census: values a real-world page resolves")


def _kind(v):
    t = type(v)
    if t is int:
        return "int"
    if t is bool or isinstance(v, bool):
        return "bool"
    if t is str:
        return "str"
    if isinstance(v, str):
        return "str subclass " + t.__name__
    if t is float:
        return "float"
    if t is Decimal:
        return "Decimal"
    if v is None:
        return "None"
    return "other:" + t.__name__


def census(engine_name):
    from collections import Counter

    import django.template.base as stock_base

    counts = Counter()
    # The *importable* stock function, not `stock_rvic`: this patch is undone in
    # the `finally`, and restoring the pinned copy into `django.template.base`
    # would replace Django's function with an exec'd duplicate for the rest of
    # the process — a mutation the probe would then be measuring. The two are the
    # same function's body, so the count is the same either way.
    original = stock_base.render_value_in_context
    ours_original = eng_base.render_value_in_context

    def spy(value, context):
        counts[_kind(value)] += 1
        return original(value, context)

    def spy_ours(value, context):
        counts[_kind(value)] += 1
        return ours_original(value, context)

    stock_base.render_value_in_context = spy
    eng_base.render_value_in_context = spy_ours
    try:
        for wl in bench_corpus.workloads():
            if wl.family not in ("real-world", "django-only"):
                continue
            if engine_name not in wl.engines:
                continue
            eng = bench_engines.build(engine_name, wl.templates, wl.libraries(engine_name))
            handle = eng.get(wl.entry)
            eng.render(handle, eng.context(wl.data))
            eng.close()
    finally:
        stock_base.render_value_in_context = original
        eng_base.render_value_in_context = ours_original
    return counts


for eng_name in ("rust", "django"):
    try:
        c = census(eng_name)
    except Exception as e:  # noqa: BLE001
        print(f"   census({eng_name}) failed: {type(e).__name__}: {e}")
        continue
    total = sum(c.values())
    print(f"   -- {eng_name}: {total} values")
    for k, n in c.most_common():
        print(f"        {k:24} {n:6}  {n / total * 100:5.1f}%")
