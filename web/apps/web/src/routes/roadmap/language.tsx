import type { ReactNode } from "react"
import { createFileRoute } from "@tanstack/react-router"

import {
  CodeBlock,
  DocSection,
  Note,
  PageHero,
  SpecTable,
  WRAP,
} from "../../site"

export const Route = createFileRoute("/roadmap/language")({
  component: LanguageReference,
})

type Rows = readonly (readonly [string, ReactNode])[]

const NAV = [
  ["types", "Types & flow"],
  ["subset", "The subset"],
  ["modulo", "Modulo"],
  ["shifts", "Shifts"],
  ["floats", "Float parity"],
  ["divergences", "Every divergence"],
] as const

const TYPES_ROWS: Rows = [
  ["signed integers", <code key="1">int[8] int[16] int[32] int[64]</code>],
  ["floating point", <code key="2">float[32] float[64]</code>],
  [
    "boolean",
    <span key="3">
      <code>bool</code> · takes no width
    </span>,
  ],
  [
    "unannotated binding",
    <span key="4" className="text-destructive">
      rejected
    </span>,
  ],
  ["widening", "automatic"],
  [
    "narrowing",
    <span key="5" className="text-destructive">
      never allowed
    </span>,
  ],
  ["int → float", "automatic"],
  [
    "float → int",
    <span key="6" className="text-destructive">
      no cast syntax exists
    </span>,
  ],
  [
    "bare integer literal",
    <span key="7">
      is <code>int[64]</code>
    </span>,
  ],
]

const SUBSET_ROWS: Rows = [
  ["assignment", "typed and untyped"],
  ["augmented assignment", <code key="1">+= -= *= /=</code>],
  ["arithmetic", <code key="2">+ - * / %</code>],
  [
    "bitwise",
    <span key="3">
      <code>&amp; | ^ &lt;&lt; &gt;&gt;</code> · integer only
    </span>,
  ],
  ["comparisons", "single, non-chained"],
  [
    "boolean operators",
    <span key="4">
      <code>and or not</code> · two operands
    </span>,
  ],
  ["control flow", <code key="5">if elif else while</code>],
  [
    "conditionals as values",
    <span key="6">
      <code>a if c else b</code> · nested
    </span>,
  ],
  [
    "loops",
    <span key="7">
      <code>for x in range(N)</code> · one argument
    </span>,
  ],
  ["functions", "recursion, self tail calls · max 2 arguments"],
  [
    "chained comparison",
    <span key="8" className="text-destructive">
      not supported
    </span>,
  ],
  [
    "for / else",
    <span key="9" className="text-destructive">
      rejected, never silently dropped
    </span>,
  ],
]

const MODULO_ROWS: Rows = [
  [
    "zero divisor",
    <span key="1">
      traps on both engines, with its own <code>modulo by zero</code> message
    </span>,
  ],
  [
    "INT64_MIN % -1",
    <span key="2">
      <code>0</code> · the one input that makes hardware <code>idiv</code> raise{" "}
      <code>#DE</code>, so the divisor is tested up front
    </span>,
  ],
  [
    "constant power-of-two divisor",
    "strength-reduced to a mask and a sign fixup",
  ],
  [
    "float Mod",
    <span key="3" className="text-destructive">
      no SSE2 instruction · computed as <code>a - n*b</code> for{" "}
      <code>n = trunc(a/b)</code>
    </span>,
  ],
]

const SHIFT_ROWS: Rows = [
  [
    "left shift overflow",
    "wraps to the low 64 bits · nothing is trapped, because there is no wider type to widen to",
  ],
  [
    "count outside 0..63",
    <span key="1" className="text-destructive">
      refused · compile time if literal, run time if not
    </span>,
  ],
  [
    "float operands",
    <span key="2" className="text-destructive">
      type error · there is no float bit pattern to reinterpret
    </span>,
  ],
  ["result width", "the left operand’s width"],
]

