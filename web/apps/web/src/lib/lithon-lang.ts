import {
  HighlightStyle,
  StreamLanguage,
  type StreamParser,
} from "@codemirror/language"
import { tags as t } from "@lezer/highlight"

/**
 * Lithon is Python plus extended PEP-526 annotations and a small built-in
 * set, so the highlighter layers Lithon's own vocabulary (widthed types,
 * pointer prefix, wrap_* family) on top of a Python-shaped token stream
 * rather than shipping a separate parser.
 */

const KEYWORDS = new Set([
  "def",
  "return",
  "if",
  "elif",
  "else",
  "for",
  "while",
  "in",
  "and",
  "or",
  "not",
  "pass",
  "break",
  "continue",
  "import",
  "from",
  "as",
])

const BUILTINS = new Set([
  "print",
  "len",
  "range",
  "addressof",
  "valueof",
  "contains",
  "wrap_add",
  "wrap_sub",
  "wrap_mul",
  "True",
  "False",
])

/** int[64], float[64], ptr[T], list[T, N], and the bare forms. */
const TYPE_HEAD = /^(?:int|float|uint|bool|ptr|list|tuple|dict|str)\b/

/** Consume a balanced `[...]` group, e.g. the `[int[64], int[64], 4]` after dict. */
function consumeBrackets(stream: any) {
  if (stream.peek() !== "[") return
  let depth = 0
  while (!stream.eol()) {
    const ch = stream.next()
    if (ch === undefined) return
    if (ch === "[") depth++
    else if (ch === "]") {
      depth--
      if (depth === 0) return
    }
  }
}

