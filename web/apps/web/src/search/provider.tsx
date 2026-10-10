import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react"
import { useNavigate } from "@tanstack/react-router"
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@workspace/ui/components/command"
import {
  ArrowSquareOutIcon,
  BookOpenIcon,
  CompassIcon,
  LightningIcon,
  MagnifyingGlassIcon,
  TextTIcon,
} from "@phosphor-icons/react"
import { Kbd } from "@workspace/ui/components/kbd"

import type { ActionEntry, SearchEntry } from "../docs/model"
import { DOCS } from "../docs/registry"
import { actionEntries } from "./actions"
import { searchEntries } from "./engine"
import {
  toggleDirection,
  toggleMachineView,
  toggleTheme,
} from "../lib/appearance"
import { toggleSound } from "../lib/sound"

const KIND_META = {
  page: {
    icon: BookOpenIcon,
    chip: "bg-primary/25 text-primary-foreground dark:text-primary",
  },
  section: {
    icon: TextTIcon,
    chip: "bg-muted text-muted-foreground",
  },
  action: {
    icon: LightningIcon,
    chip: "bg-accent-strong/20 text-accent-strong",
  },
  nav: {
    icon: CompassIcon,
    chip: "bg-emerald-600/20 text-emerald-700 dark:text-emerald-400",
  },
  external: {
    icon: ArrowSquareOutIcon,
    chip: "bg-sky-600/20 text-sky-700 dark:text-sky-400",
  },
} as const

type PaletteKind = keyof typeof KIND_META

function PaletteRow({
  kind,
  title,
  subtitle,
  shortcut,
}: {
  kind: PaletteKind
  title: string
  subtitle?: string
  shortcut?: string
}) {
  const meta = KIND_META[kind]
  const Icon = meta.icon
  return (
    <>
      <span
        className={`flex size-6 shrink-0 items-center justify-center rounded-md ${meta.chip}`}
      >
        <Icon className="size-3.5" />
      </span>
      <span className="truncate font-medium text-foreground">{title}</span>
      {shortcut && <Kbd className="ms-1">{shortcut}</Kbd>}
      {subtitle && (
        <span className="ms-auto hidden max-w-[45%] truncate text-xs text-muted-foreground sm:block">
          {subtitle}
        </span>
      )}
    </>
  )
}

function GroupHeading({
  dot,
  children,
}: {
  dot: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-center gap-2 px-2 pt-3 pb-1.5 text-[10px] font-semibold tracking-[0.14em] text-muted-foreground uppercase select-none">
      <span className={`size-1.5 rounded-full ${dot}`} />
      {children}
    </div>
  )
}

const SHORTCUTS: Record<string, string | undefined> = {
  "action:theme": "T",
  "action:direction": "D",
  "action:sound": "S",
  "action:machine-view": "M",
}

type SearchContextValue = { open: () => void }

const SearchContext = createContext<SearchContextValue>({ open: () => {} })

export function useSearch() {
  return useContext(SearchContext)
}

function runAction(entry: ActionEntry, navigate: (to: string) => void) {
  switch (entry.run) {
    case "theme":
      toggleTheme()
      return
    case "direction":
      toggleDirection()
      return
    case "sound":
      toggleSound()
      return
    case "machine-view":
      toggleMachineView()
      return
    default:
      if (entry.run.startsWith("nav:")) {
        navigate(entry.run.slice(4))
      } else if (/^https?:\/\//.test(entry.run)) {
        window.open(entry.run, "_blank", "noreferrer")
      }
  }
}