const FLOAT_ROWS: Rows = [
  [
    "division by zero",
    <span key="1">
      traps, matching Python’s <code>ZeroDivisionError</code> rather than IEEE{" "}
      <code>inf</code>/<code>nan</code>
    </span>,
  ],
  [
    "NaN divisor",
    <span key="2" className="text-destructive">
      must not trap · Python propagates
    </span>,
  ],
  [
    "-0.0 divisor",
    <span key="3">
      traps · it compares equal to <code>0.0</code>
    </span>,
  ],
  ["NaN comparison", "false against everything, including itself"],
  [
    "float live across a call",
    "spills · every XMM is caller-saved on both ABIs",
  ],
  [
    "variable stored both an int and a float",
    <span key="4" className="text-destructive">
      refused · the kind joins to <code>Unknown</code>
    </span>,
  ],
]

const DIVERGENCE_ROWS: Rows = [
  [
    "Modulo sign",
    <>
      sign of <code>dividend</code> vs sign of <code>divisor</code> · matches C;
      keeps <code>Mod</code> int-typed
    </>,
  ],
  [
    "-0.0 remainder",
    <>
      <code>0.0</code> vs <code>0.0</code> · so the float and integer paths
      agree with each other
    </>,
  ],
  [
    "Left shift past 63",
    <>wraps vs grows · no wider type exists to widen into</>,
  ],
  [
    "Shift count > 63",
    <>
      <span className="text-destructive">refused</span> vs computed · hardware
      would mask it and return a wrong answer
    </>,
  ],
  [
    "Loop variable after exit",
    <>
      <code>v == n</code> vs <code>n - 1</code> · documented; the tier diff
      reports it separately
    </>,
  ],
  [
    "Unbounded ints",
    <>
      <span className="text-destructive">do not exist</span> vs arbitrary
      precision · precision is the one guarantee a machine word cannot make
    </>,
  ],
]

