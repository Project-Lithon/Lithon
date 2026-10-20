import type { DocPage } from "../model"

const types: DocPage = {
  slug: "types",
  title: "Types & widths",
  description:
    "Fixed-width integers, floats, bool, and the three rules the verifier enforces on every conversion.",
  group: "language",
  tags: [
    "types",
    "int",
    "float",
    "bool",
    "widths",
    "widening",
    "narrowing",
    "conversion",
  ],
  sections: [
    {
      id: "the-widths",
      title: "The widths",
      blocks: [
        {
          kind: "p",
          text: "Integers carry their width in the type. There is no bare `int`: it is rejected, and no arbitrary-precision integer behind your back:",
        },
        {
          kind: "code",
          example: {
            title: "every scalar type",
            source: `i8:  int[8]  = 7
i16: int[16] = 7
i32: int[32] = 7
i64: int[64] = 7
f64: float[64] = 0.5
b:   bool = True
print(i64)`,
            output: "7",
          },
        },
        {
          kind: "table",
          caption: "Scalar types",
          rows: [
            [
              "`int[8]` … `int[64]`",
              "signed fixed-width two's-complement integer",
            ],
            ["`float[64]`", "IEEE-754 binary64"],
            ["`bool`", "one byte, `True` / `False`"],
            [
              "`ptr[T]`",
              "typed pointer to a scalar `T`: see [Pointers](/docs/pointers)",
            ],
          ],
        },
        {
          kind: "note",
          tone: "note",
          title: "Signed only, 64-bit floats only",
          text: "There is no unsigned `uint` type: `uint[8]` is rejected as an unknown type, so masking with `&` is how you get unsigned behaviour, explicitly. `float[32]` is rejected too (`LITHON-E0105: unsupported float width 32`): every float is `float[64]`.",
        },
        {
          kind: "note",
          tone: "note",
          title: "Why fixed widths are load-bearing",
          text: "A fixed width is a fact the backend can plan around: the register, the memory slot, the overflow instruction. An unbounded `int` would mean boxing, big-integer fallbacks, and guards: exactly what the native tier is built to not have.",
        },
      ],
    },
    {
      id: "widening",
      title: "Widening is automatic, narrowing is never",
      blocks: [
        {
          kind: "p",
          text: "A value may be widened to a provably larger width of the same signedness class, automatically. Narrowing, the reverse direction, is rejected even when you are certain the value fits:",
        },
        {
          kind: "code",
          example: {
            title: "widening · accepted",
            source: `small: int[8] = 5
big: int[64] = small
print(big)`,
            output: "5",
          },
        },
        {
          kind: "code",
          example: {
            title: "rejected · V1_SPEC 0.6.11",
            expect: "error",
            errorMatch: "cannot narrow",
            source: `wide: int[64] = 5
narrow: int[8] = wide
print(narrow)`,
            output:
              "RCR error: declaration of 'narrow': cannot narrow int[64] into int[8] -- narrowing is never allowed (V1_SPEC 0.6.11)",
          },
        },
        {
          kind: "p",
          text: "No cast syntax exists anywhere in the language. If you need a bit pattern reinterpreted, that is what pointers are for: explicitly, with the `_` prefix.",
        },
      ],
    },
    {
      id: "int-float",
      title: "int → float is automatic; float → int is not",
      blocks: [
        {
          kind: "p",
          text: "`int[64]` into `float[64]` is a widening conversion and happens silently. The reverse is rejected: it would truncate, and truncation is not a conversion Lithon will perform for you.",
        },
        {
          kind: "note",
          tone: "warn",
          title: "The literal exception (V1_SPEC 0.6.11)",
          text: "An int literal is not a float, so `j: float[64] = 0` is a type error: write `0.0`. The int→float conversion does not apply to a literal.",
        },
        {
          kind: "code",
          example: {
            title: "rejected · V1_SPEC 0.6.11",
            expect: "error",
            errorMatch: "must hold a float",
            source: `j: float[64] = 0
print(j)`,
            output:
              "RCR error: declaration of 'j': literal 0 is an int, but float[64] must hold a float -- write 0.0 (V1_SPEC 0.6.11)",
          },
        },
        {
          kind: "code",
          example: {
            title: "accepted",
            source: `j: float[64] = 0.0
print(j)`,
            output: "0.0",
          },
        },
      ],
    },
    {
      id: "explicit-conversions",
      title: "Explicit conversions and terminal input",
      blocks: [
        {
          kind: "p",
          text: "`int()` and `float()` are not type constructors you call with one argument: the two-argument compiler-recognized form takes an explicit width, `int(w, v)` and `float(64, v)`. `int(w, v)` converts a `float[64]` to `int[w]` and traps (`E0309`) if the value is out of range or not finite; `float(64, v)` converts an `int[8|16|32]` or `float[64]` and is refused at compile time (`E0106`) for a lossy `int[64]` source, since 53 mantissa bits cannot hold every `int[64]` value.",
        },
        {
          kind: "p",
          text: "`int(w, input(prompt))` reads a line from stdin and parses it as `int[w]`, with an optional prompt string. Three traps cover a bad terminal: `E0306` invalid integer literal, `E0307` value outside the target width, `E0308` end of input.",
        },
        {
          kind: "code",
          example: {
            title: "int(w, v), float(64, v), and int(w, input(prompt))",
            typecheck: false,
            source: `f: float[64] = 42.0
n: int[64] = int(64, f)
print(n)

w: int[32] = int(32, input("enter a number: "))
print(w)`,
            machine: {
              ir: `function __main__():
block0:
    %0 = const_f64 42.0
    store f, %0 : float[64]
    %1 = load f
    %2 = toint %1 : int[64]
    store n, %2 : int[64]
    %3 = load n
    call print_bare, %3
    call print_nl
    %4 = inputint 656e7465722061206e756d6265723a20 : int[32]
    store w, %4 : int[32]
    %5 = load w
    call print_bare, %5
    call print_nl
    return`,
              note: "`int()`/`float()` lower to `toint`/`tofloat`; `int(w, input(prompt))` lowers to one `inputint` op, the prompt hex-encoded so any character survives the text IR.",
            },
          },
        },
        {
          kind: "note",
          tone: "warn",
          title: "tools/typecheck.py lags here too",
          text: "Like `while`, the standalone checker does not yet recognize `int()`/`float()` as compiler builtins (`call to unknown function 'int'`); `src/frontend/frontend.py` lowers them correctly today. Verified against the real frontend, not the standalone checker, until it catches up.",
        },
      ],
    },
    {
      id: "mixed-arithmetic",
      title: "Mixed int/float arithmetic",
      blocks: [
        {
          kind: "p",
          text: "An int operand combined with a float operand produces a float. The int is converted to the float's width first, then the operation happens in float land:",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/mixed_numeric.lio",
            sourceRef: "tests/typed_regression/mixed_numeric.lio",
            expectedRef: "tests/typed_regression/expected/mixed_numeric.out",
            source: `i: int[64] = 10
f: float[64] = 2.5
print(i + f)
print(f + i)
print(i / 4)`,
            output: "12.5\n12.5\n2.5",
          },
        },
      ],
    },
    {
      id: "no-bare-int",
      title: "There is no bare `int`",
      blocks: [
        {
          kind: "p",
          text: "`int` without a width is rejected: the width is part of the type, not an implementation detail the compiler may pick for you. The same goes for `int[128]`: only 8/16/32/64 exist, matching the machine.",
        },
        {
          kind: "note",
          tone: "note",
          title: "Provable overflow is a compile-time error",
          text: "Integer literal overflow and provable-range overflow for binary operations are compile-time errors (V1_SPEC 0.6.5): the checker points you at `wrap_add()`/`wrap_sub()`/`wrap_mul()` when wrap-around is what you want. Overflow the verifier cannot prove at compile time on `int[64]` traps at run time instead (`E0303`), identically in both tiers; `wrap_add`/`wrap_sub`/`wrap_mul` are the explicit opt-out that keeps the machine's two's-complement wrap instead of trapping.",
        },
      ],
    },
  ],
}

