#!/usr/bin/env python3
"""Writes the byte table into `std/http/inflate.lio`.

`inflate_body` turns decoded bytes into `str` elements with a lookup table:
`out[n] = tbl[v]`. A `str` element cannot take an int[64] value directly, so
the table is 256 constant stores, one per byte value: `tbl[k] = k` for k below
128 and `tbl[k] = k - 256` above (the signed int8 a str element holds). The
element `tbl[0]` stays NUL from the zero-initialised string, so it is skipped.

The `#<<TBL>>` / `#<</TBL>>` markers stay in the file, so the script is
idempotent.

    python3 tools/gen_inflate_table.py     # rewrite std/http/inflate.lio
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "std" / "http" / "inflate.lio"
TAG = "TBL"


def table(indent: int) -> list[str]:
    pad = " " * indent
    out = []
    for k in range(1, 256):
        v = k if k < 128 else k - 256
        out.append(f"{pad}tbl[{k}] = {v}")
    return out


def fill(text: str, body: list[str]) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    seen = False
    start = f"#<<{TAG}>>"
    end = f"#<</{TAG}>>"
    while i < len(lines):
        line = lines[i]
        if line.strip() == start:
            if seen:
                raise SystemExit(f"{TARGET}: two #{TAG} markers")
            seen = True
            out.append(line)
            i += 1
            while i < len(lines) and lines[i].strip() != end:
                i += 1
            if i >= len(lines):
                raise SystemExit(f"{TARGET}: no {end} marker")
            out.extend(body)
            out.append(lines[i])
            i += 1
            continue
        out.append(line)
        i += 1
    if not seen:
        raise SystemExit(f"{TARGET}: no #{TAG} marker")
    return "\n".join(out) + "\n"


def main() -> int:
    text = TARGET.read_text()
    TARGET.write_text(fill(text, table(4)))
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
