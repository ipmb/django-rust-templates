"""M4's filter parity test: one corpus row, two engines, one comparison.

Both sides run on the pinned tree. Django's side is its real `FilterExpression`
over a real `Context`, driven by its real filter registry; ours is
`_native.filter`, which is the seam the fan-out's bodies are written against.

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
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import Context, Engine  # noqa: E402
from django.template.base import Parser  # noqa: E402
from django.utils.safestring import SafeData  # noqa: E402

from django_rust_templates import _native  # noqa: E402

from tests.filter_cases import CASES, case_value  # noqa: E402

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
            _native.filter(name, None)
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

        The argument goes through `probe.template_literal` because `django_answer`
        takes **template text**, not a Python object: a `str` argument has to be
        rendered as the constant `"b"` to become a `constant_arg` and be
        `mark_safe`d by `resolve` (`base.py:788-792`), where the bare `b` would be
        a variable lookup. Task 4's `cut` rows are the corpus's first with an
        argument, so this path had never run; it was passing `case["arg"]` raw,
        which made every argument a lookup while `test_every_case` asserted the
        token was the constant. Both sides now agree on what `x|cut:"b"` means.
        """
        given = probe.template_literal(case["arg"]) if "arg" in case else None
        return probe.django_answer(
            case["filter"], case_value(case), given, case.get("context")
        )

    def render_rust(self, case, context):
        return probe.rust_answer(
            case["filter"], case_value(case), case.get("arg"), context
        )

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
                self.assertEqual(expression.token, f"x|{case['filter']}" + (
                    f":{probe.template_literal(case['arg'])}"
                    if "arg" in case else ""
                ))

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
        """The stub set is what M4 has left, and the count says so out loud.

        `filter_names()` is asserted first because everything below is silent if
        the registry itself is short: 41 stubs out of 41 names would pass a count
        check while sixteen filters had gone missing from the registry.

        The **count** is the deliberate-edit signal, and the filter names are
        printed rather than restated: a group landing moves both this number and
        the Rust list, in a commit that says which group it was. See
        `implemented_filters` for why there is no Python list of the stub names.
        """
        all_names = set(_native.filter_names())
        implemented = implemented_filters()
        stubs = all_names - implemented

        self.assertEqual(
            len(all_names), 57, "the registry itself is not Django's 57 filters"
        )
        # 32: the 57 minus `lower` and `upper`, which Task 3 lands as the corpus's
        # seed, minus Task 4's six — `capfirst`, `title`, `cut`, `addslashes`,
        # `make_list`, `wordcount` — minus Task 5's eight: `center`, `ljust`,
        # `rjust`, `truncatechars`, `truncatechars_html`, `truncatewords`,
        # `truncatewords_html`, `wordwrap` — and minus Task 6's nine: `first`,
        # `last`, `length`, `slice`, `join`, `random`, `dictsort`,
        # `dictsortreversed`, `unordered_list`. Each group's commit decreases this
        # by its own size and adds its rows to tests/filter_cases.py — one number
        # and one list, edited together, in the commit that does the work.
        self.assertEqual(
            len(stubs),
            32,
            f"the stub count moved: {len(stubs)} left, {sorted(stubs)}. If a "
            f"group landed, this number and tests/filter_cases.py both move with "
            f"it; if nothing landed, a body has been reverted or a registry row "
            f"lost.",
        )


if __name__ == "__main__":
    unittest.main()
