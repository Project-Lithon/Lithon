#!/usr/bin/env python3
"""Loop-shape guards for break/continue loops (hardening item #6 tail).

A loop whose body contains break/continue has MORE than one exit. None of the
codegen loop transforms is allowed to touch it:

  * the straight-line unroller duplicates a body whose extra edge leaves the
    loop, so a break inside a duplicated copy would jump ... somewhere;
  * the AVX2 vectorizer demands the single canonical exit shape;
  * the diamond unroller has a specific single-exit diamond shape.

The compile_module counters (CompiledModule::loops_unrolled / diamonds_unrolled
/ loops_vectorized) are reported on stderr as "[stats] unrolled=... diamonds=...
vectorized=... rotated=..." by `tier_runner --dump-hex`. This tool asserts all
three stay 0 for every break/continue program under every transform flag, and
runs one POSITIVE control (a single-body loop with no extra exit) that proves
the counters are live -- otherwise a "0 0 0" would be vacuous.

Rotation is exempt: re-testing a loop condition on a back edge does not
duplicate the body, so it stays exact for multi-exit loops. The control loop
below deliberately must NOT rotate (its header is not rotatable shape-wise);
`rotated` is informational only.

Exit 0 on pass, 1 on the first failure. Requires build/tier_runner.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIER = ROOT / "build" / "tier_runner"
FRONT = ROOT / "src" / "frontend" / "frontend.py"

STATS_RE = re.compile(
    r"\[stats\] unrolled=(\d+) diamonds=(\d+) vectorized=(\d+) rotated=(\d+)"
)

FLAG_COMBOS = [
    [],
    ["--unroll-diamonds"],
    ["--accum-unroll"],
    ["--ssa"],
    ["--direct-phis"],
    ["--no-opt"],
]

# Positive control: the canonical single-block-body loop the unroller exists
# for (body and latch fused, ending `jump block1` immediately after header
# block1). With default options the straight-line unroller MUST fire, proving
# the counters actually observe the pipeline instead of always reading 0.
CONTROL_IR = """\
function __main__():
block0:
    %0 = const_i64 0
    store acc, %0 : int[64]
    %1 = const_i64 0
    store i, %1 : int[64]
    jump block1
block1:
    %2 = load i
    %3 = const_i64 10
    %4 = lt %2, %3
    branch %4, block2, block3
block2:
    %5 = load acc
    %6 = load i
    %7 = add %5, %6
    store acc, %7 : int[64]
    %8 = load i
    %9 = const_i64 1
    %10 = add %8, %9
    store i, %10
    jump block1
block3:
    %11 = load acc
    call print, %11
    return
"""


def compile_to_ir(py_program: Path) -> Path:
    ir_path = Path(tempfile.mkstemp(suffix=".ir")[1])
    subprocess.run(
        [sys.executable, str(FRONT), str(py_program)],
        stdout=open(ir_path, "w"),
        stderr=subprocess.PIPE,
        check=True,
    )
    return ir_path


def stats_for(ir_path: Path, flags: list[str]) -> tuple[int, int, int, int]:
    proc = subprocess.run(
        [str(TIER), str(ir_path), "--dump-hex", "--auto", *flags],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    m = STATS_RE.search(proc.stderr.decode())
    if proc.returncode != 0 or m is None:
        raise RuntimeError(
            f"tier_runner failed on {ir_path} with {flags}: "
            f"{proc.stderr.decode()[-400:]}"
        )
    return tuple(int(x) for x in m.groups())


def main() -> int:
    if not TIER.exists():
        print(f"error: {TIER} not built (run tools/verify_all.sh or build the target)")
        return 1

    break_programs = [
        ROOT / "tests" / "programs" / "break_continue.lio",
        ROOT / "tests" / "programs" / "range_step.lio",
    ]

    for prog in break_programs:
        if not prog.exists():
            print(f"error: missing {prog}")
            return 1
        ir = compile_to_ir(prog)
        try:
            for flags in FLAG_COMBOS:
                unrolled, diamonds, vectorized, rotated = stats_for(ir, flags)
                if (unrolled, diamonds, vectorized) != (0, 0, 0):
                    print(
                        f"FAIL {prog.name} {flags}: break/continue loop was "
                        f"unrolled={unrolled} diamonds={diamonds} vectorized={vectorized}"
                    )
                    return 1
            print(f"ok {prog.name}: no unroll/vectorize under any flag combo")
        finally:
            ir.unlink()

    with tempfile.NamedTemporaryFile("w", suffix=".ir", delete=False) as f:
        f.write(CONTROL_IR)
        ctl = Path(f.name)
    try:
        unrolled, diamonds, vectorized, _ = stats_for(ctl, ["--auto"])
    finally:
        ctl.unlink()
    if unrolled < 1:
        print(f"FAIL positive control: single-body loop should unroll, got {unrolled}")
        return 1
    if (diamonds, vectorized) != (0, 0):
        print(f"FAIL positive control: diamonds={diamonds} vectorized={vectorized} on a scalar loop")
        return 1
    print(f"ok positive control: straight-line unroller fired ({unrolled} loop) -- counters are live")
    return 0


if __name__ == "__main__":
    sys.exit(main())