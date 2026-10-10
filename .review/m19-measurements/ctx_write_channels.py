"""Which Python routes into a *sealed* loop body can write the context?

The adversarial reviewer's second round returned `VERDICT: FAIL` with two
BLOCKING findings against the `may_write_context` fix at `35ff15c`, and both name
a route the predicate's docstring had argued away:

* the **sequence** of a nested `{% for %}` — a `FilterExpression` resolved in
  Python (`lib.rs:4799`, `.call_method1("resolve", (&self.context, true))`) — can
  write the enclosing loop's variable, and `may_write_context` descends into a
  nested `Act::For`'s body and `empty` but never looks at its sequence;
* a **filter** can receive the context object as an explicit argument, which
  `FilterExpression.resolve` passes like any other argument, so "a filter cannot
  write the context" is false as stated.

This probe is the measurement for both, plus the two routes I would have to
argue about otherwise: an `{% include %}` in the inner body, and whether the
*frame* route's missing `context.push()` is observable on its own
(`len(ctx.dicts)`).

Every row is rendered by both engines from the same `Context` object with the
context stored in itself under `ctx`, and printed with `!r`.  The artifact sha
is printed first: a divergence is a claim about a build.

The filters are `tests/differential/ctxwrite_library.py` **in the tree**, not a
scratch module beside this probe: the record cites these rows, and a citation
that needs a directory under `/tmp` (or a job's own) is reproducible only until
that directory goes away.

Run:  .venv/bin/python -I <this>
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "differential"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

from django.template import Context, Engine  # noqa: E402

from django_rust_templates import _native, engine as rust_engine  # noqa: E402

print("artifact sha256", hashlib.sha256(open(_native.__file__, "rb").read()).hexdigest()[:16])

LIBS = {"ctxwrite": "ctxwrite_library"}

# `{% load ctxwrite %}` first in every row, so the library is registered on both
# sides identically and the LoadNode is a delegate in both.
CASES = [
    # --- the sequence of a nested loop --------------------------------------
    (
        "1  nested seq, set_upward, xs empty",
        "{% load ctxwrite %}{% for s in sections %}{% for x in xs|rebind_up:ctx %}{% endfor %}{{ s }}{% endfor %}",
        {"sections": ["old"], "xs": []},
    ),
    (
        "2  nested seq, set_upward, xs=['z']",
        "{% load ctxwrite %}{% for s in sections %}{% for x in xs|rebind_up:ctx %}{{ x }}{% endfor %}{{ s }}{% endfor %}",
        {"sections": ["old"], "xs": ["z"]},
    ),
    (
        "3  nested seq, plain assignment (push order)",
        "{% load ctxwrite %}{% for s in sections %}{% for x in xs|rebind_item:ctx %}{% endfor %}{% firstof s %}{% endfor %}",
        {"sections": ["old"], "xs": []},
    ),
    (
        "4  nested seq, write into a pushed+popped layer",
        "{% load ctxwrite %}{% for s in sections %}{% for x in xs|pop_layer:ctx %}{% endfor %}{% firstof s %}{% endfor %}",
        {"sections": ["old"], "xs": []},
    ),
    # --- the same write from an Emit ----------------------------------------
    (
        "5  emit filter, set_upward, then read",
        "{% load ctxwrite %}{% for s in sections %}{{ xs|rebind_up:ctx }}{{ s }}{% endfor %}",
        {"sections": ["old"], "xs": []},
    ),
    (
        "6  emit filter, plain assignment, then read",
        "{% load ctxwrite %}{% for s in sections %}{{ xs|rebind_item:ctx }}[{{ s }}]{% endfor %}",
        {"sections": ["old"], "xs": []},
    ),
    # --- observation rather than a write ------------------------------------
    (
        "7  layer depth seen by a filter (no write)",
        "{% load ctxwrite %}{% for s in sections %}{{ xs|ctxdepth:ctx }}{% endfor %}",
        {"sections": ["old"], "xs": []},
    ),
    (
        "8  layer depth, nested loop (no write)",
        "{% load ctxwrite %}{% for s in sections %}{% for x in xs|ctxdepth:ctx %}{% endfor %}{% endfor %}",
        {"sections": ["old"], "xs": []},
    ),
    # --- controls: no filter, and a filter that writes nothing --------------
    (
        "9  control: pure nested loop",
        "{% for s in sections %}{% for l in s %}{{ l }}{% endfor %}[{{ s }}]{% endfor %}",
        {"sections": ["old"]},
    ),
    (
        "10 control: nested seq is a plain name",
        "{% load ctxwrite %}{% for s in sections %}{% for x in xs %}{% endfor %}[{{ s }}]{% endfor %}",
        {"sections": ["old"], "xs": []},
    ),
    # --- the push order, observed rather than written ------------------------
    (
        "11 top-level seq, plain assignment (no nesting)",
        "{% load ctxwrite %}{% for x in xs|rebind_item:ctx %}{% endfor %}[{{ s }}]",
        {"sections": ["old"], "xs": []},
    ),
    (
        "12 depth seen by a top-level loop's sequence",
        "{% load ctxwrite %}{% for x in ab|ctxdepth:ctx %}{{ x }}{% endfor %}",
        {"sections": ["old"], "ab": "12"},
    ),
    (
        "13 depth seen by a nested loop's sequence",
        "{% load ctxwrite %}{% for s in sections %}{% for x in ab|ctxdepth:ctx %}{{ x }}{% endfor %}{% endfor %}",
        {"sections": ["old"], "ab": "12"},
    ),
    (
        "14 depth seen by an {% empty %} loop's sequence",
        "{% load ctxwrite %}{% for x in xs|ctxdepth:ctx %}{{ x }}{% endfor %}",
        {"sections": ["old"], "xs": []},
    ),
    # --- the residual class: a *value* holding the context ------------------
    (
        "15 emit of another name whose property writes",
        "{% load ctxwrite %}{% for s in sections %}{{ holder.s }}{{ s }}{% endfor %}",
        {"sections": ["old"], "holder": "__HOLDER__"},
    ),
    (
        "16 loop-variable attribute whose property writes",
        "{% load ctxwrite %}{% for s in holders %}{{ s.title }}{{ s }}{% endfor %}",
        {"sections": ["old"], "holders": "__HOLDERS__"},
    ),
    (
        "17 the loop variable's own __str__ writes",
        "{% load ctxwrite %}{% for s in holders %}{{ s }}{{ s }}{% endfor %}",
        {"sections": ["old"], "holders": "__HOLDERS__"},
    ),
]


class Holder:
    """A value holding the context — the class no plan can see."""

    def __init__(self, context, name):
        self._context = context
        self._name = name

    @property
    def s(self):
        self._context.set_upward("s", "X")
        return "Z"

    @property
    def title(self):
        self._context.set_upward("s", "X")
        return "T"

    def __str__(self):
        self._context.set_upward("s", "X")
        return "H"

tpl = {"p.html": "{{ s }}"}
stock = Engine(libraries=LIBS, loaders=[("django.template.loaders.locmem.Loader", tpl)])
ours = rust_engine.Engine(libraries=LIBS, loaders=[("django.template.loaders.locmem.Loader", tpl)])

diffs = 0
for name, src, data in CASES:
    results = []
    for engine in (stock, ours):
        context = Context({k: v for k, v in data.items() if not str(v).startswith("__")})
        if data.get("holder") == "__HOLDER__":
            context["holder"] = Holder(context, "s")
        if data.get("holders") == "__HOLDERS__":
            context["holders"] = [Holder(context, "s")]
        context["ctx"] = context
        try:
            results.append(engine.from_string(src).render(context))
        except Exception as exc:  # noqa: BLE001
            results.append(f"<{type(exc).__name__}: {exc}>")
    same = results[0] == results[1]
    diffs += not same
    print(f"{name:44} {'ok  ' if same else 'DIFF'} stock={results[0]!r:12} ours={results[1]!r}")

print()
print(f"{diffs} of {len(CASES)} DIFF")