const declarations: DocPage = {
  slug: "declarations",
  title: "Declarations & assignment",
  description:
    "Definite assignment, one type per name, and the loop-variable rules the verifier enforces.",
  group: "language",
  tags: [
    "declaration",
    "assignment",
    "definite assignment",
    "uninitialized",
    "scope",
    "variable",
    "0.6.10",
  ],
  sections: [
    {
      id: "one-type-per-name",
      title: "One type per name",
      blocks: [
        {
          kind: "p",
          text: "A name is declared once with an explicit type. Re-assignment is checked against that declared type: you cannot quietly reuse the name for a different width or a different type:",
        },
        {
          kind: "code",
          example: {
            title: "accepted · re-assignment within the type",
            source: `x: int[64] = 10
x = 20
print(x)`,
            output: "20",
          },
        },
        {
          kind: "code",
          example: {
            title: "rejected · re-assignment narrows",
            expect: "error",
            errorMatch: "cannot narrow",
            source: `x: int[64] = 10
x = 20
narrow: int[8] = x
print(narrow)`,
            output:
              "RCR error: declaration of 'narrow': cannot narrow int[64] into int[8] -- narrowing is never allowed (V1_SPEC 0.6.11)",
          },
        },
      ],
    },
    {
      id: "definite-assignment",
      title: "Definite assignment",
      blocks: [
        {
          kind: "p",
          text: "Every use of a variable must be dominated by an assignment whose value the verifier can trace. You may declare first and assign later, but you may not read before the first assignment.",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/uninit_decl.lio",
            sourceRef: "tests/typed_regression/uninit_decl.lio",
            expectedRef: "tests/typed_regression/expected/uninit_decl.out",
            source: `i: int[8]
i = 5
print(i)`,
            output: "5",
          },
        },
        {
          kind: "code",
          example: {
            title: "rejected · V1_SPEC 0.6.10",
            expect: "error",
            errorMatch: "never assigned a value",
            source: `i: int[8]
print(i)`,
            output:
              "RCR error: call statement: 'i' was declared with a type but never assigned a value (V1_SPEC 0.6.10)",
          },
        },
        {
          kind: "note",
          tone: "note",
          title: "Declaration is not initialization: except for containers",
          text: "For a scalar, a bare `i: int[64]` only records the type. For containers the bare declaration **is** real work: it reserves the run/table and zeroes it. See [Containers](/docs/containers).",
        },
      ],
    },
    {
      id: "branches-and-loops",
      title: "Branches and loops",
      blocks: [
        {
          kind: "p",
          text: "Scoping is function-scoped, not block-scoped: a name declared inside an `if` is visible after the `if`. Definite assignment, however, requires that **every branch** that reaches a use has assigned the variable:",
        },
        {
          kind: "code",
          example: {
            title: "rejected · one branch forgets",
            expect: "error",
            errorMatch: "not definitely assigned here",
            source: `x: int[64] = 1
if x < 3:
    y: int[64] = 2
print(y)`,
            output:
              "RCR error: call statement: 'y' is not definitely assigned here (V1_SPEC 0.6.10)",
          },
        },
        {
          kind: "p",
          text: "The same rule applies to loop variables: a `while` condition that reads a counter requires the counter to have been assigned before the loop, and the update inside the body is what makes the next iteration's read valid.",
        },
      ],
    },
  ],
}

