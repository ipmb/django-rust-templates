from __future__ import annotations
import sys, hashlib
from pathlib import Path
ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python")); sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, "/Users/pete/.claude/jobs/627577b5/tmp/libdir")
import filter_probe; filter_probe.ensure_settings()
from django.template import Context, Engine
from django_rust_templates import _native, engine as rust_engine

print("artifact sha256", hashlib.sha256(open(_native.__file__, "rb").read()).hexdigest()[:16])
LIBS = {"probe_lib": "probe_lib"}
CASES = [
    ("ctx filter then read",  "{% load probe_lib %}{% for s in sections %}{{ 'q'|ctxwrite }}{{ s }}{% endfor %}"),
    ("read then ctx filter",  "{% load probe_lib %}{% for s in sections %}{{ s }}{{ 'q'|ctxwrite }}{% endfor %}"),
    ("ctx filter in nested",  "{% load probe_lib %}{% for s in sections %}{% for l in s %}{{ 'q'|ctxwrite }}{{ l }}{% endfor %}{{ s }}{% endfor %}"),
]
for name, src in CASES:
    res = []
    for eng in (Engine(libraries=LIBS), rust_engine.Engine(libraries=LIBS)):
        try: res.append(eng.from_string(src).render(Context({"sections": ["old"]})))
        except Exception as e: res.append(f"<{type(e).__name__}: {e}>")
    print(f"{name:22} {'ok' if res[0]==res[1] else 'DIFF':4} stock={res[0]!r} ours={res[1]!r}")
