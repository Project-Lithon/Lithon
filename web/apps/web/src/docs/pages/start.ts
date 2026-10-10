import type { DocPage } from "../model"

const welcome: DocPage = {
  slug: "welcome",
  title: "Welcome to Lithon",
  description:
    "Native execution for typed Python: what Lithon is, what it is not, and why it refuses instead of guessing.",
  group: "start",
  tags: [
    "overview",
    "introduction",
    "philosophy",
    "python",
    "native",
    "jIT",
    "x86-64",
  ],
  sections: [
    {
      id: "what-is-lithon",
      title: "What is Lithon?",
      blocks: [
        {
          kind: "p",
          text: "Lithon is a **Python library and native x86-64 execution engine** that runs `.py` source using an extended PEP-526 static typing syntax. Python remains the source language — Lithon is not a separate language — but every supported program is statically verified and can execute as generated machine code, with no LLVM, no Cranelift, and no runtime package.",
        },
        {
          kind: "code",
          example: {
            title: "a small Lithon program",
            source: `x: int[64] = 10
y: int[64] = 20

result: int[64] = x + y

print(result)`,
            output: "30",
          },
        },
        {
          kind: "p",
          text: "That is the whole trick: familiar Python syntax, one annotation per binding, and the compiler now knows enough to verify the program and emit x86-64 for it.",
        },
      ],
    },
    {
      id: "principles",
      title: "Built around a few principles",
      blocks: [
        {
          kind: "cards",
          items: [
            {
              title: "Mandatory static typing",
              text: "Types are verified before execution. Removing annotations does not disable verification — it makes the program invalid.",
            },
            {
              title: "Fixed-width values",
              text: "`int[8]` through `int[64]`, `float[32]`, `float[64]`. Widths are explicit; there is no arbitrary-precision integer behind your back.",
            },
            {
              title: "Native execution",
              text: "Supported programs run as x86-64 machine code emitted by a hand-written encoder, straight into executable memory.",
            },
            {
              title: "No mandatory LLVM",
              text: "The backend is a self-contained C++20 encoder. `as` is only used to verify it, never to build it.",
            },
            {
              title: "Honest fallback",
              text: "What the native tier cannot prove runs through the Tier-0 interpreter in automatic mode — and `--strict` turns that fallback into a refusal.",
            },
            {
              title: "Explicit unsafe memory",
              text: "Pointers exist, and every pointer variable carries a mandatory `_` prefix so raw memory is visible in the source.",
            },
          ],
        },
        {
          kind: "note",
          tone: "note",
          title: "The load-bearing rule",
          text: 'If Lithon cannot prove that a program satisfies the rules required by its native backend, it refuses native compilation rather than guessing. Unknown or unprovable → REFUSE — never "generate potentially incorrect machine code".',
        },
      ],
    },
    {
      id: "how-different",
      title: "How is Lithon different?",
      blocks: [
        {
          kind: "table",
          caption: "The design space Lithon explores",
          rows: [
            ["Syntax", "Python `.py` + extended PEP-526"],
            [
              "Primary execution",
              "Native x86-64 (CPython: bytecode; PyPy: tracing JIT)",
            ],
            ["Static typing", "Mandatory (CPython/PyPy: no; mypyc: optional)"],
            [
              "Fixed-width integers",
              "Built into the type (`int[64]`, `int[32]`, …)",
            ],
            [
              "Raw typed pointers",
              "Built in (`ptr[T]`, `addressof`, `valueof`)",
            ],
            ["Native backend", "Custom x86-64 emitter — no LLVM"],
            [
              "Interpreter fallback",
              "Yes — Tier-0, same program, refused under `--strict`",
            ],
            ["Target", "x86-64 today (ARM64 is roadmap Phase III)"],
          ],
        },
        {
          kind: "p",
          text: "Lithon is not trying to replace Python's ecosystem. It explores one question: **what if Python code kept its familiar source model while giving the execution engine explicit static types, fixed-width values, native execution, and controlled low-level memory access?**",
        },
        {
          kind: "note",
          tone: "warn",
          title: "Experimental project",
          text: "Lithon is a community-preview project. The compiler already executes substantial typed programs natively with a serious verification suite, but it is not a stable production library and not a drop-in CPython replacement.",
        },
      ],
    },
    {
      id: "where-next",
      title: "Where to go next",
      blocks: [
        {
          kind: "linkcards",
          items: [
            {
              title: "Hello, native",
              href: "/docs/hello-world",
              text: "Write, type-check, and run your first program end to end.",
            },
            {
              title: "The mental model",
              href: "/docs/mental-model",
              text: "Two tiers, one rule: prove before you run.",
            },
            {
              title: "Machine view",
              href: "/docs/source-to-machine",
              text: "See the IR and x86-64 behind the examples.",
            },
          ],
        },
      ],
    },
  ],
}

