"""M8 recon: what the pinned suite actually exercises on M8's surface, and
whether those rows can see an M8 defect at all.

Two questions, both asked as counts:
  1. how many tests do the M8-relevant modules run, and do they pass;
  2. do they pass because *our* resolver ran, or because Django's did? That is
     the vacuity question the spec's §10.3 rule deliberately does not answer.

Run as `.venv/bin/python -I .review/m8-measurements/m8_measure_corpus.py`.
"""

import sys
import unittest

ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, ROOT + "/.django")
sys.path.insert(0, ROOT + "/.django/tests")
sys.path.insert(0, ROOT)

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(
    USE_I18N=False,
    USE_TZ=False,
    USE_L10N=False,
    DEBUG=False,
    # `test_context` calls `Template(...)` with no engine, which reaches
    # `Engine.get_default()` and raises ImproperlyConfigured without this. The
    # first run of this script reported that as a corpus error; it is the
    # harness, not the corpus.
    TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates"}],
)
django.setup()

from django.template import Context, Engine  # noqa: E402
from django_rust_templates import _native  # noqa: E402
from django_rust_templates.render import render_string  # noqa: E402

T = "template_tests."
MODULES = [
    T + "test_callables",
    T + "syntax_tests.test_autoescape",
    T + "syntax_tests.test_invalid_string",
    T + "filter_tests.test_escape",
    T + "filter_tests.test_force_escape",
    T + "filter_tests.test_safe",
    T + "filter_tests.test_escapeseq",
    T + "filter_tests.test_safeseq",
    T + "filter_tests.test_escapejs",
    T + "test_context",
]

print("django %s" % django.get_version())
print()
print("== (1) the corpus ==")
loader = unittest.TestLoader()
suite = loader.loadTestsFromNames(MODULES)
total = suite.countTestCases()
res = unittest.TextTestRunner(verbosity=0, stream=open("/dev/null", "w")).run(suite)
print("  modules: %d" % len(MODULES))
print("  tests  : %d run, %d failures, %d errors, %d skipped"
      % (total, len(res.failures), len(res.errors), len(res.skipped)))
print()

print("== (2) does an M8 defect reach any of them? ==")
# The instrument: a callable that Django calls and our resolver does not.
# If the corpus can see the Call arm at all, interposing our resolver must
# change at least one rendered row.
engine = Engine()
SRC = "{{ x }}"
probe = lambda: "CALLED"  # noqa: E731
stock = None
try:
    from django.template import Template
    stock = Template(SRC, engine=engine).render(Context({"x": probe}))
except Exception as e:
    stock = "%s: %s" % (type(e).__name__, e)
ours = render_string(SRC, {"x": probe}, engine=engine)
print("  {{ x }} with a plain lambda   stock=%r  ours=%r" % (str(stock), str(ours)))
print("  -> the Call arm is %s through a render"
      % ("REACHED" if stock != ours else "not reached (both engines agree)"))
print()
print("  our own entry point, same value:")
try:
    got = _native.resolve("x", {"x": probe})
    print("    _native.resolve('x', ...) -> %r" % (str(got),))
except Exception as e:
    print("    _native.resolve('x', ...) -> %s: %s" % (type(e).__name__, e))
