#!/usr/bin/env python3
"""Soundness of the native optimisations that remove checks (tests/opt_soundness).

The induction-variable overflow proof (src/jit/optimize.h) removes the E0303 check from
`k = k + 1` when the loop only runs while `k < n`. That is only worth having if it NEVER removes a
check that could fire, so every program here is run on the interpreter (which always checks) and on
the native tier, and must print the same stdout, the same stderr (the E0303 trap and its position)
and exit with the same status:

  * programs where the proof must NOT apply and the trap must still happen (`k <= n`, a step of two,
    an increment inside an inner loop, a counter changed twice, a counter whose address is taken);
  * programs where it does apply and the loop must simply be right.

A native run is given a timeout: a wrongly removed check wraps the counter around and the loop never
ends, which is exactly the failure this must catch.
"""
import os, pathlib, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNER = ROOT / "build" / "tier_runner"
FRONTEND = ROOT / "src" / "frontend" / "frontend.py"


def lines(text):
    return [l for l in text.splitlines() if not l.startswith("[tier")]


def main():
    if not RUNNER.exists():
        print("SKIP: build/tier_runner is not built")
        return 0
    tmp = pathlib.Path(tempfile.mkdtemp())
    fails = 0
    for src in sorted((ROOT / "tests" / "opt_soundness").glob("*.lio")):
        ir = tmp / (src.stem + ".ir")
        ir.write_text(subprocess.run([sys.executable, str(FRONTEND), str(src)],
                                     capture_output=True, text=True).stdout)
        a = subprocess.run([str(RUNNER), str(ir), "--interp"], capture_output=True, text=True,
                           errors="replace", timeout=60)
        try:
            b = subprocess.run([str(RUNNER), str(ir), "--native"], capture_output=True, text=True,
                               errors="replace", timeout=20)
        except subprocess.TimeoutExpired:
            print(f"  FAIL {src.stem}: the native tier does not terminate (a check was wrongly removed)")
            fails += 1
            continue
        ok = "[tier1] native" in b.stderr and \
            (a.returncode, a.stdout, lines(a.stderr)) == (b.returncode, b.stdout, lines(b.stderr))
        print(f"  {'ok  ' if ok else 'FAIL'} {src.stem}"
              f"{'  (traps E0303, identically)' if ok and 'LITHON-E0303' in b.stderr else ''}")
        fails += not ok
    print("\nall optimiser-soundness checks passed" if not fails else f"\n{fails} check(s) FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
