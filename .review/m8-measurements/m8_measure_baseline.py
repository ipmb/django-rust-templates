"""M8 recon, measurement 1: what the engine does today with safe values,
promises and callables — the surface M8 is about to change.

Sections:
  1. `_native`'s public names, and which of the spec §5.2 `ValueResolver`
     methods exist at all;
  2. `resolve` against values that differ only in their Python protocol —
     a plain str, a `mark_safe` str, a lazy gettext, a bound method, a method
     on a class carrying `alters_data`, a function carrying `alters_data`,
     and one carrying `do_not_call_in_templates`;
  3. the same seven through a real render, ours vs stock — which is the row
     that matters, because §8's callable protocol is only observable there.

Both columns are reached through the **registry** (`register.filters[name]`),
never through `defaultfilters.<name>`: six registered names are shadowed by a
different module attribute (`m8_measure_filter_names.py`).

Reads the pinned tree and our binding. Writes nothing.
Run as `.venv/bin/python -I .review/m8-measurements/m8_measure_baseline.py`.
"""

import sys

ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, ROOT + "/.django")
sys.path.insert(0, ROOT)

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(USE_I18N=False, USE_TZ=False, USE_L10N=False, DEBUG=False)
django.setup()

from django.template import Context, Engine, Template  # noqa: E402
from django.utils.functional import Promise, lazy  # noqa: E402
from django.utils.safestring import SafeData, mark_safe  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.render import render_string  # noqa: E402

print("django %s" % django.get_version())
print()

# ---------------------------------------------------------------- 1. surface
print("== (1) the surface ==")
names = sorted(n for n in dir(_native) if not n.startswith("_"))
print("  _native public names (%d): %s" % (len(names), ", ".join(names)))
spec_52 = ["resolve", "force_promise", "is_safe", "is_callable", "call", "render"]
for name in spec_52:
    print("  spec §5.2 `%s`: %s" % (name, hasattr(_native, name)))
print()


# ---------------------------------------------------------------- the values
class AltersDataClassAttr:
    """`alters_data = True` as a *class* attribute."""

    alters_data = True

    def save(self):
        return "SIDE EFFECT"

    def name(self):
        return "ada"

    def __str__(self):
        return "obj"


class AltersDataFnAttr:
    """Same, but the flag sits on the *function*."""

    def save(self):
        return "SIDE EFFECT"
    save.alters_data = True

    def name(self):
        return "ada"

    def __str__(self):
        return "obj"


class DoNotCall:
    do_not_call_in_templates = True

    def name(self):
        return "ada"

    def __str__(self):
        return "dontcall"


class Plain:
    def name(self):
        return "ada"

    def __str__(self):
        return "plain"


def lazy_it():
    return "lazy value"


lazy_value = lazy(lazy_it, str)()
lazy_value_cls = lazy_it and None  # placeholder to keep flake quiet

CASES = [
    ("plain str", "hello & bye"),
    ("safe str", mark_safe("hello & bye")),
    ("lazy gettext", lazy(lambda: "lazy & value", str)()),
    ("bound method", Plain().name),
    ("obj.alters_data=class-attr", AltersDataClassAttr()),
    ("obj.alters_data=fn-attr", AltersDataFnAttr()),
    ("obj.do_not_call=True", DoNotCall()),
]

print("== (1b) the protocol, read off the objects themselves ==")
obj_c = AltersDataClassAttr()
obj_f = AltersDataFnAttr()
print("  getattr(ClassAttr().save, 'alters_data', False) = %r"
      % getattr(obj_c.save, "alters_data", False))
print("  getattr(FnAttr().save,    'alters_data', False) = %r"
      % getattr(obj_f.save, "alters_data", False))
print("  getattr(ClassAttr,        'alters_data', False) = %r"
      % getattr(AltersDataClassAttr, "alters_data", False))
print("  getattr(FnAttr,           'alters_data', False) = %r"
      % getattr(AltersDataFnAttr, "alters_data", False))
print("  isinstance(lazy_value, Promise) = %r" % isinstance(lazy_value, Promise))
print("  Django mark_safe('x').__class__.__mro__ = %r"
      % (mark_safe("x").__class__.__mro__,))
print("  ours _native.SafeStr.__mro__             = %r" % (_native.SafeStr.__mro__,))
print("  ours issubclass(_native.SafeStr, SafeData) = %r"
      % issubclass(_native.SafeStr, SafeData))
print("  ours hasattr(_native.SafeStr, '__html__')  = %r"
      % hasattr(_native.SafeStr, "__html__"))
print()

# ------------------------------------------------------- 2. resolve, ours only
print("== (2) `_native.resolve` ==")
for label, value in CASES:
    try:
        got = _native.resolve("x", {"x": value})
        print("  %-26s -> %s %r" % (label, type(got).__name__, str(got)))
    except Exception as e:
        print("  %-26s -> %s: %s" % (label, type(e).__name__, e))
print()

# -------------------------------------------- 3. the same through a render
print("== (3) `{{ x }}` rendered, ours vs stock ==")
SRC = "{{ x }}"
engine = Engine()
for label, value in CASES:
    try:
        stock = Template(SRC, engine=engine).render(Context({"x": value}))
    except Exception as e:
        stock = "%s: %s" % (type(e).__name__, e)
    try:
        ours = render_string(SRC, {"x": value}, engine=engine)
    except Exception as e:
        ours = "%s: %s" % (type(e).__name__, e)
    print("  %-26s stock=%-24r ours=%-24r %s"
          % (label, str(stock), str(ours), "same" if stock == ours else "DIFFERS"))
print()

# ------------------------------------- 3b. the callable protocol, per attribute
# The row (3) cannot reach: `alters_data` and `do_not_call_in_templates` are
# read off the *resolved attribute*, so they are invisible unless the variable
# names a method.
import re  # noqa: E402


def stable(text):
    return re.sub(r"0x[0-9a-f]+", "0xADDR", text)


print("== (3b) `{{ x.<attr> }}`, ours vs stock ==")
ATTRS = [
    ("Plain", Plain(), ["name"]),
    ("AltersData(class attr)", AltersDataClassAttr(), ["save", "name"]),
    ("AltersData(fn attr)", AltersDataFnAttr(), ["save", "name"]),
    ("DoNotCall", DoNotCall(), ["name"]),
]
for label, obj, attrs in ATTRS:
    for attr in attrs:
        src = "{{ x.%s }}" % attr
        try:
            stock = Template(src, engine=engine).render(Context({"x": obj}))
        except Exception as e:
            stock = "%s: %s" % (type(e).__name__, e)
        try:
            ours = render_string(src, {"x": obj}, engine=engine)
        except Exception as e:
            ours = "%s: %s" % (type(e).__name__, e)
        stock, ours = stable(str(stock)), stable(str(ours))
        print("  %-24s %-6s stock=%-42r ours=%-42r %s"
              % (label, "." + attr, stock, ours, "same" if stock == ours else "DIFFERS"))
