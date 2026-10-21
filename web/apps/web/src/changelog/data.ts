/**
 * The changelog, generated from the repository's own commit history.
 *
 * There are no release tags yet, so the changelog is built from `git log`.
 * The grouping is by the feature the commit introduced, not by a hand-kept
 * list, so a new commit shows up without anyone remembering to edit a file.
 */

export type ChangeKind =
  | "language"
  | "engine"
  | "toolchain"
  | "verification"
  | "docs"
  | "site"
  | "build"

export type Change = {
  kind: ChangeKind
  title: string
  detail: string
  /** The commit short hash, when the entry came from the history. */
  commit?: string
  /** The date the change landed. */
  date?: string
  href?: string
}

export const KIND_META: Record<
  ChangeKind,
  { label: string; chip: string; order: number }
> = {
  language: {
    label: "Language",
    chip: "border-accent-strong/50 text-accent-strong",
    order: 0,
  },
  engine: {
    label: "Engine",
    chip: "border-primary/50 text-primary-foreground dark:text-primary",
    order: 1,
  },
  toolchain: {
    label: "Toolchain",
    chip: "text-muted-foreground",
    order: 2,
  },
  verification: {
    label: "Verification",
    chip: "border-emerald-600/50 text-emerald-600 dark:text-emerald-400",
    order: 3,
  },
  docs: {
    label: "Documentation",
    chip: "text-muted-foreground",
    order: 4,
  },
  site: {
    label: "Website",
    chip: "text-muted-foreground",
    order: 5,
  },
  build: {
    label: "Build & CI",
    chip: "text-muted-foreground",
    order: 6,
  },
}

/**
 * The changes, newest first. The order is the reading order; each entry
 * points at the documentation that now covers it.
 */
