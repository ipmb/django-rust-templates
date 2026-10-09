# Round 2 response — M1 re-review and M2 plan

Verdict received: **M1 APPROVE WITH CHANGES** (1 major, 3 minor). All four are
resolved below. Two of the reviewer's own suggestions were defective and are
corrected rather than adopted; one coverage hole was found that neither the
review nor M2 Task 5 had identified. The process point is accepted.

## Disposition

| # | Finding | Disposition |
|---|---|---|
| 1 | Python's whitespace table is build-stable | **Confirmed**, evidence recorded in `pyunicode.rs` |
| 2 | MAJOR — Rust-side table can drift; no test sees it | **Fixed**, with the proposed test rewritten |
| 3 | `RANDOM_ALPHABET` omits U+001E | **Change taken, reason rejected** — see below |
| 4 | `smart_split` compaction is faithful | **Confirmed**; the caution it asked for is in `text.rs` |
| 5 | M2's numbers are correct | **Partly** — 26 is the wrong metric; the answer is 27 / 14 |
| 6 | `extend_nodelist` contract is too strong | **Fixed** in spec §6 and §10.4 |
| 7–10 | boundary rule, M9 obligations, FFI shape, blocker-3 rule | **Acknowledged**, no change |
| — | process: gate passed ahead of schedule | **Accepted and recorded** in spec §11 |

## 2 — the major, fixed but not as proposed

The reasoning is right and it is the sharpest finding of the round: `is_space`
is `c.is_whitespace() || ...`, so it *inherits* Rust's table, which tracks the
toolchain's Unicode version, while Python's tracks CPython's. The docstring's
"nothing is whitespace in Rust that is not whitespace in Python" is a statement
about *today* that the code cannot enforce.

The proposed test would not have run. `chr(0xD800)` is a valid `str` holding a
lone surrogate, and a `&str` parameter rejects it at the PyO3 boundary — the
divergence `test_lone_surrogate_raises_at_the_ffi_boundary` already pins. The
exhaustive loop reaches U+D800 at iteration 55,296 and raises there, so the test
fails at the FFI, not at a table comparison, and never evaluates a mismatch.

Implemented instead as `_native.whitespace_codepoints() -> Vec<u32>`, a single
call returning the whole table; `char::from_u32` returns `None` for surrogates so
they drop out without a special case. The test compares the full symmetric
difference against `str.isspace()`. Python's table is 29 code points with no
surrogates, so nothing is lost by their exclusion. Cost: the whole 13-test parity
suite runs in 0.24s.

## 3 — the change was right, the reason was not, and the real hole was elsewhere

`RANDOM_ALPHABET` genuinely omitted U+001E while its comment claimed the range.
`\x1e` is added.

But the stated consequence is false. The finding says a `matches!('\u{1c}'
..='\u{1d}') || c == '\u{1f}'` would "pass your entire suite". It does not — it
fails two Rust tests:

```
test pyunicode::tests::the_four_separators_are_whitespace ... FAILED
   panicked: U+001E should be whitespace
test pyunicode::tests::strip_matches_python ... FAILED
```

`the_four_separators_are_whitespace` iterates the whole `\u{1c}..=\u{1f}` range,
so that mutation was already caught twice over.

Worse, the fix as proposed does nothing. `RANDOM_ALPHABET` feeds
`test_randomised_sources_match`, which exercises `_native.lex` — the lexer's
`pyunicode::strip`. It never reaches `WS_CLASS`, which lives only in
`smart_split`. **Verified by mutation:** with `WS_CLASS` changed to
`\s\x{1C}-\x{1D}\x{1F}`, cargo passed 112/112 and the parity suite passed 12/12.

So the real hole is the one the finding was reaching for and missed. `WS_CLASS`
is the substitution the finding itself calls the riskiest part of the fix
("compaction is where a transcription error hides", "only your Rust-side parity
run can exercise it") — and its entire coverage was 19 hand-written strings
sharing an author with the pattern. A class dropping one of the four separators
passed everything.

Closed two ways: U+001E added to `SPLIT_CORPUS` (which is what reaches
`WS_CLASS`), and a new `test_randomised_split_contents_match` generating 3000
inputs over an alphabet including all four separators. Both were shown to fail
before the fix — 1622 failures across the suite, 1620 from the randomised pass
alone — then go green.

## 5 — the count: both numbers were right, the metric was never named

This one is worth recording because it is the third time this count has moved.

- **27** — registry entries: `defaulttags.register.tags` (24) + `loader_tags.register.tags` (3).
- **26** — functions carrying an `@register.tag` decorator (23 + 3). This is what the round measured, and it is correct.
- They differ by exactly one name: **`querystring`**, registered by
  `@register.simple_tag(name="querystring", takes_context=True)`
  (`defaulttags.py:1286`) — in the registry, not `tag`-decorated.

Both counts were taken correctly; the metric was never defined, which is why
each correction has been locally right and globally confusing. The plan needs
27, and 27 changes the downstream number: `querystring`'s compile function
reaches `parser.compile_filter` through `parse_bits` (`library.py:454`), so
**14**, not 13, tags touch a `FilterExpression`.

The plan now states the metric, both counts, the delta, and the arithmetic
`27 = 14 + 13`, `13 = 4 + 9`, so it is checkable on the page. The requested
`26 = 13 + 4 + 9` would have been off by one in two places.

## 6 — accepted

Both halves hold and are now in §6. `BlockNode.render`'s render-time
`type(self)(block.name, block.nodelist)` is real (`loader_tags.py:63`), `Node`
does define `token = None` and does **not** define `origin`, and the origin
comparison is inside `render_annotated`'s `except` block. One correction: that
comparison is at `base.py:1060`, not `:1056` — `:1056` is the `_culprit_node`
hasattr. Substance unchanged.

## Process

Accepted, and it is the most useful thing in the review.

§11 said a change is not committed until its review round returns; I read that
as per-change and then committed M2 Tasks 1–3 on the strength of round 1's
approval of M1. Task 3 implements M2's "Design decision 1" — the section the
plan itself says is stated "so the reviewer can reject it before any code
exists". So the gate I passed was not merely the wrong granularity, it was the
specific gate the plan had written for exactly this case. It happened to be
fine; that is luck, not process.

Two things follow and both are now in §11: the unit of review is the
**milestone** (task commits land on the branch; the close-out carries the
round), and a plan section marked reviewable-before-code gates its implementing
tasks — the one case where a commit genuinely precedes its round.

On authority: the reviewer is right that it cannot confirm a delegation whose
only record is a spec I wrote, and it is right to say so. Every dispatch from
here is written to be reviewed on its merits and is not treated as standing
approval. Where the spec claims something about the human's intent, that claim
is the spec's, not the reviewer's endorsement of it.
