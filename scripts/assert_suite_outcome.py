#!/usr/bin/env python3
"""Refuse unless the acceptance run's transcript is §2's outcome.

§2:31-34 and §2:66, read against the tree in `.review/m15-recon.md` §3.1: the
count, the zero-failure clause and "any *new* skip appearing later is a
regression" are carried by prose. `check.sh` runs `django_suite/run.py
template_tests` and reads its exit status; the transcript is printed and
dropped.

The count is the weaker half of §2's own clause, and the recon probed why:
`template_tests/syntax_tests/test_numpy.py:13`'s `@skipIf(numpy is False, …)`
guards two tests §2:64-65 requires to run, and a skipped test is still
collected — so losing Numpy prints `Ran 1547 tests … OK (skipped=8)`, §2's exact
run count, with two tests silently not running. So the gate pins the summary
**line whole** and the six skips **by identity**, and refuses on each of: a run
count that is not 1547, a summary that is not `OK (skipped=6)`, any skip outside
`DECLARED_SKIPS`, and any of the six that did not skip.

Usage:
    python scripts/assert_suite_outcome.py <transcript>
"""

import re
import sys
from pathlib import Path

# §2:31-33 in two constants. The summary is pinned *whole* rather than taken
# apart, because every clause §2 states about the run's outcome moves that one
# line and `OK (…)` with any other qualifier is a different string: a failure or
# an error turns `OK` into `FAILED`, an `expectedFailure` appends
# `expected failures=N` while leaving `OK` (measured, `.review/m15-recon.md`
# §3.3), a changed skip total changes the number. Dissecting the line recovers
# those facts at the cost of a parser that can be wrong about them; comparing it
# cannot.
#
# `unexpected successes=N` — an `@expectedFailure` test that *passed* — looks
# like the same hazard as `expected failures=N` and is not, so it gets no clause
# of its own and its absence here is deliberate. Each measured alone, one
# `@unittest.expectedFailure` row run with `python -I -m unittest`:
#
#     FAILED (unexpected successes=1)   exit 1   wasSuccessful() -> False
#     OK     (expected failures=1)      exit 0   wasSuccessful() -> True
#
# Same shape, opposite verdicts, and only the second is green: on the acceptance
# run both also carry `skipped=6`. So the whole-line pin refuses the first on the
# string and the gate's exit status refuses it a second time, while the second
# needs the string because the status agrees with it. Put both rows in one module
# and the unexpected success dominates the verdict —
# `FAILED (expected failures=1, unexpected successes=1)` — the same asymmetry
# stated a third way.
#
# That third line is the whole argument for comparing a string, so it is worth
# reading twice: **`expected failures=N` never sets the verdict, in any
# combination.** Alone it rides on `OK`; beside an unexpected success it rides on
# a `FAILED` the other qualifier decided. It is not that the current gate misses
# it — no gate reading the exit status ever could, and the pinned interpreter's
# own source says so: `unittest.result.TestResult.wasSuccessful` is
# `failures == errors == 0 and not unexpectedSuccesses`, and `expectedFailures`
# does not appear in it (`inspect.getsource`, CPython 3.14.4). So the whole-line
# pin is the *only* available mechanism for §2:33's "zero `xfail`ed", not the
# cheapest one — which is the answer to a later reader asking why this parses a
# string when there is an exit code in hand.
#
# A reader who sees only the qualifier-parsing branch here might take the silence
# for an oversight.
#
# It is a literal because this is an *instrument* condition — Django's output
# format, which §2 describes in prose ("1547 tests run; 1541 pass; exactly 6
# skip") and never spells out. The *subject* — which six — is `DECLARED_SKIPS`,
# tied to §2's frame by `test_the_declared_skips_are_the_ones_section_2_names`.
DECLARED_RAN = 1547
DECLARED_SUMMARY = "OK (skipped=6)"

# §2:35-45 enumerates these six; §2:64-65 puts Numpy's two back in scope. The
# literal is here rather than parsed out of §2 because §2 composes the names —
# a class plus parenthesised methods, and a bare `test_using` belonging to two
# classes named later in the sentence — and a reader that reassembles them
# wrongly answers with a *wrong set* rather than an error.
# `tests/test_suite_outcome.py` holds the tie to the frame and every refusal arm.
DECLARED_SKIPS = frozenset(
    {
        "template_tests.test_autoreloader.Jinja2TemplateReloadTests.test_get_template_directories",
        "template_tests.test_autoreloader.Jinja2TemplateReloadTests.test_reset_all_loaders",
        "template_tests.test_autoreloader.Jinja2TemplateReloadTests.test_watch_for_template_changes",
        "template_tests.test_loaders.FileSystemLoaderTests.test_case_sensitivity",
        "template_tests.test_response.SimpleTemplateResponseTest.test_using",
        "template_tests.test_response.TemplateResponseTest.test_using",
    }
)

# The `Ran` line cannot be pinned whole the way the summary is: `runtests.py`
# prints its elapsed time, which is the instrument's fact and not ours (3.603s
# and 3.611s across two measured runs). The count and the prefix are ours.
RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.M)
SUMMARY_RE = re.compile(r"^(OK|FAILED)(?: \(([^)]*)\))?[ \t]*$", re.M)
COUNT_RE = re.compile(r"(?P<key>[a-z][a-z ]*?)=(?P<value>\d+)")
# A test's first line at verbosity 2. The `... skipped` that belongs to it is on
# the same line when the test has no docstring and on the next one when it has
# — measured on the real transcript, where the six declared skips are
# undocumented and Numpy's two are not. So the skip scan looks back for the
# nearest label rather than requiring both on one line.
LABEL_RE = re.compile(r"^(?P<label>\S.*?) \((?P<dotted>[\w.]+)\)", re.M)
SKIP_TOKEN = "... skipped "


