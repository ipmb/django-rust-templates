#!/usr/bin/env python
"""What the loop's per-iteration 0.222 us is made of, and how much of it is bookkeeping.

`loop_decomposition.py` fitted `cost(N) = a + b*N` for the corpus's `micro/loop-text`
row and got `b` = 0.222 us/row for this package and for stock Django, against
jinja2's 0.040. The body of that row is **text only** — no variable, no filter,
no lookup — so the whole of `b` is loop machinery, and the question this probe
answers is which part of it.

The claim under test is `minijinja_lessons.md` lesson 1: that ~0.18 of the 0.222
is the six `forloop` dict writes `ForNode.render` performs **unconditionally** on
every iteration (`engine/defaulttags.py:214-227`), whether or not the body reads
`forloop`. If that is right, a walk that keeps the counters natively and
materialises the dict only when a node it does not own can observe it takes
`micro/loop-text` past jinja2; if it is wrong, the loop work has to be sold on
something else.

**Two measurements, because they bound different things.**

* Section 1 measures each primitive the iteration performs, in situ, on the same
  objects the real render uses, so the sum can be compared against the fitted
  slope. A decomposition whose parts do not sum to the whole is a decomposition
  of something else (the spec's §1.2 arithmetic-closes rule).
* Section 3 is the *bound*: a `ForNode` subclass with the dict writes removed,
  rendering the same corpus row, byte-identical output asserted first. That is
  the win a native loop would collect on this row, minus the per-node Python
  frame the walk also removes — so section 3 is a **lower** bound on the walk's
  loop win, not an estimate of it.

The subclass is a probe, not a proposed change: the transcription guard pins
`ForNode.render` to upstream, so the shipped fix has to be in Rust.

Run:  .venv/bin/python -I .review/m19-measurements/loop_bookkeeping.py
"""

import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_engines  # noqa: E402

from django_rust_templates.engine.base import TextNode  # noqa: E402
from django_rust_templates.engine.defaulttags import ForNode  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

ROWS = [{"name": f"row{i}"} for i in range(50)]
ROW_TEXT = "<tr><td>x</td></tr>"
SOURCE = "{% for r in rows %}" + ROW_TEXT + "{% endfor %}"

# `loop_decomposition.txt`'s fitted `b` for `micro/loop-text`, both Django-side
# engines. Quoted here rather than re-fitted so that this probe is a check on
# that file and not a second, independent answer to the same question.
CORPUS_B = {"django": 0.221, "rust": 0.222}

REPEATS = 7


def best_us(fn, inner, target=0.05):
    """Best per-call microseconds over `REPEATS` samples, calibrated once."""
    n = 1
    while True:
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        if time.perf_counter() - t0 >= target:
            break
        n *= 2
    best = None
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        dt = (time.perf_counter() - t0) / n
        best = dt if best is None else min(best, dt)
    return best * 1e6 / inner


class HoistedLoop(ForNode):
    """`ForNode.render` with the per-iteration `forloop` writes removed.

    Sound **only** for a body that cannot observe `forloop`, which is why the
    corpus row this is measured on has a text-only body and why the assertion in
    section 0 runs before anything is timed. It is the shape a native loop would
    take when the delegation analysis (§3.4) proves the body clean — not a change
    to the shipped node, which the transcription guard pins to upstream.
    """

    def render(self, context):
        with context.push():
            values = self.sequence.resolve(context, ignore_failures=True)
            if values is None:
                values = []
            if not hasattr(values, "__len__"):
                values = list(values)
            len_values = len(values)
            if len_values < 1:
                return self.nodelist_empty.render(context)
            nodelist = []
            for item in values:
                context[self.loopvars[0]] = item
                for node in self.nodelist_loop:
                    nodelist.append(node.render_annotated(context))
        return mark_safe("".join(nodelist))


print("python:  %s on %s" % (platform.python_version(), platform.platform()))
print("row:     %s  (N=%d, body is text only)" % (SOURCE, len(ROWS)))
print("best of %d per point, calibrated to 0.05s" % REPEATS)
print()

engine = bench_engines.build("rust", {"page.html": SOURCE}, {})
template = engine.from_string(SOURCE)
context = engine.context({"rows": ROWS})
real = template.nodelist[0]

