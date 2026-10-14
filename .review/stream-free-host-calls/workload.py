#!/usr/bin/env python3
"""One arm of the before/after measurement. Prints JSON; says nothing about the other arm.

Run by `compare.py` under **two different interpreters** — one whose `_native`
was built from `main`, one from this branch — because that is the only baseline
that answers the question actually being asked.

The first harness compared the branch against itself with the Python classifier
stubbed to `False`. That is an ablation, not a baseline: both arms still ran the
new Rust dispatch, including the negative-cache lookup and the first classifier
crossing, so it could not show what those cost relative to `main`, and it
charged both arms equally for them. Review round 7 was right to reject it.

Reads a workload name and a repeat count on argv so the driver controls
ordering; everything else is fixed here so the two arms cannot drift.
"""

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / ".django"))
sys.path.insert(0, str(ROOT / "django_suite"))

from scripts.filter_probe import ensure_settings  # noqa: E402

ensure_settings()

from django_rust_templates import render  # noqa: E402
from django_rust_templates.engine import Engine, Library  # noqa: E402

BODY = "<p>hello {{ name }}</p>"


def bench_library():
    library = Library()

    @library.simple_tag
    def pageurl(target):
        return "/u/"

    @library.inclusion_tag("t.html")
    def card():
        return {}

    @library.simple_block_tag
    def wrap(content):
        return content

    return library


def shaped(n_native, n_simple, n_block, load=True):
    parts = ["{% load custom %}"] if load else []
    for i in range(max(n_native, n_simple, n_block)):
        if i < n_native:
            parts.append(f"{{% if x %}}{BODY}{{% endif %}}")
        if i < n_simple:
            parts.append("{% pageurl x %}")
        if i < n_block:
            parts.append(f"{{% wrap %}}{BODY}{{% endwrap %}}")
    return "".join(parts)


def corpus():
    """Every template in the benchmark corpus, not one of them.

    The first harness used `large.html` alone and silently fell back to a
    synthetic string when it was missing, which would have labelled synthetic
    work as corpus work. Missing files are now a hard failure.
    """
    directory = ROOT / "scripts" / "bench_templates"
    paths = sorted(directory.glob("*.html"))
    if not paths:
        raise SystemExit(f"no corpus templates under {directory}")
    return {f"corpus/{p.stem}": p.read_text() for p in paths}


WORKLOADS = {
    "median-8": shaped(6, 2, 0),
    "p90-22": shaped(14, 6, 1),
    "max-59": shaped(30, 16, 3),
    "all-native": shaped(40, 0, 0, load=False),
    "all-native-stress": shaped(200, 0, 0, load=False),
    "tiny-one-host": "{% load custom %}<p>x</p>",
    "all-streamful": shaped(0, 0, 20, load=False),
    "all-streamful-stress": shaped(0, 0, 100, load=False),
    "mixed": shaped(10, 10, 10),
    **corpus(),
}

# Tiny templates are timed in batches: one compile of `tiny-one-host` is ~12 us,
# which is close enough to timer noise that a per-compile sample measures the
# clock as much as the work.
BATCH = {"tiny-one-host": 2000}


def main():
    name, runs, debug = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "debug"
    source = WORKLOADS[name]
    batch = BATCH.get(name, 20)
    engine = Engine(
        debug=debug,
        libraries={
            "custom": "template_tests.templatetags.custom",
            # Two corpus templates `{% load i18n %}`. A bare `Engine` has no
            # installed-library discovery, so it is named here.
            "i18n": "django_rust_templates.engine.templatetags.i18n",
        },
    )
    engine.template_builtins.append(bench_library())

    for _ in range(3):  # warm
        for _ in range(batch):
            render.parse_template(source, engine)

    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        for _ in range(batch):
            render.parse_template(source, engine)
        samples.append((time.perf_counter() - start) / batch * 1e3)

    print(json.dumps({
        "workload": name,
        "debug": debug,
        "batch": batch,
        "median_ms": statistics.median(samples),
        "samples": samples,
    }))


if __name__ == "__main__":
    main()
