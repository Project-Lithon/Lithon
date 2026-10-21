import type { PagePriority } from "./model"

/**
 * Index-page emphasis, kept in one place so the reading path is a single
 * editable list rather than a field scattered across every page.
 *
 * `essential`    - on the recommended path: read these to use the language.
 * `recommended`  - worth reading once, then looked up.
 * `reference`    - a lookup table, not a read.
 */
export const PAGE_PRIORITY: Partial<Record<string, PagePriority>> = {
  // Start here
  welcome: "essential",
  "hello-world": "essential",
  installation: "recommended",
  "mental-model": "essential",

  // The language
  types: "essential",
  declarations: "essential",
  "control-flow": "essential",
  functions: "essential",
  operators: "essential",
  containers: "essential",
  pointers: "recommended",
  structs: "essential",
  enums: "essential",
  match: "essential",
  methods: "recommended",
  "type-functions": "reference",
  syscalls: "reference",
  strings: "essential",
  exceptions: "essential",
  networking: "reference",
  "python-divergences": "essential",

  // Inside the engine
  architecture: "essential",
  "dual-tier": "recommended",
  typechecking: "recommended",
  pipeline: "reference",
  simd: "reference",

  // Reference
  errors: "essential",
  builtins: "essential",
  semantics: "essential",
  verification: "recommended",
  limits: "essential",

  // Machine view
  "source-to-machine": "recommended",
  "registers-abi": "reference",
  "instruction-encoding": "reference",
}

/**
 * What a reader will be able to do after finishing a page. Shown on the
 * docs index so the path is legible before you commit to it.
 */
export const PAGE_OUTCOME: Partial<Record<string, string>> = {
  welcome:
    "Understand what Lithon is, what it refuses to do, and why the refusal is the feature.",
  "hello-world":
    "Write, verify and run your first natively-executed program end to end.",
  installation: "Build the engine and prove the install works.",
  "mental-model":
    "Hold the one rule that makes the rest of the language checkable: prove before you run.",
  types: "Pick the right width for every value, and know which conversions are legal.",
  declarations: "Give every name a type and a value, and be refused when you have not.",
  "control-flow":
    "Write every branch and loop the language supports, and know which ones it does not.",
  functions: "Give a function a contract the compiler can check, and call it safely.",
  operators: "Predict the result of every expression, including the ones that surprise CPython.",
  containers: "Model fixed-size data without a heap, and know each container's rules.",
  pointers: "Reach raw memory deliberately, with the naming rule that keeps it visible.",
  structs: "Lay out packed storage whose bytes match a C struct exactly.",
  enums: "Model a tagged union and read it back safely with match.",
  match: "Dispatch on an enum exhaustively, and read a variant's payload safely.",
  methods: "Give a struct behaviour, and understand why it is inlined.",
  "type-functions": "Build reusable types at compile time.",
  syscalls: "Call the kernel directly, with the argument rules both tiers agree on.",
  strings: "Use bounded byte runs without a heap or an allocator.",
  exceptions: "Recover from the seven engine traps and know the rest is refused.",
  networking: "Open a real socket from Lithon and pass the byte-order test.",
  "python-divergences":
    "Port Python code without being surprised by a silent semantic change.",
  architecture: "Trace a program from .lio source to executable memory.",
  "dual-tier": "Understand what Tier-0 and Tier-1 each do, and when each runs.",
  typechecking: "Read a diagnostic and know exactly which rule you broke.",
  pipeline: "See the optimizer and SSA passes that run before the encoder.",
  simd: "Know when a loop becomes AVX2 and why the result is still exact.",
  errors: "Look up any error code and what it means.",
  builtins: "Look up any built-in, its signature and its rules.",
  semantics: "Look up the exact meaning of the operations that surprise people.",
  verification: "Check the claims instead of taking them on trust.",
  limits: "Know exactly what is not implemented yet.",
  "source-to-machine": "Read the IR and the machine code for any example.",
  "registers-abi": "Know where every argument and return value lives.",
  "instruction-encoding": "Read the encoder's output byte by byte.",
}
