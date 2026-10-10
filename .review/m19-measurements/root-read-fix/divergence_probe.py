import sys, os
ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, ROOT + "/scripts")
import bench                      # puts the pinned .django on sys.path
sys.path.insert(0, ROOT + "/python")

from django.template import Engine as StockEngine, Context as StockContext
from django_rust_templates.engine import Engine as OurEngine
from django_rust_templates.engine.context import Context as OurContext

# --- BLOCKING 1 fixture: a layer whose __contains__ truncates the stack ---
def build(C):
    ctx = C({})
    class S(dict):
        def __contains__(self, key):
            self.owner.dicts[1]["k"] = "HIT"    # an already-examined layer gains the key
            del self.owner.dicts[2:]            # and the stack is truncated
            return dict.__contains__(self, key)
    ctx.dicts = [ctx.dicts[0], {"a": 1}, {"b": 2}, S({})]
    ctx.dicts[3].owner = ctx
    return ctx

def render_ours(src, ctx):
    return OurEngine().from_string(src).render(ctx)

src = "{{ k }}"
print("BLOCKING 1  stock :", repr(StockEngine().from_string(src).render(build(StockContext))))
print("BLOCKING 1  ours  :", repr(render_ours(src, build(OurContext))))

# --- weaker form: doubled __contains__ with no mutation ---
calls = []
def build2(C, log):
    ctx = C({})
    class Probe(dict):
        def __contains__(self, key):
            log.append(key); return False
    class UserDict(dict):
        pass
    ctx.dicts = [ctx.dicts[0], UserDict({"y": 1}), Probe({})]
    return ctx

l1 = []; c1 = build2(StockContext, l1)
print("weaker  stock :", repr(StockEngine().from_string("{{ y }}").render(c1)), l1)
l2 = []; c2 = build2(OurContext, l2)
print("weaker  ours  :", repr(render_ours("{{ y }}", c2)), l2)

# --- SHOULD-FIX 1: list subclass with __reversed__ ---
def build3(C):
    ctx = C({"x": "FROM_DICTS"})
    class Rev(list):
        def __reversed__(self): return iter([{"x": "FROM_REVERSED"}])
    ctx.dicts = Rev(ctx.dicts + [{"x": "FROM_SECOND"}])
    return ctx

print("SF1 stock :", repr(StockEngine().from_string("{{ x }}").render(build3(StockContext))))
print("SF1 ours  :", repr(render_ours("{{ x }}", build3(OurContext))))
