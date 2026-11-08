/**
 * The playground's built-in examples.
 *
 * They live in their own module so the IR generator can read the same
 * source the page renders, rather than a copy that can drift.
 */
export type PlaygroundExample = {
  id: string
  title: string
  note: string
  source: string
}

export const PLAYGROUND_EXAMPLES: readonly PlaygroundExample[] = [
  {
    id: "hello",
    title: "Hello, typed",
    note: "the smallest annotated program",
    source: `x: int[64] = 10
y: int[64] = 20

result: int[64] = x + y

print(result)`,
  },
  {
    id: "fib",
    title: "Recursive fibonacci",
    note: "recursion, tier-1 native",
    source: `def fib(n: int[64]) -> int[64]:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)

x: int[64] = fib(10)
print(x)`,
  },
  {
    id: "range",
    title: "Typed range loop",
    note: "for-range with a width-checked loop variable",
    source: `total: int[64] = 0
i: int[64] = 0

for i in range(10):
    total = total + i

print(total)`,
  },
  {
    id: "while",
    title: "While loop",
    note: "conditionals with a loop-carried accumulator",
    source: `i: int[64] = 0
total: int[64] = 0

while i < 10:
    total = total + i
    i = i + 1

print(total)`,
  },
  {
    id: "float",
    title: "Float arithmetic",
    note: "float[64], SSE2 end to end",
    source: `a: float[64] = 3.5
b: float[64] = 2.0

print(a + b)
print(a * b)`,
  },
  {
    id: "wrap",
    title: "Wrapping arithmetic",
    note: "wrap_add at the int[64] boundary",
    source: `x: int[64] = 9223372036854775807
y: int[64] = 1
z: int[64] = wrap_add(x, y)
print(z)`,
  },
  {
    id: "pointer",
    title: "Typed pointers",
    note: "explicit raw memory, `_` prefix required",
    source: `i: int[16] = 16
_pi: ptr[int[16]] = addressof(i)
print(valueof(_pi))`,
  },
  {
    id: "list",
    title: "Fixed-capacity list",
    note: "capacity lives in the type",
    source: `xs: list[int[64], 6]

xs[0] = 10
xs[1] = 20
xs[2] = 30

print(xs[0])
print(len(xs))`,
  },
  {
    id: "class-basic",
    title: "Classes: fields + methods",
    note: "heap reference, __init__, inlined method",
    source: `class Point:
    x: int[64]
    y: int[64]

    def __init__(self, x: int[64], y: int[64]):
        self.x = x
        self.y = y

    def total(self) -> int[64]:
        return self.x + self.y

    def scaled(self, k: int[64]) -> int[64]:
        return self.total() * k

p: Point = Point(3, 4)
print(p.total())
print(p.scaled(10))
print(p.x)`,
  },
  {
    id: "class-inherit",
    title: "Inheritance + super()",
    note: "vtable dispatch, dynamic super()",
    source: `class Shape:
    sides: int[64]

    def __init__(self, sides: int[64]):
        self.sides = sides

    def area(self) -> int[64]: ...

    def describe(self) -> int[64]:
        return self.area() * 10 + self.sides


class Square(Shape):
    side: int[64]

    def __init__(self, side: int[64]):
        super().__init__(4)
        self.side = side

    def area(self) -> int[64]:
        return self.side * self.side


s: Square = Square(4)
sh: Shape = s
print(sh.area())
print(sh.describe())`,
  },
  {
    id: "class-decorators",
    title: "Properties + classmethods",
    note: "@property read, @classmethod construct",
    source: `class Point:
    x: int[64]
    y: int[64]

    def __init__(self, x: int[64], y: int[64]):
        self.x = x
        self.y = y

    @property
    def total(self) -> int[64]:
        return self.x + self.y

    @classmethod
    def origin(cls) -> Point:
        return cls(0, 0)

p: Point = Point(2, 3)
print(p.total)
q: Point = Point.origin()
print(q.x)`,
  },
]
