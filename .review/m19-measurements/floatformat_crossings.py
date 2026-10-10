"""What the body meter says about the one fully-walked row that is still a loss.

`django-only/filter-arg` is, after `dbce272`, entirely the walk's: `applied:
floatformat=50`, `delegated: LoadNode=1` and nothing else.  It is *still*
1.10x/1.13x of stock.  `applied:` cannot explain that — it says the walk routed
the call, not that Rust computed it — so the question is the third meter's.

The census's `body xings:` line on that row reads **200 over 50 applications**,
i.e. 4 crossings of the Python host per `floatformat`.  This file asks what the
four are a function of.  The answer it checks for is **nothing**: `floatformat`
is `@stringfilter`-wrapped and its body coerces, rounds and formats through
Python regardless of what it is handed, so the count should be flat across input
types — and it should also be 4 when there is **no argument at all**, which is
what rules out the input and the new `FilterArg::Literal` arm as the cause.  They
are the four host calls the body makes, and the count reconciles with the source:
`decimal_parse` (`floatformat.rs:460`), `decimal_quantize` (`:498`),
`number_format` (`:435`) and `mark_safe` (`:441`).

**What the count is *not* flat across is the argument's *shape*, and that is the
second finding.**  A **quoted** argument (`floatformat:"2"`) reads **5**: the
extra crossing is `to_int`, because Django `mark_safe`s a quoted argument into a
`SafeString` and `int_of` (`filters.rs:367-374`) can answer a `Value::Int` for
free where it must cross for a `Value::Safe`.  So the bare-number arm is cheaper
than the quoted spelling of the same argument — the literal keeps the `int`.

`upper` and `add` are the contrast: `upper` is a Rust table over a `Value::Str`
and reads **0**; `add` is a Python body and reads **1**.  Four is `floatformat`'s
own, not the wrapper's — the wrapper's crossings are outside the window by
construction (the counter is read on either side of `(entry.run)(...)`).

Run:  .venv/bin/python -I .review/m19-measurements/floatformat_crossings.py
"""

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_engines  # noqa: E402

ITERS = 20

# (label, source, data).  One `{{ … }}` and one filter application per row.
CASES = [
    ("floatformat      (no argument)", "{{ x|floatformat }}", {"x": 1.2345}),
    ("floatformat:2    (float)", "{{ x|floatformat:2 }}", {"x": 1.2345}),
    ("floatformat:2    (int)", "{{ x|floatformat:2 }}", {"x": 12345}),
    ("floatformat:2    (str)", "{{ x|floatformat:2 }}", {"x": "1.2345"}),
    ("floatformat:2    (Decimal)", "{{ x|floatformat:2 }}", {"x": Decimal("1.2345")}),
    ("floatformat:2    (zero)", "{{ x|floatformat:2 }}", {"x": 0}),
    ("floatformat:-2   (negative arg)", "{{ x|floatformat:-2 }}", {"x": 12345.6789}),
    ("floatformat:n    (variable arg)", "{{ x|floatformat:n }}", {"x": 1.2345, "n": 2}),
    ("floatformat:\"2\"  (quoted arg)", "{{ x|floatformat:\"2\" }}", {"x": 1.2345}),
    ("floatformat:\"2g\" (suffix arg)", "{{ x|floatformat:\"2g\" }}", {"x": 6666.6666}),
    ("upper           (Rust table)", "{{ x|upper }}", {"x": "abc"}),
    ("lower           (Python body)", "{{ x|lower }}", {"x": "ABC"}),
    ("escape          (Python body)", "{{ x|escape }}", {"x": "<a>"}),
    ("add:2           (Python body)", "{{ x|add:2 }}", {"x": 5}),
]


def census_render(engine, handle, context):
    from django_rust_templates import _native

    engine.render(handle, context)  # warm the loader cache
    _native.census_start()
    try:
        out = None
        for _ in range(ITERS):
            out = engine.render(handle, context)
        _, delegated, _, filters, body_crossings = _native.census_take()
    except BaseException:
        _native.census_take()
        raise
    return (
        [(name, n / ITERS) for name, n in delegated],
        [(name, n / ITERS) for name, n in filters],
        body_crossings / ITERS,
        out,
    )


def main() -> None:
    print(f"iters={ITERS}, rust engine, render mode")
    print("body xings: host crossings inside the body the walk applied\n")
    print(f"{'case':34}  {'walked?':8}  {'applied':22}  {'xings':>6}  {'per app':>8}")
    print("-" * 88)
    for label, source, data in CASES:
        engine = bench_engines.build("rust", {"page.html": source}, {})
        try:
            handle = engine.get("page.html")
            walked = getattr(handle, "_native_plan", None) is not None
            delegated, filters, xings, _ = census_render(
                engine, handle, engine.context(data)
            )
        finally:
            engine.close()
        applied = sum(n for _, n in filters) or 0
        per = xings / applied if applied else float("nan")
        name = " ".join(f"{n}={c:g}" for n, c in filters) or "-"
        route = "walked" if walked else "delegated"
        print(f"{label:34}  {route:8}  {name:22}  {xings:>6g}  {per:>8.2f}")
    print(
        "\n`floatformat` is 4 crossings whatever *value* it is handed and 4 with\n"
        "the argument absent, so the four are the body's own round-trips and not\n"
        "the input path.  They are the four host calls the body makes\n"
        "(`decimal_parse`, `decimal_quantize`, `number_format`, `mark_safe`).\n"
        "A *quoted* argument makes it 5: the extra one is `to_int`, which Rust\n"
        "answers for a bare number (`Value::Int`) and not for a `SafeString`.\n"
        "`upper` is 0 because Rust computes it; `lower`/`escape`/`add` are 1."
    )


if __name__ == "__main__":
    main()
