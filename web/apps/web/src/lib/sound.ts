const SOUND_STORAGE_KEY = "lithon-sound"

const TICK_FREQUENCY_HZ = 660
const TICK_DURATION_S = 0.06
const TICK_GAIN = 0.1
const HOVER_FREQUENCY_HZ = 880
const HOVER_DURATION_S = 0.035
const HOVER_GAIN = 0.035
const HOVER_MIN_GAP_MS = 70
const CONFIRM_FREQUENCIES_HZ = [523.25, 783.99]
const DISMISS_FREQUENCIES_HZ = [659.25, 493.88]
const NOTE_DURATION_S = 0.14
const NOTE_GAP_S = 0.06
const CHIME_GAIN = 0.12
const SCHEDULE_LEAD_S = 0.02
const HAPTIC_TICK_MS = 9
const HAPTIC_CONFIRM_MS = [12, 26, 14]

const SOUND_TARGETS = [
  "a[href]",
  "button",
  "[role='button']",
  "[role='tab']",
  ".suggestion",
].join(", ")

let sound = false
let audioContext: AudioContext | null = null

export function readStoredSound(): boolean {
  try {
    return localStorage.getItem(SOUND_STORAGE_KEY) === "on"
  } catch {
    return false
  }
}

export function writeStoredSound(enabled: boolean): void {
  try {
    localStorage.setItem(SOUND_STORAGE_KEY, enabled ? "on" : "off")
  } catch {}
}

export function vibrate(pattern: number | number[]): void {
  if (
    typeof navigator !== "undefined" &&
    typeof navigator.vibrate === "function"
  ) {
    try {
      navigator.vibrate(pattern)
    } catch {}
  }
}

/** Lazily created on the first real gesture, so autoplay policy is satisfied
 *  without priming the context on page load. */
function context(): AudioContext | null {
  if (audioContext !== null) return audioContext
  const w = window as unknown as {
    AudioContext?: typeof AudioContext
    webkitAudioContext?: typeof AudioContext
  }
  const Ctor = w.AudioContext ?? w.webkitAudioContext
  if (Ctor === undefined) return null
  try {
    audioContext = new Ctor()
  } catch {
    return null
  }
  return audioContext
}

function tone(
  ctx: AudioContext,
  frequency: number,
  at: number,
  gain: number,
  duration: number
): void {
  const osc = ctx.createOscillator()
  const amp = ctx.createGain()
  osc.type = "sine"
  osc.frequency.setValueAtTime(frequency, at)
  // Fast attack, exponential tail: a struck key, not a beep.
  amp.gain.setValueAtTime(0.0001, at)
  amp.gain.exponentialRampToValueAtTime(gain, at + 0.006)
  amp.gain.exponentialRampToValueAtTime(0.0001, at + duration)
  osc.connect(amp).connect(ctx.destination)
  osc.start(at)
  osc.stop(at + duration + 0.02)
}

/** Schedules only once the context is actually running. A note queued while the
 *  context is still suspended is stamped with a time in the past and never
 *  plays — which is how the toggle used to go silent after a click. */
async function withAudio(run: (ctx: AudioContext) => void): Promise<void> {
  const ctx = context()
  if (ctx === null) return
  if (ctx.state !== "running") {
    try {
      await ctx.resume()
    } catch {
      return
    }
  }
  if (ctx.state !== "running") return
  run(ctx)
}

function playSequence(
  frequencies: readonly number[],
  gain: number,
  duration: number,
  gap: number
): void {
  void withAudio((ctx) => {
    const start = ctx.currentTime + SCHEDULE_LEAD_S
    frequencies.forEach((frequency, index) => {
      tone(ctx, frequency, start + index * gap, gain, duration)
    })
  })
}

function playTick(): void {
  playSequence([TICK_FREQUENCY_HZ], TICK_GAIN, TICK_DURATION_S, 0)
}

function playHover(): void {
  playSequence([HOVER_FREQUENCY_HZ], HOVER_GAIN, HOVER_DURATION_S, 0)
}

export function playConfirm(): void {
  playSequence(CONFIRM_FREQUENCIES_HZ, CHIME_GAIN, NOTE_DURATION_S, NOTE_GAP_S)
}

/** Turning sound off still deserves an answer: the site falls silent right
 *  after this, so the dismissal is the only feedback the click gets. */
export function playDismiss(): void {
  playSequence(DISMISS_FREQUENCIES_HZ, CHIME_GAIN, NOTE_DURATION_S, NOTE_GAP_S)
}

/** Flips the shared flag, remembers the choice, and answers the press —
 *  a rising chime when sound turns on, a falling one when it goes silent. */
export function toggleSound(): boolean {
  sound = !sound
  writeStoredSound(sound)
  vibrate(HAPTIC_CONFIRM_MS)
  if (sound) playConfirm()
  else playDismiss()
  return sound
}

/** Delegated click and hover hooks so sound follows the whole interface, not
 *  just the header: links, footer, filter chips, and runtime-built contributor
 *  links all answer with a tick. Runs once at the shell level, so it survives
 *  client-side navigation. */
export function initSoundFeedback(): () => void {
  sound = readStoredSound()

  const click = (event: Event) => {
    if (!sound) return
    const target = event.target
    if (!(target instanceof Element)) return
    // The sound toggle carries its own confirm/dismiss handler.
    if (target.closest(".sound-toggle") !== null) return
    if (target.closest(SOUND_TARGETS) === null) return
    vibrate(HAPTIC_TICK_MS)
    playTick()
  }

  let lastHoverAt = 0
  const pointerover = (event: Event) => {
    if (!sound) return
    const over = event as PointerEvent
    // Touch and pen synthesise a pointerover immediately before the click.
    if (over.pointerType !== "mouse") return
    const target = event.target
    if (!(target instanceof Element)) return
    if (target.closest(".sound-toggle") !== null) return
    const entered = target.closest(SOUND_TARGETS)
    if (entered === null) return
    // pointerover also fires between a control's own descendants.
    const previous = over.relatedTarget
    if (previous instanceof Element && entered.contains(previous)) return
    const now = Date.now()
    if (now - lastHoverAt < HOVER_MIN_GAP_MS) return
    lastHoverAt = now
    playHover()
  }

  document.addEventListener("click", click)
  document.addEventListener("pointerover", pointerover)
  return () => {
    document.removeEventListener("click", click)
    document.removeEventListener("pointerover", pointerover)
  }
}
