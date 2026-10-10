"""Template sources for comparing our parse tree against Django's.

Every source here must *parse successfully*: errors are Task 6's corpus.

There were no ``{{ variable }}`` tags anywhere in this file at M2 — variable
compilation is M3's, and back then such a template reached ``_native.parse`` and
was reported as unimplemented. M3's Task 5 supplies the arm, so the header's old
blanket claim is gone and variables are here for the reason Step 1 of the plan's
Task 10 gives: ``{{ … }}`` is the input that sets ``contains_nontext``.

Each entry below the first block pins one behaviour, named in its comment.
"""

# The libraries the `{% load %}` rows reach, by dotted path into `.django`'s own
# `template_tests`. Declared here, beside the rows that name them, rather than
# once per test module — **two spellings is how a corpus stops grading the
# engine**: `tests/test_parser_parity.py` and `tests/test_parser_errors.py` each
# build the `Engine` the two sides parse through, and until M6's Task 5 only the
# render path knew about `libraries=`, so the corpus graded a parser that could
# not resolve a single `{% load %}`.
LIBRARIES = {
    "custom": "template_tests.templatetags.custom",
    "testtags": "template_tests.templatetags.testtags",
    "tag_27584": "template_tests.templatetags.tag_27584",
    # The one pinned library whose tag raises something that is **not** a
    # `TemplateSyntaxError` — `badtag` raises `RuntimeError` at compile time
    # (`bad_tag.py:6-8`) — which is what makes it the only row that can tell a
    # seam carrying the exception's class from one that flattens it. It is here
    # rather than in a private fixture because `.django/tests/template_tests/
    # tests.py:114-123` asserts exactly this: `Engine.from_string("{% load
    # bad_tag %}{% badtag %}")` must raise `RuntimeError`, and that test is in
    # the 1547.
    "bad_tag": "template_tests.templatetags.bad_tag",
    # The stdlib library the M12 `blocktranslate` rows reach. A dotted path like
    # the rest, so both sides get the *same* module-level `Library` object —
    # `django.templatetags.i18n` resolves to Django's own file here (this module
    # is not the shim's), and its node classes are the ones the materialiser's
    # `NODE_CLASSES` builds from our descriptors.
    "i18n": "django_rust_templates.engine.templatetags.i18n",
}


