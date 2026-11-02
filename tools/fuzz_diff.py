#!/usr/bin/env python3
"""
Differential fuzzer: interpreter vs JIT (tier_runner --auto).

Generates random, annotation-complete Lithon programs, and for each one
that the real typechecker accepts (hello's own exit code is the gate --
we do not reimplement V1_SPEC's rules here), runs it on both execution
engines and diffs stdout + exit code. On the first mismatch, the failing
program is minimized (repeated single-statement-removal passes, each
re-checked against the SAME two gates: still typechecks, still mismatches)
and the minimal repro is written to fuzz_failures/ as both .lio and .ir.

CPython is used as a secondary oracle: if the interpreter itself disagrees
with CPython, that's a real bug too, just a different one (frontend/
interpreter vs V1_SPEC semantics, not JIT vs interpreter) -- reported
separately so it doesn't get confused with the primary JIT-vs-interpreter
comparison this tool exists to make reliable.

    tools/fuzz_diff.py                  # 500 programs, exit 1 on any bug
    tools/fuzz_diff.py --count 5000
    tools/fuzz_diff.py --seed 12345 --count 200

KNOWN LANGUAGE DIVERGENCE (found by this tool): Lithon lowers `for v in range(n)` to a counter that
IS `v` (src/frontend/frontend.py build_for), so unlike CPython:
  * after the loop, v == n (CPython: n-1);
  * a zero-iteration loop overwrites v with 0 (CPython leaves it untouched);
  * assigning to v inside the body changes the iteration count (can loop forever).
`for/else` and `while/else` are also silently dropped by the frontend. Until the language semantics are
decided, the generator uses dedicated loop variables that are never read or assigned outside their own
loop (so the CPython oracle stays meaningful); --shared-loop-vars generates the diverging shape.

AOT is not compared here: no AOT binary emitter exists in this repo yet
(see the src/ tree -- there is no ELF/PE writer anywhere). This tool
compares the two engines that actually exist. Extend CONFIGS/engines here
once a third one does.

    tools/fuzz_diff.py --floats          # float programs: arithmetic, compares, printing
    tools/fuzz_diff.py --mod             # integer modulo: powers of two, idiv, zero divisor
    tools/fuzz_diff.py --bitwise         # & | ^ << >>: both encodings, count bounds, RCX hazard

--floats is a separate mode rather than a tweak to the int generators because
the int generators annotate every variable int[64], and a float cannot be
declared that way. It emits flat, unannotated float programs instead. That
mode is the only thing here that covers float codegen at all, and it has
already paid for itself: it is what found a float temporary being clobbered
across a call, `%g` picking exponent notation by precision rather than by
value, std::stod rejecting every subnormal literal, and a NaN divisor taking
the division-by-zero trap.

--mod exists because modulo is the one operator where the CPython oracle
cannot referee the general case: C's `%` truncates toward zero and Python's
floors, so the two agree only for non-negative operands. --mod therefore
generates non-negative dividends and stays a meaningful CI gate, and
--mod-negatives opts into the diverging shape (interpreter vs JIT is still
enforced; the CPython comparison is expected to report the language gap, the
same way --shared-loop-vars does).
"""
import argparse
import ast
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNROLL_DIAMONDS = False
ACCUM_UNROLL = False
# --oop: see Gen.program_oop and Gen.program_oop_inherit. When set, the CPython
# oracle is not required to agree on the ORDER of `__del__` output. Lithon pins a
# reference's drop to the exit of the scope that owns it (a loop body releases
# each iteration, an early return releases the path's references), whereas
# CPython keeps a name alive until the frame or module it was bound in tears
# down. Both engines in this codebase agree with each other exactly -- that is
# the gate -- but they can disagree with CPython on the interleaving. This is the
# same treatment --mod-negatives gives the floored-vs-truncated `%%` gap.
# Alternate seeds use the flat 4.10.1 shape, the 4.10.2 inheritance shape and
# the 4.10.3 class-references-in-containers shape, so one --oop run exercises
# all three.
OOP_MODE = False
# --phi: run the JIT twice, with and without the SSA pipeline, and require both
# to match the interpreter. The plain run is the control: it proves the pipeline
# did not change the answer, rather than only proving the pipeline agrees with
# the interpreter on some other program's terms.
SSA_MODE = False
# --break: also compile with the direct-Phi backend, because break/continue add
# extra loop edges and the direct-Phi emitter is where a mis-wired predecessor
# would silently reset the counter (a hang, not a wrong number).
DIRECT_PHI_MODE = False
FRONTEND = ROOT / "src" / "frontend" / "frontend.py"
HELLO = ROOT / "build" / "hello"
TIER_RUNNER = ROOT / "build" / "tier_runner"

VARS = ["a", "b", "c", "d", "e", "f", "g", "h"]
LOOPVARS = ["i", "j", "k", "l"]      # never read or assigned outside their own loop (see docstring)

TIMEOUT = 15
TIMEOUT_RC = 124                     # returncode reported for a run that exceeded TIMEOUT