const controlFlow: DocPage = {
  slug: "control-flow",
  title: "Control flow",
  description:
    "if / elif / else, while, for over range, try / except over the built-in traps, and the one construct that does not exist.",
  group: "language",
  tags: [
    "control flow",
    "if",
    "else",
    "while",
    "for",
    "range",
    "branch",
    "loop",
    "try",
    "except",
    "exception",
    "trap",
  ],
  sections: [
    {
      id: "if",
      title: "if / elif / else",
      blocks: [
        {
          kind: "p",
          text: "Exactly the Python you know. Conditions are boolean expressions; there is no truthiness conversion: a condition must be a `bool`.",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/if.lio",
            sourceRef: "tests/typed_regression/if.lio",
            expectedRef: "tests/typed_regression/expected/if.out",
            source: `x: int[64] = 5
if x < 3:
    print(1)
else:
    print(3)`,
            output: "3",
            machine: {
              ir: `function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = load x
    %2 = const_i64 3
    %3 = lt %1, %2
    branch %3, block1, block2
block1:
    %4 = const_i64 1
    call print_bare, %4
    call print_nl
    jump block3
block2:
    %5 = const_i64 3
    call print_bare, %5
    call print_nl
    jump block3
block3:
    return`,
              note: "Real frontend output. `branch` is a two-way conditional branch on a comparison; both arms end in an unconditional `jump` to the join block.",
            },
          },
        },
      ],
    },
    {
      id: "while",
      title: "while",
      blocks: [
        {
          kind: "p",
          text: "A `while` is a conditional backward branch. The verifier tracks the loop-carried types across the back edge, so the body may narrow nothing and widen only what was already declared:",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/while.lio",
            sourceRef: "tests/typed_regression/while.lio",
            expectedRef: "tests/typed_regression/expected/while.out",
            typecheck: false,
            source: `i: int[64] = 0
total: int[64] = 0
while i < 10:
    total = total + i
    i = i + 1
print(total)`,
            output: "45",
          },
        },
        {
          kind: "note",
          tone: "warn",
          title: "The checker does not cover `while` yet",
          text: "`tools/typecheck.py` still answers `statement While not supported by the type-checker yet`: including for the repo's own `while.lio`. The engine lowers and executes `while` correctly (Tier-0 and Tier-1 both); it is the standalone Python checker that lags. Until it catches up, `while` programs are verified through the regression suites rather than the checker.",
        },
      ],
    },
    {
      id: "for-range",
      title: "for over range()",
      blocks: [
        {
          kind: "p",
          text: "`range(stop)` and `range(start, stop)` produce values checked against the loop variable's declared width at compile time when known (V1_SPEC 0.6.12). The loop variable is a normal declared binding:",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/for_range.lio",
            sourceRef: "tests/typed_regression/for_range.lio",
            expectedRef: "tests/typed_regression/expected/for_range.out",
            source: `total: int[64] = 0
i: int[64] = 0
for i in range(10):
    total = total + i
print(total)`,
            output: "45",
            machine: {
              ir: `function __main__():
block0:
    %0 = const_i64 0
    store total, %0 : int[64]
    %1 = const_i64 0
    store i, %1 : int[64]
    %2 = const_i64 0
    %3 = const_i64 10
    %4 = const_i64 1
    store i, %2 : int[64]
    jump block4
block4:
    %5 = load i
    %6 = lt %5, %3
    branch %6, block1, block2
block1:
    %7 = load total
    %8 = load i
    %9 = add %7, %8
    store total, %9 : int[64]
    jump block3
block3:
    %10 = load i
    %11 = const_i64 1
    %12 = add %10, %11
    store i, %12
    jump block4
block2:
    %13 = load total
    call print_bare, %13
    call print_nl
    return`,
              note: "The `for` lowers to an init / test / body / increment block structure: a pre-test loop with an explicit back edge, exactly what the encoder emits a `jcc` for.",
            },
          },
        },
      ],
    },
    {
      id: "range-step",
      title: "range with a step",
      blocks: [
        {
          kind: "p",
          text: "`range(start, stop, step)` is the full three-argument form, including a negative `step` for a descending loop. A literal zero step is a compile-time refusal (`E0107`); a runtime-computed zero step traps (`E0310`) instead, since the checker cannot prove it in advance:",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/range_step.lio",
            typecheck: false,
            sourceRef: "tests/programs/range_step.lio",
            expectedRef: "tests/programs/expected/range_step.out",
            source: `for i in range(1, 10, 3):
    print(i)
for i in range(10, 1, -3):
    print(i)
for i in range(0, 5, 1):
    print(i)
for i in range(5, 5, 1):
    print(i)
for i in range(1, 5, 6):
    print(i)
for i in range(-5, -1, 1):
    print(i)
for i in range(-1, -5, -2):
    print(i)`,
            output: "1\n4\n7\n10\n7\n4\n0\n1\n2\n3\n4\n1\n-5\n-4\n-3\n-2\n-1\n-3",
          },
        },
        {
          kind: "note",
          tone: "warn",
          title: "Empty ranges are not an error",
          text: "`range(5, 5, 1)` and a step pointed the wrong way (e.g. `range(1, 5, -1)`) both produce zero iterations, matching CPython: only `step == 0` is refused or trapped.",
        },
      ],
    },
    {
      id: "break-continue",
      title: "break and continue",
      blocks: [
        {
          kind: "p",
          text: "`break` and `continue` work in `for` and `while` bodies: `break` exits the innermost loop, `continue` jumps to its back edge. Either one used outside a loop is a compile-time refusal (`E0120`). The lowering rotates the loop body so a multi-exit loop still has one guarded back edge, not a spaghetti of jumps:",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/break_continue.lio",
            typecheck: false,
            sourceRef: "tests/programs/break_continue.lio",
            expectedRef: "tests/programs/expected/break_continue.out",
            source: `total: int[64] = 0
for i in range(100):
    if i == 5:
        continue
    if i == 10:
        break
    total = total + i
print(total)`,
            output: "40",
          },
        },
      ],
    },
    {
      id: "enumerate-and-zip",
      title: "enumerate and zip",
      blocks: [
        {
          kind: "p",
          text: "`enumerate(xs)` and `zip(xs, ys)` work in a `for` header. Neither is a value: both are a **rewrite of a counted loop** over constructs that already exist, so there is no new opcode and no runtime helper. `for i, x in enumerate(xs)` becomes `for i in range(N): x = xs[i]`, and `for a, b in zip(xs, ys)` becomes the same loop bounded at `min(N1, N2)` — a compile-time constant, because the declared capacity is what `len()` folds to.",
        },
        {
          kind: "p",
          text: "`zip` stops at the shortest input, exactly as Python does. Elements bind with their declared type, and `break`/`continue` work inside either header because the rewrite is a real loop.",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/iter_helpers.lio (excerpt)",
            typecheck: false,
            sourceRef: "tests/typed_regression/iter_helpers.lio",
            expectedRef: "tests/typed_regression/expected/iter_helpers.out",
            source: `xs: list[int[64], 5]
xs[0] = 10
xs[1] = 20
xs[2] = 30
xs[3] = 40
xs[4] = 50

for ei, ex in enumerate(xs):
    print(ei)
    print(ex)

long6: list[int[64], 6]
short3: list[int[64], 3]
long6[0] = 1
long6[1] = 2
long6[2] = 3
short3[0] = 7
short3[1] = 8
short3[2] = 9

total: int[64] = 0
for za, zb in zip(long6, short3):
    total = za + zb
print(total)`,
            output: "0\n10\n1\n20\n2\n30\n3\n40\n4\n50\n12",
          },
        },
        {
          kind: "note",
          tone: "warn",
          title: "They are for-header only",
          text: "`z = zip(a, b)` is refused (`LITHON-E0115`). The pair never needs to exist as a value, and a homogeneous tuple could not represent `enumerate`'s `(int, T)` anyway — so the rewrite is the whole design rather than an optimisation of a real iterator.",
        },
      ],
    },
    {
      id: "if-expression",
      title: "if as an expression",
      blocks: [
        {
          kind: "p",
          text: "An `if` used where a value is expected is an **if-expression** with `else` mandatory. Both arms are fully evaluated expressions: statements inside the arms are rejected:",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/if_expr.lio (excerpt)",
            sourceRef: "tests/typed_regression/if_expr.lio",
            expectedRef: "tests/typed_regression/expected/if_expr.out",
            typecheck: false,
            source: `x: int[64] = 5
y: int[64] = 2

print(x if x > y else y)
print(1 if x < y else 2 if x == 5 else 3)`,
            output: "5\n2",
          },
        },
        {
          kind: "note",
          tone: "warn",
          title: "The checker cannot infer a nested merge",
          text: "`tools/typecheck.py` answers `cannot statically determine the type of this expression yet` for a chained if-expression, even though the frontend lowers the real file correctly and CI passes it. Read the Output tab as the oracle here.",
        },
      ],
    },
    {
      id: "for-else",
      title: "There is no for/else",
      blocks: [
        {
          kind: "p",
          text: '`for`/`else`: where the `else` runs when the loop completes without `break`: is rejected. The `else` binds to `if`, not to `for`; if you need the "loop finished" signal, test the loop variable after the loop.',
        },
        {
          kind: "code",
          example: {
            title: "rejected by the frontend",
            expect: "error",
            tool: "frontend",
            errorMatch: "for/else is not supported",
            source: `i: int[8] = 0
for i in range(3):
    print(i)
else:
    print(1)`,
            output:
              "NotImplementedError: for/else is not supported (the else clause would be silently dropped)",
          },
        },
        {
          kind: "p",
          text: "It is rejected loudly rather than dropped, because the alternative: binding the `else` to the `if`: would silently change what the program means.",
        },
      ],
    },
    {
      id: "try-except",
      title: "try / except, over the built-in traps",
      blocks: [
        {
          kind: "p",
          text: "`try`/`except` catches a trap the engine itself raises. There is no exception hierarchy to import and no exception object to bind: the catchable set is closed, and a caught trap is a plain jump in the IR. Both tiers run the same handler region, so a trap caught natively is caught identically in the interpreter.",
        },
        {
          kind: "code",
          example: {
            title: "catching a division by zero",
            // tools/typecheck.py is a mirror of the older scalar-only static
            // checks and does not know `try` yet; the real checker (the C++
            // one, which runs inside tier_runner) does.
            typecheck: false,
            source: `n: int[64] = 0
try:
    q: int[64] = 100 % n
    print(q)
except ZeroDivisionError:
    print(1)`,
            output: "1",
            machine: {
              ir: `function __main__():
block0:
    %0 = const_i64 0
    store n, %0 : int[64]
    tryenter ZeroDivisionError, block1
    jump block2
block2:
    %1 = const_i64 100
    %2 = load n
    %3 = mod %1, %2
    store q, %3 : int[64]
    %4 = load q
    call print_bare, %4
    call print_nl
    tryleave
    jump block3
block1:
    trycatch ZeroDivisionError
    %5 = const_i64 1
    call print_bare, %5
    call print_nl
    jump block3
block3:
    return`,
            },
          },
        },
        {
          kind: "p",
          text: "The IR is the whole mechanism: `tryenter` marks the handler block and the region it guards, `tryleave` ends the protected region on the normal path, and `trycatch` is the landing pad. A caught trap is a branch, not an unwind -- there is no stack to unwind because there is no exception object.",
        },
        {
          kind: "p",
          text: "The catchable set is exactly seven names: `ZeroDivisionError`, `OverflowError`, `ValueError`, `IndexError`, `KeyError`, `EOFError`, and `Exception` for all of them. Anything else is refused at compile time, so a typo in the handler is a diagnostic rather than a silently-never-taken branch.",
        },
        {
          kind: "code",
          example: {
            title: "rejected: not a catchable trap",
            expect: "error",
            tool: "frontend",
            errorMatch: "is not a catchable trap",
            source: `try:
    print(1)
except RuntimeError:
    print(2)`,
            output:
              "LITHON-E0114: `except RuntimeError:` is not a catchable trap -- the catchable set is EOFError, IndexError, KeyError, OverflowError, ValueError, ZeroDivisionError, plus `Exception` for all of them (6.10)",
          },
        },
        {
          kind: "table",
          caption: "which trap lands in which handler",
          rows: [
            [
              "`ZeroDivisionError`",
              "division or modulo by zero",
            ],
            ["`OverflowError`", "a dynamic `int[64]` overflow (`LITHON-E0303`), or an input that does not fit the target width (`E0307`)"],
            [
              "`ValueError`",
              "a float-to-int conversion out of range (`E0309`), a shift count outside 0..63, a `range` step of zero (`E0310`), a non-numeric input (`E0306`)",
            ],
            ["`IndexError`", "a dynamic index outside a list's bounds"],
            ["`KeyError`", "a dict key that is not present"],
            ["`EOFError`", "input() reached end of input (`E0308`)"],
          ],
        },
        {
          kind: "note",
          tone: "warn",
          title: "three things that are deliberately not supported",
          text: "A bare `except:` is refused, because it would also catch a system exit and would suggest catching a segfault, which is not catchable. `except ... as e` is refused, because there is no exception object to bind. `try`/`finally` without an `except` is refused, because a `finally` that runs on the uncaught path is a different construct. And a trap raised inside a *callee* is not catchable by the caller: only traps raised in the same function as the `try` are.",
        },
        {
          kind: "code",
          example: {
            title: "rejected: a bare except",
            expect: "error",
            tool: "frontend",
            errorMatch: "a bare `except:` is not supported",
            source: `try:
    print(1)
except:
    print(2)`,
            output:
              "LITHON-E0114: a bare `except:` is not supported -- it also catches system-exit, and here it would suggest catching a segfault, which is not catchable; name the trap (`except Exception:` catches every catchable trap) (6.10)",
          },
        },
        {
          kind: "p",
          text: "An uncaught trap is still a trap: the program stops with the engine's own diagnostic, and the exit code is non-zero. See [Errors](/docs/errors) for the full code family and [Semantics](/docs/semantics) for the arithmetic traps in detail.",
        },
      ],
    },
  ],
}

