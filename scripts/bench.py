#!/usr/bin/env python
"""The project's public evidence: three engines, one corpus, one table, one report.

    source scripts/env.sh
    maturin develop --release                       # or every number is about the wrong file
    .venv/bin/python -I scripts/bench.py            # table
    .venv/bin/python -I scripts/bench.py --html docs/bench.html

**Three engines, and the control is one of them.** `django` is stock Django's DTL
and every ratio in the report is taken against it; `rust` is this package;
`jinja2` is the comparison Pete asked for, wired as a Django backend so all three
answer one call shape. `scripts/bench_engines.py` owns that wiring and the reason
it lives in an out-of-venv directory.

**The corpus is two families, and the split is measured, not chosen.**
`scripts/bench_corpus.py` carries the argument in full; in one line, the
`micro` and `real-world` rows are the three-engine *syntax intersection* and are
asserted to hash identically on all three, while `django-only` holds the
constructs the intersection excludes — filter arguments, the i18n library,
`forloop.*`, `{% empty %}` — and runs the two engines that have them. Every row
prints which engines ran it and why the others did not, because a row that
silently drops an engine is a row whose column a reader will fill in from the
row above it.

**Three modes per row.** `parse` compiles the template and renders it, through
`from_string` on all three engines — the compile a cold request pays. `render` is
the same template compiled once and rendered repeatedly, which is the hot path
with the parse removed. `compile` is `from_string` alone, with no render at all.
All three are reported for every row rather than one being chosen for the reader,
since which one matters depends on whether the reader is asking about a page
cache, a cold request, or the cost of a deploy that re-parses every template.
`from_string` rather than `get_template` is not a detail: see `measure` for the
cache asymmetry that made the first version of this column not a comparison at
all. **`compile` is not `parse - render`** — the two terms of that subtraction are
a compile of *this* iteration's template against a warm handle that has already
paid its lazy work, so it carries `compile + (cold - warm)` and the second term is
not small. Measured at M19 step 5 the subtraction read −1.3 us where this mode
read +17.5 us on `real-world/normal`: they disagree in sign, which is how the mode
came to exist (`.review/m19-measurements/step5.md`).

**An equality gate, not a courtesy.** Each row's output is hashed per engine and
compared against the control's. `rust` differing from `django` anywhere is a hard
failure and exits non-zero: byte-identical output is the drop-in criterion this
package exists to meet, and a timing table whose two sides render different bytes
is not a comparison. `jinja2` differing is not a failure — it is a different
template language — but it is printed, per row, and the report lists the rows
where the corpus is *not* a byte-for-byte intersection. The `compile` mode's
identity gate rests on the corpus fixing its own output, which is a precondition
rather than a fact: `nondeterministic_rows` scans for the constructs that would
break it and says so on every run, so a row carrying the clock is named rather
than read as a bug three modes deep.

**`--compare` is the other half of the instrument.** A single run is a level;
what a slice needs is a delta, and a delta taken by hand between two runs is
where two of the last three slices entered a baseline error — a `before` column
read off the wrong tree, and a `+2.3%` called a move against a 3.67% band.
`--compare A.json B.json` prints the provenance of both runs (artifact, command,
repeats), refuses two corpora or two engine sets, refuses two builds unless
`--different-artifacts` states that is the intent, and prints each row's delta in
the ratio unit. With `--band A B` — two payloads of *one* build — each delta is
marked inside or outside its own row's scatter; without it, nothing is
classified, because a delta with no band is a number and not a finding.

**What is measured.** `perf_counter` around a tight inner loop; the reported
figure is the **best** per-call time in microseconds, with the worst beside it so
a noisy run is visible rather than hidden. The iteration count is calibrated once
per (row, mode) on the **control** and used for every engine on that row, so all
three are timed over the same number of calls: calibrating per engine hands a
faster engine a larger N and therefore a tighter best-of-N, which is a bias no
column would have shown. The count is printed, because 4 iterations and 40000
iterations are different measurements of the same thing.

**The build profile is half of this instrument and it is printed.** ``pip
install`` compiles with ``--release``; ``maturin develop`` — what a checkout runs
— installs the *dev* profile, and the gap is 1.8x-3.3x. ``native:`` prints the
loaded extension's sha256 against ``target/{debug,release}``, and ``refuses``
says so if the artefact is neither. It identifies the *build*, never the
*current* build: a source edit changes neither file, so freshness is
``scripts/check.sh``'s job, which builds before it measures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Settings are configured in exactly one place in this tree, and this file is not
# it: `tests/test_settings_configuration.py` refuses a second `settings.configure`
# under `scripts/` or `tests/`, because it is a no-op once settings exist, so
# *which module runs first* would decide the values. `filter_probe` also inserts
# the pinned `.django` checkout on `sys.path`, which is what makes `import django`
# work at all under `-I` (the flag implies `-E`, so `PYTHONPATH` is ignored).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import django  # noqa: E402

import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RENDER = "render"
PARSE = "parse"
COMPILE = "compile"
MODES = (RENDER, PARSE, COMPILE)

# What the number under each mode *is*, and which way is better. It lives in the
# payload rather than in the report's prose because a chart that states its
# direction in a hand-written caption is a chart whose direction can go stale
# silently — and every chart in this report is currently "lower", so the failure
# mode is not "a wrong direction" but "a new chart inherits the old one". A mode
# added here without an entry is a `KeyError` at report time rather than a chart
# with no direction on it.
MODE_METRIC = {
    RENDER: {
        "unit": "microseconds per call",
        "what": "render a template that is already compiled",
        "better": "lower",
        "why": "the same work costs fewer microseconds",
        "button": "render only (template pre-compiled)",
    },
    PARSE: {
        "unit": "microseconds per call",
        "what": "compile a template and render it, in one call",
        "better": "lower",
        "why": "the same work costs fewer microseconds",
        "button": "parse & render (template compiled each call)",
    },
    COMPILE: {
        "unit": "microseconds per call",
        "what": "compile a template without rendering it",
        "better": "lower",
        "why": "the same compile costs fewer microseconds",
        "button": "compile only (template parsed, not rendered)",
    },
}


# --- the extension identity --------------------------------------------------------


def native_artifact(side: str = "rust") -> str:
    """The extension this venv installed, and which build of it that is.

    The profile is not readable from Python, so it is inferred from the artefact:
    `cargo` writes `target/debug/lib_native.*` and `target/release/lib_native.*`,
    `maturin` installs one, and equal content identifies it — compared by
    `sha256`, because a size match is a guess that happens to be right most of the
    time and this line's job is to be right about the file the table was measured
    on. A file matching neither candidate is a wheel built elsewhere or a cleaned
    `target/`; it says `unknown` rather than guessing, because a wrong "release"
    here is a table that lies about its own subject.
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
        else "unknown - no built dylib has this hash (a wheel built elsewhere?)"
    )
    return f"native:  {path.name} {path.stat().st_size} bytes sha256:{digest[:16]} -> {found}"


