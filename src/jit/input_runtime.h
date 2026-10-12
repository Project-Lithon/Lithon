#pragma once

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <string>

// Host-side support for input() and the float -> int conversion trap.
//
// Two things live here, both because the interpreter and the JIT must agree
// byte-for-byte on the resulting stdout/stderr (run_tier_diff.py diffs the two
// engines, and an input() program's prompt and error text are part of that):
//
//   read_int_line  -- the single implementation of "prompt, read a line,
//                     parse it as int[width]", shared by the interpreter
//                     (which throws the returned message) and the JIT helper
//                     (which prints it and exits). Keeping the parse in one
//                     place is what makes the two tiers decide EOF / bad
//                     literal / out-of-width identically.
//   host_input_int -- the JIT's entry point: the emitted code passes a prompt
//                     pointer (from the string pool) and the target width.
//
// The messages follow the LITHON-Exxxx convention (docs/lithon_error_system.md
// section 4c): the interpreter's handler prints a LITHON- line WITHOUT the
// legacy "error: " tag, so the JIT's copy must carry no tag either. The
// constants here have no trailing newline; every printer adds exactly one.

namespace lithon::jit {

// Section 4c: 3 = dynamic runtime trap. E0306/7/8 are input-specific; E0309 is
// the float -> int conversion trap (NaN, infinity, or out of the target width).
inline constexpr const char kE0306Message[] =
    "LITHON-E0306: input() expected an integer literal";
inline constexpr const char kE0307Message[] =
    "LITHON-E0307: input value does not fit in the target int width";
inline constexpr const char kE0308Message[] =
    "LITHON-E0308: input() reached end of input";
inline constexpr const char kE0309Message[] =
    "LITHON-E0309: float-to-int conversion is out of range or not finite";

// Prints `message` followed by a newline, flushes, and exits. The JIT twin of
// stack_guard.h's host_report_stack_overflow, used for the conversion trap.
[[noreturn]] inline void host_report_message_nl(const char* message) {
    std::fputs(message, stderr);
    std::fputc('\n', stderr);
    std::fflush(stderr);
    std::fflush(stdout);
    std::exit(1);
}

// Prints the prompt (if any) and reads one line from stdin, then parses it as a
// base-10 signed integer that must fit `width` bits. Returns true and stores the
// value in *out on success; on failure returns false and points *err at one of
// the message constants above. It never exits -- the caller chooses (the
// interpreter throws, host_input_int exits), so both routes share this logic.
//
// The grammar is deliberately small: optional surrounding spaces, an optional
// sign, and decimal digits. Python's int() is looser (underscores, unicode
// digits, surrounding parentheses are NOT accepted either), but the narrow set
// keeps the two tiers' acceptance identical by construction and matches the
// docs' "invalid integer literal" phrasing.
inline bool read_int_line(const char* prompt, int width, int64_t* out,
                          const char** err) {
    if (prompt != nullptr && *prompt != '\0') {
        std::fputs(prompt, stdout);
        std::fflush(stdout);
    }

    char buf[256];
    if (std::fgets(buf, sizeof(buf), stdin) == nullptr) {
        *err = kE0308Message;
        return false;
    }

    // Trim the line terminator and any surrounding spaces/tabs.
    size_t n = std::strlen(buf);
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r' ||
                     buf[n - 1] == ' ' || buf[n - 1] == '\t')) {
        buf[--n] = '\0';
    }
    const char* p = buf;
    while (*p == ' ' || *p == '\t') ++p;

    const char* digits = p;
    if (*digits == '+' || *digits == '-') ++digits;
    if (*digits == '\0') { *err = kE0306Message; return false; }
    for (const char* d = digits; *d != '\0'; ++d) {
        if (*d < '0' || *d > '9') { *err = kE0306Message; return false; }
    }

    errno = 0;
    char* end = nullptr;
    const long long v = std::strtoll(p, &end, 10);
    if (errno == ERANGE) { *err = kE0307Message; return false; }
    if (width > 0 && width < 64) {
        const long long lo = -(1LL << (width - 1));
        const long long hi = (1LL << (width - 1)) - 1;
        if (v < lo || v > hi) { *err = kE0307Message; return false; }
    }
    *out = static_cast<int64_t>(v);
    return true;
}

// The JIT entry point. On failure it prints the message and exits, matching the
// interpreter's throw-then-report path.
inline int64_t host_input_int(const char* prompt, int64_t width) {
    int64_t value = 0;
    const char* err = nullptr;
    if (!read_int_line(prompt, static_cast<int>(width), &value, &err)) {
        host_report_message_nl(err);
    }
    return value;
}

}  // namespace lithon::jit
