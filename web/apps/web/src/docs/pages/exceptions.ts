import type { DocPage } from "../model"

/**
 * `try` / `except` / `else` / `finally` over the engine's own traps.
 *
 * The catchable set is closed: seven names. This page explains what each
 * one means, when it fires, and what Lithon deliberately does not support.
 */
const exceptions: DocPage = {
  slug: "exceptions",
  title: "Exceptions & traps",
  description:
    "try/except/else/finally over a closed set of seven engine traps. What each one means, when it fires, and what is deliberately not supported.",
  group: "language",
  tags: [
    "exceptions",
    "traps",
    "try",
    "except",
    "else",
    "finally",
    "ZeroDivisionError",
    "OverflowError",
    "ValueError",
    "IndexError",
    "KeyError",
    "EOFError",
    "error handling",
  ],
  priority: "recommended",
  outcome: "Recover from the seven engine traps with try/except/else/finally.",
  minutes: 10,
  sections: [
    {
      id: "the-model",
      title: "The model: traps, not exception objects",
      blocks: [
        {
          kind: "p",
          text: "Lithon has no exception classes, no `raise`, and no exception objects. What it has is **traps**: the engine stops the program with a short message when an operation cannot produce a value. A `try` block names the traps it is prepared for, and the engine routes a trap to the matching arm.",
        },
        {
          kind: "code",
          example: {
            title: "catching a division by zero",
            typecheck: false,
            source: `n: int[64] = 0
try:
    q: int[64] = 100 % n
    print("not reached")
except ZeroDivisionError:
    print("caught ZeroDivisionError")`,
            output: "caught ZeroDivisionError",
          },
        },
        {
          kind: "callout",
          title: "The catchable set is closed",
          text: "Exactly seven names are catchable: `ZeroDivisionError`, `OverflowError`, `ValueError`, `IndexError`, `KeyError`, `EOFError`, and `Exception` (the catch-all). Any other name in an `except` arm is a compile-time refusal. You cannot invent a trap, and you cannot catch a segfault.",
        },
      ],
    },
    {
      id: "the-seven",
      title: "The seven catchable traps",
      blocks: [
        {
          kind: "deflist",
          caption: "the closed catchable set",
          items: [
            {
              term: "ZeroDivisionError",
              detail:
                "division or modulo by zero. Both `/` and `%` trap. A float divisor of `0.0` also traps — Lithon does not produce an IEEE infinity.",
            },
            {
              term: "OverflowError",
              detail:
                "a dynamic `int[64]` overflow, and an input value that does not fit the width it was read into (`int(8, input(...))` with a 300).",
            },
            {
              term: "ValueError",
              detail:
                "a float-to-int conversion whose result is out of range or not finite, a shift count outside 0..63, a `range` step of zero at run time, and non-numeric input.",
            },
            {
              term: "IndexError",
              detail: "a dynamic index outside a list's bounds.",
            },
            {
              term: "KeyError",
              detail: "a dict key that is not present.",
            },
            {
              term: "EOFError",
              detail: "`input()` reached the end of the input before a value was read.",
            },
            {
              term: "Exception",
              detail:
                "the catch-all: it matches every one of the six above, and nothing else. It is the only way to write a handler that is guaranteed to cover the set.",
            },
          ],
        },
        {
          kind: "code",
          example: {
            title: "an index trap",
            typecheck: false,
            source: `xs: list[int[64], 3] = [10, 20, 30]
k: int[64] = 9
try:
    v: int[64] = xs[k]
    print("not reached")
except IndexError:
    print("caught IndexError")`,
            output: "caught IndexError",
          },
        },
        {
          kind: "code",
          example: {
            title: "several arms, tried in order",
            typecheck: false,
            source: `try:
    y: int[64] = 5 % 0
except IndexError:
    print("wrong arm")
except ZeroDivisionError:
    print("caught ZeroDivisionError")`,
            output: "caught ZeroDivisionError",
          },
        },
      ],
    },
    {
      id: "else-and-finally",
      title: "`else` and `finally`",
      blocks: [
        {
          kind: "p",
          text: "Both clauses are supported, and they have the same meaning they have in Python: `else` runs when the `try` block completed **without** a trap, and `finally` runs on every path out of the block.",
        },
        {
          kind: "code",
          example: {
            title: "all three clauses",
            typecheck: false,
            source: `n: int[64] = 4
try:
    r: int[64] = 100 % n
except ZeroDivisionError:
    print("zero")
else:
    print("else ran")
finally:
    print("finally ran")`,
            output: "else ran\nfinally ran",
          },
        },
        {
          kind: "code",
          example: {
            title: "the same program with a trap",
            typecheck: false,
            source: `n: int[64] = 0
try:
    r: int[64] = 100 % n
except ZeroDivisionError:
    print("zero")
else:
    print("else ran")
finally:
    print("finally ran")`,
            output: "zero\nfinally ran",
          },
        },
        {
          kind: "note",
          tone: "note",
          title: "A `try` with a `finally` but no `except` is refused",
          text: "`try`/`finally` without an `except` is rejected: without an arm there is nothing to route a trap to, so the `finally` would have to be a catch-all by accident.",
        },
      ],
    },
    {
      id: "nesting",
      title: "Nesting and the trap boundary",
      blocks: [
        {
          kind: "p",
          text: "`try` blocks nest, and the innermost matching arm wins. An outer `except Exception` will catch a trap that no inner arm claimed.",
        },
        {
          kind: "code",
          example: {
            title: "inner catches first",
            typecheck: false,
            source: `a: int[64] = 5
try:
    try:
        inner: int[64] = a % 0
    except ZeroDivisionError:
        print("inner caught")
except Exception:
    print("outer caught")`,
            output: "inner caught",
          },
        },
        {
          kind: "callout",
          title: "A trap inside a called function is not catchable by the caller",
          text: "The trap unwinds **past** the call frame. If `f()` divides by zero, the `try` around `f()` in the caller does not catch it: the function boundary is a trap boundary. Catch it inside `f`.",
        },
        {
          kind: "note",
          tone: "warn",
          title: "This is the one place Lithon is stricter than Python",
          text: "In CPython an exception raised inside a callee propagates through the caller and can be caught there. Lithon does not: the trap is a property of the operation, and the operation is inside the callee.",
        },
      ],
    },
    {
      id: "what-is-refused",
      title: "What is deliberately refused",
      blocks: [
        {
          kind: "table",
          caption: "refused exception forms",
          rows: [
            [
              "A bare `except:`",
              "refused: it would also catch a system-exit, and here it would suggest catching a segfault, which is not catchable. Use `except Exception:`.",
            ],
            [
              "`except X as e:`",
              "refused: there is no exception object to bind. The handler learns **which** trap fired from which arm it is in.",
            ],
            [
              "`raise`",
              "refused: a program cannot invent a trap. Only the engine raises.",
            ],
            [
              "`except*`",
              "refused: no exception groups.",
            ],
            [
              "A `try` with only a `finally`",
              "refused: without an `except` there is nowhere to route a trap.",
            ],
            [
              "Catching an unlisted name",
              "refused: the catchable set is closed.",
            ],
          ],
        },
        {
          kind: "code",
          example: {
            title: "rejected: a bare except",
            expect: "error",
            tool: "frontend",
            errorMatch: "a bare `except:` is not supported",
            source: `try:
    x: int[64] = 1
except:
    print("no")`,
            output:
              "LITHON-E0114: a bare `except:` is not supported -- it also catches system-exit, and here it would suggest catching a segfault, which is not catchable; name the trap (`except Exception:` catches every catchable trap) (6.10)",
          },
        },
      ],
    },
    {
      id: "traps-in-the-reference",
      title: "Where the traps live in the reference",
      blocks: [
        {
          kind: "p",
          text: "The error-code table in the reference lists every code, including the ones that are *designed* rather than shipped. The distinction matters: a trap you can catch is not the same as a static refusal.",
        },
        {
          kind: "comparison",
          caption: "refusal vs trap",
          columns: ["", "Static refusal", "Runtime trap"],
          rows: [
            ["When", "before the program runs", "while the program runs"],
            ["Where", "the frontend and the type checker", "the engine"],
            ["Message", "`LITHON-Ennnn: ...`", "a short reason, e.g. `error: division by zero`"],
            ["Catchable", "no — the program does not exist", "yes, with `try`/`except`"],
            ["Example", "`LITHON-E0105: unsupported float width 32`", "`error: list index out of range`"],
          ],
        },
        {
          kind: "linkcards",
          items: [
            {
              title: "Errors & diagnostics",
              href: "/docs/errors",
              text: "Every code, tier by tier, with real output.",
            },
            {
              title: "Control flow",
              href: "/docs/control-flow",
              text: "The `try` statement in the context of the other flow forms.",
            },
            {
              title: "Divergences from Python",
              href: "/docs/python-divergences",
              text: "How Lithon's error model differs from CPython's.",
            },
          ],
        },
      ],
    },
  ],
}

export const EXCEPTION_PAGES: DocPage[] = [exceptions]
