"""A3: what Django's Parser.origin actually is, on every path we can reach."""
import inspect, posixpath, sys
sys.path.insert(0, "/Users/pete/projects/lincolnloop/django-rust-templates/.django")

from django.template import Context, Engine, TemplateSyntaxError
from django.conf import settings
settings.configure(USE_I18N=False, USE_TZ=False, TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}])
import django
django.setup()
from django.template.base import UNKNOWN_SOURCE, Parser, Lexer, Template
from django.template.base import Origin

print("UNKNOWN_SOURCE =", repr(UNKNOWN_SOURCE))

print("\n--- 1. Parser(...) built bare (what render.parser_for does) ---")
p = Parser(Lexer("").tokenize())
print("  parser.origin =", repr(p.origin))

print("\n--- 2. Template(source) with no origin ---")
t = Template("{% block a %}x{% endblock %}", engine=Engine())
print("  template.origin      =", repr(t.origin))
print("  template.origin.name =", repr(t.origin.name))
print("  .name != UNKNOWN_SOURCE ->", t.origin.name != UNKNOWN_SOURCE)

print("\n--- 3. Engine().from_string ---")
e = Engine()
t2 = e.from_string("{% block a %}x{% endblock %}")
print("  origin.name =", repr(t2.origin.name))

print("\n--- 4. what construct_relative_path sees for that name ---")
name = UNKNOWN_SOURCE
print("  dirname(name.lstrip('/')) =", repr(posixpath.dirname(name.lstrip("/"))))
print("  posixpath.join(that, 'base.html') =",
      repr(posixpath.join(posixpath.dirname(name.lstrip("/")), "base.html")))

print("\n--- 5. does a parse-time error get the 'Template: <name>, ' prefix? ---")
for label, src in [("no-origin", "{% block a %}x{% endblock %}{% block a %}y{% endblock %}")]:
    try:
        Template(src, engine=Engine())
    except TemplateSyntaxError as ex:
        print(f"  {label}: {ex!r}")
        print(f"    has raw_error_message: {hasattr(ex, 'raw_error_message')}")

print("\n--- 6. Parser.__init__ signature ---")
print(" ", inspect.signature(Parser.__init__))
print("\n--- 7. Template.__init__ signature ---")
print(" ", inspect.signature(Template.__init__))

print("\n--- 8. ExtendsNode with an unknown-source origin, parse only ---")
try:
    t3 = Template("{% extends 'base.html' %}{% block a %}x{% endblock %}")
    from django.template.loader_tags import ExtendsNode
    node = t3.nodelist.get_nodes_by_type(ExtendsNode)[0]
    print("  parsed OK; ExtendsNode.parent_name =", repr(node.parent_name))
except Exception as ex:
    print("  ", type(ex).__name__, ex)

print("\n--- 9. origin.name used in a render-time error message ---")
eng = Engine(debug=True)
try:
    eng.from_string("{% extends 'missing.html' %}").render(Context({}))
except Exception as ex:
    print("  ", type(ex).__name__, str(ex)[:200])
