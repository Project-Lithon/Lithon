total: int[64] = 0
for i in range(100):
    if i == 5:
        continue
    if i == 10:
        break
    total = total + i
print(total)

n: int[64] = 0
while n < 10:
    n = n + 1
    if n == 3:
        continue
    if n == 7:
        break
    print(n)
