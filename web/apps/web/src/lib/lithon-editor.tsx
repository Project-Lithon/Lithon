import { useEffect, useRef } from "react"
import { EditorState } from "@codemirror/state"
import {
  EditorView,
  hoverTooltip,
  keymap,
  lineNumbers,
  highlightActiveLine,
  highlightActiveLineGutter,
  drawSelection,
  rectangularSelection,
  highlightSpecialChars,
  placeholder,
} from "@codemirror/view"
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands"
import {
  bracketMatching,
  foldGutter,
  foldKeymap,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language"
import {
  autocompletion,
  closeBrackets,
  closeBracketsKeymap,
  completionKeymap,
  type CompletionContext,
} from "@codemirror/autocomplete"
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search"
import {
  lintGutter,
  lintKeymap,
  setDiagnostics,
  type Diagnostic,
} from "@codemirror/lint"

import {
  lithonHighlightStyle,
  lithonLanguage,
  lithonWordInfo,
} from "./lithon-lang"

const lithonHover = hoverTooltip((view: EditorView, pos: number) => {
  const word = view.state.doc.lineAt(pos)
  const text = word.text
  const offset = pos - word.from
  let start = offset
  let end = offset
  while (start > 0 && /[\w]/.test(text[start - 1])) start--
  while (end < text.length && /[\w]/.test(text[end])) end++
  if (start === end) return null
  const token = text.slice(start, end)
  const info = lithonWordInfo(token)
  if (!info) return null

  const before = text.slice(0, start).trimEnd()
  // also explain an annotation, e.g. `x: int[64]`
  const annotated = /:\s*$/.test(before)

  return {
    pos: word.from + start,
    end: word.from + end,
    above: true,
    create: () => {
      const dom = document.createElement("div")
      dom.className = "px-3 py-2 text-xs"
      const kind = document.createElement("div")
      kind.className =
        "mb-1 text-[10px] font-semibold tracking-wider text-muted-foreground uppercase"
      kind.textContent = annotated ? `${info.kind} · annotation` : info.kind
      const body = document.createElement("div")
      body.className = "max-w-xs leading-relaxed"
      body.textContent = annotated
        ? `${token}: ${info.doc}`
        : `${token} — ${info.doc}`
      dom.append(kind, body)
      return { dom }
    },
  }
})

const THEME = EditorView.theme({
  "&": { fontSize: "13px" },
  ".cm-scroller": {
    fontFamily:
      "var(--font-mono, 'DM Mono'), ui-monospace, SFMono-Regular, Menlo, monospace",
    lineHeight: "1.65",
  },
  ".cm-content": { padding: "12px 0" },
  ".cm-gutters": {
    backgroundColor: "transparent",
    border: "none",
    color: "var(--muted-foreground)",
  },
  ".cm-activeLine": { backgroundColor: "rgba(127,127,127,0.06)" },
  ".cm-activeLineGutter": {
    backgroundColor: "rgba(127,127,127,0.06)",
    color: "var(--foreground)",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-selectionBackground, ::selection": {
    backgroundColor: "rgba(168, 80, 15, 0.22)",
  },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--foreground)" },
  ".cm-tooltip": {
    backgroundColor: "var(--popover)",
    border: "1px solid var(--border)",
    borderRadius: "8px",
    boxShadow: "0 8px 24px rgba(0,0,0,0.28)",
    overflow: "hidden",
  },
  ".cm-tooltip.cm-tooltip-autocomplete > ul": {
    fontFamily:
      "var(--font-mono, 'DM Mono'), ui-monospace, SFMono-Regular, Menlo, monospace",
    fontSize: "12px",
    maxHeight: "16rem",
  },
  ".cm-tooltip.cm-tooltip-autocomplete > ul > li": {
    padding: "4px 10px",
    display: "flex",
    alignItems: "center",
    gap: "8px",
  },
  ".cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected]": {
    backgroundColor: "var(--accent)",
    color: "var(--accent-foreground)",
  },
  ".cm-completionIcon": {
    width: "1.1em",
    paddingRight: "6px",
    opacity: 0.7,
  },
  ".cm-completionDetail": {
    marginInlineStart: "auto",
    paddingInlineStart: "14px",
    fontStyle: "italic",
    color: "var(--muted-foreground)",
    fontSize: "11px",
  },
  ".cm-tooltip-lint": { maxWidth: "380px" },
  ".cm-diagnostic": { padding: "6px 10px", fontSize: "12px" },
  ".cm-diagnostic-error": { borderInlineStart: "3px solid var(--destructive)" },
  ".cm-diagnostic-warning": { borderInlineStart: "3px solid var(--amber-500)" },
})

const KEYWORDS = [
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
]

