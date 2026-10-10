#!/usr/bin/env python
"""Re-derive `base.py`'s declared spans with the checker's own diff.

Run after any edit that moves a line inside a declared transcription region:
`scripts/check_transcriptions.py` is read-only by design, so the new spans have
to come from somewhere else, and the somewhere else is its `differing_lines`.
Printing a span from a window I read, or by adding a delta to an old number, is
the arithmetic this project has already paid for twice.

Run:  .venv/bin/python -I .review/m19-measurements/rederive_base_regions.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_transcriptions as ct  # noqa: E402

name = "base.py"
ours = (ct.IMPL / name).read_text().splitlines()
theirs = (ct.CHECKOUT / ct.DECLARED[name][0]).read_text().splitlines()

print(f"{name}: {len(ours)} ours / {len(theirs)} upstream lines")
print()
print("actual differing regions (ours 1-based inclusive, upstream 1-based inclusive):")
for span_ours, span_theirs in ct.differing_lines(ours, theirs):
    print(f"   ours {str(span_ours):>12}   upstream {str(span_theirs):>12}")
print()
print("declared regions:")
for span_ours, span_theirs, snippet in ct.DECLARED[name][1]:
    print(f"   ours {str(span_ours):>12}   upstream {str(span_theirs):>12}   {snippet[:60]!r}")
print()
print("COMPARED_PATH['base.py'] spans, re-measured from the source now on disk:")
import ast  # noqa: E402

tree = ast.parse("\n".join(ours) + "\n")
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        print(f"   {ast.unparse(node.args)[:0]}{node.name:<24} {node.lineno}-{node.end_lineno}")
