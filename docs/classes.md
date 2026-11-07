# Classes (Lithon 4.10)

A baseless `class` is a heap object reached through an 8-byte reference — the
third `class` spelling alongside `class P(struct)` (inline packed storage)
and `class E(enum)` (tagged variant). Design record:
`docs/whitepaper.md` §§4.10.0–4.10.6. Web: `/docs/classes`.

## 1. Flat syntax and layout

```lio
class Point:
    x: int[64]
    y: int[64]

    def __init__(self, x: int[64], y: int[64]):
        self.x = x
        self.y = y


p: Point = Point(3, 4)
```

Layout: `[vptr:8][refcount:8][attr0]…` (16-byte header). `vptr` is the type
tag. Base attributes come first, so upcasting is a free pointer. Allowed
attribute types: `int[w]`/`float[64]`/`bool` and another class reference
(else E0127). No `__dict__`, no dynamic attributes, never null (no
`None`/`Optional` yet).

Definite-init: every attribute assigned on every `__init__` path or
construction is refused (E0140); undeclared attribute read/write is E0141.
A bare class name (`p: Point = Point`) is refused — only `Point(...)`
constructs.

## 2. Lifetime

`b = a`, `return`, and attr-store retain. The reference drops at the owning
scope exit (block exit, loop back edge, `return`, `break`, `continue`).
`__del__` runs at the exact last drop, instance still fully formed, then
owned children drop recursively. Cycles leak; exceptional unwind leaks.

Tests: `tests/typed_regression/class_basic.lio`,
`class_lifetime.lio`, `class_references.lio`, `class_returns.lio`.

## 3. Single inheritance and `super()`

Base attributes keep their offsets (free upcast). Each method name has one
global slot; `CallVirtual` reads the receiver's own descriptor. `super()`
is dynamic: it searches after the *defining* class in the receiver's actual
MRO. `super().__init__` is static along the construction MRO;
`super().__del__` is refused. `...` body = abstract; construction refused
(E0140).

## 4. Multiple inheritance and C3

`class D(B, C):` uses C3 linearization with prefix-compatible layout — a
diamond's shared base is stored once; a layout conflict is E0127.
`isinstance(x, C)` probes an ancestor bitset; `issubclass(A, B)` folds at
compile time. Related-class comparisons only: unrelated or narrowing casts
are E0140. `is`/`is not` are always identity.

## 5. Containers of references

`list[P,N]`/`tuple[P,N]` hold one owned reference per slot (borrow-on-read).
A literal must be full; `append` and bare declarations are refused;
`enumerate`/`zip` and params/returns are refused (E0119/E0124) — workaround
`for i in range(N): xs[i]`.

## 6. Comparison and text dunders

`__eq__`/`__ne__`/`__lt__`/`__le__`/`__gt__`/`__ge__`: one class-ref
parameter, `->bool`, left-operand dispatch only (no reflected priority,
no `NotImplemented`). Ordering without the dunder is E0135.

`__str__`/`__repr__`: one literal/f-string of direct scalar `self.*`,
`->str[N]`, not callable. `print(obj)` picks `__str__`, else `__repr__`,
by the concrete class; neither defined is E0135.

## 7. Decorators

Only `@property` (read-only, `p.total`, vtable slot) and `@classmethod`
(unannotated `cls`, no slot, `cls(...)` builds the call-time class). All
other decorators and stacked pairs are E0134; decorators on struct/enum
methods are E0134; kind-changing overrides are E0135.

## Deferred

`__dict__`/dynamic attributes, `None`/`Optional`, dict class keys/values,
other decorators, metaclasses, reference cycles (leak), reflected
comparisons, exception classes (declare `class MyError:` freely, but it
cannot be raised or caught).

## Verification

15 `class_*.lio` programs + `tools/fuzz_diff.py --oop` (6 shapes,
200/200) + `object_runtime_test` (CTest, 107 ok) +
`tools/check_error_codes.py` refusal coverage.
