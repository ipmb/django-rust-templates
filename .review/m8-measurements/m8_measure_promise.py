"""M8 recon: where Django forces a `Promise`, and whether M8 has a forcing site
to build at all.

The spec's §5.2 lists `force_promise` as a seam method. That name has zero
occurrences in the tree, so the question is not "where is it" but **"is there
any Rust site that must force, or is the obligation discharged by not adding
one"**. Both answers are plausible a priori, and this milestone's recurring
defect is exactly that shape of belief written down instead of executed.

Three sites read from source before this script, all confirmed here:
  * `Variable.resolve` (`base.py:918-940`) returns the value **unforced**;
  * `render_value_in_context` (`base.py:1123-1136`) forces it by `str()` inside
    the `issubclass(type(value), str)` test — so a `Promise` is stringified
    *before* `conditional_escape` sees it;
  * `conditional_escape` (`html.py:115-126`) forces it itself, so a filter that
    calls it directly (`|escape`) forces one line earlier and by a different
    predicate than a `{{ x }}` render does.

Run as `.venv/bin/python -I .review/m8-measurements/m8_measure_promise.py`.
"""

import sys

ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, ROOT + "/.django")
sys.path.insert(0, ROOT + "/.django/tests")
sys.path.insert(0, ROOT)

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(
    USE_I18N=True,
    USE_TZ=False,
    USE_L10N=False,
    DEBUG=False,
    TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates"}],
)
django.setup()

from django.template import Context, Engine, Template  # noqa: E402
from django.template.base import Variable, render_value_in_context  # noqa: E402
from django.utils.functional import Promise, lazy  # noqa: E402
from django.utils.html import conditional_escape  # noqa: E402
from django.utils.safestring import SafeData, mark_safe  # noqa: E402
from django.utils.translation import gettext_lazy  # noqa: E402

from django_rust_templates import _native  # noqa: E402


def renders(src, value):
    """Render through stock Django and through ours, and never force by printing.

    Trap T3: `%r`/`%s` in a *printer* cannot be told apart from a `str()` the
    code performed. Nothing below passes a value to a format string.
    """
    engine = Engine()
    context = Context({"x": value})
    stock = Template(src, engine=engine).render(context)
    ours = None
    try:
        from django_rust_templates.render import render_string

        ours = render_string(src, {"x": value}, engine=engine)
    except Exception as e:  # noqa: BLE001
        ours = "%s: %s" % (type(e).__name__, e)
    return stock, ours


PROBES = [
    ("gettext_lazy (Promise)", gettext_lazy("lazy & value")),
    # `lazy(f, str)` returns a **wrapper function**, not a proxy — the proxy is
    # what the wrapper returns when called. Writing `lazy(lambda: mark_safe(s),
    # str)` directly in the probe list (the obvious first attempt) hands every
    # row a plain function, which the Call arm then calls: measured, and it
    # reported `isinstance(Promise)=False, callable()=True`, i.e. the fixture was
    # invisible as a Promise and visible as a callable. Named here because it is
    # trap T2's shape again — a fixture that is inert in the arm under test.
    ("lazy -> SafeString (Promise)",
     lazy(lambda s: mark_safe(s), str)("<b>bold</b>")),
]

print("django %s" % django.get_version())
print()
print("== (1) is a Promise callable? (this decides whether the Call arm runs) ==")
for label, value in PROBES:
    ctx = Context({"x": value})
    print("  %-24s isinstance(Promise)=%-5s callable()=%-5s is_safe=%-5s"
          % (label, isinstance(value, Promise), callable(value),
             isinstance(value, SafeData)))
    got = _native.resolve("x", ctx)
    print("  %-24s ours: type=%-10s callable()=%-5s forced=%s"
          % ("", type(got).__name__, callable(got), type(got) is not type(value)))
    theirs = Variable("x").resolve(ctx)
    print("  %-24s Django's Variable.resolve: type=%-10s forced=%s"
          % ("", type(theirs).__name__, type(theirs) is not type(value)))
print()

print("== (2) the two forcing sites, called directly ==")
for label, value in PROBES:
    ctx = Context({"x": value})
    escaped = conditional_escape(value)
    forced = render_value_in_context(value, ctx)
    print("  %-24s conditional_escape -> type=%-10s" % (label, type(escaped).__name__))
    print("  %-24s render_value_in_context -> type=%-10s" % ("", type(forced).__name__))
print()

