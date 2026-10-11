#!/usr/bin/env python
"""Where `_native.compile_template`'s time goes, on both materialiser paths.

    source scripts/env.sh
    maturin develop --release
    .venv/bin/python -I scripts/compile_profile.py            # every bench row
    .venv/bin/python -I scripts/compile_profile.py large      # one row

`scripts/bench.py` answers "how fast is the engine" and reports one number per
mode. This answers "which part of a compile is the part to work on", and it needs
a second measurement the bench does not take: the same compile with
`parser.materialise` replaced by a stub returning an empty `NodeList`.

    native      `compile_template` with the Rust walk selected, **uninstrumented**.
    python      the same call with the Python walk selected, **uninstrumented**.
                `python - native` is what S1 is worth, and it is the number the
                decision is made from.
    stubbed     the Python path with `materialise` patched out: **the whole compile
                with the walk stubbed, not "the compile minus the walk"**. It still
                allocates a real `NodeList`, still runs both planner checks, still
                cleans up the wire and still builds the `Template`. `plan_list`
                returns `None` for a root with nodes — the stub's `NodeList` has the
                wrong length — and `plan_block_bodies` then returns immediately; for
                an **empty** source the lengths match and an empty plan is built. The
                stub's `contains_nontext` controls neither decision. And because
                `{% partialdef %}` materialises its body during the *parse*, the stub
                changes those bodies and their parse-time state too.
    materialise the **outermost** `materialise` calls, **summed per compile**, so the
                walk is its own number rather than a difference of two totals.

    samples     how many outermost `materialise` calls the recorder saw, **across all
                compiles**. This is not one per compile: a body the parser builds
                during the parse — `{% partialdef %}`'s is one — is materialised by
                the host before the root is, so one compile can make several
                non-overlapping outermost calls. Both counts are reported, so a
                template in that shape says so rather than passing quietly.
    compiles    how many compiles the recorder opened. **One per compile is the
                contract, and it is `compiles` that carries it**, not `samples`.

**There is no fourth timing and no derived `plan`.** An earlier draft reported
`(python - stubbed) - materialise` as the plan's cost. It is not a measurement: the
term mixes two passes with a global patched against one pass with nothing patched,
the `stubbed` stub allocates a real `NodeList` that the unpatched run does not, and
the `_timed` wrapper adds a call per compile to the run it measures. The three errors
point in three directions at once, and no test can validate an attribution that is
really an arithmetic coincidence between independent medians. **The plan's cost cannot
be measured from here at all** — `plan_list` (`lib.rs:3918`) and `plan_block_bodies`
(`:4017`) are private functions inside `compile_template`, not exported to Python, so
the only Python-visible quantity containing them is `stubbed`, which also contains the
parse, the wire and `template_for`. Isolating the plan is S3's, which is the
sub-project that changes `plan_list` and can time it where it lives.

**`materialise` recurses, and the first version of this file got that wrong.**
`materialise_node` resolves each child through the same patched global, so a
wrapper that appended on every call recorded *every subtree* — hundreds of entries
on `large_template()`, whose median is the cost of a typical subtree and not of the
compile's materialisation at all. It looked like a number and was the wrong
quantity. Depth is tracked and only the outermost call is recorded; the *clock* is
read only on the outermost call too, so a nested call costs one list read rather than
a `perf_counter` — small, but it is a cost this tool adds to the thing it measures,
and it costs nothing to not add it.

**And an outermost call is still not a compile, which the second version got wrong.**
A body the parser builds during the parse is materialised by the host before the root
is, so one compile can make two or more non-overlapping outermost calls.
`{% partialdef p %}{{ a }}{% endpartialdef %}{% partial p %}` is the smallest such
template, and `repeats=2` recorded **four** calls where a per-compile count would be
two. A median over those four mixes a three-character partial body with a full root
and answers neither question. The recorder therefore attributes each outermost call to
the compile it belongs to and reports the **sum per compile**, and the median is over
those sums. `compiles` and `samples` are both reported, so a template that produces
more than one call per compile says so rather than passing quietly.

**Whether a test can see that second call turns on module identity, and that is why
no row in `tests/test_compile_profile.py` reads a walk count out of the engine.** The
parse-time body is materialised through `render.materialiser()`, which caches
`django_rust_templates.engine.parser` on its first call. Five test modules used to load
a fresh copy of that file *by path* and install it in `sys.modules` under the same name
— `test_materialise`, `test_filter_expr_parity`, `test_parser_parity`,
`test_variable_parity` and `test_ref_table` — so under `scripts/run_unit_tests.sh` the
cache could end up holding the copy while this file's `parser_mod` was the original:
`ShellHost.register_partial_def`'s call then landed on a module whose `materialise` was
never patched, and one call per compile is all the recorder saw. The same source
compiled by hand in a fresh process, before discovery caches anything, shows both.

**All five are ordinary imports now, and the measurement that recorded this reads the
other way.** `render.materialiser() is not compile_profile.parser_mod` was true after
the suite had run and `is` before it; it is `is` on **both** sides now, and
`sys.modules["django_rust_templates.engine.parser"]` is the object it was at import.
The loaders were vestigial, and identically so: every one of them justified itself by
`engine/__init__.py` being the shim's `django.template`, whose own `from .engine import
Engine` then failed — while `engine/__init__.py` itself says its name "is not
`django.template`", and `test_ref_table` imported the `engine` package by name two
lines above its own by-path load.

**None of that changes what this tool has to do** — group by compile and sum,
unconditionally — and it does not change the fixture decision either, though the reason
moves. A row that asked the engine how many calls it made would be measuring the
instrument with the instrument. The two rows that pin the grouping and the summation
script their own compile instead, through a real `materialise` argument tuple captured
from a real one.

**Only `native` and `python` are measured on every call**, by flipping the switch,
rather than once at import. After S1 ships the constant is `True`, and a tool that
read it once would print the native number in both columns: a saving of zero,
reported as a result. The flip is restored in a `finally`, including when the
constant started out absent.

**The two paths are interleaved and warmed, not run as two long blocks.** An earlier
draft ran `repeats` native compiles and then `repeats` python ones. Before S1 exists
the two are the *same code*, so any difference it printed was the machine — and it
printed one: the reviewer measured `native 1849.6 us` against `python 1742.3 us`, a
saving of −107.3 us from two identical paths. Whichever block ran second inherited
the load the first one caused. Each branch now gets one discarded warm call, and the
two are alternated round by round.

**`stubbed` and `materialise` are taken with the switch pinned to Python.** This is
not tidiness: patching `parser.materialise` does nothing on the native branch, so once
Task 8 flips the default the pass would run the Rust walk, record nothing, and raise
"the walk was never called". The branch is selected rather than inherited, and
`test_both_paths_are_measured_whatever_the_switch_says` is the row that keeps it
selected — **by spying on which branch the body actually observed, not by comparing
two timings.** A timing inequality would have passed on the −107.3 us run above.

**The two paths are timed with nothing instrumented, in separate passes.** Patching
`materialise` for the `stubbed` and `materialise` numbers changes what the other two
would measure, so they are taken before any patch is installed and the patch is
removed before the report is printed. **For the number that ships, take
`scripts/bench.py` on the same release build** — this tool's job is to explain a
saving that the bench has already measured, not to be the source of it.

**The boundary is `_native.compile_template`, and the bench's is `from_string`.**
They are not the same call. This file builds the `Parser` and the `ShellHost`
*before* the clock starts, because those are per-request setup and folding them into
the timed region would put them inside `stubbed` — whose whole meaning is "what is
left when the walk is gone". `bench.py`'s `compile` mode times `Engine.from_string`,
which is that setup plus this call, plus the `Template` and `Origin` construction at
the far end.

Measured on `real-world/large`, release build, medians of 25:

    from_string          1879.3 us
    compile_template     1761.2 us

**A difference of about 118 us, and "observed" is as far as that single pair
supports.** It is not a demonstrated fixed offset: the two tools warm differently and
this file's medians of individual calls on `normal` and `complex` read *higher* than
the bench's cells for the same rows, so the sign is not even stable across the corpus.
What survives is the qualitative claim — the setup sits on both paths, so it cancels
in the saving and does not cancel in the columns — and that is why the two tools may
be compared on `python - native` and not on the columns themselves. The bench's
`large compile` cell (1897.2 us) and this file's `python` column (1727.4 us) are the
same compile seen from either end of that setup, and neither is wrong.
"""

