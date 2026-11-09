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
    # 4.14. An unsigned result outside 0..2^w-1 traps E0313 in both tiers.
    ("uint[8] sum past 255 traps (source)",
     "a: uint[8] = 200\nb: uint[8] = 100\nr: uint[8] = a + b\nprint(r)\n",
     "LITHON-E0313: unsigned integer result is out of range for its uint width"),
    ("uint[8] difference below zero traps (source)",
     "a: uint[8] = 1\nb: uint[8] = 2\nr: uint[8] = a - b\nprint(r)\n",
     "LITHON-E0313: unsigned integer result is out of range for its uint width"),
    ("uint[64] sum past 2^64-1 traps (source)",
     "a: uint[64] = 18446744073709551615\nb: uint[64] = 1\nr: uint[64] = a + b\nprint(r)\n",
     "LITHON-E0313: unsigned integer result is out of range for its uint width"),
    ("uint[64] product past 2^64-1 traps (source)",
     "a: uint[64] = 4294967296\nb: uint[64] = 4294967296\nr: uint[64] = a * b\nprint(r)\n",
     "LITHON-E0313: unsigned integer result is out of range for its uint width"),
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
            # The same program is written under two file names (t<i> and t<i>j), and with source
            # positions on the trap prints "  --> <path>:<line>": compare the line, not the file.
            def _same_file(t):
                return re.sub(r"(--> ).*?t\d+j?\.lio(:\d+)", r"\1<file>\2", t)
            text_i = _same_file(out_i + "\n" + err_i)
            text_a = _same_file(out_a + "\n" + err_a)
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
            # The same program is written under two file names (t<i> and t<i>j), and with source
            # positions on the trap prints "  --> <path>:<line>": compare the line, not the file.
            def _same_file(t):
                return re.sub(r"(--> ).*?t\d+j?\.lio(:\d+)", r"\1<file>\2", t)
            text_i = _same_file(out_i + "\n" + err_i)
            text_a = _same_file(out_a + "\n" + err_a)
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
    # Intersect the whitepaper table with the two sibling shipped tables so a
    # code documented in only one place still fails the gate. This closes the
    # E0140/E0141 class of drift (whitepaper-only coverage used to pass).
    codes = {m for m in PLAIN_CODE_RE.findall(WHITEPAPER.read_text())}
    for sibling in (
        ROOT / "docs" / "lithon_error_system.md",
        ROOT / "web" / "apps" / "web" / "src" / "docs" / "pages" / "reference.ts",
    ):
        if sibling.exists():
            codes &= {m for m in PLAIN_CODE_RE.findall(sibling.read_text())}
    return codes


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
    # Reserved-but-never-emitted codes must be named as reservations.
    es = (ROOT / "docs" / "lithon_error_system.md").read_text() if (ROOT / "docs" / "lithon_error_system.md").exists() else ""
    for reserved in ("E0136", "E0137", "E0138", "E0139"):
        if reserved not in es:
            print(f"  FAIL discipline: reserved {reserved} not named in docs/lithon_error_system.md")
            failures.append("reserved code unnamed")
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
    # 4.11. An enumerate/zip target is the user's own declared variable, and it
    # must have exactly the element type it receives; a name keeps one type.
    ("an enumerate element target declared with the wrong type (E0119)",
     "xs: list[int[64], 4]\nys: list[bool, 4]\ni: int[64] = 0\nv: int[64] = 0\n"
     "j: int[64] = 0\n"
     "for i, v in enumerate(xs):\n    print(v)\n"
     "for j, v in enumerate(ys):\n    print(v)\n",
     "LITHON-E0119"),
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
     "i: int[64] = 0\nfor i in range(5, 1, 0):\n    print(i)\n", "LITHON-E0107"),
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
     "    @staticmethod\n    def y(self) -> int[64]:\n        return self.x\n"
     "p: P = P(1)\nprint(p.y)\n", "LITHON-E0134"),
    # 4.10.6. @property and @classmethod are the only recognised decorators.
    # A property is a zero-argument, read-only computed attribute; a
    # classmethod has `cls`, is called on the class name, and is not an
    # instance method. Everything outside that small surface is refused.
    ("a property with a parameter (E0134)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    @property\n    def y(self, a: int[64]) -> int[64]:\n        return self.x\n"
     "p: P = P(1)\nprint(p.y)\n", "LITHON-E0134"),
    ("a property without a result type (E0134)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    @property\n    def y(self):\n        return self.x\n"
     "p: P = P(1)\nprint(p.y)\n", "LITHON-E0134"),
    ("a classmethod whose first parameter is not cls (E0134)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    @classmethod\n    def y(self) -> int[64]:\n        return self.x\n"
     "p: P = P(1)\nprint(p.x)\n", "LITHON-E0134"),
    ("calling a classmethod on an instance (E0135)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    @classmethod\n    def make(cls) -> P:\n        return cls(0)\n"
     "p: P = P(1)\nq: P = p.make()\n", "LITHON-E0135"),
    ("calling an instance method on the class name (E0135)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    def get(self) -> int[64]:\n        return self.x\n"
     "z: int[64] = P.get()\n", "LITHON-E0135"),
    ("calling a property as a method (E0135)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    @property\n    def y(self) -> int[64]:\n        return self.x\n"
     "p: P = P(1)\nprint(p.y())\n", "LITHON-E0135"),
    ("assigning to a read-only property (E0135)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    @property\n    def y(self) -> int[64]:\n        return self.x\n"
     "p: P = P(1)\np.y = 2\n", "LITHON-E0135"),
    ("calling a classmethod with the wrong arity (E0135)",
     "class P:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "    @classmethod\n    def make(cls, x: int[64]) -> P:\n        return cls(x)\n"
     "p: P = P.make()\n", "LITHON-E0135"),
    ("a classmethod and an instance method sharing an inherited name (E0135)",
     "class A:\n    def f(self) -> int[64]:\n        return 1\n"
     "class B:\n    @classmethod\n    def f(cls) -> int[64]:\n        return 2\n"
     "class C(A, B):\n    x: int[64]\nprint(0)\n", "LITHON-E0135"),
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
    ("a str built from a computed expression (E0114)",
     "t: str[4] = \"hi\"\ns: str[8] = t + t\nprint(s)\n", "LITHON-E0114"),
    ("a str never narrows: a wider str into a narrower one (E0111)",
     "t: str[8] = \"hi\"\ns: str[4] = t\nprint(s)\n", "LITHON-E0111"),
    ("a str assigned from a wider str (E0111)",
     "t: str[8] = \"hi\"\ns: str[4] = \"x\"\ns = t\nprint(s)\n", "LITHON-E0111"),
    # 6.11. A function that returns a str is inlined at its call sites, so it takes scalar
    # parameters only, cannot recurse, and its result must fit the declared capacity at the
    # return and at the receiving variable.
    ("a str-returning function that recurses (E0114)",
     "def f(n: int[16]) -> str[8]:\n    if n > 0:\n        return f(n - 1)\n    return \"a\"\nprint(f(2))\n",
     "LITHON-E0114"),
    ("a str-returning function's literal longer than its capacity (E0111)",
     "def f() -> str[3]:\n    return \"abcd\"\nprint(f())\n", "LITHON-E0111"),
    ("a str-returning function returns a wider str variable (E0111)",
     "def f() -> str[4]:\n    r: str[8] = \"a\"\n    return r\nprint(f())\n", "LITHON-E0111"),
    ("a str-returning function with a container parameter (E0124)",
     "def f(xs: list[int[64], 2]) -> str[4]:\n    return \"a\"\nprint(0)\n", "LITHON-E0124"),
    ("a function with a str parameter that returns a class (E0124)",
     "class C:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
     "def f(s: str[4]) -> C:\n    return C(1)\nprint(0)\n", "LITHON-E0124"),
    ("a str parameter that is recursive (E0114)",
     "def f(s: str[4], n: int[16]) -> int[64]:\n    if n > 0:\n        return f(s, n - 1)\n    return 0\nprint(f(\"a\", 2))\n",
     "LITHON-E0114"),
    ("a str argument wider than the parameter (E0111)",
     "def f(s: str[4]) -> int[64]:\n    return len(s)\nt: str[8] = \"hi\"\nprint(f(t))\n", "LITHON-E0111"),
    ("a function with a str parameter that is never called is still checked (E0114)",
     "def g(s: str[4]) -> int[64]:\n    x: int[64] = \"no\"\n    return 0\nprint(1)\n", "LITHON-E0114"),
    ("a str result used as a scalar (E0114)",
     "def f() -> str[4]:\n    return \"a\"\nx: int[64] = f()\n", "LITHON-E0114"),
    ("a str result stored into a narrower str (E0111)",
     "def f() -> str[8]:\n    return \"a\"\ns: str[4] = f()\nprint(s)\n", "LITHON-E0111"),
    ("a str-returning function called with the wrong arity (E0114)",
     "def f(n: int[16]) -> str[4]:\n    return \"a\"\nprint(f(1, 2))\n", "LITHON-E0114"),
    # 6.2. The same rules when the string is a struct FIELD or a pointer's
    # pointee. A field's companion run is keyed by the dotted variable path, so
    # a whole-str field read is valueless (E0114) and reading one through a
    # pointer into the struct has no companion name (E0128). A pointer's
    # pointee is a place, not a value, so valueof(_p) used as one is E0114.
    ("str field literal does not fit its capacity (E0111)",
     "class C(struct):\n    a: str[2]\n\nc: C\nc.a = \"abc\"\nprint(c.a)\n", "LITHON-E0111"),
    ("a whole str field into a narrower str (E0111)",
     "class C(struct):\n    a: str[8]\n\nc: C\nx: str[4] = c.a\nprint(x)\n", "LITHON-E0111"),
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
     "xs: list[int[64], 5] = [1, 2]\ni: int[64] = 0\nfor i in range(2):\n    xs.append(i)\n"
     "print(sum(xs))\n", "LITHON-E0115"),
    ("in over a run-time-length list (E0114)",
     "xs: list[int[64], 5] = [1, 2]\ni: int[64] = 0\nfor i in range(2):\n    xs.append(i)\n"
     "print(3 in xs)\n", "LITHON-E0114"),
    ("enumerate over a run-time-length list (E0119)",
     "xs: list[int[64], 5] = [1, 2]\ni: int[64] = 0\nfor i in range(2):\n    xs.append(i)\n"
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
    # 4.10.7. Optional[P] is the one nullable class reference. Its argument must
    # be a class, it cannot nest, and containers of Optional references are
    # deliberately deferred. None is not a general zero: only an Optional class
    # target may consume it, and an Optional needs a proof before an object
    # operation. Every case is a coded E0142 refusal.
    ("Optional of a non-class type (E0142)",
     "x: Optional[int[64]] = None\nprint(0)\n", "LITHON-E0142"),
    ("nested Optional (E0142)",
     "class P:\n    x: int[64]\np: Optional[Optional[P]] = None\nprint(0)\n",
     "LITHON-E0142"),
    ("Optional class element in a list (E0142)",
     "class P:\n    x: int[64]\nxs: list[Optional[P], 2]\nprint(0)\n",
     "LITHON-E0142"),
    ("Optional class value in a dict (E0142)",
     "class P:\n    x: int[64]\nd: dict[int[64], Optional[P], 2]\nprint(0)\n",
     "LITHON-E0142"),
    ("None stored in a non-optional class variable (E0142)",
     "class P:\n    x: int[64]\np: P = None\nprint(0)\n", "LITHON-E0142"),
    ("None stored in a non-optional class attribute (E0142)",
     "class P:\n    x: int[64]\n    def __init__(self):\n        self.x = None\n"
     "p: P = P()\nprint(p.x)\n", "LITHON-E0142"),
    ("None stored in a scalar declaration (E0142)",
     "x: int[64] = None\nprint(x)\n", "LITHON-E0142"),
    ("None printed as a value (E0142)",
     "print(None)\n", "LITHON-E0142"),
    ("None used as a container index (E0142)",
     "xs: list[int[64], 2] = [1, 2]\nprint(xs[None])\n", "LITHON-E0142"),
    ("None appended to a scalar list (E0142)",
     "xs: list[int[64], 2] = [1]\nxs.append(None)\n", "LITHON-E0142"),
    ("unchecked Optional used as a receiver (E0142)",
     "class P:\n    x: int[64]\np: Optional[P] = None\nprint(p.x)\n",
     "LITHON-E0142"),
    ("Optional copied into a non-optional class variable (E0142)",
     "class P:\n    x: int[64]\na: Optional[P] = None\nb: P = a\nprint(0)\n",
     "LITHON-E0142"),
    ("None passed to a non-optional free parameter (E0142)",
     "class P:\n    x: int[64]\ndef f(p: P) -> int[64]:\n    return p.x\n"
     "print(f(None))\n", "LITHON-E0142"),
    ("unchecked Optional passed to a non-optional free parameter (E0142)",
     "class P:\n    x: int[64]\ndef f(p: P) -> int[64]:\n    return p.x\n"
     "a: Optional[P] = None\nprint(f(a))\n", "LITHON-E0142"),
    ("None returned from a non-optional function (E0142)",
     "class P:\n    x: int[64]\ndef f() -> P:\n    return None\nprint(0)\n",
     "LITHON-E0142"),
    ("None returned from a non-optional method (E0142)",
     "class P:\n    x: int[64]\n    def __init__(self):\n        self.x = 1\n"
     "    def f(self) -> P:\n        return None\np: P = P()\nprint(p.x)\n",
     "LITHON-E0142"),
    ("Optional compared with == None (E0142)",
     "class P:\n    x: int[64]\np: Optional[P] = None\nif p == None:\n    print(1)\n",
     "LITHON-E0142"),
    ("None in an enum scalar payload (E0142)",
     "class Shape(enum):\n    Empty\n    Line: int[64]\n"
     "s: Shape\ns = Shape.Line(None)\nprint(0)\n", "LITHON-E0142"),
    ("None in an enum struct payload field (E0142)",
     "class Vec(struct):\n    a: int[64]\n    b: int[64]\n"
     "class Shape(enum):\n    Empty\n    Pair: Vec\n"
     "s: Shape\ns = Shape.Pair(a=None, b=2)\nprint(0)\n", "LITHON-E0142"),
    ("None in an augmented assignment (E0142)",
     "x: int[64] = 1\nx += None\nprint(x)\n", "LITHON-E0142"),
    ("enumerate index that is not declared (E0119)",
     "xs: list[int[64], 4]\nv: int[64] = 0\nfor i, v in enumerate(xs):\n    print(v)\n",
     "LITHON-E0119"),
    ("enumerate element that is not declared (E0119)",
     "xs: list[int[64], 4]\ni: int[64] = 0\nfor i, v in enumerate(xs):\n    print(v)\n",
     "LITHON-E0119"),
    ("zip target that is not declared (E0119)",
     "xs: list[int[64], 4]\nys: list[int[64], 4]\na: int[64] = 0\n"
     "for a, b in zip(xs, ys):\n    print(a)\n", "LITHON-E0119"),
    ("assignment to a live range counter (E0119)",
     "i: int[64] = 0\nfor i in range(3):\n    i = 5\n    print(i)\n", "LITHON-E0119"),
    ("augmented assignment to a live range counter (E0119)",
     "i: int[64] = 0\nfor i in range(3):\n    i += 1\n    print(i)\n", "LITHON-E0119"),
    ("assignment to a live enumerate element (E0119)",
     "xs: list[int[64], 3]\ni: int[64] = 0\nv: int[64] = 0\n"
     "for i, v in enumerate(xs):\n    v = 1\n    print(v)\n", "LITHON-E0119"),
    ("an inner loop reusing a live counter (E0119)",
     "i: int[64] = 0\nfor i in range(2):\n    for i in range(2):\n        print(i)\n",
     "LITHON-E0119"),
    # 4.11. `for i in range(...)` assigns an EXISTING int variable (V1_SPEC
    # 0.6.6); it never creates one, and it keeps the declared width.
    ("range loop over an undeclared variable (E0119)",
     "for i in range(3):\n    print(i)\n", "LITHON-E0119"),
    ("range loop over a float variable (E0119)",
     "f: float[64] = 1.0\nfor f in range(3):\n    print(f)\n", "LITHON-E0119"),
    ("range loop bound that cannot fit the declared width (E0119)",
     "i: int[8] = 0\nfor i in range(300):\n    print(i)\n", "LITHON-E0119"),
    ("range loop bound not provably inside a narrow counter (E0119)",
     "i: int[8] = 0\nn: int[64] = 5\nfor i in range(n):\n    print(i)\n",
     "LITHON-E0119"),
    ("stepped range over a narrow counter (E0119)",
     "i: int[8] = 0\nfor i in range(0, 9, 2):\n    print(i)\n", "LITHON-E0119"),
    # 4.12. `k: str[N] = str(n, input("prompt"))` is the one spelling of a string
    # read. n is a literal limit that counts the NUL and must satisfy 1 <= n <= N.
    ("str() limit larger than the string's capacity (E0116)",
     "k: str[4] = str(5, input(\"x: \"))\nprint(k)\n", "LITHON-E0116"),
    ("str() limit larger than the capacity on a re-read (E0116)",
     "k: str[4] = str(4, input(\"x: \"))\nk = str(9, input(\"y: \"))\nprint(k)\n",
     "LITHON-E0116"),
    ("str() limit that is not a literal (E0116)",
     "n: int[64] = 3\nk: str[8] = str(n, input(\"x: \"))\nprint(k)\n", "LITHON-E0116"),
    ("str() limit of zero (E0116)",
     "k: str[8] = str(0, input(\"x: \"))\nprint(k)\n", "LITHON-E0116"),
    ("str() with one argument (E0115)",
     "k: str[8] = str(5)\nprint(k)\n", "LITHON-E0115"),
    ("str() of something that is not input() (E0115)",
     "k: str[8] = str(5, 3)\nprint(k)\n", "LITHON-E0115"),
    ("str(n, input()) used as a value (E0115)",
     "print(str(5, input(\"x: \")))\n", "LITHON-E0115"),
    ("str(n, input()) stored into an int (E0115)",
     "x: int[64] = str(5, input(\"x: \"))\nprint(x)\n", "LITHON-E0115"),
    # 4.13. `const var: type[size] = value` is assigned once, by its initializer.
    ('const without an initializer (E0143)',
     'const x: int[64]\nprint(1)\n', "LITHON-E0143"),
    ('const without a type (E0143)',
     'const x = 5\nprint(x)\n', "LITHON-E0143"),
    ('assignment to a constant (E0143)',
     'const x: int[64] = 5\nx = 6\nprint(x)\n', "LITHON-E0143"),
    ('augmented assignment to a constant (E0143)',
     'const x: int[64] = 5\nx += 1\nprint(x)\n', "LITHON-E0143"),
    ('a constant redeclared as a variable (E0143)',
     'const x: int[64] = 5\nx: int[64] = 6\nprint(x)\n', "LITHON-E0143"),
    ('a constant declared twice (E0143)',
     'const x: int[64] = 5\nconst x: int[64] = 6\nprint(x)\n', "LITHON-E0143"),
    ('a constant declared over a variable (E0143)',
     'x: int[64] = 5\nconst x: int[64] = 6\nprint(x)\n', "LITHON-E0143"),
    ('element store into a constant list (E0143)',
     'const xs: list[int[64], 3] = [1, 2, 3]\nxs[0] = 9\nprint(xs[0])\n', "LITHON-E0143"),
    ('append to a constant list (E0143)',
     'const xs: list[int[64], 3] = [1]\nxs.append(2)\nprint(xs[1])\n', "LITHON-E0143"),
    ('address of a constant (E0143)',
     'const x: int[64] = 5\n_p: ptr[int[64]] = addressof(x)\nprint(1)\n', "LITHON-E0143"),
    ('a constant as a for target (E0143)',
     'const i: int[64] = 0\nfor i in range(3):\n    print(i)\n', "LITHON-E0143"),
    ('a constant string re-read (E0143)',
     'const s: str[8] = "hi"\ns = str(4, input("x: "))\nprint(s)\n', "LITHON-E0143"),
    ('a constant struct (E0143)',
     'class P(struct):\n    x: int[64]\nconst p: P = 1\nprint(1)\n', "LITHON-E0143"),
    ('a constant pointer (E0143)',
     'x: int[64] = 1\nconst _p: ptr[int[64]] = addressof(x)\nprint(1)\n', "LITHON-E0143"),
    ('a constant class reference (E0143)',
     'class A:\n    x: int[64]\n    def __init__(self):\n        self.x = 1\nconst a: A = A()\nprint(a.x)\n', "LITHON-E0143"),
    ('a function parameter named like a module constant (E0143)',
     'const N: int[64] = 5\ndef f(N: int[64]) -> int[64]:\n    return N\nprint(f(1))\n', "LITHON-E0143"),
    # 4.14. `i: uint[size] = value`: never mixes with int, never wraps, scalars only.
    ('a negative literal into a uint (E0144)',
     'x: uint[8] = -1\nprint(x)\n', "LITHON-E0144"),
    ('a literal that does not fit a uint (E0144)',
     'x: uint[8] = 256\nprint(x)\n', "LITHON-E0144"),
    ('a signed value into a uint (E0144)',
     'i: int[64] = 5\nu: uint[8] = i\nprint(u)\n', "LITHON-E0144"),
    ('a uint into a narrower int (E0144)',
     'u: uint[8] = 1\ni: int[8] = u\nprint(i)\n', "LITHON-E0144"),
    ('a wider uint result into a narrower uint (E0144)',
     'u: uint[8] = 1\nv: uint[16] = 1\nw: uint[8] = u + v\nprint(w)\n', "LITHON-E0144"),
    ('uint plus int (E0144)',
     'u: uint[8] = 1\ni: int[64] = 2\nprint(u + i)\n', "LITHON-E0144"),
    ('uint plus float (E0144)',
     'u: uint[8] = 1\nf: float[64] = 1.0\nprint(u + f)\n', "LITHON-E0144"),
    ('uint compared with int (E0144)',
     'u: uint[8] = 1\ni: int[64] = 2\nprint(u < i)\n', "LITHON-E0144"),
    ('uint compared with a negative literal (E0144)',
     'u: uint[8] = 1\nprint(u < -1)\n', "LITHON-E0144"),
    ('negating a uint (E0144)',
     'u: uint[8] = 1\nprint(-u)\n', "LITHON-E0144"),
    ('a uint[64] as an index (E0144)',
     'xs: list[int[64], 4] = [1, 2, 3, 4]\nu: uint[64] = 1\nprint(xs[u])\n', "LITHON-E0144"),
    ('a uint[64] as a range bound (E0144)',
     'i: int[64] = 0\nu: uint[64] = 3\nfor i in range(u):\n    print(i)\n', "LITHON-E0144"),
    ('a list of uint (E0144)',
     'xs: list[uint[8], 2] = [1, 2]\nprint(xs[0])\n', "LITHON-E0144"),
    ('a dict of uint (E0144)',
     'd: dict[int[64], uint[8], 2] = {1: 2}\nprint(1)\n', "LITHON-E0144"),
    ('a uint stored into a list element (E0144)',
     'xs: list[int[64], 2] = [1, 2]\nu: uint[8] = 1\nxs[0] = u\nprint(xs[0])\n', "LITHON-E0144"),
    ('a uint in float() (E0144)',
     'u: uint[8] = 1\nprint(float(64, u))\n', "LITHON-E0144"),
    ('a uint in abs() (E0144)',
     'u: uint[8] = 1\nprint(abs(u))\n', "LITHON-E0144"),
    ('a uint in an if-expression (E0144)',
     'u: uint[8] = 1\nprint(u if u > 0 else 2)\n', "LITHON-E0144"),
    ('a method parameter that is a uint (E0134)',
     'class A:\n    x: int[64]\n    def __init__(self, n: uint[8]):\n        self.x = 1\na: A = A(1)\nprint(a.x)\n', "LITHON-E0134"),
    ('uint[64] true division (E0144)',
     'a: uint[64] = 1\nb: uint[64] = 2\nprint(a / b)\n', "LITHON-E0144"),
    ('int augmented by a uint (E0144)',
     'i: int[64] = 1\nu: uint[8] = 1\ni += u\nprint(i)\n', "LITHON-E0144"),
    ('an unsupported uint width (E0105)',
     'u: uint[7] = 1\nprint(u)\n', "LITHON-E0105"),
    ('uint without a width (E0105)',
     'u: uint = 1\nprint(u)\n', "LITHON-E0105"),
    ('a shift count out of range (E0103)',
     'u: uint[8] = 1\nprint(u << 64)\n', "LITHON-E0103"),
    ('a local declaration named like a module constant (E0143)',
     'const N: int[64] = 5\ndef f() -> int[64]:\n    N: int[64] = 1\n    return N\nprint(f())\n', "LITHON-E0143"),
]

