# M1: Rust Lexer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reimplement Django's template lexer (`Lexer`, `DebugLexer`, `Token`, `TokenType`) in Rust, exposed as `_native.lex`, reproducing Django's token stream exactly — types, contents, line numbers, and character positions.

**Architecture:** A pure-Rust `lexer` module in `crates/template-core` with no Python dependency, plus a thin `#[pyfunction]` in `crates/template-py`. Correctness is established by a **parity test** that runs Django's real lexer and ours over the same corpus and compares tuple-for-tuple — this is stronger than the differential render harness, which cannot see tokens.

**Tech Stack:** Rust 2021, `regex` (for exact `tag_re` semantics), PyO3 0.29, maturin, Python 3.14, Django 6.0.9.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md`

## Global Constraints

- Django pinned to **6.0.9**, checkout at `.django/` (gitignored, **never modified**).
- `crates/template-core` has **no PyO3 dependency**. All Python interaction lives in `crates/template-py`.
- Python 3.14; Rust stable.
- `scripts/check.sh` must pass before every commit (cargo test, fmt, clippy `-D warnings`, Python unit tests, differential, Django suite).
- **`cargo test` does not run clippy.** A task whose code is only `cargo test`ed can still fail `check.sh` under `-D warnings`. Every task's verification step runs clippy explicitly. (Found the hard way: this plan's own Task 3 code failed on `module_inception` and `type_complexity`.)
- `PIP_CONFIG_FILE=/dev/null` is required for pip — the machine's global pip config is malformed. `scripts/env.sh` handles this.
- Reference source of truth for behaviour: `.django/django/template/base.py` and `.django/tests/template_tests/test_base.py`.
- **Indented code in a Rust doc comment is a doctest**, and `cargo test` compiles and runs it. Never put non-Rust code in an indented rustdoc block — it fails the build. Use an inline code span instead. (A ``` fence inside a rustdoc comment avoids the doctest but breaks the Markdown of any document that quotes the file, this plan included.)

## Review Focus

Each line names a failure mode the design implies but that no task's happy-path test would catch. Each has a test in the task that owns the code.

