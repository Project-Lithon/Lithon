import { useState } from "react"
import { createFileRoute } from "@tanstack/react-router"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@workspace/ui/components/card"
import { Kbd } from "@workspace/ui/components/kbd"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@workspace/ui/components/tabs"
import { Textarea } from "@workspace/ui/components/textarea"

import { PageHero, WRAP } from "../site"

export const Route = createFileRoute("/playground")({ component: Playground })

const EXAMPLES = [
  {
    id: "fib",
    title: "Recursive fibonacci",
    note: "recursion · native tier-1",
    code: `def fib(n: int[32]) -> int[32]:\n    if n < 2:\n        return n\n    return fib(n - 1) + fib(n - 2)\n\nprint(fib(10))`,
  },
  {
    id: "range",
    title: "Typed range loop",
    note: "for-range · fixed int[32] accumulator",
    code: `total: int[32] = 0\ni: int[32] = 0\n\nfor i in range(10):\n    total = total + i\n\nprint(total)`,
  },
  {
    id: "nested",
    title: "Nested loop accumulator",
    note: "while + for · verified nesting",
    code: `row: int[32] = 1\nsum: int[32] = 0\n\nwhile row <= 3:\n    col: int[32] = 1\n    while col <= 3:\n        sum = sum + row * col\n        col = col + 1\n    row = row + 1\n\nprint(sum)`,
  },
  {
    id: "float",
    title: "Float arithmetic",
    note: "float[64] · SSE2 native output",
    code: `a: float[64] = 3.5\nb: float[64] = 2.0\n\nprint(a + b)\nprint(a * b)`,
  },
  {
    id: "untyped",
    title: "Untyped program",
    note: "no annotations · tier-0 fallback",
    code: `def double(n):\n    return n * 2\n\nprint(double(21))`,
  },
] as const

type Line = {
  kind: "prompt" | "success" | "muted" | "failure" | "value"
  text: string
}

const LINE_CLASS: Record<Line["kind"], string> = {
  prompt: "text-muted-foreground",
  success: "text-accent-strong",
  muted: "text-muted-foreground",
  failure: "text-destructive",
  value: "font-bold text-foreground",
}

function fibonacci(index: number): bigint | null {
  if (!Number.isInteger(index) || index < 0 || index > 2000) return null
  let [prev, current] = [0n, 1n]
  for (let step = 0; step < index; step += 1)
    [prev, current] = [current, prev + current]
  return prev
}

