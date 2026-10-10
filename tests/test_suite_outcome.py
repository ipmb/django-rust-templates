"""The acceptance run's own outcome, as a refusal.

`.review/m15-recon.md` §3.1 measured the hole: `check.sh:138-139` reads the
suite's exit status and discards the transcript, so `Ran 1547 tests … OK
(skipped=6)` — §2's central claim — is enforced by nobody. `check_suite_counts.py`
says the same of itself: it checks that a *prose* count names its run, not that
the number is right.

This file drives the gate with **fabricated transcripts**, which is where an
instrument's conditions belong: a literal the gate's parser is exercised on, so
a reworded Django summary fails here and not in production. The subject — the
real set of six — is asserted against `DECLARED_SKIPS` in the gate itself and
tied to §2's prose by `test_the_declared_skips_are_the_ones_section_2_names`.
"""

import unittest

from scripts.assert_suite_outcome import DECLARED_SKIPS, DECLARED_SUMMARY, outcome


def transcript(ran=1547, summary="OK (skipped=6)", skips=(), extra=""):
    """A stand-in for `runtests.py template_tests -v 2`'s output.

    The teardown lines are not decoration. On the real run the summary is
    followed by eleven `Destroying test database for alias …` lines, so the
    verdict is *not* the transcript's last line and a reader that assumes it is
    refuses every healthy run. They are in the stand-in so that assumption
    cannot survive here.
    """
    lines = ["Testing against Django installed in '/x/.django/django' with up to 10 processes"]
    for dotted in skips:
        lines.append(f"{dotted.split('.')[-1]} ({dotted}) ... skipped 'a reason'")
    lines += [extra] if extra else []
    lines += ["", "----------------------------------------------------------------------", ""]
    lines += [f"Ran {ran} tests in 3.5s", "", summary]
    lines += ["Destroying test database for alias 'default' ('default_1.sqlite3')..."]
    return "\n".join(lines)


HEALTHY = tuple(sorted(DECLARED_SKIPS))


class HealthyRunTests(unittest.TestCase):
    def test_the_declared_outcome_is_accepted(self):
        self.assertEqual(outcome(transcript(skips=HEALTHY)).refusals(), [])

    def test_a_refusal_names_the_skip_that_is_not_declared(self):
        report = outcome(
            transcript(skips=HEALTHY + ("template_tests.unregistered_m15.FabricatedTests.test_x",))
        )
        self.assertTrue(
            any("unregistered_m15" in r for r in report.refusals()),
            report.refusals(),
        )


