"""Which filter arguments the walk carries, and where the corpus has the ones it does not.

The render walk plans a filtered `{{ … }}` (`plan_emit` → `plan_steps`) and applies
each filter body itself.  `plan_steps` reads Django's own argument pair,
`(lookup, value)` (`base.py:766-773`), and takes two shapes: `lookup=False` is a
constant (`mark_safe(arg)`), `lookup=True` is `Variable(var_arg)` resolved against
the context per render.  **A third shape exists and it is not carried**: a bare
number in the argument position matches `var_arg` — `var_chars` is `\\w\\.\\+-`
(`base.py:714`), digits included — so Django stores `(True, Variable("2"))`, and
`Variable.__init__` makes that a *literal*: `self.literal = int("2")` with
`lookups` left `None` (`base.py:887-895`), which `resolve` returns directly
(`value = self.literal`, `base.py:929-931`).  The comment that says why is
`base.py:686-687`: *"Numbers are treated as variables for implementation reasons
(so that they retain their type when passed to filters)"*.

`plan_steps`'s `lookup` arm read `value.lookups` and handed the node back when it
was `None` — which is this shape and only this shape, since a quoted argument
takes the `constant_arg` alternative and anything else has lookups.  So the node
was delegated, the output was right, and nothing failed.  That is the hole, and
this file was written to measure it before it was closed.

**The state now: closed, and the ROUTES table is the gate that keeps it closed.**
`plan_steps` reads the third shape and carries the literal
(`FilterArg::Literal`), so every bare-number row below reads `handled`.  A
regression — the arm reverting to `lookups.is_none() → return Ok(None)` — shows up
here as that row flipping back to `delegated`, which is exactly how the hole was
found.  `handle._native_plan` is the template-level stop and `delegated` the
node-level one, and they answer different questions, so both are printed.

Two questions, two halves of this file:

  * **ROUTES** — for each argument shape, does the walk take the node, and do the
    bytes match stock's?  The route is read off the census (`_native.census_take`)
    and off `handle._native_plan`; the bytes are stock Django's render of the same
    source against ours.
  * **SWEEP** — how many of the corpus's filter arguments are of each shape, and
    which rows hold the numeric ones?  Classification uses Django's own
    `filter_re` and Django's own `Variable`, i.e. exactly the rule
    `FilterExpression.__init__` applies, rather than a re-reading of it.  Every
    argument classified as the numeric shape is *printed*, because a count with no
    list is how the row named `django-only/filter-arg` came to report coverage the
    walk did not have.

**What the route column can and cannot see.**  `delegated` counts the walk's own
visits, so a delegated `VariableNode` here is one node and on these one-expression
templates that is the whole template.  That is fine for asking "did the walk take
it" and it is *not* fine for asking "is the corpus covered": a numeric argument
inside a `{% block %}` reads `delegated` for a reason that has nothing to do with
its shape — the `BlockNode` above it was delegated whole.  `django-only/realistic`
is that case, which is why the argument census below and the coverage census in
`node_census.py` disagree about it and both are right.

Run:  .venv/bin/python -I .review/m19-measurements/filter_arg_route.py
"""

import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

from django.template import defaultfilters as builtin_filters  # noqa: E402
from django.template.base import (  # noqa: E402
    FilterExpression,
    TemplateSyntaxError,
    Variable,
    filter_re,
)
from django.templatetags import cache, i18n, l10n, static, tz  # noqa: E402

ITERS = 20

# (label, source, data).  Every row is one `{{ … }}` and one filter argument, so
# the census's `delegated` list is the answer to "did the walk take it".
ROUTES = [
    ("floatformat:2 (bare number)", "{{ x|floatformat:2 }}", {"x": 1.2345}),
    ("floatformat:\"2\" (quoted)", "{{ x|floatformat:\"2\" }}", {"x": 1.2345}),
    ("floatformat:n (variable)", "{{ x|floatformat:n }}", {"x": 1.2345, "n": 2}),
    ("floatformat:2.5 (bare float)", "{{ x|floatformat:2.5 }}", {"x": 1.2345}),
    ("truncatechars:3 (bare number)", "{{ x|truncatechars:3 }}", {"x": "abcdefg"}),
    ("truncatechars:\"3\" (quoted)", "{{ x|truncatechars:\"3\" }}", {"x": "abcdefg"}),
    ("add:2 (bare number)", "{{ x|add:2 }}", {"x": 5}),
    ("add:\"2\" (quoted)", "{{ x|add:\"2\" }}", {"x": 5}),
    ("add:-2 (signed number)", "{{ x|add:-2 }}", {"x": 5}),
    ("cut:\"a\" (quoted)", "{{ x|cut:\"a\" }}", {"x": "banana"}),
    ("slice:\":2\" (quoted)", "{{ x|slice:\":2\" }}", {"x": "abcdefg"}),
    ("floatformat:2 inside a loop", "{% for r in rows %}{{ r|floatformat:2 }}{% endfor %}", {"rows": [1.2345, 2.3456]}),
]


def census_render(engine, handle, context):
    """Route, the walk's own visit counts and the output, for one render."""
    from django_rust_templates import _native

    engine.render(handle, context)  # warm the loader cache
    _native.census_start()
    try:
        out = None
        for _ in range(ITERS):
            out = engine.render(handle, context)
        handled, delegated, _, filters, body_crossings = _native.census_take()
    except BaseException:
        _native.census_take()
        raise
    return (
        [(name, n / ITERS) for name, n in handled],
        [(name, n / ITERS) for name, n in delegated],
        [(name, n / ITERS) for name, n in filters],
        body_crossings / ITERS,
        out,
    )


def fmt(rows):
    return "-" if not rows else " ".join(f"{name}={n:g}" for name, n in rows)


