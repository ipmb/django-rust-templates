"""What makes a parity comparison a comparison rather than a self-comparison.

Every `test_*_parity` module compares Rust against a Python implementation, and
each one carries a guard row asserting the Python side is genuinely Python.
Until M9 those rows said *the target file is Django's* and *the shim is not
installed in this process*. Both were proxies for one claim:

    the code the Python half of this comparison executes is not Rust.

M9 interposes `django.template.base`, and `check.sh` installs the shim for the
whole unit-test session, so neither proxy survives — the file is ours now, and
"not installed" is false by construction. The claim does, and this module
asserts it directly, in the form that keeps holding as more modules are
interposed:

* the target's file is either Django's pinned copy (the module still falls
  through) **or** a `# TRANSCRIBED:` transcription of it;
* if it is a transcription, `scripts/check_transcriptions.py` is clean *when
  this test runs* — not "was run at some point by `check.sh`". A warrant that
  lives in another command at another time is the exact hole this project
  already closed once for `DJANGO_RUST_TEMPLATES`, and it looks identical
  whether or not that command ran;
* the transcription's declared regions do not cover the code this comparison
  actually calls. Without that, a declared hunk could put the compared path on
  Rust and both sides of the equation would be Rust — `differential.py`'s
  "stock vs rust" failure, where stock *is* rust.

The last clause is the one that fails loudly when the interposition frontier
moves, which is why it is preferred here over freezing a copy of the pristine
module: a frozen copy has to grow with the frontier, silently, and a stale one
still looks like Django's.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
IMPL_DIR = PROJECT_ROOT / "python" / "django_rust_templates" / "_impl"


def key_of(source: Path) -> str:
    """``_impl``-relative posix path — the key the guard's tables use.

    ``source.name`` was the key until M10, and it was correct while ``_impl`` was
    flat. For the first file in a subpackage it is silently wrong in the worst
    available way: ``loaders/base.py`` keys as ``base.py``, which *is* a
    transcription — a different one — so ``DECLARED`` answers, the regions
    returned are ``base.py``'s, and every clause here passes while checking the
    wrong file. Nothing about that failure looks wrong: the regions are real
    regions of a real transcription, and they simply do not cover the lines of
    the file being interrogated, so the last two clauses — the ones that exist to
    catch a region on the compared path — are the ones that go quiet.

    The spelling is not chosen here. It is what ``_shim.shadowing_files()``
    reports, what ``check_transcriptions.py``'s ``DECLARED`` and
    ``COMPARED_PATH`` are keyed by, and what
    ``.review/m10-measurements/m10_measure_compared_path.py`` prints its rows in.
    """
    return source.relative_to(IMPL_DIR.resolve()).as_posix()


def assert_target_is_not_rust_owned(testcase, target, module_name):
    """Assert ``target``'s Python side is not Rust-delegated code.

    ``target`` is a class (or function) whose source file is the comparison's
    Python side; ``module_name`` is its dotted path in ``django.template``, which
    is what a transcription of it would be named in ``_impl``.
    """
    import inspect

    import scripts.check_transcriptions as guard

    source = Path(inspect.getsourcefile(target)).resolve()

    if source.is_relative_to(DJANGO_CHECKOUT.resolve()):
        # Still falling through: the target is Django's own file, which is the
        # strongest form of the claim and needs no corroboration.
        return

    testcase.assertTrue(
        source.is_relative_to(IMPL_DIR.resolve()),
        f"{module_name} came from {source}, which is neither Django's nor ours",
    )
    header = source.read_text()[:600]
    testcase.assertIn(
        "# TRANSCRIBED:",
        header,
        f"{source} is ours but is not marked as a transcription, so nothing "
        "checks it against upstream",
    )

    key = key_of(source)

    testcase.assertIn(
        key,
        guard.DECLARED,
        f"{key} is interposed but declares no differences from "
        "upstream, so nothing checks it — add it to "
        "scripts/check_transcriptions.py in the commit that creates it",
    )

    failures = guard.check()
    testcase.assertEqual(
        [],
        failures,
        "the transcription has drifted, so this comparison's Python side is no "
        f"longer upstream's: {failures}",
    )

    # The compared path must be inside the part of the file that is *unchanged*.
    # Two checks, because the first is a proxy for the second.
    regions = guard.declared_regions(key)

    # The target's own lines, cheap and general: it works for any module, and
    # it is the check that has teeth before anyone has measured a module.
    first = inspect.getsourcelines(target)[1]
    last = first + len(inspect.getsourcelines(target)[0]) - 1
    covered = [
        region for region in regions if not (last < region[0][0] or first > region[0][1])
    ]
    testcase.assertEqual(
        [],
        covered,
        f"{module_name} ({key}:{first}-{last}) is inside a declared "
        f"transcription region {covered}, so this comparison would be Rust "
        "against Rust",
    )

    # And every line the harness *executes*, which is the real claim.
    #
    # The check above is a proxy and the reviewer said so: `Parser.parse` calls
    # `extend_nodelist`, `compile_filter`, `invalid_block_tag` and every tag's
    # compile function, so a region declared inside a helper is on the compared
    # path while sitting outside `parse`'s own lines. The measured reach for
    # `base.py` is 28 functions spanning lines 116-1209 — nearly the whole
    # module — so for a transcription with a `COMPARED_PATH` entry the practical
    # rule is that no region may be in the module body at all.
    compared = guard.COMPARED_PATH.get(key, [])
    executed = [
        (label, first_line, last_line)
        for first_line, last_line, label in compared
        if any(
            guard.overlaps(region[0], first_line, last_line) for region in regions
        )
    ]
    testcase.assertEqual(
        [],
        executed,
        f"{key}: the parity harness executes {executed}, and a declared "
        "transcription region covers it, so this comparison would be Rust "
        "against Rust",
    )


_PINNED_RESOLVE_LOOKUP = None


def pinned_resolve_lookup():
    """Django's own ``Variable._resolve_lookup``, executed from ``.django``.

    M9's Task 5 delegates our ``Variable._resolve_lookup`` to
    ``_native.resolve``. Every harness that compared ``_native.resolve`` against
    ``Variable._resolve_lookup`` therefore began comparing Rust against Rust —
    and comparing it *successfully*, since both sides are now one function. That
    is precisely the failure ``assert_target_is_not_rust_owned`` above exists to
    refuse, arriving where that guard cannot see it: the guard's
    ``COMPARED_PATH`` is the reach of the *parse* harness, and none of these is
    a parse.

    So this is the replacement Python side — upstream's method, byte for byte,
    read out of the pinned checkout by ``ast``. The five users are
    ``tests/test_render_parity.py::ResolverParityTests``,
    ``tests/test_resolve_invalid.py``, ``tests/test_resolve_callarm.py``,
    ``tests/test_m8_side_effects.py`` and the resolver half of
    ``tests/differential/cases.py``'s neighbourhood.

    **Returned unbound**, because it is a method: call it as
    ``pinned_resolve_lookup()(variable, context)``. The ``Variable`` *instance*
    is ours and stays ours, which is deliberate rather than a compromise — the
    transcription of ``Variable.__init__`` is what produces the ``lookups`` both
    sides read, so sharing it keeps the comparison about the resolver instead of
    about how the path was split.

    ``exec``'d into a copy of the interposed module's globals, so the names the
    body reaches — ``VariableDoesNotExist``, ``BaseContext``, ``signature``,
    ``logger`` — are the same objects the render path uses. All four are
    Django's own (``signature`` is ``inspect``'s; the other three are
    transcribed unchanged and are not Rust-backed), which is what makes this a
    comparison of the resolver rather than of its dependencies.

    Not ``importlib``-by-name for the method, for the reason
    ``tests/test_base_parity.py`` records: under the shim the name
    ``django.template.base`` resolves to *our* file, so importing it would hand
    back the very delegation this exists to replace.
    """
    global _PINNED_RESOLVE_LOOKUP
    if _PINNED_RESOLVE_LOOKUP is not None:
        return _PINNED_RESOLVE_LOOKUP

    import ast
    import importlib

    path = DJANGO_CHECKOUT / "django" / "template" / "base.py"
    source = path.read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == "Variable":
            break
    else:  # pragma: no cover - the pinned tree has it
        raise AssertionError(f"no class Variable in {path}")
    for child in node.body:
        if isinstance(child, ast.FunctionDef) and child.name == "_resolve_lookup":
            break
    else:  # pragma: no cover - the pinned tree has it
        raise AssertionError(f"Variable._resolve_lookup is not in {path}")

    namespace = dict(importlib.import_module("django.template.base").__dict__)
    exec(compile(ast.get_source_segment(source, child), str(path), "exec"), namespace)
    _PINNED_RESOLVE_LOOKUP = namespace["_resolve_lookup"]
    return _PINNED_RESOLVE_LOOKUP
