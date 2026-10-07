# _ptr = addressof(val), val should be initialized variable
i:int[16] = 16
_ptrI:ptr[int[16]] = addressof(i)
print(_ptrI)
print(valueof(_ptrI))

