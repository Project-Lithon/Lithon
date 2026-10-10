/**
 * Stages the recorded benchmark runs for the site: copies the videos into
 * public/benchmarks and writes a manifest the /benchmarks page reads.
 *
 * The recordings were made before the test programs moved, so the command
 * each task was recorded with is remapped onto where those files live today;
 * otherwise the page would point at paths that no longer exist.
 *
 *   bun scripts/stage-benchmarks.ts
 */
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"

const WEB_ROOT = resolve(
  dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")),
  ".."
)
const REPO_ROOT = resolve(WEB_ROOT, "..")
const RESULTS = join(REPO_ROOT, "bench_vid_maker", "results")
const OUT = join(WEB_ROOT, "apps", "web", "public", "benchmarks")

/** Where a recorded command's script lives now. */
const REMAP: Record<string, string> = {
  "bench_vid_maker/tasks/python_sum.py": "test_codes_python/test_1b_fsum.py",
  "bench_vid_maker/tasks/python_sum_1k.py": "test_codes_python/test_1k_fsum.py",
  "bench_vid_maker/tasks/python_euler.py":
    "test_codes_python/test_eulars_constant_lim_def.py",
}

function remapCommand(command: string[]): string[] {
  return command.map((part) => {
    const key = part.replace(/\\/g, "/")
    const mapped = REMAP[key]
    return mapped ?? part
  })
}

mkdirSync(OUT, { recursive: true })

const entries = []
const missing: string[] = []

for (const file of readdirSync(RESULTS).sort()) {
  if (file.endsWith(".mp4")) {
    copyFileSync(join(RESULTS, file), join(OUT, file))
  }
}

for (const file of readdirSync(RESULTS).sort()) {
  if (!file.endsWith(".json")) continue
  const raw = JSON.parse(readFileSync(join(RESULTS, file), "utf8"))
  const id = file.replace(/\.json$/, "")
  const cpython = raw.results.find((r: any) => r.name === "CPython")
  const lithon = raw.results.find((r: any) => r.name === "Lithon")
  const video = `${id}.mp4`
  const hasVideo = existsSync(join(OUT, video))

  const tasks = raw.results.map((task: any) => {
    const command = remapCommand(task.command.slice(1))
    const script = command.find((c: string) => c.endsWith(".py"))
    if (script && !existsSync(join(REPO_ROOT, script))) {
      missing.push(`${id}: ${task.name} -> ${script}`)
    }
    return {
      name: task.name,
      elapsed: task.elapsed,
      stdout: task.stdout,
      returncode: task.returncode,
      command,
      script: script ?? null,
    }
  })

  entries.push({
    id,
    title: raw.title,
    subtitle: raw.subtitle ?? null,
    video: hasVideo ? `/benchmarks/${video}` : null,
    videoBytes: hasVideo ? readFileSync(join(OUT, video)).length : 0,
    speedup:
      cpython && lithon && lithon.elapsed > 0
        ? cpython.elapsed / lithon.elapsed
        : null,
    tasks,
  })
}

writeFileSync(
  join(OUT, "manifest.json"),
  `${JSON.stringify(entries, null, 2)}\n`,
  "utf8",
)

for (const entry of entries) {
  console.log(
    `${entry.id.padEnd(14)} ${
      entry.speedup ? `${entry.speedup.toFixed(1)}x` : "-"
    }  video=${entry.video ? "yes" : "no"}`,
  )
}
console.log(`\n${entries.length} entries -> web/apps/web/public/benchmarks/manifest.json`)

if (missing.length > 0) {
  console.log(`\n${missing.length} command(s) still point at a missing file:`)
  for (const m of missing) console.log(`  ${m}`)
  process.exit(1)
}
console.log("every recorded command maps to a file that exists")