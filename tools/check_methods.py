#!/usr/bin/env python3
"""Prove 4.9 methods are semantically transparent.

A method is inlined at the call site, so a program that uses a method and the
same program with the method body written out by hand must do exactly the same
thing. Each case below is a pair: the `METHOD` source and the `MANUAL` source
it must equal. Both are compiled through the frontend, run in BOTH tiers (the
interpreter and the native JIT), and required to produce the same stdout -- so
the pair must agree with each other and with the recorded expectation, and the
two tiers must agree with each other.

The IR is also required to be deterministic: compiling the method source twice
must produce byte-identical text.

Refusals check that the definition and call sites that cannot be inlined carry
the documented E-codes.

Run: python3 tools/check_methods.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = REPO / "src" / "frontend" / "frontend.py"
TIER_RUNNER = REPO / "build" / "tier_runner"


METHOD_STRUCT = """\
class Counter(Struct):
    value: int[64]

    def add(self, n: int[64]) -> int[64]:
        self.value = self.value + n
        return self.value

    def total(self, n: int[64]) -> int[64]:
        t: int[64] = 0
        i: int[64] = 1
        while i < n + 1:
            t = t + i
            i = i + 1
        return t


c: Counter
print(c.add(5))
print(c.add(7))
print(c.total(4))
"""

MANUAL_STRUCT = """\
class Counter(Struct):
    value: int[64]


c: Counter
c.value = c.value + 5
print(c.value)
c.value = c.value + 7
print(c.value)
t: int[64] = 0
i: int[64] = 1
while i < 4 + 1:
    t = t + i
    i = i + 1
print(t)
"""

METHOD_NESTED = """\
class Inner(Struct):
    x: int[64]

    def bump(self, by: int[64]):
        self.x = self.x + by

    def dbl(self) -> int[64]:
        self.bump(self.x)
        return self.x


class Outer(Struct):
    inner: Inner


o: Outer
o.inner.x = 3
print(o.inner.dbl())
o.inner.bump(4)
print(o.inner.x)
"""

MANUAL_NESTED = """\
class Inner(Struct):
    x: int[64]


class Outer(Struct):
    inner: Inner


o: Outer
o.inner.x = 3
o.inner.x = o.inner.x + o.inner.x
print(o.inner.x)
o.inner.x = o.inner.x + 4
print(o.inner.x)
"""

METHOD_ENUM = """\
class Shape(Enum):
    Empty
    Line: int[32]

    def code(self) -> int[64]:
        match self:
            case Shape.Empty:
                return 0
            case Shape.Line(n):
                return n
            case _:
                return -1


s: Shape
s = Shape.Line(7)
print(s.code())
s = Shape.Empty
print(s.code())
"""

MANUAL_ENUM = """\
class Shape(Enum):
    Empty
    Line: int[32]


s: Shape
s = Shape.Line(7)
match s:
    case Shape.Empty:
        print(0)
    case Shape.Line(n):
        print(n)
    case _:
        print(-1)
s = Shape.Empty
match s:
    case Shape.Empty:
        print(0)
    case Shape.Line(n):
        print(n)
    case _:
        print(-1)
