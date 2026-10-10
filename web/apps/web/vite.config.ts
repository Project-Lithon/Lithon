import { defineConfig } from "vite"
import { devtools } from "@tanstack/devtools-vite"
import { tanstackStart } from "@tanstack/react-start/plugin/vite"
import viteReact from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

import { lithonApi } from "./vite-lithon-api"
import { DOCS } from "./src/docs/registry"
import { SITEMAP_HOST } from "./src/lib/seo"

// GitHub Pages serves the site from /<repo>/ unless a custom domain is set.
const repoBase = `/${process.env.GITHUB_REPOSITORY?.split("/")[1] ?? "Lithon"}`
const base =
  process.env.NODE_ENV === "production"
    ? (process.env.BASE_PATH ?? (process.env.CUSTOM_DOMAIN ? "/" : repoBase))
    : "/"

const config = defineConfig({
  base,
  resolve: { tsconfigPaths: true },
  build: {
    outDir: "dist",
    // GitHub Pages has a 1MB asset ceiling warning; keep chunks split
    chunkSizeWarningLimit: 1200,
  },
  plugins: [
    devtools(),
    tailwindcss(),
    tanstackStart({
      // prerender every route to static HTML so Pages can host it
      prerender: {
        enabled: true,
        crawlLinks: true,
        autoStaticPathsDiscovery: true,
        failOnError: false,
        concurrency: 4,
      },
      pages: [
        { path: "/", prerender: { enabled: true } },
        { path: "/benchmarks", prerender: { enabled: true } },
        { path: "/playground", prerender: { enabled: true } },
        ...DOCS.pages.map((page) => ({
          path: `/docs/${page.slug}`,
          prerender: { enabled: true },
        })),
      ],
      sitemap: {
        enabled: true,
        outputPath: "sitemap.xml",
        host: SITEMAP_HOST,
      },
      router: { basepath: base },
    }),
    viteReact(),
    // dev-only: shells out to the real Lithon toolchain
    lithonApi(),
  ],
})

export default config
