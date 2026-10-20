#!/usr/bin/env python3
"""Prove 4.8 type functions are purely structural.

A type function is a compile-time type constructor: applying it substitutes its
arguments into the body and re-parses the result through
`parse_type_annotation`, so the type it yields must be *indistinguishable* from
the expansion spelled out. The strongest way to check that is to compile two
programs that differ ONLY in how the types are written -- one through a type
function, one spelled out -- and require the emitted IR to be byte-identical.
The definitions emit no code, so if any trace of the type function survived,
the two IR texts would differ.

A second part checks that a repeat application is memoized (one cache entry per
distinct call shape) and that the active-expansion stack is clean afterwards.

Run: python3 tools/check_typefunc.py
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = REPO / "src" / "frontend" / "frontend.py"

# Two programs that differ only in annotation spelling: `via` uses type
# functions, `spelled` writes the expansions. The runtime statements are
# identical, so the IR must be identical too.
VIA = """\
def Fixed(t, n: int[16]) -> NewType:
    return {list[t, n]}


def Word() -> NewType:
    return {int[32]}


def Alias(t) -> NewType:
    return {t}


def Ref(t) -> NewType:
    return {ptr[t]}


def addtwo(x: Word(), y: Word()) -> Word():
    return x + y


xs: Fixed(int[64], 4)
xs[0] = 1
w: Word() = 2
a: int[64] = 3
a: Alias(int[64]) = 4
f: int[64] = 5
_p: Ref(int[64]) = addressof(f)
print(xs[0])
print(w)
print(addtwo(10, 20))
print(a)
print(valueof(_p))
"""

SPELLED = """\
def addtwo(x: int[32], y: int[32]) -> int[32]:
    return x + y


xs: list[int[64], 4]
xs[0] = 1
w: int[32] = 2
a: int[64] = 3
a: int[64] = 4
f: int[64] = 5
_p: ptr[int[64]] = addressof(f)
print(xs[0])
print(w)
print(addtwo(10, 20))
print(a)
print(valueof(_p))
"""

# (label, source) refusals the frontend must reject with the given E-code.
REFUSALS = [
    ("definition: two return values",
     "def T(a) -> NewType:\n    return {a, a}\nprint(0)\n", "LITHON-E0132"),
    ("definition: empty braces are a dict",
     "def T() -> NewType:\n    return {}\nprint(0)\n", "LITHON-E0132"),
    ("application: value parameter annotated with a container",
     "def T(a: list[int[64], 4]) -> NewType:\n    return {int[64]}\n"
     "x: T(1) = 1\nprint(0)\n", "LITHON-E0132"),
    ("application: value parameter given a type",
     "def T(n: int[16]) -> NewType:\n    return {list[int[64], n]}\n"
     "x: T(int[64]) = 1\nprint(0)\n", "LITHON-E0132"),
    ("application: value parameter argument out of range",
     "def T(n: int[8]) -> NewType:\n    return {list[int[64], n]}\n"
     "x: T(300) = 1\nprint(0)\n", "LITHON-E0132"),
    ("application: bool value parameter given an int",
     "def T(b: bool) -> NewType:\n    return {list[int[64], 1]}\n"
     "x: T(1) = 1\nprint(0)\n", "LITHON-E0132"),
    ("definition: collides with a struct",
     "def T(a) -> NewType:\n    return {a}\nclass T(Struct):\n    x: int[64]\nprint(0)\n",
     "LITHON-E0132"),
    ("application: not a declared type function",
     "x: T(int[64]) = 1\nprint(0)\n", "LITHON-E0133"),
    ("application: wrong arity",
     "def P(a, b) -> NewType:\n    return {list[a, b]}\nx: P(int[64]) = 1\nprint(0)\n",
     "LITHON-E0133"),
    ("application: direct recursion",
     "def P(a) -> NewType:\n    return {P(a)}\nx: P(int[64]) = 1\nprint(0)\n",
     "LITHON-E0133"),
    ("application: mutual recursion",
     "def P(a) -> NewType:\n    return {Q(a)}\ndef Q(a) -> NewType:\n    return {P(a)}\n"
     "x: P(int[64]) = 1\nprint(0)\n", "LITHON-E0133"),
    ("definition: body names a struct",
     "class S(Struct):\n    v: int[64]\ndef P() -> NewType:\n    return {S}\ns: P()\n",
     "LITHON-E0132"),
]


def frontend_ir(source: str, tmp: pathlib.Path) -> tuple[int, str]:
    path = tmp / "prog.lio"
    path.write_text(source, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(FRONTEND), str(path)],
                          capture_output=True, text=True)
    return proc.returncode, (proc.stdout if proc.returncode == 0 else proc.stderr)


def load_frontend():
    spec = importlib.util.spec_from_file_location("lithon_frontend", FRONTEND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)

        a_code, a_out = frontend_ir(VIA, tmp)
        b_code, b_out = frontend_ir(SPELLED, tmp)
        if a_code != 0:
            failures.append(f"the type-function program was refused:\n{a_out}")
        if b_code != 0:
            failures.append(f"the spelled-out program was refused:\n{b_out}")
        if a_code == 0 and b_code == 0 and a_out != b_out:
            failures.append(
                "IR is NOT identical between the type-function and spelled-out "
                f"programs:\n--- via ---\n{a_out}\n--- spelled ---\n{b_out}")
        if not failures:
            print(f"  ok   IR is byte-identical: {len(a_out)} bytes, "
                  f"{a_out.count(chr(10))} lines (type function == spelled out)")

        for label, source, code in REFUSALS:
            rc, out = frontend_ir(source, tmp)
            if rc == 0:
                failures.append(f"{label}: frontend accepted it (wanted {code})")
            elif code not in out:
                failures.append(f"{label}: wanted {code}, got: {out.strip().splitlines()[-1] if out.strip() else '<empty>'}")
        if not failures:
            print(f"  ok   {len(REFUSALS)} refusals carried the right E-code")

    # Memoization: two identical applications share one cache entry, the stack
    # is clean after a run, and a repeated call hands back the same tuple object.
    fe = load_frontend()
    fe.build_program(ast.parse(
        "def Pair(t) -> NewType:\n    return {list[t, 2]}\n"
        "a: Pair(int[64])\nb: Pair(int[64])\nc: Pair(int[32])\n"))
    entries = len(fe._TYPE_FUNC_CACHE)
    active_clean = fe._TYPE_FUNC_ACTIVE == []
    call = ast.parse("Pair(int[64])", mode="eval").body
    same_object = fe._expand_type_function(call) is fe._expand_type_function(call)
    if entries != 2:
        failures.append(f"memoization: expected 2 cache entries for Pair(int[64]) "
                        f"and Pair(int[32]), got {entries}")
    if not active_clean:
        failures.append(f"active-expansion stack not clean after a run: {fe._TYPE_FUNC_ACTIVE}")
    if not same_object:
        failures.append("memoization: a repeated application did not return the cached tuple")
    if not failures:
        print(f"  ok   memoized: {entries} entries for 3 applications; repeat call is the "
              f"same object; stack clean")

    if failures:
        print("\ntype-function structural check FAILED:")
        for f in failures:
            print(f"  FAIL {f}")
        return 1
    print("\ntype functions are structural: expansion is byte-identical to the spelling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
