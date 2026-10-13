"""Template sources that must fail to parse, with the message Django gives.

Each entry is `(source, expected message)`, checked against Django's real parser by
`test_parser_errors.ErrorParityTests.test_the_corpus_matches_django`, so a wrong
expectation fails loudly. Text is pinned exactly — Django's suite compares it with
`assertRaisesMessage`, and `Template.get_exception_info` exposes `e.token`.
"""

from tests.parser_corpus import LIBRARIES

# Django's text for an unresolvable library name: `"\n".join` over the engine's sorted
# library set. **Derived from `LIBRARIES`**, so there is one spelling of the list, not two.
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

# Built with `repr` rather than written out, because Python's `repr` of a list literal is
# insertion-ordered and exactly what `"%s" % list(mapping)` produces in Django.
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
    # A matched quoted phrase with a space splits as 3 args raw vs 2 smart; only the raw
    # split reaches this count error, so this is the raw-vs-smart discriminator.
    ('{% autoescape "on off" %}', "'autoescape' tag requires exactly one argument."),
    # Unmatched quote: `smart_split` does not open a phrase, so both splitters reject on count.
    ('{% autoescape a"b c %}', "'autoescape' tag requires exactly one argument."),
    # A matched quoted single word is 2 args to both splitters, so this fails on the value.
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
    # Nested: the *innermost* command frame is popped, so the line is the inner tag's.
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
    # Raw split, so the quotes are part of the argument and it is invalid. Not a raw-vs-smart
    # discriminator (both splitters keep the quotes), unlike `{% templatetag "open block" %}`.
    (
        '{% templatetag "openblock" %}',
        "Invalid templatetag argument: '\"openblock\"'. Must be one of: "
        + repr(TEMPLATETAG_ARGUMENTS),
    ),
    # --- resetcycle: both of its paths are errors ----------------------------
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
    # The chain is walked in order, so the offending filter is named by its own position,
    # not by the first one.
    (
        "{% filter upper|safe %}x{% endfilter %}",
        '"filter safe" is not permitted. Use the "autoescape" tag instead.',
    ),
    # Not a `TemplateSyntaxError`: `do_filter` unpacks `token.contents.split(None, 1)`
    # unchecked (`defaulttags.py:744`), so this raises a bare `ValueError`. The class is pinned
    # separately by `test_a_filter_tag_with_no_argument_is_a_value_error_for_both`.
    ("{% filter %}x{% endfilter %}", "not enough values to unpack (expected 2, got 1)"),
    # --- firstof -----------------------------------------------------------
    ("{% firstof %}", "'firstof' statement requires at least one argument"),
    # --- with --------------------------------------------------------------
    ("{% with %}{% endwith %}", "'with' expected at least one variable assignment"),
    ("{% with x %}{% endwith %}", "'with' expected at least one variable assignment"),
    # The leftover bit after `token_kwargs` has taken what it recognised, through Python's
    # `repr` — the tag name too.
    ("{% with a=1 b %}{% endwith %}", "'with' received an invalid token: 'b'"),
    # A stray `and` in *kwarg* form is not a separator: only the legacy branch consumes it
    # (`base.py:1203-1206`).
    ("{% with a=1 and %}{% endwith %}", "'with' received an invalid token: 'and'"),
    # **The `Host::repr` divergence, the only row that reaches it.** U+00AD is category Cf:
    # non-printable in Python, so `repr` escapes it to `\xad`, while Rust's `char::is_control`
    # is Cc alone and would emit it raw. Every other `{% with %}` row is ASCII, so without
    # this one the `repr` override could be deleted and the suite would still be green.
    (
        "{% with a=1 foo­bar %}{% endwith %}",
        "'with' received an invalid token: 'foo\\xadbar'",
    ),
    # --- widthratio --------------------------------------------------------
    ("{% widthratio a b %}", "widthratio takes at least three arguments"),
    # Five bits — tag plus four — is the `else`, so the count message is about a tag that
    # does have three arguments.
    ("{% widthratio a b c d %}", "widthratio takes at least three arguments"),
    # Six bits takes the *`as` branch*, whose keyword is at index 4.
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
    # Arity is checked before either keyword, so a four-bit call reports this even though
    # `by` and `as` are also missing.
    ("{% regroup m by k %}", "'regroup' tag takes five arguments"),
    ("{% regroup %}", "'regroup' tag takes five arguments"),
    # The target is compiled before the `by` check, so a bad target reports the *filter* error.
    ("{% regroup m| by k as g %}", "Could not parse the remainder: '|' from 'm|'"),
    ("{% regroup m ny k as g %}", "second argument to 'regroup' tag must be 'by'"),
    ("{% regroup m by k is g %}", "next-to-last argument to 'regroup' tag must be 'as'"),
    # --- url ----------------------------------------------------------------
    ("{% url %}", "'url' takes at least one argument, a URL pattern name."),
    # `kwarg_re.match("nope=")` keys nothing and takes `"nope="` as the value, so this is the
    # *filter* compiler's error. The "Malformed arguments to url tag" branch is unreachable
    # from any token, so no row carries its message; `tags/url.rs`'s unit test drives it.
    ("{% url 'view' k=v nope= %}", "Could not parse the remainder: '=' from 'nope='"),
    # --- for ----------------------------------------------------------------
    # The arity check fires first; `%s` is the raw `token.contents`, not a repr, not the name.
    ("{% for x in %}", "'for' statements should have at least four words: for x in"),
    # `bits[-2]` is `b`, so this check fires before the loop variables are split.
    ("{% for a b c %}", "'for' statements should use the format 'for x in y': for a b c"),
    # No comma: the split yields the one variable `"a b"`, invalid because of the space.
    (
        "{% for a b in y %}{% endfor %}",
        "'for' tag received an invalid argument: for a b in y",
    ),
    # `FILTER_SEPARATOR` is forbidden, so this is an *argument* error, not a filter to compile.
    (
        "{% for a|b in y %}{% endfor %}",
        "'for' tag received an invalid argument: for a|b in y",
    ),
    # --- cycle --------------------------------------------------------------
    ("{% cycle %}", "'cycle' tag requires at least two arguments"),
    # No `as` form has run, so the registry is absent: the unknown-name message with no period.
    ("{% cycle foo %}", "No named cycles in template. 'foo' is not defined"),
    ('{% cycle "a" "b" as c %}{% cycle c2 %}', "Named cycle 'c2' does not exist"),
    # `args[-3]` must be the `as` for this branch to be reachable.
    (
        "{% cycle a b as c d %}",
        "Only 'silent' flag is allowed after cycle's name, not 'd'.",
    ),
    # --- resetcycle ---------------------------------------------------------
    # The last two need `{% cycle %}` to exist; `{% resetcycle a b %}` is also above.
    ("{% resetcycle a b %}", "'resetcycle' tag accepts at most one argument."),
    (
        "{% resetcycle nope %}{% cycle 'x' 'y' %}",
        "Named cycle 'nope' does not exist.",
    ),
    ("{% resetcycle %}", "No cycles in template."),
    # --- ifchanged ----------------------------------------------------------
    # `parse(("else", "endifchanged"))` stops at the first `else`; the second then fails to
    # terminate the body's parse. The message is the base parser's, not the tag's.
    (
        "{% ifchanged %}{% else %}{% else %}{% endifchanged %}",
        "Invalid block tag on line 1: 'else', expected 'endifchanged'. "
        "Did you forget to register or load this tag?",
    ),
    # --- if -----------------------------------------------------------------
    # Three failure routes in one tag: smartif's expression parser (the first four), the
    # host's filter compiler (the next two), the base parser's terminator handling (the last
    # five). An empty condition and a dangling operator share the `EndToken.nud` raise site
    # (`smartif.py:149-150`), reached through `IfParser.expression`.
    ("{% if %}{% endif %}", "Unexpected end of expression in if tag."),
    ("{% if a and %}{% endif %}", "Unexpected end of expression in if tag."),
    # `IfParser.parse`'s exhaustion check (`smartif.py:198-200`), not a nud.
    ("{% if a b %}{% endif %}", "Unused 'b' at end of if expression."),
    # `and` in prefix position, so `TokenBase.nud` (`smartif.py:22-26`).
    ("{% if a and and b %}{% endif %}", "Not expecting 'and' in this position in if tag."),
    # Both are compiled during the parse by `TemplateIfParser.create_var`, so both are the
    # host's `compile_filter` failing — one on the filter lookup, one inside `Variable`.
    ("{% if a|does_not_exist %}{% endif %}", "Invalid filter: 'does_not_exist'"),
    ("{% if 1>2 %}{% endif %}", "Could not parse the remainder: '>2' from '1>2'"),
    # `do_if`'s own `parser.parse` with three terminators; the "Looking for one of" list is
    # Django's, in Django's order.
    ("{% if a %}", "Unclosed tag on line 1: 'if'. Looking for one of: elif, else, endif."),
    # `elif` is not a registered tag — the block parser never starts it.
    (
        "{% elif a %}",
        "Invalid block tag on line 1: 'elif'. "
        "Did you forget to register or load this tag?",
    ),
    # The terminator line's trailing bits, from `do_if`'s single `token.contents != "endif"`
    # check (`defaulttags.py:1013-1017`), which `else foo` reaches too because
    # `token.contents == "else"` is already false. The token is the `if`'s, not the
    # terminator's, because the error is raised on the tag's own stored token.
    ("{% if a %}x{% endif foo %}", 'Malformed template tag at line 1: "endif foo"'),
    ("{% if a %}x{% else foo %}y{% endif %}", 'Malformed template tag at line 1: "else foo"'),
    # A second `endif` is *not* Malformed: the first closed the tag, the second is unknown.
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
    # --- the messages only the *host* can raise -----------------------------
    # Every message above is a string Rust ports; these are not. Each is raised *inside* a
    # compile function the host runs, against the parser the shell handed it, so a row here
    # is evidence the seam carries a live Django parser rather than that Rust copied a literal.
    #
    # `{% load %}` proves it alone: `find_library` reads `parser.libraries`
    # (`defaulttags.py:1065-1075`) and embeds `"\n".join(sorted(parser.libraries))`; ours
    # matches only because both sides get the same `Engine.template_libraries`.
    (
        "{% load nope %}",
        "'nope' is not a registered tag library. Must be one of:\n" + LIBRARY_LIST,
    ),
    # A second name in the same tag, same raise site: `do_load` treats every bit after the
    # command as a library name when there is no `from` (`defaulttags.py:1126-1130`), so
    # `custom` resolves and `no_params` does not.
    (
        "{% load custom no_params %}",
        "'no_params' is not a registered tag library. Must be one of:\n" + LIBRARY_LIST,
    ),
    # The *other* raise site, reachable only through the `from` form (`load_from_library`,
    # `defaulttags.py:1092-1098`) — a different sentence, which is why both rows exist.
    (
        "{% load nope from custom %}",
        "'nope' is not a valid tag or filter in tag library 'custom'",
    ),
    # `parse_bits` (`library.py:388-473`) binds a `simple_tag`'s arguments, building every
    # message from the *function's* signature and the caller's tokens. Four spellings, four
    # messages; the keyword-only branch is unreachable for a library that declares none.
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
    # A `simple_block_tag` whose body is unclosed re-enters Django's parse, runs out of
    # tokens, and `Parser.unclosed_block_tag` pops `parser.command_stack` (`base.py:651`) —
    # a stack Django's own `Parser.parse` maintains and Rust, driving the outer loop, does not.
    (
        "{% load custom %}{% div %}a",
        "Unclosed tag on line 1: 'div'. Looking for one of: enddiv.",
    ),
    # --- the innermost token, when the failure is *inside* a host tag --------
    # The message alone cannot tell these apart from the row above: the same `now` failure
    # with the same text either way. What moves is the **token** — Django's `Parser.error`
    # sets `.token` on the first exception it sees and never replaces one already set
    # (`base.py:619-630`), so `do_div`'s nested parse attaches the `now a b` token and the
    # outer `self.error(div_token, e)` leaves it alone.
    #
    # Rust's matching guard (`Error::error`'s `token.or_else`,
    # `crates/template-core/src/parser/mod.rs:461-466`) is inert without the token crossing the
    # seam — it would see `None` and attach the **outer** token. Both
    # `test_every_case_carries_the_same_token*` assertions fail without the transport.
    (
        "{% load custom %}{% div %}{% now a b %}{% enddiv %}",
        "'now' statement takes one argument",
    ),
    # The same failure one frame deeper, because "the innermost wins" is a claim about
    # depth, and one level cannot distinguish it from "the first nested frame wins".
    (
        "{% load custom %}{% div %}{% div %}{% now a b %}{% enddiv %}{% enddiv %}",
        "'now' statement takes one argument",
    ),
    # The inner failure Django raises itself: `Parser.parse` finds a block tag it was not
    # told to stop for and goes through `invalid_block_tag` → `self.error(token, msg)`
    # (`base.py:632-648`). Same seam, second producer of the token, so the transport must be
    # in `call_tag`'s raise path rather than in a particular tag's error.
    (
        "{% load custom %}{% div %}{% badtag %}{% enddiv %}",
        "Invalid block tag on line 1: 'badtag', expected 'enddiv'. Did you "
        "forget to register or load this tag?",
    ),
    # --- the class, which the seam was also flattening ----------------------
    # `badtag`'s compile function raises `RuntimeError` (`bad_tag.py:6-8`) and Django's
    # `Parser.parse` re-raises the *same object* (`base.py:583-585`). Every other comparison
    # here compares text — `"I am a bad tag"` either way — so a seam that flattened it into a
    # `ParseError` passed all of them.
    #
    # `.django/tests/template_tests/tests.py:114-123` is the acceptance test that cannot be
    # fooled: it asserts `assertRaises(RuntimeError)`. The class is carried as data and
    # converted at `Engine.from_string`.
    ("{% load bad_tag %}{% badtag %}", "I am a bad tag"),
    # --- one row that fills a corpus gap -------------------------------------
    # The only input that reaches `loopvar_split` on an *empty* haystack: with four bits and
    # `is_reversed`, `in_index` is 1 and `bits[1]` *is* "in", so the check passes and
    # `bits[1..1]` is empty — `re.split(r" *, *", "")` runs and Django rejects the empty loop
    # variable. One row pins the `in_index == 1` arithmetic, the empty haystack, and
    # `is_invalid_loopvar` at once.
    (
        "{% for in y reversed %}x{% endfor %}",
        "'for' tag received an invalid argument: for in y reversed",
    ),
    # --- `{% block %}`'s four failure modes ---------------------------------
    # All four are `do_block`'s own (`loader_tags.py:221-241`), each a different producer:
    # the arity check, the duplicate-name check against `loaded_blocks`, the end-tag check
    # through `invalid_block_tag`, and the *parser*'s unclosed-block report rather than
    # anything in the tag. Here `bits` is `["block"]`, so the message names the command only.
    ("{% block %}x{% endblock %}", "'block' tag takes only one argument"),
    (
        "{% block a %}{% endblock %}{% block a %}{% endblock %}",
        "'block' tag with name 'a' appears more than once",
    ),
    # `endblock <name>` is acceptable, so this is the *mismatch*: `b` is not `a`. The
    # expected list is `get_text_list`'s " or " join of the two spellings (`loader_tags.py:239-241`).
    (
        "{% block a %}x{% endblock b %}",
        "Invalid block tag on line 1: 'endblock', expected 'endblock' or "
        "'endblock a'. Did you forget to register or load this tag?",
    ),
    # Unclosed: the report comes from `unclosed_block_tag` and names the tag left open, with
    # the end-tag list joined by ", " rather than `get_text_list`'s " or ".
    (
        "{% block a %}x",
        "Unclosed tag on line 1: 'block'. Looking for one of: endblock.",
    ),
    # --- `{% extends %}` -----------------------------------------------------
    # Three producers. The third is a *rule*, not a check in the tag: `extend_nodelist`
    # refuses a `must_be_first` node once the nodelist holds anything that is not a
    # `TextNode` (`base.py:602-606`), and `ExtendsNode.must_be_first = True`
    # (`loader_tags.py:87`) makes `{% extends %}` the only built-in that sets it. Its message
    # is the token's contents and the origin, which is why the two harness helpers take an
    # `origin`. The first two are one arm: `bits` is `["extends"]` and
    # `["extends", '"a"', '"b"']`, neither of length two.
    ("{% extends %}", "'extends' takes one argument"),
    ('{% extends "a" "b" %}', "'extends' takes one argument"),
    # The duplicate: a scan of the nodelist `do_extends` just parsed, not of the siblings —
    # the **token is the first `{% extends %}`**, because that is the frame the check runs in.
    (
        '{% extends "a" %}{% extends "b" %}',
        "'extends' cannot appear more than once in the same template",
    ),
    # `must_be_first`, raising: a `{{ … }}` before it flips `contains_nontext`.
    (
        '{{ x }}{% extends "a" %}',
        '{% extends "a" %} must be the first tag in the template.',
    ),
    # Its control, which does **not** raise: a `TextNode` is exactly what `contains_nontext`
    # is defined against. This row is in the success corpus, named here so the pair is
    # findable from either side. `parse`'s default origin is the unknown one, so this needs
    # no origin; the other two of the three need a template *name* and are `ORIGIN_ERRORS`.
    (
        "{% extends './x.html' %}",
        "The relative path './x.html' cannot be evaluated due to an "
        "unknown template origin.",
    ),
    # --- `{% include %}` -----------------------------------------------------
    # One row per arm of `do_include`'s option loop (`LT:333-358`). All three messages
    # interpolate with `%r`, so the quoting is Django's and each row checks `py_repr` as much
    # as the arm. The duplicate is checked *before* the option is interpreted
    # (`if option in options`), which is why the last row is an unknown argument and the third
    # a duplicate rather than a second interpretation.
    (
        "{% include %}",
        "'include' tag takes at least one argument: the name of the template "
        "to be included.",
    ),
    (
        '{% include "a" with %}',
        '"with" in \'include\' tag needs at least one keyword argument.',
    ),
    ('{% include "a" only only %}', "The 'only' option was specified more than once."),
    ('{% include "a" nope %}', "Unknown argument for 'include' tag: 'nope'."),
    # `token_kwargs`'s `support_legacy=False` (`LT:341`) is what leaves `nope` in place to
    # *become* that unknown argument; under `{% with %}`'s `True` the same bits compile.
    ('{% include "a" with b=1 nope %}', "Unknown argument for 'include' tag: 'nope'."),
    # The same unknown-origin arm `{% extends %}` has, reached by the same `./` prefix,
    # because `construct_relative_path` runs before the flag is consulted.
    (
        "{% include './x.html' %}",
        "The relative path './x.html' cannot be evaluated due to an "
        "unknown template origin.",
    ),
]

