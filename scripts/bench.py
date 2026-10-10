#!/usr/bin/env python
"""The project's first benchmark: stock Django vs the Rust-backed engine.

Run the *same* file under both sides; the harness prints which engine it built and
which build of the extension it installed, so a transcript cannot be read as the
run it is not.

    source scripts/env.sh
    maturin develop --release                          # or the table is meaningless
    .venv/bin/python scripts/bench.py --side rust      # our engine
    .venv/bin/python -I scripts/bench.py --side stock  # stock Django (control)

**The build profile is half of this instrument, and it was the half nobody
printed.** ``pip install`` compiles the extension with ``--release``;
``maturin develop`` — what a checkout runs — installs the *dev* profile, and
``Cargo.toml`` sets ``[profile.release]`` to thin LTO with ``codegen-units = 1``
while leaving ``[profile.dev]`` at its unoptimised default. The gap is not
marginal: measured on these four workloads, the debug extension is 1.8× slower on
the pre-compiled render and 3.3× on the node-heavy parse. So the first run of this
harness after M18 reported **2.4×–6.0×** — a real measurement of the wrong binary,
which is the failure this file's other refusal (``--side``) also exists to prevent.
``native:`` below prints the extension that was loaded and which build of it that
is, identified by comparing the installed file's ``sha256`` against
``target/debug`` and ``target/release``, and saying ``unknown`` rather than
guessing when neither matches. That identifies the *build*; it does not and cannot
say whether the build is *current*, since a source edit changes neither file — so
``scripts/check.sh`` builds the extension before it runs anything, and this line is
the receipt rather than the check.

**The substitution this file used to document is gone, and the failure it left was
silent.** Until M18.2 the two sides were arranged by a finder: `DJANGO_RUST_TEMPLATES=1`
put our file on `sys.meta_path` under `django.template`'s name, so
`from django.template import Engine` in this module meant ours on one side and
Django's on the other. M18.2 deleted the finder (`scripts/env.sh` says so in as many
words), and `scripts/shimpath` — the directory that invocation put on `PYTHONPATH` —
no longer exists. Both of the old invocations therefore ran to completion and printed
`side: stock`: two runs of the same engine, whose timings agree to within noise,
which is exactly what a control that is not a control looks like from the outside.
`--side` names the engine explicitly, and `main` refuses to print a table whose
engine is not the one asked for.

Four workloads, matching the four-row table that motivated this milestone (§M16):

  realistic/render   a 50-row loop with i18n `{% translate %}` /
                     `{% blocktranslate %}` over `{% extends %}` inheritance,
                     **pre-compiled** and rendered repeatedly — the render hot
                     path with the parse removed.
  realistic/parse    the same page through `Engine.get_template` + `render`, so
                     each iteration parses and renders.
  large/parse        a generated ~200-node / 6.8 KB template, parse + render.
  tiny/parse         `{{ a|upper }}`, parse + render — the fixed-overhead row.

What is measured: `perf_counter` around a tight inner loop of `iters` calls,
repeated `--repeats` times; the reported figure is the **best** per-call time in
microseconds (best-of-N is the stable estimator for a warm, CPU-bound loop; the
spread is printed too, so a noisy run is visible rather than hidden).

`--count` wraps every `_native` entry point and reports how many times each is
called per render — the FFI-crossing census. It is the `--side rust` row only;
stock Django has no `_native`.

The module never edits a template to make a side agree: the two sides load the
same source strings, and each workload's output is printed as a sha256 so the two
transcripts can be compared byte for byte. They are two processes, so the
comparison is a `diff` of the two runs, not a flag here — which is also why
`--side` has to be given twice rather than measured once.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

# Settings are configured in exactly one place in this tree, and this file is not
# it: `tests/test_settings_configuration.py` refuses a second `settings.configure`
# under `scripts/` or `tests/`, because `settings.configure` is a no-op once
# settings exist, so *which module runs first* would decide the values. Reusing
# `filter_probe.ensure_settings` also removes this harness's own `.django`
# `sys.path` insert, which the stock side needs under `-I` (`-I` implies `-E`, so
# `PYTHONPATH` is ignored and `import django` dies): filter_probe does it at
# import, for the same reason, and one copy cannot disagree with itself.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import django  # noqa: E402

# Neither engine's `Engine` or `Context` is imported here on purpose: `--side`
# picks a pair inside `make_engine`, so there is no module-level name for a run to
# fall back on. That is the whole point of the flag.

# --- the templates -----------------------------------------------------------
# `{% load i18n %}` sits in *both* files: `{% load %}` is per-file, so a parent
# that uses `{% translate %}` without it fails the parse even when the child has
# loaded the library.

BASE = """{% load i18n %}<html><head><title>{% block title %}Default{% endblock %}</title></head>
<body><h1>{% block heading %}{% translate "Report" %}{% endblock %}</h1>
{% block content %}{% endblock %}</body></html>"""

PAGE = """{% extends "base.html" %}{% load i18n %}
{% block title %}{% translate "Monthly Report" %}{% endblock %}
{% block content %}
<table>
{% for row in rows %}
<tr><td>{{ row.name }}</td><td>{{ row.value|floatformat:2 }}</td>
<td>{% blocktranslate count count=row.count %}{{ count }} item{% plural %}{{ count }} items{% endblocktranslate %}</td></tr>
{% endfor %}
</table>
{% endblock %}"""

ROWS = [{"name": f"Row {i}", "value": i * 1.5, "count": i} for i in range(50)]


def large_template() -> str:
    """A ~200-node, ~6.8 KB template: parse-heavy, no loop, no i18n.

    Alternating `{{ row.N }}` and `{% if %}`/`{% for %}` blocks so the node count
    is dominated by variables and control tags rather than text.
    """
    parts = ['{% load i18n %}']
    parts.append("<article>")
    for i in range(20):
        parts.append(f'<section id="s{i}">')
        # ten variable nodes each
        for j in range(10):
            parts.append(f'<p>{{{{ section{i}.field{j} }}}}</p>')
        # one if + one for block each, with variables inside
        parts.append(f"{{% if section{i}.enabled %}}<b>{{{{ section{i}.label }}}}</b>{{% endif %}}")
        parts.append(
            f"{{% for item in section{i}.items %}}<li>{{{{ item.name }}}}: "
            f"{{{{ item.value }}}}</li>{{% endfor %}}"
        )
        parts.append("</section>")
    parts.append("</article>")
    return "".join(parts)


LARGE = large_template()


def make_engine(side: str):
    """The engine `side` names, and the ``Context`` class that goes with it.

    Each side is built from *its own* transcription of the same locmem loader and
    the same ``i18n`` library, and that is the only asymmetry: both engines load
    the same two source strings and render the same data. Naming the classes here
    is what replaced the finder — see the module docstring.

    The return is ``(engine, context_class)``. A ``Context`` belongs to the engine
    that made it: ours is ``django_rust_templates.engine.context.Context``, which is
    a transcription and not Django's class, so handing Django's to our engine (or
    the reverse) is a type error deep inside a render rather than a comparable run.
    """
    if side == "rust":
        from django_rust_templates.engine.context import Context as context
        from django_rust_templates.engine.engine import Engine as engine_class

        loaders = "django_rust_templates.engine.loaders.locmem.Loader"
        i18n = "django_rust_templates.engine.templatetags.i18n"
    else:
        from django.template.context import Context as context
        from django.template.engine import Engine as engine_class

        loaders = "django.template.loaders.locmem.Loader"
        i18n = "django.templatetags.i18n"

    engine = engine_class(
        loaders=[(loaders, {"base.html": BASE, "page.html": PAGE})],
        libraries={"i18n": i18n},
    )
    return engine, context


# The module each `--side` must have built, checked in `main` before a table is
# printed. Without it the harness's own failure mode is invisible: `--side stock`
# and `--side rust` disagreeing on nothing is what a dead substitution looks like,
# and it is the state this file was in between M18.2 and the round that added this.
EXPECTED_ENGINE_MODULE = {
    "rust": "django_rust_templates.engine.engine",
    "stock": "django.template.engine",
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def native_artifact(side: str) -> str:
    """The extension this venv has installed, and which build of it that is.

    Half of every figure below is the binary, and until this line existed nothing
    printed which one. The profile is not readable from Python, so it is inferred
    from the artefact: `cargo` writes the two profiles to `target/debug/lib_native.*`
    and `target/release/lib_native.*`, `maturin` installs one of them, and **equal
    content** identifies it. Compared by `sha256` rather than by byte size, which
    was the first version and is not the same question: two builds of different
    source can share a length, so a size match is a guess that happens to be right
    most of the time, and this line's whole job is to be right about the file the
    table was measured on.

    **What this can and cannot see.** It reads the installed extension and the two
    candidates, and never the source — so it answers *which build is this*, and it
    cannot answer *is it current*. Those are different questions and only the
    second one is a correctness hazard: a table measured on an extension older than
    the change it describes is wrong about the code, while one measured on the wrong
    profile is merely slower. Freshness is not this line's job and no comparison of
    two artefacts could be it — a source edit changes neither file, so the match
    survives it — which is why `scripts/check.sh` **builds** the extension before
    the suites rather than inferring that it is up to date. A file matching neither
    candidate is one installed from a wheel built elsewhere, or a `target/` that has
    been cleaned; it says `unknown` rather than guessing, because a wrong "release"
    here would be a table that lies about its own subject.

    Printed on both sides and labelled on the stock one: the file is the same in
    both processes, but the stock side does not render through it, and a reader who
    took that line for a fact about the control would be reading it backwards.
    """
    try:
        from django_rust_templates import _native
    except ImportError:
        return "native:  not importable (a source tree with no built extension?)"
    path = Path(_native.__file__)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    matches = []
    for profile in ("debug", "release"):
        for candidate in sorted((PROJECT_ROOT / "target" / profile).glob("lib_native.*")):
            if hashlib.sha256(candidate.read_bytes()).hexdigest() == digest:
                matches.append(f"{profile} ({candidate.relative_to(PROJECT_ROOT)})")
    found = (
        "; ".join(matches)
        if matches
        else "unknown — no built dylib has this hash (a wheel built elsewhere?)"
    )
    other = " (installed here; this side does not render through it)" if side == "stock" else ""
    return f"native:  {path.name} {path.stat().st_size} bytes sha256:{digest[:16]} -> {found}{other}"


# --- measurement -------------------------------------------------------------


def measure(fn, iters: int, repeats: int) -> tuple[float, float, str]:
    """Best and worst per-call microseconds, plus the joined output of one call.

    `fn` is called with no arguments and must return a `str`; one return value is
    kept so the caller can hash it and compare the two engines' bytes.
    """
    for _ in range(max(3, iters // 4)):  # warmup
        out = fn()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(iters):
            out = fn()
        samples.append((time.perf_counter() - start) / iters)
    return min(samples) * 1e6, max(samples) * 1e6, out


def build_workloads(engine, context):
    """Each entry: (name, callable-per-measurement, iter count)."""

    def realistic_render():
        t = engine.get_template("page.html")
        ctx = context({"rows": ROWS})
        return lambda: t.render(ctx)

    def realistic_parse():
        ctx = context({"rows": ROWS})
        return lambda: engine.get_template("page.html").render(ctx)

    def large_parse():
        ctx = context({"s": {}})
        # `engine.from_string`, not `Template(...)`: the bare `Template` reaches
        # `Engine.get_default()`, which needs a TEMPLATES setting this harness
        # does not configure. Same compile-then-render work either way.
        return lambda: engine.from_string(LARGE).render(ctx)

    def tiny_parse():
        ctx = context({"a": "hello world"})
        return lambda: engine.from_string("{{ a|upper }}").render(ctx)

    return [
        ("realistic/render", realistic_render, 200),
        ("realistic/parse", realistic_parse, 100),
        ("large/parse", large_parse, 100),
        ("tiny/parse", tiny_parse, 2000),
    ]


# --- the FFI census ----------------------------------------------------------


class Counter:
    """Counts calls per `_native` name over one workload's iteration."""

    def __init__(self):
        self.counts: dict[str, int] = {}
        self._originals = {}

    def install(self, native):
        for name in dir(native):
            if name.startswith("_"):
                continue
            fn = getattr(native, name)
            # Skip classes: `_native.ParseError` and friends are callable but
            # wrapping one breaks `except _native.ParseError` in `render.py`.
            if isinstance(fn, type) or not callable(fn):
                continue
            self.counts[name] = 0
            self._originals[name] = fn

            def make(name, fn):
                def wrapper(*a, **k):
                    self.counts[name] += 1
                    return fn(*a, **k)

                return wrapper

            setattr(native, name, make(name, fn))

    def uninstall(self, native):
        for name, fn in self._originals.items():
            setattr(native, name, fn)


