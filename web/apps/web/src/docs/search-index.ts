import type { DocPage, SearchEntry } from "./model"
import { PAGES } from "./pages"

/**
 * Extracts the searchable prose out of a docs page.
 *
 * The registry holds the documentation as structured data, so the text a
 * reader can see is already there -- it just has to be flattened into a
 * string the search engine can match against.
 */
export function pageBody(page: DocPage): string {
  const parts: string[] = [page.title, page.description, ...page.tags]
  for (const section of page.sections) {
    parts.push(section.title)
    for (const block of section.blocks) {
      parts.push(...blockText(block))
    }
  }
  return parts.join(" \n ")
}

function blockText(block: unknown): string[] {
  if (!block || typeof block !== "object") return []
  const b = block as Record<string, unknown>
  const out: string[] = []
  switch (b.kind) {
    case "p":
    case "quote":
      if (typeof b.text === "string") out.push(b.text)
      break
    case "note":
    case "callout":
      if (typeof b.title === "string") out.push(b.title)
      if (typeof b.text === "string") out.push(b.text)
      break
    case "list":
      if (Array.isArray(b.items)) {
        for (const item of b.items) if (typeof item === "string") out.push(item)
      }
      break
    case "table":
    case "comparison":
      if (Array.isArray(b.rows)) {
        for (const row of b.rows) {
          if (Array.isArray(row)) {
            for (const cell of row) if (typeof cell === "string") out.push(cell)
          }
        }
      }
      if (typeof b.caption === "string") out.push(b.caption)
      break
    case "deflist":
      if (Array.isArray(b.items)) {
        for (const item of b.items as { term: string; detail: string }[]) {
          out.push(item.term, item.detail)
        }
      }
      break
    case "layout":
      if (Array.isArray(b.fields)) {
        for (const f of b.fields as { name: string }[]) out.push(f.name)
      }
      break
    case "steps":
    case "cards":
    case "linkcards": {
      for (const item of itemsOf(b)) out.push(item.title, item.text)
      break
    }
    case "code": {
      const ex = b.example as { title?: string; source?: string; output?: string }
      if (ex.title) out.push(ex.title)
      if (ex.source) out.push(ex.source)
      if (ex.output) out.push(ex.output)
      break
    }
    case "tabs": {
      for (const item of tabsOf(b)) {
        out.push(item.label)
        for (const inner of item.blocks) out.push(...blockText(inner))
      }
      break
    }
    default:
      break
  }
  return out
}

/** The `{ title, text }` items of a steps / cards / linkcards block. */
function itemsOf(b: Record<string, unknown>): { title: string; text: string }[] {
  const items = b.items
  if (!Array.isArray(items)) return []
  return items.filter(
    (item): item is { title: string; text: string } =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as { title?: unknown }).title === "string" &&
      typeof (item as { text?: unknown }).text === "string",
  )
}

/** The tabs of a tabs block. */
function tabsOf(
  b: Record<string, unknown>,
): { label: string; blocks: unknown[] }[] {
  const items = b.items
  if (!Array.isArray(items)) return []
  return items.filter(
    (item): item is { label: string; blocks: unknown[] } =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as { label?: unknown }).label === "string" &&
      Array.isArray((item as { blocks?: unknown }).blocks),
  )
}

/** The searchable text of one section. */
export function sectionBody(
  section: DocPage["sections"][number],
): string {
  const parts: string[] = [section.title]
  for (const block of section.blocks) parts.push(...blockText(block))
  return parts.join(" \n ")
}

/** Every search entry, with the body text attached. */
export function buildEntries(): SearchEntry[] {
  const entries: SearchEntry[] = []
  for (const page of PAGES) {
    const body = pageBody(page)
    entries.push({
      id: `page:${page.slug}`,
      kind: "page",
      title: page.title,
      subtitle: page.description,
      group: page.group,
      tags: page.tags,
      href: `/docs/${page.slug}`,
      body,
    })
    for (const section of page.sections) {
      entries.push({
        id: `section:${page.slug}#${section.id}`,
        kind: "section",
        title: section.title,
        subtitle: page.title,
        group: page.group,
        tags: [...new Set([...page.tags, ...page.tags.slice(0, 2)])],
        href: `/docs/${page.slug}#${section.id}`,
        body: sectionBody(section),
      })
    }
  }
  return entries
}