TEMPLATES = [
    # --- text, and the things that are not tags ----------------------------
    "",
    "hello",
    "a\nb\n",
    "  leading and trailing  ",
    "line one\nline two\nline three",
    "a{{",  # an unmatched opener is text, not a tag
    "{#",  # as is an unmatched comment opener
    "{# c #}",
    "a{# c #}b",
    "{#}{#}",  # empty comments, adjacent
    "{# {% if %} #}",  # a block tag inside a comment is not a tag
    "{# {{ x }} #}",
    "héllo 🎉",  # contents and positions are counted in characters
    # --- csrf_token and debug: no arguments are read -----------------------
    "{% csrf_token %}",
    "{% csrf_token junk %}",
    "x{% csrf_token %}y",
    "{% debug %}",
    "{% debug junk more %}",
    # --- templatetag: all eight bits, then two in sequence -----------------
    "{% templatetag openblock %}",
    "{% templatetag closeblock %}",
    "{% templatetag openvariable %}",
    "{% templatetag closevariable %}",
    "{% templatetag openbrace %}",
    "{% templatetag closebrace %}",
    "{% templatetag opencomment %}",
    "{% templatetag closecomment %}",
    "{% templatetag openblock %}{% templatetag closeblock %}",
    # --- now: quoted, unquoted, empty, and the `as` form -------------------
    '{% now "jS F Y H:i" %}',
    '{% now "Y" %}',
    '{% now "Y" as year %}',
    '{% now "Y" as year %}x',
    '{% now "a b" %}',  # one quoted phrase, so `split_contents`, not `split`
    "{% now 5 %}",  # unquoted: first and last characters are dropped
    "{% now abc %}",
    '{% now "" %}',
    # --- autoescape: both settings, empty, nested, and around a non-text node
    "{% autoescape on %}x{% endautoescape %}",
    "{% autoescape off %}x{% endautoescape %}",
    "{% autoescape on %}{% endautoescape %}",
    "{% autoescape off %}{% autoescape on %}x{% endautoescape %}{% endautoescape %}",
    "a{% autoescape on %}{% csrf_token %}{% endautoescape %}b",
    "{% autoescape on %}{% comment %}c{% endcomment %}x{% endautoescape %}",
    # --- comment: the body is skipped, never parsed -----------------------
    "{% comment %}{% endcomment %}",
    "{% comment %}anything{% endcomment %}",
    # `{% nope %}` inside a comment must not be reported: `skip_past`, not
    # `parse` (Review Focus item 9).
    "{% comment %}{% if %}{{ x }}{% nope %}{% endcomment %}x",
    "a{% comment %}b{% endcomment %}c",
    # --- spaceless: text, empty, nested, and around a non-text node --------
    "{% spaceless %}  a  {% endspaceless %}",
    "{% spaceless %}{% endspaceless %}",
    "{% spaceless %}{% spaceless %}a{% endspaceless %}{% endspaceless %}",
    "{% spaceless %}{% csrf_token %}{% endspaceless %}",
    # --- verbatim: the body is rendered at parse time, and kept safe -------
    "{% verbatim %}a{% endverbatim %}",
    "{% verbatim %}{% endverbatim %}",
    "{% verbatim %}{{ x }}{% endverbatim %}",
    # Inside verbatim, `{% if %}` is text: a *lexer* behaviour the tree depends
    # on, and one Django's own lexer also has.
    "{% verbatim %}{% if %}x{% endif %}{% endverbatim %}",
    "{% verbatim %}  spaces & {braces}  {% endverbatim %}",
    "{% verbatim %}a{% endverbatim %}{% verbatim %}b{% endverbatim %}",
    # --- combinations, to reach nesting and dispatch together --------------
    "{% spaceless %}{% verbatim %}{{ x }}{% endverbatim %}{% endspaceless %}",
    "{% autoescape off %}{% spaceless %}{% templatetag openbrace %}"
    "{% endspaceless %}{% endautoescape %}",
    "{% comment %}{% spaceless %}{% endcomment %}{% spaceless %}x{% endspaceless %}",
    "text{% csrf_token %}more{% debug %}end",
    # --- filter: the synthetic `var|…` source, and its body -----------------
    # `do_filter` does not compile the token it was given; it rebuilds a
    # variable around the argument (`defaulttags.py:745`), so the descriptor's
    # `FilterExpr.token` is `"var|lower"` and `var` names nothing in the
    # template.
    "{% filter lower %}X{% endfilter %}",
    "{% filter lower|capfirst %}X{% endfilter %}",
    "{% filter lower %}{% endfilter %}",
    "{% filter lower %}{{ x }}{% endfilter %}",
    # --- firstof: the `as` form, the fallback literal, and the bare list ----
    "{% firstof a %}",
    "{% firstof a b c %}",
    "{% firstof a b as v %}",
    '{% firstof a "fallback" %}',
    "{% firstof a|lower b|upper %}",
    "{% firstof a b as v %}x",
    # `as` is read from the *end*, and the argument count is checked before it
    # is stripped, so this is a legal tag with no variables at all — measured.
    "{% firstof as v %}",
    # --- with: the kwargs form, the legacy `as` form, and both together -----
    "{% with a=1 %}{{ a }}{% endwith %}",
    "{% with a=1 b=2 %}{{ a }}{% endwith %}",
    "{% with a=1 %}{% endwith %}",
    "{% with a=1 b=2 %}{{ a }}{{ b }}{% endwith %}",
    # `token_kwargs` compiles the value side, so the filter chain is part of
    # the tree.
    "{% with a=1|add:2 %}{{ a }}{% endwith %}",
    # The legacy form: `support_legacy=True` is what makes it reachable, and
    # the name is the third bit while the value is the first (`base.py:1200`).
    "{% with a as b %}{{ b }}{% endwith %}",
    "{% with a as b and c as d %}{{ b }}{% endwith %}",
    # --- widthratio: all three operands are filter expressions -------------
    "{% widthratio a b 100 %}",
    "{% widthratio a b 100 as w %}",
    "{% widthratio a|add:1 b 100 %}",
    # The width is `compile_filter`ed too, not `int()`ed — this row is what
    # separates the two.
    "{% widthratio a b c|add:1 %}",
    # --- lorem: each optional bit, and the defaults ------------------------
    "{% lorem %}",
    "{% lorem 3 %}",
    "{% lorem 2 p %}",
    "{% lorem 2 w random %}",
    # A lone `w` is the *method*: the method pop runs before the count pop.
    "{% lorem w %}",
    "{% lorem random %}",
    # --- regroup: the derived expression is `var_name + "." + key` ----------
    "{% regroup m by k as g %}",
    # The pin: the compiled `expression` source is `g.k`, not `k`, so the corpus
    # row's value is the *joined* string and a body that compiled `bits[3]`
    # would diverge here while parsing cleanly everywhere else.
    '{% regroup m|dictsort:"k" by k as g %}',
    "{% regroup m by k as g %}{{ g }}",
    "{% regroup m by k.v as grouped %}",
    # --- url: positional and kwarg bits, and the trailing `as` --------------
    "{% url 'view' %}",
    "{% url 'view' a b %}",
    "{% url 'view' k=v %}",
    "{% url 'view' a k=v %}",
    "{% url 'view' as u %}",
    "{% url 'view' a k=v as u %}",
    "{% url 'view' k=v|default:'x' %}",
    # A variable view name: `compile_filter` compiles it, nothing resolves it.
    "{% url url_name a %}",
    # `\w` admits digits, so `1=2` is a kwarg named `1`.
    "{% url 'view' 1=2 %}",
    # --- for: the end-counted `in`, two nodelists, and the loop variables ---
    "{% for x in y %}{% endfor %}",
    # `reversed` is read from the end, so the `in` moves one bit left: the
    # sequence is still `y` and the loop variable is still `x`.
    "{% for x in y reversed %}{% endfor %}",
    # The join-then-split (`defaulttags.py:886`): all three comma spellings give
    # `["a", "b"]`, and a body that split the bits directly would give one loop
    # variable for the first two.
    "{% for a, b in y %}{% endfor %}",
    "{% for a,b in y %}{% endfor %}",
    "{% for a , b in y %}{% endfor %}",
    # The `{% empty %}` branch is a second *nodelist*, not a flag.
    "{% for x in y %}{% empty %}none{% endfor %}",
    # A nested loop, so `nodelist_loop` holds a real `ForNode`.
    "{% for a, b in y %}{% for c in d %}{{ c }}{% endfor %}{% endfor %}",
    # --- ifchanged: the variadic `_varlist` and the false branch ------------
    "{% ifchanged %}x{% endifchanged %}",
    "{% ifchanged %}x{% else %}y{% endifchanged %}",
    # Two values, so `_varlist` is a tuple of arity 2 — the shape the factory
    # exists to rebuild (`IfChangedNode` is the only variadic M5 node).
    "{% ifchanged a b %}x{% else %}y{% endifchanged %}",
    # Where the tag is meant to be used, and the only place its state is
    # interesting at render time.
    "{% for x in y %}{% ifchanged %}{{ x }}{% endifchanged %}{% endfor %}",
    # --- cycle: the five shapes, and the identity they share ----------------
    "{% cycle 'a' 'b' %}",
    "{% cycle 'a' 'b' 'b' %}",
    "{% cycle 'a' 'b' as c %}",
    "{% cycle 'a' 'b' as c silent %}",
    # A name reference: the same node again, which is why `describe` cannot be
    # a tree comparison here — both positions must be the *one* object.
    "{% cycle 'a' 'b' as c %}{% cycle c %}",
    # `cycle` is what gives `resetcycle` a success path, so these two rows are
    # also what empties `test_parser_parity`'s `deferred` set.
    "{% cycle 'a' 'b' as c %}{% resetcycle %}",
    "{% cycle 'a' 'b' as c %}{% resetcycle c %}",
    "{% for x in y %}{% cycle 'a' 'b' %}{% endfor %}",
    # Four bits is the *anonymous* form even with an `as` in it: `{% cycle a as
    # b %}` compiles three values and `as` is the middle one. Measured.
    "{% cycle a as b %}",
    # `args[-3]` is `b`, so this is the non-silent `as` form named `silent` —
    # not the silent flag. Measured.
    "{% cycle a b as silent %}",
    # --- if: one branch, two, and the else that has no condition -------------
    # The condition is not a `FilterExpression` at the seam Django uses: it is a
    # `TemplateLiteral` wrapping one, and an operator node wrapping those. These
    # rows are what makes `describe` grow an arm for the second kind.
    "{% if a %}x{% endif %}",
    "{% if a %}x{% else %}y{% endif %}",
    "{% if a %}x{% elif b %}y{% endif %}",
    "{% if a %}x{% elif b %}y{% elif c %}z{% else %}w{% endif %}",
    # The operator tree, in both spellings: `not` is a prefix operator and `and`
    # binds looser than it, so the nesting is the thing being compared.
    "{% if a and b or not c %}x{% endif %}",
    # A filter inside an operand: the filter *name* is checked through the host,
    # and the `FilterExpression` itself is the host's own object.
    "{% if a|lower == 'x' %}x{% endif %}",
    "{% if a > 1 and b < 2 %}x{% endif %}",
    "{% if not a %}x{% endif %}",
    # --- variables, and the `contains_nontext` they set ---------------------
    # M3's half of §10.4's ordering gate. `{{ … }}` is what sets
    # `contains_nontext` — measured: `text{% extends %}` is legal and
    # `{{ x }}{% extends %}` is not — so variable-versus-text and
    # variable-versus-tag are the cases that exercise the rule's input.
    # (The `{% extends %}`-specific case is M7's; see the plan's Task 10.)
    "{{ x }}{% csrf_token %}",
    "{{ x }}{% csrf_token %}y",
    "a{{ x }}",
    "{% autoescape on %}{{ x }}{% endautoescape %}{% autoescape on %}b{% endautoescape %}",
    "{% spaceless %}{{ x }}{% endspaceless %}",
    # --- the variable syntax itself, which M3's Task 5 added ----------------
    "{{ a.b.c }}",
    "{{ a.0 }}",
    "{{ a|upper }}",
    "{{ a|default:'z'|upper }}",
    "{{ 'lit' }}",
    "{{ 5 }}",
    "{{ a.0|join:', ' }}",  # a quoted argument containing the filter separator
    "{{ x }}{{ y }}",
    # --- M6: the tags the *host* compiles -----------------------------------
    # Every row above is compiled by Rust's own registry, so before this block
    # the corpus had no tree containing a host node at all — and the coverage
    # test below cannot demand one, because it derives its demand from
    # `builtin_tag_names_including_stubs()`, which is Rust's registry and
    # structurally blind to
    # every tag the host owns. `{% load %}` is how these rows reach their
    # library, and it is itself host-owned (`defaulttags.py:1099`).
    #
    # The libraries are `LIBRARIES` below, by dotted path into `.django`'s own
    # `template_tests`, and they must stay that way: `Engine(libraries=…)`
    # resolves a path to `import_module(...).register`, so both sides get the
    # *same* `Library` object, and a `SimpleNode` holds `func` compared by
    # identity.
    #
    # `extra_data` writes `parser.extra_data["extra_data"]` and returns a
    # `TextNode` (`custom.py:396-399`); its tree is unremarkable on purpose —
    # the state it writes is Task 4's, and this row is here so that a host
    # compile function *may* touch the parser without breaking the tree.
    "{% load custom %}{% extra_data %}",
    "{% load custom %}{% no_params %}",
    "{% load custom %}{% no_params %}x",
    "{% load custom %}{% one_param x %}",
    # A `simple_block_tag`: `library.py:211-212` re-enters Django's own
    # `Parser.parse` for the body, and `delete_first_token` removes the end tag
    # `parse` left on the stack.
    "{% load custom %}{% div %}a{% enddiv %}",
    "{% load custom %}{% div %}a{% enddiv %}extra",
    # A second library, so a row cannot pass by reaching `custom` through some
    # accident of the first.
    "{% load testtags %}{% echo x %}ok",
    "{% load custom %}{% no_params %}{% load testtags %}{% echo y %}",
    # The re-entrant one: `badtag` calls `parser.parse(("endbadtag",))` and
    # `delete_first_token` (`tag_27584.py:8-9`). It raises at *render* time
    # (`BadNode.render`), so the parse succeeds and the tree is comparable —
    # which is what makes it a success row rather than one for the error corpus.
    "{% load tag_27584 %}{% badtag %}{% endbadtag %}",
    # The host-owned builtin: `querystring` is Django's `simple_tag`
    # (`defaulttags.py:1286-1287`) and not in Rust's registry at all.
    "{% querystring page=2 %}",
    # --- M7: `{% block %}`, which is Rust's again ---------------------------
    # The first entry in `REGISTRY` that arrived from the *absent* list rather
    # than from a stub, so these rows are the parse that has to look the same
    # on both sides now that Rust compiles it. `block`'s own state
    # (`parser.__loaded_blocks`) is Rust's `Parser::loaded_blocks`; the
    # duplicate-name and end-tag checks are the error corpus's four rows.
    "{% block a %}x{% endblock %}",
    # `endblock <name>` is in `acceptable_endblocks` (`loader_tags.py:239`), so
    # this is the backwards-compatibility row, not an error.
    "{% block a %}x{% endblock a %}",
    # Text on both sides, so the block node is neither first nor last.
    "A{% block a %}x{% endblock %}B",
    # A block nested in a block: the inner one is a child of the outer's
    # nodelist, and both names are recorded in the order they compiled.
    "{% block a %}{% block b %}x{% endblock %}{% endblock %}",
    # --- M7: `{% extends %}` ------------------------------------------------
    # The tag that **swallows the rest of the template**: `do_extends` ends with
    # an unbounded `parser.parse()` (`loader_tags.py:307`), so everything after
    # it is this node's child rather than its sibling. The first row is the
    # minimal case and the second is the shape that makes that visible — the
    # block is *inside* the ExtendsNode's nodelist, which is why
    # `ExtendsNode.__init__`'s `nodelist.get_nodes_by_type(BlockNode)` finds it.
    "{% extends \"base.html\" %}",
    "{% extends \"base.html\" %}{% block a %}x{% endblock %}",
    # The variable form, which is why this tag splits with `split_contents()`.
    "{% extends parent %}",
    # A leading TextNode does **not** make it non-first — `extend_nodelist`
    # flips `contains_nontext` only for a node that is not a `TextNode`
    # (`base.py:602-612`, the raise and the flag it reads). The `{{ }}` variant,
    # which does raise, is an error row.
    "x{% extends \"base.html\" %}",
    # --- M7: the walk `{% extends %}` drives, on the shape that would break a
    # name-driven one. `IfNode` answers `child_nodelists`' `"nodelist"` with a
    # **synthesised property** over `conditions_nodelists`
    # (`defaulttags.py:314-320`) and Rust emits those branches under empty
    # names, so a walk that visited only *named* nodelists would find nothing
    # here while Django finds the block — silently, in `ExtendsNode.blocks`.
    "{% if x %}{% block a %}x{% endblock %}{% endif %}",
    "{% extends \"base.html\" %}{% block a %}A{% endblock %}{% if x %}{% block b %}B{% endblock %}{% endif %}",
    # --- M7: `{% include %}` ------------------------------------------------
    # Unlike `{% extends %}`, this one **stops at its own token**: no trailing
    # parse, so what follows is a sibling. The four option shapes are here
    # because they are four different descriptor bodies — no options, `with`,
    # `only`, and both — and the second is the one whose *value* is a compiled
    # `FilterExpression` rather than text.
    "{% include \"a.html\" %}",
    "{% include \"a.html\" with b=1 %}",
    "{% include \"a.html\" with b=1 only %}",
    # The variable form, which is why this tag splits with `split_contents()`
    # too.
    "{% include template_name %}",
    # --- M12 Task 6b: `{% blocktranslate %}` / `{% blocktrans %}` -----------
    # The one node whose body is the token stream itself rather than a nodelist:
    # `BlockTranslateNode.singular`/`.plural` are **lists of `Token`**, so these
    # are the rows that first carry a `Token` through `describe` (see its `Token`
    # arm) — a value type with no `__eq__`, which is why they are parity rows and
    # not only render rows.
    #
    # The library path is `LIBRARIES["i18n"]` above. The minimal row first, then
    # one that puts both token kinds in the body, since a TEXT-only row cannot
    # tell a port that keeps the stream from one that keeps strings.
    "{% load i18n %}{% blocktranslate %}x{% endblocktranslate %}",
    "{% load i18n %}{% blocktranslate %}x{{ y }}{% endblocktranslate %}",
    # The alias, registered under the same body: the node records the *spelling*
    # (`tag_name`), which is what its error messages echo.
    "{% load i18n %}{% blocktrans %}x{% endblocktrans %}",
    # `count` gives both a counter variable and a counter expression and splits
    # the body at `{% plural %}` into a second token list.
    "{% load i18n %}{% blocktranslate count c=n %}a{% plural %}b{% endblocktranslate %}",
    # Every option at once: `with` (a compiled `FilterExpression` per entry),
    # `context` (one more), `trimmed` (a bool, not a string), and `asvar` (a
    # name), so the whole descriptor body is on the wire in one row.
    '{% load i18n %}{% blocktranslate with a=1 context "g" trimmed asvar v %}x{% endblocktranslate %}',
    # The legacy `with … as …` form (`support_legacy=True`), which reverses the
    # keyword's name and value — a port that calls `token_kwargs` without the
    # flag compiles `a` as the name and `b` as the value and still parses.
    "{% load i18n %}{% blocktranslate with a as b %}x{% endblocktranslate %}",
    # --- M13 Task 3: `{% partial %}` ---------------------------------------
    # The row the coverage gate *demands* the moment the tag leaves the stub
    # set, and the reason it has to exist here rather than only in
    # `tests/test_partials_rust.py`: `test_the_corpus_covers_every_implemented_tag_with_a_success_path`
    # derives its demand from `including_stubs() - stub_names()`, so a tag that
    # lands without a corpus row reddens that test in the same commit. That is
    # the mechanism working, not a chore — it is what makes "every implemented
    # tag has a row" a derivation rather than a list someone maintains.
    #
    # **No definition in the source, on purpose.** `{% partial p %}` alone is a
    # template Django *parses* and only fails to *render* ("Partial 'p' is not
    # defined in the current template."), so it is a success row for the parse
    # corpus — the mirror of `{% badtag %}` above, whose failure is also at
    # render. A `{% partialdef %}` row could not be added here until Task 4,
    # since that tag was still a stub and raised `UnimplementedTag` before the
    # comparison could run; it is the row below.
    #
    # It is also the first row whose node carries a value that is neither a
    # node nor a plain value: `PartialNode.partial_mapping` is a
    # `DeferredSubDict`, a live view over parser state, which is why `describe`
    # has an arm for it (see its comment). Without that arm both sides compare
    # two live objects by identity and this row fails looking like a Rust bug.
    "{% partial p %}",
    # --- M13 Task 4: `{% partialdef %}` -------------------------------------
    # The row Task 3's note deferred, and the gate demanded it in the same
    # commit for the same reason: leaving the stub set removes the name from
    # `stub_names()`, so `including_stubs() - stub_names()` grew by a name and a
    # name with no happy-path row reddens the coverage test.
    #
    # **The definition is used, not merely declared**, so the row crosses the
    # seam in both directions: `{% partialdef %}` calls
    # `Host::register_partial_def` and `{% partial %}` calls
    # `Host::partial_node`, and the mapping the node holds resolves against what
    # the definition stored. It is still a *parse* success row — nothing renders
    # here — but a shell that stored the partial and then failed to hand the same
    # nodelist back would show up as a description diff rather than a difference
    # no one looks at.
    "{% partialdef p %}body{% endpartialdef %}{% partial p %}",
]