import sys
import time
from pathlib import Path

# Settings are configured in exactly one place in this tree, and this file is not
# it: `tests/test_settings_configuration.py` refuses a second `settings.configure`
# under `scripts/` or `tests/`, because it is a no-op once settings exist, so
# *which module runs first* would decide the values. `filter_probe` also inserts
# the pinned `.django` checkout on `sys.path`, which is what makes `import django`
# work at all under `-I` (the flag implies `-E`, so `PYTHONPATH` is ignored), and
# it drops this directory back on `sys.path` for `bench_corpus`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

from django_rust_templates import _native, render  # noqa: E402
from django_rust_templates.engine import parser as parser_mod  # noqa: E402
from django_rust_templates.engine.base import NodeList  # noqa: E402

UNKNOWN_ORIGIN = render.UNKNOWN_ORIGIN


def _fresh(source, engine):
    """A parser for one compile, built the way `render` builds one."""
    return render.parser_for(source, engine, UNKNOWN_ORIGIN)


def _host(parser):
    return render.ShellHost(parser)


def _compile(parser, host, source, engine):
    _native.compile_template(source, parser, parser_mod, engine, host, UNKNOWN_ORIGIN)


def _stub_materialise(nodelist_desc, origin=None, resolve_ref=None,
                      register_ref=None, parser=None):
    """A real but empty `NodeList`.

    For a root with nodes, `plan_list` then returns `None` on the length mismatch
    and `plan_block_bodies` returns immediately; for an empty source the lengths
    match and an empty plan is built. Either way this is a *stub*, not a removal:
    the allocation, both planner checks, the wire cleanup and the `Template` all
    still happen, which is why the column is labelled as it is.
    """
    nodelist = NodeList()
    nodelist.contains_nontext = True
    return nodelist