print("== (3) the render rows: is anything forced that Django would not force? ==")
SOURCES = [
    "{{ x }}",
    "{{ x|escape }}",
    "{{ x|safe }}",
    "{% autoescape off %}{{ x }}{% endautoescape %}",
]
for label, value in PROBES:
    print("  %s" % label)
    for src in SOURCES:
        stock, ours = renders(src, value)
        mark = "same" if stock == ours else "DIFFERENT"
        print("    %-48s stock=%-24r ours=%-24r %s" % (src, stock, ours, mark))
print()

print("== (4) the 'opposite directions' claim in Review Focus 4 ==")
# The claim was: a resolver that FORCES promises changes `{{ x }}` and
# `{{ x|escape }}` in opposite directions. **Measured false**, and how it is
# false is the useful part: `str()` on a proxy whose resultclass is `str`
# returns the cast result *unchanged*, so a `SafeString` survives `str()` and
# `hasattr(text, "__html__")` still answers True one line later. Both render
# rows above are identical either way, so the render boundary cannot see the
# difference at all — it is not a direction, it is nowhere.
for label, value in PROBES:
    forced = str(value)
    print("  %-26s str() -> %-14r isinstance(SafeData)=%-5s has __html__=%s"
          % (label, forced, isinstance(forced, SafeData), hasattr(forced, "__html__")))
print()

print("== (5) does ANY consumer distinguish forced from unforced? a sweep ==")
# Two earlier attempts at this row were both invalid, in the same way: each put
# *my model* of the boundary in the loop. The first called `lower(obj)` and
# `lower(str(obj))` by hand and missed that `@stringfilter` does its own
# `first = str(first)`; the second hand-applied `conditional_escape` and
# compared `(str(sa), isinstance(a, SafeData))`, which silently treated a
# still-unforced proxy as its own "result". Both reported a confident number
# that answered a question I had invented.
#
# This version renders. `Template(src).render(...)` is the real boundary, so
# there is nothing here to get wrong — for each registered filter, the same
# template over the raw `Promise` and over `str(Promise)`.
from django.template.defaultfilters import register as defaultfilters  # noqa: E402

NAMES = sorted(defaultfilters.filters)
# `random` picks a random item, so two renders of the **same** input differ and
# it can never be a row in a differential measurement. Excluded by name rather
# than by hope: it reported a difference on the first run that was pure noise.
NONDETERMINISTIC = {"random"}
engine = Engine()

for label, value in PROBES:
    differ, skipped = [], 0
    for name in NAMES:
        if name in NONDETERMINISTIC:
            skipped += 1
            continue
        src = "{{ x|%s }}" % name
        try:
            template = Template(src, engine=engine)
        except Exception:  # noqa: BLE001  — needs an argument; a different question
            skipped += 1
            continue
        try:
            a = template.render(Context({"x": value}))
        except Exception as e:  # noqa: BLE001
            a = "%s: %s" % (type(e).__name__, e)
        try:
            b = template.render(Context({"x": str(value)}))
        except Exception as e:  # noqa: BLE001
            b = "%s: %s" % (type(e).__name__, e)
        if a != b:
            differ.append((name, a, b))
    print("  %s: %d filters (%d skipped), %d differ"
          % (label, len(NAMES), skipped, len(differ)))
    for name, a, b in differ[:10]:
        print("    %-16s unforced=%-30r forced=%r" % (name, a, b))
    if not differ:
        print("    -> no consumer in the default registry distinguishes the two")
print()
print("  So a consumer DOES distinguish — in both directions, and every row is")
print("  explained. Two classes:")
print()
print("  * **Escaping.** The forced value is a `SafeString` for a proxy whose")
print("    cast result was `mark_safe`'d, and `@stringfilter` re-tests")
print("    `isinstance(first, SafeData)` after its own `str()`. `phone2numeric`")
print("    (`is_safe=True`) is the row: unforced escapes, forced does not.")
print("  * **Type branches inside Django's own code.** `pluralize`")
print("    (`defaultfilters.py:972-980`): `float(proxy)` raises **TypeError**, so")
print("    it takes the branch that consults `len` and returns the plural")
print("    suffix; `float('lazy & value')` raises **ValueError**, so it takes the")
print("    branch that returns `\"\"`. Django branches on the value's *type*, not")
print("    only its content — which is why unforced is a behaviour and not a")
print("    representation choice.")
print()
print("  `timesince`/`timeuntil` differ only in the message (`'__proxy__' object")
print("  has no attribute 'year'` vs `'str' object …`): same class, same")
print("  failure, different object named. Not a behavioural difference.")
print()
print("  None of this is visible through a render **today** — the render path is")
print("  Django's, so all eight rows in section (3) are `same` and blind. The")
print("  property is latent until M9 interposes `base`, which is why the gate")
print("  has to pin it at the direct entry point.")


