import { createFileRoute } from "@tanstack/react-router"

import { Card, CardContent } from "@workspace/ui/components/card"

import {
  CodeBlock,
  DocSection,
  Note,
  PageHero,
  SpecTable,
  WRAP,
} from "../../site"

export const Route = createFileRoute("/roadmap/phase-1-dual-tier")({
  component: Phase1DualTier,
})

const NAV = [
  ["tiers", "The two tiers"],
  ["emitter", "The emitter"],
  ["abi", "ABI & gating"],
  ["optimizer", "The optimizer"],
  ["ssa", "SSA, phase by phase"],
  ["load-bearing", "Two load-bearing calls"],
  ["owed", "What Phase I owes"],
] as const

const TILES = [
  [
    "Tier 0 · interpreter",
    "C++ execution of the IR. Always available, never assumed correct enough to be measured against.",
  ],
  [
    "Tier 1 · native",
    "Guard-free machine code in executable memory. No dispatch, no boxing, no interpreter frame.",
  ],
  [
    "The gate",
    "A printed value whose type is not provably static falls to Tier-0 rather than being emitted.",
  ],
] as const

const SUBPAGES = [
  [
    "Next in the roadmap",
    "Phase II · AOT binary synthesis",
    "/roadmap/phase-2-aot",
  ],
  ["Back to", "The full tracker", "/roadmap"],
] as const

