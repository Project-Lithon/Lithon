// Phase III: the raw syscall primitive, tested through the one seam that both
// engines share.
//
// Why a hook instead of a real syscall: a real syscall's result depends on the
// host (getuid differs per user, a write lands in a real terminal), so it
// cannot pin an argument layout. `g_syscall_hook` is consulted first on every
// platform (see jit/syscall_runtime.h), so installing one turns the whole
// primitive into a pure function of its inputs -- and, more to the point, this
// is the test that makes the central claim of the design checkable:
//
//     the interpreter and the JIT reach host_syscall with the SAME number and
//     the SAME six argument slots in the SAME order.
//
// That claim is what keeps the two tiers from drifting, and it is exactly what
// would break if the JIT's stack staging pushed the slots in the wrong order or
// passed a pointer to the wrong end. So each case runs the SAME program twice,
// through the interpreter and through the JIT, and requires the hook to observe
// byte-identical arguments both times, with the program's printed value equal
// to what the hook computed.
//
// What this does not cover: the inline-asm path (reached only with no hook
// installed) and the non-Linux fallback. Those are covered elsewhere -- the asm
// by a real pread64 that proves argument 4 lands in R10, and -ENOSYS by
// run_tier_diff.py's syscall cases.

#include "ir/ir.h"
#include "ir/text_parser.h"
#include "interpreter/interpreter.h"
#include "jit/compile_function.h"
#include "jit/syscall_runtime.h"
#include "typecheck/typecheck.h"

#include <cstdio>
#include <cstring>
#include <string>
#include <sys/mman.h>
#include <unistd.h>

namespace {

int failures = 0;

// What the hook last saw, so the test can assert on the arguments themselves
// and not only on the value the program printed.
struct Seen {
    int64_t number = 0;
    int64_t args[6] = {0, 0, 0, 0, 0, 0};
    bool called = false;
};
Seen g_seen;

// number + sum(args[i] * (i + 1)): every slot contributes with a distinct
// weight, so a reordered, duplicated or dropped argument changes the sum.
int64_t fake_syscall(int64_t number, const int64_t* args) {
    g_seen.called = true;
    g_seen.number = number;
    int64_t s = number;
    for (int i = 0; i < 6; ++i) {
        g_seen.args[i] = args[i];
        s += args[i] * (i + 1);
    }
    return s;
}

// stdout capture. Both engines print through stdio/std::cout, and std::cout is
// synced with stdio by default, so redirecting the FILE* catches both.
class Capture {
public:
    Capture() {
        char name[256];
        std::snprintf(name, sizeof name, "/tmp/lithon_syscall_hook_test.%d.out",
                      static_cast<int>(getpid()));
        path_ = name;
        std::fflush(stdout);
        std::fflush(stderr);
        saved_ = dup(fileno(stdout));
        if (std::freopen(path_.c_str(), "w", stdout) == nullptr) {
            std::printf("freopen failed\n");
            ++failures;
        }
    }
    std::string finish() {
        std::fflush(stdout);
        dup2(saved_, fileno(stdout));
        close(saved_);
        std::string out;
        FILE* f = std::fopen(path_.c_str(), "r");
        if (f) {
            char buf[512];
            while (std::fgets(buf, sizeof buf, f)) out += buf;
            std::fclose(f);
            std::remove(path_.c_str());
        }
        return out;
    }

private:
    std::string path_;
    int saved_ = -1;
};

lithon::ir::Module parse(const std::string& text) {
    lithon::ir::Module m = lithon::ir::parse_ir_text(text);
    auto errors = lithon::typecheck::check_module(m);
    if (!errors.empty()) {
        std::printf("    parse/typecheck failed: %s\n", errors[0].message.c_str());
        ++failures;
    }
    return m;
}

// Compiles and runs the entry point natively, exactly as tier_runner does.
void run_native(const lithon::ir::Module& m) {
    lithon::jit::CompiledModule compiled;
    try {
        compiled = lithon::jit::compile_module(m, {});
    } catch (const std::exception& e) {
        std::printf("    native compile refused: %s\n", e.what());
        ++failures;
        return;
    }
    auto it = compiled.function_offset.find("__main__");
    if (it == compiled.function_offset.end()) it = compiled.function_offset.find("main");
    if (it == compiled.function_offset.end()) {
        std::printf("    no entry point\n");
        ++failures;
        return;
    }
    void* mem = mmap(nullptr, compiled.code.size(), PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) { std::printf("    mmap failed\n"); ++failures; return; }
    std::memcpy(mem, compiled.code.data(), compiled.code.size());
    if (mprotect(mem, compiled.code.size(), PROT_READ | PROT_EXEC) != 0) {
        std::printf("    mprotect failed\n");
        ++failures;
        munmap(mem, compiled.code.size());
        return;
    }
    using Fn = void (*)();
    auto entry = reinterpret_cast<Fn>(static_cast<uint8_t*>(mem) + it->second);
    lithon::jit::g_call_depth = 0;
    entry();
    std::fflush(stdout);
    munmap(mem, compiled.code.size());
}

// Runs `text` through both engines with the hook installed and requires that
// the hook saw the same number and the same six slots both times, that those
// are the expected values, and that both tiers printed the hook's result.
void check_both_tiers(const char* what, const std::string& text,
                      int64_t want_number, const int64_t (&want_args)[6],
                      const char* want_output) {
    std::printf("  %s\n", what);

    Seen first, second;
    std::string interp_out, native_out;
    lithon::jit::g_syscall_hook = fake_syscall;

    g_seen = Seen{};
    {
        Capture cap;
        lithon::interp::run_main(parse(text));
        interp_out = cap.finish();
    }
    first = g_seen;

    g_seen = Seen{};
    {
        Capture cap;
        run_native(parse(text));
        native_out = cap.finish();
    }
    second = g_seen;

    lithon::jit::g_syscall_hook = nullptr;

    if (!first.called || !second.called) {
        std::printf("    FAIL: hook not reached (interp=%d native=%d)\n",
                    first.called, second.called);
        ++failures;
        return;
    }
    // The load-bearing assertion: identical arguments observed by both engines.
    if (first.number != second.number ||
        std::memcmp(first.args, second.args, sizeof first.args) != 0) {
        std::printf("    FAIL: engines disagreed on the arguments\n");
        std::printf("      interp saw number=%lld args=[%lld %lld %lld %lld %lld %lld]\n",
                    (long long)first.number, (long long)first.args[0], (long long)first.args[1],
                    (long long)first.args[2], (long long)first.args[3], (long long)first.args[4],
                    (long long)first.args[5]);
        std::printf("      native saw number=%lld args=[%lld %lld %lld %lld %lld %lld]\n",
                    (long long)second.number, (long long)second.args[0], (long long)second.args[1],
                    (long long)second.args[2], (long long)second.args[3], (long long)second.args[4],
                    (long long)second.args[5]);
        ++failures;
        return;
    }
    if (first.number != want_number) {
        std::printf("    FAIL: number %lld, want %lld\n",
                    (long long)first.number, (long long)want_number);
        ++failures;
        return;
    }
    for (int i = 0; i < 6; ++i) {
        if (first.args[i] != want_args[i]) {
            std::printf("    FAIL: arg%d %lld, want %lld\n", i + 1,
                        (long long)first.args[i], (long long)want_args[i]);
            ++failures;
            return;
        }
    }
    if (interp_out != want_output || native_out != want_output) {
        std::printf("    FAIL: output interp=%s native=%s want=%s",
                    interp_out.c_str(), native_out.c_str(), want_output);
        ++failures;
        return;
    }
    std::printf("    ok: number=%lld args=[%lld %lld %lld %lld %lld %lld], "
                "printed \"%s\", identical in both tiers\n",
                (long long)first.number, (long long)first.args[0], (long long)first.args[1],
                (long long)first.args[2], (long long)first.args[3], (long long)first.args[4],
                (long long)first.args[5], want_output);
}

}  // namespace

