# 4.9. Methods: a `def` inside a struct or enum body, called as `recv.name(...)`.
#
# A method is not a value and not a separate function: the call is INLINED at
# the call site, with the receiver bound to `self`. That is forced by the
# storage model -- a field op names a variable (not a register), and a struct
# cannot be passed to a function (E0124) -- and it is what lets a method read
# AND write the receiver's fields, run loops and branches, and call other
# methods. `struct`/`Struct` is not a Python builtin, so this file has no
# CPython oracle; expected/method_basic.out is written from the documented 4.9
# semantics and confirmed by both tiers agreeing byte for byte.
#
# A method is declared like any function: the first parameter is the
# unannotated `self` (the receiver), the rest are annotated scalars, and the
# return annotation is optional. Methods are inlined, so they cannot recurse
# (directly or indirectly); that is refused at the call site.


class Counter(Struct):
    value: int[64]

    # A value-returning method reads the receiver and takes a scalar parameter.
    def add(self, n: int[64]) -> int[64]:
        self.value = self.value + n
        return self.value

    # A method may keep locals and branch. Its locals are private to the call:
    # a name here cannot capture or clobber a caller's variable of the same
    # spelling, which is why the caller's `i` below is unchanged.
    def running_total(self, n: int[64]) -> int[64]:
        total: int[64] = 0
        i: int[64] = 1
        while i < n + 1:
            total = total + i
            i = i + 1
        return total

    # Early return: the first value above `thr`, or -1 when there is none.
    def first_over(self, thr: int[64]) -> int[64]:
        i: int[64] = 0
        while i < self.value:
            if i > thr:
                return i
            i = i + 1
        return -1

    # A method can call another method on the same receiver.
    def add_twice(self, n: int[64]) -> int[64]:
        self.add(n)
        return self.add(n)


c: Counter
print(c.add(5))
print(c.add(7))
print(c.running_total(4))

c.value = 10
print(c.first_over(4))
print(c.first_over(100))

c.value = 0
print(c.add_twice(3))

# The caller's own `i` is untouched by the method's local `i`.
i: int[64] = 99
c.value = 0
print(c.running_total(3))
print(i)


# A receiver can be a struct field: `o.inner` resolves to the field's bytes, so
# a method on Inner reads and writes the caller's `inner`.
class Inner(Struct):
    x: int[64]

    def bump(self, by: int[64]):
        self.x = self.x + by

    def get(self) -> int[64]:
        return self.x


class Outer(Struct):
    inner: Inner
    tag: int[64]


o: Outer
o.inner.x = 10
o.inner.bump(5)
print(o.inner.get())
print(o.inner.get() + o.inner.get())
o.tag = 1
print(o.tag)
