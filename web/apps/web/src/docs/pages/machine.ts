import type { DocPage } from "../model"

const sourceToMachine: DocPage = {
  slug: "source-to-machine",
  title: "Source to machine",
  description:
    "The full walk: .py → typed IR → verified SSA → x86-64 bytes, with the real artifacts at each step.",
  group: "machine",
  tags: [
    "machine",
    "IR",
    "x86-64",
    "assembly",
    "walkthrough",
    "disasm",
    "encoding",
  ],
  sections: [
    {
      id: "the-artifacts",
      title: "Four artifacts, one program",
      blocks: [
        {
          kind: "p",
          text: "Every Lithon program leaves four artifacts on the way to execution. Each one is inspectable: nothing is hidden behind a debugger:",
        },
        {
          kind: "steps",
          items: [
            {
              title: "Source (.py)",
              text: "What you wrote. Annotations and all.",
            },
            {
              title: "Typed IR (text)",
              text: "`frontend.py` output: `%N = add %a, %b`, `store x, %v : int[64]`, `branch`, `call print`. This is what the engine parses; there is no Python left after this point.",
            },
            {
              title: "Verified SSA",
              text: "After Mem2Reg and phi placement: loads and stores that provably stay in memory are the only ones that survive.",
            },
            {
              title: "x86-64 bytes",
              text: "Written into `PROT_READ | PROT_EXEC` memory and called through a function pointer. No object file, no linker, no `as`.",
            },
          ],
        },
        {
          kind: "code",
          example: {
            lang: "bash",
            title: "seeing it yourself",
            source: `$ lithon prog.py --ir              # stop after IR
$ python3 src/frontend/frontend.py prog.py > prog.ir
$ ./build/tier_runner prog.ir --strict -v
[tier1] native`,
          },
        },
      ],
    },
    {
      id: "reading-ir",
      title: "Reading the IR",
      blocks: [
        {
          kind: "p",
          text: "The IR is deliberately boring. Registers are `%N` virtuals, variables are named slots with widths, and control flow is explicit blocks with `branch`/`jump`. Here is a function with a comparison and two arms, exactly as the frontend emits it:",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/if.py → IR",
            sourceRef: "tests/typed_regression/if.py",
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
              note: "Real frontend output. `branch` is the two-way conditional; both arms end in an unconditional `jump` to the join block: the shape the encoder turns into a `jcc` plus a `jmp`.",
            },
          },
        },
        {
          kind: "p",
          text: "The corresponding x86-64 keeps the same shape: one conditional branch, one shared join:",
        },
        {
          kind: "code",
          example: {
            lang: "asm",
            title: "the same control flow, encoded",
            source: `    mov  eax, 5
    mov  QWORD PTR [rbp-0x8], rax    ; x
    mov  rax, QWORD PTR [rbp-0x8]
    cmp  rax, 3
    jge  .Lelse                       ; !(x < 3)
    mov  eax, 1
    mov  rdi, rax
    call print
    jmp  .Ljoin
.Lelse:
    mov  eax, 3
    mov  rdi, rax
    call print
.Ljoin:`,
            note: "Hand-assembled for reading. The instruction shapes are real encoder output patterns; the byte schedules live in src/jit/encode.h and are verified against GNU as.",
          },
        },
      ],
    },
    {
      id: "calls",
      title: "Calls and the frame",
      blocks: [
        {
          kind: "p",
          text: "User functions get the SysV treatment: arguments in `rdi`/`rsi` (the two-argument cap makes this exact), return value in `rax`, and a frame that `check_stack_alignment.py` audits for 16-byte alignment at every call site:",
        },
        {
          kind: "code",
          example: {
            title: "tests/typed_regression/function.py → IR",
            sourceRef: "tests/typed_regression/function.py",
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
              asm: `add:
    push rbp
    mov  rbp, rsp
    mov  rax, rdi
    add  rax, rsi            ; return a + b
    pop  rbp
    ret

main:
    push rbp
    mov  rbp, rsp
    sub  rsp, 0x10
    mov  edi, 3              ; arg0
    mov  esi, 4              ; arg1
    call add
    mov  QWORD PTR [rbp-0x8], rax   ; x
    mov  rdi, QWORD PTR [rbp-0x8]
    call print
    xor  eax, eax
    leave
    ret`,
              note: "Real IR; hand-assembled listing in the encoder's style. After SSA and coalescing, the `add` body collapses to `rdi + rsi` in `rax`: the loads never reach the encoder.",
            },
          },
        },
      ],
    },
    {
      id: "fib-machine",
      title: "Recursion, end to end",
      blocks: [
        {
          kind: "p",
          text: "The fib(10) example: real IR, real output, and the benchmark that 189× speedup comes from at fib(30):",
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
              note: "Real frontend output. The base case is block1; block2 computes both recursive calls and adds them. The encoder turns each `call` into a real `call fib` with the frame discipline above.",
            },
          },
        },
      ],
    },
  ],
}

