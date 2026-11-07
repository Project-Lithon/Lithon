/**
 * Locates the Lithon toolchain (`uv`) in a way that works on every platform.
 *
 * Resolution order:
 *   1. `UV_PATH` (explicit override, used by CI)
 *   2. `uv` on PATH
 *   3. the Windows winget install location
 *   4. the usual macOS / Linux locations
 */
import { existsSync } from "node:fs"
import { join } from "node:path"

const WINGET = process.env.LOCALAPPDATA
  ? join(
      process.env.LOCALAPPDATA,
      "Microsoft",
      "WinGet",
      "Packages",
      "astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe",
      "uv.exe",
    )
  : ""

const UNIX_CANDIDATES = [
  "/usr/local/bin/uv",
  "/usr/bin/uv",
  "/opt/homebrew/bin/uv",
  join(process.env.HOME ?? "", ".local", "bin", "uv"),
  join(process.env.HOME ?? "", ".cargo", "bin", "uv"),
]

/** The `uv` executable to drive, or `null` when it cannot be found. */
export function findUv(): string | null {
  const explicit = process.env.UV_PATH
  if (explicit && existsSync(explicit)) return explicit

  if (process.platform === "win32") {
    return existsSync(WINGET) ? WINGET : null
  }

  for (const candidate of UNIX_CANDIDATES) {
    if (candidate && existsSync(candidate)) return candidate
  }

  // PATH probe (Bun.which) before giving up — returning the bare name
  // here would make requireUv()'s actionable error dead code.
  if (Bun.which("uv")) return "uv"
  return null
}

/** The `uv` executable, or a thrown error with an actionable message. */
export function requireUv(): string {
  const uv = findUv()
  if (!uv) {
    throw new Error(
      "uv not found. Install it with `winget install --id=astral-sh.uv -e` " +
        "(Windows) or `curl -LsSf https://astral.sh/uv/install.sh | sh` " +
        "(macOS/Linux), or set UV_PATH to the executable.",
    )
  }
  return uv
}

/**
 * Runs a Lithon tool over a file and returns its exit code and streams.
 */
export function runTool(
  uv: string,
  args: string[],
  cwd: string,
): { code: number; out: string; err: string } {
  const proc = Bun.spawnSync([uv, "run", "--quiet", "--python", "3.12", ...args], {
    cwd,
    stdout: "pipe",
    stderr: "pipe",
  })
  return {
    code: proc.exitCode ?? 1,
    out: new TextDecoder().decode(proc.stdout),
    err: new TextDecoder().decode(proc.stderr),
  }
}
