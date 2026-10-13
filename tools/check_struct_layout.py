#!/usr/bin/env python3
"""Prove the 4.6 struct layout is byte-for-byte the C packed layout.

A Lithon struct is a raw byte blob: the frontend bakes each field's byte
offset into the IR (`fieldload s, <off>`, `fieldstore s, <off>, v`) and the
total size into the declaration (`store s : struct[N]`). Nothing in the C++
backend knows the field names, so the frontend's arithmetic *is* the layout.

This tool closes that loop against an independent oracle:

  1. compile an equivalent `#pragma pack(1)` C struct with `cc` and read the
     real `sizeof` / `offsetof` values it prints;
  2. run the real frontend on a canonical struct program and read the
     `struct[N]` size and field offsets it emits;
  3. require the two to agree, field by field.

If the frontend ever inserts padding, rounds a size, or drifts a field
offset, the C compiler and the frontend disagree here and this fails.

Run: python3 tools/check_struct_layout.py
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = REPO / "src" / "frontend" / "frontend.py"

# The canonical program. Every leaf field of every struct is written, so the
# IR must name every leaf offset.
PROGRAM = """\
class Point(Struct):
    x: int[64]
    y: int[64]


class SockAddrIn(Struct):
    family: int[16]
    port: int[16]
    addr: int[64]
    active: bool


class Segment(Struct):
    a: Point
    b: Point
    tag: int[8]


p: Point
p.x = 1
p.y = 2

s: SockAddrIn
s.family = 2
s.port = 80
s.addr = 16909060
s.active = True

seg: Segment
seg.a.x = 10
seg.a.y = 20
seg.b.x = 30
seg.b.y = 40
seg.tag = 7

print(p.x)
print(s.addr)
print(seg.b.y)
"""

SCALAR = {
    "int[8]": 1,
    "int[16]": 2,
    "int[32]": 4,
    "int[64]": 8,
    "float[64]": 8,
    "bool": 1,
}
C_TYPE = {
    "int[8]": "int8_t",
    "int[16]": "int16_t",
    "int[32]": "int32_t",
    "int[64]": "int64_t",
    "float[64]": "double",
    "bool": "uint8_t",
}

# (struct name, [(field name, type)]) in declaration order; a type naming an
# earlier struct in this list is a nested struct field.
STRUCTS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Point", [("x", "int[64]"), ("y", "int[64]")]),
    (
        "SockAddrIn",
        [
            ("family", "int[16]"),
            ("port", "int[16]"),
            ("addr", "int[64]"),
            ("active", "bool"),
        ],
    ),
    ("Segment", [("a", "Point"), ("b", "Point"), ("tag", "int[8]")]),
]

# variable name -> struct name.
VARIABLES: list[tuple[str, str]] = [
    ("p", "Point"),
    ("s", "SockAddrIn"),
    ("seg", "Segment"),
]

# struct name -> [(leaf path, byte offset, C offset expression)]
LEAVES: dict[str, list[tuple[str, int, str]]] = {}
SIZES: dict[str, int] = {}


def build_expected() -> None:
    for name, fields in STRUCTS:
        offset = 0
        leaves: list[tuple[str, int, str]] = []
        for fname, ftype in fields:
            if ftype in SCALAR:
                leaves.append((fname, offset, f"offsetof({name}, {fname})"))
                offset += SCALAR[ftype]
            else:
                for leaf, loff, lexpr in LEAVES[ftype]:
                    leaves.append(
                        (
                            f"{fname}.{leaf}",
                            offset + loff,
                            f"offsetof({name}, {fname}) + {lexpr}",
                        )
                    )
                offset += SIZES[ftype]
        LEAVES[name] = leaves
        SIZES[name] = offset


def c_source() -> str:
    out = ["#include <stddef.h>", "#include <stdint.h>", "#include <stdio.h>", "#pragma pack(push,1)"]
    for name, fields in STRUCTS:
        out.append("typedef struct {")
        for fname, ftype in fields:
            ctype = C_TYPE[ftype] if ftype in C_TYPE else ftype
            out.append(f"    {ctype} {fname};")
        out.append(f"}} {name};")
    out.append("#pragma pack(pop)")
    out.append("int main(void) {")
    for name, _ in STRUCTS:
        exprs = ", ".join(expr for _, _, expr in LEAVES[name])
        fmt = " ".join(["%zu"] * (len(LEAVES[name]) + 1))
        out.append(f'    printf("{name} {fmt}\\n", sizeof({name}), {exprs});')
    out.append("    return 0;")
    out.append("}")
    return "\n".join(out) + "\n"


def run_c_layout(tmp: pathlib.Path) -> dict[str, tuple[int, list[int]]]:
    c = tmp / "layout.c"
    exe = tmp / "layout"
    c.write_text(c_source())
    subprocess.run(["cc", "-O0", "-o", str(exe), str(c)], check=True)
    out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
    result: dict[str, tuple[int, list[int]]] = {}
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        result[parts[0]] = (int(parts[1]), [int(x) for x in parts[2:]])
    return result


def run_frontend(tmp: pathlib.Path) -> dict[str, tuple[int, list[int]]]:
    src = tmp / "prog.py"
    src.write_text(PROGRAM)
    proc = subprocess.run(
        [sys.executable, str(FRONTEND), str(src)], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise SystemExit(f"frontend refused the canonical program:\n{proc.stderr}")
    declared: dict[str, int] = {}
    offsets: dict[str, set[int]] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        m = re.match(r"^store\s+(\w+)\s*:\s*struct\[(\d+)\]$", line)
        if m:
            declared[m.group(1)] = int(m.group(2))
            offsets.setdefault(m.group(1), set())
            continue
        m = re.match(r"^(?:fieldload|fieldstore)\s+(\w+)\s*,\s*(-?\d+)", line)
        if m:
            offsets.setdefault(m.group(1), set()).add(int(m.group(2)))
    result: dict[str, tuple[int, list[int]]] = {}
    for var, struct_name in VARIABLES:
        if var not in declared:
            raise SystemExit(f"frontend never declared struct variable '{var}'")
        result[var] = (declared[var], sorted(offsets.get(var, set())))
    return result


def main() -> int:
    build_expected()
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        c_layout = run_c_layout(tmp)
        fe_layout = run_frontend(tmp)

    failures: list[str] = []
    for var, struct_name in VARIABLES:
        c_size, c_offs = c_layout[struct_name]
        fe_size, fe_offs = fe_layout[var]
        model_size = SIZES[struct_name]
        model_offs = sorted({o for _, o, _ in LEAVES[struct_name]})
        local: list[str] = []
        if c_size != model_size:
            local.append(f"{struct_name}: C sizeof {c_size} != model {model_size}")
        if fe_size != c_size:
            local.append(
                f"{struct_name}: frontend struct[{fe_size}] != C sizeof {c_size}"
            )
        if fe_offs != c_offs:
            local.append(
                f"{struct_name}: frontend offsets {fe_offs} != C offsetof {c_offs}"
            )
        if sorted(c_offs) != model_offs:
            local.append(f"{struct_name}: C offsets {c_offs} != model {model_offs}")
        if local:
            failures.extend(local)
        else:
            print(f"  ok   {struct_name}: sizeof={c_size}, offsets={c_offs} (frontend == C packed)")

    if failures:
        print("\nstruct layout does NOT match the C packed layout:")
        for f in failures:
            print(f"  FAIL {f}")
        return 1
    print(f"\nall {len(VARIABLES)} struct layouts match the C packed layout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
