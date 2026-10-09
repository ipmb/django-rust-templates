# Review round 1 — design spec (`7635600`), M0 (`39b9e48`), M1 lexer plan

Verified against `/Users/pete/projects/tmp/django` @ `8ee7341527` (`stable/6.0.x`,
6.0.5a0) — **not** the pinned 6.0.9. See finding 14.

## Verdict

**REJECT** — §5.5's shadow-tree justification is invalidated by Django's own test
instrumentation; the §5.2 seam cannot express parser re-entrancy or the exception
annotation protocol; M0's green suite cannot distinguish a working shim from an absent one.

The M1 plan is close to approvable and the engine-first rewrite of §10 is a real
improvement. The blockers are all in the design and in what the harness proves.

## Blockers

### 1. `setup_test_environment()` makes the Python node tree the render path for the whole suite

- Spec §5.5; Django `django/test/utils.py:108-114,151-152`, `django/test/runner.py:1119`
- Django's runner calls `setup_test_environment()`, which does
  `Template._render = instrumented_test_render` and the same for `PartialTemplate`, where

  ```python
  def instrumented_test_render(self, context):
      template_rendered.send(sender=self, template=self, context=context)
      return self.nodelist.render(context)
  ```

  and `NodeList.render` (`base.py:1091`) is
  `SafeString("".join([node.render_annotated(context) for node in self]))`.

  For all 1547 tests the render entry point is therefore **the Python nodelist**. §5.5 has
  Rust render from its own compiled form with the Python tree introspection-only. If those
  nodes are inert descriptors, every test renders empty.
- Your §5.5 claim — "zero tests call `node.render(context)` directly" — **is true**; I
  verified it. An exhaustive grep for
  `(node|Node|nodelist|NodeList)\w*\.(render|render_annotated)\(` across
  `tests/template_tests/` returns one hit, a docstring at `syntax_tests/test_for.py:334`.
  But it is a claim about *test* code, and the coupling comes from *framework* code the
  tests run under. The argument is sound and tests the wrong proposition.
- Not about the `template_rendered` signal — nothing in `template_tests` asserts it (zero
  hits). It is the `_render` replacement, and it is unconditional.
- **This fires at M9**, the moment `base` is interposed. Before M9 everything falls back and
  the suite is green trivially; at M9 the entire suite flips onto your `NodeList.render` and
  `Node.render_annotated` at once. M9 is the highest-risk step in the project and §10.3
  gives it one line.
- Fix: decide and write down which you are doing — (a) shadow `NodeList.render` to delegate
  to Rust wholesale, which keeps the patch working but moves findings 2's semantics into
  Rust; (b) make shadow nodes genuinely renderable in Python; or (c) shadow
  `django.test.utils` too, which is outside the namespace the finder owns.

### 2. The `render_annotated` error-annotation protocol crosses the seam and is not modelled

- Spec §5.2, §5.5; Django `base.py:1044-1070`
- On any render-time exception with `engine.debug`, Django mutates the **Python exception**
  and reads from the **Python node**:

  ```python
  if not hasattr(e, "_culprit_node"):
      e._culprit_node = self
  if (not hasattr(e, "template_debug")
          and context.render_context.template.origin == e._culprit_node.origin):
      e.template_debug = context.render_context.template.get_exception_info(
          e, e._culprit_node.token)
  ```

  So at render time any failing node must be nameable as a Python `Node` with a correct
  `.origin` and `.token` — exactly the render-time correspondence §5.5 says is never
  required.
- Caught by `tests/template_tests/test_nodelist.py:44-86`
  (`ErrorIndexTest.test_correct_exception_index`), which asserts exact
  `(template_debug["start"], template_debug["end"])` pairs — e.g. `(38, 56)` — for an
  exception raised by a *Python* custom tag (`bad_tag.badsimpletag`) nested inside a
  *Rust-owned* `{% for %}`. Also `tests.py:146,166-172` on `template_debug["during"]` and
  `["before"]` inside `{% block %}`/`{% include %}`.
- The logic is stateful across nested renders: innermost node wins `_culprit_node`, but
  `template_debug` attaches only at the frame whose `render_context.template.origin`
  matches. That means threading a live Python exception object through Rust render frames.
