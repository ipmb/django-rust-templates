#!/usr/bin/env python
"""The benchmark's HTML report: one self-contained file, no network, no libraries.

`bench.py --html` calls `render(payload)` here. The payload is the full record of a
run — every (workload, mode, engine) measurement, its output hash, its iteration
count and its spread.

**Charts, not decoration.** The corpus spans two and a half orders of magnitude
(0.4 us to 17 ms) within a single family, so a linear axis would draw two of the
three engines as invisible slivers and the third as a wall — a chart that lies by
omission. The x-axis is therefore logarithmic and *says so on the axis*, every bar
carries its value as a direct label, and the full table is on the same page. A log
axis without those three is an argument rather than a measurement, and dropping
any one of them would make this worse than the table alone.

**Three engines is three categorical hues, in a fixed order, never cycled.** The
order is the same in every chart and in every legend, so `rust` is the same colour
wherever the reader looks; a panel that repainted its series would make the reader
re-learn it per panel. The palette is the dataviz reference instance's first three
categorical slots — minijinja's slot is retired, not reassigned, so no surviving
series changed colour — validated in both modes (adjacent-pair CVD deltaE 9.2 light
/ 9.4 dark, normal-vision deltaE 27.6 / 26.5). One light step sits below 3:1 against
the surface, which obliges *relief* — visible labels or a table view — and this
report ships both rather than picking.

**Every caveat is printed where its number is.** A row that excludes engines names
them and says why, on the row; a row whose engines disagree says so, on the row; the
Context caveat sits under the table it qualifies, not in a footnote. A reader who
scrolls to the middle of this page and reads one row has everything that row needs.
"""

from __future__ import annotations

import html
import math
import re

# The reference palette's first three categorical slots. Dark is the same three hues
# re-stepped for the dark surface, not a flip of the light ones — which is why both
# columns were validated separately rather than derived from each other.
SERIES_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a")
SERIES_DARK = ("#3987e5", "#d95926", "#199e70")

BAR = 13.0  # bar height
BAR_GAP = 3.0  # surface gap between the bars of one group
GROUP_GAP = 17.0  # gap between one workload's group and the next
GUTTER = 216.0  # left gutter for workload labels
VALUE_COL = 124.0  # right margin for the direct labels
TOP = 34.0  # legend band
WIDTH = 1200.0

# The byte-agreement tile's direction, and it is the one direction on this page that
# is not a timing. `bench.py`'s `MODE_METRIC` cannot supply it: that table is keyed by
# mode, and agreement is not measured in a mode — a row agrees or it does not, under
# either. It is a named constant rather than a literal in the tile's f-string for the
# same reason `MODE_METRIC` lives in the payload: a direction spelled out inside the
# sentence it applies to is a direction that can be edited away from the number it
# describes without anything failing.
AGREEMENT_BETTER = "higher"


# --- number formatting -------------------------------------------------------------


def _fmt(us: float) -> str:
    """Microseconds for an axis tick or a bar label, at a size-suited precision.

    Ticks and bar labels may switch unit (``1.00 ms`` beside ``500``) because an
    axis spans decades and a whole unit name is clearer than five digits. The
    table does **not** use this — see ``_fmt_us``.
    """
    if us >= 1000:
        return f"{us / 1000:.2f} ms"
    if us >= 100:
        return f"{us:.0f}"
    if us >= 10:
        return f"{us:.1f}"
    return f"{us:.2f}"


def _fmt_us(us: float) -> str:
    """Microseconds, always, with a thousands separator and never a millisecond.

    A table column that mixes ``455`` and ``1.22 ms`` forces the reader to convert
    to compare two cells that sit one above the other, which is the one thing a
    column must never do. The unit is in the header instead, once.
    """
    if us >= 1000:
        return f"{us:,.0f}"
    if us >= 100:
        return f"{us:.0f}"
    if us >= 10:
        return f"{us:.1f}"
    return f"{us:.2f}"


def _ratio(us: float, control: float) -> str:
    """`us` against the control, at a precision that keeps small ratios readable."""
    ratio = us / control
    if ratio >= 0.1:
        return f"{ratio:.2f}x"
    return f"{ratio:.3f}x"


def _geomean(values: list[float]) -> float:
    """The mean a ratio wants: ratios multiply, so their log-average is the mean."""
    return math.exp(sum(math.log(v) for v in values) / len(values))


# --- shaping the record list -------------------------------------------------------


def _group(records: list[dict]) -> dict:
    """{(family, mode): {workload: {engine: record}}} in the corpus's own order."""
    out: dict[tuple, dict] = {}
    for rec in records:
        out.setdefault((rec["family"], rec["mode"]), {}).setdefault(rec["key"], {})[
            rec["engine"]
        ] = rec
    return out


