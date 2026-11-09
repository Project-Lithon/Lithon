#pragma once

// Picks the native backend that matches the host process. The generated code
// embeds absolute host addresses (helpers, strings, the call-depth counter) and runs
// in-process, so the backend must match the pointer size of the process it is built
// into: x86-64 for a 64-bit host, x86-32 for a 32-bit one.

#include "compile_function.h"
#include "x86_32_backend.h"

namespace lithon::jit {

inline CompiledModule compile_module_for_host(const lithon::ir::Module& module,
                                              const CompileOptions& options = CompileOptions{}) {
    if constexpr (sizeof(void*) == 4) {
        (void)options;
        return x86_32::compile_module(module);
    } else {
        return compile_module(module, options);
    }
}

}  // namespace lithon::jit
