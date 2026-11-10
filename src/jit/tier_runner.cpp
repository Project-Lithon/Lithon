// tier_runner: run an IR file on the interpreter, the native JIT, or
// "auto" (native only when provably output-safe, else interpreter).
//
//   tier_runner <file.ir> [--interp | --native | --auto | --strict]   (default: --auto)
//
//   --interp  interpreter only (the correctness oracle)
//   --auto    native when print-safe, else interpreter fallback
//   --strict  native only. If the guard or the JIT refuses, exit 3 and run
//             nothing: no interpreter fallback ("slow paths never exist").
//   --native  force the JIT with the guard OFF. Unsafe: for testing only.
//
// Linux/x86-64 only (uses mmap/mprotect).
//
// Which tier actually ran is reported on stderr as "[tier0]" (interpreter)
// or "[tier1]" (native), so a test harness can compare stdout between
// modes and still know what executed. Program output goes to stdout only.
//
// Build:
//   g++ -std=c++20 -O2 -Isrc -Isrc/jit -o build/tier_runner
//       src/jit/tier_runner.cpp src/ir/text_parser.cpp
//       src/interpreter/interpreter.cpp src/typecheck/typecheck.cpp


#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include "backend.h"
#include "native_stack.h"
#include "stack_budget.h"
#include "compile_function.h"
#include "exec_memory.h"
#include "interpreter/interpreter.h"
#include "ir/text_parser.h"
#include "ir/source_loc.h"
#include "print_guard.h"
#include "runtime/heap.h"
#include "runtime/class_runtime.h"
#include "typecheck/typecheck.h"

namespace {

// 4.1 REMOVED: this used to skip the type checker entirely for a module with no
// annotations at all, which made adding an annotation a whole-module semantic
// switch -- annotate one variable and every unannotated variable in the file
// became an error. It also meant most of the regression corpus was never
// checked at all: 14 of 18 programs in tests/programs/ had zero annotations and
// so were silently exempt. The checker is unconditional now.
void run_interpreter(const lithon::ir::Module& m) {
    std::fputs("[tier0] interpreter\n", stderr);
    lithon::interp::run_main(m);
    std::cout.flush();
}

// Returns false (and prints why) if native compilation failed, so the
// caller can fall back. Nothing has executed when this returns false.
bool run_native(const lithon::ir::Module& m, lithon::jit::CompileOptions options = {}) {
    lithon::jit::CompiledModule compiled;
    try {
        compiled = lithon::jit::compile_module_for_host(m, options);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "[tier0] native compile refused: %s\n", e.what());
        return false;
    }

    auto main_it = compiled.function_offset.find("__main__");
    if (main_it == compiled.function_offset.end())
        main_it = compiled.function_offset.find("main");
    if (main_it == compiled.function_offset.end()) {
        std::fputs("[tier0] native compile refused: no __main__/main entry\n", stderr);
        return false;
    }

    // The most stack this program can possibly use, from its call graph and the frame sizes the code
    // generator just chose (recursion is bounded by the E0401 depth guard). It either fits what we
    // are willing to reserve -- and then the program gets exactly that much stack and cannot
    // overflow -- or it is refused here, before anything has run.
    const int64_t stack_needed =
        lithon::jit::worst_case_stack(m, compiled.frame_bytes) + lithon::jit::kHostStackMargin;
    if (stack_needed > lithon::jit::native_stack_limit()) {
        std::fprintf(stderr,
                     "[tier0] native compile refused: worst-case stack of %lld bytes exceeds the "
                     "%lld-byte native stack limit\n",
                     static_cast<long long>(stack_needed),
                     static_cast<long long>(lithon::jit::native_stack_limit()));
        return false;
    }

    // The platform's own executable-memory path (mmap/mprotect on POSIX,
    // VirtualAlloc/VirtualProtect on Windows), W^X either way.
    lithon::jit::ExecutableBuffer exec;
    try {
        exec.load(compiled.code.data(), compiled.code.size());
    } catch (const std::exception& e) {
        std::fprintf(stderr, "[tier0] native compile refused: %s\n", e.what());
        return false;
    }
    void* mem = exec.data();

