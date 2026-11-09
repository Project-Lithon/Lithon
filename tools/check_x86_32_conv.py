#!/usr/bin/env python3
"""4.17: the exported C calling conventions (cdecl, stdcall, fastcall) of the x86-32 backend.

src/jit/x86_32_conv_test.cpp compiles tests/x86_32_conv/conv.lio with the export thunks
and calls each `<fn>$cdecl` / `$stdcall` / `$fastcall` entry from C through a function
pointer carrying the real calling-convention attribute, thousands of times. It runs on
both 32-bit targets: Win32 (i686 MinGW under wine) and System V i386 (native ELF).
Skipped per target when its toolchain is missing; --require makes that an error.
"""
import argparse, os, pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
ENV = dict(os.environ, WINEDEBUG="-all")
SRC = ROOT / "src/jit/x86_32_conv_test.cpp"
COMMON = ["-std=c++20", "-O0", "-msse2", "-mfpmath=sse", f"-I{ROOT/'src'}", f"-I{ROOT/'src'/'jit'}"]
EXTRA = [str(ROOT / "src/ir/text_parser.cpp"), str(ROOT / "src/typecheck/typecheck.cpp")]


def run_one(label, cmd_build, runner, ir):
    r = subprocess.run(cmd_build, capture_output=True, text=True)
    if r.returncode:
        print(f"  FAIL {label}: build error\n{r.stderr[-1500:]}")
        return 1
    exe = cmd_build[cmd_build.index("-o") + 1]
    out = subprocess.run(runner + [exe, str(ir)], capture_output=True, text=True, errors="replace",
                         env=ENV, timeout=120)
    text = out.stdout.replace("\r\n", "\n").strip().splitlines()
    last = text[-1] if text else "(no output)"
    ok = out.returncode == 0 and last.startswith("ok")
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: {last}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--require", action="store_true")
    args = ap.parse_args()
    fails = ran = 0
    with tempfile.TemporaryDirectory() as t:
        t = pathlib.Path(t)
        fe = subprocess.run([sys.executable, str(ROOT / "src/frontend/frontend.py"),
                             str(ROOT / "tests/x86_32_conv/conv.lio")], capture_output=True, text=True)
        ir = t / "conv.ir"
        ir.write_text(fe.stdout)
        if shutil.which("i686-w64-mingw32-g++") and shutil.which("wine"):
            subprocess.Popen(["wineserver", "-p"], env=ENV, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            fails += run_one("Win32 (MinGW under wine)",
                             ["i686-w64-mingw32-g++"] + COMMON + ["-static", "-o", str(t / "conv.exe"), str(SRC)] + EXTRA,
                             ["wine"], ir)
            ran += 1
        import check_sysv32
        flags = check_sysv32.toolchain(t)
        if flags is not None:
            fails += run_one("System V i386 (native ELF)",
                             ["g++", "-m32", "-static"] + COMMON + flags + ["-o", str(t / "conv"), str(SRC)] + EXTRA,
                             [], ir)
            ran += 1
    if ran == 0:
        print(("ERROR: " if args.require else "SKIP: ") + "no 32-bit toolchain available")
        return 1 if args.require else 0
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
