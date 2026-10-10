import {
  hasConsent,
  readCookie,
  readPref,
  writeCookie,
  writePref,
} from "./preferences"
import type { Dir } from "./preferences"

export type { Dir }

export const THEME_COOKIE = "lithon_theme"
export const DIRECTION_COOKIE = "lithon_direction"
export const MACHINE_VIEW_COOKIE = "lithon_machine_view"
export const SOUND_COOKIE = "lithon_sound"

/** Defaults: LTR, dark. */
export const DEFAULT_DIR: Dir = "ltr"
export const DEFAULT_THEME = "dark"

export function applyTheme(next: "dark" | "light") {
  document.documentElement.classList.toggle("dark", next === "dark")
  document.documentElement.dataset.theme = next
  writePref(THEME_COOKIE, next)
}

export function readTheme(): "dark" | "light" {
  const stored = readPref<"dark" | "light">(THEME_COOKIE, DEFAULT_THEME)
  return stored === "light" ? "light" : "dark"
}

export function toggleTheme(): "dark" | "light" {
  const next = readTheme() === "dark" ? "light" : "dark"
  applyTheme(next)
  return next
}

export function applyDirection(next: Dir) {
  document.documentElement.dir = next
  writePref(DIRECTION_COOKIE, next)
}

export function readDirection(): Dir {
  const stored = readPref<Dir>(DIRECTION_COOKIE, DEFAULT_DIR)
  return stored === "ltr" ? "ltr" : "rtl"
}

export function toggleDirection(): Dir {
  const next = readDirection() === "ltr" ? "rtl" : "ltr"
  applyDirection(next)
  return next
}

export const MACHINE_VIEW_EVENT = "lithon-machine-view"

export function readMachineView(): boolean {
  return readPref<string>(MACHINE_VIEW_COOKIE, "off") === "on"
}

export function setMachineView(on: boolean) {
  writePref(MACHINE_VIEW_COOKIE, on ? "on" : "off")
  window.dispatchEvent(new CustomEvent(MACHINE_VIEW_EVENT, { detail: on }))
}

export function toggleMachineView(): boolean {
  const next = !readMachineView()
  setMachineView(next)
  return next
}

export function onMachineViewChange(cb: (on: boolean) => void): () => void {
  const handler = (e: Event) => cb((e as CustomEvent<boolean>).detail)
  window.addEventListener(MACHINE_VIEW_EVENT, handler)
  return () => window.removeEventListener(MACHINE_VIEW_EVENT, handler)
}

/** Re-apply stored preferences, e.g. right after consent is granted. */
export function syncPreferences() {
  applyTheme(readTheme())
  applyDirection(readDirection())
}

export { hasConsent, readCookie, writeCookie, readPref, writePref }