class Report:
    def __init__(self, ran, summary, counts, skips, lines):
        self.ran = ran
        self.summary = summary  # the summary line verbatim, or None
        self.counts = counts  # parsed out of it, for this file's own check below
        self.skips = skips  # list of dotted names, in transcript order
        self.lines = lines

    def refusals(self) -> list[str]:
        """Every way this report fails §2, as sentences.

        Two *kinds* of condition live in here and they fail for different
        reasons, which is why the last one is worded the way it is. The ones
        above it are **subject** conditions — facts about §2 (1547 ran, those
        six skipped by identity). The `skipped=N` cross-check is an
        **instrument** condition: *does my reader see everything the runner
        reported*. A look-back that drops a skip line leaves a transcript that
        genuinely reported seven but whose parsed `skips` holds six, and every
        subject condition above stays green on it. It is the same split that
        settled M14's literal, and the reason the two kinds share one list is
        that both make the run unusable — but a later reader removing the check
        as "redundant with the identity loop" would be removing the only thing
        that watches the reader.
        """
        out = []
        if self.summary is None:
            return [
                "the transcript has no `OK`/`FAILED` summary line — the run did "
                "not finish, and an unfinished run is not a pass"
            ]
        if self.summary != DECLARED_SUMMARY:
            out.append(
                f"the run ended `{self.summary}` and §2:31-33 pins "
                f"`{DECLARED_SUMMARY}`"
            )
        if self.ran is None:
            out.append("the transcript has no `Ran N tests` line")
        elif self.ran != DECLARED_RAN:
            out.append(f"Ran {self.ran} tests, and §2:31 declares {DECLARED_RAN}")
        seen = set()
        for dotted in self.skips:
            if dotted in seen:
                out.append(f"{dotted} is reported twice — the transcript is not one run")
            seen.add(dotted)
        found = set(self.skips)
        for dotted in sorted(found - DECLARED_SKIPS):
            out.append(f"{dotted} skipped and §2:35-45 does not declare it")
        for dotted in sorted(DECLARED_SKIPS - found):
            out.append(f"§2:35-45 declares {dotted} and the run did not skip it")
        # The one place a *parsed* number is still read, and it is a claim about
        # this file rather than about §2: the summary saying `skipped=6` and the
        # scan finding six skips are the same fact only if the scan attributed
        # every one. A skip line whose label the look-back misses is dropped
        # silently, which would leave the identity checks above green on a
        # transcript that reported seven.
        declared = self.counts.get("skipped")
        if declared is not None and declared != len(self.skips):
            out.append(
                f"the summary says skipped={declared} and {len(self.skips)} skip "
                f"line(s) were attributed to a test"
            )
        return out


def _last(pattern: "re.Pattern[str]", text: str) -> "re.Match[str] | None":
    """The **last** match, which is not the last line.

    Two measurements say so. At verbosity 2 a test's docstring is printed at
    column 0 too, so a docstring whose first line reads `OK` must not be
    mistaken for the run's verdict; and the real transcript prints eleven
    `Destroying test database for alias …` lines *after* its summary, so a
    reader that takes `lines[-1]` refuses every healthy run.
    """
    matches = list(pattern.finditer(text))
    return matches[-1] if matches else None


def outcome(transcript: str) -> Report:
    """Parse one acceptance transcript. Pure, so a test can drive a fabricated one."""
    ran_match = _last(RAN_RE, transcript)
    summary_match = _last(SUMMARY_RE, transcript)
    counts: dict[str, int] = {}
    if summary_match and summary_match.group(2):
        counts = {
            m.group("key").strip(): int(m.group("value"))
            for m in COUNT_RE.finditer(summary_match.group(2))
        }
    # Each skip belongs to the nearest label at or before it: undocumented tests
    # print `name (dotted) ... skipped 'reason'` on one line, documented ones
    # print the label and then the docstring's first line carrying the
    # `... skipped`. Measured on the real transcript; pinned by
    # `test_a_skip_on_a_docstringed_test_is_seen`.
    labels = list(LABEL_RE.finditer(transcript))
    skips: list[str] = []
    cursor = 0
    while (at := transcript.find(SKIP_TOKEN, cursor)) != -1:
        cursor = at + 1
        owner = None
        for label in labels:
            if label.end() > at:
                break
            owner = label
        if owner is not None:
            skips.append(owner.group("dotted"))
    return Report(
        ran=int(ran_match.group(1)) if ran_match else None,
        summary=summary_match.group(0).strip() if summary_match else None,
        counts=counts,
        skips=skips,
        lines=transcript.splitlines(),
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: assert_suite_outcome.py <transcript>\n")
        return 2
    report = outcome(Path(argv[1]).read_text(encoding="utf-8", errors="replace"))
    refusals = report.refusals()
    if refusals:
        for line in report.lines:
            print(line)
        sys.stderr.write("\n" + "\n".join(refusals) + "\n")
        sys.stderr.write(
            "refusing: §2's acceptance criterion is 1547 run, zero failures and "
            "errors, and exactly the six skips it enumerates — by identity.\n"
        )
        return 1
    print(
        f"{report.summary} after {report.ran} tests, and the six declared skips "
        f"by identity (django_suite/run.py template_tests)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
