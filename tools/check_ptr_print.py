#!/usr/bin/env python3
"""print(<pointer>) must emit a hexadecimal address, in BOTH tiers.

The two tiers cannot print the same NUMBER (the interpreter hands out synthetic
addresses, native prints real frame addresses), so tools/run_tier_diff.py's
byte-identity rule does not apply to these programs. What must hold instead:

  * every pointer line is `0x` + lowercase hex, no padding, nothing else;
  * the same variable's address prints identically every time;
  * different variables have different addresses;
  * `p + 1` prints an address one pointee (8 bytes for int[64]) higher;
  * everything that is NOT a pointer still prints as before (decimal ints,
    floats, True/False) -- a pointer format must not leak onto them.
"""
import pathlib, re, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNER = ROOT / "build" / "tier_runner"
FRONTEND = ROOT / "src" / "frontend" / "frontend.py"
CASES = ROOT / "tests" / "ptr_print" / "ptr_print_cases.py"
HEX = re.compile(r"^0x[0-9a-f]+$")


def run(ir, flag):
    r = subprocess.run([str(RUNNER), str(ir), flag], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"{flag}: exit {r.returncode}\n{r.stderr}")
    return r.stdout.splitlines()


def main():
    if not RUNNER.exists():
        sys.exit(f"missing {RUNNER}; build tier_runner first")
    with tempfile.TemporaryDirectory() as t:
        ir = pathlib.Path(t) / "cases.ir"
        fe = subprocess.run([sys.executable, str(FRONTEND), str(CASES)], capture_output=True, text=True)
        if fe.returncode:
            sys.exit(fe.stderr)
        ir.write_text(fe.stdout)
        bad = 0
        for flag, tier in (("--interp", "interpreter"), ("--strict", "native")):
            out = run(ir, flag)
            # program order: a, a2, b, a+1, valueof(a), a==a2, f, valueof(f), n, valueof(n)
            if len(out) != 10:
                print(f"FAIL [{tier}] expected 10 lines, got {len(out)}: {out}"); bad += 1; continue
            a, a2, b, a8, va, eq, f, vf, n, vn = out
            checks = [
                ("int[64] ptr is hex", all(HEX.match(x) for x in (a, a2, b))),
                ("float ptr is hex",   bool(HEX.match(f))),
                ("int[8] ptr is hex",  bool(HEX.match(n))),
                ("same variable -> same address", a == a2),
                ("different variables -> different address", a != b),
                ("p + 1 is exactly one int[64] (8 bytes) higher", HEX.match(a8) and int(a8, 16) == int(a, 16) + 8),
                ("valueof still decimal", va == "1" and vn == "9"),
                ("float pointee still a float", vf == "2.5"),
                ("pointer compare still True/False", eq == "True"),
            ]
            for name, ok in checks:
                if not ok:
                    print(f"FAIL [{tier}] {name}   (output: {out})"); bad += 1
            if not bad:
                print(f"PASS [{tier}] {a}  {f}  {n}")
        print("ALL PASS" if not bad else f"{bad} FAILED")
        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
