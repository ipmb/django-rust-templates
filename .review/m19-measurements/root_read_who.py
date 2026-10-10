"""The same hook, recording WHO called each root read that still leaves Rust."""
import sys, os, collections
ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bench  # noqa: F401
import bench_corpus, bench_engines
from django_rust_templates.engine.context import BaseContext
from django_rust_templates.engine.base import BASE_CONTEXT_GETITEM

TARGET = BASE_CONTEXT_GETITEM.__code__
callers = collections.Counter()
def prof(frame, event, arg):
    if event == "call" and frame.f_code is TARGET:
        back = frame.f_back
        if back is None:
            callers["<no python frame>"] += 1
        else:
            c = back.f_code
            callers[f"{c.co_filename.rsplit('/',1)[-1]}:{c.co_firstlineno} {c.co_qualname}"] += 1

for w in bench_corpus.workloads():
    if w.key != "real-world/complex":
        continue
    eng = bench_engines.build("rust", w.templates, w.libraries("rust"))
    try:
        handle, ctx = eng.get(w.entry), eng.context(w.data)
        eng.render(handle, ctx)
        callers.clear()
        sys.setprofile(prof)
        eng.render(handle, ctx)
        sys.setprofile(None)
    finally:
        eng.close()
print("callers of the root reads that still leave Rust on real-world/complex:")
for k, v in callers.most_common():
    print(f"  {v:4}  {k}")
print(f"  total {sum(callers.values())}")
