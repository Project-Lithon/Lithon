import { createFileRoute, Link } from "@tanstack/react-router"
import {
  ArrowRightIcon,
  BookOpenIcon,
  CompassIcon,
  MagnifyingGlassIcon,
  StarIcon,
} from "@phosphor-icons/react"

import { Badge } from "@workspace/ui/components/badge"
import { Card, CardContent } from "@workspace/ui/components/card"

import { DOC_GROUPS } from "../../docs/model"
import { DOCS, outcomeOf, priorityOf } from "../../docs/registry"
import { SearchHint } from "../../docs/templates"
import { assetUrl } from "../../lib/assets"
import { metaForPath } from "../../lib/seo"
import { PageHero, WRAP } from "../../site"

export const Route = createFileRoute("/docs/")({
  head: () => {
    const meta = metaForPath("/docs")
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
  component: DocsIndex,
})

function firstPageOf(groupSlug: string) {
  return DOCS.pages.find((p) => p.group === groupSlug)
}

const PRIORITY_META = {
  essential: {
    label: "essential",
    chip: "border-accent-strong/50 text-accent-strong",
    Icon: StarIcon,
  },
  recommended: {
    label: "recommended",
    chip: "text-muted-foreground",
    Icon: CompassIcon,
  },
  reference: {
    label: "reference",
    chip: "text-muted-foreground/70",
    Icon: BookOpenIcon,
  },
} as const

function DocsIndex() {
  const essential = DOCS.pages.filter((p) => priorityOf(p) === "essential")

  return (
    <>
      <PageHero
        kicker="01 / Documentation"
        title={
          <>
            Know the path
            <br />
            from source to speed.
          </>
        }
        lede="From your first annotated program to the bytes the encoder emits: every example here is compiled by the real Lithon frontend and pinned to the repo's regression outputs."
        meta={[
          "Mandatory static types",
          "Dual-tier execution",
          "Zero dependencies",
        ]}
      />

      <section className={`${WRAP} py-10`}>
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            <b className="text-foreground">{DOCS.pages.length} pages</b> in{" "}
            {DOC_GROUPS.length} sections ·{" "}
            {essential.length} of them are the reading path
          </p>
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="flex items-center gap-1.5">
              <MagnifyingGlassIcon />
              <SearchHint />
            </Badge>
            <Badge variant="secondary">Cmd/Ctrl + K</Badge>
          </div>
        </div>

        {/* The reading path: the pages that are meant to be read in order. */}
        <div className="mb-10 overflow-hidden rounded-xl border-2 border-accent-strong/40">
          <div className="flex flex-wrap items-center gap-2 border-b border-accent-strong/25 bg-accent/10 px-4 py-2.5">
            <StarIcon className="size-4 text-accent-strong" weight="fill" />
            <span className="font-heading text-sm font-bold text-foreground">
              The reading path
            </span>
            <span className="text-xs text-muted-foreground">
              these {essential.length} pages are the ones to read, in this order
            </span>
          </div>
          <ol className="divide-y">
            {essential.map((page, i) => (
              <li key={page.slug}>
                <Link
                  to="/docs/$slug"
                  params={{ slug: page.slug }}
                  className="group flex gap-4 px-4 py-3 transition-colors hover:bg-muted/50"
                >
                  <span className="mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-full border font-mono text-xs text-muted-foreground">
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-foreground group-hover:text-accent-strong">
                        {page.title}
                      </span>
                      <Badge variant="outline" className="text-[10px]">
                        {DOC_GROUPS.find((g) => g.slug === page.group)?.title}
                      </Badge>
                    </span>
                    <span className="mt-0.5 block text-sm leading-relaxed text-muted-foreground">
                      {outcomeOf(page) ?? page.description}
                    </span>
                  </span>
                  <ArrowRightIcon className="mt-1 size-4 shrink-0 text-muted-foreground/50 group-hover:text-accent-strong" />
                </Link>
              </li>
            ))}
          </ol>
        </div>

        {/* Everything, grouped. */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {DOC_GROUPS.map((group) => {
            const pages = DOCS.pages.filter((p) => p.group === group.slug)
            const first = firstPageOf(group.slug)
            if (!first) return null
            return (
              <Card key={group.slug} size="sm" className="flex flex-col">
                <CardContent className="flex flex-1 flex-col gap-3">
                  <div>
                    <div className="flex items-center justify-between gap-2">
                      <h2 className="font-heading text-lg font-bold">
                        {group.title}
                      </h2>
                      <Badge variant="outline">{pages.length}</Badge>
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {group.blurb}
                    </p>
                  </div>
                  <ul className="flex flex-col gap-1.5 text-sm">
                    {pages.map((page) => {
                      const meta = PRIORITY_META[priorityOf(page)]
                      return (
                        <li key={page.slug}>
                          <Link
                            to="/docs/$slug"
                            params={{ slug: page.slug }}
                            className="group flex items-baseline gap-2"
                          >
                            <span className="text-muted-foreground group-hover:text-foreground">
                              {page.title}
                            </span>
                            {priorityOf(page) === "essential" && (
                              <meta.Icon
                                className="size-3 shrink-0 translate-y-px text-accent-strong"
                                weight="fill"
                                aria-label="essential"
                              />
                            )}
                          </Link>
                        </li>
                      )
                    })}
                  </ul>
                  <Link
                    to="/docs/$slug"
                    params={{ slug: first.slug }}
                    className="mt-auto inline-flex items-center gap-1.5 pt-2 text-sm font-medium text-accent-strong"
                  >
                    Start reading
                    <ArrowRightIcon className="size-3.5 rtl:rotate-180" />
                  </Link>
                </CardContent>
              </Card>
            )
          })}
        </div>

        <div className="mt-10 flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4 text-sm">
          <p className="text-muted-foreground">
            Prefer the machine view? Toggle it in the command palette and every
            example with IR opens on its Machine tab.
          </p>
          <Link
            to="/docs/$slug"
            params={{ slug: "source-to-machine" }}
            className="inline-flex items-center gap-1.5 font-medium text-accent-strong"
          >
            Source to machine
            <ArrowRightIcon className="size-3.5 rtl:rotate-180" />
          </Link>
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4 text-sm">
          <p className="text-muted-foreground">
            Looking for a specific behaviour? The reference pages are lookup
            tables, not reading material.
          </p>
          <Link
            to="/docs/$slug"
            params={{ slug: "errors" }}
            className="inline-flex items-center gap-1.5 font-medium text-accent-strong"
          >
            Errors &amp; diagnostics
            <ArrowRightIcon className="size-3.5 rtl:rotate-180" />
          </Link>
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dashed p-4 text-sm">
          <p className="text-muted-foreground">
            Building an agent, an index, or a dataset from these docs? Take the
            whole thing as plain text instead of scraping it.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <a
              href={assetUrl("/llms.txt")}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 font-medium text-accent-strong"
            >
              llms.txt
            </a>
            <a
              href={assetUrl("/llms-full.txt")}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 font-medium text-accent-strong"
            >
              llms-full.txt
            </a>
          </div>
        </div>
      </section>
    </>
  )
}
