/**
 * Base-path aware asset URLs.
 *
 * The site is deployed to GitHub Pages from the repository's `master`
 * branch, which serves it from a sub-path (`/Lithon/`) unless a custom
 * domain is configured. A hard-coded root-relative URL like
 * `/benchmarks/manifest.json` therefore 404s in production, so every
 * static asset reference goes through here.
 *
 * The base is read from the Vite `BASE_URL` global, which Vite replaces
 * at build time with the configured `base` -- the same value the router
 * and the prerenderer use. In development that is `/`, so nothing changes.
 */
const RAW_BASE: string = import.meta.env.BASE_URL

/** The deployment base path, always with a leading and trailing slash. */
export const BASE_PATH = RAW_BASE.endsWith("/") ? RAW_BASE : `${RAW_BASE}/`

/**
 * Turn a root-relative path (`/benchmarks/manifest.json`) into a URL that
 * respects the deployment base path.
 */
export function assetUrl(path: string): string {
  if (!path.startsWith("/")) return path
  if (/^https?:\/\//.test(path)) return path
  return `${BASE_PATH}${path.replace(/^\//, "")}`
}
