"""The acceptance run is a test of *this package*, and here is what says so.

`.review/m18-record.md` §1 states the load-bearing claim of M18 in one sentence:
the vendored suite is not a guard but a property of the fixture — its modules
import ``django_rust_templates.engine`` **by name**, so there is no name under
which they could resolve to Django's modules instead. That claim is true, and it
is also **not self-enforcing**, which is what this file is for.

Two mechanisms already enforce most of it, and naming them is what makes the
residue visible:

* ``scripts/gen_django_suite.py`` **derives** every file under
  ``django_suite/template_tests/`` and ``django_suite/i18n/`` from the pinned
  checkout, and ``--check`` (``scripts/check.sh:79``) re-derives the whole tree
  and diffs it against disk. A hand edit to a vendored file therefore cannot
  hide, and ``rewrite()``'s verifier additionally refuses a rewrite that renamed
  something Django's, one that left a name of ours pointing at Django, and the
  two spellings of the backend class its import rule cannot follow.
* the transcription guard and the direct-entry rows in
  ``tests/test_interposition.py`` assert that each ``engine`` module resolves to
  our file.

**Neither of them reads the file this module is about.**
``django_suite/suite_settings.py`` is *not* generated: ``gen_django_suite.py``'s
``TREES`` names ``django_suite/template_tests`` and ``django_suite/i18n``, and
``check()`` walks only those two subtrees, so a file at the suite's root is
outside both the diff and the "in the tree but nothing in Django's suite produces
it" sweep. And ``suite_settings.py`` is the file that decides **which backend all
1547 tests run against**. Changing its one ``BACKEND`` string to
``django.template.backends.django.DjangoTemplates`` turns the acceptance run into
a test of stock Django; the suite still passes, ``gen_django_suite.py --check``
still exits 0, and every other gate in ``check.sh`` stays green. That is the
second-row-arrives-green shape this project has been caught by before, in the one
place where it costs the most.

So there are three kinds of row here, and they are separate because they fail for
different reasons:

* :class:`GateBuildTests` — the **subject**. ``cargo test`` reads the source; the
  two Python steps below exercise whatever ``_native`` is installed, so the gate
  has to build it or it is reporting on an artifact nobody tied to the tree. This
  is first because it is the premise of the other two: a suite run against a stale
  extension is evidence about the old source, and this file's whole claim is that
  the run is evidence about *this package*.
* :class:`SuiteSettingsTests` — the activation. Which backend the suite's
  ``TEMPLATES`` name, read statically from the source with ``ast`` rather than by
  importing the module (importing executes ``settings.configure`` and has
  side effects a row in a shared interpreter should not take).
* :class:`VendoredImportTests` — the partition. Which Django modules the vendored
  tree still imports, asserted against a **declared closed set**. The set is
  seven statements today; a new one fails this row, which is the point, because
  the alternative is that it arrives green. The set is then asked **two
  questions, not one** — whether each entry is an import Django's own file makes,
  and whether each names an adapter rather than the engine — because a single
  predicate answers whichever of the two it was written for and silently licences
  the other. ``from django.template.base import Node`` is the entry that separates
  them: it is upstream's own import (``.django/tests/template_tests/
  test_custom.py:4``) *and* it is the engine, so "Django has this line" would
  rubber-stamp the one import this set exists to refuse.

Each class carries a control that drives its own predicate with fabricated
input, because a row that only ever reads the real file cannot show it would
refuse a wrong one — and both of these rows exist *because* the wrong input is
plausible rather than exotic.
"""

import ast
import pathlib
import re
import unittest

from tests.milestone_ownership import NOT_THE_ENGINE

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITE = PROJECT_ROOT / "django_suite"
SUITE_SETTINGS = SUITE / "suite_settings.py"

#: The pinned Django checkout — the tree ``gen_django_suite.py`` read. Named here
#: for the one row that re-derives inheritance rather than asserting it, and named
#: **by directory rather than by the lock file**, because the row compares text and
#: `scripts/assert_checkout_pinned.py` is what guarantees the two agree.
UPSTREAM = PROJECT_ROOT / ".django" / "tests"

#: The backend a project names to activate this engine, and the one the suite must
#: name for the 1547 to be evidence about this package.
OUR_BACKEND = "django_rust_templates.rust.RustTemplates"
DJANGO_BACKEND = "django.template.backends.django.DjangoTemplates"


