i:int[16] = 22
ptrI:ptr[int[16]] = addressof(i) # should be _ptrI
print(valueof(ptrI)) # and same to be here
