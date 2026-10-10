#!/usr/bin/env python
"""What the corpus actually renders, by value type, and whether escaping bites.

The M19 review's BLOCKING 3: §2 prices the str fast path on the cheapest value
type, and "the fraction of unfiltered non-str variables in your corpus is
unmeasured and it swings the per-variable estimate by 17x". BLOCKING 2's sibling
worry is the same shape — a shared row hashes identically with autoescape off if
nothing in it escapes, so "the four engines agree" would say nothing.

Both are answered by one instrument: wrap `render_value_in_context` while each
corpus workload renders, and tally (exact type of the value, whether escaping
changed the bytes). Exact type, not `isinstance`: a `SafeString` and a plain
`str` are the two cases §3.1's predicate has to tell apart, and `isinstance`
cannot.

The tally is per **workload**, because a family average over rows that differ by
17x is the number that misleads.

Run:  .venv/bin/python -I .review/m19-measurements/value_mix.py
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

from django.utils.html import conditional_escape  # noqa: E402
from django_rust_templates.engine import base as eng_base  # noqa: E402
from django_rust_templates.engine import defaulttags as eng_defaulttags  # noqa: E402
from django_rust_templates.engine.templatetags import i18n as eng_i18n  # noqa: E402

# All three bindings have to be wrapped, and that is the finding rather than an
# inconvenience: `lib.rs:2861` fetches `engine.base.render_value_in_context` by
# attribute once per render, `base.py:1161` resolves the module global at call
# time, and `defaulttags`/`i18n` each did `from ... import render_value_in_context`
# at import time and hold their own reference. Wrapping only `django.template.base`
# — the obvious one — tallies zero variables on every row, which is how this probe
# first read, and a zero tally is indistinguishable from "this corpus has no
# variables" unless you print the total.
real = eng_base.render_value_in_context
tally = Counter()
escaped = [0, 0]  # [str values whose bytes escaping changed, plain-str values seen]


def counting(value, context):
    out = real(value, context)
    tally[type(value).__qualname__] += 1
    if type(value) is str:  # only a plain str is a candidate for a native path
        escaped[1] += 1
        if conditional_escape(value) != value:
            escaped[0] += 1
    return out


for module in (eng_base, eng_defaulttags, eng_i18n):
    module.render_value_in_context = counting

print(
    "%-22s %-9s %6s  %s"
    % ("workload", "family", "vars", "by exact type (count)")
)
print("-" * 96)

per_family = {}
for workload in bench_corpus.workloads():
    tally.clear()
    escaped[0] = escaped[1] = 0
    engines = bench_engines.missing_engines()
    if "rust" in engines:
        print("cannot build the rust engine:", engines)
        sys.exit(1)
    engine = bench_engines.build(
        "rust", workload.templates, workload.libraries("rust")
    )
    try:
        handle = (
            engine.get(workload.entry)
            if workload.entry != "__string__"
            else engine.from_string(workload.templates["page.html"])
        )
        handle.render(engine.context(workload.data))
    finally:
        engine.close()
    parts = ", ".join(f"{k} {v}" for k, v in sorted(tally.items()))
    print(
        "%-22s %-9s %6d  %s%s"
        % (
            workload.key,
            workload.family,
            sum(tally.values()),
            parts,
            (
                f"   [str escaping changed bytes {escaped[0]}/{escaped[1]}]"
                if escaped[1]
                else ""
            ),
        )
    )
    fam = per_family.setdefault(workload.family, Counter())
    fam.update(tally)
    fam["_esc_yes"] += escaped[0]
    fam["_esc_tot"] += escaped[1]

for module in (eng_base, eng_defaulttags, eng_i18n):
    module.render_value_in_context = real

print()
print("%-9s %s" % ("family", "by exact type, summed over its rows"))
print("-" * 96)
for fam, counts in per_family.items():
    total = sum(v for k, v in counts.items() if not k.startswith("_"))
    nonstr = total - counts.get("str", 0)
    print(
        "%-9s total %4d   non-str %4d (%.0f%%)   str %4d   escaping changed %d/%d"
        % (
            fam,
            total,
            nonstr,
            100 * nonstr / total if total else 0,
            counts.get("str", 0),
            counts["_esc_yes"],
            counts["_esc_tot"],
        )
    )
    print(
        "          %s"
        % ", ".join(
            f"{k} {v}" for k, v in sorted(counts.items()) if not k.startswith("_")
        )
    )
