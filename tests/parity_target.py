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

    testcase.assertIn(
        source.name,
        guard.DECLARED,
        f"{source.name} is interposed but declares no differences from "
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
    _upstream, regions = guard.DECLARED[source.name]
    first = inspect.getsourcelines(target)[1]
    last = first + len(inspect.getsourcelines(target)[0]) - 1
    covered = [
        region for region in regions if not (last < region[0][0] or first > region[0][1])
    ]
    testcase.assertEqual(
        [],
        covered,
        f"{module_name} ({source.name}:{first}-{last}) is inside a declared "
        f"transcription region {covered}, so this comparison would be Rust "
        "against Rust",
    )
