import type { DocPage } from "../model"

/**
 * Networking (5.2): raw syscalls over real memory.
 *
 * The shipped proof is tests/net/loopback.lio, checked against a C oracle
 * that issues the identical syscalls.
 */
const networking: DocPage = {
  slug: "networking",
  title: "Networking",
  description:
    "Real sockets from raw syscalls: a TCP loopback round trip checked against a C oracle, and the byte-order obligation that comes with it.",
  group: "language",
  tags: [
    "networking",
    "sockets",
    "tcp",
    "syscall",
    "loopback",
    "byte order",
    "sockaddr",
    "5.2",
  ],
  priority: "reference",
  outcome: "Open a TCP connection from Lithon with raw syscalls and real memory.",
  minutes: 12,
  sections: [
    {
      id: "the-premise",
      title: "The premise: a pointer is a real address",
      blocks: [
        {
          kind: "p",
          text: "Networking in Lithon is not a library. It is `syscall` with a pointer, and the reason that works is a specific design decision: **a pointer argument to a syscall is a real host address in both tiers**. The interpreter does not emulate a synthetic address space; it hands the kernel the actual address of the variable.",
        },
        {
          kind: "callout",
          title: "This is the payoff of the storage model",
          text: "Because a `struct` is a packed blob at a known address and a pointer to it is a real address, the kernel can read and write Lithon's memory directly. A `bind()` that fills the port back into your struct is not a simulation of a kernel: it is the kernel.",
        },
        {
          kind: "p",
          text: "The shipped test is `tests/net/loopback.lio`, and `tools/check_net_loopback.py` compiles a C program (`tests/net/loopback_oracle.c`) that issues the identical syscalls, then requires the two programs to print the same lines. A C oracle rather than the interpreter, because sockets are stateful and external: both Lithon tiers could be wrong in the same way, and the C program is not.",
        },
      ],
    },
    {
      id: "syscall-signature",
      title: "The syscall primitive",
      blocks: [
        {
          kind: "p",
          text: "`syscall(n, a1, ..., a6)` issues Linux syscall number `n` and returns the kernel's return value as an `int[64]`. It is unchecked: a negative result is the kernel's `-errno`, and Lithon does not turn it into a trap.",
        },
        {
          kind: "deflist",
          caption: "argument kinds",
          items: [
            {
              term: "int[64]",
              detail:
                "a scalar argument. The kernel sees the value itself, passed in a register.",
            },
            {
              term: "ptr[T]",
              detail:
                "a real host address. The kernel reads and writes exactly the bytes the language does. This is what makes a `sockaddr` or a buffer work.",
            },
            {
              term: "float[64]",
              detail:
                "refused: *floats and aggregates are not passed by value*. A syscall that takes a float is not reachable from Lithon.",
            },
          ],
        },
        {
          kind: "code",
          example: {
            title: "socket(AF_INET, SOCK_STREAM, 0)",
            typecheck: false,
            source: `srv: int[64] = syscall(41, 2, 1, 0)
print(srv > 0)`,
            output: "True",
          },
        },
        {
          kind: "note",
          tone: "note",
          title: "Six arguments, no more",
          text: "The kernel's ABI has six argument registers. `syscall` takes at most six arguments, and the frontend refuses a seventh rather than silently dropping it.",
        },
      ],
    },
    {
      id: "the-loopback",
      title: "The loopback round trip",
      blocks: [
        {
          kind: "p",
          text: "The shipped test does a full TCP round trip in one process: a server socket binds to `127.0.0.1` on an ephemeral port, listens, and accepts; a second socket connects back; the client sends an `int[64]` and the accepted connection receives it.",
        },
        {
          kind: "code",
          example: {
            title: "tests/net/loopback.lio (excerpt)",
            sourceRef: "tests/net/loopback.lio",
            typecheck: false,
            source: `class SockAddrIn(Struct):
    lo: int[64]
    hi: int[64]


class Buf(Struct):
    v: int[64]


sa: SockAddrIn
sa.lo = 2 | (swap32(2130706433) << 32)
sa.hi = 0
_sa: ptr[SockAddrIn] = addressof(sa)

socklen: int[64] = 16
_psocklen: ptr[int[64]] = addressof(socklen)

# socket(AF_INET=2, SOCK_STREAM=1, 0)
srv: int[64] = syscall(41, 2, 1, 0)

# bind(srv, &sa, sizeof(sockaddr_in))
print(syscall(49, srv, _sa, 16))

# listen(srv, backlog=1)
print(syscall(50, srv, 1))`,
            output: "0\n0",
          },
        },
        {
          kind: "code",
          example: {
            title: "the round trip (excerpt)",
            sourceRef: "tests/net/loopback.lio",
            typecheck: false,
            source: `class Buf(Struct):
    v: int[64]


out: Buf
out.v = 123456789
inb: Buf
inb.v = 0
_pout: ptr[Buf] = addressof(out)
_pin: ptr[Buf] = addressof(inb)

# sendto(cli, &out, 8, 0, NULL, 0) == 8 bytes
print(syscall(44, cli, _pout, 8, 0, 0, 0))
# recvfrom(conn, &in, 8, 0, NULL, NULL) -> 8, and the bytes land in inb.
print(syscall(45, conn, _pin, 8, 0, 0, 0))
print(inb.v)`,
            output: "8\n8\n123456789",
          },
        },
        {
          kind: "p",
          text: "The `getsockname` call is the proof that the address is real: the kernel writes the chosen port back into the very bytes you handed it.",
        },
        {
          kind: "code",
          example: {
            title: "getsockname writes the port back (excerpt)",
            sourceRef: "tests/net/loopback.lio",
            typecheck: false,
            source: `socklen: int[64] = 16
_psocklen: ptr[int[64]] = addressof(socklen)

# getsockname(srv, &sa, &socklen): the kernel writes the real port back into the
# very bytes we handed it, which only works because the address is real memory.
print(syscall(51, srv, _sa, _psocklen))
print(socklen)`,
            output: "0\n16",
          },
        },
      ],
    },
    {
      id: "byte-order",
      title: "The byte-order obligation",
      blocks: [
        {
          kind: "p",
          text: "A `sockaddr_in` is network byte order. A Lithon `int[64]` is little-endian. The mismatch is your problem to solve, and the test solves it with a three-line swap.",
        },
        {
          kind: "code",
          example: {
            title: "the byte swap",
            sourceRef: "tests/net/loopback.lio",
            typecheck: false,
            source: `def swap32(v: int[64]) -> int[64]:
    return ((v & 255) << 24) | ((v & 65280) << 8) | ((v >> 8) & 65280) | ((v >> 24) & 255)`,
            output: "",
          },
        },
        {
          kind: "callout",
          title: "The failure is silent, and the test proves it",
          text: "The same address **without** the swap is still a perfectly good 32-bit number. Nothing crashes: its bytes just name `1.0.0.127` instead of `127.0.0.1`, and `bind` fails with `EADDRNOTAVAIL` (-99). A happy-path connect alone would not have shown this, so `loopback.lio` deliberately makes the failure explicit at the end.",
        },
        {
          kind: "code",
          example: {
            title: "the explicit failure (excerpt)",
            sourceRef: "tests/net/loopback.lio",
            typecheck: false,
            source: `class SockAddrIn(Struct):
    lo: int[64]
    hi: int[64]


bad: SockAddrIn
bad.lo = 2 | (2130706433 << 32)
bad.hi = 0
_bad: ptr[SockAddrIn] = addressof(bad)
badfd: int[64] = syscall(41, 2, 1, 0)
print(syscall(49, badfd, _bad, 16) < 0)
print(syscall(3, badfd))`,
            output: "True\n0",
          },
        },
      ],
    },
    {
      id: "the-syscall-numbers",
      title: "The syscall numbers used",
      blocks: [
        {
          kind: "deflist",
          caption: "Linux x86-64 syscall numbers",
          items: [
            {
              term: "`41` — socket(domain, type, protocol)",
              detail: "returns the file descriptor",
            },
            {
              term: "`42` — connect(fd, addr, len)",
              detail: "the client side",
            },
            {
              term: "`43` — accept(fd, addr, len)",
              detail: "the server side",
            },
            {
              term: "`44` — sendto(fd, buf, len, flags, addr, alen)",
              detail: "send",
            },
            {
              term: "`45` — recvfrom(fd, buf, len, flags, addr, alen)",
              detail: "receive",
            },
            {
              term: "`49` — bind(fd, addr, len)",
              detail: "the server side",
            },
            {
              term: "`50` — listen(fd, backlog)",
              detail: "the server side",
            },
            {
              term: "`51` — getsockname(fd, addr, len)",
              detail: "asks the kernel for the bound address",
            },
            {
              term: "`3` — close(fd)",
              detail: "release the descriptor",
            },
          ],
        },
        {
          kind: "note",
          tone: "warn",
          title: "These numbers are Linux x86-64 specific",
          text: "The syscall ABI is per-architecture. The numbers above are the x86-64 Linux ones; on another platform the same operation has a different number. Windows uses a completely different mechanism, so a Lithon program that issues Linux syscall numbers only runs on Linux.",
        },
      ],
    },
    {
      id: "what-is-not-there",
      title: "What is not there",
      blocks: [
        {
          kind: "table",
          caption: "no socket library, and that is deliberate",
          rows: [
            [
              "A `socket` type",
              "there is none. A socket is an `int[64]` file descriptor.",
            ],
            [
              "A `bind`/`listen` function",
              "there is none. These are `syscall(49, ...)` and `syscall(50, ...)`.",
            ],
            [
              "An `import`",
              "there is none. `std/` is a sketch, not a module system.",
            ],
            [
              "A DNS resolver",
              "there is none. `getaddrinfo` would need a string API the language does not have.",
            ],
            [
              "A `struct` for `sockaddr_in`",
              "you write it yourself, packed to match the kernel's layout.",
            ],
            [
              "Cross-platform support",
              "none. The syscall numbers are Linux x86-64.",
            ],
          ],
        },
        {
          kind: "linkcards",
          items: [
            {
              title: "Syscalls",
              href: "/docs/syscalls",
              text: "The primitive itself: argument kinds, and what the two tiers agree on.",
            },
            {
              title: "Pointers",
              href: "/docs/pointers",
              text: "Why a pointer to a struct is a real address.",
            },
            {
              title: "Structs",
              href: "/docs/structs",
              text: "Packed layout, so your `sockaddr_in` matches the kernel's.",
            },
            {
              title: "Strings",
              href: "/docs/strings",
              text: "Bounded byte runs, for the buffers you hand the kernel.",
            },
          ],
        },
      ],
    },
  ],
}

export const NETWORKING_PAGES: DocPage[] = [networking]
