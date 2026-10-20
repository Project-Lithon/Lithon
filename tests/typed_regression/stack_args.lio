def add3(a: int[64], b: int[64], c: int[64]) -> int[64]:
    return a + b + c

def sum8(a: int[64], b: int[64], c: int[64], d: int[64], e: int[64], f: int[64], g: int[64], h: int[64]) -> int[64]:
    return a + b + c + d + e + f + g + h

def pack6(a: int[64], b: int[64], c: int[64], d: int[64], e: int[64], f: int[64]) -> int[64]:
    return a * 100000 + b * 10000 + c * 1000 + d * 100 + e * 10 + f

def sub3(a: int[64], b: int[64], c: int[64]) -> int[64]:
    return a - b - c

def inner4(a: int[64], b: int[64], c: int[64], d: int[64]) -> int[64]:
    return a + b + c + d

def outer() -> int[64]:
    return inner4(1, 2, 3, 4) + inner4(10, 20, 30, 40)

def mut(lazy: int[64], b: int[64], c: int[64]) -> int[64]:
    lazy = lazy + 1
    return lazy

print(add3(1, 2, 3))
print(add3(-5, 100, 3))
print(sub3(30, 5, 2))
print(sum8(1, 2, 3, 4, 5, 6, 7, 8))
print(sum8(10, 20, 30, 40, 50, 60, 70, 80))
print(pack6(1, 2, 3, 4, 5, 6))
print(pack6(9, 0, 8, 7, 2, 5))
print(outer())
print(mut(5, 100, 200))