class RefusalTests(unittest.TestCase):
    def test_a_truncated_transcript_is_refused(self):
        """No summary line at all: the runner died. Silence is not a pass."""
        report = outcome("Testing against Django installed in '/x'\n")
        self.assertTrue(report.refusals())

    def test_a_duplicated_skip_line_is_refused(self):
        """A set comparison hides a doubled line; the report must not."""
        doubled = HEALTHY + (HEALTHY[0],)
        report = outcome(transcript(skips=doubled))
        self.assertTrue(any("twice" in r for r in report.refusals()), report.refusals())

    def test_a_swapped_skip_that_keeps_the_count_at_six_is_refused(self):
        """§2:31 — a count of 6 is satisfied by a different 6."""
        swapped = HEALTHY[:-1] + ("template_tests.unregistered_m15.FabricatedTests.test_y",)
        report = outcome(transcript(skips=swapped))
        self.assertTrue(report.refusals())

    def test_a_failure_is_refused(self):
        report = outcome(transcript(summary="FAILED (failures=1, skipped=6)"))
        self.assertTrue(any("failures=1" in r for r in report.refusals()), report.refusals())

    def test_an_error_is_refused(self):
        report = outcome(transcript(summary="FAILED (errors=1, skipped=6)"))
        self.assertTrue(report.refusals())

    def test_an_expected_failure_is_refused(self):
        """§2:33 — `expected failures=1` exits 0, prints `OK`, and is not zero xfailed.

        Measured in `.review/m15-recon.md` §3.3: a single
        `@unittest.expectedFailure` on a failing test yields
        `OK (expected failures=1)` with exit status 0. The exit status is not
        silent about this clause, it answers wrongly, which is why the gate
        compares the summary *line* instead of trusting `OK`.
        """
        report = outcome(transcript(summary="OK (expected failures=1, skipped=6)"))
        self.assertTrue(any("expected failures" in r for r in report.refusals()), report.refusals())

    def test_an_unexpected_success_is_refused(self):
        """A run with zero failures, zero errors and exactly six skips — and
        still not §2's outcome.

        Measured: `@unittest.expectedFailure` on a test that *passes* prints
        `FAILED (skipped=1, unexpected successes=1)` and exits **1** — the exact
        mirror of `expected failures=1`, which prints `OK (…)` and exits 0. The
        string here is that form with §2's six, so nothing but the summary moved
        and the equality is the only arm that fires. That is why this row and
        the script's docstring both say the clause needs no branch of its own:
        §2:33's "zero `xfail`ed" is answered on the whole line, and the exit
        status answers it a second time.
        """
        report = outcome(
            transcript(summary="FAILED (skipped=6, unexpected successes=1)")
        )
        self.assertTrue(
            any(DECLARED_SUMMARY in r for r in report.refusals()), report.refusals()
        )

    def test_a_summary_followed_by_teardown_lines_is_still_found(self):
        """The verdict is not the last line — `runtests.py` prints eleven teardown
        lines after it on the real run, so a `lines[-1]` reader refuses a healthy
        transcript."""
        report = outcome(transcript(skips=HEALTHY))
        self.assertEqual(report.summary, DECLARED_SUMMARY)
        self.assertEqual(report.refusals(), [])

    def test_a_changed_run_count_is_refused(self):
        report = outcome(transcript(ran=1546, skips=HEALTHY))
        self.assertTrue(any("1546" in r for r in report.refusals()), report.refusals())

    def test_a_skip_on_a_docstringed_test_is_seen(self):
        """`-v 2` splits a documented test across two lines.

        Measured on the transcript `django_suite/run.py -v 2
        template_tests` writes: an undocumented test prints
        `name (dotted) ... skipped 'reason'` on one line, while a documented one
        prints `name (dotted)` and then `Docstring first line. ... skipped
        'reason'` on the next. A parser that requires the `... skipped` on the
        name's own line silently misses every documented skip — and the run that
        would expose it is the regression this gate exists for.
        """
        two_line = (
            "Testing against Django installed in '/x'\n"
            "test_x (template_tests.test_doc.DocTests.test_x)\n"
            "Some docstring. ... skipped 'jinja2'\n"
            "\nRan 1547 tests in 3.5s\n\nOK (skipped=1)\n"
        )
        self.assertEqual(outcome(two_line).skips, ["template_tests.test_doc.DocTests.test_x"])


class SubjectTests(unittest.TestCase):
    """The tie to the document §2's clause is named for.

    §3's ten names were parseable because §3 writes them as a backticked list;
    §2:35-45 composes its six out of a class name plus parenthesised method
    names, and never spells a fully-qualified one. So the literal lives on the
    instrument (`DECLARED_SKIPS`) and the *tie* is this: every component name
    appears in the frame. Weaker than §3's equality, and honest about it —
    `test_the_declared_skips_are_the_ones_section_2_names` fails if §2 drops a
    name or `DECLARED_SKIPS` grows one.
    """

    def test_the_declared_skips_are_the_ones_section_2_names(self):
        from tests.milestone_ownership import between, spec_section

        frame = between(spec_section("2"), "The 6 permitted skips are:", "Any *new* skip")
        for dotted in DECLARED_SKIPS:
            class_name, method = dotted.rsplit(".", 2)[-2:]
            with self.subTest(dotted=dotted):
                self.assertIn(class_name, frame)
                # Not backticked: §2:42 writes the loader's one as a single
                # dotted literal, while §2:36-41 backticks the other five
                # methods individually. Requiring the ticks would fail on the
                # document rather than on the set, which is the wrong direction
                # for this row.
                self.assertIn(method, frame)

    def test_the_frame_reader_refuses_a_moved_anchor(self):
        """The control for the row above: a reworded anchor must raise."""
        from tests.milestone_ownership import between

        with self.assertRaises(AssertionError):
            between("a FRAME b", "gone ", " b")