def environment() -> dict:
    """Everything a reader needs to know whether this table is about their machine."""
    return {
        "django": django.get_version(),
        "cpython": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": str(os.cpu_count()),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# --- measurement -------------------------------------------------------------------


def calibrate(engine, workload, handle, mode, context, target: float) -> int:
    """How many calls make one sample worth about `target` seconds on `engine`.

    **The calibration call is warmed first.** The first call on a `render` handle
    pays the lazy compile, so calibrating on it reads a compile as a render and
    picks an iteration count one to two orders of magnitude too low. Measured:
    `micro/text` at 2.5 us came out as 61 iterations where `--target` asks for
    8000, and `django-only/realistic` as 10. The figures stayed sane because the
    *samples* are warm and normalised; what was wrong was the precision, and it was
    wrong in the direction that looks fine.

    This is called once per (row, mode) on the **control** engine and the count is
    then used for every engine on that row. Calibrating per engine instead — the
    first version — hands a fast engine a larger N and therefore a tighter
    best-of-N than a slow one gets, which is a bias in favour of whichever engine
    the row is least interesting for.
    """
    if mode == RENDER:

        def call():
            return engine.render(handle, context)

    else:
        source = workload.templates[workload.entry]

        if mode == COMPILE:

            def call():
                return engine.from_string(source)

        else:

            def call():
                return engine.render(engine.from_string(source), context)

    for _ in range(3):  # settle lazy work before anything is timed
        call()
    start = time.perf_counter()
    call()
    once = max(time.perf_counter() - start, 1e-7)
    return max(MIN_ITERS, min(MAX_ITERS, int(target / once)))


def measure(
    engine,
    workload,
    handle,
    mode: str,
    context,
    repeats: int,
    target: float,
    pin_iters: int | None = None,
):
    """Best and worst per-call microseconds, the iteration count, and one output.

    **Both modes compile from source, and that is the only way the two are
    comparable.** `render` uses a handle compiled once, outside the loop. `parse`
    calls ``from_string(source)`` every iteration — and *not*
    ``get_template(name)``, which was the first version and was not a comparison at
    all: Django's ``Engine.get_template`` returns a lazily-compiled ``Template``
    that re-parses on each render, while Jinja2's ``Environment.get_template``
    serves an **already-compiled** template out of a cache (as did minijinja's
    environment, the fourth engine this benchmark used to carry). Measured on
    `micro/text`, that made Jinja2's parse cost 0.7 us more than
    its render where Django's cost 2.4 us more — the column was reading *cache
    lookup* against *re-parse*, and printing it beside "ours/stock" invited exactly
    the wrong conclusion.

    ``from_string`` is unambiguous on all three: it compiles a fresh template on
    every engine, it is what the historical `large/parse` row measured, and
    Jinja2's cache is not consulted by it. ``{% extends %}`` and
    ``{% include %}`` still resolve, because the engine's loader is what serves
    them and all three engines are constructed with the row's whole template set.

    **`pin_iters` is how a row stays a comparison.** `run` calibrates on the
    control and passes the count here for every engine on the row, so all three are
    timed over the same number of calls and a faster engine's best-of-N is not
    quietly taken over a larger sample. It is `None` only when the control is not in
    this run, which `run` refuses to let happen silently.

    The context is built once by the caller and not per call: see the class
    docstring in `bench_engines` for why, and for what that leaves unmeasured.
    """
    source = workload.templates[workload.entry]

    if mode == RENDER:

        def call():
            return engine.render(handle, context)

    elif mode == COMPILE:

        def call():
            return engine.from_string(source)

    else:

        def call():
            return engine.render(engine.from_string(source), context)

    for _ in range(3):  # settle lazy work before anything is timed, pinned or not
        first = call()
    if pin_iters is None:
        start = time.perf_counter()
        call()
        once = max(time.perf_counter() - start, 1e-7)
        iters = max(MIN_ITERS, min(MAX_ITERS, int(target / once)))
    else:
        iters = pin_iters

    for _ in range(max(3, iters // 4)):  # warmup
        call()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(iters):
            out = call()
        samples.append((time.perf_counter() - start) / iters)
    # **`compile` mode's product is a compiled template, not bytes**, so the
    # identity gate reads what it *renders*. That keeps one invariant across all
    # three modes — a (row, engine) hashes the same bytes whichever mode timed it —
    # and it is checked rather than assumed: `main` refuses a run whose modes
    # disagree. Rendering here is outside every timed loop, so it reaches no figure.
    out_bytes = engine.render(out, context) if mode == COMPILE else out
    first_bytes = engine.render(first, context) if mode == COMPILE else first
    # A workload whose data the render mutates would drift across the samples and
    # be reported as a timing. Cheap to catch, and silently wrong if not caught.
    if out_bytes != first_bytes:
        raise AssertionError(
            f"{workload.key}/{mode} on {engine.name}: the output changed between the "
            f"first and the last call, so this row is not a fixed amount of work"
        )
    return min(samples) * 1e6, max(samples) * 1e6, iters, out_bytes


MIN_ITERS = 5
MAX_ITERS = 20000

# The floor row's key. It is deliberately not in the corpus: a workload is a
# *construct*, and this one renders nothing, so putting it in `workloads()` would
# add a do-nothing row to every pooled count on the page — which is the defect the
# fastest-engine tile was already fixed for once. It is measured here, beside the
# rows, and reported as a number rather than as a row.
FLOOR_KEY = "__floor__"
FLOOR_ENTRY = "floor.html"


def measure_floor(engines: list[str], repeats: int, target: float) -> dict:
    """Per (mode, engine), what a call costs when the template does nothing.

    **This is the number that says whether a micro row is about its construct.**
    Every row pays the engine's call overhead before it does any template work, and
    the overheads are not equal between engines — measured at M19, stock Django and
    this package are both ~1.72 us and Jinja2 is ~3.52 us, in the same process, on
    the same three-method interface. A row whose whole cost is near that figure is a
    measurement of the call; the report prints the share (`call N%`) beside every
    cell so a reader can tell which rows those are instead of taking the family's
    word for it.

    **The floor is per mode, because the call is not the same call.** `render`'s is
    `Template.render` on an empty node list; `parse`'s is that plus the compile of
    the empty source; `compile`'s is the compile alone. Each mode's share is taken
    against its own — dividing a compile cell by the render floor would read a
    compile as "mostly call" for the opposite reason.

    **It is not subtracted from anything.** The subtraction is tempting and it is
    wrong at the row level: a loop's fixed setup is *not* the empty template's cost.
    Fitting `cost(n) = a + b*n` over 5/20/50 rows (`.review/m19-measurements/
    micro_slope.py`) puts the loop's own intercept at ~4.0 us against the empty
    template's ~1.7 us, so subtracting the floor would leave ~2.3 us of loop setup
    inside a column that claims to be per-item. The floor is reported as itself.

    Same instrument and same pin discipline as `run`: the count is calibrated on the
    control, once per mode, and handed to every engine.
    """
    workload = bench_corpus.Workload(
        FLOOR_KEY,
        "floor",
        "a template that renders nothing, so the row is the call",
        {FLOOR_ENTRY: ""},
        FLOOR_ENTRY,
        {},
        tuple(engines),
        False,
        "",
    )
    built: dict = {}
    out: dict = {}
    try:
        for name in engines:
            built[name] = bench_engines.build(name, workload.templates, {})
        contexts = {name: built[name].context({}) for name in engines}
        control = bench_engines.CONTROL
        pins = {}
        if control in built:
            for mode in MODES:
                pins[mode] = calibrate(
                    built[control],
                    workload,
                    built[control].get(FLOOR_ENTRY),
                    mode,
                    contexts[control],
                    target,
                )
        for mode in MODES:
            out[mode] = {}
            for name in engines:
                engine = built[name]
                out[mode][name] = measure(
                    engine,
                    workload,
                    engine.get(FLOOR_ENTRY),
                    mode,
                    contexts[name],
                    repeats,
                    target,
                    pin_iters=pins.get(mode),
                )[0]
    finally:
        for engine in built.values():
            engine.close()
    return out


def run(engines: list[str], repeats: int, target: float) -> list[dict]:
    """One result record per (row, mode, engine).

    The engine is rebuilt per workload, because the template set *is* the engine's
    loader: an engine left over from the previous row would answer this row's
    `{% include %}` out of the last row's corpus. It is a dict lookup and a
    `DictLoader` either way, and it is paid once per row rather than inside the
    timed loop, so it cannot reach a figure.

    **Every engine on a row is timed over the same number of calls.** The count
    comes from one calibration on the control, taken here, and is handed to
    `measure` as `pin_iters`. Calibrating per engine would give a faster engine a
    larger N and a correspondingly tighter best-of-N — a bias that flatters exactly
    the engine the row is least interesting for, and one no column in the output
    would have shown.
    """
    records = []
    for workload in bench_corpus.workloads():
        wanted = [e for e in workload.engines if e in engines]
        if not wanted:
            continue
        built: dict = {}
        try:
            for name in wanted:
                built[name] = bench_engines.build(
                    name, workload.templates, workload.libraries(name)
                )
            contexts = {name: built[name].context(workload.data) for name in wanted}
            pins = {}
            if bench_engines.CONTROL in built:
                for mode in MODES:
                    handle = built[bench_engines.CONTROL].get(workload.entry)
                    pins[mode] = calibrate(
                        built[bench_engines.CONTROL],
                        workload,
                        handle,
                        mode,
                        contexts[bench_engines.CONTROL],
                        target,
                    )
            for engine_name in wanted:
                engine = built[engine_name]
                for mode in MODES:
                    handle = engine.get(workload.entry)
                    best, worst, iters, out = measure(
                        engine,
                        workload,
                        handle,
                        mode,
                        contexts[engine_name],
                        repeats,
                        target,
                        pin_iters=pins.get(mode),
                    )
                    records.append(
                        {
                            "key": workload.key,
                            "family": workload.family,
                            "summary": workload.summary,
                            "note": workload.note,
                            "mode": mode,
                            "engine": engine_name,
                            "identity": engine.identity,
                            "best_us": best,
                            "worst_us": worst,
                            "iters": iters,
                            "repeats": repeats,
                            "sha256": hashlib.sha256(out.encode()).hexdigest(),
                            "bytes": len(out),
                        }
                    )
        finally:
            for engine in built.values():
                engine.close()
    return records


# --- the FFI census ----------------------------------------------------------------


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


def run_census(iters: int) -> None:
    """Per-workload `_native` call counts per render — the FFI-crossing census."""
    from django_rust_templates import _native

    counter = Counter()
    counter.install(_native)
    try:
        for workload in bench_corpus.workloads():
            if "rust" not in workload.engines:
                continue
            engine = bench_engines.build(
                "rust", workload.templates, workload.libraries("rust")
            )
            handle = engine.get(workload.entry)
            context = engine.context(workload.data)
            for _ in range(3):
                engine.render(handle, context)
            before = dict(counter.counts)
            for _ in range(iters):
                engine.render(handle, context)
            delta = {n: counter.counts[n] - before[n] for n in counter.counts}
            per_call = {n: c / iters for n, c in delta.items() if c}
            detail = ", ".join(
                f"{n}={c:.1f}/call" for n, c in sorted(per_call.items(), key=lambda kv: -kv[1])
            )
            print(f"  census {workload.key:26s} {detail or '(no crossings)'}")
            engine.close()
    finally:
        counter.uninstall(_native)


# --- reporting ----------------------------------------------------------------------


def equality(records: list[dict]) -> dict:
    """Per (row, mode): which engines agree with the control, and the disagreements.

    The verdict is a *set* of engines that produced the control's bytes. An engine
    that did not run the row is absent from the set rather than counted as
    agreeing, which is the mistake this whole shape exists to make impossible.
    """
    by_row: dict[tuple, dict[str, dict]] = {}
    for rec in records:
        by_row.setdefault((rec["key"], rec["mode"]), {})[rec["engine"]] = rec
    verdicts = {}
    for key, per_engine in by_row.items():
        control = per_engine.get(bench_engines.CONTROL)
        if control is None:
            verdicts[key] = {"control_sha256": None, "agree": [], "differ": []}
            continue
        agree = [e for e, r in per_engine.items() if r["sha256"] == control["sha256"]]
        differ = [e for e, r in per_engine.items() if r["sha256"] != control["sha256"]]
        verdicts[key] = {
            "control_sha256": control["sha256"],
            "ran": sorted(per_engine),
            "agree": sorted(agree),
            "differ": sorted(differ),
        }
    return verdicts


def print_table(records: list[dict], verdicts: dict, engines: list[str]) -> None:
    """The corpus as a table, grouped by family and mode, one line per row."""
    shown = [e for e in bench_engines.ENGINES if e in engines]
    width = max((len(r["key"]) for r in records), default=10)
    width = max(width, 12)
    header = f"{'workload':{width}s} {'mode':7s} {'iters':>6s} " + " ".join(
        f"{e:>10s}" for e in shown
    )
    print(header + f" {'ours/stock':>10s}  out")
    print("-" * len(header) + "-" * 24)
    for family in bench_corpus.FAMILIES:
        rows = [r for r in records if r["family"] == family]
        if not rows:
            continue
        print(f"\n== {family} — {bench_corpus.FAMILY_SUMMARY[family]}")
        for key in dict.fromkeys(r["key"] for r in rows):
            for mode in MODES:
                cells = {}
                control_iters = None
                for rec in rows:
                    if rec["key"] == key and rec["mode"] == mode:
                        cells[rec["engine"]] = rec["best_us"]
                        if rec["engine"] == bench_engines.CONTROL:
                            control_iters = rec["iters"]
                if not cells:
                    continue
                marks = ""
                for e in bench_engines.ENGINES:
                    if e not in shown:
                        marks += "-"
                    elif e not in cells:
                        marks += " "
                    elif e in verdicts[(key, mode)]["differ"]:
                        marks += "x"
                    else:
                        marks += "="
                ratio = (
                    cells["rust"] / cells["django"]
                    if "rust" in cells and "django" in cells
                    else float("nan")
                )
                ratio_cell = f"{ratio:10.3f}" if ratio == ratio else f"{'-':>10s}"
                iters_cell = f"{control_iters:6d}" if control_iters else f"{'-':>6s}"
                print(
                    f"{key:{width}s} {mode:7s} {iters_cell} "
                    + " ".join(
                        f"{cells[e]:10.1f}" if e in cells else f"{'-':>10s}" for e in shown
                    )
                    + f" {ratio_cell}  {marks}"
                )
    print()
    print("out: '=' output identical to the django control, 'x' not, '-' engine absent,")
    print("     ' ' engine did not run this row. ours/stock = rust best / django best.")
    print("iters: calls per timed sample, calibrated once per row on the *control*")
    print("     and used for every engine on that row, so all engines are timed")
    print("     over the same number of calls to about --target seconds each.")


def refusals(records: list[dict], verdicts: dict) -> list[str]:
    """Every way this table is not about what it says it is about."""
    out = []
    for (key, mode), verdict in sorted(verdicts.items()):
        if "rust" in verdict["ran"] and "django" in verdict["ran"] and "rust" in verdict["differ"]:
            out.append(
                f"{key} / {mode}: our engine rendered different bytes from stock Django "
                f"({verdict['control_sha256'][:16]} against "
                f"{next(r['sha256'][:16] for r in records if r['key'] == key and r['mode'] == mode and r['engine'] == 'rust')})"
            )
    return out


def disagreement_detail(records: list[dict], verdicts: dict) -> list[str]:
    """Rows where a *non-control* engine differs — informational, printed anyway."""
    lines = []
    for (key, mode), verdict in sorted(verdicts.items()):
        others = [e for e in verdict["differ"] if e != "rust"]
        if not others:
            continue
        lines.append(f"  {key} / {mode}: {', '.join(others)} render different bytes from stock Django")
    return lines


def mode_disagreements(records: list[dict]) -> list[str]:
    """Every (row, engine) whose modes did not all hash the same bytes.

    **This is what makes `compile` a measurement of the same subject.** A
    compile-only figure is comparable to the other two only if the template it
    compiled is the one they rendered, and `measure` renders a compile-mode
    handle for precisely that reason — it returns rendered bytes rather than the
    handle. Here the three shas are compared, so "the modes are three timings of
    one template" is checked instead of asserted. A mode that diverged would be a
    column of the same table measuring something else.
    """
    by_row: dict[tuple, dict[str, str]] = {}
    for rec in records:
        by_row.setdefault((rec["key"], rec["engine"]), {})[rec["mode"]] = rec["sha256"]
    out = []
    for (key, engine), per_mode in sorted(by_row.items()):
        if len(set(per_mode.values())) > 1:
            detail = ", ".join(f"{m}={s[:12]}" for m, s in sorted(per_mode.items()))
            out.append(f"{key} / {engine}: modes rendered different bytes ({detail})")
    return out


# --- comparing two runs ------------------------------------------------------------

# DTL constructs whose output is **not a function of the source and the context**:
# the clock, a fresh CSRF token, a random draw. `mode_disagreements` compares three
# renders of one source and refuses when they differ, which is right for every row
# that is deterministic — but on a row carrying one of these the three *should*
# differ, so the gate would fire for a reason that is not a render bug. The scan is
# lexical and deliberately so: it has one job, to let a failure name its own cause
# at the point it prints, and a lexical miss still fails loudly (as a disagreement).
NONDETERMINISTIC = (
    (r"\{%\s*now\b", "{% now %} — reads the clock"),
    (r"\{%\s*csrf_token\b", "{% csrf_token %} — a fresh token per render"),
    (r"\{%\s*lorem\b", "{% lorem %} — random words"),
    (r"\|\s*random\b", "|random — a fresh draw per render"),
)


def nondeterministic_rows() -> list[str]:
    """Corpus rows carrying a construct whose output the source does not fix.

    The precondition `mode_disagreements` rests on, made visible rather than
    assumed: a row listed here is one where a mode disagreement is *expected*, so
    the reader learns that from this line instead of from a refusal that reads
    like a render bug.
    """
    out = []
    for workload in bench_corpus.workloads():
        if getattr(workload, "deterministic", False):
            continue
        for text in workload.templates.values():
            for pattern, why in NONDETERMINISTIC:
                if re.search(pattern, text):
                    out.append(f"{workload.key}: {why}")
    return out


def load_payload(path: str) -> dict:
    """A `--json` payload, with every field a comparison reads checked present."""
    try:
        pay = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{path}: not readable as a bench.py payload ({exc})") from exc
    for field in ("native", "args", "command", "ran", "modes", "records"):
        if field not in pay:
            raise SystemExit(f"{path}: not a bench.py payload — no {field!r}")
    return pay


def payload_sha(pay: dict) -> str:
    return pay["native"].split("sha256:")[1].split()[0]


def ratios_of(pay: dict) -> dict:
    """`(key, mode) -> ours/stock` for one payload, the unit a delta is quoted in.

    The ratio rather than the microsecond, for `bench_scatter`'s reason: stock's
    own cell is a denominator shared by every ratio in its row, so its noise
    cancels in a way a per-cell µs delta cannot show.
    """
    cells = {}
    for rec in pay["records"]:
        cells[(rec["key"], rec["mode"], rec["engine"])] = rec["best_us"]
    out = {}
    for (key, mode, engine), us in cells.items():
        if engine != "rust":
            continue
        stock = cells.get((key, mode, "django"))
        if stock:
            out[(key, mode)] = us / stock
    return out


def scatter_band(band_a: dict, band_b: dict) -> tuple[dict, float, float]:
    """Per-row and population scatter of one build, ratio against ratio.

    Two runs of the *same* artifact, so the difference cannot be a change: what it
    is, is how much a delta can be when nothing moved. Returns
    `{(key, mode): |rel|}` plus the median and max over the rows both runs share.
    """
    ra, rb = ratios_of(band_a), ratios_of(band_b)
    per_row = {}
    for cell in set(ra) & set(rb):
        if ra[cell]:
            per_row[cell] = abs(rb[cell] - ra[cell]) / ra[cell]
    mags = sorted(per_row.values())
    median = statistics.median(mags) if mags else 0.0
    return per_row, median, (mags[-1] if mags else 0.0)


def compare(pay_a: dict, pay_b: dict, band: tuple | None, names: tuple, *,
            artifacts_differ: bool = False) -> int:
    """Print what moved from run A to run B, and refuse to call noise a move.

    **This exists because the comparison was the step that failed.** Two of the
    last three slices entered their record with a baseline error — a `before`
    column read off the wrong tree, and a `+2.3%` called a move against a 3.67%
    band — and both were made by hand at exactly this step. So the provenance
    prints before the numbers (which tree, which command, which corpus), the
    deltas print as a column, and each is marked against the scatter of *its own
    row* rather than against a population figure borrowed from a summary.
    """
    name_a, name_b = names
    sha_a, sha_b = payload_sha(pay_a), payload_sha(pay_b)

    if sha_a != sha_b and not artifacts_differ:
        print(
            "REFUSING: the two payloads are different builds — "
            f"sha256:{sha_a} and sha256:{sha_b}.\n"
            "A comparison across two builds is the point of this mode, so it has\n"
            "to be stated rather than assumed: pass --different-artifacts."
        )
        return 2

    ra, rb = ratios_of(pay_a), ratios_of(pay_b)
    only_a, only_b = sorted(set(ra) - set(rb)), sorted(set(rb) - set(ra))
    if only_a or only_b:
        print("REFUSING: the two runs do not measure the same rows, so a delta "
              "between them is a delta between two corpora.\n")
        for cell in only_a:
            print(f"  {cell[0]} / {cell[1]}: in {name_a} only")
        for cell in only_b:
            print(f"  {cell[0]} / {cell[1]}: in {name_b} only")
        return 2
    if pay_a["ran"] != pay_b["ran"]:
        print(f"REFUSING: different engines ran — {pay_a['ran']} against {pay_b['ran']}.")
        return 2

    print(f"before: {name_a}")
    print(f"after:  {name_b}")
    for pay, name in ((pay_a, name_a), (pay_b, name_b)):
        print(f"  {name}: artifact sha256:{payload_sha(pay)}, "
              f"repeats={pay['args']['repeats']} target={pay['args']['target']}")
        print(f"  {name}: {pay['command']}")
    print(f"  rows: {len(ra)} (key, mode) cells, engines {', '.join(pay_a['ran'])}")
    print()

    same_build = sha_a == sha_b
    if same_build:
        print("Both runs are the *same* build. Any delta below is scatter, not a "
              "change — it is the band a later claim owes, not the claim.\n")

    per_row_band = None
    if band is not None:
        band_a, band_b = band
        if payload_sha(band_a) != payload_sha(band_b):
            print("REFUSING: the --band pair are different artifacts "
                  f"(sha256:{payload_sha(band_a)} and sha256:{payload_sha(band_b)}). "
                  "A band is two runs of one build; across two builds it is the "
                  "delta it was meant to bound.")
            return 2
        per_row_band, band_median, band_max = scatter_band(band_a, band_b)
        print(f"band: two runs of sha256:{payload_sha(band_a)} — "
              f"{len(per_row_band)} shared rows, median |rel| {band_median:.2%}, "
              f"max {band_max:.2%}")
        print("      (a row's own figure where the band pair has that row, the "
              "band's max where it does not)")
    else:
        band_max = None
        print("band: none supplied. The deltas below are printed unclassified — "
              "nothing here says which of them are moves.")
        print("      Measure the band with --band A.json B.json, two runs of one build.")
    print()

    print(f"{'row':<32} {'before':>8} {'after':>8} {'rel':>9} {'band':>8}  verdict")
    print("-" * 74)
    rows = []
    for (key, mode) in ra:
        delta = (rb[(key, mode)] - ra[(key, mode)]) / ra[(key, mode)] if ra[(key, mode)] else 0.0
        rows.append((abs(delta), key, mode, delta))
    outside = inside = unbanded = 0
    for _, key, mode, delta in sorted(rows, key=lambda r: -r[0]):
        if per_row_band is None:
            band_cell, verdict = f"{'-':>8}", ""
            unbanded += 1
        else:
            own = per_row_band.get((key, mode))
            bound = own if own is not None else band_max
            band_cell = f"{bound:>7.2%}"
            if abs(delta) > bound:
                verdict, outside = "outside", outside + 1
            else:
                verdict, inside = "inside", inside + 1
            if own is None:
                verdict += " (band max)"
        print(f"{key + ' ' + mode:<32} {ra[(key, mode)]:>8.3f} {rb[(key, mode)]:>8.3f} "
              f"{delta:>+8.2%} {band_cell}  {verdict}")

    print()
    if per_row_band is None:
        print(f"{unbanded} row(s), none classified: supply --band to separate a move "
              "from the instrument's own scatter.")
    else:
        print(f"{outside} outside its row's band, {inside} inside.")
        print("A row marked `inside` is *not resolvable to better than the band* — that "
              "is not the same finding as `did not move`.")
    return 0


# --- main --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Three engines, one corpus: stock Django, this package, Jinja2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--engines", default=",".join(bench_engines.ENGINES))
    ap.add_argument("--repeats", type=int, default=7)
    ap.add_argument(
        "--target",
        type=float,
        default=0.05,
        help="seconds of work per sample; the iteration count is calibrated to it",
    )
    ap.add_argument("--quick", action="store_true", help="--repeats 3 --target 0.02")
    ap.add_argument("--list", action="store_true", help="list the corpus and stop")
    ap.add_argument("--count", action="store_true", help="print the FFI crossing census")
    ap.add_argument("--json", default=None, help="write the full payload here")
    ap.add_argument("--html", default=None, help="write a self-contained HTML report here")
    ap.add_argument(
        "--bakerydemo", default=str(PROJECT_ROOT / "docs/benchmarks/bakerydemo-2026-10-12.json"),
        help="recorded Bakerydemo JSON to include in the report",
    )
    ap.add_argument(
        "--compare",
        nargs=2,
        metavar=("BEFORE.json", "AFTER.json"),
        default=None,
        help="print what moved between two --json payloads and stop",
    )
    ap.add_argument(
        "--band",
        nargs=2,
        metavar=("A.json", "B.json"),
        default=None,
        help="two payloads of ONE build; their scatter bounds each delta "
        "(without it the deltas print unclassified)",
    )
    ap.add_argument(
        "--different-artifacts",
        action="store_true",
        help="allow --compare the two payloads to carry different sha256 values",
    )
    args = ap.parse_args()

    if args.compare:
        pay_a, pay_b = (load_payload(p) for p in args.compare)
        band = (
            tuple(load_payload(p) for p in args.band) if args.band else None
        )
        return compare(
            pay_a, pay_b, band, tuple(args.compare),
            artifacts_differ=args.different_artifacts,
        )

    if args.list:
        import runpy

        runpy.run_module("bench_corpus", run_name="__main__")
        return 0

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    unknown = [e for e in engines if e not in bench_engines.ENGINES]
    if unknown:
        print(f"REFUSING: unknown engine(s) {unknown}; known: {list(bench_engines.ENGINES)}")
        return 2
    for required in (bench_engines.CONTROL, "rust"):
        if required not in engines:
            print(f"REFUSING: {required} is not optional — it is the control or the subject")
            return 2
    missing = bench_engines.missing_engines()
    dropped = [e for e in engines if e in missing]
    engines = [e for e in engines if e not in missing]

    if args.quick:
        args.repeats, args.target = 3, 0.02

    from django.conf import settings

    settings.ROOT_URLCONF = "bench_urls"
    settings.STATIC_URL = "/static/"
    settings.MEDIA_URL = "/media/"

    print(f"python:  {platform.python_version()} on {platform.platform()}")
    print(f"django:  {django.get_version()}")
    native = native_artifact()
    print(native)
    print(f"corpus:  {len(bench_corpus.workloads())} rows, {len(engines)} engine(s): {', '.join(engines)}")
    if dropped:
        print(
            f"dropped: {', '.join(dropped)} — not installed. The comparison is then a "
            f"subset and the report says so:\n         {bench_engines.INSTALL_HINT}"
        )
    print()

    records = run(engines, args.repeats, args.target, )
    verdicts = equality(records)
    floor = measure_floor(engines, args.repeats, args.target)

    # The engine each column claims to be, checked against the class that built it:
    # a table is a claim about three engines, so which engine produced a column is the
    # one thing that must never be inferred from the numbers.
    for rec in records:
        if rec["identity"] != bench_engines.IDENTITY[rec["engine"]]:
            print(
                f"REFUSING: engine {rec['engine']!r} built "
                f"{rec['identity']}, expected {bench_engines.IDENTITY[rec['engine']]}"
            )
            return 2

    print_table(records, verdicts, engines)

    for mode in MODES:
        cells = " ".join(f"{e} {floor[mode][e]:.3f}" for e in engines if e in floor[mode])
        print(f"\nfloor [{mode}]: a template that renders nothing, microseconds per call")
        print(f"              {cells}")
    print("              a row's `call N%` in the table below is its cost divided by")
    print("              its engine's floor, so a row near 100% measures the call.")

    detail = disagreement_detail(records, verdicts)
    if detail:
        print("\nwhere the corpus is not a byte-for-byte intersection (informational —")
        print("Jinja2 is a different template language, not another build):")
        print("\n".join(detail))

    if args.count:
        print()
        run_census(20)

    bad = refusals(records, verdicts)
    if bad:
        print("\nREFUSING: our engine and stock Django rendered different bytes.\n")
        print("\n".join(f"  {line}" for line in bad))
        return 2

    # The precondition the mode gate below rests on, printed rather than assumed: a
    # row carrying the clock or a random draw *should* disagree between modes, so on
    # such a row a refusal would not be a render bug. Printed on every run, so the
    # reader learns it from this line rather than from a refusal three modes deep.
    suspects = nondeterministic_rows()
    if suspects:
        print("\nthe corpus carries constructs whose output the source does not fix, so the")
        print("mode gate below is unsound on these rows — a green run does not clear them:")
        print("\n".join(f"  {line}" for line in suspects))

    mixed = mode_disagreements(records)
    if mixed:
        print("\nREFUSING: one (row, engine)'s modes rendered different bytes from each")
        print("other, so the modes are not three timings of one template.\n")
        print("\n".join(f"  {line}" for line in mixed))
        if suspects:
            print("\n  and the corpus carries constructs whose output the source does not")
            print("  fix, so a disagreement on these rows is expected rather than a bug.")
        return 2

    payload = build_payload(records, verdicts, engines, args, floor, native=native)
    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    if args.html:
        import bench_report

        Path(args.html).write_text(bench_report.render(payload), encoding="utf-8")
        print(f"wrote {args.html}")
    return 0


def build_payload(records, verdicts, engines, args, floor, native=None) -> dict:
    page_files = {
        "real-world/normal": "report.html", "real-world/complex": "invoice.html",
        "real-world/large": "large.html", "django-only/realistic": "translated_report.html",
    }
    bakery_path = Path(args.bakerydemo)
    return {
        "bakerydemo": json.loads(bakery_path.read_text(encoding="utf-8")) if bakery_path.is_file() else None,
        "workloads": {
            w.key: {
                "source": w.templates[w.entry],
                "libraries": list(w.library_names),
                "url": "https://github.com/ipmb/django-rust-templates/blob/main/scripts/"
                + ("bench_templates/" + page_files[w.key] if w.key in page_files else "bench_features.py"),
            }
            for w in bench_corpus.workloads()
        },
        "meta": environment(),
        # Snapshot before measuring: another build can replace the installed
        # file while this process still runs the extension it already loaded.
        "native": native if native is not None else native_artifact(),
        "args": {"repeats": args.repeats, "target": args.target},
        # The exact invocation, so a report that turns up in a directory six
        # months from now can be re-run rather than admired. `sys.orig_argv` is
        # the real command line; `sys.argv` after `-I` is only the argument list.
        "command": shlex.join(
            [Path(sys.executable).name, *getattr(sys, "orig_argv", [sys.executable])[1:]]
        ),
        "engines": [
            {
                "name": e,
                "control": e == bench_engines.CONTROL,
                "identity": bench_engines.IDENTITY[e],
            }
            for e in bench_engines.ENGINES
        ],
        "ran": engines,
        "modes": list(MODES),
        # Per mode, what the number is and which way is better — read by the
        # report's charts and table so each one states its direction, rather than
        # carrying a caption someone wrote once (see `MODE_METRIC`).
        "metrics": {mode: MODE_METRIC[mode] for mode in MODES},
        # Per (mode, engine), the cost of a template that renders nothing. Every
        # cell's `call N%` is read from this, and it is the *only* thing on the page
        # that separates a row's dispatch from its construct work. See
        # `measure_floor` for why it is reported rather than subtracted.
        "floor": floor,
        "families": {
            f: bench_corpus.FAMILY_SUMMARY[f] for f in bench_corpus.FAMILIES
        },
        "records": records,
        "verdicts": {f"{k[0]}|{k[1]}": v for k, v in verdicts.items()},
    }


if __name__ == "__main__":
    sys.exit(main())