def _ran_shared(group: dict, ran: list[str]) -> bool:
    """Whether every workload in this group was run by the whole engine set.

    **The count comes from the payload, not from the palette.** It used to read
    `len(SERIES_LIGHT)`, which is a presentation constant: one hue per engine and
    not allowed to cycle, so the two move together *when the run has three engines*.
    Under `--engines django,rust` they do not move together at all, and the palette
    happily reported "the whole engine set" for a two-engine run — which is how the
    agreement tile came out `0/0` over a corpus that had agreed everywhere, labelled
    "three-engine rows". `ran` is what actually ran.
    """
    return all(len(per) == len(ran) for per in group.values())


def _ticks(lo: float, hi: float) -> list[float]:
    """Decade and half-decade ticks inside [lo, hi] — the ticks a log reader expects."""
    out = []
    for decade in range(-2, 6):
        for mult in (1.0, 2.0, 5.0):
            value = mult * 10.0**decade
            if lo <= value <= hi:
                out.append(value)
    return out


# --- one chart ----------------------------------------------------------------------


def _svg(
    path: str, group: dict, engines: list[str], present: list[str], metric: dict
) -> str:
    """A grouped horizontal bar chart: one bar per engine, one group per workload.

    `engines` is every engine in the run, in the run's order; `present` is the
    subset that ran any row of this panel. The two are separate because the colour
    index is taken from `engines` and never from `present`: a panel that dropped a
    series and repainted the survivors would make `rust` a different colour here
    than in the panel above, and the reader would have to re-learn the legend.

    An engine absent from every row is named once in the panel's caption instead of
    being drawn as a row of italic "cannot render" text under every workload — ten
    identical placeholder lines draw the eye to the absence rather than to the
    measurements, and the absence is one fact about the panel, not one per row.

    `metric` is the mode's entry from `bench.MODE_METRIC`, and it is what makes the
    chart say **which direction is better** on its own face — in the axis label
    inside the SVG and in the `aria-label`, not only in a caption beside it. That
    matters here more than it looks: every chart in this report is currently
    "lower is better", so a chart that states nothing reads as correct, and the way
    a wrong one arrives is a future mode whose number is throughput. Reading it
    from the payload rather than spelling it in this function means a new mode
    cannot inherit the old direction without saying so.
    """
    direction = f'{metric["better"]} is better'
    keys = list(group)
    height = TOP + len(keys) * (len(present) * (BAR + BAR_GAP) + GROUP_GAP) + 36
    values = [r["best_us"] for per in group.values() for r in per.values()]
    lo = 10 ** math.floor(math.log10(min(values)))
    hi = 10 ** math.ceil(math.log10(max(values)))
    span = math.log10(hi) - math.log10(lo)
    plot_x0, plot_x1 = GUTTER, WIDTH - VALUE_COL
    slot = {engine: engines.index(engine) for engine in present}

    def x_of(us: float) -> float:
        return plot_x0 + (math.log10(us) - math.log10(lo)) / span * (plot_x1 - plot_x0)

    out = [
        f'<svg class="chart" viewBox="0 0 {WIDTH:.0f} {height:.0f}" role="img" '
        f'aria-label="{html.escape(metric["unit"])}, log scale, for {html.escape(path)}; '
        f'{html.escape(direction)}">',
        '<g class="legend">',
    ]
    lx = GUTTER
    for engine in present:
        i = slot[engine]
        out.append(
            f'<rect x="{lx:.0f}" y="8" width="11" height="11" rx="2.5" class="s{i}"/>'
            f'<text x="{lx + 17:.0f}" y="18" class="lg">{html.escape(engine)}</text>'
        )
        lx += 28 + len(engine) * 8.0
    out.append(
        f'<text x="{WIDTH - VALUE_COL + 8:.0f}" y="18" class="lg muted" '
        f'text-anchor="end">x axis: {html.escape(metric["unit"])}, log scale '
        f"&middot; <tspan class=\"dir\">{html.escape(direction)}</tspan></text></g>"
    )
    for tick in _ticks(lo, hi):
        tx = x_of(tick)
        out.append(
            f'<line x1="{tx:.1f}" y1="{TOP - 4:.0f}" x2="{tx:.1f}" '
            f'y2="{height - 32:.0f}" class="grid"/>'
            f'<text x="{tx:.1f}" y="{height - 14:.0f}" class="tick" '
            f'text-anchor="middle">{html.escape(_fmt(tick))}</text>'
        )
    y = TOP
    for workload in keys:
        per_engine = group[workload]
        control = per_engine.get("django")
        out.append(
            f'<text x="{GUTTER - 14:.0f}" '
            f'y="{y + len(present) * (BAR + BAR_GAP) / 2 + 4:.0f}" class="wl" '
            f'text-anchor="end">{html.escape(workload)}</text>'
        )
        for engine in present:
            i = slot[engine]
            rec = per_engine.get(engine)
            by = y + i * (BAR + BAR_GAP)
            if rec is None:
                # Reached only when the engine ran some other row of this panel, so
                # the reader is owed a mark where the bar would have been.
                out.append(
                    f'<text x="{plot_x0 + 8:.0f}" y="{by + BAR - 3:.0f}" class="absent">'
                    f"this engine cannot render this row &mdash; see the table</text>"
                )
                continue
            x1 = x_of(rec["best_us"])
            attrs = (
                f'data-engine="{html.escape(engine)}" '
                f'data-workload="{html.escape(workload)}" '
                f'data-us="{rec["best_us"]:.4f}" data-worst="{rec["worst_us"]:.4f}" '
                f'data-iters="{rec["iters"]}" data-bytes="{rec["bytes"]}" '
                f'data-sha="{rec["sha256"][:16]}"'
            )
            if control is not None and engine != "django":
                attrs += f' data-ratio="{rec["best_us"] / control["best_us"]:.4f}"'
            out.append(
                f'<rect class="bar s{i}" x="{plot_x0:.0f}" y="{by:.1f}" '
                f'width="{max(x1 - plot_x0, 3.0):.1f}" height="{BAR:.0f}" rx="4" '
                f"{attrs}/>"
            )
            label = _fmt(rec["best_us"])
            if control is not None and engine != "django":
                label += f" {_ratio(rec['best_us'], control['best_us'])}"
            out.append(
                f'<text x="{plot_x1 + 8:.0f}" y="{by + BAR - 3:.0f}" class="val">'
                f"{html.escape(label)}</text>"
            )
        y += len(present) * (BAR + BAR_GAP) + GROUP_GAP
    out.append("</svg>")
    return "".join(out)


