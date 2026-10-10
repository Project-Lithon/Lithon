import { useState } from "react"
import { createFileRoute } from "@tanstack/react-router"

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@workspace/ui/components/accordion"
import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@workspace/ui/components/card"
import { Separator } from "@workspace/ui/components/separator"

import { Note, PageHero, SectionHead, WRAP } from "../site"

export const Route = createFileRoute("/roadmap")({ component: Roadmap })

type Status = "done" | "active" | "next" | "later"

const STATUS_LABEL: Record<Status, string> = {
  done: "Shipped",
  active: "In progress",
  next: "Next",
  later: "Planned",
}

const STATUS_VARIANT: Record<
  Status,
  "default" | "secondary" | "outline" | "ghost"
> = {
  done: "secondary",
  active: "default",
  next: "outline",
  later: "ghost",
}

const FILTERS = [
  ["all", "All"],
  ["done", "Shipped"],
  ["active", "In progress"],
  ["next", "Next"],
  ["later", "Planned"],
] as const satisfies readonly (readonly [Status | "all", string])[]

interface Milestone {
  t: string
  s: Status
  d: string
  e: string
}

const PHASES: readonly {
  id: string
  label: string
  title: string
  state: string
  items: readonly Milestone[]
}[] = [
  {
    id: "phase-1",
    label: "Phase I",
    title: "Dual-tier JIT engine",
    state: "Active now",
    items: [
      {
        t: "Hand-rolled x86-64 encoder",
        s: "done",
        d: "Machine code assembled byte by byte in C++20. No LLVM, no Cranelift, no assembler — and every encoding proved against GNU as rather than trusted, because an encoder that is subtly wrong still produces plausible-looking disassembly.",
        e: "encoder_test · branch_test · stack_test · tools/check_encoder_vs_as.py",
      },
      {
        t: "SysV x64 calling convention",
        s: "done",
        d: "Callee-saved registers and 16-byte stack alignment proven at every call and return by disassembling each emitted function, rather than by reading the emitter and hoping.",
        e: "src/jit/jit_abi.h · tools/check_stack_alignment.py --self-test",
      },
      {
        t: "Windows x64 calling convention",
        s: "active",
        d: "The Win64 path is written and compiles, but the audit only exercises the host ABI — so on a Linux CI machine nothing has actually verified it. It stays in progress until a Windows host runs the same disassembly.",
        e: "#if defined(_WIN32) in src/jit/jit_abi.h · no host-side test evidence yet",
      },
      {
        t: "CPU target-feature detection",
        s: "done",
        d: "CPUID parsed and cross-checked against the kernel, so the AVX and AVX-512 gates cannot silently mis-detect and emit an instruction the host does not have.",
        e: "cpu_features_test (30 assertions) · tools/check_cpu_features.py",
      },
      {
        t: "VEX/SSE transition discipline",
        s: "done",
        d: "A scanner that disassembles each function and flags legacy SSE after a VEX prefix with no vzeroupper. No AVX is emitted yet, so this is a guardrail laid before the road — and its --self-test mode proves the scanner actually fires.",
        e: "tools/check_vex_transitions.py --self-test proves the scanner fires",
      },
      {
        t: "Tier-0 interpreter fallback",
        s: "done",
        d: "A block whose types cannot be established runs on the C++ interpreter instead of guessing, and --strict converts that fallback into a refusal. A clean exit under --strict is proof the bytes were emitted and ran.",
        e: "tier_runner --auto · src/jit/print_guard.h",
      },
      {
        t: "Static type flow verifier",
        s: "done",
        d: "Mandatory annotations, explicit integer widths, automatic widening and no narrowing anywhere. A bare int is not a type here: the checker rejects it by name, because a type that cannot be written down cannot be proven.",
        e: "tools/typecheck.py · run_typed_regression.py 13/13",
      },
      {
        t: "IR text format",
        s: "done",
        d: "The intermediate representation is parsed inside the engine, so the native tier carries no Python dependency of any kind. CPython stops existing once IR exists.",
        e: "src/ir/text_parser.cpp · run_regression.py 15/15",
      },
      {
        t: "Calls, recursion and tail calls",
        s: "done",
        d: "Cross-function calls work, and a self tail-call becomes a loop — so fib recurses at O(1) stack rather than growing a frame per call.",
        e: "compile_module_call_test · fib_test",
      },
      {
        t: "Bitwise operators and shifts",
        s: "done",
        d: "& | ^ << >>, integer-only and 64-bit, with a shift count outside 0..63 refused instead of masked into a wrong answer by the hardware.",
        e: "check_encoder_vs_as.py · typecheck_test · fuzz_diff --bitwise",
      },
      {
        t: "Floating point, end to end",
        s: "done",
        d: "SSE2 arithmetic, comparisons, load and store, and native print. One formatter serves both tiers, so the two agree byte for byte rather than approximately.",
        e: "compile_module_float_test · float_format_test · run_tier_diff.py 39/39",
      },
      {
        t: "Modulo with C semantics",
        s: "done",
        d: "% truncates toward zero and takes the sign of the dividend. This is the one deliberate divergence from CPython, and both engines implement it, which is what makes the tier diff a real check.",
        e: "fuzz_diff --mod --mod-negatives · adversarial 1.0 % inf",
      },
      {
        t: "SSA pipeline, phases 2.1 to 2.8",
        s: "done",
        d: "Control-flow graph and dominators, iterated dominance frontier Phi placement, Mem2Reg, copy propagation, CFG liveness, interference-graph allocation, and register coalescing so a merge can adopt a dead source's register.",
        e: "loop_info_test · ssa_test · mem2reg_test · regalloc_test · phi_register_test",
      },
      {
        t: "A direct Phi emitter",
        s: "active",
        d: "25 of 26 IR opcodes are emitted; this is the last one. What is owed is value-kind inference for a float merge, an allocator slot for the result, and codegen for the join's parallel copies.",
        e: "compute_edge_uses() in liveness.h · resolve_phis() in optimize.h",
      },
      {
        t: "Lift the two-argument cap",
        s: "active",
        d: "Functions and calls are capped at two arguments, and most of the remaining test programs are blocked waiting on this rather than on anything deeper in the compiler.",
        e: "src/jit/jit_abi.h · kTempPool in register_alloc.h",
      },
    ],
  },
  {
    id: "phase-2",
    label: "Phase II",
    title: "AOT binary synthesis",
    state: "Next",
    items: [
      {
        t: "ELF64 and PE32+ header synthesis",
        s: "next",
        d: "Write the object headers directly — program headers, section table, entry point — instead of linking against an external toolchain. A header is a byte format, so it can be got exactly right and then checked exactly.",
        e: "depends on a byte-stable emitter across both host ABIs",
      },
      {
        t: "Standalone executables under 10 KB",
        s: "next",
        d: "A .py file compiled into a native binary small enough to email, with no interpreter and no runtime beside it. The budget decides the design rather than being checked at the end.",
        e: "size budget is the real constraint, not the feature list",
      },
      {
        t: "Decoupling from the engine",
        s: "next",
        d: "The emitted binary must not need the Lithon interpreter to run, which means every place emitted code currently calls back into the engine has to become either emitted code or a trap instruction in the image.",
        e: "touches the shared float formatter first",
      },
    ],
  },
  {
    id: "phase-3",
    label: "Phase III",
    title: "Systems and SIMD",
    state: "Planned",
    items: [
      {
        t: "Direct syscall emission",
        s: "later",
        d: "The 0F 05 instruction written straight from source syntax, so a Lithon program can talk to the kernel without a libc in between. It is also the largest behavioural step in the roadmap.",
        e: "needs the emitter to stop assuming a hosted C entry point",
      },
      {
        t: "Zero-copy C pointers",
        s: "later",
        d: "Expose a raw pointer to a native array and let Lithon mutate it in place, with no copy on the way in or out. An operation that would write through it to something narrower is refused at compile time.",
        e: "depends on Phase II decoupling landing first",
      },
      {
        t: "AVX2 and SIMD vectorization",
        s: "later",
        d: "Parallel list processing across a vector unit. The detection gate and the VEX transition guardrail are already in place underneath it; no AVX is emitted yet.",
        e: "cpu_features.h gates it · check_vex_transitions.py keeps it honest",
      },
      {
        t: "An ARM64 backend",
        s: "later",
        d: "The genuinely architecture-neutral layers already exist: ir/, liveness.h and optimize.h name no registers at all. What is missing is a second encoder, which makes this a reach question rather than a design one.",
        e: "register_alloc.h only names registers via abi::kPromotionPool",
      },
    ],
  },
]

