"""M7 Task 5 / Task 6 measurements.

(a) a library tag whose returned Node sets `must_be_first = True`: does Django
    refuse it in a non-first position, and with what message?
(b) `{% block %}` duplicate names and end-tag forms, for the corpus.
(c) `render_string`'s signature and whether it can carry an origin today.
"""
import sys

sys.path.insert(0, "/Users/pete/projects/lincolnloop/django-rust-templates/.django")

from django.template import Context, Engine, TemplateSyntaxError
from django.template.base import Node, Parser, Token
from django.template.library import Library

lib = Library()


class FirstNode(Node):
    must_be_first = True

    def render(self, context):
        return "F"


@lib.tag("first_tag")
def do_first(parser, token):
    return FirstNode()


@lib.tag("plain_tag")
def do_plain(parser, token):
    return Node()


eng = Engine()
eng.template_libraries["mylib"] = lib


def run(src, ctx=None, engine=None):
    try:
        t = (engine or eng).from_string(src)
        return "OK  " + repr(t.render(Context(ctx or {})))
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def run_engine(engine, src):
    return run(src, engine=engine)


print("--- (a) host node with must_be_first=True ---")
for src in [
    "{% load mylib %}{% first_tag %}",
    "text{% load mylib %}{% first_tag %}",
    "{% load mylib %}{% plain_tag %}text{% plain_tag %}",
    " {% load mylib %}{% first_tag %}",
    "{# c #}{% load mylib %}{% first_tag %}",
]:
    print(f"  {src!r:48} -> {run(src)}")

print()
print("--- (b) {% block %} forms ---")
for src in [
    "{% block a %}x{% endblock %}",
    "{% block a %}x{% endblock a %}",
    "{% block %}x{% endblock %}",
    "{% block a b %}x{% endblock %}",
    "{% block a %}x{% endblock b %}",
    "{% block a %}{% endblock %}{% block a %}{% endblock %}",
    "{% block a %}{% block a %}{% endblock %}{% endblock %}",
    "A{% block a %}x{% endblock %}B",
]:
    print(f"  {src!r:58} -> {run(src)}")

print()
print("--- (d) can a must_be_first host node ever be *first*? ---")
# `{% load %}` is itself a non-Text node, so it flips `contains_nontext` before
# any tag it brings in can be reached (`base.py:611-612`) — a loaded library's
# `must_be_first` tag can therefore never be accepted. A *builtin* reaches the
# parser through `Parser.__init__`'s merge instead, with no node of its own, so
# it can. `Engine.template_builtins` is the list Django hands `Parser`
# (`engine.py:63`), and `get_template_builtins` only runs `import_library` over
# it — so appending a `Library` is what `OPTIONS["builtins"]` produces, with the
# module indirection removed. Measured both ways below.
eng2 = Engine()
eng2.template_builtins.append(lib)
for src in [
    "{% first_tag %}",
    "{% first_tag %}tail",
    "{% first_tag %}{% first_tag %}",
]:
    print(f"  {src!r:32} -> {run_engine(eng2, src)}")

# The documented route, for comparison: a dotted path, which needs an
# importable module. `.django/tests` is on the path here as it is in the repo's
# own tests.
sys.path.insert(0, "/Users/pete/projects/lincolnloop/django-rust-templates/.django/tests")
eng3 = Engine(builtins=["template_tests.templatetags.custom"])
print(f"  {'builtins=[custom]}: ' + repr(sorted(eng3.template_builtins[-1].tags))[:60]}")
print(f"  {'a loaded-only tag under builtins':32} -> {run_engine(eng3, '{% load mylib %}{% first_tag %}')}")

print()
print("--- (e) the two agree, row for row ---")
sys.path.insert(0, "/Users/pete/projects/lincolnloop/django-rust-templates")
from django_rust_templates import _native
from django_rust_templates.render import render_string


def ours(src, e):
    try:
        return "OK  " + repr(render_string(src, {}, e))
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


for label, e, src in [
    ("builtin, first", eng2, "{% first_tag %}"),
    ("builtin, after text", eng2, "text{% first_tag %}"),
    ("builtin, twice", eng2, "{% first_tag %}{% first_tag %}"),
    ("loaded, after load", eng, "{% load mylib %}{% first_tag %}"),
]:
    print(f"  {label:22} {src!r:34}")
    print(f"      stock -> {run(src, engine=e)}")
    print(f"      ours  -> {ours(src, e)}")

print()
print("--- (f) a host tag that returns something that is not a node ---")
# This is the *only* path on which the two spellings of the read differ: a node
# that carries the attribute answers identically under `?` and under
# `.unwrap_or(false)`, so the plan's Step 4 mutation is a no-op (measured: the
# suite stays green). The raising spelling is justified by what happens when the
# attribute is *absent*.
lib2 = Library()
lib2.tag("bad_tag", lambda parser, token: object())
eng4 = Engine()
eng4.template_libraries["badlib"] = lib2
for src in ["{% load badlib %}{% bad_tag %}"]:
    print(f"  stock {src!r:34} -> {run(src, engine=eng4)}")
    print(f"  ours  {src!r:34} -> {ours(src, eng4)}")

print()
print("--- (c) render_string / parse signatures today ---")
sys.path.insert(0, "/Users/pete/projects/lincolnloop/django-rust-templates")
import django_rust_templates.render as R
import inspect

print("  render_string", inspect.signature(R.render_string))
print("  parse_template", inspect.signature(R.parse_template))
print("  parser_for", inspect.signature(R.parser_for))
from django_rust_templates import _native

print("  _native.parse", _native.parse.__doc__)
