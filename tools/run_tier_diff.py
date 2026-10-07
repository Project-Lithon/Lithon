#!/usr/bin/env python3
"""
Tier differential test.

For every program, run it twice through build/tier_runner:
  --interp   the interpreter (the correctness oracle)
  --auto     native when the print guard proves it safe, else interpreter

The two stdout streams must be byte-identical. The tier that --auto
actually used is read from stderr ([tier1] native / [tier0] interpreter)
and reported, so a green run cannot hide "everything silently fell back".

Inputs:
  * tests/programs/*.py and tests/typed_regression/*.py (via the frontend)
  * ADVERSARIAL: hand-written IR aimed at print-format divergence
  * TRAPS: hand-written IR that must FAIL at run time, identically in both
    tiers (same stdout before the failure, same stderr, same exit code)

Adversarial cases also declare which tier they EXPECT, so a guard that
becomes too weak (native where it must refuse) or too strict (interpreter
where native is provably safe) both fail the run.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNER = ROOT / "build" / "tier_runner"
FRONTEND = ROOT / "src" / "frontend" / "frontend.py"
SUITES = [ROOT / "tests" / "programs", ROOT / "tests" / "typed_regression"]

# name -> (ir_text, expected_tier_for_auto) ; tier is "tier0" or "tier1"
# Bool-only prints run natively (True/False). Float and mixed numeric prints
# run natively too, now that the JIT has XMM arithmetic, float comparison and
# CPython-compatible float rendering. Only genuinely unknown prints must fall
# back -- a tier0 expectation here means "the guard is not yet strong enough",
# so each one is a standing reminder of what is still unsupported.
ADVERSARIAL = {
    "bool_from_compare": ("""
function main():
block0:
    %0 = const_i64 1
    %1 = const_i64 2
    %2 = lt %0, %1
    call print, %2
    return
""", "tier1"),
    "not_of_int": ("""
function main():
block0:
    %0 = const_i64 5
    %1 = not %0
    call print, %1
    %2 = const_i64 0
    %3 = not %2
    call print, %3
    return
""", "tier1"),
    "bool_via_variable": ("""
function main():
block0:
    %0 = const_i64 3
    %1 = const_i64 4
    %2 = gt %0, %1
    store flag, %2 : bool
    %3 = load flag
    call print, %3
    return
""", "tier1"),
    "bool_via_return": ("""
function less(a: int[64], b: int[64]) -> bool:
block0:
    %0 = load a
    %1 = load b
    %2 = lt %0, %1
    return %2
    return

function main():
block0:
    %0 = const_i64 1
    %1 = const_i64 2
    %2 = call less, %0, %1
    call print, %2
    return
""", "tier1"),
    "float_print": ("""
function main():
block0:
    %0 = const_f64 3.5
    %1 = const_f64 2.0
    %2 = add %0, %1
    call print, %2
    return
""", "tier1"),
    # Float modulo, including the trunc-vs-floor cases that CPython's own %
    # gets differently and so cannot live in tests/programs/float.py, which is
    # compared against CPython. The two tiers must agree byte for byte.
    #
    # 1.0 % inf is the case that was actually wrong: n = trunc(1.0/inf) is 0,
    # so the answer is the dividend, but the guard meant to skip the n*b
    # multiply jumped to the addsd that normalizes signed zero, one
    # instruction early, leaving 0 * inf to produce a NaN.
    "float_mod_trunc_and_inf": ("""
function main():
block0:
    %0 = const_f64 1e308
    store big, %0 : float[64]
    %1 = load big
    %2 = load big
    %3 = mul %1, %2
    store inf, %3 : float[64]
    %4 = load inf
    %5 = load inf
    %6 = sub %4, %5
    store nan, %6 : float[64]
    %7 = const_f64 7.5
    %8 = const_f64 2.0
    %9 = const_f64 -7.5
    %10 = const_f64 -2.0
    %11 = const_f64 -4.0
    %12 = const_f64 1.0
    %13 = const_f64 0.0
    %14 = mod %7, %8
    call print, %14
    %15 = mod %9, %8
    call print, %15
    %16 = mod %7, %10
    call print, %16
    %17 = mod %9, %10
    call print, %17
    %18 = mod %11, %8
    call print, %18
    %19 = mod %12, %8
    call print, %19
    %20 = load inf
    %21 = mod %12, %20
    call print, %21
    %22 = mod %13, %20
    call print, %22
    %23 = mod %20, %12
    call print, %23
    %24 = mod %20, %20
    call print, %24
    %25 = load nan
    %26 = mod %12, %25
    call print, %26
    %27 = mod %25, %12
    call print, %27
    return
