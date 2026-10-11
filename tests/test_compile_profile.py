"""The decomposition reports the shape it claims to.

Timings are not asserted — only that every phase is present, that both paths were
actually measured, and that the walk was grouped and summed per compile rather than
recorded once per subtree or once per call.

**Nothing here asserts a phase is non-negative, and that is deliberate.** The phases
are independent medians of independently timed runs, and a row that fails on a
loaded machine is a row that fails for being a measurement.

**And there is no fourth timing derived from the other three.** An earlier draft
reported `(python - stubbed) - materialise` as the plan's cost; it is an arithmetic
coincidence between three separately instrumented medians rather than a measurement,
and the step that would have reconciled it (Task 1 Step 5) says why the plan cannot
be isolated from Python at all.

**The walk-count rows drive a scripted compile rather than `{% partialdef %}`.**
There is a template that makes `materialise` run twice for one compile — and whether
a row can *see* the second call turns on module identity, not on the engine.
`ShellHost.register_partial_def` materialises the parse-time body through
`render.materialiser()`, which caches the `django_rust_templates.engine.parser`
module on its first call. Five modules used to load a *fresh copy* of that file by
path and install it in `sys.modules` under the same name — `test_materialise`,
`test_filter_expr_parity`, `test_parser_parity`, `test_variable_parity`,
`test_ref_table` — so under `scripts/run_unit_tests.sh` the cache ended up holding the
copy while `compile_profile.parser_mod` was the original: the body's call went to a
module whose `materialise` was never patched, and the recorder saw one call per
compile. The same template compiled by hand in a fresh process, before discovery,
cached the original and showed both.

**That hazard is gone rather than dodged.** All five are ordinary imports now, so
`render.materialiser()` and `compile_profile.parser_mod` are one object before *and*
after discovery, and the count is two per compile under either invocation. The rows
below were never borrowing a count from the engine and still do not — a row that asked
the engine how many calls it made would be measuring the instrument with the
instrument — so what changes here is only the reason, not the fixture.
"""

import contextlib
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import compile_profile  # noqa: E402

from django_rust_templates.engine import Engine  # noqa: E402
from django_rust_templates.engine import parser as parser_mod  # noqa: E402


class _Clock:
    """Monotonic, one fixed step per read."""

    def __init__(self, step_us):
        self.step_us = step_us
        self.reads = 0

    def perf_counter(self):
        self.reads += 1
        return self.reads * (self.step_us / 1e6)


class _ScriptedCompile:
    """A stand-in for `compile_template` making a scripted number of walk calls.

    **The engine's own count is not a usable fixture.** Whether `{% partialdef %}`
    materialises its body during the parse depends on the invocation — two
    outermost calls in a fresh process, one inside `scripts/run_unit_tests.sh` —
    so any number a row read from the engine would be wrong under the other one.

    **The calls it makes are real.** `call` is a genuine `materialise` argument
    tuple captured from an actual compile, so each replayed call goes through the
    production recorder and the real materialiser. Only the compile around them is
    a stand-in, which is the one thing this fixture is entitled to replace.
    """

    def __init__(self, call, counts):
        self.call = call
        self.counts = counts
        self.log = []

    def __call__(self, parser, host, source, engine):
        count = self.counts[len(self.log) % len(self.counts)]
        self.log.append(count)
        for _ in range(count):
            parser_mod.materialise(*self.call)


def _a_real_walk_call(engine):
    """One genuine `materialise` call's arguments, captured from a real compile.

    Hand-building a descriptor would be guessing at the wire; asking the engine for
    one costs a compile and cannot drift from the format.
    """
    real = parser_mod.materialise
    captured = []

    def grab(descriptor, origin=None, resolve_ref=None, register_ref=None,
             parser=None):
        captured.append((descriptor, origin, resolve_ref, register_ref, parser))
        return real(descriptor, origin, resolve_ref, register_ref, parser)

    def compile_once():
        parser = compile_profile._fresh(source, engine)
        compile_profile._compile(parser, compile_profile._host(parser), source, engine)

    source = "{{ a }}"
    parser_mod.materialise = grab
    try:
        # **The switch is pinned to Python, and that is what keeps this working
        # after Task 8.** `_native.compile_template` is handed `parser_mod` as its
        # tree module, so the root's walk call comes back through the name patched
        # above — but only on the Python branch. Once the flip lands, the native
        # branch never calls `materialise`, `captured` stays empty, and the
        # subscripts below raise `IndexError` from inside a fixture.
        compile_profile._on_path(False, compile_once)
    finally:
        parser_mod.materialise = real
    if not captured:
        raise RuntimeError(
            "the pinned compile made no walk call, so there is nothing to replay: "
            "`_a_real_walk_call` and `compile_profile._compile` have diverged"
        )
    return captured[0]


