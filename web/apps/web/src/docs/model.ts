export type Tone = "note" | "warn" | "danger" | "good"

export type TraceStep = {
  step: string
  detail: string
  state?: string
  out?: string
}

export type ExampleSpec = {
  title?: string
  lang?: "lithon" | "bash" | "text" | "asm"
  source: string
  /** Path inside the Lithon repo: the verification agent checks the source
   *  against this file byte-for-byte (modulo trailing whitespace). */
  sourceRef?: string
  /** Path to the expected stdout in the repo: agent checks it exists. */
  expectedRef?: string
  /** Real program output (from tests/.../expected or a real run). */
  output?: string
  /** What the real Lithon frontend must do with this program. */
  expect?: "ok" | "error"
  /** Which tool produces the documented diagnostic for expect: "error". */
  tool?: "typecheck" | "frontend"
  /** Substring the tool's diagnostic must contain (for expect: "error"). */
  errorMatch?: string
  /** Set when tools/typecheck.py legitimately lags the engine (while, containers, !=). */
  typecheck?: false
  /** Small caption rendered under the example. */
  note?: string
  trace?: TraceStep[]
  machine?: {
    ir?: string
    asm?: string
    note?: string
  }
}

export type Block =
  | { kind: "p"; text: string }
  | { kind: "list"; ordered?: boolean; items: string[] }
  | { kind: "code"; example: ExampleSpec }
  | {
      kind: "table"
      caption?: string
      rows: readonly (readonly [string, string])[]
    }
  | { kind: "note"; tone?: Tone; title: string; text: string }
  | { kind: "steps"; items: readonly { title: string; text: string }[] }
  | { kind: "cards"; items: readonly { title: string; text: string }[] }
  | {
      kind: "linkcards"
      items: readonly { title: string; text: string; href: string }[]
    }
  /**
   * A highlighted "remember this" box. Unlike `note`, a callout has no tone
   * axis: it is always the single most important idea on the page, so it
   * reads as a statement of fact rather than a caveat.
   */
  | { kind: "callout"; title: string; text: string }
  /**
   * A page-level tabset: the same concept shown several ways side by side
   * (CPython vs Lithon, one tier vs another, one width vs another).
   */
  | {
      kind: "tabs"
      caption?: string
      items: readonly {
        label: string
        blocks: readonly Block[]
      }[]
    }
  /**
   * A three-column comparison table. The middle column is the "answer"
   * (usually Lithon) and is rendered with emphasis.
   */
  | {
      kind: "comparison"
      caption?: string
      columns: readonly [string, string, string]
      rows: readonly (readonly [string, string, string])[]
    }
  /**
   * A definition list: term on the left, description on the right, no
   * header row. Used for API-shaped content (builtins, error codes, IR ops).
   */
  | {
      kind: "deflist"
      caption?: string
      items: readonly { term: string; detail: string }[]
    }
  /**
   * A byte-layout diagram: a run of fields with their offsets and sizes.
   */
  | {
      kind: "layout"
      caption?: string
      size: string
      fields: readonly { name: string; offset: string; size: string }[]
    }
  /**
   * A quoted principle or rule, set apart from the surrounding prose.
   */
  | { kind: "quote"; text: string; source?: string }

export type DocSection = {
  id: string
  title: string
  blocks: readonly Block[]
}

/** How prominently a page should be surfaced on the docs index. */
export type PagePriority = "essential" | "recommended" | "reference"

export type DocPage = {
  slug: string
  title: string
  description: string
  group: string
  tags: readonly string[]
  sections: readonly DocSection[]
  /**
   * Index-page emphasis. `essential` pages are the reading path and are
   * listed first; `reference` pages are looked up, not read end to end.
   */
  priority?: PagePriority
  /** One-line "what you will be able to do after this" summary. */
  outcome?: string
  /** Minutes of reading time, used as a "short read" marker on the index. */
  minutes?: number
}

export type DocGroup = {
  slug: string
  title: string
  order: number
  blurb?: string
}

export const DOC_GROUPS: readonly DocGroup[] = [
  {
    slug: "start",
    title: "Start here",
    order: 0,
    blurb: "From zero to a natively running program.",
  },
  {
    slug: "language",
    title: "The language",
    order: 1,
    blurb: "Types, flow, functions, containers, unsafe memory.",
  },
  {
    slug: "engine",
    title: "Inside the engine",
    order: 2,
    blurb: "Dual tiers, verification, SSA, optimization, SIMD.",
  },
  {
    slug: "reference",
    title: "Reference",
    order: 3,
    blurb: "Errors, builtins, verification gate, honest limits.",
  },
  {
    slug: "machine",
    title: "Machine view",
    order: 4,
    blurb: "For enthusiasts: IR, registers, x86-64 encodings.",
  },
]

export type SearchKind = "page" | "section" | "example" | "action"

export type SearchEntry = {
  id: string
  kind: SearchKind
  title: string
  subtitle?: string
  group?: string
  tags: readonly string[]
  href: string
  /**
   * The searchable body text. Populated for pages and sections so a query
   * can find an error code or an operator that only appears in the prose.
   */
  body?: string
}

export type ActionEntry = {
  id: string
  title: string
  subtitle?: string
  tags: readonly string[]
  run: string
}

/** Flat registry shape: everything in the system, tagged and searchable. */
export type DocsRegistry = {
  pages: readonly DocPage[]
  entries: readonly SearchEntry[]
}