function Phase1DualTier() {
  return (
    <>
      <PageHero
        kicker="Roadmap · Phase I"
        title={
          <>
            Two lanes,
            <br />
            <em>and a refusal.</em>
          </>
        }
        lede="How a block of typed Python becomes x86-64 bytes, what the optimizer does to them on the way, and the eight SSA phases that turned a memory-based JIT into an SSA one."
        meta={["In progress", "25 of 26 opcodes", "8 SSA phases"]}
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
          <DocSection id="tiers" title="Two lanes, and a refusal.">
            <p>
              Lithon runs the same program on two engines. A block whose types
              can be established before execution is compiled straight to x86-64
              and called through a function pointer. A block that cannot be
              established runs on the Tier-0 C++ interpreter instead of
              guessing.
            </p>
            <p>
              The refusal is the feature. <code>--strict</code> turns the
              fallback into a hard error, which is what makes the tier line in
              the output worth reading: a clean exit under <code>--strict</code>{" "}
              is proof the bytes were really emitted and really ran, whereas{" "}
              <code>--auto</code> can hide a total fallback behind output that
              happens to look right.
            </p>
            <div className="grid gap-3 sm:grid-cols-3">
              {TILES.map(([title, text]) => (
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

          <DocSection
            id="emitter"
            index="01"
            title="The emitter and executable memory"
          >
            <p>
              The backend is a hand-rolled x86-64 encoder in C++20. There is no
              LLVM and no Cranelift: every opcode is written byte by byte, and
              the encodings are checked against GNU <code>as</code> rather than
              trusted, because an encoder that is subtly wrong produces
              plausible-looking disassembly.
            </p>
            <p>
              Emitted code is written into memory the operating system marked
              executable — <code>mmap</code> with{" "}
              <code>PROT_READ | PROT_EXEC</code> on Linux,{" "}
              <code>VirtualAlloc</code> on Windows — and entered through a
              function pointer. Nothing about that requires a runtime library,
              which is where the zero-dependency claim comes from: the engine's{" "}
              <em>inputs</em> are a compiler and a text file, and its output is
              bytes the CPU already knows how to run.
            </p>
            <CodeBlock title="read the generated code">{`$ ./build/lithon_jit nested_loop.ir --dump-code /tmp/n.bin
$ objdump -D -b binary -mi386:x86-64 -M intel /tmp/n.bin

# Did strength reduction fire? nested_loop has one multiply
# per inner iteration, so zero imul means it did.
$ objdump -D -b binary -mi386:x86-64 -M intel /tmp/n.bin | grep -c imul
0`}</CodeBlock>
          </DocSection>

          <DocSection id="abi" index="02" title="ABI and target-feature gating">
            <p>
              Emitted functions have to be callable by ordinary C code, which
              means SysV and Win64 both have to be right about callee-saved
              registers and stack alignment. The audit does not read the emitter
              to decide this — it disassembles every emitted function and checks
              alignment at each call and return.
            </p>
            <p>
              On the same principle, CPU feature detection is cross-checked
              against the kernel rather than trusted to CPUID alone. That gate
              is what will make an AVX2 path safe to add later, and the VEX
              transition scanner is already in place to catch an AVX emission
              that forgets <code>vzeroupper</code>.
            </p>
            <SpecTable
              rows={[
                ["SysV x64 ABI", "shipped & audited"],
                [
                  "Win64 ABI",
                  <span key="w" className="text-destructive">
                    implemented, not yet verified on a Windows host
                  </span>,
                ],
                ["CPUID vs kernel", "cross-checked, 30 assertions"],
                ["AVX / AVX-512 gate", "shipped as a guardrail"],
                [
                  "AVX emitted today",
                  <span key="a" className="text-destructive">
                    none
                  </span>,
                ],
              ]}
            />
            <Note title="Why the Win64 row is not ticked">
              <p>
                The code path exists behind <code>#if defined(_WIN32)</code>,
                but the audit only exercises whichever ABI the host uses. On a
                Linux machine that means the Win64 path has been compiled and
                never checked, so listing it as shipped would be a claim no test
                supports.
              </p>
            </Note>
          </DocSection>

          <DocSection
            id="optimizer"
            index="03"
            title="The optimizer, measured one pass at a time"
          >
            <p>
              Every optimization is independently switchable, so its effect can
              be measured rather than assumed. The numbers below are from an
              Intel i3-3110M, twelve interleaved rounds on the CPU-time clock
              with one core pinned — and differences under about five percent on
              a shared machine are not meaningful.
            </p>
            <SpecTable
              caption="Optimization pipeline · isolated against HEAD"
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
                  "Constant folding, dead code, self tail calls",
                  <span key="5">
                    <code>optimize.h</code> ·{" "}
                    <span className="text-destructive">
                      bundled, not isolated
                    </span>
                  </span>,
                ],
                [
                  "Diamond unrolling (opt-in)",
                  <span key="6">
                    <code>optimize.h</code> ·{" "}
                    <span className="text-destructive">
                      1.10× slower, kept behind a flag
                    </span>
                  </span>,
                ],
              ]}
            />
            <p>
              Two of those rows are opt-in for a reason rather than by caution.
              Accumulator splitting reassociates a float reduction, so the
              native value stops being bit-identical to the interpreter's —
              correct, but a different answer in the last bits, which the
              tier-diff gate would (correctly) flag. Diamond unrolling is
              implemented, correct and fuzzed, and measured <em>slower</em>: a
              diamond's if/else test is irreducible, so unrolling only inflates
              the loop. It stays behind <code>--unroll-diamonds</code> instead
              of being deleted, because the measurement is about this shape and
              not about the pass.
            </p>
          </DocSection>

          <DocSection
            id="ssa"
            index="04"
            title="The SSA pipeline, phase by phase"
          >
            <p>
              The memory-based JIT is being converted to SSA one phase at a
              time, each independently switchable. The IR keeps mutable
              variables in memory — a variable is written by a{" "}
              <code>Store</code> and read by a <code>Load</code>, exactly like a
              spill slot — so promotion here means turning those into values
              that live in registers.
            </p>
            <SpecTable
              caption="Shipped phases"
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
                    Phi placement by iterated dominance frontier over each
                    variable's def blocks · <code>ssa.h</code>
                  </span>,
                ],
                [
                  "2.3",
                  <span key="3">
                    Mem2Reg — promote what is promotable, place its phis,
                    rewrite the loads and stores away · <code>ssa.h</code>
                  </span>,
                ],
                [
                  "2.4",
                  <span key="4">
                    SSA copy propagation and dead-value elimination, then{" "}
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
                    Dedicated loop-exit blocks, synthesized only where the exit
                    edge is genuinely shared · <code>loop_info.h</code>
                  </span>,
                ],
                [
                  "2.7",
                  <span key="7">
                    CFG liveness and interference-graph allocation, replacing
                    the flat instruction-span model · <code>liveness.h</code>
                  </span>,
                ],
                [
                  "2.8",
                  <span key="8">
                    Register coalescing — a merge's destination adopts its
                    source's dead register · both halves
                  </span>,
                ],
              ]}
            />
            <p>
              Phase 2.8 is the one with an honest size attached to it: it
              deletes <b>five</b> merges across the typed regression corpus.
              That is not a disappointing result so much as an accurate one.
              Mem2Reg runs first, so a merge's arms are already SSA values, and
              the ones that arrive are constants, another merge's value, or an
              arithmetic result still live somewhere else. A constant has no
              register to adopt, a merge's value would mean relocating a whole
              merge, and the third case is refused outright.
            </p>
            <p>
              The guard that makes it safe is a single comparison:{" "}
              <code>last_use == the store</code>'s index means the source is
              dead the instant the copy retires, so the merge itself is the only
              thing left wanting the register. Four conditions back it up — the
              source must be a real register, the merge must not outlive a call
              unless the register is callee-saved, no other value's range may
              touch the merge's extent, and two merges may not claim one
              register. <code>phi_register_test</code> pins all three shapes: a
              dead arithmetic source coalesces, a source read again after the
              join does not, and constants do not.
            </p>
            <CodeBlock title="--stats · where the merges went">{`$ ./build/tier_runner prog.ir --auto --stats
phi copies: 5 of 9 in registers`}</CodeBlock>
            <p>
              A shortfall there is the register budget, not a defect. A Phi copy
              gets a callee-saved register, of which there are five, and real
              variables rank for them first by loop-depth weight — so a
              merge-heavy program gets every copy in a register, while a
              function with more merges than registers keeps the memory path for
              the remainder. Still correct, just not yet free.
            </p>
          </DocSection>

          <DocSection
            id="load-bearing"
            index="05"
            title="Two decisions that are load-bearing"
          >
            <p>
              Two choices in that sequence look like details until they produce
              wrong answers. Both are recorded here because the reasoning is the
              thing a future contributor needs, not the outcome.
            </p>
            <Note title="Promotion is gated on a must-analysis that iterates downward">
              <div className="space-y-2">
                <p>
                  A variable is promoted only if it is{" "}
                  <em>definitely assigned</em> at every load and on every
                  planned Phi's incoming edge. The dataflow meets over
                  predecessors by intersection and iterates{" "}
                  <em>down from "everything"</em>, which converges on the
                  greatest fixpoint.
                </p>
                <p>
                  That is not a stylistic choice. Iterating upward from nothing,
                  a variable assigned in a preheader and read inside the loop
                  collapses at the header to the meet of{" "}
                  <code>{"{preheader}"}</code> and <code>{"{back edge}"}</code>;
                  the back-edge set never picks it up, and ordinary loop
                  accumulators become silently un-promotable forever. Anything
                  the analysis cannot prove stays in memory, and{" "}
                  <code>vars_declined</code> reports it rather than letting it
                  pass unnoticed.
                </p>
              </div>
            </Note>
            <Note title="resolve_phis() is a lowerer, not an emitter">
              <div className="space-y-2">
                <p>
                  No codegen path emits <code>Op::Phi</code>, so resolution
                  stores each incoming value on its predecessor <em>edge</em>{" "}
                  and turns the Phi into a load. That is what makes conditional
                  expressions work today, and it is correct — <code>--ssa</code>{" "}
                  is differential-fuzzed against the interpreter — but the
                  merged value still round-trips through memory.
                </p>
                <p>
                  There is a related subtlety in the liveness that feeds it. A
                  merge's operands are live on an <em>edge</em>, which
                  block-granular liveness has no term for: the operand is read
                  at the instant the predecessor's branch is taken, later than
                  every ordinary use in that block. Filing it as a use of the
                  join is wrong in both directions at once — it stretches the
                  value's range backwards across the whole join and never
                  records that the value must survive to the end of the
                  predecessor, so a value whose last real use precedes the
                  branch can look dead, have its register reused by the very
                  next instruction, and be read clobbered by the copy.{" "}
                  <code>compute_edge_uses()</code> carries these per-edge
                  instead, and <code>liveness_test</code> pins both halves: two
                  operands sharing a predecessor do interfere, because both are
                  needed at its end, while operands of different edges do not.
                </p>
              </div>
            </Note>
            <p>
              One older view has been deleted outright.{" "}
              <code>find_loops()</code> and <code>LoopSpan</code> approximated a
              loop by the contiguous flat instruction span{" "}
              <code>[header, latch]</code>, which is a superset when the body
              happens to be laid out contiguously and an <em>underset</em> when
              it is not — and the underset was the dangerous direction, because{" "}
              <code>extend_across_loops()</code> then failed to carry a value
              across the back edge past a body block the span missed, making a
              live value look dead. Phase 2.7 moved the allocator onto real CFG
              dataflow in the same change that removed those consumers, so the
              imprecise view no longer has any callers to be wrong for.
            </p>
          </DocSection>

          <DocSection id="owed" index="06" title="What Phase I still owes">
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
                    no test evidence yet
                  </span>,
                ],
                [
                  "AVX emitted",
                  <span key="6" className="text-destructive">
                    none · gating only
                  </span>,
                ],
              ]}
            />
            <p>
              The <code>Phi</code> emitter is the one that matters, and it
              relocates work rather than shrinking it. The liveness prerequisite
              has already landed in <code>compute_edge_uses()</code>. What is
              owed is value-kind inference for a float merge, an allocator slot
              for the result, and codegen for the join's parallel copies — and
              that last one is the same interference problem as phase 2.8, so it
              moves the coalescing question rather than answering it.
            </p>
            <p>
              A float merge is never promoted today, and for a structural reason
              rather than an oversight: a general-purpose register cannot hold a
              double, so those copies stay in memory under the same rule that
              governs every other float value. Closing it needs an XMM
              allocation pool.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              {SUBPAGES.map(([kicker, label, href]) => (
                <a
                  key={href}
                  href={href}
                  className="rounded-lg border p-4 hover:bg-muted"
                >
                  <span className="block text-xs text-muted-foreground">
                    {kicker}
                  </span>
                  <b className="block font-heading text-sm">{label}</b>
                </a>
              ))}
            </div>
          </DocSection>
        </div>
      </section>
    </>
  )
}
