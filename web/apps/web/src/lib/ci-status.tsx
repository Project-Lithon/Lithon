import { useEffect, useState } from "react"
import {
  CheckCircleIcon,
  CircleNotchIcon,
  MinusCircleIcon,
  XCircleIcon,
} from "@phosphor-icons/react"

import { Badge } from "@workspace/ui/components/badge"

import {
  fetchRepoStatus,
  latestPerWorkflow,
  normaliseConclusion,
  overallStatus,
  relativeTime,
  type RepoStatus,
  type RunStatus,
} from "./github-status"

const RUNS_URL = "https://github.com/Project-Lithon/Lithon/actions"

const STYLE: Record<RunStatus, { cls: string; label: string }> = {
  success: { cls: "text-emerald-600 dark:text-emerald-400", label: "passing" },
  failure: { cls: "text-destructive", label: "failing" },
  cancelled: { cls: "text-muted-foreground", label: "cancelled" },
  skipped: { cls: "text-muted-foreground", label: "skipped" },
  in_progress: { cls: "text-amber-600 dark:text-amber-400", label: "running" },
  queued: { cls: "text-muted-foreground", label: "queued" },
  unknown: { cls: "text-muted-foreground", label: "unknown" },
}

function RunIcon({ status }: { status: RunStatus }) {
  const cls = STYLE[status].cls
  if (status === "success")
    return <CheckCircleIcon className={`size-3.5 ${cls}`} />
  if (status === "failure") return <XCircleIcon className={`size-3.5 ${cls}`} />
  if (status === "in_progress")
    return <CircleNotchIcon className={`size-3.5 animate-spin ${cls}`} />
  return <MinusCircleIcon className={`size-3.5 ${cls}`} />
}

export function useRepoStatus(pollMs = 60_000) {
  const [status, setStatus] = useState<RepoStatus | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false

    const load = async () => {
      const next = await fetchRepoStatus(controller.signal)
      if (!cancelled) setStatus(next)
    }
    void load()
    const id = window.setInterval(load, pollMs)

    return () => {
      cancelled = true
      controller.abort()
      window.clearInterval(id)
    }
  }, [pollMs])

  return status
}

export function CiStatusStrip() {
  const status = useRepoStatus()

  if (!status?.ok) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <MinusCircleIcon className="size-3.5" />
        CI status unavailable offline
      </div>
    )
  }

  const overall = overallStatus(status.runs)
  const perWorkflow = latestPerWorkflow(status.runs).slice(0, 4)

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
      <a
        href={RUNS_URL}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1.5 hover:opacity-80"
      >
        <RunIcon status={overall} />
        <span className="font-medium">CI</span>
        <span className="text-muted-foreground">{STYLE[overall].label}</span>
        {status.defaultBranch && (
          <span className="text-muted-foreground">
            · {status.defaultBranch}
          </span>
        )}
      </a>

      {perWorkflow.map((run) => {
        const s = normaliseConclusion(run.status, run.conclusion)
        return (
          <a
            key={run.id}
            href={run.url}
            target="_blank"
            rel="noreferrer"
            title={`${run.name} · ${STYLE[s].label} · ${relativeTime(run.updatedAt)}`}
            className="inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground"
          >
            <RunIcon status={s} />
            <span>{run.name}</span>
          </a>
        )
      })}

      {status.stars !== null && (
        <Badge variant="secondary" className="font-normal">
          ★ {status.stars}
        </Badge>
      )}
    </div>
  )
}
