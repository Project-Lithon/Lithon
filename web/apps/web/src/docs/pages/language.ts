import type { DocPage } from "../model"

const types: DocPage = {
  slug: "types",
  title: "Types & widths",
  description:
    "Fixed-width integers, floats, bool — and the three rules the verifier enforces on every conversion.",
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
          text: "Integers carry their width in the type. There is no bare `int` — it is rejected — and no arbitrary-precision integer behind your back:",
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
              "typed pointer to a scalar `T` — see [Pointers](/docs/pointers)",
            ],
          ],
        },
        {
          kind: "note",
          tone: "note",
          title: "Signed only, 64-bit floats only",
          text: "There is no unsigned `uint` type — `uint[8]` is rejected as an unknown type, so masking with `&` is how you get unsigned behaviour, explicitly. `float[32]` is rejected too (`LITHON-E0105: unsupported float width 32`): every float is `float[64]`.",
        },
        {
          kind: "note",
          tone: "note",
          title: "Why fixed widths are load-bearing",
          text: "A fixed width is a fact the backend can plan around: the register, the memory slot, the overflow instruction. An unbounded `int` would mean boxing, big-integer fallbacks, and guards — exactly what the native tier is built to not have.",
        },
      ],
    },
    {
      id: "widening",
      title: "Widening is automatic, narrowing is never",
      blocks: [
        {
          kind: "p",
          text: "A value may be widened to a provably larger width of the same signedness class, automatically. Narrowing — the reverse — is rejected even when you are certain the value fits:",
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
          text: "No cast syntax exists anywhere in the language. If you need a bit pattern reinterpreted, that is what pointers are for — explicitly, with the `_` prefix.",
        },
      ],
    },
    {
      id: "int-float",
      title: "int → float is automatic; float → int is not",
      blocks: [
        {
          kind: "p",
          text: "`int[64]` into `float[64]` is a widening conversion and happens silently. The reverse is rejected — it would truncate, and truncation is not a conversion Lithon will perform for you.",
        },
        {
          kind: "note",
          tone: "warn",
          title: "The literal exception (V1_SPEC 0.6.11)",
          text: "An int literal is not a float, so `j: float[64] = 0` is a type error — write `0.0`. The int→float conversion does not apply to a literal.",
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
            title: "tests/typed_regression/mixed_numeric.py",
            sourceRef: "tests/typed_regression/mixed_numeric.py",
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
          text: "`int` without a width is rejected — the width is part of the type, not an implementation detail the compiler may pick for you. The same goes for `int[128]`: only 8/16/32/64 exist, matching the machine.",
        },
        {
          kind: "note",
          tone: "note",
          title: "Provable overflow is a compile-time error",
          text: "Integer literal overflow and provable-range overflow for binary operations are compile-time errors (V1_SPEC 0.6.5) — the checker points you at `wrap_add()`/`wrap_sub()`/`wrap_mul()` when wrap-around is what you want. Overflow the verifier cannot prove at compile time executes as the machine's two's-complement wrap; nothing traps at run time.",
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
          text: "A name is declared once with an explicit type. Re-assignment is checked against that declared type — you cannot quietly reuse the name for a different width or a different type:",
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
          text: "Every use of a variable must be dominated by an assignment whose value the verifier can trace. You may declare first and assign later — but you may not read before the first assignment.",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/uninit_decl.py",
            sourceRef: "tests/typed_regression/uninit_decl.py",
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
          title: "Declaration is not initialization — except for containers",
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
    "if / elif / else, while, for over range — and the one construct that does not exist.",
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
  ],
  sections: [
    {
      id: "if",
      title: "if / elif / else",
      blocks: [
        {
          kind: "p",
          text: "Exactly the Python you know. Conditions are boolean expressions; there is no truthiness conversion — a condition must be a `bool`.",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/if.py",
            sourceRef: "tests/typed_regression/if.py",
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
    call print, %4
    jump block3
block2:
    %5 = const_i64 3
    call print, %5
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
            title: "tests/typed_regression/while.py",
            sourceRef: "tests/typed_regression/while.py",
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
          text: "`tools/typecheck.py` still answers `statement While not supported by the type-checker yet` — including for the repo's own `while.py`. The engine lowers and executes `while` correctly (Tier-0 and Tier-1 both); it is the standalone Python checker that lags. Until it catches up, `while` programs are verified through the regression suites rather than the checker.",
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
            title: "tests/typed_regression/for_range.py",
            sourceRef: "tests/typed_regression/for_range.py",
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
    %2 = const_i64 10
    %3 = const_i64 0
    store i, %3 : int[64]
    jump block1
block1:
    %4 = load i
    %5 = lt %4, %2
    branch %5, block2, block3
block2:
    %6 = load total
    %7 = load i
    %8 = add %6, %7
    store total, %8 : int[64]
    %9 = load i
    %10 = const_i64 1
    %11 = add %9, %10
    store i, %11
    jump block1
block3:
    %12 = load total
    call print, %12
    return`,
              note: "The `for` lowers to an init / test / body / increment block structure — a pre-test loop with an explicit back edge, exactly what the encoder emits a `jcc` for.",
            },
          },
        },
      ],
    },
    {
      id: "if-expression",
      title: "if as an expression",
      blocks: [
        {
          kind: "p",
          text: "An `if` used where a value is expected is an **if-expression** with `else` mandatory. Both arms are fully evaluated expressions — statements inside the arms are rejected:",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/if_expr.py (excerpt)",
            sourceRef: "tests/typed_regression/if_expr.py",
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
          text: '`for`/`else` — where the `else` runs when the loop completes without `break` — is rejected. The `else` binds to `if`, not to `for`; if you need the "loop finished" signal, test the loop variable after the loop.',
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
          text: "It is rejected loudly rather than dropped, because the alternative — binding the `else` to the `if` — would silently change what the program means.",
        },
      ],
    },
  ],
}

const functions: DocPage = {
  slug: "functions",
  title: "Functions",
  description: "Mandatory contracts, the two-argument limit, and recursion.",
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
          text: "Every parameter and the return type are mandatory and checked (V1_SPEC 0.6.8). A call site is checked against the declared signature, and a declared return type must be provably wide enough for what is returned — the compiler never auto-widens your return.",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/function.py",
            sourceRef: "tests/typed_regression/function.py",
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
    return

function __main__():
block0:
    %0 = const_i64 3
    %1 = const_i64 4
    %2 = call add, %0, %1
    store x, %2 : int[64]
    %3 = load x
    call print, %3
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
              "RCR error: function 'add': return type is mandatory — write '-> int[64]' (V1_SPEC 0.6.8)",
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
          text: "Recursion works like it does in Python — each call gets its own frame. The verifier checks each call against the declared signature, including recursive calls to the function being defined:",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/recursion.py",
            sourceRef: "tests/typed_regression/recursion.py",
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
    jump block2
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
    return

function __main__():
block0:
    %0 = const_i64 10
    %1 = call fib, %0
    store x, %1 : int[64]
    %2 = load x
    call print, %2
    return`,
              note: "Real frontend output. The base case and the recursive case are separate blocks; `call fib` appears twice, once per recursive branch.",
            },
          },
        },
      ],
    },
    {
      id: "argument-limit",
      title: "The two-argument limit",
      blocks: [
        {
          kind: "p",
          text: "Functions are limited to two arguments today. This is a temporary restriction of the current encoder's calling convention support, not a design position — it will be lifted as the ABI handling matures.",
        },
        {
          kind: "note",
          tone: "note",
          title: "Workaround",
          text: "Bundle arguments into a container (`list`/`tuple`) or split the function. Both patterns appear in the regression corpus.",
        },
      ],
    },
  ],
}

