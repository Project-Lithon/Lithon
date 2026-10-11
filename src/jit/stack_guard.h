#pragma once

#include <cstdint>
#include <cstdio>
#include <cstdlib>

// E0401 stack-depth guard (docs/lithon_error_system.md: the stack-overflow
// trap). Lives here, next to float_runtime.h, because BOTH tiers shared it:
//
//   * the interpreter increments/decrements g_call_depth at the top and
//     bottom of execute_function -- its "prologue" and "epilogue";
//   * the JIT emits the increment+compare into every compiled function's
//     prologue and the decrement into every epilogue, reading and writing
//     the SAME g_call_depth by its absolute address, exactly the way emitted
//     code already reaches host_report_error.
//
// Sharing one counter and one threshold is what makes the two tiers trap at
// the SAME call depth with the SAME message: run_tier_diff.py compares the
// tiers byte-for-byte, so the stack guard, like every other trap, must agree
// exactly -- which a per-tier limit could never do.
//
// Threshold choice (4096): the interpreter's execute_function carries a Frame
// of unordered_maps on the real C++ stack, and MEASURING it showed ~1.1 KB of
// native stack per Lithon call -- an 8 MiB native stack runs out at roughly
// 7600 frames (survives 7600, SIGSEGVs at 7800 on this machine). 4096 traps
// with ~1.85x margin below that, well before the OS can crash either tier (the
// JIT's frames are far smaller), so the guard, never the operating system,
// decides where recursion ends, identically in both. An earlier draft used
// 8192 from an unchecked estimate; 8192 is ABOVE the interpreter's real limit
// and would have let it segfault before firing.
namespace lithon::jit {

// Every Lithon function prologue bumps this; a value > kMaxCallDepth is
// E0401. Reset to 0 before each program run by the entry-point drivers.
inline constexpr int64_t kMaxCallDepth = 4096;

// Process-wide call depth. inline variable: one object shared by every TU,
// and one stable address for emitted code to reference. Single-threaded by
// design -- Lithon has no threads (roadmap Phase 5), matching every other
// piece of shared JIT state (host_format_double's buffer, etc.).
inline int64_t g_call_depth = 0;

// Renders the E0401 message INCLUDING the depth that actually exceeded the
// guard. A static buffer is fine here: the process is single-threaded and
// the caller either prints or throws it immediately. The message has NO
// trailing newline: the interpreter's handler appends one ("LITHON-" lines
// are printed without the legacy "error: " tag) and the JIT's
// host_report_stack_overflow adds its own, so the final bytes are identical.
// The literal 4096 in the message and kMaxCallDepth must never drift apart;
// the tests pin both.
inline const char* format_stack_overflow_message(int64_t depth) {
    static char buffer[176];
    std::snprintf(
        buffer, sizeof(buffer),
        "LITHON-E0401: stack overflow -- call depth %lld exceeded the 4096-frame "
        "guard; rewrite the recursion as an explicit loop",
        static_cast<long long>(depth));
    return buffer;
}

// The JIT's trap handler: prints the message and exits -- never returns, like
// host_report_error, because the overflowing prologue has no recoverable
// continuation. The emitted prologue passes the ACTUAL depth in the first GP
// argument register.
[[noreturn]] inline void host_report_stack_overflow(int64_t depth) {
    std::fputs(format_stack_overflow_message(depth), stderr);
    std::fputc('\n', stderr);
    std::fflush(stderr);
    std::fflush(stdout);
    std::exit(1);
}

}  // namespace lithon::jit