/**
 * Generates the AI-facing surfaces of the site:
 *
 *   public/llms.txt        - a compact index, the llms.txt convention
 *   public/llms-full.txt   - the whole documentation as one plain-text file
 *
 * Both are built from the same registry the site renders, so they cannot
 * drift from what a reader sees.
 *
 * Run: bun scripts/gen-llms.ts
 */
import { mkdirSync, writeFileSync } from "node:fs"
import { join } from "node:path"

import { DOC_GROUPS } from "../apps/web/src/docs/model"
import { DOCS, outcomeOf, priorityOf } from "../apps/web/src/docs/registry"
import { SITE, SITEMAP_HOST } from "../apps/web/src/lib/seo"
import { CHANGES } from "../apps/web/src/changelog/data"

const PUBLIC = join("apps", "web", "public")

/** Strips the inline markup a doc block carries, leaving readable prose. */
function plain(text: string): string {
  return text
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1 ($2)")
    .trim()
}

/** Flattens one block into plain-text lines. */
function blockLines(block: unknown): string[] {
  if (!block || typeof block !== "object") return []
  const b = block as Record<string, unknown>
  const out: string[] = []
  switch (b.kind) {
    case "p":
      if (typeof b.text === "string") out.push(plain(b.text))
      break
    case "note":
    case "callout":
      out.push(
        `> **${plain(String(b.title ?? ""))}** — ${plain(String(b.text ?? ""))}`,
      )
      break
    case "quote":
      out.push(`> ${plain(String(b.text ?? ""))}`)
      if (typeof b.source === "string") out.push(`> — ${plain(b.source)}`)
      break
    case "list": {
      const items = (b.items as string[] | undefined) ?? []
      for (const item of items) out.push(`- ${plain(item)}`)
      break
    }
    case "table":
    case "comparison": {
      if (typeof b.caption === "string") out.push(`*${plain(b.caption)}*`)
      const rows = (b.rows as unknown[][] | undefined) ?? []
      const header = rows.length > 0 ? (rows[0] as unknown[]) : []
      if (header.length) {
        out.push(`| ${header.map((c) => plain(String(c))).join(" | ")} |`)
        out.push(`| ${header.map(() => "---").join(" | ")} |`)
        for (const row of rows.slice(1)) {
          out.push(
            `| ${(row as unknown[]).map((c) => plain(String(c))).join(" | ")} |`,
          )
        }
      }
      break
    }
    case "deflist": {
      if (typeof b.caption === "string") out.push(`*${plain(b.caption)}*`)
      for (const item of (b.items as { term: string; detail: string }[]) ?? []) {
        out.push(`- **${plain(item.term)}** — ${plain(item.detail)}`)
      }
      break
    }
    case "layout": {
      if (typeof b.caption === "string") out.push(`*${plain(b.caption)}*`)
      out.push(`Total size: ${b.size}`)
      for (const f of (b.fields as { name: string; offset: string; size: string }[]) ?? []) {
        out.push(`- ${f.name} @ ${f.offset} (${f.size})`)
      }
      break
    }
    case "steps":
    case "cards":
    case "linkcards": {
      for (const item of (b.items as { title: string; text: string }) ?? []) {
        out.push(`- **${plain(item.title)}** — ${plain(item.text)}`)
      }
      break
    }
    case "code": {
      const ex = b.example as {
        title?: string
        lang?: string
        source: string
        output?: string
        note?: string
        expect?: string
        errorMatch?: string
        sourceRef?: string
      }
      if (ex.title) out.push(`**${plain(ex.title)}**`)
      out.push("```" + (ex.lang === "lithon" ? "python" : (ex.lang ?? "text")))
      out.push(ex.source)
      out.push("```")
      if (ex.expect === "error" && ex.errorMatch) {
        out.push(`Expected to be refused with: \`${ex.errorMatch}\``)
      } else if (ex.output) {
        out.push("Output:")
        out.push("```")
        out.push(ex.output)
        out.push("```")
      }
      if (ex.note) out.push(`*${plain(ex.note)}*`)
      if (ex.sourceRef) out.push(`Source: \`${ex.sourceRef}\``)
      break
    }
    case "tabs": {
      if (typeof b.caption === "string") out.push(`*${plain(b.caption)}*`)
      for (const item of (b.items as { label: string; blocks: unknown[] }) ?? []) {
        out.push(`#### ${plain(item.label)}`)
        for (const inner of item.blocks) out.push(...blockLines(inner))
      }
      break
    }
    default:
      break
  }
  return out
}

// ---------------------------------------------------------------- llms.txt