# --- the parts of the page -----------------------------------------------------------


def _tile(value: str, label: str, note: str, direction: str = "") -> str:
    """One hero tile.

    `direction` is the payload's `better` for whatever this tile counts, and it is
    an argument rather than a phrase written into `note` so that the badge is built
    in exactly one place and the note stays plain escaped prose. The tiles need it
    individually because they do not agree: the two ratio tiles and the
    byte-agreement tile point opposite ways.
    """
    badge = (
        f'<span class="dir">{html.escape(direction)} is better</span> '
        if direction
        else ""
    )
    return (
        f'<div class="tile"><div class="tv">{html.escape(value)}</div>'
        f'<div class="tl">{html.escape(label)}</div>'
        f'<div class="tn">{badge}{html.escape(note)}</div></div>'
    )


def _count_word(n: int) -> str:
    """`3` -> `three`, for a label that reads as a sentence rather than a report."""
    return {1: "one", 2: "two", 3: "three", 4: "four"}.get(n, str(n))


def _heroes(grouped: dict, modes: list[str], shared: set, metrics: dict, engines: list[str]) -> str:
    """The numbers that answer "how does ours compare", before any chart.

    Every tile says which way is better, in its own `note` and not only in the one
    above it, because the tiles do not agree with each other: the ratio tiles and
    the byte-agreement tile point opposite ways, and the fastest-engine tile is not
    a ratio at all but a win count with a scope written into its own note. A reader
    who reads one tile has no way to infer the next one's direction.
    """
    out = []
    for mode in modes:
        ratios = [
            per["rust"]["best_us"] / per["django"]["best_us"]
            for (family, m), by_workload in grouped.items()
            if family == "real-world" and m == mode
            for per in by_workload.values()
            if "rust" in per and "django" in per
        ]
        if ratios:
            out.append(
                _tile(
                    f"{_geomean(ratios):.2f}x",
                    f"ours vs stock Django — real-world / {mode}",
                    f"geometric mean over {len(ratios)} pages, unweighted, so the "
                    f"smallest page counts once and no more; 1.00x is parity and "
                    f"below it we win",
                    metrics[mode]["better"],
                )
            )
    out.append(_fastest_tile(grouped, metrics))
    agree = 0
    total = 0
    n = len(engines)
    for path in shared:
        for per in grouped[path].values():
            # **A row only one engine ran is not agreement.** The predicate below is
            # a one-element set for such a row and reads true, so the guard is what
            # keeps "they all agree" from meaning "only one of them spoke".
            if len(per) < 2:
                continue
            total += 1
            if len({r["sha256"] for r in per.values()}) == 1:
                agree += 1
    out.append(
        _tile(
            f"{agree}/{total}",
            f"{_count_word(n)}-engine rows that byte-agree",
            f"every one of these rows is a row where {_count_word(n)} independent "
            f"engines produced the same bytes. The shared corpus is the measured "
            f"syntax intersection, not a preference",
            AGREEMENT_BETTER,
        )
    )
    return "".join(out)


def _winners(grouped: dict, family: str, mode: str) -> list:
    """The per-engine records of every row in one (family, mode) that >=2 engines ran."""
    out = []
    for (fam, m), by_workload in grouped.items():
        if fam != family or m != mode:
            continue
        for per in by_workload.values():
            if len(per) >= 2:
                out.append(per)
    return out


