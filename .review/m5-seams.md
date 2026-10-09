# M5 — the eleven tags, and the seams behind them

Forcing event: `Host::repr`. It is M5's `json_dumps`. Every `{% with %}` error row
in the corpus was ASCII *by construction* — `'b'`, `'and'`, the tag name `'with'` —
so the shell's override, which is what makes the message match Python's `repr`
for a non-printable character, could have been **deleted** with `cargo test`, the
corpus, and all 1547 tests staying green. Its only other guard is a
`debug_assert` inside `py_repr_str`, so a release build would ship the divergence
silently, exactly as M4's `json_dumps` shipped a wrong value behind a doc comment
that read as true.

The M4 pass enumerated nine methods with no caller. M5's tags *do* have callers,
so the analogous question is narrower: **for each seam an M5 tag reaches, is the
seam's behaviour observed by something that would fail if the seam were wrong?**
A seam with a caller but no *discriminating* input is in the same position as one
with no caller at all.

Derived against `.django/` @ 6.0.9, and against the tree at `e34a301` plus the two
audit changes below. Every verdict is a number, not a reading.

## The rule

**A verdict here is dated.** It is a number only for as long as the thing it
numbered holds still. M4's `timesince` "clean" expired the moment the method's
signature changed shape; the re-derivation is what turned up its doc defect. The
same applies to every row below: **the row expires when the seam, its caller, or
the input that exercises it changes.** This is a one-time audit, not a gate. It
cannot fail a build, and nothing re-runs it. Its only forward use is as a list of
places to look again when a nearby line moves — which is why each row names a
*symbol*, never a `tags/*.rs` line: Task 4's §D-editing already moved those lines
once, and a line number would have quietly pointed at the wrong thing.

## Verdicts

One row per M5 tag. "Seam" is the non-obvious machinery the tag reaches through;
a tag whose only seam is an ordinary `Parser::compile_filter` says so.

| tag | seam it reaches | verdict |
|---|---|---|
| `cycle` | `Host::alloc_slot`, through `Parser::alloc_slot` | clean — `cycle` is the sole production caller; the slot's identity is pinned by `tests/test_ref_table.py`'s cycle tests |
| `filter` | `pyunicode::split_once`; `ErrorKind::ValueError` (sole producer) | clean — the *class* is pinned by `test_a_filter_tag_with_no_argument_is_a_value_error_for_both`, the message by a corpus row |
| `firstof` | `Parser::compile_filter` | clean |
| `for` | `Parser::compile_filter` | clean |
| `if` | `Host::compile_filter` (sole production caller); `AttrValue::Tuple` (sole producer); `IfParser::condition`'s `Raw` arm | **one comment-only invariant** — see below |
| `ifchanged` | the variadic factory (`NODE_CLASSES["IfChangedNode"]`) | clean — exercised end-to-end by `tests/test_parser_parity.py` |
| `lorem` | `Parser::py_repr`, on the tag name | clean — the argument is `"lorem"`, ASCII by construction; no user input reaches it |
| `regroup` | `Parser::compile_filter`, twice | clean |
| `url` | `kwargs::match_kwarg`'s `None` arm | clean — dead from any token (`kwargs.rs`'s doc proves it from `kwarg_re`), driven directly by `{% url %}`'s unit test |
| `widthratio` | `Parser::compile_filter`, three times | clean |
| `with` | `Parser::py_repr`, three times — the tag name twice, the leftover bit once | **divergence path was unverified** — see below |

## `Host::repr` — a seam with a caller and no discriminating input

`Host::repr`'s default is `py_repr_str`, which is exact on ASCII **only**: it
escapes `char::is_control`, Rust's `Cc`, while Python's `repr` escapes the whole
`Cf` category too. A character like U+00AD (soft hyphen, `Cf`) is emitted raw by
`py_repr_str` and as `\xad` by Python. The shell's `FilterCheckHost::repr`
(`crates/template-py/src/lib.rs`) overrides it with Python's own `repr()`, which
is the correct behaviour — but before this audit **nothing observed the
difference**, because the only M5 caller that reprs *user input* is `{% with %}`'s
leftover bit (`with.rs`, the `"{tag} received an invalid token: {leftover}"`
message; the two other call sites repr the tag name, and `lorem`'s reprs
`"lorem"`).

Measured, the four call sites: `with.rs` × 3 (tag name twice, leftover once),
`lorem.rs` × 1 (tag name). Only the leftover is user-controlled.

**Remedy, seen to fail first.** A corpus row that puts a `Cf` character in the
leftover:

```python
(
    "{% with a=1 foo­bar %}{% endwith %}",
    "'with' received an invalid token: 'foo\\xadbar'",
)
```

With the override replaced by `format!("'{}'", s)`, the row failed exactly as
predicted — ours emitted the soft hyphen raw, so the message read
`'foo<U+00AD>bar'` against Django's `'foo\xadbar'`. Restored, it is green in both
lexer modes. This is the M5 instance of M4's rule: **a seam's doc comment is not
its gate; only an input that discriminates is.**

## `IfParser::condition` — an `unreachable!` that only a comment enforced

`IfParser::condition`'s `Raw` arm is `unreachable!("IfParser::template compiles
every literal it builds")`. The invariant it names is real — `IfParser::template`
is the only constructor `do_if` uses, and it compiles every literal — but a
comment cannot enforce it, and `IfParser::new` is `pub` on the same type. A second
production caller that built the base parser's literals would compile fine and
then **panic at run time**, in a template, on the `Raw` arm.

**Remedy, seen to fail first.** A `#[should_panic]` test that builds the base
parser directly and drives `condition` over its literal:

```rust
#[test]
#[should_panic(expected = "IfParser::template compiles every literal it builds")]
fn condition_refuses_a_literal_the_base_parser_built() { … }
```

Turning the `unreachable!` into a fallback made the test fail with `test did not
panic as expected`; restored, it passes. The `Raw` arm is now the *executed* half
of the invariant rather than a claim about it.

## The two clean-looking arms this pass did **not** clear by reading

- **`AttrValue::Tuple`.** Sole producer is `if.rs` (the `IfCondition` wire form);
  sole consumer is the materialiser. Both halves are exercised by
  `tests/test_ref_table.py`'s `IfConditionRefTests`, which asserts the *identity*
  of the condition object against the refs table, not merely its type.
- **`Host::compile_filter`.** One production path —
  `Parser::compile_condition_filter` → `if.rs`'s `HostCompiler` — and the corpus
  rows `{% if a|does_not_exist %}` / `{% if 1>2 %}` are what prove `None` is not a
  permissive default here (unlike `check_filter`): a missing argument raises
  `ErrorKind::Internal`, which the FFI maps to `ParseError`, and the rows assert
  the resulting message.

## What this pass did not cover

An audit of *seams* is not an audit of *behaviour*. Nothing here says the eleven
tags render correctly for every input — that is the corpus's and Django's own
suite's job. The rows above say only: for the machinery each tag reaches through,
is there an input that would fail if that machinery were wrong. For `with` and
`if`, before this pass, the answer was no.
