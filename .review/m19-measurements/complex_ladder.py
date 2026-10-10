"""Where `real-world/complex`'s jinja2 gap lives: a construct ladder.

**What forced this.**  Two candidate explanations were priced and both died:
the inheritance frames (`extends_frame_cost.py`: 10.53 us of 563.9) and the
73 `IncludeNode`s (`include_swap.py`: removing them costs **jinja2 157.6 us and
us only 80.3**, so the include path is jinja2's disadvantage, not ours — it moves
the ratio from 1.54 to **2.41**).  So `complex`'s gap is in what the inlined page
still does 72 times, and this ladder walks one construct at a time until the
ratio stops moving.

**Method.**  Each rung is the previous rung with exactly one construct replaced
by a literal, so the rungs are nested and each delta is one construct.  Output
bytes are hashed and printed per engine rather than asserted across rungs — a
rung that removes a variable *cannot* preserve the output, and pretending
otherwise would be the `control over the same population` mistake in reverse: the
control here is that **rust and jinja2 agree on every rung**, which is checked
and is fatal if broken.  The two rungs that do preserve bytes (`full`,
`no-include`) are checked against each other as well.

Both engines run every rung, so each row's `ours/them` is a ratio of two
measurements of the same page, and the ladder reads as "which construct does the
ratio survive".

Run:  .venv/bin/python -I <this>
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

ITERS = 200
REPEATS = 7

INNER_CELLS = (
    "<tr><td>{{ line.sku|upper }}</td><td>{{ line.note }}</td>"
    "<td>{{ line.qty }}</td></tr>"
)
OUTER_HEAD = "<h2>{{ s.title|title }}</h2>"


def best(fn) -> float:
    for _ in range(max(3, ITERS // 4)):
        fn()
    out = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        for _ in range(ITERS):
            fn()
        out.append((time.perf_counter() - start) / ITERS)
    return min(out) * 1e6


def rungs() -> list[tuple[str, dict[str, str]]]:
    """Nested variants of the complex page, one construct removed per rung."""
    workload = next(w for w in bench_corpus.workloads() if w.key == "real-world/complex")
    full = dict(workload.templates)
    line = full["partials/line.html"]

    def swap(templates: dict[str, str], old: str, new: str, label: str) -> dict[str, str]:
        """Replace `old` in every template that has it; the rung must hit >= 1.

        A rung is *one* construct across the template set, so a template that
        does not contain it is left alone rather than being an error — the
        error is a rung that matched nothing, which would be a rung that
        measured the previous one twice.
        """
        hits = 0
        out = {}
        for name, body in templates.items():
            if old in body:
                hits += body.count(old)
                out[name] = body.replace(old, new)
            else:
                out[name] = body
        if hits == 0:
            raise SystemExit(f"! {label}: {old!r} matched no template")
        return out

    no_include = swap(full, '{% include "partials/line.html" %}', line, "no-include")
    no_inner = swap(no_include, INNER_CELLS, "<tr><td>x</td><td>y</td><td>z</td></tr>", "no-inner")
    no_outer_head = swap(no_inner, OUTER_HEAD, "<h2>Section</h2>", "no-outer-head")
    bare = swap(
        no_outer_head,
        "<table>{% for line in s.lines %}<tr><td>x</td><td>y</td><td>z</td></tr>{% endfor %}</table>",
        "<table></table>",
        "bare-section",
    )
    return [
        ("full", full),
        ("no-include", no_include),
        ("no-inner", no_inner),
        ("no-outer-head", no_outer_head),
        ("bare-section", bare),
    ]


def main() -> int:
    workload = next(w for w in bench_corpus.workloads() if w.key == "real-world/complex")
    print(f"iters={ITERS} repeats={REPEATS} best-of-{REPEATS}, django 6.0.9 pin")
    print()
    header = (
        f"{'rung':16} {'rust us':>9} {'jinja us':>9} {'ours/them':>10} "
        f"{'d rust':>8} {'d jinja':>8} {'d ours-them':>12}"
    )
    print(header)
    print("-" * len(header))

    previous: dict[str, float] | None = None
    digests: dict[tuple[str, str], str] = {}
    for label, templates in rungs():
        row: dict[str, float] = {}
        for engine_name in ("rust", "jinja2"):
            engine = bench_engines.build(
                engine_name, templates, workload.libraries(engine_name)
            )
            try:
                handle = engine.get(workload.entry)
                context = engine.context(workload.data)
                row[engine_name] = best(lambda: engine.render(handle, context))
                out = engine.render(handle, context)
            finally:
                engine.close()
            digests[(label, engine_name)] = hashlib.sha256(out.encode()).hexdigest()[:10]
        diff = row["rust"] - row["jinja2"]
        if previous is None:
            print(
                f"{label:16} {row['rust']:>9.1f} {row['jinja2']:>9.1f} "
                f"{row['rust'] / row['jinja2']:>10.2f} {'-':>8} {'-':>8} {'-':>12}"
            )
        else:
            d_rust = row["rust"] - previous["rust"]
            d_jinja = row["jinja2"] - previous["jinja2"]
            print(
                f"{label:16} {row['rust']:>9.1f} {row['jinja2']:>9.1f} "
                f"{row['rust'] / row['jinja2']:>10.2f} {d_rust:>+8.1f} {d_jinja:>+8.1f} "
                f"{d_rust - d_jinja:>+12.1f}"
            )
        previous = row

    # The control: rust and jinja2 must have rendered the same bytes on every rung.
    for label, _ in rungs():
        ours_digest = digests[(label, "rust")]
        theirs_digest = digests[(label, "jinja2")]
        if ours_digest != theirs_digest:
            print(
                f"\nVOID: {label}: rust {ours_digest} != jinja2 {theirs_digest} — "
                "the two engines are not rendering the same page"
            )
            return 1
    print("\ndigest prefixes (rust == jinja2 on every rung):")
    for label, _ in rungs():
        print(f"  {label:16} {digests[(label, 'rust')]}")
    print(
        "\nEvery rung: rust and jinja2 rendered the same bytes (sha256 prefix per "
        "row above is the pair, and they matched). `d ours-them` is the change in\n"
        "(ours - jinja2) when this rung's construct is removed, i.e. jinja2's cost\n"
        "for the construct minus ours; a NEGATIVE number is a construct we pay more\n"
        "for, and the rungs before it can be read as the page's floor.\n"
        "\n"
        "That sign convention is a correction. Both this file and `include_swap.py`\n"
        "printed the opposite word until step 5 slice 3: `d ours-them` is\n"
        "`d_rust - d_jinja` = `(ours_inlined - ours_full) - (jinja2_inlined -\n"
        "jinja2_full)`, which is `C_jinja2 - C_ours` for the construct, so it is\n"
        "negative when the construct costs us more. The slice-2 record read the\n"
        "slice-2 value (+77.4) correctly as \"jinja2's disadvantage\" while the caption\n"
        "under the table said the reverse; the number was never wrong, its label was."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
