/**
 * Repairs Windows-1252 -> UTF-8 mojibake in the web app's source files.
 *
 * A character like U+2192 (RIGHTWARDS ARROW) is three UTF-8 bytes:
 * E2 86 92. If those bytes are read as Windows-1252 they become three
 * visible characters: U+00E2, U+2020 and U+2019. This script finds those
 * runs and decodes them back.
 *
 * Run: bun scripts/fix-encoding.ts [--check]
 */
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs"
import { join, extname } from "node:path"

/** Windows-1252 code points 0x80-0x9F do not map to Latin-1. */
const CP1252_HIGH: Record<number, number> = {
  0x20ac: 0x80, 0x0081: 0x81, 0x201a: 0x82, 0x0192: 0x83, 0x201e: 0x84,
  0x2026: 0x85, 0x2020: 0x86, 0x2021: 0x87, 0x02c6: 0x88, 0x2030: 0x89,
  0x0160: 0x8a, 0x2039: 0x8b, 0x0152: 0x8c, 0x017d: 0x8d, 0x017e: 0x8e,
  0x008f: 0x8f, 0x0090: 0x90, 0x2018: 0x91, 0x2019: 0x92, 0x201c: 0x93,
  0x201d: 0x94, 0x2022: 0x95, 0x2013: 0x96, 0x2014: 0x97, 0x02dc: 0x98,
  0x2122: 0x99, 0x0161: 0x9a, 0x203a: 0x9b, 0x0153: 0x9c, 0x0178: 0x9e,
}

/** The byte a Windows-1252 character stands for, or -1. */
function cp1252Byte(cp: number): number {
  if (cp >= 0x00a0 && cp <= 0x00ff) return cp
  const mapped = CP1252_HIGH[cp]
  if (mapped !== undefined) return mapped
  if (cp >= 0x0080 && cp <= 0x009f) return cp
  return -1
}

/**
 * The three lead characters of a mojibake run: the UTF-8 encoding of
 * U+00E2, U+00C2 and U+00C3 are the two- and three-byte sequences that
 * Windows-1252 renders as three separate characters.
 */
const LEADS = new Set([0x00e2, 0x00c2, 0x00c3, 0x00e3, 0x00c5])

/**
 * A run is a lead character followed by one or more bytes that could
 * continue a UTF-8 sequence.
 */
function isRunChar(cp: number): boolean {
  if (cp < 0x0080) return false
  const b = cp1252Byte(cp)
  if (b < 0) return false
  return b >= 0x80
}

function repair(text: string): { text: string; fixes: number } {
  let out = text
  let fixes = 0
  // A file can be encoded more than once over (a mojibake round trip
  // through a build step, then another), so the repair runs until the text
  // stops changing.
  for (let round = 0; round < 6; round++) {
    const once = repairOnce(out)
    if (once.fixes === 0) break
    out = once.text
    fixes += once.fixes
  }
  return { text: out, fixes }
}

function repairOnce(text: string): { text: string; fixes: number } {
  let out = ""
  let fixes = 0
  let i = 0
  while (i < text.length) {
    const cp = text.codePointAt(i)!
    if (!LEADS.has(cp) || cp > 0xffff) {
      out += text[i]
      i += 1
      continue
    }
    // collect the run
    let j = i + 1
    while (j < text.length && isRunChar(text.codePointAt(j)!)) j += 1
    const run = text.slice(i, j)
    const bytes: number[] = []
    let ok = true
    for (const ch of run) {
      const b = cp1252Byte(ch.codePointAt(0)!)
      if (b < 0) {
        ok = false
        break
      }
      bytes.push(b)
    }
    if (ok && bytes.length >= 2) {
      const decoded = new TextDecoder("utf-8", { fatal: true }).decode(
        new Uint8Array(bytes),
      )
      if (decoded !== run) {
        out += decoded
        fixes += 1
        i = j
        continue
      }
    }
    out += text[i]
    i += 1
  }
  return { text: out, fixes }
}

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".git" || entry === "dist") continue
    const full = join(dir, entry)
    const st = statSync(full)
    if (st.isDirectory()) walk(full, out)
    else if ([".ts", ".tsx", ".css", ".json", ".md"].includes(extname(full)))
      out.push(full)
  }
  return out
}

const check = process.argv.includes("--check")
const roots = ["apps/web/src", "packages/ui/src", "scripts"]
let totalFixes = 0
let totalFiles = 0

for (const root of roots) {
  for (const file of walk(root)) {
    const original = readFileSync(file, "utf8")
    const { text, fixes } = repair(original)
    if (fixes === 0) continue
    totalFixes += fixes
    totalFiles += 1
    console.log(`${check ? "would fix" : "fixed"} ${file} (${fixes})`)
    if (!check) writeFileSync(file, text, "utf8")
  }
}

console.log(
  check
    ? `${totalFixes} mojibake run(s) in ${totalFiles} file(s)`
    : `repaired ${totalFixes} mojibake run(s) in ${totalFiles} file(s)`,
)
process.exit(check && totalFixes > 0 ? 1 : 0)
