#!/usr/bin/env python3
"""
Codegen-level self-tail-call elimination (the --no-opt path).

Two implementations of "self tail call -> jump" exist and must agree:

  * the optimizer's IR-level pass (src/jit/optimize.h convert_self_tail_calls),
    which is what runs by default; and
  * the codegen-level elimination (src/jit/compile_function.h), which is the
    only one that runs when the optimizer is off (--no-opt).

The default sweep in run_tier_diff.py only ever sees the first. This script
drives the second, through build/tier_runner --no-opt, and requires:

  * shallow programs: interpreter output == --no-opt --auto output, byte for
    byte, AND --auto output (optimizer path) == --no-opt output, so the two
    mechanisms can never drift apart; and
  * deep programs (10,000,000 frames): --no-opt --auto must still run NATIVELY
    and print the exact expected value -- a stack overflow here is the E0401
    trap, i.e. the elimination silently stopped happening.

The shapes cover the codegen staging: register arguments, argument swap (the
parallel copy), a float parameter, an address-taken parameter (its landing is
memory), and more than four int or four float parameters (arguments past the
register budget stage through the outgoing-argument area).
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNER = ROOT / "build" / "tier_runner"

ACC = """
function acc(n:int[64], s:int[64]) -> int[64]:
block0:
    %0 = load n
    %1 = const_i64 1
    %2 = lt %0, %1
    branch %2, block1, block2
block1:
    %3 = load s
    return %3
    jump block2
block2:
    %5 = load n
    %6 = const_i64 1
    %7 = sub %5, %6
    %8 = load s
    %9 = load n
    %10 = add %8, %9
    %11 = call acc, %7, %10
    return %11
    return

function __main__():
block0:
    %0 = const_i64 @N@
    %1 = const_i64 0
    %2 = call acc, %0, %1
    call print, %2
    return
"""

FLIP = """
function flip(n:int[64], a:int[64], b:int[64]) -> int[64]:
block0:
    %0 = load n
    %1 = const_i64 1
    %2 = lt %0, %1
    branch %2, block1, block2
block1:
    %3 = load a
    %4 = load b
    %5 = sub %3, %4
    return %5
block2:
    %6 = load n
    %7 = const_i64 1
    %8 = sub %6, %7
    %9 = load b
    %10 = load a
    %11 = call flip, %8, %9, %10
    return %11
    return

function __main__():
block0:
    %0 = const_i64 3
    %1 = const_i64 10
    %2 = const_i64 4
    %3 = call flip, %0, %1, %2
    call print, %3
    return
"""

FSUM = """
function fsum(n:int[64], s:float[64]) -> float[64]:
block0:
    %0 = load n
    %1 = const_i64 1
    %2 = lt %0, %1
    branch %2, block1, block2
block1:
    %3 = load s
    return %3
    jump block2
block2:
    %5 = load n
    %6 = const_i64 1
    %7 = sub %5, %6
    %8 = load s
    %9 = const_f64 1.5
    %10 = add %8, %9
    %11 = call fsum, %7, %10
    return %11
    return

function __main__():
block0:
    %0 = const_i64 @N@
    %1 = const_f64 0.0
    %2 = call fsum, %0, %1
    call print, %2
    return
"""

ADDR = """
function h(p:int[64], n:int[64]) -> int[64]:
block0:
    %0 = load n
    %1 = const_i64 1
    %2 = lt %0, %1
    branch %2, block1, block2
block1:
    %3 = load p
    %4 = load n
    %5 = add %3, %4
    return %5
block2:
    %6 = addressof p
    %7 = load p
    %8 = load n
    %9 = add %7, %8
    store p, %9
    %10 = load n
    %11 = const_i64 1
    %12 = sub %10, %11
    %13 = load p
    %14 = call h, %13, %12
    return %14
    return

function __main__():
block0:
    %0 = const_i64 1
    %1 = const_i64 @N@
    %2 = call h, %0, %1
    call print, %2
    return
"""

SIX_INT = """
function f(a:int[64], b:int[64], c:int[64], d:int[64], e:int[64], n:int[64]) -> int[64]:
block0:
    %0 = load n
    %1 = const_i64 1
    %2 = lt %0, %1
    branch %2, block1, block2
block1:
    %5 = load a
    %6 = load b
    %7 = add %5, %6
    %8 = load c
    %9 = add %7, %8
    %10 = load d
    %11 = add %9, %10
    %12 = load e
    %13 = add %11, %12
    %14 = load n
    %15 = add %13, %14
    return %15
block2:
    %16 = load a
    %17 = load n
    %18 = add %16, %17
    %19 = load n
    %20 = const_i64 1
    %21 = sub %19, %20
    %22 = load b
    %23 = load c
    %24 = load d
    %25 = load e
    %26 = call f, %18, %22, %23, %24, %25, %21
    return %26
    return

function __main__():
block0:
    %0 = const_i64 1
    %1 = const_i64 2
    %2 = const_i64 3
    %3 = const_i64 4
    %4 = const_i64 5
    %5 = const_i64 @N@
    %6 = call f, %0, %1, %2, %3, %4, %5
    call print, %6
    return
