#!/usr/bin/env python3
"""4.16 Microsoft x64 ABI: build the JIT for Windows and run it under wine.

Two independent checks, both on a real Windows build (x86_64-w64-mingw32-g++):

  1. ABI conformance (src/jit/win64_abi_probe.cpp). A probe that follows the Win64
     convention exactly -- arguments in RCX/RDX/XMM0/XMM1, 32 bytes of shadow space,
     a 16-byte aligned stack -- calls JIT-compiled functions after loading every
     callee-saved register (RBX, RDI, RSI, R12-R15 and the FULL 128 bits of
     XMM6-XMM15) with a distinct sentinel, and checks they all come back unchanged,
     along with the function's own result.

  2. The program suites. `tier_runner.exe` is built with -O0, because an
     unoptimised C helper spills its arguments into its shadow space, so a host call
     that failed to reserve those 32 bytes corrupts the JIT frame and the program
     breaks. Every program in tests/typed_regression and tests/programs runs through
     the Windows interpreter and the Windows native tier; stdout and the exit status
     must be identical.

Skipped (exit 0, says so) when MinGW or wine is not installed; --require makes that
an error.

    tools/check_win64.py             # probe + all suites
    tools/check_win64.py --quick     # probe + a sample of the suites
    tools/check_win64.py --require
"""
from __future__ import annotations

import argparse
import math
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "src" / "frontend" / "frontend.py"
OUT = ROOT / "build" / "win64"
CXX = "x86_64-w64-mingw32-g++"
ENV = dict(os.environ, WINEDEBUG="-all")
# On a real Windows host the binaries run directly; elsewhere through wine.
NATIVE_WINDOWS = sys.platform in ("win32", "cygwin", "msys")
RUNNER = [] if NATIVE_WINDOWS else ["wine"]

SOURCES = {
    "tier_runner_O0.exe": ([ "src/jit/tier_runner.cpp", "src/ir/text_parser.cpp",
                            "src/interpreter/interpreter.cpp", "src/typecheck/typecheck.cpp"], "-O0"),
    "win64_abi_probe.exe": (["src/jit/win64_abi_probe.cpp", "src/ir/text_parser.cpp",
                             "src/typecheck/typecheck.cpp"], "-O1"),
}

SOURCES["tier_runner32.exe"] = (["src/jit/tier_runner.cpp", "src/ir/text_parser.cpp",
                                 "src/interpreter/interpreter.cpp",
                                 "src/typecheck/typecheck.cpp"], "-O1")
CXX32 = "i686-w64-mingw32-g++"

PROBE_PROGRAM = """\
def fmix(a: float[64], b: float[64]) -> float[64]:
    x: float[64] = a % b
    y: float[64] = a / b
    z: float[64] = x * y + a - b
    w: float[64] = z % 3.5
    if w > 0.5:
        return w + z
    return w - z


def imix(a: int[64], b: int[64]) -> int[64]:
    c: int[64] = a + b
    d: int[64] = a * 3
    e: int[64] = b * 5
    f: int[64] = c + d
    g: int[64] = d + e
    h: int[64] = e + f
    i: int[64] = 0
    t: int[64] = 0
    for i in range(10):
        t = t + c + d + e + f + g + h + i
        c = c + 1
        d = d + 2
        e = e + 3
        f = f + 4
        g = g + 5
        h = h + 6
    return t


def noisy(a: int[64], b: float[64]) -> int[64]:
    x: float[64] = b * 2.5
    print(a)
    print(x)
    y: float[64] = x % 1.5
    print(y)
    return a + 7


def mixed(a: int[64], b: float[64]) -> float[64]:
    c: float[64] = b * 3.0
    return c + a - 1.0


if __name__ == '__main__':
    print(imix(1, 2))
"""


def expected_fmix(a, b):
    x = math.fmod(a, b)
    y = a / b
    z = x * y + a - b
    w = math.fmod(z, 3.5)
    return w + z if w > 0.5 else w - z


def expected_imix(a, b):
    c, d, e = a + b, a * 3, b * 5
    f = c + d
    g = d + e
    h = e + f
    t = 0
    for i in range(10):
        t += c + d + e + f + g + h + i
        c, d, e, f, g, h = c + 1, d + 2, e + 3, f + 4, g + 5, h + 6
    return t


# (function, i0, i1, d0, d1, expected RET (None = ignore), expected DRET, stdout)
PROBES = [
    ("fmix", 0, 0, 17.5, 4.25, None, expected_fmix(17.5, 4.25), ""),
    ("fmix", 0, 0, -9.75, 2.5, None, expected_fmix(-9.75, 2.5), ""),
    ("imix", 3, 5, 0.0, 0.0, expected_imix(3, 5), None, ""),
    ("imix", -7, 11, 0.0, 0.0, expected_imix(-7, 11), None, ""),
    ("noisy", 41, 0, 0.0, 2.0, 48, None, "41\n5.0\n0.5\n"),
    ("mixed", 10, 0, 0.0, 1.5, None, 13.5, ""),
]