def run_census(engine, context, iters: int) -> dict[str, int]:
    """Per-workload `_native` call counts, as deltas over `iters` iterations."""
    from django_rust_templates import _native

    counter = Counter()
    counter.install(_native)
    try:
        for name, make, _ in build_workloads(engine, context):
            fn = make()
            for _ in range(3):  # warmup so import-lazy paths settle
                fn()
            before = dict(counter.counts)
            for _ in range(iters):
                fn()
            delta = {n: counter.counts[n] - before[n] for n in counter.counts}
            per_call = {n: c / iters for n, c in delta.items() if c}
            detail = ", ".join(f"{n}={c:.1f}/call" for n, c in
                               sorted(per_call.items(), key=lambda kv: -kv[1]))
            print(f"  census {name:18s} {detail or '(no crossings)'}")
    finally:
        counter.uninstall(_native)
    return counter.counts


# --- main --------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repeats", type=int, default=7)
    ap.add_argument("--scale", type=float, default=1.0, help="multiply every iter count")
    ap.add_argument("--count", action="store_true", help="print the FFI call census (rust side)")
    ap.add_argument(
        "--side",
        choices=sorted(EXPECTED_ENGINE_MODULE),
        required=True,
        help="which engine to build; required, because a default is what went "
        "silently wrong when the substitution was deleted",
    )
    args = ap.parse_args()

    engine, context = make_engine(args.side)
    built = f"{type(engine).__module__}.{type(engine).__qualname__}"
    print(f"engine:  {built}")
    print(f"django:  {django.get_version()}")
    print(native_artifact(args.side))
    if type(engine).__module__ != EXPECTED_ENGINE_MODULE[args.side]:
        # The control on the harness itself. A table is a claim about two engines,
        # so the one thing that must not be inferred from the numbers is which
        # engine produced them — and this is the check that was missing.
        print(
            f"REFUSING: --side {args.side} must build "
            f"{EXPECTED_ENGINE_MODULE[args.side]}, and it built "
            f"{type(engine).__module__}"
        )
        return 2
    print()

    results = {}
    for name, make, iters in build_workloads(engine, context):
        iters = max(1, int(iters * args.scale))
        best, worst, out = measure(make(), iters, args.repeats)
        results[name] = out
        print(f"{name:18s} {best:9.1f} us/call   (worst {worst:9.1f}, {iters} iters)")

    if args.count and args.side == "rust":
        print()
        run_census(engine, context, 50)

    # There is no `--check` flag: the two sides are two processes, so the hash of
    # each workload's output is printed and a wrapper compares them.
    print()

    for name, out in results.items():
        print(f"sha256 {name:18s} {hashlib.sha256(out.encode()).hexdigest()[:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