const TYPES = [
  ["int[8]", "signed 8-bit"],
  ["int[16]", "signed 16-bit"],
  ["int[32]", "signed 32-bit"],
  ["int[64]", "signed 64-bit"],
  ["float[64]", "IEEE-754 binary64"],
  ["bool", "one-byte boolean"],
  ["ptr[T]", "typed pointer, requires `_` name"],
  ["list[T, N]", "fixed-capacity list"],
  ["tuple[T, N]", "fixed-length tuple"],
  ["dict[K, V, B]", "constant-keyed table"],
]

const BUILTIN_DOCS: Record<string, string> = {
  print: "print(value) → write one scalar and a newline",
  len: "len(container) → capacity, part of the type",
  range: "range(n) / range(a, b) → loop values, width-checked",
  addressof: "addressof(x) → ptr[T] to scalar x",
  valueof: "valueof(_p) → read the scalar at _p",
  contains: "contains(d, k) → bool, key present in dict d",
  wrap_add: "wrap_add(a, b) → wrapping integer add",
  wrap_sub: "wrap_sub(a, b) → wrapping integer sub",
  wrap_mul: "wrap_mul(a, b) → wrapping integer mul",
}

function lithonCompletions(context: CompletionContext) {
  const word = context.matchBefore(/[\w_]*/)
  if (!word) return null
  if (word.from === word.to && !context.explicit) return null

  const options = [
    ...KEYWORDS.map((k) => ({ label: k, type: "keyword" })),
    ...TYPES.map(([label, detail]) => ({ label, type: "type", detail })),
    ...Object.entries(BUILTIN_DOCS).map(([label, detail]) => ({
      label,
      type: "function",
      detail,
      info: detail,
    })),
  ]

  const from = word.from
  return { from, options, validFor: /^[\w_[\]]*$/ }
}

export type LithonEditorHandle = {
  view: EditorView | null
}

export function LithonEditor({
  value,
  onChange,
  diagnostics = [],
  placeholder: placeholderText = "Write Lithon here…",
  onRun,
  minHeight = "100%",
}: {
  value: string
  onChange: (value: string) => void
  diagnostics?: Diagnostic[]
  placeholder?: string
  onRun?: () => void
  minHeight?: string
}) {
  const host = useRef<HTMLDivElement | null>(null)
  const viewRef = useRef<EditorView | null>(null)
  const onChangeRef = useRef(onChange)
  const onRunRef = useRef(onRun)
  onChangeRef.current = onChange
  onRunRef.current = onRun

  useEffect(() => {
    if (!host.current) return

    const runKeymap = keymap.of([
      {
        key: "Mod-Enter",
        preventDefault: true,
        run: () => {
          onRunRef.current?.()
          return true
        },
      },
    ])

    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers(),
        highlightActiveLineGutter(),
        highlightSpecialChars(),
        history(),
        foldGutter({
          markerDOM: (open) => {
            const el = document.createElement("span")
            el.textContent = open ? "⌄" : "›"
            el.style.cursor = "pointer"
            el.style.paddingInlineEnd = "6px"
            return el
          },
        }),
        drawSelection(),
        indentOnInput(),
        syntaxHighlighting(lithonHighlightStyle),
        bracketMatching(),
        closeBrackets(),
        rectangularSelection(),
        highlightActiveLine(),
        highlightSelectionMatches(),
        lintGutter(),
        EditorState.allowMultipleSelections.of(true),
        placeholder(placeholderText),
        lithonLanguage,
        autocompletion({
          override: [lithonCompletions],
          activateOnTyping: true,
          closeOnBlur: true,
          icons: true,
          maxRenderedOptions: 40,
        }),
        lithonHover,
        EditorView.lineWrapping,
        EditorView.theme({ "&": { minHeight } }),
        runKeymap,
        keymap.of([
          ...closeBracketsKeymap,
          ...defaultKeymap,
          ...searchKeymap,
          ...historyKeymap,
          ...foldKeymap,
          ...completionKeymap,
          ...lintKeymap,
          indentWithTab,
        ]),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            onChangeRef.current(update.state.doc.toString())
          }
        }),
        THEME,
      ],
    })

    const view = new EditorView({ state, parent: host.current })
    viewRef.current = view
    return () => {
      view.destroy()
      viewRef.current = null
    }
  }, [])

  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    const current = view.state.doc.toString()
    if (current === value) return
    view.dispatch({
      changes: { from: 0, to: current.length, insert: value },
    })
  }, [value])

  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    view.dispatch(setDiagnostics(view.state, diagnostics))
  }, [diagnostics])

  return (
    <div
      ref={host}
      className="h-full overflow-hidden [&_.cm-editor]:h-full"
      dir="ltr"
    />
  )
}
