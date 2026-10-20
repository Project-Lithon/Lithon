"""Plain-CPython equivalent of test_codes_lithon/Loops/test_1b_loop_print.lio.

Same program as Loops/test_1b_loop.py, but printing the counter each iteration.
This one is dominated by output, and is kept because the difference between
the two isolates loop cost from print cost.
"""
i = 0
while i < 1000000000:
    i = i + 1
    print(i)
