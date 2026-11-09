#!/usr/bin/env python3
"""4.17: the System V i386 target, executed natively (no wine, no emulator).

A 64-bit Linux kernel runs 32-bit ELF binaries, so the x86-32 backend's System V path
-- 16-byte stack alignment at every call, ELF/mmap executable memory, glibc's printf
-- can be run for real. This builds tier_runner as a static 32-bit ELF and runs the
same corpus + tests/x86_32 programs as tools/check_win64.py through --native,
comparing with the 64-bit reference byte for byte.

The 32-bit toolchain is taken from the system when `g++ -m32 -static` works
(apt install g++-multilib). Otherwise the few packages needed are downloaded with
`apt-get download` (no root) and unpacked under build/sysv32/root. If neither is
possible the check is skipped (--require makes that an error).

    tools/check_sysv32.py [--quick] [--require] [--rebuild]
"""
import argparse, pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import check_win64  # noqa: E402

OUT = ROOT / "build" / "sysv32"
PKGS = ["libc6-i386", "libc6-dev-i386", "lib32gcc-15-dev", "lib32stdc++-15-dev",
        "lib32gcc-s1", "lib32stdc++6", "linux-libc-dev"]
HELLO = '#include <cstdio>\n#include <string>\nint main(){std::string s="x";std::printf("%s",s.c_str());}\n'


def try_build(flags, tmp):
    src = tmp / "h.cpp"
    src.write_text(HELLO)
    r = subprocess.run(["g++", "-m32", "-static"] + flags + ["-o", str(tmp / "h"), str(src)],
                       capture_output=True, text=True)
    if r.returncode:
        return False
    return subprocess.run([str(tmp / "h")], capture_output=True, text=True).stdout == "x"


def local_flags(root):
    gcc_ver = sorted(p.name for p in (root / "usr/lib/gcc/x86_64-linux-gnu").glob("*"))[-1]
    gcc32 = root / "usr/lib/gcc/x86_64-linux-gnu" / gcc_ver / "32"
    return [f"-isystem{root}/usr/include/x86_64-linux-gnu/c++/{gcc_ver}/32",
            f"-B{gcc32}", f"-B{root}/usr/lib32", f"-L{gcc32}", f"-L{root}/usr/lib32",
            f"-isystem{root}/usr/include/x86_64-linux-gnu", f"-isystem{root}/usr/include",
            "-isystem/usr/include/x86_64-linux-gnu"]


def toolchain(tmp):
    if try_build([], tmp):
        return []
    root = OUT / "root"
    if not root.exists():
        if shutil.which("apt-get") is None or shutil.which("dpkg") is None:
            return None
        print("  fetching the 32-bit glibc/libstdc++ packages (no root needed) ...", flush=True)
        dl = OUT / "debs"
        dl.mkdir(parents=True, exist_ok=True)
        for pkg in PKGS:
            subprocess.run(["apt-get", "download", pkg], cwd=dl, capture_output=True)
        debs = list(dl.glob("*.deb"))
        if not debs:
            return None
        root.mkdir(parents=True, exist_ok=True)
        for d in debs:
            subprocess.run(["dpkg", "-x", str(d), str(root)], capture_output=True)
    flags = local_flags(root)
    return flags if try_build(flags, tmp) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--require", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        flags = toolchain(pathlib.Path(t))
    if flags is None:
        msg = "no usable 32-bit Linux toolchain (apt install g++-multilib)"
        print(("ERROR: " if args.require else "SKIP: ") + msg)
        return 1 if args.require else 0
    exe = OUT / "tier_runner32"
    srcs = ["src/jit/tier_runner.cpp", "src/ir/text_parser.cpp", "src/interpreter/interpreter.cpp",
            "src/typecheck/typecheck.cpp"]
    hdrs = [p for d in ("src/jit", "src/ir", "src/runtime", "src/interpreter", "src/typecheck")
            for p in (ROOT / d).glob("*.h")]
    newest = max(p.stat().st_mtime for p in [ROOT / s for s in srcs] + hdrs)
    if args.rebuild or not exe.exists() or exe.stat().st_mtime < newest:
        print("  building tier_runner32 (static 32-bit ELF) ...", flush=True)
        r = subprocess.run(["g++", "-m32", "-static", "-std=c++20", "-O1", "-msse2", "-mfpmath=sse",
                            f"-I{ROOT/'src'}", f"-I{ROOT/'src'/'jit'}", "-o", str(exe)] + flags +
                           [str(ROOT / s) for s in srcs], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-1500:])
            return 1
    tmp = OUT / "work"
    tmp.mkdir(exist_ok=True)
    print("System V i386 (native 32-bit ELF): x86-32 native tier vs the 64-bit reference:")
    fails = check_win64.check_win32(exe, tmp, args.quick, runner=[])
    # The alignment probe: the generated code traps (int3) if ESP is not 16-byte aligned
    # at any call. A program that dies there differs from the reference and fails.
    import os
    os.environ["LITHON_CHECK_ALIGN"] = "1"
    check_win64.ENV["LITHON_CHECK_ALIGN"] = "1"
    print("System V i386 with the stack-alignment probe (int3 on a misaligned call):")
    fails += check_win64.check_win32(exe, tmp, args.quick, runner=[])
    print("\nall System V i386 checks passed" if not fails else f"\n{fails} System V i386 check(s) FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
