import { createFileRoute, Link } from "@tanstack/react-router"
import {
  ArrowRightIcon,
  CheckCircleIcon,
  CircleIcon,
  GithubLogo,
} from "@phosphor-icons/react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@workspace/ui/components/card"

import { metaForPath } from "../lib/seo"
import { PageHero, WRAP } from "../site"

export const Route = createFileRoute("/next")({
  head: () => {
    const meta = metaForPath("/next")
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
  component: NextActions,
})

type Task = {
  title: string
  detail: string
  where: string
  href?: string
  status: "open" | "in-progress" | "done"
}

const LANGS: Task[] = [
  {
    title: "A standard library, or an honest absence of one",
    detail:
      "There is no `import` and no module loader, so `std/` is a sketch rather than a library. Either build the loader or say plainly that the language has no library story yet.",
    where: "the language",
    status: "open",
  },
  {
    title: "Closures and first-class functions",
    detail:
      "A function body cannot capture a caller's variable, and a function name is not a value. Both fall out of the current storage model, so they are a design question, not a bug.",
    where: "the language",
    status: "open",
  },
  {
    title: "A string API beyond three methods",
    detail:
      "`find`, `count` and `split` are the whole surface today. Slicing, concatenation and a `str` function parameter are all refused.",
    where: "the language",
    status: "open",
  },
  {
    title: "An `Optional` or `Union` of any kind",
    detail:
      "Every value is exactly one type. There is no null, no optional, no union, and therefore no way to express \"might not be there\" in the type system.",
    where: "the language",
    status: "open",
  },
]

const ENGINE: Task[] = [
  {
    title: "An ahead-of-time binary emit",
    detail:
      "Everything runs in-process today. Phase II targets a standalone `.bin`/`.exe` under 10 KB, with the header format and the decoupling from the tier runner still to be designed.",
    where: "Phase II",
    href: "/roadmap/phase-2-aot",
    status: "open",
  },
  {
    title: "Float promotion on merges",
    detail:
      "A float merge is never promoted into an XMM register; it stays a memory round trip. The direct-Phi path fixed the int case; the float case needs an XMM pool.",
    where: "Phase I",
    href: "/roadmap/phase-1-dual-tier",
    status: "in-progress",
  },
  {
    title: "ARM64",
    detail:
      "The whole backend is x86-64. An ARM64 port is Phase III, and the encoder design is deliberately small enough to make plausible.",
    where: "Phase III",
    href: "/roadmap/phase-3-systems",
    status: "open",
  },
  {
    title: "Win64 test evidence",
    detail:
      "The Win64 ABI is implemented, but CI runs on Linux only, so the claim is untested. A Windows runner would turn it from an assertion into a fact.",
    where: "Phase I",
    status: "open",
  },
]

const TOOLING: Task[] = [
  {
    title: "Teach the standalone checker the container types",
    detail:
      "`tools/typecheck.py` is a mirror of the older scalar-only checks and answers `unknown type 'list'`. The real checker is the C++ one that runs inside `tier_runner`; the mirror should catch up or be retired.",
    where: "toolchain",
    status: "in-progress",
  },
  {
    title: "A native parser to replace the CPython `ast` shortcut",
    detail:
      "The frontend uses Python's own `ast` module as a shortcut. That is why the source syntax is exactly Python's, and also why it is limited to what CPython's grammar accepts.",
    where: "toolchain",
    status: "open",
  },
  {
    title: "A playground that runs in production",
    detail:
      "The playground's runner is a dev-only Vite middleware, so the deployed site can only type-check locally. A static, pre-generated IR fallback would make the page useful to a visitor.",
    where: "the website",
    status: "open",
  },
  {
    title: "Release tags",
    detail:
      "There are no git tags and no published releases yet, so the changelog is built from the commit history. A tag per version would give the history a spine.",
    where: "the repository",
    status: "open",
  },
]

const DONE: Task[] = [
  {
    title: "`!=` as a first-class compare",
    detail: "Its own IR op, with IEEE 754 semantics on floats.",
    where: "2026-10-20",
    status: "done",
  },
  {
    title: "`try`/`except` over the engine's traps",
    detail: "Seven catchable names, with `else` and `finally`.",
    where: "2026-10-20",
    status: "done",
  },
  {
    title: "Raw syscalls with real pointer arguments",
    detail: "A pointer is a real host address in both tiers; the TCP loopback ships.",
    where: "2026-10-14",
    status: "done",
  },
  {
    title: "Packed-field structs",
    detail: "The byte layout matches a C `#pragma pack(1)` struct exactly.",
    where: "2026-10-13",
    status: "done",
  },
  {
    title: "Explicit conversions and terminal input",
    detail: "The width is always named; an out-of-range conversion traps.",
    where: "2026-10-12",
    status: "done",
  },
  {
    title: "The dynamic overflow trap",
    detail: "Plain `+`/`-`/`*` on `int[64]` trap; `wrap_*` is the opt-out.",
    where: "2026-10-10",
    status: "done",
  },
]

const STATUS_META = {
  open: { label: "open", Icon: CircleIcon, cls: "text-muted-foreground" },
  "in-progress": {
    label: "in progress",
    Icon: CircleIcon,
    cls: "text-amber-600 dark:text-amber-400",
  },
  done: {
    label: "shipped",
    Icon: CheckCircleIcon,
    cls: "text-emerald-600 dark:text-emerald-400",
  },
} as const

function TaskCard({ task }: { task: Task }) {
  const meta = STATUS_META[task.status]
  const Icon = meta.Icon
  return (
    <Card size="sm">
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <CardTitle className="flex items-start gap-2 text-base font-semibold">
            <Icon
              className={`mt-0.5 size-4 shrink-0 ${meta.cls}`}
              weight={task.status === "done" ? "fill" : "bold"}
            />
            {task.title}
          </CardTitle>
          <Badge variant="outline" className="shrink-0 text-[10px]">
            {task.where}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <CardDescription className="leading-relaxed">
          {task.detail}
        </CardDescription>
        {task.href && (
          <Link
            to={task.href}
            className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-accent-strong"
          >
            see the roadmap
            <ArrowRightIcon className="size-3.5 rtl:rotate-180" />
          </Link>
        )}
      </CardContent>
    </Card>
  )
}

function NextActions() {
  return (
    <>
      <PageHero
        kicker="03 / Next actions"
        title={
          <>
            What is still
            <br />
            on the list.
          </>
        }
        lede="The open questions, in the open. Every item here is a real gap between what Lithon does and what a language is expected to do — and every one of them is a design decision, not a bug."
        meta={[
          `${LANGS.length + ENGINE.length + TOOLING.length} open items`,
          "plus a public roadmap",
        ]}
      />

      <section className={`${WRAP} space-y-12 py-10`}>
        <div>
          <div className="mb-4 flex items-center gap-3">
            <h2 className="font-heading text-xl font-bold">The language</h2>
            <Badge variant="outline">{LANGS.length}</Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {LANGS.map((task) => (
              <TaskCard key={task.title} task={task} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-4 flex items-center gap-3">
            <h2 className="font-heading text-xl font-bold">The engine</h2>
            <Badge variant="outline">{ENGINE.length}</Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {ENGINE.map((task) => (
              <TaskCard key={task.title} task={task} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-4 flex items-center gap-3">
            <h2 className="font-heading text-xl font-bold">
              The toolchain and the site
            </h2>
            <Badge variant="outline">{TOOLING.length}</Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {TOOLING.map((task) => (
              <TaskCard key={task.title} task={task} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-4 flex items-center gap-3">
            <h2 className="font-heading text-xl font-bold">Recently shipped</h2>
            <Badge variant="outline">{DONE.length}</Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {DONE.map((task) => (
              <TaskCard key={task.title} task={task} />
            ))}
          </div>
          <p className="mt-4 text-sm text-muted-foreground">
            The full history is on the{" "}
            <Link
              to="/changelog"
              className="font-medium text-accent-strong underline-offset-4 hover:underline"
            >
              changelog
            </Link>
            .
          </p>
        </div>

        <div className="rounded-xl border-2 border-accent-strong/40 bg-accent/10 p-6">
          <h2 className="font-heading text-xl font-bold">
            The fastest way to help
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            The project is MIT-licensed and the roadmap is public, so the
            useful contributions are the ones that take an open item above and
            make it a shipped one — with the tests that prove it. A good first
            contribution is a program in the regression corpus that pins down
            a behaviour nobody has pinned down yet.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Button render={<a href="https://github.com/Project-Lithon/Lithon/issues" target="_blank" rel="noreferrer" />}>
              <GithubLogo />
              Browse the issues
            </Button>
            <Button
              variant="outline"
              render={<a href="https://github.com/Project-Lithon/Lithon" target="_blank" rel="noreferrer" />}
            >
              Read the source
            </Button>
          </div>
        </div>
      </section>
    </>
  )
}
