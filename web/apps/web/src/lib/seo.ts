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
  if (typeof window !== "undefined") {
    // On GitHub Pages the site lives under /<Repo>/ — window.location.origin
    // alone would emit canonicals missing the sub-path (404s in search).
    const base = SITE_BASE_PATH === "/" ? "" : SITE_BASE_PATH.replace(/\/$/, "")
    return `${window.location.origin}${base}`
  }
  return SITEMAP_HOST
}

/**
 * Per-page meta for the roadmap's five subpages.
 *
 * They all used to fall through to a single `startsWith("/roadmap")` block,
 * so six distinct pages shipped an identical `<title>` and description.
 * The copy here is the same wording the index page uses for each row.
 */
const ROADMAP_SUBPAGES: Record<string, Omit<PageMeta, "canonical">> = {
  "/roadmap/language": {
    title: "Language and semantics | Lithon",
    description:
      "Types and flow, the supported subset, and every place Lithon deliberately does not follow CPython: modulo, shifts, and the float rules.",
    facts: [
      "The supported subset, types and flow, and every deliberate divergence from CPython",
      "Modulo, shifts, and the float rules documented as implemented",
    ],
    keywords: ["lithon", "roadmap", "language", "semantics", "divergences"],
  },
  "/roadmap/phase-1-dual-tier": {
    title: "Phase I: the dual-tier JIT engine | Lithon",
    description:
      "The two execution tiers, the emitter, ABI and target-feature gating, the measured optimization pipeline, and all eight SSA phases with the reasoning behind each.",
    facts: [
      "Interpreter and native tier behind one call boundary",
      "All eight SSA phases with the reasoning behind each",
    ],
    keywords: ["lithon", "roadmap", "jit", "dual-tier", "ssa"],
  },
  "/roadmap/phase-2-aot": {
    title: "Phase II: AOT binary synthesis | Lithon",
    description:
      "Writing ELF64 and PE32+ headers by hand, staying under 10 KB, and the decoupling work that has to happen before a binary can stand on its own.",
    facts: [
      "ELF64 and PE32+ headers written by hand",
      "Staying under 10 KB with no runtime to link",
    ],
    keywords: ["lithon", "roadmap", "aot", "elf64", "pe32"],
  },
  "/roadmap/phase-3-systems": {
    title: "Phase III: systems and SIMD | Lithon",
    description:
      "Syscalls emitted from syntax, zero-copy pointers into native arrays, and the AVX2 path with the feature gating already built underneath it.",
    facts: [
      "Syscalls emitted from syntax and zero-copy pointers into native arrays",
      "AVX2 path with target-feature gating already in place",
    ],
    keywords: ["lithon", "roadmap", "syscalls", "simd", "avx2"],
  },
  "/roadmap/verification": {
    title: "Verification and benchmarks | Lithon",
    description:
      "The four checking layers from unit tests to differential fuzzing, how to isolate one optimization's effect, and what a green run does not prove.",
    facts: [
      "Four checking layers, from unit tests to differential fuzzing",
      "How to isolate one optimization's effect, and what a green run does not prove",
    ],
    keywords: ["lithon", "roadmap", "verification", "testing", "benchmarks"],
  },
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
        "35/35 CTest, 18,379 encoder cases with 0 incorrect",
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

  if (clean === "/use-cases") {
    return {
      title: "Use cases | Lithon",
      description:
        "Signal processing, dev tools, embedded and domain modelling: classes with inheritance verified statically, emitted natively.",
      canonical: `${site}/use-cases`,
      facts: [
        "Domain modelling: classes/inheritance/dunders, fixed slots, refcounted heap",
        "Signal processing: fixed-width ints, AVX2 vectorizer, syscall loopback",
        "Dev tools: zero-dependency engine, byte-identical tiers",
      ],
      keywords: ["lithon", "use cases", "classes", "oop"],
    }
  }

  if (clean.startsWith("/roadmap")) {
    // Guard on the key, not the value: TS narrows an indexed read from the
    // table's value type, so `if (sub)` alone reads as always-truthy.
    if (Object.hasOwn(ROADMAP_SUBPAGES, clean)) {
      return { ...ROADMAP_SUBPAGES[clean], canonical: `${site}${clean}` }
    }
    return {
      title: "Roadmap | Lithon",
      description:
        "Phased plan: dual-tier JIT (shipped), AOT synthesis (next), systems & SIMD (planned). OOP Phase 1 (4.10) is done.",
      canonical: `${site}${clean}`,
      facts: [
        "Phase I dual-tier shipped; Phase II AOT next; Phase III systems planned",
        "OOP 4.10.0-4.10.6 shipped 2026-11-05",
      ],
      keywords: ["lithon", "roadmap", "phases"],
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
    "script:ld+json": structuredData(pathname),
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
