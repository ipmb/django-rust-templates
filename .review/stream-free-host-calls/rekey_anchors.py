#!/usr/bin/env python3
"""Re-point `crates/**` anchors whose line moved, in the manifest *and* the prose.

`scripts/check_python_anchors.py --update` moves an entry's span columns but
never the key in column 1, and never the citation in the prose that names that
key — so the two halves have to be changed together or the gate trades one
failure for another. Doing it by hand three times in one change is how one of
them ends up wrong, which is the whole thing the anchor gate exists to prevent.

Only entries carrying a snippet that occurs on **exactly one** line are moved;
anything ambiguous is left alone and reported, because guessing is worse than a
failing gate.

    source scripts/env.sh && python .review/stream-free-host-calls/rekey_anchors.py
"""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "scripts" / "anchors.txt"
SHORTHAND = "crates/template-core/src/"


def main():
    rows = [r.split("\t") for r in MANIFEST.read_text().splitlines()]
    renames, ambiguous = {}, []

    for cols in rows:
        key = cols[0]
        if not key.startswith("crates/"):
            continue
        path, snippet = cols[1], cols[4]
        if snippet == "-":
            continue
        lines = (ROOT / path).read_text().splitlines()
        hits = [i + 1 for i, line in enumerate(lines) if snippet in line]
        if len(hits) != 1:
            ambiguous.append((key, len(hits)))
            continue
        hit = hits[0]
        tail = key.rsplit(":", 1)[-1]
        if "-" in tail:
            first, last = (int(x) for x in tail.split("-"))
            new_key = f"{path}:{hit}-{hit + (last - first)}"
            cols[2], cols[3] = str(hit), str(hit + (last - first))
        else:
            new_key = f"{path}:{hit}"
            cols[2], cols[3] = str(hit), "-"
        if new_key != key:
            renames[key] = new_key
            cols[0] = new_key

    MANIFEST.write_text("\n".join("\t".join(c) for c in rows) + "\n")

    if renames:
        files = subprocess.run(
            ["git", "grep", "-l", SHORTHAND], cwd=ROOT,
            capture_output=True, text=True,
        ).stdout.split()
        for name in files:
            if name.endswith("anchors.txt"):
                continue
            p = ROOT / name
            text = before = p.read_text()
            for old, new in renames.items():
                text = text.replace(old, new)
                # Prose also uses the `parser/mod.rs:N` shorthand form.
                text = text.replace(old.replace(SHORTHAND, ""),
                                    new.replace(SHORTHAND, ""))
            if text != before:
                p.write_text(text)
                print(f"  prose: {name}")

    for old, new in renames.items():
        print(f"  moved: {old} -> {new}")
    for key, count in ambiguous:
        print(f"  ambiguous ({count} matches), left alone: {key}", file=sys.stderr)
    print(f"{len(renames)} re-keyed, {len(ambiguous)} ambiguous")


if __name__ == "__main__":
    main()
