# M2 — Rust parser, node AST and descriptor format

**Review target: `.django/` at Django 6.0.9.** An unpinned Django also exists on
this machine, so an unqualified "Django" is ambiguous — every claim below is
against the pinned checkout, and the summary says so where it matters.

This is the milestone round (§11). The unit of review is the milestone, not the
commit, so the whole of M2 is in scope, including the seven plan-correction
commits, which are themselves a finding — see "The plan-vs-tree defect rate".

## What M2 claims

1. `_native.parse` builds the same tree Django's `Parser` does, for every
   template in a **59**-source corpus and **400** seeded random templates, in
   both lexer modes — compared node for node *after materialisation*, by `vars()`
   and by a second traversal through `get_nodes_by_type`.
2. Syntax errors match Django's **message and token**, including which token wins
   when a compile error unwinds through nested `parse` frames.
3. The materialised tree is made of Django's real classes, children built before
   their parent and `token`/`origin` attached after, so M7's `ExtendsNode` will
   find a populated `blocks` mapping.
4. The descriptor format is general enough for the tags that arrive later — in
   particular `AttrValue::Ref`, proven by a stand-in and by `{% resetcycle %}`'s
   hand-populated registry path.
5. Every `class_name` the Rust emitter can produce resolves in the materialiser's
   registry. This replaces an earlier claim that the registry covered N Django
   node classes; that number turned out to have no fixed value (below).

## Commits

| Commit | Task |
|---|---|
| `ef807e4` | M2 plan |
| `38c5b54` | Task 1 — the node descriptor |
| `4998406` | Task 2 — the parser core |
| `2bdacb5` | Task 3 — tag dispatch and the stand-ins |
| `3b9b288` | Task 4 — `_native.parse` and materialisation |
| `cfee463` | Task 5 — the nine tag compile functions |
| `6076a02` | Task 6 — syntax-error parity |
| `f6decf5` | Task 7 — descriptor parity |
| `42ed076` | the tag inventory, pinned executably |

Plan corrections, all of the same shape and all worth reviewing as a set:
`661fad7`, `d9a873d`, `909ddcb`, `a045ea0`, `36c3674`, `98348fc`, `d47dc0d`.

## Evidence

| Check | Result |
|---|---|
| `cargo test -p template-core` | 112 passed, 0 failed |
| `cargo clippy --workspace --all-targets -- -D warnings` | exit 0 |
| `tests.test_parser_parity` | 11 tests, 59 sources × 2 modes + 400 random × 2 modes, 0 mismatches |
| `tests.test_parser_errors` | 9 tests, 32-case corpus |
| `tests.test_materialise` | 20 tests |
| `tests.test_tag_inventory` | 7 tests |
| `tests.test_lexer_parity` | 13 tests |
| `scripts/check.sh` (full) | exit 0, `Ran 1547 tests … OK (skipped=6)` |
| Progress meter | 25 modules still Django's — unchanged, and expected to be |
| `scripts/differential.py` | unchanged |

The suite's 1547/6 is the acceptance criterion and it is *expected* to be
unchanged: M2 adds `_native.parse` and a shell module nothing imports yet, so the
engine is not in the suite's path (§10.1 — engine progress and integration
progress move independently). The suite's job at M2 is to prove M2 broke
nothing.

## Guards made to fail, not just read

Every test below was observed failing for its intended reason before being kept.

- **`s` → `content`** in `NodeDesc::text`: `test_corpus_matches_without_debug_positions`
  ERRORs on the first text-bearing source (`TypeError: TextNode.__init__() got an
  unexpected keyword argument 'content'`), while the corpus-coverage test still
  passes — the split between "the corpus is populated" and "the trees match"
  behaves as designed.
- **A value mutation** (`s` → `s + "!"`), added because the rename above is a
  crash rather than the structural diff the plan claimed: FAILS on `'hello'` with
  a readable diff, token identical, so the failure is the tree and not the token.
- **The second traversal**, isolated by making `get_nodes_by_type` return `[]`:
  268 subtests fail with element 0 identical — `tree` same, `subtrees` differ.
  This is the only mutation that reaches the traversal's actual purpose.
- **`WS_CLASS` losing `\x1E`**: 1622 parity failures (1620 from the randomised
  pass). The whitespace divergence, closed at M1, is genuinely guarded.
- **`is_space` accepting three of Python's four extra separators**: 2 cargo
  failures.
- **`querystring` removed from the FilterExpression bucket**: 3 inventory
  failures.
- **`SpacelessNode` removed from `NODE_CLASSES`**: the registry test reports
  `'{% spaceless %}x{% endspaceless %}' emits ['SpacelessNode'], which the
  materialiser cannot build`.

## The plan-vs-tree defect rate — a process finding, not eight slips

Seven plan-correction commits for seven tasks. The reviewer asked for this in the
shape *class of assumption / what the snippet believed / what the tree does*, and
the classes turn out to be three, not one:

| Class | Task | The snippet believed | The tree does |
|---|---|---|---|
| environment | 5 | `split_whitespace()` splits tag arguments | Django splits on Python's separator set, incl. U+001C–U+001F — the plan's *own* test required it |
| environment | 5 | `NodeRef` is importable unconditionally | it is feature-gated; an unguarded import fails |
| environment | 6 | a bare `Parser` has builtins | `builtins=None` → `[]` (`base.py:518-519`), so 25 of 32 expectations would fail |
| environment | 6 | `{% autoescape "on" %}` is an unmatched quote | not the discriminator; `{% autoescape "on off" %}` is |
| environment | 7 | `Parser(tokens)` parses built-in tags | registers nothing, so every built-in raises `Invalid block tag` — the whole corpus would have compared `ours=ok` against `theirs=error` |
| environment | 7 | `django_rust_templates._impl.parser` imports | `_impl/__init__.py` is the shim's `django.template`; the package cannot import under that name |
| count | 6 | "four unclosed cases", "30-case corpus" | seven sources across four tags; 32 cases |
| count | 7 | "60-odd" / "60-source corpus" | 59 |
| reasoning | 7 | renaming `s`→`content` yields "a small wrong tree" | it is a `TypeError` at construction |
| reasoning | 7 | renaming the nodelist kwarg yields "subtrees differs while tree may not" | also a `TypeError`; and any rename a constructor *accepts* still changes `tree`, since `describe` keys nodelists by attribute name |

