import type { DocPage } from "../model"

/**
 * Bounded strings: `str[N]`.
 *
 * A Lithon string is a fixed-capacity, NUL-terminated byte run. `N` is the
 * number of bytes the declaration reserves, and the literal must fit.
 */
const strings: DocPage = {
  slug: "strings",
  title: "Strings",
  description:
    "Bounded strings: str[N] is a fixed-capacity, NUL-terminated byte run. Length is a run-time value, capacity is part of the type.",
  group: "language",
  tags: [
    "strings",
    "str",
    "text",
    "find",
    "count",
    "split",
    "bounded",
    "nul-terminated",
    "bytes",
  ],
  priority: "recommended",
  outcome: "Declare, read, compare and slice bounded strings without a heap.",
  minutes: 8,
  sections: [
    {
      id: "what-a-string-is",
      title: "What a Lithon string is",
      blocks: [
        {
          kind: "p",
          text: "A Lithon string is not a Python `str`. It is a **bounded byte run**: `str[N]` declares `N` bytes of storage, the bytes are written one at a time, and the run is NUL-terminated. There is no heap, no allocator, and no growth. Capacity is part of the type, exactly like a `list`'s.",
        },
        {
          kind: "code",
          example: {
            title: "a bounded string",
            typecheck: false,
            source: `s: str[16] = "hello"
print(s)
print(len(s))`,
            output: "hello\n5",
          },
        },
        {
          kind: "callout",
          title: "N is bytes, and the capacity is not the length",
          text: "`str[16]` reserves 16 **bytes**, not 16 characters: a UTF-8 character can be more than one byte. And `len(s)` is the **run-time** byte count of what was written, not the 16 you declared. A `str[N]` you declared but never filled has length 0.",
        },
        {
          kind: "p",
          text: "The frontend lowers a string literal to one `IndexStore` per byte, so the storage model stays the same as every other container: a run of bytes at a fixed address.",
        },
      ],
    },
    {
      id: "declaring",
      title: "Declaring and writing",
      blocks: [
        {
          kind: "table",
          caption: "string rules",
          rows: [
            [
              "Capacity",
              "`str[N]` — `N` bytes of storage. The literal must be shorter than `N` (the terminator needs a byte).",
            ],
            [
              "Length",
              "`len(s)` is the run-time byte count of the bytes actually written.",
            ],
            [
              "Unwritten bytes",
              "read back as `0`; the terminator makes the run a C string as far as the OS is concerned.",
            ],
            [
              "Indexing",
              "`s[i]` reads one byte as an `int[8]`. The byte is signed.",
            ],
            [
              "Writing",
              "`s[i] = v` writes one byte. The right-hand side must be a single `int[8]`-sized value.",
            ],
            [
              "Comparison",
              "`s == t` compares the byte runs. The two runs may have different capacities.",
            ],
            [
              "Element type",
              "a `str[N]` may be a `list` element, a `tuple` element, a `struct` field, or a pointer pointee.",
            ],
          ],
        },
        {
          kind: "p",
          text: "Because a string is just a byte run, it can live inside a struct, inside a list, or behind a pointer, and the same rules apply to it as to any other fixed-capacity buffer.",
        },
        {
          kind: "code",
          example: {
            title: "a string as a struct field",
            typecheck: false,
            source: `class Header(Struct):
    kind: str[8]
    version: int[16]

h: Header
h.kind = "HTTP"
h.version = 11
print(h.kind)
print(h.version)`,
            output: "HTTP\n11",
          },
        },
      ],
    },
    {
      id: "methods",
      title: "The three string methods",
      blocks: [
        {
          kind: "p",
          text: "There are exactly three things you can do with a `str[N]` beyond reading and writing it. They are the whole string API.",
        },
        {
          kind: "deflist",
          caption: "the string surface",
          items: [
            {
              term: "s.find(sub)",
              detail:
                "The lowest byte index where `sub` occurs in `s`, or `-1` when it does not occur. An empty needle returns `0`.",
            },
            {
              term: "s.count(sub)",
              detail:
                "How many non-overlapping occurrences of `sub` are in `s`. An empty needle returns `len(s) + 1`.",
            },
            {
              term: "parts: list[str[K], M] = s.split(sep)",
              detail:
                "Splits `s` on every occurrence of `sep` and writes the pieces into a **declared** `list[str[K], M]`. A piece longer than `K - 1` bytes is truncated; more than `M` pieces is a runtime trap. The piece count becomes the list's live length.",
            },
          ],
        },
        {
          kind: "code",
          example: {
            title: "find and count",
            typecheck: false,
            source: `s: str[32] = "hello world"
print(s.find("world"))
print(s.find("zzz"))
print(s.count("l"))`,
            output: "6\n-1\n2",
          },
        },
        {
          kind: "code",
          example: {
            title: "split writes into a declared list",
            typecheck: false,
            source: `s: str[32] = "a,b,c"
parts: list[str[8], 4] = s.split(",")
print(parts[0])
print(parts[2])`,
            output: "a\nc",
          },
        },
        {
          kind: "note",
          tone: "warn",
          title: "split is an assignment, not a statement",
          text: "`s.split(\",\")` on its own line is refused: it has to be the **initializer** of a declared `list[str[K], M]`, because the split writes into the destination rather than returning a value. `s.split()` with no argument, and `s.split(\"\")` with an empty separator, are refused too.",
        },
      ],
    },
    {
      id: "printing",
      title: "Printing a string",
      blocks: [
        {
          kind: "p",
          text: "`print(s)` writes the bytes of the run. There are no quotes around it, because a string is a byte run and not a Python object with a `repr`.",
        },
        {
          kind: "tabs",
          caption: "print a string vs print a list of strings",
          items: [
            {
              label: "a bare string",
              blocks: [
                {
                  kind: "code",
                  example: {
                    typecheck: false,
                    source: `s: str[8] = "hi"
print(s)`,
                    output: "hi",
                  },
                },
              ],
            },
            {
              label: "a list of strings",
              blocks: [
                {
                  kind: "code",
                  example: {
                    typecheck: false,
                    source: `xs: list[str[8], 2] = ["hi", "yo"]
print(xs)`,
                    output: "['hi', 'yo']",
                  },
                },
              ],
            },          ],
        },
        {
          kind: "p",
          text: "A list of strings prints in the container `repr` form, with quotes, because a list is a composite value and the reader needs to see the element boundaries. A bare string does not.",
        },
      ],
    },
    {
      id: "strings-and-pointers",
      title: "Strings and pointers",
      blocks: [
        {
          kind: "p",
          text: "A `str[N]` can be the pointee of a pointer, which is how you hand a buffer to a syscall that reads or writes it.",
        },
        {
          kind: "code",
          example: {
            title: "a string behind a pointer",
            typecheck: false,
            source: `s: str[16] = "ping"
_p: ptr[str[16]] = addressof(s)
print(valueof(_p))`,
            output: "ping",
          },
        },
        {
          kind: "note",
          tone: "note",
          title: "One restriction worth knowing",
          text: "A `str` field reached **through** a pointer is refused: `valueof(_p).kind` where `kind` is a string field is rejected with *a bounded string field is reached through its struct variable, not through a pointer into the struct*. The string can be the pointee of the pointer directly, but not a string-typed field of a struct the pointer points at.",
        },
      ],
    },
    {
      id: "what-strings-cannot-do",
      title: "What strings cannot do",
      blocks: [
        {
          kind: "table",
          caption: "refusals",
          rows: [
            [
              "Concatenation",
              "`c: str[16] = a + b` is refused: *a str is built from a string literal, so `c: ... = <expr>` needs `\"...\"`, not a single expression*. There is no `+` on strings.",
            ],
            [
              "Slicing",
              "`s[1:3]` is refused: only a single byte index is supported.",
            ],
            [
              "A bare str literal in a container literal",
              "`xs: list[str[8], 2] = [\"a\", \"b\"]` works in the engine, but the standalone checker answers `unknown type 'list'`: it does not know the container types yet.",
            ],
            [
              "Whitespace splitting",
              "`s.split()` is refused: the separator is mandatory.",
            ],
            [
              "An empty separator",
              "`s.split(\"\")` is refused, exactly as Python refuses it.",
            ],
            [
              "A `str` function parameter",
              "a string cannot be passed to a function: *a str function parameter is not yet a string place*. Strings are addressed, not passed.",
            ],
          ],
        },
      ],
    },
    {
      id: "where-to-go-next",
      title: "Where to go next",
      blocks: [
        {
          kind: "linkcards",
          items: [
            {
              title: "Containers",
              href: "/docs/containers",
              text: "The other fixed-capacity runs: `list`, `tuple`, `dict`.",
            },
            {
              title: "Pointers",
              href: "/docs/pointers",
              text: "Raw addresses, `addressof`/`valueof`, and the `_` naming rule.",
            },
            {
              title: "Structs",
              href: "/docs/structs",
              text: "Packed storage, and how a `str[N]` field is laid out.",
            },
            {
              title: "Networking",
              href: "/docs/networking",
              text: "Using strings and pointers together for real socket calls.",
            },
          ],
        },
      ],
    },
  ],
}

export const STRINGS_PAGES: DocPage[] = [strings]