""", "tier1"),
    # The allocator may hand a dynamic shift's result RCX (it is a member of
    # kTempPool on POSIX), and `shl rcx, cl` would shift a register by its own
    # low bits. So the result is computed elsewhere and moved into RCX -- which
    # has to happen AFTER the pop that restores the shift count, or the pop
    # overwrites the result with the count and the store commits the count.
    # The first shl below is allocated to RCX, so this is that exact case.
    "dynamic_shift_rcx_dst": ("""
function main():
block0:
    %0 = const_i64 5
    store v, %0 : int[64]
    %1 = const_i64 3
    store k, %1 : int[64]
    %2 = load v
    %3 = load k
    %4 = shl %2, %3
    store r1, %4 : int[64]
    %5 = const_i64 255
    %6 = load k
    %7 = shr %5, %6
    store r2, %7 : int[64]
    %8 = load r1
    call print, %8
    %9 = load r2
    call print, %9
    return
""", "tier1"),
    "int_and_or_value_semantics": ("""
function main():
block0:
    %0 = const_i64 5
    %1 = const_i64 0
    %2 = and %0, %1
    call print, %2
    %3 = or %0, %1
    call print, %3
    %4 = and %1, %0
    call print, %4
    %5 = or %1, %0
    call print, %5
    return
""", "tier1"),
    "int_arithmetic_chain": ("""
function main():
block0:
    %0 = const_i64 7
    %1 = const_i64 6
    %2 = mul %0, %1
    %3 = const_i64 2
    %4 = sub %2, %3
    %5 = add %4, %0
    call print, %5
    return
""", "tier1"),
    "compare_used_only_for_branch": ("""
function main():
block0:
    %0 = const_i64 1
    %1 = const_i64 2
    %2 = lt %0, %1
    branch %2, block1, block2
block1:
    %3 = const_i64 111
    call print, %3
    return
block2:
    %4 = const_i64 222
    call print, %4
    return
""", "tier1"),
    "int_recursion": ("""
function fact(n: int[64]) -> int[64]:
block0:
    %0 = load n
    %1 = const_i64 2
    %2 = lt %0, %1
    branch %2, block1, block2
block1:
    %3 = const_i64 1
    return %3
    jump block2
block2:
    %4 = load n
    %5 = const_i64 1
    %6 = sub %4, %5
    %7 = call fact, %6
    %8 = load n
    %9 = mul %8, %7
    return %9
    return

function main():
block0:
    %0 = const_i64 10
    %1 = call fact, %0
    call print, %1
    return
""", "tier1"),
    # 4.1 containers, float elements, literal indices (no runtime guard emitted).
    # Two different values in two different slots: a scaled-address bug that
    # sends both stores to one slot prints 4.0 for the first line, and one that
    # reads from the wrong slot swaps or repeats them. Before the REX/F2 encoder
    # fix this shape crashed with SIGBUS.
    "list_float_roundtrip": ("""
function main():
block0:
    store xs : list[float[64],4]
    %0 = const_i64 0
    %1 = const_i64 3
    %2 = const_f64 1.5
    %3 = const_f64 4.0
    IndexStore xs, %0, %2
    IndexStore xs, %1, %3
    %4 = Index xs, %0
    %5 = Index xs, %1
    call print, %4
    call print, %5
    return
""", "tier1"),
    # Same idea with RUNTIME indices, so the bounds guard and the scaled SIB
    # form with an index register both execute. The indices arrive as
    # parameters so constant propagation cannot turn them back into literals.
    # The result is the SUM of both slots: if a and b ever alias, the second
    # write wins and the sum is 8.0 instead of 5.5.
    "list_float_dynamic_index": ("""
function get2(a: int[64], b: int[64]) -> float[64]:
block0:
    store xs : list[float[64],4]
    %0 = load a
    %1 = const_f64 1.5
    IndexStore xs, %0, %1
    %2 = load b
    %3 = const_f64 4.0
    IndexStore xs, %2, %3
    %4 = load a
    %5 = Index xs, %4
    %6 = load b
    %7 = Index xs, %6
    %8 = add %5, %7
    return %8

function main():
block0:
    %0 = const_i64 0
    %1 = const_i64 3
    %2 = call get2, %0, %1
    call print, %2
    return