def run(cmd, **kw):
    # errors="replace": a corrupted frame can make a program print arbitrary bytes,
    # and that must read as a FAILED comparison, not crash the checker.
    try:
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                              env=ENV, **kw)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timed out")


def build(name, force):
    srcs, opt = SOURCES[name]
    exe = OUT / name
    newest = max((ROOT / s).stat().st_mtime for s in srcs)
    hdrs = [p for d in ("src/jit", "src/ir", "src/runtime", "src/interpreter", "src/typecheck")
            for p in (ROOT / d).glob("*.h")]
    newest = max([newest] + [h.stat().st_mtime for h in hdrs])
    if exe.exists() and exe.stat().st_mtime >= newest and not force:
        return exe
    print(f"  building {name} ...", flush=True)
    # 32-bit: i686 defaults to x87 arithmetic, which keeps 80-bit intermediates and
    # double-rounds; the backend emits SSE2, so the host (interpreter, helpers) must
    # compute in SSE2 too or the two would disagree in the last bit.
    sse = ["-msse2", "-mfpmath=sse"] if name.endswith("32.exe") else []
    r = subprocess.run([CXX32 if name.endswith("32.exe") else CXX, "-std=c++20", opt] + sse + [ f"-I{ROOT/'src'}", f"-I{ROOT/'src'/'jit'}",
                        "-static", "-Wl,--stack,33554432", "-o", str(exe)]
                       + [str(ROOT / s) for s in srcs],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        raise SystemExit(f"build of {name} failed")
    return exe


def to_ir(src: pathlib.Path):
    r = subprocess.run([sys.executable, str(FRONTEND), str(src)], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def wine_path(p):
    return "Z:" + str(p).replace("/", "\\")


def check_probe(exe, tmp):
    src = tmp / "abi.lio"
    src.write_text(PROBE_PROGRAM)
    ir = tmp / "abi.ir"
    ir.write_text(to_ir(src))
    fails = 0
    for fn, i0, i1, d0, d1, want_ret, want_dret, want_out in PROBES:
        cmd = RUNNER + [str(exe), str(ir), fn, str(i0), str(i1), repr(d0), repr(d1)]
        r = run(cmd, timeout=60)
        # The first wine process after a fresh start can race the wine server coming
        # up and print nothing at all. Only that (no result line whatsoever) is
        # retried; a probe that ran and reported CLOBBERED is a real failure.
        for _ in range(2):
            if "ABI" in r.stdout:
                break
            r = run(cmd, timeout=60)
        lines = r.stdout.replace("\r\n", "\n").splitlines()
        info = {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] if " " in ln else "" for ln in lines
                if ln.startswith(("RET", "DRET", "ABI"))}
        printed = "".join(ln + "\n" for ln in lines
                          if not ln.startswith(("RET", "DRET", "ABI")))
        problems = []
        if info.get("ABI") != "OK":
            problems.append(f"ABI {info.get('ABI', 'no result')}")
        if want_ret is not None and info.get("RET") != str(want_ret):
            problems.append(f"RET {info.get('RET')} != {want_ret}")
        if want_dret is not None:
            got = float(info.get("DRET", "nan"))
            if not math.isclose(got, want_dret, rel_tol=1e-12, abs_tol=1e-12):
                problems.append(f"DRET {got!r} != {want_dret!r}")
        if printed != want_out:
            problems.append(f"stdout {printed!r} != {want_out!r}")
        label = f"{fn}({i0}, {i1}, {d0}, {d1})"
        if problems:
            fails += 1
            print(f"  FAIL probe {label}: {'; '.join(problems)}")
        else:
            print(f"  ok   probe {label}: callee-saved RBX RDI RSI R12-R15 XMM6-XMM15 preserved")
    return fails


def check_suites(exe, tmp, quick):
    progs = sorted((ROOT / "tests" / "typed_regression").glob("*.lio")) + \
            sorted((ROOT / "tests" / "programs").glob("*.lio"))
    if quick:
        progs = progs[::6]
    fails, ran = 0, 0
    for f in progs:
        ir = to_ir(f)
        if ir is None:
            continue
        irp = tmp / "t.ir"
        irp.write_text(ir)
        inp = f.with_suffix(".in")
        stdin = inp.read_text() if inp.exists() else ""
        res = {}
        for mode in ("--interp", "--native"):
            r = run(RUNNER + [str(exe), str(irp), mode], input=stdin, timeout=60)
            res[mode] = (r.returncode, r.stdout.replace("\r\n", "\n"))
        ran += 1
        if res["--interp"] != res["--native"]:
            fails += 1
            print(f"  FAIL {f.name}: interpreter exit {res['--interp'][0]}, native exit "
                  f"{res['--native'][0]}")
    print(f"  {ran - fails}/{ran} programs identical on the Windows interpreter and native tier")
    return fails


def check_win32(exe, tmp, quick, runner=None):
    """4.17: the 32-bit build (i686 MinGW, run under wine).

    Every program is run with --native. When the x86-32 backend accepts it
    ("[tier1] native"), stdout, stderr and the exit status must equal the 64-bit
    interpreter's -- traps included, so the E0303/E0310/E0401 messages are compared
    byte for byte. When it refuses a construct it has no code for yet, the run must
    fall back to the interpreter and still be correct. A floor on the number of
    natively compiled programs stops a regression from quietly turning every
    program into an interpreter run."""
    runner = RUNNER if runner is None else runner
    ref = ROOT / "build" / "tier_runner"
    if not ref.exists():
        print("  SKIP: build/tier_runner (the 64-bit reference) is not built")
        return 0
    progs = sorted((ROOT / "tests" / "typed_regression").glob("*.lio")) + \
            sorted((ROOT / "tests" / "programs").glob("*.lio")) + \
            sorted((ROOT / "tests" / "x86_32").glob("*.lio"))
    if quick:
        progs = progs[::6] + sorted((ROOT / "tests" / "x86_32").glob("*.lio"))
    fails = ran = native = 0

    def lines(text):
        return [l for l in text.replace("\r", "").splitlines()
                if not l.startswith("[tier") and "ndis" not in l and not l.startswith("wine:")]

    for f in progs:
        ir = to_ir(f)
        if ir is None:
            continue
        irp = tmp / "t32.ir"
        irp.write_text(ir)
        inp = f.with_suffix(".in")
        stdin = inp.read_text() if inp.exists() else ""
        want = subprocess.run([str(ref), str(irp), "--interp"], capture_output=True, text=True,
                              errors="replace", input=stdin)
        got = run(runner + [str(exe), str(irp), "--native"], input=stdin, timeout=60)
        ran += 1
        ran_native = "[tier1] native" in got.stderr
        native += ran_native
        same = (want.returncode, want.stdout, lines(want.stderr)) == \
               (got.returncode, got.stdout.replace("\r\n", "\n"), lines(got.stderr))
        if ran_native and not same:
            fails += 1
            print(f"  FAIL {f.name}: native x86-32 differs (exit {got.returncode}, reference {want.returncode})")
        elif not ran_native:
            # refused: the interpreter fallback must be right too
            fb = run(runner + [str(exe), str(irp), "--auto"], input=stdin, timeout=60)
            if (want.returncode, want.stdout) != (fb.returncode, fb.stdout.replace("\r\n", "\n")):
                fails += 1
                print(f"  FAIL {f.name}: interpreter fallback differs")
    floor = 30 if quick else 125
    print(f"  {ran} programs: {native} ran on the x86-32 native tier, all identical to the 64-bit reference; "
          f"the other {ran - native} fell back to the interpreter correctly")
    if native < floor:
        fails += 1
        print(f"  FAIL: only {native} programs compiled natively (expected at least {floor})")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--require", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    if shutil.which(CXX) is None or (not NATIVE_WINDOWS and shutil.which("wine") is None):
        msg = "MinGW (x86_64-w64-mingw32-g++) and wine are needed for the Win64 ABI checks"
        if args.require:
            print(f"ERROR: {msg}")
            return 1
        print(f"SKIP: {msg}")
        return 0
    # wine's server exits a few seconds after the last process, and restarting it
    # costs about 4 s per run, so start one that stays (wineserver -p).
    if not NATIVE_WINDOWS and shutil.which("wineserver"):
        subprocess.Popen(["wineserver", "-p"], env=ENV, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "work"
    tmp.mkdir(exist_ok=True)
    probe = build("win64_abi_probe.exe", args.rebuild)
    runner = build("tier_runner_O0.exe", args.rebuild)
    print("ABI conformance (callee-saved registers, shadow space, alignment):")
    fails = check_probe(probe, tmp)
    print("program suites on the Windows build (-O0 helpers expose a missing shadow space):")
    fails += check_suites(runner, tmp, args.quick)
    if shutil.which(CXX32):
        runner32 = build("tier_runner32.exe", args.rebuild)
        print("32-bit build (i686 MinGW): x86-32 native tier vs the 64-bit reference:")
        fails += check_win32(runner32, tmp, args.quick)
    else:
        print(f"SKIP 32-bit checks: {CXX32} is not installed")
    if fails:
        print(f"\n{fails} Win64 check(s) FAILED")
        return 1
    print("\nall Win64 checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
