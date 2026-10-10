import { useEffect, useMemo, useState } from "react"
import { createFileRoute, Link, notFound } from "@tanstack/react-router"
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CpuIcon,
  ListIcon,
} from "@phosphor-icons/react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@workspace/ui/components/sheet"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@workspace/ui/components/tooltip"

import { DOC_GROUPS } from "../../docs/model"
import { flatPageOrder, pageBySlug } from "../../docs/registry"
import { metaForPath } from "../../lib/seo"
import {
  BlockView,
  DocsBadgeRow,
  MachineViewContext,
} from "../../docs/templates"
import {
  onMachineViewChange,
  readMachineView,
  setMachineView,
} from "../../lib/appearance"
import { WRAP } from "../../site"

export const Route = createFileRoute("/docs/$slug")({
  loader: ({ params }) => {
    const page = pageBySlug(params.slug)
    if (!page) throw notFound()
    return page
  },
  head: ({ params }) => {
    const page = pageBySlug(params.slug)
    if (!page) return {}
    const meta = metaForPath(`/docs/${params.slug}`)
    const group = DOC_GROUPS.find((g) => g.slug === page.group)
    return {
      meta: [
        { title: meta.title },
        { name: "description", content: meta.description },
        { property: "og:title", content: meta.title },
        { property: "og:description", content: meta.description },
        { property: "og:type", content: "article" },
        { property: "og:url", content: meta.canonical },
        { name: "twitter:card", content: "summary_large_image" },
        { name: "twitter:title", content: meta.title },
        { name: "twitter:description", content: meta.description },
        { tagName: "link", rel: "canonical", href: meta.canonical },
        {
          "script:ld+json": JSON.stringify({
            "@context": "https://schema.org",
            "@type": "TechArticle",
            headline: page.title,
            description: page.description,
            articleSection: group?.title,
            keywords: page.tags.join(", "),
            url: meta.canonical,
            isPartOf: { "@type": "TechSite", name: "Lithon docs" },
          }),
        },
      ],
    }
  },
  component: DocsPage,
})

function useMachineViewPref() {
  const [on, setOn] = useState(false)
  useEffect(() => {
    setOn(readMachineView())
    return onMachineViewChange(setOn)
  }, [])
  return [on, (next: boolean) => setMachineView(next)] as const
}

function SidebarNav({
  currentSlug,
  onNavigate,
}: {
  currentSlug: string
  onNavigate?: () => void
}) {
  return (
    <nav aria-label="Documentation" className="space-y-5">
      {DOC_GROUPS.map((group) => {
        const pages = flatPageOrder().filter((p) => p.group === group.slug)
        if (pages.length === 0) return null
        return (
          <div key={group.slug}>
            <p className="mb-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              {group.title}
            </p>
            <ul className="space-y-0.5">
              {pages.map((page) => {
                const active = page.slug === currentSlug
                return (
                  <li key={page.slug}>
                    <Link
                      to="/docs/$slug"
                      params={{ slug: page.slug }}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={
                        active
                          ? "block rounded-md bg-primary/15 px-2 py-1 text-sm font-medium text-foreground"
                          : "block rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
                      }
                    >
                      {page.title}
                    </Link>
                  </li>
                )
              })}
            </ul>
          </div>
        )
      })}
    </nav>
  )
}

