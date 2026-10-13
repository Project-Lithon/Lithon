# 4.6. Nested structs and the no-padding layout.
#
# A field may itself be a struct declared EARLIER. Because fields are packed at
# exactly their declared offsets, the inner struct sits at a fixed offset and its
# own fields are offset from there, so `seg.a.x` resolves to one leaf field read
# at a summed byte offset -- there is no intermediate struct value in between.
#
# Point is 16 bytes (8 + 8) and Segment is 33 (16 + 16 + 1), which is the whole
# point: no padding is inserted, so the layout is exactly the sum of the field
# sizes and the C comparison in the prove-it test can compute the same numbers.
#
# CPython cannot run `class Point(Struct)`, so expected/struct_nested.out is
# written from these documented semantics rather than recorded from a run.


class Point(Struct):
    x: int[64]
    y: int[64]


class Segment(Struct):
    a: Point
    b: Point
    tag: int[8]


seg: Segment
seg.a.x = 10
seg.a.y = 20
seg.b.x = 30
seg.b.y = 40
seg.tag = 7

print(seg.a.x)
print(seg.a.y)
print(seg.b.x)
print(seg.b.y)
print(seg.tag)

# Writing through a nested path and reading it back on the same path shows the
# summed offset is applied consistently in both directions.
seg.a.x = seg.a.x + 1
print(seg.a.x)

# A fresh inner struct written into the outer one, field by field, then read
# whole-path. No struct value is ever named; only the leaves are.
seg.b.x = 100
seg.b.y = 200
print(seg.b.x + seg.b.y)