@contextlib.contextmanager
def _scripted(engine, counts, step_us=10.0):
    """`decompose` driven by a scripted compile and a fixed clock.

    The genuine call is captured before the clock is replaced, so the compile that
    produces it is timed by the real one.
    """
    call = _a_real_walk_call(engine)
    scripted = _ScriptedCompile(call, counts)
    real_time = compile_profile.time
    real_compile = compile_profile._compile
    compile_profile.time = _Clock(step_us)
    compile_profile._compile = scripted
    try:
        yield scripted
    finally:
        compile_profile.time = real_time
        compile_profile._compile = real_compile


class DecomposeTests(unittest.TestCase):
    def test_the_report_names_every_phase(self):
        report = compile_profile.decompose("{{ a }}", Engine(), repeats=3)
        self.assertEqual(
            {*report},
            {"native", "python", "stubbed", "materialise", "samples", "compiles"},
        )

    def test_both_paths_are_measured_whatever_the_switch_says(self):
        """The falsifier for the tool outliving the flip — as a spy, not a timing.

        Once Task 8 sets the constant to True, a tool that read it once at import
        would report the native number in both columns: a saving of zero, printed
        as a result.

        **This row used to assert `python > stubbed`, and that was the defect.** An
        earlier draft ran all the native repeats and then all the python ones —
        before S1 exists those are byte-identical code, and the reviewer measured
        `native 1849.6 us` against `python 1742.3 us` out of exactly that ordering.
        A timing inequality would have been green on a tool whose "saving" was
        entirely the machine. It now reads which branch each body *observed*.

        **It is also the regression guard for `_python_pass` losing its branch
        selection.** `stubbed` and `materialise` are taken with `materialise`
        patched; if the pass ran on the native branch, the patches would not take.
        """
        observed = []
        real_on_path = compile_profile._on_path

        def spy(native, body):
            seen = []

            def probe():
                seen.append(getattr(parser_mod, "USE_NATIVE_MATERIALISER", "<absent>"))
                return body()

            try:
                return real_on_path(native, probe)
            finally:
                observed.append((native, seen[0] if seen else None))

        compile_profile._on_path = spy
        try:
            report = compile_profile.decompose("{{ a }}", Engine(), repeats=3)
        finally:
            compile_profile._on_path = real_on_path

        asked = {native for native, _ in observed}
        self.assertEqual(asked, {True, False}, "one of the two paths was never selected")
        for native, seen_value in observed:
            self.assertEqual(
                seen_value, native,
                "a body ran on a branch other than the one it was pinned to",
            )
        self.assertEqual(report["samples"], 3)
        self.assertEqual(report["compiles"], 3)

    def test_the_recorder_sees_one_sample_per_compile(self):
        """The nested-call defect, pinned through the production recorder.

        `materialise` recurses: `materialise_node` resolves each child through
        the same patched global. A wrapper that appended on every call therefore
        recorded *every subtree* — on `large_template()` that is hundreds of
        entries, whose median is the cost of a typical subtree and not of the
        compile's materialisation at all. The first draft did exactly that, which
        is a plausible-looking number that is simply the wrong quantity.

        **An earlier version of this row built its own copy of the wrapper and
        measured that**, so it would have stayed green through the defect it was
        written for. It reads `decompose`'s count of its own recorder now. The
        template is three deep, so a per-subtree recorder reports far more than
        `repeats`.

        No switch flip is needed here, and that is the point: `_python_pass` selects
        the Python branch itself. A row that had to wrap `decompose` in
        `_on_path(False, …)` to get a sample would be a row testing a tool that
        cannot do its job after Task 8.
        """
        source = "{% for x in xs %}{% for y in x %}{% if y %}{{ y }}{% endif %}{% endfor %}{% endfor %}"
        report = compile_profile.decompose(source, Engine(), repeats=3)
        self.assertEqual(report["samples"], 3, "the walk was timed per subtree, not per compile")
        self.assertEqual(report["compiles"], 3)

    def test_the_recorder_groups_the_calls_of_one_compile(self):
        """An outermost call is not a compile, and the two counts are separate.

        The defect this pins: the first recorder counted *outermost calls* and
        medianed over those, so a compile making two of them contributed two
        entries and the median mixed a three-character partial body with a full
        root — a column that answered neither question and looked like a number.
        Fixing it left `samples` as the only count, which is not one per compile.

        The contract is now `compiles == repeats` always, with `samples` free to
        exceed it, and the two are reported separately so a template in that shape
        says so rather than passing quietly.
        """
        engine = Engine()
        with _scripted(engine, counts=[2]) as scripted:
            report = compile_profile.decompose("{{ a }}", engine, repeats=2)
        self.assertEqual(report["compiles"], 2, "one entry per compile is the contract")
        self.assertEqual(report["samples"], 2 * report["compiles"])
        self.assertEqual(scripted.log[-2:], [2, 2], "the fixture stopped making two calls")

    def test_the_walk_durations_are_summed_within_a_compile(self):
        """That `materialise` is a **sum**, which no count can show.

        The counts in the row above prove the recorder grouped its calls
        correctly; they say nothing about what it did with the durations. The code
        review found the gap by mutating `live[0][0] += elapsed` to
        `live[0][0] = elapsed` — **all seven rows stayed green**, so the summation
        was untested and "the median of the last call's duration" would have shipped
        under a label promising the whole walk.

        **The two compiles are made deliberately unequal, and the longer one
        first.** A uniform step makes the arithmetic exact without pinning how many
        times the recorder reads the clock, which is the brittleness this plan
        rejected in round 3. Unequal totals are what separate a median over
        *compiles* from one over *calls*, which the earlier fixture — whose compiles
        were identical — could not, and putting the longer sum first is what
        separates either from a recorder that kept only the last compile. `_median`
        takes the upper of two (`values[len(values) // 2]`), so three calls and then
        two reads 30 µs.

        **The two counts and the expected 30 µs are written out, not derived from the
        script log or from `_median`.** Deriving them would make the row agree with
        the recorder by construction — a wrong `_median` would move both sides, and a
        change that reversed the two compiles would go unnoticed. Written out, the
        log assertion fails loudly instead. It is deliberately coupled to how many
        compiles the earlier passes make, so a change there has to be looked at
        rather than absorbed.

        Three mutations die here: `=` for `+=` reads one step, a median over the five
        individual calls reads one step, and keeping only the last compile reads 20 µs.
        """
        step_us = 10.0
        engine = Engine()
        with _scripted(engine, counts=[3, 2], step_us=step_us) as scripted:
            report = compile_profile.decompose("{{ a }}", engine, repeats=2)

        self.assertEqual(
            scripted.log[-2:], [3, 2],
            "the recorder's two compiles are not the three-call-then-two-call pair "
            "this row is written against: an earlier pass changed how many compiles "
            "it makes, and the expectation below has to be re-derived",
        )
        self.assertEqual(report["compiles"], 2)
        self.assertEqual(report["samples"], 5)
        self.assertAlmostEqual(
            report["materialise"], 30.0, places=3,
            msg="the walk's durations were not summed per compile and then "
                "medianed: `=` for `+=`, a median over the individual calls, or "
                "keeping only the last compile each read a different number here",
        )

    def test_the_switch_is_restored_after_an_exception(self):
        # **Read through `getattr`, because this row runs before the constant
        # exists.** Task 3 is what adds `USE_NATIVE_MATERIALISER` to `parser.py`;
        # a bare attribute read here would make this file fail to run at all until
        # then, which is the same mistake `_on_path` is written to avoid.
        before = getattr(parser_mod, "USE_NATIVE_MATERIALISER", None)
        had = hasattr(parser_mod, "USE_NATIVE_MATERIALISER")
        real_compile = compile_profile._compile
        compile_profile._compile = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with self.assertRaises(RuntimeError):
                compile_profile.decompose("{{ a }}", Engine(), repeats=1)
        finally:
            compile_profile._compile = real_compile
        if had:
            self.assertEqual(parser_mod.USE_NATIVE_MATERIALISER, before)
        else:
            self.assertFalse(hasattr(parser_mod, "USE_NATIVE_MATERIALISER"))

    def test_the_materialise_patch_is_restored_after_an_exception(self):
        """The other restore, and the one the row above does **not** reach.

        That row raises on the first `_compile`, which is the warm call on the
        native branch — before `_python_pass` has installed either patch. So it
        proved `_on_path`'s `finally` and said nothing about the two
        `parser_mod.materialise = …` assignments. This one raises **only while a
        patch is installed**, so the failure lands inside `_python_pass`'s own
        `try`, and a missing `finally` there would leave the suite running against
        a stub for every test that followed.
        """
        real_materialise = parser_mod.materialise
        real_compile = compile_profile._compile

        def boom(*a, **k):
            if parser_mod.materialise is not real_materialise:
                raise RuntimeError("boom")
            return real_compile(*a, **k)

        compile_profile._compile = boom
        try:
            with self.assertRaises(RuntimeError):
                compile_profile.decompose("{{ a }}", Engine(), repeats=2)
        finally:
            compile_profile._compile = real_compile
        self.assertIs(
            parser_mod.materialise, real_materialise,
            "a failed pass left `materialise` patched for every later test",
        )

    def test_the_switch_is_restored_when_it_started_absent(self):
        # The other half: `getattr(…, None)` reads a *missing* constant as off, and
        # the `finally` must put absence back rather than leaving a `False` behind —
        # a test that set it `False` would then be testing a different module than
        # the one it started with.
        #
        # Before Task 3 this starts out true for free, since nothing defines the
        # constant yet; from Task 3 it is the real case. Same row either way.
        had = hasattr(parser_mod, "USE_NATIVE_MATERIALISER")
        real = getattr(parser_mod, "USE_NATIVE_MATERIALISER", None)
        if had:
            del parser_mod.USE_NATIVE_MATERIALISER
        try:
            compile_profile.decompose("{{ a }}", Engine(), repeats=2)
            self.assertFalse(hasattr(parser_mod, "USE_NATIVE_MATERIALISER"))
        finally:
            if had:
                parser_mod.USE_NATIVE_MATERIALISER = real


if __name__ == "__main__":
    unittest.main()
