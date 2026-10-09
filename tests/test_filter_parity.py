"""M4's filter parity test: one corpus row, two engines, one comparison.

Both sides run on the pinned tree. Django's side is its real `FilterExpression`
over a real `Context`, driven by its real filter registry; ours is
`_native.filter`, which is the seam the fan-out's bodies are written against.

**"Django's side" took two readings to get right, and the first one was the
trap.** Until M11 Task 2 the probe built `django.template.base.FilterExpression`
— which has been *our* file since M9 — and the comparison was still honest for a
reason nothing in this file asserted: `resolve` was upstream's text, so the code
that ran was Django's. Task 2's routing arm lands on `resolve`'s own lines, after
which that import compares Rust against Rust and passes for every row. The probe
now reads the **pinned** `FilterExpression` out of `.django` (through the same
`load_pinned` mechanism the other parity harnesses use), and
`test_the_comparison_target_is_not_rust_owned` is what watches it.

The filter **callables** are a different story and are still the interposed
module — `engine/defaultfilters.py`, pin plus a marker, so their bodies are
upstream's text. That file is transcribed rather than pristine, so it is
warranted by `tests/parity_target.py` instead of by a path check.

**The comparison is `(type name, value)` and a separate safety flag**, per §D5,
and the type half is compared *here* rather than in Rust on purpose: a Rust
`Value` cannot name a Python class, and the classes that matter —
`SafeString` against `str` — are the ones an escaping bug is made of. `str` and
`SafeString` hold the same text and differ only in what the template does with
them later, so a corpus that compared `==` alone would pass while the flag that
decides `|upper` from `|lower` was inverted.

**The harness is `scripts/filter_probe.py`, loaded by path rather than
re-implemented.** The probe is the same comparison for one case typed by hand;
this is that comparison applied to `CASES`. Two implementations would drift, and
the drift would be invisible in the worst way — the instrument you reach for
while writing a body would agree with the Rust side for a reason the gate does
not share. Loading it by path is the idiom `tests/test_filter_expr_parity.py`
already uses for the materialiser, and it keeps the probe out of the suite:
`unittest discover -p 'test_*.py'` does not collect anything under `scripts/`.
"""

import importlib.util
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import Context, Engine  # noqa: E402
from django.template.base import Parser  # noqa: E402
from django.utils.safestring import SafeData  # noqa: E402

from django_rust_templates import _native  # noqa: E402

from tests.filter_cases import CASES, ERROR_CASES, case_value  # noqa: E402

_PROBE_SPEC = importlib.util.spec_from_file_location(
    "django_rust_templates.filter_probe",
    PROJECT_ROOT / "scripts" / "filter_probe.py",
)
probe = importlib.util.module_from_spec(_PROBE_SPEC)
sys.modules[_PROBE_SPEC.name] = probe
_PROBE_SPEC.loader.exec_module(probe)


def implemented_filters() -> set[str]:
    """The registry names whose body is no longer a stub, **measured**.

    A filter is a stub iff calling it raises `UnimplementedTag` naming *that*
    filter — the same rule, on the other side of the FFI, as the Rust guard's
    `stub_error` (`filters/mod.rs`), which matches the kind *and* the name
    because a stub that named the wrong filter is exactly the copy-paste mistake
    a 57-row table invites.

    Any other exception means a real body: a body handed `None` is allowed to
    refuse it, and `add` refusing `None` must not read as "still a stub". So the
    only outcome treated as stubbed is the one that says so by name.

    **Nothing here lists the stubs.** Which filters are stubs is a Rust fact, and
    its authoritative statement is `STILL_STUBBED` in `filters/mod.rs`, where the
    guard checks it against the bodies themselves in both directions. Restating
    it as a Python literal would add a third copy whose only possible failure is
    drift between copies — a failure that costs a debugging session at commit
    time and catches nothing a body-level guard does not. So this measures.
    """
    all_names = set(_native.filter_names())
    stubs = set()
    for name in sorted(all_names):
        try:
            _native.filter(name, None, ())
        except _native.UnimplementedTag as error:
            if f"'{name}'" not in str(error):
                raise AssertionError(
                    f"`{name}` raised `UnimplementedTag` naming a different "
                    f"filter: {error}. A stub body's `run:` line is wrong, and "
                    f"the Rust guard matches on the name for this reason — but "
                    f"it matches the *registry* name, so this is the shape a "
                    f"copy-paste error takes on the way here."
                ) from error
            stubs.add(name)
        except Exception:
            # A real body, refusing `None`. Deliberately broad: the point is
            # that it *answered*, and enumerating the ways a filter may object to
            # `None` would be a second, weaker copy of the body's own logic.
            pass
    return all_names - stubs


