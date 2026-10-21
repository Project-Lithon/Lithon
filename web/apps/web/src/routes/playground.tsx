import { useCallback, useEffect, useRef, useState } from "react"
import { createFileRoute } from "@tanstack/react-router"
import {
  CaretDownIcon,
  CheckCircleIcon,
  CopyIcon,
  PlayIcon,
  StackIcon,
  TerminalWindowIcon,
  XCircleIcon,
} from "@phosphor-icons/react"

import type { Diagnostic } from "@codemirror/lint"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@workspace/ui/components/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { Kbd } from "@workspace/ui/components/kbd"

import { PageHero, WRAP } from "../site"
import { metaForPath } from "../lib/seo"
import { LithonEditor } from "../lib/lithon-editor"
import { PLAYGROUND_EXAMPLES } from "../lib/playground-examples"
import { PLAYGROUND_IR } from "../lib/playground-ir"

export const Route = createFileRoute("/playground")({
  head: () => {
    const meta = metaForPath("/playground")
    return {
      meta: [
        { title: meta.title },
        { name: "description", content: meta.description },
        { property: "og:title", content: meta.title },
        { property: "og:description", content: meta.description },
        { tagName: "link", rel: "canonical", href: meta.canonical },
      ],
    }
  },
  component: Playground,
})

type RunResult = {
  ok: boolean
  stage: "input" | "frontend" | "typecheck" | "ir"
  diagnostics: string[]
  ir: string | null
  typecheck: string | null
  hint: string
}

const EXAMPLES = PLAYGROUND_EXAMPLES

/** FNV-1a, matching `irKey()` in docs/templates.tsx. */
function irKey(source: string) {
  let h = 0x811c9dc5
  for (let i = 0; i < source.length; i++) {
    h ^= source.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16).padStart(8, "0")
}

/** The pre-generated IR for a built-in example, when there is one. */
function staticIr(source: string): string | undefined {
  return PLAYGROUND_IR[irKey(source)]
}

function toDiagnostic(message: string, line = 1): Diagnostic {
  return {
    from: Math.max(0, line - 1),
    to: Math.max(1, line),
    severity: "error",
    message,
  }
}