const operators: DocPage = {
  slug: "operators",
  title: "Operators",
  description:
    "Arithmetic, comparison, boolean, bitwise and shift — including C-style `%` and two's-complement wrap.",
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
              "float division producing `float[64]` — **not** C-style truncation",
            ],
            ["`%`", "C-style remainder: sign follows the **left** operand"],
            ["`**`", "power — see the limitation note below"],
            ["`//`", "not yet implemented — rejected at parse time"],
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
          text: "`==` `<` `>` and the other comparisons produce `bool`. `and` `or` `not` operate on `bool`. Note what is *not* here yet: `!=` (`NotEq`) is the one comparison the frontend has not lowered — write `not (a == b)` in the meantime.",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/comparison.py",
            sourceRef: "tests/typed_regression/comparison.py",
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
          kind: "note",
          tone: "note",
          title: "int and float compare across widths",
          text: "The fourth line compares a `float[64]` against an `int[64]` and returns `True` — the int is widened to float for the comparison, the same promotion rule as arithmetic.",
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
          text: "A literal shift count must be `0..63` — the machine word is 64 bits and x86 masks the count to 6 bits, so a count of 64 would silently execute as 0. A non-literal count is checked at runtime instead. This is a deliberate divergence from CPython, where ints are unbounded and `1 << 64` is a valid 65-bit result:",
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
          text: 'Plain `+`/`-`/`*` on fixed-width ints wraps in two\'s complement at run time, but overflow the verifier can prove statically is rejected at compile time (E0303) and points you at the `wrap_*` family. `wrap_add`/`wrap_sub`/`wrap_mul` are the explicit way to say "wrap-around is what I want" — the same wrapped value in both tiers:',
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/int64_wrap.py",
            sourceRef: "tests/typed_regression/int64_wrap.py",
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
    "Fixed-capacity lists, tuples, and constant-keyed dicts — declared, reserved, sized at compile time.",
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
      title: "list — fixed capacity",
      blocks: [
        {
          kind: "p",
          text: "A `list[T, N]` has a compile-time capacity `N` that is part of the type. The element type is a fixed-width scalar. The bare declaration `xs: list[int[64], 6]` **is** real work: it reserves the run and zeroes it — drop it and the first store writes to a name that was never allocated.",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/list_basic.py (excerpt)",
            sourceRef: "tests/programs/list_basic.py",
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
              "checked against `N` — a runtime index is checked, not a literal the compiler happened to see",
            ],
            [
              "Element address",
              "element `k` lands at `base + k * sizeof(T)` — packed, no pointers per element",
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
          text: "A tuple is `tuple[T, N]` — one element type and a literal length. Anything else is rejected: `tuple[int[64], float[64], bool]` fails with *needs an element type and a literal capacity*.",
        },
        {
          kind: "note",
          tone: "note",
          title: "Containers are ahead of the standalone checker",
          text: "`tools/typecheck.py` answers `unknown type 'list'` — the container types are implemented in the engine (and covered by the regression suites) but the standalone Python checker has not been taught them yet.",
        },
      ],
    },
    {
      id: "dict",
      title: "dict — constant-keyed",
      blocks: [
        {
          kind: "p",
          text: "A `dict[K, V, B]` is a fixed-capacity open-addressing table with `B` buckets, built from a constant-keyed literal. The valueless declaration reserves and zeroes the table, so the literal is a fill of a table that already exists — not an allocation trick. Literal keys are resolved while compiling; a runtime key takes the hash-and-probe path.",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/dict_basic.py (excerpt)",
            sourceRef: "tests/programs/dict_basic.py",
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
          text: "`dict_basic.py` stays one short of it and leans on the probe instead. `key_pairs.py` is the test that pins a collision down to fixed keys rather than leaving it to chance.",
        },
      ],
    },
  ],
}