const functions: DocPage = {
  slug: "functions",
  title: "Functions",
  description: "Mandatory contracts, the calling convention, and recursion.",
  group: "language",
  tags: [
    "functions",
    "def",
    "return",
    "contract",
    "recursion",
    "parameters",
    "signature",
  ],
  sections: [
    {
      id: "contracts",
      title: "Contracts are mandatory",
      blocks: [
        {
          kind: "p",
          text: "Every parameter and the return type are mandatory and checked (V1_SPEC 0.6.8). A call site is checked against the declared signature, and a declared return type must be provably wide enough for what is returned: the compiler never auto-widens your return.",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/function.lio",
            sourceRef: "tests/typed_regression/function.lio",
            expectedRef: "tests/typed_regression/expected/function.out",
            source: `def add(a: int[64], b: int[64]) -> int[64]:
    return a + b

x: int[64] = add(3, 4)
print(x)`,
            output: "7",
            machine: {
              ir: `function add(a:int[64], b:int[64]) -> int[64]:
block0:
    %0 = load a
    %1 = load b
    %2 = add %0, %1
    return %2

function __main__():
block0:
    %0 = const_i64 3
    %1 = const_i64 4
    %2 = call add, %0, %1
    store x, %2 : int[64]
    %3 = load x
    call print_bare, %3
    call print_nl
    return`,
              note: "Real frontend output. Parameters live in the frame like any other slot; the call passes constants as immediates and stores the return value into the caller's `x` slot.",
            },
          },
        },
        {
          kind: "code",
          example: {
            title: "rejected · missing return type",
            expect: "error",
            errorMatch: "return",
            source: `def add(a: int[64], b: int[64]):
    return a + b

x: int[64] = add(3, 4)
print(x)`,
            output:
              "RCR error: function 'add': return type is mandatory: write '-> int[64]' (V1_SPEC 0.6.8)",
          },
        },
      ],
    },
    {
      id: "recursion",
      title: "Recursion",
      blocks: [
        {
          kind: "p",
          text: "Recursion works like it does in Python: each call gets its own frame. The verifier checks each call against the declared signature, including recursive calls to the function being defined:",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/recursion.lio",
            sourceRef: "tests/typed_regression/recursion.lio",
            expectedRef: "tests/typed_regression/expected/recursion.out",
            source: `def fib(n: int[64]) -> int[64]:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)

x: int[64] = fib(10)
print(x)`,
            output: "55",
            trace: [
              { step: "1", detail: "fib(10)", state: "n = 10", out: "" },
              {
                step: "2",
                detail: "fib(9) + fib(8)",
                state: "n ≥ 2, recurse",
                out: "",
              },
              {
                step: "3",
                detail: "… bottom out at fib(1) / fib(0)",
                state: "return n",
                out: "",
              },
              {
                step: "4",
                detail: "combine",
                state: "fib(10) = 55",
                out: "55",
              },
            ],
            machine: {
              ir: `function fib(n:int[64]) -> int[64]:
block0:
    %0 = load n
    %1 = const_i64 2
    %2 = lt %0, %1
    branch %2, block1, block2
block1:
    %3 = load n
    return %3
block2:
    %4 = load n
    %5 = load n
    %6 = const_i64 1
    %7 = sub %5, %6
    %8 = call fib, %7
    %9 = load n
    %10 = load n
    %11 = const_i64 2
    %12 = sub %10, %11
    %13 = call fib, %12
    %14 = add %8, %13
    return %14

function __main__():
block0:
    %0 = const_i64 10
    %1 = call fib, %0
    store x, %1 : int[64]
    %2 = load x
    call print_bare, %2
    call print_nl
    return`,
              note: "Real frontend output. The base case and the recursive case are separate blocks; `call fib` appears twice, once per recursive branch.",
            },
          },
        },
      ],
    },
    {
      id: "calling-convention",
      title: "The calling convention",
      blocks: [
        {
          kind: "p",
          text: "A native call passes the first two arguments in registers (`rdi`/`rsi`, or `XMM0`/`XMM1` for floats) and every further argument in an 8-byte stack slot, exactly as SysV passes its later arguments. Up to 32 parameters are supported; a call or function past that budget is refused by the native tier and runs in the interpreter rather than mis-passing arguments.",
        },
        {
          kind: "note",
          tone: "note",
          title: "beyond the budget",
          text: "If a signature genuinely needs more than 32 parameters, run that function under the interpreter (the native tier refuses automatically) or restructure the call.",
        },
      ],
    },
  ],
}

const operators: DocPage = {
  slug: "operators",
  title: "Operators",
  description:
    "Arithmetic, comparison, boolean, bitwise and shift: including C-style `%` and two's-complement wrap.",
  group: "language",
  tags: [
    "operators",
    "arithmetic",
    "mod",
    "shift",
    "bitwise",
    "comparison",
    "boolean",
    "wrap",
    "precedence",
  ],
  sections: [
    {
      id: "arithmetic",
      title: "Arithmetic",
      blocks: [
        {
          kind: "table",
          caption: "Operators and their result rules",
          rows: [
            [
              "`+` `-` `*`",
              "same-width result; int×int wraps, float follows IEEE-754",
            ],
            [
              "`/`",
              "float division producing `float[64]`, **not** C-style truncation",
            ],
            ["`%`", "C-style remainder: sign follows the **left** operand"],
            ["`**`", "power: see the limitation note below"],
            ["`//`", "not yet implemented: rejected at parse time"],
          ],
        },
        {
          kind: "code",
          example: {
            title: "C-style % (README)",
            typecheck: false,
            source: `a: int[64] = -7
b: int[64] = 3
print(a % b)
print(7 % 3)
print(-7 % 3)`,
            output: "-1\n1\n-1",
            note: "`%` follows C, not Python: `-7 % 3` is `-1`, not `2`. Python's floor-mod and C's trunc-mod disagree on negatives, and Lithon chose the machine's behaviour. (The checker cannot infer the type of a negated-literal binding yet; the frontend and both tiers handle it.)",
          },
        },
      ],
    },
    {
      id: "comparison-boolean",
      title: "Comparison and boolean",
      blocks: [
        {
          kind: "p",
          text: "`==` `!=` `<` `>` and the other comparisons produce `bool`. `and` `or` `not` operate on `bool`. `!=` is a first-class comparison with its own IR op (`ne`), not an `==` followed by a `not`, so the optimizer and branch fusion see the real predicate.",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/comparison.lio",
            sourceRef: "tests/typed_regression/comparison.lio",
            expectedRef: "tests/typed_regression/expected/comparison.out",
            source: `a: int[64] = 1
b: int[64] = 2
c: float[64] = 1.5
print(a < b)
print(b > a)
print(a == a)
print(c < b)`,
            output: "True\nTrue\nTrue\nTrue",
          },
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/ne.lio (excerpt)",
            sourceRef: "tests/typed_regression/ne.lio",
            expectedRef: "tests/typed_regression/expected/ne.out",
            typecheck: false,
            source: `a: int[64] = 1
b: int[64] = 2
print(a != b)
print(a != a)
lo: int[8] = -128
hi: int[8] = 127
print(lo != hi)`,
            output: "True\nFalse\nTrue",
          },
        },
        {
          kind: "note",
          tone: "note",
          title: "int and float compare across widths",
          text: "The fourth line compares a `float[64]` against an `int[64]` and returns `True`: the int is widened to float for the comparison, the same promotion rule as arithmetic.",
        },
      ],
    },
    {
      id: "bitwise-shift",
      title: "Bitwise and shift",
      blocks: [
        {
          kind: "p",
          text: "`<<` `>>` `&` `|` `^` are integer-only and **not** part of the arithmetic promotion rule: `2.5 & 1` is a type error, because Lithon has no float bit pattern to reinterpret. Their result takes the left operand's width.",
        },
        {
          kind: "p",
          text: "A literal shift count must be `0..63`: the machine word is 64 bits and x86 masks the count to 6 bits, so a count of 64 would silently execute as 0. A non-literal count is checked at runtime instead. This is a deliberate divergence from CPython, where ints are unbounded and `1 << 64` is a valid 65-bit result:",
        },
        {
          kind: "code",
          example: {
            title: "rejected · bitwise on a float",
            expect: "error",
            errorMatch: "bitwise operand has type float",
            source: `print(2.5 & 1)`,
            output:
              "RCR error: call statement: bitwise operand has type float[64] -- shl/shr/and/or/xor are integer-only; Lithon has no float bit pattern to reinterpret (V1_SPEC 0.6.11)",
          },
        },
        {
          kind: "code",
          example: {
            title: "rejected · shift count out of range",
            expect: "error",
            errorMatch: "shift count",
            source: `print(1 << 64)`,
            output:
              "RCR error: call statement: shift count 64 is out of range 0..63 for `<<` -- the machine word is 64 bits and a count outside 0..63 has no defined meaning (V1_SPEC 0.6.11)",
          },
        },
        {
          kind: "code",
          example: {
            title: "accepted",
            source: `x: int[64] = 1 << 10
y: int[64] = x >> 3
z: int[64] = x & 0xFF
print(x)
print(y)
print(z)`,
            output: "1024\n128\n0",
          },
        },
      ],
    },
    {
      id: "wrap-arith",
      title: "Explicit wrap-around arithmetic",
      blocks: [
        {
          kind: "p",
          text: 'Plain `+`/`-`/`*` on fixed-width ints wraps in two\'s complement at run time, but overflow the verifier can prove statically is rejected at compile time (E0303) and points you at the `wrap_*` family. `wrap_add`/`wrap_sub`/`wrap_mul` are the explicit way to say "wrap-around is what I want": the same wrapped value in both tiers:',
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/int64_wrap.lio",
            sourceRef: "tests/typed_regression/int64_wrap.lio",
            expectedRef: "tests/typed_regression/expected/int64_wrap.out",
            typecheck: false,
            source: `x: int[64] = 9223372036854775807
y: int[64] = 1
z: int[64] = wrap_add(x, y)
print(z)

a: int[64] = -9223372036854775808
b: int[64] = 1
c: int[64] = wrap_sub(a, b)
print(c)

p: int[64] = 3037000500
q: int[64] = 3037000500
r: int[64] = wrap_mul(p, q)
print(r)`,
            output:
              "-9223372036854775808\n9223372036854775807\n-9223372036709301616",
            trace: [
              {
                step: "1",
                detail: "wrap_add(2⁶³−1, 1)",
                state: "wraps to −2⁶³",
                out: "-9223372036854775808",
              },
              {
                step: "2",
                detail: "wrap_sub(−2⁶³, 1)",
                state: "wraps to 2⁶³−1",
                out: "9223372036854775807",
              },
              {
                step: "3",
                detail: "wrap_mul(3037000500, 3037000500)",
                state: "9223010250000000000 mod 2⁶⁴",
                out: "-9223372036709301616",
              },
            ],
          },
        },
      ],
    },
  ],
}

