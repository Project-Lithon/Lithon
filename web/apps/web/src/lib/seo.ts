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

/** Canonical origin used for sitemap and canonical URLs. */
export const SITEMAP_HOST =
  process.env.SITE_ORIGIN ?? "https://project-lithon.github.io/Lithon"

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
        "Fixed-width types: int[8..64], float[64], bool, typed pointers",
        "32/32 CTest, 9,239 encoder cases with 0 incorrect",
      ],
      keywords: ["lithon", "python", "native", "x86-64", "compiler", "jit"],
    }
  }

  if (clean === "/playground") {
    return {
      title: `Playground | run Lithon against the real frontend`,
      description:
        "Write Lithon in the browser with syntax highlighting and autocomplete, then run it through this repository's actual type checker and frontend to get the typed IR back.",
      canonical: `${site}/playground`,
      facts: [
        "Runs tools/typecheck.py and src/frontend/frontend.py locally",
        "Returns the real typed IR for valid programs",
        "Shows the engine's own diagnostic for invalid ones",
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

/** JSON-LD describing the project, for search engines and AI answer engines. */
export function structuredData(pathname: string) {
  const meta = metaForPath(pathname)
  const site = originFor()
  return {
    "@context": "https://schema.org",
    "@type": pathname.startsWith("/docs") ? "TechArticle" : "WebSite",
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
  }
}
