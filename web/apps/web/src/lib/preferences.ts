/**
 * First-party preference cookies.
 *
 * Everything the site remembers about you lives in one cookie: display
 * preferences plus the consent decision itself. No third-party cookies, no
 * analytics, no identifiers. Preferences are only written once consent has
 * been granted, so declining really does leave nothing behind.
 */

export const CONSENT_COOKIE = "lithon_consent"
const YEAR = 60 * 60 * 24 * 365

export type Dir = "ltr" | "rtl"

export type ConsentState = "unknown" | "accepted" | "declined"

export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null
  const all = document.cookie ? document.cookie.split("; ") : []
  for (const part of all) {
    const eq = part.indexOf("=")
    if (eq === -1) continue
    if (part.slice(0, eq) === name) {
      return decodeURIComponent(part.slice(eq + 1))
    }
  }
  return null
}

export function writeCookie(name: string, value: string, days = YEAR) {
  if (typeof document === "undefined") return
  const maxAge = days * 24 * 60 * 60
  document.cookie = `${encodeURIComponent(name)}=${encodeURIComponent(
    value
  )}; path=/; max-age=${maxAge}; samesite=lax`
}

export function deleteCookie(name: string) {
  if (typeof document === "undefined") return
  document.cookie = `${encodeURIComponent(name)}=; path=/; max-age=0; samesite=lax`
}

export function readConsent(): ConsentState {
  const value = readCookie(CONSENT_COOKIE)
  if (value === "accepted") return "accepted"
  if (value === "declined") return "declined"
  return "unknown"
}

export function writeConsent(state: "accepted" | "declined") {
  // consent itself is strictly necessary to remember the choice
  writeCookie(CONSENT_COOKIE, state)
}

export function hasConsent(): boolean {
  return readConsent() === "accepted"
}

/** Read a preference: cookie first, then the provided default. */
export function readPref<T extends string>(name: string, fallback: T): T {
  const value = readCookie(name)
  if (value === null) return fallback
  return value as T
}

/** Write a preference, but only with consent. */
export function writePref(name: string, value: string) {
  if (!hasConsent()) return false
  writeCookie(name, value)
  return true
}

/** Plain setter for the consent cookie itself. */
export function setCookieRaw(name: string, value: string) {
  writeCookie(name, value)
}

export { writeCookie as setCookie, readCookie as getCookie }
