import type { ReactNode } from "react"

import { Badge } from "@workspace/ui/components/badge"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@workspace/ui/components/card"
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"

export const WRAP = "mx-auto w-full max-w-6xl px-4"

export function PageHero({
  kicker,
  title,
  lede,
  meta,
}: {
  kicker: string
  title: ReactNode
  lede: string
  meta: readonly string[]
}) {
  return (
    <section className={`${WRAP} border-b py-14 md:py-20`}>
      <Badge variant="outline" className="mb-4">
        {kicker}
      </Badge>
      <h1 className="font-heading text-4xl font-extrabold tracking-tight md:text-5xl">
        {title}
      </h1>
      <p className="mt-4 max-w-2xl text-muted-foreground">{lede}</p>
      <div className="mt-6 flex flex-wrap gap-2">
        {meta.map((item) => (
          <Badge key={item} variant="secondary">
            {item}
          </Badge>
        ))}
      </div>
    </section>
  )
}

export function SectionHead({
  kicker,
  title,
  lede,
}: {
  kicker: string
  title: ReactNode
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

export function DocSection({
  id,
  index,
  title,
  children,
}: {
  id?: string
  index?: string
  title: string
  children: ReactNode
}) {
  return (
    <section id={id} className="scroll-mt-20 border-b py-10 last:border-b-0">
      <h2 className="mb-4 font-heading text-2xl font-bold">
        {index && (
          <Badge variant="outline" className="me-3 align-middle font-mono">
            {index}
          </Badge>
        )}
        {title}
      </h2>
      <div className="space-y-4 text-sm leading-relaxed text-muted-foreground [&_a]:text-foreground [&_a]:underline [&_b]:text-foreground [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-xs [&_code]:text-foreground">
        {children}
      </div>
    </section>
  )
}

export function SpecTable({
  caption,
  rows,
}: {
  caption?: string
  rows: readonly (readonly [string, ReactNode])[]
}) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        {caption && <TableCaption>{caption}</TableCaption>}
        <TableHeader>
          <TableRow>
            <TableHead className="w-1/2">Item</TableHead>
            <TableHead>Detail</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map(([label, value]) => (
            <TableRow key={label}>
              <TableCell className="font-medium text-foreground">
                {label}
              </TableCell>
              <TableCell>{value}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

export function CodeBlock({
  title,
  children,
}: {
  title: string
  children: string
}) {
  return (
    <Card size="sm" className="font-mono text-xs">
      <CardHeader className="border-b pb-2">
        <CardTitle className="font-mono text-xs font-normal text-muted-foreground">
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto py-3 leading-relaxed whitespace-pre-wrap">
        {children}
      </CardContent>
    </Card>
  )
}

export function Note({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <div className="rounded-lg border border-accent-strong/40 bg-accent p-4 text-sm">
      <b className="text-accent-strong">{title}</b>
      <div className="mt-2 text-muted-foreground [&_b]:text-foreground">
        {children}
      </div>
    </div>
  )
}
