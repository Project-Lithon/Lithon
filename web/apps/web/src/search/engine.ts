import type { SearchEntry } from "../docs/model"

/** Score one entry against a query. 0 = no match. Higher = better. */
export function scoreEntry(entry: SearchEntry, query: string): number {
  const q = query.trim().toLowerCase()
  if (!q) return 0
  const terms = q.split(/\s+/).filter(Boolean)
  let total = 0
  for (const term of terms) {
    const s = scoreTerm(entry, term)
    if (s === 0) return 0
    total += s
  }
  return total
}

function scoreTerm(entry: SearchEntry, term: string): number {
  const title = entry.title.toLowerCase()
  const subtitle = entry.subtitle?.toLowerCase() ?? ""
  const tags = entry.tags.map((t) => t.toLowerCase())
  const group = entry.group?.toLowerCase() ?? ""
  const body = entry.body?.toLowerCase() ?? ""

  if (title === term) return 100
  if (title.startsWith(term)) return 80
  if (title.includes(term)) return 60
  for (const tag of tags) {
    if (tag === term) return 55
    if (tag.startsWith(term)) return 50
    if (tag.includes(term)) return 40
  }
  if (subtitle.includes(term)) return 30
  if (group.includes(term)) return 20
  // The body is the last resort before the fuzzy match, so an exact hit in
  // the prose still beats a subsequence hit in the title.
  if (body) {
    if (body.includes(term)) return 18
    // an error code like E0301, or a symbol like `wrap_add`
    if (new RegExp(`\\b${escapeRegExp(term)}\\b`).test(body)) return 25
  }
  if (fuzzy(title, term)) return 15
  if (body && fuzzy(body, term)) return 8
  return 0
}

function escapeRegExp(text: string) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

function fuzzy(haystack: string, needle: string): boolean {
  let i = 0
  for (const ch of haystack) {
    if (ch === needle[i]) i++
    if (i === needle.length) return true
  }
  return false
}

export function searchEntries(
  entries: readonly SearchEntry[],
  query: string,
  limit = 40
): SearchEntry[] {
  const scored: { entry: SearchEntry; score: number }[] = []
  for (const entry of entries) {
    const score = scoreEntry(entry, query)
    if (score > 0) scored.push({ entry, score })
  }
  scored.sort((a, b) => b.score - a.score)
  return scored.slice(0, limit).map((s) => s.entry)
}