print("== 0. the stripped loop renders the same bytes, before anything is timed ==")
assert isinstance(real, ForNode), type(real)
hoisted = HoistedLoop.__new__(HoistedLoop)
hoisted.__dict__.update(real.__dict__)
assert type(hoisted) is HoistedLoop and hoisted.nodelist_loop is real.nodelist_loop
expected = ROW_TEXT * len(ROWS)
got_real = real.render(engine.context({"rows": ROWS}))
got_hoisted = hoisted.render(engine.context({"rows": ROWS}))
assert got_real == expected, (got_real[:40], expected[:40])
assert got_hoisted == expected, (got_hoisted[:40], expected[:40])
print("   real %r" % got_real[:24])
print("   stripped %r" % got_hoisted[:24])
print("   identical, and equal to ROW_TEXT * N")
print()

# --------------------------------------------------------------------------
# 1. The primitives, in situ, each divided by the 50 iterations it serves.
# --------------------------------------------------------------------------
print("== 1. the per-iteration primitives, us per iteration ==")
print()
print("%-34s %10s" % ("primitive", "us/iter"))
print("-" * 46)

body = real.nodelist_loop[0]
assert isinstance(body, TextNode), type(body)

loop_dict = {"parentloop": {}, "length": len(ROWS)}


def six_writes():
    for i in range(len(ROWS)):
        loop_dict["counter0"] = i
        loop_dict["counter"] = i + 1
        loop_dict["revcounter"] = len(ROWS) - i
        loop_dict["revcounter0"] = len(ROWS) - i - 1
        loop_dict["first"] = i == 0
        loop_dict["last"] = i == len(ROWS) - 1


nodelist = []
ctx = engine.context({"rows": ROWS})
with ctx.push():
    ctx["forloop"] = loop_dict

    def context_write():
        for item in ROWS:
            ctx["r"] = item

    def node_frame():
        for _ in ROWS:
            body.render_annotated(ctx)

    def append_only():
        for _ in ROWS:
            nodelist.append(ROW_TEXT)

    parts = {
        "6 loop_dict writes": six_writes,
        "context[loopvar] = item": context_write,
        "TextNode.render_annotated": node_frame,
        "list.append(render result)": append_only,
        "''.join([...]) over 50": lambda: "".join([ROW_TEXT] * len(ROWS)),
    }
    for label, fn in parts.items():
        inner = len(ROWS) if label != "''.join([...]) over 50" else len(ROWS)
        print("%-34s %10.4f" % (label, best_us(fn, inner)))

print()

# --------------------------------------------------------------------------
# 2. Do the parts sum to the row?
# --------------------------------------------------------------------------
total = (
    best_us(six_writes, len(ROWS))
    + best_us(context_write, len(ROWS))
    + best_us(node_frame, len(ROWS))
    + best_us(append_only, len(ROWS))
    + best_us(lambda: "".join([ROW_TEXT] * len(ROWS)), len(ROWS))
)
print("== 2. the parts against the fitted slope ==")
print()
print("%-34s %10s" % ("quantity", "us/row"))
print("-" * 46)
print("%-34s %10.4f" % ("sum of the five parts above", total))
for name, b in CORPUS_B.items():
    print("%-34s %10.4f   (loop_decomposition.txt)" % (f"{name}: fitted b", b))
print()
print("   a sum above the row means a part is measured out of situ; a sum")
print("   below it means something in the iteration is not in the list")
print()

# --------------------------------------------------------------------------
# 3. The bound: the stripped loop against the real one.
# --------------------------------------------------------------------------
print("== 3. the bound: ForNode.render vs the same loop with the writes hoisted ==")
print()
before = best_us(lambda: real.render(engine.context({"rows": ROWS})), 1)
after = best_us(lambda: hoisted.render(engine.context({"rows": ROWS})), 1)
print("%-34s %10.3f" % ("ForNode.render, N=50 (us/render)", before))
print("%-34s %10.3f" % ("stripped, N=50 (us/render)", after))
print("%-34s %10.3f" % ("delta (us/render)", before - after))
print("%-34s %10.4f" % ("delta per iteration", (before - after) / len(ROWS)))
print("%-34s %9.2fx" % ("speedup on this row", before / after))
print()
print("   this is a lower bound on the walk's loop win: it removes the dict")
print("   writes only, not the per-node Python frame the walk also removes")

engine.close()
