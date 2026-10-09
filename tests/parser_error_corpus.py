"""Template sources that must fail to parse, with the message Django gives.

Each entry is `(source, expected message)`. The expected message is verified
against Django's real parser by
`test_parser_errors.ErrorParityTests.test_the_corpus_matches_django`, so a wrong
expectation fails loudly rather than silently agreeing with a wrong
implementation.

Error text is pinned exactly: Django's own test suite compares it with
`assertRaisesMessage`, and `Template.get_exception_info` exposes `e.token`.
"""

from tests.parser_corpus import LIBRARIES

# Django's own text for an unresolvable library name, `\n`.join over
# `get_installed_libraries()` — that is, over the engine's library set, sorted.
# **Derived from `LIBRARIES` rather than spelled out**, and it was spelled out
# until it went stale: adding `bad_tag` to that dict rewrote this message, and
# two rows here still carried the old text. They failed against Django, which is
# the guard working, but the fix is one spelling of the list and not two.
LIBRARY_LIST = "\n".join(sorted(LIBRARIES))

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
    # **The `Host::repr` divergence, and the only row that reaches it.** U+00AD
    # is category Cf: non-printable in Python, so `repr` escapes it to
    # `\xad`, while Rust's `char::is_control` is Cc alone and would emit it raw.
    # `py_repr_str` refuses non-ASCII with a `debug_assert` for exactly this
    # reason, and the FFI's `FilterCheckHost::repr` overrides it with Python's
    # own `repr()`. Every other `{% with %}` row above is ASCII *by
    # construction* (`'b'`, `'and'`, the tag name `'with'`), so without this one
    # the override could be deleted and the whole suite would still be green —
    # loud in a debug build, silently wrong in a release one. Measured: exact
    # message and token parity with Django in both lexer modes.
    (
        "{% with a=1 foo­bar %}{% endwith %}",
        "'with' received an invalid token: 'foo\\xadbar'",
    ),
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
    # --- regroup ------------------------------------------------------------
    # Arity is checked before either keyword, so a four-bit call reports this
    # even though `by` and `as` are also missing.
    ("{% regroup m by k %}", "'regroup' tag takes five arguments"),
    ("{% regroup %}", "'regroup' tag takes five arguments"),
    # The target is compiled before the `by` check, so a six-bit call with a bad
    # target reports the *filter* error, not the keyword one.
    ("{% regroup m| by k as g %}", "Could not parse the remainder: '|' from 'm|'"),
    ("{% regroup m ny k as g %}", "second argument to 'regroup' tag must be 'by'"),
    ("{% regroup m by k is g %}", "next-to-last argument to 'regroup' tag must be 'as'"),
    # --- url ----------------------------------------------------------------
    ("{% url %}", "'url' takes at least one argument, a URL pattern name."),
    # Measured: `kwarg_re.match("nope=")` keys nothing and takes `"nope="` as the
    # value, so this is the *filter* compiler's error, not the "Malformed
    # arguments to url tag" one the plan listed. That Malformed branch is
    # unreachable from any token (`kwarg_re`'s `(.+)` cannot cross a newline, and
    # no bit `split_contents` yields starts with one), so no corpus row carries
    # its message; `tags/url.rs`'s unit test drives it directly.
    ("{% url 'view' k=v nope= %}", "Could not parse the remainder: '=' from 'nope='"),
    # --- for ----------------------------------------------------------------
    # The arity check fires before anything is inspected, and `%s` is the raw
    # `token.contents`, not a repr and not the command name.
    ("{% for x in %}", "'for' statements should have at least four words: for x in"),
    # `bits[-2]` is `b`, so check 2 fires before the loop variables are even
    # split — `a b` is also an invalid variable, and is not the message.
    ("{% for a b c %}", "'for' statements should use the format 'for x in y': for a b c"),
    # No comma, so the split yields the *one* variable `"a b"`, which the space
    # makes invalid.
    (
        "{% for a b in y %}{% endfor %}",
        "'for' tag received an invalid argument: for a b in y",
    ),
    # `FILTER_SEPARATOR` is one of the four forbidden characters, so this is an
    # *argument* error rather than a filter to compile.
    (
        "{% for a|b in y %}{% endfor %}",
        "'for' tag received an invalid argument: for a|b in y",
    ),
    # --- cycle --------------------------------------------------------------
    ("{% cycle %}", "'cycle' tag requires at least two arguments"),
    # No `as` form has run, so the registry itself is absent — the first of the
    # two unknown-name messages, and the one with **no** trailing period.
    ("{% cycle foo %}", "No named cycles in template. 'foo' is not defined"),
    ('{% cycle "a" "b" as c %}{% cycle c2 %}', "Named cycle 'c2' does not exist"),
    # `args[-3]` must be the `as` for this branch to be reachable: the plan's
    # draft used `{% cycle "a" "b" c as nope %}`, which is the *other* `as` form
    # and succeeds with the name `nope`. Measured on the pinned tree.
    (
        "{% cycle a b as c d %}",
        "Only 'silent' flag is allowed after cycle's name, not 'd'.",
    ),
    # --- resetcycle ---------------------------------------------------------
    # All three are Django's, and were landed with Task 2's wire-form change;
    # the rows arrive here because these are the first templates in which
    # `{% cycle %}` exists to make the last two reachable at all.
    ("{% resetcycle a b %}", "'resetcycle' tag accepts at most one argument."),
    (
        "{% resetcycle nope %}{% cycle 'x' 'y' %}",
        "Named cycle 'nope' does not exist.",
    ),
    ("{% resetcycle %}", "No cycles in template."),
    # --- ifchanged ----------------------------------------------------------
    # Not a check in the tag: `parse(("else", "endifchanged"))` stops at the
    # first `else`, and the second one then fails to terminate the body's
    # `parse(("endifchanged",))`. Here for the wording, which the tag's own unit
    # test also pins; the coverage test exempts `ifchanged` because the message
    # is the base parser's, not the tag's.
    (
        "{% ifchanged %}{% else %}{% else %}{% endifchanged %}",
        "Invalid block tag on line 1: 'else', expected 'endifchanged'. "
        "Did you forget to register or load this tag?",
    ),
    # --- if -----------------------------------------------------------------
    # Three sources of failure in one tag, and they are all here because they
    # arrive by different routes: smartif's expression parser (the first four),
    # the host's filter compiler (the next two), and the base parser's
    # terminator handling (the last five).
    #
    # An empty condition and a dangling operator are the *same* raise site —
    # `EndToken.nud` (`smartif.py:149-150`) reached through `IfParser.expression`,
    # once as the very first token and once as an infix operator's right
    # operand. Measured, not assumed: the draft predicted two different
    # messages here.
    ("{% if %}{% endif %}", "Unexpected end of expression in if tag."),
    ("{% if a and %}{% endif %}", "Unexpected end of expression in if tag."),
    # `IfParser.parse`'s exhaustion check (`smartif.py:198-200`), not a nud.
    ("{% if a b %}{% endif %}", "Unused 'b' at end of if expression."),
    # `and` in prefix position, so `TokenBase.nud` (`smartif.py:22-26`).
    ("{% if a and and b %}{% endif %}", "Not expecting 'and' in this position in if tag."),
    # Both compiled during the parse by `TemplateIfParser.create_var`, so both
    # are the host's `compile_filter` failing — one on the filter lookup
    # (`FilterExpression.__init__`), one inside `Variable` — and neither is a
    # render-time lookup miss.
    ("{% if a|does_not_exist %}{% endif %}", "Invalid filter: 'does_not_exist'"),
    ("{% if 1>2 %}{% endif %}", "Could not parse the remainder: '>2' from '1>2'"),
    # `do_if`'s own `parser.parse` with the three terminators; the "Looking for
    # one of" list is Django's, in Django's order.
    ("{% if a %}", "Unclosed tag on line 1: 'if'. Looking for one of: elif, else, endif."),
    # `elif` is not a registered tag — the block parser never starts it.
    (
        "{% elif a %}",
        "Invalid block tag on line 1: 'elif'. "
        "Did you forget to register or load this tag?",
    ),
    # The terminator line's trailing bits, from `do_if`'s **single** check —
    # `if token.contents != "endif"` (`defaulttags.py:1013-1017`), which the
    # `else foo` spelling reaches too because `token.contents == "else"` is
    # already false by then. One raise site, two spellings (measured; the draft
    # attributed `else foo` to smartif, which raises no such message — `grep
    # Malformed .django/django/template/` is one hit). The token is the `if`'s,
    # not the terminator's, because the error is raised on the tag's own stored
    # token (measured).
    ("{% if a %}x{% endif foo %}", 'Malformed template tag at line 1: "endif foo"'),
    ("{% if a %}x{% else foo %}y{% endif %}", 'Malformed template tag at line 1: "else foo"'),
    # A second `endif` is *not* Malformed: the first one closed the tag and the
    # second is an ordinary unknown tag. The plan predicted Malformed here, and
    # the prediction was wrong — measured on the pinned tree.
    (
        "{% if a %}{% endif %}{% endif %}",
        "Invalid block tag on line 1: 'endif'. "
        "Did you forget to register or load this tag?",
    ),
    # --- the innermost token, across a nested parse -------------------------
    (
        "{% autoescape on %}\n{% now a b %}\n{% endautoescape %}",
        "'now' statement takes one argument",
    ),
    # --- M6: the messages only the *host* can raise -------------------------
    # Every message above is a string Rust ports; these are not, and that is the
    # point. Each is raised *inside* a compile function the host runs, against
    # the parser the shell handed it, so a row here is evidence that the seam
    # carries a live Django parser rather than that Rust copied a literal.
    #
    # `{% load %}` is the tag that proves it on its own: `find_library` reads
    # `parser.libraries` (`defaulttags.py:1065-1075`) and the message embeds
    # `LIBRARY_LIST` is `"\n".join(sorted(parser.libraries))` — the caller's
    # library set, not a constant. Ours matches Django's only because both sides
    # are given the same `Engine.template_libraries`; see `parser_corpus.LIBRARIES`.
    (
        "{% load nope %}",
        "'nope' is not a registered tag library. Must be one of:\n" + LIBRARY_LIST,
    ),
    # A second name in the same tag, reaching the same raise site: `do_load`
    # treats every bit after the command as a library name when there is no
    # `from` (`defaulttags.py:1099-1105`), so `custom` resolves and `no_params`
    # does not.
    (
        "{% load custom no_params %}",
        "'no_params' is not a registered tag library. Must be one of:\n" + LIBRARY_LIST,
    ),
    # The *other* raise site, which the `from` form is the only way to reach
    # (`load_from_library`, `defaulttags.py:1086-1095`) — and it is a different
    # sentence, which is why both rows exist.
    (
        "{% load nope from custom %}",
        "'nope' is not a valid tag or filter in tag library 'custom'",
    ),
    # `parse_bits` (`library.py:392-480`) is where a `simple_tag`'s arguments are
    # bound, and all of its messages are built from the *function's* signature
    # and the caller's tokens. Four spellings, four messages, one of which names
    # the parameter the caller omitted. The plan counted five branches; four
    # carry distinct text, and the fifth (the keyword-only check with no
    # keyword-only parameter declared) is unreachable from a library that
    # registers none.
    ("{% load custom %}{% no_params x %}", "'no_params' received too many positional arguments"),
    (
        "{% load custom %}{% one_param %}",
        "'one_param' did not receive value(s) for the argument(s): 'arg'",
    ),
    ("{% load custom %}{% one_param x y %}", "'one_param' received too many positional arguments"),
    (
        "{% load custom %}{% one_param x y=1 %}",
        "'one_param' received unexpected keyword argument 'y'",
    ),
    # The `command_stack` row, and it is one of the five the reviewer named as
    # surviving to M9: a `simple_block_tag` whose body is unclosed re-enters
    # Django's parse, runs out of tokens, and `Parser.unclosed_block_tag` pops
    # `parser.command_stack` (`base.py:651`) — a stack Django's own `Parser.parse`
    # maintains and Rust, driving the outer loop, does not. Measured before
    # `ShellHost` maintained it: `ParseError('pop from empty list')`.
    (
        "{% load custom %}{% div %}a",
        "Unclosed tag on line 1: 'div'. Looking for one of: enddiv.",
    ),
    # --- the innermost token, when the failure is *inside* a host tag --------
    # The message alone cannot tell these apart from the row above: the same
    # `now` failure is raised with the same text whether the host tag encloses
    # it or not. What moves is the **token** — Django's `Parser.error` sets
    # `.token` on the first exception it sees and never replaces one already set
    # (`base.py:619-630`), so `do_div`'s nested `parser.parse` attaches the
    # `now a b` token and the outer frame's `self.error(div_token, e)` leaves it
    # alone. Measured on the pinned tree, debug off and on:
    #
    #     {% load custom %}{% div %}{% now a b %}{% enddiv %}
    #     → "'now' statement takes one argument", token (2, 'now a b', 1, None)
    #       and with debug, position (26, 39) — the `now` block, not `div`'s
    #
    # Rust has the matching guard (`Error::error`'s `token.or_else`,
    # `parser/mod.rs:369-373`) and is inert without this: the token never
    # crosses the seam, so the guard sees `None` and attaches the **outer**
    # token — `(2, 'div', 1, None)`. Both assertions of
    # `test_every_case_carries_the_same_token*` fail without the transport, and
    # only the token ones: the message matches either way.
    (
        "{% load custom %}{% div %}{% now a b %}{% enddiv %}",
        "'now' statement takes one argument",
    ),
    # The same failure one frame deeper, because "the innermost wins" is a claim
    # about depth and one level cannot distinguish it from "the first nested
    # frame wins".
    (
        "{% load custom %}{% div %}{% div %}{% now a b %}{% enddiv %}{% enddiv %}",
        "'now' statement takes one argument",
    ),
    # And the inner failure Django raises itself rather than the inner tag's
    # compile function: `Parser.parse` stopped at `{% enddiv %}`'s absence finds
    # a block tag it was not told to stop for and goes through
    # `invalid_block_tag` → `self.error(token, msg)` (`base.py:632-651`). Same
    # seam, second producer of the token, so the transport has to be in
    # `call_tag`'s raise path rather than in a particular tag's error.
    (
        "{% load custom %}{% div %}{% badtag %}{% enddiv %}",
        "Invalid block tag on line 1: 'badtag', expected 'enddiv'. Did you "
        "forget to register or load this tag?",
    ),
    # --- the class, which the seam was also flattening ----------------------
    # `badtag`'s compile function raises `RuntimeError` (`bad_tag.py:6-8`), and
    # Django's `Parser.parse` re-raises the *same object* (`base.py:583-585`),
    # so the parse leaves Django as a `RuntimeError`. Every comparison in this
    # file but one compares text — and the text is `"I am a bad tag"` either
    # way — so a seam that turned it into a `ParseError` passed all of them.
    #
    # `.django/tests/template_tests/tests.py:114-123` is the acceptance test
    # that cannot be fooled: it asserts `assertRaises(RuntimeError)` around
    # `Engine.from_string("{% load bad_tag %}{% badtag %}")`. Unreachable until
    # M9 routes `Engine.from_string` through this parser, which is why the class
    # is carried as data now and converted there.
    ("{% load bad_tag %}{% badtag %}", "I am a bad tag"),
    # --- and one row that is not an M6 tag, because grading the corpus is the
    # task and the gap is in it ---------------------------------------------
    # The only input that reaches `loopvar_split` on an *empty* haystack, and
    # nothing in either corpus reached it. `reversed` appears once in the success
    # corpus and that is the happy path; the two `for` error rows above both fail
    # the `bits[in_index] != "in"` test instead. With four bits and
    # `is_reversed`, `in_index` is 1, `bits[1]` *is* "in", so that check passes
    # and `bits[1..1]` is empty: `re.split(r" *, *", "")` runs and Django rejects
    # the empty loop variable. One row pins the `in_index == 1` arithmetic, the
    # empty haystack, and `is_invalid_loopvar` at once.
    (
        "{% for in y reversed %}x{% endfor %}",
        "'for' tag received an invalid argument: for in y reversed",
    ),
]