export function SearchProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const navigate = useNavigate()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  const actions = useMemo(() => actionEntries(), [])
  const docsResults = useMemo(() => searchEntries(DOCS.entries, query), [query])
  const actionResults = useMemo(
    () =>
      query.trim()
        ? searchEntries(
            actions.map((a) => ({
              id: a.id,
              kind: "action" as const,
              title: a.title,
              subtitle: a.subtitle,
              tags: a.tags,
              href: a.run,
            })),
            query,
            12
          )
        : [],
    [actions, query]
  )

  const go = useCallback(
    (href: string) => {
      setOpen(false)
      setQuery("")
      if (href.startsWith("/docs/") && href.includes("#")) {
        const [path, hash] = href.split("#")
        navigate({ to: path, hash } as never)
      } else {
        navigate({ to: href } as never)
      }
    },
    [navigate]
  )

  const run = useCallback(
    (id: string) => {
      if (id.startsWith("action:")) {
        const action = actions.find((a) => a.id === id)
        if (!action) return
        setOpen(false)
        setQuery("")
        runAction(action, (to) => navigate({ to } as never))
        return
      }
      const entry = DOCS.entries.find((e) => e.id === id)
      if (!entry) return
      go(entry.href)
    },
    [actions, go, navigate]
  )

  const openSearch = useCallback(() => setOpen(true), [])

  const searching = query.trim().length > 0
  const pages = searching
    ? docsResults.filter((r) => r.kind === "page")
    : DOCS.pages.map((page) => ({
        id: `page:${page.slug}`,
        title: page.title,
        subtitle: page.description,
      }))
  const sections = searching
    ? docsResults.filter((r) => r.kind === "section")
    : []
  const toggleActions = searching
    ? actionResults
    : actions.filter(
        (a) => !a.run.startsWith("nav:") && !a.run.startsWith("http")
      )
  const navActions = searching
    ? []
    : actions.filter((a) => a.run.startsWith("nav:"))
  const externalActions = searching
    ? []
    : actions.filter((a) => a.run.startsWith("http"))
  const allActions = [...toggleActions, ...navActions, ...externalActions]

  return (
    <SearchContext.Provider value={{ open: openSearch }}>
      {children}
      <CommandDialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o)
          if (!o) setQuery("")
        }}
        title="Search Lithon"
        description="Search docs, sections, and site actions"
        className="sm:max-w-xl"
      >
        <Command shouldFilter={false}>
          <div className="h-0.5 bg-gradient-to-r from-primary via-accent-strong to-primary" />
          <CommandInput
            value={query}
            onValueChange={setQuery}
            placeholder='Search docs, sections, and actions… try "shift", "errors", "machine"'
            className="h-11! text-base"
          />
          <CommandList className="max-h-80 pb-1">
            <CommandEmpty>
              <div className="py-8 text-center">
                <MagnifyingGlassIcon className="mx-auto mb-2 size-6 text-muted-foreground/60" />
                <p className="text-sm font-medium">No matches</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Nothing here for “{query.trim()}”. Try “pointers”, “E0301”, or
                  “vzeroupper”.
                </p>
              </div>
            </CommandEmpty>

            {toggleActions.length > 0 && (
              <CommandGroup>
                <GroupHeading dot="bg-accent-strong">
                  {searching ? "Matching actions" : "Quick actions"}
                </GroupHeading>
                {toggleActions.map((entry) => (
                  <CommandItem key={entry.id} value={entry.id} onSelect={run}>
                    <PaletteRow
                      kind="action"
                      title={entry.title}
                      subtitle={entry.subtitle}
                      shortcut={SHORTCUTS[entry.id]}
                    />
                  </CommandItem>
                ))}
              </CommandGroup>
            )}

            {pages.length > 0 && (
              <>
                <CommandSeparator />
                <CommandGroup>
                  <GroupHeading dot="bg-primary dark:bg-primary/70">
                    {searching ? "Matching pages" : "Documentation"}
                  </GroupHeading>
                  {pages.map((entry) => (
                    <CommandItem key={entry.id} value={entry.id} onSelect={run}>
                      <PaletteRow
                        kind="page"
                        title={entry.title}
                        subtitle={entry.subtitle}
                      />
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}

            {sections.length > 0 && (
              <>
                <CommandSeparator />
                <CommandGroup>
                  <GroupHeading dot="bg-muted-foreground/60">
                    Sections
                  </GroupHeading>
                  {sections.map((entry) => (
                    <CommandItem key={entry.id} value={entry.id} onSelect={run}>
                      <PaletteRow
                        kind="section"
                        title={entry.title}
                        subtitle={entry.subtitle}
                      />
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}

            {navActions.length > 0 && (
              <>
                <CommandSeparator />
                <CommandGroup>
                  <GroupHeading dot="bg-emerald-600/70">Navigate</GroupHeading>
                  {navActions.map((entry) => (
                    <CommandItem key={entry.id} value={entry.id} onSelect={run}>
                      <PaletteRow
                        kind="nav"
                        title={entry.title}
                        subtitle={entry.subtitle}
                      />
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}

            {externalActions.length > 0 && (
              <>
                <CommandSeparator />
                <CommandGroup>
                  <GroupHeading dot="bg-sky-600/70">Community</GroupHeading>
                  {externalActions.map((entry) => (
                    <CommandItem key={entry.id} value={entry.id} onSelect={run}>
                      <PaletteRow
                        kind="external"
                        title={entry.title}
                        subtitle={entry.subtitle}
                      />
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}
          </CommandList>
          <div className="flex items-center gap-3 border-t bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <Kbd>↑</Kbd>
              <Kbd>↓</Kbd> navigate
            </span>
            <span className="inline-flex items-center gap-1">
              <Kbd>↵</Kbd> open
            </span>
            <span className="inline-flex items-center gap-1">
              <Kbd>esc</Kbd> close
            </span>
            <span className="ms-auto hidden font-mono sm:block">
              {pages.length + sections.length + allActions.length} results
            </span>
          </div>
        </Command>
      </CommandDialog>
    </SearchContext.Provider>
  )
}

export type { SearchEntry }
