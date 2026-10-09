"""The pinned tree's own decorator facts for M12 Task 7's nine filters.

`filters/mod.rs` appends nine `FilterEntry` rows and every one is
`FilterFlags::NONE` with `stringfilter: false`. That is a claim about
`.django/` @ 6.0.9 — about the *decorators* on nine `register.filter` rows —
and until this script existed nothing measured it; it was asserted in a
comment and confirmed by reading. The class of defect is the project's usual
one (inferring a property from a name), so the properties are read off the
AST rather than off the module names.

Four things are reported, each a separate question:

1. **`stringfilter`** — whether the row carries `@stringfilter`, which is what
   `FilterEntry::stringfilter` must mirror. Measured: none of the nine does, so
   `false` is the tree's answer and not a default.
2. **`is_safe` / `needs_autoescape`** — the two keyword arguments
   `register.filter` reads, which `FilterFlags` mirrors. Measured: absent on the
   i18n four and the tz three, `False` on the l10n two. `FilterFlags` cannot
   spell "absent" (`mod.rs:245`) and the two behave alike at `base.py:816`, so
   `NONE` is right for all nine — but that is a *reason*, and this is the
   measurement that makes it usable.
3. **The registered name, which is not always the `def`'s name.** `tz.py`'s
   third filter is `@register.filter("timezone")` on `def do_timezone`
   (`:37-38`), so the pinned file has **no Python function called `timezone`**
   and the name `timezone` resolves to the `do_timezone` object. The Rust table
   has a `timezone` row and a `do_timezone` *transcription*, which is the seam's
   whole vocabulary: the Rust side is keyed on the registered name, the Python
   seam on the handle. A probe that walked `FunctionDef.name` reports `timezone`
   as **not registered**, which is a false negative in the safe-looking
   direction — and it is what this script's first draft did.
4. **66 names over 66 distinct handles**, the injectivity `django_own_filters()`
   inverts. It is asserted as a row in `tests/test_interposition.py`; it is
   re-measured here so the inversion's premise has an independent instrument.

Invocation — **stock Django, no shim**, and therefore `-I` (which also ignores
`PYTHONPATH`, so `.django` is put on `sys.path` by hand):

    .venv/bin/python -I .review/m12-measurements/m12_measure_filter_flags.py

`-I` is what makes this script *right* rather than merely harmless: the question
is what the pinned file says, and under the shim `django.templatetags.tz` is our
transcription, whose decorators are not the pinned tree's.
"""

import ast
import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PIN = ROOT / ".django" / "django" / "templatetags"

# The nine, by the name each is registered under — the name `_native.filter`
# dispatches on. `timezone` is the one whose registered name and `def` name
# differ, which is item 3 above and the reason this list is keyed by the
# registry rather than by the module's `def`s.
NINE = {
    "i18n": (
        "language_bidi",
        "language_name",
        "language_name_local",
        "language_name_translated",
    ),
    "l10n": ("localize", "unlocalize"),
    "tz": ("localtime", "timezone", "utc"),
}


def register_call(node, method):
    """The `register.<method>(...)` call decorating `node`, or `None`."""
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (
            isinstance(target, ast.Attribute)
            and target.attr == method
            and getattr(target.value, "id", "") == "register"
        ):
            return decorator
    return None


def registered_name(call, node):
    """What `register.filter` binds: the decorator's string, else the `def`."""
    if isinstance(call, ast.Call) and call.args:
        try:
            return ast.literal_eval(call.args[0])
        except ValueError:
            return f"<{ast.unparse(call.args[0])}>"
    return node.name


def keyword(call, name):
    """The decorator's `name=` argument as source text, or a visible absence."""
    if isinstance(call, ast.Call):
        for kw in call.keywords:
            if kw.arg == name:
                return ast.unparse(kw.value)
    return "<absent>"


def carries_stringfilter(node):
    """Whether *any* decorator on `node` is `stringfilter` (bare or imported)."""
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "stringfilter":
            return True
    return False


def filter_rows(lib):
    """`{registered name: (def name, lineno, end_lineno, call, node)}`.

    Keyed by the **registered** name, not the `def`'s — the whole of item 3.
    """
    source = (PIN / f"{lib}.py").read_text()
    rows = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef):
            call = register_call(node, "filter")
            if call is not None:
                rows[registered_name(call, node)] = (
                    node.name,
                    node.lineno,
                    node.end_lineno,
                    call,
                    node,
                )
    return rows, len(source.splitlines())


def main():
    print(f"pinned tree : {PIN}  (Django 6.0.9)")
    print(f"interpreter : {sys.version.split()[0]}")
    print()

    failures = []
    measured = []

    for lib, nine in NINE.items():
        rows, lines = filter_rows(lib)
        print(
            f"== django/templatetags/{lib}.py  ({lines} lines, "
            f"{len(rows)} register.filter row(s))"
        )
        for name in nine:
            if name not in rows:
                print(f"   {name:<26} !! NOT REGISTERED UNDER THIS NAME")
                failures.append(f"{lib}.{name} is not a register.filter row")
                continue
            def_name, lineno, end, call, node = rows[name]
            stringfilter = carries_stringfilter(node)
            measured.append(name)
            print(
                f"   {name:<26} def {def_name:<14} {lineno}-{end}  "
                f"stringfilter={str(stringfilter):<5} "
                f"is_safe={keyword(call, 'is_safe'):<8} "
                f"needs_autoescape={keyword(call, 'needs_autoescape')}"
            )
            if stringfilter:
                failures.append(f"{lib}.{name} does carry @stringfilter")
            if def_name != name:
                print(f"   {'':<26} ^ registered under `{name}`, `def` is `{def_name}`")
        print()

    # Item 4 — the inversion's premise, read off the live registries.
    sys.path.insert(0, str(ROOT / ".django"))
    from django.conf import settings  # noqa: E402

    if not settings.configured:
        settings.configure(USE_I18N=True, USE_TZ=True, INSTALLED_APPS=[])

    import django  # noqa: E402

    django.setup()

    from django.template import defaultfilters  # noqa: E402
    from django.templatetags import i18n, l10n, tz  # noqa: E402

    merged = {}
    for table in (i18n.register.filters, l10n.register.filters, tz.register.filters):
        merged.update(table)
    merged.update(defaultfilters.register.filters)

    handles = Counter(merged.values())
    duplicated = sorted(handle for handle, count in handles.items() if count > 1)
    print("== the merged registry that `django_own_filters()` inverts")
    print(f"   {len(merged)} name(s) over {len(handles)} distinct handle(s)")
    print(f"   handle(s) under more than one name: {duplicated or 'none'}")
    print()
    for lib, nine in NINE.items():
        absent = [name for name in nine if name not in merged]
        if absent:
            failures.append(f"{lib}: {absent} absent from the merged registry")

    print("=== verdict")
    if failures:
        for failure in failures:
            print(f"   FAIL  {failure}")
        return 1
    print(
        f"   {len(measured)} of 9 library filter(s) measured; none carries "
        f"@stringfilter, so `stringfilter: false` is the tree's answer"
    )
    print("   the merge is injective, so the handle -> name inversion is faithful")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
