# Round 1 — spec amendment + M1 plan

**Dispatched:** 2026-10-08
**Reviewer:** `rusty-reviewer` (`django-rust-templates-a8`)
**Artifacts under review:**
- `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md`
  (committed `7635600`; §§2, 9, 10 changed since the first dispatch)
- `docs/superpowers/plans/2026-10-08-m1-rust-lexer.md` (new)

**Status:** dispatched — findings pending.

## What changed since the first dispatch

§2, §9 and §10 of the spec were rewritten after the first round was requested.
The reviewer should read the committed version, not any earlier copy.

- **§2** — "zero skips" was wrong. The measured baseline is `Ran 1547 tests …
  OK (skipped=6)`. The criterion is now enumerated and falsifiable: 1547 run,
  1541 pass, exactly the 6 named structural skips, and any *new* skip is a
  regression.
- **§9** — the differential harness has two modes: shim-vs-stock, which is
  meaningful from M0, and rust-native-vs-stock, which measures the engine before
  it is interposed.
- **§10.1/§10.2** — the milestone unit changed from *test file* to *module*.
  Interposition is atomic, so `test_base.py` requires `base`, `context` and
  `engine` all to be ours before it can pass anything. The project therefore runs
  engine-first through `_native` entry points, and `base` interposes late as one
  unit.
- **§10.3** — milestone table rewritten M0–M15.

## Questions put to the reviewer

1. **Is §10.2's claim survivable?** It asserts `base.py` cannot be interposed
   piecewise and must land as one unit after the variable resolver. If that is
   wrong, the whole milestone sequence is wrong.
2. **Does the M1 plan actually establish M1's stated done-when?** The claim is
   "reproduces Django's token stream exactly, incl. positions, verbatim state,
   and non-DOTALL tag matching". Is the parity test sufficient, or does it leave
   a class of divergence unmeasured?
3. **The `DebugLexer` deviation.** The plan models `DebugLexer` as a `debug` flag
   on one `Lexer` rather than a distinct type. Django keeps them separate for
   speed. Is this a fidelity risk for the Python shell at M9, which must expose
   both as distinct classes?
4. **Review Focus adequacy.** Five failure modes are listed. Which are missing?

## Pre-execution validation performed by the implementing session

Offered as evidence, not as a substitute for review:

- The plan's Rust was extracted into a throwaway crate at `/tmp/m1plan/` (outside
  the repo) and `cargo test` passed **26/26**, doctests clean.
- Its token stream was diffed against Django's real `Lexer`/`DebugLexer` over all
  **47 corpus entries in both modes — byte-identical**.
- That exercise found a real plan bug: an indented Python snippet in a rustdoc
  comment is compiled as a doctest, so `cargo test` failed on a file whose unit
  tests passed. Fixed, and pinned as a Global Constraint.
