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

  const snapshot = () => ({
    registers: [...registers.entries()]
      .filter(([k]) => k.startsWith("%"))
      .slice(-8)
      .map(([name, value]) => ({ name, value: fmtValue(value) })),
    memory: [...memory.values()].map((m) => ({ ...m })),
  })

  while (frames.length > 0 && guard < maxSteps) {
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
        const v = read(instr.args[1])
        const cell = allocSlot(name, instr.width ?? "int[64]")
        cell.value = v as number | bigint | boolean
        written = { ...cell }
        if (
          instr.width?.startsWith("float") ||
          (instr.args[1] &&
            floatRegs.has(`${frames.length}:${current.name}:${instr.args[1]}`))
        )
          floatSlots.add(name)
        note = `${cell.address} ← ${fmtValue(v)}`
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
          else if (instr.op === "mod") v = y === 0n ? 0n : x % y
          else v = wrap64(x + y)
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
      case "and":
      case "or":
      case "xor": {
        const a = read(instr.args[0]) as bigint
        const b = read(instr.args[1]) as bigint
        const v =
          instr.op === "shl"
            ? wrap64(a << (b & 63n))
            : instr.op === "shr"
              ? a >> (b & 63n)
              : instr.op === "and"
                ? a & b
                : instr.op === "or"
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
        note = `${name}[${key}] ← ${fmtValue(v)}`
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
          note = `&${instr.args[0]} → stack address`
        }
        break
      }
      case "call": {
        const callee = instr.args[0]
        if (callee === "print") {
          printedArg = instr.args[1]
          const v = read(instr.args[1])
          const isFloat =
            floatRegs.has(
              `${frames.length}:${current.name}:${instr.args[1]}`
            ) || (instr.args[1] ? floatSlots.has(instr.args[1]) : false)
          const line =
            isFloat && typeof v === "number" ? fmtFloat(v) : fmtValue(v)
          stdout.push(line)
          output = line
          printedArg = undefined
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
        note = `branch on ${fmtValue(cond)} → ${target}`
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
    if (jumpTo) {
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
  if (guard >= maxSteps) run.error = `stopped after ${maxSteps} steps`
  return run
}

export function irOutputPreview(run: IrRun): string | undefined {
  return run.stdout.length > 0 ? run.stdout.join("\n") : undefined
}