"""

FIVE_FLOAT = """
function g(a:float[64], b:float[64], c:float[64], d:float[64], e:float[64], n:int[64]) -> float[64]:
block0:
    %0 = load n
    %1 = const_i64 1
    %2 = lt %0, %1
    branch %2, block1, block2
block1:
    %3 = load a
    %4 = load b
    %5 = add %3, %4
    %6 = load c
    %7 = add %5, %6
    %8 = load d
    %9 = add %7, %8
    %10 = load e
    %11 = add %9, %10
    %12 = load n
    %13 = add %11, %12
    return %13
block2:
    %14 = load a
    %15 = load n
    %16 = add %14, %15
    %17 = load n
    %18 = const_i64 1
    %19 = sub %17, %18
    %20 = load b
    %21 = load c
    %22 = load d
    %23 = load e
    %24 = call g, %16, %20, %21, %22, %23, %19
    return %24
    return

function __main__():
block0:
    %0 = const_f64 1.0
    %1 = const_f64 2.0
    %2 = const_f64 3.0
    %3 = const_f64 4.0
    %4 = const_f64 5.0
    %5 = const_i64 @N@
    %6 = call g, %0, %1, %2, %3, %4, %5
    call print, %6
    return
"""

# Shallow: interpreter is the oracle and the two TCO mechanisms must agree.
SHALLOW = {
    "acc": ACC.replace("@N@", "100"),
    "flip_arg_swap": FLIP,
    "fsum_float": FSUM.replace("@N@", "100"),
    "addr_taken_param": ADDR.replace("@N@", "5"),
    "six_int_params": SIX_INT.replace("@N@", "5"),
    "five_float_params": FIVE_FLOAT.replace("@N@", "5"),
}

# Deep: --no-opt --auto must stay native (no E0401 trap) and print this exactly.
DEEP = {
    "acc_deep": (ACC.replace("@N@", "10000000"), "50000005000000"),
    "addr_taken_param_deep": (ADDR.replace("@N@", "10000000"), "50000005000001"),
    "six_int_params_deep": (SIX_INT.replace("@N@", "10000000"), "50000005000015"),
    "five_float_params_deep": (FIVE_FLOAT.replace("@N@", "10000000"), "50000005000015.0"),
}


def run(path, *args):
    return subprocess.run([str(RUNNER), str(path), *args],
                          capture_output=True, text=True)


def check_shallow(label, ir_path):
    ref = run(ir_path, "--interp")
    opt = run(ir_path, "--auto")
    noopt = run(ir_path, "--no-opt", "--auto")

    problems = []
    if ref.returncode != 0:
        problems.append(f"interpreter failed: {ref.stderr.strip()[:200]}")
    if opt.returncode != 0:
        problems.append(f"--auto failed: {opt.stderr.strip()[:200]}")
    if noopt.returncode != 0:
        problems.append(f"--no-opt failed: {noopt.stderr.strip()[:200]}")
    if ref.stdout != noopt.stdout:
        problems.append("STDOUT MISMATCH (interp vs --no-opt)\n"
                        f"      interp  : {ref.stdout!r}\n"
                        f"      --no-opt: {noopt.stdout!r}")
    if opt.stdout != noopt.stdout:
        problems.append("STDOUT MISMATCH (--auto vs --no-opt)\n"
                        f"      --auto  : {opt.stdout!r}\n"
                        f"      --no-opt: {noopt.stdout!r}")

    status = "PASS" if not problems else "FAIL"
    print(f"[{status}] {label:<28} {ref.stdout.strip()}")
    for p in problems:
        print(f"      {p}")
    return not problems


def check_deep(label, ir_path, expected):
    noopt = run(ir_path, "--no-opt", "--auto")
    tier = "tier1" if "[tier1]" in noopt.stderr else (
        "tier0" if "[tier0]" in noopt.stderr else "unknown")

    problems = []
    if noopt.returncode != 0:
        problems.append("--no-opt did not run to completion (stack overflow?): "
                        f"{noopt.stderr.strip()[:200]}")
    if noopt.stdout.strip() != expected:
        problems.append(f"got {noopt.stdout.strip()!r}, want {expected!r}")
    if tier != "tier1":
        problems.append(f"ran on {tier}, want tier1 (deep recursion must be native)")

    status = "PASS" if not problems else "FAIL"
    print(f"[{status}] {label:<28} 10,000,000 frames, output {noopt.stdout.strip()}")
    for p in problems:
        print(f"      {p}")
    return not problems


def main():
    if not RUNNER.exists():
        print(f"missing {RUNNER}; build it first")
        return 2
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        print("--- shallow: interp == --no-opt --auto == --auto ---")
        for name, text in SHALLOW.items():
            ir = pathlib.Path(tmp) / f"{name}.ir"
            ir.write_text(text)
            ok = check_shallow(name, ir) and ok
        print("--- deep: --no-opt stays native (no E0401 trap) ---")
        for name, (text, expected) in DEEP.items():
            ir = pathlib.Path(tmp) / f"{name}.ir"
            ir.write_text(text)
            ok = check_deep(name, ir, expected) and ok
    print("\nALL TCO CHECKS PASSED" if ok else "\nTCO CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