def case_arg(case):
    """The Python object the row's argument **is**, or `NO_ARG` for none.

    The value half of [`arg_token`], and the one the Rust side is handed: our
    `_native.filter` takes resolved values, so a variable argument is just its
    object. Kept beside the token half so the two cannot disagree about which key
    a row used.

    `probe.NO_ARG` and not `None`, because `None` is a legal argument: a row
    declaring `"arg": None` means `{{ x|f:None }}`, which is `func(obj, None)` in
    Django and a different call from `func(obj)` — `floatformat` raises
    `int(None)`'s `TypeError` on it, where the bare form answers `'6666.7'`.
    """
    if "arg_variable" in case:
        return case["arg_variable"]
    if "arg" in case:
        return case["arg"]
    return probe.NO_ARG


def arg_token(case):
    """The argument's template text, and the context names it needs. `(text, extra)`.

    Two shapes, and a row picks between them for one reason: `template_literal`
    can render only a `str`, number, `bool` or `None` as a template **constant**,
    and Django has no list or object literal in a filter argument — so
    `pluralize`'s `[1]` and `[","]`, the rows that settle `comma_bits`'s fusion,
    cannot be `arg` rows at all. A row names such an argument twice:
    `arg_variable` holds the object, and the token carries the bare name,
    which `Variable` resolves as a lookup.

    **A variable argument is deliberately not `mark_safe`d, and refusing a `str`
    here is what keeps that honest.** `resolve` marks a constant
    (`base.py:788-792`) and a lookup is not one, so a `str` under `arg_variable`
    would reach Django unmarked and ours marked — the two sides given *different
    inputs*, which is the raw-token defect this corpus has already been fixed for
    once. Every non-`str` shape is unaffected: `rust_answer` marks only a `str`.
    A row wanting a marked sequence has no template that produces it and should
    not be written; the assertion says so rather than letting the mismatch in.
    """
    if "arg_variable" in case:
        value = case["arg_variable"]
        if isinstance(value, str):
            raise AssertionError(
                f"{case['name']}: a `str` under `arg_variable` would be a "
                f"variable lookup and so unmarked, while our side marks every "
                f"`str` argument — the two engines would be given different "
                f"inputs. Use `arg` for a `str`."
            )
        return "argvar", {"argvar": value}
    if "arg" in case:
        return probe.template_literal(case["arg"]), {}
    return probe.NO_ARG, {}


class FilterParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # `Context` needs no settings, but `effective_use_tz` reads `USE_TZ` and
        # the loop's `use_tz` is genuinely `None`-means-settings. Configured in
        # `setUpClass` rather than at import for the reason
        # `tests/test_render_parity.py` gives: a module-level
        # `settings.configure` changes what every module imported after this one
        # sees, and the rest of this suite deliberately runs unconfigured.
        probe.ensure_settings()
        cls.engine = Engine()
        cls.parser = Parser(
            tokens=[],
            libraries=cls.engine.template_libraries,
            builtins=cls.engine.template_builtins,
        )

    def render_django(self, case):
        """Django's answer, through `resolve`'s loop, with the token it used.

        The argument goes through [`arg_token`] because `django_answer` takes
        **template text**, not a Python object: a `str` argument has to be
        rendered as the constant `"b"` to become a `constant_arg` and be
        `mark_safe`d by `resolve` (`base.py:788-792`), where the bare `b` would be
        a variable lookup. Task 4's `cut` rows are the corpus's first with an
        argument, so this path had never run; it was passing `case["arg"]` raw,
        which made every argument a lookup while `test_every_case` asserted the
        token was the constant. Both sides now agree on what `x|cut:"b"` means.
        """
        given, extra = arg_token(case)
        return probe.django_answer(
            case["filter"],
            case_value(case),
            given,
            case.get("context"),
            extra,
            case.get("libraries"),
        )

    def render_rust(self, case, context):
        return probe.rust_answer(
            case["filter"], case_value(case), case_arg(case), context
        )

    def settings_for(self, case):
        """The row's `settings` override, wrapped around **both** engines' calls.

        A row's answer can turn on a setting — `floatformat`'s grouping and
        separator, `filesizeformat`'s byte unit word, `yesno`'s default
        arguments — and a setting that reaches one side and not the other is
        indistinguishable from a body that ignored it. So the override is entered
        for the pair rather than for either call, and it is entered here rather
        than inside a body because a body cannot see a setting at all: its whole
        contact with the outside world is the host trait.

        **A settings-bearing row must carry `expected`, and the literal is the
        test.** Both engines are handed the same override, so a misspelt or
        ineffective setting name moves them *together* and every comparison above
        stays green — measured: `USE_THOUSAND_SEPERATOR` (sic), the natural typo,
        moved nothing anywhere. Only the pinned literal can tell a live setting
        from a dead one, which is why this is a rule and not a courtesy.

        `nullcontext` rather than an empty `override_settings()`: the latter
        still fires `setting_changed` for no setting, which is a real signal to
        anything listening and a different question from "no override".
        """
        if case.get("settings"):
            from django.test import override_settings

            return override_settings(**case["settings"])
        return nullcontext()

    def assert_same(self, case, django_result, rust_result, membership=False):
        self.assertEqual(
            type(django_result).__name__,
            type(rust_result).__name__,
            f"{case['name']}: type differs — Django says "
            f"{type(django_result).__name__} and we say "
            f"{type(rust_result).__name__}",
        )
        if membership:
            # A row whose answer is chosen **at random** asserts membership and
            # says so in its name (`random/`), because an equality row would be
            # green about one time in three. The row's own value is the
            # population, so both sides are checked against it rather than
            # against each other — and a row that declared a `value_factory`
            # could not do this without consuming a third one, so membership
            # rows are list-valued only.
            population = case["value"]
            for side, result in (("Django", django_result), ("ours", rust_result)):
                self.assertIn(
                    result,
                    population,
                    f"{case['name']}: {side} answered {result!r}, which is not "
                    f"in the population {population!r}",
                )
        else:
            self.assertEqual(
                django_result,
                rust_result,
                f"{case['name']}: value differs",
            )
        self.assertEqual(
            isinstance(django_result, SafeData),
            isinstance(rust_result, SafeData),
            f"{case['name']}: safety differs — Django says "
            f"{isinstance(django_result, SafeData)} and we say "
            f"{isinstance(rust_result, SafeData)}. The text is identical either "
            f"way, so this is the comparison a rendered template cannot make.",
        )
        if isinstance(django_result, list):
            # A list's *items* carry types a plain `==` flattens: `["1"]` and
            # `[1]` are different answers, and a sequence filter that returned
            # the wrong one of those renders the same in most templates.
            self.assertEqual(
                [type(item).__name__ for item in django_result],
                [type(item).__name__ for item in rust_result],
                f"{case['name']}: item types differ",
            )

    def test_every_case(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                with self.settings_for(case):
                    django_result, expression, context = self.render_django(case)
                    rust_result = self.render_rust(case, context)
                self.assert_same(
                    case,
                    django_result,
                    rust_result,
                    membership=case.get("membership", False),
                )
                if "expected" in case:
                    # The literal as well as the comparison, on the rows whose
                    # answer turns on a `context` key. A body that ignored the
                    # key would move Django's answer and ours together — drop
                    # `autoescape` and both engines escape — so the comparison
                    # above stays green on exactly the rows that exist to test
                    # it. This string is the claim those rows make, which is why
                    # only the rows that *depend* on a key carry one; see
                    # `tests/filter_cases.py` on `expected`. Same rule as
                    # `test_render_parity.py`'s four pinned strings.
                    self.assertEqual(
                        rust_result,
                        case["expected"],
                        f"{case['name']}: the pinned literal — parity is two "
                        f"engines agreeing, and on this row they can agree on "
                        f"the wrong answer",
                    )
                # The token is asserted to be what the row meant, so that a row
                # whose `arg` fails to render (or renders as a variable rather
                # than a constant) is a failure rather than a quietly different
                # question: `x|add:4` and `x|add:"4"` are different inputs.
                given, _ = arg_token(case)
                self.assertEqual(
                    expression.token,
                    f"x|{case['filter']}"
                    + ("" if given is probe.NO_ARG else f":{given}"),
                )

    def assert_raises_same(self, case, side, call):
        """One engine refuses the row's input, in the row's own words.

        Called once per side, and split out so the *claim* — that the two agree —
        is one body rather than two that could drift. The class is compared by
        **name** for the reason the corpus compares type names: a Rust
        `PyErrorKind` cannot name a Python class, and the name is what Django's
        own `except` clauses branch on. The message is compared as a literal
        because that is the measurement the row carries; a class alone is
        satisfied by both engines being wrong together.

        The exception is caught and asserted on **outside** the handler so a
        failure is reported as a failure rather than chained onto the raise it
        came from, which is otherwise the first thing a reader sees.
        """
        caught = None
        result = None
        try:
            result = call()
        except Exception as error:
            caught = error
        if caught is None:
            self.fail(
                f"{case['name']}: {side} answered {result!r} where the row's "
                f"whole content is that it raises {case['raises']}"
            )
        self.assertEqual(
            type(caught).__name__,
            case["raises"],
            f"{case['name']}: {side} raised {type(caught).__name__} "
            f"({caught}), not {case['raises']}",
        )
        self.assertEqual(
            str(caught),
            case["message"],
            f"{case['name']}: {side}'s message is not the measured one",
        )

    def test_every_error_case(self):
        """Both engines raise, with the same class and the same message.

        The sibling of `test_every_case`, for the inputs a `CASES` row cannot
        hold. The argument resolution is deliberately the same code path —
        [`arg_token`] for Django's token, [`case_arg`] for our value — so a row
        here asks the *same question* as a `CASES` row and differs only in what
        is asserted about the answer.

        There is no token assertion, and none is missing: the token is only in
        hand when `django_answer` returns, and on these rows it raises instead.
        The message carries what the token would have proved — that the argument
        arrived as the type the row meant — because it names that type.
        """
        for case in ERROR_CASES:
            with self.subTest(case=case["name"]):
                given, extra = arg_token(case)
                with self.settings_for(case):
                    self.assert_raises_same(
                        case,
                        "Django",
                        lambda: probe.django_answer(
                            case["filter"],
                            case_value(case),
                            given,
                            case.get("context"),
                            extra,
                            case.get("libraries"),
                        ),
                    )
                    context = probe.context_for(
                        case_value(case), case.get("context"), extra
                    )
                    self.assert_raises_same(
                        case,
                        "ours",
                        lambda: probe.rust_answer(
                            case["filter"], case_value(case), case_arg(case), context
                        ),
                    )

    def test_every_implemented_filter_has_cases(self):
        """The corpus and the bodies name the same filters — both directions.

        The two failures mean opposite things and are asserted separately for
        that reason: a case for a filter that is still a stub means the corpus
        ran ahead of the code, and an implemented filter with no case means a
        group landed without its corpus rows (§D6), which is the half that would
        otherwise pass silently.
        """
        in_corpus = {case["filter"] for case in CASES}
        implemented = implemented_filters()

        self.assertEqual(
            sorted(in_corpus - implemented),
            [],
            "these filters have corpus cases but their bodies are still stubs",
        )
        self.assertEqual(
            sorted(implemented - in_corpus),
            [],
            "these filters have bodies but no corpus cases — a group landed "
            "without its rows in tests/filter_cases.py",
        )

    def test_every_stub_is_named(self):
        """The stub set is what M4 has left, and it is now empty.

        `filter_names()` is asserted first because everything below is silent if
        the registry itself is short: 57 stubs out of 57 names would pass a count
        check while sixteen filters had gone missing from the registry.

        **The registry is 66 names, not 57, since M12 Task 7.** The 57 are
        `django.template.defaultfilters`'; the nine more are the
        `django.templatetags` library filters (`i18n`'s four `language_*`, `l10n`'s
        `localize`/`unlocalize`, `tz`'s `localtime`/`utc`/`timezone`). They are in
        the **one** Rust registry — `crates/template-core/src/filters/mod.rs`'s
        `REGISTRY`, whose own count row names the same 66 — rather than a second
        table, because this seam is keyed by filter **name** and one lookup has to
        see both sets; see that file's `REGISTRY` doc for the trap.

        **The count is the deliberate-edit signal, and it has reached its floor.**
        A group landing moves both this number and the Rust list, in a commit
        that says which group it was — and Task 10's group is the last, so the
        number is **0** and the set is empty. See `implemented_filters` for why
        there is no Python list of the stub names.
        """
        all_names = set(_native.filter_names())
        implemented = implemented_filters()
        stubs = all_names - implemented

        self.assertEqual(
            len(all_names), 66, "the registry is not the 57 built-ins plus the nine"
        )
        # 0: **M4's last group has landed.** The 57 minus `lower` and `upper`,
        # which Task 3 lands as the corpus's seed, minus Task 4's six —
        # `capfirst`, `title`, `cut`, `addslashes`, `make_list`, `wordcount` —
        # minus Task 5's eight: `center`, `ljust`, `rjust`, `truncatechars`,
        # `truncatechars_html`, `truncatewords`, `truncatewords_html`, `wordwrap`
        # — minus Task 6's nine: `first`, `last`, `length`, `slice`, `join`,
        # `random`, `dictsort`, `dictsortreversed`, `unordered_list` — minus
        # Task 7's first wave of four: `add`, `divisibleby`, `get_digit`,
        # `stringformat` — minus Task 7's second wave of four: `pluralize`,
        # `yesno`, `default`, `default_if_none` — minus Task 7's third wave of
        # two: `floatformat`, `filesizeformat` — minus Task 8's first wave of
        # six: `escape`, `force_escape`, `escapeseq`, `safe`, `safeseq`,
        # `escapejs` — minus Task 8's second wave of six: `striptags`,
        # `linebreaks`, `linebreaksbr`, `linenumbers`, `iriencode`, `urlencode`
        # — minus Task 9's wave A of three: `slugify`, `phone2numeric`, `pprint`
        # — minus Task 9's wave B of three: `json_script`, `urlize`,
        # `urlizetrunc` — and minus Task 10's four: `date`, `time`, `timesince`,
        # `timeuntil`. **The zero is the deliberate-edit signal**, and it says
        # something a nonzero never could: `STILL_STUBBED` in
        # `crates/template-core/src/filters/mod.rs` is the empty array, so every
        # one of the registry's 66 names — the 57 built-ins and M12 Task 7's nine
        # library filters — now has a body. A later group cannot move this number
        # — there is no later group — so a nonzero here means a body has been
        # reverted or re-stubbed, not that work is pending.
        self.assertEqual(
            len(stubs),
            0,
            f"the stub count moved: {len(stubs)} left, {sorted(stubs)}. M4's "
            f"last group has landed, so the stub set should be empty; a stub "
            f"here means a body has been reverted or a registry row lost.",
        )


    def test_the_comparison_target_is_not_rust_owned(self):
        """The Python half is the *pinned* `base`, and the callables are checked.

        **This row is the one M11 Task 2 needed and did not have.** Until that
        task the probe's Python side was `django.template.base.FilterExpression`
        and the sentence in this module's docstring — "Django's side is its real
        `FilterExpression`" — was already false, in the direction that does not
        fail. `base` has been ours since M9, but `resolve` was still upstream's
        text, so the comparison was still stock and nothing looked wrong. Task 2
        put a delegation on `resolve`'s own lines, and from that commit on a
        probe reading `django.template.base` would compare `_native.filter`
        against a `resolve` that *calls* `_native.filter`: green for all 454
        rows, for every filter, whatever Rust did.

        Two claims, and neither implies the other:

        * the `FilterExpression` the probe builds is the **pinned** file's. It
          reads it through `load_pinned`, and the first assertion below is what
          notices a probe that went back to the plain import — which after M9 is
          a comparison of Rust with Rust.
        * the callables the loop runs are the interposed `defaultfilters`, which
          is a transcription. `parity_target` warrants it: the marker is present,
          `check_transcriptions.check()` is clean when this runs, and no declared
          region sits on the compared path. That last clause is the one with
          teeth — `COMPARED_PATH["defaultfilters.py"]` is `stringfilter` at
          40-54, so a region declared over a filter body the corpus calls would
          be refused here.

        The three names are the ones the *parser* holds, not three chosen here:
        `self.parser.find_filter(name)` is the exact object the loop will call,
        which is why the lookup goes through the parser rather than the registry.
        """
        import inspect

        from tests.parity_target import assert_target_is_not_rust_owned

        pinned = probe.django_base()
        source = Path(inspect.getsourcefile(pinned.FilterExpression)).resolve()
        self.assertTrue(
            source.is_relative_to(DJANGO_CHECKOUT),
            f"the probe's `FilterExpression` came from {source}. M9 interposed "
            "`django.template.base`, so a plain import is our file, and the "
            "comparison would be Rust against Rust.",
        )
        assert_target_is_not_rust_owned(
            self, pinned.FilterExpression, "FilterExpression"
        )

        for name in ("date", "lower", "join"):
            with self.subTest(name=name):
                assert_target_is_not_rust_owned(
                    self, self.parser.find_filter(name), f"defaultfilters.{name}"
                )


if __name__ == "__main__":
    unittest.main()