""", "tier1"),
    # An element nobody wrote is ZERO of its kind in both tiers. Before the JIT
    # zeroed at the declaration, native printed a stale stack word as a denormal
    # (6.95e-310) where the interpreter printed 0.0. An int list and a float list
    # are both read, because the zero is different bits' worth of meaning only
    # by coincidence: it is the same all-zero fill for both.
    "list_read_before_write": ("""
function main():
block0:
    store xs : list[float[64],4]
    store ns : list[int[64],3]
    %0 = const_i64 3
    %1 = Index xs, %0
    call print, %1
    %2 = const_i64 2
    %3 = Index ns, %2
    call print, %3
    return
""", "tier1"),
    # A declaration that executes again resets the container. Iteration 0 writes
    # 9 into xs[0]; iteration 1 must read 0 again, not the 9. The output is
    # "0" twice. This is the case a prologue-only zeroing would get wrong.
    "list_declaration_in_loop_resets": ("""
function main():
block0:
    %0 = const_i64 0
    store i, %0 : int[64]
    jump block1
block1:
    %1 = load i
    %2 = const_i64 2
    %3 = lt %1, %2
    branch %3, block2, block3
block2:
    store xs : list[int[64],2]
    %4 = const_i64 0
    %5 = Index xs, %4
    call print, %5
    %6 = const_i64 9
    IndexStore xs, %4, %6
    %7 = load i
    %8 = const_i64 1
    %9 = add %7, %8
    store i, %9
    jump block1
block3:
    return
""", "tier1"),

    # 4.4. addressof/valueof through real registers: the guard must track the
    # pointer through the store/load and prove a valueof is Int (or Float)
    # before letting native run. int, float and a pointer equality that has to
    # stay an integer compare. A raw pointer is never printed here -- the
    # typechecker refuses that at the source and the guard would refuse it too.
    "ptr_round_trip": ("""
function main():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    store _p, %1 : ptr[int[64]]
    %2 = load _p
    %3 = valueof %2 : int[64]
    call print, %3
    %4 = addressof x
    store _r, %4 : ptr[int[64]]
    %5 = load _r
    %6 = load _p
    %7 = eq %5, %6
    call print, %7
    %8 = const_f64 3.5
    store y, %8 : float[64]
    %9 = addressof y
    store _py, %9 : ptr[float[64]]
    %10 = load _py
    %11 = valueof %10 : float[64]
    call print, %11
    return
""", "tier1"),
}

# Programs that must FAIL at run time, the same way in both tiers.
#
# The index guard emits a host call that prints the interpreter's own message
# and exits, so a native trap and an interpreted one must agree on all three
# observable things: what reached stdout BEFORE the failure (the leading
# print proves the native tier flushes it on the way out), the stderr text,
# and the exit status. Negative indices trap on purpose (CPython would wrap
# xs[-1]; Lithon does not), so -3 is here alongside the two upper-bound cases.
#
# Each program touches the container only after the index is rejected, so none
# of them ever reads an unwritten element.
TRAP_STORE = """
function poke(n: int[64]) -> float[64]:
block0:
    store xs : list[float[64],4]
    %0 = load n
    %1 = const_f64 2.5
    IndexStore xs, %0, %1
    return %1

function main():
block0:
    %0 = const_i64 7
    call print, %0
    %1 = const_i64 @N@
    %2 = call poke, %1
    call print, %2
    return
"""

TRAP_LOAD = """
function peek(n: int[64]) -> float[64]:
block0:
    store xs : list[float[64],4]
    %0 = load n
    %1 = Index xs, %0
    return %1

function main():
block0:
    %0 = const_i64 7
    call print, %0
    %1 = const_i64 @N@
    %2 = call peek, %1
    call print, %2
    return
"""

# 4.3. A dict read of a key that was never stored has no memory-safe answer, so
# both tiers trap. The message is the interpreter's wording in both cases, which
# is what makes a native trap and an interpreted one byte-identical.
# 4.3. A dict read of a key that was never stored has no memory-safe answer, so
# both tiers trap. The message is the interpreter's wording in both cases, which
# is what makes a native trap and an interpreted one byte-identical.
TRAP_DICT_MISS = """
function lookup() -> int[64]:
block0:
    store d : dict[int[64], int[64], 4]
    %0 = const_i64 @K@
    %1 = DictIndex d, %0
    return %1

function main():
block0:
    %0 = const_i64 7
    call print, %0
    %1 = call lookup
    call print, %1
    return
