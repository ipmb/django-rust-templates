# M6 — `Library`, the extension host, `{% load %}`, `querystring`

**Review target: `.django/` at Django 6.0.9.** An unpinned Django also exists on
this machine, so an unqualified "Django" is ambiguous; every number below is
against the pinned checkout, and every one of them was re-derived rather than
copied from the plan that predicted it.

This is the milestone round (§11), so all **141 commits** since the M5 close-out
(`c4fea51..HEAD`) are in scope — including the ones whose subject is a plan
correction, and including the 80-odd whose subject is a citation. Those are not
housekeeping. They are the milestone's second subject, and the reason it has a
second subject is this milestone's own finding; see "The citations are claims".

## What M6 claims

1. **The extension host works end to end.** A Python-defined tag or filter
   reaches the engine through `Library`, compiles through a handle that *is*
   Django's own `Parser`, and materialises as a node the resolver walks. The
   evidence is `test_custom.py`'s `filter`/`simple_tag`/`simple_block_tag`/
   `{% load %}`-error and `bad_tag`/`tag_27584`/`extra_data` groups — named, not
   asserted as a module.
2. **Re-entrancy.** `ParserHandle.parse` is called from inside a tag being
   compiled, and the Rust side survives it. Task 4 Step 6 is the row that proves
   it, and its discriminator was *executed* before it was written into the plan.
3. **`load` and `querystring` are host-owned, so Rust implements neither.**
   Measured now: `builtin_tag_names()` is **22** and `builtin_tag_stubs()` is
   `['partial', 'partialdef']`. `load` is a Python module import;
   `querystring` is the one built-in registered `takes_context=True` and the sole
   non-`do_` registration among them. `partial`/`partialdef` are names the
   registry knows with no compile function behind them.
4. **`block`/`extends`/`include` are excluded from the host.** They are Django's
   builtins but not Rust's, and `NodeDesc.must_be_first` does not cross the seam
   (`desc.rs:407`, defaulted `false` at `:445`). A host-compiled `{% extends %}`
   would be **accepted and uncheckable** — its must-be-first rule silently
   absent — which is worse than an honest refusal.
5. **`{% extends %}` still raises `Invalid block tag` at M6. That is a
   divergence from Django, not coverage of it.** It is written down as a
   divergence in the spec's M6 row rather than left to look like a gap in
   coverage, and M7 owes both the removal and the `bool` across the seam.
6. **`unshimmed()` is unchanged at 25** — no module moved out of the shim in this
   milestone, which is the correct result for a milestone that adds a seam rather
   than replacing a module.
7. **The verification, all measured this session:** `cargo test` **645**;
   `tests/parser_corpus.py` **152** sources and `tests/parser_error_corpus.py`
   **91** errors; the differential harness **48 cases, identical (stock vs rust
   engine)**; `scripts/check.sh` green with the suite at `Ran 1547 tests … OK
   (skipped=6)`.

### What M6 does not claim

`InclusionTagTests` — `InclusionNode.render` loads a template *by name*, which is
loader machinery and is M7's. `test_library.py`'s 28 tests are a gate on Python
behaviour M6 must not break, not work M6 did. And §5.2's "arbitrary attributes
must work" is **M7's** requirement: it is `{% block %}`'s compile function, and
the host does not compile `block` until M7. One half of it travels with the
requirement and is non-negotiable — the handle must *raise* `AttributeError` for
an attribute it does not have, because `do_block`'s `except` branch
(`loader_tags.py:233-234`) is the only place `parser.__loaded_blocks` is created.
A handle that answered every name would move that list's lifetime out of the
parse and into the bag, and Django's duplicate-block-name check would then be
asserting something about the bag rather than about the template.

## The citations are claims

M6's plan inherited a sweep: 545 `` `file.py:N` `` citations in our prose, each a
claim about the pinned checkout, and nothing in the tree comparing any of them to
anything. The sweep found wrong numbers in **every batch it entered** — 28 rows
read and 12 wrong in one, 21 `.rs` citations and 11 wrong in another — and the
two files that came through clean (`parser/error.rs`, `parser/tags/with.rs`, five
shorthands each, all correct) matter as much as the totals, because they are what
says the tree is not uniformly bad and the remaining passes need the detectors
only where the detectors earn their keep.

