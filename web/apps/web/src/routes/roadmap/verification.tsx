import { createFileRoute } from "@tanstack/react-router"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"

import {
  CodeBlock,
  DocSection,
  Note,
  PageHero,
  SpecTable,
  WRAP,
} from "../../site"

export const Route = createFileRoute("/roadmap/verification")({
  component: Verification,
})

const NAV = [
  ["layers", "Four layers"],
  ["proving", "Proving one pass"],
  ["benchmarks", "Benchmarks"],
  ["green", "What green does not prove"],
] as const

const TILES = [
  [
    "Layer 1 · ~0.1 s",
    "Unit tests. Build IR, call the pass, assert structure. No machine code is emitted.",
  ],
  [
    "Layer 2 · ~2 min",
    "The full gate: unit tests, encoder-vs-assembler, ABI audit, regressions, tier diff.",
  ],
  [
    "Layer 3 · ~5 min",
    "Differential fuzzing per shape, each mode aimed at passes the general generator never reaches.",
  ],
] as const

const SUBPAGES = [
  [
    "Read next",
    "Phase I · The dual-tier JIT engine",
    "/roadmap/phase-1-dual-tier",
  ],
  ["Back to", "The full tracker", "/roadmap"],
] as const

function Verification() {
  return (
    <>
      <PageHero
        kicker="Roadmap · Reference"
        title={
          <>
            Check it,
            <br />
            <em>don't believe it.</em>
          </>
        }
        lede="Every claim on this site is meant to be falsifiable. Here is the layered stack that makes it so, and the two places where a passing run can still mislead you."
        meta={["Four layers", "Per-shape fuzzing", "Measured, not asserted"]}
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
          <DocSection id="layers" title="Check it, don’t believe it.">
            <p>
              Every claim on this site is meant to be falsifiable, so the
              checking stack is layered outward from the cheapest test to the
              most expensive one. Each layer is roughly an order of magnitude
              slower than the one above it, which is what makes it reasonable to
              run all of them.
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

          <DocSection index="01" title="What each layer is actually good for">
            <p>
              The tests are not all the same kind, and knowing which is which
              saves a lot of misreading a green run.
            </p>
            <CodeBlock title="the layers, in order">{`$ cmake -B build -DCMAKE_BUILD_TYPE=Release
$ cmake --build build -j$(nproc)
$ ctest --test-dir build --output-on-failure     # 28/28

$ bash tools/verify_all.sh        # the whole gate
$ python3 tools/run_regression.py         # 15/15 untyped
$ python3 tools/run_typed_regression.py   # 13/13 typed
$ python3 tools/run_tier_diff.py          # 39/39, both tiers`}</CodeBlock>
            <p>
              <code>run_tier_diff.py</code> is the highest-value of those. It
              runs every program through <em>both</em> tiers and requires
              byte-identical stdout, and then reports which tier actually ran,
              so a green run cannot hide "everything silently fell back to the
              interpreter". That property is the reason <code>--strict</code>{" "}
              exists as a flag rather than as a habit.
            </p>
            <p>
              Within layer 1 the tests split again, and the distinction is worth
              keeping: analysis tests like <code>liveness_test</code> and{" "}
              <code>ssa_test</code> never emit a byte, so a green run there
              means the data structures are right rather than that any machine
              code ran. The tests that compile a module, map it and call it
              through a function pointer are the ones that would catch a bad
              encoding.
            </p>
          </DocSection>

          <DocSection index="02" title="Fuzzing has to aim itself">
            <p>
              The general generator annotates every variable{" "}
              <code>int[64]</code> and so emits no <code>const_f64</code> at
              all. Four general modes ran for a long time without approaching
              float code, and the float mode then found a real miscompile, an{" "}
              <code>Unknown</code>-kind operand lowered as <em>integer</em>{" "}
              arithmetic.
            </p>
            <p>
              Each mode below exists because the general one does not reach that
              shape. If you change one of these passes, the matching mode is the
              one to run.
            </p>
            <CodeBlock title="tools/fuzz_diff.py · per-shape modes">{`$ python3 tools/fuzz_diff.py --count 300             # general
$ python3 tools/fuzz_diff.py --count 300 --lsr        # strength reduction
$ python3 tools/fuzz_diff.py --count 300 --diamond   # diamond unroll
$ python3 tools/fuzz_diff.py --count 300 --floats    # int/float mixes
$ python3 tools/fuzz_diff.py --count 300 --bitwise   # & | ^ << >> + RCX hazard
$ python3 tools/fuzz_diff.py --mod --count 300        # modulo, any signs
$ python3 tools/fuzz_diff.py --mod-negatives --count 300
$ python3 tools/fuzz_diff.py --phi --count 300        # merges, with and without --ssa
$ python3 tools/fuzz_diff.py --accum --count 300      # accumulator unroll`}</CodeBlock>
            <p>
              <code>--phi</code> is the only mode whose programs <em>need</em> a
              merge to be correct, so it is the only one that exercises Mem2Reg,
              Phi placement and copy resolution end to end. It runs every
              program twice: plain <code>--auto</code> and{" "}
              <code>--auto --ssa</code>, and requires both to match the
              interpreter, which turns "the pipeline changed the answer" into a
              failure rather than a silent pass.
            </p>
            <p>
              Two caveats are honest enough to state. Because <code>%</code>{" "}
              truncates where CPython floors, most <code>--mod</code>{" "}
              disagreements with CPython are expected language gaps and are
              reported separately; the number that must stay at zero is the
              interpreter-versus-JIT mismatch count. And neither modulo mode
              generates infinities or NaNs, so the <code>0 * inf</code> class of
              bug needs the adversarial <code>run_tier_diff.py</code> case
              instead, that one <em>requires</em> the native tier, so a future
              guard change cannot quietly demote it to the interpreter and hide
              the bug.
            </p>
          </DocSection>

          <DocSection
            id="proving"
            index="03"
            title="Proving one optimization individually"
          >
            <p>
              Every optimization is toggleable, so its effect is a measurement
              rather than an assertion. This is how the numbers on the{" "}
              <a href="/roadmap/phase-1-dual-tier">Phase&nbsp;I page</a> were
              established.
            </p>
            <CodeBlock title="lithon_jit · one flag per pass">{`$ ./build/lithon_jit prog.ir --no-opt          # const fold + DCE
$ ./build/lithon_jit prog.ir --no-promote      # register promotion
$ ./build/lithon_jit prog.ir --no-rotate       # loop rotation
$ ./build/lithon_jit prog.ir --unroll=1        # all unrolling off
$ ./build/lithon_jit prog.ir --no-lsr          # strength reduction
$ ./build/lithon_jit prog.ir --accum-unroll    # opt in, reassociates FP
$ ./build/lithon_jit prog.ir --unroll-diamonds # opt in, measured slower`}</CodeBlock>
            <p>
              The disassembly check is the one that needs neither a timer nor a
              baseline, and it is often the most convincing: count the
              instructions the pass is supposed to remove, with the pass on and
              then off.
            </p>
            <CodeBlock title="did strength reduction fire?">{`$ objdump -D -b binary -mi386:x86-64 -M intel /tmp/n.bin | grep -c imul
0                        # fired: no multiplies left
$ objdump -D -b binary -mi386:x86-64 -M intel /tmp/n2.bin | grep -c imul
4                        # --no-lsr: proving the above was the pass`}</CodeBlock>
          </DocSection>

          <DocSection
            id="benchmarks"
            index="04"
            title="Benchmarks, and the two traps"
          >
            <p>
              The harness ships four official workloads plus three stress cases,
              reports a noise column, and warns that results are unreliable
              above roughly fifteen percent noise. Two mistakes make the output
              unusable, and both fail quietly rather than loudly.
            </p>
            <CodeBlock title="compare two states">{`$ python3 tools/native_bench.py --runs 30 --pin 2 --json /tmp/before.json
# ...change something, rebuild...
$ python3 tools/native_bench.py --runs 30 --pin 2 --compare /tmp/before.json`}</CodeBlock>
            <SpecTable
              rows={[
                [
                  "always pass --pin <cpu>",
                  <span key="1" className="text-destructive">
                    unpinned, one run showed 26% noise and the numbers were
                    unusable
                  </span>,
                ],
                [
                  "never --compare against benchmarks/results/*.json",
                  <span key="2" className="text-destructive">
                    different schema · the column comes out silently empty
                  </span>,
                ],
                ["read min, not median", "noise only ever adds time"],
                [
                  "differences under ~5%",
                  <span key="3" className="text-destructive">
                    not meaningful on a shared machine
                  </span>,
                ],
              ]}
            />
          </DocSection>

          <DocSection
            id="green"
            index="05"
            title="What a green run does not prove"
          >
            <p>
              Two limits are worth stating plainly, because a passing run can
              obscure both of them.
            </p>
            <Note title="Opcode coverage is not operand coverage">
              <div className="space-y-2">
                <p>
                  25 of 26 opcodes are emitted and each is exercised through{" "}
                  <code>tier_runner --strict</code>, which proves the opcode
                  really executed natively rather than fell back. It does not
                  prove every operand shape is right, that is what the encoder's
                  byte-exact assertions and the fuzz modes are for.{" "}
                  <code>gt</code> and <code>not</code> have a single native use
                  each in the checked-in IR corpus.
                </p>
                <p>
                  <code>Mod</code> is the standing example of why the two are
                  different: the general fuzzer only ever emits finite
                  constants, so it cannot generate <code>1.0 % inf</code>, which
                  was returning NaN natively while the interpreter was correct.
                </p>
              </div>
            </Note>
            <Note title="The interpreter is an oracle, not a specification">
              <p>
                Where Lithon and CPython disagree, the harness reports it
                separately as a language gap rather than a JIT bug: loop
                variables are one known case, deliberate. That is the right call
                for this project, and it also means the suite cannot catch a bug
                where both engines share the same wrong idea.
              </p>
            </Note>
            <div className="grid gap-3 sm:grid-cols-2">
              {SUBPAGES.map(([kicker, label, href]) => (
                <Card key={href} size="sm">
                  <CardContent>
                    <span className="block text-xs text-muted-foreground">
                      {kicker}
                    </span>
                    <Button
                      variant="link"
                      size="sm"
                      className="mt-1 h-auto px-0 font-heading"
                      render={<a href={href} />}
                    >
                      {label}
                    </Button>
                  </CardContent>
                </Card>
              ))}
            </div>
          </DocSection>
        </div>
      </section>
    </>
  )
}
