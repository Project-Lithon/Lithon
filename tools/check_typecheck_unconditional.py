#!/usr/bin/env python3
"""The type checker must run on EVERY module, annotated or not.

This exists because of a specific regression. The three binaries used to guard
the checker behind an all-or-nothing predicate -- scan the module, and if it had
no return type, no parameter type and no typed store anywhere, skip check_module
entirely. Two things went wrong with that:

  1. Adding an annotation became a whole-module semantic switch. Annotate one
     variable in a file and every other unannotated variable in that file
     suddenly became a type error, which is not how declaring a type works in
     any language a reader would recognise.
  2. Worse, the gate was silently exempting most of the corpus. 14 of the 18
     programs in tests/programs/ had zero annotations, so they had never been
     typechecked at all -- they passed because nobody was looking.

So this asserts the strong property directly: a module with no annotations is
still checked, and a module whose only problem is a missing annotation is
rejected by all three binaries. The "must accept" cases are what keep the test
honest -- without them, "reject everything" would pass.

    tools/check_typecheck_unconditional.py
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Every binary that runs a module end to end. All three had their own private
# copy of the gate, so all three are checked: deleting one copy would leave the
# other two still skipping the checker.
BINARIES = {
    "hello":        ([ROOT / "build" / "hello"], []),
    "lithon_jit":   ([ROOT / "build" / "lithon_jit"], []),
    "tier_runner":  ([ROOT / "build" / "tier_runner"], ["--interp"]),
}

# (name, ir, must_be_rejected, substring the diagnostic must contain)
CASES = [
    (
        "unannotated variable is rejected, not skipped",
        """
function __main__():
block0:
    %0 = const_i64 1
    store x, %0
    %1 = load x
    call print, %1
    return
""",
        True,
        "without a type annotation",
    ),
    (
        "unannotated function parameter is rejected",
        """
function f(a):
block0:
    %0 = load a
    call print, %0
    return

function __main__():
block0:
    %0 = const_i64 1
    %1 = call f, %0
    return
""",
        True,
        "parameter 'a' has no type annotation",
    ),
    (
        "a module that annotates only its return type is still accepted",
        """
function f() -> int[64]:
block0:
    %0 = const_i64 1
    return %0

function __main__():
block0:
    %0 = call f
    call print, %0
    return
""",
        False,   # this one IS annotated; it is the control for the case below
        None,
    ),
    (
        "a real type error is still caught once annotations exist",
        """
function __main__():
block0:
    %0 = const_i64 1
    store x, %0 : float[64]
    return
""",
        True,
        "must hold a float",
    ),
    (
        "a fully annotated module is accepted",
        """
function __main__():
block0:
    %0 = const_i64 1
    store x, %0 : int[64]
    %1 = load x
    call print, %1
    return
""",
        False,
        None,
    ),
    (
        "an annotated list program is accepted (lists are not a special case)",
        """
function __main__():
block0:
    store xs : list[int[64],4]
    %0 = const_i64 0
    %1 = const_i64 9
    IndexStore xs, %0, %1
    %2 = Index xs, %0
    call print, %2
    return
""",
        False,
        None,
    ),
]


def main() -> int:
    missing = [n for n, (argv, _) in BINARIES.items() if not argv[0].exists()]
    if missing:
        print(f"missing binaries: {', '.join(missing)} -- build first")
        return 2

    tmp = ROOT / "build" / "_typecheck_unconditional.ir"
    failures: list[str] = []

    for name, ir, want_reject, needle in CASES:
        tmp.write_text(ir.lstrip("\n"))
        for bin_name, (argv, flags) in BINARIES.items():
            run = subprocess.run([str(argv[0]), str(tmp), *flags],
                                 capture_output=True, text=True, timeout=30)
            rejected = run.returncode != 0
            detail = f"[{'FAIL' if rejected != want_reject else 'ok'}] {name} / {bin_name}"
            if rejected != want_reject:
                want = "reject" if want_reject else "accept"
                detail += (f"  (wanted {want}, rc={run.returncode})\n"
                           f"        stderr: {run.stderr.strip()[:200]}")
                failures.append(detail)
            elif want_reject and needle and needle not in run.stderr:
                detail += (f"  (rejected, but the diagnostic never mentioned"
                           f" {needle!r})\n        stderr: {run.stderr.strip()[:200]}")
                failures.append(detail)
            else:
                detail += "  -> rejected as required" if want_reject else "  -> accepted"
            print(detail)

    tmp.unlink(missing_ok=True)

    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        return 1
    print(f"\n{len(CASES) * len(BINARIES)} checks passed: "
          f"the type checker runs on every module in all {len(BINARIES)} binaries")
    return 0


if __name__ == "__main__":
    sys.exit(main())