const PHASE_CARDS = [
  {
    state: "Active now",
    title: "Dual-tier JIT",
    text: "A hand-rolled x86-64 emitter, a static flow verifier that settles every type before execution, and a deterministic Tier-0 interpreter underneath.",
    href: "/roadmap/phase-1-dual-tier",
    cta: "Inside phase I",
  },
  {
    state: "Next",
    title: "AOT binaries",
    text: "Synthesize ELF64 and PE32+ headers directly and emit standalone executables that do not need the engine to run.",
    href: "/roadmap/phase-2-aot",
    cta: "Inside phase II",
  },
  {
    state: "Planned",
    title: "Systems & SIMD",
    text: "Emit syscalls straight from syntax, expose C pointers without a copy, and vectorize list processing with AVX2.",
    href: "/roadmap/phase-3-systems",
    cta: "Inside phase III",
  },
] as const

const DEEP_DIVES = [
  {
    scope: "Phase I",
    title: "The dual-tier JIT engine",
    text: "The two execution tiers, the emitter, ABI and target-feature gating, the measured optimization pipeline, and all eight SSA phases with the reasoning behind each.",
    href: "/roadmap/phase-1-dual-tier",
  },
  {
    scope: "Phase II",
    title: "AOT binary synthesis",
    text: "Writing ELF64 and PE32+ headers by hand, staying under 10 KB, and the decoupling work that has to happen before a binary can stand on its own.",
    href: "/roadmap/phase-2-aot",
  },
  {
    scope: "Phase III",
    title: "Systems and SIMD",
    text: "Syscalls emitted from syntax, zero-copy pointers into native arrays, and the AVX2 path with the feature gating already built underneath it.",
    href: "/roadmap/phase-3-systems",
  },
  {
    scope: "Reference",
    title: "Language and semantics",
    text: "Types and flow, the supported subset, and every place Lithon deliberately does not follow CPython — modulo, shifts, and the float rules.",
    href: "/roadmap/language",
  },
  {
    scope: "Reference",
    title: "Verification and benchmarks",
    text: "The four checking layers from unit tests to differential fuzzing, how to isolate one optimization's effect, and what a green run does not prove.",
    href: "/roadmap/verification",
  },
] as const

