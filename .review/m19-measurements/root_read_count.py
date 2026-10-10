"""Count root reads per whole-page render, under whichever build is installed.

A root read is a call to the pinned `BaseContext.__getitem__` -- the only route
the walk has to a root layer. Counted with a `sys.setprofile` hook filtered on
the function's own `__code__`, so it counts the pinned function and not a
same-named one; `Context` and `RequestContext` do not override it, so the one
code object catches every subclass.
"""
import sys, os
ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bench  # noqa: F401  -- puts the pinned .django checkout on sys.path
import bench_corpus, bench_engines
from django_rust_templates.engine.context import BaseContext
from django_rust_templates.engine.base import BASE_CONTEXT_GETITEM
from django_rust_templates import _native

TARGET = BASE_CONTEXT_GETITEM.__code__
PIN = BaseContext.__getitem__
assert PIN is BASE_CONTEXT_GETITEM, "the module binding and the class attribute disagree"
assert PIN.__code__ is TARGET

hits = 0
def prof(frame, event, arg):
    global hits
    if event == "call" and frame.f_code is TARGET:
        hits += 1

print(f"artifact {_native.__file__}")
print(f"pin is class attr: {BaseContext.__getitem__ is BASE_CONTEXT_GETITEM}")

which = sys.argv[1]
rows = {}
for w in bench_corpus.workloads():
    if w.family != "real-world":
        continue
    eng = bench_engines.build("rust", w.templates, w.libraries("rust"))
    try:
        handle = eng.get(w.entry)
        ctx = eng.context(w.data)
        eng.render(handle, ctx)              # warm
        hits = 0
        sys.setprofile(prof)
        out = eng.render(handle, ctx)
        sys.setprofile(None)
        rows[w.key] = hits
        print(f"  {which}  {w.key:22} root reads {hits:5}   {len(out)} bytes")
    finally:
        eng.close()
import json, pathlib
pathlib.Path(f"/Users/pete/.claude/jobs/627577b5/tmp/rootread-count-{which}.json").write_text(json.dumps(rows, indent=2))
