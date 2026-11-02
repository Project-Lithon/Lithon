#pragma once

// 4.10.0. The runtime heap and reference counting.
//
// Lithon has no heap today, so 4.10.0 is where one appears: a small allocator
// that BOTH tiers share, so allocation, reference counting, and -- the one
// observable lifetime moment -- the order in which `__del__` runs are identical
// in the JIT and the interpreter. run_tier_diff.py compares the tiers
// byte-for-byte, so anything observable must agree exactly, exactly as the
// stack guard (stack_guard.h) does.
//
// Why reference counting (the decided model): it is deterministic, needs no
// pauses, keeps the runtime tiny enough for the 10 KB AOT target, matches
// CPython's own model, and makes `__del__` fire at a predictable program point.
// The cost is that every reference copy is an increment/decrement -- removed by
// the optimizer where ownership is provable (borrowed arguments, matched
// pairs, escape analysis), which is the plan in 4.10's "Optimization" section.
// Cycles leak; that is documented, and a cycle collector / weakref is a later
// addition.
//
// TIER-PARITY RULES (4.10.0), enforced here and by the tests:
//
//   1. Allocation ADDRESSES are not observable. Nothing prints an object's
//      address: a reference is print-unsafe (like Ptr), and an object prints
//      only through __str__/__repr__. So the two tiers may allocate
//      differently and still agree byte-for-byte.
//   2. The MOMENT __del__ runs IS observable, so it is pinned: an object whose
//      class has __del__ is released at the exact program point where its last
//      reference is dropped, and the optimizer may neither move nor elide that
//      drop. `release_drop` on such an object runs the destructor; on an object
//      with no __del__ it is a plain free, which may be moved or elided freely
//      because it is unobservable.
//   3. The EXCEPTIONAL path leaks. When a trap unwinds to a try handler
//      (6.9), the frames between the trap and the handler hold references; the
//      first version does NOT release them. This leak is never a safety
//      problem, and it is a documented divergence. Releasing them properly
//      needs per-frame cleanup tables, a later addition.
//   4. Tail calls release a frame's references before the call (so a tail call
//      that cannot is not a tail call). Not exercised yet -- there are no
//      references to release until 4.10.1 makes classes usable -- but the rule
//      is recorded here so the tail-call pass can consult it.

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "runtime/object.h"

#if defined(__linux__)
#include <sys/mman.h>
#endif