const containers: DocPage = {
  slug: "containers",
  title: "Containers",
  description:
    "Fixed-capacity lists, tuples, and constant-keyed dicts: declared, reserved, sized at compile time.",
  group: "language",
  tags: [
    "containers",
    "list",
    "tuple",
    "dict",
    "capacity",
    "index",
    "len",
    "hash",
  ],
  sections: [
    {
      id: "list",
      title: "list: fixed capacity",
      blocks: [
        {
          kind: "p",
          text: "A `list[T, N]` has a compile-time capacity `N` that is part of the type. The element type is a fixed-width scalar. The bare declaration `xs: list[int[64], 6]` **is** real work: it reserves the run and zeroes it: drop it and the first store writes to a name that was never allocated.",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/list_basic.lio (excerpt)",
            sourceRef: "tests/programs/list_basic.lio",
            expectedRef: "tests/programs/expected/list_basic.out",
            typecheck: false,
            source: `xs: list[int[64], 6]

xs[0] = 10
xs[1] = 20
xs[2] = 30

print(xs[0])
print(xs[1])
print(xs[2])`,
            output: "10\n20\n30",
          },
        },
        {
          kind: "table",
          caption: "list rules",
          rows: [
            [
              "Capacity",
              "`N` is in the type; `len(xs)` returns `N` and cannot change at run time",
            ],
            [
              "Unwritten slots",
              "read back as zero of their kind (the declaration zeroes the run)",
            ],
            [
              "Index bound",
              "checked against `N`: a runtime index is checked, not a literal the compiler happened to see",
            ],
            [
              "Element address",
              "element `k` lands at `base + k * sizeof(T)`: packed, no pointers per element",
            ],
          ],
        },
      ],
    },
    {
      id: "tuple",
      title: "tuple",
      blocks: [
        {
          kind: "p",
          text: "A fixed-length product of scalar types. Indexing with a literal is checked at compile time against the declared length:",
        },
        {
          kind: "code",
          example: {
            title: "a tuple",
            typecheck: false,
            source: `t: tuple[int[64], 4] = (1, 2, 3, 4)
print(t[0])
print(t[3])`,
            output: "1\n4",
          },
        },
        {
          kind: "note",
          tone: "note",
          title: "One element type, a literal capacity",
          text: "A tuple is `tuple[T, N]`: one element type and a literal length. Anything else is rejected: `tuple[int[64], float[64], bool]` fails with *needs an element type and a literal capacity*.",
        },
        {
          kind: "note",
          tone: "note",
          title: "Containers are ahead of the standalone checker",
          text: "`tools/typecheck.py` answers `unknown type 'list'`: the container types are implemented in the engine (and covered by the regression suites) but the standalone Python checker has not been taught them yet.",
        },
      ],
    },
    {
      id: "dict",
      title: "dict: constant-keyed",
      blocks: [
        {
          kind: "p",
          text: "A `dict[K, V, B]` is a fixed-capacity open-addressing table with `B` buckets, built from a constant-keyed literal. The valueless declaration reserves and zeroes the table, so the literal is a fill of a table that already exists: not an allocation trick. Literal keys are resolved while compiling; a runtime key takes the hash-and-probe path.",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/dict_basic.lio (excerpt)",
            sourceRef: "tests/programs/dict_basic.lio",
            expectedRef: "tests/programs/expected/dict_basic.out",
            typecheck: false,
            source: `d: dict[int[64], int[64], 4] = {1: 10, 2: 20, 3: 30, 4: 40}
print(d[1])
print(d[4])

k: int[64] = 2
print(d[k])
j: int[64] = k + 1
print(d[j])`,
            output: "10\n40\n20\n30",
          },
        },
        {
          kind: "note",
          tone: "note",
          title: "More entries than buckets is a duplicate-bucket problem",
          text: "`dict_basic.lio` stays one short of it and leans on the probe instead. `dict_table.lio` is the test that pins a collision down to fixed keys rather than leaving it to chance.",
        },
      ],
    },
    {
      id: "where-to-go-next",
      title: "Where to go next",
      blocks: [
        {
          kind: "linkcards",
          items: [
            {
              title: "Pointers",
              href: "/docs/pointers",
              text: "Raw addresses, `addressof`/`valueof`, and the `_` naming rule.",
            },
            {
              title: "Structs",
              href: "/docs/structs",
              text: "Packed storage: fields at fixed offsets, no padding.",
            },
            {
              title: "Enums",
              href: "/docs/enums",
              text: "A tag plus a payload, read with `match`.",
            },
            {
              title: "Type functions",
              href: "/docs/type-functions",
              text: "Compile-time type constructors that expand structurally.",
            },
            {
              title: "Built-in functions",
              href: "/docs/builtins",
              text: "The whole prelude, one row per name.",
            },
          ],
        },
      ],
    },
  ],
}

const pointers: DocPage = {
  slug: "pointers",
  title: "Pointers",
  description:
    "Typed pointers with a mandatory `_` prefix: explicit unsafe memory, no hidden aliasing.",
  group: "language",
  tags: [
    "pointers",
    "ptr",
    "addressof",
    "valueof",
    "unsafe",
    "memory",
    "underscore",
  ],
  sections: [
    {
      id: "the-underscore-rule",
      title: "The `_` rule",
      blocks: [
        {
          kind: "p",
          text: "Every pointer variable carries a **mandatory leading `_`**. This is not a naming convention the compiler happens to enforce: it is a source-level marker that raw memory is in play, greppable and impossible to miss in review.",
        },
        {
          kind: "code",
          example: {
            title: "rejected · missing underscore",
            expect: "error",
            tool: "frontend",
            errorMatch: "named with a leading underscore",
            source: `x: int[64] = 5
p: ptr[int[64]] = addressof(x)
print(valueof(p))`,
            output:
              "NotImplementedError: a pointer is named with a leading underscore, write `_p: ptr[...]` or pick a name that starts with '_' (4.4)",
          },
        },
      ],
    },
    {
      id: "basics",
      title: "addressof and valueof",
      blocks: [
        {
          kind: "p",
          text: "`addressof(x)` produces a `ptr[T]` to `x`; `valueof(_p)` reads through it. Pointer equality compares addresses. Pointer arithmetic (`_p + 1`, `_p - 1`) steps by `sizeof(T)`, not by bytes.",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/ptr_basic.lio (excerpt)",
            sourceRef: "tests/programs/ptr_basic.lio",
            expectedRef: "tests/programs/expected/ptr_basic.out",
            typecheck: false,
            source: `x: int[64] = 5
_p: ptr[int[64]] = addressof(x)
print(valueof(_p))
q: int[64] = 6
_q: ptr[int[64]] = addressof(q)
print(valueof(_q))
print(_p == _q)`,
            output: "5\n6\nFalse",
          },
        },
      ],
    },
    {
      id: "restrictions",
      title: "What pointers cannot do",
      blocks: [
        {
          kind: "table",
          caption: "Restrictions",
          rows: [
            ["Type changes", "a pointer cannot change its pointee type"],
            [
              "Arithmetic",
              "only same-type offset arithmetic (`_p ± n`, `_p1 − _p2`); no arbitrary casts",
            ],
            [
              "Deref",
              "no `*_p` syntax: `valueof` / assignment through the pointer",
            ],
            [
              "Null",
              "`_p == 0` is a comparison; there is no null-deref path in safe code",
            ],
          ],
        },
        {
          kind: "note",
          tone: "danger",
          title: "Pointers are unsafe by design",
          text: "The `_` prefix marks the boundary. Inside it, the compiler's usual guarantees about definite assignment and type agreement do not extend through raw memory writes, that is the deal you take when you reach for `ptr`.",
        },
      ],
    },
    {
      id: "float-pointers",
      title: "Pointers to floats and bools",
      blocks: [
        {
          kind: "p",
          text: "`ptr` works for any scalar pointee: `float[32]`, `float[64]`, `bool`. Reading through a `ptr[float[64]]` after the pointee changed gives you the new value, because the pointer names a location, not a snapshot:",
        },
        {
          kind: "code",
          example: {
            title: "reading through a pointer (ptr_basic.lio excerpt)",
            sourceRef: "tests/programs/ptr_basic.lio",
            typecheck: false,
            source: `f: float[64] = 2.5
_pf: ptr[float[64]] = addressof(f)
print(valueof(_pf))
f = 3.25
print(valueof(_pf))`,
            output: "2.5\n3.25",
          },
        },
      ],
    },
  ],
}

