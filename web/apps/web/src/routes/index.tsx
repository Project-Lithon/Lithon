import { useEffect, useState } from "react"
import { createFileRoute } from "@tanstack/react-router"
import { ArrowUpRightIcon } from "@phosphor-icons/react"

import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@workspace/ui/components/alert"
import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@workspace/ui/components/card"
import { Separator } from "@workspace/ui/components/separator"

export const Route = createFileRoute("/")({ component: Home })

const WRAP = "mx-auto w-full max-w-6xl px-4"

const FEATURES = [
  {
    index: "01",
    tag: "Verification",
    title: "Every variable has a proof.",
    text: "Static flow verification catches uncertainty before it reaches the machine. No hidden dynamic dispatch. No surprises at runtime.",
    link: "#architecture",
    cta: "See how it works",
  },
  {
    index: "02",
    tag: "Performance",
    title: "Skip the warm-up.",
    text: "Native x86-64 instructions are emitted directly into executable memory. Fast from the first call.",
    link: "#quickstart",
    cta: "Read the quickstart",
  },
  {
    index: "03",
    tag: "Foundations",
    title: "Small core. Big ambition.",
    text: "Built without LLVM or Cranelift. A focused engine where every layer has a clear job.",
    link: "#architecture",
    cta: "View the stack",
  },
] as const

const LAYERS = [
  {
    step: "01",
    icon: "Py",
    name: "Frontend",
    detail: "Python · expressive syntax",
  },
  {
    step: "02",
    icon: "C+",
    name: "Engine",
    detail: "C++20 · static flow verifier",
  },
  { step: "03", icon: "C", name: "Bridge", detail: "C · ABI alignment" },
  {
    step: "04",
    icon: "↯",
    name: "Backend",
    detail: "x64 · hand-encoded opcodes",
  },
] as const

const CONVICTIONS = [
  {
    index: "01",
    tag: "Ownership",
    title: "Nothing to inherit.",
    lines: [
      ["llvm", "✗"],
      ["cranelift", "✗"],
      ["runtime libraries", "✗"],
      ["first principles", "✓"],
    ] as const,
    text: "No compiler toolkit underneath, no package graph behind the language. Every layer is small enough to read in one sitting, which is the only way an engine can stay fast and stay understood at the same time.",
  },
  {
    index: "02",
    tag: "Proof",
    title: "Nothing left to chance.",
    lines: [
      ["flow analysis", "static"],
      ["guards emitted", "0"],
      ["runtime dispatch", "none"],
    ] as const,
    text: "Types are settled before the code runs. Once a program is verified, the hot path carries no guards and no dynamic lookups: the machine executes exactly what the verifier promised it would.",
  },
  {
    index: "03",
    tag: "Proximity",
    title: "Closer than a code review.",
    lines: [
      ["frontend", "Python"],
      ["engine", "C++20"],
      ["output", "x86-64"],
    ] as const,
    text: "The distance between an idea and the instruction that runs it is three deliberate layers. No abstraction tax, and no intermediate representation that nobody owns.",
  },
  {
    index: "04",
    tag: "Openness",
    title: "Open by default.",
    lines: [
      ["license", "MIT"],
      ["issues", "welcome"],
      ["roadmap", "public"],
    ] as const,
    text: "The engine, the verifier, and the roadmap sit in public from the first commit. Read it, disagree with it, and open the pull request: the roadmap here is a conversation, not a surprise.",
  },
] as const

const QUICKSTART = `git clone https://github.com/Project-Lithon/Lithon.git
cd Lithon
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
python3 src/frontend/frontend.py tests/programs/float.py > /tmp/float.ir
./build/tier_runner /tmp/float.ir --strict`

function SectionHead({
  kicker,
  title,
  lede,
}: {
  kicker: string
  title: React.ReactNode
  lede?: string
}) {
  return (
    <div className="mb-8 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
      <div>
        <Badge variant="outline" className="mb-3">
          {kicker}
        </Badge>
        <h2 className="font-heading text-3xl font-extrabold tracking-tight md:text-4xl">
          {title}
        </h2>
      </div>
      {lede && <p className="max-w-xl text-sm text-muted-foreground">{lede}</p>}
    </div>
  )
}

