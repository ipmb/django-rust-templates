"""Differential check for M19 step 5 slice 2: a loop that seals with a delegate inside.

Every template here is rendered by **stock Django** and by **our engine** and the
two outputs are compared byte for byte. The point of the row set is the *stated*
path (`Walk::run_stated`): a `{% for %}` whose body holds a node this walk hands
to Python, which now seals anyway and writes Django's per-iteration state around
the delegate.

Run from the project root against the release artifact the gate builds:

    .venv/bin/python -I <this file>
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_engines  # noqa: E402

LIBRARIES = {"reg": "mylib"}


TEMPLATES = {
    "inc_name.html": "{{ r.name }}",
    "inc_floop.html": "[{{ forloop.counter }}/{{ forloop.length }}]",
    "inc_parent.html": (
        "{% for c in r.cols %}"
        "{{ forloop.parentloop.counter }}.{{ forloop.counter }}"
        "/{{ forloop.parentloop.length }} "
        "{% endfor %}"
    ),
    "inc_shadow.html": "{% for r in rows %}s{% endfor %}",
    "inc_empty.html": "{% for z in r.none %}{{ z }}{% endfor %}empty",

    # 1. the plain case: a delegate reading the loop variable out of the context
    "a.html": "{% for r in rows %}{% include 'inc_name.html' %},{% endfor %}",
    # 2. a delegate reading `forloop` itself, including the key sealing never wrote
    "b.html": "{% for r in rows %}{% include 'inc_floop.html' %}{% endfor %}",
    # 3. a nested loop inside the delegate, reading `parentloop`
    "c.html": "{% for r in rows %}{% include 'inc_parent.html' %}{% endfor %}",
    # 4. a tag delegate beside native nodes
    "d.html": "{% for r in rows %}{{ r.name }}{% noop %}{% endfor %}",
    # 5. takes_context, which is the reason the state has to be in the context
    "e.html": "{% for r in rows %}{% shout r.name %}{% endfor %}",
    # 6. a delegate that pushes and pops its own layer over the loop variable
    "f.html": "{% for r in rows %}{% include 'inc_shadow.html' %}{{ r.name }}{% endfor %}",
    # 7. a delegate with an `{% empty %}` loop inside it
    "g.html": "{% for r in rows %}{% include 'inc_empty.html' %}{% endfor %}",
    # 8. an unplannable variable node: a library filter, which is not Rust's
    "h.html": "{% for r in rows %}{{ r.name|mark }}{% endfor %}",
    # 9. delegate inside `{% if %}` inside the body
    "i.html": "{% for r in rows %}{% if r.name %}{% noop %}{{ r.name }}{% endif %}{% endfor %}",
    # 10. reversed, with a delegate
    "j.html": "{% for r in rows reversed %}{% noop %}{{ r.name }}{% endfor %}",
    # 11. the loop variable shadowed by the delegate and restored after
    "k.html": "{% for r in rows %}{% include 'inc_shadow.html' %}{{ r.name }}{% endfor %}[{{ r }}]",
    # 12. `with` over the loop variable
    "l.html": "{% for r in rows %}{% with n=r.name %}{{ n }}{% endwith %}{% endfor %}",
    # 13. a refused `{{ }}` and a delegate in one body
    "m.html": "{% for r in rows %}{{ missing }}{% noop %}{% endfor %}",
    # 14. two delegates in one body
    "n.html": "{% for r in rows %}{% noop %}{% include 'inc_floop.html' %}{% noop %}{% endfor %}",
    # 15. a delegate whose own loop nests two deep, parentloop twice
    "o.html": (
        "{% for r in rows %}{% for c in r.cols %}"
        "{% include 'inc_floop.html' %}"
        "{% endfor %}{% endfor %}"
    ),
    # 16. nothing in the body but a delegate: the seal has no native node at all
    "p.html": "{% for r in rows %}{% include 'inc_name.html' %}{% endfor %}",
}

DATA = {
    "rows": [
        {"name": "a", "cols": [1, 2, 3]},
        {"name": "b", "cols": []},
        {"name": "c", "cols": [4]},
    ],
}


def main() -> int:
    failures = 0
    stock = bench_engines.build("django", TEMPLATES, LIBRARIES)
    ours = bench_engines.build("rust", TEMPLATES, LIBRARIES)
    try:
        for key in sorted(TEMPLATES):
            if not key.endswith(".html") or key.startswith("inc_"):
                continue
            source = "{% load reg %}" + TEMPLATES[key]
            a = stock.from_string(source).render(stock.context(DATA))
            b = ours.from_string(source).render(ours.context(DATA))
            mark = "ok  " if a == b else "DIFF"
            if a != b:
                failures += 1
            print(f"{mark} {key}  {source}")
            if a != b:
                print(f"       stock: {a!r}")
                print(f"       ours : {b!r}")
    finally:
        stock.close()
        ours.close()
    print(f"\n{len([k for k in TEMPLATES if not k.startswith('inc_')])} rows, {failures} differ")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
