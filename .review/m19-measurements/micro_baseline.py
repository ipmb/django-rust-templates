#!/usr/bin/env python
"""Is a `micro` row measuring the construct, or the call that carries it?

Pete's question: "Are the micro benchmarks valuable at all? As you note they are
dominated by overhead. Any way you can make them fairer?"

The claim under test is mine, and it is falsifiable: that a micro row's ratio is
mostly per-call overhead rather than construct work. If that is true then each
engine's *own* cost for a template that renders nothing — same engine object, same
`get_template`, same `render(handle, context)` call — contains that overhead, and
subtracting it leaves the construct.

**It uses `bench.py`'s own `calibrate`/`measure`, not a timing written here.** A
second timing is a second instrument, and two instruments disagree for reasons that
have nothing to do with the workload; the first draft of this file hand-rolled one
and its rows did not match the report's. Reusing the harness means the "row" column
below *is* the report's figure by construction, so anything the residual shows is
the baseline and not the ruler. It also inherits the pin: one calibration per
(workload, mode) on the control, handed to every engine, so a faster engine is not
quietly given a larger best-of-N.

**One engine per workload, and that is not tidiness.** The first draft built one
engine holding every workload's templates at once and `update`d them into a single
dict — but every micro workload's entry point is `page.html`, so each `update`
overwrote the last and all ten "rows" rendered `micro/extends`'s template. The tell
was that every row cost the same ~20 us regardless of workload, which is a shape no
set of ten different templates produces. `bench.py` builds one engine per row for
this reason, and so does this.

Three things this has to show, in order:

  1. **How big the baseline is** against each micro row, per engine. If it is a
     small fraction, my claim was wrong and the rows were fine as they were.
  2. **Whether the residual is a measurement.** `micro/text` renders one text node
     and does no work, so its residual must be small and positive for every engine.
     A residual that comes out negative, or larger than the row, means the
     subtraction is not cancelling what I think it is.
  3. **Whether the residual changes the verdict.** If Jinja2's ~0.28x on the micro
     rows is overhead, its residual ratio should move; and the rows where it still
     loses should be the ones with real per-item work.

Every value printed goes through an explicit numeric format; a transcript that names
an input it did not evaluate cannot be pasted back.

Run:  .venv/bin/python -I .review/m19-measurements/micro_baseline.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

# `bench` configures settings (through `filter_probe`) and puts the pinned
# `.django` checkout on `sys.path` at import; importing it is how this file gets
# the same django the report measured against.
import bench  # noqa: E402
import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

# The empty template. It is *not* in the corpus and must not be: a row that renders
# nothing has no construct to name, so it belongs here as an instrument rather than
# there as a workload. It carries a name no workload uses, so a leftover
# `page.html` collision cannot hide it.
EMPTY_NAME = "__empty__.html"
EMPTY = ""

# `bench.py`'s own defaults (`--repeats 7 --target 0.05`), so a figure here is the
# figure `docs/bench.html` was measured with; a different N would be a second
# instrument wearing the first one's name.
REPEATS = 7
TARGET = 0.05


def main():
    workloads = [w for w in bench_corpus.workloads() if w.family == "micro"]
    engines = [e for e in bench_engines.ENGINES if e not in bench_engines.missing_engines()]
    control = bench_engines.CONTROL

    print("interpreter : %s" % sys.version.split()[0])
    print("engines     : %s" % ", ".join(engines))
    print("instrument  : bench.measure, best of %d, calibrated to ~%.0f ms on the control"
          % (REPEATS, TARGET * 1e3))
    print("baseline    : a template whose source is %r, in the same engine as the row" % EMPTY)
    print()

    rows: dict = {}
    baselines: dict = {}
    for w in workloads:
        wanted = [e for e in engines if e in w.engines or e == control]
        built = {
            name: bench_engines.build(
                name, {**w.templates, EMPTY_NAME: EMPTY}, w.libraries(name)
            )
            for name in wanted
        }
        contexts = {name: built[name].context(w.data) for name in wanted}
        # One pin per workload, taken on the control over the *row* handle, and
        # then used for the empty template too — so the baseline's best-of-N is
        # taken over the same number of calls as the row's.
        pin = bench.calibrate(
            built[control], w, built[control].get(w.entry), bench.RENDER,
            contexts[control], TARGET,
        )
        rows[w.key] = {}
        baselines[w.key] = {}
        for name in wanted:
            eng = built[name]
            row_us = bench.measure(
                eng, w, eng.get(w.entry), bench.RENDER, contexts[name],
                REPEATS, TARGET, pin_iters=pin,
            )[0]
            base_us = bench.measure(
                eng, w, eng.get(EMPTY_NAME), bench.RENDER, contexts[name],
                REPEATS, TARGET, pin_iters=pin,
            )[0]
            rows[w.key][name] = row_us
            baselines[w.key][name] = base_us

    # --- 1. the baseline, per engine, pooled over the workloads --------------------

    print("1. what the call costs with nothing to do")
    print("   %-18s %10s %10s %s" % ("engine", "empty us", "spread", "rows it is on"))
    for name in engines:
        values = sorted(baselines[w.key][name] for w in workloads if name in baselines[w.key])
        print(
            "   %-18s %10.3f %10s"
            % (name, values[len(values) // 2], "%.3f-%.3f" % (values[0], values[-1]))
        )
    print()

    # --- 2. each row against its own engine's baseline ------------------------------

    print("2. each row against its own engine's baseline")
    print(
        "   %-18s %-8s %10s %10s %10s %8s %9s"
        % ("workload", "engine", "row us", "empty us", "residual", "base %", "raw r/d")
    )
    for w in workloads:
        per = rows[w.key]
        raw = per["rust"] / per[control] if "rust" in per and control in per else None
        for name in engines:
            if name not in per:
                continue
            row, base = per[name], baselines[w.key][name]
            extra = "%9.2fx" % raw if (name == "rust" and raw is not None) else ""
            print(
                "   %-18s %-8s %10.3f %10.3f %+10.3f %7.0f%% %s"
                % (w.key, name, row, base, row - base, base / row * 100, extra)
            )
        print()

    # --- 3. the same rows, as construct cost rather than call cost ------------------

    print("3. the same rows, as construct cost rather than call cost")
    print(
        "   %-18s %10s %10s %10s %10s %9s"
        % ("workload", "raw r/d", "res r/d", "raw j/d", "res j/d", "base %")
    )
    for w in workloads:
        per, base = rows[w.key], baselines[w.key]
        if control not in per:
            continue
        d, db = per[control], base[control]
        cells = []
        for other in ("rust", "jinja2"):
            if other in per:
                cells += [
                    "%.2fx" % (per[other] / d),
                    "%.2fx" % ((per[other] - base[other]) / (d - db)),
                ]
            else:
                cells += ["-", "-"]
        print(
            "   %-18s %10s %10s %10s %10s %8.0f%%"
            % (w.key, cells[0], cells[1], cells[2], cells[3], db / d * 100)
        )
    print()

    # --- the control: a text node does no work, so its residual should be small ----

    print("4. the control — `micro/text` renders one text node and does no work")
    print("   Its residual is the subtraction's own error bar: whatever it reads,")
    print("   no residual below it means anything.")
    for name in engines:
        if name not in rows["micro/text"]:
            continue
        row = rows["micro/text"][name]
        resid = row - baselines["micro/text"][name]
        print("   %-18s residual %+8.3f us  (%+.0f%% of the row)" % (name, resid, resid / row * 100))
    print()
    print("   and what each row's own cost *is* (control's row minus its baseline):")
    for w in workloads:
        if control not in rows[w.key]:
            continue
        print(
            "   %-18s %8.3f us  %s"
            % (w.key, rows[w.key][control] - baselines[w.key][control], w.summary)
        )


main()
