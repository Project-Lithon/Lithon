import type {
  DocGroup,
  DocPage,
  DocsRegistry,
  PagePriority,
} from "./model"
import { PAGES } from "./pages"
import { PAGE_OUTCOME, PAGE_PRIORITY } from "./priority"
import { buildEntries } from "./search-index"

export const DOCS: DocsRegistry = {
  pages: PAGES,
  entries: buildEntries(),
}

/** The priority of a page, defaulting to `recommended` for an unlisted one. */
export function priorityOf(page: DocPage): PagePriority {
  return PAGE_PRIORITY[page.slug] ?? page.priority ?? "recommended"
}

/** The one-line outcome of a page, if it has one. */
export function outcomeOf(page: DocPage): string | undefined {
  return PAGE_OUTCOME[page.slug] ?? page.outcome
}
/** Every page in reading order, with its priority resolved. */
export function rankedPages(): readonly {
  page: DocPage
  priority: PagePriority
  outcome: string | undefined
}[] {
  const rank: Record<PagePriority, number> = {
    essential: 0,
    recommended: 1,
    reference: 2,
  }
  return PAGES.map((page) => ({
    page,
    priority: priorityOf(page),
    outcome: outcomeOf(page),
  })).sort((a, b) => {
    const byPriority = rank[a.priority] - rank[b.priority]
    if (byPriority !== 0) return byPriority
    return PAGES.indexOf(a.page) - PAGES.indexOf(b.page)
  })
}

export function pageBySlug(slug: string): DocPage | undefined {
  return PAGES.find((page) => page.slug === slug)
}

export function flatPageOrder(): readonly DocPage[] {
  return PAGES
}

export { DOC_GROUPS } from "./model"
export type { DocGroup }