# --- the randomised pass ---------------------------------------------------
#
# The corpus and the implementation share an author, which is the standing
# weakness of a parity test written this way. The answer is a second, generated
# corpus: it reaches combinations nobody thought to write down, and it is seeded
# so any failure reproduces exactly.

RANDOM_SEED = 20261008
RANDOM_CASES = 400

# No `{`, `}`, `%` or `#`, so no random text accidentally forms a tag. What the
# generator is for is *shape*: nesting depth, sibling order, and which nodelists
# end up holding non-text nodes.
#
# U+001C..U+001F are here because Python's `str.isspace()` accepts them and
# Rust's `char::is_whitespace()` does not, which is the M1 divergence the parity
# suite exists to catch; `é` and 🎉 keep non-ASCII in the text, and the quote
# characters keep quoting in play. All four separators, not one, because a
# boundary fixed for U+001C could still be wrong for U+001D..U+001F.
RANDOM_ALPHABET = "abc \n\t'\"\u001c\u001d\u001e\u001fé🎉"

INLINE_TAGS = [
    "{% csrf_token %}",
    "{% debug %}",
    "{% templatetag openblock %}",
    "{% templatetag closebrace %}",
    '{% now "Y" %}',
    '{% now "jS F Y H:i" as when %}',
]

# The M2 round's own recorded residue was that all 400 generated templates were
# variable-free outside `{% verbatim %}`/`{% comment %}`, so a green randomised
# pass said nothing about M3's code. These are the *shapes* rather than one
# shape: a generator that only ever produced `{{ x }}` would leave dotted
# lookup, indexing and filters — the whole of M3 — unexercised, which is what
# `RandomisedParityTests.test_the_generator_reaches_variables` floors.
VARIABLES = (
    "{{ x }}",
    "{{ a.b.c }}",
    "{{ a.0 }}",
    "{{ a|upper }}",
    "{{ a|default:'z'|upper }}",
    "{{ 'lit' }}",
    "{{ 5 }}",
    "{{ a.0|join:', ' }}",
)

