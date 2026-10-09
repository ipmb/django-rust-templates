# M13 measurement scripts

The scripts `.review/m13-recon.md` cites as evidence for claims no test carries.
Same rule as the M7 and M12 directories: a recorded number whose argument cannot
be re-run is one nobody re-examines.

**The invocation is not uniform here either, and which one a script wants is a
property of its question, not a convention.** The shim installs through
`scripts/shimpath/sitecustomize.py`, which is reached by `PYTHONPATH`, and
`-I` implies `-s -E` so it closes that route. So:

| script | how to run it | because |
|---|---|---|
| `m13_measure_library_census.py` | `-I` | the question is what the **pinned file** says; under the shim `django.template.library` is ours |
| `m13_measure_parse_bits_reach.py` | plain `.venv/bin/python` | it re-execs itself with the shim's `PYTHONPATH`; the shim's own counters are the measurement |
| `m13_measure_stub_expiry.py` | plain `.venv/bin/python` | same re-exec, same reason |
| `m13_measure_partials_surface.py` | `-I` | spans and imports of the pinned tree; nothing of ours is asked |

| script | the claim it carries | where |
|---|---|---|
| `m13_measure_library_census.py` | what is actually in `django/template/library.py`, as **four separate questions** with four different answers: 492 lines, 6 top-level classes (of which 2 are not nodes — an exception and a registry), 2 module-level `def`s, and `Library`'s 8 methods. Exists because "6 classes" and "6 nodes" are different numbers and this project has quoted one for the other before. Line count is `len(source.splitlines())` and the newline count is printed beside it: the `count("\n") + 1` form over-reports by one on a trailing-newline file | `.review/m13-recon.md` §2 |
| `m13_measure_partials_surface.py` | the surface the partial pair already has on our side, and every `extra_data` / `PartialTemplate` touch in the tree, against the pinned spans: `PartialDefNode` `:413-420`, `PartialNode` `:423-435`, `partialdef_func` `:1205-1265`, `partial_func` `:1269-1283`, `PartialTemplate` `base.py:294-346`, `extra_data` at `base.py:200`/`:514` and `engine.py:194-201`. The answer it produced, which corrected a working assumption: **`NODE_CLASSES` carries no partial node class** — they arrive through the materialiser's *host-node* arm (`_impl/parser.py:289`), so "give Rust a compile function" does not imply "add a `NODE_CLASSES` row" | §4's constraint |
| `m13_measure_parse_bits_reach.py` | how far into the 1,547 `library.py`'s compile-time surface reaches, counted per call **and** per distinct test file: `parse_bits` 250/6, `Library.tag` 194/11, `.filter` 168/7, `.simple_tag` 36/7, `.inclusion_tag` 29/2, `.simple_block_tag` 28/2, `SimpleNode` 332/6, `InclusionNode` 33/1, `SimpleBlockNode` 29/1. Its third section is the instrument trap it exists to record: a module-attribute patch cannot see a binding made at import, so `django.template.library.import_library` reads **0/0** while the engine's bound name reads **11,050/26** — the same function under two spellings, printed separately rather than summed. It also records why the second import must be spelled `django.template.engine`: `django_rust_templates._impl.engine` raises `ModuleNotFoundError: No module named 'django_rust_templates._impl.library'`, which is §3's finding arriving as a stack trace | §6, §7 |
| `m13_measure_stub_expiry.py` | the population that depends on the partial pair's **route**: empty `builtin_tag_stub_names()` puts both names back on Rust's side of `ShellHost._rust_owned` (`host.py:471-474`), routing them to registry bodies that are still stubs, and the suite then runs **240 red (73 failures + 167 errors)** entirely inside `test_partials` and `syntax_tests.test_partials`. It is a *mutation* instrument — `_M13_EXPIRY_MUTATE=1` patches the stub set — and it carries the two guards its first drafts lacked: it asserts `_shim.installed()` before the runner starts, and it counts consultations of the mutated function and exits if the count is zero, because "0 consultations" and "no failures" are indistinguishable in the output | §5(a) |

**Two traps this directory's instruments paid for, both recorded in the recon's
§10 rather than only here.** `scripts/shimpath/sitecustomize.py` installs only
when `DJANGO_RUST_TEMPLATES=1` is set, so a re-exec chain that forgets it
measures **stock Django and reports `OK (skipped=6)`** — a green suite is not a
working instrument. And this checkout's `runtests.py:386-389` defaults
`--parallel` to auto while macOS `multiprocessing` **spawns** rather than forks,
so an in-process mutation is not inherited by the children; `--parallel 1` is
required and is not a performance choice.

**A verdict here is dated.** `.django/` is pinned at 6.0.9 and our side moves one
commit at a time: re-run rather than re-quote.
