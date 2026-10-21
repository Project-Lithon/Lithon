/**
 * An executable model of Lithon's IR.
 *
 * This is not a simulation of the docs: it parses the exact IR text the
 * frontend emits and executes it, so the trace, the register file, the memory
 * map and the data flow shown to the reader are all derived from the same
 * instructions the native tier compiles. Memory addresses are modelled the way
 * the encoder lays out a frame: a running `sub rsp` then slots addressed from
 * `rbp` downwards.
 */

export type IrInstr = {
  index: number
  op: string
  dest?: string
  args: string[]
  width?: string
  block: string
  text: string
}

export type IrFunction = {
  name: string
  params: string[]
  ret?: string
  blocks: string[]
  instrs: IrInstr[]
  start: number
  end: number
}

export type IrProgram = {
  functions: IrFunction[]
}

export type MemCell = {
  name: string
  address: string
  offset: number
  type: string
  value: number | bigint | string | boolean
}

export type IrStep = {
  index: number
  instr: IrInstr
  function: string
  block: string
  registers: { name: string; value: string }[]
  memory: MemCell[]
  written: MemCell | null
  produced: { register: string; value: string } | null
  consumed: { register: string; value: string }[]
  output?: string
  note?: string
}

export type IrRun = {
  steps: IrStep[]
  stdout: string[]
  error?: string
}

const I64_MIN = -(2n ** 63n)

function wrap64(v: bigint): bigint {
  const masked = (((v - I64_MIN) % 2n ** 64n) + 2n ** 64n) % 2n ** 64n
  return masked + I64_MIN
}

function fmtFloat(v: number): string {
  return Number.isInteger(v) ? `${v}.0` : String(v)
}

/**
 * The Linux x86-64 syscall numbers the docs' syscall examples can name.
 * A number that is not in this set is not a syscall the kernel knows, so
 * it comes back as -ENOSYS (-38) -- the same answer the engine's own
 * trampoline gives on a platform with no raw-syscall ABI.
 */
const KNOWN_SYSCALLS = new Set([
  0, // read
  1, // write
  2, // open
  3, // close
  9, // mmap
  10, // mprotect
  11, // munmap
  12, // brk
  13, // rt_sigaction
  14, // rt_sigprocmask
  39, // getpid
  60, // exit
  63, // uname
  102, // getuid
])

export function parseIr(text: string): IrProgram {
  const lines = text.split(/\r?\n/)
  const functions: IrFunction[] = []
  let fn: IrFunction | null = null
  let block = ""
  let index = 0

  for (const raw of lines) {
    const lineText = raw.trim()
    if (!lineText) continue

    if (lineText.startsWith("function ")) {
      const header = lineText.slice("function ".length)
      const [sig] = header.split(/:\s*(?=[a-zA-Z_]*$)/, 2)
      const fnMatch = /^([\w$]+)\s*\((.*)\)\s*(?:->\s*(.+))?$/.exec(sig.trim())
      fn = {
        name: fnMatch ? fnMatch[1] : sig.trim(),
        params:
          fnMatch && fnMatch[2]
            ? fnMatch[2]
                .split(",")
                .map((p) => p.trim().split(":")[0])
                .filter(Boolean)
            : [],
        ret: fnMatch?.[3]?.trim(),
        blocks: [],
        instrs: [],
        start: index,
        end: index,
      }
      functions.push(fn)
      block = ""
      continue
    }

    if (!fn) continue

    if (/^block\d+:$/.test(lineText)) {
      block = lineText.slice(0, -1)
      fn.blocks.push(block)
      continue
    }

    // strip the trailing width annotation: "store x, %0 : int[64]"
    const annotated =
      /^(.*?)\s*:\s*(int\[\d+\]|float\[\d+\]|bool|ptr\[[^\]]*\]|list\[[^\]]*\]|tuple\[[^\]]*\]|dict\[[^\]]*\])$/.exec(
        lineText
      )
    const body = annotated ? annotated[1].trim() : lineText
    const width = annotated ? annotated[2] : undefined

    let dest: string | undefined
    let rest = body
    const assign = /^%(\d+)\s*=\s*(.+)$/.exec(body)
    if (assign) {
      dest = `%${assign[1]}`
      rest = assign[2].trim()
    }

    const parts = rest.split(/\s+/).filter(Boolean)
    const op = parts[0]
    const args = parts.slice(1).map((a: string) => a.replace(/,$/, ""))

    fn.instrs.push({ index, op, dest, args, width, block, text: lineText })
    index += 1
  }

  for (const f of functions) {
    if (f.instrs.length > 0) {
      f.start = f.instrs[0].index
      f.end = f.instrs[f.instrs.length - 1].index
    }
  }
  return { functions }
}