"""

TRAP_MESSAGE = "error: interpreter: list index out of range"
TRAP_EXIT = 1

# name -> (ir_text, expected_tier_for_auto, expected_stderr_substring)
TRAPS = {
    "store_index_negative": (TRAP_STORE.replace("@N@", "-3"), "tier1", TRAP_MESSAGE),
    "store_index_eq_capacity": (TRAP_STORE.replace("@N@", "4"), "tier1", TRAP_MESSAGE),
    "store_index_far_out": (TRAP_STORE.replace("@N@", "99"), "tier1", TRAP_MESSAGE),
    "load_index_negative": (TRAP_LOAD.replace("@N@", "-3"), "tier1", TRAP_MESSAGE),
    "load_index_eq_capacity": (TRAP_LOAD.replace("@N@", "4"), "tier1", TRAP_MESSAGE),
    "load_index_far_out": (TRAP_LOAD.replace("@N@", "99"), "tier1", TRAP_MESSAGE),
    "dict_miss": (TRAP_DICT_MISS.replace("@K@", "1"), "tier1",
                  "error: interpreter: dict key not found"),
    "dict_miss_after_store": (TRAP_DICT_MISS.replace("@K@", "2"), "tier1",
                              "error: interpreter: dict key not found"),
}


def run(args):
    return subprocess.run([str(RUNNER)] + args, capture_output=True, text=True)


def tier_of(stderr):
    if "[tier1]" in stderr:
        return "tier1"
    if "[tier0]" in stderr:
        return "tier0"
    return "unknown"


def without_tier_markers(stderr):
    """stderr minus the runner's own [tier0]/[tier1] report lines, so the two
    tiers' error text can be compared directly."""
    return "\n".join(line for line in stderr.splitlines()
                     if "[tier0]" not in line and "[tier1]" not in line).strip()


