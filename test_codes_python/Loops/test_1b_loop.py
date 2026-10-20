"""Plain-CPython equivalent of test_codes_lithon/Loops/test_1b_loop.lio.

Same program: a 1e9-iteration while loop that increments a counter and
prints nothing -- measures pure loop overhead.
"""
i = 0
while i < 1000000000:
    i = i + 1