The terminal state, measured by the gate:

| quantity | value |
|---|---|
| anchor occurrences checked | **955** |
| manifest rows cited / total | **545 / 545** |
| failures | **0** |
| `unmigrated` (a backticked bare `:N`) | **0** |
| `dead` (a row the tree no longer cites) | **0** |
| `unquoted` rows (no quoted source text) | **104**, all cited only from files that never held a shorthand |
| `name-only` rows (a bare identifier where a line was meant) | **29** |

`--check --frozen` is now the gate (`d416cb4`). It fails on a shorthand and on a
dead row, neither of which can arrive from anywhere but an edit being written,
because `unmigrated` is zero. The 104 unquoted rows are deliberately **not**
failed on: an unquoted row records no claim to falsify, and every one of them is
cited only from files that never held a shorthand, so no naming pass will come
back for them.

### The census, re-derived over two trees

`crates/` became a checkable base in `6754684`, which is the first time our own
Rust is both cited and cited-from in these counts. Measured on constructs:
**545 spelled anchors name 539 constructs; 955 occurrences; 824 (anchor, citing
file) relations; 169 constructs are cited from two or more files, 60 from three
or more.** The earlier single-tree figures (524 / 917 / 795 / 163 / 57) were true
about the tree that produced them; the occurrence delta of 38 is the widened
scan's own new occurrence lines, which makes the two measurements a cross-check
rather than a coincidence.

The census is **invariant under naming** (it canonicalises a shorthand and a full
path to one key, so a figure taken before a pass still holds after it) and it is a
**prior, not a detector**: it says where a wrong number is expensive, not where
one is. `.review/m6-verification-order.md` is the computed form of that prior —
169 constructs ordered by citing-file count, with the line each holds. Its head
was verified against the pinned tree on 2026-10-09: `base.py:816` (14 files),
`:813` (7), `:669-670` (6), `:773` (6), `defaultfilters.py:46` (6),
`defaulttags.py:929` (6), all six correct.

**Six constructs are spelled two ways**, so the manifest holds six more rows than
it holds constructs. Two of the six are a *single file* spelling one construct
two ways — `filesizeformat.rs` cites both `html.py:502-507` and
`django/utils/html.py:502-507`; `test_host_tags.py` cites both `tests.py:148-155`
and `.django/tests/template_tests/tests.py:148-155`. The other four are two
different files disagreeing about how to spell a construct they both cite. This
is recorded rather than fixed: a name-based key would need the resolver, which is
the thing the census avoids.

## The base is a precondition, not a label

The reframe that made the repair tool safe. An anchor's **base** — which tree its
number is a claim about — decides how a wrong number should be read, and the two
readings are opposite:

- a **Django** row whose claim is outside its cited span means *the pin moved or
  the citation was wrong*. That is an investigation.
- a **crates** row in the same condition means *our file changed under the
  citation*. That is routine.

So `--update` has no bare form that touches a number in either tree: plain
`--update` reports what each base would move and saves neither,
`--update=django` and `--update=crates` write. A command that applied the
routine repair to the first case would silently ratify a wrong claim about a
pinned checkout. The two labels are iterated structurally, so interleaving the
two bases' output is unreachable rather than merely not done.

`--update` moves **only** a claim outside its span. The unconditional version was
a mutation rather than a repair — an entry whose snippet sits inside its span but
not on its first line is green, and rewriting its number to `hits[0]` moved the
manifest off the tree's number, failing `--check` on the entry it had just
"repaired".

And it searches the **whole file**, deliberately. Of the drifts this sweep
measured, one moved *backward* (`tags/mod.rs` 182 → 180, lines deleted above the
target). The tempting optimisation — scan near the cited line, widen on a miss —
would be written as a forward scan by anyone who had seen only the others, and
would lose that one silently. The warning is beside the implementation.

## Three ways a number goes wrong, and only two are repairs a tool can make

