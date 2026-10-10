import type { DocPage } from "../model"

const architecture: DocPage = {
  slug: "architecture",
  title: "Engine architecture",
  description:
    "Four layers from .py source to executable memory: frontend, IR, verifier, encoder.",
  group: "engine",
  tags: [
    "architecture",
    "engine",
    "backend",
    "encoder",
    "frontend",
    "layers",
    "mmap",
  ],
  sections: [
    {
      id: "four-layers",
      title: "The four layers",
      blocks: [
        {
          kind: "p",
          text: "Lithon's native tier is a stack of small, independently checkable layers. Python appears at exactly one step; everything downstream of IR is C++ that never touches CPython.",
        },
        {
          kind: "cards",
          items: [
            {
              title: "1 · Frontend",
              text: "`src/frontend/frontend.py` — parses `.py`, runs the extended PEP-526 checks, emits text IR. Pure Python, no engine needed.",
            },
            {
              title: "2 · IR text format",
              text: "Typed, SSA-shaped text. Parsed inside the engine, so the native tier has no Python dependency of any kind.",
            },
            {
              title: "3 · Verifier",
              text: "SSA construction, dominance, liveness, register allocation. Anything unprovable is refused before a byte is emitted.",
            },
            {
              title: "4 · Encoder",
              text: "Hand-rolled x86-64 bytes written straight into `PROT_READ | PROT_EXEC` memory (`mmap` on Linux, `VirtualAlloc` on Windows) and called through a function pointer.",
            },
          ],
        },
        {
          kind: "note",
          tone: "note",
          title: "No LLVM, no Cranelift, no runtime package",
          text: "The entire backend is C++20 with zero third-party dependencies. GNU `as` appears in exactly one place — the encoder verification harness — and never in the build.",
        },
      ],
    },
    {
      id: "walk-through",
      title: "A walk-through: arithmetic",
      blocks: [
        {
          kind: "p",
          text: "The regression corpus's smallest program, all the way down. Source on the left of your memory, machine code at the bottom:",
        },
        {
          kind: "code",
          example: {
            title: "tests/programs/arithmetic.py",
            sourceRef: "tests/programs/arithmetic.py",
            expectedRef: "tests/programs/expected/arithmetic.out",
            source: `x: int[64] = 10
y: int[64] = 20
z: int[64] = x + y
print(z)
print(x - y)
print(x * y)`,
            output: "30\n-10\n200",
            machine: {
              ir: `function __main__():
block0:
    %0 = const_i64 10
    store x, %0 : int[64]
    %1 = const_i64 20
    store y, %1 : int[64]
    %2 = load x
    %3 = load y
    %4 = add %2, %3
    store z, %4 : int[64]
    %5 = load z
    call print, %5
    %6 = load x
    %7 = load y
    %8 = sub %6, %7
    call print, %8
    %9 = load x
    %10 = load y
    %11 = mul %9, %10
    call print, %11
    return`,
              asm: `main:
    push rbp
    mov  rbp, rsp
    sub  rsp, 0x20
    mov  QWORD PTR [rbp-0x8], 10        ; x = 10
    mov  QWORD PTR [rbp-0x10], 20       ; y = 20
    mov  rax, QWORD PTR [rbp-0x8]
    add  rax, QWORD PTR [rbp-0x10]
    mov  QWORD PTR [rbp-0x18], rax      ; z = x + y
    mov  rax, QWORD PTR [rbp-0x8]
    sub  rax, QWORD PTR [rbp-0x10]
    mov  rdi, rax
    call print
    mov  rax, QWORD PTR [rbp-0x8]
    imul rax, QWORD PTR [rbp-0x10]
    mov  rdi, rax
    call print
    mov  rdi, QWORD PTR [rbp-0x18]
    call print
    xor  eax, eax
    leave
    ret`,
              note: "IR is real frontend output. The listing is hand-assembled for reading — the exact byte schedules live in the encoder — but every shape here (frame setup, `QWORD PTR [rbp-…]` slots, callee-saved discipline) is the one tools/check_stack_alignment.py parses out of real compiled modules.",
            },
          },
        },
        {
          kind: "p",
          text: "The IR stores and reloads `x`/`y` because the frontend emits a memory-shaped IR on purpose — that is the input the SSA pass (`Mem2Reg`) consumes, promoting provable slots into registers. What survives to the encoder after optimization is register-shaped, not stack-shaped.",
        },
      ],
    },
    {
      id: "calling-conventions",
      title: "Calling conventions",
      blocks: [
        {
          kind: "table",
          caption: "ABI support",
          rows: [
            ["SysV (Linux/macOS)", "implemented and audited on every host"],
            [
              "Win64",
              "implemented behind `#if defined(_WIN32)` — no test evidence yet",
            ],
            [
              "Stack alignment",
              "16-byte at call sites, audited by `check_stack_alignment.py`",
            ],
            [
              "Callee-saved",
              "RBX, RBP, R12–R15 preserved; caller-saved reused freely",
            ],
          ],
        },
        {
          kind: "note",
          tone: "warn",
          title: "Win64 is implemented but unproven",
          text: "The path exists and compiles, but the 23-module ABI audit runs on SysV hosts. Treat Win64 as experimental until it has its own evidence.",
        },
      ],
    },
  ],
}

