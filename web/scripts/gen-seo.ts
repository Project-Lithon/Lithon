import { existsSync, mkdirSync, writeFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"

import { DOCS } from "../apps/web/src/docs/registry"
import { DOC_GROUPS } from "../apps/web/src/docs/model"

/**
 * Writes public/sitemap.xml and public/robots.txt. Search engines and AI
 * answer engines both fetch these, so they are generated from the same
 * registry the site renders.
 *
 *   bun scripts/gen-seo.ts [origin]
 *
 * The origin defaults to the same constant the app uses for its canonical
 * URLs (see apps/web/src/lib/seo.ts), so the sitemap, the robots.txt and
 * the <link rel="canonical"> tags can never drift apart. CI passes the
 * deployed Pages URL explicitly.
 */

const WEB_ROOT = resolve(
  dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")),
  ".."
)
const PUBLIC = join(WEB_ROOT, "apps", "web", "public")

const DEFAULT_ORIGIN = "https://project-lithon.github.io/Lithon"
const origin = (process.argv[2] ?? process.env.SITE_ORIGIN ?? DEFAULT_ORIGIN).replace(
  /\/+$/,
  ""
)

const STATIC_PATHS = [
  "/",
  "/docs",
  "/playground",
  "/benchmarks",
  "/roadmap",
  "/use-cases",
  "/changelog",
  "/next",
  "/roadmap/language",
  "/roadmap/phase-1-dual-tier",
  "/roadmap/phase-2-aot",
  "/roadmap/phase-3-systems",
  "/roadmap/verification",
]

const all = [
  ...STATIC_PATHS,
  ...DOCS.pages.map((p) => `/docs/${p.slug}`),
]

// docs pages get their own entries; landing pages get a slightly higher weight
const weightFor = (path: string) => {
  if (path === "/") return "1.0"
  if (path === "/docs") return "0.9"
  if (path.startsWith("/docs/")) return "0.8"
  if (path === "/changelog" || path === "/next") return "0.7"
  if (path === "/playground") return "0.7"
  if (path === "/benchmarks") return "0.7"
  return "0.6"
}

// The last-modified date of the tree, so a crawler gets a freshness signal.
const lastmod = new Date().toISOString().slice(0, 10)

const urls = all
  .map(
    (path) => `  <url>
    <loc>${origin}${path}</loc>
    <lastmod>${lastmod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>${weightFor(path)}</priority>
  </url>`
  )
  .join("\n")

const sitemap = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls}
</urlset>
`

const robots = `User-agent: *
Allow: /
Disallow: /api/

# The AI-facing surfaces: a compact index and the full documentation.
# Both are generated from the same registry the site renders.

Sitemap: ${origin}/sitemap.xml
`

mkdirSync(PUBLIC, { recursive: true })
writeFileSync(join(PUBLIC, "sitemap.xml"), sitemap, "utf8")
writeFileSync(join(PUBLIC, "robots.txt"), robots, "utf8")

console.log(
  `sitemap.xml: ${all.length} urls across ${DOC_GROUPS.length} doc groups (origin ${origin})`
)
console.log(`robots.txt written`)
if (!existsSync(PUBLIC)) process.exit(1)
