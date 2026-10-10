import type { DocPage, DocsRegistry, SearchEntry } from "./model"
import { PAGES } from "./pages"

function pageEntries(pages: readonly DocPage[]): SearchEntry[] {
  const entries: SearchEntry[] = []
  for (const page of pages) {
    entries.push({
      id: `page:${page.slug}`,
      kind: "page",
      title: page.title,
      subtitle: page.description,
      group: page.group,
      tags: page.tags,
      href: `/docs/${page.slug}`,
    })
    for (const section of page.sections) {
      entries.push({
        id: `section:${page.slug}#${section.id}`,
        kind: "section",
        title: section.title,
        subtitle: page.title,
        group: page.group,
        tags: [...page.tags, ...page.tags.slice(0, 2)],
        href: `/docs/${page.slug}#${section.id}`,
      })
    }
  }
  return entries
}

export const DOCS: DocsRegistry = {
  pages: PAGES,
  entries: pageEntries(PAGES),
}

export function pageBySlug(slug: string): DocPage | undefined {
  return PAGES.find((page) => page.slug === slug)
}

export function flatPageOrder(): readonly DocPage[] {
  return PAGES
}
