"""Template sources that must fail to parse, with the message Django gives.

Each entry is `(source, expected message)`. The expected message is verified
against Django's real parser by
`test_parser_errors.ErrorParityTests.test_the_corpus_matches_django`, so a wrong
expectation fails loudly rather than silently agreeing with a wrong
implementation.

Error text is pinned exactly: Django's own test suite compares it with
`assertRaisesMessage`, and `Template.get_exception_info` exposes `e.token`.
"""

TEMPLATETAG_ARGUMENTS = [
    "openblock",
    "closeblock",
    "openvariable",
    "closevariable",
    "openbrace",
    "closebrace",
    "opencomment",
    "closecomment",
]

# Built with `repr` rather than written out, because Python's `list()` repr of a
# list literal is insertion-ordered and exactly what `"%s" % list(mapping)`
# produces in Django. The test still checks it against Django.
TEMPLATETAG_INVALID = (
    "Invalid templatetag argument: 'nope'. Must be one of: " + repr(TEMPLATETAG_ARGUMENTS)
)

ERRORS = [
    # --- empty tags ---------------------------------------------------------
    ("{{}}", "Empty variable tag on line 1"),
    ("{%  %}", "Empty block tag on line 1"),
    ("{% %}", "Empty block tag on line 1"),
    ("\n\n{{}}", "Empty variable tag on line 3"),
    # --- unknown tags ------------------------------------------------------
    (
        "{% nope %}",
        "Invalid block tag on line 1: 'nope'. "
        "Did you forget to register or load this tag?",
    ),
    (
        "x\n{% nope %}",
        "Invalid block tag on line 2: 'nope'. "
        "Did you forget to register or load this tag?",
    ),
    # The `parse_until` form, with the two-item `get_text_list` join.
    (
        "{% spaceless %}\n{% nope %}",
        "Invalid block tag on line 2: 'nope', expected 'endspaceless'. "
        "Did you forget to register or load this tag?",
    ),
    # --- autoescape --------------------------------------------------------
    ("{% autoescape %}", "'autoescape' tag requires exactly one argument."),
    ("{% autoescape on off %}", "'autoescape' tag requires exactly one argument."),
    # A *matched* quoted phrase containing a space, where the raw split and
    # `split_contents` genuinely disagree: the raw split counts three arguments
    # here, `split_contents` two. Only the raw split reaches this count error,
    # so this is the raw-vs-smart discriminator for `{% autoescape %}`.
    #
    # (The plan's original entry here was `{% autoescape "on" %}`, with a
    # comment claiming an *unmatched* quote makes the raw split count three
    # arguments. That is wrong against Django 6.0.9: `'autoescape "on"'.split()`
    # is `['autoescape', '"on"']` — two arguments, exactly as `smart_split`
    # gives — and the template fails on the *value*, not the count. The entry is
    # kept below with Django's real message.)
    ('{% autoescape "on off" %}', "'autoescape' tag requires exactly one argument."),
    # An unmatched quote: `smart_split` does not treat it as opening a phrase,
    # so both splitters split it identically and it is rejected on the count.
    ('{% autoescape a"b c %}', "'autoescape' tag requires exactly one argument."),
    # A matched quoted single word is two arguments to both splitters, so this
    # one fails on the value: `'"on"'` is not `'on'`.
    ('{% autoescape "on" %}', "'autoescape' argument should be 'on' or 'off'"),
    ("{% autoescape maybe %}", "'autoescape' argument should be 'on' or 'off'"),
    (
        "{% autoescape on %}",
        "Unclosed tag on line 1: 'autoescape'. Looking for one of: endautoescape.",
    ),
    (
        "x\n\n{% autoescape on %}y",
        "Unclosed tag on line 3: 'autoescape'. Looking for one of: endautoescape.",
    ),
    # Nested: the *innermost* command frame is popped, so the line is the inner
    # tag's, not the outer one's.
    (
        "{% autoescape on %}\n{% autoescape off %}",
        "Unclosed tag on line 2: 'autoescape'. Looking for one of: endautoescape.",
    ),
    # --- comment, through skip_past rather than parse -----------------------
    (
        "{% comment %}",
        "Unclosed tag on line 1: 'comment'. Looking for one of: endcomment.",
    ),
    (
        "{% comment %}a\nb",
        "Unclosed tag on line 1: 'comment'. Looking for one of: endcomment.",
    ),
    # --- the other body-reading tags ---------------------------------------
    (
        "{% spaceless %}",
        "Unclosed tag on line 1: 'spaceless'. Looking for one of: endspaceless.",
    ),
    (
        "{% verbatim %}",
        "Unclosed tag on line 1: 'verbatim'. Looking for one of: endverbatim.",
    ),
    # --- now ---------------------------------------------------------------
    ("{% now %}", "'now' statement takes one argument"),
    ("{% now a b %}", "'now' statement takes one argument"),
    ("{% now a b c %}", "'now' statement takes one argument"),
    ("{% now a b c d %}", "'now' statement takes one argument"),
    # --- templatetag -------------------------------------------------------
    ("{% templatetag %}", "'templatetag' statement takes one argument"),
    ("{% templatetag a b %}", "'templatetag' statement takes one argument"),
    ("{% templatetag nope %}", TEMPLATETAG_INVALID),
    # Raw split, so the quotes are part of the argument and it is invalid.
    # (Both splitters keep the quotes, so this is not a raw-vs-smart
    # discriminator; `{% templatetag "open block" %}` would be.)
    (
        '{% templatetag "openblock" %}',
        "Invalid templatetag argument: '\"openblock\"'. Must be one of: "
        + repr(TEMPLATETAG_ARGUMENTS),
    ),
    # --- resetcycle: both of its paths are errors at M2 ---------------------
    ("{% resetcycle %}", "No cycles in template."),
    ("{% resetcycle x %}", "Named cycle 'x' does not exist."),
    ("{% resetcycle a b %}", "'resetcycle' tag accepts at most one argument."),
    # --- filter ------------------------------------------------------------
    (
        "{% filter escape %}x{% endfilter %}",
        '"filter escape" is not permitted. Use the "autoescape" tag instead.',
    ),
    (
        "{% filter safe %}x{% endfilter %}",
        '"filter safe" is not permitted. Use the "autoescape" tag instead.',
    ),
    # The chain is walked in order, so the offending filter is named by its own
    # position and not by the first one.
    (
        "{% filter upper|safe %}x{% endfilter %}",
        '"filter safe" is not permitted. Use the "autoescape" tag instead.',
    ),
    # Not a `TemplateSyntaxError` at all: `do_filter` unpacks
    # `token.contents.split(None, 1)` with no length check
    # (`defaulttags.py:744`), so `{% filter %}` — whose contents hold no
    # separating run — raises a bare `ValueError`. Django's `parse` catches
    # `Exception`, not `TemplateSyntaxError`, and re-raises the same object with
    # `.token` attached, which is why the token comparison below still bites.
    #
    # The *class* is pinned separately, by
    # `test_a_filter_tag_with_no_argument_is_a_value_error_for_both` — this list
    # only compares `str()` and the token, so a `TemplateSyntaxError` carrying
    # the same text would satisfy it.
    ("{% filter %}x{% endfilter %}", "not enough values to unpack (expected 2, got 1)"),
    # --- firstof -----------------------------------------------------------
    ("{% firstof %}", "'firstof' statement requires at least one argument"),
    # --- with --------------------------------------------------------------
    ("{% with %}{% endwith %}", "'with' expected at least one variable assignment"),
    ("{% with x %}{% endwith %}", "'with' expected at least one variable assignment"),
    # The leftover bit after `token_kwargs` has taken what it recognised, and
    # it goes through Python's `repr` — the tag name too.
    ("{% with a=1 b %}{% endwith %}", "'with' received an invalid token: 'b'"),
    # A stray `and` in *kwarg* form is not a separator: only the legacy branch
    # consumes it (`base.py:1203-1206`).
    ("{% with a=1 and %}{% endwith %}", "'with' received an invalid token: 'and'"),
    # --- widthratio --------------------------------------------------------
    ("{% widthratio a b %}", "widthratio takes at least three arguments"),
    # Five bits — tag plus four — is the `else`, so the count message is about a
    # tag that does have three arguments.
    ("{% widthratio a b c d %}", "widthratio takes at least three arguments"),
    # Six bits takes the *`as` branch*, whose keyword is at index 4; the plan
    # for this task had this row down as the three-argument case.
    (
        "{% widthratio a b c d e %}",
        "Invalid syntax in widthratio tag. Expecting 'as' keyword",
    ),
    (
        "{% widthratio a b c nope w %}",
        "Invalid syntax in widthratio tag. Expecting 'as' keyword",
    ),
    # --- lorem -------------------------------------------------------------
    ("{% lorem 3 p nope %}", "Incorrect format for 'lorem' tag"),
    ("{% lorem 1 2 %}", "Incorrect format for 'lorem' tag"),
    # --- the innermost token, across a nested parse -------------------------
    (
        "{% autoescape on %}\n{% now a b %}\n{% endautoescape %}",
        "'now' statement takes one argument",
    ),
]