const dualTier: DocPage = {
  slug: "dual-tier",
  title: "Dual-tier execution",
  description:
    "Tier-1 emits native x86-64 for what is proven; Tier-0 interprets the rest; --strict refuses instead of falling back.",
  group: "engine",
  tags: [
    "tiers",
    "tier0",
    "tier1",
    "interpreter",
    "jit",
    "fallback",
    "strict",
    "native",
  ],
  sections: [
    {
      id: "tier-1",
      title: "Tier-1 — the native lane",
      blocks: [
        {
          kind: "p",
          text: "Every IR block whose types are provably static is compiled by the backend into x86-64 and written into executable memory. There are no runtime type checks, no guards, and no boxing on this path — the verifier already did that work, once, at compile time.",
        },
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "forcing the native lane",
            source: `$ lithon tests/programs/float.py --strict
[tier1] native
0.3333333333333333`,
            note: "`[tier1] native` goes to stderr; program output goes to stdout. Under `--strict`, exit 0 means the native tier really ran.",
          },
        },
      ],
    },
    {
      id: "tier-0",
      title: "Tier-0 — the honest fallback",
      blocks: [
        {
          kind: "p",
          text: "A C++ interpreter over the same IR. It exists so that a program the native tier cannot yet prove still runs — same source, same IR, same output. It is a real implementation, not a debug printer: `run_tier_diff.py` requires both tiers to agree **byte for byte** on stdout and stderr.",
        },
        {
          kind: "p",
          text: "In automatic mode, `tier_runner` tries Tier-1 first and falls back to Tier-0 with a message on stderr. This keeps the demo honest: you always know which lane ran.",
        },
      ],
    },
    {
      id: "strict",
      title: "--strict turns fallback into refusal",
      blocks: [
        {
          kind: "p",
          text: 'With `--strict`, a Tier-0 fallback is a **failure**, not a quiet slowdown. This is the switch the entire test suite leans on — without it, "the program printed the right number" could mean the interpreter carried the whole run.',
        },
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "terminal",
            source: `$ lithon program.py --strict        # native only, refuse to fall back
$ ./build/tier_runner prog.ir --strict   # same thing, two-step form`,
          },
        },
        {
          kind: "note",
          tone: "good",
          title: "The discipline in one line",
          text: "Green + `--strict` = the native backend compiled and executed this program. There is no other way to exit 0.",
        },
      ],
    },
    {
      id: "tier-diff",
      title: "run_tier_diff.py — the cross-check",
      blocks: [
        {
          kind: "p",
          text: "The highest-value script in the repo. It runs every regression program through **both** tiers and requires identical stdout, then reports which tier actually ran:",
        },
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "terminal",
            source: `$ python3 tools/run_tier_diff.py
… every program: both tiers agree …`,
          },
        },
        {
          kind: "p",
          text: 'A green run cannot hide "everything silently fell back to the interpreter", because the report says which tier ran each program.',
        },
      ],
    },
  ],
}