const registersAbi: DocPage = {
  slug: "registers-abi",
  title: "Registers & ABI",
  description:
    "The register file, the SysV convention the backend implements, and the stack discipline the auditors check.",
  group: "machine",
  tags: [
    "registers",
    "ABI",
    "SysV",
    "Win64",
    "calling convention",
    "stack",
    "callee-saved",
    "RBP",
  ],
  sections: [
    {
      id: "the-register-file",
      title: "The register file",
      blocks: [
        {
          kind: "table",
          caption:
            "x86-64 general purpose registers, and how the backend uses them",
          rows: [
            ["`RAX`", "accumulator / return value / scratch"],
            [
              "`RCX`",
              "scratch: note: shift counts come in `CL`, a known hazard the bitwise fuzzer targets",
            ],
            ["`RDX`", "scratch / div high half"],
            [
              "`RBX`, `R12`–`R15`",
              "callee-saved: the allocator's long-lived homes",
            ],
            ["`RSP`", "stack pointer, 16-byte aligned at every call"],
            [
              "`RBP`",
              "frame pointer: every function keeps it, which is what makes `[rbp-0x…]` slots readable in a dump",
            ],
            [
              "`RDI`, `RSI`",
              "SysV argument 0 / argument 1: the two-argument cap means these are always the whole argument list",
            ],
            [
              "`R8`, `R9`",
              "SysV arguments 3–4: unused today, reserved for the post-cap future",
            ],
            ["`XMM0`–`XMM15`", "SSE2 float; the vectorizer's `vmovdqu` lanes"],
          ],
        },
        {
          kind: "note",
          tone: "note",
          title: "The RCX hazard",
          text: "Shifts mask their count with 6 bits of `CL`, and the general fuzzer has a dedicated `--bitwise` mode for exactly the shapes where a live `RCX` gets clobbered. It is a real bug class this backend has already paid for.",
        },
      ],
    },
    {
      id: "sysv",
      title: "SysV discipline",
      blocks: [
        {
          kind: "p",
          text: "The audited convention. Every one of the 23 ABI modules is checked for the same invariants:",
        },
        {
          kind: "table",
          caption: "what check_stack_alignment.py parses out of real modules",
          rows: [
            ["prologue", "`push rbp` · `mov rbp, rsp` · `sub rsp, N`"],
            [
              "alignment",
              "`RSP` ≡ 0 (mod 16) at every `call`: the `push rbp` makes up the odd word",
            ],
            ["callee-saved", "RBX/RBP/R12–R15 restored before `ret`"],
            [
              "epilogue",
              "`leave` (`mov rsp, rbp; pop rbp`) or the explicit equivalent, then `ret`",
            ],
            [
              "slots",
              "`QWORD PTR [rbp-0x…]` for int[64], `DWORD`/`WORD`/`BYTE` for narrower widths",
            ],
          ],
        },
        {
          kind: "code",
          example: {
            lang: "asm",
            title: "the canonical prologue/epilogue",
            source: `my_func:
    push rbp
    mov  rbp, rsp
    sub  rsp, 0x20            ; 4 slots, keeps RSP 16-aligned after the push
    ...
    mov  QWORD PTR [rbp-0x8], rdi    ; spill arg0 if it must survive a call
    ...
    leave
    ret`,
          },
        },
      ],
    },
    {
      id: "win64",
      title: "Win64: implemented, unproven",
      blocks: [
        {
          kind: "p",
          text: "The path exists behind `#if defined(_WIN32)` and compiles, but the 23-module ABI audit runs on SysV hosts only. Win64 differs where it always differs, 32-byte shadow space, different volatile set (`RAX RCX RDX R8 R9 R10 R11`), `XMM0`–`XMM5` volatile, and none of that has test evidence yet.",
        },
        {
          kind: "note",
          tone: "warn",
          title: "Treat Win64 as experimental",
          text: "The [limits page](/docs/limits) lists this as an open gap. It will close with its own audit, not with a claim.",
        },
      ],
    },
    {
      id: "executable-memory",
      title: "Where the bytes live",
      blocks: [
        {
          kind: "p",
          text: "Emitted code is written straight into memory the OS marked executable and called through a function pointer: `mmap` with `PROT_READ | PROT_EXEC` on Linux, `VirtualAlloc` on Windows. There is no temporary file, no loader, and no `as` invocation anywhere in the pipeline.",
        },
      ],
    },
  ],
}

