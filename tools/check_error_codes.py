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
     Today: E0105 (unsupported type width) and E0303 (int64 overflow: the
     constant case refused at typecheck, the dynamic case trapping with
     byte-identical messages in the interpreter and JIT). Both enforced in
     the frontend/typechecker and pinned in both tiers.

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


def _run_source(tmp, tag, source, mode):
    """Compile a source program and run it under tier_runner in `mode`.

    Returns (returncode, normalized stdout, normalized stderr).
    """
    src = tmp / f"{tag}.py"
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


def check_e0105(failures):
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for name, where, body, needle in E0105_REJECTS:
            if where == "frontend":
                (tmp / "in.py").write_text(body)
                r = run(["python3", str(FRONTEND), str(tmp / "in.py")])
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
            (tmp / "in.py").write_text(body)
            r = run(["python3", str(FRONTEND), str(tmp / "in.py")])
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


def main() -> int:
    missing = [p.name for p in (FRONTEND, TIER) if not p.exists()]
    if missing:
        print(f"missing: {', '.join(missing)} -- build first")
        return 1
    failures = []
    check_discipline(failures)
    check_e0105(failures)
    check_e0303(failures)
    total = len(E0105_REJECTS) + len(E0105_ACCEPTS) + 1
    total += len(E0303_REJECTS) + len(E0303_WRAPS) + len(E0303_TRAPS)
    done = total - len(failures)
    if failures:
        print(f"\n{len(failures)} of {total} check(s) FAILED")
        return 1
    print(f"\nall {total} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())