# Rows whose message only an origin that **names a template** can produce —
# `construct_relative_path`'s second and third failures (`loader_tags.py:275-284`). A
# separate list because they need a third field. Each is
# `(source, origin_template_name, expected)`, graded message *and* token by
# `test_the_origin_carrying_rows_match_django`.
#
# The first row escapes the directory; the second lands on the origin itself, separating
# `current_template_name` from the resolved new name, which agree for every slash-free origin.
# Measured: with origin `/index.html`, `./index.html` reports the origin as given
# (`'/index.html'`) while the resolved name is `index.html`.
ORIGIN_ERRORS = [
    (
        "{% extends '../../x.html' %}",
        "index.html",
        "The relative path ''../../x.html'' points outside the file hierarchy "
        "that template 'index.html' is in.",
    ),
    (
        "{% extends './index.html' %}",
        "/index.html",
        "The relative path ''./index.html'' was translated to template name "
        "'/index.html', the same template in which the tag appears.",
    ),
    # The same escaping path through `{% include %}`, which shows `allow_recursion=True`
    # does **not** disable the check: the flag guards the *third* failure only. Single
    # quotes here, matching the `{% extends %}` row above, so the two are diffable.
    (
        "{% include '../../x.html' %}",
        "index.html",
        "The relative path ''../../x.html'' points outside the file hierarchy "
        "that template 'index.html' is in.",
    ),
]
