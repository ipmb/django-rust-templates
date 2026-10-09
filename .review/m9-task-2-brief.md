# M9 Task 2 review brief — the parse seam

`.django/` is pinned at **6.0.9** and is never modified; the interpreter is
pinned too (`.venv`, Python 3.14). Every Django citation below is a claim about
that tree, so name it when you check one.

## Commits

| Commit | What |
|---|---|
| `0ecc2de` | Task 1's follow-up: the compared path is measured, the guard has teeth |
| `fdb4eb0` | **Task 2** — `Template.compile_nodelist` delegates to the Rust parse |
| `de5a96a` | Task 2's corrections recorded in the plan's Self-Review |

Task 2's plan section is
`docs/superpowers/plans/2026-10-09-m9-interpose-base-and-smartif.md` ("Task 2",
and the Self-Review block at the end).

## What Task 2 claims

`python/django_rust_templates/_impl/base.py` is a transcription of
`.django/django/template/base.py` @ 6.0.9, and `Template.compile_nodelist` — and
only that method — delegates: it calls `django_rust_templates.render.parse_template`,
which drives `_native.compile_template` (Rust) and returns a `Template` built by
`_impl/parser.py::template_for` through `__new__`, so `Template.__init__` is not
re-entered. Everything after the parse (`extra_data`, the debug annotation, the
origin-name rewrite, the re-raise) stays upstream's.

Gate met on the first full run:

```
Ran 1547 tests in 3.642s
OK (skipped=6)
```

and `scripts/check.sh` → `all checks passed` (cargo test 682, rustfmt, clippy,
292 unit tests OK, anchors 0 failures, suite counts 59 lines all
provenance-bearing, transcriptions green, differential 48 cases identical,
engine installed with 24 module(s) still served by Django).

## How to reproduce

```bash
cd /Users/pete/projects/lincolnloop/django-rust-templates
. scripts/env.sh                     # cargo is not on PATH otherwise
bash scripts/check.sh
scripts/run_django_suite.sh template_tests

# the two measurements this task produced
DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \
  .venv/bin/python .review/m9-measurements/m9_measure_render_parity_oracle.py
python .review/m9-measurements/m9_guard_teeth.py

# the guard's teeth, one clause at a time
python -I scripts/check_transcriptions.py
```

Note `run_django_suite.sh` is the *only* meaningful suite invocation: the shim
installs only when `DJANGO_RUST_TEMPLATES == "1"`, which only that script
exports. Running `.django/tests/runtests.py` directly is byte-identical to stock
Django and is evidence of nothing.

## Six things the plan did not have

Each is measured, not reasoned, and each is written up with its measurement in
the plan's Self-Review. They are listed here so you can attack the *fixes* rather
than rediscover the omissions.

1. **No exception conversion anywhere in the plan.** Rust raises one class for
   every parse failure; design decision 2 says Python owns the exception
   *object*. Without a conversion the suite dies three ways at once
   (`AttributeError: 'tuple' object has no attribute 'position'` in
   `get_exception_info`; `assertRaises(TemplateSyntaxError)` seeing an unknown
   class; the parallel runner failing to pickle `_native.UnimplementedTag`). The
   conversion is `render.django_exception`, called from
   `render.parse_template` — **not** from `compile_nodelist`, so upstream's
   method stays a transcription.
2. **The Rust lexer's debug flag was hardcoded `false`** in
   `crates/template-py/src/lib.rs::compile_template`. Upstream chooses
   `DebugLexer` over `Lexer` on `self.engine.debug` (`base.py:183-186`), and the
   measured failure was `TypeError: cannot unpack non-iterable NoneType object`
   from `Template.get_exception_info`.
3. **`ShellHost.has_tag` read the tag registry's *keys* as its
   implementations**, so the two stub tags were claimed by Rust and then
   declined by the identity clause: 245 of the 1547, all
   `'partial' is a built-in tag this engine does not implement yet (M13)`.
   `_rust_owned` is now `names() - stubs()`.
4. **A host exception must be re-raised by identity.** `ShellHost.last_error`
   keeps the object; `django_exception` returns it when `error.class_name`
   matches. Without it five rows (`test_include_fail1`, `test_include_error07`,
   `test_include_error09`, `test_compile_tag_error` ×2) lose the traceback and
   `_culprit_node`. Upstream re-raises the object it caught
   (`base.py:583-585`).
5. **`tests/test_render_parity.py`'s "stock Django" oracle became Rust.** See
   the section below — this is the one I would most like you to attack.
6. **The transcription guard had to be re-measured.** Twelve lines added to
   `compile_nodelist` moved all 28 `COMPARED_PATH` spans.

## The one I would most like attacked: the render-parity oracle

`RenderParityTests.assert_renders_like_django` used to compare
`render_string(source, context)` against
`self.engine.from_string(source).render(Context(context))`. Once `base` is
interposed, `Engine.from_string` builds *our* `Template`, whose
`compile_nodelist` calls `render.parse_template` — the same function
`render_string` calls. Measured (`.review/m9-measurements/m9_measure_render_parity_oracle.py`
wraps `render.parse_template`): both sides enter it exactly once. Every row in
that class was comparing Rust against Rust while its docstring claimed the
comparison was evidence about the Rust parse.

The repair is `tests/test_render_parity.py::django_render`, which builds its tree
from Django's own `Parser` (`render.parser_for` → `parser.parse()` →
`template_for(...)` → `.render(...)`) — the claim `tests/parity_target.py`
already states for the other harnesses — plus
`test_the_python_side_is_not_the_rust_parse`, which pins the call counts.

**Questions I want a verdict on, not agreement:**

- Is `django_render` a faithful reconstruction of what `engine.from_string` did
  before M9, or does it differ in some attribute a row in that class reads?
  I passed `render.UNKNOWN_ORIGIN` to `template_for` deliberately; check that
  reasoning and check nothing else on the attribute list drifted.
- Is `Parser.parse()` under the shim genuinely not Rust? It is a transcription
  whose declared regions `COMPARED_PATH` says do not cover `parse` — is that
  measurement or is it an artefact of what the instrument wraps?
- `django_render` is now a *second* parse implementation on the Python side.
  Is that a hole of its own (a place a divergence could hide) or is it the same
  instrument `test_parser_parity.py` already relies on?

## Also worth a look

- `scripts/check_transcriptions.py`: the new clause requires the
  `COMPARED_PATH` key set to **equal** `DECLARED`'s. Is equality the right
  relation, and does `module_of()` derive what it claims to?
- `tests/compared_path.py`: the restore sweep is now a second identity sweep
  over `sys.modules` as it *is at exit*, not a list captured on the way in.
- `render.django_exception`'s `class_name`/`host.last_error` match. It compares
  `type(raised).__name__ == class_name`. Is a name comparison enough, and can a
  stale `last_error` ever be handed back?
- `_impl/base.py`'s declared regions for `compile_nodelist` — two regions rather
  than one, because `difflib` pairs our prose against upstream's parser
  construction. Check the declaration is honest rather than shaped to pass.
- The `# TRANSCRIBED:` docstring claim that "Everything after the parse is
  upstream's and stays" — read the method and say whether that is true.

## What I am *not* asking

Task 2 does not port any render path to Rust; §5.5 puts the Python node tree on
the render path, so M9 is interposition and seam delegation only. "Not enough of
this is Rust" is the milestone's design, not a finding.

Tasks 3–7 (the `smartif` transcription, `render_value_in_context`, the resolver
seam, the render-annotation protocol, guard close-out) are not done yet.