const parser: StreamParser<Record<string, never>> = {
  name: "lithon",
  startState: () => ({}),
  token(stream) {
    if (stream.eatSpace()) return null

    if (stream.match(/#.*/)) return "comment"

    // strings: triple-quoted or single-quoted, with escapes
    if (stream.match(/[rbuf]{0,2}"""/) || stream.match(/[rbuf]{0,2}'''/)) {
      const quote = stream.current().slice(-3)
      while (!stream.eol() && !stream.match(quote)) stream.next()
      return "string"
    }
    if (stream.match(/[rbuf]{0,2}"/) || stream.match(/[rbuf]{0,2}'/)) {
      const quote = stream.current().slice(-1)
      stream.eatWhile((ch: string) => ch !== quote && ch !== "\n")
      stream.eat(quote)
      return "string"
    }

    if (
      stream.match(
        /\b0[xX][0-9a-fA-F_]+\b|\b0[bB][01_]+\b|\b\d[\d_]*(\.[\d_]+)?([eE][+-]?\d+)?/
      )
    )
      return "number"

    if (stream.match(TYPE_HEAD)) {
      consumeBrackets(stream)
      return "typeName"
    }

    if (stream.match(/[A-Za-z_][A-Za-z0-9_]*/)) {
      const word = stream.current()
      if (KEYWORDS.has(word)) return "keyword"
      if (BUILTINS.has(word)) return "builtin"
      if (word.startsWith("_")) return "variableName"
      return "variableName2"
    }

    if (
      stream.match(/<<|>>|<=|>=|==|!=|\+=|-=|\*=|\/=|%=|\*\*|[-+*/%@&|^~<>=]/)
    )
      return "operator"

    if (stream.match(/[[\]{}()]/)) return "bracket"

    stream.next()
    return null
  },
  languageData: {
    commentTokens: { line: "#" },
    closeBrackets: { brackets: ["(", "[", "{", "'", '"'] },
  },
}

export const lithonLanguage = StreamLanguage.define({
  ...parser,
  /**
   * Stream token names have to resolve to real @lezer/highlight tags. Our
   * private names (`builtin`, `variableName2`) do not exist there, so map
   * every one of them; an unmapped name silently kills all highlighting.
   */
  tokenTable: {
    comment: t.comment,
    keyword: t.keyword,
    builtin: t.special(t.name),
    typeName: t.typeName,
    string: t.string,
    number: t.number,
    operator: t.operator,
    bracket: t.bracket,
    variableName: t.variableName,
    variableName2: t.name,
    atom: t.atom,
    invalid: t.invalid,
  },
})

/** Hover documentation: what each keyword, built-in and type means. */
export const LITHON_DOCS: Record<
  string,
  { kind: string; doc: string } | undefined
> = {
  int: {
    kind: "type",
    doc: "signed fixed-width integer: int[8], int[16], int[32], int[64]",
  },
  float: { kind: "type", doc: "IEEE-754 binary64. Only float[64] exists." },
  bool: { kind: "type", doc: "one-byte boolean, True / False" },
  ptr: {
    kind: "type",
    doc: "typed pointer, ptr[T]. The binding name must start with _",
  },
  list: {
    kind: "type",
    doc: "fixed-capacity list, list[T, N]. Bare declaration reserves it.",
  },
  tuple: { kind: "type", doc: "fixed-length tuple, tuple[T, N]" },
  dict: { kind: "type", doc: "constant-keyed table, dict[K, V, B]" },
  print: {
    kind: "built-in",
    doc: "print(value): write one scalar and a newline",
  },
  len: {
    kind: "built-in",
    doc: "len(container): capacity, which is part of the type",
  },
  range: {
    kind: "built-in",
    doc: "range(n) / range(a, b): loop values, width-checked at compile time",
  },
  addressof: { kind: "built-in", doc: "addressof(x): ptr[T] to scalar x" },
  valueof: { kind: "built-in", doc: "valueof(_p): read the scalar at _p" },
  contains: {
    kind: "built-in",
    doc: "contains(d, k): bool, is key k present in dict d",
  },
  wrap_add: { kind: "built-in", doc: "wrap_add(a, b): wrapping integer add" },
  wrap_sub: { kind: "built-in", doc: "wrap_sub(a, b): wrapping integer sub" },
  wrap_mul: { kind: "built-in", doc: "wrap_mul(a, b): wrapping integer mul" },
  def: {
    kind: "keyword",
    doc: "define a function. Parameters and return type are mandatory",
  },
  return: { kind: "keyword", doc: "return a value from a function" },
  if: { kind: "keyword", doc: "conditional. The condition must be a bool" },
  elif: { kind: "keyword", doc: "else-if" },
  else: { kind: "keyword", doc: "fallback branch" },
  for: {
    kind: "keyword",
    doc: "loop over range(N). The loop variable is a normal binding",
  },
  while: { kind: "keyword", doc: "conditional loop" },
  and: { kind: "keyword", doc: "boolean and, on bool operands only" },
  or: { kind: "keyword", doc: "boolean or, on bool operands only" },
  not: { kind: "keyword", doc: "boolean not" },
  pass: { kind: "keyword", doc: "no-op statement" },
  break: { kind: "keyword", doc: "leave the innermost loop" },
  continue: { kind: "keyword", doc: "next iteration" },
}

const WIDTH_HINTS: Record<string, string | undefined> = {
  "8": "one byte, range -128..127",
  "16": "two bytes, range -32768..32767",
  "32": "four bytes",
  "64": "eight bytes, the machine word",
}

export function lithonWordInfo(word: string): {
  kind: string
  doc: string
} | null {
  const direct = LITHON_DOCS[word]
  if (direct) return direct
  const hint = WIDTH_HINTS[word]
  if (/^\d+$/.test(word) && hint) {
    return { kind: "type", doc: `int[${word}]: ${hint}` }
  }
  return null
}

export const lithonHighlightStyle = HighlightStyle.define([
  { tag: t.comment, color: "var(--code-comment)", fontStyle: "italic" },
  { tag: t.keyword, color: "var(--code-keyword)", fontWeight: "600" },
  { tag: t.special(t.name), color: "var(--code-builtin)" },
  { tag: t.typeName, color: "var(--code-type)", fontWeight: "600" },
  { tag: t.string, color: "var(--code-string)" },
  { tag: t.number, color: "var(--code-number)" },
  { tag: t.operator, color: "var(--code-operator)" },
  { tag: t.bracket, color: "var(--code-bracket)" },
  { tag: t.variableName, color: "var(--code-variable)" },
  { tag: t.name, color: "var(--code-variable-2)" },
  { tag: t.invalid, color: "var(--destructive)" },
])