def backends_in(path):
    """Every ``BACKEND`` string in ``path``'s ``TEMPLATES``, with its ``NAME``.

    Static, by ``ast``, and it **refuses rather than defaults**: a settings module
    whose ``TEMPLATES`` this function cannot find returns an empty list, so a row
    built on it would pass vacuously on a file it never read. The rows below
    assert non-emptiness for that reason, and this docstring is the reason they
    have to.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "TEMPLATES" for t in node.targets):
            continue
        out = []
        for entry in node.value.elts:
            fields = {
                k.value: v
                for k, v in zip(entry.keys, entry.values)
                if isinstance(k, ast.Constant)
            }
            backend = fields.get("BACKEND")
            name = fields.get("NAME")
            out.append(
                (
                    name.value if isinstance(name, ast.Constant) else None,
                    backend.value if isinstance(backend, ast.Constant) else None,
                )
            )
        return out
    return []


def django_imports_in(root):
    """Every import of a Django template module under ``root``, as ``ast`` sees it.

    ``(path, module, names)`` per statement. A bare ``import a.b`` yields an empty
    name tuple and is recorded, because it binds the same module the ``from`` form
    does — a scan that only matched ``ImportFrom`` would miss the spelling a
    rewrite is least likely to handle.

    The scope is **imports, not strings**. Dotted paths in string literals are the
    other half of the coupling (``mock.patch("django.template.loaders…")`` and the
    ``TEMPLATES`` literals themselves), and those are the generator's business:
    it rewrites them and refuses the spellings it cannot. Naming the scope here is
    the point — "no ``django.template`` remains" is a claim about one of two
    mechanisms, and the weaker reading of it is satisfied by the other.
    """
    out = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level or not (node.module or "").startswith("django.template"):
                    continue
                out.append(
                    (
                        path.relative_to(PROJECT_ROOT).as_posix(),
                        node.module,
                        tuple(sorted(a.name for a in node.names)),
                        node.lineno,
                    )
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("django.template"):
                        out.append(
                            (
                                path.relative_to(PROJECT_ROOT).as_posix(),
                                alias.name,
                                (),
                                node.lineno,
                            )
                        )
    return out


#: The imports of Django's template package the vendored suite is allowed to keep.
#:
#: Seven statements, in five files, over three modules. **Each one is an adapter,
#: not a leak**: ``engines`` resolves a ``TEMPLATES`` alias through
#: ``EngineHandler`` and therefore returns *this* backend; ``loader`` and
#: ``response`` are Django's public entry points and reach the engine the same way;
#: ``autoreload`` is the module whose ``isinstance(backend, DjangoTemplates)``
#: checks ``rust.py`` subclasses to satisfy. None of them names an engine
#: *implementation*, which is the distinction that matters — the vendored tests
#: that do need engine behaviour import ``django_rust_templates.engine`` directly.
#:
#: Keyed on ``(path, module, names)`` rather than on the module alone, so that a
#: second file importing ``django.template`` for a reason nobody has classified
#: fails the row. That is the arrival this set is here to stop.
#:
#: **Every entry is ``inherited``, and that is checkable rather than asserted.**
#: The discriminator between a *licence* and a *check* is where the import came
#: from: one that exists because Django's own file has it cannot be removed without
#: diverging from upstream, while one declared by judgement is a decision that could
#: be revisited and should be re-justified when it is. All seven are upstream's, and
#: the vendored text does not make that obvious. Measured against the pin, by *where
#: the statement came from* rather than by how it looks: **two of the seven** are what
#: is left of a parenthesised upstream block the generator split into a statement of
#: ours plus a statement of Django's — ``from django.template import (Context, Engine,
#: …, loader)`` becomes a line of ours followed by ``from django.template import
#: loader``, so the survivor reads as if somebody here had written it. The other
#: **five are untouched**, in the shape upstream has them; of those, three name a
#: submodule (``django.template.loader``, ``django.template.response``) that this
#: package does not rewrite at all.
#: :meth:`VendoredImportTests.test_every_declared_adapter_is_upstreams_own_import`
#: re-derives that against the pin, which is what stops the set being a licence.
#:
#: What stays a **judgement**, and is therefore the part to re-argue: that each of
#: the seven is an *adapter* — a public entry point that reaches whichever backend
#: ``TEMPLATES`` configures — rather than an engine implementation in disguise.
#: That is a claim about runtime routing, and no static read of the file settles it.
#:
#: **Half of it is now checked, and the other half still is not.** The static half
#: is the *module*: is the thing imported one of the eleven Django template modules
#: §3 declares are not the engine, or the ``EngineHandler`` facade over them?
#: :meth:`VendoredImportTests.test_the_declared_adapters_name_plumbing_not_the_engine`
#: asks it and refuses ``from django.template.base import Node``, which is
#: upstream's own import and therefore passes the row above. The half that is not
#: checked is *routing* — that ``loader`` and ``engines`` reach the configured
#: backend rather than some engine of ours they happened to bind — and nothing
#: static can settle it. The sentence above stands; it is now the residue rather
#: than the whole of the claim.
DECLARED_ADAPTERS = {
    (
        "django_suite/template_tests/syntax_tests/test_include.py",
        "django.template",
        ("loader",),
    ),
    (
        "django_suite/template_tests/test_autoreloader.py",
        "django.template",
        ("autoreload",),
    ),
    (
        "django_suite/template_tests/test_partials.py",
        "django.template",
        ("engines",),
    ),
    (
        "django_suite/template_tests/test_partials.py",
        "django.template.loader",
        ("render_to_string",),
    ),
    (
        "django_suite/template_tests/test_response.py",
        "django.template",
        ("engines",),
    ),
    (
        "django_suite/template_tests/test_response.py",
        "django.template.response",
        ("ContentNotRenderedError", "SimpleTemplateResponse", "TemplateResponse"),
    ),
    (
        "django_suite/template_tests/views.py",
        "django.template.response",
        ("TemplateResponse",),
    ),
}

#: §3's declaration, **read rather than restated**. ``NOT_THE_ENGINE`` is keyed by a
#: dotted suffix relative to ``django.template`` — ``loader``, ``response``,
#: ``backends.django``, and eight more — and ``tests/test_milestone_ownership.py``
#: already re-derives it from §3's prose with checked anchors. Copying eleven names
#: into this file would make a second list that could drift from the first, which is
#: the failure ``a-subtraction-has-two-routes-to-zero`` names.
PLUMBING = frozenset(NOT_THE_ENGINE)

#: The pin's ``django`` package, which is a different root from :data:`UPSTREAM`
#: (the pin's *tests*). Both are needed: one row asks where an import came from, the
#: other what the imported name *is*, and those are questions about two trees.
TEMPLATE_INIT = PROJECT_ROOT / ".django" / "django" / "template" / "__init__.py"


def engine_handler_attributes():
    """The names the pin's ``django/template/__init__.py`` binds to an ``EngineHandler()``.

    :data:`PLUMBING` is keyed by *module*, and six of the seven declared adapters
    name one. The seventh does not: ``from django.template import engines`` — in
    ``test_partials.py`` and ``test_response.py`` — binds an **attribute of the
    package root**, which no module key can hold, and the root is the engine's own
    module rather than plumbing. So the root case needs its own licence, and this
    derives it: ``engines`` is admissible for the reason §3 admits ``loader`` — it
    is the ``EngineHandler`` facade, the object ``django.template.loader`` delegates
    to — and the pin says so on one line (``engines = EngineHandler()``), where a
    name typed here would be a claim.

    Derived by the call, not by the name: ``base``, ``context``, ``engine`` and
    ``library`` are bound in that file too, to *imports*, and every one of them is
    the engine. An empty return means the pin is missing, which is why the row
    using this asserts on it rather than letting a missing checkout refuse every
    root import and read as a genuine violation.
    """
    if not TEMPLATE_INIT.is_file():
        return set()
    out = set()
    for node in ast.parse(TEMPLATE_INIT.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if called == "EngineHandler":
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return out


ENGINE_HANDLER_ATTRS = engine_handler_attributes()

#: The pin's ``django`` package and the pin's ``django.template`` package. Two
#: roots again, and a third from :data:`UPSTREAM`: one row asks where an import
#: came from, another what the imported name *is*, and the third which vendor file
#: a suite statement lives in.
PIN_DJANGO = TEMPLATE_INIT.parent.parent


def module_source(module):
    """The pin's file for a dotted module, and whether that file is a package.

    ``django.template.loader`` is a module (``…/loader.py``); ``django.template.
    backends`` is a package (``…/backends/__init__.py``). The distinction is not
    cosmetic: it decides how a relative import inside the file resolves, so the
    reader that got it wrong would resolve ``from .base import …`` in
    ``backends/django.py`` to the wrong module and mis-derive the set below.
    """
    base = PIN_DJANGO.joinpath(*module.split(".")[1:])
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py"), False
    if (base / "__init__.py").is_file():
        return base / "__init__.py", True
    return None, False


def absolute_import(module, node, is_package):
    """The dotted module a ``from … import …`` names, relative levels resolved.

    ``from .exceptions import X`` in ``django/template/loader.py`` is
    ``django.template.exceptions``, and ``from .base import Y`` in
    ``django/template/backends/django.py`` is ``django.template.backends.base`` —
    the package's own directory is its full dotted name, a module's is its parent.
    """
    if not node.level:
        return node.module
    base = module.split(".") if is_package else module.split(".")[:-1]
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, node.module]) if node.module else ".".join(base)


def close_reexports(edges):
    """Propagate :data:`PLUMBING`'s re-exports to a fixed point over ``edges``.

    ``edges`` maps each plumbing module to its ``(origin, names)`` imports, and the
    result maps each module to the engine names it exposes, directly or through
    another plumbing module. Split out from :func:`engine_reexports` so the
    iteration can be *driven*: the pin's own map is acyclic and one hop deep, so
    every name it propagates arrives in the first pass and **no row over the pin
    can tell this loop from a single pass** —
    :meth:`VendoredImportTests.test_the_closure_propagates_a_name_across_two_hops`
    supplies a map where the two differ.

    The three branches are the three kinds of origin: the package root (judged by
    the root arm's rule), another plumbing module (which propagates only the names
    *that* module is already known to expose), and an engine module (which
    propagates everything it is asked for). Only the middle branch depends on
    ``out``, so only the middle branch can require a second pass.
    """
    out = {module: set() for module in edges}
    for _ in range(len(edges) + 1):
        changed = False
        for module, direct in edges.items():
            for origin, names in direct:
                if origin == "django.template":
                    found = {
                        name
                        for name in names
                        if name not in PLUMBING and name not in ENGINE_HANDLER_ATTRS
                    }
                elif origin.removeprefix("django.template").lstrip(".") in PLUMBING:
                    found = {name for name in names if name in out.get(origin, ())}
                else:
                    found = set(names)
                if found - out[module]:
                    out[module] |= found
                    changed = True
        if not changed:
            break
    return {module: frozenset(names) for module, names in out.items()}


def engine_reexports():
    """Per plumbing module, the names it takes **from an engine module**.

    A name reached through a plumbing module is not thereby plumbing. The
    submodule arm of :func:`names_the_engine` used to decide on the *module* alone,
    which certified ``from django.template.backends.django import Engine`` — §3
    declares ``backends.django`` plumbing and ``Engine`` ours, and the one
    indirection turned a refusal into a licence. The same class of name had two
    verdicts depending on the route (``from django.template import Engine`` was
    refused and so was ``from django.template.library import
    InvalidTemplateLibrary``, while their ``backends.django`` spellings were not),
    which is the inconsistency this closes rather than the instance of it.

    Derived from the pin, not typed: for each module in :data:`PLUMBING`, the names
    it takes from a module that is **not** plumbing, with imports from the package
    *root* judged by the root arm's own rule (a root name is plumbing iff it is in
    :data:`PLUMBING` or in :data:`ENGINE_HANDLER_ATTRS`). **At 6.0.9 that is nine
    names over eleven modules** — four of them in ``django.template.backends.
    django`` (``Engine``, ``InvalidTemplateLibrary``, ``TemplateDoesNotExist``,
    ``make_context``), two each in ``backends.dummy`` and ``backends.jinja2``, one
    in ``loader``, and none at all in the other seven. The count is per *name
    occurrence*, and it is nine rather than four because the other routes exist
    too; a reader checking "four names today" against this map will see nine, and
    the four are the ``backends.django`` row.

    The name recorded is the one the plumbing module **binds** — ``alias.asname or
    alias.name`` — because that is what a statement importing *from* that module
    would have to spell. Keying on the source name instead is the same wrong-side
    mistake the submodule arm made in the other direction: ``from ..engine import
    Engine as _Engine`` in ``backends/django.py`` would put ``Engine`` in the map,
    refusing an import of a name that module does not bind and licensing the
    ``_Engine`` it does. Latent rather than live — measured, **zero of the 70 import
    bindings across the eleven plumbing modules at 6.0.9 are aliased** — which is
    why the token is one word rather than a rewrite.

    **The relation is closed, not one hop** — see :func:`close_reexports`, which
    does the iteration and carries the control's address. Measured at 6.0.9: the
    fixed point is reached in the first pass and adds nothing, because every name
    passed between two plumbing modules (``BaseEngine``, ``get_template``,
    ``select_template``, ``DjangoTemplates``, ``csrf_input_lazy``) is *defined*
    where it is imported from. So today the closure and the direct edges agree —
    which is the fact that makes the simpler rule tempting, and the reason it is
    not the rule written here.

    An unreadable pin yields an empty mapping, which refuses nothing *extra* — the
    fail-closed direction for this set is the other one, and it is covered by the
    module arm of :func:`names_the_engine`, which refuses a module it cannot
    recognise whatever this returns.
    """
    edges = {}
    for suffix in sorted(PLUMBING):
        module = f"django.template.{suffix}"
        source, is_package = module_source(module)
        if source is None:
            continue
        direct = []
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            origin = absolute_import(module, node, is_package)
            if origin and (origin == "django.template" or origin.startswith("django.template.")):
                direct.append(
                    (origin, tuple(alias.asname or alias.name for alias in node.names))
                )
        edges[module] = direct
    return close_reexports(edges)


ENGINE_REEXPORTS = engine_reexports()


def names_the_engine(module, names):
    """Would ``from <module> import <names>`` pull in the engine itself?

    The static half of "is this an adapter?". ``module`` is a ``django.template``
    dotted path and ``names`` the names the statement binds. Two shapes, because
    §3's declaration is keyed by module and the suite's imports are not all
    submodules:

    * a **submodule** is plumbing iff its suffix is one of :data:`PLUMBING`.
      ``django.template.loader`` is (``"loader"``); ``django.template.loaders`` —
      plural, the loaders themselves — is not, and neither is
      ``django.template.base``. Exact membership and not a prefix test, so that a
      module declared not-the-engine cannot smuggle in a child of ours. **And the
      module being plumbing does not settle the statement**: a name the module
      itself took from an engine module is refused by :data:`ENGINE_REEXPORTS`,
      because the question is about what the statement binds and not only about
      where the statement lives.
    * an import **from the package root** is plumbing iff every name it binds is
      either one of :data:`PLUMBING` (``from django.template import loader`` binds
      the submodule) or an attribute :data:`ENGINE_HANDLER_ATTRS` derived from the
      pin. Every name, not any: a statement that binds one adapter and one engine
      class is an engine import.

    Fail-closed in both directions that matter: an unreadable pin empties
    :data:`ENGINE_HANDLER_ATTRS` and refuses the root imports rather than licensing
    them, and an unrecognised module is refused rather than waved through.

    **One coupling, named where it is read.** :data:`PLUMBING` is
    ``milestone_ownership.NOT_THE_ENGINE``, and that set has consumers this one
    does not share a direction with. ``unshimmed()`` is ``django_modules - ours -
    NOT_THE_ENGINE``, so the coverage meter reaches ``[]`` by interposing **or** by
    widening the exclusion; ``_djangos()`` in ``scripts/gen_django_suite.py`` asks
    the same set whether a rewritten import may be left pointing at Django, so a
    twelfth name lets another Django import survive the generator; and that file's
    ``check_partition()`` refuses a declared name that has a file under the engine
    package, so a twelfth name also narrows *that*. Each of the three is loosened
    by an addition; this file's licence is loosened by the same addition. The two
    questions — "what do we decline to own" and "what may a vendored test import" —
    happen to have the same answer, and nothing in the tree requires them to, which
    is why the answer is worth reading here rather than at each use. What makes an
    addition non-free is not any of the four but ``tests/test_milestone_ownership.py``,
    which ties ``set(NOT_THE_ENGINE)`` to §3's own enumeration: a name added to
    reach ``[]`` has to be added to the specification first.
    """
    suffix = module.removeprefix("django.template").lstrip(".")
    if suffix:
        if suffix not in PLUMBING:
            return True
        return any(name in ENGINE_REEXPORTS.get(module, ()) for name in names)
    return any(
        name not in PLUMBING and name not in ENGINE_HANDLER_ATTRS for name in names
    )


class GateBuildTests(unittest.TestCase):
    """The subject: the two Python steps are about a *build*, so the gate makes one.

    ``cargo test`` reads the source. The unit suite and the acceptance run import
    ``_native`` and exercise whatever it resolves to — **the source tree's**
    ``python/django_rust_templates/_native.<abi>.so``, reached through the editable
    install rather than from ``site-packages`` — which is whatever was built last.
    Measured, ``scripts/check.sh`` ran no build at all, so both of those steps were
    statements about an artifact nobody had tied to the tree — and it is not
    hypothetical: the extension installed while this file was written was a
    **debug** build, and several rounds of green runs were taken against it with no
    line of output saying so.

    The two failure modes are not the same and are worth separating, because only
    one is a correctness problem. A wrong *profile* costs speed — both profiles
    render identical bytes, which the four ``sha256`` in
    ``.review/m18-measurements/transcript-bench-release.txt`` agree on — and it
    produced a benchmark range that was read as a regression. A *stale* extension
    costs correctness: a row about Rust behaviour is a row about that binary, and a
    binary older than the change under test passes rows the current source does not
    satisfy. ``cargo test`` green beside it is what makes that invisible, because it
    is the one step in the gate that reads the source.
    """

    def test_the_gate_builds_the_extension_before_every_consumer(self):
        """The invocation and its **position**, over every step that consumes it.

        A build step that ran *after* the steps that need it would satisfy the
        weaker reading and fix nothing, which is the reason
        ``tests/test_anchor_manifest.py``'s row asserts one argument list rather
        than a flag occurring somewhere in the file — a claim no test reads is the
        defect this whole tool exists for.

        **The position is asserted against every consumer, not one named step.**
        The property is "before the first line that can read the built artifact",
        and an earlier version of this row checked only that the build preceded
        ``run_unit_tests.sh``. That is equivalent *only* while the unit run happens
        to be the first such line, which the row did not assert — and it is a
        proxy, not the property: ``differential.py``, ``check_transcriptions.py``
        and the acceptance run all import ``_native``, so moving any of them above
        the build reopens the gap with the old row still green. A check written
        against a stand-in passes every mutation of the stand-in, and the four
        mutations tried here (no build, a late build, a non-``maturin`` build, a
        missing ``--release``) were all edits to the build line and moved no
        consumer — the same shape as the three wrong-side keys, one layer up.
        """
        lines = [
            line
            for line in (PROJECT_ROOT / "scripts" / "check.sh")
            .read_text(encoding="utf-8")
            .splitlines()
            if not line.lstrip().startswith("#")
        ]
        builds = [
            i for i, line in enumerate(lines) if "maturin" in line and "develop" in line
        ]
        self.assertEqual(
            len(builds),
            1,
            "scripts/check.sh must build the extension exactly once, before the "
            f"steps that import it; found these candidate lines {builds} in:\n"
            + "\n".join(lines),
        )
        self.assertIn(
            "--release",
            lines[builds[0]],
            "the gate must build the profile `pip install` ships, so that a bench "
            "run after a gate run measures the binary the published figures "
            "describe rather than the unoptimised default `maturin develop`",
        )

        # `source "$HERE/env.sh"` is excluded by name: it sets the environment the
        # build itself needs, so it must come *first*, and it imports nothing.
        #
        # What makes a step a consumer is that it **reaches the built package**, so
        # the population is derived from that and not from the word `python` in a
        # line. Two shapes reach it:
        #
        #   * a line that runs the venv's Python over a project file
        #     (`.venv/bin/python -I "$HERE/x.py"`) — whatever it runs can import
        #     the extension;
        #   * a line that runs one of this repo's scripts — a consumer only if the
        #     *script* runs project Python, which is read out of the script rather
        #     than guessed from its name.
        #
        # The second shape used to be decided by the substring `run_unit_tests.sh`.
        # That was a proxy, and it held only while the gate had exactly one runner
        # script: it classified the gate's **cargo** step for `template-py` as a
        # consumer of an extension that invocation does not build — the crate is
        # built `--no-default-features`, the feature that decides whether there is
        # an extension module at all — so the row failed a step that reads nothing
        # and the only way to satisfy it would have been to move that step after
        # the build, i.e. to make the row green for a reason unrelated to its
        # subject. That is this docstring's own defect one layer down ("a check
        # written against a stand-in"), so the stand-in is gone and each named
        # script's body decides.
        def runs_project_python(script):
            """Whether ``script`` runs Python over this project's code.

            Comments are dropped, because a script that describes the package in
            prose is not one that imports it. Two ways to qualify: naming the
            package (``_native`` / ``django_rust_templates``) anywhere it runs, or
            invoking an interpreter on a module or file (``-m unittest``,
            ``x.py``). A `-c` one-liner is deliberately **not** enough on its own:
            the gate's cargo step reads `sysconfig` from the venv's Python to find
            libpython, and a row about the artifact must not turn an environment
            probe into an import.
            """
            body = "\n".join(
                line
                for line in script.read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#")
            )
            if "_native" in body or "django_rust_templates" in body:
                return True
            return bool(re.search(r"\bpython[\w.-]*\b[^\n]*(?:-m\s|\s[\w./-]+\.py\b)", body))

        consumers = [
            (i, line)
            for i, line in enumerate(lines)
            if i != builds[0]
            and not line.lstrip().startswith("source ")
            and (
                ".venv/bin/python" in line
                or any(
                    runs_project_python(PROJECT_ROOT / "scripts" / name)
                    for name in re.findall(r"\$HERE/([\w.-]+\.sh)", line)
                )
            )
        ]
        # The positive control, and it is load-bearing: the assertion below is that
        # a set of *late* consumers is empty, and an empty set is also what a
        # population that silently matched nothing would produce. So name the
        # members of the population this row is about before requiring none of
        # them to be early.
        self.assertTrue(
            consumers,
            "no consumer of the built extension was found in scripts/check.sh, so "
            "the ordering assertion below would pass vacuously",
        )
        self.assertTrue(
            any("run_unit_tests.sh" in line for _, line in consumers),
            f"the unit step is not in the consumer population {consumers}",
        )
        self.assertTrue(
            any(".venv/bin/python" in line for _, line in consumers),
            f"no Python step is in the consumer population {consumers}",
        )
        # The positive control for the *classification*, and it is the same
        # discipline as the one above: a rule that excluded nothing would also
        # leave the row green, so the step the rule exists for is named here and
        # required to be in the file **and** outside the population. It is the
        # cargo runner, which builds its crate without `extension-module` and so
        # has no extension to import; it belongs with the cargo steps above the
        # build, and this is the row that used to say otherwise.
        self.assertIn(
            "run_rust_unit_tests.sh",
            "\n".join(lines),
            "the cargo step this classification is written for is not in check.sh, "
            "so the exclusion below is untested",
        )
        self.assertFalse(
            any("run_rust_unit_tests.sh" in line for _, line in consumers),
            f"the cargo runner reads no extension — its crate is built "
            f"--no-default-features, so no extension module is built to import — "
            f"but the population classified it as a consumer: {consumers}",
        )
        late = [(i, line) for i, line in consumers if i < builds[0]]
        self.assertEqual(
            late,
            [],
            "the extension is built after a step that reads it, so that step still "
            "measures whatever was installed before:\n"
            + "\n".join(f"  line {i}: {line}" for i, line in late),
        )


class SuiteSettingsTests(unittest.TestCase):
    """The suite's ``TEMPLATES`` names this package, and nothing else does the job.

    Static, for the reason ``backends_in``'s docstring gives. The file is read
    from disk rather than imported, so these rows say something about the file
    the acceptance run will load and nothing about this interpreter's state.
    """

    def test_the_settings_file_is_where_this_module_thinks_it_is(self):
        """The premise of every row below, asserted rather than assumed.

        A missing or renamed file would otherwise make ``backends_in`` return an
        empty list, and the next row would fail for a reason that reads like a
        configuration error rather than a moved file.
        """
        self.assertTrue(
            SUITE_SETTINGS.is_file(),
            f"{SUITE_SETTINGS.relative_to(PROJECT_ROOT)} does not exist; the "
            f"acceptance run in scripts/check.sh loads it",
        )

    def test_every_templates_entry_activates_this_package(self):
        """No entry may name Django's backend, and at least one must exist.

        Both halves in one row on purpose: "every entry is ours" is satisfied
        perfectly by zero entries, and an empty ``TEMPLATES`` would make the suite
        error rather than pass — but it would make *this* row green while doing
        it, which is the failure mode ``control-over-the-same-population`` names.
        """
        entries = backends_in(SUITE_SETTINGS)
        self.assertTrue(
            entries,
            f"{SUITE_SETTINGS.relative_to(PROJECT_ROOT)} has no TEMPLATES the "
            f"reader can find — the scan, not the settings, is what failed",
        )
        wrong = [e for e in entries if e[1] != OUR_BACKEND]
        self.assertEqual(
            wrong,
            [],
            f"the acceptance run would exercise stock Django for these entries: "
            f"{wrong}",
        )

    def test_the_entry_the_suite_reaches_by_alias_is_ours(self):
        """``engines["django"]`` is how five vendored rows reach the engine.

        ``suite_settings.py`` gives one entry ``"NAME": "django"`` because two
        modules index that alias — ``template_tests/test_partials.py`` and three
        sites in ``template_tests/test_response.py`` — and ``test_autoreloader``
        walks ``engines.all()``. The alias is derived from the ``BACKEND`` string
        by ``EngineHandler``, so an entry whose name is right and whose backend is
        Django's is reachable and wrong; this row is the one that pairs the two.
        """
        entries = backends_in(SUITE_SETTINGS)
        named = [e for e in entries if e[0] == "django"]
        self.assertEqual(
            len(named),
            1,
            f'expected exactly one TEMPLATES entry named "django", got {named}',
        )
        self.assertEqual(
            named[0][1],
            OUR_BACKEND,
            'the entry `engines["django"]` resolves is not this package',
        )

    def test_the_refusal_fires_on_a_settings_file_that_names_django(self):
        """The control: a fabricated settings module, through the real predicate.

        Not a mutation of ``suite_settings.py`` — the file is generated-adjacent
        and editing it to test the test is the thing this module exists to
        prevent. A synthetic source string goes through ``backends_in``'s
        predicate via a temporary file instead, so what is exercised is the code
        path and not a copy of it.
        """
        import tempfile

        source = (
            "TEMPLATES = [\n"
            '    {"NAME": "django", "BACKEND": %r, "DIRS": [], "OPTIONS": {}},\n'
            "]\n" % DJANGO_BACKEND
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "suite_settings.py"
            path.write_text(source, encoding="utf-8")
            found = backends_in(path)
        self.assertEqual(
            found,
            [("django", DJANGO_BACKEND)],
            "the reader did not see a backend it must see, so the rows above "
            "would pass on this file too",
        )
        self.assertNotEqual(
            found[0][1],
            OUR_BACKEND,
            "the control input is not distinguishable from the real file, so the "
            "row above proves nothing",
        )


class VendoredImportTests(unittest.TestCase):
    """What the vendored tree still imports from Django, against a declared set."""

    def test_the_scan_is_not_vacuous(self):
        """A positive control over the same population the next row measures.

        ``django_imports_in`` returns a list, and a list is truthy-tested by the
        comparison below in a way that an empty one satisfies. This row names a
        statement that must be present, so a broken walk fails here — with a
        message about the instrument — instead of quietly agreeing.
        """
        found = django_imports_in(SUITE)
        self.assertGreater(
            len(found),
            0,
            f"no Django template imports found under "
            f"{SUITE.relative_to(PROJECT_ROOT)} at all, which cannot be right",
        )
        self.assertIn(
            "django_suite/template_tests/test_partials.py",
            {row[0] for row in found},
            "the walk missed a file it is known to contain imports for",
        )

    def test_every_declared_adapter_is_upstreams_own_import(self):
        """The seven are not a licence: each is a line Django's own file has.

        This is the row that turns ``DECLARED_ADAPTERS`` from a list somebody
        approved into a claim the pin can falsify. It exists because the set *looks*
        like a judgement — seven imports of Django's package, kept in a tree whose
        whole point is not to depend on it — and the reader is entitled to ask who
        decided. The answer is that nobody did: the generator splits a parenthesised
        upstream block into a statement of ours and a statement of Django's, and
        what is left behind is upstream's own name.

        So the claim is re-derived rather than asserted, and it is re-derived from
        `.django/`, which is the thing the generator read. **Both halves of the key
        come from the pin's own node** — ``node.module`` and ``alias.name`` — and
        that is not a detail: the other arrangement, reading the module off the
        *vendored* statement and only the name off the pin, produces a set that
        looks right and is not. ``module`` there is the vendored statement's own
        module, which is a ``django.template`` path by construction of the walk, so
        every vendored module is in the set no matter what the pin contains. The
        row would then accept ``from django.template import mock`` — ``mock`` is
        ``unittest``'s, Django's ``django/template/__init__.py`` binds no such name,
        and the pair is in the set anyway because ``"django.template"`` came from
        the file under test. Every entry stays green, which is exactly this
        project's "second row arrives green" shape, in the row written to prevent
        it.

        Comparison is per ``(module, name)`` and not per statement, because
        splitting is one of the things that changes a statement's shape — two
        entries here are affected: the vendored file has ``from django.template
        import engines`` where upstream has ``engines`` as one name inside a
        five-name parenthesised block. The other five entries are in upstream's own
        shape, which comparing whole statements would also have caught; comparing
        per name is what keeps the row from depending on which of the two kinds an
        entry happens to be.

        A failure here means one of two things, and the message says which by
        naming the file: either the pin has moved and this set is stale, or the
        entry is **not** inherited and is therefore a decision that needs an owner.
        Note what this row does **not** say: an entry Django's own file carries is
        not thereby an adapter. That is the other row, and ``from
        django.template.base import Node`` — upstream's, and the engine — is why
        the two questions are separate.
        """
        upstream = set()
        # The walk supplies the *files* to read; nothing it returns is compared.
        # That asymmetry is the row: what is asserted is the pin's text, and the
        # vendored statement only says which pin file to open.
        for relative, _, _, _ in django_imports_in(SUITE):
            pin = UPSTREAM / relative.removeprefix("django_suite/")
            if not pin.is_file():
                continue
            for node in ast.walk(ast.parse(pin.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.ImportFrom) or node.level:
                    continue
                for alias in node.names:
                    upstream.add((node.module, alias.name))
        # The control. An empty `upstream` — a missing checkout, a moved pin — makes
        # every entry "not upstream" and the row fails, which is right; but a checkout
        # present and *unread* would do the same, and the two read identically in the
        # message. So the population is asserted to be non-empty before it is used.
        self.assertGreater(
            len(upstream),
            0,
            f"nothing was read from {UPSTREAM.relative_to(PROJECT_ROOT)}; run "
            f"scripts/fetch_django.sh, or the row below is not measuring the pin",
        )
        not_upstream = sorted(
            (path, module, names)
            for path, module, names in DECLARED_ADAPTERS
            if not all((module, name) in upstream for name in names)
        )
        self.assertEqual(
            not_upstream,
            [],
            "these declared adapters are not imports Django's own files make, so "
            "they are decisions rather than inheritance and each needs an owner:\n"
            f"  {not_upstream}\n"
            "  (if the pin moved, this set is stale rather than wrong)",
        )

    def test_the_declared_adapters_name_plumbing_not_the_engine(self):
        """§3's declaration applied to the set: an *adapter*, not the engine.

        The row above asks whether each entry is an import Django's own file makes.
        This one asks whether it is an import that *should* have been inherited
        here — two questions, and an entry can answer yes to the first while the
        second is the whole problem. ``from django.template.base import Node``
        (``.django/tests/template_tests/test_custom.py:4``) is upstream's own line
        and it is the engine: ``base`` is where ``Template``, ``Node``, ``Parser``
        and ``Variable`` live, and the vendored tests that need them import
        ``django_rust_templates.engine`` directly. A single predicate doing both
        jobs would have rubber-stamped it, which is why there are two.

        The set the question is put to is §3's, not a new one: ``PLUMBING`` is
        ``tests/milestone_ownership.py``'s ``NOT_THE_ENGINE``, which
        ``test_milestone_ownership.py`` already re-derives from §3's prose with
        checked anchors. Restating the eleven names here would make a second list
        that could drift from the first.

        What a failure means: an entry that imports the engine itself, whose
        author would then be testing Django's engine while the file's name says
        otherwise. The message prints the declaration so the fix is visible
        without opening another file.
        """
        self.assertTrue(
            ENGINE_HANDLER_ATTRS,
            f"no EngineHandler attribute was read from "
            f"{TEMPLATE_INIT.relative_to(PROJECT_ROOT)}; run scripts/fetch_django.sh. "
            f"Until then the two root imports below fail for a reason that names the "
            f"pin, not the import",
        )
        self.assertTrue(
            ENGINE_REEXPORTS and any(ENGINE_REEXPORTS.values()),
            f"no name was read as re-exported through a plumbing module from "
            f"{PIN_DJANGO.relative_to(PROJECT_ROOT)}, where the pin has nine; run "
            f"scripts/fetch_django.sh. The submodule arm of the predicate then "
            f"licenses every plumbing module whatever it binds, which is the state "
            f"this control exists to leave",
        )
        engine_imports = sorted(
            (path, module, names)
            for path, module, names in DECLARED_ADAPTERS
            if names_the_engine(module, names)
        )
        self.assertEqual(
            engine_imports,
            [],
            "these declared adapters import the engine itself rather than the "
            "plumbing around it, so each needs an owner or a different import:\n"
            f"  {engine_imports}\n"
            f"  (§3's declaration, over modules plus the EngineHandler facade: "
            f"{sorted(PLUMBING | ENGINE_HANDLER_ATTRS)})",
        )

    def test_the_engine_test_refuses_an_engine_import(self):
        """The control: the predicate, driven with the statements it must separate.

        Fabricated input, for the reason the module docstring gives, and on two
        axes. The *statements* are invented — most of them appear in no file here,
        which is the point: a predicate can only be shown to refuse what the tree
        does not contain. The *expected verdicts* are not invented; every one is
        read out of the pin, because "is this name the engine's" is a question
        about what ``django.template.<module>`` binds, and two of these cases are
        statements the pin really makes (``backends/dummy.py`` imports ``Origin``
        from the package root, and ``from django.template.backends.base import
        BaseEngine`` is ``backends/django.py``'s own line).

        The submodule cases are here because the arm deciding them was wrong until
        this round: it read the *module* and not the names, so
        ``from django.template.backends.django import Engine`` was certified
        plumbing — §3 declares that module not-the-engine and ``Engine`` is the
        engine, and the indirection alone made the difference. ``Engine`` imported
        from the package root is refused by the same predicate; the pair is why both
        are listed. ``DjangoTemplates`` from the same module is the licensed
        counterpart and not a concession: it is the class that module *defines*,
        and ``rust.py`` subclasses it.

        Three more near misses are deliberate. ``("django.template", ("mock",))``
        is ``unittest``'s name, which the pin binds in no module. The plural case,
        ``django.template.loaders``, is one ``s`` from §3's ``loader`` and is where
        our own loaders live, so a prefix test would refuse it. And
        ``("django.template.loader", ("TemplateDoesNotExist",))`` is the exception
        the root arm refuses when it is spelled ``from django.template import
        TemplateDoesNotExist``, reached here through a module §3 does license:
        route-independence is the property, so the two spellings must agree.

        ``.django/tests/template_tests/test_custom.py:4`` — ``from django.template.
        base import Node`` — is verbatim upstream's line and the engine. If the
        predicate returned False for it, the row above would pass over a declared
        engine import, which is the arrival both rows exist to stop, and the
        acceptance suite would be reporting on Django's engine.
        """
        cases = [
            (("django.template.base", ("Node",)), True, "upstream's own, and the engine"),
            (("django.template", ("mock",)), True, "a name Django has in no module"),
            (("django.template", ("Engine",)), True, "the engine class, from the root"),
            (("django.template", ("base",)), True, "the engine module, from the root"),
            (("django.template.loaders.app_directories", ("get_app_template_dirs",)), True, "ours, one letter from a licence"),
            (("django.template.backends.django", ("Engine",)), True, "the same class, one indirection away"),
            (("django.template.backends.django", ("InvalidTemplateLibrary",)), True, "likewise, from a module we do not have"),
            (("django.template.backends.django", ("TemplateDoesNotExist",)), True, "likewise, via the package root"),
            (("django.template.loader", ("TemplateDoesNotExist",)), True, "the loader's own re-export"),
            (("django.template.backends.dummy", ("Origin",)), True, "a root name, through a backend"),
            (("django.template.backends.django", ("DjangoTemplates",)), False, "the class that module defines"),
            (("django.template.loader", ("render_to_string",)), False, "§3's loader, a name it defines"),
            (("django.template.backends.base", ("BaseEngine",)), False, "plumbing to plumbing, defined there"),
            (("django.template.response", ("TemplateResponse",)), False, "likewise, and a declared adapter"),
            (("django.template", ("engines",)), False, "the EngineHandler facade"),
            (("django.template", ("loader", "engines")), False, "both root shapes, one statement"),
        ]
        wrong = [
            (module, names, why)
            for (module, names), want, why in cases
            if names_the_engine(module, names) is not want
        ]
        self.assertEqual(
            wrong,
            [],
            "the predicate disagreed with the cases its own row depends on "
            "(module, names, why it is listed):\n"
            f"  {wrong}",
        )

    def test_the_closure_propagates_a_name_across_two_hops(self):
        """The propagation branch, driven — the pin's own map cannot drive it.

        :func:`engine_reexports` reaches a fixed point over the pin's edges, and at
        6.0.9 that fixed point **adds nothing**: every name a plumbing module takes
        from another plumbing module is defined where it comes from, so the middle
        branch of :func:`close_reexports` yields the empty set on every edge the pin
        has. A branch whose body is empty on its only input is dead by construction
        — no row over the pin can tell the written loop from a single pass, or from
        ``found = set()`` — which is the shape this project keeps being caught by,
        so the map is supplied here instead.

        The synthetic map is ordered so a single pass **cannot** produce the answer:
        ``far`` is inserted before ``near``, dict iteration follows insertion order,
        and the one pass that would carry ``X`` into ``far`` has not yet filled
        ``near``. What makes that measurable is **order-independence** — the same
        map in the other insertion order must give the same result — because that is
        exactly the property a single pass lacks and a fixed point has. Both orders
        are run and compared.

        The modules are real plumbing names rather than invented strings, because the
        middle branch recognises its origin by :data:`PLUMBING` membership and
        ``django.template.base`` is a genuine engine module. ``Y`` is the negative
        control: ``near`` is known to expose only ``X``, so a name it does not carry
        must not be propagated through it.
        """
        engine = "django.template.base"
        near = "django.template.loader"
        far = "django.template.response"
        forward = close_reexports({far: [(near, ("X", "Y"))], near: [(engine, ("X",))]})
        backward = close_reexports({near: [(engine, ("X",))], far: [(near, ("X", "Y"))]})
        self.assertEqual(
            forward,
            backward,
            "the closure is order-dependent, which a fixed point cannot be and a "
            "single pass is — so the iteration is doing nothing",
        )
        self.assertIn("X", forward[near], "the engine name did not reach its importer")
        self.assertIn("X", forward[far], "the name did not cross two hops")
        self.assertNotIn(
            "Y",
            forward[far],
            "a name `near` does not expose was propagated through it",
        )

    def test_the_imports_are_exactly_the_declared_adapters(self):
        """The closed set. A new import fails here, which is the arrival to stop.

        The failure message prints every statement found, not only the difference:
        a count that has moved is a question, and the answer is the enumeration —
        the same rule ``m18_census.py --list`` follows after four wrong counts.
        """
        found = {
            (path, module, names) for path, module, names, _ in django_imports_in(SUITE)
        }
        unexpected = sorted(found - DECLARED_ADAPTERS)
        missing = sorted(DECLARED_ADAPTERS - found)
        self.assertEqual(
            (unexpected, missing),
            ([], []),
            "the vendored suite's Django imports have moved.\n"
            f"  not declared: {unexpected}\n"
            f"  declared but gone: {missing}\n"
            f"  everything found: {sorted(found)}\n"
            "An import that is not declared here needs classifying before it is "
            "added: an *adapter* (a public entry point that reaches the configured "
            "backend) is fine; an engine *implementation* is not, because it would "
            "make the row that imports it a test of Django.",
        )
