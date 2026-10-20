# 4.7. Enum struct-payload variants and an enum used as a struct field.
#
# A variant whose payload is declared inline with `class V(Struct)` is a struct
# payload: the struct is registered as a type of its own (equivalent to
# declaring it beforehand) and packed at the payload offset, so its fields are
# bound by name in a `match`. An enum can also be a struct field, in which case
# its blob is packed inside the struct's blob and a construction or match names
# the field path (`c.proto`).
#
# Layouts, no padding: Endpoint is 7 bytes -- int[8] tag at 0, then the largest
# payload, Tcp's `int[16]` + `int[32]` = 6 bytes, at offset 1. Conn is 15 bytes:
# proto at 0 (7 bytes), id at 7 (8 bytes).
#
# CPython cannot run this file, so expected/enum_struct.out is written from the
# documented 4.7 semantics rather than recorded from a CPython run.


class Endpoint(Enum):
    Empty
    class Tcp(Struct):
        port: int[16]
        addr: int[32]
    Udp: int[32]


class Conn(Struct):
    proto: Endpoint
    id: int[64]


# Construct a struct payload by field name into an enum-typed struct field.
c: Conn
c.proto = Endpoint.Tcp(port=8080, addr=16777343)
c.id = 7

match c.proto:
    case Endpoint.Tcp(port=p, addr=a):
        print(p)
        print(a)
    case Endpoint.Udp(n):
        print(n)
    case Endpoint.Empty:
        print(-1)
print(c.id)

# Overwrite with a different payload variant: the old Tcp bytes are inactive
# and the Udp arm reads only the int[32] it owns.
c.proto = Endpoint.Udp(53)
match c.proto:
    case Endpoint.Tcp(port=p, addr=a):
        print(p)
    case Endpoint.Udp(n):
        print(n)
    case Endpoint.Empty:
        print(-1)

# And a unit variant over the top of it.
c.proto = Endpoint.Empty
match c.proto:
    case Endpoint.Empty:
        print(0)
    case _:
        print(-1)

# A construction in the declaration, with positional struct-payload fields.
e2: Endpoint = Endpoint.Tcp(80, 1)
match e2:
    case Endpoint.Tcp(port=p, addr=a):
        print(p + a)
    case _:
        print(-1)