# (4.12 refusals are appended to FRONTEND_REFUSALS below.)

# 4.11. The loop variable must hold a value before the loop (V1_SPEC 0.6.6).
# The frontend reads it first, so the checker's definite-assignment pass (0.6.10)
# is what refuses a declaration-only target on any path that stores nothing.
LOOP_START_VALUE_REJECTS = [
    ("range loop target declared but never given a value",
     "i: int[64]\nfor i in range(3):\n    print(i)\n"),
    ("enumerate element declared but never given a value",
     "xs: list[int[64], 2]\ni: int[64] = 0\nv: int[64]\n"
     "for i, v in enumerate(xs):\n    print(v)\n"),
    ("range loop target assigned on only one path",
     "i: int[64]\nif 1 < 2:\n    i = 1\nfor i in range(3):\n    print(i)\n"),
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


def check_loop_start_value(failures):
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for i, (name, source) in enumerate(LOOP_START_VALUE_REJECTS):
            rc, out, err = _run_source(tmp, f"lv{i}", source, "--interp")
            text = out + "\n" + err
            if rc == 0:
                print(f"  FAIL loop start value: {name}: accepted")
                failures.append(name)
            elif "not definitely assigned" not in text:
                print(f"  FAIL loop start value: {name}: refused without the "
                      f"definite-assignment message: {text.strip()[:160]}")
                failures.append(name)
            else:
                print(f"  ok   loop start value: {name} (V1_SPEC 0.6.6/0.6.10)")


# 4.15. Modules and the entry point. Each case is a set of files written into a
# fresh directory, the entry program named `main.lio`.
MODULE_REFUSALS = [
    ("an import of a module that does not exist",
     {"main.lio": "import nothing\nprint(1)\n"}),
    ("an import cycle between two modules",
     {"main.lio": "import a\nprint(1)\n",
      "a.lio": "import b\ndef fa() -> int[64]:\n    return 1\n",
      "b.lio": "import a\ndef fb() -> int[64]:\n    return 2\n"}),
    ("a module that imports itself",
     {"main.lio": "import a\nprint(1)\n",
      "a.lio": "import a\ndef fa() -> int[64]:\n    return 1\n"}),
    ("an executable statement at the top level of an imported module",
     {"main.lio": "import a\nprint(1)\n", "a.lio": "print(5)\n"}),
    ("a variable assignment at the top level of an imported module",
     {"main.lio": "import a\nprint(1)\n", "a.lio": "x: int[64] = 1\n"}),
    ("an import inside a function",
     {"main.lio": "def f() -> int[64]:\n    import a\n    return 1\nprint(f())\n",
      "a.lio": "def g() -> int[64]:\n    return 1\n"}),
    ("from m import *",
     {"main.lio": "from a import *\nprint(1)\n",
      "a.lio": "def g() -> int[64]:\n    return 1\n"}),
    ("from m import a name the module does not define",
     {"main.lio": "from a import missing\nprint(1)\n",
      "a.lio": "def g() -> int[64]:\n    return 1\n"}),
    ("import m.f where f is not a function of m",
     {"main.lio": "import a.missing\nprint(1)\n",
      "a.lio": "def g() -> int[64]:\n    return 1\n"}),
    ("a relative import",
     {"main.lio": "from .a import g\nprint(1)\n",
      "a.lio": "def g() -> int[64]:\n    return 1\n"}),
    ("an imported name that collides with a function of the program",
     {"main.lio": "from a import g\ndef g() -> int[64]:\n    return 2\nprint(g())\n",
      "a.lio": "def g() -> int[64]:\n    return 1\n"}),
    ("two imports that bind the same name",
     {"main.lio": "from a import g\nfrom b import g\nprint(g())\n",
      "a.lio": "def g() -> int[64]:\n    return 1\n",
      "b.lio": "def g() -> int[64]:\n    return 2\n"}),
    ("an entry guard with an else branch",
     {"main.lio": "if __name__ == '__main__':\n    print(1)\nelse:\n    print(2)\n"}),
    ("a guard that tests __name__ with !=",
     {"main.lio": "if __name__ != '__main__':\n    print(1)\n"}),
    ("__name__ used as a value",
     {"main.lio": "print(__name__)\n"}),
    ("a string constant in an imported module (not foldable)",
     {"main.lio": "import a\nprint(1)\n",
      "a.lio": "const NAME: str[8] = \"hi\"\ndef g() -> int[64]:\n    return 1\n"}),
    ("a constant with a computed value in an imported module",
     {"main.lio": "import a\nprint(1)\n",
      "a.lio": "const A: int[64] = 1\nconst B: int[64] = A + 1\n"}),
    ("a parameter that reuses an imported name",
     {"main.lio": "from a import g\ndef f(g: int[64]) -> int[64]:\n    return g\nprint(f(1))\n",
      "a.lio": "def g() -> int[64]:\n    return 1\n"}),
    ("a local that reuses an imported constant's name",
     {"main.lio": "from a import LIM\ndef f() -> int[64]:\n    LIM: int[64] = 2\n    return LIM\nprint(f())\n",
      "a.lio": "const LIM: int[64] = 1\n"}),
    ("a write through an imported constant",
     {"main.lio": "import a\na.LIM = 5\nprint(1)\n",
      "a.lio": "const LIM: int[64] = 1\n"}, "E0143"),
]

# (name, files, command-line extras, environment, expected stdout)
MODULE_ACCEPTS = [
    ("a library next to the program, with an alias and a member import",
     {"main.lio": "import m\nimport m.f as ff\nfrom m import g as gg\nif __name__ == '__main__':\n"
                  "    print(m.f(2), ff(3), gg(4))\n",
      "m.lio": "def f(x: int[64]) -> int[64]:\n    return x + 1\n"
               "def g(x: int[64]) -> int[64]:\n    return f(x) * 2\n"},
     [], {}, "3 4 10\n"),
    ("a dotted package path below the program",
     {"main.lio": "import lib.deep.m\nif __name__ == '__main__':\n    print(lib.deep.m.f(1))\n",
      "lib/deep/m.lio": "def f(x: int[64]) -> int[64]:\n    return x + 100\n"},
     [], {}, "101\n"),
    ("a library that imports a sibling of the entry program",
     {"main.lio": "import lib.m\nif __name__ == '__main__':\n    print(lib.m.f(5))\n",
      "base.lio": "def sq(x: int[64]) -> int[64]:\n    return x * x\n",
      "lib/m.lio": "import base\ndef f(x: int[64]) -> int[64]:\n    return base.sq(x)\n"},
     [], {}, "25\n"),
    ("a directory named with -I",
     {"main.lio": "from helpers import twice\nif __name__ == '__main__':\n    print(twice(21))\n",
      "extra/helpers.lio": "def twice(x: int[64]) -> int[64]:\n    return x * 2\n"},
     ["-I", "extra"], {}, "42\n"),
    ("a directory named in LITHON_PATH",
     {"main.lio": "from helpers import twice\nif __name__ == '__main__':\n    print(twice(21))\n",
      "extra/helpers.lio": "def twice(x: int[64]) -> int[64]:\n    return x * 2\n"},
     [], {"LITHON_PATH": "extra"}, "42\n"),
    ("the guard of an imported module does not run, the program's does",
     {"main.lio": "import m\nif __name__ == '__main__':\n    print(m.f())\n",
      "m.lio": "def f() -> int[64]:\n    return 7\nif __name__ == '__main__':\n    print(999)\n"},
     [], {}, "7\n"),
    ("a program with no guard still runs top to bottom",
     {"main.lio": "import m\nprint(m.f())\nprint(2)\n",
      "m.lio": "def f() -> int[64]:\n    return 1\n"},
     [], {}, "1\n2\n"),
    ("the same helper name in two modules does not clash",
     {"main.lio": "import a\nimport b\nif __name__ == '__main__':\n    print(a.go(), b.go())\n",
      "a.lio": "def helper() -> int[64]:\n    return 1\ndef go() -> int[64]:\n    return helper()\n",
      "b.lio": "def helper() -> int[64]:\n    return 2\ndef go() -> int[64]:\n    return helper()\n"},
     [], {}, "1 2\n"),
    ("a class and its subclass imported from different modules",
     {"main.lio": "from a import Base\nfrom b import Derived\nif __name__ == '__main__':\n"
                  "    d: Derived = Derived(2)\n    print(d.v(), d.w())\n",
      "a.lio": "class Base:\n    x: int[64]\n    def __init__(self, x: int[64]):\n        self.x = x\n"
               "    def v(self) -> int[64]:\n        return self.x\n",
      "b.lio": "from a import Base\nclass Derived(Base):\n    def __init__(self, x: int[64]):\n"
               "        super().__init__(x)\n    def w(self) -> int[64]:\n        return self.x * 10\n"},
     [], {}, "2 20\n"),
    ("an imported constant as a container capacity, and a module-private constant",
     {"main.lio": "from m import SIZE\nxs: list[int[64], SIZE] = [1, 2, 3]\n"
                  "if __name__ == '__main__':\n    print(len(xs), m_double(2))\n"
                  "def m_double(x: int[64]) -> int[64]:\n    return x * SIZE\n",
      "m.lio": "const SIZE: int[64] = 3\nconst HIDDEN: int[64] = 10\n"
               "def use() -> int[64]:\n    return HIDDEN\n"},
     [], {}, "3 6\n"),
    ("a module whose name starts with an underscore",
     {"main.lio": "import _hidden\nif __name__ == '__main__':\n    print(_hidden.f(), _hidden.K)\n",
      "_hidden.lio": "const K: int[64] = 8\ndef f() -> int[64]:\n    return K + 1\n"},
     [], {}, "9 8\n"),
    ("a module imported twice (a diamond) is linked once",
     {"main.lio": "import a\nimport b\nif __name__ == '__main__':\n    print(a.fa(), b.fb())\n",
      "common.lio": "def c() -> int[64]:\n    return 5\n",
      "a.lio": "import common\ndef fa() -> int[64]:\n    return common.c() + 1\n",
      "b.lio": "import common\ndef fb() -> int[64]:\n    return common.c() + 2\n"},
     [], {}, "6 7\n"),
]


def _module_dir(td, files):
    root = pathlib.Path(td)
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return root


def check_modules(failures):
    import os
    for i, case in enumerate(MODULE_REFUSALS):
        name, files = case[0], case[1]
        code = case[2] if len(case) > 2 else "E0145"
        with tempfile.TemporaryDirectory() as td:
            root = _module_dir(td, files)
            r = run(["python3", str(FRONTEND), str(root / "main.lio")])
            if r.returncode == 0:
                print(f"  FAIL module refusal: {name}: accepted when it must refuse")
                failures.append(name)
            elif f"LITHON-{code}" not in r.stderr:
                print(f"  FAIL module refusal: {name}: refused without {code}: "
                      f"{r.stderr.strip()[:160]}")
                failures.append(name)
            elif "Traceback" in r.stderr:
                print(f"  FAIL module refusal: {name}: leaked a Python traceback")
                failures.append(name)
            else:
                print(f"  ok   module refusal: {name} (LITHON-{code})")
    for i, (name, files, extra, env, want) in enumerate(MODULE_ACCEPTS):
        with tempfile.TemporaryDirectory() as td:
            root = _module_dir(td, files)
            cmd = ["python3", str(FRONTEND)]
            for a in extra:
                cmd.append(str(root / a) if a != "-I" else a)
            cmd.append(str(root / "main.lio"))
            envv = dict(os.environ)
            for k, v in env.items():
                envv[k] = str(root / v)
            r = subprocess.run(cmd, capture_output=True, text=True, env=envv)
            if r.returncode != 0:
                print(f"  FAIL module accept: {name}: refused: {r.stderr.strip()[:200]}")
                failures.append(name)
                continue
            ir = root / "out.ir"
            ir.write_text(r.stdout)
            if name.endswith("is linked once") and r.stdout.count("function common__c(") != 1:
                print(f"  FAIL module accept: {name}: common linked "
                      f"{r.stdout.count('function common__c(')} times")
                failures.append(name)
                continue
            bad = None
            for mode in ("--interp", "--auto"):
                t = run([str(TIER), str(ir), mode])
                out = "\n".join(ln for ln in t.stdout.splitlines()
                                if not ln.startswith("[tier")) + "\n"
                if t.returncode != 0 or out != want:
                    bad = f"{mode}: rc={t.returncode} out={out!r} want {want!r} " \
                          f"err={t.stderr.strip()[:120]!r}"
                    break
            if bad:
                print(f"  FAIL module accept: {name}: {bad}")
                failures.append(name)
            else:
                print(f"  ok   module accept: {name}")


def main() -> int:
    missing = [p.name for p in (FRONTEND, TIER) if not p.exists()]
    if missing:
        print(f"missing: {', '.join(missing)} -- build first")
        return 1
    failures = []
    check_discipline(failures)
    check_e0105(failures)
    check_frontend_refusals(failures)
    check_loop_start_value(failures)
    check_modules(failures)
    check_extension_gate(failures)
    check_e0303(failures)
    check_e0401(failures)
    total = len(E0105_REJECTS) + len(E0105_ACCEPTS) + 1
    total += len(FRONTEND_REFUSALS)
    total += len(LOOP_START_VALUE_REJECTS)
    total += len(MODULE_REFUSALS) + len(MODULE_ACCEPTS)
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