    using Fn = void (*)();
    auto entry = reinterpret_cast<Fn>(static_cast<uint8_t*>(mem) + main_it->second);
    // E0401: a fresh run starts recursion at depth 0; __main__'s own prologue
    // bumps it to 1.
    lithon::jit::g_call_depth = 0;
    // 4.10.0: a fresh run is a fresh heap. The arena MAPPING persists (this is
    // a one-shot process, but the reset keeps a re-run honest); only the free
    // lists and the live/destructor counters reset.
    lithon::runtime::reset_heap();
    // 4.10.1: and a fresh class-descriptor table from the module's metadata, so
    // New allocates the right size and Release runs the right destructor. A
    // __del__ resolves to its emitted function inside the mapped code block
    // (the same convention the frontend and the interpreter use).
    {
        uint8_t* base = static_cast<uint8_t*>(mem);
        lithon::runtime::build_class_table(
            m.classes,
            [&](const std::string& cls) -> void (*)(void*) {
                // A destructor is called by the runtime as a C function; on x86-32 the
                // backend emits a "$c" thunk that adapts the C call to its own convention.
                auto d = compiled.function_offset.find("__del__" + cls + "$c");
                if (d == compiled.function_offset.end()) d = compiled.function_offset.find("__del__" + cls);
                if (d == compiled.function_offset.end()) return nullptr;
                return reinterpret_cast<void (*)(void*)>(base + d->second);
            },
            [&](const std::string& cls, int slot, const char* slot_name) -> void (*)(void) {
                const std::string key = "__virt__" + cls + "." +
                                        (slot_name != nullptr ? slot_name
                                                              : std::to_string(slot));
                auto it = compiled.function_offset.find(key);
                if (it == compiled.function_offset.end()) return nullptr;
                return reinterpret_cast<void (*)(void)>(base + it->second);
            });
    }
    // Run on this thread when the stack it already has is enough, otherwise on a thread made with a
    // stack of exactly the size needed (reserved, committed as the probes touch it). If such a
    // thread cannot be made nothing has run yet, so the caller falls back to the interpreter.
    const auto go = [&] {
        std::fputs("[tier1] native\n", stderr);
        entry();
    };
    if (stack_needed <= lithon::jit::kInlineNativeStack) {
        go();
    } else if (!lithon::jit::run_on_stack(stack_needed, go)) {
        std::fprintf(stderr, "[tier0] native compile refused: could not reserve a %lld-byte stack\n",
                     static_cast<long long>(stack_needed));
        return false;
    }
    std::fflush(stdout);   // native print() goes through libc stdio
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "usage: tier_runner <file.ir> [--interp|--native|--auto|--strict]\n"
                     "                  [--no-lsr] [--accum-unroll] [--unroll-diamonds] [--dump-hex]\n"
                     "                  [--ssa] [--no-vectorize] [--no-opt]\n"
                     "       --ffast-math-equivalent reassociates FLOAT addition, shortening the\n"
                     "       dependency chain. NOT bit-exact: IEEE754 addition is not associative,\n"
                     "       so results can differ from the interpreter's in the last bit. Off by\n"
                     "       default and never implied by another flag. Integers are untouched.\n"
                     "       --accum-unroll splits a counted reduction's accumulator into 4 partials;\n"
                     "       off by default (the win is ~1.6x on a long-latency float accumulator).\n"
                     "       A float accumulator is reassociated, so its value may differ from the\n"
                     "       interpreter's.\n"
                     "       --ssa compiles through the SSA pipeline (canonicalize loops, Mem2Reg,\n"
                     "       copy propagation + dead store elimination, phi resolution).\n";
        return 2;
    }
    const std::string path = argv[1];
    std::string mode = "--auto";
    bool dump_hex = false;
    lithon::jit::CompileOptions options;
    for (int i = 2; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--interp" || a == "--native" || a == "--auto" || a == "--strict") {
            mode = a;
        } else if (a == "--no-lsr") {
            options.strength_reduce = false;
        } else if (a == "--accum-unroll") {
            options.accum_unroll = 4;
        } else if (a == "--no-accum-unroll") {
            options.accum_unroll = 1;
        } else if (a == "--ffast-math-equivalent") {
            options.ffast_math_equivalent = true;
        } else if (a == "--unroll-diamonds") {
            options.unroll_diamonds = true;
        } else if (a == "--dump-hex") {
            dump_hex = true;
        } else if (a == "--ssa") {
            options.ssa_pipeline = true;
        } else if (a == "--no-opt") {
            // Disable the optimizer, which also disables its IR-level
            // self-tail-call elimination, so the codegen-level tail-call
            // elimination is the one that runs.
            options.optimize = false;
        } else if (a == "--no-vectorize") {
            options.vectorize = false;
        } else if (a == "--direct-phis") {
            // 2.7. Implies --ssa, for the same reason: only the SSA pipeline
            // materialises a Phi.
            options.direct_phis = true;
            options.ssa_pipeline = true;
        } else {
            std::cerr << "unknown argument: " << a << "\n";
            return 2;
        }
    }

    std::ifstream file(path);
    if (!file) { std::cerr << "error: cannot open " << path << "\n"; return 2; }
    std::stringstream buf;
    buf << file.rdbuf();

    try {
        auto module = lithon::ir::parse_ir_text(buf.str());
        lithon::ir::set_sources(module.sources);

        {
            auto errors = lithon::typecheck::check_module(module);
            if (!errors.empty()) {
                for (const auto& e : errors) {
                    // LITHON-Exxxx messages are self-identifying; prose-only
                    // messages keep the legacy "RCR error:" tag.
                    std::cerr << lithon::typecheck::format_error(e, module);
                }
                return 1;
            }
        }

        if (mode == "--interp") { run_interpreter(module); return 0; }

        // --dump-hex prints the compiled machine code instead of running it,
        // which is the only way to show WHICH encoding was chosen for a given
        // source form. A literal count has to come out as C1/D1 (shift by an
        // immediate, never touching CL) and a dynamic count as D3 + a CL
        // load, and neither is visible from the source or from the output.
        if (dump_hex) {
            auto cm = lithon::jit::compile_module_for_host(module, options);
            for (size_t i = 0; i < cm.code.size(); ++i) {
                std::fprintf(stderr, "%02x", cm.code[i]);
                std::fputc((i % 16 == 15) ? '\n' : ' ', stderr);
            }
            if (cm.code.size() % 16 != 0) std::fputc('\n', stderr);
            // Loop-transform accounting (see CompiledModule): a loop carrying
            // break/continue has more than one exit, so assertion tools can
            // require all three to be 0 here without disassembling the dump.
            std::fprintf(stderr, "[stats] unrolled=%zu diamonds=%zu vectorized=%zu rotated=%zu\n",
                         cm.loops_unrolled, cm.diamonds_unrolled,
                         cm.loops_vectorized, cm.loops_rotated);
            return 0;
        }

        if (mode == "--auto" || mode == "--strict") {
            auto verdict = lithon::jit::check_print_safety(module);
            if (!verdict.native_safe) {
                for (const auto& r : verdict.reasons)
                    std::fprintf(stderr, "[guard] %s\n", r.c_str());
                if (mode == "--strict") {
                    std::fputs("[strict] refused: cannot prove native output is safe\n",
                               stderr);
                    return 3;
                }
                run_interpreter(module);
                return 0;
            }
        }

        // --native forces the JIT with no guard (used to demonstrate the divergence)
        if (!run_native(module, options)) {
            if (mode == "--strict") {
                std::fputs("[strict] refused: native compilation failed\n", stderr);
                return 3;
            }
            if (mode == "--native") return 1;
            run_interpreter(module);
        }
    } catch (const std::exception& e) {
        // LITHON-Exxxx messages (E0303 etc.) are self-identifying; prose-only
        // runtime messages keep the legacy "error: " prefix.
        const std::string m = e.what();
        std::cerr << (m.rfind("LITHON-", 0) == 0 ? "" : "error: ") << m << "\n";
        // Where it happened, when the IR carries positions. The native tier prints the same line
        // from its trap site; a stack overflow has no single site, so neither tier prints one.
        if (m.rfind("LITHON-E0401", 0) != 0)
            std::cerr << lithon::ir::describe_pos(lithon::ir::g_cur_pos.src, lithon::ir::g_cur_pos.line);
        return 1;
    }
    return 0;
}
