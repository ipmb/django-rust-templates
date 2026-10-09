"""The two divergences get_nodes_by_type records: are they reached?"""
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
RUST = {"autoescape","block","comment","cycle","debug","extends","filter","firstof",
        "for","if","ifchanged","include","load","lorem","now","querystring","regroup",
        "resetcycle","spaceless","templatetag","url","verbatim","widthratio","with"}
END = {"endblock","endfor","endif","endwith","endautoescape","endfilter","endspaceless",
       "endverbatim","endcomment","endpartialdef","endcache","endblocktrans",
       "endblocktranslate","endlorem"}

def blocks(text, want):
    try: toks = list(Lexer(text).tokenize())
    except Exception: return []
    out, stack = [], []
    for t in toks:
        if t.token_type != TokenType.BLOCK: continue
        p = t.contents.split(); cmd = p[0] if p else ""
        if cmd == want and stack and stack[-1] not in RUST:
            out.append((t.lineno, stack[-1]))
        if cmd in END:
            if stack: stack.pop()
        elif cmd not in ("else","elif","empty","plural"): stack.append(cmd)
    return out

strs = []
for f in sorted(TT.rglob("*.py")):
    try: tree = ast.parse(f.read_text())
    except SyntaxError: continue
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "{%" in n.value:
            strs.append((f.relative_to(ROOT), n.value))
for f in sorted(p for p in TT.rglob("*") if p.is_file() and p.suffix in (".html",".txt")):
    strs.append((f.relative_to(ROOT), f.read_text()))

for want in ("block", "extends"):
    hits = [(rel, ln, enc) for rel, s in strs for ln, enc in blocks(s, want)]
    print(f"`{{% {want} %}}` nested inside a host-owned block-level tag: {len(hits)}")
    for rel, ln, enc in hits:
        print(f"    {rel}:{ln} inside {enc!r}")

print("\n--- a library tag whose return value is an ExtendsNode/BlockNode subclass ---")
import re
for f in sorted(TT.rglob("*.py")):
    t = f.read_text()
    for cls in ("ExtendsNode", "BlockNode", "IncludeNode"):
        for m in re.finditer(rf"class\s+\w+\({cls}\)", t):
            print(f"    {f.relative_to(ROOT)}: {m.group(0)}")