function LanguageReference() {
  return (
    <>
      <PageHero
        kicker="Roadmap · Reference"
        title={
          <>
            Where Lithon
            <br />
            <em>parts ways.</em>
          </>
        }
        lede="A language that is a strict superset of CPython cannot skip boxing. This is the honest list of what was traded away to get static proof, and why each trade was worth it."
        meta={["Types and flow", "Modulo and shifts", "Float parity"]}
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
          <DocSection title="Where Lithon parts ways.">
            <p>
              A language that is a strict superset of CPython cannot skip
              boxing, because CPython’s semantics require it. Lithon makes a
              different promise instead — every variable has a proven, fixed
              type before execution begins — and that promise costs a few Python
              behaviours. This page is the honest list of what was traded away
              and why.
            </p>
          </DocSection>

          <DocSection id="types" index="01" title="Types and flow">
            <p>
              Every binding carries an explicit annotation and every numeric
              type carries an explicit width. A bare <code>int</code> is not a
              type in Lithon; the checker rejects it by name, which is the whole
              point — a type that cannot be written down cannot be proven.
            </p>
            <SpecTable rows={TYPES_ROWS} />
            <Note title="Why there is no float-to-int cast">
              A narrowing is refused everywhere else too, but a cast would be
              different in kind: it is a request to re-interpret a value the
              type system has already settled. Leaving it out means{" "}
              <code>int</code> and <code>float</code> stay separate worlds, and
              adding one later cannot silently change the meaning of code that
              was written without it.
            </Note>
          </DocSection>

          <DocSection id="subset" index="02" title="The supported subset">
            <p>
              Small on purpose, and the boundaries are worth knowing before you
              write anything against the native tier. Everything outside this
              set falls to the Tier-0 interpreter rather than failing to
              compile, which is why <code>--strict</code> exists.
            </p>
            <SpecTable rows={SUBSET_ROWS} />
          </DocSection>

          <DocSection id="modulo" index="03" title="% is C’s, not Python’s">
            <p>
              This is the one place Lithon deliberately does not follow Python,
              so it is worth stating plainly rather than leaving to a comment in
              a header. <code>%</code> truncates toward zero and takes the sign
              of the <strong>dividend</strong>. CPython floors instead, so its
              remainder has the sign of the <strong>divisor</strong>.
            </p>
            <CodeBlock title="the same program, two engines">{`print(-7 % 3)   # Lithon: -1     CPython: 2
print(7 % -3)   # Lithon:  1     CPython: -2`}</CodeBlock>
            <p>
              That is what C, Rust, Java and every other compiled language do.
              The reason is the one C gives: a remainder never leaves the domain
              of its operands, so <code>Mod</code> is typed like{" "}
              <code>Mul</code> — int if both operands are int — rather than like{" "}
              <code>Div</code>, which has to widen to float because a quotient
              generally is not an integer. Typing it as <code>Div</code> would
              make <code>7 % 3</code> a float and lose the point.
            </p>
            <p>
              Both engines here implement the truncating rule, which is what
              makes the tier diff a meaningful check rather than two engines
              agreeing on a shared mistake. The consequences are all covered by
              tests:
            </p>
            <SpecTable rows={MODULO_ROWS} />
            <Note title="The one float case that can go wrong">
              <p>
                Float modulo has no SSE2 instruction, so it is computed the way
                C defines it. The interesting input is <code>n == 0</code>,
                which happens exactly when <code>|a| &lt; |b|</code>, and it is
                the only case where the multiply is unsafe: IEEE makes{" "}
                <code>0 * inf</code> a NaN, but <code>n*b</code> is 0 for every{" "}
                <code>b</code> when <code>n</code> is 0, so C’s{" "}
                <code>fmod(1.0, inf)</code> is <code>1.0</code>. The guard that
                skips the multiply has to tell a real zero quotient from a NaN
                one using the same <code>ZF AND !PF</code> shape as the
                zero-divisor check, because <code>comisd</code> sets ZF for an
                unordered compare too.
              </p>
              <p className="mt-2">
                A zero remainder is normalized to <code>+0.0</code> to match
                CPython, which does not preserve the dividend’s sign. C’s{" "}
                <code>fmod(-4.0, 2.0)</code> is <code>-0.0</code>; Lithon prints{" "}
                <code>0.0</code>. That is a conscious divergence, chosen so the
                float and integer paths agree with each other rather than each
                matching a different authority.
              </p>
            </Note>
          </DocSection>

          <DocSection
            id="shifts"
            index="04"
            title="Shifts are 64-bit, and the count is checked"
          >
            <p>
              <code>&amp;</code>, <code>|</code>, <code>^</code>,{" "}
              <code>&lt;&lt;</code> and <code>&gt;&gt;</code> are integer-only,
              and each is a plain 64-bit machine operation.{" "}
              <code>&gt;&gt;</code> is arithmetic (<code>sar</code>), so{" "}
              <code>-8 &gt;&gt; 1</code> is <code>-4</code>.
            </p>
            <p>
              Three rules follow from the machine word being 64 bits wide, and
              all three diverge from CPython, whose ints are unbounded.
            </p>
            <CodeBlock title="where the word ends">{`print(5 << 63)   # Lithon: -9223372036854775808
                          # CPython: 46116860184273879040
print(5 << 62)   # Lithon:  4611686018427387904
                          # CPython: 4611686018427387904
print(1 << 64)   # Lithon: RCR error
                          # CPython: 18446744073709551616`}</CodeBlock>
            <SpecTable rows={SHIFT_ROWS} />
            <p>
              Refusing a bad shift count is the one place where x86 actively
              works against you: the hardware masks the count to its low six
              bits, so <code>1 &lt;&lt; 64</code> would execute as a shift by
              zero and return <code>1</code>, and <code>1 &lt;&lt; -1</code> as
              a shift by 63. Lithon raises an error rather than returning the
              wrong answer.
            </p>
            <p>
              Which encoding gets used depends on whether the count is known at
              compile time, and that is only visible in the generated code —
              which is exactly the sort of claim the engine makes checkable:
            </p>
            <CodeBlock title="--dump-hex, then objdump">{`shl rax,0x3      # 48 c1 e0 03   literal count 3, never touches CL
shl rax,1        # 48 d1 e0      literal count 1, the 2-byte short form
shl rax,cl       # 48 d3 e0      dynamic count
sar rax,cl       # 48 d3 f8      dynamic count`}</CodeBlock>
          </DocSection>

          <DocSection
            id="floats"
            index="05"
            title="Float, and what “identical” had to mean"
          >
            <p>
              <code>float</code> is implemented end to end: constants, load and
              store, the five arithmetic operations, the three comparisons, and
              native <code>print</code>. The hard part was never the arithmetic
              — SSE2 is straightforward once the encoding is right — it was
              making the two engines agree <em>byte for byte</em>, since that is
              the property everything else is measured against.
            </p>
            <Note title="Formatting is one function, called by both">
              CPython’s rule is the shortest string that round-trips, which
              rules out both <code>%f</code>
              (prints <code>3.500000</code>) and <code>%.17g</code> (prints{" "}
              <code>0.10000000000000001</code>). <code>%.*g</code> is wrong in a
              way that is easy to miss: it picks exponent notation based on the
              precision it happened to need, whereas CPython’s threshold is
              absolute — decimal exponent below −4 or above 16. That is why{" "}
              <code>924966630.0</code> must print in full, not as{" "}
              <code>9.2499663e+08</code>.
            </Note>
            <p>The rules that took the most care, each pinned by a test:</p>
            <SpecTable rows={FLOAT_ROWS} />
            <p>
              The last row is the one worth understanding. If a variable’s kind
              joins to <code>Unknown</code> and codegen only ever asks{" "}
              <code>is_float_value</code>, the arithmetic lowers as{" "}
              <em>integer</em> operations over a double’s bit pattern. Printing
              the resulting <code>bool</code> hides the bug completely, because
              a comparison is always <code>bool</code> and so always passes the
              print check. The guard is what turns a silent wrong answer into a
              refusal.
            </p>
            <p>
              The <code>ZF AND !PF</code> shape recurs throughout, and it is not
              incidental: <code>comisd</code> sets ZF, PF and CF together when
              the operands are unordered, so ZF alone cannot separate “equal”
              from “NaN”. The same problem is why NaN comparisons are excluded
              with <code>setcc</code> plus <code>AND setnp</code> rather than a
              parity branch — <code>0F 9A</code> is a byte-for-byte collision
              between <code>jp rel32</code> and <code>setp r/m8</code>, so a
              parity <code>Jcc</code> is simply not encodable here.
            </p>
          </DocSection>

          <DocSection
            id="divergences"
            index="06"
            title="Every deliberate divergence"
          >
            <p>
              Collected in one place, because a divergence discovered by
              surprise is worse than one documented in advance. The last row is
              a bug rather than a choice, and is listed so it is not mistaken
              for policy.
            </p>
            <SpecTable caption="Lithon vs CPython" rows={DIVERGENCE_ROWS} />
            <Note title="The interpreter is an oracle, not a specification">
              Where Lithon and CPython disagree, the tier-diff harness reports
              it as a language gap rather than a JIT bug, which is correct for
              this project and also means the suite cannot catch a bug where
              both engines share the same wrong idea. The loop-variable row
              above is the known case.
            </Note>
            <div className="flex flex-wrap justify-between gap-4 rounded-lg border p-4">
              <a href="/roadmap/verification">
                <span className="block text-xs text-muted-foreground">
                  Read next
                </span>
                <b className="font-heading">Verification and benchmarks</b>
              </a>
              <a href="/roadmap" className="text-end">
                <span className="block text-xs text-muted-foreground">
                  Back to
                </span>
                <b className="font-heading">The full tracker</b>
              </a>
            </div>
          </DocSection>
        </div>
      </section>
    </>
  )
}
