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

import { DOC_GROUPS } from "../../docs/model"
import { flatPageOrder, pageBySlug } from "../../docs/registry"
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
            <Button
              variant={machineView ? "default" : "ghost"}
              size="sm"
              className="w-full justify-start"
              onClick={() => setMachineViewPref(!machineView)}
              aria-pressed={machineView}
            >
              <CpuIcon />
              Machine view
            </Button>
          </div>
        </aside>
      </div>
    </MachineViewContext.Provider>
  )
}
