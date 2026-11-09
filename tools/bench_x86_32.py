#!/usr/bin/env python3
"""4.17.5: x86-32 performance. Times the benchmark programs in tools/bench32 on the
32-bit build (interpreter and native tier, under wine) and, for reference, on the
64-bit native tier. Prints seconds (best of N) and the native-vs-interpreter ratio.
Wine adds a fixed ~0.1 s start-up to every 32-bit run, which is shown separately.

    tools/bench_x86_32.py [--runs 3] [name ...]
"""
import argparse, os, pathlib, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXE32 = ROOT / "build" / "win64" / "tier_runner32.exe"
REF = ROOT / "build" / "tier_runner"
ENV = dict(os.environ, WINEDEBUG="-all")


def timed(cmd, runs):
    best, out = None, ""
    for _ in range(runs):
        t = time.perf_counter()
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", env=ENV, timeout=600)
        dt = time.perf_counter() - t
        out = r.stdout.replace("\r\n", "\n")
        best = dt if best is None else min(best, dt)
    return best, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("names", nargs="*")
    args = ap.parse_args()
    if not EXE32.exists() or not REF.exists():
        print("SKIP: run tools/check_win64.py first")
        return 0
    subprocess.Popen(["wineserver", "-p"], env=ENV, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    tmp = ROOT / "build" / "win64" / "work"
    tmp.mkdir(parents=True, exist_ok=True)
    empty = tmp / "empty.ir"
    empty.write_text("function __main__():\nblock0:\n    return\n")
    startup, _ = timed(["wine", str(EXE32), str(empty), "--interp"], args.runs)
    print(f"wine start-up: {startup:.3f}s (included in every 32-bit time below)\n")
    print(f"{'program':14}{'32 interp':>11}{'32 native':>11}{'speedup':>9}{'64 native':>11}{'32/64':>8}  result")
    progs = sorted((ROOT / "tools" / "bench32").glob("*.lio"))
    if args.names:
        progs = [p for p in progs if p.stem in args.names]
    bad = 0
    for p in progs:
        ir = tmp / (p.stem + ".ir")
        fe = subprocess.run([sys.executable, str(ROOT / "src/frontend/frontend.py"), str(p)], capture_output=True, text=True)
        ir.write_text(fe.stdout)
        ti, oi = timed(["wine", str(EXE32), str(ir), "--interp"], 1)
        tn, on = timed(["wine", str(EXE32), str(ir), "--native"], args.runs)
        t64, o64 = timed([str(REF), str(ir), "--native"], args.runs)
        ok = oi == on == o64
        bad += not ok
        print(f"{p.stem:14}{ti:10.2f}s{tn:10.2f}s{ti / tn:8.1f}x{t64:10.2f}s{tn / t64:7.1f}x  "
              f"{'ok' if ok else 'OUTPUT DIFFERS'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
