"""Plain-CPython equivalent of test_codes_lithon/Functions/max_prog.lio.

Same program: read two integers from stdin, print which is larger together
with the delta, then print the winner again.

Both engines read the two values from stdin, so this program is only
meaningful when stdin is fed (e.g. `echo "3 7" | python3 max_prog.py`).
"""
def get_max(a, b):
    if a > b:
        print(f"Delta is |a-b|: {a-b}")
        return a
    else:
        if b > a:
            print(f"Delta is |b-a|: {b-a}")
            return b
        elif a == b:
            print(f"Delta is: 0")
            return 0


a = int(input("Enter value 1: "))
b = int(input("Enter value 2: "))
if get_max(a, b) != 0:
    print(f"Larger value between {a} and {b} is: {get_max(a,b)}")
else:
    print("No larger value!")