- Fix: give `ExtensionHost` an explicit error channel, e.g.
  `annotate_exception(&self, err: &Error, node: NodeRef, token: &Token)`, and make `Error`
  round-trip the original Python exception rather than flattening it to a Rust type (which
  loses `_culprit_node` and the exception's identity).

### 3. `ExtensionHost::call_tag(&mut Parser)` cannot express parser re-entrancy

- Spec §5.2; Django `library.py:211-212`, `defaulttags.py:1242-1250`,
  `loader_tags.py:230-233`, `tests/template_tests/templatetags/custom.py:396-399`
- The spec hands `parser: &mut Parser` (a Rust `Parser`) to a Python callback. Python
  callbacks in the suite re-enter the parser mid-parse:
  - `library.py:211-212` — `simple_block_tag`'s compile function calls
    `parser.parse((end_name,))` then `parser.delete_first_token()`. `Library` is
    Python-owned per §6, so this is Python. `custom.py` registers three such tags (`div`,
    `div_custom_end`, `one_param_block`), exercised by `test_custom.py`.
  - `custom.py:398` — `parser.extra_data["extra_data"] = "CUSTOM_DATA"`, asserted at
    `tests.py:148-155`.
  - `defaulttags.py:1250` — `{% partialdef %}` stores a `PartialTemplate` proxy into
    `parser.extra_data`; `test_partials.py` reads `template.extra_data["partials"][...]` at
    20+ sites.
  - `loader_tags.py:230-233` — `do_block` sets an arbitrary attribute
    `parser.__loaded_blocks` (module-level function, so no name mangling).

  `parser.parse()` from Python needs `&mut Parser` while Rust still holds it, and that
  nested parse dispatches tags that may re-enter Python. Unbounded mutual recursion across
  the FFI with an aliasing `&mut`. You cannot hand `&mut Parser` to PyO3 and survive it.
- `extra_data` must also be a real Python `dict` holding Python objects, not a Rust
  `HashMap<String, Value>`. Django assigns `self.extra_data = parser.extra_data` *inside*
  the `try` in `compile_nodelist`, so the attribute is absent after a parse failure —
  `test_partials.py:171` tests that absence.
- Fix: specify a `ParserHandle` with interior mutability, an explicit re-entrancy contract,
  a Python-dict-backed `extra_data`, and somewhere for arbitrary attributes.

### 4. (M0) A green suite does not prove the shim ran

- `scripts/shimpath/sitecustomize.py:14-17`, `scripts/run_django_suite.sh:36-40`; CPython
  `site.execsitecustomize`
- `sitecustomize.py` does an unguarded `import django_rust_templates` then `install()`.
  CPython's `site.execsitecustomize` catches any non-`sitecustomize` exception, writes one
  line to stderr, and **continues**. So if `django_rust_templates` is not importable — venv
  not synced, maturin not built, `PYTHONPATH` wrong, `python -S` — the suite runs on stock
  Django and prints `Ran 1547 tests … OK (skipped=6)`, byte-identical to success.
- With everything falling through, a working shim and an absent one are indistinguishable.
  Your M0 claim ("`exceptions.py` actually in the path") is true, but `tests/test_shim.py`
  is what establishes it, not the suite — and from M9 the suite *is* the acceptance gate.
- This is the sharp form of your own "does the fallback hide work?" question. It hides more
  than unimplemented modules; it hides the shim's absence.
- Fix, three cheap independent guards: (1) wrap `sitecustomize` in try/except and
  `os._exit(1)` loudly when `DJANGO_RUST_TEMPLATES=1` but install raised — silent fallback
  must be unreachable when the flag is set; (2) have `run_django_suite.sh` assert
  interposition in a subprocess before `runtests.py`, checking
  `django.template.exceptions.__file__` is under `_impl`; (3) print `installed()` and
  `len(unshimmed())` at suite startup. The `__engine__` marker in `differential.py:55-60`
  is exactly the right instinct — it just needs to exist on the path that gates commits.

## Majors

### 5. (M0) The fallback does not survive a partially-implemented subpackage

- `python/django_rust_templates/_shim.py:52-59`
- `find_spec` sets `submodule_search_locations=[_IMPL_DIR, *real_paths]`, so
  `django.template.X` falls back to Django — but **only at the `django.template` level**.
  Django has two subpackages, `django/template/backends/` and `django/template/loaders/`.
- The first time `_impl/loaders/__init__.py` exists, `django.template.loaders` resolves to
  *our* package with `__path__ = [_IMPL_DIR/loaders]` and nothing else. Django's `loaders/`
  is no longer on that path, so `django.template.loaders.filesystem` raises
  `ModuleNotFoundError` until every loader lands in one commit. §10.3 M10 walks into this;
  M13 does the same for `backends`.
- `unshimmed()` reports the opposite of what happens: `loaders.filesystem` appears as
  "still served by Django" when it will not import at all. The meter becomes actively
  misleading exactly when it matters.
- Untested: `test_internal_imports_are_interposed` asserts only the top-level `__path__`;
  no test creates a nested package.
- Fix: handle any `fullname` under `TARGET + "."` that is a package in our tree, returning a
  spec whose `submodule_search_locations` likewise appends Django's corresponding directory.
  Add a test that creates a one-module `_impl` subpackage and asserts a sibling still
  resolves to Django's.

### 6. `{% include %}` caching is keyed on Python node identity, and `CounterNode` must persist

- Spec §5.5, §6; Django `loader_tags.py:197`; test `syntax_tests/test_include.py:371-397`
- `IncludeNode.render` caches the resolved template in
  `context.render_context.dicts[0].setdefault(self, {})` — a Python dict **keyed on the
  Python `IncludeNode` instance**. `test_include_cache` renders
  `{% for x in vars %}{% include "include" %}{% endfor %}` over an innermost
  `{% load custom %}{% counter %}` and asserts `"012345678"`. That passes only if the single
  Python `CounterNode` — mutable `self.count`, `custom.py:386-393` — is constructed once and
  re-entered nine times.
- Under a shadow tree materialised from Rust descriptors, any re-materialisation (per
  render, per include, lazily on attribute access) resets `count` and yields `"000000000"`.
  The spec never states the materialisation lifetime.
- Fix: specify materialisation as once per `Template` compile, with identity-stable
  `NodeRef` handles for the compiled template's life. Make `test_include_cache` an explicit
  M7 gate.

### 7. `ValueResolver` and `call_filter` are the wrong granularity

- Spec §5.2; Django `base.py:948-1000` and `:805-820`
- `resolve(&self, value, path: &str)` takes a whole dotted path. `_resolve_lookup` is a
  per-bit loop whose control flow is Python-exception-driven:
  `hasattr(type(current), "__getitem__")`, then `getattr`, then `current[int(bit)]`, with
  `isinstance(current, BaseContext)` special cases and a re-raise when `bit in dir(current)`
  so a raising `@property` propagates rather than being swallowed. The failure message is
  `"Failed lookup for key [%s] in %r"` with `(bit, current)` — a Python `repr` of the
  intermediate object. Passing a whole path forces the host to own the loop, so Python owns
  `VariableDoesNotExist` and its message, contradicting §6.
- `call_filter(&self, name, value, arg)` loses the per-filter attributes Django reads at
  `base.py:810-816`: `expects_localtime`, `needs_autoescape` (changes the call signature —
  the filter receives an `autoescape=` kwarg), `is_safe` (changes output escaping when the
  input `isinstance(obj, SafeData)`). `library.py:85` propagates all three.
- Fix: make resolution per-bit (`resolve_bit(&self, value, bit: &str)`) so Rust keeps the
  loop and constructs the error; add `filter_flags(&self, name) -> FilterFlags` or pass
  flags through `call_filter`.

### 8. (M1) `Token.split_contents()` is in M1's scope but absent from the plan

- Spec §10.3 M1 row names `Token`; plan Task 1 implements `Token` as a data struct only.
- `tests/template_tests/test_parser.py:24-33` (`test_token_smart_split`) constructs
  `Token(TokenType.BLOCK, 'sometag _("Page not found") value|yesno:_("yes,no")')` and
  asserts `split_contents()` returns `["sometag", '_("Page not found")',
  'value|yesno:_("yes,no")']`. That is `smart_split` plus the `_("`/`_('` translation-bit
  rejoining at `base.py:388-400` — genuinely fiddly quote handling, and nothing in the plan
  covers it.
- Either pull it into M1 (it belongs with `Token`) or amend the §10.3 M1 row to say `Token`
  means the data carrier only and name the milestone that owns `split_contents`. Right now
  the plan silently narrows the milestone.

## Answers to your four questions

### (a) Is §10.2's claim survivable?

**Yes — and your correction, while a real improvement, contains one wrong fact and an
overstated conclusion.** Verified in `django/template/`:

```
base.py:59    from django.template.context import BaseContext   # module-level
base.py:70    from .exceptions import TemplateSyntaxError       # module-level
base.py:148       from .engine import Engine                    # deferred (inside a method)
context.py    (no intra-package imports at all)
engine.py:7   from .base import Template                        # MODULE-LEVEL
engine.py:8   from .context import Context, _builtin_context_processors
engine.py:9   from .exceptions import TemplateDoesNotExist
engine.py:10  from .library import import_library
```

1. **"engine.py: none" is wrong.** `engine.py` has four module-level intra-package imports,
   including `from .base import Template` at line 7.
2. **The deferral runs the other way round.** It is `base`'s import of `engine`
   (`base.py:148`) that is deferred; `engine`'s import of `base` is module-level and
   unconditional. The cycle is broken on the base→engine side.
3. **Your conclusion #2 is right, and stronger than you argued.** Because `engine.py:7` is
   module-level and unconditional, Django's `Engine` *cannot* avoid picking up our `base`.
   Interposing `base` transparently re-points every importer, provided ours is
   API-compatible. Good news, correctly identified.
4. **"The real unit is {base, context}" is not established by your own evidence.** `base`
   needs exactly one name from `context` (`BaseContext`), and `context.py` has *zero*
   intra-package imports, so there is no cycle and no mutual dependency. Django's
   `BaseContext` works fine underneath our `base`, and our `context` works fine underneath
   Django's `base`. They are independently interposable, in either order. If anything the
   evidence says `context` is the *easiest* module in the package to interpose first — no
   inbound import-time constraints at all.
5. **None of this touches §10.2's actual claim.** "`base.py` cannot be interposed piecewise"
   is true because `base.py` is one file and interposition is per-module (§10.1). The import
   graph is irrelevant to it. The second clause — "cannot be complete until the variable
   resolver is" — is a completeness claim about `Variable.resolve`, and it is also right.

**So: keep §10.2's conclusion, fix its reasoning, and do not replace it with the
{base, context} framing.** The accurate statement is: *`base` is one module, so it
interposes atomically; it depends at import time only on `context.BaseContext` and
`exceptions`, both of which can be Django's; the binding constraint is therefore not
dependency but completeness — `base` contains `Variable.resolve`, which is not finishable
until M8.*

And the thing worth adding that neither version says: **the real reason M9 is dangerous is
finding 1.** Interposing `base` means owning `Node.render_annotated` and `NodeList.render`,
which is what `setup_test_environment` routes all 1547 tests through. That is the risk in
the milestone, not the import graph.

I would not have you re-commit the spec just for this — fold it into the round-1 resolution
as you proposed. Holding the target still during review was the right call.

### (b) Does the M1 plan establish its done-when?

**Almost. The parity test is the right instrument and `test_comparison_target_is_genuinely_djangos_lexer`
is excellent** — same self-verification instinct as `__engine__`, and it anticipates the M9
trap where `Lexer` silently becomes your own shell. I checked the plan's lexer line by line
against `base.py:406-510` and found no behavioural divergence: the `in_tag` toggle on empty
segments, `lineno += count("\n")` on non-empty only, the verbatim ordering,
`token_string[2:-2].strip()`, and `is_verbatim_start` (equivalent to
`content[:9] in ("verbatim", "verbatim ")` for all inputs, since `content` is already
stripped) are all correct. The `CharIndex` byte→char conversion is correct including the
empty-source and end-of-string cases.

**The unmeasured class is the one the corpus cannot reach: the corpus and the implementation
share an author.** 47 hand-picked cases test the divergences you thought of. A divergence
neither you nor I imagined passes. The fix is cheap and removes the whole class: add a
randomised differential pass to `tests/test_lexer_parity.py` — generate a few thousand
strings from the alphabet `{ } % # \n space a é 🎉` at lengths 0–40 with a fixed seed, and
compare `_native.lex` against Django's lexer in both modes. No new dependency, runs in
under a second, and it is the only thing in the plan that can find an unknown unknown. Pin
the seed so failures reproduce, and print the failing source on mismatch.

Three narrower gaps:

- **No corpus entry puts a newline inside a verbatim block.** `{% verbatim %}a\nb{% endverbatim %}`
  and `{% verbatim %}\n{% if x %}\n{% endverbatim %}` are unrepresented, so a bug in
  lineno accounting *during* verbatim state is invisible. Add both.
- **`segments()` has no invariant test** that the returned vec always has odd length and
  strictly alternates. The entire `in_tag` toggle depends on it; it is a one-line
  `assert!(out.len() % 2 == 1)` test over the corpus.
- **`TokenType` name mapping is dropped at the FFI.** `_native.lex` returns `u8`, and
  `Token.__repr__` uses `token_type.name.capitalize()` → `"Text"`, `"Var"`, `"Block"`,
  `"Comment"`. That repr is asserted by `test_parser.py:36-37` and inside
  `BlockTranslateNodeTests.test_repr` (`i18n/test_blocktranslate.py:881-895`). Recording
  the mapping as part of M1's contract costs nothing now; discovering it at M12 is a silent
  failure.

Otherwise the parity test does cover the debug/non-debug split properly, which is what
`LexerTests`/`DebugLexerTests` in `test_base.py` actually require.

### (c) Is the `debug`-flag Lexer a fidelity risk at M9?

**The Rust-side merge is fine. The risk is that the plan records no M9 obligation, and two
of those obligations are test-pinned in ways the plan does not mention.**

`tests/template_tests/test_parser.py:46-50` asserts:

```python
lexer = Lexer("{% for i in 1 %}{{ a }}\n{% endfor %}")
self.assertEqual(
    repr(lexer),
    '<Lexer template_string="{% for i in 1 %}{{ a...", verbatim=False>',
)
```

So at M9 the shell must provide:

1. **Two distinct classes.** `Lexer.__repr__` interpolates `self.__class__.__qualname__`, so
   the name must be literally `Lexer` (and `DebugLexer` for the other). `test_base.py:34-45`
   additionally instantiates both via a one-argument constructor and requires different
   position behaviour from each.
2. **`lexer.verbatim` must exist and be `False`, not `None`.** Your Rust models it as
   `Option<String>`. Surfacing that directly through the shell gives `verbatim=None` and
   fails this assertion on a character. Trivial to map, easy to miss.
3. **`self.verbatim` persists on the instance after `tokenize()`.** `_native.lex` is a flat
   stateless function, so the shell's `Lexer` will need to hold that state itself.

None of this makes the flag wrong — it is the right call for the Rust layer, and the
plan's rationale is sound. It makes the *docstring* wrong where it says the distinction "is
not worth reproducing here": it is not worth reproducing in Rust, and it is mandatory in
Python. Reword it to say so and add these three obligations to the M9 row, or to a
`docs/` contract note, while they are cheap to write down.

Related, same category: the plan's Rust `Token` has `lineno: usize`, but Django's
`Token.__init__(self, token_type, contents, position=None, lineno=None)` defaults both to
`None`, and `test_parser.py:25-27,36` constructs `Token(TokenType.BLOCK, "some text")` with
two positional arguments. `usize` cannot carry that. Fine for M1, a contract gap at M9.

### (d) Which failure modes are missing from Review Focus?

The five listed are the right five for the scanner, and items 2 and 3 are the two I would
have led with. Missing:

6. **The corpus shares an author with the implementation** — see (b). This is the one that
   matters; the others are instances of it.
7. **Verbatim × lineno.** Items 4 and 5 are listed separately and never crossed. No corpus
   entry has a newline inside verbatim.
8. **`TokenType` name round-trip** — the FFI drops it and `Token.__repr__` needs it. See (b).
9. **`debug_assert_eq!(token_start, COMMENT_TAG_START)` vs Django's bare `assert`.** Django's
   assert is live unless `python -O`; `debug_assert!` compiles out under
   `maturin develop --release`. The branch is unreachable given `tag_re`, so this is
   belt-and-braces, but if you believe it is reachable enough to assert, assert in release
   too.
10. **Lone surrogates.** A Python `str` may contain `"\ud800"`; a Rust `&str` cannot, so
    PyO3 rejects it while Django's lexer handles it. Almost certainly not in the suite —
    worth one line in Review Focus so nobody rediscovers it at M9.
11. **`segments()` alternation is an unchecked invariant** — see (b).

## Minor

### 9. (M0) `_impl/__init__.py` is a verbatim transcription — and §6's policy is what needs fixing

You asked directly. **Yes, that file is vendored**: it is Django's
`django/template/__init__.py` with the module docstring and the `# NOQA isort:skip` comments
stripped, and nothing else changed — same imports, same order, same split `__all__`
assignments. I diffed them.

But the right conclusion is that **§6's policy is wrong at the boundary, not that the file
is wrong.** For `__init__.py` the export list *is* the behaviour: import order is
load-bearing (`engines = EngineHandler()` must precede the later imports; `from . import
autoreload` registers signal receivers), and there is no way to write it "fresh" and
different without being wrong. Compare `_impl/exceptions.py`, which *is* written fresh —
restructured `__init__`, rewritten docstrings — and which I verified is behaviourally
identical to Django's (constructor signature, attribute defaults, `args` content,
`TemplateSyntaxError`'s empty body). That is the policy working.

Amend §6 to distinguish re-deriving an interface contract (required, must be faithful) from
copying implementation logic (not done, written fresh against Django as spec). Then mark
which files are transcriptions, because those need diffing against upstream on every Django
bump rather than reviewing as original work.

### 10. `unshimmed()` is the right trade-off, with one caveat

Static comparison is correct — avoiding import side effects matters more than runtime
accuracy for a progress meter, and moving off `pkgutil.walk_packages` was right. Two
caveats: finding 5 (it gets the nested-subpackage case positively wrong, not merely
imprecise), and it measures *file existence*, not behaviour — an `_impl/base.py` doing
`from django.template.base import *` would drop out of the list while changing nothing.
Consider pairing the count with something behavioural.

On "should the suite fail loudly when a fallback is active": no. That is `strict=True` and
it is unusable until M14. Make it fail loudly when the shim is **not installed** — different
condition, and the one that is always wrong. See finding 4.

### 11. The 1547 baseline was measured against what is effectively stock Django

§2's baseline is honest and measuring beats estimating, but it was taken with every module
falling through, which per finding 4 is indistinguishable from the shim not running. It
establishes the target, which is its job, but it is not evidence about the mechanism. One
sentence in §2 saying so would stop it reading as a validation.

(Your 1547 is right and my naive count was the wrong one: I counted 1517 `def test` methods
across 124 files; the difference is inherited methods re-run via subclassing, e.g.
`MiscBlockTranslationTests(MiscTests)`.)

### 12. The stated reason for the `test_case_sensitivity` skip is wrong

- Spec §2; Django `tests/template_tests/test_loaders.py:203-210`
- §2 attributes it to this machine's case-insensitive APFS. The actual guard is:

  ```python
  @unittest.skipUnless(
      os.path.normcase("/TEST") == os.path.normpath("/test"),
      "This test only runs on case-sensitive file systems.",
  )
  ```

  On POSIX `os.path.normcase` is the identity function, so this is `"/TEST" == "/test"` →
  `False` on **every** POSIX platform, case-sensitive Linux included. It runs only on
  Windows, where `normcase` lowercases. The filesystem is irrelevant.
- It matters because as written §2 invites someone to build a case-sensitive APFS volume to
  recover the test, and that would not work. Record it as "skipped on all POSIX platforms by
  an upstream guard".
- The real consequence is a coverage hole: `FileSystemLoader` case-sensitivity is unverified
  by the suite on any platform you will run, and that is loader code you own at M10.
  Consider a project-side test, outside Django's tree so the no-edit rule holds.

The other five skips are genuinely structural and I agree with excluding them.

### 13. (M0) `install()` imports the real package to find its path

`_shim.py:62-77,94` — `_real_search_paths()` calls `importlib.import_module("django.template")`
purely to read `__path__`, then `_purge()` removes it. Any module imported as a side effect
that bound a name via `from django.template import X` keeps a stale reference. Nothing does
today, so this is latent — but avoidable: `importlib.util.find_spec("django.template")`
yields `submodule_search_locations` without executing the module.

(I checked the related worry and it is fine: `autoreload`'s receivers carry `dispatch_uid`
at `django/template/autoreload.py:48,54`, so repeated install/uninstall cycles in
`test_shim.py` do not duplicate them.)

### 14. The review tree is not the pinned version

§4 pins `6.0.9` and the tag exists on the remote
(`9db1b3ac1022ddb8d9f42a4e9fb27b70473deb8e`), so the pin is valid. But
`/Users/pete/projects/tmp/django`, which you pointed me at, is `6.0.4-17-g8ee7341527` on
`stable/6.0.x`, dated 2026-04-20 — five patch releases of drift. §6 ports error strings and
reprs *verbatim*; porting them from this tree is porting from the wrong spec. Land
`.django/` at 6.0.9 and re-check every finding above against it. The M1 plan's Global
Constraints correctly name `.django/` as the source of truth; the plan should not be
executed against the `/tmp` tree.

## Resolved since the draft of this review

- **M4 ordering.** I had a finding that M4 ("all 57 filters" → 58 filter test files) could
  not reach its gate, because `filter_tests/` templates use `{% autoescape %}` 64 times and
  `{% if %}` once, both scheduled later. The §10 rewrite dissolves it: M4 is now measured by
  corpus parity rather than test files. The new framing is correct and the old one was not.
- **§7's finder installed too late.** I had a finding that "installed before Django's test
  runner starts" was unachievable, since `tests/runtests.py:28-31` imports `django.test` at
  module top. M0 answers it correctly with `sitecustomize` on `PYTHONPATH`, which runs
  during `site` initialisation. That is the right mechanism. §7 still describes the vague
  version — fold the M0 approach back into it.

## Plausible / unverified

- **Vendoring policy (§6).** I would keep it: the suite pins exact strings, so "written
  fresh against Django as spec" and "vendored" converge on the same behaviour, and shipping
  Django's Python inside a drop-in replacement for it is self-defeating. But findings 1, 2
  and 3 all say the Python side is materially larger than §6 implies. Measure it at M9
  before committing further.
- **`django.views.debug` and `django.forms.renderers`** both import from `django.template`
  and will receive the shim. Nothing in `template_tests` exercises them; they are the next
  thing to break in a full-Django run.
- I did not execute anything — plan mode. All M0 and M1 findings come from reading committed
  source. In particular I did not reproduce your `/tmp/m1plan` run.
- I did not evaluate M11–M15 ordering in any depth.

## What's working

- **The §5.5 claim is true and I verified it.** Zero tests call `node.render(context)`
  directly. Honestly made, correctly stated; only the inference fails.
- **`sitecustomize` on `PYTHONPATH`** — correct solution to a problem the spec left
  hand-wavy, with a docstring that explains the ordering. Best decision in M0.
- **`tests/test_shim.py`.** `test_an_implemented_module_wins_over_djangos` and
  `test_internal_imports_are_interposed` test the load-bearing claim rather than restating
  the implementation, and `test_strict_mode_raises_while_falling_back` pins a property that
  is trivially true now and will stop being so. It earns its docstring.
- **`_impl/exceptions.py`** is behaviourally identical to Django's. Verified.
- **The `__engine__` marker** (`differential.py:55-60`) and
  **`test_comparison_target_is_genuinely_djangos_lexer`** (M1 plan, Task 5). Two
  self-verifying tests that anticipate their own failure mode, one of them anticipating a
  trap that is four milestones away. Rare. Finding 4 asks you to apply the same instinct one
  level up; it is not a criticism of either.
- **The §10 engine-first rewrite.** Separating engine progress (differential corpus) from
  integration progress (`unshimmed()`) is the correct decomposition and it fixed the M4
  ordering problem as a side effect.
- **The M1 lexer itself.** I compared it line by line against `base.py:406-510` and found no
  behavioural divergence. The non-DOTALL and retry-one-character-later cases are both
  correctly identified and correctly tested, and the `CharIndex` conversion is right at the
  boundaries.
- **Testing your own §10.2 claim instead of defending it**, and declining to edit the spec
  mid-review. Both right.
- **The doctest discovery** in the pre-execution validation, and pinning it as a Global
  Constraint. A plan bug that `cargo test` catches only on a file whose unit tests pass is
  exactly what pre-execution validation is for.

---

VERDICT: FAIL
