#!/usr/bin/env python3
"""Which row of the error corpus needs `settings` configured, and why?

`tests/test_parser_errors.py` configures `USE_I18N=False` in `setUpClass` so the
corpus can run standalone. The recorded reason was: *the `{% block a %}x{%
endblock b %}` row is the first whose Django message passes a two-element list
to `get_text_list`, whose `last_word` default is `gettext_lazy("or")`*.

Both halves of that are checkable, and this measures them:

1. `invalid_block_tag` passes `"or"` **explicitly** (`base.py:641`), so the lazy
   default at `text.py:282` is never reached from any row of this corpus.
2. `_(", ")` in the body (`text.py:301`) is evaluated *eagerly*, before the
   generator it joins is consumed — so it is called for a two-element list too,
   where `list_[:-1]` yields one item and the separator it returns is discarded.

(2) is the part a reader gets wrong by reasoning about `str.join` instead of
reading the expression, and it is the part that decides whether the guard's
stated reason is true. The script answers both by counting `gettext` calls per
corpus row rather than arguing about them.

Run: `.venv/bin/python -I .review/m7-measurements/m7_measure_gettextlist.py`
`-I` because this reads the pinned `.django/` checkout; the paths it needs are
inserted below.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DJANGO_CHECKOUT = ROOT / ".django"
for path in (ROOT, DJANGO_CHECKOUT, DJANGO_CHECKOUT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from django.conf import empty, settings  # noqa: E402
from django.core.exceptions import ImproperlyConfigured  # noqa: E402

# Not `django.setup()`: that reads `LOGGING_CONFIG` and would need a configured
# settings module for reasons this measurement is not about. The template
# engine needs nothing but `USE_I18N`.
settings.configure(USE_I18N=False)

import django.utils.text as dtext  # noqa: E402
import django.utils.translation as dtrans  # noqa: E402
from django.template.base import DebugLexer, Lexer, Parser  # noqa: E402
from django.template.engine import Engine  # noqa: E402

from tests.parser_corpus import LIBRARIES  # noqa: E402
from tests.parser_error_corpus import ERRORS  # noqa: E402

ENGINE = Engine(libraries=LIBRARIES)


def django_message(source, debug=False):
    """The message Django's parser raises for `source`, or None."""
    parser = Parser(
        (DebugLexer(source) if debug else Lexer(source)).tokenize(),
        ENGINE.template_libraries,
        ENGINE.template_builtins,
    )
    try:
        parser.parse()
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


print("=" * 72)
print("1. Is `last_word`'s lazy default ever reached from `invalid_block_tag`?")
print("=" * 72)
print("base.py:641 passes the literal \"or\"; text.py:282 is the default only.")
message = dtext.get_text_list(["'endblock'", "'endblock b'"], "or")
print(f"  get_text_list([2 items], 'or') -> {message!r}")
print(
    "  the default is a *parameter default*: not evaluated when an argument "
    "is passed,"
)
print(
    "  and `str(last_word)` (text.py:302) on a plain str reaches no translation "
    "layer."
)

print()
print("=" * 72)
print("2. Is `_(\", \")` (text.py:301) reached for a two-element list?")
print("=" * 72)
calls = []
real_underscore = dtext._


def spy(msg):
    calls.append(msg)
    return real_underscore(msg)


dtext._ = spy
try:
    dtext.get_text_list(["'endblock'", "'endblock b'"], "or")
    two = list(calls)
    calls.clear()
    dtext.get_text_list(["'elif'", "'else'", "'endif'"], "or")
    three = list(calls)
finally:
    dtext._ = real_underscore
print(f"  two-element list   -> gettext calls: {two}")
print(f"  three-element list -> gettext calls: {three}")

print()
print("=" * 72)
print("3. Per corpus row: does formatting Django's message call `_`?")
print("=" * 72)
rows_with_calls = []
for index, (source, _expected) in enumerate(ERRORS):
    calls.clear()
    dtext._ = spy
    try:
        message = django_message(source)
    finally:
        dtext._ = real_underscore
    if calls:
        rows_with_calls.append((index, source, list(calls), message))

print(f"  corpus rows: {len(ERRORS)}; rows whose message calls `_`: {len(rows_with_calls)}")
for index, source, called, message in rows_with_calls[:8]:
    print(f"  [{index}] {source!r} -> _({called[0]!r}) -> {message!r}")
if len(rows_with_calls) > 8:
    print(f"  ... and {len(rows_with_calls) - 8} more")

block_index = next(
    index
    for index, (source, _m) in enumerate(ERRORS)
    if source == "{% block a %}x{% endblock b %}"
)
print(f"  the `{{% block %}}` row is index {block_index}")

print()
print("=" * 72)
print("4. Which rows raise `ImproperlyConfigured` with settings unconfigured?")
print("=" * 72)


def without_settings(source):
    """Run one row against an unconfigured `LazySettings`.

    `ImproperlyConfigured` is re-raised rather than returned as the message:
    `django_message`'s own `except Exception` would swallow it and hand back its
    text, which is how the first run of this script reported "0 rows raise".
    """
    settings._wrapped = empty
    # `Trans` caches the resolved function on first call, and its own docstring
    # says a `USE_I18N` change has no effect after that — so a row would sail
    # through on a function another row already resolved. Cleared per row, which
    # is what its docstring prescribes: delete the functions from `_trans.__dict__`.
    dtrans._trans.__dict__.clear()
    try:
        parser = Parser(
            Lexer(source).tokenize(),
            ENGINE.template_libraries,
            ENGINE.template_builtins,
        )
        parser.parse()
        return None
    except ImproperlyConfigured:
        raise
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    finally:
        settings.configure(USE_I18N=False)


raised = []
for index, (source, _expected) in enumerate(ERRORS):
    try:
        without_settings(source)
    except ImproperlyConfigured as exc:
        raised.append((index, source, str(exc).splitlines()[0]))

print(f"  rows that raise without settings: {len(raised)}")
for index, source, kind in raised:
    print(f"  [{index}] {source!r} -> ImproperlyConfigured: {kind}")
print(
    "  (`Engine(...)` is constructed once above, while configured, so a row "
    "failing here"
)
print("   failed in message formatting, not in engine construction.)")
