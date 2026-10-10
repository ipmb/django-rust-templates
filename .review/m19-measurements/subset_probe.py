#!/usr/bin/env python
"""Which constructs all four engines render byte for byte.

`bench_corpus.py` says the four-engine corpus is an *intersection*, and that the
intersection was measured one construct at a time rather than assumed. This is
that measurement, and it is the licence for every exclusion the corpus makes: a
row that is not shared is not shared because of one of the five findings below,
each of which is printed with the bytes that produce it.

The method is deliberately the dumbest one that answers the question: render the
same source string through all four engines and compare the bytes. No
normalisation, no "semantically equal" — the corpus's claim is byte equality, so
byte equality is what is tested.

Run:  .venv/bin/python -I .review/m19-measurements/subset_probe.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_engines  # noqa: E402

# One construct per case, with the data it needs. The `why` is filled in by the
# run, not asserted here: this file's job is to report what the engines do.
CASES = [
    ("plain text", "<p>hello</p>", {}),
    ("one variable", "{{ a }}", {"a": "hello world"}),
    ("ampersand", "{{ a }}", {"a": "a & b"}),
    ("less-than", "{{ a }}", {"a": "<b>bold</b>"}),
    ("apostrophe", "{{ a }}", {"a": "it's"}),
    ("quotation mark", '{{ a }}', {"a": 'say "hi"'}),
    ("solidus", "{{ a }}", {"a": "a/b"}),
    ("dict key named items", "{{ row.items }}", {"row": {"items": "42"}}),
    ("dict key named keys", "{{ row.keys }}", {"row": {"keys": "42"}}),
    ("for with empty", "{% for x in xs %}{{ x }}{% empty %}none{% endfor %}", {"xs": []}),
    ("for with else", "{% for x in xs %}{{ x }}{% else %}none{% endfor %}", {"xs": []}),
    ("if with else", "{% if a %}yes{% else %}no{% endif %}", {"a": ""}),
    ("filter, no argument", "{{ a|upper }}", {"a": "abc"}),
    ("filter, an argument", "{{ a|floatformat:2 }}", {"a": 1.5}),
    ("filter, paren argument", "{{ a|floatformat(2) }}", {"a": 1.5}),
    ("chained lookup, defined", "{{ a.b.c }}", {"a": {"b": {"c": "deep"}}}),
    ("chained lookup, undefined", "{{ nope.deeper }}", {}),
    ("if with comparison", "{% if a > 1 %}yes{% endif %}", {"a": 2}),
    ("loop variable", "{% for x in xs %}{{ x }}{% endfor %}", {"xs": [1, 2, 3]}),
    ("comment", "{# note #}x", {}),
    ("nested loop", "{% for a in xs %}{% for b in a %}{{ b }}{% endfor %}{% endfor %}",
     {"xs": [[1, 2]]}),
]

print("engines:", ", ".join(bench_engines.ENGINES))
missing = bench_engines.missing_engines()
if missing:
    print("missing:", ", ".join(missing), "-", bench_engines.INSTALL_HINT)
print()
print("%-28s %-34s %s" % ("case", "django renders", "verdict"))
print("-" * 100)

shared, excluded = [], []
for label, source, data in CASES:
    out = {}
    for name in bench_engines.ENGINES:
        # The build is inside the `try` on purpose: minijinja refuses a source it
        # cannot parse at `add_template` time, which is *construction*, not
        # render — so a probe that only guards the render reports a crash for a
        # construct that is simply outside that engine's grammar.
        try:
            engine = bench_engines.build(name, {"page.html": source}, {})
        except Exception as exc:
            out[name] = f"<{type(exc).__name__} at build: {exc}>"
            continue
        try:
            try:
                handle = engine.from_string(source)
                rendered = engine.render(handle, engine.context(dict(data)))
            except Exception as exc:  # the engine's refusal is the finding
                rendered = f"<{type(exc).__name__}: {exc}>"
            out[name] = rendered
        finally:
            engine.close()
    control = out["django"]
    agree = [n for n in bench_engines.ENGINES if out[n] == control]
    differs = [n for n in bench_engines.ENGINES if n != "django" and out[n] != control]
    verdict = "all four agree" if not differs else "differs: " + ", ".join(differs)
    print("%-28s %-34s %s" % (label, repr(control)[:34], verdict))
    for name in differs:
        print("%-28s   %-32s %s" % ("", name, repr(out[name])[:70]))
    (shared if not differs else excluded).append((label, differs, out))

print()
print("shared with all four: %d of %d" % (len(shared), len(CASES)))
print("excluded            : %d" % len(excluded))