const instructionEncoding: DocPage = {
  slug: "instruction-encoding",
  title: "Instruction encoding",
  description:
    "The hand-written x86-64 encoder: how bytes are chosen, and how GNU as is used to prove every one of them.",
  group: "machine",
  tags: [
    "encoding",
    "encoder",
    "bytes",
    "GNU as",
    "VEX",
    "AVX",
    "machine code",
    "verify",
  ],
  sections: [
    {
      id: "hand-rolled",
      title: "Why hand-roll an encoder",
      blocks: [
        {
          kind: "p",
          text: "No LLVM, no Cranelift, no assembler in the build. The backend emits bytes directly: every REX prefix, ModRM, SIB, and displacement chosen by code the project owns and can audit. The cost is that every byte is your responsibility; the benefit is that the whole native tier is ~a C++ codebase you can read in an afternoon.",
        },
        {
          kind: "p",
          text: "The encoder covers 25 of 26 IR opcodes (`Phi` is lowered, not emitted) plus the AVX2 vector forms the auto-vectorizer emits.",
        },
      ],
    },
    {
      id: "verified-against-as",
      title: "Proven against GNU as",
      blocks: [
        {
          kind: "p",
          text: '`tools/check_encoder_vs_as.py` is the answer to "how do you know your bytes are right?". It enumerates operand shapes, asks the encoder for bytes, assembles the same instruction with GNU `as`, and compares: not naively, because x86-64 often has multiple valid encodings for one instruction:',
        },
        {
          kind: "code",
          example: {
            lang: "text",
            title: "the comparison, current",
            source: `9239 instruction cases
7949 byte-identical
1290 different but valid encodings
0 incorrect encodings

WRONG = 0`,
          },
        },
        {
          kind: "p",
          text: 'The 1,290 "different" cases are where the encoder chose a different-but-equivalent form: a different REX prefix ordering, a displacement width, and the harness decodes both to confirm they mean the same instruction. The only number that matters is `WRONG = 0`.',
        },
      ],
    },
    {
      id: "vex-and-avx",
      title: "VEX and the AVX2 forms",
      blocks: [
        {
          kind: "p",
          text: "The auto-vectorizer's slices are byte-for-byte what the encoder emits, verified the same way. The register core of the vector loop, for instance:",
        },
        {
          kind: "code",
          example: {
            lang: "asm",
            title: "src/jit/encoder_test.cpp · real expected bytes",
            source: `vmovdqu ymm0, [rbp+rcx*4]        ; C4 E1 7E 6F 44 8D 00
vmovdqu [rbp+r8*4-64], ymm12     ; C4 21 7E 7F 64 85 C0`,
          },
        },
        {
          kind: "p",
          text: "Two details worth pausing on: the `[rbp+rcx*4]` form is the 4-byte-scaled addressing the 32-bit lanes need, and the second form uses the `R8` REX prefix: the encoder handles the full register range, not just the low eight.",
        },
        {
          kind: "table",
          caption: "the vector instruction set the encoder emits",
          rows: [
            ["`vmovdqu`", "unaligned 256-bit load/store: the lane mover"],
            [
              "`vpaddd` / `vpsubd` / `vpmulld`",
              "the three integer ops the recognizer fuses",
            ],
            ["`vpxor`", "reduction accumulator zeroing"],
            [
              "`vextracti128` + `vpshufd`",
              "the horizontal halving merge for reductions",
            ],
            [
              "`vzeroupper`",
              "emitted before every `Call`/`Return` in a VEX function: audited by check_vex_transitions.py",
            ],
          ],
        },
        {
          kind: "note",
          tone: "note",
          title: "vzeroupper is not optional",
          text: "Skipping it leaves the upper halves of the YMM registers dirty, and the transition penalty into legacy SSE code is brutal. The scanner that audits its placement ships with the encoder tests.",
        },
      ],
    },
    {
      id: "reading-bytes",
      title: "Reading bytes by hand",
      blocks: [
        {
          kind: "p",
          text: "A worked example: `push rbp`, the first byte of every prologue:",
        },
        {
          kind: "table",
          caption: "55 → push rbp",
          rows: [
            [
              "`0x55`",
              "one byte, no REX, no ModRM: `push` of the low half of the register file encodes as a single opcode byte",
            ],
            [
              "`RBP` is register 5",
              "the opcode `0x50 + reg` gives `0x50 + 5 = 0x55`",
            ],
          ],
        },
        {
          kind: "p",
          text: "That is the whole encoding. The interesting instructions: anything with a memory operand: are where REX, ModRM, SIB, and displacement interleave, and that is exactly the surface `check_encoder_vs_as.py` brute-forces 9,239 ways.",
        },
      ],
    },
  ],
}

export const MACHINE_PAGES: DocPage[] = [
  sourceToMachine,
  registersAbi,
  instructionEncoding,
]
