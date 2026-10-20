#!/usr/bin/env python3
"""Prove the 4.7 enum layout is `[tag][payload]` with no padding.

An enum is a raw byte blob: the frontend bakes the tag width into the
declaration (`store e : struct[N]`) and every payload offset into the
fieldload/fieldstore instructions. Nothing in the C++ backend knows a variant,
so the frontend's arithmetic *is* the layout.

This tool closes the loop against independent oracles:

  1. compile an equivalent `#pragma pack(1)` C struct (an int8/int16 tag
     followed by a union of the payloads) and read the real `sizeof`/`offsetof`;
  2. run the real frontend on a canonical enum program and read the
     `struct[N]` size and payload offsets it emits;
  3. require the two, and a model computed straight from the variant list, to
     agree.

The tag-width rule is checked at both boundaries: an int[8] tag while the enum
has at most 128 variants (0..127 is the positive range of a signed byte) and an
int[16] tag past it.

Run: python3 tools/check_enum_layout.py
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = REPO / "src" / "frontend" / "frontend.py"

SCALAR = {
    ("int", 8): 1,
    ("int", 16): 2,
    ("int", 32): 4,
    ("int", 64): 8,
    ("float", 64): 8,
    ("bool", -1): 1,
}
C_TYPE = {
    ("int", 8): "int8_t",
    ("int", 16): "int16_t",
    ("int", 32): "int32_t",
    ("int", 64): "int64_t",
    ("float", 64): "double",
    ("bool", -1): "uint8_t",
}

# (variant name, payload). payload is None (unit), ("int"|"float", width),
# ("bool",), or ("struct", [(field, width), ...]).
PACKET = [
    ("Empty", None),
    ("Ack", ("int", 32)),
    ("Data", ("struct", [("seq", 16), ("len", 32)])),
    ("Flag", ("bool",)),
    ("Wide", ("int", 64)),
]

BIG_N = 129  # one past the signed-byte tag range

def payload_bytes(payload) -> int:
    if payload is None:
        return 0
    if payload[0] == "struct":
        return sum(width // 8 for _f, width in payload[1])
    if payload[0] == "bool":
        return 1
    return payload[1] // 8


def model(variants):
    """Returns (tag_width, payload_offset, size, {leaf: offset-in-tag-space})."""
    tag_width = 8 if len(variants) <= 128 else 16
    payload_offset = tag_width // 8
    max_payload = max(payload_bytes(p) for _n, p in variants)
    leaves = {}
    for vname, payload in variants:
        if payload is None:
            continue
        if payload[0] == "struct":
            off = payload_offset
            for fname, width in payload[1]:
                leaves[f"{vname}.{fname}"] = off
                off += width // 8
        else:
            leaves[vname] = payload_offset
    return tag_width, payload_offset, payload_offset + max_payload, leaves


def program() -> str:
    lines = []
    lines.append("class Packet(Enum):")
    lines.append("    Empty")
    lines.append("    Ack: int[32]")
    lines.append("    class Data(Struct):")
    lines.append("        seq: int[16]")
    lines.append("        len: int[32]")
    lines.append("    Flag: bool")
    lines.append("    Wide: int[64]")
    lines.append("")
    # Construct every variant so the frontend names every payload offset.
    lines.append("p: Packet")
    lines.append("p = Packet.Empty")
    lines.append("p = Packet.Ack(1)")
    lines.append("p = Packet.Data(seq=2, len=3)")
    lines.append("p = Packet.Flag(True)")
    lines.append("p = Packet.Wide(4)")
    match = "match p:\n"
    match += "    case Packet.Ack(a):\n        print(a)\n"
    match += "    case Packet.Data(seq=s, len=l):\n        print(s)\n        print(l)\n"
    match += "    case Packet.Flag(f):\n        print(f)\n"
    match += "    case Packet.Wide(w):\n        print(w)\n"
    match += "    case _:\n        print(0)\n"
    lines.append(match.rstrip("\n"))
    lines.append("")
    # A 129-variant enum: the tag must widen to int[16].
    lines.append("class Big(Enum):")
    for i in range(BIG_N):
        lines.append(f"    V{i}")
    lines.append("")
    lines.append("b: Big")
    lines.append(f"b = Big.V{BIG_N - 1}")
    lines.append("match b:")
    lines.append(f"    case Big.V{BIG_N - 1}:")
    lines.append("        print(1)")
    lines.append("    case _:")
    lines.append("        print(0)")
    return "\n".join(lines) + "\n"


def c_source():
    out = ["#include <stddef.h>", "#include <stdint.h>", "#include <stdio.h>",
           "#pragma pack(push,1)"]
    out.append("typedef struct {")
    out.append("    int8_t tag;")
    out.append("    union {")
    for vname, payload in PACKET:
        if payload is None:
            continue
        if payload[0] == "struct":
            out.append("        struct {")
            for fname, width in payload[1]:
                out.append(f"            {C_TYPE[('int', width)]} {fname};")
            out.append(f"        }} {vname.lower()};")
        else:
            key = ("bool", -1) if payload[0] == "bool" else (payload[0], payload[1])
            out.append(f"        {C_TYPE[key]} {vname.lower()};")
    out.append("    } u;")
    out.append("} Packet;")
    out.append("#pragma pack(pop)")
    out.append("int main(void) {")
    # Packet size and the payload offsets in the same "tag space" the model uses.
    exprs = ["offsetof(Packet, u)"]
    names = ["u"]
    for vname, payload in PACKET:
        if payload is None:
            continue
        if payload[0] == "struct":
            for fname, _w in payload[1]:
                names.append(f"{vname}.{fname}")
                exprs.append(f"offsetof(Packet, u.{vname.lower()}.{fname})")
        else:
            names.append(vname)
            exprs.append(f"offsetof(Packet, u.{vname.lower()})")
    fmt = " ".join(["%zu"] * (len(exprs) + 1))
    out.append(f'    printf("Packet {fmt}\\n", sizeof(Packet), {", ".join(exprs)});')
    out.append("    return 0;")
    out.append("}")
    return "\n".join(out) + "\n"


def run_c(tmp: pathlib.Path):
    c = tmp / "enum_layout.c"
    exe = tmp / "enum_layout"
    c.write_text(c_source())
    subprocess.run(["cc", "-O0", "-o", str(exe), str(c)], check=True)
    out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
    line = out.splitlines()[0].split()
    return int(line[1]), line[2:]


def run_frontend(tmp: pathlib.Path):
    src = tmp / "enum.lio"
    src.write_text(program())
    proc = subprocess.run([sys.executable, str(FRONTEND), str(src)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"frontend refused the canonical enum program:\n{proc.stderr}")
    declared = {}
    offsets = {}
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
    return declared, offsets


def main() -> int:
    failures: list[str] = []
    tag_w, pay_off, size, leaves = model(PACKET)

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        c_size, c_offs = run_c(tmp)
        declared, offsets = run_frontend(tmp)

    # 1. tag width / size / offsets against the model.
    if tag_w != 8:
        failures.append(f"Packet tag width model {tag_w} != 8")
    if size != declared.get("p"):
        failures.append(f"Packet frontend struct[{declared.get('p')}] != model size {size}")
    if size != c_size:
        failures.append(f"Packet model size {size} != C sizeof {c_size}")
    frontend_offs = offsets.get("p", set())
    model_offs = {0} | set(leaves.values())
    if frontend_offs != model_offs:
        failures.append(f"Packet frontend offsets {sorted(frontend_offs)} != model {sorted(model_offs)}")
    c_off_set = {0} | {int(x) for x in c_offs}
    if c_off_set != model_offs:
        failures.append(f"Packet C offsets {sorted(c_off_set)} != model {sorted(model_offs)}")
    if not failures:
        print(f"  ok   Packet: tag={tag_w} bit, payload@{pay_off}, sizeof={size}, "
              f"offsets={sorted(model_offs)} (frontend == model == C packed)")

    # 2. the tag-width boundary: 129 variants must widen to int[16].
    big_n = BIG_N
    big_tag = 8 if big_n <= 128 else 16
    big_off = big_tag // 8
    big_size = big_off  # all variants are unit
    if big_tag != 16:
        failures.append(f"Big tag model {big_tag} != 16")
    if declared.get("b") != big_size:
        failures.append(f"Big frontend struct[{declared.get('b')}] != model size {big_size}")
    if offsets.get("b", set()) != {0}:
        failures.append(f"Big frontend offsets {sorted(offsets.get('b', set()))} != [0]")
    if not failures:
        print(f"  ok   Big({big_n}): tag={big_tag} bit, payload@{big_off}, sizeof={big_size}")

    if failures:
        print("\nenum layout does NOT match the model / C packed layout:")
        for f in failures:
            print(f"  FAIL {f}")
        return 1
    print("\nall enum layouts match the model and the C packed layout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
