# M1 review dispatch — Rust lexer

Round 2. Unlike round 1, **this round has executable code**, so the most useful
thing you can do is run it rather than read it. Everything below is reproducible
from the repo root.

## What M1 claims

The Rust lexer reproduces Django's token stream exactly — token type, contents,
`lineno`, and debug character positions — and `Token.split_contents` /
`smart_split` reproduce theirs. Both claims are established by comparing against
Django's *real* `Lexer` and `DebugLexer` imported from `.django/`, with no shim
in the path.

M1 deliberately changes nothing observable: the Django suite still runs on stock
Django and must still report `Ran 1547 tests … OK (skipped=6)`. That is by
design (§10.1: engine progress and integration progress move independently), so
"the suite is unchanged" is a *pass* here, not evidence of no work.

## Commits

| Commit | Task |
|---|---|
| `36d2e74` | Task 1 — `Token`, `TokenType` |
| `55c4e97` | Task 2 — segment scanner matching Django's `tag_re` |
| `7a57005` | Task 3 — Lexer, verbatim handling, debug positions |
| `f1d2aa4` | Task 4 — `Token::split_contents`, `smart_split` |
| `dba939a` | Task 5 — `_native.lex` binding |
| `44c9c9d` | Task 6 — parity test against Django's lexer |

Plan/spec amendments from round 1: `a2bbe64`, `4bc2528`, `7a297be`, `e356e85`,
`ea865ca`.

Task 7 (this close-out) is committed with the spec table update.

## How to reproduce

```bash
source scripts/env.sh          # cargo is not on PATH otherwise
scripts/check.sh               # full: cargo, fmt, clippy, python, differential, Django suite
scripts/check.sh --fast        # everything except the Django suite

# the acceptance test alone, with per-test output
.venv/bin/python -m unittest tests.test_lexer_parity -v
```

`maturin develop` rebuilds the extension at
`python/django_rust_templates/_native.cpython-314-darwin.so`; it must be re-run
after any Rust change or the tests silently exercise a stale `.so`.

## Evidence

| Check | Result |
|---|---|
| `cargo test -p template-core` | 38 passed, 0 failed |
| `cargo clippy --workspace --all-targets -- -D warnings` | exit 0 |
| `cargo fmt --all -- --check` | clean |
| `tests/test_lexer_parity.py` | 11 tests pass |
| Corpus parity | 51 sources × {`Lexer`, `DebugLexer`} = 102 comparisons, 0 mismatches |
| Randomised parity | 3000 seeded sources × both lexers = 6000 comparisons, 0 mismatches |
| Heavy sweep (one-off, not committed) | 6000 sources over a wider alphabet, lengths to 120 × both lexers = 12000 comparisons, 0 mismatches |
| `scripts/check.sh` (full) | `Ran 1547 tests … OK (skipped=6)` |
| Progress meter | 25 modules still Django's — unchanged from M0 |

### The guards were made to fail, not just read

Per round 1's point that a guard which has never failed is not known to work, I
broke each one and confirmed the failure:

1. **Byte/char conversion** (`segments.rs::CharIndex::chars_before` mutated to
   return the byte offset) → **103 parity failures**, each printing the source,
   Django's stream and ours. Notably the *non-debug* comparison still passed —
   positions are `None` there — which confirms Review Focus item 3 is carried
   entirely by the debug comparison. Reverted; green again.
2. **The self-comparison guard** (`test_comparison_target_is_genuinely_djangos_lexer`,
   with the checkout path changed) → fails, naming the resolved source file. This
   is the trap you flagged as four milestones away; it fires as intended.
3. **Lone surrogate** → confirmed `UnicodeEncodeError`, a `ValueError` subclass,
   so the recorded FFI difference is real rather than an artifact of a broad
   `except`.

`test_randomised_sources_match` is the mechanism for Review Focus item 6 (corpus
and implementation share an author). It is seeded (`20261008`) so any failure
reproduces exactly.

## Round-1 findings relevant to M1

