# 4.1. A list is packed: the element stride is sizeof(T), not a uniform word.
#
# Three widths in one function, so the frame layout has to be right for all of
# them at once. index 1 of each is read back after a write to index 0, which is
# the case a uniform stride gets wrong: with a uniform 8-byte stride the int[32]
# list would put element 1 at +8 and quietly leave +4..+7 unread, so the second
# write would land in the wrong slot and both tiers would have to agree on a
# wrong answer to hide it.
#
# bool is written last because it is 1 byte and therefore the stride that makes
# a mistake in the other direction -- reading +8 instead of +1 -- look like a
# plausible number instead of an obviously huge one.

ns: list[int[32], 4]
ss: list[int[16], 3]
bs: list[bool, 4]
fs: list[float[64], 3]

ns[0] = 11
ns[3] = 44
ss[0] = 300
bs[0] = True
bs[1] = False
fs[0] = 1.5
fs[2] = 2.5

print(ns[0])
print(ns[1])
print(ns[3])
print(ss[0])
print(ss[1])
print(bs[0])
print(bs[1])
print(bs[3])
print(fs[0])
print(fs[1])
print(fs[2])
print(len(ns))
print(len(bs))