const structs: DocPage = {
  slug: "structs",
  title: "Structs",
  description:
    "A named run of typed fields packed into one byte blob: declared offsets, no padding, no struct value.",
  group: "language",
  tags: [
    "structs",
    "struct",
    "fields",
    "layout",
    "offsets",
    "packed",
    "records",
  ],
  sections: [
    {
      id: "what-a-struct-is",
      title: "A struct is storage, not a value",
      blocks: [
        {
          kind: "p",
          text: "A `struct` is a named run of typed fields laid out end to end in one blob of bytes. Declaring `p: Point` reserves and zeroes those bytes, exactly the way a list declaration reserves its element run. A struct **has no value**: the only operations are reading a field and writing a field, and the whole struct is never an expression you can print or assign.",
        },
        {
          kind: "p",
          text: "The spelling is Lithon's own: a struct is a class deriving from `Struct`, whose body is nothing but field annotations.",
        },
      ],
    },
    {
      id: "declaring-and-fields",
      title: "Declaring a struct and writing fields",
      blocks: [
        {
          kind: "p",
          text: "Each `name: type` line is a field. The declaration `p: Point` zeroes the blob; writing a field is a partial write into that blob at a fixed offset the frontend computed, not a rebinding of the name.",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/struct_basic.lio (excerpt)",
            sourceRef: "tests/programs/struct_basic.lio",
            expectedRef: "tests/programs/expected/struct_basic.out",
            typecheck: false,
            source: `class Point(Struct):
    x: int[64]
    y: int[64]


p: Point
p.x = 3
p.y = 4
print(p.x)
print(p.y)`,
            output: "3\n4",
          },
        },
        {
          kind: "p",
          text: "A field nobody wrote reads back as the zero of its kind: `0` for an int, `False` for a bool, `0.0` for a float. This is the struct analogue of an unwritten list element reading zero.",
        },
        {
          kind: "note",
          tone: "note",
          title: "narrow fields are signed",
          text: "An `int[8]`/`int[16]`/`int[32]` field is signed: writing `-3` into an `int[16]` field and reading it back yields `-3`, not the zero-extended `65533`. `bool` is the exception and reads back as `0`/`1`. This is the same rule as a list element or a pointer dereference of the same width.",
        },
        {
          kind: "code",
          example: {
            title: "unwritten fields read zero (struct_basic.lio excerpt)",
            sourceRef: "tests/programs/struct_basic.lio",
            typecheck: false,
            source: `class SockAddrIn(Struct):
    family: int[16]
    port: int[16]
    addr: int[64]
    active: bool


s: SockAddrIn
s.family = 2
s.port = 80
print(s.family)
print(s.port)
print(s.addr)
print(s.active)`,
          },
        },
      ],
    },
    {
      id: "layout",
      title: "Layout: packed, no padding",
      blocks: [
        {
          kind: "p",
          text: "Fields sit at exactly the offsets their sizes imply: the first field at 0, each next field immediately after the previous. **No padding is inserted**, so the size of a struct is the sum of its field sizes. `bool` is 1 byte, `float[64]` is 8, and `int[w]` is `w / 8`.",
        },
        {
          kind: "table",
          caption: "SockAddrIn · 13 bytes, no padding",
          rows: [
            ["0", "family : int[16]"],
            ["2", "port : int[16]"],
            ["4", "addr : int[64]"],
            ["12", "active : bool"],
          ],
        },
        {
          kind: "p",
          text: "Because the layout is a pure sum, the `struct[N]` size the frontend bakes into the declaration is exactly what a C `offsetof` / `sizeof` computation over a `#pragma pack(1)` struct would produce. `tools/check_struct_layout.py` compiles the same struct definitions with `cc`, runs the frontend, and asserts that equality field by field.",
        },
        {
          kind: "note",
          tone: "note",
          title: "A field is a scalar or an earlier struct",
          text: "A field type is `int[w]`, `float[64]`, `bool`, or a struct declared **before** this one. A field may not be a list, tuple, dict, or pointer. A nested struct field is flattened: `seg.a.x` resolves to a single leaf read at the summed byte offset, so there is never an intermediate struct value.",
        },
      ],
    },
    {
      id: "float-fields",
      title: "Float fields",
      blocks: [
        {
          kind: "p",
          text: "A `float[64]` field always occupies the full eight bytes and goes through the double path, so an arithmetic read is exact and a negative value survives unchanged.",
        },
        {
          kind: "code",
          example: {
            title: "float fields (struct_basic.lio excerpt)",
            sourceRef: "tests/programs/struct_basic.lio",
            typecheck: false,
            source: `class Vec(Struct):
    x: float[64]
    y: float[64]


v: Vec
v.x = 1.5
v.y = -2.25
print(v.x)
print(v.y)
print(v.x + v.y)`,
          },
        },
      ],
    },
    {
      id: "restrictions",
      title: "What structs cannot do",
      blocks: [
        {
          kind: "table",
          caption: "Restrictions",
          rows: [
            ["Whole value", "a struct is never an expression; `print(p)` and `q = p` are refused"],
            ["Field kind", "no list/tuple/dict/pointer fields: only scalars and earlier structs"],
            ["Field as value", "`seg.inner` alone is refused; flatten to a leaf (`seg.inner.x`)"],
            ["Narrowing", "a value wider than the field is refused, exactly as for a list element"],
            ["Parameters", "structs are not passed to or returned from functions"],
          ],
        },
        {
          kind: "code",
          example: {
            title: "rejected · a container field",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "LITHON-E0127",
            source: `class Foo(Struct):
    xs: list[int[64], 4]

f: Foo
print(0)`,
          },
        },
        {
          kind: "code",
          example: {
            title: "rejected · the whole struct as a value",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "LITHON-E0128",
            source: `class Foo(Struct):
    x: int[64]

f: Foo
print(f)`,
          },
        },
      ],
    },
  ],
}

