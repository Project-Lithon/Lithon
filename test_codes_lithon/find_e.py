e:float[128] = 0.0
i:int[64] = 0

def get_factorial(lim:int[64]) -> int[64]:
	fact:int[64] = 1
	while lim > 1:
		fact *= lim
		lim -= 1
	print(fact)
	return fact

for i in range(10):
	print(1/get_factorial(i+1))
	e += 1/(get_factorial(i+1))

print(e)