function CodeWindow() {
  return (
    <Card className="font-mono text-xs">
      <CardHeader className="border-b pb-3">
        <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <span className="flex gap-1.5" aria-hidden="true">
            <i className="size-2.5 rounded-full bg-destructive/60" />
            <i className="size-2.5 rounded-full bg-primary" />
            <i className="size-2.5 rounded-full bg-chart-3" />
          </span>
          fib.py · lithon
          <Badge variant="secondary" className="ms-auto">
            ready
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-1.5 py-4 leading-relaxed">
        <p>
          <span className="text-muted-foreground">01</span>{" "}
          <b className="text-accent-strong">def</b> fib(n: int[32]) -&gt;
          int[32]:
        </p>
        <p>
          <span className="text-muted-foreground">02</span>
          &nbsp;&nbsp;&nbsp;&nbsp;<b className="text-accent-strong">if</b> n
          &lt; 2:
        </p>
        <p>
          <span className="text-muted-foreground">03</span>
          &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
          <b className="text-accent-strong">return</b> n
        </p>
        <p>
          <span className="text-muted-foreground">04</span>
          &nbsp;&nbsp;&nbsp;&nbsp;<b className="text-accent-strong">
            return
          </b>{" "}
          fib(n - 1) + fib(n - 2)
        </p>
        <p>
          <span className="text-muted-foreground">06</span>{" "}
          <b className="text-accent-strong">print</b>(fib(30))
        </p>
      </CardContent>
      <CardFooter className="flex-col items-start gap-1 text-xs">
        <span className="text-muted-foreground">
          lithon › static flow verified
        </span>
        <span>
          lithon › <b>832040</b>{" "}
          <span className="text-muted-foreground">
            · 7.01 ms · tier-1 native
          </span>
        </span>
      </CardFooter>
    </Card>
  )
}

function CopyButton() {
  const [state, setState] = useState<"idle" | "copied">("idle")

  const copy = () => {
    navigator.clipboard
      .writeText(QUICKSTART)
      .then(() => {
        setState("copied")
        window.setTimeout(() => setState("idle"), 1600)
      })
      .catch(() => {})
  }

  return (
    <Button variant="outline" size="sm" onClick={copy}>
      {state === "copied" ? "copied" : "copy commands"}
    </Button>
  )
}

function Contributors() {
  const [logins, setLogins] = useState<string[]>([])
  const [status, setStatus] = useState(
    "Reading the commit history from GitHub…"
  )

  useEffect(() => {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 8000)

    fetch(
      "https://api.github.com/repos/Project-Lithon/Lithon/contributors?per_page=30",
      {
        headers: { Accept: "application/vnd.github+json" },
        signal: controller.signal,
      }
    )
      .then((res) =>
        res.ok ? res.json() : Promise.reject(new Error(String(res.status)))
      )
      .then((payload: unknown) => {
        if (!Array.isArray(payload)) throw new Error("unexpected payload")
        const names = payload
          .filter(
            (entry): entry is Record<string, unknown> =>
              typeof entry === "object" && entry !== null
          )
          .filter(
            (entry) => entry.type !== "Bot" && typeof entry.login === "string"
          )
          .map((entry) => entry.login as string)
        setLogins(names.slice(0, 8))
        setStatus(`Live from GitHub · ${names.length} contributors`)
      })
      .catch(() =>
        setStatus(
          "Could not reach GitHub: see the full contributor graph on the repository."
        )
      )
      .finally(() => window.clearTimeout(timeout))

    return () => {
      controller.abort()
      window.clearTimeout(timeout)
    }
  }, [])

  return (
    <div className="flex flex-wrap items-center gap-2">
      {logins.map((login) => (
        <Badge
          key={login}
          variant="secondary"
          render={
            <a
              href={`https://github.com/${login}`}
              target="_blank"
              rel="noreferrer"
            />
          }
        >
          {login}
        </Badge>
      ))}
      <span className="text-xs text-muted-foreground">{status}</span>
    </div>
  )
}

