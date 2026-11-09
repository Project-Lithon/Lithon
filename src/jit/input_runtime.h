#pragma once

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <cstdint>
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
inline constexpr const char kE0310Message[] =
    "LITHON-E0310: range step is zero";
// 4.12. A line read for `str(n, input(...))` that does not fit: longer than n-1
// bytes. The bytes are never truncated; the program stops (or the enclosing
// try/except catches it as a ValueError).
inline constexpr const char kE0311Message[] =
    "LITHON-E0311: input line is longer than the string it is read into";
// 4.12. A NUL byte inside a line read for `str(n, input(...))`. A str[N] is
// NUL-terminated, so the bytes after one could never be seen; the line is
// refused rather than silently shortened.
// 4.14. An unsigned integer operation whose exact result is outside
// 0..2^w-1: a sum past the maximum, a difference below zero, a product or shift
// that does not fit. Never wraps; catchable as an OverflowError.
inline constexpr const char kE0313Message[] =
    "LITHON-E0313: unsigned integer result is out of range for its uint width";
inline constexpr const char kE0312Message[] =
    "LITHON-E0312: input line contains a NUL byte, which a string cannot hold";

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

// 4.12. Prints the prompt (if any), reads ONE whole line from stdin and stores
// its bytes in *out, with the line terminator removed ("\n", and a "\r" before
// it). Unlike read_int_line this does not trim spaces -- a string keeps its
// content -- and it has no fixed-size buffer: a line of any length is read in
// full, one byte at a time, so a too-long line is detected by its true length
// and a NUL byte is SEEN (a C-string reader such as fgets would stop at it and
// hide everything after). `limit` is the literal n of `str(n, input(...))` and
// counts the terminating NUL, so at most n-1 bytes fit. Returns true on success;
// on failure returns false with *err pointing at kE0308Message (end of input
// before any byte), kE0312Message (a NUL byte in the line) or kE0311Message (too
// long). A final line with no newline at end of file is still a line, as in
// Python. A NUL is reported before length: the content is invalid at any limit.
inline bool read_str_line(const char* prompt, int64_t limit, std::string* out,
                          const char** err) {
    if (prompt != nullptr && *prompt != '\0') {
        std::fputs(prompt, stdout);
        std::fflush(stdout);
    }
    std::string line;
    bool got_any = false;
    for (int c = std::fgetc(stdin); c != EOF; c = std::fgetc(stdin)) {
        got_any = true;
        if (c == '\n') break;
        line.push_back(static_cast<char>(c));
    }
    if (!got_any) { *err = kE0308Message; return false; }
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line.find('\0') != std::string::npos) { *err = kE0312Message; return false; }
    if (static_cast<int64_t>(line.size()) > limit - 1) {
        *err = kE0311Message;
        return false;
    }
    *out = std::move(line);
    return true;
}

// Writes `text` into a NUL-terminated byte run of `cap` bytes and zeroes the
// rest, the state `s: str[cap] = "literal"` leaves. The caller has already
// proved text.size() <= cap - 1.
inline void fill_str_run(unsigned char* run, int64_t cap, const std::string& text) {
    for (int64_t j = 0; j < cap; ++j)
        run[j] = j < static_cast<int64_t>(text.size())
                     ? static_cast<unsigned char>(text[static_cast<size_t>(j)]) : 0;
}

// The JIT's frame descriptor for InputStr, registered once per call site and
// handed to the helper by address (the same convention as the str find/split
// helpers: the run is addressed from the frame base).
struct StrInputDesc {
    int64_t dest_offset;   // frame offset of the destination run's first byte
    int64_t cap;           // N, the run's declared capacity
    int64_t limit;         // n, the literal limit (<= N, counts the NUL)
};

// Reads a line into the destination run. Returns a KIND code, not a value, for
// the reason host_input_int_ex does: 0 success, 1 too long (E0311 -> ValueError),
// 2 NUL byte in the line (E0312 -> ValueError), 3 end of input (E0308 ->
// EOFError). The caller reports or routes the trap, so a
// try/except around the read behaves exactly as it does in the interpreter.
inline int64_t host_input_str(const void* frame_base, const StrInputDesc* d,
                              const char* prompt) {
    std::string text;
    const char* err = nullptr;
    if (!read_str_line(prompt, d->limit, &text, &err)) {
        return err == kE0311Message ? 1 : err == kE0312Message ? 2 : 3;
    }
    fill_str_run(const_cast<unsigned char*>(
                     static_cast<const unsigned char*>(frame_base)) + d->dest_offset,
                 d->cap, text);
    return 0;
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

// 6.10. The try/except form. Same read and parse, but it does NOT exit on
// failure. It returns a KIND code, not the value:
//
//   0 -> read succeeded; the parsed value is in *value_out
//   1 -> kE0306Message -> ValueError
//   2 -> kE0307Message -> OverflowError
//   3 -> kE0308Message -> EOFError
//
// The kind and not the value, because a read of the literal `0` is a SUCCESS
// that happens to return zero -- returning the value would make it
// indistinguishable from the failure sentinel. A code and not the message
// pointer, because comparing pointers to string constants the JIT would have to
// know the address of is brittle; the caller maps the code back to the message
// constant when it has to report instead of catch.
inline int64_t host_input_int_ex(const char* prompt, int64_t width,
                                 int64_t* value_out) {
    int64_t value = 0;
    const char* err = nullptr;
    if (!read_int_line(prompt, static_cast<int>(width), &value, &err)) {
        *value_out = 0;
        return err == kE0306Message ? 1 : err == kE0307Message ? 2 : 3;
    }
    *value_out = value;
    return 0;
}

}  // namespace lithon::jit
