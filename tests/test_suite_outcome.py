"""The acceptance run's own outcome, as a refusal.

The hole it closes: the gate reads the suite's exit status and discards the
transcript, so `Ran 1547 tests … OK (skipped=6)` — §2's central claim — was
enforced by nobody. A second guard once checked that a *prose* count names its
run; it went with the prose it read, and this is the half that reads the run.

This file drives the gate with **fabricated transcripts**, which is where an
instrument's conditions belong: a literal the gate's parser is exercised on, so
a reworded Django summary fails here and not in production. The subject — the
real set of six — is asserted against `DECLARED_SKIPS` in the gate itself.
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

