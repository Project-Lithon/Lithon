import type { DocPage } from "../model"

/**
 * `match`: the enum dispatch statement.
 *
 * `match` is Lithon's only pattern-matching construct, and it works on
 * enums only. This page covers the arm forms, the exhaustiveness rule, the
 * binding rules, and every shape that is refused.
 */
const match: DocPage = {
  slug: "match",
  title: "match",
  description:
    "The enum dispatch statement: arm forms, bindings, exhaustiveness, and every shape that is deliberately refused.",
  group: "language",
  tags: [
    "match",
    "pattern matching",
    "enum",
    "dispatch",
    "exhaustiveness",
    "binding",
    "case",
    "wildcard",
  ],
  priority: "essential",
  outcome: "Dispatch on an enum exhaustively, and read a variant's payload safely.",
  minutes: 8,
  sections: [
    {
      id: "what-match-does",
      title: "What a match does",
      blocks: [
        {
          kind: "p",
          text: "A `match` reads the tag of an enum once and jumps to the arm whose variant matches it. An arm for a payload variant **binds** that payload, so the arm body reads it as a named variable; an arm for a unit variant binds nothing.",
        },
        {
          kind: "code",
          example: {
            title: "the shape of a match",
            typecheck: false,
            source: `class Shape(Enum):
    Empty
    Dot
    Line: int[32]
    Rect: int[64]


s: Shape
s = Shape.Line(42)
match s:
    case Shape.Empty:
        print(0)
    case Shape.Dot:
        print(1)
    case Shape.Line(n):
        print(n)
    case Shape.Rect(w):
        print(w)`,
            output: "42",
          },
        },
        {
          kind: "callout",
          title: "A match is not a value, and it is not a switch",
          text: "It binds names in the arms, and it does not produce one. The lowered form is a chain of tag comparisons and branches — the same thing you would hand-write, which is why there is no new IR instruction for it.",
        },
      ],
    },
    {
      id: "arm-forms",
      title: "The four arm forms",
      blocks: [
        {
          kind: "deflist",
          caption: "every arm form",
          items: [
            {
              term: "case E.V:",
              detail:
                "a unit variant. Binds nothing. The arm body reads only the outer variables.",
            },
            {
              term: "case E.V(n):",
              detail:
                "a scalar payload variant. Binds the one payload to `n`, with the variant's own declared type.",
            },
            {
              term: "case E.V(f1=a, f2=b):",
              detail:
                "a struct payload variant. Binds each named field. The fields are named, not positional, so a reordering of the struct does not silently rebind them.",
            },
            {
              term: "case _:",
              detail:
                "the wildcard. Matches every variant not already covered. It must be the **last** arm.",
            },
          ],
        },
        {
          kind: "code",
          example: {
            title: "a struct payload, bound by field name",
            typecheck: false,
            source: `class Endpoint(Enum):
    Empty
    class Tcp(Struct):
        port: int[16]
        addr: int[32]
    Udp: int[32]


c: Endpoint = Endpoint.Tcp(port=8080, addr=16777343)
match c:
    case Endpoint.Tcp(port=p, addr=a):
        print(p)
        print(a)
    case Endpoint.Udp(n):
        print(n)
    case Endpoint.Empty:
        print(-1)`,
            output: "8080\n16777343",
          },
        },
      ],
    },
    {
      id: "exhaustiveness",
      title: "Exhaustiveness is checked",
      blocks: [
        {
          kind: "p",
          text: "A `match` must cover every variant, or end with a `case _:`. The checker knows the enum's variants, so it can prove the coverage statically — there is no fall-through and no runtime surprise.",
        },
        {
          kind: "code",
          example: {
            title: "rejected: not exhaustive",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "is not exhaustive",
            source: `class E(Enum):
    A
    B

e: E
match e:
    case E.A:
        print(1)`,
            output:
              "LITHON-E0131: `match` on enum 'E' is not exhaustive -- no arm covers B; add it or a `case _:` (4.7)",
          },
        },
        {
          kind: "code",
          example: {
            title: "rejected: the wildcard is not last",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "must be the last arm",
            source: `class E(Enum):
    A

e: E
match e:
    case _:
        print(1)
    case E.A:
        print(2)`,
            output:
              "LITHON-E0131: `case _:` must be the last arm -- arms after it are unreachable (4.7)",
          },
        },
        {
          kind: "note",
          tone: "note",
          title: "The wildcard is not a catch-all for a missing variant",
          text: "It is a catch-all for *any* variant not named by an earlier arm. That is why it has to be last: an arm after it could never run.",
        },
      ],
    },
    {
      id: "binding-rules",
      title: "The binding rules",
      blocks: [
        {
          kind: "table",
          caption: "what a binding may be",
          rows: [
            [
              "A name",
              "`case E.V(n):` binds `n`. The name must not start with `_`: a leading underscore in a binding is a rejection, because a binding that is never read is a mistake worth catching.",
            ],
            [
              "A field name",
              "`case E.V(port=p):` binds `p` from the struct payload's `port` field.",
            ],
            [
              "A type",
              "the bound name has the variant's declared payload type: `int[32]` for `Line: int[32]`, `int[16]` for `port`.",
            ],
            [
              "A name in scope",
              "a binding shadows an outer name of the same name inside the arm body only.",
            ],
          ],
        },
        {
          kind: "code",
          example: {
            title: "rejected: a binding that starts with an underscore",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "LITHON-E0131",
            source: `class E(Enum):
    A: int[32]

e: E
match e:
    case E.A(_n):
        print(1)
    case _:
        print(2)`,
            output:
              "LITHON-E0131: a match binding cannot start with '_'; that prefix is reserved for pointers (4.4)",
          },
        },
      ],
    },
    {
      id: "the-subject",
      title: "What can be matched",
      blocks: [
        {
          kind: "p",
          text: "The subject of a `match` must be an enum **variable** or an enum **struct field**. It cannot be a function parameter, a list element, or an expression, because `match` needs a place to read the tag from.",
        },
        {
          kind: "code",
          example: {
            title: "rejected: matching a non-enum",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "its subject must be an enum variable or an enum struct field",
            source: `x: int[64] = 1
match x:
    case 1:
        print(1)`,
            output:
              "LITHON-E0131: `match` reads an enum; its subject must be an enum variable or an enum struct field (4.7)",
          },
        },
        {
          kind: "p",
          text: "Matching a struct field is the common case, because it is how you dispatch on a protocol or a state stored inside a larger record.",
        },
        {
          kind: "code",
          example: {
            title: "matching a struct field",
            typecheck: false,
            source: `class Endpoint(Enum):
    Empty
    class Tcp(Struct):
        port: int[16]
        addr: int[32]
    Udp: int[32]


class Conn(Struct):
    proto: Endpoint
    id: int[64]


c: Conn
c.proto = Endpoint.Udp(53)
c.id = 7
match c.proto:
    case Endpoint.Tcp(port=p, addr=a):
        print(p)
    case Endpoint.Udp(n):
        print(n)
    case Endpoint.Empty:
        print(-1)
print(c.id)`,
            output: "53\n7",
          },
        },
      ],
    },
    {
      id: "what-is-refused",
      title: "What match does not do",
      blocks: [
        {
          kind: "table",
          caption: "refused match shapes",
          rows: [
            [
              "Guards",
              "`case E.V if <cond>:` is refused: *LITHON-E0131: `case ... if <guard>` is not supported yet*. Put the condition in the arm body.",
            ],
            [
              "Literal patterns",
              "`case 1:` is refused: the subject has to be an enum, so the arms are variants.",
            ],
            [
              "Binding by position",
              "`case E.V(a, b):` is refused for a struct payload. The fields are named.",
            ],
            [
              "Multiple subjects",
              "`match a, b:` is refused: one subject, one dispatch.",
            ],
            [
              "A match as an expression",
              "a `match` is a statement. It binds names; it does not produce a value.",
            ],
            [
              "A non-exhaustive match",
              "refused: every variant must be covered, or a `case _:` must be present.",
            ],
          ],
        },
        {
          kind: "code",
          example: {
            title: "rejected: a guard",
            expect: "error",
            tool: "frontend",
            typecheck: false,
            errorMatch: "is not supported yet",
            source: `class E(Enum):
    A

e: E
match e:
    case E.A if True:
        print(1)
    case _:
        print(2)`,
            output: "LITHON-E0131: `case ... if <guard>` is not supported yet (4.7)",
          },
        },
      ],
    },
    {
      id: "lowering",
      title: "What it lowers to",
      blocks: [
        {
          kind: "p",
          text: "A `match` lowers to the same tag-compare chain a hand-written dispatch would be: for each arm, a `fieldload` of the tag, an equality test, and a branch whose body reads the payload fields it bound. There is no new instruction kind and no enum value flowing through the IR.",
        },
        {
          kind: "p",
          text: "That is why a `match` cannot be a subject for another `match`, and why the arms are checked in order: the lowered code is a chain, not a table.",
        },
        {
          kind: "linkcards",
          items: [
            {
              title: "Enums",
              href: "/docs/enums",
              text: "Declaring a tagged union, and its layout.",
            },
            {
              title: "Control flow",
              href: "/docs/control-flow",
              text: "`match` next to `if`, `while` and `for`.",
            },
            {
              title: "Source to machine",
              href: "/docs/source-to-machine",
              text: "The IR and the machine code behind a match.",
            },
          ],
        },
      ],
    },
  ],
}

export const MATCH_PAGES: DocPage[] = [match]