const typechecking: DocPage = {
  slug: "typechecking",
  title: "The type checker",
  description:
    "Static analysis over Python's AST — the unconditional gate every module passes before anything runs.",
  group: "engine",
  tags: [
    "typecheck",
    "checker",
    "verifier",
    "0.6",
    "rules",
    "static analysis",
    "AST",
  ],
  sections: [
    {
      id: "unconditional",
      title: "Unconditional, on every module",
      blocks: [
        {
          kind: "p",
          text: '`tools/typecheck.py` is static analysis over Python\'s `ast` — no execution, no imports evaluated. It runs whether the module annotates anything or not: an unannotated program is not "unchecked", it is invalid.',
        },
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "terminal",
            source: `$ python3 tools/typecheck.py program.py
$ echo $?
0`,
          },
        },
        {
          kind: "note",
          tone: "note",
          title: "Two checkers, one frontend",
          text: "The frontend (`src/frontend/frontend.py`) lowers to IR; the checker (`tools/typecheck.py`) is the gate. CI runs the checker **unconditionally** on every program — `check_typecheck_unconditional.py` enforces that no suite is allowed to skip it.",
        },
      ],
    },
    {
      id: "the-rules",
      title: "What it enforces",
      blocks: [
        {
          kind: "table",
          caption: "The 0.6 series",
          rows: [
            [
              "0.6.1",
              "every first assignment, parameter, and return carries an explicit annotation",
            ],
            [
              "0.6.4",
              "re-assignment is checked against the already-declared type",
            ],
            [
              "0.6.5",
              "integer literal overflow, and provable-range overflow for binary ops, are compile-time errors",
            ],
            [
              "0.6.8",
              "function contracts: parameters and return type mandatory and checked; call sites checked against the signature; declared return must be provably wide enough — never auto-widened",
            ],
            ["0.6.9", "`print()` is a fixed built-in, not a user function"],
            [
              "0.6.10",
              "definite assignment + type agreement across if/else branches (function scoping, not block scoping)",
            ],
            [
              "0.6.11",
              "conversions: widening/same-width automatic, narrowing never; int→float automatic, float→int never; no cast syntax anywhere; int literal is not a float",
            ],
            [
              "0.6.12",
              "`range()`'s produced values are checked against the loop variable's declared width at compile time when known",
            ],
          ],
        },
        {
          kind: "p",
          text: "Bitwise and shift operators (`<<`, `>>`, `&`, `|`, `^`) are integer-only and **not** part of the arithmetic promotion rule: `2.5 & 1` is a type error, because Lithon has no float bit pattern to reinterpret. Their result takes the left operand's width. A literal shift count must be `0..63` — the machine word is 64 bits and x86 masks the count to 6 bits, so a count of 64 would silently execute as 0.",
        },
      ],
    },
    {
      id: "reading-errors",
      title: "Reading an RCR error",
      blocks: [
        {
          kind: "code",
          example: {
            title: "a real diagnostic, annotated",
            expect: "error",
            errorMatch: "without ever being declared with a type annotation",
            source: `x = 10
print(x)`,
            output:
              "RCR error: 'x' is assigned without ever being declared with a type annotation (V1_SPEC 0.6.1) -- write 'x: <type> = ...' first",
          },
        },
        {
          kind: "p",
          text: "Every diagnostic carries the failing construct, the name or expression at fault, the spec section (`V1_SPEC 0.6.x`) that makes it an error, and — where one exists — the spelling that would have been accepted. The full code table lives in [Errors](/docs/errors).",
        },
      ],
    },
  ],
}

const pipeline: DocPage = {
  slug: "pipeline",
  title: "Optimizer & SSA pipeline",
  description:
    "Mem2Reg, dominance, phi placement, liveness, and register allocation — each pass switchable so effects are measured, not assumed.",
  group: "engine",
  tags: [
    "pipeline",
    "SSA",
    "optimizer",
    "mem2reg",
    "phi",
    "register allocation",
    "liveness",
    "passes",
  ],
  sections: [
    {
      id: "measured-passes",
      title: "Every pass is switchable — and measured",
      blocks: [
        {
          kind: "p",
          text: "Register allocation and liveness are shipped and tested, and so is a set of optimizations that can each be switched off individually, so their effect is measured rather than assumed:",
        },
        {
          kind: "table",
          caption: "Measured against HEAD, one pass at a time",
          rows: [
            ["Strength-reduced multiplies", "`optimize.h` · nested 1.062×"],
            [
              "Accumulator unroll (opt-in)",
              "`optimize.h` · float reduction 1.58× · 95.8 → 60.5 ms",
            ],
            ["Callee-saved borrowing", "`register_alloc.h` · fib 1.063×"],
            [
              "Mem2Reg + SSA copy resolution",
              "`ssa.h` · 29 loads · 20 stores removed",
            ],
            [
              "Diamond unrolling (opt-in)",
              "1.10× slower, kept behind `--unroll-diamonds`",
            ],
          ],
        },
      ],
    },
    {
      id: "ssa-phases",
      title: "The SSA conversion, phase by phase",
      blocks: [
        {
          kind: "p",
          text: "The IR keeps mutable variables in memory — a variable is written by a `Store` and read by a `Load` — so promotion means turning those into values that live in registers. The conversion is done one switchable phase at a time:",
        },
        {
          kind: "table",
          caption: "SSA phases · all shipped",
          rows: [
            [
              "2.1",
              "CFG, Cooper-Harvey-Kennedy dominators, natural loops, preheaders, latches, exits · `loop_info.h`",
            ],
            ["2.2", "Phi placement by iterated dominance frontier · `ssa.h`"],
            [
              "2.3",
              "Mem2Reg — promote, place phis, rewrite the memory traffic away · `ssa.h`",
            ],
            [
              "2.4",
              "SSA copy propagation, dead-value elimination, then `resolve_phis()` · `optimize.h`",
            ],
            [
              "2.5",
              "Phi copies as register moves instead of a memory round-trip · `register_alloc.h`",
            ],
            [
              "2.6",
              "Dedicated loop-exit blocks where the exit edge is genuinely shared · `loop_info.h`",
            ],
            [
              "2.7",
              "CFG liveness and interference-graph allocation · `liveness.h`",
            ],
            [
              "2.8",
              "Register coalescing — a merge adopts its source's dead register · both halves",
            ],
          ],
        },
        {
          kind: "note",
          tone: "note",
          title: "One decision in there is load-bearing",
          text: 'Promotion is gated on a *must*-analysis that iterates **down** from "everything", converging on the greatest fixpoint. Iterating upward from nothing makes ordinary loop accumulators silently un-promotable forever. Anything the analysis cannot prove stays in memory, and `vars_declined` reports it.',
        },
        {
          kind: "p",
          text: "The [Phase I page](/roadmap/phase-1-dual-tier) walks through all eight phases with the reasoning behind each, including why register coalescing honestly deletes five merges across the typed regression corpus and not more.",
        },
      ],
    },
    {
      id: "one-ir",
      title: "One IR, both tiers",
      blocks: [
        {
          kind: "p",
          text: "The SSA work happens on the same IR text the interpreter consumes. Optimizations that would change observable behaviour are refused rather than approximated — that is what lets `run_tier_diff.py` compare the two tiers byte for byte even when Tier-1 has run the full optimizer and Tier-0 has run none of it.",
        },
      ],
    },
  ],
}

