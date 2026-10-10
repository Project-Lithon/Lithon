# max_prog.py
#
# `int()` and `input()` are not supported yet -- the frontend lowers the
# string literal inside input() to "expression node Constant not supported
# yet". So the interactive prompt is replaced with fixed operands; the
# program itself is the point.
def find_max(a: int[8], b: int[8]) -> int[8]:
    if a > b:
        return a
    else:
        return b

a: int[8] = 7
b: int[8] = 3
print(find_max(a, b))