1. **Drift** — right when written, moved since. `--update` derives the new number.
2. **Misreading** — the number never pointed at the claim. A re-reading finds it.
3. **Stale at birth** — the citing commit also edits the cited file, so the
   citation is stale with respect to the file its own commit leaves behind.

The third class was found by *falsifying my own account of an instance*. I had
written that `parser/mod.rs:392-418` "was never correct" — the reviewer measured
it and was right: at `52f1cb1^`, `pub fn extend_nodelist(` is at 392 and 418 is
`nodelist.nodes.push(node);`; `52f1cb1`, the same commit that wrote the citation
into `host.py`, also added 40 lines to `mod.rs` and moved the function to **429**,
leaving 392-418 as `invalid_block_tag`'s tail. My error was measuring the interval
against a *later* commit (`6242cbc`) instead of the citing one.

The distinction is not pedantic. Neither `--update` nor a re-reading reaches this
class; only a rule does, and the rule is `--check --frozen` running **on the
commit**, which reddens the build before such a citation lands.

`d81abb3`'s sentence "One was never correct." is therefore **superseded** and is
corrected forward here rather than by rewriting three landed commits: the class,
not the instance, is what belongs in the record.

## Four holes, one shape — and a fifth class the tool cannot see

Not one of the holes found in `scripts/check_python_anchors.py` during M6 was a
wrong answer about input the tool saw. Every one was input it silently did not:

1. `ANCHOR_RE` cannot match a decorator, so a decorated `def` is never seen.
2. Its path class is `[a-zA-Z_0-9/]`, which cannot match a hyphen, so every
   `template-core`/`template-py` citation is dropped — **and the hole does not
   merely drop its own citation, it hands any bare `:N` after it to a different
   file.** `filters/striptags.rs` cited `crates/template-py/src/filters.rs` as
   `:871`, `:248`, `:1644-1645`; that path is not an anchor, so the checker
   resolved the bare spans against the nearest `.py` anchor above it — `html.py`,
   forty lines up — and reported them as "names no file", which is true but not
   the interesting part. The interesting part is that they *look* resolvable.
   Both of its `.rs` numbers were wrong, which is how the hole stopped being
   theoretical: they had drifted to 854 and 1528, lines holding a `split(",")`
   loop and a doc comment, because **nothing was checking our own tree** —
   Django's is pinned and ours moves every commit. Repaired in `0803d4b`; the
   prose now writes the full path at each of its three numbers, which is the
   whole repair the metric can ask for — the numbers still had to be read.
3. `_check_entry` slices `source[start - 1 : end]`, and a slice past the end is
   silently *short*, so an anchor naming lines that do not exist counts as cited.
   `defaulttags.py:1654-1690` named three lines past the end of a 1687-line file
   and `--check` reported 0 failures.
4. A shorthand's citation-shaped prose that is not a citation — four occurrences
   of the template token `x|upper:1:2`, where no file can ever be named.

The discipline that follows is cheaper than finding a fifth by hand: **every
place the tool slices, truncates or pattern-matches should assert it consumed
what it was given.** A slice past the end should raise; a regex that matches
nothing where something anchor-shaped exists should report.

Four instances of one shape, and the fifth class is the one no tool can be taught:
`unmigrated` is a regex count, and a regex cannot tell a Django line citation from
a citation-shaped string. That is not a defect in the metric — "shorthand
anchor(s) naming no file" is a *true* description of all three kinds it
conflates — but it makes the count a triage queue rather than a to-do list, and
the read is what divides it.

## The detectors, each with its denominator

Every count in this milestone that went wrong went wrong the same way: a
denominator that silently excluded the population the reader extended it to. Five
instances were catalogued during the sweep, and a sixth arrived at the end from
the reviewer's side.

- **The unquoted end line.** 131 unquoted rows, 87 of them ranges; of those, ten
  ended on a line carrying no text. Nine were the one-line-past shape. The tenth
  ended on a *comment*, which is not a slip — it is a line somebody chose — so
  the repair was not "back up one" but "take the unit the sentence names".
- **The quoted end line, which the fourth detector could not ask about.**
  `_check_entry` accepts the snippet anywhere in the span, so a row with a
  snippet passes whatever its end line is. Asking all 276 range rows found six
  more, all quoted, all the same shape.
