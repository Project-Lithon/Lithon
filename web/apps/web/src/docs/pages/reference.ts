import type { DocPage } from "../model"

const errors: DocPage = {
  slug: "errors",
  title: "Errors & diagnostics",
  description:
    "The LITHON-E code family: static refusals, environmental failures, and dynamic traps: with real diagnostics.",
  group: "reference",
  tags: [
    "errors",
    "diagnostics",
    "E0101",
    "E0301",
    "traps",
    "RCR",
    "codes",
    "reference",
  ],
  sections: [
    {
      id: "code-families",
      title: "The code families",
      blocks: [
        {
          kind: "p",
          text: "Every failure carries a `LITHON-Ennnn` code. The first digit is the tier the failure belongs to: the same numbering the runtime uses to decide refuse-vs-trap:",
        },
        {
          kind: "table",
          caption: "from docs/whitepaper.md",
          rows: [
            [
              "`E01xx`: tier 1 · static refusal",
              "compile-time, the program never runs",
            ],
            [
              "`E02xx`: tier 2 · environmental",
              "the environment failed the program, not the program itself",
            ],
            [
              "`E03xx`: tier 3 · dynamic arithmetic/logic trap",
              "runtime, per-operation",
            ],
            [
              "`E04xx`: tier 3 · dynamic resource trap",
              "runtime, resource exhaustion",
            ],
          ],
        },
        {
          kind: "table",
          caption: "the full code table",
          rows: [
            ["`E0101`, 1 (static refusal)", "Overflow on reassignment"],
            ["`E0102`, 1 (static refusal)", "Literal zero divisor"],
            ["`E0103`, 1 (static refusal)", "Literal shift out of range"],
            [
              "`E0104`, 1 (static refusal)",
              "Literal-provable capacity overflow",
            ],
            ["`E0105`, 1 (static refusal)", "Unsupported type width"],
            [
              "`E0201`, 2 (environmental)",
              "Fallible value read before `.ok` check",
            ],
            ["`E0301`, 3 (dynamic trap)", "Division by zero: shipped"],
            ["`E0302`, 3 (dynamic trap)", "Modulo by zero: shipped"],
            ["`E0303`, 3 (dynamic trap)", "Dynamic integer overflow"],
            ["`E0304`, 3 (dynamic trap)", "Shift amount out of range: shipped"],
            [
              "`E0305`, 3 (dynamic trap)",
              "Dynamic index/capacity out of range: shipped for list, dict TBD",
            ],
            ["`E0306`, 3 (dynamic trap)", "Invalid integer literal in input"],
            [
              "`E0307`, 3 (dynamic trap)",
              "Input value outside the target width",
            ],
            ["`E0308`, 3 (dynamic trap)", "End of input"],
            ["`E0401`, 3 (resource trap)", "Stack overflow"],
          ],
        },
      ],
    },
    {
      id: "static-vs-dynamic",
      title: "Static refusal vs dynamic trap",
      blocks: [
        {
          kind: "p",
          text: "The split is the point: overflow the verifier can prove at compile time is **E0101, a refusal**: the program never runs. Overflow only visible at run time is **E0303, a trap**: checked per operation in both tiers, identically.",
        },
        {
          kind: "code",
          example: {
            lang: "text",
            title: "the same operation, two verdicts",
            source: `x: int[64] = 9223372036854775807
x = x + 50    # LITHON-E0101: compile-time refusal, not a runtime event

b: int[64] = input_value()   # unknown until run
c: int[64] = b + 50          # Tier-3 trap (E0303) iff it overflows`,
          },
        },
        {
          kind: "note",
          tone: "warn",
          title: "E0303 and E0401 are being built",
          text: "Per docs/lithon_error_system.md, dynamic-overflow trapping for Add/Sub/Mul (E0303) closes the `find_e.py` gap and the stack-depth guard (E0401) is designed; unbounded recursion is currently a raw crash in both tiers. E0301/E0302/E0304 and list E0305 are shipped.",
        },
      ],
    },
    {
      id: "rccr-diagnostics",
      title: "RCR diagnostics from the checker",
      blocks: [
        {
          kind: "p",
          text: "The type checker's own diagnostics (`RCR error:`) are a separate family: they fire before any `LITHON-E` code could apply. Every one names the construct, the offending binding, the spec section, and the fix:",
        },
        {
          kind: "code",
          example: {
            title: "annotation missing",
            expect: "error",
            errorMatch: "without ever being declared with a type annotation",
            source: `x = 10
print(x)`,
            output:
              "RCR error: 'x' is assigned without ever being declared with a type annotation (V1_SPEC 0.6.1) -- write 'x: <type> = ...' first",
          },
        },
        {
          kind: "code",
          example: {
            title: "narrowing",
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
          kind: "code",
          example: {
            title: "use before assignment",
            expect: "error",
            errorMatch: "never assigned a value",
            source: `i: int[8]
print(i)`,
            output:
              "RCR error: call statement: 'i' was declared with a type but never assigned a value (V1_SPEC 0.6.10)",
          },
        },
      ],
    },
    {
      id: "traps-agree",
      title: "Both tiers trap identically",
      blocks: [
        {
          kind: "p",
          text: "`run_tier_diff.py` compares stderr byte for byte, so a trap message that differed between Tier-0 and Tier-1 would fail the suite. The trap text is part of the contract, not a debugging courtesy.",
        },
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "terminal",
            source: `$ lithon div_zero.py            # dynamic divisor
LITHON-E0301: division by zero`,
          },
        },
      ],
    },
  ],
}

