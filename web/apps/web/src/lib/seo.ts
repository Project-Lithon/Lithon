import { DOCS } from "../docs/registry"
import { DOC_GROUPS } from "../docs/model"

export const SITE = {
  name: "Lithon",
  tagline:
    "A zero-dependency, dual-tier execution engine that runs statically typed Python as native x86-64",
  description:
    "Lithon is a Python library and native x86-64 execution engine. Every supported program carries fixed-width type annotations, is statically verified before it runs, and executes as generated machine code with no LLVM and no runtime package.",
  repo: "https://github.com/Project-Lithon/Lithon",
  docsBase: "/docs",
} as const

/**
 * The canonical origin used for sitemap and canonical URLs.
 *
 * The site is deployed to GitHub Pages from the repository's `master`
 * branch, so the canonical home is the Pages URL for the `Lithon`
 * repository. `SITE_ORIGIN` overrides it for a fork or a custom domain;
 * `BASE_PATH` covers the sub-path a fork would be served from.
 */
export const SITEMAP_HOST =
  process.env.SITE_ORIGIN ?? "https://project-lithon.github.io/Lithon"

/** The path the site is served from, always with a leading and trailing slash. */
export const SITE_BASE_PATH = (() => {
  const explicit = process.env.BASE_PATH
  if (explicit) {
    const trimmed = explicit.replace(/\/+$/, "")
    return trimmed === "" ? "/" : `${trimmed}/`
  }
  if (process.env.CUSTOM_DOMAIN) return "/"
  const repo = process.env.GITHUB_REPOSITORY?.split("/")[1] ?? "Lithon"
  return `/${repo}/`
})()

export type PageMeta = {
  title: string
  description: string
  canonical: string
  /** Short factual summary used for structured data and AI answers. */
  facts: string[]
  keywords: string[]
}

export function originFor(): string {
  if (typeof window !== "undefined") return window.location.origin
  return SITEMAP_HOST
}

