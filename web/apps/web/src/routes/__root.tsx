import { useEffect, useState } from "react"
import {
  HeadContent,
  Link,
  Scripts,
  createRootRoute,
} from "@tanstack/react-router"
import {
  ArrowsLeftRightIcon,
  DiscordLogo,
  GithubLogo,
  ListIcon,
  MagnifyingGlassIcon,
  MoonIcon,
  SpeakerHighIcon,
  SpeakerXIcon,
  SunIcon,
} from "@phosphor-icons/react"

import { Button } from "@workspace/ui/components/button"
import { DirectionProvider } from "@workspace/ui/components/direction"
import { Kbd } from "@workspace/ui/components/kbd"
import { Separator } from "@workspace/ui/components/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@workspace/ui/components/sheet"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@workspace/ui/components/tooltip"
import appCss from "@workspace/ui/globals.css?url"
import { initSoundFeedback, readStoredSound, toggleSound } from "../lib/sound"
import { ConsentBanner, PREFERENCE_SCRIPT } from "../lib/consent"
import { CiStatusStrip } from "../lib/ci-status"
import {
  DEFAULT_DIR,
  applyDirection,
  applyTheme,
  readDirection,
  readTheme,
} from "../lib/appearance"
import { SearchProvider, useSearch } from "../search/provider"

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      {
        name: "description",
        content:
          "Lithon | A zero-dependency, dual-tier execution engine built for native speed.",
      },
      { name: "theme-color", content: "#f8fff4" },
      {
        title:
          "Lithon | Not a revolution but an evolution of the same bloodline",
      },
    ],
    links: [
      { rel: "icon", href: "/favicon.svg", type: "image/svg+xml" },
      { rel: "stylesheet", href: appCss },
    ],
  }),
  notFoundComponent: () => (
    <main className="mx-auto w-full max-w-6xl p-4 px-4 pt-16">
      <h1 className="text-2xl font-bold">404</h1>
      <p className="text-muted-foreground">
        The requested page could not be found.
      </p>
    </main>
  ),
  shellComponent: RootDocument,
})

const THEME_SCRIPT = PREFERENCE_SCRIPT

const NAV_LINKS = [
  { to: "/", hash: "engine", label: "The engine" },
  { to: "/docs", label: "Docs" },
  { to: "/use-cases", label: "Use cases" },
  { to: "/roadmap", label: "Roadmap" },
  { to: "/benchmarks", label: "Benchmarks" },
  { to: "/playground", label: "Playground" },
] as const

function BoaMark() {
  return (
    <svg viewBox="0 0 36 36" aria-hidden="true" className="size-7">
      <path
        className="fill-none stroke-foreground"
        strokeWidth="1.6"
        strokeLinecap="round"
        d="M25.7 8.3c-4.9-3.8-12.3-2.8-15.3 2.2-2.8 4.7-.6 10.1 4 11.8 4.3 1.6 8.5-1 8.2-4.9-.2-2.8-3.2-4.1-5.4-2.5-1.1.8-1.2 2.2-.6 3.1M9.7 25.3c4.2 5.7 13.1 6.2 17.8 1.8 2.8-2.6 3.5-5.9 2.2-8.6"
      />
      <path
        className="fill-primary stroke-primary-foreground/30"
        strokeWidth="1"
        d="M23.8 7.5c2.7-2 6.7-1.1 7.8 1.9l-1.3 3.9-3.9.8-3.1-2.2.5-4.4Z"
      />
      <circle className="fill-foreground" cx="28.8" cy="9.1" r="1" />
    </svg>
  )
}

function ThemeToggle() {
  const [dark, setDark] = useState(false)

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"))
  }, [])

  const toggle = () => {
    const next = !dark
    document.documentElement.classList.toggle("dark", next)
    document.documentElement.dataset.theme = next ? "dark" : "light"
    try {
      localStorage.setItem("lithon-theme", next ? "dark" : "light")
    } catch {}
    setDark(next)
  }

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={toggle}
            aria-label="Switch color theme"
          />
        }
      >
        {dark ? <MoonIcon /> : <SunIcon />}
      </TooltipTrigger>
      <TooltipContent>{dark ? "Dark mode" : "Light mode"}</TooltipContent>
    </Tooltip>
  )
}

function SoundToggle() {
  const [on, setOn] = useState(false)

  useEffect(() => {
    setOn(readStoredSound())
  }, [])

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            className={on ? "sound-toggle text-accent-strong" : "sound-toggle"}
            onClick={() => setOn(toggleSound())}
            aria-pressed={on}
            aria-label={
              on ? "Turn interface sound off" : "Turn interface sound on"
            }
          />
        }
      >
        {on ? <SpeakerHighIcon /> : <SpeakerXIcon />}
      </TooltipTrigger>
      <TooltipContent>{on ? "Sound on" : "Sound off"}</TooltipContent>
    </Tooltip>
  )
}

function DirectionToggle({
  dir,
  onToggle,
}: {
  dir: "rtl" | "ltr"
  onToggle: () => void
}) {
  const next = dir === "rtl" ? "left-to-right" : "right-to-left"
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onToggle}
            aria-label={`Switch to ${next} text direction`}
          />
        }
      >
        <ArrowsLeftRightIcon />
      </TooltipTrigger>
      <TooltipContent>{`Switch to ${next}`}</TooltipContent>
    </Tooltip>
  )
}