def routes():
    print("=== routes: does the walk take the node, and do the bytes agree ===\n")
    print(
        "`template` is the template-level stop (`plan is None` → `NodeList.render`) and\n"
        "`node` is the node-level one (`Act::Delegate` for this node).  They are two\n"
        "different answers and a row where they disagree is a walked template that\n"
        "walked nothing.\n"
    )
    print(f"{'argument':38} {'template':9} {'plan':5} {'node':10} {'delegated':20} {'applied':20} {'xings':6} bytes")
    for label, source, data in ROUTES:
        templates = {"page.html": source}
        ours = bench_engines.build("rust", templates, {})
        stock = bench_engines.build("django", templates, {})
        try:
            handle = ours.get("page.html")
            context = ours.context(data)
            handled, delegated, filters, xings, out = census_render(ours, handle, context)
            reference = stock.render(stock.get("page.html"), stock.context(data))
            plan = getattr(handle, "_native_plan", None)
            plan_nodes = "-" if plan is None else str(len(handle.nodelist))
        finally:
            ours.close()
            stock.close()
        template = "declined" if plan is None else "walked"
        node = "delegated" if delegated else "handled"
        agrees = "same" if out == reference else f"DIFFERS ({out!r} vs {reference!r})"
        print(
            f"{label:38} {template:9} {plan_nodes:>5} {node:10} "
            f"{fmt(delegated):20} {fmt(filters):20} {xings:<6g} {agrees}"
        )


TOKEN_RE = re.compile(r"{{(.*?)}}", re.S)


def arguments(source):
    """Every `(filter_name, arg_text, kind)` in `source`, by Django's own rule.

    `filter_re` is Django's regex and `Variable` is Django's class, so this
    classifies an argument the way `FilterExpression.__init__` does
    (`base.py:764-773`): the `constant_arg` alternative first, then `var_arg`, and
    a `var_arg` whose `lookups` is `None` is a literal — a number, since that is
    the only way `Variable.__init__` reaches `literal` with no lookups.

    The token is **stripped** before matching, which is what Django does with it:
    `VariableNode` builds the expression from `token.split_contents()[1:]`, so the
    spaces inside `{{ … }}` never reach `filter_re` — and its first two
    alternatives are `^`-anchored, so a leading space matches nothing at all.
    """
    found = []
    for token in TOKEN_RE.findall(source):
        token = token.strip()
        upto = 0
        while upto < len(token):
            match = filter_re.match(token, upto)
            if not match:
                break
            if match["filter_name"]:
                if match["constant_arg"]:
                    found.append((match["filter_name"], match["constant_arg"], "constant"))
                elif match["var_arg"]:
                    arg = match["var_arg"]
                    variable = Variable(arg)
                    kind = "numeric literal" if variable.lookups is None else "variable"
                    found.append((match["filter_name"], arg, kind))
            upto = match.end()
    return found


def sweep():
    print("\n=== sweep: every filter argument in the corpus, by shape ===\n")
    counts = {"constant": 0, "variable": 0, "numeric literal": 0}
    numeric = []
    raw = []
    for workload in bench_corpus.workloads():
        for name, source in workload.templates.items():
            for filter_name, arg, kind in arguments(source):
                counts[kind] += 1
                if kind == "numeric literal":
                    numeric.append((workload.key, name, filter_name, arg))
            # The scan's own control: `|name:arg` by a regex that knows nothing
            # about Django's grammar.  A classified total below this one is the
            # scan dropping arguments, which would make the list above a floor
            # rather than a census.
            raw += [
                (workload.key, name, hit)
                for hit in re.findall(r"\|\w+:[^\s|}]*", source)
            ]
    print(f"{'row':26} {'template':18} {'filter':16} arg")
    for key, name, filter_name, arg in numeric:
        print(f"{key:26} {name:18} {filter_name:16} {arg}")
    print(f"\narguments: {counts['constant']} constant, {counts['variable']} variable, "
          f"{counts['numeric literal']} numeric literal  ({sum(counts.values())} total)")
    print(f"  control, `\\|name:arg` by plain regex: {len(raw)} — "
          f"{'agree' if len(raw) == sum(counts.values()) else 'DISAGREE'}")
    for key, name, hit in raw:
        print(f"    {key:26} {name:18} {hit}")


def confirm():
    """The same arguments as real `FilterExpression` objects, not as the scan read them.

    The lookup is the row's own filters — Django's builtins plus whatever the row
    `{% load %}`s — so the pair printed here is the object `plan_steps` declines,
    typed by the pinned Django rather than by this file.  `scan` beside it is the
    text scan's verdict, so the two are checkable against each other.
    """
    print("\n=== confirm: the real `(lookup, arg)` pair for each numeric argument ===\n")
    named = {"django-only/filter-arg", "django-only/realistic"}
    filters = dict(builtin_filters.register.filters)
    for module in (i18n, l10n, tz, static, cache):
        filters.update(module.register.filters)
    parser = SimpleNamespace(find_filter=filters.__getitem__)
    for workload in bench_corpus.workloads():
        if workload.key not in named:
            continue
        for name, source in workload.templates.items():
            for filter_name, arg, kind in arguments(source):
                if kind == "variable":
                    continue
                try:
                    expression = FilterExpression(f"x|{filter_name}:{arg}", parser)
                except (TemplateSyntaxError, KeyError):
                    continue
                for func, args in expression.filters:
                    for lookup, value in args:
                        print(
                            f"{workload.key:26} {name:18} {filter_name}:{arg:10} scan={kind:15} "
                            f"lookup={lookup!r} arg={type(value).__name__} "
                            f"literal={value.literal!r} lookups={value.lookups!r}"
                        )


if __name__ == "__main__":
    routes()
    sweep()
    confirm()
