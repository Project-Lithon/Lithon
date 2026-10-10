import { createFileRoute } from "@tanstack/react-router"

import { Badge } from "@workspace/ui/components/badge"
import { Card, CardContent } from "@workspace/ui/components/card"

import { CodeBlock, DocSection, Note, PageHero, SpecTable, WRAP } from "../site"

export const Route = createFileRoute("/docs")({ component: Docs })

const NAV = [
  ["quickstart", "Quickstart"],
  ["mental-model", "Mental model"],
  ["types", "Types & flow"],
  ["language", "Language subset"],
  ["pipeline", "The pipeline"],
  ["semantics", "Semantics"],
  ["runtime", "Runtime"],
  ["verification", "Verification"],
  ["limits", "Honest limits"],
] as const

function Docs() {
  return (
    <>
      <PageHero
        kicker="01 / Documentation"
        title={
          <>
            Know the path
            <br />
            from source to speed.
          </>
        }
        lede="How Lithon is built and run: the annotation grammar the verifier actually accepts, the two execution tiers, the optimizer and SSA pipeline behind the native path, and the gaps that are still open."
        meta={[
          "Mandatory static types",
          "Dual-tier execution",
          "Zero dependencies",
        ]}
      />

      <section className={`${WRAP} grid gap-10 py-10 lg:grid-cols-[200px_1fr]`}>
        <aside className="hidden lg:block">
          <nav
            aria-label="On this page"
            className="sticky top-20 space-y-1 border-s ps-3 text-sm"
          >
            <p className="mb-2 text-xs font-semibold text-muted-foreground">
              On this page
            </p>
            {NAV.map(([id, label]) => (
              <a
                key={id}
                href={`#${id}`}
                className="block text-muted-foreground hover:text-foreground"
              >
                {label}
              </a>
            ))}
          </nav>
        </aside>

        <div>
          <DocSection
            id="quickstart"
            title="Build once. Understand everything."
          >
            <Badge variant="secondary">Start here</Badge>
            <p>
              There is no Makefile — CMake drives everything. The engine needs a
              toolchain, but no third-party libraries: no LLVM, no runtime
              package to install. Python appears at exactly one step, turning a{" "}
              <code>.py</code> file into IR; once IR exists the native program
              never touches CPython.
            </p>
            <CodeBlock title="terminal · compile and run">
              {`$ cmake -B build -DCMAKE_BUILD_TYPE=Release
$ cmake --build build -j$(nproc)
$ python3 src/frontend/frontend.py tests/programs/float.py > /tmp/float.ir
$ ./build/tier_runner /tmp/float.ir --strict
[tier1] native
0.3333333333333333`}
            </CodeBlock>
            <p>
              <code>[tier1] native</code> goes to stderr; the program's own
              output goes to stdout. <code>--strict</code> means "native only,
              refuse rather than fall back", so a clean exit status is proof the
              code was really emitted and executed.
            </p>
            <SpecTable
              rows={[
                ["build system", <code key="b">cmake</code>],
                ["compiler", <code key="c">C++20</code>],
                ["Python", <code key="p">&ge; 3.10</code>],
                [
                  "third-party dependencies",
                  <span key="n" className="text-destructive">
                    none
                  </span>,
                ],
              ]}
            />
            <p>
              The optional <code>lithon</code> wrapper does the two-step dance
              for you: <code>pip install -e .</code>, then{" "}
              <code>lithon tests/programs/float.py --strict</code>. Add{" "}
              <code>--ir</code> to print the IR and stop, or <code>-v</code> to
              see which tier ran and why.
            </p>
          </DocSection>

          <DocSection id="mental-model" index="01" title="The mental model">
            <p>
              Lithon is a two-lane road. A block whose types are provably static
              takes the Tier-1 native path and is compiled straight to x86-64.
              Anything the verifier cannot establish falls to a Tier-0 C++
              interpreter, which is a real fallback rather than a guess — and{" "}
              <code>--strict</code> turns that fallback into a refusal.
            </p>
            <div className="grid gap-3 sm:grid-cols-3">
              {[
                [
                  "Prove before run",
                  "Types are settled before execution begins. A flow that cannot be established is never emitted.",
                ],
                [
                  "Emit native",
                  "Typed AST blocks become guard-free x86-64 in executable memory. No guards on the hot path.",
                ],
                [
                  "Fall back honestly",
                  "Tier-0 runs the same program. --strict refuses rather than quietly taking the slow lane.",
                ],
              ].map(([title, text]) => (
                <Card key={title} size="sm">
                  <CardContent className="font-medium text-foreground">
                    {title}
                    <p className="mt-1 font-normal text-muted-foreground">
                      {text}
                    </p>
                  </CardContent>
                </Card>
              ))}
            </div>
          </DocSection>

          <DocSection id="types" index="02" title="Types & flow">
            <p>
              Every binding carries an explicit annotation, and numeric types
              carry an explicit width. This is not ceremony — it is the contract
              that lets Lithon skip boxing, dynamic dispatch, and hot-path
              checks. A bare <code>int</code> is not a type in Lithon: the
              checker rejects it by name.
            </p>
            <SpecTable
              rows={[
                [
                  "signed integers",
                  <code key="1">int[8] int[16] int[32] int[64]</code>,
                ],
                ["floating point", <code key="2">float[32] float[64]</code>],
                ["boolean", <code key="3">bool</code>],
                [
                  "unannotated binding",
                  <span key="4" className="text-destructive">
                    rejected
                  </span>,
                ],
                ["widening a value", "automatic"],
                [
                  "narrowing a value",
                  <span key="5" className="text-destructive">
                    never allowed
                  </span>,
                ],
                ["int &rarr; float", "automatic"],
                [
                  "float &rarr; int",
                  <span key="6" className="text-destructive">
                    does not exist · no cast syntax
                  </span>,
                ],
                [
                  "bare integer literal",
                  <span key="7">
                    is <code>int[64]</code>
                  </span>,
                ],
                ["range(N) vs loop width", "checked at compile time"],
              ]}
            />
            <CodeBlock title="widening · accepted">{`n: int[8] = 100
wide: int[64] = n
ratio: float[64] = n`}</CodeBlock>
            <CodeBlock title="narrowing · rejected">{`narrow: int[8] = wide
error: cannot narrow int[64] into int[8]`}</CodeBlock>
            <p>
              Because a bare literal is <code>int[64]</code>, passing one
              straight into a narrower parameter is always a narrowing error.
              Function contracts are checked the same way: parameters and the
              return type are mandatory, and call sites are checked against the
              signature.
            </p>
            <CodeBlock title="tests/typed_regression/function.py">{`def add(a: int[64], b: int[64]) -> int[64]:
    return a + b

x: int[64] = add(3, 4)
print(x)`}</CodeBlock>
          </DocSection>

          <DocSection id="language" index="03" title="The language subset">
            <p>
              The engine is fast and well tested on the subset it supports. That
              subset is small on purpose, and the boundaries are worth knowing
              before you write anything against it.
            </p>
            <SpecTable
              rows={[
                ["assignment", "typed and untyped"],
                ["augmented assignment", <code key="1">+= -= *= /=</code>],
                ["literals", "int · float · bool"],
                ["arithmetic", <code key="2">+ - * /</code>],
                ["comparisons", "single, non-chained"],
                [
                  "boolean operators",
                  <span key="3">
                    <code>and</code> <code>or</code> <code>not</code> · two
                    operands
                  </span>,
                ],
                [
                  "control flow",
                  <span key="4">
                    <code>if</code> · <code>elif</code> · <code>else</code> ·{" "}
                    <code>while</code>
                  </span>,
                ],
                [
                  "loops",
                  <span key="5">
                    <code>for x in range(N)</code> · one argument
                  </span>,
                ],
                ["functions", "recursion, self tail calls"],
                [
                  "chained comparison",
                  <span key="6" className="text-destructive">
                    not supported
                  </span>,
                ],
                [
                  "for / else",
                  <span key="7" className="text-destructive">
                    rejected, never silently dropped
                  </span>,
                ],
              ]}
            />
            <p>
              A loop variable must be declared before the loop, because the
              verifier checks what <code>range(N)</code> produces against the
              width already on that name:
            </p>
            <CodeBlock title="tests/typed_regression/nested_loop.py">{`total: int[64] = 0
i: int[64] = 0
j: int[64] = 0
for i in range(5):
    for j in range(5):
        total = total + i * j
print(total)`}</CodeBlock>
          </DocSection>

          <DocSection
            id="pipeline"
            index="04"
            title="The optimizer and the SSA pipeline"
          >
            <p>
              Register allocation and liveness are shipped and tested, and so is
              a set of optimizations that can each be switched off individually,
              so their effect is measured rather than assumed.
            </p>
            <SpecTable
              caption="Measured against HEAD, one pass at a time"
              rows={[
                [
                  "Strength-reduced multiplies",
                  <span key="1">
                    <code>optimize.h</code> · nested 1.062×
                  </span>,
                ],
                [
                  "Accumulator unroll (opt-in)",
                  <span key="2">
                    <code>optimize.h</code> · float reduction 1.58× · 95.8 →
                    60.5 ms
                  </span>,
                ],
                [
                  "Callee-saved borrowing",
                  <span key="3">
                    <code>register_alloc.h</code> · fib 1.063×
                  </span>,
                ],
                [
                  "Mem2Reg + SSA copy resolution",
                  <span key="4">
                    <code>ssa.h</code> · 29 loads · 20 stores removed
                  </span>,
                ],
                [
                  "Diamond unrolling (opt-in)",
                  <span key="5" className="text-destructive">
                    1.10× slower, kept behind a flag
                  </span>,
                ],
              ]}
            />
            <p>
              Underneath sits the SSA conversion, done one switchable phase at a
              time. The IR keeps mutable variables in memory — a variable is
              written by a <code>Store</code> and read by a <code>Load</code> —
              so promotion here means turning those into values that live in
              registers.
            </p>
            <SpecTable
              caption="SSA phases · all shipped"
              rows={[
                [
                  "2.1",
                  <span key="1">
                    CFG, Cooper-Harvey-Kennedy dominators, natural loops,
                    preheaders, latches, exits · <code>loop_info.h</code>
                  </span>,
                ],
                [
                  "2.2",
                  <span key="2">
                    Phi placement by iterated dominance frontier ·{" "}
                    <code>ssa.h</code>
                  </span>,
                ],
                [
                  "2.3",
                  <span key="3">
                    Mem2Reg — promote, place phis, rewrite the memory traffic
                    away · <code>ssa.h</code>
                  </span>,
                ],
                [
                  "2.4",
                  <span key="4">
                    SSA copy propagation, dead-value elimination, then{" "}
                    <code>resolve_phis()</code> · <code>optimize.h</code>
                  </span>,
                ],
                [
                  "2.5",
                  <span key="5">
                    Phi copies as register moves instead of a memory round-trip
                    · <code>register_alloc.h</code>
                  </span>,
                ],
                [
                  "2.6",
                  <span key="6">
                    Dedicated loop-exit blocks where the exit edge is genuinely
                    shared · <code>loop_info.h</code>
                  </span>,
                ],
                [
                  "2.7",
                  <span key="7">
                    CFG liveness and interference-graph allocation ·{" "}
                    <code>liveness.h</code>
                  </span>,
                ],
                [
                  "2.8",
                  <span key="8">
                    Register coalescing — a merge adopts its source's dead
                    register · both halves
                  </span>,
                ],
              ]}
            />
            <Note title="One decision in there is load-bearing">
              Promotion is gated on a <em>must</em>-analysis that iterates{" "}
              <b>down</b> from "everything", converging on the greatest
              fixpoint. Iterating upward from nothing makes ordinary loop
              accumulators silently un-promotable forever. Anything the analysis
              cannot prove stays in memory, and <code>vars_declined</code>{" "}
              reports it.
            </Note>
            <p>
              The <a href="/roadmap/phase-1-dual-tier">Phase I page</a> walks
              through all eight phases with the reasoning behind each, including
              why register coalescing honestly deletes five merges across the
              typed regression corpus and not more.
            </p>
          </DocSection>

          <DocSection
            id="semantics"
            index="05"
            title="Semantics worth knowing before you write anything"
          >
            <p>
              A few places where Lithon does not follow CPython, either because
              the machine word forces a decision or because a deliberate one was
              made. All of them are tested.
            </p>
            <CodeBlock title="modulo · truncating, like C">{`print(-7 % 3)   # Lithon: -1     CPython: 2
print(7 % -3)   # Lithon:  1     CPython: -2`}</CodeBlock>
            <p>
              <code>%</code> truncates toward zero and takes the sign of the
              dividend, which is what C, Rust and Java do. A zero divisor traps
              on both engines, and a constant power-of-two divisor is
              strength-reduced to a mask and a sign fixup.
            </p>
            <CodeBlock title="shifts · 64-bit, count checked">{`print(5 << 63)   # Lithon: -9223372036854775808
print(1 << 64)   # Lithon: RCR error`}</CodeBlock>
            <p>
              Left shifts wrap, because there is no wider type to widen into. A
              count outside <code>0..63</code> is refused — at compile time for
              a literal, at run time otherwise. <code>2.5 &amp; 1</code> is a
              type error: there is no float bit pattern in Lithon to
              reinterpret.
            </p>
            <p>
              Floats are implemented end to end in SSE2. Division by zero traps
              like Python's <code>ZeroDivisionError</code> rather than producing
              IEEE <code>inf</code>/<code>nan</code>, a NaN divisor must{" "}
              <em>not</em> trap because Python propagates it, and{" "}
              <code>-0.0</code> must, because it compares equal to{" "}
              <code>0.0</code>.
            </p>
            <p>
              The <a href="/roadmap/language">language and semantics page</a>{" "}
              has the full list, including the <code>ZF AND !PF</code> problem
              that makes <code>comisd</code> unable to tell "equal" from "NaN"
              on its own.
            </p>
          </DocSection>

          <DocSection id="runtime" index="06" title="Runtime notes">
            <p>
              The backend is a hand-rolled x86-64 encoder written in C++20.
              Emitted machine code is written straight into memory the OS marked
              executable, and called through a function pointer —{" "}
              <code>mmap</code> with <code>PROT_READ | PROT_EXEC</code> on
              Linux, <code>VirtualAlloc</code> on Windows. There is no LLVM, no
              Cranelift, and no external runtime stack in the picture.
            </p>
            <div className="grid gap-3 sm:grid-cols-3">
              {[
                [
                  "IR text format",
                  "Parsed inside the engine, so the native tier has no Python dependency of any kind.",
                ],
                [
                  "Calling conventions",
                  "SysV and Win64 ABI alignment is implemented and audited for the host convention.",
                ],
                [
                  "Float is real",
                  "SSE2 end to end. One formatter serves both tiers, so their output agrees byte for byte.",
                ],
              ].map(([title, text]) => (
                <Card key={title} size="sm">
                  <CardContent className="font-medium text-foreground">
                    {title}
                    <p className="mt-1 font-normal text-muted-foreground">
                      {text}
                    </p>
                  </CardContent>
                </Card>
              ))}
            </div>
            <p>
              Register allocation and liveness are shipped and tested, along
              with the optimizer pipeline above. SysV is audited on every host,
              and the Win64 path exists behind <code>#if defined(_WIN32)</code>{" "}
              but has no test evidence yet.
            </p>
            <p>
              CPU feature detection is cross-checked against the kernel rather
              than trusted to CPUID, so the AVX and AVX-512 gates cannot
              mis-detect. No AVX is emitted yet — that is Phase III — but the
              gating and the VEX transition scanner are already in place
              underneath it.
            </p>
          </DocSection>

          <DocSection
            id="verification"
            index="07"
            title="How the claims get checked"
          >
            <p>
              Work outward and stop when you are satisfied. Each layer is
              roughly an order of magnitude slower than the one above it, which
              is what makes running all of them reasonable rather than
              aspirational.
            </p>
            <CodeBlock title="the full gate">{`$ ctest --test-dir build --output-on-failure        # 28/28
$ bash tools/verify_all.sh
$ python3 tools/run_regression.py                  # 15/15 untyped
$ python3 tools/run_typed_regression.py            # 13/13 typed
$ python3 tools/run_tier_diff.py                   # 39/39 both tiers`}</CodeBlock>
            <p>
              <code>run_tier_diff.py</code> is the highest-value of those. It
              runs every program through both tiers and requires byte-identical
              stdout, then reports which tier actually ran — so a green run
              cannot hide "everything silently fell back to the interpreter".
            </p>
            <CodeBlock title="layer 3 · per-shape differential fuzzing">{`$ python3 tools/fuzz_diff.py --count 300             # general
$ python3 tools/fuzz_diff.py --count 300 --floats    # int/float mixes
$ python3 tools/fuzz_diff.py --count 300 --bitwise   # & | ^ << >>, RCX hazard
$ python3 tools/fuzz_diff.py --mod-negatives --count 300
$ python3 tools/fuzz_diff.py --phi --count 300        # merges, with and without --ssa`}</CodeBlock>
            <p>
              The <a href="/roadmap/verification">verification page</a> covers
              the whole stack, including how to isolate one optimization's
              effect, the two benchmark traps that fail quietly rather than
              loudly, and what a green run still does not prove.
            </p>
          </DocSection>

          <DocSection id="limits" index="08" title="Honest limits">
            <p>
              A green test run covers the subset above and should not be
              mistaken for a finished language. These are the gaps as they stand
              today.
            </p>
            <SpecTable
              rows={[
                ["IR opcodes emitted", <code key="1">25 of 26</code>],
                [
                  "Phi",
                  <span key="2" className="text-destructive">
                    lowered, not emitted
                  </span>,
                ],
                [
                  "arguments per function / call",
                  <span key="3" className="text-destructive">
                    capped at 2
                  </span>,
                ],
                [
                  "float merges",
                  <span key="4" className="text-destructive">
                    never promoted · needs an XMM pool
                  </span>,
                ],
                [
                  "Win64 ABI",
                  <span key="5" className="text-destructive">
                    implemented, no test evidence yet
                  </span>,
                ],
                [
                  "AOT binary emit",
                  <span key="6" className="text-destructive">
                    none · everything runs in-process
                  </span>,
                ],
                [
                  "architectures",
                  <span key="7" className="text-destructive">
                    x86-64 only
                  </span>,
                ],
                [
                  "AVX emitted",
                  <span key="8" className="text-destructive">
                    none · feature gating only
                  </span>,
                ],
                [
                  "diamond unrolling",
                  <span key="9">
                    opt-in via <code>--unroll-diamonds</code>
                  </span>,
                ],
              ]}
            />
            <p>
              <code>Phi</code> is the single unemitted opcode, and it is what
              blocks <code>if</code>-as-expression lowering once both arms have
              to merge without a memory round-trip. Lifting the two-argument cap
              is the other blocker: most of the remaining test programs are
              waiting on it.
            </p>
            <p>
              Everything below is how those claims get checked rather than taken
              on trust:
            </p>
            <CodeBlock title="verification">{`$ ctest --test-dir build --output-on-failure
$ bash tools/verify_all.sh
$ python3 tools/run_typed_regression.py     # 13/13
$ python3 tools/run_tier_diff.py            # 39/39
$ python3 tools/fuzz_diff.py --count 300 --floats`}</CodeBlock>
            <Note title="Opcode coverage is not operand coverage">
              25 of 26 opcodes are emitted and each is exercised through{" "}
              <code>tier_runner --strict</code>. It does not prove every operand
              shape is right — <code>gt</code> and <code>not</code> have a
              single native use each in the checked-in IR corpus.{" "}
              <code>Mod</code> is the standing example: the general fuzzer only
              emits finite constants, so it cannot generate{" "}
              <code>1.0 % inf</code>, which was returning NaN natively while the
              interpreter was correct.
            </Note>
            <p>
              The <a href="/roadmap">roadmap</a> tracks what is next and which
              phase each item belongs to, and every shipped entry there carries
              the test that enforces it.
            </p>
          </DocSection>
        </div>
      </section>
    </>
  )
}