def _fastest_tile(grouped: dict, metrics: dict) -> str:
    """Who is fastest — stated at the scope where the question has an answer.

    **This tile used to count winners over every row, and that count was the
    defect.** The rows are not exchangeable: `real-world` is 3 whole pages while
    `micro` is 10 one-construct templates, and `parse` answers the opposite way to
    `render`, because Jinja2 compiles to Python source and Django does not. Pooled,
    the count reads "django 19, jinja2 7" — a headline that a reader takes as
    "Django is faster", while Jinja2 wins **every whole-page render**, by 2.5x-3.3x.

    Both sentences are true and only one of them is what a reader wants, so the
    tile states the one that answers "which engine should I use to render a page",
    and the note says what the pooled count is and why it says `django`. A number
    that misleads is worse than a number that is absent, and the honest version of
    this one is a *scope*, not a bigger sample.
    """
    page = _winners(grouped, "real-world", "render")
    if not page:
        return ""
    wins: dict[str, int] = {}
    for per in page:
        best = min(per.values(), key=lambda r: r["best_us"])
        wins[best["engine"]] = wins.get(best["engine"], 0) + 1
    leader = min(wins, key=lambda e: (-wins[e], e))

    # The ratio the leader actually wins by, against the control, when the leader is
    # jinja2 and the control ran. Computed rather than asserted, and skipped rather
    # than guessed if either is absent.
    ratios = sorted(
        per["jinja2"]["best_us"] / per["django"]["best_us"]
        for per in page
        if leader == "jinja2" and "jinja2" in per and "django" in per
    )
    spread = (
        f", at {ratios[0]:.2f}x-{ratios[-1]:.2f}x stock Django's own time"
        if ratios
        else ""
    )

    pooled: dict[str, int] = {}
    pooled_rows = 0
    for per in _winners(grouped, "micro", "render") + _winners(grouped, "micro", "parse") + page + _winners(grouped, "real-world", "parse"):
        pooled_rows += 1
        best = min(per.values(), key=lambda r: r["best_us"])
        pooled[best["engine"]] = pooled.get(best["engine"], 0) + 1
    pooled_leader = min(pooled, key=lambda e: (-pooled[e], e))

    note = (
        f"{leader} wins every one of these {len(page)} pages{spread}. Counting "
        f"winners over the shared corpus instead reads “{pooled_leader} "
        f"{pooled[pooled_leader]} of {pooled_rows}”: that pools in the parse rows, "
        f"where the answer is the other way round, and the micro rows, which the "
        f"family's own note explains. It is not the whole corpus — the django-only "
        f"rows are outside it, because Jinja2 cannot render them."
    )
    return _tile(
        f"{leader} {wins[leader]}/{len(page)}",
        "fastest on whole-page renders — real-world / render",
        note,
        metrics["render"]["better"],
    )


