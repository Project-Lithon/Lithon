# 4.3. A dict: built from a constant-keyed literal, then read only.
#
# Everything in this file is written so that CPython itself produces the same
# output, which makes expected/dict_basic.out a real oracle rather than a
# recording of whatever this tier happened to print. A Python dict with these
# literal keys and these reads is the same relation, whatever its own table
# layout and probe order happen to be; the only thing the test fixes is that a
# dict keyed 1, 2, 3, 4 in four buckets reads back what was put into it.
#
# The two things CPython cannot express live in dict_table.py instead, because
# a bare declaration and contains(d, k) are 4.3 operations, not Python ones.

# A dict built from a literal. The valueless declaration store comes first and
# reserves and zeroes the table, so the literal is a fill of a table that
# already exists rather than an allocation trick.
d: dict[int[64], int[64], 4] = {1: 10, 2: 20, 3: 30, 4: 40}
print(d[1])
print(d[4])

# A runtime key, which is the case where the hash and the probe have to be
# emitted rather than folded. N is static, so a literal key is resolved while
# compiling and this one is not.
k: int[64] = 2
print(d[k])
j: int[64] = k + 1
print(d[j])

# A float value type is carried rather than assumed, and a float prints as a
# float.
f: dict[int[64], float[64], 4] = {1: 1.5, 2: 2.25, 3: -0.5}
print(f[1])
print(f[3])

# A bool value type, and a bool key type. In Python True and False are already
# the keys 1 and 0, which is the same normalisation 4.3 applies, so this dict
# is identical in both languages.
b: dict[int[64], bool, 4] = {1: True, 2: False}
print(b[1])
print(b[2])
f2: dict[bool, int[64], 4] = {True: 7, False: 8}
print(f2[True])
print(f2[False])

# A negative literal key. Python spells -1 as a negation of a literal, so this
# exercises the same shape as any other negative constant in the language.
n: dict[int[64], int[64], 8] = {-1: 100, -2: 200, 0: 300}
print(n[-1])
print(n[0])

# A narrow key type. The table stores one byte per key, and the key literal is
# a full width int, so this is the case where the key has to be narrowed to the
# width the dict declares before it is hashed.
w: dict[int[8], int[16], 4] = {100: 7, -128: 9}
print(w[100])
print(w[-128])

# More entries than buckets would be a duplicate bucket problem, so this stops
# one short of it and leans on the probe instead. Six keys in eight buckets
# still collide, and key_pairs.py is the test that pins a collision down to
# fixed keys rather than leaving it to chance.
c: dict[int[64], int[64], 8] = {1: 100, 2: 200, 3: 300, 4: 400, 5: 500, 6: 600}
print(c[1])
print(c[6])