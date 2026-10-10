"""Reproduce the adversarial reviewer's BLOCKING case, verbatim."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402
filter_probe.ensure_settings()

from django.template import Context, Engine  # noqa: E402
import django  # noqa: E402
from django_rust_templates import _native, engine as rust_engine  # noqa: E402
import hashlib  # noqa: E402

print("django", django.get_version())
print("artifact", _native.__file__)
print("sha256", hashlib.sha256(open(_native.__file__, "rb").read()).hexdigest()[:16])
print()

CASES = [
    (
        "reviewer exact",
        '{% for s in sections %}{% for l in s %}{% cycle "X" as s silent %}{{ s }}{% endfor %}{{ s }}{% endfor %}',
        {"sections": ["old"]},
    ),
    (
        "cycle named, no silent",
        '{% for s in sections %}{% for l in s %}{% cycle "X" as s %}{{ s }}{% endfor %}[{{ s }}]{% endfor %}',
        {"sections": ["old"]},
    ),
    (
        "cycle named silent, 2 outer",
        '{% for s in secs %}{% for l in s %}{% cycle "X" as s silent %}{{ s }}{% endfor %}{{ s }}-{% endfor %}',
        {"secs": ["old", "new"]},
    ),
    (
        "no cycle (control)",
        "{% for s in sections %}{% for l in s %}{{ l }}{% endfor %}{{ s }}{% endfor %}",
        {"sections": ["old"]},
    ),
]

stock = Engine()
ours = rust_engine.Engine()

for name, src, data in CASES:
    s = stock.from_string(src).render(Context(data))
    o = ours.from_string(src).render(Context(data))
    flag = "ok" if s == o else "DIFF"
    print(f"{name:26} {flag:4}  stock={s!r}  ours={o!r}")
