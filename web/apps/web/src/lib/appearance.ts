export type Dir = "rtl" | "ltr"

export function applyTheme(next: "dark" | "light") {
  document.documentElement.classList.toggle("dark", next === "dark")
  document.documentElement.dataset.theme = next
  try {
    localStorage.setItem("lithon-theme", next)
  } catch {}
}

export function readTheme(): "dark" | "light" {
  try {
    const stored = localStorage.getItem("lithon-theme")
    if (stored === "dark" || stored === "light") return stored
  } catch {}
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light"
}

export function toggleTheme(): "dark" | "light" {
  const next = readTheme() === "dark" ? "light" : "dark"
  applyTheme(next)
  return next
}

export function applyDirection(next: Dir) {
  document.documentElement.dir = next
  try {
    localStorage.setItem("lithon-direction", next)
  } catch {}
}

export function readDirection(): Dir {
  try {
    const stored = localStorage.getItem("lithon-direction")
    if (stored === "ltr" || stored === "rtl") return stored
  } catch {}
  return "rtl"
}

export function toggleDirection(): Dir {
  const next = readDirection() === "rtl" ? "ltr" : "rtl"
  applyDirection(next)
  return next
}

export const MACHINE_VIEW_EVENT = "lithon-machine-view"

export function readMachineView(): boolean {
  try {
    return localStorage.getItem("lithon-machine-view") === "on"
  } catch {
    return false
  }
}

export function setMachineView(on: boolean) {
  try {
    localStorage.setItem("lithon-machine-view", on ? "on" : "off")
  } catch {}
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
