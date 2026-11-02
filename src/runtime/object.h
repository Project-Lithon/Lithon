#pragma once

// 4.10.0. The object memory model: the header every class instance begins
// with, and the read-only class descriptor its vptr points at.
//
// This header is pure data layout -- no allocation, no reference counting (that
// is heap.h). It is shared by BOTH tiers and by the frontend's layout code, so
// the compiler, the interpreter, and the JIT cannot disagree about where a
// field lives or what a vtable slot means.
//
//   instance:   [ vptr: 8 ][ refcount: 8 ][ attribute 0 ][ attribute 1 ] ...
//   descriptor: [ type id ][ subtree lo ][ subtree hi ][ n slots ][ slots... ]
//
// Two things make this layout the right one:
//
//   * BASE ATTRIBUTES FIRST. A subclass instance *starts with a valid base
//     instance*, so an upcast (Dog -> Animal) is free: the pointer does not
//     move. This is what makes 4.10.2's implicit upcasts zero-cost and is the
//     single most important layout decision in the section.
//
//   * vptr IS the type tag. There is no separate `type_tag` word: the pointer
//     both identifies the class (its `type_id`, its `subtree_lo/hi` range) and
//     reaches the vtable. That is why the older 8-byte ObjHeader scaffold in
//     src/runtime/value.h (a {refcount, type_tag, flags} word) is NOT the model
//     here -- it predates the vptr decision and is superseded. value.h's
//     LithonValue still carries the Object tag and the pointer; only the
//     pointee's shape changes.

#include <cstddef>
#include <cstdint>

namespace lithon::runtime {

struct ClassDescriptor;

// The two words before the attributes. `vptr` points at a `ClassDescriptor`;
// `refcount` counts strong references (see heap.h for the immortal sentinel).
struct ObjectHeader {
    ClassDescriptor* vptr;
    uint64_t refcount;
};

static_assert(sizeof(ObjectHeader) == 16,
              "an object header is [vptr:8][refcount:8] (4.10.0)");

// The read-only class descriptor a vptr points at. One per class, built once,
// carrying everything the two tiers need to answer "is this object a C?" and
// "which method is slot k?" without a walk.
//
// `subtree_lo`/`subtree_hi` are the inclusive range of type ids in C's
// inheritance subtree. isinstance(x, C) is therefore TWO integer compares
// (`lo <= x.vptr->type_id <= hi`) with no hierarchy walk -- 4.10.2's promise,
// made possible because the hierarchy is closed and single (for 4.10.2) and
// because ids are assigned in a pre-order that keeps every subtree contiguous
// (see assign_type_ids).
//
// Slot indices are STABLE DOWN THE HIERARCHY: a subclass's slot k is the same
// method the base named at slot k (it either inherits it or overrides it), so a
// virtual call site compiled against the base reads the same offset for every
// subclass. `slots` holds function pointers; a slot may be null when the method
// is abstract and this class does not implement it (such a class cannot be
// instantiated -- 4.10.3).
struct ClassDescriptor {
    uint32_t type_id;      // unique, assigned pre-order (subtree-contiguous)
    uint32_t subtree_lo;   // inclusive low end of the id range this class covers
    uint32_t subtree_hi;   // inclusive high end
    uint32_t slot_count;   // number of vtable slots
    uint32_t instance_size;  // total bytes of a fully constructed instance
    uint32_t flags;        // kClassFlagAbstract when the class has an abstract method; 0 otherwise
    // The class-typed attributes: `ref_attr_count` 8-byte slots, at the absolute
    // offsets in `ref_attr_offsets`. Releasing an instance must drop one
    // reference to each object an attribute names BEFORE the instance's own
    // block is freed, or the held object leaks and its `__del__` never runs.
    // The list is empty (count 0, pointer null) for a class with only scalars,
    // which keeps the common case a single test.
    uint32_t ref_attr_count;
    const uint32_t* ref_attr_offsets;  // absolute, header included
    const char* name;      // for diagnostics; never printed as a value
    // The destructor (`__del__`) as a plain function pointer, or null when the
    // class has none. A null destructor is what lets the optimizer elide drops:
    // "no __del__" means "releasing this object is unobservable" (4.10.0).
    void (*destructor)(void* self);
    // The vtable. Declared last; the frontend/heap allocate a descriptor large
    // enough for `slot_count` entries, or use a buffer in a unit test.
    void (*slots[1])(void);
};

static_assert(offsetof(ClassDescriptor, type_id) == 0,
              "type_id must be the first word: the JIT reads it by fixed offset");

// Immortal reference count. Class descriptors and stack-allocated objects (the
// escape-analysis result of Phase 2) carry this count so IncRef/DecRef are
// no-ops on them and no instruction is ever emitted. Chosen as the top bit so
// it is unmistakably not a real count and cannot be reached by 2^63 increments.
inline constexpr uint64_t kImmortalRefcount = 0x8000000000000000ULL;

// 4.10.2. A class with at least one method still abstract (`...` in the body)
// sets this flag. Such a class cannot be instantiated (the frontend refuses the
// construction), and a vtable slot it never implements stays null.
inline constexpr uint32_t kClassFlagAbstract = 1u;

// The refcount word of an object, as a raw pointer so a host helper can touch
// it without knowing the type. Kept here so both tiers compute the same offset.
inline uint64_t* refcount_of(ObjectHeader* obj) {
    return &obj->refcount;
}

// True when the object must never be freed (descriptor, stack value, ...).
inline bool is_immortal(const ObjectHeader* obj) {
    return obj->refcount == kImmortalRefcount;
}

// isinstance: is this object a C (or a subclass of C)? Two integer compares,
// exactly as 4.10.2 specifies. The caller passes C's descriptor; both ends are
// inclusive, and ids are subtree-contiguous, so no walk is needed.
inline bool is_instance_of(const ObjectHeader* obj, const ClassDescriptor* cls) {
    if (obj == nullptr) return false;
    const uint32_t id = obj->vptr->type_id;
    return id >= cls->subtree_lo && id <= cls->subtree_hi;
}

}  // namespace lithon::runtime