function DocsPage() {
  const page = Route.useLoaderData()
  const [machineView, setMachineViewPref] = useMachineViewPref()
  const order = flatPageOrder()
  const index = order.findIndex((p) => p.slug === page.slug)
  const prev = index > 0 ? order[index - 1] : undefined
  const next = index < order.length - 1 ? order[index + 1] : undefined

  const contextValue = useMemo(() => machineView, [machineView])

  return (
    <MachineViewContext.Provider value={contextValue}>
      <div className={`${WRAP} grid gap-8 py-8 lg:grid-cols-[220px_1fr_180px]`}>
        <aside className="hidden lg:block">
          <div className="sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto pe-2">
            <SidebarNav currentSlug={page.slug} />
          </div>
        </aside>

        <article className="min-w-0">
          <div className="mb-8 flex items-start justify-between gap-3 lg:hidden">
            <Sheet>
              <SheetTrigger
                render={
                  <Button
                    variant="outline"
                    size="sm"
                    aria-label="Open docs navigation"
                  />
                }
              >
                <ListIcon />
                Docs menu
              </SheetTrigger>
              <SheetContent side="right" className="w-72 overflow-y-auto">
                <SheetTitle className="sr-only">Documentation</SheetTitle>
                <SheetDescription className="sr-only">
                  All documentation pages
                </SheetDescription>
                <SidebarNav
                  currentSlug={page.slug}
                  onNavigate={() => document.body.click()}
                />
              </SheetContent>
            </Sheet>
            <Button
              variant={machineView ? "default" : "outline"}
              size="sm"
              onClick={() => setMachineViewPref(!machineView)}
              aria-pressed={machineView}
            >
              <CpuIcon />
              Machine view
            </Button>
          </div>

          <header className="mb-8 border-b pb-6">
            <Badge variant="secondary" className="mb-3">
              {DOC_GROUPS.find((g) => g.slug === page.group)?.title ??
                page.group}
            </Badge>
            <h1 className="font-heading text-3xl font-extrabold tracking-tight">
              {page.title}
            </h1>
            <p className="mt-2 text-muted-foreground">{page.description}</p>
            <DocsBadgeRow tags={page.tags} />
            {machineView && (
              <p className="mt-4 rounded-lg border border-accent-strong/30 bg-accent/10 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
                <b className="text-foreground">Machine view is on.</b> Every
                example below opens on its IR / x86-64 tab instead of the
                source. Toggle it back from the sidebar or the command palette.
              </p>
            )}
          </header>

          <div className="space-y-10">
            {page.sections.map((section) => (
              <section
                key={section.id}
                id={section.id}
                className="scroll-mt-24"
              >
                <h2 className="mb-4 font-heading text-xl font-bold">
                  {section.title}
                </h2>
                <div className="space-y-4">
                  {section.blocks.map((block, i) => (
                    <BlockView key={i} block={block} />
                  ))}
                </div>
              </section>
            ))}
          </div>

          <nav
            aria-label="Page navigation"
            className="mt-12 flex items-stretch justify-between gap-3 border-t pt-6"
          >
            {prev ? (
              <Link
                to="/docs/$slug"
                params={{ slug: prev.slug }}
                className="group flex max-w-[45%] flex-col gap-0.5 text-sm"
              >
                <span className="inline-flex items-center gap-1 text-muted-foreground">
                  <ArrowLeftIcon className="size-3.5 rtl:rotate-180" />
                  Previous
                </span>
                <span className="font-medium group-hover:text-accent-strong">
                  {prev.title}
                </span>
              </Link>
            ) : (
              <span />
            )}
            {next ? (
              <Link
                to="/docs/$slug"
                params={{ slug: next.slug }}
                className="group flex max-w-[45%] flex-col gap-0.5 text-end text-sm"
              >
                <span className="inline-flex items-center justify-end gap-1 text-muted-foreground">
                  Next
                  <ArrowRightIcon className="size-3.5 rtl:rotate-180" />
                </span>
                <span className="font-medium group-hover:text-accent-strong">
                  {next.title}
                </span>
              </Link>
            ) : (
              <span />
            )}
          </nav>
        </article>

        <aside className="hidden lg:block">
          <div className="sticky top-20 space-y-4">
            <div>
              <p className="mb-2 text-xs font-semibold text-muted-foreground">
                On this page
              </p>
              <nav
                aria-label="On this page"
                className="space-y-1 border-s ps-3 text-sm"
              >
                {page.sections.map((section) => (
                  <a
                    key={section.id}
                    href={`#${section.id}`}
                    className="block text-muted-foreground hover:text-foreground"
                  >
                    {section.title}
                  </a>
                ))}
              </nav>
            </div>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant={machineView ? "default" : "ghost"}
                    size="sm"
                    className="w-full justify-start"
                    onClick={() => setMachineViewPref(!machineView)}
                    aria-pressed={machineView}
                  />
                }
              >
                <CpuIcon />
                Machine view
                <span
                  className={`ms-auto size-1.5 rounded-full ${machineView ? "bg-accent-strong" : "bg-muted-foreground/40"}`}
                />
              </TooltipTrigger>
              <TooltipContent>
                Opens every example on its IR / x86-64 tab instead of the source
              </TooltipContent>
            </Tooltip>
          </div>
        </aside>
      </div>
    </MachineViewContext.Provider>
  )
}