function IconButton({
  href,
  label,
  children,
}: {
  href: string
  label: string
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            render={<a href={href} target="_blank" rel="noreferrer" />}
            aria-label={label}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

function BrandLinks() {
  return (
    <>
      <Link to="/" aria-label="Lithon home" className="flex items-center gap-2">
        <BoaMark />
        <span className="font-heading text-lg font-extrabold tracking-tight">
          lithon
          <span className="text-primary-foreground dark:text-primary">.</span>
        </span>
      </Link>
      <nav
        aria-label="Main navigation"
        className="ms-6 hidden items-center gap-1 md:flex"
      >
        {NAV_LINKS.map((link) => (
          <Button
            key={link.to}
            variant="ghost"
            size="sm"
            render={
              <Link
                to={link.to}
                {...("hash" in link ? { hash: link.hash } : {})}
              />
            }
          >
            {link.label}
          </Button>
        ))}
      </nav>
    </>
  )
}

function SearchButton() {
  const { open } = useSearch()
  return (
    <Button
      variant="ghost"
      size="sm"
      className="hidden items-center gap-2 text-muted-foreground md:flex"
      onClick={open}
      aria-label="Search documentation and actions"
    >
      <MagnifyingGlassIcon />
      <span className="text-xs">Search</span>
      <Kbd>⌘</Kbd>
      <Kbd>K</Kbd>
    </Button>
  )
}

function SiteHeader({
  dir,
  onToggleDir,
}: {
  dir: "rtl" | "ltr"
  onToggleDir: () => void
}) {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/85 backdrop-blur">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center px-4">
        <BrandLinks />
        <div className="ms-auto flex items-center gap-1">
          <SearchButton />
          <span className="hidden sm:flex">
            <IconButton
              href="https://github.com/Project-Lithon/Lithon"
              label="GitHub"
            >
              <GithubLogo />
            </IconButton>
          </span>
          <span className="hidden sm:flex">
            <IconButton href="https://discord.gg/qaJD8Kxys" label="Discord">
              <DiscordLogo />
            </IconButton>
          </span>
          <SoundToggle />
          <DirectionToggle dir={dir} onToggle={onToggleDir} />
          <ThemeToggle />
          <Sheet>
            <SheetTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="md:hidden"
                  aria-label="Open navigation"
                />
              }
            >
              <ListIcon />
            </SheetTrigger>
            <SheetContent side="right" className="gap-1">
              <SheetTitle className="sr-only">Navigation</SheetTitle>
              <SheetDescription className="sr-only">
                Lithon site sections
              </SheetDescription>
              <nav
                className="flex flex-col gap-1"
                aria-label="Mobile navigation"
              >
                {NAV_LINKS.map((link) => (
                  <Button
                    key={link.to}
                    variant="ghost"
                    className="justify-start"
                    render={
                      <Link
                        to={link.to}
                        {...("hash" in link ? { hash: link.hash } : {})}
                      />
                    }
                  >
                    {link.label}
                  </Button>
                ))}
              </nav>
              <Separator />
              <div className="flex gap-1">
                <IconButton
                  href="https://github.com/Project-Lithon/Lithon"
                  label="GitHub"
                >
                  <GithubLogo />
                </IconButton>
                <IconButton href="https://discord.gg/qaJD8Kxys" label="Discord">
                  <DiscordLogo />
                </IconButton>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  )
}

function SiteFooter() {
  return (
    <footer className="border-t">
      <div className="mx-auto flex w-full max-w-6xl flex-col items-start gap-3 px-4 py-6 sm:flex-row sm:items-center">
        <Link to="/" className="flex items-center gap-2">
          <BoaMark />
          <span className="font-heading font-extrabold">
            lithon
            <span className="text-primary-foreground dark:text-primary">.</span>
          </span>
        </Link>
        <p className="text-sm text-muted-foreground sm:ms-4">
          Not a revolution but an evolution of the same bloodline. © 2026 Lithon
          contributors.
        </p>
        <div className="flex gap-3 text-sm sm:ms-auto">
          <Button
            variant="link"
            size="sm"
            render={
              <a
                href="https://github.com/Project-Lithon/Lithon"
                target="_blank"
                rel="noreferrer"
              />
            }
          >
            GitHub
          </Button>
          <Button
            variant="link"
            size="sm"
            onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
          >
            Back to top
          </Button>
        </div>
        <div className="w-full border-t pt-4 sm:ms-0 sm:border-0 sm:pt-0">
          <CiStatusStrip />
        </div>
      </div>
    </footer>
  )
}

function RootDocument({ children }: { children: React.ReactNode }) {
  const [dir, setDir] = useState<"rtl" | "ltr">(DEFAULT_DIR)

  useEffect(() => {
    const stop = initSoundFeedback()
    setDir(readDirection())
    applyTheme(readTheme())
    applyDirection(readDirection())
    return stop
  }, [])

  const toggleDir = () => {
    const next = dir === "rtl" ? "ltr" : "rtl"
    setDir(next)
    applyDirection(next)
  }

  return (
    <html lang="en" dir="ltr" suppressHydrationWarning>
      <head>
        <HeadContent />
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <DirectionProvider direction={dir}>
          <SearchProvider>
            <TooltipProvider>
              <a
                href="#main"
                className="sr-only focus:not-sr-only focus:absolute focus:start-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2"
              >
                Skip to content
              </a>
              <SiteHeader dir={dir} onToggleDir={toggleDir} />
              <main id="main">{children}</main>
              <SiteFooter />
              <ConsentBanner />
            </TooltipProvider>
          </SearchProvider>
        </DirectionProvider>
        <Scripts />
      </body>
    </html>
  )
}