def _on_path(native, body):
    real = getattr(parser_mod, "USE_NATIVE_MATERIALISER", None)
    had = hasattr(parser_mod, "USE_NATIVE_MATERIALISER")
    parser_mod.USE_NATIVE_MATERIALISER = native
    try:
        return body()
    finally:
        # Restore *absence* as absence, not as `False`: a test that started with
        # the constant missing must end that way, or it has silently changed which
        # module the next test is looking at.
        if had:
            parser_mod.USE_NATIVE_MATERIALISER = real
        else:
            del parser_mod.USE_NATIVE_MATERIALISER


def _once(engine, source):
    """One compile on whichever branch is selected right now, in microseconds.

    The `Parser` and the `ShellHost` are built **before** the clock starts: they
    are per-request setup, and folding them in would put them inside `stubbed`.
    """
    parser = _fresh(source, engine)
    host = _host(parser)
    start = time.perf_counter()
    _compile(parser, host, source, engine)
    return (time.perf_counter() - start) * 1e6


def _median(values):
    values.sort()
    return values[len(values) // 2]


def _both_paths(engine, source, repeats):
    """`(native, python)` medians, warmed and interleaved.

    **Interleaved because the two paths are the same code until Task 3.** Two long
    blocks hand whichever runs second the load the first one caused, and on an idle
    machine that is invisible; the reviewer measured −107.3 µs of "saving" out of
    two paths that were byte-identical. Alternating round by round makes a drift in
    machine load land on both columns instead of on one.

    **Warmed because `compile_template` is not the first thing a process does.**
    One discarded call per branch pays the allocator's first-touch cost and the
    interpreter's lazy imports before anything is measured.
    """
    _on_path(True, lambda: _once(engine, source))
    _on_path(False, lambda: _once(engine, source))

    native, python = [], []
    for round_index in range(repeats):
        first, second = (True, False) if round_index % 2 == 0 else (False, True)
        for native_flag in (first, second):
            elapsed = _on_path(native_flag, lambda: _once(engine, source))
            (native if native_flag else python).append(elapsed)
    return _median(native), _median(python)


def decompose(source, engine, repeats=25):
    """Four timings in microseconds, and both counts of the walk.

    Nothing is instrumented while `native` and `python` are timed.
    """
    native, python = _both_paths(engine, source, repeats)

    real = parser_mod.materialise
    per_compile = []
    calls = [0]
    depth = [0]
    live = [None]

    def _attribute(elapsed):
        # **An outermost call is not a compile.** `{% partialdef %}` materialises
        # its body during the parse, so one compile can make several
        # non-overlapping outermost calls; a median over those mixes a partial body
        # with a full root. They are summed per compile and the median is over the
        # sums.
        calls[0] += 1
        if live[0] is None:
            live[0] = [0.0]
            per_compile.append(live[0])
        live[0][0] += elapsed

    def _timed(nodelist_desc, origin=None, resolve_ref=None, register_ref=None,
               parser=None):
        # **The nested case is not timed at all.** `materialise_node` resolves its
        # children through this same global, so this wrapper runs once per subtree;
        # only the outermost call is the walk. The clock is read on the outermost
        # call only, so a nested call costs one list index and nothing else.
        if depth[0]:
            depth[0] += 1
            try:
                return real(nodelist_desc, origin, resolve_ref, register_ref, parser)
            finally:
                depth[0] -= 1
        depth[0] = 1
        start = time.perf_counter()
        try:
            return real(nodelist_desc, origin, resolve_ref, register_ref, parser)
        finally:
            elapsed = (time.perf_counter() - start) * 1e6
            depth[0] = 0
            _attribute(elapsed)

    def _opening(parser, host, source, engine):
        # Opens a compile for the recorder. Called inside the clock, so it is one
        # list store rather than a `perf_counter`.
        live[0] = None
        _compile(parser, host, source, engine)

    def _python_pass():
        # **The branch is selected, not inherited.** Patching `materialise` is a
        # no-op on the native branch, so after Task 8 flips the default this pass
        # would run the Rust walk, record nothing, and raise below — a tool that
        # works in Task 1 and dies in Task 8.
        def run():
            parser_mod.materialise = _stub_materialise
            try:
                stubbed = _median([_once(engine, source) for _ in range(repeats)])
            finally:
                parser_mod.materialise = real

            parser_mod.materialise = _timed
            try:
                for _ in range(repeats):
                    parser = _fresh(source, engine)
                    host = _host(parser)
                    _opening(parser, host, source, engine)
            finally:
                parser_mod.materialise = real
            return stubbed

        stubbed = _on_path(False, run)

        if not per_compile:
            raise RuntimeError("the walk was never called; the patch did not take")
        return stubbed, _median([entry[0] for entry in per_compile]), calls[0], \
            len(per_compile)

    stubbed, materialise, samples, compiles = _python_pass()

    return {
        "native": native,
        "python": python,
        "stubbed": stubbed,
        "materialise": materialise,
        "samples": samples,
        "compiles": compiles,
    }


def report(source, label, engine, repeats=25):
    numbers = decompose(source, engine, repeats)
    saving = numbers["python"] - numbers["native"]
    print(f"{label}  ({len(source)} chars)")
    print(f"  native compile_template               {numbers['native']:9.1f} us")
    print(f"  python compile_template               {numbers['python']:9.1f} us")
    print(f"  S1 saving (python - native)           {saving:9.1f} us"
          f"   ({saving / numbers['python']:5.1%})")
    print(f"  materialise itself                    {numbers['materialise']:9.1f} us"
          f"   ({numbers['materialise'] / numbers['python']:5.1%} of python)")
    print(f"  compile with the walk stubbed         {numbers['stubbed']:9.1f} us")
    print(f"  (walk: {numbers['samples']} outermost call(s) over "
          f"{numbers['compiles']} compile(s) of {repeats})")
    print()


def main(argv):
    import bench_corpus

    from django_rust_templates.engine import Engine

    engine = Engine()
    wanted = argv[1] if len(argv) > 1 else None
    rows = [
        ("real-world/normal", bench_corpus.NORMAL),
        ("real-world/complex", bench_corpus.COMPLEX),
        ("real-world/large", bench_corpus.large_template()),
    ]
    for label, source in rows:
        if wanted and wanted not in label:
            continue
        report(source, label, engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