int main() {
    std::printf("syscall_hook_test\n");

    // Six arguments, all distinct, so any reordering is visible.
    check_both_tiers("all six slots, distinct values", R"(
function __main__():
block0:
    %0 = const_i64 39
    %1 = const_i64 1
    %2 = const_i64 2
    %3 = const_i64 3
    %4 = const_i64 4
    %5 = const_i64 5
    %6 = const_i64 6
    %7 = syscall %0, %1, %2, %3, %4, %5, %6
    call print, %7
    return
)", 39, {1, 2, 3, 4, 5, 6}, "130\n");

    // Fewer arguments than slots: the omitted ones must arrive as ZERO, not as
    // leftover stack garbage. This is the case that would silently produce
    // host-dependent results if the JIT staged uninitialised memory.
    check_both_tiers("omitted trailing slots are zero", R"(
function __main__():
block0:
    %0 = const_i64 39
    %1 = const_i64 11
    %2 = syscall %0, %1
    call print, %2
    return
)", 39, {11, 0, 0, 0, 0, 0}, "50\n");

    // No arguments at all.
    check_both_tiers("number only", R"(
function __main__():
block0:
    %0 = const_i64 39
    %1 = syscall %0
    call print, %1
    return
)", 39, {0, 0, 0, 0, 0, 0}, "39\n");

    // Through a real call boundary and inside a loop, so the result is a live
    // value the allocator had to keep across a call: the op must be treated as
    // a clobbering boundary there too. The loop runs i = 0 then i = 1, so the
    // hook runs twice with number 0 then number 1 and acc = 1 + 0 + 1 = 2. The
    // view compared here is the LAST call: number 1, all six slots zero.
    // (This IR is what src/frontend/frontend.py emits for the source in the
    // comment below -- branch targets are block labels, not value ids.)
    check_both_tiers("across a call, in a loop", R"(
function probe(n:int[64]) -> int[64]:
block0:
    %0 = load n
    %1 = syscall %0
    store r, %1 : int[64]
    %2 = load r
    return %2

function __main__():
block0:
    %0 = const_i64 1
    store acc, %0 : int[64]
    %1 = const_i64 0
    store i, %1 : int[64]
    jump block1
block1:
    %2 = load i
    %3 = const_i64 2
    %4 = lt %2, %3
    branch %4, block2, block3
block2:
    %5 = load i
    %6 = call probe, %5
    store v, %6 : int[64]
    %7 = load acc
    %8 = load v
    %9 = add %7, %8
    store acc, %9 : int[64]
    %10 = load i
    %11 = load i
    %12 = const_i64 1
    %13 = add %11, %12
    store i, %13 : int[64]
    jump block1
block3:
    %14 = load acc
    call print, %14
    return
)", 1, {0, 0, 0, 0, 0, 0}, "2\n");

    if (failures) {
        std::printf("\n%d check(s) FAILED\n", failures);
        return 1;
    }
    std::printf("\nall checks passed\n");
    return 0;
}