export const CHANGES: readonly Change[] = [
  {
    kind: "language",
    title: "`!=` is a first-class comparison",
    detail:
      "`!=` is now its own IR op (`ne`) rather than `eq` followed by `not`, so the optimizer and branch fusion see the real predicate. On floats it follows IEEE 754: `nan != nan` is true, `0.0 != -0.0` is false.",
    commit: "6003463",
    date: "2026-10-20",
    href: "/docs/operators#comparison-boolean",
  },
  {
    kind: "language",
    title: "`try` / `except` over the engine's own traps",
    detail:
      "A `try` block can now catch the engine's traps, over a closed set of seven names: `ZeroDivisionError`, `OverflowError`, `ValueError`, `IndexError`, `KeyError`, `EOFError`, and `Exception`. `else` and `finally` are supported. A bare `except:`, `except X as e`, and `raise` are refused.",
    commit: "6003463",
    date: "2026-10-20",
    href: "/docs/exceptions",
  },
  {
    kind: "language",
    title: "Raw syscalls, iteration helpers, and real pointers",
    detail:
      "`syscall(n, a1..a6)` is a compiler-recognized primitive that lowers to one IR op. A pointer argument is a real host address in both tiers, which is what makes the shipped TCP loopback possible. `enumerate(xs)` and `zip(xs, ys)` rewrite a counted loop in a `for` header.",
    commit: "ed0fefa",
    date: "2026-10-14",
    href: "/docs/syscalls",
  },
  {
    kind: "language",
    title: "Packed-field structs, end to end",
    detail:
      "`class P(Struct)` with typed fields, packed with no padding, so the byte layout matches a C `#pragma pack(1)` struct exactly. Narrow integer fields keep their sign when read back.",
    commit: "7b40f2d",
    date: "2026-10-13",
    href: "/docs/structs",
  },
  {
    kind: "language",
    title: "Explicit conversions and terminal input",
    detail:
      "`int(w, v)`, `float(64, v)` and `int(w, input(prompt))` are the only way to change a type or read a value: the width is always named, and a conversion that does not fit traps instead of truncating.",
    commit: "2e7bf73",
    date: "2026-10-12",
    href: "/docs/types#explicit-conversions",
  },
  {
    kind: "language",
    title: "`break` / `continue` and a three-argument `range`",
    detail:
      "Loop control flow is supported, with the rotated lowering and multi-exit loop guards it needs. `range(start, stop, step)` takes a step, and a zero step is refused at compile time and trapped at run time.",
    commit: "322fd90",
    date: "2026-10-12",
    href: "/docs/control-flow#break-continue",
  },
  {
    kind: "language",
    title: "Uninitialized declarations",
    detail:
      "A declaration with a type and no value introduces the name and the type, emits no code, and starts unassigned. Reading it before assignment is a compile-time refusal, not a run-time surprise.",
    commit: "2454bdf",
    date: "2026-10-09",
    href: "/docs/declarations#definite-assignment",
  },
  {
    kind: "language",
    title: "C-style modulo",
    detail:
      "`%` is the C remainder: the sign follows the dividend. `-7 % 3` is `-1` in Lithon and `2` in CPython. This is the machine's behaviour, and it is now pinned by the differential suites.",
    commit: "e0b5e0c",
    date: "2026-10-01",
    href: "/docs/semantics#modulo",
  },
  {
    kind: "language",
    title: "Float is a real type",
    detail:
      "`float[64]` is a first-class type with its own arithmetic, not a fallback for an untyped value. A float literal is `0.0`, never `0`.",
    commit: "61aec93",
    date: "2026-10-01",
    href: "/docs/types",
  },
  {
    kind: "language",
    title: "Lists, tuples, dicts and pointers",
    detail:
      "The fixed-capacity container family: `list[T, N]`, `tuple[T, N]`, `dict[K, V, B]` with a power-of-two bucket count, and typed pointers with a mandatory `_` prefix.",
    commit: "8eff192",
    date: "2026-10-07",
    href: "/docs/containers",
  },
  {
    kind: "engine",
    title: "Dynamic `int[64]` overflow traps",
    detail:
      "Plain `+`, `-` and `*` on `int[64]` now trap on signed overflow instead of silently wrapping. `wrap_add`, `wrap_sub` and `wrap_mul` are the explicit opt-out, and the wrapping result is pinned to be identical across tiers.",
    commit: "5326537",
    date: "2026-10-10",
    href: "/docs/operators#wrap-arith",
  },
  {
    kind: "engine",
    title: "Stack-depth guard parity",
    detail:
      "The native prologue and the interpreter bump the same shared recursion-depth counter and trap at the same depth with the same message, so a runaway recursive program fails byte-identically in both tiers.",
    commit: "8954151",
    date: "2026-10-11",
    href: "/docs/errors",
  },
  {
    kind: "engine",
    title: "Self tail calls become loops",
    detail:
      "A self tail call is rewritten into a loop, so tail recursion runs in O(1) stack. A call that is not in tail position stays recursive.",
    commit: "144daa5",
    date: "2026-09-30",
    href: "/docs/functions#recursion",
  },
  {
    kind: "engine",
    title: "SIMD auto-vectorization",
    detail:
      "The vectorizer emits AVX2 for counted loops over `int[32]` lists, 8 elements wide. The result is exact, not approximate: the reduction order is preserved.",
    commit: "7214631",
    date: "2026-10-08",
    href: "/docs/simd",
  },
  {
    kind: "engine",
    title: "Register coalescing",
    detail:
      "A merge takes over its source's dead register, so a phi copy does not need a fresh one.",
    commit: "801be23",
    date: "2026-10-03",
    href: "/docs/pipeline",
  },
  {
    kind: "engine",
    title: "Float comparisons fuse into the branch",
    detail:
      "A float comparison is fused into the branch instead of materialising a `bool` first, so the hot path carries no intermediate value.",
    commit: "5a06b72",
    date: "2026-10-12",
    href: "/docs/pipeline",
  },
  {
    kind: "toolchain",
    title: "`.lio` is the source extension",
    detail:
      "A Lithon program is a file with the `.lio` extension. The frontend refuses anything else, so the extension is what marks a file as Lithon rather than as ordinary Python.",
    commit: "6cbc856",
    date: "2026-10-20",
    href: "/docs/installation",
  },
  {
    kind: "toolchain",
    title: "The `lithon` CLI",
    detail:
      "`lithon script.lio` runs a program. `--strict` refuses rather than falling back to the interpreter, `--ir` prints the typed IR and stops, `--interp` forces the correctness oracle, and `-v` says which tier ran and why.",
    commit: "f62bf61",
    date: "2026-10-01",
    href: "/docs/installation#cli",
  },
  {
    kind: "verification",
    title: "The full gate, wired together",
    detail:
      "`tools/verify_all.sh` runs the whole stack: CTest, the general regression suite, the typed regression suite, the tier differential, and the per-shape differential fuzzers. It ends with `ALL CHECKS PASSED` or it does not pass.",
    commit: "988e763",
    date: "2026-09-29",
    href: "/docs/verification#the-gate",
  },
  {
    kind: "verification",
    title: "The encoder against GNU as",
    detail:
      "The hand-written x86-64 encoder is compared instruction by instruction against GNU `as`. 18,379 cases, 0 incorrect encodings. A byte difference is not automatically an error, because x86-64 often has several valid encodings.",
    commit: "edf4937",
    date: "2026-10-07",
    href: "/docs/verification#encoder-vs-as",
  },
  {
    kind: "verification",
    title: "Networking checked against a C oracle",
    detail:
      "A TCP loopback round trip, built entirely from raw syscalls, is compared against a C program issuing the identical syscalls. The C program is the oracle, because both Lithon tiers could be wrong in the same way.",
    commit: "ed0fefa",
    date: "2026-10-14",
    href: "/docs/networking",
  },
  {
    kind: "docs",
    title: "The documentation platform",
    detail:
      "Every example on this site is compiled by the real Lithon frontend and pinned to the repo's regression outputs, so a doc that drifts from the engine fails the build rather than misleading a reader.",
    commit: "427a9d4",
    date: "2026-10-10",
    href: "/docs",
  },
  {
    kind: "site",
    title: "The site moves to TanStack Start",
    detail:
      "The hand-rolled static site became a Turborepo monorepo: all ten routes migrated, RTL-first layout, and theme, sound and text-direction toggles that persist.",
    commit: "b540731",
    date: "2026-10-10",
    href: "/",
  },
  {
    kind: "site",
    title: "Executable docs",
    detail:
      "Examples carry their real IR and a step-by-step execution trace, and the machine view toggles every example over to its IR and x86-64 tab.",
    commit: "b71d5b6",
    date: "2026-10-10",
    href: "/docs/source-to-machine",
  },
  {
    kind: "build",
    title: "The benchmark bot",
    detail:
      "Mamba measures `fib(30)` on every push to `master`, writes `benchmarks/results/latest.json` and a per-commit snapshot under `benchmarks/results/history/`, and updates the README's benchmark block.",
    commit: "2b185ad",
    date: "2026-10-20",
    href: "/benchmarks",
  },
]
