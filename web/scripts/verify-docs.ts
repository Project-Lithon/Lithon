/**
 * Verifies every docs example against the real Lithon toolchain.
 *
 * For each example in the docs registry:
 *  - sourceRef / expectedRef point at real files in the repo
 *  - the source is written to a BOM-free temp file
 *  - tools/typecheck.py accepts it (or rejects it with the documented error)
 *  - src/frontend/frontend.py lowers it to IR (or refuses it)
 *  - a documented machine.ir, if present, matches the real frontend output
 *
 * Run: bun scripts/verify-docs.ts
 */
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"

import { DOCS } from "../apps/web/src/docs/registry"

const APP_DIR = dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"))
const WEB_ROOT = resolve(APP_DIR, "..")
const REPO_ROOT = resolve(WEB_ROOT, "..")

const TYPECHECK = join(REPO_ROOT, "tools", "typecheck.py")
const FRONTEND = join(REPO_ROOT, "src", "frontend", "frontend.py")

const UV =
  process.env.UV_PATH ??
  join(
    process.env.LOCALAPPDATA ?? "",
    "Microsoft",
    "WinGet",
    "Packages",
    "astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe",
    "uv.exe",
  )

type Failure = { where: string; detail: string }

const failures: Failure[] = []
const TMP = join(WEB_ROOT, "node_modules", ".cache", "verify-docs")
let checked = 0
let ran = 0
let refs = 0

function fail(where: string, detail: string) {
  failures.push({ where, detail })
}

function run(cmd: string[]): { code: number; out: string; err: string } {
  const proc = Bun.spawnSync(cmd, { stdout: "pipe", stderr: "pipe" })
  return {
    code: proc.exitCode,
    out: new TextDecoder().decode(proc.stdout),
    err: new TextDecoder().decode(proc.stderr),
  }
}

function normalise(text: string): string {
  return text
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line) => line.length > 0)
    .join("\n")
}

function checkExample(
  where: string,
  example: {
    title?: string
    lang?: string
    source: string
    sourceRef?: string
    expectedRef?: string
    output?: string
    expect?: "ok" | "error"
    tool?: "typecheck" | "frontend"
    typecheck?: false
    errorMatch?: string
    machine?: { ir?: string }
  },
) {
  checked++
  if (example.lang && example.lang !== "lithon") return

  if (example.sourceRef) {
    refs++
    const src = join(REPO_ROOT, example.sourceRef)
    if (!existsSync(src)) {
      fail(where, `sourceRef not found in repo: ${example.sourceRef}`)
      return
    }
    const onDisk = readFileSync(src, "utf8")
    const documentedLines = example.source
      .replace(/\r\n/g, "\n")
      .split("\n")
      .map((l) => l.trimEnd())
      .filter((l) => l.length > 0)
    const repoLines = new Set(
      onDisk
        .replace(/\r\n/g, "\n")
        .split("\n")
        .map((l) => l.trimEnd()),
    )
    const missing = documentedLines.filter((l) => !repoLines.has(l))
    if (missing.length > 0) {
      fail(
        where,
        `sourceRef drift: ${example.sourceRef} does not contain these documented lines:\n${missing
          .slice(0, 3)
          .join("\n")}`,
      )
    }
  }
  if (example.expectedRef) {
    refs++
    const exp = join(REPO_ROOT, example.expectedRef)
    if (!existsSync(exp)) {
      fail(where, `expectedRef not found in repo: ${example.expectedRef}`)
    } else if (example.output !== undefined) {
      const expected = normalise(readFileSync(exp, "utf8"))
      const documented = normalise(example.output)
      const prefix = expected
        .split("\n")
        .slice(0, documented.split("\n").length)
        .join("\n")
      if (expected !== documented && prefix !== documented) {
        fail(
          where,
          `output does not match ${example.expectedRef}:\n--- repo ---\n${expected}\n--- docs ---\n${documented}`,
        )
      }
    }
  }

  const tmp = join(TMP, `ex_${checked}.py`)
  writeFileSync(tmp, example.source, "utf8")
  ran++

  const expect = example.expect ?? "ok"
  const useTypecheck = example.typecheck !== false

  const tc = useTypecheck
    ? run([UV, "run", "--quiet", "--python", "3.12", TYPECHECK, tmp])
    : null
  const fe = run([UV, "run", "--quiet", "--python", "3.12", FRONTEND, tmp])
  const tcText = tc ? `${tc.out}${tc.err}` : ""
  const feText = `${fe.out}${fe.err}`

  if (expect === "error") {
    const tool = example.tool ?? "typecheck"
    if (tool === "typecheck") {
      if (!tc) {
        fail(where, `error example is documented against typecheck but typecheck:false is set`)
      } else if (tc.code === 0) {
        fail(where, `typecheck accepted an example documented as rejected`)
      } else if (example.errorMatch && !tcText.includes(example.errorMatch)) {
        fail(
          where,
          `typecheck error does not contain errorMatch "${example.errorMatch}":\n${tcText.trim().split("\n")[0]}`,
        )
      }
    } else {
      if (fe.code === 0) {
        fail(where, `frontend accepted an example documented as rejected`)
      } else if (example.errorMatch && !feText.includes(example.errorMatch)) {
        fail(
          where,
          `frontend error does not contain errorMatch "${example.errorMatch}":\n${feText.trim().split("\n").slice(-3).join("\n")}`,
        )
      }
    }
    return
  }

  if (tc && tc.code !== 0) {
    fail(
      where,
      `typecheck rejected a valid example (exit ${tc.code}):\n${tcText.trim().split("\n")[0]}`,
    )
  }
  if (fe.code !== 0) {
    fail(
      where,
      `frontend refused a valid example (exit ${fe.code}):\n${feText.trim().split("\n").slice(-2).join("\n")}`,
    )
  } else if (example.machine?.ir) {
    const real = normalise(fe.out)
    const documented = normalise(example.machine.ir)
    if (real !== documented) {
      fail(
        where,
        `machine.ir does not match real frontend output.\n--- real ---\n${real}\n--- docs ---\n${documented}`,
      )
    }
  }
}