BLOCK_TAGS = [
    ("{% autoescape on %}", "{% endautoescape %}"),
    ("{% autoescape off %}", "{% endautoescape %}"),
    ("{% spaceless %}", "{% endspaceless %}"),
    ("{% verbatim %}", "{% endverbatim %}"),
    ("{% comment %}", "{% endcomment %}"),
]


def random_text(rng):
    return "".join(rng.choice(RANDOM_ALPHABET) for _ in range(rng.randint(0, 8)))


def random_tag_with_argument(rng):
    """A tag whose *argument* is random text.

    Without this, `RANDOM_ALPHABET` only ever reaches text nodes, and the
    separator table that M1 got wrong is exercised on the one path where it does
    not matter. The table bites in `smart_split`, so the argument has to be a tag
    argument for the alphabet to be worth widening.

    `{% autoescape %}` is the M2 tag with exactly one argument, so a
    disagreement here is a parser bug rather than a milestone boundary. Measured
    on the pinned tree: Python's `re` `\\s` and `str.isspace()` are the same 29
    code points, so `x<U+001C>on` splits into two arguments for Django and must
    for us; `%`, `{`, `}` and `#` are absent from the alphabet, so the random
    text cannot terminate the tag early, while `'` and `"` stay in play and make
    quoting — including unbalanced quoting, which `smart_split` handles by
    failing to match the quoted alternative rather than raising — part of the
    input space.
    """
    return "{% autoescape " + random_text(rng) + " %}x{% endautoescape %}"


