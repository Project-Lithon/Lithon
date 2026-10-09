#!/usr/bin/env python3
"""Uninitialized declarations (V1_SPEC 0.6.10).

`name: T` with no `= value` is a type-only declaration: no store, no zeroing,
no machine code. It is legal, the name starts UNASSIGNED, and it becomes
definitely assigned on the paths where a real assignment happened. This tool
exercises the full source pipeline for that rule and proves the codegen claim
the spec makes.

Three kinds of check:

  1. Reject cases -- programs that must be refused, by the frontend at source
     time or by the type checker on the lowered IR. Each pins a diagnostic
     fragment so a program cannot pass by being refused for the wrong reason.

  2. Accept cases -- programs that must run, run identically on the interpreter
     (`--interp`) and the native tier (`--auto`), and print the expected text.

  3. Codegen identity -- the machine code of `i: T; i = value; use(i)` must be
     byte-identical to that of `i: T = value; use(i)`. The two lower to the
     same IR (the declaration emits nothing), so this is exact. The dumps are
     compared under `setarch -R` (ASLR off, so absolute addresses in the JIT
     buffer are stable); where that is unavailable the comparison falls back to
     masking the bytes that are provably address-dependent, by first showing
     that the SAME input does not reproduce them.

    tools/check_uninit_decls.py
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "src" / "frontend" / "frontend.py"
TIER = ROOT / "build" / "tier_runner"
JIT = ROOT / "build" / "lithon_jit"

# (name, source, diagnostic fragment the refusal must mention)
REJECT_CASES = [
    ("read before the first assignment",
     "i: int[8]\nprint(i)\n",
     "not definitely assigned"),
    ("first assignment out of the declared range",
     "i: int[8]\ni = 300\nprint(i)\n",
     "0.6.5"),
    ("re-declared with a different type",
     "x: int[8] = 1\nx: int[64] = 2\n",
     "re-declared with a different type"),
    ("re-declared with a different type (ptr vs scalar)",
     "a: int[64] = 1\n_p: ptr[int[64]] = addressof(a)\n_p: int[64] = 2\n",
     "re-declared with a different type"),
    ("only one arm assigns",
     "i: int[8]\nif 1:\n    i = 5\nprint(i)\n",
     "not definitely assigned"),
    ("a loop-body assignment does not escape the loop",
     "i: int[8]\nk: int[8] = 0\nwhile k < 3:\n    i = k\n    k = k + 1\nprint(i)\n",
     "not definitely assigned"),
    ("addressof of an unassigned declared variable",
     "x: int[64]\n_p: ptr[int[64]] = addressof(x)\n",
     "not definitely assigned"),
    ("a bare pointer read before its addressof",
     "_p: ptr[int[64]]\nprint(valueof(_p))\n",
     "not definitely assigned"),
]

# (name, source, expected stdout)
ACCEPT_CASES = [
    ("both arms assign",
     "i: int[8]\nif 1:\n    i = 5\nelse:\n    i = 6\nprint(i)\n",
     "5\n"),
    ("assigned before a loop, reassigned in the body",
     "i: int[8]\ni = 0\nwhile i < 3:\n    i = i + 1\nprint(i)\n",
     "3\n"),
    ("declared then assigned, then printed",
     "i: int[8]\ni = 5\nprint(i)\n",
     "5\n"),
    ("bare pointer declared then assigned",
     "a: int[64] = 42\n_p: ptr[int[64]]\n_p = addressof(a)\nprint(valueof(_p))\n",
     "42\n"),
]

# (name, declaration-then-assignment source, initialized-in-place source)
IDENTITY_CASES = [
    ("scalar",
     "i: int[8]\ni = 5\nprint(i)\n",
     "i: int[8] = 5\nprint(i)\n"),
    ("counter reassigned in a loop",
     "i: int[64]\ni = 1\nwhile i < 3:\n    i = i + 1\nprint(i)\n",
     "i: int[64] = 1\nwhile i < 3:\n    i = i + 1\nprint(i)\n"),
    ("pointer",
     "a: int[64] = 7\n_p: ptr[int[64]]\n_p = addressof(a)\nprint(valueof(_p))\n",
     "a: int[64] = 7\n_p: ptr[int[64]] = addressof(a)\nprint(valueof(_p))\n"),
]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def frontend(source: str, path: pathlib.Path):
    path.write_text(source)
    return run(["python3", str(FRONTEND), str(path)])


def have_setarch() -> bool:
    try:
        return run(["setarch", "-R", "true"]).returncode == 0
    except FileNotFoundError:
        return False


SETARCH = have_setarch()


def dump_code(ir: pathlib.Path, out: pathlib.Path):
    cmd = ["setarch", "-R"] if SETARCH else []
    cmd += [str(JIT), str(ir), "--dump-code", str(out)]
    return run(cmd)


def check_reject(tmp: pathlib.Path, name, source, needle, failures):
    r = frontend(source, tmp / "src.py")
    place = "frontend"
    if r.returncode == 0:
        ir = tmp / "src.ir"
        ir.write_text(r.stdout)
        r = run([str(TIER), str(ir), "--interp"])
        place = "typechecker"
    refused = r.returncode != 0
    got = r.stderr + r.stdout
    if refused and needle in got:
        print(f"  ok   reject: {name}")
    else:
        why = "accepted" if not refused else f"refused without {needle!r}"
        print(f"  FAIL reject: {name}: {why} ({place})\n        {got.strip()[:240]}")
        failures.append(name)


def check_accept(tmp: pathlib.Path, name, source, expected, failures):
    r = frontend(source, tmp / "src.py")
    if r.returncode != 0:
        print(f"  FAIL accept: {name}: frontend refused\n        {r.stderr.strip()[:240]}")
        failures.append(name)
        return
    ir = tmp / "src.ir"
    ir.write_text(r.stdout)
    ref = run([str(TIER), str(ir), "--interp"])
    auto = run([str(TIER), str(ir), "--auto"])
    problems = []
    if ref.returncode != 0:
        problems.append(f"interpreter failed: {ref.stderr.strip()[:160]}")
    if auto.returncode != 0:
        problems.append(f"auto failed: {auto.stderr.strip()[:160]}")
    if ref.stdout != auto.stdout:
        problems.append(f"tier mismatch: interp {ref.stdout!r} vs auto {auto.stdout!r}")
    if ref.stdout != expected:
        problems.append(f"output {ref.stdout!r}, want {expected!r}")
    if problems:
        print(f"  FAIL accept: {name}:\n        " + "\n        ".join(problems))
        failures.append(name)
    else:
        print(f"  ok   accept: {name}  -> {ref.stdout.strip()!r}")


def check_identity(tmp: pathlib.Path, name, decl_src, init_src, failures):
    ra = frontend(decl_src, tmp / "decl.py")
    rb = frontend(init_src, tmp / "init.py")
    if ra.returncode != 0 or rb.returncode != 0:
        print(f"  FAIL identity: {name}: a form did not lower")
        failures.append(name)
        return
    ir_a = tmp / "decl.ir"
    ir_b = tmp / "init.ir"
    ir_a.write_text(ra.stdout)
    ir_b.write_text(rb.stdout)
    (tmp / "a.bin").unlink(missing_ok=True)
    (tmp / "b.bin").unlink(missing_ok=True)
    da = dump_code(ir_a, tmp / "a.bin")
    db = dump_code(ir_b, tmp / "b.bin")
    if da.returncode != 0 or db.returncode != 0:
        print(f"  FAIL identity: {name}: --dump-code failed\n        "
              f"{da.stderr.strip()[:160]} {db.stderr.strip()[:160]}")
        failures.append(name)
        return
    a = (tmp / "a.bin").read_bytes()
    b = (tmp / "b.bin").read_bytes()
    ir_same = ra.stdout == rb.stdout
    if SETARCH:
        ok = a == b
        note = "exact"
    else:
        # No ASLR-off: show which bytes are provably address-dependent by
        # dumping the declaration form a second time, then require every
        # difference between the two forms to fall in that set.
        (tmp / "a2.bin").unlink(missing_ok=True)
        dump_code(ir_a, tmp / "a2.bin")
        a2 = (tmp / "a2.bin").read_bytes()
        if len(a) != len(a2):
            ok = False
        else:
            unstable = {i for i in range(len(a)) if a[i] != a2[i]}
            diff = {i for i in range(len(a)) if i < len(b) and a[i] != b[i]}
            ok = len(a) == len(b) and diff <= unstable
        note = "modulo ASLR bytes"
    if ok:
        extra = " (and identical IR)" if ir_same else ""
        print(f"  ok   identity: {name}: byte-identical{extra}")
    else:
        print(f"  FAIL identity: {name}: dumps differ")
        failures.append(name)


def main() -> int:
    missing = [p.name for p in (FRONTEND, TIER, JIT) if not p.exists()]
    if missing:
        print(f"missing: {', '.join(missing)} -- build first")
        return 1

    failures = []
    mode = "setarch -R (exact bytes)" if SETARCH else "ASLR-masked bytes"
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)

        print(f"typecheck: uninitialized declarations (0.6.10)  [{mode}]")
        print("reject:")
        for name, source, needle in REJECT_CASES:
            check_reject(tmp, name, source, needle, failures)
        print("accept (interpreter == native):")
        for name, source, expected in ACCEPT_CASES:
            check_accept(tmp, name, source, expected, failures)
        print("codegen identity (declaration+assignment == in-place init):")
        for name, decl_src, init_src in IDENTITY_CASES:
            check_identity(tmp, name, decl_src, init_src, failures)

    total = len(REJECT_CASES) + len(ACCEPT_CASES) + len(IDENTITY_CASES)
    if failures:
        print(f"\n{len(failures)} of {total} check(s) FAILED")
        return 1
    print(f"\nall {total} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