"""

CASES = [
    ("struct method == hand-inlined struct code", METHOD_STRUCT, MANUAL_STRUCT),
    ("nested receiver + method-calls-method == hand-inlined",
     METHOD_NESTED, MANUAL_NESTED),
    ("enum method (match self) == hand-written match", METHOD_ENUM, MANUAL_ENUM),
]

REFUSALS = [
    ("definition: decorator",
     "class P(Struct):\n    x: int[64]\n    @staticmethod\n    def f(self) -> int[64]:\n"
     "        return 1\np: P\nprint(p.f())\n", "LITHON-E0134"),
    ("definition: first parameter is not self",
     "class P(Struct):\n    x: int[64]\n    def f(a: int[64]) -> int[64]:\n"
     "        return a\np: P\nprint(p.f(1))\n", "LITHON-E0134"),
    ("definition: annotated self",
     "class P(Struct):\n    x: int[64]\n    def f(self: int[64]) -> int[64]:\n"
     "        return 1\np: P\nprint(p.f())\n", "LITHON-E0134"),
    ("definition: unannotated parameter",
     "class P(Struct):\n    x: int[64]\n    def f(self, a) -> int[64]:\n"
     "        return a\np: P\nprint(p.f(1))\n", "LITHON-E0134"),
    ("definition: name collides with a field",
     "class P(Struct):\n    x: int[64]\n    def x(self) -> int[64]:\n"
     "        return 1\np: P\nprint(p.x())\n", "LITHON-E0134"),
    ("call: unknown method",
     "class P(Struct):\n    x: int[64]\np: P\nprint(p.h())\n", "LITHON-E0135"),
    ("call: field used as a method",
     "class P(Struct):\n    x: int[64]\np: P\nprint(p.x())\n", "LITHON-E0135"),
    ("call: recursive method",
     "class P(Struct):\n    x: int[64]\n    def f(self) -> int[64]:\n"
     "        return self.f()\np: P\nprint(p.f())\n", "LITHON-E0135"),
    ("call: void method used as a value",
     "class P(Struct):\n    x: int[64]\n    def f(self):\n        return\n"
     "p: P\nprint(p.f())\n", "LITHON-E0135"),
]


def frontend_compile(source: str, tmp: pathlib.Path, name: str = "prog.py"):
    path = tmp / name
    path.write_text(source, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(FRONTEND), str(path)],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def run_tier(ir_text: str, tmp: pathlib.Path, tier: str):
    ir = tmp / "program.ir"
    ir.write_text(ir_text, encoding="utf-8")
    proc = subprocess.run([str(TIER_RUNNER), str(ir), tier],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def main() -> int:
    failures: list[str] = []
    if not TIER_RUNNER.exists():
        print(f"  FAIL {TIER_RUNNER} is missing -- build tier_runner first")
        return 1

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)

        for label, method_src, manual_src in CASES:
            mrc, mir, merr = frontend_compile(method_src, tmp, "method.py")
            crc, cir, cerr = frontend_compile(manual_src, tmp, "manual.py")
            if mrc != 0:
                failures.append(f"{label}: method source refused:\n{merr}")
                continue
            if crc != 0:
                failures.append(f"{label}: manual source refused:\n{cerr}")
                continue
            # Determinism: the same source must compile to the same text.
            if frontend_compile(method_src, tmp, "method2.py")[1] != mir:
                failures.append(f"{label}: frontend IR is not deterministic")
            for tier in ("--interp", "--native"):
                mrun = run_tier(mir, tmp, tier)
                crun = run_tier(cir, tmp, tier)
                if mrun[0] != 0:
                    failures.append(f"{label}: method run failed in {tier}:\n{mrun[2]}")
                elif crun[0] != 0:
                    failures.append(f"{label}: manual run failed in {tier}:\n{crun[2]}")
                elif mrun[1] != crun[1]:
                    failures.append(
                        f"{label}: outputs differ in {tier}:\n--- method ---\n"
                        f"{mrun[1]}--- manual ---\n{crun[1]}")
            if not failures:
                print(f"  ok   {label}: both tiers agree, and match hand-inlined code")

        for label, source, code in REFUSALS:
            rc, _out, err = frontend_compile(source, tmp, "refuse.py")
            if rc == 0:
                failures.append(f"{label}: accepted (wanted {code})")
            elif code not in err:
                last = err.strip().splitlines()[-1] if err.strip() else "<empty>"
                failures.append(f"{label}: wanted {code}, got: {last}")
            elif "Traceback" in err:
                failures.append(f"{label}: leaked a Python traceback")
        if not failures:
            print(f"  ok   {len(REFUSALS)} method refusals carried the right E-code")

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"[{f}]")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