const pointers: DocPage = {
  slug: "pointers",
  title: "Pointers",
  description:
    "Typed pointers with a mandatory `_` prefix — explicit unsafe memory, no hidden aliasing.",
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
          text: "Every pointer variable carries a **mandatory leading `_`**. This is not a naming convention the compiler happens to enforce — it is a source-level marker that raw memory is in play, greppable and impossible to miss in review.",
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
            title: "tests/programs/ptr_basic.py (excerpt)",
            sourceRef: "tests/programs/ptr_basic.py",
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
              "no `*_p` syntax — `valueof` / assignment through the pointer",
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
          text: "The `_` prefix marks the boundary. Inside it, the compiler's usual guarantees about definite assignment and type agreement do not extend through raw memory writes — that is the deal you take when you reach for `ptr`.",
        },
      ],
    },
    {
      id: "float-pointers",
      title: "Pointers to floats and bools",
      blocks: [
        {
          kind: "p",
          text: "`ptr` works for any scalar pointee — `float[32]`, `float[64]`, `bool`. Reading through a `ptr[float[64]]` after the pointee changed gives you the new value, because the pointer names a location, not a snapshot:",
        },
        {
          kind: "code",
          example: {
            title: "reading through a pointer (ptr_basic.py excerpt)",
            sourceRef: "tests/programs/ptr_basic.py",
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

export const LANGUAGE_PAGES: DocPage[] = [
  types,
  declarations,
  controlFlow,
  functions,
  operators,
  containers,
  pointers,
]
