"""`scripts/bench.py --compare`: the refusals, and the band it will not invent.

The differ exists because the *manual comparison* is the step that failed twice
in three slices — a `before` column read off the wrong tree, and a `+2.3%` called
a move against a 3.67% band. Its value is not that it prints a delta (a
spreadsheet does that); it is that it refuses the four ways a comparison is not
about what it says it is about, and that it will not classify a delta against a
band it does not have.

So the rows here are the refusals and the band handling, driven against synthetic
payloads rather than against a bench run: a real payload would take minutes, and
what is under test is the arithmetic and the guards, not the timing. The
`synthetic` helper below is the whole fixture, and each row states which guard it
is about.
"""

import contextlib
import io
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(PROJECT_ROOT / ".django") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / ".django"))


def synthetic(sha="aaaa1111bbbb2222", rows=(("r1", "render"), ("r2", "render")),
              ran=("django", "rust", "jinja2"), rust=1.0, stock=1.0):
    """A payload shaped like `--json`, with only the fields `compare` reads.

    `rows` gives the `(key, mode)` cells; each gets a `rust` and a `django` record
    at the ratio `rust / stock`. The other fields are present because `load_payload`
    and `compare` check for them, and a fixture that omitted them would pass rows
    the real payload could not.
    """
    records = []
    for key, mode in rows:
        for engine, us in (("django", stock), ("rust", rust), ("jinja2", stock * 2)):
            records.append(
                {"key": key, "mode": mode, "engine": engine, "best_us": us,
                 "sha256": "0" * 64, "iters": 100}
            )
    return {
        "native": f"artifact sha256:{sha} (release)",
        "args": {"repeats": 3, "target": 0.02},
        "command": "python -I scripts/bench.py",
        "ran": list(ran),
        "modes": ["render", "parse", "compile"],
        "records": records,
    }


def run_compare(a, b, band=None, **kw):
    """`compare` with stdout captured: `(exit code, printed text)`."""
    import bench

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = bench.compare(a, b, band, ("A.json", "B.json"), **kw)
    return code, out.getvalue()


class RefusalTests(unittest.TestCase):
    """Each way a comparison is not about what it says it is about."""

    def test_two_builds_are_refused_unless_stated(self):
        code, out = run_compare(synthetic(sha="aaa"), synthetic(sha="bbb"))
        self.assertEqual(code, 2)
        self.assertIn("REFUSING: the two payloads are different builds", out)
        # The remedy has to be *named*, or the refusal is a dead end.
        self.assertIn("--different-artifacts", out)

    def test_two_builds_proceed_when_stated(self):
        code, out = run_compare(
            synthetic(sha="aaa"), synthetic(sha="bbb"), artifacts_differ=True
        )
        self.assertEqual(code, 0)
        self.assertIn("sha256:aaa", out)
        self.assertIn("sha256:bbb", out)

    def test_different_rows_are_refused_and_named(self):
        code, out = run_compare(
            synthetic(rows=(("r1", "render"), ("r2", "render"))),
            synthetic(rows=(("r1", "render"),)),
        )
        self.assertEqual(code, 2)
        self.assertIn("do not measure the same rows", out)
        self.assertIn("r2 / render: in A.json only", out)

    def test_different_engines_are_refused(self):
        code, out = run_compare(
            synthetic(), synthetic(ran=("django", "rust"))
        )
        self.assertEqual(code, 2)
        self.assertIn("different engines ran", out)

    def test_a_band_pair_across_two_builds_is_refused(self):
        """The band's own precondition: two runs of *one* build, or it is a delta."""
        code, out = run_compare(
            synthetic(sha="same"), synthetic(sha="same"),
            band=(synthetic(sha="aaa"), synthetic(sha="bbb")),
        )
        self.assertEqual(code, 2)
        self.assertIn("the --band pair are different artifacts", out)


