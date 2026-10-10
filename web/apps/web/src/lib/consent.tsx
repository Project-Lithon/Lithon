import { useEffect, useState } from "react"
import { CookieIcon } from "@phosphor-icons/react"

import { Button } from "@workspace/ui/components/button"
import {
  readConsent,
  writeConsent,
  writePref,
  CONSENT_COOKIE,
  type ConsentState,
} from "./preferences"
import {
  applyDirection,
  applyTheme,
  readDirection,
  readTheme,
} from "./appearance"

/**
 * Cookie consent. We store exactly one thing: your display preferences
 * (theme, direction, sound, machine view) in a first-party cookie. No
 * analytics, no tracking, no third-party cookies anywhere on this site.
 * Declining means nothing is written at all.
 */
export function ConsentBanner() {
  const [state, setState] = useState<ConsentState>("unknown")
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setState(readConsent())
    setMounted(true)
  }, [])

  if (!mounted || state !== "unknown") return null

  const decide = (choice: "accepted" | "declined") => {
    writeConsent(choice)
    if (choice === "accepted") {
      // persist the current display state now that we are allowed to
      writePref("lithon_theme", readTheme())
      writePref("lithon_direction", readDirection())
    }
    setState(choice)
  }

  return (
    <div
      role="dialog"
      aria-label="Cookie preferences"
      className="fixed inset-x-0 bottom-0 z-50 flex justify-center p-3 sm:p-4"
    >
      <div className="w-full max-w-3xl rounded-xl border bg-card/95 p-4 shadow-2xl backdrop-blur sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground">
            <CookieIcon className="size-5" />
          </span>
          <div className="flex-1 space-y-1.5">
            <h2 className="font-heading text-sm font-bold">
              Cookies: one, and only for preferences
            </h2>
            <p className="text-xs leading-relaxed text-muted-foreground">
              This site uses a single first-party cookie to remember your
              display choices: light or dark, LTR or RTL, sound on or off, and
              the machine view. No analytics, no tracking, no advertising, and
              no third-party cookies are used anywhere. Decline and nothing gets
              stored.
            </p>
            <p className="text-[11px] text-muted-foreground/80">
              We do read public GitHub data (CI status) over an anonymous
              request; that sets no cookie on this site.
            </p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => decide("declined")}>
            Decline
          </Button>
          <Button size="sm" onClick={() => decide("accepted")}>
            Accept
          </Button>
        </div>
      </div>
    </div>
  )
}

/**
 * Runs before first paint: applies the stored preferences (or the LTR + dark
 * defaults) so there is no flash of the wrong theme or direction.
 */
export const PREFERENCE_SCRIPT = `(function(){try{
var c=document.cookie||"";
function g(n){for(var p of c.split("; ")){var i=p.indexOf("=");if(i>-1&&p.slice(0,i)===n)return decodeURIComponent(p.slice(i+1));}return null;}
var t=g("lithon_theme");
var d=t==="light"?"light":t==="dark"?"dark":(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");
document.documentElement.classList.toggle("dark",d==="dark");
document.documentElement.dataset.theme=d;
var r=g("lithon_direction");
document.documentElement.dir=(r==="rtl"||r==="ltr")?r:"ltr";
}catch(e){}})();`

export { applyTheme, applyDirection, CONSENT_COOKIE }
