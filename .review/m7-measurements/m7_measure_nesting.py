"""A5's predicate: is any `{% block %}` nested inside a body Django parses?

Method: walk every .py under tests/template_tests with `ast`, collect every string
constant containing `{%`, tokenize it with Django's own Lexer, and track the open
tag stack. A `{% block %}` is *split-affected* when the innermost enclosing
block-level tag is one Rust will not own after M7.

Limits: only literals are seen, so a template assembled at runtime is invisible;
and a literal that is a fragment (e.g. "{% endblock %}") is tokenized as-is.
Both are reported rather than hidden.
"""
import ast, pathlib, sys

ROOT = pathlib.Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / ".django"))
from django.conf import settings
settings.configure(USE_I18N=False, USE_TZ=False, TEMPLATES=[
    {"BACKEND": "django.template.backends.django.DjangoTemplates",
     "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}])
import django; django.setup()
from django.template.base import Lexer, TokenType

TT = ROOT / ".django/tests/template_tests"

# Rust owns these after M7 (REGISTRY 25). Anything else that opens a body is
# host-compiled, and its nested parse is Django's.
RUST_OWNED = {
    "autoescape", "block", "comment", "cycle", "debug", "extends", "filter",
    "firstof", "for", "if", "ifchanged", "include", "load", "lorem", "now",
    "querystring", "regroup", "resetcycle", "spaceless", "templatetag", "url",
    "verbatim", "widthratio", "with",
}
ENDING = {"endblock", "endfor", "endif", "endwith", "endautoescape", "endfilter",
          "endspaceless", "endverbatim", "endcomment", "endpartialdef",
          "endcache", "endblocktrans", "endblocktranslate", "endlorem"}

found = []
strings = 0
tokened = 0
for f in sorted(TT.rglob("*.py")):
    try:
        tree = ast.parse(f.read_text())
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if "{%" not in node.value:
            continue
        strings += 1
        try:
            tokens = list(Lexer(node.value).tokenize())
        except Exception:
            continue
        tokened += 1
        stack = []
        for tok in tokens:
            if tok.token_type != TokenType.BLOCK:
                continue
            cmd = tok.contents.split()[0] if tok.contents.split() else ""
            if cmd == "block":
                enclosing = stack[-1]["cmd"] if stack else None
                if enclosing is not None and enclosing not in RUST_OWNED:
                    found.append((f.relative_to(ROOT), tok.lineno, node.value[:70], enclosing))
            if cmd in ENDING:
                if stack:
                    stack.pop()
            elif cmd not in ("else", "elif", "empty"):
                stack.append({"cmd": cmd, "lineno": tok.lineno})

print(f"scanned {strings} string literal(s), tokenized {tokened}")
print(f"`{{% block %}}` nested inside a host-owned block-level tag: {len(found)}")
for rel, lineno, frag, enc in found[:40]:
    print(f"  {rel}:{lineno} inside {enc!r}  {frag!r}")
