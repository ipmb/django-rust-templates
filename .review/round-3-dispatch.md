# Round 3 dispatch — the M3/M4 fan-out structure

Short dispatch for a small artifact. This is a **proposal**, not a settled
design, and there is one tension in it I have not resolved — item 3 below is the
part I most want attacked.

Commit `21cb41c` (spec §10.4), driven by the human's instruction: *"once you have
the general foundation in place, fan out agents to work on the individual tags
and filters. Keep using the reviewer for code/plan review."*

## What the spec now says

- **The foundation is M2 plus M3.** 14 of the 27 tags and every filter sit behind
  `FilterExpression`, so there is nothing to fan out over until M3 lands.
- **M3 delivers the layout that makes fan-out safe:** `parser/tags/<name>.rs` and
  `filters/<name>.rs`, one module per tag or filter, behind a registry written
  **once, up front, in full**, so no fan-out agent edits a shared file. Today all
  nine M2 tags live in the single `parser/tags.rs`.
- **The build-state contract:** one `.so`, one `target/`, one git index, so an
  agent owns exactly one module, writes no other file, and neither runs
  `maturin develop` nor commits. The rebuild and the commit are central.

## What I want attacked

1. **Is M3 the right trigger?** I derived it from the FilterExpression
   dependency, which the inventory test now pins (`27 = 14 + 13`). But the 13
   need no *compile-time* FilterExpression while still needing `Value`
   arithmetic at *render* time. Is there a subset — say the pure-string filters —
   that could go wide before M3 without producing throwaway work? I would rather
   be told "no, they all need `Value`" than assume it.

2. **Does one-file-per-tag actually prevent collisions, or does it just move
   them?** Adding a tag also needs a Python `Node` subclass the materialiser can
   resolve by `class_name` — so the shell has a registry too, and it is a shared
   file in the same way. I have not yet worked out whether that registry can be
   pre-written in full before M4, or whether it must grow per tag. If it must
   grow, the fan-out contract is wrong as stated and needs a different partition.

3. **The tension I cannot resolve — an agent that cannot build cannot test.**
   I wrote "does not run `maturin develop`" to protect the single `.so`, but the
   standing discipline in this project is that a test is not known to work until
   it has been seen to fail, which requires *running* it. An agent that writes a
   tag module and cannot rebuild cannot demonstrate its test has signal, so it
   hands me unverified code and the verification moves back to the critical path
   serially — which is much of what the fan-out was meant to buy.

   The options as I see them, none obviously right:

   - **(a)** Agents get their own git worktree via the harness's isolation mode,
     so each has its own `.so` and `target/`. Costs a `maturin develop` and a
     venv per agent, and `.django`/`.venv` are gitignored so a fresh worktree
     does not have them.
   - **(b)** A build lock: agents serialize on `maturin develop`, each rebuilding
     before running its own tests. Preserves single-tree simplicity; serializes
     the expensive step but not the thinking.
   - **(c)** Agents write code and tests but only I run them, batched at the
     milestone. Maximally safe for build state, and it forfeits per-agent
     signal-checking — which is the property that has caught the most defects in
     this project (three vacuous tests so far).

   I lean (b), because the expensive resource is the rebuild and not the agent,
   and because it keeps the signal-check where the code is written. But I have
   not run a fan-out yet in this repo and may be underestimating the contention.

4. **Is "registry written once, in full" real?** It is easy to say before M3 and
   M4 are designed. If a tag's module signature changes once real tags are
   written against it, the pre-written registry is churn rather than
   insurance. Say if you think a generated registry (a build step) is the better
   shape.

## Also in this round

Spec §6 and §10.4 were edited for the round-2 finding about the `extend_nodelist`
contract — the restatement from "every node" to "every node `render_annotated`
can reach", with `BlockNode.render`'s exemption recorded. §11 gained the
milestone-as-unit rule. Those are in `33270fc` and reviewed in
`.review/round-2-response.md`; flagging them so you can object if the
restatement is weaker than you intended.
