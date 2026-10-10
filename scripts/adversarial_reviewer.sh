#!/usr/bin/env bash
#
# Start the **adversarial** code reviewer — a codex agent in a herdr pane — with
# a system prompt that assumes the code is wrong.
#
# Why a script.  Three things about this invocation are not guessable from the
# other side: the system prompt is delivered by `codex -c
# model_instructions_file=<path>` and not by an argument; the sandbox has to be
# named (`-c sandbox_mode="read-only"`) because "never edit files" is a
# instruction the agent cannot be trusted to obey on its own; and `herdr agent
# start` must not be run before the pane exists.  It is also the same
# configuration each time, and a prompt typed twice is a prompt that drifts.
#
# The reviewer is a *reader*, and it is deliberately not the `rusty-reviewer`
# peer session: it has no transcript of ours, so it cannot inherit an assumption
# we have not written down.  Dispatch it with the commit and the artifact sha,
# and expect it to name the input that reproduces a finding — the first run
# returned `VERDICT: FAIL` on the sealed-loop frame rewrite, with a template that
# renders `XXXX` under Django and `XXXold` under ours.
#
# Usage:  scripts/adversarial_reviewer.sh [agent-name] [pane-name]
#         (defaults: adversarial-reviewer, the current herdr pane)
#
# Requires HERDR_ENV=1 and a herdr workspace; see `herdr --help`.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMPT="$PROJECT_ROOT/scripts/adversarial_reviewer_prompt.md"
NAME="${1:-adversarial-reviewer}"
PANE="${2:-}"

[[ -f "$PROMPT" ]] || { echo "missing $PROMPT" >&2; exit 1; }
: "${HERDR_ENV:=1}"
export HERDR_ENV

if [[ -z "$PANE" ]]; then
  PANE="$(herdr pane split --current --direction right --cwd "$PROJECT_ROOT" --no-focus)"
fi

# The prompt goes in as a file, not as an argument: `-c` overrides force codex's
# embedded mode, and the embedded mode is the one that reads
# `model_instructions_file`.  A prompt passed as a positional argument would be a
# *message*, which is a different thing and changes each run.
herdr agent start "$NAME" --kind codex --pane "$PANE" -- \
  -c "model_instructions_file=\"$PROMPT\"" \
  -c 'sandbox_mode="read-only"' \
  -c 'approval_policy="never"'

echo "started $NAME in pane $PANE"
echo "dispatch:  herdr agent prompt $NAME \"<the commit and the artifact sha>\" --wait --timeout 900000"
echo "read:      herdr agent read $NAME --source recent-unwrapped --lines 900"