function fmtKey(v: unknown): string | number {
  return typeof v === "bigint" || typeof v === "number" ? Number(v) : String(v)
}

function fmtValue(v: unknown): string {
  if (typeof v === "boolean") return v ? "True" : "False"
  if (typeof v === "bigint") return v.toString()
  if (typeof v === "number")
    return Number.isInteger(v) ? v.toString() : String(v)
  return String(v ?? "0")
}

const isFloatValue = (v: unknown) =>
  typeof v === "number" && !Number.isInteger(v)

/** Execute the IR and record every step. */
export function runIr(program: IrProgram, maxSteps = 2000): IrRun {
  const steps: IrStep[] = []
  const stdout: string[] = []

  const entry =
    program.functions.find((f) => f.name === "__main__") ??
    program.functions[program.functions.length - 1]

  if (program.functions.length === 0)
    return { steps, stdout, error: "no functions in IR" }

  const registers = new Map<string, unknown>()
  const memory = new Map<string, MemCell>()
  const lists = new Map<string, Map<string | number, unknown>>()
  const floatRegs = new Set<string>()
  const floatSlots = new Set<string>()
  let slotOffset = 8

  const byName = new Map(program.functions.map((f) => [f.name, f]))
  type Frame = { fn: IrFunction; pc: number; dest?: string }
  const frames: Frame[] = []
  let current = entry
  frames.push({ fn: current, pc: current.start })

  const allocSlot = (name: string, type: string): MemCell => {
    // one slot per (frame, name): recursion needs its own copy of `n`
    const key = `${frames.length}:${name}`
    const existing = memory.get(key)
    if (existing) return existing
    const cell: MemCell = {
      name,
      address: `rbp-0x${slotOffset.toString(16)}`,
      offset: slotOffset,
      type,
      value: 0n,
    }
    slotOffset += 8
    memory.set(key, cell)
    return cell
  }

  const lookupSlot = (name: string): MemCell | undefined =>
    memory.get(`${frames.length}:${name}`)

  /**
   * A struct is one blob with several fields at fixed byte offsets, so each
   * field needs its own storage: `fieldstore p, 0, %N` and
   * `fieldstore p, 8, %N` must not overwrite each other. The blob itself
   * keeps the overall address; the fields are keyed by (blob, offset).
   */
  const fieldCells = new Map<string, MemCell>()

  const fieldKey = (name: string, offset: number) =>
    `${frames.length}:${name}#${offset}`

  const allocField = (name: string, offset: number, type: string): MemCell => {
    const key = fieldKey(name, offset)
    const existing = fieldCells.get(key)
    if (existing) return existing
    const blob = allocSlot(name, type)
    const cell: MemCell = {
      name: `${name}+${offset}`,
      address: `${blob.address}+${offset}`,
      offset: blob.offset + offset,
      type,
      value: 0n,
    }
    fieldCells.set(key, cell)
    return cell
  }

  const pushFrame = (fn: IrFunction, args: unknown[]) => {
    frames.push({ fn, pc: fn.start, dest: undefined })
    current = fn
    // parameters arrive positionally: first arg -> a, second -> b
    args.forEach((v, i) => {
      const cell = allocSlot(fn.params[i] ?? `arg${i}`, "int[64]")
      cell.value = v as MemCell["value"]
    })
  }

  let guard = 0
  let error: string | undefined
  // The engine lowers `print(x)` into `print_bare, %N` + `print_nl`, so the
  // value is held here until the closing newline arrives.
  let pendingPrint: string | undefined
  // The handler registered by the innermost `tryenter`, if any.
  let pendingHandler: { trapName: string; handler: string } | undefined
  // Set when a trap fires: the block to jump to, or undefined when the trap
  // is not catchable and the run has to stop.
  let trapTarget: string | undefined

  const snapshot = () => ({
    registers: [...registers.entries()]
      .filter(([k]) => k.startsWith("%"))
      .slice(-8)
      .map(([name, value]) => ({ name, value: fmtValue(value) })),
    memory: [
      ...memory.values(),
      // struct fields live in their own map; show them next to the blob
      ...[...fieldCells.values()],
    ].map((m) => ({ ...m })),
  })

  while (frames.length > 0 && guard < maxSteps && !error) {
    const frame = frames.at(-1)
    if (frame === undefined) break
    const instr = current.instrs.find((i) => i.index === frame.pc)
    if (instr === undefined) {
      frames.pop()
      const back = frames.at(-1)
      if (back === undefined) break
      current = back.fn
      continue
    }
    guard += 1

    /**
     * Raise a trap. If the innermost `tryenter` catches this trap name, the
     * run continues at the handler block; otherwise the run stops with the
     * engine's own message. The catchable set is the same closed set the
     * engine uses: the six named traps plus `Exception` for all of them.
     */
    const trap = (trapName: string, message: string) => {
      const handler = pendingHandler
      const catches =
        handler !== undefined &&
        (handler.trapName === trapName || handler.trapName === "Exception")
      if (!catches) {
        error = message
        return
      }
      trapTarget = handler.handler
      // the protected region is left by jumping to the handler
      pendingHandler = undefined
    }

    let consumed: { register: string; value: string }[] = []
    let returnedValue: unknown
    const read = (tok: string | undefined): unknown => {
      if (!tok) return 0n
      if (tok.startsWith("%")) {
        // %N is per-function and per-frame: fib(3) and fib(2) both use %8
        const key = `${frames.length}:${current.name}:${tok}`
        const v = registers.get(key) ?? registers.get(tok) ?? 0n
        if (printedArg !== tok)
          consumed.push({ register: tok, value: fmtValue(v) })
        return v
      }
      const cell = lookupSlot(tok)
      if (cell) {
        if (printedArg !== tok)
          consumed.push({ register: tok, value: fmtValue(cell.value) })
        return cell.value
      }
      return 0n
    }

    /** Follow a pointer through registers and slots alike. */
    const resolvePointer = (tok: string | undefined): string | undefined => {
      if (!tok) return undefined
      if (tok.startsWith("%")) {
        const scoped = `${frames.length}:${current.name}:${tok}`
        const v = registers.get(scoped) ?? registers.get(tok)
        if (typeof v === "string" && v.startsWith("&")) return v.slice(1)
        return undefined
      }
      const cell = lookupSlot(tok)
      if (cell && typeof cell.value === "string" && cell.value.startsWith("&"))
        return cell.value.slice(1)
      return undefined
    }

    let produced: { register: string; value: string } | null = null
    let written: MemCell | null = null
    let output: string | undefined
    let note: string | undefined
    let printedArg: string | undefined
    let jumpTo: string | undefined
    let framePushed = false

    switch (instr.op) {
      case "const_i64":
      case "const_f64":
      case "const_bool": {
        const isFloat = instr.op === "const_f64"
        const v = isFloat
          ? Number(instr.args[0])
          : instr.op === "const_bool"
            ? instr.args[0] !== "0"
            : BigInt(instr.args[0])
        if (instr.dest) {
          registers.set(`${frames.length}:${current.name}:${instr.dest}`, v)
          if (isFloat)
            floatRegs.add(`${frames.length}:${current.name}:${instr.dest}`)
          else floatRegs.delete(instr.dest)
          produced = { register: instr.dest, value: fmtValue(v) }
        }
        break
      }
      case "load": {
        const v = read(instr.args[0])
        if (instr.dest) {
          registers.set(`${frames.length}:${current.name}:${instr.dest}`, v)
          if (instr.args[0] && floatSlots.has(instr.args[0]))
            floatRegs.add(`${frames.length}:${current.name}:${instr.dest}`)
          produced = { register: instr.dest, value: fmtValue(v) }
        }
        break
      }
      case "store": {
        const name = instr.args[0]
        const cell = allocSlot(name, instr.width ?? "int[64]")
        // `store xs : list[int[64], 5]` has no value: it just reserves the
        // container's storage, which is already zeroed by `allocSlot`.
        if (instr.args.length > 1) {
          const v = read(instr.args[1])
          cell.value = v as number | bigint | boolean
          written = { ...cell }
        }
        if (
          instr.width?.startsWith("float") ||
          (instr.args[1] &&
            floatRegs.has(`${frames.length}:${current.name}:${instr.args[1]}`))
        )
          floatSlots.add(name)
        note =
          instr.args.length > 1
            ? `${cell.address} â† ${fmtValue(cell.value)}`
            : `${cell.address} reserved`
        break
      }
      case "fieldstore": {
        // fieldstore p, offset, %N   -- write one field of a struct blob
        const name = instr.args[0]
        const offset = Number(instr.args[1])
        const v = read(instr.args[2])
        const cell = allocField(name, offset, instr.width ?? "int[64]")
        cell.value = v as number | bigint | boolean
        written = { ...cell }
        note = `${cell.address} â† ${fmtValue(v)}`
        break
      }
      case "fieldload": {
        // fieldload p, offset   -- read one field of a struct blob
        const name = instr.args[0]
        const offset = Number(instr.args[1])
        const cell = allocField(name, offset, instr.width ?? "int[64]")
        const v = cell.value
        if (instr.dest) {
          registers.set(`${frames.length}:${current.name}:${instr.dest}`, v)
          if (instr.width?.startsWith("float"))
            floatRegs.add(`${frames.length}:${current.name}:${instr.dest}`)
          produced = { register: instr.dest, value: fmtValue(v) }
        }
        note = `${cell.address} â†’ ${fmtValue(v)}`
        break
      }
      case "guardnonzero": {
        // guardnonzero %N -- the runtime guard the engine emits before a
        // division; a zero divisor is a trap, not a value.
        const v = read(instr.args[0])
        if (typeof v === "bigint" && v === 0n) {
          trap("ZeroDivisionError", "LITHON-E0301: division by zero")
        }
        note = "guard: divisor is not zero"
        break
      }
      case "tryenter": {
        // tryenter <TrapName>, <handlerBlock> -- mark the handler block and
        // the region it guards. A trap raised inside the region jumps to
        // the handler instead of stopping the program.
        const trapName = instr.args[0] ?? "Exception"
        const handler = instr.args[1]
        pendingHandler = { trapName, handler }
        note = `guard against ${trapName} â†’ ${handler}`
        break
      }
      case "tryleave": {
        // tryleave -- end the protected region on the normal path.
        pendingHandler = undefined
        note = "protected region ends"
        break
      }
      case "trycatch": {
        // trycatch <TrapName> -- the landing pad: this block runs when the
        // named trap was raised inside the protected region.
        note = `caught ${instr.args[0] ?? "trap"}`
        break
      }
      case "syscall": {
        // syscall number, a1..a6 -- the raw kernel primitive. The docs
        // model the *unsupported* case: a number the kernel does not know
        // comes back as -ENOSYS (-38), which is exactly what the engine's
        // own trampoline returns on a platform with no raw-syscall ABI.
        const number = read(instr.args[0])
        const known = KNOWN_SYSCALLS.has(Number(number))
        const v = known ? 0n : -38n
        if (instr.dest) {
          registers.set(`${frames.length}:${current.name}:${instr.dest}`, v)
          produced = { register: instr.dest, value: fmtValue(v) }
        }
        note = known
          ? `syscall ${fmtValue(number)}`
          : `syscall ${fmtValue(number)} â†’ -ENOSYS (-38)`
        break
      }
      case "add":
      case "sub":
      case "mul":
      case "div":
      case "mod": {
        const a = read(instr.args[0])
        const b = read(instr.args[1])
        // `/` is float division even for ints; the rest stay in int land
        // unless an operand is already a float
        const floatPath =
          instr.op === "div" || isFloatValue(a) || isFloatValue(b)
        let v: unknown
        if (floatPath) {
          const x = Number(a)
          const y = Number(b)
          if (instr.op === "add") v = x + y
          else if (instr.op === "sub") v = x - y
          else if (instr.op === "mul") v = x * y
          else if (instr.op === "div") v = y === 0 ? NaN : x / y
          else v = y === 0 ? NaN : x % y
        } else {
          const x = a as bigint
          const y = b as bigint
          if (instr.op === "sub") v = wrap64(x - y)
          else if (instr.op === "mul") v = wrap64(x * y)
          else if (instr.op === "mod") {
            // modulo by zero is a trap, not a value
            if (y === 0n) {
              trap("ZeroDivisionError", "LITHON-E0302: modulo by zero")
              v = 0n
            } else {
              v = x % y
            }
          } else v = wrap64(x + y)
        }
        if (instr.dest) {
          registers.set(`${frames.length}:${current.name}:${instr.dest}`, v)
          if (floatPath)
            floatRegs.add(`${frames.length}:${current.name}:${instr.dest}`)
          produced = { register: instr.dest, value: fmtValue(v) }
        }
        break
      }
      case "wrapadd":
      case "wrapsub":
      case "wrapmul": {
        const a = read(instr.args[0]) as bigint
        const b = read(instr.args[1]) as bigint
        const v =
          instr.op === "wrapadd"
            ? wrap64(a + b)
            : instr.op === "wrapsub"
              ? wrap64(a - b)
              : wrap64(a * b)
        if (instr.dest) {
          registers.set(`${frames.length}:${current.name}:${instr.dest}`, v)
          produced = { register: instr.dest, value: fmtValue(v) }
        }
        note = "explicit wrapping arithmetic"
        break
      }
      case "lt":
      case "gt":
      case "eq":
      case "ne": {
        const a = read(instr.args[0])
        const b = read(instr.args[1])
        const cmp =
          typeof a === "number" || typeof b === "number"
            ? Number(a) === Number(b)
              ? 0
              : Number(a) < Number(b)
                ? -1
                : 1
            : (a as bigint) === (b as bigint)
              ? 0
              : (a as bigint) < (b as bigint)
                ? -1
                : 1
        const v =
          instr.op === "lt"
            ? cmp < 0
            : instr.op === "gt"
              ? cmp > 0
              : instr.op === "eq"
                ? cmp === 0
                : cmp !== 0
        if (instr.dest) {
          registers.set(`${frames.length}:${current.name}:${instr.dest}`, v)
          produced = { register: instr.dest, value: fmtValue(v) }
        }
        break
      }
      case "shl":
      case "shr":
      case "band":
      case "bor":
      case "bxor":
      case "and":
      case "or":
      case "xor": {
        const a = read(instr.args[0]) as bigint
        const b = read(instr.args[1]) as bigint
        // The engine spells the *bitwise* ops `band`/`bor`/`bxor` and keeps
        // `and`/`or`/`xor` for the logical ones, so both spellings are
        // accepted here and treated as bitwise.
        const v =
          instr.op === "shl"
            ? wrap64(a << (b & 63n))
            : instr.op === "shr"
              ? a >> (b & 63n)
              : instr.op === "band" || instr.op === "and"
                ? a & b
                : instr.op === "bor" || instr.op === "or"
                  ? a | b
                  : a ^ b
        if (instr.dest) {
          registers.set(`${frames.length}:${current.name}:${instr.dest}`, v)
          produced = { register: instr.dest, value: fmtValue(v) }
        }
        break
      }
      case "not": {
        const a = read(instr.args[0])
        const v = typeof a === "boolean" ? !a : wrap64(~(a as bigint))
        if (instr.dest) {
          registers.set(`${frames.length}:${current.name}:${instr.dest}`, v)
          produced = { register: instr.dest, value: fmtValue(v) }
        }
        break
      }
      case "DictStore":
      case "IndexStore": {
        // DictStore d, key, value   /   IndexStore xs, idx, value
        const name = instr.args[0]
        const key =
          instr.op === "DictStore"
            ? fmtKey(read(instr.args[1]))
            : Number(read(instr.args[1]))
        const v = read(instr.args[2])
        const table = lists.get(name) ?? new Map<string | number, unknown>()
        table.set(key, v)
        lists.set(name, table)
        written = {
          name: `${name}[${key}]`,
          address: `heap+${(Number(key) || 0) * 8}`,
          offset: (Number(key) || 0) * 8,
          type: instr.width ?? "int[64]",
          value: v as number,
        }
        note = `${name}[${key}] â† ${fmtValue(v)}`
        break
      }
      case "Index":
      case "DictIndex":
      case "valueof": {
        const tok = instr.args[0]
        const target = resolvePointer(tok)
        if (target !== undefined) {
          const cell = lookupSlot(target)
          const v = cell ? cell.value : 0n
          if (instr.dest) {
            registers.set(`${frames.length}:${current.name}:${instr.dest}`, v)
            produced = { register: instr.dest, value: fmtValue(v) }
          }
          note = `deref to ${cell?.address ?? "?"} (${target})`
        } else {
          const tableName = instr.args[0]
          const key =
            instr.op === "DictIndex"
              ? fmtKey(read(instr.args[1]))
              : Number(read(instr.args[1]))
          const v = lists.get(tableName)?.get(key) ?? 0n
          if (instr.dest) {
            registers.set(`${frames.length}:${current.name}:${instr.dest}`, v)
            produced = { register: instr.dest, value: fmtValue(v) }
          }
        }
        break
      }
      case "addressof": {
        // a pointer is modelled as "&slot": it survives stores and loads
        if (instr.dest) {
          registers.set(
            `${frames.length}:${current.name}:${instr.dest}`,
            `&${instr.args[0]}`
          )
          produced = { register: instr.dest, value: `&${instr.args[0]}` }
          note = `&${instr.args[0]} â†’ stack address`
        }
        break
      }
      case "call": {
        const callee = instr.args[0]
        if (callee === "print" || callee === "print_bare" || callee === "print_sep") {
          // The engine lowered `print(x)` into two calls: `print_bare, %N`
          // (emit the value with no separator) followed by `print_nl`
          // (emit the newline). The older single-call form `print, %N` is
          // still accepted so hand-written IR from older docs keeps working.
          printedArg = instr.args[1]
          const v = read(instr.args[1])
          const isFloat =
            floatRegs.has(
              `${frames.length}:${current.name}:${instr.args[1]}`
            ) || (instr.args[1] ? floatSlots.has(instr.args[1]) : false)
          const line =
            isFloat && typeof v === "number" ? fmtFloat(v) : fmtValue(v)
          if (callee === "print") {
            // legacy single-call print: the value and the newline together
            stdout.push(line)
            output = line
          } else {
            // print_bare / print_sep: the value only; print_nl closes the line
            pendingPrint = line
            output = line
          }
          printedArg = undefined
        } else if (callee === "print_nl") {
          stdout.push(pendingPrint ?? "")
          output = pendingPrint ?? ""
          pendingPrint = undefined
        } else {
          const target = byName.get(callee)
          if (target) {
            const args = instr.args.slice(1).map((a) => read(a))
            // remember, per frame, which register receives the result
            const caller = frames.at(-1)
            if (caller) caller.dest = instr.dest
            consumed = consumed.filter(
              (c) => !instr.args.slice(1).includes(c.register)
            )
            pushFrame(target, args)
            framePushed = true
            note = `enter ${callee}(${args.map((a) => fmtValue(a)).join(", ")})`
          } else {
            note = `call ${callee}: not modelled`
          }
        }
        break
      }
      case "branch": {
        const cond = read(instr.args[0])
        const truthy =
          cond === true || (typeof cond === "bigint" && cond !== 0n)
        const target = truthy ? instr.args[1] : instr.args[2]
        note = `branch on ${fmtValue(cond)} â†’ ${target}`
        jumpTo = target
        break
      }
      case "jump":
        note = `jump ${instr.args[0]}`
        jumpTo = instr.args[0]
        break
      case "return": {
        const value = instr.args.length > 0 ? read(instr.args[0]) : undefined
        if (frames.length <= 1) {
          returnedValue = value
          break
        }
        frames.pop()
        const back = frames.at(-1)
        if (back === undefined) break
        current = back.fn
        // the caller recorded where it wants the result before pushing us
        if (back.dest) {
          const d = back.dest
          const key = `${frames.length}:${back.fn.name}:${d}`
          registers.set(key, value ?? 0n)
          produced = { register: d, value: fmtValue(value ?? 0n) }
          note = `returned to ${back.fn.name}`
        }
        // caller advances past its own call on the next tick
        back.pc += 1
        continue
      }
      default:
        note = `${instr.op} (not modelled)`
    }

    const snap = snapshot()
    steps.push({
      index: steps.length,
      instr,
      function: current.name,
      block: instr.block,
      registers: snap.registers,
      memory: snap.memory,
      written,
      produced,
      consumed,
      output,
      note,
    })

    if (instr.op === "return" && returnedValue !== undefined) break
    if (framePushed) {
      // control is now in the callee; do not advance its pc
      framePushed = false
      continue
    }
    // re-read the top frame: a branch may have changed nothing, but a
    // continue from a return may have popped one
    const live = frames.at(-1)
    if (live === undefined) break
    if (trapTarget) {
      // a trap was caught: jump to the handler block
      const target = current.instrs.find((i) => i.block === trapTarget)
      if (target) live.pc = target.index
      else live.pc += 1
      trapTarget = undefined
    } else if (jumpTo) {
      const target =
        current.instrs.find((i) => i.block === jumpTo && i.index > live.pc) ??
        current.instrs.find((i) => i.block === jumpTo)
      if (target) live.pc = target.index
      else live.pc += 1
    } else {
      live.pc += 1
    }
  }

  const run: IrRun = { steps, stdout }
  if (error) run.error = error
  else if (guard >= maxSteps) run.error = `stopped after ${maxSteps} steps`
  return run
}

export function irOutputPreview(run: IrRun): string | undefined {
  return run.stdout.length > 0 ? run.stdout.join("\n") : undefined
}