- **Finding 8** (`split_contents` absent from M1's plan) — resolved. Task 4
  implements it; Task 6 pins it, including the `_("`-rejoining and the
  `StopIteration` path Django reaches via `next(bits)` on an exhausted generator.
- **Answer (b)** (does the M1 plan establish its done-when?) — resolved by
  Task 6. The plan's original "compare token streams" was a throwaway validation
  outside the repo; it is now a committed test that runs in `check.sh`.
- **Answer (c)** (is the `debug`-flag lexer a fidelity risk at M9?) — the risk is
  now measured rather than reasoned about: the debug comparison is the one that
  catches the mutation above.
- **Answers (a), (d)** — applied to spec §5.2/§5.5/§6 and the plan's Review Focus.

## Plan defects found while executing

Worth your attention as a pattern, not just as three fixes:

1. **Task 3** — the plan's own Rust failed clippy twice
   (`clippy::module_inception` on `lexer/lexer.rs`, `clippy::type_complexity` on
   a test helper's return type). Root cause: the pre-execution validation ran
   `cargo test`, which does not run clippy, while `check.sh` gates on it under
   `-D warnings`. Fixed structurally and recorded as a Global Constraint.
2. **Task 5** — the same `type_complexity` lint in the `lex` binding. Sharper
   root cause: that validation covered `template-core` only, so **no
   `template-py` snippet had ever met the clippy gate.** Both the snippet and the
   validation claim are corrected in the plan; the generalisation recorded is
   that a plan's snippets are only as checked as the crate they were extracted
   from.
3. **Task 7 Step 2** — the progress-meter command as written cannot run: it omits
   `_shim.install()` (so `unshimmed()` raises `ShimError`) and never puts
   `.django` on `sys.path`. Corrected and run verbatim.

I also found, at close-out, that **`Lexer.verbatim` is a public attribute, not
private state**: `test_parser.py:49` asserts `verbatim=False` through
`Lexer.__repr__` on a freshly constructed lexer, and the repr truncates
`template_string` to 20 characters. The Rust models the same state as
`Option<&str>`, so the shell must map `None` → `False` at M9. Recorded in §10.4.

## What I want attacked

In rough priority order:

1. **The parity test's independence.** It compares against Django's real lexer,
   but it was written by the same author as the implementation. The randomised
   pass is my answer; if you think the alphabet (`"{}%# \naé🎉"`, max length 40)
   is too narrow to reach a real class of divergence, say which class and I will
   widen it. To pre-empt the obvious criticism I ran a heavier one-off sweep —
   seed `987654321`, alphabet widened to quotes, backslashes, pipes, CR, tab,
   `_(`/`)`, combining and astral characters, with the *literal strings*
   `verbatim`, `endverbatim`, `if` in the alphabet so accidental verbatim
   interactions are common, and lengths to 120 — 12000 comparisons, 0
   mismatches. That is not committed; say the word and I will fold the stronger
   parameters into the committed test.
2. **`smart_split`'s regex.** I compacted Django's `re.VERBOSE` pattern into a
   single line rather than reproducing it as written. Behaviour is pinned by the
   split corpus and by 4000 randomised comparisons during development, but
   compaction is where a transcription error hides. Please diff it against
   `django/utils/text.py`.
3. **The `Token`/`TokenType` FFI shape.** `_native.lex` returns
   `list[tuple[int, str, int, tuple[int, int] | None]]`. The int is deliberate —
   `TokenType` is a plain `Enum`, not an `IntEnum`, so `TokenType.TEXT == 0` is
   `False` and the shell must compare `.value`. If you think the boundary should
   carry the enum object instead, this is the moment to say so.
4. **Nothing asserts `lexer.verbatim()` is left clean** across the corpus — the
   accessor exists for that purpose and only one unit test uses it. Is that a gap
   worth a test, or is the field's final state unobservable enough not to matter?
5. **Performance is unmeasured.** No inputs longer than 40 characters, no timing.
   I do not think M1 needs it, but if you think a pathological input (deeply
   nested delimiters, very long text runs) should be pinned now, name it.

## What I did not verify

- I did not run the suite under `python -O`, so the `assert_eq!` (release-live)
  vs `debug_assert!` distinction in Review Focus item 9 is reasoned, not tested.
- I did not exercise `django.views.debug` / `django.forms.renderers`, which you
  noted will receive the shim; nothing in M1 touches them.
- The `_("` rejoining is pinned against Django only on the 13-entry split corpus,
  not a randomised pass.

## Addendum — a fix landed while this review is in flight

One commit arrived after this dispatch was written: **`1a013ae`, "M1: match
Python's whitespace table, not Rust's"**. It changes M1 code you are reviewing,
so it is called out rather than slipped in. Everything above still stands; the
fix does not touch segmentation, debug positions, or `split_contents`' control
flow.

**The defect.** Django trims tag contents with `token_string[2:-2].strip()` and
splits them with `smart_split`, whose pattern is written with `\s`/`\S`. All
three follow Python's `str.isspace()`, which is true for U+001C–U+001F as well
as for Unicode `White_Space`. Rust's `str::trim` and the `regex` crate's `\s`
follow `White_Space` only. The tables differ by exactly those four code points —
Python 29, Rust 25, checked exhaustively over U+0000..=U+10FFFF in both
directions, with nothing whitespace in Rust that is not whitespace in Python.

So `{% <U+001C> if x %}` had contents `if x` in Django and `<U+001C> if x` here.
Dispatch reads the first word of the contents, so the two engines disagreed
about which tag the template named — the divergence was in the mechanism the
differential tests exist to check, not off at the edges of it.

**Found** while writing the M2 plan, not by this review. **Fixed** rather than
pinned because carrying a known divergence through the seam every later
milestone crosses would weaken every subsequent parity claim.

**Evidence.** The separators were added to `CORPUS`, to `SPLIT_CORPUS`, and to
`RANDOM_ALPHABET` (so the randomised pass reaches them rather than only the
hand-written cases). Against the unfixed code that is 13 parity failures, each
printing the source and both streams; after the fix, green.
`scripts/check.sh` full: `Ran 1547 tests … OK (skipped=6)`.

**New module.** `crates/template-core/src/pyunicode.rs` — `is_space`, `strip`,
`split`, and `WS_CLASS` for the regex substitution — with its own unit tests
including the four-code-point difference asserted directly.

**Worth attacking, in addition to items 1–5:**

- (a) Is `c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}')` the complete
  Python table? I believe `str.isspace()` is exactly `White_Space` ∪ those four,
  but if Python's answer depends on the build (narrow/wide, ICU version) that
  assumption is where it would break.
- (b) `smart_split` now builds its pattern with `format!` instead of a literal,
  substituting a class for `\s` and its negation for `\S`. Is that substitution
  faithful everywhere, including inside the `[^…'"]` negations?
- (c) Does anything else in M1 depend on Rust's table that the corpora would not
  reach? I grepped for `trim`/`split_whitespace`/`\s` and found only the sites
  fixed, but a grep is not proof.
