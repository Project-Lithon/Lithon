const REPO = "Project-Lithon/Lithon"
const ENDPOINT = `https://api.github.com/repos/${REPO}/actions/runs?per_page=12`

export type RunStatus =
  | "success"
  | "failure"
  | "cancelled"
  | "skipped"
  | "in_progress"
  | "queued"
  | "unknown"

export type WorkflowRun = {
  id: number
  name: string
  status: string
  conclusion: string | null
  branch: string
  sha: string
  url: string
  createdAt: string
  updatedAt: string
}

export type RepoStatus = {
  runs: WorkflowRun[]
  stars: number | null
  openIssues: number | null
  defaultBranch: string | null
  fetchedAt: string
  ok: boolean
}

const FALLBACK: RepoStatus = {
  runs: [],
  stars: null,
  openIssues: null,
  defaultBranch: null,
  fetchedAt: "",
  ok: false,
}

export function normaliseConclusion(
  status: string,
  conclusion: string | null
): RunStatus {
  if (status === "in_progress") return "in_progress"
  if (status === "queued") return "queued"
  if (!conclusion) return "unknown"
  if (conclusion === "success") return "success"
  if (conclusion === "failure" || conclusion === "timed_out") return "failure"
  if (conclusion === "cancelled") return "cancelled"
  if (conclusion === "skipped") return "skipped"
  return "unknown"
}

/** Latest run per workflow name, newest first. */
export function latestPerWorkflow(runs: WorkflowRun[]): WorkflowRun[] {
  const seen = new Map<string, WorkflowRun>()
  for (const run of runs) {
    if (!seen.has(run.name)) seen.set(run.name, run)
  }
  return [...seen.values()]
}

export function overallStatus(runs: WorkflowRun[]): RunStatus {
  const meaningful = runs.filter(
    (r) => normaliseConclusion(r.status, r.conclusion) !== "skipped"
  )
  if (meaningful.length === 0) return "unknown"
  if (
    meaningful.some(
      (r) => normaliseConclusion(r.status, r.conclusion) === "failure"
    )
  )
    return "failure"
  if (
    meaningful.some(
      (r) => normaliseConclusion(r.status, r.conclusion) === "in_progress"
    )
  )
    return "in_progress"
  if (
    meaningful.every(
      (r) => normaliseConclusion(r.status, r.conclusion) === "success"
    )
  )
    return "success"
  return "unknown"
}

/**
 * Fetch live CI state from GitHub. Unauthenticated, cached, and it degrades to
 * a null result rather than throwing when the network or API is unavailable.
 */
export async function fetchRepoStatus(
  signal?: AbortSignal
): Promise<RepoStatus> {
  try {
    const res = await fetch(ENDPOINT, {
      headers: { Accept: "application/vnd.github+json" },
      signal,
    })
    if (!res.ok) return FALLBACK
    const data = (await res.json()) as {
      workflow_runs: {
        id: number
        name: string
        status: string
        conclusion: string | null
        head_branch: string
        head_sha: string
        html_url: string
        created_at: string
        updated_at: string
      }[]
    }
    const runs = data.workflow_runs.map((r) => ({
      id: r.id,
      name: r.name,
      status: r.status,
      conclusion: r.conclusion,
      branch: r.head_branch,
      sha: r.head_sha.slice(0, 7),
      url: r.html_url,
      createdAt: r.created_at,
      updatedAt: r.updated_at,
    }))

    let stars: number | null = null
    let openIssues: number | null = null
    let defaultBranch: string | null = null
    try {
      const repoRes = await fetch(`https://api.github.com/repos/${REPO}`, {
        headers: { Accept: "application/vnd.github+json" },
        signal,
      })
      if (repoRes.ok) {
        const repo = (await repoRes.json()) as {
          stargazers_count: number
          open_issues_count: number
          default_branch: string
        }
        stars = repo.stargazers_count
        openIssues = repo.open_issues_count
        defaultBranch = repo.default_branch
      }
    } catch {
      // metadata is a bonus, not required
    }

    return {
      runs,
      stars,
      openIssues,
      defaultBranch,
      fetchedAt: new Date().toISOString(),
      ok: true,
    }
  } catch {
    return FALLBACK
  }
}

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ""
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  return `${Math.floor(days / 30)}mo ago`
}
