import { createFileRoute, Link } from "@tanstack/react-router"
import { ArrowUpRightIcon } from "@phosphor-icons/react"

import { Badge } from "@workspace/ui/components/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@workspace/ui/components/card"
import { Separator } from "@workspace/ui/components/separator"

import { CHANGES, KIND_META, type ChangeKind } from "../changelog/data"
import { metaForPath } from "../lib/seo"
import { PageHero, WRAP } from "../site"

export const Route = createFileRoute("/changelog")({
  head: () => {
    const meta = metaForPath("/changelog")
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
  component: Changelog,
})

const ORDER: ChangeKind[] = [
  "language",
  "engine",
  "toolchain",
  "verification",
  "docs",
  "site",
  "build",
]

function Changelog() {
  const counts = ORDER.map((kind) => ({
    kind,
    count: CHANGES.filter((c) => c.kind === kind).length,
  })).filter((k) => k.count > 0)

  return (
    <>
      <PageHero
        kicker="02 / Changelog"
        title={
          <>
            What changed,
            <br />
            and when.
          </>
        }
        lede="Every shipped change to the language, the engine, the toolchain and this site, newest first. Each entry links to the documentation that now covers it."
        meta={[`${CHANGES.length} changes`, "from the repository history"]}
      />

      <section className={`${WRAP} py-10`}>
        <div className="mb-8 flex flex-wrap gap-2">
          {counts.map(({ kind, count }) => (
            <Badge
              key={kind}
              variant="outline"
              className={KIND_META[kind].chip}
            >
              {KIND_META[kind].label} · {count}
            </Badge>
          ))}
        </div>

        <div className="space-y-10">
          {ORDER.map((kind) => {
            const items = CHANGES.filter((c) => c.kind === kind)
            if (items.length === 0) return null
            return (
              <section key={kind}>
                <div className="mb-4 flex items-center gap-3">
                  <h2 className="font-heading text-xl font-bold">
                    {KIND_META[kind].label}
                  </h2>
                  <Separator className="flex-1" />
                </div>
                <div className="space-y-3">
                  {items.map((change) => (
                    <Card key={change.title} size="sm">
                      <CardHeader className="pb-2">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <CardTitle className="text-base font-semibold">
                            {change.title}
                          </CardTitle>
                          {change.date && (
                            <span className="font-mono text-[11px] text-muted-foreground">
                              {change.date}
                            </span>
                          )}
                        </div>
                      </CardHeader>
                      <CardContent>
                        <CardDescription className="leading-relaxed">
                          {change.detail}
                        </CardDescription>
                        <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
                          {change.commit && (
                            <a
                              href={`https://github.com/Project-Lithon/Lithon/commit/${change.commit}`}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex items-center gap-1 font-mono text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                            >
                              {change.commit}
                              <ArrowUpRightIcon />
                            </a>
                          )}
                          {change.href && (
                            <Link
                              to="/docs/$slug"
                              params={{ slug: change.href.replace("/docs/", "") }}
                              className="font-medium text-accent-strong underline-offset-4 hover:underline"
                            >
                              read the docs →
                            </Link>
                          )}
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </section>
            )
          })}
        </div>

        <div className="mt-12 flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4 text-sm">
          <p className="text-muted-foreground">
            The full commit-by-commit history is in the repository, including
            the automated benchmark reports.
          </p>
          <a
            href="https://github.com/Project-Lithon/Lithon/commits/master"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 font-medium text-accent-strong"
          >
            Commit history
            <ArrowUpRightIcon />
          </a>
        </div>
      </section>
    </>
  )
}