def random_piece(rng, depth):
    if depth >= 3:
        return random_text(rng)

    roll = rng.random()
    if roll < 0.35:
        return random_text(rng)
    if roll < 0.52:
        return rng.choice(INLINE_TAGS)
    if roll < 0.68:
        return random_tag_with_argument(rng)
    if roll < 0.84:
        return rng.choice(VARIABLES)

    opening, closing = rng.choice(BLOCK_TAGS)
    return opening + random_piece(rng, depth + 1) + closing


def random_template(rng):
    return "".join(random_piece(rng, 0) for _ in range(rng.randint(0, 5)))


def parity_sources():
    """The corpus a parity row measures over: the written one, then the generated.

    Both corpora, because they differ — the generator reaches ``Parser.error``
    and ``Parser.invalid_block_tag`` and the hand-written one does not — under
    the one seed, so any failure reproduces exactly.

    A function rather than three constants each caller recombines, because two
    rows that both rebuild "the corpus" are two corpora the day one of them is
    edited. ``tests/test_interposition.py`` re-measures the pin over this and
    ``tests/test_compared_path_instrument.py`` cross-checks the pin with a
    second observer over it; a drift between those two runs would show up as the
    instruments disagreeing when it was the input that moved.
    """
    import random

    rng = random.Random(RANDOM_SEED)
    return list(TEMPLATES) + [random_template(rng) for _ in range(RANDOM_CASES)]