const helloWorld: DocPage = {
  slug: "hello-world",
  title: "Hello, native",
  description:
    "Your first Lithon program: annotate, type-check, compile to IR, and run it on the native tier.",
  group: "start",
  tags: [
    "hello world",
    "first program",
    "tutorial",
    "getting started",
    "print",
    "run",
  ],
  sections: [
    {
      id: "first-program",
      title: "The first program",
      blocks: [
        {
          kind: "p",
          text: "Every binding carries an explicit type. A bare integer literal is `int[64]`, so this program is fully typed without any ceremony beyond the annotations:",
        },
        {
          kind: "code",
          example: {
            title: "hello.py",
            source: `x: int[64] = 10
y: int[64] = 20

result: int[64] = x + y

print(result)`,
            output: "30",
            trace: [
              { step: "1", detail: "store x, 10", state: "x: int[64] = 10" },
              { step: "2", detail: "store y, 20", state: "y: int[64] = 20" },
              { step: "3", detail: "z = x + y", state: "result: int[64] = 30" },
              { step: "4", detail: "call print, result", state: "", out: "30" },
            ],
            machine: {
              note: "The Machine tab on this page and others shows the typed IR the frontend actually emits, plus a readable x86-64 listing of the same work. The full walk-through lives in the Machine view section.",
            },
          },
        },
        {
          kind: "p",
          text: "The same shape from the regression corpus — three operations, three lines of output:",
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
            trace: [
              { step: "1", detail: "z = x + y", state: "z = 30", out: "30" },
              { step: "2", detail: "x - y", state: "10 - 20", out: "-10" },
              { step: "3", detail: "x * y", state: "10 × 20", out: "200" },
            ],
          },
        },
      ],
    },
    {
      id: "the-annotation-rule",
      title: "The annotation rule",
      blocks: [
        {
          kind: "p",
          text: "Dropping the annotations is not an option — the type checker runs **unconditionally**, on every module, whether it annotates anything or not:",
        },
        {
          kind: "code",
          example: {
            title: "rejected · V1_SPEC 0.6.1",
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
          text: "This is not ceremony. The annotation is the contract that lets the backend skip boxing, dynamic dispatch, and hot-path checks — it is what makes the native tier possible at all.",
        },
      ],
    },
    {
      id: "running-it",
      title: "Running it, three ways",
      blocks: [
        {
          kind: "steps",
          items: [
            {
              title: "Type-check only",
              text: "`tools/typecheck.py hello.py` — static analysis over the AST, no execution. Exits non-zero with an `RCR error` on the first violation.",
            },
            {
              title: "Compile to IR",
              text: "`src/frontend/frontend.py hello.py` — lowers the typed AST to Lithon's text IR on stdout. No engine needed, pure Python.",
            },
            {
              title: "Run natively",
              text: "`lithon hello.py --strict` — the two-step dance: frontend to IR, then `tier_runner` emits x86-64 and calls it. `--strict` refuses rather than falling back to the interpreter.",
            },
          ],
        },
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "terminal",
            source: `$ lithon hello.py --strict
[tier1] native
30`,
          },
        },
        {
          kind: "note",
          tone: "good",
          title: "`[tier1] native` is the proof",
          text: "`[tier1] native` goes to stderr; the program's own output goes to stdout. Under `--strict`, a clean exit status means the code was really emitted and executed — a fallback cannot hide behind success.",
        },
      ],
    },
  ],
}

