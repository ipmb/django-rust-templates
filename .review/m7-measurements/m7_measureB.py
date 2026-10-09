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


def run(src, ctx=None):
    try:
        t = eng.from_string(src)
        return "OK  " + repr(t.render(Context(ctx or {})))
    except Exception as e:
        return f"{type(e).__name__}: {e}"


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
print("--- (c) render_string / parse signatures today ---")
sys.path.insert(0, "/Users/pete/projects/lincolnloop/django-rust-templates")
import django_rust_templates.render as R
import inspect

print("  render_string", inspect.signature(R.render_string))
print("  parse_template", inspect.signature(R.parse_template))
print("  parser_for", inspect.signature(R.parser_for))
from django_rust_templates import _native

print("  _native.parse", _native.parse.__doc__)