export function metaForPath(pathname: string): PageMeta {
  const clean = pathname.replace(/\/+$/, "") || "/"
  const site = originFor()

  if (clean === "/" || clean === "") {
    return {
      title: `${SITE.name} | native execution for typed Python`,
      description: SITE.description,
      canonical: `${site}/`,
      facts: [
        "Zero third-party dependencies: no LLVM, no Cranelift, no runtime package",
        "Dual-tier: native x86-64 with an honest interpreter fallback under --strict",
        "Fixed-width types: int[8..64], float[64], bool, bounded str[N], typed pointers",
        "33/33 CTest, 18,379 encoder cases with 0 incorrect",
      ],
      keywords: ["lithon", "python", "native", "x86-64", "compiler", "jit"],
    }
  }

  if (clean === "/playground") {
    return {
      title: `Playground | run Lithon against the real frontend`,
      description:
        "Write Lithon in the browser with syntax highlighting and autocomplete, and read the typed IR the engine emits for it. The live compiler runs in local development; the built-in examples carry their recorded IR.",
      canonical: `${site}/playground`,
      facts: [
        "The built-in examples show the real frontend output",
        "The live runner is wired up in `bun run dev`",
        "Syntax highlighting and hover documentation for the whole type language",
      ],
      keywords: ["lithon", "playground", "repl", "compiler", "online"],
    }
  }

  if (clean === "/benchmarks") {
    return {
      title: `Benchmarks | CPython vs Lithon, recorded`,
      description:
        "Recorded runs of the same program on CPython and the Lithon native tier, including terminal video and byte-identical output.",
      canonical: `${site}/benchmarks`,
      facts: [
        "1,000,000,000-iteration sum: 188.56 s on CPython, 1.31 s on Lithon",
        "Both engines print the same value",
      ],
      keywords: ["lithon", "benchmark", "performance", "cpython", "speedup"],
    }
  }

  if (clean === "/changelog") {
    return {
      title: `Changelog | Lithon`,
      description:
        "Every shipped change to the Lithon language, engine, toolchain and website, newest first. Each entry links to the documentation that covers it.",
      canonical: `${site}/changelog`,
      facts: [
        "Language, engine, toolchain, verification, docs and build changes",
        "Each entry links to the documentation that covers it",
        "Built from the repository's own commit history",
      ],
      keywords: [
        "lithon",
        "changelog",
        "release notes",
        "history",
        "what changed",
      ],
    }
  }

  if (clean === "/next") {
    return {
      title: `Next actions | Lithon`,
      description:
        "The open items: the language gaps, the engine work, the toolchain debt, and the recently shipped changes that closed them.",
      canonical: `${site}/next`,
      facts: [
        "Every open item is a design decision, not a bug",
        "Each item names the phase it belongs to",
        "The recently shipped list is the changelog in reverse",
      ],
      keywords: [
        "lithon",
        "roadmap",
        "next",
        "todo",
        "planned",
        "contribute",
      ],
    }
  }

  const docMatch = /^\/docs\/([\w-]+)/.exec(clean)
  if (docMatch) {
    const page = DOCS.pages.find((p) => p.slug === docMatch[1])
    if (page) {
      const group = DOC_GROUPS.find((g) => g.slug === page.group)
      return {
        title: `${page.title} | Lithon docs`,
        description: page.description,
        canonical: `${site}/docs/${page.slug}`,
        facts: [
          ...page.tags.slice(0, 5),
          `${page.sections.length} sections`,
          group ? `Part of ${group.title}` : "",
        ].filter(Boolean),
        keywords: ["lithon", "docs", page.group, ...page.tags.slice(0, 6)],
      }
    }
  }

  if (clean === "/docs") {
    return {
      title: `Documentation | Lithon`,
      description:
        "The full Lithon documentation: language reference, engine internals, verification, and machine-level views of IR and x86-64.",
      canonical: `${site}/docs`,
      facts: [
        `${DOCS.pages.length} pages across ${DOC_GROUPS.length} groups`,
        "Every example is compiled by the real Lithon frontend",
      ],
      keywords: ["lithon", "docs", "documentation", "reference"],
    }
  }

  return {
    title: `${SITE.name}`,
    description: SITE.description,
    canonical: `${site}${clean}`,
    facts: [],
    keywords: ["lithon"],
  }
}

/**
 * JSON-LD describing the project, for search engines and AI answer engines.
 *
 * Every route that calls `metaForPath` should also emit this, so a page is
 * never invisible to a structured-data reader.
 */
export function structuredData(pathname: string) {
  const meta = metaForPath(pathname)
  const site = originFor()
  const isDoc = pathname.startsWith("/docs")
  return {
    "@context": "https://schema.org",
    "@type": isDoc ? "TechArticle" : "WebPage",
    name: meta.title,
    headline: meta.title,
    description: meta.description,
    url: meta.canonical,
    keywords: meta.keywords.join(", "),
    inLanguage: "en",
    isPartOf: {
      "@type": "WebSite",
      name: SITE.name,
      url: site,
    },
    about: {
      "@type": "SoftwareApplication",
      name: "Lithon",
      applicationCategory: "DeveloperApplication",
      operatingSystem: "x86-64 Linux, Windows",
      codeRepository: SITE.repo,
      programmingLanguage: "Python",
      description: SITE.description,
    },
    // The factual summary an answer engine can quote without parsing prose.
    ...(meta.facts.length
      ? {
          "abstract": meta.facts.join(" · "),
          "disambiguatingDescription": meta.facts.join(" · "),
        }
      : {}),
  }
}

/** The `script:ld+json` tag for a route, ready to drop into a `head()`. */
export function jsonLdTag(pathname: string) {
  return {
    "script:ld+json": JSON.stringify(structuredData(pathname)),
  }
}

/**
 * The same JSON-LD, as a `head()` entry. TanStack's `meta` array only
 * accepts `<meta>` elements, so the script tag goes in `scripts`.
 */
export function jsonLdScript(pathname: string) {
  return {
    type: "application/ld+json",
    children: JSON.stringify(structuredData(pathname)),
  }
}
