#!/usr/bin/env python3
"""4.14 unsigned integers: every operator, every width, edge-heavy values, against
an exact oracle.

For each (width, operator) one program is generated that evaluates the operator
over every pair of a set of edge values, each inside a try/except so a trap is
printed instead of ending the program. The expected line for each pair is
computed here with Python's arbitrary-precision integers: the exact result, or
"trap" when it leaves 0..2^w-1 (E0313, an OverflowError), or "zero" for `%` by
zero. The program is then run through the interpreter and the native tier, with
and without the SSA pipeline and direct Phis, and every line must match.

    tools/check_uint.py            # all widths and operators
    tools/check_uint.py --quick    # a smaller value set
"""
from __future__ import annotations

import argparse
import itertools
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "src" / "frontend" / "frontend.py"
RUNNER = ROOT / "build" / "tier_runner"
MODES = [["--interp"], ["--auto"], ["--auto", "--ssa"], ["--auto", "--direct-phis"]]


def edge_values(w, quick):
    mx = (1 << w) - 1
    vals = {0, 1, 2, 3, 7, mx, mx - 1, mx >> 1, (mx >> 1) + 1, (mx >> 1) + 2}
    if not quick:
        vals |= {10, 255 if w >= 8 else 1, mx // 3, mx - 7, 1 << (w // 2)}
    return sorted(v for v in vals if 0 <= v <= mx)


ARITH = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "%": lambda a, b: a % b,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    "^": lambda a, b: a ^ b,
}
COMPARE = {
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}


def expected_arith(op, a, b, w):
    mx = (1 << w) - 1
    if op == "%" and b == 0:
        return "zero"
    r = ARITH[op](a, b)
    return "trap" if r < 0 or r > mx else str(r)


def build_arith(op, w, vals):
    lines = [f"a: uint[{w}] = 0", f"b: uint[{w}] = 0", f"r: uint[{w}] = 0"]
    expect = []
    for a, b in itertools.product(vals, vals):
        lines += [f"a = {a}", f"b = {b}", "try:", f"    r = a {op} b", "    print(r)",
                  "except OverflowError:", '    print("trap")',
                  "except ZeroDivisionError:", '    print("zero")']
        expect.append(expected_arith(op, a, b, w))
    return "\n".join(lines) + "\n", expect


def build_shift(op, w, vals):
    mx = (1 << w) - 1
    lines = [f"a: uint[{w}] = 0", f"r: uint[{w}] = 0"]
    expect = []
    counts = sorted(k for k in {0, 1, 2, w // 2, w - 1, w, w + 1, 63} if k <= 63)
    for a in vals:
        for k in counts:
            lines += [f"a = {a}", "try:", f"    r = a {op} {k}", "    print(r)",
                      "except OverflowError:", '    print("trap")']
            r = (a << k) if op == "<<" else (a >> k)
            expect.append("trap" if r > mx else str(r))
    return "\n".join(lines) + "\n", expect


def build_compare(op, w, vals):
    lines = [f"a: uint[{w}] = 0", f"b: uint[{w}] = 0"]
    expect = []
    for a, b in itertools.product(vals, vals):
        lines += [f"a = {a}", f"b = {b}", f"print(a {op} b)"]
        expect.append(str(COMPARE[op](a, b)))
    return "\n".join(lines) + "\n", expect


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def check(tmp, name, source, expect):
    src = tmp / f"{name}.lio"
    src.write_text(source)
    ir = run(["python3", str(FRONTEND), str(src)])
    if ir.returncode != 0:
        return [f"{name}: frontend refused: {ir.stderr.strip()[:200]}"]
    irp = tmp / f"{name}.ir"
    irp.write_text(ir.stdout)
    problems = []
    for mode in MODES:
        r = run([str(RUNNER), str(irp)] + mode)
        got = [ln for ln in r.stdout.splitlines()]
        if r.returncode != 0:
            problems.append(f"{name} {' '.join(mode)}: exit {r.returncode}: "
                            f"{r.stderr.strip()[-160:]}")
        elif got != expect:
            bad = next(i for i, (g, e) in enumerate(zip(got, expect)) if g != e) \
                if len(got) == len(expect) else min(len(got), len(expect))
            problems.append(f"{name} {' '.join(mode)}: line {bad}: got "
                            f"{got[bad] if bad < len(got) else None!r}, want "
                            f"{expect[bad] if bad < len(expect) else None!r}")
    return problems


# 4.14 exposed that the SSA pipeline's constant folder wrapped an int64 product of
# two propagated constants instead of leaving it to trap E0303. Every path must
# trap, in the signed case the unsigned lowering leans on.
OVERFLOW_TRAP = """a: int[64] = 4294967296
b: int[64] = 4294967296
c: int[64] = a * b
print(c)
"""


def check_overflow_trap(tmp):
    src = tmp / "ovf.lio"
    src.write_text(OVERFLOW_TRAP)
    ir = run(["python3", str(FRONTEND), str(src)])
    irp = tmp / "ovf.ir"
    irp.write_text(ir.stdout)
    problems = []
    for mode in MODES:
        r = run([str(RUNNER), str(irp)] + mode)
        if r.returncode == 0 or "LITHON-E0303" not in r.stderr:
            problems.append(f"overflow trap {' '.join(mode)}: exit {r.returncode}, "
                            f"stdout {r.stdout!r}, stderr {r.stderr.strip()[-120:]!r}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if not RUNNER.exists():
        print(f"missing {RUNNER}; build it first")
        return 2
    failures, programs, lines = [], 0, 0
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        problems = check_overflow_trap(tmp)
        if problems:
            failures += problems
            print("  FAIL int64 product overflow traps on every path")
            for p in problems:
                print(f"       {p}")
        else:
            print("  ok   int64 product overflow traps on every path")
        for w in (8, 16, 32, 64):
            vals = edge_values(w, args.quick)
            jobs = [(f"arith{w}_{n}", *build_arith(op, w, vals))
                    for n, op in enumerate(ARITH)]
            jobs += [(f"shift{w}_{n}", *build_shift(op, w, vals))
                     for n, op in enumerate(("<<", ">>"))]
            jobs += [(f"cmp{w}_{n}", *build_compare(op, w, vals))
                     for n, op in enumerate(COMPARE)]
            for name, source, expect in jobs:
                programs += 1
                lines += len(expect)
                problems = check(tmp, name, source, expect)
                if problems:
                    failures += problems
                    print(f"  FAIL {name}")
                    for p in problems[:3]:
                        print(f"       {p}")
                else:
                    print(f"  ok   {name} ({len(expect)} cases x {len(MODES)} paths)")
    if failures:
        print(f"\n{len(failures)} failing path(s) across {programs} programs")
        return 1
    print(f"\nall {lines} cases x {len(MODES)} execution paths agree with the exact "
          f"oracle ({programs} programs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