def compile_to_ir(py_file, tmpdir):
    r = subprocess.run(["python3", str(FRONTEND), str(py_file)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"frontend failed on {py_file.name}: {r.stderr}")
    ir = pathlib.Path(tmpdir) / (py_file.stem + ".ir")
    ir.write_text(r.stdout)
    return ir


def check(label, ir_path, expected_tier=None):
    ref = run([str(ir_path), "--interp"])
    auto = run([str(ir_path), "--auto"])
    tier = tier_of(auto.stderr)

    problems = []
    if ref.returncode != 0:
        problems.append(f"interpreter failed: {ref.stderr.strip()[:200]}")
    if auto.returncode != 0:
        problems.append(f"auto failed: {auto.stderr.strip()[:200]}")
    if ref.stdout != auto.stdout:
        problems.append("STDOUT MISMATCH\n"
                        f"      interp: {ref.stdout!r}\n"
                        f"      auto  : {auto.stdout!r}")
    if expected_tier and tier != expected_tier:
        problems.append(f"expected {expected_tier}, ran {tier}")

    status = "PASS" if not problems else "FAIL"
    print(f"[{status}] {label:<40} ran on {tier}")
    for p in problems:
        print(f"      {p}")
    return not problems, tier


def check_trap(label, ir_path, expected_tier, expected_message):
    """Both tiers must fail, and fail the same way: exit status, the text on
    stderr, and the stdout produced before the failure."""
    ref = run([str(ir_path), "--interp"])
    auto = run([str(ir_path), "--auto"])
    tier = tier_of(auto.stderr)
    ref_err = without_tier_markers(ref.stderr)
    auto_err = without_tier_markers(auto.stderr)

    problems = []
    if ref.returncode != TRAP_EXIT:
        problems.append(f"interpreter exit {ref.returncode}, want {TRAP_EXIT}: "
                        f"{ref_err[:200]!r}")
    if auto.returncode != TRAP_EXIT:
        problems.append(f"auto exit {auto.returncode}, want {TRAP_EXIT}: "
                        f"{auto_err[:200]!r}")
    if expected_message not in ref_err:
        problems.append(f"interpreter stderr lacks {expected_message!r}: {ref_err!r}")
    if ref_err != auto_err:
        problems.append("STDERR MISMATCH\n"
                        f"      interp: {ref_err!r}\n"
                        f"      auto  : {auto_err!r}")
    if ref.stdout != auto.stdout:
        problems.append("STDOUT MISMATCH (output before the trap)\n"
                        f"      interp: {ref.stdout!r}\n"
                        f"      auto  : {auto.stdout!r}")
    if expected_tier and tier != expected_tier:
        problems.append(f"expected {expected_tier}, ran {tier}")

    status = "PASS" if not problems else "FAIL"
    print(f"[{status}] {label:<40} ran on {tier}")
    for p in problems:
        print(f"      {p}")
    return not problems, tier


def check_direct_phis(label, ir_path):
    """2.7: the direct-Phi path must agree with the memory path, exactly.

    The comparison is deliberately against --ssa, which resolves merges to
    memory, and not against the interpreter. The two implementations of a merge
    are what is being cross checked here. The interpreter is not in the business
    of resolving Phis at all, and going through it would only prove both paths
    are wrong in the same way.

    The three outcomes are kept apart on purpose.

    match means both compiled and printed the same thing, so that is a pass.

    known gap means both rejected the program for the same reason, so it is not a
    failure and not a pass either. This is the pre-existing hole where --ssa
    cannot handle a program with a user function call or a container, so direct
    Phi codegen is never reached for it. Collapsing this bucket into either
    neighbouring one would hide it. As a pass it would look covered, as a
    failure it would blame the new code.

    mismatch is anything else, so that is a fail.
    """
    mem = run([str(ir_path), "--auto", "--ssa"])
    direct = run([str(ir_path), "--auto", "--direct-phis"])

    mem_err = without_tier_markers(mem.stderr)
    direct_err = without_tier_markers(direct.stderr)

    problems = []
    if mem.returncode != 0 and direct.returncode != 0 and mem_err == direct_err:
        print(f"[GAP ] {label:<40} both paths reject it (known --ssa limitation)")
        return True, "gap"
    if mem.returncode != direct.returncode:
        problems.append(f"exit differs: --ssa {mem.returncode}, "
                        f"--direct-phis {direct.returncode}")
    if mem.stdout != direct.stdout:
        problems.append("STDOUT MISMATCH\n"
                        f"      --ssa        : {mem.stdout!r}\n"
                        f"      --direct-phis : {direct.stdout!r}")
    if mem_err != direct_err:
        problems.append("STDERR MISMATCH\n"
                        f"      --ssa        : {mem_err!r}\n"
                        f"      --direct-phis : {direct_err!r}")

    status = "PASS" if not problems else "FAIL"
    print(f"[{status}] {label:<40} direct-phi path matches")
    for prob in problems:
        print(f"      {prob}")
    return not problems, "match" if not problems else "mismatch"


def main():
    if not RUNNER.exists():
        print(f"missing {RUNNER}; build it first (see src/jit/tier_runner.cpp)")
        return 2

    results = []
    tiers = {"tier0": 0, "tier1": 0, "unknown": 0}

    with tempfile.TemporaryDirectory() as tmp:
        for suite in SUITES:
            for py in sorted(suite.glob("*.py")):
                ir = compile_to_ir(py, tmp)
                ok, tier = check(f"{suite.name}/{py.stem}", ir)
                results.append(ok)
                tiers[tier] += 1

        print("--- adversarial ---")
        for name, (ir_text, want) in ADVERSARIAL.items():
            ir = pathlib.Path(tmp) / f"{name}.ir"
            ir.write_text(ir_text)
            ok, tier = check(f"adversarial/{name}", ir, want)
            results.append(ok)
            tiers[tier] += 1

        print("--- traps (both tiers must fail identically) ---")
        for name, (ir_text, want, message) in TRAPS.items():
            ir = pathlib.Path(tmp) / f"trap_{name}.ir"
            ir.write_text(ir_text)
            ok, tier = check_trap(f"traps/{name}", ir, want, message)
            results.append(ok)
            tiers[tier] += 1

    print("--- direct float Op::Phi (2.7: direct path vs memory path) ---")
    phi = {"match": 0, "gap": 0, "mismatch": 0}
    with tempfile.TemporaryDirectory() as tmp:
        for suite in SUITES:
            for py in sorted(suite.glob("*.py")):
                ir = compile_to_ir(py, tmp)
                ok, kind = check_direct_phis(f"{suite.name}/{py.stem}", ir)
                results.append(ok)
                phi[kind] += 1

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed  "
          f"(native: {tiers['tier1']}, interpreter fallback: {tiers['tier0']})")
    print(f"direct-Phi sweep: {phi['match']} matched, {phi['gap']} known gaps, "
          f"{phi['mismatch']} mismatched")
    if phi["mismatch"]:
        return 1
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
