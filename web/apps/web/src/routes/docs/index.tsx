import { createFileRoute, Link } from "@tanstack/react-router"
import { ArrowLeftIcon, ArrowRightIcon } from "@phosphor-icons/react"

import { Badge } from "@workspace/ui/components/badge"
import { Card, CardContent } from "@workspace/ui/components/card"

import { DOC_GROUPS } from "../../docs/model"
import { DOCS } from "../../docs/registry"
import { SearchHint } from "../../docs/templates"
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

function DocsIndex() {
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
        <div className="mb-6 flex items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            {DOCS.pages.length} pages · press <SearchHint /> to search
            everything
          </p>
          <Badge variant="secondary">Cmd/Ctrl + K</Badge>
        </div>

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
                  <ul className="flex flex-col gap-1 text-sm">
                    {pages.map((page) => (
                      <li key={page.slug}>
                        <Link
                          to="/docs/$slug"
                          params={{ slug: page.slug }}
                          className="text-muted-foreground hover:text-foreground"
                        >
                          {page.title}
                        </Link>
                      </li>
                    ))}
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
            <ArrowLeftIcon className="size-3.5 rtl:rotate-180" />
            Source to machine
          </Link>
        </div>
      </section>
    </>
  )
}
