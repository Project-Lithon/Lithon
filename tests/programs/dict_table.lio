# 4.3. The two dict operations CPython cannot run.
#
# A bare declaration and contains(d, k) are not Python operations, so this file
# has no CPython oracle and expected/dict_table.out is written from the
# documented 4.3 semantics rather than recorded from a run. Everything in
# dict_basic.py is checked against real CPython instead; the split is here so
# the one file that needs a hand-written oracle is obvious rather than spread
# across every dict test.
#
# A bare declaration reserves and zeroes the table and is the all empty dict.
# That is the case that makes the immutability rule non trivial: construction is
# not written or partially written, never written some other way.
e: dict[int[64], int[64], 4]
print(contains(e, 1))
print(contains(e, 0))

# contains is the only way to ask about a key that may be absent. Reading it
# would trap instead, so a program that cannot rule a key out uses this.
d: dict[int[64], int[64], 4] = {1: 10, 2: 20}
print(contains(d, 1))
print(contains(d, 9))

# The key is a runtime value here, and both tiers have to hash it and probe for
# it rather than fold anything.
k: int[64] = 1
print(contains(d, k))

# A float valued dict reports a float and a bool valued dict reports a keyword,
# so the value type is carried through contains and the read alike.
f: dict[int[64], float[64], 2] = {1: 1.5}
print(f[1])
print(contains(f, 2))

# One bucket chain, chosen because these four keys all hash to the same bucket
# of a four bucket table, so the table is exactly full and every one of them was
# reached by walking past the ones stored before it.
#
# The buckets here are not guessed. With the 4.3 hash they are fixed, and
# dict_hash_test pins them down, so this file only has to read them back.
one: dict[int[64], int[64], 4] = {1: 110, 6: 660, 9: 990, 14: 1440}
print(one[1])
print(one[6])
print(one[9])
print(one[14])
print(contains(one, 5))

# A dict declared inside a loop is a fresh table each iteration, so the
# declaration is a reinitialisation rather than a second fill of the first one.
# The key is computed from the loop counter, which is legal for a read and
# would be refused for a store, because only a literal key can be a store.
i: int[64] = 0
hits: int[64] = 0
while i < 4:
    t: dict[int[64], int[64], 4] = {7: 70, 9: 90}
    probe: int[64] = i + 7
    if contains(t, probe):
        hits = hits + 1
    i = i + 1
print(hits)

# A full table that is probed for a key it does not have. Every bucket is
# occupied here, so a probe that forgot to stop after N steps would wrap for
# ever, and one that stopped at the first occupied bucket would answer the wrong
# question entirely.
full: dict[int[64], int[64], 4] = {1: 110, 6: 660, 9: 990, 14: 1440}
print(contains(full, 3))
print(contains(full, 20))