"""Which sealed-loop shapes lose an upward context write to the frame?"""
from __future__ import annotations
import sys, hashlib
from pathlib import Path
ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python")); sys.path.insert(0, str(ROOT / "scripts"))
import filter_probe; filter_probe.ensure_settings()
from django.template import Context, Engine
from django_rust_templates import _native, engine as rust_engine

print("artifact sha256", hashlib.sha256(open(_native.__file__, "rb").read()).hexdigest()[:16])
TC = {"sections": ["old"], "secs": ["old", "new"], "n": [1, 2]}
CASES = [
    # direct delegate in the loop body (slice-2 shape, pre-existing)
    ("direct cycle silent",  '{% for s in sections %}{% cycle "X" as s silent %}{{ s }}{% endfor %}', TC),
    ("direct cycle silent + tail", '{% for s in sections %}{% cycle "X" as s silent %}{{ s }}{% endfor %}[{{ s }}]', TC),
    # nested loop, no delegate in inner body (pure) — nothing can write upward
    ("nested pure",          '{% for s in sections %}{% for l in s %}{{ l }}{% endfor %}{{ s }}{% endfor %}', TC),
    # nested loop whose inner body holds a delegate -> the reviewer's class
    ("nested + include",     "{% for s in sections %}{% for l in s %}{% include 'p.html' %}{% endfor %}{{ s }}{% endfor %}", TC),
    ("nested + cycle silent",'{% for s in sections %}{% for l in s %}{% cycle "X" as s silent %}{{ s }}{% endfor %}{{ s }}{% endfor %}', TC),
    # nested loop where OUTER body (not inner) holds the cycle
    ("cycle then nested",    '{% for s in sections %}{% cycle "X" as s silent %}{% for l in s %}{{ l }}{% endfor %}{{ s }}{% endfor %}', TC),
    # if-wrapped nested
    ("nested in if",         '{% for s in sections %}{% if s %}{% for l in s %}{% cycle "X" as s silent %}{{ l }}{% endfor %}{% endif %}[{{ s }}]{% endfor %}', TC),
    # nested, cycle rebinds a name that is NOT the outer loop var
    ("cycle other name",     '{% for s in sections %}{% for l in s %}{% cycle "X" as z silent %}{{ l }}{% endfor %}{{ s }}{% endfor %}', TC),
]
tpl = {"p.html": "{{ s }}", }
stock = Engine(loaders=[("django.template.loaders.locmem.Loader", tpl)])
ours = rust_engine.Engine(loaders=[("django.template.loaders.locmem.Loader", tpl)])
bad = 0
for name, src, data in CASES:
    try: s = stock.from_string(src).render(Context(data))
    except Exception as e: s = f"<{type(e).__name__}: {e}>"
    try: o = ours.from_string(src).render(Context(data))
    except Exception as e: o = f"<{type(e).__name__}: {e}>"
    flag = "ok" if s == o else "DIFF"
    bad += flag == "DIFF"
    print(f"{name:24} {flag:4} stock={s!r:26} ours={o!r}")
print()
print(f"{bad} of {len(CASES)} DIFF")
