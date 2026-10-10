import { createContext, useContext, useMemo } from "react"
import { Link } from "@tanstack/react-router"

import { Badge } from "@workspace/ui/components/badge"
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

import type { Block, ExampleSpec, Tone } from "./model"

export const MachineViewContext = createContext(false)

const KEYWORDS = new Set([
  "def",
  "return",
  "if",
  "elif",
  "else",
  "for",
  "while",
  "in",
  "and",
  "or",
  "not",
  "pass",
])
const BUILTINS = new Set([
  "print",
  "len",
  "range",
  "addressof",
  "valueof",
  "contains",
  "wrap_add",
  "wrap_sub",
  "wrap_mul",
])

const TOKEN_RE =
  /(#[^\n]*)|("(?:[^"\\]|\\.)*")|\b(int\[\d+\]|float\[\d+\]|bool|ptr|list|tuple|dict)\b|\b(\d+\.\d+|\d+)\b|\b(def|return|if|elif|else|for|while|in|and|or|not|pass)\b|\b(print|len|range|addressof|valueof|contains|wrap_add|wrap_sub|wrap_mul)\b/g

export function Highlight({
  code,
  lang,
}: {
  code: string
  lang: "lithon" | "bash" | "text" | "asm"
}) {
  if (lang !== "lithon") {
    return <>{code}</>
  }
  const parts: React.ReactNode[] = []
  let last = 0
  let key = 0
  for (const match of code.matchAll(TOKEN_RE)) {
    const index = match.index
    if (index > last) parts.push(code.slice(last, index))
    const [full, comment, str, type, num, keyword, builtin] = match
    let cls = ""
    if (comment) cls = "text-muted-foreground/70 italic"
    else if (str) cls = "text-accent-strong"
    else if (type) cls = "font-semibold text-foreground"
    else if (num) cls = "text-accent-strong"
    else if (keyword && KEYWORDS.has(keyword))
      cls = "font-semibold text-primary-foreground dark:text-primary"
    else if (builtin && BUILTINS.has(builtin))
      cls = "text-foreground font-medium"
    parts.push(
      cls ? (
        <span key={key++} className={cls}>
          {full}
        </span>
      ) : (
        full
      )
    )
    last = index + full.length
  }
  if (last < code.length) parts.push(code.slice(last))
  return <>{parts}</>
}

export function CodeSurface({
  title,
  code,
  lang = "lithon",
  badge,
}: {
  title?: string
  code: string
  lang?: "lithon" | "bash" | "text" | "asm"
  badge?: React.ReactNode
}) {
  return (
    <Card size="sm" className="overflow-hidden">
      {(title || badge) && (
        <CardHeader className="flex flex-row items-center justify-between gap-2 border-b pb-2">
          <CardTitle className="font-mono text-xs font-normal text-muted-foreground">
            {title}
          </CardTitle>
          {badge}
        </CardHeader>
      )}
      <CardContent className="overflow-x-auto py-3">
        <pre
          dir="ltr"
          className="text-start font-mono text-xs leading-relaxed whitespace-pre"
        >
          <code>
            <Highlight code={code} lang={lang} />
          </code>
        </pre>
      </CardContent>
    </Card>
  )
}

function VerifiedBadge({ spec }: { spec: ExampleSpec }) {
  if (spec.expect === "error") {
    return (
      <Badge variant="outline" className="text-destructive">
        rejected by design
      </Badge>
    )
  }
  if (spec.sourceRef && spec.expectedRef) {
    return (
      <Badge variant="outline" className="text-accent-strong">
        run in CI
      </Badge>
    )
  }
  if (spec.sourceRef) {
    return <Badge variant="outline">from the repo</Badge>
  }
  return null
}

export function Example({ spec }: { spec: ExampleSpec }) {
  const machineView = useContext(MachineViewContext)
  const has = useMemo(
    () => ({
      execution: Boolean(spec.trace?.length),
      output: spec.output !== undefined,
      machine: Boolean(spec.machine),
    }),
    [spec]
  )
  const initial =
    machineView && has.machine
      ? "machine"
      : has.execution
        ? "execution"
        : "source"

  return (
    <Card size="sm" className="overflow-hidden">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 border-b pb-2">
        <CardTitle className="font-mono text-xs font-normal text-muted-foreground">
          {spec.title ?? "example"}
        </CardTitle>
        <VerifiedBadge spec={spec} />
      </CardHeader>
      <CardContent className="p-0">
        <Tabs defaultValue={initial} className="w-full">
          <div className="flex items-center justify-between border-b px-3">
            <TabsList variant="line" className="bg-transparent p-0 shadow-none">
              <TabsTrigger
                value="source"
                className="rounded-none border-b-2 border-transparent px-2 shadow-none data-[state=active]:border-primary data-[state=active]:shadow-none"
              >
                Source
              </TabsTrigger>
              {has.execution && (
                <TabsTrigger
                  value="execution"
                  className="rounded-none border-b-2 border-transparent px-2 shadow-none data-[state=active]:border-primary data-[state=active]:shadow-none"
                >
                  Execution
                </TabsTrigger>
              )}
              {has.output && (
                <TabsTrigger
                  value="output"
                  className="rounded-none border-b-2 border-transparent px-2 shadow-none data-[state=active]:border-primary data-[state=active]:shadow-none"
                >
                  Output
                </TabsTrigger>
              )}
              {has.machine && (
                <TabsTrigger
                  value="machine"
                  className="rounded-none border-b-2 border-transparent px-2 shadow-none data-[state=active]:border-primary data-[state=active]:shadow-none"
                >
                  Machine
                </TabsTrigger>
              )}
            </TabsList>
            <span className="hidden font-mono text-[10px] text-muted-foreground sm:block">
              {spec.lang === "bash"
                ? "shell"
                : spec.lang === "text"
                  ? "text"
                  : spec.lang === "asm"
                    ? "x86-64"
                    : "lithon"}
            </span>
          </div>
          <TabsContent value="source" className="m-0">
            <div dir="ltr" className="overflow-x-auto py-3 ps-4 text-start">
              <pre className="font-mono text-xs leading-relaxed whitespace-pre">
                <code>
                  <Highlight code={spec.source} lang={spec.lang ?? "lithon"} />
                </code>
              </pre>
            </div>
            {spec.note && (
              <p className="border-t px-4 py-2.5 text-xs leading-relaxed text-muted-foreground">
                {spec.note}
              </p>
            )}
          </TabsContent>
          {has.execution && (
            <TabsContent value="execution" className="m-0">
              <div className="divide-y">
                {spec.trace?.map((step) => (
                  <div
                    key={step.step}
                    className="grid grid-cols-[3rem_1fr] gap-3 px-4 py-2.5 text-xs sm:grid-cols-[3rem_1fr_10rem]"
                  >
                    <span className="font-mono text-muted-foreground">
                      {step.step}
                    </span>
                    <div>
                      <div dir="ltr" className="text-start font-mono">
                        {step.detail}
                      </div>
                      {step.state && (
                        <div
                          dir="ltr"
                          className="mt-1 text-start font-mono text-[11px] text-accent-strong"
                        >
                          {step.state}
                        </div>
                      )}
                    </div>
                    {step.out && (
                      <div
                        dir="ltr"
                        className="text-start font-mono text-muted-foreground sm:text-end"
                      >
                        → {step.out}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </TabsContent>
          )}
          {has.output && (
            <TabsContent value="output" className="m-0">
              <div dir="ltr" className="overflow-x-auto px-4 py-3 text-start">
                <pre className="font-mono text-xs leading-relaxed whitespace-pre">
                  {spec.output}
                </pre>
              </div>
            </TabsContent>
          )}
          {has.machine && (
            <TabsContent value="machine" className="m-0 space-y-3 p-4">
              {spec.machine?.ir && (
                <div>
                  <p className="mb-1.5 font-mono text-[10px] tracking-wide text-muted-foreground uppercase">
                    typed IR (frontend output)
                  </p>
                  <div
                    dir="ltr"
                    className="overflow-x-auto rounded-lg bg-muted p-3 text-start"
                  >
                    <pre className="font-mono text-xs leading-relaxed whitespace-pre">
                      <Highlight code={spec.machine.ir} lang="text" />
                    </pre>
                  </div>
                </div>
              )}
              {spec.machine?.asm && (
                <div>
                  <p className="mb-1.5 font-mono text-[10px] tracking-wide text-muted-foreground uppercase">
                    x86-64 (encoder-style listing)
                  </p>
                  <div
                    dir="ltr"
                    className="overflow-x-auto rounded-lg bg-muted p-3 text-start"
                  >
                    <pre className="font-mono text-xs leading-relaxed whitespace-pre">
                      {spec.machine.asm}
                    </pre>
                  </div>
                </div>
              )}
              {spec.machine?.note && (
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {spec.machine.note}
                </p>
              )}
            </TabsContent>
          )}
        </Tabs>
      </CardContent>
    </Card>
  )
}

const TONE_CLS: Record<Tone, string> = {
  note: "border-accent-strong/40 bg-accent",
  warn: "border-amber-500/40 bg-amber-500/10",
  danger: "border-destructive/40 bg-destructive/10",
  good: "border-emerald-600/40 bg-emerald-600/10",
}
const TONE_LABEL: Record<Tone, string> = {
  note: "Note",
  warn: "Watch out",
  danger: "Limitation",
  good: "Verified",
}

function renderInline(text: string, keyBase = ""): React.ReactNode[] {
  const out: React.ReactNode[] = []
  const re = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)]+)\)/g
  let last = 0
  let k = 0
  for (const m of text.matchAll(re)) {
    const i = m.index
    if (i > last) out.push(text.slice(last, i))
    const [full, code_, bold, label, href] = m
    if (code_) {
      out.push(
        <code
          key={`${keyBase}c${k++}`}
          className="rounded bg-muted px-1 py-0.5 font-mono text-xs text-foreground"
        >
          {code_}
        </code>
      )
    } else if (bold) {
      out.push(<b key={`${keyBase}b${k++}`}>{bold}</b>)
    } else if (label && href) {
      out.push(
        href.startsWith("/") ? (
          <Link
            key={`${keyBase}a${k++}`}
            to={href}
            className="underline underline-offset-4"
          >
            {label}
          </Link>
        ) : (
          <a
            key={`${keyBase}a${k++}`}
            href={href}
            className="underline underline-offset-4"
          >
            {label}
          </a>
        )
      )
    }
    last = i + full.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function Prose({ text }: { text: string }) {
  return <p className="leading-relaxed">{renderInline(text)}</p>
}

export function Inline({ text }: { text: string }) {
  return <>{renderInline(text)}</>
}

function NoteBlock({
  tone = "note",
  title,
  text,
}: {
  tone?: Tone
  title: string
  text: string
}) {
  return (
    <div className={`rounded-lg border p-4 text-sm ${TONE_CLS[tone]}`}>
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-semibold tracking-wide uppercase">
          {TONE_LABEL[tone]}
        </span>
        <b className="text-foreground">{title}</b>
      </div>
      <div className="mt-1.5 leading-relaxed text-muted-foreground">
        {renderInline(text)}
      </div>
    </div>
  )
}

function StepsBlock({
  items,
}: {
  items: readonly { title: string; text: string }[]
}) {
  return (
    <ol className="space-y-3">
      {items.map((item, i) => (
        <li key={item.title} className="flex gap-3">
          <span className="mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-full border font-mono text-xs">
            {i + 1}
          </span>
          <div className="text-sm leading-relaxed">
            <b className="text-foreground">{item.title}</b>
            <div className="mt-0.5 text-muted-foreground">
              {renderInline(item.text)}
            </div>
          </div>
        </li>
      ))}
    </ol>
  )
}

function CardsBlock({
  items,
}: {
  items: readonly { title: string; text: string }[]
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {items.map((item) => (
        <Card key={item.title} size="sm">
          <CardContent className="text-sm font-medium text-foreground">
            {item.title}
            <p className="mt-1 leading-relaxed font-normal text-muted-foreground">
              {renderInline(item.text)}
            </p>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

function LinkCardsBlock({
  items,
}: {
  items: readonly { title: string; text: string; href: string }[]
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {items.map((item) => (
        <Card
          key={item.href}
          size="sm"
          className="transition-colors hover:border-primary"
        >
          <CardContent className="text-sm">
            <Link to={item.href} className="font-medium text-foreground">
              {item.title} <span aria-hidden>→</span>
            </Link>
            <p className="mt-1 leading-relaxed font-normal text-muted-foreground">
              {renderInline(item.text)}
            </p>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

function TableBlock({
  caption,
  rows,
}: {
  caption?: string
  rows: readonly (readonly [string, string])[]
}) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        {caption && (
          <caption className="border-b px-3 py-2 text-xs text-muted-foreground">
            {caption}
          </caption>
        )}
        <thead>
          <tr className="border-b bg-muted/50">
            <th className="px-3 py-2 text-start font-medium">Item</th>
            <th className="px-3 py-2 text-start font-medium">Detail</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, value]) => (
            <tr key={label} className="border-b last:border-b-0">
              <td className="px-3 py-2 font-medium text-foreground">{label}</td>
              <td
                dir="ltr"
                className="px-3 py-2 text-start text-muted-foreground"
              >
                {renderInline(value)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case "p":
      return <Prose text={block.text} />
    case "list":
      return block.ordered ? (
        <ol className="list-decimal space-y-1.5 ps-5 text-sm leading-relaxed marker:text-muted-foreground">
          {block.items.map((item, i) => (
            <li key={i}>{renderInline(item)}</li>
          ))}
        </ol>
      ) : (
        <ul className="list-disc space-y-1.5 ps-5 text-sm leading-relaxed marker:text-muted-foreground">
          {block.items.map((item, i) => (
            <li key={i}>{renderInline(item)}</li>
          ))}
        </ul>
      )
    case "code":
      return <Example spec={block.example} />
    case "table":
      return <TableBlock caption={block.caption} rows={block.rows} />
    case "note":
      return (
        <NoteBlock tone={block.tone} title={block.title} text={block.text} />
      )
    case "steps":
      return <StepsBlock items={block.items} />
    case "cards":
      return <CardsBlock items={block.items} />
    case "linkcards":
      return <LinkCardsBlock items={block.items} />
  }
}

export function useMachineView() {
  return useContext(MachineViewContext)
}

export function DocsBadgeRow({ tags }: { tags: readonly string[] }) {
  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {tags.map((tag) => (
        <Badge key={tag} variant="secondary">
          {tag}
        </Badge>
      ))}
    </div>
  )
}

export function SearchHint() {
  return (
    <span className="hidden items-center gap-1.5 text-xs text-muted-foreground md:inline-flex">
      Search docs
      <Kbd>⌘</Kbd>
      <Kbd>K</Kbd>
    </span>
  )
}