function Playground() {
  const [code, setCode] = useState<string>(EXAMPLES[0].source)
  const [result, setResult] = useState<RunResult | null>(null)
  const [running, setRunning] = useState(false)
  const [copied, setCopied] = useState(false)
  const abort = useRef<AbortController | null>(null)

  const run = useCallback(async () => {
    abort.current?.abort()
    const controller = new AbortController()
    abort.current = controller
    setRunning(true)
    try {
      const res = await fetch("/api/lithon", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ code }),
        signal: controller.signal,
      })
      if (!res.ok) {
        // The live runner is dev-only. When it is not there, fall back to
        // the pre-generated IR for a built-in example, and say plainly that
        // this is the recorded output rather than a fresh run.
        const fallback = staticIr(code)
        setResult({
          ok: fallback !== undefined,
          stage: fallback !== undefined ? "ir" : "input",
          diagnostics: [
            fallback !== undefined
              ? "the live runner is not available here, so this is the recorded IR for a built-in example"
              : `runner unavailable (HTTP ${res.status})`,
          ],
          ir: fallback ?? null,
          typecheck: null,
          hint:
            fallback !== undefined
              ? "Run `bun run dev` locally to compile your own program."
              : "The local runner is only wired up in dev (`bun run dev`).",
        })
        return
      }
      setResult((await res.json()) as RunResult)
    } catch (e) {
      if ((e as Error).name === "AbortError") return
      const fallback = staticIr(code)
      setResult({
        ok: fallback !== undefined,
        stage: fallback !== undefined ? "ir" : "input",
        diagnostics: [
          fallback !== undefined
            ? "the live runner is not reachable, so this is the recorded IR for a built-in example"
            : String(e),
        ],
        ir: fallback ?? null,
        typecheck: null,
        hint:
          fallback !== undefined
            ? "Run `bun run dev` locally to compile your own program."
            : "Could not reach the local Lithon runner.",
      })
    } finally {
      setRunning(false)
    }
  }, [code])

  useEffect(() => () => abort.current?.abort(), [])

  const diagnostics: Diagnostic[] = result
    ? result.diagnostics.map((d) => toDiagnostic(d))
    : []

  const status = running
    ? { label: "running", tone: "default" as const }
    : !result
      ? { label: "idle", tone: "secondary" as const }
      : result.ok
        ? { label: "lowers to IR", tone: "default" as const }
        : { label: `refused at ${result.stage}`, tone: "destructive" as const }

  return (
    <>
      <PageHero
        kicker="04 / Playground"
        title={
          <>
            Write it.
            <br />
            Run it.
            <br />
            See the path.
          </>
        }
        lede="A real editor with Lithon syntax highlighting and autocomplete, wired to the actual type checker and frontend in this repo. The live compiler runs in local development; the built-in examples carry the IR the engine really emits."
        meta={["Real toolchain", "Recorded IR", "Live in dev"]}
      />

      <section className={`${WRAP} py-10`}>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-muted-foreground">
              scratch
            </span>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button variant="outline" size="sm" className="gap-1.5" />
                }
              >
                <StackIcon />
                Load an example
                <CaretDownIcon className="size-3 opacity-60" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-72">
                <DropdownMenuGroup>
                  <DropdownMenuLabel>Start from a program</DropdownMenuLabel>
                  {EXAMPLES.map((example) => (
                    <DropdownMenuItem
                      key={example.id}
                      onClick={() => {
                        setCode(example.source)
                        setResult(null)
                      }}
                    >
                      <div className="flex flex-col">
                        <span className="text-sm">{example.title}</span>
                        <span className="font-mono text-[11px] text-muted-foreground">
                          {example.note}
                        </span>
                      </div>
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant={status.tone}>{status.label}</Badge>
            <Button onClick={run} disabled={running}>
              {running ? (
                "running…"
              ) : (
                <>
                  <PlayIcon />
                  Run
                </>
              )}
            </Button>
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div className="flex min-h-[30rem] min-w-0 flex-col">
            <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
              <CardHeader className="flex flex-row items-center justify-between border-b py-2">
                <CardTitle className="font-mono text-xs font-normal text-muted-foreground">
                  program.lio
                </CardTitle>
                <div className="flex items-center gap-2">
                  <span className="hidden font-mono text-[10px] text-muted-foreground sm:block">
                    <Kbd>Ctrl</Kbd> + <Kbd>Enter</Kbd> to run
                  </span>
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    onClick={() => {
                      navigator.clipboard
                        .writeText(code)
                        .then(() => {
                          setCopied(true)
                          window.setTimeout(() => setCopied(false), 1200)
                        })
                        .catch(() => {})
                    }}
                    aria-label="Copy source"
                  >
                    {copied ? <CheckCircleIcon /> : <CopyIcon />}
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="min-h-0 flex-1 p-0">
                <LithonEditor
                  value={code}
                  onChange={setCode}
                  diagnostics={diagnostics}
                  onRun={run}
                  minHeight="30rem"
                />
              </CardContent>
            </Card>
          </div>

          <div className="flex min-h-[30rem] min-w-0 flex-col gap-4">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between border-b py-2">
                <CardTitle className="font-mono text-xs font-normal text-muted-foreground">
                  verdict
                </CardTitle>
                <span className="font-mono text-[10px] text-muted-foreground">
                  typecheck.py + frontend.py
                </span>
              </CardHeader>
              <CardContent className="space-y-2 py-3 font-mono text-xs">
                {!result && !running && (
                  <p className="text-muted-foreground">
                    Press <Kbd>Ctrl</Kbd> + <Kbd>Enter</Kbd> to send the program
                    through the real checker and frontend.
                  </p>
                )}
                {running && (
                  <p className="text-muted-foreground">
                    running tools/typecheck.py and src/frontend/frontend.py…
                  </p>
                )}
                {result && (
                  <>
                    <div className="flex items-start gap-2">
                      {result.ok ? (
                        <CheckCircleIcon className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                      ) : (
                        <XCircleIcon className="mt-0.5 size-4 shrink-0 text-destructive" />
                      )}
                      <span className={result.ok ? "" : "text-destructive"}>
                        {result.hint}
                      </span>
                    </div>
                    {result.typecheck && result.typecheck !== "ok" && (
                      <p className="ps-6 text-amber-600">
                        typecheck: {result.typecheck}
                      </p>
                    )}
                    {result.diagnostics.map((d, i) => (
                      <p key={i} className="ps-6 text-destructive">
                        {d}
                      </p>
                    ))}
                  </>
                )}
              </CardContent>
            </Card>

            <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
              <CardHeader className="flex flex-row items-center justify-between border-b py-2">
                <CardTitle className="flex items-center gap-2 font-mono text-xs font-normal text-muted-foreground">
                  <TerminalWindowIcon />
                  typed IR
                </CardTitle>
                <Badge variant="secondary">frontend output</Badge>
              </CardHeader>
              <CardContent className="min-h-0 flex-1 p-0">
                <pre
                  dir="ltr"
                  className="h-full max-h-80 overflow-auto p-4 font-mono text-xs leading-relaxed"
                >
                  {result?.ir ?? "// press Run to lower this program to IR"}
                </pre>
              </CardContent>
            </Card>
          </div>
        </div>
      </section>
    </>
  )
}
