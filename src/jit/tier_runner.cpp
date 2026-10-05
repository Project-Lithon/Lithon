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

#include <sys/mman.h>

#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include "compile_function.h"
#include "interpreter/interpreter.h"
#include "ir/text_parser.h"
#include "print_guard.h"
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
        compiled = lithon::jit::compile_module(m, options);
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

    void* mem = mmap(nullptr, compiled.code.size(), PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) { std::perror("mmap"); return false; }
    std::memcpy(mem, compiled.code.data(), compiled.code.size());
    if (mprotect(mem, compiled.code.size(), PROT_READ | PROT_EXEC) != 0) {
        std::perror("mprotect");
        munmap(mem, compiled.code.size());
        return false;
    }

    std::fputs("[tier1] native\n", stderr);
    using Fn = void (*)();
    auto entry = reinterpret_cast<Fn>(static_cast<uint8_t*>(mem) + main_it->second);
    entry();
    std::fflush(stdout);   // native print() goes through libc stdio
    munmap(mem, compiled.code.size());
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "usage: tier_runner <file.ir> [--interp|--native|--auto|--strict]\n"
                     "                  [--no-lsr] [--accum-unroll] [--unroll-diamonds] [--dump-hex]\n"
                     "                  [--ssa]\n"
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

        {
            auto errors = lithon::typecheck::check_module(module);
            if (!errors.empty()) {
                for (const auto& e : errors) std::cerr << "RCR error: " << e.message << "\n";
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
            auto cm = lithon::jit::compile_module(module, options);
            for (size_t i = 0; i < cm.code.size(); ++i) {
                std::fprintf(stderr, "%02x", cm.code[i]);
                std::fputc((i % 16 == 15) ? '\n' : ' ', stderr);
            }
            if (cm.code.size() % 16 != 0) std::fputc('\n', stderr);
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
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }
    return 0;
}
