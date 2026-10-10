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

**Two modes per row.** `parse` compiles the template and renders it, through
`from_string` on all three engines — the compile a cold request pays. `render` is
the same template compiled once and rendered repeatedly, which is the hot path
with the parse removed. Both are reported for every row rather than one being
chosen for the reader, since which one matters depends on whether the reader is
asking about a page cache or a cold request. `from_string` rather than
`get_template` is not a detail: see `measure` for the cache asymmetry that made
the first version of this column not a comparison at all.

**An equality gate, not a courtesy.** Each row's output is hashed per engine and
compared against the control's. `rust` differing from `django` anywhere is a hard
failure and exits non-zero: byte-identical output is the drop-in criterion this
package exists to meet, and a timing table whose two sides render different bytes
is not a comparison. `jinja2` differing is not a failure — it is a different
template language — but it is printed, per row, and the report lists the rows
where the corpus is *not* a byte-for-byte intersection.

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
import shlex
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
MODES = (RENDER, PARSE)

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
    },
    PARSE: {
        "unit": "microseconds per call",
        "what": "compile a template and render it, in one call",
        "better": "lower",
        "why": "the same work costs fewer microseconds",
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
    # A workload whose data the render mutates would drift across the samples and
    # be reported as a timing. Cheap to catch, and silently wrong if not caught.
    if out != first:
        raise AssertionError(
            f"{workload.key}/{mode} on {engine.name}: the output changed between the "
            f"first and the last call, so this row is not a fixed amount of work"
        )
    return min(samples) * 1e6, max(samples) * 1e6, iters, out


MIN_ITERS = 5
MAX_ITERS = 20000


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
    header = f"{'workload':{width}s} {'mode':6s} {'iters':>6s} " + " ".join(
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
                    f"{key:{width}s} {mode:6s} {iters_cell} "
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
    args = ap.parse_args()

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

    print(f"python:  {platform.python_version()} on {platform.platform()}")
    print(f"django:  {django.get_version()}")
    print(native_artifact())
    print(f"corpus:  {len(bench_corpus.workloads())} rows, {len(engines)} engine(s): {', '.join(engines)}")
    if dropped:
        print(
            f"dropped: {', '.join(dropped)} — not installed. The comparison is then a "
            f"subset and the report says so:\n         {bench_engines.INSTALL_HINT}"
        )
    print()

    records = run(engines, args.repeats, args.target, )
    verdicts = equality(records)

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

    payload = build_payload(records, verdicts, engines, args)
    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    if args.html:
        import bench_report

        Path(args.html).write_text(bench_report.render(payload), encoding="utf-8")
        print(f"wrote {args.html}")
    return 0


def build_payload(records, verdicts, engines, args) -> dict:
    return {
        "meta": environment(),
        "native": native_artifact(),
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
        "families": {
            f: bench_corpus.FAMILY_SUMMARY[f] for f in bench_corpus.FAMILIES
        },
        "records": records,
        "verdicts": {f"{k[0]}|{k[1]}": v for k, v in verdicts.items()},
    }


if __name__ == "__main__":
    sys.exit(main())
