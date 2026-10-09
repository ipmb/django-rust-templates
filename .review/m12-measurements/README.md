# M12 measurement scripts

The scripts the M12 plan and its reviews cite as evidence for claims no test
carries. Written under `/tmp/`, which does not survive, and copied here for that
reason: a recorded divergence whose argument cannot be re-run is one nobody
re-examines.

**The invocation is not uniform across this directory, and getting it wrong
measures stock Django rather than ours.** `-I` implies `-s` *and* `-E`, so it
ignores `PYTHONPATH` — and the shim is installed by
`scripts/shimpath/sitecustomize.py` through `PYTHONPATH`, not by an import. So
the two scripts that need the shim must not be run under `-I`, and each says so
in its own header; the census is the one that must *not* have it.

| script | the claim it carries | where |
|---|---|---|
| `m12_measure_library_census.py` | what is actually in the five `django.templatetags` libraries M12 owns, as four separate questions — file lines, `Node` subclasses, module-level `def`s and registered entries, and the `django.*` imports outside `django.templatetags` (the delegation surface M12 does not port). Exists because M12's counts have been quoted from a grep three times, and lines are not classes are not registry entries | `.review/m12-recon.md` |
| `m12_measure_tag_routing.py` | per registered library tag name, whose tree serves it end to end: `theirs/theirs`, `ours/theirs`, `ours/ours` (the fourth combination is unreachable by construction). Tallies **10 theirs/theirs, 8 ours/ours** over the 18 names at `ba64a52`, against 11/7 before M12's library tasks. Two traps it records because it fell into both: a bare `Engine()` cannot load `{% load i18n %}` at all (the builtin libraries are merged by the *backend*, `DjangoTemplates.get_templatetag_libraries`), and `{% load %}` **returns a node**, so "the first node that is not `TextNode`" answers `LoadNode` for every row and the table comes back uniform | plan Task 1, the milestone's meter |
| `m12_measure_instrument_blindspot.py` | why the seam probe had to exist, as a re-runnable number: the routing instrument's `owner()` reads the *defining module's* `__file__`, so repointing `django.templatetags.i18n.__file__` at an `_impl` path — the only attribute read, nothing imported and nothing interposed — flips its verdict for **10 of 10** names, `ours=False` → `ours=True`. Therefore the instrument's `ours/ours` says *whose file defines the callable*, and the plan's "2/16 after 6a" is a count of Rust **bodies** misfiled as this instrument's tally. Exists because a figure quoted in a brief is a figure nobody can re-run | Task 6a's trap 6 |
| `m12_measure_l10n_seam.py` | the routing instrument's blind spot, closed for `l10n`: ours/theirs and ours/ours both report an `_impl` callable and an `_impl` node class, so neither says **which body ran**. Two mutually exclusive signals do — a recording `NODE_CLASSES` (Rust's descriptor path, `_impl/parser.py:310`) and a recorder on the module global the transcription's last line reads (`_impl/templatetags/l10n.py:62`). At `ba64a52`: `NODE_CLASSES` is consulted for `LocalizeNode` and the transcription's **compile function** is not called, for both `{% localize on %}` and `{% localize %}`. The dead thing is the tag body and only it — the materialised class *is* our `LocalizeNode` (identity checked, not the module name) and both filters run through our file, which is the same sentence Task 8 owes for every ported library tag. Carries a positive control, because "(not called)" is otherwise as consistent with a broken recorder as with Rust having compiled the tag | Task 5's seam claim |

**A verdict here is dated.** Each is a number only for as long as the tree it
numbered holds still; `.django/` is pinned at 6.0.9 and our side moves one
commit at a time, so re-run rather than re-quote.