const llms: string[] = [
  `# ${SITE.name}`,
  "",
  `> ${SITE.description}`,
  "",
  `Lithon is a Python library and native x86-64 execution engine. Source files use the \`.lio\` extension. Every program is statically verified before it runs; a program the backend cannot prove is refused rather than guessed at.`,
  "",
  "## The one rule",
  "",
  "> If Lithon cannot prove that a program satisfies the rules required by its native backend, it refuses native compilation rather than guessing.",
  "",
  "## Type system in one block",
  "",
  "```",
  "int[8]  int[16]  int[32]  int[64]   signed, fixed width",
  "float[64]                        IEEE-754 binary64 (float[32] is refused)",
  "bool                              1 byte, reads back as 0/1",
  "str[N]                            N bytes, NUL-terminated, bounded",
  "list[T, N]                        fixed capacity N",
  "tuple[T, N]                       fixed length N, homogeneous",
  "dict[K, V, B]                     B buckets, B must be a power of two",
  "ptr[T]                            typed pointer, variable name needs a leading _",
  "class P(Struct)                   packed fields, no padding",
  "class E(Enum)                    a tag plus the largest payload",
  "```",
  "",
  "There is no bare `int`, no `uint`, no `None`, no `lambda`, no closure, no `set`, no `bytes`, no `Optional`/`Union`, no `**`, no `//`, no `<=`/`>=`, and no chained comparison.",
  "",
  "## A small program",
  "",
  "```python",
  "x: int[64] = 10",
  "y: int[64] = 20",
  "",
  "result: int[64] = x + y",
  "",
  "print(result)",
  "```",
  "",
  "## Documentation index",
  "",
]

for (const group of DOC_GROUPS) {
  const pages = DOCS.pages.filter((p) => p.group === group.slug)
  if (pages.length === 0) continue
  llms.push(`### ${group.title}`)
  llms.push("")
  if (group.blurb) {
    llms.push(group.blurb)
    llms.push("")
  }
  for (const page of pages) {
    const priority = priorityOf(page)
    const marker =
      priority === "essential" ? "[essential]" : priority === "reference" ? "[reference]" : ""
    const outcome = outcomeOf(page) ?? page.description
    llms.push(`- [${page.title}](${SITEMAP_HOST}/docs/${page.slug})${marker} — ${outcome}`)
  }
  llms.push("")
}

llms.push("### Site sections")
llms.push("")
for (const [label, path, blurb] of [
  ["Changelog", "/changelog", "Every shipped change, newest first."],
  ["Next actions", "/next", "The open items, item by item."],
  ["Roadmap", "/roadmap", "The three phases and their milestones."],
  ["Benchmarks", "/benchmarks", "Recorded CPython vs Lithon runs."],
  ["Use cases", "/use-cases", "The problems Lithon is shaped for."],
  ["Playground", "/playground", "Write Lithon in the browser."],
] as const) {
  llms.push(`- [${label}](${SITEMAP_HOST}${path}) — ${blurb}`)
}
llms.push("")
llms.push("## Optional")
llms.push("")
llms.push(
  `- [Full documentation as plain text](${SITEMAP_HOST}/llms-full.txt) — every page, every section, every example.`,
)
llms.push(
  `- [The repository](${SITE.repo}) — the source, the tests, and the verification suites.`,
)
llms.push("")

writeFileSync(join(PUBLIC, "llms.txt"), llms.join("\n"), "utf8")

// ------------------------------------------------------------ llms-full.txt

const full: string[] = [
  `# ${SITE.name} — full documentation`,
  "",
  `> ${SITE.description}`,
  "",
  `Generated from the site's own docs registry. Every example below is verified against the real Lithon frontend.`,
  "",
  "---",
  "",
]

for (const group of DOC_GROUPS) {
  const pages = DOCS.pages.filter((p) => p.group === group.slug)
  if (pages.length === 0) continue
  full.push(`## ${group.title}`)
  full.push("")
  if (group.blurb) {
    full.push(group.blurb)
    full.push("")
  }
  for (const page of pages) {
    full.push(`### ${page.title}`)
    full.push("")
    full.push(`*${page.description}*`)
    full.push("")
    full.push(`URL: ${SITEMAP_HOST}/docs/${page.slug}`)
    full.push("")
    for (const section of page.sections) {
      full.push(`#### ${section.title}`)
      full.push("")
      for (const block of section.blocks) {
        const lines = blockLines(block)
        if (lines.length) {
          full.push(...lines)
          full.push("")
        }
      }
    }
    full.push("---")
    full.push("")
  }
}

full.push("## Changelog")
full.push("")
for (const change of CHANGES) {
  full.push(
    `### ${change.title}${change.date ? ` (${change.date})` : ""}`,
  )
  full.push("")
  full.push(plain(change.detail))
  if (change.href) {
    full.push("")
    full.push(`Docs: ${SITEMAP_HOST}${change.href}`)
  }
  full.push("")
}

writeFileSync(join(PUBLIC, "llms-full.txt"), full.join("\n"), "utf8")

mkdirSync(PUBLIC, { recursive: true })
console.log(
  `wrote llms.txt (${llms.length} lines) and llms-full.txt (${full.length} lines)`,
)
