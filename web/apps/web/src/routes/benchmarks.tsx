import { useEffect, useState } from "react"
import { createFileRoute } from "@tanstack/react-router"
import {
  CheckCircleIcon,
  GithubLogo,
  PlayIcon,
  TrendDownIcon,
  TrendUpIcon,
} from "@phosphor-icons/react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@workspace/ui/components/card"
import { Separator } from "@workspace/ui/components/separator"

import { PageHero, WRAP } from "../site"
import { metaForPath } from "../lib/seo"

export const Route = createFileRoute("/benchmarks")({
  head: () => {
    const meta = metaForPath("/benchmarks")
    return {
      meta: [
        { title: meta.title },
        { name: "description", content: meta.description },
        { property: "og:title", content: meta.title },
        { property: "og:description", content: meta.description },
        { tagName: "link", rel: "canonical", href: meta.canonical },
      ],
    }
  },
  component: Benchmarks,
})

type Task = {
  name: string
  elapsed: number
  stdout: string
  returncode: number
  command: string[]
}

type Entry = {
  id: string
  title: string
  subtitle: string | null
  video: string | null
  videoBytes: number
  speedup: number | null
  tasks: Task[]
}

function fmtSeconds(s: number) {
  if (s < 1) return `${Math.round(s * 1000)} ms`
  if (s < 60) return `${s.toFixed(2)} s`
  const m = Math.floor(s / 60)
  return `${m}m ${Math.round(s - m * 60)}s`
}

function Bar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.max(1, (value / max) * 100) : 0
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
      <div
        className="h-full rounded-full bg-accent-strong/70 transition-[width] duration-700"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

function BenchmarkCard({ entry }: { entry: Entry }) {
  const [playing, setPlaying] = useState(false)
  const cpython = entry.tasks.find((t) => t.name === "CPython")
  const lithon = entry.tasks.find((t) => t.name === "Lithon")
  const max = Math.max(...entry.tasks.map((t) => t.elapsed), 0.0001)
  const identical =
    cpython && lithon && cpython.stdout.trim() === lithon.stdout.trim()

  return (
    <Card className="overflow-hidden">
      <CardHeader className="border-b pb-3">
        <CardTitle className="flex flex-wrap items-start justify-between gap-2">
          <span className="font-heading text-base">{entry.title}</span>
          {entry.speedup && (
            <Badge
              variant="outline"
              className={
                entry.speedup >= 1
                  ? "text-accent-strong"
                  : "text-muted-foreground"
              }
            >
              {entry.speedup >= 1 ? <TrendUpIcon /> : <TrendDownIcon />}
              {entry.speedup.toFixed(1)}×
            </Badge>
          )}
        </CardTitle>
        {entry.subtitle && (
          <p className="text-sm text-muted-foreground">{entry.subtitle}</p>
        )}
      </CardHeader>

      <CardContent className="space-y-4 pt-4">
        {entry.video && (
          <div className="overflow-hidden rounded-lg border bg-muted">
            {playing ? (
              <video
                src={entry.video}
                controls
                autoPlay
                className="w-full"
                onEnded={() => setPlaying(false)}
              />
            ) : (
              <button
                type="button"
                onClick={() => setPlaying(true)}
                className="group relative flex aspect-video w-full items-center justify-center"
                aria-label={`Play ${entry.title}`}
              >
                <span className="absolute inset-0 bg-gradient-to-br from-accent/40 via-transparent to-accent-strong/30" />
                <span className="relative flex size-14 items-center justify-center rounded-full bg-background/90 shadow-lg transition-transform group-hover:scale-110">
                  <PlayIcon className="size-6" />
                </span>
                <span className="absolute start-3 bottom-2 font-mono text-[10px] text-background/80">
                  {(entry.videoBytes / 1024).toFixed(0)} KB · recorded run
                </span>
              </button>
            )}
          </div>
        )}

        <div className="space-y-3">
          {entry.tasks.map((task) => (
            <div key={task.name} className="space-y-1.5">
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="font-medium">{task.name}</span>
                <span
                  className="font-mono text-muted-foreground tabular-nums"
                  dir="ltr"
                >
                  {fmtSeconds(task.elapsed)}
                </span>
              </div>
              <Bar value={task.elapsed} max={max} />
              <p
                dir="ltr"
                className="truncate font-mono text-[11px] text-muted-foreground"
              >
                {task.command.join(" ")}
              </p>
            </div>
          ))}
        </div>

        {identical && (
          <div className="flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-400">
            <CheckCircleIcon className="size-4" />
            identical output: both printed{" "}
            <code dir="ltr" className="font-mono">
              {cpython.stdout.trim().split("\n")[0]}
            </code>
          </div>
        )}

        {lithon && (
          <>
            <Separator />
            <details className="group">
              <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                Show recorded stdout
              </summary>
              <pre
                dir="ltr"
                className="mt-2 overflow-x-auto rounded-md bg-muted p-3 font-mono text-[11px]"
              >
                {lithon.stdout}
              </pre>
            </details>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Benchmarks() {
  const [entries, setEntries] = useState<Entry[] | null>(null)

  useEffect(() => {
    fetch("/benchmarks/manifest.json")
      .then((r) => r.json())
      .then((data: Entry[]) => setEntries(data))
      .catch(() => setEntries([]))
  }, [])

  const headline = entries?.find((e) => e.speedup && e.speedup > 1) ?? null

  return (
    <>
      <PageHero
        kicker="05 / Benchmarks"
        title={
          <>
            Numbers you can
            <br />
            watch happen.
          </>
        }
        lede="Every run here was recorded by running the same program twice: once on CPython, once on the Lithon native tier. The videos are the actual terminal sessions, not an animation."
        meta={["Real runs", "Same program", "Identical output"]}
      />

      <section className={`${WRAP} space-y-6 py-10`}>
        {headline && (
          <div className="flex flex-wrap items-center gap-4 rounded-lg border p-4">
            <Badge variant="outline" className="text-accent-strong">
              <TrendUpIcon />
              {headline.speedup?.toFixed(1)}×
            </Badge>
            <p className="flex-1 text-sm text-muted-foreground">
              {headline.title}: CPython{" "}
              {fmtSeconds(
                headline.tasks.find((t) => t.name === "CPython")?.elapsed ?? 0
              )}{" "}
              against Lithon{" "}
              {fmtSeconds(
                headline.tasks.find((t) => t.name === "Lithon")?.elapsed ?? 0
              )}
            </p>
            <Button
              variant="outline"
              size="sm"
              render={
                <a
                  href="https://github.com/Project-Lithon/Lithon/tree/master/bench_vid_maker"
                  target="_blank"
                  rel="noreferrer"
                />
              }
            >
              <GithubLogo />
              Reproduce
            </Button>
          </div>
        )}

        {!entries && (
          <p className="text-sm text-muted-foreground">Loading results…</p>
        )}

        {entries && entries.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No recorded runs found. Generate them with{" "}
            <code className="font-mono">bench-vid-maker</code>.
          </p>
        )}

        <div className="grid gap-4 lg:grid-cols-2">
          {entries?.map((entry) => (
            <BenchmarkCard key={entry.id} entry={entry} />
          ))}
        </div>

        <p className="text-xs leading-relaxed text-muted-foreground">
          Benchmarks prove speed, not correctness. The{" "}
          <a href="/docs/verification" className="underline underline-offset-4">
            verification gate
          </a>{" "}
          is what proves the programs are right, and it is the reason the speed
          numbers are worth quoting.
        </p>
      </section>
    </>
  )
}
