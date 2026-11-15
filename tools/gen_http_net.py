#!/usr/bin/env python3
"""Writes the mechanical copy blocks into `std/http/io.lio`.

The transport moves bytes between a bounded string and the 64-byte `Chunk`
struct the kernel is pointed at. A struct field cannot be indexed by a
computed number, so each block is one `if` per byte -- 64 of them, twice.
That is exactly the kind of repetition a generator should own: this script
fills the two `#<<COPY_IN>>` / `#<<COPY_OUT>>` markers in place, and running
it again after an edit around them keeps the blocks honest.

    python3 tools/gen_http_net.py          # rewrite std/http/io.lio

The markers stay in the file, so the script is idempotent and the result can
still be read as ordinary Lithon source.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "std" / "http" / "io.lio"

CHUNK = 64


def copy_in(indent: int) -> list[str]:
    """Request bytes -> chunk fields.

    Only the bytes that are really there are copied: `off + k < nreq` keeps
    the read inside the request and leaves the rest of the window alone (the
    send only ever hands the kernel the bytes it asked for).
    """
    pad = " " * indent
    out = []
    for k in range(CHUNK):
        out.append(f"{pad}if off + {k} < nreq:")
        out.append(f"{pad}    ch.f{k} = req[off + {k}]")
    return out


def copy_out(indent: int) -> list[str]:
    """Chunk fields -> response bytes.

    All 64 fields are copied, whatever the kernel actually returned; the
    caller only reads the `n` bytes it was given, and the byte at the new
    end is zeroed afterwards so the response stays a clean NUL-terminated
    run.
    """
    pad = " " * indent
    out = []
    for k in range(CHUNK):
        out.append(f"{pad}if cpy == {k}:")
        out.append(f"{pad}    resp[total + {k}] = ch.f{k}")
    return out


def fill(text: str, tag: str, body: list[str]) -> str:
    """Replaces the lines between `#<<TAG>>` and `#<</TAG>>` with `body`."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    seen = False
    while i < len(lines):
        line = lines[i]
        if line.strip() == f"#<<{tag}>>":
            if seen:
                raise SystemExit(f"{TARGET}: two #{tag} markers")
            seen = True
            out.append(line)
            i += 1
            end = f"#<</{tag}>>"
            while i < len(lines) and lines[i].strip() != end:
                i += 1
            if i >= len(lines):
                raise SystemExit(f"{TARGET}: no {end} marker")
            out.extend(body)
            out.append(lines[i])   # the closing marker
            i += 1
            continue
        out.append(line)
        i += 1
    if not seen:
        raise SystemExit(f"{TARGET}: no #{tag} marker")
    return "\n".join(out) + "\n"


def main() -> int:
    text = TARGET.read_text()
    # The copy-in sits directly in the `while off < nreq:` body (8 spaces);
    # the copy-out sits one level deeper, inside `while cpy < 64:` (12).
    text = fill(text, "COPY_IN", copy_in(8))
    text = fill(text, "COPY_OUT", copy_out(12))
    TARGET.write_text(text)
    print(f"wrote {TARGET.relative_to(ROOT)}: {CHUNK}-byte copy blocks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