const installation: DocPage = {
  slug: "installation",
  title: "Build & install",
  description:
    "Requirements, the CMake build, the development CLI, and the verification commands.",
  group: "start",
  tags: [
    "install",
    "setup",
    "build",
    "cmake",
    "cli",
    "lithon command",
    "requirements",
  ],
  sections: [
    {
      id: "requirements",
      title: "Requirements",
      blocks: [
        {
          kind: "table",
          rows: [
            ["CMake", "≥ 3.20"],
            ["Compiler", "C++20 (gcc, clang, or MSVC)"],
            [
              "Python",
              "≥ 3.10 (frontend only — the native tier never touches CPython)",
            ],
            ["Architecture", "x86-64 Linux or Windows"],
            [
              "Third-party libraries",
              "none — no LLVM, no Cranelift, no runtime package",
            ],
          ],
        },
      ],
    },
    {
      id: "build",
      title: "Build the engine",
      blocks: [
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "terminal",
            source: `$ git clone git@github.com:Project-Lithon/lithon.git
$ cd lithon
$ cmake -B build -DCMAKE_BUILD_TYPE=Release
$ cmake --build build -j$(nproc)`,
          },
        },
        {
          kind: "p",
          text: "There is no Makefile to hand-edit — CMake drives everything. Python appears at exactly one step, turning a `.py` file into IR; once IR exists the native program never touches CPython.",
        },
      ],
    },
    {
      id: "cli",
      title: "The development CLI",
      blocks: [
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "install in editable mode, then",
            source: `$ pip install -e .
$ lithon tests/programs/float.py
$ lithon tests/programs/float.py --strict    # native only, refuse to fall back
$ lithon tests/programs/float.py --ir       # print the IR and stop
$ lithon tests/programs/float.py -v         # say which tier ran, and why`,
          },
        },
        {
          kind: "table",
          caption: "CLI flags",
          rows: [
            [
              "`--strict`",
              "Native only. Refuse rather than fall back to Tier-0.",
            ],
            ["`--ir`", "Print the typed IR and stop before execution."],
            ["`-v`", "Verbose: report which tier ran and why."],
          ],
        },
        {
          kind: "note",
          tone: "note",
          title: "Prefer scripts? The two-step dance is open",
          text: "`python3 src/frontend/frontend.py prog.py > prog.ir` then `./build/tier_runner prog.ir --strict`. The `lithon` wrapper is convenience, not a black box.",
        },
      ],
    },
    {
      id: "verify-install",
      title: "Prove the install works",
      blocks: [
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "the full gate",
            source: `$ ctest --test-dir build --output-on-failure      # unit tests
$ python3 tools/run_regression.py                # untyped suite
$ python3 tools/run_typed_regression.py          # typed suite
$ python3 tools/run_tier_diff.py                 # every program, both tiers
$ bash tools/verify_all.sh                       # all of the above`,
          },
        },
        {
          kind: "p",
          text: '`run_tier_diff.py` is the highest-value of these: it runs every program through both tiers and requires byte-identical stdout, then reports which tier actually ran — so a green run cannot hide "everything silently fell back to the interpreter".',
        },
      ],
    },
  ],
}

const mentalModel: DocPage = {
  slug: "mental-model",
  title: "The mental model",
  description:
    "A two-lane road: prove before you run, emit native for what is proven, refuse everything else.",
  group: "start",
  tags: [
    "mental model",
    "tiers",
    "philosophy",
    "strict",
    "refusal",
    "verification",
  ],
  sections: [
    {
      id: "two-lanes",
      title: "A two-lane road",
      blocks: [
        {
          kind: "p",
          text: "A block whose types are provably static takes the **Tier-1** native path and is compiled straight to x86-64. Anything the verifier cannot establish falls to the **Tier-0** C++ interpreter — a real fallback rather than a guess — and `--strict` turns that fallback into a refusal.",
        },
        {
          kind: "cards",
          items: [
            {
              title: "Prove before run",
              text: "Types are settled before execution begins. A flow that cannot be established is never emitted.",
            },
            {
              title: "Emit native",
              text: "Verified IR blocks become guard-free x86-64 in executable memory. No guards, no checks, no boxing on the hot path.",
            },
            {
              title: "Fall back honestly",
              text: "Tier-0 runs the same program when the native tier refuses. `--strict` refuses instead — success cannot hide a fallback.",
            },
          ],
        },
      ],
    },
    {
      id: "refuse-over-guess",
      title: "Refuse over guess",
      blocks: [
        {
          kind: "code",
          example: {
            lang: "text",
            title: "the decision procedure",
            source: `Unknown / unprovable
        │
        ▼
     REFUSE

Unknown
  │
  ▼
 Guess
  │
  ▼
     Generate potentially incorrect machine code   ← never`,
          },
        },
        {
          kind: "p",
          text: "Every strange-looking rule in the language — mandatory annotations, fixed widths, no narrowing, definite assignment, shift-count ranges — exists to keep the verifier on the left path. Each one turns something the backend would have to guess about into something it knows.",
        },
        {
          kind: "note",
          tone: "note",
          title: "What this buys you",
          text: "When a Lithon program runs on Tier-1, the machine code has no runtime type checks, no overflow guards it could have proven away, and no hidden allocation. The verifier already did that work, once, at compile time.",
        },
      ],
    },
    {
      id: "strict-mode",
      title: "Strict mode is the honesty test",
      blocks: [
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "terminal",
            source: `$ lithon program.py --strict`,
          },
        },
        {
          kind: "p",
          text: 'Under `--strict`, "the program printed the right number" becomes meaningful: printing at all means the native tier compiled and executed it. This is how the test suites keep the interpreter from quietly carrying the project.',
        },
        {
          kind: "linkcards",
          items: [
            {
              title: "Inside the engine",
              href: "/docs/architecture",
              text: "Four layers from `.py` source to executable memory.",
            },
            {
              title: "Dual-tier execution",
              href: "/docs/dual-tier",
              text: "What Tier-0 and Tier-1 each do, and when.",
            },
            {
              title: "The verification gate",
              href: "/docs/verification",
              text: "How the claims get checked — layer by layer.",
            },
          ],
        },
      ],
    },
  ],
}

export const START_PAGES: DocPage[] = [
  welcome,
  helloWorld,
  installation,
  mentalModel,
]
