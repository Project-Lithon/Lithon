#!/usr/bin/env python3
"""Large stack frames run natively (4.17 follow-up: matrix_bench).

A frame is no longer limited to 32 KB. As C/C++ (-fstack-clash-protection, __chkstk) and Rust
(probestack) do, the prologue touches a frame page by page; and, beyond what they do, the worst-case
stack of the WHOLE program is computed from its call graph (src/jit/stack_budget.h) and the program is
given a stack of exactly that size, on a dedicated thread when it exceeds the starting thread's.

Checks, for every program in tests/big_frames:
  * it runs on the native tier and prints the same stdout, stderr and exit status as the interpreter
    (the runaway recursion must trap with E0401 at the same depth in both);
  * the prologue of a big frame really contains the page probes (checked in the disassembly, because
    wine and Linux commit stack pages lazily and would not notice a missing probe);
  * the computed worst-case stack is the right size (a runaway 8 KiB-frame recursion needs
    4096 * 8 KiB, an 8 MiB local list needs 8 MiB, ...);
  * a program whose stack need exceeds LITHON_NATIVE_STACK_LIMIT is refused natively and still runs
    correctly in the interpreter.
"""
import os, pathlib, re, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNER = ROOT / "build" / "tier_runner"
FRONTEND = ROOT / "src" / "frontend" / "frontend.py"
fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'' if ok else ': ' + str(detail)[:300]}")
    fails += not ok


def run(cmd, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", env=e, timeout=300)


def lines(text):
    return [l for l in text.splitlines() if not l.startswith("[tier")]


def main():
    if not RUNNER.exists():
        print("SKIP: build/tier_runner is not built")
        return 0
    tmp = pathlib.Path(tempfile.mkdtemp())
    irs = {}
    for src in sorted((ROOT / "tests" / "big_frames").glob("*.lio")):
        fe = run([sys.executable, str(FRONTEND), str(src)])
        ir = tmp / (src.stem + ".ir")
        ir.write_text(fe.stdout)
        irs[src.stem] = ir

    print("native == interpreter on large frames:")
    for name, ir in irs.items():
        a = run([str(RUNNER), str(ir), "--interp"])
        b = run([str(RUNNER), str(ir), "--native"])
        ran = "[tier1] native" in b.stderr
        same = (a.returncode, a.stdout, lines(a.stderr)) == (b.returncode, b.stdout, lines(b.stderr))
        check(f"{name}: native, identical stdout/stderr/exit", ran and same,
              f"ran={ran} {b.stderr[-200:]!r}")

    print("page probes in the prologue:")
    def disasm(ir):
        r = run([str(RUNNER), str(ir), "--dump-hex"])
        code = bytes(int(h, 16) for h in re.findall(r"\b[0-9a-f]{2}\b", r.stderr))
        p = tmp / "code.bin"
        p.write_bytes(code)
        d = run(["objdump", "-D", "-b", "binary", "-mi386:x86-64", "-Mintel", str(p)]).stdout
        return [l.split("\t", 2)[-1].strip() for l in d.splitlines() if "\t" in l]
    try:
        loop = disasm(irs["matrix"])[:14]
        check("a big frame probes with a loop (sub r11,4096 ... cmp r11,rsp ... mov r10,[rsp])",
              any("sub    r11,0x1000" in l for l in loop) and any("cmp    r11,rsp" in l for l in loop)
              and any("QWORD PTR [rsp]" in l for l in loop), loop)
        unrolled = disasm(irs["medium"])[:12]
        check("a few-page frame probes each page, highest first, then the bottom",
              [l for l in unrolled if "[rbp-0x" in l and "QWORD PTR" in l][:2] == 
              ["mov    r10,QWORD PTR [rbp-0x1000]", "mov    r10,QWORD PTR [rbp-0x2000]"]
              and any("QWORD PTR [rsp]" in l for l in unrolled), unrolled)
    except FileNotFoundError:
        print("  SKIP: objdump not found")

    print("the computed worst-case stack:")
    def needed(name, limit=1000):
        r = run([str(RUNNER), str(irs[name]), "--native"], {"LITHON_NATIVE_STACK_LIMIT": str(limit)})
        m = re.search(r"worst-case stack of (\d+) bytes", r.stderr)
        return int(m.group(1)) if m else None
    margin = 512 * 1024
    n = needed("big_list")
    check("an 8 MiB local list needs about 8 MiB", n is not None and 8 * 2**20 <= n <= 8 * 2**20 + margin + 4096, n)
    n = needed("runaway")
    check("a runaway recursion with 8 KiB frames needs 4096 frames of 8 KiB", n is not None
          and 4096 * 8192 <= n <= 4096 * 8192 + 4096 * 512 + margin, n)
    n = needed("recursion_big_frame")
    check("a recursion with 16 KiB frames needs 4096 frames of 16 KiB", n is not None
          and 4096 * 16384 <= n <= 4096 * 16384 + 4096 * 512 + margin, n)
    n = needed("mutual")
    check("mutual recursion is bounded as one group", n is not None and 4096 * 4096 <= n <= 4096 * 4608 + margin, n)

    print("over the budget: refused natively, still correct:")
    for name in ("big_list", "runaway"):
        a = run([str(RUNNER), str(irs[name]), "--interp"])
        forced = run([str(RUNNER), str(irs[name]), "--native"], {"LITHON_NATIVE_STACK_LIMIT": "1000000"})
        auto = run([str(RUNNER), str(irs[name]), "--auto"], {"LITHON_NATIVE_STACK_LIMIT": "1000000"})
        check(f"{name}: --native refuses ({'worst-case stack' in forced.stderr}) and --auto equals the interpreter",
              "worst-case stack" in forced.stderr and "[tier1]" not in forced.stderr
              and (a.returncode, a.stdout, lines(a.stderr)) == (auto.returncode, auto.stdout, lines(auto.stderr)),
              auto.stderr[-200:])

    print("\nall big-frame checks passed" if not fails else f"\n{fails} big-frame check(s) FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