function checkRegistry() {
  const slugs = new Set<string>()
  const ids = new Set<string>()
  for (const page of DOCS.pages) {
    if (slugs.has(page.slug)) fail("registry", `duplicate slug: ${page.slug}`)
    slugs.add(page.slug)
    if (page.tags.length === 0) fail(page.slug, "page has no tags")
    if (page.sections.length === 0) fail(page.slug, "page has no sections")
    const sectionIds = new Set<string>()
    for (const section of page.sections) {
      if (sectionIds.has(section.id)) {
        fail(page.slug, `duplicate section id: ${section.id}`)
      }
      sectionIds.add(section.id)
    }
  }
  for (const entry of DOCS.entries) {
    if (ids.has(entry.id)) fail("registry", `duplicate entry id: ${entry.id}`)
    ids.add(entry.id)
    if (entry.tags.length === 0) fail(entry.id, "entry has no tags")
    if (entry.kind === "page" && !slugs.has(entry.href.replace("/docs/", ""))) {
      fail(entry.id, `entry points at unknown page: ${entry.href}`)
    }
  }
}

function main() {
  if (!existsSync(UV)) {
    console.error(
      `uv not found at ${UV}\nInstall it with: winget install --id=astral-sh.uv -e`,
    )
    process.exit(2)
  }
  if (!existsSync(TYPECHECK) || !existsSync(FRONTEND)) {
    console.error("Lithon repo toolchain not found — run this from web/")
    process.exit(2)
  }

  mkdirSync(TMP, { recursive: true })
  try {
    checkRegistry()
    for (const page of DOCS.pages) {
      for (const section of page.sections) {
        section.blocks.forEach((block, i) => {
          if (block.kind === "code") {
            checkExample(`${page.slug}#${section.id}[${i}]`, block.example)
          }
        })
      }
    }
  } finally {
    rmSync(TMP, { recursive: true, force: true })
  }

  const pages = DOCS.pages.length
  console.log(
    `docs verification: ${pages} pages · ${checked} examples · ${ran} compiled · ${refs} repo refs`,
  )
  if (failures.length > 0) {
    console.error(`\n${failures.length} failure(s):\n`)
    for (const f of failures) {
      console.error(`  ✗ ${f.where}`)
      console.error(
        `    ${f.detail.replace(/\n/g, "\n    ")}`,
      )
      console.error("")
    }
    process.exit(1)
  }
  console.log("ALL DOCS EXAMPLES VERIFIED against the real Lithon toolchain")
}

main()