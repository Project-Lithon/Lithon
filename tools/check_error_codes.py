#!/usr/bin/env python3
"""LITHON-Exxxx error-code discipline (docs/lithon_error_system.md section 6).

The tier-grouped codes are the table in docs/whitepaper.md. This tool enforces
the discipline for the codes that are landed so far and grows as more land:

  1. Every LITHON-Exxxx code emitted anywhere in the source must be shaped
     `LITHON-E0<tier><dd>` with <tier> in 1..4, and must be documented in
     docs/whitepaper.md. A code that appears in code but not in the table is a
     failure -- ambiguous diagnostics regress to prose-only, which the doc
     explicitly forbids going forward.
  2. Functional checks for each coded refusal as its enforcement lands.
     Today: E0105 (unsupported type width), E0303 (int64 overflow: the
     constant case refused at typecheck, the dynamic case trapping with
     byte-identical messages in the interpreter and JIT) and E0401 (the
     stack-depth guard: unbounded recursion traps at the same depth with the
     same bytes in both tiers). Both enforced in the frontend/typechecker and
     pinned in both tiers.

    tools/check_error_codes.py
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "src" / "frontend" / "frontend.py"
TIER = ROOT / "build" / "tier_runner"
WHITEPAPER = ROOT / "docs" / "whitepaper.md"

CODE_RE = re.compile(r"LITHON-(E\d{4})")
PLAIN_CODE_RE = re.compile(r"\bE\d{4}\b")

# (name, checker to run, source/IR, needle)
E0105_REJECTS = [
    ("int[7] (source)", "frontend", "x: int[7] = 1\nprint(x)\n", "LITHON-E0105"),
    ("int[128] (source)", "frontend", "x: int[128] = 1\nprint(x)\n", "LITHON-E0105"),
    ("int[3] (source)", "frontend", "x: int[3] = 1\nprint(x)\n", "LITHON-E0105"),
    ("float[128] (source)", "frontend", "x: float[128] = 1.0\nprint(x)\n", "LITHON-E0105"),
    ("float[32] (source)", "frontend", "x: float[32] = 1.0\nprint(x)\n", "LITHON-E0105"),
    ("int with no width (source)", "frontend", "x: int = 1\nprint(x)\n", "LITHON-E0105"),
    ("int[64] element width 7 (source)",
     "frontend", "xs: list[int[7], 4] = [1, 2, 3, 4]\nprint(xs[0])\n", "LITHON-E0105"),
    ("int[7] (IR)", "typecheck", r"""
function __main__():
block0:
    store x : int[7]
    return
""", "LITHON-E0105"),
    ("int[127] (IR)", "typecheck", r"""
function __main__():
block0:
    store x : int[127]
    return
""", "LITHON-E0105"),
    ("float[16] (IR)", "typecheck", r"""
function __main__():
block0:
    store x : float[16]
    return
""", "LITHON-E0105"),
    ("ptr to int[9] (IR)", "typecheck", r"""
function __main__():
block0:
    store _p : ptr[int[9]]
    return
""", "LITHON-E0105"),
    ("list of int[7] (IR)", "typecheck", r"""
function __main__():
block0:
    store xs : list[int[7], 4]
    return
""", "LITHON-E0105"),
    ("int[7] parameter (IR)", "typecheck", r"""
function f(x: int[7]) -> int[8]:
block0:
    return
""", "LITHON-E0105"),
    ("int[7] return type (IR)", "typecheck", r"""
function f(x: int[8]) -> int[7]:
block0:
    return
""", "LITHON-E0105"),
]

E0105_ACCEPTS = [
    "x: int[8] = 1\nprint(x)\n",
    "x: int[16] = 1\nprint(x)\n",
    "x: int[32] = 1\nprint(x)\n",
    "x: int[64] = 1\nprint(x)\n",
    "x: float[64] = 1.0\nprint(x)\n",
]

# E0303 (the tier-3 int64 overflow trap). Aspect 1: the type checker bans the
# constant case and refuses wrap_* on non-int64 operands (docs/lithon_error_system.md 3a).
# Aspect 2: the dynamic overflow traps identically in the interpreter and the
# JIT, and wrap_add/sub/mul produce the SAME wrapped value in both tiers.
E0303_REJECTS = [
    ("constant add overflow (IR)", r"""
function __main__():
block0:
    %0 = const_i64 9223372036854775807
    %1 = const_i64 1
    %2 = add %0, %1
    store z, %2 : int[64]
    %3 = load z
    call print, %3
    return
""", "LITHON-E0303"),
    ("constant sub underflow (IR)", r"""
function __main__():
block0:
    %0 = const_i64 -9223372036854775808
    %1 = const_i64 1
    %2 = sub %0, %1
    store z, %2 : int[64]
    %3 = load z
    call print, %3
    return
""", "LITHON-E0303"),
    ("constant mul overflow (IR)", r"""
function __main__():
block0:
    %0 = const_i64 3037000500
    %1 = const_i64 3037000500
    %2 = mul %0, %1
    store z, %2 : int[64]
    %3 = load z
    call print, %3
    return
""", "LITHON-E0303"),
    ("wrap_add on float operands (IR)", r"""
function __main__():
block0:
    %0 = const_f64 1.5
    %1 = const_f64 2.5
    %2 = wrapadd %0, %1
    store z, %2 : int[64]
    %3 = load z
    call print, %3
    return
""", "LITHON-E0303"),
    ("wrap_add on a narrow int operand (IR)", r"""
function __main__():
block0:
    store a : int[8]
    %0 = const_i64 100
    store a, %0 : int[8]
    %1 = load a
    %2 = const_i64 100
    %3 = wrapadd %1, %2
    store z, %3 : int[64]
    %4 = load z
    call print, %4
    return
""", "LITHON-E0303"),
    ("wrap_add on a pointer operand (IR)", r"""
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    %2 = const_i64 8
    %3 = wrapadd %1, %2
    store z, %3 : int[64]
    %4 = load z
    call print, %4
    return