const enums: DocPage = {
  slug: "enums",
  title: "Enums",
  description:
    "A tagged union: one variant active at a time, packed as a tag plus the largest payload, read back with match.",
  group: "language",
  tags: [
    "enums",
    "enum",
    "tagged union",
    "variants",
    "match",
    "pattern matching",
    "tag",
    "payload",
  ],
  sections: [
    {
      id: "what-an-enum-is",
      title: "An enum is storage, not a value",
      blocks: [
        {
          kind: "p",
          text: "An `enum` is a tagged union: a byte blob holding an `int` tag and room for the payload of whichever variant is active. Like a struct it **has no value** — the only operations are constructing a variant, reading the tag with `match`, and reading a payload through the arm's binding. The whole enum is never an expression you can print, assign, pass, or return.",
        },
        {
          kind: "p",
          text: "The spelling is Lithon's own: an enum is a class deriving from `Enum`. Each body line declares one variant, in one of three forms — a bare unit name, a `name: type` payload annotation, or an inline `class V(Struct)` struct payload.",
        },
      ],
    },
    {
      id: "declaring-and-variants",
      title: "Declaring an enum and its variants",
      blocks: [
        {
          kind: "p",
          text: "The declaration `s: Shape` reserves and zeroes the blob, exactly the way a struct declaration does. The tag is written by a construction and read by a `match`; the variant names are never values in scope.",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/enum_basic.lio (excerpt)",
            sourceRef: "tests/programs/enum_basic.lio",
            expectedRef: "tests/programs/expected/enum_basic.out",
            typecheck: false,
            source: `class Shape(Enum):
    Empty
    Dot
    Line: int[32]
    Rect: int[64]


s: Shape
s = Shape.Dot
match s:
    case Shape.Empty:
        print(0)
    case Shape.Dot:
        print(1)
    case Shape.Line(n):
        print(n)
    case Shape.Rect(w):
        print(w)`,
            output: "1",
          },
        },
        {
          kind: "p",
          text: "The three variant forms are: a bare name (`Empty`, `Dot`) is a unit variant with no payload; `Line: int[32]` is a payload variant whose variant carries one scalar; and an inline `class Tcp(Struct): ...` is a struct payload whose fields are packed at the payload offset. Two variants may not share a name.",
        },
      ],
    },
    {
      id: "layout",
      title: "Layout: a tag, then the largest payload",
      blocks: [
        {
          kind: "p",
          text: "The layout is `[tag][payload]` with **no padding**. The tag sits at offset 0. It is an `int[8]` while the enum has at most 128 variants (a signed byte holds 0..127) and widens to `int[16]` past that. The payload starts at the tag width, and the enum's size is the tag width plus the largest payload of any variant.",
        },
        {
          kind: "table",
          caption: "Shape · 9 bytes, no padding",
          rows: [
            ["0", "tag : int[8] (Empty=0, Dot=1, Line=2, Rect=3)"],
            ["1", "payload : largest is Rect's int[64], 8 bytes"],
          ],
        },
        {
          kind: "p",
          text: "Only the active variant's payload bytes are ever read. Overwriting a payload variant with a smaller one leaves the old bytes in place but inactive, so a later `match` can never observe them. The size the frontend bakes into the `struct[N]` declaration is exactly what a C `offsetof`/`sizeof` over a `#pragma pack(1)` tag-plus-union would produce; `tools/check_enum_layout.py` compiles that C layout, runs the frontend, and asserts the two agree at both tag-width boundaries.",
        },
      ],
    },
    {
      id: "constructing",
      title: "Constructing a variant",
      blocks: [
        {
          kind: "p",
          text: "A construction writes the tag and the active payload; it does not create a value. `e = E.V` writes a unit variant, `e = E.V(x)` a one-scalar payload, and `e = E.V(field=x, ...)` or `e = E.V(x, ...)` a struct payload by field name or position. A construction can appear as a bare assignment, in the declaration `e: E = E.V(...)`, or into a struct field `s.efield = E.V(...)`.",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/enum_struct.lio (excerpt)",
            sourceRef: "tests/programs/enum_struct.lio",
            typecheck: false,
            source: `class Endpoint(Enum):
    Empty
    class Tcp(Struct):
        port: int[16]
        addr: int[32]
    Udp: int[32]


class Conn(Struct):
    proto: Endpoint
    id: int[64]


c: Conn
c.proto = Endpoint.Tcp(port=8080, addr=16777343)
c.id = 7`,
          },
        },
        {
          kind: "note",
          tone: "note",
          title: "stale payloads are never read",
          text: "After `s = Shape.Line(7)` then `s = Shape.Empty`, the `7` is still in the blob's payload bytes but the tag says `Empty`. A `match` reads the tag first and enters only the arm it selects, so the overwritten bytes are unreachable.",
        },
      ],
    },
    {
      id: "matching",
      title: "Reading a variant with match",
      blocks: [
        {
          kind: "p",
          text: "A `match` on an enum reads the tag once and dispatches to the arm whose variant matches. An arm for a payload variant binds that payload (`case Shape.Line(n):`); an arm for a unit variant binds nothing (`case Shape.Empty:`). A `case _:` arm is the wildcard and must be last. The match must be exhaustive: every variant is covered by some arm, or there is a `case _:`.",
        },
        {
          kind: "table",
          caption: "match arms",
          rows: [
            ["Unit arm", "`case E.V:` binds nothing"],
            ["Scalar arm", "`case E.V(n):` binds the one payload scalar"],
            ["Struct arm", "`case E.V(f1=a, f2=b):` binds each named field"],
            ["Wildcard", "`case _:` must be the final arm"],
            ["Exhaustiveness", "all variants covered, or a `case _:` present"],
          ],
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/enum_struct.lio (excerpt)",
            sourceRef: "tests/programs/enum_struct.lio",
            typecheck: false,
            source: `class Endpoint(Enum):
    Empty
    class Tcp(Struct):
        port: int[16]
        addr: int[32]
    Udp: int[32]


class Conn(Struct):
    proto: Endpoint
    id: int[64]


c: Conn
c.proto = Endpoint.Tcp(port=8080, addr=16777343)
c.id = 7
match c.proto:
    case Endpoint.Tcp(port=p, addr=a):
        print(p)
        print(a)
    case Endpoint.Udp(n):
        print(n)
    case Endpoint.Empty:
        print(-1)
print(c.id)`,
          },
        },
        {
          kind: "p",
          text: "`match` lowers to the same tag-compare if/elif chain a hand-written dispatch would be: each arm is a `fieldload` of the tag, an equality test, and a branch whose body reads the payload fields it bound. There is no new instruction kind and no enum value flowing through the IR.",
        },
      ],
    },
    {
      id: "restrictions",
      title: "What enums cannot do",
      blocks: [
        {
          kind: "table",
          caption: "Restrictions",
          rows: [
            ["Whole value", "an enum is never an expression; `print(e)` and `e2 = e` are refused"],
            ["Parameters/returns", "enums are not passed to or returned from functions"],
            ["List elements", "an enum may not be a list element or a list payload"],
            ["Enum payloads", "a variant payload is a scalar or an inline `Struct`; not another enum"],
            ["Guards", "`case E.V if <guard>:` is not supported"],
          ],
        },
        {
          kind: "code",
          example: {
            title: "rejected · an enum definition error",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "LITHON-E0129",
            source: `class E(Enum):
    A
    A
print(0)`,
          },
        },
        {
          kind: "code",
          example: {
            title: "rejected · an unknown variant",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "LITHON-E0130",
            source: `class E(Enum):
    A
e: E
e = E.B
`,
          },
        },
        {
          kind: "code",
          example: {
            title: "rejected · an enum used as a value",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "LITHON-E0131",
            source: `class E(Enum):
    A
    B: int[32]
e: E
match e:
    case E.A:
        print(0)`,
          },
        },
      ],
    },
  ],
}

const typeFunctions: DocPage = {
  slug: "type-functions",
  title: "Type Functions",
  description:
    "A compile-time type constructor: `def Name(params) -> NewType: return { T }`, applied as `Name(args)` anywhere a type is allowed.",
  group: "language",
  tags: [
    "type functions",
    "type function",
    "NewType",
    "type alias",
    "typedef",
    "generics",
    "structural",
    "type constructor",
  ],
  sections: [
    {
      id: "what-a-type-function-is",
      title: "A type function is a compile-time type constructor",
      blocks: [
        {
          kind: "p",
          text: "A type function names a type shape. You declare it with `def Name(params) -> NewType: return { T }`, and then wherever a type is allowed you write `Name(args)`; at compile time the arguments are substituted into `T` and the result is an ordinary type. It runs entirely in the frontend: a definition carries no code, and no runtime value is ever involved.",
        },
        {
          kind: "p",
          text: "`NewType` is a reserved marker in the return position. The body must be exactly one `return { <type> }`. The parameters hold **types and sizes** — they are plain unannotated names standing in for whatever is spliced at the call site, not values.",
        },
      ],
    },
    {
      id: "declaring-and-applying",
      title: "Declaring and applying a type function",
      blocks: [
        {
          kind: "code",
          example: {
            title: "tests/programs/typefunc_basic.lio (excerpt)",
            sourceRef: "tests/programs/typefunc_basic.lio",
            expectedRef: "tests/programs/expected/typefunc_basic.out",
            typecheck: false,
            source: `def Fixed(t, n) -> NewType:
    return {list[t, n]}


def Word() -> NewType:
    return {int[32]}


xs: Fixed(int[64], 4)
xs[0] = 100
xs[1] = 23
xs[2] = -3
print(xs[0] + xs[1] + xs[2])`,
            output: "120",
          },
        },
        {
          kind: "p",
          text: "`Fixed(int[64], 4)` is `list[int[64], 4]` and `Word()` is `int[32]`. A zero-parameter type function must still be applied with parentheses — write `Word()`, not a bare `Word`, which would read as a sized integer and be refused.",
        },
      ],
    },
    {
      id: "structural",
      title: "The result is structural",
      blocks: [
        {
          kind: "p",
          text: "Applying a type function yields a type **identical** to the expansion spelled out — there is no distinct typedef identity. `Alias(int[64])` is the very same type as `int[64]`, so a variable declared one way and re-declared the other is not a re-declaration with a different type.",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/typefunc_basic.lio (excerpt)",
            sourceRef: "tests/programs/typefunc_basic.lio",
            typecheck: false,
            source: `def Alias(t) -> NewType:
    return {t}


a: int[64] = 5
a: Alias(int[64]) = 6
print(a)`,
          },
        },
        {
          kind: "p",
          text: "Because the type erases completely, nothing about a type function survives into the IR. `tools/check_typefunc.py` proves this: it compiles a type-function program and the same program with every type spelled out, and asserts the two IR texts are byte-identical.",
        },
      ],
    },
    {
      id: "where-they-work",
      title: "Where a type function can be applied",
      blocks: [
        {
          kind: "p",
          text: "A call is resolved inside the single annotation parser every type flows through, so a type function works anywhere a type is written: nested in another type function's argument, in a function parameter and return type, as a struct field, and as a container element type.",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/typefunc_uses.lio (excerpt)",
            sourceRef: "tests/programs/typefunc_uses.lio",
            expectedRef: "tests/programs/expected/typefunc_uses.out",
            typecheck: false,
            source: `def Word() -> NewType:
    return {int[32]}


def Pair(t) -> NewType:
    return {list[t, 2]}


ps: Pair(Word())
ps[0] = 11
ps[1] = -22
print(ps[0] + ps[1])`,
            output: "-11",
          },
        },
        {
          kind: "note",
          tone: "note",
          title: "applications nest",
          text: "`Pair(Word())` applies `Word()` first, giving `list[int[32], 2]`. Applications are memoized by call shape, so writing the same `Pair(int[64])` twice expands the body once and returns the same type.",
        },
      ],
    },
    {
      id: "restrictions",
      title: "What a type function cannot do",
      blocks: [
        {
          kind: "table",
          caption: "Restrictions",
          rows: [
            ["Body", "exactly one `return { <type> }`; `{}` is a dict and refused, multi-element bodies and comprehensions too"],
            ["Composes", "scalar, container, pointer and dict types — not a struct or an enum (those are storage)"],
            ["Parameters", "plain unannotated positional names; no `/`, `*`, `**` or defaults"],
            ["Names", "no redeclaration, and no collision with a struct, an enum or a function"],
            ["Recursion", "must bottom out in a concrete type; direct and mutual recursion are refused"],
          ],
        },
        {
          kind: "code",
          example: {
            title: "rejected · a body holding two types",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "LITHON-E0132",
            source: `def T(a) -> NewType:
    return {a, a}
print(0)`,
          },
        },
        {
          kind: "code",
          example: {
            title: "rejected · a recursive type function",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "LITHON-E0133",
            source: `def P(a) -> NewType:
    return {P(a)}
x: P(int[64]) = 1
print(0)`,
          },
        },
      ],
    },
  ],
}

