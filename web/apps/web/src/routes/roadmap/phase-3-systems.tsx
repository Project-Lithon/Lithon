import type { ReactNode } from "react"
import { createFileRoute } from "@tanstack/react-router"

import { DocSection, Note, PageHero, SpecTable, WRAP } from "../../site"

export const Route = createFileRoute("/roadmap/phase-3-systems")({
  component: Phase3Systems,
})

type Row = readonly [string, ReactNode]

const NAV = [
  ["syscalls", "Syscalls from syntax"],
  ["ffi", "Zero-copy pointers"],
  ["simd", "AVX2 and SIMD"],
  ["arm64", "ARM64 and portability"],
] as const

const SYSCALLS: readonly Row[] = [
  [
    "mechanism",
    <span key="1">
      <code>syscall</code> · <code>0F 05</code>
    </span>,
  ],
  [
    "calling convention",
    <span key="2" className="text-destructive">
      SysV and Win64 each define their own
    </span>,
  ],
  [
    "libc dependency",
    <span key="3" className="text-destructive">
      none · the point of the exercise
    </span>,
  ],
  ["depends on", "Phase II decoupling landing first"],
]

const GROUNDWORK: readonly Row[] = [
  [
    "CPUID detection",
    <span key="1">
      Feature bits agree with the kernel’s own report, across 30 assertions ·{" "}
      <b>shipped</b>
    </span>,
  ],
  [
    "AVX / AVX-512 gate",
    <span key="2">
      <code>usable_avx</code> and <code>usable_avx512</code> cannot mis-detect ·{" "}
      <b>shipped</b>
    </span>,
  ],
  [
    "VEX transition scanner",
    <span key="3">
      Legacy SSE after VEX without <code>vzeroupper</code> is caught by
      disassembly · <b>shipped</b>
    </span>,
  ],
  [
    "AVX emission",
    <span key="4" className="text-destructive">
      , · not started
    </span>,
  ],
]

const PORTABILITY: readonly Row[] = [
  [
    "portable",
    <span key="1">
      <code>ir/</code> · <code>liveness.h</code> · <code>optimize.h</code>
    </span>,
  ],
  [
    "one seam",
    <span key="2">
      <code>register_alloc.h</code> via <code>abi::kPromotionPool</code>
    </span>,
  ],
  [
    "x86-specific",
    <span key="3">
      <code>x86_encoder.h</code> · emit calls in <code>compile_function.h</code>
    </span>,
  ],
  [
    "partly shared",
    <span key="4">
      <code>float_runtime.h</code> · formatter yes, SSE arithmetic no
    </span>,
  ],
]

function Phase3Systems() {
  return (
    <>
      <PageHero
        kicker="Roadmap · Phase III"
        title={
          <>
            The machine,
            <br />
            <em>not just the process.</em>
          </>
        }
        lede="Removing the hosted-process assumption in both directions at once: downward to the kernel through raw syscalls, and outward to memory that somebody else allocated."
        meta={["Planned", "Raw syscalls", "AVX2"]}
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
          <DocSection title="The machine, not just the process.">
            <p>
              Phases I and II both assume a hosted process: there is a C
              runtime, there is a libc, and the program asks it to do things.
              Phase III removes that assumption in both directions at once,
              downward to the kernel, and outward to memory somebody else owns.
            </p>
            <p>
              The through-line is that a Lithon value should be able to live
              where the hardware already keeps it, rather than being copied into
              whatever shape the emitter knows how to produce.
            </p>
          </DocSection>

          <DocSection
            id="syscalls"
            index="01"
            title="Syscalls straight from syntax"
          >
            <p>
              The <code>0F 05</code> instruction emitted directly, so a program
              can reach the kernel with no libc in between. That is also what
              makes a sub-10&nbsp;KB binary possible at all: every service a
              hosted program would have got from <code>libc</code> has to be
              written by hand instead, and the shortest honest way to write them
              is at the syscall boundary.
            </p>
            <p>
              It is the largest behavioural step in the roadmap too. Today the
              emitter assumes a hosted C entry point with a stack frame and a
              return address; a freestanding image has neither by default, so
              this is where the calling-convention work stops being an ABI
              detail and becomes the program’s own entry contract.
            </p>
            <SpecTable rows={SYSCALLS} />
          </DocSection>

          <DocSection id="ffi" index="02" title="Zero-copy C pointers">
            <p>
              A raw pointer to a native array, exposed to Lithon and mutated in
              place. No copy on the way in, no copy on the way out, and no
              pretence of ownership: the caller keeps the allocation and Lithon
              holds a typed view of it.
            </p>
            <p>
              It waits for Phase II because a pointer into somebody else’s
              memory only means something once there is a binary to hold it.
              In-process, the same capability would just be a way to reach
              engine internals, which is a considerably worse thing to expose.
            </p>
            <Note title="The type checker still gets a vote">
              <p>
                A pointer is a type like any other. It carries an element type
                and a width, it cannot be narrowed, and an operation that would
                write through it to something narrower than the pointed-to type
                is refused at compile time rather than trusted at run time.
                Zero-copy is about not copying bytes; it is not a waiver.
              </p>
            </Note>
          </DocSection>

          <DocSection id="simd" index="03" title="AVX2 and vectorized lists">
            <p>
              Parallel list processing across a vector unit, with the feature
              gate deciding at emit time whether the host can run what is about
              to be written. This would be the first phase to emit AVX, which is
              why the groundwork underneath it is worth naming: almost all of it
              is already shipped.
            </p>
            <SpecTable
              caption="Groundwork already landed for Phase III"
              rows={GROUNDWORK}
            />
            <p>
              The VEX scanner is the interesting one, because of its{" "}
              <code>--self-test</code> mode. That flag proves the scanner
              actually fires, which is the difference between a guardrail and a
              decoration: a check that has never been observed to fail is not
              evidence that the code is correct, only that nobody has broken it
              yet.
            </p>
          </DocSection>

          <DocSection
            id="arm64"
            index="04"
            title="ARM64 and what is already portable"
          >
            <p>
              A second architecture is not a rewrite, because the layers
              carrying the actual reasoning name no registers at all. The
              portable core is the IR, liveness and the optimizer; the register
              allocator names hardware only through one promotion pool.
            </p>
            <SpecTable rows={PORTABILITY} />
            <p>
              ARM64 sits under Phase III because it is a reach question rather
              than a design one: the architecture independence is already in the
              code, and what is missing is a second encoder.
            </p>
            <div className="flex flex-wrap gap-10 border-t pt-6">
              <a href="/roadmap/language">
                <span className="block text-xs text-muted-foreground">
                  Read next
                </span>
                Language and semantics →
              </a>
              <a href="/roadmap">
                <span className="block text-xs text-muted-foreground">
                  Back to
                </span>
                The full tracker →
              </a>
            </div>
          </DocSection>
        </div>
      </section>
    </>
  )
}