const simd: DocPage = {
  slug: "simd",
  title: "SIMD auto-vectorization",
  description:
    "8-wide AVX2 over int[32] lists, with a CPUID gate, a scalar tail, and the untouched scalar path as fallback.",
  group: "engine",
  tags: [
    "SIMD",
    "AVX2",
    "vectorizer",
    "vmovdqu",
    "vpaddd",
    "vzeroupper",
    "auto-vectorize",
  ],
  sections: [
    {
      id: "what-it-does",
      title: "What the vectorizer does",
      blocks: [
        {
          kind: "p",
          text: "The native backend can fuse a canonical elementwise or reduction loop over a fixed-capacity `int[32]` list into **8-wide AVX2** lanes. The compiler recognizes the range-loop shape the `while`/`for` lowering produces — a small rotatable header (`i < N`), a straight-line body with a single element read/write and one integer op, and an `i = i + 1` latch — and replaces it with:",
        },
        {
          kind: "steps",
          items: [
            {
              title: "CPUID gate",
              text: "A `has_avx2()` check, read from an int32 constant pool appended to the module.",
            },
            {
              title: "8-wide vector main loop",
              text: "`vmovdqu` / `vpaddd` | `vpsubd` | `vpmulld` over `N - N%8` elements. Reductions run a `vpxor` + `vextracti128` + `vpshufd` halving merge.",
            },
            {
              title: "Scalar tail",
              text: "The last `N%8` elements run scalar.",
            },
            {
              title: "Scalar fallback",
              text: "The untouched scalar header remains, so on any host without AVX2 the exact scalar code path still runs.",
            },
          ],
        },
      ],
    },
    {
      id: "why-exact",
      title: "Why it is exact",
      blocks: [
        {
          kind: "table",
          caption: "Correctness properties",
          rows: [
            [
              "Verified bytes",
              "vector slices are byte-for-byte what the encoder emits; `check_encoder_vs_as.py` verifies them",
            ],
            [
              "32-bit lanes only",
              "only `int[32]` lists vectorize, so no wider-than-lane math can be reordered into a different overflow",
            ],
            [
              "Reduction accumulator checked",
              "recognized only when the accumulator really is `int[32]` — verified from IR width annotations",
            ],
            [
              "`vzeroupper` discipline",
              "emitted before any `Call` or `Return` in a function that used the vector path — audited by `check_vex_transitions.py`",
            ],
            [
              "Escape hatches",
              "`--no-vectorize` disables the pass; it also needs loop rotation, so `--no-rotate` disables it too",
            ],
          ],
        },
        {
          kind: "note",
          tone: "good",
          title: "The vectorizer refuses rather than guesses",
          text: 'A loop the recognizer cannot prove safe is left scalar. There is no "probably fine" vector path — the same refuse-over-guess rule as the rest of the engine.',
        },
      ],
    },
    {
      id: "status",
      title: "Status",
      blocks: [
        {
          kind: "table",
          rows: [
            ["SIMD vectorizer loop cases", "6 / 6 passed"],
            ["SIMD vectorizer gate + fallback", "verified"],
            ["AVX-512", "not emitted — Phase III"],
          ],
        },
        {
          kind: "p",
          text: "CPU feature detection is cross-checked against the kernel rather than trusted to CPUID, so the AVX and AVX-512 gates cannot mis-detect. No AVX-512 is emitted yet, but the gating and the VEX transition scanner are already in place underneath it.",
        },
      ],
    },
  ],
}

export const ENGINE_PAGES: DocPage[] = [
  architecture,
  dualTier,
  typechecking,
  pipeline,
  simd,
]