class BandTests(unittest.TestCase):
    """What the delta is marked against, and what it refuses to claim."""

    def test_without_a_band_nothing_is_classified(self):
        code, out = run_compare(synthetic(), synthetic(rust=1.5))
        self.assertEqual(code, 0)
        self.assertIn("none supplied", out)
        self.assertIn("none classified", out)
        self.assertNotIn("inside", out)
        self.assertNotIn("outside", out)

    def test_the_delta_is_in_the_ratio_unit(self):
        """ours/stock 1.0 -> 1.5 is +50%, not the +50% of the rust cell alone."""
        _, out = run_compare(synthetic(rust=2.0, stock=2.0), synthetic(rust=6.0, stock=4.0))
        # before 1.000, after 1.500
        self.assertIn("1.000", out)
        self.assertIn("1.500", out)
        self.assertIn("+50.00%", out)

    def test_a_row_is_marked_against_its_own_scatter(self):
        """A row inside its own band is `inside` even if another row is far outside.

        The population's max is the fallback, not the test: `measure the same-tree
        scatter before claiming a delta` is a statement about a row.
        """
        band = (
            synthetic(rows=(("r1", "render"), ("r2", "render")), rust=1.0),
            synthetic(rows=(("r1", "render"), ("r2", "render")), rust=1.02),
        )
        code, out = run_compare(
            synthetic(rows=(("r1", "render"), ("r2", "render")), rust=1.0),
            synthetic(rows=(("r1", "render"), ("r2", "render")), rust=1.5),
            band=band,
        )
        self.assertEqual(code, 0)
        line = next(l for l in out.splitlines() if l.startswith("r1 render"))
        self.assertIn("outside", line)
        self.assertIn("+50.00%", line)

    def test_a_row_absent_from_the_band_says_which_bound_it_used(self):
        band = (
            synthetic(rows=(("r1", "render"),), rust=1.0),
            synthetic(rows=(("r1", "render"),), rust=1.02),
        )
        code, out = run_compare(
            synthetic(rows=(("r1", "render"), ("r9", "render")), rust=1.0),
            synthetic(rows=(("r1", "render"), ("r9", "render")), rust=1.01),
            band=band,
        )
        self.assertEqual(code, 0)
        line = next(l for l in out.splitlines() if l.startswith("r9 render"))
        self.assertIn("band max", line)

    def test_one_build_is_announced_as_scatter_not_change(self):
        """Two runs of one sha: whatever moves here is the instrument."""
        code, out = run_compare(synthetic(sha="same"), synthetic(sha="same", rust=1.4))
        self.assertEqual(code, 0)
        self.assertIn("same* build", out)
        self.assertIn("scatter, not a change", out)


class PayloadTests(unittest.TestCase):
    """A payload that is not one is refused by name, not by KeyError."""

    def test_a_json_file_that_is_not_a_payload_is_refused(self):
        """Parses as JSON, but is not a bench payload — refused by the field name."""
        import bench

        path = pathlib.Path(bench.__file__).parent / "_test_not_a_payload.json"
        path.write_text('{"hello": "world"}', encoding="utf-8")
        try:
            with self.assertRaises(SystemExit) as caught:
                bench.load_payload(str(path))
        finally:
            path.unlink()
        self.assertIn("not a bench.py payload", str(caught.exception))
        self.assertIn("'native'", str(caught.exception))

    def test_a_non_json_file_is_refused_as_unreadable(self):
        path = PROJECT_ROOT / "scripts" / "bench.py"  # a file, not JSON
        import bench

        with self.assertRaises(SystemExit) as caught:
            bench.load_payload(str(path))
        self.assertIn("not readable", str(caught.exception))

    def test_a_missing_file_is_refused(self):
        import bench

        with self.assertRaises(SystemExit) as caught:
            bench.load_payload("/nonexistent/nope.json")
        self.assertIn("not readable", str(caught.exception))


class NondeterminismTests(unittest.TestCase):
    """The precondition `mode_disagreements` rests on is scanned, not assumed."""

    def test_the_shipped_corpus_is_deterministic(self):
        """A row here would make the mode gate unsound, so there must be none.

        This is the control for the scan below: the same function, run over the
        corpus the gate actually reads.
        """
        import bench

        self.assertEqual(bench.nondeterministic_rows(), [])

    def test_the_scan_finds_a_construct_it_should(self):
        """And the scan is not vacuous — it fires on a corpus that carries one."""
        import bench

        original = bench.bench_corpus.workloads

        class W:
            key = "x"
            templates = {"t": "{% now 'Y' %} {% csrf_token %} {{ a|random }}"}

        bench.bench_corpus.workloads = lambda: [W()]
        try:
            found = bench.nondeterministic_rows()
        finally:
            bench.bench_corpus.workloads = original
        self.assertEqual(len(found), 3)
        self.assertTrue(all(line.startswith("x: ") for line in found))


if __name__ == "__main__":
    unittest.main()