namespace lithon::runtime {

// ---------------------------------------------------------------------------
// Allocator
// ---------------------------------------------------------------------------
//
// An mmap-backed bump/free-list allocator, no libc malloc (the AOT target wants
// raw mmap and no libc dependency; 4.10.0's BODY_SPEC). Objects are carved from
// one 64 KiB chunk mapped on first use, with a size-class free list per class
// so a released object is reused rather than leaked to the OS.
//
// IMPORTANT: it is emitted/linked only into programs that contain a class. A
// program without a class never calls into this translation unit's allocation
// path -- and after 4.10.1's frontend gate, the runtime is emitted only when a
// class exists, so class-free programs pay nothing, including the AOT size
// budget.
//
// Addresses are NOT observable (rule 1), so the two tiers may lay out the
// arena differently. What they may not do is run __del__ in a different order
// (rule 2): that is why the destructor dispatch, not the address, is the thing
// this file guarantees.

inline constexpr std::size_t kHeapChunkBytes = 64 * 1024;
inline constexpr std::size_t kMaxSizeClasses = 64;  // size classes up to 2 KB
inline constexpr std::size_t kSizeClassStride = 16; // 16, 32, 48, ... bytes

// One free-list head per size class. A freed object's first word becomes the
// `next` pointer of a simple singly-linked list -- the standard intrusive free
// list. This is deliberately the simplest thing that reuses memory; it is not
// a production allocator and does not need to be.
struct HeapArena {
    uint8_t* base = nullptr;      // start of the mapped chunk
    std::size_t used = 0;         // bump pointer offset into the chunk
    std::size_t capacity = 0;     // bytes mapped
    void* free_head[kMaxSizeClasses] = {};
    // Stats for the tests: how many objects are live, and how many destructors
    // have run. g_live_objects reaching 0 after a program's straight-line body
    // is the "no leak" proof (4.10.0 Prove it).
    int64_t live_objects = 0;
    int64_t destructor_runs = 0;
};

// One arena per process, reset by the entry-point drivers exactly like
// g_call_depth (tier_runner.cpp / lithon_jit.cpp / interpreter.cpp). inline
// variable: one object shared by every TU, one stable address.
inline HeapArena g_heap;

// Ensure the arena has room for `need` more bytes, mapping a fresh chunk if
// not. Returns false only if the OS refuses the mapping.
inline bool heap_ensure(std::size_t need) {
    if (g_heap.base != nullptr && g_heap.used + need <= g_heap.capacity) {
        return true;
    }
#if defined(__linux__)
    void* mem = ::mmap(nullptr, kHeapChunkBytes, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) return false;
#else
    void* mem = std::malloc(kHeapChunkBytes);
    if (mem == nullptr) return false;
#endif
    g_heap.base = static_cast<uint8_t*>(mem);
    g_heap.used = 0;
    g_heap.capacity = kHeapChunkBytes;
    return true;
}

inline std::size_t size_class_index(std::size_t bytes) {
    // Round up to a 16-byte stride; the header is 16 bytes so every class is at
    // least one class wide. Anything bigger than the largest class falls back to
    // the bump pointer directly.
    std::size_t idx = (bytes + kSizeClassStride - 1) / kSizeClassStride - 1;
    return idx < kMaxSizeClasses ? idx : kMaxSizeClasses;
}

// The number of bytes a block of `bytes` actually reserves: rounded UP to the
// size-class stride. This is what makes the free list sound. Allocating exactly
// `bytes` while keying the class on round-up would put a 24-byte block in the
// class that also serves 32-byte requests, and a 32-byte request would then be
// handed a 24-byte block -- an 8-byte overflow. Rounding the RESERVATION (not
// just the index) means every block in a class is at least that class's size.
inline std::size_t reserved_bytes(std::size_t bytes) {
    const std::size_t idx = size_class_index(bytes);
    if (idx >= kMaxSizeClasses) return bytes;  // over-sized: exact mmap
    return (idx + 1) * kSizeClassStride;
}

// Allocate `bytes` (header included) and hand back the header. The object is
// born with refcount 0; the CONSTRUCTOR assigns 1 once the instance is live
// (or kImmortalRefcount when the object is a stack/immortal one). A free-list
// hit reuses a released block of the same size class.
inline ObjectHeader* heap_alloc(std::size_t bytes) {
    const std::size_t reserved = reserved_bytes(bytes);
    const std::size_t idx = size_class_index(bytes);
    if (idx < kMaxSizeClasses && g_heap.free_head[idx] != nullptr) {
        void* p = g_heap.free_head[idx];
        g_heap.free_head[idx] = *static_cast<void**>(p);
        std::memset(p, 0, bytes);
        ++g_heap.live_objects;
        return static_cast<ObjectHeader*>(p);
    }
    if (!heap_ensure(reserved)) return nullptr;
    // Align the bump pointer to 16 bytes so every header is 16-aligned.
    std::size_t aligned = (g_heap.used + 15) & ~static_cast<std::size_t>(15);
    if (aligned + reserved > g_heap.capacity) {
        // A single object larger than the chunk: map an exactly-sized block.
#if defined(__linux__)
        void* mem = ::mmap(nullptr, reserved + 16, PROT_READ | PROT_WRITE,
                           MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (mem == MAP_FAILED) return nullptr;
#else
        void* mem = std::malloc(reserved + 16);
        if (mem == nullptr) return nullptr;
#endif
        std::memset(mem, 0, bytes);
        ++g_heap.live_objects;
        return static_cast<ObjectHeader*>(mem);
    }
    uint8_t* p = g_heap.base + aligned;
    g_heap.used = aligned + reserved;
    std::memset(p, 0, bytes);
    ++g_heap.live_objects;
    return reinterpret_cast<ObjectHeader*>(p);
}

// Free a block back to its size class. `bytes` must be the same size it was
// allocated with (the caller knows the instance size from the descriptor); the
// class it returns to is derived from that size the same way heap_alloc derived
// it, so a reuse is always big enough.
inline void heap_free(ObjectHeader* obj, std::size_t bytes) {
    if (obj == nullptr) return;
    --g_heap.live_objects;
    const std::size_t idx = size_class_index(bytes);
    if (idx < kMaxSizeClasses && obj >= reinterpret_cast<ObjectHeader*>(g_heap.base) &&
        reinterpret_cast<uint8_t*>(obj) < g_heap.base + g_heap.capacity) {
        *reinterpret_cast<void**>(obj) = g_heap.free_head[idx];
        g_heap.free_head[idx] = obj;
    }
    // A block from an over-sized mmap is simply dropped; rare and harmless.
}

// ---------------------------------------------------------------------------
// Reference counting
// ---------------------------------------------------------------------------

// Increment a strong reference. An immortal object (descriptor, stack value)
// is a no-op, so no instruction is emitted for one (4.10.0's "immortal static
// objects"). Null is tolerated so a caller need not test first.
inline void incref(ObjectHeader* obj) {
    if (obj == nullptr || is_immortal(obj)) return;
    ++obj->refcount;
}

// Decrement a strong reference; when it reaches zero, RELEASE the object.
// `bytes` is the object's instance size, needed to return it to the right size
// class. This is `dec_ref` + free in one, without running __del__: the drop
// point rules (rule 2) call `release_drop` instead, which runs the destructor.
inline void decref(ObjectHeader* obj, std::size_t bytes) {
    if (obj == nullptr || is_immortal(obj)) return;
    if (obj->refcount == 0) return;  // guard against a double drop
    if (--obj->refcount == 0) {
        heap_free(obj, bytes);
    }
}

// The observable drop (rule 2): decrement, and if this was the last reference
// AND the class has a __del__, run it exactly here, then free. For a class with
// no __del__ (`destructor == nullptr`) this is an ordinary free that the
// optimizer is free to move or elide.
inline void release_drop(ObjectHeader* obj, std::size_t bytes) {
    if (obj == nullptr || is_immortal(obj)) return;
    if (obj->refcount == 0) return;
    if (--obj->refcount != 0) return;
    if (obj->vptr != nullptr && obj->vptr->destructor != nullptr) {
        obj->vptr->destructor(obj);
        ++g_heap.destructor_runs;
    }
    heap_free(obj, bytes);
}

// IncRef/DecRef as the emitted-code entry points (host helpers). The JIT calls
// these through their addresses, exactly as it reaches host_format_double. The
// size is read from the object's own descriptor, so a caller that has only the
// pointer -- the common case for an emitted drop -- needs nothing else.
inline void host_incref(ObjectHeader* obj) {
    incref(obj);
}

inline void host_decref_drop(ObjectHeader* obj) {
    if (obj == nullptr) return;
    release_drop(obj, obj->vptr != nullptr ? obj->vptr->instance_size : sizeof(ObjectHeader));
}

// ---------------------------------------------------------------------------
// Entry-point lifecycle
// ---------------------------------------------------------------------------

// Reset the arena stats for a fresh program run. The MAPPING persists (Reuse
// the chunk across runs in one process, as the tier drivers do); only the
// logical counters reset, and the free lists are cleared because the previous
// run's blocks are no longer referenced.
inline void reset_heap() {
    for (std::size_t i = 0; i < kMaxSizeClasses; ++i) g_heap.free_head[i] = nullptr;
    g_heap.used = 0;
    g_heap.live_objects = 0;
    g_heap.destructor_runs = 0;
}

}  // namespace lithon::runtime