def _table(grouped: dict, engines: list[str], families: dict, metrics: dict, floor: dict) -> str:
    """Every measurement, one row per workload.

    The direction goes in **two** places, because the table holds every mode at
    once and they are not interchangeable. The engine column says the unit and the
    direction, which is what a reader needs to read a single cell; the family banner
    row says it again beside the mode name, because the mode is what picks the
    metric and the banner is the only place the mode is written.

    The column header is one per engine, not one per (engine, mode) — a row is one
    workload under one mode, so the mode is already fixed by the banner, and a
    per-mode column header would promise a layout the body does not have.
    """
    units = {metrics[m]["unit"] for m in metrics}
    betters = {metrics[m]["better"] for m in metrics}
    # Every mode is microseconds-per-call and every one is lower-is-better today, so
    # this collapses to one sentence. If a mode ever inverts, the header can no
    # longer speak for the table and the banner does the work alone — saying so is
    # cheaper than a header that is true of half the rows under it.
    shared_sub = ""
    if len(units) == 1 and len(betters) == 1:
        unit = next(iter(units))
        better = next(iter(betters))
        shared_sub = (
            f'<br><span class="sub">{html.escape(unit)}<br>'
            f'<span class="dir">{html.escape(better)} is better</span></span>'
        )
    head = "".join(f"<th>{html.escape(e)}{shared_sub}</th>" for e in engines)
    order = {family: i for i, family in enumerate(families)}
    rows = []
    for family, mode in sorted(grouped, key=lambda k: (order.get(k[0], 99), k[1])):
        group = grouped[(family, mode)]
        rows.append(
            f'<tr class="fam"><td colspan="{3 + len(engines)}">{html.escape(family)}'
            f" / {html.escape(mode)}"
            f'<span class="dirhead">'
            f'{html.escape(metrics[mode]["better"])} is better</span></td></tr>'
        )
        for workload, per_engine in group.items():
            control = per_engine.get("django")
            cells = []
            for engine in engines:
                rec = per_engine.get(engine)
                if rec is None:
                    cells.append('<td class="na">&mdash;</td>')
                    continue
                extra = ""
                if control is not None and engine != "django":
                    vs = _ratio(rec["best_us"], control["best_us"])
                    extra = f'<span class="r"> vs stock {vs}</span>'
                marker = '<span class="ok">&equiv;</span>'
                if control is not None and rec["sha256"] != control["sha256"]:
                    marker = '<span class="no">&ne;</span>'
                spread = rec["worst_us"] / rec["best_us"] if rec["best_us"] else 1.0
                # The share of this cell that is the call itself, from the engine's
                # own floor *for this mode* — `render`'s floor is the render call,
                # `compile`'s is the compile of an empty source. Capped at 100
                # because the row and the floor are two separate measurements: a row
                # that adds almost nothing to the floor can land a hair above it, and
                # "104% call" is a sentence that means nothing. The legend says so.
                call = ""
                if floor and rec["best_us"]:
                    share = floor.get(mode, {}).get(engine, 0.0) / rec["best_us"] * 100
                    if share >= 1:
                        call = f"call {min(share, 100.0):.0f}% &middot; "
                    elif share > 0:
                        call = "call &lt;1% &middot; "
                cells.append(
                    f'<td><b>{html.escape(_fmt_us(rec["best_us"]))}</b>{extra}'
                    f'<br><span class="sub">{call}spread {spread:.2f}x &middot; '
                    f"{rec['bytes']} B &middot; {marker}</span></td>"
                )
            # Every record of a workload carries the same note, so the first
            # non-empty one found is the row's note.
            note = next((r["note"] for r in per_engine.values() if r["note"]), "")
            if not note:
                note = '<span class="na">in the three-engine intersection</span>'
            rows.append(
                f'<tr><td class="wk">{html.escape(workload)}</td>'
                f'<td class="r">{note}</td>{"".join(cells)}</tr>'
            )
    return (
        f'<table class="tbl"><thead><tr><th>workload</th>'
        f"<th>why the absent engines are absent</th>{head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _disagreements(payload: dict) -> str:
    """Rows where an engine's bytes differ from the control's, listed on the page."""
    lines = []
    for flat, verdict in sorted(payload["verdicts"].items()):
        others = [e for e in verdict.get("differ", []) if e != "rust"]
        if others:
            key, mode = flat.split("|")
            lines.append(
                f"<li><b>{html.escape(key)}</b> / {html.escape(mode)}: "
                f"{html.escape(', '.join(others))} produced different bytes from "
                f"stock Django.</li>"
            )
    if not lines:
        return (
            '<p class="ok-note">Every row that more than one engine ran produced '
            "<b>byte-identical output on every engine that ran it</b>. A "
            "disagreement would be listed here rather than averaged into a timing.</p>"
        )
    return '<ul class="caveat">' + "".join(lines) + "</ul>"


def _environment(payload: dict) -> str:
    meta = payload["meta"]
    args = payload["args"]
    pairs = [
        ("django", meta["django"]),
        ("cpython", meta["cpython"]),
        ("platform", meta["platform"]),
        ("machine", meta["machine"]),
        ("cpus", meta["cpu_count"]),
        ("native extension", payload["native"]),
        ("samples", f"{args['repeats']} timed samples per row; the best is reported"),
        (
            "calls per sample",
            f"about {args['target']} s of work each, calibrated once per row on the "
            f"control and used for every engine on that row",
        ),
        ("reported at", meta["date"]),
    ]
    return "".join(
        f"<dt>{html.escape(k)}</dt><dd>{html.escape(str(v))}</dd>" for k, v in pairs
    )


# --- the page -------------------------------------------------------------------------


def render(payload: dict) -> str:
    """The whole report as one self-contained HTML document."""
    grouped = _group(payload["records"])
    engines = payload["ran"]
    modes = payload["modes"]
    shared = {path for path, group in grouped.items() if _ran_shared(group, engines)}

    panels = []
    for mode in modes:
        metric = payload["metrics"][mode]
        for family in payload["families"]:
            path = (family, mode)
            if path not in grouped:
                continue
            group = grouped[path]
            here = [e for e in engines if any(e in per for per in group.values())]
            gone = [e for e in engines if e not in here]
            # The direction leads, and the axis inside the SVG repeats it, because
            # the caption is what a reader skims and the axis is what they look at
            # once they are reading the marks. `_svg`'s docstring has why the
            # direction is in the payload instead of written here.
            #
            # The caption carries markup of its own now (`<span class="dir">`), so
            # the interpolated parts are escaped one at a time rather than the whole
            # string being passed through `html.escape` — which would emit the spans
            # as text and is not a thing a reader would report as a bug.
            # The direction is on the `<h3>` badge directly above this line, so the
            # caption does not repeat the badge — it starts on what the number *is*.
            # Said once beside the panel name, it is read while scanning; said twice
            # in a row it reads as a stutter and stops being read at all.
            caption = (
                f'{html.escape(metric["what"])}'
                f' &middot; {html.escape(payload["families"][family].rstrip("."))}'
            )
            if gone:
                caption += (
                    f". {html.escape(' and '.join(gone))} cannot render any row of "
                    f"this family; the table says why, row by row"
                )
            caption += (
                '. The <span class="tok">N.NN&times;</span> beside a bar is that row '
                "against django, so below 1.00 is faster"
            )
            # **The call share is computed here, not written into the family's own
            # note.** It is a percentage of the very cells the table below prints, so
            # a figure typed once beside a table that moves every run goes stale in
            # the direction that flatters — and the first draft of this caption did
            # exactly that, claiming 51%-96% while the cells said 32%-100%. Both
            # terms come from the payload: `floor` is the measurement and `group` is
            # the population.
            floor = payload.get("floor", {}).get(mode, {})
            # Capped exactly as the cells are, or the caption and the table below it
            # disagree: the first run printed "2%-102%" here while the cell it came
            # from printed "100%".
            shares = [
                min(floor[name] / rec["best_us"] * 100, 100.0)
                for per in group.values()
                for name, rec in per.items()
                if name in floor and rec["best_us"]
            ]
            if shares:
                caption += (
                    f'. The empty-template floor is {min(shares):.0f}%-'
                    f"{max(shares):.0f}% of these cells: at the low end the row is "
                    f"the construct, and at the high end it is the call"
                )
            # Every panel but the first mode's is hidden in the markup rather than
            # by script on load, so the page is correct with JavaScript off and the
            # button's pressed state and the visible panels always agree.
            hidden = "" if mode == modes[0] else " hidden"
            panels.append(
                f'<section class="panel{hidden}" data-mode="{html.escape(mode)}">'
                f'<h3>{html.escape(family)}'
                f'<span class="modehead">/ {html.escape(mode)}</span>'
                f'<span class="dirhead">'
                f'{html.escape(metric["better"])} is better</span></h3>'
                f'<p class="cap">{caption}</p>'
                f"{_svg('/'.join(path), group, engines, here, metric)}</section>"
            )

    legend = "".join(
        f'<span class="swkey s{i}"></span><span class="skl">{html.escape(e)}</span>'
        for i, e in enumerate(engines)
    )
    replacements = {
        "@LIGHT_VARS@": _series_vars(SERIES_LIGHT),
        "@DARK_VARS@": _series_vars(SERIES_DARK),
        "@SERIES_RULES@": "\n".join(
            f".s{i} {{ fill: var(--s{i}); background: var(--s{i}); }}"
            for i in range(len(SERIES_LIGHT))
        ),
        "@@LEGEND@@": legend,
        # **The mode buttons are generated from the payload, not written here.** The
        # two that used to be spelled out above would have survived a mode being
        # *added* by simply not existing, and a report whose controls list two of its
        # three modes is a report a reader scrolls past without noticing what is
        # missing — the third mode's panels would be in the markup and reachable only
        # by the JS below, whose button set is this list. The first button is the
        # pressed one and the first panel is the visible one, which is the same
        # choice `hidden` makes further down.
        "@@MODEBUTTONS@@": "".join(
            f'<button class="mode" data-mode="{html.escape(m)}" '
            f'aria-pressed="{"true" if i == 0 else "false"}">'
            f'{html.escape(payload["metrics"][m]["button"])}</button>'
            for i, m in enumerate(modes)
        ),
        "@@MODENOTE@@": "; ".join(
            f'<b>{html.escape(m)}</b> &mdash; {html.escape(payload["metrics"][m]["what"])}'
            for m in modes
        ),
        "@@HEROES@@": _heroes(grouped, modes, shared, payload["metrics"], engines),
        "@@PANELS@@": "".join(panels),
        "@@TABLE@@": _table(
            grouped, engines, payload["families"], payload["metrics"],
            payload.get("floor", {}),
        ),
        "@@DISAGREEMENTS@@": _disagreements(payload),
        "@@ENVIRONMENT@@": _environment(payload),
        "@@ENGINES@@": html.escape(", ".join(engines)),
        "@@COMMAND@@": html.escape(payload.get("command", "")),
    }
    page = _PAGE
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    # **A placeholder with no replacement is invisible in a browser and is not
    # invisible here.** The palette and the `--sN` declarations used to be spelled
    # out by hand in the template, so shrinking the palette from four hues to three
    # left a literal `@light3@` in the CSS — a value no browser reports and no diff
    # of the *generated* file makes obvious, since the generated file is not the one
    # anyone reads. Both are generated from the palette now, and this refuses
    # anything left over rather than shipping it.
    leftover = sorted(set(re.findall(r"@[@A-Za-z0-9_]*@", page)))
    if leftover:
        raise AssertionError(
            f"the report template still contains {leftover} — a placeholder whose "
            f"replacement list shrank renders as literal text on the page"
        )
    return page


def _series_vars(values: tuple) -> str:
    """The `--sN` custom-property declarations for one mode's palette."""
    return " ".join(f"--s{i}: {v};" for i, v in enumerate(values))


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Template engine benchmark</title>
<style>
:root {
  color-scheme: light;
  --surface-0: #f4f4f1; --surface-1: #fcfcfb; --surface-2: #ededea;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #82807a;
  --border: #dedcd5; --grid: #e7e5de;
  --status-good: #1a7f4b; --status-bad: #b3261e;
  @LIGHT_VARS@
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-0: #111110; --surface-1: #1a1a19; --surface-2: #262625;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #8f8e85;
    --border: #34342f; --grid: #2d2d2a;
    --status-good: #4fbf82; --status-bad: #f2857c;
    @DARK_VARS@
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0: #111110; --surface-1: #1a1a19; --surface-2: #262625;
  --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #8f8e85;
  --border: #34342f; --grid: #2d2d2a;
  --status-good: #4fbf82; --status-bad: #f2857c;
  @DARK_VARS@
}
* { box-sizing: border-box; }
body { margin: 0; padding: 26px 18px 72px; background: var(--surface-0);
  color: var(--text-primary); font: 15px/1.55 ui-sans-serif, -apple-system,
  "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 1260px; margin: 0 auto; }
h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: -0.015em; }
h2 { font-size: 18px; margin: 38px 0 8px; }
h3 { font-size: 14px; margin: 0 0 3px; font-weight: 650; }
.modehead { color: var(--text-muted); font-weight: 500; margin-left: 6px; }
header p { color: var(--text-secondary); margin: 0; max-width: 78ch; }
.sub, .cap, .tn { color: var(--text-secondary); font-size: 12.5px; }
.cap { margin: 0 0 12px; }
/* Direction badges: every chart, tile and table block names which way "better"
   runs. They are a word rather than a colour or an arrow, so they survive a
   colourblind reader, a printout and forced colors — and they have to be on each
   one, because the three hero tiles do not agree with each other: the ratio tiles
   and the byte-agreement tile point opposite ways.
   `.tok` is the same pill for a token being named inside a sentence (the `N.NN×`
   ratio notation) — it is *not* a direction, so it does not wear `.dir`. */
.dir, .dirhead, .tok { display: inline-block; color: var(--text-secondary);
  font-size: 11px; font-weight: 500; font-variant-numeric: normal;
  white-space: nowrap; border: 1px solid var(--border);
  background: var(--surface-2); border-radius: 999px; padding: 0 8px; }
.modehead + .dirhead { margin-left: 8px; }
.tbl th .dir { margin-top: 2px; }
/* On the table's family banner the badge sits on `--surface-2` already, so it
   takes the panel colour instead and stays visible as a pill. */
.tbl tr.fam .dirhead { background: var(--surface-1); }
.legend-inline { margin-left: auto; color: var(--text-secondary); font-size: 12.5px; }
.swkey { display: inline-block; width: 10px; height: 10px; border-radius: 2.5px;
  margin: 0 5px 0 10px; }
.skl { margin-right: 2px; }
.tiles { display: grid; gap: 12px; margin: 22px 0 4px;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
.tile { background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px; }
.tv { font-size: 29px; font-weight: 620; letter-spacing: -0.02em; line-height: 1.15; }
.tl { font-size: 13px; margin-top: 3px; }
.tn { font-size: 11.5px; margin-top: 6px; }
.controls { display: flex; gap: 8px; align-items: center; margin: 26px 0 4px;
  flex-wrap: wrap; }
.controls .lbl { font-size: 12.5px; color: var(--text-secondary); margin-right: 2px; }
button.mode { font: inherit; font-size: 13px; padding: 5px 14px; border-radius: 999px;
  border: 1px solid var(--border); background: var(--surface-1);
  color: var(--text-primary); cursor: pointer; }
button.mode[aria-pressed="true"] { background: var(--text-primary);
  color: var(--surface-1); border-color: var(--text-primary); }
@SERIES_RULES@
.panel { background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 15px 18px 6px; margin: 14px 0; overflow-x: auto; }
svg.chart { width: 100%; min-width: 1160px; height: auto; display: block; }
.grid { stroke: var(--grid); stroke-width: 1; }
text { fill: var(--text-secondary); font-size: 12px; font-family: inherit; }
text.lg { font-size: 12.5px; }
text.lg.muted, text.tick { fill: var(--text-muted); font-size: 11.5px; }
text.wl { fill: var(--text-primary); font-size: 12.5px;
  font-variant-numeric: tabular-nums; }
text.val { fill: var(--text-primary); font-size: 12px;
  font-variant-numeric: tabular-nums; }
text.absent { fill: var(--text-muted); font-size: 11.5px; font-style: italic; }
.bar { stroke: var(--surface-1); stroke-width: 1.5; cursor: default; }
.bar:hover { opacity: 0.75; }
table.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px;
  background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
  overflow: hidden; }
.tbl th, .tbl td { text-align: left; padding: 7px 10px;
  border-bottom: 1px solid var(--border); vertical-align: top; }
.tbl th { font-weight: 620; background: var(--surface-2); }
.tbl tr.fam td { background: var(--surface-2); font-weight: 620; font-size: 12px; }
.tbl td.wk { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px; white-space: nowrap; }
.tbl .r, .tbl .sub { color: var(--text-secondary); }
.tbl .na { color: var(--text-muted); }
.ok, .ok-note { color: var(--status-good); }
.no { color: var(--status-bad); }
.ok-note { background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 16px; font-size: 13.5px; }
ul.caveat { background: var(--surface-1); border: 1px solid var(--border);
  border-left: 3px solid var(--status-bad); border-radius: 8px;
  padding: 10px 16px 10px 30px; font-size: 13px; }
dl.env { display: grid; grid-template-columns: max-content 1fr; gap: 5px 20px;
  font-size: 12.5px; margin: 0 0 14px; }
dl.env dt { color: var(--text-secondary); }
dl.env dd { margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px; word-break: break-word; }
pre.cmd { background: var(--surface-2); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 14px; font-size: 11.5px; overflow-x: auto;
  margin: 12px 0 0; }
.hidden { display: none; }
#tip { position: fixed; pointer-events: none; background: var(--surface-1);
  color: var(--text-primary); border: 1px solid var(--border); border-radius: 8px;
  padding: 8px 11px; font-size: 12px; line-height: 1.5;
  box-shadow: 0 6px 22px rgba(0,0,0,0.20); opacity: 0;
  transition: opacity 90ms; z-index: 9; max-width: 340px; }
