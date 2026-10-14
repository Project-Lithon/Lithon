#pragma once

#include <cstdint>

// Phase III: the raw syscall primitive.
//
// The language surface is the compiler-recognized builtin
//
//   syscall(number, a1, a2, a3, a4, a5, a6) -> int[64]
//
// lowered to one `syscall` IR op. The result is the kernel's raw return,
// UNCHECKED: on Linux a failure is -errno, exactly as the kernel reports it.
//
// The platform fork lives HERE, not in the code generator. Both engines -- the
// interpreter and the JIT -- arrive at `host_syscall` with the number and the
// six argument slots, so the two tiers cannot disagree about the calling
// convention. The fork is:
//
//   1. An installed hook, on EVERY platform. See `g_syscall_hook` below.
//   2. Otherwise, the real instruction on Linux/x86-64: `syscall` (0F 05),
//      number in RAX, arguments in RDI/RSI/RDX/R10/R8/R9, result in RAX. Note
//      that the kernel takes argument 4 in R10 rather than RCX, and that the
//      instruction clobbers RCX (the return RIP) and R11 (the saved RFLAGS).
//   3. Otherwise, on a platform with no stable documented raw-syscall ABI
//      (Windows, and non-x86-64 POSIX), -ENOSYS rather than a guess. This is
//      the "DLL-call via host" fork: the embedder, not the language, supplies
//      the platform mapping.
namespace lithon::jit {

// The interception point for raw syscall numbers. `args` points at six int64
// slots; unused trailing slots are zero. Return the same raw value the language
// would otherwise see (a Linux-style negative errno is the convention).
//
// It is consulted FIRST, on every platform, not only where the platform fork
// would otherwise force it. Two reasons:
//
//   * Windows needs it to be reachable at all -- there is no instruction to
//     fall back to, so a hook is the only way a Windows program can issue a
//     real syscall.
//   * It makes the seam testable on the host this project builds on. If the
//     hook were only reachable from the non-Linux branch, it would be dead code
//     everywhere the tests actually run, and "the Windows path works" would be
//     an untested claim. Consulting it first means one test covers the routing
//     for both engines on Linux, and the inline-asm path below is covered
//     separately by calling it directly.
//
// Not thread-safe to swap while calls are in flight: assign it during startup,
// before any compiled code runs.
using SyscallHook = int64_t (*)(int64_t number, const int64_t* args);

inline SyscallHook g_syscall_hook = nullptr;

inline int64_t host_syscall(int64_t number, const int64_t* args) {
    if (g_syscall_hook) return g_syscall_hook(number, args);
#if defined(__linux__) && defined(__x86_64__)
    int64_t ret;
    register int64_t r10 __asm__("r10") = args[3];
    register int64_t r8 __asm__("r8") = args[4];
    register int64_t r9 __asm__("r9") = args[5];
    __asm__ volatile("syscall"
                     : "=a"(ret)
                     : "a"(number), "D"(args[0]), "S"(args[1]), "d"(args[2]),
                       "r"(r10), "r"(r8), "r"(r9)
                     : "rcx", "r11", "memory");
    return ret;
#else
    (void)number;
    (void)args;
    return -38;  // -ENOSYS: no raw-syscall ABI here until a hook is installed.
#endif
}

} // namespace lithon::jit
