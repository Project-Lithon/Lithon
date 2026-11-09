#pragma once

// Source positions for error reports. The IR may carry, per instruction, the source file index
// and line it came from (ir::Instr::src / line, `source N path` + ` @N:line` in the text). This
// header holds the process-wide state both tiers use to print "  --> path:line" under a runtime
// error, so the interpreter and the native code produce the same bytes.

#include <cstdint>
#include <string>
#include <vector>

namespace lithon::ir {

// The module's source table (set once after the IR is parsed); null when there is none.
inline std::vector<std::string> g_sources_store;
inline const std::vector<std::string>* g_sources = nullptr;
// A COPY, so it is still there when an error is reported after the module has gone out of scope.
inline void set_sources(const std::vector<std::string>& sources) {
    g_sources_store = sources;
    g_sources = &g_sources_store;
}

// Where the interpreter currently is: set before every instruction it executes.
struct SourcePos {
    int32_t src = -1;
    int32_t line = 0;
};
inline SourcePos g_cur_pos;

// A position packed into one 64-bit immediate for native code: 0 means "unknown".
inline int64_t pack_pos(int32_t src, int32_t line) {
    if (src < 0 || line <= 0) return 0;
    return (static_cast<int64_t>(src + 1) << 32) | static_cast<uint32_t>(line);
}
inline SourcePos unpack_pos(int64_t packed) {
    if (packed == 0) return {};
    return {static_cast<int32_t>((packed >> 32) - 1), static_cast<int32_t>(packed & 0xffffffff)};
}

// "  --> path:line\n", or "" when the position is unknown.
inline std::string describe_pos(int32_t src, int32_t line) {
    if (g_sources == nullptr || src < 0 || line <= 0 || static_cast<size_t>(src) >= g_sources->size() ||
        (*g_sources)[static_cast<size_t>(src)].empty())
        return "";
    return "  --> " + (*g_sources)[static_cast<size_t>(src)] + ":" + std::to_string(line) + "\n";
}

}  // namespace lithon::ir