- **`name-only`.** 29 rows quote a bare identifier where a line of source was
  meant. Their spans contain it *by construction*, so the comparison cannot
  fail — the metric is real, the rows are not defects, and they will never
  redden anything.
- **Agreement is not evidence.** Two files citing one construct at the same wrong
  number are one reader's choice copied twice. Two instances: `base.py:774`
  (cited from `parser/host.rs:73` and `parser/filter_expr.rs:665`; 774 is
  `upto = match.end()`, the remainder check is 775) and `defaulttags.py:106-112`
  (three files; 106 is a comment, the unit is `CycleNode.render`, 104-114 — held
  back rather than named, because three files must be corrected together and
  correcting them one at a time would leave two asserting a span the first had
  already disowned). The count does **not** say agreement is a detector: the
  most-agreed anchors are agreed-upon and right.
- **Publish the denominator with the ratio.** "28 rows / 12 wrong" is a fact only
  when the 28 is the row count that entered the list and the 12 is how many of
  those were wrong.
- **And the sixth, from the reviewer.** "0 blank/EOF hits" was measured correctly
  and was true of the degenerate `.rs` classes it named — and was read, by both
  of us, as reassurance about the `.rs` citations as a whole, which then measured
  **11 of 21 wrong**. A detector that reports only its true positives has no way
  to show what it never looked at.

### The rule the sweep yields, and its instances

**Never write a bare `:N` next to a different file's anchor.** The convention — a
backticked `:N` is line N of the file named nearby — is load-bearing: it is what
lets a reader resolve a shorthand and what lets the metric count one. Four
instances in the tree (the two `striptags.rs` spans above, and
`tests/test_materialise.py`'s `:207` and `:48-59`, which were cited for
`tags/mod.rs`), and two in the *conversation about the sweep*: the reviewer once
reported `defaulttags.py:1060-1063` for an anchor that is `base.py:1060-1063`,
taking the filename from my message, which had named two files in one sentence;
and a `:410` written immediately after a `smartif.py` anchor inherited smartif as
its file, and `smartif.py` has no line 410. It is the sweep's own failure mode
occurring in the discussion of the sweep, one instance in each direction.

## What M6 hands to M7

- `block`/`extends`/`include` come out of the host exclusion, and
  `must_be_first` crosses the seam as a `bool`. A host-compiled `{% extends %}`
  is only safe once its ordering rule crosses with it.
- `parser.__loaded_blocks`, for free, once the handle *is* Django's `Parser` —
  but only if the handle raises `AttributeError` for what it does not have.
- `InclusionTagTests`, and `{% include %}`'s loader machinery with it.
- The held-back `defaulttags.py:106-112` row: three files, one correction.
- `.review/m6-verification-order.md` is valid as written, because the census is
  invariant under naming. Verify by citation count, not by file.

## Open, and deliberately not closed

- **The two-spelling pairs.** Six constructs, six extra rows. Recorded, not
  merged: merging needs the resolver, which is what the census exists to avoid.
- **The disagreement signature.** Two citations of one construct at different
  numbers is a signature, and its rate **cannot be measured against the
  manifest**: the manifest holds only rows that pass `--check`, and a row with a
  wrong number cannot be in it, while the signature is precisely two *wrong*
  numbers for one line. Its domain is citations in the tree *before* naming, and
  its rate can only be collected during a sweep. It stays a candidate, for a
  stronger reason than "nobody has measured it".
- **The dated plans.** `docs/superpowers/plans/` carries stale citations by rule.
  A plan is a record of what was believed on a date; rewriting its citations
  makes it a record of what is true now and loses that distinction. Excluded from
  the checker by name, with the reason in the code.
- **The one instruction this close-out did not execute.** Task 7 Step 1 lists
  three spec errors; the second, `LibraryParseError` → `InvalidTemplateLibrary`,
  had nothing to correct — the name appears nowhere in our docs, and the recon
  records it "to stop it arriving". The plan said "correct the spec's" for an
  error the spec never made, which is its own small instance of the class above:
  a count taken from the recon's list rather than from the file.
