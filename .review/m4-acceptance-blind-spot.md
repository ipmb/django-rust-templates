# M4 — what the acceptance criterion can and cannot see

Established while checking whether the 1547-test gate validates M4's
deliverables. It does not. The reason is a design decision, not a bug, but
its consequence has to be stated or the metric reads as broader than it is.

## The route into the Rust filter layer

The only call into it from Python is `_native.parse`'s `check_filter`
(`_impl/parser.py:246-264`), which runs Django's two *name and arity* checks
and nothing else:

```python
def check(name, arg_count):
    func = parser.find_filter(name)
    FilterExpression.args_check(name, func, [None] * arg_count)
```

`_materialise_filter_expression` then rebuilds each `FilterExpression` with

```python
func = parser.find_filter(name)
```

— **Django's own registry function, unconditionally.** `{{ x|upper }}` and
`{{ x|pprint }}` take the same path, so an implemented Rust body and a stub
are indistinguishable at render time.

## The measurements

`template_tests` names `urlize` 88×, `date` 34×, `time` 32×, `timesince` 20×,
`timeuntil` 16×, `urlizetrunc` 11×, `slugify` 8×, `phone2numeric` 3×,
`json_script` 2×, `pprint` 0× — and all of those were stubs when the suite
went green. Under the shim, `{{ x|pprint }}`, `{{ x|urlize }}` and
`{{ x|slugify }}` render correctly with every one of them still stubbed.

Spec §5.5 says why this is intended: "**For all 1547 tests, the render entry
point is therefore the Python nodelist.** … **Decision: keep Django's
`NodeList.render` and `Node.render_annotated` verbatim, and make shadow nodes
genuinely renderable in Python.**" The render path is Python's by design, and
filters resolve inside Python's own `FilterExpression.resolve`.

## The consequence for M4

At M4 the Rust filter bodies are a **parallel implementation whose only
verification is the corpus**. The 1547 tests validate the parse (names and
arity, through `check_filter`) and the render path (Python's); they cannot
redden for a wrong body, a missing body, or a body whose corpus rows are too
thin to reach the branch a defect is in.

Two things follow, and both are already load-bearing in this milestone:

- `scripts/filter_probe.py` (§D6) is not a convenience. It is the only source
  of truth for a body's expected answers.
- The `json_dumps` defect is the proof: its only caller was a stub, so no gate
  could see it, and its doc comment read as true. **The same argument applies
  to a landed body with thin rows** — "landed" and "verified" are different
  states at M4, and only the corpus separates them.

The blind spot is transitional: it closes as the render path reaches the
filter layer, when `_native.render` stops delegating to Python's node
rendering. It is not closed today.