function Roadmap() {
  const [filter, setFilter] = useState<Status | "all">("all")

  return (
    <>
      <PageHero
        kicker="03 / Roadmap"
        title={
          <>
            A JIT that refuses.
            <br />A compiler that ships.
          </>
        }
        lede="Where Lithon stands, what is being built now, and what each phase has to prove before the next one starts. Nothing on this page is marked shipped without a test that fails when it regresses."
        meta={["Phase I · active", "Phase II · next", "Phase III · planned"]}
      />

      <section className={`${WRAP} py-14`}>
        <SectionHead
          kicker="The three phases"
          title={
            <>
              One core.
              <br />
              <span className="text-muted-foreground">Three horizons.</span>
            </>
          }
          lede="The phases are a dependency chain, not three parallel tracks. Phase II cannot start until the emitter produces the same bytes everywhere, and Phase III cannot start until the emitter can be pointed at hardware."
        />
        <div className="grid gap-4 md:grid-cols-3">
          {PHASE_CARDS.map((phase) => (
            <Card key={phase.title}>
              <CardHeader>
                <Badge
                  variant={phase.state === "Active now" ? "default" : "outline"}
                  className="w-fit"
                >
                  {phase.state}
                </Badge>
                <CardTitle className="mt-2 text-lg font-bold">
                  {phase.title}
                </CardTitle>
              </CardHeader>
              <CardContent className="text-muted-foreground">
                {phase.text}
                <Button
                  variant="link"
                  size="sm"
                  className="mt-3 px-0"
                  render={<a href={phase.href} />}
                >
                  {phase.cta} →
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section className="border-y bg-card py-14">
        <div className={WRAP}>
          <SectionHead
            kicker="Milestones"
            title={
              <>
                Every item,
                <br />
                <span className="text-muted-foreground">and its proof.</span>
              </>
            }
            lede="Grouped by phase, one open at a time. Each milestone says what it covers and the test that holds it in place — if that test were deleted, the row would stop being true."
          />

          <div
            className="mb-6 flex flex-wrap gap-2"
            role="group"
            aria-label="Filter milestones by status"
          >
            {FILTERS.map(([value, label]) => (
              <Button
                key={value}
                size="sm"
                variant={filter === value ? "default" : "outline"}
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
              >
                {label}
              </Button>
            ))}
          </div>

          <Accordion defaultValue={["phase-1"]}>
            {PHASES.map((phase) => {
              const items = phase.items.filter(
                (item) => filter === "all" || item.s === filter
              )
              return (
                <AccordionItem key={phase.id} value={phase.id}>
                  <AccordionTrigger>
                    <span className="flex flex-wrap items-center gap-3">
                      <Badge variant="outline" className="font-mono">
                        {phase.label}
                      </Badge>
                      <b className="font-heading text-base">{phase.title}</b>
                      <Badge variant="secondary">{phase.state}</Badge>
                      <span className="text-xs text-muted-foreground">
                        {items.length} items
                      </span>
                    </span>
                  </AccordionTrigger>
                  <AccordionContent>
                    {items.length === 0 ? (
                      <p className="text-muted-foreground">
                        No milestones match this filter.
                      </p>
                    ) : (
                      <ul className="space-y-3">
                        {items.map((item) => (
                          <li
                            key={item.t}
                            className="rounded-lg border bg-background p-4"
                          >
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <b className="font-heading text-sm">{item.t}</b>
                              <Badge variant={STATUS_VARIANT[item.s]}>
                                {STATUS_LABEL[item.s]}
                              </Badge>
                            </div>
                            <p className="mt-2 text-sm text-muted-foreground">
                              {item.d}
                            </p>
                            <p className="mt-2 font-mono text-xs text-accent-strong">
                              {item.e}
                            </p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </AccordionContent>
                </AccordionItem>
              )
            })}
          </Accordion>
        </div>
      </section>

      <section className={`${WRAP} py-14`}>
        <SectionHead
          kicker="Deep dives"
          title={
            <>
              Each phase,
              <br />
              <span className="text-muted-foreground">in detail.</span>
            </>
          }
          lede="The tracker says what is planned. These pages say why, what it costs, and what would make it wrong — including the parts that are measured rather than promised."
        />
        <div className="rounded-lg border">
          {DEEP_DIVES.map((row, index) => (
            <div key={row.href}>
              {index > 0 && <Separator />}
              <a
                href={row.href}
                className="flex items-center gap-4 px-4 py-4 hover:bg-muted"
              >
                <Badge variant="outline" className="w-24 justify-center">
                  {row.scope}
                </Badge>
                <span className="min-w-0">
                  <b className="block font-heading text-sm">{row.title}</b>
                  <span className="block text-sm text-muted-foreground">
                    {row.text}
                  </span>
                </span>
                <span className="ms-auto text-muted-foreground">→</span>
              </a>
            </div>
          ))}
        </div>

        <Note title="What counts as shipped here">
          A feature is marked shipped when a test exists that fails if the
          feature regresses, not when the code is present. That is why every
          milestone carries evidence, and why two entries are marked in progress
          despite working: the Win64 ABI has no test evidence yet, and Phi is
          emitted by lowering rather than by a code path of its own. It is also
          why there is no percentage on this page — a number that cannot be
          derived from a test is a decoration, and this tracker would rather
          show you the tests.
        </Note>
      </section>
    </>
  )
}
