"""The ordered write pass, counted — run once per artifact.

The row in `tests/test_interposition.py` asserts numbers; this prints the whole
`reads` list, because a count that keeps changing is settled by printing the
list rather than by arguing about which route each read took.

It lives in the tree rather than in a scratch directory for the same reason
`ctx_write_channels.py`'s library does: the test row cites its readings, and a
citation under `/tmp` (or a job's own directory) is reproducible only until that
directory goes away.

Run:  .venv/bin/python -I <this>        (prints the artifact sha first)

Readings, so far:

    sha256:0013195e7af12bb4  row ['sections', 'sections', 's']
                             control_by_emit ['sections', 'title', 's']
                             sealed_no_delegate ['sections', 'sections']
    sha256:b0c073c33ecdfe6c  row ['sections', 'sections', 's', 's']
                             control_by_emit ['sections', 'title', 's', 's']
                             sealed_no_delegate ['sections', 'sections']

Two things that list says and a count does not: the loop's *sequence* is
resolved in Python on both builds (the first `sections`), and the sealed
no-delegate body reads nothing from the context but its sequence.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.engine import Context, Engine  # noqa: E402

print("artifact sha256", hashlib.sha256(open(_native.__file__, "rb").read()).hexdigest()[:16])


class CountingContext(Context):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reads = []

    def __getitem__(self, key):
        self.reads.append(key)
        return super().__getitem__(key)


SOURCES = {
    # The row as written: a read of the loop variable before the delegate, one
    # after.  `sections` is read twice for a reason worth printing rather than
    # summarising: the loop's *sequence* is resolved in Python too.
    "row": (
        "{% for s in sections %}{{ sections }}{{ s }}"
        '{% cycle "X" as s silent %}{{ s }}{% endfor %}'
    ),
    # The same shape with the control as a name only an emit reads, so the
    # control's count is not shared with the sequence resolve.
    "control_by_emit": (
        "{% for s in sections %}{{ title }}{{ s }}"
        '{% cycle "X" as s silent %}{{ s }}{% endfor %}'
    ),
    # No delegate at all: the whole body is the walk's, so every read is a frame
    # read and the list says so.
    "sealed_no_delegate": "{% for s in sections %}{{ sections }}{{ s }}{{ s }}{% endfor %}",
}

for name, source in SOURCES.items():
    context = CountingContext({"sections": ["old"], "title": "T"})
    out = Engine().from_string(source).render(context)
    print(f"{name:20} out={out!r:14} reads={context.reads!r}")
    print(f"{'':20} s={context.reads.count('s')}  sections={context.reads.count('sections')}"
          f"  title={context.reads.count('title')}")
