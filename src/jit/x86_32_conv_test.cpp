// 4.17: calls Lithon functions from C through the three exported conventions.
//   x86_32_conv_test <module.ir>
// The module is compiled with export_thunks, loaded, and every `<fn>$cdecl`,
// `<fn>$stdcall` and `<fn>$fastcall` entry is called through a function pointer with
// the matching calling-convention attribute. A wrong argument slot, a wrong pop count
// (callee vs caller cleanup) or a wrong register would corrupt the caller's stack or
// the result, so each call is repeated in a loop and a stack canary is checked.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>

#include "exec_memory.h"
#include "ir/text_parser.h"
#include "x86_32_backend.h"

#define CC(name) __attribute__((name))
using lithon::jit::ExecutableBuffer;

static int failures = 0;
#define CHECK(cond, what) do { if (!(cond)) { std::printf("FAIL %s\n", what); ++failures; } } while (0)
// ESP sampled before and after a call. For every convention the net effect of the call
// sequence (argument pushes, the call, the caller's cleanup if the convention has one)
// must be zero; a thunk that pops too much or too little shows up here directly, instead
// of being absorbed by the optimiser re-deriving ESP. The harness is built at -O0 so the
// two samples stay on either side of the call.
static inline uintptr_t esp_now() { uintptr_t v; __asm__ volatile("mov %%esp, %0" : "=r"(v)); return v; }
#define CALLCHK(label, ok_expr_using_r, call_expr) do { \
    const uintptr_t before_ = esp_now(); \
    const auto r = (call_expr); \
    const uintptr_t after_ = esp_now(); \
    CHECK(after_ == before_, label " (stack not balanced)"); \
    CHECK((ok_expr_using_r), label); ++checked; } while (0)

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    std::ifstream f(argv[1]);
    std::stringstream ss;
    ss << f.rdbuf();
    lithon::ir::Module m = lithon::ir::parse_ir_text(ss.str());
    lithon::jit::CompiledModule cm = lithon::jit::x86_32::compile_module(m, /*export_thunks=*/true);
    ExecutableBuffer buf;
    buf.load(cm.code.data(), cm.code.size());
    auto addr = [&](const char* name) -> void* {
        auto it = cm.function_offset.find(name);
        return it == cm.function_offset.end() ? nullptr : static_cast<void*>(buf.data() + it->second);
    };
    volatile uint32_t canary = 0xC0FFEE42u;
    int checked = 0;

    for (int rep = 0; rep < 2000; ++rep) {
        const int64_t a = rep * 3 - 1000, b = rep % 17, c = 5 - rep;
        // ---- add3 ----
        const int64_t want3 = a * 100 + b * 10 + c;
        if (auto p = reinterpret_cast<int64_t (CC(cdecl) *)(int64_t, int64_t, int64_t)>(addr("add3$cdecl"))) { CALLCHK("add3 cdecl", r == want3, p(a, b, c)); }
        if (auto p = reinterpret_cast<int64_t (CC(stdcall) *)(int64_t, int64_t, int64_t)>(addr("add3$stdcall"))) { CALLCHK("add3 stdcall", r == want3, p(a, b, c)); }
        if (auto p = reinterpret_cast<int64_t (CC(fastcall) *)(int64_t, int64_t, int64_t)>(addr("add3$fastcall"))) { CHECK(false, "add3 has int[64] params: no fastcall thunk expected"); (void)p; }
        // ---- five (more than two parameters, all on the stack) ----
        const int64_t d = rep * 7 - 3, e = -rep;
        const int64_t want5 = a + b * 2 + c * 3 + d * 4 + e * 5;
        if (auto p = reinterpret_cast<int64_t (CC(cdecl) *)(int64_t, int64_t, int64_t, int64_t, int64_t)>(addr("five$cdecl"))) { CALLCHK("five cdecl", r == want5, p(a, b, c, d, e)); }
        if (auto p = reinterpret_cast<int64_t (CC(stdcall) *)(int64_t, int64_t, int64_t, int64_t, int64_t)>(addr("five$stdcall"))) { CALLCHK("five stdcall", r == want5, p(a, b, c, d, e)); }
        // ---- floats: arguments are doubles in 8-byte slots, the result is in ST0 ----
        const double x = rep * 0.25 - 100.0;
        const double wantm = x * static_cast<double>(a) + static_cast<double>(b);
        if (auto p = reinterpret_cast<double (CC(cdecl) *)(int64_t, double, int64_t)>(addr("mix$cdecl"))) { CALLCHK("mix cdecl", r == wantm, p(a, x, b)); }
        if (auto p = reinterpret_cast<double (CC(stdcall) *)(int64_t, double, int64_t)>(addr("mix$stdcall"))) { CALLCHK("mix stdcall", r == wantm, p(a, x, b)); }
        if (auto p = reinterpret_cast<double (CC(cdecl) *)(double)>(addr("half$cdecl"))) { CALLCHK("half cdecl", r == x / 2.0, p(x)); }
        if (auto p = reinterpret_cast<double (CC(stdcall) *)(double)>(addr("half$stdcall"))) { CALLCHK("half stdcall", r == x / 2.0, p(x)); }
        // ---- fastcall: the first two int/bool parameters travel in ECX and EDX ----
        const bool flag = (rep & 1) != 0;
        const int32_t pa = static_cast<int32_t>(rep * 1000 - 999999), pb = static_cast<int32_t>(-rep * 31);
        const int32_t wantp = flag ? pa : pb;
        if (auto p = reinterpret_cast<int32_t (CC(fastcall) *)(bool, int32_t, int64_t)>(addr("pick$fastcall"))) { CALLCHK("pick fastcall", r == wantp, p(flag, pa, pb)); }
        if (auto p = reinterpret_cast<int32_t (CC(stdcall) *)(int64_t, int64_t, int64_t)>(addr("pick$stdcall"))) { CALLCHK("pick stdcall", r == wantp, p(flag ? 1 : 0, pa, pb)); }
        if (auto p = reinterpret_cast<int64_t (CC(fastcall) *)(int32_t)>(addr("neg$fastcall"))) { CALLCHK("neg fastcall", r == static_cast<int64_t>(pa) - 1000000, p(pa)); }
        if (auto p = reinterpret_cast<int64_t (CC(cdecl) *)()>(addr("nothing$cdecl"))) { CALLCHK("nothing cdecl", r == 42, p()); }
        if (auto p = reinterpret_cast<int64_t (CC(stdcall) *)()>(addr("nothing$stdcall"))) { CALLCHK("nothing stdcall", r == 42, p()); }
        if (auto p = reinterpret_cast<int64_t (CC(fastcall) *)()>(addr("nothing$fastcall"))) { CALLCHK("nothing fastcall", r == 42, p()); }
        if (auto p = reinterpret_cast<bool (CC(cdecl) *)(int64_t)>(addr("isbig$cdecl"))) { CALLCHK("isbig cdecl", r == (a * 100000000 > 4294967296LL), p(a * 100000000)); }
        if (addr("isbig$fastcall") != nullptr) { CHECK(false, "isbig has an int[64] parameter: no fastcall thunk expected"); }
        if (failures) break;
    }
    CHECK(canary == 0xC0FFEE42u, "stack canary");
    std::printf("%s: %d calls checked, %d failures\n", failures ? "FAILED" : "ok", checked, failures);
    return failures ? 1 : 0;
}