""", "LITHON-E0303"),
]

# The opt-outs must lower from source and run identically in both tiers. The
# stdout is the pinned wrap result for each op.
E0303_WRAPS = [
    ("wrap_add(MAX, 1) wraps to -2^63 (source)", 
     "x: int[64] = 9223372036854775807\ny: int[64] = 1\nz: int[64] = wrap_add(x, y)\nprint(z)\n",
     "-9223372036854775808"),
    ("wrap_sub(MIN, 1) wraps to 2^63-1 (source)",
     "x: int[64] = -9223372036854775808\ny: int[64] = 1\nz: int[64] = wrap_sub(x, y)\nprint(z)\n",
     "9223372036854775807"),
    ("wrap_mul(3037000500, 3037000500) wraps (source)",
     "x: int[64] = 3037000500\ny: int[64] = 3037000500\nz: int[64] = wrap_mul(x, y)\nprint(z)\n",
     "-9223372036709301616"),
    ("dynamic wrap stays byte-identical through add (source)",
     "x: int[64] = 9223372036854775806\ny: int[64] = 2\nz: int[64] = wrap_add(x, y)\nprint(z)\n",
     "-9223372036854775808"),
]

# The dynamic overflow is not statically refused; it must trap with the SAME
# message in the interpreter and in native-JIT execution.
E0303_TRAPS = [
    ("dynamic int64 add overflows (source)",
     "x: int[64] = 9223372036854775806\ny: int[64] = 2\nz: int[64] = x + y\nprint(z)\n",
     "LITHON-E0303: integer overflow in add -- use wrap_add() to wrap instead"),
    ("dynamic int64 sub underflows (source)",
     "x: int[64] = -9223372036854775808\ny: int[64] = 1\nz: int[64] = x - y\nprint(z)\n",
     "LITHON-E0303: integer overflow in sub -- use wrap_sub() to wrap instead"),
]

# E0401 (the stack-depth guard). The two tiers share one counter, so a
# recursion that overflows must trap at exactly the same depth with exactly
# the same bytes in both. A recursion comfortably under the cap must still run.
E0401_ACCEPTS = [
    ("recursion of depth 1000 under the 4096 cap (source)",
     "def down(n: int[64]) -> int[64]:\n"
     "    if n < 1:\n"
     "        return 0\n"
     "    x: int[64] = down(n - 1)\n"
     "    return x + 1\n"
     "\n"
     "r: int[64] = down(1000)\n"
     "print(r)\n",
     "1000"),
]

E0401_TRAPS = [
    ("unbounded recursion overflows (source)",
     "def bomb(n: int[64]) -> int[64]:\n"
     "    x: int[64] = bomb(n + 1)\n"
     "    return x\n"
     "\n"
     "y: int[64] = bomb(0)\n"
     "print(y)\n",
     "LITHON-E0401: stack overflow -- call depth 4097 exceeded the 4096-frame "
     "guard; rewrite the recursion as an explicit loop"),
    # 6.7. str.split writes at most the destination's capacity and returns the
    # true piece count; more pieces than slots is a run-time trap, and it must
    # read identically in both tiers.
    ("str.split produces more pieces than the list holds (source)",
     "s: str[32] = \"a,b,c,d,e\"\n"
     "parts: list[str[8], 3] = s.split(\",\")\n"
     "print(parts)\n",
     "too many pieces for the list (str.split)"),
]


def _run_source(tmp, tag, source, mode):
    """Compile a source program and run it under tier_runner in `mode`.

    Returns (returncode, normalized stdout, normalized stderr).
    """
    src = tmp / f"{tag}.lio"
    src.write_text(source)
    ir = tmp / f"{tag}.ir"
    r = run(["python3", str(FRONTEND), str(src)])
    if r.returncode != 0:
        return (r.returncode, "", r.stderr)
    ir.write_text(r.stdout)
    r = run([str(TIER), str(ir), mode])
    out = "\n".join(ln for ln in r.stdout.splitlines() if not ln.startswith("[tier"))
    err = "\n".join(ln for ln in r.stderr.splitlines() if not ln.startswith("[tier"))
    return (r.returncode, out, err)


def check_e0303(failures):
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for name, body, needle in E0303_REJECTS:
            ir = tmp / "in.ir"
            ir.write_text(body)
            r = run([str(TIER), str(ir), "--interp"])
            if r.returncode != 0 and needle in (r.stderr + r.stdout):
                print(f"  ok   reject: {name}")
            else:
                print(f"  FAIL reject: {name}: "
                      f"{'accepted' if r.returncode == 0 else 'refused without ' + needle!r}")
                failures.append(name)
        for i, (name, source, expect) in enumerate(E0303_WRAPS):
            rc_i, out_i, err_i = _run_source(tmp, f"w{i}", source, "--interp")
            rc_a, out_a, err_a = _run_source(tmp, f"w{i}a", source, "--auto")
            if rc_i != 0 or rc_a != 0 or out_i != expect or out_a != expect:
                print(f"  FAIL wrap: {name}: interp rc={rc_i} out={out_i!r} err={err_i.strip()!r}; "
                      f"auto rc={rc_a} out={out_a!r} err={err_a.strip()!r}")
                failures.append(name)
            elif out_i != out_a:
                print(f"  FAIL wrap: {name}: interpreter {out_i!r} != JIT {out_a!r}")
                failures.append(name)
            else:
                print(f"  ok   wrap: {name} ({out_i})")
        for i, (name, source, needle) in enumerate(E0303_TRAPS):
            rc_i, out_i, err_i = _run_source(tmp, f"t{i}", source, "--interp")
            rc_a, out_a, err_a = _run_source(tmp, f"t{i}a", source, "--auto")
            text_i = (out_i + "\n" + err_i)
            text_a = (out_a + "\n" + err_a)
            if rc_i == 0 or rc_a == 0:
                print(f"  FAIL trap: {name}: interp rc={rc_i} auto rc={rc_a} (no trap)")
                failures.append(name)
            elif needle not in text_i or needle not in text_a:
                print(f"  FAIL trap: {name}: interp has={needle in text_i} auto has={needle in text_a}")
                failures.append(name)
            else:
                print(f"  ok   trap: {name} (byte-identical in both tiers)")


def check_e0401(failures):
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for i, (name, source, expect) in enumerate(E0401_ACCEPTS):
            rc_i, out_i, err_i = _run_source(tmp, f"a{i}", source, "--interp")
            rc_a, out_a, err_a = _run_source(tmp, f"a{i}j", source, "--auto")
            if rc_i != 0 or rc_a != 0 or out_i != expect or out_a != expect:
                print(f"  FAIL accept: {name}: interp rc={rc_i} out={out_i!r}; "
                      f"auto rc={rc_a} out={out_a!r} err={err_a.strip()!r}")
                failures.append(name)
            else:
                print(f"  ok   accept: {name} ({out_i})")
        for i, (name, source, needle) in enumerate(E0401_TRAPS):
            rc_i, out_i, err_i = _run_source(tmp, f"t{i}", source, "--interp")
            rc_a, out_a, err_a = _run_source(tmp, f"t{i}j", source, "--auto")
            text_i = (out_i + "\n" + err_i)
            text_a = (out_a + "\n" + err_a)
            if rc_i == 0 or rc_a == 0:
                print(f"  FAIL trap: {name}: interp rc={rc_i} auto rc={rc_a} (no trap)")
                failures.append(name)
            elif needle not in text_i or needle not in text_a:
                print(f"  FAIL trap: {name}: interp has={needle in text_i} "
                      f"auto has={needle in text_a}")
                failures.append(name)
            elif text_i != text_a:
                print(f"  FAIL trap: {name}: tiers differ:\n    interp {text_i!r}\n"
                      f"    auto   {text_a!r}")
                failures.append(name)
            else:
                print(f"  ok   trap: {name} (byte-identical in both tiers)")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def used_codes():
    codes = set()
    for path in list((ROOT / "src").rglob("*.cpp")) + list((ROOT / "src").rglob("*.h")):
        codes.update(CODE_RE.findall(path.read_text(errors="ignore")))
    for path in (ROOT / "src" / "frontend" / "frontend.py", ROOT / "tools" / "typecheck.py"):
        codes.update(CODE_RE.findall(path.read_text(errors="ignore")))
    return codes


def documented_codes():
    return {m for m in PLAIN_CODE_RE.findall(WHITEPAPER.read_text())}


def check_discipline(failures):
    used = used_codes()
    documented = documented_codes()
    ok_shape = True
    for code in sorted(used):
        if not re.fullmatch(r"E0[1234]\d\d", code):
            print(f"  FAIL discipline: {code} is not shaped E0<tier><dd> (tier 1..4)")
            ok_shape = False
    if not ok_shape:
        print("  FAIL discipline: a malformed code would never reach the table")
    missing = sorted(used - documented)
    if missing:
        print(f"  FAIL discipline: codes used in source but missing from docs/whitepaper.md: "
              f"{', '.join(missing)}")
        failures.append("undocumented code")
    else:
        print(f"  ok   discipline: every used code is documented ({len(used)})")


# E0107..E0125: every frontend refusal carries a LITHON-E01xx code and prints
# one clean line (no Python traceback). A refusal that regresses to bare prose,
# or leaks a traceback, fails here.
FRONTEND_REFUSALS = [
    ("break outside a loop (E0120)",
     "break\nx: int[64] = 1\nprint(x)\n", "LITHON-E0120"),
    ("continue outside a loop (E0120)",
     "continue\nx: int[64] = 1\nprint(x)\n", "LITHON-E0120"),
    ("and/or with three operands (E0114)",
     "a: int[64] = 1\nb: int[64] = 2\nc: int[64] = 3\nx: int[64] = a and b and c\nprint(x)\n",
     "LITHON-E0114"),
    ("chained comparison (E0114)",
     "a: int[64] = 1\nb: int[64] = 2\nc: int[64] = 3\nprint(a < b < c)\n",
     "LITHON-E0114"),
    ("for over a list, only range() exists (E0119)",
     "xs: list[int[64], 4]\nfor i in xs:\n    print(i)\n",
     "LITHON-E0119"),
    # 6.1. enumerate/zip are a rewrite of a counted loop, so they exist only in
    # a for header: there is no value to bind, and a homogeneous tuple cannot
    # represent enumerate's (int, T) pair anyway. Used as a value they must be
    # refused HERE, with that reason, rather than falling through to a generic
    # `call` that the checker rejects as an unknown function.
    ("zip() as a value (E0115)",
     "xs: list[int[64], 4]\nz: int[64] = zip(xs, xs)\nprint(z)\n",
     "LITHON-E0115"),
    ("enumerate() as a value (E0115)",
     "xs: list[int[64], 4]\nz: int[64] = enumerate(xs)\nprint(z)\n",
     "LITHON-E0115"),
    ("enumerate with the wrong number of targets (E0119)",
     "xs: list[int[64], 4]\nfor i in enumerate(xs):\n    print(i)\n",
     "LITHON-E0119"),
    ("enumerate over a non-container (E0119)",
     "x: int[64] = 1\nfor i, v in enumerate(x):\n    print(i)\n",
     "LITHON-E0119"),
    ("zip of a single container (E0119)",
     "xs: list[int[64], 4]\nfor a in zip(xs):\n    print(a)\n",
     "LITHON-E0119"),
    # 6.1. A loop target is a binding, so it obeys the same one-type-per-name
    # rule as an annotated declaration. Registering it is what turns a silent
    # type change into a refusal that names both types.
    ("an enumerate target rebound to a different type (E0122)",
     "xs: list[int[64], 4]\nys: list[bool, 4]\n"
     "for i, v in enumerate(xs):\n    print(v)\n"
     "for j, v in enumerate(ys):\n    print(v)\n",
     "LITHON-E0122"),
    ("while/else dropped eagerly (E0118)",
     "x: int[64] = 0\nwhile x < 3:\n    x = x + 1\nelse:\n    print(x)\n",
     "LITHON-E0118"),
    ("pointer without the underscore prefix (E0122)",
     "p: ptr[int[64]]\nprint(0)\n", "LITHON-E0122"),
    ("dict key must be a literal (E0123)",
     "d: dict[int[64], int[64], 8] = {x: 1}\nprint(0)\n", "LITHON-E0123"),
    ("tuple literal on a plain assignment (E0123)",
     "t: tuple[int[64], 2]\nt = (1, 2)\nprint(0)\n",      "LITHON-E0123"),
    ("str key in an int-keyed dict (E0123)",
     "d: dict[int[64], int[64], 8] = {\"a\": 1}\nprint(0)\n", "LITHON-E0123"),
    ("non-str key in a str-keyed dict (E0123)",
     "d: dict[str[16], int[64], 8] = {1: 1}\nprint(0)\n",  "LITHON-E0123"),
    ("str key too long for the declared key type (E0123)",
     "d: dict[str[2], int[64], 8] = {\"abc\": 1}\nprint(0)\n", "LITHON-E0123"),
    ("duplicate str key in a dict literal (E0123)",
     "d: dict[str[16], int[64], 4] = {\"a\": 1, \"a\": 2}\nprint(0)\n", "LITHON-E0123"),
    ("dict-valued dict value is refused (E0112)",
     "d: dict[int[64], dict[int[64], int[64], 4], 4]\nprint(0)\n", "LITHON-E0112"),
    ("ptr-valued dict value is refused (E0112)",
     "d: dict[int[64], ptr[int[64]], 4]\nprint(0)\n", "LITHON-E0112"),
    ("range(a,b,0) literal step is E0107",
     "for i in range(5, 1, 0):\n    print(i)\n", "LITHON-E0107"),
    ("a class with an unknown base (E0126)",
     "class Foo(Bar):\n    x: int[64]\nprint(0)\n", "LITHON-E0126"),
    ("struct body with a non-annotation statement (E0126)",
     "class Foo(struct):\n    x = 1\nprint(0)\n", "LITHON-E0126"),
    ("container field in a struct (E0127)",
     "class Foo(struct):\n    xs: list[int[64], 4]\nf: Foo\nprint(0)\n",
     "LITHON-E0127"),
    ("unknown struct field type (E0127)",
     "class Foo(struct):\n    z: blah\nf: Foo\nprint(0)\n", "LITHON-E0127"),
    ("duplicate struct field (E0127)",
     "class Foo(struct):\n    x: int[64]\n    x: int[32]\nf: Foo\nprint(0)\n",
     "LITHON-E0127"),
    ("whole struct used as a value (E0128)",
     "class Foo(struct):\n    x: int[64]\nf: Foo\nprint(f)\n", "LITHON-E0128"),
    ("unknown field access on a struct (E0128)",
     "class Foo(struct):\n    x: int[64]\nf: Foo\nf.y = 1\n", "LITHON-E0128"),
    ("a class without the `enum` base (E0126)",
     "class E(Bar):\n    A\nprint(0)\n", "LITHON-E0126"),
    ("enum member with a value (E0129)",
     "class E(enum):\n    A = 1\nprint(0)\n", "LITHON-E0129"),
    ("duplicate enum variant (E0129)",
     "class E(enum):\n    A\n    A\nprint(0)\n", "LITHON-E0129"),
    ("unknown enum variant (E0130)",
     "class E(enum):\n    A\ne: E\ne = E.B\n", "LITHON-E0130"),
    ("unit variant constructed with a payload (E0130)",
     "class E(enum):\n    A\n    B: int[32]\ne: E\ne = E.A(1)\n", "LITHON-E0130"),
    ("enum used where a value is needed (E0131)",
     "class E(enum):\n    A\ne: E\nprint(e)\n", "LITHON-E0131"),
    ("non-exhaustive enum match (E0131)",
     "class E(enum):\n    A\n    B: int[32]\ne: E\nmatch e:\n    case E.A:\n        print(0)\n",
     "LITHON-E0131"),
    ("enum match guard (E0131)",
     "class E(enum):\n    A\ne: E\nmatch e:\n    case E.A if True:\n        print(0)\n",
     "LITHON-E0131"),
    ("type function body with two types (E0132)",
     "def T(a) -> newtype:\n    return {a, a}\nprint(0)\n", "LITHON-E0132"),
    ("type function body is an empty dict (E0132)",
     "def T() -> newtype:\n    return {}\nprint(0)\n", "LITHON-E0132"),
    ("type function value parameter given a type (E0132)",
     "def T(n: int[16]) -> newtype:\n    return {list[int[64], n]}\nx: T(int[64]) = 1\nprint(0)\n",
     "LITHON-E0132"),
    ("type function name collides with a struct (E0132)",
     "def T(a) -> newtype:\n    return {a}\nclass T(struct):\n    x: int[64]\nprint(0)\n",
     "LITHON-E0132"),
    ("application of an undeclared type function (E0133)",
     "x: T(int[64]) = 1\nprint(0)\n", "LITHON-E0133"),
    ("type function arity mismatch (E0133)",
     "def P(a, b) -> newtype:\n    return {list[a, b]}\nx: P(int[64]) = 1\nprint(0)\n",
     "LITHON-E0133"),
    ("recursive type function (E0133)",
     "def P(a) -> newtype:\n    return {P(a)}\nx: P(int[64]) = 1\nprint(0)\n",
     "LITHON-E0133"),
    ("type function body names a struct (E0132)",
     "class S(struct):\n    v: int[64]\ndef P() -> newtype:\n    return {S}\ns: P()\n",
     "LITHON-E0132"),
    ("method with a decorator (E0134)",
     "class P(struct):\n    x: int[64]\n    @staticmethod\n    def f(self) -> int[64]:\n"
     "        return 1\np: P\nprint(p.f())\n", "LITHON-E0134"),
    ("method whose first parameter is not `self` (E0134)",
     "class P(struct):\n    x: int[64]\n    def f(a: int[64]) -> int[64]:\n"
     "        return a\np: P\nprint(p.f(1))\n", "LITHON-E0134"),
    ("method with an annotated `self` (E0134)",
     "class P(struct):\n    x: int[64]\n    def f(self: int[64]) -> int[64]:\n"
     "        return 1\np: P\nprint(p.f())\n", "LITHON-E0134"),
    ("method with an unannotated parameter (E0134)",
     "class P(struct):\n    x: int[64]\n    def f(self, a) -> int[64]:\n"
     "        return a\np: P\nprint(p.f(1))\n", "LITHON-E0134"),
    ("method with a container parameter (E0134)",
     "class P(struct):\n    x: int[64]\n    def f(self, a: list[int[64], 4]) -> int[64]:\n"
     "        return 1\np: P\nprint(p.f(1))\n", "LITHON-E0134"),
    ("method with a default argument (E0134)",
     "class P(struct):\n    x: int[64]\n    def f(self, a: int[64] = 3) -> int[64]:\n"
     "        return a\np: P\nprint(p.f())\n", "LITHON-E0134"),
    ("method name colliding with a field (E0134)",
     "class P(struct):\n    x: int[64]\n    def x(self) -> int[64]:\n"
     "        return 1\np: P\nprint(p.x())\n", "LITHON-E0134"),
    ("call to an unknown method (E0135)",
     "class P(struct):\n    x: int[64]\np: P\nprint(p.h())\n", "LITHON-E0135"),
    ("a field called as a method (E0135)",
     "class P(struct):\n    x: int[64]\np: P\nprint(p.x())\n", "LITHON-E0135"),
    ("method call with a keyword argument (E0135)",
     "class P(struct):\n    x: int[64]\n    def f(self, a: int[64]) -> int[64]:\n"
     "        return a\np: P\nprint(p.f(a=1))\n", "LITHON-E0135"),
    ("method call arity mismatch (E0135)",
     "class P(struct):\n    x: int[64]\n    def f(self, a: int[64]) -> int[64]:\n"
     "        return a\np: P\nprint(p.f(1, 2))\n", "LITHON-E0135"),
    ("recursive method (E0135)",
     "class P(struct):\n    x: int[64]\n    def f(self) -> int[64]:\n"
     "        return self.f()\np: P\nprint(p.f())\n", "LITHON-E0135"),
    ("method with no result used as a value (E0135)",
     "class P(struct):\n    x: int[64]\n    def f(self):\n        return\n"
     "p: P\nprint(p.f())\n", "LITHON-E0135"),
    # 4.10.1. Classes. A bare class name is not a value, an instance has exactly
    # its declared attributes, an uninitialized attribute is an error, and a
    # class body may not carry a decorator (property/classmethod are keywords in
    # 4.10.6, not decorators).
    ("bare class name in a value position (E0140)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "print(P)\n", "LITHON-E0140"),
    ("read of an undeclared class attribute (E0141)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "p: P = P(1)\nprint(p.y)\n", "LITHON-E0141"),
    ("write of an undeclared class attribute (E0141)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "p: P = P(1)\np.y = 2\n", "LITHON-E0141"),
    ("an attribute left uninitialized by __init__ (E0140)",
     "class P:\n    x: int[64]\n    y: int[64]\n    def __init__(self, x: int[64]):\n"
     "        self.x = x\np: P = P(1)\nprint(p.x)\n", "LITHON-E0140"),
    ("a class method with a decorator (E0134)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    @property\n    def y(self) -> int[64]:\n        return self.x\n"
     "p: P = P(1)\nprint(p.y)\n", "LITHON-E0134"),
    # 4.10.2. Single inheritance. The base must be declared earlier, a subclass
    # adds attributes rather than shadowing them, an override keeps the slot's
    # signature, an abstract class cannot be instantiated, and super() has a
    # base to start from and the base's own constructor arity.
    ("a duplicate base (E0126)",
     "class A:\n    x: int[64]\nclass C(A, A):\n    y: int[64]\nprint(0)\n",
     "LITHON-E0126"),
    ("a subclass declared before its base (E0126)",
     "class B(A):\n    x: int[64]\nclass A:\n    x: int[64]\nprint(0)\n",
     "LITHON-E0126"),
    ("a subclass re-declaring an inherited attribute (E0127)",
     "class A:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "class B(A):\n    x: int[64]\n    def __init__(self, x: int[64]):\n"
     "        super().__init__(x)\n        self.x = x\nb: B = B(1)\nprint(b.x)\n",
     "LITHON-E0127"),
    ("an override with a different signature (E0135)",
     "class A:\n    def f(self, x: int[64]) -> int[64]:\n        return x\n"
     "class B(A):\n    def f(self) -> int[64]:\n        return 1\n"
     "b: B = B()\nprint(b.f())\n", "LITHON-E0135"),
    ("instantiating an abstract class (E0140)",
     "class A:\n    def f(self) -> int[64]: ...\na: A = A()\nprint(a.f())\n",
     "LITHON-E0140"),
    ("super() in a class with no base (E0135)",
     "class A:\n    def f(self) -> int[64]:\n        return super().f()\n"
     "a: A = A()\nprint(a.f())\n", "LITHON-E0135"),
    ("super() outside a method (E0135)",
     "def f() -> int[64]:\n    return super().f()\nprint(f())\n", "LITHON-E0135"),
    ("super().__init__ with the wrong arity (E0135)",
     "class A:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "class B(A):\n    def __init__(self):\n        super().__init__()\n"
     "b: B = B()\nprint(b.x)\n", "LITHON-E0135"),
    ("assigning an unrelated class reference (E0140)",
     "class A:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "class B:\n    y: int[64]\n    def __init__(self, y: int[64]):\n        self.y = y\n"
     "a: A = B(1)\nprint(a.x)\n", "LITHON-E0140"),
    ("isinstance on a value that is not a class reference (E0140)",
     "class A:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "n: int[64] = 1\nprint(isinstance(n, A))\n", "LITHON-E0140"),
    ("isinstance with a non-class second argument (E0135)",
     "class A:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "a: A = A(1)\nprint(isinstance(a, 3))\n", "LITHON-E0135"),
    ("identity compare between unrelated classes (E0140)",
     "class A:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "class B:\n    y: int[64]\n    def __init__(self, y: int[64]):\n        self.y = y\n"
     "a: A = A(1)\nb: B = B(2)\nprint(a is b)\n", "LITHON-E0140"),
    # 4.10.4. Multiple inheritance. C3 must produce one consistent order, every
    # base must be a real class with a prefix-compatible attribute layout, and a
    # slot inherited from two bases must have ONE signature. A dynamic super()
    # may only run when every concrete subclass has a later implementation.
    ("multiple bases with an inconsistent C3 linearization (E0126)",
     "class A:\n    def f(self) -> int[64]:\n        return 1\n"
     "class B(A):\n    def b(self) -> int[64]:\n        return 2\n"
     "class C(A):\n    def c(self) -> int[64]:\n        return 3\n"
     "class X(B, C):\n    def x(self) -> int[64]:\n        return 4\n"
     "class Y(C, B):\n    def y(self) -> int[64]:\n        return 5\n"
     "class Z(X, Y):\n    def z(self) -> int[64]:\n        return 6\nprint(0)\n",
     "LITHON-E0126"),
    ("a struct used as a class base (E0126)",
     "class S(struct):\n    x: int[64]\n"
     "class A:\n    def f(self) -> int[64]:\n        return 1\n"
     "class D(A, S):\n    def g(self) -> int[64]:\n        return 2\nprint(0)\n",
     "LITHON-E0126"),
    ("an enum used as a class base (E0126)",
     "class E(enum):\n    A\n"
     "class A:\n    def f(self) -> int[64]:\n        return 1\n"
     "class D(A, E):\n    def g(self) -> int[64]:\n        return 2\nprint(0)\n",
     "LITHON-E0126"),
    ("multiple bases with conflicting attribute layouts (E0127)",
     "class A:\n    x: int[64]\n"
     "    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    def f(self) -> int[64]:\n        return 1\n"
     "class B:\n    y: int[64]\n"
     "    def __init__(self, y: int[64]):\n        self.y = y\n"
     "    def f(self) -> int[64]:\n        return 2\n"
     "class D(A, B):\n    def g(self) -> int[64]:\n        return 3\nprint(0)\n",
     "LITHON-E0127"),
    ("multiple bases with incompatible inherited signatures (E0135)",
     "class A:\n    def f(self, x: int[64]) -> int[64]:\n        return x\n"
     "class B:\n    def f(self) -> int[64]:\n        return 1\n"
     "class D(A, B):\n    def g(self) -> int[64]:\n        return 2\nprint(0)\n",
     "LITHON-E0135"),
    ("dynamic super() with no later implementation for every concrete subclass (E0135)",
     "class B:\n    def f(self) -> int[64]:\n        return super().f() + 1\n"
     "class C:\n    def f(self) -> int[64]:\n        return 2\n"
     "class D(B, C):\n    def g(self) -> int[64]:\n        return 3\nprint(0)\n",
     "LITHON-E0135"),
    # 4.10.5. Special methods are a deliberately small typed surface:
    # comparison dunders take one class reference and return bool, while
    # __str__/__repr__ may only return one literal/f-string expression that
    # print() can inline (a bounded string has no value form). Unsupported
    # dunders, missing ordering methods, objects without text, and explicit
    # calls to a text method are all refused with a code.
    ("an unsupported special method (E0134)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    def __add__(self, other: P) -> P:\n        return self\n"
     "p: P = P(1)\nprint(p.x)\n", "LITHON-E0134"),
    ("an equality method with the wrong arity (E0134)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    def __eq__(self) -> bool:\n        return True\n"
     "p: P = P(1)\nprint(p.x)\n", "LITHON-E0134"),
    ("an equality method that does not return bool (E0134)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    def __eq__(self, other: P) -> int[64]:\n        return self.x\n"
     "p: P = P(1)\nprint(p.x)\n", "LITHON-E0134"),
    ("a text method that does not return a bounded string (E0134)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    def __str__(self) -> int[64]:\n        return self.x\n"
     "p: P = P(1)\nprint(p.x)\n", "LITHON-E0134"),
    ("a text method body that is not one return expression (E0134)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    def __str__(self) -> str[32]:\n        s = \"x\"\n        return s\n"
     "p: P = P(1)\nprint(p.x)\n", "LITHON-E0134"),
    ("a text method f-string field that is not a scalar self attribute (E0134)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    def __str__(self) -> str[32]:\n        return f\"{self}\"\n"
     "p: P = P(1)\nprint(p.x)\n", "LITHON-E0134"),
    ("printing an object with neither __str__ nor __repr__ (E0135)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "p: P = P(1)\nprint(p)\n", "LITHON-E0135"),
    ("ordering a class without the matching special method (E0135)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "p: P = P(1)\nq: P = P(2)\nprint(p < q)\n", "LITHON-E0135"),
    ("calling __str__ as a value (E0135)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    def __str__(self) -> str[32]:\n        return \"p\"\n"
     "p: P = P(1)\nprint(p.__str__())\n", "LITHON-E0135"),
    ("a text method literal longer than its declared result (E0111)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    def __str__(self) -> str[4]:\n        return \"abcdef\"\n"
     "p: P = P(1)\nprint(p)\n", "LITHON-E0111"),
    # 4.10.3. Class references as list/tuple elements. Every slot must be filled
    # by the literal (a null reference is never a value), an element slot may be
    # overwritten only with a related class reference, append has no room, and
    # the ownership of a NAME bound to an element across iterations is not
    # settled, so enumerate/zip over these containers is refused too. A
    # class-typed container is local storage, not a value that crosses a
    # function header.
    ("a bare class-typed list declaration (E0140)",
     "class T:\n    v: int[64]\n    def __init__(self, v: int[64]):\n        self.v = v\n"
     "xs: list[T, 2]\nprint(0)\n", "LITHON-E0140"),
    ("a bare class-typed tuple declaration (E0140)",
     "class T:\n    v: int[64]\n    def __init__(self, v: int[64]):\n        self.v = v\n"
     "ts: tuple[T, 2]\nprint(0)\n", "LITHON-E0140"),
    ("a class-typed list literal shorter than its capacity (E0123)",
     "class T:\n    v: int[64]\n    def __init__(self, v: int[64]):\n        self.v = v\n"
     "xs: list[T, 2] = [T(1)]\nprint(0)\n", "LITHON-E0123"),
    ("list.append on a class-typed list (E0140)",
     "class T:\n    v: int[64]\n    def __init__(self, v: int[64]):\n        self.v = v\n"
     "xs: list[T, 2] = [T(1), T(2)]\nxs.append(T(3))\n", "LITHON-E0140"),
    ("a non-class value searched in a class-typed list (E0140)",
     "class T:\n    v: int[64]\n    def __init__(self, v: int[64]):\n        self.v = v\n"
     "xs: list[T, 2] = [T(1), T(2)]\nprint(1 in xs)\n", "LITHON-E0140"),
    ("an unrelated class searched in a class-typed list (E0140)",
     "class A:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "class B:\n    y: int[64]\n    def __init__(self, y: int[64]):\n        self.y = y\n"
     "xs: list[A, 2] = [A(1), A(2)]\nb: B = B(1)\nprint(b in xs)\n", "LITHON-E0140"),
    ("enumerate over a class-typed list (E0119)",
     "class T:\n    v: int[64]\n    def __init__(self, v: int[64]):\n        self.v = v\n"
     "xs: list[T, 2] = [T(1), T(2)]\nfor i, x in enumerate(xs):\n    print(x.v)\n",
     "LITHON-E0119"),
    ("zip over a class-typed list (E0119)",
     "class T:\n    v: int[64]\n    def __init__(self, v: int[64]):\n        self.v = v\n"
     "xs: list[T, 2] = [T(1), T(2)]\nys: list[T, 2] = [T(3), T(4)]\n"
     "for a, b in zip(xs, ys):\n    print(a.v + b.v)\n", "LITHON-E0119"),
    ("a class-typed list as a parameter (E0124)",
     "class T:\n    v: int[64]\n    def __init__(self, v: int[64]):\n        self.v = v\n"
     "def f(xs: list[T, 2]) -> int[64]:\n    return xs[0].v\nprint(0)\n",
     "LITHON-E0124"),
    ("a class-typed list as a return type (E0124)",
     "class T:\n    v: int[64]\n    def __init__(self, v: int[64]):\n        self.v = v\n"
     "def f() -> list[T, 2]:\n    return 0\nprint(0)\n", "LITHON-E0124"),
    ("a non-class value stored into a class-typed element (E0140)",
     "class T:\n    v: int[64]\n    def __init__(self, v: int[64]):\n        self.v = v\n"
     "xs: list[T, 2] = [T(1), T(2)]\nxs[0] = 1\n", "LITHON-E0140"),
    ("an unrelated class stored into a class-typed element (E0140)",
     "class A:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "class B:\n    y: int[64]\n    def __init__(self, y: int[64]):\n        self.y = y\n"
     "xs: list[A, 2] = [A(1), A(2)]\nb: B = B(1)\nxs[0] = b\n", "LITHON-E0140"),
    # 6.2. Bounded strings. A str[N] is N bytes, so a literal of L bytes needs
    # L < N (the last byte is the NUL terminator), the capacity must be a
    # positive literal, and the value must be a string literal -- there is no
    # str value to copy or compute in the language.
    ("str literal does not fit its capacity (E0111)",
     "s: str[2] = \"abc\"\nprint(s)\n", "LITHON-E0111"),
    ("str capacity must be positive (E0111)",
     "s: str[0]\nprint(0)\n", "LITHON-E0111"),
    ("str value must be a string literal (E0114)",
     "t: str[4] = \"hi\"\ns: str[4] = t\nprint(s)\n", "LITHON-E0114"),
    # 6.2. The same rules when the string is a struct FIELD or a pointer's
    # pointee. A field's companion run is keyed by the dotted variable path, so
    # a whole-str field read is valueless (E0114) and reading one through a
    # pointer into the struct has no companion name (E0128). A pointer's
    # pointee is a place, not a value, so valueof(_p) used as one is E0114.
    ("str field literal does not fit its capacity (E0111)",
     "class C(struct):\n    a: str[2]\n\nc: C\nc.a = \"abc\"\nprint(c.a)\n", "LITHON-E0111"),
    ("whole str field used as a value (E0114)",
     "class C(struct):\n    a: str[4]\n\nc: C\nx: str[4] = c.a\nprint(x)\n", "LITHON-E0114"),
    ("str field read through a pointer into the struct (E0128)",
     "class C(struct):\n    a: str[4]\n\np: C\n_pp: ptr[C] = addressof(p)\n"
     "print(valueof(_pp).a)\n", "LITHON-E0128"),
    ("whole str pointee used as a value (E0114)",
     "s: str[4] = \"hi\"\n_p: ptr[str[4]] = addressof(s)\n"
     "x: int[64] = valueof(_p)\nprint(x)\n", "LITHON-E0114"),
    # 6.3 Stage A. An f-string chunk is printed as bytes, so the only two
    # Python f-string features this slice does not implement are refused where
    # the source spells them, rather than silently dropped.
    ("f-string format specifier is not supported (E0114)",
     "x: int[32] = 3\nprint(f\"{x:.2f}\")\n", "LITHON-E0114"),
    ("f-string conversion is not supported (E0114)",
     "x: int[32] = 3\nprint(f\"{x!r}\")\n", "LITHON-E0114"),
    # 6.4. find/count need a string needle and exactly one argument; anything
    # else is named where the source spells it.
    ("str.find with a non-string needle (E0114)",
     "s: str[8] = \"hi\"\nx: int[32] = 1\nprint(s.find(x))\n", "LITHON-E0114"),
    ("str.count with the wrong number of arguments (E0114)",
     "s: str[8] = \"hi\"\nprint(s.count(\"h\", \"i\"))\n", "LITHON-E0114"),
    # 6.7. list.append: one value, a list receiver, a statement, and somewhere to
    # put the element. Each is refused where the source spells it.
    ("list.append with the wrong number of arguments (E0114)",
     "xs: list[int[64], 4] = [1, 2]\nxs.append(1, 2)\n", "LITHON-E0114"),
    ("list.append used as a value (E0135)",
     "xs: list[int[64], 4] = [1, 2]\ny: int[64] = xs.append(3)\n", "LITHON-E0135"),
    ("list.append onto a full list (E0111)",
     "xs: list[int[64], 2] = [1, 2]\nxs.append(3)\n", "LITHON-E0111"),
    # 6.7. A reduction or membership scan unrolls over a compile-time length, so
    # a list whose length is only known at run time has no fixed bound. The
    # bound is given up by an append inside a loop.
    ("sum over a run-time-length list (E0115)",
     "xs: list[int[64], 5] = [1, 2]\nfor i in range(2):\n    xs.append(i)\n"
     "print(sum(xs))\n", "LITHON-E0115"),
    ("in over a run-time-length list (E0114)",
     "xs: list[int[64], 5] = [1, 2]\nfor i in range(2):\n    xs.append(i)\n"
     "print(3 in xs)\n", "LITHON-E0114"),
    ("enumerate over a run-time-length list (E0119)",
     "xs: list[int[64], 5] = [1, 2]\nfor i in range(2):\n    xs.append(i)\n"
     "for j, v in enumerate(xs):\n    print(v)\n", "LITHON-E0119"),
    # 6.7. str.split: a statement-shaped call with a string source, a NON-EMPTY
    # string separator, and a destination that is a list of bounded strings.
    # Each of those is refused where the source spells it.
    ("str.split with an empty separator (E0114)",
     "s: str[8] = \"a,b\"\np: list[str[4], 2] = s.split(\"\")\n", "LITHON-E0114"),
    ("str.split with no separator (E0114)",
     "s: str[8] = \"a,b\"\np: list[str[4], 2] = s.split()\n", "LITHON-E0114"),
    ("str.split with a non-string separator (E0114)",
     "s: str[8] = \"a,b\"\np: list[str[4], 2] = s.split(3)\n", "LITHON-E0114"),
    ("str.split into a list that cannot hold strings (E0114)",
     "s: str[8] = \"a,b\"\np: list[int[64], 2] = s.split(\",\")\n", "LITHON-E0114"),
    ("sum over a split result's run-time length (E0115)",
     "s: str[16] = \"a,b,c\"\np: list[str[4], 4] = s.split(\",\")\n"
     "print(sum(p))\n", "LITHON-E0115"),
    # 6.10. The try/except slice is deliberately closed: a bare `except:`, an
    # unnameable trap, an `as e` binding and a bare `try/finally` are each
    # refused where the source spells them, not silently accepted or dropped.
    ("try/except with a bare except (E0114)",
     "a: int[64] = 1\ntry:\n    b: int[64] = a % 0\nexcept:\n    print(\"x\")\n",
     "LITHON-E0114"),
    ("try/except naming a non-trap (E0114)",
     "a: int[64] = 1\ntry:\n    b: int[64] = a % 0\nexcept MyError:\n    print(\"x\")\n",
     "LITHON-E0114"),
    ("try/except with an `as e` binding (E0114)",
     "a: int[64] = 1\ntry:\n    b: int[64] = a % 0\nexcept ZeroDivisionError as e:\n    print(\"x\")\n",
     "LITHON-E0114"),
    ("try/finally with no except (E0114)",
     "a: int[64] = 1\ntry:\n    b: int[64] = a % 0\nfinally:\n    print(\"x\")\n",
     "LITHON-E0114"),
    ("raise (E0114)",
     "a: int[64] = 1\ntry:\n    b: int[64] = a % 0\nexcept ZeroDivisionError:\n    raise\n",
     "LITHON-E0114"),
]


def check_frontend_refusals(failures):
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for i, (name, source, needle) in enumerate(FRONTEND_REFUSALS):
            src = tmp / f"r{i}.lio"
            src.write_text(source)
            r = run(["python3", str(FRONTEND), str(src)])
            if r.returncode == 0:
                print(f"  FAIL refusal: {name}: accepted when it must refuse")
                failures.append(name)
            elif needle not in r.stderr:
                print(f"  FAIL refusal: {name}: refused without {needle!r}: "
                      f"{r.stderr.strip()[:160]}")
                failures.append(name)
            elif "Traceback" in r.stderr:
                print(f"  FAIL refusal: {name}: leaked a Python traceback "
                      f"instead of one clean line")
                failures.append(name)
            else:
                print(f"  ok   refusal: {name} ({needle})")


def check_e0105(failures):
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for name, where, body, needle in E0105_REJECTS:
            if where == "frontend":
                (tmp / "in.lio").write_text(body)
                r = run(["python3", str(FRONTEND), str(tmp / "in.lio")])
            else:
                ir = tmp / "in.ir"
                ir.write_text(body)
                r = run([str(TIER), str(ir), "--interp"])
            if r.returncode != 0 and needle in (r.stderr + r.stdout):
                print(f"  ok   reject: {name}")
            else:
                print(f"  FAIL reject: {name}: "
                      f"{'accepted' if r.returncode == 0 else 'refused without ' + needle!r}")
                failures.append(name)
        for i, body in enumerate(E0105_ACCEPTS):
            (tmp / "in.lio").write_text(body)
            r = run(["python3", str(FRONTEND), str(tmp / "in.lio")])
            if r.returncode != 0:
                print(f"  FAIL accept: case {i + 1} refused by frontend: "
                      f"{r.stderr.strip()[:160]}")
                failures.append(f"accept {i + 1}")
                continue
            ir = tmp / f"a{i}.ir"
            ir.write_text(r.stdout)
            r = run([str(TIER), str(ir), "--interp"])
            if r.returncode != 0:
                print(f"  FAIL accept: case {i + 1} rejected at typecheck: "
                      f"{(r.stderr + r.stdout).strip()[:160]}")
                failures.append(f"accept {i + 1}")
            else:
                print(f"  ok   accept: case {i + 1} ({body.splitlines()[0].strip()})")


def check_extension_gate(failures):
    """A Lithon program is `.lio`, and only `.lio`.

    The extension is the one marker that separates a Lithon source from an
    ordinary Python script, so the frontend refuses anything else outright.
    Both halves are checked: the SAME bytes are refused as `.py` and accepted
    as `.lio`, which is what proves the gate keys on the extension and not on
    the contents.
    """
    body = "x: int[64] = 7\nprint(x)\n"
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "prog.py").write_text(body)
        r = run(["python3", str(FRONTEND), str(tmp / "prog.py")])
        if r.returncode == 0:
            print("  FAIL extension gate: a .py source was accepted")
            failures.append("extension gate (.py accepted)")
        elif "LITHON-E0114" not in r.stderr:
            print(f"  FAIL extension gate: .py refused without E0114: "
                  f"{r.stderr.strip()[:160]}")
            failures.append("extension gate (no E0114)")
        else:
            print("  ok   extension gate: .py source refused (E0114)")
        (tmp / "prog.lio").write_text(body)
        r = run(["python3", str(FRONTEND), str(tmp / "prog.lio")])
        if r.returncode != 0:
            print(f"  FAIL extension gate: the same bytes as .lio were refused: "
                  f"{r.stderr.strip()[:160]}")
            failures.append("extension gate (.lio refused)")
        else:
            print("  ok   extension gate: the same bytes as .lio compile")


def main() -> int:
    missing = [p.name for p in (FRONTEND, TIER) if not p.exists()]
    if missing:
        print(f"missing: {', '.join(missing)} -- build first")
        return 1
    failures = []
    check_discipline(failures)
    check_e0105(failures)
    check_frontend_refusals(failures)
    check_extension_gate(failures)
    check_e0303(failures)
    check_e0401(failures)
    total = len(E0105_REJECTS) + len(E0105_ACCEPTS) + 1
    total += len(FRONTEND_REFUSALS)
    total += 2   # check_extension_gate: the .py refusal and the .lio accept
    total += len(E0303_REJECTS) + len(E0303_WRAPS) + len(E0303_TRAPS)
    total += len(E0401_ACCEPTS) + len(E0401_TRAPS)
    done = total - len(failures)
    if failures:
        print(f"\n{len(failures)} of {total} check(s) FAILED")
        return 1
    print(f"\nall {total} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())