def run(cmd, **kw):
    """Like subprocess.run, but a timeout is returned as rc=TIMEOUT_RC instead of raised, so an
    infinite loop in one engine is a finding to report, not a crash of the fuzzer."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, **kw)
    except subprocess.TimeoutExpired as e:
        def txt(x):
            return x.decode(errors="replace") if isinstance(x, bytes) else (x or "")
        return subprocess.CompletedProcess(cmd, TIMEOUT_RC, txt(e.stdout), txt(e.stderr))


# ---------------------------------------------------------------------
# Program generation. Every binding is explicitly typed (V1_SPEC 0.6.1),
# every function fully annotated (0.6.8), for-loop variables predeclared
# with a value before the loop (0.6.6), and only already-declared
# variables are mutated inside if/else bodies (so nothing trips the
# definite-assignment check at 0.6.10 for reasons unrelated to what
# we're testing). Magnitudes stay small so int[64] overflow -- a real,
# deliberate divergence from Python's arbitrary-precision ints -- never
# fires and muddies the diff.
# ---------------------------------------------------------------------
class Gen:
    # Float mode. The int generator above cannot see any of the float
    # machinery: it emits no float literals, never divides, and compares only
    # ints, so float codegen was entirely uncovered by the fuzzer until this
    # mode existed -- which is how a float temporary live across a `print`
    # survived in a caller-saved XMM, and how `%g` choosing exponent notation
    # by precision instead of by value went unnoticed.
    #
    # Deliberate choices, each of which is a case that has actually been wrong:
    #   * `/` is generated, so the zero-divisor trap and the NaN-divisor
    #     non-trap are both exercised.
    #   * float and int atoms are mixed in the same expression, so the
    #     int-to-double promotion and mixed comparison paths get hit.
    #   * literals include subnormals and huge/tiny magnitudes, because the
    #     round-trip formatter has a different code path for each and
    #     std::stod used to reject every subnormal outright.
    #   * a `print` is emitted *between* statements so values are live across
    #     a call, which is the register-allocator case above.
    FLOAT_VARS = ["p", "q", "r", "s"]

    # Small decimals print as themselves; the wide ones stress the exponent
    # threshold (CPython switches to exponent form only below 1e-4 or at/above
    # 1e16) and the subnormals stress the short-round-trip search.
    FLOAT_LITERALS = [
        "0.0", "-0.0", "1.0", "-1.0", "0.5", "2.5", "3.5", "7.0", "0.1",
        "1e16", "1e15", "1e-5", "1e-4", "1e300", "1e-300", "1.5e-8",
        "5e-324", "2.2250738585072014e-308", "1.7976931348623157e308",
        "1.0000000000000002", "0.30000000000000004", "-2.5", "1e6", "1e-6",
    ]

    def __init__(self, seed, shared_loop_vars=False, floats=False, mod_negatives=False):
        self.r = random.Random(seed)
        self.lines = []
        self.declared = set()
        self.float_declared = set()
        self.shared_loop_vars = shared_loop_vars
        self.floats = floats
        self.mod_negatives = mod_negatives
        self.pool = self.FLOAT_VARS if floats else VARS

    def emit(self, indent, s):
        self.lines.append("    " * indent + s)

    def float_atom(self):
        if self.float_declared and self.r.random() < 0.7:
            return self.r.choice(sorted(self.float_declared))
        return self.r.choice(self.FLOAT_LITERALS)

    def atom(self):
        if not self.floats:
            if self.declared and self.r.random() < 0.7:
                return self.r.choice(sorted(self.declared))
            return str(self.r.randint(0, 9))
        # Float mode mixes int and float atoms freely: an int in a float
        # expression is what forces the cvtsi2sd promotion, and a float in an
        # otherwise-int context is what forces the guard to infer a float kind.
        k = self.r.random()
        if k < 0.5 and self.float_declared:
            return self.r.choice(sorted(self.float_declared))
        if k < 0.8:
            return self.r.choice(self.FLOAT_LITERALS)
        if self.declared and self.r.random() < 0.5:
            return self.r.choice(sorted(self.declared))
        return str(self.r.randint(0, 9))

    def fresh_var(self, exclude=()):
        """A VARS-pool name that is not already declared in this scope."""
        free = [v for v in self.pool if v not in self.declared and v not in exclude]
        if not free:
            return self.r.choice(self.pool)
        return self.r.choice(free)

    def expr(self, depth=0):
        k = self.r.random()
        if depth >= 2 or k < 0.4:
            return self.atom()
        # `/` is the important one: it is the only op that can trap, and the
        # only one whose result is a float even when both operands are ints.
        op = self.r.choice(["+", "-", "*", "/"] if self.floats else ["+", "-"])
        return f"{self.expr(depth + 1)} {op} {self.expr(depth + 1)}"

    def cond(self):
        ops = ["<", ">", "==", "!="]
        c = f"{self.expr(1)} {self.r.choice(ops)} {self.expr(1)}"
        k = self.r.random()
        if k < 0.15:
            return f"not ({c})"
        return c

    def declare(self, indent, name, value_expr):
        self.emit(indent, f"{name}: int[64] = {value_expr}")
        self.declared.add(name)

    def float_program(self):
        """A float-only program: no loops, no helper calls, flat.

        Deliberately flat. Everything interesting about float support is in the
        arithmetic, the comparison and the formatting, and a flat shape keeps
        any mismatch attributable to one of those rather than to a control-flow
        transform on top of it.

        Every float variable is declared `float[64]` up front. The typed
        pipeline is mandatory now, so an unannotated `v = ...` is refused before
        either engine sees it and the program would be skipped; the annotation
        is what lets the float comparison paths -- including `!=`'s
        setne-OR-setp -- actually reach the two engines.
        """
        # Declare every float variable up front, so a later statement can read
        # one that was written several statements earlier -- that distance is
        # what pushes a value across a `print` and out of a register.
        for v in self.FLOAT_VARS:
            self.emit(0, f"{v}: float[64] = {self.r.choice(self.FLOAT_LITERALS)}")
            self.float_declared.add(v)

        for _ in range(self.r.randint(4, 10)):
            k = self.r.random()
            if k < 0.35:
                # Read a float, do arithmetic, print the result.
                self.emit(0, f"print({self.expr(1)})")
            elif k < 0.55:
                # Compare and print the bool: exercises the setcc-and-not-parity
                # path, including the NaN case.
                self.emit(0, f"print({self.cond()})")
            elif k < 0.7:
                name = self.r.choice(sorted(self.float_declared))
                # Force a float: the typed pipeline refuses an int result into a
                # float[64] slot (no implicit int -> float on assignment, only
                # in arithmetic), and a bare int literal is exactly what
                # `expr()` can hand back. `+ 0.0` promotes without changing the
                # value, so the arithmetic being fuzzed is still the expression.
                self.emit(0, f"{name} = {self.expr(1)} + 0.0")
            elif k < 0.85:
                # A bare int print between float statements: the call here is
                # what a float value has to survive.
                self.emit(0, f"print({self.r.randint(0, 9)})")
            else:
                self.emit(0, f"print({self.float_atom()})")
        return "\n".join(self.lines) + "\n"

    def stmt(self, indent, depth):
        k = self.r.random()
        if not self.declared or k < 0.3:
            name = self.r.choice(VARS)
            self.declare(indent, name, self.expr())
        elif k < 0.55:
            name = self.r.choice(sorted(self.declared))
            self.emit(indent, f"{name} = {self.expr()}")
        elif k < 0.7:
            self.emit(indent, f"print({self.expr()})")
        elif k < 0.85 and depth < 2:
            self.emit(indent, f"if {self.cond()}:")
            self.if_block(indent + 1, depth + 1)
            if self.r.random() < 0.5:
                self.emit(indent, "else:")
                self.if_block(indent + 1, depth + 1)
        elif depth < 2:
            n = self.r.randint(1, 6)
            if self.shared_loop_vars:
                var = self.r.choice(VARS)
                self.declare(indent, var, "0")          # later code may read/assign it: diverges from CPython
            else:
                var = self.r.choice(LOOPVARS)
                self.emit(indent, f"{var}: int[64] = 0")  # deliberately NOT added to self.declared
            # acc += m * var, with m a variable the loop never writes. This is
            # the shape strength_reduce_multiplies() rewrites, so the fuzzer
            # reaches that pass instead of only ever seeing `print(var)`.
            # Both `m` and `acc` have to be declared *before* the `for`, or the
            # declaration lands in the loop body and the frontend rejects it.
            acc = None
            if self.declared and self.r.random() < 0.5:
                m = self.r.choice(sorted(set(self.declared) - {var}))
                acc = self.fresh_var(exclude={m, var})
                self.declare(indent, acc, "0")
            self.emit(indent, f"for {var} in range({n}):")
            if acc is not None:
                self.emit(indent + 1, f"{acc} = {acc} + {m} * {var}")
            else:
                self.emit(indent + 1, f"print({var})")
        else:
            self.emit(indent, f"print({self.expr()})")

    def if_block(self, indent, depth):
        # Only mutate variables that already exist before the if/else, so
        # nothing declared here needs to survive past the branch.
        wrote = False
        for _ in range(self.r.randint(1, 3)):
            if self.floats:
                # Float mode has no annotated int variables, so the only
                # assignable state is the float pool.
                if self.float_declared and self.r.random() < 0.7:
                    name = self.r.choice(sorted(self.float_declared))
                    self.emit(indent, f"{name} = {self.expr(1)}")
                else:
                    self.emit(indent, f"print({self.expr(1)})")
                wrote = True
                continue
            if self.declared and self.r.random() < 0.7:
                name = self.r.choice(sorted(self.declared))
                self.emit(indent, f"{name} = {self.expr()}")
                wrote = True
            else:
                self.emit(indent, f"print({self.expr()})")
                wrote = True
        if not wrote:
            self.emit(indent, "print(0)")

    # Two recursive shapes: a tail-recursive accumulator (like
    # tests/jit_only/tail_deep.lio, just far shallower here -- that file
    # already covers the extreme-depth/TCO case) and a tree-recursive,
    # non-tail form (two arithmetic operations around the recursive call,
    # so it genuinely exercises call/return plumbing instead of being
    # rewritten into a loop). Depths stay well under CPython's default
    # 1000-frame recursion limit.
    def emit_recursive_helper(self):
        if self.r.random() < 0.5:
            self.emit(0, "def rec(n: int[64], acc: int[64]) -> int[64]:")
            self.emit(1, "if n < 1:")
            self.emit(2, "return acc")
            self.emit(1, "return rec(n - 1, acc + n)")
            depth = self.r.randint(5, 200)
            return f"rec({depth}, 0)"
        else:
            self.emit(0, "def rec(n: int[64]) -> int[64]:")
            self.emit(1, "if n < 1:")
            self.emit(2, "return 0")
            self.emit(1, "return n + rec(n - 1)")
            depth = self.r.randint(3, 15)
            return f"rec({depth})"

    # A deliberately small generator for strength reduction. The general
    # generator declares 3 variables up front and keeps declaring more, but
    # strength_reduce_multiplies() only fires when the function stores few
    # enough variables for the accumulator to still win a promotion register
    # (see the guard in optimize.h). Without this mode the fuzzer almost never
    # reaches the pass at all -- 300 general seeds fired it zero times.
    #
    # So: at most 2 program variables plus 1 accumulator, and the only
    # statement shapes are ones that keep the multiply and its induction
    # variable in the canonical two-block counted loop.
    def program_mod(self):
        """Modulo-focused programs.

        The whole point of a separate generator is the sign of the dividend.
        C's `%` truncates toward zero and Python's floors, so `a % b` agrees
        only when both are non-negative:

            -7 % 3   C: -1    Python: 2
            -7 % 4   C: -3    Python: 1

        This mode therefore generates NON-NEGATIVE dividends by default, which
        keeps the CPython secondary oracle meaningful and the mode usable in
        CI. --mod-negatives generates the diverging shape, where the
        interpreter/JIT agreement is still enforced but the CPython comparison
        is expected to report the known language gap (the same treatment
        --shared-loop-vars gets).

        Divisors are chosen to hit each emitted shape:
          1, 2, 4, 8, 16, 1024  a power of two -> the `and` + sign-fixup path
          3, 5, 7, 10, 100      imm32 non-power-of-two -> idiv with a constant
          0                      the runtime/modulo-by-zero trap
          a declared variable    idiv reading a register that may be RAX or RDX
        """
        self.lines = []
        self.declared = set()

        # Keep every dividend non-negative unless the caller opted into the
        # diverging shape: the CPython oracle cannot referee those.
        def nonneg_atom():
            if self.declared and self.r.random() < 0.6:
                return self.r.choice(sorted(self.declared))
            return str(self.r.randint(0, 12))

        def dividend_atom(allow_negative):
            if allow_negative and self.r.random() < 0.45:
                return "-" + str(self.r.randint(1, 12))
            return nonneg_atom()

        const_divisors = [1, 2, 3, 4, 5, 7, 8, 10, 16, 100, 1024, 0]
        allow_negative = self.mod_negatives

        # Two live variables, so RAX and RDX are genuinely occupied when the
        # idiv sequence runs -- that is the clobber hazard this mode exists
        # to hit, and it cannot show up if the divisor is a literal.
        x = self.fresh_var()
        y = self.fresh_var(exclude={x})
        self.declare(0, x, nonneg_atom())
        self.declare(0, y, nonneg_atom())

        for _ in range(self.r.randint(4, 10)):
            a = dividend_atom(allow_negative)
            shape = self.r.random()
            if shape < 0.25:
                b = str(self.r.choice(const_divisors))
            elif shape < 0.45:
                b = y                                     # divisor in a register
            elif shape < 0.55:
                b = str(self.r.choice(const_divisors))    # both sides constant
                a = str(self.r.randint(0, 40)) if not allow_negative \
                    else str(self.r.randint(-40, 40))
            else:
                b = str(self.r.choice(const_divisors))
            k = self.r.random()
            if k < 0.55:
                self.emit(0, f"print({a} % {b})")
            elif k < 0.7:
                # `%=` lowers to the same mod instruction but through a store,
                # so the result also has to survive a variable write/read.
                tgt = self.r.choice(sorted(self.declared))
                self.emit(0, f"{tgt} %= {b}")
                self.emit(0, f"print({tgt})")
            elif k < 0.85:
                tgt = self.r.choice(sorted(self.declared))
                self.emit(0, f"{tgt} = {a} % {b}")
                # a print between statements keeps values live across a call
                self.emit(0, f"print({x})")
                self.emit(0, f"print({tgt})")
            else:
                # chained, to nest a modulo inside a larger expression
                self.emit(0, f"print(({a} % {b}) + {nonneg_atom()})")
        return "\n".join(self.lines) + "\n"

    def program_lsr(self):
        self.lines = []
        self.declared = set()
        outer = self.r.choice(VARS[:4])
        acc = self.fresh_var(exclude={outer})
        self.declare(0, outer, str(self.r.randint(1, 4)))
        self.declare(0, acc, "0")
        iv = self.r.choice(LOOPVARS)
        self.emit(0, f"{iv}: int[64] = 0")
        n = self.r.randint(1, 5)
        self.emit(0, f"for {iv} in range({n}):")
        shape = self.r.random()
        if shape < 0.45:
            # acc += m * iv  -- the exact match, varying operand order
            if self.r.random() < 0.5:
                self.emit(1, f"{acc} = {acc} + {outer} * {iv}")
            else:
                self.emit(1, f"{acc} = {acc} + {iv} * {outer}")
        elif shape < 0.7:
            # two accumulations in one body
            self.emit(1, f"{acc} = {acc} + {outer} * {iv}")
            self.emit(1, f"{acc} = {acc} - {outer} * {iv}")
        else:
            # product used through a local
            self.emit(1, f"p: int[64] = {outer} * {iv}")
            self.emit(1, f"{acc} = {acc} + p")
        self.emit(0, f"print({acc})")
        return "\n".join(self.lines) + "\n"

    def program_diamond(self):
        """Focused generator for the aggressive diamond unroller.

        Emits a counted loop whose body is exactly the shape
        match_diamond_unroll() claims to handle:

            H:  iv < n   -> D, exit
            D:  iv < k   -> A, E
            A:  acc = acc +- x;  jump B
            E:  acc = acc +- y;  jump B
            B:  iv = iv + 1;    jump H

        Trip counts deliberately straddle the unroll factor (0, 1, U-1, U,
        U+1, primes) because the unroller duplicates the exit test into every
        copy and closes the loop with a back edge to the entry test, so an
        off-by-one there only shows up on counts that are not a multiple of U.

        The arms are kept to pure arithmetic: a print() or a call in an arm
        would make the body fail is_unrollable_body() and the shape would
        never reach the unroller at all.
        """
        self.lines = []
        self.declared = set()
        acc = VARS[0]
        self.declare(0, acc, "0")
        n = self.r.choice([0, 1, 2, 3, 4, 5, 7, 8, 9, 11, 16, 17])
        # Mix the degenerate splits (always-then, always-else, never-taken)
        # with k in the middle of the range, so both arms and the transition
        # between them get executed.
        k = self.r.choice([0, n, n // 2, self.r.randint(0, n + 1)])
        then_delta = self.r.randint(1, 4)
        else_delta = self.r.randint(1, 4)
        op = self.r.choice(["+", "-"])
        iv = LOOPVARS[0]
        self.emit(0, f"{iv}: int[64] = 0")
        self.emit(0, f"for {iv} in range({n}):")
        self.emit(1, f"if {iv} < {k}:")
        self.emit(2, f"{acc} = {acc} {op} {then_delta}")
        self.emit(1, "else:")
        self.emit(2, f"{acc} = {acc} {op} {else_delta}")
        self.emit(0, f"print({acc})")
        return "\n".join(self.lines) + "\n"

    def program_break(self):
        """break/continue inside range loops (1, 2 and 3 argument forms).

        The trip counts straddle the break/continue indices (including
        never-taken and always-taken), and the 3-argument forms cover both a
        positive step and a negative one. The loop variable always advances, so
        the body cannot spin; what this mode checks is that break/continue pick
        the right target (loop exit vs. the increment/latch) and that the extra
        loop edges survive the SSA pipeline and the direct-Phi backend.
        """
        self.lines = []
        self.declared = set()
        acc = VARS[0]
        self.declare(0, acc, "0")
        n = self.r.randint(0, 12)
        brk = self.r.randint(0, n + 2)
        cont = self.r.randint(0, n + 2)
        form = self.r.choice(["1", "2", "3pos", "3neg"])
        iv = LOOPVARS[0]
        if form == "1":
            self.emit(0, f"for {iv} in range({n}):")
        elif form == "2":
            lo = self.r.randint(0, 3)
            self.emit(0, f"for {iv} in range({lo}, {lo + n}):")
        elif form == "3pos":
            lo = self.r.randint(0, 3)
            st = self.r.choice([1, 2, 3])
            hi = lo + n * st + self.r.randint(0, st)
            self.emit(0, f"for {iv} in range({lo}, {hi}, {st}):")
        else:
            hi = n * self.r.choice([1, 2, 3]) + self.r.randint(1, 4)
            st = self.r.choice([1, 2, 3])
            self.emit(0, f"for {iv} in range({hi}, 0, {-st}):")
        self.emit(1, f"{acc} = {acc} + {iv}")
        self.emit(1, f"if {iv} == {cont}:")
        self.emit(2, "continue")
        self.emit(1, f"if {iv} == {brk}:")
        self.emit(2, "break")
        self.emit(1, f"{acc} = {acc} + 1")
        self.emit(0, f"print({acc})")
        return "\n".join(self.lines) + "\n"

    def program_bitwise(self):
        """Bitwise and shift-focused programs.

        A separate mode for the same reason --mod is: the interesting shapes
        are operand forms the general generator never produces, and folding
        them into expr() would make every unrelated seed dilute them.

        What this is really reaching for, in order of how badly it has gone:

          * A shift result landing in RCX. RCX is a member of kTempPool on
            POSIX, so the allocator can pick it as a shift's destination, and
            `shl rcx, cl` would shift a register by its own low bits. The
            result has to be computed elsewhere and moved into RCX *after* the
            pop that restores the count. Two count variables plus an
            intervening print is the shape that actually produced that
            allocation.
          * Values live across a call. Every print below sits between
            statements on purpose, so a shift result has to survive being
            spilled or promoted while something else is live.
          * Both encodings, selected by whether the count is a literal or a
            variable. Literal counts in {1} and {small} pick the D1 and C1
            immediate forms; variable counts pick the D3/CL form.
          * The count bounds check itself, in both directions: 0 and 63 must
            work, and anything outside must trap identically on both engines.

        Keeping the CPython oracle usable is a design constraint, not an
        afterthought. `&`, `|`, `^` and `>>` agree with CPython for every
        int64 value -- they are width-preserving, and Python's `>>` floors
        toward negative infinity exactly like `sar`. Only `<<` can disagree,
        by wrapping where Python grows. So every left shift here is bounded so
        its result fits in int64, and the deliberate disagreements are left to
        tests/programs/bitwise_wrap.py. Without that care every generated
        program containing a shift would report interp_vs_cpython and the mode
        would be useless as a CI gate.
        """
        self.lines = []
        self.declared = set()

        # Bit patterns worth hitting: single bits, byte boundaries, all-ones,
        # and the sign bit. Negatives matter as much as positives because
        # &, |, ^ are two's complement and `>>` is arithmetic.
        PATTERNS = [0, 1, 2, 3, 5, 7, 8, 15, 16, 31, 32, 63, 64, 127, 128,
                    255, 256, 1023, 65535, -1, -2, -8, -127, -255, -256]
        # Left-shift counts: small enough that PATTERNS_MAX << 8 still fits in
        # int64, so CPython and Lithon cannot diverge on the result.
        SHIFT_COUNTS = [0, 1, 2, 3, 4, 5, 6, 7, 8]
        # Right-shift counts and bitwise counts have no such constraint.
        WIDE_COUNTS = [0, 1, 2, 7, 8, 31, 32, 61, 62, 63]
        # Counts that must trap at run time. These can only arrive through a
        # variable, never as a literal: a literal out-of-range count is a
        # compile-time RCR error, the frontend refuses the whole program, and
        # the fuzzer skips it without ever comparing the two engines. Holding
        # the bad value in a variable is what turns it into the runtime trap
        # this mode exists to fuzz.
        TRAP_COUNTS = [64, 65, 100, 1000, -1, -100]

        def operand():
            if self.declared and self.r.random() < 0.65:
                return self.r.choice(sorted(self.declared))
            return str(self.r.choice(PATTERNS))

        def small_count():
            """A count expression guaranteed to be in 0..8.

            Only k1/k2 or a literal -- NOT any declared variable. The value
            variables hold PATTERNS, which include 63 and 255; using one as a
            count is exactly the int64-overflowing left shift this mode is
            trying to keep out of the CPython comparison.

            A declared variable rather than a literal, because the variable
            form is the one that takes the D3/CL path where the RCX hazard
            lives; the literal immediate forms get their own shapes below.
            """
            return self.r.choice([k1, k2, str(self.r.choice(SHIFT_COUNTS))])

        def wide_count():
            return str(self.r.choice(WIDE_COUNTS))

        # Two count variables up front, so a body can hold two live shift
        # counts at once. That is what forces the allocator to pick a
        # destination other than the obvious scratch.
        k1 = self.fresh_var()
        k2 = self.fresh_var(exclude={k1})
        self.declare(0, k1, str(self.r.choice(SHIFT_COUNTS)))
        self.declare(0, k2, str(self.r.choice(SHIFT_COUNTS)))
        # Two value variables, so operands are not always constants and the
        # load/shift/store path is reached as well as the const one.
        x = self.fresh_var(exclude={k1, k2})
        y = self.fresh_var(exclude={k1, k2, x})
        self.declare(0, x, str(self.r.choice(PATTERNS)))
        self.declare(0, y, str(self.r.choice(PATTERNS)))

        for _ in range(self.r.randint(5, 12)):
            shape = self.r.random()
            if shape < 0.18:
                self.emit(0, f"print({operand()} & {operand()})")
            elif shape < 0.32:
                self.emit(0, f"print({operand()} | {operand()})")
            elif shape < 0.46:
                self.emit(0, f"print({operand()} ^ {operand()})")
            elif shape < 0.58:
                # Right shift, wide count: always agrees with CPython, so this
                # is the shape that can use the whole 0..63 range.
                self.emit(0, f"print({operand()} >> {self.r.choice([wide_count(), k1, k2])})")
            elif shape < 0.70:
                # Left shift, bounded count so int64 cannot overflow.
                self.emit(0, f"print({operand()} << {self.r.choice([small_count(), k1, k2])})")
            elif shape < 0.80:
                # Augmented forms: these lower through a store, so the result
                # has to survive a variable write and the following read.
                tgt = self.r.choice([v for v in (x, y) if v in self.declared])
                op = self.r.choice(["&=", "|=", "^=", ">>=", "<<="])
                rhs = operand() if op in ("&=", "|=", "^=") else \
                    (wide_count() if op == ">>=" else small_count())
                self.emit(0, f"{tgt} {op} {rhs}")
                # The print here is deliberate: it is what makes the stored
                # value live across a call.
                self.emit(0, f"print({tgt})")
            elif shape < 0.88:
                # Two shifts back to back, results stored. This is the exact
                # shape of the RCX-destination bug: the first shift's result
                # was allocated to RCX and the second shift's count check
                # clobbered it.
                self.emit(0, f"{x} = {operand()} << {small_count()}")
                self.emit(0, f"{y} = {operand()} >> {wide_count()}")
                self.emit(0, f"print({x})")
                self.emit(0, f"print({y})")
            elif shape < 0.94:
                # Nested, so a shift feeds another expression rather than
                # going straight to a print.
                self.emit(0, f"print(({operand()} >> {wide_count()}) & {operand()})")
                self.emit(0, f"print(({operand()} << {small_count()}) ^ {operand()})")
            else:
                # A counted loop, so the unroller and the loop passes see
                # shifts rather than only straight-line code. The mask at the
                # end of the body keeps acc bounded no matter what the body
                # does to it, which is what stops `<<` from overflowing int64
                # and making the CPython oracle useless.
                iv = self.r.choice([v for v in LOOPVARS if v not in self.declared])
                self.emit(0, f"{iv}: int[64] = 0")
                n = self.r.randint(1, 6)
                self.emit(0, f"for {iv} in range({n}):")
                body = self.r.random()
                if body < 0.4:
                    self.emit(1, f"{x} = ({x} + {operand()}) & 255")
                elif body < 0.7:
                    self.emit(1, f"{x} = ({x} << {small_count()}) & 255")
                else:
                    self.emit(1, f"{x} = ({x} >> {wide_count()}) & 255")
                self.emit(0, f"print({x})")

        # Optionally end with a runtime shift trap. This is deliberately last:
        # the trap aborts the process, so anything after it would never run and
        # would silently shrink the coverage of this program. Both engines must
        # agree that it trapped, with the same operator, and evaluate() treats
        # that as a result rather than a CPython language gap.
        if self.r.random() < 0.5:
            self.emit(0, f"{k1} = {self.r.choice(TRAP_COUNTS)}")
            self.emit(0, f"print({operand()} {self.r.choice(['<<', '>>'])} {k1})")
        return "\n".join(self.lines) + "\n"

    def program_phi(self):
        """Merge-focused programs: conditional expressions, run through --ssa.

        A conditional expression is the one program shape that *only* SSA can
        express well. `a if c else b` needs a value defined on both arms and
        read at the join, which is a Phi in every compiler; here it is a
        temporary variable that the SSA pipeline promotes, and Mem2Reg's
        promotion rule, the phi placement, the copy resolution, and the
        register allocator all have to be right for the printed answer to
        match the interpreter's.

        What this reaches for, in order of how badly it has gone:

          * Merges nested in merges. The outer arm's value is itself a merge,
            so the inner one has to be resolved before the outer reads it and
            the block ordering has to survive the resolution.
          * Merges inside loops, so a promoted value crosses a back edge as
            well as a join -- the loop-header phi, whose incoming operand for
            the first iteration comes from outside the loop.
          * Merges over floats. A promoted value's *type* matters, not just
            its id: a phi that is silently treated as an integer prints
            nonsense. Float literals are kept to small decimals so the CPython
            oracle stays usable (see program_bitwise on why that matters).
          * Merges as the right-hand side of an assignment, so the merge block
            has to be closed correctly before the store is emitted into it.

        Merges whose two arms are *different* types are deliberately not
        generated: V1_SPEC is statically typed and the checker rejects them
        (correctly) with "type of '__ifexprN' disagrees across branches", so
        they would only ever be skipped. The type-per-merge case that matters
        is already covered by running this generator in both int and float
        mode -- the same program shape, two different inferred kinds.

        Every value printed is also compared against CPython, so a merge that
        picks the wrong arm is a mismatch rather than a silently wrong
        program.
        """
        self.lines = []
        self.declared = set()
        self.float_declared = set()

        def int_atom():
            if self.declared and self.r.random() < 0.7:
                return self.r.choice(sorted(self.declared))
            return str(self.r.randint(0, 9))

        def float_atom():
            # Small decimals only: exact in binary, so the printed text is
            # identical in both engines and a mismatch means a real bug rather
            # than a rounding difference in the round-trip formatter.
            if self.float_declared and self.r.random() < 0.6:
                return self.r.choice(sorted(self.float_declared))
            return self.r.choice(["0.0", "0.5", "1.0", "1.5", "2.5", "3.5",
                                  "-1.5", "7.0", "10.25"])

        def arith(depth=0):
            atom = float_atom() if self.floats else int_atom()
            if depth >= 1 or self.r.random() < 0.5:
                return atom
            return f"{atom} {self.r.choice(['+', '-'])} {arith(depth + 1)}"

        def condition():
            return f"{arith(1)} {self.r.choice(['<', '>', '==', '!='])} {arith(1)}"

        def merge(depth=0):
            if depth < 2 and self.r.random() < 0.25:
                return f"{merge(depth + 1)} if {condition()} else {merge(depth + 1)}"
            return f"{arith(1)} if {condition()} else {arith(1)}"

        if self.floats:
            a, b = "f", "g"
            self.emit(0, f"{a}: float[64] = {float_atom()}")
            self.emit(0, f"{b}: float[64] = {float_atom()}")
            self.declared = {a, b}
            self.float_declared = {a, b}
        else:
            a, b = VARS[0], VARS[1]
            self.declare(0, a, "0")
            self.declare(0, b, "1")
            self.declared = {a, b}

        self.emit(0, f"print({merge()})")

        # A merge as a call argument: the merge block has to be finished before
        # the call is emitted into it.
        if self.r.random() < 0.5:
            m = self.fresh_var(exclude={a, b})
            kind = "float[64]" if self.floats else "int[64]"
            # Built before m joins the pool: a declaration's own initialiser
            # cannot read the variable it declares, and the checker is right to
            # reject that (0.6.10).
            self.emit(0, f"{m}: {kind} = {a} + ({merge()})")
            if self.floats:
                self.float_declared.add(m)
            else:
                self.declared.add(m)

        # Merges across a back edge, in a loop whose trip count is small and
        # non-trivial (0 and 1 exercise the "never taken" and "taken once"
        # header phis).
        n = self.r.choice([1, 2, 3, 4, 5])
        acc = self.fresh_var(exclude={a, b})
        self.emit(0, f"{acc}: int[64] = 0")
        iv = LOOPVARS[0]
        self.emit(0, f"{iv}: int[64] = 0")
        self.emit(0, f"for {iv} in range({n}):")
        self.emit(1, f"{acc} = {acc} + 1")
        self.emit(1, f"{acc} = {acc} + (1 if {condition()} else -1)")
        self.emit(0, f"print({acc})")
        return "\n".join(self.lines) + "\n"

    def program_oop(self):
        """A class program, exercising the 4.10.1 object model.

        Each generated program declares one small class with two int attributes,
        an `__init__`, one or two plain methods, and -- half the time -- a
        `__del__` that prints an attribute. The body then constructs objects,
        aliases them (`b = a`, which retains), mutates attributes through the
        reference, calls methods, passes objects to a function and returns them,
        compares identity (`is`/`is not`), and drops references at scope exits.
        Half the time it also declares a second HOLDER class whose attribute is
        a reference to the first, so a reference stored INSIDE an object -- and
        the release of the object it names when the holder dies -- is exercised
        too, not only references held in local names.

        Why these shapes and not a free-for-all:
          * Every printed value is an INT. A class reference is print-unsafe by
            design (its text would be an address, which the two tiers may lay
            out differently), so printing one is refused -- the generator must
            print attribute values, never the object.
          * Magnitudes stay small so int[64] overflow never fires; the point is
            the object model, not arithmetic.
          * The `__del__` print is the observable lifetime signal: if the two
            tiers disagree about WHEN the last reference drops -- a scope exit,
            a loop back edge, an early return -- the printed order differs and
            the diff catches it.
          * The program is also valid CPython (`int[64]` annotations are never
            evaluated at module or local scope), so the secondary oracle is
            meaningful.
        """
        r = self.r
        cname = r.choice(["Box", "Cell", "Node", "Pair"])
        a1, a2 = "lo", "hi"

        lines = []
        lines.append(f"class {cname}:")
        lines.append(f"    {a1}: int[64]")
        lines.append(f"    {a2}: int[64]")
        lines.append("")
        lines.append(f"    def __init__(self, {a1}: int[64], {a2}: int[64]):")
        lines.append(f"        self.{a1} = {a1}")
        lines.append(f"        self.{a2} = {a2}")
        lines.append("")

        # A method combining the two attributes, in one of a few shapes so that
        # both the read path and the arithmetic around it vary.
        body_expr = r.choice([
            f"self.{a1} + self.{a2}",
            f"self.{a1} - self.{a2}",
            f"self.{a1} + self.{a2} + self.{a1}",
        ])
        lines.append(f"    def total(self) -> int[64]:")
        lines.append(f"        return {body_expr}")
        lines.append("")

        has_del = r.random() < 0.5
        if has_del:
            which = r.choice([a1, a2])
            lines.append("    def __del__(self):")
            lines.append(f"        print(self.{which} + {r.randint(0, 3)})")
            lines.append("")

        # A free function that takes two objects and returns one, so references
        # cross a call boundary and a returned reference is consumed as a
        # receiver.
        pick_true = r.random() < 0.5
        lines.append(f"def pick(a: {cname}, b: {cname}) -> {cname}:")
        if pick_true:
            lines.append("    return a")
        else:
            lines.append("    return b")
        lines.append("")

        # A function that returns a sum, so a method runs on a passed-in object.
        lines.append(f"def sum_to(a: {cname}, b: {cname}) -> int[64]:")
        lines.append("    return a.total() + b.total()")
        lines.append("")

        # Half the time, add a HOLDER class with a CLASS-TYPED attribute. This is
        # the nested-ownership path: the holder owns a reference to the object
        # its attribute names, so releasing the holder must release that object,
        # and the held object's __del__ must run at the holder's drop. Without
        # this variant the generator never exercised a reference stored INSIDE an
        # object (only aliases in local names), which is exactly the leak the
        # hand-written class_lifetime test caught.
        has_holder = r.random() < 0.5
        hname = "Wrap"
        holder_del = r.random() < 0.5
        if has_holder:
            lines.append(f"class {hname}:")
            lines.append(f"    item: {cname}")
            lines.append("    tag: int[64]")
            lines.append("")
            lines.append(f"    def __init__(self, item: {cname}, tag: int[64]):")
            lines.append("        self.item = item")
            lines.append("        self.tag = tag")
            lines.append("")
            lines.append("    def score(self) -> int[64]:")
            lines.append("        return self.tag + self.item.total()")
            lines.append("")
            if holder_del:
                lines.append("    def __del__(self):")
                lines.append("        print(self.item.total() + 5000)")
                lines.append("")
            lines.append("")

        v1, v2, v3 = (r.randint(0, 9) for _ in range(3))
        lines.append(f"p: {cname} = {cname}({v1}, {v2})")
        lines.append(f"q: {cname} = {cname}({v3}, {r.randint(0, 9)})")
        lines.append("print(sum_to(p, q))")
        lines.append("print(pick(p, q).total())")
        lines.append("print(p.total())")

        # Aliasing: both names own a reference, and a mutation is visible
        # through either.
        lines.append("alias: %s = p" % cname)
        delta = r.randint(0, 5)
        lines.append(f"alias.{a1} = alias.{a1} + {delta}")
        lines.append("print(p.total())")

        # Identity.
        lines.append("if p is alias:")
        lines.append("    print(1000)")
        lines.append("if p is not q:")
        lines.append("    print(2000)")
        if r.random() < 0.5:
            lines.append("r2: %s = q" % cname)
            lines.append("if q is r2:")
            lines.append("    print(3000)")

        # Reassignment drops the reference the name held before, so the object it
        # named dies at that statement (its __del__ runs there).
        if r.random() < 0.5:
            lines.append("alias = %s(%d, %d)" % (cname, r.randint(0, 9), r.randint(0, 9)))
            lines.append("print(alias.total())")

        # A user function that RETURNS a reference: the result is a temporary the
        # caller owns, consumed either as another call's argument or discarded.
        if r.random() < 0.5:
            lines.append("print(pick(q, p).total())")

        # A loop that constructs and drops a fresh object each iteration: with a
        # __del__ this pins the per-iteration drop point.
        if r.random() < 0.5:
            n = r.randint(1, 4)
            lines.append(f"i: int[64] = 0")
            lines.append(f"acc: int[64] = 0")
            lines.append(f"while i < {n}:")
            lines.append(f"    t: {cname} = {cname}(i, {r.randint(0, 9)})")
            lines.append("    acc = acc + t.total()")
            lines.append("    i = i + 1")
            lines.append("print(acc)")

        # An early return: the references the returned path owns are dropped on
        # that path only.
        if r.random() < 0.5:
            flag = r.choice(["True", "False"])
            lines.append(f"def early(flag: bool) -> int[64]:")
            lines.append(f"    a: {cname} = {cname}({r.randint(0, 9)}, {r.randint(0, 9)})")
            lines.append("    if flag:")
            lines.append("        return a.total()")
            lines.append(f"    b: {cname} = {cname}({r.randint(0, 9)}, {r.randint(0, 9)})")
            lines.append("    return b.total()")
            lines.append(f"print(early({flag}))")

        lines.append("print(q.total())")

        # A holder built from a FRESH object: the attribute is the only surviving
        # reference once the constructor's temporary is gone, so this pins the
        # nested drop (the held object dies when the holder does). Guarded on the
        # holder existing so the program stays valid when it does not.
        if has_holder:
            lines.append(f"w: {hname} = {hname}({cname}({r.randint(0, 9)}, {r.randint(0, 9)}), {r.randint(0, 9)})")
            lines.append("print(w.score())")
            lines.append("print(w.item.total())")
            if r.random() < 0.5:
                # A holder whose held object is ALSO still named locally: the
                # held object must survive to the last drop of either.
                lines.append(f"keep: {cname} = {cname}({r.randint(0, 9)}, {r.randint(0, 9)})")
                lines.append(f"w2: {hname} = {hname}(keep, {r.randint(0, 9)})")
                lines.append("print(w2.score() + keep.total())")
        return "\n".join(lines) + "\n"

    def program_oop_inherit(self):
        """A class program with SINGLE INHERITANCE, exercising 4.10.2.

        The base declares two int attributes, an `__init__`, and a `total`
        method. A derived class adds one attribute, calls `super().__init__`,
        and -- most of the time -- overrides `total` (its override itself calls
        `super().total()`, so the base implementation is reached through the
        chain). A sibling class extends the same base without overriding
        `total`, so the inherited implementation and the "not a derived" branch
        of isinstance are both exercised.

        The body then:
          * calls the override directly and through a BASE-typed reference (the
            vtable must pick the override at run time),
          * calls `super().total()` from a derived method that the base does not
            have (`bonus`),
          * checks isinstance/issubclass over true, inherited and sibling pairs,
          * passes a derived where a base parameter is expected, and returns a
            derived through a base return type,
          * aliases and reassigns base-typed names (retain/drop),
          * builds a fresh derived temporary, and constructs/drops one per loop
            iteration,
          * optionally gives the BASE a `__del__`, which both subclasses
            inherit, so a drop of any hierarchy object is observable.

        As in program_oop, every printed value is an INT (never a reference),
        magnitudes stay small, and the program is valid CPython. When a
        `__del__` is present the evaluator skips the CPython drop-ORDER
        comparison (OOP_MODE), but the interpreter-vs-JIT comparison is always
        enforced in full.
        """
        r = self.r
        bname = r.choice(["Base", "Shape", "Animal"])
        dname = r.choice(["Derived", "Square", "Dog"])
        sname = r.choice(["Sibling", "Other", "Bird"])
        a1, a2, a3 = "lo", "hi", "extra"

        base_expr = r.choice([
            f"self.{a1} + self.{a2}",
            f"self.{a1} - self.{a2}",
            f"self.{a1} + self.{a2} + self.{a1}",
        ])
        override_expr = r.choice([
            f"super().total() + self.{a3}",
            f"super().total() * 2 + self.{a3}",
            f"self.{a3} - super().total()",
        ])
        override = r.random() < 0.7
        has_del = r.random() < 0.5
        del_which = r.choice([a1, a2])
        del_k = r.randint(0, 3)

        lines = []
        lines.append(f"class {bname}:")
        lines.append(f"    {a1}: int[64]")
        lines.append(f"    {a2}: int[64]")
        lines.append("")
        lines.append(f"    def __init__(self, {a1}: int[64], {a2}: int[64]):")
        lines.append(f"        self.{a1} = {a1}")
        lines.append(f"        self.{a2} = {a2}")
        lines.append("")
        lines.append("    def total(self) -> int[64]:")
        lines.append(f"        return {base_expr}")
        lines.append("")
        if has_del:
            lines.append("    def __del__(self):")
            lines.append(f"        print(self.{del_which} + {del_k})")
            lines.append("")

        # The derived class: adds an attribute, chains __init__, and usually
        # overrides total. bonus always calls super().total(), so the base
        # implementation is reached through super() even without an override.
        lines.append(f"class {dname}({bname}):")
        lines.append(f"    {a3}: int[64]")
        lines.append("")
        lines.append(f"    def __init__(self, {a1}: int[64], {a2}: int[64], {a3}: int[64]):")
        lines.append(f"        super().__init__({a1}, {a2})")
        lines.append(f"        self.{a3} = {a3}")
        lines.append("")
        if override:
            lines.append("    def total(self) -> int[64]:")
            lines.append(f"        return {override_expr}")
            lines.append("")
        lines.append("    def bonus(self) -> int[64]:")
        lines.append(f"        return super().total() * 10 + self.{a3}")
        lines.append("")

        # A sibling: same base, no total override, one extra attribute. This
        # gives isinstance a FALSE branch and the inherited slot a non-derived
        # receiver.
        lines.append(f"class {sname}({bname}):")
        lines.append("    tag: int[64]")
        lines.append("")
        lines.append(f"    def __init__(self, {a1}: int[64], {a2}: int[64], tag: int[64]):")
        lines.append(f"        super().__init__({a1}, {a2})")
        lines.append("        self.tag = tag")
        lines.append("")
        lines.append("    def score(self) -> int[64]:")
        lines.append("        return self.total() + self.tag")
        lines.append("")

        # Free functions: a base parameter accepts a derived (free upcast), and
        # a base return type carries a derived back out (implicit upcast on
        # return).
        lines.append(f"def sum_two(x: {bname}, y: {bname}) -> int[64]:")
        lines.append("    return x.total() + y.total()")
        lines.append("")
        lines.append(f"def upcast(x: {dname}) -> {bname}:")
        lines.append("    return x")
        lines.append("")

        v1, v2, v3 = (r.randint(0, 9) for _ in range(3))
        lines.append(f"d: {dname} = {dname}({v1}, {v2}, {v3})")
        lines.append("print(d.total())")
        lines.append("print(d.bonus())")

        # A base-typed reference to a derived object: the call site is compiled
        # against the base, so an override must be reached through the vtable.
        lines.append(f"b: {bname} = d")
        lines.append("print(b.total())")
        lines.append(f"print(isinstance(d, {dname}))")
        lines.append(f"print(isinstance(d, {bname}))")
        lines.append(f"print(isinstance(b, {dname}))")
        lines.append(f"print(isinstance(d, {sname}))")
        lines.append(f"print(issubclass({dname}, {bname}))")
        lines.append(f"print(issubclass({bname}, {dname}))")

        lines.append(f"s: {sname} = {sname}({r.randint(0, 9)}, {r.randint(0, 9)}, {r.randint(0, 9)})")
        lines.append("print(s.score())")
        lines.append(f"print(isinstance(s, {bname}))")
        lines.append(f"print(isinstance(s, {dname}))")
        lines.append(f"sb: {bname} = s")
        lines.append("print(sb.total())")

        # A base parameter accepts both a derived and a sibling; the derived
        # one dispatches to its override.
        lines.append("print(sum_two(d, s))")
        lines.append("print(sum_two(b, sb))")
        lines.append("print(upcast(d).total())")

        # Aliasing and reassignment: both names own a reference, and moving the
        # base-typed name from the derived object to the sibling drops the
        # derived reference (which d still owns).
        lines.append(f"alias: {bname} = d")
        lines.append("print(alias.total())")
        lines.append("alias = s")
        lines.append("print(alias.total())")
        lines.append("if d is b:")
        lines.append("    print(1000)")
        lines.append("if b is not s:")
        lines.append("    print(2000)")

        # A fresh derived temporary, dropped at the statement that owns it.
        lines.append(f"print({dname}({r.randint(0, 9)}, {r.randint(0, 9)}, {r.randint(0, 9)}).total())")

        # One derived per iteration: with an inherited __del__ this pins the
        # per-iteration drop point.
        n = r.randint(1, 4)
        lines.append("i: int[64] = 0")
        lines.append("acc: int[64] = 0")
        lines.append(f"while i < {n}:")
        lines.append(f"    t: {dname} = {dname}(i, {r.randint(0, 9)}, i + 1)")
        lines.append("    acc = acc + t.total()")
        lines.append("    i = i + 1")
        lines.append("print(acc)")

        # Early return: only the returned path's references are dropped there.
        lines.append("def early(flag: bool) -> int[64]:")
        lines.append(f"    x: {dname} = {dname}({r.randint(0, 9)}, {r.randint(0, 9)}, {r.randint(0, 9)})")
        lines.append("    if flag:")
        lines.append("        return x.total()")
        lines.append(f"    y: {bname} = {dname}({r.randint(0, 9)}, {r.randint(0, 9)}, {r.randint(0, 9)})")
        lines.append("    return y.total()")
        lines.append("print(early(True))")
        lines.append("print(early(False))")

        lines.append("print(s.total())")
        return "\n".join(lines) + "\n"

    def program_oop_containers(self):
        """A class program whose OBJECTS live in lists and tuples -- 4.10.3.

        The shape is one base class plus a derived override, so a container
        declared with the BASE element type can hold either class. That makes
        every read `xs[i]` a reference whose static class is the base but whose
        real class may be the derived one, which is exactly the case virtual
        dispatch and isinstance must get right through a container.

        The body exercises:
          * a list literal with a derived, a base and a derived element,
          * reads through `xs[i]` (attributes, methods, virtual dispatch),
          * overwriting a slot with a fresh object and with another slot,
          * a class-typed name aliased from an element (retain),
          * identity and membership over references,
          * isinstance over base/derived elements,
          * a nested list and a class-typed tuple,
          * an explicit `while` index loop (the supported iteration form),
          * passing an element to a free function and returning a reference,
          * an early return from a function that owns a container,
          * optionally a base `__del__`, inherited by the derived class, so the
            per-slot drop at an overwrite or a scope exit is observable.

        Every printed value is an INT (a reference is print-unsafe), magnitudes
        stay small, and the program is valid CPython. When a `__del__` is
        present the CPython oracle skips only the drop ORDER, as in the other
        OOP shapes; the interpreter-vs-JIT comparison is always exact.
        """
        r = self.r
        bname = r.choice(["Base", "Shape", "Animal"])
        dname = r.choice(["Derived", "Square", "Dog"])
        a1, a2, a3 = "lo", "hi", "extra"

        base_expr = r.choice([
            f"self.{a1} + self.{a2}",
            f"self.{a1} - self.{a2}",
            f"self.{a1} + self.{a2} + self.{a1}",
        ])
        override = r.random() < 0.7
        override_expr = r.choice([
            f"super().total() + self.{a3}",
            f"super().total() * 2 + self.{a3}",
            f"self.{a3} - super().total()",
        ])
        has_del = r.random() < 0.5
        del_which = r.choice([a1, a2])
        del_k = r.randint(0, 3)

        lines = []
        lines.append(f"class {bname}:")
        lines.append(f"    {a1}: int[64]")
        lines.append(f"    {a2}: int[64]")
        lines.append("")
        lines.append(f"    def __init__(self, {a1}: int[64], {a2}: int[64]):")
        lines.append(f"        self.{a1} = {a1}")
        lines.append(f"        self.{a2} = {a2}")
        lines.append("")
        lines.append("    def total(self) -> int[64]:")
        lines.append(f"        return {base_expr}")
        lines.append("")
        if has_del:
            lines.append("    def __del__(self):")
            lines.append(f"        print(self.{del_which} + {del_k})")
            lines.append("")

        lines.append(f"class {dname}({bname}):")
        lines.append(f"    {a3}: int[64]")
        lines.append("")
        lines.append(f"    def __init__(self, {a1}: int[64], {a2}: int[64], {a3}: int[64]):")
        lines.append(f"        super().__init__({a1}, {a2})")
        lines.append(f"        self.{a3} = {a3}")
        lines.append("")
        if override:
            lines.append("    def total(self) -> int[64]:")
            lines.append(f"        return {override_expr}")
            lines.append("")
        lines.append("    def bonus(self) -> int[64]:")
        lines.append(f"        return super().total() * 10 + self.{a3}")
        lines.append("")

        # Free functions: an element read is a BORROW, so passing it must not
        # claim the container's reference. A returned reference is a fresh one
        # the caller owns.
        lines.append(f"def sum_pair(x: {bname}, y: {bname}) -> int[64]:")
        lines.append("    return x.total() + y.total()")
        lines.append("")
        lines.append(f"def lift(x: {bname}) -> {bname}:")
        lines.append("    return x")
        lines.append("")

        v = [r.randint(0, 9) for _ in range(9)]
        lines.append(f"xs: list[{bname}, 3] = [{dname}({v[0]}, {v[1]}, {v[2]}), "
                     f"{bname}({v[3]}, {v[4]}), {dname}({v[5]}, {v[6]}, {v[7]})]")
        lines.append("print(xs[0].total())")
        lines.append("print(xs[1].total())")
        lines.append("print(xs[2].total())")
        lines.append("print(len(xs))")

        # A base-typed name aliased from an element owns a second reference.
        lines.append(f"p: {bname} = xs[1]")
        lines.append("print(p.total())")
        lines.append("print(p is xs[1])")
        lines.append("print(xs[1] is xs[2])")
        lines.append("print(p in xs)")
        lines.append("print(xs[0] in xs)")
        lines.append(f"print({dname}({r.randint(0, 9)}, {r.randint(0, 9)}, "
                     f"{r.randint(0, 9)}) in xs)")

        lines.append(f"print(isinstance(xs[0], {dname}))")
        lines.append(f"print(isinstance(xs[1], {dname}))")
        lines.append(f"print(isinstance(p, {bname}))")
        lines.append("print(sum_pair(xs[0], xs[1]))")
        lines.append("print(lift(xs[2]).total())")

        # Overwrite with a fresh object, then with another slot. The old slot
        # reference drops where the store happens.
        lines.append(f"xs[0] = {dname}({r.randint(0, 9)}, {r.randint(0, 9)}, "
                     f"{r.randint(0, 9)})")
        lines.append("print(xs[0].total())")
        lines.append("xs[2] = xs[0]")
        lines.append("print(xs[2] is xs[0])")
        lines.append("print(xs[2].total())")

        # A nested list: the flat run is filled by the literal and each leaf is
        # an independent reference.
        lines.append(f"grid: list[list[{bname}, 2], 2] = "
                     f"[[{bname}({r.randint(0, 9)}, {r.randint(0, 9)}), "
                     f"{dname}({r.randint(0, 9)}, {r.randint(0, 9)}, {r.randint(0, 9)})], "
                     f"[{dname}({r.randint(0, 9)}, {r.randint(0, 9)}, {r.randint(0, 9)}), "
                     f"{bname}({r.randint(0, 9)}, {r.randint(0, 9)})]]")
        lines.append("print(grid[0][1].total())")
        lines.append("print(grid[1][0].total())")
        lines.append(f"grid[0][0] = {bname}({r.randint(0, 9)}, {r.randint(0, 9)})")
        lines.append("print(grid[0][0].total())")

        # A class-typed tuple is filled once by its literal and read only.
        lines.append(f"ts: tuple[{bname}, 2] = ({dname}({r.randint(0, 9)}, {r.randint(0, 9)}, "
                     f"{r.randint(0, 9)}), {bname}({r.randint(0, 9)}, {r.randint(0, 9)}))")
        lines.append("print(ts[0].total())")
        lines.append("print(ts[1].total())")

        # The explicit index loop is the supported iteration form.
        n = r.randint(1, 3)
        lines.append("ci: int[64] = 0")
        lines.append("acc: int[64] = 0")
        lines.append(f"while ci < {n}:")
        lines.append("    acc = acc + xs[ci].total()")
        lines.append("    ci = ci + 1")
        lines.append("print(acc)")

        # An early return from a function that owns a container: the returned
        # path releases the container's slots when it returns, the other path
        # releases them on its own return.
        lines.append("def early(flag: bool) -> int[64]:")
        lines.append(f"    ys: list[{bname}, 2] = [{bname}({r.randint(0, 9)}, {r.randint(0, 9)}), "
                     f"{dname}({r.randint(0, 9)}, {r.randint(0, 9)}, {r.randint(0, 9)})]")
        lines.append("    if flag:")
        lines.append("        return ys[0].total()")
        lines.append("    return ys[1].total()")
        lines.append("print(early(True))")
        lines.append("print(early(False))")

        lines.append("print(p.total())")
        return "\n".join(lines) + "\n"

    def program(self):
        call_expr = self.emit_recursive_helper() if self.r.random() < 0.5 else None
        for v in VARS[:3]:
            self.declare(0, v, str(self.r.randint(0, 5)))
        for _ in range(self.r.randint(4, 10)):
            self.stmt(0, 0)
        if call_expr:
            self.emit(0, f"print({call_expr})")
        self.emit(0, f"print({self.expr()})")
        return "\n".join(self.lines) + "\n"


# ---------------------------------------------------------------------
# Running one candidate through both engines.
# ---------------------------------------------------------------------
class Result:
    def __init__(self, ok, interp_out=None, interp_rc=None, jit_out=None,
                 jit_rc=None, cpy_out=None, cpy_rc=None, reason=None):
        self.ok = ok            # False => a genuine mismatch was found
        self.interp_out, self.interp_rc = interp_out, interp_rc
        self.jit_out, self.jit_rc = jit_out, jit_rc
        self.cpy_out, self.cpy_rc = cpy_out, cpy_rc
        self.reason = reason    # None | "skip" | "interp_vs_cpython" | "jit_vs_interp"


def evaluate(py_path: Path, tmp_ir: Path):
    fe = run([sys.executable, str(FRONTEND), str(py_path)])
    if fe.returncode != 0:
        return Result(True, reason="skip")   # not even valid Lithon syntax
    tmp_ir.write_text(fe.stdout)

    interp = run([str(HELLO), str(tmp_ir)])

    # A divide/modulo-by-zero trap is a *result*, not a rejection: the
    # interpreter prints "error: division by zero" (or "modulo by zero") and
    # exits 1, and the JIT prints the same line and exits 1. Both engines are
    # supposed to do that, so the program is worth comparing -- it
    # exercises the emitted zero test (comisd sets ZF for an unordered compare
    # as well as for an equal one, so the check has to be ZF AND !PF or it
    # traps on a NaN divisor instead). Skipping these would throw away precisely
    # the code most likely to be wrong; --floats generated 21 of them in 300
    # programs.
    #
    # Only those two messages are treated this way. Every other nonzero exit is
    # a typecheck or other rejection, which is not what this fuzzer is about.
    # The two are kept as separate strings on purpose: the interpreter spells
    # them differently and run_tier_diff.py diffs stderr byte-for-byte, so a
    # single combined substring would still catch both.
    #
    # An out-of-range shift count is the same kind of result for a different
    # reason. x86 masks the count to its low 6 bits, so `1 << 64` would
    # silently execute as a shift by 0 and `1 << -1` as a shift by 63; Lithon
    # traps instead of returning the wrong answer. The trap is part of the
    # language, so a program that hits it is a result to compare between the
    # two engines, not a rejection to skip and not a language gap to report.
    interp_stderr = interp.stderr or ""
    is_div_trap = (interp.returncode == 1
                   and ("division by zero" in interp_stderr
                        or "modulo by zero" in interp_stderr))
    is_shift_trap = (interp.returncode == 1
                     and "shift count out of range" in interp_stderr)
    if interp.returncode not in (0, TIMEOUT_RC) and not (is_div_trap or is_shift_trap):
        return Result(True, reason="skip")

    # The diamond unroller is off by default (it measured slower), so --diamond
    # has to opt in or it would fuzz a build that never runs the transform.
    jit_args = [str(TIER_RUNNER), str(tmp_ir), "--auto"]
    if UNROLL_DIAMONDS:
        jit_args.append("--unroll-diamonds")
    # Accumulator unrolling is off by default for the same reason diamond
    # unrolling is: the fast path is the one production runs, so an opt-in
    # transform needs an explicit mode or it never gets exercised. It matters
    # more than usual here -- it REORDERS blocks (the jammed main loop is emitted
    # after the remainder), which is precisely what a liveness model built on
    # textual block ranges gets wrong, silently and only for float
    # accumulators.
    if ACCUM_UNROLL:
        jit_args.append("--accum-unroll")
    jit = run(jit_args)
    if SSA_MODE:
        jit_ssa = run(jit_args + ["--ssa"])
        # A crash in the pipeline is itself the finding, so compare it here
        # rather than letting the CPython oracle explain it away.
        if jit_ssa.returncode != jit.returncode or jit_ssa.stdout != jit.stdout:
            return Result(False, interp.stdout, interp.returncode,
                          jit_ssa.stdout, jit_ssa.returncode,
                          cpy_out=None, cpy_rc=None, reason="jit_vs_interp")
    if DIRECT_PHI_MODE:
        jit_dp = run(jit_args + ["--direct-phis"])
        if jit_dp.returncode != jit.returncode or jit_dp.stdout != jit.stdout:
            return Result(False, interp.stdout, interp.returncode,
                          jit_dp.stdout, jit_dp.returncode,
                          cpy_out=None, cpy_rc=None, reason="jit_vs_interp")
    cpy = run([sys.executable, str(py_path)])
    seen = (interp.stdout, interp.returncode, jit.stdout, jit.returncode, cpy.stdout, cpy.returncode)

    # 1. PRIMARY: the two engines must agree with each other. Checked first so a CPython disagreement
    #    can never mask a JIT bug. Both hanging counts as agreement (partial stdout is not comparable).
    both_hang = interp.returncode == TIMEOUT_RC and jit.returncode == TIMEOUT_RC
    if not both_hang and (jit.stdout != interp.stdout or jit.returncode != interp.returncode):
        return Result(False, *seen, reason="jit_vs_interp")

    # Both engines reached a nonzero exit that looks like a divide trap, so
    # require that BOTH actually report it: one side trapping and the other
    # exiting 1 for some other reason is a divergence even though rc and stdout
    # already matched. The stderr lines are not compared verbatim because
    # tier_runner also writes its "[tier1] native" / "[tier0] interpreter"
    # banner there, which is not part of the program's output.
    #
    # The message text is compared too, not just its presence: a program that
    # traps on modulo in the interpreter must not appear as a division trap in
    # the JIT, and the two spellings are the only way to tell them apart. The
    # shift message is matched whole, operator included, for the same reason:
    # `<<` and `>>` trap with different text and swapping them would be a bug
    # this comparison exists to catch.
    def trap_text(stderr):
        for m in ("modulo by zero", "division by zero",
                  "shift count out of range 0..63 for `<<`",
                  "shift count out of range 0..63 for `>>`"):
            if m in (stderr or ""):
                return m
        return None

    interp_trapped = trap_text(interp.stderr)
    jit_trapped = trap_text(jit.stderr)
    if (interp.returncode != 0 or jit.returncode != 0) and not both_hang:
        if interp_trapped != jit_trapped:
            return Result(False, *seen, reason="jit_vs_interp")

    # 2. SECONDARY: what they agree on must match CPython (a language-semantics gap, not a JIT bug).
    #
    # An out-of-range shift is exempt. CPython has no such rule -- `1 << 64` is
    # a perfectly good 65-bit number there -- so CPython exits 0 with output
    # where Lithon exits 1 with none, and comparing them would report every
    # single generated trap as a "language gap" and bury the real findings.
    # The primary JIT-vs-interpreter agreement above, including that both
    # engines trap with the same operator, is still enforced in full.
    # The primary gate above already holds -- both engines agree. The CPython
    # oracle is a secondary semantics check; in --oop mode a program with a
    # __del__ is exempt from matching CPython's drop ORDER (see OOP_MODE), so
    # only its presence is worth comparing, not the interleaving.
    oop_del_exempt = OOP_MODE and "__del__" in py_path.read_text()
    if cpy.returncode == 0 and not (is_shift_trap and interp_trapped == jit_trapped) \
            and not oop_del_exempt:
        if interp.returncode == TIMEOUT_RC:
            return Result(False, *seen, reason="hang_vs_cpython")   # Lithon never finishes; CPython does
        if cpy.stdout != interp.stdout:
            return Result(False, *seen, reason="interp_vs_cpython")

    return Result(True)


# ---------------------------------------------------------------------
# Minimization: repeated whole-STATEMENT removal on the AST (a block is removed together with its
# body). The old line-based version could delete an `if` header and leave its body -- or an `else:` --
# behind, silently turning the program into a different program (a loop-body assignment, a for/else).
# A removal is kept only if the program still reproduces the SAME class of mismatch.
# ---------------------------------------------------------------------
def _statement_lists(tree):
    lists = [tree.body]
    for node in ast.walk(tree):
        if node is tree:
            continue
        for field in ("body", "orelse"):
            lst = getattr(node, field, None)
            if isinstance(lst, list) and lst and isinstance(lst[0], ast.stmt):
                lists.append(lst)
    return lists


def minimize(src: str, target_reason: str, tmp_dir: Path) -> str:
    tree = ast.parse(src)

    def reproduces():
        py = tmp_dir / "_min.lio"
        py.write_text(ast.unparse(tree) + "\n")
        r = evaluate(py, tmp_dir / "_min.ir")
        return (not r.ok) and r.reason == target_reason

    def remove_one():
        for lst in _statement_lists(tree):
            for i in range(len(lst)):
                if len(lst) == 1:
                    break                      # keep every block non-empty
                removed = lst.pop(i)
                if reproduces():
                    return True
                lst.insert(i, removed)
        return False

    while remove_one():
        pass
    return ast.unparse(tree) + "\n"


def main():
    global TIMEOUT
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=500)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=TIMEOUT, help="seconds before a run counts as a hang")
    ap.add_argument("--lsr", action="store_true",
                    help="use the small strength-reduction-focused generator (see Gen.program_lsr)")
    ap.add_argument("--no-diamond-unroll", action="store_true",
                    help="do not pass --unroll-diamonds to tier_runner (used by --diamond)")
    ap.add_argument("--accum", action="store_true",
                    help="run the JIT with --accum-unroll (float accumulator splitting)")
    ap.add_argument("--diamond", action="store_true",
                    help="use the small if/else-diamond-focused generator (see Gen.program_diamond)")
    ap.add_argument("--shared-loop-vars", action="store_true",
                    help="generate loop variables that are read/assigned elsewhere (reproduces the known "
                         "for-loop divergence from CPython; see the docstring)")
    ap.add_argument("--floats", action="store_true",
                    help="generate flat float programs (arithmetic, comparisons, printing, int/float mixing)")
    ap.add_argument("--mod", action="store_true",
                    help="use the modulo-focused generator (see Gen.program_mod)")
    ap.add_argument("--bitwise", action="store_true",
                    help="use the bitwise/shift-focused generator (see Gen.program_bitwise)")
    ap.add_argument("--break", dest="break_mode", action="store_true",
                    help="use the break/continue + range-focused generator (see Gen.program_break)")
    ap.add_argument("--mod-negatives", action="store_true",
                    help="let --mod generate negative dividends, where C's truncating %% and Python's "
                         "floored %% disagree; the CPython oracle then reports the known language gap")
    ap.add_argument("--phi", action="store_true",
                    help="use the merge-focused generator (see Gen.program_phi) and compare the JIT "
                         "with and without the SSA pipeline against the interpreter")
    ap.add_argument("--oop", action="store_true",
                    help="use the class-focused generator (see Gen.program_oop): construction, "
                         "attributes, methods, aliasing, __del__ lifetime, identity")
    args = ap.parse_args()
    TIMEOUT = args.timeout
    global UNROLL_DIAMONDS, SSA_MODE, ACCUM_UNROLL, DIRECT_PHI_MODE, OOP_MODE
    UNROLL_DIAMONDS = args.diamond and not args.no_diamond_unroll
    ACCUM_UNROLL = args.accum
    SSA_MODE = args.phi
    DIRECT_PHI_MODE = args.break_mode
    OOP_MODE = args.oop

    for exe, name in ((HELLO, "hello"), (TIER_RUNNER, "tier_runner")):
        if not exe.exists():
            print(f"error: {name} not built -- run: cmake --build build", file=sys.stderr)
            return 2

    tmp_dir = Path("/tmp/lithon_fuzz_diff")
    tmp_dir.mkdir(exist_ok=True)
    fail_dir = ROOT / "fuzz_failures"

    n_ok = n_skip = n_fail = 0
    by_reason = {}
    for seed in range(args.seed, args.seed + args.count):
        gen = Gen(seed, args.shared_loop_vars, floats=args.floats,
                  mod_negatives=args.mod_negatives)
        if args.oop:
            # Alternate seeds across the three OOP shapes: the flat 4.10.1
            # object model, the 4.10.2 single-inheritance shape, and the 4.10.3
            # class-references-in-containers shape. All three are pinned by the
            # same interpreter-vs-JIT gate; the extra shapes add vtable
            # dispatch, super(), isinstance, upcasts, and per-slot ownership.
            if seed % 3 == 0:
                src = gen.program_oop()
            elif seed % 3 == 1:
                src = gen.program_oop_inherit()
            else:
                src = gen.program_oop_containers()
        elif args.phi:
            src = gen.program_phi()
        elif args.floats:
            src = gen.float_program()
        elif args.mod:
            src = gen.program_mod()
        elif args.bitwise:
            src = gen.program_bitwise()
        elif args.break_mode:
            src = gen.program_break()
        elif args.diamond:
            src = gen.program_diamond()
        elif args.lsr:
            src = gen.program_lsr()
        else:
            src = gen.program()
        py = tmp_dir / "candidate.lio"
        py.write_text(src)
        result = evaluate(py, tmp_dir / "candidate.ir")

        if result.ok:
            if result.reason == "skip":
                n_skip += 1
            else:
                n_ok += 1
            continue

        n_fail += 1
        print(f"\n=== MISMATCH (seed={seed}, {result.reason}) ===")
        minimal = minimize(src, result.reason, tmp_dir)

        fail_dir.mkdir(exist_ok=True)
        stem = f"seed{seed}_{result.reason}"
        (fail_dir / f"{stem}.lio").write_text(minimal)
        fe = run([sys.executable, str(FRONTEND), str(fail_dir / f"{stem}.lio")])
        if fe.returncode == 0:
            (fail_dir / f"{stem}.ir").write_text(fe.stdout)

        print(f"minimal repro written to {fail_dir / (stem + '.py')} "
              f"and {fail_dir / (stem + '.ir')}")
        print("--- minimal source ---")
        print(minimal)
        reval = evaluate(fail_dir / f"{stem}.lio", tmp_dir / "reval.ir")
        print(f"interpreter: rc={reval.interp_rc} stdout={reval.interp_out!r}")
        print(f"jit        : rc={reval.jit_rc} stdout={reval.jit_out!r}")
        print(f"cpython    : rc={reval.cpy_rc} stdout={reval.cpy_out!r}")
        print("(rc=%d means the run hit --timeout and was killed)" % TIMEOUT_RC)
        by_reason[result.reason] = by_reason.get(result.reason, 0) + 1

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"\n{n_ok} passed, {n_skip} skipped (rejected before reaching the "
          f"interp/JIT comparison), {n_fail} mismatches, out of {args.count}")
    for reason, n in sorted(by_reason.items()):
        meaning = {"jit_vs_interp": "JIT BUG: native output differs from the interpreter",
                   "interp_vs_cpython": "language gap: Lithon output differs from CPython",
                   "hang_vs_cpython": "language gap: Lithon does not terminate, CPython does"}.get(reason, "")
        print(f"  {reason}: {n}   {meaning}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
