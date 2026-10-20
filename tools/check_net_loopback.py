#!/usr/bin/env python3
"""5.2. A loopback TCP round trip built from raw syscalls must agree with a raw
C program that issues the identical syscalls.

Why a C oracle and not the interpreter: sockets are stateful and external. If
both tiers shared a wrong idea of `sockaddr_in` (say, an unswapped address),
they would agree with each other and both be wrong. The independent reference
is a libc-free C program that builds the address with `htonl` and calls the same
numbers; Lithon's side hand-rolls the byte swap, so the two agree only if the
swap is right.

`tests/net/loopback.py` is frontend-compiled and run through tier_runner in all
three modes (--interp, --strict, --auto); `tests/net/loopback_oracle.c` is
compiled with the host C compiler. Every mode's stdout must equal the oracle's,
line for line.

If the host cannot complete a loopback round trip at all (the oracle itself does
not print the expected sequence), the check reports SKIP rather than pretending
a sandbox supports sockets -- the same honesty rule the rest of verify_all.sh
uses.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNER = ROOT / "build" / "tier_runner"
FRONTEND = ROOT / "src" / "frontend" / "frontend.py"
PROGRAM = ROOT / "tests" / "net" / "loopback.lio"
ORACLE = ROOT / "tests" / "net" / "loopback_oracle.c"

# The exact sequence the oracle must show for the host to count as
# loopback-capable: bind=0, getsockname=0, socklen=16, listen=0, connect=0,
# accept ok, send=8, recv=8, payload, three close=0. The two trailing lines are
# the unswapped-address failure case; they are compared but not part of the
# capability gate, because whether 1.0.0.127 is refused is a host property.
CORE = [
    "0", "0", "16", "0", "0", "True", "8", "8", "123456789", "0", "0", "0",
]
TIER_FLAGS = [("--interp", "interpreter"), ("--strict", "native"), ("--auto", "auto")]


def find_cc():
    for cand in (os.environ.get("CC"), shutil.which("cc"),
                 shutil.which("gcc"), shutil.which("clang")):
        if cand:
            return cand
    return None


def compile_oracle(tmp):
    cc = find_cc()
    if cc is None:
        print("SKIP: no C compiler (cc/gcc/clang) to build the raw oracle")
        return None
    exe = tmp / "loopback_oracle"
    r = subprocess.run([cc, "-O2", "-o", str(exe), str(ORACLE)],
                       capture_output=True, text=True)
    if r.returncode:
        print(f"FAIL: could not compile {ORACLE.name}:\n{r.stderr}")
        return False
    return exe


def run(exe_or_args):
    r = subprocess.run(exe_or_args, capture_output=True, text=True)
    if r.returncode:
        return None, f"exit {r.returncode}\n{r.stderr}"
    return r.stdout.splitlines(), None


def main():
    if not RUNNER.exists():
        sys.exit(f"missing {RUNNER}; build tier_runner first (see src/jit/tier_runner.cpp)")

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)

        fe = subprocess.run([sys.executable, str(FRONTEND), str(PROGRAM)],
                            capture_output=True, text=True)
        if fe.returncode:
            sys.exit(f"frontend failed on {PROGRAM.name}:\n{fe.stderr}")
        ir = tmp / "loopback.ir"
        ir.write_text(fe.stdout)

        exe = compile_oracle(tmp)
        if exe is None:
            return 0
        if exe is False:
            return 1

        ref, err = run([str(exe)])
        if err:
            print(f"SKIP: the raw oracle could not run ({err.strip()})")
            return 0
        if ref[:len(CORE)] != CORE:
            print("SKIP: this host cannot complete a loopback round trip; the raw")
            print(f"      oracle printed {ref}, expected to start with {CORE}")
            return 0

        bad = 0
        print(f"oracle (C, htonl):  {ref}")
        for flag, tier in TIER_FLAGS:
            out, err = run([str(RUNNER), str(ir), flag])
            if err:
                print(f"FAIL [{tier}] {flag}: {err.strip()}")
                bad += 1
                continue
            if out != ref:
                print(f"FAIL [{tier}] {flag} disagrees with the C oracle")
                print(f"  oracle:  {ref}")
                print(f"  lithon:  {out}")
                bad += 1
            else:
                print(f"PASS [{tier}] {flag}: identical to the C oracle")

        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