const builtins: DocPage = {
  slug: "builtins",
  title: "Built-in functions",
  description:
    "print, len, range, addressof, valueof, contains, and the wrap_* family: the whole prelude.",
  group: "reference",
  tags: [
    "builtins",
    "print",
    "len",
    "range",
    "addressof",
    "valueof",
    "contains",
    "wrap",
    "prelude",
  ],
  sections: [
    {
      id: "the-prelude",
      title: "The prelude",
      blocks: [
        {
          kind: "p",
          text: "Lithon's prelude is deliberately tiny. `print()` is a fixed built-in, not a user function (V1_SPEC 0.6.9); everything below is checked by name by the type checker and lowered specially by the frontend:",
        },
        {
          kind: "table",
          caption: "built-ins",
          rows: [
            [
              "`print(x)`",
              "write one scalar value and a newline: the only output primitive",
            ],
            [
              "`len(xs)`",
              "capacity of a `list`/`tuple`/`dict`: part of the type, cannot change at run time",
            ],
            [
              "`range(n)` / `range(a, b)`",
              "loop values, checked against the loop variable's width at compile time when known (0.6.12)",
            ],
            ["`addressof(x)`", "`ptr[T]` to scalar `x`"],
            ["`valueof(_p)`", "read the scalar at `_p`"],
            [
              "`contains(d, k)`",
              "`bool`: key `k` present in dict `d` (constant and runtime keys)",
            ],
            [
              "`wrap_add(a, b)`",
              "wrapping integer add: same wrapped value in both tiers",
            ],
            ["`wrap_sub(a, b)`", "wrapping integer sub"],
            ["`wrap_mul(a, b)`", "wrapping integer mul"],
          ],
        },
        {
          kind: "note",
          tone: "note",
          title: "wrap_* is the explicit opt-in",
          text: "Provable static overflow of plain `+`/`-`/`*` is a compile error (E0101/E0303-family) that points you at `wrap_*`. The wrapping result is pinned to be identical across tiers by tools/check_error_codes.py.",
        },
      ],
    },
    {
      id: "wrap-in-action",
      title: "wrap_* in action",
      blocks: [
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/int64_wrap.py",
            sourceRef: "tests/typed_regression/int64_wrap.py",
            expectedRef: "tests/typed_regression/expected/int64_wrap.out",
            source: `x: int[64] = 9223372036854775807
y: int[64] = 1
z: int[64] = wrap_add(x, y)
print(z)`,
            output: "-9223372036854775808",
          },
        },
      ],
    },
    {
      id: "not-builtins",
      title: "What is not a built-in",
      blocks: [
        {
          kind: "table",
          caption: "rejected by name",
          rows: [
            [
              "`len()` on a scalar",
              "type error: `len` is a container operation",
            ],
            [
              "`int()` / `float()` constructors",
              "do not exist: conversion is a typing rule, not a call",
            ],
            ["`cast()`", "does not exist anywhere in the language"],
            [
              "user-defined `print`",
              "rejected: `print` is reserved as the fixed built-in",
            ],
            ["`abs()` / `min()` / `max()`", "not yet: write the comparison"],
          ],
        },
      ],
    },
  ],
}