const methods: DocPage = {
  slug: "methods",
  title: "Methods",
  description:
    "A function declared in a struct or enum body, with an implicit self, inlined at each call site.",
  group: "language",
  tags: [
    "methods",
    "method",
    "self",
    "receiver",
    "struct methods",
    "enum methods",
    "inline",
  ],
  sections: [
    {
      id: "what-a-method-is",
      title: "A method is a function in a class body",
      blocks: [
        {
          kind: "p",
          text: "A `def` inside a `Struct` or `Enum` body is a **method**. Its first parameter is the implicit receiver `self`, the rest are annotated scalars, and the return type is optional. A method is called on a struct or enum place: `p.f(1)`, where `p` may be a variable or a struct/enum field.",
        },
        {
          kind: "p",
          text: "A method is **inlined at the call site**. That is forced by the storage model — a field access names a variable, not a value, and a struct or enum cannot be passed to a function (E0124). Inlining is what lets a method read and write the receiver's fields, run loops and branches, and call other methods.",
        },
      ],
    },
    {
      id: "declaring-and-calling",
      title: "Declaring and calling a method",
      blocks: [
        {
          kind: "code",
          example: {
            title: "tests/programs/method_basic.lio (excerpt)",
            sourceRef: "tests/programs/method_basic.lio",
            expectedRef: "tests/programs/expected/method_basic.out",
            typecheck: false,
            source: `class Counter(Struct):
    value: int[64]

    def add(self, n: int[64]) -> int[64]:
        self.value = self.value + n
        return self.value


c: Counter
print(c.add(5))
print(c.add(7))`,
            output: "5\n12",
          },
        },
        {
          kind: "p",
          text: "A method's locals are private to the call: a name in the body cannot capture or clobber a caller's variable of the same spelling, because the inline renames every method local with a per-call prefix. A method that returns nothing is a statement (`p.log()`); one that returns a scalar is an expression you can print or combine.",
        },
      ],
    },
    {
      id: "receivers",
      title: "The receiver, and enum methods",
      blocks: [
        {
          kind: "p",
          text: "The receiver is a place, so it can be a struct field: `o.inner` resolves to the field's bytes and a method on `Inner` reads and writes the caller's `inner`. A method on an enum reads `self` with `match self` — the only way to read an enum — and returns a scalar computed from the active variant.",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/method_enum.lio (excerpt)",
            sourceRef: "tests/programs/method_enum.lio",
            expectedRef: "tests/programs/expected/method_enum.out",
            typecheck: false,
            source: `class Shape(Enum):
    Empty
    Line: int[32]

    def code(self) -> int[64]:
        match self:
            case Shape.Empty:
                return 0
            case Shape.Line(n):
                return n
            case _:
                return -1


s: Shape
s = Shape.Empty
print(s.code())`,
            output: "0",
          },
        },
      ],
    },
    {
      id: "what-methods-cannot-do",
      title: "What a method cannot do",
      blocks: [
        {
          kind: "table",
          caption: "Restrictions",
          rows: [
            ["Receiver", "the first parameter must be the unannotated name `self`"],
            ["Signature", "scalars only (`int[w]`/`float[64]`/`bool`); not a struct, enum, container or pointer, as parameter or return"],
            ["Arguments", "plain positional names; no `/`, `*`, `**` or defaults"],
            ["Names", "no redeclaration or overloading; may not shadow a field or variant"],
            ["Recursion", "inlined at each call site, so direct and mutual recursion are refused"],
          ],
        },
        {
          kind: "code",
          example: {
            title: "rejected · a method whose first parameter is not `self`",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "LITHON-E0134",
            source: `class P(Struct):
    x: int[64]
    def f(a: int[64]) -> int[64]:
        return a
p: P
print(p.f(1))`,
          },
        },
        {
          kind: "code",
          example: {
            title: "rejected · a recursive method",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "LITHON-E0135",
            source: `class P(Struct):
    x: int[64]
    def f(self) -> int[64]:
        return self.f()
p: P
print(p.f())`,
          },
        },
      ],
    },
  ],
}

const syscalls: DocPage = {
  slug: "syscalls",
  title: "Syscalls",
  description:
    "The raw syscall primitive: `syscall(number, a1..a6) -> int[64]`, one IR instruction, one host trampoline, identical in both tiers.",
  group: "language",
  tags: ["syscall", "syscalls", "kernel", "raw", "int[64]", "errno", "Phase III"],
  sections: [
    {
      id: "the-primitive",
      title: "One instruction, unchecked",
      blocks: [
        {
          kind: "p",
          text: "`syscall(number, a1, a2, a3, a4, a5, a6)` is a compiler-recognized name, not a function you import. It takes a syscall number and up to six arguments and returns the kernel's **raw** result as `int[64]`. The whole call lowers to a single `Syscall` IR instruction.",
        },
        {
          kind: "p",
          text: "Nothing is wrapped and nothing is checked. On Linux a failure is simply a negative number: the negated errno, exactly as the kernel reports it. There is no `fallible[...]` to unwrap and no use-before-check rule in this slice — a program that wants to handle failure compares the result itself.",
        },
        {
          kind: "code",
          example: {
            title: "a syscall number that does not exist",
            source: `r: int[64] = syscall(-1)
print(r)`,
            output: "-38",
            note: "-1 is not a syscall number, so the kernel returns -ENOSYS (-38). The number is stable across both tiers and across the platform fork, which is what makes it a checkable example.",
          },
        },
      ],
    },
    {
      id: "integer-arguments",
      title: "Arguments are integers",
      blocks: [
        {
          kind: "p",
          text: "A raw syscall argument must be an `int` in this slice. A pointer is **rejected at compile time**, and the reason is worth stating plainly: the interpreter does not have real memory addresses. It proves pointer semantics on a synthetic address space of its own (see [Pointers](/docs/pointers)), so a pointer handed to a kernel syscall would name nothing in the interpreter while naming a real frame slot natively. The two tiers would silently disagree.",
        },
        {
          kind: "p",
          text: "Refusing the pointer is the honest answer: it keeps the primitive tier-neutral. Pointer-taking syscalls such as `write` and `read` are deferred until the interpreter grows a real address-space story, rather than shipping as a construct that works in one tier and faults in the other.",
        },
        {
          kind: "code",
          example: {
            title: "rejected · a pointer argument",
            lang: "text",
            source: `x: int[64] = 65
_ptrX: ptr[int[64]] = addressof(x)
r: int[64] = syscall(1, _ptrX)
print(r)`,
            output:
              "a raw syscall argument must be an integer in this slice",
          },
        },
      ],
    },
    {
      id: "both-tiers-agree",
      title: "Both tiers execute the same trampoline",
      blocks: [
        {
          kind: "p",
          text: "The platform fork lives in exactly one place, `src/jit/syscall_runtime.h`. It consults an embedder-installed hook first, on every platform. With no hook it runs the real `syscall` instruction on Linux/x86-64; on a platform whose raw ABI is not stable (Windows) it returns `-ENOSYS` rather than guessing.",
        },
        {
          kind: "p",
          text: "Both the interpreter and the JIT call that one helper, so the two engines cannot drift about the calling convention — the argument registers, the clobbered registers, and the raw return are defined once. The JIT treats the op as a register-clobbering boundary, exactly like a call, so a value live across a syscall is spilled or placed in a callee-saved register.",
        },
      ],
    },
  ],
}

export const LANGUAGE_PAGES: DocPage[] = [
  types,
  declarations,
  controlFlow,
  functions,
  operators,
  containers,
  pointers,
  structs,
  enums,
  methods,
  typeFunctions,
  syscalls,
]