function Home() {
  return (
    <>
      {/* Hero */}
      <section
        className={`${WRAP} grid items-center gap-10 py-16 md:grid-cols-2 md:py-24`}
      >
        <div>
          <Badge variant="outline" className="mb-5">
            Open source · Python-flavored · Native speed
          </Badge>
          <h1 className="font-heading text-4xl font-extrabold tracking-tight md:text-6xl">
            Not a <em className="text-accent-strong not-italic">revolution</em>
            <br />
            but an evolution
            <br />
            of the same bloodline
          </h1>
          <p className="mt-5 max-w-lg text-muted-foreground">
            Lithon is a zero-dependency execution engine that turns statically
            verified Python-flavored code into raw, guard-free x86-64
            instructions.
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <Button size="lg" render={<a href="#quickstart" />}>
              Start building <ArrowUpRightIcon />
            </Button>
            <Button size="lg" variant="ghost" render={<a href="#engine" />}>
              Explore the engine ↓
            </Button>
          </div>
          <div className="mt-8 flex items-center gap-3 text-xs text-muted-foreground">
            <span>Designed in Sri Lanka</span>
            <Separator orientation="vertical" className="h-4" />
            <span>MIT</span>
          </div>
        </div>
        <CodeWindow />
      </section>

      {/* Signals */}
      <section className="border-y bg-card">
        <div
          className={`${WRAP} grid gap-4 py-5 text-sm sm:grid-cols-2 lg:grid-cols-4`}
        >
          <span>
            <b className="me-2">01</b>Static by design
          </span>
          <span>
            <b className="me-2">02</b>Native at runtime
          </span>
          <span>
            <b className="me-2">03</b>Open by default
          </span>
          <span className="text-accent-strong">Built for the curious →</span>
        </div>
      </section>

      {/* Engine */}
      <section id="engine" className={`${WRAP} scroll-mt-20 py-16`}>
        <SectionHead
          kicker="01 / The engine"
          title={
            <>
              Python syntax.
              <br />
              <span className="text-muted-foreground">
                Closer to the metal.
              </span>
            </>
          }
          lede="The expressiveness you know, with the overhead you don't. Lithon checks every type before execution so the hot path stays clean, predictable, and fast."
        />
        <div className="grid gap-4 md:grid-cols-3">
          {FEATURES.map((feature) => (
            <Card key={feature.index}>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <Badge variant="outline">{feature.index}</Badge>
                  <Badge variant="secondary">{feature.tag}</Badge>
                </div>
                <CardTitle className="mt-3 text-lg font-bold">
                  {feature.title}
                </CardTitle>
              </CardHeader>
              <CardContent className="text-muted-foreground">
                {feature.text}
                <Button
                  variant="link"
                  size="sm"
                  className="mt-3 px-0"
                  render={<a href={feature.link} />}
                >
                  {feature.cta} ↗
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* Architecture */}
      <section
        id="architecture"
        className="scroll-mt-20 border-y bg-card py-16"
      >
        <div className={`${WRAP} grid gap-10 md:grid-cols-2`}>
          <div>
            <SectionHead
              kicker="02 / Architecture"
              title={
                <>
                  Four layers.
                  <br />
                  <span className="text-muted-foreground">One clean path.</span>
                </>
              }
            />
            <p className="mb-6 max-w-md text-sm text-muted-foreground">
              Each language does the work it is best at. Lithon moves from
              readable source to executable instructions without dragging a
              heavyweight compiler stack behind it.
            </p>
            <Button
              variant="outline"
              render={
                <a
                  href="https://github.com/Project-Lithon/Lithon"
                  target="_blank"
                  rel="noreferrer"
                />
              }
            >
              Read the source <ArrowUpRightIcon />
            </Button>
          </div>
          <Card>
            <CardContent className="divide-y">
              {LAYERS.map((layer) => (
                <div
                  key={layer.step}
                  className="flex items-center gap-4 py-3 first:pt-1"
                >
                  <span className="text-xs text-muted-foreground">
                    {layer.step}
                  </span>
                  <Badge variant="outline" className="font-mono">
                    {layer.icon}
                  </Badge>
                  <span className="text-sm">
                    <b>{layer.name}</b>
                    <span className="block text-xs text-muted-foreground">
                      {layer.detail}
                    </span>
                  </span>
                  <span className="ms-auto text-muted-foreground">↓</span>
                </div>
              ))}
              <div className="flex items-center justify-between pt-3 text-sm">
                <span className="text-xs text-muted-foreground">output</span>
                <b>direct CPU execution ✓</b>
              </div>
            </CardContent>
          </Card>
        </div>
      </section>

      {/* Quickstart */}
      <section id="quickstart" className={`${WRAP} scroll-mt-20 py-16`}>
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <Badge variant="outline" className="mb-3">
                  03 / Quickstart
                </Badge>
                <CardTitle className="text-2xl font-extrabold md:text-3xl">
                  From clone to run.
                </CardTitle>
                <CardDescription className="mt-2">
                  Three commands. Zero dependency hunting. The fastest way to
                  see Lithon's promise for yourself.
                </CardDescription>
              </div>
              <CopyButton />
            </div>
          </CardHeader>
          <CardContent>
            <pre className="overflow-x-auto rounded-lg bg-muted p-4 font-mono text-xs leading-relaxed">
              {QUICKSTART.split("\n").map((line, index) => (
                <div key={index}>
                  <span className="text-accent-strong">$</span> {line}
                </div>
              ))}
              <div className="text-muted-foreground">
                ✓ [tier1] native · no interpreter fallback
              </div>
              <div>0.3333333333333333</div>
            </pre>
          </CardContent>
        </Card>
      </section>

      {/* Convictions */}
      <section className="border-y bg-card py-16">
        <div className={WRAP}>
          <SectionHead
            kicker="04 / The themes"
            title={
              <>
                Four convictions
                <br />
                <span className="text-muted-foreground">
                  behind the engine.
                </span>
              </>
            }
            lede="Lithon is not a bag of features. It is four convictions about what a language owes the person writing the code, and every layer of the engine exists to serve one of them."
          />
          <div className="grid gap-4 md:grid-cols-2">
            {CONVICTIONS.map((item) => (
              <Card key={item.index}>
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <Badge variant="outline">{item.index}</Badge>
                    <Badge variant="secondary">{item.tag}</Badge>
                  </div>
                  <CardTitle className="mt-3 text-xl font-bold">
                    {item.title}
                  </CardTitle>
                </CardHeader>
                <CardContent className="text-muted-foreground">
                  <div className="mb-4 space-y-1 rounded-lg bg-muted p-3 font-mono text-xs">
                    {item.lines.map(([label, value]) => (
                      <div key={label} className="flex justify-between">
                        <span>{label}</span>
                        <b
                          className={
                            value === "✗"
                              ? "text-destructive"
                              : "text-accent-strong"
                          }
                        >
                          {value}
                        </b>
                      </div>
                    ))}
                  </div>
                  {item.text}
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* Contributors */}
      <section className={`${WRAP} py-16`}>
        <SectionHead
          kicker="05 / Contributors"
          title={
            <>
              Everyone who
              <br />
              <span className="text-muted-foreground">left a mark.</span>
            </>
          }
          lede="Lithon is built in the open, so the credits are read straight from the commit history: fetched live from GitHub rather than hand-written into this page."
        />
        <Card>
          <CardHeader>
            <CardTitle className="font-mono text-sm">
              Project-Lithon / Lithon
            </CardTitle>
            <CardAction>
              <Button
                variant="outline"
                size="sm"
                render={
                  <a
                    href="https://github.com/Project-Lithon/Lithon/graphs/contributors"
                    target="_blank"
                    rel="noreferrer"
                  />
                }
              >
                Full contributor graph →
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent>
            <Contributors />
          </CardContent>
        </Card>
      </section>

      {/* Closing */}
      <section className={`${WRAP} pb-20 text-center`}>
        <Badge variant="outline" className="mb-4">
          The next instruction is yours
        </Badge>
        <h2 className="font-heading text-3xl font-extrabold tracking-tight md:text-5xl">
          Make something
          <br />
          <span className="text-accent-strong">unreasonably fast.</span>
        </h2>
        <Button
          size="lg"
          className="mt-6"
          render={
            <a
              href="https://github.com/Project-Lithon/Lithon"
              target="_blank"
              rel="noreferrer"
            />
          }
        >
          Visit Lithon on GitHub <ArrowUpRightIcon />
        </Button>
        <Alert className="mt-10 text-start">
          <AlertTitle>Zero dependencies</AlertTitle>
          <AlertDescription>
            No LLVM, no Cranelift, no runtime package: just C++20 and the
            machine.
          </AlertDescription>
        </Alert>
      </section>
    </>
  )
}
