#!/usr/bin/env python
"""Is `".".join(self.lookups)` a faithful input to `parse_variable`?

M19 §1.3a's cheap win is to stop re-joining: engine/base.py:1033 built
`".".join(self.lookups)` and handed it to `_native.resolve`, which re-parsed it back
into the same components one frame later. The spec prices that at 0.22 us per
variable. This probe asks the prior question — **is the round trip exact?** — because
a cheap win that changes behaviour is not a win.

**Present tense in the paragraph above only as of the probe's own date.** The join
it cites has since been removed — step 1 landed the component handover, so at
`e139d89` no `".".join(self.lookups)` remains in `engine/base.py` and the number
`1033` there names no such line. This file is the record of the question, not of
the shipped code; the shipped state is the commit.

**It is not exact, and this probe found a reachable render divergence because of
it.** `1.3a`'s premise survives the finding — the repair is still to stop re-joining —
but it now carries a second reason beside the 0.22 us, and the second one is the
load-bearing one.

Four sections:

  1. **The path condition.** `_resolve_lookup` is reached only when
     `self.lookups is not None`. Every spelling that reaches it is listed with the
     `lookups` Django holds, so the population under test is visible rather than
     assumed.
  2. **The round trip, componentwise.** For each such spelling,
     `parse_variable(".".join(lookups))` is compared to `lookups`. Over the
     components it is exact, on every row.
  3. **The round trip, at the dispatch.** `parse_variable` is not only a splitter:
     it classifies. Re-parsing a *path* runs `parse_variable`'s pre-split numeric
     branch over a string that is no longer a source token — and for the
     trailing-dot numerals (`crates/template-core/src/parser/variable.rs:66-73`)
     that branch assigns a literal **and** sets `numeric = false`, so the re-parse
     yields a literal *and* the same lookups. `Variable.resolve` dispatches
     `lookups` first; `_native.resolve` dispatches `literal` first. Both are set, so
     the two disagree.
  4. **The falsifier.** The disagreement is not academic: rendered, stock prints `''`
     where this engine prints `2.0`. A row that only compared the components would be
     green on a template that renders differently.

**Two `Variable` classes are live here and they are not the same object.** The left
column of every table is `django.template.base.Variable` — stock Django's, which is
what `_resolve_lookup` lives on, and the thing whose `self.lookups` the shortcut would
pass across. The right column is `_native.parse_variable`'s return, which is *our*
`Variable` (`crates/template-py/src/lib.rs:776`): a `#[pyclass]` with `.var`,
`.literal`, `.lookups` and `.translate` as attributes, not a dict. A probe that read
`.lookups` off the wrong one would compare a type to itself.

`parse_variable`'s return is printed with `{v!r}`, never with `type(v).__name__`, so a
transcript line names an input that can be pasted back into a shell.

Run:  .venv/bin/python -I .review/m19-measurements/lookup_roundtrip.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

from django.template.base import Variable  # noqa: E402
from django.template.context import Context as StockContext  # noqa: E402
from django.template.engine import Engine as StockEngine  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.engine.context import Context as RustContext  # noqa: E402
from django_rust_templates.engine.engine import Engine as RustEngine  # noqa: E402

# The spellings, chosen for the branch each one takes in `Variable.__init__` rather
# than for realism: a plain path, a numeric component, a path with the separator
# doubled, a literal, a number, the `_(...)` translate wrapper, and the three that
# raise. `Variable` is constructed inside a `try` per row, so a raise is a printed
# outcome and not the end of the run.
CASES = [
    "a",
    "a.b",
    "a.b.c",
    "a.0",
    "a.0.b",
    "a.b.c.d.e",
    "a..b",
    ".",
    '"quoted"',
    "'quoted'",
    "12",
    "1.5",
    "2.",
    "2.e3",
    "0.0.",
    "_(a.b)",
    "_a",
    "a._b",
    "a+b",
    "a-b",
]

print("interpreter : %s" % sys.version.split()[0])
print("extension   : %s" % Path(_native.__file__).name)
print(
    "sha256      : %s"
    % hashlib.sha256(Path(_native.__file__).read_bytes()).hexdigest()[:16]
)
print()

# --- 1. and 2. the path condition and the componentwise round trip ----------------


print("1-2. what `Variable` holds, and whether the components round-trip")
print()
print(
    "   %-12s %-9s %-24s %-24s %s"
    % ("spelling", "reaches?", "Variable.lookups", "parse(join).lookups", "same?")
)
print("   " + "-" * 94)

reaches, total, bad = 0, 0, []
for raw in CASES:
    total += 1
    try:
        variable = Variable(raw)
    except Exception as exc:
        print("   %-12r %-9s %s: %s" % (raw, "no", type(exc).__name__, exc))
        continue
    lookups = variable.lookups
    if lookups is None:
        # A literal or a number: `resolve` never calls `_resolve_lookup`, so the
        # join is not on this row's path at all.
        print(
            "   %-12r %-9s %-24s %-24s %s"
            % (raw, "literal", f"None (literal={variable.literal!r})", "-", "-")
        )
        continue
    reaches += 1
    joined = ".".join(lookups)
    parsed = _native.parse_variable(joined)
    reparsed = tuple(parsed.lookups) if parsed.lookups is not None else None
    same = reparsed == tuple(lookups)
    if not same:
        bad.append((raw, lookups, reparsed))
    print(
        "   %-12r %-9s %-24r %-24r %s"
        % (raw, "yes", lookups, reparsed, "yes" if same else "**NO**")
    )

print()
print(
    f"   {reaches} of {total} spellings reach `_resolve_lookup`; {len(bad)} lose a "
    f"component in the join"
)
print()

# --- 3. the round trip at the dispatch --------------------------------------------


print("3. what the re-parse classifies the joined path as, and who wins")
print()
print(
    "   %-12s %-24s %-10s %-10s %-14s %s"
    % ("joined path", "(from)", "literal", "lookups", "stock dispatch", "ours dispatch")
)
print("   " + "-" * 94)

stock_order = "lookups first"  # `Variable.resolve`: `if self.lookups is not None`
mismatched = []
for raw in CASES:
    try:
        variable = Variable(raw)
    except Exception:
        continue
    lookups = variable.lookups
    if lookups is None:
        continue
    joined = ".".join(lookups)
    desc = _native.parse_variable(joined)
    literal = desc.literal
    got_lookups = tuple(desc.lookups) if desc.lookups is not None else None
    # `_native.resolve` is `if translate ... if literal ... if lookups ...`
    # (`crates/template-py/src/lib.rs:2591-2614`).
    ours_pick = "translate" if desc.translate else ("literal" if literal is not None else "lookups")
    theirs_pick = "translate" if variable.translate and got_lookups is None else "lookups"
    flag = ""
    if literal is not None and got_lookups is not None:
        flag = "  <-- both set"
        mismatched.append((raw, joined, literal, got_lookups))
    print(
        "   %-12r %-24r %-10s %-10s %-14s %s%s"
        % (joined, raw, repr(literal), got_lookups, stock_order, ours_pick, flag)
    )

print()
if mismatched:
    print("   Paths where the re-parse leaves both a literal and lookups, so the two")
    print("   dispatch orders disagree:")
    for raw, joined, literal, got_lookups in mismatched:
        print(f"      {raw!r} -> {joined!r}   literal={literal!r} lookups={got_lookups!r}")
    print()
    print("   `Variable.resolve` reads `lookups` first, so it does the lookup;")
    print("   `_native.resolve` reads `literal` first, so it returns the number.")
else:
    print("   No joined path carries both. The dispatch orders cannot disagree here.")
print()

# --- 4. the falsifier: a template that renders differently ------------------------


print("4. rendered: the same template through both engines")
print()
print("   The two engines' `locmem` loaders and context classes differ; each side is")
print("   built the way `bench.py` builds its own column, so the comparison is of")
print("   engines and not of harnesses.")
print()

DATA = {"x": "X", "2": "two", "a": {"b": "B"}}
# The trailing-dot numerals are the population from §3, plus the neighbours that
# must NOT move: a real number, an exponent form, and a variable whose first
# component happens to be a digit.
SOURCES = [
    "{{ 2. }}",
    "{{ 0. }}",
    "{{ 123. }}",
    "{{ x }}{{ 2. }}",
    "{{ 2. }}|{{ 2 }}",
    "{{ 2.}}{% if 2. %}y{% endif %}",
    "{{ 2.0 }}",
    "{{ 2.e3 }}",
    "{{ 0.0. }}",
    "{{ a.b }}",
]


def render(engine_cls, loader, context_cls, source):
    engine = engine_cls(loaders=[(loader, {"page.html": source})], libraries={})
    try:
        return engine.get_template("page.html").render(context_cls(dict(DATA)))
    except Exception as exc:  # a refusal is a result here, not a crash
        return "<%s: %s>" % (type(exc).__name__, exc)


print("   %-34s %-14s %-14s %s" % ("template", "stock", "ours", "verdict"))
print("   " + "-" * 78)
divergent = 0
for source in SOURCES:
    theirs = render(
        StockEngine, "django.template.loaders.locmem.Loader", StockContext, source
    )
    ours = render(
        RustEngine,
        "django_rust_templates.engine.loaders.locmem.Loader",
        RustContext,
        source,
    )
    same = ours == theirs
    divergent += 0 if same else 1
    print(
        "   %-34r %-14r %-14r %s"
        % (source, theirs, ours, "same" if same else "**DIFFERENT**")
    )
print()
print(f"   {divergent} of {len(SOURCES)} templates render differently")
print()
print(
    f"   **This section read 6 of 10 before the fix and reads {divergent} of"
    f" {len(SOURCES)} after it.**\n"
    "   The six were every trailing-dot numeral. The file is a transcript of the\n"
    "   tree as it stands, so a re-run diffing against it must expect the post-fix\n"
    "   numbers here: the finding is the divergence, not the row."
)
print()

# --- 5. does the acceptance suite cover it? ---------------------------------------


print("5. is any of this in the 1547?")
print()
SUITE = ROOT / "django_suite" / "template_tests"
if not SUITE.is_dir():
    raise SystemExit(f"refusing to search {SUITE}: not a directory")
import re  # noqa: E402

# A `{{ ... }}` holding a bare digits-then-dot numeral. Deliberately narrow: it is
# the shape §3 says is broken and nothing wider, so a hit is a hit.
needle = re.compile(r"\{\{[^}]*?(?<![\d.])\d+\.[^}\d]")
hits = 0
walked = 0
for path in sorted(SUITE.rglob("*")):
    if not path.is_file() or path.suffix not in (".py", ".html"):
        continue
    walked += 1
    for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if needle.search(line):
            hits += 1
            print("   %s:%d | %s" % (path.relative_to(ROOT), number, line.strip()[:88]))
print(
    "   %d files walked in %s; %d lines match a trailing-dot numeral in a `{{ }}`"
    % (walked, SUITE.relative_to(ROOT), hits)
)
if not hits:
    print("   So the suite does not cover it: this divergence is one the 1547 cannot see.")
print()
