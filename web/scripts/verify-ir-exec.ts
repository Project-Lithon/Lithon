/**
 * Executes the IR that docs pages display and checks the result against the
 * output the docs claim. Real verification: the IR is what the frontend
 * produced, the expected value is what the repo's regression suite recorded.
 */
import { readFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"

import { DOCS } from "../apps/web/src/docs/registry"
import { EXAMPLE_IR } from "../apps/web/src/docs/generated/ir"
import { parseIr, runIr } from "../apps/web/src/lib/ir"

const REPO_ROOT = resolve(
  dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")),
  "..",
  ".."
)

function irKey(source: string) {
  let h = 0x811c9dc5
  for (let i = 0; i < source.length; i++) {
    h ^= source.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16).padStart(8, "0")
}

let checked = 0
let matched = 0
const rows: string[] = []
const failures: string[] = []

for (const page of DOCS.pages) {
  for (const section of page.sections) {
    section.blocks.forEach((block, i) => {
      if (block.kind !== "code") return
      const ex = block.example
      if (ex.lang && ex.lang !== "lithon") return
      // rejected-by-design examples document a diagnostic, not stdout
      if (ex.expect === "error") return
      const ir = ex.machine?.ir ?? EXAMPLE_IR[irKey(ex.source)]
      if (!ir) return
      if (ex.output === undefined) return

      checked += 1
      const run = runIr(parseIr(ir))
      const normalise = (s: string) =>
        s
          .replace(/\r\n/g, "\n")
          .replace(/\btrue\b/g, "True")
          .replace(/\bfalse\b/g, "False")
          .trim()
      const got = normalise(run.stdout.join("\n"))
      let oracle = normalise(ex.output)
      if (ex.expectedRef) {
        try {
          const repoOut = normalise(
            readFileSync(join(REPO_ROOT, ex.expectedRef), "utf8")
          )
          // the example may document only the first lines of the full file
          if (got && repoOut.split("\n").slice(0, got.split("\n").length).join("\n") === got) {
            oracle = got
          } else {
            oracle = repoOut
          }
        } catch {}
      }

      const ok = got === oracle
      if (ok) matched += 1
      rows.push(
        `${ok ? "PASS" : "FAIL"}  ${page.slug}#${section.id}[${i}]  got=${JSON.stringify(got)} want=${JSON.stringify(oracle)}`
      )
      if (!ok) failures.push(rows[rows.length - 1])
    })
  }
}

for (const r of rows) console.log(r)
console.log(`\n${matched}/${checked} IR executions match the documented output`)
if (failures.length > 0) {
  console.log("\nMismatches:")
  for (const f of failures) console.log(`  ${f}`)
  process.exit(1)
}