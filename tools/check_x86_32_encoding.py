#!/usr/bin/env python3
"""4.17: byte-check the x86-32 encoder against a real disassembler.

src/jit/x86_32_encoder_test.cpp emits every encoder form and prints the instruction
it means to encode. This disassembles the bytes with i686 objdump and requires each
decoded instruction to equal the intended one, so a wrong opcode, ModRM byte,
operand order or relative displacement fails here rather than in generated code.
A relative jump is checked by its decoded target. Skipped when the i686 binutils
are not installed (--require makes that an error).
"""
import argparse, pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--require", action="store_true")
    args = ap.parse_args()
    objdump = shutil.which("i686-w64-mingw32-objdump") or shutil.which("objdump")
    if objdump is None:
        print("ERROR: objdump not found" if args.require else "SKIP: objdump not found")
        return 1 if args.require else 0
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        exe = d / "enc"
        r = subprocess.run(["g++", "-std=c++20", "-Wall", "-Wextra", f"-I{ROOT/'src'/'jit'}", "-o", str(exe),
                            str(ROOT / "src/jit/x86_32_encoder_test.cpp")], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr)
            return 1
        out = subprocess.run([str(exe), str(d / "code.bin")], capture_output=True, text=True)
        want = out.stdout.splitlines()
        dis = subprocess.run([objdump, "-D", "-b", "binary", "-mi386", "-Mintel", str(d / "code.bin")],
                             capture_output=True, text=True).stdout
    got = []
    for line in dis.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\t(?:[0-9a-f]{2} )+\s*\t?(.*)$", line)
        if not m or not m.group(2).strip():
            continue   # objdump wraps long instructions onto bytes-only lines
        got.append((int(m.group(1), 16), re.sub(r"\s+", " ", m.group(2).strip())))
    fails = 0
    if len(got) != len(want):
        print(f"FAIL: {len(want)} instructions expected, {len(got)} decoded")
        return 1
    for (addr, text), exp in zip(got, want):
        rel = re.match(r"(j\w+|call) <rel\+0x([0-9a-f]+)>", exp)
        if rel:
            # the target is the end of this instruction + the stated offset
            m = re.match(r"(j\w+|call) 0x([0-9a-f]+)", text)
            length = 5 if rel.group(1) in ("jmp", "call") else 6
            ok = bool(m) and m.group(1) == rel.group(1) and int(m.group(2), 16) == addr + length + int(rel.group(2), 16)
        else:
            ok = text == exp
        if not ok:
            fails += 1
            print(f"FAIL at {addr:#x}: decoded {text!r}, intended {exp!r}")
    print(f"x86-32 encoder: {len(want) - fails}/{len(want)} instructions decode as intended")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
