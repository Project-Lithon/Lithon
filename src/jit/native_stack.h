#pragma once

// Runs a function on a stack of a chosen size.
//
// The thread that starts the program has the OS default (8 MiB on Linux, whatever the linker
// asked for on Windows). A program whose worst-case stack (stack_budget.h) is larger than that is
// run on a new thread created with a stack of exactly the size it needs -- the technique Rust
// uses for `thread::Builder::stack_size`, and what every C/C++ program that needs a deep or
// large stack does by hand. The stack is RESERVED, not committed: pages are only backed as the
// prologue's probes (and the code) touch them.

#include <cstdint>
#include <functional>

#if defined(_WIN32)
  #ifndef WIN32_LEAN_AND_MEAN
    #define WIN32_LEAN_AND_MEAN
  #endif
  #ifndef NOMINMAX
    #define NOMINMAX
  #endif
  #include <windows.h>
#else
  #include <pthread.h>
#endif

namespace lithon::jit {

#if defined(_WIN32)
namespace detail {
// CreateThread wants a WINAPI (stdcall on 32-bit Windows) function: a lambda would be cdecl.
inline DWORD WINAPI win_thread_main(LPVOID p) {
    (*static_cast<const std::function<void()>*>(p))();
    return 0;
}
}  // namespace detail
#endif

// Calls `fn` on a stack of at least `bytes`. Returns false, having run nothing, if such a thread
// could not be created (so the caller can still fall back to the interpreter).
inline bool run_on_stack(int64_t bytes, const std::function<void()>& fn) {
    // Round up to a whole number of 64 KiB (the Windows allocation granularity).
    const size_t size = static_cast<size_t>((bytes + 65535) / 65536) * 65536;
#if defined(_WIN32)
    HANDLE h = CreateThread(nullptr, size, &detail::win_thread_main,
                            const_cast<std::function<void()>*>(&fn),
                            STACK_SIZE_PARAM_IS_A_RESERVATION, nullptr);
    if (h == nullptr) return false;
    WaitForSingleObject(h, INFINITE);
    CloseHandle(h);
    return true;
#else
    pthread_attr_t attr;
    if (pthread_attr_init(&attr) != 0) return false;
    if (pthread_attr_setstacksize(&attr, size) != 0) { pthread_attr_destroy(&attr); return false; }
    pthread_t t;
    const int rc = pthread_create(&t, &attr,
                                  [](void* p) -> void* { (*static_cast<const std::function<void()>*>(p))(); return nullptr; },
                                  const_cast<std::function<void()>*>(&fn));
    pthread_attr_destroy(&attr);
    if (rc != 0) return false;
    pthread_join(t, nullptr);
    return true;
#endif
}

}  // namespace lithon::jit