@media (prefers-reduced-motion: reduce) { #tip { transition: none; } }
</style></head><body><main>
<header>
  <h1>Template engine benchmark</h1>
  <p>The same templates rendered by three engines: stock Django&rsquo;s DTL, this
  package&rsquo;s Rust engine, and Jinja2. Engines in this run:
  <b>@@ENGINES@@</b>. Rows marked <span class="ok">&equiv;</span> produced
  byte-identical output; every row hashes what it rendered, and a disagreement is
  printed rather than averaged into a timing.</p>
</header>

<div class="tiles">@@HEROES@@</div>

<div class="controls">
  <span class="lbl">show</span>
@@MODEBUTTONS@@
  <span class="legend-inline">@@LEGEND@@</span>
</div>

@@PANELS@@

<h2>Every measurement</h2>
<p class="cap">&ldquo;spread&rdquo; is worst/best across a row&rsquo;s timed samples: a
spread well above 1.00 means a noisy machine and a figure worth re-running. &equiv;
means the bytes matched the <code>django</code> control, &ne; that they did not, and
&mdash; that the engine cannot render the row at all. &ldquo;call&rdquo; is that
engine&rsquo;s floor &mdash; the cost of a template that renders nothing, printed
above &mdash; as a share of the cell, capped at 100% because the two are separate
measurements: a row near <b>call 100%</b> is a measurement of the call, and a row
near <b>call 2%</b> is a measurement of the construct. <b>Read it down a column,
not across a row</b>: each cell divides by <i>its own engine&rsquo;s</i> floor, and
the floors are not equal &mdash; Jinja2&rsquo;s is about twice stock Django&rsquo;s
&mdash; so two shares in one row have different numerators and are not comparable.
Down a column the floor is fixed and the share answers one question: how much of
<i>this</i> engine&rsquo;s time on this row is the call.</p>
@@TABLE@@

<h2>Where the engines do not agree</h2>
@@DISAGREEMENTS@@

<h2>How this was measured</h2>
<dl class="env">@@ENVIRONMENT@@</dl>
<p class="cap">Modes: @@MODENOTE@@ &mdash; and every one of them goes through
<code>from_string</code> on all three engines, so that none of them is served
from a cache the others do not have.
<b>compile</b> is not <code>parse &minus; render</code>: the two terms of that
subtraction are a compile of the current iteration&rsquo;s template against a
<i>warm</i> handle that has already paid its lazy work, so it carries
<code>compile + (cold &minus; warm)</code> and the second term is not small. One
asymmetry is inside these numbers and is named here rather than left for a
reader to find: the Django-side engines are handed a <code>Context</code> built
once per row rather than once per call, which is what the milestone table this
supersedes measured &mdash; a view that builds one per request pays about the same
again on both of them.</p>
<pre class="cmd">@@COMMAND@@</pre>
<div id="tip" role="tooltip"></div>
<script>
(function () {
  var tip = document.getElementById('tip');
  function move(e) {
    tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 350) + 'px';
    tip.style.top = Math.min(e.clientY + 14, window.innerHeight - 120) + 'px';
  }
  Array.prototype.forEach.call(document.querySelectorAll('.bar'), function (bar) {
    bar.addEventListener('mouseenter', function (e) {
      var d = bar.dataset;
      var ratio = d.ratio
        ? 'vs stock Django: <b>' + d.ratio + 'x</b>'
        : '(this is the control column)';
      tip.innerHTML = '<b>' + d.engine + '</b> · ' + d.workload +
        '<br>' + parseFloat(d.us).toFixed(2) + ' µs per call, ' + ratio +
        '<br><span style="opacity:.72">spread ' + (d.worst / d.us).toFixed(2) +
        'x · ' + d.iters + ' iters per sample · ' + d.bytes +
        ' bytes · sha256 ' + d.sha + '…</span>';
      tip.style.opacity = 1;
      move(e);
    });
    bar.addEventListener('mousemove', move);
    bar.addEventListener('mouseleave', function () { tip.style.opacity = 0; });
  });
  var buttons = Array.prototype.slice.call(
    document.querySelectorAll('.controls button.mode')
  );
  buttons.forEach(function (button) {
    button.addEventListener('click', function () {
      buttons.forEach(function (other) {
        other.setAttribute('aria-pressed', String(other === button));
      });
      Array.prototype.forEach.call(
        document.querySelectorAll('.panel'), function (panel) {
          panel.classList.toggle('hidden', panel.dataset.mode !== button.dataset.mode);
        }
      );
    });
  });
})();
</script>
</main></body></html>
"""