1. **Tags spanning newlines.** `tag_re` is `re.compile(r"({%.*?%}|{{.*?}}|{#.*?#})")` — no `DOTALL`, so `.` does not match `\n`. `{%\nif x %}` is **text**, not a block. A scanner that uses `(?s)` or hand-rolls "find `{%` then find `%}`" gets this wrong. (Task 2)
2. **Re-scanning after a failed opener.** In `{{% a %}` the leftmost opener is `{{` but there is no closing `}}`, so Python's regex engine advances and matches `{% a %}` starting one character later — leaving `{` as text. A scanner that commits to the first opener and gives up produces different tokenisation. (Task 2)
3. **Character indices, not bytes.** Python positions and slices are character indices. Rust `regex` reports byte offsets. `"héllo{{x}}"` has different byte and character positions; using byte offsets silently misreports every position after the first non-ASCII character. (Task 2)
4. **Verbatim state.** Inside `{% verbatim %}`, `{% endverbatim %}` is a BLOCK token, `{% other %}` is TEXT, and `{{ x }}` is TEXT *including its delimiters*. Ending requires exact content equality, not prefix matching. (Task 3)
5. **`lineno` accumulates over segments including empty ones.** The `in_tag` toggle flips on **every** split segment, including empty strings from adjacent tags, while `lineno` only advances on non-empty ones. Getting the toggle wrong desynchronises every subsequent token. (Task 3)
6. **The corpus and the implementation share an author.** 47 hand-picked cases test only the divergences I thought of. A divergence neither author imagined passes silently. This is the class that cannot be closed by adding cases, only by generating them. (Task 5's seeded randomised pass)
7. **Verbatim crossed with line numbers.** Items 4 and 5 are each covered alone; no case has a newline *inside* verbatim, so a bug in `lineno` accounting while `verbatim` is set is invisible. (Task 3, Task 5)
8. **`TokenType`'s name must survive the FFI.** `_native.lex` returns an int, but `Token.__repr__` renders `token_type.name.capitalize()` → `"Block"`, and that repr is asserted in `test_parser.py:36` and `i18n/test_blocktranslate.py:881`. The int is the right wire format; the name mapping is part of M1's contract and belongs in a test. (Task 5)
9. **`debug_assert_eq!` is not Django's `assert`.** Django's assert is live unless run under `python -O`; `debug_assert!` silently compiles out under `--release`, which is how the extension is built. If the branch is worth asserting, assert it in release. (Task 3)
10. **Lone surrogates.** A Python `str` may legally hold `"\ud800"`; a Rust `&str` cannot, so PyO3 rejects the argument where Django's lexer handles it. Not expected in the suite, but a known FFI-boundary difference worth recording so it is recognised rather than rediscovered. (Task 5)
11. **`segments()`' alternation is an unchecked invariant.** The entire `in_tag` toggle assumes the returned vec has odd length and strictly alternates text/tag. Nothing asserts it. (Task 2)

---

## Pre-execution validation

The Rust in this plan was extracted into a throwaway crate outside the repo and
run before handoff, so the executor starts from code known to build:

- `cargo test` → **30 passed, 0 failed**, doctests clean, no warnings.
- Token-stream comparison against Django's real `Lexer`/`DebugLexer` over all
  **47 corpus entries in both modes → byte-identical output**.
- `smart_split` + `split_contents` comparison against `django.utils.text.smart_split`
  and `Token.split_contents` over **13 corpus entries plus 4000 seeded random
  cases → byte-identical output** (20,497 result lines).

**Correction, added after first execution: that validation ran `cargo test`, not
`clippy`, so it never checked the code against the gate that governs commits
here.** Executing Task 3 found two lints in this plan's own Rust —
`clippy::module_inception` (the Lexer at `lexer/lexer.rs`) and
`clippy::type_complexity` (the debug test helper's return type) — both of which
`scripts/check.sh` rejects under `-D warnings`. So the accurate claim is that
this Rust was known to *build and match*, not known to *pass*. Future plans
should lint-check extracted code, and each task below now runs clippy in its
verification step rather than relying on `cargo test` alone.

**Second correction, added after executing Task 5: that throwaway validation
covered `template-core` only.** No snippet belonging to `template-py` had ever
met the clippy gate, so the same blind spot bit again — Task 5's `lex` binding
returned an anonymous
`Vec<(u8, String, usize, Option<(usize, usize)>)>`, which is
`clippy::type_complexity`. The snippet below is corrected. The generalisation is
that "known to build and match" says nothing about a crate the validation never
compiled, so a plan's snippets are only as checked as the crate they were
extracted from.

The first run of the token comparison failed, and the failure was worth the
exercise: an indented block in the `segments.rs` doc comment had been written as
a Python snippet, which rustdoc compiles as a doctest and runs. `cargo test`
therefore failed on a file whose unit tests all passed. That is why Global
Constraints forbids indented non-Rust code in doc comments, and why the regex is
now quoted inline rather than shown in a block.

Note what this validation does **not** establish: it was written by the same
author as the implementation, so it shares the blind spot that Task 6's
randomised pass exists to cover. It proves the code builds and matches on the
cases I thought of; it cannot prove the corpus is complete.

The throwaway crate is at `/tmp/m1plan/` and `/tmp/m1plan2/` and is not part of
the repository.

---

## M9 obligations recorded while they are cheap

The Rust layer deliberately does not reproduce some Python-visible details. Each
of these is fine in Rust and mandatory in the shell, and each is pinned by a test
that will fail confusingly if rediscovered at M9 rather than here.

1. **Two distinct classes, literally named.** `Lexer.__repr__` interpolates
   `self.__class__.__qualname__`, and `test_parser.py:46-50` asserts
   `'<Lexer template_string="{% for i in 1 %}{{ a...", verbatim=False>'`. The
   shell needs classes named exactly `Lexer` and `DebugLexer`, both constructible
   with one argument, with different position behaviour (`test_base.py:34-45`).
2. **`verbatim` surfaces as `False`, not `None`.** Rust models it as
   `Option<String>`; passing that through gives `verbatim=None` and fails that
   assertion on one character. The shell must map `None` to `False`.
3. **Verbatim state persists on the instance after `tokenize()`.** `_native.lex`
   is a flat, stateless function returning a list, so the shell's `Lexer` must
   hold the post-tokenize state itself, not read it back from the call.
4. **`Token.lineno` and `Token.position` default to `None`.** Django's signature
   is `Token(token_type, contents, position=None, lineno=None)`, and
   `test_parser.py:25-27,36` constructs `Token(TokenType.BLOCK, "some text")`.
   Rust's `usize` cannot carry that; the shell's `Token` needs `Optional`.

These are recorded rather than implemented because none of them is reachable
before the shell exists, and writing them down now costs a paragraph.

---

### Task 1: Token types

**Files:**
- Create: `crates/template-core/src/token.rs`
- Modify: `crates/template-core/src/lib.rs` (add `pub mod token;`)
- Modify: `crates/template-core/Cargo.toml` (add `regex`)

**Interfaces:**
- Consumes: nothing.
- Produces: `TokenType` (`#[repr(u8)]`, `Text = 0`, `Var = 1`, `Block = 2`, `Comment = 3`), and `Token { token_type: TokenType, contents: String, lineno: usize, position: Option<(usize, usize)> }`.

- [ ] **Step 1: Add the `regex` dependency**

In `crates/template-core/Cargo.toml`, under `[dependencies]`:

```toml
[dependencies]
regex = "1"
```

- [ ] **Step 2: Write the failing test**

Create `crates/template-core/src/token.rs`:

```rust
//! Token types, mirroring `django.template.base.TokenType` and `Token`.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum TokenType {
    Text = 0,
    Var = 1,
    Block = 2,
    Comment = 3,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Token {
    pub token_type: TokenType,
    pub contents: String,
    pub lineno: usize,
    /// Character indices into the source, or `None` for the non-debug lexer.
    pub position: Option<(usize, usize)>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_type_discriminants_match_djangos_enum() {
        // django.template.base.TokenType is TEXT=0, VAR=1, BLOCK=2, COMMENT=3.
        // These values cross the FFI boundary, so they are part of the contract.
        assert_eq!(TokenType::Text as u8, 0);
        assert_eq!(TokenType::Var as u8, 1);
        assert_eq!(TokenType::Block as u8, 2);
        assert_eq!(TokenType::Comment as u8, 3);
    }

    #[test]
    fn tokens_compare_by_value() {
        let a = Token {
            token_type: TokenType::Text,
            contents: "hi".to_string(),
            lineno: 1,
            position: None,
        };
        assert_eq!(a.clone(), a);
    }
}
```

Add to `crates/template-core/src/lib.rs`:

```rust
pub mod token;
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cargo test -p template-core token`
Expected: PASS (2 tests). This task is types only; the discriminants are the assertion.

- [ ] **Step 4: Commit**

```bash
git add crates/template-core/Cargo.toml crates/template-core/src/lib.rs crates/template-core/src/token.rs Cargo.lock
git commit -m "M1: add Token and TokenType"
```

---

### Task 2: Segment scanner

This is the `tag_re` equivalent. Everything downstream depends on it being byte-exact with Python.

**Files:**
- Create: `crates/template-core/src/lexer/mod.rs`
- Create: `crates/template-core/src/lexer/segments.rs`
- Modify: `crates/template-core/src/lib.rs` (replace `pub mod lexer;` stub with `pub mod lexer;` pointing at the new directory)

**Interfaces:**
- Consumes: nothing.
- Produces: `Segment<'a> { text: &'a str, span: (usize, usize) }` and `segments(source: &str) -> Vec<Segment<'_>>`. The returned vec strictly alternates text, tag, text, tag, …, text, so **odd indices are tags**.

- [ ] **Step 1: Delete the placeholder module**

`crates/template-core/src/lexer.rs` currently holds a placeholder. Delete it; `src/lexer/mod.rs` replaces it.

```bash
git rm crates/template-core/src/lexer.rs
```

- [ ] **Step 2: Write the failing test**

Create `crates/template-core/src/lexer/segments.rs`:

```rust
//! Splitting a template into alternating text and tag segments.
//!
//! This reproduces `tag_re` from `django/template/base.py`, which is
//! `re.compile(r"({%.*?%}|{{.*?}}|{#.*?#})")`.
//!
//! Two properties of that regex are load-bearing and easy to lose:
//!
//! 1. There is no `DOTALL`, so `.` never matches a newline. A tag may not span
//!    lines.
//! 2. If the leftmost opener has no matching closer, Python's engine advances
//!    one character and tries again. In `{{% a %}` the `{{` at index 0 never
//!    closes, so the match is `{% a %}` from index 1 and the leading `{` is text.
//!    Hand-rolling "find an opener, then find its closer" gets this wrong.
//!
//! Using the `regex` crate rather than scanning by hand is deliberate: it has
//! the same leftmost-first alternation semantics as Python, so property 2 comes
//! for free.

use std::sync::OnceLock;

use regex::Regex;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Segment<'a> {
    pub text: &'a str,
    /// Character indices, matching Python's `re` spans.
    pub span: (usize, usize),
}

fn tag_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\})").expect("tag_re is a valid pattern")
    })
}

/// Maps byte offsets to character indices, matching Python's indexing.
struct CharIndex {
    /// Byte offset at which each character starts.
    starts: Vec<usize>,
}

impl CharIndex {
    fn new(source: &str) -> Self {
        Self {
            starts: source.char_indices().map(|(byte, _)| byte).collect(),
        }
    }

    /// Number of characters before this byte offset.
    fn chars_before(&self, byte: usize) -> usize {
        self.starts.partition_point(|&start| start < byte)
    }
}

/// Split `source` into alternating text and tag segments.
///
/// Equivalent to Python's `tag_re.split(source)` with a capturing group: the
/// result always begins and ends with a text segment, either of which may be
/// empty, and tags occupy the odd indices.
pub fn segments(source: &str) -> Vec<Segment<'_>> {
    let index = CharIndex::new(source);
    let total_chars = index.starts.len();

    let mut out = Vec::new();
    let mut byte_cursor = 0usize;

    for m in tag_re().find_iter(source) {
        let (start, end) = (m.start(), m.end());

        out.push(Segment {
            text: &source[byte_cursor..start],
            span: (index.chars_before(byte_cursor), index.chars_before(start)),
        });
        out.push(Segment {
            text: m.as_str(),
            span: (index.chars_before(start), index.chars_before(end)),
        });

        byte_cursor = end;
    }

    out.push(Segment {
        text: &source[byte_cursor..],
        span: (index.chars_before(byte_cursor), total_chars),
    });

    // Load-bearing: the lexer's in_tag toggle assumes the vec starts and ends
    // with a text segment and alternates strictly, so tags are the odd indices.
    // Every push above is paired, so this holds by construction — the assert is
    // here so that a future edit to the loop cannot break it silently.
    assert_eq!(out.len() % 2, 1, "segments must alternate text/tag/text");

    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn texts(source: &str) -> Vec<&str> {
        segments(source).into_iter().map(|s| s.text).collect()
    }

    fn spans(source: &str) -> Vec<(usize, usize)> {
        segments(source).into_iter().map(|s| s.span).collect()
    }

    #[test]
    fn plain_text_is_a_single_segment() {
        assert_eq!(texts("hello"), vec!["hello"]);
        assert_eq!(spans("hello"), vec![(0, 5)]);
    }

    #[test]
    fn alternates_text_and_tags() {
        assert_eq!(texts("a{{ b }}c"), vec!["a", "{{ b }}", "c"]);
    }

    #[test]
    fn leading_tag_yields_an_empty_leading_text_segment() {
        assert_eq!(texts("{{ b }}c"), vec!["", "{{ b }}", "c"]);
    }

    #[test]
    fn adjacent_tags_yield_an_empty_segment_between_them() {
        assert_eq!(texts("{{ a }}{{ b }}"), vec!["", "{{ a }}", "", "{{ b }}", ""]);
    }

    #[test]
    fn all_three_tag_kinds_are_recognised() {
        assert_eq!(
            texts("{% a %}{{ b }}{# c #}"),
            vec!["", "{% a %}", "", "{{ b }}", "", "{# c #}", ""],
        );
    }

    #[test]
    fn tags_do_not_span_newlines() {
        // No DOTALL: the `.` in `.*?` cannot match \n, so this is all text.
        assert_eq!(texts("{%\nif x %}"), vec!["{%\nif x %}"]);
        assert_eq!(texts("{{ a\n}}"), vec!["{{ a\n}}"]);
    }

    #[test]
    fn unmatched_opener_is_retried_one_character_later() {
        // The `{{` at 0 never closes, so the match is `{% a %}` from index 1.
        assert_eq!(texts("{{% a %}"), vec!["{", "{% a %}", ""]);
        assert_eq!(spans("{{% a %}"), vec![(0, 1), (1, 8), (8, 8)]);
    }

    #[test]
    fn comment_wins_over_an_opener_inside_it() {
        // A `{%` inside a comment is not a tag: the comment matches first.
        assert_eq!(texts("{# {% #}"), vec!["", "{# {% #}", ""]);
    }

    #[test]
    fn spans_are_character_indices_not_byte_offsets() {
        // "héllo" is 5 characters but 6 bytes. A byte-offset implementation
        // reports the tag starting at 6, not 5.
        let source = "héllo{{ x }}";
        assert_eq!(spans(source), vec![(0, 5), (5, 12), (12, 12)]);
    }

    #[test]
    fn segments_always_alternate_text_and_tag() {
        // The lexer's in_tag toggle depends on this shape: odd length, text at
        // even indices, tags at odd indices. Assert it directly rather than
        // leaving it as an assumption the toggle happens to rely on.
        const SOURCES: &[&str] = &[
            "", "a", "{{ a }}", "{{% a %}", "{{ a }}{{ b }}", "{% a %}{# b #}",
            "a{% b %}c", "{%\nif x %}", "héllo{{ x }}", "{#}{#}", "{{ {% }}",
            "{% verbatim %}{{ x }}{% endverbatim %}",
        ];
        for source in SOURCES {
            let segs = segments(source);
            assert_eq!(segs.len() % 2, 1, "odd length for {source:?}");
            for (i, seg) in segs.iter().enumerate() {
                let is_tag = seg.text.starts_with("{%")
                    || seg.text.starts_with("{{")
                    || seg.text.starts_with("{#");
                // Even indices are text, odd indices are tags. An even-index
                // segment may still *look* like a tag if it is plain text that
                // merely begins with those characters, so only assert the odd
                // direction, which is the one the toggle relies on.
                if i % 2 == 1 {
                    assert!(is_tag, "odd index {i} is not a tag in {source:?}");
                }
            }
        }
    }

    #[test]
    fn segments_reconstruct_the_source() {
        let source = "a{{ b }}c{% d %}{# e #}f";
        let rebuilt: String = segments(source).into_iter().map(|s| s.text).collect();
        assert_eq!(rebuilt, source);
    }
}
```

Create `crates/template-core/src/lexer/mod.rs`:

```rust
//! Lexing, following `django.template.base.Lexer` and `DebugLexer`.

pub mod segments;

pub use segments::{segments, Segment};
```

Ensure `crates/template-core/src/lib.rs` has:

```rust
pub mod lexer;
```

(It already does; the module now resolves to `lexer/mod.rs`.)

- [ ] **Step 3: Run tests to verify they pass**

Run: `cargo test -p template-core segments`
Expected: PASS (11 tests).

If `unmatched_opener_is_retried_one_character_later` fails, the `regex` crate's
alternation is behaving differently than assumed — stop and investigate before
continuing, because every downstream task depends on this.

- [ ] **Step 4: Commit**

```bash
git add -A crates/template-core/src/lexer crates/template-core/src/lib.rs
git commit -m "M1: segment scanner matching Django's tag_re"
```

---

### Task 3: Lexer

**Files:**
- Rewrite: `crates/template-core/src/lexer/mod.rs` — currently only declares and
  re-exports `segments`; the `Lexer` belongs here beside it, not in a nested
  `lexer/lexer.rs`, which is `clippy::module_inception` and fails `check.sh`.

**Interfaces:**
- Consumes: `segments::segments`, `Segment`, `token::{Token, TokenType}`.
- Produces:
  - `Lexer::new(source: &str) -> Lexer<'_>` — non-debug, positions are `None`.
  - `Lexer::debug(source: &str) -> Lexer<'_>` — positions are `Some`.
  - `Lexer::tokenize(&mut self) -> Vec<Token>`.
  - `Lexer::verbatim(&self) -> Option<&str>` — exposed for tests.

- [ ] **Step 1: Write the failing test**

Create `crates/template-core/src/lexer/mod.rs` — the module declarations, the
re-exports, and then the `Lexer` itself:

```rust
//! The lexer, following `django.template.base.Lexer` and `DebugLexer`.
//!
//! `Lexer` and `DebugLexer` differ only in whether token positions are
//! recorded, so one implementation with a `debug` flag serves both. Django
//! keeps them as separate classes for speed; that distinction is **not worth
//! reproducing in Rust**, but it is **mandatory in the Python shell at M9**,
//! which must expose both as distinct classes. See "M9 obligations" in the
//! plan for the three things the shell must reconstruct.

pub mod segments;

// Also a `use`, so `segments` and `Segment` are in scope below.
pub use segments::{segments, Segment};

use crate::token::{Token, TokenType};
```

…followed by the `Lexer` implementation. The test helper's return types are
named rather than spelled out inline — `Vec<(TokenType, String, usize, Option<(usize, usize)>)>`
is `clippy::type_complexity`:

```rust
    /// One token's observable fields, for compact assertions.
    type Lexed = (TokenType, String, usize);
    type LexedDebug = (TokenType, String, usize, Option<(usize, usize)>);
```

`crates/template-core/src/lib.rs` already has `pub mod lexer;`, which now
resolves to this directory. No entry for `Lexer` is needed there.

```rust
//! The lexer, following `django.template.base.Lexer` and `DebugLexer`.
//!
//! `Lexer` and `DebugLexer` differ only in whether token positions are
//! recorded, so one implementation with a `debug` flag serves both. Django
//! keeps them as separate classes for speed; that distinction is **not worth
//! reproducing in Rust**, but it is **mandatory in the Python shell at M9**,
//! which must expose both as distinct classes. See "M9 obligations" in the
//! plan for the three things the shell must reconstruct.

use crate::lexer::segments::{segments, Segment};
use crate::token::{Token, TokenType};

// Template syntax constants, from django/template/base.py.
const BLOCK_TAG_START: &str = "{%";
const VARIABLE_TAG_START: &str = "{{";
const COMMENT_TAG_START: &str = "{#";

pub struct Lexer<'a> {
    source: &'a str,
    /// Set while inside a `{% verbatim %}` block; holds the exact contents the
    /// closing tag must have, e.g. `"endverbatim"`.
    verbatim: Option<String>,
    debug: bool,
}

impl<'a> Lexer<'a> {
    pub fn new(source: &'a str) -> Self {
        Self {
            source,
            verbatim: None,
            debug: false,
        }
    }

    pub fn debug(source: &'a str) -> Self {
        Self {
            source,
            verbatim: None,
            debug: true,
        }
    }

    /// Verbatim state, exposed so tests can assert it is left clean.
    pub fn verbatim(&self) -> Option<&str> {
        self.verbatim.as_deref()
    }

    pub fn tokenize(&mut self) -> Vec<Token> {
        let mut out = Vec::new();
        let mut in_tag = false;
        let mut lineno = 1usize;

        for segment in segments(self.source) {
            if !segment.text.is_empty() {
                out.push(self.create_token(&segment, lineno, in_tag));
                lineno += segment.text.matches('\n').count();
            }
            // The toggle happens for every segment, including empty ones —
            // that is what keeps adjacent tags aligned.
            in_tag = !in_tag;
        }

        out
    }

    fn create_token(&mut self, segment: &Segment<'_>, lineno: usize, in_tag: bool) -> Token {
        let position = if self.debug { Some(segment.span) } else { None };
        let text = segment.text;

        if in_tag {
            let token_start = &text[..2];

            if token_start == BLOCK_TAG_START {
                // `[2..len - 2]` strips `{%` and `%}`. Safe on byte indices
                // because both delimiters are ASCII, so both edges are
                // character boundaries.
                let content = text[2..text.len() - 2].trim();

                if let Some(expected) = self.verbatim.clone() {
                    if content != expected {
                        return Token {
                            token_type: TokenType::Text,
                            contents: text.to_string(),
                            lineno,
                            position,
                        };
                    }
                    self.verbatim = None;
                } else if is_verbatim_start(content) {
                    self.verbatim = Some(format!("end{content}"));
                }

                return Token {
                    token_type: TokenType::Block,
                    contents: content.to_string(),
                    lineno,
                    position,
                };
            }

            if self.verbatim.is_none() {
                let content = text[2..text.len() - 2].trim().to_string();
                let token_type = if token_start == VARIABLE_TAG_START {
                    TokenType::Var
                } else {
                    // Django writes this as a bare `assert`, which is live unless
                    // the interpreter runs under `-O`. `debug_assert!` would
                    // compile out under `--release`, which is how this extension
                    // is actually built, so a real assert is the faithful port.
                    assert_eq!(token_start, COMMENT_TAG_START);
                    TokenType::Comment
                };
                return Token {
                    token_type,
                    contents: content,
                    lineno,
                    position,
                };
            }
        }

        Token {
            token_type: TokenType::Text,
            contents: text.to_string(),
            lineno,
            position,
        }
    }
}

/// Whether a block's contents open a verbatim block.
///
/// Django tests `content[:9] in ("verbatim", "verbatim ")`, i.e. the contents
/// are exactly `verbatim` or begin with `verbatim `.
fn is_verbatim_start(content: &str) -> bool {
    match content.strip_prefix("verbatim") {
        Some(rest) => rest.is_empty() || rest.starts_with(' '),
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lex(source: &str) -> Vec<(TokenType, String, usize)> {
        Lexer::new(source)
            .tokenize()
            .into_iter()
            .map(|t| (t.token_type, t.contents, t.lineno))
            .collect()
    }

    fn lex_debug(source: &str) -> Vec<(TokenType, String, usize, Option<(usize, usize)>)> {
        Lexer::debug(source)
            .tokenize()
            .into_iter()
            .map(|t| (t.token_type, t.contents, t.lineno, t.position))
            .collect()
    }

    #[test]
    fn plain_text() {
        assert_eq!(lex("hello"), vec![(TokenType::Text, "hello".to_string(), 1)]);
    }

    #[test]
    fn variable_trims_whitespace() {
        assert_eq!(
            lex("{{   spaced   }}"),
            vec![(TokenType::Var, "spaced".to_string(), 1)],
        );
    }

    #[test]
    fn block_and_comment() {
        assert_eq!(
            lex("{% if x %}{# note #}"),
            vec![
                (TokenType::Block, "if x".to_string(), 1),
                (TokenType::Comment, "note".to_string(), 1),
            ],
        );
    }

    #[test]
    fn lineno_counts_newlines_in_text() {
        assert_eq!(
            lex("a\nb\n{{ x }}"),
            vec![
                (TokenType::Text, "a\nb\n".to_string(), 1),
                (TokenType::Var, "x".to_string(), 3),
            ],
        );
    }

    #[test]
    fn lineno_counts_newlines_inside_a_text_run_only() {
        // Two newlines in the leading text; the tag itself may not span lines.
        assert_eq!(
            lex("\n\n{% if x %}\n{{ y }}"),
            vec![
                (TokenType::Text, "\n\n".to_string(), 1),
                (TokenType::Block, "if x".to_string(), 3),
                (TokenType::Text, "\n".to_string(), 3),
                (TokenType::Var, "y".to_string(), 4),
            ],
        );
    }

    #[test]
    fn non_debug_lexer_records_no_positions() {
        assert!(Lexer::new("{{ x }}").tokenize().iter().all(|t| t.position.is_none()));
    }

    #[test]
    fn debug_lexer_records_positions() {
        assert_eq!(
            lex_debug("a{{ x }}b"),
            vec![
                (TokenType::Text, "a".to_string(), 1, Some((0, 1))),
                (TokenType::Var, "x".to_string(), 1, Some((1, 8))),
                (TokenType::Text, "b".to_string(), 1, Some((8, 9))),
            ],
        );
    }

    #[test]
    fn empty_segments_still_toggle_tag_state() {
        // Adjacent tags produce empty text segments between them, and the
        // in_tag toggle must flip on those too. A lexer that toggles only on
        // non-empty segments reads `{% a %}` here as text.
        assert_eq!(
            lex("{{ a }}{% b %}{# c #}"),
            vec![
                (TokenType::Var, "a".to_string(), 1),
                (TokenType::Block, "b".to_string(), 1),
                (TokenType::Comment, "c".to_string(), 1),
            ],
        );
    }

    #[test]
    fn verbatim_contents_are_text_including_delimiters() {
        assert_eq!(
            lex("{% verbatim %}{{ x }}{% endverbatim %}"),
            vec![
                (TokenType::Block, "verbatim".to_string(), 1),
                (TokenType::Text, "{{ x }}".to_string(), 1),
                (TokenType::Block, "endverbatim".to_string(), 1),
            ],
        );
    }

    #[test]
    fn verbatim_makes_other_blocks_text() {
        assert_eq!(
            lex("{% verbatim %}{% if x %}{% endverbatim %}"),
            vec![
                (TokenType::Block, "verbatim".to_string(), 1),
                (TokenType::Text, "{% if x %}".to_string(), 1),
                (TokenType::Block, "endverbatim".to_string(), 1),
            ],
        );
    }

    #[test]
    fn verbatim_named_block_requires_exact_match() {
        assert_eq!(
            lex("{% verbatim foo %}{% endverbatim bar %}{% endverbatim foo %}"),
            vec![
                (TokenType::Block, "verbatim foo".to_string(), 1),
                (TokenType::Text, "{% endverbatim bar %}".to_string(), 1),
                (TokenType::Block, "endverbatim foo".to_string(), 1),
            ],
        );
    }

    #[test]
    fn verbatim_state_is_cleared() {
        let mut lexer = Lexer::new("{% verbatim %}{% endverbatim %}");
        lexer.tokenize();
        assert_eq!(lexer.verbatim(), None);
    }

    #[test]
    fn verbatim_prefix_that_is_not_a_verbatim_tag() {
        // `verbatimx` must not open a verbatim block.
        assert_eq!(
            lex("{% verbatimx %}"),
            vec![(TokenType::Block, "verbatimx".to_string(), 1)],
        );
    }

    #[test]
    fn unterminated_verbatim_leaves_state_set() {
        let mut lexer = Lexer::new("{% verbatim %}never closed");
        let tokens = lexer.tokenize();
        assert_eq!(tokens.len(), 2);
        assert_eq!(lexer.verbatim(), Some("endverbatim"));
    }
}
```

- [ ] **Step 2: Run tests and the lint gate**

Run: `cargo test -p template-core lexer::`
Expected: PASS (14 lexer tests).

Run: `cargo clippy -p template-core --all-targets -- -D warnings`
Expected: clean. `cargo test` does **not** run clippy, so a task can pass Step 2
and still fail `check.sh` — which is what happened when this plan was first
executed. Both lints it caught were in the plan's own code.

- [ ] **Step 3: Commit**

```bash
git add crates/template-core/src/lexer
git commit -m "M1: Rust lexer with verbatim handling and debug positions"
```

---

### Task 4: `Token::split_contents` and `smart_split`

M1's §10.3 row names `Token`, and `split_contents` is a `Token` method that Django's
`test_parser.py:24-33` tests directly. It is in scope, so it is planned rather than
silently dropped.

`smart_split` itself lives in `django/utils/text.py`, **outside** the namespace our
finder owns. It is therefore still live in Django for other callers, and the Rust
implementation must match it exactly rather than inherit it.

**Files:**
- Create: `crates/template-core/src/text.rs`
- Modify: `crates/template-core/src/lib.rs` (add `pub mod text;`)
- Modify: `crates/template-core/src/token.rs` (add `split_contents`)
- Modify: `crates/template-py/src/lib.rs` (expose `_native.split_contents`)

**Interfaces:**
- Consumes: `token::Token`.
- Produces: `text::smart_split(&str) -> Vec<&str>`; `text::SplitError`;
  `Token::split_contents(&self) -> Result<Vec<String>, SplitError>`; and
  `_native.split_contents(contents: str) -> list[str]`, raising `StopIteration` on
  `SplitError`.

- [ ] **Step 1: Read the specification**

`smart_split`'s regex is the specification and is reproduced from
`django/utils/text.py:409`:

```python
smart_split_re = _lazy_re_compile(
    r"""
    ((?:
        [^\s'"]*
        (?:
            (?:"(?:[^"\\]|\\.)*" | '(?:[^'\\]|\\.)*')
            [^\s'"]*
        )+
    ) | \S+)
""",
    re.VERBOSE,
)
```

Compacted out of `VERBOSE` form it is one line, and it is used with
`finditer(...)` — the result is the non-overlapping matches in order, not a
capture-group split.

- [ ] **Step 2: Write the failing test**

Create `crates/template-core/src/text.rs`:

```rust
//! Text utilities ported from `django/utils/text.py`.
//!
//! These live outside `django.template`, so shadowing the template namespace
//! does not replace them — Django's originals stay live for other callers. The
//! Rust versions must therefore match them exactly rather than inherit them.

use std::sync::OnceLock;

use regex::Regex;

/// Splits a string on whitespace, keeping quoted phrases together, where the
/// quotes are kept in the output.
///
/// Mirrors `smart_split` (`django/utils/text.py:423`), which is
/// `smart_split_re.finditer(str(text))`.
pub fn smart_split(text: &str) -> Vec<&str> {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| {
        Regex::new(
            r#"((?:[^\s'"]*(?:(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')[^\s'"]*)+)|\S+)"#,
        )
        .expect("smart_split_re is a valid pattern")
    });
    re.find_iter(text).map(|m| m.as_str()).collect()
}

/// Raised where Django's `Token.split_contents` lets `next(bits)` raise
/// `StopIteration` — an unterminated `_("...` marker at the end of the token.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SplitError;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_on_whitespace() {
        assert_eq!(smart_split("a b c"), vec!["a", "b", "c"]);
        assert_eq!(smart_split("  leading"), vec!["leading"]);
        assert_eq!(smart_split("trailing  "), vec!["trailing"]);
    }

    #[test]
    fn keeps_quoted_phrases_together_and_keeps_the_quotes() {
        assert_eq!(smart_split(r#"a "b c" d"#), vec!["a", "\"b c\"", "d"]);
        assert_eq!(smart_split("a 'b c' d"), vec!["a", "'b c'", "d"]);
    }

    #[test]
    fn escaped_quotes_do_not_close_the_phrase() {
        // The `\\.` alternative means a backslash-escaped quote is consumed
        // rather than ending the quoted run.
        assert_eq!(smart_split(r#"a "b\"c" d"#), vec!["a", r#""b\"c""#, "d"]);
    }

    #[test]
    fn empty_and_whitespace_only_inputs_yield_nothing() {
        assert!(smart_split("").is_empty());
        assert!(smart_split("   ").is_empty());
    }

    #[test]
    fn unquoted_quotes_are_kept_in_the_word() {
        // `a"b` has no closing quote, so the quoted alternative cannot match and
        // `\S+` takes the whole run.
        assert_eq!(smart_split(r#"a"b"#), vec![r#"a"b"#]);
        assert_eq!(smart_split("a'b"), vec!["a'b"]);
    }
}
```

Add to `crates/template-core/src/lib.rs`:

```rust
pub mod text;
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cargo test -p template-core text::`
Expected: PASS (5 tests).

- [ ] **Step 4: Write the failing test for `split_contents`**

Add to `crates/template-core/src/token.rs`, inside the existing `tests` module:

```rust
    use crate::text::SplitError;

    #[test]
    fn split_contents_keeps_translation_markers_whole() {
        // From tests/template_tests/test_parser.py:24-33.
        let token = Token {
            token_type: TokenType::Block,
            contents: r#"sometag _("Page not found") value|yesno:_("yes,no")"#.to_string(),
            lineno: 1,
            position: None,
        };
        assert_eq!(
            token.split_contents().unwrap(),
            vec![
                "sometag".to_string(),
                r#"_("Page not found")"#.to_string(),
                r#"value|yesno:_("yes,no")"#.to_string(),
            ],
        );
    }

    #[test]
    fn split_contents_plain_token() {
        let token = Token {
            token_type: TokenType::Block,
            contents: "if x > 1".to_string(),
            lineno: 1,
            position: None,
        };
        assert_eq!(
            token.split_contents().unwrap(),
            vec!["if".to_string(), "x".to_string(), ">".to_string(), "1".to_string()],
        );
    }

    #[test]
    fn split_contents_reports_an_unterminated_translation_marker() {
        // Django reaches `next(bits)` on an exhausted iterator here, which
        // raises StopIteration out of `split_contents`. Rust cannot, so the
        // condition is explicit and the shell raises StopIteration for parity.
        let token = Token {
            token_type: TokenType::Block,
            contents: r#"tag _("unterminated"#.to_string(),
            lineno: 1,
            position: None,
        };
        assert_eq!(token.split_contents(), Err(SplitError));
    }
```

- [ ] **Step 5: Implement `split_contents`**

Add to `crates/template-core/src/token.rs`:

```rust
use crate::text::{smart_split, SplitError};

impl Token {
    /// Split the token's contents into arguments, keeping quoted phrases and
    /// translation-marked `_("...")` runs together.
    ///
    /// Ports `Token.split_contents` (`django/template/base.py:388-400`). The
    /// inner `while` loop is where Django calls `next(bits)` on an exhausted
    /// generator; that surfaces here as `Err(SplitError)` rather than a panic.
    pub fn split_contents(&self) -> Result<Vec<String>, SplitError> {
        let bits = smart_split(&self.contents);
        let mut split = Vec::with_capacity(bits.len());
        let mut i = 0;

        while i < bits.len() {
            let mut bit = bits[i].to_string();

            if bit.starts_with("_(\"") || bit.starts_with("_('") {
                // `bit[2]` is the quote character, so byte 2 is a char boundary.
                let sentinel = format!("{})", &bit[2..3]);
                let mut trans_bit = vec![bit.clone()];

                while !bit.ends_with(&sentinel) {
                    i += 1;
                    if i >= bits.len() {
                        return Err(SplitError);
                    }
                    bit = bits[i].to_string();
                    trans_bit.push(bit.clone());
                }

                bit = trans_bit.join(" ");
            }

            split.push(bit);
            i += 1;
        }

        Ok(split)
    }
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cargo test -p template-core`
Expected: PASS (all tests, including the three new ones).

- [ ] **Step 7: Expose `_native.split_contents`**

In `crates/template-py/src/lib.rs`:

```rust
use template_core::token::{Token, TokenType};
use pyo3::exceptions::PyStopIteration;

/// Split template-tag contents into arguments.
///
/// Raises `StopIteration` where Django's implementation would, so the shell's
/// `Token.split_contents` has identical error behaviour.
#[pyfunction]
fn split_contents(py: Python<'_>, contents: &str) -> PyResult<Vec<String>> {
    let token = Token {
        token_type: TokenType::Block,
        contents: contents.to_string(),
        lineno: 1,
        position: None,
    };

    token
        .split_contents()
        .map_err(|_| PyStopIteration::new_err("unterminated translation marker"))
}
```

Register it in the module alongside `lex`:

```rust
    m.add_function(wrap_pyfunction!(split_contents, m)?)?;
```

- [ ] **Step 8: Commit**

```bash
git add crates/template-core/src/text.rs crates/template-core/src/token.rs \
        crates/template-core/src/lib.rs crates/template-py/src/lib.rs
git commit -m "M1: Token::split_contents and smart_split"
```

---

### Task 5: `_native.lex` binding

**Files:**
- Modify: `crates/template-py/Cargo.toml` (add `template-core` features if needed — none expected)
- Modify: `crates/template-py/src/lib.rs`

**Interfaces:**
- Consumes: `template_core::lexer::Lexer`, `template_core::token::TokenType`.
- Produces: `_native.lex(source: str, debug: bool = False) -> list[tuple[int, str, int, tuple[int, int] | None]]`, where the int is the `TokenType` discriminant and the position tuple holds character indices.

- [ ] **Step 1: Write the binding**

In `crates/template-py/src/lib.rs`, add:

```rust
use template_core::lexer::Lexer as CoreLexer;

// `TokenType` is already in scope from the `split_contents` binding's import.
// Adding it a second time is E0252.

/// One lexed token as it crosses into Python: token type discriminant,
/// contents, line number, and character-index span (or `None` when lexed
/// without `debug`).
///
/// Named rather than written inline because `clippy::type_complexity` rejects
/// the anonymous tuple — the same lint that caught the debug test helper in
/// `template-core`'s lexer module.
type LexedToken = (u8, String, usize, Option<(usize, usize)>);

/// Lex `source`, returning `(token_type, contents, lineno, position)` tuples.
///
/// The token type is an `int` matching `django.template.base.TokenType`'s
/// discriminants, and `position` holds **character** indices (or `None` for the
/// non-debug lexer).
#[pyfunction]
#[pyo3(signature = (source, debug = false))]
fn lex(source: &str, debug: bool) -> Vec<LexedToken> {
    let mut lexer = if debug {
        CoreLexer::debug(source)
    } else {
        CoreLexer::new(source)
    };

    lexer
        .tokenize()
        .into_iter()
        .map(|token| {
            (
                token.token_type as u8,
                token.contents,
                token.lineno,
                token.position,
            )
        })
        .collect()
}
```

Register it in the module — alongside `split_contents`, which Task 4 added, so
that the module block reads:

```rust
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(engine_version, m)?)?;
    m.add_function(wrap_pyfunction!(split_contents, m)?)?;
    m.add_function(wrap_pyfunction!(lex, m)?)?;
    Ok(())
}
```

- [ ] **Step 2: Rebuild the extension**

Run: `maturin develop`
Expected: builds and installs cleanly.

- [ ] **Step 3: Verify by hand**

Run:

```bash
.venv/bin/python -c "
from django_rust_templates import _native
for t in _native.lex('a{{ x }}b'):
    print(t)
"
```

Expected: three tuples, `(0, 'a', 1, None)`, `(1, 'x', 1, None)`, `(0, 'b', 1, None)`.

- [ ] **Step 4: Commit**

```bash
git add crates/template-py/src/lib.rs
git commit -m "M1: expose _native.lex"
```

---

### Task 6: Parity test against Django's real lexer

This is the task that actually establishes the milestone's claim. Everything before it is scaffolding for this comparison.

**Files:**
- Create: `tests/test_lexer_parity.py`
- Create: `tests/lexer_corpus.py`

**Interfaces:**
- Consumes: `_native.lex`, and Django's real `Lexer`/`DebugLexer` imported from `.django/`.
- Produces: `tests/lexer_corpus.py::CORPUS: list[str]`, reusable by later milestones.

- [ ] **Step 1: Write the corpus**

Create `tests/lexer_corpus.py`:

```python
"""Template sources used to compare our lexer against Django's.

The corpus is deliberately adversarial: each entry below the first block exists
to pin a specific behaviour that is easy to get wrong, and the comment says
which. Sources are lexed as-is; they are not required to be valid templates.
"""

CORPUS = [
    # --- the case from Django's own test_base.py ---
    "text\n{% if test %}{{ varvalue }}{% endif %}"
    "{#comment {{not a var}} {%not a block%} #}end text",
    # --- ordinary shapes ---
    "",
    "plain text",
    "{{ x }}",
    "{% if x %}y{% endif %}",
    "{# c #}",
    "a{{ b }}c{% d %}{# e #}f",
    "{{ a }}{{ b }}",
    "{% a %}{% b %}",
    # --- whitespace handling in tag contents ---
    "{{    spaced    }}",
    "{%   if x   %}",
    "{#   note   #}",
    "{{\tx\t}}",
    # --- line numbers ---
    "\n\n{{ x }}",
    "a\nb\nc",
    "{{ a }}\n{{ b }}\n{{ c }}",
    "\n{% if x %}\n{% endif %}\n",
    # --- tags must not span newlines (no DOTALL) ---
    "{%\nif x %}",
    "{{ a\n}}",
    "{# a\n#}",
    "{% if\nx %}y{% endif %}",
    # --- unmatched openers ---
    # `{{% a %}` retries one character later and finds `{% a %}`; the other two
    # have no closer anywhere and end up entirely as text.
    "{{% a %}",
    "{% a }}",
    "{# a %}",
    "{{% a %} b {{ c }}",
    "{{{{ x }}",
    # --- interleaved and nested delimiters ---
    "{# {% #}",
    "{# {{ #}",
    "{{ {% }}",
    "{% {{ %} }}",
    "{#comment {{not a var}} {%not a block%} #}",
    # --- verbatim ---
    "{% verbatim %}{{ x }}{% endverbatim %}",
    "{% verbatim %}{% if x %}{% endverbatim %}",
    "{% verbatim %}{{ x }}",
    "{% verbatim foo %}{{ x }}{% endverbatim foo %}",
    "{% verbatim foo %}{% endverbatim bar %}{% endverbatim foo %}",
    "{% verbatimx %}",
    "{% verbatim %}{% endverbatim %}{{ after }}",
    "{% verbatim %}{% verbatim %}{% endverbatim %}",
    # --- verbatim crossed with line numbers ---
    # Items 4 and 5 of Review Focus are each covered alone above; these cross
    # them, so a lineno bug that only appears while verbatim state is set shows.
    "{% verbatim %}a\nb{% endverbatim %}",
    "{% verbatim %}\n{% if x %}\n{% endverbatim %}",
    "{% verbatim %}\n\n\n{% endverbatim %}",
    "x\n{% verbatim %}\ny\n{% endverbatim %}\nz",
    # --- non-ASCII, where byte and character offsets diverge ---
    "héllo{{ x }}",
    "{{ café }}",
    "日本語{% if 文字 %}中文{% endif %}",
    "emoji 🎉{{ x }}🎉",
    # --- empty and adjacent tags ---
    "{{}}{{}}",
    "{%%}{%%}",
    "{{  }}",
    "{#}{#}",
]
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_lexer_parity.py`:

```python
"""Parity between the Rust lexer and Django's own.

This is M1's acceptance test. It runs Django's real `Lexer` and `DebugLexer`
imported straight from the pinned checkout — no shim involved — and compares
their token stream against `_native.lex` tuple for tuple.

Comparing the *debug* lexer matters most: it pins character positions, which is
where a byte-offset implementation diverges silently.
"""

import inspect
import random
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template.base import DebugLexer, Lexer, Token, TokenType  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from tests.lexer_corpus import CORPUS  # noqa: E402

# Token contents for `split_contents`, kept separate from CORPUS because these
# are tag *contents*, not whole templates.
SPLIT_CORPUS = [
    'sometag _("Page not found") value|yesno:_("yes,no")',
    "if x > 1",
    "for i in items",
    "",
    "   ",
    'a "b c" d',
    "a 'b c' d",
    r'a "b\"c" d',
    r'a"b',
    'value|default:"a b"',
    "_(\"outer _('inner') outer\")",
    'x _("a") _("b") y',
    "{% if x %}",
]

# Pinned so a randomised failure reproduces exactly.
RANDOM_SEED = 20261008
RANDOM_CASES = 3000
RANDOM_ALPHABET = "{}%# \naé🎉"
RANDOM_MAX_LEN = 40


def django_tokens(source, debug):
    lexer = DebugLexer(source) if debug else Lexer(source)
    return [
        (t.token_type.value, t.contents, t.lineno, t.position)
        for t in lexer.tokenize()
    ]


def rust_tokens(source, debug):
    return [tuple(t) for t in _native.lex(source, debug)]


class LexerParityTests(unittest.TestCase):
    def assert_matches(self, source, debug):
        expected = django_tokens(source, debug)
        actual = rust_tokens(source, debug)
        self.assertEqual(
            actual,
            expected,
            f"\nsource:   {source!r}\n"
            f"debug:    {debug}\n"
            f"django:   {expected}\n"
            f"rust:     {actual}",
        )

    def test_corpus_matches_non_debug(self):
        for source in CORPUS:
            with self.subTest(source=source):
                self.assert_matches(source, debug=False)

    def test_corpus_matches_debug(self):
        for source in CORPUS:
            with self.subTest(source=source):
                self.assert_matches(source, debug=True)

    def test_the_suite_own_lexer_case_matches(self):
        """The exact string Django's test_base.py asserts on."""
        source = (
            "text\n"
            "{% if test %}{{ varvalue }}{% endif %}"
            "{#comment {{not a var}} {%not a block%} #}"
            "end text"
        )
        self.assert_matches(source, debug=False)
        self.assert_matches(source, debug=True)

    def test_corpus_is_not_trivially_small(self):
        self.assertGreaterEqual(len(CORPUS), 40)

    def test_randomised_sources_match(self):
        """Generated sources, because the corpus shares an author with the lexer.

        CORPUS only probes divergences its author thought of; a divergence
        neither author imagined passes it silently. Generating sources from a
        small alphabet of the characters that matter — tag delimiters, percent,
        hash, newline, space, and characters that are multi-byte in UTF-8 — is
        the only mechanism here that can reach that class.

        The seed is pinned so a failure reproduces exactly, and mismatch reports
        come from ``assert_matches``, which prints the source.
        """
        rng = random.Random(RANDOM_SEED)
        for _ in range(RANDOM_CASES):
            length = rng.randrange(RANDOM_MAX_LEN + 1)
            source = "".join(rng.choice(RANDOM_ALPHABET) for _ in range(length))
            for debug in (False, True):
                with self.subTest(source=source, debug=debug):
                    self.assert_matches(source, debug)

    def test_token_type_names_round_trip(self):
        """The FFI carries an int; the name is part of the contract.

        ``Token.__repr__`` renders ``token_type.name.capitalize()``, and that repr
        is asserted by ``test_parser.py:36`` and
        ``i18n/test_blocktranslate.py:881``. The int is the right wire format —
        this pins that it maps back to the right name.
        """
        names = [
            TokenType(token[0]).name.capitalize()
            for token in _native.lex("a{% b %}{{ c }}{# d #}")
        ]
        self.assertEqual(names, ["Text", "Block", "Var", "Comment"])

    def test_lone_surrogate_raises_at_the_ffi_boundary(self):
        """A documented difference between the two implementations.

        A Python ``str`` may legally contain a lone surrogate; a Rust ``&str``
        cannot, so PyO3 rejects the argument where Django's lexer processes it.
        Not expected anywhere in the suite — recorded so that it is recognised
        rather than rediscovered, and so the difference is a decision rather
        than a surprise. ``ValueError`` covers PyO3's decode/encode errors,
        which are both subclasses.
        """
        with self.assertRaises(ValueError):
            _native.lex("a\ud800b")

    def test_split_contents_matches(self):
        """`split_contents` is a `Token` method, so it is M1's to get right."""
        for contents in SPLIT_CORPUS:
            with self.subTest(contents=contents):
                self.assertEqual(
                    list(_native.split_contents(contents)),
                    Token(TokenType.BLOCK, contents).split_contents(),
                )

    def test_split_contents_unterminated_marker_raises_the_same_way(self):
        """Django lets `next(bits)` raise `StopIteration` here; so must we.

        An obscure path, but it is the one place `split_contents` can fail, and
        matching the exception type is cheaper than discovering the difference
        in a traceback later.
        """
        contents = 'tag _("unterminated'
        with self.assertRaises(StopIteration):
            Token(TokenType.BLOCK, contents).split_contents()
        with self.assertRaises(StopIteration):
            _native.split_contents(contents)

    def test_comparison_target_is_genuinely_djangos_lexer(self):
        """Guard against this test silently becoming a self-comparison.

        Once M9 interposes `django.template.base`, importing `Lexer` below will
        resolve to our shell instead of Django's, and the parity test would then
        be comparing the Rust lexer against Python that merely delegates to it —
        passing no matter what the Rust lexer does. Fail loudly at that point so
        the comparison is rebuilt against the real source file.
        """
        source_file = inspect.getsourcefile(Lexer)
        self.assertTrue(
            source_file.startswith(str(DJANGO_CHECKOUT)),
            f"parity target is not the pinned checkout: {source_file}",
        )

    def test_a_lexer_error_is_not_silently_swallowed(self):
        """Both implementations must agree on error behaviour too.

        `Lexer(None)` raises on `len()`; ours receives a `str` only, so the
        interesting case is that neither crashes on the corpus above. This test
        pins that errors surface rather than produce empty output.
        """
        with self.assertRaises(TypeError):
            _native.lex(None)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test**

Run: `scripts/check.sh --fast`
Expected: PASS.

Then run the parity test alone with verbose output to confirm every corpus entry ran:

Run: `.venv/bin/python -m unittest tests.test_lexer_parity -v`
Expected: 11 tests, PASS. `test_randomised_sources_match` runs 3000 sources × 2
modes under one test, so its subtests are the bulk of the work.

If `test_corpus_matches_debug` fails on a non-ASCII source, the byte/character
conversion in `segments.rs` is wrong — that is Review Focus item 3 and it is the
likeliest failure in this milestone.

- [ ] **Step 4: Commit**

```bash
git add tests/test_lexer_parity.py tests/lexer_corpus.py
git commit -m "M1: lexer parity test against Django's lexer"
```

---

### Task 7: Milestone close-out

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` (§10.3, M1 row → done)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing new; this is the milestone gate.

- [ ] **Step 1: Run the full check**

Run: `scripts/check.sh`
Expected: cargo test, fmt, clippy, Python unit tests, differential (40 cases identical), and the Django suite all pass. The Django suite must still report `Ran 1547 tests … OK (skipped=6)` — M1 adds no interposed modules, so nothing about the suite should move.

- [ ] **Step 2: Confirm the progress meter is unchanged**

Run:

```bash
.venv/bin/python -c "
import sys
from pathlib import Path

# Nothing puts the pinned checkout on the path automatically; env.sh only sets
# DJANGO_RUST_TEMPLATES. Same two lines as tests/test_shim.py.
sys.path.insert(0, str(Path('.').resolve() / '.django'))

from django_rust_templates import _shim

# unshimmed() raises ShimError('not installed; call install() first') until the
# finder is in place, so install() is not optional here.
_shim.install()
remaining = _shim.unshimmed()
print(len(remaining), 'modules still Django\'s')
assert 'django.template.exceptions' not in remaining, 'exceptions regressed'
print('django.template.exceptions is ours;', len(remaining), 'still fall back')
"
```

`unshimmed()` returns sorted dotted names, e.g. `['django.template.base', ...]`.

Expected: `django.template.exceptions` is ours and **25** modules still fall
back — the same 25 M0 recorded, listed above in `.review/round-1-response.md`.
M1 changes the engine, not the integration, which is exactly what §10.1
predicts.

- [ ] **Step 3: Update the spec's milestone table**

Change the M1 row of §10.3 from `| M1 | Rust lexer, ... | ... |` to:

```markdown
| M1 | Rust lexer, `Token`, `TokenType`, `DebugLexer`, `_native.lex` | ✅ token-stream parity with Django over `tests/lexer_corpus.py`, debug positions included |
```

- [ ] **Step 4: Hand to review, then commit**

Write the milestone summary to `.review/` and send it to `rusty-reviewer` per the workflow in spec §11. Commit only after the review returns APPROVE or all blocker/major findings are resolved.

```bash
git add docs/superpowers/specs
git commit -m "M1: milestone complete"
```