const semantics: DocPage = {
  slug: "semantics",
  title: "Semantics",
  description:
    "Where Lithon deliberately diverges from CPython: modulo, shifts, float traps, truthiness.",
  group: "reference",
  tags: [
    "semantics",
    "modulo",
    "shift",
    "float",
    "ZeroDivision",
    "NaN",
    "truthiness",
    "CPython",
  ],
  sections: [
    {
      id: "modulo",
      title: "% is C's %, not Python's",
      blocks: [
        {
          kind: "p",
          text: "`%` truncates toward zero and takes the sign of the dividend: what C, Rust and Java do. CPython uses floor-division semantics; the two disagree on negatives:",
        },
        {
          kind: "code",
          example: {
            title: "modulo · truncating, like C",
            typecheck: false,
            source: `print(-7 % 3)
print(7 % -3)`,
            output: "-1\n1",
            note: "CPython would print 2 and -2. Lithon chose the machine's behaviour.",
          },
        },
        {
          kind: "p",
          text: "A zero divisor traps on both engines (E0301/E0302), and a constant power-of-two divisor is strength-reduced to a mask and a sign fixup.",
        },
      ],
    },
    {
      id: "shifts",
      title: "Shifts are 64-bit, count checked",
      blocks: [
        {
          kind: "code",
          example: {
            title: "shifts",
            source: `print(5 << 63)`,
            output: "-9223372036854775808",
          },
        },
        {
          kind: "p",
          text: "Left shifts wrap, because there is no wider type to widen into. A count outside `0..63` is refused: at compile time for a literal (RCR, V1_SPEC 0.6.11), at run time otherwise (E0304). `2.5 & 1` is a type error: there is no float bit pattern in Lithon to reinterpret.",
        },
      ],
    },
    {
      id: "float-traps",
      title: "Float traps like Python, not like IEEE",
      blocks: [
        {
          kind: "p",
          text: "Floats are implemented end to end in SSE2, and one formatter serves both tiers, so their output agrees byte for byte. Three trap rules matter:",
        },
        {
          kind: "table",
          rows: [
            [
              "division by zero",
              "traps like Python's `ZeroDivisionError`, **not** IEEE `inf`/`nan`",
            ],
            ["NaN divisor", "must **not** trap, Python propagates NaN"],
            ["`-0.0` divisor", "must trap: it compares equal to `0.0`"],
          ],
        },
        {
          kind: "note",
          tone: "note",
          title: "The ZF AND !PF problem",
          text: '`comisd` sets ZF for both "equal" and "NaN", distinguishable only with PF. The backend handles this explicitly; the [language page](/roadmap/language) has the full list.',
        },
      ],
    },
    {
      id: "no-truthiness",
      title: "No truthiness, no chaining",
      blocks: [
        {
          kind: "table",
          rows: [
            ["`if x:` with `x: int`", "type error: a condition must be `bool`"],
            ["`and` / `or` on non-bool", "type error"],
            ["chained comparison `a < b < c`", "not supported"],
            ["`for`/`else`", "rejected, never silently dropped"],
          ],
        },
      ],
    },
  ],
}

const verification: DocPage = {
  slug: "verification",
  title: "Verification",
  description:
    "The numbers behind the claims: 32/32 CTest, 9,239 encoder cases with 0 wrong, both tiers byte-identical.",
  group: "reference",
  tags: [
    "verification",
    "testing",
    "differential",
    "encoder",
    "fuzz",
    "benchmark",
    "numbers",
  ],
  sections: [
    {
      id: "the-numbers",
      title: "Current verification highlights",
      blocks: [
        {
          kind: "table",
          caption: "from README.md: run it yourself, don't trust it",
          rows: [
            ["CTest", "32 / 32 passed"],
            ["x86-64 encoder comparisons", "9,239 cases"],
            ["Incorrect encoder cases", "0"],
            ["Typed differential fuzzing", "300 / 300 passed"],
            ["Bitwise / shift fuzzing", "200 / 200 passed"],
            ["Conditional-expression / SSA fuzzing", "200 / 200 per suite"],
            ["Direct float Phi sweep", "38 matched / 0 mismatched"],
            ["ABI modules audited", "23"],
            ["ABI stack/callee-saved failures", "0"],
            ["Typed regression suite", "15 / 15 passed"],
            ["General regression suite", "23 / 23 passed"],
            ["SIMD vectorizer loop cases", "6 / 6 passed"],
          ],
        },
        {
          kind: "note",
          tone: "good",
          title: "The gate concludes",
          text: "`bash tools/verify_all.sh` runs the whole stack and ends with `ALL CHECKS PASSED` or it does not pass.",
        },
      ],
    },
    {
      id: "encoder-vs-as",
      title: "Encoder vs GNU as",
      blocks: [
        {
          kind: "p",
          text: "The hand-written x86-64 encoder is compared against GNU `as`, instruction by instruction. A byte difference is not automatically an error: x86-64 often has multiple valid encodings, so the harness decodes and classifies:",
        },
        {
          kind: "code",
          example: {
            lang: "text",
            title: "tools/check_encoder_vs_as.py",
            source: `9239 instruction cases
7949 byte-identical
1290 different but valid encodings
0 incorrect encodings

WRONG = 0`,
          },
        },
        {
          kind: "p",
          text: "The only number that matters is `WRONG = 0`.",
        },
      ],
    },
    {
      id: "the-gate",
      title: "Work outward, stop where you are satisfied",
      blocks: [
        {
          kind: "p",
          text: "Each layer is roughly an order of magnitude slower than the one above it, which is what makes running all of them reasonable rather than aspirational:",
        },
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "the full gate",
            source: `$ ctest --test-dir build --output-on-failure      # 32/32
$ python3 tools/run_regression.py                # general suite
$ python3 tools/run_typed_regression.py          # typed suite
$ python3 tools/run_tier_diff.py                 # both tiers, byte-identical
$ bash tools/verify_all.sh                       # ALL CHECKS PASSED`,
          },
        },
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "layer 3 · per-shape differential fuzzing",
            source: `$ python3 tools/fuzz_diff.py --count 300             # general
$ python3 tools/fuzz_diff.py --count 300 --floats    # int/float mixes
$ python3 tools/fuzz_diff.py --count 300 --bitwise   # & | ^ << >>, RCX hazard
$ python3 tools/fuzz_diff.py --mod-negatives --count 300
$ python3 tools/fuzz_diff.py --phi --count 300        # merges, with and without --ssa`,
          },
        },
        {
          kind: "note",
          tone: "warn",
          title: "What a green run does not prove",
          text: "Opcode coverage is not operand coverage. 25 of 26 opcodes are emitted and exercised, but that does not prove every operand shape is right: `Mod` is the standing example: the general fuzzer only emits finite constants, so it cannot generate `1.0 % inf`, which was returning NaN natively while the interpreter was correct.",
        },
      ],
    },
    {
      id: "benchmark",
      title: "The headline benchmark",
      blocks: [
        {
          kind: "table",
          caption: "fib(30), from benchmarks/results/latest.json",
          rows: [
            ["CPython 3.12", "790.2430 ms"],
            ["Lithon native", "4.1810 ms"],
            ["Speedup", "**189.0×**"],
            ["Result", "832040: identical on both"],
          ],
        },
        {
          kind: "p",
          text: "Benchmarks are the weakest form of evidence: they prove speed, not correctness. The suites above are what prove correctness; the benchmark is what the correctness buys you.",
        },
      ],
    },
  ],
}

