import { createFileRoute } from "@tanstack/react-router"

import { Card, CardContent } from "@workspace/ui/components/card"

import { DocSection, Note, PageHero, SpecTable, WRAP } from "../../site"

export const Route = createFileRoute("/roadmap/phase-2-aot")({
  component: Phase2Aot,
})

const NAV = [
  ["goal", "What ships"],
  ["headers", "Headers by hand"],
  ["budget", "The 10 KB budget"],
  ["decoupling", "Stopping the callbacks"],
  ["sequence", "Why this waits"],
] as const

const GOAL_SPEC = [
  [
    "input",
    <>
      a <code>.py</code> file
    </>,
  ],
  ["output", "a native executable"],
  ["targets", "ELF64 (Linux) · PE32+ (Windows)"],
  ["size budget", "under 10 KB"],
  [
    "needs the engine at run time",
    <span className="text-destructive">never</span>,
  ],
] as const

const CALLBACK_SPEC = [
  ["div by zero", "already a compare and a branch"],
  ["shift count check", "already emitted, compile-time when constant"],
  [
    "float formatting",
    <span className="text-destructive">
      host call · must move into the image
    </span>,
  ],
  [
    "symbol resolution",
    <span className="text-destructive">host call · must become static</span>,
  ],
] as const

const TILES = [
  [
    "Fits",
    "Integer formatting, simple output, arithmetic, control flow — all of it already emits.",
  ],
  [
    "Must move",
    "The shared double formatter, which today lives in host code called by both tiers.",
  ],
  [
    "Will not fit",
    "A hosted C runtime. Every syscall it would have made becomes Phase III work instead.",
  ],
] as const

function Phase2Aot() {
  return (
    <>
      <PageHero
        kicker="Roadmap · Phase II"
        title={
          <>
            From a process
            <br />
            <em>to a file.</em>
          </>
        }
        lede="What it takes to compile a Lithon program into a native executable that runs on a machine with no Lithon installed and nothing beside it."
        meta={["Planned", "ELF64 and PE32+", "Under 10 KB"]}
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
          <DocSection id="goal" title="From a process to a file.">
            <p>
              Everything in Phase I runs in-process: the emitter writes bytes,
              the engine maps them executable, and the host calls in. That is a
              JIT, and a JIT has a floor — the engine has to be there. Phase II
              removes the floor by emitting the container as well as the code.
            </p>
            <p>
              The target is deliberately unambitious in the way that matters: a
              Lithon program compiles to a native executable, <code>.bin</code>{" "}
              or <code>.exe</code>, that runs on a machine with no Lithon
              installed and nothing beside it. Not a bundle. Not a runtime. One
              file.
            </p>
            <SpecTable rows={GOAL_SPEC} />
          </DocSection>

          <DocSection id="headers" index="01" title="Headers written by hand">
            <p>
              There is no system linker in this plan. The object file is
              synthesized directly: the ELF64 header and its program headers on
              Linux, the DOS stub, PE signature and section table on Windows,
              then the emitted machine code appended into a loadable segment
              with an entry point.
            </p>
            <p>
              This is the part of the project most like the encoder itself, and
              for the same reason: it is a byte format, so it can be got exactly
              right and then checked exactly. A header is also unforgiving in a
              way source code is not — a field that is merely unusual loads, and
              a field that is wrong does not.
            </p>
            <Note title="What has to be true first">
              A loadable image is only as stable as the code inside it. Before a
              binary can be written out, the emitter has to produce the same
              bytes for the same IR on both ABIs — which is exactly the gap the
              Win64 audit has to close first.
            </Note>
          </DocSection>

          <DocSection id="budget" index="02" title="The 10 KB budget">
            <p>
              Under 10 KB is a constraint that decides the design rather than a
              target checked at the end. A statically linked libc will not fit
              in it, which means no <code>printf</code>, no <code>malloc</code>,
              and no <code>snprintf</code> — so anything the program prints has
              to be formatted by emitted code.
            </p>
            <p>
              That is not as frightening as it sounds, because most of what a
              Lithon program needs is already written in the engine. Float
              formatting is the honest example:{" "}
              <code>host_format_double()</code> already reproduces CPython's
              shortest-round-trip rule, and the version that would have to move
              into the binary is a translation rather than a redesign. Integer
              formatting is easier still.
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

          <DocSection id="decoupling" index="03" title="Stopping the callbacks">
            <p>
              The harder half of decoupling is not the headers. It is every
              place the emitted code currently calls back into the engine to get
              something done — formatting a float, trapping a division by zero,
              refusing an out-of-range shift. Each of those is a call into a
              symbol the binary will not have.
            </p>
            <p>
              So each one has to become either emitted code or a trap
              instruction in the image. Division by zero is the clean case,
              because it already lowers to a compare and a branch rather than a
              libm call. Float formatting is the expensive case, because it is
              the one piece of real logic the runtime currently provides.
            </p>
            <SpecTable rows={CALLBACK_SPEC} />
          </DocSection>

          <DocSection id="sequence" index="04" title="Why this waits">
            <p>
              Phase I is still moving, and Phase II inherits everything it has
              not finished. Two items in particular are on the critical path:
              the Win64 ABI has no test evidence yet, and the <code>Phi</code>{" "}
              emitter is owed.
            </p>
            <p>
              Neither blocks writing a header, but both mean the emitted image
              would still change shape — and an image format is much harder to
              revise once binaries exist in the wild. Sequencing Phase II after
              Phase I closes is cheaper than versioning a container format.
            </p>
            <div className="flex flex-wrap gap-6 border-t pt-4">
              <a href="/roadmap/phase-3-systems" className="text-sm">
                <span className="block text-xs text-muted-foreground">
                  Next in the roadmap
                </span>
                <b>Phase III · Systems and SIMD</b>
              </a>
              <a href="/roadmap" className="text-sm">
                <span className="block text-xs text-muted-foreground">
                  Back to
                </span>
                <b>The full tracker</b>
              </a>
            </div>
          </DocSection>
        </div>
      </section>
    </>
  )
}
