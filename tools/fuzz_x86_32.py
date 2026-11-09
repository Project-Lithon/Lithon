#!/usr/bin/env python3
"""4.17: differential fuzzing of the x86-32 native tier.

Random programs from tools/fuzz_diff.py's generator are run with --native on the
32-bit build (i686 MinGW under wine) and compared, stdout + stderr + exit status,
with the 64-bit interpreter. Programs the 32-bit backend refuses are counted but
not compared (they fall back to the interpreter, which tools/check_win64.py covers).
It needs the build that tools/check_win64.py creates: run that first.

    tools/fuzz_x86_32.py --count 100            # default (int) programs
    tools/fuzz_x86_32.py --floats --count 100
"""
import argparse, os, pathlib, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import fuzz_diff  # noqa: E402

EXE32 = ROOT / "build" / "win64" / "tier_runner32.exe"
REF = ROOT / "build" / "tier_runner"
ENV = dict(os.environ, WINEDEBUG="-all")


def lines(text):
    return [l for l in text.replace("\r", "").splitlines()
            if not l.startswith("[tier") and "ndis" not in l and not l.startswith("wine:")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--sysv", action="store_true", help="run the native 32-bit ELF (System V i386), not Win32 under wine")
    ap.add_argument("--floats", action="store_true")
    ap.add_argument("--mod", action="store_true")
    ap.add_argument("--bitwise", action="store_true")
    ap.add_argument("--break", dest="brk", action="store_true")
    ap.add_argument("--diamond", action="store_true")
    ap.add_argument("--lsr", action="store_true")
    ap.add_argument("--phi", action="store_true")
    ap.add_argument("--oop", action="store_true", help="cycle through the seven class-model shapes")
    args = ap.parse_args()
    exe = ROOT / "build" / "sysv32" / "tier_runner32" if args.sysv else EXE32
    runner = [] if args.sysv else ["wine"]
    if not exe.exists() or not REF.exists():
        print("SKIP: run tools/check_sysv32.py / tools/check_win64.py first (needs the 32-bit runner and build/tier_runner)")
        return 0
    if not args.sysv:
        subprocess.Popen(["wineserver", "-p"], env=ENV, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    native = refused = bad = skipped = 0
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for seed in range(args.seed, args.seed + args.count):
            gen = fuzz_diff.Gen(seed, False, floats=args.floats)
            oop_shapes = ["program_oop", "program_oop_inherit", "program_oop_containers",
                          "program_oop_multiple", "program_oop_special", "program_oop_decorators",
                          "program_oop_optional"]
            if args.oop:
                src = getattr(gen, oop_shapes[seed % len(oop_shapes)])()
            elif args.floats:
                src = gen.float_program()
            elif args.mod:
                src = gen.program_mod()
            elif args.bitwise:
                src = gen.program_bitwise()
            elif args.brk:
                src = gen.program_break()
            elif args.diamond:
                src = gen.program_diamond()
            elif args.lsr:
                src = gen.program_lsr()
            elif args.phi:
                src = gen.program_phi()
            else:
                src = gen.program()
            lio, ir = d / "p.lio", d / "p.ir"
            lio.write_text(src)
            fe = subprocess.run([sys.executable, str(ROOT / "src/frontend/frontend.py"), str(lio)],
                                capture_output=True, text=True)
            if fe.returncode:
                skipped += 1
                continue
            ir.write_text(fe.stdout)
            want = subprocess.run([str(REF), str(ir), "--interp"], capture_output=True, text=True,
                                  errors="replace", timeout=60)
            try:
                got = subprocess.run(runner + [str(exe), str(ir), "--native"], capture_output=True,
                                     text=True, errors="replace", env=ENV, timeout=60)
            except subprocess.TimeoutExpired:
                print(f"seed {seed}: 32-bit run timed out")
                bad += 1
                continue
            if "[tier1] native" not in got.stderr:
                refused += 1
                continue
            native += 1
            if (want.returncode, want.stdout, lines(want.stderr)) != \
               (got.returncode, got.stdout.replace("\r\n", "\n"), lines(got.stderr)):
                bad += 1
                keep = ROOT / "fuzz_failures"
                keep.mkdir(exist_ok=True)
                (keep / f"x86_32_seed{seed}.lio").write_text(src)
                print(f"seed {seed}: MISMATCH (saved fuzz_failures/x86_32_seed{seed}.lio)")
    print(f"{native} compared natively, {refused} refused (fell back), {skipped} rejected by the frontend, "
          f"{bad} mismatches")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
