"""Every spelling of `{% endpartialdef %}`, against the pinned tree.

M13's Task 4 turns on an asymmetry that is easy to get wrong in *either*
direction, so it is worth an instrument rather than a sentence:

* the scan that decides where a `{% partialdef %}` block **ends** matches the
  **first word** of the end token (`django/template/base.py:561`, `:564` — the
  `command in parse_until` test, whose `command` is `token.contents.split()[0]`);
* the check that decides whether it ended **legitimately** compares the **whole
  contents** (`django/template/defaulttags.py:1244` — `endpartial.contents not in
  valid_endpartials`).

`Lexer.create_token` strips only the *outer* whitespace
(`base.py:446`, `content = token_string[2:-2].strip()`), so inner whitespace
survives into `contents`. The consequence is that `{% endpartialdef  p %}` — two
spaces, or three, or a tab — **terminates the block and is then refused**, with
`Invalid block tag … expected 'endpartialdef' or 'endpartialdef p'`. Only the
exact spellings `endpartialdef` and `endpartialdef p` are accepted.

An implementation that compares whole contents to *stop* the scan reports
`Unclosed tag` for the multi-space form; one that compares the first word to
*validate* silently accepts a template Django rejects. Both are wrong, and
differently.

Pinned tree and pinned interpreter, named by version: `.django/` @ 6.0.9 on
`.venv`/CPython 3.14.4. Run under `-I` **plus** an explicit `sys.path` insert,
with `DJANGO_RUST_TEMPLATES=0`.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".django"))

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(TEMPLATES=[], INSTALLED_APPS=[], DEBUG=True)
django.setup()

from django.template import Engine  # noqa: E402
from django.template.base import Lexer, TokenType  # noqa: E402
from django.template.exceptions import TemplateSyntaxError  # noqa: E402

engine = Engine(debug=True)

CASES = [
    ("no name", "{% partialdef p %}x{% endpartialdef %}"),
    ("one space", "{% partialdef p %}x{% endpartialdef p %}"),
    ("two spaces", "{% partialdef p %}x{% endpartialdef  p %}"),
    ("three spaces", "{% partialdef p %}x{% endpartialdef   p %}"),
    ("tab", "{% partialdef p %}x{% endpartialdef\tp %}"),
    ("wrong name", "{% partialdef p %}x{% endpartialdef q %}"),
    ("unclosed", "{% partialdef p %}x"),
]

print(f"django {django.get_version()} on {sys.version.split()[0]}")
print("\nwhat each spelling does:")
for label, source in CASES:
    try:
        engine.from_string(source)
    except TemplateSyntaxError as error:
        print(f"  {label:<14} TemplateSyntaxError: {str(error)!r}")
    else:
        print(f"  {label:<14} OK")

# The two values the two checks read, printed as `repr` so the whitespace is
# visible rather than described.
print("\nthe token, as each check sees it (first word vs whole contents):")
for label, source in CASES[:5]:
    for token in Lexer(source).tokenize():
        if token.token_type != TokenType.BLOCK:
            continue
        first_word = token.contents.split()[0]
        if first_word != "endpartialdef":
            continue
        print(
            f"  {label:<14} contents={token.contents!r}"
            f"  first_word={first_word!r}"
        )