Three things this says that are worth more than the count.

**The environment class is not carelessness.** Task 7's plan is *correct* that
both `{% resetcycle %}` paths error at M2 — measured: `No cycles in template.`
and `Named cycle 'foo' does not exist.`, because the anonymous path needs a prior
`{% cycle %}`, which needs `FilterExpression`. A plan that gets Django's behaviour
right while getting the harness wrong is not written carelessly; it is written
with good knowledge of the target and no model of the environment the snippet
runs in. The fix that follows is a **declared environment per task** — what is
constructible at that point, what imports resolve, what the shim does and does
not shadow — which also matters for fan-out agents, who have strictly less harness
context than the author.

**The reasoning class is caught by neither of the other two fixes.** "Renaming
`this` produces that evidence" is a claim about what an edit does, and only
running the step refutes it. The plan's prescribed mutations were replaced by
substitutes that reached the stated goal, which is why the evidence table above
is honest rather than empty.

**The two classes want different fixes, and neither is "run everything".** An
environment assumption is a wrong belief about *static* facts, so a written-down
contract can contradict it before the snippet runs — cheap, and it catches the
error while writing rather than after. A mutation instruction is a *prediction
about the effect of an edit*, and nothing contradicts a wrong prediction except
making it and looking: no declared environment would have told the plan that
renaming `s`→`content` produces a `TypeError` rather than a small wrong tree.
So the answer is two targeted fixes — a declared environment per task for the
static class, and mandatory execution for mutation steps specifically — rather
than a general rule to execute every snippet, which spends the expensive remedy
where it is not the only one.

Executing snippets before committing is a rule either way (§11); the refinement
is that it is the *sole* remedy for one class and overkill for the other.

## What I did not verify

- `{% resetcycle %}`'s **success** path is exercised only with a hand-populated
  registry, because `{% cycle %}` is M5. Its descriptor shape is pinned by a unit
  test, not by a template.
- `render_text_only`'s non-text arm is unreachable at M2 and is tested directly
  instead.
- No timing. The parser is recursive and unbounded in nesting depth for a hostile
  template, exactly as Django's is; that is a deliberate match, not a measurement.
- The randomised alphabet is `"abc \n\t'\"é🎉"` plus a tag vocabulary, so it
  reaches shape and nesting but not exotic text. Whether it is wide enough is
  attack item 5.

## Carried findings

**M1, fixed before M2 began: Python's whitespace table is not Rust's.** Python's
`str.isspace()` accepts U+001C–U+001F as well as Unicode `White_Space`; Rust's
`char::is_whitespace()` does not — Python 29 code points, Rust 25, checked
exhaustively over U+0000..U+10FFFF. M1 had shipped a lexer that trimmed tag
contents with the Rust table and a `smart_split` whose pattern used Rust's `\s`,
so Django accepted `{% autoescape<U+001C>on %}` and this implementation rejected
it. Fixed in M1 (`1a013ae`), not pinned, because it is exactly what the
differential tests exist to catch. The residual risk runs the *other* way and is
recorded in `pyunicode.rs`: `is_space` inherits Rust's table, which tracks the
toolchain's Unicode version, and no `cargo test` can see that drift — the guard is
`test_whitespace_table_is_exactly_pythons`.

**M2: `_impl/parser.py` makes `django.template.parser` importable** — a name
Django does not have. Nothing imports it, and `_shim.unshimmed()` compares source
trees so the meter is unaffected, but if Django ever grows `template/parser.py`
this name would shadow it.

**No fixed number of Django node classes exists.** An earlier figure this project
carried — 34 — was an artifact: `Node.__subclasses__()` reports import *state* and
moves as modules load (6 with only `base` imported, 42 after `django.setup()`,
45/46/47 as the templatetag modules follow). The reproducible figures are 32
transitive in `django/template/*.py`, 29 as a direct `class X(Node)` scan (the
three extra being `InclusionNode`, `SimpleNode`, `SimpleBlockNode`), 15 in
`django/templatetags/*.py`, 47 total. And of the 47, only **12** are observable to
Django's suite at all. The registry assertion was therefore replaced with a closed
obligation, and the pin-bump checklist item is the drift guard (§10.4).

## What I want attacked

1. **The wire form**: nested tuples with native values and no discriminant.
   `bool`-before-`int` decoding is the hazard; is there a third case that needs
   its own wire class?
2. **`py_repr_str`** — a hand-rolled Python `repr` for two messages. Is the
   reachable-input argument airtight, and should the `{% resetcycle %}` message
   route through the host's `repr` instead?
3. **Host-first dispatch order.** It is argued from `add_library`'s
   last-registration-wins semantics; is that the right reading of Django's
   registry at M6?
4. **The `Unimplemented` error for `{{ variable }}`.** It makes M2 loud rather
   than wrong; does it make any M2 test vacuous?
5. **Task 7's randomised pass compares outcomes**, so illegal templates compare as
   failures. Is the alphabet too narrow to reach a real class of divergence?

Ask for executable findings where you can — a command and its expected output
rather than prose. Where a finding is about a number, please name the metric; four
count disputes in this project have each turned out to be two right answers to two
unstated questions.
