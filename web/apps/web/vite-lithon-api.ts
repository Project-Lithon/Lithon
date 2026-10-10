import { spawn } from "node:child_process"
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import type { Plugin } from "vite"

// this file lives at <lithon>/web/apps/web/, so the Lithon repo root is 3 up
const REPO_ROOT = resolve(__dirname, "..", "..", "..")
const FRONTEND = join(REPO_ROOT, "src", "frontend", "frontend.py")
const TYPECHECK = join(REPO_ROOT, "tools", "typecheck.py")

function uvPath(): string {
  const local = process.env.LOCALAPPDATA
  if (local) {
    const p = join(
      local,
      "Microsoft",
      "WinGet",
      "Packages",
      "astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe",
      "uv.exe"
    )
    if (existsSync(p)) return p
  }
  return "uv"
}

function run(
  cmd: string,
  args: string[],
  cwd: string
): Promise<{ code: number; out: string; err: string }> {
  return new Promise((res) => {
    const child = spawn(cmd, args, { cwd, windowsHide: true })
    let out = ""
    let err = ""
    child.stdout.on("data", (d) => (out += d.toString()))
    child.stderr.on("data", (d) => (err += d.toString()))
    const timer = setTimeout(() => child.kill(), 15_000)
    child.on("close", (code) => {
      clearTimeout(timer)
      res({ code: code ?? -1, out, err })
    })
    child.on("error", (e) => {
      clearTimeout(timer)
      res({ code: -1, out, err: String(e) })
    })
  })
}

function json(res: any, status: number, body: unknown) {
  const text = JSON.stringify(body)
  res.statusCode = status
  res.setHeader("content-type", "application/json")
  res.end(text)
}

async function readBody(req: any): Promise<string> {
  const chunks: Buffer[] = []
  for await (const c of req) chunks.push(c as Buffer)
  return Buffer.concat(chunks).toString("utf8")
}

/**
 * POST /api/lithon
 *
 * Runs the submitted program through the real Lithon toolchain in the repo:
 * the type checker, then the frontend's IR lowering. No simulation: whatever
 * comes back is what the engine actually says.
 */
export function lithonApi(): Plugin {
  return {
    name: "lithon-api",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use("/api/lithon", async (req, res) => {
        if (req.method !== "POST") {
          res.statusCode = 405
          res.end("method not allowed")
          return
        }

        let code = ""
        try {
          const parsed = JSON.parse(await readBody(req))
          code = typeof parsed.code === "string" ? parsed.code : ""
        } catch {
          json(res, 400, { error: "malformed request body" })
          return
        }

        if (!code.trim()) {
          json(res, 200, {
            ok: false,
            stage: "input",
            diagnostics: ["empty program: nothing to run"],
            ir: null,
            typecheck: null,
          })
          return
        }

        const dir = mkdtempSync(join(tmpdir(), "lithon-"))
        const file = join(dir, "program.py")
        try {
          // utf8 without BOM: a BOM makes the frontend choke on line 1
          writeFileSync(file, code, "utf8")

          const uv = uvPath()
          const [tc, fe] = await Promise.all([
            run(
              uv,
              ["run", "--quiet", "--python", "3.12", TYPECHECK, file],
              REPO_ROOT
            ),
            run(
              uv,
              ["run", "--quiet", "--python", "3.12", FRONTEND, file],
              REPO_ROOT
            ),
          ])

          const diagnostic = (raw: string) =>
            raw
              .split(/\r?\n/)
              .map((l) => l.trim())
              .filter(Boolean)
              .filter((l) => !/^Traceback/.test(l))
              .pop() ?? ""

          const frontendFailed = fe.code !== 0
          const typecheckFailed = tc.code !== 0

          json(res, 200, {
            ok: !frontendFailed && !typecheckFailed,
            stage: frontendFailed
              ? "frontend"
              : typecheckFailed
                ? "typecheck"
                : "ir",
            typecheck: tc.code === 0 ? "ok" : diagnostic(`${tc.out}${tc.err}`),
            diagnostics: [
              ...(frontendFailed ? [diagnostic(`${fe.out}${fe.err}`)] : []),
              ...(typecheckFailed && !frontendFailed
                ? [diagnostic(`${tc.out}${tc.err}`)]
                : []),
            ],
            ir: frontendFailed ? null : fe.out,
            hint: frontendFailed
              ? "The frontend refused this program."
              : typecheckFailed
                ? "Lowers to IR, but the type checker is not happy yet."
                : "Lowers to IR cleanly. Native execution needs a built tier_runner.",
          })
        } catch (e) {
          json(res, 500, { error: String(e) })
        } finally {
          rmSync(dir, { recursive: true, force: true })
        }
      })

      server.middlewares.use("/api/examples", async (req, res) => {
        if (req.method !== "GET") {
          res.statusCode = 405
          res.end("method not allowed")
          return
        }
        const dir = join(REPO_ROOT, "test_codes_lithon")
        const files = [
          "test_1k_fsum.py",
          "test_float.py",
          "base_var.py",
          "test_eulars_constant_lim_def.py",
          "check_pointers.py",
          "test_multi_ops.py",
        ]
        const out: { name: string; source: string }[] = []
        for (const name of files) {
          const p = join(dir, name)
          if (!existsSync(p)) continue
          const { readFileSync } = await import("node:fs")
          out.push({ name, source: readFileSync(p, "utf8") })
        }
        json(res, 200, out)
      })
    },
  }
}
