import type { DocPage } from "../model"

/**
 * Where Lithon deliberately differs from CPython.
 *
 * Every row here is a difference you can trip over. The rule behind all of
 * them: Lithon is statically typed and fixed-width, so anything CPython gets
 * from dynamic dispatch or arbitrary precision has to be spelled explicitly.
 */
const divergences: DocPage = {
  slug: "python-divergences",
  title: "Divergences from Python",
  description:
    "Every place Lithon deliberately behaves differently from CPython, and why: modulo, division, shifts, narrowing, truthiness, strings, exceptions.",
  group: "language",
  tags: [
    "divergences",
    "python",
    "cpython",
    "differences",
    "semantics",
    "gotchas",
    "porting",
    "migration",
  ],
  priority: "recommended",
  outcome: "Port Python code to Lithon without being surprised by a silent semantic change.",
  minutes: 12,
  sections: [
    {
      id: "the-short-list",
      title: "The short list",
      blocks: [
        {
          kind: "p",
          text: "Lithon's source syntax is Python's. Its *semantics* are not, in eleven specific places, all of which come from the same two decisions: **fixed-width values** and **no dynamic dispatch**. This page is the list; each row links to the page that explains it in full.",
        },
        {
          kind: "comparison",
          caption: "the eleven differences that will bite you",
          columns: ["Behaviour", "CPython", "Lithon"],
          rows: [
            [
              "Modulo with a negative dividend",
              "`-7 % 3 == 2`",
              "`-7 % 3 == -1` — C-style, the sign follows the dividend",
            ],
            [
              "Integer division",
              "`7 / 2 == 3.5`",
              "`7 / 2 == 3.5` — same, but `//` is refused outright",
            ],
            [
              "Narrowing an integer",
              "always allowed",
              "refused: `int[8]` never becomes `int[64]` implicitly",
            ],
            [
              "Float to int",
              "`int(3.7) == 3`",
              "`int(64, 3.7) == 3` — the width has to be named",
            ],
            [
              "Truthiness",
              "`if 0:` is False, `if \"\":` is False",
              "refused: a condition must be a `bool`",
            ],
            [
              "Strings",
              "unbounded, heap-allocated, `+` concatenates",
              "bounded `str[N]` byte runs; `+` is refused",
            ],
            [
              "Exceptions",
              "raise anything, catch by class",
              "seven named engine traps; no `raise`, no exception objects",
            ],
            [
              "Unassigned variables",
              "`NameError` at run time",
              "refused at compile time: every name needs a type and a value",
            ],
            [
              "Recursion",
              "bounded by the C stack",
              "self tail calls become loops; a stack guard traps at 4096",
            ],
            [
              "Function arguments",
              "positional, keyword, `*args`, `**kwargs`, defaults",
              "positional only, fixed arity, no defaults, no keywords",
            ],
            [
              "Comparisons",
              "`a <= b`, chained `a < b < c`",
              "only `<`, `>`, `==`, `!=`; no chaining",
            ],
          ],
        },
        {
          kind: "callout",
          title: "The rule behind all eleven",
          text: "CPython can decide what an expression means at run time. Lithon cannot, so anything the compiler would have to guess about becomes a refusal. A refusal is a compile-time error with a precise message, not a wrong answer at run time.",
        },
      ],
    },
    {
      id: "modulo",
      title: "Modulo follows C, not Python",
      blocks: [
        {
          kind: "p",
          text: "Lithon's `%` is the C remainder: the sign of the result follows the **dividend**, not the divisor. Python's is a true modulo: the sign follows the **divisor**.",
        },
        {
          kind: "tabs",
          caption: "-7 % 3",
          items: [
            {
              label: "Lithon",
              blocks: [
                {
                  kind: "code",
                  example: {
                    typecheck: false,
                    source: `print(-7 % 3)`,
                    output: "-1",
                  },
                },
              ],
            },
            {
              label: "CPython",
              blocks: [
                {
                  kind: "code",
                  example: {
                    lang: "text",
                    source: `>>> -7 % 3
2`,
                    output: "",
                  },
                },
              ],
            },
          ],
        },
        {
          kind: "p",
          text: "The same holds for `7 % -3`: Lithon gives `1`, CPython gives `-2`. This is not an oversight: the C-style form is the one a machine instruction actually implements, and Lithon emits exactly that instruction.",
        },
        {
          kind: "note",
          tone: "note",
          title: "Where to read more",
          text: "The full statement of the rule, with the machine-level reason, is on the [semantics](/docs/semantics) page.",
        },
      ],
    },
    {
      id: "division",
      title: "Division is always float; `//` does not exist",
      blocks: [
        {
          kind: "p",
          text: "`/` always produces a `float[64]`, including when both operands are integers: `7 / 2` is `3.5`, not `3`. There is no integer division operator at all — `//` is refused with *operator FloorDiv not supported yet*.",
        },
        {
          kind: "p",
          text: "To get an integer quotient, write it yourself or use a shift when the divisor is a power of two: `x // 2` becomes `x >> 1` for non-negative `x`.",
        },
        {
          kind: "callout",
          title: "Division by zero traps in both tiers",
          text: "`1 / 0` is not an IEEE infinity and not a NaN: it is a `ZeroDivisionError` trap, exactly like Python's, and it is catchable with `try`/`except`.",
        },
      ],
    },
    {
      id: "narrowing",
      title: "Narrowing is never implicit",
      blocks: [
        {
          kind: "p",
          text: "CPython integers are arbitrary-precision, so every integer is every other integer. Lithon integers are fixed-width, so an `int[8]` value is not an `int[64]` value: widening happens automatically, narrowing is refused.",
        },
        {
          kind: "code",
          example: {
            title: "widening is fine",
            typecheck: false,
            source: `small: int[8] = 5
big: int[64] = small
print(big)`,
            output: "5",
          },
        },
        {
          kind: "code",
          example: {
            title: "narrowing is refused",
            expect: "error",
            tool: "typecheck",
            errorMatch: "cannot narrow",
            source: `wide: int[64] = 5
narrow: int[8] = wide
print(narrow)`,
            output:
              "RCR error: declaration of 'narrow': cannot narrow int[64] into int[8] -- narrowing is never allowed (V1_SPEC 0.6.11)",
          },
        },
        {
          kind: "p",
          text: "There is no cast syntax for this. If you genuinely want to truncate, you have to say so with the explicit conversion, and the conversion **traps** rather than truncating when the value does not fit.",
        },
      ],
    },
    {
      id: "conversions",
      title: "Conversions name their width",
      blocks: [
        {
          kind: "comparison",
          caption: "conversions",
          columns: ["Operation", "CPython", "Lithon"],
          rows: [
            ["int from float", "`int(3.7)`", "`int(64, 3.7)`"],
            ["float from int", "`float(3)`", "`float(64, 3)`"],
            [
              "out of range",
              "`int(1e30)` is a big int",
              "`int(8, 300.0)` **traps** with `ValueError`",
            ],
            [
              "float from int[64]",
              "n/a",
              "refused: `int[64]` has no exact `float[64]` (E0106)",
            ],
          ],
        },
        {
          kind: "p",
          text: "The width is not decoration. `int(8, v)` promises the value fits in a signed byte, and the engine checks that promise at run time instead of silently wrapping.",
        },
      ],
    },
    {
      id: "truthiness",
      title: "There is no truthiness",
      blocks: [
        {
          kind: "p",
          text: "In CPython, `if x:` asks whether `x` is truthy, and almost everything has a truth value. In Lithon, a condition must be a `bool`. `if x:` where `x: int[64]` is a type error, not a check for non-zero.",
        },
        {
          kind: "code",
          example: {
            title: "the Lithon way",
            typecheck: false,
            source: `n: int[64] = 0
if n != 0:
    print("not zero")`,
            output: "",
          },
        },
        {
          kind: "note",
          tone: "note",
          title: "Why",
          text: "A truthiness test on an int is a comparison the compiler could have proved away, but on a container it depends on the container's length, which is a run-time value. One rule — *conditions are `bool` values* — is easier to verify and faster to compile than a truthiness table.",
        },
      ],
    },
    {
      id: "strings",
      title: "Strings are bounded byte runs",
      blocks: [
        {
          kind: "comparison",
          caption: "strings",
          columns: ["Behaviour", "CPython", "Lithon"],
          rows: [
            ["Type", "`str`, unbounded", "`str[N]`, N bytes"],
            ["Length", "`len(s)` in characters", "`len(s)` in bytes, at run time"],
            ["Concatenation", "`a + b`", "refused"],
            ["Slicing", "`s[1:3]`", "refused"],
            ["Comparison", "by content", "by content, across different capacities"],
            ["Methods", "dozens", "three: `find`, `count`, `split`"],
            ["In a `list`", "yes", "yes, as a `str[N]` element"],
            ["As a function argument", "yes", "refused: a string is addressed, not passed"],
          ],
        },
        {
          kind: "note",
          tone: "note",
          title: "Where to read more",
          text: "The whole string model is on the [strings](/docs/strings) page.",
        },
      ],
    },
    {
      id: "exceptions",
      title: "Exceptions are a closed set of engine traps",
      blocks: [
        {
          kind: "p",
          text: "CPython lets you `raise` anything and catch by class. Lithon has no exception objects and no `raise`: the only thing you can catch is one of **seven named traps** that the engine itself raises.",
        },
        {
          kind: "deflist",
          caption: "the catchable set",
          items: [
            {
              term: "ZeroDivisionError",
              detail: "division or modulo by zero.",
            },
            {
              term: "OverflowError",
              detail:
                "a dynamic `int[64]` overflow, or an input value that does not fit the width it was read into.",
            },
            {
              term: "ValueError",
              detail:
                "a float-to-int conversion out of range, a shift count outside 0..63, a zero `range` step, or non-numeric input.",
            },
            { term: "IndexError", detail: "a dynamic index outside a list's bounds." },
            { term: "KeyError", detail: "a dict key that is not present." },
            { term: "EOFError", detail: "`input()` hit the end of the input." },
            {
              term: "Exception",
              detail: "the catch-all: matches every one of the six above.",
            },
          ],
        },
        {
          kind: "p",
          text: "A bare `except:` is refused, `except X as e` is refused (there is no exception object to bind), and `raise` is refused. A trap raised inside a **called function** is not catchable by the caller: the trap unwinds past it.",
        },
        {
          kind: "note",
          tone: "note",
          title: "Where to read more",
          text: "The full `try`/`except`/`else`/`finally` story is on the [control flow](/docs/control-flow) page.",
        },
      ],
    },
    {
      id: "unassigned",
      title: "Unassigned names are a compile error",
      blocks: [
        {
          kind: "p",
          text: "CPython raises `NameError` when you read a name you never assigned. Lithon refuses the program before it runs, and the refusal is per-name and per-path.",
        },
        {
          kind: "code",
          example: {
            title: "rejected · V1_SPEC 0.6.10",
            expect: "error",
            tool: "typecheck",
            errorMatch: "never assigned a value",
            source: `x: int[64]
print(x)`,
            output:
              "RCR error: call statement, print argument 1: 'x' was declared with a type but never assigned a value (V1_SPEC 0.6.10)",
          },
        },
        {
          kind: "p",
          text: "The same rule covers branches: if one arm of an `if` assigns a name and the other does not, the program is refused, because the verifier cannot prove which arm runs.",
        },
      ],
    },
    {
      id: "functions",
      title: "Functions have a fixed shape",
      blocks: [
        {
          kind: "comparison",
          caption: "function signatures",
          columns: ["Feature", "CPython", "Lithon"],
          rows: [
            ["Parameter types", "optional", "mandatory"],
            ["Return type", "optional", "mandatory"],
            ["Default values", "yes", "no"],
            ["Keyword arguments", "yes", "no"],
            ["`*args` / `**kwargs`", "yes", "no"],
            ["Default arguments", "yes", "no"],
            ["Closures", "yes", "no: a function body cannot capture a caller's variable"],
            ["First-class functions", "yes", "no: a function name is not a value"],
            ["Recursion", "yes", "yes, with a stack guard at 4096 frames"],
            ["Overloading", "yes", "no"],
          ],
        },
        {
          kind: "p",
          text: "Every one of these is a place where CPython would have to build a closure, a tuple, or a dictionary at run time. Lithon has no such machinery, so the shape of a function is fixed at compile time and the machine code is a direct call.",
        },
      ],
    },
    {
      id: "comparisons",
      title: "Comparisons are four operators, never chained",
      blocks: [
        {
          kind: "p",
          text: "Lithon has `<`, `>`, `==` and `!=`. `<=` and `>=` are refused (*comparison LtE not supported yet*), and chained comparisons (`a < b < c`) are refused, because a chain means two comparisons and an intermediate value that a machine register cannot hold.",
        },
        {
          kind: "code",
          example: {
            title: "the Lithon way",
            typecheck: false,
            source: `a: int[64] = 1
b: int[64] = 2
if a < b and b < 10:
    print("ok")`,
            output: "ok",
          },
        },
        {
          kind: "p",
          text: "`!=` is a first-class operator with its own machine instruction, not `==` followed by `not`. On floats it follows IEEE 754: `nan != nan` is true.",
        },
      ],
    },
    {
      id: "what-is-the-same",
      title: "What is exactly the same",
      blocks: [
        {
          kind: "p",
          text: "It is worth being precise about what did **not** change, because the list above can read as if Lithon is a different language. These are identical:",
        },
        {
          kind: "list",
          items: [
            "`while` and `for` loops, `break`, `continue` — same shape, same semantics.",
            "`if` / `elif` / `else` — same, and `elif` is spelled `elif`, not `else if`.",
            "The conditional expression `a if cond else b` — same.",
            "Integer literals, float literals, `True` / `False` — same spellings.",
            "Operator precedence — Python's, exactly, because the frontend uses Python's own parser.",
            "`and` / `or` / `not` — same precedence, but the operands must be `bool`.",
            "Function calls, subscripting, attribute access — same syntax.",
            "Recursion — supported, and the same shape as Python's.",
            "`range(start, stop, step)` — the same three forms and the same values.",
          ],
        },
        {
          kind: "quote",
          text: "The source model is Python's. The type system is not, and where the type system changes the meaning, the meaning changes.",
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
              title: "Types & widths",
              href: "/docs/types",
              text: "The type system behind most of these differences.",
            },
            {
              title: "Semantics",
              href: "/docs/semantics",
              text: "Modulo, shifts, float traps, and no truthiness, in full.",
            },
            {
              title: "Errors & diagnostics",
              href: "/docs/errors",
              text: "Every error code, and how a refusal reads.",
            },
            {
              title: "Honest limits",
              href: "/docs/limits",
              text: "The gaps that are not yet implemented at all.",
            },
          ],
        },
      ],
    },
  ],
}

export const DIVERGENCE_PAGES: DocPage[] = [divergences]