function verify(code: string): Line[] {
  const trimmed = code.trim()
  if (trimmed === "")
    return [
      { kind: "prompt", text: "lithon ›" },
      { kind: "failure", text: "empty program — nothing to verify" },
    ]

  const annotations =
    trimmed.match(/:\s*(?:(?:int|float)\s*\[\s*\d+\s*\]|bool\b)/g)?.length ?? 0
  const typed = annotations > 0
  const match =
    /print\s*\(\s*fib\s*\(\s*(\d+)\s*\)\s*\)/.exec(trimmed) ??
    /fib\s*\(\s*(\d+)\s*\)/.exec(trimmed)
  const index = match ? Number(match[1]) : Number.NaN
  const value = Number.isNaN(index) ? null : fibonacci(index)

  const lines: Line[] = [{ kind: "prompt", text: "lithon ›" }]
  lines.push(
    typed
      ? { kind: "success", text: "✓ static flow verified · tier-1 native" }
      : {
          kind: "muted",
          text: "○ static flow unverified — would run on the tier-0 interpreter",
        }
  )
  lines.push(
    typed
      ? {
          kind: "success",
          text: `✓ ${annotations} fixed type annotation${annotations === 1 ? "" : "s"} resolved`,
        }
      : {
          kind: "muted",
          text: "○ annotate a binding — try `total: int[32] = 0`",
        },
    { kind: "prompt", text: "lithon ›" }
  )

  if (value !== null) {
    lines.push({ kind: "value", text: `result: ${value}` })
    lines.push({
      kind: "muted",
      text: `fib(${index}) · native path · 0.42 ms simulated`,
    })
  } else if (/\bprint\s*\(/.test(trimmed)) {
    lines.push({
      kind: "muted",
      text: "program accepted — add print(fib(10)) to preview a computed value",
    })
  } else {
    lines.push({
      kind: "muted",
      text: "no top-level call detected — nothing to evaluate",
    })
  }
  return lines
}

function Playground() {
  const [tab, setTab] = useState("code")
  const [code, setCode] = useState<string>(EXAMPLES[0].code)
  const [file, setFile] = useState("scratch.lithon")
  const [state, setState] = useState<"idle" | "verifying" | "ready">("idle")
  const [output, setOutput] = useState<Line[]>([
    {
      kind: "muted",
      text: "Press “Run program” to send your code through the verifier.",
    },
  ])

  const run = () => {
    setState("verifying")
    setOutput([
      { kind: "prompt", text: "lithon ›" },
      { kind: "muted", text: "checking static flow…" },
    ])
    window.setTimeout(() => {
      setOutput(verify(code))
      setState("ready")
    }, 500)
  }

  const load = (id: string) => {
    const example = EXAMPLES.find((item) => item.id === id)
    if (!example) return
    setCode(example.code)
    setFile(`${example.id}.lithon`)
    setTab("code")
  }

  return (
    <>
      <PageHero
        kicker="04 / Playground · REPL"
        title={
          <>
            Write it.
            <br />
            Run it.
            <br />
            See the path.
          </>
        }
        lede="A small interactive surface for trying the Lithon shape. This frontend REPL simulates the verifier output so the experience is useful before the native engine is connected."
        meta={["Interactive demo", "Client-side only", "No setup required"]}
      />

      <section className={`${WRAP} py-10`}>
        <Tabs value={tab} onValueChange={setTab}>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <TabsList>
              <TabsTrigger value="code">Code</TabsTrigger>
              <TabsTrigger value="examples">Examples</TabsTrigger>
            </TabsList>
            <Button onClick={run} disabled={state === "verifying"}>
              {state === "verifying" ? "verifying…" : "Run program ↗"}
            </Button>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <TabsContent value="code">
              <Card>
                <CardHeader className="border-b pb-3">
                  <CardTitle className="flex items-center justify-between font-mono text-xs font-normal text-muted-foreground">
                    <span>{file}</span>
                    <Badge variant="secondary">Python-flavored</Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent className="py-4">
                  <Textarea
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    onKeyDown={(event) => {
                      if (
                        event.key === "Enter" &&
                        (event.metaKey || event.ctrlKey)
                      ) {
                        event.preventDefault()
                        run()
                      }
                    }}
                    aria-label="Lithon source code"
                    spellCheck={false}
                    autoCapitalize="off"
                    autoCorrect="off"
                    className="min-h-64 resize-y bg-muted font-mono text-xs leading-relaxed"
                  />
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="examples">
              <Card>
                <CardHeader className="border-b pb-3">
                  <CardTitle className="flex items-center justify-between font-mono text-xs font-normal text-muted-foreground">
                    <span>examples</span>
                    <Badge variant="secondary">pick a starting point</Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent className="divide-y py-2">
                  {EXAMPLES.map((example) => (
                    <button
                      key={example.id}
                      type="button"
                      onClick={() => load(example.id)}
                      className="w-full px-1 py-3 text-start hover:bg-muted"
                    >
                      <b className="block font-heading text-sm">
                        {example.title}
                      </b>
                      <span className="font-mono text-xs text-muted-foreground">
                        {example.note}
                      </span>
                    </button>
                  ))}
                </CardContent>
              </Card>
            </TabsContent>

            <div>
              <Card>
                <CardHeader className="border-b pb-3">
                  <CardTitle className="flex items-center justify-between font-mono text-xs font-normal text-muted-foreground">
                    <span>● lithon runtime</span>
                    <Badge
                      variant={state === "verifying" ? "default" : "secondary"}
                    >
                      {state}
                    </Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent
                  className="min-h-64 space-y-1 py-4 font-mono text-xs"
                  aria-live="polite"
                >
                  {output.map((line, index) => (
                    <p key={index} className={LINE_CLASS[line.kind]}>
                      {line.text}
                    </p>
                  ))}
                </CardContent>
              </Card>
            </div>
          </div>
        </Tabs>

        <p className="mt-4 text-sm text-muted-foreground">
          <b className="text-foreground">Note:</b> This browser playground is an
          interactive product preview. Native compilation happens in the Lithon
          engine. Press <Kbd>Ctrl</Kbd> or <Kbd>⌘</Kbd> + <Kbd>Enter</Kbd> to
          run.
        </p>
      </section>
    </>
  )
}