const limits: DocPage = {
  slug: "limits",
  title: "Honest limits",
  description:
    "The gaps as they stand: two-argument functions, Phi not emitted, Win64 unproven, x86-64 only.",
  group: "reference",
  tags: ["limits", "gaps", "roadmap", "not supported", "honest", "TODO"],
  sections: [
    {
      id: "the-gaps",
      title: "The gaps",
      blocks: [
        {
          kind: "p",
          text: "A green test run covers the supported subset and should not be mistaken for a finished language. These are the gaps as they stand today:",
        },
        {
          kind: "table",
          rows: [
            ["IR opcodes emitted", "25 of 26"],
            [
              "`Phi`",
              "lowered, not emitted: blocks if-as-expression merges without a memory round-trip",
            ],
            ["arguments per function / call", "capped at 2"],
            ["float merges", "never promoted · needs an XMM pool"],
            ["Win64 ABI", "implemented, no test evidence yet"],
            ["AOT binary emit", "none · everything runs in-process"],
            ["architectures", "x86-64 only · ARM64 is Phase III"],
            ["AVX-512", "not emitted · feature gating only"],
            ["`E0303`/`E0401` runtime traps", "designed, being built"],
            ["`//` floor division", "not yet implemented"],
            ["`**` power", "limited: see the regression corpus"],
            ["unsigned ints", "no `uint`: signed at every width"],
            [
              "diamond unrolling",
              "opt-in via `--unroll-diamonds`, 1.10× slower today",
            ],
          ],
        },
        {
          kind: "note",
          tone: "note",
          title: "The two blockers worth knowing",
          text: "`Phi` is the single unemitted opcode. The two-argument cap is the other: most of the remaining test programs are waiting on it. Both are engineering, not research.",
        },
      ],
    },
    {
      id: "not-a-cpython-replacement",
      title: "This is not a CPython replacement",
      blocks: [
        {
          kind: "p",
          text: 'No stdlib, no import system beyond the built-ins, no classes, no closures, no exceptions. Lithon is an experiment in one narrow question: what happens to a Python-shaped language when the types are mandatory and the machine is visible, and the honest answer to "can it run my app?" is "almost certainly not, yet".',
        },
        {
          kind: "linkcards",
          items: [
            {
              title: "Roadmap",
              href: "/roadmap",
              text: "What is next, which phase each item belongs to.",
            },
            {
              title: "Verification",
              href: "/docs/verification",
              text: "How the claims above get checked rather than taken on trust.",
            },
          ],
        },
      ],
    },
  ],
}

export const REFERENCE_PAGES: DocPage[] = [
  errors,
  builtins,
  semantics,
  verification,
  limits,
]
