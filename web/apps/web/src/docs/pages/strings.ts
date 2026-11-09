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
            source: `class Header(struct):
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
      id: "reading-a-line",
      title: "Reading a line (`str(n, input(...))`)",
      blocks: [
        {
          kind: "p",
          text: "A line from standard input is read straight into a declared string: `k: str[N] = str(n, input(\"Enter something: \"))`. The prompt is a string literal and `n` is a **literal limit that counts the NUL**, exactly as `str[N]` does, so at most `n-1` bytes are read and `1 <= n <= N`. It can also re-read into an existing string (`k = str(n, input(\"...\"))`) or into a struct's `str` field; each read rewrites the whole run, so a shorter line leaves no old tail. The newline is removed, an empty line is an empty string, and spaces are kept.",
        },
        {
          kind: "note",
          tone: "warn",
          title: "A line that does not fit traps; it is never truncated",
          text: "A line longer than `n-1` bytes stops the program with `LITHON-E0311` and a line containing a NUL byte with `LITHON-E0312` (both a `ValueError` to `try`/`except`; a string cannot hold a NUL, so it is refused rather than silently shortened), and end of input is `LITHON-E0308` (an `EOFError`), identically in both tiers. A limit larger than the capacity, or one that is not a literal, is refused at compile time (`E0116`), and `str(...)` anywhere else is refused (`E0115`): it reads a line *into* a string, so it has no value of its own.",
        },
      ],
    },
    {
      id: "methods",
      title: "The three string operations (`find`/`count`/`split`)",
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
          text: "`print(s)` writes the bytes of the run. There are no quotes around it, because a string is a byte run and not a Python object with a `repr` — a baseless class may define `__str__`/`__repr__` returning one literal/f-string of scalar attrs, inlined by `print()`/f-string (see [Classes](/docs/classes)).",
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
      id: "returning-strings",
      title: "Returning a string from a function",
      blocks: [
        {
          kind: "p",
          text: "A function may be declared `-> str[N]`. A string is frame storage with no value form, so such a function has no machine code of its own: **its body is expanded at each call site** into a fresh `str[N]` result buffer, with its parameters and locals renamed, and `return` writes into that buffer. The call then stands for the buffer wherever a string is accepted. Because it is a frontend rewrite, it needs no new IR operation and works unchanged on every tier and target.",
        },
        {
          kind: "table",
          caption: "where a str-returning call is accepted",
          rows: [
            ["`print(f(x))`, an f-string field", "the buffer prints"],
            ["`f(x) == \"lit\"`, `f(x) != t`", "a run comparison; `s == \"literal\"` now works for any string"],
            ["`len(f(x))`, `f(x)[i]`, `f(x).find(..)`, `.count(..)`, `.split(..)`", "the buffer is the receiver"],
            ["`s: str[M] = f(x)`", "copied in; `M` must be at least the function's `N`"],
            ["`s = f(x)`", "copied over the whole string and the old tail cleared, so a shorter result replaces a longer one"],
            ["`return <literal>`, `return s`, `return g(y)`", "each is checked against the declared capacity"],
          ],
        },
        {
          kind: "table",
          caption: "refused",
          rows: [
            ["Recursion", "refused: the function is inlined at its call sites and cannot call itself, directly or indirectly (E0114)"],
            ["A parameter that is not an annotated `int`, `uint`, `float`, `bool` or `str`", "refused (E0124): such a function cannot take a container, struct, class or pointer"],
            ["A result that may not fit", "a literal longer than `N-1` bytes, a wider str variable, or a receiving `str[M]` smaller than `N` is refused (E0111): a str is never narrowed"],
            ["Using the result as a number", "`x: int[64] = f()` is refused (E0114) with the list of places a string is accepted"],
          ],
        },
        {
          kind: "p",
          text: "A `uint[w]` return was already supported for ordinary functions; it needs nothing special here.",
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
              "a function can take a `str[N]` parameter and return a `str[N]` (or an int/float/bool): the function is expanded at each call site and the parameter is a *copy*. It cannot be recursive.",
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
